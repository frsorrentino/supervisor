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
opt_keys = {"n", "label", "value", "recommended", "description", "files"}   # files dal 1.137
T.check("AT6 the cmd-result-sample examples (…1300 attese, …1301 attesa, …1302 closed) carry the fields of the real answers",
        set(s_list) == {"at", "items"} and set(s_list["items"][0]) == set(real_card) and set(s_list["items"][1]) == set(real_chat)
        and set(s_one) == set(rx) and set(s_one["msg"]) == set(rx["msg"])
        and set(s_one["answer"]) == set(real_card["answer"]) and set(s_list["items"][1]["answer"]) == set(real_chat["answer"])
        and all(set(o) <= opt_keys and {"n", "label", "value"} <= set(o) for a in s_list["items"] for o in a["options"] or [])
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
        and acts(ch)[1] == ("Chiaro", "option", "approve", None) and acts(ch)[2][2] == "approval_redo" and acts(ch)[3][2] == "approval_close"
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

# AT8 (1.137, 12/11/2026, supervisor-attesa-allegati-12-11): i file da guardare per decidere
(tmp / "mock").mkdir()
(tmp / "mock" / "home-a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
(tmp / "mock" / "home-b.pdf").write_bytes(b"%PDF-1.4 x")
(tmp / "segreto.txt").write_text("non dichiarato")
add("al-scelta", "beta")
add("al-prova", "alfa")
r8 = cm("task", "wait-ok", "al-scelta", "--what", "quale home?", "--where", "app", "--options", "A|B", "--files", "mock/home-a.png, mock/home-b.pdf")
r8bad = cm("task", "wait-ok", "al-scelta", "--what", "x", "--where", "y", "--files", "mock/manca.png")
r8p = cm("task", "prova", "al-prova", "--build", "b2", "--dove", "home", "--fai", "guarda", "--atteso", "uguale", "--files", str(tmp / "mock" / "home-a.png"))
w8 = {a.get("task"): a for a in AT.collect()}
c8, p8 = w8.get("al-scelta") or {}, w8.get("al-prova") or {}
fa, fb = str((tmp / "mock" / "home-a.png").resolve()), str((tmp / "mock" / "home-b.pdf").resolve())
T.check("AT8 wait-ok --files: paths relative to the task's folder; the choice card carries files [{path, name, mime, size, kind}] in that order; a missing file is refused with its name, nothing recorded",
        r8.returncode == 0 and [f["path"] for f in c8.get("files") or []] == [fa, fb]
        and c8["files"][0] == {"path": fa, "name": "home-a.png", "mime": "image/png", "size": 72, "kind": "image", "session": None}
        and c8["files"][1]["kind"] == "document" and c8["files"][1]["mime"] == "application/pdf"
        and r8bad.returncode != 0 and "mock/manca.png" in r8bad.stderr, json.dumps(c8.get("files")) + r8bad.stderr)
T.check("AT8 task prova --files: the prova card and the single test (op prove with a number) carry the file; a card without files has files null",
        r8p.returncode == 0 and [f["path"] for f in p8.get("files") or []] == [fa] and ok.get("files") is None
        and [f["path"] for f in (json.loads(RL.execute({"op": "prove", "arg": r8p.stdout.strip()})[1]).get("files") or [])] == [fa],
        json.dumps(p8.get("files")) + r8p.stdout + r8p.stderr)
ok8, t8 = RL.execute({"op": "attesa", "arg": c8.get("id"), "device": "phone"})
T.check("AT8 op attesa returns the files with the card", ok8 and [f["name"] for f in json.loads(t8).get("files") or []] == ["home-a.png", "home-b.pdf"], t8[:300])
f_ok = RL.execute({"op": "file", "session": None, "arg": fa, "id": "al-file-1", "_local": True, "device": "phone"})
f_no = RL.execute({"op": "file", "session": None, "arg": str((tmp / "segreto.txt").resolve()), "id": "al-file-2", "_local": True, "device": "phone"})
loc = json.loads((tmp / "relay" / "local" / "file" / "al-file-1.json").read_text()) if (tmp / "relay" / "local" / "file" / "al-file-1.json").exists() else {}
T.check("AT8 op file serves a file of an open request with no session (here to the local web app); a path no request declared is refused",
        f_ok[0] is True and loc.get("path") == fa and loc.get("mime") == "image/png" and f_no[0] is False, f"{f_ok} {f_no} {loc}")
_ap8 = RL.execute({"op": "approve", "arg": "al-scelta", "text": "ok", "choice": "A", "device": "phone", "by": "phone-pixel8", "session": None})
f_after = RL.execute({"op": "file", "session": None, "arg": fb, "id": "al-file-3", "_local": True, "device": "phone"})
# 1.142 (12/11, supervisor-file-scheda-chiusa-12-11): a scelta fatta la scheda sta in archivio, e i suoi file si servono ancora
f_nodecl = RL.execute({"op": "file", "session": None, "arg": str((tmp / "segreto.txt").resolve()), "id": "al-file-4", "_local": True, "device": "phone"})
import sqlite3 as _sq8
_c8 = _sq8.connect(str(tmp / "t.db"))
_c8.execute("UPDATE approvals SET at=at-?", (200 * 86400,))
_c8.execute("UPDATE prove SET at=at-?", (200 * 86400,))
_c8.commit(); _c8.close()
f_old = RL.execute({"op": "file", "session": None, "arg": fb, "id": "al-file-5", "_local": True, "device": "phone"})
T.check("AT8 (1.142) after the choice the card's files are still served while the card is in the archive; a path no request declared says so (not «no session»); older than the archive: «expired»",
        _ap8[0] and f_after[0] is True and f_nodecl[0] is False and "dichiara" in f_nodecl[1] and "no session" not in f_nodecl[1]
        and f_old[0] is False and "scaduto" in f_old[1], str((_ap8, f_after, f_nodecl, f_old)))

# AT9 da dove viene: con i file il messaggio che li ha mandati; senza, il testo del turno piu' vicino alla domanda
E = lambda i, role, text, **k: dict({"id": f"e{i}", "role": role, "text": text, "at": i, "files": None}, **k)  # noqa: E731
ents = [E(1, "user", "fammi i mockup"),
        E(2, "assistant", "Ecco i due mockup della home: A con la barra in alto, B con le schede in basso. Quale home preferisci?"),
        E(3, "tool", "SendUserFile", tool="SendUserFile", files=[{"path": fa}, {"path": fb}]),
        E(4, "tool", "supervisor task wait-ok al-scelta --what quale home?", tool="Bash"),
        E(5, "assistant", "Ho messo le scelte nel registro, ti aspettano nell'app.")]
a9 = {"kind": "choice", "task": "al-scelta", "text": "quale home?", "options": ["A", "B"], "files": [{"path": fa}]}
a9n = dict(a9, files=None)
ents_n = [E(1, "user", "che sfondo?"), E(2, "assistant", "Lo sfondo scuro regge meglio il contrasto; il chiaro e' piu' leggero. Quale sfondo vuoi?"),
          E(3, "tool", "supervisor task wait-ok al-scelta --what quale sfondo?", tool="Bash"), E(4, "assistant", "Ho messo la scelta nel registro.")]
a9s = {"kind": "choice", "task": "al-scelta", "text": "quale sfondo?", "options": ["Scuro", "Chiaro"], "files": None}
ents_o = [E(1, "user", "vai"), E(2, "tool", "supervisor task wait-ok al-scelta --what pubblico?", tool="Bash"), E(3, "assistant", "Pronto: pubblico la 1.0 quando dici ok.")]
T.check("AT9 «Da dove viene» of a request with files: the message that sent them (here SendUserFile with its caption), not the hand-off line after the wait-ok",
        (AT.origin_in(ents, a9) or {}).get("id") == "e3", str(AT.origin_in(ents, a9)))
T.check("AT9 without files: in the turn, the text sharing most words with the question wins over the hand-off line; with no better text, the one after the call as before",
        (AT.origin_in(ents_n, a9s) or {}).get("id") == "e2" and (AT.origin_in(ents_o, {"kind": "ok", "task": "al-scelta", "text": "pubblico?", "files": None}) or {}).get("id") == "e3",
        str([AT.origin_in(ents_n, a9s), AT.origin_in(ents_o, {"kind": "ok", "task": "al-scelta", "text": "pubblico?"})]))
# il caso vero delle 14:41 (master, app-mockup-sposta-master-12-11): la domanda e' la didascalia di SendUserFile, poi la
# chiamata, poi due comandi e un testo d'altro alle 14:42
ents_m = [E(1, "user", "fammi le proposte"),
          E(2, "tool", "Riga della Home in basso. Nell'ordine: A coi contatori, B minima.", tool="SendUserFile", files=[{"path": "/x/home-a.png"}]),
          E(3, "tool", "supervisor task wait-ok app-mockup-chat-home-12-11 --what \"Riga della Home\"", tool="Bash"),
          E(4, "tool", "Tasto rotondo che sposta la master. Nell'ordine: A frecce a raggiera, B pannello con lo schermo in miniatura.", tool="SendUserFile",
            files=[{"path": fa}]),
          E(5, "tool", "supervisor task wait-ok al-scelta --what \"Tasto rotondo che sposta la master\"", tool="Bash"),
          E(6, "tool", "ssh win Get-ChildItem skills", tool="Bash"),
          E(7, "assistant", "Win non ha le skill di design: ha solo cinque plugin, nessuno per l'interfaccia. Le installo lì.")]
a_m = {"kind": "choice", "task": "al-scelta", "text": "Tasto rotondo che sposta la master", "options": ["A frecce", "B pannello"], "files": None}
T.check("AT9 the real case of 14:41: the question was SendUserFile's caption → that entry, not the unrelated text of 14:42; with the file declared, the entry that sent it",
        (AT.origin_in(ents_m, a_m) or {}).get("id") == "e4" and (AT.origin_in(ents_m, dict(a_m, files=[{"path": fa}])) or {}).get("id") == "e4",
        str([AT.origin_in(ents_m, a_m), AT.origin_in(ents_m, dict(a_m, files=[{"path": fa}]))]))
# una richiesta senza --files il cui messaggio d'origine e' un SendUserFile: i file di quel messaggio, con la sua sessione
m10 = {"session": "master", "id": "e4", "text": "Tasto rotondo", "_files": [{"path": fa, "mime": "image/png", "size": 72}]}
a10 = AT.with_files({"kind": "choice", "files": None}, m10)
a10d = AT.with_files({"kind": "choice", "files": [{"path": fb, "name": "home-b.pdf", "mime": "application/pdf", "size": 10, "kind": "document"}]},
                     {"session": "master", "_files": [{"path": fa}]})
T.check("AT9 op attesa without declared files: the files of the message it comes from (SendUserFile), with that session for op file; declared files win, session null; msg loses the internal field",
        a10["files"] == [{"path": fa, "name": "home-a.png", "mime": "image/png", "size": 72, "kind": "image", "session": "master"}] and "_files" not in m10
        and [f["path"] for f in a10d["files"]] == [fb] and a10d["files"][0]["session"] is None, json.dumps([a10, a10d]))
# AT10 (1.137, supervisor-scheda-scelta-12-11): la scheda di scelta — descrizione, opzioni con riga e file, «nessuna, rifare»,
# giri nella stessa scheda, archivio dopo la risposta
add("sc-1", "beta")
(tmp / "mock" / "c.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 10)
r10 = cm("task", "wait-ok", "sc-1", "--what", "Tasto che sposta la master", "--desc", "Dove mettere il tasto rotondo che sposta la master fra i monitor.",
         "--option", "A frecce (consigliata)|chiaro, ma copre il testo|mock/home-a.png", "--option", "B pannello|si vede lo schermo, occupa spazio|mock/c.png,mock/home-b.pdf")
c10 = next((a for a in AT.collect() if a.get("task") == "sc-1"), {})
o10 = c10.get("options") or [{}, {}]
T.check("AT10 one wait-ok opens the choice card: description, each option with its line of pros and cons and its files, files = all of them, card_id = the id, state open, answered null; «where» optional",
        r10.returncode == 0 and c10.get("description", "").startswith("Dove mettere") and [o["label"] for o in o10] == ["A frecce", "B pannello"]
        and o10[0]["description"] == "chiaro, ma copre il testo" and [f["name"] for f in o10[1]["files"]] == ["c.png", "home-b.pdf"] and o10[0]["recommended"] is True
        and [f["name"] for f in c10["files"]] == ["home-a.png", "c.png", "home-b.pdf"] and c10["card_id"] == c10["id"] and c10["state"] == "open" and c10["answered"] is None,
        json.dumps(c10, ensure_ascii=False)[:900] + r10.stderr)
T.check("AT10 the card's buttons: the options, then «Nessuna, rifare» (op approval_redo, the reason in text), then «Chiudi senza ok»",
        [x["label"] for x in c10["actions"]] == ["A frecce", "B pannello", "Nessuna, rifare", "Chiudi senza ok"]
        and c10["actions"][2]["cmd"] == {"session": None, "op": "approval_redo", "arg": "sc-1"} and c10["actions"][2]["text_field"] == "text", json.dumps(c10["actions"], ensure_ascii=False))
talklog.write_text("")
rd0 = RL.execute({"op": "approval_redo", "arg": "sc-1", "text": "  ", "device": "phone", "by": "phone-pixel8"})
rd1 = RL.execute({"op": "approval_redo", "arg": "sc-1", "text": "troppo grandi, rifalle piccole", "device": "phone", "by": "phone-pixel8"})
st10 = json.loads(cm("task", "show", "sc-1").stdout)["state"]
T.check("AT10 approval_redo: without a reason refused; with it the round is discarded, the task back to running, the session told «nessuna, rifare» with the reason",
        rd0[0] is False and "motivo" in rd0[1] and rd1[0] is True and json.loads(rd1[1])["closed"] is True and st10 == "running"
        and "nessuna, rifare" in talklog.read_text() and "troppo grandi" in talklog.read_text(), f"{rd0} {rd1} {st10} {talklog.read_text()[-300:]!r}")
f_r1 = RL.execute({"op": "file", "session": None, "arg": str((tmp / "mock" / "c.png").resolve()), "id": "sc-file-1", "_local": True, "device": "phone"})
time.sleep(1.1)
r10b = cm("task", "wait-ok", "sc-1", "--what", "Tasto che sposta la master, giro 2", "--desc", "Più piccoli.", "--option", "C piccolo|discreto|mock/c.png", "--option", "D bordo|sempre visibile")
c10b = next((a for a in AT.collect() if a.get("task") == "sc-1"), {})
ok10b, t10b = RL.execute({"op": "attesa", "arg": c10b.get("id"), "device": "phone"})
x10b = json.loads(t10b) if ok10b else {}
T.check("AT10 a new wait-ok on the same task is a new round of the same card: a new id, the same card_id; op attesa lists the earlier round with its options, files, state redo and the reason",
        r10b.returncode == 0 and c10b.get("id") not in (None, c10["id"]) and c10b.get("card_id") == c10["id"] and f_r1[0] is True   # 1.142: un giro scartato sta in archivio, i suoi file si servono
        and [g["state"] for g in x10b.get("rounds") or []] == ["redo"] and x10b["rounds"][0]["id"] == c10["id"] and x10b["rounds"][0]["reason"].startswith("troppo grandi")
        and [o["label"] for o in x10b["rounds"][0]["options"]] == ["A frecce", "B pannello"] and x10b["rounds"][0]["options"][1]["files"][0]["name"] == "c.png"
        and x10b["options"][1]["files"] is None, json.dumps(x10b, ensure_ascii=False)[:900])
ap10 = RL.execute({"op": "approve", "arg": "sc-1", "text": "ok", "choice": "C piccolo", "device": "phone", "by": "phone-pixel8", "session": None})
gone10 = RL.execute({"op": "attesa", "arg": c10b["id"], "device": "phone"})
ar1 = RL.execute({"op": "attesa", "arg": c10b["id"], "archive": True, "device": "phone"})
ar0 = RL.execute({"op": "attesa", "arg": c10["id"], "archive": True, "device": "phone"})
y1, y0 = (json.loads(ar1[1]) if ar1[0] else {}), (json.loads(ar0[1]) if ar0[0] else {})
T.check("AT10 after the choice: op attesa without archive answers as before (no longer waiting); with archive true the card from the archive: state chosen, answered with by and choice, no buttons, the earlier round kept",
        ap10[0] and gone10[0] is False and ar1[0] and y1["state"] == "chosen" and y1["answered"]["choice"] == "C piccolo" and y1["answered"]["by"]
        and y1["actions"] == [] and y1["card_id"] == c10["id"] and [g["state"] for g in y1["rounds"]] == ["redo"] and y1["description"] == "Più piccoli."
        and [o["label"] for o in y1["options"]] == ["C piccolo", "D bordo"], json.dumps([ap10, gone10, y1], ensure_ascii=False)[:900])
T.check("AT10 the id of an earlier round opens the same card from the archive (old messages still open it); an id of no round stays an error",
        ar0[0] and y0["id"] == y1["id"] and y0["card_id"] == c10["id"]
        and RL.execute({"op": "attesa", "arg": "w0000000000", "archive": True, "device": "phone"})[0] is False, json.dumps(y0, ensure_ascii=False)[:300])
add("sc-2", "beta")
cm("task", "wait-ok", "sc-2", "--what", "x", "--option", "A|", "--option", "B")
rr = cm("task", "redo", "sc-2", "--by", "tester", "--reason", "nessuna va bene")
T.check("AT10 task redo from the terminal does the same as approval_redo; --option and --options together are refused",
        rr.returncode == 0 and json.loads(cm("task", "show", "sc-2").stdout)["state"] == "running"
        and cm("task", "wait-ok", "sc-2", "--what", "x", "--options", "A|B", "--option", "C").returncode != 0, rr.stdout + rr.stderr)
smp8 = {r["id"].split("-")[1]: r for r in SAMPLE["result"]}
s_one8 = json.loads(smp8["1301"]["text"])
T.check("AT8 the contract's samples: …1301 attesa with two files carrying the fields of the real answer; …1370 op file of an attachment with session null",
        {frozenset(f) for f in s_one8.get("files") or []} == {frozenset(f) for f in json.loads(t8)["files"]} and "files" in json.loads(smp8["1300"]["text"])["items"][1]
        and set(s_one8["rounds"][0]) == set(x10b["rounds"][0]) and set(json.loads(smp8["1390"]["text"])) == set(y1)
        and set(json.loads(smp8["1390"]["text"])["answered"]) == set(y1["answered"]) and smp8["1381"]["ok"] is False
        and next(c for c in SAMPLE["cmd"] if c["id"].split("-")[1] == "1370")["session"] is None and smp8["1370"]["ok"] is True, "")

T.rm(tmp)
T.finish()
