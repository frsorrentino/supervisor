#!/usr/bin/env python3
"""Avvisi Telegram: tre tipi di messaggio e nient'altro (17/11/2026, supervisor-avvisi-telegram-17-11).

Il 17/11 fino alle 12:14 sono arrivati nove «stato fermo da 3 min» e «il telefono non si aggiorna», che la guardia
risolveva da sola; il resoconto della notte arrivava a parte alle 02:00; il recap delle 20 era il diario per progetto,
con le emoji. La proposta scelta: un allarme solo quando serve un'azione (relay fermo o push fallite da oltre 15
minuti, quota sopra il 92%), due righe col link all'app; il mattino alle 8 (quote con l'ora di azzeramento, Firebase
del mese, spazio dei server, monitoraggi); il recap alle 20 (uscito, ti aspetta col link, stanotte, la notte e le voci
di `supervisor recap add`). Italiano, niente emoji, la prima riga dice cosa e' successo.
Telegram finto (server HTTP locale), registro temporaneo, relay e guardia caricati come moduli o lanciati.

AG1 guard del relay: stato fermo da 5 minuti, la guardia prova la push ma Telegram tace; a 16 minuti UN allarme di due
    righe (cosa e dove, cosa fare col link all'app), senza emoji; un giro dopo non lo ripete
AG2 guard delle push: tre push fallite in un minuto non vanno su Telegram; fallite da 16 minuti si', una volta
AG3 guard della quota: al 93% (soglia 92) un allarme di due righe col link; i compiti fermi vanno alla master, non a
    Telegram
AR1 recap add --kind K "testo": la voce entra nel recap del suo giorno, non in quello di un altro; un kind non valido no
AR2 recap --send alle 20: un solo messaggio; prima riga «Oggi, …» con quante uscite e quante voci aspettano; Uscito (il
    compito chiuso oggi), Ti aspetta col link della voce, Stanotte (la coda), Notte (il giro di stanotte), le voci di
    recap add con il nome del tipo; niente emoji
AR3 recap install: la riga delle 20 e quella del mattino alle 8
AM1 recap morning --send: «Mattino, …»; la quota dei due account con l'ora di azzeramento, Firebase del mese in GB,
    solo i server sopra soglia, il monitoraggio sopra soglia; niente emoji
"""
import datetime as dt
import hashlib
import importlib.util
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
        out = json.dumps({"ok": True, "result": {"message_id": len(SENT)}}).encode()   # come Telegram: senza message_id l invio conta come fallito
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


srv = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
cron = tmp / "crontab"
cron.write_text("")
fake_crontab = tmp / "crontab.sh"
fake_crontab.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (cron, cron, cron, cron))
fake_crontab.chmod(0o755)
argslog, stall_file = tmp / "cm-args.log", tmp / "stalled.json"
fake_cm = tmp / "supervisor"
fake_cm.write_text(f'#!/bin/sh\nif [ "$1 $2" = "task stall-check" ]; then cat "{stall_file}" 2>/dev/null || echo "[]"; exit 0; fi\n'
                   f'if [ "$1" = "task" ]; then echo "[]"; exit 0; fi\nprintf \'%s\\n\' "$*" >> "{argslog}"\n')
fake_cm.chmod(0o755)
servers = tmp / "spazio-server.json"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "workspace": {"root": str(ws), "root_session_name": "master", "sessions_scope": "all"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}, "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "default_account": "personale", "quota": {"source": str(qdir)},
    "bot": {"api_base": f"http://127.0.0.1:{srv.server_port}", "token_file": str(tg / ".env"), "access_file": str(tg / "access.json")},
    "relay": {"dir": str(relay), "web_app_url": APP},
    "night": {"queue_file": str(state / "night-queue.jsonl")},
    "recap": {"cron_time": "20:00", "summary": "last", "servers_file": str(servers)},
    "tile": {"chrome_bridge_cli": ""}}))
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE")}
ENV.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASKS_DB": str(tmp / "t.db"), "CM_RELAY_NO_PUSH": "1",
            "CM_CRONTAB_CMD": str(fake_crontab), "CM_MONITORS": str(tmp / "monitors"), "CM_GUARD_CM": str(fake_cm), "CM_DIARY_FAKE_PROC": "1"})
os.environ.clear()
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")


def two_lines(m):
    lines = [x for x in m.get("text", "").splitlines() if x.strip()]
    return len(lines) == 2 and APP in lines[1] and not EMOJI.search(m["text"])


def qfile(acc_dir, **kw):
    h = hashlib.sha256(str(acc_dir).encode()).hexdigest()[:8]
    (qdir / f"quota-{h}.json").write_text(json.dumps(kw))


# ------------------------------------------------------------------ AG1: il relay fermo
sp = importlib.util.spec_from_file_location("cm_relay_avvisi", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
RL.log = lambda line, now=None: None
pushes = []
RL.push = lambda **k: pushes.append(k)   # la push diretta della guardia non sblocca: lo stato resta fermo
RL.emit = lambda *a, **k: None
T0 = time.time() - 3600
(relay / "last-state.json").write_text(json.dumps({"state": {"published_at": T0}, "pushed_at": T0}))
RL.guard(now=T0 + 300)
n5 = len(SENT)
RL.guard(now=T0 + 16 * 60)
got = SENT[n5:]
RL.guard(now=T0 + 17 * 60)
T.check("AG1 guard: the state stuck for 5 minutes, the guard tries a direct push and Telegram stays quiet",
        pushes and n5 == 0, f"{len(pushes)} pushes, {SENT[:1]}")
T.check("AG1 guard: stuck for 16 minutes, ONE alarm of two lines (what and where, what to do with the app link), no emoji; the next pass does not repeat it",
        len(got) == 1 and two_lines(got[0]) and "16 min" in got[0]["text"] and len(SENT) == n5 + 1, str(SENT)[:600])

# ------------------------------------------------------------------ AG2: le push fallite
(relay / "guard.json").unlink(missing_ok=True)
n0 = len(SENT)
for i in range(3):
    RL.push_failed_note(RuntimeError("HTTP 503"), "test", now=T0 + 20 * i)
quick = SENT[n0:]
RL.push_failed_note(RuntimeError("HTTP 503"), "test", now=T0 + 16 * 60)
RL.push_failed_note(RuntimeError("HTTP 503"), "test", now=T0 + 17 * 60)
late = SENT[n0 + len(quick):]
T.check("AG2 guard: three failed pushes in a minute do not go to Telegram", not quick, str(quick)[:300])
T.check("AG2 guard: failing for 16 minutes, one alarm of two lines with the app link, once",
        len(late) == 1 and two_lines(late[0]) and "HTTP 503" in late[0]["text"], str(late)[:600])

# ------------------------------------------------------------------ AG3: la quota
now = time.time()
qfile(home / ".claude", five_hour_used_pct=93, five_hour_resets_at=now + 5400, weekly_used_pct=50, weekly_resets_at=now + 3 * 86400)
qfile(home / ".claude-pixel", five_hour_used_pct=10, five_hour_resets_at=now + 5400, weekly_used_pct=20, weekly_resets_at=now + 4 * 86400)
stall_file.write_text(json.dumps([{"task": "fermo-1", "session": "alfa", "idle_s": 4 * 3600, "waits": {"on": "person"}, "next": "x"}]))
n0 = len(SENT)
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-guard.py"), "run"], capture_output=True, text=True, env={**ENV, "CM_GUARD_NOW": str(int(now))}, timeout=120)
g3 = SENT[n0:]
talks = argslog.read_text() if argslog.exists() else ""
T.check("AG3 guard: quota at 93% (threshold 92), one alarm of two lines with the account, the reset time and the app link",
        r.returncode == 0 and len(g3) == 1 and two_lines(g3[0]) and "personale" in g3[0]["text"] and "93%" in g3[0]["text"], r.stderr[-400:] + str(g3)[:600])
T.check("AG3 guard: the stalled commitments go to the master (talk), not to Telegram", "talk master" in talks and not any("fermo-1" in m["text"] for m in g3), talks[-300:])
stall_file.unlink()

# ------------------------------------------------------------------ AR1-AR3: il recap delle 20
today = dt.date.today()
yday = today - dt.timedelta(days=1)


def recap(*a):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-recap.py"), *a], capture_output=True, text=True, env=ENV, timeout=120)


r1 = recap("add", "--kind", "rilasci", "Claude Code 2.1.330: hook più veloci")
r2 = recap("add", "--kind", "adozione", "fable-director 41 installazioni (+3)")
r3 = recap("add", "--kind", "rilasci", "Claude Code 2.1.329 di ieri", "--date", yday.isoformat())
r4 = recap("add", "--kind", "Non Va!", "x")
extra = [json.loads(x) for x in (state / "recap-extra.jsonl").read_text().splitlines()] if (state / "recap-extra.jsonl").exists() else []
T.check("AR1 recap add --kind K \"testo\": three entries with their day and kind; an invalid kind is refused",
        r1.returncode == 0 and r2.returncode == 0 and r3.returncode == 0 and r4.returncode != 0 and len(extra) == 3
        and {e.get("day") for e in extra} == {today.isoformat(), yday.isoformat()}, f"{r1.stderr}{r4.returncode} {extra}")


def task(*a, stdin=None):
    return subprocess.run([CLI, "task", *a], input=stdin, capture_output=True, text=True, env=ENV, timeout=120)


def add(tid, title):
    c = {"schema_version": 1, "task": {"id": tid, "title": title, "plan": "p", "where": {"project": str(ws), "session": "alfa", "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": str(ws), "timeout_s": 30}, "perimeter": ["x"], "lane": "open", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
                                       "outcome": {"expected": "x", "kind": "custom", "proof": "x"}}}
    assert task("add", "-", stdin=json.dumps(c)).returncode == 0
    task("start", tid)


add("rel-a", "supervisor 0.8.181 avvisi")
rd = task("done", "rel-a")
add("ok-b", "pubblica il sito")
task("wait-ok", "ok-b", "--consiglio", "va bene", "--what", "push del sito", "--where", "produzione")
(state / "night-queue.jsonl").write_text(json.dumps({"id": "n2", "dir": str(ws / "beta"), "prompt": "rivedi i test", "account": "personale"}) + "\n")
(state / "night-done.jsonl").write_text(
    json.dumps({"id": "n1", "dir": str(ws / "alfa"), "prompt": "pulisci il changelog", "account": "personale", "rc": 0, "seconds": 120,
                "finished": dt.datetime.combine(today, dt.time(2, 30)).isoformat()}) + "\n"
    + json.dumps({"id": "n0", "dir": str(ws / "vecchio"), "prompt": "di una settimana fa", "account": "personale", "rc": 0, "seconds": 9,
                  "finished": dt.datetime.combine(today - dt.timedelta(days=7), dt.time(2, 30)).isoformat()}) + "\n")
(state / "ledger.jsonl").write_text("")
n0 = len(SENT)
r = recap("--send")
ev = SENT[n0:]
txt = ev[0]["text"] if ev else ""
lines = txt.splitlines()
T.check("AR2 recap --send at 20: one message; the first line «Oggi, …» says how many came out and how many items wait",
        r.returncode == 0 and rd.returncode == 0 and len(ev) == 1 and lines[0].startswith("Oggi, ") and "1 uscita" in lines[0]
        and f"{txt.count(APP + '/#/attesa/w')} voci ti aspettano" in lines[0] and "- pubblica il sito: " + APP in txt,
        rd.stderr[-200:] + r.stderr[-400:] + txt[:600])
T.check("AR2 recap: Uscito with the task closed today, Ti aspetta with the item's app link, Stanotte with the queue, Notte with last night's run (not last week's)",
        "Uscito: supervisor 0.8.181 avvisi" in txt and "Ti aspetta:" in txt and f"{APP}/#/attesa/w" in txt and "Stanotte: 1 lavoro in coda (beta)" in txt
        and "Notte: 1 lavoro, 1 riuscito" in txt and "alfa" in txt and "vecchio" not in txt, txt)
T.check("AR2 recap: today's recap add entries under their kind's name, not yesterday's; no emoji, no HTML",
        "Rilasci di Claude Code: Claude Code 2.1.330: hook più veloci" in txt and "Adozione dei plugin: fable-director 41 installazioni (+3)" in txt
        and "2.1.329" not in txt and not EMOJI.search(txt) and "<b>" not in txt, txt)
r = recap("install")
c = cron.read_text()
T.check("AR3 recap install: the 20:00 line (recap --send) and the 08:00 morning line (recap morning --send)",
        r.returncode == 0 and "0 20 * * *" in c and "recap --send" in c and "0 8 * * *" in c and "recap morning --send" in c, r.stdout + r.stderr + c)

# ------------------------------------------------------------------ AM1: il mattino
reset_w = time.mktime((today + dt.timedelta(days=2)).timetuple()) + 3 * 3600
qfile(home / ".claude", five_hour_used_pct=5, five_hour_resets_at=now + 3600, weekly_used_pct=92, weekly_resets_at=reset_w)
qfile(home / ".claude-pixel", five_hour_used_pct=1, five_hour_resets_at=now + 3600, weekly_used_pct=40, weekly_resets_at=reset_w + 86400)
first = today.replace(day=1)
usage = {}
for i in range(min(today.day, 3)):
    usage[(first + dt.timedelta(days=i)).isoformat()] = {"result_bytes": 500_000_000, "state_bytes": 0}
usage[(first - dt.timedelta(days=1)).isoformat()] = {"result_bytes": 9_000_000_000, "state_bytes": 0}   # il mese prima: fuori
(relay / "usage-firebase.json").write_text(json.dumps(usage))
servers.write_text(json.dumps({"Alpha": {"livello": "alto", "liberi_gb": 49, "uso": 91, "ora": "07:30"},
                               "Beta": {"livello": "ok", "liberi_gb": 300, "uso": 40, "ora": "07:30"}}))
mon = lambda *a: subprocess.run([CLI, "monitor", *a], capture_output=True, text=True, env=ENV, timeout=60)  # noqa: E731
mon("add", "prezzo", "--title", "Prezzo del portatile", "--form", "tile", "--unit", "€")
mon("alert", "prezzo", "--below", "1500")
mon("point", "prezzo", "1399")
n0 = len(SENT)
r = recap("morning", "--send")
mm = SENT[n0:]
txt = mm[0]["text"] if mm else ""
when = time.strftime("%d/%m", time.localtime(reset_w))
gb = round(min(today.day, 3) * 0.5, 1)
print(txt) if os.environ.get("AVVISI_SHOW") else None
T.check("AM1 recap morning --send: one message, first line «Mattino, …»; the quota of both accounts with the reset day and hour",
        r.returncode == 0 and len(mm) == 1 and txt.startswith("Mattino, ") and "Quota personale: settimana 92%" in txt and f"{when} alle 03:00" in txt
        and "Quota professionale: settimana 40%" in txt, r.stderr[-400:] + txt)
T.check("AM1 recap morning: Firebase of the month in GB (not last month), only the servers past the threshold, the monitor past its threshold; no emoji",
        f"Firebase: {str(gb).replace('.', ',')} GB" in txt and "Alpha 91% (49 GB liberi)" in txt and "Beta" not in txt and "Prezzo del portatile" in txt
        and not EMOJI.search(txt), txt)

srv.shutdown()
T.rm(tmp)
T.finish()
