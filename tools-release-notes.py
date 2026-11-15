#!/usr/bin/env python3
"""Le note di una release dal CHANGELOG (author tooling, non va nello zip).

  python3 tools-release-notes.py VER             le note, su stdout
  python3 tools-release-notes.py VER --ids       gli id dei compiti nominati nelle note, uno per riga
  python3 tools-release-notes.py VER --summary   una riga: quante voci, da quale a quale, quanti id

Le note sono la voce VER del CHANGELOG e, sotto di lei, ogni voce che non e' mai uscita: si scende finche' non si
trova una voce la cui versione ha gia' un tag `v<versione>`, una riga vuota o un titolo. 15/11/2026: cinque versioni
(0.8.164-0.8.168) erano pronte in main senza poter uscire, e release.sh rilascia solo la versione di HEAD con la sola
sua voce: gli id dei compiti delle altre non sarebbero entrati in nessuna release. In un repo senza nessun tag `v*`
(o fuori da git) vale la sola voce VER, come prima.
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENTRY = re.compile(r"^- \*\*(\d+\.\d+\.\d+)\b")
# l'id di un compito: parole minuscole unite da trattini, in coda giorno e mese (supervisor-recap-appeso-15-11)
TASK_ID = re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+-\d{1,2}-\d{1,2}\b")


def tags():
    """Le versioni che hanno un tag v<versione>; vuoto fuori da git o senza tag."""
    try:
        out = subprocess.run(["git", "tag", "-l", "v*"], cwd=str(HERE), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {t[1:] for t in out.stdout.split()} if out.returncode == 0 else set()


def entries(ver, text, released):
    """[(versione, [righe])] da VER in giu', finche' le voci non sono uscite."""
    out, cur, on = [], None, False
    for line in text.splitlines():
        m = ENTRY.match(line)
        if m:
            if not on and m.group(1) != ver:
                continue
            if on and (not released or m.group(1) in released):
                break
            on = True
            cur = (m.group(1), [line])
            out.append(cur)
        elif on:
            if not line.strip() or line.startswith("#"):
                break
            cur[1].append(line)
    return out


def main(argv):
    if not argv or argv[0].startswith("-"):
        print("usage: tools-release-notes.py VER [--ids | --summary]", file=sys.stderr)
        return 2
    ver = argv[0]
    got = entries(ver, (HERE / "CHANGELOG.md").read_text(encoding="utf-8"), tags())
    if not got:
        print(f"FAIL: could not extract {ver} notes from CHANGELOG", file=sys.stderr)
        return 1
    body = "\n".join(line for _, lines in got for line in lines)
    ids = sorted(set(TASK_ID.findall(body)))
    if "--ids" in argv:
        print("\n".join(ids))
    elif "--summary" in argv:
        span = got[0][0] if len(got) == 1 else f"{got[0][0]} down to {got[-1][0]}"
        print(f"release notes: {len(got)} CHANGELOG {'entry' if len(got) == 1 else 'entries'} ({span}), {len(ids)} task ids")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
