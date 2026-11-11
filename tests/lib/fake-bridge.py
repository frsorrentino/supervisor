#!/usr/bin/env python3
"""chrome-bridge finto per i test di cm-tile.py: un mondo (finestre, schede, monitor) in un
file JSON (FAKE_BRIDGE_STATE), gli stessi comandi della CLI vera, ogni chiamata registrata.

Regole di Chrome che emula (le trappole pagate):
- tile_windows su una finestra MASSIMIZZATA: applied False, bounds ignorati (T34);
- bounds oltre il 50% fuori da ogni monitor: errore «50% within visible screen space» (T32);
- `actual` nei results solo se la finestra si e' mossa davvero (T37);
- move_tab risponde `to_window` (T36); tab_action duplicate crea una scheda shell nella stessa
  finestra e — come farebbe la shell nuova — attacca un client tmux alla sessione scritta nel
  segnaposto (FAKE_BRIDGE_PLACEHOLDER), cosi' `attendi_nuovo_client` vede un client NUOVO (T27);
- execute_js risponde screen.avail* del monitor su cui sta il CENTRO della finestra della scheda;
- get_tabs, create_tab, window_layout (save/restore/list su file).
"""
import json
import os
import pty
import subprocess
import sys

STATE = os.environ["FAKE_BRIDGE_STATE"]


def load():
    return json.load(open(STATE))


def save(w):
    json.dump(w, open(STATE, "w"), indent=1)


def monitor_of(w, win):
    cx, cy = win["left"] + win["width"] / 2, win["top"] + win["height"] / 2
    for m in w["monitors"]:
        if m["left"] <= cx <= m["left"] + m["width"] and m["top"] <= cy <= m["top"] + m["height"]:
            return m
    return None


def visible(w, area):
    """almeno il 50% del rettangolo dentro un monitor"""
    for m in w["monitors"]:
        ix = max(0, min(area["left"] + area["width"], m["left"] + m["width"]) - max(area["left"], m["left"]))
        iy = max(0, min(area["top"] + area["height"], m["top"] + m["height"]) - max(area["top"], m["top"]))
        if ix * iy >= 0.5 * area["width"] * area["height"]:
            return True
    return False


def main():
    args = sys.argv[1:]
    cmd = args[0]
    params = {}
    if "--json" in args:
        params = json.loads(args[args.index("--json") + 1])
    w = load()
    w.setdefault("calls", []).append({"cmd": cmd, "params": params})
    out = None
    if cmd == "get_tabs":
        out = {"tabs": w["tabs"], "windows": w["windows"]} if params.get("include_windows") else w["tabs"]
    elif cmd == "tile_windows":
        results = []
        area = params["area"]
        for wid in params["window_ids"]:
            win = next((x for x in w["windows"] if x["id"] == wid), None)
            if not win:
                results.append({"window_id": wid, "applied": False, "error": "no such window"})
                continue
            if not visible(w, area):
                results.append({"window_id": wid, "applied": False, "error": "bounds must be at least 50% within visible screen space"})
                continue
            if win.get("state") in ("maximized", "minimized"):
                results.append({"window_id": wid, "applied": False})
                continue
            moved = any(win[k] != area[k] for k in ("left", "top", "width", "height"))
            win.update({k: area[k] for k in ("left", "top", "width", "height")})
            win["state"] = "normal"
            r = {"window_id": wid, "applied": True}
            if moved:
                r["actual"] = dict(area)
            results.append(r)
        out = {"results": results}
    elif cmd == "move_tab":
        tab = next(t for t in w["tabs"] if t["id"] == params["tab_id"])
        old = next(x for x in w["windows"] if x["id"] == tab["windowId"])
        nid = max(x["id"] for x in w["windows"]) + 1
        w["windows"].append({"id": nid, "type": params.get("window_type", "normal"), "state": "normal",
                             "left": old["left"] + 30, "top": old["top"] + 30, "width": old["width"], "height": old["height"]})
        tab["windowId"] = nid
        if not any(t["windowId"] == old["id"] for t in w["tabs"]):
            w["windows"] = [x for x in w["windows"] if x["id"] != old["id"]]
        out = {"to_window": nid}
    elif cmd == "tab_action":
        if params["action"] == "close":
            tab = next((t for t in w["tabs"] if t["id"] == params["tab_id"]), None)
            if tab:
                if tab.get("client_pid"):   # come il Terminale vero: chiudere la scheda chiude il client tmux che mostra
                    try:
                        os.kill(int(tab["client_pid"]), 15)
                    except OSError:
                        pass
                w["tabs"] = [t for t in w["tabs"] if t["id"] != tab["id"]]
                if not any(t["windowId"] == tab["windowId"] for t in w["tabs"]):
                    w["windows"] = [x for x in w["windows"] if x["id"] != tab["windowId"]]
            out = {"closed": params["tab_id"]}
        elif params["action"] == "duplicate":
            src = next(t for t in w["tabs"] if t["id"] == params["tab_id"])
            nid = max(t["id"] for t in w["tabs"]) + 1
            w["tabs"].append({"id": nid, "windowId": src["windowId"], "url": "chrome-untrusted://terminal/html/terminal.html",
                              "title": "Terminal", "active": True})
            out = {"duplicated": nid}
            w["tabs"][-1]["pending_shell"] = True   # T73: la shell parte solo all'activate
        elif params["action"] == "activate":
            tab = next((t for t in w["tabs"] if t["id"] == params["tab_id"]), None)
            out = {"activated": params["tab_id"]}
            ph = os.environ.get("FAKE_BRIDGE_PLACEHOLDER")
            if tab and tab.pop("pending_shell", False) and ph and os.path.exists(ph):
                name = open(ph).read().strip()
                os.remove(ph)
                # il titolo della nuova scheda diventa quello della sessione (come farebbe attach)
                tab["title"] = "🔴 " + name
                tab["url"] += f"?command=supervisor&args[]=attach&args[]={name}&args[]=ephemeral"
                # un aiutante STACCATO tiene aperto il master dello pty finche' il client vive:
                # se il master si chiude, il client tmux riceve hangup ed esce subito
                helper = (
                    "import os, pty, subprocess, sys, time\n"
                    "m, s = pty.openpty()\n"
                    "p = subprocess.Popen(['tmux'] + %r + ['attach', '-t', '=%s'], stdin=s, stdout=s, stderr=s, start_new_session=True, env={**os.environ, 'TERM': 'xterm-256color'})\n"
                    "while p.poll() is None:\n"
                    "    try: os.read(m, 4096)\n"
                    "    except OSError: time.sleep(0.2)\n"
                ) % (os.environ.get("FAKE_BRIDGE_TMUX_ARGS", "").split(), name)
                subprocess.Popen([sys.executable, "-c", helper], start_new_session=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif cmd == "execute_js":
        tab = next(t for t in w["tabs"] if t["id"] == params["tab_id"])
        win = next(x for x in w["windows"] if x["id"] == tab["windowId"])
        m = monitor_of(w, win) or w["monitors"][0]
        out = {"result": json.dumps({"left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]})}
    elif cmd == "create_tab":
        nid = max(x["id"] for x in w["windows"]) + 1
        tid = max(t["id"] for t in w["tabs"]) + 1
        m = w["monitors"][0]
        w["windows"].append({"id": nid, "type": "normal", "state": "normal", "left": m["left"] + 50, "top": m["top"] + 50, "width": 800, "height": 600})
        w["tabs"].append({"id": tid, "windowId": nid, "url": params["url"], "title": "example", "active": False})
        out = {"tabId": tid}
    elif cmd == "window_layout":
        lay = w.setdefault("layouts", {})
        if params["action"] == "save":
            lay[params["name"]] = [dict(x) for x in w["windows"]]
            out = {"saved": params["name"], "windows": len(w["windows"])}
        elif params["action"] == "restore":
            saved = lay.get(params["name"], [])
            n = 0
            for s_ in saved:
                win = next((x for x in w["windows"] if x["id"] == s_["id"]), None)
                if win:
                    win.update({k: s_[k] for k in ("left", "top", "width", "height")}); n += 1
            out = {"restored": n, "missing": len(saved) - n}
        else:
            out = {"layouts": [{"name": n, "windows": len(v.get("windows", [])) if isinstance(v, dict) else 0, "savedAt": "2026-09-10T00:00:00.000Z"} for n, v in lay.items()]}
    else:
        print(f"fake-bridge: unknown command {cmd}", file=sys.stderr)
        sys.exit(1)
    save(w)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
