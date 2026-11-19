#!/usr/bin/env python3
"""L'installazione su Windows spiega i passi esatti, o li fa (19/11/2026, supervisor-installazione-windows-16-11; D7 di
docs/analisi/avvio-master-2026-11-19.md).

Due colleghi si erano fermati: da Git Bash `install.sh` stampava «installazione completa», poi una riga sola («apri il
terminale di Ubuntu») e usciva con 3; da PowerShell non c'era niente da lanciare.

IW1 install.sh su Windows (uname MINGW/MSYS/CYGWIN) senza Ubuntu: i quattro passi (wsl --install -d Ubuntu, riavvio,
    Ubuntu dal menu Start, la riga dell'installatore) e la riga di install.ps1, in italiano e in inglese; esce con 3;
    niente intestazione «installazione completa»
IW2 con Ubuntu in WSL (i nomi in UTF-16, come li scrive wsl.exe) e il si': l'installatore continua dentro, `wsl.exe -d
    <Ubuntu> -- bash -c` con un PATH pulito (senza quello di Windows) e la riga di install.sh; il codice d'uscita e' il suo
IW3 con Ubuntu e senza il si' (nessun terminale): la riga da incollare in Ubuntu, esce con 3, wsl.exe non lancia niente
IW4 install.ps1: controlla le distribuzioni (wsl -l -q, Ubuntu), installa WSL con conferma (wsl --install -d Ubuntu, da
    amministratore con RunAs), poi lancia install.sh dentro Ubuntu; tutto in una funzione (con `irm | iex` un exit
    chiuderebbe la finestra); solo ASCII (PowerShell 5.1 legge senza BOM in ANSI)
IW5 install.ps1 eseguito davvero, se c'e' powershell.exe (WSL su Windows): con Ubuntu e il no → la riga da incollare;
    senza Ubuntu e il si' → wsl --install -d Ubuntu e i tre passi rimasti
IW6 README e details/windows.md dicono come si installa su Windows (install.ps1, wsl --install)
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
home.mkdir()
fb = tmp / "bin"
fb.mkdir()
(fb / "uname").write_text("#!/bin/sh\necho MINGW64_NT-10.0-22000\n")
(fb / "uname").chmod(0o755)
log = tmp / "wsl.log"
URL_SH = "https://raw.githubusercontent.com/frsorrentino/supervisor/main/install.sh"
URL_PS = "https://raw.githubusercontent.com/frsorrentino/supervisor/main/install.ps1"


def fake_wsl(distros, rc=0):
    w = tmp / f"wsl-{len(distros)}.exe"
    # wsl -l -q scrive in UTF-16LE con CRLF
    names = "".join(d + "\r\n" for d in distros).encode("utf-16-le")
    (tmp / "names.bin").write_bytes(names)
    w.write_text(f"""#!/bin/sh
if [ "$1" = "-l" ]; then {'cat ' + str(tmp / 'names.bin') if distros else 'exit 1'}; exit 0; fi
printf '%s\\n' "$*" >> "{log}"
exit {rc}
""")
    w.chmod(0o755)
    return w


def install(lang, wsl, yes=None):
    env = {"PATH": f"{fb}:{os.environ['PATH']}", "HOME": str(home), "LANG": lang, "SUPERVISOR_WSL_BIN": str(wsl)}
    if yes:
        env["SUPERVISOR_INSTALL_YES"] = yes
    log.write_text("")
    return subprocess.run(["bash", str(T.ROOT / "install.sh")], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)


# ------------------------------------------------------------------ IW1
none = fake_wsl([])
r_it, r_en = install("it_IT.UTF-8", none), install("en_US.UTF-8", none)
steps_it = ["wsl --install -d Ubuntu", "Riavvia il computer", "Apri «Ubuntu» dal menu Start", f"curl -fsSL {URL_SH} | bash", f"irm {URL_PS} | iex"]
steps_en = ["wsl --install -d Ubuntu", "Restart the computer", "Open «Ubuntu» from the Start menu", f"curl -fsSL {URL_SH} | bash", f"irm {URL_PS} | iex"]
T.check("IW1 Windows without Ubuntu (Italian): the four steps and the install.ps1 line, exit 3",
        r_it.returncode == 3 and all(s in r_it.stdout for s in steps_it), r_it.stdout + r_it.stderr)
T.check("IW1 the same in English", r_en.returncode == 3 and all(s in r_en.stdout for s in steps_en), r_en.stdout + r_en.stderr)
T.check("IW1 no «complete install» header on Windows (it said so, then stopped)",
        "installazione completa" not in r_it.stdout and "complete install" not in r_en.stdout, r_it.stdout[:200])

# ------------------------------------------------------------------ IW2
ub = fake_wsl(["docker-desktop", "Ubuntu-24.04"], rc=7)
r = install("it_IT.UTF-8", ub, yes="1")
calls = log.read_text().splitlines()
T.check("IW2 Ubuntu in WSL and a yes: the installer goes on inside it (`-d Ubuntu-24.04 -- bash -c`, a clean PATH, the install.sh line); its exit code is the installer's",
        r.returncode == 7 and len(calls) == 1 and calls[0].startswith("-d Ubuntu-24.04 -- bash -c ")
        and "PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" in calls[0] and f"curl -fsSL {URL_SH} | bash" in calls[0], r.stdout + str(calls))

# ------------------------------------------------------------------ IW3
r = install("it_IT.UTF-8", ub)
T.check("IW3 Ubuntu but no yes (no terminal): the line to paste in Ubuntu, exit 3, nothing launched",
        r.returncode == 3 and "Ubuntu-24.04" in r.stdout and f"curl -fsSL {URL_SH} | bash" in r.stdout and log.read_text() == "", r.stdout)

# ------------------------------------------------------------------ IW4
ps = (T.ROOT / "install.ps1").read_text(encoding="utf-8") if (T.ROOT / "install.ps1").is_file() else ""
code = "\n".join(l for l in ps.splitlines() if not l.lstrip().startswith("#"))
T.check("IW4 install.ps1 exists, looks for Ubuntu (wsl -l -q), installs WSL after asking (wsl --install -d Ubuntu, RunAs), runs install.sh inside Ubuntu",
        bool(ps) and "-l -q" in code and "'--install', '-d', 'Ubuntu'" in code and "-Verb RunAs" in code and "Read-Host" in code
        and "-d $distro -- bash -c" in code and URL_SH in code, ps[:200])
T.check("IW4 everything in a function, no top-level exit (with `irm | iex` it would close the window); ASCII only",
        bool(ps) and re.search(r"^function Install-Supervisor", code, re.M) and not re.search(r"^\s*exit\b", code, re.M | re.I)
        and all(ord(c) < 128 for c in ps), "")

# ------------------------------------------------------------------ IW5
pwsh = shutil.which("powershell.exe")
if pwsh and ps and shutil.which("wslpath"):
    win = Path("/mnt/c/Windows/Temp") / f"cm-iw-{os.getpid()}"
    win.mkdir(parents=True, exist_ok=True)
    try:
        (win / "install.ps1").write_text(ps)
        wlog = win / "wsl.log"
        for name, body in (("with.cmd", "@echo off\r\nif \"%1\"==\"-l\" (echo Ubuntu-24.04& exit /b 0)\r\necho %*>>\"%~dp0wsl.log\"\r\nexit /b 0\r\n"),
                           ("without.cmd", "@echo off\r\nif \"%1\"==\"-l\" exit /b 1\r\necho %*>>\"%~dp0wsl.log\"\r\nexit /b 0\r\n")):
            (win / name).write_text(body)
        wp = lambda p: subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True).stdout.strip()  # noqa: E731

        def run_ps(fake, yes):
            env = dict(os.environ, SUPERVISOR_WSL_BIN=wp(win / fake), SUPERVISOR_INSTALL_YES=yes, SUPERVISOR_LANG="it",
                       WSLENV="SUPERVISOR_WSL_BIN:SUPERVISOR_INSTALL_YES:SUPERVISOR_LANG")
            wlog.unlink(missing_ok=True)
            return subprocess.run([pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", wp(win / "install.ps1")],
                                  capture_output=True, text=True, errors="replace", env=env, timeout=120)   # la codepage OEM di PowerShell
        a = run_ps("with.cmd", "0")
        T.check("IW5 install.ps1 run for real: Ubuntu and a no → the line to paste in Ubuntu, nothing launched",
                "Ubuntu-24.04" in a.stdout and f"curl -fsSL {URL_SH} | bash" in a.stdout and not wlog.exists(), a.stdout + a.stderr)
        b = run_ps("without.cmd", "1")
        got = wlog.read_text().strip() if wlog.exists() else ""
        T.check("IW5 install.ps1 run for real: no Ubuntu and a yes → wsl --install -d Ubuntu, then the three steps left",
                got == "--install -d Ubuntu" and "Riavvia il computer" in b.stdout and f"irm {URL_PS} | iex" in b.stdout, b.stdout + b.stderr + got)
    finally:
        shutil.rmtree(win, ignore_errors=True)
else:
    print("  (IW5 skipped: no powershell.exe here; install.ps1 is checked by IW4)")

# ------------------------------------------------------------------ IW6
readme = (T.ROOT / "README.md").read_text()
wmd = (T.ROOT / "details" / "windows.md").read_text()
T.check("IW6 README: the Windows line says how to install there (install.ps1)", f"irm {URL_PS} | iex" in readme, "")
T.check("IW6 details/windows.md: the steps (wsl --install -d Ubuntu, install.ps1, install.sh from Git Bash)",
        "wsl --install -d Ubuntu" in wmd and f"irm {URL_PS} | iex" in wmd and "Git Bash" in wmd, "")

T.finish()
