#!/usr/bin/env python3
"""Controllo privacy prima di pubblicare: nessun nome della blocklist (docs/privacy-blocklist.txt, mai
pubblicata) nell'albero che finisce sul ramo pubblico — codice, test, README, CHANGELOG, SVG e testi alt
delle immagini. Esce 1 con le righe incriminate. Uso: python3 tools-privacy-check.py [--quiet] [FILE ...] — con dei file controlla solo quelli."""
import pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent
words = [l.strip().lower() for l in (ROOT / "docs" / "privacy-blocklist.txt").read_text().splitlines()
         if l.strip() and not l.startswith("#")]
paths = [a for a in sys.argv[1:] if not a.startswith("--")]
if paths:   # S10 (14/09/2026): i file indicati (una prova, un file nuovo non ancora tracciato) al posto dell'albero
    files = [str(pathlib.Path(a).resolve()) for a in paths]
else:
    files = subprocess.run(["git", "ls-files", "--", ".", ":!docs", ":!BRIEF.md"], capture_output=True, text=True, cwd=ROOT).stdout.split()
    files += [str(p.relative_to(ROOT)) for p in (ROOT / "assets").rglob("*.svg") if str(p.relative_to(ROOT)) not in files]
hits = []
for f in files:
    p = ROOT / f
    if p.suffix in (".png", ".jpg", ".gif", ".webp", ".zip", ".pyc") or not p.is_file():
        continue
    try:
        text = p.read_text(errors="ignore")
    except OSError:
        continue
    for i, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for w in words:
            if w in low:
                hits.append(f"{f}:{i}: «{w}»  {line.strip()[:90]}")
if hits:
    print("PRIVACY: nomi della blocklist nell'albero pubblico:")
    print("\n".join(hits[:60]))
    sys.exit(1)
if "--quiet" not in sys.argv:
    print(f"privacy ok: {len(files)} file, {len(words)} nomi cercati, nessuno trovato")
