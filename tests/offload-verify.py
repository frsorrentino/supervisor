#!/usr/bin/env python3
"""Verifica di scheduler, prenotazioni e offload (piano multi-PC v1, passi 1.3-1.5), con un host finto «box»
servito da tests/lib/fake-ssh.py (la sua «macchina» e' una HOME temporanea) e misure scritte a mano.

  S1-S7   scelta dello scheduler: pesante con guadagno > costo, copia troppo cara, leggero, durata sconosciuta,
          regia senza il requisito, limite pieno → coda, --host con limite pieno → coda
  F1-F5   fiducia: segreti verso host senza segreti (anche con --host, regola citata), host non confermato, clienti
  E1-E9   offload vero: .env e segreti non partono, asset non ritrasmessi, fetch con sha256, riepilogo, clean
  W1      wait esce da solo dopo --max-min (exit 4, «rilancia»): il limite dei comandi in background di Claude Code
  N1-N2   night: limite della regia pieno → voce in coda con il motivo
  L1      heavy run in locale: prenotazione presa e rilasciata
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

LIB = Path(__file__).resolve().parent / "lib"
tmp = Path(T.tmpdir())
home, rh, state = tmp / "home", tmp / "rh", tmp / "st"
for d in (home / ".claude", rh / ".claude", state):
    d.mkdir(parents=True)
proj = home / "ws" / "film"
proj.mkdir(parents=True)
cfgf = tmp / "config.json"
LOCAL_M = {"threads": 8, "ram_gb": 6.6, "tools": {"node": "24.1", "ffmpeg": "5.1", "tmux": "3.3"},
           "bench": {"runtime": "node v24.1", "cpu_multi_raw": 1000, "cpu_single_raw": 900}, "webgl": None, "at": "2026-09-27T20:00"}
BOX_M = {"threads": 8, "ram_gb": 16, "tools": {"node": "24.19", "ffmpeg": "9.0"}, "kind": "linux",
         "bench": {"runtime": "node v24.19", "cpu_multi_raw": 2000, "cpu_single_raw": 800},
         "webgl": {"hardware": True, "ms": 3.0, "renderer": "ANGLE (fake)"}, "bandwidth_mb_s": 20, "at": "2026-09-27T20:00"}
BOX = {"kind": "linux-tmux", "transport": {"ssh": "box"}, "remote_root": str(rh / "claude-work"), "measured": BOX_M,
       "roles": ["compute", "render"], "limits": {"heavy": 1, "render": 1, "sessions": 2},
       "trust": {"level": "work", "accounts": ["personale"], "secrets": False, "clients": False}, "confirmed_at": "2026-09-27T20:01"}
CFG = {"language": "it", "state_dir": str(state), "accounts": {"personale": {"config_dir": "~/.claude"}},
       "scheduler": {"remote_root": {"posix": str(tmp / "localwork")}, "client_paths": ["~/ws/clienti"]},
       "hosts": {"local": {"measured": LOCAL_M, "limits": {"heavy": 1, "render": 0, "sessions": 5}}, "box": BOX}}


def write_cfg():
    cfgf.write_text(json.dumps(CFG))


write_cfg()
ENV = dict(os.environ, HOME=str(home), CM_HOME=str(home), CC_SUPERVISOR_CONFIG=str(cfgf),
           CM_SSH_BIN=str(LIB / "fake-ssh.py"), CM_SCP_BIN=str(LIB / "fake-scp.py"), FAKE_SSH_HOME=str(rh),
           CM_HOSTS_TTY="0", CM_FAKE_LOAD_PCT="10", CM_FAKE_RAM_KB="16777216 8388608", CM_NIGHT_FREE_MB="4000", CM_FAKE_LOCAL_STATUS=json.dumps({"load_pct": 10, "ram_free_gb": 3, "threads": 8}),
           FAKE_SCP_LOG=str(tmp / "scp.log"), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@t")
ENV.pop("CLAUDE_CONFIG_DIR", None)


def run(*args, env=None, timeout=180):
    return subprocess.run([sys.executable, str(T.SCRIPTS / args[0])] + list(args[1:]), capture_output=True, text=True,
                          env=dict(ENV, **(env or {})), timeout=timeout)


def g(*a):
    subprocess.run(["git", "-C", str(proj), *a], check=True, capture_output=True, env=ENV)


# un progetto come il film: sorgente, asset ignorati, un .env tracciato per errore, uscita ignorata
(proj / "src").mkdir()
(proj / "src" / "main.txt").write_text("film\n")
(proj / ".env").write_text("TOKEN=abc\n")
(proj / "public" / "audio").mkdir(parents=True)
(proj / "public" / "audio" / "a.bin").write_bytes(os.urandom(200000))
(proj / "public" / "audio" / "b.bin").write_bytes(os.urandom(100000))
(proj / ".gitignore").write_text("public/audio/\nout/\n")
recipes = {"recipes": {
    "render": {"needs": {"role": "render", "gpu": "webgl-hardware", "tools": {"ffmpeg": "*"}},
               "files": {"assets": ["public/audio/**"], "out": ["out"]},
               "cmd": {"default": "mkdir -p out && ls -a > out/files.txt && cat public/audio/a.bin > out/a.copy && echo {threads} > out/threads.txt"},
               "est_local_s": 240},
    "build": {"needs": {"role": "compute"}, "files": {"out": ["out"]}, "cmd": "mkdir -p out && echo built > out/b.txt", "est_local_s": 240},
    "big": {"needs": {"role": "compute"}, "files": {"assets": ["public/audio/**"], "out": ["out"]}, "cmd": "true",
            "est_local_s": 20},
    "light": {"needs": {}, "cmd": "echo hi", "est_local_s": 5},
    "nodur": {"needs": {"role": "compute"}, "cmd": "true"},
    "secret": {"needs": {"role": "compute", "secrets": True}, "cmd": "true", "est_local_s": 300}}}
(proj / ".cm-offload.json").write_text(json.dumps(recipes))
subprocess.run(["git", "init", "-q", str(proj)], check=True)
g("add", "-A")
g("commit", "-qm", "init")

# prima un giro del sondatore, per la lettura fresca di box
r = run("cm-hosts.py", "poll", "--force")
r0 = run("cm-hosts.py", "update", "box", "--yes")
T.check("S0 box helper installed", r0.returncode == 0, r0.stdout + r0.stderr)


def explain(recipe, *extra):
    r = run("cm-offload.py", "offload", str(proj), "--recipe", recipe, "--explain", "--dry-run", *extra)
    return r


r = explain("build")
T.check("S1 heavy job, control machine free: goes to box, gain > cost", "scelta: run su box" in r.stdout
        and "guadagnare" in r.stdout, r.stdout + r.stderr)
T.check("S1b --explain prints the table", "guadagno" in r.stdout and "margine" in r.stdout and "local" in r.stdout, r.stdout)
CFG["hosts"]["box"]["measured"] = dict(BOX_M, bandwidth_mb_s=0.01)
write_cfg()
r = explain("big")
T.check("S2 huge copy on a slow link: stays here", "scelta: run su local" in r.stdout and "copia" in r.stdout, r.stdout + r.stderr)
CFG["hosts"]["box"]["measured"] = BOX_M
write_cfg()
r = explain("light")
T.check("S3 light job with a free control machine: stays here", "scelta: run su local" in r.stdout and "leggero" in r.stdout, r.stdout)
r = explain("nodur")
T.check("S4 unknown duration: stays here with the reason", "scelta: run su local" in r.stdout and "durata sconosciuta" in r.stdout, r.stdout)
r = explain("render")
T.check("S5 render needs the render role (hardware WebGL): control machine excluded, box chosen",
        "scelta: run su box" in r.stdout and "non ha il ruolo render" in r.stdout and "requisiti" in r.stdout, r.stdout)

# ------------------------------------------------------------------ fiducia
r = explain("secret")
T.check("F1 secrets job: box refused with the rule, stays on the control machine",
        "trust.secrets=false" in r.stdout and "scelta: run su local" in r.stdout, r.stdout)
r = run("cm-offload.py", "offload", str(proj), "--recipe", "secret", "--host", "box")
T.check("F2 secrets job with --host box: refused, rule cited", r.returncode == 6 and "trust.secrets=false" in r.stderr, r.stdout + r.stderr)
CFG["hosts"]["box"].pop("confirmed_at")
write_cfg()
r = explain("build")
T.check("F3 unconfirmed host gets no work", "non e' confermato" in r.stdout and "scelta: run su local" in r.stdout, r.stdout)
CFG["hosts"]["box"]["confirmed_at"] = "2026-09-27T20:01"
write_cfg()
cl = home / "ws" / "clienti" / "sito"
cl.mkdir(parents=True)
subprocess.run(["git", "init", "-q", str(cl)], check=True)
(cl / "f.txt").write_text("x")
subprocess.run(["git", "-C", str(cl), "add", "-A"], check=True, env=ENV)
subprocess.run(["git", "-C", str(cl), "commit", "-qm", "i"], check=True, env=ENV)
r = run("cm-offload.py", "offload", str(cl), "--needs", "role=compute", "--est", "300", "--host", "box", "--dry-run", "--", "true")
T.check("F4 client folder: box refused even with --host", r.returncode == 6 and "trust.clients=false" in r.stderr, r.stdout + r.stderr)
(proj / "src" / "key.txt").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nxx\n")
g("add", "-A")
g("commit", "-qm", "oops")
r = run("cm-offload.py", "offload", str(proj), "--recipe", "build", "--dry-run")
T.check("F5 a private key in the commit blocks the send, citing the file", r.returncode == 5 and "src/key.txt" in r.stderr, r.stderr)
g("rm", "-q", "src/key.txt")
g("commit", "-qm", "fix")

# ------------------------------------------------------------------ offload vero
r = run("cm-offload.py", "offload", str(proj), "--recipe", "render")
T.check("E1 render offload starts on box", r.returncode == 0 and "partito su box" in r.stdout, r.stdout + r.stderr)
jid = r.stdout.split()[0] if r.returncode == 0 else ""
T.check("E2 first send carries both assets", "2 asset nuovi" in r.stdout, r.stdout)
r = run("cm-offload.py", "offload", "wait", jid, timeout=120)
T.check("E3 wait: done", r.returncode == 0 and "done" in r.stdout, r.stdout + r.stderr)
files = (proj / "out" / "files.txt").read_text() if (proj / "out" / "files.txt").exists() else ""
T.check("E4 .env did not leave", files and ".env" not in files and "src" in files, files)
T.check("E5 output fetched into the ignored out/ with sha256 identical",
        (proj / "out" / "a.copy").read_bytes() == (proj / "public" / "audio" / "a.bin").read_bytes())
T.check("E5b {threads} took the host's value", (proj / "out" / "threads.txt").read_text().strip() == "8")
summ = proj / f"offload-{jid}.json"
T.check("E6 summary next to the results", summ.exists() and json.loads(summ.read_text()).get("outputs"), str(summ))
before = (tmp / "scp.log").read_text().count("\n")
r = run("cm-offload.py", "offload", str(proj), "--recipe", "render")
jid2 = r.stdout.split()[0] if r.returncode == 0 else ""
T.check("E7 second send: no asset retransmitted", "0 asset nuovi" in r.stdout, r.stdout + r.stderr)
run("cm-offload.py", "offload", "wait", jid2, timeout=120)
log = [json.loads(x) for x in (state / "scheduler.log").read_text().splitlines()]
T.check("E8 scheduler.log: decision and done with the real duration", any(x["event"] == "decision" for x in log)
        and any(x["event"] == "done" and x.get("seconds") is not None for x in log))
for j in (jid, jid2):
    run("cm-offload.py", "offload", "clean", j)
T.check("E9 clean leaves no folder on the host", not any((rh / "claude-work" / "jobs").iterdir()),
        str(list((rh / "claude-work" / "jobs").iterdir())))

# ------------------------------------------------------------------ coda e limiti
leases = state / "heavy" / "leases.json"
leases.parent.mkdir(parents=True, exist_ok=True)
sleeper = subprocess.Popen(["sleep", "600"])   # vivo fino a N1 anche con la macchina carica: lo si uccide dopo
leases.write_text(json.dumps({"leases": [{"pid": sleeper.pid, "host": "box", "cls": "heavy", "since": 0},
                                         {"pid": sleeper.pid, "host": "local", "cls": "heavy", "since": 0}]}))
r = explain("build")
T.check("S6 limits full everywhere: the job goes in the queue", "scelta: queue" in r.stdout and "coda" in r.stdout, r.stdout)
r = explain("build", "--host", "box")
T.check("S7 --host on a full host: queued there", "scelta: queue su box" in r.stdout, r.stdout)
# W1 (Claude Code 2.1.285: un comando in background si ferma al suo timeout, 2 ore al massimo): wait non aspetta per
# sempre, esce da solo con exit 4 e dice di rilanciarlo
r = run("cm-offload.py", "offload", str(proj), "--recipe", "build", "--host", "box")
jq = r.stdout.split()[0] if r.returncode == 0 else ""
t0 = time.time()
r = run("cm-offload.py", "offload", "wait", jq, "--max-min", "0.01", timeout=60)
T.check("W1 wait past --max-min on a queued job → exit 4, «ancora in corso, rilancia» with the command, in seconds",
        jq and r.returncode == 4 and "ancora in corso" in r.stdout and f"offload wait {jq}" in r.stdout and time.time() - t0 < 30, r.stdout + r.stderr)
run("cm-offload.py", "offload", "cancel", jq)
r = run("cm-night.py", "add", str(proj), "fai qualcosa")
r = run("cm-night.py", "run", "--dry-run")
T.check("N1 night with the local heavy limit full: item stays, reason given", "pieno" in r.stdout, r.stdout + r.stderr)
sleeper.kill()
sleeper.wait()
r = run("cm-night.py", "run", "--dry-run")
T.check("N2 stale lease (dead pid) reclaimed: night item would run", "pieno" not in r.stdout, r.stdout)
leases.write_text(json.dumps({"leases": []}))
r = run("cm-offload.py", "heavy", "run", "--dir", str(proj), "--", "sh", "-c", f"cat {leases} > {tmp / 'during.json'}")
during = json.loads((tmp / "during.json").read_text())
T.check("L1 heavy run stays here (unknown duration) inside a lease, released after",
        r.returncode == 0 and any(l["host"] == "local" for l in during["leases"]) and json.loads(leases.read_text())["leases"] == [],
        r.stdout + r.stderr + json.dumps(during))

T.rm(tmp)
T.finish()
