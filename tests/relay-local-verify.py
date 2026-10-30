#!/usr/bin/env python3
"""Verifica la prova da un minuto senza Firebase (13/10/2026, chiesta dal maintainer): `relay web` col relay spento e
senza relay.firebase_url.

LW1 `relay web --no-open` accende relay.enabled e relay.web.enabled nel config (il resto com'e'), avvia il daemon e
    stampa l'indirizzo col token
LW2 senza relay.web.dir la web app viene da relay.web_app_url: index.html e un asset, tenuti in <relay.dir>/web-cache;
    fuori da assets/ (index.html, sw.js) si riprende a ogni richiesta; col sito irraggiungibile resta la cache; un asset
    mai visto → 502
LW3 /api/state risponde con lo stato pubblicato in locale; nel log niente Firebase (nessun «rete assente», nessuna push
    fallita)
LW4 un relay spento con relay.firebase_url impostato resta spento: `relay web` esce 2 e non tocca il config
LW5 doctor col relay solo locale: INFO relay_local, nessun FAIL per cryptography o crontab
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
rdir = tmp / "relay"
cfg = tmp / "config.json"
SITE = {"/index.html": b"<!doctype html><title>site</title><script src=/assets/app-1.js></script>",
        "/assets/app-1.js": b"console.log('site')", "/sw.js": b"// sw 1"}
HITS = []


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        HITS.append(self.path)
        body = SITE.get(self.path)
        self.send_response(200 if body else 404)
        self.end_headers()
        self.wfile.write(body or b"")

    def log_message(self, *a):
        pass


site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=site.serve_forever, daemon=True).start()
import socket  # noqa: E402
_s = socket.socket(); _s.bind(("127.0.0.1", 0)); port = _s.getsockname()[1]; _s.close()


def write_cfg(**relay):
    cfg.write_text(json.dumps({
        "language": "en", "state_dir": str(tmp / "state"), "workspace": {"root": str(home / "ws")},
        "accounts": {"personal": {"config_dir": str(home / ".claude")}}, "default_account": "personal",
        "relay": dict({"dir": str(rdir), "web": {"port": port}, "web_app_url": f"http://127.0.0.1:{site.server_address[1]}"}, **relay),
        "tune": {"keep": "me"}}))


ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
       "CM_CRONTAB_CMD": "false", "LANG": "C.UTF-8"}


def relay(*a):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-relay.py"), *a], capture_output=True, text=True, env=ENV, timeout=60)


def get(path, token=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError:
        return 0, b""


def stop_daemon():
    try:
        os.kill(int((rdir / "serve.pid").read_text()), 15)
    except (OSError, ValueError):
        pass


write_cfg(enabled=False)
r = relay("web", "--no-open")
c = json.loads(cfg.read_text())
tok = (rdir / "web-token").read_text().strip() if (rdir / "web-token").exists() else ""
T.check("LW1 relay web with the relay off and no Firebase → relay.enabled and relay.web.enabled on, the rest kept, the address with the token, exit 0",
        r.returncode == 0 and c["relay"]["enabled"] is True and c["relay"]["web"] == {"port": port, "enabled": True}
        and c["tune"] == {"keep": "me"} and f"http://127.0.0.1:{port}/?t=" in r.stdout and "local mode" in r.stdout, r.stdout + r.stderr)
T.check("LW1 the daemon listens", T.wait_until(lambda: get("/")[0] == 200, 8), (rdir / "relay.log").read_text()[-400:] if (rdir / "relay.log").exists() else "")

st, body = get("/")
st2, body2 = get("/assets/app-1.js")
st3, body3 = get("/sessions/master")   # una rotta della web app
T.check("LW2 without relay.web.dir: / and an asset come from relay.web_app_url, an app route gets index.html",
        st == 200 and body == SITE["/index.html"] and st2 == 200 and body2 == SITE["/assets/app-1.js"] and st3 == 200 and body3 == SITE["/index.html"], f"{st} {st2} {st3}")
T.check("LW2 kept in <relay.dir>/web-cache", (rdir / "web-cache" / "assets" / "app-1.js").read_bytes() == SITE["/assets/app-1.js"]
        and (rdir / "web-cache" / "index.html").is_file(), str(list((rdir / "web-cache").rglob("*"))) if (rdir / "web-cache").exists() else "no cache")
n = len(HITS)
get("/assets/app-1.js")
T.check("LW2 an asset already in the cache is not fetched again", HITS[n:] == [], str(HITS[n:]))
get("/sw.js")
SITE["/sw.js"] = b"// sw 2"
st6, body6 = get("/sw.js")
T.check("LW2 everything outside assets/ (sw.js, index.html) is fetched again: a new build reaches the service worker", st6 == 200 and body6 == b"// sw 2", body6[:40])
site.shutdown(); site.server_close()
st, body = get("/")
st2, _ = get("/assets/app-1.js")
st4, _ = get("/assets/never-seen.js")
T.check("LW2 the site unreachable → index.html and the asset from the cache; an asset never seen → 502", st == 200 and body == SITE["/index.html"] and st2 == 200 and st4 == 502, f"{st} {st2} {st4}")
st5, _ = get("/../config.json")
T.check("LW2 no way out of the cache", st5 in (400, 404), str(st5))

ok = T.wait_until(lambda: get("/api/state", tok)[0] == 200, 60)   # 30/10: a nice 19 la prima push passa i 15 s (release 0.8.87 ferma qui)
st, body = get("/api/state", tok)
try:
    state = json.loads(body)
except ValueError:
    state = {}
log = (rdir / "relay.log").read_text()
T.check("LW3 /api/state answers with the state published locally", ok and isinstance(state.get("sessions"), list) and state.get("published_at"), body[:200])
T.check("LW3 no Firebase in the log: no «rete assente», no failed push", "rete assente" not in log and "FAILED" not in log and "no key" not in log, log[-600:])
stop_daemon()

write_cfg(enabled=False, firebase_url="https://example.firebaseio.com")
before = cfg.read_text()
r = relay("web", "--no-open")
T.check("LW4 a relay off with relay.firebase_url set stays off: relay web exits 2, config untouched", r.returncode == 2 and cfg.read_text() == before, r.stdout + r.stderr)

write_cfg(enabled=True)
fake = T.fake_machine(tmp, home, chromeos=False, procs=[], which=["tmux", "claude", "python3", "bash"])   # niente cryptography, niente crontab
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-config.py"), "doctor"], capture_output=True, text=True, env=dict(ENV, CM_FAKE_MACHINE=str(fake)), timeout=60)
T.check("LW5 doctor with the relay in local mode: INFO relay_local, no FAIL for cryptography or crontab",
        "INFO relay_local" in r.stdout and "FAIL crypto" not in r.stdout and "FAIL crontab" not in r.stdout, r.stdout[-1200:])
stop_daemon()
T.rm(tmp)
T.finish()
