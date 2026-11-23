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
  tar --extract --file=bundle.tar || fail "bundle.tar"
  mkdir -p src && tar --extract --file=src.tar --directory=src || fail "src.tar"
  if [ -f assets.tar ]; then tar --extract --file=assets.tar --directory="$ROOT/cache/assets" || fail "assets.tar"; fi
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

deps_field() {  # deps_field NAME: the value of the line "NAME=..." of deps.txt
  while IFS= read -r line; do
    case "$line" in "$1="*) printf '%s\n' "${line#*=}" ;; esac
  done < deps.txt
}

cmd_run_job() {
  d="$(jobdir "$1")"; cd "$d" || exit 1
  STARTED="$(now_iso)"; echo $$ > wrapper.pid
  write_status starting "$$"
  # env.txt: the job's own environment, one NAME=VALUE per line, written by cm-offload from the job's "env".
  # The name is what comes before the first "=", the value everything after it.
  if [ -f env.txt ]; then
    while IFS= read -r line; do
      name="${line%%=*}"; value="${line#*=}"
      [ -n "$name" ] && [ "$name" != "$line" ] && export "$name=$value"
    done < env.txt
  fi
  # dipendenze in cache per hash del lockfile (3.2.4): la prima volta girano qui e si spostano in cache.
  # deps.txt has three lines, written by cm-offload: key (the lockfile hash), dir (the folder the command
  # fills), cmd (the command the job's own spec gives).
  if [ -f deps.txt ]; then
    key=$(deps_field key); ddir=$(deps_field dir); dcmd=$(deps_field cmd)
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
  tar --create --file=../out.tar $list || fail "tar"
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
