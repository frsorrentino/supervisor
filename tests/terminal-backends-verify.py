#!/usr/bin/env python3
"""Verifica cm-terminal.sh in dry run: un argv per backend (D8), rilevamento, headless.

B1 chromeos: garcon --client --terminal <dispatcher> attach NOME ephemeral (T6: nessun token con `-` dopo il dispatcher)
B2 gnome, kitty, wt: title + dispatcher attach
B3 iterm2, macos-terminal: osascript con il comando
B4 none: nessun comando, exit 0
B5 headless (niente DISPLAY) con backend grafico → skip silenzioso (T57)
B6 auto: rilevamento (garcon finto presente → chromeos; TERM_PROGRAM=iTerm.app → iterm2; niente → none)
B7 backend sconosciuto → exit 2; chromeos senza garcon → exit 3
B8 fake: registra la chiamata nel log
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
garcon = Path(tmp) / "garcon"
garcon.write_text("#!/bin/sh\n")
garcon.chmod(0o755)
DISPATCH = str(T.PLUGIN / "scripts" / "supervisor")


def run(backend, *args, display="wayland-0", extra=None, garcon_path=str(garcon)):
    cfg = tmp / f"config-{backend}.json"
    cfg.write_text(json.dumps({"language": "en", "accounts": {"default": {"config_dir": str(home / ".claude")}},
                               "terminal": {"backend": backend, "garcon": garcon_path}}))
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
           "CM_TERMINAL_DRY_RUN": "1"}
    if display:
        env["WAYLAND_DISPLAY"] = display
    if extra:
        env.update(extra)
    return subprocess.run([str(T.SCRIPTS / "cm-terminal.sh")] + list(args), capture_output=True, text=True, env=env, timeout=30)


r = run("chromeos", "open", "sito-com", "ephemeral")
T.check("B1 chromeos argv", r.returncode == 0 and r.stdout.strip() == f"{garcon} --client --terminal {DISPATCH} attach sito-com ephemeral", r.stdout + r.stderr)
T.check("B1 no dash-leading token after dispatcher", all(not t.startswith("-") for t in r.stdout.split()[3:]), r.stdout)
r = run("gnome", "open", "alfa", "ephemeral")
T.check("B2 gnome argv", r.stdout.strip() == f"gnome-terminal --title alfa -- {DISPATCH} attach alfa ephemeral", r.stdout + r.stderr)
r = run("kitty", "open", "alfa")
T.check("B2 kitty argv (no ephemeral)", r.stdout.strip() == f"kitty --title alfa {DISPATCH} attach alfa", r.stdout + r.stderr)
r = run("wt", "open", "alfa", "ephemeral")
T.check("B2 wt argv", r.stdout.strip() == f"wt.exe -w new nt --title alfa wsl.exe -e {DISPATCH} attach alfa ephemeral", r.stdout + r.stderr)
r = run("iterm2", "open", "alfa", "ephemeral")
argv = shlex.split(r.stdout)
T.check("B3 iterm2 osascript", argv[:2] == ["osascript", "-e"] and "iTerm2" in argv[2] and f'"{DISPATCH} attach alfa ephemeral"' in argv[2], r.stdout + r.stderr)
r = run("macos-terminal", "open", "alfa", "ephemeral")
argv = shlex.split(r.stdout)
T.check("B3 Terminal.app osascript", argv[:2] == ["osascript", "-e"] and 'application "Terminal" to do script' in argv[2] and "attach alfa ephemeral" in argv[2], r.stdout + r.stderr)
r = run("none", "open", "alfa", "ephemeral")
T.check("B4 none: no command, exit 0", r.returncode == 0 and "skip" in r.stdout, r.stdout + r.stderr)
r = run("chromeos", "open", "alfa", "ephemeral", display=None)
T.check("B5 headless skip for graphical backend", r.returncode == 0 and "headless" in r.stdout and "garcon" not in r.stdout, r.stdout + r.stderr)
r = run("auto", "detect")
T.check("B6 auto → chromeos when garcon present", r.stdout.strip() == "chromeos", r.stdout + r.stderr)
r = run("auto", "detect", garcon_path=str(tmp / "no-garcon"), extra={"TERM_PROGRAM": "iTerm.app"})
T.check("B6 auto → iterm2 from TERM_PROGRAM", r.stdout.strip() == "iterm2", r.stdout + r.stderr)
# PATH minimo: solo gli strumenti che lo script usa, nessun terminale
minibin = tmp / "minibin"
minibin.mkdir()
import shutil
for tool in ("bash", "env", "python3", "readlink", "dirname", "sed", "awk", "sort", "cut", "printf", "setsid", "tmux"):
    real = shutil.which(tool)
    if real:
        os.symlink(real, minibin / tool)
r = run("auto", "detect", garcon_path=str(tmp / "no-garcon"), extra={"PATH": str(minibin)})
T.check("B6 auto → none with nothing found", r.stdout.strip() == "none", r.stdout + r.stderr)
r = run("plan9", "open", "alfa")
T.check("B7 unknown backend → exit 2", r.returncode == 2, r.stdout + r.stderr)
r = run("chromeos", "open", "alfa", garcon_path=str(tmp / "no-garcon"))
T.check("B7 chromeos without garcon → exit 3 with message", r.returncode == 3 and "garcon" in r.stderr, r.stdout + r.stderr)
log = tmp / "fake.log"
r = run("fake", "open", "alfa", "ephemeral", extra={"CM_TERMINAL_FAKE_LOG": str(log), "CM_TERMINAL_DRY_RUN": "0"})
T.check("B8 fake backend logs the call", r.returncode == 0 and log.read_text().strip() == "open alfa ephemeral", r.stdout + r.stderr)

T.rm(str(tmp))
T.finish()
