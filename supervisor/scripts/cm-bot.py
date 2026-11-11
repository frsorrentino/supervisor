#!/usr/bin/env python3
"""supervisor bot — Telegram a SENSO UNICO: il PC manda, nessuno risponde.

  supervisor bot status    abilitato, token, chat autorizzate, log

Il bot interattivo e' stato ritirato il 16/09/2026 (decisione dell'utente del 15/09): comandi a
parola nuda, tastiere inline, schede, «segui», il daemon in long polling e il digest del mattino
sono spariti. Li faceva per mancanza d'altro, quando il telefono era l'unico modo di guardare le
sessioni da fuori; oggi l'app al polso fa le stesse cose in tempo reale, e Telegram restava a
duplicare gli avvisi (nel log del bot, dal 12/09: una ventina di comandi, un'ottantina di avvisi
doppi, un centinaio di errori di rete).

Resta la spedizione, e solo quella: i testi lunghi che sul polso non si leggono (il diario delle
20:00, il rapporto della notte con `night run --send`), gli avvisi della guardia quota e la scorta
per il polso quando il relay non risponde.

Il riassunto del mattino («cosa aspetta te», il vecchio `bot digest` alle 8:00) non c'e' piu':
al mattino si guarda l'orologio, che mostra senza sosta chi aspetta una risposta e chi e' ferma.
Se dovesse tornare a servire sul telefono, sono una decina di righe sopra `sessions --json`.

Senza token o senza chat autorizzate tutto tace, senza errori. Nessuna dipendenza: urllib.
"""
import importlib.util
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
B = CFG["bot"]
MAX_TEXT = 3900   # Telegram: 4096 caratteri per messaggio


# ------------------------------------------------------------------ file di stato e credenziali
def log_path():
    return Path(cm.expand(B["log"]))


def log(line):
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")


def token():
    """TELEGRAM_BOT_TOKEN dal file .env del plugin (KEY=VALUE, apici tollerati)."""
    p = Path(cm.expand(B["token_file"]))
    try:
        for raw in p.read_text().splitlines():
            k, _, v = raw.strip().partition("=")
            if k.strip() == "TELEGRAM_BOT_TOKEN":
                return v.strip().strip("'\"")
    except OSError:
        return ""
    return ""


def allowed_chats():
    """Le chat autorizzate: `allowFrom` di access.json (stringhe di id)."""
    try:
        d = json.loads(Path(cm.expand(B["access_file"])).read_text())
    except (OSError, ValueError):
        return set()
    return {str(x) for x in d.get("allowFrom", [])}


# ------------------------------------------------------------------ Telegram (solo in uscita)
def api(method, _http_timeout=None, **params):
    url = f"{B['api_base'].rstrip('/')}/bot{token()}/{method}"
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=float(_http_timeout or B["http_timeout_s"])) as r:
        return json.loads(r.read().decode())


def watch_active(now=None):
    """L'orologio e' accoppiato al relay (devices.json non vuoto) e l'ultima push e' andata a buon fine da meno di
    bot.watch_fresh_s (180 s): allora l'app al polso avvisa lei, e Telegram deve restare muto (l'utente via master
    12/09 15:26: le domande arrivavano due volte, notifica dell'app + inoltro Wear OS di quella Telegram)."""
    rd = Path(cm.expand((CFG.get("relay") or {}).get("dir") or "~/.cc-supervisor/relay"))
    try:
        devices = json.loads((rd / "devices.json").read_text())
        last = json.loads((rd / "last-state.json").read_text())
    except (OSError, ValueError):
        return False
    fresh = float(B.get("watch_fresh_s") or 180)
    return bool(devices) and (now or time.time()) - float(last.get("pushed_at") or 0) < fresh


def reply(chat_id, text, parse_mode=None, silent=False, watch_quiet=True):
    """Manda un testo a una chat; torna il message_id (o None). `silent` = disable_notification: il telefono
    vibra per le domande e per quello che l'utente ha chiesto apposta, e nemmeno per quelle se l'orologio
    accoppiato riceve gia' dal relay (bot.quiet_when_watch, default on; `watch_quiet=False` per forzare)."""
    text = text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n…"
    if not silent and watch_quiet and B.get("quiet_when_watch", True) and watch_active():
        silent = True
    try:
        r = api("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true", parse_mode=parse_mode,
                disable_notification="true" if silent else None)
        return ((r or {}).get("result") or {}).get("message_id")
    except urllib.error.HTTPError as e:
        log(f"reply to {chat_id} FAILED: HTTP {e.code}")
        return None
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"reply to {chat_id} FAILED: {e}")
        return None


def send(text, parse_mode=None, silent=False, watch_quiet=True):
    """Manda lo stesso testo a tutte le chat autorizzate; torna quante ne hanno ricevuto. E' l'unico modo di
    mandare su Telegram dal 16/09: diario, rapporto della notte, avvisi della guardia, scorta del polso."""
    if not token():
        return 0
    n = 0
    for c in sorted(allowed_chats()):
        if reply(c, text, parse_mode=parse_mode, silent=silent, watch_quiet=watch_quiet) is not None:
            n += 1
    return n


# ------------------------------------------------------------------ stato
def status():
    print(M("bot.status_enabled", state="true" if B["enabled"] else "false"))
    print(M("bot.status_token", state="ok" if token() else "MISSING", path=B["token_file"]))
    print(M("bot.status_chats", n=len(allowed_chats()), path=B["access_file"]))
    lp = log_path()
    if lp.is_file():
        tail = lp.read_text().splitlines()[-5:]
        print(M("bot.status_log", path=lp))
        for l in tail:
            print("  " + l)
    return 0


def main(argv):
    verb = argv[0] if argv else "status"
    if verb == "status":
        return status()
    print(M("bot.usage"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
