#!/usr/bin/env python3
"""L'indice di `supervisor cerca` non pesa sul disco (20/11/2026, supervisor-cerca-indice-leggero-20-11). Il 20/11 alle 10:13 il
Chromebook rallentava, con la pressione del disco al 48%: a ogni giro (cerca.index_every_s) index_commits lanciava `git log -n500`
in ognuno dei circa 170 progetti, anche dove non era cambiato niente, e nice 19 non abbassa la priorita' del disco.

CI1 un secondo giro con HEAD fermo in tutti i progetti non lancia nessun git log
CI2 un commit nuovo in un progetto: il giro dopo lancia git log solo li', e il commit si trova (una volta sola, niente doppioni)
CI3 HEAD letto anche dai refs impacchettati (git pack-refs): fermo resta fermo; un cambio di ramo e' un HEAD cambiato
CI4 un repository senza commit non lancia git log a ogni giro
CI5 cerca_tick lancia l'indice con ionice -c3 davanti a nice 19, e il comando gira davvero
CI6 senza ionice nel PATH cerca_tick lancia l'indice lo stesso, con nice 19
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
state = tmp / "state"
state.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "default_account": "personale",
                           "workspace": {"root": str(ws), "root_session_name": "master"},
                           "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                           "relay": {"enabled": False, "dir": str(tmp / "relay")}}))
REAL_GIT = shutil.which("git")
fakebin = tmp / "fakebin"
fakebin.mkdir()
GITLOG = tmp / "git-calls.log"
(fakebin / "git").write_text(f'#!/bin/sh\necho "$*" >> "{GITLOG}"\nexec "{REAL_GIT}" "$@"\n')
(fakebin / "git").chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_PROC_SCAN_PIDS": "",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
GENV = dict(ENV)   # git dei preparativi: quello vero, fuori dal conto
ENV["PATH"] = f"{fakebin}:{os.environ['PATH']}"
CLI = str(T.SCRIPTS / "supervisor")


def git(repo, *a):
    return subprocess.run([REAL_GIT, "-C", str(repo), *a], check=True, env=GENV, capture_output=True, text=True).stdout.strip()


def project(name, commits):
    d = ws / "personali" / name
    d.mkdir(parents=True)
    git(d, "init", "-q", "-b", "main")
    for i, m in enumerate(commits):
        (d / f"f{i}.txt").write_text(m)
        git(d, "add", ".")
        git(d, "commit", "-q", "-m", m)
    pd = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(d))
    pd.mkdir(parents=True)
    (pd / f"S-{name}.jsonl").write_text(json.dumps({"type": "user", "uuid": f"u-{name}", "timestamp": "2026-11-18T10:00:00.000Z",
                                                    "sessionId": f"S-{name}", "cwd": str(d), "origin": {"kind": "human"},
                                                    "message": {"role": "user", "content": f"lavoro su {name}"}}) + "\n")
    return d


alfa = project("alfa", ["alfa: primo passo"])
beta = project("beta", ["beta: base", "beta: listino zafferano"])
gamma = project("gamma", ["gamma: inizio"])
vuoto = project("vuoto", [])


def index():
    GITLOG.write_text("")
    r = subprocess.run([CLI, "cerca", "--index"], capture_output=True, text=True, env=ENV, timeout=180)
    calls = [l for l in GITLOG.read_text().splitlines() if l.strip()]
    logs = [l for l in calls if re.search(r"(^| )log( |$)", l)]
    return r, calls, logs


def commits_of(word):
    r = subprocess.run([CLI, "cerca", word, "--json"], capture_output=True, text=True, env=ENV, timeout=180)
    try:
        return [x for x in json.loads(r.stdout or "[]") if x.get("kind") == "commit"]
    except ValueError:
        return [{"bad": r.stdout[-300:] + r.stderr[-300:]}]


r0, c0, l0 = index()
first_ok = r0.returncode == 0 and len(commits_of("zafferano")) == 1
r1, c1, l1 = index()
T.check("CI1 a second run with HEAD still in every project launches no git log",
        first_ok and r1.returncode == 0 and not l1, f"first={first_ok} {r0.stderr[-300:]} | second: {l1}")

(beta / "nuovo.txt").write_text("x")
git(beta, "add", ".")
git(beta, "commit", "-q", "-m", "beta: ordine pistacchio")
r2, c2, l2 = index()
pist = commits_of("pistacchio")
T.check("CI2 a new commit in one project: the next run launches git log only there, and the commit is found once (no duplicates)",
        r2.returncode == 0 and len(l2) == 1 and str(beta) in l2[0] and len(pist) == 1 and len(commits_of("zafferano")) == 1,
        f"{l2} | {pist}")

git(gamma, "pack-refs", "--all")
git(alfa, "pack-refs", "--all")
r3, c3, l3 = index()
git(beta, "checkout", "-q", "-b", "altro", "HEAD~1")
r4, c4, l4 = index()
r5, c5, l5 = index()
T.check("CI3 HEAD read from packed refs too: still stays still; a branch switch is a changed HEAD",
        not l3 and len(l4) == 1 and str(beta) in l4[0] and not l5 and len(commits_of("zafferano")) == 1,
        f"packed: {l3} | switch: {l4} | after: {l5}")
T.check("CI4 a repository with no commits does not launch git log at every run",
        not [l for l in l1 + l3 + l5 if str(vuoto) in l], l1 + l3 + l5)

# cerca_tick dal relay: il comando che lancia
os.environ.update({k: ENV[k] for k in ("HOME", "CM_HOME", "CC_SUPERVISOR_CONFIG", "CM_PROC_SCAN_PIDS")})
sp = importlib.util.spec_from_file_location("cm_relay_ci", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
SPAWNED = []


class FakePopen:
    def __init__(self, argv, **kw):
        SPAWNED.append(list(argv))


real_popen = RL.subprocess.Popen
stamp = state / "cerca" / ".tick"


def tick(path):
    SPAWNED.clear()
    stamp.unlink(missing_ok=True)
    old = os.environ["PATH"]
    os.environ["PATH"] = path
    RL.subprocess.Popen = FakePopen
    try:
        RL.cerca_tick(10 ** 10)
    finally:
        RL.subprocess.Popen = real_popen
        os.environ["PATH"] = old
    return SPAWNED[0] if SPAWNED else []


withio = tmp / "conio"
withio.mkdir()
(withio / "ionice").write_text("#!/bin/sh\nexit 0\n")
(withio / "ionice").chmod(0o755)
a = tick(f"{withio}:{os.environ['PATH']}")
real_io = shutil.which("ionice")
ran = None
if a[:2] == ["ionice", "-c3"] and real_io:
    ran = subprocess.run(a, env=ENV, capture_output=True, text=True, timeout=180)
T.check("CI5 cerca_tick launches the index with ionice -c3 in front of nice 19, and the command really runs",
        a[:5] == ["ionice", "-c3", "nice", "-n", "19"] and a[-2:] == [str(T.SCRIPTS / "cm-cerca.py"), "--index"]
        and (ran is None or ran.returncode == 0), f"{a} | {ran.stderr[-300:] if ran else 'no real ionice'}")
noio = tmp / "senzaio"
noio.mkdir()
b = tick(str(noio))
T.check("CI6 without ionice on PATH cerca_tick still launches the index, with nice 19",
        b[:3] == ["nice", "-n", "19"] and b[-1:] == ["--index"], b)
T.finish()
