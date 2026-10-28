#!/usr/bin/env bash
# Funzioni comuni degli script bash di supervisor. Si fa `source`.
#
# Carica la configurazione UNA volta (export CM_* + MSG_*), poi:
#   cm_tmux ...              tmux con il socket giusto (CM_TMUX_ARGS nei test, tmux.socket in config)
#   cm_account_of_name NOME  account dal prefisso tmux (prefisso piu' lungo vince; "" = quello senza prefisso)
#   cm_get ACCOUNT CAMPO     campo di un account (CONFIG_DIR, TMUX_PREFIX, SHAPE, SHELL_COMMAND, LABEL)
#   cm_msg CHIAVE k=v ...    messaggio formattato nella lingua configurata
#   cm_orphan_hook           hook tmux session-closed: chiude i claude rimasti senza pannello (28/10/2026)
CM_SCRIPTS="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# T81 (11/09/2026, dal polso dell'utente): dal cron PATH=/usr/bin:/bin e `claude` (link in ~/.local/bin)
# non si trova: il bot rispondeva «AVVIO FALLITO». Ogni script passa di qui: si antepone una volta.
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:/usr/local/bin:$PATH" ;;
esac
# Una volta per catena di script, ma legata a QUALE config: la chiave e' file, data di modifica e home.
# Con il solo «1» (fino al 14/09/2026) le CM_* passavano dal server tmux, partito da uno script al boot, a
# ogni sessione e ai suoi figli: un test con la sua config e il suo tmux scriveva nel registro colori vero
# e potava le sessioni vive (schede e polso con colori diversi); e una config cambiata restava invisibile.
_cm_cfg="${CC_SUPERVISOR_CONFIG:-${TEAM_SUPERVISOR_CONFIG:-${CLAUDE_MASTER_CONFIG:-${CM_HOME:-$HOME}/.config/cc-supervisor/config.json}}}"
_cm_key="$_cm_cfg|$(date -r "$_cm_cfg" +%s 2>/dev/null)|${CM_HOME:-}|$HOME"
if [ "${CM_CONFIG_LOADED:-}" != "$_cm_key" ]; then
  eval "$(python3 "$CM_SCRIPTS/cm-config.py" --sh --messages)"
  export CM_CONFIG_LOADED="$_cm_key"
fi
unset _cm_cfg _cm_key

cm_tmux() {
  # shellcheck disable=SC2086
  if [ -n "${CM_TMUX_ARGS:-}" ]; then tmux $CM_TMUX_ARGS "$@"
  elif [ -n "${CM_TMUX_SOCKET:-}" ]; then tmux -L "$CM_TMUX_SOCKET" "$@"
  else tmux "$@"; fi
}

cm_orphan_hook() {  # l'hook session-closed del server tmux: alla morte di una sessione si chiudono i claude rimasti senza pannello
  # 28/10/2026: il claude della master (PID 13277) e' rimasto vivo e occupato per 52 minuti dopo che il suo tmux era
  # stato rifatto, col suo Remote Control «master» registrato; SIGHUP e SIGTERM non lo fermavano. L'hook e' globale
  # e con un indice suo (73), per non togliere quelli della persona; si riscrive a ogni launch (idempotente). L'uscita
  # va in state_dir/orphans.log: quella di run-shell comparirebbe nel pannello attivo.
  local cfg="${CC_SUPERVISOR_CONFIG:-${TEAM_SUPERVISOR_CONFIG:-${CLAUDE_MASTER_CONFIG:-${CM_HOME:-$HOME}/.config/cc-supervisor/config.json}}}"
  local envs="HOME='$HOME' CC_SUPERVISOR_CONFIG='$cfg' CM_TMUX_ARGS='-S #{socket_path}'"
  [ -n "${CM_HOME:-}" ] && envs="$envs CM_HOME='$CM_HOME'"
  cm_tmux set-hook -g 'session-closed[73]' \
    "run-shell -b \"env $envs python3 '$CM_SCRIPTS/cm-sessions.py' --reap-orphans --now >/dev/null 2>&1\"" 2>/dev/null || true
}

cm_recover_display() {  # esporta il display del desktop se manca: 0 = recuperato
  # 14/09/2026: il server tmux ripartito al boot del 12/09 senza DISPLAY/WAYLAND_DISPLAY/XDG_RUNTIME_DIR li nega
  # a ogni sessione e ai suoi figli (restart, launch): cm-terminal saltava la finestra come headless (T57).
  # Solo sotto tmux (TMUX impostata): cron e i daemon restano headless. Solo con il socket del compositor vivo.
  # Con «force» (una finestra chiesta apposta: `launch --window`, come fa `reopen` dal polso, 15/09) anche fuori
  # da tmux: il relay nato da cron non ha display, ma chi tocca «Riprendi» vuole vedere la sessione sul desktop.
  # XDG_RUNTIME_DIR anche: senza, un WAYLAND_DISPLAY relativo non trova il socket.
  [ -z "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] && { [ -n "${TMUX:-}" ] || [ "${1:-}" = force ]; } || return 1
  local rt="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  [ -S "$rt/wayland-0" ] && export XDG_RUNTIME_DIR="$rt" WAYLAND_DISPLAY=wayland-0
  [ -S "${CM_X11_SOCKET_DIR:-/tmp/.X11-unix}/X0" ] && export DISPLAY=:0
  [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]
}

cm_get() {  # cm_get personale TMUX_PREFIX
  local v="CM_ACCOUNTS_${1^^}_${2}"
  v="${v//-/_}"
  printf '%s' "${!v:-}"
}

cm_account_of_name() {
  local nome="$1" best="" bestlen=-1 acc pfx
  for acc in $CM_ACCOUNTS_KEYS; do
    pfx="$(cm_get "$acc" TMUX_PREFIX)"
    if [ -z "$pfx" ]; then
      [ "$bestlen" -lt 0 ] && { best="$acc"; bestlen=0; }
    elif [ "${nome#"$pfx"}" != "$nome" ] && [ "${#pfx}" -gt "$bestlen" ]; then
      best="$acc"; bestlen="${#pfx}"
    fi
  done
  printf '%s' "${best:-$CM_DEFAULT_ACCOUNT}"
}

cm_msg() {
  python3 "$CM_SCRIPTS/cm-config.py" --msg "$@"
}
