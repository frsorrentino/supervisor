#!/usr/bin/env python3
"""Un blocco che chiede la persona (il limite d'uso, la quota, il login) apre da solo una voce, che resta finche' il
blocco c'e' (16/11/2026, supervisor-blocco-permanente-16-11).

Il 16/11 win:supervisor-app e' rimasta ferma sul limite di Fable dalle 17:44 alle 19:07 e l'app non l'ha detto: su win
gli hook di supervisor non arrivano al relay, e lo stato dell'host non diceva niente della trascrizione. Sulle sessioni
locali lo StopFailure apriva il quota_limit, ma il relay riconosceva solo «reached your Fable limit»: col testo della
2.1.323 («You've hit your Fable limit · resets Nov 19, 3am (Europe/Rome)») il limite di Fable diventava la finestra
delle 5 ore, senza il tasto «Opus 5.5»; e la voce si chiudeva al primo prompt, anche se quel prompt riprendeva il
limite. Concordato con la master: limite e quota → il quota_limit coi tasti, login → un needs-you; il titolo dice in
parole semplici chi e' ferma, perche' e fino a quando; la voce sta nei due posti dell'analisi di supervisor-app (la
sessione e Dalla master), cioe' fra le attese.

BP1  cm-relay-state.api_error: la voce vera del 16/11 (isApiErrorMessage, error rate_limit, apiError
     usage_limit_reached, quotaLimits.resetsAt) e' kind limit col reset; il login e' login; un 529 e un 429 di
     frequenza sono error; il «reached your Fable limit» <synthetic> delle versioni vecchie e' limit; il resto None
BP2  cm-core.api_state: l'ultima voce assistant della trascrizione (non di un sottoagente): l'errore → block; un turno
     riuscito dopo → block None e last_at; per una sessione di win i campi della riga (lo stato dell'host), None se
     l'aiutante di win e' vecchio
BP3  quota_limit di una sessione locale col testo nuovo: finestra model, «Opus 5.5», il reset dal giorno e dal fuso del
     testo, titolo «alfa è ferma: limite di Fable fino al G/M alle 3»; la voce porta strade, modello e reset
BP4  quota_limit di una sessione di win: ref e sessione «win:win-alfa», niente «altro account» (il passaggio partirebbe
     da qui), il reset di quotaLimits quando il testo non lo dice
BP5  la voce resta finche' il blocco c'e': un prompt che riprende il limite non la chiude, un turno riuscito dopo si';
     per win lo dice lo stato dell'host (con l'aiutante vecchio resta); una sessione sparita la chiude
BP6  i due posti: nello stato la voce ha il suo wid e closes «check»; fra le attese e' «limite» col titolo chiaro e i
     tasti (continua con Opus 5.5, riprendi al reset, fatto)
BP7  hosts poll: un limite nuovo di una sessione di win apre il quota_limit, una volta per blocco; un login apre un
     needs-you «win:win-beta è ferma: serve un login» che resta e va via da solo dopo un turno riuscito; le righe di win
     portano block e api_last_at
BP8  login di una sessione locale: lo StopFailure apre il needs-you (non il turno fallito), il prompt dopo non lo
     toglie, la fine di un turno riuscito si'; un needs-you qualunque va ancora via al prompt; il relay toglie quello
     di un blocco quando la trascrizione dice che la sessione e' ripartita, senza svegliarla
"""
import datetime as _dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude", ".claude-lavoro"):
    (home / d).mkdir(parents=True)
state, rdir, ws, winroot = tmp / "state", tmp / "relay", tmp / "ws", tmp / "win"
for d in (state, rdir, winroot):
    d.mkdir()
proj = ws / "personale" / "alfa"
proj.mkdir(parents=True)
cfg = tmp / "config.json"
WIN = {"kind": "windows-native", "transport": {"ssh": "win"}, "confirmed_at": "2026-10-01T23:50:00+02:00", "roles": ["compute", "sessions"],
       "trust": {"level": "work", "accounts": ["personale"], "secrets": False, "clients": False}, "sessions": {"root": "W:/cm", "allow": ["*"]}}
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "default_account": "personale", "workspace": {"root": str(ws)},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}, "lavoro": {"config_dir": str(home / ".claude-lavoro"), "tmux_prefix": "lav-"}},
    "hosts": {"win": WIN},
    "relay": {"enabled": True, "dir": str(rdir), "firebase_url": "http://127.0.0.1:9", "host": "prova", "web_app_url": "https://app.example"},
    "tune": {"models": [{"id": "claude-opus-5-5[1m]", "label": "Opus 5.5", "pick": ["Opus 5.5", "Opus"]},
                        {"id": "claude-fable-5-2", "label": "Fable 5.2", "pick": ["Fable 5.2", "Fable"]}]}}))
ENV = dict(os.environ, CC_SUPERVISOR_CONFIG=str(cfg), HOME=str(home), CM_HOME=str(home), FAKE_WIN_ROOT=str(winroot), CM_PROC_SCAN_PIDS="",
           CM_RSESSION_ADAPTER=str(T.ROOT / "tests" / "lib" / "fake-rsession-adapter.py"))
os.environ.update(ENV)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_bp", T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def local_iso(t):   # come le righe del ledger
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


NOW = time.time()
ROME = ZoneInfo("Europe/Rome")
R3 = (_dt.datetime.fromtimestamp(NOW, ROME) + _dt.timedelta(days=3)).replace(hour=3, minute=0, second=0, microsecond=0)
RESET = int(R3.timestamp())
MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[R3.month - 1]
LIMIT = f"You've hit your Fable limit · resets {MON} {R3.day}, 3am (Europe/Rome). /model to switch models."
UNTIL = f"fino al {R3.day}/{R3.month} alle 3"
SID = "aaaa1111-0000-4000-8000-0000000000a1"


def limit_entry(t, text=LIMIT, resets=RESET, sid=SID):
    """La voce che Claude Code 2.1.323 ha scritto il 16/11 alle 17:44 (la forma vera, letta in sola lettura)."""
    return {"parentUuid": "p", "isSidechain": False, "type": "assistant", "timestamp": iso(t), "sessionId": sid,
            "message": {"id": "m", "model": "<synthetic>", "role": "assistant", "type": "message", "stop_reason": "stop_sequence",
                        "content": [{"type": "text", "text": text}]},
            "error": "rate_limit", "isApiErrorMessage": True, "apiError": "usage_limit_reached", "apiErrorStatus": 429,
            "apiErrorParams": {"rate_limit_info": {"status": "rejected", "resetsAt": resets, "rateLimitType": "seven_day_overage_included"}},
            "quotaLimits": {"status": "rejected", "resetsAt": resets, "rateLimitType": "seven_day_overage_included", "upgradePaths": ["upgrade_plan", "overage"]}}


LOGIN_TEXT = 'Please run /login · API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"OAuth token has expired."}}'


def login_entry(t, sid=SID):
    return {"type": "assistant", "timestamp": iso(t), "sessionId": sid, "isApiErrorMessage": True, "error": "authentication_failed",
            "message": {"model": "<synthetic>", "role": "assistant", "content": [{"type": "text", "text": LOGIN_TEXT}]}}


def ok_entry(t, text="Fatto.", side=False, sid=SID):
    return {"type": "assistant", "timestamp": iso(t), "isSidechain": side, "sessionId": sid,
            "message": {"model": "claude-fable-5-2", "role": "assistant", "content": [{"type": "text", "text": text}]}}


def user_entry(t, text, sid=SID):
    return {"type": "user", "timestamp": iso(t), "sessionId": sid, "message": {"role": "user", "content": text}}


RS = load("cm-relay-state")
CORE = load("cm-core")

# BP1
def kind(d):
    e = RS.api_error(d)
    return e and e["kind"]


e1 = RS.api_error(limit_entry(NOW))
T.check("BP1 the real 2.1.323 entry of 16/11: kind limit, error rate_limit, the reset from quotaLimits, the limit type, the text",
        bool(e1) and e1["kind"] == "limit" and e1["error"] == "rate_limit" and e1["resets_at"] == RESET
        and e1["limit_type"] == "seven_day_overage_included" and e1["text"].startswith("You've hit your Fable limit"), str(e1))
O529 = {"type": "assistant", "isApiErrorMessage": True, "error": "server_error",
        "message": {"model": "<synthetic>", "content": [{"type": "text", "text": 'API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'}]}}
R429 = {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
        "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "API Error: 429 This request would exceed your organization's rate limit of 400,000 input tokens per minute."}]}}
OLD = {"type": "assistant", "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "You've reached your Fable limit. Run /usage-credits to continue or switch models with /model."}]}}
NORESP = {"type": "assistant", "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "No response requested."}]}}
T.check("BP1 login → login; a 529 and a per-minute 429 → error (not a block for the person); the old «reached your Fable limit» → limit; an answer and «No response requested.» → None",
        [kind(login_entry(NOW)), kind(O529), kind(R429), kind(OLD), kind(ok_entry(NOW)), kind(NORESP), kind(user_entry(NOW, "x"))]
        == ["login", "error", "error", "limit", None, None, None], str([kind(login_entry(NOW)), kind(O529), kind(R429), kind(OLD), kind(ok_entry(NOW)), kind(NORESP)]))

# BP2
pdir = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(proj))
pdir.mkdir(parents=True)
TR = pdir / f"{SID}.jsonl"


def write(*entries):
    TR.write_text("".join(json.dumps(e) + "\n" for e in entries))


def add(*entries):
    with open(TR, "a") as f:
        f.write("".join(json.dumps(e) + "\n" for e in entries))


ROW = {"name": "alfa", "tmux": "alfa", "account": "personale", "cwd": str(proj), "session_id": SID, "status": "idle"}
T0 = NOW - 900
write(user_entry(T0, "ciao"), ok_entry(T0 + 5), user_entry(T0 + 60, "continua"), limit_entry(T0 + 62), ok_entry(T0 + 70, "di un sottoagente", side=True))
s2 = CORE.api_state(ROW) or {}
T.check("BP2 the last assistant entry (a subagent's does not count) is the limit → block limit, last_at its time",
        (s2.get("block") or {}).get("kind") == "limit" and abs((s2.get("last_at") or 0) - (T0 + 62)) < 1.5, str(s2))
add(user_entry(T0 + 120, "riprova"), ok_entry(T0 + 125, "Ripartita su Opus."))
s2b = CORE.api_state(ROW) or {}
WBLK = {"kind": "limit", "error": "rate_limit", "text": LIMIT, "resets_at": RESET}
T.check("BP2 a turn that works after it → no block, last_at the new answer; a win row → its fields (the host's status); a win row without them (old helper) → None",
        s2b.get("block") is None and abs((s2b.get("last_at") or 0) - (T0 + 125)) < 1.5
        and CORE.api_state({"name": "win:x", "host": "win", "block": WBLK, "api_last_at": 123}) == {"block": WBLK, "last_at": 123}
        and CORE.api_state({"name": "win:y", "host": "win"}) is None, str(s2b))

# BP3: il relay, una sessione locale
RL = load("cm-relay")
got = []
QUOTA = {"personale": {"cinque_ore_pct": 10, "settimana_pct": 20, "reset_cinque_ore": int(NOW + 3600), "reset_settimanale": int(NOW + 5 * 86400)}}
WROW = {"name": "win:win-alfa", "tmux": "", "host": "win", "account": "personale", "cwd": "D:\\cm\\alfa", "session_id": "sid-alfa", "status": "idle"}
rows_now = [ROW]
RL._json_cmd = lambda *a, **kw: (list(rows_now) if a[0] == "sessions" else QUOTA if a[0] == "quota" else None)
RL.emit = lambda kind_, title, body, **kw: got.append((kind_, title, kw))
RL.rdir = lambda: rdir
write(user_entry(NOW - 30, "continua"), limit_entry(NOW - 5))
_429 = "rate_limit: 429 This request would exceed your account's rate limit. Please try again later."
RL.quota_limit("alfa", _429, str(TR))
RL.quota_limit("alfa", _429, str(TR))   # lo stesso blocco: nessun secondo evento
ev3 = [g for g in got if g[0] == "quota_limit"]
x3 = ev3[0][2].get("extra") if ev3 else {}
T3 = f"alfa è ferma: limite di Fable {UNTIL}"
T.check("BP3 a local session on «You've hit your Fable limit · resets <day>, 3am (Europe/Rome)»: window model, «Opus 5.5» first, reset at 3:00 in Rome, the plain title; once",
        len(ev3) == 1 and ev3[0][1] == T3 and x3.get("window") == "model" and x3.get("other_model") == "Opus 5.5" and x3.get("reset_at") == RESET
        and x3.get("actions") == ["other_model", "other_account", "at_reset"] and ev3[0][2].get("ref") == "alfa", str(got))
a3 = next((x for x in json.loads((rdir / "attention.json").read_text()) if x.get("kind") == "quota_limit" and x.get("ref") == "alfa"), {})
T.check("BP3 the item in «Serve te» says the same and carries the routes, the model and the reset (for the attesa)",
        a3.get("title") == T3 and a3.get("actions") == ["other_model", "other_account", "at_reset"] and a3.get("other_model") == "Opus 5.5" and a3.get("reset_at") == RESET, str(a3))

# BP4: il relay, una sessione di win
rows_now[:] = [ROW, dict(WROW, block=None)]
got.clear()
try:
    RL.quota_limit("win:win-alfa", "You've hit your Fable limit. /model to switch models.", "", RESET)   # il testo senza l'ora: quella di quotaLimits
    err4 = None
except TypeError as e:
    err4 = e
ev4 = [g for g in got if g[0] == "quota_limit"]
x4 = ev4[0][2].get("extra") if ev4 else {}
T.check("BP4 a win session: ref and session «win:win-alfa», «win:win-alfa è ferma: limite di Fable fino al …», the reset of quotaLimits, no «other account» (the handoff would start here)",
        err4 is None and len(ev4) == 1 and ev4[0][1] == f"win:win-alfa è ferma: limite di Fable {UNTIL}" and ev4[0][2].get("ref") == "win:win-alfa"
        and x4.get("reset_at") == RESET and x4.get("actions") == ["other_model", "at_reset"], f"{err4} {got}")

# BP5: la voce resta finche' il blocco c'e'
since5 = int(a3.get("since") or NOW)
add(user_entry(since5 + 2, "Dalla master: riprova"), limit_entry(since5 + 4))
LEDGER = [{"event": "prompt", "session_id": SID, "ts": local_iso(since5 + 2)}]
k5 = RL.attention_open([ROW, dict(WROW, block=WBLK, api_last_at=since5 + 4)], LEDGER)
T.check("BP5 a prompt that takes the limit again does not close the item (it closed at the first prompt)",
        any(x.get("ref") == "alfa" and x.get("kind") == "quota_limit" for x in k5), str(k5))
add(user_entry(since5 + 30, "riprova"), ok_entry(since5 + 35, "Ripartita."))
k5b = RL.attention_open([ROW, dict(WROW, block=WBLK, api_last_at=since5 + 4)], LEDGER)
k5c = RL.attention_open([ROW, dict(WROW)], LEDGER)   # l'aiutante di win vecchio: non si sa, la voce resta
T.check("BP5 a turn that works after it closes it; a win session still blocked (the host's status) keeps its item, and so does one whose state is unknown (old helper)",
        not any(x.get("ref") == "alfa" for x in k5b) and any(x.get("ref") == "win:win-alfa" for x in k5b) and any(x.get("ref") == "win:win-alfa" for x in k5c), f"{k5b} {k5c}")
k5d = RL.attention_open([ROW, dict(WROW, block=None, api_last_at=since5 + 60)], LEDGER)
RL.attention_mark("quota_limit", "win:win-alfa", "win:win-alfa", "x")
k5e = RL.attention_open([ROW], LEDGER)
T.check("BP5 win: the host says a turn worked → closed; a session that is gone → closed",
        not any(x.get("ref") == "win:win-alfa" for x in k5d) and not any(x.get("ref") == "win:win-alfa" for x in k5e), f"{k5d} {k5e}")

# BP6: i due posti
ITEM = {"kind": "quota_limit", "session": "win:win-alfa", "ref": "win:win-alfa", "title": f"win:win-alfa è ferma: limite di Fable {UNTIL}", "since": int(NOW),
        "actions": ["other_model", "at_reset"], "other_model": "Opus 5.5", "reset_at": RESET}
w6 = RS.wid_attention(ITEM)
try:
    st6 = RS.build_state({"host": "prova", "rows": [], "ledger": [], "attention": [ITEM]}, int(NOW), fit=False)
except Exception as e:   # noqa: BLE001
    st6 = {"error": repr(e)}
s6 = next((x for x in st6.get("attention") or [] if x.get("kind") == "quota_limit"), {})
T.check("BP6 in the state the item has its wid (it is an attesa) and closes «check» (the relay closes it when the block is gone)",
        bool(w6) and w6 == RS.wid_attention(dict(ITEM)) and s6.get("wid") == w6 and s6.get("closes") == "check" and s6.get("reset_at") == RESET, f"{w6} {s6} {st6.get('error')}")
AT = load("cm-attese")
lim = [a for a in AT.collect(int(NOW), state={"sessions": [{"name": "win:win-alfa"}], "attention": [ITEM]}) if a.get("kind") == "limite"]
b6 = [(b["cmd"].get("op"), b["cmd"].get("session"), b["cmd"].get("arg"), b["cmd"].get("kind")) for b in (lim[0]["actions"] if lim else [])]
T.check("BP6 among the attese: kind «limite», session win:win-alfa, the plain title, «Decidi»; buttons «Continua con Opus 5.5», «Riprendi al reset», «Fatto»",
        len(lim) == 1 and lim[0]["session"] == "win:win-alfa" and lim[0]["title"] == ITEM["title"] and lim[0]["label"] == "decide" and lim[0]["id"] == w6
        and b6 == [("quota_route", "win:win-alfa", "other_model", None), ("quota_route", "win:win-alfa", "at_reset", None), ("attention_done", None, "win:win-alfa", "quota_limit")]
        and "Opus 5.5" in lim[0]["actions"][0]["label"] and lim[0]["actions"][0].get("style") == "primary", json.dumps(lim, ensure_ascii=False)[:900])

# la fixture per l'app (contratto 1.150): la voce di /state.attention e la sua attesa, come le scrive il relay oggi
FIX = T.ROOT / "tests" / "fixtures" / "relay" / "limite-sample.json"
FITEM = {"kind": "quota_limit", "session": "win:supervisor-app", "ref": "win:supervisor-app", "title": "win:supervisor-app è ferma: limite di Fable fino al 19/11 alle 3",
         "since": 1794847448, "actions": ["other_model", "at_reset"], "other_model": "Opus 5.5", "reset_at": 1795053600, "window": "model"}
try:
    fst = RS.build_state({"host": "prova", "rows": [], "ledger": [], "attention": [FITEM]}, 1794847500, fit=False)
except Exception as e:   # noqa: BLE001
    fst = {"error": repr(e)}
sample = {"state_attention": next((x for x in fst.get("attention") or [] if x.get("kind") == "quota_limit"), None),
          "attese": [a for a in AT.collect(1794847500, state={"sessions": [{"name": "win:supervisor-app"}], "attention": [FITEM]}) if a.get("kind") == "limite"]}
if os.environ.get("CM_WRITE_FIXTURE") == "limite-sample":   # per rigenerarla a mano, dopo un cambio voluto
    FIX.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
T.check("BP6 the fixture limite-sample.json (sent to the app) is what the relay writes today",
        FIX.is_file() and json.loads(FIX.read_text(encoding="utf-8")) == json.loads(json.dumps(sample)), json.dumps(sample, ensure_ascii=False)[:600])

# BP7: hosts poll
HM = load("cm-hosts")
calls = []
HM.relay_limit = lambda row, blk: calls.append((row.get("name"), blk.get("kind"), blk.get("resets_at")))
BLK_L = {"kind": "limit", "error": "rate_limit", "text": LIMIT, "at": iso(NOW - 50), "resets_at": RESET, "limit_type": "seven_day_overage_included"}
BLK_G = {"kind": "login", "error": "authentication_failed", "text": LOGIN_TEXT, "at": iso(NOW - 40)}


def winreg(alfa, beta):
    (winroot / "registry.json").write_text(json.dumps([
        {"name": "win-alfa", "sessionId": "sid-alfa", "status": "idle", "cwd": "D:\\cm\\alfa", "pid": 11, "_api": alfa},
        {"name": "win-beta", "sessionId": "sid-beta", "status": "idle", "cwd": "D:\\cm\\beta", "pid": 12, "_api": beta},
        {"name": "win-gamma", "sessionId": "sid-gamma", "status": "idle", "cwd": "D:\\cm\\gamma", "pid": 13, "_api": {"block": None, "last_at": iso(NOW - 100)}}]))


NY = state / "needs-you"
winreg({"block": BLK_L, "last_at": iso(NOW - 50)}, {"block": BLK_G, "last_at": iso(NOW - 40)})
HM.poll_one("win", force=True)
ny7 = json.loads((NY / "sid-beta.json").read_text()) if (NY / "sid-beta.json").exists() else {}
T.check("BP7 a new limit of a win session → the relay's quota_limit for win:win-alfa; a login → a needs-you «win:win-beta è ferma: serve un login», source block; nothing for win-gamma",
        calls == [("win:win-alfa", "limit", RESET)] and ny7.get("source") == "block" and str(ny7.get("reason") or "").startswith("win:win-beta è ferma: serve un login")
        and ny7.get("name") == "win:win-beta" and not (NY / "sid-gamma.json").exists() and not (NY / "sid-alfa.json").exists(), f"{calls} {ny7}")
time.sleep(1.2)
HM.poll_one("win", force=True)
ny7b = json.loads((NY / "sid-beta.json").read_text()) if (NY / "sid-beta.json").exists() else {}
rows7 = {r["name"]: r for r in HM.remote_session_rows()}
T.check("BP7 the same blocks at the next read: no second call, the needs-you untouched (no new notice); the win rows carry block and api_last_at",
        len(calls) == 1 and ny7b.get("at") == ny7.get("at") and (rows7.get("win:win-alfa") or {}).get("block", {}).get("kind") == "limit"
        and (rows7.get("win:win-gamma") or {}).get("block", "x") is None and abs(((rows7.get("win:win-gamma") or {}).get("api_last_at") or 0) - (NOW - 100)) < 1.5,
        f"{calls} {ny7b} {json.dumps(rows7, default=str)[:600]}")
winreg({"block": BLK_L, "last_at": iso(NOW - 50)}, {"block": None, "last_at": iso(NOW + 5)})
HM.poll_one("win", force=True)
BLK_L2 = dict(BLK_L, resets_at=RESET + 7 * 86400, at=iso(NOW + 10))
winreg({"block": BLK_L2, "last_at": iso(NOW + 10)}, {"block": None, "last_at": iso(NOW + 5)})
HM.poll_one("win", force=True)
T.check("BP7 a turn that works after the login: the needs-you goes by itself; another limit (another reset) → the relay again",
        not (NY / "sid-beta.json").exists() and calls == [("win:win-alfa", "limit", RESET), ("win:win-alfa", "limit", RESET + 7 * 86400)], str(calls))

# BP8: il login di una sessione locale, dagli hook
SIDL = "bbbb2222-0000-4000-8000-0000000000b2"


def hook(ev, payload, tm="beta"):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), ev], input=json.dumps(payload), capture_output=True, text=True,
                          env=dict(ENV, CM_HOOK_TMUX_NAME=tm), cwd=str(proj), timeout=60)


r8 = hook("StopFailure", {"session_id": SIDL, "cwd": str(proj), "transcript_path": "", "error": "authentication_failed", "error_details": "",
                          "last_assistant_message": LOGIN_TEXT})
ny8 = json.loads((NY / f"{SIDL}.json").read_text()) if (NY / f"{SIDL}.json").exists() else {}
T.check("BP8 a local session's login failure → a needs-you «beta è ferma: serve un login», source block, not a failed turn",
        r8.returncode == 0 and ny8.get("source") == "block" and str(ny8.get("reason") or "").startswith("beta è ferma: serve un login"), f"{r8.returncode} {r8.stderr[-300:]} {ny8}")
hook("UserPromptSubmit", {"session_id": SIDL, "cwd": str(proj), "prompt": "riprova"})
kept8 = (NY / f"{SIDL}.json").exists()
hook("Stop", {"session_id": SIDL, "cwd": str(proj), "last_assistant_message": "Fatto."})
T.check("BP8 the next prompt does not remove it (the block is still there until a turn works); the end of a turn that works does",
        kept8 and not (NY / f"{SIDL}.json").exists(), str(kept8))
(NY / f"{SIDL}.json").write_text(json.dumps({"reason": "un codice", "at": int(NOW), "name": "beta", "expires_at": int(NOW) + 86400}))
hook("UserPromptSubmit", {"session_id": SIDL, "cwd": str(proj), "prompt": "eccolo"})
T.check("BP8 an ordinary needs-you still goes at the next prompt (contract 1.68)", not (NY / f"{SIDL}.json").exists(), "")
woke = []
RL._wake = lambda tm, text: woke.append(tm)
(NY / f"{SID}.json").write_text(json.dumps({"reason": "alfa è ferma: serve un login", "at": int(since5 + 40), "name": "alfa", "source": "block", "expires_at": int(NOW) + 86400}))
add(login_entry(since5 + 45))
o8 = RL.needs_you_of([ROW])
still8 = (NY / f"{SID}.json").exists()
add(user_entry(since5 + 90, "riprova"), ok_entry(since5 + 95, "Rientrata."))
o8b = RL.needs_you_of([ROW])
T.check("BP8 the relay keeps a block's needs-you while the transcript says blocked, and removes it once a turn works, without waking the session",
        "alfa" in o8 and still8 and "alfa" not in o8b and not (NY / f"{SID}.json").exists() and not woke, f"{list(o8)} {still8} {list(o8b)} {woke}")

T.rm(str(tmp))
T.finish()
