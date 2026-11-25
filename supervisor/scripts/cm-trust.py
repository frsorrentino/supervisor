#!/usr/bin/env python3
"""cm-trust — la fiducia nella cartella prima di avviare Claude Code (25/11/2026, supervisor-fiducia-cartella-25-11).

Il maintainer alle 12:24: «spesso quando su una sessione appena avviata appare la scritta trust this folder? in
supervisor non appare niente, invece dovrebbe prima rispondere yes in automatico». Claude Code chiede la fiducia alla
prima apertura di una cartella e la ricorda in .claude.json (projects.<cartella>.hasTrustDialogAccepted); una sessione
aperta da supervisor (launch, «Riapri» e lancio dall'app, servizio d'avvio, restart) non ha nessuno davanti allo schermo.

  cm-trust.py ensure CARTELLA CONFIG_DIR   segna la cartella come fidata nel .claude.json dell'account e stampa
                                          trusted (scritta ora), already (c'era), no_config (l'account non ha ancora
                                          .claude.json: c'e' l'accesso da fare), outside (fuori dalla radice: niente)
                                          o error
  cm-trust.py allowed CARTELLA            exit 0 se la cartella sta sotto workspace.root

Sicurezza: la domanda protegge da hook e server MCP di cartelle estranee. Il si' automatico vale solo per le cartelle
sotto la radice dei progetti (workspace.root, la stessa cartella o una sua discendente, dopo i link), mai per un
percorso qualsiasi; le cartelle di sessione di win le segna cm-adapters.session_trust. Dove sta .claude.json: la regola
T68 di cm-launch.sh, ~/.claude.json per l'account in ~/.claude, <config_dir>/.claude.json per gli altri.
"""
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)


def root():
    return os.path.realpath(cm.expand((CFG.get("workspace") or {}).get("root") or "~"))


def allowed(folder):
    """La cartella sotto la radice dei progetti (o la radice stessa), dopo i link. Mai con la radice «/»."""
    r = root()
    f = os.path.realpath(os.path.expanduser(str(folder)))
    if r == os.sep or not os.path.isdir(f):
        return False
    return f == r or f.startswith(r.rstrip(os.sep) + os.sep)


def state_file(config_dir):
    """Il .claude.json dell'account, con la regola T68: ~/.claude.json per ~/.claude, <config_dir>/.claude.json altrimenti."""
    home = Path(os.path.expanduser("~"))
    c = Path(os.path.expanduser(str(config_dir or home / ".claude")))
    if os.path.realpath(c) == os.path.realpath(home / ".claude"):
        return home / ".claude.json"
    return c / ".claude.json"


def ensure(folder, config_dir):
    if not allowed(folder):
        return "outside"
    f = state_file(config_dir)
    if not f.is_file():
        return "no_config"
    try:
        d = json.loads(f.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return "error"
    if not isinstance(d, dict):
        return "error"
    a = os.path.abspath(os.path.expanduser(str(folder)))
    keys = list(dict.fromkeys([a, os.path.realpath(a)]))   # la chiave di Claude Code: la cartella com'e' e senza link
    projects = d.setdefault("projects", {})
    if not isinstance(projects, dict):
        return "error"
    if all(isinstance(projects.get(k), dict) and projects[k].get("hasTrustDialogAccepted") is True for k in keys):
        return "already"
    for k in keys:
        p = projects.get(k) if isinstance(projects.get(k), dict) else {}
        p["hasTrustDialogAccepted"] = True
        projects[k] = p
    try:
        bak = f.with_name(f.name + ".cm-bak")
        if not bak.exists():
            shutil.copy2(f, bak)
        tmp = f.with_name(f"{f.name}.cm-{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        shutil.copymode(f, tmp)
        os.replace(tmp, f)   # scrittura atomica: Claude Code non legge mai un file a meta'
    except OSError:
        return "error"
    return "trusted"


def main(argv):
    if argv[:1] == ["ensure"] and len(argv) == 3:
        print(ensure(argv[1], argv[2]))
        return 0
    if argv[:1] == ["allowed"] and len(argv) == 2:
        return 0 if allowed(argv[1]) else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
