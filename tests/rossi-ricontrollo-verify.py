#!/usr/bin/env python3
"""I compiti con tutte le prove ok e il controllo rosso si ricontrollano da soli (27/11/2026, supervisor-rossi-ricontrollo-27-11).

Dal telefono alle 21:46: «Quelli rossi rimangono qui anche se sono superati». Due compiti dell'app erano rossi dalle
20:50 per «ultima build non verde»; alle 21:47 la CI era verde, ma restavano in «Da provare › Controllo rosso» finche'
la master non li ha chiusi a mano: settle() parte solo quando arriva l'esito di una prova. Ora `task resettle` (dalla
guardia, ogni 10 minuti) li ricontrolla. Registro temporaneo; il tempo che passa e' il `now_` del giro.

RR1 subito dopo il settle rosso: il giro non li ricontrolla (al piu' ogni 10 minuti), niente cambia
RR2 dieci minuti dopo, la CI ancora rossa: il controllo rigira; nessun tentativo speso, il compito resta aperto, nessun
    avviso, nessun passo nuovo col motivo uguale; l'altro compito, col motivo cambiato, ha il passo col motivo nuovo
RR3 lo stesso giro ripetuto subito: niente (al piu' una volta ogni 10 minuti per compito)
RR4 la CI torna verde: al giro dopo il compito si chiude come con settle (done, esito verde) e la sessione e la master
    lo sanno; esce dai rossi di `task prove`
RR5 un settle sta girando (il suo lucchetto e' preso): il giro si ferma e lo dice (busy), nessun controllo
RR6 dalla riga di comando, col tempo vero: `task resettle --log` stampa il rosso e lo scrive in guard.log; ripetuto
    subito non fa niente (il file accanto al registro ricorda l'ora); un secondo giro mentre gira il primo esce subito
RR7 una pausa per la persona aperta dopo il rosso: come auto-close, il giro non lo ricontrolla
"""
import fcntl
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
home.mkdir()
state = tmp / "state"
state.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "accounts": {"p": {"config_dir": str(home / ".claude")}},
                           "default_account": "p", "workspace": {"root": str(tmp), "root_session_name": "master"}}))
talks = tmp / "talks.log"
fake = tmp / "fake-cm"
fake.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{talks}"\nexit 0\n')
fake.chmod(0o755)
db = tmp / "t.db"
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update({"CM_TASKS_DB": str(db), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASK_TALK_BIN": str(fake)})
os.environ.clear()
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")
flag = tmp / "ci-verde"
why_file = tmp / "motivo"
why_file.write_text("deploy fermo\n")


def cm(*a, stdin=None):
    return subprocess.run([CLI, "task", *a], input=stdin, capture_output=True, text=True, env=ENV, timeout=300)


def add(tid, check, session):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"compito {tid}", "plan": "p", "where": {"project": str(tmp), "session": session, "host": "local", "account": None},
                                       "check": {"cmd": check, "cwd": str(tmp), "timeout_s": 60}, "perimeter": ["x"], "lane": "open", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
                                       "outcome": {"expected": "x", "kind": "custom", "proof": "x"}}}
    r = cm("add", "-", stdin=json.dumps(c))
    assert r.returncode == 0, r.stderr
    r = cm("prova", tid, "--build", "b1", "--dove", "home", "--fai", "tocca", "--atteso", "si apre")
    assert r.returncode == 0, r.stderr


add("rr-ci", f'if [ -f "{flag}" ]; then echo "build verde"; else echo "ultima build non verde"; exit 1; fi', "s-ci")
add("rr-deploy", f'cat "{why_file}"; exit 1', "s-deploy")
sp = importlib.util.spec_from_file_location("cm_tasks_rr", T.SCRIPTS / "cm-tasks.py")
TK = importlib.util.module_from_spec(sp)
sp.loader.exec_module(TK)
con = TK.connect()
res = TK.prove_mark(con, [r[0] for r in con.execute("SELECT n FROM prove")], "ok", "maintainer", "phone")
settled = [cm("settle", t) for t in ("rr-ci", "rr-deploy")]
T0 = int(time.time())
resettle = getattr(TK, "resettle", None)


def round_(now_):
    try:
        return resettle(con, now_=now_) if resettle else None
    except Exception as e:   # noqa: BLE001 — su main il verbo non c'e': il controllo dice rosso, non si ferma
        return f"{type(e).__name__}: {e}"


def info(tid):
    row = TK.get_task(con, tid)
    steps = [r["text"] for r in con.execute("SELECT text FROM steps WHERE task=? AND kind='prova-red' ORDER BY n", (tid,))]
    return {"state": row["state"], "attempts": TK.attempts(con, tid), "red_steps": steps}


def talk_lines(wait=0.0):
    end = time.time() + wait
    while True:
        lines = talks.read_text().splitlines() if talks.exists() else []
        if time.time() >= end:
            return lines
        time.sleep(0.2)


base_talks = len(talk_lines(wait=1.5))   # i due avvisi rossi di settle, staccati

# RR1
out = round_(T0)
a, b = info("rr-ci"), info("rr-deploy")
T.check("RR1 right after the red settle: the round does not check them again (at most every 10 minutes), nothing changes",
        all(s.returncode == 1 for s in settled) and len(res["complete"]) == 2 and out == []
        and a == {"state": a["state"], "attempts": 0, "red_steps": ["ultima build non verde"]} and a["state"] in TK.OPEN_STATES
        and b["red_steps"] == ["deploy fermo"],
        f"settle={[(s.returncode, s.stdout.strip()[-120:]) for s in settled]} out={out} a={a} b={b}")

# RR2
why_file.write_text("deploy fermo: manca la chiave\n")
out = round_(T0 + 601)
a, b = info("rr-ci"), info("rr-deploy")
red = {x["task"]: x["reason"] for x in TK.prove_list(con)["red"]}
T.check("RR2 ten minutes later, CI still red: the check runs again; no attempt spent, the task stays open, no message, no new step for the same reason; the other task, whose reason changed, has a step with the new one",
        isinstance(out, list) and sorted((o["task"], o["outcome"]) for o in out) == [("rr-ci", "red"), ("rr-deploy", "red")]
        and a["attempts"] == 0 and a["state"] in TK.OPEN_STATES and a["red_steps"] == ["ultima build non verde"]
        and b["attempts"] == 0 and b["red_steps"] == ["deploy fermo", "deploy fermo: manca la chiave"]
        and red == {"rr-ci": "ultima build non verde", "rr-deploy": "deploy fermo: manca la chiave"}
        and len(talk_lines(wait=1.0)) == base_talks,
        f"out={out} a={a} b={b} red={red} talks={talk_lines()[base_talks:]}")

# RR3 subito, all'ora vera: il file accanto al registro ha l'ora vera del controllo di RR2
out = round_(int(time.time()))
T.check("RR3 the same round again at once: nothing (at most once every 10 minutes per task)",
        out == [] and info("rr-ci")["red_steps"] == ["ultima build non verde"], f"out={out}")

# RR4
flag.write_text("ok\n")
out = round_(T0 + 1202)
a = info("rr-ci")
res_row = con.execute("SELECT outcome, reason FROM results WHERE task='rr-ci' ORDER BY n DESC LIMIT 1").fetchone()
new_talks = talk_lines(wait=2.0)[base_talks:]
closed = [ln for ln in new_talks if "rr-ci: tutte le prove ok, controllo verde: compito chiuso" in ln]
red = {x["task"] for x in TK.prove_list(con)["red"]}
T.check("RR4 CI green again: at the next round the task closes as with settle (done, a green outcome), the session and the master are told; it leaves the reds of `task prove`",
        isinstance(out, list) and ("rr-ci", "green") in [(o["task"], o["outcome"]) for o in out] and a["state"] == "done"
        and res_row is not None and res_row["outcome"] == "green"
        and sorted(ln.split()[1] for ln in closed) == ["master", "s-ci"] and "rr-ci" not in red and "rr-deploy" in red,
        f"out={out} a={a} res={dict(res_row) if res_row else None} talks={new_talks} red={red}")

# RR5
held = open(str(db) + ".settle.lock", "a")
fcntl.flock(held, fcntl.LOCK_EX)
n_before = len(info("rr-deploy")["red_steps"])
why_file.write_text("deploy fermo: terzo motivo\n")
out = round_(T0 + 1803)
held.close()
T.check("RR5 a settle is running (its lock is held): the round stops and says so (busy), no check runs",
        out == [{"task": "rr-deploy", "outcome": "busy", "reason": None}] and len(info("rr-deploy")["red_steps"]) == n_before,
        f"out={out} steps={info('rr-deploy')['red_steps']}")

# RR6 dalla riga di comando, col tempo vero: l'ultimo controllo di rr-deploy portato indietro di 11 minuti
con.execute("UPDATE steps SET at=at-660 WHERE task='rr-deploy'")
con.execute("UPDATE prove SET result_at=result_at-660 WHERE task='rr-deploy'")
con.commit()
seen_f = Path(str(db) + ".resettle.json")
seen = json.loads(seen_f.read_text()) if seen_f.exists() else {}
seen.pop("rr-deploy", None)
seen_f.write_text(json.dumps(seen))
r1 = cm("resettle", "--log")
glog = (state / "guard.log").read_text() if (state / "guard.log").exists() else ""
r2 = cm("resettle", "--log")
run_lock = open(str(db) + ".resettle.lock", "a")
fcntl.flock(run_lock, fcntl.LOCK_EX)
r3 = cm("resettle")
run_lock.close()
T.check("RR6 from the command line, real time: `task resettle --log` prints the red and writes it in guard.log; again at once it does nothing (the file next to the registry keeps the time); a second round while the first runs exits at once",
        r1.returncode == 0 and "rr-deploy: red — deploy fermo: terzo motivo; no attempt spent" in r1.stdout
        and "resettle: rr-deploy: red — deploy fermo: terzo motivo" in glog
        and r2.returncode == 0 and r2.stdout.strip() == "" and info("rr-deploy")["attempts"] == 0
        and r3.returncode == 0 and "another round is running" in r3.stderr,
        f"r1={r1.returncode} {r1.stdout[-200:]} {r1.stderr[-200:]} glog={glog[-200:]} r2={r2.stdout[-200:]} r3={r3.returncode} {r3.stderr[-120:]}")

# RR7 una pausa per la persona aspetta lei: come auto-close, niente ricontrollo che lo chiuda da solo
add("rr-pausa", 'echo "build rossa"; exit 1', "s-pausa")
n = [r[0] for r in con.execute("SELECT n FROM prove WHERE task='rr-pausa'")]
TK.prove_mark(con, n, "ok", "maintainer", "phone")
s7 = cm("settle", "rr-pausa")
p7 = cm("pause", "rr-pausa", "--reason", "aspetta la persona: quale build provare", "--waits", "person")
out = round_(int(time.time()) + 601)
T.check("RR7 a pause for the person waits for her: as with auto-close, no recheck closes the task by itself",
        s7.returncode == 1 and p7.returncode == 0 and isinstance(out, list) and "rr-pausa" not in [o["task"] for o in out]
        and info("rr-pausa")["red_steps"] == ["build rossa"] and info("rr-pausa")["state"] in TK.OPEN_STATES,
        f"settle={s7.returncode} pause={p7.returncode} {p7.stderr[-160:]} out={out} info={info('rr-pausa')}")

con.close()
T.rm(tmp)
T.finish()
