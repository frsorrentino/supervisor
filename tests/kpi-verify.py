#!/usr/bin/env python3
"""cm-kpi.py: i numeri per le viste KPI dell'app (contratto 1.96, 29/10/2026), su un registro, una trascrizione e una
cartella del relay finti.

K1 kpi_tasks: aperti, che aspettano la persona, fermi, chiusi oggi; per compito eta', tentativi rossi su attempts_max
K2 kpi_checks_week: 7 giorni dal piu' vecchio, verdi e rossi per giorno; un controllo di 8 giorni fa non conta
K3 kpi_turns_today: i turni di oggi dalla trascrizione — un prompt apre il turno, i token una volta per messaggio, un
   errore dell'API e' fail, l'ultimo turno di una sessione al lavoro e' running; ieri e i tool_result non contano
K4 kpi_health: latenza dell'ultima ora (mediana, massimo, campioni; oltre l'ora e fuori misura fuori), sessioni di
   questo PC sul tetto, host con RAM e disco in percento
K5 kpi_suggestions_7d: accettati (con le scelte), discussi, rimandati, mai, scaduti dei 7 giorni
K6 kpi_waits: record_waits scrive inizio e fine di domanda, permesso e needs-you una volta sola; le attese di adesso
   e la mediana dei minuti per giorno dalle attese chiuse; la timeline le mostra come eventi wait
K7 la stessa attesa (sessione, tipo, since) sparita per una push e tornata: un inizio solo, la fine falsa non conta
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"personal": {"config_dir": str(tmp / ".claude")}},
                           "default_account": "personal", "sessions": {"max_sessions": 5}}))
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(tmp), "CM_HOME": str(tmp)})
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("cm_kpi", T.SCRIPTS / "cm-kpi.py")
K = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(K)

NOW = int(datetime.now().replace(hour=15, minute=0, second=0, microsecond=0).timestamp())
D = 86400
TODAY = datetime.fromtimestamp(NOW).strftime("%Y-%m-%d")

# ------------------------------------------------------------------ K1, K2
con = sqlite3.connect(":memory:")
con.executescript("CREATE TABLE tasks (id TEXT PRIMARY KEY, plan TEXT, project TEXT, session TEXT, title TEXT, lane TEXT, state TEXT, contract TEXT, created_at INTEGER, updated_at INTEGER);"
                  "CREATE TABLE results (n INTEGER PRIMARY KEY, task TEXT, attempt INTEGER, outcome TEXT, reason TEXT, proof TEXT, perimeter TEXT, at INTEGER, accepted INTEGER);")
for tid, st, upd, amax in (("a", "running", NOW - 100, 3), ("b", "approved", NOW - 100, 5), ("c", "done", NOW - 3600, 3), ("d", "done", NOW - 2 * D, 3)):
    con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)", (tid, "", "/p", "s", tid, "closed", st, json.dumps({"task": {"attempts_max": amax}}), NOW - D, upd))
for task, outcome, at in (("a", "red", NOW - 7200), ("a", "red", NOW - 3600), ("b", "green", NOW - D - 60), ("c", "green", NOW - 3500), ("x", "red", NOW - 8 * D)):
    con.execute("INSERT INTO results (task, attempt, outcome, reason, at, accepted) VALUES (?,?,?,?,?,1)", (task, 1, outcome, "r", at))
commits = [{"task": "a", "short": "Alpha", "state": "running", "age_s": 9000, "stalled": True, "waits": {"who": "master", "on": "session"}},
           {"task": "b", "short": "Beta", "state": "approved", "age_s": 600, "stalled": False, "waits": {"who": "maintainer", "on": "person"}}]
kt = K.kpi_tasks(NOW, con, commits)
T.check("K1 kpi_tasks: open 2, waiting for the person 1, stalled 1, closed today 1 (c, not d of two days ago)",
        (kt["open"], kt["waiting_you"], kt["stalled"], kt["closed_today"]) == (2, 1, 1, 1), json.dumps(kt))
T.check("K1 kpi_tasks: per task {task, short, state, age_s, attempts, attempts_max, stalled, who}; a has 2 red checks of 3",
        kt["tasks"][0] == {"task": "a", "short": "Alpha", "state": "running", "age_s": 9000, "attempts": 2, "attempts_max": 3, "stalled": True, "who": "master"}
        and kt["tasks"][1]["attempts"] == 0 and kt["tasks"][1]["attempts_max"] == 5, json.dumps(kt["tasks"]))
kc = K.kpi_checks_week(NOW, con)
T.check("K2 kpi_checks_week: seven days, oldest first, today with 1 green and 2 red, yesterday 1 green; 8 days ago left out",
        len(kc) == 7 and kc[-1] == {"date": TODAY, "green": 1, "red": 2} and kc[-2]["green"] == 1 and sum(d["red"] for d in kc) == 2, json.dumps(kc))

# ------------------------------------------------------------------ K3
def iso(t):
    return datetime.fromtimestamp(t).astimezone().isoformat()


tx = tmp / "t.jsonl"
lines = [
    {"type": "user", "timestamp": iso(NOW - D), "message": {"content": "ieri"}},
    {"type": "assistant", "timestamp": iso(NOW - D + 5), "message": {"id": "m0", "usage": {"input_tokens": 999, "output_tokens": 999}}},
    {"type": "user", "timestamp": iso(NOW - 3600), "message": {"content": "primo"}},
    {"type": "assistant", "timestamp": iso(NOW - 3590), "message": {"id": "m1", "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100, "output_tokens": 5, "cache_read_input_tokens": 1000}}},
    {"type": "assistant", "timestamp": iso(NOW - 3589), "message": {"id": "m1", "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100, "output_tokens": 5, "cache_read_input_tokens": 1000}}},
    {"type": "user", "timestamp": iso(NOW - 3580), "message": {"content": [{"type": "tool_result", "content": "x"}]}},
    {"type": "assistant", "timestamp": iso(NOW - 3500), "message": {"id": "m2", "usage": {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 50}}},
    {"type": "user", "timestamp": iso(NOW - 1800), "message": {"content": "secondo"}},
    {"type": "assistant", "timestamp": iso(NOW - 1790), "isApiErrorMessage": True, "message": {"id": "m3", "model": "<synthetic>", "content": "API Error"}},
    {"type": "user", "timestamp": iso(NOW - 600), "message": {"content": "terzo"}},
    {"type": "assistant", "timestamp": iso(NOW - 590), "message": {"id": "m4", "usage": {"input_tokens": 3, "output_tokens": 4}}},
]
tx.write_text("".join(json.dumps(x) + "\n" for x in lines))
tt = K.kpi_turns_today([("alfa", str(tx), "busy"), ("beta", str(tmp / "none.jsonl"), "idle")], NOW)
T.check("K3 kpi_turns_today: three turns today (yesterday's left out, a tool_result is not a prompt): ok, fail, running; tokens once per message id (115+3, then 7), cache reads apart; beta without turns is not listed",
        [x["session"] for x in tt] == ["alfa"] and [(t["outcome"], t["tokens"], t["cache_read"]) for t in tt[0]["turns"]] == [("ok", 118, 1050), ("fail", 0, 0), ("running", 7, 0)]
        and tt[0]["turns"][0]["start"] == NOW - 3600 and tt[0]["turns"][0]["end"] == NOW - 3500
        and all(list(t) == ["start", "end", "outcome", "tokens", "cache_read"] for t in tt[0]["turns"]), json.dumps(tt))

# ------------------------------------------------------------------ K4
rd = tmp / "relay"
rd.mkdir()
for at, s in ((NOW - 7000, 9.0), (NOW - 1000, 1.0), (NOW - 500, 3.0), (NOW - 100, 2.0)):
    K.record_latency(rd, at, s)
K.record_latency(rd, NOW - 50, 9999)
st = {"hosts_status": [{"name": "win", "up": True, "cpu": 0.04, "ram_used": 8_000, "ram_total": 16_000, "disk_free": 25, "disk_total": 100}]}
rows = [{"status": "busy"}, {"status": "idle", "host": "local"}, {"status": "idle", "host": "win"}, {"status": "dead"}]
kh = K.kpi_health(st, rows, NOW, rd)
T.check("K4 kpi_health: latency of the last hour (median 2, max 3, 3 samples; the one of 2 hours ago and one out of range left out), 2 sessions here of 5, win at 50% RAM and 75% disk",
        kh == {"latency": {"p50_s": 2.0, "max_s": 3.0, "n": 3}, "sessions_here": 2, "sessions_max": 5,
               "hosts": [{"name": "win", "up": True, "cpu": 0.04, "ram_pct": 50, "disk_pct": 75}]}, json.dumps(kh))

# ------------------------------------------------------------------ K5
hist = [{"action": a, "at": NOW - age} for a, age in (("accepted", 100), ("chosen", 200), ("discussed", 300), ("hidden", 400), ("never", 500), ("expired", 600), ("accepted", 8 * D))]
T.check("K5 kpi_suggestions_7d: accepted counts the choices, older than 7 days left out",
        K.kpi_suggestions_7d(NOW, hist) == {"accepted": 2, "discussed": 1, "later": 1, "never": 1, "expired": 1}, json.dumps(K.kpi_suggestions_7d(NOW, hist)))

# ------------------------------------------------------------------ K6
s1 = {"sessions": [{"name": "alfa", "state": "waiting", "since": NOW - 900, "question": {"asked_at": NOW - 900}},
                   {"name": "beta", "state": "waiting", "since": NOW - 300},
                   {"name": "gamma", "state": "idle", "needs_you": {"at": NOW - 60, "reason": "login"}}]}
e1 = K.record_waits(s1, NOW, rd)
e2 = K.record_waits(s1, NOW + 30, rd)
s2 = {"sessions": [{"name": "alfa", "state": "busy"}, {"name": "beta", "state": "waiting", "since": NOW - 300}, {"name": "gamma", "state": "idle"}]}
(rd / "waits-open.json").unlink()   # 29/10: senza il file delle aperte l'attesa gia' scritta non si riapre
e2b = K.record_waits(s1, NOW + 60, rd)
e3 = K.record_waits(s2, NOW + 600, rd)
T.check("K6 kpi_waits: record_waits opens question, permission and needs-you once (a second push writes nothing, nor one after waits-open.json is gone) and closes them when they go",
        [(e["event"], e["session"], e["kind"]) for e in e1] == [("start", "alfa", "question"), ("start", "beta", "permission"), ("start", "gamma", "needs_you")]
        and e2 == [] and e2b == [] and [(e["event"], e["session"], e["kind"], e["at"], e["since"]) for e in e3] == [("end", "alfa", "question", NOW + 600, NOW - 900), ("end", "gamma", "needs_you", NOW + 600, NOW - 60)],
        json.dumps([e1, e2, e3]))
kw = K.kpi_waits(s2, NOW + 600, rd)
T.check("K6 kpi_waits: now beta waits on a permission since its time; today's median of the closed waits is (25 + 11) / 2 = 18 minutes from 2 waits",
        kw["now"] == [{"session": "beta", "kind": "permission", "since": NOW - 300}] and kw["week"][-1] == {"date": TODAY, "median_min": 18.0, "n": 2}
        and len(kw["week"]) == 7 and kw["week"][0]["median_min"] is None, json.dumps(kw))
TL = importlib.util.spec_from_file_location("cm_timeline", T.SCRIPTS / "cm-timeline.py")
TLM = importlib.util.module_from_spec(TL)
TL.loader.exec_module(TLM)
we = TLM.wait_events(rd, "alfa", NOW - 3600)
T.check("K6 kpi_waits: the timeline gets the waits of a session as events of kind wait (start and end, with the kind in the text)",
        [(e["kind"], e["at"]) for e in we] == [("wait", NOW - 900), ("wait", NOW + 600)] and all("question" in e["text"] or "domanda" in e["text"] for e in we), json.dumps(we))
s3 = {"sessions": [{"name": "alfa", "state": "busy"}, {"name": "gamma", "state": "idle"}]}   # una push che per un giro non vede beta
e4 = K.record_waits(s3, NOW + 700, rd)
e5 = K.record_waits(s2, NOW + 800, rd)
kw7 = K.kpi_waits(s2, NOW + 800, rd)
we7 = TLM.wait_events(rd, "beta", NOW - 3600)
T.check("K7 kpi_waits (29/10, the R27 double of WSL): a wait gone for one push and back with the same since is the same wait: the timeline shows one start, the median does not count the false end",
        [(e["event"], e["session"]) for e in e4] == [("end", "beta")] and [(e["event"], e["session"], e["since"]) for e in e5] == [("start", "beta", NOW - 300)]
        and [(e["kind"], e["at"]) for e in we7] == [("wait", NOW - 300)] and kw7["week"][-1] == {"date": TODAY, "median_min": 18.0, "n": 2}, json.dumps([e4, e5, we7, kw7["week"][-1]]))
full = K.collect(NOW, st, rows, [("alfa", str(tx), "busy")], rd, con, commits, hist)
T.check("K1-K6 collect: every block under its key, at in front",
        list(full) == ["at", "kpi_tasks", "kpi_checks_week", "kpi_turns_today", "kpi_health", "kpi_suggestions_7d", "kpi_waits"] and all(full[k] is not None for k in full), json.dumps(full)[:300])
T.rm(str(tmp))
T.finish()
