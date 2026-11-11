#!/usr/bin/env python3
"""Set dimostrativo per la card1 (S05): la tabella VERA di `supervisor sessions` (cm-sessions.py del repo,
language en) su un registro finto e un tmux privato, come fa tests/sessions-verify.py. Nessuna sessione reale:
home, config e tmux sono finti, i processi sono `sleep` con argv0 «claude». Stampa la tabella.
Uso: python3 tools-demo-sessions.py REPO > assets/readme/demo-sessions.txt"""
import json
import os
import pty
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

repo = Path(sys.argv[1])
SCRIPT = repo / "supervisor" / "scripts" / "cm-sessions.py"
tmp = Path(tempfile.mkdtemp(prefix="cm-demo-"))
home = tmp / "home"
for d in (".claude/sessions", ".claude-work/sessions", "projects/atlas-shop", "projects/field-notes", "work/ledger-api", "work/orbit-docs"):
    (home / d).mkdir(parents=True)
state = tmp / "state"
(state / "waiting").mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "en", "state_dir": str(state), "workspace": {"root": str(home)},
    "accounts": {"personal": {"config_dir": str(home / ".claude")},
                 "work": {"config_dir": str(home / ".claude-work"), "tmux_prefix": "work-"}},
    "default_account": "personal", "quota": {"source": str(tmp / "noquota")},
}))
sock = f"cm-demo-{os.getpid()}"
T = ["tmux", "-L", sock]
procs = []


def spawn(conf=None, argv0="claude"):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    if conf:
        env["CLAUDE_CONFIG_DIR"] = conf
    p = subprocess.Popen(["bash", "-c", f"exec -a {argv0} sleep 600"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    time.sleep(0.2)
    return p.pid


def proc_start(pid):
    return open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[19]


def entry(reg, pid, name, cwd, tmux, status, age_min):
    Path(reg, f"{pid}.json").write_text(json.dumps({
        "pid": pid, "name": name, "cwd": cwd, "status": status, "tmux": f"{tmux}:@0.%0",
        "startedAt": int((time.time() - age_min * 60) * 1000), "procStart": proc_start(pid), "sessionId": f"sid-{name}"}))


# (nome, account, cartella, stato, minuti, attaccata, in attesa)
DEMO = [("master", "personal", str(home), "busy", 194, True, False),
        ("atlas-shop", "work", str(home / "work" / "atlas-shop"), "busy", 41, True, False),
        ("field-notes", "personal", str(home / "projects" / "field-notes"), "idle", 2880, True, False),
        ("ledger-api", "work", str(home / "work" / "ledger-api"), "busy", 12, False, True)]
(home / "work" / "atlas-shop").mkdir(parents=True, exist_ok=True)
clients = []
try:
    for name, acc, cwd, st, age, attached, waiting in DEMO:
        tm = name if acc == "personal" else "work-" + name
        subprocess.run(T + ["new-session", "-d", "-s", tm, "-x", "100", "-y", "30", "bash", "--norc"], check=True)
        conf = str(home / (".claude" if acc == "personal" else ".claude-work"))
        pid = spawn(conf if acc == "work" else None)
        entry(Path(conf) / "sessions", pid, tm, cwd, tm, st, age)   # nome del registro = nome tmux, come dal vivo
        if waiting:
            (state / "waiting" / f"sid-{tm}").write_text("AskUserQuestion")
        if attached:
            m, s = pty.openpty()
            clients.append(subprocess.Popen(T + ["attach", "-t", f"={tm}"], stdin=s, stdout=s, stderr=s, start_new_session=True,
                                            env={**os.environ, "TERM": "xterm-256color"}))
    time.sleep(1.5)
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
           "CM_TMUX_ARGS": f"-L {sock}", "CM_PROC_SCAN_PIDS": " ".join(str(p.pid) for p in procs)}
    r = subprocess.run([sys.executable, str(SCRIPT), "--no-screen"], capture_output=True, text=True, env=env, timeout=60)
    print(r.stdout, end="")
    if r.returncode:
        print(r.stderr, file=sys.stderr)
finally:
    for c in clients:
        c.kill()
    for p in procs:
        p.kill()
    subprocess.run(T + ["kill-server"], capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)
