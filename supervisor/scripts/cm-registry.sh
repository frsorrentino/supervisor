#!/usr/bin/env bash
# supervisor registry — fotografa le sessioni Claude vive per il ripristino dopo un riavvio.
#
# Quando la macchina si riavvia (spesso per saturazione della RAM) le sessioni
# muoiono tutte e nessuno ricorda quali erano. Questo file lo ricorda. Lo
# aggiornano `launch` a ogni lancio (T52), gli hook di sessione (fase 4) e un
# cron di riconciliazione; `restore` lo legge.
#
# Fonte: `cm-sessions.py --json --no-screen` (registro peer + /proc). Per ogni
# sessione con un nome tmux: nome, cartella, account. Formato uguale al
# registro legacy (`salvato`, `sessioni[] {nome, cartella, account}`), ma con
# i nomi degli account della configurazione.
#
# Guardia (T19, 07/09/2026): se non trova nessuna sessione NON sovrascrive il
# registro. Dopo un riavvio il cron dei 5 minuti girava prima del ripristino e
# svuotava la lista che il ripristino doveva leggere.
#
# Fotografia «ultimo insieme buono» (`registry.good_file`, 11/09/2026): il mattino
# dell'11/09 l'utente chiuse quattro finestre a mano prima del riavvio, il cron
# riconcilio' il registro con la sola superstite e il ripristino rilancio' 1 su 5.
# La fotografia cresce quando l'insieme cresce e si sostituisce quando tutte le
# sue sessioni sono ancora vive; NON dimagrisce quando le sessioni spariscono da
# sole. Ne esce una sessione solo con una chiusura esplicita (`--closed NOME`:
# `close`, `/exit`). Ogni voce porta `visto`, l'ultima volta che era viva:
# `restore` propone l'unione delle due liste dicendo da dove viene ciascuna.
#
# Uso: supervisor registry                scrive registro e fotografia
#      supervisor registry --closed NOME  chiusura esplicita: NOME esce dalla fotografia
#      supervisor registry --show         stampa il registro corrente   (it: --mostra)
#      supervisor registry --good         stampa la fotografia
set -u
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cm-lib.sh"
REG="$CM_REGISTRY_FILE"
GOOD="$CM_REGISTRY_GOOD_FILE"
CLOSED=""
case "${1:-}" in
  --show|--mostra) cat "$REG" 2>/dev/null || cm_msg registry.none; exit 0 ;;
  --good|--fotografia) cat "$GOOD" 2>/dev/null || cm_msg registry.good_none; exit 0 ;;
  --closed|--chiusa) CLOSED="${2:-}" ;;
esac
command -v tmux >/dev/null || exit 0
mkdir -p "$(dirname "$REG")" "$(dirname "$GOOD")"
# il JSON passa per variabile: `python3 -` legge lo script da stdin, la pipe non ci arriverebbe
CM_SESSIONS_JSON="$(python3 "$CM_SCRIPTS/cm-sessions.py" --json --no-screen 2>/dev/null)" \
python3 - "$REG" "$GOOD" "$CLOSED" <<'PY'
import json, os, sys, time
reg_p, good_p, closed = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    rows = [r for r in json.loads(os.environ.get("CM_SESSIONS_JSON") or "[]") if r.get("tmux") and r.get("cwd")]
except ValueError:
    rows = []
now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
live = {}
for r in rows:
    if r["tmux"] != closed:
        # session_id: la conversazione da riprendere con `reopen` quando la sessione sparisce (15/09)
        live[r["tmux"]] = {"nome": r["tmux"], "cartella": r["cwd"], "account": r["account"], "session_id": r.get("session_id") or ""}


def write(path, data):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# registro riconciliato: mai svuotato (T19)
if live:
    write(reg_p, {"salvato": now, "sessioni": list(live.values())})
# fotografia
try:
    good = {s["nome"]: s for s in json.load(open(good_p)).get("sessioni", [])}
except (OSError, ValueError):
    good = {}
changed = False
if closed and closed in good:
    del good[closed]
    changed = True
if live:
    if set(live) >= set(good):
        good = {n: {**e, "visto": now} for n, e in live.items()}      # tutte ancora vive: la fotografia e' il vivo
    else:
        for n, e in live.items():                                     # dimagrito: le vive si aggiornano, le sparite restano
            good[n] = {**e, "visto": now}
    changed = True
if changed:
    write(good_p, {"salvato": now, "sessioni": list(good.values())})
PY
