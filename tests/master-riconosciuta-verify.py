#!/usr/bin/env python3
"""La master e' riconosciuta in ogni modo di avvio (19/11/2026, supervisor-master-riconosciuta-19-11; D1, D4, D5, D8 di
docs/analisi/avvio-master-2026-11-19.md; contratto 1.154).

MR1 (D1) relay: una sessione di Claude Code nella radice senza tmux ne' nome (`claude` puro, da un IDE) entra nello
    stato col nome della master e `master: true`; prima era scartata. Le altre sessioni non hanno la chiave `master`
MR2 (D1) relay: nella radice in tmux con un altro nome («lavoro») → `master: true` su «lavoro»
MR3 relay: con la tmux «master» viva e un'altra sessione nella radice, `master: true` solo sulla tmux «master»
MR4 relay: un `claude -p` nella radice (fuori dal registro di Claude Code: niente session_id) resta fuori
MR5 (D1) hook: SessionStart della master fuori da tmux (nella radice, nessuna tmux «master») dice in una riga che e'
    fuori da tmux e come rimediare, e fa il copione della master; una sessione in un progetto no
MR6 (D5) hook: SessionStart chiede una seconda push dopo start_push_later_s (qui 2 s), da un processo staccato
MR7 (D4) setup: su macOS con bash l'integrazione va in .bash_profile; su Linux con bash in .bashrc; con zsh in .zshrc
MR8 (D8) terminale: la finestra di Windows Terminal apre `wsl.exe -d <la distribuzione di qui>`
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
ws = home / "ws"
for d in ("ws/personali/alfa", ".claude/sessions"):
    (home / d).mkdir(parents=True)
state = tmp / "state"
rdir = tmp / "relay"
rdir.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "en", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master", "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": True, "dir": str(rdir), "host": "test", "start_push_later_s": 2},
    "onboarding": {"enabled": False},
}))
alive = tmp / "alive.json"
fake_cm = tmp / "supervisor"
fake_cm.write_text(f"""#!/bin/sh
case "$1" in
  sessions) cat "{alive}" ;;
  registry) echo '{{"sessioni": []}}' ;;
  quota) echo '{{}}' ;;
  *) echo '[]' ;;
esac
""")
fake_cm.chmod(0o755)
EN = json.loads((T.ROOT / "supervisor/messages/en.json").read_text())
BASE = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_HOOK_TMUX_NAME")}
BASE.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_RELAY_CM": str(fake_cm), "CM_PROC_SCAN_PIDS": ""})
root = str(ws)
alfa = str(ws / "personali" / "alfa")


def row(pid, name, tmux, cwd, sid, started=1789200000000):
    return {"pid": pid, "name": name, "tmux": tmux, "cwd": cwd, "status": "idle", "waiting": False, "session_id": sid,
            "account": "personale", "link": "", "attached": False, "started_at": started}


def sessions(*rows):
    alive.write_text(json.dumps(list(rows)))
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-relay.py"), "push", "--dry-run"], capture_output=True, text=True, env=BASE, timeout=120)
    try:
        return json.loads(r.stdout)["sessions"], r
    except (ValueError, KeyError):
        return [], r


# ------------------------------------------------------------------ MR1
ss, r = sessions(row(10, "", "", root, "S-M"), row(11, "alfa", "alfa", alfa, "S-A"))
m = [s for s in ss if s.get("master")]
T.check("MR1 a plain `claude` in the root (no tmux, no name) is in the state as «master» with master: true (before: dropped)",
        len(m) == 1 and m[0]["name"] == "master", r.stderr[-400:] + json.dumps(ss)[:400])
T.check("MR1 the other sessions carry no `master` key", all("master" not in s for s in ss if s["name"] != "master"), json.dumps(ss)[:400])

# ------------------------------------------------------------------ MR2
ss, r = sessions(row(12, "lavoro", "lavoro", root, "S-L"), row(11, "alfa", "alfa", alfa, "S-A"))
T.check("MR2 a tmux session with another name in the root («lavoro») → master: true on «lavoro»",
        [s["name"] for s in ss if s.get("master")] == ["lavoro"], r.stderr[-400:] + json.dumps(ss)[:400])

# ------------------------------------------------------------------ MR3
ss, r = sessions(row(13, "master", "master", root, "S-M", 1789300000000), row(12, "lavoro", "lavoro", root, "S-L", 1789100000000))
T.check("MR3 with the tmux «master» alive and another session in the root, master: true only on «master»",
        [s["name"] for s in ss if s.get("master")] == ["master"], json.dumps(ss)[:400])

# ------------------------------------------------------------------ MR4
ss, r = sessions(row(14, "", "", root, ""), row(11, "alfa", "alfa", alfa, "S-A"))
T.check("MR4 a `claude -p` in the root (no session_id: outside Claude Code's registry) stays out of the state",
        [s["name"] for s in ss] == ["alfa"], json.dumps(ss)[:400])


# ------------------------------------------------------------------ MR5, MR6
def hook(cwd, sid):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "SessionStart"],
                          input=json.dumps({"session_id": sid, "source": "startup", "cwd": cwd}),
                          capture_output=True, text=True, env=dict(BASE, CM_TMUX_ARGS="-L cm-mr-none"), timeout=60)


(rdir / "serve.pid").write_text(str(os.getpid()))   # un demone «vivo»: la push e' solo il tocco della richiesta
req = rdir / "push-req" / "hook"
note = EN.get("hook.master_no_tmux", "\0").split("(", 1)[0]
h = hook(root, "S-M")
t0 = time.time()
m1 = req.stat().st_mtime if req.exists() else 0
T.check("MR5 SessionStart of the master outside tmux: a line says it is outside tmux and how to fix it",
        h.returncode == 0 and "hook.master_no_tmux" in EN and note in h.stdout and "supervisor launch" in h.stdout, h.stdout[-600:] + h.stderr[-300:])
h2 = hook(alfa, "S-A")
T.check("MR5 a session in a project gets no such line", h2.returncode == 0 and note not in h2.stdout, h2.stdout[-300:])
got = T.wait_until(lambda: req.exists() and req.stat().st_mtime >= m1 + 1.5, timeout=8)
T.check("MR6 SessionStart asks for a second push after start_push_later_s (2 s here), from a detached process",
        m1 and got and time.time() - t0 < 8, f"m1={m1} now={req.stat().st_mtime if req.exists() else 0}")

# ------------------------------------------------------------------ MR7
os.environ.update(BASE)
spec = importlib.util.spec_from_file_location("cm_setup_mr", T.SCRIPTS / "cm-setup.py")
SU = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SU)
got = {}
for osn, sh in (("darwin", "/bin/bash"), ("linux", "/bin/bash"), ("darwin", "/bin/zsh")):
    os.environ.update(CM_SETUP_OS=osn, SHELL=sh)
    got[(osn, sh)] = SU.rc_file().name
T.check("MR7 macOS with bash → .bash_profile (login shells do not read .bashrc); Linux with bash → .bashrc; zsh → .zshrc",
        got == {("darwin", "/bin/bash"): ".bash_profile", ("linux", "/bin/bash"): ".bashrc", ("darwin", "/bin/zsh"): ".zshrc"}, str(got))

# ------------------------------------------------------------------ MR8
d = subprocess.run([str(T.SCRIPTS / "cm-terminal.sh"), "open", "master"], capture_output=True, text=True, timeout=30,
                   env=dict(BASE, CM_TERMINAL_BACKEND="wt", CM_TERMINAL_DRY_RUN="1", WSL_DISTRO_NAME="sup-prova"))
T.check("MR8 the Windows Terminal window opens `wsl.exe -d <this distribution>`, not Windows' default one",
        "wsl.exe -d sup-prova -e" in d.stdout, d.stdout + d.stderr)

T.finish()
