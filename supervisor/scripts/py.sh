#!/usr/bin/env bash
# py.sh script.py args… — runs a Python script with the first real Python 3.8+: python3, python, py -3.
# Same launcher as claude-observe's observe/py.sh (Windows test of 27/09/2026), with supervisor's names.
# On Windows `python3` is often the Microsoft Store alias: it exists (command -v succeeds) but exits 9009 (49 in Git
# Bash), so every candidate is run, never just looked up. PYTHONUTF8: Windows Python otherwise reads and writes cp1252.
# With no Python at all: one line on stderr and exit 0, so a hook never fails the session. The choice is cached
# (CC_SUPERVISOR_PY overrides it): hooks run every turn, and on Windows probing the Store alias alone costs ~0.2 s.
# 26/11/2026 (supervisor-directory-hook-credenziali-26-11, second measure of the directory's portal): one shell reads
# this text one way. No nested defaults, and the three interpreters are written out in a `case`, never run from a
# variable split into words; the cache holds python3, python or py (a cache with «py -3» is still read).
cache="${XDG_CACHE_HOME:-$HOME/.cache}/cc-supervisor/python"
over="${CC_SUPERVISOR_PY:-}"
[ -n "$over" ] || over="${TEAM_SUPERVISOR_PY:-}"
[ -n "$over" ] || over="${CLAUDE_MASTER_PY:-}"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
# 16/11/2026 (supervisor-hook-lenti-16-11): l'ora di partenza, per il tempo vero di un hook compreso l'avvio di bash
# e di python (bash 5: una variabile, nessun processo; altrove vuota, e l'hook conta da quando parte lui)
export CM_HOOK_T0="${EPOCHREALTIME:-}"
if [ -n "$over" ]; then
  case "$over" in
    "py -3") exec py -3 "$@" ;;
    *) exec "$over" "$@" ;;
  esac
fi
works() {
  case "$1" in
    python3) python3 -c 'import sys; assert sys.hexversion >= 0x03080000' > /dev/null 2> /dev/null ;;
    python) python -c 'import sys; assert sys.hexversion >= 0x03080000' > /dev/null 2> /dev/null ;;
    py) py -3 -c 'import sys; assert sys.hexversion >= 0x03080000' > /dev/null 2> /dev/null ;;
    *) return 1 ;;
  esac
}
pick=""
if [ -r "$cache" ]; then
  read -r pick < "$cache" || pick=""
  case "$pick" in
    "py -3") pick=py ;;
  esac
  case "$pick" in
    python3|python|py) command -v "$pick" > /dev/null 2> /dev/null || pick="" ;;   # uninstalled since: probe again
    *) pick="" ;;
  esac
fi
if [ -z "$pick" ]; then
  for c in python3 python py; do
    if works "$c"; then
      pick="$c"
      { mkdir -p "${cache%/*}" && echo "$pick" > "$cache"; } 2> /dev/null
      break
    fi
  done
fi
case "$pick" in
  python3) exec python3 "$@" ;;
  python) exec python "$@" ;;
  py) exec py -3 "$@" ;;
esac
echo "supervisor: no Python 3.8+ found, tried python3 python and py -3, it needs Python 3 and tmux on Linux macOS or WSL" >&2
exit 0
