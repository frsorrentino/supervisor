#!/usr/bin/env python3
"""Un altro account di Claude Code su win (06/11/2026, supervisor-win-altro-account-06-11). Tutto in una cartella
temporanea: gli script che vanno su win girano qui con node e una home finta, PowerShell no (si guarda lo script).

WA1 session_start: per .claude-pixel il .cmd mette CLAUDE_CONFIG_DIR=%USERPROFILE%\\.claude-pixel prima di claude, con
    le virgolette protette per la stringa PowerShell; per .claude nessuna riga; un nome di config non sicuro e' rifiutato
WA2 la fiducia nella cartella (node): per .claude-pixel nel .claude.json dentro quella cartella, anche se non c'e' ancora,
    e ~/.claude.json non si tocca; per .claude in ~/.claude.json come prima
WA3 cm-session.js trova una sessione registrata in .claude-pixel/sessions e legge la sua trascrizione da
    .claude-pixel/projects; una di .claude come prima
WA4 cm-login.js con un claude finto: stampa l'indirizzo in out.txt, passa a claude il codice di code.txt, scrive
    result.json con rc 0 e lo stato di auth status, con CLAUDE_CONFIG_DIR della config giusta; codice sbagliato: rc 1
WA5 login_start e login_code rifiutano email e codici con caratteri da shell prima di toccare l'host
"""
import base64
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
home.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                           "default_account": "personale"}))
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "CM_HOME": str(home)})
sp = importlib.util.spec_from_file_location("cm_adapters_wa", T.SCRIPTS / "cm-adapters.py")
AD = importlib.util.module_from_spec(sp)
sp.loader.exec_module(AD)
REMOTE = T.SCRIPTS.parent / "remote"


class Spy(AD.WindowsNative):
    """L'adattatore di win senza ssh: gli script che manderebbe restano qui."""
    def __init__(self):
        self.sent = []

    def ps_json(self, script, timeout=120):
        self.sent.append(script)
        return {"ok": True}


# WA1
w = Spy()
w.session_start("joy-seo", "D:/cm-sessions/studio/clienti/beta", ["--remote-control", "joy-seo"], ".claude-pixel")
w.session_start("alfa", "D:/cm-sessions/personali/alfa", ["--remote-control", "alfa"])
s_pix, s_def = w.sent
line = next((ln for ln in s_pix.splitlines() if "WriteAllText($cmdf" in ln), "")
try:
    w.session_start("x", "D:/x", [], ".claude;calc")
    bad_cfg = False
except AD.HostError:
    bad_cfg = True
T.check("WA1 the .cmd of a .claude-pixel session sets CLAUDE_CONFIG_DIR before claude, quotes escaped for PowerShell; .claude has no set line; an unsafe config name is refused",
        'set `"CLAUDE_CONFIG_DIR=%USERPROFILE%\\.claude-pixel`"`r`n' in line and line.index("CLAUDE_CONFIG_DIR") < line.index("claude --remote-control")
        and "CLAUDE_CONFIG_DIR" not in s_def and bad_cfg, line[:400])

# WA2
js = tmp / "trust.js"
js.write_text(AD.WindowsNative._TRUST_JS)
nenv = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
(home / ".claude.json").write_text(json.dumps({"projects": {}, "keep": 1}, indent=2))
before = (home / ".claude.json").read_text()
r1 = subprocess.run(["node", str(js), "D:\\cm-sessions\\studio\\clienti\\beta", ".claude-pixel"], capture_output=True, text=True, env=nenv, timeout=30)
pix = json.loads((home / ".claude-pixel" / ".claude.json").read_text()) if (home / ".claude-pixel" / ".claude.json").is_file() else {}
r2 = subprocess.run(["node", str(js), "D:\\cm-sessions\\personali\\alfa", ".claude"], capture_output=True, text=True, env=nenv, timeout=30)
dflt = json.loads((home / ".claude.json").read_text())
T.check("WA2 trust for .claude-pixel goes in .claude-pixel/.claude.json (created), ~/.claude.json untouched; for .claude in ~/.claude.json as before",
        r1.returncode == 0 and pix.get("projects", {}).get("D:/cm-sessions/studio/clienti/beta", {}).get("hasTrustDialogAccepted") is True
        and before != "" and r2.returncode == 0 and dflt.get("keep") == 1 and dflt["projects"].get("D:/cm-sessions/personali/alfa", {}).get("hasTrustDialogAccepted") is True
        and "D:/cm-sessions/studio/clienti/beta" not in dflt["projects"], r1.stdout + r1.stderr + r2.stderr + json.dumps(pix))

# WA3
for cd, name, sid, cwd, text in ((".claude-pixel", "joy-seo", "sid-joy", "D:\\cm\\joy", "risposta dello studio"),
                                 (".claude", "alfa", "sid-alfa", "D:\\cm\\alfa", "risposta personale")):
    (home / cd / "sessions").mkdir(parents=True, exist_ok=True)
    (home / cd / "sessions" / f"{os.getpid()}.json").write_text(json.dumps({"name": name, "pid": os.getpid(), "sessionId": sid, "cwd": cwd, "status": "idle", "startedAt": 1}))
    pdir = home / cd / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{sid}.jsonl").write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
outs = {}
for name in ("joy-seo", "alfa"):
    r = subprocess.run(["node", str(REMOTE / "cm-session.js"), "read", name, "0"], capture_output=True, text=True, env=nenv, timeout=30)
    outs[name] = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"err": r.stderr}
T.check("WA3 cm-session.js finds the session of .claude-pixel/sessions and reads its transcript from .claude-pixel/projects; the .claude one as before",
        outs["joy-seo"].get("texts") == ["risposta dello studio"] and outs["alfa"].get("texts") == ["risposta personale"], json.dumps(outs))

# WA4
fb = tmp / "bin"
fb.mkdir()
(fb / "claude").write_text("""#!/bin/sh
if [ "$1 $2" = "auth login" ]; then
  echo "Opening browser to sign in..."
  echo "If the browser didn't open, visit: https://example.test/oauth?login_hint=$4&cfg=${CLAUDE_CONFIG_DIR:-none}"
  printf "Paste code here if prompted > "
  read code
  if [ "$code" = "good#state" ]; then echo "Login successful"; exit 0; fi
  echo "Login failed: That code wasn't accepted."; exit 1
fi
if [ "$1 $2" = "auth status" ]; then
  echo "{\\"loggedIn\\": true, \\"email\\": \\"prova@example.com\\", \\"configDirectory\\": \\"${CLAUDE_CONFIG_DIR:-default}\\"}"; exit 0
fi
exit 9
""")
(fb / "claude").chmod(0o755)
lenv = dict(nenv, PATH=str(fb) + os.pathsep + os.environ["PATH"])


def login(cfgname, code):
    d = tmp / f"login{cfgname}{code[:4]}"
    d.mkdir()
    p = subprocess.Popen(["node", str(REMOTE / "cm-login.js"), "serve", str(d), cfgname, "prova@example.com"], env=lenv,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    url = ""
    for _ in range(100):
        m = re.search(r"visit:\s*(https://\S+)", (d / "out.txt").read_text() if (d / "out.txt").is_file() else "")
        if m:
            url = m.group(1)
            break
        time.sleep(0.1)
    (d / "code.txt").write_text(code)
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
    res = json.loads((d / "result.json").read_text()) if (d / "result.json").is_file() else {}
    return url, res, (d / "code.txt").exists()


u1, res1, left1 = login(".claude-pixel", "good#state")
u2, res2, _ = login(".claude-pixel", "bad#state")
u3, res3, _ = login(".claude", "good#state")
T.check("WA4 cm-login.js: the address in out.txt, the code passed to claude and consumed, result rc 0 with auth status, CLAUDE_CONFIG_DIR of .claude-pixel",
        "login_hint=prova@example.com" in u1 and u1.endswith(str(home / ".claude-pixel")) and res1.get("rc") == 0 and res1.get("sent") is True
        and (res1.get("status") or {}).get("loggedIn") is True and (res1.get("status") or {}).get("configDirectory") == str(home / ".claude-pixel") and not left1,
        json.dumps([u1, res1]))
T.check("WA4 a wrong code: rc 1; .claude: no CLAUDE_CONFIG_DIR (Claude Code's default)",
        res2.get("rc") == 1 and u3.endswith("cfg=none") and (res3.get("status") or {}).get("configDirectory") == "default", json.dumps([res2, u3, res3]))

# WA5
w = Spy()
refused = []
for fn, args in ((w.login_start, (".claude-pixel", "a@b.it & calc")), (w.login_start, (".claude-pixel", "a\"@b.it")),
                 (w.login_code, (".claude-pixel", "abc';calc;'")), (w.login_start, ("..\\x", "a@b.it"))):
    try:
        fn(*args)
        refused.append(False)
    except AD.HostError:
        refused.append(True)
w.login_start(".claude-pixel", "info@example.com", wait_s=1)
T.check("WA5 emails and codes with shell characters, and a config outside .claude*, are refused before anything is sent; a good one is sent as a scheduled task",
        all(refused) and len(w.sent) == 1 and "cm-login-.claude-pixel" in w.sent[0] and "serve" in w.sent[0] and "info@example.com" in w.sent[0],
        str(refused) + (w.sent[0][:300] if w.sent else ""))

T.rm(tmp)
T.finish()
