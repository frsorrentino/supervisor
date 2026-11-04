#!/usr/bin/env python3
"""supervisor host — le macchine su cui supervisor manda lavoro (piano docs/plans/2026-09-27-multi-pc.md, v1).

  supervisor host add NOME --ssh ALIAS [--kind linux-tmux|macos-tmux|windows-native|wsl] [--root DIR] [--yes]
  supervisor host doctor NOME|local [--no-bench] [--force-bench] [--no-webgl] [--json]
  supervisor host confirm NOME [--roles a,b] [--trust work|full] [--accounts a,b] [--secrets] [--clients]
                                  [--limits heavy=N,render=N,sessions=N] [--root DIR] [--yes]
  supervisor host list [--json]
  supervisor host remove NOME
  supervisor host update NOME [--yes]
  supervisor host admit NOME <cartella>   la cartella puo' avere una sessione su NOME? (la regola, exit 0/1)
  supervisor host fetch NOME <sessione>   i commit fatti la' → ramo locale NOME/<cartella>
  supervisor host sessions               le sessioni lanciate su altri host (launch --host)
  supervisor hosts poll [--force] [--cron]  un giro del sondatore (dal cron ogni minuto, con offload tick)
  supervisor hosts install | uninstall     la riga del sondatore nel crontab

Host dichiarati, capacita' misurate: l'utente dice nome, trasporto e (se vuole) tipo; `doctor` misura il resto e
propone ruoli, limiti e fiducia; l'utente conferma. Un host non confermato non riceve lavoro. L'host `local` (la
regia) esiste sempre e passa dallo stesso doctor. Nessuna scrittura su un altro host senza chiederla: l'aiutante
remoto si installa solo con un si' (o --yes). Prove: CM_SSH_BIN, CM_SCP_BIN, CM_HOSTS_TTY=0|1.
"""
import base64
import datetime as dt
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
ad = _load("cm-adapters")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
S = CFG["scheduler"]
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Sistemi senza patch di sicurezza (5): build di Windows → fine del supporto per Home/Pro. Una build fuori tabella non
# genera avvisi. Aggiornare quando Microsoft pubblica nuove date.
WINDOWS_EOS = {17763: "2020-11-10", 18362: "2020-12-08", 18363: "2021-05-11", 19041: "2021-12-14",
               19042: "2022-05-10", 19043: "2022-12-13", 19044: "2023-06-13", 19045: "2025-10-14",
               22000: "2023-10-10", 22621: "2024-10-08", 22631: "2025-11-11", 26100: "2026-10-13",
               26200: "2027-10-12"}


def tty():
    v = os.environ.get("CM_HOSTS_TTY")
    if v is not None:
        return v == "1"
    return sys.stdin.isatty() and sys.stdout.isatty()


def state_dir():
    return Path(cm.expand(CFG["state_dir"]))


def snap_path(name):
    return state_dir() / "hosts" / f"{name}.json"


def proposal_path(name):
    return state_dir() / "hosts" / f"{name}.proposal.json"


def now_iso():
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M")


def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def write_json(p, data):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


# ------------------------------------------------------------------ config: le tre parti di hosts.<nome>
def raw_config():
    return read_json(cm.config_path(), {}) or {}


def save_host(name, entry):
    """Scrive hosts.<nome> nel file di configurazione, lasciando com'e' il resto del file."""
    raw = raw_config()
    raw.setdefault("hosts", {})
    if entry is None:
        raw["hosts"].pop(name, None)
    else:
        raw["hosts"][name] = entry
    write_json(cm.config_path(), raw)
    CFG["hosts"] = raw["hosts"]


def hosts():
    """Gli host dichiarati piu' `local`, sempre presente."""
    h = dict(CFG.get("hosts") or {})
    loc = dict(h.get("local") or {})
    loc.setdefault("kind", "linux-tmux")
    h["local"] = loc
    return h


def host(name):
    h = hosts().get(name)
    if h is None:
        raise SystemExit(M("host.unknown", name=name, known=", ".join(sorted(hosts()))))
    return h


def adapter(name, first_contact=False):
    fake = os.environ.get("CM_RSESSION_ADAPTER")   # prove: lo stesso adattatore finto di cm-rsession
    if fake:
        spec = importlib.util.spec_from_file_location("cm_rsession_fake", fake)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.adapter_for(name, hosts().get(name) or {}, CFG)
    return ad.adapter_for(name, hosts().get(name) or {}, CFG, first_contact=first_contact)


def confirmed(h, name=""):
    return name == "local" or bool(h.get("confirmed_at"))


# ------------------------------------------------------------------ misure → ruoli, limiti, fiducia (1.3-1.5)
def rel_bench(m, local_m, key):
    """Punteggio relativo alla regia (1,0 = come la regia), solo fra misure fatte con lo stesso runtime."""
    b, lb = (m or {}).get("bench") or {}, (local_m or {}).get("bench") or {}
    if key == "webgl":
        w, lw = (m or {}).get("webgl") or {}, (local_m or {}).get("webgl") or {}
        if w.get("ms") and lw.get("ms") and w.get("hardware") and lw.get("hardware"):
            return round(lw["ms"] / w["ms"], 2)
        return None
    raw = f"cpu_{key}_raw"
    if b.get(raw) and lb.get(raw) and (b.get("runtime") or "").split(".")[0] == (lb.get("runtime") or "").split(".")[0]:
        return round(b[raw] / lb[raw], 2)
    return None


def derive_roles(m, local_m, kind):
    th = S["roles"]
    roles = []
    rel = rel_bench(m, local_m, "multi")
    if rel is not None and rel >= float(th["compute_cpu_multi"]) and (m.get("ram_gb") or 0) >= float(th["compute_ram_gb"]):
        roles.append("compute")
    if ((m.get("webgl") or {}).get("hardware")) and (m.get("tools") or {}).get("ffmpeg"):
        roles.append("render")
    t = m.get("tools") or {}
    desk = m.get("interactive_desktop") if kind == "windows-native" else bool(t.get("tmux"))
    if t.get("claude") and m.get("accounts") and desk:
        roles.append("sessions")
    return roles


def derive_limits(m, name="", footprint_gb=None):
    L = S["limits"]
    threads = int(m.get("threads") or 1)
    ram = float(m.get("ram_gb") or 0)
    reserve = float(L["reserve_gb"])
    if name == "local":
        reserve += int(CFG["sessions"]["max_sessions"]) * float(L["session_reserve_gb"])
    fp = float(footprint_gb or L["heavy_footprint_gb"])
    heavy = max(1, min(threads // int(L["threads_per_heavy"]), int(max(0.0, ram - reserve) // fp)))
    render = 1 if (m.get("webgl") or {}).get("hardware") else 0
    sessions = min(int(L["sessions_cap"]), int(max(0.0, ram - reserve) // float(L["session_peak_gb"])))
    formula = (f"heavy = min({threads}//{L['threads_per_heavy']}, ({ram:g} - {reserve:g}) // {fp:g}) = {heavy}; "
               f"render = {render}; sessions = min({L['sessions_cap']}, ({ram:g} - {reserve:g}) // {L['session_peak_gb']}) = {sessions}")
    return {"heavy": heavy, "render": render, "sessions": sessions}, formula


def account_names_for(dirs):
    """I nomi degli account locali i cui config_dir hanno la stessa cartella base di quelle con login sull'host."""
    out = []
    for acc, a in (CFG.get("accounts") or {}).items():
        if os.path.basename(cm.expand(a.get("config_dir") or "").rstrip("/")) in (dirs or []):
            out.append(acc)
    return out


def eos(m):
    """(data di fine supporto, scaduto?) per i sistemi in tabella, altrimenti (None, False)."""
    b = m.get("os_build")
    d = WINDOWS_EOS.get(int(b)) if b else None
    if not d:
        return None, False
    return d, dt.date.fromisoformat(d) < dt.date.today()


def warnings(name, h, m):
    w = []
    if name == "local":
        return w
    d, gone = eos(m)
    if gone:
        w.append(M("host.warn_eos", date=d))
    if m.get("admin") is True or m.get("admin") == "sudo":
        w.append(M("host.warn_admin"))
    if m.get("defender_note") in ("disabled", "never-updated"):
        w.append(M("host.warn_defender", note=m["defender_note"]))
    elif m.get("defender_signature_age_d") and m["defender_signature_age_d"] > 7:
        w.append(M("host.warn_defender_old", days=m["defender_signature_age_d"]))
    for c in m.get("foreign_credentials") or []:
        w.append(M("host.warn_cred", what=c))
    if m.get("power") and m["power"] != "no-sleep-on-ac":
        w.append(M("host.warn_power", power=m["power"]))
    w.append(M("host.warn_ip"))
    return w


def proposal(name, h, m):
    local_m = hosts()["local"].get("measured") or {}
    roles = derive_roles(m, local_m, h.get("kind"))
    limits, formula = derive_limits(m, name)
    fam = "windows" if h.get("kind") == "windows-native" else "posix"
    return {"roles": roles, "limits": limits, "formula": formula,
            "trust": {"level": "work", "accounts": account_names_for(m.get("accounts")), "secrets": False, "clients": False},
            "remote_root": h.get("remote_root") or S["remote_root"][fam],
            "rel": {"cpu_single": rel_bench(m, local_m, "single"), "cpu_multi": rel_bench(m, local_m, "multi"),
                    "webgl": rel_bench(m, local_m, "webgl")}}


# ------------------------------------------------------------------ comandi
def ask(q):
    if not tty():
        return False
    print(q + " ", end="", flush=True)
    return sys.stdin.readline().strip().lower() in ("s", "si", "sì", "y", "yes")


def flag(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def cmd_add(argv):
    if not argv or argv[0].startswith("-"):
        print(M("host.add_usage"), file=sys.stderr)
        return 2
    name = argv[0]
    if name == "local" or not NAME_RE.match(name):
        print(M("host.bad_name", name=name), file=sys.stderr)
        return 2
    alias = flag(argv, "--ssh")
    if "--cloud" in argv:
        save_host(name, {"kind": "cloud", "transport": {"cloud": True, "account": flag(argv, "--account") or ""},
                         "added_at": now_iso()})
        print(M("host.added_cloud", name=name))
        return 0
    if not alias:
        print(M("host.add_usage"), file=sys.stderr)
        return 2
    kind = flag(argv, "--kind")
    if kind and kind not in ad.KINDS:
        print(M("host.bad_kind", kind=kind, kinds=", ".join(ad.KINDS)), file=sys.stderr)
        return 2
    entry = {"kind": kind or "", "transport": {"ssh": alias}, "added_at": now_iso()}
    if flag(argv, "--root"):
        entry["remote_root"] = flag(argv, "--root")
    # primo contatto: BatchMode, chiave dell'host fissata nel known_hosts dedicato (accept-new: una chiave gia'
    # fissata e diversa fallisce lo stesso)
    try:
        if not kind:
            kind = ad.detect_kind(name, entry, CFG, first_contact=True)
            entry["kind"] = kind
        a = ad.adapter_for(name, entry, CFG, first_contact=True)
        rc, _, err = a.exec("echo ok", timeout=30)
    except ad.HostError as e:
        print(M("host.err_" + e.code, name=name, detail=e.detail), file=sys.stderr)
        return 3
    save_host(name, entry)
    print(M("host.added", name=name, kind=kind, alias=alias, known_hosts=str(state_dir() / "known_hosts")))
    ok, _ = a.helper_ok()
    if not ok:
        if "--yes" in argv or ask(M("host.ask_helper", name=name, dir=ad.HELPER_DIR)):
            a.install_helper()
            ok, _ = a.helper_ok()
            print(M("host.helper_installed" if ok else "host.helper_mismatch", name=name))
        else:
            print(M("host.helper_needed", name=name))
            return 0
    if "--no-doctor" in argv:
        print(M("host.next_doctor", name=name))
        return 0
    return cmd_doctor([name] + [x for x in argv if x in ("--no-bench", "--force-bench", "--no-webgl", "--yes")])


def bandwidth(a):
    """Andata e ritorno di un file fisso di 32 MB con lo stesso trasporto dei lavori (scp): MB/s."""
    if not a.remote:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="cm-bw-"))
    f = tmp / "cm-bw.bin"
    f.write_bytes(bytes(range(256)) * (32 * 4096))
    prep = a.call("prepare", "cm-bw")
    rpath = prep["dir"] + (prep.get("sep") or "/") + "cm-bw.bin"
    t0 = time.time()
    a.put(f, rpath)
    a.get(rpath, tmp / "back.bin")
    s = time.time() - t0
    try:
        a.call("clean", "cm-bw")
    except ad.HostError:
        pass
    ok = (tmp / "back.bin").stat().st_size == f.stat().st_size
    for x in tmp.iterdir():
        x.unlink()
    tmp.rmdir()
    return round(64 / s, 1) if ok and s > 0 else None


def cmd_doctor(argv):
    if not argv:
        print(M("host.doctor_usage"), file=sys.stderr)
        return 2
    name = argv[0]
    h = host(name)
    a = adapter(name)
    t0 = time.time()
    try:
        ok, have = a.helper_ok()
        if not ok:
            print(M("host.helper_mismatch", name=name), file=sys.stderr)
            return 4
        quick = a.status()
        load = quick.get("load_pct") or 0
        bench = "--no-bench" not in argv
        prev = h.get("measured") or {}
        if bench and load > int(S["bench_max_load_pct"]) and "--force-bench" not in argv:
            print(M("host.bench_postponed", name=name, load=load, max=S["bench_max_load_pct"]))
            bench = False
        m = a.probe(bench=bench, webgl="--no-webgl" not in argv)
        m.pop("ok", None)
        if m.get("bench"):
            m["bench"]["load_pct"] = load
        elif prev.get("bench"):
            m["bench"] = prev["bench"]
        if "--no-webgl" in argv and prev.get("webgl"):
            m["webgl"] = prev["webgl"]
        m["bandwidth_mb_s"] = bandwidth(a) if a.remote else None
        m["helper"] = have
    except ad.HostError as e:
        print(M("host.err_" + e.code, name=name, detail=e.detail), file=sys.stderr)
        return 3
    m["at"] = now_iso()
    m["seconds"] = round(time.time() - t0, 1)
    h = dict(h)
    h["measured"] = m
    if name != "local" and not h.get("kind"):
        h["kind"] = m.get("kind")
    save_host(name, h)
    prop = proposal(name, h, m)
    write_json(proposal_path(name), prop)
    if "--json" in argv:
        print(json.dumps({"measured": m, "proposal": prop, "warnings": warnings(name, h, m)}, ensure_ascii=False, indent=2))
        return 0
    print_measured(name, h, m)
    if name == "local":
        return 0
    print_proposal(name, prop, warnings(name, h, m))
    if confirmed(h):
        return 0
    if tty():
        return confirm_interactive(name, prop)
    print(M("host.next_confirm", name=name))
    return 0


def fmt(v, unit=""):
    return "-" if v is None else f"{v}{unit}"


def print_measured(name, h, m):
    w = m.get("webgl") or {}
    b = m.get("bench") or {}
    print(M("host.measured_header", name=name, kind=h.get("kind") or m.get("kind"), at=m.get("at"), s=m.get("seconds")))
    print(f"  {m.get('os')} · {m.get('arch')} · {m.get('cores')}C/{m.get('threads')}T · RAM {m.get('ram_gb')} GB")
    print("  GPU: " + (", ".join(g.get("name", "?") for g in m.get("gpu") or []) or "-"))
    print("  WebGL: " + (f"{w.get('renderer')} ({'hardware' if w.get('hardware') else 'software'}, {w.get('ms')} ms)"
                         if w.get("renderer") else "-"))
    tools = m.get("tools") or {}
    print("  " + " · ".join(f"{k} {v}" for k, v in tools.items() if v and k != "chrome"))
    if b:
        print(f"  bench ({b.get('runtime')}): cpu1 {b.get('cpu_single_raw')} · cpuN {b.get('cpu_multi_raw')} · "
              f"disco {b.get('disk_write_mb_s')} MB/s" + (f" · rete {m['bandwidth_mb_s']} MB/s" if m.get("bandwidth_mb_s") else ""))


def print_proposal(name, p, warns):
    r = p["rel"]
    print(M("host.proposal_header", name=name))
    print(M("host.proposal_rel", single=fmt(r["cpu_single"]), multi=fmt(r["cpu_multi"]), webgl=fmt(r["webgl"])))
    print(M("host.proposal_roles", roles=", ".join(p["roles"]) or "-"))
    print(M("host.proposal_limits", heavy=p["limits"]["heavy"], render=p["limits"]["render"],
            sessions=p["limits"]["sessions"], formula=p["formula"]))
    t = p["trust"]
    print(M("host.proposal_trust", level=t["level"], accounts=", ".join(t["accounts"]) or "-",
            secrets=str(t["secrets"]).lower(), clients=str(t["clients"]).lower()))
    print(M("host.proposal_root", root=p["remote_root"]))
    for x in warns:
        print("  ! " + x)


def parse_limits(s):
    out = {}
    for part in (s or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = int(v)
    return out


def apply_confirm(name, prop, over):
    h = dict(host(name))
    m = h.get("measured") or {}
    roles = over.get("roles", prop["roles"])
    limits = dict(prop["limits"])
    limits.update(over.get("limits") or {})
    for k, v in (over.get("limits") or {}).items():
        if v > prop["limits"].get(k, v):
            print(M("host.warn_limit_up", key=k, value=v, computed=prop["limits"].get(k)))
    trust = dict(prop["trust"])
    trust.update({k: v for k, v in (over.get("trust") or {}).items() if v is not None})
    if trust.get("level") == "full" and eos(m)[1]:
        print(M("host.full_refused_eos", name=name), file=sys.stderr)
        return 5
    h.update({"roles": roles, "limits": limits, "trust": trust, "remote_root": over.get("remote_root") or prop["remote_root"],
              "confirmed_at": now_iso()})
    save_host(name, h)
    print(M("host.confirmed", name=name, roles=", ".join(roles) or "-", trust=trust["level"]))
    return 0


def confirm_interactive(name, prop):
    print(M("host.confirm_ask"), end=" ", flush=True)
    a = sys.stdin.readline().strip().lower()
    if a in ("s", "si", "sì", "y", "yes"):
        return apply_confirm(name, prop, {})
    if a in ("m", "modifica", "e", "edit"):
        over = {}
        r = input(M("host.edit_roles", roles=",".join(prop["roles"])) + " ").strip()
        if r:
            over["roles"] = [x for x in r.split(",") if x]
        lim = input(M("host.edit_limits") + " ").strip()
        if lim:
            over["limits"] = parse_limits(lim)
        acc = input(M("host.edit_accounts", accounts=",".join(prop["trust"]["accounts"])) + " ").strip()
        if acc:
            over["trust"] = {"accounts": [x for x in acc.split(",") if x]}
        return apply_confirm(name, prop, over)
    print(M("host.not_confirmed", name=name))
    return 0


def cmd_confirm(argv):
    if not argv:
        print(M("host.confirm_usage"), file=sys.stderr)
        return 2
    name = argv[0]
    if name == "local":
        print(M("host.local_no_confirm"))
        return 0
    host(name)
    prop = read_json(proposal_path(name))
    if not prop:
        print(M("host.no_proposal", name=name), file=sys.stderr)
        return 4
    over = {}
    if flag(argv, "--roles") is not None:
        over["roles"] = [x for x in flag(argv, "--roles").split(",") if x]
    if flag(argv, "--limits"):
        over["limits"] = parse_limits(flag(argv, "--limits"))
    t = {}
    if flag(argv, "--trust"):
        if flag(argv, "--trust") not in ("work", "full"):
            print(M("host.bad_trust"), file=sys.stderr)
            return 2
        t["level"] = flag(argv, "--trust")
    if flag(argv, "--accounts") is not None:
        t["accounts"] = [x for x in flag(argv, "--accounts").split(",") if x]
    if "--secrets" in argv:
        t["secrets"] = True
    if "--clients" in argv:
        t["clients"] = True
    if (t.get("secrets") or t.get("clients")) and t.get("level", prop["trust"]["level"]) != "full":
        print(M("host.secrets_need_full"), file=sys.stderr)
        return 2
    over["trust"] = t
    if flag(argv, "--root"):
        over["remote_root"] = flag(argv, "--root")
    return apply_confirm(name, prop, over)


def age_s(snap):
    return time.time() - (snap or {}).get("read_at", 0)


def fmt_age(s):
    if s is None or s > 10 ** 8:
        return M("host.never")
    if s < 90:
        return M("host.age_s", n=int(s))
    if s < 5400:
        return M("host.age_min", n=int(s // 60))
    return M("host.age_h", n=int(s // 3600))


def host_rows():
    rows = []
    for name, h in hosts().items():
        snap = read_json(snap_path(name), {}) or {}
        st = snap.get("status") or {}
        state = M("host.state_regia") if name == "local" else (M("host.state_confirmed") if confirmed(h) else M("host.state_pending"))
        if snap.get("error"):
            reach = M("host.unreachable_since", age=fmt_age(age_s({"read_at": snap.get("last_ok_at", 0)})))
        else:
            reach = ""
        m = h.get("measured") or {}
        old = ""
        if m.get("at"):
            try:
                days = (dt.datetime.now() - dt.datetime.fromisoformat(m["at"])).days
                if days > int(S["doctor_max_age_d"]):
                    old = M("host.measure_old", days=days)
            except ValueError:
                pass
        rows.append({"name": name, "kind": h.get("kind") or "?", "state": state,
                     "roles": ",".join(h.get("roles") or []) if name != "local" else "-",
                     "trust": (h.get("trust") or {}).get("level", "-") if name != "local" else "full",
                     "limits": "/".join(str((h.get("limits") or derive_limits(m, name)[0] if m else {}).get(k, "-"))
                                        for k in ("heavy", "render", "sessions")),
                     "load": "-" if st.get("load_pct") is None else f"{st['load_pct']}%",
                     "read": fmt_age(age_s(snap)) if snap else M("host.never"),
                     "note": " ".join(x for x in (reach, old, "" if m else M("host.not_measured")) if x),
                     "admin": m.get("admin") in (True, "sudo") and name != "local"})
    return rows


def cmd_list(argv):
    rows = host_rows()
    if "--json" in argv:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    hdr = [M("host.col_name"), M("host.col_kind"), M("host.col_state"), M("host.col_roles"), M("host.col_trust"),
           "heavy/render/sess", M("host.col_load"), M("host.col_read"), ""]
    table = [hdr] + [[r["name"], r["kind"], r["state"], r["roles"], r["trust"], r["limits"], r["load"], r["read"],
                      (r["note"] + (" " + M("host.admin_badge") if r["admin"] else "")).strip()] for r in rows]
    widths = [max(len(str(r[i])) for r in table) for i in range(len(hdr))]
    for r in table:
        print("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)).rstrip())
    return 0


def cmd_remove(argv):
    if not argv or argv[0] == "local":
        print(M("host.remove_usage"), file=sys.stderr)
        return 2
    host(argv[0])
    save_host(argv[0], None)
    for p in (snap_path(argv[0]), proposal_path(argv[0])):
        if p.exists():
            p.unlink()
    print(M("host.removed", name=argv[0]))
    return 0


def cmd_update(argv):
    if not argv or argv[0] == "local":
        print(M("host.update_usage"), file=sys.stderr)
        return 2
    name = argv[0]
    host(name)
    a = adapter(name)
    try:
        ok, _ = a.helper_ok()
        if ok:
            print(M("host.helper_current", name=name))
            return 0
        if not ("--yes" in argv or ask(M("host.ask_helper", name=name, dir=ad.HELPER_DIR))):
            print(M("host.helper_needed", name=name))
            return 0
        a.install_helper()
        ok, _ = a.helper_ok()
    except ad.HostError as e:
        print(M("host.err_" + e.code, name=name, detail=e.detail), file=sys.stderr)
        return 3
    print(M("host.helper_installed" if ok else "host.helper_mismatch", name=name))
    return 0 if ok else 4


# ------------------------------------------------------------------ sondatore (2.3)
def lively(snap):
    """Qualcosa di vivo nell'ultima lettura: lavori in corso o sessioni remote aperte."""
    st = (snap or {}).get("status") or {}
    jobs = any(((j or {}).get("status") or {}).get("state") in ("starting", "deps", "running") for j in (st.get("jobs") or {}).values())
    sess = any(s.get("alive") for s in st.get("sessions") or [])
    return jobs or sess


def remote_alive(snap):
    """{(nome, stato)} delle sessioni vive di uno snapshot."""
    return {((s.get("entry") or {}).get("name"), (s.get("entry") or {}).get("status"))
            for s in ((snap or {}).get("status") or {}).get("sessions") or [] if s.get("alive")}


def relay_push_request():
    """09/10 (dall'app: una sessione di prova su win chiusa alle 23:00 restava «al lavoro» nello stato e i prompt
    tornavano «not running»): le sessioni remote cambiate chiedono una push al demone del relay, come un hook. Senza
    relay acceso, niente."""
    R = CFG.get("relay") or {}
    if not R.get("enabled"):
        return
    try:
        d = Path(cm.expand(R.get("dir") or "~/.cc-supervisor/relay")) / "push-req"
        d.mkdir(parents=True, exist_ok=True)
        (d / "hosts").touch()
    except OSError:
        pass


def poll_one(name, force=False):
    """status() di un host confermato e scrittura dello snapshot; nessuna lettura se quella di prima e' fresca."""
    h = hosts()[name]
    prev = read_json(snap_path(name), {}) or {}
    due = int(S["poll_busy_s"]) if lively(prev) else int(S["poll_idle_s"])
    if name != "local" and not confirmed(h):
        return None
    if not force and prev and age_s(prev) < due - 5:
        return None
    a = adapter(name)
    snap = {"name": name, "read_at": time.time()}
    try:
        snap["status"] = a.status()
        snap["last_ok_at"] = snap["read_at"]
    except ad.HostError as e:
        snap.update({"error": e.code, "detail": e.detail[:200], "last_ok_at": prev.get("last_ok_at", 0),
                     "status": prev.get("status")})
    write_json(snap_path(name), snap)
    if not snap.get("error") and remote_alive(prev) != remote_alive(snap):
        relay_push_request()   # 09/10: una sessione remota aperta, chiusa o ferma su una domanda arriva subito all'app
    # 09/10: la sessione ferma su una domanda porta qui il suo transcript: domanda e opzioni nell'app senza aspettare;
    # 10/10: anche quando cambia stato (fine turno: esito e «Prossimi» nella card) o la copia non c'e' ancora
    before = remote_alive(prev)
    for s in [] if snap.get("error") else ((snap.get("status") or {}).get("sessions") or []):
        e = s.get("entry") or {}
        if s.get("alive") and e.get("name") and (e.get("status") == "waiting" or (e.get("name"), e.get("status")) not in before
                                                  or not mirror_path(name, e.get("sessionId") or "").is_file()):
            try:
                sync_transcript(name, e["name"], e.get("sessionId") or "")
            except Exception:   # noqa: BLE001 — la copia e' in piu': mai far cadere il giro del sondatore
                pass
    if not snap.get("error"):
        try:
            forward_to_master(name, (snap.get("status") or {}).get("sessions") or [])
        except Exception:   # noqa: BLE001 — come la copia: mai far cadere il giro del sondatore
            pass
    return snap


MASTER_MARK = re.compile(r"^\W{0,4}(Per la master|For the master)\W{0,4}:", re.I)
TAIL_LINE = re.compile(r"^\W{0,4}((Esito|Prossimi|Watch|Outcome|Next)\W{0,4}:|(Tipo|Type)\W{0,4}:\W{0,4}"   # 1.98
                       r"(pubblicato|fatto|aspetta|in corso|risposta|problema|shipped|done|waiting|running|answer|problem)\W{0,4}$)", re.I)
FORWARD_KEEP = 500


def _epoch(ts):
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def master_blocks(path, offset, since=0.0):
    """10/10 (il maintainer: una sessione su win non poteva passare un compito alla master): i blocchi «Per la master:» nei
    testi dell'assistente della copia del transcript, dal byte `offset`, scritti non prima di `since`. Solo i blocchi di testo dell'assistente,
    mai i risultati dei tool ne' i messaggi dell'utente. Un blocco va dalla riga del marcatore alla fine di quel
    testo, senza le righe finali Esito, Prossimi e Watch. → ([(chiave uuid:i:j, testo)], offset della fine
    dell'ultima riga intera letta)."""
    with open(path, "rb") as f:
        f.seek(offset)
        buf = f.read()
    end = buf.rfind(b"\n") + 1
    out = []
    for raw in buf[:end].splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if d.get("type") != "assistant" or d.get("isSidechain") or _epoch(d.get("timestamp")) < since:
            continue
        content = (d.get("message") or {}).get("content")
        for i, b in enumerate(content if isinstance(content, list) else []):
            if not isinstance(b, dict) or b.get("type") != "text":
                continue
            lines = str(b.get("text") or "").splitlines()
            starts = [j for j, l in enumerate(lines) if MASTER_MARK.match(l)]
            for n, j in enumerate(starts):
                block = lines[j:starts[n + 1] if n + 1 < len(starts) else len(lines)]
                while block and (not block[-1].strip() or TAIL_LINE.match(block[-1])):
                    block.pop()
                text = "\n".join(block).strip()
                if text:
                    out.append((f"{d.get('uuid') or ''}:{i}:{j}", text))
    return out, offset + end


def forward_to_master(host, sessions):
    """10/10: inoltra alla master, con `talk master --no-wait`, ogni blocco «Per la master:» che una sessione di
    `host` ha scritto (master_blocks), una volta sola: le chiavi gia' partite e il punto letto di ogni copia stanno
    in hosts/forwarded-<host>.json, quindi anche dopo un riavvio. Niente storia vecchia alla master: partono solo i
    messaggi scritti dopo l'attivazione del canale (`since`, il primo giro); la copia di una sessione puo' essere
    indietro e portare blocchi vecchi dopo il punto letto (21:17 del 10/10: un blocco delle 20:07 inoltrato)."""
    st_p = state_dir() / "hosts" / f"forwarded-{re.sub(r'[^A-Za-z0-9._-]', '_', host)}.json"
    first = not st_p.is_file()
    st = read_json(st_p, {}) or {}
    since = float(st.get("since") or 0) or time.time()
    offs, sent = st.get("offsets") or {}, list(st.get("sent") or [])
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    changed = first or not st.get("since")
    for s in sessions:
        e = s.get("entry") or {}
        sid, ename = str(e.get("sessionId") or ""), str(e.get("name") or "")
        p = mirror_path(host, sid) if sid else None
        if not p or not p.is_file():
            continue
        if first:
            offs[sid] = p.stat().st_size
            continue
        blocks, new_off = master_blocks(p, int(offs.get(sid) or 0) if int(offs.get(sid) or 0) <= p.stat().st_size else 0, since)
        shown = ename[len(host) + 1:] if ename.startswith(host + "-") and (state_dir() / "rsessions" / f"{ename}.json").is_file() else ename
        for key, text in blocks:
            if key in sent:
                continue
            msg = M("host.forward_head", host=host, name=shown, time=time.strftime("%H:%M")) + "\n" + text
            r = subprocess.run([sys.executable, str(HERE / "cm-talk.py"), "talk", master, msg, "--no-wait"],
                               capture_output=True, text=True, timeout=120)
            if r.returncode not in (0, 5):   # 5 = la master e' chiusa: il messaggio resta nella sua casella
                new_off = None
                break
            sent.append(key)
        if new_off is not None:
            offs[sid] = new_off
        changed = True
    if changed:
        write_json(st_p, {"since": since, "offsets": offs, "sent": sent[-FORWARD_KEEP:]})


# ------------------------------------------------------------------ letture per sessions e quota (4.3, 6)
def remote_snapshots():
    """{nome: snapshot} degli host dichiarati diversi da local. Solo file: sessions e quota non toccano il trasporto."""
    out = {}
    for name, h in hosts().items():
        if name == "local":
            continue
        snap = read_json(snap_path(name))
        if snap:
            out[name] = snap
    return out


def local_account_of_dir(dirname):
    """L'account locale il cui config_dir ha questa cartella base (.claude, .claude-pixel)."""
    for acc, a in (CFG.get("accounts") or {}).items():
        if os.path.basename(cm.expand(a.get("config_dir") or "").rstrip("/")) == dirname:
            return acc
    return dirname


def remote_session_rows():
    """Le sessioni vive nei registri peer degli host, con lo schema di riga di cm-sessions.collect piu' `host`."""
    rows = []
    for name, snap in remote_snapshots().items():
        st = snap.get("status") or {}
        age = age_s(snap)
        for s in st.get("sessions") or []:
            e = s.get("entry") or {}
            if not s.get("alive"):
                continue
            bridge = str(e.get("bridgeSessionId") or "")
            ename = e.get("name") or e.get("sessionId", "")[:8]
            # 10/10 (dal telefono: «win» due volte nel nome): una sessione di launch --host si chiama «HOST-base»;
            # col qualificatore «HOST:» davanti basta la base. cm-rsession.resolve rimette il prefisso per i comandi.
            shown = ename[len(name) + 1:] if ename.startswith(name + "-") and (state_dir() / "rsessions" / f"{ename}.json").is_file() else ename
            rows.append({"pid": e.get("pid") or 0, "name": f"{name}:{shown}", "remote_name": ename,
                         "account": local_account_of_dir(s.get("config_dir") or ".claude"), "cwd": e.get("cwd") or "",
                         "tmux": "", "status": e.get("status") or "?", "session_id": e.get("sessionId") or "",
                         "link": ("https://claude.ai/code/session_" + bridge.removeprefix("session_")) if bridge else "",
                         "started_at": e.get("startedAt"), "socket": "", "registry": "", "attached": None,
                         "waiting": e.get("status") == "waiting", "channel": "remoto", "version": e.get("version") or "",
                         "host": name, "read_age_s": round(age), "unreachable": bool(snap.get("error")),
                         # 09/10: la copia locale del transcript (sync_transcript), per la chat e la domanda nell'app
                         "transcript": str(mirror_path(name, e.get("sessionId") or "")) if e.get("sessionId") else "",
                         # 10/10: la cartella di qui da cui e' partita (il record di launch --host), per il recap
                         "local_dir": str((read_json(state_dir() / "rsessions" / f"{e.get('name')}.json") or {}).get("dir") or "") if e.get("name") else ""})
    return rows


MIRROR_MAX = 32 * 1024 * 1024   # una lettura copia al piu' questo: un transcript enorme arriva in piu' giri


def mirror_path(host, sid):
    return state_dir() / "hosts" / "transcripts" / re.sub(r"[^A-Za-z0-9._-]", "_", host) / f"{re.sub(r'[^A-Za-z0-9-]', '', sid)}.jsonl"


def sync_transcript(host, session, sid=""):
    """04/11 (supervisor-chat-vuota-04-11): una copia alla volta per sessione, anche fra processi. Due letture insieme
    (la PWA ricaricata chiede piu' pagine, ognuna di qualche secondo) appendevano gli stessi byte due volte: la copia
    diventava piu' lunga del file di la' e ripartiva da zero, e chi la leggeva in quel momento la trovava vuota."""
    import fcntl
    d = state_dir() / "hosts" / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f".lock-{re.sub(r'[^A-Za-z0-9._-]', '_', f'{host}-{session}')}", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            return _sync_transcript(host, session, sid)
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _sync_transcript(host, session, sid=""):
    """09/10 (dal telefono: la chat di una sessione su win vuota nell'app): porta qui i byte nuovi del
    transcript della sessione `session` sull'host, in coda alla copia locale (mirror_path). Byte per byte, nessuna
    riscrittura: la copia e' il file di la'. Un file di la' piu' corto della copia la riparte da zero; un altro
    sessionId (la sessione riaperta) ha la sua copia. → il percorso della copia, o None se l'host non sa leggerlo."""
    a = adapter(host)
    if not hasattr(a, "session_tail"):
        return None
    p = mirror_path(host, sid) if sid else None
    off, got = (p.stat().st_size if p and p.is_file() else 0), 0
    while got < MIRROR_MAX:
        d = a.session_tail(session, off)
        size, at = int(d.get("size") or 0), int(d.get("offset") or 0)
        if not d.get("sid"):
            return p
        q = mirror_path(host, d["sid"])
        if q != p or size < off:   # un altro transcript, o quello di la' e' piu' corto della copia
            q.parent.mkdir(parents=True, exist_ok=True)
            known = q.stat().st_size if q.is_file() else 0
            if known > size:
                q.write_bytes(b"")
                known = 0
            if q != p or known != off:
                p, off = q, known
                if off != at:
                    continue
        data = base64.b64decode(d.get("data") or "")
        if data:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "ab") as f:
                f.write(data)
            off += len(data)
            got += len(data)
        if not data or off >= size:
            break
    return p


FETCH_TTL_S = 3600   # le copie dei file presi da un host restano un'ora (la web locale li legge dopo)


def fetch_file(host, path, max_bytes):
    """10/10 (dal telefono: i file di una sessione su win «missing or unreadable»): copia qui il file `path` di
    `host`, prima misurato la' contro `max_bytes`. → (percorso della copia, byte); (None, byte) se troppo grande;
    (None, -1) se la' non c'e' o l'host non sa leggerlo. Le copie stanno in hosts/files, le vecchie si tolgono."""
    import hashlib
    a = adapter(host)
    if not hasattr(a, "file_size"):
        return None, -1
    size = a.file_size(path)
    if size < 0 or size > max_bytes:
        return None, size
    d = state_dir() / "hosts" / "files"
    d.mkdir(parents=True, exist_ok=True)
    for old in d.iterdir():
        if old.is_file() and time.time() - old.stat().st_mtime > FETCH_TTL_S:
            old.unlink(missing_ok=True)
    base = re.sub(r"[^A-Za-z0-9._-]", "_", re.split(r"[\\/]", path)[-1])[-80:]
    local = d / f"{hashlib.sha1(f'{host}|{path}'.encode()).hexdigest()[:12]}-{base}"
    a.file_get(path, local)
    os.chmod(local, 0o600)
    return local, local.stat().st_size


def host_summary_lines():
    """Una riga per host in fondo a sessions: carico, RAM, disco, lavoro pesante con l'avanzamento, eta' della lettura."""
    lines = []
    for name, snap in remote_snapshots().items():
        st = snap.get("status") or {}
        if snap.get("error"):
            lines.append(M("host.summary_down", name=name, age=fmt_age(age_s({"read_at": snap.get("last_ok_at", 0)}))))
            continue
        jobs = []
        for jid, j in (st.get("jobs") or {}).items():
            state = ((j or {}).get("status") or {}).get("state")
            if state in ("starting", "deps", "running"):
                m = re.findall(r"(\d+)/(\d+)", (j or {}).get("tail") or "")
                jobs.append(f"{jid} {state}" + (f" {m[-1][0]}/{m[-1][1]}" if m else ""))
        lines.append(M("host.summary", name=name, load=st.get("load_pct", "-"), free=st.get("ram_free_gb", "-"),
                       ram=st.get("ram_gb", "-"), disk=st.get("disk_free_gb", "-"),
                       jobs=", ".join(jobs) or M("host.no_jobs"), age=fmt_age(age_s(snap))))
    return lines


def quota_hash(home, dirname, windows):
    import hashlib
    path = home + ("\\" if windows else "/") + dirname
    return hashlib.sha256(path.encode()).hexdigest()[:8]


def remote_quota(account):
    """Le letture della quota di `account` sugli altri host: [(host, mtime, dati)]. Il file si chiama come lo scrive
    la statusline di fable-director su quella macchina: quota-<sha256(config_dir)[:8]>.json."""
    out = []
    a = (CFG.get("accounts") or {}).get(account) or {}
    dirname = os.path.basename(cm.expand(a.get("config_dir") or "").rstrip("/"))
    for name, snap in remote_snapshots().items():
        h = hosts().get(name) or {}
        m = h.get("measured") or {}
        if not m.get("home"):
            continue
        want = f"quota-{quota_hash(m['home'], dirname, h.get('kind') == 'windows-native')}.json"
        for q in (snap.get("status") or {}).get("quota") or []:
            if q.get("file") == want and q.get("data"):
                out.append((name, q.get("mtime") or 0, q["data"]))
    return out


def remote_consumers():
    """{account: {host: n}} delle sessioni vive sugli altri host."""
    out = {}
    for r in remote_session_rows():
        out.setdefault(r["account"], {}).setdefault(r["host"], 0)
        out[r["account"]][r["host"]] += 1
    return out


def remote_transcripts(account):
    """Le copie locali dei transcript delle sessioni interattive vive di `account` sugli altri host (per l'eta' della quota)."""
    out = []
    for name, snap in remote_snapshots().items():
        for s in (snap.get("status") or {}).get("sessions") or []:
            e = s.get("entry") or {}
            if s.get("alive") and e.get("kind") == "interactive" and e.get("sessionId") \
                    and local_account_of_dir(s.get("config_dir") or ".claude") == account:
                out.append(str(mirror_path(name, e["sessionId"])))
    return out


def poll_round(names, force):
    for name in names:
        try:
            poll_one(name, force=force)
        except Exception as e:   # un host rotto non ferma il giro degli altri
            print(f"poll {name}: {e}", file=sys.stderr)


def cmd_poll(argv):
    """Un giro; con --cron (ogni minuto) anche la chiusura dei lavori finiti e la coda (offload tick), e un secondo
    giro dopo 30 s se qualcosa e' vivo: cosi' la cadenza e' 30 s solo mentre serve."""
    force = "--force" in argv
    only = [a for a in argv if not a.startswith("-")]
    names = only or list(hosts())
    poll_round(names, force)
    if "--cron" not in argv:
        return 0
    off = _load("cm-offload")
    off.tick([])
    if any(lively(read_json(snap_path(n), {})) for n in names if n != "local"):
        time.sleep(30)
        poll_round(names, False)
        off.tick([])
    return 0


CRON_TAG = "supervisor hosts poll"


def cron_line():
    return f"* * * * * {cm.home() / '.local' / 'bin' / 'supervisor'} hosts poll --cron >/dev/null 2>&1"


def crontab(text=None):
    c = os.environ.get("CM_CRONTAB_CMD", "crontab")
    if text is None:
        return subprocess.run([c, "-l"], capture_output=True, text=True).stdout
    subprocess.run([c, "-"], input=text, text=True, check=True)


def ensure_poller():
    """True se ha appena messo il sondatore nel crontab, False se c'era gia'. 09/10: senza sondatore lo snapshot degli
    host non si scrive mai e le sessioni remote non compaiono in sessions ne' nell'app."""
    cur = crontab()
    if CRON_TAG in cur:
        return False
    crontab(cur.rstrip("\n") + ("\n" if cur.strip() else "") + "# supervisor: sondatore degli host (piano multi-PC 2.3)\n" + cron_line() + "\n")
    return True


def poller_installed():
    return CRON_TAG in crontab()


def cmd_install(argv):
    if argv:   # 09/10: «install --help» installava davvero
        print(M("host.cron_usage"), file=sys.stderr)
        return 2
    print(M("host.cron_installed", line=cron_line()) if ensure_poller() else M("host.cron_present"))
    return 0


def cmd_uninstall(argv):
    if argv:
        print(M("host.cron_usage"), file=sys.stderr)
        return 2
    cur = crontab()
    lines = [l for l in cur.splitlines() if CRON_TAG not in l and "sondatore degli host" not in l]
    crontab("\n".join(lines) + ("\n" if lines else ""))
    print(M("host.cron_removed"))
    return 0


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    fn = {"add": cmd_add, "doctor": cmd_doctor, "confirm": cmd_confirm, "list": cmd_list, "ls": cmd_list,
          "remove": cmd_remove, "rm": cmd_remove, "update": cmd_update, "poll": cmd_poll, "install": cmd_install,
          "uninstall": cmd_uninstall,
          # sessioni su un altro host (piano 4.2): il codice sta in cm-rsession.py
          "admit": lambda a: _load("cm-rsession").main(["admit"] + a),
          "fetch": lambda a: _load("cm-rsession").main(["fetch"] + a),
          "sessions": lambda a: _load("cm-rsession").main(["sessions"] + a)}.get(cmd)
    if not fn:
        print(__doc__)
        return 2
    return fn(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
