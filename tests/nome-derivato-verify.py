#!/usr/bin/env python3
"""Il nome che Claude Code da' da se' non e' un nome (19/11/2026, prova vera in sup-prova, supervisor-master-prova-vera-19-11).
Claude Code 2.1.328 scrive nel registro delle sessioni un nome anche a chi non ne ha uno («prova-39», nameSource «derived»):
supervisor lo prendeva per buono, e `tmux new -s lavoro` + claude nella radice compariva nell'app come «prova-39»; la
master fuori da tmux non prendeva il nome «master».

ND1 registry_entries: un nome «derived» diventa vuoto; un nome scelto (-n, /rename: nessun nameSource o un altro) resta
ND2 sessions: la riga di una sessione con nome derivato non ha nome (vale il nome tmux, o quello della master)
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
reg = home / ".claude" / "sessions"
reg.mkdir(parents=True)
ws = home / "ws"
ws.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "en", "state_dir": str(tmp / "state"), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "terminal": {"backend": "none"},
}))
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_PROC_SCAN_PIDS": "",
                   "CM_TMUX_ARGS": f"-L nd-{os.getpid()}"})
for k in ("TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR"):
    os.environ.pop(k, None)

spec = importlib.util.spec_from_file_location("cm_sessions_nd", T.SCRIPTS / "cm-sessions.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

procs = [subprocess.Popen(["sleep", "300"]) for _ in range(2)]
time.sleep(0.3)
derived, chosen = procs
for p, name, src, sid in ((derived, "prova-39", "derived", "S-DER"), (chosen, "mio", None, "S-CHO")):
    d = {"pid": p.pid, "sessionId": sid, "cwd": str(ws), "startedAt": int(time.time() * 1000), "procStart": S.proc_start(p.pid),
         "version": "2.1.328", "kind": "interactive", "name": name, "status": "idle"}
    if src:
        d["nameSource"] = src
    (reg / f"{p.pid}.json").write_text(json.dumps(d))
try:
    ents = {d["sessionId"]: d.get("name") for d in S.registry_entries()}
    T.check("ND1 registry_entries: a «derived» name becomes empty; a chosen name stays", ents == {"S-DER": "", "S-CHO": "mio"}, json.dumps(ents))
    rows = {r.get("session_id"): r.get("name") for r in S.collect(read_screen=False)}
    T.check("ND2 sessions: the row of a session with a derived name has no name", rows.get("S-DER") == "" and rows.get("S-CHO") == "mio",
            json.dumps(rows))
finally:
    for p in procs:
        p.kill()

# ND3 (prova vera del 20/11, 01:30): la master viva senza nome (`claude` fuori da tmux nella radice) e la riga chiusa
# «master» della fotografia (la stessa master, chiusa prima): la viva prende il nome, la chiusa esce. Prima la viva
# restava «?» nell'app
spec = importlib.util.spec_from_file_location("cm_relay_state_nd", T.SCRIPTS / "cm-relay-state.py")
RS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RS)
rows = [{"tmux": "", "name": "", "cwd": str(ws), "session_id": "S-LIVE", "status": "idle"},
        {"tmux": "master", "name": "master", "cwd": str(ws), "session_id": "S-OLD", "status": "dead"},
        {"tmux": "alfa", "name": "alfa", "cwd": str(ws / "alfa"), "session_id": "S-A", "status": "idle"}]
pick = RS.mark_master(rows, str(ws), "master")
names = [(r.get("name"), r.get("status"), bool(r.get("_master"))) for r in rows]
T.check("ND3 the live master without a name takes the master's name; the closed «master» row (the same master) leaves",
        pick is not None and pick.get("session_id") == "S-LIVE" and pick.get("name") == "master"
        and names == [("master", "idle", True), ("alfa", "idle", False)], json.dumps(names))
T.finish()
