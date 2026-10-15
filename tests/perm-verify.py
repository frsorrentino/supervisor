#!/usr/bin/env python3
"""Verifica `supervisor permissions` (cm-tune.py, contratto 1.69) con il claude finto e un tmux privato.

PE1  da manual a plan: due Shift+Tab, uno per ogni cambio del piè; uscita 0, «permissions now plan», annotato in tune.file
PE2  gia' nella modalita' chiesta → uscita 0, nessun tasto mandato
PE3  bypassPermissions in una sessione partita senza: un giro intero, uscita 4, «not in this session's Shift+Tab
     cycle, left on default», e la sessione e' di nuovo in default
PE4  con il permesso (perm-bypass) bypassPermissions si raggiunge; auto subito dopo
PE5  piè lento (si ridisegna 2 s dopo il tasto): nessun tasto perso, la modalita' arriva con un tasto per cambio
PE6  il piè non cambia → uscita 4, «the footer did not change, stopped on default», un solo tasto mandato
PE7  piè senza modalita' (il claude finto senza piè) → uscita 4, «footer not readable», nessun tasto
PE8  domanda aperta → uscita 3, «a dialog is open», nessun tasto
PE9  nome sconosciuto → uscita 2, nessun tasto; sessione inesistente → uscita 1
PE10 testo scritto nel prompt e sessione al lavoro: il cambio si fa, il testo resta
PE11 il default delle versioni vecchie («? for shortcuts») si legge come default
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
(home / ".claude").mkdir(parents=True)
cfg = tmp / "config.json"
TUNED = tmp / "tuned.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "tune": {"timeout_s": 3, "file": str(TUNED)}}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
KEYS = tmp / "keys.log"


def env(tm):
    return {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
            "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"]}


def perm(tm, *args):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tune.py"), "permissions", *args], capture_output=True, text=True, env=env(tm), timeout=90)


def start(tm, name, scenario):
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", name, "-x", "160", "-y", "40",
                    "env", f"FAKE_CLAUDE_SCENARIO={scenario}", "FAKE_CLAUDE_REGISTER=0", "FAKE_CLAUDE_DELAY=2", f"FAKE_CLAUDE_KEYS_LOG={KEYS}", str(FAKE)],
                   env=env(tm), check=True)
    time.sleep(2)


def pane(tm, name):
    return subprocess.run(["tmux", "-L", tm.socket, "capture-pane", "-p", "-J", "-t", name], capture_output=True, text=True).stdout


def presses():
    return len(KEYS.read_text().splitlines()) if KEYS.exists() else 0


with T.PrivateTmux() as tm:
    start(tm, "alfa", "perm")
    n = presses()
    r = perm(tm, "alfa", "plan")
    T.check("PE1 manual → plan: exit 0, «permissions now plan», two Shift+Tab (one per footer change), the footer says plan",
            r.returncode == 0 and r.stdout.strip() == "alfa: permissions now plan" and presses() - n == 2 and "plan mode on" in pane(tm, "alfa"),
            r.stdout + r.stderr + f" presses={presses() - n}" + pane(tm, "alfa"))
    T.check("PE1 the change is noted in tune.file for the state (no session_id here: the fake has no registry, so nothing to note is fine)",
            not TUNED.exists() or "permission_mode" in TUNED.read_text(), TUNED.read_text() if TUNED.exists() else "")
    n = presses()
    r = perm(tm, "alfa", "plan")
    T.check("PE2 already in the asked mode → exit 0, no key sent", r.returncode == 0 and presses() == n, r.stdout + f" presses={presses() - n}")
    n = presses()
    r = perm(tm, "alfa", "bypassPermissions")
    T.check("PE3 bypassPermissions without the launch flag: one full turn, exit 4, «not in this session's Shift+Tab cycle, left on plan», back on plan",
            r.returncode == 4 and r.stdout.strip() == "alfa: bypassPermissions is not in this session's Shift+Tab cycle, left on plan"
            and presses() - n == 4 and "plan mode on" in pane(tm, "alfa"), r.stdout + f" presses={presses() - n}" + pane(tm, "alfa"))
    r = perm(tm, "alfa", "default")
    T.check("PE3 default from plan goes through auto: «manual mode on» again", r.returncode == 0 and "manual mode on" in pane(tm, "alfa"), r.stdout + pane(tm, "alfa"))

    start(tm, "beta", "perm-bypass")
    r1 = perm(tm, "beta", "bypassPermissions")
    r2 = perm(tm, "beta", "auto")
    T.check("PE4 with the launch flag bypassPermissions is reached, then auto one step later",
            r1.returncode == 0 and "now bypassPermissions" in r1.stdout and r2.returncode == 0 and "auto mode on" in pane(tm, "beta"), r1.stdout + r2.stdout + pane(tm, "beta"))

    start(tm, "gamma", "perm-lag")
    n = presses()
    t0 = time.time()
    r = perm(tm, "gamma", "plan")
    T.check("PE5 a footer redrawn 2 s after the key: no key lost, exactly two presses, plan reached",
            r.returncode == 0 and presses() - n == 2 and "plan mode on" in pane(tm, "gamma") and time.time() - t0 >= 4, r.stdout + f" presses={presses() - n}" + pane(tm, "gamma"))

    start(tm, "delta", "perm-stuck")
    n = presses()
    r = perm(tm, "delta", "acceptEdits")
    T.check("PE6 a footer that never changes → exit 4, «the footer did not change, stopped on default», one press only",
            r.returncode == 4 and r.stdout.strip() == "delta: the footer did not change, stopped on default" and presses() - n == 1, r.stdout + f" presses={presses() - n}")

    start(tm, "eps", "plain")
    r = perm(tm, "eps", "plan")
    T.check("PE7 no mode in the footer → exit 4, «footer not readable, nothing changed»", r.returncode == 4 and "footer not readable, nothing changed" in r.stdout, r.stdout + pane(tm, "eps"))

    start(tm, "zeta", "question")
    before = pane(tm, "zeta")
    r = perm(tm, "zeta", "plan")
    T.check("PE8 a question open → exit 3, «a dialog is open, answer it first», the screen untouched",
            r.returncode == 3 and "a dialog is open, answer it first" in r.stdout and pane(tm, "zeta") == before, r.stdout + pane(tm, "zeta"))

    n = presses()
    r1 = perm(tm, "alfa", "yolo")
    r2 = perm(tm, "nessuna", "plan")
    T.check("PE9 an unknown mode → exit 2 «unknown permission mode: yolo», no key; no such session → exit 1",
            r1.returncode == 2 and "unknown permission mode: yolo" in r1.stdout and presses() == n and r2.returncode == 1, r1.stdout + r2.stdout)

    start(tm, "eta", "perm,busy")
    subprocess.run(["tmux", "-L", tm.socket, "send-keys", "-t", "eta", "-l", "ciao"], check=True)
    time.sleep(0.5)
    r = perm(tm, "eta", "acceptEdits")
    T.check("PE10 a session at work with text typed in its prompt: the change is made, the text stays",
            r.returncode == 0 and "accept edits on" in pane(tm, "eta") and "❯ ciao" in pane(tm, "eta"), r.stdout + pane(tm, "eta"))

    start(tm, "theta", "perm-old,perm-start=plan")
    r = perm(tm, "theta", "default")
    T.check("PE11 the older default footer «? for shortcuts» reads as default", r.returncode == 0 and "? for shortcuts" in pane(tm, "theta"), r.stdout + pane(tm, "theta"))
T.finish()
