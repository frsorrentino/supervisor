#!/usr/bin/env python3
"""cm-proc: processi e misure di sistema su Linux e macOS (11/11/2026, supervisor-macos-11-11: su un Mac nessuna sessione
risultava viva, perche' supervisor leggeva /proc).

PR1 Linux (/proc), sul pid di questo test e su un figlio: vivo, segno d'avvio = campo 22 di stat, comando, nome, cartella,
    padre, ambiente, figli; un pid che non c'e' non e' vivo; uno zombie non e' vivo; carico, memoria, avvio della macchina
PR2 macOS (CM_PROC_PLATFORM=darwin, ps/lsof/sysctl/vm_stat finti con l'uscita di macOS): le stesse domande con le risposte
    di quei comandi; il segno d'avvio non si confronta con procStart; memoria da hw.memsize e vm_stat; boot_id dall'avvio
PR3 cm-sessions: una voce del registro con un procStart che su macOS non si puo' confrontare resta, se il processo e' vivo;
    su Linux un procStart diverso la scarta (pid riusato)
PR4 cm-system su macOS: carico e memoria da cm-proc, i processi da `ps` (cpu, rss, eta')
PR5 doctor: la riga processes_ok sulla piattaforma
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())


def load(name, tag):
    sp = importlib.util.spec_from_file_location(tag, T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


P = load("cm-proc", "cm_proc_t")

# PR1 — Linux
os.environ.pop("CM_PROC_PLATFORM", None)
me = os.getpid()
stat = Path(f"/proc/{me}/stat").read_text()
child = subprocess.Popen(["sleep", "30"], cwd=str(tmp), env=dict(os.environ, CM_PROVA_PROC="si"))
time.sleep(0.3)
T.check("PR1 Linux: this pid alive, start = field 22 of stat, command, name, folder, parent",
        P.platform() == "linux" and P.alive(me) and P.start_token(me) == stat[stat.rindex(")") + 2:].split()[19]
        and "proc-verify" in P.cmdline(me) and P.comm(me).startswith("python") and P.cwd(me) == os.getcwd() and P.ppid(me) == os.getppid(),
        json.dumps({"start": P.start_token(me), "comm": P.comm(me), "cwd": P.cwd(me), "ppid": P.ppid(me)}))
T.check("PR1 Linux: a child — its folder, its environment, listed among our children and among all pids",
        P.cwd(child.pid) == str(tmp) and P.environ(child.pid).get("CM_PROVA_PROC") == "si" and (child.pid, "sleep") in P.children(me)
        and child.pid in P.pids() and P.argv(child.pid) == ["sleep", "30"], str(P.children(me)))
child.kill()
time.sleep(0.2)
zombie_alive = P.alive(child.pid)   # non ancora raccolto: zombie
child.wait()
T.check("PR1 Linux: a zombie is not alive; a pid that is gone neither; a bad pid is no error",
        not zombie_alive and not P.alive(child.pid) and not P.alive(0) and not P.alive("x") and P.cmdline(child.pid) == "", "")
tot, avail = P.memory()
T.check("PR1 Linux: load, memory (total and available in MB), boot time and boot id",
        P.loadavg() is not None and tot and avail and 0 < avail <= tot and P.boot_time() and P.boot_time() < time.time() and len(P.boot_id()) > 8,
        f"{P.loadavg()} {tot} {avail} {P.boot_time()} {P.boot_id()}")

# PR2 — macOS con i comandi finti
fake = tmp / "macbin"
fake.mkdir()
(fake / "ps").write_text(r'''#!/bin/sh
# ps di macOS, finto: -o <campo>= -p <pid>, -axo pid=, -axo pid=,ppid=,comm=, -E (ambiente dopo il comando)
args="$*"
case "$args" in
  *"-axo pid=,ppid=,comm="*) printf '  1     0 /sbin/launchd\n 4242  1 /usr/local/bin/claude\n 4343 4242 /bin/zsh\n'; exit 0 ;;
  *"-axww -o pid=,ppid=,etime=,%cpu=,rss=,stat=,command="*)
    printf ' 4242     1    01:02:03  12,5  204800 S    /usr/local/bin/claude --remote-control master -n master\n'
    printf ' 4343  4242 2-03:04:05   0,0    1024 Z    (zsh)\n'; exit 0 ;;
  *"-axo pid="*) printf '    1\n 4242\n 4343\n'; exit 0 ;;
esac
pid=""; for a in "$@"; do [ "$prev" = "-p" ] && pid="$a"; prev="$a"; done
[ "$pid" = 4242 ] || [ "$pid" = 4343 ] || exit 1
case "$args" in
  *"stat="*) [ "$pid" = 4343 ] && echo "Z" || echo "Ss" ;;
  *"lstart="*) echo "Tue Nov 11 09:00:00 2026" ;;
  *"-E"*) echo "/usr/local/bin/claude --remote-control master HOME=/Users/prova CLAUDE_CONFIG_DIR=/Users/prova/.claude-work TERM=xterm" ;;
  *"command="*) echo "/usr/local/bin/claude --remote-control master -n master" ;;
  *"comm="*) echo "/usr/local/bin/claude" ;;
  *"ppid="*) echo "    1" ;;
esac
''')
(fake / "lsof").write_text('#!/bin/sh\nprintf "p4242\\nfcwd\\nn/Users/prova/progetti\\n"\n')
(fake / "sysctl").write_text('#!/bin/sh\ncase "$*" in *hw.memsize*) echo 17179869184 ;; *kern.boottime*) echo "{ sec = 1794380000, usec = 0 } Tue Nov 11 08:13:20 2026" ;; *vm.loadavg*) echo "{ 1.50 1.20 1.00 }" ;; esac\n')
(fake / "vm_stat").write_text('#!/bin/sh\nprintf "Mach Virtual Memory Statistics: (page size of 16384 bytes)\\nPages free:                               50000.\\nPages active:                            200000.\\nPages inactive:                          100000.\\nPages speculative:                        10000.\\n"\n')
for f in fake.iterdir():
    f.chmod(0o755)
os.environ["CM_PROC_PLATFORM"] = "darwin"
os.environ["PATH"] = f"{fake}:{os.environ['PATH']}"
os.kill = (lambda real: (lambda pid, sig: None if pid in (4242, 4343) else real(pid, sig)))(os.kill)   # i pid finti «ci sono»
T.check("PR2 macOS: alive from kill -0 and ps stat (4343 is a zombie), start from lstart, not comparable with procStart",
        P.mac() and P.alive(4242) and not P.alive(4343) and P.start_token(4242) == "Tue Nov 11 09:00:00 2026" and not P.comparable_start(), "")
T.check("PR2 macOS: command, name, executable, folder (lsof), parent, environment (ps -E), pids, children",
        P.cmdline(4242) == "/usr/local/bin/claude --remote-control master -n master" and P.comm(4242) == "claude" and P.exe(4242) == "/usr/local/bin/claude"
        and P.cwd(4242) == "/Users/prova/progetti" and P.ppid(4242) == 1 and P.environ(4242).get("CLAUDE_CONFIG_DIR") == "/Users/prova/.claude-work"
        and P.pids() == [1, 4242, 4343] and P.children(4242) == [(4343, "zsh")], json.dumps({"cwd": P.cwd(4242), "env": P.environ(4242), "ch": P.children(4242)}))
tot_m, av_m = P.memory()
T.check("PR2 macOS: memory from hw.memsize (16 GB) and vm_stat (free + inactive + speculative pages of 16 KB), boot from kern.boottime",
        tot_m == 16384 and av_m == (50000 + 100000 + 10000) * 16384 // (1024 * 1024) and P.boot_time() == 1794380000 and P.boot_id() == "boot-1794380000",
        f"{tot_m} {av_m} {P.boot_time()}")

# PR3 — cm-sessions sul registro
home = tmp / "home"
(home / ".claude" / "sessions").mkdir(parents=True)
cfg = tmp / "c.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "s"), "accounts": {"p": {"config_dir": str(home / ".claude")}},
                           "default_account": "p", "workspace": {"root": str(home)}}))
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home)})
(home / ".claude" / "sessions" / "4242.json").write_text(json.dumps({"pid": 4242, "sessionId": "s1", "name": "master", "procStart": "123456"}))
SS = load("cm-sessions", "cm_sessions_mac")
mac_entries = [d["pid"] for d in SS.registry_entries()]
os.environ["CM_PROC_PLATFORM"] = "linux"
real_child = subprocess.Popen(["sleep", "30"])
time.sleep(0.2)
(home / ".claude" / "sessions" / "4242.json").unlink()
(home / ".claude" / "sessions" / f"{real_child.pid}.json").write_text(json.dumps({"pid": real_child.pid, "sessionId": "s2", "name": "x", "procStart": "1"}))
lin_bad = [d["pid"] for d in SS.registry_entries()]
(home / ".claude" / "sessions" / f"{real_child.pid}.json").write_text(json.dumps({"pid": real_child.pid, "sessionId": "s2", "name": "x",
                                                                                  "procStart": P.start_token(real_child.pid)}))
lin_ok = [d["pid"] for d in SS.registry_entries()]
real_child.kill(); real_child.wait()
T.check("PR3 cm-sessions: on macOS a registry entry of a live process stays although procStart cannot be compared; on Linux a different procStart drops it (pid reused), the same keeps it",
        mac_entries == [4242] and lin_bad == [] and lin_ok == [real_child.pid], f"{mac_entries} {lin_bad} {lin_ok}")

# PR4 — cm-system su macOS
os.environ["CM_PROC_PLATFORM"] = "darwin"
SY = load("cm-system", "cm_system_mac")
mm = SY.measure()
rows = SY.proc_rows()
T.check("PR4 cm-system on macOS: load and memory from cm-proc; processes from ps with cpu, rss and age; zombies left out",
        mm["load1"] == P.loadavg() and mm["mem_total_mb"] == 16384 and mm["mem_available_mb"] == av_m
        and [(r["pid"], r["cpu"], r["rss_mb"], r["age_s"]) for r in rows] == [(4242, 12.5, 200, 3723)] and rows[0]["args"].endswith("-n master"),
        f"{mm} {rows}")

# PR5 — doctor
os.environ["CM_PROC_PLATFORM"] = "linux"
CC = load("cm-config", "cm_config_proc")
r5 = CC.processes_row()
os.environ["CM_PROC_PLATFORM"] = "darwin"
r5b = CC.processes_row(pid=4242)
T.check("PR5 doctor: processes_ok on Linux with this pid; on macOS with the fake tools too",
        r5[0] == "PASS" and r5[2]["platform"] == "Linux" and r5b[0] == "PASS" and r5b[2]["platform"] == "macOS", f"{r5} {r5b}")
T.rm(tmp)
T.finish()
