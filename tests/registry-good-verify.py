#!/usr/bin/env python3
"""Verifica la fotografia «ultimo insieme buono» del registro (mandato dell'11/09/2026, dopo il riavvio
delle 09:51 in cui il restore rilanciò 1 sessione su 5): tmux privato, claude finto.

G1  registry con 5 sessioni vive → registro 5 e fotografia 5
G2  4 chiuse A MANO (kill-session, come chiudere le finestre) + riconciliazione → registro 1, fotografia
    ANCORA 5 (la guardia copre il registro dimagrito, non solo quello vuoto)
G3  una sessione nuova → la fotografia cresce (6); `close` esplicito → la toglie (5)
G4  SessionEnd con reason prompt_input_exit (/exit) → tolta dalla fotografia; reason other → resta
G5  «riavvio» (server tmux morto) → `restore --dry-run` propone TUTTE E 5, dicendo per ciascuna se viene
    dalla fotografia o dal registro, con la data di ciascuna fonte; `registry --good` la stampa
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
NAMES = ("alfa", "beta", "gamma", "delta", "master")
for d in (".claude/sessions",) + tuple(f"ws/{n}" for n in NAMES + ("epsilon", "zeta", "eta")):
    (home / d).mkdir(parents=True)
reg = tmp / "registry.json"
good = tmp / "good.json"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "registry": {"file": str(reg), "good_file": str(good)},
    "restore": {"last": "master"},
    "terminal": {"backend": "none"},
}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CM_PROC_SCAN_PIDS": ""}
    e.update(extra)
    return e


def run(script, *args, **extra):
    return subprocess.run([str(T.SCRIPTS / script)] + list(args), capture_output=True, text=True, env=env(**extra), timeout=60)


def hook(event, payload, **extra):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), event], input=json.dumps(payload), capture_output=True, text=True, env=env(**extra), timeout=60)


def start(name):
    tm("new-session", "-d", "-s", name, "-x", "100", "-y", "30", "-c", str(home / "ws" / name),
       f"env CLAUDE_CONFIG_DIR='{home}/.claude' FAKE_CLAUDE_SCENARIO=plain '{FAKE}' --dangerously-skip-permissions -n {name}")


def names(p):
    return sorted(s["nome"] for s in json.loads(p.read_text())["sessioni"]) if p.is_file() else None


with T.PrivateTmux() as tm:
    for n in NAMES:
        start(n)
    time.sleep(2)
    run("cm-registry.sh")
    T.check("G1 registry and snapshot both hold the 5", names(reg) == sorted(NAMES) and names(good) == sorted(NAMES), f"{names(reg)} {names(good)}")
    T.check("G1 every snapshot entry carries its conversation id (session_id, for `reopen` once the session is gone)", all(s.get("session_id") for s in json.loads(good.read_text())["sessioni"]), good.read_text()[:300])
    # G2: quattro finestre chiuse a mano, poi il cron
    for n in ("alfa", "beta", "gamma", "master"):
        tm("kill-session", "-t", f"={n}")
    time.sleep(0.5)
    run("cm-registry.sh")
    T.check("G2 registry shrinks to delta, snapshot keeps the 5", names(reg) == ["delta"] and names(good) == sorted(NAMES), f"{names(reg)} {names(good)}")
    # G3: una nuova cresce la fotografia; close esplicito la toglie
    start("epsilon"); time.sleep(2)
    run("cm-registry.sh")
    T.check("G3 a new session grows the snapshot (6)", names(good) == sorted(NAMES + ("epsilon",)), str(names(good)))
    r = run("cm-close.sh", "epsilon"); time.sleep(0.5)
    T.check("G3 explicit close removes it from the snapshot (5)", r.returncode == 0 and names(good) == sorted(NAMES), r.stdout + r.stderr + str(names(good)))
    # G4: /exit (SessionEnd prompt_input_exit) toglie; reason other (finestra chiusa, crash) no
    start("zeta"); start("eta"); time.sleep(2)
    run("cm-registry.sh")
    T.check("G4 zeta and eta in the snapshot", names(good) == sorted(NAMES + ("zeta", "eta")), str(names(good)))
    pane = tm("display-message", "-p", "-t", "zeta", "#{pane_id}").stdout.strip()
    hook("SessionEnd", {"session_id": "x", "cwd": str(home / "ws" / "zeta"), "reason": "prompt_input_exit"}, TMUX_PANE=pane)
    tm("kill-session", "-t", "=zeta")
    # l'hook stacca `registry --closed` in un processo a parte: sotto carico 1,5 s fissi non bastavano (la release
    # 0.4.11 si e' fermata qui il 16/09, e di nuovo il 17/09 con un carico di 13). Si aspetta l'esito, fino a 15 s.
    T.wait_until(lambda: names(good) == sorted(NAMES + ("eta",)), 15)
    T.check("G4 /exit removes zeta from the snapshot", names(good) == sorted(NAMES + ("eta",)), str(names(good)))
    pane = tm("display-message", "-p", "-t", "eta", "#{pane_id}").stdout.strip()
    hook("SessionEnd", {"session_id": "y", "cwd": str(home / "ws" / "eta"), "reason": "other"}, TMUX_PANE=pane)
    tm("kill-session", "-t", "=eta"); time.sleep(1.5)
    run("cm-registry.sh")
    T.check("G4 reason other keeps eta in the snapshot", names(good) == sorted(NAMES + ("eta",)), str(names(good)))
    # restore: solo le 5 del mattino (eta tolta a mano dalla fotografia per la prova)
    g = json.loads(good.read_text()) if good.is_file() else {"sessioni": []}; g["sessioni"] = [s for s in g["sessioni"] if s["nome"] != "eta"]; good.write_text(json.dumps(g))
    # G5: riavvio
    tm("kill-server"); time.sleep(1.5)
    upf = tmp / "uptime"; upf.write_text("0.5 0.0\n")   # la macchina e' appena ripartita: le voci sono tutte di prima
    r = run("cm-restore.sh", "--dry-run", CM_UPTIME_FILE=str(upf))
    out = r.stdout
    T.check("G5 restore proposes all 5", r.returncode == 0 and "(5)" in out and all(n in out for n in NAMES), out + r.stderr)
    T.check("G5 says which come from the snapshot (4) and which from the registry (1), each with a date", out.count("fotografia") >= 4 and out.count("registro") >= 1 and "delta" in [l.split()[0] for l in out.splitlines() if "registro" in l], out)
    T.check("G5 nothing launched (dry run)", tm("list-sessions").returncode != 0, tm("list-sessions").stdout)
    r = run("cm-registry.sh", "--good")
    T.check("G5 registry --good prints the snapshot", '"alfa"' in r.stdout and '"visto"' in r.stdout, r.stdout + r.stderr)
T.rm(tmp)
T.finish()
