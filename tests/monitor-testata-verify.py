#!/usr/bin/env python3
"""La testata di un monitoraggio con piu' serie e «ultimo giro» (15/11/2026, supervisor-monitor-testata-15-11, contratto 1.147).

Il caso vero: un monitoraggio «multiples» del consumo di un servizio, tre serie in percentuale del limite e una soglia
sola, sopra l'80%. `monitor show` diceva «da guardare: 0,1%», la prima serie (dati conservati), mentre la soglia la
passava un'altra, al 134%. E «ultimo giro: riuscito 11/11 14:44» restava fermo mentre i valori arrivavano ogni giorno
da un cron che scrive i punti senza `monitor refresh`.

MT1  la scheda: value e delta sono della serie che passa di piu' la soglia, value_series ne dice il nome; hits la mette
     per prima e il testo di ogni soglia scattata dice la serie
MT2  `show`: «da guardare: <serie> 134%»; `line` e `list` lo stesso valore
MT3  «Bacheca»: la voce dice la serie peggiore e il suo valore; con una frase di chi raccoglie che non la nomina,
     la serie e il valore vanno in coda alla frase
MT4  senza soglie scattate la testata e' la serie piu' vicina alla soglia; senza soglie col numero la prima serie (come
     prima); con una sola serie value_series e' null
MT5  due serie oltre la soglia: in testa quella che la passa di piu' in proporzione, anche con una soglia «sotto»
MT6  «ultimo giro»: un punto scritto dopo la fine dell'ultimo giro chiesto (il cron) sposta l'ultimo giro a
     quell'ora, riuscito, external true; `show` lo dice; retry_at resta quello del giro chiesto
MT7  un giro chiesto dopo vale di nuovo lui (niente external); un giro fallito dopo l'ultimo punto resta fallito
"""
import datetime as dt
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
cfg = tmp / "config.json"
WEB = "https://app.example.test"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"p": {"config_dir": str(home / ".claude")}},
                           "default_account": "p", "workspace": {"root": str(tmp), "root_session_name": "tst-capo"},
                           "relay": {"dir": str(tmp / "relay"), "web_app_url": WEB + "/"}}))
(tmp / "relay").mkdir()
(tmp / "run").mkdir()
fake = tmp / "fake-cm"
fake.write_text("#!/bin/sh\nexit 0\n")
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASKS_DB": str(tmp / "t.db"),
            "CM_MONITORS": str(tmp / "monitors"), "CM_SUGGESTIONS": str(tmp / "suggestions.json"), "CM_TASK_TALK_BIN": str(fake),
            "CM_RELAY_NO_PUSH": "1", "CM_RELAY_CM": str(fake), "CM_TALK_CM": str(fake), "TMUX_TMPDIR": str(tmp / "run"),
            "CM_TMUX_ARGS": "-L monitor-testata-verify", "XDG_RUNTIME_DIR": str(tmp / "run"), "TZ": "Europe/Rome"})
os.environ.clear()
os.environ.update(ENV)
time.tzset()
CLI = str(T.SCRIPTS / "supervisor")
MON = tmp / "monitors"


def mo(*a):
    return subprocess.run([CLI, "monitor", *a], capture_output=True, text=True, env=ENV, timeout=60)


def show(mid):
    r = mo("show", mid, "--json")
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {"err": r.stdout + r.stderr}


def sug(key):
    try:
        items = json.loads((tmp / "suggestions.json").read_text()).get("items") or []
    except (OSError, ValueError):
        items = []
    return next((x for x in items if x.get("key") == key), {})


def ts(day, hour=9):
    return str(int(dt.datetime(2026, 11, day, hour).timestamp()))


# il caso vero: tre serie in % del limite, una soglia sola sopra l'80%
SER = ("Dati conservati", "Scaricati nel mese", "Connessioni")
mo("add", "consumo", "--title", "Consumo del servizio", "--form", "multiples", "--unit", "%", "--series", ",".join(SER))
mo("alert", "consumo", "--above", "80")
for day, vals in ((8, (0.1, 71, 12)), (11, (0.1, 96, 14)), (15, (0.1, 134, 9))):
    for name, v in zip(SER, vals):
        mo("point", "consumo", "--series", name, str(v), "--ts", ts(day))
c = show("consumo")
T.check("MT1 the card: value and delta are those of the series that passes the threshold (134%), not of the first one (0,1%), and value_series names it",
        c.get("status") == "watch" and c.get("value") == "134%" and c.get("value_series") == "Scaricati nel mese" and "63" in str(c.get("delta")),
        f"{c.get('status')} {c.get('value')} {c.get('value_series')} {c.get('delta')} {c.get('err')}")
T.check("MT1 hits: one, on that series, and its text names the series although the threshold has none",
        [(h.get("series"), h.get("text")) for h in c.get("hits") or []] == [("Scaricati nel mese", "sopra 80% · Scaricati nel mese")], str(c.get("hits")))
out = mo("show", "consumo").stdout
T.check("MT2 show: «da guardare: Scaricati nel mese 134%», never «da guardare: 0,1%»",
        "da guardare: Scaricati nel mese 134%" in out and "da guardare: 0,1%" not in out, out)
line, lst = mo("line", "consumo").stdout, mo("list").stdout
T.check("MT2 line names the series with its value; list shows 134% on the monitor's row",
        "Consumo del servizio: Scaricati nel mese 134%" in line and any("consumo" in x and "134%" in x and x.startswith("!") for x in lst.splitlines()), line + lst)
s3 = sug("monitor:consumo")
T.check("MT3 «Board»: the entry says the worst series and its value, in the text and in the prompt",
        "Scaricati nel mese" in s3.get("text", "") and "134%" in s3.get("text", "") and "0,1%" not in s3.get("text", "")
        and "ultimo valore Scaricati nel mese 134%" in s3.get("prompt", ""), str(s3))
mo("verdict", "consumo", "Consumo oltre il limite del piano gratuito")
s3b = sug("monitor:consumo")
T.check("MT3 a collector's sentence that does not name the series gets the series and its value at the end",
        s3b.get("text") == "Consumo oltre il limite del piano gratuito · Scaricati nel mese 134%", str(s3b.get("text")))
mo("verdict", "consumo", "Scaricati nel mese al 134% del limite")
T.check("MT3 a sentence that names it stays as written", sug("monitor:consumo").get("text") == "Scaricati nel mese al 134% del limite", str(sug("monitor:consumo").get("text")))

# MT4: senza soglie scattate
mo("add", "calmo", "--title", "Calmo", "--form", "multiples", "--unit", "%", "--series", "a,b,c")
for name, v in (("a", 5), ("b", 70), ("c", 30)):
    mo("point", "calmo", "--series", name, str(v), "--ts", ts(14))
c4a = show("calmo")
mo("alert", "calmo", "--above", "80")
c4b = show("calmo")
mo("add", "solo", "--title", "Solo", "--form", "line", "--unit", "%", "--series", "a")
mo("alert", "solo", "--above", "80")
mo("point", "solo", "--series", "a", "90", "--ts", ts(14))
c4c = show("solo")
T.check("MT4 no numeric threshold: the first series, as before; with one, and nothing hit: the series closest to it (b, 70%), status ok",
        c4a.get("value") == "5%" and c4a.get("value_series") == "a" and c4b.get("value") == "70%" and c4b.get("value_series") == "b" and c4b.get("status") == "ok",
        f"{c4a.get('value')} {c4a.get('value_series')} | {c4b.get('value')} {c4b.get('value_series')} {c4b.get('status')}")
T.check("MT4 a monitor with one series: value_series null, and the hit's text does not repeat the series",
        "value_series" in c4c and c4c.get("value_series") is None and c4c.get("value") == "90%" and [h["text"] for h in c4c.get("hits") or []] == ["sopra 80%"],
        f"{c4c.get('value_series')} {c4c.get('value')} {c4c.get('hits')}")

# MT5: due serie oltre, e una soglia «sotto»
mo("add", "due", "--title", "Due", "--form", "multiples", "--series", "x,y,z")
mo("alert", "due", "--above", "100", "--series", "x")
mo("alert", "due", "--below", "10", "--series", "y")
for name, v in (("x", 120), ("y", 2), ("z", 50)):
    mo("point", "due", "--series", name, str(v), "--ts", ts(14))
c5 = show("due")
T.check("MT5 two series past their thresholds: the head is the one further past in proportion (y: 2 against «below 10» = 80%; x: 120 against 100 = 20%)",
        c5.get("value_series") == "y" and c5.get("value") == "2" and [h["series"] for h in c5.get("hits") or []] == ["y", "x"],
        f"{c5.get('value_series')} {c5.get('value')} {[h.get('series') for h in c5.get('hits') or []]}")

# MT6: l'ultimo giro
mo("set", "consumo", "--refresh", "echo fatto", "--refresh-min-interval", "600")
old = int(time.time()) - 4 * 86400
(MON / "consumo" / "refresh.json").write_text(json.dumps({"state": "ok", "started": old - 3, "ended": old, "rc": 0, "message": "fatto"}))
pf = MON / "consumo" / "points.jsonl"
os.utime(pf, (old - 60, old - 60))
r6a = show("consumo").get("refresh") or {}
T.check("MT6 points written before the end of the asked run: the last run is the asked one (ok, its end, no external)",
        r6a.get("state") == "ok" and r6a.get("ended") == old and r6a.get("message") == "fatto" and "external" not in r6a, str(r6a))
cron = int(time.time()) - 3600
with open(pf, "a") as f:   # il cron scrive i punti direttamente, senza `monitor refresh`
    f.write(json.dumps({"ts": cron, "series": "Scaricati nel mese", "value": 135.0}) + "\n")
os.utime(pf, (cron, cron))
c6 = show("consumo")
r6 = c6.get("refresh") or {}
T.check("MT6 a point written after it (the cron): the last run moves to that time, ok, external true; retry_at stays the asked run's",
        r6.get("state") == "ok" and r6.get("ended") == cron and r6.get("external") is True and r6.get("rc") is None and r6.get("message") is None
        and r6.get("retry_at") == old + 600 and c6.get("value") == "135%", f"{r6} {c6.get('value')}")
out6 = mo("show", "consumo").stdout
when = dt.datetime.fromtimestamp(cron).strftime("%d/%m %H:%M")
stale = dt.datetime.fromtimestamp(old).strftime("%d/%m %H:%M")
T.check("MT6 show: «ultimo giro: riuscito <the cron's time>», saying the values came from outside; the old date is gone",
        f"ultimo giro: riuscito {when}" in out6 and "da fuori" in out6 and f"riuscito {stale}" not in out6, out6)

# MT7
r7 = mo("refresh", "consumo", "--wait")
a7 = show("consumo").get("refresh") or {}
T.check("MT7 a run asked afterwards counts again: ok, its message, no external", r7.returncode == 0 and a7.get("state") == "ok" and a7.get("message") == "fatto"
        and a7.get("rc") == 0 and "external" not in a7 and a7.get("ended", 0) > cron, f"{r7.returncode} {r7.stdout}{r7.stderr} {a7}")
now = int(time.time())
(MON / "consumo" / "refresh.json").write_text(json.dumps({"state": "error", "started": now + 5, "ended": now + 6, "rc": 3, "message": "rotto"}))
b7 = show("consumo").get("refresh") or {}
T.check("MT7 a failed run after the last point stays failed", b7.get("state") == "error" and b7.get("rc") == 3 and b7.get("message") == "rotto" and "external" not in b7, str(b7))

T.rm(str(tmp))
T.finish()
