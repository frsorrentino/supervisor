#!/usr/bin/env python3
"""Il turno sospeso (20/11/2026, supervisor-turno-sospeso-19-11; approvato dal maintainer il 19/11 alle 22:23).

Caso vero: win:supervisor ha chiuso il turno delle 20:16 del 19/11 con «Il passaggio di consegne è aggiornato: puoi mandare
`/clear` quando vuoi. Se non lo fai, comincio `riassunto-fine` da qui» ed e' rimasta ferma fino alle 22:16. Il payload dello
Stop e' quello misurato da Claude Code 2.1.328 (tests/fixtures/stop-payload-2.1.328.json, catturato in una WSL di prova), col
messaggio vero al posto del suo.

TS1 hook Stop con il payload misurato e il messaggio vero, sessione con un compito in coda: la master riceve una riga
    (talk --no-wait) con la sessione, la frase e il compito, entro pochi secondi
TS2 lo stesso messaggio una seconda volta: nessuna riga in piu'
TS3 niente avviso per le attese vere e per chi non ha niente in coda: una carta wait-ok aperta della sessione, una richiesta
    needs-you aperta, una scelta fra opzioni, una riga «Tipo: aspetta», una sessione senza compiti in coda, la master stessa,
    un turno chiuso con «Fatto»
TS4 le frasi del contratto, con maiuscole e varianti: tutte riconosciute; «Procedo con la release» (un'azione, non una
    domanda) no
TS5 una sessione su win (giro di `hosts poll` sulla copia della trascrizione): la stessa riga alla master, una volta sola
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

REAL = (Path(__file__).resolve().parent / "fixtures" / "turno-sospeso-19-11.txt").read_text(encoding="utf-8")
MEASURED = json.loads((Path(__file__).resolve().parent / "fixtures" / "stop-payload-2.1.328.json").read_text(encoding="utf-8"))
tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
proj = tmp / "ws" / "lavoro"
proj.mkdir(parents=True)
state = tmp / "state"
state.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "accounts": {"personal": {"config_dir": str(home / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp / "ws"), "root_session_name": "master"},
                           "relay": {"enabled": False, "dir": str(tmp / "relay")}, "registry": {"file": str(state / "sessions.json")},
                           "hosts": {"win": {"transport": "ssh", "ssh": "win"}},
                           "hooks": {"local_time": {"enabled": False}, "session_kernel": {"enabled": False}},
                           "tasks": {"commitments_prompt": False}}))
talk_log = tmp / "talk.log"
fake = tmp / "fake-talk"
fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {talk_log}\nexit 0\n')
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_HOOK_TMUX_NAME", "CM_RELAY_CM")}
ENV.update({"CM_TASKS_DB": str(tmp / "tasks.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home),
            "CM_SOSPESO_TALK_BIN": str(fake), "CM_HOSTS_TALK_BIN": str(fake), "CM_TASK_TALK_BIN": str(fake), "CM_PROC_SCAN_PIDS": "",
            "CLAUDE_CONFIG_DIR": str(home / ".claude")})
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")


def task(*a, stdin=None):
    return subprocess.run([CLI, "task", *a], input=stdin, capture_output=True, text=True, env=ENV, timeout=60)


def add(tid, session, short=None):
    t = {"id": tid, "title": f"compito {tid}", "plan": None, "where": {"project": str(proj), "session": session, "host": "local", "account": None},
         "check": {"cmd": "true", "cwd": None, "timeout_s": 30}, "perimeter": ["x"], "lane": "open", "depends_on": [], "route": "session",
         "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}
    if short:
        t["short"] = short
    r = task("add", "-", stdin=json.dumps({"schema_version": 1, "task": t}))
    assert r.returncode == 0, r.stderr


def talks():
    return talk_log.read_text(encoding="utf-8") if talk_log.exists() else ""


def stop(name, text, sid="S-L"):
    p = dict(MEASURED, session_id=sid, cwd=str(proj), transcript_path=str(tmp / f"{sid}.jsonl"), last_assistant_message=text, stop_hook_active=False)
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "Stop"], input=json.dumps(p), capture_output=True, text=True,
                          env=dict(ENV, CM_HOOK_TMUX_NAME=name), timeout=60)


def wait_talk(n0, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if len(talks().splitlines()) > n0:
            return True
        time.sleep(0.3)
    return False


add("riassunto-fine-x", "lavoro", short="riassunto a fine sessione")
n0 = len(talks().splitlines())
h = stop("lavoro", REAL)
got = wait_talk(n0)
last = talks().splitlines()[-1] if talks() else ""
T.check("TS1 hook Stop with the measured payload and the real message, a task queued: one line to the master with the session, the phrase and the task",
        h.returncode == 0 and got and last.startswith("master ") and "«lavoro»" in last and "Se non lo fai, comincio riassunto-fine da qui" in last
        and "riassunto-fine-x" in last and last.rstrip().endswith("--no-wait"), f"rc {h.returncode} {h.stderr[-300:]}\n{talks()}")
n1 = len(talks().splitlines())
stop("lavoro", REAL)
time.sleep(4)
T.check("TS2 the same message a second time: no further line", len(talks().splitlines()) == n1, talks())

# ------------------------------------------------------------------ TS3
add("carta-y", "attesa")
task("start", "carta-y")
task("wait-ok", "carta-y", "--consiglio", "va bene", "--what", "push", "--where", "repo")
add("coda-z", "attesa")
add("coda-n", "bloccata")
(state / "needs-you").mkdir(exist_ok=True)
(state / "needs-you" / "S-NY.json").write_text(json.dumps({"reason": "serve il codice", "at": int(time.time())}))
add("coda-s", "scelte")
add("coda-t", "tipo")
add("coda-m", "master")
add("coda-f", "finito")
n2 = len(talks().splitlines())
cases = [("attesa", REAL, "S-A"), ("bloccata", REAL, "S-NY"), ("scelte", "Quale preferisci?\n1) la corta\n2) la lunga\nVuoi che continui con la 1?", "S-C"),
         ("tipo", "Serve il tuo ok sul push.\nTipo: aspetta\nVuoi che proceda?", "S-T"), ("vuota", REAL, "S-V"), ("master", REAL, "S-M"),
         ("finito", "Fatto: test verde, release pubblicata.", "S-F")]
for name, text, sid in cases:
    stop(name, text, sid)
time.sleep(6)
T.check("TS3 no notice for real waits nor without a queue: wait-ok card, needs-you, a choice, «Tipo: aspetta», no queued task, the master, «Fatto»",
        len(talks().splitlines()) == n2, talks().splitlines()[n2:])

# ------------------------------------------------------------------ TS4
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("cm_sospeso_ts", T.SCRIPTS / "cm-sospeso.py")
SO = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SO)
yes = ["Tutto pronto. Se no comincio dal recap.", "Se non lo fai, comincio la release.", "Ho finito. Altrimenti, parto con la release.",
       "Se non rispondi, parto alle 22.", "Puoi mandare `/clear` quando vuoi.", "Manda /clear quando vuoi.", "Vuoi che continui con il secondo?",
       "VUOI CHE PROCEDA?", "Il test è verde. Procedo?", "Dimmi se continuo con cerca-lavoro.", "Aspetto il tuo via per la release."]
no = ["Procedo con la release adesso.", "Fatto: test verde.", "Ho continuato con il secondo passo."]
miss = [t for t in yes if not SO.phrase_of(t)] + [t for t in no if SO.phrase_of(t)]
T.check("TS4 the contract's phrases, any case and variants, are all recognised; «Procedo con la release» (an action, not a question) is not",
        not miss, miss)

# ------------------------------------------------------------------ TS5
spec = importlib.util.spec_from_file_location("cm_hosts_ts", T.SCRIPTS / "cm-hosts.py")
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
add("coda-w", "win:supervisor", short="recap telegram")
SID = "S-WIN-9"
mirror = H.mirror_path("win", SID)
mirror.parent.mkdir(parents=True, exist_ok=True)
SESS = [{"entry": {"sessionId": SID, "name": "win-supervisor"}}]
H.forward_to_master("win", SESS)   # il primo giro accende il canale


def assistant(text):
    with open(mirror, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "uuid": f"u{time.time_ns()}", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                            "sessionId": SID, "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}, ensure_ascii=False) + "\n")


n3 = len(talks().splitlines())
assistant(REAL)
H.forward_to_master("win", SESS)
H.forward_to_master("win", SESS)
new = talks().splitlines()[n3:]
T.check("TS5 a session on win (hosts poll on the transcript copy): the same line to the master, once",
        len(new) == 1 and "win:supervisor" in new[0] and "coda-w" in new[0] and "Se non lo fai, comincio" in new[0], new)
T.finish()
