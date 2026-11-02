#!/usr/bin/env python3
"""Fase B del programma approvato il 01/11/2026 (contratto 1.102): quello che resta fuori. Nessuna chiamata a un modello.

  supervisor rimasto add PRODOTTO CHIAVE --dove T --percorsi a,b [--comando C] [--progetto DIR] [--sessione S] [--titolo T]
  supervisor rimasto list [--json] | check | ok PRODOTTO CHIAVE | allinea PRODOTTO CHIAVE | remove PRODOTTO CHIAVE
  supervisor altrove "descrizione" --rivelatore CMD [--obiettivi a,b] [--host H --controllo DIR] [--prova] [--da-capo] [--json]
  supervisor altrove list [--json] | card ID --correzione T [--sessione S] | verifica ID
  supervisor remind add "testo" --until CMD [--expires D] [--prompt P] | --at AAAA-MM-GG
  supervisor remind list [--json] | remove ID

RIMASTO INDIETRO: per prodotto, cio' che dipende dal codice (demo, scheda Play, pagina del sito, README, privacy). Ogni voce
dice dove sta, quali percorsi la invecchiano e, se si puo', un comando che verifica (0 = allineata). Una voce e' indietro se
quei percorsi sono cambiati dall'ultimo allineamento o il comando fallisce. Il controllo gira ogni ora con `suggest fill`.

STESSO DIFETTO ALTROVE: il rivelatore gira in ogni cartella: 0 = positivo, 1 = negativo, altro = errore, mai negativo. Prima
gira su una cartella vuota: se li' e' positivo, non dimostra niente e si rifiuta. A lotti, interrompibile, riprende. Un giro
parziale non sovrascrive uno completo. In sola lettura per contratto: nessuna scrittura senza `card`, che passa dal registro
con l'ok. Mai sul Chromebook se puo' girare altrove: --host esegue via ssh, e li' la cartella vuota la dai con --controllo.

PROMEMORIA: niente motore di regole: scatta una volta e sparisce. --until come `needs-you`, ogni ora da `suggest fill`.
--at per una condizione che non si puo' verificare. Allo scatto notifica e suggerimento; --prompt parte solo da Accetta.
"""
import hashlib
import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)
BATCH = 10
DETECT_TIMEOUT_S = 120
UNTIL_TIMEOUT_S = 20
FIRED_KEEP = 20
RUN_KEEP = 10
_stop = {"now": False}


def M(_k, **kw):
    return cm.msg(CFG, _k, **kw)


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sdir():
    return Path(cm.expand(CFG["state_dir"]))


def rj(p, default):
    try:
        d = json.loads(Path(p).read_text())
        return d if isinstance(d, type(default)) else default
    except (OSError, ValueError):
        return default


def wj(p, d):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    t = p.with_suffix(".tmp")
    t.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    t.replace(p)


def opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            sys.exit(f"{name}: missing value")
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


def flag(args, name):
    if name in args:
        args.remove(name)
        return True
    return False


def shell(cmd, cwd=None, timeout=UNTIL_TIMEOUT_S):
    """Codice di uscita, o None se non e' finito (un controllo che non risponde non dice niente)."""
    try:
        return subprocess.run(["nice", "-n", "19", "sh", "-c", cmd], cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout).returncode
    except (OSError, subprocess.SubprocessError):
        return None


def git(project, *a):
    try:
        p = subprocess.run(["git", "-C", project, *a], capture_output=True, text=True, timeout=30)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def open_task(tid, title, project, session, check, expected, plan):
    """Un compito nel registro, poi l'ok: ogni correzione passa da li'."""
    tk = _load("cm-tasks")
    con = tk.connect(create=True)
    contract = {"schema_version": 1, "task": {
        "id": tid, "title": title[:200], "type": "fix", "perimeter": ["**"],
        "where": {"project": project, "session": session or None, "host": "local", "account": None},
        "check": {"cmd": check, "cwd": project, "timeout_s": 120},
        "outcome": {"expected": expected, "kind": "custom", "proof": "il controllo rieseguito"},
        "plan": plan, "lane": "closed", "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal",
        "requested_by": plan}}
    got, errs = tk.add(con, contract)
    con.commit()
    return got, errs


# ------------------------------------------------------------------ rimasto indietro
def rimasto_path():
    return sdir() / "rimasto.json"


def stale_of(item):
    """(indietro?, motivo). Git che non risponde non fa indietro: si dice che non si sa."""
    project = cm.expand(item["project"])
    if not os.path.isdir(project):
        return None, "no_project"
    changed = None
    if item.get("paths"):
        out = git(project, "log", "--format=%h", f"{item.get('aligned') or 'HEAD'}..HEAD", "--", *item["paths"])
        changed = len(out.splitlines()) if out is not None else None
    if changed:
        return True, f"paths:{changed}"
    if item.get("check"):
        rc = shell(item["check"], cwd=project)
        if rc is None:
            return None, "check_timeout"
        if rc != 0:
            return True, "check"
    return (None, "no_git") if changed is None and item.get("paths") else (False, "")


def rimasto_rows(save=True):
    d = rj(rimasto_path(), {})
    rows = []
    for product, items in sorted(d.items()):
        for key, it in sorted(items.items()):
            stale, why = stale_of(it)
            rows.append({"product": product, "key": key, "title": it.get("title") or key, "where": it.get("where"), "stale": stale, "why": why,
                         "session": it.get("session"), "aligned_version": it.get("aligned_version"), "aligned_at": it.get("aligned_at"), "task": it.get("task")})
    if save:
        wj(sdir() / "rimasto-stale.json", {"at": int(time.time()), "n": sum(1 for r in rows if r["stale"])})
    return rows


def rimasto_ok(product, key):
    d = rj(rimasto_path(), {})
    it = (d.get(product) or {}).get(key)
    if not it:
        return False, M("seguiti.no_item", item=f"{product}/{key}")
    project = cm.expand(it["project"])
    it.update(aligned=git(project, "rev-parse", "HEAD"), aligned_version=git(project, "describe", "--tags", "--abbrev=0"), aligned_at=int(time.time()))
    it.pop("task", None)
    wj(rimasto_path(), d)
    rimasto_rows()
    return True, M("seguiti.rimasto_ok", item=key)


def rimasto_allinea(product, key):
    d = rj(rimasto_path(), {})
    it = (d.get(product) or {}).get(key)
    if not it:
        return False, M("seguiti.no_item", item=f"{product}/{key}")
    tid = re.sub(r"[^a-z0-9-]+", "-", f"allinea-{product}-{key}".lower()) + time.strftime("-%d-%m")
    check = it.get("check") or f"{shlex.quote(str(HERE / 'supervisor'))} rimasto verifica {shlex.quote(product)} {shlex.quote(key)}"
    title = M("seguiti.allinea_title", product=product, title=it.get("title") or key)
    got, errs = open_task(tid, title, cm.expand(it["project"]), it.get("session"), check, M("seguiti.allinea_expected", where=it.get("where") or key),
                          M("seguiti.allinea_plan", where=it.get("where") or key, paths=", ".join(it.get("paths") or [])))
    if errs:
        return False, str(errs[0])[:200]
    it["task"] = got
    wj(rimasto_path(), d)
    return True, got


def cmd_rimasto(args):
    verb = args.pop(0) if args else "list"
    as_json = flag(args, "--json")
    if verb == "add":
        where, paths, check, project, session, title = (opt(args, x) for x in ("--dove", "--percorsi", "--comando", "--progetto", "--sessione", "--titolo"))
        if len(args) != 2 or not where or not (paths or check):
            sys.exit("usage: rimasto add PRODOTTO CHIAVE --dove T --percorsi a,b [--comando C] [--progetto DIR] [--sessione S] [--titolo T]")
        project = os.path.realpath(cm.expand(project or os.getcwd()))
        d = rj(rimasto_path(), {})
        d.setdefault(args[0], {})[args[1]] = {"where": where, "paths": [x for x in (paths or "").split(",") if x], "check": check, "project": project, "session": session,
                                              "title": title or args[1], "aligned": git(project, "rev-parse", "HEAD"), "aligned_at": int(time.time()),
                                              "aligned_version": git(project, "describe", "--tags", "--abbrev=0")}
        wj(rimasto_path(), d)
        return 0
    if verb in ("ok", "allinea", "remove", "verifica"):
        if len(args) != 2:
            sys.exit(f"usage: rimasto {verb} PRODOTTO CHIAVE")
        if verb == "verifica":   # il controllo di un compito «Allinea»: verde quando la voce e' stata segnata allineata
            return 0 if next((r for r in rimasto_rows(save=False) if (r["product"], r["key"]) == tuple(args)), {}).get("stale") is False else 1
        if verb == "remove":
            d = rj(rimasto_path(), {})
            (d.get(args[0]) or {}).pop(args[1], None)
            wj(rimasto_path(), d)
            return 0
        ok, text = (rimasto_ok if verb == "ok" else rimasto_allinea)(*args)
        print(text)
        return 0 if ok else 1
    if verb in ("list", "check"):
        rows = rimasto_rows()
        if as_json:
            print(json.dumps(rows, ensure_ascii=False))
        elif verb == "list":
            for r in rows:
                mark = {True: "INDIETRO", False: "ok", None: "?"}[r["stale"]]
                print(f"{mark:<9} {r['product']}/{r['key']}  {r['where']}  {r['why']}")
        return 0
    sys.exit(__doc__)


# ------------------------------------------------------------------ stesso difetto altrove
def runs_dir():
    return sdir() / "altrove"


def default_targets():
    ws = CFG["workspace"]
    root = Path(cm.expand(ws["root"]))
    skip = set(ws.get("excluded_dirs") or [])
    out = []
    for pd in ws.get("project_dirs") or ["."]:
        base = root if pd in (".", "") else root / pd
        for d in sorted(base.iterdir()) if base.is_dir() else []:
            if d.is_dir() and d.name not in skip and not d.name.startswith("."):
                out.append(str(d))
    return out


def detect(cmd, target, host=None):
    """positive | negative | error. Un errore non e' mai un negativo."""
    if host:
        try:
            rc = subprocess.run(["ssh", host, f"cd {shlex.quote(target)}; {cmd}"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=DETECT_TIMEOUT_S).returncode
        except (OSError, subprocess.SubprocessError):
            rc = None
    else:
        rc = shell(cmd, cwd=target, timeout=DETECT_TIMEOUT_S) if os.path.isdir(target) else None
    return "positive" if rc == 0 else "negative" if rc == 1 else "error"


def run_view(r):
    """Vale il giro completo; quello in corso si dice, senza sostituirlo."""
    base = r.get("complete") or r["current"]
    res = base["results"]
    return {"id": r["id"], "text": r["text"], "complete": bool(r.get("complete")), "at": base.get("at"), "total": len(base["targets"]), "done": len(res),
            "positive": sorted(k for k, v in res.items() if v == "positive"), "errors": sorted(k for k, v in res.items() if v == "error"),
            "running": not r["current"].get("finished"), "task": r.get("task")}


def run_altrove(r, host=None):
    cur = r["current"]
    todo = [t for t in cur["targets"] if t not in cur["results"]]
    path = runs_dir() / f"{r['id']}.json"
    for i in range(0, len(todo), BATCH):
        for t in todo[i:i + BATCH]:
            if _stop["now"]:
                wj(path, r)
                return False
            cur["results"][t] = detect(r["cmd"], t, host)
        wj(path, r)
    cur["finished"] = int(time.time())
    r["complete"] = dict(cur, at=cur["finished"])
    wj(path, r)
    return True


def cmd_altrove(args):
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    as_json = flag(args, "--json")
    if args[0] == "list":
        rows = [run_view(rj(f, {})) for f in sorted(runs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)][:RUN_KEEP]
        print(json.dumps(rows, ensure_ascii=False) if as_json else "\n".join(f"{x['id']} {x['text']}: {len(x['positive'])} positivi su {x['total']}" for x in rows))
        return 0
    if args[0] in ("card", "verifica"):
        verb = args.pop(0)
        fix, session = opt(args, "--correzione"), opt(args, "--sessione")
        p = runs_dir() / f"{args[0] if args else ''}.json"
        r = rj(p, {})
        if not r:
            sys.exit(f"altrove: no run {args[0] if args else ''}")
        v = run_view(r)
        if verb == "verifica":   # il controllo del compito: nessun positivo, rieseguendo sui positivi
            return 0 if not any(detect(r["cmd"], t, r.get("host")) != "negative" for t in v["positive"]) else 1
        if not fix:
            sys.exit("usage: altrove card ID --correzione T [--sessione S]")
        if not v["positive"]:
            print(M("seguiti.altrove_none"))
            return 0
        tid = f"altrove-{r['id']}"
        got, errs = open_task(tid, M("seguiti.altrove_title", text=r["text"], n=len(v["positive"])), os.path.realpath(cm.expand(CFG["workspace"]["root"])), session or None,
                              f"{shlex.quote(str(HERE / 'supervisor'))} altrove verifica {r['id']}", M("seguiti.altrove_expected", text=r["text"]),
                              M("seguiti.altrove_plan", text=r["text"], fix=fix, targets=", ".join(v["positive"])[:800]))
        if errs:
            print(errs[0], file=sys.stderr)
            return 1
        subprocess.run([str(HERE / "supervisor"), "task", "wait-ok", got, "--what", fix[:300], "--where", ", ".join(Path(x).name for x in v["positive"])[:300],
                        "--untested", M("seguiti.altrove_untested"), "--undo", "in-parte"], check=False)
        r["task"] = got
        wj(p, r)
        print(got)
        return 0
    cmd, targets, host, control = opt(args, "--rivelatore"), opt(args, "--obiettivi"), opt(args, "--host"), opt(args, "--controllo")
    dry, again = flag(args, "--prova"), flag(args, "--da-capo")
    if len(args) != 1 or not cmd:
        sys.exit('usage: altrove "descrizione" --rivelatore CMD [--obiettivi a,b] [--host H --controllo DIR] [--prova] [--da-capo] [--json]')
    if host and not control:
        sys.exit("altrove: --host wants --controllo, an empty folder on that host")
    if host and not targets:
        sys.exit("altrove: --host wants --obiettivi, the folders on that host")
    list_t = [x for x in (targets or "").split(",") if x] or default_targets()
    # un rivelatore positivo dove non c'e' niente non dimostra niente
    if host:
        ctl = detect(cmd, control, host)
    else:
        with tempfile.TemporaryDirectory() as td:
            ctl = detect(cmd, td)
    if ctl != "negative":
        print(M("seguiti.altrove_control", outcome=ctl), file=sys.stderr)
        return 2
    rid = hashlib.sha1((args[0] + "\0" + cmd).encode()).hexdigest()[:10]
    if dry:
        print(json.dumps({"id": rid, "targets": list_t, "control": ctl}, ensure_ascii=False) if as_json else "\n".join(list_t))
        return 0
    p = runs_dir() / f"{rid}.json"
    r = rj(p, {}) or {"id": rid, "text": args[0], "cmd": cmd, "host": host}
    if again or not r.get("current") or r["current"].get("finished"):
        r["current"] = {"targets": list_t, "results": {}, "started": int(time.time())}
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: _stop.__setitem__("now", True))
    os.nice(19)
    done = run_altrove(r, host)
    v = run_view(r)
    print(json.dumps(v, ensure_ascii=False) if as_json else M("seguiti.altrove_result", n=len(v["positive"]), total=v["total"], errors=len(v["errors"]), id=rid))
    return 0 if done else 130


# ------------------------------------------------------------------ promemoria
def remind_path():
    return sdir() / "reminders.json"


def remind_load():
    d = rj(remind_path(), {})
    d.setdefault("items", [])
    d.setdefault("fired", [])
    return d


def remind_tick(now=None):
    """Una volta l'ora da `suggest fill`: chi scatta diventa un suggerimento e sparisce; chi scade sparisce e basta."""
    now = int(now or time.time())
    d = remind_load()
    if not d["items"]:
        return []
    keep, fired = [], []
    for x in d["items"]:
        if x.get("expires_at") and now >= x["expires_at"]:
            d["fired"].append(dict(x, outcome="expired", at=now))
            continue
        if x.get("at_time"):
            hit = now >= x["at_time"]
        else:
            hit = shell(x["until"], cwd=x.get("cwd") if os.path.isdir(x.get("cwd") or "") else None) == 0
        if hit:
            fired.append(x)
            d["fired"].append(dict(x, outcome="fired", at=now))
        else:
            keep.append(x)
    d["items"], d["fired"] = keep, d["fired"][-FIRED_KEEP:]
    if fired:
        sg = _load("cm-suggest")
        s = sg.load()
        for x in fired:
            key = f"remind:{x['id']}"
            if key not in s["never"]:
                sg.upsert(s, now, "reminder", key, x["text"], x.get("prompt") or M("seguiti.remind_prompt", text=x["text"]), 7 * 86400, source="script")
        sg.save(s, now)
    wj(remind_path(), d)
    return [x["id"] for x in fired]


def cmd_remind(args):
    verb = args.pop(0) if args else "list"
    as_json = flag(args, "--json")
    if verb == "add":
        until, expires, prompt, at = opt(args, "--until"), opt(args, "--expires"), opt(args, "--prompt"), opt(args, "--at")
        if len(args) != 1 or bool(until) == bool(at):
            sys.exit('usage: remind add "testo" --until CMD [--expires D] [--prompt P]  |  --at AAAA-MM-GG')
        now = int(time.time())
        x = {"id": hashlib.sha1(f"{args[0]}{now}".encode()).hexdigest()[:8], "text": args[0], "until": until, "prompt": prompt, "created_at": now, "cwd": os.getcwd()}
        if at:
            try:
                x["at_time"] = int(time.mktime(time.strptime(at, "%Y-%m-%d")))
            except ValueError:
                sys.exit("remind: --at AAAA-MM-GG")
        if expires:
            m = re.fullmatch(r"(\d+)([smhd])", expires)
            if m:
                x["expires_at"] = now + int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
            else:
                try:
                    x["expires_at"] = int(time.mktime(time.strptime(expires, "%Y-%m-%d")))
                except ValueError:
                    sys.exit("remind: --expires 30d or AAAA-MM-GG")
        d = remind_load()
        d["items"].append(x)
        wj(remind_path(), d)
        print(x["id"])
        return 0
    if verb == "remove":
        return 0 if remind_remove(args[0] if args else "") else 1
    if verb == "list":
        d = remind_load()
        print(json.dumps(d, ensure_ascii=False) if as_json else "\n".join(f"{x['id']}  {x['text']}" for x in d["items"]))
        return 0
    sys.exit(__doc__)


def remind_remove(rid):
    d = remind_load()
    n = len(d["items"])
    d["items"] = [x for x in d["items"] if x["id"] != rid]
    wj(remind_path(), d)
    return len(d["items"]) < n


# ------------------------------------------------------------------ per il relay
def summary():
    """Il blocco di /state, senza git: il conto del giro orario. Manca quando e' tutto a zero."""
    n = int(rj(sdir() / "rimasto-stale.json", {}).get("n") or 0)
    pos = 0
    if runs_dir().is_dir():
        for f in runs_dir().glob("*.json"):
            r = rj(f, {})
            if r and not r.get("task"):
                pos += len(run_view(r)["positive"])
    out = {"rimasto": n, "altrove": pos, "promemoria": len(remind_load()["items"])}
    return out if any(out.values()) else None


def view():
    """L'op seguiti, una lettura."""
    runs = [run_view(rj(f, {})) for f in sorted(runs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)][:RUN_KEEP] if runs_dir().is_dir() else []
    d = remind_load()
    keys = ("id", "text", "created_at", "expires_at", "at_time")
    return {"at": int(time.time()), "rimasto": rimasto_rows(), "altrove": runs,
            "promemoria": [{k: x.get(k) for k in keys} for x in d["items"]],
            "promemoria_scattati": [{"id": x["id"], "text": x["text"], "outcome": x["outcome"], "at": x["at"]} for x in d["fired"]]}


def main(argv):
    name = Path(sys.argv[0]).name
    verb = argv[0] if argv else ""
    args = argv[1:]
    if verb == "rimasto":
        return cmd_rimasto(args)
    if verb == "altrove":
        return cmd_altrove(args)
    if verb == "remind":
        return cmd_remind(args)
    sys.exit(f"{name}: rimasto | altrove | remind")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
