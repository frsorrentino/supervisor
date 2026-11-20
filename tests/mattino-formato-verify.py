#!/usr/bin/env python3
"""Il messaggio delle 8 su Telegram in HTML come il recap delle 20 (20/11/2026, supervisor-mattino-formattato-20-11; la
segnalazione del maintainer alle 08:09). Il mattino del 20/11 partiva come testo semplice: i monitoraggi in una riga con gli
URL nudi e «;», le scadenze con «pratica c_…» e i percorsi ~/…, Firebase due volte con numeri diversi (la stima del relay,
2,5 GB su 10, e il monitoraggio, 14,9 GB su 10). Dati finti: nessuna pratica, scadenza o cartella vera.

MF1 recap morning senza --send stampa l'HTML che manda: la prima riga in grassetto col giorno e quante voci sono vicino alla
    soglia, una sezione per blocco col titolo in grassetto (QUOTE, FIREBASE, SERVER, MONITORAGGI, SCADENZE), una voce per riga
MF2 nel testo visibile (tolti i tag) niente URL nudi, niente percorsi ~/, niente «pratica c_»: i link sono <a href> con testo breve
MF3 Firebase una volta sola: col monitoraggio di Firebase c'e' il suo numero (misurato), non la stima del relay; senza
    monitoraggio la stima, detta come stima
MF4 le scadenze: data, quando e cosa; mai l'id della pratica ne' la cartella
MF5 recap morning --send: un messaggio con parse_mode HTML, lo stesso testo che stampa
MF6 & < > scappati, niente emoji, entro 4096 caratteri
"""
import datetime as dt
import html
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
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
(home / ".claude-pixel").mkdir()
state, relay, qdir, ws = tmp / "state", tmp / "relay", tmp / "quota", tmp / "ws"
for d in (state, relay, qdir, ws):
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
servers = tmp / "spazio-server.json"
servers.write_text(json.dumps({"Alpha": {"livello": "alto", "liberi_gb": 51, "uso": 90, "ora": "07:30"},
                               "Bravo": {"livello": "alto", "liberi_gb": 20, "uso": 83, "ora": "07:30"},
                               "Gamma": {"livello": "ok", "liberi_gb": 300, "uso": 40, "ora": "07:30"}}))
today = dt.date.today()
ics = tmp / "cal.ics"
d1, d2 = today + dt.timedelta(days=3), today + dt.timedelta(days=14)
ics.write_text("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
               f"BEGIN:VEVENT\r\nUID:a1\r\nDTSTART;VALUE=DATE:{d1:%Y%m%d}\r\nSUMMARY:Pagamento & rinnovo <finto>\r\nX-STUDIO-CASE:c_000000000001\r\nEND:VEVENT\r\n"
               f"BEGIN:VEVENT\r\nUID:a2\r\nDTSTART;VALUE=DATE:{d2:%Y%m%d}\r\nSUMMARY:Adesione di prova\r\nX-STUDIO-CASE:c_000000000002\r\nEND:VEVENT\r\n"
               "END:VCALENDAR\r\n")
secrets = tmp / "calendar.env"
secrets.write_text(f"CALENDAR_ICS_URL=file://{ics}\n")
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "workspace": {"root": str(ws), "root_session_name": "master", "sessions_scope": "all"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}, "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "default_account": "personale", "quota": {"source": str(qdir)},
    "bot": {"api_base": f"http://127.0.0.1:{srv.server_port}", "token_file": str(tg / ".env"), "access_file": str(tg / "access.json")},
    "relay": {"dir": str(relay), "web_app_url": APP},
    "recap": {"cron_time": "20:00", "summary": "last", "servers_file": str(servers)},
    "calendar": {"url_file": str(secrets), "remind_days": 30, "cases": {"c_000000000001": "~/ws/pratiche/prova-uno", "c_000000000002": "~/ws/pratiche/prova-due"}},
    "tile": {"chrome_bridge_cli": ""}}))
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE")}
ENV.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASKS_DB": str(tmp / "t.db"), "CM_RELAY_NO_PUSH": "1",
            "CM_MONITORS": str(tmp / "monitors")})
CLI = str(T.SCRIPTS / "supervisor")
now = time.time()


def qfile(acc_dir, **kw):
    h = hashlib.sha256(str(acc_dir).encode()).hexdigest()[:8]
    (qdir / f"quota-{h}.json").write_text(json.dumps(kw))


reset_w = time.mktime((today + dt.timedelta(days=2)).timetuple()) + 3 * 3600
qfile(home / ".claude", five_hour_used_pct=5, five_hour_resets_at=now + 3600, weekly_used_pct=92, weekly_resets_at=reset_w)
qfile(home / ".claude-pixel", five_hour_used_pct=1, five_hour_resets_at=now + 3600, weekly_used_pct=40, weekly_resets_at=reset_w + 86400)
first = today.replace(day=1)
(relay / "usage-firebase.json").write_text(json.dumps({first.isoformat(): {"result_bytes": 1_500_000_000, "state_bytes": 0}}))   # la stima: 1,5 GB


def mon(*a):
    return subprocess.run([CLI, "monitor", *a], capture_output=True, text=True, env=ENV, timeout=60)


mon("add", "firebase-consumo", "--title", "Consumo Firebase", "--form", "tile", "--unit", "GB")
mon("alert", "firebase-consumo", "--above", "10")
mon("point", "firebase-consumo", "14.9")
mon("verdict", "firebase-consumo", "Dati conservati 2 MB su 1 GB (0%); scaricati a novembre 14,9 GB su 10 (149%), di questo passo 19 GB a fine mese; "
    "connessioni insieme al massimo 4 su 100.")
mon("add", "pubblicazioni", "--title", "Pubblicazioni in revisione", "--form", "tile")
mon("alert", "pubblicazioni", "--above", "0")
mon("point", "pubblicazioni", "2")
mon("verdict", "pubblicazioni", "Ancora in revisione: App di prova su Play (dal 27/10), plugin di prova nella directory (dal 13/10).")
mon("add", "tranquillo", "--title", "Tutto bene", "--form", "tile")
mon("point", "tranquillo", "1")


def recap(*a):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-recap.py"), *a], capture_output=True, text=True, env=ENV, timeout=120)


r = recap("morning")
out = r.stdout.strip()
lines = out.splitlines()
vis = html.unescape(re.sub(r"<[^>]+>", "", out))   # il testo che si vede: tolti i tag, le entita' tornano caratteri
DAYS = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
T.check("MF1 recap morning without --send prints the HTML it sends: bold first line with the day and how many items are near the threshold, "
        "one section per block with its bold title, one item per line",
        r.returncode == 0 and lines[:1] and lines[0].startswith(f"<b>{DAYS[today.weekday()]} {today:%d/%m} · ") and lines[0].endswith("</b>")
        and all(f"<b>{s}</b>" in out for s in ("QUOTE", "FIREBASE", "SERVER", "MONITORAGGI", "SCADENZE"))
        and "• personale: settimana 92%" in vis and "• Alpha 90% (51 GB liberi)" in vis and "• Bravo 83% (20 GB liberi)" in vis,
        r.stderr[-300:] + out)
T.check("MF2 in the visible text no bare URL, no ~/ path, no «pratica c_»: the links are <a href> with a short text",
        "https://" not in vis and "~/" not in vis and "pratica c_" not in vis and f'<a href="{APP}/#/monitor/pubblicazioni">' in out
        and f'<a href="{APP}/#/monitor/firebase-consumo">' in out, vis)
fb = out.split("<b>FIREBASE</b>", 1)[-1].split("<b>", 1)[0]
T.check("MF3 Firebase once: with Firebase's own monitor its number (measured), not the relay's estimate; «scaricat» once",
        "14,9 GB su 10" in fb and "1,5 GB" not in out and vis.lower().count("scaricat") == 1 and "Consumo Firebase" not in out.split("<b>MONITORAGGI</b>", 1)[-1],
        fb + "\n---\n" + out)
dl = vis.split("SCADENZE", 1)[-1]
T.check("MF4 the deadlines: date, when and what; never the case id nor the folder",
        f"• {d1:%d/%m} (tra 3 giorni) Pagamento & rinnovo <finto>" in dl and f"• {d2:%d/%m} (tra 14 giorni) Adesione di prova" in dl
        and "c_0000" not in out and "pratiche/prova" not in out, dl)
n0 = len(SENT)
r2 = recap("morning", "--send")
sent = SENT[n0:]
T.check("MF5 recap morning --send: one message with parse_mode HTML, the same text it prints",
        r2.returncode == 0 and len(sent) == 1 and sent[0].get("parse_mode") == "HTML" and sent[0].get("text", "").strip() == out, str(sent)[:400])
T.check("MF6 & < > escaped, no emoji, within 4096 characters",
        "Pagamento &amp; rinnovo &lt;finto&gt;" in out and not EMOJI.search(out) and len(out) <= 4096, out[-300:])
# senza il monitoraggio di Firebase: la stima del relay, detta come stima
mon("remove", "firebase-consumo")
r3 = recap("morning")
fb3 = r3.stdout.split("<b>FIREBASE</b>", 1)[-1].split("<b>", 1)[0]
T.check("MF3 without Firebase's monitor: the relay's estimate, said as an estimate",
        "1,5 GB" in fb3 and "stima" in fb3.lower(), r3.stdout[-600:] + r3.stderr[-200:])
srv.shutdown()
T.finish()
