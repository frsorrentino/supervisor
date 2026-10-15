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
# ssh and scp can be replaced (tests/wsl-tools-verify.py does, with fakes)
SSH="${WSL_TESTS_SSH:-ssh}"; SCP="${WSL_TESTS_SCP:-scp}"
$SSH win 'New-Item -ItemType Directory -Force -Path C:\Users\Utente\ts-tests | Out-Null'
$SCP -q "$TMP/tree.tgz" "win:C:/Users/Utente/ts-tests/$NAME.tgz"
$SCP -q "$TMP/run.sh" "win:C:/Users/Utente/ts-tests/$NAME.sh"
[ -f "$TMP/obs.tgz" ] && $SCP -q "$TMP/obs.tgz" "win:C:/Users/Utente/ts-tests/claude-observe.tgz"
# 15/10/2026: WSL that does not start (Wsl/Service/CreateInstance/CreateVm/ERROR_TIMEOUT) printed its error and the
# script exited 0, with no suite run: a green without tests. The verdict now comes from the last line only: GREEN → 0,
# RED → 1, anything else (WSL down, ssh cut, run.sh never reached) → 3 with the reason.
set +e
$SSH win "wsl -d Ubuntu-24.04 -- bash /mnt/c/Users/Utente/ts-tests/$NAME.sh" 2>&1 | tr -d '\000\r' | tee "$TMP/out.log"
rc=${PIPESTATUS[0]}
set -e
last="$(grep '^WSL-TESTS ' "$TMP/out.log" | tail -1 || true)"
case "$last" in
  "WSL-TESTS GREEN") exit 0 ;;
  "WSL-TESTS RED") exit 1 ;;
esac
why="$(grep -v '^[[:space:]]*$' "$TMP/out.log" | head -2 | tr '\n' ' ' || true)"
echo "WSL-TESTS NO RESULT: no suite verdict from win (ssh exit $rc)${why:+: $why}" >&2
exit 3
