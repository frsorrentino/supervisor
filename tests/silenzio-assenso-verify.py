#!/usr/bin/env python3
"""Silenzio assenso: una carta o una prova che aspetta una risposta si chiude da sola a una scadenza, solo col controllo
verde, e non diventa mai un ok della persona (23/11/2026, supervisor-silenzio-assenso-23-11).

Il maintainer il 23/11: vale per le prove e gli ok chiesti a un cliente o a lui, «anche per lavorazioni personali o
lavorative che non richiedono verifica del cliente». Mai per una scelta fra opzioni, ne' per release, push, deploy, merge,
pagamenti e invii a clienti. Per 14 giorni un difetto sullo stesso progetto si propone collegato (task add --defect-of).

SI1 wait-ok --silenzio (senza valore: 3 giorni) scrive la scadenza; task list --json la porta in request.silence_until;
    --silenzio 48h e una data valgono; 10m e' rifiutato
SI2 rifiutato per una scelta fra opzioni, per un esito release, per un titolo che pubblica o paga, per «invia al cliente»
SI3 prima della scadenza silenzio-check non fa niente; scaduta col controllo rosso: il compito resta aperto, un passo lo
    dice, la seconda volta niente di nuovo
SI4 scaduta col controllo verde: done; il giro della carta e' «silence» col motivo; nessuna riga di ok con un nome (mai un
    ok della persona); task silenzi lo elenca
SI5 task prova --silenzio: la scadenza sulla prova (anche in prove_list); scaduta e verde → done
SI6 il recap del mattino ha la sezione SILENZIO ASSENSO con i chiusi e i rossi
SI7 per 14 giorni task add di un bug sullo stesso progetto propone task defect-of; non un compito che non e' un bug, non
    un altro progetto, non dopo 14 giorni
SI8 relay: le carte nello stato portano silence_until; la guardia lancia silenzio-check
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
proj, proj2 = tmp / "proj", tmp / "altro"
proj.mkdir()
proj2.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "workspace": {"root": str(tmp), "root_session_name": "master"}}))
DB = tmp / "tasks.db"
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update({"CM_TASKS_DB": str(DB), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(tmp), "CM_HOME": str(tmp), "CM_RELAY_CM": "/bin/false"})


def cm(*args, stdin=None):
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), *args], input=stdin, capture_output=True, text=True, env=ENV, timeout=120)
    return r.returncode, r.stdout, r.stderr


def contract(tid, project=proj, ttype="feature", title=None, kind="custom", expected="it works", check="test -f ok.txt"):
    return {"schema_version": 1, "task": {
        "id": tid, "title": title or f"unit {tid}", "type": ttype, "plan": None,
        "where": {"project": str(project), "session": None, "host": "local", "account": None},
        "outcome": {"expected": expected, "kind": kind, "proof": "the check"},
        "check": {"cmd": check, "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open",
        "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}}


def add(c):
    return cm("add", "-", stdin=json.dumps(c))


def show(tid):
    return json.loads(cm("show", tid, "--json")[1])


def db(q, *p):
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    try:
        out = [dict(r) for r in con.execute(q, p).fetchall()]
        con.commit()
        return out
    finally:
        con.close()


def wait_ok(tid, *extra):
    return cm("wait-ok", tid, "--what", "il report", "--where", "la cartella", "--consiglio", "approvalo: e' quello chiesto", *extra)


def expire(table, tid):
    db(f"UPDATE {table} SET silence_until=? WHERE task=? AND silence_until IS NOT NULL", int(time.time()) - 5, tid)


# SI1
add(contract("s1"))
t0 = time.time()
rc, out, err = wait_ok("s1", "--silenzio")
req = next((x for x in json.loads(cm("list", "--json")[1]) if x["id"] == "s1"), {}).get("request") or {}
T.check("SI1 wait-ok --silenzio: 3 days by default, said on the terminal, in request.silence_until",
        rc == 0 and "silenzio assenso" in out and abs((req.get("silence_until") or 0) - (t0 + 3 * 86400)) < 60, out + err + json.dumps(req))
add(contract("s1b"))
add(contract("s1c"))
add(contract("s1d"))
r48 = wait_ok("s1b", "--silenzio", "48h")
rdate = wait_ok("s1c", "--silenzio", time.strftime("%Y-%m-%d", time.localtime(time.time() + 5 * 86400)))
r10 = wait_ok("s1d", "--silenzio", "10m")
until48 = db("SELECT silence_until FROM approvals WHERE task='s1b'")[0]["silence_until"]
T.check("SI1 --silenzio 48h and a date are accepted; 10m is refused (between 1 hour and 60 days)",
        r48[0] == 0 and abs(until48 - (time.time() + 48 * 3600)) < 60 and rdate[0] == 0 and r10[0] != 0 and "10m" in r10[2], str((r48, rdate, r10)))

# SI2
add(contract("n1"))
add(contract("n2", kind="release", expected="la 1.2 pubblicata", check="git ls-remote --tags origin | grep -q v1.2"))
add(contract("n3", title="Pubblica la pagina dei prezzi"))
add(contract("n4"))
add(contract("n5", title="Bonifico al fornitore"))
rn1 = cm("wait-ok", "n1", "--what", "w", "--where", "d", "--options", "A|B", "--consigliata", "1", "--consiglio", "A", "--silenzio")
rn2, rn3, rn5 = wait_ok("n2", "--silenzio"), wait_ok("n3", "--silenzio"), wait_ok("n5", "--silenzio")
rn4 = cm("wait-ok", "n4", "--what", "invio il preventivo al cliente", "--where", "email", "--consiglio", "ok", "--silenzio")
T.check("SI2 refused for a choice, a release, a title that publishes, a payment, a send to a client; nothing written",
        all(r[0] != 0 for r in (rn1, rn2, rn3, rn4, rn5)) and "scelta" in rn1[2] and "release" in rn2[2]
        and not db("SELECT 1 FROM approvals WHERE task IN ('n1','n2','n3','n4','n5')"), " | ".join(r[2].strip() for r in (rn1, rn2, rn3, rn4, rn5)))

# SI3
rc, out, _ = cm("silenzio-check", "--json")
T.check("SI3 before the deadline silenzio-check does nothing", rc == 0 and json.loads(out) == [] and show("s1")["state"] == "awaiting_ok", out)
expire("approvals", "s1")
rc, out, _ = cm("silenzio-check", "--json")
res = json.loads(out or "[]")
again = json.loads(cm("silenzio-check", "--json")[1] or "[]")
s = show("s1")
T.check("SI3 expired with the check red: still open, a step says it, the second run adds nothing",
        rc == 0 and [x["outcome"] for x in res] == ["red"] and s["state"] == "awaiting_ok" and again == []
        and any("controllo rosso" in (x.get("text") or "") for x in s.get("steps") or []), json.dumps(res) + json.dumps(s.get("steps"))[:600])

# SI4
add(contract("g1"))
wait_ok("g1", "--silenzio")
expire("approvals", "g1")
(proj / "ok.txt").write_text("x")
res = json.loads(cm("silenzio-check", "--json")[1] or "[]")
s = show("g1")
named = db("SELECT by_ FROM approvals WHERE task='g1' AND by_ IS NOT NULL")
rnd = db("SELECT closed_kind, closed_reason FROM approvals WHERE task='g1'")
listed = json.loads(cm("silenzi", "--json")[1] or "[]")
T.check("SI4 expired with the check green: done, the round closed as silence with its reason, never an ok with a name; task silenzi lists it",
        [x["outcome"] for x in res if x["task"] == "g1"] == ["done"] and s["state"] == "done" and not named
        and rnd and rnd[0]["closed_kind"] == "silence" and "silenzio assenso" in rnd[0]["closed_reason"]
        and any(x["task"] == "g1" and x["outcome"] == "done" for x in listed) and any(x["task"] == "s1" and x["outcome"] == "red" for x in listed),
        json.dumps({"res": res, "state": s["state"], "named": named, "rnd": rnd, "listed": listed})[:900])
(proj / "ok.txt").unlink()

# SI5
add(contract("p1"))
rc, out, err = cm("prova", "p1", "--build", "app-0.1.1", "--dove", "home", "--fai", "apri la home", "--atteso", "la lista c'e'", "--silenzio", "2d")
n = int(out.split()[0]) if rc == 0 and out.split() else None
row = db("SELECT silence_until FROM prove WHERE n=?", n)[0] if n else {}
spec = importlib.util.spec_from_file_location("cm_tasks_si", T.SCRIPTS / "cm-tasks.py")
os.environ.update(ENV)
TK = importlib.util.module_from_spec(spec)
spec.loader.exec_module(TK)
con = TK.connect()
pl = TK.prove_list(con)
con.close()
items = [x for g in pl["groups"] for x in g["prove"] if x["id"] == n]
T.check("SI5 task prova --silenzio 2d: the deadline on the test, also in prove_list",
        rc == 0 and abs((row.get("silence_until") or 0) - (time.time() + 2 * 86400)) < 60 and items and items[0].get("silence_until") == row["silence_until"],
        out + err + json.dumps(items))
expire("prove", "p1")
(proj / "ok.txt").write_text("x")
res = json.loads(cm("silenzio-check", "--json")[1] or "[]")
T.check("SI5 the test's silence expired, check green → done, never a confirmation with a name",
        [x["outcome"] for x in res if x["task"] == "p1"] == ["done"] and show("p1")["state"] == "done"
        and not db("SELECT 1 FROM approvals WHERE task='p1' AND by_ IS NOT NULL"), json.dumps(res))

# SI6
AV = importlib.util.module_from_spec(importlib.util.spec_from_file_location("cm_avvisi_si", T.SCRIPTS / "cm-avvisi.py"))
AV.__spec__.loader.exec_module(AV)
AV.monitor_cards = lambda: []
AV.server_items = lambda: (None, None, 0)
morning = AV.morning_text()
T.check("SI6 the morning recap has SILENZIO ASSENSO with the closed ones and the red one", "SILENZIO ASSENSO" in morning
        and "unit g1: chiuso" in morning and "unit p1: chiuso" in morning and "unit s1: scaduto" in morning, morning[-800:])

# SI7
rc, out, _ = add(contract("b1", ttype="bug"))
rc2, out2, _ = add(contract("f1", ttype="feature"))
rc3, out3, _ = add(contract("b2", project=proj2, ttype="bug"))
db("UPDATE silences SET at=at-? WHERE project=?", 15 * 86400, str(proj))
rc4, out4, _ = add(contract("b3", ttype="bug"))
T.check("SI7 within 14 days a bug on the same project proposes task defect-of with the silenced task; not a feature, not another project, not after 14 days",
        rc == 0 and "task defect-of b1 p1" in out and "defect-of" not in out2 and "defect-of" not in out3 and rc4 == 0 and "defect-of" not in out4,
        " | ".join((out, out2, out3, out4)))

# SI8
add(contract("r1"))
wait_ok("r1", "--silenzio")
RL = importlib.util.module_from_spec(importlib.util.spec_from_file_location("cm_relay_si", T.SCRIPTS / "cm-relay.py"))
RL.__spec__.loader.exec_module(RL)
RL._memo = lambda key, fn, **kw: fn()
RL._json_cmd = lambda *a, **k: json.loads(cm(*a[1:])[1]) if a and a[0] == "task" else None
apr = next((x for x in RL.approvals_list() if x["task"] == "r1"), {})
guard = (T.SCRIPTS / "cm-guard.py").read_text(encoding="utf-8")
T.check("SI8 relay: the card in the state carries silence_until; the guard runs silenzio-check",
        isinstance(apr.get("silence_until"), int) and abs(apr["silence_until"] - (time.time() + 3 * 86400)) < 60 and '"silenzio-check"' in guard, json.dumps(apr))

T.rm(str(tmp))
T.finish()
