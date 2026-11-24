#!/usr/bin/env python3
"""Una carta di ok o di scelta senza risposta tiene aperto il compito (24/11/2026, supervisor-autoclose-carta-24-11).

Il 24/11 alle 16:57:07 `task auto-close` ha chiuso app-indicatore-attivita-24-11, in awaiting_ok con la carta di scelta
1899 ancora aperta (quattro opzioni, choice e closed_at nulli): il suo controllo guardava solo i mockup, quindi verde, e la
carta e' sparita da `task decisioni` e dagli impegni. auto_close saltava le prove da fare, le pause e i compiti mai
lavorati, non le carte aperte. Ora una carta aperta tiene aperto il compito come una prova da fare; `task done` a mano su un
compito con la carta aperta la chiude col motivo e lo dice, invece di lasciarla orfana.

AC1 auto-close: un compito lavorato, col controllo verde, aperto da 7 ore, in awaiting_ok con una scelta di quattro
    opzioni senza risposta resta awaiting_ok, il controllo non gira, la carta resta aperta e in `task decisioni`
AC2 lo stesso con una carta di ok (senza opzioni)
AC3 la scelta presa (l'ok con la scelta, dal relay): auto-close lo chiude
AC4 la carta chiusa dalla persona senza ok (withdraw): il compito esce da awaiting_ok e auto-close lo chiude
AC5 `task done` a mano con la scelta aperta e il controllo verde: done, la carta chiusa (closed_at, closed_by «task done»,
    il motivo), un passo nel registro, un avviso su stderr, il giro «closed» col motivo
AC6 `task done` a mano col controllo rosso: il compito resta awaiting_ok e la carta aperta
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
proj = tmp / "proj"
proj.mkdir()
(proj / "ok.txt").write_text("ok")
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "workspace": {"root": str(tmp), "root_session_name": "master"}}))
DB = tmp / "tasks.db"
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update({"CM_TASKS_DB": str(DB), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(tmp), "CM_HOME": str(tmp), "CM_RELAY_CM": "/bin/false"})


def cm(*args):
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), *args], capture_output=True, text=True, env=ENV, timeout=120)
    return r.returncode, r.stdout, r.stderr


def contract(tid, check="test -f ok.txt"):
    return {"schema_version": 1, "task": {
        "id": tid, "title": f"indicatore {tid}", "type": "feature", "plan": None,
        "where": {"project": str(proj), "session": None, "host": "local", "account": None},
        "outcome": {"expected": "i mockup ci sono", "kind": "custom", "proof": "the check"},
        "check": {"cmd": check, "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open",
        "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}}


def add(tid, **kw):
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), "add", "-"], input=json.dumps(contract(tid, **kw)),
                       capture_output=True, text=True, env=ENV, timeout=120)
    assert r.returncode == 0, r.stderr
    cm("start", tid)
    cm("step", tid, "scritti i mockup")


def choice(tid):
    return cm("wait-ok", tid, "--what", "quale indicatore di attivita'", "--where", "i mockup", "--options", "Punti|Barra|Anello|Onda",
              "--consiglio", "Anello: si legge anche piccolo", "--consigliata", "Anello")


def okcard(tid):
    return cm("wait-ok", tid, "--what", "il report", "--where", "la cartella", "--consiglio", "approvalo: e' quello chiesto")


def show(tid):
    return json.loads(cm("show", tid, "--json")[1])


def db(q, *p):
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    try:
        out = [dict(r) for r in con.execute(q, p).fetchall()]
        con.commit()
        return out
    finally:
        con.close()


def req(tid):
    return db("SELECT * FROM approvals WHERE task=? AND by_ IS NULL ORDER BY n DESC LIMIT 1", tid)[0]


def decision_tasks():
    d = json.loads(cm("decisioni", "--json")[1] or "{}")
    return {i.get("task") for g in d.get("groups", []) for i in g.get("items", [])}


add("ac_scelta")
r1 = choice("ac_scelta")
add("ac_ok")
okcard("ac_ok")
add("ac_presa")
choice("ac_presa")
T.device_ok("approve", "ac_presa", "maintainer (app)", "Anello", ENV, choice="Anello")   # come la registra il relay
add("ac_ritirata")
choice("ac_ritirata")
cm("withdraw", "ac_ritirata", "--by", "maintainer", "--reason", "non serve piu'")
db("UPDATE tasks SET created_at=? WHERE id LIKE 'ac_%'", int(time.time()) - 7 * 3600)
st0 = {t: show(t)["state"] for t in ("ac_scelta", "ac_ok", "ac_presa", "ac_ritirata")}
rc, out, err = cm("auto-close", "--json")
res = {x["task"]: x["green"] for x in json.loads(out or "[]")}
dec = decision_tasks()

T.check("AC1 auto-close: worked, check green, open for 7 hours, awaiting_ok with a four-option choice unanswered: stays awaiting_ok, its check not run, the card open and in `task decisioni`",
        r1[0] == 0 and st0["ac_scelta"] == "awaiting_ok" and show("ac_scelta")["state"] == "awaiting_ok" and "ac_scelta" not in res
        and req("ac_scelta")["closed_at"] is None and req("ac_scelta")["choice"] is None and "ac_scelta" in dec,
        f"{r1[2][-200:]} {st0} {res} {req('ac_scelta')} {dec}")
T.check("AC2 the same with an ok card (no options)",
        st0["ac_ok"] == "awaiting_ok" and show("ac_ok")["state"] == "awaiting_ok" and "ac_ok" not in res and req("ac_ok")["closed_at"] is None and "ac_ok" in dec,
        f"{st0} {res} {dec}")
T.check("AC3 the choice taken (the ok with the choice, from the relay): auto-close closes it",
        st0["ac_presa"] == "approved" and res.get("ac_presa") is True and show("ac_presa")["state"] == "done", f"{st0} {res} {out} {err[-200:]}")
T.check("AC4 the card closed by the person without an ok (withdraw): out of awaiting_ok (assigned, no session), auto-close closes it",
        st0["ac_ritirata"] in ("running", "assigned") and res.get("ac_ritirata") is True and show("ac_ritirata")["state"] == "done", f"{st0} {res}")

rc, out, err = cm("done", "ac_scelta", "--json")
r = req("ac_scelta")
steps = [s["text"] for s in show("ac_scelta").get("steps", [])]
T.check("AC5 `task done` by hand with the choice open and the check green: done, the card closed (closed_at, closed_by «task done», the reason), a step, a warning on stderr",
        rc == 0 and show("ac_scelta")["state"] == "done" and r["closed_at"] is not None and r["closed_by"] == "task done"
        and r["closed_kind"] == "closed" and "task done" in (r["closed_reason"] or "") and any("task done" in (s or "") for s in steps)
        and "ac_scelta" in err and "scelta" in err and str(r["n"]) in err and json.loads(out).get("card_closed") == r["n"],
        f"rc {rc} {err[-300:]} {r} {steps}")

add("ac_rosso", check="test -f manca.txt")
choice("ac_rosso")
rc, out, err = cm("done", "ac_rosso")
T.check("AC6 `task done` by hand with the check red: the task stays awaiting_ok and the card open",
        rc != 0 and show("ac_rosso")["state"] == "awaiting_ok" and req("ac_rosso")["closed_at"] is None, f"rc {rc} {err[-200:]}")
T.rm(str(tmp))
T.finish()
