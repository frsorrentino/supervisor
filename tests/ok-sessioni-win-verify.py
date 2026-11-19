#!/usr/bin/env python3
"""Un ok che la persona scrive a una sessione su win arriva nel registro (19/11/2026, supervisor-ok-sessioni-win-19-11).

Il caso: win:supervisor-app ha ricevuto i «sì» della persona alla pausa di supervisor-app-pulizia-nomi-29-10, ma da li'
`task ok` non si puo' (il registro sta su questo PC): la pausa e' rimasta aperta. Ora la sessione su win scrive nella
risposta una riga «Ok della persona: <compito> [scelta «X»] «<le parole esatte della persona>»»; il giro degli host la
legge dalla copia della trascrizione (come «Per la master:») e la registra con le regole di `task ok --inbox`: il
messaggio deve esserci davvero, della persona, destinato a quella sessione, dell'ultima ora.

OW1 la persona ha scritto nella chat della sessione su win (riga user umana della trascrizione): la pausa si chiude, il
    record nella casella della sessione ha origine terminal, la master lo sa
OW2 la persona ha scritto dall'app (record del relay nella casella della sessione): vale quello, nessun record nuovo
OW3 una citazione che non c'e' → niente registrato, la master sa perche'
OW4 le parole di un'altra sessione (riga user non umana) o un messaggio di piu' di un'ora fa → niente
OW5 una volta sola: il giro dopo non la registra di nuovo
OW6 le righe vecchie (scritte prima che il canale si accendesse) non partono
"""
import datetime
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
proj = tmp / "proj"
proj.mkdir()
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
state_dir = tmp / "state"
state_dir.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state_dir), "accounts": {"personal": {"config_dir": str(home / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"},
                           "relay": {"dir": str(tmp / "relay")}, "registry": {"file": str(state_dir / "sessions.json")},
                           "hosts": {"win": {"transport": "ssh", "ssh": "win"}},
                           "tasks": {"commitments_prompt": False}}))
talk_log = tmp / "talk.log"
fake_cm = tmp / "fake-cm"
fake_cm.write_text(f'#!/bin/sh\necho "$*" >> {talk_log}\nexit 0\n')
fake_cm.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA", "CM_RELAY_CM", "CM_HOOK_TMUX_NAME")}
ENV.update({"CM_TASKS_DB": str(tmp / "tasks.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home),
            "CM_TASK_TALK_BIN": str(fake_cm), "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CM_PROC_SCAN_PIDS": "", "CM_INBOX_WAIT_S": "1",
            "CM_HOSTS_TALK_BIN": str(fake_cm)})
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")


def cm(*args, stdin=None):
    p = subprocess.run([CLI, "task", *args], input=stdin, capture_output=True, text=True, env=ENV, timeout=60)
    return p.returncode, p.stdout, p.stderr


def add(tid):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"unit {tid}", "plan": None,
                                       "where": {"project": str(proj), "session": "win:supervisor-app", "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open",
                                       "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal",
                                       "requested_by": "test"}}
    cm("add", "-", stdin=json.dumps(c))
    cm("pause", tid, "--reason", "serve il tuo ok sulla pulizia dei nomi", "--waits", "person")


def paused(tid):
    return bool(json.loads(cm("show", tid, "--json")[1]).get("pause_open") or [p for p in json.loads(cm("show", tid, "--json")[1]).get("pauses") or [] if not p.get("resumed_at")])


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


spec = importlib.util.spec_from_file_location("cm_hosts_ow", T.SCRIPTS / "cm-hosts.py")
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
spec = importlib.util.spec_from_file_location("cm_inbox_ow", T.SCRIPTS / "cm-inbox.py")
IB = importlib.util.module_from_spec(spec)
spec.loader.exec_module(IB)

SID = "S-WIN-1"
mirror = H.mirror_path("win", SID)
mirror.parent.mkdir(parents=True, exist_ok=True)
SESS = [{"entry": {"sessionId": SID, "name": "win-supervisor-app"}}]


def line(**d):
    with open(mirror, "a") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")


def user(t, text, **kw):
    line(type="user", timestamp=iso(t), sessionId=SID, message={"role": "user", "content": text}, **kw)


def assistant(text):
    line(type="assistant", timestamp=iso(time.time()), sessionId=SID, uuid=f"u{time.time_ns()}",
         message={"role": "assistant", "content": [{"type": "text", "text": text}]})


def talks():
    return talk_log.read_text() if talk_log.exists() else ""


now = time.time()
for t in ("t1", "t2", "t3", "t4", "t5"):
    add(t)
# OW6: una riga di prima che il canale si accendesse
user(now - 120, "sì, chiudi pure t5")
assistant("Ok della persona: t5 «sì, chiudi pure t5»")
H.forward_to_master("win", SESS)   # il primo giro accende il canale: niente parte
T.check("OW6 lines written before the channel was on do not go (the first round only takes the offsets)", paused("t5"), talks())

# ------------------------------------------------------------------ OW1
user(time.time() - 60, "sì, chiudi pure la pulizia dei nomi")
time.sleep(1.1)
assistant("Fatto il riepilogo.\n\nOk della persona: t1 «sì, chiudi pure la pulizia dei nomi»\n\nEsito: in attesa del registro")
H.forward_to_master("win", SESS)
recs = [r for r in IB.messages("win-supervisor-app", include_all=True) if r.get("text") == "sì, chiudi pure la pulizia dei nomi"]
T.check("OW1 the person wrote in the win session's chat: the pause closes, the record in that session's box has origin terminal",
        not paused("t1") and len(recs) == 1 and recs[0]["origin"] == "terminal", str(recs) + talks()[-600:])
T.check("OW1 the master is told it was recorded", "talk master" in talks() and "t1" in talks().split("talk master")[-1], talks()[-400:])

# ------------------------------------------------------------------ OW2
rel = IB.put("win-supervisor-app", "Dall'utente via telefono. ok per t2, procedi", "supervisor", origin="relay")
time.sleep(1.1)
assistant("Ok della persona: t2 «ok per t2, procedi»")
n_before = len(IB.messages("win-supervisor-app", include_all=True))
H.forward_to_master("win", SESS)
T.check("OW2 the person wrote from the app (the relay's record in the session's box): that one counts, no new record",
        not paused("t2") and len(IB.messages("win-supervisor-app", include_all=True)) == n_before, talks()[-400:])

# ------------------------------------------------------------------ OW3, OW4
talk_log.write_text("")
user(time.time() - 30, "ok anche t4", turnOrigin="peer")
user(time.time() - 4000, "vecchio sì per t4")
time.sleep(1.1)
assistant("Ok della persona: t3 «questo non l'ha mai scritto nessuno»\n\nOk della persona: t4 «ok anche t4»\n\nOk della persona: t4 «vecchio sì per t4»")
H.forward_to_master("win", SESS)
T.check("OW3 a quote that is not there: nothing recorded, the master learns why", paused("t3") and "t3" in talks() and "talk master" in talks(), talks()[-500:])
T.check("OW4 another session's words (a user line that is not human) or a message older than an hour: nothing recorded", paused("t4"), talks()[-500:])

# ------------------------------------------------------------------ OW5
talk_log.write_text("")
H.forward_to_master("win", SESS)
T.check("OW5 once only: the next round does not record it again", "talk master" not in talks(), talks())

T.finish()
