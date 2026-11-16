#!/usr/bin/env python3
"""La sessione della radice quando la sua cartella e' di un account con un prefisso (16/11/2026,
supervisor-master-prefisso-16-11).

Trovato cercando un «senza master» che aveva un'altra causa: con la cartella dei progetti di un account che ha un
tmux_prefix, `launch` e il wrapper della shell chiamavano la sessione «<prefisso>master», e tutto il resto di supervisor
(hook, relay, casella, conteggi) cerca la master col nome nudo. Home temporanea, due account, claude finto.

MC1  `launch <radice>` con la radice dell'account col prefisso «pix-»: la sessione tmux si chiama «master», claude
     riceve `-n master`, e gira con la configurazione di quell'account
MC2  supervisor la riconosce: `sessions` la mostra come «master» con l'account giusto, e non la conta fra le sessioni
     al lavoro (la master non passa dal tetto)
MC3  un progetto dello stesso account prende ancora il prefisso: «pix-delta»
MC4  l'account della master si sa dalla cartella: `color master` da' la forma dell'account della radice (il quadrato),
     e cm-guard lo deduce allo stesso modo; un nome qualunque senza prefisso resta dell'account senza prefisso
MC5  una master aperta prima dell'aggiornamento, col nome vecchio «pix-master»: `launch <radice>` non ne apre una
     seconda, dice di chiuderla e riaprirla, esce con 6
MC6  dove la radice e' dell'account senza prefisso non cambia niente: «master», come prima
MC7  il wrapper della shell fa lo stesso: `claude-pro` lanciato a mano nella radice apre «master»; in una cartella di
     progetto «pix-<cartella>»
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
home = tmp / "home"
for d in (".claude/sessions", ".claude-pixel/sessions", "ws/delta", "ws2"):
    (home / d).mkdir(parents=True)
cfg = tmp / "config.json"
argslog = tmp / "args.log"
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


def conf(root, root_account):
    cfg.write_text(json.dumps({
        "language": "it", "state_dir": str(tmp / "state"),
        "workspace": {"root": str(root)},
        "accounts": {"personale": {"config_dir": str(home / ".claude"), "shape": "circle"},
                     "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-", "shape": "square"}},
        "folder_map": [{"path": str(root), "account": root_account}],
        "session": {"dialog_patterns": ["trust (this|the) folder"], "startup_timeout_s": 20, "death_check_s": 1},
        "sessions": {"max_sessions": 50},
        "terminal": {"backend": "none"},
        "registry": {"file": str(tmp / "registry.json")},
        "tabs": {"color_registry": str(tmp / "colors")},
        "relay": {"enabled": False},
    }))


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(FAKE), "CM_PROC_SCAN_PIDS": "",
         "FAKE_CLAUDE_ARGS_LOG": str(argslog), "FAKE_CLAUDE_ECHO_ENV": "CLAUDE_CONFIG_DIR"}
    e.update(extra)
    return e


def run(script, *args, **extra):
    exe = [sys.executable, str(T.SCRIPTS / script)] if script.endswith(".py") else [str(T.SCRIPTS / script)]
    return subprocess.run(exe + list(args), capture_output=True, text=True, env=env(**extra), timeout=120)


def names():
    return sorted(x for x in tm("list-sessions", "-F", "#{session_name}").stdout.split() if x != "seed")


def last_args():
    return argslog.read_text().splitlines()[-1] if argslog.exists() else ""


ws = home / "ws"
with T.PrivateTmux() as tm:
    # il server tmux nasce con l'ambiente del test: le sessioni lo ereditano (il claude finto scrive li' i suoi argomenti)
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "seed", "bash", "--norc"], env=env(), check=True)

    # MC1
    conf(ws, "professionale")
    r = run("cm-launch.sh", str(ws), "--no-window")
    la = last_args()
    T.check("MC1 launch of the root folder, which belongs to the account with the prefix «pix-»: the tmux session is «master», not «pix-master»",
            r.returncode == 0 and names() == ["master"], f"{r.returncode} {names()} {r.stdout[-300:]} {r.stderr[-300:]}")
    T.check("MC1 claude gets -n master and runs with that account's configuration",
            "-n master" in la and "pix-master" not in la and f"CLAUDE_CONFIG_DIR={home / '.claude-pixel'}" in la, la)

    # MC2
    rows = json.loads(run("cm-sessions.py", "--json", "--no-screen").stdout or "[]")
    row = next((x for x in rows if (x.get("tmux") or x.get("name")) == "master"), {})
    cnt = run("cm-sessions.py", "--count-active")
    T.check("MC2 supervisor knows it: sessions shows «master» with the root's account, and it is not counted among the sessions at work",
            row.get("account") == "professionale" and row.get("name") == "master" and cnt.stdout.strip() == "0",
            f"{row} | count={cnt.stdout.strip()!r} {cnt.stderr[-200:]}")

    # MC3
    r = run("cm-launch.sh", str(ws / "delta"), "--no-window")
    T.check("MC3 a project of the same account still takes the prefix: «pix-delta»", r.returncode == 0 and names() == ["master", "pix-delta"] and "-n pix-delta" in last_args(),
            f"{names()} {last_args()} {r.stderr[-300:]}")

    # MC4
    r = run("cm-color.sh", "master")
    spec = importlib.util.spec_from_file_location("cm_guard_mc", T.SCRIPTS / "cm-guard.py")
    os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home)})
    G = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(G)
    squares = "🟥🟧🟨🟩🟦🟪⬜"
    T.check("MC4 the master's account comes from its folder: `color master` gives the square of the root's account, not the circle",
            r.returncode == 0 and any(c in r.stdout for c in squares) and not any(c in r.stdout for c in "🔴🟠🟡🟢🔵🟣⚪"), r.stdout + r.stderr)
    T.check("MC4 cm-guard deduces it the same way; a plain name with no prefix stays with the account that has none, a «pix-» name with its own",
            G.account_of_name("master") == "professionale" and G.account_of_name("alfa") == "personale" and G.account_of_name("pix-delta") == "professionale",
            f"{G.account_of_name('master')} {G.account_of_name('alfa')} {G.account_of_name('pix-delta')}")

    # MC5: la master aperta prima dell'aggiornamento
    run("cm-close.sh", "master") if (T.SCRIPTS / "cm-close.sh").exists() else None
    tm("kill-session", "-t", "=master")
    tm("kill-session", "-t", "=pix-delta")
    time.sleep(1)
    tm("new-session", "-d", "-s", "pix-master", "-c", str(ws), "bash", "--norc")
    n_args = len(argslog.read_text().splitlines())
    r = run("cm-launch.sh", str(ws), "--no-window")
    T.check("MC5 a master opened before the update, still «pix-master»: launch opens no second one, says to close it and launch again, exit 6",
            r.returncode == 6 and names() == ["pix-master"] and "pix-master" in r.stderr and "supervisor close pix-master" in r.stderr and "«master»" in r.stderr
            and len(argslog.read_text().splitlines()) == n_args, f"{r.returncode} {names()} {r.stderr}")
    tm("kill-session", "-t", "=pix-master")

    # MC6: la radice dell'account senza prefisso
    conf(home / "ws2", "personale")
    r = run("cm-launch.sh", str(home / "ws2"), "--no-window")
    la = last_args()
    T.check("MC6 where the root belongs to the account with no prefix nothing changes: «master», that account's configuration",
            r.returncode == 0 and names() == ["master"] and "-n master" in la and f"CLAUDE_CONFIG_DIR={home / '.claude-pixel'}" not in la, f"{names()} {la} {r.stderr[-300:]}")

    # MC7: il wrapper della shell (serve un tty per l'attach: dentro un runner)
    tm("kill-session", "-t", "=master")
    conf(ws, "professionale")
    c7 = json.loads(cfg.read_text())
    c7["shell"] = {"wrappers": {"claude": "personale", "claude-pro": "professionale"}}
    cfg.write_text(json.dumps(c7))
    os.symlink(FAKE, home / "claude-bin")
    SH = T.PLUGIN / "shell" / "supervisor.sh"
    for where, name in ((ws, "master"), (ws / "delta", "pix-delta")):
        runner = (f"env -u TMUX PATH='{os.environ['PATH']}' HOME='{home}' CM_HOME='{home}' CC_SUPERVISOR_CONFIG='{cfg}' CM_TMUX_ARGS='{tm.env['CM_TMUX_ARGS']}' "
                  f"CM_CLAUDE_BIN='{FAKE}' FAKE_CLAUDE_ARGS_LOG='{argslog}' bash -c 'source \"{SH}\"; cd \"{where}\"; claude-pro'; sleep 4")
        tm("new-session", "-d", "-s", "runner", "-x", "100", "-y", "24", runner)
        ok = T.wait_until(lambda: tm("has-session", "-t", f"={name}").returncode == 0 and f"-n {name}" in last_args(), timeout=15)
        T.check(f"MC7 the shell wrapper, `claude-pro` typed by hand in {'the root' if name == 'master' else 'a project folder'}: the session is «{name}»",
                ok and f"--remote-control {name}" in last_args(), f"{names()} {last_args()} " + tm("capture-pane", "-p", "-t", "runner").stdout[-200:])
        tm("kill-session", "-t", "=runner")
        tm("kill-session", "-t", f"={name}")

T.rm(str(tmp))
T.finish()
