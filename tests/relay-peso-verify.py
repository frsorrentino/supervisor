#!/usr/bin/env python3
"""Il peso del relay su Firebase (11/11/2026, supervisor-relay-peso-firebase-11-11: 5,88 GB conservati su 1 gratuito, 288.812
voci in /result mai tolte, 12,3 GB scaricati in 11 giorni su 10 al mese). Firebase finto in memoria: nessuna rete.

PW1 ogni esito scritto entra nella lista della pulizia; prima di RESULT_TTL_S resta, dopo esce con UN PATCH di null; una
    pulizia fallita (rete) lascia la lista com'era e riprova al giro dopo
PW2 una lettura riuscita porta etag (l'impronta del testo); lo stesso etag nel Cmd → unchanged true e testo vuoto; un etag
    diverso → il testo intero; un comando che cambia qualcosa non ha etag
PW3 il battito: uno stato uguale pubblicato da meno di STATE_REPUBLISH_S non si ripubblica su Firebase (in locale si'),
    dopo si'; _same_state ignora solo ts e published_at
PW4 relay results-prune: --dry-run conta e non tocca niente; --yes toglie le voci vecchie e tiene quelle in lista
PW5 i conti in usage-firebase.json e la riga firebase_usage di doctor (PASS sotto la soglia, WARN sopra)
"""
import importlib.util
import json
import os
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
sp = importlib.util.spec_from_file_location("cm_relay_peso", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
RL.C.save_key(str(rd), RL.C.new_key())

DB, CALLS, FAIL = {}, [], [False]


def fake_rtdb(method, path, obj=None, query=None, timeout=20):
    CALLS.append((method, path, query))
    if FAIL[0]:
        raise OSError("rete assente (finta)")
    parts = path.strip("/").split("/")
    node = DB
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    last = parts[-1]
    if method == "PUT":
        node[last] = obj
    elif method == "DELETE":
        node.pop(last, None)
    elif method == "PATCH":
        tgt = node.setdefault(last, {})
        for k, v in obj.items():
            if v is None:
                tgt.pop(k, None)
            else:
                tgt[k] = v
    elif method == "GET":
        v = node.get(last)
        if (query or {}).get("shallow") == "true" and isinstance(v, dict):
            return {k: True for k in v}
        return v
    return None


RL.rtdb = fake_rtdb
RL.log = lambda *_a, **_k: None

# PW1
for i in range(3):
    RL.handle_cmd(f"c{i}", {"id": f"c{i}", "op": "monitors", "device": "web", "by": "maintainer"}, [])
written = json.loads((rd / "results-written.json").read_text())
T.check("PW1 every result written goes on the cleaning list", sorted(written) == ["c0", "c1", "c2"] and set(DB.get("result", {})) == {"c0", "c1", "c2"}, str(written))
n_early = RL.sweep_results(time.time() + 10)
T.check("PW1 before RESULT_TTL_S nothing leaves", n_early == 0 and len(DB["result"]) == 3, str(DB.get("result", {}).keys()))
FAIL[0] = True
n_fail = RL.sweep_results(time.time() + RL.RESULT_TTL_S + 5)
FAIL[0] = False
T.check("PW1 a cleaning that fails (network) keeps the list for the next round",
        n_fail == 0 and len(json.loads((rd / "results-written.json").read_text())) == 3, str(n_fail))
CALLS.clear()
n_late = RL.sweep_results(time.time() + RL.RESULT_TTL_S + 5)
T.check("PW1 after RESULT_TTL_S the results leave Firebase with ONE PATCH of nulls, and the list empties",
        n_late == 3 and not DB["result"] and [c[:2] for c in CALLS] == [("PATCH", "result")] and json.loads((rd / "results-written.json").read_text()) == {},
        str(CALLS))

# PW2
RL.execute = lambda cmd: (True, '{"items": [1, 2, 3]}') if cmd.get("op") == "monitors" else (True, "fatto")
r1 = RL.run_cmd({"id": "e1", "op": "monitors", "device": "web", "by": "maintainer"})
r2 = RL.run_cmd({"id": "e2", "op": "monitors", "device": "web", "by": "maintainer", "etag": r1.get("etag")})
r3 = RL.run_cmd({"id": "e3", "op": "monitors", "device": "web", "by": "maintainer", "etag": "0000000000000000"})
r4 = RL.run_cmd({"id": "e4", "op": "prompt", "session": "x", "arg": "ciao", "device": "web", "by": "maintainer"})
T.check("PW2 a read carries etag; the same etag back → unchanged true and an empty text; another etag → the whole text",
        r1["ok"] and len(r1.get("etag") or "") == 16 and r1["text"] == '{"items": [1, 2, 3]}' and "unchanged" not in r1
        and r2["unchanged"] is True and r2["text"] == "" and r2["etag"] == r1["etag"] and r3["text"] == r1["text"] and "unchanged" not in r3,
        f"{r1} | {r2} | {r3}")
T.check("PW2 a command that changes something has no etag", "etag" not in r4 and "unchanged" not in r4, str(r4))

# PW3
st = {"v": 1, "ts": 100.0, "published_at": 100.0, "sessions": [{"name": "master", "state": "idle"}]}
T.check("PW3 _same_state ignores ts and published_at only",
        RL._same_state(st, dict(st, ts=200.0, published_at=300.0)) and not RL._same_state(st, dict(st, sessions=[]))
        and not RL._same_state(st, None), "")
now = time.time()
fp = RL.sources_print()
(rd / "beat.json").write_text(json.dumps({"fp": fp, "full_at": now}))
(rd / "last-state.json").write_text(json.dumps({"state": dict(st, published_at=now - 30), "full": dict(st, published_at=now - 30), "seq": 0,
                                                "pushed_at": now - 30}))
DB.pop("state", None)
m1 = {}
s1 = RL._light(m1)
local1 = json.loads((rd / "local" / "state.json").read_text()) if (rd / "local" / "state.json").is_file() else {}
T.check("PW3 the beat with an equal state published 30 s ago: nothing on Firebase, the local copy updated",
        s1 is not None and m1.get("skipped") is True and "state" not in DB and local1.get("published_at", 0) >= now - 1, f"{m1} {DB.keys()} {local1.get('published_at')}")
(rd / "last-state.json").write_text(json.dumps({"state": dict(st, published_at=now - RL.STATE_REPUBLISH_S - 5), "full": dict(st), "seq": 0, "pushed_at": now - 400}))
m2 = {}
RL._light(m2)
T.check("PW3 past STATE_REPUBLISH_S the same state is published again (the apps say «stale» after 180 s)",
        not m2.get("skipped") and "state" in DB and RL.STATE_REPUBLISH_S < 180, str(m2))

# PW4
DB["result"] = {f"old{i}": {"v": 1, "enc": "x" * 100} for i in range(7)}
DB["result"]["fresh"] = {"v": 1, "enc": "y"}
RL.result_written("fresh")
r_usage = RL.results_prune([])
before = dict(DB["result"])
import io  # noqa: E402
import contextlib  # noqa: E402
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc_dry = RL.results_prune(["--dry-run"])
T.check("PW4 results-prune --dry-run counts the old entries and touches nothing; without --dry-run or --yes it refuses",
        rc_dry == 0 and DB["result"] == before and "8 voci; 7 vecchie" in buf.getvalue() and r_usage == 2, buf.getvalue())
os.environ["CM_PRUNE_SETTLE_S"] = "0"
_real_sleep = RL.time.sleep
RL.time.sleep = lambda _s: DB["result"].__setitem__("arrived-meanwhile", {"v": 1, "enc": "z"})   # un esito arrivato nell'attesa
try:
    with contextlib.redirect_stdout(io.StringIO()):
        rc_yes = RL.results_prune(["--yes"])
finally:
    RL.time.sleep = _real_sleep
T.check("PW4 results-prune --yes removes the old entries and keeps the ones on the cleaning list and one written while it waited",
        rc_yes == 0 and sorted(DB["result"]) == ["arrived-meanwhile", "fresh"], str(DB["result"].keys()))

# PW5
u = json.loads((rd / "usage-firebase.json").read_text())
today = u[time.strftime("%Y-%m-%d")]
T.check("PW5 the counters: results written with their bytes, the ones removed, state published and skipped, the old ones pruned",
        today.get("results") == 3 and today.get("result_bytes", 0) > 0 and today.get("results_deleted") == 3 and today.get("state_pubs") == 1
        and today.get("state_skipped") == 1 and today.get("results_pruned") == 7, str(today))
spc = importlib.util.spec_from_file_location("cm_config_peso", T.SCRIPTS / "cm-config.py")
CC = importlib.util.module_from_spec(spc)
spc.loader.exec_module(CC)
c = CC.load(cfg, warn=False)
row_ok = CC.firebase_usage_row(c)
u[time.strftime("%Y-%m-%d")]["result_bytes"] = 900e6
(rd / "usage-firebase.json").write_text(json.dumps(u))
row_hi = CC.firebase_usage_row(c)
T.check("PW5 doctor: firebase_usage PASS under the free threshold, WARN with the fix above it",
        row_ok[0] == "PASS" and row_ok[1] == "doctor.firebase_usage" and row_hi[0] == "WARN" and row_hi[3] == "doctor.fix_firebase_usage", f"{row_ok} {row_hi}")

# PW6 gli esempi del contratto: …1350 una lettura con etag, …1351 la stessa con quell'etag → unchanged
SAMPLE = json.loads((T.ROOT / "tests" / "fixtures" / "relay" / "cmd-result-sample.json").read_text())
res = {r["id"].split("-")[1]: r for r in SAMPLE["result"]}
cmds = {c["id"].split("-")[1]: c for c in SAMPLE["cmd"]}
T.check("PW6 the samples …1350/…1351: etag is the code's fingerprint of the text; the read sent back with it is unchanged with an empty text",
        res["1350"]["etag"] == RL.etag_of(res["1350"]["text"]) and cmds["1351"]["etag"] == res["1350"]["etag"]
        and res["1351"] == {"id": cmds["1351"]["id"], "ok": True, "text": "", "etag": res["1350"]["etag"], "unchanged": True, "at": res["1351"]["at"]}
        and list(res["1351"]) == list(r2), f"{res.get('1351')} | {list(r2)}")
T.rm(tmp)
T.finish()
