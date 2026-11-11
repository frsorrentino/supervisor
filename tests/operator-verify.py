#!/usr/bin/env python3
"""Verifica next, park/unpark (con governo della RAM), cloud/follow/desk (argv), --teleport, stallo e quota in sessions.

N1  next: ordine — staccata+waiting prima, poi waiting attaccata, poi busy stallata (> stall_min), poi idle recente; (questa) esclusa; --json
N2  next senza candidati → «niente»
P1  park NOME: rifiuta attaccata e busy (senza --force); staccata idle → registra in parked.json e chiude
P2  park --idle-over: solo staccate idle da abbastanza; --dry-run non chiude
P3  park --ram-below con /proc/meminfo finto: sotto soglia parcheggia UNA sessione (la piu' idle); sopra soglia niente
P4  unpark: `launch <cwd> --resume <id> --account <acc>` (claude finto: argv) e la voce sparisce
C1  cloud: argv `--cloud "task"` nella cartella, account dedotto (CLAUDE_CONFIG_DIR per il secondo); follow: `-p msg --cloud id`
C2  launch --teleport <id>: argv con --teleport, senza -c
D0  desk spento di default: start rifiuta (desk.enabled)
D1  desk (abilitato): sessione tmux `sportello` con `remote-control --name sportello --capacity 4`; status; stop
S1  sessions: colonna «busy da N min» (stallo) e riga quota sotto soglia
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
for d in (".claude/sessions", ".claude-pixel/sessions", "ws/personali/alfa", "ws/personali/beta", "ws/personali/gamma", "ws/pro/delta", "quota"):
    (home / d).mkdir(parents=True)
import hashlib
qdir = home / "quota"
(qdir / f"quota-{hashlib.sha256(str(home / '.claude-pixel').encode()).hexdigest()[:8]}.json").write_text(json.dumps({"five_hour_used_pct": 10, "weekly_used_pct": 92, "weekly_resets_at": time.time() + 86400}))
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws")},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "folder_map": [{"path": str(home / "ws" / "pro"), "account": "professionale"}],
    "session": {"dialog_patterns": ["trust (this|the) folder", "Is this a project you", "Bypass Permissions mode"], "startup_timeout_s": 20, "death_check_s": 1},
    "sessions": {"max_sessions": 50, "stall_min": 20, "recent_min": 5},
    "quota": {"source": str(qdir), "warn_pct": 85},
    "terminal": {"backend": "none"},
    "registry": {"file": str(tmp / "registry.json")},
    "tabs": {"color_registry": str(tmp / "colors")},
}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
argslog = tmp / "args.log"
procs = []


def proc_start(pid):
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat[stat.rindex(")") + 2:].split()[19]


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(FAKE), "CM_PROC_SCAN_PIDS": "",
         "FAKE_CLAUDE_ARGS_LOG": str(argslog)}
    e.update(extra)
    return e


def run(script, *args, **extra):
    exe = [sys.executable, str(T.SCRIPTS / script)] if script.endswith(".py") else [str(T.SCRIPTS / script)]
    return subprocess.run(exe + list(args), capture_output=True, text=True, env=env(**extra), timeout=120)


def fake_row(name, cwd, status, ago_min, conf=None, tmux_name=None):
    """Voce di registro su un processo `sleep` vivo, con tmux nel nome."""
    e = dict(os.environ); e.pop("CLAUDE_CONFIG_DIR", None)
    if conf:
        e["CLAUDE_CONFIG_DIR"] = conf
    p = subprocess.Popen(["sleep", "600"], env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    time.sleep(0.2)
    reg = Path(conf or home / ".claude") / "sessions"
    t = int((time.time() - ago_min * 60) * 1000)
    reg.joinpath(f"{p.pid}.json").write_text(json.dumps({"pid": p.pid, "name": name, "cwd": cwd, "status": status, "tmux": f"{tmux_name or name}:@0.%0",
                                                          "startedAt": t - 3600_000, "statusUpdatedAt": t, "updatedAt": t, "procStart": proc_start(p.pid),
                                                          "sessionId": f"sid-{name}"}))
    return p.pid


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "seed", "bash", "--norc"], env=env(), check=True)
    for s in ("alfa", "beta", "gamma", "pix-delta", "eps"):
        tm("new-session", "-d", "-s", s, "-x", "100", "-y", "30", "bash", "--norc")
    fake_row("alfa", str(home / "ws" / "personali" / "alfa"), "waiting", 3)          # staccata, aspetta → 1
    fake_row("beta", str(home / "ws" / "personali" / "beta"), "busy", 45)             # stallo → 3
    fake_row("gamma", str(home / "ws" / "personali" / "gamma"), "idle", 2)            # idle recente → 4
    fake_row("pix-delta", str(home / "ws" / "pro" / "delta"), "idle", 300, conf=str(home / ".claude-pixel"))   # idle da 5 h
    fake_row("eps", str(home), "waiting", 1)                                          # waiting ma attaccata → 2
    import pty
    m, s_ = pty.openpty()
    client = subprocess.Popen(["tmux", "-L", tm.socket, "attach", "-t", "=eps"], stdin=s_, stdout=s_, stderr=s_, start_new_session=True)
    time.sleep(1)

    r = run("cm-next.py", "--json", "--all")
    rows = json.loads(r.stdout)
    order = [x["name"] for x in rows]
    T.check("N1 next order: abandoned, waiting-attached, stalled, recent idle, rest", order[:4] == ["alfa", "eps", "beta", "gamma"] and "pix-delta" in order, str(order) + r.stderr)
    T.check("N1 why lines", "nessuno" in rows[0]["why"] and "min" in rows[2]["why"], str([x["why"] for x in rows]))
    r = run("cm-next.py")
    T.check("N1 table starts with alfa", r.stdout.strip().startswith("1  alfa"), r.stdout + r.stderr)
    # S1: stallo e quota in sessions
    r = run("cm-sessions.py")
    T.check("S1 sessions shows the stall note and the quota warning", "45" in r.stdout and "professionale" in r.stdout and "92%" in r.stdout, r.stdout)

    # P1
    r = run("cm-park.py", "park", "eps")
    T.check("P1 attached → refused (exit 4)", r.returncode == 4 and tm("has-session", "-t", "=eps").returncode == 0, r.stderr)
    r = run("cm-park.py", "park", "beta")
    T.check("P1 busy → refused without --force", r.returncode == 4 and tm("has-session", "-t", "=beta").returncode == 0, r.stderr)
    r = run("cm-park.py", "park", "gamma")
    parked = json.loads((tmp / "state" / "parked.json").read_text())
    T.check("P1 detached idle → parked and closed", r.returncode == 0 and tm("has-session", "-t", "=gamma").returncode != 0 and parked[0]["name"] == "gamma" and parked[0]["session_id"] == "sid-gamma", r.stdout + r.stderr + str(parked))
    # P2
    r = run("cm-park.py", "park", "--idle-over", "2h", "--dry-run")
    T.check("P2 --idle-over 2h --dry-run lists pix-delta only", "pix-delta" in r.stdout and "alfa" not in r.stdout and tm("has-session", "-t", "=pix-delta").returncode == 0, r.stdout + r.stderr)
    # P3
    mem = tmp / "meminfo"
    mem.write_text("MemTotal:  6600000 kB\nMemAvailable:  400000 kB\n")
    r = run("cm-park.py", "park", "--ram-below", "800", CM_MEMINFO_FILE=str(mem))
    T.check("P3 RAM below → parks the longest-idle detached session (pix-delta)", r.returncode == 0 and tm("has-session", "-t", "=pix-delta").returncode != 0 and tm("has-session", "-t", "=alfa").returncode == 0, r.stdout + r.stderr)
    mem.write_text("MemTotal:  6600000 kB\nMemAvailable:  3000000 kB\n")
    r = run("cm-park.py", "park", "--ram-below", "800", CM_MEMINFO_FILE=str(mem))
    T.check("P3 RAM ok → nothing parked", r.returncode == 0 and "ok" in r.stdout.lower(), r.stdout + r.stderr)
    r = run("cm-park.py", "park", "--list")
    T.check("P3 --list shows gamma and pix-delta", "gamma" in r.stdout and "pix-delta" in r.stdout, r.stdout)
    # P4
    r = run("cm-park.py", "unpark", "pix-delta", "--dry-run")
    T.check("P4 unpark dry-run → launch --resume sid --account professionale", "--resume sid-pix-delta" in r.stdout and "--account professionale" in r.stdout, r.stdout + r.stderr)
    scen = tmp / "scen"; scen.write_text("plain")
    import re
    slug = re.sub(r"[^A-Za-z0-9]", "-", str((home / "ws" / "personali" / "gamma").resolve()))
    (home / ".claude" / "projects" / slug).mkdir(parents=True)
    (home / ".claude" / "projects" / slug / "sid-gamma.jsonl").write_text("{}\n")   # launch verifica il transcript (T10)
    r = run("cm-park.py", "unpark", "gamma", "--no-window", FAKE_CLAUDE_SCENARIO_FILE=str(scen))
    T.check("P4 unpark relaunches (fake claude with --resume) and forgets the entry", r.returncode == 0 and "--resume sid-gamma" in argslog.read_text() and "gamma" not in (tmp / "state" / "parked.json").read_text(), r.stdout + r.stderr)
    # C1
    r = run("cm-cloud.sh", "cloud", str(home / "ws" / "pro" / "delta"), "sistema i test")
    T.check("C1 cloud argv and account env", "--cloud sistema i test" in argslog.read_text().splitlines()[-1] and "professionale" in r.stdout, argslog.read_text().splitlines()[-1] + r.stdout + r.stderr)
    r = run("cm-cloud.sh", "follow", "session_01X", "continua")
    T.check("C1 follow argv", "-p continua --cloud session_01X" in argslog.read_text().splitlines()[-1], argslog.read_text().splitlines()[-1] + r.stderr)
    # C2
    r = run("cm-launch.sh", str(home / "ws" / "personali" / "alfa"), "--teleport", "session_01T", "--no-window", FAKE_CLAUDE_SCENARIO_FILE=str(scen))
    last = argslog.read_text().splitlines()[-1]
    T.check("C2 launch --teleport → argv --teleport id, no -c", r.returncode == 0 and "--teleport session_01T" in last and " -c" not in last, last + r.stderr)
    # D0: spento di default (10/09/2026): start rifiuta e dice la chiave; status/stop restano
    r = run("cm-cloud.sh", "desk", "start", "--no-window", FAKE_CLAUDE_SCENARIO_FILE=str(scen))
    T.check("D0 desk start refuses by default (desk.enabled false) naming the key", r.returncode == 3 and "desk.enabled" in r.stderr and tm("has-session", "-t", "=sportello").returncode != 0, r.stdout + r.stderr)
    import json as _json
    _c = _json.loads(cfg.read_text()); _c["desk"] = {"enabled": True}; cfg.write_text(_json.dumps(_c))
    # D1
    r = run("cm-cloud.sh", "desk", "start", "--no-window", FAKE_CLAUDE_SCENARIO_FILE=str(scen))
    last = argslog.read_text().splitlines()[-1]
    T.check("D1 desk start → tmux sportello with remote-control --name --capacity", r.returncode == 0 and tm("has-session", "-t", "=sportello").returncode == 0 and "remote-control --name sportello --capacity 4" in last, r.stdout + r.stderr + last)
    r = run("cm-cloud.sh", "desk", "status")
    T.check("D1 desk status running", "sportello" in r.stdout and r.returncode == 0, r.stdout)
    r = run("cm-cloud.sh", "desk", "stop")
    T.check("D1 desk stop", tm("has-session", "-t", "=sportello").returncode != 0, r.stdout + r.stderr)
    client.kill()

for p in procs:
    p.kill()
T.rm(str(tmp))
T.finish()
