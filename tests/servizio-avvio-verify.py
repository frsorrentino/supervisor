#!/usr/bin/env python3
"""Il servizio d'avvio: all'accesso relay e master ripartono da soli, su ogni sistema (19/11/2026,
supervisor-servizio-avvio-ovunque-19-11; S3 e D6 di docs/analisi/avvio-master-2026-11-19.md).

SV1 Linux con systemd utente: `service on` scrive supervisor-boot.service (oneshot, RemainAfterExit, KillMode=process,
    ExecStart = lo shim `service boot`, WantedBy=default.target) e lo abilita; `off` lo disabilita e lo toglie
SV2 Linux senza systemd utente: una riga @reboot nel crontab; `off` la toglie e lascia le altre
SV3 macOS: un LaunchAgent con RunAtLoad e AbandonProcessGroup (la master resta viva), caricato con launchctl; `off` lo toglie
SV4 Windows (WSL): un'attivita' pianificata all'accesso via powershell.exe: wscript con un .vbs nascosto che lancia
    `wsl.exe -d <distribuzione> -u <utente> -- <shim> service boot --hold`; `off` la toglie
SV5 `service boot`: relay ensure; master chiusa → launch della radice senza finestra, con la sua ultima conversazione;
    master aperta → niente launch; una riga nel registro service.log
SV6 `supervisor setup` lo propone acceso (--service yes lo accende, --service no no; --dry-run non tocca niente)
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
for d in ("ws/personali/alfa", ".claude/sessions", ".local/bin"):
    (home / d).mkdir(parents=True)
state = tmp / "state"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "en", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master", "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(tmp / "relay")},
}))
calls = tmp / "calls.log"


def fake(name, body):
    p = tmp / name
    p.write_text(f"#!/bin/sh\nprintf '{name} %s\\n' \"$*\" >> \"{calls}\"\n{body}\n")
    p.chmod(0o755)
    return str(p)


crontab_file = tmp / "crontab.txt"
alive = tmp / "alive.json"
alive.write_text("[]")
SYSTEMCTL_OK = fake("systemctl", "exit 0")
SYSTEMCTL_NO = fake("systemctl-no", "exit 1")
LAUNCHCTL = fake("launchctl", "exit 0")
PS = fake("powershell", "echo ok")
CRONTAB = fake("crontab", f'if [ "$1" = "-l" ]; then cat "{crontab_file}" 2>/dev/null; else cat > "{crontab_file}"; fi')
CM = fake("cm", f'case "$1" in sessions) cat "{alive}" ;; *) echo ok ;; esac')
BASE = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "CLAUDECODE", "WSL_DISTRO_NAME")}
BASE.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_CRONTAB_CMD": CRONTAB,
             "CM_SERVICE_LAUNCHCTL": LAUNCHCTL, "CM_SERVICE_POWERSHELL": PS, "CM_SERVICE_CM": CM, "USER": "collega"})
SHIM = str(home / ".local" / "bin" / "supervisor")
EN = json.loads((T.ROOT / "supervisor/messages/en.json").read_text())


def svc(*args, **env):
    calls.write_text("")
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-service.py"), *args], capture_output=True, text=True, env=dict(BASE, **env), timeout=60)
    return r, calls.read_text().splitlines()


# ------------------------------------------------------------------ SV1
unit = home / ".config" / "systemd" / "user" / "supervisor-boot.service"
r, c = svc("on", CM_SERVICE_OS="linux", CM_SERVICE_SYSTEMCTL=SYSTEMCTL_OK)
u = unit.read_text() if unit.exists() else ""
T.check("SV1 Linux with user systemd: supervisor-boot.service written (oneshot, RemainAfterExit, KillMode=process, the shim's `service boot`, default.target) and enabled",
        r.returncode == 0 and "Type=oneshot" in u and "RemainAfterExit=yes" in u and "KillMode=process" in u
        and f"ExecStart={SHIM} service boot" in u and "WantedBy=default.target" in u and "systemctl --user enable supervisor-boot.service" in c, r.stdout + r.stderr + str(c))
r, c = svc("off", CM_SERVICE_OS="linux", CM_SERVICE_SYSTEMCTL=SYSTEMCTL_OK)
T.check("SV1 service off: disabled and removed", r.returncode == 0 and not unit.exists() and "systemctl --user disable supervisor-boot.service" in c, str(c))

# ------------------------------------------------------------------ SV2
crontab_file.write_text("* * * * * altro lavoro\n")
r, c = svc("on", CM_SERVICE_OS="linux", CM_SERVICE_SYSTEMCTL=SYSTEMCTL_NO)
cron = crontab_file.read_text()
T.check("SV2 Linux without user systemd: an @reboot line in the crontab, the other lines kept",
        r.returncode == 0 and f"@reboot {SHIM} service boot" in cron and "altro lavoro" in cron and not unit.exists(), cron)
svc("on", CM_SERVICE_OS="linux", CM_SERVICE_SYSTEMCTL=SYSTEMCTL_NO)
T.check("SV2 a second `on` does not double the line", crontab_file.read_text().count("@reboot") == 1, crontab_file.read_text())
r, c = svc("off", CM_SERVICE_OS="linux", CM_SERVICE_SYSTEMCTL=SYSTEMCTL_NO)
T.check("SV2 service off removes it and keeps the others", "@reboot" not in crontab_file.read_text() and "altro lavoro" in crontab_file.read_text(), crontab_file.read_text())

# ------------------------------------------------------------------ SV3
plist = home / "Library" / "LaunchAgents" / "supervisor.boot.plist"
r, c = svc("on", CM_SERVICE_OS="darwin")
pl = plist.read_text() if plist.exists() else ""
T.check("SV3 macOS: a LaunchAgent with RunAtLoad and AbandonProcessGroup, the shim's `service boot`, loaded with launchctl",
        r.returncode == 0 and "<key>RunAtLoad</key><true/>" in pl and "<key>AbandonProcessGroup</key><true/>" in pl
        and f"<string>{SHIM}</string><string>service</string><string>boot</string>" in pl and any(x.startswith("launchctl load -w") for x in c), pl + str(c))
r, c = svc("off", CM_SERVICE_OS="darwin")
T.check("SV3 service off: unloaded and removed", not plist.exists() and any(x.startswith("launchctl unload -w") for x in c), str(c))

# ------------------------------------------------------------------ SV4
r, c = svc("on", CM_SERVICE_OS="wsl", WSL_DISTRO_NAME="Ubuntu-24.04")
script = c[0] if c else ""
T.check("SV4 Windows (WSL): a scheduled task at logon through powershell.exe: wscript with a hidden .vbs running `wsl.exe -d Ubuntu-24.04 -u collega -- <shim> service boot --hold`",
        r.returncode == 0 and "Register-ScheduledTask -TaskName 'boot-Ubuntu-24.04' -TaskPath '\\supervisor\\'" in script and "-AtLogOn" in script
        and "wscript.exe" in script and f"wsl.exe -d Ubuntu-24.04 -u collega -- {SHIM} service boot --hold" in script and ", 0, False" in script, script[:900])
r2, c2 = svc("on", CM_SERVICE_OS="wsl", WSL_DISTRO_NAME="sup-prova")
T.check("SV4 (19/11, the test in sup-prova) one task per distribution: a second distribution does not take the first one's place",
        "-TaskName 'boot-sup-prova'" in (c2[0] if c2 else "") and "'boot-sup-prova.vbs'" in (c2[0] if c2 else "") and "boot-Ubuntu-24.04" not in (c2[0] if c2 else ""), str(c2)[:400])
T.check("SV4 (19/11, the update in sup-prova) `on` also removes 0.8.185's single-name task when it launches this distribution (else both start at login)",
        "-match ' -d Ubuntu-24.04 '" in script and script.index("-match ' -d Ubuntu-24.04 '") < script.index("Register-ScheduledTask"), script[:500])
r, c = svc("off", CM_SERVICE_OS="wsl", WSL_DISTRO_NAME="Ubuntu-24.04")
T.check("SV4 service off: this distribution's task and .vbs go, and the single-name one of 0.8.185 when it launches this distribution",
        r.returncode == 0 and "Unregister-ScheduledTask -TaskName 'boot-Ubuntu-24.04'" in (c[0] if c else "") and "boot-Ubuntu-24.04.vbs" in (c[0] if c else "")
        and "-match ' -d Ubuntu-24.04 '" in (c[0] if c else ""), str(c)[:600])
T.check("SV6 (19/11, the test in sup-prova) the setup summary names the step («startup service»), not the raw key",
        EN.get("install.step_service") == "startup service", str(EN.get("install.step_service")))

# ------------------------------------------------------------------ SV5
proj = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(ws)))
proj.mkdir(parents=True)
(proj / "M-LAST.jsonl").write_text("{}\n")
r, c = svc("boot", CM_SERVICE_OS="linux")
log = (state / "service.log").read_text() if (state / "service.log").exists() else ""
T.check("SV5 boot with the master closed: relay ensure, then `launch <root> --no-window --account personale --resume M-LAST`; a line in service.log",
        r.returncode == 0 and "cm relay ensure" in c and f"cm launch {ws} --no-window --account personale --resume M-LAST" in c and "launch master rc 0" in log, str(c) + log)
alive.write_text(json.dumps([{"name": "master", "tmux": "master", "cwd": str(ws), "status": "idle", "session_id": "M-LAST"}]))
r, c = svc("boot", CM_SERVICE_OS="linux")
T.check("SV5 boot with the master open: no launch", r.returncode == 0 and not any(x.startswith("cm launch") for x in c) and "master alive" in r.stdout, str(c))

# ------------------------------------------------------------------ SV6
src = (T.SCRIPTS / "cm-setup.py").read_text()
T.check("SV6 setup proposes it turned on: --service yes|no, the question with yes as default, step_service runs `cm-service.py on`",
        '"--service"' in src and 'ask(M("install.ask_service") + " [Y/n]", "y"' in src and '"cm-service.py"), "on"]' in src
        and "install.ask_service" in EN and "--service yes|no" in EN.get("install.usage", ""), "")
d = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-setup.py"), "--dry-run", "--yes", "--root", str(ws), "--phone", "no"],
                   capture_output=True, text=True, env=dict(BASE, CM_SERVICE_OS="linux", CM_SERVICE_SYSTEMCTL=SYSTEMCTL_OK), timeout=180)
T.check("SV6 setup --dry-run --yes lists the startup service among the steps and touches nothing",
        EN["install.dry_service"] in d.stdout and not unit.exists(), d.stdout[-800:] + d.stderr[-300:])
T.check("SV6 the dispatcher has `service`", 'service|servizio)' in (T.SCRIPTS / "supervisor").read_text(), "")

T.finish()
