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

PROCESSI BLOCCATI SUL PONTE GRAFICO (1.91, 28/10, dal vivo alle 15:08): carico 49,85 su 8 core senza CPU in uso — 48
processi in stato D (sommelier e notificationd, wchan virtwl_ioctl_ptr e do_vfd_close), lasciati da tre unita' systemd
--user in ciclo di riavvio dopo il riavvio di ChromeOS delle 11:59, circa 20 l'ora da 3,3 MB l'uno. Un processo in D
conta nel carico ma non usa CPU, e non muore nemmeno con SIGKILL: si libera solo riavviando Linux. Si contano a parte
(`stuck`: numero, da quando, memoria, unita') e il carico che colora li toglie. Di un processo in D si leggono solo
stat, wchan e cgroup, mai la cmdline: leggerla puo' bloccare anche chi legge.

CONSIGLI, ciascuno con un'azione per il pulsante dell'app o null (solo consiglio):
  orphan       un claude senza il suo tmux (cm-sessions.orphans)                        → sys_kill pid:procStart
  orphan_proc  un processo senza padre (figlio di init o systemd) che corrisponde a uno schema di
               system.kill_patterns, oltre l'eta' e la CPU dello schema                  → sys_kill pid:procStart
  stale        sessioni idle da sessions.idle_hours col contesto ≥ system.stale_context_pct → close (op esistente)
  too_many     sessions.max_sessions sessioni vive o piu', esclusa la master             → close delle due piu' ferme
               (idle da almeno un'ora)
  heavy_job    un lavoro pesante (system.heavy_patterns) sopra system.heavy_cpu          → null: «spostalo su win»
  low_memory   memoria disponibile sotto system.mem_red_mb                               → null
  stuck_loop   unita' di system.stuck_units con processi bloccati, almeno system.stuck_min_restarts riavvii e
               ancora attive (il ciclo che li produce)                                   → sys_unit_stop «Ferma il ciclo»
  stuck        processi bloccati sul ponte grafico: quanti, da quando, quanta memoria; il riavvio di Linux solo come
               testo, mai automatico (chiude tutte le sessioni)                         → null
Mai la sessione corrente ne' il relay (`me`), mai la master ne' una sessione attaccata, mai un processo fuori dagli
schemi: l'op sys_kill rifa' la misura e chiude solo un pid:procStart che e' ancora fra i consigli; sys_unit_stop ferma
solo unita' ancora consigliate e dell'elenco ammesso (`systemctl --user stop --no-block`).

Uso: cm-system.py [--json]
"""
import fnmatch
import importlib.util
import json
import os
import re
import subprocess
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


def measure(proc="/proc", cores=None, stuck=None):
    """Carico, memoria e livello. `stuck` (stuck_of): i processi bloccati sul ponte grafico non colorano il carico."""
    c = conf()
    cores = cores or os.cpu_count() or 1
    load1 = float(Path(proc, "loadavg").read_text().split()[0])
    mem = {}
    for line in Path(proc, "meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        if v.strip().split() and v.strip().split()[0].isdigit():
            mem[k] = int(v.split()[0])
    total, avail = round(mem.get("MemTotal", 0) / 1024), round(mem.get("MemAvailable", mem.get("MemFree", 0)) / 1024)
    lpc = round(max(load1 - (stuck or {}).get("count", 0), 0) / cores, 2)
    ll = level_of(lpc, c["load_yellow"], c["load_red"])
    ml = level_of(avail, c["mem_yellow_mb"], c["mem_red_mb"], higher_is_worse=False)
    worst = "red" if "red" in (ll, ml) else "yellow" if "yellow" in (ll, ml) else "green"
    return {"cores": cores, "load1": load1, "load_per_core": lpc, "mem_total_mb": total, "mem_available_mb": avail,
            "level": worst, "load_level": ll, "mem_level": ml, "stuck": stuck}


def proc_rows(proc="/proc"):
    """[{pid, ppid, start, age_s, cpu, rss_mb, rss_kb, args, state}] per ogni processo con una riga di comando (non i
    thread del kernel). Un processo in D porta anche wchan, unit (dal cgroup) e started_at, e per args il suo comm: la
    cmdline di un processo in D non si legge (1.91)."""
    uptime = float(Path(proc, "uptime").read_text().split()[0])
    now = time.time()
    rows, seen = [], {}
    for d in os.listdir(proc):
        if not d.isdigit():
            continue
        extra = {}
        try:
            st = Path(proc, d, "stat").read_text()
            f = st[st.rindex(")") + 2:].split()
            if f[0] == "D":
                extra = {"wchan": Path(proc, d, "wchan").read_text().strip(), "unit": _unit_of(Path(proc, d, "cgroup").read_text())}
                args = st[st.index("(") + 1:st.rindex(")")]
            else:
                args = Path(proc, d, "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        except (OSError, ValueError):
            continue
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
        if extra:
            extra["started_at"] = int(now - age)
        rows.append({"pid": pid, "ppid": int(f[1]), "start": start, "age_s": int(age), "cpu": round(max(cpu, 0.0), 1),
                     "rss_mb": round(int(f[21]) * PAGE_KB / 1024), "rss_kb": int(f[21]) * PAGE_KB, "args": args, "state": f[0], **extra})
    _PREV.clear()
    _PREV.update(seen)
    return rows


def _unit_of(cgroup):
    """L'unita' systemd di un processo dal suo /proc/<pid>/cgroup (l'ultimo pezzo che finisce in .service), o ""."""
    for line in cgroup.splitlines():
        for part in reversed(line.rpartition(":")[2].split("/")):
            if part.endswith(".service"):
                return part
    return ""


def stuck_of(rows, proc=None):
    """I processi in D fermi sul ponte grafico (wchan in system.stuck_wchan): {count, since, rss_mb, units} o None."""
    wch = set(conf()["stuck_wchan"])
    st = [r for r in rows if r.get("state") == "D" and r.get("wchan") in wch]
    if not st:
        return None
    return {"count": len(st), "since": min(r["started_at"] for r in st), "rss_mb": round(sum(r["rss_kb"] for r in st) / 1024),
            "units": sorted({r["unit"] for r in st if r.get("unit")})}


def _allowed_unit(u):
    return bool(re.fullmatch(r"[A-Za-z0-9@._-]+\.service", u)) and any(fnmatch.fnmatch(u, p) for p in conf()["stuck_units"])


def _systemctl():
    return os.environ.get("CM_SYSTEMCTL", "systemctl")


def unit_info(names):
    """{unit: {restarts, state, stopping}} da `systemctl --user show` (una chiamata per tutte) e `list-jobs`; {} se
    systemctl non risponde. stopping = uno stop gia' in coda: un'unita' ferma in «deactivating» perche' i suoi processi
    sono in D (dal vivo alle 15:13, dopo lo stop della master) non e' piu' un ciclo da fermare."""
    names = [u for u in names if re.fullmatch(r"[A-Za-z0-9@._-]+\.service", u)]
    if not names:
        return {}
    try:
        r = subprocess.run([_systemctl(), "--user", "show", "-p", "Id", "-p", "NRestarts", "-p", "ActiveState", *names],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {}
    out = {}
    for block in r.stdout.strip().split("\n\n"):
        kv = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if kv.get("Id"):
            out[kv["Id"]] = {"restarts": int(kv.get("NRestarts") or 0) if (kv.get("NRestarts") or "0").isdigit() else 0, "state": kv.get("ActiveState") or "",
                             "stopping": False}
    try:
        jobs = subprocess.run([_systemctl(), "--user", "list-jobs", "--no-legend"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        jobs = ""
    for line in jobs.splitlines():
        f = line.split()
        if len(f) >= 3 and f[1] in out and f[2] == "stop":
            out[f[1]]["stopping"] = True
    return out


def stop_units(names):
    """`systemctl --user stop --no-block` per ogni unita' dell'elenco ammesso; una sola fuori elenco → False, niente."""
    if not names or not all(_allowed_unit(u) for u in names):
        return False
    ok = True
    for u in names:
        try:
            ok = subprocess.run([_systemctl(), "--user", "stop", "--no-block", u], capture_output=True, timeout=10).returncode == 0 and ok
        except (OSError, subprocess.SubprocessError):
            ok = False
    return ok


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


def advise(m, rows, orphans=(), sessions=(), runtime=None, me=(), prefixes=(), units=None):
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
    stuck = m.get("stuck")
    if stuck:
        units = units or {}
        loop = [u for u in stuck["units"] if _allowed_unit(u) and (units.get(u) or {}).get("restarts", 0) >= c["stuck_min_restarts"]
                and (units.get(u) or {}).get("state") in ("active", "activating", "deactivating", "reloading") and not units[u].get("stopping")]
        if loop:
            add("stuck_loop", ",".join(loop), "red", msg("system.advice_stuck_loop", n=len(loop), restarts=max(units[u]["restarts"] for u in loop)),
                {"op": "sys_unit_stop", "arg": ",".join(loop), "label": msg("system.action_stop_loop")})
        when = time.strftime("%H:%M" if time.strftime("%Y%m%d", time.localtime(stuck["since"])) == time.strftime("%Y%m%d") else "%d/%m %H:%M",
                             time.localtime(stuck["since"]))
        add("stuck", "graphic", "yellow", msg("system.advice_stuck", n=stuck["count"], since=when, mb=stuck["rss_mb"]))
    # i rossi prima; a pari livello il ciclo da fermare prima di tutto (e' la causa dei bloccati)
    out.sort(key=lambda a: (a["level"] != "red", a["kind"] != "stuck_loop"))
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
    procs = proc_rows(proc)
    stuck = stuck_of(procs)
    m = measure(proc, stuck=stuck)
    adv = advise(m, procs, orphans=_orphans(), sessions=rows, runtime=runtime, me=me_pids(), prefixes=prefixes,
                 units=unit_info(stuck["units"]) if stuck else {})
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
