#!/usr/bin/env python3
"""Un «non va» su una prova gia' ok di un compito aperto (28/11/2026, supervisor-ko-su-prova-ok-28-11).

Alle 15:48 `task ok --ko` sulla prova 1461, gia' ok, di un compito aperto rispondeva «no test and no pause waits for the
person»: un «non va» arrivato dopo l'ok non fermava la chiusura da sola. Ora la prova passa ko col motivo, l'ok resta
nella storia (un passo), e le conferme scritte prima non contano piu': settle, resettle e auto-close aspettano una prova
nuova ok o una conferma nuova. Registro temporaneo, la risposta registrata nel processo (answer), un nome inventato.

KO1 --ko con la prova nominata, gia' ok: ko col motivo; un passo dice che era ok, di chi e quando; la sessione lo sa;
    senza --prove, con una sola prova ok in vigore, vale per quella
KO2 il compito non si chiude: non e' fra i rossi (resettle), auto-close lo salta, settle rifiuta, task confirmed esce 1
KO3 «Non serve piu'» sulla prova ko: nessuna prova in vigore, la conferma di prima non conta (auto-close aspetta); una
    conferma nuova lo chiude
KO4 «Ora va» sulla prova ko: tutte ok di nuovo, settle verde chiude
KO5 due prove ok e --ko senza --prove: rifiutato, niente scritto
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
ADA = "Il dock si allunga fra le icone. Telefono e web. Prova di Ada."


def cm(*args):
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), *args], capture_output=True, text=True, env=ENV, timeout=120)
    return r.returncode, r.stdout, r.stderr


def add(tid):
    c = {"schema_version": 1, "task": {
        "id": tid, "title": f"compito {tid}", "type": "bug", "plan": None,
        "where": {"project": str(proj), "session": "s-app", "host": "local", "account": None},
        "outcome": {"expected": ADA, "kind": "custom", "proof": "commit, build, deploy web, prova"},
        "check": {"cmd": "test -f ok.txt", "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open",
        "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}}
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), "add", "-"], input=json.dumps(c), capture_output=True, text=True, env=ENV, timeout=120)
    assert r.returncode == 0, r.stderr
    cm("start", tid)
    cm("step", tid, "commit, build e deploy fatti")


def prova(tid, fai):
    cm("prova", tid, "--build", "b1", "--dove", "home", "--fai", fai, "--atteso", "si allunga")
    return max(r["n"] for r in db("SELECT n FROM prove WHERE task=?", tid))


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


def answer(tids, ko, prove_ids=None):
    try:
        return TK.answer(con, tids, "maintainer", "non va piu'", prove_ids=prove_ids, ko=ko, origin="chat"), None
    except SystemExit as e:
        return None, str(e.code)


for t in ("ko_app", "ko_one", "ko_two"):
    add(t)
sp = importlib.util.spec_from_file_location("cm_tasks_ko", T.SCRIPTS / "cm-tasks.py")
TK = importlib.util.module_from_spec(sp)
os.environ.update(ENV)
sp.loader.exec_module(TK)
con = TK.connect()
n_app, n_one = prova("ko_app", "trascina il dock"), prova("ko_one", "trascina il dock")
n_two = [prova("ko_two", "trascina il dock"), prova("ko_two", "apri il menu")]
TK.prove_mark(con, [n_app, n_one] + n_two, "ok", "maintainer", "phone")   # senza settle: aperti con tutte le prove ok
n0 = len(talks.read_text().splitlines()) if talks.exists() else 0

out1, err1 = answer(["ko_app"], "il dock non si allunga piu'", prove_ids=[n_app])
out1b, err1b = answer(["ko_one"], "si blocca a meta'")
time.sleep(1.0)   # l'avviso alla sessione e' staccato
told = (talks.read_text().splitlines() if talks.exists() else [])[n0:]
pv = {r["n"]: r for r in db("SELECT n, result, result_text, superseded_at FROM prove")}
steps = [s["text"] for s in db("SELECT text FROM steps WHERE task='ko_app' ORDER BY n")]
T.check("KO1 --ko with the test named, already ok: ko with the reason; a step says it was ok, by whom and when; the session is told; with no --prove and one ok test in force, it holds for that one",
        err1 is None and pv[n_app]["result"] == "ko" and pv[n_app]["result_text"] == "il dock non si allunga piu'" and pv[n_app]["superseded_at"] is None
        and any(s.startswith(f"prova #{n_app}: era ok (maintainer, ") and s.endswith("ora non va: il dock non si allunga piu'") for s in steps)
        and any(ln.startswith("talk s-app ") and "ko_app" in ln for ln in told)
        and err1b is None and pv[n_one]["result"] == "ko" and pv[n_one]["result_text"] == "si blocca a meta'",
        f"err1={err1} err1b={err1b} pv={pv} steps={steps[-2:]} told={told}")

# KO5 prima di resettle, che chiuderebbe ko_two (tutte ok, controllo verde)
out5, err5 = answer(["ko_two"], "non va")
T.check("KO5 two ok tests and --ko with no --prove: refused, nothing written",
        out5 is None and err5 and "--prove" in err5 and all(r["result"] == "ok" for r in db("SELECT result FROM prove WHERE task='ko_two'")), f"err5={err5}")

out2 = TK.resettle(con, now_=int(time.time()) + 3600) if hasattr(TK, "resettle") else []
red = {x["task"] for x in TK.prove_list(con)["red"]}
db("UPDATE tasks SET created_at=? WHERE id IN ('ko_app', 'ko_one')", int(time.time()) - 7 * 3600)   # ko_two resta per KO5
rc2, o2, _ = cm("auto-close", "--json")
res2 = {x["task"]: x["green"] for x in json.loads(o2 or "[]")}
s2, c2 = cm("settle", "ko_app"), cm("confirmed", "ko_app")
T.check("KO2 the task does not close: not among the reds (resettle), auto-close skips it, settle refuses, task confirmed exits 1",
        "ko_app" not in red and "ko_app" not in [o["task"] for o in out2] and "ko_app" not in res2 and s2[0] == 3 and c2[0] == 1
        and state("ko_app") in OPEN and state("ko_one") in OPEN, f"red={red} out2={out2} res2={res2} settle={s2} confirmed={c2}")

ret = TK.prova_retire(con, n_one, "maintainer", "phone", "non serve piu'")
rc3, o3, _ = cm("auto-close", "--json")
res3 = {x["task"]: x["green"] for x in json.loads(o3 or "[]")}
T.device_ok("confirm", "ko_one", "maintainer (app)", "ora va", ENV)
rc3b, o3b, _ = cm("auto-close", "--json")
res3b = {x["task"]: x["green"] for x in json.loads(o3b or "[]")}
T.check("KO3 «Non serve più» on the ko test: no test in force, the confirmation from before does not count (auto-close waits); a new confirmation closes it",
        ret.get("task") == "ko_one" and "ko_one" not in res3 and res3b.get("ko_one") is True and state("ko_one") == "done", f"ret={ret} res3={res3} res3b={res3b}")

mk = TK.prove_mark(con, [n_app], "ok", "maintainer", "phone")
s4 = cm("settle", "ko_app")
T.check("KO4 «Ora va» on the ko test: all ok again, settle green closes it", mk == {"complete": ["ko_app"]} and s4[0] == 0 and state("ko_app") == "done", f"mk={mk} settle={s4}")

con.close()
T.rm(tmp)
T.finish()
