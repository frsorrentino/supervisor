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
ver() {  # prima riga di --version, solo la versione
  have "$1" || { printf null; return; }
  v=$("$@" 2>&1 | head -n1 | grep -oE '[0-9]+(\.[0-9]+)+' | head -n1)
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
  grep -qs 'oauth_token' "$HOME/.config/gh/hosts.yml" && addc "gh auth (~/.config/gh/hosts.yml)"
  for k in "$HOME"/.ssh/id_*; do case "$k" in *.pub) ;; *) [ -f "$k" ] && addc "~/.ssh/$(basename "$k")" ;; esac; done
  grep -qs '_authToken' "$HOME/.npmrc" && addc "~/.npmrc (token)"
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
  if [ "$BENCH" = 1 ]; then
    if have node; then b=$(node "$(dirname "$SELF")/cm-bench.js" "$ROOT" 2>/dev/null | tail -n1); fi
    case "$b" in '{'*) ;; *) b=null ;; esac
  fi
  out "{\"ok\":true,\"kind\":\"$kind\",\"os\":$(js "$pretty"),\"os_version\":$(js "$(uname -r)"),\"arch\":$(js "$(uname -m)"),\"cores\":$(num "$c"),\"threads\":$(num "$t"),\
\"ram_gb\":$(gb "$rt"),\"ram_free_gb\":$(gb "$ra"),\"disk_free_gb\":{$(js "$ROOT"):$(num "$(free_gb "$ROOT")")},\"gpu\":[${gpus}],\
\"webgl\":$w,\"bench\":$b,\
\"tools\":{\"node\":$(ver node --version),\"ffmpeg\":$(ver ffmpeg -version),\"python\":$(ver python3 --version),\"git\":$(ver git --version),\
\"tmux\":$(ver tmux -V),\"claude\":$(ver claude --version),\"tar\":$(ver tar --version),\"chrome\":$(js "$(find_chrome)")},\
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

cmd_prepare() {
  d="$(jobdir "$1")"; mkdir -p "$d" "$ROOT/cache/assets" "$ROOT/cache/deps" || fail "mkdir $d"
  out "{\"ok\":true,\"dir\":$(js "$d"),\"free_gb\":$(num "$(free_gb "$d")"),\"sep\":\"/\"}"
}

cmd_missing() {
  d="$(jobdir "$1")"; m=""
  [ -f "$d/manifest.txt" ] || fail "manifest missing"
  while IFS='	' read -r sha size path; do
    [ -n "$sha" ] || continue
    [ -f "$ROOT/cache/assets/$sha" ] || m="$m${m:+,}\"$sha\""
  done < "$d/manifest.txt"
  out "{\"ok\":true,\"missing\":[${m}]}"
}

cmd_unpack() {
  d="$(jobdir "$1")"; cd "$d" || fail "no job dir"
  tar -xf bundle.tar || fail "bundle.tar"
  mkdir -p src && tar -xf src.tar -C src || fail "src.tar"
  if [ -f assets.tar ]; then tar -xf assets.tar -C "$ROOT/cache/assets" || fail "assets.tar"; fi
  n=0
  if [ -f manifest.txt ]; then
    while IFS='	' read -r sha size path; do
      [ -n "$sha" ] || continue
      [ -f "$ROOT/cache/assets/$sha" ] || fail "asset $path not in cache"
      mkdir -p "src/$(dirname "$path")"
      ln -f "$ROOT/cache/assets/$sha" "src/$path" 2>/dev/null || cp "$ROOT/cache/assets/$sha" "src/$path" || fail "link $path"
      n=$((n+1))
    done < manifest.txt
  fi
  rm -f bundle.tar src.tar assets.tar
  out "{\"ok\":true,\"linked\":$n}"
}

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }
write_status() {  # stato [pid] [rc]
  printf '{"state":"%s","pid":%s,"rc":%s,"heartbeat":"%s","started":"%s"}\n' "$1" "$(num "${2:-}")" "$(num "${3:-}")" "$(now_iso)" "${STARTED:-}" > status.json.tmp
  mv -f status.json.tmp status.json
}

cmd_run_job() {
  d="$(jobdir "$1")"; cd "$d" || exit 1
  STARTED="$(now_iso)"; echo $$ > wrapper.pid
  write_status starting "$$"
  if [ -f env.txt ]; then while IFS='=' read -r k v; do [ -n "$k" ] && export "$k=$v"; done < env.txt; fi
  # dipendenze in cache per hash del lockfile (3.2.4): la prima volta girano qui e si spostano in cache
  if [ -f deps.txt ]; then
    key=$(sed -n 's/^key=//p' deps.txt); ddir=$(sed -n 's/^dir=//p' deps.txt); dcmd=$(sed -n 's/^cmd=//p' deps.txt)
    cache="$ROOT/cache/deps/$key"
    if [ ! -f "$cache/.ok" ]; then
      write_status deps "$$"
      ( cd src && sh -c "$dcmd" ) >> log.txt 2>&1 || { write_status failed "$$" 90; exit 90; }
      mkdir -p "$cache" && rm -rf "$cache/$ddir" && mv "src/$ddir" "$cache/$ddir" && touch "$cache/.ok"
    fi
    [ -e "src/$ddir" ] || ln -s "$cache/$ddir" "src/$ddir"
  fi
  to=$(cat timeout.txt 2>/dev/null || echo 0)
  ( cd src && exec sh -c "$(cat ../cmd.txt)" ) >> log.txt 2>&1 &
  child=$!; echo "$child" > child.pid
  write_status running "$child"
  el=0
  while kill -0 "$child" 2>/dev/null; do
    sleep 5; el=$((el+5))
    [ $((el % 15)) = 0 ] && write_status running "$child"
    if [ "${to:-0}" -gt 0 ] && [ "$el" -ge "$to" ]; then kill -TERM "$child" 2>/dev/null; echo "[supervisor] timeout ${to}s" >> log.txt; fi
  done
  wait "$child"; rc=$?
  if [ -f cancelled ]; then write_status cancelled "$child" "$rc"; else
    [ "$rc" = 0 ] && write_status done "$child" 0 || write_status failed "$child" "$rc"; fi
}

cmd_detach() {
  d="$(jobdir "$1")"; [ -f "$d/cmd.txt" ] || fail "cmd.txt missing"
  if [ "$(uname -s)" = Linux ] && have systemd-run && [ -f "/var/lib/systemd/linger/$(id -un)" ]; then
    unit="cm-job-$1"
    systemd-run --user --quiet --collect --unit="$unit" --property=KillMode=control-group sh "$SELF" "$ROOT" run-job "$1" >/dev/null 2>&1 \
      && { out "{\"ok\":true,\"id\":$(js "systemd:$unit")}"; return; }
  fi
  if have setsid; then setsid nohup sh "$SELF" "$ROOT" run-job "$1" </dev/null >/dev/null 2>&1 &
  else nohup sh "$SELF" "$ROOT" run-job "$1" </dev/null >/dev/null 2>&1 & fi
  out "{\"ok\":true,\"id\":$(js "pid:$!")}"
}

cmd_pack() {
  id="$1"; shift; d="$(jobdir "$id")"; cd "$d/src" || fail "no src"
  files=""; list=""
  for p in "$@"; do
    case "$p" in /*|*..*) fail "bad path $p" ;; esac
    [ -e "$p" ] || continue
    list="$list $p"
  done
  [ -n "$list" ] || fail "no output"
  # shellcheck disable=SC2086
  tar -cf ../out.tar $list || fail "tar"
  for f in $(find $list -type f 2>/dev/null); do
    files="$files${files:+,}{\"path\":$(js "$f"),\"sha256\":\"$(sha_of "$f")\",\"size\":$(wc -c < "$f" | tr -d ' ')}"
  done
  out "{\"ok\":true,\"tar\":$(js "$d/out.tar"),\"files\":[${files}]}"
}

kill_tree() {  # pid: il gruppo di processi (setsid) o i discendenti
  p="$1"; [ -n "$p" ] || return
  kill -TERM -- "-$p" 2>/dev/null
  for c in $(pgrep -P "$p" 2>/dev/null); do kill_tree "$c"; done
  kill -TERM "$p" 2>/dev/null
}

cmd_cancel() {
  d="$(jobdir "$1")"; [ -d "$d" ] || fail "no job"
  touch "$d/cancelled"
  systemctl --user stop "cm-job-$1" >/dev/null 2>&1
  kill_tree "$(cat "$d/child.pid" 2>/dev/null)"; kill_tree "$(cat "$d/wrapper.pid" 2>/dev/null)"
  sleep 1
  ( cd "$d" && STARTED="" && write_status cancelled "" "" )
  out '{"ok":true}'
}

cmd_clean() {
  if [ "${1:-}" = --older ]; then
    days="${2:-7}"; n=0
    for d in "$ROOT"/jobs/*/; do
      [ -d "$d" ] || continue
      grep -qE '"state":"(done|failed|cancelled)"' "$d/status.json" 2>/dev/null || continue
      [ -n "$(find "$d/status.json" -mtime +"$days" 2>/dev/null)" ] || continue
      rm -rf "$d" && n=$((n+1))
    done
    out "{\"ok\":true,\"removed\":$n}"; return
  fi
  d="$(jobdir "$1")"
  if [ -f "$d/status.json" ] && grep -qE '"state":"(running|deps|starting)"' "$d/status.json"; then
    kill -0 "$(cat "$d/wrapper.pid" 2>/dev/null)" 2>/dev/null && fail "job running"
  fi
  rm -rf "$d"; [ -e "$d" ] && fail "rm $d"
  out '{"ok":true}'
}

case "$VERB" in
  version)
    here="$(dirname "$SELF")"; v=""
    for f in cm-remote.sh cm-bench.js cm-webgl.html; do
      [ -f "$here/$f" ] && v="$v${v:+,}\"$f\":\"$(sha_of "$here/$f")\""
    done
    out "{\"ok\":true,\"files\":{$v}}" ;;
  probe)   cmd_probe "$@" ;;
  status)  cmd_status "$@" ;;
  prepare) cmd_prepare "$@" ;;
  missing) cmd_missing "$@" ;;
  unpack)  cmd_unpack "$@" ;;
  detach)  cmd_detach "$@" ;;
  run-job) cmd_run_job "$@" ;;
  pack)    cmd_pack "$@" ;;
  cancel)  cmd_cancel "$@" ;;
  clean)   cmd_clean "$@" ;;
  *) fail "unknown verb $VERB" ;;
esac
