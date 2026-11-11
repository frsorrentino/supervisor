#!/usr/bin/env bash
# detach.sh script.py args… — runs a py.sh hook command detached from the hook that started it (stdin handed over).
#
# Why: since Claude Code 2.1.287 every session.end hook shares one 1.5 s wall-clock bound, and Python alone takes
# 0.7-1 s to start; cm-hook.py SessionEnd was «cancelled» in 2 `claude -p` runs out of about 8 (fable-director,
# 01/10/2026, fixed there the same way in 8def2c8). stdin goes to a temp file, the work runs in a new session (setsid
# where it exists) and this wrapper returns in milliseconds: a process the hook let go of outlives the bound.
# One more step than fable-director's: SessionEnd names the tmux session from $TMUX_PANE (an /exit leaves the «last
# good set»), and after /exit the pane can be gone before the detached child asks; the name is read here, before
# detaching, and handed over in CM_HOOK_TMUX_NAME.
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ -n "${TMUX_PANE:-}" ] && [ -z "${CM_HOOK_TMUX_NAME:-}" ]; then
  CM_HOOK_TMUX_NAME=$("${CM_TMUX_BIN:-tmux}" ${CM_TMUX_ARGS:-} display-message -p -t "$TMUX_PANE" '#{session_name}' 2>/dev/null)
  export CM_HOOK_TMUX_NAME
fi
tmp=$(mktemp "${TMPDIR:-/tmp}/cm-detach.XXXXXX") || exit 0
cat > "$tmp"
run() { bash "$here/py.sh" "$@" < "$tmp" > /dev/null 2>&1; rm -f "$tmp"; }
if command -v setsid > /dev/null 2>&1; then
  export -f run 2> /dev/null
  export here tmp
  setsid bash -c 'run "$@"' _ "$@" < /dev/null > /dev/null 2>&1 &
else
  ( run "$@" & ) < /dev/null > /dev/null 2>&1
fi
exit 0
