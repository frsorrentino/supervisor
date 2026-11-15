#!/usr/bin/env python3
"""L'avviso su /logout nelle sessioni --bg (Claude Code 2.1.323; supervisor-logout-bg-13-11, contratto 1.146).

Dalla 2.1.323 un /logout dato in una sessione `claude --bg` scollega tutte le sessioni del PC e cancella i login MCP e i
segreti dei plugin. Misurato sulla 2.1.325 col payload vero (docs/prove/2026-11-13-logout-hook.md): l'hook
UserPromptSubmit non riceve /logout, quindi non si puo' rifiutare: lo si dice nella scheda della sessione bg.

LB1  `sessions --json`: una sessione col registro di `claude --bg` ("kind": "bg", voce vera della 2.1.325) ha bg true;
     una sessione in tmux ("kind": "interactive") no
LB2  `sessions`: la riga della sessione bg porta la nota, e sotto la tabella c'e' l'avviso (una volta); senza sessioni
     bg niente avviso
LB3  /state (build_session): bg true solo per la sessione bg viva; assente per le altre e per una sessione gone
LB4  `launch --bg` stampa l'avviso dopo l'avvio; un launch in tmux no
LB5  l'avviso dice cosa succede e cosa fare, in italiano e in inglese; la misura e' nei docs
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
for d in (".claude/sessions", "ws/alfa", "ws/beta", "ws/gamma"):
    (home / d).mkdir(parents=True)
cfg = tmp / "config.json"
BASE = {
    "language": "en", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws")},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "session": {"dialog_patterns": ["trust (this|the) folder"], "startup_timeout_s": 20, "death_check_s": 1},
    "sessions": {"max_sessions": 50},
    "terminal": {"backend": "none"},
    "registry": {"file": str(tmp / "registry.json")},
    "tabs": {"color_registry": str(tmp / "colors")},
    "relay": {"enabled": False},
}
cfg.write_text(json.dumps(BASE))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
procs = []


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(FAKE), "CM_PROC_SCAN_PIDS": ""}
    e.update(extra)
    return e


def run(script, *args, **extra):
    exe = [sys.executable, str(T.SCRIPTS / script)] if script.endswith(".py") else [str(T.SCRIPTS / script)]
    return subprocess.run(exe + list(args), capture_output=True, text=True, env=env(**extra), timeout=120)


def reg_row(name, cwd, **fields):
    """Una voce del registro di Claude Code su un processo `sleep` vivo."""
    p = subprocess.Popen(["sleep", "600"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    time.sleep(0.2)
    t = int(time.time() * 1000)
    d = {"pid": p.pid, "name": name, "cwd": cwd, "status": "idle", "startedAt": t - 3600_000, "statusUpdatedAt": t, "updatedAt": t,
         "procStart": T.proc_start(p.pid), "sessionId": f"sid-{name}", "version": "2.1.325"}
    d.update(fields)
    f = home / ".claude" / "sessions" / f"{p.pid}.json"
    f.write_text(json.dumps(d))
    return f


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), T.SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "seed", "bash", "--norc"], env=env(), check=True)
    tm("new-session", "-d", "-s", "alfa", "-x", "100", "-y", "30", "bash", "--norc")
    reg_row("alfa", str(home / "ws" / "alfa"), tmux="alfa:@0.%0", kind="interactive")

    r = run("cm-sessions.py")
    T.check("LB2 with no --bg session the table has no /logout warning", r.returncode == 0 and "alfa" in r.stdout and "/logout" not in r.stdout, r.stdout + r.stderr)

    # la voce vera di una sessione `claude --bg` della 2.1.325 (13/11/2026): kind bg, jobId, niente tmux
    fbg = reg_row("beta-bg", str(home / "ws" / "beta"), kind="bg", entrypoint="cli", jobId="8b45bfa5", peerProtocol=1, nameSource="peer",
                  messagingSocketPath="/tmp/cc-socks/81.sock")
    r = run("cm-sessions.py", "--json", "--no-screen")
    rows = {x.get("name"): x for x in (json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else [])}
    T.check("LB1 sessions --json: the session whose registry entry has kind «bg» carries bg true, the tmux one bg false",
            (rows.get("beta-bg") or {}).get("bg") is True and (rows.get("alfa") or {}).get("bg") is False, r.stdout[:600] + r.stderr)

    r = run("cm-sessions.py")
    line = next((x for x in r.stdout.splitlines() if "beta-bg" in x), "")
    alfa = next((x for x in r.stdout.splitlines() if x.split()[2:3] == ["alfa"]), "")
    T.check("LB2 sessions: the --bg session's row says «no /logout here», the tmux session's row does not",
            "--bg" in line and "/logout" in line and "/logout" not in alfa and alfa, f"{line!r} {alfa!r}")
    warn = [x for x in r.stdout.splitlines() if "2.1.323" in x]
    T.check("LB2 under the table, once: /logout in a --bg session signs out every session on this PC, MCP logins and plugins' secrets; what to do instead",
            len(warn) == 1 and "EVERY session" in warn[0] and "MCP" in warn[0] and "secrets" in warn[0] and "claude auth login" in warn[0] and "hooks" in warn[0], str(warn))

    # LB3: /state
    S = load("cm-relay-state")
    sb = S.build_session(dict(rows["beta-bg"]), {})
    sa = S.build_session(dict(rows["alfa"]), {})
    sg = S.build_session(dict(rows["beta-bg"], status="dead", visto_ts=int(time.time())), {})
    T.check("LB3 /state: bg true on the live --bg session only (absent on the tmux one and on a gone one)",
            sb.get("bg") is True and "bg" not in sa and sg.get("state") == "gone" and "bg" not in sg, f"{sb.get('bg')} {sa.get('bg')} {sg.get('state')} {sg.get('bg')}")

    # LB4: launch
    r = run("cm-launch.sh", str(home / "ws" / "gamma"), "--bg")
    T.check("LB4 launch --bg prints the /logout warning after the start",
            r.returncode == 0 and "2.1.323" in r.stdout and "EVERY session" in r.stdout and r.stdout.index("2.1.323") > r.stdout.index("claude attach"), r.stdout + r.stderr)
    r = run("cm-launch.sh", str(home / "ws" / "gamma"), "--no-window")
    T.check("LB4 a tmux launch does not print it", r.returncode == 0 and "/logout" not in r.stdout, r.stdout + r.stderr)

    # LB5: i messaggi e la misura
    M = {lang: json.loads((T.PLUGIN / "messages" / f"{lang}.json").read_text(encoding="utf-8")) for lang in ("it", "en")}
    ok = all(all(w in M[lang]["sessions.bg_logout"] for w in ("2.1.323", "/logout", "MCP", "claude auth login", "claude stop")) and "/logout" in M[lang]["sessions.bg"]
             for lang in M)
    T.check("LB5 the warning names the version, /logout, the MCP logins and the way to change account, in Italian and in English", ok,
            str({lang: M[lang].get("sessions.bg_logout") for lang in M}))
    doc = T.ROOT / "docs" / "prove" / "2026-11-13-logout-hook.md"
    text = doc.read_text(encoding="utf-8") if doc.exists() else ""
    T.check("LB5 the measure is in docs/prove/2026-11-13-logout-hook.md: the real payload of a normal prompt, and no payload for /logout",
            '"hook_event_name":"UserPromptSubmit"' in text and "/logout" in text and "2.1.325" in text and "--bg" in text, str(doc))

for p in procs:
    p.kill()
T.rm(str(tmp))
T.finish()
