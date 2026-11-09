#!/usr/bin/env python3
"""I Monitoraggi: valori raccolti da script senza modello, in un formato unico, mandati all'app con la forma del
grafico; una soglia superata apre una voce in «Dalla master» (09/11/2026, supervisor-monitoraggi-09-11, contratto 1.132).
Configurazione, monitoraggi e suggerimenti in una cartella temporanea; nessun relay vero, nessun talk.

MO1 add, point, list, show: una cartella per monitoraggio con def.json e points.jsonl; la scheda ha valore e variazione
    gia' formattati (1.749 €, −10% dall'8/11; «−1 in 7 giorni»), le serie, la fonte, l'esempio, il link #/monitor/<id>
MO2 il catalogo chiuso: una forma fuori catalogo e' rifiutata con l'elenco; meter senza --max rifiutato; status
    accetta si'/no e conta i giorni; bars somma i valori di ogni giorno; table da' le righe; al massimo 60 punti per serie
MO3 soglie: --below 1800 --series yoga apre UNA voce in «Dalla master» (kind monitor, key monitor:<id>, link), stato
    watch; due valori sotto soglia = sempre una voce; il valore rientra = voce tolta (resolved), stato ok; --new-min
    scatta sul minimo di tutta la storia; `suggest fill` non tocca la voce
MO4 verdict: la frase di chi raccoglie va nella scheda e nella voce; `line` stampa una riga breve col link
MO5 il relay: /state.monitors {n, watch, at} (assente senza monitoraggi); op monitors da execute (con arg: una scheda); id
    sconosciuto o non valido = errore; la voce in state.suggestions porta link
MO6 remove: cartella tolta e voce chiusa; un id non valido e' rifiutato prima di toccare il disco
MO7 gli esempi di cmd-result-sample (…1310 monitors, …1311 monitors con arg, …1312 sconosciuto) hanno i campi delle risposte vere
"""
import datetime as dt
import importlib.util
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
talklog = tmp / "talk.log"
fake = tmp / "fake-cm"
fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {talklog}\nexit 0\n")
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_TASKS_DB": str(tmp / "t.db"),
            "CM_MONITORS": str(tmp / "monitors"), "CM_SUGGESTIONS": str(tmp / "suggestions.json"), "CM_TASK_TALK_BIN": str(fake),
            "CM_RELAY_NO_PUSH": "1", "CM_RELAY_CM": str(fake), "CM_TALK_CM": str(fake), "TMUX_TMPDIR": str(tmp / "run"),
            "CM_TMUX_ARGS": "-L monitor-verify", "XDG_RUNTIME_DIR": str(tmp / "run"), "TZ": "Europe/Rome"})
os.environ.clear()
os.environ.update(ENV)
time.tzset()
CLI = str(T.SCRIPTS / "supervisor")


def mo(*a):
    return subprocess.run([CLI, "monitor", *a], capture_output=True, text=True, env=ENV, timeout=60)


def show(mid):
    r = mo("show", mid, "--json")
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {"err": r.stdout + r.stderr}


def sug():
    try:
        return json.loads((tmp / "suggestions.json").read_text())
    except (OSError, ValueError):
        return {}


def ts(day, hour=9):
    return str(int(dt.datetime(2026, 11, day, hour).timestamp()))


# MO1
r_add = mo("add", "prezzi", "--title", "Prezzi portatili", "--form", "multiples", "--unit", "€", "--series", "yoga,zen",
           "--source", "negozi affidabili", "--every", "3h")
for day, y, z in ((8, 1940, 1300), (12, 1900, 1290), (20, 1820, 1250), (27, 1749, 1280)):
    mo("point", "prezzi", "--series", "yoga", str(y), "--ts", ts(day))
    mo("point", "prezzi", "--series", "zen", str(z), "--ts", ts(day))
c1 = show("prezzi")
d1 = json.loads((tmp / "monitors" / "prezzi" / "def.json").read_text()) if (tmp / "monitors" / "prezzi" / "def.json").is_file() else {}
lines1 = (tmp / "monitors" / "prezzi" / "points.jsonl").read_text().splitlines() if (tmp / "monitors" / "prezzi" / "points.jsonl").is_file() else []
T.check("MO1 add and point: monitors/prezzi/def.json (id, title, unit, form, series, source, every) and points.jsonl {ts, series, value}",
        r_add.returncode == 0 and d1.get("form") == "multiples" and d1.get("series") == ["yoga", "zen"] and d1.get("source") == "negozi affidabili"
        and len(lines1) == 8 and json.loads(lines1[0]) == {"ts": int(ts(8)), "series": "yoga", "value": 1940.0}, f"{r_add.stdout}{r_add.stderr} {d1} {lines1[:2]}")
T.check("MO1 the card: value and delta already formatted (1.749 €, −3,9% in 7 days for a currency), two series with their points, link #/monitor/prezzi",
        c1.get("value") == "1.749 €" and c1.get("delta") == "−3,9% in 7 giorni" and c1.get("delta_pct") == -3.9 and c1.get("form") == "multiples"
        and [s["name"] for s in c1.get("series") or []] == ["yoga", "zen"] and c1["series"][1]["last_text"] == "1.280 €"
        and c1["series"][0]["points"][0] == [int(ts(8)), 1940.0] and c1.get("link") == WEB + "/#/monitor/prezzi" and c1.get("status") == "ok"
        and c1.get("example") is False and c1.get("source") == "negozi affidabili" and c1.get("at") == int(ts(27)), json.dumps(c1)[:900])
mo("add", "adozione", "--title", "Adozione dei plugin", "--form", "tile", "--unit", "utenti", "--example")
for day, v in ((1, 129), (2, 131), (9, 130)):
    mo("point", "adozione", str(v), "--ts", ts(day, 7))
c1b = show("adozione")
mo("add", "corto", "--title", "Corto", "--form", "line", "--unit", "€")
mo("point", "corto", "100", "--ts", ts(8))
mo("point", "corto", "90", "--ts", ts(10))
c1c = show("corto")
r_list = mo("list")
T.check("MO1 a tile: «−1 in 7 giorni» against the value of 7 days before, example true; a short history: «−10% dall'8/11»; list shows them",
        c1b.get("value") == "130 utenti" and c1b.get("delta") == "−1 in 7 giorni" and c1b.get("example") is True
        and c1c.get("delta") == "−10% dall'8/11" and r_list.returncode == 0 and "adozione" in r_list.stdout and "prezzi" in r_list.stdout,
        f"{c1b.get('value')} {c1b.get('delta')} {c1c.get('delta')} {r_list.stdout}")

# MO2
r_bad = mo("add", "torta", "--title", "Torta", "--form", "pie")
r_meter = mo("add", "disco", "--title", "Disco", "--form", "meter", "--unit", "GB")
r_meter2 = mo("add", "disco", "--title", "Disco", "--form", "meter", "--unit", "GB", "--max", "100")
mo("point", "disco", "46", "--ts", ts(9))
mo("add", "build", "--title", "Build verde", "--form", "status")
for day, v in ((5, "no"), (6, "si"), (7, "ok"), (8, "1")):
    mo("point", "build", v, "--ts", ts(day))
r_st = mo("point", "build", "forse")
mo("add", "visite", "--title", "Visite", "--form", "bars")
for h, v in ((8, 3), (12, 4), (18, 5)):
    mo("point", "visite", str(v), "--ts", ts(8, h))
mo("point", "visite", "2", "--ts", ts(9, 10))
mo("add", "stelle", "--title", "Stelle", "--form", "table")
for name, v in (("chrome-bridge", 7), ("fable-director", 4), ("chrome-bridge", 8)):
    mo("point", "stelle", "--series", name, str(v))
mo("add", "fitto", "--title", "Fitto", "--form", "line")
t0 = int(ts(1, 0))
with open(tmp / "monitors" / "fitto" / "points.jsonl", "a") as f:
    for i in range(200):
        f.write(json.dumps({"ts": t0 + i * 3600, "series": "", "value": float(i)}) + "\n")
cm_, cb, cv, ct, cf = show("disco"), show("build"), show("visite"), show("stelle"), show("fitto")
T.check("MO2 a form outside the catalog is refused naming the catalog; meter without --max refused, with it: pct and limit",
        r_bad.returncode == 2 and "tile, line, multiples, bars, meter, table, status" in r_bad.stderr and not (tmp / "monitors" / "torta").exists()
        and r_meter.returncode == 2 and "--max" in r_meter.stderr and r_meter2.returncode == 0 and cm_.get("pct") == 46.0 and cm_.get("limit") == "100 GB"
        and cm_.get("value") == "46 GB", f"{r_bad.stderr} {r_meter.stderr} {cm_}")
T.check("MO2 status: si/ok/1 are yes, «forse» refused, «sì da 3 giorni»; bars: the values of a day summed; table: the last value of each row",
        cb.get("value") == "sì" and cb.get("delta") == "da 3 giorni" and r_st.returncode == 2
        and [p[1] for p in cv["series"][0]["points"]] == [12.0, 2.0]
        and ct.get("rows") == [{"name": "chrome-bridge", "value": 8.0, "text": "8"}, {"name": "fable-director", "value": 4.0, "text": "4"}],
        f"{cb.get('value')} {cb.get('delta')} {r_st.returncode} {cv.get('series')} {ct.get('rows')}")
pts = cf["series"][0]["points"] if cf.get("series") else []
T.check("MO2 200 values → at most 60 points, the last one kept, n=200, min and max of the whole history",
        0 < len(pts) <= 60 and pts[-1] == [t0 + 199 * 3600, 199.0] and cf["series"][0]["n"] == 200 and cf["series"][0]["min"] == 0.0
        and cf["series"][0]["max"] == 199.0, f"{len(pts)} {pts[-1:]} {cf.get('series', [{}])[0].get('n')}")

# MO3
r_al = mo("alert", "prezzi", "--below", "1800", "--series", "yoga")
c3 = show("prezzi")
items3 = [x for x in sug().get("items") or [] if x.get("key") == "monitor:prezzi"]
mo("point", "prezzi", "--series", "yoga", "1760", "--ts", ts(28))
items3b = [x for x in sug().get("items") or [] if x.get("key") == "monitor:prezzi"]
T.check("MO3 --below 1800 --series yoga: status watch, ONE item in «Dalla master» (kind monitor, source script, link), still one after another value under it",
        r_al.returncode == 0 and c3.get("status") == "watch" and c3["hits"] == [{"kind": "below", "value": 1800.0, "series": "yoga", "text": "sotto 1.800 € · yoga"}]
        and len(items3) == 1 and items3[0]["kind"] == "monitor" and items3[0]["source"] == "script" and items3[0]["link"] == WEB + "/#/monitor/prezzi"
        and "sotto 1.800 €" in items3[0]["text"] and "supervisor monitor show prezzi" in items3[0]["prompt"]
        and len(items3b) == 1 and items3b[0]["id"] == items3[0]["id"], f"{r_al.stdout}{r_al.stderr} {c3.get('hits')} {items3} {items3b}")
SG = None
spec = importlib.util.spec_from_file_location("cm_suggest_mo", T.SCRIPTS / "cm-suggest.py")
SG = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SG)
fill = SG.fill(state={"sessions": [], "attention": []}, commitments=[])
kept = [x for x in sug().get("items") or [] if x.get("key") == "monitor:prezzi"]
mo("point", "prezzi", "--series", "yoga", "1850", "--ts", ts(29))
c3c = show("prezzi")
gone = [x for x in sug().get("items") or [] if x.get("key") == "monitor:prezzi"]
hist = [h for h in sug().get("history") or [] if h.get("key") == "monitor:prezzi"]
T.check("MO3 suggest fill leaves the item alone; the value back above 1800 → item removed as resolved, status ok",
        fill is not None and len(kept) == 1 and not gone and hist and hist[-1]["action"] == "resolved" and c3c.get("status") == "ok",
        f"{fill} {kept} {gone} {hist} {c3c.get('status')}")
mo("alert", "corto", "--new-min")
mo("point", "corto", "95", "--ts", ts(11))
nm1 = show("corto")["status"]
mo("point", "corto", "85", "--ts", ts(12))
nm2 = show("corto")
T.check("MO3 --new-min: not hit at 95 (min 90), hit at 85 (lowest of the whole history) with «un nuovo minimo»",
        nm1 == "ok" and nm2["status"] == "watch" and nm2["hits"][0]["text"] == "un nuovo minimo", f"{nm1} {nm2.get('hits')}")

# MO4
r_v = mo("verdict", "adozione", "Settimana stabile: 130 utenti nello Store (−1 in 7 giorni).")
r_l = mo("line", "adozione")
T.check("MO4 verdict: the collector's sentence in the card; line = «Titolo: frase <link>» on one line",
        r_v.returncode == 0 and show("adozione").get("verdict") == "Settimana stabile: 130 utenti nello Store (−1 in 7 giorni)."
        and r_l.stdout.strip() == f"Adozione dei plugin: Settimana stabile: 130 utenti nello Store (−1 in 7 giorni). {WEB}/#/monitor/adozione"
        and len(r_l.stdout.strip().splitlines()) == 1, r_v.stderr + r_l.stdout)
r_l2 = mo("line", "disco")
T.check("MO4 without a verdict line gives the value and the delta", r_l2.stdout.strip() == f"Disco: 46 GB {WEB}/#/monitor/disco", r_l2.stdout)

# MO5
sp = importlib.util.spec_from_file_location("cm_relay_mo", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
S = RL.S
summ = RL.monitors_summary()
st = S.build_state({"monitors": summ}, 1)
st0 = S.build_state({}, 1)
ok_l, t_l = RL.execute({"op": "monitors", "device": "phone", "by": "maintainer"})
ok_o, t_o = RL.execute({"op": "monitors", "arg": "prezzi", "device": "phone", "by": "maintainer"})
ok_u, t_u = RL.execute({"op": "monitors", "arg": "non-esiste", "device": "phone", "by": "maintainer"})
ok_b, t_b = RL.execute({"op": "monitors", "arg": "../x", "device": "phone", "by": "maintainer"})
items_l = json.loads(t_l)["items"] if ok_l else []
T.check("MO5 /state.monitors {n, watch, at} (corto is watching); absent without monitors; op monitors lists every card",
        st.get("monitors") == {"n": 8, "watch": 1, "at": summ["at"]} and summ["at"] and "monitors" not in st0
        and ok_l and len(items_l) == 8 and {i["id"] for i in items_l} >= {"prezzi", "adozione", "disco"}, f"{st.get('monitors')} {ok_l} {t_l[:200]}")
T.check("MO5 op monitors with arg gives one card; an unknown id or a bad one is an error; one passive op only (the ops list lives in the 8 KB)",
        ok_o and json.loads(t_o)["id"] == "prezzi" and not ok_u and "non c'è più" in t_u and not ok_b and "monitors" in RL.OPS and "monitor" not in RL.OPS
        and "monitors" in RL.PASSIVE_OPS, f"{t_o[:120]} {t_u} {t_b}")
sug_state = SG.for_state()
mine = [x for x in sug_state if x.get("key") == "monitor:corto"]
via_relay = [S.suggestion_of(x) for x in sug_state if x.get("key") == "monitor:corto"]
T.check("MO5 the item in state.suggestions carries link (cm-suggest.for_state and the relay's suggestion_of); the others have none",
        len(mine) == 1 and mine[0].get("link") == WEB + "/#/monitor/corto" and via_relay[0].get("link") == WEB + "/#/monitor/corto"
        and all("link" not in S.suggestion_of({"id": "s-x", "kind": "idea", "key": "k", "text": "t"}) for _ in [0]), f"{mine} {via_relay}")

# MO6
r_rm = mo("remove", "corto")
r_rm2 = mo("remove", "../prezzi")
left = [x for x in sug().get("items") or [] if x.get("key") == "monitor:corto"]
T.check("MO6 remove: the folder gone and its item closed; a bad id refused, nothing touched",
        r_rm.returncode == 0 and not (tmp / "monitors" / "corto").exists() and not left and r_rm2.returncode == 2
        and (tmp / "monitors" / "prezzi" / "def.json").is_file(), f"{r_rm.stdout}{r_rm.stderr} {r_rm2.stderr} {left}")

# MO7
SAMPLE = json.loads((T.ROOT / "tests" / "fixtures" / "relay" / "cmd-result-sample.json").read_text())
smp = {r["id"].split("-")[1]: r for r in SAMPLE["result"]}
cmds = {c["id"].split("-")[1]: c for c in SAMPLE["cmd"]}
s10, s11, s12 = smp.get("1310") or {}, smp.get("1311") or {}, smp.get("1312") or {}
real = json.loads(t_o) if ok_o else {}
try:
    b10, b11 = json.loads(s10.get("text") or "{}"), json.loads(s11.get("text") or "{}")
except ValueError:
    b10, b11 = {}, {}
T.check("MO7 the samples …1310 monitors, …1311 monitor, …1312 unknown: ok/err and the same keys as the real answers",
        s10.get("ok") is True and set(b10) == {"at", "items"} and b10["items"] and set(b10["items"][0]) == set(real)
        and s11.get("ok") is True and set(b11) == set(real) and set(b11["series"][0]) == set(real["series"][0])
        and s12.get("ok") is False and "non c'è più" in (s12.get("text") or "")
        and [cmds.get(k, {}).get("op") for k in ("1310", "1311", "1312")] == ["monitors", "monitors", "monitors"] and all(isinstance(x.get("at"), int) for x in (s10, s11, s12)),
        f"{sorted(set(b11) ^ set(real))} {s10.get('ok')} {s12}")

T.check("no talk left the test", not talklog.exists() or talklog.read_text() == "", talklog.read_text() if talklog.exists() else "")
T.rm(tmp)
T.finish()
