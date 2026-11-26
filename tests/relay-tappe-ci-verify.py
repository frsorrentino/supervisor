#!/usr/bin/env python3
"""La tappa CI guarda l'ultimo commit del ramo che contiene quello del compito (26/11/2026,
supervisor-tappe-ci-successiva-26-11).

Caso vero: app-animazioni-passi-26-11 ha il commit 136c0229; la sua run 45831059359 e' fallita per un golden mancante,
preso nel commit dopo (98ed9c23, verde). La riga della sessione mostrava ancora CI ✕ «CI non e' passata»: la tappa
leggeva solo le run del commit del compito.

TC1 il caso vero: la run del commit del compito fallita, quella del commit dopo sullo stesso ramo verde → ok, at = la
    fine della run verde
TC2 il commit dopo ha una run in corso → run (anche se quella del compito e' fallita)
TC3 una run fallita, piu' recente, su un commit di un altro ramo che NON contiene quello del compito non conta: resta ok
TC4 senza commit dopo, vale la run del commit del compito (fallita → ko, come prima)
TC5 leggero come prima: una sola chiamata a gh per commit in 60 s, di nuovo dopo
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude" / "sessions").mkdir(parents=True)
proj = home / "ws" / "personali" / "app"
proj.mkdir(parents=True)
ghlog, ghout = tmp / "gh-calls.log", tmp / "gh-out.json"
gh = tmp / "gh"
gh.write_text(f'#!/bin/sh\necho "$*" >> "{ghlog}"\ncat "{ghout}"\n')
gh.chmod(0o755)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"), "default_account": "personale",
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(tmp / "relay"), "gh_bin": str(gh)},
    "registry": {"file": str(tmp / "registry.json")}, "tabs": {"color_registry": str(tmp / "colors")},
}))
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE"):
    os.environ.pop(k, None)
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_PROC_SCAN_PIDS": "", "CM_RELAY_NO_PUSH": "1",
                   "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def git(*a):
    return subprocess.run(["git", "-C", str(proj), *a], capture_output=True, text=True, check=True).stdout.strip()


git("init", "-q", "-b", "main")
(proj / "a.txt").write_text("1")
git("add", "-A")
git("commit", "-qm", "feat: i passi (app-animazioni-passi-26-11)")
task = git("rev-parse", "HEAD")
(proj / "golden.png").write_text("g")
git("add", "-A")
git("commit", "-qm", "test: il golden mancante")
fix = git("rev-parse", "HEAD")
git("checkout", "-q", "-b", "altro", task + "~0")
(proj / "x.txt").write_text("x")
git("add", "-A")
git("commit", "-qm", "altro ramo")
other = git("rev-parse", "HEAD")
git("checkout", "-q", "main")
# un ramo che non contiene il compito: parte da prima (un commit vuoto radice a parte)
git("checkout", "-q", "--orphan", "lontano")
git("commit", "-q", "--allow-empty", "-m", "lontano")
far = git("rev-parse", "HEAD")
git("checkout", "-q", "main")

spec = importlib.util.spec_from_file_location("cm_relay_tc", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RL)
RL.MEMO_ON = False


def run(sha, status, concl, created, updated):
    return {"headSha": sha, "status": status, "conclusion": concl, "createdAt": created, "updatedAt": updated}


def ci(runs, commit=task, now=1000):
    ghout.write_text(json.dumps(runs))
    RL._STEPS_CI.clear() if hasattr(RL, "_STEPS_CI") else None
    return RL.ci_state(str(proj), commit, now=now)


ep = lambda s: RL._utc_epoch(s)  # noqa: E731
real = [run(fix, "completed", "success", "2026-11-26T12:10:00Z", "2026-11-26T12:16:00Z"),
        run(task, "completed", "failure", "2026-11-26T12:00:00Z", "2026-11-26T12:06:00Z")]
r1 = ci(real)
T.check("TC1 the real case: the task commit's run failed, the next commit on its branch green → ok, at = the end of the green run",
        r1 == ("ok", ep("2026-11-26T12:16:00Z")), f"{r1}")
r2 = ci([run(fix, "in_progress", "", "2026-11-26T12:10:00Z", "2026-11-26T12:11:00Z"), real[1]])
T.check("TC2 the next commit has a run in progress → run, even with the task's run failed", r2 and r2[0] == "run", f"{r2}")
r3 = ci([run(far, "completed", "failure", "2026-11-26T13:00:00Z", "2026-11-26T13:05:00Z"), *real])
T.check("TC3 a newer failed run on a commit that does NOT contain the task's (another branch) does not count: still ok",
        r3 and r3[0] == "ok", f"{r3} far={far[:8]} other={other[:8]}")
r4 = ci([real[1]])
T.check("TC4 no later commit: the task commit's own run counts (failed → ko, as before)", r4 and r4[0] == "ko", f"{r4}")
ghout.write_text(json.dumps(real))
RL._STEPS_CI.clear() if hasattr(RL, "_STEPS_CI") else None
ghlog.unlink(missing_ok=True)
for _ in range(3):
    RL.ci_state(str(proj), task, now=5000)
n1 = len(ghlog.read_text().splitlines()) if ghlog.exists() else 0
RL.ci_state(str(proj), task, now=5061)
n2 = len(ghlog.read_text().splitlines()) if ghlog.exists() else 0
T.check("TC5 light as before: one gh call per commit in 60 s, again after", n1 == 1 and n2 == 2, f"{n1} {n2}")
T.rm(str(tmp))
T.finish()
