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
MO8 --new-min e --new-max sono stretti (supervisor-monitor-minimo-stretto-09-11): un solo valore non scatta; un prezzo fermo
    (1.936,95 due volte) non scatta; 1.899 scatta e apre la voce; di nuovo 1.899 non scatta e la voce esce; idem --new-max
MO9 l'aggiornamento a richiesta (supervisor-monitor-aggiorna-10-11, contratto 1.133): `set ID --refresh CMD` scrive
    def.json.refresh {cmd, timeout_s 180, min_interval_s 60}; la scheda ha refreshable e refresh (idle) e MAI il comando;
    set su un id che non c'e', secondi fuori misura, --no-refresh insieme a --refresh: rifiutati; --no-refresh lo toglie
MO10 `refresh ID --wait`: il comando finto scrive un punto con la CLI (SUPERVISOR_MONITOR_ID); stato ok, rc 0, l'ultima
    riga come message, il punto nella scheda, una riga monitor-refresh nel registro; subito dopo: troppo presto (uscita 4)
MO11 il blocco: un giro in sottofondo torna subito con stato running; la seconda richiesta risponde «già in corso»
    (uscita 3) e il comando gira UNA volta; a fine giro stato ok
MO12 tempo scaduto: rc 124, stato error, la frase di supervisor, e il processo del comando non c'e' piu'
MO13 errore: uscita 3 del comando = stato error, rc 3, l'ultima riga di stderr; un `running` senza nessuno sul blocco e'
    letto come giro interrotto; un monitoraggio senza comando: `refresh` rifiutato (uscita 2)
MO14 il relay: op monitor_refresh in OPS e non fra le letture; senza comando dichiarato = errore e niente parte; il testo
    di un comando mandato dall'app (text, cmd, refresh) non viene mai eseguito, gira solo quello di def.json; risponde la
    scheda con running; una seconda richiesta durante il giro = ok, sempre running, un solo giro; troppo presto = errore
MO15 gli esempi …1320 (avviato), …1321 (senza comando), …1322 (troppo presto) hanno i campi e i testi delle risposte vere
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
        and ct.get("rows") == [{"name": "chrome-bridge", "value": 8.0, "text": "8", "url": None}, {"name": "fable-director", "value": 4.0, "text": "4", "url": None}],
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
        r_al.returncode == 0 and c3.get("status") == "watch" and c3["hits"] == [{"kind": "below", "value": 1800.0, "series": "yoga", "text": "sotto 1.800 € · yoga", "url": None}]
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

# MO8
def items_of(mid):
    return [x for x in sug().get("items") or [] if x.get("key") == f"monitor:{mid}"]


mo("add", "fermo", "--title", "Fermo", "--form", "multiples", "--unit", "€", "--series", "yoga,zen")
mo("alert", "fermo", "--new-min", "--series", "yoga")
mo("point", "fermo", "--series", "yoga", "1936.95", "--ts", ts(9, 8))
m_one = show("fermo")["status"]
mo("point", "fermo", "--series", "yoga", "1936.95", "--ts", ts(9, 12))
m_same, i_same = show("fermo"), items_of("fermo")
mo("point", "fermo", "--series", "yoga", "1899", "--ts", ts(9, 13))
m_low, i_low = show("fermo"), items_of("fermo")
mo("point", "fermo", "--series", "yoga", "1899", "--ts", ts(9, 14))
m_again, i_again = show("fermo"), items_of("fermo")
T.check("MO8 --new-min strict: one value no hit; 1936.95 twice no hit, no item; 1899 hit with one item; 1899 again no hit, item gone",
        m_one == "ok" and m_same["status"] == "ok" and not m_same["hits"] and not i_same
        and m_low["status"] == "watch" and m_low["hits"][0]["kind"] == "new_min" and len(i_low) == 1
        and m_again["status"] == "ok" and not i_again,
        f"{m_one} {m_same.get('hits')} {i_same} {m_low.get('hits')} {i_low} {m_again.get('hits')} {i_again}")
mo("add", "tetto", "--title", "Tetto", "--form", "line", "--unit", "utenti")
mo("alert", "tetto", "--new-max")
mo("point", "tetto", "130", "--ts", ts(9, 8))
x_one = show("tetto")["status"]
mo("point", "tetto", "130", "--ts", ts(9, 12))
x_same = show("tetto")
mo("point", "tetto", "131", "--ts", ts(9, 13))
x_high = show("tetto")
T.check("MO8 --new-max strict: one value no hit; the same value again no hit; a higher one hit",
        x_one == "ok" and x_same["status"] == "ok" and not x_same["hits"] and x_high["status"] == "watch" and x_high["hits"][0]["kind"] == "new_max",
        f"{x_one} {x_same.get('hits')} {x_high.get('hits')}")

# MO9 — l'aggiornamento a richiesta (contratto 1.133)
MON = tmp / "monitors"
ledger = tmp / "state" / "ledger.jsonl"


def refresh_rows(mid):
    try:
        rows = [json.loads(x) for x in ledger.read_text().splitlines() if x.strip()]
    except OSError:
        return []
    return [r for r in rows if r.get("event") == "monitor-refresh" and r.get("monitor") == mid]


def wait_refresh(mid, t=20):
    end = time.time() + t
    while time.time() < end:
        v = show(mid).get("refresh") or {}
        if v.get("state") in ("ok", "error"):
            return v
        time.sleep(0.2)
    return show(mid).get("refresh") or {}


def lines_of(path):
    try:
        return path.read_text().splitlines()
    except OSError:
        return []


mo("add", "giro", "--title", "Giro", "--form", "line", "--unit", "€")
mo("point", "giro", "10", "--ts", ts(8))
c9a = show("giro")
CMD9 = f'{CLI} monitor point "$SUPERVISOR_MONITOR_ID" 42 >/dev/null && echo "letto 1 prezzo"'
r_set = mo("set", "giro", "--refresh", CMD9)
d9 = json.loads((MON / "giro" / "def.json").read_text())
c9 = show("giro")
T.check("MO9 set --refresh: def.json.refresh {cmd, timeout_s 180, min_interval_s 60}; before it the card is not refreshable (refresh null)",
        r_set.returncode == 0 and d9.get("refresh") == {"cmd": CMD9, "timeout_s": 180, "min_interval_s": 60}
        and c9a.get("refreshable") is False and c9a.get("refresh") is None and "refresh" in c9a, f"{r_set.stdout}{r_set.stderr} {d9.get('refresh')} {c9a.get('refresh')}")
T.check("MO9 the card: refreshable true, refresh idle with the seven fields, and the command's text nowhere in it (nor in the list)",
        c9.get("refreshable") is True and c9.get("refresh") == {"state": "idle", "started": None, "ended": None, "rc": None, "message": None,
                                                                 "retry_at": None, "timeout_s": 180}
        and "letto 1 prezzo" not in json.dumps(c9, ensure_ascii=False) and "letto 1 prezzo" not in mo("list", "--json").stdout
        and "letto 1 prezzo" in mo("show", "giro").stdout, json.dumps(c9.get("refresh")))
r_set_u = mo("set", "non-esiste", "--refresh", "true")
r_set_s = mo("set", "giro", "--refresh-timeout", "0")
r_set_b = mo("set", "giro", "--refresh", "true", "--no-refresh")
r_set_n = mo("set", "giro", "--refresh", "echo a\necho b")
same9 = json.loads((MON / "giro" / "def.json").read_text()).get("refresh") == d9["refresh"]
mo("add", "nuovo", "--title", "Nuovo", "--form", "tile", "--refresh", "true", "--refresh-timeout", "5", "--refresh-min-interval", "0")
d9n = json.loads((MON / "nuovo" / "def.json").read_text()).get("refresh")
r_no = mo("set", "nuovo", "--no-refresh")
T.check("MO9 refused, nothing written: set on an unknown id (no folder made), --refresh-timeout 0, --refresh with --no-refresh, a two-line command; "
        "add --refresh with its seconds; --no-refresh removes it",
        r_set_u.returncode == 2 and not (MON / "non-esiste").exists() and r_set_s.returncode == 2 and r_set_b.returncode == 2
        and r_set_n.returncode == 2 and same9 and d9n == {"cmd": "true", "timeout_s": 5, "min_interval_s": 0} and r_no.returncode == 0
        and "refresh" not in json.loads((MON / "nuovo" / "def.json").read_text()) and show("nuovo").get("refreshable") is False,
        f"{r_set_u.returncode} {r_set_s.returncode} {r_set_b.returncode} {r_set_n.returncode} {same9} {d9n} {r_no.stderr}")

# MO10
t10 = int(time.time())
r_w = mo("refresh", "giro", "--wait")
c10 = show("giro")
v10 = c10.get("refresh") or {}
rows10 = refresh_rows("giro")
T.check("MO10 refresh --wait: the command ran with SUPERVISOR_MONITOR_ID and wrote a point through the CLI; state ok, rc 0, message = its last line",
        r_w.returncode == 0 and "letto 1 prezzo" in r_w.stdout and c10.get("value") == "42 €" and c10["series"][0]["n"] == 2
        and v10.get("state") == "ok" and v10.get("rc") == 0 and v10.get("message") == "letto 1 prezzo"
        and isinstance(v10.get("started"), int) and t10 <= v10["started"] <= v10.get("ended", 0) and v10.get("retry_at") == v10["ended"] + 60,
        f"{r_w.returncode} {r_w.stdout}{r_w.stderr} {c10.get('value')} {v10}")
T.check("MO10 every run leaves one monitor-refresh row in the ledger (monitor, ok, rc, seconds, text)",
        len(rows10) == 1 and rows10[0]["ok"] is True and rows10[0]["rc"] == 0 and rows10[0]["text"] == "letto 1 prezzo"
        and isinstance(rows10[0].get("seconds"), int), str(rows10))
r_soon = mo("refresh", "giro", "--wait")
T.check("MO10 again at once: too soon (exit 4, «riprova fra N s»), the command not run again, the state untouched",
        r_soon.returncode == 4 and "riprova fra" in r_soon.stdout and show("giro")["series"][0]["n"] == 2 and len(refresh_rows("giro")) == 1
        and show("giro").get("refresh") == v10, f"{r_soon.returncode} {r_soon.stdout}{r_soon.stderr}")

# MO11
mark11 = tmp / "mark11"
mo("add", "lento", "--title", "Lento", "--form", "tile", "--refresh", f"echo giro >> {mark11}; sleep 2; echo finito",
   "--refresh-min-interval", "0")
t11 = time.time()
r_bg = mo("refresh", "lento")
took11 = time.time() - t11
v11 = show("lento").get("refresh") or {}
r_bg2 = mo("refresh", "lento")
r_bg3 = mo("refresh", "lento", "--wait")
end11 = wait_refresh("lento")
T.check("MO11 a background refresh returns at once with state running (ended and rc null)",
        r_bg.returncode == 0 and took11 < 1.9 and v11.get("state") == "running" and v11.get("ended") is None and v11.get("rc") is None
        and v11.get("retry_at") is None and isinstance(v11.get("started"), int), f"{r_bg.returncode} {took11:.1f} {v11} {r_bg.stderr}")
T.check("MO11 the lock: a second and a third request while it runs answer «già in corso» (exit 3); the command ran ONCE; at the end state ok",
        r_bg2.returncode == 3 and "già in corso" in r_bg2.stdout and r_bg3.returncode == 3 and end11.get("state") == "ok"
        and end11.get("message") == "finito" and lines_of(mark11) == ["giro"] and len(refresh_rows("lento")) == 1,
        f"{r_bg2.returncode} {r_bg2.stdout} {r_bg3.returncode} {end11} {lines_of(mark11)}")
r_again = mo("refresh", "lento", "--wait")
T.check("MO11 with --refresh-min-interval 0 the next request runs (the lock was released)",
        r_again.returncode == 0 and lines_of(mark11) == ["giro", "giro"] and show("lento")["refresh"].get("retry_at") is None,
        f"{r_again.returncode} {r_again.stderr} {lines_of(mark11)}")

# MO12
pid12 = tmp / "pid12"
mo("add", "fermo-li", "--title", "Fermo lì", "--form", "tile", "--refresh", f"echo $$ > {pid12}; sleep 30; echo mai",
   "--refresh-timeout", "1", "--refresh-min-interval", "0")
t12 = time.time()
r_to = mo("refresh", "fermo-li", "--wait")
took12 = time.time() - t12
v12 = show("fermo-li").get("refresh") or {}
time.sleep(0.3)
pid = (lines_of(pid12) or ["0"])[0]
T.check("MO12 timeout: after 1 s the run ends with rc 124, state error and supervisor's own sentence; the command's process is gone",
        r_to.returncode == 1 and took12 < 8 and v12.get("state") == "error" and v12.get("rc") == 124
        and v12.get("message") == "tempo scaduto dopo 1 s: comando fermato" and v12.get("timeout_s") == 1
        and pid.isdigit() and int(pid) > 1 and not Path(f"/proc/{pid}").exists(), f"{r_to.returncode} {took12:.1f} {v12} pid {pid}")

# MO13
mo("add", "rotto", "--title", "Rotto", "--form", "tile", "--refresh", "echo prima; echo 'negozio irraggiungibile' >&2; exit 3",
   "--refresh-min-interval", "0")
r_er = mo("refresh", "rotto", "--wait")
v13 = show("rotto").get("refresh") or {}
T.check("MO13 a command that exits 3: refresh --wait exits 1, state error, rc 3, message = its last line (stderr too), one ledger row with ok false",
        r_er.returncode == 1 and "negozio irraggiungibile" in r_er.stderr and v13.get("state") == "error" and v13.get("rc") == 3
        and v13.get("message") == "negozio irraggiungibile" and [r["ok"] for r in refresh_rows("rotto")] == [False], f"{r_er.returncode} {r_er.stderr} {v13}")
(MON / "rotto" / "refresh.json").write_text(json.dumps({"state": "running", "started": int(time.time()) - 5, "ended": None, "rc": None, "message": None}))
v13b = show("rotto").get("refresh") or {}
r_er2 = mo("refresh", "rotto", "--wait")
T.check("MO13 «running» with nobody holding the lock is read as an interrupted run (error), and the next request runs",
        v13b.get("state") == "error" and v13b.get("message") == "aggiornamento interrotto prima della fine" and r_er2.returncode == 1
        and show("rotto")["refresh"].get("rc") == 3, f"{v13b} {r_er2.returncode}")
r_none = mo("refresh", "adozione")
r_fd = mo("refresh", "rotto", "--run-fd", "0")
T.check("MO13 a monitor with no command: refresh refused (exit 2) naming `monitor set`; the internal --run-fd by hand runs nothing",
        r_none.returncode == 2 and "monitor set adozione --refresh" in r_none.stderr and not (MON / "adozione" / "refresh.json").exists()
        and r_fd.returncode == 2 and len(refresh_rows("rotto")) == 2, f"{r_none.returncode} {r_none.stderr} {r_fd.returncode} {len(refresh_rows('rotto'))}")

# MO14
mark14, evil14 = tmp / "mark14", tmp / "evil14"
mo("add", "da-app", "--title", "Da app", "--form", "tile", "--refresh", f"echo giro >> {mark14}; sleep 2; echo fatto",
   "--refresh-min-interval", "30")
EVIL = f"echo x >> {evil14}"
ok_n, t_n = RL.execute({"op": "monitor_refresh", "arg": "adozione", "device": "phone", "by": "maintainer"})
ok_g, t_g = RL.execute({"op": "monitor_refresh", "arg": "non-esiste", "device": "phone", "by": "maintainer"})
ok_x, t_x = RL.execute({"op": "monitor_refresh", "arg": f"da-app; {EVIL}", "device": "phone", "by": "maintainer"})
ok_e, t_e = RL.execute({"op": "monitor_refresh", "arg": "adozione", "text": EVIL, "cmd": EVIL, "refresh": {"cmd": EVIL}, "device": "phone", "by": "maintainer"})
T.check("MO14 op monitor_refresh is in OPS and is not a read; a monitor with no declared command, an unknown id, a bad id: error, nothing starts",
        "monitor_refresh" in RL.OPS and "monitor_refresh" not in RL.PASSIVE_OPS and not ok_n and "non ha un comando di aggiornamento" in t_n
        and not ok_g and "non c'è più" in t_g and not ok_x and not ok_e and not (MON / "adozione" / "refresh.json").exists()
        and not evil14.exists(), f"{t_n} | {t_g} | {t_x} | {t_e}")
t14 = time.time()
ok_r, t_r = RL.execute({"op": "monitor_refresh", "arg": "da-app", "text": EVIL, "cmd": EVIL, "refresh": {"cmd": EVIL}, "timeout_s": 1,
                        "device": "phone", "by": "maintainer"})
took14 = time.time() - t14
card14 = json.loads(t_r) if ok_r else {}
ok_r2, t_r2 = RL.execute({"op": "monitor_refresh", "arg": "da-app", "device": "phone", "by": "maintainer"})
card14b = json.loads(t_r2) if ok_r2 else {}
end14 = wait_refresh("da-app")
d14 = json.loads((MON / "da-app" / "def.json").read_text())["refresh"]
T.check("MO14 from the app: starts in the background and answers the card with refresh running (the keys of op monitors); a second request "
        "while it runs is ok and still running",
        ok_r and took14 < 1.9 and card14.get("id") == "da-app" and card14.get("refreshable") is True and card14["refresh"]["state"] == "running"
        and set(card14) == set(json.loads(t_o)) and ok_r2 and card14b["refresh"]["state"] == "running"
        and card14b["refresh"]["started"] == card14["refresh"]["started"], f"{ok_r} {took14:.1f} {t_r[:200]} {ok_r2} {t_r2[:120]}")
T.check("MO14 a command text sent by the app (text, cmd, refresh.cmd, timeout_s) is never run and never written: only def.json's command ran, once",
        end14.get("state") == "ok" and end14.get("message") == "fatto" and lines_of(mark14) == ["giro"] and not evil14.exists()
        and d14 == {"cmd": f"echo giro >> {mark14}; sleep 2; echo fatto", "timeout_s": 180, "min_interval_s": 30}
        and end14.get("timeout_s") == 180 and len(refresh_rows("da-app")) == 1, f"{end14} {lines_of(mark14)} {evil14.exists()} {d14}")
ok_s, t_s = RL.execute({"op": "monitor_refresh", "arg": "da-app", "device": "phone", "by": "maintainer"})
T.check("MO14 right after the end: too soon is an error with the seconds to wait, nothing starts",
        not ok_s and t_s.startswith("da-app: aggiornato da poco, riprova fra ") and lines_of(mark14) == ["giro"], t_s)

# MO15
s20, s21, s22 = smp.get("1320") or {}, smp.get("1321") or {}, smp.get("1322") or {}
try:
    b20 = json.loads(s20.get("text") or "{}")
except ValueError:
    b20 = {}
T.check("MO15 the samples …1320 (started: the card, refresh running), …1321 (no command), …1322 (too soon): the keys and the texts of the real answers; "
        "…1310/…1311 carry refreshable and refresh",
        [cmds.get(k, {}).get("op") for k in ("1320", "1321", "1322")] == ["monitor_refresh"] * 3 and s20.get("ok") is True
        and set(b20) == set(card14) and set(b20["refresh"]) == set(card14["refresh"]) and b20["refresh"]["state"] == "running" and b20["refreshable"] is True
        and s21.get("ok") is False and s21.get("text") == t_n and s22.get("ok") is False
        and s22.get("text", "").rsplit(" ", 2)[0] == t_s.replace("da-app", "prezzi-portatili").rsplit(" ", 2)[0]
        and set(b11["refresh"]) == set(end14) and b11["refreshable"] is True
        and {i["id"]: i["refreshable"] for i in b10["items"]} == {"prezzi-portatili": True, "adozione": False}
        and all((i["refresh"] is None) == (not i["refreshable"]) for i in b10["items"])
        and all(set(c) == {"id", "op", "session", "arg", "issued", "by"} for c in (cmds["1320"], cmds["1321"], cmds["1322"])),
        f"{s21.get('text')} | {t_n} | {s22.get('text')} | {t_s} | {sorted(set(b20) ^ set(card14))}")

# MO16 (1.138, 12/11/2026, supervisor-monitor-link-12-11): il link della cosa monitorata — per punto e fisso per serie
mo("add", "pc", "--title", "Prezzi pc", "--form", "multiples", "--unit", "€", "--series", "yoga,zen", "--link", "zen=https://shop.example/zen")
mo("alert", "pc", "--new-min", "--series", "yoga")
mo("point", "pc", "--series", "yoga", "1900", "--ts", ts(8), "--url", "https://a.example/yoga?id=1")
mo("point", "pc", "--series", "zen", "1300", "--ts", ts(8))
mo("point", "pc", "--series", "yoga", "1799", "--ts", ts(9), "--url", "https://unieuro.example/yoga-9i")
bad_u = mo("point", "pc", "--series", "yoga", "1700", "--url", "javascript:alert(1)")
bad_l = mo("set", "pc", "--link", "zen=ftp://x")
c16 = show("pc")
s16 = {s["name"]: s for s in c16.get("series") or []}
it16 = next((x for x in (sug().get("items") or []) if x.get("key") == "monitor:pc"), {})
T.check("MO16 a point carries --url, a series a fixed --link: each series gives the url of its last point that has one, else the fixed one; a new minimum's hit carries the url of the point that set it off, and so does the card",
        s16.get("yoga", {}).get("url") == "https://unieuro.example/yoga-9i" and s16.get("zen", {}).get("url") == "https://shop.example/zen"
        and [h.get("url") for h in c16.get("hits") or []] == ["https://unieuro.example/yoga-9i"] and c16.get("url") == "https://unieuro.example/yoga-9i", json.dumps(c16)[:900])
T.check("MO16 the item in «Dalla master» carries the page (url) beside the app link; only http and https, the rest refused with nothing written",
        it16.get("url") == "https://unieuro.example/yoga-9i" and it16.get("link") == WEB + "/#/monitor/pc"
        and bad_u.returncode != 0 and bad_l.returncode != 0 and "javascript" not in (tmp / "monitors" / "pc" / "points.jsonl").read_text(), json.dumps(it16) + bad_u.stderr + bad_l.stderr)
mo("set", "pc", "--link", "zen=")
c16b = show("pc")
SG16 = importlib.util.spec_from_file_location("sg16", T.SCRIPTS / "cm-suggest.py")
T.check("MO16 «--link S=» removes the fixed link; a monitor with no url has url null everywhere (old apps see nothing new)",
        {s["name"]: s["url"] for s in c16b["series"]}.get("zen") is None and show("prezzi").get("url") is None
        and all(s.get("url") is None for s in show("prezzi")["series"]), json.dumps(c16b["series"])[:400])
T.check("no talk left the test", not talklog.exists() or talklog.read_text() == "", talklog.read_text() if talklog.exists() else "")
T.rm(tmp)
T.finish()
