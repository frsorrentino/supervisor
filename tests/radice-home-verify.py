#!/usr/bin/env python3
"""Mai la home come cartella della master (19/11/2026, supervisor-radice-home-19-11; scelta A del maintainer alle 23:00). Prova vera
in sup-prova: un'installazione nuova sceglieva la home («default (~)») quando non c'erano ~/workspaces, ~/Projects...; li'
Claude Code chiede la fiducia a ogni avvio («Yes lasts for this session only») e la master avviata senza mani (servizio
d'avvio, «Riapri», launch dall'app, --bg) restava ferma.

RH1 detect su una home vuota: la radice proposta e' ~/workspaces, non la home
RH2 detect con sessioni in due cartelle della home (antenato comune = la home): la radice non e' la home
RH3 setup --root <la home> e' rifiutato (exit 2) col motivo e la cartella da usare, prima di scrivere qualcosa
RH4 setup --yes --dry-run senza --root su una home vuota propone ~/workspaces e non crea niente
RH5 doctor con la radice nella home: un WARN che dice perche' e il comando per cambiarla
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (home / ".claude", tmp / "bin", tmp / "run", home / "a", home / "b"):
    d.mkdir(parents=True, exist_ok=True)
(home / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark"}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
claude = tmp / "bin" / "claude"
claude.write_text('#!/bin/sh\ncase "$1" in --version) echo "2.1.320 (Claude Code)"; exit 0 ;; plugin) echo "[]"; exit 0 ;;\n'
                  '  auth) echo \'{"loggedIn": true}\'; exit 0 ;; esac\n'
                  f'exec {FAKE} "$@"\n')
claude.chmod(0o755)
fake_cron = tmp / "bin" / "fake-crontab"
fake_cron.write_text(f'#!/bin/sh\nif [ "$1" = "-l" ]; then cat {tmp}/crontab.txt 2>/dev/null; exit 0; fi\ncat > {tmp}/crontab.txt\n')
fake_cron.chmod(0o755)
import shutil  # noqa: E402
_tmux_dir = os.path.dirname(shutil.which("tmux") or "/usr/bin/tmux")
ENV = {"PATH": f"{tmp / 'bin'}:{_tmux_dir}:/usr/local/bin:/usr/bin:/bin", "HOME": str(home), "CM_HOME": str(home), "SHELL": "/bin/bash",
       "LANG": "it_IT.UTF-8", "CM_CRONTAB_CMD": str(fake_cron), "CM_CLAUDE_BIN": str(FAKE), "FAKE_CLAUDE_SCENARIO": "plain",
       "CM_TMUX_ARGS": "-L radice-home", "TMUX_TMPDIR": str(tmp / "run"), "XDG_RUNTIME_DIR": str(tmp / "run"),
       "CM_RELAY_OPEN": "true", "TZ": "Europe/Rome", "CM_SETUP_CRON_RUNNING": "1", "CM_FIREBASE_CMD": str(tmp / "bin" / "no-firebase")}
CLI = str(T.SCRIPTS / "supervisor")
conf = home / ".config" / "cc-supervisor" / "config.json"


def detect(procs):
    machine = tmp / "machine.json"
    machine.write_text(json.dumps({"platform": "linux", "procs": procs, "env": {"LANG": "it_IT.UTF-8"}}))
    code = ("import importlib.util,json,sys\n"
            f"spec=importlib.util.spec_from_file_location('c',{str(T.SCRIPTS / 'cm-config.py')!r})\n"
            "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "c,s=m.detect(m.Machine()); print(json.dumps({'root': c['workspace']['root'], 'src': s.get('workspace.root')}))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(ENV, CM_FAKE_MACHINE=str(machine)), timeout=60)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"err": r.stderr[-400:]}


d1 = detect([])
T.check("RH1 detect on an empty home: the proposed root is ~/workspaces, not the home", d1.get("root") == "~/workspaces", json.dumps(d1))
procs = [{"pid": 11, "cmdline": "claude", "environ": {}, "cwd": str(home / "a"), "tmux": "a"},
         {"pid": 12, "cmdline": "claude", "environ": {}, "cwd": str(home / "b"), "tmux": "b"}]
d2 = detect(procs)
T.check("RH2 detect with sessions in two home folders (common ancestor = the home): the root is not the home",
        d2.get("root") not in ("~", str(home), None) and "err" not in d2, json.dumps(d2))

r3 = subprocess.run([CLI, "setup", "--yes", "--root", str(home)], capture_output=True, text=True, env=ENV, timeout=300, stdin=subprocess.DEVNULL)
T.check("RH3 setup --root <home> is refused (exit 2) with the reason and the folder to use, before writing anything",
        r3.returncode == 2 and "fiducia" in r3.stderr and "~/workspaces" in r3.stderr and not conf.exists(), r3.stdout[-300:] + r3.stderr[-400:])

r4 = subprocess.run([CLI, "setup", "--yes", "--dry-run"], capture_output=True, text=True, env=ENV, timeout=300, stdin=subprocess.DEVNULL)
T.check("RH4 setup --yes --dry-run with no --root on an empty home proposes ~/workspaces and creates nothing",
        r4.returncode == 0 and "workspaces" in r4.stdout and not (home / "workspaces").exists() and not conf.exists(),
        r4.stdout[-500:] + r4.stderr[-300:])

cfg = tmp / "config-home.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "workspace": {"root": "~", "root_session_name": "master"},
                           "accounts": {"personale": {"config_dir": str(home / ".claude")}}, "default_account": "personale",
                           "terminal": {"backend": "none"}}))
r5 = subprocess.run([CLI, "doctor"], capture_output=True, text=True, env=dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg)), timeout=300, stdin=subprocess.DEVNULL)
out5 = r5.stdout + r5.stderr
T.check("RH5 doctor with the root in the home: a WARN that says why and the command to change it",
        "WARN" in out5 and "fiducia" in out5 and "setup --root" in out5, out5[-800:])
T.finish()
