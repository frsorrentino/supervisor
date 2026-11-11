#!/usr/bin/env python3
"""Contratto 1.121 (04/11/2026, compito supervisor-feedback-app-04-11): il Feedback dell'app («Suggerimento» o «Segnala un
bug») arriva al relay con l'op feedback_app, diventa un'osservazione di supervisor-app e torna all'app come bozza
anonimizzata con tre scelte; l'invio scelto apre la issue. Il giro completo con l'invio anonimo verso un servizio finto
che applica le stesse regole di claude-observe/server/report.php (campi esatti, titolo e corpo di una bozza di observe):
una bozza che il servizio vero rifiuterebbe qui fallisce.

FA draft (primo passo), FS screenshot (solo col si', mai nell'invio anonimo), FN anonymous, FG github, FL later,
FX contratto (OPS, fixture). Mai un invio senza un tocco: senza `device` niente parte."""
import http.server
import importlib.util
import base64
import json
import os
import re
import sys
import threading
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
conf = home / ".claude"
(conf / "plugins").mkdir(parents=True)
store = tmp / "observe-state"
store.mkdir()

# il servizio anonimo finto, con le regole di report.php (observe_parse): un campo in piu' o in meno, un titolo o un
# corpo che non sono quelli di una bozza di observe → 400, come il servizio vero
FIELDS = ["plugin", "version", "security", "severity", "title", "body"]
REPOS = {"supervisor-app": "frsorrentino/supervisor-feedback", "supervisor": "frsorrentino/supervisor"}
POSTS, REFUSED, UPLOADS = [], [], []
SERVICE = {"refuse_upload": None}
KIND_LABELS = ("app", "suggerimento", "bug")


def image_check(plugin, img):
    """05/11 (1.123): l'immagine come la vuole report.php (observe_image): solo supervisor-app, {mime, data}, jpeg o png, 1 MB."""
    if plugin != "supervisor-app":
        return "images are accepted only for: supervisor-app"
    if not isinstance(img, dict) or sorted(img) != ["data", "mime"] or img["mime"] not in ("image/jpeg", "image/png"):
        return "the image must be image/jpeg or image/png"
    try:
        b = base64.b64decode(img["data"], validate=True)
    except (ValueError, TypeError):
        return "image data is not base64"
    if not b or len(b) > 1048576:
        return "the image is larger than 1048576 bytes"
    if not b.startswith(b"\x89PNG" if img["mime"] == "image/png" else b"\xff\xd8"):
        return "the image is not a " + img["mime"]
    return None


def service_check(r):
    if isinstance(r, dict) and sorted(r) == ["image", "plugin"]:
        return image_check(r["plugin"], r["image"])
    if not isinstance(r, dict) or set(FIELDS) - set(r) or set(r) - set(FIELDS) - {"labels", "image"}:
        return "the fields must be exactly: " + ", ".join(FIELDS)
    if "labels" in r and (not isinstance(r["labels"], list) or not r["labels"] or len(set(map(str, r["labels"]))) != len(r["labels"])
                          or any(x not in KIND_LABELS for x in r["labels"]) or r["security"] is not False):
        return "labels"
    if "image" in r and (r["security"] is not False or image_check(r["plugin"], r["image"])):
        return "image: " + str(image_check(r["plugin"], r["image"]))
    if r["plugin"] not in REPOS:
        return "plugin"
    if not (r["version"] is None or (isinstance(r["version"], str) and re.fullmatch(r"[0-9A-Za-z.+-]{1,40}", r["version"]))):
        return "version"
    if not isinstance(r["security"], bool) or r["severity"] not in (None, "high"):
        return "security"
    p = re.escape(r["plugin"])
    if len(r["title"]) > 300 or "\n" in r["title"] or not re.match(rf"^\[{p}\] field observations: ", r["title"]):
        return "the title is not the one of a claude-observe draft"
    lines = r["body"].split("\n")
    if len(lines) < 3 or not re.match(r"^Collected on \d{4}-\d{2}-\d{2} by claude-observe ", lines[0]) or lines[1] != "":
        return "the body is not the one of a claude-observe draft"
    if any(not re.match(r"^(- |  - (note|workaround): )", ln) or len(ln) > 2000 for ln in lines[2:]):
        return "the body is not the one of a claude-observe draft (line)"
    return None


class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        r = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
        err = service_check(r) or ("the fields must be exactly: " + ", ".join(FIELDS) if SERVICE.get("six_only") and sorted(r) != sorted(FIELDS) else None)
        if not err and sorted(r) == ["image", "plugin"] and SERVICE["refuse_upload"]:
            err = SERVICE["refuse_upload"]
        if err:
            REFUSED.append((err, r))
            out, code = {"error": err}, 400 if not SERVICE["refuse_upload"] else 429
        elif sorted(r) == ["image", "plugin"]:
            UPLOADS.append(r)
            out, code = {"url": f"https://github.com/frsorrentino/supervisor-feedback/raw/main/images/2026/11/{'%024x' % len(UPLOADS)}.png"}, 201
        else:
            POSTS.append(r)
            out, code = {"url": f"https://github.com/frsorrentino/supervisor-feedback/issues/{900 + len(POSTS)}"}, 200
        body = json.dumps(out).encode()
        self.send_response(code)
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

bin_ = tmp / "bin"
bin_.mkdir()
(bin_ / "gh").write_text("#!/bin/sh\nexit 1\n")   # nessun gh: niente parte da un account GitHub
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


def load(n):
    sp = importlib.util.spec_from_file_location(n.replace("-", "_"), T.SCRIPTS / f"{n}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


RL = load("cm-relay")


def ex(**cmd):
    return RL.execute(dict({"id": "x", "session": None}, **cmd))[:2]


def box():
    p = store / "supervisor-app.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def drafts():
    return sorted((tmp / "s" / "feedback-app").glob("draft-*.json"))


TECH = {"app_version": "2.4.1", "os": "Android 15", "device": "Pixel 7", "screen": "Sessioni"}
BUG = (f"L'elenco delle sessioni resta vuoto dopo il login.\nSuccede anche con {home}/Documenti/rossi.pdf aperto "
       "e scrivendo a mario.rossi@example.com")

# ------------------------------------------------------------------ FA il primo passo: la bozza
ok, t = ex(op="feedback_app", kind="bug", text=BUG, tech=TECH)
T.check("FA1 without a touch (no device) refused: no observation, no draft, nothing sent",
        not ok and box() == [] and drafts() == [] and POSTS == [], t)
ok, t = ex(op="feedback_app", kind="lamentela", text="x", device="phone")
T.check("FA2 a kind that is not suggerimento or bug refused, naming the valid ones", not ok and "suggerimento" in t and box() == [], t)
ok, t = ex(op="feedback_app", kind="bug", text="   ", device="phone")
T.check("FA3 an empty text refused", not ok and box() == [], t)
ok, t = ex(op="feedback_app", kind="bug", text="x" * 4001, device="phone")
T.check("FA4 over 4000 characters refused", not ok and box() == [], t)
ok, t = ex(op="feedback_app", kind="bug", text="x", tech="Pixel 7", device="phone")
T.check("FA5 tech that is not an object refused", not ok and box() == [], t)

ok, t = ex(op="feedback_app", kind="bug", text=BUG, tech=TECH, device="phone")
d1 = json.loads(t) if ok else {}
T.check("FA6 a touch: ok with the draft as JSON — hash, kind, label, title, body, screenshot, options; nothing sent",
        ok and list(d1) == ["hash", "kind", "label", "title", "body", "screenshot", "screenshot_notice", "github_ok", "options"] and d1["kind"] == "bug"
        and d1["screenshot"] is False and d1["screenshot_notice"] is None and d1["github_ok"] is False and POSTS == [] and REFUSED == [], t[:400])
T.check("FA7 the three choices as buttons, in order: github «Invia dal mio GitHub», anonymous «Invia anonimo», later «Non ora»",
        [(o["id"], o["label"]) for o in d1.get("options", [])] == [("github", "Invia dal mio GitHub"), ("anonymous", "Invia anonimo"), ("later", "Non ora")]
        and all(o["description"] for o in d1["options"]), str(d1.get("options")))
txt = json.dumps(d1, ensure_ascii=False)
T.check("FA8 the draft is anonymized: no home, no file name, no email",
        str(home) not in txt and "rossi.pdf" not in txt and "mario.rossi" not in txt and "<EMAIL>" in d1["body"] and "<file>.pdf" in d1["body"], d1.get("body", "")[:400])
T.check("FA9 the draft says what and where: «[supervisor-app] field observations: bug …», the text as «- bug: …», the technical data in a note",
        d1["title"].startswith("[supervisor-app] field observations: bug from the app, L'elenco delle sessioni")
        and "\n- bug: L'elenco delle sessioni resta vuoto dopo il login." in d1["body"]
        and "  - note: app_version 2.4.1 · os Android 15 · device Pixel 7 · screen Sessioni" in d1["body"], d1["title"] + "\n" + d1["body"])
T.check("FA10 the draft is one the real service accepts (same rules as report.php)",
        service_check({"plugin": "supervisor-app", "version": None, "security": False, "severity": None, "title": d1["title"], "body": d1["body"]}) is None, d1["body"])
b = box()
T.check("FA11 an observation of supervisor-app in observe's box: a note, source app, the kind, the technical data, status new",
        len(b) == 1 and b[0]["tool"] == "supervisor-app" and b[0]["kind"] == "note" and b[0]["source"] == "app" and b[0]["app_kind"] == "bug"
        and b[0]["tech"] == TECH and b[0]["status"] == "new" and b[0]["call"] == "app bug", json.dumps(b)[:400])
T.check("FA12 the observation keeps the text cleaned like any observe record (no home, no email)",
        str(home) not in json.dumps(b) and "mario.rossi" not in json.dumps(b), json.dumps(b)[:400])

# ------------------------------------------------------------------ FN l'invio anonimo, il giro completo
ok, t = ex(op="feedback_app", hash=d1["hash"], choice="anonymous")
T.check("FN1 the choice without a touch refused, nothing sent", not ok and POSTS == [], t)
ok, t = ex(op="feedback_app", hash="0000000000", choice="anonymous", device="phone")
T.check("FN2 a hash that is no draft refused, nothing sent", not ok and POSTS == [], t)
ok, t = ex(op="feedback_app", hash=d1["hash"], choice="maybe", device="phone")
T.check("FN3 a choice that is not github, anonymous or later refused, naming them", not ok and "anonymous" in t and POSTS == [], t)
ok, t = ex(op="feedback_app", hash=d1["hash"], choice="anonymous", device="phone")
r1 = json.loads(t) if ok else {}
T.check("FN4 «Invia anonimo»: one POST the service accepts, exactly the draft shown, plugin supervisor-app, never security; 1.123 the labels app and the kind, no image",
        ok and len(POSTS) == 1 and REFUSED == [] and POSTS[0]["title"] == d1["title"] and POSTS[0]["body"] == d1["body"]
        and POSTS[0]["plugin"] == "supervisor-app" and POSTS[0]["security"] is False and POSTS[0]["labels"] == ["app", "bug"] and "image" not in POSTS[0], t + json.dumps(REFUSED)[:300])
T.check("FN5 the answer: {choice, url, text, screenshot_url} with the issue URL", r1 == {"choice": "anonymous", "url": "https://github.com/frsorrentino/supervisor-feedback/issues/901",
                                                                    "text": "Inviato in forma anonima: https://github.com/frsorrentino/supervisor-feedback/issues/901", "screenshot_url": None}, t)
T.check("FN6 the observation is reported with the issue URL", box()[0]["status"] == "reported" and box()[0]["reported"].endswith("/issues/901"), json.dumps(box())[:300])
ok, t = ex(op="feedback_app", hash=d1["hash"], choice="anonymous", device="phone")
T.check("FN7 a draft goes once: the same hash again is refused, nothing sent twice", not ok and len(POSTS) == 1, t)

# ------------------------------------------------------------------ FS la schermata: solo col si', mai nell'invio anonimo
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
share = RL.local_dir("share")


def put_share(sid):
    (share / f"{sid}.json").write_text(json.dumps({"mime": "image/png"}))
    (share / f"{sid}.bin").write_bytes(PNG)


put_share("shot1")
ok, t = ex(op="feedback_app", kind="suggerimento", text="Un tasto per riprovare", share="shot1", device="phone", _local=True)
T.check("FS1 a screenshot without the person's yes (screenshot_ok) refused: no observation, and the upload is deleted",
        not ok and len(box()) == 1 and not (share / "shot1.bin").exists(), t)
put_share("shot2")
ok, t = ex(op="feedback_app", kind="suggerimento", text="Un tasto per riprovare\nsulla card della sessione", share="shot2", screenshot_ok=True, device="phone", _local=True)
d2 = json.loads(t) if ok else {}
kept = sorted((tmp / "s" / "feedback-app").glob("*.png"))
T.check("FS2 with the yes: the draft says screenshot true; the screenshot is kept with the observation on this computer, the upload deleted",
        ok and d2["screenshot"] is True and len(kept) == 1 and kept[0].read_bytes() == PNG and oct(kept[0].stat().st_mode)[-3:] == "600"
        and not (share / "shot2.bin").exists() and len(box()) == 2, t[:300])
T.check("FS3 the suggestion's draft: «suggestion», one note per further line; 1.123 the sheet's notice says only the maintainer sees it, as it is (private repository), the descriptions say it goes",
        "\n- suggestion: Un tasto per riprovare\n  - note: sulla card della sessione" in d2["body"] and "solo il manutentore" in d2["screenshot_notice"] and "nessuno ne toglie" in d2["screenshot_notice"]
        and "con la schermata" in d2["options"][1]["description"] and "schermata già dentro" in d2["options"][0]["description"]
        and "repo privato" in d2["options"][0]["description"] and "solo il manutentore" in d2["options"][1]["description"], json.dumps(d2, ensure_ascii=False)[:500])
ok, t = ex(op="feedback_app", hash=d2["hash"], choice="anonymous", device="phone")
T.check("FS4 (1.123) the anonymous send carries the screenshot as the service wants it (image/png, the same bytes) and the labels; the answer says with the screenshot",
        ok and len(POSTS) == 2 and set(POSTS[1]) == set(FIELDS) | {"labels", "image"} and POSTS[1]["image"]["mime"] == "image/png"
        and base64.b64decode(POSTS[1]["image"]["data"]) == PNG and POSTS[1]["labels"] == ["app", "suggerimento"]
        and "con la schermata" in json.loads(t)["text"] and not UPLOADS, t)
# 1.123 «Invia dal mio GitHub» con la schermata: prima il caricamento dal servizio, il link e' gia' nel testo della issue
put_share("shot3")
ok, t = ex(op="feedback_app", kind="bug", text="La card resta grigia", share="shot3", screenshot_ok=True, device="phone", _local=True)
d6 = json.loads(t) if ok else {}
ok, t = ex(op="feedback_app", hash=d6["hash"], choice="github", device="phone")
r6 = json.loads(t) if ok else {}
q6 = urllib.parse.parse_qs(urllib.parse.urlsplit(r6.get("url") or "").query)
T.check("FS5 (1.123) «Invia dal mio GitHub» with the screenshot: one upload {plugin, image} to the service, its link already in the issue's body, no issue from the service",
        ok and len(UPLOADS) == 1 and base64.b64decode(UPLOADS[0]["image"]["data"]) == PNG and len(POSTS) == 2
        and q6.get("body") == [d6["body"] + f"\n- screenshot: ![screenshot]({r6['screenshot_url']})"] and r6["screenshot_url"].startswith("https://github.com/frsorrentino/supervisor-feedback/raw/main/images/")
        and "con la schermata" in r6["text"], t[:400])
put_share("shot4")
ok, t = ex(op="feedback_app", kind="bug", text="Il menu non si chiude", share="shot4", screenshot_ok=True, device="phone", _local=True)
d7 = json.loads(t) if ok else {}
SERVICE["refuse_upload"] = "the service reached its daily limit of images: retry tomorrow, or send without the image"
ok, t = ex(op="feedback_app", hash=d7["hash"], choice="github", device="phone")
SERVICE["refuse_upload"] = None
r7 = json.loads(t) if ok else {}
q7 = urllib.parse.parse_qs(urllib.parse.urlsplit(r7.get("url") or "").query)
T.check("FS6 the upload refused (the day's limit): the link still comes, its body without the image, and the answer says why",
        ok and q7.get("body") == [d7["body"]] and r7["screenshot_url"] is None and "senza la schermata" in r7["text"] and "daily limit" in r7["text"], t[:400])
_big = (tmp / "s" / "feedback-app")
put_share("shot5")
ok, t = ex(op="feedback_app", kind="bug", text="Troppo grande", share="shot5", screenshot_ok=True, device="phone", _local=True)
d8 = json.loads(t) if ok else {}
Path(json.loads((_big / f"draft-{d8['hash']}.json").read_text())["screenshot"]).write_bytes(PNG + b"\0" * 1048576)
ok, t = ex(op="feedback_app", hash=d8["hash"], choice="anonymous", device="phone")
T.check("FS7 a screenshot over 1 MB: the anonymous send goes without it, and the answer says so",
        ok and len(POSTS) == 3 and "image" not in POSTS[2] and "senza la schermata (oltre 1 MB)" in json.loads(t)["text"], t[:300])
put_share("shot6")
ok, t = ex(op="feedback_app", kind="bug", text="Un servizio di prima", share="shot6", screenshot_ok=True, device="phone", _local=True)
d9 = json.loads(t) if ok else {}
SERVICE["six_only"] = True
ok, t = ex(op="feedback_app", hash=d9["hash"], choice="anonymous", device="phone")
SERVICE["six_only"] = False
T.check("FS8 a service that takes only the six fields (report.php before 1.123) refuses the image: the text goes once more alone, and the answer says without the screenshot and why",
        ok and len(POSTS) == 4 and sorted(POSTS[3]) == sorted(FIELDS) and REFUSED[-1][0].startswith("the fields must be exactly")
        and "senza la schermata" in json.loads(t)["text"] and "fields must be exactly" in json.loads(t)["text"], t[:300])

# ------------------------------------------------------------------ FG «Invia dal mio GitHub»
ok, t = ex(op="feedback_app", kind="bug", text="Il tasto Chiudi non risponde", tech={"app_version": "2.4.1"}, device="web")
d3 = json.loads(t) if ok else {}
ok, t = ex(op="feedback_app", hash=d3["hash"], choice="github", device="web")
r3 = json.loads(t) if ok else {}
q = urllib.parse.parse_qs(urllib.parse.urlsplit(r3.get("url") or "").query)
T.check("FG1 (1.123 private) the link of the issue already written on frsorrentino/supervisor-feedback, to open with the person's GitHub: title and body of the draft",
        ok and (r3.get("url") or "").startswith("https://github.com/frsorrentino/supervisor-feedback/issues/new?")
        and q.get("title") == [d3["title"]] and q.get("body") == [d3["body"]], t[:300])
T.check("FG2 with the labels app and the kind", q.get("labels") == ["app,bug"], str(q.get("labels")))
T.check("FG3 nothing goes to the anonymous service, and the observation is marked as sent through the link",
        len(POSTS) == 4 and box()[-1]["status"] == "reported" and box()[-1]["reported"] == "github link", json.dumps(box()[-1])[:300])

# ------------------------------------------------------------------ FL «Non ora»
ok, t = ex(op="feedback_app", kind="suggerimento", text="Un tema scuro per l'orologio", device="watch")
d4 = json.loads(t) if ok else {}
ok, t = ex(op="feedback_app", hash=d4["hash"], choice="later", device="watch")
T.check("FL1 «Non ora»: nothing sent, the observation stays new in the box, the draft is gone",
        ok and json.loads(t)["choice"] == "later" and json.loads(t)["url"] is None and len(POSTS) == 4
        and box()[-1]["status"] == "new" and not (tmp / "s" / "feedback-app" / f"draft-{d4['hash']}.json").exists(), t)

# ------------------------------------------------------------------ senza endpoint: niente invio anonimo
ok, t = ex(op="feedback_app", kind="bug", text="ancora", device="phone")
d5 = json.loads(t) if ok else {}
ocfg.write_text(json.dumps({"dir": str(store), "endpoint": "", "anonymizer": str(tmp / "none.py")}))
ok, t = ex(op="feedback_app", hash=d5["hash"], choice="anonymous", device="phone")
T.check("FN8 with no endpoint the anonymous send is refused and nothing goes", not ok and len(POSTS) == 4, t)

# ------------------------------------------------------------------ FO 1.124 github_ok: il gh di questo computer legge il repo privato?
GHLOG = tmp / "gh.log"
(bin_ / "gh").write_text(f"#!/bin/sh\necho \"$*\" >> {GHLOG}\n[ \"$1 $2\" = \"api repos/frsorrentino/supervisor-feedback\" ] && exit 0\nexit 1\n")
GHOK = tmp / "s" / "feedback-app" / "gh-ok.json"
GHOK.unlink(missing_ok=True)
ok, t = ex(op="feedback_app", kind="suggerimento", text="Una prova di github_ok", device="phone")
T.check("FO1 a gh that reads frsorrentino/supervisor-feedback (exit 0): github_ok true, one call gh api repos/frsorrentino/supervisor-feedback",
        ok and json.loads(t)["github_ok"] is True and GHLOG.read_text().split("\n")[0].startswith("api repos/frsorrentino/supervisor-feedback"), t[:200])
(bin_ / "gh").write_text(f"#!/bin/sh\necho \"$*\" >> {GHLOG}\nexit 1\n")
ok, t = ex(op="feedback_app", kind="suggerimento", text="Una prova di github_ok, ancora", device="phone")
T.check("FO2 within 10 minutes the answer is remembered: github_ok still true, gh not called again",
        ok and json.loads(t)["github_ok"] is True and len(GHLOG.read_text().splitlines()) == 1, GHLOG.read_text())
_c = json.loads(GHOK.read_text()); _c["at"] -= 601; GHOK.write_text(json.dumps(_c))
ok, t = ex(op="feedback_app", kind="suggerimento", text="Una prova di github_ok, dopo", device="phone")
T.check("FO3 after 10 minutes gh is asked again: a gh that cannot read it (404, no login) → github_ok false",
        ok and json.loads(t)["github_ok"] is False and len(GHLOG.read_text().splitlines()) == 2, GHLOG.read_text())

# ------------------------------------------------------------------ FX il contratto
F = [json.loads((T.ROOT / f"tests/fixtures/relay/{f}.json").read_text()) for f in ("state-1-question", "state-2-idle", "state-3-stale")]
T.check("FX1 feedback_app is in OPS right after feedback_text, not a passive read, and in every state fixture's ops",
        RL.OPS[RL.OPS.index("feedback_text") + 1] == "feedback_app" and "feedback_app" not in RL.PASSIVE_OPS
        and all(x["ops"] == list(RL.OPS) for x in F), "")
fx = json.loads((T.ROOT / "tests/fixtures/relay/cmd-result-sample.json").read_text())
cmds = {c["id"]: c for c in fx["cmd"] if c["op"] == "feedback_app"}
res = {r["id"]: r for r in fx["result"] if r["id"] in cmds}
first = [json.loads(res[i]["text"]) for i, c in cmds.items() if "kind" in c and res[i]["ok"]]
second = [json.loads(res[i]["text"]) for i, c in cmds.items() if "choice" in c and res[i]["ok"]]
T.check("FX2 cmd-result-sample has a draft with the code's keys and options, and an answer to each of the three choices",
        any(list(x) == list(d1) and [o["id"] for o in x["options"]] == ["github", "anonymous", "later"] for x in first)
        and sorted(x["choice"] for x in second) == ["anonymous", "github", "later"] and all(list(x) == list(r1) for x in second), json.dumps(list(cmds.values()))[:300])
T.check("FX3 the fixture's drafts are ones the service accepts",
        first and all(service_check({"plugin": "supervisor-app", "version": None, "security": False, "severity": None, "title": x["title"], "body": x["body"]}) is None for x in first), "")
srv.shutdown()
T.finish()
