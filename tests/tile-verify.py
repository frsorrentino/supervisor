#!/usr/bin/env python3
"""Verifica cm-tile.py con il chrome-bridge finto (tests/lib/fake-bridge.py) e un tmux privato.

G1  senza chrome-bridge → exit con messaggio; backend non chromeos → exit con messaggio
G2  --where elenca i monitor (dalle finestre massimizzate e dalle schede http)
G3  --dry-run: piano senza toccare niente
G4  tile: tre sessioni in tre finestre → colonne uguali nell'ordine chiesto, una per volta (T35); `actual` assente non fa morire (T37)
G5  T34/T72: finestra massimizzata o minimizzata → la sessione riparte in una finestra app NUOVA via garcon
    (attach NOME, client nuovo T27), la scheda vecchia chiusa, poi il tiling; MAI popup (l'utente 11/09 13:26)
G10 sessioni in finestre popup (versioni precedenti) → convertite in finestre app via garcon; dopo tile
    nessuna finestra popup
G11 DISPARI con la master (l'utente 11/09 15:50, tile.odd_layout master-primary): master grande a sinistra (60%),
    le altre impilate a destra in N-1 righe; home 0, popup 0; G12 PARI → colonne come prima;
    G13 dispari SENZA master → colonne uniformi (ripiego); G14 odd_layout uniform → comportamento vecchio;
    sotto min_column_px la colonna impilata è consentita ma segnalata
G6  T39: sessioni sparse su due monitor → radunate dove sta la maggioranza
G7  T40: sotto min_column_px si passa a griglia
G8  move destra: monitor dal registro; T32: voce stantia rifiutata dal tile di prova e tolta dal registro
G9  T33: pagina di riferimento scelta col CENTRO nel riquadro delle sessioni
M1  merge: sessioni in finestre diverse → segnaposto, duplicate nella finestra di raccolta (non popup, con sorgente), client NUOVO atteso (T27), schede vecchie chiuse, #home sola chiusa (T28)
M2  T28: le schede #home e quelle di attach non sono sorgenti duplicabili
M3  T78 (11/09): la scheda iniziale (#home) della SWA non si chiude finché ha altre schede accanto; merge preferisce
    come finestra di raccolta una finestra app SENZA #home; M4 senza nessuna finestra così usa quella con #home
    e la SFRATTA (T80, chrome-bridge ed24b83: move_tab della home in un popup, poi close): mai una #home
    accanto a una sessione
L1  layout save/restore/list con l'elenco delle sessioni presenti
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
(home / ".claude" / "sessions").mkdir(parents=True)
state = tmp / "state"
bridge_state = tmp / "bridge.json"
FAKE_BRIDGE = T.ROOT / "tests" / "lib" / "fake-bridge.py"
cfg = tmp / "config.json"
URL = "chrome-untrusted://terminal/html/terminal.html"
NATIVE = {"left": 0, "top": 0, "width": 1536, "height": 864}
LEFTM = {"left": -2226, "top": -1252, "width": 2226, "height": 1204}
RIGHTM = {"left": 749, "top": -1252, "width": 2226, "height": 1204}


FAKE_GARCON = T.ROOT / "tests" / "lib" / "fake-garcon.py"
garcon_log = tmp / "garcon.log"


def write_cfg(backend="chromeos", cli=str(FAKE_BRIDGE), odd_layout=None):
    cfg.write_text(json.dumps({
        "language": "it", "state_dir": str(state),
        "accounts": {"personale": {"config_dir": str(home / ".claude")}, "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
        "terminal": {"backend": backend, "garcon": str(FAKE_GARCON)},
        "tile": {"chrome_bridge_cli": cli, "terminal_url": "chrome-untrusted://terminal/", "min_column_px": 340,
                 "monitor_registry": str(state / "monitors.json"), "placeholder_file": str(state / "next-session"),
                 "new_client_wait_s": 15, "window_open_wait_s": 3, **({"odd_layout": odd_layout} if odd_layout else {})},
        "tabs": {"color_registry": str(tmp / "colors")},
    }))


write_cfg()


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "FAKE_BRIDGE_STATE": str(bridge_state), "CM_PROC_SCAN_PIDS": "",
         "FAKE_GARCON_LOG": str(garcon_log), "FAKE_BRIDGE_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "WAYLAND_DISPLAY": "wl-0",
         "FAKE_BRIDGE_PLACEHOLDER": str(state / "next-session"), "FAKE_BRIDGE_TMUX_ARGS": tm.env["CM_TMUX_ARGS"]}
    e.update(extra)
    return e


def tile(*args, **extra):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tile.py")] + list(args), capture_output=True, text=True, env=env(**extra), timeout=180)


def term_tab(tid, wid, name):
    return {"id": tid, "windowId": wid, "url": f"{URL}?command=supervisor&args[]=attach&args[]={name}&args[]=ephemeral", "title": f"🔴 {name}", "active": True}


def win(wid, l, t, w=600, h=500, state_="normal", type_="app"):
    return {"id": wid, "type": type_, "state": state_, "left": l, "top": t, "width": w, "height": h}


def world(windows, tabs, monitors):
    bridge_state.write_text(json.dumps({"windows": windows, "tabs": tabs, "monitors": monitors, "calls": []}))


def calls():
    return json.load(open(bridge_state))["calls"]


def windows():
    return {x["id"]: x for x in json.load(open(bridge_state))["windows"]}


def garcon_calls():
    return garcon_log.read_text().splitlines() if garcon_log.exists() else []


def popups():
    return [x for x in json.load(open(bridge_state))["windows"] if x.get("type") == "popup"]


def homes_with_sessions():
    st = json.load(open(bridge_state))
    with_sess = {t["windowId"] for t in st["tabs"] if "args[]=attach" in t["url"]}
    return [t for t in st["tabs"] if "#home" in t["url"] and t["windowId"] in with_sess]


def win_of(name):
    """la finestra in cui sta ora la scheda della sessione"""
    st = json.load(open(bridge_state))
    t = next((t for t in st["tabs"] if f"args[]={name}" in t["url"]), None)
    return next((x for x in st["windows"] if t and x["id"] == t["windowId"]), None)


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "alfa", "bash", "--norc"], env=env(), check=True)
    for s in ("beta", "gamma"):
        tm("new-session", "-d", "-s", s, "bash", "--norc")
    state.mkdir()
    # G1
    write_cfg(cli="")
    r = tile("tile")
    T.check("G1 no chrome-bridge → refuses with message", r.returncode != 0 and "chrome-bridge" in (r.stderr + r.stdout), r.stderr)
    write_cfg(backend="gnome")
    r = tile("tile")
    T.check("G1 backend not chromeos → refuses", r.returncode != 0 and "chromeos" in (r.stderr + r.stdout), r.stderr)
    write_cfg()
    # G2: monitor da una massimizzata (nativo) e da una scheda http su sinistra
    world([win(1, 0, 0, 1536, 864, "maximized", "app"), win(2, -2000, -1000, 800, 600, "normal", "normal")],
          [{"id": 1, "windowId": 1, "url": URL + "#home", "title": "Terminal", "active": True},
           {"id": 2, "windowId": 2, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE, LEFTM])
    r = tile("tile", "--where")
    T.check("G2 --where lists native and above/external monitors", r.returncode == 0 and "nativo" in r.stdout and "1536x864" in r.stdout and "2226x1204" in r.stdout, r.stdout + r.stderr)
    # G3/G4: tre sessioni, tre finestre popup sul nativo + una finestra http di riferimento
    world([win(11, 10, 10), win(12, 620, 10), win(13, 10, 520), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), term_tab(103, 13, "gamma"),
           {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE, LEFTM])
    r = tile("tile", "gamma", "alfa", "beta", "--dry-run")
    T.check("G3 dry-run plans 3 columns and touches nothing", r.returncode == 0 and "3 " in r.stdout and windows()[11]["left"] == 10 and not any(c["cmd"] == "tile_windows" for c in calls()), r.stdout + r.stderr)
    r = tile("tile", "gamma", "alfa", "beta")
    ws = windows()
    T.check("G4 columns in the requested order: gamma, alfa, beta", r.returncode == 0 and ws[13]["left"] == 0 and ws[11]["left"] == 512 and ws[12]["left"] == 1024 and ws[13]["width"] == 512, r.stdout + r.stderr + str({k: (v['left'], v['width']) for k, v in ws.items()}))
    tiles = [c for c in calls() if c["cmd"] == "tile_windows"]
    T.check("G4 one tile_windows call per window (T35)", len(tiles) == 3 and all(len(c["params"]["window_ids"]) == 1 for c in tiles), str([c["params"]["window_ids"] for c in tiles]))
    r = tile("tile", "gamma", "alfa", "beta")
    T.check("G4 re-run with windows already in place: no crash (T37), reports ok", r.returncode == 0 and "3/3" in r.stdout, r.stdout + r.stderr)
    # G11: cinque con la master → master-primary
    tm("new-session", "-d", "-s", "master", "bash", "--norc")
    for s_ in ("delta",):
        tm("new-session", "-d", "-s", s_, "bash", "--norc")
    five = ["master", "alfa", "beta", "gamma", "delta"]
    world([win(20 + i, 10 + 40 * i, 10) for i in range(5)] + [win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(200 + i, 20 + i, n_) for i, n_ in enumerate(five)] + [{"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("tile", *five)
    ws = windows()
    T.check("G11 odd with master: master big on the left (60%, full height), the four stacked on the right in 4 rows", r.returncode == 0 and "main-vertical" in r.stdout and ws[20]["left"] == 0 and ws[20]["width"] == 921 and ws[20]["height"] == 864
            and all(ws[21 + i]["left"] == 921 and ws[21 + i]["width"] == 615 and ws[21 + i]["height"] == 216 and ws[21 + i]["top"] == 216 * i for i in range(4)),
            r.stdout + r.stderr + str({k: (v["left"], v["top"], v["width"], v["height"]) for k, v in ws.items()}))
    T.check("G11 no home next to sessions, no popup", not homes_with_sessions() and not popups(), str(json.load(open(bridge_state))["tabs"]))
    # G12: quattro → colonne come prima
    four = ["alfa", "beta", "gamma", "delta"]
    world([win(20 + i, 10 + 40 * i, 10) for i in range(4)] + [win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(200 + i, 20 + i, n_) for i, n_ in enumerate(four)] + [{"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("tile", *four)
    ws = windows()
    T.check("G12 even: equal columns as before", r.returncode == 0 and "main-vertical" not in r.stdout and all(ws[20 + i]["width"] == 384 and ws[20 + i]["left"] == 384 * i for i in range(4)), r.stdout + r.stderr + str({k: (v["left"], v["width"]) for k, v in ws.items()}))
    # G13: tre SENZA master → colonne uniformi
    three = ["alfa", "beta", "gamma"]
    world([win(20 + i, 10 + 40 * i, 10) for i in range(3)] + [win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(200 + i, 20 + i, n_) for i, n_ in enumerate(three)] + [{"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("tile", *three)
    ws = windows()
    T.check("G13 odd without master: uniform columns (fallback)", r.returncode == 0 and "main-vertical" not in r.stdout and all(ws[20 + i]["width"] == 512 for i in range(3)), r.stdout + r.stderr + str({k: (v["left"], v["width"]) for k, v in ws.items()}))
    # G14: odd_layout uniform → vecchio comportamento (5 su 1536 = 307 < 340 → griglia)
    write_cfg(odd_layout="uniform")
    world([win(20 + i, 10 + 40 * i, 10) for i in range(5)] + [win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(200 + i, 20 + i, n_) for i, n_ in enumerate(five)] + [{"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("tile", *five)
    T.check("G14 odd_layout uniform: the old behaviour (grid below min_column_px)", r.returncode == 0 and "main-vertical" not in r.stdout and ("grid" in r.stdout or "griglia" in r.stdout), r.stdout + r.stderr)
    write_cfg()
    # G11b: sette con la master → colonna impilata sotto min_column_px consentita ma segnalata
    for s_ in ("e1", "e2"):
        tm("new-session", "-d", "-s", s_, "bash", "--norc")
    seven = five + ["e1", "e2"]
    world([win(20 + i, 10 + 40 * i, 10) for i in range(7)] + [win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(200 + i, 20 + i, n_) for i, n_ in enumerate(seven)] + [{"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("tile", *seven)
    ws = windows()
    T.check("G11b seven with master: still main-vertical, stack of 6 at 144 px rows, narrow stack reported not refused", r.returncode == 0 and "main-vertical" in r.stdout and ws[20]["width"] == 921 and ws[26]["top"] == 144 * 5 and ws[26]["height"] == 144, r.stdout + r.stderr + str({k: (v["left"], v["top"], v["height"]) for k, v in ws.items()}))
    for s_ in ("master", "delta", "e1", "e2"):
        tm("kill-session", "-t", f"={s_}")
    # G5: massimizzata
    world([win(11, 0, 0, 1536, 864, "maximized", "app"), win(12, 620, 10), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    n_g = len(garcon_calls())
    r = tile("tile", "alfa", "beta")
    tabs = json.load(open(bridge_state))["tabs"]
    T.check("G5 maximized: alfa reopened in a NEW app window through garcon (attach alfa), old tab closed, no move_tab of a session tab, no popup, then tiled", r.returncode == 0 and any("attach alfa" in l for l in garcon_calls()[n_g:]) and not any(c["cmd"] == "move_tab" and c["params"].get("tab_id") in (101, 102) for c in calls()) and not any(t["id"] == 101 for t in tabs) and not popups() and "2/2" in r.stdout and win_of("alfa") and win_of("alfa")["width"] == 768, r.stdout + r.stderr + str(garcon_calls()) + str(windows()))
    # G5b: minimizzata (T72): stessa sorte della massimizzata
    world([win(11, 795, 0, 573, 864, "minimized", "app"), win(12, 620, 10), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    n_g = len(garcon_calls())
    r = tile("tile", "alfa", "beta")
    T.check("G5b minimized: same fate, app window through garcon, reported", r.returncode == 0 and any("attach alfa" in l for l in garcon_calls()[n_g:]) and not popups() and "2/2" in r.stdout and "minimized" in r.stdout, r.stdout + r.stderr + str(garcon_calls()))
    T.check("G5b the garcon window's #home evicted: no #home next to a session, no popup left", not homes_with_sessions() and not popups(), str(json.load(open(bridge_state))["tabs"]))
    # G10: sessioni in finestre POPUP (staccate da versioni precedenti) → convertite in finestre app
    world([win(11, 10, 10, type_="popup"), win(12, 620, 10, type_="popup"), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    n_g = len(garcon_calls())
    r = tile("tile", "alfa", "beta")
    T.check("G10 popup windows converted: two garcon attach calls, no popup left, both tiled as app windows", r.returncode == 0 and len(garcon_calls()) == n_g + 2 and not popups() and "2/2" in r.stdout and win_of("alfa") and win_of("beta") and win_of("alfa")["type"] == "app" and {win_of("alfa")["left"], win_of("beta")["left"]} == {0, 768}, r.stdout + r.stderr + str(garcon_calls()[n_g:]) + str(windows()))
    T.check("G10 both app windows without #home", not homes_with_sessions(), str(json.load(open(bridge_state))["tabs"]))
    # G5c: sei sessioni su 1536 px = 256 per colonna < 340 → griglia 3x2 da 512
    for s_ in ("delta", "epsilon", "zeta"):
        tm("new-session", "-d", "-s", s_, "bash", "--norc")
    six = ["alfa", "beta", "gamma", "delta", "epsilon", "zeta"]
    world([win(10 + i, 10 + 40 * i, 10) for i in range(6)] + [win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(100 + i, 10 + i, n_) for i, n_ in enumerate(six)] + [{"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("tile", *six)
    ws = windows()
    T.check("G5c 6 columns below min_column_px → automatic 3x2 grid", r.returncode == 0 and "griglia" in r.stdout or "grid" in r.stdout, r.stdout + r.stderr)
    T.check("G5c grid cells are 512 wide, second row at top 432", ws[10]["width"] == 512 and ws[13]["left"] == 0 and ws[13]["top"] == 432, str({k: (v["left"], v["top"], v["width"]) for k, v in ws.items()}))
    for s_ in ("delta", "epsilon", "zeta"):
        tm("kill-session", "-t", f"={s_}")
    # G6: sparse su due monitor → maggioranza (sinistra)
    world([win(11, -2000, -1000), win(12, -1300, -1000), win(13, 100, 100), win(9, -2200, -600, 700, 300, "normal", "normal"), win(8, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), term_tab(103, 13, "gamma"),
           {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}, {"id": 80, "windowId": 8, "url": "https://example.org/", "title": "ex2", "active": True}], [NATIVE, LEFTM])
    r = tile("tile", "alfa", "beta", "gamma")
    ws = windows()
    T.check("G6 scattered sessions gathered on the majority monitor (the external one)", r.returncode == 0 and ws[13]["left"] < 0 and "2 monitor" in r.stdout, r.stdout + r.stderr + str({k: v['left'] for k, v in ws.items()}))
    # G7: griglia sotto 340 px per colonna: 6 sessioni sul nativo (1536/6 = 256)
    for s in ("d1", "d2", "d3"):
        tm("new-session", "-d", "-s", s, "bash", "--norc")
    world([win(11, 10, 10), win(12, 620, 10), win(13, 10, 520), win(14, 300, 300), win(15, 400, 300), win(16, 500, 300), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), term_tab(103, 13, "gamma"), term_tab(104, 14, "d1"), term_tab(105, 15, "d2"), term_tab(106, 16, "d3"),
           {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("tile", "alfa", "beta", "gamma", "d1", "d2", "d3", "--grid")
    ws = windows()
    T.check("G7 grid 3x2 with 512px cells", r.returncode == 0 and ws[11]["width"] == 512 and ws[14]["top"] == 432 and "grid" in r.stdout, r.stdout + r.stderr + str({k: (v['left'], v['top'], v['width']) for k, v in ws.items()}))
    for s in ("d1", "d2", "d3"):
        tm("kill-session", "-t", f"={s}")
    # G8: move destra dal registro dei monitor (nessuna finestra la' sopra); poi voce stantia
    (state / "monitors.json").write_text(json.dumps([RIGHTM]))
    world([win(11, 10, 10), win(12, 620, 10), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE, RIGHTM])
    r = tile("move", "destra", "alfa", "beta")
    ws = windows()
    T.check("G8 move destra tiles on the right monitor known from the registry", r.returncode == 0 and ws[11]["left"] == 749 and ws[12]["left"] == 749 + 1113, r.stdout + r.stderr + str({k: v['left'] for k, v in ws.items()}))
    (state / "monitors.json").write_text(json.dumps([{"left": 5000, "top": -1252, "width": 2226, "height": 1204}]))
    world([win(11, 10, 10), win(12, 620, 10), win(9, 700, 500, 700, 300, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), {"id": 90, "windowId": 9, "url": "https://example.com/", "title": "ex", "active": True}], [NATIVE])
    r = tile("move", "sopra", "alfa", "beta")
    T.check("G8 stale registry entry rejected by the probe tile and removed (T32)", r.returncode != 0 and "stantia" in (r.stdout + r.stderr) and not any(m["left"] == 5000 for m in json.loads((state / "monitors.json").read_text())), r.stdout + r.stderr + (state / "monitors.json").read_text())
    # G9: pagina di riferimento col centro nel riquadro: la finestra 8 sfiora il bordo ma il centro sta fuori
    world([win(11, 10, 10), win(12, 620, 10), win(8, 1200, 300, 700, 300, "normal", "normal"), win(9, 300, 300, 200, 100, "normal", "normal")],
          [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta"), {"id": 80, "windowId": 8, "url": "https://far.example/", "title": "far", "active": True},
           {"id": 90, "windowId": 9, "url": "https://near.example/", "title": "near", "active": True}], [NATIVE])
    r = tile("tile", "alfa", "beta")
    js = [c for c in calls() if c["cmd"] == "execute_js"]
    T.check("G9 reference page chosen by centre inside the sessions' box (near, not far)", r.returncode == 0 and js and js[-1]["params"]["tab_id"] == 90, str([c["params"]["tab_id"] for c in js]))
    for s_ in ("alfa", "beta", "gamma"):   # i client finti di G5/G10 restano attaccati: via, M1 conta i NUOVI
        tm("detach-client", "-s", s_)
    time.sleep(1)
    # M1: merge — finestra di raccolta 5 (app) con una shell duplicabile; alfa in finestra 11
    world([win(5, 0, 0, 1536, 864, "normal", "app"), win(11, 10, 10), win(7, 900, 100, 400, 300, "normal", "app")],
          [{"id": 50, "windowId": 5, "url": URL, "title": "Terminal", "active": True}, term_tab(101, 11, "alfa"),
           {"id": 70, "windowId": 7, "url": URL + "#home", "title": "Terminal", "active": True}], [NATIVE])
    r = tile("merge", "alfa")
    ws = windows()
    tabs = json.load(open(bridge_state))["tabs"]
    T.check("M1 merge: placeholder consumed, tab duplicated in the collecting window, old popup closed, lone #home closed",
            r.returncode == 0 and any(c["cmd"] == "tab_action" and c["params"].get("action") == "duplicate" for c in calls())
            and 11 not in ws and 7 not in ws and any(t["windowId"] == 5 and "args[]=alfa" in t["url"] for t in tabs) and not (state / "next-session").exists(),
            r.stdout + r.stderr + str(list(ws)) + str(tabs))
    T.check("M1 a NEW client attached to alfa (T27)", "alfa 1" in tm("list-sessions", "-F", "#{session_name} #{session_attached}").stdout, tm("list-sessions", "-F", "#{session_name} #{session_attached}").stdout)
    # M2: senza sorgente duplicabile (solo #home e schede di attach) → non raccoglie li'
    world([win(5, 0, 0, 1536, 864, "normal", "app"), win(11, 10, 10)],
          [{"id": 50, "windowId": 5, "url": URL + "#home", "title": "Terminal", "active": True}, term_tab(51, 5, "beta"), term_tab(101, 11, "gamma")], [NATIVE])
    r = tile("merge", "gamma", CM_TILE_NO_GARCON="1")
    T.check("M2 no duplicable source (#home, attach tabs) → no duplicate from them", not any(c["cmd"] == "tab_action" and c["params"].get("action") == "duplicate" and c["params"].get("tab_id") in (50, 51) for c in calls()), str(calls()[-3:]))
    # M3: due finestre app con una shell duplicabile: la 5 ha la #home, la 8 no → si raccoglie nella 8
    world([win(5, 0, 0, 1536, 864, "normal", "app"), win(8, 100, 100, 900, 600, "normal", "app"), win(11, 10, 10)],
          [{"id": 50, "windowId": 5, "url": URL + "#home", "title": "Terminal", "active": False},
           {"id": 52, "windowId": 5, "url": URL, "title": "Terminal", "active": True},
           {"id": 80, "windowId": 8, "url": URL, "title": "Terminal", "active": True}, term_tab(101, 11, "alfa")], [NATIVE])
    r = tile("merge", "alfa")
    dup = [c["params"].get("tab_id") for c in calls() if c["cmd"] == "tab_action" and c["params"].get("action") == "duplicate"]
    T.check("M3 merge prefers the app window WITHOUT #home (duplicates tab 80 of window 8)", r.returncode == 0 and dup == [80] and "raccolgo nella finestra 8" in r.stdout, r.stdout + r.stderr + str(dup))
    # M4: solo finestre con la #home → si usa quella e si dice di trascinare fuori una scheda
    world([win(5, 0, 0, 1536, 864, "normal", "app"), win(11, 10, 10)],
          [{"id": 50, "windowId": 5, "url": URL + "#home", "title": "Terminal", "active": False},
           {"id": 52, "windowId": 5, "url": URL, "title": "Terminal", "active": True}, term_tab(101, 11, "alfa")], [NATIVE])
    r = tile("merge", "alfa")
    dup = [c["params"].get("tab_id") for c in calls() if c["cmd"] == "tab_action" and c["params"].get("action") == "duplicate"]
    T.check("M4 only a window with #home: collects there, the #home evicted (move_tab to a popup, closed), none left next to sessions", r.returncode == 0 and dup == [52] and any(c["cmd"] == "move_tab" and c["params"].get("tab_id") == 50 and c["params"].get("window_type") == "popup" for c in calls()) and not homes_with_sessions() and not popups() and 5 in windows(), r.stdout + r.stderr + str(dup) + str(json.load(open(bridge_state))["tabs"]))
    # L1
    world([win(11, 10, 10), win(12, 620, 10)], [term_tab(101, 11, "alfa"), term_tab(102, 12, "beta")], [NATIVE])
    r = tile("layout", "save", "mattina")
    T.check("L1 layout save records the live sessions", r.returncode == 0 and (state / "layouts" / "mattina.json").exists() and "alfa" in (state / "layouts" / "mattina.json").read_text(), r.stdout + r.stderr)
    tm("kill-session", "-t", "=beta")
    r = tile("layout", "restore", "mattina")
    T.check("L1 layout restore names the missing session", r.returncode == 0 and "beta" in r.stdout and "restored" in r.stdout, r.stdout + r.stderr)
    r = tile("layout", "list")
    T.check("L1 layout list", "mattina" in r.stdout, r.stdout + r.stderr)

T.rm(str(tmp))
T.finish()
