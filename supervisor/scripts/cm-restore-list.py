#!/usr/bin/env python3
"""Le sessioni da ripristinare, per cm-restore.sh: una riga per sessione, nome, cartella, account, fonte e session_id
separati da tab. Argomenti: registro, fotografia, testo della fonte «registro», testo della fonte «fotografia», finestra
in minuti, file dell'uptime. Era un heredoc dentro <( ... ) di cm-restore.sh: bash 3.2 (macOS) non lo legge (11/11/2026,
supervisor-macos-11-11)."""
import json, sys, time
from datetime import datetime
def load(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return {}
reg, good = load(sys.argv[1]), load(sys.argv[2])
seen = set()
for s in reg.get("sessioni", []):
    seen.add(s["nome"])
    print(f'{s["nome"]}\t{s["cartella"]}\t{s.get("account", "")}\t' + sys.argv[3].format(t=str(reg.get("salvato", ""))[:16].replace("T", " ")) + f'\t{s.get("session_id") or ""}')
def ts(v):
    try:
        return datetime.strptime(str(v), "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        return None
try:
    boot = time.time() - float(open(sys.argv[6]).read().split()[0])
except (OSError, ValueError, IndexError):
    import re, subprocess   # macOS: niente /proc/uptime, l'avvio da sysctl
    try:
        m = re.search(r"sec\s*=\s*(\d+)", subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5).stdout)
        boot = float(m.group(1)) if m else time.time()
    except (OSError, subprocess.SubprocessError):
        boot = time.time()
prima = [t for t in (ts(s.get("visto")) for s in good.get("sessioni", [])) if t is not None and t < boot]
ultimo = max(prima) if prima else None
for s in good.get("sessioni", []):
    if s["nome"] in seen:
        continue
    t = ts(s.get("visto"))
    if t is None or ultimo is None or t >= boot or t < ultimo - float(sys.argv[5]) * 60:
        continue
    print(f'{s["nome"]}\t{s["cartella"]}\t{s.get("account", "")}\t' + sys.argv[4].format(t=str(s.get("visto", ""))[:16].replace("T", " ")) + f'\t{s.get("session_id") or ""}')
