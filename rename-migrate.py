#!/usr/bin/env python3
"""rename-migrate.py — one-off move of THIS workstation from claude-master to team-supervisor (author tooling,
not shipped; plan docs/plans/2026-10-07-rinomino-team-supervisor.md, point 8).

  python3 rename-migrate.py [--repo DIR]          prints what it would do (dry-run, default)
  python3 rename-migrate.py --yes [--repo DIR]    does it

--repo is the checkout whose team-supervisor/ becomes plugin_root (default: the folder of this file).
Every file rewritten gets a .bak-<stamp>-rename copy first; every moved folder leaves a symlink at the old path
(until 21/10/2026), so sessions still running the old plugin keep working. Idempotent: a step already done is
skipped. Plugin reinstall, relay restart and the folder rename are separate steps of the handoff.

Test hooks: CM_HOME (fake home), CM_CRONTAB_CMD.
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

YES = "--yes" in sys.argv
REPO = Path(sys.argv[sys.argv.index("--repo") + 1]).resolve() if "--repo" in sys.argv else Path(__file__).resolve().parent
H = Path(os.environ.get("CM_HOME") or Path.home())
CRONTAB = os.environ.get("CM_CRONTAB_CMD", "crontab")
STAMP = time.strftime("%Y%m%d-%H%M")
NEW_CFG = H / ".config" / "team-supervisor" / "config.json"
DISPATCH = REPO / "team-supervisor" / "scripts" / "team-supervisor"


def say(step, what):
    print(f"  [{step}] {what}" + ("" if YES else "  (dry-run)"))


def backup(p):
    b = p.with_name(p.name + f".bak-{STAMP}-rename")
    if YES:
        shutil.copy2(p, b)
    return b


def move_dirs():
    for old_rel, new_rel in [(".config/claude-master", ".config/team-supervisor"), (".claude-master", ".team-supervisor"),
                             (".local/state/claude-master", ".local/state/team-supervisor"),
                             (".cache/claude-master", ".cache/team-supervisor")]:
        old, new = H / old_rel, H / new_rel
        if old.is_symlink() or not old.is_dir():
            say("dirs", f"{old}: nothing to move")
            continue
        if new.exists():
            sys.exit(f"FAIL: {new} already exists next to a real {old}: nothing moved")
        say("dirs", f"{old} -> {new}, symlink at the old path")
        if YES:
            old.rename(new)
            old.symlink_to(new)


def rewrite_config():
    cfg = NEW_CFG if NEW_CFG.exists() else H / ".config" / "claude-master" / "config.json"
    s = cfg.read_text()
    t = s.replace("/.claude-master/", "/.team-supervisor/")
    t = re.sub(r"personali/claude-master(?=[/\"])", "personali/" + REPO.name, t)
    t = re.sub(r'("plugin_root":\s*)"[^"]*"', lambda m: m.group(1) + '"' + str(REPO / "team-supervisor") + '"', t)
    if t == s:
        say("config", f"{cfg}: already rewritten")
        return
    for a, b in zip(s.splitlines(), t.splitlines()):
        if a != b:
            say("config", f"{a.strip()}  =>  {b.strip()}")
    if YES:
        backup(cfg)
        cfg.write_text(t)


def launchers():
    say("shim", f"{DISPATCH} init --shim --shell (writes ~/.local/bin/team-supervisor and ~/.config/team-supervisor/shell.sh)")
    if YES:
        env = dict(os.environ, TEAM_SUPERVISOR_ROOT=str(REPO / "team-supervisor"))
        for flag in ("--shim", "--shell"):
            subprocess.run([str(DISPATCH), "init", flag], env=env, check=True)
    old = H / ".local" / "bin" / "claude-master"
    alias = '#!/usr/bin/env bash\n# claude-master: alias of team-supervisor on this workstation only, until 21/10/2026.\n' \
            'exec "$HOME/.local/bin/team-supervisor" "$@"\n'
    if old.exists() and old.read_text() == alias:
        say("shim", f"{old}: already the alias")
        return
    say("shim", f"{old} -> alias that execs team-supervisor")
    if YES:
        if old.exists():
            backup(old)
        old.write_text(alias)
        old.chmod(0o755)


def bashrc():
    p = H / ".bashrc"
    s = p.read_text() if p.exists() else ""
    t = s.replace(".config/claude-master/shell.sh", ".config/team-supervisor/shell.sh") \
         .replace("# >>> claude-master", "# >>> team-supervisor").replace("# <<< claude-master <<<", "# <<< team-supervisor <<<")
    if t == s:
        say("bashrc", "nothing to change")
        return
    say("bashrc", "source ~/.config/team-supervisor/shell.sh")
    if YES:
        backup(p)
        p.write_text(t)


def cron():
    cur = subprocess.run([CRONTAB, "-l"], capture_output=True, text=True).stdout
    new = cur.replace("/.local/bin/claude-master ", "/.local/bin/team-supervisor ").replace("# claude-master", "# team-supervisor")
    if new == cur:
        say("cron", "nothing to change")
        return
    n = sum(1 for a, b in zip(cur.splitlines(), new.splitlines()) if a != b)
    say("cron", f"{n} lines -> ~/.local/bin/team-supervisor")
    if YES:
        (H / f".crontab.bak-{STAMP}-rename").write_text(cur)
        subprocess.run([CRONTAB, "-"], input=new, text=True, check=True)


def main():
    if not DISPATCH.is_file():
        sys.exit(f"FAIL: {DISPATCH} missing (is --repo the renamed checkout?)")
    print(f"== rename-migrate ({'yes' if YES else 'dry-run'}), repo {REPO}")
    move_dirs()
    rewrite_config()
    launchers()
    bashrc()
    cron()
    print("== done" if YES else "== dry-run: nothing written (--yes to apply)")


if __name__ == "__main__":
    main()
