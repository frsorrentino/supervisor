#!/usr/bin/env python3
"""Verifica la modalità SCHEDA (mandato dell'utente 11/09 13:20: «sempre tutte schede»): launch/restart/restore
aprono la sessione come scheda di una finestra app del Terminale già aperta (duplicazione + segnaposto,
come merge), garcon solo quando non c'è nessuna finestra, e allora UNA sola per tutte. Bridge finto,
garcon finto (aggiunge una finestra al mondo del bridge e attacca un client come farebbe la shell nuova),
tmux privato, claude finto.

O1  finestra app con una shell duplicabile: `cm-terminal.sh open alfa ephemeral` → duplicate nella finestra,
    client NUOVO su alfa, conteggio finestre invariato, garcon NON chiamato; preferita la finestra senza #home
O2  nessuna finestra del Terminale: garcon chiamato UNA volta, senza comando (finestra semplice + segnaposto),
    client attaccato, finestre +1
O3  subito dopo, beta → scheda nella finestra nata in O2 (la sua shell è sorgente), garcon non chiamato
O4  terminal.open_as_tab false → garcon col comando attach (comportamento precedente)
O5  restore --dry-run in modalità scheda dice «schede» e la finestra
O6  launch intero (claude finto) con una finestra aperta → «attaccata», nessuna finestra nuova
O7  SFRATTO della #home (chrome-bridge ed24b83, l'utente 15:25): la finestra nata da garcon porta la #home; dopo
    la prima sessione la home viene portata via da sola in un popup (move_tab) e chiusa lì: nessuna finestra
    con sessioni mostra #home (O2, O3, O6); una finestra con la sola #home + shell e nessuna sessione resta
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
for d in (".claude/sessions", "ws/personali/alfa", "ws/personali/beta", "ws/personali/gamma"):
    (home / d).mkdir(parents=True)
state = tmp / "state"; state.mkdir()
bridge_state = tmp / "bridge.json"
URL = "chrome-untrusted://terminal/html/terminal.html"
NATIVE = {"left": 0, "top": 0, "width": 1536, "height": 864}
garcon_log = tmp / "garcon.log"
fake_garcon = T.ROOT / "tests" / "lib" / "fake-garcon.py"
cfg = tmp / "config.json"


def write_cfg(as_tab=True):
    cfg.write_text(json.dumps({
        "language": "it", "sessions": {"max_sessions": 50}, "state_dir": str(state),
        "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
        "accounts": {"personale": {"config_dir": str(home / ".claude")}},
        "session": {"dialog_patterns": ["trust (this|the) folder", "Is this a project you", "Bypass Permissions mode"], "startup_timeout_s": 25, "death_check_s": 1},
        "terminal": {"backend": "chromeos", "garcon": str(fake_garcon), "attach_wait_s": 8, "attach_retry_wait_s": 4, "open_as_tab": as_tab},
        "tile": {"chrome_bridge_cli": str(T.ROOT / "tests" / "lib" / "fake-bridge.py"), "placeholder_file": str(state / "next-session"),
                 "monitor_registry": str(state / "monitors.json"), "new_client_wait_s": 8, "window_open_wait_s": 5},
        "registry": {"file": str(tmp / "registry.json"), "good_file": str(tmp / "good.json")},
        "restore": {"last": "master"},
        "tabs": {"color_registry": str(tmp / "colors")},
    }))


write_cfg(True)
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "FAKE_BRIDGE_STATE": str(bridge_state), "FAKE_BRIDGE_PLACEHOLDER": str(state / "next-session"),
         "FAKE_BRIDGE_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "FAKE_GARCON_LOG": str(garcon_log), "WAYLAND_DISPLAY": "wl-0", "CM_PROC_SCAN_PIDS": "", "CM_CLAUDE_BIN": str(FAKE)}
    e.update(extra)
    return e


def world(windows, tabs):
    bridge_state.write_text(json.dumps({"windows": windows, "tabs": tabs, "monitors": [NATIVE], "calls": []}))


def wstate():
    return json.load(open(bridge_state))


def calls():
    return wstate()["calls"]


def term_open(name, **extra):
    return subprocess.run([str(T.SCRIPTS / "cm-terminal.sh"), "open", name, "ephemeral"], capture_output=True, text=True, env=env(**extra), timeout=120)


def homes_with_sessions():
    """#home nelle finestre che contengono almeno una scheda di sessione"""
    st = wstate()
    with_sess = {t["windowId"] for t in st["tabs"] if "args[]=attach" in t["url"]}
    return [t for t in st["tabs"] if "#home" in t["url"] and t["windowId"] in with_sess]


def clients(name):
    return [l for l in tm("list-clients", "-F", "#{client_session}").stdout.splitlines() if l == name]


with T.PrivateTmux() as tm:
    for s in ("alfa", "beta", "gamma"):
        # 11/11: il server nasce con l'HOME del test, non con quello vero: il claude finto lanciato poi in O6 scriveva il suo
        # registro in ~/.claude/sessions della macchina, launch non lo trovava (e usciva 0 lo stesso, fino alla 0.8.147)
        tm("new-session", "-d", "-s", s, "bash", "--norc", env=env())
    # O1: finestra 5 (app) con #home + shell duplicabile; finestra 8 (app) solo shell → si preferisce la 8
    world([{"id": 5, "type": "app", "state": "normal", "left": 0, "top": 0, "width": 1536, "height": 864},
           {"id": 8, "type": "app", "state": "normal", "left": 100, "top": 100, "width": 900, "height": 600}],
          [{"id": 50, "windowId": 5, "url": URL + "#home", "title": "Terminal", "active": False},
           {"id": 52, "windowId": 5, "url": URL, "title": "Terminal", "active": True},
           {"id": 80, "windowId": 8, "url": URL, "title": "Terminal", "active": True}])
    r = term_open("alfa")
    dup = [c["params"].get("tab_id") for c in calls() if c["cmd"] == "tab_action" and c["params"].get("action") == "duplicate"]
    T.check("O1 open as tab: duplicate of the shell in the home-less window 8, exit 0", r.returncode == 0 and dup == [80], r.stdout + r.stderr + str(calls()[-4:]))
    T.check("O1 a new client attached to alfa, window count unchanged (2), garcon not called", len(clients("alfa")) == 1 and len(wstate()["windows"]) == 2 and not garcon_log.exists(), tm("list-clients").stdout + str(len(wstate()["windows"])))
    T.check("O1 the new tab carries alfa in its URL", any("args[]=alfa" in t["url"] for t in wstate()["tabs"] if t["windowId"] == 8), str(wstate()["tabs"]))
    # O2: nessuna finestra del Terminale → garcon UNA volta senza comando, con segnaposto
    world([], [])
    r = term_open("beta")
    T.check("O2 no Terminal window: garcon called once, plain (no attach command), exit 0", r.returncode == 0 and garcon_log.exists() and len(garcon_log.read_text().splitlines()) == 1 and "attach" not in garcon_log.read_text(), r.stdout + r.stderr + (garcon_log.read_text() if garcon_log.exists() else "-"))
    T.check("O2 beta attached through the placeholder, one window", len(clients("beta")) == 1 and len(wstate()["windows"]) == 1 and not (state / "next-session").exists(), tm("list-clients").stdout + str(wstate()["windows"]))
    ev = [c for c in calls() if c["cmd"] == "move_tab" and c["params"].get("window_type") == "popup"]
    T.check("O7 the home of the garcon window evicted after the first session: move_tab to a popup + close, zero #home next to sessions, still one window", len(ev) == 1 and not homes_with_sessions() and len(wstate()["windows"]) == 1 and any(c["cmd"] == "tab_action" and c["params"].get("action") == "close" and c["params"].get("tab_id") == ev[0]["params"]["tab_id"] for c in calls()), str(calls()[-6:]) + str(wstate()["tabs"]))
    # O3: gamma → scheda nella finestra nata in O2
    r = term_open("gamma")
    T.check("O3 next session becomes a tab of that window: garcon still called once, 1 window, gamma attached", r.returncode == 0 and len(garcon_log.read_text().splitlines()) == 1 and len(wstate()["windows"]) == 1 and len(clients("gamma")) == 1, r.stdout + r.stderr + garcon_log.read_text() + str(wstate()["windows"]))
    T.check("O7 still no #home next to sessions after the second tab", not homes_with_sessions(), str(wstate()["tabs"]))
    # O4: open_as_tab false → garcon con attach (vecchio comportamento)
    write_cfg(False)
    tm("new-session", "-d", "-s", "delta", "bash", "--norc")
    r = term_open("delta")
    time.sleep(2)
    T.check("O4 open_as_tab false: garcon with the attach command, a new window", r.returncode == 0 and "attach delta" in garcon_log.read_text().splitlines()[-1] and len(wstate()["windows"]) == 2, r.stdout + r.stderr + garcon_log.read_text())
    write_cfg(True)
    # O5: restore --dry-run in modalità scheda
    (tmp / "registry.json").write_text(json.dumps({"salvato": "2026-09-11T09:00:00+0200", "sessioni": [
        {"nome": "epsilon", "cartella": str(home / "ws" / "personali" / "alfa"), "account": "personale"},
        {"nome": "zeta", "cartella": str(home / "ws" / "personali" / "beta"), "account": "personale"}]}))
    r = subprocess.run([str(T.SCRIPTS / "cm-restore.sh"), "--dry-run"], capture_output=True, text=True, env=env(), timeout=60)
    T.check("O5 restore --dry-run says the sessions open as tabs in the window", r.returncode == 0 and "(2)" in r.stdout and "schede" in r.stdout, r.stdout + r.stderr)
    # O6: launch intero con una finestra aperta → scheda, nessuna finestra nuova
    scen = tmp / "scen"; scen.write_text("plain")
    n_win = len(wstate()["windows"]); n_garcon = len(garcon_log.read_text().splitlines())
    r = subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), str(home / "ws" / "personali" / "gamma")], capture_output=True, text=True,
                       env=env(FAKE_CLAUDE_SCENARIO_FILE=str(scen), FAKE_CLAUDE_ARGS_LOG=str(tmp / "args.log")), timeout=150)
    T.check("O6 launch with a window open: attached as a tab, no new window, garcon not called", r.returncode == 0 and "attaccata" in r.stdout and len(wstate()["windows"]) == n_win and len(garcon_log.read_text().splitlines()) == n_garcon, r.stdout + r.stderr + str(wstate()["windows"]))
    w_g2 = next(t["windowId"] for t in wstate()["tabs"] if "args[]=gamma-2" in t["url"])
    T.check("O7 after launch: no #home in the window that received the tab (the O4 window, opened with open_as_tab false, keeps its home by design)", not [t for t in homes_with_sessions() if t["windowId"] == w_g2], str(wstate()["tabs"]))
T.rm(tmp)
T.finish()
