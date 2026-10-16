#!/usr/bin/env python3
"""supervisor task — il registro dei compiti (fase 1, 03/10/2026).

  supervisor task add FILE|- [--topic T]     un contratto {schema_version, task}: validato, con i vincoli
                                                pertinenti allegati (stesso progetto, stesso argomento)
  supervisor task list [--state S] [--plan P] [--json]
  supervisor task show ID [--json]
  supervisor task start ID [--session NOME]  in corso (la guardia della corsia chiusa lo cerca)
  supervisor task result ID FILE|-           l'esito che la sessione manda indietro (non e' accettazione)
  supervisor task check ID [--json]           riesegue il controllo senza cambiare niente: exit 0 verde, 1 rosso
  supervisor task done ID [--constraint T]   riesegue il controllo: verde = fatto; rosso = un tentativo in
                                                piu', e al terzo «fallito». Exit 0 verde, 1 rosso
  supervisor task wait-ok ID --what W --where D   attende ok (corsia chiusa fuori da un piano approvato)
  supervisor task approve ID --by B --text T [--what W] [--where D]
  supervisor task approve ID --by B --inbox MSG [--text NOTA] [--what W] [--where D]
                                                da una sessione Claude l'ok a nome di una persona passa solo cosi':
                                                dalla master, citando un suo messaggio del relay dell'ultima ora
  supervisor task auto-close [--hours 6] [--json]   chiude i compiti aperti da piu' di 6 ore col controllo verde
  supervisor task cancel ID
  supervisor task board [--json] [--timeline [--since 6h]]   stati ed esiti dal codice; con --timeline la cronologia
  supervisor task constraint add (--project P | --topic T) TESTO
  supervisor task constraint list [--project P] [--topic T]

Il contratto e' schemas/task-contract.v1.json, condiviso con fable-director (copiato, mai importato). Il
registro e' SQLite in ~/.config/cc-supervisor/tasks.db (CM_TASKS_DB per i test). Nessun «fatto» a giudizio:
solo il controllo rieseguito e verde. Senza registro nessun altro comando di supervisor cambia.
"""
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE.parent / "schemas" / "task-contract.v1.json"
STATES = ("proposed", "assigned", "running", "awaiting_ok", "approved", "done", "failed", "cancelled")
OPEN_STATES = ("proposed", "assigned", "running", "awaiting_ok", "approved")
PROOF_TAIL = 12


# ------------------------------------------------------------------ schema
def schema():
    return json.loads(SCHEMA_PATH.read_text())


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def validate(value, node=None, root=None, path="$"):
    """Gli errori (lista di stringhe) di `value` rispetto allo schema: solo le parole chiave che lo schema usa
    (type, const, enum, required, properties, additionalProperties false, items, min/maxLength, minimum,
    maximum, minItems, pattern, $ref locale). Nessuna dipendenza: il plugin gira con python3 nudo."""
    root = root or schema()
    node = root if node is None else node
    if "$ref" in node:
        node = root["$defs"][node["$ref"].rsplit("/", 1)[-1]]
    errs = []
    t = node.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_is(value, x) for x in ts):
            return [f"{path}: expected {'|'.join(ts)}"]
    if "const" in node and value != node["const"]:
        errs.append(f"{path}: must be {node['const']!r}")
    if "enum" in node and value not in node["enum"]:
        errs.append(f"{path}: one of {', '.join(map(str, node['enum']))}")
    if isinstance(value, str):
        if len(value) < node.get("minLength", 0) or len(value) > node.get("maxLength", 10 ** 9):
            errs.append(f"{path}: length out of range")
        if "pattern" in node and not re.search(node["pattern"], value):
            errs.append(f"{path}: does not match {node['pattern']}")
    if _is(value, "number"):
        if "minimum" in node and value < node["minimum"] or "maximum" in node and value > node["maximum"]:
            errs.append(f"{path}: out of range")
    if isinstance(value, list):
        if len(value) < node.get("minItems", 0):
            errs.append(f"{path}: at least {node['minItems']} item(s)")
        for i, x in enumerate(value):
            errs += validate(x, node.get("items") or {}, root, f"{path}[{i}]")
    if isinstance(value, dict):
        props = node.get("properties") or {}
        for k in node.get("required") or []:
            if k not in value:
                errs.append(f"{path}.{k}: required")
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], root, f"{path}.{k}")
            elif node.get("additionalProperties") is False:
                errs.append(f"{path}.{k}: not allowed")
    return errs


# ------------------------------------------------------------------ registro
def db_path():
    p = os.environ.get("CM_TASKS_DB")
    if p:
        return Path(p)
    c = (os.environ.get("CC_SUPERVISOR_CONFIG") or os.environ.get("TEAM_SUPERVISOR_CONFIG") or os.environ.get("CLAUDE_MASTER_CONFIG"))
    base = Path(c).parent if c else Path.home() / ".config" / "cc-supervisor"
    return base / "tasks.db"


SQL = """
CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, title TEXT, file TEXT, approved_by TEXT, approved_at INTEGER,
    approved_text TEXT, created_at INTEGER);
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, plan TEXT, project TEXT, session TEXT, title TEXT, lane TEXT,
    state TEXT, contract TEXT, created_at INTEGER, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS results (n INTEGER PRIMARY KEY, task TEXT, attempt INTEGER, outcome TEXT, reason TEXT,
    proof TEXT, perimeter TEXT, at INTEGER, accepted INTEGER);
CREATE TABLE IF NOT EXISTS approvals (n INTEGER PRIMARY KEY, task TEXT, plan TEXT, by_ TEXT, at INTEGER, text TEXT,
    what TEXT, where_ TEXT);
CREATE TABLE IF NOT EXISTS constraints (n INTEGER PRIMARY KEY, project TEXT, topic TEXT, text TEXT, source TEXT,
    at INTEGER);
"""


def connect(create=True):
    p = db_path()
    if not create and not p.exists():
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SQL)
    return con


def now():
    return int(time.time())


def get_task(con, tid):
    r = con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not r:
        sys.exit(f"task: no task {tid}")
    return r


def set_state(con, tid, state):
    con.execute("UPDATE tasks SET state=?, updated_at=? WHERE id=?", (state, now(), tid))


def constraints_for(con, project, topic):
    rows = con.execute("SELECT text FROM constraints WHERE (project IS NOT NULL AND project=?) OR (topic IS NOT NULL AND topic=?) "
                       "ORDER BY n", (project, topic or "")).fetchall()
    return [r["text"] for r in rows]


def add(con, contract, topic=None):
    """(id, errori). Il contratto entra solo se valido; i vincoli pertinenti si aggiungono a task.constraints."""
    errs = validate(contract)
    if errs:
        return None, errs
    t = contract["task"]
    if con.execute("SELECT 1 FROM tasks WHERE id=?", (t["id"],)).fetchone():
        return None, [f"$.task.id: {t['id']} already in the registry"]
    have = list(t.get("constraints") or [])
    t["constraints"] = have + [c for c in constraints_for(con, t["where"]["project"], topic) if c not in have]
    if not t["constraints"]:
        t.pop("constraints")
    state = "assigned" if t["where"]["session"] else "proposed"
    con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                (t["id"], t["plan"], t["where"]["project"], t["where"]["session"], t["title"], t["lane"], state,
                 json.dumps(contract, ensure_ascii=False), now(), now()))
    con.commit()
    return t["id"], []


def attempts(con, tid):
    return con.execute("SELECT COUNT(*) FROM results WHERE task=? AND accepted IS NOT NULL", (tid,)).fetchone()[0]


def run_check(task):
    """(rc, coda dell'uscita): il controllo del contratto, nella sua cartella e col suo timeout."""
    chk = task["check"]
    cwd = chk.get("cwd") or task["where"]["project"]
    try:
        p = subprocess.run(chk["cmd"], shell=True, cwd=cwd, capture_output=True, text=True, timeout=chk["timeout_s"])
        out = (p.stdout + p.stderr).rstrip().splitlines()
        return p.returncode, "\n".join(out[-PROOF_TAIL:])
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {chk['timeout_s']} s"
    except OSError as e:
        return 127, str(e)


def done(con, tid, constraint=None):
    """Accettazione dal codice: riesegue il controllo. (verde, result)."""
    row = get_task(con, tid)
    if row["state"] not in OPEN_STATES:
        sys.exit(f"task {tid}: already {row['state']}")
    task = json.loads(row["contract"])["task"]
    rc, tail = run_check(task)
    n = attempts(con, tid) + 1
    green = rc == 0
    last = (tail.splitlines() or [""])[-1][:300]
    res = {"task": tid, "attempt": min(n, 3), "outcome": "green" if green else "red",
           "reason": "check green" if green else f"check exit {rc}: {last or 'no output'}",
           "proof": {"kind": "check", "value": f"$ {task['check']['cmd']}\n{tail}\n(exit {rc})", "rc": rc}, "at": now()}
    con.execute("INSERT INTO results (task, attempt, outcome, reason, proof, perimeter, at, accepted) VALUES (?,?,?,?,?,?,?,1)",
                (tid, res["attempt"], res["outcome"], res["reason"], json.dumps(res["proof"], ensure_ascii=False), None, res["at"]))
    if green:
        set_state(con, tid, "done")
    elif n >= task["attempts_max"]:
        set_state(con, tid, "failed")
    if constraint or (not green and n >= task["attempts_max"]):
        con.execute("INSERT INTO constraints (project, topic, text, source, at) VALUES (?,?,?,?,?)",
                    (task["where"]["project"], None, constraint or f"{task['title']}: {res['reason']}", tid, now()))
    con.commit()
    return green, res


def record_result(con, tid, result):
    """L'esito mandato dalla sessione: registrato, mai accettazione (accepted NULL); lo stato non cambia."""
    get_task(con, tid)
    errs = validate(result, {"$ref": "#/$defs/result"})
    if errs:
        return errs
    if result["task"] != tid:
        return [f"$.task: {result['task']} is not {tid}"]
    con.execute("INSERT INTO results (task, attempt, outcome, reason, proof, perimeter, at, accepted) VALUES (?,?,?,?,?,?,?,NULL)",
                (tid, result["attempt"], result["outcome"], result["reason"], json.dumps(result["proof"], ensure_ascii=False),
                 json.dumps(result.get("perimeter")) if result.get("perimeter") else None, result["at"]))
    con.commit()
    return []


def approve(con, tid, by, text, what=None, where=None):
    row = get_task(con, tid)
    con.execute("INSERT INTO approvals (task, plan, by_, at, text, what, where_) VALUES (?,?,?,?,?,?,?)",
                (tid, row["plan"], by, now(), text, what, where))
    if row["state"] == "awaiting_ok":
        set_state(con, tid, "approved")
    con.commit()


INBOX_MAX_AGE_S = 3600
AUTO_CLOSE_H = 6


def _caller_session():
    """Il nome tmux della sessione che chiama (TMUX_PANE), o ''."""
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return ""
    try:
        r = subprocess.run(["tmux"] + os.environ.get("CM_TMUX_ARGS", "").split() + ["display-message", "-p", "-t", pane, "#{session_name}"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def inbox_approval_text(by, rec_id, note=None):
    """16/10/2026 (the maintainer from the phone, «Sì, tutte e due»): l'ok a nome di una persona registrato da una sessione vale solo se lo
    registra la master e cita un messaggio del relay (telefono, orologio, web) arrivato nella sua casella nell'ultima
    ora; il testo dell'ok e' copiato da li', non riscritto. Torna il testo o esce con il motivo."""
    cm = _load("cm-config")
    cfg = cm.load(warn=False)
    master = str((cfg.get("workspace") or {}).get("root_session_name") or "master")
    caller = _caller_session()
    if caller != master:
        sys.exit(f"task approve --inbox: only the master session ({master}) records an ok on someone's behalf; this is {caller or 'not a tmux session'}")
    rec = _load("cm-inbox").load(rec_id)
    if not rec:
        sys.exit(f"task approve --inbox: no message {rec_id} in the inbox")
    if rec.get("to") != master:
        sys.exit(f"task approve --inbox: message {rec_id} is for {rec.get('to')}, not for {master}")
    if rec.get("origin") != "relay":
        sys.exit(f"task approve --inbox: message {rec_id} did not come from the relay (origin {rec.get('origin') or '?'}): an ok on someone's behalf comes from their device")
    age = time.time() - float(rec.get("created") or 0)
    if age > INBOX_MAX_AGE_S:
        sys.exit(f"task approve --inbox: message {rec_id} is {int(age // 60)} minutes old (at most {INBOX_MAX_AGE_S // 60})")
    quote = " ".join(str(rec.get("text") or "").split())[:400]
    when = time.strftime("%d/%m %H:%M", time.localtime(float(rec["created"])))
    return f"{by}, {when}: «{quote}» (inbox {rec_id}, origin relay)" + (f" — {note}" if note else "")


def auto_close(con, hours=AUTO_CLOSE_H):
    """16/10/2026: i compiti aperti da piu' di `hours` ore col controllo verde si chiudono da soli (lo stesso `done`:
    il controllo rieseguito, mai un giudizio). Un controllo rosso qui non conta come tentativo: resta com'era.
    Torna [(id, verde)] dei compiti controllati."""
    cutoff = now() - int(hours * 3600)
    out = []
    for r in con.execute("SELECT id, contract FROM tasks WHERE state IN (%s) AND created_at <= ? ORDER BY created_at"
                         % ",".join("?" * len(OPEN_STATES)), (*OPEN_STATES, cutoff)).fetchall():
        task = json.loads(r["contract"])["task"]
        rc, _ = run_check(task)
        if rc == 0:
            done(con, r["id"])
        out.append((r["id"], rc == 0))
    return out


def board(con):
    rows = [dict(r) for r in con.execute("SELECT id, plan, project, session, title, lane, state, updated_at FROM tasks ORDER BY updated_at DESC")]
    counts = {s: 0 for s in STATES}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    last = {}
    for r in con.execute("SELECT task, outcome, reason, attempt, at FROM results ORDER BY n"):
        last[r["task"]] = {"outcome": r["outcome"], "reason": r["reason"], "attempt": r["attempt"], "at": r["at"]}
    for r in rows:
        r["last"] = last.get(r["id"])
    plans = {}
    for r in rows:
        if r["plan"]:
            p = plans.setdefault(r["plan"], {s: 0 for s in STATES})
            p[r["state"]] += 1
    return {"counts": counts, "plans": plans,
            "awaiting_ok": [r for r in rows if r["state"] == "awaiting_ok"],
            "running": [r for r in rows if r["state"] == "running"],
            "failed": [r for r in rows if r["state"] == "failed"],
            "tasks": rows}


# ------------------------------------------------------------------ CLI
def _read(src):
    try:
        return json.loads(sys.stdin.read() if src == "-" else Path(src).read_text())
    except (OSError, ValueError) as e:
        sys.exit(f"task: cannot read {src}: {e}")


def _opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            sys.exit(f"task: {name} needs a value")
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


def _flag(args, name):
    if name in args:
        args.remove(name)
        return True
    return False


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    verb, args = argv[0], list(argv[1:])
    as_json = _flag(args, "--json")
    con = connect()
    if verb == "add":
        topic = _opt(args, "--topic")
        if len(args) != 1:
            sys.exit("usage: task add FILE|- [--topic T]")
        tid, errs = add(con, _read(args[0]), topic)
        if errs:
            print("\n".join(errs), file=sys.stderr)
            return 2
        print(tid)
        return 0
    if verb == "list":
        st, plan = _opt(args, "--state"), _opt(args, "--plan")
        q, p = "SELECT id, plan, state, lane, title, project FROM tasks WHERE 1=1", []
        if st:
            q += " AND state=?"; p.append(st)
        if plan:
            q += " AND plan=?"; p.append(plan)
        rows = [dict(r) for r in con.execute(q + " ORDER BY created_at, id", p)]
        if as_json:
            # contratto 1.37 (05/10): la richiesta di ok (wait-ok: cosa esce e dove) per «Da approvare» nell'app
            for r in rows:
                a = con.execute("SELECT what, where_, at FROM approvals WHERE task=? AND by_ IS NULL ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
                r["request"] = {"what": a["what"], "where": a["where_"], "at": a["at"]} if a else None
        print(json.dumps(rows, ensure_ascii=False) if as_json else "\n".join(f"{r['id']}  {r['state']:<11} {r['lane']:<6} {r['title']}" for r in rows))
        return 0
    if verb == "show":
        row = get_task(con, args[0])
        out = dict(row)
        out["contract"] = json.loads(row["contract"])
        out["results"] = [dict(r, proof=json.loads(r["proof"])) for r in con.execute("SELECT * FROM results WHERE task=? ORDER BY n", (args[0],))]
        out["approvals"] = [dict(r) for r in con.execute("SELECT * FROM approvals WHERE task=? ORDER BY n", (args[0],))]
        print(json.dumps(out, ensure_ascii=False, indent=None if as_json else 2))
        return 0
    if verb == "start":
        sess = _opt(args, "--session")
        row = get_task(con, args[0])
        if row["state"] not in OPEN_STATES:
            sys.exit(f"task {args[0]}: already {row['state']}")
        if sess:
            con.execute("UPDATE tasks SET session=? WHERE id=?", (sess, args[0]))
        set_state(con, args[0], "running")
        con.commit()
        return 0
    if verb == "result":
        errs = record_result(con, args[0], _read(args[1]))
        if errs:
            print("\n".join(errs), file=sys.stderr)
            return 2
        return 0
    if verb == "check":
        # 14/10 (spunti della valutazione di Kaya): il controllo rieseguito SENZA cambiare stato ne' contare tentativi,
        # per dire al telefono verde o rosso quando la sessione si ferma. Solo `done` accetta.
        task = json.loads(get_task(con, args[0])["contract"])["task"]
        rc, tail = run_check(task)
        res = {"task": args[0], "green": rc == 0, "rc": rc, "tail": tail, "title": task.get("title") or ""}
        print(json.dumps(res, ensure_ascii=False) if as_json else f"{args[0]}: {'green' if rc == 0 else 'red'} (exit {rc})")
        return 0 if rc == 0 else 1
    if verb == "done":
        constraint = _opt(args, "--constraint")
        green, res = done(con, args[0], constraint)
        print(json.dumps(res, ensure_ascii=False) if as_json else f"{args[0]}: {res['outcome']} — {res['reason']}")
        return 0 if green else 1
    if verb == "wait-ok":
        what, where = _opt(args, "--what"), _opt(args, "--where")
        if not what or not where:
            sys.exit("usage: task wait-ok ID --what W --where D")
        get_task(con, args[0])
        con.execute("INSERT INTO approvals (task, plan, by_, at, text, what, where_) VALUES (?,?,NULL,?,NULL,?,?)",
                    (args[0], None, now(), what, where))   # la richiesta: by NULL finche' nessuno approva
        set_state(con, args[0], "awaiting_ok")
        con.commit()
        return 0
    if verb == "approve":
        by, text, rec_id = _opt(args, "--by"), _opt(args, "--text"), _opt(args, "--inbox")
        if not by or not (text or rec_id) or not args:
            sys.exit("usage: task approve ID --by B (--text T | --inbox MSG [--text NOTE]) [--what W] [--where D]")
        get_task(con, args[0])
        if rec_id:
            text = inbox_approval_text(by, rec_id, text)
        elif os.environ.get("CLAUDECODE") and os.environ.get("CM_APPROVE_VIA") != "relay":
            # una sessione Claude (anche la master) non scrive un ok a nome d'altri senza il messaggio che lo prova;
            # il relay eredita CLAUDECODE da chi l'ha avviato, e si dichiara con CM_APPROVE_VIA
            sys.exit("task approve: from a Claude session an ok needs --inbox MSG (a relay message to the master from the last hour)")
        approve(con, args[0], by, text, _opt(args, "--what"), _opt(args, "--where"))
        return 0
    if verb == "auto-close":
        hours = float(_opt(args, "--hours", AUTO_CLOSE_H))
        res = auto_close(con, hours)
        print(json.dumps([{"task": t, "green": g} for t, g in res]) if as_json else "\n".join(f"{t}: {'done' if g else 'red, left open'}" for t, g in res))
        return 0
    if verb == "cancel":
        get_task(con, args[0])
        set_state(con, args[0], "cancelled")
        con.commit()
        return 0
    if verb == "board":
        b = board(con)
        since = _opt(args, "--since", "6h")
        if _flag(args, "--timeline"):
            # fase 2 (03/10): sotto i conteggi, la cronologia delle sessioni (prompt, test, commit, esiti, compiti)
            spec = importlib.util.spec_from_file_location("cm_timeline", HERE / "cm-timeline.py")
            tl_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tl_mod)
            b["timeline"] = tl_mod.timeline(tl_mod.parse_since(since))
        if as_json:
            print(json.dumps(b, ensure_ascii=False))
            return 0
        print("  ".join(f"{s} {n}" for s, n in b["counts"].items() if n) or "no tasks")
        for key in ("awaiting_ok", "running", "failed"):
            for r in b[key]:
                last = r["last"]
                print(f"{key:<11} {r['id']}  {r['title']}" + (f"  [{last['outcome']} #{last['attempt']}: {last['reason']}]" if last else ""))
        if "timeline" in b:
            print("\n" + tl_mod.render(b["timeline"]))
        return 0
    if verb == "constraint":
        sub = args.pop(0) if args else ""
        project, topic = _opt(args, "--project"), _opt(args, "--topic")
        if sub == "add":
            if not args or not (project or topic):
                sys.exit("usage: task constraint add (--project P | --topic T) TEXT")
            con.execute("INSERT INTO constraints (project, topic, text, source, at) VALUES (?,?,?,?,?)",
                        (project, topic, " ".join(args), None, now()))
            con.commit()
            return 0
        if sub == "list":
            rows = [dict(r) for r in con.execute("SELECT * FROM constraints ORDER BY n")
                    if (not project or r["project"] == project) and (not topic or r["topic"] == topic)]
            print(json.dumps(rows, ensure_ascii=False) if as_json else "\n".join(f"{r['project'] or '#' + str(r['topic'])}: {r['text']}" for r in rows))
            return 0
        sys.exit("usage: task constraint add|list")
    sys.exit(f"task: unknown verb {verb}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
