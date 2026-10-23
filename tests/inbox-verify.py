#!/usr/bin/env python3
"""Verifica la casella persistente (cm-inbox.py, talk, cm-hook) — 23/09/2026.

IB1 un messaggio in attesa: consegnato al SessionStart della destinataria (testo, mittente), poi non piu'
IB2 stesso nome tmux in un'altra cartella: niente consegna
IB3 scaduto (48 h): non si consegna, stato expired
IB4 oltre 5 messaggi: se ne mostrano 5, gli altri restano in attesa e il testo dice come leggerli
IB5 talk verso una sessione nota (registro) ma chiusa: exit 0, salvato; verso un nome mai visto: exit 3, niente salvato
IB6 cm-hook: SessionStart consegna nel contesto; Stop con messaggi in attesa → decision block; con stop_hook_active → niente
IB7 inbox status / talk --status
IB8 registro: righe talk e delivered nel diario, con la sola prima riga del testo
IB10 origin: «session» from talk (no option to change it), «relay» only through mark_origin
IB9 inbox cancel ID: un messaggio in attesa diventa cancelled e non si consegna piu'; uno gia' consegnato o un
    id ignoto: exit 1, niente cambia
IB11 prune (23/10/2026, «ok 7 giorno per supervisor»): sette giorni per ogni origine, anche mancante; le origini in
    `ages` la loro; un file con mtime fresco non si legge
"""
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
work = home / "ws" / "alfa"
work.mkdir(parents=True)
state = tmp / "state"
state.mkdir()
cfg = tmp / "config.json"
good = state / "sessions-good.json"
good.write_text(json.dumps({"salvato": "x", "sessioni": [{"nome": "chiusa", "cartella": str(work), "account": "personale"}]}))
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "default_account": "personale",
                           "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                           "registry": {"good_file": str(good), "file": str(state / "sessions.json")},
                           "hooks": {"local_time": {"enabled": False}, "session_kernel": {"enabled": False}}}))


def env(extra=None):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CM_PROC_SCAN_PIDS": ""}
    e.update(extra or {})
    return e


def py(code):
    """Esegue codice con il modulo cm-inbox caricato come `I`."""
    pre = ("import importlib.util,sys,json;s=importlib.util.spec_from_file_location('i',sys.argv[1]);I=importlib.util.module_from_spec(s);"
           "s.loader.exec_module(I);")
    return subprocess.run([sys.executable, "-c", pre + code, str(T.SCRIPTS / "cm-inbox.py")], capture_output=True, text=True, env=env(), timeout=30)


def recs(to):
    return [json.loads(p.read_text()) for p in sorted((state / "inbox" / to).glob("*.json"))] if (state / "inbox" / to).exists() else []


def load_status(rec_id):
    return json.loads(py(f"print(json.dumps(I.load({rec_id!r})))").stdout or 'null').get('status')


def hook(ev, payload, extra=None):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), ev], input=json.dumps(payload), capture_output=True, text=True,
                          env=env(extra), timeout=60)


# IB1-IB4 con il modulo
py(f"I.put('alfa','Risposta: la release e\\' pubblicata.\\nSeconda riga.','master',{str(work)!r})")
b1 = py(f"print(I.deliver_block('alfa',{str(work)!r},'session-start'))").stdout
b2 = py(f"print(I.deliver_block('alfa',{str(work)!r},'session-start'))").stdout.strip()
T.check("IB1 a pending message is delivered once, with sender and text; then nothing", "da master" in b1 and "la release e' pubblicata" in b1
        and "Seconda riga." in b1 and b2 == "" and recs("alfa")[0]["status"] == "delivered", b1 + "|" + b2)
py(f"I.put('alfa','per la vecchia alfa','master',{str(work)!r})")
other = home / "ws" / "altrove"
other.mkdir()
b3 = py(f"print(I.deliver_block('alfa',{str(other)!r},'session-start'))").stdout.strip()
T.check("IB2 same tmux name in another folder: not delivered, still pending", b3 == "" and any(r["status"] == "pending" for r in recs("alfa")), b3)
py("r=I.put('beta','vecchio','master');import json,os;p=I.box('beta')/(r['id']+'.json');d=json.loads(p.read_text());d['expires']=1;p.write_text(json.dumps(d))")
b4 = py("print(I.deliver_block('beta','','session-start'))").stdout.strip()
T.check("IB3 an expired message (past 48 h) is not delivered: status expired", b4 == "" and recs("beta")[0]["status"] == "expired", b4)
py("[I.put('gamma',f'messaggio numero {i}','master') for i in range(7)]")
b5 = py("print(I.deliver_block('gamma','','stop'))").stdout
T.check("IB4 over five messages: five shown, the rest still pending and the text says how to read them",
        b5.count("— da master") == 5 and "altri 2" in b5 and "supervisor inbox gamma" in b5
        and sum(1 for r in recs("gamma") if r["status"] == "pending") == 2, b5[-300:])

# IB5 talk
t1 = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "talk", "chiusa", "ciao dalla prova"], capture_output=True, text=True,
                    env=env({"CM_TMUX_ARGS": "-L cm-inbox-none"}), timeout=60)
t2 = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "talk", "mai-vista", "ciao"], capture_output=True, text=True,
                    env=env({"CM_TMUX_ARGS": "-L cm-inbox-none"}), timeout=60)
saved = recs("chiusa")
T.check("IB5 talk to a known but closed session → exit 0, saved with its folder; an unknown name → exit 3, nothing saved",
        t1.returncode == 0 and "salvato nella casella" in t1.stderr and len(saved) == 1 and saved[0]["to_cwd"] == os.path.realpath(work)
        and t2.returncode == 3 and not (state / "inbox" / "mai-vista").exists(), t1.stderr + t2.stderr)

# IB10 (0319ab, 14/10): l'origine del messaggio. `talk` scrive sempre «session» e non ha un'opzione per cambiarla;
# solo mark_origin (chiamata dal relay dopo il suo talk) scrive «relay» sul record appena consegnato.
t3 = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "talk", "chiusa", "ok dal finto telefono", "--origin", "relay"], capture_output=True,
                    text=True, env=env({"CM_TMUX_ARGS": "-L cm-inbox-none"}), timeout=60)
T.check("IB10 a message from `talk` has origin «session»; `talk --origin relay` is refused (exit 2), nothing saved",
        saved[0].get("origin") == "session" and t3.returncode == 2 and not any(r["text"] == "ok dal finto telefono" for r in recs("chiusa")), t3.stderr)
t0 = time.time()
phone = "Dall'utente via telefono. ok"
py(f"I.put('chiusa',{json.dumps(phone)},'supervisor')")
py("I.put('chiusa','altro testo','supervisor')")
mid = py(f"print(I.mark_origin('chiusa',{json.dumps(phone)},{t0},'relay'))").stdout.strip()
by_text = {r["text"]: r.get("origin") for r in recs("chiusa")}
T.check("IB10 mark_origin marks only the record with that text written after `since`: origin relay; the others stay session",
        mid not in ("", "None") and by_text.get("Dall'utente via telefono. ok") == "relay" and by_text.get("altro testo") == "session"
        and by_text.get("ciao dalla prova") == "session", str(by_text) + mid)
T.check("IB10 nothing matching → None, nothing changes", py(f"print(I.mark_origin('chiusa','mai scritto',{t0},'relay'))").stdout.strip() == "None", "")
t1 = time.time()
py("I.put('chiusa','testo composto da cm-report','supervisor')")
mid2 = py(f"print(I.mark_origin('chiusa',None,{t1},'relay'))").stdout.strip()
by_text = {r["text"]: r.get("origin") for r in recs("chiusa")}
T.check("IB10 mark_origin with text None (the report composes its own text) marks the newest record from `since`, not the older ones",
        mid2 not in ("", "None") and by_text.get("testo composto da cm-report") == "relay" and by_text.get("altro testo") == "session", str(by_text))

# IB10b (88c86e, 16/10): il segno del relay prima di talk → origin «relay» gia' alla creazione, mai una lettura «session»
py("C = I.claim_origin('segni', 'ok dal telefono, segno prima')")
py("I.put('segni','ok dal telefono, segno prima','supervisor')")
py("I.put('segni','ok dal telefono, segno prima','supervisor')")
first = [r.get("origin") for r in recs("segni") if r["text"] == "ok dal telefono, segno prima"]
py("I.claim_origin('segni', None)")
py("I.put('segni','segnalazione composta','supervisor')")
rep = [r.get("origin") for r in recs("segni") if r["text"] == "segnalazione composta"]
T.check("IB10b a claim before talk: the record is written with origin relay at once; the claim is used once (the second put stays session)",
        sorted(first) == ["relay", "session"], str(first))
T.check("IB10b a claim with text None (report) marks the next message to that session at its creation", rep == ["relay"], str(rep))
py("I.claim_origin('altra', 'per altra')")
py("I.put('segni','per altra','supervisor')")
T.check("IB10b a claim for another session does not mark this one", [r.get("origin") for r in recs("segni") if r["text"] == "per altra"] == ["session"], "")

# IB12 (17/10): `needs-you --list` era diventato il motivo «--list»; un'opzione sconosciuta e' rifiutata, nulla scritto
ny = subprocess.run([str(T.SCRIPTS / "supervisor"), "needs-you", "--list"], capture_output=True, text=True, env=env(), timeout=30)
T.check("IB12 needs-you with an unknown option → exit 2, «opzione sconosciuta --list», nothing saved",
        ny.returncode == 2 and "opzione sconosciuta --list" in ny.stderr and not (state / "needs-you").exists(), ny.stdout + ny.stderr)

# IB11 (14/10): talk --reopen verso una sessione chiusa la rilancia (per session_id se la trascrizione c'e') e consegna;
# se il rilancio non riesce il messaggio resta in casella come senza --reopen
fake_cm = tmp / "fake-cm.sh"
fake_cm.write_text(f"#!/bin/sh\necho \"$*\" >> {tmp / 'fake-cm.log'}\nexit 1\n")
fake_cm.chmod(0o755)
good.write_text(json.dumps({"salvato": "x", "sessioni": [{"nome": "chiusa", "cartella": str(work), "account": "personale", "session_id": "5e55-1d"}]}))
_slug = __import__("re").sub(r"[^A-Za-z0-9]", "-", os.path.realpath(work))
(home / ".claude" / "projects" / _slug).mkdir(parents=True, exist_ok=True)
(home / ".claude" / "projects" / _slug / "5e55-1d.jsonl").write_text("{}\n")
t4 = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "talk", "chiusa", "riapri e leggi", "--reopen", "--no-wait"], capture_output=True, text=True,
                    env=env({"CM_TMUX_ARGS": "-L cm-inbox-none", "CM_TALK_CM": str(fake_cm)}), timeout=120)
log4 = (tmp / "fake-cm.log").read_text() if (tmp / "fake-cm.log").exists() else ""
T.check("IB11 talk --reopen: «reopening (--resume <id>)», `launch <folder> --resume <id> --account personale`; the launch fails → saved in the inbox, exit 0",
        t4.returncode == 0 and "--resume 5e55-1d" in t4.stderr and log4.strip() == f"launch {work} --resume 5e55-1d --account personale"
        and any(r["text"] == "riapri e leggi" for r in recs("chiusa")), t4.stderr + log4)
good.write_text(json.dumps({"salvato": "x", "sessioni": [{"nome": "chiusa", "cartella": str(work), "account": "personale"}]}))

# IB6 hook, in un tmux privato: il nome della sessione e' «chiusa»
with T.PrivateTmux() as tm:
    tm("new-session", "-d", "-s", "chiusa", "-x", "100", "-y", "20", "sleep 60")
    pane = tm("list-panes", "-t", "chiusa", "-F", "#{pane_id}").stdout.strip()
    E = {**tm.env, "TMUX_PANE": pane}
    s1 = hook("SessionStart", {"session_id": "S1", "cwd": str(work), "source": "resume"}, E)
    T.check("IB6 SessionStart of the recipient (tmux name + folder) puts the pending message in its context",
            "ciao dalla prova" in s1.stdout and "MESSAGGI ARRIVATI" in s1.stdout and recs("chiusa")[0]["status"] == "delivered", s1.stdout[-400:] + s1.stderr)
    py(f"I.put('chiusa','arrivato mentre lavorava','master',{str(work)!r})")
    s2 = hook("Stop", {"session_id": "S1", "cwd": str(work), "stop_hook_active": True, "last_assistant_message": "x"}, E)
    s3 = hook("Stop", {"session_id": "S1", "cwd": str(work), "stop_hook_active": False, "last_assistant_message": "x"}, E)
    blk = json.loads(s3.stdout.strip().splitlines()[-1]) if s3.stdout.strip() else {}
    T.check("IB6 Stop: with stop_hook_active nothing; otherwise a decision block carrying the message, then delivered",
            "arrivato mentre lavorava" not in s2.stdout and blk.get("decision") == "block" and "arrivato mentre lavorava" in blk.get("reason", "")
            and all(r["status"] == "delivered" for r in recs("chiusa")), s2.stdout + "|" + s3.stdout[-300:])

# IB7
rid = recs("chiusa")[0]["id"]
st = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "talk", "--status", rid], capture_output=True, text=True, env=env(), timeout=30)
lst = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-inbox.py"), "gamma"], capture_output=True, text=True, env=env(), timeout=30)
T.check("IB7 talk --status ID says delivered and how; inbox NAME lists the pending ones", f"messaggio {rid}" in st.stdout and "delivered" in st.stdout
        and ("via session-start" in st.stdout or "via stop" in st.stdout) and lst.stdout.count("pending") == 2, st.stdout + st.stderr + lst.stdout)

# IB8
led = [json.loads(l) for l in (state / "ledger.jsonl").read_text().splitlines() if l.strip()]
talks = [r for r in led if r["event"] == "talk"]
T.check("IB8 the activity log: talk rows (sender, recipient, id, first line only) and delivered rows",
        any(r["to"] == "alfa" and r["text"] == "Risposta: la release e' pubblicata." for r in talks) and any(r["event"] == "delivered" for r in led)
        and not any("Seconda riga" in json.dumps(r) for r in led), json.dumps(talks[:2]))
# IB9: annullare un messaggio in attesa (01/10/2026, un /exit rimasto per una sessione chiusa prima di riceverlo)
py(f"I.put('delta','/exit','supervisor',{str(work)!r})")
did = recs("delta")[0]["id"]
inbox = lambda *a: subprocess.run([sys.executable, str(T.SCRIPTS / "cm-inbox.py")] + list(a), capture_output=True, text=True, env=env(), timeout=30)
c1 = inbox("cancel", did)
blk = py(f"print(I.deliver_block('delta',{str(work)!r},'session-start'))").stdout.strip()
T.check("IB9 cancel: the pending message turns cancelled and is no longer delivered at the next start",
        c1.returncode == 0 and "annullato" in c1.stdout and recs("delta")[0]["status"] == "cancelled" and blk == ""
        and "cancelled" in inbox("status", did).stdout, c1.stdout + c1.stderr + "|" + blk)
c2 = inbox("cancel", did)
c3 = inbox("cancel", rid)
c4 = inbox("cancel", "nessuno")
T.check("IB9 cancel refuses what is not pending (cancelled, delivered) and an unknown id: exit 1, the status unchanged",
        c2.returncode == 1 and c3.returncode == 1 and "delivered" in c3.stderr and c4.returncode == 1
        and load_status(rid) == "delivered", c2.stderr + c3.stderr + c4.stderr)
# IB11: la ritenzione della casella (23/10/2026)
py(f"I.put('zeta','talk vecchia','beta',{str(work)!r})")
py(f"I.put('zeta','relay vecchio','supervisor',{str(work)!r},origin='relay')")
py(f"I.put('zeta','senza origine','beta',{str(work)!r})")
py(f"I.put('zeta','talk di sei giorni','beta',{str(work)!r})")
py(f"I.put('zeta','prompt di due giorni','terminal',{str(work)!r},origin='terminal')")
py(f"I.put('zeta','creato ieri, file di oggi','beta',{str(work)!r})")
ages11 = {"talk vecchia": 8, "relay vecchio": 8, "senza origine": 8, "talk di sei giorni": 6, "prompt di due giorni": 2, "creato ieri, file di oggi": 8}
for p11 in (state / "inbox" / "zeta").glob("*.json"):
    d11 = json.loads(p11.read_text()); d11["created"] -= ages11[d11["text"]] * 86400
    if d11["text"] == "senza origine":
        del d11["origin"]
    p11.write_text(json.dumps(d11))
    if d11["text"] != "creato ieri, file di oggi":
        os.utime(p11, (d11["created"], d11["created"]))
py("I.prune('zeta', {'terminal': 86400})")
left11 = sorted(r["text"] for r in recs("zeta"))
T.check("IB11 prune: records older than seven days go whatever the origin (talk, relay, none); an origin in `ages` has its own retention; a record of six days and a file with a fresh mtime stay",
        left11 == ["creato ieri, file di oggi", "talk di sei giorni"], str(left11))
T.finish()
