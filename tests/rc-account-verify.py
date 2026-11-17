#!/usr/bin/env python3
"""Remote Control per account sui due percorsi che lo ignoravano (17/11/2026, supervisor-rc-per-account-17-11).

accounts.<account>.remote_control=false valeva solo per `launch` in tmux (cm-launch.sh). Il comando di shell
dell'account (shell/supervisor.sh, _cm_wrap: «claude-pixel») leggeva solo session.remote_control, e
`launch --host win` (cm-rsession.py) aggiungeva sempre --remote-control: le sessioni dell'account condiviso coi
colleghi partivano con Remote Control acceso.

RA1 supervisor.sh: il wrapper dell'account con remote_control false apre la sessione tmux senza --remote-control;
    quello dell'account senza la chiave lo mette (session.remote_control true); con la chiave true anche
RA2 cm-rsession.py: launch --host win dell'account con remote_control false parte la' senza --remote-control e senza
    aspettare un link; l'account senza la chiave lo mette, con il link
"""
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
for d in (".claude/sessions", ".claude-pixel/sessions", "bin", "ws/personali/alfa", "ws/pro/beta"):
    (home / d).mkdir(parents=True, exist_ok=True)
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
os.symlink(FAKE, home / "bin" / "claude")
winroot = tmp / "win"
winroot.mkdir()
WIN = {"kind": "windows-native", "transport": {"ssh": "win"}, "confirmed_at": "2026-10-01T23:50:00+02:00",
       "roles": ["compute", "render", "sessions"],
       "trust": {"level": "work", "accounts": ["personale", "professionale"], "secrets": False, "clients": False},
       "sessions": {"root": "W:/cm", "allow": ["personali/*", "pro/*"], "deny": [], "start_timeout_s": 4}}
cfg = tmp / "config.json"
base_cfg = {
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-", "remote_control": False}},
    "default_account": "personale",
    "folder_map": [{"path": str(ws / "pro"), "account": "professionale"}, {"path": str(ws / "personali"), "account": "personale"}],
    "session": {"claude_args": ["--dangerously-skip-permissions"], "startup_timeout_s": 25, "death_check_s": 1},
    "shell": {"wrappers": {"claude": "personale", "claude-pixel": "professionale"}},
    "terminal": {"backend": "none"},
    "registry": {"file": str(tmp / "registry.json")},
    "sessions": {"max_sessions": 0},
    "scheduler": {"client_paths": [], "session_offload": {"load_per_core": 1.0, "free_ram_gb": 2}},
    "tabs": {"color_registry": str(tmp / "colors")},
    "hosts": {"win": WIN},
}
cfg.write_text(json.dumps(base_cfg))
SH = T.PLUGIN / "shell" / "supervisor.sh"
argslog = tmp / "args.log"
FAKE_AD = T.ROOT / "tests" / "lib" / "fake-rsession-adapter.py"   # l'host finto di cm-rsession.py
CRON = tmp / "crontab"
CRON.write_text("")
FAKE_CRON = tmp / "crontab.sh"
FAKE_CRON.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (CRON, CRON, CRON, CRON))
FAKE_CRON.chmod(0o755)


def env(**extra):
    e = {"PATH": f"{home}/bin:{os.environ['PATH']}", "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CM_RSESSION_ADAPTER": str(FAKE_AD), "FAKE_WIN_ROOT": str(winroot),
         "CM_CRONTAB_CMD": str(FAKE_CRON), "CM_LOADAVG": "0.1", "CM_NPROC": "8", "CM_MEMAVAIL_GB": "5", "CM_PROC_SCAN_PIDS": "",
         "CM_LAUNCH_NO_TTY": "1", "TERM": "xterm-256color", "LC_ALL": "C", "LANGUAGE": "",
         "GIT_AUTHOR_NAME": "Prova", "GIT_AUTHOR_EMAIL": "prova@example.com", "GIT_COMMITTER_NAME": "Prova",
         "GIT_COMMITTER_EMAIL": "prova@example.com"}
    e.update(extra)
    return e


def g(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, env=env(), check=True).stdout


def repo(d):
    g(d, "init", "-q")
    (d / "README.md").write_text(d.name + "\n")
    g(d, "add", "README.md")
    g(d, "commit", "-q", "-m", "inizio")


def calls():
    p = winroot / "calls.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


# RA1: il comando di shell dell'account (supervisor.sh, _cm_wrap), con un tty per l'attach dentro un runner tmux
with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "seed", "bash", "--norc"], env=env(FAKE_CLAUDE_ARGS_LOG=str(argslog), FAKE_CLAUDE_SCENARIO="plain"), check=True)

    def tmx(*a):
        return subprocess.run(["tmux", "-L", tm.socket, *a], capture_output=True, text=True, env=env())

    def wrap(cmd, where, name):
        argslog.unlink(missing_ok=True)
        runner = (f"env -u TMUX PATH='{home}/bin:{os.environ['PATH']}' HOME='{home}' CM_HOME='{home}' CC_SUPERVISOR_CONFIG='{cfg}' "
                  f"CM_TMUX_ARGS='-L {tm.socket}' FAKE_CLAUDE_ARGS_LOG='{argslog}' bash -c 'source \"{SH}\"; cd \"{where}\"; {cmd}'; sleep 4")
        tmx("new-session", "-d", "-s", "runner", "-x", "100", "-y", "24", runner)
        ok = T.wait_until(lambda: tmx("has-session", "-t", f"={name}").returncode == 0 and argslog.exists()
                          and f"-n {name}" in (argslog.read_text().splitlines() or [""])[-1], timeout=15)
        last = argslog.read_text().splitlines()[-1] if argslog.exists() else ""
        pane = tmx("capture-pane", "-p", "-t", "runner").stdout[-300:]
        tmx("kill-session", "-t", "=runner")
        tmx("kill-session", "-t", f"={name}")
        return ok, last, pane

    ok, last, pane = wrap("claude-pixel", ws / "pro" / "beta", "pix-beta")
    T.check("RA1 supervisor.sh: claude-pixel (account with remote_control false) opens its tmux session without --remote-control",
            ok and "-n pix-beta" in last and "--remote-control" not in last, f"ok={ok} «{last}» {pane}")
    ok, last, pane = wrap("claude", ws / "personali" / "alfa", "alfa")
    T.check("RA1 supervisor.sh: claude (account without the key) keeps session.remote_control true: --remote-control alfa",
            ok and "--remote-control alfa" in last, f"ok={ok} «{last}» {pane}")
    base_cfg["accounts"]["professionale"]["remote_control"] = True
    cfg.write_text(json.dumps(base_cfg))
    ok, last, pane = wrap("claude-pixel", ws / "pro" / "beta", "pix-beta")
    T.check("RA1 supervisor.sh: with the key true claude-pixel has --remote-control pix-beta",
            ok and "--remote-control pix-beta" in last, f"ok={ok} «{last}» {pane}")
    base_cfg["accounts"]["professionale"]["remote_control"] = False
    cfg.write_text(json.dumps(base_cfg))

# RA2: launch --host win (cm-launch.sh → cm-rsession.py)
alfa, beta = ws / "personali" / "alfa", ws / "pro" / "beta"
repo(alfa)
repo(beta)


def launch(*args, **extra):
    return subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), *args], capture_output=True, text=True, env=env(**extra), timeout=120)


t0 = time.time()
r = launch(str(beta), "--host", "win", "--no-window")
dt = time.time() - t0
s = [c for c in calls() if c["verb"] == "start"]
T.check("RA2 cm-rsession.py: launch --host win of the account with remote_control false starts there without --remote-control, with -n, without waiting for a link that never comes",
        r.returncode == 0 and s and "--remote-control" not in s[0]["args"] and "-n" in s[0]["args"] and dt < 4 and "senza Remote Control" in r.stdout,
        f"rc={r.returncode} {dt:.1f}s {json.dumps(s)} {r.stdout[-300:]} {r.stderr[-300:]}")
n = len(calls())
r = launch(str(alfa), "--host", "win", "--no-window")
s = [c for c in calls()[n:] if c["verb"] == "start"]
T.check("RA2 cm-rsession.py: the account without the key keeps --remote-control, and the link",
        r.returncode == 0 and s and "--remote-control" in s[0]["args"] and "claude.ai/code/" in r.stdout, f"{json.dumps(s)} {r.stdout[-300:]} {r.stderr[-300:]}")

T.rm(tmp)
T.finish()
