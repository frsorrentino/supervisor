#!/usr/bin/env python3
"""Verifica degli host (piano multi-PC v1, passi 1.1-1.3): deduzione dell'account unificata, `hosts` in config,
adattatore via ssh finto (CM_SSH_BIN/CM_SCP_BIN) con tutti i verbi, host spento, chiave dell'host alterata,
onboarding add → doctor → confirm, ruoli/limiti/fiducia derivati. Nessun host vero: la «macchina remota» e' una
HOME finta in una cartella temporanea, servita da tests/lib/fake-ssh.py.

  H1-H4    account_for e --account-for (prefisso piu' lungo, link simbolici, default)
  H5-H6    hosts in config: chiavi libere senza avviso, niente CM_HOSTS in --sh
  V1-V12   verbi dell'adattatore ssh POSIX e dell'host local
  X1-X3    host spento (< 5 s), chiave alterata (fallisce chiuso), opzioni ssh
  O1-O12   host add/doctor/confirm/list/remove/update
  R1-R6    ruoli, limiti, fiducia derivati dalle misure
"""
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

LIB = Path(__file__).resolve().parent / "lib"
tmp = Path(T.tmpdir())
home = tmp / "home"
remote_home = tmp / "rh"
state = tmp / "st"
for d in (home / ".claude", remote_home / ".claude", state):
    d.mkdir(parents=True)
(remote_home / ".claude" / ".credentials.json").write_text("{}")
(home / "ws" / "alfa" / "sub").mkdir(parents=True)
(home / "ws" / "beta").mkdir(parents=True)
os.symlink(home / "ws" / "alfa", home / "alias-alfa")
cfgf = tmp / "config.json"
cfgf.write_text(json.dumps({
    "language": "it", "state_dir": str(state),
    "accounts": {"personale": {"config_dir": "~/.claude"}, "pro": {"config_dir": "~/.claude-pro"}},
    "default_account": "personale",
    "folder_map": [{"path": "~/ws", "account": "personale"}, {"path": "~/ws/alfa", "account": "pro"}],
    "scheduler": {"remote_root": {"posix": str(remote_home / "claude-work")}},
}))
ENV = dict(os.environ, HOME=str(home), CM_HOME=str(home), CC_SUPERVISOR_CONFIG=str(cfgf),
           CM_SSH_BIN=str(LIB / "fake-ssh.py"), CM_SCP_BIN=str(LIB / "fake-scp.py"), FAKE_SSH_HOME=str(remote_home),
           CM_HOSTS_TTY="0", FAKE_SSH_LOG=str(tmp / "ssh.log"))
ENV.pop("CLAUDE_CONFIG_DIR", None)


def run(*args, env=None, timeout=120):
    e = dict(ENV, **(env or {}))
    return subprocess.run([sys.executable, str(T.SCRIPTS / args[0])] + list(args[1:]), capture_output=True, text=True,
                          env=e, timeout=timeout)


def load(name, env=None):
    old = dict(os.environ)
    os.environ.update(dict(ENV, **(env or {})))
    try:
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), T.SCRIPTS / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.environ.clear()
        os.environ.update(old)


# ------------------------------------------------------------------ H: account e config
cm = load("cm-config")
C = cm.load(str(cfgf), warn=False)
old_home = os.environ.get("CM_HOME")
os.environ["CM_HOME"] = str(home)
T.check("H1 longest prefix wins", cm.account_for(C, home / "ws" / "alfa" / "sub") == ("pro", True))
T.check("H2 shorter prefix", cm.account_for(C, home / "ws" / "beta") == ("personale", True))
T.check("H3 symlink resolves to its target", cm.account_for(C, home / "alias-alfa") == ("pro", True))
T.check("H4 no match → default, not deduced", cm.account_for(C, tmp) == ("personale", False))
r = run("cm-config.py", "--account-for", str(home / "ws" / "alfa"))
T.check("H4b --account-for prints the deduced account", r.stdout.strip() == "pro", r.stdout + r.stderr)
r = run("cm-config.py", "--account-for", str(tmp))
T.check("H4c --account-for prints nothing when nothing matches", r.stdout.strip() == "", r.stdout)
raw = json.loads(cfgf.read_text())
raw["hosts"] = {"box": {"kind": "linux-tmux", "transport": {"ssh": "box"}, "measured": {"gpu": [{"name": "x"}]}}}
cfgf.write_text(json.dumps(raw))
r = run("cm-config.py", "--sh")
T.check("H5 free-form hosts keys are not «unknown»", "unknown keys" not in r.stderr, r.stderr)
T.check("H6 --sh does not export hosts", "CM_HOSTS" not in r.stdout and "CM_SCHEDULER_FRESH_S" in r.stdout)
raw.pop("hosts")
cfgf.write_text(json.dumps(raw))
if old_home is None:
    os.environ.pop("CM_HOME", None)

# ------------------------------------------------------------------ V: verbi dell'adattatore
ad = load("cm-adapters")
C = cm.load(str(cfgf), warn=False)
os.environ.update({k: ENV[k] for k in ("CM_SSH_BIN", "CM_SCP_BIN", "FAKE_SSH_HOME", "FAKE_SSH_LOG")})
os.environ["CM_HOME"] = str(home)
h = {"kind": "linux-tmux", "transport": {"ssh": "fakebox"}, "remote_root": str(remote_home / "claude-work")}
a = ad.adapter_for("fakebox", h, C)
T.check("V1 adapter type", type(a).__name__ == "SSHPosix" and a.remote)
ok, have = a.helper_ok()
T.check("V2 no helper before install", not ok)
a.install_helper()
ok, have = a.helper_ok()
T.check("V3 helper installed, sha256 match", ok, str(have))
st = a.status()
T.check("V4 status snapshot", all(k in st for k in ("load_pct", "ram_free_gb", "disk_free_gb", "jobs", "sessions", "quota")), str(st)[:300])
p = a.probe()
T.check("V5 probe basics", p.get("threads", 0) >= 1 and p.get("ram_gb", 0) > 0 and p.get("accounts") == [".claude"], str(p)[:300])
rc, out, _ = a.exec("echo ciao")
T.check("V6 exec", rc == 0 and out.strip() == "ciao")

# sync: un bundle con sorgente, un asset, file di controllo
job = {"id": "t1"}
work = tmp / "job-t1"
work.mkdir()
(work / "asset.bin").write_bytes(b"A" * 5000)
sha = ad.sha256_file(work / "asset.bin")
(work / "manifest.txt").write_text(f"{sha}\t5000\tpublic/asset.bin\n")
job["manifest"] = str(work / "manifest.txt")
srcdir = work / "src"
(srcdir / "out").mkdir(parents=True)
(srcdir / "hello.txt").write_text("hi\n")


def build(missing):
    with tarfile.open(work / "src.tar", "w") as t:
        t.add(srcdir / "hello.txt", "hello.txt")
    with tarfile.open(work / "assets.tar", "w") as t:
        for s in missing:
            t.add(work / "asset.bin", s)
    (work / "cmd.txt").write_text("mkdir -p out && cat public/asset.bin | wc -c > out/size.txt && cp hello.txt out/ && sleep 1\n")
    (work / "timeout.txt").write_text("60\n")
    with tarfile.open(work / "bundle.tar", "w") as t:
        for f in ("src.tar", "assets.tar", "cmd.txt", "timeout.txt"):
            t.add(work / f, f)
    return work / "bundle.tar"


res = a.sync(job, build)
T.check("V7 sync: first send carries the asset", res["missing"] == [sha], str(res))
jd = remote_home / "claude-work" / "jobs" / "t1"
T.check("V7b asset linked into the job", (jd / "src" / "public" / "asset.bin").read_bytes() == b"A" * 5000)
rid = a.detach(job)
T.check("V8 detach returns a remote id", str(rid).startswith(("pid:", "systemd:")), str(rid))
for _ in range(40):
    s = (a.status()["jobs"].get("t1") or {}).get("status") or {}
    if s.get("state") in ("done", "failed"):
        break
    time.sleep(0.5)
T.check("V9 job done, rc 0", s.get("state") == "done" and s.get("rc") == 0, str(s))
dest = tmp / "fetched"
files = a.fetch(job, ["out"], dest)
got = {f["path"]: f["sha256"] for f in files}
T.check("V10 fetch: sha256 identical", got and all(ad.sha256_file(dest / p) == s for p, s in got.items()), str(got))
T.check("V10b fetched content", (dest / "out" / "size.txt").read_text().strip() == "5000")
job2 = {"id": "t2", "manifest": str(work / "manifest.txt")}
res2 = a.sync(job2, build)
T.check("V11 second send: no asset retransmitted", res2["missing"] == [], str(res2))
a.clean(job)
a.clean(job2)
T.check("V12 clean leaves no folder", not jd.exists() and not (remote_home / "claude-work" / "jobs" / "t2").exists())

# cancel di un lavoro lungo
job3 = {"id": "t3"}
(work / "manifest.txt").write_text("")
job3["manifest"] = None


def build_long(missing):
    with tarfile.open(work / "src.tar", "w") as t:
        t.add(srcdir / "hello.txt", "hello.txt")
    (work / "cmd.txt").write_text("sleep 60\n")
    with tarfile.open(work / "bundle.tar", "w") as t:
        for f in ("src.tar", "cmd.txt"):
            t.add(work / f, f)
    return work / "bundle.tar"


a.sync(job3, build_long)
a.detach(job3)
time.sleep(1.5)
a.cancel(job3)
time.sleep(1)
s = (a.status()["jobs"].get("t3") or {}).get("status") or {}
T.check("V13 cancel stops the tree", s.get("state") == "cancelled", str(s))
a.clean(job3)

loc = ad.adapter_for("local", {}, C)
T.check("V14 local adapter", type(loc).__name__ == "LocalPosix" and not loc.remote and loc.helper_ok()[0])

# ------------------------------------------------------------------ X: trasporto
os.environ["FAKE_SSH_DOWN"] = "1"
t0 = time.time()
try:
    a.status()
    err = None
except ad.HostError as e:
    err = e.code
T.check("X1 host down: unreachable in < 5 s", err == "unreachable" and time.time() - t0 < 5, f"{err} {time.time() - t0:.1f}s")
os.environ.pop("FAKE_SSH_DOWN")
os.environ["FAKE_SSH_HOSTKEY"] = "bad"
try:
    a.status()
    err = None
except ad.HostError as e:
    err = e.code
T.check("X2 changed host key: fails closed", err == "hostkey", str(err))
os.environ.pop("FAKE_SSH_HOSTKEY")
lines = [json.loads(x) for x in (tmp / "ssh.log").read_text().splitlines()]
flat = " ".join(" ".join(x) for x in lines)
T.check("X3 ssh options: BatchMode, pinned known_hosts, strict, no ~/.ssh/config edits",
        "BatchMode=yes" in flat and f"UserKnownHostsFile={state}/known_hosts" in flat
        and "StrictHostKeyChecking=yes" in flat and "-F" not in flat.split(), flat[:200])

# ------------------------------------------------------------------ O: onboarding
import shutil  # noqa: E402
shutil.rmtree(remote_home / ".claude-master-remote")
r = run("cm-hosts.py", "add", "box", "--ssh", "fakebox", "--no-bench", "--no-webgl")
T.check("O1 add without --yes and without a terminal: no remote write", r.returncode == 0
        and not (remote_home / ".claude-master-remote").exists() and "host update box" in r.stdout, r.stdout + r.stderr)
cfg_now = json.loads(cfgf.read_text())
# il remoto finto e' questa macchina: dentro WSL (le suite su win) cm-remote.sh dice giustamente «wsl»
IN_WSL = "microsoft" in (Path("/proc/version").read_text().lower() if Path("/proc/version").is_file() else "")
T.check("O2 host declared in config, kind detected", cfg_now["hosts"]["box"]["kind"] == ("wsl" if IN_WSL else "linux-tmux")
        and cfg_now["hosts"]["box"]["transport"] == {"ssh": "fakebox"}, str(cfg_now.get("hosts")))
T.check("O2b first contact used accept-new", "StrictHostKeyChecking=accept-new" in (tmp / "ssh.log").read_text())
r = run("cm-hosts.py", "update", "box", "--yes")
T.check("O3 update --yes installs the helper", r.returncode == 0 and (remote_home / ".claude-master-remote" / "cm-remote.sh").exists(), r.stdout + r.stderr)
r = run("cm-hosts.py", "doctor", "box", "--no-bench", "--no-webgl")
cfg_now = json.loads(cfgf.read_text())
m = cfg_now["hosts"]["box"].get("measured") or {}
T.check("O4 doctor writes measured", m.get("threads") and m.get("at") and "bandwidth_mb_s" in m and m.get("helper"), r.stdout + r.stderr)
T.check("O5 doctor prints a proposal and asks for confirm", "host confirm box" in r.stdout, r.stdout)
r = run("cm-hosts.py", "list")
T.check("O6 list: box to confirm", "box" in r.stdout and "da confermare" in r.stdout, r.stdout)
r = run("cm-hosts.py", "confirm", "box", "--secrets")
T.check("O7 --secrets without full: refused", r.returncode == 2 and "full" in r.stderr, r.stderr)
r = run("cm-hosts.py", "confirm", "box", "--roles", "compute", "--limits", "heavy=1")
cfg_now = json.loads(cfgf.read_text())
hb = cfg_now["hosts"]["box"]
T.check("O8 confirm writes roles, limits, trust work without secrets/clients", r.returncode == 0 and hb["roles"] == ["compute"]
        and hb["limits"]["heavy"] == 1 and hb["trust"]["level"] == "work" and hb["trust"]["secrets"] is False
        and hb["trust"]["clients"] is False and hb.get("confirmed_at"), json.dumps(hb)[:300])
T.check("O8b trust accounts proposed from the host's logins", hb["trust"]["accounts"] == ["personale"], str(hb["trust"]))
r = run("cm-hosts.py", "list")
T.check("O9 list: confirmed", "confermato" in r.stdout, r.stdout)


def run_in(stdin, *args):
    e = dict(ENV, CM_HOSTS_TTY="1")
    return subprocess.run([sys.executable, str(T.SCRIPTS / args[0])] + list(args[1:]), capture_output=True, text=True,
                          env=e, input=stdin, timeout=120)


raw = json.loads(cfgf.read_text())
raw["hosts"]["box"].pop("confirmed_at")
cfgf.write_text(json.dumps(raw))
r = run_in("n\n", "cm-hosts.py", "doctor", "box", "--no-bench", "--no-webgl")
T.check("O9b interactive: «no» leaves it to confirm", "non confermato" in r.stdout
        and not json.loads(cfgf.read_text())["hosts"]["box"].get("confirmed_at"), r.stdout[-300:])
r = run_in("m\ncompute\nheavy=1\n\n", "cm-hosts.py", "doctor", "box", "--no-bench", "--no-webgl")
hb = json.loads(cfgf.read_text())["hosts"]["box"]
T.check("O9c interactive: «edit» takes roles and limits", hb.get("confirmed_at") and hb["roles"] == ["compute"]
        and hb["limits"]["heavy"] == 1, r.stdout[-300:])
t0 = time.time()
r = run("cm-hosts.py", "doctor", "box", env={"FAKE_SSH_DOWN": "1"})
T.check("O10 doctor on a host that is off: exit ≠ 0 in < 5 s", r.returncode != 0 and time.time() - t0 < 5
        and "non raggiungibile" in r.stderr, f"{r.returncode} {time.time() - t0:.1f}s {r.stderr}")
r = run("cm-hosts.py", "doctor", "box", env={"FAKE_SSH_HOSTKEY": "bad"})
T.check("O11 doctor with a changed key: fails closed and says so", r.returncode != 0 and "chiave" in r.stderr, r.stderr)
r = run("cm-hosts.py", "poll", "--force")
snap = json.loads((state / "hosts" / "box.json").read_text())
T.check("O12 poll writes the snapshot with the read time", snap.get("status", {}).get("ok") and snap.get("read_at"), str(snap)[:200])
r = run("cm-hosts.py", "poll", "--force", env={"FAKE_SSH_DOWN": "1"})
r = run("cm-hosts.py", "list")
T.check("O13 host off: «non raggiungibile» with the age of the last good read", "non raggiungibile" in r.stdout, r.stdout)

# ------------------------------------------------------------------ R: ruoli, limiti, fiducia
hm = load("cm-hosts")
lm = {"bench": {"runtime": "node v24.1", "cpu_multi_raw": 1000, "cpu_single_raw": 500}, "webgl": {"hardware": False},
      "threads": 8, "ram_gb": 6.6}
wm = {"bench": {"runtime": "node v24.9", "cpu_multi_raw": 1700, "cpu_single_raw": 450}, "threads": 8, "ram_gb": 16,
      "webgl": {"hardware": True, "ms": 3.1}, "tools": {"ffmpeg": "9.0", "claude": "2.1"}, "accounts": [".claude"],
      "interactive_desktop": True}
T.check("R1 roles from measures: compute, render, sessions", hm.derive_roles(wm, lm, "windows-native") == ["compute", "render", "sessions"])
slow = dict(wm, bench={"runtime": "node v24", "cpu_multi_raw": 600}, webgl={"hardware": False})
T.check("R2 slower than the control machine, software WebGL: no compute, no render", hm.derive_roles(slow, lm, "windows-native") == ["sessions"])
T.check("R3 different runtimes are not compared", hm.rel_bench({"bench": {"runtime": "python 3", "cpu_multi_raw": 9}}, lm, "multi") is None)
lim, formula = hm.derive_limits(wm, "win")
T.check("R4 limits of the plan's appendix: heavy 2, render 1, sessions 6", lim == {"heavy": 2, "render": 1, "sessions": 6}, str(lim))
lim_l, _ = hm.derive_limits(lm, "local")
T.check("R5 local reserves room for the live sessions", lim_l["heavy"] == 1 and "heavy" in formula, str(lim_l))
T.check("R6 Windows 11 21H2 is past its end of support", hm.eos({"os_build": 22000})[1] is True and hm.eos({"os_build": 99999}) == (None, False))

# ------------------------------------------------------------------ P: sondatore, sessions, quota (1.6)
import hashlib  # noqa: E402
raw = json.loads(cfgf.read_text())
raw["hosts"]["pc"] = {"kind": "windows-native", "transport": {"ssh": "pc"}, "confirmed_at": "x",
                      "measured": {"home": "C:\\Users\\u"}}
cfgf.write_text(json.dumps(raw))
qh = hashlib.sha256("C:\\Users\\u\\.claude".encode()).hexdigest()[:8]
now_s = time.time()
snap = {"name": "pc", "read_at": now_s - 20, "last_ok_at": now_s - 20, "status": {
    "ok": True, "load_pct": 12, "ram_gb": 16, "ram_free_gb": 9, "disk_free_gb": 20,
    "jobs": {"j1": {"status": {"state": "running"}, "tail": "Rendered 447/2517"}},
    "sessions": [{"config_dir": ".claude", "alive": True, "entry": {"pid": 77, "name": "lavoro", "status": "idle",
                  "cwd": "D:\\w", "sessionId": "abc", "bridgeSessionId": "session_x"}},
                 {"config_dir": ".claude", "alive": False, "entry": {"pid": 78, "name": "morta"}}],
    "quota": [{"file": f"quota-{qh}.json", "mtime": now_s - 30, "data": {"five_hour_used_pct": 11.0, "weekly_used_pct": 55.0,
               "weekly_resets_at": now_s + 86400}}]}}
(state / "hosts" / "pc.json").write_text(json.dumps(snap))
(home / ".claude" / "fable-director").mkdir(parents=True, exist_ok=True)
lq = home / ".claude" / "fable-director" / f"quota-{hashlib.sha256(str(home / '.claude').encode()).hexdigest()[:8]}.json"
lq.write_text(json.dumps({"five_hour_used_pct": 1.0, "weekly_used_pct": 40.0, "weekly_resets_at": now_s + 86400}))
os.utime(lq, (now_s - 7200, now_s - 7200))
qenv = {"CM_PROC_SCAN_PIDS": "", "TMUX_TMPDIR": str(tmp / "notmux")}
raw["quota"] = {"source": str(home / ".claude" / "fable-director")}
cfgf.write_text(json.dumps(raw))
t0 = time.time()
r = run("cm-sessions.py", "--no-screen", env=qenv)
dt_s = time.time() - t0
T.check("P1 sessions: remote row HOST:name, dead ones left out", "pc:lavoro" in r.stdout and "pc:morta" not in r.stdout, r.stdout + r.stderr)
T.check("P2 sessions: one line per host with load, RAM, job progress", "pc: carico 12 %" in r.stdout and "447/2517" in r.stdout, r.stdout)
snap["error"], snap["read_at"], snap["last_ok_at"] = "unreachable", now_s - 5, now_s - 600
(state / "hosts" / "pc.json").write_text(json.dumps(snap))
t0 = time.time()
r = run("cm-sessions.py", "--no-screen", env=qenv)
dt_s = max(dt_s, time.time() - t0)
T.check("P3 host off: «non raggiungibile, … 10 min fa», no transport", "pc: non raggiungibile" in r.stdout and "10 min fa" in r.stdout, r.stdout)
T.check("P4 sessions in < 2 s", dt_s < 2, f"{dt_s:.2f}s")
r = run("cm-quota.py", "--no-screen", env=qenv)
T.check("P5 quota: the freshest reading wins, with its provenance", "55%" in r.stdout and "(da pc)" in r.stdout, r.stdout + r.stderr)
T.check("P6 quota: consumers per host", "1 su pc" in r.stdout, r.stdout)
r = run("cm-sessions.py", "--json", "--no-screen", env=qenv)
rows = json.loads(r.stdout)
T.check("P7 sessions --json carries the remote row with host", any(x.get("host") == "pc" and x["name"] == "pc:lavoro" for x in rows))
cron = tmp / "crontab"
cron.write_text("")
fc = tmp / "crontab.sh"
fc.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (cron, cron, cron, cron))
fc.chmod(0o755)
run("cm-hosts.py", "install", env={"CM_CRONTAB_CMD": str(fc)})
r = run("cm-hosts.py", "install", env={"CM_CRONTAB_CMD": str(fc)})
T.check("P8 hosts install writes one poller line", (tmp / "crontab").read_text().count("hosts poll --cron") == 1, r.stdout + r.stderr)
_before = (tmp / "crontab").read_text()
r = run("cm-hosts.py", "uninstall", "--help", env={"CM_CRONTAB_CMD": str(fc)})
T.check("P8 (09/10) hosts uninstall --help (any argument) → exit 2 with the usage, crontab untouched", r.returncode == 2 and (tmp / "crontab").read_text() == _before, r.stdout + r.stderr)
run("cm-hosts.py", "uninstall", env={"CM_CRONTAB_CMD": str(fc)})
r = run("cm-hosts.py", "install", "--help", env={"CM_CRONTAB_CMD": str(fc)})
T.check("P8 (09/10) hosts install --help → exit 2, nothing installed", r.returncode == 2 and "hosts poll" not in (tmp / "crontab").read_text(), r.stdout + r.stderr)

T.rm(tmp)
T.finish()
