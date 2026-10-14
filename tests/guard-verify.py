#!/usr/bin/env python3
"""Verifica cm-guard.py con file quota finti, ledger finto, registro peer finto, Telegram finto,
dispatcher finto (talk/night) e orologio finto (CM_GUARD_NOW).

GU1 sotto soglia: nessun avviso, stato vuoto
GU2 sopra soglia: UN avviso Telegram per finestra (account, %, ora del reset); un secondo giro non lo ripete
GU3 al reset: le sessioni vive dell'account con un turno fallito dopo l'avviso ricevono «riprendi» via talk,
    le altre no; la coda notturna non vuota fa partire night run --send; un giro dopo non ripete
GU4 install (riga cron ogni 5 min), status, uninstall
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
tg = home / ".claude" / "channels" / "telegram"
tg.mkdir(parents=True)
(tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
(tg / "access.json").write_text(json.dumps({"allowFrom": ["1001"]}))
(home / ".claude-pixel").mkdir()
state = tmp / "state"
state.mkdir()
qdir = tmp / "quota"
qdir.mkdir()
CALLS = {"sendMessage": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        params = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        CALLS.setdefault(self.path.rsplit("/", 1)[-1], []).append(params)
        out = json.dumps({"ok": True, "result": {}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)


srv = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
argslog = tmp / "cm-args.log"
fake_cm = tmp / "supervisor"
fake_cm.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{argslog}"\n')
fake_cm.chmod(0o755)
cron = tmp / "crontab"
cron.write_text("")
fake_crontab = tmp / "crontab.sh"
fake_crontab.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (cron, cron, cron, cron))
fake_crontab.chmod(0o755)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state),
    "accounts": {"personale": {"config_dir": str(home / ".claude")}, "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "default_account": "personale",
    "quota": {"source": str(qdir)},
    "bot": {"api_base": f"http://127.0.0.1:{srv.server_port}", "token_file": str(tg / ".env"), "access_file": str(tg / "access.json")},
    "guard": {"warn_pct": 95, "cron_minutes": 5},
    "relay": {"dir": str(tmp / "relay")},
    "night": {"queue_file": str(state / "night-queue.jsonl")},
}))


def qfile(config_dir):
    return qdir / f"quota-{hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]}.json"


NOW = 1_800_000_000
RESET = NOW + 3600


def write_quota(pct_personal, pct_pro=10.0):
    qfile(home / ".claude").write_text(json.dumps({"five_hour_used_pct": pct_personal, "five_hour_resets_at": RESET, "weekly_used_pct": 40.0, "weekly_resets_at": RESET + 86400}))
    qfile(home / ".claude-pixel").write_text(json.dumps({"five_hour_used_pct": pct_pro, "five_hour_resets_at": RESET, "weekly_used_pct": 5.0, "weekly_resets_at": RESET + 86400}))


def guard(*args, now=NOW):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
           "CM_CRONTAB_CMD": str(fake_crontab), "CM_GUARD_CM": str(fake_cm), "CM_GUARD_NOW": str(now), "CM_GUARD_FAKE_PROC": "1"}
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-guard.py"), *args], capture_output=True, text=True, env=env, timeout=60)


# GU1
write_quota(60.0)
r = guard("run")
T.check("GU1 below the threshold: no message, no state", r.returncode == 0 and not CALLS["sendMessage"] and not (state / "guard.json").exists(), r.stdout + r.stderr)

# GU2
write_quota(97.0)
r = guard("run")
T.check("GU2 above the threshold: one Telegram warning with account, % and reset time", r.returncode == 0 and len(CALLS["sendMessage"]) == 1 and "personale" in CALLS["sendMessage"][0]["text"] and "97%" in CALLS["sendMessage"][0]["text"] and "reset" in CALLS["sendMessage"][0]["text"].lower(), r.stdout + r.stderr + str(CALLS))
r = guard("run", now=NOW + 600)
T.check("GU2 a second pass in the same window does not repeat it", len(CALLS["sendMessage"]) == 1, str(CALLS))
# GU2b (16/09, Telegram a senso unico): con l'orologio che riceve, la quota gliela dicono gli eventi del relay
rdirg = tmp / "relay"; rdirg.mkdir(parents=True, exist_ok=True)
(rdirg / "devices.json").write_text(json.dumps({"uid-watch": True}))
(rdirg / "last-state.json").write_text(json.dumps({"pushed_at": time.time()}))
(state / "guard.json").unlink(missing_ok=True)
n_before = len(CALLS["sendMessage"])
r = guard("run")
T.check("GU2b the watch is receiving → the quota warning does not go to Telegram (the app has the event)", r.returncode == 0 and len(CALLS["sendMessage"]) == n_before, r.stdout + r.stderr + str(CALLS["sendMessage"][n_before:]))
(rdirg / "devices.json").unlink()

# GU3: registro peer vivo (due sessioni personali, una professionale), ledger con un turno fallito solo per alfa
(home / ".claude" / "sessions").mkdir(parents=True)
for i, (name, sid) in enumerate([("alfa", "S-A"), ("beta", "S-B"), ("pix-gamma", "S-G")]):
    (home / ".claude" / "sessions" / f"{1000 + i}.json").write_text(json.dumps({"pid": 1000 + i, "sessionId": sid, "name": name, "status": "idle"}))
import datetime as dt
t_fail = dt.datetime.fromtimestamp(NOW + 900).isoformat(timespec="seconds")
(state / "ledger.jsonl").write_text(json.dumps({"ts": t_fail, "event": "stop-failure", "session_id": "S-A", "cwd": "/x", "account": "personale", "pid": 1000, "error": "rate limit"}) + "\n"
                                    + json.dumps({"ts": t_fail, "event": "stop-failure", "session_id": "S-G", "cwd": "/y", "account": "professionale", "pid": 1002, "error": "rate limit"}) + "\n")
(state / "night-queue.jsonl").write_text(json.dumps({"id": "n1", "dir": "/x", "prompt": "p", "account": "personale"}) + "\n")
r = guard("run", now=NOW + 1800)
T.check("GU3 before the reset: nothing resumed", not argslog.exists(), r.stdout + r.stderr)
write_quota(3.0)   # dopo il reset la quota riparte
r = guard("run", now=RESET + 60)
args = argslog.read_text().splitlines() if argslog.exists() else []
T.check("GU3 at the reset: talk «riprendi» only to the personal session that failed (alfa), not beta, not pix-gamma", r.returncode == 0 and any(a.startswith("talk alfa ") and "--no-wait" in a for a in args) and not any(a.startswith("talk beta") or a.startswith("talk pix-gamma") for a in args), r.stdout + r.stderr + str(args))
T.check("GU3 night queue not empty → night run --send", any(a.startswith("night run --send") for a in args), str(args))
T.check("GU3 a Telegram line reports the resume", len(CALLS["sendMessage"]) == 2 and "alfa" in CALLS["sendMessage"][1]["text"], str(CALLS["sendMessage"][-1:]))
n_args = len(args)
r = guard("run", now=RESET + 400)
T.check("GU3 a later pass repeats nothing", len(argslog.read_text().splitlines()) == n_args and len(CALLS["sendMessage"]) == 2, r.stdout + r.stderr)

# GU5 (1.67, 14/10): il reset settimanale — personale al 20% a 6 ore dal reset (quota da usare); professionale all'80%
# dopo 2 giorni su 7 (a questo ritmo finisce prima del reset). Un avviso ciascuno, una volta per finestra
(state / "guard.json").unlink(missing_ok=True)
(state / "ledger.jsonl").write_text("")
qfile(home / ".claude").write_text(json.dumps({"weekly_used_pct": 20.0, "weekly_resets_at": NOW + 6 * 3600}))
qfile(home / ".claude-pixel").write_text(json.dumps({"weekly_used_pct": 80.0, "weekly_resets_at": NOW + 5 * 86400}))
n5 = len(CALLS["sendMessage"])
r = guard("run")
msgs5 = [c["text"] for c in CALLS["sendMessage"][n5:]]
r2 = guard("run", now=NOW + 600)
T.check("GU5 weekly reset: «80 % still unused» for personale, «at this pace the week runs out» for professionale; a second pass repeats neither",
        r.returncode == 0 and len(msgs5) == 2 and any("personale" in m and "80 %" in m for m in msgs5) and any("professionale" in m and "⏳" in m for m in msgs5)
        and len(CALLS["sendMessage"]) == n5 + 2, r.stdout + r.stderr + str(msgs5))

# GU6 (14/10): cambio automatico di account al limite, solo per gli account elencati, con isteresi e attesa
_cfg6 = json.loads(cfg.read_text())
cfg.write_text(json.dumps(dict(_cfg6, auto_switch={"accounts": ["personale"], "at_pct": 90, "back_pct": 75, "min_s": 1800})))
(state / "guard.json").unlink(missing_ok=True)
qfile(home / ".claude").write_text(json.dumps({"five_hour_used_pct": 95.0, "five_hour_resets_at": NOW + 3600}))
qfile(home / ".claude-pixel").write_text(json.dumps({"five_hour_used_pct": 96.0, "five_hour_resets_at": NOW + 3600}))
guard("run")
both6 = guard("switch-for", "personale").stdout.strip()
qfile(home / ".claude-pixel").write_text(json.dumps({"five_hour_used_pct": 30.0, "five_hour_resets_at": NOW + 3600}))
on6, pro6 = guard("switch-for", "personale").stdout.strip(), guard("switch-for", "professionale").stdout.strip()
qfile(home / ".claude").write_text(json.dumps({"five_hour_used_pct": 60.0, "five_hour_resets_at": NOW + 3600}))
guard("run", now=NOW + 600)
still6 = guard("switch-for", "personale").stdout.strip()
guard("run", now=NOW + 2000)
off6 = guard("switch-for", "personale").stdout.strip()
cfg.write_text(json.dumps(_cfg6))
T.check("GU6 auto_switch: personale at 95% → new sessions on professionale (not while professionale is past the threshold too); professionale (not listed) stays itself; under 75% only after min_s it goes back",
        both6 == "personale" and on6 == "professionale" and pro6 == "professionale" and still6 == "professionale" and off6 == "personale", str((both6, on6, pro6, still6, off6)))

# GU4
r = guard("install")
T.check("GU4 install: cron every 5 minutes", r.returncode == 0 and "*/5 * * * *" in cron.read_text() and "guard run" in cron.read_text(), r.stdout + cron.read_text())
r = guard("status")
T.check("GU4 status: cron yes, one line per account with % and reset", "yes" in r.stdout and "personale" in r.stdout and "professionale" in r.stdout, r.stdout + r.stderr)
r = guard("uninstall")
T.check("GU4 uninstall", r.returncode == 0 and "guard" not in cron.read_text(), r.stdout + cron.read_text())

srv.shutdown()
T.rm(tmp)
T.finish()
