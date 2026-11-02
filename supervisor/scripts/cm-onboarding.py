#!/usr/bin/env python3
"""I cinque livelli dell'avvio (specifica del 17/10/2026). Ogni livello si ricava dallo stato reale; <state_dir>/onboarding.json
tiene solo le scelte della persona: «piu' tardi» e «non chiedere piu'». Il copione arriva alla master da cm-hook, block().

  supervisor doctor --onboarding [--json] [--mark N later|never|open]
"""
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
LATER_S = 86400


def M(_msg, **kw):
    return cm.msg(CFG, _msg, **kw)


def path():
    return Path(cm.expand(CFG["state_dir"])) / "onboarding.json"


def _json(p):
    try:
        d = json.loads(Path(p).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _out(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def facts():
    """{livello: fatto?} dallo stato reale."""
    relay = CFG.get("relay") or {}
    devices = _json(Path(cm.expand(relay.get("dir") or "")) / "devices.json")
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    try:
        cmd = [os.environ.get("CM_ONBOARDING_REGISTRY") or str(HERE / "supervisor")] + ([] if os.environ.get("CM_ONBOARDING_REGISTRY") else ["registry", "--good"])
        names = {x.get("nome") for x in json.loads(_out(cmd) or "{}").get("sessioni") or []}
    except ValueError:
        names = set()
    try:   # dal vivo, 02/11: il registro «buono» non elenca la master; le sessioni aperte si'
        names |= {x.get("name") for x in json.loads(_out([str(HERE / "supervisor"), "sessions", "--json"]) or "[]")}
    except (ValueError, TypeError, AttributeError):
        pass
    cron = _out([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-l"])
    return {"1": master in names and len(names) > 1, "2": bool(devices), "3": bool(devices) and bool(str(relay.get("firebase_url") or "").strip()),
            "4": any(d.get("kind") == "watch" or "watch" in str(d.get("name") or "").lower() for d in devices.values() if isinstance(d, dict)),
            "5": "night run" in cron}, master in names


def differs():
    """Le impostazioni diverse da quelle iniziali: si elencano e si chiede, mai cambiate di nascosto."""
    out = []
    for keys, default in ((("sessions", "max_sessions"), 5), (("recap", "cron_time"), "20:00"), (("night", "cron_time"), "02:00")):
        v = (CFG.get(keys[0]) or {}).get(keys[1], default)
        if v != default:
            out.append({"key": ".".join(keys), "value": v, "default": default})
    return out


def read(now=None):
    now = now or time.time()
    done, master_seen = facts()
    saved = _json(path())
    if not saved:
        saved = {"mode": "recovery" if any(done.values()) else "new", "at": int(now), "levels": {}}
        try:
            path().parent.mkdir(parents=True, exist_ok=True)
            path().write_text(json.dumps(saved))
        except OSError:
            pass
    levels = {}
    for n in map(str, range(1, 6)):
        s = (saved.get("levels") or {}).get(n) or {}
        if done[n]:
            levels[n] = {"state": "done"}
        elif s.get("state") == "refused" or (s.get("state") == "later" and float(s.get("until") or 0) > now):
            levels[n] = dict(s)
        else:
            levels[n] = {"state": "open"}
    reached = max([int(n) for n in done if done[n]] or [0])
    nxt = next((int(n) for n, x in levels.items() if x["state"] == "open"), None)
    return {"mode": saved.get("mode"), "reached": reached, "next": nxt, "levels": levels, "master_seen": master_seen, "differs": differs()}


def mark(n, what, now=None):
    if n not in {"1", "2", "3", "4", "5"} or what not in ("later", "never", "open"):
        return False
    read(now)
    d = _json(path())
    now = now or time.time()
    rows = d.setdefault("levels", {})
    rows[n] = {"later": {"state": "later", "until": int(now + LATER_S), "at": int(now)}, "never": {"state": "refused", "at": int(now)}, "open": {"state": "open", "at": int(now)}}[what]
    path().write_text(json.dumps(d))
    return True


def block():
    """Il copione, solo con un livello da proporre: altrimenti niente, e l'avvio della master non costa un carattere."""
    r = read()
    if r["next"] is None:
        return ""
    f = HERE.parent / f"onboarding_{CFG.get('language')}.md"
    if not f.is_file():
        f = HERE.parent / "onboarding_en.md"
    rows = ", ".join(f"{n} {M('onboarding.state_' + x['state'])}" for n, x in r["levels"].items())
    return f.read_text(encoding="utf-8").strip() + "\n" + M("onboarding.block_status", mode=r["mode"], rows=rows, next=M(f"onboarding.level{r['next']}"))


def main(argv):
    if "--mark" in argv:
        i = argv.index("--mark")
        if len(argv) < i + 3 or not mark(argv[i + 1], argv[i + 2]):
            print("doctor --onboarding --mark N later|never|open (N 1-5)", file=sys.stderr)
            return 2
    r = read()
    if "--json" in argv:
        print(json.dumps(r, ensure_ascii=False))
        return 0
    for n, x in r["levels"].items():
        print(f"{n}. {M('onboarding.level' + n)}: {M('onboarding.state_' + x['state'])}")
    print(M("onboarding.reached", level=r["reached"], next=M(f"onboarding.level{r['next']}") if r["next"] else M("onboarding.all_done")))
    if not r["master_seen"]:
        print(M("onboarding.never_master", root=(CFG.get("workspace") or {}).get("root") or "~"))
    for d in r["differs"]:
        print(M("onboarding.differs", **d))
    return 0
