#!/usr/bin/env python3
"""supervisor statusline (14/10/2026): la quota senza fable-director.

SL1 riga minima (modello, contesto, 5 ore, settimana) e il file quota-<hash>.json con le chiavi di fable-director
SL2 fusione: a pari reset non scende, un reset piu' avanti e' una finestra nuova, uno piu' indietro si ignora
SL3 senza rate_limits nessun file; --wrap passa lo stesso input al comando e stampa la sua riga
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="cm-statusline-"))
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "en", "quota": {"source": str(tmp / "q")}}))
conf = tmp / "conf"
ENV = dict(os.environ, CC_SUPERVISOR_CONFIG=str(cfg), CLAUDE_CONFIG_DIR=str(conf))
QF = tmp / "q" / f"quota-{hashlib.sha256(str(conf).encode()).hexdigest()[:8]}.json"


def run(payload, *args):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-statusline.py"), *args], input=json.dumps(payload), capture_output=True, text=True, env=ENV, timeout=30)


def rl(h5, r5, wk, rw):
    return {"model": {"display_name": "Opus 5.5"}, "context_window": {"used_percentage": 41},
            "rate_limits": {"five_hour": {"used_percentage": h5, "resets_at": r5}, "seven_day": {"used_percentage": wk, "resets_at": rw}}}


r = run(rl(22.4, 1792016400, 14, 1792468800))
q = json.loads(QF.read_text()) if QF.exists() else {}
T.check("SL1 minimal line and quota-<sha256(CLAUDE_CONFIG_DIR)[:8]>.json with fable-director's keys",
        r.returncode == 0 and r.stdout.strip() == "Opus 5.5 · ctx 41% · 5h 22% · week 14%"
        and q == {"five_hour_used_pct": 22.4, "five_hour_resets_at": 1792016400, "weekly_used_pct": 14.0, "weekly_resets_at": 1792468800}, r.stdout + r.stderr + str(q))
run(rl(10, 1792016500, 12, 1792468800))
q2 = json.loads(QF.read_text())
run(rl(3, 1792034400, 15, 1792468800))
q3 = json.loads(QF.read_text())
run(rl(1, 1791990000, 15, 1792468800))
q4 = json.loads(QF.read_text())
T.check("SL2 same window: the quota never goes down; a later reset is a new window; an earlier one is an old reading, ignored",
        q2["five_hour_used_pct"] == 22.4 and q2["weekly_used_pct"] == 14.0 and q3["five_hour_used_pct"] == 3.0 and q3["five_hour_resets_at"] == 1792034400
        and q4["five_hour_used_pct"] == 3.0 and q4["five_hour_resets_at"] == 1792034400, str((q2, q3, q4)))
QF.unlink()
r = run({"model": {"display_name": "Opus 5.5"}})
no_file = not QF.exists()
w = run(rl(5, 1792016400, 20, 1792468800), "--wrap", "cat")
T.check("SL3 no rate_limits → no file; --wrap gives the same JSON to the command, prints its line and still records the quota",
        r.returncode == 0 and no_file and w.returncode == 0 and json.loads(w.stdout)["rate_limits"]["five_hour"]["used_percentage"] == 5
        and QF.exists(), r.stdout + w.stdout[:200])
T.finish()
