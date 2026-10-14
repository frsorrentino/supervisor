#!/usr/bin/env python3
"""Registro dei compiti (fase 1, 03/10/2026): contratto validato, accettazione dal codice, tentativi, vincoli,
approvazioni, board. Tutto su un tasks.db temporaneo (CM_TASKS_DB): il registro vero non si tocca."""
import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="cm-tasks-"))
proj = tmp / "proj"
proj.mkdir()
ENV = dict(os.environ, CM_TASKS_DB=str(tmp / "tasks.db"))
CLI = str(T.SCRIPTS / "supervisor")


def cm(*args, stdin=None):
    p = subprocess.run([CLI, "task", *args], input=stdin, capture_output=True, text=True, env=ENV, timeout=60)
    return p.returncode, p.stdout, p.stderr


def contract(tid, cmd="test -f ok.txt", **over):
    t = {"id": tid, "title": f"unit {tid}", "plan": None,
         "where": {"project": str(proj), "session": None, "host": "local", "account": None},
         "check": {"cmd": cmd, "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open",
         "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal",
         "requested_by": "test"}
    t.update(over)
    return {"schema_version": 1, "task": t}


def add(c, *extra):
    return cm("add", "-", *extra, stdin=json.dumps(c))


def show(tid):
    return json.loads(cm("show", tid, "--json")[1])


# T1 il contratto: valido entra, gli altri no e con l'errore detto
rc, out, err = add(contract("t1"))
T.check("T1 a valid contract enters the registry (exit 0, its id), state proposed with no session", rc == 0 and out.strip() == "t1" and show("t1")["state"] == "proposed", out + err)
bad = contract("t2")
del bad["task"]["check"]
bad["task"]["lane"] = "fast"
bad["task"]["extra"] = 1
rc, out, err = add(bad)
T.check("T1 no check, an unknown lane, an extra field → exit 2, each said, nothing stored",
        rc == 2 and "$.task.check: required" in err and "$.task.lane: one of open, wide, closed" in err and "$.task.extra: not allowed" in err
        and cm("list", "--json")[1].count('"id"') == 1, err)
rc, out, err = add(contract("t1"))
T.check("T1 the same id twice → refused", rc == 2 and "already in the registry" in err, err)
dep = contract("t3", depends_on=[{"task": "t1"}])
T.check("T1 an edge must name what passes (depends_on[].passes)", add(dep)[0] == 2, "")

# T2 accettazione dal codice: il controllo rieseguito, verde = fatto
add(contract("g1", where={"project": str(proj), "session": "cm-a", "host": "local", "account": None}))
T.check("T2 a task with a session starts assigned", show("g1")["state"] == "assigned", "")
cm("start", "g1")
(proj / "ok.txt").write_text("x")
rc, out, _ = cm("done", "g1")
s = show("g1")
T.check("T2 done reruns the check: green → exit 0, state done, an accepted result with the command, its output tail and exit 0 as proof",
        rc == 0 and s["state"] == "done" and s["results"][-1]["outcome"] == "green" and s["results"][-1]["accepted"] == 1
        and s["results"][-1]["proof"]["kind"] == "check" and "(exit 0)" in s["results"][-1]["proof"]["value"] and "test -f ok.txt" in s["results"][-1]["proof"]["value"], out + json.dumps(s)[:300])
rc, _, err = cm("done", "g1")
T.check("T2 a finished task cannot be done again", rc != 0 and "already done" in err, err)

# T3 rosso: un tentativo per volta, al terzo «fallito» e un vincolo per il progetto
add(contract("r1", cmd="echo 'missing: docs/x.md' >&2; exit 3", attempts_max=3))
cm("start", "r1")
rcs = [cm("done", "r1")[0] for _ in range(2)]
mid = show("r1")["state"]
rc3, out3, _ = cm("done", "r1")
s = show("r1")
T.check("T3 red → exit 1 and the task stays open until attempts_max; at the third red it is failed",
        rcs == [1, 1] and mid == "running" and rc3 == 1 and s["state"] == "failed" and [r["attempt"] for r in s["results"]] == [1, 2, 3], str(rcs) + mid + json.dumps(s["results"])[:300])
T.check("T3 the red result says why: exit code and the last line of the output", s["results"][-1]["reason"] == "check exit 3: missing: docs/x.md", s["results"][-1]["reason"])

# T4 arco di apprendimento: il fallito lascia un vincolo che il compito dopo, nello stesso progetto, porta con se'
cm("constraint", "add", "--topic", "windows", "use PYTHONUTF8=1 and pwd -W")
add(contract("n1"), "--topic", "windows")
cons = show("n1")["contract"]["task"].get("constraints") or []
T.check("T4 a new task of the same project carries the constraint left by the failed one, and the one of its topic",
        any("unit r1: check exit 3" in c for c in cons) and "use PYTHONUTF8=1 and pwd -W" in cons, str(cons))
other = contract("n2", where={"project": str(tmp), "session": None, "host": "local", "account": None})
add(other)
T.check("T4 a task of another project and no topic carries none", "constraints" not in show("n2")["contract"]["task"], "")
cm("start", "n1")
cm("done", "n1", "--constraint", "the bench runs outside the repo")
add(contract("n3"))
T.check("T4 done --constraint adds one by hand, attached to the next task", "the bench runs outside the repo" in show("n3")["contract"]["task"]["constraints"], "")

# T5 l'esito della sessione si registra ma non accetta niente
res = {"task": "n3", "attempt": 1, "outcome": "green", "reason": "suite green", "proof": {"kind": "commit", "value": "abc1234"}, "at": 1}
rc, _, err = cm("result", "n3", "-", stdin=json.dumps(res))
s = show("n3")
T.check("T5 result from the session: stored (accepted null), the state does not move", rc == 0 and s["state"] == "proposed" and s["results"][0]["accepted"] is None, err + json.dumps(s)[:200])
rc, _, err = cm("result", "n3", "-", stdin=json.dumps(dict(res, outcome="maybe")))
T.check("T5 a result outside the contract is refused", rc == 2 and "$.outcome: one of green, red" in err, err)

# T6 corsia chiusa fuori da un piano: attende ok, poi approvato con chi, cosa, dove
add(contract("p1", lane="closed"))
cm("wait-ok", "p1", "--what", "release 0.5.12", "--where", "github frsorrentino/supervisor")
st1 = show("p1")["state"]
_l6 = json.loads(cm("list", "--state", "awaiting_ok", "--json")[1])
T.check("T6 (contract 1.37) list --json carries the ok request of a task waiting for it: {what, where, at}",
        [(x["id"], x["request"]["what"], x["request"]["where"], isinstance(x["request"]["at"], int)) for x in _l6] == [("p1", "release 0.5.12", "github frsorrentino/supervisor", True)], json.dumps(_l6))
cm("approve", "p1", "--by", "maintainer (phone)", "--text", "ok", "--what", "release 0.5.12", "--where", "github frsorrentino/supervisor")
s = show("p1")
T.check("T6 wait-ok → awaiting_ok; approve → approved, with who, text, what and where recorded",
        st1 == "awaiting_ok" and s["state"] == "approved" and s["approvals"][-1]["by_"] == "maintainer (phone)" and s["approvals"][-1]["where_"] == "github frsorrentino/supervisor", json.dumps(s["approvals"]))

# T7 board dal codice
b = json.loads(cm("board", "--json")[1])
T.check("T7 board --json: counts per state and the failed task with its last result",
        b["counts"]["done"] == 2 and b["counts"]["failed"] == 1 and b["failed"][0]["id"] == "r1" and b["failed"][0]["last"]["outcome"] == "red", json.dumps(b["counts"]))
T.check("T7 board as text names the failed task and why", "failed      r1  unit r1  [red #3: check exit 3: missing: docs/x.md]" in cm("board")[1], cm("board")[1])

# T8 lo schema e' valido per se stesso: l'esempio di ritorno e quello d'andata completi passano
full = contract("z1", constraints=["c"])
full["result"] = {"task": "z1", "attempt": 1, "outcome": "red", "reason": "r", "proof": {"kind": "check", "value": "v", "rc": 1}, "perimeter": ["a.py"], "at": 1}
full["measures"] = {"cost_eq": 1.5, "turns": 3, "attempts": 1, "tokens_in": None, "tokens_out": None, "model": None}
spec = __import__("importlib.util").util.spec_from_file_location("cm_tasks", T.SCRIPTS / "cm-tasks.py")
mod = __import__("importlib.util").util.module_from_spec(spec)
spec.loader.exec_module(mod)
T.check("T8 a full contract (task, result, measures) is valid; a wrong schema_version is not",
        mod.validate(full) == [] and mod.validate(dict(full, schema_version=2)) != [], str(mod.validate(full)))

# T10 (14/10, spunti della valutazione di Kaya): `task check` riesegue il controllo senza cambiare niente
add(contract("t10"))
cm("start", "t10")
(proj / "ok.txt").unlink(missing_ok=True)
rc_r, out_r, _ = cm("check", "t10", "--json")
(proj / "ok.txt").write_text("x")
rc_g, out_g, _ = cm("check", "t10")
s10 = show("t10")
T.check("T10 task check: red → exit 1 with JSON {task, green false, rc, tail, title}; green → exit 0; the state stays running, no result, no attempt counted",
        rc_r == 1 and json.loads(out_r)["green"] is False and json.loads(out_r)["title"] == "unit t10" and rc_g == 0 and "green" in out_g
        and s10["state"] == "running" and s10["results"] == [], out_r + out_g + str(s10["state"]))

# T9 lo schema e' lo stesso di fable-director, byte per byte (saltato se il repo o la copia non ci sono)
fd = Path.home() / "Desktop/workspaces/personali/fable-director/fable-director-marketplace/fable-director/schemas/task-contract.v1.json"
if fd.exists():
    T.check("T9 schemas/task-contract.v1.json identical to fable-director's copy", fd.read_bytes() == (T.PLUGIN / "schemas" / "task-contract.v1.json").read_bytes(), str(fd))
else:
    print("  skip T9 (fable-director's copy of the schema not found)")

T.finish()
