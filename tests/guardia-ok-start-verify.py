#!/usr/bin/env python3
"""La guardia della corsia, su un compito con l'ok gia' registrato ma non ancora partito, dice di lanciare `supervisor
task start ID` e non che l'ok manca (22/11/2026, supervisor-guardia-ok-start-22-11, nota supervisor-d5ee4048).

Il 22/11 alle 10:21 una sessione con l'ok in mano ha letto «non c'è un compito di un piano approvato né un ok
registrato»: `task approve` su un compito assigned registra l'ok ma lo lascia assigned (solo awaiting_ok diventa
approved), e la guardia contava solo i compiti running o approved.

GO1 compito assigned, nessun ok: il push negato col messaggio di oggi (ok mancante)
GO2 lo stesso compito con l'ok registrato (resta assigned): il push ancora negato, ma il motivo dice
    «supervisor task start ID» e non dice che l'ok manca; in inglese e in italiano
GO3 dopo `supervisor task start ID`: il push passa
GO4 l'ok di un compito di un'altra cartella non cambia il messaggio qui
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="cm-guardia-ok-"))
proj, other = tmp / "proj", tmp / "other"
proj.mkdir()
other.mkdir()
cfg = tmp / "config.json"
DB = tmp / "tasks.db"
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update(CM_TASKS_DB=str(DB), CC_SUPERVISOR_CONFIG=str(cfg))


def config(lang):
    cfg.write_text(json.dumps({"language": lang, "tasks": {"guard": True}}))


def hook(cmd, cwd=proj):
    p = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-lane-guard.py")], capture_output=True, text=True, env=ENV, timeout=30,
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd), "hook_event_name": "PreToolUse"}))
    if p.returncode != 0:
        return "error " + p.stderr[-300:]
    if not p.stdout.strip():
        return "allow"
    out = json.loads(p.stdout)["hookSpecificOutput"]
    return out["permissionDecision"] + ": " + out["permissionDecisionReason"]


def cm(*args, stdin=None):
    return subprocess.run([str(T.SCRIPTS / "supervisor"), *args], input=stdin, capture_output=True, text=True, env=ENV, timeout=60)


def add(tid, project):
    c = {"schema_version": 1, "task": {"id": tid, "title": f"publish {tid}", "plan": None,
                                       "where": {"project": str(project), "session": "alfa", "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": None, "timeout_s": 10}, "perimeter": [], "lane": "closed", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}}
    return cm("task", "add", "-", stdin=json.dumps(c))


def state(tid):
    r = cm("task", "show", tid, "--json")
    try:
        return json.loads(r.stdout).get("state")
    except ValueError:
        return r.stdout[:80]


config("en")
cm("task", "list")
r = add("pub-ok", proj)
r_o = add("altro-ok", other)

# GO1
d1 = hook("git push origin main")
T.check("GO1 task assigned, no ok: push denied with today's message (no ok registered)",
        r.returncode == 0 and state("pub-ok") == "assigned" and d1.startswith("deny") and "task start" not in d1, f"{r.stderr[:200]} {state('pub-ok')} {d1}")

# GO4 (prima): l'ok di un compito di un'altra cartella
T.device_ok("approve", "altro-ok", "maintainer (phone)", "ok", ENV)
d4 = hook("git push origin main")
T.check("GO4 the ok of a task in another folder does not change the message here", d4.startswith("deny") and "task start" not in d4, d4)

# GO2
T.device_ok("approve", "pub-ok", "maintainer (phone)", "ok", ENV)
d2 = hook("git push origin main")
T.check("GO2 ok registered, task still assigned: push still denied, the reason says «supervisor task start pub-ok», not that the ok is missing",
        state("pub-ok") == "assigned" and d2.startswith("deny") and "supervisor task start pub-ok" in d2 and "no recorded ok" not in d2,
        f"{state('pub-ok')} {d2}")
config("it")
d2i = hook("git push origin main")
T.check("GO2 the same in Italian: «Lancia supervisor task start pub-ok», not «né un ok registrato»",
        d2i.startswith("deny") and "supervisor task start pub-ok" in d2i and "né un ok registrato" not in d2i, d2i)

# GO3
r3 = cm("task", "start", "pub-ok")
d3 = hook("git push origin main")
T.check("GO3 after supervisor task start: the push passes", r3.returncode == 0 and d3 == "allow", f"{r3.stdout[:200]} {r3.stderr[:200]} {state('pub-ok')} {d3}")

T.rm(str(tmp))
T.finish()
