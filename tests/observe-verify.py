#!/usr/bin/env python3
"""Verifica la copia di claude-observe dentro supervisor (23/09/2026). La logica la prova la suite della fonte
(github.com/frsorrentino/claude-observe, tests/observe-verify.py); qui solo che la copia e' quella e che e' collegata.

OI1 check.sh della fonte: la copia coincide (la fonte sta accanto, ../claude-observe, o in CLAUDE_OBSERVE_SRC)
OI2 hooks.json: PostToolUseFailure solo verso la copia, con matcher Bash; SessionStart con cm-hook E la copia
OI3 `supervisor observe add/list` passa dalla copia, nel file supervisor.jsonl della cartella comune
OI4 l'hook della copia: un comando supervisor fallito → record con la versione di plugin.json; un codice innocuo → niente
OI5 il relay: record_external come lo chiama cm-relay.py (source relay)
OI6 cm-hook.py non tocca piu' le osservazioni: niente righe OSSERVAZIONI, PostToolUseFailure ignorato
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

SRC = Path(os.environ.get("CLAUDE_OBSERVE_SRC") or T.ROOT.parent / "claude-observe")
COPY = T.PLUGIN / "observe" / "observe.py"
tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
state = tmp / "state"
box = state / "claude-observe"
cfg = tmp / "cm-config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "cmstate"), "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                           "default_account": "personale"}))
ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "LANG": "it_IT.UTF-8", "XDG_STATE_HOME": str(state),
       "CLAUDE_OBSERVE_CONFIG": str(tmp / "observe.json"), "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CC_SUPERVISOR_CONFIG": str(cfg)}


def recs():
    try:
        return [json.loads(l) for l in (box / "supervisor.jsonl").read_text().splitlines() if l.strip()]
    except OSError:
        return []


r = subprocess.run(["bash", str(SRC / "check.sh"), str(T.PLUGIN)], capture_output=True, text=True) if (SRC / "check.sh").exists() else None
T.check("OI1 the copy is the source's observe.py (check.sh of claude-observe)", r is not None and r.returncode == 0,
        (r.stdout + r.stderr) if r else f"fonte assente: {SRC}")
hooks = json.loads((T.PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
ptf = [(e.get("matcher"), h["command"]) for e in hooks.get("PostToolUseFailure", []) for h in e["hooks"]]
ss = [h["command"] for e in hooks.get("SessionStart", []) for h in e["hooks"]]
T.check("OI2 PostToolUseFailure only to the copy (matcher Bash); SessionStart runs cm-hook and the copy",
        ptf == [("Bash", 'bash "${CLAUDE_PLUGIN_ROOT}/observe/py.sh" "${CLAUDE_PLUGIN_ROOT}/observe/observe.py" hook')]
        and any("cm-hook.py\" SessionStart" in c for c in ss) and any("observe.py\" session-start" in c for c in ss), json.dumps(hooks)[:600])
cli = T.PLUGIN / "scripts" / "supervisor"
a = subprocess.run([str(cli), "observe", "add", "supervisor", "restart --clean perde la coda", "--class", "D"], capture_output=True, text=True, env=ENV)
l = subprocess.run([str(cli), "observe", "list"], capture_output=True, text=True, env=ENV)
T.check("OI3 supervisor observe add/list go through the copy, into supervisor.jsonl of the common folder",
        a.returncode == 0 and any(x["source"] == "manual" for x in recs()) and "restart --clean perde la coda" in l.stdout, a.stdout + a.stderr + l.stdout)
ver = json.loads((T.PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"]


def hook(cmd, err):
    p = {"hook_event_name": "PostToolUseFailure", "session_id": "S", "cwd": str(home), "tool_name": "Bash",
         "tool_input": {"command": cmd}, "error": err, "is_interrupt": False}
    return subprocess.run([sys.executable, str(COPY), "hook"], input=json.dumps(p), capture_output=True, text=True, env=ENV)


hook("supervisor tile --boh", "Exit code 2\nopzione sconosciuta")
hook("supervisor restart arm --switch-account", "Exit code 4\nrifiutato")
h = [x for x in recs() if x["source"] == "hook-bash"]
T.check("OI4 the copy's hook: a failed supervisor command → a record with the version from plugin.json; a benign exit → nothing",
        len(h) == 1 and h[0]["call"] == "supervisor tile --boh" and h[0]["context"]["tool_version"] == ver, json.dumps(h)[:500])
code = ("import importlib.util,sys;from pathlib import Path;s=importlib.util.spec_from_file_location('o',Path(sys.argv[1]));"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.record_external('relay','relay answer','sessione occupata','atlas-shop')")
subprocess.run([sys.executable, "-c", code, str(T.SCRIPTS.parent / "observe" / "observe.py")], env=ENV, check=True)
relay_src = (T.SCRIPTS / "cm-relay.py").read_text()
T.check("OI5 relay: record_external as cm-relay.py calls it → a relay record",
        any(x["source"] == "relay" for x in recs()) and 'HERE.parent / "observe" / "observe.py"' in relay_src and 'record_external("relay"' in relay_src, "")
st = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "SessionStart"], input=json.dumps({"session_id": "S", "cwd": str(T.ROOT)}),
                    capture_output=True, text=True, env=ENV)
pf = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "PostToolUseFailure"], input="{}", capture_output=True, text=True, env=ENV)
T.check("OI6 cm-hook.py no longer handles observations (the copy's own hook does): no OSSERVAZIONI, PostToolUseFailure a no-op",
        "OSSERVAZIONI" not in st.stdout and pf.returncode == 0 and pf.stdout == "", st.stdout[-300:])
T.finish()
