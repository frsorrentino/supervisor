#!/usr/bin/env python3
"""Verifica il comportamento su Windows nativo (test del 27/09/2026): py.sh, il silenzio degli hook, la riga della CLI.

P1  py.sh: python3 e' l'alias dello Store (esce 9009) → salta a `python`, esporta PYTHONUTF8/PYTHONIOENCODING
P2  py.sh: la scelta resta in cache (~/.cache/cc-supervisor/python) e vale al giro dopo, senza sonde
P3  py.sh: nessun Python (python3, python, py tutti rotti) → UNA riga su stderr, niente stdout, uscita 0
P4  py.sh: CC_SUPERVISOR_PY vince su cache e sonde
P5  hooks.json: ogni hook di cm-hook.py passa da scripts/py.sh (SessionEnd da detach.sh, che usa py.sh), nessun `python3` nudo
H1  cm-hook.py con os.name == "nt": il primo SessionStart stampa una riga (WSL2) ed esce 0, senza caricare la config
H2  cm-hook.py su Windows: il secondo SessionStart e Stop/UserPromptSubmit/PostToolUse/SessionEnd tacciono, uscita 0
C1  CLI con uname MINGW: un comando qualunque → una riga su stderr, niente stdout, uscita 3, nessun python avviato
C2  CLI su Windows: LANG=it → la riga in italiano
C3  CLI su Windows: `version` risponde lo stesso (non serve python)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
PYSH = T.SCRIPTS / "py.sh"


def stub(d, name, body):
    p = d / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)


def run_py(bindir, home, extra=None, code="import os; print(os.environ.get('PYTHONUTF8'), os.environ.get('PYTHONIOENCODING'))"):
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(home)}
    env.update(extra or {})
    return subprocess.run(["bash", str(PYSH), "-c", code], capture_output=True, text=True, env=env, timeout=30)


# --- P1/P2: python3 is the Store alias, python is real
home1 = tmp / "home1"
home1.mkdir()
b1 = tmp / "bin1"
b1.mkdir()
stub(b1, "python3", "echo 'Python non è stato trovato; eseguire senza argomenti da installare dal Microsoft Store' >&2; exit 9009")
stub(b1, "py", "exit 127")
os.symlink(sys.executable, b1 / "python")
r = run_py(b1, home1)
T.check("P1 Store alias skipped, `python` used, UTF-8 exported", r.returncode == 0 and r.stdout.strip() == "1 utf-8", f"{r.returncode} {r.stdout!r} {r.stderr!r}")
cache = home1 / ".cache" / "cc-supervisor" / "python"
cached = cache.read_text().strip() if cache.exists() else ""
# poison the probe: if the cache is honoured, python3 is never run again
stub(b1, "python3", f"touch {tmp}/probed; exit 9009")
r2 = run_py(b1, home1)
T.check("P2 choice cached and reused without probing", cached == "python" and r2.returncode == 0 and not (tmp / "probed").exists(),
        f"cache={cached!r} rc={r2.returncode} probed={(tmp / 'probed').exists()}")

# --- P3: no Python at all
home3 = tmp / "home3"
home3.mkdir()
b3 = tmp / "bin3"
b3.mkdir()
stub(b3, "python3", "exit 9009")
stub(b3, "python", "exit 127")
stub(b3, "py", "exit 127")
r = run_py(b3, home3)
T.check("P3 no Python: one stderr line, no stdout, exit 0",
        r.returncode == 0 and r.stdout == "" and len(r.stderr.strip().splitlines()) == 1 and "Python 3.8+" in r.stderr, f"{r.returncode} {r.stdout!r} {r.stderr!r}")
T.check("P3b no Python: nothing cached", not (home3 / ".cache" / "cc-supervisor" / "python").exists())

# --- P4: override
r = run_py(b3, home3, {"CC_SUPERVISOR_PY": sys.executable}, code="print('over')")
T.check("P4 CC_SUPERVISOR_PY wins", r.returncode == 0 and r.stdout.strip() == "over", f"{r.returncode} {r.stdout!r} {r.stderr!r}")

# --- P5: hooks.json
hooks = json.loads((T.PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
cmds = [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]
cm = [c for c in cmds if "cm-hook.py" in c]
# SessionEnd passa da detach.sh, che lancia py.sh staccato (2.1.287: 1,5 s per tutti gli hook di session.end)
# 24/10/2026 (1.83): nove con Elicitation ed ElicitationResult
T.check("P5 every cm-hook.py hook runs through scripts/py.sh (SessionEnd through detach.sh, which runs py.sh)",
        len(cm) == 9 and all(c.startswith('bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" ')
                             or (c.startswith('bash "${CLAUDE_PLUGIN_ROOT}/scripts/detach.sh" ') and c.endswith(" SessionEnd"))
                             for c in cm)
        and 'py.sh' in (T.SCRIPTS / "detach.sh").read_text(), json.dumps(cm)[:400])

# --- H1/H2: cm-hook.py believing it runs on Windows (os.name set by a sitecustomize before the script starts)
site = tmp / "site"
site.mkdir()
(site / "sitecustomize.py").write_text("import os\nos.name = 'nt'\n")
homew = tmp / "homew"
homew.mkdir()
state = tmp / "state-should-not-exist"
cfgw = tmp / "config-w.json"
cfgw.write_text(json.dumps({"state_dir": str(state)}))


def hook(ev):
    env = {"PATH": os.environ["PATH"], "HOME": str(homew), "PYTHONPATH": str(site), "CC_SUPERVISOR_CONFIG": str(cfgw)}
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), ev], input=json.dumps({"session_id": "S", "cwd": str(tmp)}),
                          capture_output=True, text=True, env=env, timeout=30)


r = hook("SessionStart")
mark = homew / ".local" / "state" / "cc-supervisor" / "windows-notice-shown"
T.check("H1 first SessionStart on Windows: one line about WSL2, exit 0, marker written",
        r.returncode == 0 and len(r.stdout.strip().splitlines()) == 1 and "WSL2" in r.stdout and r.stderr == "" and mark.exists(),
        f"{r.returncode} {r.stdout!r} {r.stderr!r}")
T.check("H1b no kernel injected, no state written", "SUPERVISOR" not in r.stdout and not state.exists())
rs = [(ev, hook(ev)) for ev in ("SessionStart", "UserPromptSubmit", "PostToolUse", "PermissionRequest", "Stop", "StopFailure", "SessionEnd")]
T.check("H2 every later event on Windows is silent, exit 0",
        all(x.returncode == 0 and x.stdout == "" and x.stderr == "" for _, x in rs) and not state.exists(),
        "; ".join(f"{ev}:{x.returncode}:{x.stdout[:60]!r}:{x.stderr[:120]!r}" for ev, x in rs if x.returncode or x.stdout or x.stderr))

# --- C1-C3: the CLI under Git Bash (uname says MINGW64_NT)
bw = tmp / "binw"
bw.mkdir()
stub(bw, "uname", "echo MINGW64_NT-10.0-19045")
stub(bw, "python3", f"touch {tmp}/python-ran; exit 9009")
CLI = T.PLUGIN / "bin" / "supervisor"


def cli(*args, lang="C.UTF-8"):
    env = {"PATH": f"{bw}:/usr/bin:/bin", "HOME": str(homew), "LANG": lang}
    return subprocess.run(["bash", str(CLI), *args], capture_output=True, text=True, env=env, timeout=30)


outs = [cli(c) for c in ("sessions", "doctor", "launch", "init")]
T.check("C1 Windows CLI: one stderr line, no stdout, exit 3, python never started",
        all(x.returncode == 3 and x.stdout == "" and len(x.stderr.strip().splitlines()) == 1 and "WSL2" in x.stderr for x in outs)
        and not (tmp / "python-ran").exists(), repr([(x.returncode, x.stdout, x.stderr) for x in outs])[:500])
r = cli("sessions", lang="it_IT.UTF-8")
T.check("C2 LANG=it → Italian line", r.returncode == 3 and "richiede Linux o macOS con tmux" in r.stderr, r.stderr)
r = cli("version")
ver = json.loads((T.PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"]
T.check("C3 `version` still answers on Windows", r.returncode == 0 and r.stdout.strip() == ver, f"{r.returncode} {r.stdout!r} {r.stderr!r}")

T.finish()
