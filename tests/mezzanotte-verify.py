#!/usr/bin/env python3
"""I test che contano da mezzanotte non dipendono dall'orologio vero (24/11/2026, supervisor-test-mezzanotte-24-11).

Il 24/11 il primo giro della release 0.8.203 si e' fermato su R61 transcript_deploys (relay-verify 564/566): le voci erano
scritte 40-50 s prima di time.time() e contate da RL._midnight(time.time()); la suite e' passata dalle 00:00 e sono cadute
nel giorno prima. git_published nello stesso R61 aveva la stessa finestra.

MZ1 nessun test calcola l'inizio della finestra con la mezzanotte dell'orologio vero (_midnight(time.time()))
MZ2 relay-verify ha il tratto di R61 fra i segni R61-clock-begin e R61-clock-end
MZ3 quel tratto, eseguito cosi' com'e' con TEST_NOW alle 23:59:50 e un orologio che passa la mezzanotte a meta', e' verde
MZ4 lo stesso con TEST_NOW alle 00:00:20
MZ5 la forma di prima (voci da time.time(), finestra da mezzanotte), con lo stesso orologio alle 00:00:20, e' rossa: la prova vede il difetto
"""
import datetime as _dt
import importlib.util
import json
import os
import re
import sys
import time as _time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

TESTS = Path(__file__).resolve().parent
tmp = Path(T.tmpdir())
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"personal": {"config_dir": str(tmp / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"},
                           "relay": {"dir": str(tmp / "relay")}}))
(tmp / "relay").mkdir()
os.environ.update({"HOME": str(tmp), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db")})

WALL = re.compile(r"_midnight\(\s*(?:int\(\s*)?time\.time\(\)")
hits = [f"{p.name}:{n}" for p in sorted(TESTS.glob("*.py")) if p.name != Path(__file__).name
        for n, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if WALL.search(ln) and not ln.lstrip().startswith("#")]
T.check("MZ1 no test takes the start of its window from the real clock's midnight (_midnight(time.time()))", not hits, ", ".join(hits))

src = (TESTS / "relay-verify.py").read_text(encoding="utf-8")
m = re.search(r"^# R61-clock-begin.*?^# R61-clock-end$", src, re.S | re.M)
T.check("MZ2 relay-verify has the R61 part between the marks R61-clock-begin and R61-clock-end", bool(m), "")
block = m.group(0) if m else ""

sp = importlib.util.spec_from_file_location("cm_relay_mz", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)


def local(day_off, hh, mm, ss):
    t = _time.localtime()
    return int(_time.mktime((t.tm_year, t.tm_mon, t.tm_mday + day_off, hh, mm, ss, 0, 0, -1)))


class Clock:
    """time finto: ogni chiamata a time() va avanti di 15 s, partendo da start."""
    def __init__(self, start):
        self.t = float(start)

    def time(self):
        self.t += 15
        return self.t - 15

    def __getattr__(self, name):
        return getattr(_time, name)


class Rec:
    def __init__(self):
        self.res = []

    def check(self, name, ok, detail=""):
        self.res.append((name, bool(ok), detail))


def run(code, test_now, clock_start, tag):
    rec = Rec()
    d = tmp / tag
    d.mkdir()
    ns = {"RL": RL, "tmp": d, "ROOT_WS": "/home/demo/workspaces", "json": json, "_dt": _dt, "T": rec, "TEST_NOW": test_now,
          "time": Clock(clock_start), "os": os}
    try:
        exec(compile(code, f"relay-verify R61 ({tag})", "exec"), ns)
    except Exception as e:   # noqa: BLE001 — un errore del tratto e' un esito rosso, col suo testo
        rec.res.append(("exec", False, repr(e)))
    return rec.res


def green(res):
    return bool(res) and all(ok for _, ok, _ in res)


late = local(0, 23, 59, 50)
early = local(1, 0, 0, 20)
r3 = run(block, late, late, "mz3") if block else []
T.check("MZ3 that part, run as it is with TEST_NOW at 23:59:50 and a clock that passes midnight halfway, is green",
        green(r3) and len(r3) == 2, str([(n[:40], ok, d[:200]) for n, ok, d in r3]))
r4 = run(block, early, early - 30, "mz4") if block else []
T.check("MZ4 the same with TEST_NOW at 00:00:20", green(r4) and len(r4) == 2, str([(n[:40], ok, d[:200]) for n, ok, d in r4]))
old = block.replace("_clk61 - s", "time.time() - s").replace("_clk61 - 3600", "RL._midnight(time.time())")
r5 = run(old, early, early, "mz5") if block else []
T.check("MZ5 the earlier form (entries from time.time(), window from midnight) with the clock at 00:00:20 is red: the check sees the defect",
        bool(block) and old != block and not green(r5), str([(n[:40], ok) for n, ok, _ in r5]))
T.finish()
