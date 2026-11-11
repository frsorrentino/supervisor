#!/usr/bin/env python3
"""Motore della mappa (fase 1, 03/10/2026): plan check / approve / run con una supervisor finta.

Nodi: a e b in parallelo; c dipende da a e diventa verde al secondo tentativo; s e' uno script; d dipende da b,
che resta rosso tre volte (fallito, d annullato, la master avvisata). Prima senza fable-director, poi con
fable-director abilitato nelle impostazioni dell'account: cambia solo il brief."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

FAKE = str(Path(__file__).resolve().parent / "lib" / "fake-cm-plan.py")


def setup(fd):
    tmp = Path(tempfile.mkdtemp(prefix="cm-plan-"))
    conf = tmp / ".claude"
    conf.mkdir()
    if fd:
        (conf / "settings.json").write_text(json.dumps({"enabledPlugins": {"fable-director@fable-director": True}}))
    cfg = tmp / "config.json"
    cfg.write_text(json.dumps({"language": "en", "default_account": "personal",
                               "accounts": {"personal": {"config_dir": str(conf)}}}))
    fake = tmp / "fake"
    fake.mkdir()
    env = dict(os.environ, CM_TASKS_DB=str(tmp / "tasks.db"), CC_SUPERVISOR_CONFIG=str(cfg), CM_BIN=FAKE,
               FAKE_CM_DIR=str(fake), CM_PLAN_POLL="0.2")
    projs = {k: tmp / k for k in "abcds"}
    for p in projs.values():
        p.mkdir()
    ok = 'CM-RESULT {"task": "a", "attempt": 1, "outcome": "green", "reason": "file written", "proof": {"kind": "check", "value": "test -f done.txt"}, "at": 1}'
    (fake / "script.json").write_text(json.dumps({
        str(projs["a"]): [{"touch": ["done.txt"], "say": "done\n" + ok, "sleep": 1.0}],
        str(projs["b"]): [{"say": "tried", "sleep": 1.0}],
        str(projs["c"]): [{"say": 'CM-RESULT {"task": "c", "attempt": 1, "outcome": "red", "reason": "half", "proof": {"kind": "check", "value": "x"}, "perimeter": ["c.txt"], "at": 1}'},
                          {"touch": ["c.txt"]}],
        str(projs["d"]): [{"touch": ["d.txt"]}]}))
    return tmp, env, projs, fake


def node(nid, proj, check, deps=(), route="session", **over):
    n = {"id": nid, "title": f"node {nid}", "plan": "p1",
         "where": {"project": str(proj), "session": None, "host": "local", "account": None},
         "check": {"cmd": check, "cwd": None, "timeout_s": 30}, "perimeter": [f"{nid}.txt"], "lane": "open",
         "depends_on": [{"task": d, "passes": f"output of {d}"} for d in deps], "route": route, "hold": False,
         "attempts_max": 3, "data_class": "internal", "requested_by": "test"}
    n.update(over)
    return n


def cm(env, *args):
    p = subprocess.run([str(T.SCRIPTS / "supervisor"), *args], capture_output=True, text=True, env=env, timeout=300)
    return p.returncode, p.stdout + p.stderr


def calls(fake):
    return [json.loads(x) for x in (fake / "calls.log").read_text().splitlines()] if (fake / "calls.log").exists() else []


def scenario(fd):
    tmp, env, P, fake = setup(fd)
    plan = {"schema_version": 1, "id": "p1", "title": "test plan", "nodes": [
        node("a", P["a"], "test -f done.txt"),
        node("b", P["b"], "test -f never.txt"),
        node("c", P["c"], "test -f c.txt", deps=["a"]),
        node("d", P["d"], "test -f d.txt", deps=["b"]),
        node("s", P["s"], "test -f s.txt", route="script", run="touch s.txt")]}
    pf = tmp / "plan.json"
    pf.write_text(json.dumps(plan))
    return tmp, env, P, fake, plan, pf


tag = "(alone)"
tmp, env, P, fake, plan, pf = scenario(False)

# check
bad = dict(plan, nodes=[dict(plan["nodes"][0], depends_on=[{"task": "c", "passes": "x"}])] + plan["nodes"][1:])
(tmp / "cycle.json").write_text(json.dumps(bad))
rc_c, out_c = cm(env, "plan", "check", str(tmp / "cycle.json"))
bad2 = dict(plan, nodes=[dict(plan["nodes"][0], depends_on=[{"task": "zz", "passes": "x"}], plan="other"), dict(plan["nodes"][4], run=None)])
(tmp / "bad.json").write_text(json.dumps(bad2))
rc_b, out_b = cm(env, "plan", "check", str(tmp / "bad.json"))
T.check("P1 plan check: a cycle, an edge to an unknown node, a node of another plan, a script node without run → exit 2, each said",
        rc_c == 2 and "cycle" in out_c and rc_b == 2 and "unknown node zz" in out_b and "plan: must be p1" in out_b and "run: required for route script" in out_b, out_c + out_b)
T.check("P1 a good plan → ok", cm(env, "plan", "check", str(pf)) == (0, "ok\n"), "")

# run senza approvazione
rc, out = cm(env, "plan", "run", str(pf))
T.check("P2 run of a plan that is not approved → refused, nothing launched", rc != 0 and "not approved" in out and not calls(fake), out)
rc, out = cm(env, "plan", "approve", str(pf), "--by", "maintainer", "--text", "ok il piano")
st = json.loads(cm(env, "plan", "status", "p1", "--json")[1])
T.check("P2 approve registers the plan and its five tasks (proposed)", rc == 0 and [r["state"] for r in st] == ["proposed"] * 5, out + str(st))
rc, out = cm(env, "plan", "run", "p1", "--dry-run")
T.check("P2 --dry-run shows the waves: a, b, s first; then c, d", "wave: a, b, s" in out and "wave: c, d" in out, out)

rc, out = cm(env, "plan", "run", "p1", "--parallel", "3", "--turn-timeout", "30")
st = {r["id"]: r["state"] for r in json.loads(cm(env, "plan", "status", "p1", "--json")[1])}
cl = calls(fake)
seq = [(c["args"][0], c["args"][1] if len(c["args"]) > 1 else "") for c in cl]
T.check("P3 run: a, c, s done; b failed after 3 attempts; d cancelled; exit 1", rc == 1 and st == {"a": "done", "b": "failed", "c": "done", "d": "cancelled", "s": "done"}, str(st) + out[-600:])
launches = [c["args"][1] for c in cl if c["args"][0] == "launch"]
first_c = next(i for i, c in enumerate(cl) if c["args"][:2] == ["talk", "c"])
T.check("P3 a and b launched in parallel (both before either finished); c only after a was green; d never launched; s without a session",
        launches[:2] in ([str(P["a"]), str(P["b"])], [str(P["b"]), str(P["a"])]) and str(P["d"]) not in launches and str(P["s"]) not in launches
        and all(c["args"][0] != "launch" or c["args"][1] != str(P["c"]) or i > [j for j, x in enumerate(cl) if x["args"][:2] == ["wait", "a"]][0] for i, c in enumerate(cl))
        and (P["s"] / "s.txt").exists(), str(seq))
talks_c = [c["args"][2] for c in cl if c["args"][:2] == ["talk", "c"]]
T.check("P4 c: red then green — sent twice, the second brief says attempt 2 of 3, the check's reason, and the perimeter narrowed to what the session named",
        len(talks_c) == 2 and "Attempt 2 of 3" in talks_c[1] and "check exit 1" in talks_c[1] and "Perimeter (only these files): c.txt" in talks_c[1], str(talks_c)[:600])
T.check("P4 the brief carries the check, the perimeter, the lane and the CM-RESULT line; no fable-director line without it",
        "Check (must exit 0; the engine reruns it): test -f c.txt" in talks_c[0] and "Lane: open" in talks_c[0] and "CM-RESULT" in talks_c[0] and "fable-director" not in talks_c[0], talks_c[0])
talks_b = [c for c in cl if c["args"][:2] == ["talk", "b"]]
notice = [c["args"][2] for c in cl if c["args"][:2] == ["talk", "master"]]
T.check("P5 b: only b retried (3 talks), then one notice to master naming b and the reason", len(talks_b) == 3 and len(notice) == 1 and "unit b (node b) is red after 3 attempts" in notice[0], str(notice))
closes = sorted(c["args"][1] for c in cl if c["args"][0] == "close")
T.check("P6 every session the engine opened is closed (a, b, c), none else", closes == ["a", "b", "c"], str(closes))
show_a = json.loads(cm(env, "task", "show", "a", "--json")[1])
T.check("P7 the session's CM-RESULT is recorded (not accepted); the acceptance is the engine's check",
        [(r["outcome"], r["accepted"]) for r in show_a["results"]] == [("green", None), ("green", 1)], str(show_a["results"]))
n = len(cl)
rc, out = cm(env, "plan", "run", "p1")
T.check("P8 run again: nothing left to do — no launch, no talk, same final state", all(c["args"][0] == "sessions" for c in calls(fake)[n:]), str(calls(fake)[n:]))

# con fable-director abilitato: solo il brief cambia
tmp, env, P, fake, plan, pf = scenario(True)
cm(env, "plan", "approve", str(pf), "--by", "maintainer", "--text", "ok")
rc, out = cm(env, "plan", "run", "p1", "--parallel", "3", "--turn-timeout", "30")
ta = [c["args"][2] for c in calls(fake) if c["args"][:2] == ["talk", "a"]]
st2 = {r["id"]: r["state"] for r in json.loads(cm(env, "plan", "status", "p1", "--json")[1])}
T.check("P9 with fable-director enabled: the brief asks to open its budget with the node's check as --verify and the perimeter as --paths; the run ends the same",
        ta and '--verify "test -f done.txt" --paths "a.txt" --data-class internal' in ta[0] and st2 == {"a": "done", "b": "failed", "c": "done", "d": "cancelled", "s": "done"}, (ta[0] if ta else "") + str(st2))

# superate_dipendenze (04/11/2026, supervisor-superate-dipendenze-04-11): una dipendenza superseded vale come done
tmp, env, P, fake = setup(False)
plan3 = {"schema_version": 1, "id": "p3", "title": "superseded plan", "nodes": [
    node("x", P["a"], "test -f never.txt", plan="p3"),
    node("y", P["s"], "test -f y.txt", deps=["x"], route="script", run="touch y.txt", plan="p3")]}
pf3 = tmp / "plan3.json"
pf3.write_text(json.dumps(plan3))
cm(env, "plan", "approve", str(pf3), "--by", "maintainer", "--text", "ok")
_c = __import__("sqlite3").connect(tmp / "tasks.db")
_c.execute("UPDATE tasks SET state='superseded' WHERE id='x'")
_c.commit(); _c.close()
rc3, out3 = cm(env, "plan", "run", "p3", "--turn-timeout", "30")
st3 = {r["id"]: r["state"] for r in json.loads(cm(env, "plan", "status", "p3", "--json")[1])}
T.check("P10 a dependency closed as superseded lets its dependent start (y done), x is not launched, the plan ends ok (exit 0)",
        st3 == {"x": "superseded", "y": "done"} and rc3 == 0 and not any(c["args"][0] == "launch" for c in calls(fake)), str(st3) + out3[-400:])

T.finish()
