#!/usr/bin/env python3
"""Riduce i file veri dei sottoagenti di Claude Code al fixture di tests/sottoagenti-verify.py (23/11/2026).

  python3 riduci.py SORGENTE DEST

SORGENTE = una cartella subagents/ vera (agent-*.jsonl e agent-*.meta.json, eventuali workflows/wf_*/). Ogni riga
della trascrizione resta una riga, con le chiavi che supervisor legge: type, timestamp, agentId, uuid e, per le
voci assistant, message.id, message.model e message.usage. Il resto (prompt, risposte, allegati, percorsi) cade;
nel meta.json la descrizione diventa «fixture <n>». Le righe sono quelle vere: ore, numero di blocchi per messaggio,
usage ripetuta su ogni blocco.
"""
import json
import sys
from pathlib import Path

KEEP = ("type", "timestamp", "agentId", "uuid", "isSidechain")


def reduce_line(line):
    d = json.loads(line)
    out = {k: d[k] for k in KEEP if k in d}
    m = d.get("message")
    if d.get("type") == "assistant" and isinstance(m, dict):
        out["message"] = {k: m[k] for k in ("id", "model", "usage") if k in m}
    return json.dumps(out, separators=(",", ":"))


def main(src, dst):
    src, dst = Path(src), Path(dst)
    n = 0
    for mp in sorted(src.rglob("agent-*.meta.json")):
        rel = mp.relative_to(src)
        n += 1
        meta = json.loads(mp.read_text(encoding="utf-8"))
        meta["description"] = f"fixture {n}"
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        (dst / rel).write_text(json.dumps(meta), encoding="utf-8")
        jp = mp.with_name(mp.name[:-len(".meta.json")] + ".jsonl")
        if jp.exists():
            lines = [reduce_line(x) for x in jp.read_text(encoding="utf-8").splitlines() if x.strip()]
            (dst / rel.with_name(jp.name)).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{n} agenti in {dst}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
