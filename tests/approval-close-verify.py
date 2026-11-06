#!/usr/bin/env python3
"""La carta di ok o di scelta si chiude sempre dall'app (06/11/2026, supervisor-approval-close-06-11, contratto 1.129).
Registro temporaneo, talk finto (CM_TASK_TALK_BIN), il relay caricato come modulo.

AC1 op approval_close su un compito che chiede una scelta (wait-ok con --options): ok {task, closed true, state running};
    la carta esce da state.approvals, il compito non e' approvato (nessuna riga di ok), la chiusura e' nel registro come
    «superata» con chi e perche' (tidy e passo); la sessione del compito e la master lo sanno con talk
AC2 lo stesso tocco su una carta gia' chiusa, o su un compito gia' approvato: ok con closed false, niente cambia
AC3 un id sconosciuto o non valido: errore; un compito senza sessione torna ad assigned; `task withdraw` dalla CLI
AC4 passa da execute (l'op e' nell'elenco del relay)
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
(home / ".claude").mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"p": {"config_dir": str(home / ".claude")}},
                           "default_account": "p", "workspace": {"root": str(tmp), "root_session_name": "master"}, "relay": {"dir": str(tmp / "relay")}}))
(tmp / "relay").mkdir()
talklog = tmp / "talk.log"
fake = tmp / "fake-cm"
fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {talklog}\nexit 0\n")
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update({"CM_TASKS_DB": str(tmp / "t.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASK_TALK_BIN": str(fake),
            "CM_RELAY_NO_PUSH": "1"})
os.environ.clear()
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")


def cm(*a, stdin=None):
    return subprocess.run([CLI, "task", *a], input=stdin, capture_output=True, text=True, env=ENV, timeout=120)


def add(tid, session):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"t {tid}", "plan": "p", "where": {"project": str(tmp), "session": session, "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": str(tmp), "timeout_s": 30}, "perimeter": ["x"], "lane": "open", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
                                       "outcome": {"expected": "x", "kind": "custom", "proof": "x"}}}
    assert cm("add", "-", stdin=json.dumps(c)).returncode == 0
    if session:
        cm("start", tid)


add("ac-scelta", "alfa")
add("ac-solo", None)
add("ac-ok", "alfa")
cm("wait-ok", "ac-scelta", "--what", "come si apre la pagina", "--where", "app telefono", "--options", "A|B|C")
cm("wait-ok", "ac-solo", "--what", "pubblico?", "--where", "web")
cm("wait-ok", "ac-ok", "--what", "pubblico?", "--where", "web")
cm("approve", "ac-ok", "--by", "maintainer", "--pin", "x")   # puo' fallire senza PIN: si approva dal registro sotto
sp = importlib.util.spec_from_file_location("cm_relay_ac", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
TK = RL._load("cm-tasks")
con = TK.connect()
if con.execute("SELECT state FROM tasks WHERE id='ac-ok'").fetchone()["state"] == "awaiting_ok":
    TK.approve(con, "ac-ok", "maintainer", "ok (test)")
con.close()

before = [a["task"] for a in RL.approvals_list()]
talklog.write_text("")
ok1, t1 = RL.approval_close({"op": "approval_close", "device": "phone", "by": "maintainer", "text": "non serve più"}, "ac-scelta")
time.sleep(0.5)
r1 = json.loads(t1) if ok1 else {}
RL._MEMO.clear() if hasattr(RL, "_MEMO") else None
after = [a["task"] for a in RL.approvals_list()]
show = json.loads(cm("show", "ac-scelta", "--json").stdout)
con = TK.connect()
oks = con.execute("SELECT COUNT(*) FROM approvals WHERE task='ac-scelta' AND by_ IS NOT NULL").fetchone()[0]
tidy = con.execute("SELECT kind, text FROM tidy WHERE task='ac-scelta'").fetchall()
step = con.execute("SELECT by_, text FROM steps WHERE task='ac-scelta' ORDER BY n DESC LIMIT 1").fetchone()
con.close()
talks = talklog.read_text()
T.check("AC1 approval_close on a choice card: ok closed, the task back to running, out of state.approvals, no ok recorded",
        ok1 and r1.get("closed") is True and r1.get("state") == "running" and "ac-scelta" in before and "ac-scelta" not in after
        and show["state"] == "running" and oks == 0, f"{ok1} {t1} before={before} after={after} state={show['state']} oks={oks}")
T.check("AC1 the registry says «superata» with who and why; the task's session and the master are told",
        len(tidy) == 1 and tidy[0]["kind"] == "approval" and "superata" in tidy[0]["text"] and "non serve più" in tidy[0]["text"] and "maintainer" in tidy[0]["text"]
        and step and step["by_"] == "maintainer" and "talk alfa " in talks and "talk master " in talks and "senza dare l'ok" in talks,
        f"{[dict(x) for x in tidy]} {dict(step) if step else None} {talks!r}")

talklog.write_text("")
ok2, t2 = RL.approval_close({"op": "approval_close", "device": "phone", "by": "maintainer"}, "ac-scelta")
ok3, t3 = RL.approval_close({"op": "approval_close", "device": "web", "by": "maintainer"}, "ac-ok")
time.sleep(0.3)
st_ok = json.loads(cm("show", "ac-ok", "--json").stdout)["state"]
T.check("AC2 a card already closed, or a task already approved: ok with closed false, nothing changes, nobody told",
        ok2 and json.loads(t2)["closed"] is False and json.loads(t2)["state"] == "running" and ok3 and json.loads(t3)["closed"] is False
        and st_ok == "approved" and talklog.read_text() == "", f"{t2} {t3} {st_ok} {talklog.read_text()!r}")

ok4, t4 = RL.approval_close({"op": "approval_close", "device": "phone"}, "non-esiste")
ok5, t5 = RL.approval_close({"op": "approval_close", "device": "phone"}, "x; rm -rf /")
r6 = cm("withdraw", "ac-solo", "--by", "maintainer", "--reason", "fatto altrove", "--json")
st_solo = json.loads(cm("show", "ac-solo", "--json").stdout)["state"]
T.check("AC3 unknown or invalid id: an error; a task with no session goes back to assigned; task withdraw from the CLI",
        not ok4 and "sconosciuto" in t4 and not ok5 and r6.returncode == 0 and json.loads(r6.stdout) == {"task": "ac-solo", "closed": True, "state": "assigned"}
        and st_solo == "assigned", f"{t4} {t5} {r6.stdout} {r6.stderr} {st_solo}")

cm("wait-ok", "ac-scelta", "--what", "di nuovo", "--where", "app", "--options", "A|B")
ok7, t7 = RL.execute({"op": "approval_close", "arg": "ac-scelta", "device": "phone", "by": "maintainer"})
T.check("AC4 the op goes through the relay's execute", "approval_close" in RL.OPS and ok7 and json.loads(t7)["closed"] is True, f"{ok7} {t7}")

T.rm(tmp)
T.finish()
