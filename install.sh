#!/usr/bin/env bash
# supervisor — the whole install in one line (11/11/2026, supervisor-installazione-semplice-11-11):
#
#   curl -fsSL https://raw.githubusercontent.com/frsorrentino/supervisor/main/install.sh | bash
#
# 1. checks what supervisor needs (bash, python3, tmux, git, crontab, Claude Code) and installs what is missing,
#    after asking: with sudo only when the system's package manager needs it, and it shows the exact line first;
# 2. installs the plugin, or updates it if it is already there;
# 3. runs `supervisor setup`, which asks two things (your projects folder, the app on your phone) and does the rest.
# Run it again at any time: what is in place stays, and it picks up where it stopped.
# Options are passed to `supervisor setup` (e.g. --root ~/projects --phone no --yes). SUPERVISOR_INSTALL_YES=1 answers
# yes to the installs; SUPERVISOR_INSTALL_SOURCE=<folder> installs the plugin from a local checkout. Nothing here runs a model or sends anything anywhere but the package managers and GitHub.
set -u

MARKET="${SUPERVISOR_INSTALL_SOURCE:-frsorrentino/supervisor}"   # a local folder for tests and unreleased trees
PLUGIN="supervisor@supervisor-dev"
MIN_CLAUDE="2.1.292"
it() { case "${LANG:-}${LC_ALL:-}" in it*|*it_IT*) return 0 ;; esac; grep -qi '"language" *: *"ital' "$HOME/.claude/settings.json" 2>/dev/null; }
say() { if it; then printf '%s\n' "$1"; else printf '%s\n' "$2"; fi; }
ok() { printf '  \342\234\223 %s\n' "$*"; }
bad() { printf '  \342\234\227 %s\n' "$*"; }
FAILED=""
fail() { FAILED="${FAILED}${FAILED:+; }$1"; bad "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2"; }

# The questions go to the terminal even under `curl … | bash`, where stdin is the script.
# A terminal only when someone is looking at it: stdout or stderr on a terminal (true under `curl | bash` in a terminal).
# 11/11, the test on a clean WSL: /dev/tty opened there with no terminal at all, and setup waited for an answer forever.
TTY=""
if { [ -t 1 ] || [ -t 2 ]; } && [ -r /dev/tty ] && (exec </dev/tty) 2>/dev/null; then TTY=/dev/tty; fi
ask_yes() {   # $1 = question; yes unless the answer is no; no terminal → SUPERVISOR_INSTALL_YES decides
  [ "${SUPERVISOR_INSTALL_YES:-}" = 1 ] && return 0
  [ -n "$TTY" ] || return 1
  local a; printf '%s [Y/n] ' "$1" >/dev/tty; read -r a </dev/tty || return 1
  case "$a" in n|N|no|No|NO) return 1 ;; esac; return 0
}
version_ge() {   # $1 >= $2, numeri a punti; senza sort -V, che il sort di macOS non ha
  awk -v a="$1" -v b="$2" 'BEGIN { n = split(a, x, "."); m = split(b, y, "."); for (i = 1; i <= (n > m ? n : m); i++) {
    if (x[i] + 0 > y[i] + 0) exit 0; if (x[i] + 0 < y[i] + 0) exit 1 } exit 0 }'
}

say "supervisor: installazione completa. Si può rilanciare: quello che è già a posto resta com'è." \
    "supervisor: complete install. You can run it again: what is already in place stays as it is."

# --- 0. the system ----------------------------------------------------------------------------------------------------
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*)
    say "supervisor gira su Linux, macOS o dentro WSL2 su Windows: apri il terminale di Ubuntu (WSL) e rilancia lì." \
        "supervisor runs on Linux, macOS or inside WSL2 on Windows: open the Ubuntu (WSL) terminal and run it there."
    exit 3 ;;
  Darwin)
    # 11/11 (supervisor-macos-11-11): su macOS supervisor non riconosce ancora le sessioni vive (le legge da /proc) e gli
    # script vogliono bash 4: meglio fermarsi qui con una riga chiara che installare a meta'
    say "supervisor non funziona ancora su macOS: la correzione è in arrivo. Per ora usalo su Linux, ChromeOS o WSL2." \
        "supervisor does not work on macOS yet: a fix is on its way. For now use it on Linux, ChromeOS or WSL2."
    [ "${SUPERVISOR_INSTALL_MACOS:-}" = 1 ] || exit 3
    OS=macos ;;
  *) OS=linux; { [ -n "${WSL_DISTRO_NAME:-}" ] || [ -n "${WSL_INTEROP:-}" ]; } && OS=wsl ;;
esac
PM=""
if [ "$OS" = macos ]; then command -v brew >/dev/null 2>&1 && PM=brew
else for p in apt-get dnf pacman zypper apk; do command -v "$p" >/dev/null 2>&1 && { PM=$p; break; }; done; fi
SUDO="sudo"; [ "$(id -u)" = 0 ] && SUDO=""
pkg_line() {   # $@ = packages as the package manager names them → the exact line
  case "$PM" in
    apt-get) echo "$SUDO apt-get update && $SUDO apt-get install -y $*" ;;
    dnf) echo "$SUDO dnf install -y $*" ;;
    pacman) echo "$SUDO pacman -S --noconfirm $*" ;;
    zypper) echo "$SUDO zypper install -y $*" ;;
    apk) echo "$SUDO apk add $*" ;;
    brew) echo "brew install $*" ;;
  esac
}
pkg_name() {   # $1 = what is needed → the package that brings it on this system
  case "$1:$PM" in
    crontab:apt-get) echo cron ;; crontab:dnf|crontab:pacman|crontab:zypper) echo cronie ;; crontab:apk) echo dcron ;;
    python3:pacman) echo python ;; python3:brew) echo python ;;
    *) echo "$1" ;;
  esac
}

# --- 1. what supervisor needs -----------------------------------------------------------------------------------------
missing=()
for t in bash python3 tmux git curl; do command -v "$t" >/dev/null 2>&1 || missing+=("$t"); done
[ "$OS" = macos ] || command -v crontab >/dev/null 2>&1 || missing+=(crontab)
if [ "${#missing[@]}" -gt 0 ]; then
  pkgs=(); for t in "${missing[@]}"; do pkgs+=("$(pkg_name "$t")"); done
  line="$(pkg_line "${pkgs[@]}")"
  if [ -z "$line" ] && [ "$OS" = macos ]; then
    # macOS senza Homebrew: prima Homebrew (la riga ufficiale), poi i pacchetti; python3 e git vengono anche dagli strumenti
    # da riga di comando di Xcode
    fail "$(say "mancano: ${missing[*]} (e Homebrew, che li installa)" "missing: ${missing[*]} (and Homebrew, which installs them)")" \
      '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" && brew install '"${pkgs[*]}"
  elif [ -z "$line" ]; then
    fail "$(say "mancano: ${missing[*]}" "missing: ${missing[*]}")" "$(say "installali con il gestore di pacchetti del sistema, poi rilancia" "install them with your system's package manager, then run this again")"
  elif ask_yes "$(say "Mancano ${missing[*]}. Li installo con: $line ?" "${missing[*]} missing. Install them with: $line ?")"; then
    bash -c "$line" <"${TTY:-/dev/null}" 2>&1 | tail -3
    still=(); for t in "${missing[@]}"; do command -v "$t" >/dev/null 2>&1 || still+=("$t"); done
    if [ "${#still[@]}" -gt 0 ]; then fail "$(say "ancora mancanti: ${still[*]}" "still missing: ${still[*]}")" "$line"; else ok "${missing[*]}"; fi
  else
    fail "$(say "mancano: ${missing[*]}" "missing: ${missing[*]}")" "$line"
  fi
else
  ok "bash, python3, tmux, git$([ "$OS" = macos ] || echo ", crontab")"
fi
if command -v python3 >/dev/null 2>&1 && ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  fail "$(say "python3 è troppo vecchio (serve 3.8 o più)" "python3 is too old (3.8 or newer needed)")" "$(pkg_line "$(pkg_name python3)")"
fi
# WSL and containers often have crontab but no cron running: the relay would not come back after a restart
if [ "$OS" != macos ] && command -v crontab >/dev/null 2>&1 && ! pgrep -x cron >/dev/null 2>&1 && ! pgrep -x crond >/dev/null 2>&1; then
  cron_line="$SUDO service cron start"; [ "$PM" = dnf ] || [ "$PM" = pacman ] && cron_line="$SUDO systemctl enable --now cronie"
  if ask_yes "$(say "cron non è in esecuzione. Lo avvio con: $cron_line ?" "cron is not running. Start it with: $cron_line ?")"; then
    bash -c "$cron_line" <"${TTY:-/dev/null}" >/dev/null 2>&1 || true
  fi
  if pgrep -x cron >/dev/null 2>&1 || pgrep -x crond >/dev/null 2>&1; then ok cron
  else bad "$(say "cron non è in esecuzione: il relay non si riaccende da solo dopo un riavvio ($cron_line)" "cron is not running: the relay will not come back by itself after a restart ($cron_line)")"; fi
fi

# --- Claude Code --------------------------------------------------------------------------------------------------------
# In WSL the Windows Claude Code can answer through the Windows PATH: sessions need the one inside the distro.
claude_bin() { local c; c="$(command -v claude 2>/dev/null)" || return 1; case "$c" in /mnt/*) return 1 ;; esac; echo "$c"; }
CL_INSTALL="curl -fsSL https://claude.ai/install.sh | PATH=/usr/local/bin:/usr/bin:/bin bash"
if ! claude_bin >/dev/null && [ -x "$HOME/.local/bin/claude" ]; then export PATH="$HOME/.local/bin:$PATH"; fi
if ! claude_bin >/dev/null; then
  if ask_yes "$(say "Claude Code non c'è su questo sistema. Lo installo con: $CL_INSTALL ?" "Claude Code is not on this system. Install it with: $CL_INSTALL ?")"; then
    bash -c "$CL_INSTALL" </dev/null 2>&1 | tail -2
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi
CLAUDE="$(claude_bin || true)"
if [ -z "$CLAUDE" ]; then
  fail "$(say "Claude Code non trovato" "Claude Code not found")" "$CL_INSTALL"
else
  cv="$("$CLAUDE" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  if [ -n "$cv" ] && ! version_ge "$cv" "$MIN_CLAUDE"; then
    ask_yes "$(say "Claude Code $cv è vecchio (serve $MIN_CLAUDE). Lo aggiorno con: claude update ?" "Claude Code $cv is old ($MIN_CLAUDE needed). Update it with: claude update ?")" \
      && "$CLAUDE" update </dev/null >/dev/null 2>&1
    cv="$("$CLAUDE" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  fi
  if [ -n "$cv" ] && version_ge "$cv" "$MIN_CLAUDE"; then ok "Claude Code $cv"
  else fail "$(say "Claude Code ${cv:-?}: serve $MIN_CLAUDE o più" "Claude Code ${cv:-?}: $MIN_CLAUDE or newer needed")" "claude update"; fi
fi

if [ -n "$FAILED" ]; then
  echo
  say "Fermo qui: sistema le righe con ✗ (il comando è sotto ciascuna) e rilancia la stessa riga." \
      "Stopped here: fix the lines marked ✗ (the command is under each one) and run the same line again."
  say "--- da mandare a chi ti aiuta ---" "--- to send to whoever helps you ---"
  printf 'supervisor install.sh · %s · %s · pm %s · claude %s · %s\n' "$OS" "$(uname -sr)" "${PM:--}" "${cv:--}" "$FAILED"
  exit 1
fi

# --- 2. the plugin ------------------------------------------------------------------------------------------------------
if "$CLAUDE" plugin list 2>/dev/null | grep -q "supervisor@"; then
  "$CLAUDE" plugin marketplace update supervisor-dev </dev/null >/dev/null 2>&1 || true
  if "$CLAUDE" plugin update "$PLUGIN" </dev/null >/dev/null 2>&1; then ok "$(say "plugin aggiornato" "plugin updated")"
  else ok "$(say "plugin già installato" "plugin already installed")"; fi
else
  "$CLAUDE" plugin marketplace add "$MARKET" </dev/null >/dev/null 2>&1 || true
  if out="$("$CLAUDE" plugin install "$PLUGIN" --scope user </dev/null 2>&1)"; then ok "$(say "plugin installato" "plugin installed")"
  else
    bad "$(say "plugin non installato" "plugin not installed")"; printf '      %s\n' "$(printf '%s' "$out" | tail -2)"
    say "--- da mandare a chi ti aiuta ---" "--- to send to whoever helps you ---"
    printf 'supervisor install.sh · %s · %s · claude %s · plugin install: %s\n' "$OS" "$(uname -sr)" "${cv:--}" "$(printf '%s' "$out" | tail -1)"
    exit 1
  fi
fi

# --- 3. the rest: supervisor setup ---------------------------------------------------------------------------------------
BIN=""
best=""
for d in "$HOME"/.claude/plugins/cache/supervisor-dev/supervisor/*/; do   # la versione installata piu' alta
  v="$(basename "$d")"; [ -x "$d/bin/supervisor" ] || continue
  if [ -z "$best" ] || version_ge "$v" "$best"; then best="$v"; fi
done
for c in "${best:+$HOME/.claude/plugins/cache/supervisor-dev/supervisor/$best/bin/supervisor}" \
         "$HOME/.claude/plugins/marketplaces/supervisor-dev/supervisor/bin/supervisor"; do
  [ -n "$c" ] && [ -x "$c" ] && { BIN="$c"; break; }
done
if [ -z "$BIN" ]; then
  bad "$(say "il plugin è installato ma il suo comando non si trova" "the plugin is installed but its command is not found")"
  exit 1
fi
echo
if [ -n "$TTY" ]; then exec "$BIN" setup "$@" </dev/tty; fi
exec "$BIN" setup --yes "$@"
