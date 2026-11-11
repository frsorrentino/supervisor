#!/usr/bin/env python3
"""Un claude rimasto senza il suo tmux si chiude (orphan_claude_closed, 28/10/2026).

Dal vivo il 28/10: il claude della master partito alle 11:58 (PID 13277, `--remote-control master -n master -c`) era
ancora figlio del server tmux ma senza pannello, dopo che il tmux «master» era stato rifatto alle 12:42. Vivo e occupato
per 52 minuti, col suo Remote Control «master» registrato: l'app mostrava due master, una che non rispondeva. SIGTERM
ignorato, e' servito SIGKILL. Il claude finto con lo scenario `deaf` fa lo stesso: ignora HUP e TERM.

O1 launch mette l'hook session-closed: kill-session di una sessione il cui claude ignora HUP e TERM → il claude si chiude
O2 senza l'hook l'orfano resta: `sessions --json` non lo elenca, la tabella lo segna «orfana»; `--reap-orphans` lo segna
   la prima volta e lo chiude passati sessions.orphan_grace_s
O3 launch di un nome chiude prima l'orfano con quel nome: la sessione nuova prende il nome, non «-2»
O4 mai la sessione corrente: `--reap-orphans --now` lanciato da dentro l'orfano lo lascia vivo
O5 non si tocca altro: la sessione con il suo pannello e un claude fuori da tmux restano vivi
"""
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
for d in (".claude/sessions", "ws/personali"):
    (home / d).mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it",
    "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "default_account": "personale",
    "session": {"claude_args": ["--dangerously-skip-permissions"], "startup_timeout_s": 25, "death_check_s": 1},
    "terminal": {"backend": "fake", "attach_wait_s": 2, "attach_retry_wait_s": 1},
    "registry": {"file": str(tmp / "registry.json")},
    "tabs": {"color_registry": str(tmp / "colors")},
    "sessions": {"max_sessions": 50, "orphan_grace_s": 3, "orphan_wait_s": 1},
}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
scenfile = tmp / "scenario"
runfile = tmp / "run"
HOOK = "session-closed[73]"


def env_base(tm, scenario="plain"):
    scenfile.write_text(scenario)
    env = {"FAKE_CLAUDE_SCENARIO_FILE": str(scenfile), "PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home),
           "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(FAKE),
           "FAKE_CLAUDE_RUN_FILE": str(runfile), "CM_PROC_SCAN_PIDS": "", "CM_TERMINAL_FAKE_LOG": str(tmp / "fake-terminal.log")}
    return env


def launch(tm, name, scenario="plain"):
    d = home / "ws" / "personali" / name
    return subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), str(d), "--create", "--no-window"], capture_output=True, text=True,
                          env=env_base(tm, scenario), timeout=400)


def sessions(tm, *args):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-sessions.py"), *args], capture_output=True, text=True,
                          env=env_base(tm, scenfile.read_text()), timeout=200)


def reg_pid(name):
    for f in (home / ".claude" / "sessions").glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except ValueError:
            continue
        if d.get("name") == name and alive(d.get("pid")):
            return d["pid"]
    return None


def alive(pid):
    try:
        return pid is not None and "\nState:\tZ" not in Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return False


def orphan(tm, name):
    """Una sessione `deaf` di nome NAME, poi kill-session senza l'hook: il suo claude resta senza pannello."""
    r = launch(tm, name, "deaf")
    pid = reg_pid(name)
    tm("set-hook", "-gu", HOOK)
    tm("kill-session", "-t", f"={name}")
    time.sleep(1)
    return pid, r


outsider = None
try:
    with T.PrivateTmux() as tm:
        r = launch(tm, "custode")   # tiene vivo il server tmux: senza sessioni tmux esce, e con lui l'hook
        custode = reg_pid("custode")
        T.check("setup: the keeper session is up", r.returncode == 0 and custode, r.stdout + r.stderr)

        # O1
        r = launch(tm, "alfa", "deaf")
        alfa = reg_pid("alfa")
        hooks = tm("show-hooks", "-g").stdout
        T.check("O1 orphan_claude_closed: launch sets the session-closed hook", "session-closed[73]" in hooks, hooks)
        tm("kill-session", "-t", "=alfa")
        T.check("O1 orphan_claude_closed: kill-session of a claude that ignores HUP and TERM → the claude is closed",
                alfa and T.wait_until(lambda: not alive(alfa), timeout=20), f"pid {alfa} alive={alive(alfa)}")

        # O2
        beta, _ = orphan(tm, "beta")
        T.check("O2 setup: without the hook the claude survives its tmux (the bug of 28/10)", alive(beta), f"pid {beta}")
        js = sessions(tm, "--json", "--no-screen")
        rows = json.loads(js.stdout or "[]")
        T.check("O2 orphan_claude_closed: sessions --json does not list the orphan as live",
                beta not in [x.get("pid") for x in rows] and custode in [x.get("pid") for x in rows], js.stdout[:600] + js.stderr)
        tab = sessions(tm, "--no-screen")
        T.check("O2 the table marks it «orfana», with name and pid", "orfana" in tab.stdout and "beta" in tab.stdout and str(beta) in tab.stdout,
                tab.stdout + tab.stderr)
        r1 = sessions(tm, "--reap-orphans")
        T.check("O2 first --reap-orphans marks it and leaves it alive (grace)", alive(beta) and "beta" in r1.stdout, r1.stdout + r1.stderr)
        time.sleep(3.5)
        r2 = sessions(tm, "--reap-orphans")
        T.check("O2 orphan_claude_closed: after sessions.orphan_grace_s --reap-orphans closes it (HUP, TERM, KILL)",
                T.wait_until(lambda: not alive(beta), timeout=10) and "beta" in r2.stdout, r2.stdout + r2.stderr)

        # O3
        gamma, _ = orphan(tm, "gamma")
        T.check("O3 setup: orphan gamma alive", alive(gamma), f"pid {gamma}")
        r = launch(tm, "gamma")
        T.check("O3 orphan_claude_closed: launch of the same name closes the orphan first and takes the name (not gamma-2)",
                r.returncode == 0 and "nome tmux: gamma\n" in r.stdout and not alive(gamma), f"alive={alive(gamma)}\n" + r.stdout + r.stderr)

        # O4
        selfie, _ = orphan(tm, "selfie")
        envs = env_base(tm, scenfile.read_text())
        exports = " ".join(f"{k}='{v}'" for k, v in envs.items())
        out = Path(f"{runfile}.selfie.out")
        Path(f"{runfile}.selfie").write_text(f"env {exports} {sys.executable} {T.SCRIPTS / 'cm-sessions.py'} --reap-orphans --now\n")
        T.wait_until(out.exists, timeout=20)
        time.sleep(1)
        T.check("O4 never the current session: --reap-orphans --now run from inside the orphan leaves it alive",
                out.exists() and alive(selfie), (out.read_text() if out.exists() else "no output") + f" alive={alive(selfie)}")
        r = sessions(tm, "--reap-orphans", "--now")
        T.check("O4 from outside, --now closes it", T.wait_until(lambda: not alive(selfie), timeout=10), r.stdout + r.stderr)

        # O5
        oenv = {k: v for k, v in env_base(tm, "deaf").items()}
        oenv.pop("TMUX", None)
        (tmp / "fuori").mkdir()
        outsider = subprocess.Popen([str(FAKE), "--remote-control", "fuori", "-n", "fuori"], cwd=tmp / "fuori", env=oenv,
                                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        T.wait_until(lambda: reg_pid("fuori"), timeout=10)
        r = sessions(tm, "--reap-orphans", "--now")
        time.sleep(1)
        T.check("O5 a claude outside tmux and the keeper in its pane are not touched",
                alive(outsider.pid) and alive(custode) and reg_pid("fuori"), r.stdout + r.stderr)
finally:
    if outsider:
        outsider.kill()
    T.rm(str(tmp))
T.finish()
