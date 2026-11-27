#!/usr/bin/env python3
"""Le favicon arrivano anche dagli hosting che rifiutano i client strani (19/11/2026, supervisor-favicon-scaricate-19-11).

Misurato dalla master: `cm-favicon list` dava fetch_failed per tutti i domini. Con lo UA «Mozilla/5.0 (compatible;
supervisor-favicon)» SiteGround chiudeva la connessione (RemoteDisconnected) o rispondeva 502; con «Mozilla/5.0» 200; lo UA
completo di Chrome rifiutato anche lui (impronta TLS). Il blocco per IP colpisce tutti i siti del PC.

Un server finto locale fa da hosting (CM_FAVICON_URL):
  ua-strano.it   chiude la connessione senza risposta se lo UA non e' esattamente «Mozilla/5.0»
  solo-curl.it   chiude la connessione se la richiesta porta Accept-Encoding (urllib lo manda, curl no): un client che
                 l'hosting rifiuta, come per l'impronta TLS
  ok.it          risponde a tutti

FS1 lo UA e' «Mozilla/5.0»: ua-strano.it da' la favicon (prima fetch_failed)
FS2 se urllib fallisce, un secondo tentativo con curl: solo-curl.it da' la favicon
FS3 refresh --force rifa' subito un fetch_failed di oggi (senza --force resta, il server non lo vede)
FS4 poche richieste e lente: fra una richiesta e l'altra almeno la pausa; un 404 non si ripete con curl
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

try:
    import io
    from PIL import Image
except ImportError:
    print("  (skipped: no Pillow)")
    T.finish()


def png(size):
    b = io.BytesIO()
    Image.new("RGBA", (size, size), (200, 30, 30, 255)).save(b, "PNG")
    return b.getvalue()


PAGE = b'<html><head><link rel="icon" type="image/png" href="/i.png"></head></html>'
HITS = []


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def do_GET(self):
        m = re.match(r"^/([^/]+)(/.*)?$", self.path)
        dom, path = (m.group(1), m.group(2) or "/") if m else ("", "/")
        ua = self.headers.get("User-Agent") or ""
        HITS.append((time.monotonic(), dom, path, ua, "Accept-Encoding" in self.headers))
        if (dom == "ua-strano.it" and ua != "Mozilla/5.0") or (dom == "solo-curl.it" and "Accept-Encoding" in self.headers):
            self.close_connection = True
            self.connection.close()   # come SiteGround: nessuna risposta
            return
        body = {"/": (200, "text/html", PAGE), "/i.png": (200, "image/png", png(64))}.get(path, (404, "text/plain", b"no"))
        if dom not in ("ua-strano.it", "solo-curl.it", "ok.it"):
            body = (404, "text/plain", b"no")
        self.send_response(body[0])
        self.send_header("Content-Type", body[1])
        self.send_header("Content-Length", str(len(body[2])))
        self.end_headers()
        self.wfile.write(body[2])


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
tmp = Path(T.tmpdir())
home = tmp / "home"
home.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "en", "state_dir": str(tmp / "state"), "relay": {"dir": str(tmp / "relay")}}))
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
            "CM_FAVICON_URL": f"http://127.0.0.1:{srv.server_address[1]}/{{domain}}", "CM_FAVICON_PACE_S": "0.3"})


def fav(*args):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-favicon.py"), *args], capture_output=True, text=True, env=ENV, timeout=120)


def rec(d):
    p = tmp / "relay" / "favicons" / f"{d}.json"
    return json.loads(p.read_text()) if p.exists() else {}


fav("refresh", "ua-strano.it")
T.check("FS1 the UA is «Mozilla/5.0»: a host that closes the connection to «(compatible; …)» gives its favicon",
        bool(rec("ua-strano.it").get("hash")) and all(h[3] == "Mozilla/5.0" for h in HITS), str(rec("ua-strano.it")) + str({h[3] for h in HITS}))
fav("refresh", "solo-curl.it")
T.check("FS2 when urllib fails, a second try with curl: a host that refuses Python's client gives its favicon",
        bool(rec("solo-curl.it").get("hash")) and any(h[1] == "solo-curl.it" and not h[4] for h in HITS), str(rec("solo-curl.it")))

# FS3: un fetch_failed di oggi
p = tmp / "relay" / "favicons" / "ok.it.json"
p.write_text(json.dumps({"domain": "ok.it", "at": time.time(), "why": "fetch_failed"}))
n0 = len(HITS)
fav("refresh", "ok.it")
quiet = len(HITS) == n0 and rec("ok.it").get("why") == "fetch_failed"
r = fav("refresh", "--force", "ok.it")
T.check("FS3 refresh --force fetches a fetch_failed of today at once (without --force it stays, the host never sees it)",
        quiet and bool(rec("ok.it").get("hash")) and "ok.it" in r.stdout, f"quiet={quiet} {rec('ok.it')} {r.stdout} {r.stderr[-300:]}")

# FS4: la pausa, e un 404 non si ripete
HITS.clear()
fav("refresh", "--force", "ok.it", "nessuno.it")
gaps = [b[0] - a[0] for a, b in zip(HITS, HITS[1:])]
# 27/11/2026: il client distanzia l'inizio delle richieste di 0,3 s, ma qui si misura l'arrivo al server finto, che cambia
# con la latenza di ogni connessione (curl dopo urllib): misurati 0,23 e 0,25 sulla release 0.8.221, 1 volta su 5 da solo.
# Senza pausa gli intervalli sarebbero di pochi millisecondi: 0,2 lo distingue ancora
T.check("FS4 few and slow requests: at least the pause between one request and the next",
        len(HITS) >= 3 and min(gaps) >= 0.2, str([round(g, 2) for g in gaps]))
T.check("FS4 a 404 is an answer: never asked again with curl (one request per URL of nessuno.it)",
        len([h for h in HITS if h[1] == "nessuno.it"]) == len({h[2] for h in HITS if h[1] == "nessuno.it"}), str([(h[1], h[2], h[4]) for h in HITS]))

srv.shutdown()
T.finish()
