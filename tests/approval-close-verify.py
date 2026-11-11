#!/usr/bin/env python3
"""La carta di ok o di scelta si chiude sempre dall'app (06/11/2026, supervisor-approval-close-06-11, contratto 1.129).
Registro temporaneo, talk finto (CM_TASK_TALK_BIN), il relay caricato come modulo.

AC1 op approval_close su un compito che chiede una scelta (wait-ok con --options): ok {task, closed true, state running};
    la carta esce da state.approvals, il compito non e' approvato (nessuna riga di ok), la chiusura e' nel registro come
    «superata» con chi e perche' (tidy e passo); la sessione del compito e la master lo sanno con talk
AC2 lo stesso tocco su una carta gia' chiusa, o su un compito gia' approvato: ok con closed false, niente cambia
AC3 un id sconosciuto o non valido: errore; un compito senza sessione torna ad assigned; `task withdraw` dalla CLI
AC4 passa da execute (l'op e' nell'elenco del relay)
AC5 gli esempi del contratto (cmd-result-sample …1290-1292) sono quello che il codice risponde
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

# AC5: la fixture del contratto (cmd-result-sample, …1290-1292) e' quello che il codice risponde, in inglese
FX = json.loads((T.ROOT / "tests" / "fixtures" / "relay" / "cmd-result-sample.json").read_text())
fcmd = [c for c in FX["cmd"] if c.get("op") == "approval_close"]
fres = {r["id"]: r for r in FX["result"]}
cfg_en = json.loads(cfg.read_text()); cfg_en["language"] = "en"
(tmp / "config-en.json").write_text(json.dumps(cfg_en))
ENV5 = dict(ENV, CC_SUPERVISOR_CONFIG=str(tmp / "config-en.json"), CM_TASKS_DB=str(tmp / "t5.db"))
os.environ.clear()
os.environ.update(ENV5)
ENV.update(ENV5)
add("atlas-pages-layout", "atlas-shop")
add("atlas-release-2-4", "atlas-shop")
cm("wait-ok", "atlas-pages-layout", "--what", "layout", "--where", "web", "--options", "A|B")
cm("wait-ok", "atlas-release-2-4", "--what", "release", "--where", "web")
sp5 = importlib.util.spec_from_file_location("cm_relay_ac5", T.SCRIPTS / "cm-relay.py")
RL5 = importlib.util.module_from_spec(sp5)
sp5.loader.exec_module(RL5)
con = RL5._load("cm-tasks").connect()
RL5._load("cm-tasks").approve(con, "atlas-release-2-4", "maintainer", "ok (test)")
con.close()
got, want = [], []
for c in fcmd:
    ok, text = RL5.execute(dict(c))
    got.append((ok, json.loads(text) if ok else text))
    w = fres[c["id"]]
    want.append((w["ok"], json.loads(w["text"]) if w["ok"] else w["text"]))
T.check("AC5 the contract's samples (cmd-result-sample …1290-1292) are what the code answers: closed and back to running, already approved, unknown",
        len(fcmd) == 3 and got == want, json.dumps(got, ensure_ascii=False) + " vs " + json.dumps(want, ensure_ascii=False))

T.rm(tmp)
T.finish()
