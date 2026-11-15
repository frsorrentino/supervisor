#!/usr/bin/env bash
# supervisor cloud / follow / desk — nuvola e sportello (N4, N9).
#
#   supervisor cloud <cartella> "task" [--account N]   sessione cloud (claude --cloud) dall'account
#                                                         dedotto dalla cartella: il cwd deve avere un
#                                                         remote GitHub raggiungibile (o < 100 MB: bundle)
#   supervisor follow <id|url> "messaggio"             accoda un messaggio a una sessione cloud (claude -p --cloud)
#   supervisor desk [start|stop|status] [--no-window]  «sportello»: `claude remote-control` in tmux nella
#                                                         radice dei workspaces. OPZIONALE, spento di default
#                                                         (desk.enabled): la sessione della radice resta
#                                                         l'ingresso dal telefono (l'utente, 10/09/2026)
#                                                         Le sessioni dello sportello nascono senza Claude in
#                                                         Chrome: `remote-control` lo accende solo con --chrome
#                                                         (2.1.285). `claude remote-control` che esce con 4
#                                                         riparte da solo, con 69 chiede il login (2.1.325);
#                                                         `status --check` esce 1 da fermo
#   (--teleport <id> e' un'opzione di `launch`: porta una sessione cloud in tmux con finestra e colore)
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"
CLAUDE="${CM_CLAUDE_BIN:-$(command -v claude)}"

account_env() {  # stampa "CLAUDE_CONFIG_DIR=..." solo per gli account fuori da ~/.claude (T68)
  local conf; conf="$(cm_get "$1" CONFIG_DIR)"
  [ "$(readlink -f "$conf")" != "$(readlink -f "$(eval echo ~)/.claude")" ] && printf 'CLAUDE_CONFIG_DIR=%s' "$conf" || printf 'CM_LAUNCHED=1'
}
deduce() {  # account_for in cm-config.py, l'unica copia della regola; il default se nessuna voce copre la cartella
  local a; a="$(python3 "$CM_SCRIPTS/cm-config.py" --account-for "$1" 2>/dev/null)"
  printf '%s' "${a:-$CM_DEFAULT_ACCOUNT}"
}

cmd_cloud() {
  local dir="${1:-}" task="${2:-}" acc=""
  shift 2 2>/dev/null || { cm_msg cloud.usage >&2; exit 2; }
  while [ $# -gt 0 ]; do case "$1" in --account) shift; acc="$1" ;; *) cm_msg launch.unknown_option "opt=$1" >&2; exit 2 ;; esac; shift; done
  [ -d "$dir" ] && [ -n "$task" ] || { cm_msg cloud.usage >&2; exit 2; }
  [ -n "$acc" ] || acc="$(deduce "$dir")"
  cm_msg cloud.starting "dir=$dir" "account=$(cm_get "$acc" LABEL)"
  if [ -t 0 ] && [ -t 1 ]; then
    ( cd "$dir" && env "$(account_env "$acc")" "$CLAUDE" --cloud "$task" )
  else
    # T75 (09/09/2026): `claude --cloud` esige un terminale interattivo e da una sessione Claude
    # (o da uno script) non c'e': si apre in una sessione tmux staccata, che il TTY ce l'ha
    local nome; nome="cloud-$(basename "$dir" | tr -c 'A-Za-z0-9_\n-' '-')"
    cm_tmux has-session -t "=$nome" 2>/dev/null && { cm_msg cloud.tmux_busy "name=$nome" >&2; exit 1; }
    # remain-on-exit nella stessa chiamata al server: `claude --cloud` stampa link e id e ESCE,
    # e con la finestra morta l'output sarebbe perso (successo alle 23:08). set-option col nome
    # nudo (T64). Poi si aspetta la fine, si stampa lo schermo (-J: righe a capo riunite) e si chiude.
    cm_tmux new-session -d -s "$nome" -c "$dir" env "$(account_env "$acc")" "$CLAUDE" --cloud "$task" \; set-option -t "$nome" remain-on-exit on || exit 1
    local i dead=""
    for i in $(seq 1 "${CM_CLOUD_WAIT_S:-120}"); do
      dead="$(cm_tmux display-message -p -t "$nome" '#{pane_dead}' 2>/dev/null)" || break
      [ "$dead" = 1 ] && break
      sleep 1
    done
    if [ "$dead" = 1 ]; then
      cm_tmux capture-pane -p -J -S - -t "$nome" | sed '/^[[:space:]]*$/d; /^Pane is dead/d'
      cm_tmux kill-session -t "=$nome" 2>/dev/null
    else
      cm_msg cloud.in_tmux "name=$nome"
    fi
  fi
}
cmd_follow() {
  local id="${1:-}" text="${2:-}"
  [ -n "$id" ] && [ -n "$text" ] || { cm_msg cloud.follow_usage >&2; exit 2; }
  "$CLAUDE" -p "$text" --cloud "$id"
}
# 13/11/2026 (supervisor-sportello-rc-13-11), Claude Code 2.1.325: `claude remote-control` esce con 4 quando si ferma
# da solo e con 69 quando non riesce a registrarsi. Lo sportello gira dentro questo ciclo, nella sua sessione tmux:
# con 4 riparte (pausa DESK_RESTART_PAUSE_S; al massimo DESK_RESTART_MAX uscite di fila durate meno di
# DESK_RESTART_QUICK_S, poi si ferma e lo dice); con 69 chiede la persona con la procedura di login e si ferma; con
# ogni altro codice esce, e la sessione tmux si chiude come prima.
DESK_RESTART_PAUSE_S="${CM_DESK_RESTART_PAUSE_S:-5}"
DESK_RESTART_MAX="${CM_DESK_RESTART_MAX:-5}"
DESK_RESTART_QUICK_S=60
desk_needs() {  # $1 = sportello, $2 = cosa serve: needs-you sulla sessione della radice (senza: nella sua casella)
  local self; self="$(readlink -f "${BASH_SOURCE[0]}")"
  python3 "$CM_SCRIPTS/cm-needsyou.py" "$2" --session "${CM_WORKSPACE_ROOT_SESSION_NAME:-master}" --from "$1" \
    --until "CM_SUBCOMMAND=desk '$self' status --check >/dev/null 2>&1"
}
desk_run() {  # $1 = nome: il ciclo dentro la sessione tmux dello sportello
  local nome="$1" n=0 t0 rc conf login="claude auth login"
  conf="$(cm_get "$CM_DEFAULT_ACCOUNT" CONFIG_DIR)"
  [ "$(readlink -f "$conf")" != "$(readlink -f "$(eval echo ~)/.claude")" ] && login="CLAUDE_CONFIG_DIR=$conf claude auth login"
  while :; do
    t0="$(date +%s)"
    env "$(account_env "$CM_DEFAULT_ACCOUNT")" "$CLAUDE" remote-control \
      --name "$nome" --capacity "${CM_DESK_CAPACITY:-4}" --permission-mode "${CM_DESK_PERMISSION_MODE:-default}"
    rc=$?
    case "$rc" in
      4)
        [ $(( $(date +%s) - t0 )) -ge "$DESK_RESTART_QUICK_S" ] && n=0
        n=$((n + 1))
        if [ "$n" -gt "$DESK_RESTART_MAX" ]; then
          cm_msg desk.restart_limit "name=$nome" "max=$DESK_RESTART_MAX" "quick=$DESK_RESTART_QUICK_S" >&2
          desk_needs "$nome" "$(cm_msg desk.limit_reason "name=$nome" "n=$n" "quick=$DESK_RESTART_QUICK_S")"
          exit 4
        fi
        cm_msg desk.restarting "name=$nome" "n=$n" "max=$DESK_RESTART_MAX" "pause=$DESK_RESTART_PAUSE_S"
        sleep "$DESK_RESTART_PAUSE_S" ;;
      69)
        cm_msg desk.unregistered "name=$nome" >&2
        desk_needs "$nome" "$(cm_msg desk.login_reason "name=$nome" "login=$login")"
        exit 69 ;;
      *) exit "$rc" ;;
    esac
  done
}
cmd_desk() {
  local verb="${1:-start}" nome="${CM_DESK_NAME:-sportello}" window=true check=false
  for a in "$@"; do [ "$a" = --no-window ] && window=false; [ "$a" = --check ] && check=true; done
  local dir; dir="$(eval echo "$CM_WORKSPACE_ROOT")"
  case "$verb" in
    run) desk_run "$nome" ;;   # lo lancia `start`, dentro tmux
    status)
      if cm_tmux has-session -t "=$nome" 2>/dev/null; then cm_msg desk.running "name=$nome"; cm_tmux capture-pane -p -t "$nome" | grep -oE 'https://claude\.ai/code/session_[A-Za-z0-9]+' | head -1
      else cm_msg desk.stopped "name=$nome"; if [ "$check" = true ]; then exit 1; fi; fi ;;
    stop)
      cm_tmux kill-session -t "=$nome" 2>/dev/null && cm_msg desk.stopped "name=$nome" || cm_msg desk.not_running "name=$nome" ;;
    start)
      [ "${CM_DESK_ENABLED:-false}" = true ] || { cm_msg desk.disabled >&2; exit 3; }
      cm_tmux has-session -t "=$nome" 2>/dev/null && { cm_msg desk.already "name=$nome"; exit 0; }
      # il ciclo (desk_run) gira nella sessione tmux, che eredita l'ambiente del SERVER e non il nostro: config, home,
      # tmux e claude gli si passano qui
      local self cfg; self="$(readlink -f "${BASH_SOURCE[0]}")"
      cfg="${CC_SUPERVISOR_CONFIG:-${TEAM_SUPERVISOR_CONFIG:-${CLAUDE_MASTER_CONFIG:-${CM_HOME:-$HOME}/.config/cc-supervisor/config.json}}}"
      local -a e=("HOME=$HOME" "CC_SUPERVISOR_CONFIG=$cfg" "CM_CLAUDE_BIN=$CLAUDE" "CM_SUBCOMMAND=desk"
                  "CM_DESK_RESTART_PAUSE_S=$DESK_RESTART_PAUSE_S" "CM_DESK_RESTART_MAX=$DESK_RESTART_MAX")
      [ -n "${CM_HOME:-}" ] && e+=("CM_HOME=$CM_HOME")
      [ -n "${CM_TMUX_ARGS:-}" ] && e+=("CM_TMUX_ARGS=$CM_TMUX_ARGS")
      cm_tmux new-session -d -s "$nome" -c "$dir" env "${e[@]}" "$self" run
      sleep 2
      cm_tmux has-session -t "=$nome" 2>/dev/null || { cm_msg desk.died "name=$nome" >&2; exit 5; }
      [ "$window" = true ] && "$CM_SCRIPTS/cm-terminal.sh" open "$nome"
      cm_msg desk.started "name=$nome" "dir=$dir" ;;
    *) cm_msg desk.usage >&2; exit 2 ;;
  esac
}

case "${CM_SUBCOMMAND:-${1:-}}" in
  cloud)  [ "${1:-}" = cloud ] && shift; cmd_cloud "$@" ;;
  follow) [ "${1:-}" = follow ] && shift; cmd_follow "$@" ;;
  desk|sportello) [ "${1:-}" = desk ] || [ "${1:-}" = sportello ] && shift; cmd_desk "$@" ;;
  *) cm_msg cloud.usage >&2; exit 2 ;;
esac
