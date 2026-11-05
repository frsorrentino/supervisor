#!/usr/bin/env python3
"""Chiudi dall'app su una sessione di win (05/11/2026, supervisor-chiudi-win-05-11).

CW1 slash exit su una riga di un altro host (tmux vuoto): va a close_sessions, come `supervisor close`, invece di «no session»
CW2 un altro comando slash su una sessione di win resta rifiutato; /exit su una sessione di qui senza finestra va a close come dalla 1.120
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"personal": {"config_dir": str(tmp / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"},
                           "relay": {"dir": str(tmp / "relay"), "slash_commands": ["exit", "clear", "compact"]}}))
(tmp / "relay").mkdir()
os.environ.update({"HOME": str(tmp), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db")})
sp = importlib.util.spec_from_file_location("cm_relay_cw", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
ROWS = [{"name": "win:app", "tmux": "", "host": "win", "status": "idle", "attached": None},
        {"name": "qui", "tmux": "qui", "status": "idle", "attached": False}]
RL._json_cmd = lambda *a, **k: ROWS
closed, typed = [], []
RL.close_sessions = lambda cmd, arg, session="": (closed.append(arg), (True, f"chiusa: {arg}"))[1]
RL.relay_talk = lambda *a: (typed.append(a), (0, ""))[1]
RL.close_check = lambda n, rows, **k: ({"closable": True}, rows[1])
RL.save_closed = lambda chk, row, by: None
ok, t = RL.slash_send({"device": "phone"}, "win:app", "win:app", "exit")
T.check("CW1 /exit on a session of win (no tmux here) goes to close_sessions like supervisor close, not «no session»",
        ok and closed == ["win:app"] and not typed, str((ok, t, closed, typed)))
ok2, t2 = RL.slash_send({"device": "phone"}, "win:app", "win:app", "compact")
ok3, t3 = RL.slash_send({"device": "phone"}, "qui", "qui", "exit")
T.check("CW2 another slash on a win session still refused as before; /exit on a session of here without a window goes to close as since 1.120",
        not ok2 and closed == ["win:app", "qui"], str((ok2, t2, ok3, t3, closed)))
T.finish()
