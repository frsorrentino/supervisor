#!/usr/bin/env python3
"""Il recap delle 20 su Telegram leggibile, e le menzioni GitHub in una riga italiana (19/11/2026, supervisor-recap-telegram-19-11;
formato approvato dal maintainer alle 20:10). Caso vero: il recap del 19/11 (27 uscite, 13 voci) era un solo paragrafo coi titoli
tagliati a 90 caratteri a meta' parola («… eventi Conversio,», «… leggibili nel,», «reopen ripiega su ,») e «negozio-prova.it:»
ripetuto a ogni voce; la menzione di HOL Guard delle 13:50 era il corpo inglese della PR tagliato.

RT1 un messaggio in HTML; la prima riga in grassetto «Giovedì 19/11 · 7 ti aspettano · N fatti» (giorno della settimana per esteso)
RT2 TI ASPETTANO: al massimo 5 voci «progetto: cosa, cosa serve» col link della voce, poi «… altre 2 nell'app» col link all'app
RT3 FATTI OGGI in <blockquote expandable>, una riga per progetto, il progetto una volta sola, i titoli senza il prefisso del
    progetto, task.short quando c'e', mai una parola tagliata ne' una virgola in coda
RT4 il testo e' HTML valido per Telegram (& < > scappati), senza emoji, entro 4096 caratteri
RT5 le voci di `recap add` del giorno restano, col nome del tipo; Stanotte resta
RT6 `supervisor recap github` sulla menzione vera delle 13:50: «chrome-bridge: wondermuse ha aperto una PR su HOL Guard (profilo di
    sicurezza per chrome-bridge) — <link>»; --html mette il link come «link alla PR»
RT7 senza modello (claude -p che fallisce) la riga usa il titolo della PR fra «»; un [COMMENTO DA UTENTE] diventa «repo: chi ha
    commentato l'issue #n — link»
"""
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿⏩-⏺️]")
APP = "https://app.example"
tmp = Path(T.tmpdir())
home = tmp / "home"
tg = home / ".claude" / "channels" / "telegram"
tg.mkdir(parents=True)
(tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
(tg / "access.json").write_text(json.dumps({"allowFrom": ["1001"]}))
state, relay, ws = tmp / "state", tmp / "relay", tmp / "ws"
for d in (state, relay, ws):
    d.mkdir()
SENT = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        params = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        if self.path.endswith("/sendMessage"):
            SENT.append(params)
        out = json.dumps({"ok": True, "result": {"message_id": len(SENT)}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


srv = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
fake_claude = tmp / "fake-claude"
fake_claude.write_text("#!/bin/sh\nif [ -n \"$FAKE_FAIL\" ]; then exit 1; fi\necho 'profilo di sicurezza per chrome-bridge'\n")
fake_claude.chmod(0o755)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "workspace": {"root": str(ws), "root_session_name": "master", "sessions_scope": "all"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}}, "default_account": "personale",
    "bot": {"api_base": f"http://127.0.0.1:{srv.server_port}", "token_file": str(tg / ".env"), "access_file": str(tg / "access.json")},
    "relay": {"dir": str(relay), "web_app_url": APP},
    "night": {"queue_file": str(state / "night-queue.jsonl")},
    "recap": {"cron_time": "20:00", "summary": "last"},
    "tile": {"chrome_bridge_cli": ""}}))
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE")}
# DATA-RT-begin — tests/data-mezzanotte-verify.py esegue questo tratto con l'orologio a mezzanotte e a fine mese
# 24/11 (supervisor-test-oggi-24-11): il giorno si legge una volta e va agli script come CM_TODAY: recap add e il recap delle 20 restano in quel
# giorno anche se fra le due letture scatta la mezzanotte o finisce il mese
today = dt.date.today()
TODAY_ENV = {"CM_TODAY": today.isoformat()}
# DATA-RT-end
ENV.update({**TODAY_ENV, "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASKS_DB": str(tmp / "t.db"), "CM_RELAY_NO_PUSH": "1",
            "CM_CLAUDE_BIN": str(fake_claude), "CM_DIARY_FAKE_PROC": "1"})
CLI = str(T.SCRIPTS / "supervisor")


def task(*a, stdin=None):
    return subprocess.run([CLI, "task", *a], input=stdin, capture_output=True, text=True, env=ENV, timeout=120)


def add(tid, title, project, session, short=None):
    (ws / project).mkdir(exist_ok=True)
    t = {"id": tid, "title": title, "plan": "p", "where": {"project": str(ws / project), "session": session, "host": "local", "account": None},
         "check": {"cmd": "true", "cwd": str(ws), "timeout_s": 30}, "perimeter": ["x"], "lane": "open", "depends_on": [],
         "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
         "outcome": {"expected": "x", "kind": "custom", "proof": "x"}}
    if short:
        t["short"] = short
    r = task("add", "-", stdin=json.dumps({"schema_version": 1, "task": t}))
    assert r.returncode == 0, r.stderr
    task("start", tid)


# i titoli veri del recap del 19/11 (col prefisso del progetto, come le sessioni li scrivono)
DONE = [
    ("g1", "negozio-prova.it: testi SEO del cliente in 9 categorie, titolo e descrizione leggibili nel menu del negozio", "negozio-prova.it", None),
    ("g2", "negozio-prova.it: patch ai moduli di terzi elencate con diff, controllo giornaliero su GitHub", "negozio-prova.it", None),
    ("g3", "negozio-prova.it: testi SEO delle 9 categorie in inglese e verifica degli eventi Conversion API", "negozio-prova.it", None),
    ("s1", "Un tocco dall'app lancia la master anche quando non è nella fotografia: reopen ripiega sulla radice", "supervisor", None),
    ("s2", "Un prompt scritto mentre la sessione lavora (messo in coda) arriva comunque nella casella", "supervisor", "Prompt in coda"),
    ("j1", "Joy: in home il banner Summer Sale diventa Autunno Inverno (immagine già nei media del sito)", "joy", None),
    ("w1", "wesud: page_view GA4 dopo «Accetta» del banner (sessioni senza pagina di destinazione)", "wesud", None),
    ("p1", "pixelgramps 1.3.0: campo «Post» & <nascondi> su Joy", "pixelgramps", None),
]
for tid, title, proj, short in DONE:
    add(tid, title, proj, proj, short)
    assert task("done", tid).returncode == 0
WAIT = [("a1", "Albo Chiaro fase 3", "albo-chiaro"), ("a2", "Albo Chiaro fase 2", "albo-chiaro"), ("a3", "Albo Chiaro, Regione", "albo-chiaro"),
        ("a4", "Albo Chiaro fase 1", "albo-chiaro"), ("a5", "Albo Chiaro, Provincia", "albo-chiaro"), ("r1", "README alla 1.61", "supervisor"),
        ("r2", "Pulizia nomi dal repo", "supervisor")]
for tid, title, proj in WAIT:
    add(tid, title, proj, proj)
    task("wait-ok", tid, "--consiglio", "va bene", "--what", title, "--where", "repo")
# la Prima raccolta di un'installazione nuova (una voce senza sessione) e' rimandata di un giorno: i conteggi sono quelli dei compiti
code = ("import importlib.util,time\n"
        f"spec=importlib.util.spec_from_file_location('rc',{str(T.SCRIPTS / 'cm-raccolta.py')!r})\n"
        "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "m.save({'consent': None, 'ask_after': int(time.time()) + 86400})\n")
rs = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=ENV, timeout=60)
assert rs.returncode == 0, rs.stderr
(state / "night-queue.jsonl").write_text(json.dumps({"id": "n2", "dir": str(ws / "beta"), "prompt": "rivedi i test", "account": "personale"}) + "\n")
subprocess.run([sys.executable, str(T.SCRIPTS / "cm-recap.py"), "add", "--kind", "rilasci", "Claude Code 2.1.328: hook più veloci"],
               capture_output=True, text=True, env=ENV, timeout=60)
(state / "ledger.jsonl").write_text("")

r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-recap.py"), "--send"], capture_output=True, text=True, env=ENV, timeout=180)
msg = SENT[-1] if SENT else {}
txt = msg.get("text", "")
lines = txt.splitlines()
DAYS = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
head = f"<b>{DAYS[today.weekday()]} {today.strftime('%d/%m')} · 7 ti aspettano · {len(DONE)} fatti</b>"
T.check("RT1 one HTML message; the first line in bold «Giovedì 19/11 · 7 ti aspettano · N fatti»",
        r.returncode == 0 and len(SENT) == 1 and msg.get("parse_mode") == "HTML" and lines[:1] == [head],
        f"rc {r.returncode} {r.stderr[-300:]} {msg.get('parse_mode')}\n{txt}")

wait_part = txt.split("<b>TI ASPETTANO</b>", 1)[1].split("<b>FATTI OGGI</b>", 1)[0] if "<b>TI ASPETTANO</b>" in txt and "<b>FATTI OGGI</b>" in txt else ""
items = [l for l in wait_part.splitlines() if l.startswith("• ")]
T.check("RT2 TI ASPETTANO: at most 5 items «progetto: cosa, cosa serve» with the item's link, then «… altre 2 nell'app» with the app's link",
        len(items) == 5 and all(re.match(r"^• [\w.-]+: .+, .+ <a href=\"" + re.escape(APP) + r"/#/attesa/w[0-9a-f]+\">", l) for l in items)
        and items[0].startswith("• albo-chiaro: Albo Chiaro fase 3, ") and f"… altre 2 nell'app" in wait_part and f'<a href="{APP}' in wait_part.split("altre 2")[1],
        wait_part)

m = re.search(r"<blockquote expandable>(.*?)</blockquote>", txt, re.S)
quote = m.group(1) if m else ""
qlines = [l for l in quote.splitlines() if l.strip()]
by_proj = {}
for l in qlines:
    p, _, rest = l.partition(": ")
    by_proj[html.unescape(p)] = [html.unescape(x) for x in rest.split(" · ")]
titles = {proj: [] for _, _, proj, _ in DONE}
for _, title, proj, short in DONE:
    titles[proj].append((title, short))


def fair(item, title, short):
    if short:
        return item == short
    t = re.sub(r"^[\w.-]+:\s+", "", title)
    return t.startswith(item) and (len(item) == len(t) or t[len(item)] == " ") and not item.endswith((",", ":", "("))


ok3 = bool(m) and sorted(by_proj) == sorted(titles) and quote.count("negozio-prova.it") == 1 \
    and all(len(by_proj[p]) == len(titles[p]) and all(fair(i, t, s) for i, (t, s) in zip(by_proj[p], titles[p])) for p in titles)
T.check("RT3 FATTI OGGI in <blockquote expandable>, one line per project, the project once, titles without its prefix, task.short when there, "
        "never a cut word nor a trailing comma", ok3, quote[:900])

bare = re.sub(r"<(/?)(b|a|blockquote)( [^>]*)?>", "", txt)
T.check("RT4 valid Telegram HTML (& < > escaped), no emoji, within 4096 characters",
        "&amp;" in txt and "&lt;nascondi&gt;" in txt and "<" not in bare and ">" not in bare and not EMOJI.search(txt) and len(txt) <= 4096,
        [x for x in re.findall(r"<[^>]*>", bare)][:5])
T.check("RT5 today's `recap add` entries stay with their kind's name; Stanotte stays",
        "Rilasci di Claude Code: Claude Code 2.1.328: hook più veloci" in txt and "Stanotte: 1 lavoro in coda (beta)" in txt, txt[-400:])

MENTION = ("[MENZIONE] hashgraph-online/hol-guard PullRequest «Add Chrome Bridge MCP server profile» 11-19T12:45\n"
           "  wondermuse https://github.com/hashgraph-online/hol-guard/pull/8079 Adds a Guard profile for [Chrome Bridge](https://github.com/frsorrentino/chrome-bridge), "
           "which exposes the user's real, logged-in Chrome as an MCP server. The profile covers the 46 tools in the live inventory I captured from chrome-bridge-mcp 1.32.5.")
URL = "https://github.com/hashgraph-online/hol-guard/pull/8079"


def gh(*a, extra=None, inp=MENTION):
    return subprocess.run([CLI, "recap", "github", *a], input=inp, capture_output=True, text=True, env=dict(ENV, **(extra or {})), timeout=120)


g1, g2 = gh(), gh("--html")
T.check("RT6 `supervisor recap github` on the real mention: one Italian line with the link; --html puts «link alla PR»",
        g1.stdout.strip() == f"chrome-bridge: wondermuse ha aperto una PR su HOL Guard (profilo di sicurezza per chrome-bridge) — {URL}"
        and g2.stdout.strip() == f'chrome-bridge: wondermuse ha aperto una PR su HOL Guard (profilo di sicurezza per chrome-bridge) — <a href="{URL}">link alla PR</a>',
        g1.stdout + g1.stderr[-300:] + "\n" + g2.stdout)
g3 = gh(extra={"FAKE_FAIL": "1"})
COMMENT = ("[COMMENTO DA UTENTE] frsorrentino/supervisor octocat 11-19T18:02\n  https://github.com/frsorrentino/supervisor/issues/12#issuecomment-99\n"
           "  Grazie, funziona anche su macOS")
g4 = gh(inp=COMMENT, extra={"FAKE_FAIL": "1"})
T.check("RT7 without a model the line uses the PR's title in «»; a [COMMENTO DA UTENTE] becomes «repo: who commented on issue #n — link»",
        g3.stdout.strip() == f"chrome-bridge: wondermuse ha aperto una PR su HOL Guard («Add Chrome Bridge MCP server profile») — {URL}"
        and g4.stdout.strip() == "supervisor: octocat ha commentato l'issue #12 — https://github.com/frsorrentino/supervisor/issues/12#issuecomment-99",
        g3.stdout + g3.stderr[-200:] + "\n" + g4.stdout + g4.stderr[-200:])
srv.shutdown()
T.finish()
