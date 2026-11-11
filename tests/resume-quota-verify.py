#!/usr/bin/env python3
"""Verifica cm-resume-quota.py: l'avviso di Claude Code 2.1.304 sul costo della ripresa (testo dal binario 2.1.305).

RQ1 parse: trigger resume (numerato) e idle (senza numeri), percentuale, «all» = 100, cursore, nessun falso positivo
RQ2 claude finto, cursore gia' su «Start a new conversation»: solo Invio, poi il rimando
RQ3 windowless = needs_you: nessun tasto, il file di needs-you con il motivo, la casella della master
RQ4 trigger idle senza finestra: niente scelta automatica (il messaggio non inviato resta), needs-you
RQ5 windowless = off: nessun tasto, nessun needs-you, la nota lo dice
RQ6 sessions: l'avviso conta come «aspetta una risposta» anche senza numeri e senza «Enter to select»
RQ7 nessun dialogo entro --wait: nessuna nota, nessun tasto
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
(home / ".claude" / "sessions").mkdir(parents=True)
ws = home / "ws"
(ws / "alfa").mkdir(parents=True)
state = tmp / "state"
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


def write_cfg(mode):
    cfg = tmp / f"config-{mode}.json"
    cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "workspace": {"root": str(ws), "root_session_name": "master"},
                               "accounts": {"personale": {"config_dir": str(home / ".claude")}}, "default_account": "personale",
                               "sessions": {"max_sessions": 50}, "relay": {"dir": str(tmp / "relay")},
                               "resume_quota": {"windowless": mode, "watch_s": 6}}))
    return cfg


sys.argv = [sys.argv[0]]
os.environ["CC_SUPERVISOR_CONFIG"] = str(write_cfg("new"))
os.environ["HOME"] = os.environ["CM_HOME"] = str(home)
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("rq", T.SCRIPTS / "cm-resume-quota.py")
rq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rq)

NUM = """ Resume this conversation?

 This conversation has been inactive for 3h 12m and is 412k tokens long. Resuming it will use about
 38% of your 5-hour usage limit.

 ❯ 1. Resume
   2. Start a new conversation
      Claude can look this one up if you refer to it later

 Enter to confirm · Esc to resume
"""
IDLE = NUM.replace("❯ 1. Resume", "❯ Resume").replace("  2. Start", "  Start").replace("will use about\n 38% of", "will use all of")
p1, p2 = rq.parse(NUM), rq.parse(IDLE)
T.check("RQ1 numbered dialog: trigger resume, 38%, cursor on Resume, lookup offered",
        p1 == {"trigger": "resume", "pct": 38, "cursor": "resume", "lookup": True}, str(p1))
T.check("RQ1 unnumbered dialog: trigger idle, «all» = 100%", p2 and p2["trigger"] == "idle" and p2["pct"] == 100 and p2["cursor"] == "resume", str(p2))
p3 = rq.parse(NUM.replace("❯ 1. Resume", "  1. Resume").replace("  2. Start a new", "❯ 2. Start a new"))
T.check("RQ1 cursor on «Start a new conversation» read from the screen", p3 and p3["cursor"] == "new", str(p3))
T.check("RQ1 no false positive: an AskUserQuestion menu, a prompt quoting the title",
        rq.parse("❯ 1. Resume\n  2. Start a new conversation\nEnter to select") is None and rq.parse("❯ Resume this conversation? please") is None, "")


def env(tm, cfg, extra=None):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"],
         "CLAUDE_CONFIG_DIR": str(home / ".claude")}
    e.update(extra or {})
    return e


def start(tm, name, scenario, cfg):
    tm("new-session", "-d", "-s", name, "-x", "120", "-y", "40", "-c", str(ws / "alfa"),
       "env", f"FAKE_CLAUDE_SCENARIO={scenario}", f"CLAUDE_CONFIG_DIR={home / '.claude'}", f"HOME={home}", "FAKE_CLAUDE_RQ_DELAY=0.5",
       str(FAKE), "-n", name, env=env(tm, cfg))


def check(tm, name, cfg, *args):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-resume-quota.py"), "check", name, *args], capture_output=True, text=True,
                          env=env(tm, cfg), timeout=60)


def screen(tm, name):
    return tm("capture-pane", "-p", "-t", name).stdout


with T.PrivateTmux() as tm:
    cfg = write_cfg("new")
    start(tm, "rq2", "resume-quota-new", cfg)
    r = check(tm, "rq2", cfg, "--resume-id", "abc123", "--folder", str(ws / "alfa"))
    time.sleep(1)
    s = screen(tm, "rq2")
    T.check("RQ2 cursor already on «Start a new conversation»: chosen, pointer sent, note printed",
            r.returncode == 0 and "conversazione nuova" in r.stdout and "Resume this conversation?" not in s and "Rimando di supervisor" in s, r.stdout + r.stderr + s)
    T.check("RQ2 the pointer names the transcript of the previous conversation", "abc123.jsonl" in "".join(s.split()), s)

    cfg = write_cfg("needs_you")
    start(tm, "rq3", "resume-quota", cfg)
    r = check(tm, "rq3", cfg)
    s = screen(tm, "rq3")
    files = list((state / "needs-you").glob("*.json")) if (state / "needs-you").is_dir() else []
    reason = json.loads(files[0].read_text()) if files else {}
    T.check("RQ3 windowless = needs_you: no keys (dialog still open, cursor on Resume), needs-you written with the reason",
            r.returncode == 0 and "❯ 1. Resume" in s and reason.get("name") == "rq3" and "38%" in reason.get("reason", "") and "needs-you" in r.stdout,
            r.stdout + r.stderr + s + str(reason))
    for f in files:
        f.unlink()

    cfg = write_cfg("new")
    start(tm, "rq4", "resume-quota-idle", cfg)
    r = check(tm, "rq4", cfg)
    s = screen(tm, "rq4")
    files = list((state / "needs-you").glob("*.json"))
    T.check("RQ4 idle trigger without a window: no automatic choice, needs-you instead",
            r.returncode == 0 and "Resume this conversation?" in s and "❯ Resume" in s and len(files) == 1 and "needs-you" in r.stdout, r.stdout + r.stderr + s)
    for f in files:
        f.unlink()

    sessions_spec = importlib.util.spec_from_file_location("sess", T.SCRIPTS / "cm-sessions.py")
    sess = importlib.util.module_from_spec(sessions_spec)
    sessions_spec.loader.exec_module(sess)
    T.check("RQ6 sessions: the warning (unnumbered too) counts as waiting for an answer",
            sess.waits_on_screen("", screen=IDLE) and sess.waits_on_screen("", screen=NUM) and not sess.waits_on_screen("", screen="❯ ciao\n? for shortcuts"), "")

    cfg = write_cfg("off")
    start(tm, "rq5", "resume-quota", cfg)
    r = check(tm, "rq5", cfg)
    s = screen(tm, "rq5")
    T.check("RQ5 windowless = off: no keys, no needs-you, the note says so",
            r.returncode == 0 and "❯ 1. Resume" in s and not list((state / "needs-you").glob("*.json")) and "off" in r.stdout, r.stdout + r.stderr + s)

    start(tm, "rq7", "plain", cfg)
    t0 = time.time()
    r = check(tm, "rq7", cfg, "--wait", "2")
    T.check("RQ7 no dialog within --wait: no note, exit 0, about --wait seconds", r.returncode == 0 and r.stdout.strip() == "" and time.time() - t0 < 8,
            r.stdout + r.stderr)

T.finish()
