#!/usr/bin/env python3
"""Una sessione lanciata da una sessione Claude salva la sua trascrizione (24/11/2026, supervisor-lancio-trascrizione-24-11).

Il 24/11 alle 12:43 derattizzazione, lanciata con supervisor launch dal Bash della master, diceva «Transcript saving is
off — inherited CLAUDE_CODE_CHILD_SESSION marker». Claude Code (2.1.331) mette CLAUDE_CODE_CHILD_SESSION=1 nella shell
delle sue sessioni; una sessione interattiva che lo eredita non salva la trascrizione, salvo che sia in tmux e
`tmux show-environment -g` (250 ms al massimo) lo trovi nell'ambiente globale («ambient»). Le altre tre sessioni
avevano lo stesso segno e scrivevano: la prova le vedeva ambient. Col Chromebook carico la prova scade («unknown») e
la trascrizione si spegne. Il lancio ora toglie il segno: launch (tmux e --bg; restart, restore, il telefono, il
servizio e il cron passano da li'), lo sportello e il .cmd dell'host win.

Il claude finto di qui segue la regola di Claude Code (e5t nel binario 2.1.331): segno e niente
CLAUDE_CODE_FORCE_SESSION_PERSISTENCE → la trascrizione solo se la prova di tmux risponde ambient; FAKE_PROBE=slow e' la
prova scaduta.

LT1 il server tmux nato da una shell di Claude (il segno nel suo ambiente globale), la prova scaduta: la sessione
    lanciata non ha il segno e scrive il suo .jsonl
LT2 il claude finto segue la regola: senza segno scrive; col segno e la prova ambient scrive; col segno e la prova
    scaduta no; col segno e FORCE scrive
LT3 --bg lanciato da una shell di Claude: claude parte senza il segno
LT4 il resto dell'ambiente del lancio resta: CLAUDE_CONFIG_DIR per l'account fuori da ~/.claude
LT5 ogni riga di cm-launch.sh che avvia claude (tmux e --bg) e lo sportello di cm-cloud.sh tolgono il segno
LT6 host win: il .cmd di session_start svuota CLAUDE_CODE_CHILD_SESSION prima di claude, per ogni account
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

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude/sessions", ".claude-pixel/sessions", "ws/personali/alfa", "ws/personali/beta", "ws/personali/gamma", "ws/pro/delta"):
    (home / d).mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "default_account": "personale",
    "folder_map": [{"path": str(home / "ws" / "pro"), "account": "professionale"},
                   {"path": str(home / "ws" / "personali"), "account": "personale"}],
    "session": {"dialog_patterns": [], "claude_args": [], "startup_timeout_s": 25, "death_check_s": 1},
    "terminal": {"backend": "fake"}, "registry": {"file": str(tmp / "registry.json")},
    "tabs": {"color_registry": str(tmp / "colors")}, "sessions": {"max_sessions": 50}}))
LOG = tmp / "lt.log"
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
WRAP = tmp / "claude-trascrizione.sh"
WRAP.write_text(r"""#!/bin/bash
# la regola della trascrizione di Claude Code 2.1.331 per una sessione interattiva, poi il claude finto dei test
cfg="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"; name=""; prev=""; bg=no
for a in "$@"; do [ "$prev" = -n ] && name="$a"; [ "$a" = --bg ] && bg=yes; prev="$a"; done
persist=1; probe=-
if [ -n "${CLAUDE_CODE_CHILD_SESSION:-}" ] && [ -z "${CLAUDE_CODE_FORCE_SESSION_PERSISTENCE:-}" ]; then
  probe=absent
  if [ -n "${TMUX:-}" ]; then
    if [ "${FAKE_PROBE:-}" = slow ]; then probe=unknown
    elif tmux show-environment -g 2>/dev/null | grep -q '^CLAUDE_CODE_CHILD_SESSION='; then probe=ambient; fi
  fi
  [ "$probe" = ambient ] || persist=0
fi
echo "$name bg=$bg child=${CLAUDE_CODE_CHILD_SESSION:-} probe=$probe persist=$persist cfg=$cfg" >> "$LT_LOG"
if [ "$bg" = no ] && [ "$persist" = 1 ]; then
  slug="$(pwd | sed 's#[^A-Za-z0-9]#-#g')"; mkdir -p "$cfg/projects/$slug"
  echo '{"type":"user","message":{"role":"user","content":"ciao"}}' > "$cfg/projects/$slug/$name.jsonl"
fi
exec "@FAKE@" "$@"
""".replace("@FAKE@", str(FAKE)))
WRAP.chmod(0o755)


def jsonl(cfg_dir, folder, name):
    return Path(cfg_dir) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(folder)) / f"{name}.jsonl"


def logline(name):
    return next((ln for ln in reversed(LOG.read_text().splitlines()) if ln.startswith(name + " ")), "") if LOG.exists() else ""


with T.PrivateTmux() as tm:
    base = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
            "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(WRAP), "FAKE_CLAUDE_SCENARIO": "plain",
            "FAKE_CLAUDE_ARGS_LOG": str(tmp / "args.log"), "CM_TERMINAL_FAKE_LOG": str(tmp / "term.log"), "LT_LOG": str(LOG),
            "FAKE_CLAUDE_DELAY": "1"}
    claude_shell = dict(base, CLAUDE_CODE_CHILD_SESSION="1", CLAUDECODE="1", FAKE_PROBE="slow")
    # il server tmux nasce dalla shell di una sessione Claude: il segno e' nel suo ambiente globale
    subprocess.run(["tmux", *tm.env["CM_TMUX_ARGS"].split(), "new-session", "-d", "-s", "seed", "sleep", "600"], env=claude_shell, check=True)
    glob = tm("show-environment", "-g").stdout

    def launch(folder, *extra, env=claude_shell):
        return subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), str(folder), "--no-window", *extra], capture_output=True, text=True,
                              env=env, timeout=120)

    alfa = home / "ws" / "personali" / "alfa"
    r = launch(alfa)
    pid = tm("list-panes", "-t", "=alfa", "-F", "#{pane_pid}").stdout.strip() or 0
    penv = T.proc_env(pid) if pid else ""
    T.check("LT1 tmux server born in a Claude Code shell (the marker in its global environment), the tmux probe timed out: the launched session has no marker and writes its .jsonl",
            "CLAUDE_CODE_CHILD_SESSION=1" in glob and r.returncode == 0 and "CLAUDE_CODE_CHILD_SESSION=" not in penv
            and "persist=1" in logline("alfa") and jsonl(home / ".claude", alfa, "alfa").is_file(),
            f"rc {r.returncode} {r.stderr[-300:]} | {logline('alfa')} | pane marker: {'CLAUDE_CODE_CHILD_SESSION=' in penv}")

    # la regola del claude finto, provata sul claude finto da solo dentro tmux
    def direct(name, **e):
        cmd = " ".join(f"{k}={v}" for k, v in e.items())
        tm("new-session", "-d", "-s", name, "-c", str(alfa), f"env -u CLAUDE_CODE_CHILD_SESSION {cmd} {WRAP} -n {name}")
        T.wait_until(lambda: bool(logline(name)), 15)
        return logline(name)
    # dal 24/11 (supervisor-lancio-ambiente-24-11) il lancio di LT1 toglie il segno dall'ambiente globale: lo si rimette per
    # provare la regola con la prova ambient
    tm("set-environment", "-g", "CLAUDE_CODE_CHILD_SESSION", "1")
    tm("set-environment", "-g", "FAKE_PROBE", "fast")
    no_mark = direct("lt2a")
    ambient = direct("lt2b", CLAUDE_CODE_CHILD_SESSION="1")
    tm("set-environment", "-g", "FAKE_PROBE", "slow")
    timed_out = direct("lt2c", CLAUDE_CODE_CHILD_SESSION="1")
    forced = direct("lt2d", CLAUDE_CODE_CHILD_SESSION="1", CLAUDE_CODE_FORCE_SESSION_PERSISTENCE="1")
    T.check("LT2 the fake follows Claude Code's rule: no marker writes; marker with the probe ambient writes; marker with the probe timed out does not; marker with FORCE writes",
            "persist=1" in no_mark and "probe=ambient persist=1" in ambient and "probe=unknown persist=0" in timed_out and "persist=1" in forced,
            " | ".join((no_mark, ambient, timed_out, forced)))

    beta = home / "ws" / "personali" / "beta"
    r = launch(beta, "--bg")
    T.check("LT3 --bg launched from a Claude Code shell: claude starts without the marker",
            r.returncode == 0 and " bg=yes child= " in logline("beta"), f"rc {r.returncode} {r.stderr[-300:]} | {logline('beta')}")

    delta = home / "ws" / "pro" / "delta"
    r = launch(delta)
    pid = tm("list-panes", "-t", "=pix-delta", "-F", "#{pane_pid}").stdout.strip() or 0
    penv = T.proc_env(pid) if pid else ""
    T.check("LT4 the rest of the launch's environment stays: CLAUDE_CONFIG_DIR for the account outside ~/.claude, and its .jsonl there",
            r.returncode == 0 and f"CLAUDE_CONFIG_DIR={home / '.claude-pixel'}" in penv and "CLAUDE_CODE_CHILD_SESSION=" not in penv
            and jsonl(home / ".claude-pixel", delta, "pix-delta").is_file(), f"rc {r.returncode} {r.stderr[-300:]} | {logline('pix-delta')}")

src = (T.SCRIPTS / "cm-launch.sh").read_text(encoding="utf-8")
runs = [ln for ln in src.splitlines() if '"$CLAUDE" ${' in ln and not ln.lstrip().startswith("#")]
LIB = (T.SCRIPTS / "cm-lib.sh").read_text(encoding="utf-8")
MARK = 'env "${CM_ENV_UNSET[@]}" '
desk = [ln for ln in (T.SCRIPTS / "cm-cloud.sh").read_text(encoding="utf-8").splitlines() if '"$CLAUDE" remote-control' in ln]
T.check("LT5 every line of cm-launch.sh that starts claude (tmux and --bg) and the desk of cm-cloud.sh drop the marker",
        # dal 24/11 (supervisor-lancio-ambiente-24-11) il segno si toglie con le altre variabili figlie: CM_ENV_UNSET di cm-lib.sh
        len(runs) == 2 and all(MARK in ln for ln in runs) and desk and all(MARK in ln for ln in desk)
        and " CLAUDE_CODE_CHILD_SESSION " in " " + (re.search(r'^CM_CHILD_ENV="([^"]*)"', LIB, re.M) or [None, ""])[1] + " ",
        "\n".join(runs + desk))

sp = importlib.util.spec_from_file_location("cm_adapters_lt", T.SCRIPTS / "cm-adapters.py")
AD = importlib.util.module_from_spec(sp)
sp.loader.exec_module(AD)


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
T.check("LT6 win host: the .cmd of session_start empties CLAUDE_CODE_CHILD_SESSION before claude, for both accounts",
        all('set `"CLAUDE_CODE_CHILD_SESSION=`"`r`n' in ln and ln.index("CLAUDE_CODE_CHILD_SESSION") < ln.index("claude --remote-control") for ln in lines),
        "\n".join(x[:300] for x in lines))
T.rm(str(tmp))
T.finish()
