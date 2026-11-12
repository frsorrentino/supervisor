#!/usr/bin/env python3
"""supervisor-ambito-sessioni-12-11 (12/11/2026, dal collega su Mac): le sessioni fuori da workspace.root restano private.

AM1 lo slug decide senza aprire: dentro la root sì, fuori no, la cartella sorella «ws-old» no; il cwd si legge solo se lo
    slug è ambiguo (una cartella sotto la root che non c'è più)
AM2 nessuna funzione apre la trascrizione privata (ogni open/os.open è registrato: il test fallisce se la tocca): prima
    raccolta, ricerca, trascrizione per id, studio, consumi di oggi, cronologia, consegne, consumi della master, quota
AM3 il registro comune: relay, recap e guardia non vedono le righe della sessione privata
AM4 le righe di `sessions --json` che vanno al relay: la privata no, quella di un'automazione (la notte) sì, un altro host
    con la root delle sue sessioni
AM5 sessions_scope = all: tutto come prima; sessions_also: cartelle in più
AM6 l'hook di una sessione privata non chiede push al relay; quella dentro la root sì
AM7 doctor dice l'ambito e come cambiarlo; default «root» anche senza la chiave nel file
"""
import builtins
import importlib.util as _ilu
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir()).resolve()
home = tmp / "home"
conf = home / ".claude"
root = tmp / "ws"
inside = root / "atlas"
inside.mkdir(parents=True)
(root / "orbit").mkdir()
private = tmp / "private-zone" / "secret"
private.mkdir(parents=True)
sibling = tmp / "ws-old" / "x"   # slug «…-ws-old-x»: comincia come la root, ma è fuori
sibling.mkdir(parents=True)
gone = root / "gone-dir"         # sotto la root ma non c'è più: lo slug non basta, si legge il cwd
night_dir = tmp / "night-out"    # un'automazione di supervisor fuori dalla root
night_dir.mkdir()
state_dir = tmp / "state"
state_dir.mkdir()
relay_dir = tmp / "relay"
relay_dir.mkdir()
cfg_path = tmp / "config.json"
CFG0 = {"language": "it", "state_dir": str(state_dir), "accounts": {"personal": {"config_dir": str(conf)}},
        "default_account": "personal",
        "workspace": {"root": str(root), "root_session_name": "master", "project_dirs": ["."]},
        "relay": {"dir": str(relay_dir)},
        "hosts": {"win": {"kind": "windows-native", "sessions": {"root": "D:/cm-sessions"}, "measured": {"home": "C:\\Users\\U"}}}}
cfg_path.write_text(json.dumps(CFG0))
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg_path),
                   "CM_TASKS_DB": str(tmp / "tasks.db"), "CM_RELAY_STATE": str(tmp / "no-state.json"), "CM_OBSERVE_DIR": str(tmp / "no-observe")})
for k in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR", "CM_RELAY_CM"):
    os.environ.pop(k, None)


def slug(p):
    return re.sub(r"[^A-Za-z0-9]", "-", str(p))


NOW = time.time()
TS = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - 60))


def transcript(cwd, sid, word):
    d = conf / "projects" / slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    rows = [{"type": "user", "cwd": str(cwd), "sessionId": sid, "timestamp": TS, "uuid": sid + "-u",
             "message": {"role": "user", "content": f"cerca {word}"}},
            {"type": "assistant", "cwd": str(cwd), "sessionId": sid, "timestamp": TS, "uuid": sid + "-a",
             "message": {"role": "assistant", "model": "claude-x", "content": [{"type": "text", "text": f"Esito: {word}"}],
                         "usage": {"input_tokens": 10, "output_tokens": 5}}}]
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    sub = d / sid / "subagents"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "agent-1.jsonl").write_text(json.dumps(rows[1]) + "\n")
    return f


SID_IN, SID_PRIV, SID_SIB, SID_GONE, SID_NIGHT = "aaaa-in", "bbbb-priv", "cccc-sib", "dddd-gone", "eeee-night"
f_in = transcript(inside, SID_IN, "parola-dentro")
f_priv = transcript(private, SID_PRIV, "parola-fuori")
f_sib = transcript(sibling, SID_SIB, "parola-sorella")
f_gone = transcript(gone, SID_GONE, "parola-sparita")
f_night = transcript(night_dir, SID_NIGHT, "parola-notte")
NEVER = [str(f_priv.parent), str(f_sib.parent)]   # le cartelle che nessuno deve aprire

# il registro delle sessioni interattive dell'account (cm-quota)
(conf / "sessions").mkdir(parents=True)
for i, (cwd, sid) in enumerate(((inside, SID_IN), (private, SID_PRIV))):
    (conf / "sessions" / f"{i}.json").write_text(json.dumps({"kind": "interactive", "sessionId": sid, "cwd": str(cwd)}))

# il registro comune
led = state_dir / "ledger.jsonl"
led.write_text("".join(json.dumps(r) + "\n" for r in (
    {"ts": TS, "event": "start", "session_id": SID_IN, "cwd": str(inside), "account": "personal"},
    {"ts": TS, "event": "stop", "session_id": SID_IN, "cwd": str(inside), "account": "personal", "last": "dentro", "esito": "Esito: dentro"},
    {"ts": TS, "event": "stop-failure", "session_id": SID_IN, "cwd": str(inside), "error": "x"},
    {"ts": TS, "event": "start", "session_id": SID_PRIV, "cwd": str(private), "account": "personal"},
    {"ts": TS, "event": "stop", "session_id": SID_PRIV, "cwd": str(private), "account": "personal", "last": "segreto", "esito": "Esito: segreto"},
    {"ts": TS, "event": "stop-failure", "session_id": SID_PRIV, "cwd": str(private), "error": "x"},
    {"ts": TS, "event": "end", "session_id": SID_PRIV, "cwd": str(private), "reason": "x"},
)))

# --- ogni apertura di file passa di qui
OPENED = []
_open, _io_open, _os_open = builtins.open, io.open, os.open


def _rec(p):
    try:
        OPENED.append(os.path.realpath(os.fsdecode(p)) if isinstance(p, (str, bytes, os.PathLike)) else "")
    except (TypeError, ValueError):
        pass


def _o(p, *a, **k):
    _rec(p)
    return _open(p, *a, **k)


def _io(p, *a, **k):
    _rec(p)
    return _io_open(p, *a, **k)


def _oo(p, *a, **k):
    _rec(p)
    return _os_open(p, *a, **k)


def load(name):
    spec = _ilu.spec_from_file_location(name.replace("-", "_"), T.SCRIPTS / f"{name}.py")
    m = _ilu.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cm = load("cm-config")
CFG = cm.load(warn=False)
RL = load("cm-relay")
R = load("cm-raccolta")
TL = load("cm-timeline")
RS = load("cm-results")
MU = load("cm-master-usage")
Q = load("cm-quota")
RC = load("cm-recap")
G = load("cm-guard")
core = load("cm-core")

ROWS = [{"name": "atlas", "tmux": "atlas", "cwd": str(inside), "session_id": SID_IN, "account": "personal"},
        {"name": "secret", "tmux": "secret", "cwd": str(private), "session_id": SID_PRIV, "account": "personal"},
        {"name": "win:app", "host": "win", "cwd": "D:\\cm-sessions\\personali\\app", "session_id": "w1"},
        {"name": "win:fuori", "host": "win", "cwd": "D:\\workspaces\\segreto", "session_id": "w2"}]
RL.run_cm = lambda *a, **k: (0, json.dumps(ROWS)) if a[:2] == ("sessions", "--json") else (1, "")
builtins.open, io.open, os.open = _o, _io, _oo
try:
    # AM1
    v_in, v_priv, v_sib = (cm.transcript_in_scope(CFG, f) for f in (f_in, f_priv, f_sib))
    opened_am1 = [p for p in OPENED if any(p.startswith(n) for n in NEVER) or p.startswith(str(f_in.parent))]
    v_gone = cm.transcript_in_scope(CFG, f_gone)
    gone_read = any(p.startswith(str(f_gone.parent)) for p in OPENED)
    # AM2
    got = {}
    got["raccolta"] = R.past_files(NOW)
    got["search"] = sorted(RL.search_files(NOW)[1])
    got["by_sid"] = [RL.transcript_by_sid(SID_IN), RL.transcript_by_sid(SID_PRIV), RL.transcript_by_sid(SID_SIB)]
    got["studio"] = RL.studio_outcomes([str(private), str(inside)], NOW)
    got["usage"] = RL.usage_today([], NOW)
    got["timeline"] = [x[3] for x in TL.sessions_in(NOW - 3600, live=RL._json_cmd("sessions", "--json", "--no-screen"))]
    got["results"] = [RS._transcript_of_sid(SID_IN), RS._transcript_of_sid(SID_PRIV)]
    got["master_week"] = [str(x) for x in MU._week_files(str(conf), NOW - 86400)] if True else []
    got["quota"] = Q.trascrizioni_locali(str(conf))
    # AM3
    got["ledger"] = sorted({r.get("session_id") for r in core.ledger_rows()})
    got["recap"] = sorted({e.get("session_id") for e in RC.read_events(since=__import__("datetime").datetime.now() - __import__("datetime").timedelta(hours=24))})
    got["guard"] = sorted(G.failed_since(0))
    # AM4
    got["rows"] = [r["name"] for r in RL._json_cmd("sessions", "--json", "--no-screen")]
    cm.note_automation(CFG, str(night_dir))
    v_night = cm.transcript_in_scope(CFG, f_night)
finally:
    builtins.open, io.open, os.open = _open, _io_open, _os_open

touched = sorted({p for p in OPENED if any(p.startswith(n) for n in NEVER)})
T.check("AM1 the slug decides without opening: inside the root yes, outside no, the sibling folder «ws-old» no",
        v_in is True and v_priv is False and v_sib is False and not opened_am1, str((v_in, v_priv, v_sib, opened_am1)))
T.check("AM1 a folder under the root that no longer exists: the slug is not enough, the cwd written in the transcript decides",
        v_gone is True and gone_read, str((v_gone, gone_read)))
T.check("AM2 no function opens the private transcript or the sibling's (every open is recorded)", not touched, str(touched))
flat = json.dumps(got)
T.check("AM2 first collection, search, transcript by id, studio, today's usage, timeline, deliveries, master usage, quota: none lists the private one",
        "private-zone" not in flat and "ws-old" not in flat and SID_PRIV not in flat
        and str(f_in) in got["raccolta"] and os.path.realpath(f_in) in got["search"] and got["by_sid"][0] == str(f_in)
        and got["by_sid"][1] is None and got["by_sid"][2] is None and got["results"] == [str(f_in), None]
        and any(SID_IN in x for x in got["master_week"]) and any(SID_IN in x for x in got["quota"]), flat[:900])
T.check("AM3 the shared ledger: relay, recap and guard do not see the private session's rows",
        got["ledger"] == [SID_IN] and got["recap"] == [SID_IN] and got["guard"] == [SID_IN], str((got["ledger"], got["recap"], got["guard"])))
T.check("AM4 the rows of `sessions --json` that reach the relay: the private one no; another host follows the root of its sessions",
        got["rows"] == ["atlas", "win:app"], str(got["rows"]))
T.check("AM4 a folder where supervisor ran an automation (the night) stays in scope even outside the root",
        v_night is True and str(night_dir) in json.loads((state_dir / "scope-automations.json").read_text()), str(v_night))

# AM5
c_all = json.loads(json.dumps(CFG))
c_all["workspace"]["sessions_scope"] = "all"
c_also = json.loads(json.dumps(CFG))
c_also["workspace"]["sessions_also"] = [str(tmp / "private-zone")]
cm._SCOPE_MEMO.clear()
T.check("AM5 sessions_scope = all: every transcript and row as before",
        all(cm.transcript_in_scope(c_all, f) for f in (f_priv, f_sib)) and len(cm.scope_rows(c_all, ROWS)) == 4, "")
T.check("AM5 sessions_also: an extra folder is in scope, the others still not",
        cm.transcript_in_scope(c_also, f_priv) is True and cm.transcript_in_scope(c_also, f_sib) is False
        and cm.cwd_in_scope(c_also, str(private)) is True, "")
R.CFG["workspace"]["sessions_scope"] = "all"
T.check("AM5 with «all» the first collection lists the private transcript again", str(f_priv) in R.past_files(NOW), "")
R.CFG["workspace"]["sessions_scope"] = "root"

# AM6 l'hook: una sessione privata non chiede push al relay
c6 = dict(CFG0, relay={"dir": str(relay_dir), "enabled": True})
cfg6 = tmp / "config6.json"
cfg6.write_text(json.dumps(c6))
(relay_dir / "serve.pid").write_text(str(os.getpid()))
req = relay_dir / "push-req" / "hook"


def hook(cwd, sid):
    req.unlink(missing_ok=True)
    env = dict(os.environ, CC_SUPERVISOR_CONFIG=str(cfg6))
    subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "SessionEnd"], input=json.dumps({"session_id": sid, "cwd": str(cwd), "reason": "other"}),
                   capture_output=True, text=True, env=env, timeout=60)
    return req.exists()


T.check("AM6 the hook of a private session asks the relay for no push; a session inside the root does",
        hook(private, "p6") is False and hook(inside, "i6") is True, "")

# AM7
r7 = T.run_config(["doctor"], home, cfg_path)
cfg_old = tmp / "config-old.json"
cfg_old.write_text(json.dumps({k: v for k, v in CFG0.items()}))
T.check("AM7 doctor says the scope, where the private sessions are seen and how to read them all",
        "sessions_scope_root" in r7.stdout and str(root) in r7.stdout and "\"sessions_scope\": \"all\"" in r7.stdout, r7.stdout[-600:])
T.check("AM7 the default is «root» also for a config written before the key existed",
        "sessions_scope" not in json.loads(cfg_old.read_text())["workspace"] and cm.sessions_scope(cm.load(str(cfg_old), warn=False)) == "root", "")
T.finish()
