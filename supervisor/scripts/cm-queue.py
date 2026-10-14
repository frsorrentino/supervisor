#!/usr/bin/env python3
"""supervisor queue — accoda un prompt a una sessione; lo riceve al prossimo Stop (E5, 2.5).

  supervisor queue <nome> "prompt" [--expires <minuti>]   accoda (scadenza default 12 h)
  supervisor queue <nome> "prompt" --at HH:MM             da consegnare dopo quell'ora (oggi, o domani se e' passata)
  supervisor queue --due                                  consegna le voci con l'ora passata alle sessioni ferme
  supervisor queue <nome> --show                          mostra la coda
  supervisor queue <nome> --clear                         svuota

Lo Stop hook della sessione consegna UNA voce per turno con {"decision":"block","reason":...},
mai quando `stop_hook_active` e' vero: la coda si svuota, non gira in tondo. Ogni voce
scade: un lavoro accodato ieri non deve partire domani a sorpresa.
1.63 (14/10, dall'analisi dei concorrenti): una voce con `at` (epoch) aspetta quell'ora. La consegna lo Stop dopo l'ora
se la sessione lavora; se e' ferma la consegna `queue --due`, che `relay ensure` lancia dal cron ogni minuto.
"""
import importlib.util
import json
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
sessions = _load("cm-sessions")
CFG = cm.load(warn=False)


def at_of(hhmm, now=None):
    """L'epoch della prossima HH:MM locale: oggi se non e' ancora passata, altrimenti domani. None se il formato e' sbagliato."""
    m = re.fullmatch(r"([01]?\d|2[0-3])[:.]([0-5]\d)", str(hhmm or "").strip())
    if not m:
        return None
    now = now or time.time()
    lt = time.localtime(now)
    t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(m.group(1)), int(m.group(2)), 0, 0, 0, -1))
    return t if t > now else time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, int(m.group(1)), int(m.group(2)), 0, 0, 0, -1))


def due():
    """Le voci con l'ora passata, per le sessioni ferme (una sessione che lavora la riceve dal suo Stop). Una per
    sessione per giro, consegnata con `talk --no-wait` e tolta dalla coda solo se talk e' riuscito."""
    qd = Path(cm.expand(CFG["state_dir"])) / "queue"
    files = [p for p in qd.glob("*") if p.is_file()] if qd.is_dir() else []
    now = time.time()
    todo = {}
    for p in files:
        try:
            items = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        except (OSError, ValueError):
            continue
        hit = next((i for i in items if i.get("at") and i["at"] <= now and (not i.get("expires") or i["expires"] > now)), None)
        if hit:
            todo[p.name] = (p, items, hit)
    if not todo:
        return 0
    for r in sessions.collect(read_screen=False):
        sid = r.get("session_id") or ""
        if sid not in todo or r.get("status") != "idle" or r.get("waiting") or not r.get("tmux"):
            continue
        p, items, hit = todo[sid]
        t0 = time.time()
        rc = subprocess.run([str(HERE / "supervisor"), "talk", r["tmux"], hit["text"], "--no-wait"], capture_output=True, text=True, timeout=120).returncode
        if rc == 0 and hit.get("origin") == "relay":   # 0319ab: la voce l'ha marcata il relay (mark_queued), non la CLI
            _load("cm-inbox").mark_origin(r["tmux"], hit["text"], t0, "relay")
        if rc == 0:
            rest = [i for i in items if i is not hit]
            p.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in rest)) if rest else p.unlink(missing_ok=True)
            print(cm.msg(CFG, "queue.delivered_due", name=r["tmux"]))
    return 0


def main(argv):
    if argv and argv[0] == "--due":
        return due()
    if not argv:
        print(cm.msg(CFG, "queue.usage"), file=sys.stderr)
        return 2
    name = argv[0]
    row = next((r for r in sessions.collect(read_screen=False) if r["name"] == name or r["tmux"] == name), None)
    if not row or not row.get("session_id"):
        print(cm.msg(CFG, "talk.missing", name=name), file=sys.stderr)
        return 3
    q = Path(cm.expand(CFG["state_dir"])) / "queue" / row["session_id"]
    if "--show" in argv:
        print(q.read_text() if q.is_file() else cm.msg(CFG, "queue.empty", name=name))
        return 0
    if "--clear" in argv:
        q.unlink(missing_ok=True)
        print(cm.msg(CFG, "queue.cleared", name=name))
        return 0
    text = argv[1] if len(argv) > 1 else ""
    if not text or text.startswith("--"):
        print(cm.msg(CFG, "queue.usage"), file=sys.stderr)
        return 2
    minutes = int(argv[argv.index("--expires") + 1]) if "--expires" in argv else 12 * 60
    at = None
    if "--at" in argv:
        at = at_of(argv[argv.index("--at") + 1] if argv.index("--at") + 1 < len(argv) else "")
        if at is None:
            print(cm.msg(CFG, "queue.bad_at"), file=sys.stderr)
            return 2
    if "--at-epoch" in argv:   # 1.63: l'ora calcolata dal telefono (epoch), dal relay
        try:
            at = float(argv[argv.index("--at-epoch") + 1])
        except (IndexError, ValueError):
            print(cm.msg(CFG, "queue.bad_at"), file=sys.stderr)
            return 2
    item = {"text": text, "queued": time.strftime("%Y-%m-%dT%H:%M:%S"), "expires": (at or time.time()) + minutes * 60}
    if at:
        item["at"] = at
    q.parent.mkdir(parents=True, exist_ok=True)
    with open(q, "a") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    n = sum(1 for l in q.read_text().splitlines() if l.strip())
    print(cm.msg(CFG, "queue.scheduled", name=name, t=time.strftime("%H:%M", time.localtime(at))) if at else cm.msg(CFG, "queue.added", name=name, n=n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
