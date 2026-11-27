#!/usr/bin/env python3
"""Gli esiti dei suggerimenti dei 7 giorni in ogni push: state.suggestions_7d (27/11/2026, supervisor-suggest-accettati-27-11,
contratto 1.164). Alle 21:18 la home diceva «0% accettati» dopo cinque accettazioni dalle 21:13. La storia le aveva gia'
(4 accepted e 1 chosen: 5 su 23, il 22%), ma i numeri stavano solo nell'op kpi, che telefono e web rileggono al piu'
ogni 5 minuti e mai dopo un Accetta. Ora la push che segue un Accetta o una scelta li porta, su ogni dispositivo.

SP1 la push porta state.suggestions_7d {accepted, discussed, later, never, expired}, gli stessi numeri di
    kpi_suggestions_7d dell'op kpi: accepted conta le scelte, resolved e le voci di piu' di 7 giorni non contano
SP2 dopo un Accetta e dopo una scelta (op suggestion) la push seguente ha gia' il numero nuovo: con la formula dell'app
    0% → 33% → 50%, senza aspettare l'op kpi
SP3 build_state copia solo le cinque chiavi, interi da 0 in su; senza la fonte (un relay vecchio, una storia che non si
    legge) la chiave non c'e'; fit_state non la toglie
SP4 il contratto: details/relay.md ha la 1.164 con suggestions_7d; la fixture suggestions-7d-sample.json e' cio' che
    build_state pubblica coi numeri delle 21:18 (5 su 23: 22% con la formula dell'app). Le tre fixture di stato restano
    com'erano: stanno apposta a ridosso degli 8 KB (8180-8186 byte), e una chiave in piu' farebbe tagliare altro
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

KEYS = ["accepted", "discussed", "later", "never", "expired"]
tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude" / "sessions").mkdir(parents=True)
ws = home / "ws"
ws.mkdir()
calls = tmp / "cm-calls.log"
fake_cm = tmp / "fake-cm"
fake_cm.write_text(f'#!/bin/sh\necho "$*" >> "{calls}"\n[ "$1" = talk ] && {{ echo "sent to master" >&2; exit 0; }}\necho "[]"\n')
fake_cm.chmod(0o755)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(tmp / "relay")},
    "registry": {"file": str(tmp / "registry.json")}, "tabs": {"color_registry": str(tmp / "colors")},
}))
sg = tmp / "suggestions.json"
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE"):
    os.environ.pop(k, None)
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_SUGGESTIONS": str(sg),
                   "CM_TASKS_DB": str(tmp / "tasks.db"), "CM_RELAY_CM": str(fake_cm), "CM_RELAY_NO_PUSH": "1", "CM_PROC_SCAN_PIDS": ""})

now = int(time.time())
H = 3600
sg.write_text(json.dumps({
    "items": [
        {"id": "s-acc0000001", "kind": "followup", "key": "followup:demo:1", "text": "demo: riprendi il CSV", "prompt": "Riprendi il CSV di demo",
         "at": now - 600, "expires_at": now + 20 * H, "rank": 4, "source": "script"},
        {"id": "s-cho0000001", "kind": "choice", "key": "choice:demo-e", "text": "Quale effetto per la chat?", "prompt": "Tengo la {choice}",
         "at": now - 500, "expires_at": now + 10 * H, "rank": 2, "source": "master", "options": ["B", "E"]},
    ],
    "never": ["old-never"], "never_at": {"old-never": now - 3 * 86400},
    "history": [
        {"key": "a", "kind": "proposal", "source": "master", "action": "expired", "at": now - 2 * 86400},
        {"key": "old-never", "kind": "idea", "source": "master", "action": "never", "at": now - 3 * 86400},
        {"key": "r1", "kind": "unsaved", "source": "script", "action": "resolved", "at": now - 5 * H},
        {"key": "r2", "kind": "waiting", "source": "script", "action": "resolved", "at": now - 4 * H},
        {"key": "r3", "kind": "monitor", "source": "script", "action": "resolved", "at": now - 3 * H},
        {"key": "z", "kind": "proposal", "source": "master", "action": "accepted", "at": now - 9 * 86400},
    ],
}))


def load(name, tag):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + tag, T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def rate(s):
    """La formula dell'app (Kpi.acceptRate, kpi.ts acceptRate): la parte accettata in percento, None senza voci."""
    total = sum(int(s.get(k) or 0) for k in KEYS)
    return round(s["accepted"] * 100 / total) if total else None


def pushed():
    """Lo stato intero che la push pubblicherebbe adesso: le fonti del relay e build_state, come _push."""
    try:
        src = RL.collect_sources(time.time())
        return RL.S.build_state(src, time.time(), fit=False)
    except Exception as e:   # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


RL = load("cm-relay", "_sp")
RL.MEMO_ON = False
KPI = load("cm-kpi", "_sp")
SG = load("cm-suggest", "_sp")
hist = SG.load()["history"]
want0 = {"accepted": 0, "discussed": 0, "later": 0, "never": 1, "expired": 1}
st0 = pushed()
T.check("SP1 the push carries state.suggestions_7d {accepted, discussed, later, never, expired}: the numbers of the op kpi (kpi_suggestions_7d); resolved and items older than 7 days do not count",
        KPI.kpi_suggestions_7d(now, hist) == want0 and st0.get("suggestions_7d") == want0 and list(st0["suggestions_7d"]) == KEYS,
        f"kpi={KPI.kpi_suggestions_7d(now, hist)} state={st0.get('suggestions_7d', st0.get('error'))}")

ok_a = RL.suggestion_act({"action": "accept", "device": "phone", "by": "phone test"}, "s-acc0000001")
st1 = pushed()
ok_c = RL.suggestion_act({"action": "choose", "choice": "E", "device": "phone", "by": "phone test"}, "s-cho0000001")
st2 = pushed()
s1, s2 = st1.get("suggestions_7d") or {}, st2.get("suggestions_7d") or {}
talks = [x for x in calls.read_text().splitlines() if x.startswith("talk master")] if calls.exists() else []
T.check("SP2 after an Accept and after a choice (op suggestion) the next push already has the new number: 0% → 33% → 50% with the app's formula, with no op kpi",
        ok_a[0] is True and ok_c[0] is True and len(talks) == 2 and s1.get("accepted") == 1 and s2.get("accepted") == 2
        and [rate(st0.get("suggestions_7d") or want0), rate(s1) if s1 else None, rate(s2) if s2 else None] == [0, 33, 50]
        and s2 == KPI.kpi_suggestions_7d(int(time.time()), SG.load()["history"]),
        f"accept={ok_a} choose={ok_c} talks={len(talks)} s1={s1 or st1.get('error')} s2={s2 or st2.get('error')}")

b1 = RL.S.build_state({"rows": [], "suggestions_7d": {"accepted": "3", "discussed": None, "later": -2, "never": 1.0, "expired": "x", "extra": 9}}, now, fit=False)
b2 = RL.S.build_state({"rows": []}, now, fit=False)
b3 = RL.S.build_state({"rows": [], "suggestions_7d": None}, now, fit=False)
f1 = RL.S.fit_state(json.loads(json.dumps(b1)), 8)
T.check("SP3 build_state copies only the five keys, integers from 0 up; with no source (an older relay, a history that cannot be read) the key is absent; fit_state keeps it",
        b1.get("suggestions_7d") == {"accepted": 3, "discussed": 0, "later": 0, "never": 1, "expired": 0} and "suggestions_7d" not in b2
        and "suggestions_7d" not in b3 and f1.get("suggestions_7d") == b1.get("suggestions_7d"),
        f"b1={b1.get('suggestions_7d')} b2={'suggestions_7d' in b2} b3={'suggestions_7d' in b3} f1={f1.get('suggestions_7d')}")

doc = (T.ROOT / "details" / "relay.md").read_text(encoding="utf-8")
c164 = doc.split("Contract 1.164", 1)[1].split("\nContract 1.", 1)[0] if "Contract 1.164" in doc else ""
fx = json.loads((T.ROOT / "tests" / "fixtures" / "relay" / "suggestions-7d-sample.json").read_text(encoding="utf-8"))
b4 = RL.S.build_state({"rows": [], "suggestions_7d": {"accepted": 5, "discussed": 0, "later": 0, "never": 0, "expired": 18}}, now, fit=False)
T.check("SP4 the contract: details/relay.md has 1.164 with state.suggestions_7d; the fixture suggestions-7d-sample.json is what build_state publishes with the numbers of 21:18 (5 of 23, 22% with the app's formula)",
        "`state.suggestions_7d`" in c164 and "supervisor-suggest-accettati-27-11" in c164 and "suggestions-7d-sample.json" in c164
        and list(fx) == ["suggestions_7d"] and fx["suggestions_7d"] == b4.get("suggestions_7d") and list(fx["suggestions_7d"]) == KEYS
        and rate(fx["suggestions_7d"]) == 22,
        f"doc={'yes' if c164 else 'no'} fixture={fx} built={b4.get('suggestions_7d')}")

T.rm(tmp)
T.finish()
