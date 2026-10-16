#!/usr/bin/env python3
"""Scadenze da un calendario .ics (16/10/2026): le leggono il recap e `supervisor next`.

  supervisor calendar [--json]     le scadenze nel periodo di avviso, adesso

L'indirizzo del calendario porta quasi sempre un token, quindi non sta in config.json ma in un file di segreti
accanto (`calendar.url_file`, default ~/.config/cc-supervisor/calendar.env, permessi 0600), una riga
`CALENDAR_ICS_URL=https://...`. Senza quel file la funzione e' spenta e nessuno script va in rete.

Una scadenza e' un VEVENT. Entra nel periodo di avviso quando mancano al massimo N giorni alla data: N viene dal
suo VALARM (TRIGGER:-P3D, -P1W, -PT12H arrotondato al giorno pieno; con piu' allarmi vale il piu' lontano), e
senza VALARM da `calendar.remind_days`. Le passate non contano. La proprieta' `calendar.case_property` (default
X-STUDIO-CASE) porta l'id della pratica; `calendar.cases` la collega a una cartella, cosi' la master sa dove
aprire la sessione.

Il feed scaricato si tiene in <state_dir>/calendar.ics per `calendar.cache_min` minuti: `next` si chiama spesso e
non deve aspettare la rete ogni volta. Senza rete si usa la copia vecchia, se c'e', e l'errore si dice comunque.
Il token non si scrive mai: ne' nei messaggi d'errore ne' nella cache.
"""
import datetime as dt
import importlib.util
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
C = CFG["calendar"]
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731


def url():
    """CALENDAR_ICS_URL dal file dei segreti (KEY=VALUE, apici tollerati), o ''."""
    try:
        for raw in Path(cm.expand(C["url_file"])).read_text().splitlines():
            k, _, v = raw.strip().partition("=")
            if k.strip() == "CALENDAR_ICS_URL":
                return v.strip().strip("'\"")
    except OSError:
        return ""
    return ""


def cache_path():
    return Path(cm.expand(CFG["state_dir"])) / "calendar.ics"


def fetch():
    """(testo .ics o '', errore o ''). La cache fresca evita la rete; senza rete vale la copia vecchia."""
    u = url()
    if not u:
        return "", ""
    p = cache_path()
    try:
        fresh = time.time() - p.stat().st_mtime < float(C["cache_min"]) * 60
    except OSError:
        fresh = False
    if fresh:
        return p.read_text(errors="replace"), ""
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "supervisor-calendar"})
        with urllib.request.urlopen(req, timeout=float(C["timeout_s"])) as r:
            text = r.read().decode("utf-8", errors="replace")
        if "BEGIN:VCALENDAR" not in text:
            raise ValueError("not a calendar")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(text)
        os.chmod(tmp, 0o600)
        tmp.replace(p)
        return text, ""
    except Exception:   # noqa: BLE001 — rete, DNS, HTTP, contenuto: per chi legge e' «non raggiungibile»
        old = p.read_text(errors="replace") if p.is_file() else ""
        return old, M("calendar.unreachable")


def unfold(text):
    """Righe logiche (RFC 5545 §3.1): una riga che comincia con spazio o tab continua la precedente."""
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        elif line:
            out.append(line)
    return out


def unescape(v):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def parse_date(params, value):
    """DTSTART → data locale. DATE, datetime flottante o con TZID (presa com'e'), UTC con Z (convertita)."""
    v = value.strip()
    try:
        if ("VALUE=DATE" in params.upper() and "VALUE=DATE-TIME" not in params.upper()) or re.fullmatch(r"\d{8}", v):
            return dt.date(int(v[:4]), int(v[4:6]), int(v[6:8]))
        t = dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
        if v.endswith("Z"):
            t = t.replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
        return t.date()
    except ValueError:
        return None


def trigger_days(value):
    """TRIGGER prima dell'evento → giorni pieni (-P3D → 3, -P1W → 7, -PT12H → 1, -P1DT6H → 2); altro → None."""
    m = re.fullmatch(r"-P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip().upper())
    if not m or not any(m.groups()):
        return None
    w, d, h, mi, s = (int(x or 0) for x in m.groups())
    secs = ((w * 7 + d) * 24 + h) * 3600 + mi * 60 + s
    return -(-secs // 86400)


def parse(text):
    """Gli eventi del calendario: [{uid, summary, date, remind, case}]."""
    prop = str(C.get("case_property") or "X-STUDIO-CASE").upper()
    events, ev, alarm = [], None, False
    for line in unfold(text):
        head, _, value = line.partition(":")
        name, _, params = head.partition(";")
        name = name.upper()
        if name == "BEGIN" and value.upper() == "VEVENT":
            ev, alarm = {"uid": "", "summary": "", "date": None, "remind": None, "case": ""}, False
        elif ev is None:
            continue
        elif name == "BEGIN" and value.upper() == "VALARM":
            alarm = True
        elif name == "END" and value.upper() == "VALARM":
            alarm = False
        elif name == "END" and value.upper() == "VEVENT":
            if ev["date"]:
                events.append(ev)
            ev = None
        elif alarm:
            if name == "TRIGGER" and "RELATED=END" not in params.upper() and "VALUE=DATE-TIME" not in params.upper():
                n = trigger_days(value)
                if n is not None:
                    ev["remind"] = max(ev["remind"] or 0, n)
        elif name == "DTSTART":
            ev["date"] = parse_date(params, value)
        elif name == "SUMMARY":
            ev["summary"] = " ".join(unescape(value).split())
        elif name == "UID":
            ev["uid"] = value.strip()
        elif name == prop:
            ev["case"] = unescape(value).strip()
    return events


def today():
    """Oggi, o CM_TODAY (YYYY-MM-DD) nei test."""
    t = os.environ.get("CM_TODAY")
    return dt.date.fromisoformat(t) if t else dt.date.today()


def due(events, day=None):
    """Le scadenze nel loro periodo di avviso, la piu' vicina per prima, con giorni mancanti e cartella."""
    day = day or today()
    cases = C.get("cases") or {}
    out = []
    for e in events:
        left = (e["date"] - day).days
        remind = e["remind"] if e["remind"] is not None else int(C["remind_days"])
        if 0 <= left <= remind:
            folder = cases.get(e["case"]) if e["case"] else None
            out.append({**e, "date": e["date"].isoformat(), "days_left": left, "remind": remind,
                        "dir": cm.expand(folder) if folder else ""})
    out.sort(key=lambda e: (e["days_left"], e["summary"]))
    return out


def deadlines():
    """(scadenze in avviso, errore o ''). Spenta (nessun indirizzo) → ([], '')."""
    text, err = fetch()
    return (due(parse(text)) if text else []), err


def when(e):
    """«oggi», «domani», «tra 3 giorni»."""
    n = e["days_left"]
    return M("calendar.today") if n == 0 else M("calendar.tomorrow") if n == 1 else M("calendar.in_days", n=n)


def line(e):
    """Una riga leggibile: «19/10 (tra 3 giorni) Titolo · pratica 42 → /cartella»."""
    d = dt.date.fromisoformat(e["date"]).strftime("%d/%m")
    out = f"{d} ({when(e)}) {e['summary'] or '?'}"
    if e["case"]:
        out += " · " + M("calendar.case", case=e["case"])
        if e["dir"]:
            out += f" → {cm.contract(e['dir'])}"
    return out


def main(argv):
    items, err = deadlines()
    if "--json" in argv:
        print(json.dumps({"deadlines": items, "error": err}, ensure_ascii=False, indent=1))
        return 0
    if not url():
        print(M("calendar.off", path=C["url_file"]))
        return 0
    for e in items:
        print(line(e))
    if err:
        print(err)
    elif not items:
        print(M("calendar.none"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
