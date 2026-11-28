#!/usr/bin/env python3
"""auto-close aspetta la prova della persona quando l'esito la chiede; `task reopen` (28/11/2026, supervisor-autoclose-prova-28-11).

Alle 03:41 del 28/11 auto-close ha chiuso tre compiti col controllo verde e nessuna prova, il cui esito chiedeva la prova
della persona («… Telefono e web. Prova di <nome>.»); uno era in pausa --waits external, che auto-close tratta come
chiudibile. Il controllo guardava commit, build e release, non la prova. Ora quei compiti aspettano una prova ok in vigore
o la conferma della persona; `task reopen` riapre un compito chiuso per errore. Registro temporaneo, un nome inventato.

AP1 auto-close: lavorato, controllo verde, aperto da 7 ore, esito «Prova di Ada.», nessuna prova: resta aperto e il suo
    controllo non gira; anche in pausa --waits external (il caso del dock) e con la prova chiesta solo in expected
AP2 la voce «prova» in outcome.proof («commit, build, deploy web, prova») basta, anche senza il nome
AP3 un esito senza la prova della persona si chiude come prima; «una prova di carico» (minuscola dopo «di») non e' la persona
AP4 la prova ok in vigore, o la conferma della persona (task confirm, «Tutto va»): auto-close lo chiude
AP5 resettle: un compito con l'esito che chiede la prova e una prova ancora da fare non e' fra i rossi, e non lo chiude
AP6 `task reopen ID --reason R --by B`: torna running (era stato lavorato), la chiusura esce dalla pagella, un passo dice
    chi, da che stato e perche', la sessione lo sa; di nuovo: exit 3, gia' aperto; senza --reason: l'uso; --json
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


def cm(*args):
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), *args], capture_output=True, text=True, env=ENV, timeout=120)
    return r.returncode, r.stdout, r.stderr


def add(tid, expected, proof, check="test -f ok.txt"):
    c = {"schema_version": 1, "task": {
        "id": tid, "title": f"compito {tid}", "type": "feature", "plan": None,
        "where": {"project": str(proj), "session": "s-app", "host": "local", "account": None},
        "outcome": {"expected": expected, "kind": "custom", "proof": proof},
        "check": {"cmd": check, "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open",
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


APP = "Il dock si allunga fra le icone e tira come un elastico ai bordi. Telefono e web. Prova di Ada."
add("ap_dock", APP, "commit, build, deploy web, prova")
cm("pause", "ap_dock", "--reason", "aspetta il deploy web", "--waits", "external")
add("ap_webp", "Un WebP animato si muove in loop quando e' visibile. Telefono e web. Prova di Ada.", "commit, build, deploy web, prova")
add("ap_expected", "Quando Ada accetta un suggerimento, la percentuale cambia subito. Prova di Ada.", "tests/x-verify.py verde, release con l'id")
add("ap_voce", "Il deploy e' su, la pagina si apre.", "commit, build, deploy web, prova")
add("ap_lavoro", "Test rosso prima e verde dopo; release con l'id.", "test rosso prima e verde dopo, release con l'id")
add("ap_carico", "Una prova di carico di 10 minuti regge senza errori.", "la prova di carico, il log")
add("ap_provata", APP, "commit, build, deploy web, prova")
add("ap_confermata", APP, "commit, build, deploy web, prova")
add("ap_da_provare", APP, "commit, build, deploy web, prova")
cm("prova", "ap_provata", "--build", "b1", "--dove", "home", "--fai", "trascina il dock", "--atteso", "si allunga")
cm("prova", "ap_da_provare", "--build", "b1", "--dove", "home", "--fai", "trascina il dock", "--atteso", "si allunga")
sp = importlib.util.spec_from_file_location("cm_tasks_ap", T.SCRIPTS / "cm-tasks.py")
TK = importlib.util.module_from_spec(sp)
os.environ.update(ENV)
sp.loader.exec_module(TK)
con = TK.connect()
TK.prove_mark(con, [r["n"] for r in db("SELECT n FROM prove WHERE task='ap_provata'")], "ok", "maintainer", "phone")   # senza settle: lo fa auto-close
T.device_ok("confirm", "ap_confermata", "maintainer (app)", "Tutto va", ENV)
db("UPDATE tasks SET created_at=? WHERE id LIKE 'ap_%'", int(time.time()) - 7 * 3600)
rc, out, err = cm("auto-close", "--json")
res = {x["task"]: x["green"] for x in json.loads(out or "[]")}
st = {t: state(t) for t in ("ap_dock", "ap_webp", "ap_expected", "ap_voce", "ap_lavoro", "ap_carico", "ap_provata", "ap_confermata", "ap_da_provare")}
OPEN = ("proposed", "assigned", "running", "awaiting_ok", "approved")

T.check("AP1 auto-close: worked, check green, open 7 hours, outcome «Prova di Ada.», no test: stays open and its check does not run; also in a --waits external pause (the dock), and with the test asked only in expected",
        rc == 0 and all(st[t] in OPEN and t not in res for t in ("ap_dock", "ap_webp", "ap_expected")), f"rc={rc} {err[-200:]} st={st} res={res}")
T.check("AP2 the item «prova» in outcome.proof («commit, build, deploy web, prova») is enough, without the name",
        st["ap_voce"] in OPEN and "ap_voce" not in res, f"st={st['ap_voce']} res={res}")
T.check("AP3 an outcome that does not ask for the person's test closes as before; «una prova di carico» (lower case after «di») is not the person",
        res.get("ap_lavoro") is True and st["ap_lavoro"] == "done" and res.get("ap_carico") is True and st["ap_carico"] == "done", f"st={st} res={res}")
T.check("AP4 a test marked ok and in force, or the person's confirmation («Tutto va»): auto-close closes it",
        res.get("ap_provata") is True and st["ap_provata"] == "done" and res.get("ap_confermata") is True and st["ap_confermata"] == "done", f"st={st} res={res}")

# AP5 resettle: la prova da fare tiene il compito fuori dai rossi
out5 = TK.resettle(con, now_=int(time.time()) + 3600) if hasattr(TK, "resettle") else None
red = {x["task"] for x in TK.prove_list(con)["red"]}
T.check("AP5 resettle: a task whose outcome asks for the test, with a test still to do, is not among the reds and is not closed",
        isinstance(out5, list) and "ap_da_provare" not in red and "ap_da_provare" not in [o["task"] for o in out5] and state("ap_da_provare") in OPEN,
        f"out={out5} red={red} st={state('ap_da_provare')}")

# AP6 task reopen
closures0 = db("SELECT task FROM closures WHERE task='ap_lavoro'")
n_talks = len(talks.read_text().splitlines()) if talks.exists() else 0
r1 = cm("reopen", "ap_lavoro", "--reason", "chiuso per errore: mancava la prova", "--by", "master")
steps = [s["text"] for s in db("SELECT text FROM steps WHERE task='ap_lavoro' ORDER BY n")]
time.sleep(1.0)   # l'avviso alla sessione e' staccato
new_talks = (talks.read_text().splitlines() if talks.exists() else [])[n_talks:]
r2 = cm("reopen", "ap_lavoro", "--reason", "di nuovo")
r3 = cm("reopen", "ap_carico")
r4 = cm("reopen", "ap_carico", "--reason", "anche questo", "--json")
T.check("AP6 `task reopen ID --reason R --by B`: back to running (it was worked on), its closure leaves the report card, a step says who, from which state and why, the session is told; again: exit 3, already open; without --reason: the usage; --json",
        r1[0] == 0 and "reopened (was done) → running" in r1[1] and state("ap_lavoro") == "running"
        and closures0 and not db("SELECT task FROM closures WHERE task='ap_lavoro'")
        and any(s == "riaperto (era done) da master: chiuso per errore: mancava la prova" for s in steps)
        and any(ln.startswith("talk s-app ap_lavoro: riaperto da master: chiuso per errore: mancava la prova.") for ln in new_talks)
        and r2[0] == 3 and "already open" in r2[2] and r3[0] != 0 and "usage: task reopen" in r3[2]
        and r4[0] == 0 and json.loads(r4[1]) == {"task": "ap_carico", "reopened": True, "state": "running", "prove_superseded": []} and state("ap_carico") == "running",
        f"r1={r1} closures0={closures0} steps={steps[-2:]} talks={new_talks} r2={r2[0]} {r2[2][-80:]} r3={r3[0]} {r3[2][-80:]} r4={r4}")

con.close()
T.rm(tmp)
T.finish()
