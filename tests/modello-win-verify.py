#!/usr/bin/env python3
"""Il cambio di modello di una sessione su win, e talk verso una sessione ferma sul limite (16/11/2026,
supervisor-modello-win-16-11).

Il 16/11 win:supervisor-app e' rimasta ferma sul limite di Fable dalle 17:44 alle 19:07: ogni messaggio della master le
arrivava subito e riceveva subito «You've hit your Fable limit», che talk --wait mostrava come una risposta (sembrava
vecchia) dopo «consegnato»; due `supervisor model` sono finiti con «conferma non arrivata». Provato dal vivo su win, in
una config usa e getta con Claude Code 2.1.325 nativo e un finto server dell'API: la sequenza di supervisor (Esc no,
«s», «1» sul dialogo «Switch model?») va a segno nella console di Windows; non andava a segno perche' dalla 2.1.323 le
voci del selettore sono per famiglia («2. Opus», il modello nella descrizione: «Opus 5.5 · …») e supervisor cercava
«Opus 5.5»; e il segno del modello attuale e' «√». Gli schermi di quella prova sono in tests/fixtures/win-console/.

MW1  sugli schermi veri della console di win: le voci del selettore con il modello della descrizione; «√» non entra
     nell'etichetta; la voce di Opus 5.5 e' la «2. Opus»
MW2  supervisor model verso una sessione di win (console finta che rimette gli schermi veri): su «Opus», «s», «1» sul
     dialogo, conferma «Set model to Opus 5.5 for this session only» → uscita 0; mai Invio nel selettore
MW3  un dialogo che supervisor non conosce dopo «s» (qui uno inventato: «uso extra» per il contesto da 1M): mai
     confermato; si chiude, uscita 4, e il messaggio dice che cosa c'era a schermo
MW4  una voce che non c'e': uscita 4, e il messaggio elenca le voci del selettore con i loro modelli
MW5  cm-session.js (lato win): l'errore dell'API nella trascrizione va in errors, non in texts; read e post dicono block
     {kind limit, …} finche' l'ultima voce e' l'errore, e niente block dopo un turno riuscito; un errore di login e'
     kind login
MW6  talk verso una sessione di win ferma sul limite: niente «consegnato», una riga sola con la frase concordata con la
     master («… è ferma sul limite di Fable fino al 19/11 alle 3: il messaggio è arrivato, la sessione non può
     rispondere; per farla lavorare cambia modello (supervisor model … "Opus 5.5")»), uscita 8; il testo dell'errore
     non esce come risposta; --no-wait lo dice subito; wait lo stesso, senza la vecchia risposta come nuova
MW7  il lettore del testo del limite (cm-relay-state.limit_info): modello e reset nelle forme viste («hit your Fable
     limit · resets Nov 19, 3am (Europe/Rome)», «reached your Fable limit. Run /usage-credits…», «resets 3:10pm»,
     «resets at 23:10»); «your account's rate limit» e «your limit» non sono un modello; il giorno a cavallo dell'anno
"""
import base64
import datetime as _dt
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
cfg = tmp / "config.json"
winroot = tmp / "win"
winroot.mkdir()
WIN = {"kind": "windows-native", "transport": {"ssh": "win"}, "confirmed_at": "2026-10-01T23:50:00+02:00", "roles": ["compute", "sessions"],
       "trust": {"level": "work", "accounts": ["personale"], "secrets": False, "clients": False}, "sessions": {"root": "W:/cm", "allow": ["*"]}}
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                           "default_account": "personale", "workspace": {"root": str(home / "ws")}, "hosts": {"win": WIN},
                           "tune": {"timeout_s": 2, "switch_timeout_s": 4, "file": str(tmp / "tuned.json")}}))
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home)})
FX = T.ROOT / "tests" / "fixtures" / "win-console"
SCR = {f.stem: f.read_text(encoding="utf-8") for f in FX.glob("*.txt")}

spec = importlib.util.spec_from_file_location("cm_tune_mw", T.SCRIPTS / "cm-tune.py")
TU = importlib.util.module_from_spec(spec)
spec.loader.exec_module(TU)
TU.GLYPHS = ("❯", ">")          # come bind() per una sessione di un altro host
TU.remember = lambda *a, **k: None   # l'annotazione del cambio (tune.file) non e' di questo test
OPUS = next(m for m in TU.models() if m["id"] == "claude-opus-5-5[1m]")

# MW1
rows = TU.picker_rows(SCR["2-picker"])
T.check("MW1 the real win picker (2.1.325): rows by family with the model of their description; «√» stays out of the label",
        rows == [(1, "Default (recommended)", False, "Use the default model (currently Opus 5.5) ·"), (2, "Opus", False, "Opus 5.5"),
                 (3, "Fable", True, "Fable 5.2"), (4, "Sonnet", False, "Sonnet 5.5")], str(rows))
T.check("MW1 Opus 5.5 is the row «2. Opus» (its description names it); «Default» is not, though its text names Opus 5.5 too",
        [n for n, label, _, model in rows if TU.row_is(OPUS, label, model)] == [2], str(rows))

OTHER = "\n".join(SCR["4-dialog"].splitlines()[:-6] + [
    "   Use Opus 5.5 with 1M context?",
    "   This uses extra usage. Your Fable limit resets Nov 19.",
    "   > 1. Yes, use extra usage",
    "     2. No, go back"]) + "\n"   # un dialogo inventato: la forma di quelli veri, un contenuto che supervisor non conosce
KEPT = SCR["5-confirmed"].replace("Set model to Opus 5.5 for this session only", "Kept model as Fable 5.2").replace("Opus 5.5 · API", "Fable 5.2 · API")


class WinConsole:
    """La console di una sessione di win come la vede cm-console.ps1, rimessa dagli schermi veri della prova del 16/11:
    i tasti muovono lo stato (prompt, selettore col cursore, dialogo, conferma), lo schermo e' quello di quello stato."""

    def __init__(self, other=False):
        self.state, self.cur, self.typed, self.other = "prompt", 3, "", other
        self.keys, self.enter_in_picker, self.confirmed_other = [], False, False

    def picker(self):
        out = []
        for line in SCR["2-picker"].splitlines():
            m = re.match(r"^   [> ↓] (\d)\. ", line)
            if m:
                n = int(m.group(1))
                line = "   " + (">" if n == self.cur else "↓" if n == 4 else " ") + line[4:]
            out.append(line)
        return "\n".join(out) + "\n"

    def screen(self):
        return {"prompt": SCR["1-prompt"], "picker": self.picker(), "dialog": SCR["4-dialog"], "other": OTHER, "confirmed": SCR["5-confirmed"],
                "kept": KEPT}[self.state]

    def press(self, k):
        if k.startswith("wait:"):
            return
        if self.state in ("prompt", "kept", "confirmed"):
            if k.startswith("text:"):
                self.typed += k[5:]
            elif k == "Enter" and self.typed == "/model":
                self.state, self.typed, self.cur = "picker", "", 3
        elif self.state == "picker":
            if k == "Up":
                self.cur = max(1, self.cur - 1)
            elif k == "Down":
                self.cur = min(4, self.cur + 1)
            elif k == "text:s" and self.cur == 2:
                self.state = "other" if self.other else "dialog"
            elif k == "Enter":
                self.enter_in_picker = True   # salverebbe il predefinito vero: non deve succedere mai
            elif k == "Esc":
                self.state = "kept"
        elif self.state == "dialog":
            if k == "text:1":
                self.state = "confirmed"
            elif k in ("text:2", "Esc"):
                self.state = "picker"
        elif self.state == "other":
            if k in ("text:1", "Enter"):
                self.confirmed_other = True
            elif k in ("text:2", "Esc"):
                self.state = "picker"

    def session_console(self, name, keys=None, timeout=90):
        for k in keys or []:
            self.keys.append(k)
            self.press(k)
        return {"pid": 4242, "screen": self.screen()}


def switch(console, wanted):
    import contextlib
    import io as _io
    TU.REMOTE["win-prova"] = (console, "prova")
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = TU._set_model("win-prova", wanted)
    return rc, buf.getvalue()


# MW2
c2 = WinConsole()
rc2, out2 = switch(c2, "claude-opus-5-5[1m]")
T.check("MW2 supervisor model on a win session: on «Opus», «s», «1» on «Switch model?», the confirmation → exit 0, «Opus 5.5»",
        rc2 == 0 and c2.state == "confirmed" and "Opus 5.5" in out2 and "text:s" in c2.keys and "text:1" in c2.keys, f"{rc2} {out2!r} {c2.keys}")
T.check("MW2 never Enter in the picker (it would save the real default): the only Enter is the one that opens /model",
        not c2.enter_in_picker and c2.keys.count("Enter") == 1, str(c2.keys))

# MW3
c3 = WinConsole(other=True)
rc3, out3 = switch(c3, "claude-opus-5-5[1m]")
T.check("MW3 a dialog supervisor does not know after «s» (here a made-up «extra usage» one): never confirmed, closed, exit 4",
        rc3 == 4 and not c3.confirmed_other and c3.state == "kept" and "conferma non arrivata" in out3, f"{rc3} {out3!r} {c3.keys}")
T.check("MW3 the message says what was on screen before closing: the dialog's title and options",
        "a schermo, prima di chiudere" in out3 and "Use Opus 5.5 with 1M context?" in out3 and "extra usage" in out3, out3)

# MW4
TU.T.setdefault("models", [])
TU.T["models"] = TU.T["models"] + [{"id": "claude-mythos-9", "label": "Mythos 9", "pick": ["Mythos 9"]}]
c4 = WinConsole()
rc4, out4 = switch(c4, "claude-mythos-9")
T.check("MW4 a model with no row: exit 4 after a whole turn of the list, never «s», and the message lists the rows with their models",
        rc4 == 4 and "text:s" not in c4.keys and "Mythos 9 non c'è nel selettore" in out4 and "2. Opus (Opus 5.5)" in out4 and "3. Fable (Fable 5.2)" in out4,
        f"{rc4} {out4!r}")

# MW5: cm-session.js lato win (node, con una home finta)
nhome = tmp / "nhome"
SID, CWD = "sid-limite", "D:\\cm-sessions\\personali\\app"
sd = nhome / ".claude" / "sessions"
sd.mkdir(parents=True)
sock = str(tmp / "pipe.sock")
got = []
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(sock)
srv.listen(4)


def serve():
    while True:
        try:
            c, _ = srv.accept()
        except OSError:
            return
        data = b""
        while True:
            b = c.recv(65536)
            if not b:
                break
            data += b
        got.append(data.decode())
        c.close()


threading.Thread(target=serve, daemon=True).start()
(sd / f"{os.getpid()}.json").write_text(json.dumps({"name": "app", "pid": os.getpid(), "sessionId": SID, "cwd": CWD, "status": "idle",
                                                    "startedAt": 1, "messagingSocketPath": sock}))
(sd / f"{os.getpid()}.abc.key").write_text(json.dumps({"peerToken": "tok"}))
pdir = nhome / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", CWD)
pdir.mkdir(parents=True)
tr = pdir / f"{SID}.jsonl"
LIMIT = "You've hit your Fable limit · resets Nov 19, 3am (Europe/Rome). /model to switch models."


def entry(kind, text, **kw):
    if kind == "user":
        return json.dumps({"type": "user", "message": {"role": "user", "content": text}, **kw})
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, **kw})


tr.write_text("\n".join([entry("user", "ciao"), entry("assistant", "Ciao, al lavoro."), entry("user", "continua"),
                         entry("assistant", LIMIT, isApiErrorMessage=True, error="rate_limit", timestamp="2026-11-16T16:44:08.000Z"),
                         json.dumps({"type": "system", "subtype": "turn_duration"})]) + "\n")
JS = T.PLUGIN / "remote" / "cm-session.js"
nenv = dict(os.environ, HOME=str(nhome), USERPROFILE=str(nhome))


def node(*args):
    r = subprocess.run(["node", str(JS), *args], capture_output=True, text=True, env=nenv, timeout=30)
    return json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"err": r.stderr}


rd = node("read", "app", "0")
T.check("MW5 cm-session.js read: the API error is in errors (kind limit, rate_limit), not in texts; block says the session is stopped on it",
        rd.get("texts") == ["Ciao, al lavoro."] and [(x["kind"], x["error"]) for x in rd.get("errors") or []] == [("limit", "rate_limit")]
        and (rd.get("block") or {}).get("kind") == "limit" and LIMIT in (rd.get("block") or {}).get("text", ""), json.dumps(rd)[:600])
pt = node("post", "app", base64.b64encode("Dalla master: riprova".encode()).decode(), "master", "uds:")
T.check("MW5 cm-session.js post: delivered (the pipe got it) and block {kind limit} in its answer",
        pt.get("ok") is True and (pt.get("block") or {}).get("kind") == "limit" and T.wait_until(lambda: any("riprova" in g for g in got), timeout=5),
        json.dumps(pt)[:400] + str(got)[:200])
with open(tr, "a") as f:
    f.write(entry("user", "riprova") + "\n" + entry("assistant", "Ripartita su Opus.") + "\n")
rd2 = node("read", "app", "0")
T.check("MW5 after a turn that works, no block (the error stays in errors, history)", rd2.get("block") is None and rd2.get("texts")[-1] == "Ripartita su Opus.", json.dumps(rd2)[:400])
with open(tr, "a") as f:
    f.write(entry("assistant", "Please run /login · API Error: 401 {\"type\":\"error\",\"error\":{\"type\":\"authentication_error\"}}",
                  isApiErrorMessage=True, error="authentication_failed") + "\n")
rd3 = node("read", "app", "0")
T.check("MW5 an authentication error is kind login", (rd3.get("block") or {}).get("kind") == "login", json.dumps(rd3.get("block")))
srv.close()

# MW6: talk e wait verso una sessione di win ferma sul limite (l'adattatore finto, come in rsession-verify)
(winroot / "registry.json").write_text(json.dumps([{"name": "win-alfa", "status": "idle"}, {"name": "win-beta", "status": "idle"},
                                                     {"name": "win-gamma", "status": "idle"}]))
tenv = dict(os.environ, CM_RSESSION_ADAPTER=str(T.ROOT / "tests" / "lib" / "fake-rsession-adapter.py"), FAKE_WIN_ROOT=str(winroot),
            FAKE_WIN_LIMIT="win-alfa", FAKE_WIN_STALE_BLOCK="win-gamma", CM_PROC_SCAN_PIDS="")
TALK = [sys.executable, str(T.SCRIPTS / "cm-talk.py")]
AGREED = ("win:win-alfa è ferma sul limite di Fable fino al 19/11 alle 3: il messaggio è arrivato, la sessione non può rispondere; "
          'per farla lavorare cambia modello (supervisor model win:win-alfa "Opus 5.5").')
(winroot / "transcript-win-alfa.jsonl").write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "VECCHIA RISPOSTA di prima del limite"}]}}) + "\n")
r6 =subprocess.run(TALK + ["talk", "win:win-alfa", "Dalla master: a che punto sei?", "--wait", "30"], capture_output=True, text=True, env=tenv, timeout=90)
T.check("MW6 talk to a win session stopped on the limit: no «consegnato», ONE line with the phrase agreed with the master, exit 8",
        r6.returncode == 8 and "consegnato" not in r6.stderr and r6.stderr.count(AGREED) == 1 and r6.stderr.count("è ferma") == 1,
        f"rc={r6.returncode} out={r6.stdout!r} err={r6.stderr!r}")
T.check("MW6 the error text does not come out as the session's answer (stdout)", "You've hit" not in r6.stdout, r6.stdout)
r6b = subprocess.run(TALK + ["talk", "win:win-alfa", "secondo", "--no-wait"], capture_output=True, text=True, env=tenv, timeout=60)
r6c = subprocess.run(TALK + ["talk", "win:win-beta", "a te", "--wait", "30"], capture_output=True, text=True, env=tenv, timeout=90)
r6d = subprocess.run(TALK + ["wait", "win:win-alfa", "--timeout", "20"], capture_output=True, text=True, env=tenv, timeout=60)
T.check("MW6 --no-wait says it at once (exit 8); a session that works answers as before («consegnato», the reply, exit 0); wait says it too (exit 8)",
        r6b.returncode == 8 and AGREED in r6b.stderr and r6c.returncode == 0 and "consegnato" in r6c.stderr and "RISPOSTA: a te" in r6c.stdout
        and r6d.returncode == 8 and AGREED in r6d.stderr,
        f"{r6b.returncode} {r6b.stderr!r} | {r6c.returncode} {r6c.stderr!r} {r6c.stdout!r} | {r6d.returncode} {r6d.stderr!r}")
r6e = subprocess.run(TALK + ["talk", "win:win-gamma", "ci sei?", "--wait", "30"], capture_output=True, text=True, env=tenv, timeout=90)
T.check("MW6 stopped when the message left (its last entry was the error) but it answers: the reply, «no longer stopped», exit 0",
        r6e.returncode == 0 and "RISPOSTA: ci sei?" in r6e.stdout and "non è più ferma" in r6e.stderr, f"{r6e.returncode} {r6e.stdout!r} {r6e.stderr!r}")
T.check("MW6 wait on a stopped session does not print the last real answer (from before the limit) as if it were new",
        "VECCHIA RISPOSTA" not in r6d.stdout and "VECCHIA RISPOSTA" not in r6.stdout, repr(r6d.stdout))

# MW7: il lettore del testo del limite
RS = importlib.util.module_from_spec(importlib.util.spec_from_file_location("cm_relay_state_mw", T.SCRIPTS / "cm-relay-state.py"))
RS.__spec__.loader.exec_module(RS)
from zoneinfo import ZoneInfo  # noqa: E402
ROME = ZoneInfo("Europe/Rome")
NOW = _dt.datetime(2026, 11, 16, 18, 0, tzinfo=ROME).timestamp()
i1 = RS.limit_info(LIMIT, NOW)
T.check("MW7 «You've hit your Fable limit · resets Nov 19, 3am (Europe/Rome)»: Fable, 19/11 at 3:00 in Rome",
        (i1["model"], i1["day"], i1["month"], i1["hour"], i1["minute"], i1["tz"]) == ("Fable", 19, 11, 3, 0, "Europe/Rome")
        and i1["at"] == int(_dt.datetime(2026, 11, 19, 3, 0, tzinfo=ROME).timestamp()), str(i1))
i2 = RS.limit_info("You've reached your Fable limit. Run /usage-credits to continue or switch models with /model.", NOW)
i3 = RS.limit_info("You've hit your limit · resets 3:10pm (Europe/Rome)", NOW)
i4 = RS.limit_info("API Error: 429 would exceed your account's rate limit. resets at 23:10", NOW)
T.check("MW7 «reached your Fable limit» without a time: Fable, no reset; «your limit · resets 3:10pm»: no model, 15:10 tomorrow (today's is past)",
        (i2["model"], i2["at"]) == ("Fable", None) and i3["model"] is None and (i3["hour"], i3["minute"], i3["day"]) == (15, 10, None)
        and i3["at"] == int(_dt.datetime(2026, 11, 17, 15, 10, tzinfo=ROME).timestamp()), f"{i2} {i3}")
T.check("MW7 «your account's rate limit … resets at 23:10»: no model, 23:10 today (local time: the text names no zone)",
        i4["model"] is None and (i4["hour"], i4["minute"]) == (23, 10) and i4["at"] > NOW and i4["tz"] is None, str(i4))
i5 = RS.limit_info("You've hit your Opus 5.5 limit · resets Jan 2, 9am (Europe/Rome)", _dt.datetime(2026, 12, 30, 12, 0, tzinfo=ROME).timestamp())
T.check("MW7 «Opus 5.5 limit · resets Jan 2, 9am» read on 30/12: Opus 5.5, 2 January of the next year",
        i5["model"] == "Opus 5.5" and i5["at"] == int(_dt.datetime(2027, 1, 2, 9, 0, tzinfo=ROME).timestamp()), str(i5))

T.rm(str(tmp))
T.finish()
