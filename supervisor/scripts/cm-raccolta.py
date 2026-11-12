#!/usr/bin/env python3
"""supervisor prima-raccolta — una volta sola, quello che oggi aspetta il cron (funzione 10, contratto 1.100, 02/11/2026).

  supervisor prima-raccolta [--sessions si|no] [--again] [--json]

Passi in ordine: macchina, altre macchine, quota, plugin e skill, progetti, sessioni vive, sessioni passate, suggerimenti.
Nessuna chiamata a un modello: `claude` solo per `plugin marketplace update`, come il controllo di ogni 6 ore. A bassa
priorita', a lotti, interrompibile: Ctrl-C o SIGTERM lasciano in <state_dir>/prima-raccolta.json i passi fatti e il
cursore, e il giro dopo riprende da li'.

SESSIONI PASSATE: si leggono solo con un consenso, chiesto una volta (qui, con --sessions o dal telefono). Senza, nessuna
trascrizione si apre. Con il si': quante sessioni negli ultimi 14 giorni e, per le piu' recenti, la riga «Prossimi» con cui
si sono fermate, che diventa un suggerimento. I dati restano sulla macchina; al telefono arrivano cifrati come il resto.
Dei prompt non si tiene niente.

Una sezione vuota dice perche' e quando si riempira'. Il primo giro della master consuma quota: la lettura lo dichiara e
parte solo da un tocco (op prima_raccolta_azione), una volta.
"""
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)
STEPS = ("machine", "hosts", "quota", "plugins", "projects", "live", "sessions", "suggestions")
DAYS = 14
BATCH = 25
FOLLOW_MAX = 3
FOLLOW_TTL_S = 72 * 3600
TAIL_BYTES = 256 * 1024
_stop = {"now": False}


class Interrupted(Exception):
    pass


def M(key, **kw):
    return cm.msg(CFG, key, **kw)


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def path():
    return Path(cm.expand(CFG["state_dir"])) / "prima-raccolta.json"


def read_status():
    try:
        d = json.loads(path().read_text())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def save(s):
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False))
    tmp.replace(p)


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _cli(*args, timeout=60):
    """(stdout) di un comando di supervisor; "" se non risponde: una lettura mancante e' una sezione vuota, non un errore."""
    try:
        return subprocess.run([str(HERE / "supervisor"), *args], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# ------------------------------------------------------------------ i passi
def step_machine(_s):
    st = _load("cm-system").measure()
    return {"load": st["load1"], "cores": st["cores"], "mem": st["mem_available_mb"], "level": st["level"]}


def step_hosts(_s):
    h = _load("cm-hosts")
    names = [n for n in h.hosts() if n != "local"]
    if names:
        h.poll_round(names, True)
    up = 0
    for n in names:
        try:
            snap = json.loads(h.snap_path(n).read_text())
            up += bool(snap.get("read_at")) and not snap.get("error")
        except (OSError, ValueError):
            pass
    return {"n": len(names), "up": up}


def step_quota(_s):
    try:
        q = json.loads(_cli("quota", "--json", "--no-screen"))
    except ValueError:
        q = {}
    quota_monitor(q)
    return {"n": sum(1 for x in q.values() if isinstance(x, dict) and x.get("stato") == "ok"), "accounts": len(q)}


QUOTA_MONITOR = "consumo-quota"


def quota_monitor(q):
    """1.139 (12/11, supervisor-avvio-popola-12-11): la prima voce dei Monitoraggi. Senza nessun monitoraggio, uno
    d'esempio sul consumo settimanale della quota (meter, --example), un punto a ogni raccolta per account letto. Se ce
    n'e' gia' uno, nessuno nuovo; quello d'esempio continua a ricevere i punti."""
    ok = {a: x for a, x in (q or {}).items() if isinstance(x, dict) and x.get("stato") == "ok" and x.get("settimana_pct") is not None}
    if not ok:
        return
    MO = _load("cm-monitor")
    have = MO.ids()
    if QUOTA_MONITOR not in have:
        if have:
            return
        names = ",".join(re.sub(r"[^A-Za-z0-9 ._+-]", "-", a)[:40] for a in ok)
        _cli("monitor", "add", QUOTA_MONITOR, "--title", M("raccolta.quota_monitor_title"), "--form", "table" if len(ok) > 1 else "meter",
             "--unit", "%", "--max", "100", "--series", names, "--source", M("raccolta.quota_monitor_source"), "--every", "1h", "--example")
    for a, x in ok.items():
        _cli("monitor", "point", QUOTA_MONITOR, "--series", re.sub(r"[^A-Za-z0-9 ._+-]", "-", a)[:40], str(x["settimana_pct"]))


def step_plugins(_s):
    pl = _load("cm-plugins")
    pl.check(force=True)
    st = pl.collect()
    plugins = [p for a in st["accounts"] for p in a["plugins"]]
    return {"n": len(plugins), "updates": sum(1 for p in plugins if p["update"]), "skills": sum(len(a["skills"]) for a in st["accounts"])}


def step_projects(_s):
    """Le cartelle lanciabili, con le regole di `Lancia` (cm-relay, projects)."""
    ws = CFG["workspace"]
    root = Path(cm.expand(ws["root"]))
    skip = set(ws.get("excluded_dirs") or [])
    n = 0

    def good(d):
        return d.is_dir() and d.name not in skip and not d.name.startswith(".")
    for pd in ws.get("project_dirs") or ["."]:
        base = root if pd in (".", "") else root / pd
        for d in sorted(base.iterdir()) if base.is_dir() else []:
            if good(d):
                n += 1
                if pd in (".", ""):
                    try:
                        n += sum(1 for dd in d.iterdir() if good(dd))
                    except OSError:
                        pass
    return {"n": n}


def step_live(_s):
    try:
        rows = json.loads(_cli("sessions", "--json"))
    except ValueError:
        rows = []
    if isinstance(rows, dict):
        rows = rows.get("sessions") or rows.get("sessioni") or []
    return {"n": len(cm.scope_rows(CFG, rows))}   # 12/11 (supervisor-ambito-sessioni-12-11): solo l'ambito


def read_tail(f):
    """(cwd, testo dell'ultima risposta) dalla coda di una trascrizione. I prompt non si guardano."""
    try:
        with open(f, "rb") as fh:
            size = fh.seek(0, 2)
            fh.seek(max(size - TAIL_BYTES, 0))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None, ""
    cwd, text = None, ""
    for line in reversed(lines):
        try:
            x = json.loads(line)
        except ValueError:
            continue
        if not isinstance(x, dict):
            continue
        cwd = cwd or x.get("cwd")
        if not text and x.get("type") == "assistant":
            c = (x.get("message") or {}).get("content")
            text = c if isinstance(c, str) else "\n".join(b.get("text") or "" for b in c or [] if isinstance(b, dict) and b.get("type") == "text")
        if cwd and text:
            break
    return cwd, text


def past_files(now):
    out = []
    for a in (CFG.get("accounts") or {}).values():
        for f in (Path(cm.expand(a.get("config_dir") or "")) / "projects").glob("*/*.jsonl"):
            if not cm.transcript_in_scope(CFG, f):   # 12/11 (supervisor-ambito-sessioni-12-11): solo l'ambito
                continue
            try:
                t = f.stat().st_mtime
            except OSError:
                continue
            if now - t <= DAYS * 86400:
                out.append((t, str(f)))
    return [f for _, f in sorted(out, reverse=True)]


def step_sessions(s):
    """Le sessioni passate, a lotti, con il cursore nello stato. Il consenso si guarda qui: senza, niente si apre."""
    if s.get("consent") is not True:
        return {"state": "waiting" if s.get("consent") is None else "skipped", "n": 0}
    files = past_files(time.time())
    partial = s.get("partial") or {"follow": []}
    state = _load("cm-relay-state")
    follow = partial["follow"]
    seen = {x["project"] for x in follow}
    for i in range(int(s.get("cursor") or 0), len(files), BATCH):
        for f in files[i:i + BATCH]:
            if len(follow) < FOLLOW_MAX:
                cwd, text = read_tail(f)
                steps = state.next_steps(text) if cwd and cwd not in seen and os.path.isdir(cwd) else []
                steps = [x for x in steps if not x["text"].startswith("/")]   # dal vivo, 02/11: «/clear» non e' un seguito
                if steps:
                    seen.add(cwd)
                    follow.append({"sid": Path(f).stem, "project": cwd, "step": next((x for x in steps if x.get("blocking")), steps[0])["text"]})
        s["cursor"], s["partial"] = min(i + BATCH, len(files)), {"follow": follow}
        save(s)
        if _stop["now"]:
            raise Interrupted()
    sg = _load("cm-suggest")
    d = sg.load()
    now = int(time.time())
    for x in follow:
        key = f"raccolta:{x['sid']}"
        if key not in d["never"]:
            project = sg.label(x["project"], Path(x["project"]).name)
            sg.upsert(d, now, "followup", key, M("raccolta.followup_text", project=project, step=x["step"]),
                      M("raccolta.followup_prompt", project=x["project"], step=x["step"]), FOLLOW_TTL_S, source="script")
    sg.save(d, now)
    s.pop("cursor", None)
    s.pop("partial", None)
    return {"n": len(files), "follow": len(follow)}


def step_suggestions(_s):
    r = _load("cm-suggest").fill()
    return {"filled": r is not None}


RUN = {"machine": step_machine, "hosts": step_hosts, "quota": step_quota, "plugins": step_plugins, "projects": step_projects,
       "live": step_live, "sessions": step_sessions, "suggestions": step_suggestions}


# ------------------------------------------------------------------ le sezioni
def _cron():
    try:
        return subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-l"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _sec(key, dove, n, text, why=None, when=None):
    return {"key": key, "dove": dove, "n": n, "text": text if n else None, "why": None if n else why, "when": None if n else when}


def sections(s):
    st = s.get("steps") or {}
    r = lambda k: st.get(k) or {}  # noqa: E731

    def plain(key, dove, n, text_key, **kw):
        if key not in st and key not in ("night", "recap"):
            return _sec(key, dove, 0, None, M("raccolta.pending_why"), M("raccolta.pending_when"))
        return _sec(key, dove, n, M(f"raccolta.{text_key}", n=n, **kw) if n else None, M(f"raccolta.{key}_why"), M(f"raccolta.{key}_when"))
    m = r("machine")
    p = r("plugins")
    out = [plain("machine", "home", 1 if m else 0, "machine_text", load=m.get("load"), cores=m.get("cores"), mem=m.get("mem")),
           plain("hosts", "home", r("hosts").get("n", 0), "hosts_text", up=r("hosts").get("up", 0)),
           plain("quota", "usage", r("quota").get("n", 0), "quota_text"),
           plain("plugins", "settings", p.get("n", 0), "plugins_text", updates=p.get("updates", 0), skills=p.get("skills", 0)),
           plain("projects", "launch", r("projects").get("n", 0), "projects_text"),
           plain("live", "home", r("live").get("n", 0), "live_text")]
    ses = r("sessions")
    if ses.get("n"):
        out.append(_sec("sessions", "search", ses["n"], M("raccolta.sessions_text", n=ses["n"], days=DAYS, follow=ses.get("follow", 0))))
    else:
        wait = s.get("consent") is None
        out.append(_sec("sessions", "search", 0, None, M("raccolta.sessions_ask_why" if wait else "raccolta.sessions_no_why" if s.get("consent") is False else "raccolta.sessions_why"),
                        M("raccolta.sessions_ask_when" if wait else "raccolta.sessions_no_when" if s.get("consent") is False else "raccolta.sessions_when")))
    sg = _load("cm-suggest")
    now = int(time.time())
    try:
        n_sug = len(sg.live(sg.load()["items"], now))
    except Exception:   # noqa: BLE001 — una sezione vuota, non un errore
        n_sug = 0
    out.append(plain("suggestions", "home", n_sug, "suggestions_text"))
    try:
        queue = sum(1 for x in (Path(cm.expand(CFG["state_dir"])) / "night-queue.jsonl").read_text().splitlines() if x.strip())
    except OSError:
        queue = 0
    night_t = (CFG.get("night") or {}).get("cron_time", "02:00")
    cron = _cron()
    out.append(_sec("night", "night", queue, M("raccolta.night_text", n=queue, at=night_t), M("raccolta.night_why"),
                    M("raccolta.night_when", at=night_t) if "night run" in cron else M("raccolta.night_off")))
    recap_t = (CFG.get("recap") or {}).get("cron_time", "20:00")
    try:
        items = ((json.loads(Path(sg.state_path()).read_text()).get("recap")) or {}).get("items") or []
    except (OSError, ValueError):
        items = []
    out.append(_sec("recap", "home", len(items), M("raccolta.recap_text", n=len(items)), M("raccolta.recap_why"), M("raccolta.recap_when", at=recap_t)))
    return out


def _counts(s):
    st = s.get("steps") or {}
    return sum(1 for k in STEPS if k in st and st[k].get("state") != "waiting"), len(STEPS)


def view(s=None):
    """Quello che legge l'op prima_raccolta."""
    s = read_status() if s is None else s
    if not s:
        s = {"status": "none", "steps": {}, "consent": None}
    done, total = _counts(s)
    ask = asking(s)
    # 1.139 (12/11, il maintainer alle 15:38: «serve molto più sintetico, tipo: Per completare la configurazione, effettua la
    # scansione delle sessioni»): una riga, sotto in piccolo le due garanzie, i tasti corti; il si' fa anche il primo giro
    return {"at": int(time.time()), "status": s["status"], "done": done, "total": total, "consent": s.get("consent"),
            "ask_text": M("raccolta.consent_question", days=DAYS) if ask else None, "cost": 0, "sections": sections(s),
            "ask": {"text": M("raccolta.consent_question", days=DAYS), "note": M("raccolta.consent_note"), "yes": M("raccolta.consent_yes"),
                    "later": M("raccolta.consent_later"), "first_round": True} if ask else None,
            "first_round": {"at": s.get("first_round_at"), "cost": M("raccolta.first_round_cost")}}


LATER_S = 86400


ASK_V = 2   # 1.139: la domanda breve; chi aveva detto no alla vecchia la rivede una volta


def asking(s):
    """La domanda va mostrata, anche a chi ha installato prima (il maintainer alle 16:32: «serve anche che lo riproponga
    a chi ha installato e non l'ha fatto»): nessuna risposta; un no alla domanda vecchia (una volta, col testo nuovo); un
    si' senza il primo giro della master. Mai entro 24 ore da un «Non ora». Raccolta e primo giro fatti: niente."""
    if int(s.get("ask_after") or 0) > time.time():
        return False
    c = s.get("consent")
    return c is None or (c is False and int(s.get("asked_v") or 1) < ASK_V) or (c is True and not s.get("first_round_at"))


def summary():
    """Il blocco di /state, piccolo; manca se la raccolta non e' mai partita o se e' finita col primo giro gia' fatto."""
    s = read_status()
    if not s:   # 1.139: un'installazione che non ha mai fatto la raccolta riceve comunque la domanda
        return {"status": "none", "done": 0, "total": len(STEPS), "ask": asking({}), "first_round_at": None}
    if s.get("status") == "done" and s.get("first_round_at"):
        return None
    done, total = _counts(s)
    return {"status": s.get("status"), "done": done, "total": total, "ask": asking(s), "first_round_at": s.get("first_round_at")}


def set_consent(value):
    s = read_status() or {"status": "none", "steps": {}, "consent": None}
    s["asked_v"] = ASK_V   # una risposta alla domanda nuova vale: non si richiede piu'
    if s.get("consent") is not value:
        s["consent"] = bool(value)
        (s.get("steps") or {}).pop("sessions", None)
        (s.get("steps") or {}).pop("suggestions", None)
        s.pop("cursor", None)
        s.pop("partial", None)
    save(s)
    return s


def ask_later():
    """«Non ora»: la domanda torna fra LATER_S secondi; nessun consenso scritto."""
    s = read_status() or {"status": "none", "steps": {}, "consent": None}
    s["ask_after"] = int(time.time()) + LATER_S
    save(s)
    return s


def first_round(cli=None):
    """Il primo giro della master, una volta sola: True se e' partito ora."""
    if not mark_first_round():
        return False
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    try:
        subprocess.run([cli or os.environ.get("CM_BIN") or str(HERE / "supervisor"), "talk", master, M("raccolta.first_round_prompt"), "--no-wait"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


FIRST_TASK = "primi-passi"


def first_steps():
    """1.139: la prima voce di «Verifica» su un'installazione nuova (registro senza compiti): il compito primi-passi con
    una prova, «manda un messaggio dal telefono». Con un registro gia' usato niente. Torna l'id della prova o None."""
    tk = _load("cm-tasks")
    con = tk.connect()
    try:
        if con.execute("SELECT 1 FROM tasks LIMIT 1").fetchone():
            return None
    finally:
        con.close()
    root = cm.expand((CFG.get("workspace") or {}).get("root") or "~")
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    c = {"schema_version": 1, "task": {"id": FIRST_TASK, "title": M("raccolta.first_task_title"), "plan": M("raccolta.first_task_plan"), "short": M("raccolta.first_task_short"),
                                       "where": {"project": root, "session": master, "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": root, "timeout_s": 30}, "perimeter": ["-"], "lane": "open", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "supervisor prima-raccolta",
                                       "outcome": {"expected": M("raccolta.first_prova_atteso"), "kind": "custom", "proof": M("raccolta.first_prova_atteso")}}}
    cli = os.environ.get("CM_BIN") or str(HERE / "supervisor")
    try:
        a = subprocess.run([cli, "task", "add", "-"], input=json.dumps(c, ensure_ascii=False), capture_output=True, text=True, timeout=60)
        if a.returncode != 0:
            return None
        subprocess.run([cli, "task", "start", FIRST_TASK], capture_output=True, text=True, timeout=60)
        p = subprocess.run([cli, "task", "prova", FIRST_TASK, "--build", "avvio", "--dove", "home", "--fai", M("raccolta.first_prova_fai"),
                            "--atteso", M("raccolta.first_prova_atteso")], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def mark_first_round():
    """True la prima volta: il giro della master parte una volta sola."""
    s = read_status() or {"status": "none", "steps": {}, "consent": None}
    if s.get("first_round_at"):
        return False
    s["first_round_at"] = int(time.time())
    save(s)
    return True


# ------------------------------------------------------------------ il giro
def run(s):
    stop_at = os.environ.get("CM_RACCOLTA_STOP_AT")
    for k in STEPS:
        if _stop["now"] or k == stop_at:
            raise Interrupted()
        if (s["steps"].get(k) or {}).get("state") in ("done", "skipped"):
            continue
        try:
            res = RUN[k](s)
        except Interrupted:
            raise
        except Exception as e:   # noqa: BLE001 — un passo che non riesce non ferma gli altri, e si riprova al giro dopo
            res = {"state": "failed", "error": str(e)[:160]}
        s["steps"][k] = dict({"state": "done", "at": int(time.time())}, **res)
        save(s)
    return "ask" if s.get("consent") is None else "done"


def _push():
    try:
        d = Path(cm.expand((CFG.get("relay") or {}).get("dir") or "")) / "push-req"
        d.mkdir(parents=True, exist_ok=True)
        (d / "prima-raccolta").touch()
    except OSError:
        pass


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    want = None
    if "--sessions" in argv:
        i = argv.index("--sessions")
        val = argv[i + 1] if i + 1 < len(argv) else ""
        if val not in ("si", "sì", "yes", "no"):
            print("prima-raccolta: --sessions si|no", file=sys.stderr)
            return 2
        want = val != "no"
    s = read_status() or {}
    if s.get("status") == "running" and alive(s.get("pid")) and int(s["pid"]) != os.getpid():
        print(M("raccolta.already_running", pid=s["pid"]), file=sys.stderr)
        return 1
    if "--again" in argv:
        s = {"consent": s.get("consent"), "first_round_at": s.get("first_round_at")}
    s = dict({"consent": None, "steps": {}, "started_at": int(time.time())}, **s)
    if want is not None:
        s["asked_v"] = ASK_V
    if want is not None and s.get("consent") is not want:
        s["consent"] = want
        s["steps"].pop("sessions", None)
        s["steps"].pop("suggestions", None)
        s.pop("cursor", None)
        s.pop("partial", None)
    if s.get("consent") is None and sys.stdin.isatty() and "--json" not in argv:
        print(M("raccolta.consent_note"))
        s["consent"] = input(M("raccolta.consent_question", days=DAYS) + " [s/N] ").strip().lower() in ("s", "si", "sì", "y", "yes")
    if (s["steps"].get("sessions") or {}).get("state") == "waiting":
        s["steps"].pop("sessions")   # si rilegge a ogni giro: dice waiting finche' manca la risposta
    os.nice(19)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: _stop.__setitem__("now", True))
    s.update(status="running", pid=os.getpid())
    save(s)
    rc = 0
    try:
        s["status"] = run(s)
        s["finished_at"] = int(time.time())
    except Interrupted:
        s["status"], rc = "interrupted", 130
    s.pop("pid", None)
    save(s)
    if rc == 0 and "--first-round" in argv and s.get("consent") is True:   # 1.139: col si' il primo giro parte da solo
        first_round()
    if rc == 0 and not s.get("first_steps_at"):
        first_steps()
        s["first_steps_at"] = int(time.time())   # una volta sola, che abbia creato la prova o no
        save(s)
    _push()
    v = view(s)
    if "--json" in argv:
        print(json.dumps(v, ensure_ascii=False))
        return rc
    print(M("raccolta.interrupted") if rc else M("raccolta.header", done=v["done"], total=v["total"]))
    for x in v["sections"]:
        print(f"  {x['key']:<12} " + (x["text"] or f"{x['why']} {x['when']}"))
    if rc == 0:
        print(M("raccolta.footer"))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
