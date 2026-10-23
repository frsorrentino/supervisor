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
    T.check("T10 --inbox of a message with origin session → refused", rc != 0 and "did not come from the person" in err and show("i3")["state"] == "awaiting_ok", err)
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

# T11 (22/10/2026, P1 — the day lost on the Gantt APK): the expected outcome in the contract, and a check that touches it
oc = contract("o1", cmd="test -f ok.txt", outcome={"expected": "release 1.0 su GitHub", "kind": "release", "proof": None})
rc, out, err = add(oc)
T.check("T11 outcome release with a check that does not touch it → exit 2, the markers named, nothing stored",
        rc == 2 and "does not touch the outcome (kind release)" in err and "gh release" in err and cm("show", "o1")[0] != 0, err)
ok = contract("o2", cmd="git tag --list | grep -q v1.0", outcome={"expected": "release 1.0 su GitHub", "kind": "release"})
T.check("T11 a check with `git tag` enters", add(ok)[0] == 0 and show("o2")["contract"]["task"]["outcome"]["kind"] == "release", "")
T.check("T11 custom needs a proof", add(contract("o3", outcome={"expected": "x", "kind": "custom"}))[0] == 2
        and add(contract("o4", outcome={"expected": "x", "kind": "custom", "proof": "il maintainer lo dice"}))[0] == 0, "")
con11 = sqlite3.connect(str(tmp / "tasks.db"))   # un compito entrato prima della regola: il contratto cambiato nel DB
c11 = json.loads(con11.execute("SELECT contract FROM tasks WHERE id='o2'").fetchone()[0])
c11["task"]["check"]["cmd"] = "test -f ok.txt"
con11.execute("UPDATE tasks SET contract=?, state='running' WHERE id='o2'", (json.dumps(c11),)); con11.commit(); con11.close()
rc, out, err = cm("done", "o2")
T.check("T11 done refuses a check that does not touch the outcome: exit 3, state unchanged, no result, no attempt",
        rc == 3 and "does not touch the outcome" in err and show("o2")["state"] == "running" and show("o2")["results"] == [], err)
rc, out, err = cm("auto-close", "--json")
T.check("T11 auto-close skips it (never closes on an intermediate step)", "o2" not in [r["task"] for r in json.loads(out or "[]")] and show("o2")["state"] == "running", out + err)
# T12 amend: il controllo corretto, poi done verde; un passo «amend» registrato
rc, out, err = cm("amend", "o2", "--check", "git tag --list >/dev/null; test -f ok.txt", "--by", "master")
s12 = show("o2")
T.check("T12 amend --check rewrites the check (validated again) and records an amend step", rc == 0 and s12["contract"]["task"]["check"]["cmd"].startswith("git tag")
        and s12["steps"][-1]["kind"] == "amend" and s12["steps"][-1]["by_"] == "master", err + json.dumps(s12.get("steps")))
T.check("T12 amend --check that does not touch the outcome → exit 2, contract unchanged", cm("amend", "o2", "--check", "true")[0] == 2
        and show("o2")["contract"]["task"]["check"]["cmd"].startswith("git tag"), "")
rc, out, err = cm("amend", "o2", "--outcome", "la 1.0 è installata", "--kind", "install")
T.check("T12 amend --outcome with a kind the check does not touch → exit 2", rc == 2 and show("o2")["contract"]["task"]["outcome"]["kind"] == "release", err)
T.check("T12 amend --timeout that is not a number → refused", cm("amend", "o2", "--timeout", "x")[0] != 0, "")
T.check("T12 done after the amend: green", cm("done", "o2")[0] == 0 and show("o2")["state"] == "done", "")
T.check("T12 amend on a finished task → refused", cm("amend", "o2", "--check", "git tag")[0] != 0, "")
# T13 confirm / confirmed: la conferma della persona come controllo di un esito «install»
add(contract("c1", cmd="supervisor task confirmed c1", outcome={"expected": "APK sul telefono", "kind": "install"}))
T.check("T13 confirmed before any confirmation → exit 1", cm("confirmed", "c1")[0] == 1, "")
rc, _, err = cm("confirm", "c1", "--by", "maintainer", "--text", "lo vedo", env=CC)
T.check("T13 confirm from a Claude session needs --inbox, as approve", rc != 0 and "--inbox" in err, err)
rc, _, err = cm("confirm", "c1", "--by", "maintainer", "--text", "lo vedo (dal telefono)", env=dict(CC, CM_APPROVE_VIA="relay"))
T.check("T13 confirm from the relay → recorded (what = confirmed), the state does not move; confirmed → exit 0", rc == 0 and cm("confirmed", "c1")[0] == 0
        and show("c1")["approvals"][-1]["what"] == "confirmed" and show("c1")["state"] == "proposed", err)

# T14 (22/10/2026, P2): gli impegni = aperti e approvati (ok o piano), piu' quelli in attesa di ok; eta', passi, prossimo, chi aspetta chi, pausa
cfg14 = tmp / "config-it.json"
cfg14.write_text(json.dumps({"language": "it", "state_dir": str(state9), "workspace": {"root_session_name": "master"}}))
IT = {"CC_SUPERVISOR_CONFIG": str(cfg14)}
r14 = json.loads(cm("impegni", "--json", env=IT)[1])
ids14 = {x["task"] for x in r14}
T.check("T14 impegni --json lists only open tasks with an ok or an approved plan, and the awaiting_ok ones (approved false); a confirmation alone is not an ok",
        ids14 >= {"i1", "p1", "ok9", "i2", "i3", "i4", "i5"} and not ids14 & {"t1", "n2", "n3", "ac_new", "ac_red", "o4", "c1", "g1", "o2"}
        and next(x for x in r14 if x["task"] == "i2")["approved"] is False and next(x for x in r14 if x["task"] == "i2")["waits"] == {"who": "master", "on": "person"},
        str([(x["task"], x["state"], x["approved"]) for x in r14]))
x14 = next(x for x in r14 if x["task"] == "i1")
T.check("T14 an approved task: approved_at/by, age_s, last_step = the approval (it beats the state change of the same second), waits person→session, next from the state, stalled false (just approved)",
        x14["approved_by"] == "maintainer" and isinstance(x14["approved_at"], int) and x14["age_s"] >= 0 and x14["last_step"]["kind"] == "approval"
        and x14["waits"] == {"who": "person", "on": "master"} and "task done i1" in x14["next"] and x14["stalled"] is False and x14["paused"] is None, json.dumps(x14))
rc, out, _ = cm("step", "i1", "test verdi", "--next", "release 0.9", env=IT)
r14b = {x["task"]: x for x in json.loads(cm("impegni", "--json", env=IT)[1])}
T.check("T14 task step: the last step and next come from it", rc == 0 and r14b["i1"]["last_step"]["text"] == "test verdi" and r14b["i1"]["last_step"]["kind"] == "step"
        and r14b["i1"]["next"] == "release 0.9", json.dumps(r14b["i1"]))
cm("pause", "i1", "--reason", "aspetta la CI", "--by", "master", env=IT)
r14c = {x["task"]: x for x in json.loads(cm("impegni", "--json", env=IT)[1])}
con14 = sqlite3.connect(str(tmp / "tasks.db"))
con14.execute("UPDATE approvals SET at=? WHERE task='ok9'", (int(time.time()) - 3 * 3600,))
con14.execute("UPDATE tasks SET updated_at=? WHERE id='ok9'", (int(time.time()) - 3 * 3600,))
con14.commit(); con14.close()
r14d = {x["task"]: x for x in json.loads(cm("impegni", "--json", env=IT)[1])}
T.check("T14 pause: visible with its reason and who, never stalled; an approved task idle for 3 hours is stalled and comes first",
        r14c["i1"]["paused"]["reason"] == "aspetta la CI" and r14c["i1"]["paused"]["by"] == "master" and r14c["i1"]["stalled"] is False
        and r14d["ok9"]["stalled"] is True and r14d["ok9"]["idle_s"] >= 3 * 3600 and list(r14d)[0] == "ok9" and r14d["i1"]["stalled"] is False, json.dumps(r14d["ok9"]))
T.check("T14 resume clears the pause", cm("resume", "i1", env=IT)[0] == 0 and {x["task"]: x for x in json.loads(cm("impegni", "--json", env=IT)[1])}["i1"]["paused"] is None, "")
T.check("T14 an awaiting_ok task is never stalled (it waits for the person): i5 with a 3-hour-old request",
        all(x["stalled"] is False for x in r14d.values() if x["state"] == "awaiting_ok"), "")
# T15 il testo: intestazione con i conteggi, una riga per voce, --brief taglia, --stalled filtra
txt = cm("impegni", env=IT)[1]
T.check("T15 text: «Impegni (N, K fermi)», one line per item with id, session, state, ok time, idle, next, who waits for whom; the paused one carries PAUSA",
        txt.startswith("Impegni (") and " fermi):" in txt.splitlines()[0] and any(l.startswith("- ok9 · master · approved · ok ") and "fermo da 3 h" in l for l in txt.splitlines())
        and "prossimo:" in txt and "· tocca a master" in txt and any("- i2 · master · awaiting_ok · chiesto" in l and l.endswith("· aspetta l'utente") for l in txt.splitlines()), txt[:600])
cm("pause", "p1", "--reason", "dopo la 0.9", env=IT)
T.check("T15 PAUSA: reason on the paused line", any("- p1 ·" in l and "PAUSA: dopo la 0.9" in l for l in cm("impegni", env=IT)[1].splitlines()), cm("impegni", env=IT)[1])
for i in range(9):
    add(contract(f"m{i}", lane="closed"))
    cm("wait-ok", f"m{i}", "--what", "x", "--where", "y")
brief = cm("impegni", "--brief", env=IT)[1]
full = cm("impegni", env=IT)[1]
T.check("T15 --brief: the header, 8 items, then «… e altri N»; the full text has them all", len(brief.splitlines()) == 10 and brief.splitlines()[-1].startswith("… e altri ")
        and len(full.splitlines()) == 1 + len(json.loads(cm("impegni", "--json", env=IT)[1])), brief)
T.check("T15 --stalled: only the stalled ones", [x["task"] for x in json.loads(cm("impegni", "--json", "--stalled", env=IT)[1])] == ["ok9"], "")
en15 = cm("impegni", env={"CC_SUPERVISOR_CONFIG": str(cfg9)})[1]
T.check("T15 English config: «Commitments (N, K stalled)»", en15.startswith("Commitments (") and "idle for" in en15 and "master's turn" in en15 and "waits for the user" in en15, en15[:200])
# T16 (P3): stall-check stampa i fermi una volta per passo; un passo nuovo riarma
A16 = dict(IT, CM_TASKS_ALERTS=str(tmp / "alerts.json"))
a1 = json.loads(cm("stall-check", "--json", env=A16)[1])
a2 = json.loads(cm("stall-check", "--json", env=A16)[1])
cm("step", "ok9", "ripreso", env=A16)
a3 = json.loads(cm("stall-check", "--json", env=A16)[1])
con16 = sqlite3.connect(str(tmp / "tasks.db"))
con16.execute("UPDATE steps SET at=? WHERE task='ok9'", (int(time.time()) - 3 * 3600,)); con16.execute("UPDATE tasks SET updated_at=? WHERE id='ok9'", (int(time.time()) - 3 * 3600,))
con16.commit(); con16.close()
a4 = json.loads(cm("stall-check", "--json", env=A16)[1])
T.check("T16 stall-check: the stalled task once (alerts file), nothing the second time, nothing after a fresh step, again when that step is 3 hours old",
        [x["task"] for x in a1] == ["ok9"] and a2 == [] and a3 == [] and [x["task"] for x in a4] == ["ok9"] and json.loads((tmp / "alerts.json").read_text()) == {"ok9": a4[0]["last_step"]["at"]},
        str((a1, a2, a3, a4))[:400])

# T17 (22/10/2026, contratto 1.77): una pausa che aspetta la persona (--waits person) e l'inizio vero (--since)
cm("pause", "ok9", "--reason", "aspetta la casella Aruba", "--waits", "person", "--since", "2026-10-18T09:00", "--by", "master", env=IT)
r17 = {x["task"]: x for x in json.loads(cm("impegni", "--json", env=IT)[1])}
t17 = time.mktime(time.strptime("2026-10-18T09:00", "%Y-%m-%dT%H:%M"))
T.check("T17 pause --waits person --since: waits = {who: session, on: person}, paused.at = the real start, never stalled, the line says «aspetta l'utente» with PAUSA",
        r17["ok9"]["waits"] == {"who": "master", "on": "person"} and r17["ok9"]["paused"]["at"] == int(t17) and r17["ok9"]["paused"]["reason"] == "aspetta la casella Aruba"
        and r17["ok9"]["stalled"] is False and any("- ok9 ·" in l and "aspetta l'utente" in l and "PAUSA: aspetta la casella Aruba" in l for l in cm("impegni", env=IT)[1].splitlines())
        and "ok9" not in [x["task"] for x in json.loads(cm("stall-check", "--json", env=A16)[1])], json.dumps(r17["ok9"]))
T.check("T17 resume brings waits back to the session's turn", cm("resume", "ok9", env=IT)[0] == 0 and {x["task"]: x for x in json.loads(cm("impegni", "--json", env=IT)[1])}["ok9"]["waits"] == {"who": "person", "on": "master"}, "")
T.check("T17 --waits takes only person; --since wants an epoch or an ISO time", cm("pause", "ok9", "--reason", "x", "--waits", "nobody")[0] != 0 and cm("pause", "ok9", "--reason", "x", "--since", "ieri")[0] != 0, "")
cm("pause", "ok9", "--reason", "pausa normale", "--since", "1700000000", env=IT)
T.check("T17 --since as an epoch; a pause without --waits leaves waits as it was", {x["task"]: x for x in json.loads(cm("impegni", "--json", env=IT)[1])}["ok9"]["paused"]["at"] == 1700000000
        and {x["task"]: x for x in json.loads(cm("impegni", "--json", env=IT)[1])}["ok9"]["waits"]["on"] == "master", "")

# T18 (1.77): task closed — i compiti chiusi per giorno con la prova (release, commit, file, controllo) e i giorni prima
add(contract("z2", cmd="git fetch -q --tags origin && git tag -l v1.2.3 | grep -q '^v1.2.3$'", outcome={"expected": "la 1.2.3 pubblicata", "kind": "release"}))
add(contract("z3", cmd="test -f ok.txt", outcome={"expected": "il commit 9f8e7d6 pushato", "kind": "custom", "proof": "git log"}))
add(contract("z4", cmd="test -f ok.txt", outcome={"expected": "docs/notte/2026-10-22-prova.md con FINE", "kind": "custom", "proof": "il file"}))
add(contract("z5", cmd='R=$(gh release view impegni-2026-10-22-b --json tagName -q .tagName) && test -n "$R"', outcome={"expected": "la pre-release installata", "kind": "release"}))
for z in ("z2", "z3", "z4", "z5"):
    cm("cancel", z)
c18 = json.loads(cm("closed", "--json", env=IT)[1])
rows18 = {x["task"]: x for x in c18["rows"]}
T.check("T18 closed --json: {date (today), rows (newest first, outcome green|cancelled|failed, at, session, proof), days, more false}",
        c18["date"] == time.strftime("%Y-%m-%d") and c18["more"] is False and [x["at"] for x in c18["rows"]] == sorted((x["at"] for x in c18["rows"]), reverse=True)
        and rows18["g1"]["outcome"] == "green" and rows18["g1"]["session"] == "cm-a" and rows18["r1"]["outcome"] == "failed" and rows18["z2"]["outcome"] == "cancelled"
        and list(rows18["g1"]) == ["task", "title", "short", "project", "session", "at", "outcome", "proof"], json.dumps(c18)[:400])
T.check("T18 the proof: release from the check's tag; commit from the outcome (short hash, no url without a GitHub remote); file from the outcome; else the check's first line",
        rows18["z2"]["proof"] == {"kind": "release", "value": "v1.2.3", "url": None} and rows18["z5"]["proof"] == {"kind": "release", "value": "impegni-2026-10-22-b", "url": None} and rows18["z3"]["proof"] == {"kind": "commit", "value": "9f8e7d6", "url": None}
        and rows18["z4"]["proof"] == {"kind": "file", "value": "docs/notte/2026-10-22-prova.md", "url": None} and rows18["g1"]["proof"] == {"kind": "check", "value": "test -f ok.txt", "url": None}, json.dumps({k: v["proof"] for k, v in rows18.items()}))
con18 = sqlite3.connect(str(tmp / "tasks.db"))
y18 = int(time.time()) - 86400
con18.execute("UPDATE results SET at=? WHERE task='n1' AND accepted=1", (y18,)); con18.execute("UPDATE tasks SET updated_at=? WHERE id='z4'", (y18 - 86400,)); con18.commit(); con18.close()
c18b = json.loads(cm("closed", "--json", env=IT)[1])
T.check("T18 days: the days before with at least one closed task, newest first, with the count; a day asked for explicitly lists its rows",
        [(d["date"], d["n"]) for d in c18b["days"]] == [(time.strftime("%Y-%m-%d", time.localtime(y18)), 1), (time.strftime("%Y-%m-%d", time.localtime(y18 - 86400)), 1)]
        and [x["task"] for x in json.loads(cm("closed", time.strftime("%Y-%m-%d", time.localtime(y18)), "--json", env=IT)[1])["rows"]] == ["n1"], json.dumps(c18b["days"]))
T.check("T18 a bad date → usage; no registry → exit 3 «no task registry» and no database created",
        cm("closed", "ieri")[0] != 0 and cm("closed", "--json", env={"CM_TASKS_DB": str(tmp / "no" / "tasks.db")})[0] == 3 and not (tmp / "no" / "tasks.db").exists(), "")

# T19 (22/10/2026, ok-in-sessione): l'ok scritto dalla persona nella sessione del compito vale; i testi di altre sessioni no
with T.PrivateTmux() as tm:
    tm("new-session", "-d", "-s", "master", "sleep 300")
    tm("new-session", "-d", "-s", "altra", "sleep 300")
    pane_m = tm("display-message", "-p", "-t", "master", "#{pane_id}").stdout.strip()
    pane_a = tm("display-message", "-p", "-t", "altra", "#{pane_id}").stdout.strip()
    M19 = dict(CC, CM_TMUX_ARGS=tm.env["CM_TMUX_ARGS"], TMUX_PANE=pane_m)
    A19 = dict(M19, TMUX_PANE=pane_a)
    for tid, sess in (("s1", "altra"), ("s2", "altra"), ("s3", "altra"), ("s4", None), ("s5", "altra")):
        add(contract(tid, where={"project": str(proj), "session": sess, "host": "local", "account": None}))
        cm("wait-ok", tid, "--what", "push", "--where", "github")
    term = inbox_put("altra", "ok, pubblica pure", "terminal")
    rc, out, err = cm("approve", "s1", "--by", "maintainer", "--inbox", term, env=A19)
    a19 = show("s1")["approvals"][-1]
    T.check("T19 from the task's session, a message the person typed there (origin terminal) → approved; the text quotes it with id, origin and recipient",
            rc == 0 and show("s1")["state"] == "approved" and "«ok, pubblica pure»" in a19["text"] and f"inbox {term}, origin terminal, to altra" in a19["text"], err + json.dumps(a19))
    rc, out, err = cm("approve", "s2", "--by", "maintainer", "--inbox", term, env=M19)
    T.check("T19 the master cannot use a message addressed to another session", rc != 0 and "is for altra, not for master" in err and show("s2")["state"] == "awaiting_ok", err)
    sess19 = inbox_put("altra", "ok dalla master via talk", "session")
    rc, out, err = cm("approve", "s3", "--by", "maintainer", "--inbox", sess19, env=A19)
    T.check("T19 a text from another session (origin session) is refused even in the task's session", rc != 0 and "never from another session" in err and show("s3")["state"] == "awaiting_ok", err)
    rc, out, err = cm("approve", "s4", "--by", "maintainer", "--inbox", term, env=A19)
    T.check("T19 a task with no session: only the master records an ok", rc != 0 and "only the master session (master) records" in err and show("s4")["state"] == "awaiting_ok", err)
    rel19 = inbox_put("altra", "Dall'utente via telefono. vai pure", "relay")
    rc, out, err = cm("approve", "s5", "--by", "maintainer", "--inbox", rel19, env=A19)
    T.check("T19 from the task's session, a relay message addressed to it (the app's chat) → approved", rc == 0 and show("s5")["state"] == "approved" and "origin relay, to altra" in show("s5")["approvals"][-1]["text"], err)

# T21 (23/10/2026, casella-ok-origine, the maintainer in the master's terminal at 11:10: «l'ok da terminale ha lo stesso valore di
# quello da app»): anche per la master un messaggio scritto nel terminale (origin terminal) vale; cron e non verificati mai
with T.PrivateTmux() as tm:
    tm("new-session", "-d", "-s", "master", "sleep 300")
    pane_m = tm("display-message", "-p", "-t", "master", "#{pane_id}").stdout.strip()
    M21 = dict(CC, CM_TMUX_ARGS=tm.env["CM_TMUX_ARGS"], TMUX_PANE=pane_m)
    for tid in ("v1", "v2", "v3"):
        add(contract(tid))
        cm("wait-ok", tid, "--what", "release", "--where", "github")
    term21 = inbox_put("master", "ok al rilascio di chrome-bridge 1.30.6", "terminal")
    rc, out, err = cm("approve", "v1", "--by", "maintainer", "--inbox", term21, env=M21)
    T.check("T21 the master, a message the person typed in its terminal (origin terminal) → approved; the text quotes it with origin terminal, to master",
            rc == 0 and show("v1")["state"] == "approved" and f"inbox {term21}, origin terminal, to master" in show("v1")["approvals"][-1]["text"], err)
    cron21 = inbox_put("master", "Regia di Bluey: ok, fai solo questo", "cron")
    rc, out, err = cm("approve", "v2", "--by", "maintainer", "--inbox", cron21, env=M21)
    T.check("T21 a prompt of the session's own cron (origin cron) → refused, the origin named", rc != 0 and "origin cron" in err and show("v2")["state"] == "awaiting_ok", err)
    unv21 = inbox_put("master", "ok", "unverified")
    rc, out, err = cm("approve", "v3", "--by", "maintainer", "--inbox", unv21, env=M21)
    T.check("T21 a prompt the hook could not attribute (origin unverified) → refused, the origin named", rc != 0 and "origin unverified" in err and show("v3")["state"] == "awaiting_ok", err)

# T20 (22/10/2026, contratto 1.79, titolo-breve): task.short al massimo 28 caratteri; `task short`; il ripiego dal titolo a parole intere
rc, out, err = add(contract("u1", title="supervisor: titolo breve dei compiti (campo short) nel registro", short="x" * 29))
T.check("T20 a short of 29 characters is refused by task add (exit 2, the field named)", rc == 2 and "$.task.short: length out of range" in err, err)
rc, out, err = add(contract("u1", title="supervisor: titolo breve dei compiti (campo short) nel registro", short="Titolo breve nel registro"))
add(contract("u2", title="Albo Chiaro fase 1 (task 1-8): repository e CI, verifiche, database"))
add(contract("u3", title="Move the docs site to the new host"))
for tid in ("u1", "u2", "u3"):
    cm("approve", tid, "--by", "maintainer", "--text", "ok")
imp = {x["task"]: x for x in json.loads(cm("impegni", "--json")[1])}
T.check("T20 task add keeps a short of 28 or less; impegni --json carries `short` right after `title`",
        rc == 0 and imp["u1"]["short"] == "Titolo breve nel registro" and list(imp["u1"])[:3] == ["task", "title", "short"], out + err + json.dumps(imp.get("u1")))
T.check("T20 without short: derived from the title, whole words within 28, no «…», no dangling parenthesis, no article or preposition at the end; «name: » prefix dropped",
        imp["u2"]["short"] == "Albo Chiaro fase 1" and imp["u3"]["short"] == "Move the docs site" and "…" not in imp["u2"]["short"]
        and json.loads(cm("show", "u2", "--json")[1])["contract"]["task"].get("short") is None, json.dumps({k: v["short"] for k, v in imp.items()}))
rc, out, err = cm("short", "u2", "Albo Chiaro, fase 1", "--by", "master")
s20 = show("u2")
T.check("T20 `task short ID \"testo\" --by B` sets task.short (validated, an amend step with who) and impegni shows it",
        rc == 0 and s20["contract"]["task"]["short"] == "Albo Chiaro, fase 1" and s20["steps"][-1]["kind"] == "amend" and s20["steps"][-1]["text"] == "short: Albo Chiaro, fase 1" and s20["steps"][-1]["by_"] == "master"
        and {x["task"]: x["short"] for x in json.loads(cm("impegni", "--json")[1])}["u2"] == "Albo Chiaro, fase 1", err + json.dumps(s20["steps"][-1:]))
rc, out, err = cm("short", "u2", "  Un titolo che supera i ventotto caratteri  ")
rc2, out2, err2 = cm("short", "u2", "   ")
T.check("T20 `task short` refuses 29+ characters and an empty text (exit 2, nothing changes)", rc == 2 and rc2 == 2 and "$.task.short: 1-28" in err and "$.task.short: 1-28" in err2 and show("u2")["contract"]["task"]["short"] == "Albo Chiaro, fase 1", err + err2)
cm("cancel", "u3")
rc, out, err = cm("short", "u3", "Docs site")
cl = {x["task"]: x for x in json.loads(cm("closed", "--json")[1])["rows"]}
T.check("T20 a closed task takes no short; `task closed` rows carry `short` too (derived here)", rc != 0 and "already cancelled" in err and cl["u3"]["short"] == imp["u3"]["short"] and list(cl["u3"])[:3] == ["task", "title", "short"], err + json.dumps(cl.get("u3")))

T.finish()
