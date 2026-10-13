"""Harness dei test di supervisor.

Nessun framework: ogni test e' uno script che stampa OK/FAIL per caso ed esce
con codice diverso da zero se uno fallisce (convenzione di fable-director).

Fornisce:
- check(name, ok, detail): registra e stampa;
- finish(): riepilogo + exit;
- fake_home(): una HOME finta con la forma di questa macchina (due account,
  workspace, script legacy) per i test di init/doctor, senza toccare la vera;
- fake_machine(): il file JSON che cm-config.py legge al posto di /proc, PATH e
  variabili d'ambiente quando CM_FAKE_MACHINE e' impostata;
- run_config(): invoca cm-config.py con HOME e config isolate;
- PrivateTmux: un server tmux su socket privato (-L), ucciso a fine test.
  Nessun test tocca il server tmux vero: le sessioni di lavoro non si toccano.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN = ROOT / "supervisor"
SCRIPTS = PLUGIN / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"

FAILS = []
COUNT = 0


def check(name, ok, detail=""):
    global COUNT
    COUNT += 1
    print(f"  {'OK ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def finish():
    print(f"\n{COUNT - len(FAILS)}/{COUNT} OK" + (f", FAIL: {', '.join(FAILS)}" if FAILS else ""))
    sys.exit(1 if FAILS else 0)


def fake_home(tmp, legacy=True, second_account=True, workspace=True, language="Italiano"):
    """HOME finta con la forma di questa macchina (08/09/2026).

    legacy=True: skill nuova-sessione, registro sessioni, registro colori,
    wrapper claude-pixel nel .bashrc, hook ora locale nei settings — cioe' lo
    stato «prima del plugin» che init deve riconoscere.
    """
    home = Path(tmp) / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}")
    (home / ".claude" / "settings.json").write_text(json.dumps({
        **({"language": language} if language else {}),
        "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
                  "command": "date '+%A %d/%m/%Y %H:%M' | sed 's/^/[ora locale] /'"}]}]},
        "mcpServers": {"chrome-bridge": {"command": "node",
                       "args": [str(home / "cb" / "server" / "index.js")]}},
    }))
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"chrome-bridge": {
        "type": "stdio", "command": "node", "args": [str(home / "cb" / "server" / "index.js")]}}}))
    (home / "cb" / "server").mkdir(parents=True)
    (home / "cb" / "server" / "index.js").write_text("")
    (home / "cb" / "server" / "cli.js").write_text("")
    if second_account:
        (home / ".claude-pixel").mkdir()
        (home / ".claude-pixel" / ".credentials.json").write_text("{}")
        (home / ".claude-pixel" / "settings.json").write_text(json.dumps({
            "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
                      "command": "date '+%A %d/%m/%Y %H:%M' | sed 's/^/[ora locale] /'"}]}]}}))
    if workspace:
        ws = home / "Desktop" / "workspaces"
        for d in ("personali/alfa", "personali/beta", "agenzia/clienti/sito.com",
                  "agenzia/nostri/hub", "_archivio", "_prod_backups", ".claude"):
            (ws / d).mkdir(parents=True)
    if legacy:
        sk = home / ".claude" / "skills" / "nuova-sessione"
        sk.mkdir(parents=True)
        (sk / "lancia.sh").write_text("#!/bin/sh\n")
        (home / ".claude" / "sessioni-vive.json").write_text(json.dumps({
            "salvato": "2026-09-08T22:00:00+02:00",
            "sessioni": [
                {"nome": "master", "cartella": str(home / "Desktop/workspaces"), "account": "personale"},
                {"nome": "alfa", "cartella": str(home / "Desktop/workspaces/personali/alfa"), "account": "personale"},
                {"nome": "pix-sito-com", "cartella": str(home / "Desktop/workspaces/agenzia/clienti/sito.com"), "account": "aziendale"},
                {"nome": "pix-hub", "cartella": str(home / "Desktop/workspaces/agenzia/nostri/hub"), "account": "aziendale"},
            ]}))
        (home / ".claude" / "colori-sessione").write_text("master\t2\nalfa\t0\npix-sito-com\t3\n")
        (home / ".bashrc").write_text(
            'claude() { command claude "$@"; }\n'
            '# >>> claude-pixel >>>\n'
            'claude-pixel() {\n'
            '  CLAUDE_CONFIG_DIR="$HOME/.claude-pixel" command claude "$@"\n'
            '}\n')
        (home / ".tmux.conf").write_text(
            'bind a run-shell -b "$HOME/.local/bin/affianca"\n'
            'bind u run-shell -b "$HOME/.local/bin/unisci"\n'
            'bind -r Left run-shell -b "$HOME/.local/bin/sposta sinistra"\n')
    return home


def fake_machine(tmp, home, chromeos=True, procs=None, which=None, env=None):
    """Descrizione della macchina finta letta da cm-config.py (CM_FAKE_MACHINE)."""
    m = {
        "which": which if which is not None else ["tmux", "claude", "python3", "bash", "gnome-terminal"],
        "exists": [ "/opt/google/cros-containers/bin/garcon" ] if chromeos else [],
        "env": env or {"LANG": "en_US.UTF-8"},
        "procs": procs if procs is not None else [
            {"pid": 100, "cmdline": "claude --dangerously-skip-permissions --remote-control master -n master",
             "environ": {}, "cwd": str(home / "Desktop/workspaces"), "tmux": "master"},
            {"pid": 101, "cmdline": "claude --dangerously-skip-permissions",
             "environ": {}, "cwd": str(home / "Desktop/workspaces/personali/alfa"), "tmux": "alfa"},
            {"pid": 102, "cmdline": "claude --dangerously-skip-permissions",
             "environ": {"CLAUDE_CONFIG_DIR": str(home / ".claude-pixel")},
             "cwd": str(home / "Desktop/workspaces/agenzia/clienti/sito.com"), "tmux": "pix-sito-com"},
        ],
    }
    p = Path(tmp) / "machine.json"
    p.write_text(json.dumps(m))
    return p


def run_config(args, home, config=None, machine=None, extra_env=None, stdin=None):
    env = {k: v for k, v in os.environ.items() if k in ("PATH",)}
    env["HOME"] = str(home)
    env["CM_HOME"] = str(home)
    if config is not None:
        env["CC_SUPERVISOR_CONFIG"] = str(config)
    if machine is not None:
        env["CM_FAKE_MACHINE"] = str(machine)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(SCRIPTS / "cm-config.py")] + list(args),
                          capture_output=True, text=True, env=env, input=stdin, timeout=60)


class PrivateTmux:
    """Server tmux su socket privato. Con `with`, muore a fine test."""

    def __init__(self, name=None):
        self.socket = name or f"cm-test-{os.getpid()}"

    def __enter__(self):
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        return self

    def __exit__(self, *exc):
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        # gli aiutanti pty del bridge/garcon finto (`tmux -L <socket> attach`) possono sopravvivere al
        # server: 34 trovati l'11/09/2026 dopo una suite uccisa a meta'. Il nome del socket e' nel loro argv.
        subprocess.run(["pkill", "-9", "-f", f"tmux.*{self.socket}"], capture_output=True)

    def __call__(self, *args, **kw):
        return subprocess.run(["tmux", "-L", self.socket] + list(args),
                              capture_output=True, text=True, **kw)

    @property
    def env(self):
        return {"CM_TMUX_ARGS": f"-L {self.socket}"}


def tmpdir():
    d = tempfile.mkdtemp(prefix="cm-test-")
    return d


def rm(d):
    shutil.rmtree(d, ignore_errors=True)


def fake_telegram(token="123:ABC"):
    """Un Telegram finto su HTTP locale: `sendMessage` registra i parametri in calls["sendMessage"],
    `getUpdates` serve la coda `queue`. Ritorna (api_base, calls, queue)."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs
    calls, queue = {"getUpdates": [], "sendMessage": []}, []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            params = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
            method = self.path.rsplit("/", 1)[-1]
            if not self.path.startswith(f"/bot{token}/"):
                self.send_response(401); self.end_headers(); return
            # rete «caduta» a comando (test del long polling): con il file FAKE_TG_FAIL risponde 503
            fail = os.environ.get("FAKE_TG_FAIL", "")
            if fail and os.path.exists(fail):
                calls.setdefault("failed", []).append(method)
                self.send_response(503); self.end_headers(); return
            if method == "sendMessage" and "intent://" in (params.get("reply_markup") or ""):
                calls.setdefault("rejected", []).append(params)
                out = json.dumps({"ok": False, "error_code": 400, "description": "Bad Request: BUTTON_URL_INVALID"}).encode()
                self.send_response(400); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
                return
            calls.setdefault(method, []).append(params)
            if method == "getUpdates":
                off = int(params.get("offset") or 0)
                res = [u for u in queue if u["update_id"] >= off]
                # long polling emulato: senza update tiene la connessione fino a `timeout` (max 2 s)
                hold = min(float(params.get("timeout") or 0), 2.0)
                if not res and hold > 0:
                    import time as _t
                    end = _t.time() + hold
                    while _t.time() < end and not res:
                        _t.sleep(0.1)
                        res = [u for u in queue if u["update_id"] >= off]
                body = {"ok": True, "result": res}
            elif method == "sendMessage":
                # message_id progressivo: il bot edita il messaggio vivo per id
                body = {"ok": True, "result": {"message_id": len(calls["sendMessage"])}}
            elif method in ("editMessageText", "answerCallbackQuery", "setMyCommands"):
                body = {"ok": True, "result": True}
            else:
                body = {"ok": False}
            out = json.dumps(body).encode()
            try:
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
            except (BrokenPipeError, ConnectionResetError):
                pass   # il client (un daemon ucciso a meta' richiesta) se n'e' andato

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}", calls, queue


def fake_rtdb(project="fake-project"):
    """Un Firebase finto su HTTP locale per cm-relay: RTDB REST (GET/PUT/PATCH/DELETE su /<path>.json,
    ?print=silent, ?shallow=true, stream SSE con Accept: text/event-stream: `put` iniziale poi un `put`/`patch`
    per ogni scrittura sotto il nodo, keep-alive ogni 2 s), il token endpoint (POST /token) e FCM HTTP v1
    (POST /v1/projects/<p>/messages:send). Ritorna (base_url, calls, store): `store` e' il dict vivo del DB,
    `calls["token"]` i JWT ricevuti, `calls["fcm"]` i messaggi, `calls["failed"]` le richieste rifiutate con 503
    quando esiste il file FAKE_RTDB_FAIL."""
    import queue
    import threading
    import time as _t
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse
    store = {}
    calls = {"token": [], "fcm": [], "failed": [], "requests": []}
    streams = []   # (prefix, queue)
    lock = threading.Lock()

    def parts(path):
        return [x for x in path.strip("/").split("/") if x]

    def get_node(path):
        node = store
        for k in parts(path):
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node

    def set_node(path, value):
        ps = parts(path)
        if not ps:
            store.clear()
            if isinstance(value, dict):
                store.update(value)
            return
        node = store
        for k in ps[:-1]:
            if not isinstance(node.get(k), dict):
                node[k] = {}
            node = node[k]
        if value is None:
            node.pop(ps[-1], None)
        else:
            node[ps[-1]] = value

    def notify(path, kind, data):
        pp = parts(path)
        for prefix, q in list(streams):
            pf = parts(prefix)
            if pp[:len(pf)] == pf:
                rel = "/" + "/".join(pp[len(pf):])
                q.put((kind, rel, data))

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _json(self, code, body):
            out = json.dumps(body).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            try:
                self.wfile.write(out)
            except BrokenPipeError:   # il client ha chiuso a meta' (un pair interrotto): non e' un errore del finto
                pass

        def _fail(self):
            f = os.environ.get("FAKE_RTDB_FAIL", "")
            if f and os.path.exists(f):
                calls["failed"].append(self.command + " " + self.path)
                self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers()
                return True
            return False

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n).decode() if n else ""

        def do_POST(self):
            if self._fail():
                return
            u = urlparse(self.path)
            body = self._body()
            calls["requests"].append(("POST", u.path))
            if u.path == "/token":
                q = parse_qs(body)
                calls["token"].append(q.get("assertion", [""])[0])
                return self._json(200, {"access_token": "fake-token", "expires_in": 3600, "token_type": "Bearer"})
            if u.path.startswith("/v1/projects/") and u.path.endswith("/messages:send"):
                try:
                    calls["fcm"].append(json.loads(body))
                except ValueError:
                    return self._json(400, {"error": "bad json"})
                return self._json(200, {"name": f"projects/{project}/messages/{len(calls['fcm'])}"})
            if u.path.endswith(".json"):   # push con nome generato
                key = f"-K{len(calls['requests']):06d}"
                with lock:
                    set_node(u.path[:-5] + "/" + key, json.loads(body))
                    notify(u.path[:-5] + "/" + key, "put", json.loads(body))
                return self._json(200, {"name": key})
            self._json(404, {"error": "not found"})

        def do_GET(self):
            if self._fail():
                return
            u = urlparse(self.path)
            calls["requests"].append(("GET", u.path))
            if not u.path.endswith(".json"):
                return self._json(404, {"error": "not found"})
            path = u.path[:-5]
            if "text/event-stream" in (self.headers.get("Accept") or ""):
                q = queue.Queue()
                with lock:
                    streams.append((path.strip("/"), q))
                    data = get_node(path)
                self.send_response(200); self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache"); self.end_headers()
                try:
                    self.wfile.write(("event: put\ndata: " + json.dumps({"path": "/", "data": data}) + "\n\n").encode()); self.wfile.flush()
                    last = _t.time()
                    while True:
                        try:
                            kind, rel, d = q.get(timeout=0.1)
                            self.wfile.write((f"event: {kind}\ndata: " + json.dumps({"path": rel, "data": d}) + "\n\n").encode()); self.wfile.flush()
                        except queue.Empty:
                            f = os.environ.get("FAKE_RTDB_FAIL", "")
                            if f and os.path.exists(f):
                                break   # rete caduta: la connessione si chiude come dal vivo
                            if _t.time() - last > 2:
                                self.wfile.write(b"event: keep-alive\ndata: null\n\n"); self.wfile.flush(); last = _t.time()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with lock:
                        streams[:] = [s for s in streams if s[1] is not q]
                return
            with lock:
                node = get_node(path)
            qs = parse_qs(u.query)
            if qs.get("shallow", [""])[0] == "true" and isinstance(node, dict):
                node = {k: True for k in node}
            self._json(200, node)

        def do_PUT(self):
            self._write("put")

        def do_PATCH(self):
            self._write("patch")

        def do_DELETE(self):
            if self._fail():
                return
            u = urlparse(self.path)
            calls["requests"].append(("DELETE", u.path))
            with lock:
                set_node(u.path[:-5], None)
                notify(u.path[:-5], "put", None)
            self._json(200, None)

        def _write(self, kind):
            if self._fail():
                return
            u = urlparse(self.path)
            calls["requests"].append((kind.upper(), u.path))
            try:
                value = json.loads(self._body() or "null")
            except ValueError:
                return self._json(400, {"error": "bad json"})
            path = u.path[:-5]
            with lock:
                if kind == "patch":
                    node = get_node(path)
                    if not isinstance(node, dict):
                        set_node(path, {})
                    for k, v in (value or {}).items():
                        set_node(path + "/" + k, v)
                else:
                    set_node(path, value)
                notify(path, kind, value)
            silent = "print=silent" in u.query
            if silent:
                self.send_response(204); self.send_header("Content-Length", "0"); self.end_headers()
            else:
                self._json(200, value)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}", calls, store


def wait_until(pred, timeout=5.0, step=0.2):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()
