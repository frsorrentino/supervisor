#!/usr/bin/env python3
"""La chat dopo un /clear, un riavvio o una ripresa (05/11/2026, supervisor-catena-trascrizioni-05-11, contratto 1.126).
Ledger, trascrizioni e relay in una cartella temporanea.

CT1 il SessionStart scrive nella riga start i legami: tmux; prev_sid per un /clear (la riga end della stessa tmux, il
    pid del ledger non lega) e per un riavvio di supervisor (il marcatore dell'esecutore, restart clean|resume); handoff
CT2 la catena: dalla conversazione di adesso alle precedenti della stessa sessione, fino a 7 giorni; un'altra tmux o
    una cartella diversa non entrano; le righe di prima del 05/11 (senza prev_sid) si legano per cartella e tempo
CT3 op transcript: la pagina dal fondo con poche voci dopo il /clear porta anche quelle di prima, con la voce
    separator (reason, at, memory, handoff, text); before= passa alla conversazione di prima; after= con l'id di una
    voce di prima non azzera piu' la chat (niente reset) e porta il separator e le voci nuove
CT4 una compattazione nello stesso file: separator leggero (reason compact, memory summary) al suo posto
CT5 la fixture del contratto: una pagina con un separator, con le chiavi che manda il codice
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
                           "default_account": "personal", "workspace": {"root": str(tmp / "ws"), "root_session_name": "master", "sessions_scope": "all"},
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


# CT1 — la sessione «alfa»: A, poi /clear → B, poi un riavvio pulito di supervisor → C
A, B, C, X = "aaaa1111-0000-4000-8000-000000000001", "bbbb2222-0000-4000-8000-000000000002", "cccc3333-0000-4000-8000-000000000003", "xxxx9999-0000-4000-8000-000000000009"
conv(A, NOW - 3600, ["uno", "due", "tre"], compact_at=1)
conv(B, NOW - 1800, ["quattro"])
conv(C, NOW - 600, ["cinque"])
conv(X, NOW - 1700, ["altra"])
hook("SessionStart", {"session_id": A, "cwd": str(ws), "source": "startup"}, "alfa")
hook("SessionStart", {"session_id": X, "cwd": str(ws), "source": "startup"}, "beta")
hook("SessionEnd", {"session_id": A, "cwd": str(ws), "reason": "clear"}, "alfa")
hook("SessionEnd", {"session_id": X, "cwd": str(ws), "reason": "clear"}, "beta")
hook("SessionStart", {"session_id": B, "cwd": str(ws), "source": "clear"}, "alfa")
(state / "restarted").mkdir(exist_ok=True)
(state / "restarted" / "alfa.json").write_text(json.dumps({"prev_sid": B, "clean": True, "at": time.time()}))
hook("SessionStart", {"session_id": C, "cwd": str(ws), "source": "startup"}, "alfa")
st = {r["session_id"]: r for r in ledger_starts()}
T.check("CT1 the start rows carry the links: tmux; B prev_sid A (the end row of alfa, not beta's, though both cleared); C a restart clean from B",
        st[B].get("tmux") == "alfa" and st[B].get("prev_sid") == A and st[C].get("restart") == "clean" and st[C].get("prev_sid") == B
        and not (state / "restarted" / "alfa.json").exists(), json.dumps({k[:4]: {x: v.get(x) for x in ("tmux", "prev_sid", "restart")} for k, v in st.items()}))

sp = importlib.util.spec_from_file_location("cm_relay_ct", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)

# CT2
ch = RL.conversation_chain(C, str(ws), "alfa")
T.check("CT2 the chain from C: B (opened by a restart, memory none) then A (opened by a /clear); beta's conversation not in it",
        [x["sid"] for x in ch] == [B, A] and ch[0]["sep"]["reason"] == "restart" and ch[1]["sep"]["reason"] == "clear"
        and ch[0]["sep"]["memory"] == "none" and "Riavvio" in ch[0]["sep"]["text"], json.dumps(ch)[:600])
T.check("CT2 nothing older than 7 days", RL.conversation_chain(C, str(ws), "alfa", now_=NOW + 8 * 86400) == [], "")
led = state / "ledger.jsonl"
full = led.read_text()
old = [json.loads(x) for x in full.splitlines()]
for r in old:
    if r.get("event") == "start":
        r.pop("prev_sid", None); r.pop("tmux", None)
    if r.get("event") == "end":
        r.pop("tmux", None)
old = [r for r in old if not (r.get("event") == "end" and r.get("session_id") == X)]
led.write_text("\n".join(json.dumps(r) for r in old) + "\n")
ch_old = RL.conversation_chain(B, str(ws), "alfa")
T.check("CT2 rows from before the links (no prev_sid, no tmux): a /clear still binds by folder and time", [x["sid"] for x in ch_old] == [A], json.dumps(ch_old)[:300])

# CT3 — op transcript di alfa adesso (C), con la catena
row = {"tmux": "alfa", "name": "alfa", "session_id": C, "cwd": str(ws), "status": "idle", "transcript": str(proj_dir / f"{C}.jsonl")}
RL.live_row = lambda tm: dict(row) if tm == "alfa" else None
# il ledger coi legami, e alla partenza di C un passaggio di consegne annunciato
rows_full = [json.loads(x) for x in full.splitlines()]
led.write_text("\n".join(json.dumps(dict(r, handoff=str(ws / "docs" / "handoff.md")) if r.get("event") == "start" and r.get("session_id") == C else r)
                         for r in rows_full) + "\n")
ok, t = RL.transcript_page("alfa", "alfa", "3")
p1 = json.loads(t)
roles = [(e["role"], e.get("reason")) for e in p1["entries"]]
sep = next((e for e in p1["entries"] if e["role"] == "separator"), {})
T.check("CT3 the page from the end after the restart (C has 2 entries): the separator, then C's entries; more true (B is before)",
        ok and p1["more"] is True and roles == [("separator", "restart"), ("user", None), ("assistant", None)], json.dumps(p1)[:700])
T.check("CT3 the separator: reason, at, memory handoff (a handoff was announced), the handoff file, a readable text",
        set(sep) >= {"id", "role", "reason", "at", "memory", "handoff", "text"} and sep["memory"] == "handoff" and sep["handoff"].endswith("handoff.md")
        and "passaggio di consegne" in sep["text"], json.dumps(sep))
first = p1["entries"][0]["id"]
ok2, t2 = RL.transcript_page("alfa", "alfa", f"12:before={first}")
p2 = json.loads(t2)
T.check("CT3 before= the separator goes on into the conversations before: B's answer, the /clear separator, A's entries",
        ok2 and [e.get("text") for e in p2["entries"]][-1] == "risposta quattro" and any(e.get("text") == "risposta uno" for e in p2["entries"])
        and ("separator", "clear") in [(e["role"], e.get("reason")) for e in p2["entries"]], json.dumps(p2)[:600])
b_ans = next(e["id"] for e in p2["entries"] if e.get("text") == "risposta quattro")
ok3, t3 = RL.transcript_page("alfa", "alfa", f"10:after={b_ans}")
p3 = json.loads(t3)
T.check("CT3 after= with an id of the conversation before (the chat open across the /clear): no reset, the separator and the new entries",
        ok3 and not p3.get("reset") and [e["role"] for e in p3["entries"]][:1] == ["separator"] and p3["entries"][-1].get("text") == "risposta cinque", json.dumps(p3)[:500])

# CT4
comp = [e for e in p2["entries"] if e.get("reason") == "compact"]
T.check("CT4 a compaction inside A: a light separator (memory summary) between «due» and «tre»", len(comp) == 1 and comp[0]["memory"] == "summary"
        and [e.get("text") for e in p2["entries"]].index(comp[0]["text"]) > [e.get("text") for e in p2["entries"]].index("risposta due"), json.dumps(comp))

# CT5
fx = json.loads((T.ROOT / "tests/fixtures/relay/cmd-result-sample.json").read_text())
res = {r["id"]: r for r in fx["result"]}
pages = [json.loads(res[c["id"]]["text"]) for c in fx["cmd"] if c["op"] == "transcript" and c["id"] in res and res[c["id"]]["ok"]]
fsep = [e for pg in pages for e in pg.get("entries", []) if e.get("role") == "separator"]
T.check("CT5 the contract's sample: a transcript page with a separator carrying the keys the code sends", fsep and all(set(e) == set(sep) for e in fsep), json.dumps(fsep)[:300])
T.finish()
