#!/usr/bin/env python3
"""Verifica le domande dei server MCP sul telefono (cm-elicit.py e l'hook Elicitation di cm-hook.py, contratto 1.83).

E1  fields_of: enum → choice (un tasto per valore, oneOf/enumNames per le etichette), boolean, number/integer con
    min/max, string → text (lunghezze, format); un tipo ignoto, un enum non di stringhe, troppi campi → None
E2  check_content: valori validi puliti (numeri da stringa, interi), required mancante, valore fuori dalle opzioni,
    fuori intervallo, campo sconosciuto → errore con la chiave del messaggio
E3  answer(): solo una domanda aperta in modo form; azione ignota, link, gia' risposta, scaduta → rifiutata; il file
    di risposta porta origine «relay»; pending() cancella le scadute; clear_session() toglie quelle della sessione
E4  cm-hook.py Elicitation: senza telefono accoppiato o spenta → niente in uscita, nessun file (dialogo al
    terminale); con telefono → la domanda in state/elicit, la risposta del relay → hookSpecificOutput accept con i
    valori; decline → decline; una risposta senza origine «relay» non vale; senza risposta in wait_s → niente in
    uscita e la domanda sparisce; modo URL → esce subito, la scheda resta fino alla scadenza; schema ignoto → terminale
E5  cm-hook.py ElicitationResult toglie la scheda della sessione; hooks.json registra i due eventi (timeout 150 e 5)
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

_spec = importlib.util.spec_from_file_location("cm_elicit", T.SCRIPTS / "cm-elicit.py")
EL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(EL)

SCHEMA = {"type": "object", "required": ["env", "confirm"], "properties": {
    "env": {"type": "string", "title": "Environment", "enum": ["staging", "prod"], "enumNames": ["Staging", "Production"]},
    "confirm": {"type": "boolean", "title": "Confirm", "default": False},
    "count": {"type": "integer", "minimum": 1, "maximum": 5},
    "note": {"type": "string", "maxLength": 10, "description": "Why"},
    "region": {"type": "string", "oneOf": [{"const": "eu", "title": "Europe"}, {"const": "us", "title": "United States"}]},
    "ratio": {"type": "number"},
    "mail": {"type": "string", "format": "email"}}}

# E1
F = EL.fields_of(SCHEMA)
T.check("E1 fields_of: enum → choice with labels from enumNames, boolean with default, integer with min/max, text with max_length, oneOf → choice, number, text with format",
        F == [{"key": "env", "title": "Environment", "required": True, "kind": "choice", "options": [{"value": "staging", "label": "Staging"}, {"value": "prod", "label": "Production"}]},
              {"key": "confirm", "title": "Confirm", "required": True, "kind": "boolean", "default": False},
              {"key": "count", "title": "count", "required": False, "kind": "integer", "min": 1, "max": 5},
              {"key": "note", "title": "note", "required": False, "description": "Why", "kind": "text", "max_length": 10},
              {"key": "region", "title": "region", "required": False, "kind": "choice", "options": [{"value": "eu", "label": "Europe"}, {"value": "us", "label": "United States"}]},
              {"key": "ratio", "title": "ratio", "required": False, "kind": "number"},
              {"key": "mail", "title": "mail", "required": False, "kind": "text", "format": "email"}], json.dumps(F))
T.check("E1 fields_of → None for an unknown type, a non-string enum, an array, more than 8 fields, no properties, not an object",
        EL.fields_of({"type": "object", "properties": {"x": {"type": "object"}}}) is None
        and EL.fields_of({"type": "object", "properties": {"x": {"type": "integer", "enum": [1, 2]}}}) is None
        and EL.fields_of({"type": "object", "properties": {"x": {"type": "array", "items": {"enum": ["a"]}}}}) is None
        and EL.fields_of({"type": "object", "properties": {f"k{i}": {"type": "string"} for i in range(9)}}) is None
        and EL.fields_of({"type": "object", "properties": {}}) is None and EL.fields_of(["x"]) is None, "")

# E2
ok2, err2 = EL.check_content(F, {"env": "prod", "confirm": True, "count": "3", "note": "go", "ratio": "0.5"})
T.check("E2 check_content: valid values kept, integer from a string, number from a string, absent optional fields left out",
        err2 is None and ok2 == {"env": "prod", "confirm": True, "count": 3, "note": "go", "ratio": 0.5}, str((ok2, err2)))
cases = [({"confirm": True}, "elicit_required"), ({"env": "dev", "confirm": True}, "elicit_bad_choice"), ({"env": "prod", "confirm": "yes"}, "elicit_bad_boolean"),
         ({"env": "prod", "confirm": True, "count": 9}, "elicit_out_of_range"), ({"env": "prod", "confirm": True, "count": 2.5}, "elicit_bad_integer"),
         ({"env": "prod", "confirm": True, "note": "x" * 11}, "elicit_bad_length"), ({"env": "prod", "confirm": True, "other": 1}, "elicit_unknown_field"),
         ({"env": "prod", "confirm": True, "ratio": "abc"}, "elicit_bad_number")]
got2 = [(EL.check_content(F, c)[1] or ("ok",))[0] for c, _ in cases]
T.check("E2 check_content refuses: required missing, choice not among the options, boolean as text, out of range, a fraction for an integer, too long, an unknown field, not a number",
        got2 == [k for _, k in cases], str(got2))

# E3
tmp = Path(T.tmpdir())
st = tmp / "state"
now = 1789210000
req = {"id": "e-abc123", "session": "atlas-shop", "session_id": "sid-a", "server": "tracker", "message": "Pick", "mode": "form",
       "asked_at": now, "expires_at": now + 120, "fields": F}
(st / "elicit").mkdir(parents=True)
(st / "elicit" / "e-abc123.json").write_text(json.dumps(req))
(st / "elicit" / "e-url001.json").write_text(json.dumps(dict(req, id="e-url001", mode="url", url="https://auth.example.com", fields=None)))
(st / "elicit" / "e-old001.json").write_text(json.dumps(dict(req, id="e-old001", expires_at=now - 1)))
r3 = [EL.answer(st, "e-abc123", "maybe", {}, "phone-pixel8", "phone", now)[1],
      EL.answer(st, "e-url001", "accept", {}, "phone-pixel8", "phone", now)[1],
      EL.answer(st, "e-old001", "decline", {}, "phone-pixel8", "phone", now)[1],
      EL.answer(st, "../x", "decline", {}, "phone-pixel8", "phone", now)[1],
      EL.answer(st, "e-abc123", "accept", {"confirm": True}, "phone-pixel8", "phone", now)[1]]
T.check("E3 answer refuses: an unknown action, a link, an expired question, a bad id, a missing required value — nothing written",
        r3 == ["elicit_bad_action", "elicit_is_link", "elicit_not_waiting", "elicit_not_waiting", "elicit_required"] and not (st / "elicit" / "e-abc123.answer.json").exists(), str(r3))
a3 = EL.answer(st, "e-abc123", "accept", {"env": "prod", "confirm": True}, "phone-pixel8", "phone", now)
w3 = json.loads((st / "elicit" / "e-abc123.answer.json").read_text())
T.check("E3 answer accept → (True, elicit_accepted), the answer file with origin relay, the clean content, by and device; a second answer → already",
        a3 == (True, "elicit_accepted", {"id": "e-abc123", "server": "tracker"}) and w3 == {"id": "e-abc123", "origin": "relay", "action": "accept", "content": {"env": "prod", "confirm": True}, "by": "phone-pixel8", "device": "phone", "at": now}
        and EL.answer(st, "e-abc123", "decline", {}, "x", "phone", now)[1] == "elicit_already", str((a3, w3)))
p3 = EL.pending(st, now)
T.check("E3 pending lists the open questions and deletes the expired one; for_state keeps no session_id",
        [x["id"] for x in p3] == ["e-abc123", "e-url001"] and not (st / "elicit" / "e-old001.json").exists()
        and EL.for_state(p3[1], "atlas-shop") == {"id": "e-url001", "session": "atlas-shop", "server": "tracker", "message": "Pick", "mode": "url", "asked_at": now, "expires_at": now + 120, "url": "https://auth.example.com"}
        and "session_id" not in EL.for_state(p3[0], "atlas-shop"), str(p3))
T.check("E3 clear_session removes that session's questions only", EL.clear_session(st, "sid-a") == 2 and EL.pending(st, now) == [], "")

# E4: cm-hook.py dal vivo, con una config di prova
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
rd = tmp / "relay"
rd.mkdir()
(rd / "serve.pid").write_text(str(os.getpid()))   # un demone «vivo»: relay_push tocca solo push-req, nessuna push vera
state = tmp / "hstate"
cfg = tmp / "config.json"
base = {"language": "it", "state_dir": str(state), "workspace": {"root": str(home / "ws")},
        "accounts": {"personale": {"config_dir": str(home / ".claude")}}, "terminal": {"backend": "none"},
        "registry": {"file": str(tmp / "registry.json")}, "relay": {"enabled": True, "dir": str(rd)},
        "hooks": {"elicitation": {"enabled": True, "wait_s": 12}}}
cfg.write_text(json.dumps(base))


def env():
    return {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_PROC_SCAN_PIDS": ""}


PAY = {"session_id": "sid-h", "cwd": str(home), "hook_event_name": "Elicitation", "mcp_server_name": "tracker", "message": "Which environment?",
       "mode": "form", "requested_schema": {"type": "object", "required": ["env"], "properties": {"env": {"type": "string", "enum": ["staging", "prod"]}, "ok": {"type": "boolean"}}}}


def hook(event, payload):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), event], input=json.dumps(payload), capture_output=True, text=True, env=env(), timeout=60)


def hook_bg(payload):
    p = subprocess.Popen([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "Elicitation"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env())
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    p.stdin = None   # communicate() non deve richiuderlo
    return p


def open_requests():
    d = state / "elicit"
    return [json.loads(f.read_text()) for f in sorted(d.glob("e-*.json")) if not f.name.endswith(".answer.json")] if d.is_dir() else []


t0 = time.time()
r = hook("Elicitation", PAY)
T.check("E4 no paired device → exit 0, no output, no question written (the dialog stays in the terminal), at once",
        r.returncode == 0 and r.stdout == "" and open_requests() == [] and time.time() - t0 < 5, r.stdout + r.stderr)
(rd / "devices.json").write_text(json.dumps({"uid-1": {"name": "Pixel 8"}}))
cfg.write_text(json.dumps(dict(base, hooks={"elicitation": {"enabled": False}})))
r = hook("Elicitation", PAY)
T.check("E4 paired but hooks.elicitation.enabled false → no output, nothing written", r.returncode == 0 and r.stdout == "" and open_requests() == [], r.stdout + r.stderr)
cfg.write_text(json.dumps(base))
r = hook("Elicitation", dict(PAY, requested_schema={"type": "object", "properties": {"tags": {"type": "array"}}}))
T.check("E4 a schema the phone cannot show → no output, nothing written", r.returncode == 0 and r.stdout == "" and open_requests() == [], r.stdout + r.stderr)

p = hook_bg(PAY)
T.wait_until(lambda: open_requests(), 10)
q = (open_requests() or [{}])[0]
T.check("E4 paired → the question in state/elicit: server, message, fields from the schema, expires after wait_s; push-req touched",
        q.get("server") == "tracker" and q.get("message") == "Which environment?" and [f["kind"] for f in q.get("fields") or []] == ["choice", "boolean"]
        and q.get("expires_at", 0) - q.get("asked_at", 0) == 12 and (rd / "push-req" / "hook").exists(), str(q))
ok4 = EL.answer(state, q.get("id"), "accept", {"env": "prod"}, "phone-pixel8", "phone", int(time.time()))
out, errt = p.communicate(timeout=20)
T.check("E4 the relay's answer → hookSpecificOutput accept with the values; the question and the answer file are gone",
        ok4[0] and p.returncode == 0 and json.loads(out or "{}") == {"hookSpecificOutput": {"hookEventName": "Elicitation", "action": "accept", "content": {"env": "prod"}}}
        and open_requests() == [] and not list((state / "elicit").glob("*.answer.json")), out + errt)
led = [json.loads(x) for x in (state / "ledger.jsonl").read_text().splitlines()]
T.check("E4 the ledger records each elicitation with server, mode and outcome (unpaired, off, terminal, accept)",
        [x.get("outcome") for x in led if x["event"] == "elicitation"] == ["unpaired", "off", "terminal", "accept"] and led[-1].get("server") == "tracker", str(led[-2:]))

p = hook_bg(PAY)
T.wait_until(lambda: open_requests(), 10)
q = (open_requests() or [{}])[0]
EL.answer(state, q.get("id"), "decline", None, "phone-pixel8", "phone", int(time.time()))
out, errt = p.communicate(timeout=20)
T.check("E4 decline from the phone → hookSpecificOutput decline", json.loads(out or "{}") == {"hookSpecificOutput": {"hookEventName": "Elicitation", "action": "decline"}}, out + errt)

cfg.write_text(json.dumps(dict(base, hooks={"elicitation": {"enabled": True, "wait_s": 10}})))
p = hook_bg(PAY)
T.wait_until(lambda: open_requests(), 10)
q = (open_requests() or [{}])[0]
(state / "elicit" / f"{q.get('id')}.answer.json").write_text(json.dumps({"id": q.get("id"), "action": "accept", "content": {"env": "prod"}}))   # scritta da fuori, senza origine
t0 = time.time()
out, errt = p.communicate(timeout=30)
T.check("E4 an answer file without origin relay is ignored; no answer within wait_s → no output (the terminal dialog), the question gone",
        p.returncode == 0 and out == "" and open_requests() == [] and 5 < time.time() - t0 < 15, f"{out}{errt} {time.time() - t0:.1f}s")

t0 = time.time()
r = hook("Elicitation", dict(PAY, mode="url", url="https://auth.example.com/login", message="Log in", requested_schema=None))
T.check("E4 url mode → exits at once with no output (the dialog stays), the card with the link stays for the phone",
        r.returncode == 0 and r.stdout == "" and time.time() - t0 < 5 and [(x["mode"], x["url"]) for x in open_requests()] == [("url", "https://auth.example.com/login")], r.stdout + r.stderr)

# E5
r = hook("ElicitationResult", {"session_id": "sid-h", "mcp_server_name": "tracker", "action": "accept", "mode": "url"})
T.check("E5 ElicitationResult removes the session's card", r.returncode == 0 and open_requests() == [], r.stdout + r.stderr)
hj = json.loads((T.PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
T.check("E5 hooks.json: Elicitation (all servers, timeout 150 > wait 140 max) and ElicitationResult (timeout 5) run cm-hook.py",
        [(e.get("matcher"), e["hooks"][0]["timeout"], e["hooks"][0]["command"].split()[-1]) for e in hj["Elicitation"]] == [(None, 150, "Elicitation")]
        and [(e.get("matcher"), e["hooks"][0]["timeout"], e["hooks"][0]["command"].split()[-1]) for e in hj["ElicitationResult"]] == [(None, 5, "ElicitationResult")]
        and EL.WAIT_MAX_S < 150, str(hj.get("Elicitation")))

T.finish()
