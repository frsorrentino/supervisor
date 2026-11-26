#!/usr/bin/env python3
"""supervisor-ok-sessioni-bg-26-11 (26/11/2026, l'ok del maintainer delle 19:25, inbox eca3b8, arrivato a una sessione
avviata con `launch --bg` e mai registrato): `task ok --inbox` in una sessione --bg. Fuori da tmux TMUX_PANE e' vuoto e
_caller_session() tornava '': «only the session that received an ok records it», sempre. Ora il nome viene dal registro
di Claude Code, dal processo claude con "kind": "bg" di cui chi chiama discende. Tutto su un registro temporaneo.

BG1 una sessione --bg (il processo del registro e' un antenato di chi chiama) registra l'ok arrivato nella sua casella
BG2 una shell estranea resta rifiutata: lo stesso nome --bg nel registro, ma su un processo che non e' suo antenato, e una
    variabile d'ambiente con l'id della sessione non basta
BG3 un processo claude antenato che non e' --bg (una sessione interattiva fuori da tmux) non da' un nome: rifiutata come prima
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
proj = tmp / "proj"
proj.mkdir()
home = tmp / "home"
reg = home / ".claude" / "sessions"
reg.mkdir(parents=True)
state_dir = tmp / "state"
state_dir.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state_dir), "accounts": {"personal": {"config_dir": str(home / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"},
                           "relay": {"dir": str(tmp / "relay")}, "registry": {"file": str(state_dir / "sessions.json")},
                           "hooks": {"local_time": {"enabled": False}, "session_kernel": {"enabled": False}},
                           "tasks": {"commitments_prompt": False}}))
fake_cm = tmp / "fake-cm"
fake_cm.write_text("#!/bin/sh\nexit 0\n")
fake_cm.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA", "CM_RELAY_CM", "CM_HOOK_TMUX_NAME",
                                                         "CLAUDE_CODE_SESSION_ID")}
ENV.update({"CM_TASKS_DB": str(tmp / "tasks.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home),
            "CM_TASK_TALK_BIN": str(fake_cm), "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CM_PROC_SCAN_PIDS": "", "CM_INBOX_WAIT_S": "1"})
CLI = str(T.SCRIPTS / "supervisor")


def cm(*args, stdin=None, env=None):
    p = subprocess.run([CLI, "task", *args], input=stdin, capture_output=True, text=True, env=dict(ENV, **(env or {})), timeout=60)
    return p.returncode, p.stdout, p.stderr


def add(tid):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"unit {tid}", "plan": None,
                                       "where": {"project": str(proj), "session": "fondo", "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open",
                                       "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal",
                                       "requested_by": "test"}}
    return cm("add", "-", stdin=json.dumps(c))


def inbox_put(to, text, origin="relay"):
    code = ("import importlib.util,sys;s=importlib.util.spec_from_file_location('i',sys.argv[1]);I=importlib.util.module_from_spec(s);"
            "s.loader.exec_module(I);r=I.put(sys.argv[2],sys.argv[3],'supervisor',origin=sys.argv[4]);print(r['id'])")
    return subprocess.run([sys.executable, "-c", code, str(T.SCRIPTS / "cm-inbox.py"), to, text, origin],
                          capture_output=True, text=True, env=ENV, timeout=30).stdout.strip()


def entry(pid, **kw):
    for f in reg.glob("*.json"):
        f.unlink()
    d = {"pid": pid, "sessionId": "sid-fondo", "cwd": str(proj), "startedAt": 0, "status": "idle", "name": "fondo"}
    d.update(kw)
    (reg / f"{pid}.json").write_text(json.dumps(d))


def approvals(tid):
    con = sqlite3.connect(str(tmp / "tasks.db"))
    try:
        return con.execute("SELECT COUNT(*) FROM approvals WHERE task = ?", (tid,)).fetchone()[0]
    finally:
        con.close()


for t in ("b1", "b2", "b3"):
    r = add(t)
    assert r[0] == 0, r

# BG1: il processo del registro e' questo test, antenato del comando
entry(os.getpid(), kind="bg")
m1 = inbox_put("fondo", "scegli consigliata e prosegui")
rc, out, err = cm("ok", "b1", "--by", "maintainer", "--inbox", m1)
T.check("BG1 a --bg session (its claude process is an ancestor of the caller) records the ok that reached its inbox",
        rc == 0 and approvals("b1") >= 1, f"rc={rc} {out[-300:]} {err[-300:]}")

# BG2: lo stesso nome --bg, ma su un processo vivo che non e' un antenato
other = subprocess.Popen(["sleep", "60"])
try:
    entry(other.pid, kind="bg")
    m2 = inbox_put("fondo", "ok vai")
    r2 = cm("ok", "b2", "--by", "maintainer", "--inbox", m2, env={"CLAUDE_CODE_SESSION_ID": "sid-fondo"})
finally:
    other.kill()
    other.wait()
T.check("BG2 a foreign shell stays refused: same --bg name on a process that is not its ancestor, the session id in the environment does not help",
        r2[0] != 0 and approvals("b2") == 0 and "only the session that received an ok records it" in r2[2], str(r2))

# BG3: un antenato claude che non e' --bg
entry(os.getpid())
m3 = inbox_put("fondo", "ok anche questo")
r3 = cm("ok", "b3", "--by", "maintainer", "--inbox", m3)
T.check("BG3 an ancestor claude process that is not --bg (interactive, outside tmux) gives no name: refused as before",
        r3[0] != 0 and approvals("b3") == 0 and "this shell" in r3[2], str(r3))
T.rm(str(tmp))
T.finish()
