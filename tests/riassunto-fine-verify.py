#!/usr/bin/env python3
"""Il riassunto a fine sessione (19/11/2026, supervisor-riassunto-fine-19-11; idea 1 da claude-mem). Una sessione con lavoro
vero (almeno riassunto.min_prompts prompt) che finisce senza passaggio di consegne, anche chiusa di colpo, lascia nella sua
cartella docs/riassunto-<data>-<sid8>.md: fatti, decisioni, comandi riusciti, cose aperte, scritti da `claude -p` (modello
medio, account della cartella) da un digest di prompt, esiti del registro e commit. Il SessionStart dopo lo annuncia.
Parte dal SessionEnd e da un giro periodico (`supervisor riassunto scan`, lanciato dal relay); un tetto al giorno, costo nel
registro, mai il testo <private>.

RF1 scan: riassunto per la sessione finita con lavoro vero; nessuno per quella corta, per quella con un passaggio di
    consegne scritto dopo il suo inizio, per quella ancora aperta
RF2 claude -p con il CLAUDE_CONFIG_DIR dell'account della cartella (folder_map), il modello di riassunto.model e le
    opzioni leggere; gira fuori dalla cartella del progetto
RF3 il digest porta i prompt, l'esito del registro e il commit della sessione; mai il testo <private>, neanche nel file
    se il modello lo ripete
RF4 il SessionStart dopo, nella stessa cartella, annuncia il riassunto
RF5 il registro ha la riga «riassunto» con il percorso e il costo
RF6 un secondo giro non rifa' lo stesso riassunto
RF7 una sessione chiusa di colpo (nessuna riga «end», ferma da piu' di riassunto.idle_min, nessun processo) ha il suo
RF8 hook SessionEnd: la sessione appena finita ha il suo riassunto senza aspettare il giro
RF9 tetto al giorno: oltre riassunto.max_per_day nessun claude -p in piu', e il giro lo dice
"""
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

SECRET = "codice-riservato-77341"
tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
F = {n: ws / "personali" / n for n in ("alfa", "beta", "gamma", "delta", "eps", "zeta", "eta")}
for d in F.values():
    d.mkdir(parents=True)
work_cfg = home / ".claude-lavoro"
for d in (home / ".claude", work_cfg):
    d.mkdir(parents=True)
state = tmp / "state"
state.mkdir()
calls = tmp / "calls.jsonl"
fake = tmp / "fake-claude.py"
fake.write_text(f"""#!/usr/bin/env python3
import json, os, sys
with open({str(calls)!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "cfg": os.environ.get("CLAUDE_CONFIG_DIR"), "cwd": os.getcwd()}}) + "\\n")
print(json.dumps({{"type": "result", "result": "## Fatti\\n- il listino e' pronto\\n- ripetuto: <private>{SECRET}</private>\\n## Aperto\\n- la prova sul telefono",
                  "total_cost_usd": 0.0123}}))
""")
fake.chmod(0o755)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}, "lavoro": {"config_dir": str(work_cfg)}},
    "folder_map": [{"path": str(F["alfa"]), "account": "lavoro"}],
    "relay": {"enabled": False, "dir": str(tmp / "relay")},
    "registry": {"file": str(tmp / "registry.json"), "good_file": str(tmp / "good.json")},
    "terminal": {"backend": "none"},
    "hooks": {"local_time": {"enabled": False}, "session_kernel": {"enabled": False}},
    "riassunto": {"min_prompts": 3, "max_per_day": 3, "model": "sonnet", "idle_min": 60},
}))
BASE = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_HOOK_TMUX_NAME", "CLAUDE_CONFIG_DIR")}
BASE.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_PROC_SCAN_PIDS": "",
             "CM_CLAUDE_BIN": str(fake), "TMPDIR": str(tmp)})
NOW = datetime.datetime.now()


def iso(min_ago):
    return (NOW - datetime.timedelta(minutes=min_ago)).isoformat(timespec="seconds")


def transcript(sid, cwd, prompts):
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))   # come Claude Code
    p = home / ".claude" / "projects" / slug / f"{sid}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, t in enumerate(prompts):
        ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=40 - i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        rows.append({"type": "user", "uuid": f"u{i}", "timestamp": ts, "sessionId": sid, "cwd": str(cwd),
                     "message": {"role": "user", "content": t}})
        rows.append({"type": "assistant", "uuid": f"a{i}", "timestamp": ts, "sessionId": sid,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": f"risposta {i}"}]}})
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


ROWS = []


def session(sid, cwd, prompts, start_ago=45, end_ago=5, ended=True, esito=""):
    tr = transcript(sid, cwd, prompts)
    acc = "lavoro" if cwd == F["alfa"] else "personale"
    base = {"session_id": sid, "cwd": str(cwd), "account": acc, "pid": 1}
    ROWS.append(dict(base, ts=iso(start_ago), event="start", source="startup"))
    for i, _ in enumerate(prompts):
        ROWS.append(dict(base, ts=iso(start_ago - 1 - i), event="prompt"))
    ROWS.append(dict(base, ts=iso(end_ago + 1), event="stop", last="fatto", tail="fatto", esito=esito, watch=""))
    if ended:
        ROWS.append(dict(base, ts=iso(end_ago), event="end", reason="prompt_input_exit", transcript=str(tr)))
    return tr


WRITTEN = [0]


def write_ledger():
    """Aggiunge solo le righe nuove: quelle che supervisor scrive (riassunto) restano."""
    with open(state / "ledger.jsonl", "a") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ROWS[WRITTEN[0]:]))
    WRITTEN[0] = len(ROWS)


def ncalls():
    return len(calls.read_text().splitlines()) if calls.is_file() else 0


def summaries(d):
    return sorted((d / "docs").glob("riassunto-*.md")) if (d / "docs").is_dir() else []


def run(*args, timeout=120):
    return subprocess.run([str(T.SCRIPTS / "supervisor"), "riassunto", *args], capture_output=True, text=True, env=BASE,
                          timeout=timeout, cwd=str(tmp))


# alfa: git con un commit dentro la sessione
g = dict(BASE, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
         GIT_AUTHOR_DATE=iso(20), GIT_COMMITTER_DATE=iso(20))
subprocess.run(["git", "init", "-q", str(F["alfa"])], check=True, env=g)
(F["alfa"] / "listino.md").write_text("listino\n")
subprocess.run(["git", "-C", str(F["alfa"]), "add", "listino.md"], check=True, env=g)
subprocess.run(["git", "-C", str(F["alfa"]), "commit", "-q", "-m", "listino: prezzi di dicembre"], check=True, env=g)

session("S-AAAAAAAA-1", F["alfa"], ["prepara il listino di dicembre", f"il codice e' <private>{SECRET}</private>, usalo",
                                    "aggiungi le note", "controlla i totali"], esito="Esito: listino pronto con le note")
session("S-BBBBBBBB-2", F["beta"], ["solo una domanda"])                                  # corta
session("S-CCCCCCCC-3", F["gamma"], ["uno", "due", "tre", "quattro"])                     # passaggio di consegne dopo
(F["gamma"] / "docs").mkdir()
(F["gamma"] / "docs" / "handoff-2026-11-19.md").write_text("# Passaggio di consegne\n")
session("S-DDDDDDDD-4", F["delta"], ["uno", "due", "tre", "quattro"], ended=False, end_ago=2)   # ancora aperta (attiva ora)
session("S-EEEEEEEE-5", F["eps"], ["uno", "due", "tre"], start_ago=150, end_ago=90, ended=False)   # chiusa di colpo
write_ledger()

# ------------------------------------------------------------------ RF1..RF3, RF5, RF7
r = run("scan")
got = {n: [p.name for p in summaries(d)] for n, d in F.items()}
T.check("RF1 scan: a summary for the finished session with real work; none for the short one, the one with a handoff after its "
        "start, the one still open",
        len(got["alfa"]) == 1 and got["alfa"][0].endswith("-S-AAAAAA.md") and not got["beta"] and not got["delta"]
        and got["gamma"] == [], json.dumps(got) + r.stdout[-300:] + r.stderr[-600:])
C = [json.loads(l) for l in calls.read_text().splitlines()] if calls.is_file() else []
ca = next((c for c in C if any("listino di dicembre" in a for a in c["argv"])), {})
argv = ca.get("argv") or []
T.check("RF2 claude -p with the folder's account (folder_map), riassunto.model, the lean options, outside the project folder",
        bool(ca) and ca["cfg"] == str(work_cfg) and "-p" in argv and argv[argv.index("--model") + 1] == "sonnet"
        and "--no-session-persistence" in argv and Path(ca["cwd"]).resolve() != F["alfa"].resolve(), json.dumps(ca)[:600])
digest = " ".join(argv)
body = summaries(F["alfa"])[0].read_text() if got["alfa"] else ""
T.check("RF3 the digest carries the prompts, the registry's outcome and the session's commit; never the <private> text, not "
        "even in the file when the model repeats it",
        all(x in digest for x in ("controlla i totali", "listino pronto con le note", "listino: prezzi di dicembre", "[privato]"))
        and SECRET not in digest and SECRET not in body and "il listino e' pronto" in body, digest[-700:] + "\n---\n" + body[-300:])
led = [json.loads(l) for l in (state / "ledger.jsonl").read_text().splitlines()]
rr = [x for x in led if x.get("event") == "riassunto" and x.get("session_id") == "S-AAAAAAAA-1"]
T.check("RF5 the registry has a «riassunto» row with the path and the cost",
        len(rr) == 1 and rr[0].get("path") == str(summaries(F["alfa"])[0]) and abs(float(rr[0].get("cost_usd") or 0) - 0.0123) < 1e-9,
        json.dumps(rr)[:400])
T.check("RF7 a session closed abruptly (no «end» row, idle past riassunto.idle_min, no process) gets its summary",
        len(got["eps"]) == 1, json.dumps(got["eps"]))

# ------------------------------------------------------------------ RF4
spec = importlib.util.spec_from_file_location("cm_handoff_rf", T.SCRIPTS / "cm-handoff.py")
os.environ.update(BASE)
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
line = H.announce(str(F["alfa"]), "S-NEXT")
T.check("RF4 the next SessionStart in the same folder announces the summary",
        bool(got["alfa"]) and str(summaries(F["alfa"])[0]) in line and "riassunto" in line.lower(), line)

# ------------------------------------------------------------------ RF6
n0 = ncalls()
r = run("scan")
T.check("RF6 a second round does not redo the same summary", ncalls() == n0 and len(summaries(F["alfa"])) == 1,
        f"calls {n0} -> {ncalls()}; {r.stdout[-200:]}")

# ------------------------------------------------------------------ RF8
tr = session("S-ZZZZZZZZ-6", F["zeta"], ["uno", "due", "tre", "quattro"], ended=False, end_ago=1)
write_ledger()
h = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "SessionEnd"], capture_output=True, text=True, env=BASE, timeout=60,
                   input=json.dumps({"session_id": "S-ZZZZZZZZ-6", "cwd": str(F["zeta"]), "transcript_path": str(tr),
                                     "reason": "prompt_input_exit", "hook_event_name": "SessionEnd"}))
t0 = time.time()
while time.time() - t0 < 30 and not summaries(F["zeta"]):
    time.sleep(0.5)
T.check("RF8 SessionEnd hook: the session just ended gets its summary without waiting for the round",
        len(summaries(F["zeta"])) == 1, f"rc {h.returncode} {h.stderr[-300:]}")

# ------------------------------------------------------------------ RF9
session("S-HHHHHHHH-7", F["eta"], ["uno", "due", "tre", "quattro"])
write_ledger()
n0 = ncalls()
r = run("scan")
T.check("RF9 daily cap: past riassunto.max_per_day no further claude -p, and the round says so",
        ncalls() == n0 and not summaries(F["eta"]) and ("tetto" in r.stdout.lower() or "cap" in r.stdout.lower()),
        f"calls {n0} -> {ncalls()}; out {r.stdout[-300:]} {r.stderr[-300:]}")

T.finish()
