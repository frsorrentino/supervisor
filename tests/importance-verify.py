#!/usr/bin/env python3
"""L'importanza dei compiti (05/11/2026, supervisor-importanza-compito-05-11): il consiglio di modello di fable-director
segue quanto pesa il lavoro. Tutto su un registro temporaneo.

IM1 dichiarata nel contratto (task.importance), accettata da add e da amend; un valore fuori lista rifiutato
IM2 dedotta dai fatti del registro: alta con un ok che ha undo no, con un esito deploy, in una cartella ad alta posta
    (default personali/rino, clienti, enti; tasks.high_stakes_dirs la cambia); bassa per chore e prova; media altrimenti
IM3 il file per advice.py (~/.claude/supervisor/task-importance.json, CM_IMPORTANCE_FILE): version 1, una voce per
    cartella col compito attivo — prima running, poi l'ultimo passo piu' recente —; riscritto in modo atomico dopo un
    comando che cambia il registro, mai dopo una lettura; un compito chiuso ne esce
IM4 task importance --cwd: la cartella piu' vicina risalendo; nessun compito → tutto null, exit 0
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
home.mkdir()
ws = tmp / "ws"
P = {k: ws / v for k, v in {"app": "personali/app", "rino": "personali/rino/atti", "cli": "clienti/acme", "low": "personali/low",
                            "dep": "personali/dep", "undo": "personali/undo", "mine": "altro/mio"}.items()}
for d in P.values():
    d.mkdir(parents=True)
cfg = tmp / "config.json"
BASE_CFG = {"language": "it", "state_dir": str(tmp / "state"), "accounts": {"personal": {"config_dir": str(home / ".claude")}},
            "default_account": "personal", "workspace": {"root": str(ws), "root_session_name": "master"}, "relay": {"dir": str(tmp / "relay")}}
cfg.write_text(json.dumps(BASE_CFG))
IMP = tmp / "imp" / "task-importance.json"
fake = tmp / "fake-cm"
fake.write_text("#!/bin/sh\nexit 0\n")
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA", "CM_RELAY_CM")}
ENV.update({"CM_TASKS_DB": str(tmp / "tasks.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home),
            "CM_TASK_TALK_BIN": str(fake), "CM_IMPORTANCE_FILE": str(IMP)})
CLI = str(T.SCRIPTS / "supervisor")


def cm(*args, stdin=None):
    return T.cli_or_device(CLI, args, ENV, stdin)


def add(tid, project, **kw):
    t = {"id": tid, "title": f"unit {tid}", "plan": None, "where": {"project": str(project), "session": "sess", "host": "local", "account": None},
         "check": {"cmd": "true", "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open", "depends_on": [], "route": "session",
         "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}
    t.update(kw)
    return cm("add", "-", stdin=json.dumps({"schema_version": 1, "task": t}))


def imp(tid):
    return json.loads(cm("importance", tid, "--json")[1])


def filed():
    return json.loads(IMP.read_text())


# IM1 dichiarata
add("im-decl", P["mine"], importance="bassa")
T.check("IM1 declared in the contract: bassa, source declared", imp("im-decl")["importance"] == "bassa" and imp("im-decl")["source"] == "declared", str(imp("im-decl")))
rc_bad = add("im-bad", P["mine"], importance="altissima")[0]
T.check("IM1 a value out of the list refused by add", rc_bad != 0 and cm("show", "im-bad")[0] != 0, str(rc_bad))
r_am = cm("amend", "im-decl", "--importance", "alta")
T.check("IM1 amend --importance alta: declared alta, a step says so", r_am[0] == 0 and imp("im-decl")["importance"] == "alta"
        and any("importance: alta" in (s["text"] or "") for s in json.loads(cm("show", "im-decl", "--json")[1])["steps"]), str(r_am))
T.check("IM1 amend with a value out of the list refused, the importance unchanged", cm("amend", "im-decl", "--importance", "boh")[0] != 0 and imp("im-decl")["importance"] == "alta", "")

# IM2 dedotta
add("im-undo", P["undo"])
cm("wait-ok", "im-undo", "--what", "manda la PEC", "--where", "qui", "--undo", "no")
add("im-dep", P["dep"], outcome={"expected": "il sito pubblicato", "kind": "deploy", "proof": "deploy"},
    check={"cmd": "true || curl -s https://example.invalid", "cwd": None, "timeout_s": 30})
add("im-rino", P["rino"])
add("im-cli", P["cli"])
add("im-chore", P["low"], type="chore")
add("im-verify", P["low"], type="verify")
add("im-app", P["app"], type="feature")
got = {t: imp(t) for t in ("im-undo", "im-dep", "im-rino", "im-cli", "im-chore", "im-verify", "im-app")}
T.check("IM2 inferred alta: an ok with undo no, an outcome deploy, a folder under personali/rino and under clienti",
        all(got[t]["importance"] == "alta" and got[t]["source"] == "inferred" for t in ("im-undo", "im-dep", "im-rino", "im-cli"))
        and "deploy" in got["im-dep"]["why"] and "rino" in got["im-rino"]["why"], json.dumps(got, ensure_ascii=False))
T.check("IM2 inferred bassa for chore and verify (a test); media otherwise",
        got["im-chore"]["importance"] == "bassa" and got["im-verify"]["importance"] == "bassa" and got["im-app"]["importance"] == "media", json.dumps(got, ensure_ascii=False))
cfg.write_text(json.dumps(dict(BASE_CFG, tasks={"high_stakes_dirs": ["personali/app"]})))
T.check("IM2 tasks.high_stakes_dirs replaces the default: personali/app is alta, personali/rino no longer",
        imp("im-app")["importance"] == "alta" and imp("im-rino")["importance"] == "media", str((imp("im-app"), imp("im-rino"))))
cfg.write_text(json.dumps(BASE_CFG))

# IM3 il file
f0 = filed()
T.check("IM3 the file: version 1, updated_at, one entry per folder with the keys of the contract, paths with /",
        f0["version"] == 1 and isinstance(f0["updated_at"], int) and set(f0["dirs"]) == {str(p) for k, p in P.items() if k != "app"} | {str(P["app"])}
        and all(set(v) == {"importance", "task", "source", "why", "state", "last_step_at"} for v in f0["dirs"].values()), json.dumps(f0)[:500])
add("im-app2", P["app"], type="chore")
cm("step", "im-app2", "--text", "un passo")
T.check("IM3 two tasks in one folder, none running: the one with the newest step is the active one",
        filed()["dirs"][str(P["app"])]["task"] == "im-app2" and filed()["dirs"][str(P["app"])]["state"] == "open", json.dumps(filed()["dirs"][str(P["app"])]))
cm("start", "im-app")
T.check("IM3 a running task wins over a newer step: im-app, state running, media",
        filed()["dirs"][str(P["app"])]["task"] == "im-app" and filed()["dirs"][str(P["app"])]["state"] == "running"
        and filed()["dirs"][str(P["app"])]["importance"] == "media", json.dumps(filed()["dirs"][str(P["app"])]))
IMP.unlink()
cm("show", "im-app")
cm("impegni")
cm("importance", "--cwd", str(P["app"]))
T.check("IM3 a read (show, impegni, importance) does not rewrite the file", not IMP.exists(), "")
cm("cancel", "im-dep")
T.check("IM3 a command that changes the registry rewrites it; a cancelled task leaves it, no .tmp left",
        IMP.exists() and str(P["dep"]) not in filed()["dirs"] and not list(IMP.parent.glob("*.tmp")), json.dumps(filed())[:300])

# IM4 --cwd
sub = P["rino"] / "fascicolo" / "allegati"
sub.mkdir(parents=True)
r4 = json.loads(cm("importance", "--cwd", str(sub), "--json")[1])
T.check("IM4 --cwd from a folder below: the nearest folder with a task (personali/rino/atti), alta", r4["task"] == "im-rino" and r4["importance"] == "alta", str(r4))
r4b = cm("importance", "--cwd", str(tmp), "--json")
T.check("IM4 no task in the folder or above: all null, exit 0", r4b[0] == 0 and json.loads(r4b[1]) == {"importance": None, "task": None, "source": None, "why": None}, str(r4b))
T.finish()
