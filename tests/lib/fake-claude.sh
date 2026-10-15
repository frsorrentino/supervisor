#!/usr/bin/env bash
# claude finto per i test di launch/close/restart: disegna le schermate che
# Claude Code 2.1.26x mostra davvero (fixture catturate dal vivo) e risponde ai
# tasti come lui. Nessun modello, nessuna rete.
#
# Scenario da FAKE_CLAUDE_SCENARIO (default "plain"), piu' d'uno separati da virgola:
#   trust         prima chiede la fiducia sulla cartella («❯ No, exit» / «Yes, I trust»): T3
#   trust-late    come trust ma la domanda compare dopo FAKE_CLAUDE_DELAY secondi (default 4): T4
#   bypass        dialogo «Bypass Permissions mode» (T61)
#   slow          schermo vuoto per FAKE_CLAUDE_DELAY secondi prima del prompt (T5)
#   die           esce subito con errore (sessione morta dopo l'avvio)
#   question      dopo il prompt mostra un menu numerato con «Enter to select» (T13)
#   bridge-late   si registra SUBITO ma il bridgeSessionId (link) arriva dopo FAKE_CLAUDE_DELAY secondi,
#                 e niente link sullo schermo: nota (a) della master, 09/09/2026
#   plain         prompt subito
# Scrive il registro peer come Claude Code: $CLAUDE_CONFIG_DIR/sessions/<pid>.json
# (FAKE_CLAUDE_REGISTER=0 per non farlo). Stampa il link Remote Control se
# --remote-control e' fra gli argomenti. Registra gli argomenti ricevuti in
# FAKE_CLAUDE_ARGS_LOG (una riga per avvio) per i test sull'argv (--continue,
# --resume, -n, --remote-control, --bg).
set -u
# Lo scenario si legge da un FILE se FAKE_CLAUDE_SCENARIO_FILE e' impostata: le
# sessioni tmux ereditano l'ambiente del SERVER (quello del primo comando), non
# del chiamante, quindi una variabile cambiata fra un lancio e l'altro non arriva.
SCEN="${FAKE_CLAUDE_SCENARIO:-plain}"
[ -n "${FAKE_CLAUDE_SCENARIO_FILE:-}" ] && [ -f "$FAKE_CLAUDE_SCENARIO_FILE" ] && SCEN="$(cat "$FAKE_CLAUDE_SCENARIO_FILE")"
SCEN=",${SCEN},"
DELAY="${FAKE_CLAUDE_DELAY:-4}"
case "${1:-}" in agents|attach|logs|stop) exit 1 ;; esac
case " $* " in *" --cloud "*|*" -p "*)
  ev=""; IFS=, read -ra _evs <<< "${FAKE_CLAUDE_ECHO_ENV:-}"; for _e in "${_evs[@]}"; do ev="$ev ENV $_e=${!_e:-}"; done
  [ -n "${FAKE_CLAUDE_ARGS_LOG:-}" ] && printf '%s%s\n' "$*" "$ev" >> "$FAKE_CLAUDE_ARGS_LOG"
  # 16/10/2026: la coda notturna chiede stream-json: l'avvio, la risposta e l'evento result, una riga JSON ciascuno
  case " $* " in *" --output-format stream-json "*)
    printf '%s\n' '{"type":"system","subtype":"init","session_id":"fake"}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"Sent to cloud session (fake)"}]}}' \
      '{"type":"result","subtype":"success","is_error":false,"num_turns":1,"result":"Sent to cloud session (fake)","session_id":"fake"}'
    exit 0 ;;
  esac
  echo "Sent to cloud session (fake)"; exit 0 ;;
esac
if [ "${1:-}" = remote-control ]; then [ -n "${FAKE_CLAUDE_ARGS_LOG:-}" ] && printf '%s\n' "$*" >> "$FAKE_CLAUDE_ARGS_LOG"; echo "Remote Control server (fake) https://claude.ai/code/session_01FAKEDESK"; sleep 600; exit 0; fi
if [ -n "${FAKE_CLAUDE_ARGS_LOG:-}" ]; then
  ev=""; IFS=, read -ra _evs <<< "${FAKE_CLAUDE_ECHO_ENV:-}"; for _e in "${_evs[@]}"; do ev="$ev ENV $_e=${!_e:-}"; done
  printf '%s%s\n' "$*" "$ev" >> "$FAKE_CLAUDE_ARGS_LOG"
fi
NAME=""; RC=""; BG=no; args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    -n|--name) NAME="${args[$((i+1))]:-}" ;;
    --remote-control) RC="${args[$((i+1))]:-auto}" ;;
    --bg|--background) BG=yes ;;
    --version|-v) echo "2.1.265 (fake)"; exit 0 ;;
  esac
done

if [ "$BG" = yes ]; then
  echo "Background session started: bg-$$"
  echo "  claude attach bg-$$"
  exit 0
fi

case "$SCEN" in *,die,*) echo "Error: cannot start (fake die)" >&2; exit 1 ;; esac

register() {
  [ "${FAKE_CLAUDE_REGISTER:-1}" = 1 ] || return 0
  local dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions" ps tm
  mkdir -p "$dir"
  ps=$(awk '{print $22}' /proc/$$/stat)
  tm=$(tmux display-message -p '#S:@#{window_index}.%#{pane_index}' 2>/dev/null || echo "")
  printf '{"pid":%s,"sessionId":"fake-%s","cwd":"%s","startedAt":%s,"procStart":"%s","version":"2.1.265","kind":"interactive","tmux":"%s","messagingSocketPath":"/tmp/fake-%s.sock","name":"%s","status":"idle","bridgeSessionId":"%s"}\n' \
    "$$" "$$" "$PWD" "$(date +%s)000" "$ps" "$tm" "$$" "${NAME:-$(basename "$PWD")}" "$(case "$SCEN" in *,bridge-late,*) ;; *) printf '%s' "${RC:+session_01FAKE$$}";; esac)" > "$dir/$$.json"
  case "$SCEN" in *,bridge-late,*)
    ( sleep "$DELAY"; sed -i "s/\"bridgeSessionId\":\"\"/\"bridgeSessionId\":\"session_01FAKE$$\"/" "$dir/$$.json" ) & ;;
  esac
  printf '{"peerToken":"faketoken%s","procStart":"%s"}\n' "$$" "$ps" > "$dir/$$.fakehash.key"
  trap 'rm -f "$dir/$$.json" "$dir/$$.fakehash.key"; exit' EXIT INT TERM HUP   # anche su kill-server (SIGHUP)
}

dialog() {  # $1 = testo della domanda, $2 = voce si'
  printf '\033[2J\033[H'   # Claude Code ridisegna lo schermo: il dialogo precedente sparisce
  printf '\n %s\n ❯ No, exit\n   %s\n Enter to confirm · Esc to cancel\n' "$1" "$2"
  local sel=0 k
  while IFS= read -rsn1 k; do
    case "$k" in
      $'\x1b') read -rsn2 -t 0.2 k2 2>/dev/null || true
               case "${k2:-}" in '[B') sel=1 ;; '[A') sel=0 ;; esac
               [ "$sel" = 1 ] && printf '\n   No, exit\n ❯ %s\n' "$2" || printf '\n ❯ No, exit\n   %s\n' "$2" ;;
      "")      if [ "$sel" = 1 ]; then return 0; else echo "exit (No)"; exit 0; fi ;;
      1)       echo "exit (typed 1 = No)"; exit 0 ;;
      2)       return 0 ;;
    esac
  done
}

case "$SCEN" in *,trust-late,*) sleep "$DELAY";; esac
case "$SCEN" in *,trust,*|*,trust-late,*)
  dialog "Quick safety check: Is this a project you created or one you trust? (Like your own code, a well-known open source project, or work from your team). If not, take a moment to review what's in this folder first." "Yes, I trust this folder" ;;
esac
case "$SCEN" in *,bypass,*)
  dialog "WARNING: Claude Code running in Bypass Permissions mode" "Yes, I accept" ;;
esac
case "$SCEN" in *,slow,*) sleep "$DELAY";; esac

register
clear 2>/dev/null || true
echo " ▐▛███▛█   Claude Code v2.1.265 (fake)"
case "$SCEN" in *,bridge-late,*) ;; *) [ -n "$RC" ] && echo "▎ Keep working from anywhere: https://claude.ai/code/session_01FAKE$$";; esac
echo "──────────────────────────── ${NAME:-$(basename "$PWD")} ─"
menu3() {  # come menu, ma SENZA le righe di descrizione sotto le opzioni
  local q="$1"; shift; local opts=("$@") i
  printf '\033[2J\033[H\n ☐ Via\n%s\n' "$q"
  for i in "${!opts[@]}"; do
    if [ "$i" = 0 ]; then printf '❯ %d. %s\n' $((i+1)) "${opts[$i]}"; else printf '  %d. %s\n' $((i+1)) "${opts[$i]}"; fi
  done
  printf 'Enter to select · ↑/↓ to navigate · Esc to cancel\n'
  while IFS= read -rsn1 k; do :; done
}


menu() {  # $1 = domanda, poi le opzioni: come AskUserQuestion di Claude Code (catturata il 10/09/2026)
  # «Type something.» e' un campo in riga: col cursore sopra si scrive e la voce mostra il testo; Invio a campo
  # vuoto non fa nulla. «Chat about this» (indice n) si raggiunge con le frecce (dal vivo il 15/09).
  local q="$1"; shift; local opts=("$@") sel=0 k k2 i n=${#opts[@]} buf=""
  draw() {
    printf '\033[2J\033[H\n ☐ Colore\n%s\n' "$q"
    for i in "${!opts[@]}"; do
      local lab="${opts[$i]}"; [ "$lab" = "Type something." ] && [ -n "$buf" ] && lab="$buf"
      if [ "$i" = "$sel" ]; then printf '❯ %d. %s\n' $((i+1)) "$lab"; else printf '  %d. %s\n' $((i+1)) "$lab"; fi
      printf '     descrizione %d\n' $((i+1))
    done
    printf '────────\n'
    if [ "$sel" = "$n" ]; then printf '❯ %d. Chat about this\n' $((n+1)); else printf '  %d. Chat about this\n' $((n+1)); fi
    printf 'Enter to select · ↑/↓ to navigate · Esc to cancel\n'
  }
  draw
  while IFS= read -rsn1 k; do
    case "$k" in
      $'\x1b') read -rsn2 -t 0.2 k2 2>/dev/null || true
               case "${k2:-}" in '[B') [ "$sel" -lt "$n" ] && sel=$((sel+1)) ;; '[A') [ "$sel" -gt 0 ] && sel=$((sel-1)) ;; esac
               draw ;;
      "") if [ "$sel" = "$n" ]; then printf '\033[2J\033[H  ⎿  · %s → (chat)\n' "$q"; return; fi
          if [ "${opts[$sel]}" = "Type something." ]; then
            [ -n "$buf" ] || continue
            printf '\033[2J\033[H  ⎿  · %s → %s\n' "$q" "$buf"; return
          fi
          printf '\033[2J\033[H  ⎿  · %s → %s\n' "$q" "${opts[$sel]}"; return ;;
      *) [ "${opts[$sel]:-}" = "Type something." ] && { buf="$buf$k"; draw; } ;;
    esac
  done
}
case "$SCEN" in
  *,question2,*) menu "colore preferito?" "rosso" "blu" "verde" "Type something."; menu "taglia?" "S" "M" "L" ;;
  *,question,*)  menu "colore preferito?" "rosso" "blu" "verde" "Type something." ;;
  *,question3,*) menu3 "Procedo?" "Sì" "No" ;;
esac
# 1.12 (16/09/2026): i selettori di /model e /effort come in Claude Code 2.1.273, catturati dal vivo alle 11:30;
# voci di /model aggiornate alla 2.1.280 (22/09/2026) e poi alla 2.1.283 (26/09/2026, lista che scorre: vedi sotto).
# «s» sceglie SOLO per la sessione; Invio salverebbe il default: lo si annota in FAKE_CLAUDE_DEFAULT_LOG, cosi' un
# test puo' dire che nessuno l'ha mai premuto.
model_picker() {
  # 2.1.283 (26/09/2026, dal vivo all'01:20): undici voci con nome e versione («Opus 5.5», non piu' «Opus (1M context)»),
  # sette a schermo; «↑ n.» sulla prima riga visibile quando sopra ce ne sono altre, «↓ n.» sull'ultima quando ce ne
  # sono sotto, «… +4 models» in coda; Giu' oltre l'ultima voce torna alla prima.
  local labels=("Default (recommended)" "Opus 5.5" "Fable 5.1" "Sonnet 5" "Haiku 4.5" "Opus 5" "Fable 5" "Opus 4.8" "Opus 4.7" "Opus 4.6" "Sonnet 4.6")
  local names=("Opus 5.5" "Opus 5.5" "Fable 5.1" "Sonnet 5" "Haiku 4.5" "Opus 5" "Fable 5" "Opus 4.8" "Opus 4.7" "Opus 4.6" "Sonnet 4.6")
  local descs=("Opus 5.5 · Best for everyday, complex tasks" "Most capable for ambitious work" "For your toughest challenges" "Most efficient for everyday tasks" "Fastest for quick answers" "Best for everyday, complex tasks" "Most capable for your hardest and longest-running tasks" "Best for everyday, complex tasks" "Best for everyday, complex tasks" "Best for everyday, complex tasks" "Efficient for routine tasks")
  local sel=1 top=0 n=${#labels[@]} win=7 k k2 i
  draw() {
    [ "$sel" -lt "$top" ] && top=$sel
    [ "$sel" -ge $((top+win)) ] && top=$((sel-win+1))
    printf '\033[2J\033[H'
    printf '   Select model\n   Switch between Claude models. Your pick becomes the default for new sessions.\n'
    for ((i=top; i<top+win && i<n; i++)); do
      local mark="  " tick=""
      [ "$i" = "$top" ] && [ "$top" -gt 0 ] && mark="↑ "
      [ "$i" = $((top+win-1)) ] && [ $((top+win)) -lt "$n" ] && mark="↓ "
      [ "$i" = "$sel" ] && mark="❯ "
      [ "$i" = 1 ] && tick=" ✔"
      printf '   %s%d. %-22s %s\n' "$mark" $((i+1)) "${labels[$i]}$tick" "${descs[$i]}"
    done
    [ "$n" -gt "$win" ] && printf '      … +%d models\n' $((n-win))
    # riquadro stretto: sotto le 40 colonne la lista scorre e la riga finale resta sotto il bordo (dal vivo, 34x48,
    # 16/09 11:55); fra 40 e 60 Claude Code va a capo da se' DENTRO la frase
    if [ "$(tput cols 2>/dev/null || echo 80)" -lt 40 ]; then
      printf '   ○ High effort                    ↓\n'
    elif [ "$(tput cols 2>/dev/null || echo 80)" -lt 60 ]; then
      printf '   ○ High effort ←/→ to adjust\n   Enter to set as default · s to use\n   this session only · Esc to cancel\n'
    else
      printf '   ○ High effort ←/→ to adjust\n   Enter to set as default · s to use this session only · Esc to cancel\n'
    fi
  }
  draw
  while IFS= read -rsn1 k; do
    case "$k" in
      $'\x1b') read -rsn2 -t 0.2 k2 2>/dev/null || true
               case "${k2:-}" in
                 '[B') sel=$(( (sel+1) % n )); draw ;;
                 '[A') sel=$(( (sel-1+n) % n )); draw ;;
                 '[C'|'[D') draw ;;
                 *) printf '\033[2J\033[H  ⎿  Cancelled\n'; return ;;
               esac ;;
      s) # model-compact (2.1.301, 16/10/2026): verso una finestra piu' piccola la conversazione si riassume prima del
         # cambio, con lo spinner a schermo per FAKE_CLAUDE_DELAY secondi; un Esc lo interrompe e il cambio non avviene
         case "$SCEN" in *,model-compact,*)
           local end=$((SECONDS + DELAY)) k3
           printf '\033[2J\033[H✻ Compacting conversation… (esc to interrupt)\n'
           while [ "$SECONDS" -lt "$end" ]; do
             if IFS= read -rsn1 -t 0.2 k3 && [ "$k3" = $'\x1b' ]; then
               [ -n "${FAKE_CLAUDE_KEYS_LOG:-}" ] && echo "Esc during compact" >> "$FAKE_CLAUDE_KEYS_LOG"
               printf '\033[2J\033[H  ⎿  Interrupted · What should Claude do instead?\n'; return
             fi
           done ;;
         esac
         if [ "$(tput cols 2>/dev/null || echo 80)" -lt 60 ]; then printf '\033[2J\033[H  ⎿  Set model to %s for\n     this session only\n' "${names[$sel]}"
         else printf '\033[2J\033[H  ⎿  Set model to %s for this session only\n' "${names[$sel]}"; fi; return ;;
      "") [ -n "${FAKE_CLAUDE_DEFAULT_LOG:-}" ] && echo "model ${names[$sel]}" >> "$FAKE_CLAUDE_DEFAULT_LOG"
          printf '\033[2J\033[H  ⎿  Set model to %s and saved as your default for new sessions\n' "${names[$sel]}"; return ;;
    esac
  done
}
effort_picker() {
  local levels=(low medium high xhigh max ultracode) sel=2 k k2
  draw() {
    printf '\033[2J\033[H   Effort\n   low   medium   high   xhigh   max   ultracode\n   now: %s\n' "${levels[$sel]}"
    if [ "$(tput cols 2>/dev/null || echo 80)" -lt 60 ]; then printf '   ←/→ to adjust · Enter to\n   confirm · s for this session\n   only · Esc to cancel\n'
    else printf '   ←/→ to adjust · Enter to confirm · s for this session only · Esc to cancel\n'; fi
  }
  draw
  while IFS= read -rsn1 k; do
    case "$k" in
      $'\x1b') read -rsn2 -t 0.2 k2 2>/dev/null || true
               case "${k2:-}" in
                 '[C') [ "$sel" -lt 5 ] && sel=$((sel+1)); draw ;;
                 '[D') [ "$sel" -gt 0 ] && sel=$((sel-1)); draw ;;
                 '[A'|'[B') draw ;;
                 *) printf '\033[2J\033[H  ⎿  Cancelled\n'; return ;;
               esac ;;
      s) if [ "$(tput cols 2>/dev/null || echo 80)" -lt 60 ]; then printf '\033[2J\033[H  ⎿  Set effort level to %s (this\n     session only): fake description\n' "${levels[$sel]}"
         else printf '\033[2J\033[H  ⎿  Set effort level to %s (this session only): fake description\n' "${levels[$sel]}"; fi; return ;;
      "") [ -n "${FAKE_CLAUDE_DEFAULT_LOG:-}" ] && echo "effort ${levels[$sel]}" >> "$FAKE_CLAUDE_DEFAULT_LOG"
          printf '\033[2J\033[H  ⎿  Set effort level to %s (saved as your default for new sessions)\n' "${levels[$sel]}"; return ;;
    esac
  done
}
case "$SCEN" in *,busy,*) echo "✻ Working… (esc to interrupt)";; esac
# Claude Code 2.1.294: la riga del turno senza «esc to interrupt» (08/10)
case "$SCEN" in *,busy294,*) echo "● Slithering… (1m 9s · ↓ 2.7k tokens)";; esac
# 1.69 (15/10/2026): il piè di pagina dei permessi e Shift+Tab come in Claude Code 2.1.300, dal vivo con una sessione
# partita con --dangerously-skip-permissions: manual → accept edits → plan → bypass permissions → auto → manual.
#   perm         il giro senza bypass;  perm-bypass  con bypass;  perm-old  il default con «? for shortcuts»
#   perm-lag     il piè si ridisegna FAKE_CLAUDE_DELAY secondi dopo il tasto (dal vivo fino a 5 s)
#   perm-stuck   Shift+Tab non cambia niente;  perm-start=<modalita'> la modalita' di partenza
# Il testo scritto resta sulla riga del prompt; Shift+Tab non lo tocca. Ogni Shift+Tab si annota in FAKE_CLAUDE_KEYS_LOG.
case "$SCEN" in *,perm,*|*,perm-bypass,*|*,perm-old,*|*,perm-lag,*|*,perm-stuck,*)
  modes=(default acceptEdits plan auto); case "$SCEN" in *,perm-bypass,*) modes=(default acceptEdits plan bypassPermissions auto);; esac
  m=0; for i in "${!modes[@]}"; do case "$SCEN" in *",perm-start=${modes[$i]},"*) m=$i;; esac; done
  pbuf=""
  pdraw() {
    local f
    case "${modes[$m]}" in
      default) case "$SCEN" in *,perm-old,*) f="? for shortcuts";; *) f="⏸ manual mode on · ← for agents";; esac ;;
      acceptEdits) f="⏵⏵ accept edits on (shift+tab to cycle) · ← for agents" ;;
      plan) f="⏸ plan mode on (shift+tab to cycle) · ← for agents" ;;
      bypassPermissions) f="⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents" ;;
      auto) f="⏵⏵ auto mode on (shift+tab to cycle) · ← for agents" ;;
    esac
    printf '\033[2J\033[H ▐▛███▛█   Claude Code v2.1.300 (fake)\n'
    case "$SCEN" in *,busy,*) printf '✻ Working… (esc to interrupt)\n';; esac
    printf '────────────────────────────\n❯ %s\n────────────────────────────\n  %s\n' "$pbuf" "$f"
  }
  pdraw
  while IFS= read -rsn1 k; do
    case "$k" in
      $'\x1b') read -rsn2 -t 0.2 k2 2>/dev/null || true
               if [ "${k2:-}" = "[Z" ]; then
                 [ -n "${FAKE_CLAUDE_KEYS_LOG:-}" ] && echo "BTab" >> "$FAKE_CLAUDE_KEYS_LOG"
                 case "$SCEN" in *,perm-stuck,*) continue;; *,perm-lag,*) sleep "$DELAY";; esac
                 m=$(( (m+1) % ${#modes[@]} )); pdraw
               fi ;;
      "") [ "$pbuf" = "/exit" ] && exit 0; pbuf=""; pdraw ;;
      *) pbuf="$pbuf$k"; pdraw ;;
    esac
  done
  exit 0 ;;
esac
printf '❯ '   # senza newline: il testo digitato resta sulla riga del prompt, come in Claude Code
# resta vivo finche' non riceve /exit (o un segnale)
while IFS= read -r line; do
  line="${line//$'\e'/}"   # un ESC arrivato prima del testo (Escape di talk) non e' testo
  case "$line" in /exit|/quit) echo "bye"; exit 0 ;; esac
  case "$SCEN" in *,nopicker,*) ;; *)
    case "$line" in
      /model) model_picker; printf '❯ '; continue ;;
      /effort) effort_picker; printf '❯ '; continue ;;
    esac ;;
  esac
  printf '> %s\n❯ ' "$line"
done
