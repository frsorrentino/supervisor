#!/usr/bin/env python3
"""Un testo lungo verso una sessione di win arriva (09/11/2026, supervisor-talk-win-testo-lungo-09-11).
Il 09/11 alle 09:10 due talk a win:supervisor-app (circa 3.900 caratteri) uscivano «unreachable»: lo script va a
PowerShell come -EncodedCommand (UTF-16LE in base64) con cm-session.js incorporato, e la riga superava i 32.767 caratteri
di CreateProcess; ssh usciva 255 senza stderr. Qui un ssh finto che fa come Windows: oltre il limite esce 255 muto;
sotto, decodifica il comando, prende lo script da stdin se il comando lo legge, e risponde come cm-session.js.

WL1 session_post con un testo di 10.000 caratteri (accenti, virgolette, a capo): arriva intero, la riga di comando
    resta sotto i 32.767 caratteri; un testo breve passa ancora sulla riga (niente stdin)
WL2 il limite che resta (qui: lo script piu' grande di quello che stdin accetta, simulato) non e' «unreachable»:
    l'errore dice che il comando e' troppo lungo per Windows
"""
import base64
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
home.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                           "default_account": "personale"}))
log = tmp / "ssh.jsonl"
fake = tmp / "fake-win-ssh"
fake.write_text(f"""#!{sys.executable}
import base64, json, re, sys
argv = sys.argv[1:]
if "-O" in argv:
    sys.exit(0)
cmd = argv[-1]
row = {{"len": len(cmd)}}
if len(cmd) > 32767:   # CreateProcess: la riga non parte, sshd chiude senza dire niente
    open({str(log)!r}, "a").write(json.dumps(dict(row, refused=True)) + "\\n")
    sys.exit(255)
m = re.search(r"-EncodedCommand (\\S+)", cmd)
script = base64.b64decode(m.group(1)).decode("utf-16-le") if m else cmd
if "[Console]::In.ReadToEnd()" in script:
    script = sys.stdin.read()
    row["stdin"] = len(script)
p = re.search(r"node \\$js 'post' '([^']+)' '([^']+)'", script)
row["text"] = base64.b64decode(p.group(2)).decode() if p else None
open({str(log)!r}, "a").write(json.dumps(row) + "\\n")
print(json.dumps({{"ok": True, "pid": 1, "sid": "s", "offset": 0, "status": "idle"}}))
""")
fake.chmod(0o755)
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "CM_HOME": str(home), "CM_SSH_BIN": str(fake)})
sp = importlib.util.spec_from_file_location("cm_adapters_wl", T.SCRIPTS / "cm-adapters.py")
AD = importlib.util.module_from_spec(sp)
sp.loader.exec_module(AD)
w = AD.WindowsNative("win", {"kind": "windows-native", "transport": {"ssh": "fakewin"}}, {"state_dir": str(tmp / "state")})


def rows():
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


# WL1
long_text = ("Compito app-monitoraggi: «perché» non è un problema, $HOME e `backtick`, 'apici' e \"virgolette\".\n" * 120)[:10000]
try:
    d1 = w.session_post("win-supervisor-app", long_text, "master", "uds:")
    e1 = None
except Exception as e:   # noqa: BLE001
    d1, e1 = None, e
r1 = rows()
T.check("WL1 session_post with 10.000 characters: delivered whole, the command line under 32.767, the script through stdin",
        e1 is None and d1 and d1.get("ok") and r1 and r1[-1].get("text") == long_text and r1[-1]["len"] <= 32767 and r1[-1].get("stdin"),
        f"{e1!r} {d1} {[{k: v for k, v in r.items() if k != 'text'} for r in r1]}")
w.session_post("win-supervisor-app", "breve", "master", "uds:")
r1b = rows()[-1]
T.check("WL1 a short text still goes on the command line (no stdin)", r1b.get("text") == "breve" and "stdin" not in r1b, str(r1b))

# WL2
try:
    w.ssh.run("x" * 40000, timeout=30)
    e2 = None
except AD.HostError as e:
    e2 = e
T.check("WL2 a command line over Windows' limit that still reaches ssh: the error says too long, not «unreachable»",
        e2 is not None and "unreachable" not in str(e2) and "32767" in str(e2), repr(e2))

T.rm(tmp)
T.finish()
