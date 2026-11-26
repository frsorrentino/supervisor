#!/usr/bin/env bash
# cm-fast.sh pre|post — la porta veloce degli hook che partono a ogni chiamata di strumento (16/11/2026,
# supervisor-hook-lenti-16-11).
#
# Misurato: PreToolUse (Bash, Monitor) e PostToolUse avviavano python a ogni chiamata di strumento, 120-210 ms su una
# macchina veloce e scarica, secondi su una carica (27 scadenze a 5 s in dieci giorni su PreToolUse Bash), per poi non
# fare niente quasi sempre. Qui la decisione «c'e' qualcosa da fare?» si prende in bash, senza avviare altri processi:
#
#   pre   la guardia della corsia (cm-lane-guard.py) serve solo se il comando nomina push, release, publish o tmux:
#         sono le parole di tutto cio' che la guardia sa negare (git push, gh release, npm publish, claude plugin
#         publish, release.sh, tmux send-keys/paste-buffer). Senza, esce 0 e il comando passa, come avrebbe deciso lei.
#   post  cm-hook.py PostToolUse serve solo se la sessione ha una domanda aperta: il file <state_dir>/waiting/<id>.
#         La cartella di stato la scrive cm-hook.py in una cache a ogni suo avvio; senza cache si passa da python,
#         come prima.
#
# Tutto il resto resta com'e': quando serve, python parte con lo stesso input e decide lui. Un errore qui non ferma
# mai una sessione: nel dubbio si passa a python.
set -u
mode="${1:-}"
here="$(dirname "${BASH_SOURCE[0]}")"
in="$(cat)"
# 26/11/2026 (supervisor-directory-hook-credenziali-26-11): l'input passa con un here-string; un a capo in piu' in fondo al JSON non cambia
# niente per json.load
run_py() { exec bash "$here/py.sh" "$@" <<<"$in"; }
case "$mode" in
  pre)
    case "$in" in
      *push*|*release*|*publish*|*tmux*) run_py "$here/cm-lane-guard.py" ;;
    esac
    exit 0 ;;
  post)
    cfg="${CC_SUPERVISOR_CONFIG:-${TEAM_SUPERVISOR_CONFIG:-${CLAUDE_MASTER_CONFIG:-${CM_HOME:-$HOME}/.config/cc-supervisor/config.json}}}"
    key="${cfg//[^A-Za-z0-9]/_}"
    [ "${#key}" -le 180 ] || key="${key: -180}"   # gli ultimi 180 caratteri (un offset negativo oltre la lunghezza da' vuoto)
    cache="${XDG_CACHE_HOME:-$HOME/.cache}/cc-supervisor/state-dir.$key"
    state=""
    [ -r "$cache" ] && IFS= read -r state < "$cache"
    sid="${in#*\"session_id\"}"
    [ "$sid" != "$in" ] || sid=""
    sid="${sid#*\"}"; sid="${sid%%\"*}"
    case "$sid" in *[!A-Za-z0-9._-]*) sid="" ;; esac   # un id che non si legge pulito: decide python
    if [ -n "$state" ] && [ -d "$state" ] && [ -n "$sid" ] && [ ! -e "$state/waiting/$sid" ]; then
      exit 0   # nessuna domanda aperta: niente da chiudere
    fi
    run_py "$here/cm-hook.py" PostToolUse ;;
  *) echo "usage: cm-fast.sh pre|post" >&2; exit 2 ;;
esac
