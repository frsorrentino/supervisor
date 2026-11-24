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
T.rm(str(tmp))
T.finish()
