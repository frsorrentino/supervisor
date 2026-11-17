#!/usr/bin/env python3
"""Riallineamento, passo 4: il plugin (17/11/2026, supervisor-riall-4-plugin-16-11, contratto 1.152).

Dall'analisi di supervisor-app (docs/valutazioni/2026-11-16-chiarezza-sessione.md, capitolo 7), direzione A scelta dal manutentore:
- l'hook della master aggiungeva a OGNI prompt «Ti aspettano: … Dillo all'utente, anche se si parla d'altro», e la master
  ripeteva le voci in ogni messaggio, mentre le mostra l'app;
- i Prossimi che chiudono una voce («Prova 1293 ok») restavano toccabili dopo che la voce era chiusa;
- Esito, Tipo e Watch erano righe di testo lette con espressioni regolari dall'app.
Registro temporaneo, talk finto, il relay e l'hook caricati come moduli.

RP1 l'hook della master porta una voce una volta sola, quando entra, col suo link; al prompt dopo niente; una voce nuova
    porta solo quella; non chiede piu' di ripeterle «anche se si parla d'altro»
RP2 un Prossimo con l'id di una voce («! Approva #w…») ha `wid` e il testo senza l'id; un «!» legato a una voce non apre
    un turn_blocking suo (la voce c'e' gia')
RP3 alla push un Prossimo legato a una voce chiusa (o mai esistita) sparisce; quello di una voce aperta resta; dopo l'ok
    della carta sparisce anche quello
RP4 Esito, Tipo e Watch nello stato come campi: sessions[].outcome {esito, tipo, watch}; null quando la riga non c'e'
RP5 nella chat (transcript) la voce dell'assistente ha `fields` {esito, tipo, watch, prossimi} e `body`, il testo senza
    quelle righe; un messaggio senza le righe non ha campi
RP6 il kernel, in italiano e in inglese, chiede l'id della voce nei Prossimi che la chiudono e non chiede di riscrivere il
    consiglio di una carta aperta; la fixture del contratto 1.152 ha le chiavi che il codice scrive
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
state = tmp / "state"
cfg = tmp / "config.json"
WEB = "https://app.example.test"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "accounts": {"p": {"config_dir": str(home / ".claude")}}, "default_account": "p",
                           "workspace": {"root": str(tmp), "root_session_name": "tst-capo"}, "relay": {"dir": str(tmp / "relay"), "web_app_url": WEB + "/"}}))
(tmp / "relay").mkdir()
(tmp / "run").mkdir()
fake = tmp / "fake-cm"
fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {tmp / 'talk.log'}\nexit 0\n")
fake.chmod(0o755)
fake_relay = tmp / "fake-relay-cm"
fake_relay.write_text(f"#!/bin/sh\nif [ \"$1\" = talk ]; then exit 0; fi\nexec {T.SCRIPTS / 'supervisor'} \"$@\"\n")
fake_relay.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update({"CM_TASKS_DB": str(tmp / "t.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASK_TALK_BIN": str(fake),
            "CM_RELAY_NO_PUSH": "1", "CM_RELAY_CM": str(fake_relay), "CM_TALK_CM": str(fake), "TMUX_TMPDIR": str(tmp / "run"),
            "CM_TMUX_ARGS": "-L riall-plugin-verify", "XDG_RUNTIME_DIR": str(tmp / "run")})
os.environ.clear()
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")


def load(name, mod):
    sp = importlib.util.spec_from_file_location(mod, T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


# RP1 — la riga della master
now = int(time.time())
att = [{"kind": "needs_you", "session": "pix-docs", "ref": "pix-docs", "title": "login su win", "since": now - 720},
       {"kind": "question", "session": "alfa", "ref": "q-1-1", "title": "quale sfondo?", "since": now - 300}]
(tmp / "relay" / "last-state.json").write_text(json.dumps({"full": {"attention": att}}))
HK = load("cm-hook", "cm_hook_rp")
HK.my_tmux_name = lambda: "tst-capo"
AT = load("cm-attese", "cm_attese_rp")
l1 = HK.waiting_for_you()
l2 = HK.waiting_for_you()
att.append({"kind": "needs_you", "session": "beta", "ref": "beta", "title": "il codice di 6 cifre", "since": now - 30})
(tmp / "relay" / "last-state.json").write_text(json.dumps({"full": {"attention": att}}))
l3 = HK.waiting_for_you()
w_docs = AT.wid_attention(att[0])
T.check("RP1 the master's hook brings an item once, when it comes in, with its link; at the next prompt nothing",
        "pix-docs" in l1 and "alfa" in l1 and f"{WEB}/#/attesa/{w_docs}" in l1 and l2 == "", f"«{l1}» «{l2}»")
T.check("RP1 a new item brings only that one, and the line no longer asks to repeat them «even if you are talking about something else»",
        "beta" in l3 and "pix-docs" not in l3 and "alfa" not in l3 and "anche se si parla d'altro" not in l1 + l3, f"«{l3}»")

# RP2 — i Prossimi legati a una voce
S = load("cm-relay-state", "cm_relay_state_rp")
W_OPEN, W_GONE = "w0123456789", "wfedcba9876"
TEXT = ("Ho preparato la carta.\nEsito: la carta della release è aperta\nTipo: aspetta\n"
        f"Prossimi: ! Approva #{W_OPEN} · rilancia i test · vecchia #{W_GONE}\nWatch: carta aperta")
steps = S.next_steps(TEXT)
ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 10))
row = {"tmux": "alfa", "name": "alfa", "session_id": "sid-alfa", "status": "idle", "cwd": str(tmp)}
sess = S.build_session(dict(row), {"ledger": [{"event": "prompt", "session_id": "sid-alfa", "ts": ts},
                                              {"event": "stop", "session_id": "sid-alfa", "ts": ts, "tail": TEXT}]})
T.check("RP2 a next step with an item's id has `wid` and its text without the id; one without keeps no wid",
        steps == [{"text": "Approva", "blocking": True, "wid": W_OPEN}, {"text": "rilancia i test", "blocking": False},
                  {"text": "vecchia", "blocking": False, "wid": W_GONE}], json.dumps(steps, ensure_ascii=False))
T.check("RP2 a «!» bound to an item opens no turn_blocking of its own (the item is already there)",
        not sess.get("turn_blocking") and len(sess.get("next_steps") or []) == 3, json.dumps(sess.get("turn_blocking")))

# RP3 — alla push: le voci chiuse tolgono i loro Prossimi
c = {"schema_version": 1, "task": {"id": "rp-ok", "title": "t rp-ok", "plan": "p", "where": {"project": str(tmp), "session": "alfa", "host": "local", "account": None},
                                   "check": {"cmd": "true", "cwd": str(tmp), "timeout_s": 30}, "perimeter": ["x"], "lane": "open", "depends_on": [],
                                   "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
                                   "outcome": {"expected": "x", "kind": "custom", "proof": "x"}}}
assert subprocess.run([CLI, "task", "add", "-"], input=json.dumps(c), capture_output=True, text=True, env=ENV).returncode == 0
subprocess.run([CLI, "task", "start", "rp-ok"], capture_output=True, env=ENV)
r = subprocess.run([CLI, "task", "wait-ok", "rp-ok", "--consiglio", "va bene: prova", "--what", "pubblico la 1.0?", "--where", "web"], capture_output=True, text=True, env=ENV)
assert r.returncode == 0, r.stderr
RL = load("cm-relay", "cm_relay_rp")
items = RL.attese_view({"sessions": [], "approvals": RL.approvals_list()}, time.time(), [], write=False) or []
card = next((a for a in items if a.get("task") == "rp-ok"), None)
W_CARD = card["id"] if card else "w0000000000"
full = {"sessions": [{"name": "alfa", "next_steps": [{"text": "Approva", "blocking": True, "wid": W_CARD}, {"text": "rilancia i test", "blocking": False},
                                                       {"text": "vecchia", "blocking": False, "wid": W_GONE}]}], "approvals": RL.approvals_list()}
RL.attese_view(full, time.time(), [], write=False)
k1 = [x["text"] for x in full["sessions"][0].get("next_steps") or []]
TK = RL._load("cm-tasks")
con = TK.connect()
TK.approve(con, "rp-ok", "maintainer", "ok (test)")
con.close()
RL._MEMO.clear()
full2 = {"sessions": [{"name": "alfa", "next_steps": [{"text": "Approva", "blocking": True, "wid": W_CARD}, {"text": "rilancia i test", "blocking": False}]}],
         "approvals": RL.approvals_list()}
RL.attese_view(full2, time.time(), [], write=False)
k2 = [x["text"] for x in full2["sessions"][0].get("next_steps") or []]
T.check("RP3 at the push a next step bound to a closed (or unknown) item is gone, the one of the open card stays, the plain one stays",
        card is not None and k1 == ["Approva", "rilancia i test"], f"card={card and card['id']} {k1}")
T.check("RP3 after the card's ok its next step is gone too", k2 == ["rilancia i test"], str(k2))

# RP4 — i campi dell'esito nello stato
o = sess.get("outcome") or {}
plain = S.build_session(dict(row), {"ledger": [{"event": "stop", "session_id": "sid-alfa", "ts": ts, "tail": "Ho risposto alla domanda."}]}).get("outcome") or {}
T.check("RP4 sessions[].outcome carries esito, tipo (the key) and watch as fields",
        o.get("esito") == "la carta della release è aperta" and o.get("tipo") == "waiting" and o.get("watch") == "carta aperta", json.dumps(o, ensure_ascii=False)[:400])
T.check("RP4 without those lines the fields are absent (the state has an 8 KB cap)", not ({"esito", "tipo", "watch"} & set(plain)),
        json.dumps(plain, ensure_ascii=False)[:300])

# RP5 — la chat
CORE = load("cm-core", "cm_core_rp")
tr = tmp / "t.jsonl"
with open(tr, "w") as f:
    for i, t_ in enumerate((TEXT, "Solo una spiegazione, senza righe finali.")):
        f.write(json.dumps({"type": "assistant", "uuid": f"a{i}", "sessionId": "sid-alfa", "timestamp": "2026-11-17T10:00:0%dZ" % i,
                            "message": {"role": "assistant", "content": [{"type": "text", "text": t_}]}}) + "\n")
ents = [e for e in CORE.transcript_entries(str(tr)) if e.get("role") == "assistant"]
f0 = ents[0].get("fields") or {}
T.check("RP5 the assistant's entry has fields {esito, tipo, watch, prossimi} and body, the text without those lines",
        f0.get("esito") == "la carta della release è aperta" and f0.get("tipo") == "waiting" and f0.get("watch") == "carta aperta"
        and [x.get("wid") for x in f0.get("prossimi") or []] == [W_OPEN, None, W_GONE] and ents[0].get("body") == "Ho preparato la carta.",
        json.dumps(ents[0], ensure_ascii=False)[:600])
T.check("RP5 a message without those lines has no fields and no body", "fields" not in ents[1] and "body" not in ents[1], json.dumps(ents[1], ensure_ascii=False)[:300])

# RP6 — il kernel e la fixture
k_it = (T.PLUGIN / "kernel_it.md").read_text()
k_en = (T.PLUGIN / "kernel_en.md").read_text()
T.check("RP6 the kernel (it and en) asks for the item's id in a next step that closes it, and not to rewrite the advice of an open card",
        "#w" in k_it and "#w" in k_en and "consiglio" in k_it.split("12.")[1] and "advice" in k_en.split("12.")[1]
        and "non riscriverlo" in k_it and "do not rewrite it" in k_en, "")
fx = json.loads((T.ROOT / "tests" / "fixtures" / "relay" / "riall-plugin-sample.json").read_text()) if (T.ROOT / "tests" / "fixtures" / "relay" / "riall-plugin-sample.json").exists() else {}
fs = (fx.get("state") or {}).get("sessions", [{}])[0] if fx else {}
T.check("RP6 the 1.152 fixture has the keys the code writes: outcome {esito, tipo, watch}, next_steps with wid, a chat entry with fields and body",
        fx and set(o) <= set(fs.get("outcome") or {}) and any(x.get("wid") for x in fs.get("next_steps") or [])
        and set(ents[0]) <= set((fx.get("transcript") or [{}])[0]), json.dumps(fx, ensure_ascii=False)[:300])

T.rm(tmp)
T.finish()
