#!/usr/bin/env python3
"""Nessun file del plugin supera i 256 KiB che la directory dei plugin legge per intero (24/11/2026, supervisor-relay-256kb-24-11).

Sulla 0.8.205 il portale della directory aveva il blocco «Files or downloads the validator couldn't inspect» per
scripts/cm-relay.py (293.132 byte): un file di testo oltre i 256 KiB il validatore non lo legge tutto, e il blocco resta
anche da organizzazione Verified. cm-relay.py e' diviso: cm-relay-sessions.py e cm-relay-measure.py sono parti eseguite
nel suo namespace, come cm-relay-cmds.py dal 25/10.

PF1 nessun file sotto supervisor/ (come lo impacchetta release.sh: senza __pycache__, *.pyc, *.bak*, evals/results) supera
    262.144 byte
PF2 ogni parte che cm-relay.py esegue nel suo namespace c'e', e' in CODE_FILES (una modifica riavvia il demone) e non
    ha una docstring, che diventerebbe il __doc__ del relay
PF3 cm-relay.py con le parti al loro posto (T.relay_src) contiene le funzioni spostate, nell'ordine di prima
PF4 il relay caricato ha le funzioni delle parti nel suo namespace, e una funzione di una parte vede quello che un test
    cambia nel modulo del relay (la stessa tabella dei nomi)
"""
import fnmatch
import importlib.util
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

LIMIT = 256 * 1024
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}
SKIP_FILES = ("*.pyc", "*.bak*")
big = []
for root, dirs, files in os.walk(T.PLUGIN):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (d == "results" and Path(root).name == "evals")]
    for f in files:
        p = Path(root) / f
        if not any(fnmatch.fnmatch(f, g) for g in SKIP_FILES) and p.stat().st_size > LIMIT:
            big.append(f"{p.relative_to(T.PLUGIN)} ({p.stat().st_size} byte)")
T.check("PF1 no file of the plugin is over 256 KiB (262,144 bytes), the size the plugin directory reads in full", not big, ", ".join(big))

src = (T.SCRIPTS / "cm-relay.py").read_text(encoding="utf-8")
parts = re.findall(r'^(_[A-Z_]+) = HERE / "(cm-relay-[a-z]+\.py)"\nexec\(compile\(\1\.read_text', src, re.M)
names = [fn for _v, fn in parts]
spec = importlib.util.spec_from_file_location("cm_relay_pf", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RL)
nodoc = [fn for fn in names if (T.SCRIPTS / fn).read_text(encoding="utf-8").lstrip().startswith(('"' * 3, "'" * 3))]
T.check("PF2 every part cm-relay.py runs in its namespace exists, is in CODE_FILES and has no docstring of its own (the new ones; cm-relay-cmds.py has had one since 25/10)",
        {"cm-relay-cmds.py", "cm-relay-sessions.py", "cm-relay-measure.py"} <= set(names) and all((T.SCRIPTS / fn).is_file() for fn in names)
        and all(fn in RL.CODE_FILES for fn in names) and not [fn for fn in nodoc if fn != "cm-relay-cmds.py"], f"{names} {RL.CODE_FILES} {nodoc}")

full = T.relay_src()
order = ["def suggestions_list(now):", "def close_sessions(cmd, arg, session=\"\"):", "def commitments_list():", "def push(",
         "STATS_PUSH = re.compile(", "def main(argv):"]
pos = [full.find(x) for x in order]
T.check("PF3 cm-relay.py with its parts in place (T.relay_src) has the moved functions, in the order they had", all(p >= 0 for p in pos) and pos == sorted(pos),
        str(list(zip(order, pos))))

_save = RL.closed_dir
RL.closed_dir = lambda: Path("/patched/closed")
try:
    _cf = RL.closed_file("alfa")
finally:
    RL.closed_dir = _save
T.check("PF4 the loaded relay has the parts' functions in its namespace, and they see what a test patches on the relay module",
        all(callable(getattr(RL, n, None)) for n in ("suggestions_list", "usage_today", "close_sessions", "attese_view", "commitments_list"))
        and RL.usage_today.__globals__ is vars(RL) and RL.close_sessions.__code__.co_filename.endswith("cm-relay-sessions.py")
        and _cf == Path("/patched/closed/alfa.json") and bool(RL.STATS_PUSH.pattern), f"{_cf} globals same: {RL.usage_today.__globals__ is vars(RL)}")
T.finish()
