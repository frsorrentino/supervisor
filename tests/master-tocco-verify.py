#!/usr/bin/env python3
"""Un tocco dall'app lancia la master anche quando non e' nella fotografia (19/11/2026, supervisor-master-tocco-19-11;
D2 e D3 di docs/analisi/avvio-master-2026-11-19.md).

Prima: `reopen master` dopo /exit (la chiusura esplicita la toglie dalla fotografia) o su un'installazione nuova dava
«master is not a closed session»; `launch <radice>` dava «no project», perche' la radice non e' fra le cartelle
dell'inventario. Il «Riapri» della card «La master non e' aperta» non poteva fare niente.

MT1 reopen della master senza fotografia, con una sua conversazione su disco → `launch <radice> --window --account
    <account della radice> --resume <ultima conversazione>`, ok
MT2 reopen della master senza fotografia e senza conversazioni → `launch <radice> --window --account …` senza --resume
    ne' --continue (conversazione nuova), ok, il testo lo dice
MT3 con un'altra sessione viva nella radice: mai --continue; si riprende l'ultima conversazione che non e' di una
    sessione viva; se c'e' solo quella dell'altra, conversazione nuova
MT4 launch con arg = la radice e la master chiusa → la stessa strada di MT1 (ok, launch della radice); con `text` il primo
    prompt va alla master con talk
MT5 launch della radice con la master viva → «gia' aperta», nessun launch (11/11, invariato)
MT6 reopen di un nome qualunque fuori dalla fotografia resta rifiutato («… is not a closed session»)
"""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
for d in ("ws/personali/alfa", ".claude/sessions"):
    (home / d).mkdir(parents=True)
state = tmp / "state"
rdir = tmp / "relay"
rdir.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "en", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master", "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(rdir), "duplicate_prompt_s": 0},
}))
argslog, alive, good = tmp / "args.log", tmp / "alive.json", tmp / "good.json"
alive.write_text("[]")
good.write_text(json.dumps({"sessioni": []}))
fake_cm = tmp / "supervisor"
fake_cm.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{argslog}"
case "$1" in
  sessions) cat "{alive}" ;;
  registry) if [ "$2" != "--closed" ]; then cat "{good}"; fi ;;
  launch) echo "sessione avviata" ;;
  talk) echo "consegnato" ;;
esac
""")
fake_cm.chmod(0o755)
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE"):
    os.environ.pop(k, None)
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_RELAY_CM": str(fake_cm)})

spec = importlib.util.spec_from_file_location("cm_relay_mt", T.SCRIPTS / "cm-relay.py")
RL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RL)
RL.push_async = lambda *a, **k: None   # nessuna push vera
EN = json.loads((T.ROOT / "supervisor/messages/en.json").read_text())

proj = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(ws)))


def conv(sid, mtime):
    proj.mkdir(parents=True, exist_ok=True)
    f = proj / f"{sid}.jsonl"
    f.write_text(json.dumps({"type": "user", "sessionId": sid}) + "\n")
    os.utime(f, (mtime, mtime))


def calls():
    return argslog.read_text().splitlines() if argslog.exists() else []


def run(op, session="master", arg=None, text=None):
    argslog.write_text("")
    cmd = {"id": "x", "op": op, "session": session, "arg": arg, "by": "phone-test"}
    if text:
        cmd["text"] = text
    r = RL.execute(cmd)
    return (r[0], r[1]), [c for c in calls() if c.startswith(("launch", "talk"))]


root = str(ws)
alfa_dir = str(ws / "personali" / "alfa")
# ------------------------------------------------------------------ MT2 (prima: nessuna conversazione)
res, lc = run("reopen")
T.check("MT2 reopen of the master outside the snapshot, no conversation on disk → `launch <root> --window --account personale`, no --resume and no --continue, ok",
        res[0] is True and lc == [f"launch {root} --window --account personale"], f"{res} {lc}")
T.check("MT2 the text says it started with a new conversation",
        "relay.cmd_reopen_done_new" in EN and res[1] == EN["relay.cmd_reopen_done_new"].format(name="master", account="personale"), res[1])

# ------------------------------------------------------------------ MT1
conv("M-OLD", 1789000000)
conv("M-NEW", 1789100000)
res, lc = run("reopen")
T.check("MT1 reopen of the master outside the snapshot (after /exit, or a fresh install) → `launch <root> --window --account personale --resume <latest conversation>`, ok",
        res[0] is True and lc == [f"launch {root} --window --account personale --resume M-NEW"], f"{res} {lc}")
T.check("MT1 the text is the usual «same conversation» one", res[1] == EN["relay.cmd_reopen_done"].format(name="master", account="personale"), res[1])

# ------------------------------------------------------------------ MT3
alive.write_text(json.dumps([{"pid": 5, "name": "otra", "tmux": "otra", "cwd": root, "status": "idle", "session_id": "M-NEW", "account": "personale"}]))
res, lc = run("reopen")
T.check("MT3 another live session in the root holds the latest conversation → the master takes the previous one (--resume M-OLD), never --continue",
        res[0] is True and lc == [f"launch {root} --window --account personale --resume M-OLD"], f"{res} {lc}")
(proj / "M-OLD.jsonl").unlink()
res, lc = run("reopen")
T.check("MT3 only the other live session's conversation on disk → a new conversation, never --continue",
        res[0] is True and lc == [f"launch {root} --window --account personale"], f"{res} {lc}")
alive.write_text("[]")
conv("M-OLD", 1789000000)

# ------------------------------------------------------------------ MT4
res, lc = run("launch", session="", arg=root)
T.check("MT4 launch with arg = the root and the master closed → ok, `launch <root> --window --account personale --resume M-NEW` (before: «no project»)",
        res[0] is True and lc == [f"launch {root} --window --account personale --resume M-NEW"], f"{res} {lc}")
res, lc = run("launch", session="", arg=root, text="buongiorno")
T.check("MT4 launch of the root with a first message → launch, then talk to the master with the message",
        res[0] is True and len(lc) == 2 and lc[0].startswith(f"launch {root} --window") and lc[1].startswith("talk master ") and "buongiorno" in lc[1], f"{res} {lc}")

# ------------------------------------------------------------------ MT5
alive.write_text(json.dumps([{"pid": 6, "name": "master", "tmux": "master", "cwd": root, "status": "idle", "session_id": "M-NEW", "account": "personale"}]))
res, lc = run("launch", session="", arg=root)
T.check("MT5 launch of the root with the master alive → «master is already running», no launch", res[0] is True and res[1] == EN["relay.cmd_reopen_alive"].format(name="master") and lc == [], f"{res} {lc}")
res, lc = run("reopen")
T.check("MT5 reopen of the master alive → «already running», no launch", res[0] is True and lc == [], f"{res} {lc}")
alive.write_text("[]")

# ------------------------------------------------------------------ MT6
res, lc = run("reopen", session="nessuno")
T.check("MT6 reopen of any other name outside the snapshot is still refused, no launch",
        res[0] is False and res[1] == EN["relay.cmd_reopen_unknown"].format(name="nessuno") and lc == [], f"{res} {lc}")

# ------------------------------------------------------------------ MT7 (win:supervisor-app, card ad8844d2)
# L'app, senza master nella fotografia e senza sessions[].master, manda «reopen master»: «master» vale come la radice
# anche quando root_session_name ha un altro nome
c = json.loads(cfg.read_text())
c["workspace"]["root_session_name"] = "regia"
cfg.write_text(json.dumps(c))
RL.core._CFG = None
res, lc = run("reopen", session="master")
T.check("MT7 root_session_name «regia»: reopen «master» (the app's alias) launches the root, ok",
        res[0] is True and lc == [f"launch {root} --window --account personale --resume M-NEW"], f"{res} {lc}")
alive.write_text(json.dumps([{"pid": 7, "name": "regia", "tmux": "regia", "cwd": root, "status": "idle", "session_id": "M-NEW", "account": "personale"}]))
res, lc = run("reopen", session="master")
T.check("MT7 with «regia» alive, reopen «master» → «already running», no launch",
        res[0] is True and res[1] == EN["relay.cmd_reopen_alive"].format(name="master") and lc == [], f"{res} {lc}")
alive.write_text(json.dumps([{"pid": 8, "name": "master", "tmux": "master", "cwd": alfa_dir, "status": "idle", "session_id": "S-X", "account": "personale"}]))
res, lc = run("reopen", session="master")
T.check("MT7 a real session named «master» elsewhere keeps its name: reopen «master» is about it (alive), no launch of the root",
        res[0] is True and lc == [], f"{res} {lc}")

T.finish()
