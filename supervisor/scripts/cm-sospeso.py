#!/usr/bin/env python3
"""Il turno sospeso (20/11/2026, supervisor-turno-sospeso-19-11, approvato dal maintainer il 19/11 alle 22:23).

Caso vero: win:supervisor ha chiuso il turno delle 20:16 del 19/11 con «puoi mandare `/clear` quando vuoi. Se non lo fai,
comincio `riassunto-fine` da qui» ed e' rimasta ferma due ore: nessuno doveva rispondere, ma la sessione aspettava un via.
Se n'e' accorto solo l'avviso degli impegni fermi da 2 ore.

A fine turno (hook Stop, nella parte staccata; per le sessioni su un altro host, il giro di `hosts poll` sulla copia della
trascrizione) il messaggio finale si confronta con le frasi di chi aspetta un via che non serve: «se no / se non lo fai /
altrimenti comincio», «se non rispondi parto», «puoi mandare /clear», «manda /clear quando vuoi», «vuoi che continui /
proceda?», «procedo?», «dimmi se continuo», «aspetto il tuo via». Se la sessione ha compiti in coda nel registro, la master
riceve una riga in casella (supervisor talk master --no-wait): la sessione, la frase e il compito. Solo riconoscere e
avvisare: farla proseguire da sola e' un'altra scelta.

Niente avviso per le attese vere: una carta wait-ok aperta della sessione (awaiting_ok), una richiesta needs-you aperta,
una scelta fra opzioni nel messaggio (righe «1)», «A)»...), una riga «Tipo: aspetta»; ne' per una sessione senza compiti in
coda, ne' per la master stessa. Una volta sola per messaggio (<state_dir>/sospeso-sent.json).
Prove: CM_SOSPESO_TALK_BIN (il comando talk finto).
"""
import hashlib
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
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_so", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
STATE = Path(cm.expand(CFG["state_dir"]))
SENT_KEEP = 300
TAIL_CHARS = 700   # solo la coda del messaggio: la frase che chiude il turno, non una citazione a meta'

GO = r"(?:comincio|inizio|parto|procedo|continuo|vado\s+avanti)"
PHRASES = [re.compile(p, re.I) for p in (
    rf"\bse\s+no\b\s*,?\s*{GO}",
    rf"\bse\s+non\s+lo\s+fai\s*,?\s*{GO}",
    rf"\baltrimenti\s*,?\s*{GO}",
    rf"\bse\s+non\s+(?:rispondi|mi\s+dici\s+niente|ricevo\s+niente)\s*,?\s*{GO}",
    r"\bpuoi\s+mandare\s+`?/clear`?",
    r"\bmanda(?:re)?\s+`?/clear`?\s+quando\s+vuoi",
    r"\bvuoi\s+che\s+(?:continui|proceda|vada\s+avanti|cominci|inizi|parta)\b[^?\n]{0,60}\?",
    r"(?:^|[\s.;:])procedo\s*\?",
    r"\bdimmi\s+se\s+(?:continuo|procedo|vado\s+avanti|comincio)",
    r"\baspetto\s+il\s+tuo\s+(?:via|ok\s+per\s+(?:continuare|procedere))",
    r"\bif\s+not\s*,?\s*i(?:'ll|\s+will)\s+(?:start|continue|proceed)",
    r"\byou\s+can\s+send\s+`?/clear`?",
    r"\b(?:shall|should)\s+i\s+(?:continue|proceed|go\s+on)\s*\?",
)]
CHOICE_RE = re.compile(r"(?mi)^\s*(?:[-*]\s*)?(?:[1-9][.)]|[A-D][.):]|opzione\s+[A-D1-9]|option\s+[A-D1-9])\s+\S")
WAITS_RE = re.compile(r"(?im)^\s*[*_]*(?:tipo|type)[*_]*\s*:\s*[*_]*(?:aspetta|waiting)\b")
QUEUED = ("approved", "assigned", "running")


def phrase_of(text):
    """La frase che chiude il turno aspettando un via, o None."""
    tail = str(text or "")[-TAIL_CHARS:]
    for rx in PHRASES:
        m = rx.search(tail)
        if m:
            s = max(tail.rfind(".", 0, m.start()), tail.rfind("\n", 0, m.start()), tail.rfind("!", 0, m.start())) + 1
            e = min([i for i in (tail.find(".", m.end()), tail.find("\n", m.end())) if i >= 0] or [len(tail)])
            return " ".join(tail[s:e + 1].replace("`", "").split()).strip()[:200] or m.group(0)
    return None


def a_real_wait(text):
    """Una scelta fra opzioni o un «Tipo: aspetta»: un'attesa vera, la persona deve rispondere."""
    t = str(text or "")
    return bool(WAITS_RE.search(t)) or len(CHOICE_RE.findall(t)) >= 2



def tasks_of(names):
    """(in coda, carte wait-ok aperte) della sessione nel registro: due liste di (id, short)."""
    try:
        tk = _load("cm-tasks")
        con = tk.connect(create=False)
    except Exception:   # noqa: BLE001
        return [], []
    if con is None:
        return [], []
    want = {str(n) for n in names if n} | {str(n).replace(":", "-") for n in names if n}
    q, w = [], []
    try:
        for r in con.execute("SELECT id, title, session, state, contract FROM tasks WHERE state IN ('approved','assigned','running','awaiting_ok')"):
            sess = str(r[2] or "")
            try:
                c = json.loads(r[4] or "{}").get("task") or {}
            except ValueError:
                c = {}
            sess = sess or str((c.get("where") or {}).get("session") or "")
            if not ({sess, sess.replace(":", "-")} & want):
                continue
            item = (r[0], c.get("short") or r[1] or r[0])
            (w if r[3] == "awaiting_ok" else q).append(item)
    finally:
        con.close()
    return q, w


def needs_you_open(sid):
    return bool(sid) and (STATE / "needs-you" / f"{sid}.json").is_file()


def _sent_path():
    return STATE / "sospeso-sent.json"


def _talk(master, text):
    fake = os.environ.get("CM_SOSPESO_TALK_BIN")
    cmd = [fake] if fake else [os.environ.get("CM_BIN") or str(HERE / "supervisor"), "talk"]
    try:
        return subprocess.run(cmd + [master, text, "--no-wait"], capture_output=True, text=True, timeout=60).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def check(name, text, sid=None, key=None, also=()):
    """Il turno di `name` finito con `text`: avvisa la master se aspetta un via che non serve. Torna la frase se ha avvisato,
    altrimenti il motivo per cui no («none», «master», «wait», «needs_you», «awaiting_ok», «no_queue», «sent»)."""
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    if not name or name == master:
        return "master"
    ph = phrase_of(text)
    if not ph:
        return "none"
    if a_real_wait(text):
        return "wait"
    if needs_you_open(sid):
        return "needs_you"
    queued, waiting = tasks_of([name, *also])
    if waiting:
        return "awaiting_ok"
    if not queued:
        return "no_queue"
    k = key or hashlib.sha1(f"{name}\n{text}".encode()).hexdigest()[:16]
    p = _sent_path()
    try:
        sent = json.loads(p.read_text())
    except (OSError, ValueError):
        sent = []
    if k in sent:
        return "sent"
    tid, short = queued[0]
    more = len(queued) - 1
    msg = M("sospeso.notice", name=name, phrase=ph, task=tid, short=short, more=(M("sospeso.more", n=more) if more else ""))
    if _talk(master, msg) not in (0, 5):   # 5 = la master e' chiusa: il messaggio resta nella sua casella
        return "talk_failed"
    sent = (sent + [k])[-SENT_KEEP:]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sent))
    try:
        with open(STATE / "ledger.jsonl", "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "turno-sospeso", "session": name, "phrase": ph,
                                "task": tid}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return ph


def last_text(path, off=0):
    """(testo, uuid) dell'ultimo messaggio di testo dell'assistente da `off` in poi (le sidechain no)."""
    text, uid = None, None
    try:
        with open(path, "rb") as f:
            f.seek(off)
            for ln in f:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if d.get("type") != "assistant" or d.get("isSidechain"):
                    continue
                for b in (d.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "text" and str(b.get("text") or "").strip():
                        text, uid = b["text"], d.get("uuid")
    except OSError:
        pass
    return text, uid


if __name__ == "__main__":   # supervisor-turno-sospeso: prova a mano, `cm-sospeso.py NOME < testo`
    print(check(sys.argv[1] if len(sys.argv) > 1 else "", sys.stdin.read()))
