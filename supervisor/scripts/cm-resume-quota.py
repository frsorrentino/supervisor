#!/usr/bin/env python3
"""L'avviso di Claude Code sul costo della ripresa (2.1.304) in una sessione che nessuno guarda.

  cm-resume-quota.py check <nome> [--window] [--wait S] [--resume-id ID] [--folder F] [--account A] [--host H]

Da Claude Code 2.1.304, sui piani Pro e Max, riprendere una conversazione rimasta ferma, con la cache fredda, puo'
mostrare un dialogo prima di tutto il resto (testo letto nel binario 2.1.305 il 21/10/2026):

    Resume this conversation?
    This conversation has been inactive for 3h 12m and is 412k tokens long. Resuming it will use about 38% of
    your 5-hour usage limit.            (oppure «will use all of your 5-hour usage limit», da 100% in su)
    ❯ 1. Resume
      2. Start a new conversation
         Claude can look this one up if you refer to it later

Compare alla ripresa (`--resume`, `--continue`: opzioni numerate) e su una sessione ferma quando arriva un prompt
(trigger «idle»: opzioni senza numero, il messaggio non inviato resta nella casella). Il cursore parte su «Resume»
o su «Start a new conversation» secondo un flag lato server: non si da' per scontato, si legge.

Una sessione lanciata senza finestra, o su un altro host, resterebbe ferma sul dialogo finche' qualcuno non lo
apre. `check` guarda lo schermo per `resume_quota.watch_s` secondi dopo un lancio con ripresa e:
  - con una finestra aperta non tocca niente: decide chi la guarda;
  - senza finestra, con `resume_quota.windowless` = "new" (default): sceglie «Start a new conversation» e manda
    alla sessione nuova un rimando alla conversazione precedente (id, cartella, trascrizione), come la regola della
    master «contesto grande = handoff + sessione pulita»;
  - se la scelta non riesce, o con "needs_you", o sul trigger «idle»: `needs-you` con il motivo, al telefono;
  - "off": nessuna azione.
Stampa una riga per il chiamante («nota: …») ed esce sempre 0: il lancio e' gia' riuscito.
"""
import importlib.util
import json
import os
import re
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

TITLE = "Resume this conversation?"
LIMIT = re.compile(r"of your 5-hour usage\s+limit")
PCT = re.compile(r"use about\s+(\d+)%")
ALL = re.compile(r"use all\s+of your 5-hour")
NEW = "Start a new conversation"
CURSOR = re.compile(r"^\s*(❯|>)\s*(?:\d+\.\s+)?(\S.*?)\s*$")
NUMBERED = re.compile(r"^\s*(❯|>)?\s*\d+\.\s+Resume\s*$", re.M)


def parse(screen):
    """{"trigger": "resume"|"idle", "pct": int|None, "cursor": "resume"|"new"|None, "lookup": bool} o None."""
    if not screen or TITLE not in screen or not LIMIT.search(" ".join(screen.split())) or NEW not in screen:
        return None
    flat = " ".join(screen.split())
    m = PCT.search(flat)
    pct = 100 if ALL.search(flat) else int(m.group(1)) if m else None
    cursor = None
    for line in screen.splitlines():
        c = CURSOR.match(line)
        if c:
            cursor = "new" if c.group(2).startswith(NEW) else "resume" if c.group(2) == "Resume" else cursor
    return {"trigger": "resume" if NUMBERED.search(screen) else "idle", "pct": pct, "cursor": cursor,
            "lookup": "look this one up" in flat}


def settings():
    d = {"windowless": "new", "watch_s": 10}
    d.update(CFG.get("resume_quota") or {})
    return d


def pointer(sid, folder, account, info):
    tr = ""
    if sid and folder:
        acc = CFG["accounts"].get(account or CFG.get("default_account") or "") or {}
        slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(folder))
        tr = str(Path(cm.expand(acc.get("config_dir", "~/.claude"))) / "projects" / slug / f"{sid}.jsonl")
    return M("resume_quota.pointer", sid=sid or "?", folder=folder or "?", pct=info.get("pct") if info.get("pct") is not None else "?",
             transcript=tr or "?")


def choose_new(term, info, tries=4):
    """Porta il cursore su «Start a new conversation» e conferma. True se il dialogo e' sparito."""
    scr = term.screen()
    for _ in range(tries):
        cur = parse(scr)
        if not cur:
            return False
        if cur["cursor"] == "new":
            break
        scr = term.keys(["Down"])
    else:
        return False
    if (parse(scr) or {}).get("cursor") != "new":
        return False
    term.keys(["Enter"])
    end = time.time() + 10
    while time.time() < end:
        if not parse(term.screen()):
            return True
        time.sleep(0.5)
    return False


def needs_you(name, reason):
    """Il file di `needs-you` per la sessione NAME (vista da fuori: non c'e' TMUX_PANE), con la stessa forma."""
    ny = _load("cm-needsyou")
    sessions = _load("cm-sessions")
    row = next((r for r in sessions.collect(read_screen=False) if (r.get("tmux") or r.get("name")) == name and r.get("session_id")), None)
    if not row:
        return False
    return ny.mark(row, reason) == 0


def check(name, window, wait_s, sid, folder, account="", term=None):
    """Guarda lo schermo fino a wait_s secondi. Torna (codice, dettaglio): none, left, new, needs_you, off."""
    s = settings()
    if term is None:
        term = _load("cm-answer").term_for(name)
    if term is None:
        return "none", ""
    end = time.time() + wait_s
    info = None
    while True:
        info = parse(term.screen())
        if info or time.time() >= end:
            break
        time.sleep(1)
    if not info:
        return "none", ""
    pct = info["pct"] if info["pct"] is not None else "?"
    if window:
        return "left", M("resume_quota.left", name=name, pct=pct)
    mode = s.get("windowless") or "new"
    if mode == "off":
        return "off", M("resume_quota.off", name=name, pct=pct)
    if mode == "new" and info["trigger"] == "resume" and choose_new(term, info):
        time.sleep(1)
        term.keys(["text:" + pointer(sid, folder, account, info), "Enter"])
        return "new", M("resume_quota.new", name=name, pct=pct)
    reason = M("resume_quota.reason", pct=pct)
    return ("needs_you" if needs_you(name, reason) else "stuck"), M("resume_quota.asked", name=name, pct=pct)


def main(argv):
    if not argv or argv[0] != "check" or len(argv) < 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    name, rest = argv[1], argv[2:]

    def opt(k, default=""):
        return rest[rest.index(k) + 1] if k in rest and rest.index(k) + 1 < len(rest) else default
    wait_s = float(opt("--wait", settings()["watch_s"]))
    term = None
    if opt("--host"):
        term = _load("cm-answer").Remote(opt("--host"), name)
    code, line = check(name, "--window" in rest, wait_s, opt("--resume-id"), opt("--folder"), opt("--account"), term=term)
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
