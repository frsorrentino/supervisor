#!/usr/bin/env python3
"""I link alle carte non si rompono quando la carta cambia giro (20/11/2026, contratto per app-bozze-link-19-11; il maintainer il
19/11 alle 18:19: i link alle carte riaperte erano rotti). Una carta (contratto 1.137) raccoglie i giri di un compito fino alla
risposta: un wait-ok nuovo senza risposta, o una chiusura senza ok seguita da un wait-ok, restano nella stessa carta.

BL1 op attesa con l'id di un giro vecchio della stessa carta (sostituito da un wait-ok nuovo): la carta attuale, con
    "replaced_from": l'id chiesto; anche con archive true (l'app lo manda sempre)
BL2 chiusa senza ok e riaperta con un wait-ok: l'id vecchio porta alla carta aperta, con replaced_from
BL3 una carta con risposta resta d'archivio (archive true), anche se il compito ha intanto una carta nuova aperta
BL4 un compito senza carte aperte: il rifiuto di oggi, e il risultato porta "task" accanto a ok e text (text resta la frase)
BL5 i link #/attesa/ delle voci (op attese, recap) usano card_id: lo stesso link fra i giri della carta
BL6 l'id resta «w» + 10 cifre esadecimali
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
(home / ".claude").mkdir(parents=True)
proj = tmp / "ws" / "alfa"
proj.mkdir(parents=True)
state = tmp / "state"
state.mkdir()
APP = "https://app.example"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "default_account": "personale",
                           "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                           "workspace": {"root": str(tmp / "ws"), "root_session_name": "master"},
                           "relay": {"enabled": True, "dir": str(tmp / "relay"), "web_app_url": APP},
                           "registry": {"file": str(state / "sessions.json")}, "tasks": {"commitments_prompt": False}}))
fake = tmp / "fake-cm"
fake.write_text("#!/bin/sh\necho '[]'\n")
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_HOOK_TMUX_NAME")}
ENV.update({"CM_TASKS_DB": str(tmp / "tasks.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home),
            "CM_TASK_TALK_BIN": str(fake), "CM_PROC_SCAN_PIDS": "", "CM_RELAY_NO_PUSH": "1"})
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")


def task(*a, stdin=None):
    return subprocess.run([CLI, "task", *a], input=stdin, capture_output=True, text=True, env=ENV, timeout=60)


def add(tid):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"compito {tid}", "plan": None,
                                       "where": {"project": str(proj), "session": "alfa", "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": None, "timeout_s": 30}, "perimeter": ["x"], "lane": "open", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}}
    assert task("add", "-", stdin=json.dumps(c)).returncode == 0
    task("start", tid)


def wait_ok(tid, what):
    r = task("wait-ok", tid, "--consiglio", "va bene", "--what", what, "--where", "repo")
    assert r.returncode == 0, r.stderr


spec = importlib.util.spec_from_file_location("cm_relay_bl", T.SCRIPTS / "cm-relay.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
spec = importlib.util.spec_from_file_location("cm_attese_bl", T.SCRIPTS / "cm-attese.py")
AT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AT)


def items(tid):
    return [a for a in AT.collect() if a.get("task") == tid]


def attesa(i, archive=True):
    res = R.execute({"op": "attesa", "arg": i, "archive": archive, "by": "test"})
    ok, text = res[0], res[1]
    extra = res[2] if len(res) > 2 and isinstance(res[2], dict) else {}
    try:
        body = json.loads(text) if ok else None
    except ValueError:
        body = None
    return ok, text, body, extra


import time  # noqa: E402

# ------------------------------------------------------------------ BL1
add("t1")
wait_ok("t1", "push del sito, prima versione")
old = items("t1")[0]["id"]
time.sleep(1.1)
wait_ok("t1", "push del sito, seconda versione")
cur = items("t1")[0]
r1 = [attesa(old, True), attesa(old, False)]
T.check("BL1 op attesa with an older round's id of the same card: the current card with replaced_from (archive true and false)",
        cur["id"] != old and all(ok and b and b.get("id") == cur["id"] and b.get("replaced_from") == old for ok, _, b, _ in r1),
        json.dumps([(ok, (b or {}).get("id"), (b or {}).get("replaced_from"), t[:80]) for ok, t, b, _ in r1]))

# ------------------------------------------------------------------ BL2
add("t2")
wait_ok("t2", "cambia il colore")
old2 = items("t2")[0]["id"]
task("withdraw", "t2", "--by", "test", "--reason", "non ora")
time.sleep(1.1)
wait_ok("t2", "cambia il colore, di nuovo")
cur2 = items("t2")[0]
ok, t, b, _ = attesa(old2)
T.check("BL2 closed without ok and reopened with a wait-ok: the old id leads to the open card, with replaced_from",
        ok and b and b.get("id") == cur2["id"] and b.get("replaced_from") == old2 and b.get("card_id") == cur2.get("card_id"), t[:300])

# ------------------------------------------------------------------ BL3
add("t3")
wait_ok("t3", "pubblica la pagina")
old3 = items("t3")[0]["id"]
R._MEMO.clear()   # come run_cmd prima di un comando attivo
ap = R.execute({"op": "approve", "arg": "t3", "by": "test", "text": "ok"})   # come il tasto «Approva» dell app
assert ap[0], ap[1]
time.sleep(1.1)
task("start", "t3")
wait_ok("t3", "pubblica la pagina nuova")
ok, t, b, _ = attesa(old3, True)
T.check("BL3 a card with an answer stays from the archive (archive true), even with a new open card on the task",
        ok and b and b.get("id") == old3 and b.get("answered") and not b.get("replaced_from"), t[:300])

# ------------------------------------------------------------------ BL4
add("t4")
wait_ok("t4", "manda la mail")
old4 = items("t4")[0]["id"]
task("withdraw", "t4", "--by", "test", "--reason", "non serve")
ok_n, t_n, _, ex_n = attesa(old4, False)
T.check("BL4 a task with no open card: today's refusal, with \"task\" beside ok and text (text stays a sentence)",
        not ok_n and ex_n.get("task") == "t4" and not t_n.strip().startswith("{"), f"{ok_n} {t_n!r} {ex_n}")

# ------------------------------------------------------------------ BL5, BL6
a1 = items("t1")[0]
T.check("BL5 the #/attesa/ links of the items use card_id: the same link across the card's rounds",
        a1.get("card_id") == old and a1.get("link") == f"{APP}/#/attesa/{old}", json.dumps({k: a1.get(k) for k in ("id", "card_id", "link")}))
T.check("BL6 the id stays «w» + 10 hex digits", all(re.fullmatch(r"w[0-9a-f]{10}", x) for x in (old, old2, old3, old4, cur["id"], cur2["id"])),
        [old, old2, old3, old4])
T.finish()
