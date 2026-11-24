#!/usr/bin/env python3
"""Una sessione lanciata da supervisor non eredita le variabili Claude Code di un'altra sessione (24/11/2026,
supervisor-lancio-ambiente-24-11).

Sul Chromebook `tmux show-environment -g` aveva 16 variabili CLAUDE* della master 27560c52 del 20/11, e la 0.8.209 ne
toglieva solo CLAUDE_CODE_CHILD_SESSION: derattizzazione, lanciata alle 14:35, aveva le altre 15 nel suo ambiente, con
CLAUDE_CODE_SESSION_ID della master. Con CLAUDE_CODE_EFFORT_LEVEL=max e CLAUDE_EFFORT=max ogni sessione lanciata girava a
«Opus 5.5 max», mentre i settings vogliono high (scelta del maintainer del 12/11: max solo per la master), anche sulla quota
condivisa coi colleghi. La master stessa tornava a max dopo un riavvio solo per quella variabile. Ora il lancio toglie tutte
le variabili figlie (CM_CHILD_ENV in cm-lib.sh), anche dall'ambiente globale del server tmux, e la radice senza --profile
prende workspace.root_profile: la master ha max dal suo profilo, le altre lo sforzo dei settings.

LA1 il server tmux nato dalla shell della master (le 16 variabili nel suo ambiente globale): la sessione lanciata non ne
    ha nessuna, tiene CLAUDE_CODE_DISABLE_CFC_PROMPT e parte senza --effort (lo sforzo dei settings)
LA2 dopo il lancio l'ambiente globale del server tmux non ha piu' le variabili figlie; CLAUDE_CODE_DISABLE_CFC_PROMPT resta
LA3 --bg dalla shell della master: claude parte senza le variabili figlie
LA4 la master rilanciata come la rilancia `supervisor restart` (--continue --force --prompt, senza --profile) prende
    workspace.root_profile: --effort max e CLAUDE_CODE_EFFORT_LEVEL=max dal profilo, il resto dell'ambiente pulito
LA5 una sessione che non e' la radice non prende root_profile: niente --effort, niente CLAUDE_CODE_EFFORT_LEVEL
LA6 root_profile che non e' un profilo in config: la master parte lo stesso, senza profilo, e lo si dice
LA7 ogni riga che avvia claude o codex in cm-launch.sh e lo sportello di cm-cloud.sh usano CM_ENV_UNSET; CHILD_ENV di
    cm-adapters.py e' la stessa lista di CM_CHILD_ENV; la lista copre le 15 variabili figlie viste sul Chromebook e non
    tocca CLAUDE_CONFIG_DIR ne' CLAUDE_CODE_DISABLE_CFC_PROMPT
LA8 host win: il .cmd di session_start svuota ogni variabile figlia prima di claude, per ogni account, e tiene
    CLAUDE_CONFIG_DIR dell'account fuori da .claude
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

# l'ambiente della master 27560c52 del 20/11 nel server tmux del Chromebook (i valori sono finti)
MASTER_ENV = {
    "CLAUDECODE": "1", "CLAUDE_CODE_BRIDGE_SESSION_ID": "session_01master", "CLAUDE_CODE_CHILD_NOTES_DIR": "/tmp/notes-master",
    "CLAUDE_CODE_CHILD_SESSION": "1", "CLAUDE_CODE_DISABLE_CFC_PROMPT": "1", "CLAUDE_CODE_EFFORT_LEVEL": "max",
    "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/cc-msg.sock", "CLAUDE_CODE_MESSAGING_TOKEN": "tok",
    "CLAUDE_CODE_SESSION_ATTENDED": "1", "CLAUDE_CODE_SESSION_ID": "27560c52-0000-4000-8000-000000000000",
    "CLAUDE_EFFORT": "max", "CLAUDE_PID": "4242", "CLAUDE_PLUGIN_DATA": "/tmp/plugin-data", "CLAUDE_PLUGIN_ROOT": "/tmp/plugin-root",
    "CLAUDE_PROJECT_DIR": "/tmp/master-dir",
}
CHILD = sorted(k for k in MASTER_ENV if k != "CLAUDE_CODE_DISABLE_CFC_PROMPT")

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude/sessions", ".claude-pixel/sessions", "ws/personali/alfa", "ws/personali/beta", "ws/pro/delta"):
    (home / d).mkdir(parents=True)
cfg = tmp / "config.json"


def write_cfg(root_profile):
    cfg.write_text(json.dumps({
        "language": "it", "state_dir": str(tmp / "state"),
        "workspace": {"root": str(home / "ws"), "root_session_name": "master", "root_profile": root_profile},
        "accounts": {"personale": {"config_dir": str(home / ".claude")},
                     "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
        "default_account": "personale",
        "folder_map": [{"path": str(home / "ws" / "pro"), "account": "professionale"},
                       {"path": str(home / "ws" / "personali"), "account": "personale"}],
        "profiles": {"max": {"effort": "max", "env": {"CLAUDE_CODE_EFFORT_LEVEL": "max"}}},
        "session": {"dialog_patterns": [], "claude_args": [], "startup_timeout_s": 25, "death_check_s": 1},
        "terminal": {"backend": "fake"}, "registry": {"file": str(tmp / "registry.json")},
        "tabs": {"color_registry": str(tmp / "colors")}, "sessions": {"max_sessions": 50}}))


write_cfg("max")
LOG = tmp / "la.log"
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
WRAP = tmp / "claude-ambiente.sh"
WRAP.write_text(r"""#!/bin/bash
# scrive gli argomenti e le variabili CLAUDE* che claude riceve, poi il claude finto dei test
name=""; prev=""; bg=no
for a in "$@"; do [ "$prev" = -n ] && name="$a"; [ "$a" = --bg ] && bg=yes; prev="$a"; done
echo "$name bg=$bg args=[$*] env=[$(env | grep -E '^CLAUDE' | sort | tr '\n' ' ')]" >> "$LA_LOG"
exec "@FAKE@" "$@"
""".replace("@FAKE@", str(FAKE)))
WRAP.chmod(0o755)


def logline(name):
    return next((ln for ln in reversed(LOG.read_text().splitlines()) if ln.startswith(name + " ")), "") if LOG.exists() else ""


def envpart(line):
    m = re.search(r"env=\[(.*)\]$", line)
    return dict(kv.split("=", 1) for kv in (m.group(1).split() if m else []) if "=" in kv)


with T.PrivateTmux() as tm:
    base = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
            "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(WRAP), "FAKE_CLAUDE_SCENARIO": "plain",
            "FAKE_CLAUDE_ARGS_LOG": str(tmp / "args.log"), "CM_TERMINAL_FAKE_LOG": str(tmp / "term.log"), "LA_LOG": str(LOG),
            "FAKE_CLAUDE_DELAY": "1"}
    master_shell = dict(base, **MASTER_ENV)
    # il server tmux nasce dalla shell della master: le sue variabili nell'ambiente globale
    subprocess.run(["tmux", *tm.env["CM_TMUX_ARGS"].split(), "new-session", "-d", "-s", "seed", "sleep", "600"], env=master_shell, check=True)
    glob0 = tm("show-environment", "-g").stdout

    def launch(folder, *extra, env=master_shell):
        return subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), str(folder), "--no-window", *extra], capture_output=True, text=True,
                              env=env, timeout=120)

    def pane_env(name):
        pid = tm("list-panes", "-t", "=" + name, "-F", "#{pane_pid}").stdout.strip()
        return T.proc_env(pid) if pid else ""

    alfa = home / "ws" / "personali" / "alfa"
    r = launch(alfa)
    T.wait_until(lambda: bool(logline("alfa")), 15)
    got = envpart(logline("alfa"))
    penv = pane_env("alfa")
    T.check("LA1 tmux server born in the master's shell (its 16 variables in the global environment): the launched session has none of the child ones, keeps CLAUDE_CODE_DISABLE_CFC_PROMPT and starts without --effort",
            all(k + "=" in glob0 for k in MASTER_ENV) and r.returncode == 0 and bool(logline("alfa"))
            and not [k for k in CHILD if k in got] and not [k for k in CHILD if f"\n{k}=" in "\n" + penv]
            and got.get("CLAUDE_CODE_DISABLE_CFC_PROMPT") == "1" and "--effort" not in logline("alfa"),
            f"rc {r.returncode} {r.stderr[-300:]} | {logline('alfa')[:600]}")

    glob1 = tm("show-environment", "-g").stdout
    left = [k for k in CHILD if re.search(rf"^{k}=", glob1, re.M)]
    T.check("LA2 after the launch the tmux server's global environment has no child variable left; CLAUDE_CODE_DISABLE_CFC_PROMPT stays",
            not left and re.search(r"^CLAUDE_CODE_DISABLE_CFC_PROMPT=1$", glob1, re.M) is not None, f"left: {left}\n{glob1[:800]}")

    beta = home / "ws" / "personali" / "beta"
    r = launch(beta, "--bg")
    got = envpart(logline("beta"))
    T.check("LA3 --bg launched from the master's shell: claude starts without the child variables",
            r.returncode == 0 and " bg=yes " in logline("beta") and not [k for k in CHILD if k in got] and "--effort" not in logline("beta"),
            f"rc {r.returncode} {r.stderr[-300:]} | {logline('beta')[:600]}")

    # la master rilanciata come la rilancia cm-restart.sh (OPZ: --continue --force --prompt), senza --profile
    root = home / "ws"
    r = launch(root, "--continue", "--force", "--prompt", "riprendi")
    T.wait_until(lambda: bool(logline("master")), 15)
    got = envpart(logline("master"))
    T.check("LA4 the master relaunched as `supervisor restart` does (--continue --force --prompt, no --profile) takes workspace.root_profile: --effort max and CLAUDE_CODE_EFFORT_LEVEL=max from the profile, the rest clean",
            r.returncode == 0 and "--effort max" in logline("master") and got.get("CLAUDE_CODE_EFFORT_LEVEL") == "max"
            and not [k for k in CHILD if k in got and k != "CLAUDE_CODE_EFFORT_LEVEL"],
            f"rc {r.returncode} {r.stderr[-300:]} | {logline('master')[:600]}")

    delta = home / "ws" / "pro" / "delta"
    r = launch(delta)
    T.wait_until(lambda: bool(logline("pix-delta")), 15)
    got = envpart(logline("pix-delta"))
    T.check("LA5 a session that is not the root does not take root_profile: no --effort, no CLAUDE_CODE_EFFORT_LEVEL, its CLAUDE_CONFIG_DIR",
            r.returncode == 0 and "--effort" not in logline("pix-delta") and "CLAUDE_CODE_EFFORT_LEVEL" not in got and "CLAUDE_EFFORT" not in got
            and got.get("CLAUDE_CONFIG_DIR") == str(home / ".claude-pixel"), f"rc {r.returncode} {r.stderr[-300:]} | {logline('pix-delta')[:600]}")

    tm("kill-session", "-t", "=master")
    T.wait_until(lambda: tm("has-session", "-t", "=master").returncode != 0, 10)
    write_cfg("nessuno")
    LOG.write_text("")
    r = launch(root, "--force")
    T.wait_until(lambda: bool(logline("master")), 15)
    T.check("LA6 root_profile that is not a configured profile: the master starts anyway, without a profile, and says so",
            r.returncode == 0 and bool(logline("master")) and "--effort" not in logline("master") and "root_profile" in r.stderr and "nessuno" in r.stderr,
            f"rc {r.returncode} {r.stderr[-400:]} | {logline('master')[:400]}")

lib = (T.SCRIPTS / "cm-lib.sh").read_text(encoding="utf-8")
m = re.search(r'^CM_CHILD_ENV="([^"]*)"', lib, re.M)
lst = m.group(1).split() if m else []
src = (T.SCRIPTS / "cm-launch.sh").read_text(encoding="utf-8")
runs = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#") and ('"$CLAUDE" ${' in ln or '"$CODEX"' in ln) and " env " in ln]
desk = [ln for ln in (T.SCRIPTS / "cm-cloud.sh").read_text(encoding="utf-8").splitlines() if '"$CLAUDE" remote-control' in ln]
sp = importlib.util.spec_from_file_location("cm_adapters_la", T.SCRIPTS / "cm-adapters.py")
AD = importlib.util.module_from_spec(sp)
sp.loader.exec_module(AD)
T.check("LA7 every line that starts claude or codex in cm-launch.sh and the desk of cm-cloud.sh use CM_ENV_UNSET; CHILD_ENV of cm-adapters.py is CM_CHILD_ENV; the list covers the 15 child variables seen on the Chromebook and spares CLAUDE_CONFIG_DIR and CLAUDE_CODE_DISABLE_CFC_PROMPT",
        len(runs) == 3 and all('env "${CM_ENV_UNSET[@]}" ' in ln for ln in runs) and desk and all('env "${CM_ENV_UNSET[@]}" ' in ln for ln in desk)
        and list(AD.CHILD_ENV) == lst and set(CHILD) <= set(lst) and not {"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_DISABLE_CFC_PROMPT"} & set(lst)
        and "cm_tmux_clean_env" in src, "\n".join(runs + desk) + f"\n{lst}")


class Spy(AD.WindowsNative):
    def __init__(self):
        self.sent = []

    def ps_json(self, script, timeout=120):
        self.sent.append(script)
        return {"ok": True}


w = Spy()
w.session_start("alfa", "D:/cm-sessions/personali/alfa", ["--remote-control", "alfa"])
w.session_start("pix-beta", "D:/cm-sessions/studio/beta", ["--remote-control", "pix-beta"], ".claude-pixel")
lines = [next((ln for ln in s.splitlines() if "WriteAllText($cmdf" in ln), "") for s in w.sent]
T.check("LA8 win host: the .cmd of session_start empties every child variable before claude, for both accounts, and keeps CLAUDE_CONFIG_DIR of the account outside .claude",
        len(lines) == 2 and all(all(f'set `"{k}=`"`r`n' in ln and ln.index(f'set `"{k}=') < ln.index("claude --remote-control") for k in CHILD) for ln in lines)
        and "CLAUDE_CONFIG_DIR" not in lines[0] and 'set `"CLAUDE_CONFIG_DIR=%USERPROFILE%\\.claude-pixel`"' in lines[1],
        "\n".join(x[:400] for x in lines))
T.rm(str(tmp))
T.finish()
