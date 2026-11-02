#!/usr/bin/env python3
"""Fase B del programma approvato il 01/11/2026 (contratto 1.102), tutto in una HOME temporanea.
rimasto_indietro  RI1 nuova: allineata; RI2 i percorsi cambiati la fanno indietro, gli altri no; RI3 il comando; RI4 «Va bene così»;
                  RI5 «Allinea» apre un compito; RI6 git che non risponde non fa indietro
stesso_difetto    SD1 positivi, negativi, errori; SD2 errore non e' negativo; SD3 controllo su cartella vuota; SD4 interrotto riprende;
                  SD5 un parziale non sovrascrive un completo; SD6 --prova non esegue; SD7 card col registro, senza ok non e' approvata
promemoria        PM1 scatta una volta e sparisce; PM2 --prompt solo in Accetta; PM3 scade; PM4 --at; PM5 fill non lo toglie, notifica una volta
relay             RL op, tocco, /state, fixture"""
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
repo = root / "atlas"
repo.mkdir(parents=True)
(root / "orbit").mkdir()
(root / "ledger").mkdir()
sd = tmp / "state"
cfg = tmp / "c.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(sd), "accounts": {"personal": {"config_dir": str(home / ".claude")}}, "default_account": "personal",
                           "workspace": {"root": str(root), "root_session_name": "master", "project_dirs": ["."]}, "relay": {"dir": str(tmp / "relay")}}))
fake = tmp / "fake-cm"
log = tmp / "talk.log"
fake.write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db"), "CM_SUGGESTIONS": str(tmp / "s.json"), "CM_RELAY_STATE": str(tmp / "none.json"),
            "CM_OBSERVE_DIR": str(tmp / "no"), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"})
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")


def run(*a, env=None, cwd=None):
    p = subprocess.run([CLI, *a], capture_output=True, text=True, env=dict(ENV, **(env or {})), stdin=subprocess.DEVNULL, timeout=120, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


def git(*a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def commit(f, msg="c"):
    (repo / f).parent.mkdir(exist_ok=True)
    (repo / f).write_text(str(time.time()))
    git("add", f)
    git("commit", "-qm", msg)


def load(n):
    sp = importlib.util.spec_from_file_location(n.replace("-", "_"), T.SCRIPTS / f"{n}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


def rows():
    return {(r["product"], r["key"]): r for r in json.loads(run("rimasto", "list", "--json")[1])}


git("init", "-q")
commit("src/a.py")
git("tag", "v1.0.0")
rc, out, err = run("rimasto", "add", "atlas", "demo", "--dove", "la demo del sito", "--percorsi", "src/", "--progetto", str(repo), "--sessione", "atlas")
T.check("rimasto_indietro RI1 a new item is aligned", rc == 0 and rows()[("atlas", "demo")]["stale"] is False and rows()[("atlas", "demo")]["aligned_version"] == "v1.0.0", out + err + str(rows()))
commit("docs/x.md")
T.check("rimasto_indietro RI2 another path changed: still aligned", rows()[("atlas", "demo")]["stale"] is False, str(rows()))
commit("src/b.py")
_r = rows()[("atlas", "demo")]
T.check("rimasto_indietro RI2 its path changed: behind, says how many commits", _r["stale"] is True and _r["why"] == "paths:1", str(_r))
T.check("rimasto_indietro the check closes only when marked aligned", run("rimasto", "verifica", "atlas", "demo")[0] == 1, "")
run("rimasto", "add", "atlas", "play", "--dove", "la scheda Play", "--comando", "test -f play-ok", "--progetto", str(repo))
T.check("rimasto_indietro RI3 a failing command makes it behind", rows()[("atlas", "play")]["stale"] is True and rows()[("atlas", "play")]["why"] == "check", str(rows()))
(repo / "play-ok").write_text("x")
T.check("rimasto_indietro RI3 the command passing aligns it", rows()[("atlas", "play")]["stale"] is False, "")
_c = json.loads(cfg.read_text())
git("tag", "v1.1.0")
rc, out, err = run("rimasto", "allinea", "atlas", "demo")
_t = out.strip()
_sh = json.loads(run("task", "show", _t)[1]) if rc == 0 else {}
T.check("rimasto_indietro RI5 Allinea opens a task for the right session, nothing marked aligned", rc == 0 and _sh.get("session") == "atlas" and rows()[("atlas", "demo")]["stale"] is True, out + err)
T.check("rimasto_indietro RI5 the task is not approved by itself", _sh.get("state") not in ("approved", "done"), str(_sh.get("state")))
rc, out, err = run("rimasto", "ok", "atlas", "demo")
_r = rows()[("atlas", "demo")]
T.check("rimasto_indietro RI4 Va bene così: aligned with this version", rc == 0 and _r["stale"] is False and _r["aligned_version"] == "v1.1.0", str(_r))
T.check("rimasto_indietro the task's check is green once aligned", run("rimasto", "verifica", "atlas", "demo")[0] == 0, "")
run("rimasto", "add", "orbit", "x", "--dove", "y", "--percorsi", "a", "--progetto", str(root / "orbit"))
T.check("rimasto_indietro RI6 no git: not behind, it says it does not know", rows()[("orbit", "x")]["stale"] is None and rows()[("orbit", "x")]["why"] == "no_git", str(rows()[("orbit", "x")]))
run("rimasto", "remove", "orbit", "x")

(repo / "BUG").write_text("x")
(root / "ledger" / "BUG").write_text("x")
DET = 'test -f BUG'
rc, out, err = run("altrove", "--help")
T.check("stesso_difetto the command has a help", rc == 0 and "--rivelatore" in out, out)
rc, out, err = run("altrove", "il bug", "--rivelatore", DET, "--prova", "--json")
T.check("stesso_difetto SD6 --prova lists the targets, runs nothing", rc == 0 and len(json.loads(out)["targets"]) == 3 and not list((sd / "altrove").glob("*")) if (sd / "altrove").exists() else rc == 0, out + err)
rc, out, err = run("altrove", "il bug", "--rivelatore", DET, "--json")
v = json.loads(out)
T.check("stesso_difetto SD1 positives found, complete", rc == 0 and [Path(x).name for x in v["positive"]] == ["atlas", "ledger"] and v["complete"] and v["total"] == 3, out + err)
rc, out, err = run("altrove", "sempre vero", "--rivelatore", "true")
T.check("stesso_difetto SD3 a detector positive on an empty folder proves nothing: refused", rc == 2 and "vuota" in err, out + err)
rc, out, err = run("altrove", "rotto", "--rivelatore", "exit 3")
T.check("stesso_difetto SD3 a broken detector is refused too", rc == 2, out + err)
_det2 = 'if [ -f BUG ]; then exit 0; elif [ -d .git ] || [ "$(basename "$PWD")" = orbit ]; then exit 7; else exit 1; fi'
rc, out, err = run("altrove", "misto", "--rivelatore", _det2, "--json")
v2 = json.loads(out)
T.check("stesso_difetto SD2 an error is not a negative", [Path(x).name for x in v2["errors"]] == ["orbit"] and [Path(x).name for x in v2["positive"]] == ["atlas", "ledger"], out + err)
S = load("cm-seguiti")
rid = v["id"]
rp = sd / "altrove" / f"{rid}.json"
_rec = json.loads(rp.read_text())
_before = json.dumps(_rec["complete"])
_rec["current"] = {"targets": _rec["current"]["targets"], "results": {_rec["current"]["targets"][0]: "negative"}, "started": 1}
rp.write_text(json.dumps(_rec))
_vw = S.run_view(json.loads(rp.read_text()))
T.check("stesso_difetto SD5 a partial run does not replace the complete one", _vw["complete"] and len(_vw["positive"]) == 2 and _vw["running"], str(_vw))
_seen = []
_od = S.detect
S.detect = lambda c, t, h=None: (_seen.append(t), _od(c, t, h))[1]
_r2 = json.loads(rp.read_text())
S._stop["now"] = False
S.run_altrove(_r2)
S.detect = _od
T.check("stesso_difetto SD4 resumes: the target already read is not read again", len(_seen) == 2 and _r2["current"]["targets"][0] not in _seen, str(_seen))
T.check("stesso_difetto SD5 a finished run becomes the complete one", _r2["complete"]["results"] == _r2["current"]["results"], "")
_r3 = json.loads(rp.read_text())
_r3["current"] = {"targets": _r3["current"]["targets"], "results": {}, "started": 1}
S._stop["now"] = True
T.check("stesso_difetto SD4 a stop saves and returns", S.run_altrove(_r3) is False and json.loads(rp.read_text())["current"]["results"] == {}, "")
S._stop["now"] = False
rc, out, err = run("altrove", "card", rid, "--correzione", "togli BUG")
_tc = out.strip().splitlines()[-1] if out.strip() else ""
_sc = json.loads(run("task", "show", _tc)[1]) if rc == 0 else {}
T.check("stesso_difetto SD7 one card through the registry, awaiting the ok", rc == 0 and _sc.get("state") == "awaiting_ok" and "ledger" in str(_sc.get("plan")), out + err + str(_sc.get("state")) + str(_sc.get("plan")))
T.check("stesso_difetto SD7 its check is red while the defect is there", run("altrove", "verifica", rid)[0] == 1, "")
(repo / "BUG").unlink()
(root / "ledger" / "BUG").unlink()
T.check("stesso_difetto SD7 its check is green when gone", run("altrove", "verifica", rid)[0] == 0, "")
rc, out, err = run("altrove", "x", "--rivelatore", DET, "--host", "win")
T.check("stesso_difetto --host wants the empty folder on that host", rc != 0 and "--controllo" in err, err)

flagf = tmp / "go"
rc, out, err = run("remind", "add", "Google ha approvato: icona ambra", "--until", f"test -f {flagf}", "--prompt", "cambia l'icona")
pid = out.strip()
sg = load("cm-suggest")
T.check("promemoria PM1 listed, not fired", rc == 0 and [x["id"] for x in json.loads(run("remind", "list", "--json")[1])["items"]] == [pid], out + err)
run("suggest", "fill")
T.check("promemoria PM1 condition false: nothing", not [x for x in sg.load()["items"] if x["kind"] == "reminder"], "")
flagf.write_text("x")
run("suggest", "fill")
_it = [x for x in sg.load()["items"] if x["kind"] == "reminder"]
T.check("promemoria PM1 fired: one suggestion, gone from the list", len(_it) == 1 and _it[0]["text"].startswith("Google") and not json.loads(run("remind", "list", "--json")[1])["items"], str(_it))
T.check("promemoria PM2 the prompt waits inside the suggestion: no session got it", _it and _it[0]["prompt"] == "cambia l'icona" and not log.exists(), str(_it))
sg.fill(state={"sessions": []}, commitments=[])
T.check("promemoria PM5 the hourly fill keeps it", [x["id"] for x in sg.load()["items"] if x["kind"] == "reminder"] == [_it[0]["id"]], "")
run("suggest", "fill")
T.check("promemoria PM1 once only", len([x for x in sg.load()["items"] if x["kind"] == "reminder"]) == 1, "")
rc, out, err = run("remind", "add", "scaduto", "--until", "false", "--expires", "1s")
_exp = out.strip()
time.sleep(1.2)
run("suggest", "fill")
_d = json.loads(run("remind", "list", "--json")[1])
T.check("promemoria PM3 expired: gone, no suggestion", not _d["items"] and any(x["id"] == _exp and x["outcome"] == "expired" for x in _d["fired"]) and not any(x["text"] == "scaduto" for x in sg.load()["items"]), str(_d))
run("remind", "add", "il 2020", "--at", "2020-01-01")
run("remind", "add", "il 2099", "--at", "2099-01-01")
run("suggest", "fill")
T.check("promemoria PM4 --at: a date, for what cannot be verified", any(x["text"] == "il 2020" for x in sg.load()["items"]) and [x["text"] for x in json.loads(run("remind", "list", "--json")[1])["items"]] == ["il 2099"], "")
T.check("promemoria bad arguments refuse", run("remind", "add", "x")[0] != 0 and run("remind", "add", "x", "--at", "domani")[0] != 0, "")

os.environ["CM_RELAY_CM"] = str(fake)
RL = load("cm-relay")
ok, txt = RL.execute({"id": "a", "op": "seguiti"})[:2]
d = json.loads(txt)
T.check("relay op seguiti reads the three lists, a passive read", ok and {"at", "rimasto", "altrove", "promemoria", "promemoria_scattati"} == set(d) and "seguiti" in RL.PASSIVE_OPS and "seguiti_azione" not in RL.PASSIVE_OPS and {"seguiti", "seguiti_azione"} <= set(RL.OPS), txt[:200])
commit("src/c.py")
S.rimasto_rows()
sm = S.summary()
T.check("relay the summary: numbers only, from the hourly count", sm["rimasto"] == 1 and sm["promemoria"] == 1 and set(sm) == {"rimasto", "altrove", "promemoria"}, str(sm))
st = RL.S.build_state({"seguiti": sm, "sessions": [], "projects": []}, 1)
T.check("relay /state carries the block, absent when all zero", st["seguiti"] == sm and "seguiti" not in RL.S.build_state({"sessions": []}, 1), str(st.get("seguiti")))
ok, t = RL.execute({"id": "b", "op": "seguiti_azione", "action": "ok", "product": "atlas", "key": "demo"})[:2]
T.check("relay no touch, no action", not ok and S.summary()["rimasto"] == 1, t)
ok, t = RL.execute({"id": "b", "op": "seguiti_azione", "action": "ok", "product": "atlas", "key": "demo", "device": "phone"})[:2]
T.check("relay Va bene così from a touch", ok and rows()[("atlas", "demo")]["stale"] is False, t)
commit("src/d.py")
ok, t = RL.execute({"id": "c", "op": "seguiti_azione", "action": "boh", "device": "phone"})[:2]
T.check("relay an unknown action refuses", not ok, t)
_ev, _ = RL.S.events_between({"sessions": [], "suggestions": []}, {"sessions": [], "quota": {}, "suggestions": [{"id": "s1", "kind": "reminder", "text": "icona"}, {"id": "s2", "kind": "idea", "text": "x"}]}, 5)
T.check("promemoria PM5 one notification when it fires, only for a reminder", [(e["kind"], e["title"], e["ref"]) for e in _ev] == [("reminder", "icona", "s1")], str(_ev))
_ev, _ = RL.S.events_between({"sessions": [], "suggestions": [{"id": "s1"}]}, {"sessions": [], "quota": {}, "suggestions": [{"id": "s1", "kind": "reminder", "text": "icona"}]}, 5)
T.check("promemoria PM5 not twice", not _ev, str(_ev))
_fp = json.loads((T.ROOT / "tests/fixtures/relay/cmd-result-sample.json").read_text())
_fr = {r["id"]: r for r in _fp["result"]}
_fx = next(json.loads(_fr[c["id"]]["text"]) for c in _fp["cmd"] if c["op"] == "seguiti")
T.check("relay the contract's sample has the keys the code sends", set(_fx) == set(d) and set(_fx["rimasto"][0]) == set(d["rimasto"][0]) and set(_fx["altrove"][0]) == set(d["altrove"][0]), str(sorted(d)))
_f3 = json.loads((T.ROOT / "tests/fixtures/relay/state-3-stale.json").read_text())
T.check("relay the sample state has the block and the ops", set(_f3["seguiti"]) == set(sm) and {"seguiti", "seguiti_azione"} <= set(_f3["ops"]), "")
T.finish()
