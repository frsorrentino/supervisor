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
# da un terminale: fuori da una sessione Claude (CLAUDECODE) e da tmux; le prove T9 li rimettono apposta
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV["CM_TASKS_DB"] = str(tmp / "tasks.db")
CLI = str(T.SCRIPTS / "supervisor")


def cm(*args, stdin=None, env=None):
    p = subprocess.run([CLI, "task", *args], input=stdin, capture_output=True, text=True, env=dict(ENV, **(env or {})), timeout=60)
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

# T10 (16/10/2026, the maintainer, «Sì, tutte e due»): l'ok a nome di una persona da una sessione Claude solo dalla master,
# citando un messaggio del relay dell'ultima ora; e la chiusura dei compiti verdi aperti da piu' di 6 ore
import sqlite3  # noqa: E402
import time  # noqa: E402
state9 = tmp / "state"
cfg9 = tmp / "config.json"
cfg9.write_text(json.dumps({"language": "en", "state_dir": str(state9), "workspace": {"root_session_name": "master"}}))
add(contract("ok9"))
cm("wait-ok", "ok9", "--what", "release", "--where", "github")
CC = {"CLAUDECODE": "1", "CC_SUPERVISOR_CONFIG": str(cfg9)}
rc, out, err = cm("approve", "ok9", "--by", "maintainer", "--text", "ok", env=CC)
T.check("T10 from a Claude session, --text alone → refused, it asks for --inbox; nothing recorded",
        rc != 0 and "--inbox" in err and show("ok9")["state"] == "awaiting_ok" and all(a["by_"] is None for a in show("ok9")["approvals"]), err)
rc, out, err = cm("approve", "ok9", "--by", "app", "--text", "ok (from the phone)", env=dict(CC, CM_APPROVE_VIA="relay"))
T.check("T10 the relay (CM_APPROVE_VIA=relay, it inherits CLAUDECODE) still records the device's ok", rc == 0 and show("ok9")["state"] == "approved", err)


def inbox_put(to, text, origin, age_s=0):
    code = ("import importlib.util,sys,json,os;s=importlib.util.spec_from_file_location('i',sys.argv[1]);I=importlib.util.module_from_spec(s);"
            "s.loader.exec_module(I);r=I.put(sys.argv[2],sys.argv[3],'supervisor',origin=sys.argv[4]);"
            "p=I._path_of(r['id']);d=json.loads(p.read_text());d['created']-=float(sys.argv[5]);p.write_text(json.dumps(d));print(r['id'])")
    return subprocess.run([sys.executable, "-c", code, str(T.SCRIPTS / "cm-inbox.py"), to, text, origin, str(age_s)],
                          capture_output=True, text=True, env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg9)), timeout=30).stdout.strip()


with T.PrivateTmux() as tm:
    tm("new-session", "-d", "-s", "master", "sleep 300")
    tm("new-session", "-d", "-s", "altra", "sleep 300")
    pane_m = tm("display-message", "-p", "-t", "master", "#{pane_id}").stdout.strip()
    pane_a = tm("display-message", "-p", "-t", "altra", "#{pane_id}").stdout.strip()
    M9 = dict(CC, CM_TMUX_ARGS=tm.env["CM_TMUX_ARGS"], TMUX_PANE=pane_m)
    good = inbox_put("master", "Dall'utente via telefono. Sì, tutte e due", "relay")
    for tid in ("i1", "i2", "i3", "i4", "i5"):
        add(contract(tid))
        cm("wait-ok", tid, "--what", "release", "--where", "github")
    rc, out, err = cm("approve", "i1", "--by", "maintainer", "--inbox", good, "--text", "release after the diff", env=M9)
    a = show("i1")["approvals"][-1]
    T.check("T10 --inbox from the master, a relay message of now → approved; the text quotes the message, with its id and the note",
            rc == 0 and show("i1")["state"] == "approved" and a["by_"] == "maintainer" and "«Dall'utente via telefono. Sì, tutte e due»" in a["text"]
            and f"inbox {good}" in a["text"] and a["text"].endswith("— release after the diff"), err + json.dumps(a))
    rc, out, err = cm("approve", "i2", "--by", "maintainer", "--inbox", good, env=dict(M9, TMUX_PANE=pane_a))
    T.check("T10 --inbox from another session → refused (only the master)", rc != 0 and "only the master" in err and show("i2")["state"] == "awaiting_ok", err)
    sess = inbox_put("master", "ok scritto da una sessione", "session")
    rc, out, err = cm("approve", "i3", "--by", "maintainer", "--inbox", sess, env=M9)
    T.check("T10 --inbox of a message with origin session → refused", rc != 0 and "did not come from the relay" in err and show("i3")["state"] == "awaiting_ok", err)
    old = inbox_put("master", "Dall'utente via telefono. ok di ieri", "relay", age_s=2 * 3600)
    rc, out, err = cm("approve", "i4", "--by", "maintainer", "--inbox", old, env=M9)
    T.check("T10 --inbox of a relay message older than an hour → refused", rc != 0 and "minutes old" in err and show("i4")["state"] == "awaiting_ok", err)
    rc, out, err = cm("approve", "i5", "--by", "maintainer", "--inbox", "ffffff", env=M9)
    T.check("T10 --inbox of a message that does not exist → refused", rc != 0 and "no message ffffff" in err, err)

(proj / "ok.txt").write_text("x")
add(contract("ac_green"))
add(contract("ac_red", cmd="false"))
add(contract("ac_new"))
cm("start", "ac_green")
con9 = sqlite3.connect(str(tmp / "tasks.db"))
con9.execute("UPDATE tasks SET created_at=? WHERE id IN ('ac_green', 'ac_red')", (int(time.time()) - 7 * 3600,))
con9.commit()
con9.close()
rc, out, err = cm("auto-close", "--json")
res = {r["task"]: r["green"] for r in json.loads(out or "[]")}
T.check("T10 auto-close: open for more than 6 hours with a green check → done; a red one stays open with no attempt counted; a new one is not checked",
        rc == 0 and show("ac_green")["state"] == "done" and show("ac_red")["state"] == "proposed" and not show("ac_red")["results"]
        and "ac_new" not in res and show("ac_new")["state"] == "proposed" and res.get("ac_green") is True and res.get("ac_red") is False, out + err)

T.finish()
