#!/bin/sh
# supervisor — aiutante remoto per host POSIX (linux-tmux, macos-tmux, wsl). Solo sh; niente jq.
#
#   sh cm-remote.sh ROOT VERB [ARG...]
#
# ROOT e' la radice di lavoro dell'host (remote_root in config, ~ espansa qui). Ogni verbo stampa una riga
# sentinella e poi UNA riga JSON: chi chiama legge solo quello che segue la sentinella, cosi' il rumore del
# profilo o di motd non rompe il protocollo. Verbi (piano multi-PC 2.1, 3.2, 3.4):
#   version                 sha256 di questo file
#   probe [--bench] [--webgl]   sistema, GPU, strumenti, account, credenziali estranee; micro-benchmark a richiesta
#   status [ID...]          carico, RAM, disco, stato dei lavori, registro peer delle sessioni, file quota
#   prepare ID              crea la cartella del lavoro; spazio libero
#   missing ID              gli sha256 del manifesto (manifest.txt) che la cache non ha
#   unpack ID               estrae bundle.tar: sorgente, asset nella cache, collegamento degli asset nel lavoro
#   detach ID               avvia run-job fuori dalla sessione ssh (systemd-run --user con linger, altrimenti setsid)
#   run-job ID              (interno) dipendenze in cache, comando, status.json con heartbeat ogni 15 s, log.txt
#   pack ID PATH...         out.tar dei percorsi di src/ richiesti, con lo sha256 di ogni file
#   cancel ID               ferma l'albero dei processi del lavoro
#   clean ID | --older D    toglie la cartella del lavoro (o quelle finite da piu' di D giorni)
set -u
SENT='@@CM-JSON@@'
ROOT="${1:?root}"; VERB="${2:?verb}"; shift 2
case "$ROOT" in "~"|"~/"*) ROOT="$HOME${ROOT#\~}" ;; esac
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

js() {  # stringa JSON: niente caratteri di controllo, \ e " protetti
  printf '"%s"' "$(printf '%s' "$1" | tr -d '\000-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"
}
num() { case "${1:-}" in ''|*[!0-9.]*) printf null ;; *) printf '%s' "$1" ;; esac; }
out() { printf '%s\n%s\n' "$SENT" "$1"; }
fail() { out "{\"ok\":false,\"error\":$(js "$1")}"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
jobdir() { case "${1:-}" in ''|*/*|.*) fail "bad job id" ;; esac; printf '%s/jobs/%s' "$ROOT" "$1"; }
sha_of() { if have sha256sum; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
is_json_obj() { [ -s "$1" ] && [ "$(head -c1 "$1")" = "{" ] && tail -c 4 "$1" | tr -d ' \r\n' | grep -q '}$'; }
threads() { nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1; }
ram_kb() {  # totale libera (kB)
  [ -n "${CM_FAKE_RAM_KB:-}" ] && { printf '%s\n' "$CM_FAKE_RAM_KB"; return; }   # solo prove: «totale libera»
  if [ -r /proc/meminfo ]; then
    awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{print t, a}' /proc/meminfo
  else
    t=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024 ))
    f=$(vm_stat 2>/dev/null | awk '/free|inactive|speculative/{gsub(/\./,"",$NF); s+=$NF} END{print s*4}')
    echo "$t ${f:-0}"
  fi
}
gb() { awk -v k="${1:-0}" 'BEGIN{printf "%.1f", k/1048576}'; }
free_gb() {  # spazio libero sulla cartella (o sul primo antenato che esiste)
  d="$1"; while [ ! -d "$d" ] && [ "$d" != / ]; do d="$(dirname "$d")"; done
  df -Pk "$d" 2>/dev/null | awk 'NR==2{printf "%.1f", $4/1048576}'
}
disk_of() {  # 1.75: {path, free_b, total_b} of the filesystem holding the folder (or its first existing ancestor)
  d="$1"; while [ ! -d "$d" ] && [ "$d" != / ]; do d="$(dirname "$d")"; done
  df -Pk "$d" 2>/dev/null | awk 'NR==2{p=$6; gsub(/["\\]/, "", p); printf "{\"path\":\"%s\",\"free_b\":%.0f,\"total_b\":%.0f}", p, $4*1024, $2*1024}' | grep . || printf null
}
load_pct() {
  [ -n "${CM_FAKE_LOAD_PCT:-}" ] && { printf '%s' "$CM_FAKE_LOAD_PCT"; return; }   # solo prove
  l=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}')
  awk -v l="${l:-0}" -v t="$(threads)" 'BEGIN{printf "%d", (l/t)*100}'
}
version_in() {  # the version number in the first line of a tool's own output, as JSON (null if none)
  v=$(printf '%s\n' "$1" | head -n1 | grep -oE '[0-9]+(\.[0-9]+)+' | head -n1)
  [ -n "$v" ] && js "$v" || printf null
}

find_chrome() {
  for c in google-chrome google-chrome-stable chromium chromium-browser; do have "$c" && { command -v "$c"; return; }; done
  for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
           "$ROOT"/cache/deps/*/node_modules/.remotion/chrome-headless-shell/*/*/chrome-headless-shell \
           "$HOME"/.cache/ms-playwright/chromium*/chrome-linux/chrome; do
    [ -x "$c" ] && { printf '%s' "$c"; return; }
  done
}

webgl() {  # {renderer, hardware, ms} da un Chrome headless; null senza Chrome
  c="$(find_chrome)"; [ -n "$c" ] || { printf null; return; }
  page="$(dirname "$SELF")/cm-webgl.html"; [ -f "$page" ] || { printf null; return; }
  case "$(uname -s)" in Darwin) angles="metal" ;; *) angles="gl-egl" ;; esac
  for a in $angles; do
    ud="$(mktemp -d)"
    r=$(timeout 20 "$c" --headless=new --ignore-gpu-blocklist --use-gl=angle --use-angle="$a" --no-first-run \
        --user-data-dir="$ud" --dump-dom "file://$page" 2>/dev/null | sed -n 's/.*CMWEBGL\(.*\)CMWEBGL.*/\1/p' | head -n1)
    rm -rf "$ud"
    case "$r" in *renderer*) break ;; esac
  done
  case "$r" in
    *renderer*)
      rend=$(printf '%s' "$r" | sed -n 's/.*"renderer":"\([^"]*\)".*/\1/p')
      ms=$(printf '%s' "$r" | sed -n 's/.*"ms":\([0-9.]*\).*/\1/p')
      hw=true; printf '%s' "$rend" | grep -qiE 'swiftshader|llvmpipe|softpipe|software|basic render' && hw=false
      printf '{"renderer":%s,"hardware":%s,"ms":%s,"browser":%s}' "$(js "$rend")" "$hw" "$(num "$ms")" "$(js "$c")" ;;
    *) printf '{"renderer":null,"hardware":false,"ms":null,"browser":%s}' "$(js "$c")" ;;
  esac
}

cmd_probe() {
  BENCH=0; WEBGL=0
  for a in "$@"; do case "$a" in --bench) BENCH=1 ;; --webgl) WEBGL=1 ;; esac; done
  os="$(uname -s)"
  if [ -r /etc/os-release ]; then pretty=$(. /etc/os-release; printf '%s' "${PRETTY_NAME:-$os}")
  elif have sw_vers; then pretty="macOS $(sw_vers -productVersion)"; else pretty="$os"; fi
  kind=linux; [ "$os" = Darwin ] && kind=macos; grep -qi microsoft /proc/version 2>/dev/null && kind=wsl
  t=$(threads)
  c=$(lscpu -p=core 2>/dev/null | grep -v '^#' | sort -u | wc -l | tr -d ' ')
  [ "${c:-0}" -gt 0 ] 2>/dev/null || c=$(sysctl -n hw.physicalcpu 2>/dev/null || echo "$t")
  set -- $(ram_kb); rt=$1; ra=$2
  gpus=""
  if have nvidia-smi; then
    gpus=$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>/dev/null \
      | awk -F', *' '{printf "%s{\"name\":\"%s\",\"vram_gb\":%.1f,\"driver\":\"%s\"}", (NR>1?",":""), $1, $2/1024, $3}')
  fi
  if [ -z "$gpus" ] && have lspci; then
    gpus=$(lspci 2>/dev/null | grep -iE 'vga|3d|display' | sed 's/^[^:]*: //' | while IFS= read -r l; do printf '%s{"name":%s}' "${sep:-}" "$(js "$l")"; sep=,; done)
  fi
  if [ -z "$gpus" ] && have system_profiler; then
    gpus=$(system_profiler SPDisplaysDataType 2>/dev/null | sed -n 's/^ *Chipset Model: //p' | while IFS= read -r l; do printf '%s{"name":%s}' "${sep:-}" "$(js "$l")"; sep=,; done)
  fi
  accs=""
  for d in "$HOME"/.claude "$HOME"/.claude-*; do
    [ -f "$d/.credentials.json" ] && { accs="$accs${accs:+,}$(js "$(basename "$d")")"; }
  done
  creds=""
  addc() { creds="$creds${creds:+,}$(js "$1")"; }
  [ -s "$HOME/.git-credentials" ] && addc "~/.git-credentials"
  # only reads gh's hosts.yml to report that a token is there; nothing is written
  grep --quiet --no-messages --fixed-strings --regexp=oauth_token "$HOME/.config/gh/hosts.yml" && addc "gh auth (~/.config/gh/hosts.yml)"
  for k in "$HOME"/.ssh/id_*; do case "$k" in *.pub) ;; *) [ -f "$k" ] && addc "~/.ssh/$(basename "$k")" ;; esac; done
  # only reads ~/.npmrc to report that a token is there; nothing is written
  grep --quiet --no-messages --fixed-strings --regexp=_authToken "$HOME/.npmrc" && addc "~/.npmrc (token)"
  # nomi, mai valori; fuori le variabili di supervisor e di Claude Code stesso
  for v in $(env | cut -d= -f1 | grep -E '(^|_)(TOKEN|SECRET|KEY|PASSWORD)($|_)' | grep -vE '^(CM_|CLAUDE_CODE_)|_KEYS$' || true); do
    addc "env $v"; done
  admin=false; [ "$(id -u)" = 0 ] && admin=true
  id -Gn 2>/dev/null | tr ' ' '\n' | grep -qxE 'sudo|wheel|admin' && admin=sudo
  [ "$admin" = sudo ] && admin='"sudo"'
  linger=false; [ -f "/var/lib/systemd/linger/$(id -un)" ] && linger=true
  mkdir -p "$ROOT" 2>/dev/null
  b=null; w=null
  [ "$WEBGL" = 1 ] && w="$(webgl)"
  # the version of each tool, each one called by its own name
  tool_node=null; have node && tool_node=$(version_in "$(node --version 2>&1)")
  tool_ffmpeg=null; have ffmpeg && tool_ffmpeg=$(version_in "$(ffmpeg -version 2>&1)")
  tool_python=null; have python3 && tool_python=$(version_in "$(python3 --version 2>&1)")
  tool_git=null; have git && tool_git=$(version_in "$(git --version 2>&1)")
  tool_tmux=null; have tmux && tool_tmux=$(version_in "$(tmux -V 2>&1)")
  tool_claude=null; have claude && tool_claude=$(version_in "$(claude --version 2>&1)")
  tool_tar=null; have tar && tool_tar=$(version_in "$(tar --version 2>&1)")
  if [ "$BENCH" = 1 ]; then
    if have node; then b=$(node "$(dirname "$SELF")/cm-bench.js" "$ROOT" 2>/dev/null | tail -n1); fi
    case "$b" in '{'*) ;; *) b=null ;; esac
  fi
  out "{\"ok\":true,\"kind\":\"$kind\",\"os\":$(js "$pretty"),\"os_version\":$(js "$(uname -r)"),\"arch\":$(js "$(uname -m)"),\"cores\":$(num "$c"),\"threads\":$(num "$t"),\
\"ram_gb\":$(gb "$rt"),\"ram_free_gb\":$(gb "$ra"),\"disk_free_gb\":{$(js "$ROOT"):$(num "$(free_gb "$ROOT")")},\"gpu\":[${gpus}],\
\"webgl\":$w,\"bench\":$b,\
\"tools\":{\"node\":$tool_node,\"ffmpeg\":$tool_ffmpeg,\"python\":$tool_python,\"git\":$tool_git,\
\"tmux\":$tool_tmux,\"claude\":$tool_claude,\"tar\":$tool_tar,\"chrome\":$(js "$(find_chrome)")},\
\"accounts\":[${accs}],\"foreign_credentials\":[${creds}],\"admin\":$admin,\"linger\":$linger,\
\"interactive_desktop\":null,\"power\":null,\"home\":$(js "$HOME"),\"root\":$(js "$ROOT"),\"load_pct\":$(load_pct)}"
}

job_json() {  # stato del lavoro: status.json del lavoro + coda del log
  d="$ROOT/jobs/$1"
  st=null; [ -f "$d/status.json" ] && is_json_obj "$d/status.json" && st=$(cat "$d/status.json")
  tl=""; [ -f "$d/log.txt" ] && tl=$(tail -c 400 "$d/log.txt" | tr '\r' '\n' | tail -n 3)
  printf '%s:{"status":%s,"tail":%s}' "$(js "$1")" "$st" "$(js "$tl")"
}

cmd_status() {
  dk="${1:-$ROOT}"   # 1.75: the folder whose disk the status reports (the sessions root), before set -- below
  set -- $(ram_kb); rt=$1; ra=$2
  jobs=""
  if [ -d "$ROOT/jobs" ]; then
    for d in "$ROOT"/jobs/*/; do [ -d "$d" ] || continue; jobs="$jobs${jobs:+,}$(job_json "$(basename "$d")")"; done
  fi
  sess=""
  for cd in "$HOME"/.claude "$HOME"/.claude-*; do
    [ -d "$cd/sessions" ] || continue
    for f in "$cd"/sessions/*.json; do
      [ -f "$f" ] && is_json_obj "$f" || continue
      pid=$(basename "$f" .json); alive=false; kill -0 "$pid" 2>/dev/null && alive=true
      sess="$sess${sess:+,}{\"config_dir\":$(js "$(basename "$cd")"),\"alive\":$alive,\"entry\":$(cat "$f")}"
    done
  done
  quota=""
  for f in "$HOME"/.claude/fable-director/quota-*.json; do
    [ -f "$f" ] && is_json_obj "$f" || continue
    case "$f" in *history*) continue ;; esac
    m=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f")
    quota="$quota${quota:+,}{\"file\":$(js "$(basename "$f")"),\"mtime\":$(num "$m"),\"data\":$(cat "$f")}"
  done
  out "{\"ok\":true,\"at\":$(date +%s),\"load_pct\":$(load_pct),\"threads\":$(threads),\"ram_gb\":$(gb "$rt"),\"ram_free_gb\":$(gb "$ra"),\
\"disk_free_gb\":$(num "$(free_gb "$ROOT")"),\"jobs\":{${jobs}},\"sessions\":[${sess}],\"quota\":[${quota}],\
\"ram_total_b\":$(( rt * 1024 )),\"ram_free_b\":$(( ra * 1024 )),\"disk\":$(disk_of "$dk")}"
}
