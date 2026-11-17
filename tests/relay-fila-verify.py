#!/usr/bin/env python3
"""Le letture del relay non aspettano i lavori lenti (17/11/2026, supervisor-relay-fila-comandi-17-11).

Il 17/11 sul Chromebook la pagina di una voce (op attesa) ha aspettato 8,8 s in fila e 4,1 s di esecuzione, mentre
girava un report da 26,5 s; fra 11:15 e 11:35 la raccolta della push durava p50 78 s, p95 291 s, quasi tutta CPU, e il
relay restava minuti senza push. Il profilo (push --dry-run): ledger_rows 142 s su 218, perche' cwd_in_scope ricalcolava
le cartelle ammesse (expand, realpath, il file delle automazioni) per ognuna delle 54.095 righe del ledger. Tutte le
corsie dei comandi stanno nello stesso processo: la raccolta le rallentava tutte. E «attesa» stava fra le letture
lente, dietro kpi (fila fino a 120 s) e timeline (esecuzione fino a 98 s).

FI1 ledger_rows su 60.000 righe: le cartelle ammesse si calcolano al piu' una volta per host (prima una per riga), e le
    righe sono le stesse del filtro riga per riga (dentro e fuori dall'ambito, sul PC e su un altro host)
FI2 con un report da 30 s in corso (corsia dei comandi) e una timeline da 30 s (letture lente), un comando attesa parte
    in meno di 2 s
FI3 l'esecuzione di attesa non ricarica cm-attese a ogni voce (prima: un caricamento del modulo e della config per
    chiamata) e sta sotto 1 s
"""
import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
state = tmp / "state"
ws = home / "ws"
for d in (ws / "alfa", ws / "beta", home / "fuori" / "x", state, tmp / "relay", home / ".claude"):
    d.mkdir(parents=True, exist_ok=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "workspace": {"root": str(ws), "sessions_also": [str(home / "anche")]},
    "accounts": {"p": {"config_dir": str(home / ".claude")}}, "default_account": "p",
    "hosts": {"win": {"kind": "windows-native", "sessions": {"root": "C:\\Users\\u\\cm"}}},
    "relay": {"enabled": True, "dir": str(tmp / "relay"), "firebase_url": "https://finto-default-rtdb.example"}}))
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_RELAY_NO_PUSH": "1",
                   "CM_TASKS_DB": str(tmp / "t.db")})

# FI1 — il ledger
N = 60000
cwds = [(str(ws / "alfa"), None), (str(ws / "beta" / "sub"), None), (str(home / "fuori" / "x"), None), ("", None),
        ("C:\\Users\\u\\cm\\personali\\alfa", "win"), ("D:\\altro", "win")]
with open(state / "ledger.jsonl", "w") as f:
    for i in range(N):
        cwd, host = cwds[i % len(cwds)]
        row = {"ts": "2026-11-17T11:00:00", "event": "stop" if i % 3 else "start", "session_id": f"s{i % 50}", "cwd": cwd}
        if host:
            row["host"] = host
        f.write(json.dumps(row) + "\n")
        if i % 7 == 0:
            f.write(json.dumps({"ts": "2026-11-17T11:00:00", "event": "watch-cmd", "cwd": str(ws / "alfa")}) + "\n")
sp = importlib.util.spec_from_file_location("cm_core_fila", T.SCRIPTS / "cm-core.py")
core = importlib.util.module_from_spec(sp)
sp.loader.exec_module(core)
cmc = core.cm
calls = {"n": 0}
real_dirs = cmc.scope_dirs


def counted(cfg_, host=None):
    calls["n"] += 1
    return real_dirs(cfg_, host)


cmc.scope_dirs = counted
t0 = time.time()
got = core.ledger_rows()
n_first, dt_first = calls["n"], time.time() - t0
calls["n"] = 0
t0 = time.time()
got2 = core.ledger_rows()
n_second, dt_second = calls["n"], time.time() - t0
cmc.scope_dirs = real_dirs
raw = [json.loads(x) for x in (state / "ledger.jsonl").read_text().splitlines() if '"watch-cmd"' not in x]
c_ = cmc.load(warn=False)
want = [r for r in raw if cmc.cwd_in_scope(c_, r.get("cwd") or "", r.get("host"))]
inside = {r.get("cwd") for r in got}
T.check("FI1 ledger_rows on 60,000 rows: the allowed folders are computed at most once per host (before: once per row), on the first read and on the next one",
        n_first <= 3 and n_second <= 3, f"scope_dirs: {n_first} then {n_second} calls; {dt_first:.2f} s, {dt_second:.2f} s")
T.check("FI1 the same rows as the row-by-row filter: in the root and with no cwd yes, outside the root no, on the host only under its sessions root",
        got == want and got2 == want and len(got) == 4 * N // 6 and str(home / "fuori" / "x") not in inside and "D:\\altro" not in inside
        and "C:\\Users\\u\\cm\\personali\\alfa" in inside and "" in inside, f"{len(got)} rows, want {len(want)}; {sorted(inside)}")

# FI2 — le corsie: un report da 30 s, una timeline da 30 s, poi attesa
sp = importlib.util.spec_from_file_location("cm_relay_fila", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
RL.log_lines = []
RL.log = lambda line, now=None: RL.log_lines.append(line)
RL.C.save_key(tmp / "relay", RL.C.new_key())   # le corsie decifrano con la chiave del relay, come in relay-verify
ran, t_ = {}, {}
sv = dict(ex=RL.execute, pa=RL.push_async, last=RL.last_sessions, rtdb=RL.rtdb)


def fake_exec(cmd):
    ran[cmd["id"]] = time.time()
    if cmd["op"] in ("report", "timeline"):
        time.sleep(30)
    return (True, "ok")


try:
    RL.execute, RL.push_async, RL.last_sessions, RL.rtdb = fake_exec, (lambda *a, **k: None), (lambda: {}), (lambda *a, **k: None)
    served = []
    lanes = RL.CmdLanes([], served.append)
    now = time.time()
    lanes.put({"rep": {"op": "report", "session": "alfa", "arg": "x", "issued": now}}, now)
    lanes.put({"tl": {"op": "timeline", "session": "", "issued": now}}, now)
    T.wait_until(lambda: "rep" in ran and "tl" in ran, timeout=5)
    t_put = time.time()
    lanes.put({"att": {"op": "attesa", "session": "", "arg": "wd0b4265780", "issued": t_put}}, t_put)
    T.wait_until(lambda: "att" in ran, timeout=40)
    lanes.close()
finally:
    RL.execute, RL.push_async, RL.last_sessions, RL.rtdb = sv["ex"], sv["pa"], sv["last"], sv["rtdb"]
wait_att = ran.get("att", 1e12) - t_put
T.check("FI2 a 30 s report (the commands' lane) and a 30 s timeline (the slow reads) running: an attesa starts in under 2 s",
        "rep" in ran and "tl" in ran and wait_att < 2, f"attesa waited {wait_att:.1f} s; ran {sorted(ran)}")

# FI3 — l'esecuzione di attesa
loads = []
real_load = RL._load


def counting_load(name):
    loads.append(name)
    return real_load(name)


RL._load = counting_load
try:
    RL.attese_mod()   # quello della push, gia' caricato dal primo giro
    loads.clear()
    times = []
    for _ in range(3):
        t0 = time.time()
        ok, txt = RL.execute({"op": "attesa", "arg": "wd0b4265780", "device": "phone"})
        times.append(time.time() - t0)
finally:
    RL._load = real_load
T.check("FI3 three attesa commands do not load cm-attese again (before: module and config at every call), and each runs in under 1 s",
        "cm-attese" not in loads and max(times) < 1, f"loads {loads}; times {[round(x, 2) for x in times]}; last {ok} {str(txt)[:120]}")

T.rm(tmp)
T.finish()
