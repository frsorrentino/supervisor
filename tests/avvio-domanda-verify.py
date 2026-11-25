#!/usr/bin/env python3
"""Un avvio senza mani fermo su una domanda per la persona apre «Serve te» (19/11/2026, supervisor-avvio-domanda-19-11; regola
n3; prova vera in sup-prova: con la radice nella home Claude Code chiede la fiducia a ogni avvio, e servizio d'avvio,
«Riapri» e launch dall'app restavano fermi senza che nessuno lo vedesse).

AD1 launch fermo sulla domanda di fiducia di una cartella fuori dalla radice (dialog_patterns vuoto, il default): nessun tasto premuto, e la richiesta (la domanda fino al «?»)
    needs-you della sessione col testo della domanda (<state>/needs-you/start-<nome>.json)
AD2 relay: la sessione ancora fuori registro (niente session_id) ha needs_you col testo della domanda, e «Serve te» la mostra
AD3 relay: quando la sessione e' partita (ha il session_id) la richiesta si chiude da sola, con la riga nel registro
AD4 hook SessionStart della sessione: la richiesta si chiude subito, senza aspettare il relay
AD5 «Fatto» dall'app (op attention_done needs_you) chiude anche una richiesta d'avvio
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
ws = home / "ws"
for d in (".claude/sessions", "ws/personali/alfa"):
    (home / d).mkdir(parents=True)
out = tmp / "fuori"   # 25/11/2026 (supervisor-fiducia-cartella-25-11): sotto la radice la fiducia la da' supervisor
out.mkdir()
state = tmp / "state"
rdir = tmp / "relay"
rdir.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "en", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "session": {"startup_timeout_s": 20, "death_check_s": 1},
    "relay": {"enabled": True, "dir": str(rdir), "host": "test"},
    "terminal": {"backend": "none"},
    "registry": {"file": str(tmp / "registry.json"), "good_file": str(tmp / "good.json")},
    "hooks": {"local_time": {"enabled": False}, "session_kernel": {"enabled": False}},
}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
QUESTION = "Is this a project you created or one you trust?"
alive = tmp / "alive.json"
fake_cm = tmp / "supervisor"
fake_cm.write_text(f"""#!/bin/sh
case "$1" in
  sessions) cat "{alive}" ;;
  registry) echo '{{"sessioni": []}}' ;;
  quota) echo '{{}}' ;;
  *) echo '[]' ;;
esac
""")
fake_cm.chmod(0o755)
ny = state / "needs-you" / "start-fuori.json"


def rec():
    try:
        return json.loads(ny.read_text())
    except (OSError, ValueError):
        return None


def row(sid):
    return {"pid": 10, "name": "", "tmux": "fuori", "cwd": str(ws), "status": "idle", "waiting": False, "session_id": sid,
            "account": "personale", "link": "", "attached": False, "started_at": 1789200000000}


with T.PrivateTmux() as tm:
    BASE = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_HOOK_TMUX_NAME", "CLAUDE_CONFIG_DIR")}
    BASE.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_PROC_SCAN_PIDS": "",
                 "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(FAKE), "FAKE_CLAUDE_SCENARIO": "trust",
                 "CM_RELAY_CM": str(fake_cm)})
    r = subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), str(out), "--no-window"], capture_output=True, text=True, env=BASE, timeout=120)
    scr = tm("capture-pane", "-p", "-t", "fuori").stdout
    got = rec()
    T.check("AD1 launch stopped on the trust question: no key pressed, a needs-you request with the question's text",
            r.returncode == 0 and "No, exit" in scr and got is not None and QUESTION in str(got.get("reason")) and str(got.get("question", "")).endswith("?") and got.get("session") == "fuori",
            f"rc {r.returncode} rec {got}\n{r.stdout[-400:]}{r.stderr[-300:]}")

    def push(sid):
        alive.write_text(json.dumps([row(sid)]))
        p = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-relay.py"), "push", "--dry-run"], capture_output=True, text=True, env=BASE, timeout=120)
        try:
            return json.loads(p.stdout), p
        except ValueError:
            return {}, p

    st, p = push("")
    m = next((s for s in st.get("sessions") or [] if s.get("name") == "fuori"), {})
    att = [a for a in st.get("attention") or [] if a.get("kind") == "needs_you" and a.get("session") == "fuori"]
    T.check("AD2 relay: the session not yet registered has needs_you with the question, and «Serve te» shows it",
            QUESTION in str((m.get("needs_you") or {}).get("reason")) and len(att) == 1, json.dumps(m)[:400] + p.stderr[-300:])
    st, p = push("S-1")
    m = next((s for s in st.get("sessions") or [] if s.get("name") == "fuori"), {})
    led = (state / "ledger.jsonl").read_text() if (state / "ledger.jsonl").is_file() else ""
    T.check("AD3 relay: once the session started (session_id) the request closes by itself, with a registry row",
            not m.get("needs_you") and not ny.exists() and '"needs-you-done"' in led and '"started"' in led, json.dumps(m)[:300] + led[-300:])

    # AD4: di nuovo ferma; il SessionStart della sessione la chiude subito
    w = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-needsyou.py"), "--starting", "fuori", QUESTION], capture_output=True, text=True,
                       env=BASE, timeout=60)
    made = ny.exists()
    h = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "SessionStart"], capture_output=True, text=True, timeout=60,
                       env=dict(BASE, CM_HOOK_TMUX_NAME="fuori"),
                       input=json.dumps({"session_id": "S-2", "cwd": str(out), "source": "startup", "hook_event_name": "SessionStart"}))
    T.check("AD4 hook SessionStart of the session: the request closes at once", made and not ny.exists(),
            f"made {made} rc {w.returncode} {w.stdout[-200:]}{w.stderr[-200:]} hook {h.returncode} {h.stderr[-200:]}")

    # AD5: «Fatto» dall'app
    subprocess.run([sys.executable, str(T.SCRIPTS / "cm-needsyou.py"), "--starting", "fuori", QUESTION], capture_output=True, text=True, env=BASE, timeout=60)
    alive.write_text(json.dumps([row("")]))
    code = ("import importlib.util,json,sys\n"
            f"spec=importlib.util.spec_from_file_location('rl',{str(T.SCRIPTS / 'cm-relay.py')!r})\n"
            "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "print(json.dumps(m.attention_done({'kind':'needs_you','ref':'fuori','by':'test'}, 'fuori')))\n")
    a = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=BASE, timeout=120)
    T.check("AD5 «Fatto» from the app (attention_done needs_you) also closes a start request", not ny.exists() and "true" in a.stdout.lower(),
            a.stdout[-300:] + a.stderr[-300:])
T.finish()
