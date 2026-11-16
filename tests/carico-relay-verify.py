#!/usr/bin/env python3
"""Il carico del relay sulla macchina (16/11/2026, supervisor-hook-lenti-16-11).

Misurato sul Chromebook: il demone al 27% di CPU di media, un thread al 62%; nel log 29 «transcript master ·
200:after=<id>» in dieci minuti dalla chat aperta nella web app, su una trascrizione di 37,9 MB; `task closed --json`
al 369%. Con la macchina satura (bash -c true a 580 ms) gli hook scadevano. Qui una trascrizione di 20 MB e un registro
con molti compiti chiusi; si conta quanto file si legge e quanti processi partono.

CR1  «after=<ultima voce>» ripetuto su una trascrizione che non cambia: la seconda volta non si legge niente (la stessa
     risposta); quando il file cresce si rilegge, e arriva la voce nuova
CR2  la catena delle conversazioni (che legge il ledger intero) non si calcola se la voce sta nella finestra
CR3  «after» con una voce che non c'e', su 20 MB: mai il file intero (al piu' la finestra di 16 MB), risposta = le
     ultime voci con reset; la volta dopo, a file cresciuto, una sola lettura della prima finestra (2 MB)
CR4  le altre letture non cambiano: «before» trova una voce lontana allargando la finestra, come prima
CR5  `task closed`: con 40 compiti chiusi in 40 cartelle nei giorni prima e 2 oggi, git parte al piu' per le 2 cartelle
     di oggi (prima per tutte); righe, prova e conteggi dei giorni come prima
CR6  la riga di log di un comando porta la CPU spesa («· cpu N s»)
"""
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
state = tmp / "state"
import re as _re  # noqa: E402
(home / "ws" / "alfa").mkdir(parents=True)
# la cartella delle trascrizioni come la chiama Claude Code: il percorso della cartella di lavoro, ogni carattere strano in «-»
proj = home / ".claude" / "projects" / _re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(home / "ws" / "alfa"))
for d in (proj, state, tmp / "relay", tmp / "bin"):
    d.mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "workspace": {"root": str(home / "ws")},
                           "accounts": {"p": {"config_dir": str(home / ".claude")}}, "default_account": "p",
                           "relay": {"enabled": True, "dir": str(tmp / "relay"), "firebase_url": "https://finto-default-rtdb.example"}}))
gitlog = tmp / "git.log"
real_git = subprocess.run(["bash", "-c", "command -v git"], capture_output=True, text=True).stdout.strip()
(tmp / "bin" / "git").write_text(f'#!/bin/sh\necho "$*" >> "{gitlog}"\nexec "{real_git}" "$@"\n')
(tmp / "bin" / "git").chmod(0o755)
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_RELAY_NO_PUSH": "1", "CM_TASKS_DB": str(tmp / "t.db"),
                   "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}"})
sp = importlib.util.spec_from_file_location("cm_relay_carico", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
RL.log_lines = []
RL.log = lambda line, now=None: RL.log_lines.append(line)

# una trascrizione di circa 20 MB: 9000 scambi
SID = "11111111-2222-4333-8444-555555555555"
tr = proj / f"{SID}.jsonl"
t0 = int(time.time()) - 9000 * 20


def line(i, role, text):
    uid = f"{i:08d}-0000-4000-8000-{'u' if role == 'user' else 'a'}00000000000".replace("u", "1").replace("a", "2")
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t0 + i * 20))
    msg = {"role": role, "content": text if role == "user" else [{"type": "text", "text": text}]}
    return json.dumps({"type": role, "uuid": uid, "sessionId": SID, "timestamp": ts, "cwd": str(home / "ws" / "alfa"), "message": msg})


with open(tr, "w") as f:
    for i in range(9000):
        f.write(line(2 * i, "user", f"domanda {i} " + "x" * 300) + "\n")
        f.write(line(2 * i + 1, "assistant", f"risposta {i} " + "y" * 1700) + "\n")
size0 = tr.stat().st_size

READS = []
real_entries = RL.core.transcript_entries


def counted(path, offset=0, *a, **k):
    READS.append((str(path), int(offset or 0)))
    return real_entries(path, offset, *a, **k)


RL.core.transcript_entries = counted
CHAIN = []
real_chain = RL.conversation_chain
RL.conversation_chain = lambda *a, **k: (CHAIN.append(a[0]), real_chain(*a, **k))[1]
row = {"tmux": "alfa", "name": "alfa", "session_id": SID, "cwd": str(home / "ws" / "alfa"), "status": "idle", "transcript": str(tr)}
RL.live_row = lambda tm: dict(row) if tm == "alfa" else None


def page(arg):
    ok, t = RL.transcript_page("alfa", "alfa", arg)
    return ok, (json.loads(t) if ok else t)


# CR1
ok, p0 = page("5")
last = p0["entries"][-1]["id"] if ok else ""
del READS[:], CHAIN[:]
ok1, a1 = page(f"200:after={last}")
n1 = len(READS)
ok2, a2 = page(f"200:after={last}")
T.check("CR1 «after=<the last entry>» again on a transcript that has not changed: nothing is read the second time, and the answer is the same (no entries, no reset)",
        size0 > 18_000_000 and ok1 and ok2 and n1 == 1 and len(READS) == 1 and a1 == a2 and a1["entries"] == [] and not a1.get("reset"),
        f"{size0} bytes, reads {READS}, {str(a1)[:120]}")
T.check("CR2 the chain of conversations (it reads the whole ledger) is not computed when the entry is in the window", CHAIN == [], str(CHAIN))
with open(tr, "a") as f:
    f.write(line(18000, "user", "una domanda nuova") + "\n")
del READS[:]
ok3, a3 = page(f"200:after={last}")
T.check("CR1 when the file grows it is read again, the first window only, and the new entry arrives",
        ok3 and len(READS) == 1 and READS[0][1] >= tr.stat().st_size - RL.TRANSCRIPT_WINDOWS[0] and [e["text"] for e in a3["entries"]] == ["una domanda nuova"],
        f"{READS} {str(a3)[:200]}")

# CR3
del READS[:], CHAIN[:]
ok4, a4 = page("50:after=non-esiste.0")
starts4 = [o for _, o in READS]
T.check("CR3 «after» with an entry that is not there, on 20 MB: never the whole file (the 16 MB window at most), and the answer is the last entries with reset",
        ok4 and a4.get("reset") is True and 1 <= len(a4["entries"]) <= 50 and 0 not in starts4 and min(starts4) >= tr.stat().st_size - RL.AFTER_WINDOW_MAX and CHAIN == [],
        f"starts {starts4}, size {tr.stat().st_size}, reset {a4.get('reset') if ok4 else a4}")
with open(tr, "a") as f:
    f.write(line(18001, "assistant", "una risposta nuova") + "\n")
del READS[:]
ok5, a5 = page("50:after=non-esiste.0")
T.check("CR3 the next time, with the file grown: one read of the first window (2 MB), still the last entries with reset",
        ok5 and a5.get("reset") is True and len(READS) == 1 and READS[0][1] >= tr.stat().st_size - RL.TRANSCRIPT_WINDOWS[0] and a5["entries"][-1]["text"] == "una risposta nuova",
        f"{READS} {a5.get('reset') if ok5 else a5}")

# CR4
ok6, far = RL.transcript_read(str(tr), 3, None, None)[0], None
whole = real_entries(str(tr), 0)
far = whole[40]["id"]
del READS[:]
ok7, b7 = page(f"3:before={far}")
T.check("CR4 the other reads are as before: «before» an entry far back widens the window down to the start of the file and finds it",
        ok7 and [e["id"] for e in b7["entries"]] == [e["id"] for e in whole[37:40]] and 0 in [o for _, o in READS], f"{[o for _, o in READS]} {str(b7)[:160]}")

# CR5: task closed
TK_ENV = dict(os.environ)
CLI = [sys.executable, str(T.SCRIPTS / "cm-tasks.py")]


def tk(*a, stdin=None):
    return subprocess.run(CLI + list(a), input=stdin, capture_output=True, text=True, env=TK_ENV, timeout=120)


def contract(tid, folder):
    return json.dumps({"schema_version": 1, "task": {"id": tid, "title": f"t {tid}", "plan": "p", "where": {"project": str(folder), "session": "alfa", "host": "local", "account": None},
                                                    "check": {"cmd": "true", "cwd": str(folder), "timeout_s": 30}, "perimeter": ["x"], "lane": "open", "depends_on": [],
                                                    "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
                                                    "outcome": {"expected": "x", "kind": "custom", "proof": "x"}}})


ids = []
for i in range(42):
    d = tmp / "p" / f"proj-{i:02d}"
    d.mkdir(parents=True)
    tid = f"cr-{i:02d}"
    assert tk("add", "-", stdin=contract(tid, d)).returncode == 0, tid
    tk("start", tid)
    r = tk("done", tid)
    ids.append(tid)
import sqlite3  # noqa: E402
con = sqlite3.connect(tmp / "t.db")
now_ = int(time.time())
for i, tid in enumerate(ids[:40]):   # quaranta chiusi nei tre giorni prima
    con.execute("UPDATE results SET at=? WHERE task=?", (now_ - 86400 * (1 + i % 3), tid))
con.commit()
closed_states = [r[0] for r in con.execute("SELECT state FROM tasks")]
con.close()
gitlog.write_text("")
r5 = tk("closed", "--json")
out5 = json.loads(r5.stdout) if r5.returncode == 0 and r5.stdout.strip().startswith("{") else {}
git_calls = [x for x in gitlog.read_text().splitlines() if "remote" in x]
T.check("CR5 task closed, 40 tasks closed in 40 folders on the days before and 2 today: git starts for today's 2 folders at most (for all 42 before)",
        closed_states.count("done") == 42 and len(git_calls) <= 2 and len(out5.get("rows") or []) == 2, f"{len(git_calls)} git calls, {len(out5.get('rows') or [])} rows, {r5.stderr[-200:]}")
T.check("CR5 the rows, their proof and the counts of the days before are as before",
        sorted(x["task"] for x in out5.get("rows") or []) == ["cr-40", "cr-41"] and all(x["proof"]["kind"] == "check" and x["outcome"] == "green" for x in out5["rows"])
        and [d["n"] for d in out5.get("days") or []] == [14, 13, 13] and sum(d["n"] for d in out5["days"]) == 40, str(out5.get("days")) + str(out5.get("rows"))[:200])

# CR6
RL.execute = lambda cmd: (True, "{}")
RL.rtdb = lambda *a, **k: None
RL.C.save_key(str(tmp / "relay"), RL.C.new_key())
del RL.log_lines[:]
RL.run_cmd({"id": "c-cpu", "op": "monitors", "device": "web", "by": "maintainer"})
ln = next((x for x in RL.log_lines if x.startswith("cmd c-cpu")), "")
import re  # noqa: E402
T.check("CR6 a command's log line carries the CPU it took («· cpu N s»), after queue and execution; relay stats still reads the line",
        bool(re.search(r"· fila [\d.,]+ s · esecuzione [\d.,]+ s · cpu [\d.,]+ s", ln)) and bool(RL.STATS_CMD.match("2026-11-16T10:00:00 " + ln)), ln)

T.rm(str(tmp))
T.finish()
