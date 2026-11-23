#!/usr/bin/env python3
"""I sottoagenti di una sessione arrivano al relay e a supervisor sessions (23/11/2026, supervisor-sottoagenti-23-11).

Il maintainer il 23/11: «dobbiamo trovare il modo di mostrare i sottoagenti quando vengono lanciati, sul terminale si possono
vedere, su supervisor non ce n'e' traccia». Claude Code li scrive accanto alla trascrizione, in <sid>/subagents/. Il
fixture (tests/fixtures/subagents/, vedi il README) e' fatto di file veri: quattro sottoagenti di Claude Code 2.1.329
su win ridotti con riduci.py (le righe e le chiavi lette restano, i testi no) e un Workflow con la forma di uno vero
del Chromebook (2.1.322), con un agente ancora in corso.

SA1 cm-subagents.summary: 7 sottoagenti, 1 in corso (l'agente del Workflow senza result), il Workflow con i suoi conti,
    3 voci con quella in corso per prima
SA2 i valori dei file veri: l'Explore in background ha type, modello vero dalla trascrizione (il meta.json dice
    «haiku»), profondita', inizio e fine, e i token contati una volta per message.id (69142 in, 589 out: gli stessi
    del file intero e della lettura di cm-remote.ps1 su win)
SA3 il fork di ottobre senza trascrizione: modello «inherit», token null, finito
SA4 una sessione che non gira piu': niente in corso, tutti finiti con la loro fine
SA5 relay: la sessione locale ha `subagents` nello stato (al piu' 3 voci); finita → running 0; senza sottoagenti il
    campo non c'e'
SA6 fit_state: con lo stato oltre gli 8 KB le voci cadono per prime, i conti e tutte le sessioni restano
SA7 op subagents: l'elenco lungo della sessione (tutte e 7 le voci), ed e' fra le op offerte
SA8 una sessione di win: i sottoagenti dallo stato dell'host (cm-remote.ps1 status → cm-hosts → relay e sessions);
    cm-remote.ps1 li mette nello stato di ogni sessione viva
SA9 supervisor sessions: «sottoagenti: 7, 1 in corso» sulla riga della sessione; niente per chi non ne ha
"""
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

FIX = T.ROOT / "tests" / "fixtures" / "subagents" / "sessione"
tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
proj = ws / "personali" / "alfa"
proj.mkdir(parents=True)
(ws / "personali" / "beta").mkdir(parents=True)
(home / ".claude" / "sessions").mkdir(parents=True)
state, rdir = tmp / "state", tmp / "relay"
state.mkdir()
rdir.mkdir()
SID, SID_B = "11111111-2222-3333-4444-555555555555", "66666666-7777-8888-9999-000000000000"
slug = "".join(c if c.isalnum() else "-" for c in os.path.realpath(proj))
pdir = home / ".claude" / "projects" / slug
pdir.mkdir(parents=True)
(pdir / f"{SID}.jsonl").write_text('{"type":"user","timestamp":"2026-11-23T12:00:00Z"}\n')
shutil.copytree(FIX, pdir / SID)
slug_b = "".join(c if c.isalnum() else "-" for c in os.path.realpath(ws / "personali" / "beta"))
(home / ".claude" / "projects" / slug_b).mkdir(parents=True)
(home / ".claude" / "projects" / slug_b / f"{SID_B}.jsonl").write_text('{"type":"user","timestamp":"2026-11-23T12:00:00Z"}\n')

cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master", "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(rdir)},
    "registry": {"file": str(tmp / "registry.json")}, "tabs": {"color_registry": str(tmp / "colors")},
}))
ROW = {"pid": 4242, "name": "alfa", "tmux": "alfa", "account": "personale", "cwd": str(proj), "status": "idle", "session_id": SID,
       "attached": False, "channel": "nativo", "started_at": int(time.time() * 1000) - 60000, "waiting": False, "registry": "x", "version": "2.1.330"}
ROW_B = dict(ROW, pid=4343, name="beta", tmux="beta", cwd=str(ws / "personali" / "beta"), session_id=SID_B)
fake_cm = tmp / "supervisor"
fake_cm.write_text("#!/bin/sh\ncase \"$1\" in\n  sessions) cat '%s' ;;\n  registry) echo '{\"sessioni\": []}' ;;\n"
                   "  *) echo \"supervisor $1: not here\" >&2; exit 1 ;;\nesac\n" % (tmp / "rows.json"))
fake_cm.chmod(0o755)
(tmp / "rows.json").write_text(json.dumps([ROW, ROW_B]))
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE", "CLAUDE_OBSERVE_TOOL", "XDG_STATE_HOME"):
    os.environ.pop(k, None)
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_RELAY_CM": str(fake_cm),
                   "CM_PROC_SCAN_PIDS": "", "CM_RELAY_NO_PUSH": "1"})


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_sa", T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SA = load("cm-subagents")
sdir = pdir / SID

# SA1
s1 = SA.summary(sdir, alive=True)
T.check("SA1 summary: 7 subagents, 1 running (the Workflow agent with no result), the Workflow counted, 3 items, the running one first",
        s1 and s1["n"] == 7 and s1["running"] == 1 and s1.get("workflows") == [{"id": "wf_fixture-1", "n": 3, "running": 1}]
        and len(s1["items"]) == 3 and s1["items"][0]["id"] == "b000000000000000c" and s1["items"][0]["finished"] is False
        and s1["items"][0]["ended"] is None and s1["items"][0].get("phase") == "Review", json.dumps(s1)[:1500])

# SA2 / SA3
allx = {x["id"]: x for x in SA.summary(sdir, alive=True, n_items=30)["items"]}
ex = allx.get("a76eb4828a2262868") or {}
T.check("SA2 the real Explore: type, the real model from the transcript, background, depth 1, start and end, tokens once per message.id",
        ex.get("type") == "Explore" and ex.get("model") == "claude-haiku-5-5" and ex.get("bg") is True and ex.get("depth") == 1
        and ex.get("tokens") == {"in": 69142, "out": 589} and ex.get("finished") is True and ex.get("desc") == "fixture 2"
        and isinstance(ex.get("started"), int) and isinstance(ex.get("ended"), int) and ex["ended"] >= ex["started"], json.dumps(ex))
fk = allx.get("a1f3685a786f72627") or {}
T.check("SA3 the October fork without transcript: model inherit, tokens null, finished", fk.get("type") == "fork" and fk.get("model") == "inherit"
        and fk.get("tokens") is None and fk.get("finished") is True and len(allx) == 7, json.dumps(fk))

# SA4
s4 = SA.summary(sdir, alive=False, n_items=30)
T.check("SA4 a session that is no longer running: nothing running, every item finished with its end",
        s4["running"] == 0 and all(x["finished"] for x in s4["items"]) and allx and all(x["ended"] for x in s4["items"] if x.get("tokens"))
        and s4["workflows"][0]["running"] == 0, json.dumps(s4)[:800])

# SA5
RL = load("cm-relay")
RL.push_async = lambda *a, **k: None
S = RL.S
got = RL.subagents_of([ROW, ROW_B])
src = {"subagents": got, "root": str(ws)}
sess = S.build_session(ROW, src)
sess_b = S.build_session(ROW_B, src)
sess_g = S.build_session(dict(ROW, status="dead", visto_ts=1), {"subagents": {"alfa": s1}, "root": str(ws)})
sub = sess.get("subagents") or {}
T.check("SA5 relay: the local session carries subagents (n 7, running 1, 3 items); a session without any has no field; gone → running 0",
        "beta" not in got and sub.get("n") == 7 and sub.get("running") == 1 and len(sub.get("items") or []) == 3
        and "subagents" not in sess_b and (sess_g.get("subagents") or {}).get("running") == 0
        and all(x["finished"] for x in sess_g["subagents"]["items"]), json.dumps({"got": list(got), "sub": sub, "gone": sess_g.get("subagents")})[:1500])

# SA6
st = {"sessions": [dict(json.loads(json.dumps(sess)), name=f"s{i}", id=f"s{i}") for i in range(14)], "projects": [], "recap": {"items": []}}
before = S.size_of(st)
S.fit_state(st, 8)
T.check("SA6 fit_state over 8 KB: the subagent items go first, the counts and all 14 sessions stay",
        before > 8 * 1024 and S.size_of(st) <= 8 * 1024 and len(st["sessions"]) == 14
        and all(x["subagents"]["n"] == 7 and x["subagents"]["running"] == 1 for x in st["sessions"])
        and any(x["subagents"]["items"] == [] for x in st["sessions"]), f"{before} → {S.size_of(st)}")

# SA7
r7 = RL.run_cmd({"id": "c-sa7", "op": "subagents", "session": "alfa", "by": "phone"})
body = json.loads(r7["text"]) if r7 and r7.get("ok") else {}
T.check("SA7 op subagents: the long list of the session (7 items) and it is among the offered ops",
        len(((body.get("subagents") or {}).get("items")) or []) == 7 and body["subagents"]["n"] == 7 and "subagents" in RL.offered_ops(),
        json.dumps(r7)[:800])
r7b = RL.run_cmd({"id": "c-sa7b", "op": "subagents", "session": "beta", "by": "phone"})
T.check("SA7 op subagents on a session without any: {\"subagents\": null}", r7b and r7b.get("ok") and json.loads(r7b["text"]) == {"subagents": None},
        json.dumps(r7b)[:400])

# SA8
H = load("cm-hosts")
remote_sub = json.loads(json.dumps(s1))
H.remote_snapshots = lambda: {"win": {"at": time.time(), "status": {"sessions": [
    {"alive": True, "config_dir": ".claude", "entry": {"pid": 77, "sessionId": "abcd1234-0000", "cwd": "D:\\prova", "status": "idle", "name": "prova"},
     "subagents": remote_sub}]}}}
rrows = H.remote_session_rows()
rr = next((r for r in rrows if r.get("host") == "win"), {})
rs = S.build_session(dict(rr, tmux="", status="idle"), {"subagents": RL.subagents_of([dict(rr, tmux="")]), "root": str(ws)})
ps1 = (T.ROOT / "supervisor" / "remote" / "cm-remote.ps1").read_text(encoding="utf-8")
T.check("SA8 a win session: subagents from the host state reach the row, the relay and the counts; cm-remote.ps1 puts them in every live session",
        (rr.get("subagents") or {}).get("n") == 7 and (rs.get("subagents") or {}).get("running") == 1
        and "function SubagentsOf" in ps1 and "$item.subagents = $sa" in ps1, json.dumps({"row": rr.get("subagents", {}).get("n"), "rs": rs.get("subagents")})[:600])

# SA9
SS = load("cm-sessions")
out = SS.render([ROW, ROW_B, dict(rr, attached=None, registry="", channel="remoto", started_at=None, read_age_s=0)])
line_a = next((ln for ln in out.splitlines() if " alfa " in ln), "")
line_b = next((ln for ln in out.splitlines() if " beta " in ln), "")
line_w = next((ln for ln in out.splitlines() if "win:" in ln), "")
T.check("SA9 supervisor sessions: «sottoagenti: 7, 1 in corso» on the session's row (local and win), nothing on beta",
        "sottoagenti: 7, 1 in corso" in line_a and "sottoagenti" not in line_b and "sottoagenti: 7, 1 in corso" in line_w, out[:1500])

T.rm(str(tmp))
T.finish()
