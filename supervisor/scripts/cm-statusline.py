#!/usr/bin/env python3
"""supervisor statusline — la quota senza fable-director (14/10/2026, dall'analisi dei concorrenti).

  supervisor statusline                  statusline minima: modello, contesto, 5 ore, settimana
  supervisor statusline --wrap CMD ...   gira attorno a una statusline che c'e' gia': stesso input, la sua riga

Claude Code passa alla statusline un JSON con `rate_limits` (five_hour e seven_day: used_percentage, resets_at).
Questo script ne scrive `<quota.source>/quota-<hash>.json`, dove <hash> sono i primi 8 caratteri dello sha256 di
CLAUDE_CONFIG_DIR: lo stesso file, le stesse chiavi e la stessa regola di fusione della statusline di
fable-director, cosi' `supervisor quota`, la guardia e il relay leggono la quota anche dove fable-director non c'e'.
Nessuna rete, nessun token: solo il JSON che Claude Code gia' passa. Mai l'endpoint OAuth non documentato.

Si mette in settings.json: {"statusLine": {"type": "command", "command": "supervisor statusline"}}; con una
statusline propria: "supervisor statusline --wrap <il suo comando>".
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)
WINDOW_SLACK_S = 600   # due reset entro 10 minuti sono la stessa finestra (come fable-director)


def readings(d):
    """{five_hour_used_pct, five_hour_resets_at, weekly_used_pct, weekly_resets_at} dal JSON della statusline."""
    rl = d.get("rate_limits") if isinstance(d.get("rate_limits"), dict) else {}
    q = {}
    fh = rl.get("five_hour") if isinstance(rl.get("five_hour"), dict) else {}
    if fh.get("used_percentage") is not None:
        q["five_hour_used_pct"] = round(float(fh["used_percentage"]), 1)
    if fh.get("resets_at") is not None:
        q["five_hour_resets_at"] = fh["resets_at"]
    for k in ("seven_day", "weekly", "seven_days"):
        wd = rl.get(k) if isinstance(rl.get(k), dict) else {}
        if wd.get("used_percentage") is not None:
            q["weekly_used_pct"] = round(float(wd["used_percentage"]), 1)
            if wd.get("resets_at") is not None:
                q["weekly_resets_at"] = wd["resets_at"]
            break
    unknown = sorted(set(rl) - {"five_hour", "seven_day", "weekly", "seven_days"})
    if unknown:
        q["unknown_buckets"] = unknown
    return q


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def merge(q, old, now):
    """La regola di fable-director: dentro una finestra la quota non scende (a pari reset il massimo); un reset piu'
    avanti e' una finestra nuova; uno piu' indietro e' una lettura vecchia; un bucket assente resta finche' non scade."""
    mq = dict(q)
    for pk, rk in (("weekly_used_pct", "weekly_resets_at"), ("five_hour_used_pct", "five_hour_resets_at")):
        ov, orr = old.get(pk), _num(old.get(rk))
        nv, nr = q.get(pk), _num(q.get(rk))
        if ov is None:
            continue
        if nv is None:
            if orr is None or orr > now:
                mq[pk] = ov
                if old.get(rk) is not None:
                    mq[rk] = old[rk]
            continue
        if orr is None or nr is None:
            continue
        if nr < orr - WINDOW_SLACK_S:
            mq[pk], mq[rk] = ov, old.get(rk)
        elif abs(nr - orr) <= WINDOW_SLACK_S:
            mq[pk] = max(float(ov), float(nv))
    return mq


def quota_file():
    conf = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(cm.expand(CFG["quota"]["source"])) / f"quota-{hashlib.sha256(conf.encode()).hexdigest()[:8]}.json"


def write(q, now=None):
    """Scrive il file se la lettura cambia qualcosa, in modo atomico (piu' sessioni ridisegnano insieme)."""
    if not q:
        return
    f = quota_file()
    try:
        old = json.loads(f.read_text()) if f.is_file() else {}
    except (OSError, ValueError):
        old = {}
    if not isinstance(old, dict):
        old = {}
    mq = merge(q, old, now or time.time())
    if mq == old:
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(mq))
    os.replace(tmp, f)


def line(d, q):
    model = ((d.get("model") or {}).get("display_name") or "").strip() if isinstance(d.get("model"), dict) else ""
    ctx = (d.get("context_window") or {}).get("used_percentage") if isinstance(d.get("context_window"), dict) else None
    parts = [model] if model else []
    if ctx is not None:
        parts.append(f"ctx {float(ctx):.0f}%")
    if q.get("five_hour_used_pct") is not None:
        parts.append(f"5h {q['five_hour_used_pct']:.0f}%")
    if q.get("weekly_used_pct") is not None:
        parts.append(f"week {q['weekly_used_pct']:.0f}%")
    return " · ".join(parts)


def main(argv):
    raw = sys.stdin.read()
    try:
        d = json.loads(raw) if raw.strip() else {}
    except ValueError:
        d = {}
    if not isinstance(d, dict):
        d = {}
    q = readings(d)
    try:
        write(q)
    except OSError:
        pass   # la statusline si disegna comunque: la quota e' un extra
    if argv and argv[0] == "--wrap":
        if len(argv) < 2:
            print("usage: supervisor statusline --wrap <command>", file=sys.stderr)
            return 2
        p = subprocess.run(argv[1:] if len(argv) > 2 else argv[1], shell=len(argv) == 2, input=raw, capture_output=True, text=True, timeout=10)
        sys.stdout.write(p.stdout)
        return p.returncode
    print(line(d, q))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
