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
T.finish()
