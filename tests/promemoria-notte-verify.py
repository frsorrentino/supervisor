#!/usr/bin/env python3
"""I promemoria di «Serve te» rispettano le ore di quiete (24/11/2026, supervisor-promemoria-notte-24-11).

Dalla 0.8.202 una carta suona dopo 1 e dopo 3 ore (attention_reminders); il relay non leggeva tasks.quiet_hours, che cm-guard
rispetta per gli allarmi di fermo: una carta nata alle 22:30 suonava su telefono e orologio alle 23:30 e alle 01:30, e la
soglia passata di notte veniva segnata come fatta.

PN1 carta nata alle 22:30, quiete [23, 7): niente alle 23:30 e alle 01:30, e nessuna soglia segnata
PN2 alle 07:00 un promemoria solo per le due soglie della notte, con l'eta' vera; dopo, niente piu'
PN3 di giorno niente cambia: carta delle 09:00, uno alle 10:00 (60 min) e uno alle 12:00 (180 min); domanda delle 15:00 a 15:10 e 15:30
PN4 una domanda nata alle 06:55: la soglia dei 10 minuti cade alle 07:05, fuori dalla quiete, e suona come di giorno
PN5 la lettura delle ore di quiete e' quella di cm-guard, ora per ora, per [23, 7], [8, 9] e senza quiete
PN6 quiet_hours [] (spente): la carta delle 22:30 suona alle 23:30 come prima
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"personal": {"config_dir": str(tmp / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"},
                           "relay": {"dir": str(tmp / "relay")}, "tasks": {"quiet_hours": [23, 7]}}))
(tmp / "relay").mkdir()
os.environ.update({"HOME": str(tmp), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db")})


def load(name, mod):
    sp = importlib.util.spec_from_file_location(mod, T.SCRIPTS / name)
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


RL = load("cm-relay.py", "cm_relay_pn")
rd = tmp / "relay-pn"
rd.mkdir()
RL.rdir = lambda: rd
events = []
RL.emit = lambda kind, title, body, **k: events.append((kind, title, k.get("ref")))


def at(day_off, hh, mm):
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday + day_off, hh, mm, 0, 0, 0, -1)))


def card(ref, since, kind="card"):
    return {"kind": kind, "ref": ref, "since": since, "title": f"carta {ref}", "session": "s", "wid": ref}


def tick(items, now):
    n = len(events)
    RL.attention_reminders({"attention": items}, now)
    return events[n:]


def marks(ref):
    d = json.loads((rd / "reminders.json").read_text()) if (rd / "reminders.json").is_file() else {}
    return next((v for k, v in d.items() if f"|{ref}|" in k), None)


c1 = card("c1", at(0, 22, 30))
tick([c1], at(0, 22, 31))
e2330 = tick([c1], at(0, 23, 30))
e0130 = tick([c1], at(1, 1, 30))
T.check("PN1 a card born at 22:30, quiet [23, 7): nothing at 23:30 nor at 01:30, and no threshold marked",
        e2330 == [] and e0130 == [] and marks("c1") == [], str((e2330, e0130, marks("c1"))))
e0700 = tick([c1], at(1, 7, 0))
e0701 = tick([c1], at(1, 7, 1))
T.check("PN2 at 07:00 one reminder only for the two thresholds of the night, with the true age (510 min); nothing after",
        len(e0700) == 1 and e0700[0][0] == "attention_reminder" and "(510 min)" in e0700[0][1] and e0700[0][2] == "c1"
        and e0701 == [] and sorted(marks("c1")) == [3600, 10800], str((e0700, e0701, marks("c1"))))

c2, q1 = card("c2", at(0, 9, 0)), card("q1", at(0, 15, 0), kind="question")
tick([c2], at(0, 9, 1))
d1 = tick([c2], at(0, 10, 0))
d2 = tick([c2], at(0, 12, 0))
tick([q1], at(0, 15, 1))
d3 = tick([q1], at(0, 15, 10))
d4 = tick([q1], at(0, 15, 30))
T.check("PN3 by day nothing changes: a card of 09:00 rings at 10:00 (60 min) and at 12:00 (180 min); a question of 15:00 at 15:10 and 15:30",
        [len(d1), len(d2), len(d3), len(d4)] == [1, 1, 1, 1] and "(60 min)" in d1[0][1] and "(180 min)" in d2[0][1]
        and "(10 min)" in d3[0][1] and "(30 min)" in d4[0][1], str((d1, d2, d3, d4)))

q2 = card("q2", at(1, 6, 55), kind="question")
tick([q2], at(1, 6, 56))
d5 = tick([q2], at(1, 7, 5))
T.check("PN4 a question born at 06:55: the 10 minutes fall at 07:05, out of the quiet hours, and ring as by day",
        len(d5) == 1 and "(10 min)" in d5[0][1], str(d5))

GD = load("cm-guard.py", "cm_guard_pn")
same = hasattr(RL, "quiet_now")
for q in ([23, 7], [8, 9], []) if same else ():
    RL.CFG.setdefault("tasks", {})["quiet_hours"] = q
    GD.CFG.setdefault("tasks", {})["quiet_hours"] = q
    for h in range(24):
        if RL.quiet_now(at(0, h, 30)) != GD.quiet_now(at(0, h, 30)):
            same = False
T.check("PN5 the quiet hours are read as cm-guard reads them, hour by hour, for [23, 7], [8, 9] and none", same, "")

RL.CFG["tasks"]["quiet_hours"] = []
c3 = card("c3", at(0, 22, 30))
tick([c3], at(0, 22, 31))
e6 = tick([c3], at(0, 23, 30))
T.check("PN6 quiet_hours [] (off): the card of 22:30 rings at 23:30 as before", len(e6) == 1 and "(60 min)" in e6[0][1], str(e6))
T.finish()
