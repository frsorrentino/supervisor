#!/usr/bin/env python3
"""supervisor handoff — il passaggio di consegne di una sessione su disco, per chi non ha fable-director.

  supervisor handoff --prepare [--here]   stampa PATH, SECTIONS e THEN: dove scrivere e con quali sezioni
  supervisor handoff --written PATH       registra il file scritto (lo annuncia il SessionStart dopo /clear)
  supervisor handoff --announce CWD       usato dal hook: la riga da mettere nel contesto, una volta sola

Il tasto «Handoff» dell'app manda il comando che lo stato indica per la sessione (sessions[].handoff, contratto
1.51): «/fable-director:handoff --here» se la sessione ha fable-director, altrimenti «/supervisor:handoff --here»
(13/10, chiesto dal maintainer: senza fable-director il tasto mandava un comando che non esisteva). Stesso
distillato, stesse sette sezioni; niente controllo di budget, che e' di fable-director.

--here scrive in <cartella>/docs/handoff-AAAA-MM-GG.md (con -HHMM se c'e' gia'); senza, nella cartella di stato
(handoffs/<cartella>-AAAA-MM-GG-HHMM.md). Il puntatore all'ultimo sta in handoffs/last-<hash della cartella>.json e
vale ANNOUNCE_H ore: il SessionStart con source «clear» (o «startup») lo legge e lo dice una volta.
"""
import datetime
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load()
STATE = Path(cm.expand(CFG["state_dir"]))
ANNOUNCE_H = 6
SECTIONS = {
    "en": ["1. Goal and state", "2. Decisions", "3. Verified facts", "4. Open items", "5. Paths and commands",
           "6. Invoke next", "7. Date and staleness"],
    "it": ["1. Obiettivo e stato", "2. Decisioni", "3. Fatti verificati", "4. Cose aperte", "5. Percorsi e comandi",
           "6. Da invocare per primi", "7. Data e scadenza"],
}


def lang():
    return "it" if CFG.get("language") == "it" else "en"


def pointer(cwd):
    h = hashlib.sha1(os.path.realpath(cwd).encode()).hexdigest()[:12]
    return STATE / "handoffs" / f"last-{h}.json"


def target(cwd, here, now):
    day = now.strftime("%Y-%m-%d")
    if here:
        d = Path(cwd) / "docs"
        p = d / f"handoff-{day}.md"
        if p.exists():
            p = d / f"handoff-{day}-{now.strftime('%H%M')}.md"
        return p
    slug = "".join(c if c.isalnum() or c in "-_." else "-" for c in Path(cwd).name) or "root"
    return STATE / "handoffs" / f"{slug}-{day}-{now.strftime('%H%M')}.md"


def prepare(argv, cwd):
    p = target(cwd, "--here" in argv, datetime.datetime.now())
    p.parent.mkdir(parents=True, exist_ok=True)
    print(f"PATH: {p}")
    print("SECTIONS: " + " | ".join(SECTIONS[lang()]))
    print(f"THEN: supervisor handoff --written {p}")
    return 0


def written(path, cwd):
    p = Path(path)
    if not p.is_file():
        print(f"supervisor handoff: {p} not found", file=sys.stderr)
        return 1
    text = p.read_text(encoding="utf-8", errors="replace")
    ptr = pointer(cwd)
    ptr.parent.mkdir(parents=True, exist_ok=True)
    ptr.write_text(json.dumps({"path": str(p), "at": int(time.time()), "cwd": os.path.realpath(cwd)}))
    words = len(text.split())
    print(f"{p} · {words} words, ~{int(words * 1.4)} tokens")
    return 0


def announce(cwd):
    """La riga per il SessionStart: solo un puntatore recente, e una volta sola (poi il puntatore e' consumato)."""
    ptr = pointer(cwd)
    try:
        d = json.loads(ptr.read_text())
    except (OSError, ValueError):
        return ""
    ptr.unlink(missing_ok=True)
    if time.time() - int(d.get("at") or 0) > ANNOUNCE_H * 3600 or not Path(d.get("path") or "").is_file():
        return ""
    hm = time.strftime("%H:%M", time.localtime(int(d["at"])))
    if lang() == "it":
        return f"HANDOFF: prima di tutto leggi {d['path']} (scritto alle {hm} da questa sessione prima di /clear) e riprendi da lì."
    return f"HANDOFF: first read {d['path']} (written at {hm} by this session before /clear) and continue from there."


def main(argv):
    cwd = os.getcwd()
    if argv[:1] == ["--prepare"]:
        return prepare(argv[1:], cwd)
    if argv[:1] == ["--written"] and len(argv) > 1:
        return written(argv[1], cwd)
    if argv[:1] == ["--announce"]:
        line = announce(argv[1] if len(argv) > 1 else cwd)
        if line:
            print(line)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
