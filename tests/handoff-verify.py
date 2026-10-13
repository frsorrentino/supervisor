#!/usr/bin/env python3
"""Verifica l'handoff di supervisor (13/10/2026, contratto 1.51).

H1 --prepare: senza --here il file va nella cartella di stato (handoffs/<cartella>-<data>-<ora>.md); con --here in
   <cartella>/docs/handoff-<data>.md, e con -HHMM se quello del giorno c'e' gia'; le sette sezioni; la riga THEN
H2 --written: file assente → exit 1; presente → puntatore per la cartella, dimensione stampata
H3 --announce: la riga HANDOFF col percorso una volta sola; un puntatore vecchio (oltre ANNOUNCE_H ore) non dice niente
H4 il SessionStart con source «clear» porta la riga nel contesto; il secondo avvio non la ripete
H5 handoffs_of (relay): fable-director abilitato → il suo comando; solo supervisor → il nostro; nessuno dei due → None;
   sessione su un altro host → None
"""
import importlib.util as _ilu
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
proj = home / "ws" / "alfa"
proj.mkdir(parents=True)
for d, plugins in ((".claude", {"fable-director@fsorrentino": True, "supervisor@supervisor-dev": True}),
                   (".claude-work", {"supervisor@supervisor-dev": True, "fable-director@fsorrentino": False}),
                   (".claude-bare", {})):
    (home / d).mkdir(parents=True)
    (home / d / "settings.json").write_text(json.dumps({"enabledPlugins": plugins}))
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "en", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws")},
    "accounts": {"personal": {"config_dir": str(home / ".claude")}, "work": {"config_dir": str(home / ".claude-work")},
                 "bare": {"config_dir": str(home / ".claude-bare")}},
    "default_account": "personal",
}))
ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg)}


def ho(*args, cwd=proj):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-handoff.py")] + list(args), capture_output=True, text=True,
                          env=ENV, cwd=str(cwd), timeout=30)


def field(out, key):
    return next((l.split(": ", 1)[1] for l in out.splitlines() if l.startswith(key + ": ")), "")


today = time.strftime("%Y-%m-%d")
r = ho("--prepare")
p_state = field(r.stdout, "PATH")
T.check("H1 --prepare without --here → the state folder, named after the project and dated",
        r.returncode == 0 and p_state.startswith(str(tmp / "state" / "handoffs" / f"alfa-{today}-")) and p_state.endswith(".md"), r.stdout + r.stderr)
T.check("H1 seven sections and the THEN line", len(field(r.stdout, "SECTIONS").split(" | ")) == 7
        and field(r.stdout, "THEN") == f"supervisor handoff --written {p_state}", r.stdout)
r = ho("--prepare", "--here")
p_here = field(r.stdout, "PATH")
T.check("H1 --here → <project>/docs/handoff-<date>.md, docs/ created", p_here == str(proj / "docs" / f"handoff-{today}.md") and (proj / "docs").is_dir(), r.stdout)
Path(p_here).write_text("## 1. Goal and state\nall green\n")
r = ho("--prepare", "--here")
T.check("H1 --here when today's file exists → -HHMM suffix", field(r.stdout, "PATH").startswith(str(proj / "docs" / f"handoff-{today}-")), r.stdout)

r = ho("--written", str(proj / "docs" / "missing.md"))
T.check("H2 --written on a missing file → exit 1", r.returncode == 1, r.stdout + r.stderr)
r = ho("--written", p_here)
T.check("H2 --written → size printed, pointer recorded", r.returncode == 0 and "words" in r.stdout
        and any((tmp / "state" / "handoffs").glob("last-*.json")), r.stdout + r.stderr)

r = ho("--announce", str(proj))
T.check("H3 --announce → the HANDOFF line with the path", r.stdout.startswith("HANDOFF: first read " + p_here), r.stdout)
r = ho("--announce", str(proj))
T.check("H3 a second --announce says nothing", r.stdout.strip() == "", r.stdout)
ho("--written", p_here)
ptr = next((tmp / "state" / "handoffs").glob("last-*.json"))
d = json.loads(ptr.read_text()); d["at"] -= 7 * 3600; ptr.write_text(json.dumps(d))
r = ho("--announce", str(proj))
T.check("H3 a pointer older than ANNOUNCE_H hours says nothing", r.stdout.strip() == "", r.stdout)


def session_start(source):
    payload = json.dumps({"session_id": "sid-h4", "cwd": str(proj), "source": source, "hook_event_name": "SessionStart"})
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "SessionStart"], input=payload, capture_output=True,
                          text=True, env=dict(ENV, CM_RELAY_NO_PUSH="1"), cwd=str(proj), timeout=60)


ho("--written", p_here)
r = session_start("clear")
T.check("H4 SessionStart after /clear carries the HANDOFF line", "HANDOFF: first read " + p_here in r.stdout, r.stdout[-400:] + r.stderr[-400:])
r = session_start("clear")
T.check("H4 the next start does not repeat it", "HANDOFF:" not in r.stdout, r.stdout[-400:])

os.environ.update(ENV)
spec = _ilu.spec_from_file_location("cm_relay", T.SCRIPTS / "cm-relay.py")
RL = _ilu.module_from_spec(spec)
spec.loader.exec_module(RL)
got = RL.handoffs_of([{"tmux": "a", "account": "personal"}, {"tmux": "b", "account": "work"}, {"tmux": "c", "account": "bare"},
                      {"tmux": "win:d", "account": "personal", "host": "win"}])
T.check("H5 handoffs_of: fable-director → its command; supervisor only → ours; neither → None; another host → None",
        got == {"a": "/fable-director:handoff --here", "b": "/supervisor:handoff --here", "c": None, "win:d": None}, str(got))

T.rm(tmp)
T.finish()
