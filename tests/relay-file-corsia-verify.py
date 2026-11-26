#!/usr/bin/env python3
"""Le letture «file» di due dispositivi non si sostituiscono (26/11/2026, supervisor-relay-file-corsia-26-11).

Alle 11:30 la carta w61eeb4cc67, aperta con task wait-ok --files e 8 PNG, ne ha mostrati 6 nella web app: mancavano
02-lettura e 03-al-lavoro. Telefono e web chiedevano gli stessi file, che passano nella corsia dei file; in in_order la
chiave di una lettura era (op, sessione, argomento), senza il dispositivo, e la lettura piu' vecchia diventava «superseded»
anche se era dell'altro dispositivo, che riprovava due volte e restava con «non arrivata».

FC1 telefono e web chiedono gli stessi 8 file di una carta (sessione null) nello stesso giro di corsia: escono tutte e 16,
    nessun superseded
FC2 lo stesso dispositivo che ripete lo stesso file: vale l'ultima, la vecchia riceve superseded (come prima)
FC3 il cliente come in rate_key: uid, altrimenti by/device; due web senza uid ma con device diversi non si sostituiscono;
    la chat (transcript after=) dello stesso dispositivo resta «vale l'ultima»
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude" / "sessions").mkdir(parents=True)
(home / "ws").mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"), "default_account": "personale",
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(tmp / "relay")},
    "registry": {"file": str(tmp / "registry.json")}, "tabs": {"color_registry": str(tmp / "colors")},
}))
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE"):
    os.environ.pop(k, None)
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_PROC_SCAN_PIDS": "", "CM_RELAY_NO_PUSH": "1"})
spec = importlib.util.spec_from_file_location("cm_relay_fc", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RL)
written = []
RL.key = lambda: b"k" * 32
RL.rtdb = lambda method, path, *a, **k: written.append((method, path))
RL.C.encrypt = lambda doc, k: doc
RL.result_written = lambda cid: None


def sup():
    return sorted(p.split("/", 1)[1] for m, p in written if m == "PUT" and p.startswith("result/"))


files = [f"/w/carta/0{i}-passo.png" for i in range(1, 9)]
cmds = {}
for i, f in enumerate(files):
    cmds[f"ph-{i}"] = {"op": "file", "session": None, "arg": f, "uid": "u-phone", "by": "phone", "device": "pixel", "issued": 1795680000 + i}
    cmds[f"web-{i}"] = {"op": "file", "session": None, "arg": f, "uid": "u-web", "by": "web", "device": "chrome", "issued": 1795680000 + i}
got = [c for c, _ in RL.in_order(cmds)]
T.check("FC1 phone and web ask the same 8 files of a card in one lane round: all 16 come out, none superseded",
        sorted(got) == sorted(cmds) and sup() == [], f"{len(got)} {sup()}")

written.clear()
cmds2 = {"a1": {"op": "file", "session": None, "arg": files[1], "uid": "u-web", "issued": 1795680100},
         "a2": {"op": "file", "session": None, "arg": files[1], "uid": "u-web", "issued": 1795680101},
         "b1": {"op": "file", "session": None, "arg": files[1], "uid": "u-phone", "issued": 1795680100}}
got2 = [c for c, _ in RL.in_order(cmds2)]
T.check("FC2 the same device asking the same file again: the newest runs, the older gets superseded (as before); the other device's runs",
        sorted(got2) == ["a2", "b1"] and sup() == ["a1"], f"{got2} {sup()}")

written.clear()
cmds3 = {"w1": {"op": "file", "session": "x", "arg": files[2], "by": "web", "device": "chrome-a", "issued": 1795680200},
         "w2": {"op": "file", "session": "x", "arg": files[2], "by": "web", "device": "chrome-b", "issued": 1795680201},
         "t1": {"op": "transcript", "session": "x", "arg": "200:after=e-1", "uid": "u-web", "issued": 1795680202},
         "t2": {"op": "transcript", "session": "x", "arg": "200:after=e-2", "uid": "u-web", "issued": 1795680203},
         "t3": {"op": "transcript", "session": "x", "arg": "200:after=e-2", "uid": "u-phone", "issued": 1795680203}}
got3 = [c for c, _ in RL.in_order(cmds3)]
T.check("FC3 the client as in rate_key: two webs with no uid but different devices both run; the chat's after= of one device: only the newest, the other device's too",
        sorted(got3) == ["t2", "t3", "w1", "w2"] and sup() == ["t1"], f"{got3} {sup()}")

T.rm(str(tmp))
T.finish()
