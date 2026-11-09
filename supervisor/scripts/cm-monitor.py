#!/usr/bin/env python3
"""supervisor monitor — i Monitoraggi: valori raccolti da script, senza modello, in un formato unico (contratto 1.132,
09/11/2026, supervisor-monitoraggi-09-11).

  supervisor monitor add ID --title T --form F [--unit U] [--series a,b] [--source S] [--every 1h] [--max N] [--example]
  supervisor monitor alert ID (--below N | --above N | --new-min | --new-max) [--series S]
  supervisor monitor alert ID --clear
  supervisor monitor point ID [--series S] VALORE [--ts EPOCA|AAAA-MM-GG[THH:MM]]
  supervisor monitor verdict ID "frase"
  supervisor monitor list [--json]
  supervisor monitor show ID [--json]
  supervisor monitor line ID
  supervisor monitor remove ID

Ogni monitoraggio e' una cartella sotto la configurazione di supervisor (monitors/<id>/, CM_MONITORS per i test):
def.json (id, titolo, unita', forma, serie, soglie, fonte, frequenza, esempio, frase) e points.jsonl, una riga per
valore {ts, series, value}. Nessuna chiamata a un modello: la frase la scrive chi raccoglie, con `verdict`.

Le forme, un catalogo chiuso (dalla skill dataviz, references/choosing-a-form.md); una scala per grafico, mai due assi:
  tile      un valore con la variazione e una sparkline
  line      una serie nel tempo
  multiples piu' serie, un grafico piccolo per serie, con gli stessi giorni
  bars      conteggi per giorno (i valori dello stesso giorno si sommano)
  meter     un valore contro un limite (--max)
  table     righe: l'ultimo valore di ogni serie
  status    si'/no, con i giorni (1 o 0, si'/no, ok/ko)

Le soglie (alert): --below/--above N sull'ultimo valore, --new-min/--new-max quando l'ultimo valore e' il minimo (o il
massimo) di tutta la storia; --series S le lega a una serie, senza vale per ognuna. Quando scattano, una sola voce in
«Dalla master» (state.suggestions, kind monitor, key monitor:<id>) col link al monitoraggio; esce quando il valore
rientra. `line` stampa una riga breve col link, per i cron che oggi mandano report lunghi su Telegram.
Il link: <relay.web_app_url>/#/monitor/<id>.
"""
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB_APP_URL = "https://supervisor.francescosorrentino.com"   # come cm-relay.WEB_APP_URL
FORMS = ("tile", "line", "multiples", "bars", "meter", "table", "status")
ALERTS = ("below", "above", "new_min", "new_max")
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
SERIES_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+-]{0,39}")
TITLE_MAX = 60
VERDICT_MAX = 200
UNIT_MAX = 12
SOURCE_MAX = 120
POINTS_MAX = 60          # punti per serie nel contratto
DELTA_DAYS = 7           # la variazione: contro il valore di 7 giorni prima (o il primo, se la storia e' piu' corta)
SUGGEST_TTL_S = 7 * 24 * 3600
CURRENCY = ("€", "$", "£", "EUR", "USD")
YES = ("1", "si", "sì", "yes", "true", "ok", "y")
NO = ("0", "no", "false", "ko", "n")


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
LANG = "en" if str(CFG.get("language") or "it").startswith("en") else "it"


class Bad(Exception):
    """Un errore per chi scrive il comando: il messaggio e' gia' tradotto."""


# ------------------------------------------------------------------ archivio
def root():
    p = os.environ.get("CM_MONITORS")
    if p:
        return Path(p)
    c = (os.environ.get("CC_SUPERVISOR_CONFIG") or os.environ.get("TEAM_SUPERVISOR_CONFIG") or os.environ.get("CLAUDE_MASTER_CONFIG"))
    return (Path(c).parent if c else Path.home() / ".config" / "cc-supervisor") / "monitors"


def _dir(mid):
    return root() / mid


def check_id(mid):
    if not ID_RE.fullmatch(str(mid or "")):
        raise Bad(M("monitor.bad_id", id=str(mid)[:40]))
    return mid


def read_def(mid):
    try:
        d = json.loads((_dir(mid) / "def.json").read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("id") == mid else None


def need(mid):
    d = read_def(check_id(mid))
    if d is None:
        raise Bad(M("monitor.unknown", id=mid))
    return d


def write_def(d):
    p = _dir(d["id"])
    p.mkdir(parents=True, exist_ok=True)
    tmp = p / "def.json.tmp"
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, p / "def.json")


def read_points(mid):
    out = []
    try:
        with open(_dir(mid) / "points.jsonl") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    out.append({"ts": int(r["ts"]), "series": str(r.get("series") or ""), "value": float(r["value"])})
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    return sorted(out, key=lambda r: r["ts"])


def ids():
    try:
        return sorted(p.name for p in root().iterdir() if p.is_dir() and ID_RE.fullmatch(p.name) and (p / "def.json").is_file())
    except OSError:
        return []


# ------------------------------------------------------------------ formato dei numeri
def fmt_num(v):
    a = abs(v)
    dec = 0 if a >= 100 or a == int(a) else 1 if a >= 10 else 2
    s = f"{a:,.{dec}f}"
    if dec:
        s = s.rstrip("0").rstrip(".")
    if LANG == "it":
        s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return ("−" if v < 0 else "") + s


def fmt_val(v, unit):
    n = fmt_num(v)
    if not unit:
        return n
    return n + unit if unit == "%" else f"{n} {unit}"


def signed(v, unit=""):
    s = fmt_val(v, unit)
    return s if v < 0 else "+" + s


def day_text(ts):
    d = dt.datetime.fromtimestamp(ts)
    return f"{d.day}/{d.month}"


def since_text(ts):
    d = dt.datetime.fromtimestamp(ts).day
    if LANG == "it":   # l'elisione: dall'1, dall'8, dall'11
        return ("dall'" if d in (1, 8, 11) else "dal ") + day_text(ts)
    return "since " + day_text(ts)


# ------------------------------------------------------------------ la scheda per l'app
def link(mid):
    base = str((CFG.get("relay") or {}).get("web_app_url", WEB_APP_URL) or "").rstrip("/")
    return f"{base}/#/monitor/{mid}" if base and mid else None


def _by_series(d, pts):
    names = list(d.get("series") or [])
    for p in pts:
        if p["series"] not in names:
            names.append(p["series"])
    return [(n, [p for p in pts if p["series"] == n]) for n in names if any(p["series"] == n for p in pts)] or \
           [(n, []) for n in (d.get("series") or [""])]


def _per_day(pts, how):
    """bars: la somma di ogni giorno; status: l'ultimo valore di ogni giorno. [(ts del giorno a mezzanotte, v)]."""
    days = {}
    for p in pts:
        t = dt.datetime.fromtimestamp(p["ts"])
        k = int(dt.datetime(t.year, t.month, t.day).timestamp())
        days[k] = days.get(k, 0) + p["value"] if how == "sum" else p["value"]
    return sorted(days.items())


def reduce(points, n=POINTS_MAX):
    """Al massimo n punti: il tempo diviso in n intervalli uguali, di ognuno l'ultimo valore. [[ts, v]]."""
    if len(points) <= n:
        return [[t, v] for t, v in points]
    t0, t1 = points[0][0], points[-1][0]
    step = (t1 - t0) / n or 1
    out = {}
    for t, v in points:
        out[min(n - 1, int((t - t0) / step))] = [t, v]
    return [out[k] for k in sorted(out)]


def _hits(d, series):
    """Le soglie che scattano adesso: [{kind, value, series, text}]."""
    out = []
    for a in d.get("alerts") or []:
        for name, pts in series:
            if a.get("series") not in (None, "", name) or not pts:
                continue
            last = pts[-1][1]
            prev = [v for _, v in pts[:-1]]
            hit = (a["kind"] == "below" and last < a["value"]) or (a["kind"] == "above" and last > a["value"]) or \
                  (a["kind"] == "new_min" and prev and last <= min(prev)) or (a["kind"] == "new_max" and prev and last >= max(prev))
            if hit:
                out.append(dict(alert_view(a, d.get("unit") or ""), series=name or None))
    return out


def alert_view(a, unit):
    t = M(f"monitor.alert_{a['kind']}", v=fmt_val(a["value"], unit) if a.get("value") is not None else "")
    return {"kind": a["kind"], "value": a.get("value"), "series": a.get("series") or None,
            "text": t + (f" · {a['series']}" if a.get("series") else "")}


def _delta(pts, unit):
    """(testo, percentuale, da quando) della variazione dell'ultimo valore; (None, None, None) con un solo punto."""
    if len(pts) < 2:
        return None, None, None
    t_last, v_last = pts[-1]
    ref = next(((t, v) for t, v in reversed(pts[:-1]) if t <= t_last - DELTA_DAYS * 86400), pts[0])
    diff = v_last - ref[1]
    pct = round(diff / ref[1] * 100, 1) if ref[1] else None
    if unit in CURRENCY and pct is not None:
        d = ("+" if pct >= 0 else "") + fmt_val(pct, "%")
    else:
        d = signed(diff, unit if len(unit) <= 3 else "")   # «−1 in 7 giorni», non «−1 utenti in 7 giorni»
    span = (t_last - ref[0]) / 86400
    if span >= DELTA_DAYS - 0.5:
        text = M("monitor.delta_days", d=d, n=round(span))
    else:
        text = f"{d} {since_text(ref[0])}"
    return text, pct, ref[0]


def card(mid, d=None, pts=None):
    """La scheda di un monitoraggio per l'app (op monitor e monitors)."""
    d = d or need(mid)
    pts = read_points(mid) if pts is None else pts
    unit, form = d.get("unit") or "", d["form"]
    raw = []
    for name, ps in _by_series(d, pts):
        if form == "bars":
            seq = _per_day(ps, "sum")
        elif form == "status":
            seq = _per_day(ps, "last")
        else:
            seq = [(p["ts"], p["value"]) for p in ps]
        raw.append((name, seq))
    hits = _hits(d, raw)
    main = next((s for s in raw if s[1]), raw[0] if raw else ("", []))
    last = main[1][-1] if main[1] else None
    c = {"id": mid, "title": d["title"], "form": form, "unit": unit or None, "status": "watch" if hits else "ok",
         "verdict": d.get("verdict") or None, "verdict_at": d.get("verdict_at"),
         "value": None, "delta": None, "delta_pct": None, "delta_since": None,
         "at": max((p["ts"] for p in pts), default=None), "source": d.get("source") or None, "every": d.get("every") or None,
         "example": bool(d.get("example")), "alerts": [alert_view(a, unit) for a in d.get("alerts") or []], "hits": hits,
         "series": [], "link": link(mid)}
    if form == "status":
        if last:
            c["value"] = M("monitor.yes") if last[1] else M("monitor.no")
            same = 0
            for _, v in reversed(main[1]):
                if bool(v) != bool(last[1]):
                    break
                same += 1
            c["delta"] = M("monitor.days_in_state", n=same)
    elif last:
        c["value"] = fmt_val(last[1], unit)
        c["delta"], c["delta_pct"], c["delta_since"] = _delta(main[1], unit)
    if form == "meter":
        c["max"] = d.get("max")
        c["limit"] = fmt_val(d["max"], unit) if d.get("max") is not None else None
        c["pct"] = round(last[1] / d["max"] * 100, 1) if last and d.get("max") else None
    for name, seq in raw:
        vals = [v for _, v in seq]
        c["series"].append({"name": name or None, "points": reduce(seq), "n": len(seq),
                            "last": vals[-1] if vals else None, "last_text": fmt_val(vals[-1], unit) if vals else None,
                            "min": min(vals) if vals else None, "max": max(vals) if vals else None})
    if form == "table":
        c["rows"] = [{"name": s["name"], "value": s["last"], "text": s["last_text"]} for s in c["series"]]
    return c


def cards():
    out = []
    for mid in ids():
        try:
            out.append(card(mid))
        except Bad:
            continue
    return out


def summary():
    """1.132: il riassunto per /state — {n, watch, at}; None senza monitoraggi."""
    cs = cards()
    if not cs:
        return None
    return {"n": len(cs), "watch": sum(c["status"] == "watch" for c in cs), "at": max((c["at"] or 0 for c in cs), default=0) or None}


def line_of(c):
    body = c["verdict"] or " ".join(x for x in (c["value"], f"({c['delta']})" if c["delta"] else None) if x) or M("monitor.no_value")
    return f"{c['title']}: {body}" + (f" {c['link']}" if c["link"] else "")


# ------------------------------------------------------------------ «Dalla master»
def sync(mid, c=None):
    """La voce in «Dalla master» di un monitoraggio: una sola, aperta finche' una soglia e' superata, tolta quando il
    valore rientra o il monitoraggio non c'e' piu'. Rispetta «Mai» e una voce gia' accettata (non torna finche' regge)."""
    SG = _load("cm-suggest")
    now = int(time.time())
    key = f"monitor:{mid}"
    d = SG.load()
    before = json.dumps(d, sort_keys=True)
    rows = []
    old = next((x for x in d["items"] if x.get("key") == key), None)
    if c is None or not c["hits"]:
        if old:
            d["items"].remove(old)
            SG.close(d, old, "resolved", now, rows)
        d["accepted_live"].pop(key, None)
    elif key not in d["never"] and key not in d["accepted_live"]:
        reason = "; ".join(h["text"] for h in c["hits"])
        text = (c["verdict"] or f"{c['title']}: {reason}" + (f" ({c['value']})" if c["value"] else ""))[:SG.TEXT_MAX]
        prompt = M("monitor.suggest_prompt", title=c["title"], reason=reason, value=c["value"] or "-", id=mid, link=c["link"] or "-")
        SG.upsert(d, now, "monitor", key, text, prompt[:SG.PROMPT_MAX], SUGGEST_TTL_S, source="script")
        x = next(x for x in d["items"] if x.get("key") == key)
        if c["link"]:
            x["link"] = c["link"]
        x["monitor"] = mid
    if json.dumps(d, sort_keys=True) != before:
        SG.save(d, now)
    SG.ledger_write(rows)


# ------------------------------------------------------------------ comandi
def _num(s, what):
    try:
        v = float(str(s).replace(",", ".") if str(s).count(",") == 1 and "." not in str(s) else str(s))
    except ValueError:
        raise Bad(M("monitor.bad_number", what=what, value=str(s)[:40]))
    if v != v or v in (float("inf"), float("-inf")):
        raise Bad(M("monitor.bad_number", what=what, value=str(s)[:40]))
    return v


def _opt(argv, name):
    if name in argv:
        i = argv.index(name)
        if i + 1 >= len(argv):
            raise Bad(M("monitor.missing_value", opt=name))
        v = argv[i + 1]
        del argv[i:i + 2]
        return v
    return None


def _flag(argv, name):
    if name in argv:
        argv.remove(name)
        return True
    return False


def _ts(s):
    if s is None:
        return int(time.time())
    if re.fullmatch(r"\d{9,11}", s):
        return int(s)
    try:
        return int(dt.datetime.fromisoformat(s).timestamp())
    except ValueError:
        raise Bad(M("monitor.bad_ts", value=s[:40]))


def cmd_add(argv):
    mid = check_id(argv.pop(0) if argv else "")
    title, form, unit = _opt(argv, "--title"), _opt(argv, "--form"), _opt(argv, "--unit")
    series, source, every, mx = _opt(argv, "--series"), _opt(argv, "--source"), _opt(argv, "--every"), _opt(argv, "--max")
    example = _flag(argv, "--example")
    real = _flag(argv, "--real")
    if argv:
        raise Bad(M("monitor.unknown_option", opt=argv[0]))
    d = read_def(mid)
    new = d is None
    if new and (not title or not form):
        raise Bad(M("monitor.add_needs"))
    d = d or {"id": mid, "created": int(time.time()), "alerts": [], "series": []}
    if form is not None:
        if form not in FORMS:
            raise Bad(M("monitor.bad_form", form=form[:20], forms=", ".join(FORMS)))
        d["form"] = form
    if title is not None:
        d["title"] = title.strip()[:TITLE_MAX]
    if unit is not None:
        d["unit"] = unit.strip()[:UNIT_MAX]
    if series is not None:
        names = [s.strip() for s in series.split(",") if s.strip()]
        bad = [s for s in names if not SERIES_RE.fullmatch(s)]
        if bad:
            raise Bad(M("monitor.bad_series", series=bad[0][:40]))
        d["series"] = names
    if source is not None:
        d["source"] = source.strip()[:SOURCE_MAX]
    if every is not None:
        d["every"] = every.strip()[:20]
    if mx is not None:
        d["max"] = _num(mx, "--max")
    if example or real:
        d["example"] = example
    if d["form"] == "meter" and d.get("max") is None:
        raise Bad(M("monitor.meter_needs_max"))
    write_def(d)
    print(M("monitor.added" if new else "monitor.updated", id=mid, form=d["form"], link=link(mid) or "-"))
    sync(mid, card(mid, d))
    return 0


def cmd_alert(argv):
    d = need(argv.pop(0) if argv else "")
    if _flag(argv, "--clear"):
        d["alerts"] = []
    else:
        series = _opt(argv, "--series")
        below, above = _opt(argv, "--below"), _opt(argv, "--above")
        nmin, nmax = _flag(argv, "--new-min"), _flag(argv, "--new-max")
        if argv:
            raise Bad(M("monitor.unknown_option", opt=argv[0]))
        if sum(x is not None and x is not False for x in (below, above, nmin or None, nmax or None)) != 1:
            raise Bad(M("monitor.alert_one"))
        if series is not None and d.get("series") and series not in d["series"]:
            raise Bad(M("monitor.unknown_series", series=series[:40], id=d["id"], known=", ".join(d["series"])))
        kind = "below" if below is not None else "above" if above is not None else "new_min" if nmin else "new_max"
        a = {"kind": kind, "value": _num(below if below is not None else above, f"--{kind}") if kind in ("below", "above") else None,
             "series": series or None}
        d["alerts"] = [x for x in d.get("alerts") or [] if (x["kind"], x.get("series")) != (kind, a["series"])] + [a]
    write_def(d)
    c = card(d["id"], d)
    print(M("monitor.alerts", id=d["id"], alerts="; ".join(a["text"] for a in c["alerts"]) or M("monitor.none")))
    sync(d["id"], c)
    return 0


def cmd_point(argv):
    d = need(argv.pop(0) if argv else "")
    series, ts = _opt(argv, "--series"), _ts(_opt(argv, "--ts"))
    if len(argv) != 1:
        raise Bad(M("monitor.point_usage"))
    raw = argv[0]
    if d["form"] == "status":
        low = raw.strip().lower()
        if low not in YES + NO:
            raise Bad(M("monitor.bad_status", value=raw[:40]))
        value = 1.0 if low in YES else 0.0
    else:
        value = _num(raw, "VALORE")
    names = d.get("series") or []
    if series is None:
        if len(names) > 1 or (d["form"] in ("multiples", "table") and names):
            raise Bad(M("monitor.series_needed", id=d["id"], known=", ".join(names)))
        series = names[0] if names else ""
    elif names and series not in names and d["form"] != "table":
        raise Bad(M("monitor.unknown_series", series=series[:40], id=d["id"], known=", ".join(names)))
    elif not SERIES_RE.fullmatch(series):
        raise Bad(M("monitor.bad_series", series=series[:40]))
    p = _dir(d["id"])
    with open(p / "points.jsonl", "a") as f:
        f.write(json.dumps({"ts": ts, "series": series, "value": value}) + "\n")
    c = card(d["id"], d)
    sync(d["id"], c)
    print(line_of(c))
    return 0


def cmd_verdict(argv):
    d = need(argv.pop(0) if argv else "")
    if len(argv) != 1:
        raise Bad(M("monitor.verdict_usage"))
    d["verdict"] = " ".join(argv[0].split())[:VERDICT_MAX] or None
    d["verdict_at"] = int(time.time())
    write_def(d)
    c = card(d["id"], d)
    sync(d["id"], c)
    print(line_of(c))
    return 0


def cmd_list(argv):
    cs = cards()
    if "--json" in argv:
        print(json.dumps({"at": int(time.time()), "items": cs}, ensure_ascii=False))
        return 0
    if not cs:
        print(M("monitor.empty"))
        return 0
    for c in cs:
        mark = "!" if c["status"] == "watch" else " "
        print(f"{mark} {c['id']:<24} {c['form']:<9} {(c['value'] or '-'):>12}  {c['verdict'] or c['delta'] or ''}")
    return 0


def cmd_show(argv):
    c = card(check_id(argv[0] if argv else ""))
    if "--json" in argv:
        print(json.dumps(c, ensure_ascii=False))
        return 0
    print(f"{c['title']} ({c['id']}, {c['form']}{', ' + M('monitor.example') if c['example'] else ''})")
    print(f"  {M('monitor.status_' + c['status'])}: {c['value'] or '-'}{'  ' + c['delta'] if c['delta'] else ''}")
    if c["verdict"]:
        print(f"  {c['verdict']}")
    for s in c["series"]:
        print(f"  {s['name'] or '-'}: {s['n']} {M('monitor.points')}, {M('monitor.last')} {s['last_text'] or '-'}")
    for a in c["alerts"]:
        print(f"  {M('monitor.alert')}: {a['text']}{'  ←' if any(h['kind'] == a['kind'] and h['series'] == a['series'] for h in c['hits']) else ''}")
    if c["link"]:
        print(f"  {c['link']}")
    return 0


def cmd_line(argv):
    print(line_of(card(check_id(argv[0] if argv else ""))))
    return 0


def cmd_remove(argv):
    d = need(argv[0] if argv else "")
    shutil.rmtree(_dir(d["id"]))
    sync(d["id"], None)
    print(M("monitor.removed", id=d["id"]))
    return 0


def main(argv):
    fn = {"add": cmd_add, "alert": cmd_alert, "point": cmd_point, "verdict": cmd_verdict, "list": cmd_list,
          "show": cmd_show, "line": cmd_line, "remove": cmd_remove}.get(argv[0] if argv else "")
    if not fn:
        print(__doc__)
        return 2
    try:
        return fn(list(argv[1:]))
    except Bad as e:
        print(f"supervisor monitor: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
