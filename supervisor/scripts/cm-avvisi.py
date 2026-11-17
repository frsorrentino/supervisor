#!/usr/bin/env python3
"""I messaggi di Telegram del mattino e della sera, e le voci degli altri script (17/11/2026, supervisor-avvisi-telegram-17-11).

  supervisor recap morning [--send]                       il mattino (dal cron alle recap.morning_time, 08:00)
  supervisor recap add --kind K "testo" [--date AAAA-MM-GG]   una voce per il recap delle 20 di quel giorno
  (supervisor recap --send, alle 20, manda evening_text)

Su Telegram tre tipi di messaggio e nient'altro, scelti dal maintainer il 17/11:
1. l'allarme, subito, solo se serve un'azione (relay fermo o push fallite da oltre 15 minuti: cm-relay; quota sopra il
   92%: cm-guard), due righe col link all'app;
2. il mattino: le quote dei due account con l'ora di azzeramento, Firebase del mese, lo spazio dei server
   (recap.servers_file, lo scrive spazio-server.py), i monitoraggi sopra soglia e le scadenze del calendario;
3. il recap delle 20: uscito (i compiti chiusi oggi), ti aspetta (le voci aperte col link), stanotte (la coda della
   notte), la notte appena passata (night-done.jsonl: `night run --send` non manda piu' a parte) e le voci del giorno
   di `recap add` (i rilasci di Claude Code, l'adozione del lunedi').
Italiano, niente emoji, la prima riga dice cosa e' successo. Le voci di `recap add` stanno in
<state_dir>/recap-extra.jsonl ({at, day, kind, text}), tenute RECAP_EXTRA_DAYS giorni.
"""
import calendar
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
D = CFG.get("recap") or {}
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿⏩-⏺️‍]")
KIND_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,30}")
RECAP_EXTRA_DAYS = 30
WAIT_MAX = 8
SERVERS_FILE = "~/Desktop/workspaces/.claude/spazio-server.json"
SERVER_PCT = 80
SERVER_HIGH = {"alto", "critico", "attenzione", "pieno", "high", "critical", "warn", "warning", "alarm"}
FIREBASE_MONTH_GB = 10


def clean(text):
    return " ".join(EMOJI.sub("", str(text or "")).split())


def state_dir():
    return Path(cm.expand(CFG["state_dir"]))


def day_label(d):
    return f"{M('avvisi.days').split(',')[d.weekday()]} {d.strftime('%d/%m')}"


def when(epoch):
    t = dt.datetime.fromtimestamp(float(epoch))
    return M("avvisi.when", day=day_label(t.date()), t=t.strftime("%H:%M"))


def num(x, digits=1):
    return f"{x:.{digits}f}".replace(".", ",") if M("avvisi.decimal") == "," else f"{x:.{digits}f}"


def plural(n, key):
    return M(f"avvisi.{key}_one" if n == 1 else f"avvisi.{key}", n=n)


def read_jsonl(p):
    out = []
    try:
        for line in Path(p).read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ recap add
def extra_path():
    return state_dir() / "recap-extra.jsonl"


def cmd_add(argv):
    kind, day, words = None, dt.date.today(), []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--kind", "--tipo") and i + 1 < len(argv):
            kind, i = argv[i + 1], i + 2
            continue
        if a in ("--date", "--data") and i + 1 < len(argv):
            try:
                day = dt.date.fromisoformat(argv[i + 1])
            except ValueError:
                print(M("avvisi.add_bad_date", date=argv[i + 1]), file=sys.stderr)
                return 2
            i += 2
            continue
        words.append(a)
        i += 1
    text = clean(" ".join(words))[:300]
    if not kind or not KIND_RE.fullmatch(kind) or not text:
        print(M("avvisi.add_usage"), file=sys.stderr)
        return 2
    cut = (dt.date.today() - dt.timedelta(days=RECAP_EXTRA_DAYS)).isoformat()
    rows = [r for r in read_jsonl(extra_path()) if str(r.get("day") or "") >= cut]
    rows.append({"at": int(time.time()), "day": day.isoformat(), "kind": kind, "text": text})
    p = extra_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(p)
    print(M("avvisi.added", kind=kind, day=day.strftime("%d/%m")))
    return 0


def extras(day):
    out = OrderedDict()
    for r in read_jsonl(extra_path()):
        if r.get("day") == day.isoformat() and r.get("kind") and r.get("text"):
            out.setdefault(r["kind"], []).append(clean(r["text"]))
    return out


def kind_label(kind):
    key = f"avvisi.kind_{kind}"
    own = cm.messages(CFG.get("language", "en")).get(key) or cm.messages("en").get(key)
    return own or kind.replace("-", " ").capitalize()


# ------------------------------------------------------------------ la sera
def closed_on(day):
    """I titoli dei compiti chiusi (done) quel giorno, dal registro; [] senza registro."""
    try:
        tk = _load("cm-tasks")
        con = tk.connect(create=False)
    except Exception:   # noqa: BLE001
        return []
    if con is None:
        return []
    a = int(time.mktime(day.timetuple()))
    b = a + 86400
    try:
        rows = con.execute("SELECT t.id, t.title FROM closures c JOIN tasks t ON t.id=c.task WHERE t.state='done' AND c.at>=? AND c.at<? ORDER BY c.at",
                           (a, b)).fetchall()
    except Exception:   # noqa: BLE001
        rows = []
    finally:
        con.close()
    return [clean(r[1] or r[0])[:90] for r in rows]


def waiting():
    """Le voci aperte che aspettano la persona (Tocca a te), col link dell'app."""
    try:
        return [a for a in _load("cm-attese").collect() if a.get("state", "open") == "open"]
    except Exception:   # noqa: BLE001 — senza registro ne' stato il recap esce lo stesso
        return []


def night_queue():
    return read_jsonl(cm.expand((CFG.get("night") or {}).get("queue_file") or "~/.cc-supervisor/night-queue.jsonl"))


def night_done(day):
    return [r for r in read_jsonl(state_dir() / "night-done.jsonl") if str(r.get("finished") or "").startswith(day.isoformat())]


def evening_text(day=None):
    day = day or dt.date.today()
    out, waits, queue, done, ex = closed_on(day), waiting(), night_queue(), night_done(day), extras(day)
    what = [plural(len(out), "n_out")] if out else []
    if waits:
        what.append(plural(len(waits), "n_wait"))
    lines = [M("avvisi.evening_title", day=day_label(day), what=", ".join(what) or M("avvisi.nothing_new"))]
    lines.append(M("avvisi.out", items=", ".join(out)) if out else M("avvisi.out_none"))
    if waits:
        lines.append(M("avvisi.wait_head"))
        for a in waits[:WAIT_MAX]:
            lines.append(M("avvisi.wait_item", title=clean(a.get("title") or a.get("text") or a.get("kind"))[:80], link=a.get("link") or "-"))
        if len(waits) > WAIT_MAX:
            lines.append(M("avvisi.wait_more", n=len(waits) - WAIT_MAX))
    else:
        lines.append(M("avvisi.wait_none"))
    if queue:
        dirs = ", ".join(OrderedDict((os.path.basename(str(r.get("dir") or "").rstrip("/")) or "?", 1) for r in queue))
        lines.append(M("avvisi.tonight", jobs=plural(len(queue), "jobs"), dirs=dirs))
    else:
        lines.append(M("avvisi.tonight_none"))
    if done:
        ok = sum(1 for r in done if r.get("rc") == 0)
        lines.append(M("avvisi.night", jobs=plural(len(done), "jobs"), ok=plural(ok, "ok")))
        for r in done:
            lines.append(M("avvisi.night_item", mark="ok" if r.get("rc") == 0 else "KO", dir=os.path.basename(str(r.get("dir") or "").rstrip("/")) or "?",
                           prompt=clean(r.get("prompt"))[:70]))
    for kind, texts in ex.items():
        lines.append(M("avvisi.extra", kind=kind_label(kind), items="; ".join(texts)))
    return "\n".join(lines)


# ------------------------------------------------------------------ il mattino
def quota_file(acc):
    conf = cm.expand(CFG["accounts"][acc]["config_dir"])
    f = Path(cm.expand(CFG["quota"]["source"])) / f"quota-{hashlib.sha256(str(conf).encode()).hexdigest()[:8]}.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None


def quota_lines(now):
    """Una riga per account: la settimana con il giorno e l'ora dell'azzeramento; le cinque ore solo sopra la soglia
    della guardia. Torna (righe, quante vicino alla soglia)."""
    near_pct = float((CFG.get("quota") or {}).get("warn_pct", 85))
    warn = float((CFG.get("guard") or {}).get("warn_pct", 92))
    lines, near = [], 0
    for acc in CFG["accounts"]:
        d = quota_file(acc)
        if not d:
            lines.append(M("avvisi.quota_none", account=acc))
            continue
        w, w_at = d.get("weekly_used_pct"), d.get("weekly_resets_at")
        h, h_at = d.get("five_hour_used_pct"), d.get("five_hour_resets_at")
        parts = []
        if w is not None:
            parts.append(M("avvisi.quota_week", pct=round(float(w)), when=when(w_at) if w_at else "-"))
        if h is not None and (w is None or float(h) >= warn):
            parts.append(M("avvisi.quota_5h", pct=round(float(h)), t=dt.datetime.fromtimestamp(float(h_at)).strftime("%H:%M") if h_at else "-"))
        if not parts:
            lines.append(M("avvisi.quota_none", account=acc))
            continue
        if max(float(x) for x in (w, h) if x is not None) >= near_pct:
            near += 1
        lines.append(M("avvisi.quota", account=acc, parts="; ".join(parts)))
    return lines, near


def firebase_line(now):
    """Firebase del mese: la stima del relay (usage-firebase.json, come doctor.firebase_usage) sommata sui giorni del mese."""
    rd = Path(cm.expand((CFG.get("relay") or {}).get("dir") or "~/.cc-supervisor/relay"))
    try:
        usage = json.loads((rd / "usage-firebase.json").read_text())
    except (OSError, ValueError):
        return None, False
    try:
        devices = len(json.loads((rd / "devices.json").read_text()) or {}) or 1
    except (OSError, ValueError):
        devices = 1
    today = dt.date.fromtimestamp(now)
    month = today.strftime("%Y-%m-")
    b = sum(float(r.get("result_bytes", 0)) + float(r.get("state_bytes", 0)) * devices
            for k, r in (usage or {}).items() if isinstance(r, dict) and str(k).startswith(month))
    gb = b / 1e9
    proj = gb * calendar.monthrange(today.year, today.month)[1] / max(1, today.day)
    name = M("avvisi.months").split(",")[today.month - 1]
    return M("avvisi.firebase", gb=num(gb), limit=FIREBASE_MONTH_GB, month=name, pct=round(100 * gb / FIREBASE_MONTH_GB),
             proj=num(proj, 0)), proj > FIREBASE_MONTH_GB


def _pct(v):
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(v if v is not None else ""))
    return float(m.group(0).replace(",", ".")) if m else None


def servers_line():
    """Lo spazio dei server (recap.servers_file, campi livello, liberi_gb, uso, ora): solo quelli sopra
    recap.server_pct o con un livello d'allarme. Il file puo' essere {nome: {...}}, [{nome, ...}] o {"server(s)": ...}."""
    p = Path(cm.expand(D.get("servers_file") or SERVERS_FILE))
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None, 0
    if isinstance(data, dict):
        data = data.get("servers", data.get("server", data))
    items = []
    if isinstance(data, dict):
        items = [(str(k), v) for k, v in data.items() if isinstance(v, dict)]
    elif isinstance(data, list):
        items = [(str(v.get("nome") or v.get("name") or v.get("server") or v.get("host") or "?"), v) for v in data if isinstance(v, dict)]
    if not items:
        return None, 0
    lim = float(D.get("server_pct", SERVER_PCT))
    high = []
    for name, v in items:
        uso = _pct(v.get("uso"))
        if (uso is not None and uso >= lim) or str(v.get("livello") or "").lower() in SERVER_HIGH:
            free = _pct(v.get("liberi_gb"))
            high.append(M("avvisi.server_item", name=name, uso="?" if uso is None else round(uso),
                          free=("" if free is None else M("avvisi.server_free", gb=round(free)))))
    if not high:
        return M("avvisi.servers_ok", pct=round(lim)), 0
    rest = M("avvisi.servers_rest", pct=round(lim)) if len(high) < len(items) else ""
    return M("avvisi.servers", items=", ".join(high), rest=rest), len(high)


def monitors_line():
    try:
        mon = _load("cm-monitor")
        cs = mon.cards()
    except Exception:   # noqa: BLE001
        return None, 0
    if not cs:
        return None, 0
    hot = [clean(mon.line_of(c)) for c in cs if c.get("status") == "watch"]
    return (M("avvisi.monitors", items="; ".join(hot)) if hot else M("avvisi.monitors_none")), len(hot)


def deadlines_line():
    try:
        cal = _load("cm-calendar")
        if not cal.url():
            return None
        items, err = cal.deadlines()
    except Exception:   # noqa: BLE001
        return None
    if not items:
        return None
    return M("avvisi.deadlines", items="; ".join(clean(cal.line(e)) for e in items))


def morning_text(now=None):
    now = now or time.time()
    lines, near = quota_lines(now)
    fb, fb_high = firebase_line(now)
    if fb:
        lines.append(fb)
        near += int(fb_high)
    for fn in (servers_line, monitors_line):
        line, n = fn()
        if line:
            lines.append(line)
            near += n
    dl = deadlines_line()
    if dl:
        lines.append(dl)
    title = M("avvisi.morning_title", day=day_label(dt.date.fromtimestamp(now)), what=plural(near, "n_near") if near else M("avvisi.all_below"))
    return "\n".join([title] + lines)


def send(text):
    bot = _load("cm-bot")
    if not bot.token():
        print(M("bot.no_token", path=CFG["bot"]["token_file"]), file=sys.stderr)
        return 1
    print(M("recap.sent", n=bot.send(text)))
    return 0


def cmd_morning(argv):
    text = morning_text()
    print(text)
    return send(text) if "--send" in argv or "--invia" in argv else 0


def main(argv):
    verb = argv[0] if argv else ""
    if verb == "add":
        return cmd_add(argv[1:])
    if verb in ("morning", "mattino"):
        return cmd_morning(argv[1:])
    if verb in ("evening", "sera"):
        print(evening_text())
        return 0
    print(M("avvisi.add_usage"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
