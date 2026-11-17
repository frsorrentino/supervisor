#!/usr/bin/env python3
"""Un ok chiude la carta e le voci collegate alla push seguente (17/11/2026, supervisor-ok-propagazione-17-11).

Il 17/11 un compito e' stato approvato alle 11:51 (dall'inbox) e l'app l'ha visto verso le 11:54;
intanto la stessa richiesta restava aperta come needs-you della sua sessione («ok push del link PDF»). I minuti: la
raccolta della push durava 78-296 s (vedi tests/relay-fila-verify.py, FI1). La richiesta aperta: il needs-you si spegneva
solo con «Approva» nell'app (wake_after_approve), e solo se il nome della sessione nel registro era quello tmux; un ok
dall'inbox o dalla CLI non lo toccava.
Registro temporaneo, talk finto (CM_TASK_TALK_BIN), il relay caricato come modulo.

OK1 compito in attesa d'ok (wait-ok) e un needs-you legato (--task) sulla sua sessione: la carta e' fra le approvazioni,
    il needs-you fra le richieste
OK2 ok dal registro (come dall'inbox o dalla CLI, non dall'app): l'impronta delle sorgenti cambia (il battito fa la
    raccolta completa, non la ripubblicazione), alla raccolta seguente la carta esce e il needs-you si chiude, con la
    riga needs-you-done how=ok nel ledger; tutto in meno di 5 s
OK3 restano: il needs-you legato a un altro compito ancora in attesa, quello senza compito, quello scritto dopo l'ok
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
state = tmp / "state"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "accounts": {"p": {"config_dir": str(home / ".claude")}},
                           "default_account": "p", "workspace": {"root": str(tmp), "root_session_name": "master"}, "relay": {"dir": str(tmp / "relay")}}))
(tmp / "relay").mkdir()
fake = tmp / "fake-cm"
fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {tmp / 'talk.log'}\nexit 0\n")
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update({"CM_TASKS_DB": str(tmp / "t.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASK_TALK_BIN": str(fake),
            "CM_RELAY_NO_PUSH": "1"})
os.environ.clear()
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")


def task(*a, stdin=None):
    return subprocess.run([CLI, "task", *a], input=stdin, capture_output=True, text=True, env=ENV, timeout=120)


def add(tid, session):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"t {tid}", "plan": "p", "where": {"project": str(tmp), "session": session, "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": str(tmp), "timeout_s": 30}, "perimeter": ["x"], "lane": "open", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
                                       "outcome": {"expected": "x", "kind": "custom", "proof": "x"}}}
    assert task("add", "-", stdin=json.dumps(c)).returncode == 0
    task("start", tid)


# il registro dice «my-alfa» (il nome corto), la sessione tmux e' «pix-my-alfa»: come il 17/11
add("op-link", "my-alfa")
add("op-altro", "beta")
for t_ in ("op-link", "op-altro"):
    r = task("wait-ok", t_, "--consiglio", "va bene: prova", "--what", "push del link PDF", "--where", "produzione")
    assert r.returncode == 0, r.stderr
sp = importlib.util.spec_from_file_location("cm_relay_okp", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
RL._wake = lambda *a, **k: None
NY = RL._load("cm-needsyou")
rows = [{"tmux": "pix-my-alfa", "name": "pix-my-alfa", "session_id": "sid-alfa", "status": "idle", "cwd": str(tmp)},
        {"tmux": "beta", "name": "beta", "session_id": "sid-beta", "status": "idle", "cwd": str(tmp)},
        {"tmux": "gamma", "name": "gamma", "session_id": "sid-gamma", "status": "idle", "cwd": str(tmp)}]
NY.mark(rows[0], "ok push del link PDF", task="op-link", quiet=True)
NY.mark(rows[1], "ok per l'altro", task="op-altro", quiet=True)
NY.mark(rows[2], "guarda la pagina", quiet=True)
ny = state / "needs-you"
for sid in ("sid-alfa", "sid-beta", "sid-gamma"):   # scritti prima dell'ok, anche se il test corre nello stesso secondo
    p = ny / f"{sid}.json"
    rec = json.loads(p.read_text())
    rec["at"] = int(time.time()) - 60
    p.write_text(json.dumps(rec))

# OK1
cards0 = [a["task"] for a in RL.approvals_list()]
needs0 = RL.needs_you_of(rows)
T.check("OK1 the task awaiting ok (wait-ok) is a card, and the needs-you bound to it (--task) is open on its session",
        "op-link" in cards0 and "op-altro" in cards0 and needs0.get("pix-my-alfa", {}).get("task") == "op-link" and "beta" in needs0 and "gamma" in needs0,
        f"{cards0} {sorted(needs0)}")

# OK2: l'ok dal registro, non dall'app
fp0 = RL.sources_print()
t0 = time.time()
TK = RL._load("cm-tasks")
con = TK.connect()
TK.approve(con, "op-link", "maintainer", "ok (dall'inbox)")
con.close()
fp1 = RL.sources_print()
cards1 = [a["task"] for a in RL.approvals_list()]
needs1 = RL.needs_you_of(rows)
dt = time.time() - t0
led = [json.loads(x) for x in (state / "ledger.jsonl").read_text().splitlines()] if (state / "ledger.jsonl").exists() else []
done = [x for x in led if x.get("event") == "needs-you-done" and x.get("how") == "ok"]
T.check("OK2 an ok from the registry (inbox or CLI, not the app): the sources' fingerprint changes, so the beat runs a full collection",
        fp1 != fp0, f"{fp0[-80:]} → {fp1[-80:]}")
T.check("OK2 at the next collection the card is gone and the bound needs-you is closed, with needs-you-done how=ok in the ledger, all in under 5 s",
        "op-link" not in cards1 and "pix-my-alfa" not in needs1 and not (ny / "sid-alfa.json").exists()
        and any(x.get("task") == "op-link" for x in done) and dt < 5, f"{cards1} {sorted(needs1)} {done} {dt:.1f} s")

# OK3
NY.mark(rows[0], "un'altra cosa sullo stesso compito, dopo l'ok", task="op-link", quiet=True)
p = ny / "sid-alfa.json"
rec = json.loads(p.read_text())
rec["at"] = int(time.time()) + 5   # dopo l'ok
p.write_text(json.dumps(rec))
needs2 = RL.needs_you_of(rows)
T.check("OK3 the needs-you bound to another task still waiting, the one without a task and the one written after the ok stay open",
        "beta" in needs2 and "gamma" in needs2 and "pix-my-alfa" in needs2 and "op-altro" in [a["task"] for a in RL.approvals_list()], f"{sorted(needs2)}")

T.rm(tmp)
T.finish()
