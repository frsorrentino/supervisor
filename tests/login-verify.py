#!/usr/bin/env python3
"""La scadenza del login si rilegge anche dal cron (12/11/2026, supervisor-login-scaduto-fermo-11-11: «Il login del secondo account è
scaduto il 09/11» restava fra le decisioni l'11/11 alle 19 con il login rinnovato; la guardia girava dal cron, col PATH
/usr/bin:/bin non trovava `claude` e teneva la lettura vecchia).

LG1 col PATH del cron e senza CM_CLAUDE_BIN, claude_bin() trova ~/.local/bin/claude
LG2 `task login-check` col PATH del cron: la voce «scaduto» di una lettura vecchia sparisce, la scadenza nuova e' scritta
LG3 senza nessun claude da nessuna parte la lettura di prima resta (mai una voce falsa per un errore)
LG4 launch: la condizione che fa rileggere il login vale per l'account con la voce aperta e non per gli altri; launch la usa
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (home / ".claude", home / ".claude-pixel", home / ".local" / "bin"):
    d.mkdir(parents=True)
state = tmp / "state"
cfg = tmp / "c.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "workspace": {"root": str(home)},
                           "accounts": {"personale": {"config_dir": str(home / ".claude")},
                                        "professionale": {"config_dir": str(home / ".claude-pixel"), "label": "Studio"}},
                           "default_account": "personale"}))
NOW = int(time.time())
claude = home / ".local" / "bin" / "claude"
claude.write_text('#!/bin/sh\n[ "$1 $2" = "auth status" ] && echo \'{"loggedIn": true, "loginExpiresAt": "2026-12-07T10:00:00Z"}\'\n')
claude.chmod(0o755)
ENV = {"PATH": "/usr/bin:/bin", "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db"), "LANG": "it_IT.UTF-8"}
state.mkdir()
old = {"at": NOW - 3 * 86400, "accounts": {"personale": {"label": "personale", "logged_in": True, "expires_at": NOW + 30 * 86400},
                                           "professionale": {"label": "Studio", "logged_in": True, "expires_at": NOW - 2 * 86400}}}
(state / "login-expiry.json").write_text(json.dumps(old))


def py(code):
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=ENV, timeout=60)


LOAD = (f"import importlib.util as u; s=u.spec_from_file_location('l', {str(T.SCRIPTS / 'cm-login.py')!r}); "
        "L=u.module_from_spec(s); s.loader.exec_module(L); ")

# LG1
r1 = py(LOAD + "print(L.claude_bin())")
T.check("LG1 with the cron's PATH (/usr/bin:/bin) and no CM_CLAUDE_BIN, claude_bin finds ~/.local/bin/claude", r1.stdout.strip() == str(claude), r1.stdout + r1.stderr)

due_before = json.loads(py(LOAD + "import json; print(json.dumps(L.due()))").stdout)
# LG2
r2 = subprocess.run([str(T.SCRIPTS / "supervisor"), "task", "login-check", "--json"], capture_output=True, text=True, env=ENV, timeout=120)
after = json.loads((state / "login-expiry.json").read_text())
due_after = json.loads(py(LOAD + "import json; print(json.dumps(L.due()))").stdout)
T.check("LG2 task login-check with the cron's PATH: the «expired» item of an old reading goes away, the new expiry written for both accounts",
        [x["account"] for x in due_before] == ["professionale"] and due_after == [] and r2.returncode == 0
        and after["accounts"]["professionale"]["expires_at"] == 1796637600 and after["at"] >= NOW, f"{due_before} {due_after} {r2.stdout}{r2.stderr} {after}")

# LG3
(state / "login-expiry.json").write_text(json.dumps(old))
claude.unlink()
ENV3 = dict(ENV, PATH="/nonexistent")
r3 = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), "login-check", "--json"], capture_output=True, text=True, env=ENV3, timeout=120)
kept = json.loads((state / "login-expiry.json").read_text())["accounts"]
T.check("LG3 with no claude anywhere the earlier reading stays (never a false item because of an error)",
        kept == old["accounts"], f"{kept} {r3.stderr[-300:]}")

# LG4
COND = (str(T.SCRIPTS / "cm-launch.sh"))
src = Path(COND).read_text()
line = next((l for l in src.splitlines() if "m.due()" in l), "")
snippet = line.split("python3 -c '", 1)[1].split("'", 1)[0] if "python3 -c '" in line else ""
yes = subprocess.run([sys.executable, "-c", snippet, str(T.SCRIPTS / "cm-login.py"), "professionale"], env=ENV, capture_output=True, text=True, timeout=60).returncode
no = subprocess.run([sys.executable, "-c", snippet, str(T.SCRIPTS / "cm-login.py"), "personale"], env=ENV, capture_output=True, text=True, timeout=60).returncode
T.check("LG4 launch: its condition holds for the account with an open login item and not for the other; when it holds launch starts task login-check in the background",
        snippet and yes == 0 and no == 1 and 'task login-check >/dev/null 2>&1 < /dev/null &' in src, f"{yes} {no} {snippet[:80]}")
T.rm(tmp)
T.finish()
