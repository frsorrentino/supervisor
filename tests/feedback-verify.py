#!/usr/bin/env python3
"""Contratto 1.105 (02/11/2026, compito supervisor-feedback-28-10): le note di claude-observe di ogni plugin installato
arrivano al telefono, e da li' si accende il consenso e si manda. FS feedback_state (/state.feedback: note nuove, i tre
errori piu' frequenti gia' ripuliti, consenso, ultimo invio), AC always_anonymous_consent (op feedback_consent: il tocco
vale come il si' dell'utente, salvato dove lo legge observe), SE feedback_send (anteprima, poi l'invio solo con l'hash
dell'anteprima), TX feedback_text (un suggerimento scritto, ripulito, con l'avviso che diventa una issue pubblica).
Mai un invio senza un tocco: ogni op senza `device` e' rifiutata e niente parte."""
import http.server
import importlib.util
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
conf = home / ".claude"
(conf / "plugins").mkdir(parents=True)
store = tmp / "observe-state"
store.mkdir()


# il servizio anonimo finto: registra ogni POST e risponde con l'URL della issue
POSTS = []


class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        POSTS.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()))
        body = json.dumps({"url": f"https://github.com/acme/tool/issues/{len(POSTS)}"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
ENDPOINT = f"http://127.0.0.1:{srv.server_address[1]}/report.php"
ocfg = tmp / "observe.json"
ocfg.write_text(json.dumps({"dir": str(store), "endpoint": ENDPOINT, "anonymizer": str(tmp / "none.py")}))

# due plugin installati: uno con la sua copia di observe, uno senza; un terzo con observe ma spento dall'utente
plug = tmp / "cache" / "supervisor" / "0.9.0"
shutil.copytree(T.PLUGIN / "observe", plug / "observe", ignore=shutil.ignore_patterns("__pycache__"))
(plug / ".claude-plugin").mkdir()
(plug / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "supervisor", "version": "0.9.0"}))
plain = tmp / "cache" / "plain" / "1.0.0"
plain.mkdir(parents=True)
(conf / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
    "supervisor@dev": [{"scope": "user", "installPath": str(plug), "version": "0.9.0"}],
    "plain@dev": [{"scope": "user", "installPath": str(plain), "version": "1.0.0"}]}}))

bin_ = tmp / "bin"
bin_.mkdir()
(bin_ / "gh").write_text("#!/bin/sh\nexit 1\n")   # niente gh: l'anteprima non cerca issue esistenti
(bin_ / "gh").chmod(0o755)
cfg = tmp / "c.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "s"), "accounts": {"personal": {"config_dir": str(conf)}}, "default_account": "personal",
                           "workspace": {"root": str(tmp / "ws"), "root_session_name": "master", "project_dirs": ["."]}, "relay": {"dir": str(tmp / "r")}}))
(tmp / "r").mkdir()
(tmp / "ws").mkdir()
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR", "CLAUDE_OBSERVE_TOOL")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db"),
            "CLAUDE_OBSERVE_CONFIG": str(ocfg), "PATH": f"{bin_}:{os.environ.get('PATH', '')}", "LANG": "it_IT.UTF-8"})
os.environ.clear()
os.environ.update(ENV)

now = time.time()


def rec(rid, call, error, count, status="new", kind=None, source="hook", **kw):
    r = {"v": 1, "id": rid, "tool": "supervisor", "source": source, "call": call, "error": error, "key": error, "count": count,
         "first_seen": now - 3600, "last_seen": now - 60, "examples": [], "workaround": None, "class": None, "status": status,
         "account": ".claude", "project": "acme"}
    if kind:
        r["kind"] = kind
    return dict(r, **kw)


HOME_ERR = f"No such file: {home}/Video/clip finale.mp4"
recs = [rec("sv-1", "supervisor talk", "exit 1: session not found", 9),
        rec("sv-2", "supervisor launch", HOME_ERR, 7),
        rec("sv-3", "supervisor close", "exit 3: refused", 5, status="triaged", **{"class": "D"}),
        rec("sv-4", "supervisor quota", "exit 2: no data", 2),
        rec("sv-5", "a note by hand", "", 1, kind="note", source="manual"),
        rec("sv-6", "supervisor old", "exit 1: old", 40, status="reported", reported="https://github.com/acme/tool/issues/1"),
        rec("sv-7", "supervisor secret", "exit 1: token", 3, security=True)]
(store / "supervisor.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))


def load(n):
    sp = importlib.util.spec_from_file_location(n.replace("-", "_"), T.SCRIPTS / f"{n}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


FB = load("cm-feedback")
RL = load("cm-relay")
S = load("cm-relay-state")


def ex(**cmd):
    return RL.execute(dict({"id": "x", "session": None}, **cmd))[:2]


# ------------------------------------------------------------------ FS feedback_state
st = FB.state()
pl = (st or {}).get("plugins") or []
sv = pl[0] if pl else {}
T.check("feedback_state FS1 one row per installed plugin that carries observe; none for a plugin without it",
        [p["plugin"] for p in pl] == ["supervisor"], json.dumps(st)[:400])
T.check("feedback_state FS2 the row's keys: plugin, version, new, pending, security, anonymous, consent, last_sent, top",
        list(sv) == ["plugin", "version", "new", "pending", "security", "anonymous", "consent", "last_sent", "top"], str(list(sv)))
T.check("feedback_state FS3 counts: new = not yet triaged, pending = what a send would carry (no reported, no security), security apart",
        (sv.get("new"), sv.get("pending"), sv.get("security"), sv.get("version")) == (4, 5, 1, "0.9.0"), str(sv)[:300])
top = sv.get("top") or []
T.check("feedback_state FS4 the three most frequent errors, never a note, never a reported or security one",
        [x["count"] for x in top] == [9, 7, 5] and list(top[0]) == ["call", "error", "count"], str(top))
T.check("feedback_state FS5 already cleaned: no home, no file name, as observe sends them",
        str(home) not in json.dumps(st) and "clip finale" not in json.dumps(st) and "<file>.mp4" in top[1]["error"], top[1]["error"] if len(top) > 1 else "")
T.check("feedback_state FS6 consent off by default, anonymous available with an endpoint, nothing sent yet but the reported record's URL",
        sv.get("consent") == {"on": False, "since": None} and sv.get("anonymous") is True
        and sv.get("last_sent") == {"at": None, "url": "https://github.com/acme/tool/issues/1"}, str(sv.get("consent")) + str(sv.get("last_sent")))
T.check("feedback_state FS7 the notice for a written suggestion is in the state, and it says it becomes a public issue",
        "pubblica" in str((st or {}).get("notice") or ""), str((st or {}).get("notice")))
T.check("feedback_state FS8 small: under 1.5 KB for one plugin", len(json.dumps(st or {}, ensure_ascii=False).encode()) < 1536, str(len(json.dumps(st or {}))))

# /state: build_state carries it, fit_state drops the errors before anything that matters
bs = S.build_state({"feedback": st}, int(now), fit=False)
T.check("feedback_state FS9 build_state carries state.feedback as given; absent without plugins",
        bs.get("feedback") == st and "feedback" not in S.build_state({}, int(now), fit=False), "")
big = S.build_state({"feedback": st, "projects": [{"path": f"/p/{i}", "name": f"progetto-numero-{i:03d}-" + "x" * 60, "account": "a", "last_used": i} for i in range(200)]}, int(now))
T.check("feedback_state FS10 over the cap fit_state leaves feedback whole (a real state stays over the cap with its live sessions: a cut here would always fire)",
        big.get("feedback") == st and len(big["projects"]) < 200, str(big.get("feedback"))[:300])
T.check("feedback_state FS11 the relay's source reads it (feedback_summary) and the three ops are in OPS, none of them a passive read",
        {k: v for k, v in RL.feedback_summary(fresh=True).items() if k != "at"} == {k: v for k, v in FB.state().items() if k != "at"} and all(o in RL.OPS for o in ("feedback_consent", "feedback_send", "feedback_text"))
        and not any(o in RL.PASSIVE_OPS for o in ("feedback_consent", "feedback_send", "feedback_text")), "")

# ------------------------------------------------------------------ AC always_anonymous_consent
ok, t = ex(op="feedback_consent", arg="supervisor", on=True)
T.check("always_anonymous_consent AC1 without a touch (no device) refused, nothing saved",
        not ok and "always_anonymous" not in ocfg.read_text(), t)
ok, t = ex(op="feedback_consent", arg="supervisor", on=True, device="phone")
saved = json.loads(ocfg.read_text())
T.check("always_anonymous_consent AC2 a touch turns it on where observe reads it (tools.supervisor.always_anonymous), the rest of the config kept",
        ok and bool(saved["tools"]["supervisor"].get("always_anonymous")) and saved["endpoint"] == ENDPOINT and saved["dir"] == str(store), t + json.dumps(saved))
sv2 = FB.state()["plugins"][0]
T.check("always_anonymous_consent AC3 the state says on, since when", sv2["consent"]["on"] is True and sv2["consent"]["since"], str(sv2["consent"]))
T.check("always_anonymous_consent AC4 turning it on sends nothing by itself", POSTS == [], str(POSTS)[:200])
stats = json.loads((store / ".stats-supervisor.json").read_text())
T.check("always_anonymous_consent AC5 counted in observe's local stats as a choice from the phone", stats.get("always_on_phone") == 1, str(stats))
ok, t = ex(op="feedback_consent", arg="supervisor", on=False, device="web")
T.check("always_anonymous_consent AC6 off from a touch: removed, the state says off",
        ok and "always_anonymous" not in json.loads(ocfg.read_text())["tools"]["supervisor"] and FB.state()["plugins"][0]["consent"] == {"on": False, "since": None}, t)
ok, t = ex(op="feedback_consent", arg="plain", on=True, device="phone")
T.check("always_anonymous_consent AC7 a plugin without observe refused", not ok, t)
ok, t = ex(op="feedback_consent", arg="supervisor", device="phone")
T.check("always_anonymous_consent AC8 `on` must be a boolean", not ok and "always_anonymous" not in json.loads(ocfg.read_text())["tools"]["supervisor"], t)

# ------------------------------------------------------------------ SE feedback_send
ok, t = ex(op="feedback_send", arg="supervisor")
T.check("feedback_send SE1 without a touch refused, nothing sent", not ok and POSTS == [], t)
ok, t = ex(op="feedback_send", arg="supervisor", device="phone")
pv = json.loads(t) if ok else {}
T.check("feedback_send SE2 without hash: the preview — {plugin, n, title, body, hash}, nothing sent",
        ok and list(pv) == ["plugin", "n", "title", "body", "hash"] and pv["n"] == 5 and POSTS == [], t[:300])
T.check("feedback_send SE3 the preview is already cleaned", str(home) not in t and "clip finale" not in t and "[supervisor]" in pv.get("title", ""), pv.get("body", "")[:300])
ok, t = ex(op="feedback_send", arg="supervisor", hash="0000000000", device="phone")
T.check("feedback_send SE4 a hash that is not the preview's refused, nothing sent", not ok and POSTS == [], t)
ok, t = ex(op="feedback_send", arg="supervisor", hash=pv.get("hash"), device="phone")
T.check("feedback_send SE5 with the preview's hash: one anonymous POST, exactly the previewed text",
        ok and len(POSTS) == 1 and POSTS[0]["title"] == pv["title"] and POSTS[0]["body"] == pv["body"] and POSTS[0]["plugin"] == "supervisor", t + str(POSTS)[:300])
after = FB.state()["plugins"][0]
T.check("feedback_send SE6 the sent records are reported: pending 0, last_sent with the issue URL and the time",
        after["pending"] == 0 and after["last_sent"]["url"] == "https://github.com/acme/tool/issues/1" and isinstance(after["last_sent"]["at"], int)
        and after["security"] == 1, str(after))
ok, t = ex(op="feedback_send", arg="supervisor", device="phone")
T.check("feedback_send SE7 nothing left: ok with n 0 and no hash, nothing sent", ok and json.loads(t).get("n") == 0 and len(POSTS) == 1, t)

# ------------------------------------------------------------------ TX feedback_text
ok, t = ex(op="feedback_text", arg="supervisor", text="vorrei il pulsante")
T.check("feedback_text TX1 without a touch refused, nothing sent", not ok and len(POSTS) == 1, t)
ok, t = ex(op="feedback_text", arg="supervisor", text="   ", device="phone")
T.check("feedback_text TX2 an empty text refused", not ok and len(POSTS) == 1, t)
ok, t = ex(op="feedback_text", arg="supervisor", text=f"Il file {home}/Documenti/contratto rossi.pdf non si apre: vorrei un pulsante «Riprova»", device="phone")
T.check("feedback_text TX3 a written suggestion: one anonymous POST, cleaned (no home, no file name), titled with the plugin",
        ok and len(POSTS) == 2 and str(home) not in json.dumps(POSTS[1]) and "contratto rossi" not in json.dumps(POSTS[1])
        and "Riprova" in POSTS[1]["body"] and POSTS[1]["title"].startswith("[supervisor]") and POSTS[1]["security"] is False, t + json.dumps(POSTS[1:])[:400])
T.check("feedback_text TX4 the answer gives the issue URL; last_sent moves to it", "issues/2" in t and FB.state()["plugins"][0]["last_sent"]["url"].endswith("/issues/2"), t)
ok, t = ex(op="feedback_text", arg="supervisor", text="x" * 4001, device="phone")
T.check("feedback_text TX5 over 4000 characters refused", not ok and len(POSTS) == 2, t)

# senza endpoint: niente opzione anonima, niente consenso, niente invio
ocfg.write_text(json.dumps({"dir": str(store), "endpoint": "", "anonymizer": str(tmp / "none.py")}))
T.check("feedback_state FS12 with no endpoint anonymous is false", FB.state()["plugins"][0]["anonymous"] is False, "")
ok, t = ex(op="feedback_consent", arg="supervisor", on=True, device="phone")
ok2, t2 = ex(op="feedback_text", arg="supervisor", text="ciao", device="phone")
T.check("always_anonymous_consent AC9 with no endpoint the consent and the text are refused, nothing sent",
        not ok and not ok2 and len(POSTS) == 2 and "always_anonymous" not in ocfg.read_text(), t + " | " + t2)

# il contratto: le fixture hanno le chiavi che il codice manda
F3 = json.loads((T.ROOT / "tests/fixtures/relay/state-3-stale.json").read_text())
F1, F2 = (json.loads((T.ROOT / f"tests/fixtures/relay/{f}.json").read_text()) for f in ("state-1-question", "state-2-idle"))
T.check("feedback_state FS13 state-3 carries feedback with the code's keys, and every state's ops carry the three feedback ops in a row",
        list(F3.get("feedback") or {}) == list(st) and list(F3["feedback"]["plugins"][0]) == list(sv) and list(F3["feedback"]["plugins"][0]["consent"]) == ["on", "since"]
        and all(F["ops"][F["ops"].index("feedback_consent"):][:3] == ["feedback_consent", "feedback_send", "feedback_text"] for F in (F1, F2, F3)), str(F3.get("feedback"))[:300])
_fp = json.loads((T.ROOT / "tests/fixtures/relay/cmd-result-sample.json").read_text())
T.check("feedback_send SE8 cmd-result-sample has the three ops and the preview with the code's keys",
        {"feedback_consent", "feedback_send", "feedback_text"} <= {c["op"] for c in _fp["cmd"]}
        and any(list(json.loads(r["text"])) == list(pv) for r in _fp["result"] if r["text"].startswith('{"plugin"')), "")
srv.shutdown()
T.finish()
