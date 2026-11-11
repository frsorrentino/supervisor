# supervisor — integrazione con la shell (bash e zsh). Si fa `source` dal file rc.
#
# Cosa mette nella shell (tutto da configurazione):
#   - i wrapper `shell.wrappers` (per esempio `claude` e `claude-work`): ogni sessione Claude
#     lanciata a mano nasce DENTRO tmux, con lo stesso schema di nomi di `launch`, il
#     titolo di scheda di `color` e la regola unica «finestra chiusa = sessione finita»;
#   - gli alias `shell.aliases` (lancia, sessioni, chiudi, ...);
#   - il consumo del SEGNAPOSTO (`tile.placeholder_file`, T21): una scheda di terminale
#     che nasce si attacca alla sessione richiesta da `merge`;
#   - l'innesco del RIPRISTINO dopo un riavvio (T20): prima shell interattiva fuori da
#     tmux, macchina su da meno di `restore.uptime_max_min`, nessun server tmux,
#     registro non vuoto → `supervisor restore`.
#
# Perche' ogni sessione nasce in tmux anche se lanciata a mano: una sessione fuori da
# tmux non e' pilotabile (niente talk, niente riavvio che conservi la conversazione).
# Il 2026-07-28 giravano sette sessioni, tutte fuori, tutte irraggiungibili.
#
# Il costo: un python3 (~60 ms) per leggere la configurazione a ogni shell nuova.
# Nei test: CM_HOME, CC_SUPERVISOR_CONFIG, CM_TMUX_ARGS, CM_CLAUDE_BIN, CM_UPTIME_FILE,
# CM_RESTORE_CMD sostituiscono macchina, tmux, claude, uptime e ripristino.

_cm_root="$(cd "$(dirname "${BASH_SOURCE[0]:-${(%):-%x}}")/.." && pwd)"
eval "$(python3 "$_cm_root/scripts/cm-config.py" --sh 2>/dev/null)"
[ -n "${CM_ACCOUNTS_KEYS:-}" ] || return 0

# T22: negli shell snapshot di Claude Code i wrapper vengono catturati ma le funzioni
# di supporto no: senza guardia, `claude` dentro una sessione esplode con "command not
# found". Ogni wrapper controlla che `_cm_wrap` esista, altrimenti claude puro.
_cm_fn_exists() { typeset -f "$1" >/dev/null 2>&1; }   # 11/11: in zsh `declare -F` dichiara un decimale

_cm_tmux() {
  if [ -n "${CM_TMUX_ARGS:-}" ]; then tmux ${CM_TMUX_ARGS} "$@"
  elif [ -n "${CM_TMUX_SOCKET:-}" ]; then tmux -L "$CM_TMUX_SOCKET" "$@"
  else tmux "$@"; fi
}
# 11/11 (supervisor-macos-11-11): niente ${1^^} ne' ${!v}: questo file lo carica anche zsh (macOS) e bash 3.2
_cm_get() { local v; v="CM_ACCOUNTS_$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')_$2"; v="$(printf '%s' "$v" | tr '-' '_')"; eval "printf '%s' \"\${$v:-}\""; }

# La cartella corrente dice quale account SERVIREBBE (folder_map). Qui non si decide
# niente al posto tuo — il comando digitato resta sovrano — ma se i due non coincidono
# lo si dice (T49): sbagliare account non da' errore, consuma la quota sbagliata e la
# sessione non compare nell'app dove la cerchi. Te ne accorgi ore dopo.
_cm_expected_account() {
  local best="" bestlen=0 p a
  while IFS=$'\t' read -r p a; do
    [ -n "$p" ] || continue
    case "$PWD/" in "$p"/*) [ "${#p}" -gt "$bestlen" ] && { best="$a"; bestlen="${#p}"; } ;; esac
  done <<<"${CM_FOLDER_MAP:-}"
  printf '%s' "$best"
}

_cm_wrap() {
  local acc="$1"; shift
  local conf; conf="$(_cm_get "$acc" CONFIG_DIR)"
  local claude="${CM_CLAUDE_BIN:-claude}"
  local -a envargs=()
  # T68: la variabile solo per gli account fuori dalla cartella di default
  [ "$(readlink -f "$conf")" != "$(readlink -f "$HOME/.claude")" ] && envargs=(CLAUDE_CONFIG_DIR="$conf")
  # comandi non interattivi passano dritti (T23): creare una sessione tmux per stampare
  # una riga sarebbe assurdo
  case "${1:-}" in
    --version|-v|--help|-h|-p|--print|mcp|update|doctor|install|agents|attach|logs|stop|plugin|auth|login)
      env ${envargs[@]+"${envargs[@]}"} "$claude" "$@"; return $? ;;
  esac
  local exp; exp="$(_cm_expected_account)"
  if [ -n "$exp" ] && [ "$exp" != "$acc" ] && [ "${CM_ACCOUNTS_WARN_ON_MISMATCH:-true}" = true ]; then
    python3 "$_cm_root/scripts/cm-config.py" --msg shell.account_mismatch "expected=$exp" "asked=$acc" >&2
  fi
  # dentro tmux non si annida (T24); senza tmux, claude puro
  if [ -n "${TMUX:-}" ] || ! command -v tmux >/dev/null 2>&1; then
    env ${envargs[@]+"${envargs[@]}"} "$claude" "$@"; return $?
  fi
  # stesso schema di nomi di launch, cosi' sessions e talk li riconoscono allo stesso modo
  local base nome n=2
  if [ "$(readlink -f "$PWD")" = "$(readlink -f "$(eval echo "$CM_WORKSPACE_ROOT")")" ]; then base="$CM_WORKSPACE_ROOT_SESSION_NAME"
  else base="$(basename "$PWD" | tr "$CM_SESSION_NAME_SANITIZE_CHARS" "$(printf '%*s' "${#CM_SESSION_NAME_SANITIZE_CHARS}" '' | tr ' ' '-')")"; fi
  base="$(_cm_get "$acc" TMUX_PREFIX)$base"
  nome="$base"
  while _cm_tmux has-session -t "=$nome" 2>/dev/null; do nome="$base-$n"; n=$((n + 1)); done
  # Il titolo si scrive qui: il nome tmux e' gia' deciso ed e' la chiave del registro dei colori.
  [ "${CM_TABS_TITLE:-true}" = true ] && printf '\033]0;%s\007' "$("$_cm_root/scripts/cm-color.sh" "$nome")"
  local -a args=()
  [ -n "${CM_SESSION_CLAUDE_ARGS:-}" ] && read -ra args <<<"$CM_SESSION_CLAUDE_ARGS"
  [ "${CM_SESSION_REMOTE_CONTROL:-true}" = true ] && args+=(--remote-control "$nome")
  args+=(-n "$nome")
  [ "${#envargs[@]}" -eq 0 ] && envargs=(CM_LAUNCHED=1)
  _cm_tmux new-session -d -s "$nome" -c "$PWD" env ${envargs[@]+"${envargs[@]}"} "$claude" ${args[@]+"${args[@]}"} "$@"
  # Regola unica (T7): se una finestra si apre su una sessione e poi si chiude, la
  # sessione finisce. destroy-unattached si accende DOPO l'attacco, dentro lo stesso
  # comando: acceso prima ucciderebbe la sessione appena creata, che nasce staccata.
  # Un hook client-detached NON funziona (al distacco session_attached vale ancora 1).
  _cm_tmux attach -t "=$nome" \; set-option -t "$nome" destroy-unattached on
}

# --- wrapper per account (da shell.wrappers: comando<TAB>account) ----------------------
while IFS=$'\t' read -r _cm_cmd _cm_acc; do
  [ -n "$_cm_cmd" ] && [ -n "$_cm_acc" ] || continue
  # T76 (10/09/2026): lo shell snapshot di Claude Code cattura i wrapper per account
  # ma NON gli helper (_cm_fn_exists, _cm_wrap): dentro una sessione ogni `claude --version`
  # stampava «_cm_fn_exists: command not found». Il wrapper usa solo il builtin `typeset` (bash e zsh).
  eval "$_cm_cmd() { if typeset -f _cm_wrap >/dev/null 2>&1; then _cm_wrap '$_cm_acc' \"\$@\"; else command claude \"\$@\"; fi; }"
done <<<"${CM_SHELL_WRAPPERS:-}"

# --- alias (da shell.aliases: alias<TAB>sottocomando) ----------------------------------
while IFS=$'\t' read -r _cm_al _cm_sub; do
  [ -n "$_cm_al" ] && [ -n "$_cm_sub" ] || continue
  alias "$_cm_al=supervisor $_cm_sub"
done <<<"${CM_SHELL_ALIASES:-}"

[ "${CM_SHELL_DISABLE_TERMINAL_TITLE:-true}" = true ] && export CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1

# --- solo nelle shell INTERATTIVE fuori da tmux: segnaposto e ripristino ---------------
case "$-" in *i*) _cm_interactive=1 ;; *) _cm_interactive="${CM_FORCE_INTERACTIVE:-}" ;; esac
if [ -n "$_cm_interactive" ] && [ -z "${TMUX:-}" ]; then
  # Segnaposto (T21): una scheda che nasce si attacca alla sessione richiesta. Tre
  # protezioni, perche' questo blocco gira a OGNI shell interattiva: scade dopo
  # placeholder_ttl_s; si consuma con `mv` (atomico: due schede insieme non possono
  # attaccarsi entrambe alla stessa sessione); solo se la sessione esiste davvero.
  _cm_ph="${CM_TILE_PLACEHOLDER_FILE:-}"
  if [ -n "$_cm_ph" ] && [ -f "$_cm_ph" ]; then
    _cm_age=$(( $(date +%s) - $(stat -c %Y "$_cm_ph" 2>/dev/null || stat -f %m "$_cm_ph" 2>/dev/null || echo 0) ))
    if [ "$_cm_age" -le "${CM_TILE_PLACEHOLDER_TTL_S:-120}" ]; then
      _cm_mine="$_cm_ph.$$"
      if mv "$_cm_ph" "$_cm_mine" 2>/dev/null; then
        _cm_dove=$(cat "$_cm_mine"); rm -f "$_cm_mine"
        if _cm_tmux has-session -t "=$_cm_dove" 2>/dev/null; then
          exec "$_cm_root/scripts/supervisor" attach "$_cm_dove" ephemeral
        fi
      fi
    else
      rm -f "$_cm_ph"
    fi
  fi
  # Ripristino dopo un riavvio (T20): macchina su da poco, nessun server tmux, registro
  # non vuoto. Una chiusura volontaria (uptime alto) non conta: l'utente chiude spesso
  # sessioni a mano, e un avviso su sessioni mancanti sarebbe rumore.
  # Una sola domanda per accensione: `mkdir` e' atomico, la prima shell prende il segno e le
  # altre tacciono. Prima ogni scheda aperta nei primi 15 minuti richiedeva, e quella lasciata
  # senza risposta ripartiva da sola dopo il timeout anche se l'utente aveva detto n in un'altra.
  # 11/11 (supervisor-macos-11-11): su macOS niente /proc/uptime ne' boot_id: l'avvio da sysctl kern.boottime
  _cm_boot=""
  if [ ! -r "${CM_UPTIME_FILE:-/proc/uptime}" ]; then
    _cm_boot="$(sysctl -n kern.boottime 2>/dev/null | sed -n 's/.*sec = \([0-9]*\).*/\1/p')"
  fi
  if [ -n "$_cm_boot" ]; then
    _cm_up_min=$(( ($(date +%s) - _cm_boot) / 60 )); _cm_bootid="boot-$_cm_boot"
  else
    _cm_up_min="$(awk '{print int($1/60)}' "${CM_UPTIME_FILE:-/proc/uptime}" 2>/dev/null)"
    _cm_bootid="$(cat "${CM_BOOT_ID_FILE:-/proc/sys/kernel/random/boot_id}" 2>/dev/null)"
  fi
  if [ "${CM_SHELL_RESTORE_PROMPT:-true}" = true ] && ! _cm_tmux has-session 2>/dev/null \
     && [ "${_cm_up_min:-99999}" -lt "${CM_RESTORE_UPTIME_MAX_MIN:-15}" ] \
     && [ -s "${CM_REGISTRY_FILE:-}" ] && grep -q '"nome"' "$CM_REGISTRY_FILE" \
     && mkdir -p "${CM_STATE_DIR:-$HOME/.claude}/restore" \
     && mkdir "${CM_STATE_DIR:-$HOME/.claude}/restore/asked-$_cm_bootid" 2>/dev/null; then
    python3 "$_cm_root/scripts/cm-config.py" --msg shell.restore_prompt
    ${CM_RESTORE_CMD:-"$_cm_root/scripts/supervisor" restore}
  fi
fi
unset _cm_cmd _cm_acc _cm_al _cm_sub _cm_ph _cm_age _cm_mine _cm_dove _cm_interactive _cm_boot _cm_up_min _cm_bootid
