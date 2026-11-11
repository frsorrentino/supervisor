#!/usr/bin/env python3
"""cm-proc — processi e misure di sistema, in un posto solo, per Linux e macOS (11/11/2026, supervisor-macos-11-11: su un
Mac supervisor non vedeva mai una sessione viva, perche' leggeva /proc, che li' non c'e').

Linux (WSL e ChromeOS compresi): /proc. macOS: ps, lsof, sysctl, vm_stat, e quello che Python sa gia' fare ovunque
(os.kill(pid, 0), os.getloadavg()). Ogni funzione torna None, "" o [] quando il dato non si puo' leggere: mai
un'eccezione a chi chiama.

CM_PROC_PLATFORM=darwin|linux forza il ramo (i test del ramo macOS su Linux, con PATH che porta ps/lsof/sysctl finti).
"""
import glob
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def platform():
    p = os.environ.get("CM_PROC_PLATFORM")
    if p in ("darwin", "linux"):
        return p
    return "darwin" if sys.platform == "darwin" else "linux"


def mac():
    return platform() == "darwin"


_SYS_DIRS = ("/usr/sbin", "/sbin", "/usr/bin", "/bin")


def _tool(name):
    """Il comando dal PATH, o dalle cartelle di sistema (lsof e sysctl stanno in /usr/sbin, fuori dal PATH di cron)."""
    import shutil
    found = shutil.which(name)
    if found:
        return found
    for d in _SYS_DIRS:
        if os.access(os.path.join(d, name), os.X_OK):
            return os.path.join(d, name)
    return name


def _run(*cmd, timeout=10):
    cmd = (_tool(cmd[0]),) + tuple(cmd[1:])
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout if p.returncode == 0 else ""


def _ps(pid, field):
    return _run("ps", "-ww", "-o", f"{field}=", "-p", str(int(pid))).strip()


# ------------------------------------------------------------------ un processo
def exists(pid):
    """Il pid c'e' (anche zombie). os.kill(pid, 0): su un pid di un altro utente PermissionError = c'e'."""
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError, OverflowError):
        return False


def zombie(pid):
    if mac():
        return _ps(pid, "stat").startswith("Z")
    try:
        return "\nState:\tZ" in Path(f"/proc/{int(pid)}/status").read_text()
    except (OSError, ValueError):
        return False


def alive(pid):
    """Vivo = c'e' e non e' uno zombie (la regola di `vivo` di cm-restart.sh e cm-launch.sh)."""
    return exists(pid) and not zombie(pid)


def start_token(pid):
    """Un segno dell'avvio del processo, per distinguere un pid riusato: su Linux il campo 22 di /proc/<pid>/stat (tick
    dall'avvio della macchina, lo stesso numero che Claude Code scrive come procStart); su macOS l'istante di `ps -o
    lstart`, che non e' confrontabile con procStart (chi chiama lo sa: comparable_start())."""
    if mac():
        return _ps(pid, "lstart") or None
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
    except (OSError, ValueError):
        return None
    return stat[stat.rindex(")") + 2:].split()[19]


def comparable_start():
    """Il segno d'avvio si puo' confrontare con il procStart del registro di Claude Code? Solo su Linux."""
    return not mac()


def cmdline(pid):
    """La riga di comando, gli argomenti separati da spazi; "" se non si legge."""
    if mac():
        return _ps(pid, "command")
    try:
        return Path(f"/proc/{int(pid)}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except (OSError, ValueError):
        return ""


def argv(pid):
    """Gli argomenti; su macOS spezzati sugli spazi (ps non dice dove finisce un argomento)."""
    if mac():
        return cmdline(pid).split()
    try:
        return [a for a in Path(f"/proc/{int(pid)}/cmdline").read_bytes().decode(errors="replace").split("\0") if a]
    except (OSError, ValueError):
        return []


def comm(pid):
    """Il nome del processo (senza cartella)."""
    if mac():
        return os.path.basename(_ps(pid, "comm"))
    try:
        return Path(f"/proc/{int(pid)}/comm").read_text().strip()
    except (OSError, ValueError):
        return ""


def exe(pid):
    """Il percorso dell'eseguibile; su macOS quello che ps scrive come comm (il percorso completo con cui e' partito)."""
    if mac():
        return _ps(pid, "comm")
    try:
        return os.readlink(f"/proc/{int(pid)}/exe")
    except (OSError, ValueError):
        return ""


def cwd(pid):
    if mac():
        for line in _run("lsof", "-a", "-p", str(int(pid)), "-d", "cwd", "-Fn").splitlines():
            if line.startswith("n"):
                return line[1:]
        return ""
    try:
        return os.readlink(f"/proc/{int(pid)}/cwd")
    except (OSError, ValueError):
        return ""


def ppid(pid):
    if mac():
        v = _ps(pid, "ppid")
        return int(v) if v.isdigit() else None
    try:
        for line in Path(f"/proc/{int(pid)}/status").read_text().splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


_ENV_TOKEN = re.compile(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=(\S*)")


def environ(pid):
    """L'ambiente del processo. Su macOS `ps -E` lo aggiunge dopo la riga di comando, solo per i processi dello stesso
    utente e senza separatori sicuri: si prendono le parole NOME=valore (un valore con spazi resta tagliato). Basta per
    le variabili che servono qui (CLAUDE_CONFIG_DIR, TMUX, …), che non hanno spazi."""
    if mac():
        line = _run("ps", "-E", "-ww", "-o", "command=", "-p", str(int(pid)))
        return {k: v for k, v in _ENV_TOKEN.findall(line)}
    env = {}
    try:
        for kv in Path(f"/proc/{int(pid)}/environ").read_bytes().split(b"\0"):
            k, _, v = kv.partition(b"=")
            if k:
                env[k.decode(errors="replace")] = v.decode(errors="replace")
    except (OSError, ValueError):
        pass
    return env


# ------------------------------------------------------------------ tutti i processi
def pids():
    if mac():
        return [int(x) for x in _run("ps", "-axo", "pid=").split() if x.isdigit()]
    out = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            out.append(int(os.path.basename(d)))
        except ValueError:
            continue
    return out


def cmdlines():
    """{pid: riga di comando} di tutti i processi in una volta: su macOS un solo `ps` invece di uno per pid (centinaia di
    processi: una scansione chiamava ps centinaia di volte, decine di secondi)."""
    if mac():
        out = {}
        for line in _run("ps", "-axww", "-o", "pid=,command=", timeout=20).splitlines():
            parts = line.strip().split(None, 1)
            if parts and parts[0].isdigit():
                out[int(parts[0])] = parts[1] if len(parts) > 1 else ""
        return out
    return {pid: cmdline(pid) for pid in pids()}


def children(pid):
    """[(pid, nome del comando)] dei figli diretti."""
    pid = int(pid)
    if mac():
        out = []
        for line in _run("ps", "-axo", "pid=,ppid=,comm=").splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[1] == str(pid) and parts[0].isdigit():
                out.append((int(parts[0]), os.path.basename(parts[2])))
        return out
    out = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            stat = Path(f"{d}/stat").read_text()
            name = stat[stat.index("(") + 1:stat.rindex(")")]
            if int(stat[stat.rindex(")") + 2:].split()[1]) == pid:
                out.append((int(os.path.basename(d)), name))
        except (OSError, ValueError):
            continue
    return out


# ------------------------------------------------------------------ la macchina
def loadavg():
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return None


def memory():
    """(MB totali, MB ancora disponibili) o (None, None). Linux: MemTotal e MemAvailable; macOS: hw.memsize e le pagine
    libere, inattive e speculative di vm_stat (quelle che il sistema da' senza swap)."""
    if mac():
        try:
            total = int(_run("sysctl", "-n", "hw.memsize").strip()) // (1024 * 1024)
        except ValueError:
            return None, None
        vm = _run("vm_stat")
        m = re.search(r"page size of (\d+) bytes", vm)
        size = int(m.group(1)) if m else 4096
        pages = 0
        for key in ("Pages free", "Pages inactive", "Pages speculative"):
            m = re.search(rf"^{key}:\s+(\d+)", vm, re.M)
            pages += int(m.group(1)) if m else 0
        return total, (pages * size // (1024 * 1024)) if vm else None
    try:
        info = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l)
        total = int(info["MemTotal"].split()[0]) // 1024
        avail = int((info.get("MemAvailable") or info["MemFree"]).split()[0]) // 1024
        return total, avail
    except (OSError, KeyError, ValueError):
        return None, None


def boot_time():
    """L'istante (epoca) dell'avvio della macchina, o None."""
    if mac():
        m = re.search(r"sec\s*=\s*(\d+)", _run("sysctl", "-n", "kern.boottime"))
        return int(m.group(1)) if m else None
    try:
        return int(time.time() - float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def boot_id():
    """Un nome dell'avvio della macchina, diverso dopo ogni riavvio. Linux: boot_id del kernel; macOS: l'istante
    dell'avvio."""
    if not mac():
        try:
            return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            pass
    b = boot_time()
    return f"boot-{b}" if b else ""


if __name__ == "__main__":
    import json
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else os.getpid()
    print(json.dumps({"platform": platform(), "pid": pid, "alive": alive(pid), "zombie": zombie(pid), "start": start_token(pid),
                      "comm": comm(pid), "exe": exe(pid), "cwd": cwd(pid), "ppid": ppid(pid), "cmdline": cmdline(pid)[:120],
                      "loadavg": loadavg(), "memory": memory(), "boot_time": boot_time(), "boot_id": boot_id(),
                      "n_pids": len(pids()), "children": children(pid)[:5]}, indent=1))
