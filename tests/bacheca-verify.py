#!/usr/bin/env python3
"""La categoria dell'app con Decidi, Verifica, Seguiti, Suggerimenti e Monitoraggi si chiama «Bacheca», in inglese
«Board» (27/11/2026, supervisor-bacheca-27-11: il manutentore l'ha rinominata alle 16:53; nell'app la chiave resta
`you`). Alle 17:09: «bacheca e per te devono avere la loro traduzione», quindi nei testi inglesi «Board» e «For you».
Qui cambiano solo i testi: l'aiuto di `monitor`, le note nel codice, la documentazione, le etichette dei test.
BA1 l'aiuto di `monitor` (cm-monitor.py senza comando, in italiano) dice «Bacheca» e non usa piu' il nome vecchio
BA2 nel plugin (supervisor/), in details/ e nel README il nome vecchio non c'e' piu'; resta solo il prefisso con cui
    cominciano i messaggi della master, che cm-night-report.py toglie dal titolo di un obiettivo: non e' la categoria
BA3 la documentazione inglese (details/) dice «Board» e «For you», mai i nomi italiani «Bacheca» e «Per te»
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

OLD = "Dalla master"
PREFIX_RE = 're.sub(r"^' + OLD   # cm-night-report.assigned_goals: il prefisso dei messaggi della master

tmp = Path(T.tmpdir())
env = dict(os.environ, HOME=str(tmp), CM_HOME=str(tmp), CC_SUPERVISOR_CONFIG=str(tmp / "c.json"))
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-monitor.py")], capture_output=True, text=True, env=env, timeout=60)
T.check("BA1 the help of `monitor` (Italian) says «Bacheca» and no longer uses the category's old name",
        r.returncode == 2 and "«Bacheca»" in r.stdout and OLD not in r.stdout, f"rc={r.returncode} {r.stdout[:300]} {r.stderr[:300]}")

files = [p for p in [T.ROOT / "README.md"] if p.is_file()]
files += sorted(p for d in ("supervisor", "details") for p in (T.ROOT / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
left = []
for p in files:
    try:
        text = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    left += [f"{p.relative_to(T.ROOT)}:{n}" for n, ln in enumerate(text.splitlines(), 1) if OLD in ln and PREFIX_RE not in ln]
nr = (T.SCRIPTS / "cm-night-report.py").read_text(encoding="utf-8")
T.check("BA2 supervisor/, details/ and the README no longer use the old name; only the master's message prefix that the night report takes off a goal's title stays",
        len(files) > 50 and not left and nr.count(PREFIX_RE) == 1, f"{len(files)} files; " + " ".join(left[:20]))

docs = {p.name: p.read_text(encoding="utf-8") for p in sorted((T.ROOT / "details").glob("*.md"))}
italian = [f"{n} {w}" for n, t in docs.items() for w in ("«Bacheca»", "«Per te»") if w in t]
T.check("BA3 the English documentation (details/) says «Board» and «For you», never the Italian names «Bacheca» and «Per te»",
        not italian and "«Board»" in docs.get("relay.md", "") and "«Board»" in docs.get("commands.md", "")
        and "«For you»" in docs.get("relay.md", "") and "«For you»" in docs.get("tasks-and-plans.md", ""), " ".join(italian))
T.rm(tmp)
T.finish()
