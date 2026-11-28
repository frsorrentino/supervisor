#!/usr/bin/env python3
"""«Riprendi» su una voce rimasta indietro (28/11/2026, supervisor-seguiti-riprendi-28-11, contratto 1.166).

La persona alle 16:38: «la sezione riprendi mi mostra cos'è rimasto indietro ma non mostra un tasto per accettare la
ripresa». «Allinea» sparisce quando la voce ha gia' un compito aperto, e nessuna op lo rimandava alla sua sessione. Ora
seguiti_azione accetta «riprendi»: col compito aperto lo rimanda alla sessione della voce (rilanciata prima se e' chiusa,
`talk --reopen`), senza compito aperto fa come «Allinea»; risponde col testo dell'esito o col motivo del no. HOME
temporanea, un supervisor finto che dice quali sessioni sono vive e se il rilancio lascia il messaggio in casella.

SR1 senza tocco niente
SR2 nessun compito: come «Allinea», un compito nuovo per la sessione della voce, che lo sa; la risposta lo dice, con task e session
SR3 il compito aperto e la sessione viva: le arriva «Riprendi il compito …» (talk --reopen); «atlas riprende il compito …»
SR4 la sessione chiusa: talk --reopen la rilancia; «atlas era chiusa: riaperta, riprende il compito …»
SR5 il rilancio non riesce (il messaggio resta in casella): ok false col motivo
SR6 il compito della voce chiuso: come «Allinea», un compito nuovo
SR7 una voce che non c'e', un'azione sconosciuta (che nomina riprendi): ok false col motivo
SR8 il contratto: details/relay.md ha la 1.166; cmd-result-sample.json ha «riprendi» con task e session
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
repo = root / "atlas"
repo.mkdir(parents=True)
sd = tmp / "state"
cfg = tmp / "c.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(sd), "accounts": {"personal": {"config_dir": str(home / ".claude")}}, "default_account": "personal",
                           "workspace": {"root": str(root), "root_session_name": "master", "project_dirs": ["."]}, "relay": {"dir": str(tmp / "relay")}}))
log, live, inbox = tmp / "cm.log", tmp / "LIVE", tmp / "INBOX"
fake = tmp / "fake-cm"
fake.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{log}"
case "$1" in
  sessions) if [ -f "{live}" ]; then printf '[{{"name": "atlas", "tmux": "atlas", "cwd": "{repo}"}}]\\n'; else printf '[]\\n'; fi ;;
  talk) if [ -f "{inbox}" ]; then printf 'atlas non c e: messaggio in casella (supervisor talk --status 42)\\n'; fi ;;
esac
exit 0
""")
fake.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db"), "CM_SUGGESTIONS": str(tmp / "s.json"),
            "CM_RELAY_STATE": str(tmp / "none.json"), "CM_OBSERVE_DIR": str(tmp / "no"), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"})
os.environ.update(ENV)
CLI = str(T.SCRIPTS / "supervisor")
IT = json.loads((T.SCRIPTS.parent / "messages" / "it.json").read_text(encoding="utf-8"))


def it(k, **kw):
    return IT[k].format(**kw) if k in IT else f"<{k} missing>"


def run(*a):
    p = subprocess.run([CLI, *a], capture_output=True, text=True, env=ENV, stdin=subprocess.DEVNULL, timeout=120)
    return p.returncode, p.stdout, p.stderr


def git(*a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def commit(f):
    (repo / f).parent.mkdir(exist_ok=True)
    (repo / f).write_text(str(time.time()))
    git("add", f)
    git("commit", "-qm", "c")


def load(n):
    sp = importlib.util.spec_from_file_location(n.replace("-", "_") + "_sr", T.SCRIPTS / f"{n}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


def item(key):
    return (json.loads(S.rimasto_path().read_text()).get("atlas") or {}).get(key) or {}


def go(cmd):
    res = RL.execute(dict({"id": "x", "op": "seguiti_azione", "device": "phone"}, **cmd))
    return res[0], res[1], (res[2] if len(res) > 2 and isinstance(res[2], dict) else {})


def new_lines(n0):
    return (log.read_text().splitlines() if log.exists() else [])[n0:]


def mark():
    return len(log.read_text().splitlines()) if log.exists() else 0


git("init", "-q")
commit("src/a.py")
git("tag", "v1.0.0")
run("rimasto", "add", "atlas", "demo", "--dove", "la demo del sito", "--percorsi", "src/", "--progetto", str(repo), "--sessione", "atlas")
run("rimasto", "add", "atlas", "play", "--dove", "la scheda Play", "--comando", "test -f play-ok", "--progetto", str(repo), "--sessione", "atlas")
commit("src/b.py")
os.environ["CM_RELAY_CM"] = str(fake)
S = load("cm-seguiti")
RL = load("cm-relay")

ok, t = RL.execute({"id": "a", "op": "seguiti_azione", "action": "riprendi", "product": "atlas", "key": "demo"})[:2]
T.check("SR1 no touch, nothing", not ok and not item("demo").get("task"), t)

n0 = mark()
ok, t, ex = go({"action": "riprendi", "product": "atlas", "key": "demo"})
tid = item("demo").get("task")
sh = json.loads(run("task", "show", tid)[1]) if tid else {}
T.check("SR2 no task: as «Allinea», a new task for the item's session, which is told; the answer says so, with task and session",
        ok and tid and sh.get("session") == "atlas" and sh.get("state") == "assigned" and t == it("seguiti.riprendi_new", name="atlas", task=tid, key="demo")
        and ex == {"task": tid, "session": "atlas"} and any(ln.startswith(f"talk atlas Compito {tid}: allinea demo") for ln in new_lines(n0)),
        f"ok={ok} t={t} ex={ex} sh={sh.get('state')}/{sh.get('session')} log={new_lines(n0)}")

live.write_text("x")
n0 = mark()
ok, t, ex = go({"action": "riprendi", "product": "atlas", "key": "demo"})
talk3 = [ln for ln in new_lines(n0) if ln.startswith("talk ")]
T.check("SR3 the task open and the session live: «Riprendi il compito …» reaches it (talk --reopen); «atlas riprende il compito …»",
        ok and t == it("seguiti.riprendi_sent", name="atlas", task=tid) and ex == {"task": tid, "session": "atlas"} and len(talk3) == 1
        and talk3[0].startswith(f"talk atlas Riprendi il compito {tid}: la voce atlas/demo (la demo del sito) è rimasta indietro.") and talk3[0].endswith("--no-wait --reopen"),
        f"ok={ok} t={t} ex={ex} talk={talk3}")

live.unlink()
n0 = mark()
ok, t, ex = go({"action": "riprendi", "product": "atlas", "key": "demo"})
talk4 = [ln for ln in new_lines(n0) if ln.startswith("talk ")]
T.check("SR4 the session closed: talk --reopen relaunches it; «atlas era chiusa: riaperta, riprende il compito …»",
        ok and t == it("seguiti.riprendi_reopened", name="atlas", task=tid) and len(talk4) == 1 and talk4[0].endswith("--no-wait --reopen"), f"ok={ok} t={t} talk={talk4}")

inbox.write_text("x")
ok, t, ex = go({"action": "riprendi", "product": "atlas", "key": "demo"})
inbox.unlink()
T.check("SR5 the relaunch fails (the message stays in the inbox): ok false with the reason",
        not ok and t == it("seguiti.riprendi_inbox", name="atlas", task=tid), f"ok={ok} t={t}")

c = {"schema_version": 1, "task": {"id": "vecchio-play", "title": "allinea play, ieri", "type": "fix", "plan": None,
                                   "where": {"project": str(repo), "session": "atlas", "host": "local", "account": None},
                                   "outcome": {"expected": "la scheda Play allineata", "kind": "custom", "proof": "il controllo rieseguito"},
                                   "check": {"cmd": "test -f play-ok", "cwd": None, "timeout_s": 30}, "perimeter": ["**"], "lane": "closed", "depends_on": [],
                                   "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}}
subprocess.run([CLI, "task", "add", "-"], input=json.dumps(c), capture_output=True, text=True, env=ENV, timeout=120)
run("task", "cancel", "vecchio-play")
d = json.loads(S.rimasto_path().read_text())
d["atlas"]["play"]["task"] = "vecchio-play"
S.rimasto_path().write_text(json.dumps(d))
ok, t, ex = go({"action": "riprendi", "product": "atlas", "key": "play"})
tid6 = item("play").get("task")
T.check("SR6 the item's task is closed: as «Allinea», a new task",
        ok and tid6 and tid6 != "vecchio-play" and json.loads(run("task", "show", tid6)[1]).get("state") == "assigned"
        and t == it("seguiti.riprendi_new", name="atlas", task=tid6, key="play") and ex.get("task") == tid6, f"ok={ok} t={t} ex={ex} task={tid6}")

ok7, t7, _ = go({"action": "riprendi", "product": "atlas", "key": "nessuna"})
ok7b, t7b, _ = go({"action": "boh", "product": "atlas", "key": "demo"})
T.check("SR7 an item that is not there, an unknown action (which names riprendi): ok false with the reason",
        not ok7 and t7 == it("seguiti.no_item", item="atlas/nessuna") and not ok7b and "riprendi" in t7b, f"{t7} | {t7b}")

doc = (T.ROOT / "details" / "relay.md").read_text(encoding="utf-8")
c166 = doc.split("Contract 1.166", 1)[1].split("\nContract 1.", 1)[0] if "Contract 1.166" in doc else ""
fp = json.loads((T.ROOT / "tests" / "fixtures" / "relay" / "cmd-result-sample.json").read_text(encoding="utf-8"))
fr = {r["id"]: r for r in fp["result"]}
rip = [fr[c["id"]] for c in fp["cmd"] if c["op"] == "seguiti_azione" and c.get("action") == "riprendi" and c["id"] in fr]
T.check("SR8 the contract: details/relay.md has 1.166 with riprendi; cmd-result-sample.json has «riprendi» with task and session",
        "`riprendi`" in c166 and "--reopen" in c166 and any(r["ok"] and {"task", "session"} <= set(r) for r in rip) and any(not r["ok"] for r in rip),
        json.dumps(rip, ensure_ascii=False)[:400])

T.rm(str(tmp))
T.finish()
