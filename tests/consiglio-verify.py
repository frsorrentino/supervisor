#!/usr/bin/env python3
"""Ogni carta di decisione porta il consiglio di chi chiede (16/11/2026, supervisor-consiglio-carte-16-11, contratto 1.149).

La persona, dalla web app alle 10:24 e alle 10:29: «vorrei che aggiungessi ad ogni decisione da prendere in ti aspetta il
tuo suggerimento con consigliato» e «una breve motivazione del consiglio». Registro, configurazione e relay in una
cartella temporanea; nessun talk vero.

CO1  `task wait-ok` senza --consiglio e' rifiutato con un errore che dice come scriverlo, e la carta non nasce (il compito
     resta dov'era); con --consiglio nasce
CO2  una carta di scelta vuole anche l'opzione: --consigliata N o l'etichetta; senza e' rifiutata; «(consigliata)» in fondo a
     UNA etichetta vale; un numero o un'etichetta che non c'e', un consiglio vuoto o oltre 200 caratteri: rifiutati
CO3  una carta di ok: si consiglia di approvare; --consigliata no = si consiglia di non approvare; un altro valore e' rifiutato
CO4  il registro lo tiene: task list --json (request.advice), task decisioni, i giri della scheda
CO5  le attese: advice {text, option, approve}; l'opzione consigliata ha recommended true (e solo lei) e il suo tasto e'
     «primary» con recommended; su un ok il tasto «Approva» ha recommended true; con «no» lo ha «Chiudi senza ok»
CO6  lo stato del relay: approvals[].advice con le sole chiavi che dicono qualcosa ({text, approve} o {text, option})
CO7  `task advise ID --consiglio T [--option N]` su una carta gia' aperta: lo aggiunge (una carta nata prima della 1.149)
     o lo cambia; su un compito senza carta aperta e' rifiutato; stesse regole di wait-ok
CO8  la regola 12 del kernel, in italiano e in inglese, lo mette nel wait-ok che insegna; `supervisor task --help` nomina
     --consiglio
CO9  le fixture del contratto: la carta di scelta di attese e attesa porta advice (le chiavi di una carta vera);
     approvals-advice-sample.json un ok con {text, approve} e una scelta con {text, option}, con le chiavi delle voci vere;
     uno stato oltre gli 8 KB perde il consiglio per primo; il contratto e' in details/relay.md
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"p": {"config_dir": str(home / ".claude")}},
                           "default_account": "p", "workspace": {"root": str(tmp), "root_session_name": "tst-capo"},
                           "relay": {"dir": str(tmp / "relay"), "web_app_url": "https://app.example.test/"}}))
(tmp / "relay").mkdir()
(tmp / "run").mkdir()
fake = tmp / "fake-cm"
fake.write_text("#!/bin/sh\nexit 0\n")
fake.chmod(0o755)
fake_relay = tmp / "fake-relay-cm"   # il relay legge il registro con la CLI vera; i talk restano finti
fake_relay.write_text(f"#!/bin/sh\nif [ \"$1\" = talk ]; then exit 0; fi\nexec {T.SCRIPTS / 'supervisor'} \"$@\"\n")
fake_relay.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update({"CM_TASKS_DB": str(tmp / "t.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASK_TALK_BIN": str(fake),
            "CM_RELAY_NO_PUSH": "1", "CM_RELAY_CM": str(fake_relay), "CM_TALK_CM": str(fake), "TMUX_TMPDIR": str(tmp / "run"), "CM_TMUX_ARGS": "-L consiglio-verify",
            "XDG_RUNTIME_DIR": str(tmp / "run")})
os.environ.clear()
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")


def cm(*a, stdin=None):
    return subprocess.run([CLI, *a], input=stdin, capture_output=True, text=True, env=ENV, timeout=120)


def add(tid, session="alfa"):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"t {tid}", "plan": "p", "where": {"project": str(tmp), "session": session, "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": str(tmp), "timeout_s": 30}, "perimeter": ["x"], "lane": "open", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
                                       "outcome": {"expected": "x", "kind": "custom", "proof": "x"}}}
    assert cm("task", "add", "-", stdin=json.dumps(c)).returncode == 0
    cm("task", "start", tid)


def state_of(tid):
    return next((t["state"] for t in json.loads(cm("task", "list", "--json").stdout) if t["id"] == tid), None)


def request(tid):
    return next((t.get("request") for t in json.loads(cm("task", "list", "--json").stdout) if t["id"] == tid), None) or {}


def attesa(tid):
    got = json.loads(cm("attese", "--json").stdout or "[]")
    items = got if isinstance(got, list) else got.get("items") or []
    return next((x for x in items if x.get("task") == tid), {})


WHY = "la release è provata e si torna indietro con un tag"

# CO1
for t in ("co-ok", "co-scelta", "co-no", "co-vecchia", "co-chiusa"):
    add(t)
r0 = cm("task", "wait-ok", "co-ok", "--what", "pubblico la 2.0?", "--where", "web")
T.check("CO1 wait-ok with no --consiglio is refused, the error says how to write it, and no card is born (the task stays running)",
        r0.returncode != 0 and "--consiglio" in r0.stderr and "advice" in r0.stderr and "--consigliata" in r0.stderr and state_of("co-ok") == "running" and not request("co-ok"),
        f"{r0.returncode} {r0.stderr} {state_of('co-ok')}")
r1 = cm("task", "wait-ok", "co-ok", "--what", "pubblico la 2.0?", "--where", "web", "--consiglio", WHY)
T.check("CO1 with --consiglio the card is born", r1.returncode == 0 and state_of("co-ok") == "awaiting_ok", r1.stdout + r1.stderr)

# CO2
base = ("task", "wait-ok", "co-scelta", "--what", "quale sfondo?", "--where", "app", "--options", "Scuro|Chiaro|Sistema")
bad = {"no option": cm(*base, "--consiglio", "il più leggibile di notte"),
       "number out of range": cm(*base, "--consiglio", "x", "--consigliata", "4"),
       "unknown label": cm(*base, "--consiglio", "x", "--consigliata", "Verde"),
       "empty": cm(*base, "--consiglio", "  ", "--consigliata", "1"),
       "too long": cm(*base, "--consiglio", "x" * 201, "--consigliata", "1"),
       "two marked": cm("task", "wait-ok", "co-scelta", "--what", "q", "--where", "app", "--options", "A (consigliata)|B (consigliata)", "--consiglio", "x")}
T.check("CO2 a choice card needs the option too: without it, with a number or a label that is not there, an empty advice, one over 200 characters, two labels marked: all refused, no card",
        all(r.returncode != 0 for r in bad.values()) and "--consigliata" in bad["no option"].stderr and "1-3" in bad["no option"].stderr
        and "Scuro | Chiaro | Sistema" in bad["unknown label"].stderr and "200" in bad["too long"].stderr and state_of("co-scelta") == "running",
        str({k: (r.returncode, r.stderr.strip()[:90]) for k, r in bad.items()}))
r2 = cm(*base, "--consiglio", "il più leggibile di notte", "--consigliata", "2")
a2 = request("co-scelta").get("advice")
T.check("CO2 --consigliata 2 → the second option, with the reason", r2.returncode == 0 and a2 == {"text": "il più leggibile di notte", "option": "Chiaro", "approve": None}, f"{r2.stderr} {a2}")
cm("task", "wait-ok", "co-scelta", "--what", "quale sfondo?", "--where", "app", "--options", "Scuro|Chiaro|Sistema", "--consiglio", "segue il telefono", "--consigliata", "Sistema")
a2b = request("co-scelta").get("advice")
add("co-segno")
cm("task", "wait-ok", "co-segno", "--what", "quale?", "--where", "app", "--options", "Scuro (consigliata)|Chiaro", "--consiglio", "meno luce")
a2c = request("co-segno").get("advice")
T.check("CO2 the label works as well as the number, and «(consigliata)» at the end of ONE label counts without --consigliata",
        a2b == {"text": "segue il telefono", "option": "Sistema", "approve": None} and a2c == {"text": "meno luce", "option": "Scuro (consigliata)", "approve": None}, f"{a2b} {a2c}")

# CO3
r3 = cm("task", "wait-ok", "co-no", "--what", "mando la PEC?", "--where", "qui", "--consiglio", "manca la firma: meglio aspettare domani", "--consigliata", "no")
r3bad = cm("task", "wait-ok", "co-vecchia", "--what", "x", "--where", "y", "--consiglio", "x", "--consigliata", "forse")
T.check("CO3 an ok card: the advice is to approve; --consigliata no = the advice is not to approve; another value is refused",
        request("co-ok").get("advice") == {"text": WHY, "option": None, "approve": True}
        and r3.returncode == 0 and request("co-no").get("advice") == {"text": "manca la firma: meglio aspettare domani", "option": None, "approve": False}
        and r3bad.returncode != 0 and "si or no" in r3bad.stderr, f"{request('co-ok').get('advice')} {request('co-no').get('advice')} {r3bad.stderr}")

# CO4
dec = json.loads(cm("task", "decisioni", "--json").stdout or "{}")
items = {it["task"]: it for g in dec.get("groups") or [] for it in g.get("items") or []}
T.check("CO4 the registry keeps it: task decisioni carries advice on the ok items", (items.get("co-ok") or {}).get("advice") == {"text": WHY, "option": None, "approve": True}
        and (items.get("co-scelta") or {}).get("advice", {}).get("option") == "Sistema", str({k: v.get("advice") for k, v in items.items()}))

# CO5
ok5, sc5, no5 = attesa("co-ok"), attesa("co-scelta"), attesa("co-no")
acts = lambda a: {x["cmd"]["op"] + ("/" + str(x["cmd"].get("choice")) if x["cmd"].get("choice") else ""): x for x in a.get("actions") or []}  # noqa: E731
T.check("CO5 attese, an ok card: advice {text, option null, approve true}; the «Approva» button has recommended true, «Chiudi senza ok» has not",
        ok5.get("advice") == {"text": WHY, "option": None, "approve": True} and acts(ok5)["approve"].get("recommended") is True and "recommended" not in acts(ok5)["approval_close"],
        f"{ok5.get('advice')} {ok5.get('actions')}")
rec5 = [(o["label"], o.get("recommended")) for o in sc5.get("options") or []]
T.check("CO5 attese, a choice card: advice {text, option = the value of the advised option, approve null}; that option alone has recommended true, and its button is primary with recommended",
        sc5.get("advice") == {"text": "segue il telefono", "option": "Sistema", "approve": None} and rec5 == [("Scuro", None), ("Chiaro", None), ("Sistema", True)]
        and acts(sc5)["approve/Sistema"].get("recommended") is True and acts(sc5)["approve/Sistema"]["style"] == "primary"
        and acts(sc5)["approve/Scuro"]["style"] == "option" and "recommended" not in acts(sc5)["approve/Scuro"], f"{sc5.get('advice')} {rec5} {sc5.get('actions')}")
T.check("CO5 attese, the advice not to approve: approve false, recommended on «Chiudi senza ok» and not on «Approva»",
        no5.get("advice", {}).get("approve") is False and acts(no5)["approval_close"].get("recommended") is True and "recommended" not in acts(no5)["approve"], str(no5.get("actions")))
one = json.loads(cm("attese", "show", sc5.get("id") or "x", "--json").stdout or "{}")
T.check("CO5 attese show ID carries the same advice", one.get("advice") == sc5.get("advice"), str(one.get("advice")))

# CO6: lo stato del relay
import importlib.util  # noqa: E402
sp = importlib.util.spec_from_file_location("cm_relay_co", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
ap = {a["task"]: a for a in RL.approvals_list()}
T.check("CO6 the relay's state: approvals[].advice with only the keys that say something ({text, approve} on an ok, {text, option} on a choice)",
        ap["co-ok"].get("advice") == {"text": WHY, "approve": True} and ap["co-scelta"].get("advice") == {"text": "segue il telefono", "option": "Sistema"}
        and ap["co-no"].get("advice") == {"text": "manca la firma: meglio aspettare domani", "approve": False}, str({k: v.get("advice") for k, v in ap.items()}))

# CO7: advise
cm("task", "wait-ok", "co-vecchia", "--what", "pubblico?", "--where", "web", "--consiglio", "x")
con = sqlite3.connect(tmp / "t.db")
con.execute("UPDATE approvals SET advice=NULL WHERE task='co-vecchia'")   # una carta nata prima della 1.149
con.commit()
con.close()
old7 = attesa("co-vecchia")
r7 = cm("task", "advise", "co-vecchia", "--consiglio", "provata sul telefono: approva")
new7 = attesa("co-vecchia")
T.check("CO7 a card born before 1.149 has advice null and no recommended button; task advise ID --consiglio T adds it",
        old7.get("advice") is None and "advice" in old7 and not any(x.get("recommended") for x in old7.get("actions") or [])
        and r7.returncode == 0 and "added" in r7.stdout and new7.get("advice") == {"text": "provata sul telefono: approva", "option": None, "approve": True}, f"{old7.get('advice')} {r7.stdout}{r7.stderr} {new7.get('advice')}")
r7b = cm("task", "advise", "co-scelta", "--option", "1", "--consiglio", "ripensandoci: il buio stanca meno")
r7c = cm("task", "advise", "co-scelta", "--consiglio", "senza opzione")
r7d = cm("task", "advise", "co-chiusa", "--consiglio", "x")
r7e = cm("task", "advise", "co-ok")
T.check("CO7 advise changes it on a choice (--option N); without the option, on a task with no open card, or with no --consiglio it is refused",
        r7b.returncode == 0 and "changed" in r7b.stdout and attesa("co-scelta").get("advice") == {"text": "ripensandoci: il buio stanca meno", "option": "Scuro", "approve": None}
        and r7c.returncode != 0 and r7d.returncode != 0 and "no open card" in r7d.stderr and r7e.returncode != 0 and "--consiglio" in r7e.stderr,
        f"{r7b.stdout}{r7b.stderr} | {r7c.stderr} | {r7d.stderr} | {r7e.stderr}")

# CO8
for lang in ("it", "en"):
    r12 = next(x for x in (T.PLUGIN / f"kernel_{lang}.md").read_text(encoding="utf-8").splitlines() if x.startswith("12."))
    T.check(f"CO8 rule 12 of the kernel ({lang}) names --consiglio T in the wait-ok it tells the sessions to write (the rest is in the CLI's error)",
            "`task wait-ok … --consiglio T --untested T" in r12, r12)
h = cm("task", "--help")
T.check("CO8 supervisor task --help names --consiglio and task advise", h.returncode == 0 and "--consiglio" in h.stdout and "task advise ID" in h.stdout, h.stdout[:300] + h.stderr)

# CO9: le fixture del contratto
FX = T.ROOT / "tests" / "fixtures" / "relay"
smp = json.loads((FX / "cmd-result-sample.json").read_text(encoding="utf-8"))
found = []
for r in smp["result"]:
    t = r.get("text")
    if isinstance(t, str) and '"advice"' in t:
        try:
            o = json.loads(t)
        except ValueError:
            continue
        for it in (o.get("items") if isinstance(o.get("items"), list) else [o]):
            if isinstance(it, dict) and it.get("kind") in ("ok", "choice") and it.get("advice"):
                found.append((it["kind"], set(it["advice"]), [bool(x.get("recommended")) for x in it.get("actions") or []],
                              [bool(x.get("recommended")) for x in it.get("options") or []]))
fa = json.loads((FX / "approvals-advice-sample.json").read_text(encoding="utf-8"))["approvals"]
st = [a.get("advice") for a in fa]
real_ok, real_ch = dict(ap["co-ok"], wid="w"), dict(ap["co-scelta"], wid="w")   # wid lo aggiunge build_state
T.check("CO9 the contract's samples: the choice card of attese and attesa with advice {text, option, approve} and the advised option marked; approvals-advice-sample.json an ok card with {text, approve} and a choice with {text, option}, with the keys of the real state items",
        len(found) == 3 and all(k == "choice" and keys == {"text", "option", "approve"} and o_ == [True, False] for k, keys, _, o_ in found)
        and st == [{"text": "Tested on staging; a tag undoes it", "approve": True}, {"text": "Fewer steps to pay", "option": "Strada A"}]
        and set(fa[0]) == set(real_ok) and set(fa[1]) == set(real_ch), f"{found} {st} {sorted(fa[0])} {sorted(real_ok)} {sorted(fa[1])} {sorted(real_ch)}")
S9 = RL.S
base = json.loads((FX / "state-1-question.json").read_text(encoding="utf-8"))   # sta al tetto: 4 byte liberi
base.pop("published_at", None)
big = json.loads(json.dumps(base))
big["approvals"][0]["advice"] = dict(fa[0]["advice"])
fit = S9.fit_state(json.loads(json.dumps(big)), 8)
small = S9.fit_state({"v": 1, "sessions": [], "approvals": [dict(a) for a in fa]}, 8)
T.check("CO9 a state under the 8 KB cap keeps the advice; state-1, which sits at the cap, with an advice added goes over it and loses the advice and nothing else",
        all(a.get("advice") for a in small["approvals"]) and S9.size_of(big) + S9.PUBLISHED_AT_BYTES > 8192 and not any("advice" in a for a in fit["approvals"])
        and fit == base, f"{S9.size_of(big)} {[('advice' in a) for a in fit['approvals']]} {fit == base}")
real, fx = attesa("co-scelta"), None
for r in smp["result"]:
    if r["id"].endswith("1300"):
        fx = next(x for x in json.loads(r["text"])["items"] if x.get("kind") == "choice")
T.check("CO9 the sample's card has the keys of a real choice card, advice included (after description)",
        fx is not None and "advice" in fx and set(real) ^ set(fx) <= {"rounds"} and list(fx).index("advice") == list(fx).index("description") + 1
        and list(real).index("advice") == list(real).index("description") + 1,
        f"{list(fx or {})} | {list(real)}")
doc = (T.ROOT / "details" / "relay.md").read_text(encoding="utf-8")
T.check("CO9 the contract is in details/relay.md (1.149, advice)", "Contract 1.149" in doc and "`advice`" in doc.split("Contract 1.149")[1], "")

T.rm(str(tmp))
T.finish()
