#!/usr/bin/env python3
"""Dall'app si chiude anche una sessione aperta al PC (20/11/2026, supervisor-chiudi-attaccate-20-11).

Il maintainer alle 16:46: «perché la sessione non si chiude? Dobbiamo fare in modo che si possa chiudere.» Dall'app ha chiuso due
volte una sessione ferma da 4 ore ma con la finestra aperta sul Chromebook; il relay ha risposto alle 16:44:33 e alle
16:45:05 «… not closed: it is attached to the PC». close_check bloccava «attached» in ogni modo (now, after, force, save)
dalla 1.84, mentre `supervisor close` dal 07/10 chiude un'attaccata ferma chiudendo la sua scheda. Ora il relay fa lo
stesso, e le risposte della chiusura sono in italiano (l'app le mostra come arrivano).

CA1 op close (now) di una sessione ferma e aperta al PC: `supervisor close <nome> --subito`, la risposta «chiusa: <nome>»,
    salvata e nel registro; close_preview la dice chiudibile
CA2 «Salva e chiudi» (save true) sulla stessa: prima la richiesta del passaggio di consegne, poi a fine turno la chiusura
CA3 aperta al PC e al lavoro: now la tiene col motivo «sta lavorando»; after la arma e si chiude appena si ferma
CA4 la scheda non si trova: la risposta dice perche' (la frase di `supervisor close`), niente salvato come chiuso
CA5 le chiavi della chiusura in it.json sono in italiano (nessun «not closed», «attached», «closed:»); en.json resta inglese
CA6 la catena vera: una sessione lanciata con la finestra (un client tmux attaccato), op close dal relay col supervisor vero
    → la scheda chiusa dal chrome-bridge (finto) e la sessione finita; la risposta «chiusa: <nome>»
"""
import datetime
import importlib.util
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
alfa = ws / "personali" / "alfa"
alfa.mkdir(parents=True)
(home / ".claude" / "sessions").mkdir(parents=True)
state = tmp / "state"
state.mkdir()
rdir = tmp / "relay"
rdir.mkdir()
bridge_log = tmp / "bridge.log"
bridge = tmp / "fake-bridge.py"
bridge.write_text(f"""import json, os, subprocess, sys
cmd = sys.argv[1]
params = json.loads(sys.argv[sys.argv.index("--json") + 1]) if "--json" in sys.argv else {{}}
open({str(bridge_log)!r}, "a").write(cmd + " " + json.dumps(params) + "\\n")
names = [x for x in os.environ.get("FAKE_TABS", "").split(",") if x]
if cmd == "get_tabs":
    print(json.dumps([{{"id": 70 + i, "url": "chrome-untrusted://terminal/html/terminal.html?command=vmshell&args[]=--&args[]=supervisor&args[]=attach&args[]=" + n,
                       "title": "Terminale " + n}} for i, n in enumerate(names)]))
elif cmd == "tab_action":
    n = names[params["tab_id"] - 70]
    subprocess.run(["tmux"] + os.environ["CM_TMUX_ARGS"].split() + ["detach-client", "-s", "=" + n])   # la scheda chiusa: il client se ne va
    print(json.dumps({{"ok": True}}))
""")
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master", "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(rdir), "duplicate_prompt_s": 0},
    "session": {"dialog_patterns": [], "claude_args": ["--dangerously-skip-permissions"], "startup_timeout_s": 25, "death_check_s": 1},
    "terminal": {"backend": "fake", "attach_wait_s": 2, "attach_retry_wait_s": 1},
    "registry": {"file": str(tmp / "registry.json")},
    "tabs": {"color_registry": str(tmp / "colors")},
    "sessions": {"max_sessions": 50},
    "tile": {"chrome_bridge_cli": str(bridge)},
}))
calls, alive = tmp / "calls.log", tmp / "alive.json"
close_rc = tmp / "close-rc"
fake_cm = tmp / "supervisor"
fake_cm.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{calls}"
case "$1" in
  sessions) cat "{alive}" ;;
  registry) echo '{{"sessioni": []}}' ;;
  close) if [ -s "{close_rc}" ]; then echo "supervisor close: «$2» è aperta in una finestra, ma non trovo con certezza la sua scheda (nessuna scheda): non la chiudo." >&2; exit 4; fi
         echo "  chiusa: $2" ;;
  talk) echo "consegnato" ;;
  interrupt) echo "fermata" ;;
esac
""")
fake_cm.chmod(0o755)
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE"):
    os.environ.pop(k, None)
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_RELAY_CM": str(fake_cm),
                   "CM_CLOSESAVE_CM": str(fake_cm), "CM_PROC_SCAN_PIDS": ""})
IT = json.loads((T.ROOT / "supervisor/messages/it.json").read_text(encoding="utf-8"))
EN = json.loads((T.ROOT / "supervisor/messages/en.json").read_text(encoding="utf-8"))


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_ca", T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RL = load("cm-relay")
RL.push_async = lambda *a, **k: None
RL.git_dirty = lambda cwd: (0, [])


def row(status="idle", waiting=False, attached=True):
    return {"pid": 5, "name": "alfa", "tmux": "alfa", "cwd": str(alfa), "status": status, "waiting": waiting,
            "session_id": "S-A", "account": "personale", "attached": attached, "started_at": 1789200000000}


def log():
    return calls.read_text().splitlines() if calls.exists() else []


def reset(r):
    calls.write_text("")
    close_rc.write_text("")
    for f in (state / "closed").glob("*.json") if (state / "closed").is_dir() else []:
        f.unlink()
    RL.close_armed_path().unlink(missing_ok=True)
    (state / "ledger.jsonl").unlink(missing_ok=True)
    alive.write_text(json.dumps([r]))


def stop_event(t):
    with open(state / "ledger.jsonl", "a") as f:
        f.write(json.dumps({"ts": datetime.datetime.fromtimestamp(t).isoformat(timespec="seconds"), "event": "stop", "session_id": "S-A",
                            "cwd": str(alfa)}) + "\n")


def led(event):
    p = state / "ledger.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip() and json.loads(x).get("event") == event] if p.exists() else []


# ------------------------------------------------------------------ CA1
reset(row())
pv = json.loads(RL.execute({"op": "close_preview", "arg": "alfa", "by": "phone"})[1] or "{}").get("sessions") or [{}]
ok1, t1 = RL.execute({"op": "close", "arg": "alfa", "by": "phone"})[:2]
rec1 = state / "closed" / "alfa.json"
T.check("CA1 op close (now) of an idle session open on the PC: `supervisor close alfa --subito`, «chiusa: alfa», saved and in the ledger; close_preview says closable",
        ok1 is True and t1 == "chiusa: alfa" and "close alfa --subito" in log() and rec1.is_file() and len(led("closed-from-app")) == 1
        and pv[0].get("closable") is True and pv[0].get("blocked_by") is None, f"{ok1} {t1!r} {log()} {pv}")

# ------------------------------------------------------------------ CA2
reset(row())
ok2, t2 = RL.execute({"op": "close", "arg": "alfa", "save": True, "by": "phone"})[:2]
a2 = RL.close_armed().get("alfa") or {}
talk2 = [c for c in log() if c.startswith("talk alfa ")]
early2 = any(c.startswith("close ") for c in log())
stop_event(time.time() + 2)
RL.close_tick(time.time() + 5)
T.check("CA2 «Salva e chiudi» (save true) on it: first the handoff request, then at the end of that turn the closing",
        ok2 is True and a2.get("mode") == "save" and len(talk2) == 1 and IT["close.save_prompt"][:40] in talk2[0] and not early2
        and "close alfa --subito" in log() and "alfa" not in RL.close_armed(), f"{t2} {a2} {log()}")

# ------------------------------------------------------------------ CA3
reset(row("busy"))
ok3n, t3n = RL.execute({"op": "close", "arg": "alfa", "by": "phone"})[:2]
ok3a, t3a = RL.execute({"op": "close", "arg": "alfa", "when": "after", "by": "phone"})[:2]
a3 = RL.close_armed().get("alfa") or {}
RL.close_tick()
still3 = "alfa" in RL.close_armed() and not any(c.startswith("close ") for c in log())
alive.write_text(json.dumps([row()]))
RL.close_tick()
T.check("CA3 open on the PC and working: now keeps it with «sta lavorando»; after arms it and it closes as soon as it stops",
        ok3n is False and t3n == "alfa non chiusa: sta lavorando" and ok3a is True and t3a == "alfa si chiude appena finisce"
        and a3.get("mode") == "after" and still3 and "close alfa --subito" in log() and "alfa" not in RL.close_armed(),
        f"{t3n!r} {t3a!r} {a3} {still3} {log()}")

# ------------------------------------------------------------------ CA4
reset(row())
close_rc.write_text("4")
ok4, t4 = RL.execute({"op": "close", "arg": "alfa", "by": "phone"})[:2]
T.check("CA4 the tab cannot be found: the answer says why (the sentence of `supervisor close`), nothing saved as closed",
        ok4 is False and t4.startswith("alfa non chiusa: supervisor close: «alfa» è aperta in una finestra") and not rec1.exists()
        and not led("closed-from-app"), f"{ok4} {t4!r}")

# ------------------------------------------------------------------ CA5
CLOSE_KEYS = [k for k in IT if k.startswith(("relay.cmd_close", "relay.close_why"))]
bad = [k for k in CLOSE_KEYS if any(w in IT[k] for w in ("not closed", "attached", "closed:"))]
T.check("CA5 the closing keys of it.json are in Italian (no «not closed», «attached», «closed:»); en.json stays English",
        len(CLOSE_KEYS) >= 12 and not bad and EN["relay.cmd_close_kept"] == "{name} not closed: {why}" and EN["relay.cmd_close_done"] == "closed: {names}",
        bad)

# ------------------------------------------------------------------ CA6: la catena vera
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
beta = ws / "personali" / "beta"
beta.mkdir(parents=True)
ok6, t6, gone6, tabs6, attached6, st6 = None, "", False, [], None, ""
with T.PrivateTmux() as tm:
    env6 = dict(os.environ, PATH=os.environ["PATH"], WAYLAND_DISPLAY="fake-0", CM_TMUX_ARGS=tm.env["CM_TMUX_ARGS"], CM_CLAUDE_BIN=str(FAKE),
                FAKE_CLAUDE_SCENARIO="plain", CM_TERMINAL_FAKE_LOG=str(tmp / "fake-terminal.log"), CM_TERMINAL_FAKE_ATTACH="1", FAKE_TABS="beta")
    env6.pop("CM_RELAY_CM", None)
    tm("new-session", "-d", "-s", "master", "sleep 600", env=env6)
    r = subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), str(beta)], capture_output=True, text=True, env=env6, timeout=120)
    sess = []
    for _ in range(60):
        out = subprocess.run([str(T.SCRIPTS / "supervisor"), "sessions", "--json"], capture_output=True, text=True, env=env6, timeout=60).stdout
        sess = [x for x in json.loads(out or "[]") if x.get("tmux") == "beta"]
        if sess and sess[0].get("attached") and sess[0].get("status") == "idle":
            break
        time.sleep(0.5)
    attached6, st6 = (sess[0].get("attached"), sess[0].get("status")) if sess else (None, "")
    p = subprocess.run([sys.executable, "-c", f"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("rl6", {str(T.SCRIPTS / "cm-relay.py")!r})
RL = importlib.util.module_from_spec(spec); spec.loader.exec_module(RL)
RL.push_async = lambda *a, **k: None
print(json.dumps(RL.execute({{"op": "close", "arg": "beta", "by": "phone"}})[:2]))
"""], capture_output=True, text=True, env=env6, timeout=180)
    try:
        ok6, t6 = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        t6 = p.stdout[-400:] + p.stderr[-600:]
    gone6 = T.wait_until(lambda: tm("has-session", "-t", "=beta").returncode != 0, timeout=10)
    tabs6 = bridge_log.read_text().splitlines() if bridge_log.exists() else []
T.check("CA6 the real chain: a session launched with its window (a tmux client attached), op close from the relay with the real supervisor "
        "→ the tab closed through chrome-bridge (fake) and the session ended; the answer «chiusa: beta»",
        attached6 is True and st6 == "idle" and ok6 is True and t6 == "chiusa: beta" and gone6 and any(x.startswith("tab_action ") for x in tabs6),
        f"launch rc {r.returncode} {r.stderr[-300:]} | attached {attached6} {st6} | {ok6} {t6!r} | gone {gone6} | {tabs6}")
T.finish()
