#!/usr/bin/env python3
"""Verifica cm-registry.sh e cm-close.sh su un tmux privato con il claude finto.

R1  registry scrive nome/cartella/account delle sessioni tmux vive (formato legacy)
R2  guardia T19: senza sessioni NON sovrascrive; --show stampa
C1  close NOME chiude (=NOME esatto: `alfa` non tocca `alfa-2`, T1/T50)
C2  close su sessione attaccata → exit 4 e resta viva (T14)
C3  close inesistente → exit 3 con l'elenco
C4  --abandoned --dry-run elenca solo le staccate ferme su una domanda; senza --dry-run le chiude
"""
import json
import os
import pty
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude/sessions", "ws/alfa", "ws/alfa-2", "ws/gamma"):
    (home / d).mkdir(parents=True)
reg = tmp / "registry.json"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws")},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "registry": {"file": str(reg)},
}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CLAUDE_CONFIG_DIR": str(home / ".claude")}
    e.update(extra)
    return e


def run(script, *args):
    return subprocess.run([str(T.SCRIPTS / script)] + list(args), capture_output=True, text=True, env=env(), timeout=60)


with T.PrivateTmux() as tm:
    # tre sessioni con il claude finto dentro (si registra da solo nel registro peer)
    for name, scen in (("alfa", "plain"), ("alfa-2", "plain"), ("gamma", "question")):
        tm("new-session", "-d", "-s", name, "-x", "100", "-y", "30", "-c", str(home / "ws" / name),
           f"env CLAUDE_CONFIG_DIR='{home}/.claude' FAKE_CLAUDE_SCENARIO={scen} '{FAKE}' --dangerously-skip-permissions -n {name}")
    time.sleep(2)
    r = run("cm-registry.sh")
    d = json.loads(reg.read_text())
    names = {s["nome"]: s for s in d["sessioni"]}
    T.check("R1 registry has the three sessions", set(names) == {"alfa", "alfa-2", "gamma"}, str(names))
    T.check("R1 legacy fields nome/cartella/account", names["alfa"]["cartella"] == str(home / "ws" / "alfa") and names["alfa"]["account"] == "personale", str(names["alfa"]))
    r = run("cm-registry.sh", "--show")
    T.check("R2 --show prints the file", '"alfa"' in r.stdout, r.stdout)

    # C2: alfa attaccata via pty
    master, slave = pty.openpty()
    client = subprocess.Popen(["tmux", "-L", tm.socket, "attach", "-t", "=alfa"], stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    time.sleep(1)
    r = run("cm-close.sh", "alfa")
    T.check("C2 attached idle session without chrome-bridge → refused (exit 4), still alive: no destructive fallback", r.returncode == 4 and tm("has-session", "-t", "=alfa").returncode == 0, r.stderr)
    # C5 (07/10, ok del maintainer): un'attaccata IDLE si chiude chiudendo la sua scheda (chrome-bridge finto), mai con
    # detach o kill; busy o ferma su una domanda → rifiutata; senza una scheda certa → rifiutata; mai se stessa
    bstate = tmp / "bridge.json"
    URL5 = "chrome-untrusted://terminal/html/terminal.html"
    bstate.write_text(json.dumps({"windows": [{"id": 1, "left": 0, "top": 0, "width": 1536, "height": 864, "state": "normal"}],
                                  "tabs": [{"id": 11, "windowId": 1, "url": URL5 + "?command=vmshell&args[]=--&args[]=supervisor&args[]=attach&args[]=alfa", "title": "🟢 alfa", "client_pid": client.pid},
                                           {"id": 12, "windowId": 1, "url": URL5 + "?command=vmshell&args[]=--&args[]=supervisor&args[]=attach&args[]=alfa-2", "title": "🟡 alfa-2"}],
                                  "monitors": [{"left": 0, "top": 0, "width": 1536, "height": 864}], "calls": []}))
    cfg5 = tmp / "config-c5.json"
    c5 = json.loads(cfg.read_text())
    c5["terminal"] = {"backend": "chromeos"}
    c5["tile"] = {"chrome_bridge_cli": str(T.ROOT / "tests" / "lib" / "fake-bridge.py"), "placeholder_file": str(tmp / "next-session"), "monitor_registry": str(tmp / "monitors.json")}
    cfg5.write_text(json.dumps(c5))

    def run5(*args, **extra):
        return subprocess.run([str(T.SCRIPTS / "cm-close.sh")] + list(args), capture_output=True, text=True, timeout=90,
                              env=env(CC_SUPERVISOR_CONFIG=str(cfg5), FAKE_BRIDGE_STATE=str(bstate), WAYLAND_DISPLAY="wl-0", **extra))
    tm("set-option", "-t", "alfa", "destroy-unattached", "on")   # nome nudo: set-option non accetta «=» (T1)
    m2, s2 = pty.openpty()
    client_g = subprocess.Popen(["tmux", "-L", tm.socket, "attach", "-t", "=gamma"], stdin=s2, stdout=s2, stderr=s2, start_new_session=True)
    time.sleep(1)
    r = run5("gamma")
    T.check("C5 attached and waiting on a question → refused (exit 4) with the state, still alive, no tab closed",
            r.returncode == 4 and "waiting" in r.stderr and tm("has-session", "-t", "=gamma").returncode == 0
            and not [c for c in json.loads(bstate.read_text())["calls"] if c.get("cmd") == "tab_action"], r.stdout + r.stderr)
    client_g.kill(); time.sleep(0.5)
    pane = tm("list-panes", "-t", "=alfa", "-F", "#{pane_id}").stdout.strip()
    r = run5("alfa", TMUX=f"/tmp/tmux-fake/{tm.socket},1,0", TMUX_PANE=pane)
    T.check("C5 a session never closes itself (exit 4), still alive", r.returncode == 4 and "questa sessione" in r.stderr and tm("has-session", "-t", "=alfa").returncode == 0, r.stdout + r.stderr)
    r = run5("alfa", "--dry-run")
    T.check("C5 --dry-run names the tab it would close, closes nothing", r.returncode == 0 and "alfa" in r.stdout and tm("has-session", "-t", "=alfa").returncode == 0
            and len(json.loads(bstate.read_text())["tabs"]) == 2, r.stdout + r.stderr)
    r = run5("alfa")
    left5 = json.loads(bstate.read_text())["tabs"]
    T.check("C5 attached and idle → its tab (exact args[]=alfa, never alfa-2's) closed through chrome-bridge, the session gone (destroy-unattached), the other tab and window alive, exit 0",
            r.returncode == 0 and tm("has-session", "-t", "=alfa").returncode != 0 and tm("has-session", "-t", "=alfa-2").returncode == 0
            and [t["id"] for t in left5] == [12], r.stdout + r.stderr + str(left5))
    # rimette alfa per i controlli che seguono, staccata come prima
    tm("new-session", "-d", "-s", "alfa", "-x", "100", "-y", "30", "-c", str(home / "ws" / "alfa"),
       f"env CLAUDE_CONFIG_DIR='{home}/.claude' FAKE_CLAUDE_SCENARIO=plain '{FAKE}' --dangerously-skip-permissions -n alfa")
    time.sleep(2)
    m3, s3 = pty.openpty()
    client = subprocess.Popen(["tmux", "-L", tm.socket, "attach", "-t", "=alfa"], stdin=s3, stdout=s3, stderr=s3, start_new_session=True)
    time.sleep(1)
    r = run5("alfa")
    T.check("C5 attached and idle but no certain tab (no args[]=alfa, no unique title) → refused (exit 4), still alive",
            r.returncode == 4 and tm("has-session", "-t", "=alfa").returncode == 0, r.stdout + r.stderr)
    r = run("cm-close.sh", "nessuna")
    T.check("C3 missing → exit 3 with the list", r.returncode == 3 and "alfa" in r.stderr, r.stderr)
    # C4: gamma e' staccata e ferma sul menu numerato del claude finto
    r = run("cm-close.sh", "--abandoned", "--dry-run")
    T.check("C4 --abandoned --dry-run lists only gamma", "gamma" in r.stdout and "alfa" not in r.stdout, r.stdout + r.stderr)
    T.check("C4 dry run closes nothing", tm("has-session", "-t", "=gamma").returncode == 0, tm("list-sessions").stdout)
    r = run("cm-close.sh", "--abandoned")
    time.sleep(0.5)
    T.check("C4 --abandoned closes gamma, keeps alfa and alfa-2",
            tm("has-session", "-t", "=gamma").returncode != 0 and tm("has-session", "-t", "=alfa-2").returncode == 0, tm("list-sessions").stdout)
    client.kill()
    time.sleep(0.5)
    # C1: chiudere "alfa" non deve toccare "alfa-2" (prefisso!)
    tm("kill-session", "-t", "=alfa")   # alfa via, resta alfa-2: senza `=` un kill di "alfa" prenderebbe alfa-2
    tm("new-session", "-d", "-s", "alfa", "bash", "--norc")
    r = run("cm-close.sh", "alfa")
    T.check("C1 close alfa closes alfa only", r.returncode == 0 and tm("has-session", "-t", "=alfa").returncode != 0 and tm("has-session", "-t", "=alfa-2").returncode == 0,
            r.stdout + tm("list-sessions").stdout)
    tm("kill-session", "-t", "=alfa")   # se per errore fosse rimasta
    r = run("cm-close.sh", "alfa")      # ora "alfa" non esiste: NON deve agganciare alfa-2 per prefisso
    T.check("C1 close of a missing exact name never hits the -2 sibling (T50)", r.returncode == 3 and tm("has-session", "-t", "=alfa-2").returncode == 0, r.stderr + tm("list-sessions").stdout)

    # R2: guardia sul vuoto
    tm("kill-server")
    before = reg.read_text()
    r = run("cm-registry.sh")
    T.check("R2 no sessions → registry untouched (T19)", reg.read_text() == before, reg.read_text())

T.rm(str(tmp))
T.finish()
