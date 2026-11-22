#!/usr/bin/env python3
"""supervisor <comando> --opzione-sbagliata — il controllo comune delle opzioni lunghe, prima che il comando parta.

  cm-args.py COMANDO FILE [ARG ...]

Lo chiama il dispatcher (22/11/2026, supervisor-help-ignorato-22-11) quando fra gli argomenti c'e' almeno un'opzione
lunga («--qualcosa»): esce 0 se tutte le opzioni sono conosciute, 2 con una riga su stderr alla prima che non lo e'.
Prima ogni script decideva da se' e molti ignoravano quello che non conoscevano: `task list --opzione-inesistente`
stampava l'elenco dei compiti, `relay setup --boh` faceva il setup.

Un'opzione e' conosciuta se compare come testo nello script del comando, in uno degli script che quello nomina
direttamente (cm-*.py, cm-*.sh, `_load("cm-...")`) o nelle librerie comuni: e' il modo di sapere quali opzioni accetta
senza una tabella da tenere allineata a mano. Valgono anche `--<account>` (launch, restart e color accettano il nome di un
account come opzione). Dopo «--» non si guarda piu' niente: e' il confine dei comandi che passano argomenti ad altri
(bg -- comando). Il valore di `--opz=valore` non conta, solo il nome. Le opzioni corte (-x) non si controllano.
Il controllo legge i file e basta: non importa ne' esegue gli script.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = re.compile(r"""(?:\$S/|["'/\s(]|^)(cm-[a-z0-9-]+\.(?:py|sh))\b|_load\(\s*["'](cm-[a-z0-9-]+)["']""", re.M)
OPT = re.compile(r"(?<![A-Za-z0-9_-])--[A-Za-z][A-Za-z0-9_-]*")


COMMON = ("cm-lib.sh", "cm-config.py", "cm-core.py")   # le librerie che tutti usano


def sources(first):
    """Lo script del comando, quelli che nomina direttamente e le librerie comuni. Non oltre: seguiti fino in fondo
    gli script si nominano tutti fra loro (68 file, 2 MB, fino a 3 s per chiamata su un disco lento) e il controllo
    diventava «l'opzione esiste da qualche parte in supervisor». Provato il 22/11: a questa profondita' ogni opzione
    scritta nell'aiuto di un comando e' accettata (tests/help-ignorato-verify.py, HI5)."""
    first = Path(first).resolve()
    if not first.is_file():
        return []
    seen = [first]
    for m in NAME.finditer(first.read_text(encoding="utf-8", errors="replace")):
        p = (HERE / (m.group(1) or (m.group(2) + ".py"))).resolve()
        if p not in seen and p.is_file():
            seen.append(p)
    for c in COMMON:
        p = (HERE / c).resolve()
        if p not in seen and p.is_file():
            seen.append(p)
    return seen


def known(first):
    out = set()
    for p in sources(first):
        out.update(OPT.findall(p.read_text(encoding="utf-8", errors="replace")))
    return out


def _cm():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    if len(argv) < 2:
        print("usage: cm-args.py COMMAND FILE [ARG ...]", file=sys.stderr)
        return 2
    cmd, first, args = argv[0], argv[1], argv[2:]
    opts = []
    for a in args:
        if a == "--":
            break
        if a.startswith("--") and len(a) > 2:
            opts.append(a.split("=", 1)[0])
    if not opts:
        return 0
    ok = known(first)
    bad = [o for o in opts if o not in ok]
    if not bad:
        return 0
    cm = _cm()
    try:
        cfg = cm.load(warn=False)
    except Exception:   # una config rotta non deve far passare l'opzione: si rifiuta lo stesso, in inglese
        cfg = {}
    accounts = {f"--{k}" for k in (cfg.get("accounts") or {})}
    bad = [o for o in bad if o not in accounts]
    if not bad:
        return 0
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    print(cm.msg(cfg, "cli.unknown_option", cmd=cmd, opt=bad[0]), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
