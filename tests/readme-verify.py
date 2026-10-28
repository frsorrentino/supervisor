#!/usr/bin/env python3
"""Verifica README.md e details/commands.md: ogni sottocomando del dispatcher è documentato come
`supervisor <sub>` nel riferimento dei comandi, il badge di versione è quello dinamico della release
di GitHub, le sezioni obbligatorie ci sono, nessun nome di fornitore (R1–R5).

28/10/2026 (README a scoperta progressiva): il riferimento completo dei comandi sta in
details/commands.md e la configurazione in details/configuration.md; il README resta sotto le 320 righe
e il badge non porta più la versione scritta a mano, che invecchiava a ogni release."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

root = Path(__file__).resolve().parent.parent
readme = (root / "README.md").read_text()
commands = (root / "details" / "commands.md").read_text()
dispatcher = (root / "supervisor" / "scripts" / "supervisor").read_text()
# righe del case: `  launch|lancia)  exec ...` → il primo nome è quello inglese
subs = sorted({m.group(1) for m in re.finditer(r"^  ([a-z]+)(?:\|[a-z|-]+)?\)\s", dispatcher, re.M)})
subs = [s for s in subs if s not in ("help", "version", "root")]
missing = [s for s in subs if not re.search(rf"`supervisor {s}\b", commands)]
T.check("R1 every subcommand appears as `supervisor <sub>` in details/commands.md", not missing, ", ".join(missing) or f"{len(subs)} subcommands")
line3 = readme.splitlines()[2]
T.check("R2 the version badge on line 3 is the dynamic one of the GitHub release (no version typed by hand)",
        "img.shields.io/github/v/release/frsorrentino/supervisor" in line3 and not re.search(r"version-\d+\.\d+\.\d+-", line3), line3)
for sec in ("## The problem", "## Try it in 60 seconds", "## What you get", "## Every day", "## On your phone and watch",
            "## What you are trusting", "## Install", "## How it works", "## Tests", "## License"):
    T.check(f"R3 section {sec}", sec in readme, "")
for f in ("commands.md", "configuration.md"):
    T.check(f"R3 details/{f} exists and the README links it", (root / "details" / f).is_file() and f"](details/{f})" in readme, "")
T.check("R5 the README stays under 320 lines", len(readme.splitlines()) < 320, str(len(readme.splitlines())))
T.check("R4 no vendor name in the README", ("pixel" + "farm") not in readme.lower(), "")   # a pezzi: il nome non sta nel ramo pubblico (S10)
T.finish()
