#!/usr/bin/env python3
"""Il recap non resta appeso (15/11/2026, supervisor-recap-appeso-15-11).

`supervisor diary` restava muto per piu' di 100 secondi e veniva fermato a mano: non stampa niente finche' non ha il
riassunto del modello, un `claude -p` che aveva 120 secondi, non veniva ricordato quando scadeva e veniva richiamato
una seconda volta per docs/recap.md. Qui il modello e' un claude finto che non risponde mai (e lascia un figlio).

RA1  un modello che non risponde: il recap esce lo stesso entro il tempo del riassunto, con l'ultimo messaggio di ogni
     progetto, uscita 0, e lo dice su stderr; il modello e' stato chiamato UNA volta, non due
RA2  del claude finto e del suo figlio non resta nessun processo
RA3  il giro dopo non richiama il modello (ricordato per mezz'ora) ed e' veloce; passata la mezz'ora lo richiama
RA4  il claude finto riceve l'input chiuso: un `claude -p` che aspetta l'input non tiene piu' appeso il recap
RA5  il tempo massimo del comando intero (recap.max_s): allo scadere uscita 124 e una riga chiara che dice a che punto
     era; niente resta in esecuzione
RA6  i default: recap.max_s 150, recap.summary_timeout_s 60, recap.send_max_s 420; con un modello che risponde tutto
     come prima (una chiamata, il riassunto nel recap, nessun avviso)
RA7  il modello parte leggero (senza server MCP, strumenti, comandi slash, sessione su disco); una CLI che non conosce
     quelle opzioni ed esce con un errore: si riprova senza, e il riassunto arriva
RA8  con --send (il cron) il riassunto ha tutto il tempo che resta al comando, non i secondi del recap a mano, e si
     richiede anche dopo uno appena non arrivato
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
state = tmp / "state"
state.mkdir()
ws = home / "ws"
(ws / "personali" / "alfa").mkdir(parents=True)
(ws / "personali" / "beta").mkdir(parents=True)
A, B = str(ws / "personali" / "alfa"), str(ws / "personali" / "beta")
rows = [
    {"ts": "2026-09-09T09:00:00", "event": "start", "session_id": "A", "cwd": A, "account": "personale", "pid": 1, "source": "startup"},
    {"ts": "2026-09-09T09:05:00", "event": "stop", "session_id": "A", "cwd": A, "account": "personale", "pid": 1, "last": "prima risposta"},
    {"ts": "2026-09-09T09:20:00", "event": "stop", "session_id": "A", "cwd": A, "account": "personale", "pid": 1, "last": "test verdi su alfa"},
    {"ts": "2026-09-09T09:30:00", "event": "end", "session_id": "A", "cwd": A, "account": "personale", "pid": 1, "reason": "other"},
    {"ts": "2026-09-09T10:00:00", "event": "start", "session_id": "B", "cwd": B, "account": "personale", "pid": 2, "source": "startup"},
    {"ts": "2026-09-09T10:05:00", "event": "stop", "session_id": "B", "cwd": B, "account": "personale", "pid": 2, "last": "pagina di beta pubblicata"},
    {"ts": "2026-09-09T10:30:00", "event": "end", "session_id": "B", "cwd": B, "account": "personale", "pid": 2, "reason": "other"},
]
(state / "ledger.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
cfg = tmp / "config.json"
BASE = {
    "language": "it", "sessions": {"max_sessions": 50}, "state_dir": str(state),
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude"), "shape": "circle"}},
    "tabs": {"color_registry": str(tmp / "colors")},
    "tile": {"chrome_bridge_cli": ""},
    "recap": {"cron_time": "20:00", "summary": "model", "min_turns": 1, "summary_timeout_s": 3, "max_s": 40},
}


def conf(**recap):
    c = json.loads(json.dumps(BASE))
    c["recap"].update(recap)
    cfg.write_text(json.dumps(c))


calls, pids, stdin_log = tmp / "calls.log", tmp / "pids.log", tmp / "stdin.log"
hang = tmp / "claude-hang.sh"
hang.write_text(f"""#!/bin/sh
# claude finto che non risponde mai: annota la chiamata, il suo pid e quello di un figlio, e cosa trova sull'input
echo "$@" >> "{calls}"
sleep 300 &
echo "$$ $!" >> "{pids}"
if read -r x; then echo "input: $x" >> "{stdin_log}"; else echo "input chiuso" >> "{stdin_log}"; fi
sleep 300
""")
hang.chmod(0o755)
good = tmp / "claude-good.sh"
good.write_text(f"""#!/bin/sh
echo "$@" >> "{calls}"
echo '{{"alfa": {{"fatto": "Test di alfa sistemati", "prossimo": ""}}, "beta": {{"fatto": "Pagina di beta pubblicata", "prossimo": ""}}}}'
""")
good.chmod(0o755)


def recap(claude, *args, stdin=subprocess.DEVNULL, wait=90):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_DIARY_FAKE_PROC": "1",
           "CM_CLAUDE_BIN": str(claude)}
    t = time.time()
    try:
        r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-recap.py"), "--date", "2026-09-09", *args], stdin=stdin, capture_output=True, text=True,
                           env=env, timeout=wait)
    except subprocess.TimeoutExpired as e:
        r = subprocess.CompletedProcess(e.cmd, -1, (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "ANCORA APPESO")
    return r, time.time() - t


def n_calls():
    return sum(1 for x in calls.read_text().splitlines() if x.startswith("-p ")) if calls.exists() else 0   # il prompt ha piu' righe


def left_alive():
    out = []
    for line in (pids.read_text().splitlines() if pids.exists() else []):
        out += [int(p) for p in line.split() if T.proc_alive(int(p))]
    return out


# RA1
conf()
r, took = recap(hang, wait=20)
T.check("RA1 a model that never answers: the recap still comes out within the summary's time (3 s), exit 0, with each project's last message",
        r.returncode == 0 and took < 12 and "alfa: test verdi su alfa" in r.stdout and "beta: pagina di beta pubblicata" in r.stdout, f"{r.returncode} {took:.1f}s {r.stdout} {r.stderr}")
T.check("RA1 it says so on stderr: the summary did not come in 3 s, the last messages are used, no new request for 30 minutes",
        "riassunto del modello non è arrivato in 3 s" in r.stderr and "30 minuti" in r.stderr, r.stderr)
T.check("RA1 the model was called once in the run, not a second time for docs/recap.md", n_calls() == 1, f"{n_calls()}")
time.sleep(1)
T.check("RA2 nothing of the fake claude is left running: neither it nor the child it started", bool(pids.exists()) and left_alive() == [], f"{left_alive()}")
T.check("RA4 the model gets a closed input: a `claude -p` that waits for input no longer holds the recap",
        stdin_log.exists() and stdin_log.read_text().split("\n")[0] == "input chiuso", stdin_log.read_text() if stdin_log.exists() else "-")

# RA3
r, took = recap(hang, wait=20)
T.check("RA3 the next run does not call the model again (remembered) and is quick; the recap is the same",
        r.returncode == 0 and n_calls() == 1 and took < 3 and "alfa: test verdi su alfa" in r.stdout, f"{r.returncode} {n_calls()} {took:.1f}s {r.stderr}")
cache = state / "recap-summaries" / "09_09_2026.json"
c = json.loads(cache.read_text()) if cache.exists() else {}
ok_c = bool(c) and all(isinstance(v, dict) and v.get("failed_at") for v in c.values())
for v in c.values():
    v["failed_at"] = int(time.time()) - 1900
if c:
    cache.write_text(json.dumps(c))
r, took = recap(good, wait=20)
T.check("RA3 after half an hour it asks again, and a model that answers gives the summary",
        ok_c and r.returncode == 0 and n_calls() == 2 and "alfa: Test di alfa sistemati" in r.stdout and "riassunto del modello" not in r.stderr, f"{ok_c} {n_calls()} {r.stdout} {r.stderr}")

# RA5: il tempo massimo del comando intero
(state / "recap-summaries" / "09_09_2026.json").unlink()
before = n_calls()
conf(summary_timeout_s=30, max_s=4)
r, took = recap(hang, wait=20)   # 30 s di riassunto dentro 4 s di comando: il riassunto prende quello che resta (1 s)
T.check("RA5 the summary never gets more than what is left of the whole command: with max_s 4 it is cut at once and the recap comes out, exit 0",
        r.returncode == 0 and took < 8 and "alfa: test verdi su alfa" in r.stdout and n_calls() == before + 1, f"{r.returncode} {took:.1f}s {r.stderr}")
# un Telegram che accetta la connessione e non risponde mai: il recap resta fermo mentre manda
import socket
mute = socket.socket()
mute.bind(("127.0.0.1", 0))
mute.listen(5)
tg = home / ".claude" / "channels" / "telegram"
tg.mkdir(parents=True)
(tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC" + chr(10))
(tg / "access.json").write_text(json.dumps({"allowFrom": ["1001"]}))
BASE["bot"] = {"api_base": f"http://127.0.0.1:{mute.getsockname()[1]}", "token_file": str(tg / ".env"), "access_file": str(tg / "access.json")}
conf(summary="last", send_max_s=2)
r, took = recap(hang, "--send", wait=20)
T.check("RA5 the whole command's time (with --send: recap.send_max_s 2): exit 124 and one clear line saying how long and where it was; the recap printed before stays",
        r.returncode == 124 and took < 8 and "fermato dopo 2 s" in r.stderr and "mandava il recap" in r.stderr and "Traceback" not in r.stderr
        and "alfa: test verdi su alfa" in r.stdout, f"{r.returncode} {took:.1f}s {r.stdout} {r.stderr}")
mute.close()
del BASE["bot"]
time.sleep(1)
T.check("RA5 nothing is left running after it", left_alive() == [], f"{left_alive()}")

# RA6
spec_env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(tmp / "none.json")}
d = subprocess.run([sys.executable, "-c", "import importlib.util as u,json,sys;s=u.spec_from_file_location('c',sys.argv[1]);m=u.module_from_spec(s);s.loader.exec_module(m);"
                    "print(json.dumps(m.load(warn=False)['recap']))", str(T.SCRIPTS / "cm-config.py")], capture_output=True, text=True, env=spec_env)
dflt = json.loads(d.stdout) if d.returncode == 0 and d.stdout.strip() else {}
T.check("RA6 defaults: recap.max_s 150, recap.summary_timeout_s 60, recap.send_max_s 420",
        dflt.get("max_s") == 150 and dflt.get("summary_timeout_s") == 60 and dflt.get("send_max_s") == 420, f"{dflt} {d.stderr[-300:]}")
conf(summary_timeout_s=30, max_s=40)
(state / "recap-summaries" / "09_09_2026.json").unlink(missing_ok=True)
before = n_calls()
r, took = recap(good, wait=20)
T.check("RA6 with a model that answers everything is as before: one call, the summary in the recap, no warning",
        r.returncode == 0 and n_calls() == before + 1 and "alfa: Test di alfa sistemati" in r.stdout and "beta: Pagina di beta pubblicata" in r.stdout and not r.stderr.strip(),
        f"{r.returncode} {n_calls() - before} {r.stdout} {r.stderr}")

# RA7
last_call = [x for x in calls.read_text().splitlines() if x.startswith("-p ")][-1] if calls.exists() else ""
tail = calls.read_text().rstrip().splitlines()[-1] if calls.exists() else ""
T.check("RA7 the model starts light: --strict-mcp-config, no tools, no slash commands, no session on disk, after --model and --max-turns 1",
        "--model haiku --max-turns 1 --strict-mcp-config --tools  --disable-slash-commands --no-session-persistence" in tail, tail[-200:])
old_cli = tmp / "claude-old.sh"
old_cli.write_text(f"""#!/bin/sh
echo "$@" >> "{calls}"
case " $* " in *" --strict-mcp-config "*) echo "error: unknown option '--strict-mcp-config'" >&2; exit 1 ;; esac
echo '{{"alfa": {{"fatto": "Test di alfa sistemati", "prossimo": ""}}, "beta": {{"fatto": "Pagina di beta pubblicata", "prossimo": ""}}}}'
""")
old_cli.chmod(0o755)
(state / "recap-summaries" / "09_09_2026.json").unlink(missing_ok=True)
before = n_calls()
r, took = recap(old_cli, wait=20)
T.check("RA7 a CLI that does not know those options and exits with an error: asked again without them, and the summary comes",
        r.returncode == 0 and n_calls() == before + 2 and "alfa: Test di alfa sistemati" in r.stdout and "riassunto del modello" not in r.stderr,
        f"{r.returncode} {n_calls() - before} {r.stdout} {r.stderr}")

# RA8: --send. Il modello ci mette 5 s: troppo per i 2 s del recap a mano, abbastanza per quello del cron
slow = tmp / "claude-slow.sh"
slow.write_text(f"""#!/bin/sh
echo "$@" >> "{calls}"
sleep 5
echo '{{"alfa": {{"fatto": "Test di alfa sistemati", "prossimo": ""}}, "beta": {{"fatto": "Pagina di beta pubblicata", "prossimo": ""}}}}'
""")
slow.chmod(0o755)
(state / "recap-summaries" / "09_09_2026.json").unlink(missing_ok=True)
conf(summary_timeout_s=2, max_s=40, send_max_s=40)
r1, took1 = recap(slow, wait=30)
before = n_calls()
r2, took2 = recap(slow, "--send", wait=40)   # senza token di Telegram: il recap esce, l'invio dice che manca (uscita 1)
T.check("RA8 by hand the 5 s model is cut at 2 s (last messages, remembered); with --send right after it is asked again and has the time left: the summary is there",
        r1.returncode == 0 and "alfa: test verdi su alfa" in r1.stdout and "non è arrivato in 2 s" in r1.stderr
        and n_calls() == before + 1 and "alfa: Test di alfa sistemati" in r2.stdout and "non è arrivato" not in r2.stderr and took2 >= 5,
        f"{r1.returncode} {r1.stderr} | {n_calls() - before} {took2:.1f}s {r2.stdout} {r2.stderr}")
r3, took3 = recap(slow, wait=30)
T.check("RA8 the summary that came with --send stays in the day's cache: the recap asked by hand afterwards has it at once, with no call",
        r3.returncode == 0 and "alfa: Test di alfa sistemati" in r3.stdout and n_calls() == before + 1 and took3 < 3, f"{took3:.1f}s {r3.stdout} {r3.stderr}")

T.rm(str(tmp))
T.finish()
