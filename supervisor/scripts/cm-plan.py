#!/usr/bin/env python3
"""supervisor plan — il motore della mappa (fase 1, 03/10/2026).

  supervisor plan check FILE                   contratto di ogni nodo, archi verso nodi esistenti, nessun ciclo
  supervisor plan approve FILE --by B --text T registra l'approvazione e i compiti del piano nel registro
  supervisor plan run FILE|ID [--parallel N] [--turn-timeout S] [--dry-run]
  supervisor plan status ID [--json]

Il piano e' un file JSON {schema_version, id, title, nodes: [task del contratto]}; gli archi sono i
`depends_on` dei nodi, ciascuno con cosa passa. `plan run`:
- esegue in parallelo i nodi pronti (dipendenze fatte), al massimo --parallel alla volta (default 2): le soglie
  di RAM e il tetto delle sessioni restano quelli di `launch`, che rifiuta da solo;
- route script: esegue `task.run` qui, senza sessione; gli altri aprono (o riusano) una sessione nella cartella,
  `win` su quell'host, mandano l'unita' con `talk --no-wait` e aspettano con `wait`;
- l'esito della sessione (riga `CM-RESULT {json}`) si registra, ma decide solo il controllo rieseguito qui;
- verde sblocca i successori; rosso rimanda SOLO quell'unita', con il motivo e il perimetro ristretto, fino ad
  attempts_max; poi «fallito», i successori annullati e un messaggio alla master (`talk master`);
- chiude le sessioni che ha aperto. Lo stato sta nel registro: rilanciato dopo un'interruzione riprende.
Con fable-director attivo nell'account della sessione, il brief chiede di aprire il budget con il controllo del
nodo come --verify; senza, il controllo lo riesegue solo il motore. Nessuna dipendenza fra i due plugin.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
TK = _load("cm-tasks")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
CM_BIN = os.environ.get("CM_BIN") or str(HERE / "supervisor")
RESULT_RE = re.compile(r"^CM-RESULT\s+(\{.*\})\s*$", re.M)
LOCK = threading.Lock()


def run_cm(*args, timeout=None):
    try:
        p = subprocess.run([CM_BIN, *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"


# ------------------------------------------------------------------ piano
def load_plan(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        sys.exit(f"plan: cannot read {path}: {e}")


def check_plan(plan):
    """Gli errori del piano: forma, contratto di ogni nodo (plan = id del piano), archi, cicli, route script."""
    errs = []
    if plan.get("schema_version") != 1 or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", str(plan.get("id") or "")):
        errs.append("$: schema_version 1 and an id are required")
    nodes = plan.get("nodes") if isinstance(plan.get("nodes"), list) else []
    if not nodes:
        errs.append("$.nodes: at least one node")
    ids = [n.get("id") for n in nodes if isinstance(n, dict)]
    if len(ids) != len(set(ids)):
        errs.append("$.nodes: duplicate ids")
    for i, n in enumerate(nodes):
        errs += [e.replace("$.task", f"$.nodes[{i}]") for e in TK.validate({"schema_version": 1, "task": n})]
        if isinstance(n, dict):
            if n.get("plan") != plan.get("id"):
                errs.append(f"$.nodes[{i}].plan: must be {plan.get('id')}")
            if n.get("route") == "script" and not n.get("run"):
                errs.append(f"$.nodes[{i}].run: required for route script")
            for d in n.get("depends_on") or []:
                if isinstance(d, dict) and d.get("task") not in ids:
                    errs.append(f"$.nodes[{i}].depends_on: unknown node {d.get('task')}")
    if errs:
        return errs
    deps = {n["id"]: [d["task"] for d in n["depends_on"]] for n in nodes}
    seen, stack = set(), set()

    def cyclic(v):
        if v in stack:
            return True
        if v in seen:
            return False
        seen.add(v)
        stack.add(v)
        bad = any(cyclic(w) for w in deps[v])
        stack.discard(v)
        return bad
    if any(cyclic(v) for v in deps):
        errs.append("$.nodes: the edges make a cycle")
    return errs


def approve(con, plan, path, by, text):
    if con.execute("SELECT approved_at FROM plans WHERE id=? AND approved_at IS NOT NULL", (plan["id"],)).fetchone():
        sys.exit(f"plan {plan['id']}: already approved")
    con.execute("INSERT OR REPLACE INTO plans VALUES (?,?,?,?,?,?,?)",
                (plan["id"], plan.get("title") or "", str(Path(path).resolve()), by, TK.now(), text, TK.now()))
    con.execute("INSERT INTO approvals (task, plan, by_, at, text, what, where_) VALUES (NULL,?,?,?,?,?,?)",
                (plan["id"], by, TK.now(), text, "plan " + plan["id"], str(Path(path).resolve())))
    con.commit()
    for n in plan["nodes"]:
        tid, errs = TK.add(con, {"schema_version": 1, "task": n}, plan.get("topic"))
        if errs:
            sys.exit("\n".join(errs))


# ------------------------------------------------------------------ fable-director (facoltativo)
def fd_active(account):
    """fable-director installato E abilitato nelle impostazioni dell'account della sessione. Solo una lettura di
    file: nessun import, nessuna chiamata. Assente o spento = il motore fa da solo."""
    accs = CFG.get("accounts") or {}
    acc = accs.get(account or "") or accs.get(CFG.get("default_account") or "") or {}
    p = Path(cm.expand(acc.get("config_dir") or "~/.claude")) / "settings.json"
    try:
        enabled = json.loads(p.read_text()).get("enabledPlugins") or {}
    except (OSError, ValueError):
        return False
    return any(k.split("@", 1)[0] == "fable-director" and v is True for k, v in enabled.items())


def brief(node, attempt, red=None):
    perim = (red or {}).get("perimeter") or node["perimeter"]
    parts = [M("plan.brief", id=node["id"], title=node["title"], check=node["check"]["cmd"],
               perimeter=", ".join(perim) or M("plan.read_only"), lane=node["lane"])]
    if node.get("constraints"):
        parts.append(M("plan.brief_constraints", items="; ".join(node["constraints"])))
    if red:
        parts.append(M("plan.brief_retry", attempt=attempt, max=node["attempts_max"], reason=red["reason"]))
    if fd_active(node["where"].get("account")):
        parts.append(M("plan.brief_fd", check=node["check"]["cmd"], paths=",".join(perim) or "none", data_class=node["data_class"],
                       type=f" --type {node['type']}" if node.get("type") else ""))
    parts.append(M("plan.brief_result", id=node["id"], attempt=attempt))
    return "\n\n".join(parts)


# ------------------------------------------------------------------ esecuzione
def session_for(node):
    """(nome, aperta da noi). La sessione dichiarata, o una viva nella cartella, o una nuova con `launch`."""
    where = node["where"]
    if where["host"] == "win":
        name = where["session"] or "win:" + Path(where["project"]).name
        rc, out = run_cm("launch", where["project"], "--host", "win", "--no-window", *(("--account", where["account"]) if where["account"] else ()), timeout=600)
        return (name, True) if rc == 0 else (None, out)
    if where["session"]:
        return where["session"], False
    rows = _sessions()
    live = next((r for r in rows if os.path.realpath(r.get("cwd") or "") == os.path.realpath(where["project"])), None)
    if live:
        return live.get("tmux") or live.get("name"), False
    rc, out = run_cm("launch", where["project"], "--no-window", *(("--account", where["account"]) if where["account"] else ()), timeout=300)
    if rc != 0:
        return None, out
    for _ in range(60):
        live = next((r for r in _sessions() if os.path.realpath(r.get("cwd") or "") == os.path.realpath(where["project"])), None)
        if live:
            return live.get("tmux") or live.get("name"), True
        time.sleep(2)
    return None, "launched, but no session appeared in the folder"


def _sessions():
    rc, out = run_cm("sessions", "--json", "--no-screen", timeout=60)
    try:
        return json.loads(out) if rc == 0 else []
    except ValueError:
        return []


def _reported(con, node, out):
    """La riga CM-RESULT della risposta, se c'e' e rispetta il contratto: registrata, senza accettare niente."""
    m = None
    for m in RESULT_RE.finditer(out or ""):
        pass
    if not m:
        return None
    try:
        res = json.loads(m.group(1))
    except ValueError:
        return None
    with LOCK:
        return res if not TK.record_result(con, node["id"], res) else None


def run_node(node, turn_timeout, log):
    con = TK.connect()
    tid = node["id"]
    name, opened = None, False
    try:
        if node["route"] != "script":
            name, opened = session_for(node)
            if not name:
                log(f"{tid}: no session ({opened})")
                with LOCK:
                    TK.set_state(con, tid, "failed")
                    con.commit()
                return False
            if opened is not True:
                opened = False
        with LOCK:
            row = TK.get_task(con, tid)
            if row["state"] not in ("running",):
                TK.set_state(con, tid, "running")
            if name:
                con.execute("UPDATE tasks SET session=? WHERE id=?", (name, tid))
            con.commit()
        red = None
        while True:
            attempt = TK.attempts(con, tid) + 1
            if node["route"] == "script":
                p = subprocess.run(node["run"], shell=True, cwd=node["check"].get("cwd") or node["where"]["project"],
                                   capture_output=True, text=True, timeout=node["check"]["timeout_s"])
                log(f"{tid}: run exit {p.returncode}")
            else:
                rc, out = run_cm("talk", name, brief(node, attempt, red), "--no-wait", timeout=120)
                if rc != 0:
                    log(f"{tid}: talk failed: {(out.splitlines() or ['?'])[0]}")
                rc, out = run_cm("wait", name, "--timeout", str(turn_timeout), timeout=turn_timeout + 60)
                said = _reported(con, node, out)
                log(f"{tid}: attempt {attempt} reported {said['outcome'] if said else 'nothing'}")
            with LOCK:
                green, res = TK.done(con, tid)
            log(f"{tid}: attempt {attempt} check {res['outcome']} — {res['reason']}")
            if green:
                return True
            with LOCK:
                state = TK.get_task(con, tid)["state"]
            if state == "failed":
                run_cm("talk", (CFG.get("tasks") or {}).get("notify") or "master",
                       M("plan.failed_notice", id=tid, title=node["title"], n=attempt, reason=res["reason"]), "--no-wait", timeout=120)
                return False
            said = None
            if node["route"] != "script":
                rows = con.execute("SELECT perimeter FROM results WHERE task=? AND accepted IS NULL ORDER BY n DESC LIMIT 1", (tid,)).fetchone()
                said = json.loads(rows["perimeter"]) if rows and rows["perimeter"] else None
            red = {"reason": res["reason"], "perimeter": said}
    finally:
        if opened is True and name:
            run_cm("close", name, timeout=120)


def run_plan(plan, parallel=2, turn_timeout=3600, dry_run=False, log=print):
    """Esegue il piano fino a quando non resta niente da fare. True se tutti i nodi sono fatti."""
    con = TK.connect()
    if not con.execute("SELECT 1 FROM plans WHERE id=? AND approved_at IS NOT NULL", (plan["id"],)).fetchone():
        sys.exit(f"plan {plan['id']}: not approved (supervisor plan approve FILE --by ... --text ...)")
    nodes = {n["id"]: dict(n, constraints=json.loads(TK.get_task(con, n["id"])["contract"])["task"].get("constraints") or [])
             for n in plan["nodes"]}
    if dry_run:
        for wave in waves(nodes):
            log("wave: " + ", ".join(wave))
        return True
    running = {}
    while True:
        with LOCK:
            states = {tid: TK.get_task(con, tid)["state"] for tid in nodes}
        for tid, th in list(running.items()):
            if not th.is_alive():
                running.pop(tid)
        for tid, st in states.items():
            if st in TK.OPEN_STATES and tid not in running and any(states[d["task"]] in ("failed", "cancelled") for d in nodes[tid]["depends_on"]):
                with LOCK:
                    TK.set_state(con, tid, "cancelled")
                    con.commit()
                log(f"{tid}: cancelled (a dependency failed)")
                states[tid] = "cancelled"
        ready = [tid for tid, st in states.items() if st in TK.OPEN_STATES and tid not in running
                 and all(states[d["task"]] in TK.DEPS_MET for d in nodes[tid]["depends_on"])]
        for tid in ready[:max(0, parallel - len(running))]:
            th = threading.Thread(target=run_node, args=(nodes[tid], turn_timeout, log), daemon=True)
            running[tid] = th
            th.start()
        if not running and not ready:
            break
        time.sleep(float(os.environ.get("CM_PLAN_POLL", "2")))
    final = {tid: TK.get_task(con, tid)["state"] for tid in nodes}
    log("plan " + plan["id"] + ": " + ", ".join(f"{k} {v}" for k, v in final.items()))
    return all(v in TK.DEPS_MET for v in final.values())   # 1.116: un nodo superato e' uscito, non manca


def waves(nodes):
    done, out = set(), []
    while len(done) < len(nodes):
        w = sorted(t for t, n in nodes.items() if t not in done and all(d["task"] in done for d in n["depends_on"]))
        if not w:
            break
        out.append(w)
        done.update(w)
    return out


# ------------------------------------------------------------------ CLI
def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    verb, args = argv[0], list(argv[1:])
    as_json = TK._flag(args, "--json")
    if verb == "check":
        errs = check_plan(load_plan(args[0]))
        print("\n".join(errs) if errs else "ok")
        return 2 if errs else 0
    if verb == "approve":
        by, text = TK._opt(args, "--by"), TK._opt(args, "--text")
        if not by or not text or len(args) != 1:
            sys.exit("usage: plan approve FILE --by B --text T")
        plan = load_plan(args[0])
        errs = check_plan(plan)
        if errs:
            print("\n".join(errs), file=sys.stderr)
            return 2
        approve(TK.connect(), plan, args[0], by, text)
        print(plan["id"])
        return 0
    if verb == "run":
        parallel = int(TK._opt(args, "--parallel", "2"))
        turn = int(TK._opt(args, "--turn-timeout", "3600"))
        dry = TK._flag(args, "--dry-run")
        src = args[0]
        if not Path(src).exists():
            row = TK.connect().execute("SELECT file FROM plans WHERE id=?", (src,)).fetchone()
            if not row:
                sys.exit(f"plan: no plan {src}")
            src = row["file"]
        plan = load_plan(src)
        errs = check_plan(plan)
        if errs:
            print("\n".join(errs), file=sys.stderr)
            return 2
        return 0 if run_plan(plan, parallel, turn, dry, log=lambda s: print(time.strftime("%H:%M:%S"), s, flush=True)) else 1
    if verb == "status":
        con = TK.connect()
        rows = [dict(r) for r in con.execute("SELECT id, state, session, title FROM tasks WHERE plan=? ORDER BY created_at, id", (args[0],))]
        if not rows:
            sys.exit(f"plan: no plan {args[0]}")
        print(json.dumps(rows, ensure_ascii=False) if as_json else "\n".join(f"{r['id']}  {r['state']:<11} {r['session'] or '-':<20} {r['title']}" for r in rows))
        return 0
    sys.exit(f"plan: unknown verb {verb}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
