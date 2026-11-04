#!/usr/bin/env python3
"""supervisor-ok-ovunque-04-11 (04/11/2026 17:23, the maintainer from the phone: «se approvo da sessione o da sezioni come prova
decidi etc deve essere equivalente, non posso approvare in un punto e doverlo rifare altrove»): una risposta della persona
vale una volta sola, dovunque l'abbia data. Tutto su un registro temporaneo e un tmux privato.

chat     OV1 il caso del 04/11: «tutto ok» alla master sulle prove → `task ok --prove` le segna, `task confirmed` verde, Prova
         vuota, il controllo gira, la sessione del compito lo sa; OV2 `task confirm` con prove in vigore le segna (prima restava
         rossa); OV3 senza --prove solo le prove scritte prima del messaggio; OV4 carta con scelta: senza --choice nulla,
         con --choice approvata e sparita da Decidi; OV5 l'ok nella sessione del compito: lei non riceve l'avviso, la master
         si'; OV6 pausa di prova: Tutto va + controllo; OV7 pausa qualsiasi: chiusa, niente controllo; OV8 --ko su una prova;
         OV9-OV10 --ko rifiutato quando vale per voci diverse o per una carta; OV11 nessuna voce: la conferma; OV12 le
         guardie della casella (origine session, casella d'altri, senza --inbox, compito chiuso); OV13 «Tocca a te» spento;
         OV14 una scelta non vale per due carte
app      OA1 un tocco su Prova arriva alla sessione del compito (ok e non va); OA2 una voce risposta in chat sparisce dalle
         letture del relay, e una risposta nell'app sparisce dall'elenco dell'hook
hook     OH1 al messaggio della persona, le voci della sessione col comando unico e l'id del messaggio; OH2 alla master tutte;
         OH3 niente senza voci; OH4 un messaggio del telefono: l'id e' quello del relay
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
proj = tmp / "proj"
proj.mkdir()
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
state_dir = tmp / "state"
state_dir.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state_dir), "accounts": {"personal": {"config_dir": str(home / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"},
                           "relay": {"dir": str(tmp / "relay")}, "registry": {"file": str(state_dir / "sessions.json")},
                           "hooks": {"local_time": {"enabled": False}, "session_kernel": {"enabled": False}},
                           "tasks": {"commitments_prompt": False}}))
talk_log = tmp / "talk.log"
fake_cm = tmp / "fake-cm"
fake_cm.write_text(f'#!/bin/sh\necho "$*" >> {talk_log}\nexit 0\n')
fake_cm.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA", "CM_RELAY_CM", "CM_HOOK_TMUX_NAME")}
ENV.update({"CM_TASKS_DB": str(tmp / "tasks.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home),
            "CM_TASK_TALK_BIN": str(fake_cm), "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CM_PROC_SCAN_PIDS": "", "CM_INBOX_WAIT_S": "1"})
os.environ.update(ENV)
for _k in ("CLAUDECODE", "CM_APPROVE_VIA", "TMUX_PANE"):
    os.environ.pop(_k, None)
CLI = str(T.SCRIPTS / "supervisor")


def cm(*args, stdin=None, env=None):
    p = subprocess.run([CLI, "task", *args], input=stdin, capture_output=True, text=True, env=dict(ENV, **(env or {})), timeout=60)
    return p.returncode, p.stdout, p.stderr


def add(tid, session="sess", check="true"):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"unit {tid}", "plan": None,
                                       "where": {"project": str(proj), "session": session, "host": "local", "account": None},
                                       "check": {"cmd": check, "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open",
                                       "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal",
                                       "requested_by": "test"}}
    return cm("add", "-", stdin=json.dumps(c))


def prova(tid, fai="tocca", build="b1"):
    return int(cm("prova", tid, "--build", build, "--dove", "home", "--fai", fai, "--atteso", "si apre")[1].strip())


def show(tid):
    return json.loads(cm("show", tid, "--json")[1])


def prove():
    return json.loads(cm("prove", "--json")[1])


def decisioni():
    return json.loads(cm("decisioni", "--json")[1])


def in_decidi(tid):
    return any(i.get("task") == tid for g in decisioni()["groups"] for i in g["items"])


def confirmed(tid):
    return cm("confirmed", tid)[0] == 0


def talks():
    return talk_log.read_text() if talk_log.exists() else ""


def reset_talks():
    talk_log.write_text("")


def inbox_put(to, text, origin, age_s=0):
    code = ("import importlib.util,sys,json;s=importlib.util.spec_from_file_location('i',sys.argv[1]);I=importlib.util.module_from_spec(s);"
            "s.loader.exec_module(I);r=I.put(sys.argv[2],sys.argv[3],'supervisor',origin=sys.argv[4]);"
            "p=I._path_of(r['id']);d=json.loads(p.read_text());d['created']-=float(sys.argv[5]);p.write_text(json.dumps(d));print(r['id'])")
    return subprocess.run([sys.executable, "-c", code, str(T.SCRIPTS / "cm-inbox.py"), to, text, origin, str(age_s)],
                          capture_output=True, text=True, env=ENV, timeout=30).stdout.strip()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RL = load("cm_relay", T.SCRIPTS / "cm-relay.py")
TALKS, SPAWNS, WAKES = [], [], []
RL.relay_talk = lambda name, text, *o: (TALKS.append((name, text)), (0, ""))[1]
RL._spawn_cm = lambda *a: SPAWNS.append(a)
RL._wake = lambda tm, text: WAKES.append((tm, text))
RL.clear_needs_you = lambda tm: None
PHONE = {"device": "phone", "by": "maintainer"}
TK = load("cm_tasks_ov", T.SCRIPTS / "cm-tasks.py")


def hook(name, prompt):
    p = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "UserPromptSubmit"],
                       input=json.dumps({"session_id": f"sid-{name}", "prompt": prompt, "cwd": str(proj)}),
                       capture_output=True, text=True, env=dict(ENV, CM_HOOK_TMUX_NAME=name), timeout=60)
    return p.stdout


with T.PrivateTmux() as tm:
    for s in ("master", "sess", "altra"):
        tm("new-session", "-d", "-s", s, "sleep 600")
    pane = {s: tm("display-message", "-p", "-t", s, "#{pane_id}").stdout.strip() for s in ("master", "sess", "altra")}
    AS = {s: dict(tm.env, TMUX_PANE=pane[s], CLAUDECODE="1") for s in pane}

    # ------------------------------------------------------------------ OV1: il caso del 04/11
    add("t1"); add("t2", session="altra")
    p1, p2, p3 = prova("t1", "uno"), prova("t1", "due"), prova("t2", "tre")
    time.sleep(1.1)
    msg = inbox_put("master", "Dall'utente via telefono. tutto ok sulle prove", "relay")
    reset_talks()
    rc, out, err = cm("ok", "--prove", f"{p1}-{p2},{p3}", "--by", "maintainer", "--inbox", msg, env=AS["master"])
    T.check("OV1 task ok --prove from the master marks every test ok, no ID needed", rc == 0 and prove()["n"] == 0, out + err)
    T.check("OV1 task confirmed is green for both tasks: the answer counts where prove_mark counts", confirmed("t1") and confirmed("t2"), out)
    _t = talks()
    T.check("OV1 the check runs for each task (task settle, detached), as after «Tutte ok» in the app", f"task settle t1" in _t and "task settle t2" in _t, _t)
    T.check("OV1 the owning session is told, with the quote; the master (who recorded it) is not", "talk sess" in _t and "talk altra" in _t
            and "tutto ok sulle prove" in _t and "talk master" not in _t, _t)
    _r = [p for p in TK.connect().execute("SELECT * FROM prove WHERE task='t1'").fetchall()]
    T.check("OV1 the test rows carry who and the person's words", len(_r) == 2 and all(r["result"] == "ok" and r["result_by"] == "maintainer" and "tutto ok sulle prove" in (r["result_text"] or "") for r in _r), str([dict(r) for r in _r]))
    _c = [a for a in show("t1")["approvals"] if a["what"] == "confirmed"]
    T.check("OV1 the confirmation says it came from the chat, not from the relay", _c and "origin chat" in _c[-1]["text"], str(_c))

    # ------------------------------------------------------------------ OV2: task confirm con prove in vigore
    add("t3"); prova("t3")
    time.sleep(1.1)
    msg = inbox_put("master", "ok, lo vedo", "relay")
    rc, out, err = cm("confirm", "t3", "--by", "maintainer", "--inbox", msg, env=AS["master"])
    T.check("OV2 task confirm with a test open marks it: task confirmed green, nothing left in Prova", rc == 0 and confirmed("t3") and not TK.prove_pending(TK.connect(), "t3"), out + err)

    # ------------------------------------------------------------------ OV3: un ok non allarga
    add("t4")
    q1 = prova("t4", "prima")
    time.sleep(1.1)
    msg = inbox_put("sess", "ok va", "terminal")
    time.sleep(1.1)
    q2 = prova("t4", "dopo")
    rc, out, err = cm("ok", "t4", "--by", "maintainer", "--inbox", msg, env=AS["sess"])
    _pend = [r["n"] for r in TK.prove_pending(TK.connect(), "t4")]
    T.check("OV3 without --prove: only the test written before the message; the newer one stays and the output says so",
            rc == 0 and _pend == [q2] and str(q2) in out and not confirmed("t4"), out + err)

    # ------------------------------------------------------------------ OV4: carta con scelta
    add("t5")
    cm("wait-ok", "t5", "--what", "quale build", "--where", "Play", "--options", "A|B")
    msg = inbox_put("master", "la B", "relay")
    rc, out, err = cm("ok", "t5", "--by", "maintainer", "--inbox", msg, env=AS["master"])
    T.check("OV4 a card that asks a choice: without --choice refused, nothing written", rc != 0 and "--choice" in err and show("t5")["state"] == "awaiting_ok", err)
    reset_talks()
    rc, out, err = cm("ok", "t5", "--by", "maintainer", "--inbox", msg, "--choice", "B", env=AS["master"])
    _a = [a for a in show("t5")["approvals"] if a["by_"]]
    T.check("OV4 with --choice B: approved with the choice, gone from Decidi, the session told with the choice",
            rc == 0 and show("t5")["state"] == "approved" and _a and _a[-1]["choice"] == "B" and not in_decidi("t5") and "talk sess" in talks() and "B" in talks(), out + err + talks())

    # ------------------------------------------------------------------ OV5: l'ok nella sessione del compito
    add("t6")
    cm("wait-ok", "t6", "--what", "push", "--where", "github")
    msg = inbox_put("sess", "ok pubblica", "terminal")
    reset_talks()
    rc, out, err = cm("ok", "t6", "--by", "maintainer", "--inbox", msg, env=AS["sess"])
    T.check("OV5 the ok written in the task's own session: approved; the master is told, the session itself is not",
            rc == 0 and show("t6")["state"] == "approved" and "talk master" in talks() and "talk sess" not in talks(), out + err + talks())
    add("t6b")
    cm("wait-ok", "t6b", "--what", "push", "--where", "github")
    msg = inbox_put("master", "ok anche questo", "relay")
    reset_talks()
    rc, out, err = cm("approve", "t6b", "--by", "maintainer", "--inbox", msg, env=AS["master"])
    T.check("OV5 task approve from the master now tells the task's session too (it used to learn nothing)", rc == 0 and "talk sess" in talks(), talks())

    # ------------------------------------------------------------------ OV6-OV7: pause per la persona
    add("t7")
    cm("pause", "t7", "--reason", "prova la pre-release sul telefono", "--waits", "person", "--prova", "--release", "v1.2.3-rc1")
    add("t8")
    cm("pause", "t8", "--reason", "serve il tuo ok sul testo", "--waits", "person")
    msg = inbox_put("master", "tutto va, e ok il testo", "relay")
    reset_talks()
    rc, out, err = cm("ok", "t7", "t8", "--by", "maintainer", "--inbox", msg, env=AS["master"])
    _t = talks()
    T.check("OV6 a test pause: closed as «Tutto va», confirmed, the check runs", rc == 0 and not in_decidi("t7") and confirmed("t7") and "task settle t7" in _t, out + err + _t)
    T.check("OV7 any other pause for the person: closed, the session told, no check run", not in_decidi("t8") and "task settle t8" not in _t and "talk sess" in _t, _t)

    # ------------------------------------------------------------------ OV8-OV10: «non va»
    add("t9")
    k1 = prova("t9", "uno")
    msg = inbox_put("master", "la prova non va: schermo bianco", "relay")
    reset_talks()
    rc, out, err = cm("ok", "t9", "--by", "maintainer", "--inbox", msg, "--ko", "schermo bianco", env=AS["master"])
    _ko = [x for x in prove()["ko"] if x["id"] == k1]
    T.check("OV8 --ko on a test: listed as not working, with the reason; no confirmation; the session told",
            rc == 0 and _ko and _ko[0]["text"] == "schermo bianco" and not confirmed("t9") and "talk sess" in talks() and "schermo bianco" in talks(), out + err + talks())
    add("t10")
    prova("t10", "uno"); prova("t10", "due")
    msg = inbox_put("master", "non va", "relay")
    rc, out, err = cm("ok", "t10", "--by", "maintainer", "--inbox", msg, "--ko", "non va", env=AS["master"])
    T.check("OV9 --ko with two tests open and no --prove: refused (the other test is not a «no»), nothing written",
            rc != 0 and "--prove" in err and len(TK.prove_pending(TK.connect(), "t10")) == 2 and not [x for x in prove()["ko"] if x["task"] == "t10"], err)
    add("t11")
    cm("wait-ok", "t11", "--what", "x", "--where", "y")
    rc, out, err = cm("ok", "t11", "--by", "maintainer", "--inbox", msg, "--ko", "no", env=AS["master"])
    T.check("OV10 --ko on a card: refused, the card stays", rc != 0 and show("t11")["state"] == "awaiting_ok", err)

    # ------------------------------------------------------------------ OV11: nessuna voce aperta
    add("t12")
    msg = inbox_put("master", "lo vedo installato", "relay")
    rc, out, err = cm("ok", "t12", "--by", "maintainer", "--inbox", msg, env=AS["master"])
    T.check("OV11 nothing waits: the person's confirmation is recorded (task confirmed reads it)", rc == 0 and confirmed("t12"), out + err)

    # ------------------------------------------------------------------ OV12: le guardie della casella restano
    add("t13")
    cm("wait-ok", "t13", "--what", "x", "--where", "y")
    sess_msg = inbox_put("master", "ok da una sessione", "session")
    _r1 = cm("ok", "t13", "--by", "maintainer", "--inbox", sess_msg, env=AS["master"])
    good = inbox_put("master", "ok", "relay")
    _r2 = cm("ok", "t13", "--by", "maintainer", "--inbox", good, env=AS["altra"])
    _r3 = cm("ok", "t13", "--by", "maintainer", "--text", "ok", env=AS["master"])
    old = inbox_put("master", "ok di ieri", "relay", age_s=7200)
    _r4 = cm("ok", "t13", "--by", "maintainer", "--inbox", old, env=AS["master"])
    T.check("OV12 refused, nothing written: origin session, another session's inbox, no --inbox/--pin, a message over an hour old",
            all(r[0] != 0 for r in (_r1, _r2, _r3, _r4)) and show("t13")["state"] == "awaiting_ok"
            and "did not come from the person" in _r1[2] and "not for altra" in _r2[2] and "--inbox" in _r3[2] and "minutes old" in _r4[2], str((_r1, _r2, _r3, _r4)))
    cm("cancel", "t12")
    _r5 = cm("ok", "t12", "--by", "maintainer", "--inbox", good, env=AS["master"])
    T.check("OV12 a closed task: refused, it says so", _r5[0] != 0 and "already" in _r5[2], _r5[2])

    # ------------------------------------------------------------------ OV13: «Tocca a te» spento
    add("t14")
    cm("wait-ok", "t14", "--what", "x", "--where", "y")
    (state_dir / "needs-you").mkdir(exist_ok=True)
    _ny = state_dir / "needs-you" / "sid-x.json"
    _ny.write_text(json.dumps({"reason": "serve il tuo ok", "at": int(time.time()), "name": "sess", "task": "t14", "expires_at": int(time.time()) + 3600}))
    _ny2 = state_dir / "needs-you" / "sid-y.json"
    _ny2.write_text(json.dumps({"reason": "altro", "at": int(time.time()), "name": "altra", "task": "altro", "expires_at": int(time.time()) + 3600}))
    rc, out, err = cm("ok", "t14", "--by", "maintainer", "--inbox", good, env=AS["master"])
    T.check("OV13 the needs-you request tied to the task is gone (Tocca a te), the others stay", rc == 0 and not _ny.exists() and _ny2.exists(), out + err)

    # ------------------------------------------------------------------ OV14: una scelta, due carte
    add("t15"); add("t16")
    cm("wait-ok", "t15", "--what", "x", "--where", "y", "--options", "A|B")
    cm("wait-ok", "t16", "--what", "x", "--where", "y", "--options", "A|B")
    rc, out, err = cm("ok", "t15", "t16", "--by", "maintainer", "--inbox", good, "--choice", "A", env=AS["master"])
    T.check("OV14 one choice for two cards: refused, ask which (an ok never widens)", rc != 0 and show("t15")["state"] == "awaiting_ok" and show("t16")["state"] == "awaiting_ok", err)

    # ------------------------------------------------------------------ OA1: Prova nell'app arriva alla sessione
    add("a1"); add("a2", session="altra")
    n1, n2 = prova("a1", "uno"), prova("a1", "due")
    WAKES.clear()
    ok, txt = RL.prova_op(dict(PHONE, result="ok"), str(n1))
    T.check("OA1 a touch on Prova (ok): the owning session is told, with what is left", ok and any(tm_ == "sess" and str(n1) in t and "a1" in t for tm_, t in WAKES), str(WAKES))
    m1 = prova("a2", "x")
    WAKES.clear(); TALKS.clear()
    ok, txt = RL.prova_op(dict(PHONE, result="ko", text="crash"), str(m1))
    T.check("OA1 «Non va» in Prova: to the master (as before) and now to the owning session too", ok and any(n == "master" for n, _ in TALKS) and any(tm_ == "altra" and "crash" in t for tm_, t in WAKES), str((TALKS, WAKES)))

    # ------------------------------------------------------------------ OA2: un solo record
    add("a3")
    n3 = prova("a3")
    time.sleep(1.1)
    msg = inbox_put("master", "ok", "relay")
    cm("ok", "a3", "--by", "maintainer", "--inbox", msg, env=AS["master"])
    _po = json.loads(RL.prove_op()[1])
    T.check("OA2 answered in chat: gone from the relay's Prova (op prove) at once", all(p["task"] != "a3" for g in _po["groups"] for p in g["prove"]), str(_po))
    con = TK.connect()
    T.check("OA2 answered in the app (OA1): gone from what the hook lists", all(t != "a1" or str(n1) not in w for t, w in TK.awaiting_person(con)), str(TK.awaiting_person(con)))
    con.close()

    # ------------------------------------------------------------------ OH: l'hook del prompt
    add("h1")
    cm("wait-ok", "h1", "--what", "x", "--where", "y")
    out = hook("sess", "va bene, procedi")
    _ids = sorted((state_dir / "inbox" / "sess").glob("*.json"), key=lambda p: p.stat().st_mtime)
    _id = json.loads(_ids[-1].read_text())["id"] if _ids else "?"
    T.check("OH1 a message of the person in a session with items waiting: the line names them and the one command with this message's id",
            "h1 (ok)" in out and f"--inbox {_id}" in out and "task ok" in out and "t15" in out and "a2" not in out, out)
    out = hook("master", "come va?")
    _all = TK.awaiting_person(TK.connect())
    _shown = [t for t, _w in _all[:6]]
    T.check("OH2 at the master: every item, of any session (six named, then how many more)",
            len(_all) > 6 and all(f"{t} (" in out for t in _shown) and f"+{len(_all) - 6}" in out and {"sess", "altra"} <= {TK.get_task(TK.connect(), t)["session"] for t, _w in _all}, out)
    out = hook("vuota", "ciao")
    T.check("OH3 a session with nothing waiting: no line", "task ok" not in out, out)
    rid = inbox_put("altra", "Dall'utente via telefono. ok per a2", "relay")
    out = hook("altra", "Dall'utente via telefono. ok per a2")
    T.check("OH4 a message from the phone, already in the inbox: the id is the relay's record", f"--inbox {rid}" in out, out)

    # ------------------------------------------------------------------ isolamento (04/11 17:38: avvisi dei test alla master vera)
    _code = ("import importlib.util as u,sys;s=u.spec_from_file_location('t',sys.argv[1]);m=u.module_from_spec(s);s.loader.exec_module(m);"
             "print(m._cm_bin())")
    _e = {k: v for k, v in ENV.items() if k != "CM_TASK_TALK_BIN"}
    _o = subprocess.run([sys.executable, "-c", _code, str(T.SCRIPTS / "cm-tasks.py")], capture_output=True, text=True, env=_e, timeout=30).stdout.strip()
    _e.pop("CM_TASKS_DB")
    _o2 = subprocess.run([sys.executable, "-c", _code, str(T.SCRIPTS / "cm-tasks.py")], capture_output=True, text=True, env=_e, timeout=30).stdout.strip()
    T.check("isolation: a test registry (CM_TASKS_DB) without a fake supervisor sends no notice; the real registry keeps the real one",
            _o == "None" and _o2.endswith("/supervisor"), f"{_o} | {_o2}")

    # ------------------------------------------------------------------ kernel e help
    for _lang in ("it", "en"):
        _k = (T.PLUGIN / f"kernel_{_lang}.md").read_text()
        T.check(f"kernel ({_lang}) rule 12 names task ok with --prove, --choice and --ko, under the cap",
                all(x in _k for x in ("task ok ID --by", "--prove 72-80", "--choice", "--ko")) and len(_k) <= 3700, str(len(_k)))
    T.check("task --help names task ok", "supervisor task ok" in cm("--help")[1], "")

T.finish()
