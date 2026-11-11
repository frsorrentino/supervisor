#!/usr/bin/env python3
"""garcon finto (`garcon --client --terminal [cmd args...]`) per i test con il chrome-bridge finto:
aggiunge al mondo del bridge (FAKE_BRIDGE_STATE) una finestra app nuova con la scheda #home e una
shell; senza comando la shell consuma il segnaposto (FAKE_BRIDGE_PLACEHOLDER) e attacca un client
tmux come farebbe shell/supervisor.sh; con `attach NOME` attacca NOME (la scheda porta l'URL di
attach). Ogni chiamata in FAKE_GARCON_LOG. Il client vive in un aiutante staccato (pty)."""
import json
import os
import subprocess
import sys

URL = "chrome-untrusted://terminal/html/terminal.html"
args = sys.argv[1:]
if os.environ.get("FAKE_GARCON_LOG"):
    open(os.environ["FAKE_GARCON_LOG"], "a").write(" ".join(args) + "\n")
state = os.environ["FAKE_BRIDGE_STATE"]
w = json.load(open(state))
nid = max([x["id"] for x in w["windows"]] + [0]) + 1
tid = max([t["id"] for t in w["tabs"]] + [0]) + 1
w["windows"].append({"id": nid, "type": "app", "state": "normal", "left": 40 + 20 * (nid % 7), "top": 40, "width": 900, "height": 600})
w["tabs"].append({"id": tid, "windowId": nid, "url": URL + "#home", "title": "Terminal", "active": False})
name = ""
if "attach" in args:
    name = args[args.index("attach") + 1]
    w["tabs"].append({"id": tid + 1, "windowId": nid, "url": f"{URL}?command=supervisor&args[]=attach&args[]={name}&args[]=ephemeral", "title": "🔴 " + name, "active": True})
else:
    ph = os.environ.get("FAKE_BRIDGE_PLACEHOLDER", "")
    w["tabs"].append({"id": tid + 1, "windowId": nid, "url": URL, "title": "Terminal", "active": True})
    if ph and os.path.exists(ph):
        name = open(ph).read().strip()
        os.remove(ph)
        w["tabs"][-1]["title"] = "🔴 " + name
json.dump(w, open(state, "w"), indent=1)
if name:
    helper = (
        "import os, pty, subprocess, time\n"
        "m, s = pty.openpty()\n"
        "p = subprocess.Popen(['tmux'] + %r + ['attach', '-t', '=%s'], stdin=s, stdout=s, stderr=s, start_new_session=True, env={**os.environ, 'TERM': 'xterm-256color'})\n"
        "while p.poll() is None:\n"
        "    try: os.read(m, 4096)\n"
        "    except OSError: time.sleep(0.2)\n"
    ) % ((os.environ.get("FAKE_BRIDGE_TMUX_ARGS") or os.environ.get("CM_TMUX_ARGS", "")).split(), name)
    subprocess.Popen([sys.executable, "-c", helper], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
