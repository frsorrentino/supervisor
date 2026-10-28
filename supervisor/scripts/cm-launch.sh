#!/usr/bin/env bash
# supervisor launch — avvia una sessione Claude in una cartella, dentro tmux, staccata dal terminale.
#
# Uso: supervisor launch <percorso assoluto> [--create] [--continue|--resume <id>]
#        [--account <nome>|--<nome-account>] [--no-window] [--bg] [--profile <nome>] [--force]
#        [--model M] [--effort E]
#   --create        crea la cartella se non esiste (mkdir -p)          (it: --crea)
#   --continue      riprende l'ultima conversazione della cartella      (it: --continua)
#   --resume <id>   riprende UNA conversazione precisa                  (it: --riprendi)
#   --account N     forza l'account (scorciatoie: --personale, --professionale, ...)
#   --no-window     non aprire la finestra sul desktop                  (it: --senza-finestra)
#   --window        aprirla anche se il default e' no; recupera il display anche fuori da tmux (it: --finestra)
#   --bg            sessione in BACKGROUND (claude --bg): niente tmux, finestra, colore
#   --profile N     profilo di lancio da config `profiles.<N>` (N8): argomenti, modello, effort, env
#
# Senza flag l'account si DEDUCE dal percorso (`folder_map`): e' un default con
# avviso, mai un divieto (T49) — il caso legittimo esiste (due sessioni sulla
# stessa cartella, una per account). tmux fornisce il TTY che manca quando il
# comando arriva da un assistente; Remote Control si attiva da solo se
# remoteControlAtStartup e' true nelle impostazioni utente.
#
# --continue e --resume non sono intercambiabili: -c prende sempre la conversazione
# PIU' RECENTE della cartella, quindi su due sessioni aperte sulla stessa cartella
# lanciarlo due volte le fa atterrare entrambe sulla stessa conversazione (T10,
# 2026-08-24). Un id sbagliato non da' errore: Claude apre una conversazione
# VUOTA che somiglia a una ripresa riuscita — si verifica prima.
#
# Lo script e' volutamente severo: solo percorsi assoluti, nessuna euristica sui
# nomi. La tolleranza sui percorsi parziali sta nella skill.
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"
if [ -n "${CM_TRACE:-}" ]; then PS4='+ $(date +%T) '; set -x; fi   # CM_TRACE=1: traccia con orari (diagnosi dei tempi)

CARTELLA="${1:-}"
CREA=no; CONTINUA=no; RIPRENDI=""; ACCOUNT=""; FINESTRA="$CM_SESSION_WINDOW_BY_DEFAULT"; BG=no; FORCE=no
PROFILO=""; MODEL=""; EFFORT=""; TELEPORT=""; AGENT=claude; HOST=""
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --create|--crea) CREA=si ;;
    --continue|--continua) CONTINUA=si ;;
    --resume|--riprendi) shift; RIPRENDI="${1:-}"; [ -n "$RIPRENDI" ] || { cm_msg launch.resume_needs_id >&2; exit 2; } ;;
    --account) shift; ACCOUNT="${1:-}" ;;
    --teleport) shift; TELEPORT="${1:-}" ;;   # N4: una sessione cloud portata in tmux con finestra e colore
    --no-window|--senza-finestra) FINESTRA=false ;;
    --window|--finestra) FINESTRA=true; FINESTRA_CHIESTA=force ;;
    --bg|--background) BG=si ;;
    --force|--forza) FORCE=si ;;   # oltre il tetto sessions.max_sessions senza chiedere
    --profile|--profilo) shift; PROFILO="${1:-}" ;;
    --model) shift; MODEL="${1:-}" ;;
    --effort) shift; EFFORT="${1:-}" ;;
    --agent) shift; AGENT="${1:-}" ;;   # S09: prova, `codex` solo con experimental.codex
    --host) shift; HOST="${1:-}" ;;   # piano multi-PC 4.2: un altro host (cm-rsession.py), `auto` sceglie se la regia e' carica
    --aziendale)  # nome storico: vale il secondo account, con avviso
      ACCOUNT=$(for a in $CM_ACCOUNTS_KEYS; do [ "$a" != "$CM_DEFAULT_ACCOUNT" ] && { echo "$a"; break; }; done)
      cm_msg launch.legacy_flag "flag=--aziendale" "account=$ACCOUNT" >&2 ;;
    --*)
      cand="${1#--}"; found=no
      for a in $CM_ACCOUNTS_KEYS; do [ "$a" = "$cand" ] && { ACCOUNT="$a"; found=si; }; done
      [ "$found" = si ] || { cm_msg launch.unknown_option "opt=$1" >&2; exit 2; } ;;
    *) cm_msg launch.unknown_option "opt=$1" >&2; exit 2 ;;
  esac
  shift
done
case "$AGENT" in
  claude) ;;
  codex)
    [ "${CM_EXPERIMENTAL_CODEX:-false}" = true ] || { cm_msg launch.agent_off "agent=codex" >&2; exit 2; }
    [ "$BG" = si ] && { cm_msg launch.agent_no_bg >&2; exit 2; } ;;
  *) cm_msg launch.unknown_agent "agent=$AGENT" >&2; exit 2 ;;
esac

if [ "$CONTINUA" = si ] && [ -n "$RIPRENDI" ]; then cm_msg launch.continue_xor_resume >&2; exit 2; fi
if [ -n "$HOST" ] && [ "$HOST" != local ]; then
  for c in "BG=--bg" "RIPRENDI=--resume" "TELEPORT=--teleport"; do
    v="${c%%=*}"; [ -n "${!v}" ] && [ "${!v}" != no ] && { cm_msg launch.host_conflict "opt=${c#*=}" >&2; exit 2; }
  done
  [ "$AGENT" = claude ] || { cm_msg launch.host_conflict "opt=--agent $AGENT" >&2; exit 2; }
fi
HOST_DATO="$HOST"
if [ "$HOST" = auto ]; then HOST="$(python3 "$CM_SCRIPTS/cm-rsession.py" pick "${CARTELLA:-/}" 2>/dev/null || true)"; fi
[ "$HOST" = local ] && HOST=""
[ -n "$CARTELLA" ] || { cm_msg launch.usage >&2; exit 2; }
case "$CARTELLA" in /*) ;; *) cm_msg launch.not_absolute "path=$CARTELLA" >&2; exit 2 ;; esac

# --- account: forzato, altrimenti dedotto da folder_map (prefisso piu' lungo) ------
# una sola copia della regola, in cm-config.py (account_for): la usano anche cloud, night e il relay
deduci_account() { python3 "$CM_SCRIPTS/cm-config.py" --account-for "$1" 2>/dev/null; }
DEDOTTO=$(deduci_account "$CARTELLA")
DEDUZIONE=""
if [ -z "$ACCOUNT" ]; then
  ACCOUNT="${DEDOTTO:-$CM_DEFAULT_ACCOUNT}"
  [ -n "$DEDOTTO" ] && DEDUZIONE=si
  # 14/10: cambio automatico al limite (auto_switch.accounts), solo per una sessione NUOVA: --resume e --continue
  # riprendono una conversazione che sta nell'account di partenza
  if [ "$CONTINUA" = no ] && [ -z "$RIPRENDI" ]; then
    ALT=$(python3 "$CM_SCRIPTS/cm-guard.py" switch-for "$ACCOUNT" 2>/dev/null)
    if [ -n "$ALT" ] && [ "$ALT" != "$ACCOUNT" ]; then cm_msg launch.auto_switched "from=$ACCOUNT" "to=$ALT" >&2; ACCOUNT="$ALT"; DEDUZIONE=""; fi
  fi
elif [ -n "$DEDOTTO" ] && [ "$DEDOTTO" != "$ACCOUNT" ] && [ "$CM_ACCOUNTS_WARN_ON_MISMATCH" = true ]; then
  cm_msg launch.account_mismatch "deduced=$DEDOTTO" "asked=$ACCOUNT" >&2
fi
CONF_DIR="$(cm_get "$ACCOUNT" CONFIG_DIR)"
[ -n "$CONF_DIR" ] || { cm_msg launch.unknown_account "account=$ACCOUNT" "known=$CM_ACCOUNTS_KEYS" >&2; exit 2; }
PREFIX="$(cm_get "$ACCOUNT" TMUX_PREFIX)"

# 2.3: quota settimanale dell'account oltre soglia → avviso (consiglio, non blocco)
python3 "$CM_SCRIPTS/cm-sessions.py" --quota-warn "$ACCOUNT" >&2 2>/dev/null || true
# tetto (25/09/2026): 6,6 GB di RAM e ~350 MB a sessione. Oltre sessions.max_sessions sessioni al lavoro (busy o
# idle, esclusa la master) si avvisa con il conteggio e la memoria libera e si chiede conferma; senza terminale
# (lanciata da una sessione Claude) ci si ferma con exit 7 e si dice di ripetere con --force.
# La master (la radice dei workspace) non passa dal tetto: e' l'ingresso dal telefono, e il Riapri dell'app la
# lancia senza terminale (01/10/2026 23:17: «reopen master → 6 sessioni già al lavoro», exit 7).
if [ -z "$HOST" ] && [ "$FORCE" != si ] && [ "$(readlink -f "$CARTELLA")" != "$(readlink -f "$(eval echo "$CM_WORKSPACE_ROOT")")" ]; then
  ATTIVE="$(python3 "$CM_SCRIPTS/cm-sessions.py" --count-active 2>/dev/null || echo 0)"
  MAX="${CM_SESSIONS_MAX_SESSIONS:-5}"
  if [ "${ATTIVE:-0}" -ge "$MAX" ] 2>/dev/null; then
    LIBERA="$(free -m 2>/dev/null | awk '/^Mem:/{print $7}')"
    cm_msg launch.too_many "n=$ATTIVE" "max=$MAX" "free=${LIBERA:-?}" >&2
    if [ -t 0 ] && [ -z "${CM_LAUNCH_NO_TTY:-}" ]; then
      printf '%s ' "$(cm_msg launch.too_many_ask)" >&2; read -r risposta
      case "$risposta" in s|S|si|sì|SI|y|Y|yes) ;; *) cm_msg launch.too_many_stop >&2; exit 7 ;; esac
    else
      cm_msg launch.too_many_noninteractive >&2; exit 7
    fi
  fi
fi
# regia carica e cartella ammessa su un host con il ruolo sessions: una riga di proposta (il lancio resta qui)
[ -z "$HOST_DATO" ] && [ "$BG" != si ] && python3 "$CM_SCRIPTS/cm-rsession.py" propose "$CARTELLA" || true
if [ -z "$HOST" ]; then
command -v tmux >/dev/null || { cm_msg launch.no_tmux >&2; exit 3; }
# il binario: CM_CLAUDE_BIN (prove), poi il PATH, poi il link ~/.local/bin/claude (T81: dal cron il PATH
# e' minimo; da shell `claude` e' una funzione wrapper, il binario e' quel link)
CLAUDE="${CM_CLAUDE_BIN:-$(command -v claude 2>/dev/null || true)}"
[ -n "$CLAUDE" ] && [ -x "$CLAUDE" ] || CLAUDE="$HOME/.local/bin/claude"
[ -x "$CLAUDE" ] || { cm_msg launch.no_claude "tried=PATH ($PATH), $HOME/.local/bin/claude" >&2; exit 3; }
if [ "$AGENT" = codex ]; then   # S09: stessa regola del binario di claude
  CODEX="${CM_CODEX_BIN:-$(command -v codex 2>/dev/null || true)}"
  [ -n "$CODEX" ] && [ -x "$CODEX" ] || CODEX="$HOME/.local/bin/codex"
  [ -x "$CODEX" ] || { cm_msg launch.no_codex "tried=PATH ($PATH), $HOME/.local/bin/codex" >&2; exit 3; }
fi
fi

if [ ! -e "$CARTELLA" ]; then
  if [ "$CREA" = si ]; then mkdir -p "$CARTELLA" || { cm_msg launch.mkdir_failed "path=$CARTELLA" >&2; exit 4; }
  else cm_msg launch.missing_dir "path=$CARTELLA" >&2; exit 4; fi
elif [ ! -d "$CARTELLA" ]; then
  cm_msg launch.not_a_dir "path=$CARTELLA" >&2; exit 4
fi

# --- nome: radice → root_session_name; altrimenti basename sanificato (T2) --------
# tmux riscrive i punti in trattini bassi dentro i nomi di sessione: passare
# "sito.com" e poi cercarlo con quel nome fa dichiarare morta una sessione viva.
# Si normalizza prima, cosi' il nome passato e quello cercato coincidono.
if [ "$(readlink -f "$CARTELLA")" = "$(readlink -f "$(eval echo "$CM_WORKSPACE_ROOT")")" ]; then
  BASE="$CM_WORKSPACE_ROOT_SESSION_NAME"
else
  BASE=$(basename "$CARTELLA" | tr "$CM_SESSION_NAME_SANITIZE_CHARS" "$(printf '%*s' "${#CM_SESSION_NAME_SANITIZE_CHARS}" '' | tr ' ' '-')")
fi
BASE="$PREFIX$BASE"
# 16/10/2026 (dal vivo il 15/10 alle 23:40): una sessione --bg non sta in tmux, quindi has-session non la vedeva e un
# secondo `launch --bg` nella stessa cartella riprendeva lo stesso nome: due «pix-claude-plugins», e l'app in crash
# sulla chiave doppia. Un nome e' preso anche quando lo porta una sessione VIVA del registro di Claude Code, di ogni
# account: si va avanti con -2, -3 come per tmux. Uno zombie non e' vivo, come per `vivo` di cm-restart.sh: restart
# rilancia appena il processo vecchio esce, quando la sua voce puo' esserci ancora e /proc/<pid> anche (visto nei test
# il 16/10: il rilancio diventava «alfa-2»).
nome_preso() {
  cm_tmux has-session -t "=$1" 2>/dev/null && return 0
  local acc f pid
  for acc in $CM_ACCOUNTS_KEYS; do
    for f in "$(cm_get "$acc" CONFIG_DIR)"/sessions/*.json; do
      grep -qF "\"name\":\"$1\"" "$f" 2>/dev/null || continue
      pid="${f##*/}"; pid="${pid%%.*}"
      [ -d "/proc/$pid" ] && ! grep -q '^State:.*Z' "/proc/$pid/status" 2>/dev/null && return 0
    done
  done
  return 1
}
# 28/10/2026: un claude rimasto senza il suo tmux con questo nome (il doppione che non risponde) si chiude prima: altrimenti
# la sua voce nel registro tiene il nome e la sessione nuova diventa «-2», accanto a lui. Restart passa di qui.
if [ -z "$HOST" ] && [ "$BG" != si ]; then
  python3 "$CM_SCRIPTS/cm-sessions.py" --reap-orphans --now --name "$BASE" >&2 || true
fi
NOME="$BASE"; n=2
while nome_preso "$NOME"; do NOME="$BASE-$n"; n=$((n + 1)); done

# --- --resume: l'id deve esistere (T10) ---------------------------------------------
if [ -n "$RIPRENDI" ]; then
  # il nome della cartella dei transcript traduce OGNI carattere non alfanumerico in
  # trattino ("googlebook.it" → "...-googlebook-it"), non solo le barre
  SLUG=$(readlink -f "$CARTELLA" | sed 's|[^A-Za-z0-9]|-|g')
  DIR_CONV="$CONF_DIR/projects/$SLUG"
  if [ ! -f "$DIR_CONV/$RIPRENDI.jsonl" ]; then
    # una sessione background finita non ha per forza il transcript qui: si chiede a claude
    if ! CLAUDE_CONFIG_DIR="$CONF_DIR" "$CLAUDE" agents --json --all 2>/dev/null | grep -q "\"$RIPRENDI\""; then
      cm_msg launch.resume_missing "id=$RIPRENDI" "dir=$DIR_CONV" >&2
      if [ -d "$DIR_CONV" ]; then
        cm_msg launch.resume_recent >&2
        ls -lt --time-style='+%d/%m %H:%M' "$DIR_CONV"/*.jsonl 2>/dev/null | head -5 \
          | awk '{n=split($NF,a,"/"); sub(/\.jsonl$/,"",a[n]); printf "    %s  %6.1fMB  %s %s\n", a[n], $5/1048576, $6, $7}' >&2
      fi
      exit 4
    fi
  fi
fi

# --- profilo (N8): argomenti, modello, effort, env, bg/finestra ---------------------
PROF_ARGS=(); PROF_ENV=()
if [ -n "$PROFILO" ]; then
  case " ${CM_PROFILES_KEYS:-} " in *" $PROFILO "*) ;; *) cm_msg launch.unknown_profile "profile=$PROFILO" "known=${CM_PROFILES_KEYS:-}" >&2; exit 2 ;; esac
  pv="CM_PROFILES_${PROFILO^^}"; pv="${pv//-/_}"
  a="${pv}_ARGS"; [ -n "${!a:-}" ] && read -ra PROF_ARGS <<<"${!a}"
  a="${pv}_MODEL"; [ -z "$MODEL" ] && MODEL="${!a:-}"
  a="${pv}_EFFORT"; [ -z "$EFFORT" ] && EFFORT="${!a:-}"
  a="${pv}_BG"; [ "${!a:-}" = true ] && BG=si
  a="${pv}_WINDOW"; [ "${!a:-}" = false ] && FINESTRA=false
  a="${pv}_ENV_KEYS"
  for k in ${!a:-}; do v="${pv}_ENV_${k^^}"; PROF_ENV+=("$k=${!v:-}"); done
fi
for k in ${CM_SESSION_ENV_KEYS:-}; do v="CM_SESSION_ENV_${k^^}"; PROF_ENV+=("$k=${!v:-}"); done

ARGS=()
[ -n "${CM_SESSION_CLAUDE_ARGS:-}" ] && read -ra ARGS <<<"$CM_SESSION_CLAUDE_ARGS"
ARGS+=("${PROF_ARGS[@]}")
[ -n "$MODEL" ] && ARGS+=(--model "$MODEL")
[ -n "$EFFORT" ] && ARGS+=(--effort "$EFFORT")
[ "$CONTINUA" = si ] && ARGS+=(-c)
[ -n "$RIPRENDI" ] && ARGS+=(--resume "$RIPRENDI")
[ -n "$TELEPORT" ] && ARGS+=(--teleport "$TELEPORT")
# T68 (09/09/2026, dal vivo): CLAUDE_CONFIG_DIR NON va impostata per l'account
# che vive nella cartella di default (~/.claude). Con la variabile presente Claude
# Code legge lo stato in $CLAUDE_CONFIG_DIR/.claude.json invece di ~/.claude.json:
# la sessione parte «nuova», chiede il login e nel frattempo non si registra.
ENVARGS=("${PROF_ENV[@]}")
if [ "$(readlink -f "$CONF_DIR")" != "$(readlink -f "$(eval echo ~)/.claude")" ]; then
  ENVARGS=(CLAUDE_CONFIG_DIR="$CONF_DIR" "${PROF_ENV[@]}")
fi
[ "${#ENVARGS[@]}" -eq 0 ] && ENVARGS=(CM_LAUNCHED=1)   # `env` vuole almeno un argomento prima del comando
LABEL="$(cm_get "$ACCOUNT" LABEL)"

# --- --host: la sessione parte su un altro host (cm-rsession.py: ammissione, fotografia, avvio, link) ---------
if [ -n "$HOST" ]; then
  exec python3 "$CM_SCRIPTS/cm-rsession.py" launch "$HOST" "$CARTELLA" "$BASE" -- "${ARGS[@]}"
fi

# --- --bg: nel gestore di background di Claude Code, non in tmux (T47) --------------
if [ "$BG" = si ]; then
  BGARGS=(--bg -n "$NOME" "${ARGS[@]}")
  [ "$CM_SESSION_BG_REMOTE_CONTROL" = true ] && BGARGS+=(--remote-control "$NOME")
  ESITO=$(cd "$CARTELLA" && env "${ENVARGS[@]}" "$CLAUDE" "${BGARGS[@]}" 2>&1) || RC=$?
  if [ "${RC:-0}" -ne 0 ]; then
    cm_msg launch.bg_failed "rc=$RC" >&2; printf '%s\n' "$ESITO" | sed 's/^/  /' >&2; exit 7
  fi
  cm_msg launch.bg_started
  echo "  nome:      $NOME"
  echo "  account:   $LABEL${DEDUZIONE:+ ($(cm_msg launch.deduced))}"
  echo "  cartella:  $CARTELLA"
  printf '%s\n' "$ESITO" | sed 's/^/  /'
  cm_msg launch.bg_howto "name=$NOME"
  exit 0
fi

# --- tmux -----------------------------------------------------------------------------
# Remote Control: accounts.<account>.remote_control, se c'e', prevale su session.remote_control (30/09: uno dei due
# account puo' restare senza, l'altro lo usa)
RC="$(cm_get "$ACCOUNT" REMOTE_CONTROL)"; RC="${RC:-$CM_SESSION_REMOTE_CONTROL}"
[ "$RC" = true ] && ARGS+=(--remote-control "$NOME")
ARGS+=(-n "$NOME")
if [ "$AGENT" = codex ]; then   # S09: Codex CLI nudo, senza gli argomenti di Claude Code (-n, remote control, profili)
  cm_tmux new-session -d -s "$NOME" -c "$CARTELLA" env CM_LAUNCHED=1 CM_AGENT=codex "$CODEX"
else
  cm_tmux new-session -d -s "$NOME" -c "$CARTELLA" env "${ENVARGS[@]}" "$CLAUDE" "${ARGS[@]}"
fi
cm_orphan_hook   # 28/10/2026: alla morte del tmux di una sessione il suo claude si chiude (HUP, TERM, KILL)
# Il file di registro NON e' <pane_pid>.json: il binario `claude` si rilancia
# in un figlio (visto dal vivo il 09/09: pane 1122 → sessione registrata 1146),
# quindi si cerca il file che dichiara QUESTA sessione tmux nel campo `tmux`.
# Il file piu' RECENTE fra quelli che dichiarano questa sessione tmux e il cui pid e' vivo: dopo
# un riavvio il file del processo vecchio puo' restare qualche secondo (kill -KILL non esegue la
# pulizia) e `head -1` riportava il suo link (visto nella traccia del 10/09).
reg_file() {
  local f pid
  for f in $(ls -t "$CONF_DIR"/sessions/*.json 2>/dev/null); do
    grep -q "\"tmux\":\"$NOME:" "$f" 2>/dev/null || continue
    pid="${f##*/}"; pid="${pid%%.*}"
    [ -d "/proc/$pid" ] && { printf '%s\n' "$f"; return 0; }
  done
  return 1
}
REG_FILE=""

# T1/T54: il prefisso "=" vale per has-session, non per capture-pane/send-keys.
schermo() { cm_tmux capture-pane -p -t "$NOME" 2>/dev/null; }
e_dialogo() {  # una delle domande da accettare con "Yes" (trust T3, bypass T61)
  # Nessun pattern (il default dal 07/10): nessun dialogo da accettare. `grep -E ""` riconosce ogni riga: su
  # un'installazione nuova launch premeva Giu'+Invio sulla schermata di accesso e aspettava fino allo scadere
  # (prova d'installazione da zero del 13/10).
  [ -n "$CM_SESSION_DIALOG_REGEX" ] || { cat >/dev/null; return 1; }
  grep -qiE "$CM_SESSION_DIALOG_REGEX"
}
# Una schermata a cui risponde la persona: l'accesso di Claude Code, il primo avvio, o un dialogo di fiducia o
# permessi che la config non accetta da se'. Si smette di aspettare la registrazione e si apre la finestra.
PER_LA_PERSONA='How do you want to sign in|Select login method|Choose the text style|Do you trust the|trust (this|the) folder|Is this a project you|Bypass Permissions mode'
per_la_persona() { grep -qiE "$PER_LA_PERSONA"; }
# Attesa dell'avvio: un solo ciclo che sorveglia insieme la registrazione nel
# registro peer (avvio riuscito, 1.2), lo schermo e i dialoghi, che possono
# comparire quando pare (T4: il 2026-08-20 la fiducia e' arrivata dopo un ciclo
# separato di 15 s). Si risponde una volta per dialogo: un secondo Invio finirebbe
# come TESTO nella casella (T3). Dalla 2.1.25x la prima voce e' "No, exit": si porta
# il cursore su "Yes" e si conferma.
RISPOSTE=0; SCHERMO=""; REGISTRATA=no; ASPETTA_PERSONA=no
for _ in $(seq 1 "$CM_SESSION_STARTUP_TIMEOUT_S"); do
  cm_tmux has-session -t "=$NOME" 2>/dev/null || break
  SCHERMO=$(schermo)
  if [ "$AGENT" = codex ]; then   # S09: niente registro peer; il dialogo di fiducia ha gia' «› 1. Yes» scelto: un Invio
    if grep -q "Do you trust the contents of this directory" <<<"$SCHERMO"; then
      # ancora aperto 2 s dopo l'Invio (Codex non ancora in ascolto) → di nuovo, fino a 3 volte
      [ "$RISPOSTE" -lt 3 ] && { cm_tmux send-keys -t "$NOME" Enter; RISPOSTE=$((RISPOSTE + 1)); }
      CX_STABILE=0; sleep 2; continue
    fi
    # pronta quando Codex lo dice (banner, casella del prompt): scrive «> You are in …» PRIMA del dialogo di fiducia, e
    # uscire alla prima riga — o dopo 3 s senza dialogo, con la macchina carica — lasciava il dialogo aperto: il primo
    # `talk` lo «rispondeva» e Codex si chiudeva (prove del 14/09). 8 s senza dialogo: ripiego per altre versioni.
    grep -qE 'OpenAI Codex \(v|Ask Codex to do anything|for shortcuts' <<<"$SCHERMO" && { REGISTRATA=si; break; }
    if [ -n "${SCHERMO//[[:space:]]/}" ]; then CX_STABILE=$(( ${CX_STABILE:-0} + 1 )); else CX_STABILE=0; fi
    [ "$CX_STABILE" -ge 8 ] && { REGISTRATA=si; break; }
    sleep 1; continue
  fi
  if e_dialogo <<<"$SCHERMO"; then
    if [ "$RISPOSTE" -lt 3 ]; then
      grep -qE '❯.*Yes, I' <<<"$SCHERMO" || { cm_tmux send-keys -t "$NOME" Down; sleep 0.5; }
      cm_tmux send-keys -t "$NOME" Enter
      RISPOSTE=$((RISPOSTE + 1))
    fi
    SCHERMO=""; sleep 2; continue
  fi
  REG_FILE=$(reg_file)
  [ -n "$REG_FILE" ] && { REGISTRATA=si; break; }
  per_la_persona <<<"$SCHERMO" && { ASPETTA_PERSONA=si; break; }
  sleep 1
done

# Se Claude muore subito (cartella inaccessibile, avvio fallito) bisogna saperlo
# ora, non scoprirlo dal telefono trovando la lista vuota.
sleep "$CM_SESSION_DEATH_CHECK_S"
if ! cm_tmux has-session -t "=$NOME" 2>/dev/null; then
  cm_msg launch.died "name=$NOME" "dir=$CARTELLA" >&2; exit 5
fi
SCHERMO=$(schermo)
ILLEGGIBILE=no
if [ "$REGISTRATA" = no ]; then
  # T5: uno schermo vuoto qui significa "non sono riuscito a leggere", non "tutto
  # a posto"; ma uscire lasciava la sessione SENZA finestra e il chiamante convinto
  # del contrario (2026-09-02). Si avvisa e si va avanti.
  [ -z "${SCHERMO//[[:space:]]/}" ] && ILLEGGIBILE=si
fi
if e_dialogo <<<"$SCHERMO"; then cm_msg launch.stuck_dialog "name=$NOME" >&2; exit 6; fi

# --- finestra sul desktop, verificata ATTACCATA (T8) ----------------------------------
FINESTRA_APERTA=""
# una launch da una sessione nata sotto il server tmux senza display (14/09/2026): cm_recover_display (cm-lib.sh);
# con --window esplicito anche da un daemon fuori da tmux (reopen dal polso, 15/09)
if [ "$FINESTRA" = true ]; then cm_recover_display "${FINESTRA_CHIESTA:-}" || true; fi
# Backend «none» o nessun display: niente da aprire e niente da aspettare (prima si aspettava
# attach_wait_s per una finestra impossibile: 25 s a ogni riavvio, traccia del 10/09)
if [ "$FINESTRA" = true ] && [ "$("$CM_SCRIPTS/cm-terminal.sh" detect)" = none ]; then
  FINESTRA_IMPOSSIBILE=si
else
  FINESTRA_IMPOSSIBILE=no
fi
if [ "$FINESTRA" = true ] && [ "$FINESTRA_IMPOSSIBILE" = no ]; then
  attaccata() { [ "$(cm_tmux list-sessions -F '#{session_name} #{session_attached}' 2>/dev/null | awk -v n="$NOME" '$1 == n {print $2}')" = 1 ]; }
  MODE=""; [ "$CM_TERMINAL_EPHEMERAL" = true ] && MODE=ephemeral
  # "Finestra aperta" vuol dire ATTACCATA: una scheda che nasce ma non si attacca
  # (visto tre volte il 2026-09-02) lascia la sessione orfana e, con
  # destroy-unattached, la uccide alla prima chiusura. Si verifica, e si riprova una volta.
  "$CM_SCRIPTS/cm-terminal.sh" open "$NOME" $MODE
  for _ in $(seq 1 "$CM_TERMINAL_ATTACH_WAIT_S"); do sleep 1; attaccata && { FINESTRA_APERTA=si; break; }; done
  if [ -z "$FINESTRA_APERTA" ] && [ "$("$CM_SCRIPTS/cm-terminal.sh" detect)" != none ]; then
    "$CM_SCRIPTS/cm-terminal.sh" open "$NOME" $MODE
    for _ in $(seq 1 "$CM_TERMINAL_ATTACH_RETRY_WAIT_S"); do sleep 1; attaccata && { FINESTRA_APERTA=si; break; }; done
  fi
fi

# 21/10/2026 (Claude Code 2.1.304): riprendere una conversazione ferma puo' aprire l'avviso «Resume this conversation?»
# sul costo in quota; senza finestra nessuno risponderebbe. cm-resume-quota.py sceglie o chiede (resume_quota.*)
NOTA_RIPRESA=""
if [ "$AGENT" = claude ] && { [ -n "$RIPRENDI" ] || [ "$CONTINUA" = si ]; } && [ "$ASPETTA_PERSONA" = no ]; then
  NOTA_RIPRESA=$(python3 "$CM_SCRIPTS/cm-resume-quota.py" check "$NOME" ${FINESTRA_APERTA:+--window --wait 2} \
    --resume-id "$RIPRENDI" --folder "$CARTELLA" --account "$ACCOUNT" 2>/dev/null || true)
fi

"$CM_SCRIPTS/cm-registry.sh" >/dev/null 2>&1   # T52: per il ripristino dopo un riavvio

cm_msg launch.started
echo "  nome tmux: $NOME"
echo "  account:   $LABEL${DEDUZIONE:+ ($(cm_msg launch.deduced))}"
echo "  cartella:  $CARTELLA"
if [ "$FINESTRA" = true ]; then
  if [ -n "$FINESTRA_APERTA" ]; then echo "  locale:    $(cm_msg launch.window_attached "name=$NOME")"
  elif [ "$("$CM_SCRIPTS/cm-terminal.sh" detect)" = none ] || [ -z "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then echo "  locale:    $(cm_msg launch.window_skipped "name=$NOME")"
  else echo "  locale:    $(cm_msg launch.window_failed "name=$NOME")"; fi
else
  echo "  locale:    $(cm_msg launch.window_none "name=$NOME")"
fi
[ "$ILLEGGIBILE" = si ] && echo "  nota:      $(cm_msg launch.slow_screen)"
[ "$ASPETTA_PERSONA" = si ] && echo "  nota:      $(cm_msg launch.needs_person "name=$NOME")"
[ -n "$NOTA_RIPRESA" ] && echo "  $NOTA_RIPRESA"
# Il link Remote Control: dal registro (1.2), altrimenti dallo schermo (ripiego T55).
# Il registro nasce PRIMA che il bridge sia connesso: bridgeSessionId arriva qualche secondo
# dopo (con --continue la master ha letto «non ancora nel registro» il 09/09, poi il link c'era):
# si aspetta fino a session.link_wait_s, solo se il Remote Control e' stato chiesto.
# bridgeSessionId porta gia' il prefisso "session_" (visto dal vivo il 09/09): non si raddoppia
leggi_link() {
  [ -n "$REG_FILE" ] || REG_FILE=$(reg_file)
  [ -n "$REG_FILE" ] && [ -f "$REG_FILE" ] && sed -n 's/.*"bridgeSessionId":"\([^"]*\)".*/\1/p' "$REG_FILE" | head -1 | sed 's|^session_||; s|^\(.\)|https://claude.ai/code/session_\1|'
}
LINK=""; ATTESA=0; [ "$RC" = true ] && ATTESA="${CM_SESSION_LINK_WAIT_S:-10}"
[ "$AGENT" = codex ] && ATTESA=0   # S09: Codex non ha Remote Control
[ "$ASPETTA_PERSONA" = si ] && ATTESA=0   # 13/10: prima della risposta della persona il registro non nasce
i=0
while :; do
  LINK=$(leggi_link)
  [ -n "$LINK" ] && break
  [ "$i" -ge "$ATTESA" ] && break
  i=$((i + 1)); sleep 1
done
[ -n "$LINK" ] || LINK=$(grep -oE 'https://claude\.ai/code/session_[A-Za-z0-9]+' <<<"$SCHERMO" | head -1)
if [ -n "$LINK" ]; then echo "  link:      $LINK"; else echo "  telefono:  $(cm_msg launch.no_link)"; fi
