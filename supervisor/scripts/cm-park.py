#!/usr/bin/env python3
"""supervisor park / unpark — ibernare una sessione e riprenderla (2.2, N6).

  supervisor park <nome> [--force]             registra cartella/account/conversazione e chiude
  supervisor park --idle-over <durata>         tutte le sessioni STACCATE e idle da piu' di 2h, 90m, ...
  supervisor park --ram-below <MB> [--auto]    se la RAM libera e' sotto la soglia, parcheggia una
                                                  sessione alla volta (la staccata idle da piu' tempo)
                                                  finche' non risale; --auto per il cron (silenzioso)
  supervisor park --list                       le sessioni parcheggiate
  supervisor unpark <nome>                     `launch <cartella> --resume <id> --account <acc>`
  Ogni verbo accetta --dry-run.

Perche': ogni sessione tiene 400-600 MB; con 8 sessioni la RAM satura e la macchina si
riavvia (e' la causa dei riavvii, non un caso). Una sessione staccata e idle da ore non sta
lavorando: si chiude registrando DOVE riprenderla, e `unpark` la riapre sulla stessa
conversazione (`--resume <id>`, mai `--continue`: T10). Mai una sessione attaccata, mai una
busy senza --force: chi la guarda la sta usando, chi lavora non va interrotto.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def _cm_proc():
    """cm-proc: processi e misure per Linux e macOS (11/11, supervisor-macos-11-11)."""
    import importlib.util as _u
    spec = _u.spec_from_file_location("cm_proc", Path(__file__).resolve().parent / "cm-proc.py")
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

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
PARKED = Path(cm.expand(CFG["state_dir"])) / "parked.json"


def load_parked():
    try:
        return json.load(open(PARKED))
    except (OSError, ValueError):
        return []


def save_parked(items):
    PARKED.parent.mkdir(parents=True, exist_ok=True)
    PARKED.write_text(json.dumps(items, ensure_ascii=False, indent=1))


def duration_min(s):
    m = re.fullmatch(r"(\d+)\s*(h|m|min|ore|d)?", s.strip())
    if not m:
        raise ValueError(s)
    n, u = int(m.group(1)), (m.group(2) or "m")
    return n * {"h": 60, "ore": 60, "d": 1440}.get(u, 1)


def idle_min(row):
    try:
        d = json.load(open(Path(row["registry"]) / f"{row['pid']}.json"))
        t = d.get("statusUpdatedAt") or d.get("updatedAt")
        return (time.time() - t / 1000) / 60 if t else None
    except (OSError, ValueError, TypeError):
        return None


def mem_available_mb():
    src = os.environ.get("CM_MEMINFO_FILE")
    if not src:   # 11/11: Linux e macOS da cm-proc; CM_MEMINFO_FILE resta per le prove
        return _cm_proc().memory()[1]
    try:
        for line in open(src):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


def park_one(row, dry, force):
    if row["attached"]:
        print(M("park.attached", name=row["tmux"]), file=sys.stderr)
        return False
    if row["status"] == "busy" and not force:
        print(M("park.busy", name=row["tmux"]), file=sys.stderr)
        return False
    if not row.get("session_id"):
        print(M("park.no_session_id", name=row["tmux"]), file=sys.stderr)
        return False
    rec = {"name": row["tmux"], "cwd": row["cwd"], "account": row["account"], "session_id": row["session_id"],
           "parked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if dry:
        print(M("park.would_park", name=row["tmux"], cwd=row["cwd"]))
        return True
    items = [i for i in load_parked() if i["name"] != rec["name"]]
    items.append(rec)
    save_parked(items)
    r = subprocess.run([str(HERE / "cm-close.sh"), row["tmux"]], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        return False
    print(M("park.parked", name=row["tmux"], cwd=row["cwd"], sid=row["session_id"]))
    return True


def cmd_park(argv):
    dry = "--dry-run" in argv or "--prova" in argv
    force = "--force" in argv
    if "--list" in argv:
        items = load_parked()
        if not items:
            print(M("park.none"))
        for i in items:
            print(f"{i['name']:<24} {i['account']:<13} {i['parked_at']}  {i['cwd']}")
        return 0
    rows = [r for r in sessions.collect(read_screen=False) if r["tmux"] and r["channel"] != "(questa)"]
    if "--idle-over" in argv:
        lim = duration_min(argv[argv.index("--idle-over") + 1])
        cands = [r for r in rows if not r["attached"] and r["status"] == "idle" and (idle_min(r) or 0) >= lim]
        if not cands:
            print(M("park.none_idle", min=lim))
        for r in sorted(cands, key=lambda r: -(idle_min(r) or 0)):
            park_one(r, dry, force)
        return 0
    if "--ram-below" in argv:
        limit = int(argv[argv.index("--ram-below") + 1])
        quiet = "--auto" in argv
        free = mem_available_mb()
        if free is None:
            print(M("park.no_meminfo"), file=sys.stderr)
            return 1
        if free >= limit:
            quiet or print(M("park.ram_ok", free=free, limit=limit))
            return 0
        cands = sorted([r for r in rows if not r["attached"] and r["status"] == "idle"], key=lambda r: -(idle_min(r) or 0))
        if not cands:
            print(M("park.ram_low_nothing", free=free, limit=limit), file=sys.stderr)
            return 1
        print(M("park.ram_low", free=free, limit=limit))
        park_one(cands[0], dry, False)   # una alla volta: il giro dopo rimisura
        return 0
    name = next((a for a in argv if not a.startswith("--")), "")
    if not name:
        print(M("park.usage"), file=sys.stderr)
        return 2
    row = next((r for r in rows if r["tmux"] == name or r["name"] == name), None)
    if not row:
        print(M("talk.missing", name=name), file=sys.stderr)
        return 3
    return 0 if park_one(row, dry, force) else 4


def cmd_unpark(argv):
    dry = "--dry-run" in argv or "--prova" in argv
    name = next((a for a in argv if not a.startswith("--")), "")
    items = load_parked()
    rec = next((i for i in items if i["name"] == name), None)
    if not rec:
        print(M("park.not_parked", name=name), file=sys.stderr)
        for i in items:
            print("  " + i["name"], file=sys.stderr)
        return 3
    cmd = [str(HERE / "cm-launch.sh"), rec["cwd"], "--resume", rec["session_id"], "--account", rec["account"]]
    cmd += [a for a in argv if a.startswith("--") and a not in ("--dry-run", "--prova")]
    if dry:
        print(" ".join(cmd))
        return 0
    r = subprocess.run(cmd, text=True)
    if r.returncode == 0:
        save_parked([i for i in items if i["name"] != name])
    return r.returncode


if __name__ == "__main__":
    a = sys.argv[1:]
    sub = "unpark" if os.environ.get("CM_SUBCOMMAND") == "unpark" else "park"
    if a and a[0] in ("park", "unpark"):
        sub, a = a[0], a[1:]
    sys.exit(cmd_unpark(a) if sub == "unpark" else cmd_park(a))
