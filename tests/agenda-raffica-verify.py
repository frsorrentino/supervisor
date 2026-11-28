#!/usr/bin/env python3
"""Verifica supervisor-agenda-raffica-13-11: senza relay.agenda_file il relay offriva comunque l'op agenda e rispondeva
«no agenda file»; un client che la ripeteva subito (10.512 richieste in 60 s) teneva il demone al 60% di CPU e faceva
ruotare relay.log ogni due minuti, con le righe FAILED perse.

AR1 senza agenda_file lo stato non offre agenda ne' agenda_set in `ops`; con un file che c'e' le offre
AR2 un client finto ripete `agenda` 300 volte sull'API locale: ogni richiesta ha la sua risposta, ma il relay la esegue
    al piu' una volta al secondo (le altre ricevono l'esito della prima, senza eseguire)
AR3 un altro client, o un argomento diverso, non e' limitato da quelle
AR4 il log non cresce con la raffica: le righe uguali di fila sono scritte una volta, col conteggio
AR6 la stessa lettura chiesta di nuovo mentre la prima e' ancora in corso la aspetta: un'esecuzione, a tutti la risposta
AR7 dopo un comando dell'utente la stessa lettura si esegue di nuovo, anche entro il secondo
AR5 gli errori (FAILED, → ERR) stanno anche in un file a parte, uno per giorno, con i ripetuti contati; i file piu'
    vecchi di ERR_LOG_DAYS giorni escono, quelli dentro restano; relay.log puo' ruotare senza portarli via. La parte in
    processo parte da un'ora data (le 12:00 di oggi), cosi' e' verde a ogni ora vera, anche fra le 23:38 e mezzanotte
"""
import importlib.util as _ilu
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
rdir = tmp / "relay"
cfg = tmp / "config.json"
_s = socket.socket(); _s.bind(("127.0.0.1", 0)); port = _s.getsockname()[1]; _s.close()
webdir = tmp / "webapp"
webdir.mkdir()
(webdir / "index.html").write_text("<!doctype html><title>t</title>")
agenda = tmp / "agenda.tsv"
agenda.write_text("aperto\tprogetto\towner\tUna voce\t\n")


def write_cfg(**relay):
    cfg.write_text(json.dumps({
        "language": "en", "state_dir": str(tmp / "state"), "workspace": {"root": str(home / "ws")},
        "accounts": {"personal": {"config_dir": str(home / ".claude")}}, "default_account": "personal",
        "relay": dict({"dir": str(rdir), "enabled": True, "web": {"port": port, "enabled": True, "dir": str(webdir)}}, **relay)}))


ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
       "CM_CRONTAB_CMD": "false", "LANG": "C.UTF-8"}


def relay(*a):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-relay.py"), *a], capture_output=True, text=True, env=ENV, timeout=60)


def call(path, tok, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (OSError, ValueError):
        return 0, None


def stop_daemon():
    try:
        pid = int((rdir / "serve.pid").read_text())
        os.kill(pid, 15)
        T.wait_until(lambda: not T.proc_alive(pid), 10)
    except (OSError, ValueError):
        pass


def logtext():
    try:
        return (rdir / "relay.log").read_text()
    except OSError:
        return ""


def ran(text, op="agenda"):
    """Le righe di un comando eseguito davvero: «cmd <id>: agenda … → ok|ERR …»."""
    return [x for x in text.splitlines() if f": {op} " in x and " → " in x]


def start():
    r = relay("ensure")
    tok = ""
    if T.wait_until(lambda: (rdir / "web-token").exists(), 20):
        tok = (rdir / "web-token").read_text().strip()
    T.wait_until(lambda: call("/api/state", tok)[0] == 200 and call("/api/state", tok)[1], 90)
    return tok, r


# ---------------------------------------------------------------- AR1-AR4: la raffica, senza agenda configurata
write_cfg()
tok, r0 = start()
st, state = call("/api/state", tok)
ops = (state or {}).get("ops") or []
T.check("AR1 no relay.agenda_file → the state offers neither agenda nor agenda_set in ops (the other ops stay)",
        st == 200 and "agenda" not in ops and "agenda_set" not in ops and "transcript" in ops and "prompt" in ops, f"{st} {ops[:6]} {r0.stdout[-200:]}{r0.stderr[-200:]}")

N = 300
size0 = len(logtext())
t0 = time.time()
answers = [call("/api/cmd", tok, {"op": "agenda", "by": "web"}) for _ in range(N)]
took = time.time() - t0
burst = logtext()[size0:]
done = ran(burst)
T.check(f"AR2 a fake client repeats agenda {N} times: every request gets its answer",
        all(s == 200 and isinstance(b, dict) and b.get("ok") is False and b.get("text") for s, b in answers), str(answers[:2]))
T.check("AR2 after the first three the relay runs it at most once a second for the same client, op and argument",
        1 <= len(done) <= int(took) + 2 + 3, f"{len(done)} runs in {took:.1f} s")
T.check("AR2 the repeats get the result of the one that ran, with their own id", len({b.get("text") for _, b in answers if b}) == 1
        and len({b.get("id") for _, b in answers if b}) == N, str({b.get("text") for _, b in answers if b}))

time.sleep(1.2)
n0 = len(ran(logtext()))
call("/api/cmd", tok, {"op": "agenda", "by": "web"})
call("/api/cmd", tok, {"op": "agenda", "by": "other-client"})
call("/api/cmd", tok, {"op": "agenda", "by": "web", "arg": "x"})
T.check("AR3 after the second has passed it runs again; another client and another argument are not limited by it",
        len(ran(logtext())) - n0 == 3, f"{len(ran(logtext())) - n0} runs of 3")

T.check("AR4 the burst does not grow the log: under 4 KB for 300 requests, the equal lines written once with their count",
        len(burst) < 4096 and "ripetuta" in logtext(), f"{len(burst)} bytes | {burst[-300:]}")
# DATA-AR5-begin — tests/data-mezzanotte-verify.py esegue questo tratto con l'orologio a mezzanotte
# 24/11 (supervisor-test-data-24-11): il file e' del giorno in cui il comando e' fallito, fra l'inizio della raffica (t0) e
# adesso; passata la mezzanotte possono essere due, e le righe si contano su tutti
_u5 = time.time()
errs = sorted(rdir.glob("relay-errors-*.log"))
etext = "".join(e.read_text() for e in errs)
_n5 = [f"relay-errors-{d}.log" for d in T.days(t0, _u5)]
T.check("AR5 the failed command is also in the errors file of the day (relay-errors-AAAA-MM-GG.log), a few lines and not 300",
        1 <= len(errs) and all(e.name in _n5 for e in errs) and "agenda" in etext and "ERR" in etext
        and len(etext.splitlines()) <= 8, f"{[e.name for e in errs]} | {etext[-300:]}")
# DATA-AR5-end
stop_daemon()

# ---------------------------------------------------------------- AR1: con l'agenda configurata l'op e' offerta
write_cfg(agenda_file=str(agenda))
(rdir / "local" / "state.json").unlink(missing_ok=True)
tok, _ = start()
st, state = call("/api/state", tok)
ops = (state or {}).get("ops") or []
s1, b1 = call("/api/cmd", tok, {"op": "agenda", "by": "web"})
T.check("AR1 with an agenda file that is there the state offers agenda and agenda_set, and agenda answers with the rows",
        "agenda" in ops and "agenda_set" in ops and s1 == 200 and b1 and b1.get("ok") is True and len(json.loads(b1["text"])["rows"]) == 1, f"{ops[:4]} {b1}")
stop_daemon()

# ---------------------------------------------------------------- AR4-AR5 in processo: conteggio, file per giorno, pulizia
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home)})
spec = _ilu.spec_from_file_location("cm_relay", T.SCRIPTS / "cm-relay.py"); RL = _ilu.module_from_spec(spec); spec.loader.exec_module(RL)
lp = rdir / "relay.log"
lp.write_text("")
for e in rdir.glob("relay-errors-*.log"):
    e.unlink()
# 28/11/2026 (supervisor-ar5-orario-28-11): un'ora data, le 12:00 di oggi, non time.time(). Ogni riga qui porta la sua ora
# (log(now=...)) e AR5 scrive fino a now+1301 s: con now fra le 23:38 e mezzanotte le righe dopo la mezzanotte finivano
# nel file degli errori del giorno dopo, AR5 era rosso e fermava il preflight della release (27/11 alle 23:43). Prova:
# `python3 tests/lib/orologio.py 23:50 tests/agenda-raffica-verify.py` (rosso con la forma di prima, verde con questa)
now = time.mktime(time.localtime()[:3] + (12, 0, 0, 0, 0, -1))
for i in range(50):
    RL.log("serve: la stessa riga", now=now + i * 0.1)
RL.log("serve: un'altra riga", now=now + 6)
lines = lp.read_text().splitlines()
T.check("AR4 50 equal lines in a row → the line once, then one line with the count (49 more), then the next line",
        len(lines) == 3 and lines[0].endswith("serve: la stessa riga") and "49" in lines[1] and "ripetuta" in lines[1] and lines[2].endswith("serve: un'altra riga"), str(lines))
lp.write_text("")
RL.log("serve: riga a", now=now + 10); RL.log("serve: riga b", now=now + 11); RL.log("serve: riga a", now=now + 12)
T.check("AR4 equal lines that are not in a row stay as they are", len(lp.read_text().splitlines()) == 3, lp.read_text())
lp.write_text("")
RL.log("serve: a lungo", now=now + 20)
for i in range(1, 200):
    RL.log("serve: a lungo", now=now + 20 + i)
flushed = [x for x in lp.read_text().splitlines() if "ripetuta" in x]
T.check("AR4 a repeat that goes on is counted in the log about once a minute, not kept in memory until it ends", 2 <= len(flushed) <= 4, lp.read_text())

# AR6-AR7: il limite, in processo, con un'esecuzione finta che conta le chiamate
import threading  # noqa: E402
_runs = []


def _fake_execute(cmd):
    _runs.append(cmd.get("op"))
    time.sleep(float(cmd.get("_sleep") or 0))
    return True, json.dumps({"run": len(_runs)})


RL.execute = _fake_execute
_same = {"op": "done", "by": "web", "_local": True}
_b = [RL.run_cmd(dict(_same, id=f"b{i}")) for i in range(6)]
T.check("AR2 the first three equal commands in a second run as always (a double tap, the same request after a change); the limit starts at the fourth",
        _runs == ["done"] * RL.CMD_BURST == ["done"] * 3 and [r.get("text") for r in _b[3:]] == [_b[2].get("text")] * 3 and len({r.get("text") for r in _b[:3]}) == 3,
        f"{_runs} {[r.get('text') for r in _b]}")
del _runs[:]
RL.CMD_BURST = 1   # AR6 e AR7 guardano la chiave gia' limitata: qui dal secondo comando uguale
RL._RATE.clear()
_res = {}
_slow = {"op": "prove", "by": "web", "_local": True, "_sleep": 0.7}
_ths = [threading.Thread(target=lambda i=i: _res.__setitem__(i, RL.run_cmd(dict(_slow, id=f"c{i}")))) for i in range(3)]
_ths[0].start(); time.sleep(0.15); _ths[1].start(); _ths[2].start()
for _t in _ths:
    _t.join(20)
T.check("AR6 the same read asked again while the first is still running waits for it: one run, every asker gets the result, each under its own id",
        _runs == ["prove"] and len(_res) == 3 and all(r.get("ok") is True and r.get("text") == _res[0].get("text") for r in _res.values())
        and {r.get("id") for r in _res.values()} == {"c0", "c1", "c2"}, f"{_runs} {_res}")
del _runs[:]
_read = {"op": "transcript", "session": "x", "arg": "50", "by": "web", "_local": True}
_r1 = RL.run_cmd(dict(_read, id="r1"))
_r1b = RL.run_cmd(dict(_read, id="r1b"))
_a = RL.run_cmd({"id": "a1", "op": "prompt", "session": "x", "arg": "ciao", "by": "web", "_local": True})
_r2 = RL.run_cmd(dict(_read, id="r2"))
T.check("AR7 a read repeated within the second gets the kept result; after a command of the user the same read runs again, on what the command changed",
        _runs == ["transcript", "prompt", "transcript"] and _r1b.get("text") == _r1.get("text") and _r2.get("text") != _r1.get("text") and _a.get("ok") is True,
        f"{_runs} {_r1} {_r1b} {_r2}")

day = lambda t: time.strftime("%Y-%m-%d", time.localtime(t))  # noqa: E731
for back in (1, RL.ERR_LOG_DAYS - 1, RL.ERR_LOG_DAYS + 2, 40):
    (rdir / f"relay-errors-{day(now - back * 86400)}.log").write_text("vecchio\n")
(rdir / "relay-errors-note.txt").write_text("non e' un file degli errori\n")
for i in range(30):
    RL.log(f"push FAILED ({100 + i * 30} s): [Errno 1] Operation not permitted: '/x/Desktop/progetti'", now=now + 300 + i * 30)
RL.log("cmd 1111-aaaa: agenda  da web (locale) → ERR no agenda file · fila 0.0 s · esecuzione 0.0 s", now=now + 1300)
RL.log("push: 3 sessioni · origine beat", now=now + 1301)
ep = rdir / f"relay-errors-{day(now)}.log"
el = ep.read_text().splitlines() if ep.exists() else []
T.check("AR5 FAILED and → ERR lines go to the errors file of the day, other lines do not; 30 failed pushes that differ only in "
        "their numbers are one line and a count, and the last one's text is kept",
        2 <= len(el) <= 5 and "push FAILED (100 s)" in el[0] and any("ripetut" in x and "970 s" in x for x in el) and "ERR no agenda file" in el[-1]
        and not any("origine beat" in x for x in el), str(el))
left = sorted(p.name for p in rdir.glob("relay-errors-*"))
T.check(f"AR5 errors files older than {RL.ERR_LOG_DAYS} days are removed when a new day's file starts, the others and foreign files stay",
        left == sorted([f"relay-errors-{day(now - b * 86400)}.log" for b in (0, 1, RL.ERR_LOG_DAYS - 1)] + ["relay-errors-note.txt"]), str(left))
lp.write_text("x" * (RL.LOG_MAX_BYTES + 10))
RL.log("serve: dopo la rotazione", now=now + 1400)
T.check("AR5 relay.log rotates as before (relay.log.1) and the errors file is untouched by it",
        (rdir / "relay.log.1").exists() and lp.stat().st_size < 400 and ep.read_text().splitlines()[:len(el)] == el, str(lp.stat().st_size))
T.rm(tmp)
T.finish()
