#!/usr/bin/env python3
"""cm-system.py: quanto pesa la macchina e cosa fare (system_advice, contratto 1.90, 28/10/2026).

Richiesta del maintainer dalla web app il 28/10 alle 13:19, dopo che alle 13:14 la master aveva trovato a mano un server
chrome-bridge --launch orfano (46% CPU, 939 MB) e una master orfana, con carico 13-15 su 8 core.

SY1 misura da una /proc finta: carico per core, memoria disponibile, livello (verde, giallo, rosso dal peggiore),
    consumatori raggruppati (claude, chrome_bridge, relay, other) con numero, CPU e memoria
SY2 CPU di un processo = differenza dei tick fra due misure dello stesso processo (non la media dalla nascita)
SY3 processi senza padre: chrome-bridge --launch → «Chiudi» (sys_kill pid:procStart); lo stesso figlio di claude no;
    uno script della persona senza padre al 90% da ore no (fuori dagli schemi); un server MCP senza padre sotto i 30 min
    no, oltre i 30 min e il 10% si'; mai il relay ne' i suoi antenati
SY4 un claude senza il suo tmux → «Chiudi» col suo pid
SY5 sessioni: idle da ore col contesto grande → «Chiudi le ferme» (op close, nomi dello stato), mai la master ne' una
    attaccata; 5 sessioni vive o piu' → le piu' ferme da chiudere, senza ripetere quelle gia' nel consiglio stale
SY6 lavoro pesante (ffmpeg al 150%) → «spostalo su win», nessuna azione; memoria sotto 1 GB → rosso, nessuna azione,
    i gruppi che pesano di piu' nel testo; i rossi prima, al massimo 5
SY7 status() sulla macchina vera (sola lettura): la forma del blocco
SY8 system_stuck (28/10, dal vivo alle 15:08): carico 49,85 con 48 processi in D sul ponte grafico (sommelier e
    notificationd, wchan virtwl_ioctl_ptr e do_vfd_close, figli di tre unita' systemd --user in ciclo di riavvio). Si
    contano a parte senza leggerne la cmdline (numero, da quando, memoria, unita'); il carico che colora non li conta;
    «Ferma il ciclo» (op sys_unit_stop) solo per le unita' ammesse con molti riavvii e ancora attive; il riavvio di Linux
    solo nel testo; un processo in D su un'altra attesa resta nel carico
SY9 system_stuck (28/10, nota di supervisor-app dal vivo: rosso a 6,7-7,3 per core e niente diceva cosa pesa, o solo un
    chrome-bridge allo 0%): il consiglio dei bloccati dice che il carico viene da loro, non da lavoro, e passa davanti;
    a pari livello l'ordine e' la CPU vera; un carico alto che nessun consiglio spiega nomina i processi che pesano, o
    dice che viene da attese
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"),
                           "workspace": {"root": str(tmp / "ws"), "root_session_name": "master"},
                           "accounts": {"personale": {"config_dir": str(tmp / ".claude")}}, "default_account": "personale"}))
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(tmp), "CM_HOME": str(tmp), "CM_TMUX_ARGS": f"-L cm-test-sys-{os.getpid()}",
                   "CM_PROC_SCAN_PIDS": ""})
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("cm_system", T.SCRIPTS / "cm-system.py")
SY = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SY)
HZ = os.sysconf("SC_CLK_TCK")
PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024
UPTIME = 100000.0


def fake_proc(d, procs, load="13.20 14.10 12.00 3/900 4242", avail_kb=2_800_000, total_kb=6_795_000):
    d.mkdir(parents=True, exist_ok=True)
    (d / "loadavg").write_text(load + "\n")
    (d / "uptime").write_text(f"{UPTIME} 1.0\n")
    (d / "meminfo").write_text(f"MemTotal:       {total_kb} kB\nMemFree:          100000 kB\nMemAvailable:   {avail_kb} kB\n")
    for p in procs:
        pd = d / str(p["pid"])
        pd.mkdir(exist_ok=True)
        start = int((UPTIME - p.get("age", 3600)) * HZ)
        # pid (comm) state ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt utime stime cutime cstime
        # priority nice threads itreal starttime vsize rss
        fields = [p.get("state", "S"), p["ppid"], 0, 0, 0, 0, 0, 0, 0, 0, 0, p.get("ticks", 0), 0, 0, 0, 20, 0, 1, 0, start, 0, int(p.get("rss_mb", 10) * 1024 / PAGE_KB)]
        (pd / "stat").write_text(f"{p['pid']} (x y) " + " ".join(str(x) for x in fields) + "\n")
        if p.get("cmdline_dir"):   # system_stuck: una cmdline che non si deve leggere (in D si puo' restare bloccati)
            (pd / "cmdline").mkdir(exist_ok=True)
        else:
            (pd / "cmdline").write_bytes(p["args"].replace(" ", "\0").encode() + b"\0")
        (pd / "wchan").write_text(p.get("wchan", "0"))
        (pd / "cgroup").write_text(f"0::{p.get('cgroup', '/user.slice/user-1000.slice/session-1.scope')}\n")


SY._PREV.clear()
P1 = tmp / "proc1"
procs = [
    {"pid": 1, "ppid": 0, "args": "/sbin/init"},
    {"pid": 50, "ppid": 1, "args": "/lib/systemd/systemd --user"},
    {"pid": 100, "ppid": 50, "args": "tmux new-session -d -s master"},
    {"pid": 200, "ppid": 100, "args": "/home/u/.local/bin/claude --remote-control master -n master", "rss_mb": 450, "ticks": 360 * HZ},
    {"pid": 210, "ppid": 200, "args": "node /home/u/chrome-bridge/server/index.js", "rss_mb": 120, "ticks": 0},
    {"pid": 300, "ppid": 50, "args": "node /home/u/chrome-bridge/server/index.js --launch", "rss_mb": 939, "ticks": int(0.46 * 3600 * HZ)},
    {"pid": 310, "ppid": 1, "args": "python3 /home/u/mio-script.py", "rss_mb": 80, "ticks": int(0.9 * 7200 * HZ), "age": 7200},
    {"pid": 320, "ppid": 1, "args": "node /home/u/.local/bin/firebase mcp --only core", "rss_mb": 200, "ticks": int(0.5 * 600 * HZ), "age": 600},
    {"pid": 330, "ppid": 50, "args": "node /home/u/.local/bin/firebase mcp --only core", "rss_mb": 210, "ticks": int(0.2 * 7200 * HZ), "age": 7200},
    {"pid": 400, "ppid": 50, "args": "/usr/bin/python3 /x/supervisor/scripts/cm-relay.py serve", "rss_mb": 60, "ticks": int(0.12 * 3600 * HZ)},
    {"pid": 500, "ppid": 1, "args": "node /home/u/chrome-bridge/server/index.js --launch", "rss_mb": 50, "ticks": 0},
]
fake_proc(P1, procs)
m = SY.measure(str(P1), cores=8)
T.check("SY1 system_advice: load per core 13.2/8 → 1.65, memory available 2734 MB of 6636, level yellow (load yellow, memory green)",
        m["load1"] == 13.2 and m["cores"] == 8 and m["load_per_core"] == 1.65 and m["mem_available_mb"] == 2734 and m["mem_total_mb"] == 6636
        and (m["load_level"], m["mem_level"], m["level"]) == ("yellow", "green", "yellow"), json.dumps(m))
rows = SY.proc_rows(str(P1))
cons = {c["group"]: c for c in SY.consumers(rows)}
T.check("SY1 system_advice: consumers grouped — claude 1 (450 MB), chrome_bridge 3 (210 under claude and the two --launch), relay 1, other the rest; CPU and MB summed",
        set(cons) == {"claude", "chrome_bridge", "relay", "other"}
        and cons["claude"]["count"] == 1 and cons["claude"]["rss_mb"] == 450 and cons["chrome_bridge"]["count"] == 3
        and cons["chrome_bridge"]["rss_mb"] == 939 + 120 + 50 and cons["relay"]["count"] == 1
        and round(cons["claude"]["cpu"]) == 10, json.dumps(cons))
T.check("SY1 consumers sorted by CPU, the biggest first (other 160%, chrome_bridge 46%, relay 12%, claude 10%)",
        [c["group"] for c in SY.consumers(rows)] == ["other", "chrome_bridge", "relay", "claude"], json.dumps(SY.consumers(rows)))
for lvl, load, avail, want in (("green", "3.0 1 1 1/1 1", 3_000_000, "green"), ("red-load", "17.0 1 1 1/1 1", 3_000_000, "red"), ("red-mem", "3.0 1 1 1/1 1", 900_000, "red"),
                               ("yellow-mem", "3.0 1 1 1/1 1", 1_400_000, "yellow")):
    fake_proc(tmp / f"p-{lvl}", [], load=load, avail_kb=avail)
    T.check(f"SY1 level {lvl}", SY.measure(str(tmp / f"p-{lvl}"), cores=8)["level"] == want, json.dumps(SY.measure(str(tmp / f"p-{lvl}"), cores=8)))

# SY2: la seconda misura usa la differenza dei tick
p200 = next(r for r in rows if r["pid"] == 200)
T.check("SY2 first reading: average since birth (360 s of CPU in 3600 s → 10%)", round(p200["cpu"]) == 10, json.dumps(p200))
procs2 = [dict(p) for p in procs]
next(p for p in procs2 if p["pid"] == 200)["ticks"] += 60 * HZ   # 60 s di CPU in ~0 s di orologio: il conto usa l'ora vera
SY._PREV.clear()
SY.proc_rows(str(P1))
k = next(iter(x for x in SY._PREV if x[0] == 200))
SY._PREV[k] = (SY._PREV[k][0], SY._PREV[k][1] - 60.0)   # la misura prima, 60 s fa
fake_proc(P1, procs2)
p200b = next(r for r in SY.proc_rows(str(P1)) if r["pid"] == 200)
T.check("SY2 system_advice: second reading = ticks difference over the clock between readings (60 s of CPU in 60 s → 100%)",
        95 <= p200b["cpu"] <= 101, json.dumps(p200b))

# SY3/SY4
fake_proc(P1, procs)
SY._PREV.clear()
rows = SY.proc_rows(str(P1))
orph = [{"pid": 700, "name": "master", "proc_start": "123"}]
adv = SY.advise(m, rows, orphans=orph, sessions=[], runtime={}, me={400, 1, 50})
kinds = [(a["kind"], (a.get("action") or {}).get("arg")) for a in adv]
start300 = next(r for r in rows if r["pid"] == 300)["start"]
T.check("SY3 system_advice: chrome-bridge --launch without a parent → «Chiudi» with op sys_kill and arg pid:procStart",
        ("orphan_proc", f"300:{start300}") in kinds and next(a for a in adv if a["kind"] == "orphan_proc" and a["action"]["arg"].startswith("300:"))["action"]["op"] == "sys_kill",
        json.dumps(adv, ensure_ascii=False))
T.check("SY3 the chrome-bridge child of claude, the person's script without a parent (90% for 2 h) and the young MCP server are not touched",
        not any((arg or "").split(":")[0] in ("210", "310", "320") for _, arg in kinds), json.dumps(kinds))
T.check("SY3 system_advice: an MCP server without a parent for over 30 min above 10% → «Chiudi»", any(k == "orphan_proc" and (arg or "").startswith("330:") for k, arg in kinds), json.dumps(kinds))
T.check("SY3 the relay itself (pid in `me`) is never advised", not any((arg or "").startswith("400:") for _, arg in kinds), json.dumps(kinds))
T.check("SY3 the --launch with no CPU is still advised (the rule of the pattern: any age, any CPU)", any((arg or "").startswith("500:") for _, arg in kinds), json.dumps(kinds))
T.check("SY4 system_advice: a claude without its tmux → «Chiudi» with its pid", ("orphan", "700:123") in kinds, json.dumps(kinds))
T.check("SY4 every advice: {id, kind, level, text, action}; action {op, arg, label} or null; text names the process",
        all(list(a) == ["id", "kind", "level", "text", "action"] for a in adv)
        and all(a["action"] is None or set(a["action"]) == {"op", "arg", "label"} for a in adv)
        and "master" in next(a for a in adv if a["kind"] == "orphan")["text"], json.dumps(adv, ensure_ascii=False))

# SY5 sessioni
H = 3600
srows = [
    {"tmux": "master", "name": "master", "status": "idle", "status_age_min": 600, "attached": False},
    {"tmux": "alfa", "name": "alfa", "status": "idle", "status_age_min": 300, "attached": False},
    {"tmux": "pix-beta", "name": "pix-beta", "status": "idle", "status_age_min": 200, "attached": False},
    {"tmux": "gamma", "name": "gamma", "status": "idle", "status_age_min": 400, "attached": True},
    {"tmux": "delta", "name": "delta", "status": "busy", "status_age_min": 5, "attached": False},
    {"tmux": "eps", "name": "eps", "status": "idle", "status_age_min": 30, "attached": False},
    {"tmux": "zeta", "name": "zeta", "status": "idle", "status_age_min": 150, "attached": False},
]
rt = {"master": {"context": 90}, "alfa": {"context": 70}, "pix-beta": {"context": 55}, "gamma": {"context": 80}, "delta": {"context": 90}, "eps": {"context": 95}, "zeta": {"context": 10}}
adv5 = SY.advise(m, [], orphans=[], sessions=srows, runtime=rt, me=set(), prefixes=["pix-"])
st = next((a for a in adv5 if a["kind"] == "stale"), None)
T.check("SY5 system_advice: idle for hours with a big context → «Chiudi le ferme», op close with the state's names (alfa, beta); never the master, never an attached one, never a young idle",
        st is not None and st["action"] == {"op": "close", "arg": "alfa,beta", "label": st["action"]["label"]} and st["action"]["label"], json.dumps(adv5, ensure_ascii=False))
tm5 = next((a for a in adv5 if a["kind"] == "too_many"), None)
T.check("SY5 system_advice: 7 sessions alive (6 without the master) ≥ 5 → too_many with the stillest not already advised (zeta), op close",
        tm5 is not None and tm5["action"]["op"] == "close" and tm5["action"]["arg"] == "zeta" and "zeta" in tm5["text"], json.dumps(adv5, ensure_ascii=False))
adv5b = SY.advise(m, [], orphans=[], sessions=srows[:3], runtime=rt, me=set(), prefixes=["pix-"])
T.check("SY5 three sessions → no too_many", not any(a["kind"] == "too_many" for a in adv5b), json.dumps(adv5b, ensure_ascii=False))

# SY6
heavy = [{"pid": 900, "ppid": 1, "start": "1", "age_s": 300, "cpu": 150.0, "rss_mb": 700, "args": "ffmpeg -i a.mov out.mp4"},
         {"pid": 901, "ppid": 1, "start": "1", "age_s": 300, "cpu": 20.0, "rss_mb": 100, "args": "ffmpeg -i b.mov out2.mp4"}]
fake_proc(tmp / "p-low", [], load="3.0 1 1 1/1 1", avail_kb=800_000)
mlow = SY.measure(str(tmp / "p-low"), cores=8)
adv6 = SY.advise(mlow, heavy + [r for r in rows if r["pid"] in (200, 300)], orphans=orph, sessions=[], runtime={}, me=set())
hv = next((a for a in adv6 if a["kind"] == "heavy_job"), None)
lm = next((a for a in adv6 if a["kind"] == "low_memory"), None)
T.check("SY6 system_advice: ffmpeg at 150% → heavy_job «win» in the text, no action; the one at 20% is not named",
        hv is not None and hv["action"] is None and "win" in hv["text"] and "ffmpeg" in hv["text"] and len([a for a in adv6 if a["kind"] == "heavy_job"]) == 1, json.dumps(adv6, ensure_ascii=False))
T.check("SY6 system_advice: memory below 1 GB → low_memory red, no action, the heaviest groups in the text",
        lm is not None and lm["level"] == "red" and lm["action"] is None and "chrome_bridge" in lm["text"], json.dumps(lm, ensure_ascii=False))
T.check("SY6 the reds first, at most 5", [a["level"] for a in adv6] == sorted([a["level"] for a in adv6], key=lambda x: x != "red") and len(adv6) <= 5,
        json.dumps([a["level"] for a in adv6]))

# SY7
real = SY.status(rows=[], runtime={}, prefixes=[])
T.check("SY7 status() on this machine: {at, cores, load1, load_per_core, mem_total_mb, mem_available_mb, level, load_level, mem_level, stuck, consumers, advice}",
        list(real) == ["at", "cores", "load1", "load_per_core", "mem_total_mb", "mem_available_mb", "level", "load_level", "mem_level", "stuck", "consumers", "advice"]
        and real["level"] in ("green", "yellow", "red") and len(real["consumers"]) == 4 and isinstance(real["advice"], list), json.dumps(real)[:400])
# SY8 system_stuck
UNIT = "/user.slice/user-1000.slice/user@1000.service/app.slice/"
stk = [{"pid": 1, "ppid": 0, "args": "/sbin/init"}, {"pid": 50, "ppid": 1, "args": "/lib/systemd/systemd --user"},
       {"pid": 582, "ppid": 50, "args": "sommelier --peer-cmd-prefix x", "cgroup": UNIT + "sommelier@0.service"}]
pid = 1000
for unit, n, comm in (("sommelier-x@0.service", 16, "sommelier"), ("sommelier-x@1.service", 15, "sommelier"), ("cros-notificationd.service", 14, "notificationd")):
    for i in range(n):
        stk.append({"pid": pid, "ppid": 50, "state": "D", "wchan": "virtwl_ioctl_ptr", "cgroup": UNIT + unit, "args": comm, "cmdline_dir": True,
                    "rss_mb": 3.3, "age": 11000 - pid}); pid += 1
for i in range(3):   # dentro sommelier@0, sano: non ha riavvii
    stk.append({"pid": pid, "ppid": 582, "state": "D", "wchan": "do_vfd_close", "cgroup": UNIT + "sommelier@0.service", "args": "sommelier", "cmdline_dir": True, "rss_mb": 3.3, "age": 600}); pid += 1
stk.append({"pid": pid, "ppid": 50, "state": "D", "wchan": "virtwl_ioctl_ptr", "cgroup": UNIT + "foo.service", "args": "foo", "cmdline_dir": True, "rss_mb": 3.3, "age": 900}); pid += 1
stk.append({"pid": pid, "ppid": 50, "state": "D", "wchan": "nfs_wait_bit_killable", "cgroup": UNIT + "bar.service", "args": "cp -r /mnt/nfs /tmp", "rss_mb": 5, "age": 60})
P8 = tmp / "proc8"
fake_proc(P8, stk, load="49.85 46.90 42.96 3/900 4242", avail_kb=4_190_000)
SY._PREV.clear()
rows8 = SY.proc_rows(str(P8))
st8 = SY.stuck_of(rows8, str(P8))
_now8 = __import__("time").time()
T.check("SY8 system_stuck: 49 processes in D on the graphic bridge (virtwl_ioctl_ptr, do_vfd_close) counted although their cmdline is not read; the one in D on NFS is not",
        st8 is not None and st8["count"] == 49, json.dumps(st8))
T.check("SY8 system_stuck: since = the oldest one's start (about 10000 s ago), about 160 MB, the units sorted",
        st8 and abs(st8["since"] - (_now8 - 10000)) <= 3 and 158 <= st8["rss_mb"] <= 163
        and st8["units"] == ["cros-notificationd.service", "foo.service", "sommelier-x@0.service", "sommelier-x@1.service", "sommelier@0.service"], json.dumps(st8))
m8 = SY.measure(str(P8), cores=8, stuck=st8)
T.check("SY8 system_stuck: the load that colours leaves them out — load1 49.85 raw, load_per_core (49.85-49)/8 = 0.11, green",
        m8["load1"] == 49.85 and m8["load_per_core"] == 0.11 and m8["load_level"] == "green" and m8["level"] == "green" and m8["stuck"] == st8, json.dumps(m8))
U8 = {"sommelier-x@0.service": {"restarts": 57, "state": "deactivating"}, "sommelier-x@1.service": {"restarts": 56, "state": "deactivating"},
      "cros-notificationd.service": {"restarts": 58, "state": "deactivating"}, "sommelier@0.service": {"restarts": 0, "state": "active"},
      "foo.service": {"restarts": 60, "state": "activating"}}
adv8 = SY.advise(m8, rows8, units=U8)
lp = next((a for a in adv8 if a["kind"] == "stuck_loop"), None)
sk = next((a for a in adv8 if a["kind"] == "stuck"), None)
T.check("SY8 system_stuck: «Ferma il ciclo» = op sys_unit_stop on the allowed units with many restarts still running (not sommelier@0 with none, not foo.service outside the list), red, first",
        lp is not None and lp["action"] == {"op": "sys_unit_stop", "arg": "cros-notificationd.service,sommelier-x@0.service,sommelier-x@1.service", "label": "Ferma il ciclo"}
        and lp["level"] == "red" and adv8[0] is lp and "58" in lp["text"], json.dumps(adv8, ensure_ascii=False))
T.check("SY8 system_stuck: the stuck processes as words only — how many, since when, the MB, «riavviando Linux» and that it closes every session; no action",
        sk is not None and sk["action"] is None and "49" in sk["text"] and "riavviando Linux" in sk["text"] and "chiude tutte le sessioni" in sk["text"] and "MB" in sk["text"],
        json.dumps(sk, ensure_ascii=False))
adv8b = SY.advise(m8, rows8, units={k: dict(v, state="inactive") for k, v in U8.items()})
T.check("SY8 system_stuck: once the units are stopped (inactive) no «Ferma il ciclo», the stuck count stays as words",
        not any(a["kind"] == "stuck_loop" for a in adv8b) and any(a["kind"] == "stuck" for a in adv8b), json.dumps(adv8b, ensure_ascii=False))
adv8c = SY.advise(m8, rows8, units={k: dict(v, stopping=k != "sommelier@0.service") for k, v in U8.items()})
T.check("SY8 system_stuck: a unit with a stop already queued (deactivating because its processes are in D, as after the master's stop at 15:10) is not advised again",
        not any(a["kind"] == "stuck_loop" for a in adv8c) or "sommelier-x" not in next(a for a in adv8c if a["kind"] == "stuck_loop")["action"]["arg"], json.dumps(adv8c, ensure_ascii=False))
_fs8 = tmp / "systemctl-show"
_fs8.write_text("#!/bin/sh\ncase \"$2\" in\n show) printf 'NRestarts=57\\nId=sommelier-x@0.service\\nActiveState=deactivating\\n\\nNRestarts=0\\nId=sommelier@0.service\\nActiveState=active\\n' ;;\n"
                "list-jobs) printf '3939 sommelier-x@0.service      stop running\\n3950 other.service start waiting\\n' ;;\nesac\n")
_fs8.chmod(0o755)
os.environ["CM_SYSTEMCTL"] = str(_fs8)
_ui8 = SY.unit_info(["sommelier-x@0.service", "sommelier@0.service", "bad name;rm"])
T.check("SY8 system_stuck: unit_info reads NRestarts and ActiveState from `systemctl --user show`, and a queued stop from `list-jobs`",
        _ui8 == {"sommelier-x@0.service": {"restarts": 57, "state": "deactivating", "stopping": True}, "sommelier@0.service": {"restarts": 0, "state": "active", "stopping": False}}, json.dumps(_ui8))
_log8 = tmp / "systemctl.log"
_fk8 = tmp / "systemctl"
_fk8.write_text(f"#!/bin/sh\necho \"$*\" >> {_log8}\n")
_fk8.chmod(0o755)
os.environ["CM_SYSTEMCTL"] = str(_fk8)
_ok8 = SY.stop_units(["sommelier-x@0.service", "cros-notificationd.service"])
_no8 = SY.stop_units(["sommelier@0.service-evil;rm", "ssh.service"])
T.check("SY8 system_stuck: stop_units runs `systemctl --user stop --no-block` for each allowed unit; a unit outside the list is refused and nothing runs",
        _ok8 is True and _no8 is False and _log8.read_text().splitlines() == ["--user stop --no-block sommelier-x@0.service", "--user stop --no-block cros-notificationd.service"],
        _log8.read_text() if _log8.exists() else "no call")

# SY9
skl = next(a for a in adv8b if a["kind"] == "stuck")
T.check("SY9 system_stuck: when the raw load (49.85/8) would be red and without the stuck it is green, the advice says the load comes from them, not from work, with the raw figure",
        "non da lavoro" in skl["text"] and "6,2" in skl["text"], skl["text"])
adv9 = SY.advise(m8, rows8 + [r for r in rows if r["pid"] == 500], units={k: dict(v, state="inactive") for k, v in U8.items()})
T.check("SY9 system_stuck: the stuck advice that explains the load goes before a chrome-bridge at 0% CPU",
        [a["kind"] for a in adv9][:2] == ["stuck", "orphan_proc"], json.dumps([(a["kind"], a["text"]) for a in adv9], ensure_ascii=False))
adv9b = SY.advise(m, rows, orphans=orph, me={400, 1, 50})
yk = [a["action"]["arg"].split(":")[0] if a["action"] else a["kind"] for a in adv9b if a["level"] == "yellow"]
T.check("SY9 system_stuck: same level, ordered by real CPU — the MCP server at 20% before the --launch and the orphan claude at 0%",
        yk.index("330") < yk.index("500") and yk.index("330") < yk.index("700"), json.dumps(yk))
rows9 = [r for r in rows if r["pid"] not in (300,)]
adv9c = SY.advise(m, rows9, me={400})
ld = next((a for a in adv9c if a["kind"] == "load"), None)
T.check("SY9 system_stuck: a yellow load that no advice explains (the MCP at 20% is below 25%) → «load», no action, the heaviest by CPU named, the person's script at 90% first",
        ld is not None and ld["action"] is None and ld["level"] == "yellow" and "mio-script.py 90%" in ld["text"] and "1,6" in ld["text"], json.dumps(adv9c, ensure_ascii=False))
idle9 = [dict(r, cpu=0.5) for r in rows9]
ld2 = next((a for a in SY.advise(m, idle9, me={400}) if a["kind"] == "load"), None)
T.check("SY9 system_stuck: a high load with nothing using CPU → the advice says it comes from waits",
        ld2 is not None and "attese" in ld2["text"], json.dumps(ld2, ensure_ascii=False))
T.check("SY9 system_stuck: a load explained by an advice above 25% (the --launch at 46%) has no «load» advice",
        not any(a["kind"] == "load" for a in adv9b), json.dumps([a["kind"] for a in adv9b]))
T.rm(str(tmp))
T.finish()
