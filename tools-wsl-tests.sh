#!/usr/bin/env bash
# tools-wsl-tests.sh — run the test suites of a checkout on win, inside WSL (author tooling, not shipped).
# The Chromebook stays light (global rule «Chromebook leggero»): the suites need tmux, which native Windows lacks,
# so they run in the Ubuntu-24.04 distro of win. Set up by the master on 08/10/2026.
#
#   bash tools-wsl-tests.sh [DIR] [SUITE...]   DIR = the checkout (default: this folder); SUITE = tests/x-verify.py
#                                               (default: every tests/*-verify.py). Exit 0 only if every suite is green.
#
# How: the tracked files of DIR (working tree, uncommitted changes included) go to win in a tarball with scp; a
# script in WSL unpacks them in ~/ts-tests/<name> (always fresh) and runs each suite with nice; its log comes back
# on stdout. Over ssh win answers in PowerShell 5.1, which breaks nested double quotes: so the WSL side is a file,
# run as `wsl -d Ubuntu-24.04 -- bash <file>`.
set -euo pipefail
DIR="$(cd "${1:-$(dirname "$0")}" && pwd)"; shift || true
NAME="$(basename "$DIR")"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
git -C "$DIR" ls-files -z | (cd "$DIR" && tar --null -T - -czf "$TMP/tree.tgz")
# release-verify runs the preflight, which compares observe/ with its source in ../claude-observe: ship it too
OBS="$(dirname "$DIR")/claude-observe"
if [ -d "$OBS/.git" ]; then git -C "$OBS" ls-files -z | (cd "$OBS" && tar --null -T - -czf "$TMP/obs.tgz"); fi
SUITES="${*:-}"
cat > "$TMP/run.sh" <<EOF
set -u
D="\$HOME/ts-tests/$NAME"; rm -rf "\$D"; mkdir -p "\$D"
tar -xzf /mnt/c/Users/Utente/ts-tests/$NAME.tgz -C "\$D"
if [ -f /mnt/c/Users/Utente/ts-tests/claude-observe.tgz ]; then
  O="\$HOME/ts-tests/claude-observe"; rm -rf "\$O"; mkdir -p "\$O"; tar -xzf /mnt/c/Users/Utente/ts-tests/claude-observe.tgz -C "\$O"
fi
cd "\$D"
suites="$SUITES"; [ -n "\$suites" ] || suites=\$(ls tests/*-verify.py)
fail=0
for t in \$suites; do
  echo "-- \$t"
  nice -n 10 timeout 1500 python3 "\$t" || { echo "rc \$?"; fail=1; }
done
echo "WSL-TESTS \$([ \$fail = 0 ] && echo GREEN || echo RED)"
exit \$fail
EOF
ssh win 'New-Item -ItemType Directory -Force -Path C:\Users\Utente\ts-tests | Out-Null'
scp -q "$TMP/tree.tgz" "win:C:/Users/Utente/ts-tests/$NAME.tgz"
scp -q "$TMP/run.sh" "win:C:/Users/Utente/ts-tests/$NAME.sh"
[ -f "$TMP/obs.tgz" ] && scp -q "$TMP/obs.tgz" "win:C:/Users/Utente/ts-tests/claude-observe.tgz"
ssh win "wsl -d Ubuntu-24.04 -- bash /mnt/c/Users/Utente/ts-tests/$NAME.sh"
