#!/usr/bin/env python3
"""Le parole della persona negli ok e nei loro avvisi (05/11/2026, supervisor-ok-testo-relay-05-11). Registro temporaneo.

OT1 un messaggio dal relay (telefono, web, orologio; anche per una sessione di win): si citano le parole della persona,
    senza «Dall'utente via telefono. Rispondi nella tua finestra…»; un messaggio dal terminale com'e'
OT2 task ok/approve --inbox e task standing add: il testo nel registro cita le parole della persona (il caso delle 19:56)
OT3 l'avviso alla sessione di un compito su win porta la carta (titolo, esito atteso, piano); a una sessione di qui no
OT4 «Approva» dall'app: il via alla sessione di win porta la carta; quello alla master e a una sessione di qui com'era
"""
import importlib.util
import json
import os
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(home / ".claude"), "accounts": {"personal": {"config_dir": str(home / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"}, "relay": {"dir": str(tmp / "relay")}}))
(tmp / "relay").mkdir()
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db"),
                   "CM_TASK_TALK_BIN": str(tmp / "no-talk"), "CM_IMPORTANCE_FILE": str(tmp / "imp.json")})
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE"):
    os.environ.pop(k, None)


def load(n):
    sp = importlib.util.spec_from_file_location(n.replace("-", "_") + "_t", T.SCRIPTS / f"{n}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


TK = load("cm-tasks")
IT = json.loads((T.ROOT / "supervisor/messages/it.json").read_text())
WORDS = "Applica la correzione (1+2) e poi applica anche il rinvio con Gemini giù"
WRAP = {d: IT[f"relay.prompt_prefix_{d}"] + " " + WORDS for d in ("phone", "web", "watch")}
REMOTE_WRAP = IT["relay.prompt_prefix_phone"].replace(IT["relay.prompt_handoff"], IT["relay.prompt_handoff_remote"]) + " " + WORDS

# OT1
got1 = [TK._user_words(WRAP[d]) for d in WRAP] + [TK._user_words(REMOTE_WRAP)]
T.check("OT1 a message from the relay (phone, web, watch, and the form for a session on win): only the person's words",
        got1 == [WORDS] * 4, json.dumps(got1, ensure_ascii=False)[:400])
T.check("OT1 a message typed at the terminal stays as it is", TK._user_words("ok, procedi con la B") == "ok, procedi con la B", "")

# OT2
REC = {"id": "e8b916", "to": "sess", "origin": "relay", "created": time.time() - 60, "text": WRAP["phone"]}
real_load = TK._load
TK._load = lambda n: types.SimpleNamespace(load=lambda rid: dict(REC) if rid == "e8b916" else None, resolve=lambda r: r) if n == "cm-inbox" else real_load(n)
TK._caller_session = lambda: "sess"
t_ok = TK.inbox_approval_text("maintainer", "e8b916")
t_st = TK.standing_text("maintainer", "e8b916")
T.check("OT2 the ok's text in the registry quotes the person (the 19:56 case), not the relay's wrapper; the inbox id stays",
        f"«{WORDS}»" in t_ok and "Dall'utente" not in t_ok and "(inbox e8b916, origin relay, to sess)" in t_ok
        and f"«{WORDS}»" in t_st and "Dall'utente" not in t_st, t_ok + "\n" + t_st)
TK._load = real_load

# OT3
con = TK.connect()


def add(tid, session):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"La barra fra le conversazioni ({tid})", "plan": "Disegnare la barra sopra le conversazioni vecchie, con motivo e ora.",
                                       "where": {"project": str(tmp), "session": session, "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
                                       "outcome": {"expected": "la barra nella chat, provata sul telefono", "kind": "custom", "proof": "pre-release"}}}
    tid_, errs = TK.add(con, c)
    assert not errs, errs
    con.execute("INSERT INTO approvals (task, plan, by_, at, text, what, where_) VALUES (?,NULL,NULL,?,NULL,'la barra','app')", (tid, time.time()))
    TK.set_state(con, tid, "awaiting_ok")
    con.commit()


add("app-barra", "win:supervisor-app")
add("loc-barra", "sess-qui")
told = []
TK._talk = lambda name, text: told.append((name, text))
TK.root_session = lambda: "master"
TK.answer(con, ["app-barra"], "maintainer", t_ok, caller="sess")
TK.answer(con, ["loc-barra"], "maintainer", t_ok, caller="sess")
to = {n: t for n, t in told}
T.check("OT3 the session on win gets the card with the ok: title, expected outcome, plan; the quote is the person's words",
        "La carta di app-barra" in to["win:supervisor-app"] and "Disegnare la barra" in to["win:supervisor-app"] and "provata sul telefono" in to["win:supervisor-app"]
        and WORDS in to["win:supervisor-app"] and "Dall'utente" not in to["win:supervisor-app"], to.get("win:supervisor-app", "")[:600])
T.check("OT3 a session of here and the master: the notice as before, no card", "La carta di" not in to["sess-qui"] and all("La carta di" not in t for n, t in told if n == "master"), json.dumps(told, ensure_ascii=False)[:500])

# OT4
RL = load("cm-relay")
spawned = []
RL.subprocess.Popen = lambda cmd, **kw: spawned.append(cmd)
RL.clear_needs_you = lambda s: None
info = json.loads(json.dumps({"session": "win:supervisor-app", "plan": None, "approvals": [{"what": "la barra", "by_": "maintainer"}],
                              "contract": {"task": json.loads(con.execute("SELECT contract FROM tasks WHERE id='app-barra'").fetchone()[0])["task"]}}))
RL._json_cmd = lambda *a, **k: info
RL.wake_after_approve("app-barra", "ok")
w = {c[2]: c[3] for c in spawned if c[1] == "talk"}
T.check("OT4 «Approva» in the app: the go to the session on win carries the card; the master's notice has none",
        "La carta di app-barra" in w.get("win:supervisor-app", "") and "Disegnare la barra" in w["win:supervisor-app"] and "La carta di" not in w.get("master", "x"), json.dumps(w, ensure_ascii=False)[:600])
T.finish()
