#!/usr/bin/env python3
"""supervisor next — la prossima sessione che ha bisogno di te (2.1).

  supervisor next [--all] [--json] [--attach]

Ordine di urgenza, dai dati di `sessions`:
  1. ferma su una domanda/permesso e NESSUNO la guarda (lavoro fermo che nessuno vedra'), o ferma per te
     (`supervisor needs-you`, 1.68)
  2. ferma su una domanda con una finestra aperta
  3. una scadenza del calendario nel suo periodo di avviso (cm-calendar.py, 16/10): sotto le sessioni ferme, che
     restano piu' urgenti; con `calendar.cases` la voce porta la cartella in cui aprire la sessione
  4. busy da piu' di `sessions.stall_min` minuti senza cambi di stato (stallo, 2.4: informazione, mai azione)
  5. idle da poco (< `sessions.recent_min` minuti): ha appena finito, forse c'e' un esito da leggere
Con 5-8 sessioni su tre monitor e' il gesto che manca: `--attach` apre la prima nel terminale
corrente (o ci passa, se sei gia' in tmux).
"""
import importlib.util
import json
import os
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
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731


def status_age_min(row):
    """Minuti dall'ultimo cambio di stato (statusUpdatedAt del registro peer), o None."""
    try:
        d = json.load(open(Path(row["registry"]) / f"{row['pid']}.json"))
        t = d.get("statusUpdatedAt") or d.get("updatedAt")
        return (time.time() - t / 1000) / 60 if t else None
    except (OSError, ValueError, TypeError):
        return None


def needs_you(sid):
    """Il motivo di `supervisor needs-you` ancora aperto per la sessione, o ''."""
    if not sid:
        return ""
    try:
        return json.loads((Path(cm.expand(CFG["state_dir"])) / "needs-you" / f"{sid}.json").read_text()).get("reason") or "?"
    except (OSError, ValueError, AttributeError):
        return ""


def deadline_rows():
    """Le scadenze in avviso come voci di next (priorita' 3); il calendario spento o irraggiungibile non ne da'."""
    try:
        cal = _load("cm-calendar")
        items, _ = cal.deadlines()
    except Exception:   # noqa: BLE001 — il calendario non deve mai rompere next
        return []
    out = []
    for e in items:
        date = e["date"][8:10] + "/" + e["date"][5:7]
        why = M("next.why_deadline", date=date, when=cal.when(e))
        if e["dir"]:
            why += " · " + M("next.deadline_dir", dir=cm.contract(e["dir"]))
        out.append({"kind": "deadline", "name": e["summary"], "tmux": "", "account": "", "status": "deadline",
                    "priority": 3, "why": why, "status_age_min": None, "date": e["date"], "days_left": e["days_left"],
                    "case": e["case"], "dir": e["dir"]})
    return out


def classify(rows):
    stall = CFG["sessions"]["stall_min"]
    recent = CFG["sessions"]["recent_min"]
    out = []
    for r in rows:
        if r["channel"] == "(questa)":
            continue
        age = status_age_min(r)
        r["status_age_min"] = round(age) if age is not None else None
        nyu = needs_you(r.get("session_id"))
        if nyu:
            r["priority"], r["why"] = 1, M("next.why_needs_you", reason=nyu)
        elif r["waiting"] and not r["attached"]:
            r["priority"], r["why"] = 1, M("next.why_abandoned")
        elif r["waiting"]:
            r["priority"], r["why"] = 2, M("next.why_waiting")
        elif r["status"] == "busy" and age is not None and age >= stall:
            r["priority"], r["why"] = 4, M("next.why_stalled", min=round(age))
        elif r["status"] == "idle" and age is not None and age <= recent:
            r["priority"], r["why"] = 5, M("next.why_recent", min=round(age))
        else:
            r["priority"], r["why"] = 9, ""
        out.append(r)
    out += deadline_rows()
    out.sort(key=lambda r: (r["priority"], -(r["status_age_min"] or 0)))
    return out


def main(argv):
    rows = classify(sessions.collect(read_screen=True))
    show = rows if "--all" in argv else [r for r in rows if r["priority"] < 9]
    if "--json" in argv:
        print(json.dumps(show, ensure_ascii=False, indent=1))
        return 0
    if not show:
        print(M("next.nothing"))
        return 0
    for r in show[: (None if "--all" in argv else 5)]:
        print(f"{r['priority']}  {r['name'] or r['tmux']:<24} {r['account']:<13} {r['status']:<8} {r['why']}")
    first = next((r for r in show if r.get("kind") != "deadline"), None)
    if "--attach" in argv and first:
        name = first["tmux"] or first["name"]
        if os.environ.get("TMUX"):
            os.execvp("tmux", sessions.TMUX + ["switch-client", "-t", f"={name}"])
        os.execv(str(HERE / "cm-attach.sh"), [str(HERE / "cm-attach.sh"), name])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
