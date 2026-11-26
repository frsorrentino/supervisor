#!/usr/bin/env python3
"""Le tappe della prova di un compito in verifica nello stato del relay: sessions[].steps (26/11/2026,
supervisor-relay-tappe-26-11, contratto 1.162, chiesto da win-supervisor-app per app-animazioni-otto-26-11).

TP1 la fixture (tests/fixtures/tappe/steps-in-verifica.json): proof «test, CI, pre-release, prova», test ok (task result
    green), CI ok (gh: le run del commit riuscite), pre-release in corso (il commit senza tag: la prima da fare), prova
    da fare; le etichette in italiano (Test, CI, Pre-release, Prova), at l'ora del passaggio
TP2 le tappe sono quelle che outcome.proof nomina, nel suo ordine; un tag che contiene il commit = pre-release ok; una
    prova aperta = run, una ko = ko; una run di CI in corso = run, una fallita = ko
TP3 il Chromebook resta leggero: nessuna chiamata a gh senza commit; per lo stesso commit gh al piu' una volta ogni 60 s;
    senza gh o con gh non autenticato la tappa resta todo, nessuna riga nel log
TP4 null: una sessione senza compito, un compito con tutte le tappe da fare, uno senza tappe nel proof; nello stato la
    chiave steps c'e' solo con un compito in verifica (assente = null: lo stato resta sotto il taglio)
TP5 il contratto del relay (details/relay.md) ha la 1.162 con sessions[].steps
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
(home / ".claude" / "sessions").mkdir(parents=True)
ws = home / "ws"
proj = ws / "personali" / "alfa"
proj.mkdir(parents=True)
bin_ = tmp / "bin"
bin_.mkdir()
ghlog, ghout = tmp / "gh-calls.log", tmp / "gh-out.json"
gh = bin_ / "gh"
gh.write_text(f'#!/bin/sh\necho "$*" >> "{ghlog}"\n[ -f "{tmp}/gh-noauth" ] && {{ echo "gh: not logged in" >&2; exit 4; }}\ncat "{ghout}"\n')
gh.chmod(0o755)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(tmp / "relay"), "gh_bin": str(gh)},
    "registry": {"file": str(tmp / "registry.json")}, "tabs": {"color_registry": str(tmp / "colors")},
}))
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE"):
    os.environ.pop(k, None)
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_PROC_SCAN_PIDS": "", "CM_RELAY_NO_PUSH": "1",
                   "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def load(name, tag):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + tag, T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def git(*a):
    return subprocess.run(["git", "-C", str(proj), *a], capture_output=True, text=True, check=True).stdout.strip()


git("init", "-q")
(proj / "b.txt").write_text("2")
git("add", "-A")
git("commit", "-qm", "fix: altro (app-con-tag-26-11)")
git("tag", "v0.2.1")
(proj / "a.txt").write_text("1")
git("add", "-A")
git("commit", "-qm", "feat: le animazioni (app-animazioni-otto-26-11)")
sha = git("rev-parse", "HEAD")

TK = load("cm-tasks", "_tp")
con = TK.connect()
NOW = int(time.time())


def task(tid, session, proof, state="running", upd=NOW):
    c = {"schema_version": 1, "task": {"id": tid, "title": tid, "outcome": {"expected": "x", "kind": "custom", "proof": proof}}}
    con.execute("INSERT INTO tasks (id, project, session, title, state, contract, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (tid, str(proj), session, tid, state, json.dumps(c), upd, upd))


task("app-animazioni-otto-26-11", "supervisor-app", "test, CI, pre-release, prova")
con.execute("INSERT INTO results (task, attempt, outcome, reason, proof, at) VALUES (?,?,?,?,?,?)",
            ("app-animazioni-otto-26-11", 1, "green", "ok", "{}", NOW - 600))
task("app-con-tag-26-11", "beta", "prova, test e pre-release")
con.execute("INSERT INTO prove (task, build, dove, fai, atteso, at) VALUES (?,?,?,?,?,?)", ("app-con-tag-26-11", "v0.2.1", "web", "apri", "va", NOW - 60))
task("app-tutto-da-fare-26-11", "gamma", "test, CI")
task("app-senza-tappe-26-11", "delta", "screenshot")
con.commit()
ghout.write_text(json.dumps([{"status": "completed", "conclusion": "success", "updatedAt": "2026-11-26T10:00:00Z"},
                             {"status": "completed", "conclusion": "success", "updatedAt": "2026-11-26T10:05:00Z"}]))

RL = load("cm-relay", "_tp")
RL.MEMO_ON = False
fix = json.loads((T.ROOT / "tests" / "fixtures" / "tappe" / "steps-in-verifica.json").read_text())
st = RL.steps_summary() if hasattr(RL, "steps_summary") else {}
a = st.get("supervisor-app") or {}
shape = lambda s: {"task": s.get("task"), "items": [{k: i.get(k) for k in ("key", "label", "state")} for i in s.get("items") or []]}  # noqa: E731
want_ci_at = int(__import__("datetime").datetime(2026, 11, 26, 10, 5, tzinfo=__import__("datetime").timezone.utc).timestamp())
items = {i["key"]: i for i in a.get("items") or []}
T.check("TP1 the fixture: test ok (result green), CI ok (the commit's runs passed), pre-release in progress (the first still to do), prova to do; Italian labels; at = the time of the change",
        shape(a) == shape(fix["sessions"][0]["steps"]) and items.get("test", {}).get("at") == NOW - 600 and items.get("ci", {}).get("at") == want_ci_at
        and items.get("prerelease", {}).get("at") == max(NOW - 600, want_ci_at) and items.get("proof", {}).get("at") is None,
        f"{json.dumps(a)}\n{json.dumps(fix['sessions'][0]['steps'])}")

b = st.get("beta") or {}
c1 = RL.ci_state(str(proj), "c-run", now=1) if hasattr(RL, "ci_state") else None
ghout.write_text(json.dumps([{"status": "in_progress", "conclusion": "", "updatedAt": "2026-11-26T10:00:00Z"}]))
c2 = RL.ci_state(str(proj), "c-run2", now=1) if hasattr(RL, "ci_state") else None
ghout.write_text(json.dumps([{"status": "completed", "conclusion": "failure", "updatedAt": "2026-11-26T10:00:00Z"}]))
c3 = RL.ci_state(str(proj), "c-fail", now=1) if hasattr(RL, "ci_state") else None
T.check("TP2 the steps the proof names, in its order (prova, test, pre-release); a tag containing the commit = pre-release ok; an open test = run; CI in progress = run, a failed run = ko",
        [(i["key"], i["state"]) for i in b.get("items") or []] == [("proof", "run"), ("test", "todo"), ("prerelease", "ok")]
        and c2 and c2[0] == "run" and c3 and c3[0] == "ko", f"{b} {c1} {c2} {c3}")

ghlog.unlink(missing_ok=True)
if hasattr(RL, "_STEPS_CI"):
    RL._STEPS_CI.clear()
t0 = time.time()
for _ in range(3):
    RL.steps_summary()
n1 = len(ghlog.read_text().splitlines()) if ghlog.exists() else 0
x1 = RL.ci_state(str(proj), sha, now=t0 + 61)[0] if hasattr(RL, "ci_state") else None
n2 = len(ghlog.read_text().splitlines()) if ghlog.exists() else 0
(tmp / "gh-noauth").write_text("")
RL._STEPS_CI.clear() if hasattr(RL, "_STEPS_CI") else None
noauth = RL.ci_state(str(proj), sha) if hasattr(RL, "ci_state") else None
os.remove(tmp / "gh-noauth")
RL.CFG["relay"]["gh_bin"] = str(tmp / "non-c-e")
RL._STEPS_CI.clear() if hasattr(RL, "_STEPS_CI") else None
nogh = RL.ci_state(str(proj), sha) if hasattr(RL, "ci_state") else None
logf = RL.log_path() if hasattr(RL, "log_path") else tmp / "none"
calls = ghlog.read_text() if ghlog.exists() else ""
T.check("TP3 light: gh only for a task with a commit (never for the others), at most once per commit in 60 s, again after; no gh or gh not signed in → todo, nothing in the log",
        n1 == 1 and sha in calls and n2 == 2 and x1 == "ko" and noauth == ("todo", None) and nogh == ("todo", None)
        and not (logf.exists() and "gh" in logf.read_text()), f"{n1} {n2} {x1} {noauth} {nogh} {calls}")

src = {"rows": [{"name": n, "tmux": n, "status": "idle", "session_id": f"s-{n}"} for n in ("supervisor-app", "gamma", "delta", "epsilon")], "steps": st}
try:
    state = RL.S.build_state(src, NOW, fit=False)
    by = {s["name"]: s for s in state["sessions"]}
except Exception as e:  # noqa: BLE001
    by = {"error": str(e)}
T.check("TP4 null for a session with no task, a task with all steps still to do, a task whose proof names none; in the state the key steps only with a task under test (absent = null)",
        "gamma" not in st and "delta" not in st and all(n in by and "steps" not in by[n] for n in ("gamma", "delta", "epsilon")) and (by.get("supervisor-app", {}).get("steps") or {}).get("task") == "app-animazioni-otto-26-11",
        f"{sorted(st)} {json.dumps({n: s.get('steps') for n, s in by.items()})[:600]}")

doc = (T.ROOT / "details" / "relay.md").read_text()
T.check("TP5 the relay contract (details/relay.md) has 1.162 with sessions[].steps",
        "Contract 1.162" in doc and "sessions[].steps" in doc and "Pre-release" in doc, "")
con.close()
T.rm(str(tmp))
T.finish()
