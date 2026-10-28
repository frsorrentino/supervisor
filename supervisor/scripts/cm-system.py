#!/usr/bin/env python3
"""supervisor system — quanto pesa questa macchina e cosa si puo' fare (contratto 1.90, 28/10/2026).

Richiesta del maintainer dalla web app, 28/10 13:19: «un monitoraggio di sistema con consiglio su risoluzione problemi di
pesantezza su home». Alle 13:14 la master aveva misurato carico 13-15 su 8 core e trovato a mano un server
chrome-bridge --launch orfano (46% CPU, 939 MB) e una master rimasta senza il suo tmux.

MISURA, a ogni push del relay e a costo quasi zero: /proc/loadavg, /proc/meminfo (MemAvailable: la memoria che il
contenitore puo' ancora dare senza swap) e /proc/<pid>/stat di ogni processo. La CPU di un processo e' la differenza
dei suoi tick fra due misure dello stesso demone, in percento di un core; alla prima misura, la media dalla nascita
(quella di `ps`). Livello verde, giallo, rosso: il peggiore fra il carico per core (system.load_yellow/load_red) e la
memoria disponibile (system.mem_yellow_mb/mem_red_mb). Consumatori raggruppati: claude, chrome_bridge, relay, other.

CONSIGLI, ciascuno con un'azione per il pulsante dell'app o null (solo consiglio):
  orphan       un claude senza il suo tmux (cm-sessions.orphans)                        → sys_kill pid:procStart
  orphan_proc  un processo senza padre (figlio di init o systemd) che corrisponde a uno schema di
               system.kill_patterns, oltre l'eta' e la CPU dello schema                  → sys_kill pid:procStart
  stale        sessioni idle da sessions.idle_hours col contesto ≥ system.stale_context_pct → close (op esistente)
  too_many     sessions.max_sessions sessioni vive o piu', esclusa la master             → close delle due piu' ferme
               (idle da almeno un'ora)
  heavy_job    un lavoro pesante (system.heavy_patterns) sopra system.heavy_cpu          → null: «spostalo su win»
  low_memory   memoria disponibile sotto system.mem_red_mb                               → null
Mai la sessione corrente ne' il relay (`me`), mai la master ne' una sessione attaccata, mai un processo fuori dagli
schemi: l'op sys_kill rifa' la misura e chiude solo un pid:procStart che e' ancora fra i consigli.

Uso: cm-system.py [--json]
"""
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)

HZ = os.sysconf("SC_CLK_TCK")
PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024
ADVICE_MAX = 5
_PREV = {}   # (pid, procStart) → (tick, ora): la misura prima, nello stesso demone
CLAUDE = re.compile(r"(^|/)claude( |$)")
GROUPS = (("claude", CLAUDE), ("chrome_bridge", re.compile(r"chrome-bridge/server/index\.js")), ("relay", re.compile(r"cm-relay\.py")))


def conf():
    """system.* della config (default in cm-config.py): soglie, schemi ammessi per «Chiudi», lavori pesanti."""
    return CFG["system"]


def msg(key, **kw):
    return cm.msg(CFG, key, **kw)


def level_of(value, yellow, red, higher_is_worse=True):
    if higher_is_worse:
        return "red" if value >= red else "yellow" if value >= yellow else "green"
    return "red" if value < red else "yellow" if value < yellow else "green"


def measure(proc="/proc", cores=None):
    c = conf()
    cores = cores or os.cpu_count() or 1
    load1 = float(Path(proc, "loadavg").read_text().split()[0])
    mem = {}
    for line in Path(proc, "meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        if v.strip().split() and v.strip().split()[0].isdigit():
            mem[k] = int(v.split()[0])
    total, avail = round(mem.get("MemTotal", 0) / 1024), round(mem.get("MemAvailable", mem.get("MemFree", 0)) / 1024)
    lpc = round(load1 / cores, 2)
    ll = level_of(lpc, c["load_yellow"], c["load_red"])
    ml = level_of(avail, c["mem_yellow_mb"], c["mem_red_mb"], higher_is_worse=False)
    worst = "red" if "red" in (ll, ml) else "yellow" if "yellow" in (ll, ml) else "green"
    return {"cores": cores, "load1": load1, "load_per_core": lpc, "mem_total_mb": total, "mem_available_mb": avail,
            "level": worst, "load_level": ll, "mem_level": ml}


def proc_rows(proc="/proc"):
    """[{pid, ppid, start, age_s, cpu, rss_mb, args}] per ogni processo con una riga di comando (non i thread del kernel)."""
    uptime = float(Path(proc, "uptime").read_text().split()[0])
    now = time.time()
    rows, seen = [], {}
    for d in os.listdir(proc):
        if not d.isdigit():
            continue
        try:
            st = Path(proc, d, "stat").read_text()
            args = Path(proc, d, "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        f = st[st.rindex(")") + 2:].split()
        if not args or f[0] == "Z":
            continue
        pid, ticks, start = int(d), int(f[11]) + int(f[12]), f[19]
        age = max(uptime - int(start) / HZ, 1.0)
        prev = _PREV.get((pid, start))
        if prev and now - prev[1] >= 1:
            cpu = (ticks - prev[0]) / HZ / (now - prev[1]) * 100
        else:
            cpu = ticks / HZ / age * 100
        seen[(pid, start)] = prev if prev and now - prev[1] < 1 else (ticks, now)
        rows.append({"pid": pid, "ppid": int(f[1]), "start": start, "age_s": int(age), "cpu": round(max(cpu, 0.0), 1),
                     "rss_mb": round(int(f[21]) * PAGE_KB / 1024), "args": args})
    _PREV.clear()
    _PREV.update(seen)
    return rows


def group_of(args):
    return next((g for g, rx in GROUPS if rx.search(args)), "other")


def consumers(rows):
    out = {g: {"group": g, "count": 0, "cpu": 0.0, "rss_mb": 0} for g in ("claude", "chrome_bridge", "relay", "other")}
    for r in rows:
        c = out[group_of(r["args"])]
        c["count"] += 1
        c["cpu"] += r["cpu"]
        c["rss_mb"] += r["rss_mb"]
    for c in out.values():
        c["cpu"] = round(c["cpu"], 1)
    return sorted(out.values(), key=lambda c: (-c["cpu"], c["group"]))


def _short(args):
    """Il nome leggibile di un processo: lo script per un interprete, altrimenti il binario."""
    parts = args.split()
    base = os.path.basename(parts[0]) if parts else "?"
    if base in ("node", "python", "python3", "bash", "sh") and len(parts) > 1:
        script = next((p for p in parts[1:] if not p.startswith("-")), parts[1])
        return f"{base} {script.replace(os.path.expanduser('~'), '~')}"[:60]
    return base[:60]


def _gb(mb):
    return f"{mb / 1024:.1f}".replace(".", "," if (CFG.get("language") or "it") == "it" else ".")


def advise(m, rows, orphans=(), sessions=(), runtime=None, me=(), prefixes=()):
    """I consigli, deterministici, dalla misura `m`, dai processi, dagli orfani di cm-sessions e dalle righe di
    `sessions --json` (con il contesto di runtime per nome tmux). `me` = i pid da non toccare mai."""
    c = conf()
    by_pid = {r["pid"]: r for r in rows}
    me = set(me)
    out = []

    def add(kind, key, level, text, action=None):
        out.append({"id": f"{kind}:{key}", "kind": kind, "level": level, "text": text, "action": action})

    for o in orphans:
        if o["pid"] in me:
            continue
        add("orphan", f"{o['pid']}:{o['proc_start']}", "yellow", msg("system.advice_orphan", name=o["name"], pid=o["pid"]),
            {"op": "sys_kill", "arg": f"{o['pid']}:{o['proc_start']}", "label": msg("system.action_kill")})
    parents = set(c["orphan_parents"])
    claimed = {o["pid"] for o in orphans}
    for r in rows:
        if r["pid"] in me or r["pid"] in claimed or r["pid"] <= 1:
            continue
        par = by_pid.get(r["ppid"])
        parentless = r["ppid"] <= 1 or (par is not None and os.path.basename(par["args"].split()[0]) in parents)
        if not parentless:
            continue
        for pat in c["kill_patterns"]:
            if re.search(pat["match"], r["args"]) and r["age_s"] >= pat.get("min_age_s", 0) and r["cpu"] >= pat.get("min_cpu", 0):
                add("orphan_proc", f"{r['pid']}:{r['start']}", "red" if r["cpu"] >= 25 else "yellow",
                    msg("system.advice_orphan_proc", name=pat["name"], pid=r["pid"], cpu=round(r["cpu"]), mb=r["rss_mb"]),
                    {"op": "sys_kill", "arg": f"{r['pid']}:{r['start']}", "label": msg("system.action_kill")})
                break

    root = (CFG.get("workspace") or {}).get("root_session_name") or "master"
    sc = CFG.get("sessions") or {}
    runtime = runtime or {}
    short = lambda tm: next((tm[len(p):] for p in prefixes if p and tm.startswith(p)), tm)  # noqa: E731
    live = [s for s in sessions if s.get("status") in ("busy", "idle", "waiting") and not s.get("host") and (s.get("tmux") or s.get("name")) != root]
    free = [s for s in live if s.get("status") == "idle" and not s.get("waiting") and not s.get("attached")]
    stale = [s for s in free if (s.get("status_age_min") or 0) >= sc.get("idle_hours", 2) * 60
             and ((runtime.get(s.get("tmux") or s.get("name")) or {}).get("context") or 0) >= c["stale_context_pct"]]
    stale.sort(key=lambda s: -(s.get("status_age_min") or 0))
    if stale:
        names = [short(s.get("tmux") or s.get("name")) for s in stale]
        add("stale", ",".join(names), "yellow", msg("system.advice_stale", n=len(names), names=", ".join(names)),
            {"op": "close", "arg": ",".join(names), "label": msg("system.action_close_stale")})
    if len(live) >= sc.get("max_sessions", 5):
        taken = {s.get("tmux") for s in stale}
        # ferme da almeno un'ora: una idle da pochi minuti e' fra un turno e l'altro
        cand = sorted((s for s in free if s.get("tmux") not in taken and (s.get("status_age_min") or 0) >= 60),
                      key=lambda s: -(s.get("status_age_min") or 0))[:2]
        if cand:
            names = [short(s.get("tmux") or s.get("name")) for s in cand]
            add("too_many", ",".join(names), "yellow", msg("system.advice_too_many", n=len(live), names=", ".join(names)),
                {"op": "close", "arg": ",".join(names), "label": msg("system.action_close")})

    heavy = {}
    for r in rows:
        if r["pid"] in me or CLAUDE.search(r["args"]) or r["cpu"] < c["heavy_cpu"]:
            continue
        for p in c["heavy_patterns"]:
            if re.search(rf"(^|[ /]){p}( |$)", r["args"]):
                if p not in heavy or heavy[p]["cpu"] < r["cpu"]:
                    heavy[p] = r
                break
    for p, r in sorted(heavy.items(), key=lambda x: -x[1]["cpu"])[:2]:
        add("heavy_job", f"{r['pid']}:{r['start']}", "red" if m.get("level") == "red" else "yellow",
            msg("system.advice_heavy", name=_short(r["args"]), cpu=round(r["cpu"])))
    if m.get("mem_available_mb", 1 << 30) < c["mem_red_mb"]:
        top = [x for x in sorted(consumers(rows), key=lambda x: -x["rss_mb"]) if x["rss_mb"]][:2]
        add("low_memory", "mem", "red", msg("system.advice_low_memory", gb=_gb(m["mem_available_mb"]),
                                            top=", ".join(f"{x['group']} {_gb(x['rss_mb'])} GB" for x in top) or "-"))
    out.sort(key=lambda a: a["level"] != "red")
    return out[:ADVICE_MAX]


def me_pids():
    """Questo processo e i suoi antenati: la sessione da cui si guarda, il relay."""
    out, pid = set(), os.getpid()
    while pid and pid > 1 and len(out) < 64:
        out.add(pid)
        try:
            pid = int(next(x for x in Path(f"/proc/{pid}/status").read_text().splitlines() if x.startswith("PPid:")).split()[1])
        except (OSError, StopIteration, ValueError):
            break
    return out


def _orphans():
    try:
        spec = importlib.util.spec_from_file_location("cm_sessions_sys", HERE / "cm-sessions.py")
        s = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(s)
        return s.orphans()
    except Exception:   # noqa: BLE001 — un consiglio in meno, mai la misura persa
        return []


def status(rows=(), runtime=None, prefixes=(), proc="/proc"):
    """Il blocco `system` di /state (1.90)."""
    m = measure(proc)
    procs = proc_rows(proc)
    adv = advise(m, procs, orphans=_orphans(), sessions=rows, runtime=runtime, me=me_pids(), prefixes=prefixes)
    return {"at": int(time.time()), **m, "consumers": consumers(procs), "advice": adv}


if __name__ == "__main__":
    st = status()
    if "--json" in sys.argv:
        print(json.dumps(st, ensure_ascii=False, indent=1))
    else:
        print(f"{st['level']}  carico {st['load1']} su {st['cores']} core ({st['load_per_core']}/core)  memoria {st['mem_available_mb']} MB su {st['mem_total_mb']}")
        for c in st["consumers"]:
            print(f"  {c['group']:<14} {c['count']:>4}  {c['cpu']:>6}%  {c['rss_mb']:>6} MB")
        for a in st["advice"]:
            print(f"  [{a['level']}] {a['text']}" + (f"  → {a['action']['label']}" if a["action"] else ""))
