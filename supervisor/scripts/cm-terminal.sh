#!/usr/bin/env bash
# supervisor — apertura di una finestra di terminale attaccata a una sessione tmux.
#
#   cm-terminal.sh open <nome> [ephemeral]   apre la finestra (backend da config, `auto` = rilevato)
#   cm-terminal.sh detect                    stampa il backend che userebbe
#
# Backend: chromeos (garcon, Terminale di ChromeOS) · gnome (gnome-terminal) ·
# kitty · iterm2 · macos-terminal · wt (Windows Terminal + WSL) · none ·
# fake (test: registra la chiamata in CM_TERMINAL_FAKE_LOG, e con
# CM_TERMINAL_FAKE_ATTACH=1 attacca davvero un client tmux su uno pty).
#
# Ogni backend esegue `supervisor attach <nome> [ephemeral]` (il dispatcher
# del plugin): argomenti senza trattini, cosi' passano integri dal parser di
# garcon (T6). Senza display (cron, headless) si salta in silenzio (T57).
# CM_TERMINAL_DRY_RUN=1 stampa il comando invece di eseguirlo (test D8: i
# backend non provabili qui si verificano sull'argv).
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"
DISPATCH="$CM_PLUGIN_DIR/scripts/supervisor"

cm_terminal_detect() {
  case "${CM_TERMINAL_BACKEND:-auto}" in
    auto) ;;
    *) printf '%s' "$CM_TERMINAL_BACKEND"; return ;;
  esac
  if [ -x "$CM_TERMINAL_GARCON" ]; then printf chromeos
  elif [ "${TERM_PROGRAM:-}" = "iTerm.app" ]; then printf iterm2
  elif [ "${TERM_PROGRAM:-}" = "Apple_Terminal" ]; then printf macos-terminal
  elif [ -n "${KITTY_WINDOW_ID:-}" ] || command -v kitty >/dev/null 2>&1; then printf kitty
  elif [ -n "${WT_SESSION:-}" ] || command -v wt.exe >/dev/null 2>&1; then printf wt
  elif command -v gnome-terminal >/dev/null 2>&1; then printf gnome
  else printf none; fi
}

run_detached() {  # esegue staccato dal terminale corrente, oppure stampa (dry run)
  if [ "${CM_TERMINAL_DRY_RUN:-}" = 1 ]; then printf '%q ' "$@"; echo; return 0; fi
  setsid "$@" >/dev/null 2>&1 < /dev/null &
}

cm_terminal_open() {
  local nome="$1" mode="${2:-}" be
  be="$(cm_terminal_detect)"
  case "$be" in
    chromeos|gnome|kitty)
      if [ -z "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] && [ "$CM_TERMINAL_HEADLESS_SKIP" = true ]; then
        [ "${CM_TERMINAL_DRY_RUN:-}" = 1 ] && echo "skip: headless"
        return 0
      fi ;;
  esac
  case "$be" in
    chromeos)
      [ -x "$CM_TERMINAL_GARCON" ] || { cm_msg terminal.backend_missing "backend=chromeos" "bin=$CM_TERMINAL_GARCON" >&2; return 3; }
      # «sempre tutte schede» (terminal.open_as_tab, 11/09/2026): prima come scheda di una finestra
      # gia' aperta (cm-tile.py open-tab: duplicazione + segnaposto; senza finestre ne apre UNA semplice);
      # garcon col comando attach solo senza bridge (exit 5), con open_as_tab false, o quando
      # `tile` vuole apposta una finestra app nuova (CM_TERMINAL_FORCE_WINDOW=1)
      if [ "${CM_TERMINAL_OPEN_AS_TAB:-true}" = true ] && [ "${CM_TERMINAL_FORCE_WINDOW:-}" != 1 ] && [ "${CM_TERMINAL_DRY_RUN:-}" != 1 ]; then
        python3 "$CM_SCRIPTS/cm-tile.py" open-tab "$nome" ${mode:+"$mode"}; local rc=$?
        [ "$rc" -eq 0 ] && return 0
        [ "$rc" -eq 5 ] || return "$rc"
      fi
      run_detached "$CM_TERMINAL_GARCON" --client --terminal "$DISPATCH" attach "$nome" ${mode:+"$mode"} ;;
    gnome)
      run_detached gnome-terminal --title "$nome" -- "$DISPATCH" attach "$nome" ${mode:+"$mode"} ;;
    kitty)
      run_detached kitty --title "$nome" "$DISPATCH" attach "$nome" ${mode:+"$mode"} ;;
    iterm2)
      run_detached osascript -e "tell application \"iTerm2\" to create window with default profile command \"$DISPATCH attach $nome ${mode}\"" ;;
    macos-terminal)
      run_detached osascript -e "tell application \"Terminal\" to do script \"$DISPATCH attach $nome ${mode}\"" ;;
    wt)
      run_detached wt.exe -w new nt --title "$nome" wsl.exe -e "$DISPATCH" attach "$nome" ${mode:+"$mode"} ;;
    none)
      [ "${CM_TERMINAL_DRY_RUN:-}" = 1 ] && echo "skip: backend none"
      return 0 ;;
    fake)
      printf 'open %s %s\n' "$nome" "$mode" >> "${CM_TERMINAL_FAKE_LOG:-/dev/null}"
      if [ "${CM_TERMINAL_FAKE_ATTACH:-}" = 1 ]; then
        # staccato e senza stdout/stderr ereditati: altrimenti chi ha lanciato
        # `open` resta appeso finche' il client tmux finto non muore
        setsid python3 - "$nome" "$mode" "$CM_SCRIPTS" >/dev/null 2>&1 <<'PY' &
import os, pty, select, subprocess, sys, time
nome, mode, scripts = sys.argv[1], sys.argv[2], sys.argv[3]
m, s = pty.openpty()
env = dict(os.environ); env.pop("TMUX", None)
env.setdefault("TERM", "xterm-256color")   # senza TERM il client tmux rifiuta: «terminal does not support clear»
p = subprocess.Popen([f"{scripts}/cm-attach.sh", nome] + ([mode] if mode else []), stdin=s, stdout=s, stderr=s,
                     start_new_session=True, env=env)
# 11/10: il lato slave chiuso qui e la lettura con un limite di tempo. Prima un os.read bloccato sul master
# non vedeva morire il client tmux e questo processo restava acceso per giorni (22 rimasti dalle suite)
os.close(s)
log = os.environ.get("CM_TERMINAL_FAKE_LOG", "/dev/null") + ".attach"
with open(log, "ab") as f:
    while p.poll() is None:
        if not select.select([m], [], [], 0.5)[0]:
            continue
        try:
            f.write(os.read(m, 4096)); f.flush()
        except OSError:
            time.sleep(0.2)
PY
      fi
      return 0 ;;
    *)
      cm_msg terminal.backend_unknown "backend=$be" >&2; return 2 ;;
  esac
}

case "${1:-}" in
  open)   shift; cm_terminal_open "$@" ;;
  detect) cm_terminal_detect; echo ;;
  *) echo "uso: cm-terminal.sh open <nome> [ephemeral] | detect" >&2; exit 2 ;;
esac
