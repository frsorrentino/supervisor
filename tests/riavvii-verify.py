#!/usr/bin/env python3
"""Riavvii improvvisi e allineamenti (06/11/2026, supervisor-riavvii-allineamenti-06-11). Tutto in una cartella
temporanea: stato, trascrizioni, talk finto (CM_BG_TALK_BIN, CM_GUARD_CM), elenco dei plugin finto.

RV1 riavvio pulito: il SessionStart di una conversazione nuova nella stessa sessione tmux nomina quella di prima (id,
    `supervisor restart arm --resume ID`, /resume ID) e la lega nel ledger (prev_sid); una conversazione di prima
    senza trascrizione vera (una partenza vuota) non conta; al resume nessuna riga
RV2 lavoro interrotto: la conversazione ripresa ha quattro comandi in background — uno finito, uno fermato dalla
    sessione (TaskStop), uno con la sola notifica «didn't finish before the previous session ended», uno senza niente:
    il SessionStart elenca i due interrotti, una volta sola; dopo un avvio nuovo li legge dalla conversazione di prima
RV3 supervisor bg: il lavoro sopravvive alla morte del gruppo di processi che l'ha lanciato (la sessione), scrive log
    e rc.json nella cartella di stato, e a lavoro finito manda alla sessione proprietaria un talk con rc e righe finali
RV4 un lavoro di bg morto con la macchina (boot diverso): `bg list` lo dice morto; il SessionStart della sessione
    proprietaria lo elenca una volta, quello di un'altra sessione no
RV5 plugin_stale: la guardia vede la cache indietro; prima di 30 minuti tace, poi un talk alla master (versioni e
    comando per allineare) una volta sola per (account, cache, checkout); a cache allineata la chiave sparisce
"""
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
state = tmp / "state"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                           "default_account": "personale", "workspace": {"root": str(tmp), "root_session_name": "master"},
                           "registry": {"file": str(tmp / "registry.json")}, "hooks": {"kernel": {"enabled": False}}}))
talklog = tmp / "talk.log"
fake = tmp / "fake-cm"
fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {talklog}\nexit 0\n")
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CODE_SESSION_ID", "CM_HOOK_TMUX_NAME")}
ENV.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_BG_TALK_BIN": str(fake), "CM_GUARD_CM": str(fake),
            "CM_PROC_SCAN_PIDS": ""})
proj = home / ".claude" / "projects" / "-x-alfa"
proj.mkdir(parents=True)


def hook(source, sid, tmux="alfa"):
    p = {"session_id": sid, "source": source, "cwd": str(tmp), "transcript_path": str(proj / f"{sid}.jsonl"), "hook_event_name": "SessionStart"}
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "SessionStart"], input=json.dumps(p), capture_output=True, text=True,
                          env=dict(ENV, CM_HOOK_TMUX_NAME=tmux), timeout=60)


def ledger_start(sid):
    rows = [json.loads(x) for x in (state / "ledger.jsonl").read_text().splitlines() if x.strip()]
    return next((r for r in reversed(rows) if r.get("event") == "start" and r.get("session_id") == sid), {})


now = time.time()
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))  # noqa: E731


def bash_bg(tid, desc, cmd, t):
    return [{"type": "assistant", "timestamp": iso(t), "message": {"content": [{"type": "tool_use", "id": tid, "name": "Bash",
             "input": {"command": cmd, "description": desc, "run_in_background": True}}]}},
            {"type": "user", "timestamp": iso(t + 1), "toolUseResult": {"backgroundTaskId": "b" + tid[-4:]},
             "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": f"Command running in background with ID: b{tid[-4:]}."}]}}]


def note(tid, status, summary, t):
    return {"type": "user", "timestamp": iso(t), "message": {"role": "user", "content":
            f"<task-notification>\n<task-id>b{tid[-4:]}</task-id>\n<tool-use-id>{tid}</tool-use-id>\n<status>{status}</status>\n<summary>{summary}</summary>\n</task-notification>"}}


# RV1 / RV2: la conversazione A, con lavoro in background
A, B, C = "aaaaaaaa-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002", "cccccccc-0000-4000-8000-000000000003"
rows = [{"type": "user", "timestamp": iso(now - 3000), "message": {"role": "user", "content": "lavora " + "x" * 5000}}]
rows += bash_bg("toolu_done01", "Suite finita", "bash ./tests.sh", now - 2900) + [note("toolu_done01", "completed", 'Background command "Suite finita" completed (exit code 0)', now - 2800)]
rows += bash_bg("toolu_stop02", "Attesa fermata", "until false; do sleep 5; done", now - 2700)
rows += [{"type": "assistant", "timestamp": iso(now - 2650), "message": {"content": [{"type": "tool_use", "id": "toolu_ts", "name": "TaskStop", "input": {"task_id": "bop02"}}]}}]
rows += bash_bg("toolu_rel003", "Release 9.9.9 con la suite", "RELEASE_TESTS=wsl bash ./release.sh 9.9.9", now - 2600)
rows += bash_bg("toolu_suit04", "Suite completa su win", "bash ./tools-wsl-tests.sh .", now - 2500)
rows += [note("toolu_suit04", "stopped", "Background shell command didn't finish before the previous session ended", now - 100)]
(proj / f"{A}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
hook("startup", A)
r = hook("resume", A)
r_again = hook("resume", A)
T.check("RV2 resume: the start lists the two interrupted background commands (the release with no record, the suite with only Claude Code's «didn't finish» note), not the finished one nor the one the session stopped",
        "LAVORO INTERROTTO (2)" in r.stdout and "Release 9.9.9 con la suite" in r.stdout and "Suite completa su win" in r.stdout
        and "Suite finita" not in r.stdout and "Attesa fermata" not in r.stdout and "supervisor bg" in r.stdout, r.stdout + r.stderr)
T.check("RV2 said once: the next start of the same conversation does not repeat it", "LAVORO INTERROTTO" not in r_again.stdout, r_again.stdout)
T.check("RV1 resume of the same conversation: no «previous conversation» line", "CONVERSAZIONE DI PRIMA" not in r.stdout, r.stdout)

# RV1: avvio nuovo B nella stessa sessione → nomina A; poi C dopo una B vuota → ancora A
os.utime(proj / f"{A}.jsonl", (now - 60, now - 60))
rows2 = bash_bg("toolu_new005", "Build lunga", "make all", now - 50)
(proj / f"{A}.jsonl").open("a").write("".join(json.dumps(x) + "\n" for x in rows2))
rB = hook("startup", B)
lB = ledger_start(B)
rC = hook("startup", C)
rO = hook("startup", "dddddddd-0000-4000-8000-000000000004", tmux="beta")
T.check("RV1 a new conversation in the same tmux session names the previous one, with the command to resume it, and links it in the ledger",
        f"CONVERSAZIONE DI PRIMA di «alfa»: {A}" in rB.stdout and f"supervisor restart arm --resume {A}" in rB.stdout and f"/resume {A}" in rB.stdout
        and lB.get("prev_sid") == A, rB.stdout + json.dumps(lB))
T.check("RV2 after a new start the interrupted work is read from the previous conversation (only what was not said yet)",
        "LAVORO INTERROTTO (1)" in rB.stdout and "Build lunga" in rB.stdout and "Release 9.9.9" not in rB.stdout, rB.stdout)
T.check("RV1 a previous start with no real transcript (B, empty) does not count: C still names A; another tmux session names nothing",
        f"CONVERSAZIONE DI PRIMA di «alfa»: {A}" in rC.stdout and "CONVERSAZIONE DI PRIMA" not in rO.stdout, rC.stdout + "\n--\n" + rO.stdout)

# RV3: bg sopravvive alla sessione
talklog.write_text("")
launcher = subprocess.Popen(["bash", "-c", f"'{T.SCRIPTS / 'supervisor'}' bg --name prova-lunga -- 'sleep 2; echo riga-finale; exit 3'; sleep 60"],
                            env=dict(ENV, CM_HOOK_TMUX_NAME="alfa"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
time.sleep(1.2)
os.killpg(launcher.pid, signal.SIGKILL)   # la sessione muore col lavoro appena partito
launcher.wait()
jobs_dir = state / "jobs"
for _ in range(60):
    done = [d for d in jobs_dir.iterdir() if (d / "rc.json").is_file()] if jobs_dir.is_dir() else []
    if done and talklog.read_text().strip():
        break
    time.sleep(0.5)
jd = done[0] if done else None
rc = json.loads((jd / "rc.json").read_text()) if jd else {}
lst = json.loads(subprocess.run([str(T.SCRIPTS / "supervisor"), "bg", "list", "--json"], capture_output=True, text=True, env=ENV, timeout=60).stdout or "[]")
T.check("RV3 supervisor bg: the job outlives the killed launcher, log and rc.json in the state dir, owner talked with rc and last lines",
        jd is not None and rc.get("rc") == 3 and "riga-finale" in (jd / "out.log").read_text() and str(state) in str(jd)
        and talklog.read_text().startswith("talk alfa ") and "rc 3" in talklog.read_text() and "riga-finale" in talklog.read_text() and "--no-wait" in talklog.read_text()
        and any(j.get("state") == "done" and j.get("rc") == 3 and j.get("label") == "prova-lunga" for j in lst), talklog.read_text() + json.dumps(lst))

# RV4: lavoro morto con la macchina
dead = jobs_dir / "1106-104000-dead"
dead.mkdir()
(dead / "meta.json").write_text(json.dumps({"id": dead.name, "label": "Release morta", "cmd": "bash ./release.sh 9.9.8", "cwd": str(tmp), "owner": "alfa",
                                             "started": now - 4000, "boot": "un-altro-avvio", "pid": os.getpid()}))
lst = json.loads(subprocess.run([str(T.SCRIPTS / "supervisor"), "bg", "list", "--json"], capture_output=True, text=True, env=ENV, timeout=60).stdout or "[]")
r_other = hook("resume", "eeeeeeee-0000-4000-8000-000000000005", tmux="beta")
r_own = hook("resume", C)
r_own2 = hook("resume", C)
T.check("RV4 a bg job of another boot is «died» even with a live pid; the owner's start lists it once, another session's start does not",
        any(j["id"] == dead.name and j["state"] == "died" for j in lst) and "Release morta" not in r_other.stdout
        and "Release morta" in r_own.stdout and "Release morta" not in r_own2.stdout, json.dumps(lst)[:600] + r_own.stdout + "\n--\n" + r_own2.stdout)

# RV5: guardia, plugin_stale
plist = tmp / "plugins.json"
plist.write_text(json.dumps([{"id": "supervisor@supervisor-dev", "version": "0.0.1", "enabled": True}]))
os.environ.clear()
os.environ.update(dict(ENV, CM_FAKE_PLUGIN_LIST=str(plist)))
sp = importlib.util.spec_from_file_location("cm_guard_rv", T.SCRIPTS / "cm-guard.py")
G = importlib.util.module_from_spec(sp)
sp.loader.exec_module(G)
G.notify = lambda text: False   # niente Telegram
ver = json.loads((T.SCRIPTS.parent / ".claude-plugin" / "plugin.json").read_text())["version"]
talklog.write_text("")
t0 = time.time()
G.plugin_alerts(t0)
first = talklog.read_text()
G.plugin_alerts(t0 + 300)          # meno di 10 minuti: il giro non riparte
G.plugin_alerts(t0 + 1900)         # oltre i 30 minuti: avviso
second = talklog.read_text()
G.plugin_alerts(t0 + 2600)         # stessa chiave: niente di nuovo
third = talklog.read_text()
plist.write_text(json.dumps([{"id": "supervisor@supervisor-dev", "version": ver, "enabled": True}]))
G.plugin_alerts(t0 + 3300)
seen = json.loads((state / "plugin-stale.json").read_text())
T.check("RV5 plugin_stale: silent in the first 30 minutes, then one talk to the master with the versions and the fix, not repeated; aligned → key gone",
        first == "" and second.startswith("talk master ") and "0.0.1" in second and ver in second and "claude plugin update supervisor" in second
        and third == second and seen == {}, f"first={first!r} second={second[:300]!r} third_same={third == second} seen={seen}")

T.rm(tmp)
T.finish()
