#!/usr/bin/env python3
"""La chat al punto di un risultato della ricerca (06/11/2026, supervisor-trascrizione-attorno-06-11, contratto 1.127).
Ledger, trascrizioni e relay in una cartella temporanea.

AT1 op transcript «n:around=<id>» nella conversazione di adesso: la voce al centro, anchor, more (prima) e more_after
    (dopo); vicino alla fine la pagina si sposta indietro e more_after e' false; senza around niente chiavi nuove
AT2 around con l'id di una voce di due conversazioni fa (un /clear e un riavvio in mezzo), chiesta per nome della
    sessione: le voci attorno con i separator, more_after true; da li' before= e after= proseguono
AT3 vicino all'inizio della catena la pagina si sposta avanti, more false; un id che non c'e' e' rifiutato
AT4 la ricerca: un risultato nella conversazione di prima del /clear porta il nome della sessione viva, live true e il
    sid della conversazione; around con la sua entry lo trova; anche per sid (session null) nella sola conversazione
AT5 la fixture del contratto: una pagina around con anchor e more_after, con le chiavi che manda il codice
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
proj_dir = home / ".claude" / "projects" / "-ws-proj"
proj_dir.mkdir(parents=True)
state = home / ".claude"
ws = tmp / "ws" / "proj"
ws.mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "accounts": {"personal": {"config_dir": str(home / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp / "ws"), "root_session_name": "master"},
                           "relay": {"dir": str(tmp / "relay")}, "restart": {"flag_file": str(state / "riavvio.json")}}))
(tmp / "relay").mkdir()
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR", "CM_RESTART_FLAG_FILE")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db"), "CM_RELAY_NO_PUSH": "1",
            "CM_AUTOCLEAR_SPAWN": str(tmp / "ac.log"), "CM_FEATURES_FILE": str(tmp / "f.json"), "CM_IMPORTANCE_FILE": str(tmp / "i.json"), "CM_TUNE_SPAWN": str(tmp / "tu.log")})
os.environ.clear()
os.environ.update(ENV)
NOW = time.time()


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def conv(sid, t0, texts, compact_at=None):
    """Una trascrizione: per ogni testo una riga user e una assistant, un minuto l'una dall'altra."""
    rows = []
    for i, tx in enumerate(texts):
        t = t0 + i * 60
        rows.append({"type": "user", "uuid": f"{sid[:4]}-u{i}", "timestamp": iso(t), "sessionId": sid, "message": {"role": "user", "content": f"domanda {tx}"}})
        rows.append({"type": "assistant", "uuid": f"{sid[:4]}-a{i}", "timestamp": iso(t + 20), "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": f"risposta {tx}"}]}})
        if compact_at is not None and i == compact_at:
            rows.append({"type": "system", "subtype": "compact_boundary", "uuid": f"{sid[:4]}-cb", "timestamp": iso(t + 40), "compactMetadata": {"preTokens": 1000, "postTokens": 100}})
    (proj_dir / f"{sid}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def hook(ev, payload, tm):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), ev], input=json.dumps(payload), capture_output=True, text=True,
                          env=dict(ENV, CM_HOOK_TMUX_NAME=tm), cwd=str(ws), timeout=60)


def ledger_starts():
    return [json.loads(x) for x in (state / "ledger.jsonl").read_text().splitlines() if '"start"' in x]



A, B, C = "aaaa1111-0000-4000-8000-000000000001", "bbbb2222-0000-4000-8000-000000000002", "cccc3333-0000-4000-8000-000000000003"
conv(A, NOW - 7200, ["uno", "due", "tre", "quattro", "cinque", "sei", "giacca blu", "otto", "nove", "dieci"])
conv(B, NOW - 1800, ["undici", "dodici"])
conv(C, NOW - 900, ["c%d" % i for i in range(1, 11)])
hook("SessionStart", {"session_id": A, "cwd": str(ws), "source": "startup"}, "alfa")
hook("SessionEnd", {"session_id": A, "cwd": str(ws), "reason": "clear"}, "alfa")
hook("SessionStart", {"session_id": B, "cwd": str(ws), "source": "clear"}, "alfa")
(state / "restarted").mkdir(exist_ok=True)
(state / "restarted" / "alfa.json").write_text(json.dumps({"prev_sid": B, "clean": True, "at": time.time()}))
hook("SessionStart", {"session_id": C, "cwd": str(ws), "source": "startup"}, "alfa")

sp = importlib.util.spec_from_file_location("cm_relay_at", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
# la cartella di projects/ col nome che Claude Code da' alla cartella vera (cm-core.transcript_of la cerca cosi')
(home / ".claude" / "projects" / __import__("re").sub(r"[^A-Za-z0-9]", "-", os.path.realpath(ws))).symlink_to(proj_dir)
row = {"tmux": "alfa", "account": "personal", "name": "alfa", "session_id": C, "cwd": str(ws), "status": "idle", "transcript": str(proj_dir / f"{C}.jsonl")}
RL.live_row = lambda tm: dict(row) if tm == "alfa" else None


def page(arg, sid=None):
    ok, t = RL.transcript_page(None, None, arg, sid=sid) if sid else RL.transcript_page("alfa", "alfa", arg)
    return ok, (json.loads(t) if ok else {"err": t})


def texts(p):
    return [e.get("text") if e["role"] != "separator" else "SEP:" + e["reason"] for e in p.get("entries", [])]


# AT1
ok, p = page("5:around=cccc-a4.0")
T.check("AT1 around in the current conversation: the entry in the middle, anchor, more true, more_after true",
        ok and texts(p) == ["risposta c4", "domanda c5", "risposta c5", "domanda c6", "risposta c6"] and p["anchor"] == "cccc-a4.0"
        and p["more"] is True and p["more_after"] is True, json.dumps(p)[:500])
ok, p = page("6:around=cccc-u9.0")
T.check("AT1 near the end the page moves back: the last 6 entries, more_after false",
        ok and texts(p)[-1] == "risposta c10" and len(p["entries"]) == 6 and p["more_after"] is False and p["more"] is True, json.dumps(p)[:500])
ok, p = page("6")
T.check("AT1 a page without around has no anchor nor more_after (as before 1.127)", ok and "anchor" not in p and "more_after" not in p, json.dumps(p)[:300])

# AT2
ok, p = page("9:around=aaaa-a6.0")
tx = texts(p)
T.check("AT2 around an entry two conversations back (a /clear and a restart since), by session name: the entries around it, more_after true",
        ok and p["anchor"] == "aaaa-a6.0" and "risposta giacca blu" in tx and tx.index("risposta giacca blu") == 4 and p["more"] is True and p["more_after"] is True,
        json.dumps(p)[:600])
ok2, pa = page(f"10:after={p['entries'][-1]['id']}") if ok else (False, {})
ok3, pb = page(f"10:before={p['entries'][0]['id']}") if ok else (False, {})
T.check("AT2 from there after= goes on with the /clear separator and B, before= with the earlier entries of A",
        ok2 and "SEP:clear" in texts(pa) and "risposta undici" in texts(pa) and ok3 and texts(pb)[:1] == ["domanda uno"], json.dumps([pa, pb])[:600])
ok, p = page("40:around=bbbb-u1.0")
T.check("AT2 a page around B crosses both separators (clear before, restart after)",
        ok and "SEP:clear" in texts(p) and "SEP:restart" in texts(p) and p["anchor"] == "bbbb-u1.0", json.dumps(p)[:600])

# AT3
ok, p = page("6:around=aaaa-u0.0")
T.check("AT3 near the start of the chain the page moves forward: 6 entries from «domanda uno», more false",
        ok and texts(p)[:1] == ["domanda uno"] and len(p["entries"]) == 6 and p["more"] is False and p["more_after"] is True, json.dumps(p)[:500])
ok, p = page("6:around=zzzz-u0.0")
T.check("AT3 an id that is not there is refused", not ok and "zzzz-u0.0" in p.get("err", ""), json.dumps(p))

# AT4
RL._json_cmd = lambda *a, **k: [dict(row)] if a[:1] == ("sessions",) else None
ok, t = RL.search("giacca")
hits = json.loads(t)["hits"] if ok else []
h = next((x for x in hits if x["entry"] == "aaaa-a6.0"), {})
T.check("AT4 a hit in the conversation before the /clear: the live session's name, live true, the conversation's sid",
        h.get("session") == "alfa" and h.get("live") is True and h.get("sid") == A, json.dumps(hits)[:500])
ok, p = page(f"5:around={h.get('entry')}")
ok2, p2 = page(f"5:around={h.get('entry')}", sid=h.get("sid"))
T.check("AT4 around with the hit's entry finds it by session name, and by sid (that conversation only, read-only)",
        ok and p["anchor"] == "aaaa-a6.0" and ok2 and p2["anchor"] == "aaaa-a6.0" and texts(p2)[2:3] == ["risposta giacca blu"], json.dumps([p, p2])[:500])

# AT5
fx = json.loads((T.ROOT / "tests/fixtures/relay/cmd-result-sample.json").read_text())
res = {r["id"]: r for r in fx["result"]}
fa = [(c, json.loads(res[c["id"]]["text"])) for c in fx["cmd"] if c["op"] == "transcript" and "around=" in str(c.get("arg")) and c["id"] in res and res[c["id"]]["ok"]]
ok, p = page("9:around=aaaa-a6.0")
T.check("AT5 the contract's sample: an around page with anchor, more, more_after and a separator, the keys the code sends",
        bool(fa) and all(set(pg) == set(p) and pg["anchor"] in [e["id"] for e in pg["entries"]] and c["arg"].endswith(pg["anchor"])
                         and any(e["role"] == "separator" for e in pg["entries"]) for c, pg in fa), json.dumps(fa)[:400])
T.finish()
