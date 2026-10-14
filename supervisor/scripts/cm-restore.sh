#!/usr/bin/env bash
# supervisor restore — rilancia le sessioni registrate prima di un riavvio.
#
#   supervisor restore            rilancia: con un terminale chiede conferma (N s poi si'),
#                                    SENZA terminale si ferma alla lista (serve --yes)
#   supervisor restore --dry-run  dice cosa farebbe                                  (it: --prova)
#   supervisor restore --yes      non chiede                                          (it: --si)
#
# Legge il registro (`registry.file`, aggiornato dagli hook di sessione e da `launch`;
# il cron lo riconcilia). Ogni sessione riparte con --resume <session_id> quando il registro ha
# l'id e la sua trascrizione esiste (14/10: con --continue due sessioni nella stessa cartella
# riprendevano tutte e due l'ultima conversazione di quella cartella), altrimenti con
# --continue, e lo dice; ognuna con la sua finestra, in parallelo; quella della radice (`restore.last`)
# per ultima: e' quella a cui ci si attacca. Se una sessione con quel nome e' gia' viva
# la salta: si puo' rilanciare a meta'. Le cartelle sparite si saltano e si dicono.
#
# Le sessioni PARCHEGGIATE non sono nel registro (park le chiude e l'hook SessionEnd
# aggiorna il registro): restano parcheggiate, si riprendono con `unpark`.
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"
REG="$CM_REGISTRY_FILE"
GOOD="$CM_REGISTRY_GOOD_FILE"
PROVA=no; CONFERMA=si
for a in "$@"; do case "$a" in --dry-run|--prova) PROVA=si ;; --yes|--si) CONFERMA=no ;; esac; done
[ -f "$REG" ] || [ -f "$GOOD" ] || { cm_msg restore.no_registry "path=$REG"; exit 1; }
LOGDIR="$CM_STATE_DIR/restore"; mkdir -p "$LOGDIR"

# unione del registro riconciliato e della fotografia «ultimo insieme buono» (11/09/2026): una
# sessione chiusa a mano prima del riavvio sta solo nella fotografia; si dice da dove viene ciascuna
# La fotografia non dimagrisce da sola (cm-registry.sh): senza filtro riportava in vita sessioni
# morte da giorni (28/09/2026: due sessioni chiuse il 19/09 e il 20/09). Se ne prendono solo
# le voci viste nell'ultima accensione prima di questa, entro `restore.snapshot_window_min`
# dall'ultimo segno di vita; quelle viste dopo l'avvio chiuse a mano restano chiuse.
mapfile -t RIGHE < <(python3 - "$REG" "$GOOD" "$MSG_RESTORE_SRC_REGISTRY" "$MSG_RESTORE_SRC_SNAPSHOT" "${CM_RESTORE_SNAPSHOT_WINDOW_MIN:-60}" "${CM_UPTIME_FILE:-/proc/uptime}" <<'PY'
import json, sys, time
from datetime import datetime
def load(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return {}
reg, good = load(sys.argv[1]), load(sys.argv[2])
seen = set()
for s in reg.get("sessioni", []):
    seen.add(s["nome"])
    print(f'{s["nome"]}\t{s["cartella"]}\t{s.get("account", "")}\t' + sys.argv[3].format(t=str(reg.get("salvato", ""))[:16].replace("T", " ")) + f'\t{s.get("session_id") or ""}')
def ts(v):
    try:
        return datetime.strptime(str(v), "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        return None
try:
    boot = time.time() - float(open(sys.argv[6]).read().split()[0])
except (OSError, ValueError, IndexError):
    boot = time.time()
prima = [t for t in (ts(s.get("visto")) for s in good.get("sessioni", [])) if t is not None and t < boot]
ultimo = max(prima) if prima else None
for s in good.get("sessioni", []):
    if s["nome"] in seen:
        continue
    t = ts(s.get("visto"))
    if t is None or ultimo is None or t >= boot or t < ultimo - float(sys.argv[5]) * 60:
        continue
    print(f'{s["nome"]}\t{s["cartella"]}\t{s.get("account", "")}\t' + sys.argv[4].format(t=str(s.get("visto", ""))[:16].replace("T", " ")) + f'\t{s.get("session_id") or ""}')
PY
)
[ "${#RIGHE[@]}" -gt 0 ] || { cm_msg restore.empty; exit 1; }
SALVATO=$(python3 -c "import json,sys
for p in sys.argv[1:]:
    try: print(json.load(open(p)).get('salvato','')[:16]); break
    except Exception: pass" "$REG" "$GOOD")

# come riprende una sessione (14/10): --resume <id> se il registro ha l'id e la trascrizione c'e' nell'account,
# altrimenti --continue. Imposta `opz` e `modo` (la parola per la lista)
come_riprendere() {  # $1 cartella, $2 account, $3 session_id
  local a="$2" conf
  [ -n "$a" ] || a="$(python3 "$CM_SCRIPTS/cm-config.py" --account-for "$1" 2>/dev/null)"
  conf="$(cm_get "$a" CONFIG_DIR 2>/dev/null || true)"
  opz=(--continue); modo="continue"
  # lo stesso controllo di launch --resume (T10): la trascrizione nella cartella della sessione
  if [ -n "$3" ] && [ -n "$conf" ] && [ -f "$conf/projects/$(readlink -f "$1" | sed 's|[^A-Za-z0-9]|-|g')/$3.jsonl" ]; then
    opz=(--resume "$3"); modo="resume ${3:0:8}"
  fi
  [ -n "$2" ] && opz+=(--account "$2")
  return 0
}

DA_FARE=()
for r in "${RIGHE[@]}"; do
  IFS=$'\t' read -r nome cartella acct fonte sid <<<"$r"
  if cm_tmux has-session -t "=$nome" 2>/dev/null; then echo "  $(cm_msg restore.alive "name=$nome")"; continue; fi
  [ -d "$cartella" ] || { echo "  $(cm_msg restore.gone "name=$nome" "dir=$cartella")"; continue; }
  DA_FARE+=("$r")
done
[ "${#DA_FARE[@]}" -gt 0 ] || { cm_msg restore.nothing; exit 0; }

cm_msg restore.todo "n=${#DA_FARE[@]}" "saved=$SALVATO"
for r in "${DA_FARE[@]}"; do
  IFS=$'\t' read -r nome cartella acct fonte sid <<<"$r"; come_riprendere "$cartella" "$acct" "$sid"
  printf '  %-28s %-13s %-40s %-24s %s\n' "$nome" "$acct" "$cartella" "$fonte" "$modo"
  [ "$modo" = continue ] && echo "    $(cm_msg restore.continue_no_id "name=$nome")"
done
# modalita' scheda (terminal.open_as_tab, 11/09/2026): tutte in UNA finestra del Terminale, quindi in
# SEQUENZA (una duplicazione per volta, ~20 s l'una), non in parallelo
A_SCHEDE=no
if [ "${CM_TERMINAL_OPEN_AS_TAB:-true}" = true ] && [ "$("$CM_SCRIPTS/cm-terminal.sh" detect)" = chromeos ]; then
  A_SCHEDE=si
  IFS=$'\t' read -r nome cartella acct fonte sid <<<"${DA_FARE[0]}"
  piano=$(python3 "$CM_SCRIPTS/cm-tile.py" open-tab "$nome" --dry-run 2>/dev/null) && echo "  $(cm_msg restore.as_tabs "plan=$piano")"
fi
[ "$PROVA" = si ] && exit 0
if [ "$CONFERMA" = si ]; then
  if [ -t 0 ]; then
    read -r -t "$CM_RESTORE_CONFIRM_TIMEOUT_S" -p "$(cm_msg restore.confirm "s=$CM_RESTORE_CONFIRM_TIMEOUT_S") " risp || risp=s
    case "${risp:-s}" in [nN]*) cm_msg restore.aborted; exit 0 ;; esac   # n, N, no, No
  else
    # senza terminale nessuno puo' rispondere: si ferma alla lista (come --dry-run) e dice
    # come eseguire davvero. Un `restore` letto «per vedere» da una sessione Claude rilanciava
    # tutto (master, 09/09/2026 23:20).
    cm_msg restore.no_tty
    exit 0
  fi
fi

# tutte in parallelo tranne l'ultima (restore.last), che va per ultima
ULTIMA=""
for r in "${DA_FARE[@]}"; do
  IFS=$'\t' read -r nome cartella acct fonte sid <<<"$r"
  if [ "$nome" = "$CM_RESTORE_LAST" ]; then ULTIMA="$r"; continue; fi
  come_riprendere "$cartella" "$acct" "$sid"
  if [ "$A_SCHEDE" = si ]; then
    "$CM_SCRIPTS/cm-launch.sh" "$cartella" "${opz[@]}" >"$LOGDIR/$nome.log" 2>&1 \
      && echo "  $(cm_msg restore.ok "name=$nome")" || echo "  $(cm_msg restore.failed "name=$nome" "log=$LOGDIR/$nome.log")"
  else
    ( "$CM_SCRIPTS/cm-launch.sh" "$cartella" "${opz[@]}" >"$LOGDIR/$nome.log" 2>&1 \
        && echo "  $(cm_msg restore.ok "name=$nome")" || echo "  $(cm_msg restore.failed "name=$nome" "log=$LOGDIR/$nome.log")" ) &
  fi
done
wait
if [ -n "$ULTIMA" ]; then
  IFS=$'\t' read -r nome cartella acct fonte sid <<<"$ULTIMA"
  come_riprendere "$cartella" "$acct" "$sid"
  "$CM_SCRIPTS/cm-launch.sh" "$cartella" "${opz[@]}" >"$LOGDIR/$nome.log" 2>&1 \
    && echo "  $(cm_msg restore.ok "name=$nome")" || echo "  $(cm_msg restore.failed "name=$nome" "log=$LOGDIR/$nome.log")"
fi
cm_msg restore.done; cm_tmux list-sessions -F '  #{session_name}' 2>/dev/null
# il bot del telefono riparte subito col ripristino, senza aspettare il cron (mandato 11/09 18:30)
