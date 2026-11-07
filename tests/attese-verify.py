#!/usr/bin/env python3
"""Ogni domanda che aspetta la persona e' un'attesa con id stabile e link (07/11/2026, supervisor-attese-link-07-11,
contratto 1.130). Registro, relay e trascrizioni in una cartella temporanea; talk finto (CM_TASK_TALK_BIN).

AT1 collect: una carta di ok (cosa, non provato, annullare), una di scelta (opzioni con la consigliata), una prova, e dallo stato una domanda AskUserQuestion, una
    needs-you e un turno chiuso con «Prossimi: !…» (le opzioni sono le voci); id «w» + 10 esadecimali, uguali in due
    letture; link <web_app_url>/#/attesa/<id>; answer dice l'op che risponde
AT2 lo stesso id nello stato: approvals[].wid del relay e wid_attention delle voci di attention sono gli id di collect;
    una richiesta nuova dello stesso compito ha un id nuovo; relay.web_app_url "" = link null
AT3 da dove viene (origin_in): la risposta dopo `task wait-ok ID` (non un testo di un altro turno), l'ultimo testo con
    «Prossimi» per il turno, la chiamata AskUserQuestion per la domanda; nessuna chiamata = None
AT4 op attesa da execute: la voce con msg {session, session_id, id, uuid, text, key (la frase chiave)} dalla trascrizione della sessione; id non
    valido o chiuso = errore; op attese = l'elenco; CLI `supervisor attese` stampa i link
AT5 la riga «Ti aspettano» dell'hook della master (qui «tst-capo») porta il link di ogni voce, carte di ok comprese
AT7 (1.131) title, card.deploy e actions: i tasti con il comando del relay gia' pronto, che passa da execute com'e'
AT6 gli esempi di cmd-result-sample (…1300 attese, …1301 attesa, …1302 chiusa) hanno i campi delle risposte vere
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
# 07/11 21:22: un approve del relay aveva raggiunto la master vera con il tmux di default. Tutto isolato: il talk del relay
# e dei compiti e' finto, tmux e i socket stanno nella cartella temporanea, nessuna sessione si chiama «master»
(tmp / "run").mkdir()
ENV.update({"CM_TASKS_DB": str(tmp / "t.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASK_TALK_BIN": str(fake),
            "CM_RELAY_NO_PUSH": "1", "CM_RELAY_CM": str(fake_relay), "CM_TALK_CM": str(fake), "TMUX_TMPDIR": str(tmp / "run"), "CM_TMUX_ARGS": "-L attese-verify",
            "XDG_RUNTIME_DIR": str(tmp / "run")})
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


add("at-ok", "alfa")
add("at-scelta", "beta")
add("at-prova", "alfa")
cm("task", "wait-ok", "at-ok", "--what", "pubblico la 1.0?", "--where", "web", "--untested", "il login su iOS", "--undo", "si", "--undo-how", "tolgo il tag")
cm("task", "wait-ok", "at-scelta", "--what", "quale sfondo?", "--where", "app in produzione", "--options", "Scuro (consigliata)|Chiaro")
cm("task", "prova", "at-prova", "--build", "b1", "--dove", "home", "--fai", "apri la home", "--atteso", "si vede")
NOW = int(time.time())
state = {"sessions": [{"name": "gamma", "question": {"id": "q-17", "text": "Quale?", "options": [{"label": "Uno"}, {"label": "Due"}], "asked_at": NOW - 50}},
                      {"name": "delta", "next_steps": [{"text": "Riga a sinistra", "blocking": True}, {"text": "Bordo leggero", "blocking": True}]}],
         "attention": [{"kind": "question", "session": "gamma", "ref": "q-17", "title": "Quale?", "since": NOW - 50},
                       {"kind": "needs_you", "session": "eps", "ref": "eps", "title": "il codice del login", "since": NOW - 40},
                       {"kind": "turn_blocking", "session": "delta", "ref": "delta", "title": "Riga a sinistra", "since": NOW - 30},
                       {"kind": "turn_failed", "session": "delta", "ref": "delta", "title": "errore", "since": NOW - 20}]}

sp = importlib.util.spec_from_file_location("cm_relay_at", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
approvals = RL.S.build_state({"approvals": RL.approvals_list()}, NOW, fit=False)["approvals"]   # le carte come le pubblica lo stato, con wid
state["approvals"] = approvals
(tmp / "relay" / "last-state.json").write_text(json.dumps({"state": state, "full": state}))
AT = RL._load("cm-attese")

# AT1
lab = lambda a: [o["label"] for o in a.get("options") or []]  # noqa: E731
w1, w2 = AT.collect(), AT.collect()
by = {a["kind"]: a for a in w1}
ok = by.get("ok") or {}
ch = by.get("choice") or {}
pv = by.get("prova") or {}
q = by.get("question") or {}
ny = by.get("needs_you") or {}
px = by.get("prossimi") or {}
T.check("AT1 cards (ok, choice with options, prova) and chat waits (question, needs-you, Prossimi with its items) in one list, ids w+10 hex, the same twice",
        sorted(by) == ["choice", "needs_you", "ok", "prossimi", "prova", "question"] and len(w1) == 6
        and all(AT.ID_RE.fullmatch(a["id"]) for a in w1) and [a["id"] for a in w1] == [a["id"] for a in w2]
        and ok.get("task") == "at-ok" and ok.get("type") == "carta" and ok.get("text") == "pubblico la 1.0?" and ok.get("session") == "alfa"
        and lab(ch) == ["Scuro", "Chiaro"] and ch["options"][0].get("recommended") is True and ch["options"][0]["value"] == "Scuro (consigliata)"
        and "recommended" not in ch["options"][1]
        and pv.get("task") == "at-prova" and "apri la home" in (pv.get("text") or "")
        and lab(q) == ["Uno", "Due"] and q.get("type") == "chat" and ny.get("text") == "il codice del login"
        and lab(px) == ["Riga a sinistra", "Bordo leggero"] and px.get("session") == "delta", json.dumps(w1, ensure_ascii=False)[:1500])
T.check("AT1 the type label (decide/choose/verify/reply, «Decidi», «Scegli», «Verifica», «Rispondi»); an ok card says what, what is untested and how to undo",
        [ok["label"], ch["label"], pv["label"], q["label"], px["label"], ny["label"]] == ["decide", "choose", "verify", "reply", "reply", "reply"]
        and [ok["label_text"], ch["label_text"], pv["label_text"], q["label_text"]] == ["Decidi", "Scegli", "Verifica", "Rispondi"]
        and ok["card"] == {"what": "pubblico la 1.0?", "where": "web", "untested": "il login su iOS", "undo": "si", "undo_how": "tolgo il tag", "deploy": False}
        and q["card"] is None, json.dumps([ok.get("card"), ok.get("label_text")], ensure_ascii=False))
T.check("AT1 link <web_app_url>/#/attesa/<id> and the answering op of each wait",
        all(a["link"] == f"{WEB}/#/attesa/{a['id']}" for a in w1) and ok["answer"] == {"op": "approve", "arg": "at-ok"} and pv["answer"]["op"] == "prova"
        and q["answer"]["op"] == "answer" and q["answer"]["question"] == "q-17" and px["answer"]["op"] == "prompt" and ny["answer"]["kind"] == "needs_you",
        json.dumps([a["answer"] for a in w1]))

# AT2
st_ids = {a["task"]: a["wid"] for a in approvals}
att_ids = [RL.S.wid_attention(x) for x in state["attention"]]
cm("task", "wait-ok", "at-ok", "--what", "pubblico la 1.1?", "--where", "web")
time.sleep(1.1)
cm("task", "wait-ok", "at-ok", "--what", "pubblico la 1.2?", "--where", "web")
RL._MEMO.clear() if hasattr(RL, "_MEMO") else None
new_ok = next((a for a in AT.collect() if a.get("task") == "at-ok"), {})
cfg.write_text(json.dumps(dict(cfg_data, relay=dict(cfg_data["relay"], web_app_url=""))))
off = RL._load("cm-attese")
cfg.write_text(json.dumps(cfg_data))
T.check("AT2 the state carries the same ids (approvals[].wid, wid_attention); a new request of the same task gets a new id; web_app_url \"\" = no link",
        st_ids == {"at-ok": ok["id"], "at-scelta": ch["id"]} and att_ids == [q["id"], ny["id"], px["id"], None]
        and new_ok.get("id") not in (None, ok["id"]) and off.link("w0123456789") is None,
        f"{st_ids} {att_ids} {new_ok.get('id')} {ok['id']}")

# AT3
def line(kind, uid, content, ts):
    return json.dumps({"type": kind, "uuid": uid, "timestamp": ts, "message": {"role": kind, "content": content}})


tr_dir = tmp / "tr"
tr_dir.mkdir()
tr = tr_dir / "alfa.jsonl"
tr.write_text("\n".join([
    line("user", "u1", "fai la release", "2026-11-07T10:00:00Z"),
    line("assistant", "a1", [{"type": "text", "text": "Preparo la release."}], "2026-11-07T10:00:01Z"),
    line("assistant", "a2", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "supervisor task wait-ok at-ok --what 'pubblico?'"}}], "2026-11-07T10:00:02Z"),
    line("user", "u2", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}], "2026-11-07T10:00:03Z"),
    line("assistant", "a3", [{"type": "text", "text": "Serve il tuo ok per pubblicare la 1.0: la release esce sul sito.\n\nProssimi: ! ok pubblica"}], "2026-11-07T10:00:04Z"),
    line("user", "u3", "altro", "2026-11-07T10:05:00Z"),
    line("assistant", "a4", [{"type": "text", "text": "Fatto altro."}], "2026-11-07T10:05:01Z"),
    line("assistant", "a5", [{"type": "tool_use", "id": "t2", "name": "AskUserQuestion", "input": {"questions": [{"question": "Quale?", "options": [{"label": "Uno"}]}]}}], "2026-11-07T10:06:00Z"),
]) + "\n")
core = RL._load("cm-core")
es = core.transcript_entries(str(tr), 0)
o_ok = AT.origin_in(es, {"kind": "ok", "task": "at-ok"})
o_px = AT.origin_in(es, {"kind": "prossimi"})
o_q = AT.origin_in(es, {"kind": "question"})
o_none = AT.origin_in(es, {"kind": "pausa", "task": "at-ok"})
T.check("AT3 where it comes from: the reply after `task wait-ok ID`, the last text with «Prossimi» for a turn, the AskUserQuestion call; no call = None",
        o_ok and o_ok["id"] == "a3.0" and o_px and o_px["id"] == "a3.0" and o_q and o_q["id"] == "a5.0" and o_none is None,
        f"{o_ok and o_ok['id']} {o_px and o_px['id']} {o_q and o_q['id']} {o_none}")

# AT4
rows = [{"name": "alfa", "tmux": "alfa", "host": "win", "transcript": str(tr), "session_id": "sid-alfa"}]
RL.live_rows = lambda: rows
cur = next(a for a in AT.collect() if a.get("task") == "at-ok")
okx, tx = RL.execute({"op": "attesa", "arg": cur["id"], "device": "phone"})
rx = json.loads(tx) if okx else {}
bad = RL.execute({"op": "attesa", "arg": "x; rm", "device": "phone"})
gone = RL.execute({"op": "attesa", "arg": "w0000000000", "device": "phone"})
okl, tl = RL.execute({"op": "attese", "device": "web"})
cli = cm("attese")
T.check("AT4 op attesa: the wait with msg from the session's transcript; a bad or closed id is an error; op attese lists them; the CLI prints the links",
        "attesa" in RL.OPS and "attese" in RL.OPS and okx and rx.get("id") == cur["id"] and (rx.get("msg") or {}).get("id") == "a3.0"
        and (rx.get("msg") or {}).get("session_id") == "sid-alfa" and "1.0" in (rx.get("msg") or {}).get("text", "") and rx["msg"].get("uuid") == "a3"
        and rx["msg"].get("key") == "Serve il tuo ok per pubblicare la 1.0: la release esce sul sito."
        and not bad[0] and not gone[0] and "non aspetta" in gone[1] and okl and len(json.loads(tl)["items"]) == 6
        and cli.returncode == 0 and f"{WEB}/#/attesa/{cur['id']}" in cli.stdout,
        f"{okx} {tx[:400]} {bad} {gone} {cli.stdout[:300]} {cli.stderr[:300]}")

# AT5
hp = importlib.util.spec_from_file_location("cm_hook_at", T.SCRIPTS / "cm-hook.py")
HK = importlib.util.module_from_spec(hp)
hp.loader.exec_module(HK)
HK.my_tmux_name = lambda: "tst-capo"
line5 = HK.waiting_for_you()
T.check("AT5 the master's «Ti aspettano» line carries each item's link, ok cards included",
        f"{WEB}/#/attesa/{ok['id']}" in line5 and f"{WEB}/#/attesa/{q['id']}" in line5 and f"{WEB}/#/attesa/{px['id']}" in line5, line5[:600])

# AT6
SAMPLE = json.loads((T.SCRIPTS.parent.parent / "tests" / "fixtures" / "relay" / "cmd-result-sample.json").read_text())
smp = {r["id"].split("-")[1]: r for r in SAMPLE["result"]}
s_list, s_one, s_gone = json.loads(smp["1300"]["text"]), json.loads(smp["1301"]["text"]), smp["1302"]
real_card = next(a for a in json.loads(tl)["items"] if a["kind"] == "choice")
real_chat = next(a for a in json.loads(tl)["items"] if a["kind"] == "prossimi")
opt_keys = {"n", "label", "value", "recommended", "description"}
T.check("AT6 the cmd-result-sample examples (…1300 attese, …1301 attesa, …1302 closed) carry the fields of the real answers",
        set(s_list) == {"at", "items"} and set(s_list["items"][0]) == set(real_card) and set(s_list["items"][1]) == set(real_chat)
        and set(s_one) == set(rx) and set(s_one["msg"]) == set(rx["msg"])
        and set(s_one["answer"]) == set(real_card["answer"]) and set(s_list["items"][1]["answer"]) == set(real_chat["answer"])
        and all(set(o) <= opt_keys and {"n", "label", "value"} <= set(o) for a in s_list["items"] for o in a["options"])
        and all(set(o) <= opt_keys for o in real_card["options"]) and s_gone["ok"] is False
        and all(set(x) <= {"label", "style", "cmd", "text_field", "recommended"} and {"op", "session"} <= set(x["cmd"]) for a in s_list["items"] for x in a["actions"])
        and set(s_one["card"]) <= {"what", "where", "untested", "undo", "undo_how", "deploy"} and "deploy" in s_one["card"] and gone[1].startswith("w0000000000:")
        and s_list["items"][0]["id"] == s_one["id"] and s_one["link"].endswith("/#/attesa/" + s_one["id"]),
        f"{sorted(set(s_one) ^ set(rx))} {sorted(set(s_one['msg']) ^ set(rx.get('msg') or {}))} {sorted(set(s_list['items'][0]) ^ set(real_card))}")

# AT7 (1.131): title, card.deploy, actions; i comandi dei tasti passano dall'execute vero
def acts(a):
    return [(x["label"], x["style"], x["cmd"]["op"], x.get("text_field")) for x in a["actions"]]


answer_cmd = next(x["cmd"] for x in q["actions"] if x["label"] == "Due")
T.check("AT7 title is one line (the task's short name for a card, the question for chat), card.deploy names production, actions are the card's buttons",
        ok["title"] == "t at-ok" and q["title"] == "Quale?" and px["title"] == "Riga a sinistra" and ok["card"]["deploy"] is False and ch["card"]["deploy"] is True
        and acts(ok) == [("Approva", "primary", "approve", None), ("Chiudi senza ok", "secondary", "approval_close", "text")]
        and acts(ch)[0] == ("Scuro", "primary", "approve", None) and ch["actions"][0].get("recommended") is True and ch["actions"][0]["cmd"]["choice"] == "Scuro (consigliata)"
        and acts(ch)[1] == ("Chiaro", "option", "approve", None) and acts(ch)[2][2] == "approval_close"
        and acts(pv) == [("Va", "primary", "prova", None), ("Non va", "secondary", "prova", "text")] and pv["actions"][0]["cmd"]["result"] == "ok"
        and answer_cmd == {"session": "gamma", "op": "answer", "arg": "2", "question": "q-17"}
        and [x["label"] for x in px["actions"]] == ["Riga a sinistra", "Bordo leggero", "Rispondi", "Fatto"] and px["actions"][0]["cmd"] == {"session": "delta", "op": "prompt", "arg": "Riga a sinistra"}
        and px["actions"][3]["cmd"] == {"session": None, "op": "attention_done", "arg": "delta", "kind": "turn_blocking"}
        and [x["label"] for x in ny["actions"]] == ["Rispondi", "Fatto"], json.dumps([ok.get("title"), acts(ok), acts(ch), acts(pv), answer_cmd, acts(px)], ensure_ascii=False)[:900])
cmd_c = dict(ch["actions"][0]["cmd"], device="phone", by="maintainer")
okc, tc = RL.execute(cmd_c)
time.sleep(0.5)
T.check("AT7 the recommended option's command goes through execute as it is (approve with its choice); its talk goes to the fake binary, never to a real session",
        okc and "talk beta " in talklog.read_text(), f"{tc} {talklog.read_text()[-300:]!r}")

T.rm(tmp)
T.finish()
