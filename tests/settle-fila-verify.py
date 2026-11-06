#!/usr/bin/env python3
"""I controlli dopo un ok in blocco delle prove (06/11/2026, supervisor-settle-in-fila-06-11). Registro temporaneo.

SF1 dodici `task settle` lanciati insieme (come dopo un ok in blocco): ogni compito ha il suo esito (tutti chiusi), e i
    controlli girano uno alla volta (nessuna sovrapposizione negli istanti di inizio e fine che scrivono)
SF2 un controllo che scade: si ferma anche il processo figlio della shell (un `git fetch` appeso), non solo la shell;
    exit 124 con il motivo
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
home.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"p": {"config_dir": str(home / ".claude")}},
                           "default_account": "p", "workspace": {"root": str(tmp), "root_session_name": "master"}}))
fake = tmp / "fake-cm"
fake.write_text("#!/bin/sh\nexit 0\n")
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update({"CM_TASKS_DB": str(tmp / "t.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASK_TALK_BIN": str(fake)})
os.environ.clear()
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")
log = tmp / "runs.log"


def cm(*a, stdin=None):
    return subprocess.run([CLI, "task", *a], input=stdin, capture_output=True, text=True, env=ENV, timeout=300)


N = 12
for i in range(N):
    check = f"echo s {i} $(date +%s.%N) >> {log}; sleep 0.4; echo e {i} $(date +%s.%N) >> {log}"
    c = {"schema_version": 1, "task": {"id": f"sf-{i}", "title": f"sf {i}", "plan": "p", "where": {"project": str(tmp), "session": "s", "host": "local", "account": None},
                                       "check": {"cmd": check, "cwd": str(tmp), "timeout_s": 60}, "perimeter": ["x"], "lane": "open", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
                                       "outcome": {"expected": "x", "kind": "custom", "proof": "x"}}}
    assert cm("add", "-", stdin=json.dumps(c)).returncode == 0
    cm("prova", f"sf-{i}", "--build", "b1", "--dove", "home", "--fai", "tocca", "--atteso", "si apre")
sp = importlib.util.spec_from_file_location("cm_tasks_sf", T.SCRIPTS / "cm-tasks.py")
TK = importlib.util.module_from_spec(sp)
sp.loader.exec_module(TK)
con = TK.connect()
res = TK.prove_mark(con, [r[0] for r in con.execute("SELECT n FROM prove")], "ok", "maintainer", "phone")
con.close()
ps = [subprocess.Popen([CLI, "task", "settle", t], env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for t in res["complete"]]
for p in ps:
    p.communicate(timeout=300)
states = {json.loads(cm("show", f"sf-{i}", "--json").stdout)["state"] for i in range(N)}
ev = sorted((float(t), k, i) for k, i, t in (ln.split() for ln in log.read_text().splitlines()))
depth, worst = 0, 0
for _, k, _ in ev:
    depth += 1 if k == "s" else -1
    worst = max(worst, depth)
T.check("SF1 twelve settles at once: every task has its outcome (all closed green), the checks run one at a time",
        len(res["complete"]) == N and states == {"done"} and worst == 1 and len(ev) == 2 * N, f"{states} overlap {worst} events {len(ev)}")

# SF2
mark = f"{time.time():.0f}.123"
t0 = time.time()
rc, tail = TK.run_check({"check": {"cmd": f"sleep {mark}; true", "cwd": str(tmp), "timeout_s": 2}, "where": {"project": str(tmp)}})
time.sleep(0.5)
left = subprocess.run(["pgrep", "-f", f"sleep {mark}"], capture_output=True, text=True).stdout.strip()
for pid in left.split():   # un orfano rimasto (codice vecchio): via, o resterebbe per sempre
    try:
        os.kill(int(pid), 9)
    except (OSError, ValueError):
        pass
T.check("SF2 a check that times out: exit 124 with the reason, and the shell's child is stopped too (no orphan left holding the repository)",
        rc == 124 and "timeout after 2 s" in tail and time.time() - t0 < 6 and not left, f"{rc} {tail} left={left}")

T.rm(tmp)
T.finish()
