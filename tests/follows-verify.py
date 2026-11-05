#!/usr/bin/env python3
"""Il seguito di un compito eredita l'ok della persona (05/11/2026, supervisor-ok-seguito-05-11). Registro temporaneo.

OS1 task add --follows ID di un compito approvato: l'ok nasce subito, a nome della stessa persona, col testo «approvato
    dal seguito di ID», l'ora e il testo dell'ok d'origine; il contratto ha task.follows; impegni lo dà approvato
OS2 l'origine approvata dopo: l'ok arriva al wait-ok del seguito; anche task.follows scritto nel contratto
OS3 un'origine chiusa verde passa l'ok; una senza ok della persona, cancellata o ferma su una carta nuova no
OS4 una carta con opzioni (una scelta) non si eredita mai; un seguito che ha gia' un ok non ne prende un altro
OS5 --follows di un compito che non c'e': rifiutato, niente registrato
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
home.mkdir()
proj = tmp / "proj"
proj.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"personal": {"config_dir": str(home / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"}, "relay": {"dir": str(tmp / "relay")}}))
fake = tmp / "fake-cm"
fake.write_text("#!/bin/sh\nexit 0\n")
fake.chmod(0o755)
DB = tmp / "tasks.db"
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA", "CM_RELAY_CM")}
ENV.update({"CM_TASKS_DB": str(DB), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASK_TALK_BIN": str(fake),
            "CM_IMPORTANCE_FILE": str(tmp / "imp.json")})
CLI = str(T.SCRIPTS / "supervisor")


def cm(*args, stdin=None):
    return T.cli_or_device(CLI, args, ENV, stdin)


def add(tid, *extra, **kw):
    t = {"id": tid, "title": f"unit {tid}", "plan": None, "where": {"project": str(proj), "session": "sess", "host": "local", "account": None},
         "check": {"cmd": "true", "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open", "depends_on": [], "route": "session",
         "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}
    t.update(kw)
    return cm("add", "-", *extra, stdin=json.dumps({"schema_version": 1, "task": t}))


def person_ok(tid, text="Ok, procedi con la A", at=None):
    """Lo stato di partenza: l'ok della persona gia' registrato sul compito d'origine (come lo scrive task ok --inbox)."""
    c = sqlite3.connect(DB)
    c.execute("INSERT INTO approvals (task, plan, by_, at, text) VALUES (?,NULL,'maintainer',?,?)", (tid, at or int(time.time()) - 3 * 3600, text))
    c.execute("UPDATE tasks SET state='approved' WHERE id=?", (tid,))
    c.commit()
    c.close()


def oks(tid):
    return [a for a in json.loads(cm("show", tid, "--json")[1])["approvals"] if a["by_"] and a["what"] != "confirmed"]


# OS1
add("orig-a")
person_ok("orig-a", "La persona 11:46: scelta A, procedi")
r1 = add("seg-a", "--follows", "orig-a")
o1 = oks("seg-a")
c1 = json.loads(cm("show", "seg-a", "--json")[1])["contract"]["task"]
imp1 = {x["task"]: x for x in json.loads(cm("impegni", "--json")[1])}
T.check("OS1 --follows of an approved task: approved at once, by the same person, «approvato dal seguito di orig-a», the time and the text of the original ok",
        r1[0] == 0 and "approved as a follow-up of orig-a" in r1[1] and len(o1) == 1 and o1[0]["by_"] == "maintainer"
        and "approvato dal seguito di orig-a" in o1[0]["text"] and "scelta A, procedi" in o1[0]["text"] and time.strftime("%d/%m") in o1[0]["text"], str(r1) + str(o1))
T.check("OS1 the contract has task.follows; impegni says approved, by the person", c1.get("follows") == "orig-a" and imp1["seg-a"]["approved"] and imp1["seg-a"]["approved_by"] == "maintainer", str(c1.get("follows")))

# OS2
add("orig-b")
add("seg-b", follows="orig-b")
T.check("OS2 origin not yet approved: no ok at add", oks("seg-b") == [], "")
person_ok("orig-b")
r2 = cm("wait-ok", "seg-b", "--what", "la parte dell'app", "--where", "app")
T.check("OS2 approved later: the ok arrives at the follow-up's wait-ok (follows written in the contract), state approved",
        "approved as a follow-up of orig-b" in r2[1] and len(oks("seg-b")) == 1 and json.loads(cm("show", "seg-b", "--json")[1])["state"] == "approved", str(r2))

# OS3
add("orig-done")
person_ok("orig-done")
c = sqlite3.connect(DB); c.execute("UPDATE tasks SET state='done' WHERE id='orig-done'"); c.commit(); c.close()
add("seg-done", "--follows", "orig-done")
add("orig-none")
add("seg-none", "--follows", "orig-none")
add("orig-canc")
person_ok("orig-canc")
c = sqlite3.connect(DB); c.execute("UPDATE tasks SET state='cancelled' WHERE id='orig-canc'"); c.commit(); c.close()
add("seg-canc", "--follows", "orig-canc")
add("orig-card")
person_ok("orig-card")
cm("wait-ok", "orig-card", "--what", "un'altra cosa", "--where", "qui")
add("seg-card", "--follows", "orig-card")
T.check("OS3 an origin closed green passes the ok; one with no ok of the person, cancelled, or waiting on a new card does not",
        len(oks("seg-done")) == 1 and oks("seg-none") == [] and oks("seg-canc") == [] and oks("seg-card") == [],
        str([len(oks(t)) for t in ("seg-done", "seg-none", "seg-canc", "seg-card")]))

# OS4
add("orig-c")
person_ok("orig-c")
add("seg-c", follows="orig-c")
c = sqlite3.connect(DB); c.execute("DELETE FROM approvals WHERE task='seg-c'"); c.execute("UPDATE tasks SET state='assigned' WHERE id='seg-c'"); c.commit(); c.close()
r4 = cm("wait-ok", "seg-c", "--what", "quale strada", "--where", "qui", "--options", "A|B")
T.check("OS4 a card with options (a choice) is never inherited: it stays awaiting_ok", "follow-up" not in r4[1] and oks("seg-c") == []
        and json.loads(cm("show", "seg-c", "--json")[1])["state"] == "awaiting_ok", str(r4))
cm("wait-ok", "seg-a", "--what", "di nuovo", "--where", "qui")
T.check("OS4 a follow-up that already has an ok does not get a second one", len(oks("seg-a")) == 1, str(oks("seg-a")))

# OS5
r5 = add("seg-x", "--follows", "nessuno")
T.check("OS5 --follows of a task that is not there: refused, nothing registered", r5[0] != 0 and cm("show", "seg-x")[0] != 0, str(r5))
T.finish()
