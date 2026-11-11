#!/usr/bin/env python3
"""Fase C del programma approvato il 01/11/2026 (contratto 1.103). rendiconto: RE1 per cliente e mese, RE2 commit e uscite, RE3 niente
ore e nessun invio, RE4 bozza solo da un tocco. pagella: PA1 sotto i 10 casi niente numeri, PA2 difetti solo da un legame vero, PA3
consiglio solo con una differenza vera, PA4 modello ed effort registrati alla chiusura, ignoto mai dedotto."""
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
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
root = tmp / "ws"
acme = root / "acme"
beta = root / "beta"
acme.mkdir(parents=True)
beta.mkdir()
cfg = tmp / "c.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "s"), "accounts": {"personal": {"config_dir": str(home / ".claude")}}, "default_account": "personal",
                           "workspace": {"root": str(root), "root_session_name": "master", "project_dirs": ["."]}, "relay": {"dir": str(tmp / "r")}}))
(tmp / "r").mkdir()
fake = tmp / "fake-cm"
log = tmp / "talk.log"
fake.write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db"), "CM_RELAY_CM": str(fake), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"})
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")


def run(*a):
    p = subprocess.run([CLI, *a], capture_output=True, text=True, env=ENV, stdin=subprocess.DEVNULL, timeout=120)
    return p.returncode, p.stdout, p.stderr


def load(n):
    sp = importlib.util.spec_from_file_location(n.replace("-", "_"), T.SCRIPTS / f"{n}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


TK = load("cm-tasks")
TK.closing_runtime = lambda s: ("m-a", "high") if s == "sa" else ("m-b", "low") if s == "sb" else (None, None)
con = TK.connect()


def task(tid, project, session="sa", kind="fix", at=None, close=True):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"Lavoro {tid}", "type": kind, "perimeter": ["**"], "where": {"project": str(project), "session": session, "host": "local", "account": None},
                                      "check": {"cmd": "test -d .", "cwd": str(project), "timeout_s": 30}, "outcome": {"expected": "x", "kind": "custom", "proof": "y"}, "plan": "p", "lane": "closed",
                                      "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "t"}}
    got, errs = TK.add(con, c)
    assert not errs, errs
    if close:
        TK.done(con, got, checked=(0, "ok"))
        if at:
            con.execute("UPDATE results SET at=? WHERE task=?", (at, got))
    con.commit()
    return got


M0 = "2026-09"
AT = int(time.mktime((2026, 9, 15, 12, 0, 0, 0, 0, -1)))
OTHER = int(time.mktime((2026, 8, 15, 12, 0, 0, 0, 0, -1)))
task("a1", acme, at=AT)
task("a2", acme, at=AT + 60)
task("a3", acme, at=OTHER)
task("b1", beta, at=AT)
task("open1", acme, close=False)
for args in (["init", "-q"],):
    subprocess.run(["git", "-C", str(acme), *args], check=True)
(acme / "f").write_text("1")
genv = dict(ENV, GIT_AUTHOR_DATE=f"{AT} +0000", GIT_COMMITTER_DATE=f"{AT} +0000")
subprocess.run(["git", "-C", str(acme), "add", "f"], check=True)
subprocess.run(["git", "-C", str(acme), "commit", "-qm", "fix the checkout"], check=True, env=genv)
subprocess.run(["git", "-C", str(acme), "tag", "-a", "v1", "-m", "v1"], check=True, env=genv)
rc, out, err = run("rendiconto", "--help")
T.check("rendiconto the command has a help", rc == 0 and "--mese" in out, out + err)
rc, out, err = run("rendiconto", "--mese", M0, "--json")
rows = json.loads(out)
T.check("rendiconto RE1 one client per row, counted in the month, an open task out", [(x["name"], x["n"]) for x in rows] == [("acme", 2), ("beta", 1)], out + err)
rc, out, err = run("rendiconto", str(acme), "--mese", M0, "--json")
d = json.loads(out)
T.check("rendiconto RE2 tasks, commit and release of the month", d["measured"] == {"tasks": 2, "commits": 1, "releases": 1} and d["commits"][0]["subject"] == "fix the checkout" and d["releases"][0]["tag"] == "v1", out + err)
T.check("rendiconto RE3 no hours anywhere: only what is measured", not {"hours", "ore", "tempo", "billable"} & set(d) and not {"hours", "ore"} & set(d["measured"]), str(list(d)))
rc, out, err = run("rendiconto", "acme", "--mese", "2026-08", "--json")
T.check("rendiconto RE2 another month, another count; a relative name resolves", json.loads(out)["measured"]["tasks"] == 1 and json.loads(out)["measured"]["commits"] == 0, out + err)
(acme / "beta").mkdir()
_p = subprocess.run([CLI, "rendiconto", "beta", "--mese", M0, "--json"], capture_output=True, text=True, env=ENV, cwd=str(acme))
T.check("rendiconto a relative name is not looked up from the current folder", json.loads(_p.stdout)["project"] == os.path.realpath(beta), _p.stdout + _p.stderr)
(acme / "beta").rmdir()
T.check("rendiconto bad month refused", run("rendiconto", "--mese", "settembre")[0] != 0, "")
T.check("rendiconto unknown folder refused", run("rendiconto", "nessuno", "--mese", M0)[0] != 0, "")
_src = (T.SCRIPTS / "cm-conti.py").read_text()
T.check("rendiconto RE3 nothing here sends: no talk, no network, no mail", not any(x in _src for x in ("urllib", "smtplib", "requests", "fcm", "\"talk\"")), "")
RL = load("cm-relay")
ok, txt = RL.execute({"id": "a", "op": "conti", "arg": M0, "project": "acme"})[:2]
v = json.loads(txt)
T.check("rendiconto op conti: clients, detail, project relative, passive read", ok and v["clienti"][0]["project"] == "acme" and v["dettaglio"]["project"] == "acme" and "conti" in RL.PASSIVE_OPS and "conti_azione" not in RL.PASSIVE_OPS and {"conti", "conti_azione"} <= set(RL.OPS), txt[:300])
T.check("rendiconto the months list holds the months with work", M0 in v["mesi"] and "2026-08" in v["mesi"], str(v["mesi"]))
ok, t = RL.execute({"id": "b", "op": "conti_azione", "arg": M0, "project": "acme"})[:2]
T.check("rendiconto RE4 no touch, no draft: no quota spent", not ok and not log.exists(), t)
ok, t = RL.execute({"id": "b", "op": "conti_azione", "arg": M0, "project": "acme", "device": "phone"})[:2]
_tl = log.read_text() if log.exists() else ""
T.check("rendiconto RE4 a touch asks the master, says not to send and no hours", ok and "talk master" in _tl and "Non inviarla" in _tl and "niente ore" in _tl, t + _tl)
T.check("rendiconto RE4 the row remembers the request", json.loads(RL.execute({"id": "a", "op": "conti", "arg": M0})[1])["clienti"][0]["draft_at"], "")
T.check("rendiconto RE4 a bad month refuses", not RL.execute({"id": "b", "op": "conti_azione", "arg": "x", "project": "acme", "device": "phone"})[0], "")

# pagella
for i in range(12):
    task(f"ga{i}", beta, session="sa", kind="feature")
for i in range(6):
    task(f"gb{i}", beta, session="sb", kind="fix")
pg = TK.pagella(con)
_r = {(x["model"], x["kind"]): x for x in pg["rows"]}
T.check("pagella PA1 under 10 cases: no numbers", _r[("m-b", "fix")]["enough"] is False and _r[("m-b", "fix")]["defects"] is None and _r[("m-b", "fix")]["rate"] is None, str(_r[("m-b", "fix")]))
T.check("pagella PA2 no link, no defect: zero, with enough cases", _r[("m-a", "feature")]["enough"] and _r[("m-a", "feature")]["defects"] == 0, str(_r[("m-a", "feature")]))
T.check("pagella PA4 model and effort recorded at closure", _r[("m-a", "feature")]["efforts"] == {"high": 12}, str(_r[("m-a", "feature")]))
fx = task("fix-ga0", beta, session="sa", kind="fix", close=False)
rc = TK.main(["defect-of", fx, "ga0"])
con = TK.connect()
pg = TK.pagella(con)
T.check("pagella PA2 a true defect_of link counts on the task it corrects", {(x["model"], x["kind"]): x for x in pg["rows"]}[("m-a", "feature")]["defects"] == 1, str(pg["rows"]))
T.check("pagella PA3 no advice with no real difference", pg["advice"] == [], str(pg["advice"]))
task("nullo", beta, session=None)
T.check("pagella PA4 an unknown model is never deduced: counted apart", TK.pagella(con)["unknown_model"] == 1, str(TK.pagella(con)))
_real = TK.closing_runtime
del TK.closing_runtime
T.check("pagella PA4 the runtime's shape from a live session: model as {id, label}, plain ids stored", TK.plain_runtime({"model": {"id": "claude-opus-5-5", "label": "Opus 5.5"}, "effort": "high"}) == ("claude-opus-5-5", "high") and TK.plain_runtime({}) == (None, None), "")
T.check("pagella PA4 no session: unknown", load("cm-tasks").closing_runtime(None) == (None, None), "")
T.check("pagella PA4 a session not found: unknown, the closure does not fail", load("cm-tasks").closing_runtime("nessuna") == (None, None), "")
TK.closing_runtime = _real
for i in range(20):
    task(f"xa{i}", beta, session="sa", kind="docs")
    task(f"xb{i}", beta, session="sb", kind="docs")
    if i < 12:
        _f = task(f"fxb{i}", beta, session="sa", kind="other", close=False)
        con.execute("INSERT OR REPLACE INTO defects (task, defect_of, at) VALUES (?,?,1)", (_f, f"xb{i}"))
con.commit()
adv = TK.pagella(con)["advice"]
T.check("pagella PA3 advice with a real difference, on the same kind of work", [(a["kind"], a["better"], a["worse"]) for a in adv] == [("docs", "m-a", "m-b")], str(adv))
rc, out, err = run("task", "pagella")
T.check("pagella the command prints it", rc == 0 and json.loads(out)["min"] == 10, out + err)
T.check("pagella defect_of is in the help", "defect_of" in run("task", "--help")[1], "")
_fp = json.loads((T.ROOT / "tests/fixtures/relay/cmd-result-sample.json").read_text())
_fr = {r["id"]: r for r in _fp["result"]}
_fx = next(json.loads(_fr[c["id"]]["text"]) for c in _fp["cmd"] if c["op"] == "conti")
_rv = json.loads(RL.execute({"id": "a", "op": "conti", "arg": M0, "project": "acme"})[1])
T.check("rendiconto the contract's sample has the keys the code sends", set(_fx) == set(_rv) and set(_fx["dettaglio"]) == set(_rv["dettaglio"]) and set(_fx["clienti"][0]) == set(_rv["clienti"][0]) and set(_fx["pagella"]) == set(_rv["pagella"]) and set(_fx["pagella"]["rows"][0]) == set(_rv["pagella"]["rows"][0]), str(sorted(_rv)))
T.finish()
