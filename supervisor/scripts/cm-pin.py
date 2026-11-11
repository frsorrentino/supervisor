#!/usr/bin/env python3
"""supervisor pin — il PIN degli ok (23/10/2026, il maintainer dalla web alle 17:14: «ok PIN fisso»).

Un ok a nome della persona registrato da una sessione Claude vale solo con una prova che una sessione non puo'
fabbricare per sbaglio o su istruzione. La casella non basta: l'origine «terminal» sono tasti nel pane, anche da un
`tmux send-keys` di un'altra sessione; l'origine «relay» e' un file JSON. Il PIN lo imposta la persona a un
terminale; su disco restano solo sale e hash (PBKDF2-SHA256, 200000 giri, file 0600); «ok 1234» scritto in qualunque
terminale o chat vale, e la sessione che lo riceve lo registra con `supervisor task approve ID --by NOME --pin 1234`,
da qualunque sessione e anche da una shell: la prova e' il PIN, non il terminale.

  supervisor pin set [--by NOME]     imposta o cambia il PIN (4-8 cifre), mai da una sessione Claude (CLAUDECODE):
                                     chiede il PIN attuale se c'e', poi il nuovo, dal terminale senza eco (o da
                                     stdin, una riga ciascuno, per gli script); avvisa la master, riga nel diario
  supervisor pin status [--json]     c'e' o no, di chi, da quando, il blocco; mai l'hash
  supervisor pin clear               toglie il PIN (chiede quello attuale); mai da una sessione Claude

Limiti dichiarati (la scelta del 23/10, al posto del codice monouso sui dispositivi): chi vede il PIN scritto una
volta puo' riusarlo finche' non si cambia — la trascrizione della sessione dove e' stato digitato lo contiene (file di
Claude Code); la casella lo maschera («ok ••••»). Cinque errori in 15 minuti bloccano per 15 minuti, con avviso alla
master e riga nel diario.
"""
import getpass
import hashlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
STATE = Path(cm.expand(CFG["state_dir"]))
PIN_FILE = STATE / "pin.json"
TRIES_FILE = STATE / "pin-attempts.json"
PIN_RE = re.compile(r"^\d{4,8}$")
ITERATIONS = 200000
MAX_FAILS = 5
WINDOW_S = 900
LOCK_S = 900


def _hash(pin, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), ITERATIONS).hex()


def load():
    """Il record del PIN {holder, salt, hash, set_at} o None. Mai stampato intero."""
    try:
        rec = json.loads(PIN_FILE.read_text())
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("salt") and rec.get("hash") else None


def _write_private(path, data):
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _tries():
    try:
        t = json.loads(TRIES_FILE.read_text())
        if isinstance(t, dict):
            return {"fails": [float(x) for x in t.get("fails") or []], "locked_until": float(t.get("locked_until") or 0)}
    except (OSError, ValueError):
        pass
    return {"fails": [], "locked_until": 0.0}


def master_name():
    return str((CFG.get("workspace") or {}).get("root_session_name") or "master")


def _ledger(event, **kw):
    try:
        _load("cm-inbox").ledger(event, **kw)
    except Exception:   # noqa: BLE001 — il diario non ferma un ok
        pass


def _notify_master(text):
    try:
        _load("cm-inbox").put(master_name(), text, "pin", quiet=True)
    except Exception:   # noqa: BLE001
        pass


def _hhmm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def status():
    rec = load()
    t = _tries()
    return {"set": bool(rec), "holder": (rec or {}).get("holder"), "set_at": (rec or {}).get("set_at"),
            "locked_until": t["locked_until"] if t["locked_until"] > time.time() else None,
            "fails": len([x for x in t["fails"] if time.time() - x < WINDOW_S])}


def verify(pin, caller=""):
    """(True, titolare) se il PIN e' quello; altrimenti (False, motivo), senza mai il PIN nel motivo. Cinque errori in
    15 minuti bloccano per 15 minuti (avviso alla master); ogni esito va nel diario con chi ha provato."""
    rec = load()
    if not rec:
        return False, "no PIN set: the person runs `supervisor pin set` at a terminal"
    now = time.time()
    t = _tries()
    if t["locked_until"] > now:
        _ledger("pin-refused", caller=caller, why="locked")
        return False, f"PIN locked until {_hhmm(t['locked_until'])} after {MAX_FAILS} wrong attempts"
    if PIN_RE.match(str(pin or "")) and hmac.compare_digest(_hash(str(pin), rec["salt"]), rec["hash"]):
        _write_private(TRIES_FILE, {"fails": [], "locked_until": 0})
        _ledger("pin-ok", caller=caller, holder=rec.get("holder"))
        return True, str(rec.get("holder") or "owner")
    fails = [x for x in t["fails"] if now - x < WINDOW_S] + [now]
    t = {"fails": fails, "locked_until": 0}
    if len(fails) >= MAX_FAILS:
        t["locked_until"] = now + LOCK_S
        _notify_master(f"PIN degli ok sbagliato {MAX_FAILS} volte in 15 minuti da {caller or 'un terminale'}: bloccato fino alle {_hhmm(t['locked_until'])}")
    _write_private(TRIES_FILE, t)
    _ledger("pin-refused", caller=caller, fails=len(fails))
    if t["locked_until"]:
        return False, f"wrong PIN, the {MAX_FAILS}th in 15 minutes: locked until {_hhmm(t['locked_until'])}"
    return False, f"wrong PIN ({len(fails)} of {MAX_FAILS} before a 15-minute lock)"


def _asker():
    """Il PIN dal terminale senza eco; senza terminale (script, test) una riga di stdin per domanda."""
    if sys.stdin.isatty():
        return lambda prompt: getpass.getpass(prompt).strip()
    return lambda prompt: sys.stdin.readline().strip()


def _not_from_claude(verb):
    if os.environ.get("CLAUDECODE"):
        sys.exit(f"pin {verb}: the person sets the PIN at a terminal, never a Claude session")


def cmd_set(argv):
    _not_from_claude("set")
    by = argv[argv.index("--by") + 1] if "--by" in argv and argv.index("--by") + 1 < len(argv) else ""
    cur = load()
    ask = _asker()
    if cur:
        ok, why = verify(ask("PIN attuale: "), "pin set")
        if not ok:
            sys.exit(f"pin set: {why}")
    new = ask("Nuovo PIN (4-8 cifre): ")
    if not PIN_RE.match(new):
        sys.exit("pin set: the PIN is 4 to 8 digits")
    if sys.stdin.isatty() and ask("Ripeti il nuovo PIN: ") != new:
        sys.exit("pin set: the two PINs differ")
    holder = by or (cur or {}).get("holder") or str((CFG.get("relay") or {}).get("agenda_owner") or "owner")
    salt = secrets.token_hex(16)
    _write_private(PIN_FILE, {"holder": holder, "salt": salt, "hash": _hash(new, salt), "set_at": int(time.time())})
    _write_private(TRIES_FILE, {"fails": [], "locked_until": 0})
    _ledger("pin-set", holder=holder, changed=bool(cur))
    _notify_master(f"PIN degli ok {'cambiato' if cur else 'impostato'} da un terminale alle {_hhmm(time.time())} (titolare {holder})")
    print(f"PIN {'cambiato' if cur else 'impostato'} per {holder}: «ok» seguito dal PIN vale da qualunque terminale o chat")
    return 0


def cmd_clear(argv):
    _not_from_claude("clear")
    if not load():
        print("nessun PIN")
        return 0
    ok, why = verify(_asker()("PIN attuale: "), "pin clear")
    if not ok:
        sys.exit(f"pin clear: {why}")
    PIN_FILE.unlink(missing_ok=True)
    TRIES_FILE.unlink(missing_ok=True)
    _ledger("pin-clear")
    _notify_master(f"PIN degli ok tolto da un terminale alle {_hhmm(time.time())}")
    print("PIN tolto")
    return 0


def cmd_status(argv):
    s = status()
    if "--json" in argv:
        print(json.dumps(s))
        return 0
    if not s["set"]:
        print("nessun PIN: la persona lo imposta a un terminale con `supervisor pin set`")
        return 0
    line = f"PIN impostato per {s['holder']} il {time.strftime('%d/%m %H:%M', time.localtime(s['set_at'] or 0))}"
    if s["locked_until"]:
        line += f"; bloccato fino alle {_hhmm(s['locked_until'])}"
    elif s["fails"]:
        line += f"; {s['fails']} errori negli ultimi 15 minuti"
    print(line)
    return 0


def main(argv):
    verb = argv[0] if argv else "status"
    if verb == "set":
        return cmd_set(argv[1:])
    if verb == "clear":
        return cmd_clear(argv[1:])
    if verb == "status":
        return cmd_status(argv[1:])
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
