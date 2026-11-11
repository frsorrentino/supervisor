#!/usr/bin/env python3
"""supervisor recurring — le azioni ricorrenti della master, per il box «Ricorrenti» dell'app (contratto 1.33, 04/10).

  supervisor recurring list [--json]
  supervisor recurring add ID --label L --prompt P [--param]   aggiunge o aggiorna, e la porta in cima
  supervisor recurring remove ID
  supervisor recurring used ID                                 la porta in cima (eseguita adesso)

La lista sta sul PC (recurring.json accanto alla config; CM_RECURRING per i test), una sola per telefono, tablet e
Chromebook: il relay la pubblica come state.recurring [{id, label, prompt, param}], dall'ultima usata, al massimo 8.
ID: [a-z0-9-], stabile. label: al massimo 40 caratteri. param: il prompt aspetta un pezzo da aggiungere (un link, un
numero di release) e l'app non offre l'invio diretto. La riempie la master, con l'ok dell'utente: la prima volta le
azioni fatte almeno 3 volte, poi quando nota una ripetizione o le si dice «aggiungi ai ricorrenti».
"""
import json
import os
import re
import sys
import time
from pathlib import Path

ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
LABEL_MAX = 40
PROMPT_MAX = 2000
STATE_MAX = 8


def path():
    p = os.environ.get("CM_RECURRING")
    if p:
        return Path(p)
    c = (os.environ.get("CC_SUPERVISOR_CONFIG") or os.environ.get("TEAM_SUPERVISOR_CONFIG") or os.environ.get("CLAUDE_MASTER_CONFIG"))
    return (Path(c).parent if c else Path.home() / ".config" / "cc-supervisor") / "recurring.json"


def load():
    try:
        d = json.loads(path().read_text())
        return [x for x in d if isinstance(x, dict) and ID_RE.fullmatch(str(x.get("id") or ""))] if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def save(items):
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, p)


def ordered(items):
    return sorted(items, key=lambda x: (-(x.get("used_at") or 0), -(x.get("added_at") or 0), x["id"]))


def for_state(items=None):
    """1.33: la lista per lo stato — [{id, label, prompt, param}] dall'ultima usata, al massimo 8; [] se vuota."""
    return [{"id": x["id"], "label": str(x.get("label") or "")[:LABEL_MAX], "prompt": str(x.get("prompt") or ""),
             "param": bool(x.get("param"))} for x in ordered(load() if items is None else items)[:STATE_MAX]]


def mark_used(text=None, rid=None, now=None):
    """La porta in cima: per id, o per il testo di un prompt che comincia con quello di una voce (un prompt dal telefono
    alla master, anche con il pezzo aggiunto di un `param`). Torna l'id toccato o None."""
    items = load()
    t = " ".join(str(text or "").split())
    hit = next((x for x in items if (rid and x["id"] == rid)
                or (t and " ".join(str(x.get("prompt") or "").split()) and t.startswith(" ".join(str(x.get("prompt") or "").split())))), None)
    if not hit:
        return None
    hit["used_at"] = int(now or time.time())
    save(items)
    return hit["id"]


def main(argv):
    args = list(argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    verb = args.pop(0)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]

    def opt(name):
        if name in args:
            i = args.index(name)
            if i + 1 >= len(args):
                sys.exit(f"recurring: {name} needs a value")
            v = args[i + 1]
            del args[i:i + 2]
            return v
        return None
    if verb == "list":
        items = for_state(load()) if as_json else ordered(load())
        if as_json:
            print(json.dumps(items, ensure_ascii=False))
        else:
            print("\n".join(f"{x['id']:<24} {x.get('label', '')}{'  [param]' if x.get('param') else ''}" for x in items) or "no recurring actions")
        return 0
    if verb == "add":
        label, prompt = opt("--label"), opt("--prompt")
        param = "--param" in args
        args = [a for a in args if a != "--param"]
        if len(args) != 1 or not label or not prompt:
            sys.exit("usage: recurring add ID --label L --prompt P [--param]")
        rid = args[0]
        if not ID_RE.fullmatch(rid):
            sys.exit("recurring: ID is [a-z0-9-], 40 characters at most")
        if len(label) > LABEL_MAX or len(prompt) > PROMPT_MAX or not label.strip() or not prompt.strip():
            sys.exit(f"recurring: label 1..{LABEL_MAX} characters, prompt 1..{PROMPT_MAX}")
        items = [x for x in load() if x["id"] != rid]
        now = int(time.time())
        old = next((x for x in load() if x["id"] == rid), {})
        items.append({"id": rid, "label": label.strip(), "prompt": prompt.strip(), "param": param,
                      "added_at": old.get("added_at") or now, "used_at": now})
        save(items)
        print(rid)
        return 0
    if verb == "remove":
        if len(args) != 1:
            sys.exit("usage: recurring remove ID")
        items = load()
        rest = [x for x in items if x["id"] != args[0]]
        if len(rest) == len(items):
            sys.exit(f"recurring: no {args[0]}")
        save(rest)
        return 0
    if verb == "used":
        if len(args) != 1:
            sys.exit("usage: recurring used ID")
        if not mark_used(rid=args[0]):
            sys.exit(f"recurring: no {args[0]}")
        return 0
    sys.exit(f"recurring: unknown verb {verb}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
