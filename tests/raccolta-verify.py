#!/usr/bin/env python3
"""Funzione 10 del programma approvato il 01/11/2026 (contratto 1.100): avvio a pieno regime. Tutto in una HOME temporanea.

prima_raccolta  PR1 una raccolta riempie le sezioni con dati veri e dice perche' sono vuote le altre; PR2 nessun modello e
                bassa priorita'; PR3 consenso una volta sola, senza consenso nessuna trascrizione aperta; PR4 con consenso:
                conto e seguiti veri; PR5 interrotta riprende, non rifa' il fatto; PR6 una sola alla volta; PR7 op e /state;
                PR8 il primo giro: costo detto prima, parte solo da un tocco, una volta; PR9 fill non toglie i seguiti
avvio           AV1 livelli dallo stato reale, nuova / a meta' / completa; AV2 piu' tardi e mai; AV3 il copione solo alla master
                e solo se serve; AV4 init propone la master e con --cron scrive la config; AV5 README e copioni
"""
import importlib.util
import json
import re
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
conf = home / ".claude"
(conf / "plugins").mkdir(parents=True)
(conf / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {"demo@market": [{"version": "1.0.0"}]}}))
(conf / "skills" / "una").mkdir(parents=True)
(conf / "skills" / "una" / "SKILL.md").write_text("---\nname: una\ndescription: d\n---\n")
root = tmp / "ws"
(root / "atlas-shop").mkdir(parents=True)
(root / "orbit-site").mkdir()
state_dir = tmp / "state"
relay_dir = tmp / "relay"
relay_dir.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state_dir), "accounts": {"personal": {"config_dir": str(conf)}},
                           "default_account": "personal", "workspace": {"root": str(root), "root_session_name": "master", "project_dirs": ["."]},
                           "relay": {"dir": str(relay_dir)}}))
fakebin = tmp / "bin"
fakebin.mkdir()
claude_log = tmp / "claude.log"
(fakebin / "claude").write_text(f'#!/bin/sh\necho "$*" >> {claude_log}\nexit 0\n')
(fakebin / "claude").chmod(0o755)
cron = tmp / "cron.txt"
cron.write_text("")
(fakebin / "fake-crontab").write_text(f'#!/bin/sh\nif [ "$1" = "-l" ]; then cat {cron}; else cat > {cron}; fi\n')
(fakebin / "fake-crontab").chmod(0o755)
talk_log = tmp / "talk.log"
fake_cm = tmp / "fake-cm"
fake_cm.write_text(f'#!/bin/sh\necho "$*" >> {talk_log}\nexit 0\n')
fake_cm.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR", "CM_RELAY_CM")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "PATH": f"{fakebin}:{ENV['PATH']}",
            "CM_CRONTAB_CMD": str(fakebin / "fake-crontab"), "CM_TASKS_DB": str(tmp / "tasks.db"), "CM_SUGGESTIONS": str(tmp / "suggestions.json"),
            "CM_RELAY_STATE": str(tmp / "no-state.json"), "CM_OBSERVE_DIR": str(tmp / "no-observe")})
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")
STATUS = state_dir / "prima-raccolta.json"


def run(*args, stdin=None, env=None):
    p = subprocess.run([CLI, *args], capture_output=True, text=True, env=dict(ENV, **(env or {})), input=stdin, timeout=120)
    return p.returncode, p.stdout, p.stderr


def status():
    return json.loads(STATUS.read_text())


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), T.SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def transcript(project, sid, step, age=3600):
    d = conf / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(project))   # come Claude Code (12/11: «_» di mkdtemp compreso)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in [
        {"type": "user", "cwd": str(project), "message": {"role": "user", "content": "SEGRETO-DEL-PROMPT"}},
        {"type": "assistant", "cwd": str(project), "message": {"role": "assistant", "content": [{"type": "text", "text": f"Esito: fatto.\nProssimi: {step} · altra cosa"}]}}]) + "\n")
    t = time.time() - age
    os.utime(f, (t, t))
    return f


# ---------------------------------------------------------------- PR1, PR2, PR3: la raccolta, senza consenso
for i in range(3):
    transcript(root / "atlas-shop", f"s{i}", f"pubblica la pagina {i}", age=3600 * (i + 1))
transcript(root / "orbit-site", "old", "roba vecchia", age=86400 * 40)
(root / "slash-only").mkdir()
transcript(root / "slash-only", "sl", "/clear", age=7200)
rc, out, err = run("prima-raccolta", "--help")
T.check("prima_raccolta the command exists and has a help", rc == 0 and "--sessions" in out, out + err)
rc, out, err = run("prima-raccolta", "--json", stdin="")
d = json.loads(out) if out.startswith("{") else {}
sec = {s["key"]: s for s in d.get("sections") or []}
T.check("prima_raccolta PR1 no consent asked yet: the state says it", rc == 0 and d.get("status") == "ask" and d.get("consent") is None, out + err)
T.check("prima_raccolta PR1 the sections are all there", set(sec) >= {"machine", "hosts", "quota", "plugins", "sessions", "live", "projects", "suggestions", "night", "recap"}, str(sorted(sec)))
T.check("prima_raccolta PR1 projects counted for real", sec.get("projects", {}).get("n") == 3 and "3" in sec["projects"]["text"], str(sec.get("projects")))
T.check("prima_raccolta PR1 plugins and skills counted for real", sec.get("plugins", {}).get("n") == 1 and "1" in sec["plugins"]["text"], str(sec.get("plugins")))
T.check("prima_raccolta PR1 machine read from the real /proc", sec.get("machine", {}).get("n") == 1 and "core" in sec["machine"]["text"], str(sec.get("machine")))
T.check("prima_raccolta PR1 an empty section says why and when, invents nothing",
        sec.get("quota", {}).get("n") == 0 and sec["quota"].get("why") and sec["quota"].get("when") and not sec["quota"].get("text"), str(sec.get("quota")))
T.check("prima_raccolta PR1 every section points to a screen the app knows", all(s["dove"] in ("queue", "box-aspetta", "box-lavoro", "box-esiti", "lavori", "launch", "night", "search", "settings", "homeorder", "usage", "home") for s in sec.values()), str([s["dove"] for s in sec.values()]))
_log = claude_log.read_text() if claude_log.exists() else ""
T.check("prima_raccolta PR2 no model call: claude only for the marketplace", "marketplace update" in _log and all("marketplace" in x for x in _log.splitlines()), _log)
T.check("prima_raccolta PR2 no prompt reaches a session", not talk_log.exists(), "")
_src = (T.SCRIPTS / "cm-raccolta.py").read_text()
T.check("prima_raccolta PR2 low priority set in the code", "os.nice(19)" in _src, "")
T.check("prima_raccolta PR3 without consent no transcript read: no count, no follow-up",
        sec["sessions"]["n"] == 0 and status()["steps"]["sessions"]["state"] == "waiting" and not json.loads(Path(ENV["CM_SUGGESTIONS"]).read_text() if Path(ENV["CM_SUGGESTIONS"]).exists() else "{}").get("items"), str(sec["sessions"]))
_r = load("cm-raccolta")
_opened = []
_orig = _r.read_tail
_r.read_tail = lambda f: (_opened.append(f), _orig(f))[1]
_r.step_sessions({"consent": False, "steps": {}})
T.check("prima_raccolta PR3 the step itself opens nothing without consent", not _opened, str(_opened))
_r.read_tail = _orig
rc, out, err = run("prima-raccolta", "--sessions", "no", "--json")
d = json.loads(out)
T.check("prima_raccolta PR3 a no is kept and asked no more", d["consent"] is False and d["status"] == "done" and status()["steps"]["sessions"]["state"] == "skipped", out)
rc, out, err = run("prima-raccolta", "--json", stdin="si\n")
T.check("prima_raccolta PR3 asked once: later runs keep the no", json.loads(out)["consent"] is False, out)

# ---------------------------------------------------------------- PR4: con consenso
rc, out, err = run("prima-raccolta", "--sessions", "si", "--json")
d = json.loads(out)
sec = {s["key"]: s for s in d["sections"]}
T.check("prima_raccolta PR4 yes: the recent sessions counted, the 40-day-old one out", d["consent"] is True and sec["sessions"]["n"] == 4 and "4" in sec["sessions"]["text"], str(sec["sessions"]))
_items = json.loads(Path(ENV["CM_SUGGESTIONS"]).read_text())["items"]
T.check("prima_raccolta PR4 the follow-ups come from the sessions' own «Prossimi», newest first", [x["text"].count("pubblica la pagina 0") for x in _items][:1] == [1] and all(x["kind"] == "followup" and x["source"] == "script" and x["key"].startswith("raccolta:") for x in _items), str(_items))
T.check("prima_raccolta PR4 a slash command is not a next step", not any("/clear" in x["text"] for x in _items) and any("altra cosa" in x["text"] for x in _items), str(_items))
T.check("prima_raccolta PR4 no prompt text of a past session is stored", "SEGRETO" not in STATUS.read_text() and "SEGRETO" not in Path(ENV["CM_SUGGESTIONS"]).read_text(), "")
rc, out, err = run("prima-raccolta", "--sessions", "si", "--json")
T.check("prima_raccolta PR4 a second pass does not double them", len(json.loads(Path(ENV["CM_SUGGESTIONS"]).read_text())["items"]) == len(_items), "")

# ---------------------------------------------------------------- PR9: fill le lascia stare
_sg = load("cm-suggest")
_keys = {x["key"] for x in _sg.load()["items"]}
_sg.fill(state={"sessions": []}, commitments=[])
T.check("prima_raccolta PR9 the hourly fill keeps the follow-ups", {x["key"] for x in _sg.load()["items"]} >= _keys and _keys, str(_keys))

# ---------------------------------------------------------------- PR5, PR6: interrotta e ripresa
STATUS.unlink()
rc, out, err = run("prima-raccolta", "--sessions", "si", "--json", env={"CM_RACCOLTA_STOP_AT": "plugins"})
T.check("prima_raccolta PR5 interrupted: says so, exits non zero", rc == 130 and status()["status"] == "interrupted", f"{rc} {out}{err}")
_st = status()["steps"]
T.check("prima_raccolta PR5 the finished steps are recorded, the next not", _st["machine"]["state"] == "done" and "plugins" not in _st, str(_st))
_at = _st["machine"]["at"]
time.sleep(1.1)
rc, out, err = run("prima-raccolta", "--json")
T.check("prima_raccolta PR5 resumes: done, without redoing what was done", rc == 0 and status()["status"] == "done" and status()["steps"]["machine"]["at"] == _at and status()["steps"]["plugins"]["state"] == "done", out + err)
STATUS.write_text(json.dumps(dict(status(), cursor=2, steps={k: v for k, v in status()["steps"].items() if k != "sessions"}, partial={"n": 2, "follow": []})))
_r = load("cm-raccolta")
_s = dict(_r.read_status(), consent=True)
_seen = []
_orig = _r.read_tail
_r.read_tail = lambda f: (_seen.append(f), _orig(f))[1]
_res = _r.step_sessions(_s)
_r.read_tail = _orig
T.check("prima_raccolta PR5 the batches resume from the cursor", len(_seen) == 2 and _res["n"] == 4, f"{len(_seen)} {_res}")
_sl = subprocess.Popen(["sleep", "30"])
STATUS.write_text(json.dumps(dict(status(), status="running", pid=_sl.pid)))
rc, out, err = run("prima-raccolta", "--json")
T.check("prima_raccolta PR6 one at a time: a live pid refuses", rc == 1 and status()["pid"] == _sl.pid, out + err)
_sl.send_signal(signal.SIGKILL)
_sl.wait()
rc, out, err = run("prima-raccolta", "--json")
T.check("prima_raccolta PR6 a dead pid does not block", rc == 0 and status()["status"] == "done", out + err)

# ---------------------------------------------------------------- PR7, PR8: relay
ENV["CM_RELAY_CM"] = str(fake_cm)
os.environ["CM_RELAY_CM"] = str(fake_cm)
sys.path.insert(0, str(T.SCRIPTS))
RL = load("cm-relay")
RC = RL.C if False else None
_ok, _txt = RL.execute({"id": "x", "op": "prima_raccolta"})[:2]
_d = json.loads(_txt)
T.check("prima_raccolta PR7 op prima_raccolta reads the list", _ok and _d["status"] == "done" and {s["key"] for s in _d["sections"]} >= {"projects", "plugins"}, _txt[:200])
T.check("prima_raccolta PR7 the op is a passive read", "prima_raccolta" in RL.PASSIVE_OPS and "prima_raccolta_azione" not in RL.PASSIVE_OPS and {"prima_raccolta", "prima_raccolta_azione"} <= set(RL.OPS), "")
T.check("prima_raccolta PR8 the cost of the first round is declared in the read", _d["first_round"]["at"] is None and _d["first_round"]["cost"], str(_d["first_round"]))
_S = RL.S
_sum = load("cm-raccolta").summary()
_st = _S.build_state({"prima_raccolta": _sum, "sessions": [], "projects": []}, 1)
T.check("prima_raccolta PR7 /state carries only the small block", set(_st["prima_raccolta"]) == {"status", "done", "total", "ask", "first_round_at"}, str(_st.get("prima_raccolta")))
_ok, _t = RL.execute({"id": "y", "op": "prima_raccolta_azione", "action": "first_round"})[:2]
T.check("prima_raccolta PR8 no touch, no round: a session cannot spend the quota", not _ok and not talk_log.exists(), _t)
_ok, _t = RL.execute({"id": "y", "op": "prima_raccolta_azione", "action": "first_round", "device": "phone"})[:2]
_tl = talk_log.read_text() if talk_log.exists() else ""
T.check("prima_raccolta PR8 a touch sends the round to the master", _ok and "talk master" in _tl and "suggest add" in _tl, _t + _tl)
_ok, _t = RL.execute({"id": "y", "op": "prima_raccolta_azione", "action": "first_round", "device": "phone"})[:2]
T.check("prima_raccolta PR8 once only", _ok and talk_log.read_text().count("talk master") == 1, _t)
_ok, _t = RL.execute({"id": "z", "op": "prima_raccolta_azione", "action": "consent", "value": "no"})[:2]
T.check("prima_raccolta PR3 consent from the phone needs a touch", not _ok, _t)
_ok, _t = RL.execute({"id": "z", "op": "prima_raccolta_azione", "action": "boh", "device": "phone"})[:2]
T.check("prima_raccolta PR7 an unknown action refuses", not _ok, _t)

_sp = []
_pp = RL.subprocess.Popen
RL.subprocess.Popen = lambda *a, **k: _sp.append(a[0])
RL.raccolta_after_pair()
T.check("prima_raccolta PR7 a first pairing does not start a second collection", not _sp, str(_sp))
STATUS.rename(STATUS.with_suffix(".tmp"))
RL.raccolta_after_pair()
RL.subprocess.Popen = _pp
STATUS.with_suffix(".tmp").rename(STATUS)
T.check("prima_raccolta PR7 a first pairing starts it when it never ran", len(_sp) == 1 and "prima-raccolta" in _sp[0], str(_sp))

# ---------------------------------------------------------------- fixture del contratto
_fp = json.loads((T.ROOT / "tests/fixtures/relay/cmd-result-sample.json").read_text())
T.check("cmd-result-sample every result carries `at`, as run_cmd sends it", all(isinstance(r.get("at"), int) for r in _fp["result"]), str([r["id"] for r in _fp["result"] if "at" not in r]))
_fr = {r["id"]: r for r in _fp["result"]}
_fx = next(json.loads(_fr[c["id"]]["text"]) for c in _fp["cmd"] if c["op"] == "prima_raccolta")
T.check("prima_raccolta PR7 the contract's sample has the keys the code sends", set(_fx) == set(_d) and set(_fx["sections"][0]) == set(_d["sections"][0]) and set(_fx["first_round"]) == set(_d["first_round"]), str(sorted(_d)))
_f2 = json.loads((T.ROOT / "tests/fixtures/relay/state-3-stale.json").read_text())
T.check("prima_raccolta PR7 the sample state has the block and the ops", set(_f2["prima_raccolta"]) == set(_st["prima_raccolta"]) and {"prima_raccolta", "prima_raccolta_azione"} <= set(_f2["ops"]), "")
_h = subprocess.run([CLI, "task", "prova"], capture_output=True, text=True, env=ENV)
T.check("task prova's help lists the screens with «Portami lì»", "box-aspetta" in _h.stderr + _h.stdout and "homeorder" in _h.stderr + _h.stdout, _h.stderr)

# ---------------------------------------------------------------- AV1, AV2: i livelli
OB = state_dir / "onboarding.json"


def levels():
    rc, out, err = run("doctor", "--onboarding", "--json")
    return json.loads(out) if out.startswith("{") else {"err": out + err}


_l = levels()
T.check("avvio AV1 fresh: nothing done, level 0, next 1", _l.get("reached") == 0 and _l.get("next") == 1 and _l.get("mode") == "new", str(_l))
_c0 = json.loads(cfg.read_text())
cfg.write_text(json.dumps(dict(_c0, relay=dict(_c0.get("relay") or {}, enabled=True, web={"enabled": True}))))
OB.unlink()
_l = levels()
T.check("avvio AV1 (11/11) the web app in this computer's browser, with no Firebase and no device, is level 2; the phone (3) stays open",
        _l["levels"]["2"]["state"] == "done" and _l["levels"]["3"]["state"] == "open", str(_l))
cfg.write_text(json.dumps(_c0))
(relay_dir / "devices.json").write_text(json.dumps({"u1": {"name": "p", "paired_at": 1}}))
OB.unlink()
_l = levels()
T.check("avvio AV1 halfway: a paired device is level 2, recovery mode", _l["levels"]["2"]["state"] == "done" and _l["mode"] == "recovery" and _l["levels"]["3"]["state"] == "open", str(_l))
_c = json.loads(cfg.read_text())
_c["relay"]["firebase_url"] = "https://demo.example"
cfg.write_text(json.dumps(_c))
(relay_dir / "devices.json").write_text(json.dumps({"u1": {"name": "p", "paired_at": 1}, "u2": {"name": "w", "paired_at": 1, "kind": "watch"}}))
cron.write_text("0 2 * * * supervisor night run --send\n")
(fakebin / "fake-cm-reg").write_text('#!/bin/sh\necho \'{"sessioni":[{"nome":"master"},{"nome":"atlas-shop"}]}\'\n')
(fakebin / "fake-cm-reg").chmod(0o755)
_l = load  # noqa
_e = {"CM_ONBOARDING_REGISTRY": str(fakebin / "fake-cm-reg")}
ENV.update(_e)
_l = levels()
T.check("avvio AV1 full: every level from the real state", all(_l["levels"][str(i)]["state"] == "done" for i in range(1, 6)) and _l["next"] is None, str(_l))
_c["sessions"] = {"max_sessions": 9}
cfg.write_text(json.dumps(_c))
rc, out, err = run("doctor", "--onboarding")
T.check("avvio AV1 the plain output says where you are", rc == 0 and "5" in out, out + err)
T.check("avvio AV1 a setting different from the initial one is listed, not changed", "sessions.max_sessions" in out and json.loads(cfg.read_text())["sessions"]["max_sessions"] == 9, out + err)
del _c["sessions"]
(relay_dir / "devices.json").write_text("{}")
OB.unlink()
cron.write_text("")
_c["relay"]["firebase_url"] = ""
cfg.write_text(json.dumps(_c))
rc, out, err = run("doctor", "--onboarding", "--mark", "2", "later")
_l = levels()
T.check("avvio AV2 later: next level moves on until tomorrow", _l["levels"]["2"]["state"] == "later" and _l["levels"]["2"]["until"] > time.time(), str(_l))
run("doctor", "--onboarding", "--mark", "3", "never")
_l = levels()
T.check("avvio AV2 never stays refused", _l["levels"]["3"]["state"] == "refused", str(_l))
rc, out, err = run("doctor", "--onboarding", "--mark", "9", "never")
T.check("avvio AV2 a wrong mark refuses", rc != 0, out)

# ---------------------------------------------------------------- AV3: l'iniezione
OB.unlink()
H = T.SCRIPTS / "cm-hook.py"


def hook(name, source="startup"):
    e = dict(ENV, CM_HOOK_TMUX_NAME=name)
    p = subprocess.run(["python3", str(H), "SessionStart"], input=json.dumps({"session_id": "s", "cwd": str(root), "source": source}), capture_output=True, text=True, env=e, timeout=60)
    return p.stdout


_m = (T.ROOT / "supervisor/onboarding_it.md").read_text().strip().splitlines()[0]
T.check("avvio AV3 the master gets the script when a level is open", _m in hook("master"), "")
T.check("avvio AV3 another session never", _m not in hook("atlas-shop"), "")
T.check("avvio AV3 not after a compaction", _m not in hook("master", "compact"), "")
_all = {str(i): {"state": "refused"} for i in range(1, 6)}
OB.write_text(json.dumps({"levels": _all, "mode": "recovery"}))
T.check("avvio AV3 not when nothing is left to propose", _m not in hook("master"), "")

# ---------------------------------------------------------------- AV4: init
h2 = tmp / "h2"
(h2 / ".claude").mkdir(parents=True)
_e2 = dict(ENV, HOME=str(h2), CM_HOME=str(h2), CC_SUPERVISOR_CONFIG=str(h2 / "c.json"))
_p = subprocess.run([CLI, "init", "--yes", "--shim", "--shell", "--cron", "--tmux"], capture_output=True, text=True, env=_e2, input="", timeout=60)
T.check("avvio AV4 the single install line also writes the config", (h2 / "c.json").is_file(), _p.stdout + _p.stderr)
T.check("avvio AV4 init ends by proposing the master and the collection", "supervisor launch" in _p.stdout and "prima-raccolta" in _p.stdout, _p.stdout[-400:])

# ---------------------------------------------------------------- AV5: testi
_rd = (T.ROOT / "README.md").read_text()
T.check("avvio AV5 README: the collection and the five levels", "supervisor prima-raccolta" in _rd and all(f"| {i}. " in _rd for i in range(1, 6)), "")
# PR10 (1.139, 12/11/2026, supervisor-avvio-popola-12-11): una domanda sola, corta, coi tasti; il si' fa tutto; le prime voci
os.environ["CM_MONITORS"] = str(tmp / "monitors10")
os.environ["CM_TASKS_DB"] = str(tmp / "tasks10.db")
R10 = load("cm-raccolta")
R10.save({"status": "done", "steps": {}, "consent": None})
v10 = R10.view()
T.check("prima_raccolta PR10 the question is one short line, with the small note (data on this computer, a small part of the quota) and the short buttons from the relay; ask_text as before",
        v10["ask"] and v10["ask"]["text"] == "Per completare la configurazione, effettua la scansione delle sessioni." and "\n" not in v10["ask"]["text"]
        and "questo computer" in v10["ask"]["note"] and "quota" in v10["ask"]["note"] and v10["ask"]["yes"] == "Scansiona" and v10["ask"]["later"] == "Non ora"
        and v10["ask"]["first_round"] is True and v10["ask_text"] == v10["ask"]["text"], json.dumps(v10.get("ask"), ensure_ascii=False))
R10.ask_later()
v10b, s10b = R10.view(), R10.summary()
T.check("prima_raccolta PR10 «Not now» (consent later): no consent written, the question gone for a day (ask null, summary ask false)",
        v10b["ask"] is None and v10b["ask_text"] is None and v10b["consent"] is None and s10b["ask"] is False
        and R10.read_status()["ask_after"] >= time.time() + 86000, json.dumps([v10b.get("ask"), s10b]))
# riproposta (il maintainer alle 16:32): a chi ha installato prima e non ha fatto la scansione
cases10 = {"mai partita": None, "no alla vecchia": {"status": "done", "steps": {}, "consent": False},
           "no alla nuova": {"status": "done", "steps": {}, "consent": False, "asked_v": 2},
           "si senza primo giro": {"status": "done", "steps": {}, "consent": True},
           "tutto fatto": {"status": "done", "steps": {}, "consent": True, "first_round_at": 1}}
got10 = {}
for k_, st_ in cases10.items():
    R10.path().unlink(missing_ok=True)
    if st_ is not None:
        R10.save(st_)
    sm_ = R10.summary()
    got10[k_] = (bool((R10.view() or {}).get("ask")), (sm_ or {}).get("ask"))
R10.save({"status": "done", "steps": {}, "consent": False})
R10.set_consent(False)
again10 = bool(R10.view()["ask"])
T.check("prima_raccolta PR10 existing installs: never ran or never answered → asked (also in state); a no to the old question → asked once with the new text, a no to the new one → not again; yes without the first round → asked; all done → nothing",
        got10 == {"mai partita": (True, True), "no alla vecchia": (True, True), "no alla nuova": (False, False), "si senza primo giro": (True, True), "tutto fatto": (False, None)}
        and again10 is False, str(got10) + str(again10))
talk_log.unlink(missing_ok=True)
R10.save({"status": "done", "steps": {}, "consent": True})
fr1, fr2 = R10.first_round(cli=str(fake_cm)), R10.first_round(cli=str(fake_cm))
T.check("prima_raccolta PR10 with the yes the master's first round starts by itself, once (prima-raccolta --first-round at the end of the collection)",
        fr1 is True and fr2 is False and talk_log.read_text().count("talk master") == 1 and "--first-round" in (T.SCRIPTS / "cm-relay-cmds.py").read_text(), talk_log.read_text()[-200:])
R10.quota_monitor({"personal": {"stato": "ok", "settimana_pct": 18}})
MO10 = load("cm-monitor")
c10 = MO10.card("consumo-quota") if "consumo-quota" in MO10.ids() else {}
R10.quota_monitor({"personal": {"stato": "ok", "settimana_pct": 21}})
c10b = MO10.card("consumo-quota") if "consumo-quota" in MO10.ids() else {}
T.check("prima_raccolta PR10 the first item of Monitoraggi: an example on the week's quota (meter, example true), a point at every collection",
        c10.get("example") is True and c10.get("form") == "meter" and c10.get("value") == "18%" and c10b.get("value") == "21%" and c10b["series"][0]["n"] == 2, json.dumps(c10b)[:400])
os.environ["CM_MONITORS"] = str(tmp / "monitors10b")
(tmp / "monitors10b" / "mio").mkdir(parents=True)
(tmp / "monitors10b" / "mio" / "def.json").write_text(json.dumps({"id": "mio", "title": "Mio", "form": "tile", "series": [], "alerts": []}))
R10.quota_monitor({"personal": {"stato": "ok", "settimana_pct": 18}})
T.check("prima_raccolta PR10 with a monitor of one's own no example is added", load("cm-monitor").ids() == ["mio"], str(load("cm-monitor").ids()))
n10 = R10.first_steps()
n10b = R10.first_steps()
sh10 = subprocess.run([CLI, "task", "show", "primi-passi"], capture_output=True, text=True, env=dict(ENV, CM_TASKS_DB=str(tmp / "tasks10.db")))
pv10 = subprocess.run([CLI, "task", "prove", "--json"], capture_output=True, text=True, env=dict(ENV, CM_TASKS_DB=str(tmp / "tasks10.db")))
T.check("prima_raccolta PR10 the first item of Verifica on a new install: the task primi-passi with the test «send a message from the phone»; a registry already used gets nothing",
        n10 and n10b is None and sh10.returncode == 0 and "messaggio" in pv10.stdout and "primi-passi" in pv10.stdout, sh10.stderr[-300:] + pv10.stdout[-300:])
for lang in ("it", "en"):
    _t = (T.ROOT / f"supervisor/onboarding_{lang}.md").read_text()
    T.check(f"avvio AV5 script {lang}: one step at a time, secrets never in chat, the three buttons, the collection", "prima-raccolta" in _t and "doctor --onboarding" in _t and "--mark" in _t, "")
T.finish()
