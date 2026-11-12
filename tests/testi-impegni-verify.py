#!/usr/bin/env python3
"""I testi degli impegni (12/11/2026, supervisor-testi-impegni-12-11: il promemoria della guardia scriveva «tocca a
l'utente», e l'intestazione «1 fermi»).

TI1 il promemoria della guardia: un compito che aspetta la persona → «aspetta l'utente»; uno che aspetta una sessione →
    «tocca a master»; mai «tocca a l'utente»
TI2 l'intestazione di `task impegni`: «Impegni (1, 1 fermo):» con un compito fermo, «(2, 2 fermi)» con due, «0 fermi» con
    nessuno; in inglese «1 stalled»
TI3 il riepilogo invariato dell'hook: «Impegni: 1, 1 fermo …»
"""
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
cfg = tmp / "c.json"


def write_cfg(lang):
    cfg.write_text(json.dumps({"language": lang, "state_dir": str(tmp / "s"), "workspace": {"root": str(home), "root_session_name": "master"},
                               "accounts": {"p": {"config_dir": str(home / ".claude")}}, "default_account": "p", "relay": {"dir": str(tmp / "r")}}))


write_cfg("it")
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASKS_DB": str(tmp / "t.db")})


def load(name, tag):
    sp = importlib.util.spec_from_file_location(tag, T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


# TI1 — la guardia, con stall-check e talk finti
G = load("cm-guard", "cm_guard_ti")
ITEMS = [{"task": "app-mockup-formato-unico-11-11", "idle_s": 7200, "waits": {"on": "person"}, "next": "scegli il formato"},
         {"task": "supervisor-x", "idle_s": 10800, "waits": {"on": "master"}, "next": "rilancia"}]
TALKS = []


def fake_run(cmd, **kw):
    if cmd[1:4] == ["task", "stall-check", "--json"]:
        return types.SimpleNamespace(stdout=json.dumps(ITEMS), returncode=0)
    if cmd[1] == "talk":
        TALKS.append(cmd[3])
    return types.SimpleNamespace(stdout="", returncode=0)


G.subprocess = types.SimpleNamespace(run=fake_run, TimeoutExpired=G.subprocess.TimeoutExpired)
G.quiet_now = lambda t: True   # solo la master: niente relay, niente Telegram
G.log = lambda *_a, **_k: None
G.stalled_alerts(0)
txt = TALKS[0] if TALKS else ""
T.check("TI1 the guard's reminder: a task waiting for the person → «aspetta l'utente»; one waiting for a session → «tocca a master»; never «tocca a l'utente»",
        "app-mockup-formato-unico-11-11 (2 h): aspetta l'utente; prossimo: scegli il formato" in txt
        and "supervisor-x (3 h): tocca a master; prossimo: rilancia" in txt and "tocca a l'utente" not in txt, txt)

# TI2 — l'intestazione di task impegni
TK = load("cm-tasks", "cm_tasks_ti")


def it_(stalled, waits="person"):
    return {"task": "t", "session": "s", "state": "running", "approved": True, "approved_at": 1, "last_step": None, "age_s": 60, "idle_s": 60,
            "next": "n", "waits": {"on": waits}, "paused": None, "stalled": stalled, "expected": None}


h1 = TK.render_commitments([it_(True)]).splitlines()[0]
h2 = TK.render_commitments([it_(True), it_(True)]).splitlines()[0]
h0 = TK.render_commitments([it_(False)]).splitlines()[0]
write_cfg("en")
TKen = load("cm-tasks", "cm_tasks_ti_en")
hen = TKen.render_commitments([it_(True)]).splitlines()[0]
T.check("TI2 task impegni header: «Impegni (1, 1 fermo):», «(2, 2 fermi)», «(1, 0 fermi)»; in English «1 stalled»",
        h1 == "Impegni (1, 1 fermo):" and h2 == "Impegni (2, 2 fermi):" and h0 == "Impegni (1, 0 fermi):" and hen == "Commitments (1, 1 stalled):",
        f"{h1} | {h2} | {h0} | {hen}")

# TI3 — il riepilogo invariato, con lo stesso testo
write_cfg("it")
CC = load("cm-config", "cm_config_ti")
c = CC.load(cfg, warn=False)
one = CC.msg(c, "tasks.unchanged", n=1, stalled=CC.msg(c, "tasks.stalled_one", count=1))
T.check("TI3 the unchanged summary: «Impegni: 1, 1 fermo …»", one.startswith("Impegni: 1, 1 fermo (invariati"), one)
src = (T.SCRIPTS / "cm-hook.py").read_text()
T.check("TI3 cm-hook builds it with the singular for one (tasks.stalled_one) and never passes k= to M",
        '"tasks.stalled_one" if k_ == 1 else "tasks.stalled_many", count=k_' in src, "")
T.rm(tmp)
T.finish()
