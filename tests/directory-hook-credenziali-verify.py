#!/usr/bin/env python3
"""Gli hook e le credenziali come li chiede il portale della directory (26/11/2026, supervisor-directory-hook-credenziali-26-11;
ok del maintainer alle 16:23).

La misura del tag directory-0.8.215 dava HOOK_GRANTS_PERMISSION (PermissionRequest e due PreToolUse): «print its JSON answer
whole with one command; keep backslash escapes such as \\x27 and printf % conversions out of the hook's text and scripts,
and allow or approve out of any other quoted value; send everything else to standard error (>&2) without merging it
back (2>&1)». E MCP_FORWARDS_CREDENTIAL_ENV su 13 file, fra cui la lista dei file esclusi letta come una lettura.

HC1 gli script degli hook (cm-fast.sh, py.sh, detach.sh, cm-lane-guard.py, cm-hook.py): niente printf, niente \\x27,
    niente 2>&1, e allow/approve in nessun testo fra virgolette
HC2 cm-hook.py: quello che va a Claude Code esce con UNA scrittura su stdout, alla fine, anche se le parti sono piu'
HC3 la guardia legge il registro attraverso cm-tasks (lane_covered, lane_waiting_start): stesso esito di prima
HC4 scheduler.exclude_globs al posto di secret_globs: il nome vecchio nel file si legge ancora (la lista della persona
    resta, nessuna chiave ignota), i default e config.example.json hanno il nome nuovo
HC5 doctor non apre il token della CLI di Firebase: la riga sulla pulizia degli account anonimi dice dove controllarla,
    con il link del progetto

Seconda misura (directory-0.8.217): la guardia «writes a backslash escape (such as \\156 or \\x22) that printf, echo or a
JSON parser could turn into a printable character», e il testo di PermissionRequest (py.sh) «could not be read to the end».
HC6 cm-lane-guard.py, cm-fast.sh e py.sh: nessuna sequenza di escape stampabile (barra rovescia + x, u, una cifra
    ottale, virgolette, apice, barra o barra rovescia)
HC7 il rifiuto della guardia, per un comando con virgolette e barre rovesce: il JSON scritto non ha sequenze di escape
HC8 py.sh: nessuna espansione annidata, nessun interprete lanciato da una variabile spezzata in parole; la cache di
    prima con «py -3» si legge ancora

Terza misura (directory-0.8.219, 27/11/2026): COMMAND_PATH_COMPUTED, un programma in riga (python3 -c) e i percorsi
calcolati di cm-fast.sh; e HOOK_GRANTS_PERMISSION resta su PermissionRequest, che non stampa niente.
HC9  il comando di PermissionRequest manda lo stdout su stderr (>&2): lanciato com'e' in hooks.json, niente su stdout,
     e il file waiting/<sid> si scrive come prima
HC10 py.sh prova ogni interprete con scripts/pyver.py (niente -c), anche senza CLAUDE_PLUGIN_ROOT; cm-fast.sh lancia
     ogni script col percorso letterale ${CLAUDE_PLUGIN_ROOT}/scripts/<file>, senza $(…) ne' $here
"""
import importlib.util
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
cfgp = tmp / "config.json"
cfgp.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "workspace": {"root": str(home / "ws")},
                            "scheduler": {"secret_globs": ["*.segreto", ".env*"]},
                            "relay": {"firebase_url": "https://progetto-prova-default-rtdb.europe-west1.firebasedatabase.app"}}))
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfgp), "CM_PROC_SCAN_PIDS": ""})
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE"):
    os.environ.pop(k, None)


def load(name, tag):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + tag, T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


HOOKS = ["cm-fast.sh", "py.sh", "detach.sh", "cm-lane-guard.py", "cm-hook.py"]
bad = []
for f in HOOKS:
    text = (T.SCRIPTS / f).read_text(encoding="utf-8")
    for i, ln in enumerate(text.splitlines(), 1):
        code = ln.split("#", 1)[0] if f.endswith(".sh") else ln
        if re.search(r"\bprintf\b", code) or "\\x27" in code or "2>&1" in code:
            bad.append(f"{f}:{i}: {ln.strip()[:100]}")
    for m in re.finditer(r"\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'", text):
        if re.search(r"\b(allow|allowed|approve|approved)\b", m.group(0), re.I):
            bad.append(f"{f}: {m.group(0)[:90]!r}")
T.check("HC1 the hook scripts: no printf, no \\x27, no 2>&1, allow/approve in no quoted text", not bad, "\n".join(bad[:20]))

H = load("cm-hook", "_hc")
calls = []


class Count(io.StringIO):
    def write(self, s):
        calls.append(s)
        return len(s)


H.main = lambda argv: (sys.stdout.write("riga uno\n"), sys.stdout.write('{"decision": "block", "reason": "x"}\n'), 0)[2]
real = sys.stdout
sys.stdout = Count()
try:
    rc = H.run_once(["UserPromptSubmit"])
finally:
    sys.stdout = real
T.check("HC2 cm-hook.py: what goes to Claude Code leaves with ONE write on stdout, at the end, even with several parts",
        rc == 0 and calls == ['riga uno\n{"decision": "block", "reason": "x"}\n'], f"{rc} {calls}")

TK = load("cm-tasks", "_hc")
G = load("cm-lane-guard", "_hc")
proj = home / "ws" / "alfa"
proj.mkdir(parents=True)
con = sqlite3.connect(":memory:")
con.row_factory = sqlite3.Row
con.executescript("CREATE TABLE tasks (id TEXT, plan TEXT, state TEXT, project TEXT, contract TEXT);"
                  "CREATE TABLE plans (id TEXT, approved_at INTEGER);"
                  "CREATE TABLE approvals (task TEXT, by_ TEXT);")
c = json.dumps({"task": {"id": "t1"}})
con.execute("INSERT INTO tasks VALUES ('t1', NULL, 'running', ?, ?)", (str(proj), c))
con.execute("INSERT INTO tasks VALUES ('t2', NULL, 'assigned', ?, ?)", (str(proj / "b"), c))
con.execute("INSERT INTO approvals VALUES ('t1', 'persona')")
con.execute("INSERT INTO approvals VALUES ('t2', 'persona')")
(proj / "b").mkdir()
src = (T.SCRIPTS / "cm-lane-guard.py").read_text(encoding="utf-8")
T.check("HC3 the guard reads the registry through cm-tasks (lane_covered, lane_waiting_start): same answers as before",
        hasattr(TK, "lane_covered") and G.covered(con, str(proj / "x"))[0] is True and G.covered(con, str(home))[0] is False
        and G.waiting_start(con, str(proj / "b")) == "t2" and "FROM approvals" not in src and "SELECT" not in src, "")

CF = load("cm-config", "_hc")
err = io.StringIO()
real_err, sys.stderr = sys.stderr, err
try:
    cfg = CF.load()
finally:
    sys.stderr = real_err
ex = json.loads((T.ROOT / "supervisor" / "config.example.json").read_text(encoding="utf-8"))
T.check("HC4 scheduler.exclude_globs: the old name in the file is still read (the person's list stays, no unknown key), defaults and config.example.json use the new name",
        cfg["scheduler"].get("exclude_globs") == ["*.segreto", ".env*"] and "secret_globs" not in cfg["scheduler"] and "unknown" not in err.getvalue()
        and "exclude_globs" in CF.defaults_for("it")["scheduler"] and "exclude_globs" in ex["scheduler"] and "secret_globs" not in ex["scheduler"],
        f"{cfg['scheduler'].get('exclude_globs')} {err.getvalue()[:200]}")

store = tmp / "firebase-tools.json"
store.write_text(json.dumps({"tokens": {"access_token": "non-va-letto", "expires_at": 9e15}}))
os.environ["CM_FIREBASE_CONFIGSTORE"] = str(store)
opened = []
orig = Path.read_text
Path.read_text = lambda self, *a, **k: (opened.append(str(self)), orig(self, *a, **k))[1]
try:
    row = CF.anon_cleanup_row(cfg)
finally:
    Path.read_text = orig
T.check("HC5 doctor does not open the Firebase CLI token: the anonymous clean-up row says where to check it, with the project's link",
        row and row[0] == "INFO" and row[1] == "doctor.anon_cleanup_check" and "progetto-prova/authentication/settings" in row[2]["link"]
        and str(store) not in opened and "firebase-tools" not in "".join(opened), f"{row} {opened}")
ESC = re.compile(re.escape(chr(92)) + "(x[0-9a-fA-F]|u[0-9a-fA-F]{4}|[0-7]|[" + chr(34) + "'/]|" + re.escape(chr(92)) + ")")
bad = []
for f in ("cm-lane-guard.py", "cm-fast.sh", "py.sh"):
    for i, ln in enumerate((T.SCRIPTS / f).read_text(encoding="utf-8").splitlines(), 1):
        if ESC.search(ln):
            bad.append(f"{f}:{i}: {ln.strip()[:100]}")
T.check("HC6 cm-lane-guard.py, cm-fast.sh, py.sh: no printable backslash escape in the text", not bad, "\n".join(bad[:20]))

cmd = 'git commit -m "x" && cd ' + chr(92) + 'tmp' + chr(92) + 'b && git push'
shown = G.shown(cmd)
out = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                         "permissionDecisionReason": "Closed lane: «" + shown + "» … " + G.KEYS_REASON}}, ensure_ascii=False)
T.check("HC7 the guard's refusal for a command with quotes and backslashes: no escape sequence in the JSON it writes",
        chr(92) not in out and "git push" in shown and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny", out)

ps = (T.SCRIPTS / "py.sh").read_text(encoding="utf-8")
code = "\n".join(ln.split("#", 1)[0] for ln in ps.splitlines())
cache = home / ".cache" / "cc-supervisor" / "python"
cache.parent.mkdir(parents=True, exist_ok=True)
cache.write_text("py -3\n")
fakebin = tmp / "bin-py"
fakebin.mkdir()
(fakebin / "py").write_text("#!/bin/sh\necho \"py:$*\"\n")
(fakebin / "py").chmod(0o755)
env = {k: v for k, v in os.environ.items() if k not in ("CC_SUPERVISOR_PY", "TEAM_SUPERVISOR_PY", "CLAUDE_MASTER_PY", "XDG_CACHE_HOME")}
env.update({"HOME": str(home), "PATH": str(fakebin) + os.pathsep + env.get("PATH", "")})
r = subprocess.run(["bash", str(T.SCRIPTS / "py.sh"), "x.py", "a"], capture_output=True, text=True, env=env, timeout=30)
T.check("HC8 py.sh: no nested expansion, no interpreter run from a word-split variable; the old cache «py -3» still works",
        ":-${" not in code and not re.search(r"(^|[;&|]|exec)\s+\$[A-Za-z_]", code, re.M) and r.stdout.strip() == "py:-3 x.py a",
        f"{r.returncode} {r.stdout!r} {r.stderr!r}")
# ------------------------------------------------------------------ terza misura (27/11/2026)
hooks = json.loads((T.PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
pr = [h["command"] for e in hooks["PermissionRequest"] for h in e["hooks"]]
cfg9 = tmp / "config-hc9.json"
cfg9.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state9"), "workspace": {"root": str(home / "ws")},
                            "hooks": {"ask_notify": {"enabled": False}}}))
env9 = {k: v for k, v in os.environ.items() if k not in ("CC_SUPERVISOR_PY", "TEAM_SUPERVISOR_PY", "CLAUDE_MASTER_PY")}
env9.update({"HOME": str(home), "CLAUDE_PLUGIN_ROOT": str(T.PLUGIN), "CC_SUPERVISOR_CONFIG": str(cfg9), "XDG_CACHE_HOME": str(tmp / "cache9")})
pay = {"session_id": "hc9", "cwd": str(home), "hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "ls"}}
r9 = subprocess.run(["bash", "-c", pr[0]], input=json.dumps(pay), capture_output=True, text=True, env=env9, timeout=60) if len(pr) == 1 else None
wf = tmp / "state9" / "waiting" / "hc9"
T.check("HC9 PermissionRequest: the command sends its stdout to stderr (>&2); run as hooks.json has it, nothing on stdout, waiting/<sid> written as before",
        len(pr) == 1 and pr[0].endswith(" PermissionRequest >&2") and "2>&1" not in pr[0] and r9.returncode == 0 and r9.stdout == ""
        and wf.is_file() and json.loads(wf.read_text())["tool"] == "Bash", f"{pr} {r9 and (r9.returncode, r9.stdout[:200], r9.stderr[-300:])}")

ps_code = "\n".join(ln.split("#", 1)[0] for ln in (T.SCRIPTS / "py.sh").read_text(encoding="utf-8").splitlines())
fs_code = "\n".join(ln.split("#", 1)[0] for ln in (T.SCRIPTS / "cm-fast.sh").read_text(encoding="utf-8").splitlines())
probe = tmp / "probe.py"
probe.write_text("print('ok-hc10')\n")
env10 = {k: v for k, v in env9.items() if k != "CLAUDE_PLUGIN_ROOT"}
env10["XDG_CACHE_HOME"] = str(tmp / "cache10")
r10 = subprocess.run(["bash", str(T.SCRIPTS / "py.sh"), str(probe)], capture_output=True, text=True, env=env10, timeout=60)
pv = subprocess.run([sys.executable, str(T.SCRIPTS / "pyver.py")], capture_output=True, text=True, timeout=30)
T.check("HC10 py.sh probes with scripts/pyver.py (no -c program), also without CLAUDE_PLUGIN_ROOT; cm-fast.sh runs each script by its literal ${CLAUDE_PLUGIN_ROOT}/scripts/ path",
        " -c " not in ps_code and ps_code.count('"${CLAUDE_PLUGIN_ROOT}/scripts/pyver.py"') == 3 and "$(" not in ps_code
        and pv.returncode == 0 and r10.returncode == 0 and r10.stdout.strip() == "ok-hc10"
        and (tmp / "cache10" / "cc-supervisor" / "python").read_text().strip() in ("python3", "python", "py")
        and "$here" not in fs_code and "dirname" not in fs_code
        and all(f'"${{CLAUDE_PLUGIN_ROOT}}/scripts/{x}"' in fs_code for x in ("py.sh", "cm-lane-guard.py", "cm-hook.py")),
        f"{r10.returncode} {r10.stdout!r} {r10.stderr[-300:]!r} {pv.returncode}")
T.rm(str(tmp))
T.finish()
