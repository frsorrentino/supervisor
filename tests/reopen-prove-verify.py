#!/usr/bin/env python3
"""Dopo `task reopen` le prove ok di prima sono superate e le conferme di prima non contano (28/11/2026, supervisor-reopen-prove-28-11).

Alle 15:48 del 28/11 la master ha riaperto un compito perche' la persona diceva che non funzionava; alle 15:50 resettle
l'ha richiuso: la sua prova ok di prima era ancora in vigore e il controllo verde. Ora la riapertura supera le prove ok
in vigore (restano nella storia) e le conferme scritte prima non valgono piu': resettle, auto-close e settle aspettano
una prova nuova ok o una conferma nuova della persona. Registro temporaneo, un nome inventato.

RP1 reopen di un compito chiuso con la prova ok: la prova e' superata, resta nella storia come ok (prova_one), il passo
    e l'avviso alla sessione lo dicono, --json la elenca
RP2 resettle: il compito riaperto non e' fra i rossi e non si richiude, anche col controllo verde
RP3 auto-close: un compito riaperto aspetta una prova nuova o una conferma nuova, anche se l'esito non chiede la prova
    della persona; prima della riapertura chiudeva come prima
RP4 la conferma di prima («Tutto va»): `task confirmed` esce 1 e `task settle` non chiude
RP5 la strada per chiudere: una prova nuova ok (settle verde), una conferma nuova (auto-close, task confirmed, settle)
RP6 una seconda riapertura supera anche la prova nuova e la sua conferma
"""
import importlib.util
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
talks = tmp / "talks.log"
fake = tmp / "fake-cm"
fake.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{talks}"\nexit 0\n')
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update({"CM_TASKS_DB": str(DB), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(tmp), "CM_HOME": str(tmp), "CM_RELAY_CM": "/bin/false",
            "CM_TASK_TALK_BIN": str(fake)})
OPEN = ("proposed", "assigned", "running", "awaiting_ok", "approved")
ADA = "Le novita' dicono cosa cambia. Telefono e web. Prova di Ada."
LAVORO = "Test rosso prima e verde dopo; release con l'id."


def cm(*args):
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), *args], capture_output=True, text=True, env=ENV, timeout=120)
    return r.returncode, r.stdout, r.stderr


def add(tid, expected, proof):
    c = {"schema_version": 1, "task": {
        "id": tid, "title": f"compito {tid}", "type": "bug", "plan": None,
        "where": {"project": str(proj), "session": "s-app", "host": "local", "account": None},
        "outcome": {"expected": expected, "kind": "custom", "proof": proof},
        "check": {"cmd": "test -f ok.txt", "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open",
        "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}}
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), "add", "-"], input=json.dumps(c), capture_output=True, text=True, env=ENV, timeout=120)
    assert r.returncode == 0, r.stderr
    cm("start", tid)
    cm("step", tid, "commit, build e deploy fatti")


def state(tid):
    return json.loads(cm("show", tid, "--json")[1])["state"]


def db(q, *p):
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    try:
        out = [dict(r) for r in con.execute(q, p).fetchall()]
        con.commit()
        return out
    finally:
        con.close()


def prova(tid, build):
    cm("prova", tid, "--build", build, "--dove", "home", "--fai", "apri le novita'", "--atteso", "dicono cosa cambia")
    return max(r["n"] for r in db("SELECT n FROM prove WHERE task=?", tid))


def talks_now():
    return talks.read_text().splitlines() if talks.exists() else []


# i compiti: chiusi come si chiudono dal vivo (una prova ok e settle, `task done`, «Tutto va» e done), poi riaperti
for t in ("rp_res", "rp_auto"):
    add(t, ADA, "commit, build, deploy web, prova")
add("rp_lavoro", LAVORO, "test rosso prima e verde dopo, release con l'id")
add("rp_tuttova", LAVORO, "test rosso prima e verde dopo, release con l'id")
sp = importlib.util.spec_from_file_location("cm_tasks_rp", T.SCRIPTS / "cm-tasks.py")
TK = importlib.util.module_from_spec(sp)
os.environ.update(ENV)
sp.loader.exec_module(TK)
con = TK.connect()
n_res, n_auto = prova("rp_res", "b1"), prova("rp_auto", "b1")
TK.prove_mark(con, [n_res, n_auto], "ok", "maintainer", "phone")
closed = [cm("settle", "rp_res")[0], cm("settle", "rp_auto")[0], cm("done", "rp_lavoro")[0]]
T.device_ok("confirm", "rp_tuttova", "maintainer (app)", "Tutto va", ENV)
closed.append(cm("done", "rp_tuttova")[0])
assert closed == [0, 0, 0, 0] and all(state(t) == "done" for t in ("rp_res", "rp_auto", "rp_lavoro", "rp_tuttova")), closed

n0 = len(talks_now())
r1 = cm("reopen", "rp_res", "--reason", "Ada: le novita' non funzionano", "--by", "master", "--json")
time.sleep(1.0)   # l'avviso alla sessione e' staccato
told = talks_now()[n0:]
for t in ("rp_auto", "rp_lavoro", "rp_tuttova"):
    cm("reopen", t, "--reason", "non funziona", "--by", "master")
pv = db("SELECT result, superseded_at FROM prove WHERE n=?", n_res)[0]
step = [s["text"] for s in db("SELECT text FROM steps WHERE task='rp_res' ORDER BY n")][-1]
one = TK.prova_one(con, n_res)
T.check("RP1 reopen of a task closed with its test ok: the test is superseded, stays in the history as ok, the step and the session's notice say so, --json lists it",
        r1[0] == 0 and json.loads(r1[1] or "{}") == {"task": "rp_res", "reopened": True, "state": "running", "prove_superseded": [n_res]}
        and pv["result"] == "ok" and pv["superseded_at"] and one and one["state"] == "ok" and not TK._live_prove(con, "rp_res")
        and step.startswith("riaperto (era done) da master: Ada: le novita' non funzionano") and f"#{n_res}" in step
        and any(ln.startswith("talk s-app rp_res: riaperto da master") and f"#{n_res}" in ln and "prova nuova" in ln for ln in told),
        f"r1={r1} pv={pv} one={one and one['state']} step={step!r} told={told}")

out2 = TK.resettle(con, now_=int(time.time()) + 3600)
red = {x["task"] for x in TK.prove_list(con)["red"]}
T.check("RP2 resettle: the reopened task is not among the reds and does not close again, with its check green",
        "rp_res" not in red and "rp_res" not in [o["task"] for o in out2] and state("rp_res") in OPEN, f"red={red} out={out2} st={state('rp_res')}")

db("UPDATE tasks SET created_at=? WHERE id LIKE 'rp_%'", int(time.time()) - 7 * 3600)
rc3, out3, err3 = cm("auto-close", "--json")
res3 = {x["task"]: x["green"] for x in json.loads(out3 or "[]")}
T.check("RP3 auto-close: a reopened task waits for a new test or a new confirmation, also when its outcome does not ask for the person's test",
        rc3 == 0 and all(t not in res3 and state(t) in OPEN for t in ("rp_auto", "rp_lavoro", "rp_tuttova")), f"rc={rc3} {err3[-200:]} res={res3} st={[state(t) for t in ('rp_auto', 'rp_lavoro', 'rp_tuttova')]}")

c4, s4 = cm("confirmed", "rp_tuttova"), cm("settle", "rp_tuttova")
T.check("RP4 the confirmation from before («Tutto va»): `task confirmed` exits 1 and `task settle` does not close",
        c4[0] == 1 and s4[0] == 3 and state("rp_tuttova") in OPEN, f"confirmed={c4} settle={s4} st={state('rp_tuttova')}")

n_new = prova("rp_res", "b2")
mk = TK.prove_mark(con, [n_new], "ok", "maintainer", "phone")
s5 = cm("settle", "rp_res")
T.device_ok("confirm", "rp_lavoro", "maintainer (app)", "ora va", ENV)
rc5, out5, _ = cm("auto-close", "--json")
res5 = {x["task"]: x["green"] for x in json.loads(out5 or "[]")}
T.device_ok("confirm", "rp_tuttova", "maintainer (app)", "ora va", ENV)
c5, s5b = cm("confirmed", "rp_tuttova"), cm("settle", "rp_tuttova")
T.check("RP5 the way to close: a new test ok (settle green), a new confirmation (auto-close, task confirmed, settle)",
        mk == {"complete": ["rp_res"]} and s5[0] == 0 and state("rp_res") == "done" and res5.get("rp_lavoro") is True and state("rp_lavoro") == "done"
        and c5[0] == 0 and s5b[0] == 0 and state("rp_tuttova") == "done" and "rp_auto" not in res5 and state("rp_auto") in OPEN,
        f"mk={mk} s5={s5} res5={res5} c5={c5} s5b={s5b} st={[state(t) for t in ('rp_res', 'rp_lavoro', 'rp_tuttova', 'rp_auto')]}")

r6 = cm("reopen", "rp_res", "--reason", "ancora no", "--by", "master", "--json")
c6 = cm("confirmed", "rp_res")
T.check("RP6 a second reopen supersedes the new test too, and its confirmation no longer counts",
        r6[0] == 0 and json.loads(r6[1] or "{}").get("prove_superseded") == [n_new] and c6[0] == 1 and not TK._live_prove(con, "rp_res")
        and TK.prova_one(con, n_new)["state"] == "ok", f"r6={r6} c6={c6}")

con.close()
T.rm(tmp)
T.finish()
