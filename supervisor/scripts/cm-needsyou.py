#!/usr/bin/env python3
"""supervisor needs-you — la sessione dice che le serve la persona (14/10/2026, contratto 1.68; 23/10, contratto 1.80).

  supervisor needs-you "<cosa serve>" [--until "<comando>"] [--expires 2h]
                                          questa sessione e' ferma per te: login, un codice, un tasto, un file
  supervisor needs-you --clear            non serve piu' (lo fa anche il prompt dopo, da solo)

Per una cosa che solo la persona puo' fare. La sessione passa a «ferma per te» con il motivo: il relay la mette in
cima a «Serve te» su telefono e orologio, con un avviso e i promemoria, e la master la vede nella sua casella e in una
riga a ogni prompt. Il file e' <state_dir>/needs-you/<session_id>.json; l'hook UserPromptSubmit della sessione lo
toglie al prompt dopo.

Come si chiude da sola (23/10/2026, la segnalazione delle 18:39: «le richieste in attesa appaiano solo quando ci sono
davvero e scompaiano quando non ci sono piu'»): con `--until "<comando>"` il relay prova il comando a ogni push (al
massimo una volta al minuto, 10 s di tempo, nella cartella di chi ha chiesto) e quando esce 0 toglie la voce e avvisa
la sessione in casella; con `--expires 2h` (s, m, h, d; senza: 24 h) la voce scade da sola, con un avviso alla
sessione. Dall'app il tasto «Fatto» (op attention_done) la chiude e avvisa la sessione.
"""
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_EXPIRES_S = 86400
DURATION_RE = re.compile(r"^(\d+)([smhd])$")
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731


def parse_duration(text):
    """«2h», «30m», «1d», «90s» → secondi; None se non si legge."""
    m = DURATION_RE.match(str(text or "").strip())
    return int(m.group(1)) * UNITS[m.group(2)] if m else None


def parse_args(argv):
    """(parole del motivo, until, expires_s, errore). 17/10/2026: un'opzione sconosciuta e' un errore, non un motivo."""
    words, until, expires, i = [], None, None, 0
    while i < len(argv):
        a = argv[i]
        if a in ("--until", "--expires"):
            if i + 1 >= len(argv):
                return words, until, expires, M("needs_you.option_needs_value", opt=a)
            if a == "--until":
                until = argv[i + 1].strip()
            else:
                expires = parse_duration(argv[i + 1])
                if expires is None:
                    return words, until, expires, M("needs_you.bad_expires", value=argv[i + 1])
            i += 2
            continue
        if a.startswith("-") and a != "--clear":
            return words, until, expires, M("needs_you.unknown_option", opt=a)
        words.append(a)
        i += 1
    return words, until, expires, None


def own_row():
    """La riga di `sessions` di chi chiama: la sessione tmux del riquadro (TMUX_PANE), come `talk` per il mittente."""
    sessions = _load("cm-sessions")
    pane = os.environ.get("TMUX_PANE", "")
    name = (sessions.tmux("display-message", "-p", "-t", pane, "#{session_name}") or "").strip() if pane else ""
    if not name:
        return None
    return next((r for r in sessions.collect(read_screen=False) if (r.get("tmux") or r.get("name")) == name and r.get("session_id")), None)


def main(argv):
    words, until, expires, err = parse_args(argv)
    if err:
        print(err, file=sys.stderr)
        print(M("needs_you.usage"), file=sys.stderr)
        return 2
    row = own_row()
    if not row:
        print(M("needs_you.no_session"), file=sys.stderr)
        return 3
    if words and words[0] == "--clear":
        (Path(cm.expand(CFG["state_dir"])) / "needs-you" / f"{row['session_id']}.json").unlink(missing_ok=True)
        print(M("needs_you.cleared"))
        return 0
    reason = " ".join(" ".join(words).split())[:200]
    if not reason:
        print(M("needs_you.usage"), file=sys.stderr)
        return 2
    return mark(row, reason, until=until, expires_s=expires, cwd=os.getcwd())


def mark(row, reason, until=None, expires_s=None, cwd=None):
    """La sessione della riga e' ferma per la persona: il file, la casella della master e la push. Anche da fuori
    (21/10/2026: cm-resume-quota.py, per una sessione senza finestra ferma sull'avviso di ripresa). 23/10: `until`
    (il comando che dice «fatto», lo prova il relay), `expires_s` (senza: un giorno), `cwd` (dove provarlo)."""
    d = Path(cm.expand(CFG["state_dir"])) / "needs-you"
    f = d / f"{row['session_id']}.json"
    d.mkdir(parents=True, exist_ok=True)
    name = row.get("tmux") or row.get("name")
    now = int(time.time())
    rec = {"reason": reason, "at": now, "name": name, "expires_at": now + int(expires_s or DEFAULT_EXPIRES_S)}
    if until:
        rec["until"] = str(until)[:500]
        rec["cwd"] = str(cwd or "")
    f.write_text(json.dumps(rec, ensure_ascii=False))
    # la master lo sa in silenzio: un record gia' consegnato nella sua casella, e la riga del suo hook a ogni prompt
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    if name != master:
        try:
            inbox = _load("cm-inbox")
            rec_m = inbox.put(master, M("needs_you.to_master", name=name, reason=reason), name)
            inbox.mark(rec_m["id"], "delivered", "silent")
        except Exception:   # noqa: BLE001
            pass
    # la push subito, come l'hook: con il demone vivo basta toccare la richiesta
    R = CFG.get("relay") or {}
    if R.get("enabled"):
        try:
            rd = Path(cm.expand(R.get("dir") or "~/.cc-supervisor/relay")) / "push-req"
            rd.mkdir(parents=True, exist_ok=True)
            (rd / "hook").touch()
        except OSError:
            pass
    print(M("needs_you.saved", name=name, reason=reason))
    if until:
        print(M("needs_you.saved_until", cmd=rec["until"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
