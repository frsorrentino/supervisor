#!/usr/bin/env python3
"""rename-supervisor.py — one-off rename of THIS repository from team-supervisor to supervisor (author tooling, not
shipped; plan docs/plans/2026-10-07-rinomino-supervisor.md, points 1-6).

  python3 rename-supervisor.py            prints the files it would change and how many replacements (dry-run)
  python3 rename-supervisor.py --yes      rewrites the tracked files and moves the renamed paths with `git mv`

Kept on purpose (the plan, «Cosa resta col nome vecchio»): docs/, the CHANGELOG history, the contract fixtures in
tests/fixtures/relay (the app owns them and sends the new texts), the night report schema id
`team-supervisor/night-report` (a contract value), and every `claude-master` name. The fallbacks to the old
variables and to the old prompt prefixes are written by hand after this script.
"""
import re
import subprocess
import sys

YES = "--yes" in sys.argv
SKIP = ("docs/", "tests/fixtures/relay/", "CHANGELOG.md", "rename-supervisor.py", "rename-migrate.py",
        "team-supervisor/observe/", "supervisor/observe/")   # la copia di observe si rigenera con sync.py
KEEP = "team-supervisor/night-report"
RULES = [
    (r"TEAM_SUPERVISOR_", "CC_SUPERVISOR_"),
    (r"TEAM-SUPERVISOR", "SUPERVISOR"),
    (r"Team Supervisor", "Supervisor"),
    (r"team-supervisor-app", "supervisor-app"),
    (r"team-supervisor-dev", "supervisor-dev"),
    (r"(\.config|\.local/state|\.cache)/team-supervisor", r"\1/cc-supervisor"),
    (r"(?<![\w-])\.team-supervisor", ".cc-supervisor"),
    (r"team-supervisor", "supervisor"),
]
MOVES = [("team-supervisor/bin/team-supervisor", "team-supervisor/bin/supervisor"),
         ("team-supervisor/scripts/team-supervisor", "team-supervisor/scripts/supervisor"),
         ("team-supervisor/shell/team-supervisor.sh", "team-supervisor/shell/supervisor.sh"),
         ("team-supervisor", "supervisor")]   # i file dentro prima, la cartella del plugin per ultima


def files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()
    return [f for f in out if not f.startswith(SKIP)]


def rewrite(text):
    text = text.replace(KEEP, "\0KEEP\0")
    n = 0
    for pat, rep in RULES:
        text, k = re.subn(pat, rep, text)
        n += k
    return text.replace("\0KEEP\0", KEEP), n


total = 0
for f in files():
    try:
        with open(f, encoding="utf-8") as fh:
            old = fh.read()
    except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
        continue
    new, n = rewrite(old)
    if n:
        total += n
        print(f"{n:5}  {f}")
        if YES:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(new)
print(f"{total} replacements" + ("" if YES else " (dry-run)"))
if YES:
    for src, dst in MOVES:
        subprocess.run(["git", "mv", src, dst], check=True)
