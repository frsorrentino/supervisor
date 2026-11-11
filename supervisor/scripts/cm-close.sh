#!/usr/bin/env bash
# supervisor close — chiude una sessione Claude per davvero.
#
#   supervisor close <nome-tmux>              chiude quella sessione
#   supervisor close --abandoned [--dry-run]  quelle staccate E ferme su una domanda   (it: --abbandonate --prova)
#
# Esiste perche' le sessioni sopravvivono alla chiusura della finestra: una
# finestra e' una vista, la sessione vive finche' non la chiudi tu. Una sessione ATTACCATA
# si chiude solo se idle, chiudendo la sua scheda (07/10/2026; prima T14 le rifiutava tutte).
# Una sessione non puo' chiudere se stessa: il kill arriverebbe a turno aperto (c'e' `restart`).
#
# `kill-session -t =NOME` (T1/T50): senza `=` tmux aggancia per prefisso e con i
# nomi -2/-3 uccide la sessione sbagliata — l'08/09/2026 e' morta `fable-director-2`.
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"

PROVA=no; BERSAGLIO=""; ABBANDONATE=no; FORZA=""
for arg in "$@"; do
  case "$arg" in
    --dry-run|--prova) PROVA=si ;;
    --force|--forza) FORZA=--force ;;   # solo per le sessioni su un altro host: chiude anche una busy o waiting
    --abandoned|--abbandonate) ABBANDONATE=si ;;
    -*) cm_msg close.unknown_option "opt=$arg" >&2; exit 2 ;;
    *) BERSAGLIO="$arg" ;;
  esac
done

attaccata() {
  [ "$(cm_tmux list-sessions -F '#{session_name} #{session_attached}' 2>/dev/null | awk -v n="$1" '$1 == n {print $2}')" = "1" ]
}
chiudi() {
  if [ "$PROVA" = si ]; then echo "  $(cm_msg close.would_close "name=$1")"; return; fi
  cm_tmux kill-session -t "=$1" 2>/dev/null && echo "  $(cm_msg close.closed "name=$1")"
  "$CM_SCRIPTS/cm-registry.sh" --closed "$1" >/dev/null 2>&1   # chiusura esplicita: esce dalla fotografia
}

if [ "$ABBANDONATE" = si ]; then
  # stessa regola di `sessions`: flag dell'hook o due indizi sullo schermo, e nessuno attaccato
  trovate=0
  while read -r nome; do
    [ -n "$nome" ] || continue
    chiudi "$nome"; trovate=$((trovate + 1))
  done < <(python3 "$CM_SCRIPTS/cm-sessions.py" --json | python3 -c '
import json, sys
for r in json.load(sys.stdin):
    if r.get("tmux") and r.get("waiting") and not r.get("attached"):
        print(r["tmux"])')
  [ "$trovate" -eq 0 ] && echo "  $(cm_msg close.none_abandoned)"
  exit 0
fi

if [ -z "$BERSAGLIO" ]; then
  cm_msg close.usage >&2
  echo "  $(cm_msg close.running): $(cm_tmux list-sessions -F '#{session_name}' 2>/dev/null | tr '\n' ' ')" >&2
  exit 2
fi
# fase 2.2 del piano multi-PC: `HOST:nome` o una sessione lanciata con launch --host si chiude sull'host
if python3 "$CM_SCRIPTS/cm-rsession.py" resolve "$BERSAGLIO" >/dev/null 2>&1; then
  [ "$PROVA" = si ] && { echo "  $(cm_msg close.would_close "name=$BERSAGLIO")"; exit 0; }
  exec python3 "$CM_SCRIPTS/cm-rsession.py" close "$BERSAGLIO" $FORZA
fi
cm_tmux has-session -t "=$BERSAGLIO" 2>/dev/null || {
  cm_msg close.missing "name=$BERSAGLIO" >&2
  echo "  $(cm_msg close.running): $(cm_tmux list-sessions -F '#{session_name}' 2>/dev/null | tr '\n' ' ')" >&2
  exit 3
}
if attaccata "$BERSAGLIO"; then
  # 07/10/2026 (ok del maintainer): con destroy-unattached ogni sessione con finestra risulta attaccata, e la vecchia
  # regola T14 le rifiutava tutte. Ora un'attaccata IDLE si chiude chiudendo la sua scheda (chrome-bridge): la sessione
  # finisce da sola. Mai detach-client ne' kill-session su un'attaccata (05/10: un detach e' coinciso con la morte di
  # tutte le sessioni). Busy, ferma su una domanda o con un turno aperto: rifiutata come prima. Mai se stessa.
  if [ -n "${TMUX:-}" ] && [ "$(cm_tmux display-message -p '#S' 2>/dev/null)" = "$BERSAGLIO" ]; then
    cm_msg close.self "name=$BERSAGLIO" >&2; exit 4
  fi
  # con lo schermo: una domanda a video senza il flag dell'hook e' «waiting» solo leggendolo
  stato=$(python3 "$CM_SCRIPTS/cm-sessions.py" --json 2>/dev/null | python3 -c '
import json, sys
n = sys.argv[1]
for r in json.load(sys.stdin):
    if (r.get("tmux") or r.get("name")) == n:
        print("waiting" if r.get("waiting") else (r.get("status") or "?")); break
else:
    print("?")' "$BERSAGLIO")
  if [ "$stato" != idle ]; then
    cm_msg close.attached_busy "name=$BERSAGLIO" "state=$stato" >&2; exit 4
  fi
  if [ "$PROVA" = si ]; then
    out=$(python3 "$CM_SCRIPTS/cm-tile.py" close-tab "$BERSAGLIO" --dry-run 2>&1); rc=$?
    [ $rc -eq 0 ] && { echo "  $(cm_msg close.would_close_tab "name=$BERSAGLIO" "tab=$out")"; exit 0; }
    cm_msg close.attached_no_tab "name=$BERSAGLIO" "why=${out:-rc $rc}" >&2; exit 4
  fi
  out=$(python3 "$CM_SCRIPTS/cm-tile.py" close-tab "$BERSAGLIO" 2>&1); rc=$?
  if [ $rc -ne 0 ]; then
    cm_msg close.attached_no_tab "name=$BERSAGLIO" "why=${out:-rc $rc}" >&2; exit 4
  fi
  for _ in $(seq 1 15); do cm_tmux has-session -t "=$BERSAGLIO" 2>/dev/null || break; sleep 1; done
  if cm_tmux has-session -t "=$BERSAGLIO" 2>/dev/null; then
    cm_msg close.attached_still "name=$BERSAGLIO" >&2; exit 4
  fi
  echo "  $(cm_msg close.closed "name=$BERSAGLIO")"
  "$CM_SCRIPTS/cm-registry.sh" --closed "$BERSAGLIO" >/dev/null 2>&1
  exit 0
fi
chiudi "$BERSAGLIO"
