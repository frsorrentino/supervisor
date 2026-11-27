#!/usr/bin/env python3
"""Le attese, la fonte unica di ogni voce per te (16/11/2026, supervisor-riall-1-dati-16-11, contratto 1.151).

Il manutentore ha scelto la direzione A dell'analisi di supervisor-app (docs/valutazioni/2026-11-16-chiarezza-sessione.md,
capitoli 5-8): due posti con gli stessi dati, la lista «Per te» nella sessione e il contenitore in Dalla master, una
fonte sola. Oggi le voci arrivano da sei fonti e si contano in tre modi: «Verifica 5» con l'elenco vuoto, «Uscite
uniformi: sì» toccabile un'ora dopo l'ok. Passo 1 lato relay: `attese` ha ogni voce per te, anche delle sessioni aperte,
con id stabile e la sessione col nome dello stato; state.tocca e sessions[].tocca si contano da li'; una voce chiusa da
qualunque punto va in state.attese.closed, cosi' ogni dispositivo la toglie subito.

RD1  le attese hanno ogni voce: le carte del registro (ok, scelta, prova) e quelle delle sessioni (domanda, Prossimi,
     needs-you, limite, e le nuove: il turno fallito «turno», col tasto Continua, e il compito rosso «rosso»); id
     stabili; la sessione e' il nome dello stato («win:alfa» per un compito registrato per «alfa»)
RD2  state.tocca e sessions[].tocca si contano dalle stesse voci: n = le voci, prove (verify), decidi (decide e choose),
     rispondi (reply); guarda e segui restano dalle loro fonti
RD3  una voce chiusa (un ok dal telefono) va in state.attese.closed con l'ora, rev cambia, la voce esce; dopo dieci minuti
     esce anche da closed; un registro che non si legge non chiude niente (niente voci sparite per finta)
RD4  l'op attese da' la lista contata dall'ultima push: la stessa rev, le stesse voci
RD5  sopra gli 8 KB closed e' la prima cosa che cade; rev e i conti restano
RD6  la fixture attese-rd-sample.json (per l'app) e' quello che il relay scrive oggi
"""
import copy
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
(home / ".claude").mkdir(parents=True)
cfg = tmp / "config.json"
WEB = "https://app.example.test"
cfg_data = {"language": "it", "state_dir": str(tmp / "state"), "accounts": {"p": {"config_dir": str(home / ".claude")}}, "default_account": "p",
            "workspace": {"root": str(tmp), "root_session_name": "tst-capo"}, "relay": {"dir": str(tmp / "relay"), "web_app_url": WEB + "/"}}
cfg.write_text(json.dumps(cfg_data))
(tmp / "relay").mkdir()
talklog = tmp / "talk.log"
fake = tmp / "fake-cm"
fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {talklog}\nexit 0\n")
fake.chmod(0o755)
fake_relay = tmp / "fake-relay-cm"   # il relay legge il registro con la CLI vera; i talk restano qui
fake_relay.write_text(f"#!/bin/sh\nif [ \"$1\" = talk ]; then printf '%s\\n' \"$*\" >> {talklog}; exit 0; fi\nexec {T.SCRIPTS / 'supervisor'} \"$@\"\n")
fake_relay.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
(tmp / "run").mkdir()
ENV.update({"CM_TASKS_DB": str(tmp / "t.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASK_TALK_BIN": str(fake),
            "CM_RELAY_NO_PUSH": "1", "CM_RELAY_CM": str(fake_relay), "CM_TALK_CM": str(fake), "TMUX_TMPDIR": str(tmp / "run"),
            "CM_TMUX_ARGS": "-L riall-verify", "XDG_RUNTIME_DIR": str(tmp / "run")})
os.environ.clear()
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")


def cm(*a, stdin=None):
    return subprocess.run([CLI, *a], input=stdin, capture_output=True, text=True, env=ENV, timeout=120)


def add(tid, session):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"t {tid}", "plan": "p", "where": {"project": str(tmp), "session": session, "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": str(tmp), "timeout_s": 30}, "perimeter": ["x"], "lane": "open", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
                                       "outcome": {"expected": "x", "kind": "custom", "proof": "x"}}}
    assert cm("task", "add", "-", stdin=json.dumps(c)).returncode == 0
    cm("task", "start", tid)


add("rd-ok", "alfa")   # registrato per «alfa», mentre vive «win:alfa»
add("rd-scelta", "beta")
add("rd-prova", "alfa")
cm("task", "wait-ok", "rd-ok", "--consiglio", "va bene: provato", "--what", "pubblico la 1.0?", "--where", "web")
cm("task", "wait-ok", "rd-scelta", "--consiglio", "la prima", "--consigliata", "1", "--what", "quale sfondo?", "--where", "app", "--options", "Scuro|Chiaro")
cm("task", "prova", "rd-prova", "--build", "b1", "--dove", "home", "--fai", "apri la home", "--atteso", "si vede")
NOW = int(time.time())
ROWS = [{"name": "win:alfa", "remote_name": "win-alfa", "host": "win", "status": "idle", "session_id": "s-alfa"},
        {"name": "beta", "tmux": "beta", "status": "idle", "session_id": "s-beta"},
        {"name": "gamma", "tmux": "gamma", "status": "waiting", "session_id": "s-gamma"},
        {"name": "delta", "tmux": "delta", "status": "idle", "session_id": "s-delta"}]
STATE0 = {"sessions": [{"name": "win:alfa", "state": "idle", "tocca": {"prove": 0, "decidi": 0, "guarda": 2, "segui": 1}},
                       {"name": "beta", "state": "idle"},
                       {"name": "gamma", "state": "waiting", "question": {"id": "q-1", "text": "Quale?", "options": [{"label": "Uno"}, {"label": "Due"}], "asked_at": NOW - 50}},
                       {"name": "delta", "state": "idle", "next_steps": [{"text": "Riga a sinistra", "blocking": True}]}],
          "attention": [{"kind": "question", "session": "gamma", "ref": "q-1", "title": "Quale?", "since": NOW - 50},
                        {"kind": "needs_you", "session": "beta", "ref": "beta", "title": "il codice del login", "since": NOW - 40},
                        {"kind": "turn_blocking", "session": "delta", "ref": "delta", "title": "Riga a sinistra", "since": NOW - 30},
                        {"kind": "turn_failed", "session": "delta", "ref": "delta", "title": "delta: turno fallito (529)", "since": NOW - 20},
                        {"kind": "task_red", "session": "beta", "ref": "rd-scelta", "title": "rd-scelta: il controllo è rosso", "since": NOW - 15},
                        {"kind": "quota_limit", "session": "win:alfa", "ref": "win:alfa", "title": "win:alfa è ferma: limite di Fable fino al 19/11 alle 3",
                         "since": NOW - 10, "actions": ["other_model", "at_reset"], "other_model": "Opus 5.5", "reset_at": NOW + 86400, "window": "model"}],
          "tocca": {"n": 2, "prove": 1, "decidi": 1, "today": {"n": 0, "items": []}}}

sp = importlib.util.spec_from_file_location("cm_relay_rd", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
STATE0["approvals"] = RL.S.build_state({"approvals": RL.approvals_list()}, NOW, fit=False)["approvals"]
AT = RL.attese_mod() if hasattr(RL, "attese_mod") else RL._load("cm-attese")   # lo stesso modulo che usa la push


def view(state=None, rows=ROWS, now=None):
    full = copy.deepcopy(state or STATE0)
    if hasattr(RL, "_MEMO"):
        RL._MEMO.clear()
    try:
        RL.attese_view(full, now or NOW, rows)
    except AttributeError as e:   # prima della 1.151 non c'era
        full["error"] = repr(e)
    return full


def acts(a):
    return [(b["cmd"].get("op"), b["cmd"].get("arg"), b["cmd"].get("action") or b["cmd"].get("kind") or b["cmd"].get("result") or b["cmd"].get("value")) for b in a.get("actions") or []]


# RD1
try:
    items = AT.collect(NOW, state=STATE0, alias=RL.state_alias(ROWS))
except TypeError:   # prima della 1.151: niente alias
    items = AT.collect(NOW, state=STATE0)
by = {a["kind"]: a for a in items}
T.check("RD1 one list with every item: ok, choice, prova (registry); question, prossimi, needs_you, limite, turno, rosso (sessions); the Prima raccolta; stable ids",
        sorted(by) == ["choice", "limite", "needs_you", "ok", "prossimi", "prova", "question", "raccolta", "rosso", "turno"] and len(items) == 10
        and [a["id"] for a in items] == [a["id"] for a in AT.collect(NOW, state=STATE0)] and all(AT.ID_RE.fullmatch(a["id"]) for a in items),
        json.dumps(sorted(by)))
T.check("RD1 the session is the name in the state: «win:alfa» for the tasks registered for «alfa» (ok, prova); the Prima raccolta has none",
        (by.get("ok") or {}).get("session") == "win:alfa" and (by.get("prova") or {}).get("session") == "win:alfa" and (by.get("choice") or {}).get("session") == "beta"
        and (by.get("question") or {}).get("session") == "gamma" and (by.get("limite") or {}).get("session") == "win:alfa" and (by.get("raccolta") or {}).get("session", "x") is None,
        json.dumps({k: v.get("session") for k, v in by.items()}))
ok1, pv1, rc1 = by.get("ok") or {}, by.get("prova") or {}, by.get("raccolta") or {}
key1 = (ok1.get("decision") or {}).get("key")
T.check("RD1 a registry item carries its decision group (key, title, weight, where) and «Non ora» (24 h) and «Lascia perdere» (with the reason), as in op decisioni",
        bool(key1) and (ok1.get("decision") or {}).get("weight") == 1 and (ok1.get("decision") or {}).get("where") == "here" and ok1.get("asks") == "ok"
        and acts(ok1)[-3:-1] == [("decisione", key1, "not_now"), ("decisione", key1, "drop")] and ok1["actions"][-3]["cmd"].get("hours") == 24
        and ok1["actions"][-2].get("text_field") == "reason" and acts(ok1)[-1][:2] == ("standby", "rd-ok")   # 1.163: Stand by in fondo
        and acts(ok1)[0] == ("approve", "rd-ok", None), json.dumps(ok1, ensure_ascii=False)[:900])
T.check("RD1 a prova carries each test (id, build, dove, fai, atteso, state todo) and its build: the app says «installa prima» for another version",
        [(t["build"], t["dove"], t["fai"], t["atteso"], t["state"]) for t in pv1.get("tests") or []] == [("b1", "home", "apri la home", "si vede", "todo")]
        and pv1.get("build") == "b1" and pv1.get("label") == "verify", json.dumps(pv1, ensure_ascii=False)[:700])
T.check("RD1 the Prima raccolta while its question is open: «Scansiona» and «Non ora» (op prima_raccolta_azione), the question as title",
        [(b["cmd"].get("op"), b["cmd"].get("action"), b["cmd"].get("value")) for b in rc1.get("actions") or []]
        == [("prima_raccolta_azione", "consent", "si"), ("prima_raccolta_azione", "consent", "later")] and rc1.get("label") == "decide"
        and rc1.get("title") == RL.M("raccolta.consent_question", days=14), json.dumps(rc1, ensure_ascii=False)[:500])
tu, ro = by.get("turno") or {}, by.get("rosso") or {}
T.check("RD1 «turno»: Decidi, «Continua» (the prompt of the Continue button) and «Fatto»; «rosso» (the guard's): Verifica, the task, «Fatto»",
        tu.get("label") == "decide" and acts(tu) == [("prompt", RL.M("relay.continue_text"), None), ("attention_done", "delta", "turn_failed")]
        and tu["actions"][0]["cmd"].get("session") == "delta" and tu["actions"][0]["label"] == "Continua"
        and ro.get("task") == "rd-scelta" and ro.get("label") == "verify" and acts(ro) == [("attention_done", "rd-scelta", "task_red")], json.dumps([tu, ro], ensure_ascii=False)[:900])

# RD2
f1 = view()
want = {"n": 10, "prove": 2, "decidi": 5, "rispondi": 3}   # prove: prova, rosso; decidi: ok, choice, limite, turno, raccolta
tc = f1.get("tocca") or {}
ss = {s["name"]: s.get("tocca") for s in f1.get("sessions") or []}
T.check("RD2 state.tocca from the same items: n = 10 = the rows, prove 2, decidi 5, rispondi 3; today kept; state.attese {rev, n 10, closed []}",
        {k: tc.get(k) for k in want} == want and tc.get("today") == {"n": 0, "items": []} and (f1.get("attese") or {}).get("n") == 10
        and (f1.get("attese") or {}).get("closed") == [] and len(str((f1.get("attese") or {}).get("rev") or "")) == 10, json.dumps([tc, f1.get("attese"), f1.get("error")]))
T.check("RD2 sessions[].tocca from the same items per session (win:alfa: ok, prova, limite; beta: choice, needs_you, rosso; gamma: question; delta: prossimi, turno); guarda and segui kept",
        ss.get("win:alfa") == {"prove": 1, "decidi": 2, "rispondi": 0, "guarda": 2, "segui": 1} and ss.get("beta") == {"prove": 1, "decidi": 1, "rispondi": 1, "guarda": 0, "segui": 0}
        and ss.get("gamma") == {"prove": 0, "decidi": 0, "rispondi": 1, "guarda": 0, "segui": 0} and ss.get("delta") == {"prove": 0, "decidi": 1, "rispondi": 1, "guarda": 0, "segui": 0},
        json.dumps(ss))

# RD3
okid = ok1.get("id")
r3 = RL.execute({"op": "approve", "arg": "rd-ok", "text": "ok", "by": "test", "device": "phone"})
STATE1 = dict(STATE0, approvals=RL.S.build_state({"approvals": RL.approvals_list()}, NOW, fit=False)["approvals"])
f2 = view(STATE1, now=NOW + 5)
cl = (f2.get("attese") or {}).get("closed") or []
T.check("RD3 an ok from the phone: its id in state.attese.closed with the time, rev changed, 9 items, n and decidi down by one",
        r3[0] is True and cl == [{"id": okid, "at": NOW + 5}] and (f2.get("attese") or {}).get("rev") != (f1.get("attese") or {}).get("rev")
        and (f2.get("attese") or {}).get("n") == 9 and (f2.get("tocca") or {}).get("decidi") == 4, f"{r3} {f2.get('attese')} {f2.get('tocca')}")
f3 = view(STATE1, now=NOW + 700)
_reg = getattr(AT, "registry_items", None)
if _reg:
    AT.registry_items = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked"))
f4 = view(STATE1, now=NOW + 710)
if _reg:
    AT.registry_items = _reg
T.check("RD3 after ten minutes it leaves closed; a registry that cannot be read closes nothing (the same items and counts, no rev change)",
        (f3.get("attese") or {}).get("closed") == [] and (f4.get("attese") or {}).get("closed") == [] and (f4.get("attese") or {}).get("rev") == (f3.get("attese") or {}).get("rev")
        and (f4.get("tocca") or {}).get("n") == 9, f"{f3.get('attese')} {f4.get('attese')} {f4.get('tocca')}")

# RD4
ok4, t4 = RL.execute({"op": "attese", "device": "web"})
d4 = json.loads(t4) if ok4 else {}
T.check("RD4 op attese answers with the list the last push counted: the same rev, the same ids, as many as state.attese.n",
        ok4 and d4.get("rev") == (f4.get("attese") or {}).get("rev") and [a["id"] for a in d4.get("items") or []] == [a["id"] for a in AT.collect(NOW + 710, state=STATE1, alias=RL.state_alias(ROWS))]
        and len(d4.get("items") or []) == (f4.get("attese") or {}).get("n"), f"{ok4} {str(t4)[:300]}")

# RD5
big = copy.deepcopy(f2)
big.setdefault("attese", {})["closed"] = [{"id": f"w{i:010x}", "at": NOW} for i in range(10)]
big["pad"] = ""
big["pad"] = "x" * max(0, 8 * 1024 - RL.S.PUBLISHED_AT_BYTES + 150 - RL.S.size_of(big))   # 150 byte sopra il tetto: closed basta
fit5 = RL.S.fit_state(copy.deepcopy(big), 8)
T.check("RD5 over the 8 KB the closed list is the first to go; rev, n and the counts stay",
        "closed" not in (fit5.get("attese") or {}) and (fit5.get("attese") or {}).get("rev") == big["attese"]["rev"] and fit5.get("tocca") == big.get("tocca"),
        json.dumps(fit5.get("attese")))

# RD6: la fixture per l'app, una voce per tipo, con voci del registro fisse (i tempi veri del registro cambiano a ogni giro)
FIX = T.ROOT / "tests" / "fixtures" / "relay" / "attese-rd-sample.json"
W = RL.S.wid
DEC = {"key": "a1b2c3d4e5f6", "title": "Pubblico la 1.4 sul Play Store?", "cause": None, "where": "here", "weight": 2, "unblocks": ["app-x", "app-x2"], "note": None, "since": 1794849000}
FIXREG = [
    {"id": RL.S.wid_approval("app-x", 1794849000), "type": "carta", "kind": "ok", "session": "alfa", "task": "app-x", "text": "Pubblico la 1.4 sul Play Store?", "options": None,
     "at": 1794849000, "answer": {"op": "approve", "arg": "app-x"}, "decision": DEC, "plan_start": "La 1.4 porta la riga nuova in home.", "asks": "ok", "where": "here",
     "precedent": None, "also": None, "_short": "pubblica 1.4", "card": {"what": "Pubblico la 1.4 sul Play Store?", "where": "Play Store", "undo": "si", "undo_how": "ritiro la release"},
     "files": None, "_advice": {"text": "le prove sono verdi", "approve": True}, "_card": None, "silence_ok": False},
    {"id": W("prova", "app-y", 1301), "type": "carta", "kind": "prova", "session": "alfa", "task": "app-y", "_short": "riga in home", "files": None,
     "text": "home: apri la home; impostazioni: cambia tema", "options": None, "at": 1794849100, "answer": {"op": "prova", "arg": "1301,1302"},
     "tests": [{"id": 1301, "build": "1.4.0-rc2", "dove": "home", "fai": "apri la home", "atteso": "la riga nuova", "files": None, "state": "todo", "text": None, "at": 1794849100},
               {"id": 1302, "build": "1.4.0-rc2", "dove": "impostazioni", "fai": "cambia tema", "atteso": "resta scuro", "files": None, "state": "todo", "text": None, "at": 1794849100}],
     "build": "1.4.0-rc2", "silence_ok": True},
    {"id": W("nonva", 1290), "type": "carta", "kind": "nonva", "session": "delta", "task": "app-z", "_short": "login col codice", "files": None, "text": "Non entra col codice",
     "options": None, "at": 1794849200, "answer": {"op": "prova", "arg": "1290", "result": "ok"},
     "tests": [{"id": 1290, "build": "1.3.9", "dove": "login", "fai": "entra col codice", "atteso": "la home", "files": None, "state": "ko", "text": "Non entra col codice", "at": 1794849200}],
     "build": "1.3.9"},
    {"id": W("rosso", "app-w", 1794849300), "type": "carta", "kind": "rosso", "session": "delta", "task": "app-w", "_short": "build web", "text": "npm test: 2 failing",
     "options": None, "at": 1794849300, "answer": None, "red": {"reason": "npm test: 2 failing", "running": False}}]
fx_state = {"sessions": [{"name": "win:alfa", "state": "idle"}, {"name": "delta", "state": "idle"}],
            "attention": [{"kind": "turn_failed", "session": "delta", "ref": "delta", "title": "delta: turno fallito (529 Overloaded)", "since": 1794850000},
                          {"kind": "task_red", "session": "delta", "ref": "app-w", "title": "app-w: il controllo è rosso", "since": 1794850100}],
            "tocca": {"n": 0, "prove": 0, "decidi": 0, "today": {"n": 0, "items": []}}}
_reg = getattr(AT, "registry_items", None)
if _reg:
    AT.registry_items = lambda *a, **k: copy.deepcopy(FIXREG)
(tmp / "relay" / "attese-state.json").unlink(missing_ok=True)
# l'ora vera: la lista dell'ultima push vale per l'op attese solo se e' di pochi minuti fa; il contenuto non ne dipende
fx = view(fx_state, rows=[{"name": "win:alfa", "host": "win", "remote_name": "win-alfa", "status": "idle"}, {"name": "delta", "tmux": "delta", "status": "idle"}],
          now=int(time.time()))
if _reg:
    AT.registry_items = _reg
ok6, t6 = RL.execute({"op": "attese", "device": "web"})
sample = {"attese": json.loads(t6) if ok6 else None, "state": {"attese": fx.get("attese"), "tocca": fx.get("tocca"),
                                                            "sessions": [{"name": s["name"], "tocca": s.get("tocca")} for s in fx.get("sessions") or []]}}
if sample["attese"]:
    sample["attese"]["at"] = 1794850200
if os.environ.get("CM_WRITE_FIXTURE") == "attese-rd-sample":   # per rigenerarla a mano, dopo un cambio voluto
    FIX.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
T.check("RD6 the fixture attese-rd-sample.json (sent to the app) is what the relay writes today: one item per new kind, the counts from them",
        FIX.is_file() and json.loads(FIX.read_text(encoding="utf-8")) == json.loads(json.dumps(sample)), json.dumps(sample, ensure_ascii=False)[:700])

T.rm(str(tmp))
T.finish()
