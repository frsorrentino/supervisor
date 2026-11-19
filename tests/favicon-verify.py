#!/usr/bin/env python3
"""La favicon dei progetti che sono siti (18/11/2026, supervisor-favicon-siti-18-11). L'utente dalla web app alle 11:47:
«se il progetto riguarda un sito, al posto dell'icona accanto al nome potremmo mettere la favicon del sito»; alle 12:01
«ok, per favicon, prosegui». Il PC la prende dal sito stesso, mai da un servizio esterno (l'elenco dei clienti non
esce); la push porta solo l'impronta, l'immagine la chiede l'app una volta con l'op `favicon`.

Un server finto locale fa da sito (CM_FAVICON_URL lo sostituisce a https://<dominio>):
  sito-a.it      <link rel="icon" href="/img/logo.png"> (PNG 128 px) e anche /favicon.ico
  solo-ico.com   nessun link, /favicon.ico (ICO 32 px)
  wp-blog.it     nessun link, /favicon.ico rimanda al logo di WordPress (wp-includes/images/w-logo-blue-white-bg.png)
  vuoto.org      404 ovunque

FV1 quale cartella e' un sito: il nome e' un dominio (sito.com, www.studio.it); no a supervisor, notes.md, v1.2, foo.py
FV2 sito-a.it: dal link rel=icon (non da /favicon.ico), PNG 64 px, piccola, con impronta
FV3 solo-ico.com: senza link, /favicon.ico; PNG 64 px
FV4 wp-blog.it (l'icona di WordPress) e vuoto.org: nessuna favicon, nessuna impronta
FV5 ogni settimana: una favicon di oggi non si riscarica (il server non la vede); una di 8 giorni fa si'
FV6 nella push: la sessione col sito ha `favicon` = l'impronta, le altre non hanno il campo; nessuna immagine nello stato; le cartelle
    da rinfrescare partono in un processo a parte, una volta
FV7 op `favicon`: con l'impronta l'immagine (mime, data, 64 px); un'impronta sconosciuta e' un errore; l'op e' fra le
    letture (OPS, PASSIVE_OPS, ARG_KEYED_OPS)
FV8 niente servizi di terzi nel codice (s2/favicons di Google, DuckDuckGo, icon.horse, favicon grabber)
"""
import base64
import importlib.util
import io
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

from PIL import Image  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
conf = home / ".claude"
ws = tmp / "ws"
for d in (conf, ws / "clienti" / "sito-a.it", ws / "supervisor", tmp / "r"):
    d.mkdir(parents=True, exist_ok=True)
cfg = tmp / "c.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "s"), "default_account": "personal",
                           "accounts": {"personal": {"config_dir": str(conf)}},
                           "workspace": {"root": str(ws), "root_session_name": "master"}, "relay": {"dir": str(tmp / "r")}}))


def png(size, color=(200, 30, 30, 255)):
    b = io.BytesIO()
    Image.new("RGBA", (size, size), color).save(b, "PNG")
    return b.getvalue()


def ico(size):
    b = io.BytesIO()
    Image.new("RGBA", (size, size), (30, 30, 200, 255)).save(b, "ICO", sizes=[(size, size)])
    return b.getvalue()


HITS = []
SITES = {
    "sito-a.it": {"/": (200, "text/html", b'<html><head><title>A</title><link rel="stylesheet" href="/s.css"><link rel="icon" type="image/png" href="/img/logo.png"></head><body>x</body></html>'),
                  "/img/logo.png": (200, "image/png", png(128)), "/favicon.ico": (200, "image/x-icon", ico(16))},
    "solo-ico.com": {"/": (200, "text/html", b"<html><head><title>B</title></head></html>"), "/favicon.ico": (200, "image/x-icon", ico(32))},
    "wp-blog.it": {"/": (200, "text/html", b"<html><head><meta name='generator' content='WordPress 6.8'></head></html>"),
                   "/favicon.ico": (302, "/wp-includes/images/w-logo-blue-white-bg.png", b""),
                   "/wp-includes/images/w-logo-blue-white-bg.png": (200, "image/png", png(64, (0, 115, 170, 255)))},
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        m = re.match(r"^/([^/]+)(/.*)?$", self.path)
        dom, path = (m.group(1), m.group(2) or "/") if m else ("", "/")
        HITS.append((dom, path))
        code, ctype, body = SITES.get(dom, {}).get(path, (404, "text/plain", b"no"))
        self.send_response(code)
        if code == 302:
            self.send_header("Location", f"/{dom}{ctype}")
            body = b""
        else:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}/{{domain}}"

ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db"), "CM_FAVICON_URL": BASE, "CM_FAVICON_PACE_S": "0"})   # 19/11: niente pausa fra le richieste al server finto
os.environ.clear()
os.environ.update(ENV)


def load(n):
    sp = importlib.util.spec_from_file_location(n.replace("-", "_") + "_fv", T.SCRIPTS / f"{n}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


fav_path = T.SCRIPTS / "cm-favicon.py"
if not fav_path.exists():
    T.check("cm-favicon.py exists", False, str(fav_path))
    T.finish()
FV = load("cm-favicon")


def pic(rec):
    return Image.open(io.BytesIO(base64.b64decode(rec["data"])))


# ------------------------------------------------------------------ FV1
yes = ["sito.com", "www.studio.it", "sito-a.it", "Negozio.Shop"]
no = ["supervisor", "notes.md", "v1.2", "foo.py", "backup.old", "a.b", ".hidden", "sito.com.bak"]
got = {x: FV.domain_of("/home/u/ws/clienti/" + x) for x in yes + no}
T.check("FV1 a folder named like a domain is a site; supervisor, notes.md, v1.2, foo.py and the like are not",
        all(got[x] == x.lower() for x in yes) and all(got[x] is None for x in no), json.dumps(got))

# ------------------------------------------------------------------ FV2..FV4
ra = FV.refresh("sito-a.it")
T.check("FV2 sito-a.it: from the link rel=icon (not /favicon.ico), PNG 64 px, small, with a hash",
        ra.get("hash") and ra.get("mime") == "image/png" and pic(ra).size == (64, 64) and pic(ra).getpixel((32, 32))[:3] == (200, 30, 30)
        and len(base64.b64decode(ra["data"])) <= 16384 and ("sito-a.it", "/img/logo.png") in HITS and ("sito-a.it", "/favicon.ico") not in HITS,
        json.dumps({k: v for k, v in ra.items() if k != "data"}) + str(HITS))
rb = FV.refresh("solo-ico.com")
T.check("FV3 solo-ico.com: no link, so /favicon.ico; PNG 64 px",
        rb.get("hash") and pic(rb).size == (64, 64) and pic(rb).getpixel((32, 32))[:3] == (30, 30, 200) and ("solo-ico.com", "/favicon.ico") in HITS,
        json.dumps({k: v for k, v in rb.items() if k != "data"}))
rw, rv = FV.refresh("wp-blog.it"), FV.refresh("vuoto.org")
T.check("FV4 WordPress's own icon and a site with none: no favicon, no hash",
        not rw.get("hash") and not rw.get("data") and not rv.get("hash") and FV.hash_for("wp-blog.it") is None and FV.hash_for("vuoto.org") is None
        and FV.hash_for("sito-a.it") == ra["hash"], json.dumps([rw, rv]))

# ------------------------------------------------------------------ FV5
n0 = len(HITS)
FV.refresh_due(["sito-a.it", "solo-ico.com"])
fresh_ok = len(HITS) == n0 and not FV.stale("sito-a.it")
cache = FV.cache_path("sito-a.it")
old = json.loads(cache.read_text())
old["at"] = time.time() - 8 * 86400
cache.write_text(json.dumps(old))
st8 = FV.stale("sito-a.it")
FV.refresh_due(["sito-a.it", "solo-ico.com"])
T.check("FV5 weekly: today's favicon is not fetched again; one from 8 days ago is",
        fresh_ok and st8 and ("sito-a.it", "/img/logo.png") in HITS[n0:] and not any(d == "solo-ico.com" for d, _ in HITS[n0:]) and not FV.stale("sito-a.it"),
        str(HITS[n0:]))

# ------------------------------------------------------------------ FV6
RL = load("cm-relay")
S = load("cm-relay-state")
spawned = []
RL._favicon_spawn = lambda doms: spawned.append(sorted(doms))
FV.cache_path("solo-ico.com").unlink()   # da rinfrescare
(ws / "clienti" / "solo-ico.com").mkdir()
rows = [{"name": "sito-a-it", "tmux": "sito-a-it", "cwd": str(ws / "clienti" / "sito-a.it"), "status": "idle", "session_id": "s1"},
        {"name": "supervisor", "tmux": "supervisor", "cwd": str(ws / "supervisor"), "status": "idle", "session_id": "s2"},
        {"name": "solo-ico-com", "tmux": "solo-ico-com", "cwd": str(ws / "clienti" / "solo-ico.com"), "status": "idle", "session_id": "s3"}]
fav = RL.favicons_of(rows)
RL.favicons_of(rows)
sess = {r["tmux"]: S.build_session(r, {"rows": rows, "ledger": [], "root": str(ws), "favicons": fav}) for r in rows}
blob = json.dumps(sess)
T.check("FV6 in the push: the site's session carries `favicon` = the hash, the others have no such key (no weight); no image in the state",
        sess["sito-a-it"].get("favicon") == ra["hash"] and "favicon" not in sess["supervisor"]
        and "favicon" not in sess["solo-ico-com"] and ra["data"][:40] not in blob, json.dumps({k: v.get("favicon") for k, v in sess.items()}))
T.check("FV6 the folders to refresh go to a separate process, once (not at every push)", spawned == [["solo-ico.com"]], str(spawned))

# ------------------------------------------------------------------ FV7
ok, txt = RL.execute({"id": "f1", "op": "favicon", "arg": ra["hash"], "device": "phone"})[:2]
body = json.loads(txt) if ok else {}
ok2, txt2 = RL.execute({"id": "f2", "op": "favicon", "arg": "0123456789ab", "device": "phone"})[:2]
T.check("FV7 op favicon with the hash: the image (mime, data, 64 px); an unknown hash is an error",
        ok and body.get("hash") == ra["hash"] and body.get("mime") == "image/png" and pic(body).size == (64, 64) and body.get("domain") == "sito-a.it"
        and not ok2 and "0123456789ab" in txt2, txt[:300] + " | " + txt2[:300])
T.check("FV7 favicon is a read keyed by its argument (OPS, PASSIVE_OPS, ARG_KEYED_OPS)",
        "favicon" in RL.OPS and "favicon" in RL.PASSIVE_OPS and "favicon" in RL.ARG_KEYED_OPS, "")

# ------------------------------------------------------------------ FV8
src = fav_path.read_text().lower()
T.check("FV8 no third-party favicon service in the code", not any(x in src for x in ("s2/favicons", "google.com", "duckduckgo", "icon.horse", "favicongrabber", "besticon")), "")

srv.shutdown()
T.finish()
