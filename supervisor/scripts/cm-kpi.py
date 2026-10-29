#!/usr/bin/env python3
"""supervisor kpi — i numeri per le viste KPI dell'app (contratto 1.96, 29/10/2026).

Proposte del 25/10 (mockup in supervisor-app, docs/proposte/2026-10-25-kpi/), approvate dal maintainer alle 21:47 per
dopo il rinnovo della quota del 29/10. Un blocco per proposta, tutto da file locali, senza modello:

  kpi_tasks           (8)  i compiti aperti: quanti, quanti aspettano la persona, fermi, chiusi oggi; per compito eta',
                           tentativi del controllo spesi (risultati rossi) su attempts_max, chi aspetta
  kpi_checks_week     (9)  i controlli dei compiti degli ultimi 7 giorni, verdi e rossi per giorno
  kpi_turns_today     (10) per sessione viva, i turni di oggi: inizio, fine, esito, token (input, scrittura in cache,
                           output) e letture dalla cache, dall'usage della trascrizione
  kpi_health          (12) la latenza dalla push alla lettura sul dispositivo (ultima ora), le sessioni di questo PC
                           sul tetto sessions.max_sessions, carico e memoria degli altri host
  kpi_suggestions_7d  (13) come sono finiti i suggerimenti degli ultimi 7 giorni
  kpi_waits           (17) chi aspetta la persona adesso e da quando; la mediana dei minuti d'attesa per giorno

Le attese si registrano da oggi (record_waits, a ogni push): inizio e fine di ogni domanda, permesso o needs-you per
sessione in <relay>/waits.jsonl; la timeline le mostra. Niente ricostruzione del passato. Il limite per modello
(proposta 3) non c'e': la statusline di Claude Code passa solo five_hour e seven_day.

Uso: cm-kpi.py [--json]
"""
import importlib.util
import json
import os
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
TASKS_MAX = 12
TURNS_MAX = 30
WAIT_KINDS = ("question", "permission", "needs_you")
OPEN = ("approved", "assigned", "running", "paused", "awaiting_ok")


def day_start(now, days_back=0):
    d = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
    return int(d.timestamp())


def _json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def _jsonl(p):
    out = []
    try:
        for line in Path(p).read_text().splitlines():
            try:
                x = json.loads(line)
            except ValueError:
                continue
            if isinstance(x, dict):
                out.append(x)
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ 8, 9: il registro dei compiti
def kpi_tasks(now, con, commitments):
    """{open, waiting_you, stalled, closed_today, tasks: [{task, short, state, age_s, attempts, attempts_max, stalled, who}]}"""
    red = {r[0]: r[1] for r in con.execute("SELECT task, COUNT(*) FROM results WHERE outcome != 'green' GROUP BY task")}
    mx = {}
    for tid, contract in con.execute("SELECT id, contract FROM tasks"):
        try:
            mx[tid] = int(((json.loads(contract or "{}").get("task") or {}).get("attempts_max")) or 3)
        except (ValueError, TypeError, AttributeError):
            mx[tid] = 3
    closed = con.execute("SELECT COUNT(*) FROM tasks WHERE state = 'done' AND updated_at >= ?", (day_start(now),)).fetchone()[0]
    rows = [c for c in commitments if c.get("state") in OPEN or c.get("approved") is False]
    waiting = [c for c in rows if (c.get("waits") or {}).get("on") == "person" or c.get("approved") is False]
    tasks = [{"task": c["task"], "short": c.get("short") or c.get("title") or c["task"], "state": c.get("state"),
              "age_s": int(c.get("age_s") or 0), "attempts": int(red.get(c["task"], 0)), "attempts_max": mx.get(c["task"], 3),
              "stalled": bool(c.get("stalled")), "who": (c.get("waits") or {}).get("who")} for c in rows[:TASKS_MAX]]
    return {"open": len(rows), "waiting_you": len(waiting), "stalled": sum(1 for c in rows if c.get("stalled")),
            "closed_today": int(closed), "tasks": tasks}


def kpi_checks_week(now, con):
    """[{date, green, red}] per i 7 giorni fino a oggi, dal piu' vecchio: ogni controllo rieseguito di un compito."""
    first = day_start(now, 6)
    days = [{"date": datetime.fromtimestamp(day_start(now, 6 - i)).strftime("%Y-%m-%d"), "green": 0, "red": 0} for i in range(7)]
    idx = {d["date"]: d for d in days}
    for outcome, at in con.execute("SELECT outcome, at FROM results WHERE at >= ?", (first,)):
        d = idx.get(datetime.fromtimestamp(int(at)).strftime("%Y-%m-%d"))
        if d is not None:
            d["green" if outcome == "green" else "red"] += 1
    return days


# ------------------------------------------------------------------ 13: suggerimenti
def kpi_suggestions_7d(now, history):
    """{accepted, discussed, later, never, expired} sugli esiti dei 7 giorni (accepted comprende chosen)."""
    out = {"accepted": 0, "discussed": 0, "later": 0, "never": 0, "expired": 0}
    key = {"accepted": "accepted", "chosen": "accepted", "discussed": "discussed", "hidden": "later", "never": "never", "expired": "expired"}
    for h in history or []:
        k = key.get(str(h.get("action") or ""))
        if k and float(h.get("at") or 0) >= now - 7 * 86400:
            out[k] += 1
    return out


# ------------------------------------------------------------------ 17: attese
def waits_now(state):
    """{(sessione, tipo): da quando} dallo stato intero: la domanda aperta, il permesso (stato waiting senza domanda), il
    needs-you."""
    out = {}
    for s in (state or {}).get("sessions") or []:
        name = s.get("name")
        if not name:
            continue
        if s.get("question"):
            out[(name, "question")] = int(s["question"].get("asked_at") or s.get("since") or 0)
        elif s.get("state") == "waiting":
            out[(name, "permission")] = int(s.get("since") or 0)
        if s.get("needs_you"):
            out[(name, "needs_you")] = int(s["needs_you"].get("at") or 0)
    return out


def waits_paths(rdir):
    return Path(rdir) / "waits.jsonl", Path(rdir) / "waits-open.json"


def record_waits(state, now, rdir):
    """A ogni push: inizio e fine di ogni attesa in waits.jsonl ({event: start|end, session, kind, at, since}). Le attese
    gia' aperte stanno in waits-open.json; la prima volta si aprono quelle in corso, mai il passato."""
    log_p, open_p = waits_paths(rdir)
    cur = waits_now(state)
    if open_p.exists():
        prev = {(x["session"], x["kind"]): int(x["since"]) for x in _json(open_p, []) if isinstance(x, dict)}
    else:   # il file delle aperte e' sparito: si ricavano dal log, mai un secondo inizio della stessa attesa
        prev = {}
        for r in _jsonl(log_p):
            k = (r.get("session"), r.get("kind"))
            if r.get("event") == "start":
                prev[k] = int(r.get("since") or r.get("at") or 0)
            elif r.get("event") == "end":
                prev.pop(k, None)
    rows = []
    for (s, k), since in sorted(cur.items()):
        if (s, k) not in prev:
            rows.append({"event": "start", "session": s, "kind": k, "at": int(since or now), "since": int(since or now)})
    for (s, k), since in sorted(prev.items()):
        if (s, k) not in cur:
            rows.append({"event": "end", "session": s, "kind": k, "at": int(now), "since": since})
    if rows:
        with open(log_p, "a") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if rows or not open_p.exists():
        open_p.write_text(json.dumps([{"session": s, "kind": k, "since": v} for (s, k), v in sorted(cur.items())]))
    return rows


def wait_rows(rows):
    """Le righe di waits.jsonl con un'attesa sola per (sessione, tipo, since): il primo inizio e la fine solo se e' l'ultima
    riga del gruppo. 29/10: una push che per un giro non vede la domanda scrive una fine, la successiva un nuovo inizio
    con lo stesso since; e' la stessa attesa, mai due nella timeline o nella mediana."""
    last = {}
    for i, r in enumerate(rows):
        last[(r.get("session"), r.get("kind"), int(r.get("since") or r.get("at") or 0))] = i
    seen, out = set(), []
    for i, r in enumerate(rows):
        k = (r.get("session"), r.get("kind"), int(r.get("since") or r.get("at") or 0))
        if r.get("event") == "start" and k not in seen:
            seen.add(k)
            out.append(r)
        elif r.get("event") == "end" and last[k] == i:
            out.append(r)
    return out


def kpi_waits(state, now, rdir):
    """{now: [{session, kind, since}], week: [{date, median_min, n}]} — la settimana dalle attese chiuse (da oggi in poi)."""
    cur = [{"session": s, "kind": k, "since": v} for (s, k), v in sorted(waits_now(state).items(), key=lambda x: x[1])]
    first = day_start(now, 6)
    days = {datetime.fromtimestamp(day_start(now, 6 - i)).strftime("%Y-%m-%d"): [] for i in range(7)}
    for r in wait_rows(_jsonl(waits_paths(rdir)[0])):
        if r.get("event") == "end" and int(r.get("at") or 0) >= first:
            d = datetime.fromtimestamp(int(r["at"])).strftime("%Y-%m-%d")
            if d in days:
                days[d].append(max(0, int(r["at"]) - int(r.get("since") or r["at"])) / 60)
    week = [{"date": d, "median_min": round(statistics.median(v), 1) if v else None, "n": len(v)} for d, v in days.items()]
    return {"now": cur, "week": week}


# ------------------------------------------------------------------ 10: turni di oggi
def _is_prompt(x):
    if x.get("type") != "user" or x.get("isMeta") or x.get("isSidechain") or x.get("toolUseResult") is not None:
        return False
    c = (x.get("message") or {}).get("content")
    if isinstance(c, str):
        return bool(c.strip()) and not c.lstrip().startswith("<")
    if isinstance(c, list):
        return any(isinstance(b, dict) and b.get("type") == "text" and str(b.get("text") or "").strip() for b in c) \
            and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
    return False


def _ts(x):
    try:
        return int(datetime.fromisoformat(str(x.get("timestamp")).replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return None


def turns_of(path, since, status=None):
    """I turni di una trascrizione da `since`: [{start, end, outcome, tokens, cache_read}]. outcome ok, fail (un errore
    dell'API nel turno), question (l'ultimo turno di una sessione che aspetta) o running (l'ultimo, sessione al lavoro)."""
    turns, cur, seen_ids = [], None, set()
    try:
        fh = open(path, errors="replace")
    except OSError:
        return []
    with fh:
        for line in fh:
            if '"timestamp"' not in line:
                continue
            try:
                x = json.loads(line)
            except ValueError:
                continue
            at = _ts(x)
            if at is None or at < since:
                continue
            if _is_prompt(x):
                cur = {"start": at, "end": at, "outcome": "ok", "tokens": 0, "cache_read": 0}
                turns.append(cur)
                continue
            if cur is None or x.get("type") != "assistant" or x.get("isSidechain"):
                continue
            cur["end"] = at
            m = x.get("message") or {}
            if x.get("isApiErrorMessage") or m.get("model") == "<synthetic>":
                cur["outcome"] = "fail"
            mid = m.get("id")
            u = m.get("usage") or {}
            if u and mid not in seen_ids:
                seen_ids.add(mid)
                cur["tokens"] += int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + int(u.get("output_tokens") or 0)
                cur["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
    if turns and status in ("busy", "waiting"):
        turns[-1]["outcome"] = "running" if status == "busy" else "question"
    return turns[-TURNS_MAX:]


def kpi_turns_today(sessions, now):
    """[{session, turns}] per le sessioni [(nome, trascrizione, stato)] con almeno un turno oggi."""
    since = day_start(now)
    out = []
    for name, path, status in sessions:
        t = turns_of(path, since, status) if path else []
        if t:
            out.append({"session": name, "turns": t})
    return out


# ------------------------------------------------------------------ 12: salute
def latency_path(rdir):
    return Path(rdir) / "kpi-latency.jsonl"


def record_latency(rdir, at, seconds):
    """Un campione: un dispositivo ha letto `seconds` dopo la push. Si tengono le ultime 2 ore."""
    if not (0 <= seconds < 600):
        return
    p = latency_path(rdir)
    rows = [r for r in _jsonl(p) if float(r.get("at") or 0) >= at - 7200] + [{"at": round(at, 1), "s": round(seconds, 2)}]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def kpi_health(state, rows, now, rdir):
    """{latency: {p50_s, max_s, n}, sessions_here, sessions_max, hosts: [{name, up, cpu, ram_pct, disk_pct}]}"""
    lat = [float(r["s"]) for r in _jsonl(latency_path(rdir)) if float(r.get("at") or 0) >= now - 3600 and "s" in r]
    here = sum(1 for r in rows or [] if (not r.get("host") or r.get("host") == "local") and r.get("status") in ("busy", "idle", "waiting"))
    hosts = []
    for h in (state or {}).get("hosts_status") or []:
        pct = lambda used, tot: round(100 * float(used) / float(tot)) if used is not None and tot else None  # noqa: E731
        free, dtot = h.get("disk_free"), h.get("disk_total")
        hosts.append({"name": h.get("name"), "up": bool(h.get("up")), "cpu": h.get("cpu"), "ram_pct": pct(h.get("ram_used"), h.get("ram_total")),
                      "disk_pct": pct(dtot - free, dtot) if free is not None and dtot else None})
    return {"latency": {"p50_s": round(statistics.median(lat), 2) if lat else None, "max_s": round(max(lat), 2) if lat else None, "n": len(lat)},
            "sessions_here": here, "sessions_max": int((CFG.get("sessions") or {}).get("max_sessions", 5)), "hosts": hosts}


def collect(now, state, rows, sessions, rdir, con=None, commitments=None, history=None):
    """Tutti i blocchi; un blocco che non si legge vale null (mai l'op persa per una fonte)."""
    out = {"at": int(now)}
    if con is not None:
        try:
            out["kpi_tasks"] = kpi_tasks(now, con, commitments or [])
            out["kpi_checks_week"] = kpi_checks_week(now, con)
        except Exception:   # noqa: BLE001
            out["kpi_tasks"] = out["kpi_checks_week"] = None
    else:
        out["kpi_tasks"] = out["kpi_checks_week"] = None
    for k, fn in (("kpi_turns_today", lambda: kpi_turns_today(sessions, now)), ("kpi_health", lambda: kpi_health(state, rows, now, rdir)),
                  ("kpi_suggestions_7d", lambda: kpi_suggestions_7d(now, history)), ("kpi_waits", lambda: kpi_waits(state, now, rdir))):
        try:
            out[k] = fn()
        except Exception:   # noqa: BLE001
            out[k] = None
    return out


if __name__ == "__main__":
    rd = Path(cm.expand(((CFG.get("relay") or {}).get("dir")) or "~/.cc-supervisor/relay"))
    st = (_json(rd / "last-state.json", {}) or {}).get("full") or {}
    tk = _load("cm-tasks")
    c = tk.connect(create=False)
    res = collect(time.time(), st, [], [], rd, c, tk.commitments(c, time.time()) if c else [], (_load("cm-suggest").load() or {}).get("history"))
    print(json.dumps(res, ensure_ascii=False, indent=None if "--json" in sys.argv else 1))
