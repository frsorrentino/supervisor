#!/usr/bin/env python3
"""Chiusura dal telefono di una sessione su win con un lavoro in sottofondo (23/11/2026, supervisor-chiusura-remota-23-11).

Il 23/11 alle 21:15 close_preview dava win:supervisor chiudibile mentre «wsl … release-run.sh 0.8.202» girava in un Bash
in sottofondo: background_pids restituiva [] per ogni sessione di un altro host, e la chiusura ha fermato la release.

CR1 una sessione su win con un Bash in sottofondo (bash.exe -c "source …/shell-snapshots/…") non e' chiudibile: blocked_by background
CR2 la barra di stato (bash.exe -c "<statusLine.command>"), un server MCP (cmd.exe /c npx) e l'aiutante di prima (niente shells) non bloccano
CR3 anche la shell dello strumento PowerShell (cmd.exe … CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT) e' sottofondo
CR4 «Chiudi comunque» su win: taskkill /T /F di quel pid sull'host, solo se e' ancora figlio del processo claude; nessun os.kill qui
CR5 cm-hosts.remote_session_rows porta `shells` dallo stato dell'host nella riga della sessione
CR6 cm-remote.ps1 status mette `shells` (ShellKids) in ogni sessione viva; con powershell.exe a portata, anche dal vivo
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"personal": {"config_dir": str(tmp / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"},
                           "relay": {"dir": str(tmp / "relay")}, "hosts": {"win": {"adapter": "windows-native"}}}))
(tmp / "relay").mkdir()
os.environ.update({"HOME": str(tmp), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db")})
sp = importlib.util.spec_from_file_location("cm_relay_cr", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(sp)
sp.loader.exec_module(RL)
RL.tmux_of = lambda s: s
RL.last_sessions = lambda: {}

STATUSLINE = {"pid": 77116, "name": "bash.exe", "cmd": '"C:\\Program Files\\Git\\bin\\bash.exe" -c "bash \\"C:/Users/Utente/.claude/plugins/cache/x/statusline-ctx.sh\\""'}
MCP = {"pid": 15152, "name": "cmd.exe", "cmd": 'C:\\WINDOWS\\system32\\cmd.exe /d /s /c "npx ^"-y^" ^"chrome-bridge-mcp@1.32.8^""'}
BASH_BG = {"pid": 70356, "name": "bash.exe", "cmd": '"C:\\Program Files\\Git\\bin\\bash.exe" -c "source /c/Users/Utente/.claude/shell-snapshots/snapshot-bash-1795198189536-hsx3kk.sh 2>/dev/null || true && export TEMP=\'C:\\Users\\Utente\\AppData\\Local\\Temp\''}
PS_BG = {"pid": 53080, "name": "cmd.exe", "cmd": 'C:\\WINDOWS\\System32\\cmd.exe /d /s /c ""C:\\WINDOWS\\System32\\chcp.com" 65001 >nul & "C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$__claudeCodeScript = $env:CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT;'}


def row(name, shells):
    r = {"pid": 69496, "name": name, "tmux": "", "host": "win", "status": "idle", "session_id": "sid-" + name, "cwd": "D:\\x"}
    if shells is not None:
        r["shells"] = shells
    return r


ROWS = [row("win:supervisor", [STATUSLINE, MCP, BASH_BG]), row("win:quieta", [STATUSLINE, MCP]), row("win:vecchia", None),
        row("win:ps", [MCP, PS_BG])]
chk = {r["name"]: RL.close_check(r["name"], ROWS)[0] for r in ROWS}
T.check("CR1 a win session with a Bash in background (shell-snapshots) is not closable: blocked_by background",
        chk["win:supervisor"]["closable"] is False and chk["win:supervisor"]["blocked_by"] == "background", str(chk["win:supervisor"]))
T.check("CR2 the status bar, an MCP server (cmd /c npx) and the helper of before (no shells) do not block",
        all(chk[n]["closable"] and chk[n]["blocked_by"] is None for n in ("win:quieta", "win:vecchia")), str(chk))
T.check("CR3 the PowerShell tool shell (CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT) is background too",
        chk["win:ps"]["blocked_by"] == "background" and RL.background_pids(ROWS[3]) == [53080], str(chk["win:ps"]))

ran, killed = [], []


class FakeAdapter:
    def exec(self, cmd, timeout=30):
        ran.append(cmd)
        return 0, "", ""


class FakeHosts:
    def adapter(self, name, first_contact=False):
        ran.append(("host", name))
        return FakeAdapter()


RL._hosts_mod = lambda: FakeHosts()
real_kill = RL.os.kill
RL.os.kill = lambda pid, sig: killed.append(pid)
RL.run_cm = lambda *a, **k: (0, "")
RL.relay_talk = lambda *a, **k: (0, "")
try:
    ok = RL.close_force_start("win:supervisor", ROWS[0], chk["win:supervisor"], "phone")
finally:
    RL.os.kill = real_kill
script = next((c for c in ran if isinstance(c, str)), "")
T.check("CR4 «Chiudi comunque» on win: taskkill /T /F of that pid on the host, only while it is still a child of the claude process; no os.kill here",
        ok and ("host", "win") in ran and "@(70356)" in script and "taskkill /T /F" in script and "-eq 69496" in script
        and "77116" not in script and "15152" not in script and not killed, str((ok, ran, killed)))

hp = importlib.util.spec_from_file_location("cm_hosts_cr", T.SCRIPTS / "cm-hosts.py")
HM = importlib.util.module_from_spec(hp)
hp.loader.exec_module(HM)
HM.remote_snapshots = lambda: {"win": {"read_at": 0, "status": {"sessions": [
    {"alive": True, "config_dir": ".claude", "entry": {"pid": 69496, "name": "win-supervisor", "sessionId": "s1", "cwd": "D:\\x", "status": "idle"},
     "shells": [STATUSLINE, BASH_BG, "rumore"]},
    {"alive": True, "config_dir": ".claude", "entry": {"pid": 6716, "name": "win-app", "sessionId": "s2", "cwd": "D:\\y", "status": "idle"}}]}}}
rows = HM.remote_session_rows()
by = {r["remote_name"]: r for r in rows}
T.check("CR5 cm-hosts.remote_session_rows carries `shells` from the host status into the session row (absent with the helper of before)",
        [x["pid"] for x in by["win-supervisor"].get("shells") or []] == [77116, 70356] and "shells" not in by["win-app"]
        and RL.background_pids(by["win-supervisor"]) == [70356], str(rows))

ps1 = (T.SCRIPTS.parent / "remote" / "cm-remote.ps1").read_text(encoding="utf-8")
static = "function ShellKids" in ps1 and "$item.shells = ShellKids" in ps1 and all(ord(ch) < 128 for ch in ps1)
live, why = True, "powershell.exe non c'e': solo il controllo del testo"
pw = shutil.which("powershell.exe")
if static and pw:
    src = str(T.SCRIPTS.parent / "remote" / "cm-remote.ps1")
    if shutil.which("wslpath"):
        src = subprocess.run(["wslpath", "-w", src], capture_output=True, text=True).stdout.strip()
    p = subprocess.run([pw, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", src, "~/.claude-master-remote", "status"],
                       capture_output=True, text=True, timeout=180)
    out = p.stdout.split("@@CM-JSON@@")[-1].strip().splitlines()
    try:
        d = json.loads(out[0]) if out else {}
    except ValueError:
        d = {}
    alive = [s for s in d.get("sessions") or [] if s.get("alive")]
    live = bool(d.get("ok")) and all(isinstance(s.get("shells"), list) and all(isinstance(x.get("pid"), int) and x.get("name") for x in s["shells"])
                                     for s in alive)
    why = f"{len(alive)} sessioni vive: " + "; ".join(f"{s['entry'].get('name')}: {[x['name'] for x in s.get('shells') or []]}" for s in alive)[:600]
T.check("CR6 cm-remote.ps1 status puts `shells` (ShellKids) in every live session; with powershell.exe at hand, also live",
        static and live, why)
T.finish()
