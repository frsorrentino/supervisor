#!/usr/bin/env python3
"""Verifica cm-screen.py (lo schermo di una sessione dal telefono, idea 2 dell'11/09) col claude finto.

S1  screen <nome>: le ultime righe dello schermo (default 30), senza righe vuote in coda, exit 0
S2  --lines N limita; S3 sessione inesistente → messaggio ed exit 1; senza argomenti → uso, exit 2
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text('{"language": "it", "state_dir": "%s"}' % (tmp / "state"))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


def env(tm):
    return {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"]}


def screen(tm, *args):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-screen.py"), *args], capture_output=True, text=True, env=env(tm), timeout=60)


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "alfa", "-x", "100", "-y", "40",
                    "env", "FAKE_CLAUDE_SCENARIO=question", "FAKE_CLAUDE_REGISTER=0", str(FAKE)], env=env(tm), check=True)
    time.sleep(2)
    r = screen(tm, "alfa")
    lines = r.stdout.splitlines()
    T.check("S1 last lines of the screen, no trailing blanks, exit 0", r.returncode == 0 and "Enter to select" in r.stdout and "colore preferito?" in r.stdout and lines and lines[-1].strip() != "" and len(lines) <= 30, r.stdout + r.stderr)
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "lungo", "-x", "40", "-y", "12", "sh", "-c",
                    "printf '%s\\n' 'parola spezzata a meta dal riquadro stretto di quaranta colonne'; sleep 60"], env=env(tm), check=True)
    time.sleep(1.5)
    plain = screen(tm, "lungo").stdout
    joined = screen(tm, "lungo", "--join").stdout
    T.check("S4 --join: tmux reunites the lines it wrapped (the long sentence is whole on one line; without it, cut)", "parola spezzata a meta dal riquadro stretto di quaranta colonne" in joined and "parola spezzata a meta dal riquadro stretto di quaranta colonne" not in plain, "PLAIN=" + repr(plain[-200:]) + " JOINED=" + repr(joined[-200:]))
    r = screen(tm, "alfa", "--lines", "2")
    T.check("S2 --lines 2 → two lines, the last ones", r.returncode == 0 and len(r.stdout.splitlines()) == 2 and "Enter to select" in r.stdout, r.stdout + r.stderr)
    r = screen(tm, "nessuna")
    T.check("S3 unknown session: message, exit 1", r.returncode == 1 and "nessuna sessione" in r.stderr, r.stdout + r.stderr)
    r = screen(tm)
    T.check("S3 no args: usage, exit 2", r.returncode == 2 and "uso:" in r.stderr, r.stdout + r.stderr)
T.rm(tmp)
T.finish()
