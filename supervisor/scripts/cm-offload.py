#!/usr/bin/env python3
"""supervisor offload / heavy — lavori pesanti sull'host che li fa meglio (piano multi-PC 3.1-3.4, v1).

  supervisor offload <cartella> [--recipe NOME [--recipes FILE] | --needs k=v,... [--secrets] [--files GLOB] [--out PATH]
                        [--est S] -- <comando>] [--host H] [--explain] [--dirty] [--no-fetch] [--dest DIR] [--notify SESSIONE]
  supervisor offload list | status <id> | log <id> [-f] | wait <id> [--max-min N] | fetch <id> | cancel <id> | clean [<id> | --older 7d]
  supervisor offload tick                  chiude i lavori finiti e avvia quelli in coda (lo chiama `hosts poll`)
  supervisor heavy run [--needs ...] [--recipe NOME] [--host H] [--explain] -- <comando>
  supervisor heavy status

Un lavoro dichiara di cosa ha bisogno (ricetta in <cartella>/.cm-offload.json o flag); lo scheduler filtra gli host
per fiducia e requisiti, toglie quelli senza posto e sceglie: un lavoro leggero resta in locale finche' la regia ha
posto; un lavoro pesante va altrove quando il tempo guadagnato supera il costo della copia (decisione dell'utente,
27/09/2026 20:32). Tutte le prenotazioni stanno in <state_dir>/heavy/leases.json (flock), con i limiti per host.
Ogni decisione e' una riga di <state_dir>/scheduler.log: stima, e a lavoro finito la durata vera.
"""
import datetime as dt
import fcntl
import fnmatch
import glob
import hashlib
import importlib.util
import json
import os
import re
import secrets as _secrets
import shlex
import shutil
import statistics
import subprocess
import sys
import tarfile
import time
from contextlib import contextmanager
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
ad = _load("cm-adapters")
hm = _load("cm-hosts")
CFG = hm.CFG
S = CFG["scheduler"]
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
STATE = Path(cm.expand(CFG["state_dir"]))
ACTIVE = ("queued", "syncing", "starting", "deps", "running")
# token noti e chiavi private: un invio senza segreti si blocca se il sorgente ne contiene uno (3.2.5)
SECRET_RE = (r"-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|"
             r"sk-ant-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|sk_live_[A-Za-z0-9]{20,}|"
             r"glpat-[A-Za-z0-9_-]{20}|npm_[A-Za-z0-9]{36}")
LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "requirements.txt", "poetry.lock")


class Refused(Exception):
    pass


def now():
    return time.time()


def iso(t=None):
    return dt.datetime.fromtimestamp(t or now()).strftime("%Y-%m-%dT%H:%M:%S")


def jobs_dir():
    return STATE / "offload"


def job_path(jid):
    if not re.match(r"^[A-Za-z0-9_-]+$", jid or ""):
        raise SystemExit(M("offload.bad_id", id=jid))
    return jobs_dir() / jid / "job.json"


def load_job(jid):
    j = hm.read_json(job_path(jid))
    if not j:
        raise SystemExit(M("offload.unknown", id=jid))
    return j


def save_job(j):
    hm.write_json(job_path(j["id"]), j)


def all_jobs():
    out = []
    for p in sorted(jobs_dir().glob("*/job.json")):
        j = hm.read_json(p)
        if j:
            out.append(j)
    return out


def sched_log(row):
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "scheduler.log", "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_sched_log():
    p = STATE / "scheduler.log"
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


# ------------------------------------------------------------------ prenotazioni (1.4, 3.4)
@contextmanager
def leases():
    d = STATE / "heavy"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "leases.lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        data = hm.read_json(d / "leases.json", {"leases": []}) or {"leases": []}
        yield data
        hm.write_json(d / "leases.json", data)


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def reclaim(data):
    """Prenotazioni stantie: il pid locale e' morto (lavori in locale, heavy run), o il lavoro remoto e' finito o
    orfano (heartbeat piu' vecchio di 3 minuti con l'host irraggiungibile). Il lavoro orfano non si abbatte."""
    keep = []
    for l in data["leases"]:
        if l.get("pid") and not pid_alive(l["pid"]):
            continue
        if l.get("job"):
            j = hm.read_json(job_path(l["job"])) or {}
            if j.get("state") not in ACTIVE:
                continue
            if j.get("state") != "queued":
                snap = hm.read_json(hm.snap_path(l["host"]), {}) or {}
                hb = (j.get("remote_status") or {}).get("heartbeat")
                if snap.get("error") and hb and age_iso(hb) > 180:
                    j["state"] = "orphan"
                    save_job(j)
                    continue
        keep.append(l)
    data["leases"] = keep


def age_iso(s):
    try:
        t = dt.datetime.strptime(s.rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
        return (dt.datetime.utcnow() - t).total_seconds()
    except ValueError:
        return 0


def used(data, host):
    ls = [l for l in data["leases"] if l["host"] == host and not l.get("queued")]
    return {"heavy": sum(1 for l in ls if l["cls"] in ("heavy", "render")), "render": sum(1 for l in ls if l["cls"] == "render")}


# ------------------------------------------------------------------ ricette e requisiti (3.1)
def parse_needs(s):
    """--needs gpu=webgl-hardware,ram=4,threads=4,role=render,tools=ffmpeg+node>=20,os=windows+linux"""
    n = {}
    for part in (s or "").split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = {"ram": "ram_gb"}.get(k.strip(), k.strip())
        if k in ("threads", "ram_gb"):
            n[k] = float(v)
        elif k == "tools":
            n["tools"] = {}
            for t in v.split("+"):
                m = re.match(r"^([a-z0-9_-]+)(.*)$", t.strip())
                if m:
                    n["tools"][m.group(1)] = m.group(2) or "*"
        elif k == "os":
            n["os"] = v.split("+")
        else:
            n[k] = v
    return n


def load_recipe(folder, name, path=None):
    f = Path(cm.expand(path)) if path else Path(folder) / ".cm-offload.json"
    data = hm.read_json(f)
    if not data or name not in (data.get("recipes") or {}):
        raise Refused(M("offload.no_recipe", name=name, file=str(f)))
    r = dict(data["recipes"][name])
    r["name"] = name
    return r


def job_class(needs):
    role = (needs or {}).get("role")
    return "render" if role == "render" else ("heavy" if role == "compute" else "light")


def os_family(kind):
    return {"windows-native": "windows", "macos-tmux": "macos"}.get(kind, "linux")


def low_priority(cmd):
    """08/10 (piano prestazioni, fase 1): il lavoro pesante che resta sulla regia gira a priorita' bassa, CPU e disco,
    cosi' il controllo (relay, hook) non aspetta dietro a build e test. Il relay non puo' alzarsi la priorita'
    (`ulimit -e` = 0): si abbassa il resto."""
    pre = (["nice", "-n", "10"] if shutil.which("nice") else []) + (["ionice", "-c3"] if shutil.which("ionice") else [])
    return " ".join(pre + ["sh", "-c", shlex.quote(cmd)]) if pre and cmd else cmd


def cmd_for(recipe, kind):
    c = recipe.get("cmd")
    if isinstance(c, str):
        return c
    return (c or {}).get(kind) or (c or {}).get("default")


def ver_ok(have, want):
    if not have:
        return False
    if want in ("*", "", None):
        return True
    m = re.match(r"^(>=|>|==|=)?\s*([0-9.]+)$", str(want))
    if not m:
        return True
    hv = [int(x) for x in re.findall(r"\d+", str(have))[:3]]
    wv = [int(x) for x in m.group(2).split(".")]
    hv, wv = hv + [0] * (3 - len(hv)), wv + [0] * (3 - len(wv))
    return {"": hv >= wv, ">=": hv >= wv, ">": hv > wv, "==": hv == wv, "=": hv == wv}[m.group(1) or ""]


def is_client_path(folder):
    rp = os.path.realpath(folder)
    for p in S.get("client_paths") or []:
        q = os.path.realpath(cm.expand(p))
        if rp == q or rp.startswith(q + "/"):
            return True
    acc, deduced = cm.account_for(CFG, folder)
    return deduced and (CFG["accounts"].get(acc) or {}).get("kind") == "agency"


def host_roles(name, h):
    if name == "local":
        m = h.get("measured") or {}
        return set(hm.derive_roles(m, m, h.get("kind")) + (["compute"] if m else []))
    return set(h.get("roles") or [])


def host_limits(name, h):
    if h.get("limits"):
        return h["limits"]
    m = h.get("measured") or {}
    if not m:
        return {"heavy": 1, "render": 0, "sessions": 1}
    return hm.derive_limits(m, name)[0]


def trust_rule(name, h, job):
    """La regola di fiducia che esclude l'host, o ''. Vale anche con --host (1.5)."""
    if name == "local":
        return ""
    t = h.get("trust") or {"level": "work", "secrets": False, "clients": False, "accounts": []}
    if h.get("kind") == "cloud" or (h.get("transport") or {}).get("cloud"):
        return M("sched.rule_cloud", host=name)
    if not hm.confirmed(h, name):
        return M("sched.rule_unconfirmed", host=name)
    if job.get("secrets") and not t.get("secrets"):
        return M("sched.rule_secrets", host=name)
    if job.get("client") and not t.get("clients"):
        return M("sched.rule_clients", host=name)
    if job.get("account") and job["account"] not in (t.get("accounts") or []):
        return M("sched.rule_account", host=name, account=job["account"])
    return ""


def needs_rule(name, h, job):
    """Il requisito rigido che l'host non soddisfa, o ''. Anche questi valgono con --host."""
    m = h.get("measured") or {}
    n = job.get("needs") or {}
    kind = h.get("kind") or "linux-tmux"
    if not m:
        return M("sched.rule_unmeasured", host=name)
    if not cmd_for(job, kind):
        return M("sched.rule_no_cmd", host=name, kind=kind)
    if n.get("os") and os_family(kind) not in n["os"]:
        return M("sched.rule_os", host=name, os=os_family(kind))
    if n.get("role") and n["role"] not in host_roles(name, h):
        return M("sched.rule_role", host=name, role=n["role"])
    if n.get("threads") and (m.get("threads") or 0) < float(n["threads"]):
        return M("sched.rule_threads", host=name, have=m.get("threads"), want=n["threads"])
    if n.get("ram_gb") and (m.get("ram_gb") or 0) < float(n["ram_gb"]):
        return M("sched.rule_ram", host=name, have=m.get("ram_gb"), want=n["ram_gb"])
    if n.get("gpu") == "webgl-hardware" and not (m.get("webgl") or {}).get("hardware"):
        return M("sched.rule_gpu", host=name)
    for tool, want in (n.get("tools") or {}).items():
        if not ver_ok((m.get("tools") or {}).get(tool), want):
            return M("sched.rule_tool", host=name, tool=tool, want=want, have=(m.get("tools") or {}).get(tool) or "-")
    return ""


# ------------------------------------------------------------------ stato attuale degli host
def local_status():
    """Il carico della regia letto adesso, non dallo snapshot: e' la macchina su cui si decide."""
    if os.environ.get("CM_FAKE_LOCAL_STATUS"):   # solo prove
        return json.loads(os.environ["CM_FAKE_LOCAL_STATUS"])
    P = _cm_proc()   # 11/11: Linux e macOS
    la, avail = P.loadavg(), P.memory()[1]
    threads = os.cpu_count() or 1
    if la is None or avail is None:
        return {"load_pct": 0, "ram_free_gb": 99, "threads": threads}
    return {"load_pct": int(la / threads * 100), "ram_free_gb": round(avail / 1024, 1), "threads": threads}


def fresh_status(name, h):
    if name == "local":
        return local_status(), 0
    snap = hm.read_json(hm.snap_path(name), {}) or {}
    if not snap or snap.get("error") or hm.age_s(snap) > int(S["fresh_s"]):
        try:
            snap = hm.poll_one(name, force=True) or snap
        except Exception:
            pass
    if not snap or snap.get("error") or not snap.get("status"):
        return None, hm.age_s(snap) if snap else None
    return snap["status"], hm.age_s(snap)


def gate_closed(name, h, st):
    L = S["limits"]
    reserve = float(L["reserve_gb"])
    if name == "local":
        reserve += int(CFG["sessions"]["max_sessions"]) * float(L["session_reserve_gb"])
        reserve = min(reserve, 1.5)   # la regia vive sotto la riserva teorica: basta lasciare le sessioni respirare
    if (st.get("load_pct") or 0) > int(S["load_gate_pct"]):
        return M("sched.gate_load", load=st.get("load_pct"), max=S["load_gate_pct"])
    if st.get("ram_free_gb") is not None and st["ram_free_gb"] < reserve:
        return M("sched.gate_ram", free=st["ram_free_gb"], reserve=round(reserve, 1))
    return ""


# ------------------------------------------------------------------ stima e costo (3.3)
def rel(name, h, cls, local_m):
    m = h.get("measured") or {}
    if name == "local":
        return 1.0
    if cls == "render":
        r = hm.rel_bench(m, local_m, "webgl")
        if r:
            return r
    return hm.rel_bench(m, local_m, "multi")


def estimate_local_s(job, local_m):
    """(secondi, fonte): mediana delle esecuzioni della stessa ricetta riportate alla regia, poi est_local_s."""
    key = job.get("recipe_key")
    runs = []
    for r in read_sched_log():
        if r.get("event") == "done" and r.get("recipe_key") == key and key and r.get("rc") == 0 and r.get("seconds"):
            runs.append(r["seconds"] * (r.get("rel") or 1.0))
    if runs:
        return statistics.median(runs[-7:]), M("sched.src_history", n=len(runs[-7:]))
    if job.get("est_local_s"):
        return float(job["est_local_s"]), M("sched.src_recipe")
    return None, M("sched.src_unknown")


def sent_assets(name):
    p = STATE / "hosts" / f"{name}.assets"
    return set(p.read_text().split()) if p.exists() else set()


def remember_assets(name, shas):
    p = STATE / "hosts" / f"{name}.assets"
    p.parent.mkdir(parents=True, exist_ok=True)
    have = sent_assets(name) | set(shas)
    p.write_text("\n".join(sorted(have)) + "\n")


def deps_cached(name, key):
    p = STATE / "hosts" / f"{name}.deps"
    return p.exists() and key in p.read_text().split()


def remember_deps(name, key):
    p = STATE / "hosts" / f"{name}.deps"
    p.parent.mkdir(parents=True, exist_ok=True)
    have = set(p.read_text().split()) if p.exists() else set()
    p.write_text("\n".join(sorted(have | {key})) + "\n")


def copy_cost(name, h, job):
    """(secondi, byte) della copia verso H: archivio del commit + asset che H non ha + ritorno, alla banda misurata,
    piu' la preparazione fissa e l'installazione delle dipendenze se la loro cache manca su H."""
    if name == "local":
        return 0.0, 0
    have = sent_assets(name)
    b = int(job.get("src_bytes") or 0) + sum(a["size"] for a in job.get("assets") or [] if a["sha"] not in have)
    b += int(job.get("out_bytes") or 0)
    bw = float((h.get("measured") or {}).get("bandwidth_mb_s") or 0) or 5.0
    c = b / (bw * 1024 * 1024) + float(S["setup_s"])
    if job.get("deps") and not deps_cached(name, job["deps"]["key"]):
        c += float(job.get("deps_s") or 120)
    return round(c, 1), b


def schedule(job, force_host=None):
    """La decisione: (host scelto o None, stato 'run'|'queue'|'refuse', motivo, tabella per --explain)."""
    H = hm.hosts()
    local_m = H["local"].get("measured") or {}
    cls = job_class(job.get("needs"))
    job["cls"] = cls
    rows, eligible = [], []
    est, src = estimate_local_s(job, local_m)
    with leases() as data:
        reclaim(data)
        for name, h in H.items():
            row = {"host": name, "rule": "", "slot": "", "load": None, "rel": None, "est": est, "src": src,
                   "gain": None, "bytes": None, "bw": (h.get("measured") or {}).get("bandwidth_mb_s"), "cost": None, "margin": None}
            rows.append(row)
            if force_host and name != force_host:
                row["rule"] = M("sched.rule_forced_other", host=force_host)
                continue
            row["rule"] = trust_rule(name, h, job) or needs_rule(name, h, job)
            if row["rule"]:
                continue
            st, age = fresh_status(name, h)
            if st is None:
                row["rule"] = M("sched.rule_stale", host=name)
                continue
            if name != "local":
                try:
                    ok, _ = ad.adapter_for(name, h, CFG).helper_ok() if job.get("_check_helper") else (True, None)
                except ad.HostError:
                    ok = False
                if not ok:
                    row["rule"] = M("sched.rule_helper", host=name)
                    continue
            row["load"] = st.get("load_pct")
            lim = host_limits(name, h)
            u = used(data, name)
            full = (cls in ("heavy", "render") and u["heavy"] >= int(lim.get("heavy", 1))) or \
                   (cls == "render" and u["render"] >= int(lim.get("render", 0)))
            gate = gate_closed(name, h, st) if cls != "light" or name == "local" else ""
            row["slot"] = M("sched.slot_full", used=u["heavy"], lim=lim.get("heavy")) if full else (gate or M("sched.slot_ok"))
            r = rel(name, h, cls, local_m)
            ls = local_status()
            lf = min(0.95, max(0.0, (ls["load_pct"] or 0) / 100))
            hf = min(0.95, max(0.0, (st.get("load_pct") or 0) / 100))
            r_eff = (r or 1.0) * (1 - hf) / (1 - lf) if name != "local" else 1.0
            row["rel"] = round(r_eff, 2)
            cost, nbytes = copy_cost(name, h, job)
            row["cost"], row["bytes"] = cost, nbytes
            if est is not None and name != "local":
                row["gain"] = round(est * (1 - 1 / r_eff), 1) if r_eff > 0 else None
                row["margin"] = round(row["gain"] - cost, 1) if row["gain"] is not None else None
            eligible.append((name, h, row, bool(full or gate)))
        if not eligible:
            return None, "refuse", M("sched.none_eligible"), rows
        free = [e for e in eligible if not e[3]]
        local_free = next((e for e in free if e[0] == "local"), None)
        choice, why = None, ""
        if force_host:
            e = eligible[0]
            if e[3]:
                return e[0], "queue", M("sched.why_forced_queue", host=e[0]), rows
            choice, why = e[0], M("sched.why_forced", host=e[0])
        elif cls == "light":
            if local_free:
                choice, why = "local", M("sched.why_light_local")
            elif free:
                best = max(free, key=lambda e: e[2]["rel"] or 0)
                choice, why = best[0], M("sched.why_local_full", host=best[0])
        else:
            remote = [e for e in free if e[0] != "local"]
            if local_free:
                if est is None:
                    choice, why = "local", M("sched.why_unknown_duration")
                else:
                    win = [e for e in remote if e[2]["margin"] is not None and e[2]["margin"] > 0]
                    if win:
                        best = max(win, key=lambda e: e[2]["margin"])
                        choice, why = best[0], M("sched.why_gain", host=best[0], gain=best[2]["gain"], cost=best[2]["cost"])
                    else:
                        choice, why = "local", M("sched.why_cost")
            elif remote:
                # regia esclusa (requisito) o satura: vince il punteggio piu' alto, anche se il guadagno non copre la copia
                best = max(remote, key=lambda e: (e[2]["margin"] if e[2]["margin"] is not None else -1e9, e[2]["rel"] or 0))
                excluded = not any(e[0] == "local" for e in eligible)
                choice, why = best[0], M("sched.why_only_remote" if excluded else "sched.why_local_full", host=best[0])
        if choice is None:
            order = sorted(eligible, key=lambda e: -(e[2]["rel"] or 0))
            return order[0][0], "queue", M("sched.why_queue", host=order[0][0]), rows
        for r in rows:
            r["chosen"] = r["host"] == choice
        return choice, "run", why, rows


def explain_table(rows):
    hdr = ["host", M("sched.col_filter"), M("sched.col_slot"), M("sched.col_load"), "rel", M("sched.col_est"),
           M("sched.col_gain"), M("sched.col_bytes"), M("sched.col_bw"), M("sched.col_cost"), M("sched.col_margin"), ""]
    t = [hdr]
    for r in rows:
        t.append([r["host"], r["rule"] or "ok", r["slot"] or "-", "-" if r["load"] is None else f"{r['load']}%",
                  "-" if r["rel"] is None else r["rel"],
                  "-" if r["est"] is None else f"{int(r['est'])}s ({r['src']})",
                  "-" if r["gain"] is None else f"{r['gain']}s", "-" if r["bytes"] is None else f"{r['bytes'] / 1048576:.1f}MB",
                  "-" if not r["bw"] else f"{r['bw']}MB/s", "-" if r["cost"] is None else f"{r['cost']}s",
                  "-" if r["margin"] is None else f"{r['margin']}s", "←" if r.get("chosen") else ""])
    w = [max(len(str(x[i])) for x in t) for i in range(len(hdr))]
    return "\n".join("  ".join(str(c).ljust(w[i]) for i, c in enumerate(x)).rstrip() for x in t)


# ------------------------------------------------------------------ preparazione del lavoro (3.2)
def git(folder, *args, check=True):
    p = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise Refused(M("offload.git_error", cmd=" ".join(args[:2]), err=p.stderr.strip()[:200]))
    return p.stdout


def secret_pathspecs(globs):
    out = []
    for g in globs:
        out += [f":(exclude,glob){g}", f":(exclude,glob)**/{g}"]
    return out


def is_secret_name(rel_path, globs):
    base = os.path.basename(rel_path)
    return any(fnmatch.fnmatch(base, g) for g in globs)


def prepare(job):
    """Commit esatto, dimensione del sorgente, manifesto degli asset, controlli per il tipo di destinazione ancora da
    fare (li fa check_target quando l'host e' scelto). Solleva Refused con il nome del file."""
    folder = job["dir"]
    if git(folder, "rev-parse", "--is-inside-work-tree", check=False).strip() != "true":
        raise Refused(M("offload.not_git", dir=folder))
    top = git(folder, "rev-parse", "--show-toplevel").strip()
    job["git_top"] = top
    job["sub"] = os.path.relpath(os.path.realpath(folder), os.path.realpath(top))
    dirty = git(folder, "status", "--porcelain", "--untracked-files=no").strip()
    if dirty and not job.get("dirty"):
        raise Refused(M("offload.dirty", files=", ".join(l[3:] for l in dirty.splitlines()[:5])))
    commit = (git(folder, "stash", "create").strip() if dirty else "") or git(folder, "rev-parse", "HEAD").strip()
    job["commit"] = commit
    globs = S["secret_globs"]
    specs = ["."] + secret_pathspecs(globs)
    # -e: il pattern comincia con «-----BEGIN» e senza -e git lo prenderebbe per un'opzione
    scan = subprocess.run(["git", "-C", top, "grep", "-I", "-l", "-E", "-e", SECRET_RE, commit, "--"] + specs,
                          capture_output=True, text=True, cwd=folder)
    hits = [l.split(":", 1)[1] if ":" in l else l for l in scan.stdout.splitlines()]
    if hits:
        raise Refused(M("offload.secret_found", files=", ".join(hits[:5])))
    tree = git(top, "ls-tree", "-r", "-l", "--full-tree", commit, "--", job["sub"] if job["sub"] != "." else ".")
    size, files, links = 0, [], []
    for line in tree.splitlines():
        meta, path = line.split("\t", 1)
        mode, typ, _, sz = meta.split()
        if is_secret_name(path, globs):
            continue
        rel_p = os.path.relpath(path, job["sub"]) if job["sub"] != "." else path
        files.append(rel_p)
        if mode == "120000":
            links.append(rel_p)
        if sz.isdigit():
            size += int(sz)
    job["src_bytes"], job["src_files"], job["symlinks"] = size, files, links
    assets = []
    ignore = set()
    for pat in (job.get("files") or {}).get("assets") or []:
        for p in sorted(glob.glob(os.path.join(folder, pat), recursive=True)):
            if not os.path.isfile(p):
                continue
            rp = os.path.relpath(p, folder)
            if rp in ignore or is_secret_name(rp, globs):
                continue
            ignore.add(rp)
            assets.append({"path": rp, "size": os.path.getsize(p), "sha": ad.sha256_file(p)})
    job["assets"] = assets
    deps = (job.get("files") or {}).get("deps")
    if deps:
        h = hashlib.sha256(deps.encode())
        for lf in LOCKFILES + ("package.json",):
            f = Path(folder) / lf
            if f.exists():
                h.update(lf.encode() + b"\0" + f.read_bytes())
        job["deps"] = {"cmd": deps, "dir": (job.get("files") or {}).get("deps_dir") or "node_modules", "key": h.hexdigest()[:24]}
    return job


def check_target(job, name, h, remote_root):
    """3.2.6: percorsi incompatibili con il tipo di destinazione. Il primo problema rifiuta il lavoro col nome del file."""
    kind = h.get("kind") or "linux-tmux"
    if kind in ("windows-native", "macos-tmux"):
        seen = {}
        for p in job["src_files"] + [a["path"] for a in job["assets"]]:
            k = p.lower()
            if k in seen and seen[k] != p:
                raise Refused(M("offload.case_clash", a=seen[k], b=p, host=name))
            seen[k] = p
    if kind == "windows-native":
        if job["symlinks"]:
            raise Refused(M("offload.symlink", file=job["symlinks"][0], host=name))
        base = len(str(remote_root)) + len(f"\\jobs\\{job['id']}\\src\\") + 12
        for p in job["src_files"] + [a["path"] for a in job["assets"]]:
            if base + len(p) > 259:
                raise Refused(M("offload.path_long", file=p, n=base + len(p), host=name))


def build_bundle_fn(job, name, h, adapter):
    work = jobs_dir() / job["id"]

    def build(missing):
        top, sub = job["git_top"], job["sub"]
        treeish = job["commit"] + (f":{sub}" if sub != "." else "")
        with open(work / "src.tar", "wb") as f:
            subprocess.run(["git", "-C", top, "archive", "--format=tar", treeish, "--", "."] + secret_pathspecs(S["secret_globs"]),
                           stdout=f, check=True)
        if job.get("secrets") and (h.get("trust") or {}).get("secrets") or (job.get("secrets") and name == "local"):
            # solo i segreti elencati, e solo verso un host che li ammette (3.2.5)
            with tarfile.open(work / "src.tar", "a") as t:
                for rel_p in (job.get("needs") or {}).get("secrets_from") or []:
                    t.add(os.path.join(job["dir"], rel_p), rel_p)
        with tarfile.open(work / "assets.tar", "w") as t:
            by_sha = {a["sha"]: a for a in job["assets"]}
            for s in missing:
                if s in by_sha:
                    t.add(os.path.join(job["dir"], by_sha[s]["path"]), s)
        kind = h.get("kind") or "linux-tmux"
        threads = int((h.get("measured") or {}).get("threads") or os.cpu_count() or 1)
        cmd = cmd_for(job, kind).replace("{threads}", str(threads))
        (work / "cmd.txt").write_text(cmd + "\n")
        (work / "env.txt").write_text("".join(f"{k}={v}\n" for k, v in (job.get("env") or {}).items()))
        (work / "timeout.txt").write_text(f"{int(job.get('timeout_s') or 0)}\n")
        names = ["src.tar", "assets.tar", "cmd.txt", "env.txt", "timeout.txt"]
        if job.get("deps"):
            d = job["deps"]
            (work / "deps.txt").write_text(f"key={d['key']}\ndir={d['dir']}\ncmd={d['cmd']}\n")
            names.append("deps.txt")
        with tarfile.open(work / "bundle.tar", "w") as t:
            for n in names:
                t.add(work / n, n)
        return work / "bundle.tar"
    return build


def write_manifest(job):
    p = jobs_dir() / job["id"] / "manifest.txt"
    p.write_text("".join(f"{a['sha']}\t{a['size']}\t{a['path']}\n" for a in job["assets"]))
    return str(p) if job["assets"] else None


# ------------------------------------------------------------------ invio, stato, ritorno
def start(job, name):
    h = hm.hosts()[name]
    a = ad.adapter_for(name, h, CFG)
    if a.remote:
        ok, _ = a.helper_ok()
        if not ok:
            raise Refused(M("sched.rule_helper", host=name))
    check_target(job, name, h, a.root)
    job.update({"host": name, "state": "syncing", "synced_at": now()})
    save_job(job)
    t0 = now()
    res = a.sync({"id": job["id"], "manifest": write_manifest(job)}, build_bundle_fn(job, name, h, a))
    job["sync_s"] = round(now() - t0, 1)
    job["sent_assets"] = len(res["missing"])
    job["sent_bytes"] = sum(x["size"] for x in job["assets"] if x["sha"] in set(res["missing"]))
    job["remote_dir"] = res["dir"]
    remember_assets(name, [x["sha"] for x in job["assets"]])
    job["remote_id"] = a.detach({"id": job["id"]})
    job.update({"state": "running", "started_at": now()})
    save_job(job)
    for f in ("src.tar", "assets.tar", "bundle.tar"):
        p = jobs_dir() / job["id"] / f
        if p.exists():
            p.unlink()


def refresh(job):
    """Lo stato remoto del lavoro dallo snapshot (fresco) dell'host."""
    if job.get("state") not in ("syncing", "starting", "deps", "running", "orphan"):
        return job
    h = hm.hosts().get(job["host"]) or {}
    # lettura dal vivo (e snapshot aggiornato): lo stato di un lavoro in corso non puo' aspettare il giro del sondatore
    try:
        if job["host"] == "local":
            st = ad.adapter_for("local", h, CFG).status()
        else:
            snap = hm.poll_one(job["host"], force=True) or {}
            st = None if snap.get("error") else snap.get("status")
    except ad.HostError:
        st = None
    if st is None:
        return job
    rj = (st.get("jobs") or {}).get(job["id"])
    if rj:
        job["remote_status"] = rj.get("status") or {}
        job["tail"] = rj.get("tail") or ""
        s = job["remote_status"].get("state")
        if s in ("done", "failed", "cancelled"):
            job["state"] = s
            job["rc"] = job["remote_status"].get("rc")
            job["ended_at"] = now()
        elif s:
            job["state"] = s
        prog = job.get("progress_re")
        if prog and job["tail"]:
            ms = re.findall(prog, job["tail"])
            if ms:
                last = ms[-1]
                job["progress"] = "/".join(last) if isinstance(last, tuple) else str(last)
    save_job(job)
    return job


def dest_for(job):
    """--dest se dato; <cartella>/<out> solo se git lo ignora; altrimenti <state_dir>/offload/<id>/out. Mai sopra un
    file tracciato."""
    if job.get("dest_dir"):
        return Path(job["dest_dir"]), False
    outs = job.get("out") or []
    def ignored(o):   # «out/» nel .gitignore vale solo per una cartella: se out non esiste ancora si chiede anche «out/»
        return any(subprocess.run(["git", "-C", job["dir"], "check-ignore", "-q", x]).returncode == 0
                   for x in (o, o.rstrip("/") + "/"))
    if outs and all(ignored(o) for o in outs):
        return Path(job["dir"]), True
    return jobs_dir() / job["id"] / "out", False


def fetch(job):
    h = hm.hosts()[job["host"]]
    a = ad.adapter_for(job["host"], h, CFG)
    tmp = jobs_dir() / job["id"] / "fetched"
    if tmp.exists():
        shutil.rmtree(tmp)
    files = a.fetch({"id": job["id"]}, job.get("out") or [], tmp)
    bad = [f["path"] for f in files if ad.sha256_file(tmp / f["path"]) != f["sha256"]]
    if bad:
        raise Refused(M("offload.sha_mismatch", files=", ".join(bad[:3])))
    dest, in_project = dest_for(job)
    tracked = set(git(job["dir"], "ls-files").splitlines()) if in_project else set()
    for f in files:
        if f["path"] in tracked:
            continue
        target = dest / f["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp / f["path"]), str(target))
    shutil.rmtree(tmp, ignore_errors=True)
    job.update({"fetched": [f["path"] for f in files], "outputs": {f["path"]: f["sha256"] for f in files},
                "dest": str(dest), "fetched_at": now()})
    rec = {k: job.get(k) for k in ("id", "commit", "host", "why", "rc", "outputs", "recipe", "dir")}
    rec["seconds"] = round((job.get("ended_at") or now()) - (job.get("started_at") or now()), 1)
    # accanto ai risultati (3.4): nel progetto o in --dest; nella cartella di stato solo se i risultati sono li'
    hm.write_json((dest if in_project or job.get("dest_dir") else jobs_dir() / job["id"]) / f"offload-{job['id']}.json", rec)
    save_job(job)
    return files


def release(job):
    with leases() as data:
        data["leases"] = [l for l in data["leases"] if l.get("job") != job["id"]]


def notify(job):
    text = M("offload.finished", id=job["id"], host=job["host"], state=job["state"], rc=job.get("rc"),
             dest=job.get("dest") or "-")
    try:
        inbox = _load("cm-inbox")
        inbox.ledger("offload", id=job["id"], host=job["host"], state=job["state"], rc=job.get("rc"))
        if job.get("notify"):
            inbox.put(job["notify"], text, "supervisor offload")
    except Exception:
        pass
    return text


def finish(job):
    """Un lavoro finito: fetch (salvo --no-fetch), riga nello scheduler.log con la durata vera, prenotazione libera."""
    if job["state"] in ("done", "failed") and not job.get("no_fetch") and not job.get("fetched_at") and job.get("out"):
        try:
            fetch(job)
        except (Refused, ad.HostError, subprocess.CalledProcessError) as e:
            job["fetch_error"] = str(e)
    h = hm.hosts().get(job["host"]) or {}
    r = rel(job["host"], h, job.get("cls"), hm.hosts()["local"].get("measured") or {}) or 1.0
    secs = round((job.get("ended_at") or now()) - (job.get("started_at") or now()), 1)
    sched_log({"event": "done", "id": job["id"], "at": iso(), "host": job["host"], "recipe_key": job.get("recipe_key"),
               "rc": job.get("rc"), "state": job["state"], "seconds": secs, "rel": r, "est_local_s": job.get("est"),
               "predicted_s": job.get("predicted_s"), "sync_s": job.get("sync_s"), "sent_bytes": job.get("sent_bytes")})
    if job.get("deps") and job["state"] == "done":
        remember_deps(job["host"], job["deps"]["key"])
    job["closed"] = True
    save_job(job)
    release(job)
    notify(job)


# ------------------------------------------------------------------ comandi
def split_cmd(argv):
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, []


def opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def new_job(folder, argv, cmd):
    folder = os.path.realpath(cm.expand(folder))
    if not os.path.isdir(folder):
        raise Refused(M("offload.no_dir", dir=folder))
    jid = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + _secrets.token_hex(2)
    job = {"id": jid, "dir": folder, "created_at": now(), "state": "new", "dirty": "--dirty" in argv,
           "no_fetch": "--no-fetch" in argv, "notify": opt(argv, "--notify") or "",
           "dest_dir": os.path.realpath(cm.expand(opt(argv, "--dest"))) if opt(argv, "--dest") else ""}
    rn = opt(argv, "--recipe")
    if rn:
        r = load_recipe(folder, rn, opt(argv, "--recipes"))
        job.update({"recipe": rn, "needs": r.get("needs") or {}, "files": r.get("files") or {}, "cmd": r.get("cmd"),
                    "footprint_gb": r.get("footprint_gb"), "est_local_s": r.get("est_local_s"),
                    "progress_re": r.get("progress"), "timeout_s": r.get("timeout_s"), "env": r.get("env") or {},
                    "deps_s": r.get("deps_s"), "out_bytes": r.get("out_bytes")})
        job["out"] = (r.get("files") or {}).get("out") or []
    else:
        if not cmd:
            raise Refused(M("offload.usage"))
        job.update({"needs": parse_needs(opt(argv, "--needs")), "cmd": " ".join(shlex.quote(c) for c in cmd) if len(cmd) > 1 else cmd[0],
                    "files": {"assets": [opt(argv, "--files")] if opt(argv, "--files") else []},
                    "out": [opt(argv, "--out")] if opt(argv, "--out") else [], "est_local_s": float(opt(argv, "--est", 0)) or None})
        job["recipe_key_cmd"] = job["cmd"]
    n = job["needs"]
    job["secrets"] = bool(n.get("secrets")) or "--secrets" in argv
    job["client"] = is_client_path(folder)
    job["recipe_key"] = f"{folder}#{rn or hashlib.sha1(job['cmd'].encode()).hexdigest()[:10]}"
    return job


def cmd_offload(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    sub = argv[0]
    if sub in ("list", "ls"):
        return cmd_list(argv[1:])
    if sub in ("status", "log", "wait", "fetch", "cancel", "clean", "tick"):
        return {"status": cmd_status, "log": cmd_log, "wait": cmd_wait, "fetch": cmd_fetch, "cancel": cmd_cancel,
                "clean": cmd_clean, "tick": cmd_tick}[sub](argv[1:])
    head, cmd = split_cmd(argv)
    try:
        job = new_job(head[0], head[1:], cmd)
        prepare(job)
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 5
    force = opt(head, "--host")
    if force and force not in hm.hosts():
        print(M("host.unknown", name=force, known=", ".join(hm.hosts())), file=sys.stderr)
        return 2
    job["_check_helper"] = False
    host, verdict, why, rows = schedule(job, force)
    job.pop("_check_helper", None)
    est = next((r["est"] for r in rows if r["est"] is not None), None)
    job.update({"why": why, "est": est})
    if "--explain" in head:
        print(explain_table(rows))
        print(M("sched.verdict", verdict=verdict, host=host or "-", why=why))
    sched_log({"event": "decision", "id": job["id"], "at": iso(), "recipe_key": job["recipe_key"], "cls": job.get("cls"),
               "verdict": verdict, "host": host, "why": why, "est_local_s": est,
               "rows": [{k: r[k] for k in ("host", "rule", "rel", "gain", "cost", "margin", "bytes")} for r in rows]})
    if verdict == "refuse":
        print(M("sched.refused", why=why), file=sys.stderr)
        for r in rows:
            if r["rule"]:
                print(f"  {r['host']}: {r['rule']}", file=sys.stderr)
        return 6
    if "--dry-run" in head:
        return 0
    jobs_dir().joinpath(job["id"]).mkdir(parents=True, exist_ok=True)
    job["host"] = host
    with leases() as data:
        data["leases"].append({"job": job["id"], "host": host, "cls": job["cls"], "since": now(), "queued": verdict == "queue"})
    if verdict == "queue":
        job["state"] = "queued"
        save_job(job)
        print(M("offload.queued", id=job["id"], host=host, why=why))
        return 0
    save_job(job)
    try:
        start(job, host)
    except (Refused, ad.HostError, subprocess.CalledProcessError) as e:
        job.update({"state": "failed", "error": str(e)})
        save_job(job)
        release(job)
        print(M("offload.start_failed", id=job["id"], host=host, err=str(e)), file=sys.stderr)
        return 7
    print(M("offload.started", id=job["id"], host=host, why=why, sent=job.get("sent_assets", 0),
            mb=round((job.get("sent_bytes") or 0) / 1048576, 1), s=job.get("sync_s")))
    return 0


def cmd_list(argv):
    rows = all_jobs()
    if "--json" in argv:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print(M("offload.none"))
        return 0
    for j in rows[-30:]:
        if not j.get("closed"):
            j = refresh(j)
        print(f"  {j['id']}  {j.get('host') or '-':8} {j.get('state'):10} {j.get('progress') or '':10} "
              f"{os.path.basename(j.get('dir', ''))} {j.get('recipe') or ''}")
    return 0


def cmd_status(argv):
    if not argv:
        print(M("offload.id_needed"), file=sys.stderr)
        return 2
    j = refresh(load_job(argv[0]))
    if "--json" in argv:
        print(json.dumps(j, ensure_ascii=False, indent=2))
        return 0
    print(M("offload.status_line", id=j["id"], host=j.get("host"), state=j.get("state"), progress=j.get("progress") or "-",
            rc="-" if j.get("rc") is None else j["rc"], why=j.get("why") or "-"))
    if j.get("tail"):
        print("  " + j["tail"].replace("\n", "\n  "))
    return 0


def cmd_log(argv):
    if not argv:
        print(M("offload.id_needed"), file=sys.stderr)
        return 2
    j = load_job(argv[0])
    h = hm.hosts()[j["host"]]
    a = ad.adapter_for(j["host"], h, CFG)
    sep = "\\" if a.family == "windows" else "/"
    remote = (j.get("remote_dir") or "") + sep + "log.txt"
    local = jobs_dir() / j["id"] / "log.txt"
    offset = 0
    while True:
        try:
            a.get(remote, local)
            data = local.read_bytes()
            sys.stdout.write(data[offset:].decode("utf-8", "replace"))
            sys.stdout.flush()
            offset = len(data)
        except ad.HostError as e:
            print(M("host.err_" + e.code, name=j["host"], detail=e.detail), file=sys.stderr)
            return 3
        if "-f" not in argv:
            return 0
        j = refresh(j)
        if j["state"] not in ACTIVE:
            return 0
        time.sleep(10)


WAIT_MAX_MIN = 110   # sotto le 2 ore che Claude Code 2.1.285 concede al massimo a un comando in background


def cmd_wait(argv):
    """Una sessione lo lancia con run_in_background e viene svegliata alla fine (3.4). Da Claude Code 2.1.285 un comando
    in background si ferma al suo `timeout` (30 min se non lo dice, 2 ore al massimo) e la sessione non si risveglia a
    fine lavoro: `wait` esce da solo dopo --max-min minuti (110) con exit 4 e «ancora in corso, rilancia». Si lancia
    con timeout 7200000 e si rilancia finche' non esce con 0 o 1."""
    if not argv:
        print(M("offload.id_needed"), file=sys.stderr)
        return 2
    max_min = WAIT_MAX_MIN
    if "--max-min" in argv:
        i = argv.index("--max-min")
        try:
            max_min = float(argv[i + 1])
        except (IndexError, ValueError):
            print(M("offload.wait_usage"), file=sys.stderr)
            return 2
        argv = argv[:i] + argv[i + 2:]
    deadline = time.time() + max_min * 60
    j = load_job(argv[0])
    while True:
        if j["state"] == "queued":
            tick([])
            j = load_job(argv[0])
        else:
            j = refresh(j)
        if j["state"] not in ACTIVE and j["state"] != "orphan":
            break
        pause = 15 if j["state"] != "queued" else 30
        if time.time() + pause > deadline:
            print(M("offload.wait_again", id=j["id"], host=j.get("host") or "-", state=j["state"], min=f"{max_min:g}"))
            return 4
        time.sleep(pause)
    if not j.get("closed"):
        finish(j)
    j = load_job(argv[0])
    print(M("offload.finished", id=j["id"], host=j["host"], state=j["state"], rc=j.get("rc"), dest=j.get("dest") or "-"))
    return 0 if j["state"] == "done" else 1


def cmd_fetch(argv):
    if not argv:
        print(M("offload.id_needed"), file=sys.stderr)
        return 2
    j = refresh(load_job(argv[0]))
    try:
        files = fetch(j)
    except (Refused, ad.HostError) as e:
        print(str(e), file=sys.stderr)
        return 3
    print(M("offload.fetched", n=len(files), dest=j["dest"]))
    return 0


def cmd_cancel(argv):
    if not argv:
        print(M("offload.id_needed"), file=sys.stderr)
        return 2
    j = load_job(argv[0])
    if j["state"] == "queued":
        j["state"] = "cancelled"
    else:
        try:
            ad.adapter_for(j["host"], hm.hosts()[j["host"]], CFG).cancel({"id": j["id"]})
        except ad.HostError as e:
            print(M("host.err_" + e.code, name=j["host"], detail=e.detail), file=sys.stderr)
            return 3
        j["state"] = "cancelled"
    j["ended_at"] = now()
    save_job(j)
    release(j)
    print(M("offload.cancelled", id=j["id"]))
    return 0


def cmd_clean(argv):
    if argv and argv[0] == "--older":
        days = int(re.sub(r"\D", "", argv[1] if len(argv) > 1 else "7") or 7)
        n = 0
        for j in all_jobs():
            if j.get("state") in ACTIVE or now() - (j.get("ended_at") or j.get("created_at") or now()) < days * 86400:
                continue
            n += clean_one(j)
        print(M("offload.cleaned_n", n=n))
        return 0
    if not argv:
        print(M("offload.id_needed"), file=sys.stderr)
        return 2
    j = load_job(argv[0])
    if j.get("state") in ACTIVE:
        print(M("offload.still_running", id=j["id"]), file=sys.stderr)
        return 4
    clean_one(j)
    print(M("offload.cleaned", id=j["id"]))
    return 0


def clean_one(j):
    if j.get("host") and j.get("state") not in ("new", "queued"):
        try:
            ad.adapter_for(j["host"], hm.hosts()[j["host"]], CFG).clean({"id": j["id"]})
        except (ad.HostError, KeyError):
            return 0
    # restano job.json, il riepilogo e i risultati (out/): si tolgono solo i file di lavoro
    for p in (jobs_dir() / j["id"]).iterdir():
        if p.name in ("job.json", "out", f"offload-{j['id']}.json"):
            continue
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    j["cleaned_at"] = now()
    save_job(j)
    return 1


def tick(argv):
    """Chiude i lavori finiti e avvia quelli in coda quando un posto si libera. Lo chiama `hosts poll`."""
    out = []
    for j in all_jobs():
        if j.get("closed"):
            continue
        if j["state"] in ("syncing", "starting", "deps", "running", "orphan"):
            j = refresh(j)
        if j["state"] in ("done", "failed", "cancelled"):
            finish(j)
            out.append(j["id"])
    for j in all_jobs():
        if j.get("state") != "queued":
            continue
        with leases() as data:
            reclaim(data)
            data["leases"] = [l for l in data["leases"] if l.get("job") != j["id"]]
        host, verdict, why, _ = schedule(j, None)
        if verdict != "run":
            with leases() as data:
                data["leases"].append({"job": j["id"], "host": host or j["host"], "cls": j["cls"], "since": now(), "queued": True})
            continue
        with leases() as data:
            data["leases"].append({"job": j["id"], "host": host, "cls": j["cls"], "since": now(), "queued": False})
        j["why"] = why
        try:
            start(j, host)
        except (Refused, ad.HostError, subprocess.CalledProcessError) as e:
            j.update({"state": "failed", "error": str(e), "closed": True})
            save_job(j)
            release(j)
    return out


def cmd_tick(argv):
    tick(argv)
    return 0


# ------------------------------------------------------------------ heavy run
def cmd_heavy(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    if argv[0] == "status":
        with leases() as data:
            reclaim(data)
            ls = list(data["leases"])
        for name, h in hm.hosts().items():
            mine = [l for l in ls if l["host"] == name]
            lim = host_limits(name, h)
            print(M("heavy.status_host", host=name, used=sum(1 for l in mine if not l.get("queued")), lim=lim.get("heavy"),
                    queued=sum(1 for l in mine if l.get("queued"))))
            for l in mine:
                j = hm.read_json(job_path(l["job"])) if l.get("job") else None
                label = (j or {}).get("recipe") or os.path.basename((j or {}).get("dir", "")) if j else (l.get("label") or "heavy run")
                print(f"    {l.get('job') or l.get('pid')}  {l['cls']}  {label}  {(j or {}).get('progress') or ''}"
                      + ("  [coda]" if l.get("queued") else ""))
        return 0
    if argv[0] != "run":
        print(__doc__)
        return 2
    head, cmd = split_cmd(argv[1:])
    folder = opt(head, "--dir") or os.getcwd()
    if not cmd and not opt(head, "--recipe"):
        print(M("heavy.usage"), file=sys.stderr)
        return 2
    if "--needs" not in head and not opt(head, "--recipe"):
        head = head + ["--needs", "role=compute"]
    try:
        job = new_job(folder, head, cmd)
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 5
    host, verdict, why, rows = schedule(job, opt(head, "--host"))
    if "--explain" in head:
        print(explain_table(rows))
        print(M("sched.verdict", verdict=verdict, host=host or "-", why=why))
    if verdict == "refuse":
        print(M("sched.refused", why=why), file=sys.stderr)
        return 6
    if host == "local" and verdict == "run":
        # resta sulla regia: si esegue qui, nella cartella vera, dentro la prenotazione
        with leases() as data:
            data["leases"].append({"pid": os.getpid(), "host": "local", "cls": job["cls"], "since": now(),
                                   "label": " ".join(cmd)[:60]})
        t0 = now()
        try:
            rc = subprocess.run(low_priority(cmd_for(job, hm.hosts()["local"].get("kind") or "linux-tmux")), shell=True, cwd=job["dir"]).returncode
        finally:
            with leases() as data:
                data["leases"] = [l for l in data["leases"] if l.get("pid") != os.getpid()]
        sched_log({"event": "done", "id": job["id"], "at": iso(), "host": "local", "recipe_key": job["recipe_key"], "rc": rc,
                   "state": "done" if rc == 0 else "failed", "seconds": round(now() - t0, 1), "rel": 1.0})
        return rc
    # un altro host (o la coda): diventa un offload, e si aspetta la fine
    try:
        prepare(job)
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 5
    jobs_dir().joinpath(job["id"]).mkdir(parents=True, exist_ok=True)
    job.update({"host": host, "why": why})
    with leases() as data:
        data["leases"].append({"job": job["id"], "host": host, "cls": job["cls"], "since": now(), "queued": verdict == "queue"})
    job["state"] = "queued" if verdict == "queue" else job["state"]
    save_job(job)
    if verdict == "run":
        try:
            start(job, host)
        except (Refused, ad.HostError, subprocess.CalledProcessError) as e:
            job.update({"state": "failed", "error": str(e)})
            save_job(job)
            release(job)
            print(M("offload.start_failed", id=job["id"], host=host, err=str(e)), file=sys.stderr)
            return 7
    print(M("heavy.offloaded", id=job["id"], host=host, why=why), file=sys.stderr)
    return cmd_wait([job["id"]])


# ------------------------------------------------------------------ night (3.4)
def night_blocked(account):
    """Il motivo per cui una voce della notte non parte adesso per il registro delle prenotazioni, o ''. La voce
    resta sulla regia (v1): conta come un lavoro pesante sul limite di `local`."""
    h = hm.hosts()["local"]
    with leases() as data:
        reclaim(data)
        u = used(data, "local")
    lim = host_limits("local", h)
    if u["heavy"] >= int(lim.get("heavy", 1)):
        return M("sched.night_full", used=u["heavy"], lim=lim.get("heavy"))
    return ""


@contextmanager
def night_lease(label):
    with leases() as data:
        data["leases"].append({"pid": os.getpid(), "host": "local", "cls": "heavy", "since": now(), "label": label})
    try:
        yield
    finally:
        with leases() as data:
            data["leases"] = [l for l in data["leases"] if l.get("pid") != os.getpid()]


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "offload":
        return cmd_offload(argv[1:])
    if argv[0] == "heavy":
        return cmd_heavy(argv[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
