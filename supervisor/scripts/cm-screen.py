#!/usr/bin/env python3
"""supervisor screen — lo schermo di una sessione, per vedere dal telefono cosa sta facendo.

  supervisor screen <nome> [--lines N] [--join]   le ultime N righe (30) del riquadro tmux della sessione

`capture-pane -p` col nome nudo (T1: `-t =nome` non risolve per capture-pane), righe vuote in coda
tolte. `--join` aggiunge `-J`: tmux riunisce le righe che ha mandato a capo dentro la larghezza del
riquadro, cosi' chi ha uno schermo stretto (l'orologio, 13/09/2026) non legge parole spezzate a meta'
e riformatta lui; il taglio a `--lines` vale sulle righe LOGICHE. Dal telefono: «schermo NOME» alla master → questo comando, risposta in un blocco di codice.
Il testo dopo «❯» è un SUGGERIMENTO di Claude Code, non dell'utente (regola 1 del kernel).
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
sessions = _load("cm-sessions")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731


def tmux(*args):
    return subprocess.run(sessions.TMUX + list(args), capture_output=True, text=True)


def main(argv):
    lines = 30
    name = ""
    join = False
    i = 0
    while i < len(argv):
        if argv[i] == "--lines" and i + 1 < len(argv) and argv[i + 1].isdigit():
            lines = int(argv[i + 1]); i += 2
        elif argv[i] in ("--join", "--unisci"):
            join = True; i += 1
        elif not name and not argv[i].startswith("-"):
            name = argv[i]; i += 1
        else:
            print(M("screen.usage"), file=sys.stderr)
            return 2
    if not name:
        print(M("screen.usage"), file=sys.stderr)
        return 2
    if tmux("has-session", "-t", f"={name}").returncode != 0:
        print(M("screen.no_session", name=name), file=sys.stderr)
        return 1
    rows = tmux("capture-pane", "-pJ" if join else "-p", "-t", name).stdout.splitlines()
    while rows and not rows[-1].strip():
        rows.pop()
    print("\n".join(rows[-lines:]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
