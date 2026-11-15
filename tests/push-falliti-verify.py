#!/usr/bin/env python3
"""Verifica supervisor-push-falliti-13-11: su un Mac, dopo un riavvio, il demone del relay avviato da cron non leggeva la
cartella dei progetti sotto Desktop (macOS la nega ai processi di cron senza Accesso completo al disco). Ogni push dava
«[Errno 1] Operation not permitted», riprovata in silenzio per ~20 ore; `doctor` dava 0 FAIL e 0 WARN, `relay status`
«serve: VIVO» e l'ultima push.

PF1 le push che falliscono di fila (l'EPERM simulato sulla raccolta) si contano in push-fails.json; alla terza la persona
    lo sa, una volta: un evento relay_stale con l'errore e, su macOS con i progetti sotto Desktop, cosa fare con
    /usr/sbin/cron; la quarta e la quinta non avvisano di nuovo
PF2 `relay status` dice l'ultimo errore (ora e messaggio) e le push fallite dell'ultima ora e di fila
PF3 `doctor` FAIL: la cartella dei progetti illeggibile dal demone (con la via d'uscita di macOS), o le push fallite
PF4 alla prima push riuscita il conto torna a zero e doctor non ha piu' il FAIL; l'ultimo errore resta in relay status;
    una serie nuova avvisa di nuovo
PF5 `doctor` WARN se lo stato non si pubblica da piu' di dieci minuti col demone vivo; col demone fermo no
PF6 `relay install` su macOS con i progetti sotto Desktop: avvisa e prova con un job di cron vero. cron non legge → lo dice
    con /usr/sbin/cron, e doctor FAIL; legge → lo dice, doctor PASS; nessuna risposta → lo dice. Il job di prova non resta
    nel crontab. Fuori da macOS, o coi progetti altrove, nessuna prova
"""
import importlib.util as _ilu
import io
import json
import os
import contextlib
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
root = home / "Desktop" / "progetti"
(root / "alfa").mkdir(parents=True)
elsewhere = home / "lavoro"
(elsewhere / "beta").mkdir(parents=True)
rdir = tmp / "relay"
cfg = tmp / "config.json"
CRON = tmp / "crontab.txt"
CRONLOG = tmp / "crontab-writes.txt"
fake_ls = tmp / "fake-ls"
fake_ls.write_text("#!/bin/sh\necho \"ls: $1: Operation not permitted\" >&2\nexit 1\n")
fake_ls.chmod(fake_ls.stat().st_mode | stat.S_IXUSR)
fake_crontab = tmp / "fake-crontab"
# un crontab finto che fa anche da cron per il job di prova: ok lo esegue, eperm lo esegue con un ls che da' l'errore di
# macOS, silent non lo esegue (cron spento)
fake_crontab.write_text(f"""#!/usr/bin/env python3
import os, subprocess, sys
p, log = {str(CRON)!r}, {str(CRONLOG)!r}
if sys.argv[1:] == ["-l"]:
    sys.stdout.write(open(p).read() if os.path.exists(p) else "")
    sys.exit(0)
text = sys.stdin.read()
open(p, "w").write(text)
open(log, "a").write(text + "=====\\n")
mode = os.environ.get("FAKE_CRON_MODE", "silent")
for line in text.splitlines():
    if "cron-probe" in line and mode in ("ok", "eperm"):
        cmd = line.split(" ", 5)[5]
        if mode == "eperm":
            cmd = cmd.replace("/bin/ls", {str(fake_ls)!r}, 1)
        subprocess.run(["/bin/sh", "-c", cmd])
""")
fake_crontab.chmod(fake_crontab.stat().st_mode | stat.S_IXUSR)


def write_cfg(ws_root=root):
    cfg.write_text(json.dumps({
        "language": "en", "state_dir": str(tmp / "state"), "workspace": {"root": str(ws_root)},
        "accounts": {"personal": {"config_dir": str(home / ".claude")}}, "default_account": "personal",
        "relay": {"dir": str(rdir), "enabled": True}}))


ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
       "CM_CRONTAB_CMD": str(fake_crontab), "LANG": "C.UTF-8", "CM_FAKE_MACOS": "1", "CM_CRON_PROBE_WAIT_S": "4"}


def relay(*a, **extra):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-relay.py"), *a], capture_output=True, text=True, env=dict(ENV, **extra), timeout=120)


def doctor(**extra):
    fake = T.fake_machine(tmp, home, chromeos=False, procs=[], which=["tmux", "claude", "python3", "bash"])
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-config.py"), "doctor"], capture_output=True, text=True,
                          env=dict(ENV, CM_FAKE_MACHINE=str(fake), **extra), timeout=120).stdout


def fails():
    try:
        return json.loads((rdir / "push-fails.json").read_text())
    except (OSError, ValueError):
        return {}


def stale_events():
    try:
        evs = json.loads((rdir / "local" / "events.json").read_text())
    except (OSError, ValueError):
        return []
    return [e for e in (evs.values() if isinstance(evs, dict) else evs) if e.get("kind") == "relay_stale"]


def stop_daemon():
    try:
        pid = int((rdir / "serve.pid").read_text())
        os.kill(pid, 15)
        T.wait_until(lambda: not T.proc_alive(pid), 10)
    except (OSError, ValueError):
        pass


write_cfg()
os.environ.update({k: ENV[k] for k in ("CC_SUPERVISOR_CONFIG", "HOME", "CM_HOME", "CM_FAKE_MACOS", "CM_CRONTAB_CMD")})
spec = _ilu.spec_from_file_location("cm_relay", T.SCRIPTS / "cm-relay.py"); RL = _ilu.module_from_spec(spec); spec.loader.exec_module(RL)
rdir.mkdir(parents=True, exist_ok=True)
real_collect = RL.collect_sources
real_unreadable = getattr(RL, "root_unreadable", None)
EPERM = PermissionError(1, "Operation not permitted", str(root))


def broken(on):
    """La cartella dei progetti negata al demone, come su macOS a un processo di cron: la raccolta da' EPERM."""
    def boom(now):
        raise PermissionError(1, "Operation not permitted", str(root))
    RL.collect_sources = boom if on else real_collect
    if real_unreadable:
        RL.root_unreadable = (lambda: str(EPERM)) if on else real_unreadable


def try_push():
    try:
        RL.push(origin="test")
        return None
    except Exception as e:  # noqa: BLE001
        return str(e)


first = try_push()
T.check("PF0 a push with the projects folder readable works and leaves no failure count", first is None and not fails().get("n"), f"{first} {fails()}")

broken(True)
e1, e2 = try_push(), try_push()
f2, ev2 = fails(), stale_events()
e3 = try_push()
f3, ev3 = fails(), stale_events()
e4, e5 = try_push(), try_push()
f5, ev5 = fails(), stale_events()
T.check("PF1 pushes failing in a row (EPERM on the projects folder) are counted: how many, since when, the last error, the folder the daemon cannot read",
        all(x and "Operation not permitted" in x for x in (e1, e2, e3, e4, e5)) and f2.get("n") == 2 and f5.get("n") == 5 and f5.get("since") == f2.get("since")
        and "Operation not permitted" in str(f5.get("error")) and "Operation not permitted" in str(f5.get("root_error")) and len(f5.get("recent") or []) == 5,
        f"{e1} | {f5}")
T.check("PF1 two failures say nothing yet; at the third the person is told, with a relay_stale event that carries the error",
        len(ev2) == 0 and len(ev3) == 1 and "3" in ev3[0].get("title", "") and "Operation not permitted" in ev3[0].get("body", "") and f3.get("told") is True,
        f"{len(ev2)} {ev3}")
T.check("PF1 on macOS with the projects under Desktop the event says what to do: Full Disk Access for /usr/sbin/cron",
        bool(ev3) and "/usr/sbin/cron" in ev3[0].get("body", "") and "Desktop" in ev3[0].get("body", ""), str(ev3))
T.check("PF1 the fourth and the fifth failure do not tell again", len(ev5) == 1, str(len(ev5)))

s = relay("status").stdout
T.check("PF2 relay status: the last error with its time and message, the failed pushes of the last hour and in a row",
        "last error:" in s and "Operation not permitted" in s and time.strftime("%Y-%m-%d") in s and "5 in the last hour, 5 in a row" in s, s)

d = doctor()
T.check("PF3 doctor FAIL: the daemon cannot read the projects folder, with the way out on macOS (/usr/sbin/cron)",
        "FAIL relay_root_unreadable" in d and "Operation not permitted" in d and "/usr/sbin/cron" in d, d[-1500:])
f = fails(); f["root_error"] = ""; f["error"] = "HTTP Error 503"
(rdir / "push-fails.json").write_text(json.dumps(f))
d = doctor()
T.check("PF3 doctor FAIL also when the pushes fail for another reason: how many in a row, since when, the error",
        "FAIL relay_push_failed" in d and "HTTP Error 503" in d and "5 in a row" in d and "relay_root_unreadable" not in d, d[-1500:])
f["last_at"] = time.time() - 3600; f["recent"] = []
(rdir / "push-fails.json").write_text(json.dumps(f))
T.check("PF3 a failure older than a quarter of an hour, with no newer one, is not a FAIL", "FAIL relay_" not in doctor(), "")
f["last_at"] = time.time()
(rdir / "push-fails.json").write_text(json.dumps(f))

broken(False)
ok = try_push()
f6 = fails()
s = relay("status").stdout
d = doctor()
T.check("PF4 the first push that works zeroes the count; doctor has no FAIL; relay status keeps the last error, 0 in a row",
        ok is None and f6.get("n") == 0 and f6.get("ok_at") and "FAIL relay_" not in d and "last error:" in s and "0 in a row" in s, f"{ok} {f6} {s}")
broken(True)
for _ in range(3):
    try_push()
T.check("PF4 a new run of failures tells the person again", len(stale_events()) == 2 and fails().get("n") == 3, f"{len(stale_events())} {fails()}")
broken(False)
try_push()

# PF5: lo stato fermo col demone vivo
last = json.loads((rdir / "last-state.json").read_text())
last.setdefault("state", {})["published_at"] = time.time() - 1500
last["pushed_at"] = time.time() - 1500
(rdir / "last-state.json").write_text(json.dumps(last))
(rdir / "serve.pid").write_text(str(os.getpid()))
d_alive = doctor()
(rdir / "serve.pid").write_text("999999")
d_dead = doctor()
(rdir / "serve.pid").unlink()
T.check("PF5 doctor WARN when the daemon is alive and the state was last published more than ten minutes ago; no WARN with the daemon stopped",
        "WARN relay_state_old" in d_alive and "25 minutes" in d_alive and "relay_state_old" not in d_dead, d_alive[-900:])

# PF6: relay install su macOS, coi progetti sotto Desktop
def install(mode, **extra):
    CRONLOG.write_text("")
    r = relay("install", FAKE_CRON_MODE=mode, **extra)
    stop_daemon()
    try:
        probe = json.loads((rdir / "cron-probe.json").read_text())
    except (OSError, ValueError):
        probe = None
    return r.stdout + r.stderr, probe, CRON.read_text() if CRON.exists() else "", CRONLOG.read_text()


out, probe, tab, writes = install("eperm")
d = doctor()
T.check("PF6 relay install on macOS with the projects under Desktop warns and checks with a test cron job; cron cannot read → it says so "
        "with the error and Full Disk Access for /usr/sbin/cron",
        "Desktop" in out and "Full Disk Access" in out and "/usr/sbin/cron" in out and "Operation not permitted" in out
        and probe and probe.get("ok") is False and "cron-probe" in writes, out + str(probe))
T.check("PF6 the test job does not stay in the crontab, the relay's lines do", "cron-probe" not in tab and "relay ensure" in tab, tab)
T.check("PF6 doctor FAIL after a failed check", "FAIL cron_disk_access_fail" in d and "/usr/sbin/cron" in d, d[-900:])
out, probe, tab, writes = install("ok")
d = doctor()
T.check("PF6 cron reads the folder → it says so, doctor PASS, nothing left in the crontab",
        "cron reads the projects folder" in out and "/usr/sbin/cron" not in out and probe and probe.get("ok") is True and "cron-probe" not in tab
        and "PASS cron_disk_access_ok" in d, out + str(probe) + d[-400:])
out, probe, tab, writes = install("silent")
T.check("PF6 no answer from the test job (cron not running) → it says so and names /usr/sbin/cron; nothing left in the crontab",
        "did not answer" in out and "/usr/sbin/cron" in out and probe and probe.get("ok") is None and "cron-probe" not in tab, out + str(probe))
out, probe, tab, writes = install("eperm", CM_FAKE_MACOS="0")
T.check("PF6 not on macOS: no check, no test job, no leftover result", "Full Disk Access" not in out and "cron-probe" not in writes and probe is None, out + str(probe))
write_cfg(elsewhere)
out, probe, tab, writes = install("eperm")
T.check("PF6 on macOS with the projects outside Desktop, Documents and Downloads: no check", "Full Disk Access" not in out and "cron-probe" not in writes and probe is None,
        out + str(probe))
stop_daemon()
T.rm(tmp)
T.finish()
