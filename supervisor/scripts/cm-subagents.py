#!/usr/bin/env python3
"""I sottoagenti di una sessione di Claude Code (23/11/2026, supervisor-sottoagenti-23-11).

Claude Code scrive ogni sottoagente accanto alla trascrizione della sessione, in <projects>/<slug>/<session id>/:
  subagents/agent-<id>.meta.json              Agent/Task (anche in background) e i fork: agentType, description,
                                              toolUseId, spawnDepth, requestShape, model, finished
  subagents/agent-<id>.jsonl                  la sua trascrizione (le voci assistant hanno message.id, model, usage)
  subagents/workflows/wf_<id>/agent-<id>.*    gli agenti di un Workflow: il meta.json non ha toolUseId ne' finished,
                                              ha workflowPhase; journal.jsonl dice started/result per agentId

Letto su file veri: 2.1.329 su win (primo piano, background, un fork di ottobre) e 2.1.322 sul Chromebook (un Workflow
di 43 agenti, ~470 KB di trascrizione l'uno). Per questo i meta.json si leggono tutti (piccoli: contano), le
trascrizioni solo per le voci mostrate: inizio dalla prima riga, fine dall'ultima, i token leggendola intera, con
una cache per percorso, dimensione e mtime.

Una voce: {id, type, desc, model, bg, depth, started, ended, finished, tokens: {in, out}} piu' wf e phase per gli
agenti di un Workflow. `finished` e' quello che Claude Code scrive nel meta.json (true quando l'agente ha chiuso il
suo turno) o, per un Workflow, una riga result nel journal; `ended` e' l'ora dell'ultima riga della trascrizione di
un agente finito, null mentre lavora. Una sessione che non gira piu' non ha sottoagenti in corso.

  cm-subagents.py DIR [--items N]     il riassunto della cartella della sessione, in JSON (DIR = la trascrizione
                                      senza .jsonl)
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

DESC_MAX = 80
STATE_ITEMS = 3      # le voci nello stato del relay (8 KB): le altre con l'op subagents
OP_ITEMS = 30
HEAD_BYTES = 256 * 1024

_cache = {}   # percorso → (size, mtime, valore) per prima/ultima riga e token


def epoch(ts):
    if not ts:
        return None
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _read_json(p):
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _cached(p, kind, fn):
    try:
        st = os.stat(p)
    except OSError:
        return None
    key = (str(p), kind)
    hit = _cache.get(key)
    if hit and hit[0] == st.st_size and hit[1] == st.st_mtime:
        return hit[2]
    val = fn(p, st.st_size)
    _cache[key] = (st.st_size, st.st_mtime, val)
    return val


def _ts_of(line):
    try:
        return epoch(json.loads(line).get("timestamp"))
    except (ValueError, AttributeError):
        return None


def _span(p, size):
    """(prima, ultima) ora delle righe della trascrizione, leggendo solo l'inizio e la fine."""
    first = last = None
    with open(p, "rb") as f:
        for raw in f.read(min(size, HEAD_BYTES)).split(b"\n"):
            first = _ts_of(raw.decode("utf-8", "replace"))
            if first:
                break
        back = min(size, HEAD_BYTES)
        f.seek(size - back)
        for raw in reversed(f.read(back).split(b"\n")):
            last = _ts_of(raw.decode("utf-8", "replace"))
            if last:
                break
    return first, last


def _usage(p, size):
    """{in, out, model}: l'ultima usage di ogni message.id (Claude Code la ripete su ogni blocco del messaggio);
    in = input + cache scritta + cache letta."""
    by_id, model = {}, None
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"assistant"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message") if d.get("type") == "assistant" else None
            if not isinstance(m, dict):
                continue
            if m.get("model") and m["model"] != "<synthetic>":
                model = m["model"]
            if isinstance(m.get("usage"), dict):
                by_id[m.get("id") or d.get("uuid")] = m["usage"]
    tin = sum(int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) for u in by_id.values())
    tout = sum(int(u.get("output_tokens") or 0) for u in by_id.values())
    return {"in": tin, "out": tout, "model": model}


def _journal(wdir):
    """{agentId: True se ha una riga result, False se solo started} dal journal di un Workflow."""
    out = {}
    try:
        with open(wdir / "journal.jsonl", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                aid = d.get("agentId")
                if aid and d.get("type") in ("started", "result"):
                    out[aid] = out.get(aid, False) or d["type"] == "result"
    except OSError:
        pass
    return out


def agents(sdir):
    """Tutti i sottoagenti della cartella della sessione: [(meta_path, jsonl_path, meta, wf, done)], senza aprire
    le trascrizioni."""
    sub = Path(sdir) / "subagents"
    found = []
    for mp in sorted(sub.glob("agent-*.meta.json")):
        meta = _read_json(mp) or {}
        found.append((mp, mp.with_name(mp.name[:-len(".meta.json")] + ".jsonl"), meta, None, meta.get("finished") is True))
    for wdir in sorted(sub.glob("workflows/wf_*")):
        jr = _journal(wdir)
        for mp in sorted(wdir.glob("agent-*.meta.json")):
            meta = _read_json(mp) or {}
            aid = mp.name[len("agent-"):-len(".meta.json")]
            found.append((mp, mp.with_name(f"agent-{aid}.jsonl"), meta, wdir.name, bool(jr.get(aid))))
    return found


def _start(mp, jp):
    """L'ora della prima riga della trascrizione; senza trascrizione l'mtime del meta.json."""
    span = _cached(jp, "span", _span) or (None, None)
    if span[0]:
        return span[0]
    try:
        return int(os.path.getmtime(mp))
    except OSError:
        return None


def item(mp, jp, meta, wf, done, alive=True, tokens=True):
    span = _cached(jp, "span", _span) or (None, None)
    started = _start(mp, jp)
    finished = bool(done) or not alive
    it = {"id": mp.name[len("agent-"):-len(".meta.json")],
          "type": str(meta.get("agentType") or "?"),
          "desc": str(meta.get("description") or "")[:DESC_MAX],
          "model": str(meta.get("model") or "") or None,
          "bg": meta.get("requestShape") == "background",
          "depth": int(meta.get("spawnDepth") or 1),
          "started": started,
          "ended": span[1] if finished else None,
          "finished": finished,
          "tokens": None}
    if tokens and jp.exists():
        u = _cached(jp, "usage", _usage) or {}
        it["tokens"] = {"in": u.get("in", 0), "out": u.get("out", 0)}
        if u.get("model"):
            it["model"] = u["model"]   # il meta.json dice l'alias chiesto («haiku», «inherit»), la trascrizione il modello vero
    if wf:
        it["wf"] = wf
        if meta.get("workflowPhase"):
            it["phase"] = str(meta["workflowPhase"])[:40]
    return it


def _order(rows, alive):
    """In corso prima, poi dal piu' recente."""
    return sorted(rows, key=lambda r: (not (alive and not r[4]), -(_start(r[0], r[1]) or 0)))


def summary(sdir, alive=True, n_items=STATE_ITEMS):
    """{n, running, at, items, workflows} o None senza sottoagenti. at = l'inizio del piu' recente."""
    rows = agents(sdir)
    if not rows:
        return None
    ordered = _order(rows, alive)
    items = [item(*r, alive=alive) for r in ordered[:n_items]]
    starts = [s for s in (_start(r[0], r[1]) for r in rows) if s]
    wfs = {}
    for r in rows:
        if r[3]:
            w = wfs.setdefault(r[3], {"id": r[3], "n": 0, "running": 0})
            w["n"] += 1
            w["running"] += int(alive and not r[4])
    out = {"n": len(rows), "running": sum(1 for r in rows if alive and not r[4]), "at": max(starts) if starts else None, "items": items}
    if wfs:
        out["workflows"] = list(wfs.values())
    return out


def counts(sdir, alive=True):
    """(lanciati, in corso) senza aprire le trascrizioni: per `supervisor sessions`."""
    rows = agents(sdir)
    return len(rows), sum(1 for r in rows if alive and not r[4])


def session_dir(transcript):
    """La cartella dei sottoagenti di una trascrizione <slug>/<sid>.jsonl: <slug>/<sid>."""
    t = str(transcript or "")
    return t[:-len(".jsonl")] if t.endswith(".jsonl") else ""


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[-2].strip())
        return 0 if argv else 2
    n = OP_ITEMS
    if "--items" in argv:
        n = int(argv[argv.index("--items") + 1])
    print(json.dumps(summary(argv[0], True, n), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
