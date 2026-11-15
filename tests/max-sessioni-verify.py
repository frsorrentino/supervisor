#!/usr/bin/env python3
"""Verifica supervisor-max-sessioni-app-13-11 (contratto 1.145): il relay manda kpi_health.sessions_max, ma il massimo
delle sessioni si cambiava solo a mano nella config (sessions.max_sessions). L'op `max_sessions_set` lo cambia dall'app.

MS1 max_sessions_set e' un comando in ops (non una lettura), offerto dallo stato; le tre fixture dello stato lo portano
MS2 arg «9» (o il numero 9) → ok, testo {"max_sessions": 9}; nella config utente cambia solo sessions.max_sessions: il
    resto del file, chiavi sconosciute comprese, e i permessi del file restano
MS3 vale subito: per il demone (la config in memoria, i consigli di cm-system, kpi_health.sessions_max) e per un
    processo nuovo (il tetto dei lanci legge la config a ogni lancio)
MS4 fuori dall'intervallo 1-20, non intero, vuoto → ok false con un errore che dice l'intervallo; la config resta
MS5 una config senza la sezione sessions la riceve; una config che non e' JSON non si tocca: rifiuto
MS6 gli esempi del contratto (cmd-result-sample …1400 e …1401) sono le risposte del codice; nella fixture ogni id c'e' una volta
"""
import importlib.util as _ilu
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

FIX = T.ROOT / "tests" / "fixtures" / "relay"
tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
rdir = tmp / "relay"
cfg = tmp / "config.json"
BASE = {"language": "en", "state_dir": str(tmp / "state"), "workspace": {"root": str(home / "ws")},
        "accounts": {"personal": {"config_dir": str(home / ".claude")}}, "default_account": "personal",
        "sessions": {"max_sessions": 5, "idle_hours": 3}, "relay": {"dir": str(rdir), "enabled": True},
        "tune": {"keep": "me"}, "una_chiave_sconosciuta": {"resta": True}}


def write_cfg(d=None):
    cfg.write_text(json.dumps(BASE if d is None else d, ensure_ascii=False, indent=2) + "\n")
    cfg.chmod(0o600)


def disk():
    return json.loads(cfg.read_text())


write_cfg()
ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "LANG": "C.UTF-8"}
os.environ.update({k: ENV[k] for k in ("CC_SUPERVISOR_CONFIG", "HOME", "CM_HOME")})
spec = _ilu.spec_from_file_location("cm_relay", T.SCRIPTS / "cm-relay.py"); RL = _ilu.module_from_spec(spec); spec.loader.exec_module(RL)


def op(arg):
    return RL.execute({"op": "max_sessions_set", "arg": arg, "by": "web"})


def fresh():
    """sessions.max_sessions come lo legge un processo nuovo (il tetto dei lanci legge la config a ogni lancio)."""
    code = ("import importlib.util as u, sys\ns = u.spec_from_file_location('c', sys.argv[1]); m = u.module_from_spec(s); s.loader.exec_module(m)\n"
            "print(m.load(warn=False)['sessions']['max_sessions'])")
    return subprocess.run([sys.executable, "-c", code, str(T.SCRIPTS / "cm-config.py")], capture_output=True, text=True, env=ENV, timeout=60).stdout.strip()


states = [json.loads((FIX / f).read_text()) for f in ("state-1-question.json", "state-2-idle.json", "state-3-stale.json")]
offered = getattr(RL, "offered_ops", lambda: list(RL.OPS))()
T.check("MS1 max_sessions_set is a command in ops, not a passive read, offered by the state; the three state fixtures carry it",
        "max_sessions_set" in RL.OPS and "max_sessions_set" not in RL.PASSIVE_OPS and "max_sessions_set" in offered
        and all("max_sessions_set" in s["ops"] and s["ops"] == list(RL.OPS) for s in states), str(RL.OPS[-3:]))

RL.SY = RL._load("cm-system")   # il demone li tiene caricati: devono vedere il valore nuovo
RL._kpi()
r9 = op("9")
d9 = disk()
T.check("MS2 arg «9» → ok, the text is {\"max_sessions\": 9}", r9[0] is True and json.loads(r9[1]) == {"max_sessions": 9}, str(r9))
T.check("MS2 in the user's config only sessions.max_sessions changes: the rest of the file, unknown keys included, and the file's mode stay",
        d9 == dict(BASE, sessions={"max_sessions": 9, "idle_hours": 3}) and oct(cfg.stat().st_mode & 0o777) == "0o600", f"{d9} {oct(cfg.stat().st_mode & 0o777)}")
T.check("MS3 at once for the daemon: its config in memory, the advice of cm-system (too_many) and kpi_health.sessions_max",
        RL.CFG["sessions"]["max_sessions"] == 9 and RL.SY.CFG["sessions"]["max_sessions"] == 9 and RL.KPI.CFG["sessions"]["max_sessions"] == 9,
        f"{RL.CFG['sessions']} {RL.SY.CFG['sessions']} {RL.KPI.CFG['sessions']}")
T.check("MS3 at once for a new process: the launch cap reads the config at every launch", fresh() == "9", fresh())
r7 = op(7)
T.check("MS2 the number 7 instead of the string is taken too", r7[0] is True and json.loads(r7[1]) == {"max_sessions": 7} and disk()["sessions"]["max_sessions"] == 7, str(r7))
r1, r20 = op("1"), op(" 20 ")
T.check("MS2 the ends of the range, 1 and 20, are taken", r1[0] is True and r20[0] is True and disk()["sessions"]["max_sessions"] == 20, f"{r1} {r20}")

before = cfg.read_text()
bad = [op(x) for x in ("0", "21", "abc", "", None, "3.5", "-1", "7x", "999", True, [9])]
T.check("MS4 out of 1-20, not an integer, empty → ok false with an error that names the range; the config stays",
        all(b == (False, "max_sessions out of range 1-20") for b in bad) and cfg.read_text() == before and RL.CFG["sessions"]["max_sessions"] == 20, str(bad))

write_cfg({k: v for k, v in BASE.items() if k != "sessions"})
r5 = op("12")
T.check("MS5 a config with no sessions section gets it, with this key only", r5[0] is True and disk()["sessions"] == {"max_sessions": 12}
        and disk()["tune"] == {"keep": "me"}, f"{r5} {disk().get('sessions')}")
cfg.write_text("{ non e' json")
r6 = op("8")
T.check("MS5 a config that is not JSON is not touched: refused", r6[0] is False and cfg.read_text() == "{ non e' json", f"{r6} {cfg.read_text()[:40]}")

write_cfg()
sample = json.loads((FIX / "cmd-result-sample.json").read_text())
cmds = {c["id"].split("-")[1]: c for c in sample["cmd"]}
res = {r["id"].split("-")[1]: r for r in sample["result"]}
ok80 = "1400" in cmds and "1401" in cmds and "1400" in res and "1401" in res
got = {n: RL.execute(cmds[n]) for n in ("1400", "1401")} if ok80 else {}
T.check("MS6 the contract's samples …1400 (a new maximum) and …1401 (out of range) are the code's answers",
        ok80 and cmds["1400"]["op"] == cmds["1401"]["op"] == "max_sessions_set"
        and all((res[n]["ok"], res[n]["text"]) == (got[n][0], got[n][1]) for n in got), f"{got} | {res.get('1400')} {res.get('1401')}")
import collections  # noqa: E402
_dup = {k: sorted(i for i, n in collections.Counter(x["id"] for x in sample[k] if isinstance(x, dict)).items() if n > 1) for k in ("cmd", "result")}
T.check("MS6 in cmd-result-sample every id is there once among the commands and once among the results (…1380 and …1381 were already approval_redo's)",
        _dup == {"cmd": [], "result": []}, str(_dup))
T.rm(tmp)
T.finish()
