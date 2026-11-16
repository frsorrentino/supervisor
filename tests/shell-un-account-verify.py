#!/usr/bin/env python3
"""shell/supervisor.sh con un solo account, su macOS e in zsh (16/11/2026, dalla segnalazione di un collega:
supervisor-wrapper-un-account-16-11, supervisor-ripristino-macos-16-11).

Tre difetti, tutti invisibili a tests/shell-verify.py, che ha due wrapper (uno col trattino), gira in bash e legge
l'uptime da un file come su Linux:
- con un solo account `--sh` non emetteva CM_SHELL_WRAPPERS: il wrapper `claude` non nasceva e claude partiva fuori da
  tmux («senza master» nell'app, «no session …» dal relay);
- su macOS l'ora di avvio si prendeva dai microsecondi di `sysctl kern.boottime`: il ripristino non scattava mai;
- in zsh il comando di ripristino diventava una parola sola.
zsh: quello nel PATH, o CM_TEST_ZSH; senza, i controlli in zsh sono saltati e lo si dice.

W1c  un solo wrapper {"claude": "<account>"}: `--sh` emette CM_SHELL_WRAPPERS (e CM_SHELL_ALIASES anche con alias
     tutti senza trattino); `claude` e' una funzione in bash (e in zsh); le variabili per chiave restano
W4c  con quel solo wrapper `claude` in una cartella apre la sessione tmux col nome della cartella; nella radice si
     chiama master; in zsh come in bash, con gli argomenti di session.claude_args (in zsh `read -ra` li perdeva, e
     CM_TMUX_ARGS non veniva spezzato)
W8c  strada macOS (sysctl finto nel PATH, CM_UPTIME_FILE non leggibile): avvio 5 minuti fa → il ripristino parte;
     avvio di ieri → no; l'ora presa e' quella di «sec», non i microsecondi di «usec»
W8d  in zsh: lo stesso innesco di W8 (uptime da file) fa partire il comando di ripristino, e anche la strada macOS
W8e  senza CM_RESTORE_CMD parte `<plugin>/scripts/supervisor restore`, in bash e in zsh (una copia della shell con
     un supervisor finto accanto)
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude/sessions", "ws/alfa", "bin", "mac"):
    (home / d).mkdir(parents=True)
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
os.symlink(FAKE, home / "bin" / "claude")
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "default_account": "personale",
    "session": {"claude_args": ["--dangerously-skip-permissions", "--verbose"]},
    "shell": {"wrappers": {"claude": "personale"}, "aliases": {"lancia": "launch", "sessioni": "sessions"}},
    "registry": {"file": str(tmp / "registry.json")},
    "restore": {"uptime_max_min": 15},
    "tabs": {"color_registry": str(tmp / "colors")},
}))
SH = T.PLUGIN / "shell" / "supervisor.sh"
argslog = tmp / "args.log"
uptime_low = tmp / "uptime-low"
uptime_low.write_text("300.0 100.0\n")
uptime_high = tmp / "uptime-high"
uptime_high.write_text("99999.0 100.0\n")
ZSH = os.environ.get("CM_TEST_ZSH") or shutil.which("zsh")
SHELLS = ["bash"] + ([ZSH] if ZSH else [])


def base_env(**extra):
    e = {"PATH": f"{home}/bin:{os.environ['PATH']}", "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "FAKE_CLAUDE_ARGS_LOG": str(argslog), "FAKE_CLAUDE_SCENARIO": "plain",
         "CM_UPTIME_FILE": str(uptime_high), "TERM": "xterm-256color", "LC_ALL": "C", "LANGUAGE": ""}
    e.update(extra)
    return e


def sh(shell, script, source=SH, **extra):
    """La shell (non interattiva, senza i file rc dell'utente) fa source del file e poi esegue lo script."""
    argv = [shell, "-c"] if shell == "bash" else [shell, "-f", "-c"]
    return subprocess.run(argv + [f'source "{source}"; {script}'], capture_output=True, text=True, env=base_env(**extra), timeout=60)


def nm(shell):
    return "bash" if shell == "bash" else "zsh"


def sysctl(boot):
    """Un sysctl finto come quello di macOS: `sysctl -n kern.boottime` → «{ sec = N, usec = 110223 } data»."""
    f = home / "mac" / "sysctl"
    f.write_text(f'#!/bin/sh\n[ "$1" = -n ] && [ "$2" = kern.boottime ] && echo "{{ sec = {int(boot)}, usec = 110223 }} Mon Nov 16 09:40:59 2026" && exit 0\nexit 1\n')
    f.chmod(0o755)
    return f"{home}/mac:{home}/bin:{os.environ['PATH']}"


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "seed", "bash", "--norc"], env=base_env(), check=True)
    if not ZSH:
        print("  --  zsh non trovata (PATH o CM_TEST_ZSH): i controlli in zsh sono saltati")

    # W1c
    out = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-config.py"), "--sh"], capture_output=True, text=True, env=base_env()).stdout
    T.check("W1c one wrapper, {\"claude\": account}: --sh emits the list CM_SHELL_WRAPPERS (claude<TAB>personale), and CM_SHELL_ALIASES although no alias has a dash; the per-key variables stay",
            "export CM_SHELL_WRAPPERS='claude\tpersonale'" in out and "export CM_SHELL_WRAPPERS_CLAUDE='personale'" in out
            and "export CM_SHELL_ALIASES='lancia\tlaunch\nsessioni\tsessions'" in out and "export CM_SHELL_ALIASES_LANCIA='launch'" in out,
            "\n".join(x for x in out.splitlines() if "CM_SHELL_" in x))
    for s in SHELLS:
        r = sh(s, "typeset -f claude >/dev/null && echo CLAUDE-FUNZIONE; alias lancia; claude --version")
        T.check(f"W1c in {nm(s)}: `claude` is a function (not the bare binary), the aliases are there, and --version passes through",
                "CLAUDE-FUNZIONE" in r.stdout and "supervisor launch" in r.stdout and "fake" in r.stdout, r.stdout + r.stderr)

    # W4c: il wrapper apre la sessione in tmux (serve un tty per l'attach: dentro un runner)
    for s in SHELLS:
        for where, name in ((home / "ws" / "alfa", "alfa"), (home / "ws", "master")):
            opt = "" if s == "bash" else " -f"
            runner = (f"env -u TMUX PATH='{home}/bin:{os.environ['PATH']}' HOME='{home}' CM_HOME='{home}' CC_SUPERVISOR_CONFIG='{cfg}' "
                      f"CM_TMUX_ARGS='{tm.env['CM_TMUX_ARGS']}' FAKE_CLAUDE_ARGS_LOG='{argslog}' {s}{opt} -c 'source \"{SH}\"; cd \"{where}\"; claude'; sleep 4")
            tm("new-session", "-d", "-s", "runner", "-x", "100", "-y", "24", runner)
            # la sessione esiste appena tmux la crea; gli argomenti il claude finto li scrive un attimo dopo: si aspettano tutti e due
            ok = T.wait_until(lambda: tm("has-session", "-t", f"={name}").returncode == 0 and argslog.exists()
                              and f"-n {name}" in (argslog.read_text().splitlines() or [""])[-1], timeout=15)
            last = argslog.read_text().splitlines()[-1] if argslog.exists() else ""
            T.check(f"W4c in {nm(s)}, one wrapper: `claude` in {'the root' if name == 'master' else 'a folder'} opens the tmux session «{name}», with -n, --remote-control and session.claude_args",
                    ok and f"-n {name}" in last and f"--remote-control {name}" in last and last.startswith("--dangerously-skip-permissions --verbose "), f"{tm('list-sessions', '-F', '#{session_name}').stdout.split()} {last} "
                    + tm("capture-pane", "-p", "-t", "runner").stdout[-300:])
            tm("kill-session", "-t", "=runner")
            tm("kill-session", "-t", f"={name}")

    # W8c: la strada macOS
    (tmp / "registry.json").write_text(json.dumps({"salvato": "x", "sessioni": [{"nome": "alfa", "cartella": str(home), "account": "personale"}]}))
    marker = tmp / "restored"
    no_tmux = "-L cm-no-such-server-ua"
    for s in SHELLS:
        boot = int(time.time()) - 300
        r = sh(s, "echo done", CM_FORCE_INTERACTIVE="1", PATH=sysctl(boot), CM_UPTIME_FILE=str(tmp / "no-uptime"), CM_RESTORE_CMD=f"touch {marker}", CM_TMUX_ARGS=no_tmux)
        asked = sorted(p.name for p in (tmp / "state" / "restore").glob("asked-*")) if (tmp / "state" / "restore").is_dir() else []
        T.check(f"W8c in {nm(s)}, the macOS way (a fake sysctl, no uptime file), booted 5 minutes ago: the restore command runs, and the boot time is the «sec» field, not usec's microseconds",
                marker.exists() and "done" in r.stdout and f"asked-boot-{boot}" in asked and "asked-boot-110223" not in asked, f"{r.stdout}{r.stderr} {asked}")
        marker.unlink(missing_ok=True)
        r = sh(s, "echo done", CM_FORCE_INTERACTIVE="1", PATH=sysctl(time.time() - 86400), CM_UPTIME_FILE=str(tmp / "no-uptime"), CM_RESTORE_CMD=f"touch {marker}", CM_TMUX_ARGS=no_tmux)
        T.check(f"W8c in {nm(s)}, booted yesterday: no restore", not marker.exists() and "done" in r.stdout, r.stdout + r.stderr)
        shutil.rmtree(tmp / "state" / "restore", ignore_errors=True)

    # W8d: zsh, uptime da file (la strada Linux): il comando di ripristino con uno spazio dentro
    if ZSH:
        boot_id = tmp / "boot-id"
        boot_id.write_text("boot-zsh\n")
        r = sh(ZSH, "echo done", CM_FORCE_INTERACTIVE="1", CM_UPTIME_FILE=str(uptime_low), CM_RESTORE_CMD=f"touch {marker}", CM_TMUX_ARGS=no_tmux, CM_BOOT_ID_FILE=str(boot_id))
        T.check("W8d in zsh, fresh boot + no tmux + registry: the restore command runs (before: «no such file or directory: touch …»)",
                marker.exists() and "done" in r.stdout and "no such file" not in r.stderr, r.stdout + r.stderr)
        marker.unlink(missing_ok=True)
        shutil.rmtree(tmp / "state" / "restore", ignore_errors=True)

    # W8e: il comando di default, senza CM_RESTORE_CMD
    copy = tmp / "plugin"
    (copy / "shell").mkdir(parents=True)
    shutil.copy(SH, copy / "shell" / "supervisor.sh")
    shutil.copytree(T.SCRIPTS, copy / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(T.PLUGIN / "messages", copy / "messages")
    (copy / "scripts" / "supervisor").write_text(f'#!/bin/sh\necho "$@" > "{marker}"\n')
    (copy / "scripts" / "supervisor").chmod(0o755)
    for i, s in enumerate(SHELLS):
        bid = tmp / f"boot-e{i}"
        bid.write_text(f"boot-e{i}\n")
        r = sh(s, "echo done", source=copy / "shell" / "supervisor.sh", CM_FORCE_INTERACTIVE="1", CM_UPTIME_FILE=str(uptime_low), CM_TMUX_ARGS=no_tmux, CM_BOOT_ID_FILE=str(bid))
        T.check(f"W8e in {nm(s)}, no CM_RESTORE_CMD: `<plugin>/scripts/supervisor restore` runs, as two words",
                marker.exists() and marker.read_text().strip() == "restore" and "done" in r.stdout, f"{marker.read_text() if marker.exists() else '-'} {r.stdout}{r.stderr}")
        marker.unlink(missing_ok=True)

T.rm(str(tmp))
T.finish()
