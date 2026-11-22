#!/usr/bin/env python3
"""Gli stati normali del relay non finiscono fra le note di errore, e quelle gia' raccolte si chiudono (22/11/2026,
supervisor-note-rumore-22-11).

Il 22/11 observe list sul Chromebook aveva 656 note in 220 gruppi: quasi tutte stati normali di un'op dal telefono
(«no transcript yet for X» mentre la sessione parte, «X is not running», una domanda gia' risposta, «master is busy»),
e i difetti veri (consegna unreachable, un modello che non c'e' nel selettore, un'op non permessa) ci sparivano in mezzo.
Le forme qui sotto sono quelle del 22/11 con nomi finti.

NR1 normal_state: rumore esattamente le forme degli stati normali, in inglese e in italiano e col prefisso «relay <op> »;
    i difetti veri no
NR2 un'op che fallisce per uno stato normale (prompt a una sessione che non gira, interrupt senza niente da fermare):
    la risposta all'app resta quella, nessuna nota nella casella
NR3 un'op che fallisce davvero (un'op sconosciuta): la nota c'e' come prima
NR4 al primo avvio del demone (tidy_noise_notes) le note gia' raccolte di stati normali diventano done (noise), i difetti
    veri restano new; la seconda volta non rifa' niente; observe list non mostra piu' il rumore
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
(ws / "personali" / "alfa").mkdir(parents=True)
(home / ".claude" / "sessions").mkdir(parents=True)
state, rdir, store = tmp / "state", tmp / "relay", tmp / "observe"
for d in (state, rdir, store):
    d.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master", "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(rdir), "duplicate_prompt_s": 0},
    "registry": {"file": str(tmp / "registry.json")}, "tabs": {"color_registry": str(tmp / "colors")},
}))
ocfg = tmp / "observe.json"
ocfg.write_text(json.dumps({"dir": str(store), "anonymizer": str(tmp / "none.py")}))
fake_cm = tmp / "supervisor"
fake_cm.write_text("""#!/bin/sh
case "$1" in
  sessions) echo '[]' ;;
  registry) echo '{"sessioni": []}' ;;
  *) echo "supervisor $1: not here" >&2; exit 1 ;;
esac
""")
fake_cm.chmod(0o755)
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE", "CLAUDE_OBSERVE_TOOL", "XDG_STATE_HOME"):
    os.environ.pop(k, None)
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_RELAY_CM": str(fake_cm),
                   "CM_PROC_SCAN_PIDS": "", "CLAUDE_OBSERVE_CONFIG": str(ocfg), "CM_RELAY_NO_PUSH": "1"})


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_nr", T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RL = load("cm-relay")
RL.push_async = lambda *a, **k: None
OBS = RL._observe() if hasattr(RL, "_observe") else load("../observe/observe")


def notes():
    return [r for r in OBS.all_records() if r.get("source") == "relay"]


def obs_list():
    return subprocess.run([sys.executable, str(T.ROOT / "supervisor" / "observe" / "observe.py"), "list"], capture_output=True, text=True,
                          env=os.environ.copy(), timeout=60).stdout


NOISE = [
    "alfa: questa domanda non aspetta più (chiusa o già risposta)",
    "a-1b2c: this question is no longer waiting (closed or already answered)",
    "beta is not running", "relay transcript beta is not running", "no transcript yet for beta", "no transcript yet for win:beta",
    "no entry abc123.4 in the transcript", "relay transcript no entry abc123.4 in the transcript", "beta: no transcript",
    "beta: nothing to stop", "beta: the same message was sent 3 s ago, not sent again", "prova 4: non c'è più o è già confermata",
    "prova 9: non esiste", "beta is not running: nothing sent", "master is busy", "relay prompt empty prompt", "master is already running",
    "needs_you n-12: nothing to close",
]
REAL = [
    "relay screen beta screen: nessuna sessione tmux trovata", "no session ?", "beta: conferma non arrivata", "no session win:beta",
    "too large: 12", "no «Sì» option on this question", "/home/x/y non si è aperto in beta",
    "beta: consegna a win fallita (unreachable: Connection timed out during banner exchange)", "text too long: at most 4000 characters",
    "beta ha del testo scritto nel prompt", "beta: remote: not running", "Fable 5.2 non c'è nel selettore di win:beta",
    "attachment not saved: unreachable: Connection timed out during banner exchange",
    "beta not closed: a job is running in the background", "timeout", "bad name", "not in the transcript", "op projects not allowed",
    "image missing or unreadable", "reopen failed: beta launch: 9 sessioni già al lavoro (tetto sessions.max_sessions 8)",
    "relay report no session beta", "relay transcript op transcript not allowed",
]

if not hasattr(RL, "normal_state"):
    T.check("NR1 the relay knows the normal states (normal_state)", False, "no normal_state in cm-relay.py")
else:
    wrong = [t for t in NOISE if not RL.normal_state(t)] + [f"REAL:{t}" for t in REAL if RL.normal_state(t)]
    T.check(f"NR1 normal_state: the {len(NOISE)} forms of normal states are noise, the {len(REAL)} real defects are not", not wrong, " | ".join(wrong))

# NR2
for f in store.glob("*"):
    f.unlink()
r_p = RL.run_cmd({"id": "c-nr2a", "op": "prompt", "session": "gamma", "arg": "ciao", "by": "phone"})
r_i = RL.run_cmd({"id": "c-nr2b", "op": "interrupt", "session": "gamma", "by": "phone"})
T.check("NR2 prompt and interrupt to a session that is not running: the same answer to the app, no error note",
        r_p and r_p["ok"] is False and "gamma" in r_p["text"] and r_i and r_i["ok"] is False and "gamma" in r_i["text"] and notes() == [],
        f"{r_p and r_p['text']!r} | {r_i and r_i['text']!r} | {[(n.get('call'), n.get('error')) for n in notes()]}")

# NR3
r_u = RL.run_cmd({"id": "c-nr3", "op": "boh-op", "session": "gamma", "by": "phone"})
T.check("NR3 an op that really fails (unknown op): its note is there as before",
        r_u and r_u["ok"] is False and any(n.get("call") == "relay boh-op" for n in notes()), f"{r_u and r_u['text']!r} | {[(n.get('call'), n.get('error')) for n in notes()]}")

# NR4
for f in store.glob("*"):
    f.unlink()
for t in NOISE + REAL:
    OBS.record_external("relay", "relay transcript", t, "beta")
n_before = len([n for n in notes() if n.get("status") == "new"])
got = RL.tidy_noise_notes() if hasattr(RL, "tidy_noise_notes") else None
again = RL.tidy_noise_notes() if hasattr(RL, "tidy_noise_notes") else "missing"
by = {n.get("error"): n for n in notes()}
get = lambda t: by.get(OBS.scrub(t)) or {}   # noqa: E731 — observe pulisce i percorsi nel testo della nota
bad = [t for t in NOISE if get(t).get("status") != "done" or get(t).get("noise") != "relay-normal-state"] + \
      [f"REAL:{t}" for t in REAL if get(t).get("status") != "new"]
T.check(f"NR4 first start of the daemon: the {len(NOISE)} noise notes done (noise), the {len(REAL)} real ones still new; the second time nothing",
        n_before == len(NOISE) + len(REAL) and got == len(NOISE) and again is None and not bad,
        f"before {n_before} got {got} again {again} | {bad[:6]}")
out = obs_list()
T.check("NR4 observe list no longer shows the noise (no transcript yet, non aspetta più, is not running, is busy) and shows the real ones",
        not re.search(r" new .*(no transcript yet|non aspetta più|is not running|is busy)", out) and "unreachable" in out, out[:1200])

T.rm(str(tmp))
T.finish()
