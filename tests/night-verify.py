#!/usr/bin/env python3
"""Verifica cm-night.py con il claude finto (-p), RAM finta, Telegram finto e crontab finto.

NI1 add: voce in coda con account dedotto dalla folder_map, --model/--effort/--max-turns; list; remove; clear
NI2 run --dry-run: dice cosa farebbe, non tocca la coda, niente claude
NI3 run: claude -p nella cartella con --permission-mode e --max-turns, CLAUDE_CONFIG_DIR solo per
    il secondo account, rapporto in docs/notte, voce spostata in done, riassunto --send su Telegram
NI4 guardie: RAM sotto soglia → voce saltata con motivo, resta in coda; --one esegue una sola voce
NI5 install (riga cron alle 02:00), status, uninstall
NI3b a fine giro (non --dry-run, almeno una voce eseguita) il rapporto della pagina «Notte» si rigenera
NI6 (contratto 1.17 del relay) prompt vuoto e coda piena rifiutati; durante `run` la voce in corso porta `started`
    e `remove` la rifiuta, una voce tolta non parte, una voce aggiunta resta in coda
NI3b/NI3c (16/10/2026) la variabile d'attesa al limite d'uso della 2.1.301; claude -p in stream-json con --verbose
NI7 (16/10/2026) l'esito dall'evento `result`: due turni (il riassunto dalla riga «Esito:» del risultato finale), un
    turno che riparte dopo un risultato, un processo che resta aperto dopo il risultato (chiuso dopo
    night.after_result_s, figli compresi), un risultato d'errore (rc 1), nessun risultato entro item_timeout_s (rc 124)
"""
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
ws = home / "ws"
for d in ("personali/alfa", "agenzia/clienti/sito.com"):
    (ws / d).mkdir(parents=True)
state = tmp / "state"
state.mkdir()
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
cron = tmp / "crontab"
cron.write_text("")
fake_crontab = tmp / "crontab.sh"
fake_crontab.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (cron, cron, cron, cron))
fake_crontab.chmod(0o755)
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
argslog = tmp / "args.log"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "sessions": {"max_sessions": 50}, "state_dir": str(state),
    "workspace": {"root": str(ws)},
    "accounts": {"personale": {"config_dir": "~/.claude"}, "professionale": {"config_dir": "~/.claude-pixel", "tmux_prefix": "pix-"}},
    "default_account": "personale",
    "folder_map": [{"path": str(ws / "agenzia"), "account": "professionale"}],
    "bot": {"api_base": f"http://127.0.0.1:{srv.server_port}", "token_file": str(tg / ".env"), "access_file": str(tg / "access.json")},
    "night": {"cron_time": "02:00", "min_free_mb": 500, "max_quota_pct": 80, "max_items_per_run": 3, "max_turns": 12, "max_queued": 3, "permission_mode": "acceptEdits"},   # 06/10: il default e' «default», acceptEdits lo sceglie l'utente
}))


report_marker = tmp / "night-report.calls"
fake_report = tmp / "fake-night-report.py"
fake_report.write_text(f"import sys\nopen({str(report_marker)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\nprint('/x/report.json')\n")


def night(*args, free_mb="4000", claude=FAKE, conf=None):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(conf or cfg),
           "CM_CRONTAB_CMD": str(fake_crontab), "CM_CLAUDE_BIN": str(claude), "FAKE_CLAUDE_ARGS_LOG": str(argslog),
           "FAKE_CLAUDE_ECHO_ENV": "CLAUDE_CODE_RETRY_WATCHDOG,CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS,CLAUDE_CODE_RETRY_WATCHDOG_MAX_USAGE_LIMIT_WAIT_MS,CLAUDE_CODE_FORK_SUBAGENT,CLAUDE_CONFIG_DIR",
           "CM_NIGHT_FREE_MB": free_mb, "CM_NIGHT_REPORT_BIN": str(fake_report)}
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-night.py"), *args], capture_output=True, text=True, env=env, timeout=120)


queue = state / "night-queue.jsonl"

# NI1
r = night("add", str(ws / "personali" / "alfa"), "sistema i test rossi")
T.check("NI1 add: queued with the default account", r.returncode == 0 and "personale" in r.stdout and queue.is_file(), r.stdout + r.stderr)
r = night("add", str(ws / "agenzia" / "clienti" / "sito.com"), "aggiorna il changelog", "--model", "sonnet", "--effort", "low", "--max-turns", "5")
rows = [json.loads(l) for l in queue.read_text().splitlines()]
T.check("NI1 add: account from folder_map, model/effort/max-turns kept", r.returncode == 0 and rows[1]["account"] == "professionale" and rows[1]["model"] == "sonnet" and rows[1]["effort"] == "low" and rows[1]["max_turns"] == 5 and rows[0]["max_turns"] == 12, r.stdout + r.stderr + str(rows))
r = night("add", str(tmp / "nope"), "x")
T.check("NI1 add: missing folder refused", r.returncode != 0 and len(queue.read_text().splitlines()) == 2, r.stdout + r.stderr)
r = night("list")
T.check("NI1 list shows both", "alfa" in r.stdout and "sito.com" in r.stdout and "2" in r.stdout, r.stdout)
r = night("add", str(ws / "personali" / "alfa"), "da togliere")
rid = [json.loads(l) for l in queue.read_text().splitlines()][-1]["id"]
r = night("remove", rid)
T.check("NI1 remove by id", r.returncode == 0 and rid not in queue.read_text() and len(queue.read_text().splitlines()) == 2, r.stdout + r.stderr)

# NI2
r = night("run", "--dry-run")
T.check("NI2 --dry-run: lists what it would run, queue untouched, claude not called", r.returncode == 0 and r.stdout.count("-p") >= 2 and len(queue.read_text().splitlines()) == 2 and not argslog.exists(), r.stdout + r.stderr)
T.check("NI2 --dry-run: the night report is not rebuilt", not report_marker.exists(), "")

# NI3
r = night("run", "--send")
args = argslog.read_text().splitlines() if argslog.exists() else []
T.check("NI3 run: claude -p with permission-mode and max-turns, twice", r.returncode == 0 and len(args) == 2 and "-p sistema i test rossi --permission-mode acceptEdits --max-turns 12" in args[0] and "--max-turns 5 --model sonnet --effort low" in args[1], r.stdout + r.stderr + str(args))
T.check("NI3 CLAUDE_CONFIG_DIR only for the second account (T68)", "CLAUDE_CONFIG_DIR=" in args[0] and args[0].rstrip().endswith("CLAUDE_CONFIG_DIR=") and str(home / ".claude-pixel") in args[1], str(args))
T.check("NI3b retry watchdog on for every item, capped at night.retry_watchdog_max_wait_ms (default 15 min)", len(args) == 2 and all("ENV CLAUDE_CODE_RETRY_WATCHDOG=1 " in a and "ENV CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS=900000 " in a for a in args), str(args))
T.check("NI3b at a usage limit the watchdog waits at most night.retry_watchdog_max_usage_limit_wait_ms (2.1.301, default 15 min)",
        len(args) == 2 and all("ENV CLAUDE_CODE_RETRY_WATCHDOG_MAX_USAGE_LIMIT_WAIT_MS=900000 " in a for a in args), str(args))
T.check("NI3d fork subagents off for every item (CLAUDE_CODE_FORK_SUBAGENT=0, 2.1.302), night.fork_subagent false by default",
        len(args) == 2 and all("ENV CLAUDE_CODE_FORK_SUBAGENT=0 " in a for a in args), str(args))
T.check("NI3c claude -p in stream-json with --verbose (the outcome from the `result` event)",
        len(args) == 2 and all(" --output-format stream-json --verbose" in a for a in args), str(args))
reports = sorted((ws / "personali" / "alfa" / "docs" / "notte").glob("*.md")) + sorted((ws / "agenzia" / "clienti" / "sito.com" / "docs" / "notte").glob("*.md"))
T.check("NI3 a report per item in docs/notte with prompt and output", len(reports) == 2 and "sistema i test rossi" in reports[0].read_text() and "Sent to cloud session (fake)" in reports[0].read_text(), str(reports))
T.check("NI3 queue emptied, done has both with rc", queue.read_text().strip() == "" and len((state / "night-done.jsonl").read_text().splitlines()) == 2 and '"rc": 0' in (state / "night-done.jsonl").read_text(), (state / "night-done.jsonl").read_text()[:300])
T.check("NI3 end of the run: the night report rebuilt once, after the items, and the log says so",
        report_marker.is_file() and len(report_marker.read_text().splitlines()) == 1
        and "night report: rc=0 /x/report.json" in (state / "night.log").read_text(), (state / "night.log").read_text()[-400:])
T.check("NI3 --send: one Telegram message with both items", len(CALLS["sendMessage"]) == 1 and "alfa" in CALLS["sendMessage"][0]["text"] and "sito.com" in CALLS["sendMessage"][0]["text"] and "✓" in CALLS["sendMessage"][0]["text"], str(CALLS["sendMessage"])[:400])

# NI4
argslog.unlink()
night("add", str(ws / "personali" / "alfa"), "uno")
night("add", str(ws / "personali" / "alfa"), "due")
r = night("run", free_mb="100")
T.check("NI4 low RAM: both skipped with the reason, queue intact, claude not called", r.returncode == 0 and "RAM" in r.stdout and len(queue.read_text().splitlines()) == 2 and not argslog.exists(), r.stdout + r.stderr)
r = night("run", "--one")
T.check("NI4 --one runs a single item, one stays queued", r.returncode == 0 and len(argslog.read_text().splitlines()) == 1 and len(queue.read_text().splitlines()) == 1 and "due" in queue.read_text(), r.stdout + queue.read_text())
r = night("clear")
T.check("NI4 clear empties the queue", queue.read_text().strip() == "", r.stdout)

# NI6
def ids():
    return [json.loads(l)["id"] for l in queue.read_text().splitlines() if l.strip()]


r = night("add", str(ws / "personali" / "alfa"), "   ")
T.check("NI6 add: empty prompt refused (exit 2), queue untouched", r.returncode == 2 and ids() == [] and "vuoto" in r.stderr, r.stdout + r.stderr)
for w in ("uno", "due", "tre"):
    night("add", str(ws / "personali" / "alfa"), w)
r = night("add", str(ws / "personali" / "alfa"), "quattro")
T.check("NI6 add: past night.max_queued (3) → exit 4 «coda piena», three still queued", r.returncode == 4 and "piena" in r.stderr and len(ids()) == 3, r.stdout + r.stderr)
first, second, third = ids()
# claude finto che, mentre gira la PRIMA voce, fa quello che farebbe il telefono: prova a togliere la voce in corso,
# toglie la seconda, ne aggiunge una nuova (il lock della coda li serializza con run)
probe = tmp / "probe.log"
busy = tmp / "busy-claude.sh"
busy.write_text(f"""#!/bin/sh
if [ ! -f "{probe}" ]; then
  {sys.executable} "{T.SCRIPTS / 'cm-night.py'}" remove {first} > /dev/null 2>&1; echo "remove-running rc=$?" >> "{probe}"
  grep -c '"started"' "{queue}" >> "{probe}"
  {sys.executable} "{T.SCRIPTS / 'cm-night.py'}" remove {second} > /dev/null 2>&1; echo "remove-second rc=$?" >> "{probe}"
  {sys.executable} "{T.SCRIPTS / 'cm-night.py'}" add "{ws / 'personali' / 'alfa'}" "aggiunta di notte" > /dev/null 2>&1; echo "add rc=$?" >> "{probe}"
fi
exec "{FAKE}" "$@"
""")
busy.chmod(0o755)
argslog.unlink()
r = night("run", claude=busy)
pl = probe.read_text().splitlines() if probe.exists() else []
ran = argslog.read_text().splitlines() if argslog.exists() else []
T.check("NI6 during run the running job carries `started` and `remove` refuses it (exit 5)", len(pl) == 4 and pl[0].endswith("rc=5") and pl[1] == "1", str(pl))
T.check("NI6 a job removed during the run does not start; the job added during the run stays queued; the others ran",
        r.returncode == 0 and len(pl) == 4 and pl[2].endswith("rc=0") and pl[3].endswith("rc=0") and len(ran) == 2 and "-p uno " in ran[0] and "-p tre " in ran[1]
        and not any("-p due " in a for a in ran) and [json.loads(l)["prompt"] for l in queue.read_text().splitlines()] == ["aggiunta di notte"], r.stdout + r.stderr + str(ran) + queue.read_text())
T.check("NI6 done holds the two jobs that ran", [json.loads(l)["prompt"] for l in (state / "night-done.jsonl").read_text().splitlines()][-2:] == ["uno", "tre"], (state / "night-done.jsonl").read_text()[-300:])
night("clear")

# NI5
r = night("install")
T.check("NI5 install: cron at 02:00 with run --send", r.returncode == 0 and "0 2 * * *" in cron.read_text() and "night run --send" in cron.read_text(), r.stdout + cron.read_text())
r = night("status")
T.check("NI5 status: cron yes, queue 0, done 5", "yes" in r.stdout and "0" in r.stdout and "5" in r.stdout, r.stdout + r.stderr)
r = night("uninstall")
T.check("NI5 uninstall", r.returncode == 0 and "night" not in cron.read_text(), r.stdout + cron.read_text())

# NI7 (16/10/2026, Claude Code 2.1.292-2.1.301): stream-json, l'esito dal `result`, la voce chiusa dopo il risultato
# quando il processo resta aperto (un comando in background, una sveglia), un turno che riparte la rimanda. Un claude
# finto per prompt: il prompt dice cosa fare.
c7 = json.loads(cfg.read_text())
c7["night"].update(max_queued=5, max_items_per_run=5, item_timeout_s=12, after_result_s=4)
cfg7 = tmp / "config7.json"
cfg7.write_text(json.dumps(c7))
bgpid, fakepid = tmp / "bg.pid", tmp / "fake.pid"


def ev(**d):
    return "'" + json.dumps(d).replace("'", "") + "'"


res_ok = lambda text: ev(type="result", subtype="success", is_error=False, result=text)   # noqa: E731
said = lambda text: ev(type="assistant", message={"content": [{"type": "text", "text": text}]})   # noqa: E731
stream = tmp / "stream-claude.sh"
stream.write_text(f"""#!/bin/sh
case "$2" in
  two-turns) printf '%s\\n' {ev(type="system", subtype="init")} {res_ok("primo turno: build in background")} {said("build finito")} \\
               {res_ok("build finito, test verdi" + chr(10) + "Esito: build e test verdi")} ;;
  restart)   printf '%s\\n' {res_ok("turno uno")}; sleep 0.3; printf '%s\\n' {said("comando in background finito")}; sleep 6
             printf '%s\\n' {res_ok("turno due" + chr(10) + "Esito: tutto fatto")} ;;
  stays-open) sleep 300 & echo $! > "{bgpid}"; printf '%s\\n' {res_ok("server lasciato acceso" + chr(10) + "Esito: fatto")}; sleep 300 ;;
  error)     printf '%s\\n' {ev(type="result", subtype="error_max_turns", is_error=True, result="max turns")}; sleep 300 ;;
  timeout)   echo $$ > "{fakepid}"; printf '%s\\n' {said("sto lavorando")}; sleep 300 ;;
esac
""")
stream.chmod(0o755)
KINDS7 = ("two-turns", "restart", "stays-open", "error", "timeout")
for w in KINDS7:
    night("add", str(ws / "personali" / "alfa"), w, conf=cfg7)
t0 = time.time()
r = night("run", claude=stream, conf=cfg7)
took = time.time() - t0
done7 = {d["prompt"]: d for d in (json.loads(l) for l in (state / "night-done.jsonl").read_text().splitlines()) if d["prompt"] in KINDS7}
out7 = {k: (Path(d["report"]).read_text() if Path(d["report"]).is_file() else "") for k, d in done7.items()}
so = r.stdout.splitlines()
lines7 = {k: l for i, l in enumerate(so[:-1]) if l.startswith(("✓", "✗")) for k, d in done7.items() if d["id"] in so[i + 1]}


def alive(pidfile):
    """Il processo del file e' vivo (uno zombie non conta)."""
    try:
        return pidfile.is_file() and Path(f"/proc/{pidfile.read_text().strip()}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


T.check("NI7 five items ran", r.returncode == 0 and len(done7) == 5, r.stdout + r.stderr)
T.check("NI7 two turns: the report has both results; the summary is the final result's «Esito:» line, not the first line",
        done7.get("two-turns", {}).get("rc") == 0 and "primo turno: build in background" in out7.get("two-turns", "")
        and "Esito: build e test verdi" in out7.get("two-turns", "") and lines7.get("two-turns", "").endswith(": build e test verdi"), str(lines7) + out7.get("two-turns", ""))
T.check("NI7 a turn that starts again after a result postpones the closure: both turns, rc 0, not closed",
        done7.get("restart", {}).get("rc") == 0 and "turno due" in out7.get("restart", "") and "dopo il risultato" not in out7.get("restart", ""), out7.get("restart", ""))
T.check("NI7 a process left open after the result: closed after night.after_result_s, rc 0, the report says so, the background child stopped too",
        done7.get("stays-open", {}).get("rc") == 0 and "chiusa 4 s dopo il risultato" in out7.get("stays-open", "")
        and done7.get("stays-open", {}).get("seconds", 99) < 30 and bgpid.is_file() and not alive(bgpid), out7.get("stays-open", "") + str(done7.get("stays-open")))
T.check("NI7 an error result left open: closed, rc 1", done7.get("error", {}).get("rc") == 1 and "chiusa 4 s" in out7.get("error", ""), out7.get("error", ""))
T.check("NI7 no result within item_timeout_s: rc 124, the text said so far and «tempo scaduto», the process stopped",
        done7.get("timeout", {}).get("rc") == 124 and "sto lavorando" in out7.get("timeout", "") and "tempo scaduto" in out7.get("timeout", "")
        and fakepid.is_file() and not alive(fakepid), out7.get("timeout", ""))
T.check("NI7 the whole run well under the 300 s the fakes would sleep", took < 90, f"{took:.0f} s")

srv.shutdown()
T.rm(tmp)
T.finish()
