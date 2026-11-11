#!/usr/bin/env python3
"""cm-relay-web — la web app e l'API locale del relay (contratto 1.35, 05/10/2026, chiesto dall'app per il maintainer).

Un server HTTP solo su 127.0.0.1 dentro `relay serve`: serve i file statici della web app (relay.web.dir) su `/` e
un'API con lo stesso JSON del contratto, in chiaro (niente buste {v, enc}: non esce dalla macchina).

  GET  /api/state               lo stato, come /state
  GET  /api/events?since=<ts>   gli eventi con ts > since, dal piu' recente (forma di events-sample)
  GET  /api/stream              SSE: `event: state` con lo stato intero all'apertura e a ogni cambio
  POST /api/cmd                 un Cmd → il suo CmdResult (le stesse op del contratto)
  GET  /api/file/<id>           il file chiesto col comando `file` <id>, intero e in chiaro
  POST /api/share/<id>          {mime, data base64, name} da allegare col comando `report` arg=<id>

Ogni chiamata porta `Authorization: Bearer <token>` (il token sta in <relay.dir>/web-token, 0600); le GET accettano
anche `?t=<token>`, perche' EventSource e <img src> non mandano intestazioni. Host deve essere 127.0.0.1 o localhost
sulla porta del relay (niente DNS rebinding), un Origin diverso da quello della pagina si rifiuta, nessuna intestazione
CORS. Il modulo non conosce Firebase: il relay gli passa `api`, un oggetto con state(), events(since), cmd(c),
file(id) e share(id, blob).
"""
import base64
import binascii
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ID_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")
CMD_BODY_MAX = 256 * 1024
SHARE_BODY_MAX = 96 * 1024 * 1024   # base64 di un file fino a ~70 MB
STREAM_POLL_S = 0.5
STREAM_PING_S = 15
UPSTREAM_TIMEOUT_S = 15
UPSTREAM_PATH_RE = re.compile(r"[A-Za-z0-9._/-]{1,200}")


def load_token(rdir):
    """Il token locale: creato alla prima richiesta, 0600, stabile finche' qualcuno non cancella il file."""
    p = Path(rdir) / "web-token"
    try:
        t = p.read_text().strip()
        if t:
            return t
    except OSError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(32)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t + "\n")
    return t


def url(port, token):
    return f"http://127.0.0.1:{int(port)}/?t={urllib.parse.quote(token)}"


class Handler(BaseHTTPRequestHandler):
    server_version = "claude-master-relay"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):   # il relay ha il suo log
        pass

    # ---------------------------------------------------------------- risposte
    def _send(self, status, body=b"", ctype="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode())

    def _err(self, status, text):
        self._json(status, {"error": text})

    # ---------------------------------------------------------------- guardie
    def _host_ok(self):
        port = self.server.server_address[1]
        return (self.headers.get("Host") or "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _origin_ok(self):
        o = self.headers.get("Origin")
        port = self.server.server_address[1]
        return o is None or o in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _auth_ok(self, query):
        tok = self.server.token
        h = self.headers.get("Authorization") or ""
        given = h[7:].strip() if h.startswith("Bearer ") else ""
        if not given and self.command == "GET":
            given = (query.get("t") or [""])[0]
        return bool(given) and hmac.compare_digest(given.encode(), tok.encode())

    def _guard(self, query, api=True):
        if not self._host_ok():
            self._err(403, "host"); return False
        if not self._origin_ok():
            self._err(403, "origin"); return False
        if api and not self._auth_ok(query):
            self._err(401, "token"); return False
        return True

    def _body(self, cap):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > cap:
            self._err(413, f"too large: {n} max {cap}"); return None
        return self.rfile.read(n)

    # ---------------------------------------------------------------- metodi
    def do_OPTIONS(self):   # nessun preflight: CORS chiuso
        self._err(405, "method")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        api = self.server.api
        if not u.path.startswith("/api/"):
            if self._guard(q, api=False):
                self._static(u.path)
            return
        if not self._guard(q):
            return
        try:
            if u.path == "/api/state":
                st = api.state()
                return self._json(200, st) if st is not None else self._err(503, "no state yet")
            if u.path == "/api/events":
                try:
                    since = float((q.get("since") or ["0"])[0] or 0)
                except ValueError:
                    return self._err(400, "since")
                return self._json(200, api.events(since))
            if u.path == "/api/stream":
                return self._stream()
            m = re.fullmatch(r"/api/tail/([^/]+)", u.path)
            if m:
                # 1.70: la coda della chat come /tail/<id> su Firebase; ?tx= uguale → 304. Il 404 della sessione ha
                # un corpo suo, cosi' la web lo distingue dal 404 di un relay vecchio che non ha la rotta
                body = api.tail(urllib.parse.unquote(m.group(1)))
                if body is None:
                    return self._err(404, "no such session")
                if (q.get("tx") or [""])[0] == body["tx"]:
                    return self._send(304)
                return self._json(200, body)
            m = re.fullmatch(r"/api/file/([^/]+)", u.path)
            if m:
                return self._file(m.group(1))
        except (BrokenPipeError, ConnectionResetError):
            return
        self._err(404, "not found")

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if not self._guard(q):
            return
        api = self.server.api
        if u.path == "/api/cmd":
            raw = self._body(CMD_BODY_MAX)
            if raw is None:
                return
            try:
                cmd = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._err(400, "json")
            if not isinstance(cmd, dict) or not cmd.get("op"):
                return self._err(400, "cmd")
            return self._json(200, api.cmd(cmd))
        m = re.fullmatch(r"/api/share/([^/]+)", u.path)
        if m:
            sid = m.group(1)
            if not ID_RE.fullmatch(sid):
                return self._err(400, "id")
            raw = self._body(SHARE_BODY_MAX)
            if raw is None:
                return
            try:
                blob = json.loads(raw.decode("utf-8"))
                data = base64.b64decode(str(blob["data"]), validate=True)
                mime = str(blob.get("mime") or "").strip().lower()
            except (ValueError, UnicodeDecodeError, KeyError, TypeError, binascii.Error):
                return self._err(400, "share")
            if not data or not mime:
                return self._err(400, "share")
            api.share(sid, {"mime": mime, "data": data, "name": blob.get("name")})
            return self._json(200, {"id": sid, "ok": True, "size": len(data)})
        self._err(404, "not found")

    # ---------------------------------------------------------------- pezzi
    def _stream(self):
        """SSE: lo stato all'apertura e a ogni cambio (il relay riscrive il file a ogni push), un commento ogni
        STREAM_PING_S perche' Chrome non chiuda la connessione."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        api = self.server.api
        last, ping = None, time.time()
        while not self.server.stopping:
            stamp = api.state_stamp()
            if stamp != last:
                st = api.state()
                if st is not None:
                    self.wfile.write(b"event: state\ndata: " + json.dumps(st, ensure_ascii=False).encode() + b"\n\n")
                    self.wfile.flush()
                    ping = time.time()
                last = stamp
            elif time.time() - ping >= STREAM_PING_S:
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                ping = time.time()
            time.sleep(STREAM_POLL_S)

    def _file(self, fid):
        if not ID_RE.fullmatch(fid):
            return self._err(400, "id")
        f = self.server.api.file(fid)
        if not f:
            return self._err(404, "not found")
        try:
            data = Path(f["path"]).read_bytes()
        except OSError:
            return self._err(404, "unreadable")
        name = urllib.parse.quote(str(f.get("name") or "file"))
        self._send(200, data, str(f.get("mime") or "application/octet-stream"),
                   {"Content-Disposition": f"inline; filename*=UTF-8''{name}"})

    def _static(self, path):
        root = self.server.web_dir
        if not root and self.server.upstream:
            return self._upstream(path)
        if not root or not root.is_dir():
            return self._err(404, "relay.web.dir")
        rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            return self._err(404, "not found")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            if "." in rel.rsplit("/", 1)[-1]:   # un asset che non c'e'; le rotte della web app vanno a index.html
                return self._err(404, "not found")
            target = root / "index.html"
            if not target.is_file():
                return self._err(404, "index.html")
        import mimetypes
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


    def _upstream(self, path):
        """13/10: senza relay.web.dir la web app viene dal sito (relay.web_app_url, la stessa build): ogni file preso
        una volta e tenuto in <relay.dir>/web-cache. Solo gli asset (assets/, nomi con l'impronta della build) restano
        quelli della prima volta; tutto il resto — index.html, sw.js, il manifest — si riprende a ogni richiesta, cosi'
        una build nuova del sito arriva anche al service worker. Se la rete manca, resta quello in cache."""
        rel = urllib.parse.unquote(path).lstrip("/")
        if not rel or rel.endswith("/") or "." not in rel.rsplit("/", 1)[-1]:
            rel = "index.html"   # le rotte della web app
        if ".." in rel.split("/") or not UPSTREAM_PATH_RE.fullmatch(rel):
            return self._err(404, "not found")
        cache = self.server.cache_dir / rel
        if not rel.startswith("assets/") or not cache.is_file():
            try:
                req = urllib.request.Request(self.server.upstream + "/" + urllib.parse.quote(rel), headers={"User-Agent": "supervisor-relay"})
                with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT_S) as r:
                    data = r.read()
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_name(cache.name + ".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, cache)
            except (urllib.error.URLError, OSError, ValueError):
                if not cache.is_file():
                    return self._err(502, "web app not reachable: " + self.server.upstream)
        import mimetypes
        ctype = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self._send(200, cache.read_bytes(), ctype)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, token, web_dir, api, upstream="", cache_dir=None):
        self.token = token
        self.web_dir = Path(web_dir).expanduser().resolve() if web_dir else None
        self.upstream = str(upstream or "").rstrip("/")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.api = api
        self.stopping = False
        super().__init__(("127.0.0.1", int(port)), Handler)

    def stop(self):
        self.stopping = True
        self.shutdown()
        self.server_close()


def start(port, token, web_dir, api, upstream="", cache_dir=None):
    """Il server in un thread demone; ritorna il Server (stop() lo ferma)."""
    srv = Server(port, token, web_dir, api, upstream, cache_dir)
    threading.Thread(target=srv.serve_forever, name="relay-web", daemon=True).start()
    return srv
