#!/usr/bin/env python3
"""La domanda di fiducia della cartella non ferma piu' una sessione aperta da supervisor (25/11/2026, supervisor-fiducia-cartella-25-11).

Il maintainer alle 12:24: «spesso quando su una sessione appena avviata appare la scritta trust this folder? in supervisor non
appare niente, invece dovrebbe prima rispondere yes in automatico». Con session.dialog_patterns vuoto (il default dal 07/10)
launch non rispondeva mai alla fiducia. Ora la cartella si segna come fidata nel .claude.json dell'account prima di avviare
claude, e se la domanda compare lo stesso si risponde Yes; solo sotto la radice dei progetti (workspace.root).

FC1 account in ~/.claude, cartella sotto la radice, mai aperta: ~/.claude.json ha projects.<cartella>.hasTrustDialogAccepted
    (il resto del file com'era), claude parte senza domanda, registrata, la nota lo dice, nessun «Serve te»
FC2 il secondo account (CLAUDE_CONFIG_DIR): la fiducia va nel suo .claude.json, non in ~/.claude.json
FC3 la domanda compare lo stesso (trust-always) sotto la radice: Yes da solo, registrata, nessun «Serve te»
FC4 una cartella fuori dalla radice: niente scritto, nessun tasto, la domanda resta e va in «Serve te» (needs-you col testo)
FC5 i limiti: un link sotto la radice che porta fuori no, la radice si', la radice «/» mai; una gia' fidata non si riscrive
FC6 gli altri avvii passano da cm-launch.sh (restart, park, il rilancio di win) e win segna la fiducia sull'host prima del
    lancio (session_trust); il README dice la regola
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
ws = home / "ws"
for d in (".claude/sessions", ".claude-pixel/sessions", "ws/personali/alfa", "ws/personali/gamma", "ws/pro/beta"):
    (home / d).mkdir(parents=True)
out = tmp / "fuori"
out.mkdir()
(ws / "personali" / "link-fuori").symlink_to(out)
state = tmp / "state"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "folder_map": [{"path": str(ws / "pro"), "account": "professionale"}],
    "session": {"startup_timeout_s": 20, "death_check_s": 1},
    "relay": {"enabled": False, "dir": str(tmp / "relay")},
    "terminal": {"backend": "none"},
    "registry": {"file": str(tmp / "registry.json"), "good_file": str(tmp / "good.json")},
    "sessions": {"max_sessions": 50},
    "hooks": {"local_time": {"enabled": False}, "session_kernel": {"enabled": False}},
}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
MAIN_CJ = home / ".claude.json"
PIX_CJ = home / ".claude-pixel" / ".claude.json"
MAIN_CJ.write_text(json.dumps({"numStartups": 7, "userID": "u-1", "projects": {"/altrove": {"allowedTools": ["Bash"], "hasTrustDialogAccepted": False}}}, indent=2))
PIX_CJ.write_text(json.dumps({"numStartups": 2, "projects": {}}, indent=2))
scen = tmp / "scenario"


def cj(p):
    return json.loads(p.read_text())


def trusted(p, folder):
    return (cj(p).get("projects") or {}).get(str(folder), {}).get("hasTrustDialogAccepted") is True


def ny(name):
    try:
        return json.loads((state / "needs-you" / f"start-{name}.json").read_text())
    except (OSError, ValueError):
        return None


with T.PrivateTmux() as tm:
    BASE = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_HOOK_TMUX_NAME", "CLAUDE_CONFIG_DIR")}
    BASE.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_PROC_SCAN_PIDS": "",
                 "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(FAKE), "FAKE_CLAUDE_SCENARIO_FILE": str(scen)})

    def launch(folder, scenario="trust"):
        scen.write_text(scenario)
        r = subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), str(folder), "--no-window"], capture_output=True, text=True, env=BASE, timeout=120)
        name = next((l.split(":", 1)[1].strip() for l in r.stdout.splitlines() if "nome tmux:" in l), "")
        time.sleep(0.5)
        scr = tm("capture-pane", "-p", "-t", name).stdout if name else ""
        return r, name, scr

    def registered(conf):
        return any(json.loads(f.read_text()).get("status") for f in (home / conf / "sessions").glob("*.json"))

    alfa = ws / "personali" / "alfa"
    r1, n1, s1 = launch(alfa)
    T.check("FC1 account in ~/.claude, a folder under the root never opened: ~/.claude.json gets hasTrustDialogAccepted (the rest as it was), claude starts with no question, registered, the note says so, no «Serve te»",
            r1.returncode == 0 and trusted(MAIN_CJ, alfa) and cj(MAIN_CJ).get("numStartups") == 7 and cj(MAIN_CJ)["projects"]["/altrove"] == {"allowedTools": ["Bash"], "hasTrustDialogAccepted": False}
            and "Is this a project you" not in s1 and "Claude Code v" in s1 and registered(".claude") and "fidata" in r1.stdout and ny(n1) is None
            and "aspetta" not in r1.stdout.lower(),
            f"rc {r1.returncode} {cj(MAIN_CJ)}\n{r1.stdout[-500:]}{r1.stderr[-300:]}\n{s1[-300:]}")

    beta = ws / "pro" / "beta"
    r2, n2, s2 = launch(beta)
    T.check("FC2 the second account (CLAUDE_CONFIG_DIR): the trust goes into its own .claude.json, not ~/.claude.json",
            r2.returncode == 0 and n2.startswith("pix-") and trusted(PIX_CJ, beta) and not trusted(MAIN_CJ, beta) and cj(PIX_CJ).get("numStartups") == 2
            and "Is this a project you" not in s2 and registered(".claude-pixel"),
            f"rc {r2.returncode} {n2} {cj(PIX_CJ)}\n{r2.stdout[-400:]}{r2.stderr[-300:]}\n{s2[-300:]}")

    gamma = ws / "personali" / "gamma"
    r3, n3, s3 = launch(gamma, "trust-always")
    T.check("FC3 the question shows up anyway (trust-always) under the root: Yes by itself, registered, no «Serve te»",
            r3.returncode == 0 and "Yes, I trust" not in s3 and "Claude Code v" in s3 and ny(n3) is None and "aspetta" not in r3.stdout.lower(),
            f"rc {r3.returncode}\n{r3.stdout[-400:]}{r3.stderr[-300:]}\n{s3[-400:]}")

    before = MAIN_CJ.read_text()
    r4, n4, s4 = launch(out)
    got = ny(n4)
    T.check("FC4 a folder outside the root: nothing written, no key pressed, the question stays and goes to «Serve te» (needs-you with its text)",
            r4.returncode == 0 and MAIN_CJ.read_text() == before and "No, exit" in s4 and got is not None
            and "Is this a project you created or one you trust?" in str(got.get("question")) and "fidata" not in r4.stdout,
            f"rc {r4.returncode} {got}\n{r4.stdout[-400:]}{r4.stderr[-300:]}\n{s4[-300:]}")

    def tr(*a, cfgp=cfg):
        return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-trust.py"), *a], capture_output=True, text=True, timeout=60,
                              env=dict(BASE, CC_SUPERVISOR_CONFIG=str(cfgp)))
    slash = tmp / "config-slash.json"
    slash.write_text(json.dumps(dict(json.loads(cfg.read_text()), workspace={"root": "/", "root_session_name": "master"})))
    m0 = MAIN_CJ.stat().st_mtime_ns
    time.sleep(0.05)
    again = tr("ensure", str(alfa), str(home / ".claude")).stdout.strip()
    T.check("FC5 the limits: a link under the root that leads outside no, the root yes, root «/» never; an already trusted folder is not rewritten",
            tr("allowed", str(ws / "personali" / "link-fuori")).returncode == 1 and tr("ensure", str(ws / "personali" / "link-fuori"), str(home / ".claude")).stdout.strip() == "outside"
            and tr("allowed", str(ws)).returncode == 0 and tr("allowed", str(out)).returncode == 1 and tr("allowed", str(alfa), cfgp=slash).returncode == 1
            and again == "already" and MAIN_CJ.stat().st_mtime_ns == m0 and not trusted(MAIN_CJ, out) and not trusted(MAIN_CJ, ws / "personali" / "link-fuori"),
            f"{again} {cj(MAIN_CJ)}")

src = {n: (T.SCRIPTS / n).read_text() for n in ("cm-restart.sh", "cm-park.py", "cm-rsession.py")}
rs = src["cm-rsession.py"]
readme = (T.ROOT / "README.md").read_text()
T.check("FC6 the other starts go through cm-launch.sh (restart, park, the win relaunch) and win marks the trust on the host before launching (session_trust); the README says the rule",
        "cm-launch.sh" in src["cm-restart.sh"] and "cm-launch.sh" in src["cm-park.py"] and "cm-launch.sh" in rs and "a.session_trust(rdir, cdir)" in rs
        and "Folders under your projects root are trusted for you" in readme and "never trusted" in readme, "")
T.rm(str(tmp))
T.finish()
