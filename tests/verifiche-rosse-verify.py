#!/usr/bin/env python3
"""Le verifiche della master non stanno in Ti aspetta e non mostrano l'uscita del controllo (28/11/2026,
supervisor-verifiche-rosse-frasi-28-11, contratto 1.165).

Alle 16:47 la persona: «perché vedo questi errori?». In Ti aspetta c'era una carta VERIFICA «Bordo attorno alla master ·
AssertionError: build-android.yml non contiene f9f23b6e · La fa la master»: un compito con le prove tutte ok e il
controllo rosso, che resettle ricontrolla e chiude da solo. Ora quelle voci non sono in `attese` (ne' nei conti di
«Tocca a te»); dove restano (op prove, la prova per numero) una frase dice cosa manca e che il compito si chiude da solo.
L'uscita grezza resta solo per la master (CLI `task prove`). Registro temporaneo.

VR1 attese: nessuna voce rosso dal registro per un compito con le prove tutte ok e il controllo rosso; il rosso della
    guardia (task_red, col suo «Fatto») resta
VR2 op prove: red[].reason e' la frase della build, mai l'uscita del controllo; null mentre il controllo gira
VR3 op prove col numero della prova: state red, text la stessa frase
VR4 cosa manca, dal controllo e dalla sua uscita: release, pubblicazione, push, altrimenti il controllo verde
VR5 la master vede ancora l'uscita: `task prove` (RED …) e prove_list.reason; prove_list.text e' la frase
VR6 il contratto: details/relay.md ha la 1.165; la fixture attese-rd-sample.json non ha un rosso del registro
"""
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
(home / ".claude").mkdir(parents=True)
root = tmp / "ws"
proj = root / "proj"
proj.mkdir(parents=True)
(tmp / "r").mkdir()
cfg = tmp / "c.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "s"), "accounts": {"personal": {"config_dir": str(home / ".claude")}}, "default_account": "personal",
                           "workspace": {"root": str(root), "root_session_name": "master", "project_dirs": ["."]}, "relay": {"dir": str(tmp / "r")}}))
DB = tmp / "t.db"
fake = tmp / "fake-cm"
fake.write_text("#!/bin/sh\nexit 0\n")
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR", "CM_APPROVE_VIA")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(DB), "CM_RELAY_CM": "/bin/false",
            "CM_TASK_TALK_BIN": str(fake)})
os.environ.update(ENV)
IT = json.loads((T.SCRIPTS.parent / "messages" / "it.json").read_text(encoding="utf-8"))


def it(k):
    return IT.get(k, f"<{k} missing>")


RAW = "AssertionError: build-android.yml non contiene f9f23b6e"


def cm(*args):
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), *args], capture_output=True, text=True, env=ENV, timeout=120)
    return r.returncode, r.stdout, r.stderr


def add(tid, check, short):
    c = {"schema_version": 1, "task": {
        "id": tid, "title": f"compito {tid}", "short": short, "type": "feature", "plan": None,
        "where": {"project": str(proj), "session": "s-app", "host": "local", "account": None},
        "outcome": {"expected": "Il bordo attorno alla master si vede sul telefono.", "kind": "custom", "proof": "commit, build, prova"},
        "check": {"cmd": check, "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open",
        "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}}
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), "add", "-"], input=json.dumps(c), capture_output=True, text=True, env=ENV, timeout=120)
    assert r.returncode == 0, r.stderr
    cm("start", tid)
    cm("step", tid, "commit e build fatti")
    cm("prova", tid, "--build", "b1", "--dove", "home", "--fai", "guarda la master", "--atteso", "il bordo")


def load(name):
    sp = importlib.util.spec_from_file_location(name.replace("-", "_") + "_vr", T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


CHECKS = {
    "vr_build": (f"echo '{RAW}' >&2; exit 1", "Bordo attorno alla master"),
    "vr_release": ("test -f released.txt", "Release della 2.0"),
    "vr_deploy": ("test -f deploy-web.ok", "Pagina pubblicata"),
    "vr_push": ("git log origin/main --oneline 2>/dev/null | grep -q abc1234", "Commit pubblicato"),
    "vr_other": ("test -f non-ancora.txt", "Altro"),
    "vr_running": ("test -f non-ancora.txt", "In corso"),
}
for tid, (chk, short) in CHECKS.items():
    add(tid, chk, short)
TK = load("cm-tasks")
con = TK.connect()
nums = {r["task"]: r["n"] for r in con.execute("SELECT task, n FROM prove")}
TK.prove_mark(con, list(nums.values()), "ok", "maintainer", "phone")
for tid in CHECKS:
    if tid != "vr_running":   # vr_running: tutte ok, il controllo non ha ancora girato (reason null)
        cm("settle", tid)
now_ = int(time.time())
pl = TK.prove_list(con)
red = {x["task"]: x for x in pl["red"]}
assert set(red) == set(CHECKS), red

A = load("cm-attese")
items = A.collect(now_, state={"sessions": [{"name": "s-app", "state": "idle"}],
                               "attention": [{"kind": "task_red", "session": "s-app", "ref": "vr_other", "title": "vr_other: il controllo è rosso", "since": now_ - 5}]})
mine = [a for a in items if a.get("task") in CHECKS]
guard = [a for a in mine if a["kind"] == "rosso"]
T.check("VR1 attese: no rosso item from the registry for a task with every test ok and a red check; the guard's rosso (task_red, with its «Fatto») stays",
        not [a for a in A.registry_items(now_) if a["kind"] == "rosso"] and len(guard) == 1 and guard[0]["task"] == "vr_other" and guard[0]["type"] == "chat"
        and any((x.get("cmd") or {}).get("op") == "attention_done" for x in guard[0].get("actions") or []) and RAW not in json.dumps(items, ensure_ascii=False),
        json.dumps(mine, ensure_ascii=False)[:600])

RL = load("cm-relay")
ok2, t2 = RL.execute({"id": "a", "op": "prove"})[:2]
op = {x["task"]: x for x in json.loads(t2)["red"]} if ok2 else {}
T.check("VR2 op prove: red[].reason is the build sentence, never the check's output; null while the check runs",
        op.get("vr_build", {}).get("reason") == it("tasks.red_text_build") and "f9f23b6e" not in t2 and "AssertionError" not in t2
        and op.get("vr_running", {}).get("reason", "x") is None, t2[:600])

ok3, t3 = RL.execute({"id": "b", "op": "prove", "arg": str(nums["vr_build"])})[:2]
one = json.loads(t3) if ok3 else {}
T.check("VR3 op prove with the test's number: state red, text the same sentence",
        one.get("state") == "red" and one.get("text") == it("tasks.red_text_build"), t3[:400])

want = {"vr_release": "tasks.red_text_release", "vr_deploy": "tasks.red_text_deploy", "vr_push": "tasks.red_text_push", "vr_other": "tasks.red_text_check"}
T.check("VR4 what is missing, from the check and its output: the release, the publication, the push, otherwise the green check; each says the task closes by itself",
        all(op.get(t, {}).get("reason") == it(k) for t, k in want.items()) and all("si chiude da solo" in it(k) for k in list(want.values()) + ["tasks.red_text_build"]),
        json.dumps({t: op.get(t, {}).get("reason") for t in want}, ensure_ascii=False))

rc5, out5, _ = cm("prove")
T.check("VR5 the master still sees the output: `task prove` (RED …) and prove_list.reason; prove_list.text is the sentence",
        rc5 == 0 and f"RED vr_build: {RAW}" in out5 and red["vr_build"]["reason"] == RAW and red["vr_build"].get("text") == it("tasks.red_text_build")
        and red["vr_running"]["reason"] is None and red["vr_running"].get("text", "x") is None, out5[-400:] + json.dumps(red["vr_build"], ensure_ascii=False))

doc = (T.ROOT / "details" / "relay.md").read_text(encoding="utf-8")
c165 = doc.split("Contract 1.165", 1)[1].split("\nContract 1.", 1)[0] if "Contract 1.165" in doc else ""
fx = json.loads((T.ROOT / "tests" / "fixtures" / "relay" / "attese-rd-sample.json").read_text(encoding="utf-8"))
fx_red = [a for a in fx["attese"]["items"] if a["kind"] == "rosso"]
T.check("VR6 the contract: details/relay.md has 1.165 (rosso, op prove); the fixture attese-rd-sample.json has no rosso from the registry, only the guard's",
        "`rosso`" in c165 and "op `prove`" in c165 and len(fx_red) == 1 and fx_red[0]["type"] == "chat", json.dumps(fx_red, ensure_ascii=False)[:400])

con.close()
T.rm(tmp)
T.finish()
