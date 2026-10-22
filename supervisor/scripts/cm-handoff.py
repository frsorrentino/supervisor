#!/usr/bin/env python3
"""supervisor handoff — il passaggio di consegne di una sessione su disco, per chi non ha fable-director.

  supervisor handoff --prepare [--here]   stampa PATH, SECTIONS e THEN: dove scrivere e con quali sezioni
  supervisor handoff --written PATH       registra il file scritto (lo annuncia il SessionStart dopo /clear) e vi
                                          aggiunge la sezione «Impegni dal registro» (P5, 22/10: mai scritta a mano)
  supervisor handoff --announce CWD       usato dal hook: la riga da mettere nel contesto, una volta sola
  supervisor handoff --snapshot CWD NAME  usato da `restart arm` nella master: la sezione impegni nel passaggio di
                                          consegne recente, o in handoffs/impegni-NAME-<data>-<ora>.md con il puntatore

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
import re
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


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SECTION_RE = re.compile(r"\n+## (?:Impegni dal registro|Commitments from the registry) \(\d\d:\d\d\)\n.*\Z", re.S)


def commitments_section():
    """P5 (22/10/2026): la parte «impegni» del passaggio di consegne, dal registro e non a mano — il 21/10 alle 17:20 un ok
    del mattino era diventato «proposta non accolta» nel riassunto scritto dal modello. None senza registro (o con un
    errore: il file resta com'e'), '' con il registro senza impegni (la sezione vecchia cade)."""
    try:
        mod = _load("cm-tasks")
        con = mod.connect(create=False)
        if con is None:
            return None
        try:
            items = mod.commitments(con, git=False)
        finally:
            con.close()
        if not items:
            return ""
        title = cm.msg(CFG, "tasks.handoff_section")
        return f"\n## {title} ({time.strftime('%H:%M')})\n\n{mod.render_commitments(items)}\n"
    except Exception:   # noqa: BLE001 — un'informazione in piu': mai far fallire l'handoff
        return None


def with_section(text):
    """Il testo con la sezione impegni in fondo, una sola (quella di prima sostituita); senza registro com'era."""
    section = commitments_section()
    if section is None:
        return text
    return SECTION_RE.sub("", text).rstrip("\n") + "\n" + section


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
    text = with_section(p.read_text(encoding="utf-8", errors="replace"))
    p.write_text(text, encoding="utf-8")
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


def snapshot(cwd, name):
    """restart arm dalla master: la sezione impegni nel passaggio di consegne recente di questa cartella (puntatore
    entro ANNOUNCE_H), altrimenti un file solo di impegni, annunciato dal SessionStart come un handoff."""
    section = commitments_section()
    if not section:
        return 0
    ptr = pointer(cwd)
    try:
        d = json.loads(ptr.read_text())
        recent = time.time() - int(d.get("at") or 0) <= ANNOUNCE_H * 3600 and Path(d.get("path") or "").is_file()
    except (OSError, ValueError):
        d, recent = {}, False
    if recent:
        p = Path(d["path"])
        p.write_text(with_section(p.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
        print(str(p))
        return 0
    now = datetime.datetime.now()
    p = STATE / "handoffs" / f"impegni-{name}-{now.strftime('%Y-%m-%d-%H%M')}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# {cm.msg(CFG, 'tasks.handoff_section')} — {name}, {now.strftime('%d/%m %H:%M')}\n{section}", encoding="utf-8")
    ptr.write_text(json.dumps({"path": str(p), "at": int(time.time()), "cwd": os.path.realpath(cwd)}))
    print(str(p))
    return 0


def main(argv):
    cwd = os.getcwd()
    if argv[:1] == ["--snapshot"] and len(argv) > 2:
        return snapshot(argv[1], argv[2])
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
