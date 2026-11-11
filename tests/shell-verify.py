#!/usr/bin/env python3
"""Verifica shell/supervisor.sh in bash (e zsh se installata) con claude finto e tmux privato.

W1  i wrapper di shell.wrappers esistono; gli alias di shell.aliases esistono
W2  passthrough (T23): `claude --version` non crea sessioni tmux
W3  dentro tmux (TMUX impostata) → claude puro, niente --remote-control (T24)
W4  fuori tmux: `claude` in una cartella crea la sessione tmux col nome della cartella, -n/--remote-control, destroy-unattached DOPO l'attacco (T7); secondo account → prefisso e CLAUDE_CONFIG_DIR; primo account → senza (T68)
W5  avviso quando la cartella dice un altro account (T49), ma procede
W6  guardia degli shell snapshot (T22): wrapper senza _cm_wrap → command claude
W7  segnaposto (T21): file recente + sessione esistente → exec attach; file vecchio → rimosso; sessione inesistente → ignorato
W8  innesco ripristino (T20): uptime basso + nessun server tmux + registro pieno → CM_RESTORE_CMD eseguito; uptime alto → no; con tmux vivo → no
W8b una sola domanda per accensione (boot_id): la seconda shell tace, un'accensione nuova richiede
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude/sessions", ".claude-pixel/sessions", "ws/personali/alfa", "ws/pro/beta", "bin"):
    (home / d).mkdir(parents=True)
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
os.symlink(FAKE, home / "bin" / "claude")
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-", "shell_command": "claude-pro"}},
    "default_account": "personale",
    "folder_map": [{"path": str(home / "ws" / "pro"), "account": "professionale"}, {"path": str(home / "ws" / "personali"), "account": "personale"}],
    "session": {"claude_args": ["--dangerously-skip-permissions"]},
    "shell": {"wrappers": {"claude": "personale", "claude-pro": "professionale"}},
    "registry": {"file": str(tmp / "registry.json")},
    "restore": {"uptime_max_min": 15},
    "tile": {"placeholder_file": str(tmp / "next-session"), "placeholder_ttl_s": 120},
    "tabs": {"color_registry": str(tmp / "colors")},
}))
SH = T.PLUGIN / "shell" / "supervisor.sh"
argslog = tmp / "args.log"
uptime_low = tmp / "uptime-low"; uptime_low.write_text("300.0 100.0\n")
uptime_high = tmp / "uptime-high"; uptime_high.write_text("99999.0 100.0\n")


def base_env(**extra):
    e = {"PATH": f"{home}/bin:{os.environ['PATH']}", "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "FAKE_CLAUDE_ARGS_LOG": str(argslog), "FAKE_CLAUDE_SCENARIO": "plain",
         "CM_UPTIME_FILE": str(uptime_high), "TERM": "xterm-256color"}
    e.update(extra)
    return e


def bash(script, **extra):
    """bash NON interattiva che fa source del file e poi esegue script (CM_FORCE_INTERACTIVE per i blocchi interattivi)."""
    return subprocess.run(["bash", "-c", f'source "{SH}"; {script}'], capture_output=True, text=True, env=base_env(**extra), timeout=60)


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "seed", "bash", "--norc"], env=base_env(), check=True)   # server con l'ambiente del test (T66)
    r = bash("type claude; type claude-pro; alias lancia; alias sessioni")
    T.check("W1 wrappers and aliases defined", "claude is a function" in r.stdout and "claude-pro is a function" in r.stdout and "supervisor launch" in r.stdout and "supervisor sessions" in r.stdout, r.stdout + r.stderr)
    r = bash("claude --version")
    T.check("W2 passthrough: --version answered by claude, no tmux session", "fake" in r.stdout and tm("list-sessions", "-F", "#{session_name}").stdout.strip() == "seed", r.stdout + r.stderr)
    r = bash("cd '%s'; claude -n dentro </dev/null" % (home / "ws" / "personali" / "alfa"), TMUX="/tmp/fake-tmux,1,0")
    T.check("W3 inside tmux: plain claude, no --remote-control", "--remote-control" not in argslog.read_text().splitlines()[-1] and tm("has-session", "-t", "=alfa").returncode != 0, argslog.read_text())
    # W4: wrapper fuori tmux in un runner (serve un tty per attach)
    runner = (f"env -u TMUX PATH='{home}/bin:{os.environ['PATH']}' HOME='{home}' CM_HOME='{home}' CC_SUPERVISOR_CONFIG='{cfg}' CM_TMUX_ARGS='{tm.env['CM_TMUX_ARGS']}' "
              f"FAKE_CLAUDE_ARGS_LOG='{argslog}' bash -c 'source \"{SH}\"; cd \"{home}/ws/personali/alfa\"; claude'; sleep 5")
    tm("new-session", "-d", "-s", "runner", "-x", "100", "-y", "24", runner)
    for _ in range(20):
        time.sleep(0.5)
        if tm("has-session", "-t", "=alfa").returncode == 0 and "on" in (tm("show-options", "-t", "alfa", "-v", "destroy-unattached").stdout or ""):
            break
    T.check("W4 wrapper creates the tmux session named after the folder", tm("has-session", "-t", "=alfa").returncode == 0, tm("list-sessions").stdout)
    T.check("W4 -n NAME and --remote-control NAME in argv, claude_args present", "-n alfa" in argslog.read_text().splitlines()[-1] and "--remote-control alfa" in argslog.read_text().splitlines()[-1] and "--dangerously-skip-permissions" in argslog.read_text().splitlines()[-1], argslog.read_text().splitlines()[-1])
    T.check("W4 destroy-unattached on after attach (T7)", "on" in (tm("show-options", "-t", "alfa", "-v", "destroy-unattached").stdout or ""), tm("show-options", "-t", "alfa").stdout)
    pane_env = T.proc_env(tm("list-panes", "-t", "alfa", "-F", "#{pane_pid}").stdout.strip() or 0)
    T.check("W4 first account: no CLAUDE_CONFIG_DIR (T68)", "CLAUDE_CONFIG_DIR=" not in pane_env, pane_env[:200])
    tm("kill-session", "-t", "=runner")
    runner2 = runner.replace('cd \\"' + str(home / "ws/personali/alfa") + '\\"; claude', 'cd \\"' + str(home / "ws/pro/beta") + '\\"; claude-pro')
    runner2 = runner.replace(f'cd "{home}/ws/personali/alfa"; claude', f'cd "{home}/ws/pro/beta"; claude-pro')
    tm("new-session", "-d", "-s", "runner2", "-x", "100", "-y", "24", runner2)
    for _ in range(20):
        time.sleep(0.5)
        if tm("has-session", "-t", "=pix-beta").returncode == 0:
            break
    pane_env = T.proc_env(tm("list-panes", "-t", "pix-beta", "-F", "#{pane_pid}").stdout.strip() or 0)
    T.check("W4 second account: prefix and CLAUDE_CONFIG_DIR", tm("has-session", "-t", "=pix-beta").returncode == 0 and f"CLAUDE_CONFIG_DIR={home}/.claude-pixel" in pane_env, pane_env[:300] + tm("list-sessions").stdout)
    tm("kill-session", "-t", "=runner2")
    # W5: avviso di account — senza tty l'attach fallisce, ma l'avviso e' gia' su stderr
    r = bash("cd '%s'; claude </dev/null 2>&1 | head -3" % (home / "ws" / "pro" / "beta"))
    T.check("W5 warning when the folder says another account", "professionale" in r.stdout and ("nota" in r.stdout.lower() or "note" in r.stdout.lower()), r.stdout + r.stderr)
    tm("kill-session", "-t", "=beta")
    # W6
    r = bash("unset -f _cm_wrap; claude --version")
    T.check("W6 snapshot guard: no _cm_wrap → command claude", "fake" in r.stdout, r.stdout + r.stderr)
    # T76: lo snapshot vero porta SOLO i wrapper: niente helper, niente _cm_fn_exists
    r = bash("unset -f _cm_wrap _cm_fn_exists _cm_tmux; claude --version")
    T.check("W6b snapshot guard: wrappers alone (no helper at all) → command claude, no 'command not found'", "fake" in r.stdout and "command not found" not in r.stderr, r.stdout + r.stderr)
    # W7: segnaposto
    ph = tmp / "next-session"
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "ph-target", "bash", "--norc"], env=base_env(), check=True)
    ph.write_text("ph-target\n")
    runner4 = f"env -u TMUX PATH='{home}/bin:{os.environ['PATH']}' HOME='{home}' CM_HOME='{home}' CC_SUPERVISOR_CONFIG='{cfg}' CM_TMUX_ARGS='{tm.env['CM_TMUX_ARGS']}' CM_FORCE_INTERACTIVE=1 bash -c 'source \"{SH}\"; echo NOT-ATTACHED; sleep 3'"
    tm("new-session", "-d", "-s", "runner4", "-x", "100", "-y", "24", runner4)
    time.sleep(3)
    T.check("W7 placeholder consumed and shell attached to the session", not ph.exists() and "ph-target 1" in tm("list-sessions", "-F", "#{session_name} #{session_attached}").stdout
            and "NOT-ATTACHED" not in tm("capture-pane", "-p", "-t", "runner4").stdout, tm("list-sessions", "-F", "#{session_name} #{session_attached}").stdout)
    tm("kill-session", "-t", "=runner4")
    ph.write_text("ph-target\n"); os.utime(ph, (time.time() - 600, time.time() - 600))
    r = bash("echo done", CM_FORCE_INTERACTIVE="1")
    T.check("W7 stale placeholder removed, shell continues", not ph.exists() and "done" in r.stdout, r.stdout + r.stderr)
    ph.write_text("nessuna\n")
    r = bash("echo done", CM_FORCE_INTERACTIVE="1")
    T.check("W7 placeholder for a missing session ignored, shell continues", "done" in r.stdout, r.stdout + r.stderr)
    ph.unlink(missing_ok=True)
    # W8: innesco del ripristino
    (tmp / "registry.json").write_text(json.dumps({"salvato": "x", "sessioni": [{"nome": "alfa", "cartella": str(home), "account": "personale"}]}))
    marker = tmp / "restored"
    r = bash("echo done", CM_FORCE_INTERACTIVE="1", CM_UPTIME_FILE=str(uptime_low), CM_RESTORE_CMD=f"touch {marker}", CM_TMUX_ARGS="-L cm-no-such-server")
    T.check("W8 fresh boot + no tmux + registry → restore command runs", marker.exists() and "done" in r.stdout, r.stdout + r.stderr)
    marker.unlink()
    r = bash("echo done", CM_FORCE_INTERACTIVE="1", CM_UPTIME_FILE=str(uptime_high), CM_RESTORE_CMD=f"touch {marker}", CM_TMUX_ARGS="-L cm-no-such-server")
    T.check("W8 high uptime → no restore", not marker.exists(), "")
    r = bash("echo done", CM_FORCE_INTERACTIVE="1", CM_UPTIME_FILE=str(uptime_low), CM_RESTORE_CMD=f"touch {marker}")
    T.check("W8 tmux server alive → no restore", not marker.exists(), "")
    # W8b: una sola domanda per accensione (28/09/2026: una seconda scheda ripartiva da sola dopo un «n»)
    boot = tmp / "boot-id"; boot.write_text("boot-A\n")
    r = bash("echo done", CM_FORCE_INTERACTIVE="1", CM_UPTIME_FILE=str(uptime_low), CM_RESTORE_CMD=f"touch {marker}", CM_TMUX_ARGS="-L cm-no-such-server", CM_BOOT_ID_FILE=str(boot))
    T.check("W8b first shell of a boot asks", marker.exists(), r.stdout + r.stderr)
    marker.unlink(missing_ok=True)
    r = bash("echo done", CM_FORCE_INTERACTIVE="1", CM_UPTIME_FILE=str(uptime_low), CM_RESTORE_CMD=f"touch {marker}", CM_TMUX_ARGS="-L cm-no-such-server", CM_BOOT_ID_FILE=str(boot))
    T.check("W8b second shell of the same boot does not ask again", not marker.exists() and "done" in r.stdout, r.stdout + r.stderr)
    boot.write_text("boot-B\n")
    r = bash("echo done", CM_FORCE_INTERACTIVE="1", CM_UPTIME_FILE=str(uptime_low), CM_RESTORE_CMD=f"touch {marker}", CM_TMUX_ARGS="-L cm-no-such-server", CM_BOOT_ID_FILE=str(boot))
    T.check("W8b a new boot asks again", marker.exists(), r.stdout + r.stderr)

T.rm(str(tmp))
T.finish()
