#!/usr/bin/env python3
"""Prima di chiudere, il passaggio di consegne (19/11/2026, supervisor-chiudi-salva-19-11; contratto 1.155).

L'utente alle 15:10: «se chiudo da supervisor la sessione prima di exit dovrebbe controllare se occorre salvare stato,
eseguire e poi chiudere»; alle 15:16 i due tasti dell'app, «Salva e chiudi» e «Chiudi senza salvare».

CS1 op close con save true su una sessione ferma: la richiesta del passaggio di consegne, la chiusura armata «save» con la
    scadenza; lo stato pubblica closing {mode: save, at, deadline}
CS2 a fine di quel turno (un stop nel ledger) col passaggio di consegne scritto: chiusa; closed {handoff: true, file}
CS3 save true su una sessione al lavoro: aspetta (closing save), e la richiesta parte quando si ferma
CS4 il salvataggio scade senza fine turno: chiusa lo stesso; closed {handoff: false, reason «salvataggio scaduto»}
CS5 save false (o assente): chiusa subito come prima; closed without handoff (the field is absent)
CS6 `supervisor close` dal PC (cm-closesave save): la richiesta, l'attesa della fine turno, il record della chiusura con
    il passaggio di consegne; una sessione che non e' Claude (senza session_id) non riceve niente
CS7 close --subito non chiede niente; il relay chiude con --subito (il salvataggio lo ha gia' fatto lui)
CS8 il contratto 1.155 e' scritto in details/relay.md
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
alfa = ws / "personali" / "alfa"
alfa.mkdir(parents=True)
(home / ".claude").mkdir(parents=True)
state = tmp / "state"
state.mkdir()
rdir = tmp / "relay"
rdir.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master", "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(rdir), "duplicate_prompt_s": 0},
}))
calls, alive = tmp / "calls.log", tmp / "alive.json"
fake_cm = tmp / "supervisor"
fake_cm.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{calls}"
case "$1" in
  sessions) cat "{alive}" ;;
  registry) echo '{{"sessioni": []}}' ;;
  close) echo "chiusa: $2" ;;
  talk) echo "consegnato" ;;
  interrupt) echo "fermata" ;;
esac
""")
fake_cm.chmod(0o755)
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE"):
    os.environ.pop(k, None)
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_RELAY_CM": str(fake_cm),
                   "CM_CLOSESAVE_CM": str(fake_cm)})
IT = json.loads((T.ROOT / "supervisor/messages/it.json").read_text())


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_cs", T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RL = load("cm-relay")
RL.push_async = lambda *a, **k: None
RL.git_dirty = lambda cwd: (0, [])
HO = load("cm-handoff")
CS = load("cm-closesave")


def row(status="idle", waiting=False, sid="S-A"):
    return {"pid": 5, "name": "alfa", "tmux": "alfa", "cwd": str(alfa), "status": status, "waiting": waiting,
            "session_id": sid, "account": "personale", "attached": False, "started_at": 1789200000000}


def iso(t):
    return datetime.datetime.fromtimestamp(t).isoformat(timespec="seconds")   # come il ledger vero: ora locale


def stop_event(sid, t=None):
    with open(state / "ledger.jsonl", "a") as f:
        f.write(json.dumps({"ts": iso(t or time.time()), "event": "stop", "session_id": sid, "cwd": str(alfa)}) + "\n")


def handoff_written(t=None):
    f = alfa / "docs" / "handoff-2026-11-19.md"
    f.parent.mkdir(exist_ok=True)
    f.write_text("# Passaggio di consegne\n")
    p = HO.pointer(str(alfa))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"path": str(f), "at": int(t or time.time()), "cwd": str(alfa)}))
    return str(f)


def log():
    return calls.read_text().splitlines() if calls.exists() else []


def reset():
    calls.write_text("")
    for d in ("closed", "closing"):
        for f in (state / d).glob("*.json") if (state / d).is_dir() else []:
            f.unlink()
    RL.close_armed_path().unlink(missing_ok=True)
    (state / "ledger.jsonl").unlink(missing_ok=True)
    HO.pointer(str(alfa)).unlink(missing_ok=True)


def closed_rec():
    p = state / "closed" / "alfa.json"
    return json.loads(p.read_text()) if p.exists() else {}


def published_closed():
    """sessions[].closed come lo pubblica lo stato per la gone."""
    S = RL.S
    rec = closed_rec()
    out = S.build_session({"name": "alfa", "tmux": "alfa", "status": "dead", "cwd": str(alfa), "session_id": "S-A", "account": "personale"},
                          {"closed": {"alfa": rec} if rec else {}, "prefixes": [], "awaiting": []})
    return out.get("closed")


# ------------------------------------------------------------------ CS1, CS2
reset()
alive.write_text(json.dumps([row()]))
ok, text = RL.execute({"op": "close", "arg": "alfa", "save": True, "by": "phone"})[:2]
armed = RL.close_armed().get("alfa") or {}
closing = RL.closing_of([row()]).get("alfa") or {}
T.check("CS1 op close with save true on an idle session: the handoff request goes, the closing is armed «save» with its deadline",
        ok is True and any(c.startswith("talk alfa ") and IT["close.save_prompt"][:40] in c for c in log()) and armed.get("mode") == "save"
        and armed.get("deadline") and not any(c.startswith("close ") for c in log()), f"{ok} {text} {log()} {armed}")
T.check("CS1 the state publishes closing {mode: save, at, deadline}", closing.get("mode") == "save" and closing.get("deadline"), str(closing))
f = handoff_written(time.time() + 1)
stop_event("S-A", time.time() + 2)
RL.close_tick(time.time() + 5)
rec = closed_rec()
T.check("CS2 at the end of that turn with the handoff written: closed with --subito, the record says handoff true and the file",
        "close alfa --subito" in log() and rec.get("handoff") is True and rec.get("handoff_file") == f, str(log()) + str(rec))
pc = published_closed() or {}
T.check("CS2 the gone session publishes closed {handoff: true, file}", pc.get("handoff") is True and pc.get("file") == f and "reason" not in pc, str(pc))

# ------------------------------------------------------------------ CS3
reset()
alive.write_text(json.dumps([row("busy")]))
RL.execute({"op": "close", "arg": "alfa", "save": True, "by": "phone"})
a3 = RL.close_armed().get("alfa") or {}
c3 = RL.closing_of([row("busy")]).get("alfa") or {}
no_talk = not any(c.startswith("talk ") for c in log())
alive.write_text(json.dumps([row()]))
RL.close_tick()
a3b = RL.close_armed().get("alfa") or {}
T.check("CS3 save true on a working session: it waits (published as saving), and the request goes when it stops",
        a3.get("mode") == "save_wait" and c3.get("mode") == "save" and no_talk and a3b.get("mode") == "save"
        and any(c.startswith("talk alfa ") for c in log()), f"{a3} {c3} {a3b} {log()}")

# ------------------------------------------------------------------ CS4
reset()
alive.write_text(json.dumps([row()]))
RL.execute({"op": "close", "arg": "alfa", "save": True, "by": "phone"})
RL.close_tick(time.time() + RL.CLOSE_FORCE_S + 5)
rec4 = closed_rec()
pc4 = published_closed() or {}
T.check("CS4 the save expires with no end of turn: closed anyway; closed {handoff: false, reason «salvataggio scaduto»}",
        "close alfa --subito" in log() and not pc4.get("handoff") and pc4.get("reason") == IT["close.reason_expired"], str(rec4) + str(pc4))

# ------------------------------------------------------------------ CS5
reset()
alive.write_text(json.dumps([row()]))
ok5, t5 = RL.execute({"op": "close", "arg": "alfa", "save": False, "by": "phone"})[:2]
pc5 = published_closed() or {}
T.check("CS5 save false: closed now as before, no request; closed without handoff (the field is absent)",
        ok5 is True and "close alfa --subito" in log() and not any(c.startswith("talk ") for c in log())
        and "handoff" not in pc5 and "file" not in pc5, f"{t5} {log()} {pc5}")

# ------------------------------------------------------------------ CS6
reset()
alive.write_text(json.dumps([row()]))
os.environ["CM_CLOSE_SAVE_TIMEOUT_S"] = "20"


def answer_later():
    time.sleep(3)
    handoff_written(time.time())
    stop_event("S-A", time.time())


import threading  # noqa: E402
threading.Thread(target=answer_later, daemon=True).start()
r6 = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-closesave.py"), "save", "alfa", "--line"], capture_output=True, text=True,
                    env=dict(os.environ), timeout=60)
rec6 = closed_rec()
T.check("CS6 `supervisor close` from the PC: the request, the wait for the end of the turn, the closing record with the handoff; the line says so",
        r6.returncode == 0 and any(c.startswith("talk alfa ") for c in log()) and rec6.get("handoff") is True and rec6.get("by") == "pc"
        and IT["close.saved_handoff"].split("{")[1].split("}")[-1][:5] in r6.stdout, r6.stdout + r6.stderr + str(rec6))
reset()
alive.write_text(json.dumps([dict(row(), session_id="")]))
r6b = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-closesave.py"), "save", "alfa", "--line"], capture_output=True, text=True,
                     env=dict(os.environ), timeout=60)
T.check("CS6 a session that is not Claude (no session_id): nothing asked, nothing written", r6b.returncode == 0 and not r6b.stdout.strip()
        and not any(c.startswith("talk ") for c in log()) and not closed_rec(), r6b.stdout + str(log()))

# ------------------------------------------------------------------ CS7
src = (T.SCRIPTS / "cm-close.sh").read_text()
T.check("CS7 close --subito asks nothing (the save step is skipped), and the relay's close passes --subito",
        "--subito|--now) SUBITO=si" in src and '[ "$SUBITO" = si ]' in src and 'run_cm("close", row.get("tmux") or row.get("name"), "--subito")' in T.relay_src(), "")

# ------------------------------------------------------------------ CS8
md = (T.ROOT / "details" / "relay.md").read_text()
T.check("CS8 contract 1.155 written in details/relay.md (save, closing save/exit, closed handoff/file/reason)",
        "Contract 1.155" in md and "`save`" in md and "\"exit\"" in md and "handoff" in md.split("Contract 1.155", 1)[-1], "")

T.finish()
