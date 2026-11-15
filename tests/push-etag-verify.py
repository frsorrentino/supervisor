#!/usr/bin/env python3
"""L'impronta dello stato nelle sveglie FCM (15/11/2026, supervisor-push-etag-15-11, contratto 1.148).

Dalla misura del giorno il 99% dei download da Firebase era /state, 35,5 KB riletti interi da 4 dispositivi a ogni
sveglia di evento. Ogni sveglia porta ora due campi facoltativi, etag e published_at dello stato che c'e' su /state:
chi lo ha gia' non lo riscarica. Firebase finto in memoria, FCM finto: nessuna rete.

PE1  una push con un evento: la sveglia porta etag = i primi 16 esadecimali dello SHA-1 dell'enc scritto in /state, e
     published_at = quello dello stato; last-state.json li tiene in `pub`
PE2  il battito che ripubblica lo stato (dopo STATE_REPUBLISH_S): `pub` segue il documento nuovo; il battito che non
     ripubblica uno stato uguale lascia l'etag di quello che e' su Firebase
PE3  un evento che non nasce dal diff (recap): /state non cambia, la sveglia porta l'impronta di quello che c'e' gia'
PE4  un last-state.json scritto prima della 1.148 (senza `pub`): la sveglia parte coi campi di sempre, senza i due nuovi
PE5  ogni valore della sveglia e' una stringa; i campi sono quelli della fixture fcm-event-sample.json, che ha anche
     l'esempio di un relay piu' vecchio
PE6  una push senza eventi e con lo stato uguale e recente non ripubblica, non manda sveglie e tiene `pub`
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
rd = tmp / "relay"
rd.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "workspace": {"root": str(home)},
                           "accounts": {"p": {"config_dir": str(home / ".claude")}}, "default_account": "p",
                           "relay": {"enabled": True, "dir": str(rd), "firebase_url": "https://finto-default-rtdb.example"}}))
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_RELAY_NO_PUSH": "1"})
sp = importlib.util.spec_from_file_location("cm_relay_etag", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
RL.C.save_key(str(rd), RL.C.new_key())

DB, FCM = {}, []


def fake_rtdb(method, path, obj=None, query=None, timeout=20):
    parts = path.strip("/").split("/")
    node = DB
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    if method == "PUT":
        node[parts[-1]] = obj
    elif method == "PATCH":
        node.setdefault(parts[-1], {}).update(obj)
    elif method == "GET":
        return node.get(parts[-1])
    return None


def fake_fcm(data):
    FCM.append({k: str(v) for k, v in data.items() if v is not None})   # come fcm_send: stringhe, i None fuori
    return True


RL.rtdb = fake_rtdb
RL.fcm_send = fake_fcm
RL.log = lambda *_a, **_k: None


def etag_now():
    return hashlib.sha1(DB["state"]["enc"].encode()).hexdigest()[:16]


def last():
    return json.loads((rd / "last-state.json").read_text())


def on_bus():
    return RL.C.decrypt(DB["state"], RL.key())


# una push vera di _push, con la raccolta e il diff finti: lo stato e gli eventi li decide il test
STATE = {"v": 1, "sessions": [{"name": "alfa", "state": "idle", "id": "s1"}]}
EVENTS = []
RL.collect_sources = lambda now: {"rows": [], "prefixes": (), "state_max_kb": 8}
RL.S.build_state = lambda src, now, fit=False: dict(json.loads(json.dumps(STATE)), ts=now)
RL.S.fit_state = lambda st, kb: st
RL.S.events_between = lambda old, new, now, seq, warn_pct=95: ([dict(e, key=f"{int(now)}_{seq + i:03d}", ts=int(now)) for i, e in enumerate(EVENTS)], seq + len(EVENTS))
for name in ("note_tail_paths", "push_tails", "attention_reminders", "unseen_notice", "fallback_clear", "prune_events", "prune_share", "prune_local"):
    setattr(RL, name, lambda *a, **k: None)


def push():
    m = {}
    st = RL._push(False, time.time(), m)
    if m.get("post"):
        m["post"]()
    return st, m


# PE1
EVENTS[:] = [{"kind": "question", "session": "alfa"}, {"kind": "done", "session": "beta", "quiet": True}]
st1, m1 = push()
p1 = last().get("pub") or {}
T.check("PE1 a push with events: each wake-up carries etag = the first 16 hex of the SHA-1 of the enc written to /state, and published_at = the state's",
        len(FCM) == 2 and all(x.get("etag") == etag_now() and x.get("published_at") == str(on_bus()["published_at"]) for x in FCM)
        and re.fullmatch(r"[0-9a-f]{16}", FCM[0].get("etag") or ""), f"{FCM} {DB.get('state') and etag_now()}")
T.check("PE1 the old fields are as before (kind, session, ts, key; quiet «1» on the quiet one), and last-state.json keeps pub {etag, published_at}",
        FCM[0]["kind"] == "question" and FCM[0]["session"] == "alfa" and "quiet" not in FCM[0] and FCM[1].get("quiet") == "1" and "key" in FCM[0] and "ts" in FCM[0]
        and p1 == {"etag": etag_now(), "published_at": on_bus()["published_at"]}, f"{FCM} {p1}")

# PE6
EVENTS[:] = []
del FCM[:]
enc_before = DB["state"]["enc"]
st6, m6 = push()
T.check("PE6 a push with no event and an equal, recent state: nothing written to /state, no wake-up, pub kept",
        m6.get("skipped") is True and DB["state"]["enc"] == enc_before and FCM == [] and last().get("pub") == p1, f"{m6.get('skipped')} {FCM} {last().get('pub')}")

# PE3
ev = RL.emit("recap", "Diario del 15/11", "testo del diario", ref="2026-11-15")
T.check("PE3 an event outside the diff (recap): /state is not written, and the wake-up carries the etag and published_at of the state already there",
        DB["state"]["enc"] == enc_before and len(FCM) == 1 and FCM[0]["kind"] == "recap" and FCM[0]["key"] == ev["key"] and "session" not in FCM[0]
        and FCM[0].get("etag") == p1["etag"] and FCM[0].get("published_at") == str(p1["published_at"]), str(FCM))

# PE2
fp = RL.sources_print()
(rd / "beat.json").write_text(json.dumps({"fp": fp, "full_at": time.time()}))
l2 = last()
old_pub = l2["state"]["published_at"]
l2["state"]["published_at"] = l2["full"]["published_at"] = time.time() - RL.STATE_REPUBLISH_S - 5
(rd / "last-state.json").write_text(json.dumps(l2))
m2 = {}
RL._light(m2)
p2 = last().get("pub") or {}
T.check("PE2 the beat that publishes the state again: pub follows the new document (a new etag, the new published_at)",
        not m2.get("skipped") and DB["state"]["enc"] != enc_before and p2 == {"etag": etag_now(), "published_at": on_bus()["published_at"]}
        and p2["etag"] != p1["etag"] and p2["published_at"] > old_pub, f"{m2} {p2} {p1}")
(rd / "beat.json").write_text(json.dumps({"fp": RL.sources_print(), "full_at": time.time()}))
m2b = {}
RL._light(m2b)
T.check("PE2 the beat that does not publish an equal state again leaves the etag of what is on Firebase",
        m2b.get("skipped") is True and last().get("pub") == p2 and p2["etag"] == etag_now(), f"{m2b} {last().get('pub')}")

# PE4
l4 = last()
l4.pop("pub")
(rd / "last-state.json").write_text(json.dumps(l4))
del FCM[:]
RL.emit("recap", "Diario del 15/11", "testo", ref="2026-11-15")
T.check("PE4 a last-state.json from before 1.148 (no pub): the wake-up leaves with the fields it always had, without the two new ones",
        len(FCM) == 1 and set(FCM[0]) == {"kind", "ts", "key"}, str(FCM))
T.check("PE4 fcm_pub: nothing for a missing or empty pub", RL.fcm_pub(None) == {} and RL.fcm_pub({}) == {} and RL.fcm_pub({"published_at": 1.0}) == {}, "")

# PE5
FX = json.loads((T.ROOT / "tests" / "fixtures" / "relay" / "fcm-event-sample.json").read_text(encoding="utf-8"))
del FCM[:]
EVENTS[:] = [{"kind": "question", "session": "alfa"}, {"kind": "done", "session": "beta", "quiet": True}]
STATE["sessions"][0]["state"] = "waiting"
push()
RL.emit("recap", "Diario", "testo", ref="2026-11-15")
samples = FX.get("data") or []
ok5 = len(samples) == 3 and len(FCM) == 3 and set(FCM[0]) == set(samples[0]) and set(FCM[1]) == set(samples[1]) \
    and all(isinstance(v, str) for x in samples + FCM for v in x.values()) \
    and all(re.fullmatch(r"[0-9a-f]{16}", x["etag"]) and float(x["published_at"]) > 0 for x in samples[:2] + FCM) \
    and set(samples[2]) == {"kind", "ts", "key"}
T.check("PE5 every value of a wake-up is a string; the real messages have the fields of fcm-event-sample.json (an event, a quiet one), which also has an older relay's message without etag",
        ok5, f"{FCM} | {samples}")
doc = (T.ROOT / "details" / "relay.md").read_text(encoding="utf-8")
T.check("PE5 the contract is in details/relay.md: 1.148, etag as the SHA-1 of enc, published_at, the fixture",
        "Contract 1.148" in doc and "SHA-1" in doc.split("Contract 1.148")[1] and "fcm-event-sample.json" in doc.split("Contract 1.148")[1], "")

T.rm(str(tmp))
T.finish()
