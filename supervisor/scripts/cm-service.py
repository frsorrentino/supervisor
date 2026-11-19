#!/usr/bin/env python3
"""supervisor service — il servizio d'avvio: all'accesso tiene accesi il relay e la master, senza mani (19/11/2026,
supervisor-servizio-avvio-ovunque-19-11; S3 di docs/analisi/avvio-master-2026-11-19.md).

  supervisor service on       lo installa (lo propone anche `supervisor setup`)
  supervisor service off      lo toglie                                        (it: spento)
  supervisor service status   dice se c'e' e dove                              (it: stato)
  supervisor service boot [--hold]   quello che fa all'accesso: `relay ensure`, poi la master senza finestra se non e'
                              aperta, con la sua ultima conversazione (o una nuova); --hold resta acceso (WSL)

Dove, per sistema:
  Linux, ChromeOS   un'unita' systemd --user (supervisor-boot.service, WantedBy=default.target); senza systemd utente,
                    una riga @reboot nel crontab
  macOS             un LaunchAgent (RunAtLoad, AbandonProcessGroup: la master resta viva quando il job finisce)
  Windows (WSL)     un'attivita' pianificata di Windows all'accesso, \\supervisor\\boot, che lancia senza finestra
                    `wsl.exe -d <distribuzione> -u <utente> -- supervisor service boot --hold`: la VM di WSL parte e
                    resta accesa (senza nessun wsl.exe aperto si spegne, e il relay con lei). Non chiede l'amministratore.
Perche': dopo un riavvio niente ripartiva da solo, e su Windows la VM restava spenta finche' qualcuno non apriva Ubuntu
(D6): l'app vedeva uno stato vecchio e il suo «Riapri» non arrivava a nessuno.
Prove: CM_SERVICE_OS (linux|darwin|wsl), CM_SERVICE_SYSTEMCTL, CM_SERVICE_LAUNCHCTL, CM_SERVICE_POWERSHELL,
CM_CRONTAB_CMD, CM_SERVICE_CM (il dispatcher) sostituiscono sistema e comandi.
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
UNIT = "supervisor-boot.service"
LABEL = "supervisor.boot"
TASK = "boot"   # 0.8.185: un nome solo; dalla 0.8.186 boot-<distribuzione>, il vecchio lo toglie off
TASK_PATH = "\\supervisor\\"
CRON_MARK = "# supervisor service"


def system():
    o = os.environ.get("CM_SERVICE_OS")
    if o:
        return o
    if sys.platform == "darwin":
        return "darwin"
    return "wsl" if os.environ.get("WSL_DISTRO_NAME") else "linux"


def shim():
    return str(cm.home() / ".local" / "bin" / "supervisor")


def cli():
    return os.environ.get("CM_SERVICE_CM") or shim()


def run(cmd, **kw):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


# ------------------------------------------------------------------ Linux: systemd --user, o @reboot
def systemctl():
    return os.environ.get("CM_SERVICE_SYSTEMCTL") or "systemctl"


def unit_path():
    return cm.home() / ".config" / "systemd" / "user" / UNIT


def has_user_systemd():
    rc, _ = run([systemctl(), "--user", "show-environment"])
    return rc == 0


def unit_text():
    # oneshot che resta «attivo» e, fermato, non uccide la master: KillMode=process tocca solo il processo di boot
    return (f"[Unit]\nDescription=supervisor: relay and master at login\n\n[Service]\nType=oneshot\nRemainAfterExit=yes\n"
            f"KillMode=process\nEnvironment=PATH={cm.home()}/.local/bin:/usr/local/bin:/usr/bin:/bin\n"
            f"ExecStart={shim()} service boot\n\n[Install]\nWantedBy=default.target\n")


def crontab_cmd():
    return os.environ.get("CM_CRONTAB_CMD") or "crontab"


def cron_line():
    return f"@reboot {shim()} service boot >/dev/null 2>&1 {CRON_MARK}"


def cron_lines():
    rc, out = run([crontab_cmd(), "-l"])
    return out.splitlines() if rc == 0 else []


def cron_write(lines):
    return run([crontab_cmd(), "-"], input="\n".join(lines) + ("\n" if lines else ""))[0] == 0


# ------------------------------------------------------------------ macOS: LaunchAgent
def plist_path():
    return cm.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def plist_text():
    path = f"{cm.home()}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0">\n<dict>\n'
            f"  <key>Label</key><string>{LABEL}</string>\n"
            f"  <key>ProgramArguments</key><array><string>{shim()}</string><string>service</string><string>boot</string></array>\n"
            "  <key>RunAtLoad</key><true/>\n  <key>AbandonProcessGroup</key><true/>\n"
            f"  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path}</string></dict>\n"
            "</dict>\n</plist>\n")


def launchctl():
    return os.environ.get("CM_SERVICE_LAUNCHCTL") or "launchctl"


# ------------------------------------------------------------------ Windows (WSL): attivita' pianificata
PS_PATHS = ("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",)


def powershell():
    """powershell.exe dal PATH, o dal suo posto fisso: l'installatore gira in WSL con un PATH pulito, senza Windows."""
    if os.environ.get("CM_SERVICE_POWERSHELL"):
        return os.environ["CM_SERVICE_POWERSHELL"]
    import shutil
    return shutil.which("powershell.exe") or next((p for p in PS_PATHS if os.path.exists(p)), "powershell.exe")


def distro():
    return os.environ.get("WSL_DISTRO_NAME") or "Ubuntu"


def task_name():
    """19/11/2026 (supervisor-master-prova-vera-19-11, la prova in sup-prova): un'attivita' per distribuzione. Con un nome
    solo («boot») la seconda distribuzione che accende il servizio prendeva il posto della prima."""
    return "boot-" + "".join(c if c.isalnum() or c in "-_." else "-" for c in distro())


def wsl_command():
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    return f"wsl.exe -d {distro()}" + (f" -u {user}" if user else "") + f" -- {shim()} service boot --hold"


def ps_on():
    vbs = f'CreateObject("WScript.Shell").Run "{wsl_command()}", 0, False'
    return (ps_legacy_off() + "$ErrorActionPreference='Stop'; $d = Join-Path $env:LOCALAPPDATA 'supervisor'; "
            "New-Item -ItemType Directory -Force $d | Out-Null; $v = Join-Path $d '" + task_name() + ".vbs'; "
            f"Set-Content -Encoding ASCII -Path $v -Value '{vbs.replace(chr(39), chr(39) * 2)}'; "
            "$a = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('\"' + $v + '\"'); "
            "$t = New-ScheduledTaskTrigger -AtLogOn -User \"$env:USERDOMAIN\\$env:USERNAME\"; "
            "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero); "
            f"Register-ScheduledTask -TaskName '{task_name()}' -TaskPath '{TASK_PATH}' -Action $a -Trigger $t -Settings $s -Force | Out-Null; 'ok'")


def ps_legacy_off():
    """L'attivita' della 0.8.185, col nome solo («boot»), se lancia questa distribuzione. 19/11 (la prova in sup-prova):
    l'aggiornamento accendeva boot-<distro> e lasciava anche quella, e all'accesso partivano tutte e due."""
    return (f"$v = Join-Path $env:LOCALAPPDATA 'supervisor\\boot.vbs'; if ((Test-Path $v) -and ((Get-Content $v -Raw) -match ' -d {distro()} ')) "
            f"{{ Unregister-ScheduledTask -TaskName '{TASK}' -TaskPath '{TASK_PATH}' -Confirm:$false -ErrorAction SilentlyContinue; Remove-Item -Force $v }}; ")


def ps_off():
    return (f"Unregister-ScheduledTask -TaskName '{task_name()}' -TaskPath '{TASK_PATH}' -Confirm:$false -ErrorAction SilentlyContinue; "
            f"Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $env:LOCALAPPDATA 'supervisor\\{task_name()}.vbs'); "
            + ps_legacy_off() + "'ok'")


def ps_status():
    return f"if (Get-ScheduledTask -TaskName '{task_name()}' -TaskPath '{TASK_PATH}' -ErrorAction SilentlyContinue) {{ 'on' }} else {{ 'off' }}"


def ps(script):
    return run([powershell(), "-NoProfile", "-NonInteractive", "-Command", script])


# ------------------------------------------------------------------ i comandi
def on():
    s = system()
    if s == "wsl":
        rc, out = ps(ps_on())
        if rc != 0 or "ok" not in out.splitlines()[-1:]:
            print(M("service.failed", line=(out.splitlines() or ["?"])[-1]), file=sys.stderr)
            return 1
        print(M("service.on_windows", task=TASK_PATH + task_name()))
        return 0
    if s == "darwin":
        p = plist_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(plist_text())
        run([launchctl(), "unload", str(p)])
        rc, out = run([launchctl(), "load", "-w", str(p)])
        if rc != 0:
            print(M("service.failed", line=(out.splitlines() or ["launchctl load"])[-1]), file=sys.stderr)
            return 1
        print(M("service.on_macos", path=cm.contract(str(p))))
        return 0
    if has_user_systemd():
        p = unit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(unit_text())
        run([systemctl(), "--user", "daemon-reload"])
        rc, out = run([systemctl(), "--user", "enable", UNIT])
        if rc != 0:
            print(M("service.failed", line=(out.splitlines() or ["systemctl enable"])[-1]), file=sys.stderr)
            return 1
        print(M("service.on_systemd", unit=UNIT))
        return 0
    lines = [l for l in cron_lines() if CRON_MARK not in l] + [cron_line()]
    if not cron_write(lines):
        print(M("service.failed", line="crontab"), file=sys.stderr)
        return 1
    print(M("service.on_cron"))
    return 0


def off():
    s = system()
    if s == "wsl":
        rc, out = ps(ps_off())
        if rc != 0:
            print(M("service.failed", line=(out.splitlines() or ["?"])[-1]), file=sys.stderr)
            return 1
    elif s == "darwin":
        p = plist_path()
        if p.exists():
            run([launchctl(), "unload", "-w", str(p)])
            p.unlink()
    else:
        p = unit_path()
        if p.exists():
            run([systemctl(), "--user", "disable", UNIT])
            p.unlink()
            run([systemctl(), "--user", "daemon-reload"])
        cur = cron_lines()
        if any(CRON_MARK in l for l in cur):
            cron_write([l for l in cur if CRON_MARK not in l])
    print(M("service.off"))
    return 0


def status():
    s = system()
    if s == "wsl":
        rc, out = ps(ps_status())
        here = rc == 0 and out.strip().endswith("on")
    elif s == "darwin":
        here = plist_path().exists()
    else:
        here = unit_path().exists() or any(CRON_MARK in l for l in cron_lines())
    print(M("service.status_on" if here else "service.status_off"))
    return 0 if here else 1


def boot(hold=False):
    """All'accesso: il relay (se c'e'), poi la master se non e' aperta. Una riga nel registro di bordo per giro."""
    # dall'attivita' di Windows, da @reboot e da launchd il PATH e' minimo: claude e lo shim stanno in ~/.local/bin
    extra = [str(cm.home() / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    os.environ["PATH"] = os.pathsep.join(extra + [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p not in extra])
    core = _load("cm-core")
    log = Path(cm.expand(CFG["state_dir"])) / "service.log"
    notes = []
    rc, _ = run([cli(), "relay", "ensure"])
    notes.append(f"relay ensure rc {rc}")
    master = core.master_name()
    rc, out = run([cli(), "sessions", "--json", "--no-screen"])
    try:
        rows = json.loads(out) if rc == 0 and out.startswith("[") else []
    except ValueError:
        rows = []
    if any((r.get("tmux") or r.get("name")) == master and r.get("status") != "dead" for r in rows):
        notes.append("master alive")
    else:
        root = cm.expand(CFG["workspace"]["root"])
        acct = cm.account_for(CFG, root)[0]
        busy = {r.get("session_id") for r in rows if r.get("session_id") and r.get("cwd") and os.path.realpath(r["cwd"]) == os.path.realpath(root)}
        sid = core.last_conversation(root, acct, busy)
        rc, out = run([cli(), "launch", root, "--no-window", "--account", acct] + (["--resume", sid] if sid else []))
        notes.append(f"launch master rc {rc}" + (f" --resume {sid}" if sid else " (new conversation)"))
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + "; ".join(notes) + "\n")
    except OSError:
        pass
    print("; ".join(notes))
    if hold:   # WSL: questo processo tiene aperto il wsl.exe dell'attivita', e con lui la VM
        sys.stdout.flush()
        os.execvp("sleep", ["sleep", "infinity"])
    return 0


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd in ("on", "acceso", "install"):
        return on()
    if cmd in ("off", "spento", "uninstall"):
        return off()
    if cmd in ("status", "stato"):
        return status()
    if cmd == "boot":
        return boot("--hold" in argv[1:])
    print(__doc__)
    return 0 if cmd in ("-h", "--help", "help") else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
