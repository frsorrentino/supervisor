#!/usr/bin/env python3
"""Le carte di scelta, di ok e di prova che bloccano un compito in corso stanno in «Serve te», con due promemoria; una
risposta data in un punto le chiude ovunque (23/11/2026, supervisor-promemoria-carte-23-11, contratto 1.160).

La causa: una carta di app-silenzio-assenso-23-11 e' rimasta aperta cinque ore senza un promemoria, perche'
state.attention (e quindi attention_reminders) aveva solo domande, turni falliti, quote e compiti rossi. La condizione
del maintainer: una risposta data in un punto vale ovunque e subito (app, web, orologio, chat della master, terminale).

PC1 attese_view: le carte ok, choice e prova di un compito non in pausa entrano in state.attention come kind «card»
    (ref = wid = l'id dell'attesa, task, card_kind, closes answer) e la loro voce di attese ha blocking: true; un
    compito in pausa (la persona o un'attesa esterna) no
PC2 i promemoria: una card dopo 1 e dopo 3 ore, mai a 10 o 30 minuti; le altre voci restano a 10 e 30 minuti
PC3 una risposta dall'app (op approve) e una prova fatta (op prova) chiudono la voce al giro dopo: esce da attention,
    l'id sta in attese.closed, e un evento attention_closed quieto (ref, wid, attention_kind) fa togliere la notifica
PC4 il recap delle 20: le carte che bloccano un compito stanno in cima a «Ti aspettano»
PC5 fit_state: il titolo delle card cade subito dopo il consiglio delle carte; la voce resta
PC6 (la master, 23/11): ogni carta dice se il silenzio assenso e' ammesso (silence_ok), con la regola con cui il registro
    lo rifiuta: si' per un ok qualsiasi e per una prova, no per una scelta e per un ok che pubblica («pubblica 1.4 sul
    Play Store», anche senza deploy), nelle attese e in state.approvals; il registro rifiuta proprio quelle
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
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"p": {"config_dir": str(home / ".claude")}}, "default_account": "p",
                           "workspace": {"root": str(tmp), "root_session_name": "tst-capo"},
                           "relay": {"dir": str(tmp / "relay"), "web_app_url": "https://app.example.test/"}}))
(tmp / "relay").mkdir()
talklog = tmp / "talk.log"
fake = tmp / "fake-cm"
fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {talklog}\nexit 0\n")
fake.chmod(0o755)
fake_relay = tmp / "fake-relay-cm"   # il relay usa la CLI vera; i talk restano qui
fake_relay.write_text(f"#!/bin/sh\nif [ \"$1\" = talk ]; then printf '%s\\n' \"$*\" >> {talklog}; exit 0; fi\nexec {T.SCRIPTS / 'supervisor'} \"$@\"\n")
fake_relay.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
(tmp / "run").mkdir()
ENV.update({"CM_TASKS_DB": str(tmp / "t.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASK_TALK_BIN": str(fake),
            "CM_RELAY_NO_PUSH": "1", "CM_RELAY_CM": str(fake_relay), "CM_TALK_CM": str(fake), "TMUX_TMPDIR": str(tmp / "run"),
            "CM_TMUX_ARGS": "-L promemoria-verify", "XDG_RUNTIME_DIR": str(tmp / "run")})
os.environ.clear()
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")


def cm(*a, stdin=None):
    return subprocess.run([CLI, *a], input=stdin, capture_output=True, text=True, env=ENV, timeout=120)


def add(tid, session):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"t {tid}", "short": f"breve {tid}", "plan": "p",
                                       "where": {"project": str(tmp), "session": session, "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": str(tmp), "timeout_s": 30}, "perimeter": ["x"], "lane": "open", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
                                       "outcome": {"expected": "x", "kind": "custom", "proof": "x"}}}
    r = cm("task", "add", "-", stdin=json.dumps(c))
    assert r.returncode == 0, r.stdout + r.stderr
    cm("task", "start", tid)


# la pausa prima, cosi' nel recap e' la voce piu' vecchia: le carte devono passarle davanti
add("pc-pausa", "alfa")
cm("task", "wait-ok", "pc-pausa", "--consiglio", "si", "--what", "il testo", "--where", "web")
cm("task", "pause", "pc-pausa", "--reason", "aspetta il cliente", "--waits", "external", "--recheck", "2d")
add("pc-ferma", "alfa")
cm("task", "prova", "pc-ferma", "--build", "b0", "--dove", "home", "--fai", "apri", "--atteso", "si vede")
cm("task", "pause", "pc-ferma", "--reason", "lo riprendo domani", "--waits", "person")
time.sleep(1.1)
add("pc-ok", "alfa")
add("pc-scelta", "beta")
add("pc-prova", "alfa")
cm("task", "wait-ok", "pc-ok", "--consiglio", "va bene: provato", "--what", "il report", "--where", "la cartella")
cm("task", "wait-ok", "pc-scelta", "--consiglio", "la prima", "--consigliata", "1", "--what", "quale sfondo?", "--where", "app", "--options", "Scuro|Chiaro")
cm("task", "prova", "pc-prova", "--build", "b1", "--dove", "home", "--fai", "apri la home", "--atteso", "si vede")
NOW = int(time.time())
ROWS = [{"name": "alfa", "tmux": "alfa", "status": "idle", "session_id": "s-alfa"}, {"name": "beta", "tmux": "beta", "status": "idle", "session_id": "s-beta"}]
STATE0 = {"sessions": [{"name": "alfa", "state": "idle"}, {"name": "beta", "state": "idle"}],
          "attention": [{"kind": "needs_you", "session": "beta", "ref": "beta", "title": "il codice del login", "since": NOW - 40}]}

sp = importlib.util.spec_from_file_location("cm_relay_pc", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)


def view(now=None):
    full = copy.deepcopy(STATE0)
    RL._MEMO.clear()
    items = RL.attese_view(full, now or NOW, ROWS)
    return full, items or []


# PC1
full1, items1 = view()
cards = [x for x in full1.get("attention") or [] if x.get("kind") == "card"]
by_task = {x.get("task"): x for x in cards}
att_by_id = {a["id"]: a for a in items1}
T.check("PC1 the ok, choice and prova cards of running tasks are in state.attention as kind card (ref = wid = the attese id, closes answer)",
        sorted(by_task) == ["pc-ok", "pc-prova", "pc-scelta"]
        and [by_task[t]["card_kind"] for t in ("pc-ok", "pc-scelta", "pc-prova")] == ["ok", "choice", "prova"]
        and all(x["ref"] == x["wid"] and x["wid"] in att_by_id and x["closes"] == "answer" and x["title"] for x in cards),
        json.dumps(cards)[:900])
T.check("PC1 a task paused for the person or for an external wait has no card in attention; the needs_you item stays",
        "pc-pausa" not in by_task and "pc-ferma" not in by_task and any(x.get("kind") == "needs_you" for x in full1.get("attention") or []),
        json.dumps(full1.get("attention"))[:600])
T.check("PC1 the attese item of a blocking card has blocking: true; the others do not",
        all(att_by_id[x["wid"]].get("blocking") is True for x in cards)
        and not any(a.get("blocking") for a in items1 if a["id"] not in {x["wid"] for x in cards}),
        json.dumps([(a["kind"], a.get("task"), a.get("blocking")) for a in items1]))

# PC2
r2 = tmp / "r2"
r2.mkdir()
_rd, _em = RL.rdir, RL.emit
got = []
RL.rdir, RL.emit = (lambda: r2), (lambda kind, title, body, **kw: got.append((kind, title, kw.get("ref"), (kw.get("extra") or {}))))
# 24/11 (supervisor-promemoria-notte-24-11): alle 09:00 di oggi, fuori dalle ore di quiete [23, 7): con l'ora vera, di
# notte i promemoria non suonano piu' e PC2 diventava rosso
_lt2 = time.localtime(NOW)
D2 = int(time.mktime((_lt2.tm_year, _lt2.tm_mon, _lt2.tm_mday, 9, 0, 0, 0, 0, -1)))
c0 = dict(cards[0], since=D2)
f2 = {"attention": [c0, {"kind": "needs_you", "session": "beta", "ref": "beta", "title": "il codice", "since": D2}]}
for dt in (5, 700, 1900, 3700, 7200, 10900, 11000):
    RL.attention_reminders(f2, D2 + dt)
RL.rdir, RL.emit = _rd, _em
card_rem = [g for g in got if g[0] == "attention_reminder" and g[2] == c0["ref"]]
other_rem = [g for g in got if g[0] == "attention_reminder" and g[2] == "beta"]
T.check("PC2 a card reminds after 1 hour and after 3 hours, never at 10 or 30 minutes",
        len(card_rem) == 2 and "(60 min)" in card_rem[0][1] and "(180 min)" in card_rem[1][1] and card_rem[0][3].get("attention_kind") == "card", str(got))
T.check("PC2 the other items keep 10 and 30 minutes", len(other_rem) == 2 and "(10 min)" in other_rem[0][1] and "(30 min)" in other_rem[1][1], str(other_rem))

# PC3
r3 = tmp / "r3"
r3.mkdir()
got3 = []
RL.rdir, RL.emit = (lambda: r3), (lambda kind, title, body, **kw: got3.append((kind, kw.get("ref"), (kw.get("extra") or {}))))
RL.attention_reminders(full1, NOW + 10)
RL.rdir, RL.emit = _rd, _em
ok_wid = by_task["pc-ok"]["wid"]
prova_wid = by_task["pc-prova"]["wid"]
ap = RL.execute({"op": "approve", "arg": "pc-ok", "text": "ok", "device": "phone", "by": "phone-pixel8", "session": None})
prova_ids = att_by_id[prova_wid]["answer"]["arg"]
pv = RL.execute({"op": "prova", "arg": prova_ids, "result": "ok", "device": "watch", "by": "watch", "session": None})
full3, items3 = view(NOW + 20)
left = {x.get("task") for x in full3.get("attention") or [] if x.get("kind") == "card"}
closed3 = {c["id"] for c in (full3.get("attese") or {}).get("closed") or []}
T.check("PC3 an answer from the app (op approve) and a test done from the watch (op prova) close the card at the next round: out of attention, in attese.closed",
        ap[0] and pv[0] and left == {"pc-scelta"} and {ok_wid, prova_wid} <= closed3, f"{ap} {pv} {left} {closed3}")
RL.rdir, RL.emit = (lambda: r3), (lambda kind, title, body, **kw: got3.append((kind, kw.get("ref"), (kw.get("extra") or {}))))
RL.attention_reminders(full3, NOW + 30)
RL.attention_reminders(full3, NOW + 40)
RL.rdir, RL.emit = _rd, _em
cl = [g for g in got3 if g[0] == "attention_closed"]
T.check("PC3 one quiet attention_closed event per closed card (ref, wid, attention_kind card), once: phone and watch drop the notification",
        sorted(g[1] for g in cl) == sorted([ok_wid, prova_wid]) and all(g[2].get("wid") == g[1] and g[2].get("attention_kind") == "card" and g[2].get("quiet") is True for g in cl),
        str(cl))
src = (T.SCRIPTS / "cm-relay.py").read_text(encoding="utf-8")
T.check("PC3 the FCM wake-up of an event says quiet when the event is quiet", '{"quiet": "1"} if ev.get("quiet")' in src, "")
T.check("PC3 the FCM wake-up of attention_reminder and attention_closed carries ref and wid (the web's service worker does not read /events); not the others",
        RL.fcm_attention("attention_closed", {"ref": ok_wid, "wid": ok_wid}) == {"ref": ok_wid, "wid": ok_wid}
        and RL.fcm_attention("attention_reminder", {"ref": "beta", "wid": None}) == {"ref": "beta"}
        and RL.fcm_attention("recap", {"ref": "x", "wid": "w"}) == {} and "**fcm_attention(kind, ev)" in src
        and card_rem[0][3].get("wid") == c0["wid"], str(card_rem[:1]))

# PC4
(tmp / "relay" / "last-state.json").write_text(json.dumps({"state": full1, "full": full1}))
sp4 = importlib.util.spec_from_file_location("cm_avvisi_pc", T.SCRIPTS / "cm-avvisi.py")
AV = importlib.util.module_from_spec(sp4)
sp4.loader.exec_module(AV)
w4 = AV.waiting()
kinds4 = [(a.get("kind"), a.get("task")) for a in w4]
n_first = sum(1 for a in w4[:2] if a.get("task") == "pc-scelta" or a.get("kind") in ("ok", "choice", "prova"))
T.check("PC4 the evening recap lists the cards that block a task first, before the older pause",
        bool(w4) and w4[0].get("task") == "pc-scelta" and all(a.get("kind") not in ("pausa",) for a in w4[:1]) and n_first >= 1, str(kinds4))

# PC5
S = RL.S
big = {"sessions": [], "attention": [{"kind": "card", "session": "alfa", "ref": f"w{i:010d}", "wid": f"w{i:010d}", "task": f"compito-{i}", "card_kind": "ok",
                                      "title": "x" * 80, "since": NOW, "closes": "answer"} for i in range(45)]}
fit = S.fit_state(copy.deepcopy(big), 8)
T.check("PC5 fit_state: the card titles go before anything else that matters; the 45 items stay",
        S.size_of(big) > 8 * 1024 and len(fit["attention"]) == 45 and all(x["title"] == "" for x in fit["attention"]) and S.size_of(fit) <= 8 * 1024,
        f"{S.size_of(big)} {S.size_of(fit)}")

# PC6
add("pc-pubblica", "beta")
cm("task", "wait-ok", "pc-pubblica", "--consiglio", "si", "--what", "pubblica 1.4 sul Play Store", "--where", "console")
add("pc-ok2", "alfa")
cm("task", "wait-ok", "pc-ok2", "--consiglio", "si", "--what", "il riassunto", "--where", "la cartella")
add("pc-prova2", "alfa")
cm("task", "prova", "pc-prova2", "--build", "b2", "--dove", "home", "--fai", "apri le impostazioni", "--atteso", "si aprono")
full6, items6 = view(int(time.time()))
ok6 = {(a["kind"], a.get("task")): a.get("silence_ok") for a in items6 if a["kind"] in ("ok", "choice", "prova")}
RL._MEMO.clear()
ap6 = {a["task"]: a.get("silence_ok") for a in RL.approvals_list()}
ref6 = [cm("task", "silenzio", t).returncode for t in ("pc-ok2", "pc-pubblica", "pc-scelta", "pc-prova2")]
T.check("PC6 silence_ok on the attese items: an ordinary ok and a test yes; a choice and an ok that publishes no",
        ok6.get(("ok", "pc-ok2")) is True and ok6.get(("prova", "pc-prova2")) is True and ok6.get(("choice", "pc-scelta")) is False
        and ok6.get(("ok", "pc-pubblica")) is False, str(ok6))
T.check("PC6 silence_ok on state.approvals the same; the registry refuses exactly where it is false",
        ap6.get("pc-ok2") is True and ap6.get("pc-pubblica") is False and ap6.get("pc-scelta") is False and ref6[0] == 0 and ref6[1] != 0 and ref6[2] != 0
        and ref6[3] == 0, f"{ap6} {ref6}")

subprocess.run(["tmux", "-L", "promemoria-verify", "kill-server"], capture_output=True)
T.rm(str(tmp))
T.finish()
