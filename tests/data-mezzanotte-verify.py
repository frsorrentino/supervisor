#!/usr/bin/env python3
"""I test che confrontano la data di oggi con un file o un testo scritto un attimo prima non dipendono dal secondo in cui
scatta la mezzanotte (24/11/2026, supervisor-test-data-24-11).

R14 e R43 di relay-verify, H1 di handoff-verify, PW5 di relay-peso-verify, AR5 di agenda-raffica-verify e PF2 di
push-falliti-verify leggevano la data dall'orologio vero prima o dopo lo script che la scrive: se in mezzo scatta la
mezzanotte, sono due giorni diversi e il test fallisce senza un difetto. Ora prendono i giorni fra una lettura prima e
una dopo (T.days) e cercano li' quello dello script.

DM1 nessun test legge la data dall'orologio vero senza un'ora data (time.strftime("%Y-%m-%d") e simili)
DM2 i sei tratti stanno fra i segni DATA-<caso>-begin e DATA-<caso>-end
DM3 ogni tratto, eseguito cosi' com'e' con l'orologio a 23:59:58, 23:59:59 e 00:00:00 (avanti di 1 s a ogni lettura; lo
    script finto scrive la data del suo secondo), e' verde; anche a mezzogiorno
DM4 la forma di prima di ogni tratto, con gli stessi script finti, e' rossa ad almeno una di quelle ore e verde a
    mezzogiorno: la prova vede il difetto e gli script finti non sono rotti
DM5 T.days: un giorno solo nello stesso giorno, due a cavallo della mezzanotte, nell'ordine
DM6 avvisi-telegram, calendario, mattino-formato e recap-telegram leggono il giorno fra i segni DATA-<caso> e lo passano
    agli script come CM_TODAY (TODAY_ENV) (24/11, supervisor-test-oggi-24-11)
DM7 quel tratto, con l'orologio a 23:59:58, 23:59:59, 00:00:00, all'ultimo giorno del mese e a mezzogiorno, seguito dalle
    funzioni vere degli script (recap add, recap delle 20, mattino col Firebase del mese, calendario) con lo stesso
    orologio: lo script trova il giorno del test
DM8 la forma di prima (senza CM_TODAY) e' rossa ad almeno una di quelle ore e verde a mezzogiorno
DM9 relay-k-verify prende DAY0 e ieri da NOW, non da una seconda lettura dell'orologio
"""
import json
import re
import sys
import time as _time
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

TESTS = Path(__file__).resolve().parent
tmp = Path(T.tmpdir())

WALL = re.compile(r"\btime\.strftime\(\s*([\"'])[^\"']*%[dmYyjaAbB][^\"']*\1\s*\)")
hits = [f"{p.name}:{n}" for p in sorted(TESTS.glob("*.py")) if p.name != Path(__file__).name
        for n, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if WALL.search(ln) and not ln.lstrip().startswith("#")]
T.check("DM1 no test reads the date from the real clock without a given time (time.strftime(\"%Y-%m-%d\") and the like)", not hits, ", ".join(hits))

CASES = {"R14": "relay-verify.py", "R43": "relay-verify.py", "H1": "handoff-verify.py", "PW5": "relay-peso-verify.py",
         "AR5": "agenda-raffica-verify.py", "PF2": "push-falliti-verify.py"}
BLOCKS = {}
for case, fn in CASES.items():
    m = re.search(rf"^# DATA-{case}-begin.*?^# DATA-{case}-end$", (TESTS / fn).read_text(encoding="utf-8"), re.S | re.M)
    BLOCKS[case] = m.group(0) if m else ""
T.check("DM2 the six parts are between the marks DATA-<case>-begin and DATA-<case>-end", all(BLOCKS.values()),
        ", ".join(c for c, b in BLOCKS.items() if not b))

# la forma di prima, com'era su main (0.8.204), con gli stessi nomi
OLD = {
    "R14": '''today = time.strftime("%Y-%m-%d")
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-recap.py"), "--send"], capture_output=True, text=True, env=ENV, timeout=120)
ev = new_events("recap", before)
T.check("R14", r.returncode == 0 and len(ev) == 1 and ev[0]["title"] == "Diario del " + time.strftime("%d/%m") and ev[0]["ref"] == today and "<b>" not in ev[0]["body"])
''',
    "R43": '''r43 = send_cmd(_c43[_ok43])
_seg43 = atlas / "docs" / "segnalazioni"
_day43 = time.strftime("%Y-%m-%d")
_p43 = [_seg43 / f"{_day43}-two-screens-of-the-checkout.png", _seg43 / f"{_day43}-two-screens-of-the-checkout.jpg"]
T.check("R43", r43 and r43["ok"] is True and r43["text"] == _r43[_ok43]["text"].replace("2026-10-10", _day43)
        and [x.read_bytes() if x.is_file() else None for x in _p43] == [_PNG43, _JPG43])
''',
    "H1": '''today = time.strftime("%Y-%m-%d")
r = ho("--prepare")
p_state = field(r.stdout, "PATH")
T.check("H1", r.returncode == 0 and p_state.startswith(str(tmp / "state" / "handoffs" / f"alfa-{today}-")) and p_state.endswith(".md"))
''',
    "PW5": '''u = json.loads((rd / "usage-firebase.json").read_text())
today = u[time.strftime("%Y-%m-%d")]
T.check("PW5", today.get("results") == 3 and today.get("results_pruned") == 7)
''',
    "AR5": '''errs = sorted(rdir.glob("relay-errors-*.log"))
etext = errs[-1].read_text() if errs else ""
T.check("AR5", len(errs) == 1 and errs[0].name == f"relay-errors-{time.strftime('%Y-%m-%d')}.log" and "agenda" in etext and "ERR" in etext
        and len(etext.splitlines()) <= 8)
''',
    "PF2": '''s = relay("status").stdout
T.check("PF2", "last error:" in s and "Operation not permitted" in s and time.strftime("%Y-%m-%d") in s and "5 in the last hour, 5 in a row" in s, s)
''',
}


def local(day_off, hh, mm, ss):
    t = _time.localtime()
    return int(_time.mktime((t.tm_year, t.tm_mon, t.tm_mday + day_off, hh, mm, ss, 0, 0, -1)))


def ds(t, fmt="%Y-%m-%d"):
    return _time.strftime(fmt, _time.localtime(t))


class Clock:
    """time finto: ogni lettura va avanti di 1 s, partendo da start; strftime e localtime senza ora leggono questo."""
    def __init__(self, start):
        self.t = float(start)

    def time(self):
        self.t += 1
        return self.t - 1

    def localtime(self, t=None):
        return _time.localtime(self.time() if t is None else t)

    def strftime(self, fmt, t=None):
        return _time.strftime(fmt, self.localtime() if t is None else t)

    def __getattr__(self, name):
        return getattr(_time, name)


class Rec:
    SCRIPTS = T.SCRIPTS

    def __init__(self):
        self.res = []

    def check(self, name, ok, detail=""):
        self.res.append((name, bool(ok), str(detail)))

    @staticmethod
    def days(*a, **k):
        return T.days(*a, **k)


def field(out, key):
    return next((l.split(": ", 1)[1] for l in out.splitlines() if l.startswith(key + ": ")), "")


def namespace(case, clk, start, d):
    """Quello che il tratto trova intorno a se' nel test vero, con script finti che scrivono la data del loro secondo.
    PW5, AR5 e PF2 leggono un file scritto prima del tratto: qui scritto un secondo prima di start."""
    ns = {"time": clk, "T": Rec(), "json": json, "sys": sys}
    if case == "R14":
        ev = []

        def run(*_a, **_k):
            t = clk.time()
            ev.append({"title": "Diario del " + ds(t, "%d/%m"), "ref": ds(t), "body": "diario\nriga", "key": "e_9", "session": None})
            return NS(returncode=0, stdout="diario\nriga\n", stderr="")
        ns.update(subprocess=NS(run=run), ENV={}, before=set(), seq0=0, n_fcm=0, new_events=lambda _k, _b: list(ev),
                  CALLS={"fcm": [{"message": {"data": {"kind": "recap"}}}]}, ev_ok=lambda _e: True)
    elif case == "R43":
        png, jpg = b"\x89PNG" + b"a" * 30, b"\xff\xd8" + b"b" * 20
        text = "saved docs/segnalazioni/2026-10-10-two-screens-of-the-checkout.png, docs/segnalazioni/2026-10-10-two-screens-of-the-checkout.jpg"

        def send_cmd(_c):
            t = clk.time()
            seg = d / "docs" / "segnalazioni"
            seg.mkdir(parents=True, exist_ok=True)
            (seg / f"{ds(t)}-two-screens-of-the-checkout.png").write_bytes(png)
            (seg / f"{ds(t)}-two-screens-of-the-checkout.jpg").write_bytes(jpg)
            return {"ok": True, "text": text.replace("2026-10-10", ds(t))}
        ns.update(send_cmd=send_cmd, _c43={"x": {}}, _ok43="x", _r43={"x": {"text": text}}, atlas=d, _PNG43=png, _JPG43=jpg,
                  STORE={}, _a43="a", _b43="b")
    elif case == "H1":
        def ho(*_a):
            t = clk.time()
            return NS(returncode=0, stdout=f"PATH: {d / 'state' / 'handoffs' / ('alfa-' + ds(t) + '-' + ds(t, '%H%M') + '.md')}\n", stderr="")
        ns.update(ho=ho, field=field, tmp=d)
    elif case == "PW5":
        (d / "usage-firebase.json").write_text(json.dumps({ds(start - 1): {"results": 3, "result_bytes": 120, "results_deleted": 3,
                                                                           "state_pubs": 1, "state_skipped": 1, "results_pruned": 7}}))
        ns.update(rd=d, _t5=start - 2)
    elif case == "AR5":
        (d / f"relay-errors-{ds(start - 1)}.log").write_text("ERR agenda: no agenda configured (300 volte)\n")
        ns.update(rdir=d, t0=start - 2)
    elif case == "PF2":
        out = f"last error: {ds(start - 1)} {ds(start - 1, '%H:%M:%S')} [Errno 1] Operation not permitted\nfailed pushes: 5 in the last hour, 5 in a row\n"
        ns.update(relay=lambda *_a: NS(stdout=out), _t2=start - 2)
    return ns


def run(case, code, start, tag):
    d = tmp / f"{case}-{tag}"
    d.mkdir()
    clk = Clock(start)
    ns = namespace(case, clk, start, d)
    try:
        exec(compile(code, f"{CASES[case]} {case} ({tag})", "exec"), ns)
    except Exception as e:   # noqa: BLE001 — un errore del tratto e' un esito rosso, col suo testo
        ns["T"].res.append(("exec", False, repr(e)))
    return ns["T"].res


def green(res):
    return bool(res) and all(ok for _, ok, _ in res)


def show(res):
    return str([(n[:30], ok, x[:160]) for n, ok, x in res if not ok])


STARTS = {"23:59:58": local(0, 23, 59, 58), "23:59:59": local(0, 23, 59, 59), "00:00:00": local(1, 0, 0, 0)}
NOON = local(0, 12, 0, 0)
for case, block in BLOCKS.items():
    got = {h: run(case, block, s, "new-" + h.replace(":", "")) for h, s in STARTS.items()} if block else {}
    noon = run(case, block, NOON, "new-noon") if block else []
    T.check(f"DM3 {case} ({CASES[case]}) run as it is with the clock at 23:59:58, 23:59:59 and 00:00:00 is green, and at noon",
            bool(block) and all(green(r) for r in got.values()) and green(noon),
            "; ".join(f"{h} {show(r)}" for h, r in got.items() if not green(r)) + ("" if green(noon) else f"; noon {show(noon)}"))
    old = {h: run(case, OLD[case], s, "old-" + h.replace(":", "")) for h, s in STARTS.items()}
    old_noon = run(case, OLD[case], NOON, "old-noon")
    T.check(f"DM4 {case}: the earlier form is red at one of those times and green at noon (the fake scripts are sound)",
            any(not green(r) for r in old.values()) and green(old_noon), f"{[(h, green(r)) for h, r in old.items()]} noon {show(old_noon)}")

a, b = local(0, 23, 59, 59), local(1, 0, 0, 1)
T.check("DM5 T.days: one day within a day, two across midnight, in order",
        hasattr(T, "days") and T.days(a - 60, a) == [ds(a)] and T.days(a, b) == [ds(a), ds(b)] and T.days(a, b, "%d/%m") == [ds(a, "%d/%m"), ds(b, "%d/%m")]
        and T.days(b, a) == [ds(b)], f"{T.days(a, b)}" if hasattr(T, "days") else "no T.days")
# ---------------------------------------------------------------- DM6-DM9 (24/11, supervisor-test-oggi-24-11)
# Quattro test leggevano il giorno una volta e lo confrontavano con quello che lo script legge dal suo orologio. Ora il
# giorno va agli script come CM_TODAY (TODAY_ENV). Qui il tratto del test gira con l'orologio finto, poi le funzioni
# vere degli script (cm-avvisi, cm-recap, cm-calendar) con lo stesso orologio e l'ambiente che il tratto ha dato.
import contextlib  # noqa: E402
import datetime as _dt  # noqa: E402
import importlib.util  # noqa: E402
import io  # noqa: E402
import os  # noqa: E402

TODAY_FILES = {"AT": "avvisi-telegram-verify.py", "CA": "calendario-verify.py", "MF": "mattino-formato-verify.py", "RT": "recap-telegram-verify.py"}
TB = {}
for case, fn in TODAY_FILES.items():
    src = (TESTS / fn).read_text(encoding="utf-8")
    m = re.search(rf"^# DATA-{case}-begin.*?^# DATA-{case}-end$", src, re.S | re.M)
    TB[case] = (m.group(0) if m else "", src.count("TODAY_ENV") >= 2)
T.check("DM6 the four tests that compare today with the script's have their part between DATA-<case> marks, and pass TODAY_ENV on to the scripts",
        all(b and used for b, used in TB.values()), ", ".join(c for c, (b, u) in TB.items() if not (b and u)))
rk = (TESTS / "relay-k-verify.py").read_text(encoding="utf-8")
T.check("DM9 relay-k-verify takes DAY0 and yesterday from NOW, not from a second reading of the clock",
        "DAY0 = NOW." in rk and "yesterday = (NOW." in rk and "dt.datetime.now().replace" not in rk and "(dt.datetime.now() - dt.timedelta(days=1))" not in rk)

home6 = tmp / "home6"
(home6 / "state").mkdir(parents=True)
(home6 / "relay").mkdir()
cfg6 = home6 / "config.json"
cfg6.write_text(json.dumps({"language": "it", "state_dir": str(home6 / "state"), "accounts": {"personal": {"config_dir": str(home6 / ".claude")}},
                            "default_account": "personal", "workspace": {"root": str(home6), "root_session_name": "master"},
                            "relay": {"dir": str(home6 / "relay")}}))
os.environ.update({"HOME": str(home6), "CM_HOME": str(home6), "CC_SUPERVISOR_CONFIG": str(cfg6), "CM_TASKS_DB": str(home6 / "t.db")})
os.environ.pop("CM_TODAY", None)


def load(name, alias):
    sp = importlib.util.spec_from_file_location(alias, T.SCRIPTS / name)
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod


AV, RC, CAL = load("cm-avvisi.py", "cm_avvisi_dm"), load("cm-recap.py", "cm_recap_dm"), load("cm-calendar.py", "cm_calendar_dm")


def fake_dt(clk):
    class D(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date.fromtimestamp(clk.time())
    return NS(date=D, datetime=_dt.datetime, timedelta=_dt.timedelta, time=_dt.time, timezone=_dt.timezone)


def script_side(case, day, clk):
    """Quello che lo script vede, con l'orologio finto: [(che cosa, il giorno del test ritrovato?)]."""
    for mod in (AV, RC, CAL):
        mod.dt, mod.time = fake_dt(clk), clk
    out = []
    if case in ("AT", "RT"):
        AV.extra_path().unlink(missing_ok=True)
        with contextlib.redirect_stdout(io.StringIO()):
            AV.cmd_add(["--kind", "rilasci", "prova"])
        rows = AV.read_jsonl(AV.extra_path())
        out.append(("recap add day", [r.get("day") for r in rows] == [day.isoformat()]))
        out.append(("evening header", f" {day:%d/%m}" in AV.evening_text().splitlines()[0]))
        out.append(("recap today", RC.today() == day))
    if case in ("AT", "MF"):
        first = day.replace(day=1)
        usage = {(first + _dt.timedelta(days=i)).isoformat(): {"result_bytes": 500_000_000, "state_bytes": 0} for i in range(min(day.day, 3))}
        usage[(first - _dt.timedelta(days=1)).isoformat()] = {"result_bytes": 9_000_000_000, "state_bytes": 0}
        (home6 / "relay" / "usage-firebase.json").write_text(json.dumps(usage))
        txt = AV.morning_text()
        gb = str(round(min(day.day, 3) * 0.5, 1)).replace(".", ",")
        out.append(("morning header", f" {day:%d/%m}" in txt.splitlines()[0]))
        out.append(("morning Firebase of the month", f"{gb} GB scaricati" in txt))
    if case == "CA":
        out.append(("calendar today", CAL.today() == day))
        ev = {"uid": "x", "summary": "s", "date": day + _dt.timedelta(days=3), "remind": None, "case": ""}
        up = CAL.upcoming([ev], 60)
        out.append(("days_left", [e["days_left"] for e in up] == [3]))
    return out


def run6(case, code, start):
    clk = Clock(start)
    ns = {"dt": fake_dt(clk), "time": clk}
    try:
        exec(compile(code, f"{TODAY_FILES[case]} {case}", "exec"), ns)
        day = ns["today" if case != "CA" else "TODAY"]
        env = ns.get("TODAY_ENV") or {}
        os.environ.pop("CM_TODAY", None)
        os.environ.update(env)
        res = script_side(case, day, clk)
    except Exception as e:   # noqa: BLE001 — un errore e' un esito rosso, col suo testo
        res = [("exec", False, repr(e))]
    finally:
        os.environ.pop("CM_TODAY", None)
    return res


def ok6(res):
    return bool(res) and all(x[1] for x in res)


def last_of_month():
    t = _dt.date.today()
    nxt = (t.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
    d = nxt - _dt.timedelta(days=1)
    return int(_time.mktime((d.year, d.month, d.day, 23, 59, 59, 0, 0, -1)))


STARTS6 = dict(STARTS, **{"end of month 23:59:59": last_of_month()})
for case, (block, _u) in TB.items():
    new = {h: run6(case, block, s) for h, s in STARTS6.items()} if block else {}
    noon6 = run6(case, block, NOON) if block else []
    T.check(f"DM7 {case} ({TODAY_FILES[case]}): the test's day is the script's at 23:59:58, 23:59:59, 00:00:00, at the end of the month and at noon",
            bool(block) and all(ok6(r) for r in new.values()) and ok6(noon6),
            "; ".join(f"{h} {[x for x in r if not x[1]]}" for h, r in new.items() if not ok6(r)) + ("" if ok6(noon6) else f"; noon {noon6}"))
    old_block = re.sub(r"^TODAY_ENV = .*$", "TODAY_ENV = {}", block, flags=re.M)
    old6 = {h: run6(case, old_block, s) for h, s in STARTS6.items()} if block else {}
    old_noon6 = run6(case, old_block, NOON) if block else []
    T.check(f"DM8 {case}: the earlier form (no CM_TODAY) is red at one of those times and green at noon",
            bool(block) and old_block != block and any(not ok6(r) for r in old6.values()) and ok6(old_noon6),
            f"{[(h, ok6(r)) for h, r in old6.items()]} noon {old_noon6}")
T.rm(str(tmp))
T.finish()
