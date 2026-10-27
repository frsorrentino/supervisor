#!/usr/bin/env python3
"""Verifica supervisor quota --master (cm-master-usage.py) su trascrizioni finte della master.

MU1 ogni turno alla sua origine: telefono, web app, segnalazione, suggerimento accettato, terminale, un'altra
    sessione, un'automazione in elenco (anche dentro la busta di un'altra sessione o come avviso di sistema), un turno
    pianificato non in elenco, i lavori in sottofondo; allegati e righe di comando non aprono un turno
MU2 il peso di una risposta in token eq come fable-director (cache read alla tariffa del modello, scrittura di un'ora 2x)
MU3 lo stesso messaggio scritto a pezzi conta una volta
MU4 compattazione: la stima della chiamata (contesto di prima dalla cache, riassunto) e la prima risposta dopo
MU5 un subagente pesa sul turno che l'ha avviato
MU6 incrementale: una seconda lettura conta solo le righe nuove; l'elenco cambiato rifà il conto
MU7 avvisi: automazioni oltre un quarto, un'automazione raddoppiata nelle ultime 24 ore, una non in elenco
MU8 --json: hours[] e origins[] con name, kind, runs, share
MU9 --notify (e il vecchio --suggest): gli avvisi come notifica «week_pace» del relay, non come suggerimenti; la
    stessa automazione al massimo una volta ogni 24 ore
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
ws.mkdir(parents=True)
state = tmp / "state"
slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(ws))
proj = home / ".claude" / "projects" / slug
proj.mkdir(parents=True)
(home / ".claude-work").mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "workspace": {"root": str(ws)}, "relay": {"enabled": True, "dir": str(tmp / "relay")},
                           "accounts": {"personal": {"config_dir": str(home / ".claude")}, "work": {"config_dir": str(home / ".claude-work")}}}))
local = tmp / "automations.json"
sugg = tmp / "suggestions.json"
ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
       "CM_AUTOMATIONS": str(local), "CM_SUGGESTIONS": str(sugg)}

NOW = time.time()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(t, text, origin=None, **kw):
    d = {"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": text}}
    if origin:
        d["turnOrigin"] = origin
    d.update(kw)
    return json.dumps(d, ensure_ascii=False)


def peer(t, sender, text, origin="peer"):
    return user(t, f'Another Claude session sent a message:\n<cross-session-message from="uds:/x.sock" from-name="{sender}" from-mode="bypass">\n{text}\n</cross-session-message>\n\nThis came from another Claude session', origin)


def reply(t, mid, out=10, inp=100, cr=0, cc=0, cc1=0, model="claude-opus-5-5"):
    u = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc,
         "cache_creation": {"ephemeral_1h_input_tokens": cc1, "ephemeral_5m_input_tokens": cc - cc1}}
    return json.dumps({"type": "assistant", "timestamp": iso(t), "message": {"id": mid, "model": model, "role": "assistant", "usage": u,
                                                                              "content": [{"type": "text", "text": "ok"}]}})


def run(*args):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-quota.py"), "--master", *args], capture_output=True, text=True, env=ENV, timeout=60)


def origins(d):
    return {(o["kind"], o["name"]): o for o in d["origins"]}


t0 = NOW - 3 * 3600
SUMMARY = "This session is being continued from a previous conversation. " + "x" * 338   # 400 caratteri
lines = [
    user(t0, "Dall'utente via telefono. Rispondi nella tua finestra. fai il deploy", "peer"), reply(t0 + 1, "m1"),
    peer(t0 + 10, "supervisor", "Dall'utente via web app. Rispondi nella tua finestra. stato?"), reply(t0 + 11, "m2"),
    peer(t0 + 20, "supervisor", "Segnalazione del 25/10/2026: il tasto non va"), reply(t0 + 21, "m3"),
    peer(t0 + 30, "supervisor", "[dai suggerimenti] chiudi i compiti in pausa"), reply(t0 + 31, "m4"),
    user(t0 + 40, "come va?", "human", promptSource="typed"), reply(t0 + 41, "m5"),
    user(t0 + 42, "[Image: original 10x10]", isMeta=True), user(t0 + 43, "<local-command-caveat>x</local-command-caveat>", isMeta=True),
    reply(t0 + 44, "m5b"),   # stessa richiesta del terminale: allegato e comando non aprono un turno
    peer(t0 + 50, "atlas-shop", "Commit 1234 su main: fatto"), reply(t0 + 51, "m6"),
    peer(t0 + 60, "supervisor", "Giro dei suggerimenti (memoria x): leggi supervisor task impegni"), reply(t0 + 61, "m7"),
    user(t0 + 70, '[Cross-session idle notice] "atlas-shop", which you messaged, is idle', "system", isMeta=True), reply(t0 + 71, "m8"),
    user(t0 + 80, "Ronda delle tre: controlla le code", "scheduled", isMeta=True), reply(t0 + 81, "m9"),
    user(t0 + 90, "<task-notification> <task-id>b1</task-id> done", "task_notification"), reply(t0 + 91, "m10"),
    # MU2/MU3: una risposta pesata, scritta in due righe
    user(t0 + 100, "conto", "human"),
    reply(t0 + 101, "m11", inp=100, out=10, cr=1000, cc=400, cc1=100, model="claude-fable-5-1"),
    reply(t0 + 101, "m11", inp=100, out=10, cr=1000, cc=400, cc1=100, model="claude-fable-5-1"),
    # MU4: compattazione a meta' turno
    user(t0 + 200, "lavoro lungo", "human"), reply(t0 + 201, "m12", model="claude-fable-5-1"),
    json.dumps({"type": "system", "subtype": "compact_boundary", "timestamp": iso(t0 + 210), "compactMetadata": {"trigger": "auto", "preTokens": 100000}}),
    user(t0 + 211, SUMMARY, isCompactSummary=True),
    reply(t0 + 212, "m13", inp=0, out=0, cc=8000, model="claude-fable-5-1"),
    reply(t0 + 213, "m14", model="claude-fable-5-1"),
]
(proj / "sid-a.jsonl").write_text("\n".join(lines) + "\n")
# MU5: un subagente avviato dalla richiesta del telefono
(proj / "sid-a" / "subagents").mkdir(parents=True)
(proj / "sid-a" / "subagents" / "agent-1.jsonl").write_text(user(t0 + 2, "cerca i file") + "\n" + reply(t0 + 3, "s1", inp=1000, out=0) + "\n")

r = run("--json")
d = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
o = origins(d) if d else {}
want = {("richiesta", "telefono"), ("richiesta", "web app"), ("richiesta", "segnalazione"), ("richiesta", "suggerimento accettato"),
        ("richiesta", "terminale"), ("sessione", "atlas-shop"), ("automazione", "giro dei suggerimenti"), ("automazione", "notifiche delle sessioni"),
        ("automazione", "non in elenco: Ronda delle tre: controlla le code"), ("altro", "lavori in sottofondo"), ("automazione", "compattazioni")}
T.check("MU1 each turn to its origin: phone, web app, report, accepted suggestion, terminal, another session, a listed automation (inside the envelope and as a system notice), an unlisted scheduled turn, background work",
        want <= set(o) and o[("richiesta", "telefono")]["runs"] == 1, r.stdout[:1500] + r.stderr[-800:])
T.check("MU1 attachments and command lines do not open a turn: the reply after them stays on the terminal (3 turns, 5 replies)",
        o.get(("richiesta", "terminale"), {}).get("runs") == 3, str(o.get(("richiesta", "terminale"))))
T.check("MU2+MU3 a reply weighs input 1, output 5, cache read 0.025 on Fable, cache write 1.25 (5 min) and 2 (1 h) (750); the same message written twice counts once; terminal = 150 × 4 + 750",
        o.get(("richiesta", "terminale"), {}).get("eq") == 1350, str(o.get(("richiesta", "terminale"))))
T.check("MU4 compaction: 100000 tokens read from cache at 0.025 (2500) + the summary (400 characters ≈ 100 tokens × 5) + the first reply after (8000 × 1.25); one run",
        o.get(("automazione", "compattazioni"), {}).get("eq") == 2500 + 500 + 10000 and o[("automazione", "compattazioni")]["runs"] == 1, str(o.get(("automazione", "compattazioni"))))
T.check("MU5 a subagent's replies weigh on the turn that started it (the phone request: 150 + 1000)",
        o.get(("richiesta", "telefono"), {}).get("eq") == 1150, str(o.get(("richiesta", "telefono"))))
T.check("MU8 --json: hours[] with eq and automazioni, origins[] with name, kind, runs, share (shares sum to 1), automations first",
        d.get("hours") and {"hour", "eq", "automazioni"} <= set(d["hours"][0]) and {"name", "kind", "runs", "share"} <= set(d["origins"][0])
        and abs(sum(x["share"] for x in d["origins"]) - 1) < 0.01 and d["origins"][0]["kind"] == "automazione", str(d)[:600])
T.check("MU7 an unlisted automation → an alert with a stable key", any(a["key"].startswith("quota:master-fuori-elenco:") and "Ronda delle tre" in a["text"] for a in d.get("alerts", [])), str(d.get("alerts")))

# MU6: incrementale, poi l'elenco locale cambia e il conto si rifa'
with open(proj / "sid-a.jsonl", "a") as f:
    f.write(user(t0 + 300, "Dall'utente via telefono. ancora", "peer") + "\n" + reply(t0 + 301, "m15") + "\n")
o2 = origins(json.loads(run("--json").stdout))
T.check("MU6 a second read counts only the new lines (phone: one more turn, +150)", o2[("richiesta", "telefono")]["runs"] == 2 and o2[("richiesta", "telefono")]["eq"] == 1300
        and o2[("richiesta", "terminale")]["eq"] == 1350, str(o2.get(("richiesta", "telefono"))))
local.write_text(json.dumps({"automations": [{"name": "ronda delle tre", "match": "^Ronda delle tre", "origin": "scheduled", "since": "2026-10-25", "agreed_by": "maintainer"},
                                             {"name": "giro dei suggerimenti", "agreed_by": "maintainer"}]}))
d3 = json.loads(run("--json").stdout)
o3 = origins(d3)
T.check("MU6 the local list adds an automation and corrects one by name; the turns already counted are reassigned (no unlisted one left, no alert for it)",
        o3.get(("automazione", "ronda delle tre"), {}).get("runs") == 1 and o3[("automazione", "giro dei suggerimenti")].get("agreed_by") == "maintainer"
        and o3[("automazione", "giro dei suggerimenti")].get("since") == "2026-10-24" and not any(k[1].startswith("non in elenco") for k in o3)
        and not any("fuori-elenco" in a["key"] for a in d3["alerts"]) and o3[("richiesta", "telefono")]["eq"] == 1300, str(sorted(o3))[:600])

# MU7: le automazioni oltre un quarto; un'automazione raddoppiata nelle ultime 24 ore
old = NOW - 5 * 86400
(proj / "sid-b.jsonl").write_text("\n".join([
    peer(old, "supervisor", "Giro dei suggerimenti: leggi"), reply(old + 1, "b1", inp=100, out=0),
    peer(NOW - 600, "supervisor", "Giro dei suggerimenti: leggi"), reply(NOW - 599, "b2", inp=40000, out=0),
]) + "\n")
d4 = json.loads(run("--json").stdout)
keys = [a["key"] for a in d4["alerts"]]
T.check("MU7 automations over a quarter of the period → «quota:master-automazioni»; the suggestions round doubled in the last 24 hours → «quota:master-raddoppio:giro dei suggerimenti»; texts on one line without «…»",
        "quota:master-automazioni" in keys and "quota:master-raddoppio:giro dei suggerimenti" in keys and d4["automations_share"] > 0.25
        and all("…" not in a["text"] and "\n" not in a["text"] and len(a["text"]) <= 200 for a in d4["alerts"]), str(d4["alerts"]))
r5 = run("--suggest")
items = json.loads(sugg.read_text())["items"] if sugg.exists() else []
evp = tmp / "relay" / "local" / "events.json"


def quota_events():
    return sorted((e for e in (json.loads(evp.read_text()) if evp.exists() else {}).values() if e.get("kind") == "week_pace"), key=lambda e: e["key"])


ev5 = quota_events()
T.check("MU9 quota_alert_not_suggestion: --suggest no longer writes «quota» suggestions (no Accept, Not now, Never for an alert); the alerts go as one «week_pace» notification of the relay (quiet, no buttons): the title counts them, the body lists them; the text says how many were sent",
        r5.returncode == 0 and not [x for x in items if x.get("kind") == "quota"] and len(ev5) == 1 and ev5[0]["title"] == f"Consumo della master: {len(keys)} avvisi"
        and ev5[0]["body"].splitlines() == [a["text"] for a in d4["alerts"]] + ["supervisor quota --master"] and f"avvisi mandati: {len(keys)}" in r5.stdout,
        r5.stdout + r5.stderr + str(items) + str(ev5))
r5b = run("--notify")
apath = state / "master-usage-alerts.json"
sent = json.loads(apath.read_text()) if apath.exists() else {}
T.check("MU9 quota_alert_not_suggestion: the same automation at most once every 24 hours — a second run sends nothing",
        r5b.returncode == 0 and len(quota_events()) == len(ev5) and "avvisi mandati: 0" in r5b.stdout and sorted(sent) == sorted(keys), r5b.stdout + str(sent))
apath.write_text(json.dumps({k: (v - 25 * 3600 if k == "quota:master-automazioni" else v) for k, v in sent.items()}))
r5c = run("--notify")
T.check("MU9 quota_alert_not_suggestion: after 24 hours the same alert goes out again, only that one, its text as the title",
        r5c.returncode == 0 and len(quota_events()) == 2 and quota_events()[-1]["title"] == next(a["text"] for a in d4["alerts"] if a["key"] == "quota:master-automazioni")
        and "avvisi mandati: 1" in r5c.stdout, r5c.stdout + str(quota_events()))
r6 = run()
T.check("MU8 the text report: the period, the automations with since and who agreed, the requests, the sessions, the alerts",
        r6.returncode == 0 and "Master, ultimi 7 giorni" in r6.stdout and "Automazioni concordate" in r6.stdout and "(dal 2026-10-24, maintainer)" in r6.stdout
        and "Sessioni" in r6.stdout and "AVVISO" in r6.stdout, r6.stdout + r6.stderr)
T.check("MU8 --days wants a number", run("--days", "x").returncode == 2, "")

T.rm(str(tmp))
T.finish()
