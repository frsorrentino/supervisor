#!/usr/bin/env python3
"""supervisor suggest — i suggerimenti della master per la home dell'app (contratto 1.84, 24/10/2026).

  supervisor suggest add --kind K --key KEY --text T --prompt P [--ttl 12h]
  supervisor suggest list [--json]
  supervisor suggest clear [--kind K]

La master li prepara due volte al giorno (alle 8 e alle 19, dal registro e dal diario), non a ogni turno. kind:
recurring | cleanup | deadline | quota. key: stabile per lo stesso suggerimento (cleanup:paused:albo-*), serve per
«Mai». text: la frase per la persona, una riga, mai con «…». prompt: quello che riceve la master se la persona accetta.
--ttl (s, m, h, d; senza: 12 h): poi il suggerimento scade da solo.

Il relay li pubblica come state.suggestions [{id, kind, key, text, prompt, at, expires_at}], dal piu' recente, al
massimo 6. L'app risponde con l'op suggestion {arg: id, action}: accept manda il prompt alla master e toglie la voce;
later la nasconde fino al giro dopo (un `add` con la stessa key la rimette, con il testo nuovo); never mette la key in
una lista che `add` poi salta in silenzio (esce 0, niente scritto).

Il file sta accanto alla config (suggestions.json; CM_SUGGESTIONS per i test).
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

KINDS = ("recurring", "cleanup", "deadline", "quota")
KEY_MAX = 120
TEXT_MAX = 200
PROMPT_MAX = 2000
STATE_MAX = 6
DEFAULT_TTL_S = 12 * 3600
DURATION_RE = re.compile(r"^(\d+)([smhd])$")
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def path():
    p = os.environ.get("CM_SUGGESTIONS")
    if p:
        return Path(p)
    c = (os.environ.get("CC_SUPERVISOR_CONFIG") or os.environ.get("TEAM_SUPERVISOR_CONFIG") or os.environ.get("CLAUDE_MASTER_CONFIG"))
    return (Path(c).parent if c else Path.home() / ".config" / "cc-supervisor") / "suggestions.json"


def load():
    try:
        d = json.loads(path().read_text())
    except (OSError, ValueError):
        d = {}
    d = d if isinstance(d, dict) else {}
    return {"items": [x for x in d.get("items") or [] if isinstance(x, dict) and x.get("id")],
            "never": [str(k) for k in d.get("never") or []]}


def save(d):
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, p)


def live(items, now):
    return [x for x in items if int(x.get("expires_at") or 0) > now]


def for_state(now=None):
    """1.84: [{id, kind, key, text, prompt, at, expires_at}] delle voci non scadute e non nascoste, dalla piu' recente,
    al massimo 6; [] se non ce ne sono."""
    now = int(now or time.time())
    items = [x for x in live(load()["items"], now) if not x.get("hidden")]
    return [{k: x.get(k) for k in ("id", "kind", "key", "text", "prompt", "at", "expires_at")}
            for x in sorted(items, key=lambda x: (-int(x.get("at") or 0), x["id"]))[:STATE_MAX]]


def act(sid, action, now=None):
    """L'op suggestion: (codice, voce). codice: accepted | hidden | never | unknown | bad_action. accept toglie la voce
    (chi chiama manda il prompt), later la nasconde, never la toglie e mette la sua key fra quelle da saltare."""
    if action not in ("accept", "later", "never"):
        return "bad_action", None
    now = int(now or time.time())
    d = load()
    hit = next((x for x in live(d["items"], now) if x["id"] == sid and not x.get("hidden")), None)
    if not hit:
        return "unknown", None
    if action == "later":
        hit["hidden"] = now
    else:
        d["items"] = [x for x in d["items"] if x["id"] != sid]
        if action == "never" and hit.get("key") not in d["never"]:
            d["never"].append(hit["key"])
    save(d)
    return {"accept": "accepted", "later": "hidden", "never": "never"}[action], hit


def parse_duration(text):
    m = DURATION_RE.match(str(text or "").strip())
    return int(m.group(1)) * UNITS[m.group(2)] if m else None


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
                sys.exit(f"suggest: {name} needs a value")
            v = args[i + 1]
            del args[i:i + 2]
            return v
        return None
    now = int(time.time())
    if verb == "add":
        kind, key, text, prompt, ttl = opt("--kind"), opt("--key"), opt("--text"), opt("--prompt"), opt("--ttl")
        if args or not kind or not key or not text or not prompt:
            sys.exit("usage: suggest add --kind K --key KEY --text T --prompt P [--ttl 12h]")
        if kind not in KINDS:
            sys.exit(f"suggest: --kind is one of {', '.join(KINDS)}")
        key, text, prompt = key.strip(), " ".join(text.split()), prompt.strip()
        if not key or len(key) > KEY_MAX:
            sys.exit(f"suggest: --key 1..{KEY_MAX} characters")
        if not text or len(text) > TEXT_MAX or "…" in text:
            sys.exit(f"suggest: --text one line, 1..{TEXT_MAX} characters, never «…»")
        if not prompt or len(prompt) > PROMPT_MAX:
            sys.exit(f"suggest: --prompt 1..{PROMPT_MAX} characters")
        ttl_s = DEFAULT_TTL_S if ttl is None else parse_duration(ttl)
        if not ttl_s:
            sys.exit(f"suggest: --ttl wants a duration like 30m, 12h, 2d (not «{ttl}»)")
        d = load()
        if key in d["never"]:
            return 0   # «Mai» dall'app: saltato in silenzio
        sid = "s-" + hashlib.sha1(f"{key}\n{now}\n{text}".encode()).hexdigest()[:10]
        d["items"] = [x for x in live(d["items"], now) if x.get("key") != key]
        d["items"].append({"id": sid, "kind": kind, "key": key, "text": text, "prompt": prompt, "at": now, "expires_at": now + ttl_s})
        save(d)
        print(sid)
        return 0
    if verb == "list":
        d = load()
        items = sorted(live(d["items"], now), key=lambda x: -int(x.get("at") or 0))
        if as_json:
            print(json.dumps({"items": items, "never": d["never"]}, ensure_ascii=False))
        else:
            print("\n".join(f"{x['id']}  {x['kind']:<9} {x['text']}{'  [later]' if x.get('hidden') else ''}" for x in items) or "no suggestions")
        return 0
    if verb == "clear":
        kind = opt("--kind")
        if args or (kind is not None and kind not in KINDS):
            sys.exit("usage: suggest clear [--kind K]")
        d = load()
        d["items"] = [x for x in live(d["items"], now) if kind and x.get("kind") != kind]
        save(d)
        return 0
    sys.exit(f"suggest: unknown verb {verb}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
