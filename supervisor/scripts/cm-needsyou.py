#!/usr/bin/env python3
"""supervisor needs-you — la sessione dice che le serve la persona (14/10/2026, contratto 1.68).

  supervisor needs-you "<cosa serve>"     questa sessione e' ferma per te: login, un codice, un tasto, un file
  supervisor needs-you --clear            non serve piu' (lo fa anche il prompt dopo, da solo)

Per una cosa che solo la persona puo' fare. La sessione passa a «ferma per te» con il motivo: il relay la mette in
cima a «Serve te» su telefono e orologio, con un avviso e i promemoria, e la master la vede nella sua casella e in una
riga a ogni prompt. Il file e' <state_dir>/needs-you/<session_id>.json; l'hook UserPromptSubmit della sessione lo
toglie al prompt dopo.
"""
import importlib.util
import json
import os
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
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731


def own_row():
    """La riga di `sessions` di chi chiama: la sessione tmux del riquadro (TMUX_PANE), come `talk` per il mittente."""
    sessions = _load("cm-sessions")
    pane = os.environ.get("TMUX_PANE", "")
    name = (sessions.tmux("display-message", "-p", "-t", pane, "#{session_name}") or "").strip() if pane else ""
    if not name:
        return None
    return next((r for r in sessions.collect(read_screen=False) if (r.get("tmux") or r.get("name")) == name and r.get("session_id")), None)


def main(argv):
    # 17/10/2026: `needs-you --list` era diventato il motivo «--list»; un'opzione che non conosce la rifiuta
    if argv and argv[0].startswith("-") and argv[0] != "--clear":
        print(M("needs_you.unknown_option", opt=argv[0]), file=sys.stderr)
        print(M("needs_you.usage"), file=sys.stderr)
        return 2
    row = own_row()
    if not row:
        print(M("needs_you.no_session"), file=sys.stderr)
        return 3
    if argv and argv[0] == "--clear":
        (Path(cm.expand(CFG["state_dir"])) / "needs-you" / f"{row['session_id']}.json").unlink(missing_ok=True)
        print(M("needs_you.cleared"))
        return 0
    reason = " ".join(" ".join(argv).split())[:200]
    if not reason:
        print(M("needs_you.usage"), file=sys.stderr)
        return 2
    return mark(row, reason)


def mark(row, reason):
    """La sessione della riga e' ferma per la persona: il file, la casella della master e la push. Anche da fuori
    (21/10/2026: cm-resume-quota.py, per una sessione senza finestra ferma sull'avviso di ripresa)."""
    d = Path(cm.expand(CFG["state_dir"])) / "needs-you"
    f = d / f"{row['session_id']}.json"
    d.mkdir(parents=True, exist_ok=True)
    name = row.get("tmux") or row.get("name")
    f.write_text(json.dumps({"reason": reason, "at": int(time.time()), "name": name}, ensure_ascii=False))
    # la master lo sa in silenzio: un record gia' consegnato nella sua casella, e la riga del suo hook a ogni prompt
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    if name != master:
        try:
            inbox = _load("cm-inbox")
            rec = inbox.put(master, M("needs_you.to_master", name=name, reason=reason), name)
            inbox.mark(rec["id"], "delivered", "silent")
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
