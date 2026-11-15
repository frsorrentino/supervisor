#!/usr/bin/env python3
"""Lo sportello e i codici d'uscita di `claude remote-control` (Claude Code 2.1.325; supervisor-sportello-rc-13-11).

Dalla 2.1.325 `claude remote-control` esce con 4 quando si ferma da solo e con 69 quando non riesce a registrarsi.
Il claude finto qui sotto esce con i codici scritti in un file, uno per avvio; finiti i codici resta acceso.

SR1  uscita 4 → riparte da solo nella stessa sessione tmux (stessa sessione, tre avvii per due uscite con 4)
SR2  uscita 69, sessione della radice assente → niente ciclo, la sessione tmux si chiude, la procedura di login va nella
     casella della master
SR3  uscita 69, sessione della radice viva → needs-you sulla radice con la procedura di login (claude auth login, poi
     supervisor desk start) e il comando che la chiude; niente ciclo
SR4  un altro codice (1, 0) → come prima: la sessione si chiude, nessun riavvio, nessuna richiesta
SR5  tetto: piu' uscite con 4 ravvicinate del massimo → si ferma e lo dice con una needs-you
SR6  `desk status --check` esce 0 con lo sportello acceso e 1 da fermo; `needs-you --session` senza valore e' un errore
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
for d in (".claude/sessions", "ws"):
    (home / d).mkdir(parents=True)
state = tmp / "state"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "en", "state_dir": str(state),
    "workspace": {"root": str(home / "ws")},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "desk": {"enabled": True},
    "sessions": {"max_sessions": 50},
    "terminal": {"backend": "none"},
    "registry": {"file": str(tmp / "registry.json")},
    "tabs": {"color_registry": str(tmp / "colors")},
    "relay": {"enabled": False},
}))
fake = tmp / "fake"
fake.mkdir()
claude = fake / "claude"
claude.write_text("""#!/usr/bin/env bash
# claude finto: `remote-control` esce col primo codice di <cartella>/codes (e lo toglie); senza codici resta acceso
d="$(dirname "$0")"
echo "$*" >> "$d/args.log"
[ "${1:-}" = remote-control ] || exit 0
echo "Remote Control server (fake) https://claude.ai/code/session_01FAKEDESK"
code="$(head -n 1 "$d/codes" 2>/dev/null)"
if [ -n "$code" ]; then tail -n +2 "$d/codes" > "$d/codes.tmp"; mv "$d/codes.tmp" "$d/codes"; exit "$code"; fi
sleep 600
""")
claude.chmod(0o755)
argslog = fake / "args.log"
codes = fake / "codes"
procs = []


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(claude), "CM_PROC_SCAN_PIDS": "",
         "CM_DESK_RESTART_PAUSE_S": "0"}
    e.update(extra)
    return e


def run(script, *args, **extra):
    exe = [sys.executable, str(T.SCRIPTS / script)] if script.endswith(".py") else [str(T.SCRIPTS / script)]
    return subprocess.run(exe + list(args), capture_output=True, text=True, env=env(**extra), timeout=120)


def launches():
    return [x for x in (argslog.read_text().splitlines() if argslog.exists() else []) if x.startswith("remote-control ")]


def alive():
    return tm("has-session", "-t", "=sportello").returncode == 0


def desk_id():
    """L'id tmux della sessione dello sportello ($N): cambia se la sessione viene rifatta."""
    return next((x.split()[1] for x in tm("list-sessions", "-F", "#{session_name} #{session_id}").stdout.splitlines() if x.split()[0] == "sportello"), "")


def start(code_list, **extra):
    """Sportello fermo, i codici nel file, `desk start`: (risultato, avvii prima)."""
    run("cm-cloud.sh", "desk", "stop")
    codes.write_text("".join(f"{c}\n" for c in code_list))
    n0 = len(launches())
    return run("cm-cloud.sh", "desk", "start", "--no-window", **extra), n0


def needs():
    d = state / "needs-you"
    return {f.stem: json.loads(f.read_text()) for f in d.glob("*.json")} if d.is_dir() else {}


def root_row():
    """La sessione della radice viva: una voce di registro su un processo `sleep`, con la sessione tmux «master»."""
    tm("new-session", "-d", "-s", "master", "-x", "100", "-y", "30", "bash", "--norc")
    p = subprocess.Popen(["sleep", "600"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    time.sleep(0.2)
    t = int(time.time() * 1000)
    (home / ".claude" / "sessions" / f"{p.pid}.json").write_text(json.dumps({
        "pid": p.pid, "name": "master", "cwd": str(home / "ws"), "status": "idle", "tmux": "master:@0.%0", "startedAt": t - 3600_000,
        "statusUpdatedAt": t, "updatedAt": t, "procStart": T.proc_start(p.pid), "sessionId": "sid-master"}))


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "seed", "bash", "--norc"], env=env(), check=True)

    # SR1: due uscite con 4, poi resta acceso
    r, n0 = start([4, 4])
    sid0 = desk_id()
    ok = T.wait_until(lambda: len(launches()) - n0 >= 3, timeout=20)
    time.sleep(1)
    sid1 = desk_id()
    T.check("SR1 exit 4 → claude remote-control starts again by itself in the same tmux session (three starts for two exits with 4)",
            r.returncode == 0 and ok and len(launches()) - n0 == 3 and alive() and sid0 and sid0 == sid1 and not needs(),
            f"{r.returncode} {r.stderr} launches={len(launches()) - n0} alive={alive()} {sid0} {sid1} {needs()}")
    T.check("SR1 every start has the same arguments: remote-control --name sportello --capacity 4 --permission-mode default",
            set(launches()[n0:]) == {"remote-control --name sportello --capacity 4 --permission-mode default"}, str(launches()[n0:]))
    r = run("cm-cloud.sh", "desk", "status", "--check")
    T.check("SR6 desk status --check exits 0 while the desk runs", r.returncode == 0 and "sportello" in r.stdout, f"{r.returncode} {r.stdout}{r.stderr}")
    run("cm-cloud.sh", "desk", "stop")
    r = run("cm-cloud.sh", "desk", "status", "--check")
    T.check("SR6 desk status --check exits 1 when the desk is stopped (plain status still 0)",
            r.returncode == 1 and run("cm-cloud.sh", "desk", "status").returncode == 0, f"{r.returncode} {r.stdout}{r.stderr}")

    # SR2: 69 senza la sessione della radice → la casella della master
    r, n0 = start([69, 4, 4])
    ok = T.wait_until(lambda: not alive(), timeout=20)
    time.sleep(1)
    box = " ".join(json.loads(f.read_text()).get("text", "") for f in (state / "inbox" / "master").glob("*.json")) if (state / "inbox" / "master").is_dir() else ""
    T.check("SR2 exit 69 with no root session → no loop (one start), the tmux session closes, the login steps go to the master's inbox",
            ok and len(launches()) - n0 == 1 and "claude auth login" in box and "desk start" in box and "69" in box and not needs(),
            f"alive={alive()} launches={len(launches()) - n0} box={box} {needs()} {r.stderr}")

    # SR3: 69 con la radice viva → needs-you sulla radice
    root_row()
    r, n0 = start([69, 4])
    ok = T.wait_until(lambda: "sid-master" in needs(), timeout=20)
    time.sleep(1)
    rec = needs().get("sid-master") or {}
    T.check("SR3 exit 69 with the root session alive → a needs-you on it with the login steps (claude auth login, then supervisor desk start), from the desk",
            ok and "claude auth login" in rec.get("reason", "") and "desk start" in rec.get("reason", "") and "69" in rec.get("reason", "")
            and rec.get("name") == "master" and rec.get("from") == "sportello" and len(rec.get("reason", "")) <= 200, str(rec))
    T.check("SR3 no loop after 69: one start, the tmux session is closed, the 4 that follows in the file is never read",
            len(launches()) - n0 == 1 and not alive() and codes.read_text().strip() == "4", f"launches={len(launches()) - n0} alive={alive()} codes={codes.read_text()!r}")
    until = rec.get("until") or ""
    u1 = subprocess.run(["bash", "-c", until], capture_output=True, text=True, env=env()) if until else None
    codes.write_text("")
    run("cm-cloud.sh", "desk", "start", "--no-window")
    u2 = subprocess.run(["bash", "-c", until], capture_output=True, text=True, env=env()) if until else None
    T.check("SR3 the request closes by itself: its `until` command fails with the desk stopped and passes once the desk runs again",
            bool(until) and u1.returncode != 0 and u2.returncode == 0, f"{until!r} {u1 and u1.returncode} {u2 and u2.returncode} {u2 and u2.stderr}")
    for f in (state / "needs-you").glob("*.json"):
        f.unlink()

    # SR4: gli altri codici come prima
    for code in (1, 0):
        r, n0 = start([code, 4])
        ok = T.wait_until(lambda: not alive(), timeout=20)
        time.sleep(1)
        T.check(f"SR4 exit {code} → as before: the tmux session closes, no restart, no request",
                ok and len(launches()) - n0 == 1 and not needs(), f"alive={alive()} launches={len(launches()) - n0} {needs()}")

    # SR5: il tetto dei riavvii ravvicinati
    r, n0 = start([4] * 8, CM_DESK_RESTART_MAX="2")
    ok = T.wait_until(lambda: "sid-master" in needs(), timeout=20)
    time.sleep(1)
    rec = needs().get("sid-master") or {}
    T.check("SR5 more close exits with 4 than the maximum (2) → three starts, then it stops and says so with a needs-you naming the desk",
            ok and len(launches()) - n0 == 3 and not alive() and "sportello" in rec.get("reason", "") and "desk start" in rec.get("reason", ""),
            f"launches={len(launches()) - n0} alive={alive()} {rec}")

    # SR6: needs-you --session
    r = run("cm-needsyou.py", "x", "--session")
    T.check("SR6 needs-you --session with no value is an error (exit 2)", r.returncode == 2 and "--session" in r.stderr, f"{r.returncode} {r.stderr}")
    run("cm-cloud.sh", "desk", "stop")

for p in procs:
    p.kill()
T.rm(str(tmp))
T.finish()
