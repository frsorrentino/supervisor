#!/usr/bin/env python3
"""Un prompt in coda arriva nella casella anche se Claude Code annulla il suo UserPromptSubmit (19/11/2026,
supervisor-prompt-in-coda-19-11, D10).

Caso vero: la trascrizione della master, 19/11 alle 09:38:41-47 ora locale. La persona scrive la scelta mentre la master
lavora; il prompt va in coda (queue-operation), parte a fine turno e i due hook UserPromptSubmit risultano
`hook_cancelled`. Il prompt non entra nella casella, e `task ok --inbox` non ha un record da citare.

PQ1 StopLate: la riga user umana del prompt annullato entra nella casella della sessione, origine «terminal», gia'
    consegnata, con l'ora della riga
PQ2 restano fuori: le righe di un'altra sessione (turnOrigin peer), le notifiche, i tool_result, i comandi «/», le righe
    piu' vecchie della finestra, il primo prompt di `launch --prompt` (l'impronta in CM_FIRST_PROMPT_SHA) e un prompt
    del telefono che ha gia' il suo record del relay
PQ3 un secondo giro non lo duplica
PQ4 task ok --inbox accetta il record recuperato (origine terminal, la sessione e' quella del compito)
PQ5 al prompt seguente (UserPromptSubmit) un prompt annullato prima entra anche senza Stop
"""
import datetime
import hashlib
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
ws = home / "ws"
for d in ("ws/personali/alfa", ".claude/sessions"):
    (home / d).mkdir(parents=True)
state = tmp / "state"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(tmp / "relay")},
}))
LAUNCH = "Dalla master: compito alfa, procedi."
BASE = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_HOOK_TMUX_NAME", "CM_FIRST_PROMPT_BY", "CM_FIRST_PROMPT_SHA")}
BASE.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db"),
             "CM_TASK_TALK_BIN": str(tmp / "no-talk"), "CM_INBOX_WAIT_S": "1", "CM_PROC_SCAN_PIDS": "",
             "CM_HOOK_TMUX_NAME": "master", "CM_FIRST_PROMPT_BY": "master", "CM_FIRST_PROMPT_SHA": hashlib.sha256(LAUNCH.encode()).hexdigest()[:16]})
now = time.time()
CHOICE = "Scelgo S1+S2+S3 ovunque: procedi con tutte le correzioni."
PHONE = "dal telefono: va bene, rilascia"


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(t, text, **kw):
    return {"type": "user", "timestamp": iso(t), "sessionId": "S-M", "message": {"role": "user", "content": text}, **kw}


def cancelled(t, cmd):
    return {"type": "attachment", "timestamp": iso(t), "sessionId": "S-M",
            "attachment": {"type": "hook_cancelled", "hookName": "UserPromptSubmit", "hookEvent": "UserPromptSubmit", "command": cmd}}


tr = tmp / "S-M.jsonl"
rows = [
    user(now - 4000, "un ok vecchio, fuori dalla finestra"),
    user(now - 300, LAUNCH),
    user(now - 200, "<cross-session-message from=\"x\">ok</cross-session-message>", turnOrigin="peer"),
    user(now - 190, "<task-notification>fatto</task-notification>", turnOrigin="task_notification"),
    user(now - 180, [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
    user(now - 170, "/model sonnet"),
    user(now - 160, PHONE),
    {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(now - 90), "sessionId": "S-M", "content": CHOICE},
    {"type": "queue-operation", "operation": "dequeue", "timestamp": iso(now - 84), "sessionId": "S-M"},
    user(now - 84, CHOICE),
    cancelled(now - 83, "bash cm-hook.py UserPromptSubmit"),
    cancelled(now - 83, "bash route-hint.py"),
]
tr.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")

os.environ.update(BASE)
spec = importlib.util.spec_from_file_location("cm_inbox_pq", T.SCRIPTS / "cm-inbox.py")
IB = importlib.util.module_from_spec(spec)
spec.loader.exec_module(IB)
ph = IB.put("master", PHONE, "relay", str(ws), origin="relay")   # il relay scrive il suo record prima di consegnare


def hook(ev, payload):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), ev], input=json.dumps(payload),
                          capture_output=True, text=True, env=BASE, timeout=60)


def recs():
    d = state / "inbox" / "master"
    return [json.loads(f.read_text()) for f in sorted(d.glob("*.json"))] if d.is_dir() else []


P = {"session_id": "S-M", "cwd": str(ws), "transcript_path": str(tr), "last_assistant_message": "fatto"}
h = hook("StopLate", P)
mine = [r for r in recs() if r.get("text") == CHOICE]
T.check("PQ1 StopLate: the prompt whose UserPromptSubmit was cancelled is in the session's inbox, origin terminal, delivered, at the line's time",
        h.returncode == 0 and len(mine) == 1 and mine[0].get("origin") == "terminal" and mine[0].get("status") == "delivered"
        and abs(float(mine[0].get("created") or 0) - (now - 84)) < 2, h.stderr[-500:] + json.dumps(recs(), ensure_ascii=False)[:800])
others = sorted(r.get("text") for r in recs() if r.get("text") != CHOICE)
T.check("PQ2 left out: another session's line, a notification, a tool_result, a «/» command, a line older than the window, the launch prompt; the phone's prompt keeps only its relay record",
        others == [PHONE], json.dumps(others, ensure_ascii=False))

hook("StopLate", P)
T.check("PQ3 a second run does not duplicate it", len([r for r in recs() if r.get("text") == CHOICE]) == 1, json.dumps(recs(), ensure_ascii=False)[:600])

spec = importlib.util.spec_from_file_location("cm_tasks_pq", T.SCRIPTS / "cm-tasks.py")
TK = importlib.util.module_from_spec(spec)
spec.loader.exec_module(TK)
TK._caller_session = lambda: "master"
why, ok = "", ""
try:
    ok = TK.inbox_approval_text("utente", mine[0]["id"] if mine else "000000")
except SystemExit as e:
    why = str(e)
T.check("PQ4 task ok --inbox accepts the recovered record (origin terminal, the task's session)", "origin terminal" in str(ok) and not why, why or str(ok))

# ------------------------------------------------------------------ PQ5
LATER = "seconda scelta, scritta mentre lavorava"
with open(tr, "a") as f:
    f.write(json.dumps(user(now - 20, LATER), ensure_ascii=False) + "\n")
    f.write(json.dumps(cancelled(now - 19, "bash cm-hook.py UserPromptSubmit")) + "\n")
h5 = hook("UserPromptSubmit", dict(P, prompt="e adesso un'altra cosa"))
T.check("PQ5 at the next prompt (UserPromptSubmit) an earlier cancelled prompt gets in too, with no Stop in between",
        h5.returncode == 0 and len([r for r in recs() if r.get("text") == LATER and r.get("origin") == "terminal"]) == 1,
        h5.stderr[-400:] + json.dumps([r.get("text") for r in recs()], ensure_ascii=False))

T.finish()
