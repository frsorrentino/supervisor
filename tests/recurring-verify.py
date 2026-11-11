#!/usr/bin/env python3
"""supervisor recurring (contratto 1.33, 04/10/2026): la lista delle azioni ricorrenti della master sul PC, l'ordine
dall'ultima usata, al massimo 8 nello stato, e la voce che sale in cima quando il suo prompt parte."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="cm-recurring-"))
ENV = dict(os.environ, CM_RECURRING=str(tmp / "recurring.json"))


def rc(*a):
    p = subprocess.run([str(T.SCRIPTS / "supervisor"), "recurring", *a], capture_output=True, text=True, env=ENV, timeout=30)
    return p.returncode, p.stdout, p.stderr


T.check("RC1 empty: list says so, --json is []", rc("list")[1].strip() == "no recurring actions" and json.loads(rc("list", "--json")[1]) == [], "")
rc("add", "x-posts", "--label", "Post su X", "--prompt", "Analizza i post su X di oggi")
time.sleep(1.1)
rc("add", "release-log", "--label", "Changelog release", "--prompt", "Scrivi il changelog della release", "--param")
items = json.loads(rc("list", "--json")[1])
T.check("RC2 add: newest on top, {id, label, prompt, param} only, param true where asked", [x["id"] for x in items] == ["release-log", "x-posts"] and set(items[0]) == {"id", "label", "prompt", "param"} and items[0]["param"] is True and items[1]["param"] is False, str(items))
bad = [rc("add", "Bad ID", "--label", "x", "--prompt", "y")[0], rc("add", "ok-id", "--label", "x" * 41, "--prompt", "y")[0], rc("add", "ok-id", "--label", "x")[0]]
T.check("RC3 refused: an id outside [a-z0-9-], a label over 40 characters, no prompt", all(b != 0 for b in bad) and len(json.loads(rc("list", "--json")[1])) == 2, str(bad))
time.sleep(1.1)
rc("used", "x-posts")
T.check("RC4 used brings it to the top", [x["id"] for x in json.loads(rc("list", "--json")[1])] == ["x-posts", "release-log"], "")
spec = __import__("importlib.util").util.spec_from_file_location("cm_recurring", T.SCRIPTS / "cm-recurring.py")
mod = __import__("importlib.util").util.module_from_spec(spec)
os.environ["CM_RECURRING"] = ENV["CM_RECURRING"]
spec.loader.exec_module(mod)
hit = mod.mark_used("Scrivi il changelog della release 0.6.5", now=time.time() + 5)
T.check("RC5 a prompt that starts with an action's prompt (with the param added) brings that action to the top; another text touches nothing",
        hit == "release-log" and [x["id"] for x in mod.for_state()] == ["release-log", "x-posts"] and mod.mark_used("ciao") is None, str(hit))
for i in range(9):
    rc("add", f"a{i}", "--label", f"azione {i}", "--prompt", f"fai la cosa {i}")
T.check("RC6 the state carries at most 8", len(mod.for_state()) == 8 and len(json.loads(Path(ENV["CM_RECURRING"]).read_text())) == 11, "")
T.check("RC7 remove; removing a missing one fails", rc("remove", "a0")[0] == 0 and rc("remove", "a0")[0] != 0 and "a0" not in [x["id"] for x in mod.load()], "")
T.finish()
