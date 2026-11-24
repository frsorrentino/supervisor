#!/usr/bin/env python3
"""cm-closesave — prima di chiudere una sessione, il suo passaggio di consegne (19/11/2026, supervisor-chiudi-salva-19-11;
l'utente alle 15:10: «se chiudo da supervisor la sessione prima di exit dovrebbe controllare se occorre salvare stato,
eseguire e poi chiudere»; alle 15:16 i due tasti dell'app, «Salva e chiudi» e «Chiudi senza salvare»).

  cm-closesave.py save NOME [--deadline S]   chiede alla sessione il passaggio di consegne se serve, aspetta la fine
                                             del suo turno (al piu' S secondi, default close.save_timeout_s = 180) e
                                             stampa {handoff, file, reason}; `supervisor close` poi chiude
  cm-closesave.py settle NOME [--wait S]     lo stato della sessione (idle, busy, waiting, ?), aspettando al piu' S
                                             secondi (close.settle_s = 30) che smetta di lavorare
Una sessione che non e' Claude (nessun session_id nel registro: un processo qualsiasi in tmux) non ha niente da salvare.
La sessione decide da se': scrive il passaggio di consegne, o risponde «niente da salvare».

I pezzi qui li usa anche il relay (op close con save, chiusure armate): il testo della richiesta, il passaggio di
consegne scritto dopo un momento, il record della chiusura in <state_dir>/closed/<tmux>.json (quello che lo stato
pubblica in sessions[].closed) e lo stato «sta salvando» in <state_dir>/closing/<tmux>.json per una chiusura dal PC.
"""
import importlib.util
import json
import os
import re
import subprocess
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
STATE = Path(cm.expand(CFG["state_dir"]))
EXPIRED = "save_expired"   # closed.reason: il tempo del salvataggio e' scaduto e la sessione e' stata chiusa lo stesso


def timeout_s():
    return float(os.environ.get("CM_CLOSE_SAVE_TIMEOUT_S") or (CFG.get("close") or {}).get("save_timeout_s") or 180)


def prompt():
    return M("close.save_prompt")


def _safe(tm):
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", tm)


def handoff_since(cwd, since):
    """Il passaggio di consegne scritto per `cwd` da `since` in poi (il puntatore di `supervisor handoff --written`, e
    anche quello di fable-director, che passa da li'), o None."""
    if not cwd:
        return None
    try:
        ho = _load("cm-handoff")
        p = ho.pointer(cwd)
        d = json.loads(p.read_text())
    except (OSError, ValueError, AttributeError):
        return None
    if float(d.get("at") or 0) + 1 >= float(since) and d.get("path") and Path(d["path"]).is_file():
        return d["path"]
    return None


def closing_path(tm):
    return STATE / "closing" / f"{_safe(tm)}.json"


def set_closing(tm, mode, at, deadline):
    p = closing_path(tm)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"tmux": tm, "mode": mode, "at": int(at), "deadline": int(deadline) if deadline else None}))


def clear_closing(tm):
    closing_path(tm).unlink(missing_ok=True)


def closing_all(now=None):
    """{tmux: {mode, at, deadline}} delle chiusure dal PC in corso; una piu' vecchia di un'ora va via."""
    now = now or time.time()
    out = {}
    d = STATE / "closing"
    for f in d.glob("*.json") if d.is_dir() else []:
        try:
            r = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if now - float(r.get("at") or 0) > 3600:
            f.unlink(missing_ok=True)
            continue
        out[r.get("tmux") or f.stem] = {"mode": r.get("mode"), "at": r.get("at"), "deadline": r.get("deadline")}
    return out


def write_closed(row, handoff_file, reason=None, by=None, extra=None):
    """Il record della chiusura (closed_dir del relay): lo stato lo pubblica come sessions[].closed della gone."""
    tm = row.get("tmux") or row.get("name") or ""
    rec = dict(extra or {}, name=row.get("name") or tm, tmux=tm, cwd=row.get("cwd") or "", account=row.get("account") or "",
               session_id=row.get("session_id") or (extra or {}).get("session_id") or None, closed_at=int(time.time()), by=by or None,
               handoff=bool(handoff_file), handoff_file=handoff_file or None, reason=reason or None)
    d = STATE / "closed"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{_safe(tm)}.json").write_text(json.dumps(rec, ensure_ascii=False))
    return rec


def _cli():
    return os.environ.get("CM_CLOSESAVE_CM") or str(HERE / "supervisor")


def _row(name):
    try:
        out = subprocess.run([_cli(), "sessions", "--json", "--no-screen"], capture_output=True, text=True, timeout=60).stdout
        rows = json.loads(out) if out.strip().startswith("[") else []
    except (OSError, ValueError, subprocess.SubprocessError):
        rows = []
    return next((r for r in rows if (r.get("tmux") or r.get("name")) == name), None)


def _stopped_since(sid, since):
    core = _load("cm-core")
    return any(x.get("event") == "stop" and x.get("session_id") == sid and _epoch(x.get("ts")) >= since for x in core.ledger_rows())


def _epoch(ts):
    import datetime
    try:
        return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def save(name, deadline_s=None, remote=False):
    """Il salvataggio prima di `close`. → {handoff, file, reason}. Locale: la sessione deve essere ferma (aspetta che lo
    diventi entro il tempo), poi il prompt, poi la fine del suo turno. Remota (win): il prompt con talk e l'attesa della
    risposta, nello stesso tempo."""
    deadline_s = timeout_s() if deadline_s is None else float(deadline_s)
    t0 = time.time()
    end = t0 + deadline_s
    if remote:
        set_closing(name, "save", t0, end)
        r = subprocess.run([_cli(), "talk", name, prompt(), "--wait", str(int(deadline_s))], capture_output=True, text=True,
                           timeout=deadline_s + 60)
        set_closing(name, "exit", t0, end)
        return {"handoff": False, "file": None, "reason": None if r.returncode == 0 else EXPIRED, "answer": r.stdout.strip()[-400:]}
    row = _row(name)
    if not row or not row.get("session_id"):
        return {"handoff": False, "file": None, "reason": None, "skipped": "not_claude"}
    set_closing(name, "save", t0, end)
    while (row.get("status") != "idle" or row.get("waiting")) and time.time() < end:
        time.sleep(2)
        row = _row(name) or row
    if time.time() >= end:
        set_closing(name, "exit", t0, end)
        return {"handoff": False, "file": None, "reason": EXPIRED, "row": row}
    asked = time.time()
    subprocess.run([_cli(), "talk", name, prompt(), "--no-wait"], capture_output=True, text=True, timeout=120)
    while time.time() < end and not _stopped_since(row["session_id"], asked):
        time.sleep(2)
    done = _stopped_since(row["session_id"], asked)
    f = handoff_since(row.get("cwd"), asked)
    set_closing(name, "exit", t0, end)
    return {"handoff": bool(f), "file": f, "reason": None if done else EXPIRED, "row": row}


def settle_s():
    return float(os.environ.get("CM_CLOSE_SETTLE_S") or (CFG.get("close") or {}).get("settle_s") or 30)


def state_of(name):
    """Lo stato per cm-close.sh, con lo schermo (una domanda a video e' «waiting» solo leggendolo): idle, busy, waiting, ?."""
    try:
        out = subprocess.run([_cli(), "sessions", "--json"], capture_output=True, text=True, timeout=60).stdout
        rows = json.loads(out) if out.strip().startswith("[") else []
    except (OSError, ValueError, subprocess.SubprocessError):
        rows = []
    r = next((r for r in rows if (r.get("tmux") or r.get("name")) == name), None)
    if not r:
        return "?"
    return "waiting" if r.get("waiting") else (r.get("status") or "?")


def settle(name, wait_s=None):
    """24/11/2026 (supervisor-close-autoclear-24-11): dopo il salvataggio la sessione puo' essere ancora al lavoro per un momento
    (lo Stop hook del turno del passaggio di consegne, un prompt arrivato in quel mentre): si aspetta che si fermi, al
    piu' wait_s (close.settle_s, 30 s), prima di decidere. Solo busy si aspetta: una domanda aperta non si risolve da sola."""
    wait_s = settle_s() if wait_s is None else float(wait_s)
    end = time.time() + wait_s
    st = state_of(name)
    while st == "busy" and time.time() < end:
        time.sleep(1)
        st = state_of(name)
    return st


def main(argv):
    if argv[:1] == ["save"] and len(argv) >= 2:
        dl = None
        if "--deadline" in argv:
            dl = float(argv[argv.index("--deadline") + 1])
        res = save(argv[1], dl, remote="--remote" in argv)
        row = res.pop("row", None) or {"tmux": argv[1], "name": argv[1]}
        if not res.get("skipped"):
            write_closed(row, res.get("file"), M("close.reason_expired") if res.get("reason") else None, by="pc")
        if "--line" in argv:   # per cm-close.sh: la riga da dire, gia' nella lingua della configurazione
            if not res.get("skipped"):
                key = "close.saved_handoff" if res.get("handoff") else ("close.save_expired" if res.get("reason") else "close.saved_nothing")
                print(M(key, name=argv[1], file=res.get("file") or "", s=int(timeout_s())))
        else:
            print(json.dumps(res, ensure_ascii=False))
        return 0
    if argv[:1] == ["settle"] and len(argv) >= 2:   # per cm-close.sh: lo stato, aspettata la fine del lavoro
        print(settle(argv[1], argv[argv.index("--wait") + 1] if "--wait" in argv else None))
        return 0
    if argv[:1] == ["clear"] and len(argv) == 2:
        clear_closing(argv[1])
        return 0
    if argv[:1] == ["drop"] and len(argv) == 2:   # la chiusura non e' riuscita: niente record, niente «sta chiudendo»
        clear_closing(argv[1])
        (STATE / "closed" / f"{_safe(argv[1])}.json").unlink(missing_ok=True)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
