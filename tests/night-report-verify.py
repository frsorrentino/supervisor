#!/usr/bin/env python3
"""Dati della pagina «Notte» (07/10/2026): la finestra, le domande e gli ok in alto, la cronologia dei lavori e delle
sessioni, le schede dei progetti con le parti da TASKS.md, dal piano o dal registro, l'anteprima HTML. Tutto finto:
HOME, trascrizioni, coda della notte, registro, stato del relay e un repo git vero in una cartella temporanea.
NR23-25 (07/10, dalla prova della notte): esito di un lavoro dalla sua riga «Esito:», commit dei worktree una volta
sola, niente doppioni di sessione."""
import datetime as dt
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="cm-night-report-"))
conf = tmp / ".claude"
ws = tmp / "ws"
atlas, orbit, nova, vega = ws / "atlas", ws / "orbit", ws / "nova", ws / "vega"
for d in (atlas, orbit, nova, vega):
    d.mkdir(parents=True)
state = tmp / "state"
state.mkdir()
relay = tmp / "relay"
relay.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "default_account": "personal", "state_dir": str(state), "workspace": {"root": str(ws)},
                           "relay": {"dir": str(relay)}, "accounts": {"personal": {"config_dir": str(conf), "tmux_prefix": "w-"}}}))
fake = tmp / "fake"
fake.mkdir()
ENV = dict(os.environ, CC_SUPERVISOR_CONFIG=str(cfg), CM_BIN=str(Path(__file__).resolve().parent / "lib" / "fake-cm-plan.py"),
           FAKE_CM_DIR=str(fake), CM_TASKS_DB=str(tmp / "tasks.db"), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
os.environ.update({k: ENV[k] for k in ("CC_SUPERVISOR_CONFIG", "CM_BIN", "FAKE_CM_DIR", "CM_TASKS_DB")})

# la notte di prova: «adesso» sono le 03:00 di oggi; la sera e' cominciata alle 20:00 di ieri (7 ore fa)
NOW = int(dt.datetime.now().replace(hour=3, minute=0, second=0, microsecond=0).timestamp())
H = 3600
EVE = NOW - 7 * H


def iso(t):
    return dt.datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def local_iso(t):
    return dt.datetime.fromtimestamp(t).isoformat(timespec="seconds")


def slug(p):
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(p)))


def line(kind, t, content, cwd, sdk=False, **kw):
    d = {"type": kind, "uuid": f"u{t}{kind}", "timestamp": iso(t), "isSidechain": False, "cwd": str(cwd),
         "entrypoint": "sdk-cli" if sdk else "cli", "message": {"role": kind, "content": content}}
    if kind == "user" and "origin" not in kw:
        d["origin"] = {"kind": "human"}
    d.update(kw)
    return json.dumps(d, separators=(",", ":"))


def bash(t, tid, cmd, cwd, sdk=False):
    return line("assistant", t, [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}], cwd, sdk)


def result(t, tid, text, cwd, sdk=False, err=False):
    return line("user", t, [{"type": "tool_result", "tool_use_id": tid, "is_error": err, "content": text}], cwd, sdk, origin=None)


def transcript(proj, sid, lines, last):
    d = conf / "projects" / slug(proj)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    f.write_text("\n".join(lines) + "\n")
    os.utime(f, (last, last))


# atlas: la sessione viva della persona. Ultimo suo prompt alle 23:30; poi lavora e chiude verde alle 00:30
transcript(atlas, "S-A", [
    line("user", EVE + 1 * H, "prepara il checkout", atlas),
    line("user", EVE + 3 * H + 1800, "vai avanti stanotte, io dormo", atlas),
    bash(EVE + 4 * H, "t1", "python3 tests/checkout-verify.py", atlas),
    result(EVE + 4 * H + 30, "t1", "12/12 OK", atlas),
    line("assistant", EVE + 4 * H + 1800, [{"type": "text", "text": "Esito: checkout pronto, suite verde"}], atlas),
], EVE + 4 * H + 1800)
# orbit: un `claude -p` lanciato dalla master all'01:00: il suo prompt NON e' della persona; test rosso alle 01:20
transcript(orbit, "S-O", [
    line("user", EVE + 5 * H, "lavoro notturno della master", orbit, sdk=True),
    bash(EVE + 5 * H + 600, "t2", "python3 tests/docs-verify.py", orbit, sdk=True),
    result(EVE + 5 * H + 1200, "t2", "3/4 OK, FAIL: D2", orbit, sdk=True),
], EVE + 5 * H + 1200)
# nova: la sessione `claude -p` del lavoro della coda delle 06:00-06:10 (02:00-02:10 locali): e' quel lavoro, non una riga in piu'
transcript(nova, "S-N", [
    line("user", EVE + 6 * H + 60, "rifai il README <b>bold</b>", nova, sdk=True),
    line("assistant", EVE + 6 * H + 500, [{"type": "text", "text": "Esito: README rifatto"}], nova, sdk=True),
], EVE + 6 * H + 500)
# lyra: una sessione che nella finestra ha solo ricevuto un prompt (come la master): nella cronologia si', scheda no
lyra = ws / "lyra"
lyra.mkdir()
transcript(lyra, "S-L", [line("user", EVE + 5 * H, "segui le sessioni", lyra, sdk=True)], EVE + 5 * H)
(fake / "sessions.json").write_text(json.dumps([{"tmux": "w-atlas", "name": "w-atlas", "cwd": str(atlas), "account": "personal", "session_id": "S-A"}]))

# commit: uno nella finestra (00:40), uno di ieri pomeriggio fuori
g = lambda *a, t=None: subprocess.run(["git", "-C", str(atlas), *a], capture_output=True, text=True, env=dict(ENV, **({"GIT_COMMITTER_DATE": f"@{t}", "GIT_AUTHOR_DATE": f"@{t}"} if t else {})))  # noqa: E731
g("init", "-q", "-b", "main")
(atlas / "a.txt").write_text("1"); g("add", "a.txt"); g("commit", "-qm", "old afternoon work", t=EVE - 4 * H)
(atlas / "a.txt").write_text("2"); g("add", "a.txt"); g("commit", "-qm", "feat: checkout", t=EVE + 4 * H + 2400)

# parti: atlas da TASKS.md, orbit dal piano toccato stanotte (quello vecchio no), vega dal registro, nova niente
(atlas / "TASKS.md").write_text("# Compiti\n\n- [x] carrello\n- [~] checkout\n- [!] pagamento (aspetta le chiavi)\n- [ ] email\nTesto senza casella\n")
(orbit / "docs" / "plans").mkdir(parents=True)
(orbit / "docs" / "plans" / "old.md").write_text("- [ ] vecchio piano\n")
os.utime(orbit / "docs" / "plans" / "old.md", (EVE - 48 * H, EVE - 48 * H))
(orbit / "docs" / "plans" / "new.md").write_text("1. [x] indice\n2. [ ] glossario\n")
os.utime(orbit / "docs" / "plans" / "new.md", (EVE + 5 * H, EVE + 5 * H))

# coda della notte: due finiti nella finestra (uno verde, uno rosso), uno finito ieri pomeriggio, uno in corso, uno in attesa
rep = nova / "docs" / "notte" / "x-n1-readme.md"
rep.parent.mkdir(parents=True)
rep.write_text("# Turno di notte\n\n- id: `n1`\n\n## Prompt\n\nrifai il README\n\n## Output\n\n\nREADME rifatto in 3 sezioni\naltro\n")
done = [
    {"id": "n0", "dir": str(nova), "prompt": "ieri", "rc": 0, "seconds": 60, "started": EVE - 5 * H, "finished": local_iso(EVE - 5 * H + 60), "report": ""},
    {"id": "n1", "dir": str(nova), "prompt": "rifai il README <b>bold</b>", "rc": 0, "seconds": 600, "started": EVE + 6 * H, "finished": local_iso(EVE + 6 * H + 600), "report": str(rep)},
    {"id": "n2", "dir": str(vega), "prompt": "## Dipendenze\n**aggiorna** le `dipendenze`, vedi [il piano](docs/p.md)\n" + "controlla ogni pacchetto " * 30, "rc": 1, "seconds": 300, "finished": local_iso(EVE + 6 * H + 1500), "report": str(tmp / "missing.md")},
]
(state / "night-done.jsonl").write_text("".join(json.dumps(r) + "\n" for r in done))
(state / "night-queue.jsonl").write_text(json.dumps({"id": "n3", "dir": str(vega), "prompt": "", "started": EVE + 6 * H + 1600}) + "\n"
                                         + json.dumps({"id": "n4", "dir": str(nova), "prompt": "traduci il sito", "added": "2026-10-06T23:00:00"}) + "\n")

# registro: vega ha un compito fatto, uno in corso e uno in attesa di ok (wait-ok)
CM = str(T.SCRIPTS / "supervisor")
for tid, title in (("v1", "schema"), ("v2", "migrazione"), ("v3", "deploy")):
    task = {"schema_version": 1, "task": {"id": tid, "title": title, "plan": None, "where": {"project": str(vega), "session": None, "host": "local", "account": None},
            "check": {"cmd": "true", "cwd": None, "timeout_s": 10}, "perimeter": [], "lane": "open", "depends_on": [], "route": "session", "hold": False,
            "attempts_max": 3, "data_class": "internal", "requested_by": "test"}}
    subprocess.run([CM, "task", "add", "-"], input=json.dumps(task), capture_output=True, text=True, env=ENV)
subprocess.run([CM, "task", "start", "v2"], capture_output=True, text=True, env=ENV)
subprocess.run([CM, "task", "wait-ok", "v3", "--what", "release 1.2", "--where", "produzione"], capture_output=True, text=True, env=ENV)
con = sqlite3.connect(str(tmp / "tasks.db"))
con.execute("UPDATE tasks SET state='done' WHERE id='v1'")
con.commit()
con.close()

# stato del relay: atlas ferma su una domanda, con un «Prossimi: !ok al merge · altro» e il suo «→ prossimo»
(relay / "last-state.json").write_text(json.dumps({"full": {"sessions": [
    {"name": "atlas", "project": "atlas", "state": "waiting", "next": "rilascio del checkout", "next_at": EVE - 20 * H,
     "question": {"id": "q-1-1", "kind": "ask", "text": "Pubblico adesso?", "options": [{"n": 1, "label": "sì"}, {"n": 2, "label": "no"}], "tier": "medium", "asked_at": EVE + 4 * H},
     "next_steps": [{"text": "ok al merge", "blocking": True}, {"text": "altro", "blocking": False}]},
    {"name": "orbit", "project": "orbit", "state": "idle", "question": None, "next": "prossimo di dieci giorni fa", "next_at": EVE - 240 * H}]}}))


def run(*a):
    p = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-night-report.py"), *a], capture_output=True, text=True, env=ENV, timeout=120)
    return p.returncode, p.stdout, p.stderr


# ------------------------------------------------------------------ la regola della finestra, pura
spec = importlib.util.spec_from_file_location("cm_night_report", T.SCRIPTS / "cm-night-report.py")
NR = importlib.util.module_from_spec(spec)
spec.loader.exec_module(NR)
w = NR.window(NOW, [])
T.check("NR1 no message: the window starts at 20:00 of the evening (floor_20) and ends now",
        w["start"] == EVE and w["end"] == NOW and w["start_source"] == "floor_20" and w["last_message"] is None, str(w))
noon = NOW + 9 * H
w = NR.window(noon, [])
T.check("NR2 opened at 12:00: never more than 14 hours, the start moves to 22:00 (cap_14h)",
        w["start"] == noon - 14 * H and w["start_source"] == "cap_14h", str(w))
w = NR.window(NOW + 5 * H, [{"at": EVE - 1800}, {"at": EVE + 2 * H}, {"at": NOW + 4 * H + 3300}])
T.check("NR3 the last message of the evening (22:00) wins; 19:30 is before the floor, 07:55 is morning",
        w["start"] == EVE + 2 * H and w["start_source"] == "last_message", str(w))
w = NR.window(EVE + 5400, [{"at": EVE + 3600}])
T.check("NR4 opened at 21:30 the same evening: the floor is today's 20:00, the 21:00 message starts the window",
        w["start"] == EVE + 3600 and w["floor"] == EVE, str(w))

# ------------------------------------------------------------------ il rapporto intero
rc, out, err = run("--now", str(NOW), "--print")
r = json.loads(out) if rc == 0 else {}
T.check("NR5 --print: schema and version (2 since 21/10), the date of now, every source read", rc == 0 and r.get("schema") == "team-supervisor/night-report" and r.get("v") == 2
        and r.get("date") == dt.date.fromtimestamp(NOW).isoformat()
        and r.get("sources") == {"transcripts": "ok", "timeline": "ok", "night_done": "ok", "night_queue": "ok", "registry": "ok", "relay_state": "ok"},
        out[:300] + err[-800:])
win = r.get("window", {})
T.check("NR6 window from the person's last prompt (23:30 in atlas, typed); the master's `claude -p` prompt at 01:00 does not count",
        win.get("start") == EVE + 3 * H + 1800 and win.get("start_source") == "last_message" and (win.get("last_message") or {}).get("session") == "atlas"
        and (win.get("last_message") or {}).get("origin") == "pc", json.dumps(win))
att = r.get("attention", {})
T.check("NR7 on top: the question with its id and options, the registry's ok with what and where, the «!» step only",
        [(q["session"], q["id"], len(q["options"]), q["project"]) for q in att.get("questions", [])] == [("atlas", "q-1-1", 2, os.path.realpath(atlas))]
        and [(a["task"], a["what"], a["where"]) for a in att.get("approvals", [])] == [("v3", "release 1.2", "produzione")]
        and [u["text"] for u in att.get("unblock", [])] == ["ok al merge"], json.dumps(att))
# NR-C (1.76, 22/10, P2): gli impegni dal registro nel resoconto — v3 aspetta l'ok (approved false), v2 senza ok non entra; la sezione nell'HTML
cms = r.get("commitments")
T.check("NR-C commitments from the registry: v3 awaiting_ok (approved false, waits for the person); v1 done and v2 without an ok are not commitments; key order of the contract",
        [(x["task"], x["state"], x["approved"], x["waits"]) for x in cms or []] == [("v3", "awaiting_ok", False, {"who": "master", "on": "person"})]
        and list(cms[0]) == ["task", "title", "project", "session", "state", "lane", "plan", "approved", "approved_at", "approved_by", "age_s", "last_step", "idle_s", "next", "waits", "expected", "outcome_kind", "check", "paused", "stalled"], json.dumps(cms)[:300])
_html_c = NR.render_html(r)
T.check("NR-C the HTML has an «Impegni» section with the task, «aspetta l'utente» and the next step", "<h2>Impegni</h2>" in _html_c and "<b>v3 · master · awaiting_ok</b>" in _html_c and "aspetta l&#x27;utente" in _html_c, _html_c[_html_c.find("<h2>Impegni"):][:300])
tl = r.get("timeline", [])
byid = {x["id"]: x for x in tl}
T.check("NR8 timeline: the two night jobs of the window, the running one, the sessions atlas and orbit; yesterday's job and nova's `claude -p` transcript not repeated",
        sorted(byid) == ["atlas", "lyra", "n1", "n2", "n3", "orbit"] and [x["start"] for x in tl] == sorted(x["start"] for x in tl), str(sorted(byid)))
n1, n2, n3 = byid.get("n1", {}), byid.get("n2", {}), byid.get("n3", {})
T.check("NR9 night jobs: start from `started` or finished − seconds, outcome from rc, detail from the .md Output, report only if it exists; running = end null",
        (n1.get("start"), n1.get("end"), n1.get("outcome"), n1.get("detail"), n1.get("report")) == (EVE + 6 * H, EVE + 6 * H + 600, "ok", "README rifatto in 3 sezioni", str(rep))
        and (n2.get("start"), n2.get("outcome"), n2.get("report")) == (EVE + 6 * H + 1200, "failed", None)
        and (n3.get("outcome"), n3.get("end")) == ("running", None), json.dumps([n1, n2, n3]))
a, o = byid.get("atlas", {}), byid.get("orbit", {})
T.check("NR10 sessions: atlas ok with its «Esito:», live, counts; orbit failed on its red test",
        a.get("outcome") == "ok" and a.get("detail") == "checkout pronto, suite verde" and a.get("live") is True
        and a.get("counts") == {"prompts": 1, "tests": 1, "commits": 1} and o.get("outcome") == "failed" and o.get("live") is False, json.dumps([a, o]))
_p2 = n2.get("prompt") or ""
T.check("NR26 (1.46) timeline[].prompt: a night job's queue text without markdown, on one line, cut at a whole word within 400; a session's first prompt of the night; null when missing",
        n1.get("prompt") == "rifai il README <b>bold</b>"
        and _p2.startswith("Dipendenze aggiorna le dipendenze, vedi il piano controlla ogni pacchetto") and len(_p2) <= 400 and _p2.endswith("pacchetto")
        and n3.get("prompt") is None and a.get("prompt") == "vai avanti stanotte, io dormo" and o.get("prompt") == "lavoro notturno della master"
        and byid.get("lyra", {}).get("prompt") == "segui le sessioni", json.dumps({k: byid.get(k, {}).get("prompt") for k in byid}, ensure_ascii=False))
T.check("NR11 the queue still to run: one item, not started", [q["id"] for q in r.get("queue", [])] == ["n4"], json.dumps(r.get("queue")))
cards = {c["name"]: c for c in r.get("projects", [])}
ca, co, cn, cv = (cards.get(k, {}) for k in ("atlas", "orbit", "nova", "vega"))
T.check("NR12 parts from TASKS.md in their order and states, lines without a box ignored",
        ca.get("parts_source") == "TASKS.md" and [(p["title"], p["state"]) for p in ca.get("parts", [])]
        == [("carrello", "done"), ("checkout", "running"), ("pagamento (aspetta le chiavi)", "blocked"), ("email", "todo")], json.dumps(ca)[:400])
T.check("NR13 parts from the plan touched tonight (not the old one)", co.get("parts_source") == "plan" and co.get("parts_file", "").endswith("new.md")
        and [(p["title"], p["state"]) for p in co.get("parts", [])] == [("indice", "done"), ("glossario", "todo")], json.dumps(co)[:400])
T.check("NR14 parts from the registry: done, running, awaiting_ok = blocked", cv.get("parts_source") == "registry"
        and [(p["title"], p["state"]) for p in cv.get("parts", [])] == [("schema", "done"), ("migrazione", "running"), ("deploy", "blocked")], json.dumps(cv)[:400])
T.check("NR15 no source of parts: none invented, only the events", cn.get("parts_source") is None and cn.get("parts") == [] and cn.get("events"), json.dumps(cn)[:400])
T.check("NR16 a card says what it waits on and the next step (orbit's ten-day-old «→ next» from the relay is not it); the commit of the window is there, the afternoon one is not",
        ca.get("waiting_on") == ["domanda: Pubblico adesso?", "!: ok al merge"] and ca.get("next") == "rilascio del checkout"
        and cv.get("waiting_on") == ["ok: deploy"] and co.get("next") == "glossario"
        and [e["text"] for e in ca.get("events", []) if e["kind"] == "commit"] == ["feat: checkout"], json.dumps([ca.get("waiting_on"), ca.get("next"), cv.get("waiting_on"), co.get("next")]))
T.check("NR17 cards needing the person come first; a project with nothing to show (lyra) has no card",
        [c["name"] for c in r.get("projects", [])][:2] == ["atlas", "vega"] and "lyra" not in cards, str([c["name"] for c in r.get("projects", [])]))

# ------------------------------------------------------------------ file e anteprima
out_dir = tmp / "out"
rc, out, err = run("--now", str(NOW), "--out-dir", str(out_dir))
day = dt.date.fromtimestamp(NOW).isoformat()
page = (out_dir / f"{day}.html").read_text() if (out_dir / f"{day}.html").is_file() else ""
saved = json.loads((out_dir / f"{day}.json").read_text()) if (out_dir / f"{day}.json").is_file() else {}
T.check("NR18 --out-dir writes <date>.json and <date>.html; the JSON on disk is the same report", rc == 0 and saved.get("window") == r.get("window")
        and [x["id"] for x in saved.get("timeline", [])] == [x["id"] for x in tl], out + err[-500:])
T.check("NR19 the HTML: one file, no script, no external resource, text escaped, the sections and a lane per item",
        page.startswith("<!doctype html>") and "<script" not in page and not re.search(r"(?:src|href)=\"(?:https?:)?//", page)
        and "&lt;b&gt;bold&lt;/b&gt;" in page and "<b>bold</b>" not in page and "Serve a te" in page and "Pubblico adesso?" in page
        and page.count("class=\"lane\"") == len(tl) and page.count("class=\"bar\"") >= len(tl) and "da TASKS.md" in page and "solo eventi" in page, page[:300])
rc, out, err = run("--now", str(NOW), "--out-dir", str(tmp / "out2"), "--json-only")
T.check("NR20 --json-only: no HTML", rc == 0 and (tmp / "out2" / f"{day}.json").is_file() and not (tmp / "out2" / f"{day}.html").exists(), out + err)

# fonti assenti: niente coda, niente registro, niente relay → il rapporto c'e' lo stesso e lo dice
ENV2 = dict(ENV, CM_TASKS_DB=str(tmp / "none.db"))
cfg2 = tmp / "config2.json"
cfg2.write_text(json.dumps({"language": "it", "default_account": "personal", "state_dir": str(tmp / "empty"), "workspace": {"root": str(ws)},
                            "relay": {"dir": str(tmp / "norelay")}, "accounts": {"personal": {"config_dir": str(conf), "tmux_prefix": "w-"}}}))
ENV2["CC_SUPERVISOR_CONFIG"] = str(cfg2)
p = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-night-report.py"), "--now", str(NOW), "--print"], capture_output=True, text=True, env=ENV2, timeout=120)
r2 = json.loads(p.stdout) if p.returncode == 0 else {}
T.check("NR21 missing sources are named in `sources` and the report is still built", p.returncode == 0
        and {k: r2["sources"][k] for k in ("night_done", "night_queue", "registry", "relay_state")} == {k: "missing" for k in ("night_done", "night_queue", "registry", "relay_state")}
        and r2["attention"] == {"questions": [], "approvals": [], "unblock": []} and r2["commitments"] == [] and {x["id"] for x in r2["timeline"]} == {"atlas", "lyra", "orbit", "nova"},
        p.stdout[:300] + p.stderr[-500:])
T.check("NR22 a bad --now → a plain error", run("--now", "yesterday", "--print")[0] != 0, "")

# ------------------------------------------------------------------ difetti della prova del 07/10
# helio con un worktree helio-wt: un commit fatto nel worktree e uno nel principale; due conversazioni chiuse della
# stessa sessione in helio; la stessa trascrizione letta da due righe vive; un lavoro della coda fermo con rc=0
conf3, ws3, st3, fk3 = tmp / "c3", tmp / "ws3", tmp / "st3", tmp / "fk3"
helio, helio_wt = ws3 / "helio", ws3 / "helio-wt"
for d in (conf3, helio, st3, fk3):
    d.mkdir(parents=True)
cfg3 = tmp / "config3.json"
cfg3.write_text(json.dumps({"language": "it", "default_account": "personal", "state_dir": str(st3), "workspace": {"root": str(ws3)},
                            "relay": {"dir": str(tmp / "norelay3")}, "accounts": {"personal": {"config_dir": str(conf3), "tmux_prefix": "w-"}}}))
ENV3 = dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg3), FAKE_CM_DIR=str(fk3), CM_TASKS_DB=str(tmp / "none3.db"))
g3 = lambda d, *a, t=None: subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, env=dict(ENV3, **({"GIT_COMMITTER_DATE": f"@{t}", "GIT_AUTHOR_DATE": f"@{t}"} if t else {})))  # noqa: E731
g3(helio, "init", "-q", "-b", "main")
(helio / "h.txt").write_text("0"); g3(helio, "add", "h.txt"); g3(helio, "commit", "-qm", "base", t=EVE - 30 * H)
g3(helio, "worktree", "add", "-q", "-b", "notte/x", str(helio_wt))
(helio_wt / "w.txt").write_text("1"); g3(helio_wt, "add", "w.txt"); g3(helio_wt, "commit", "-qm", "feat: made in the worktree", t=EVE + 6 * H + 300)
(helio / "h.txt").write_text("1"); g3(helio, "add", "h.txt"); g3(helio, "commit", "-qm", "fix: made in main", t=EVE + 4 * H + 300)


def tr3(proj, sid, lines, last):
    d = conf3 / "projects" / slug(proj)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")
    os.utime(d / f"{sid}.jsonl", (last, last))


tr3(helio, "H1", [line("user", EVE + 1 * H + 600, "prima conversazione", helio),
                  line("assistant", EVE + 3 * H + 600, [{"type": "text", "text": "Esito: handoff scritto"}], helio)], EVE + 3 * H + 600)
tr3(helio, "H2", [line("user", EVE + 1 * H + 1200, "dopo il clear", helio),
                  line("assistant", EVE + 4 * H + 400, [{"type": "text", "text": "Esito: fix fatto"}], helio)], EVE + 4 * H + 400)
tr3(helio_wt, "W1", [line("user", EVE + 6 * H + 10, "lavoro della coda", helio_wt, sdk=True),
                     line("assistant", EVE + 6 * H + 500, [{"type": "text", "text": "Esito: fatto"}], helio_wt, sdk=True)], EVE + 6 * H + 500)
tr3(helio, "H3", [line("user", EVE + 1 * H, "sessione viva", helio),
                  line("assistant", EVE + 5 * H + 100, [{"type": "text", "text": "Esito: in attesa"}], helio)], EVE + 5 * H + 100)
(fk3 / "sessions.json").write_text(json.dumps([{"tmux": n, "name": n, "cwd": str(helio), "account": "personal", "session_id": "H3"} for n in ("w-sole", "w-luna")]))
r3 = helio_wt / "docs" / "notte"
r3.mkdir(parents=True)
(r3 / "s1.md").write_text("# Turno di notte\n\n- esito: rc=0\n\n## Output\n\nSono fermo prima del piano e non ho scritto codice.\n\n"
                          "Esito: lavoro notturno fermo per la specifica non leggibile, blocco segnalato.\n\nProssimi: ! specifica copiata\n")
(r3 / "s2.md").write_text("# Turno di notte\n\n## Output\n\nFatto: quattro regole con i test.\n\n**Esito:** nucleo fatto e verificato, 5 commit; push alla master.\n")
(st3 / "night-done.jsonl").write_text("".join(json.dumps(x) + "\n" for x in (
    {"id": "s1", "dir": str(helio_wt), "prompt": "nucleo", "rc": 0, "seconds": 100, "started": EVE + 5 * H + 1800, "finished": local_iso(EVE + 5 * H + 1900), "report": str(r3 / "s1.md")},
    {"id": "s2", "dir": str(helio_wt), "prompt": "nucleo, di nuovo", "rc": 0, "seconds": 900, "started": EVE + 6 * H, "finished": local_iso(EVE + 6 * H + 900), "report": str(r3 / "s2.md")})))
p = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-night-report.py"), "--now", str(NOW), "--print"], capture_output=True, text=True, env=ENV3, timeout=120)
r3j = json.loads(p.stdout) if p.returncode == 0 else {}
b3 = {x["id"]: x for x in r3j.get("timeline", [])}
T.check("NR23 a night job that exits 0 but whose «Esito:» says it stopped is `stopped`, with the Esito as detail; one that did its work is ok",
        (b3.get("s1", {}).get("outcome"), b3.get("s1", {}).get("detail")) == ("stopped", "lavoro notturno fermo per la specifica non leggibile, blocco segnalato.")
        and (b3.get("s2", {}).get("outcome"), b3.get("s2", {}).get("detail")) == ("ok", "nucleo fatto e verificato, 5 commit; push alla master."),
        json.dumps([b3.get("s1"), b3.get("s2")]) + p.stderr[-600:])
c3 = {c["name"]: [e["text"] for e in c["events"] if e["kind"] == "commit"] for c in r3j.get("projects", [])}
T.check("NR24 (v2) worktrees of one repo: each commit once (not `git log --all` twice); a worktree named «<repo>-…» is in the repo's card",
        sorted(c3.get("helio") or []) == ["feat: made in the worktree", "fix: made in main"] and "helio-wt" not in c3
        and [c["worktrees"] for c in r3j.get("projects", []) if c["name"] == "helio"] == [[os.path.realpath(helio_wt)]], json.dumps(c3))
sess = [x for x in r3j.get("timeline", []) if x["kind"] == "session"]
T.check("NR25 no duplicates: the two closed conversations of helio are one row, the transcript read by two live rows is one row, the queue's `claude -p` is its job",
        len(sess) == 2 and sorted(x["id"] for x in sess) in (["helio", "sole"], ["helio", "luna"]) and "helio-wt" not in b3,
        json.dumps([(x["id"], x["start"], x["end"]) for x in r3j.get("timeline", [])]))

# ------------------------------------------------------------------ v2 (21/10, segnalazione del maintainer sulla notte 20-21/10)
# la notte: alle 00:00 la master scrive il piano di deneb e assegna il lavoro con un messaggio (deneb non ha un prompt
# della persona: prima spariva); la persona scrive alla master fino all'01:00; rigel lavora su win; mira si ferma; sirio
# chiude due resoconti con uno finale; una prova in uno scratchpad; alle 06:30 una sessione prova (pc) un'altra; alle
# 07:30 la persona scrive alla master: la notte finisce li'. «Adesso» sono le 08:00.
NOW4 = NOW + 5 * H
DAY4 = dt.date.fromtimestamp(NOW4).isoformat()
conf4, ws4, st4, fk4, win4 = tmp / "c4", tmp / "ws4", tmp / "st4", tmp / "fk4", tmp / "win4"
deneb, rigel, mira, sirio, vela, kappa = (ws4 / n for n in ("deneb", "rigel", "mira", "sirio", "vela", "kappa"))
for d in (conf4, st4, fk4, win4, deneb, rigel, mira, sirio, vela):
    d.mkdir(parents=True)
cfg4 = tmp / "config4.json"
cfg4.write_text(json.dumps({"language": "it", "default_account": "personal", "state_dir": str(st4), "workspace": {"root": str(ws4)},
                            "relay": {"dir": str(tmp / "norelay4")}, "accounts": {"personal": {"config_dir": str(conf4), "tmux_prefix": "w-"}},
                            "hosts": {"win": {"kind": "windows-native", "confirmed_at": "2026-10-01T00:00:00", "sessions": {"root": "W:/s"}}}}))
ENV4 = dict(ENV, CC_SUPERVISOR_CONFIG=str(cfg4), FAKE_CM_DIR=str(fk4), CM_TASKS_DB=str(tmp / "none4.db"),
            CM_RSESSION_ADAPTER=str(Path(__file__).resolve().parent / "lib" / "fake-rsession-adapter.py"), FAKE_WIN_ROOT=str(win4))
g4 = lambda d, *a, t=None: subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, env=dict(ENV4, **({"GIT_COMMITTER_DATE": f"@{t}", "GIT_AUTHOR_DATE": f"@{t}"} if t else {})))  # noqa: E731


def tr4(root, proj, sid, lines, last, sub=None):
    d = (root / "projects" / (sub or slug(proj)))
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")
    os.utime(d / f"{sid}.jsonl", (last, last))


def md(path, text, t):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (t, t))


T0 = EVE + 4 * H   # 00:00
peer = {"kind": "peer", "name": "supervisor", "msg_id": "m1", "body": "Dalla master, su richiesta di Ada: leggi docs/proposte e lavora. Ramo notte/play."}
tr4(conf4, ws4, "M1", [line("user", T0 - 600, "prepara il piano di deneb", ws4), line("user", T0 + H, "vado a dormire", ws4),
                       line("user", NOW4 - 1800, "com'e' andata stanotte?", ws4)], NOW4 - 1800)
tr4(conf4, deneb, "D1", [json.dumps({"type": "agent-name", "agentName": "deneb", "sessionId": "D1"}),
                         line("user", T0 + 1800, "Another Claude session sent a message", deneb, origin=peer),
                         line("assistant", T0 + 2400, [{"type": "text", "text": "leggo la proposta"}], deneb),
                         line("assistant", T0 + 5400, [{"type": "text", "text": "Esito: pubblicazione preparata"}], deneb)], T0 + 5400)
tr4(conf4, mira, "MI1", [line("user", T0 + 600, "lavora su mira", mira), line("assistant", T0 + 3000, [{"type": "text", "text": "provo"}], mira)], T0 + 3000)
tr4(conf4, sirio, "SI1", [line("user", T0 + 600, "lavora su sirio", sirio), line("assistant", T0 + 4000, [{"type": "text", "text": "fatto"}], sirio)], T0 + 4000)
tr4(conf4, Path("/tmp/claude-1000/x/scratchpad/live/work"), "X1", [line("user", T0 + 600, "prova", "/tmp/claude-1000/x/scratchpad/live/work"),
                                                                  line("assistant", T0 + 3000, [{"type": "text", "text": "ok"}], "/tmp/claude-1000/x/scratchpad/live/work")], T0 + 3000)
tr4(conf4, vela, "V1", [line("user", NOW4 - 5400, "Rispondi solo: ok", vela), line("assistant", NOW4 - 5370, [{"type": "text", "text": "ok"}], vela)], NOW4 - 5370)
# rigel su win: la trascrizione e il resoconto stanno la', con i percorsi W:\s\rigel
tr4(win4, None, "R1", [json.dumps({"type": "agent-name", "agentName": "win-rigel", "sessionId": "R1"}),
                       line("user", T0 + 1200, "Another Claude session sent a message", "W:\\s\\rigel", origin=dict(peer, msg_id="m2", body="Dalla master: Rigel ad oltranza. Poi il resto.")),
                       line("assistant", T0 + 4800, [{"type": "text", "text": "stanze fatte"}], "W:\\s\\rigel")], T0 + 4800, sub="W--s-rigel")
md(win4 / "s" / "rigel" / "docs" / "notte" / f"{DAY4}-rigel.md", "# Notte — rigel\n\n## Esito\n\nTre stanze su cinque.\n\n## Decisioni aperte per Ada\n\n1. **Il nome della quarta stanza**: Mercato o Piazza?\n", T0 + 4900)
# il piano di deneb, scritto alle 00:00: tre punti; il resoconto con «FINE»
md(deneb / "docs" / "proposte" / f"{DAY4}-play.md", "# Pubblicazione su Play (richiesta di Ada, 21/10)\n\nTesto.\n\n## Cosa produrre\n\n1. **Build firmata**: AAB.\n2. **Scheda dello store** in due lingue.\n3. Lista per Ada.\n\n## Vincoli\n\n- niente segreti\n", T0)
md(deneb / "docs" / "proposte" / "2026-10-01-vecchia.md", "# Vecchia proposta\n", T0 + 60)
md(deneb / "docs" / "notte" / f"{DAY4}-play.md", "# Notte 21/10/2026 — deneb\n\nEsito: AAB firmato e scheda pronta\n\nFINE\n", T0 + 5500)
md(mira / "docs" / "notte" / f"{DAY4}-mira.md", "# mira\n\nEsito: fermo, manca la chiave di firma\n", T0 + 3100)
md(sirio / "docs" / "notte" / f"{DAY4}-parte.md", "# sirio, parte\n\n## Esito\n\nprima parte fatta\n", T0 + 3500)
md(sirio / "docs" / "notte" / f"{DAY4}-resoconto-finale.md", "# sirio, finale\n\nTutto verde.\n\nFINE\n", T0 + 4100)
# deneb: un commit all'01:10, il push e il tag all'01:20
bare = tmp / "deneb.git"
subprocess.run(["git", "init", "-q", "--bare", str(bare)], capture_output=True)
g4(deneb, "init", "-q", "-b", "main")
(deneb / "a.txt").write_text("1"); g4(deneb, "add", "a.txt"); g4(deneb, "commit", "-qm", "build: AAB firmato", t=T0 + 4200)
g4(deneb, "remote", "add", "origin", str(bare))
g4(deneb, "push", "-q", "origin", "main", t=T0 + 4800)
g4(deneb, "tag", "-a", "v1.0", "-m", "release", t=T0 + 4800)
# vela con un worktree che non si chiama «vela-…»: e' un progetto suo
g4(vela, "init", "-q", "-b", "main"); (vela / "v.txt").write_text("1"); g4(vela, "add", "v.txt"); g4(vela, "commit", "-qm", "base", t=EVE - 30 * H)
g4(vela, "worktree", "add", "-q", "-b", "k", str(kappa))
(fk4 / "sessions.json").write_text("[]")

p = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-night-report.py"), "--now", str(NOW4), "--print"], capture_output=True, text=True, env=ENV4, timeout=120)
r4 = json.loads(p.stdout) if p.returncode == 0 else {}
w4 = r4.get("window", {})
T.check("NR27 (v2) the night starts at the first plan (00:00), not at the person's last evening message (01:00), and ends at the first morning message to the master (07:30); the 06:30 probe of one session to another («pc») does not end it",
        (w4.get("start"), w4.get("start_source"), w4.get("end"), w4.get("end_source")) == (T0, "night_plan", NOW4 - 1800, "morning_message")
        and (w4.get("last_message") or {}).get("text") == "vado a dormire", json.dumps(w4) + p.stderr[-800:])
b4 = {x["id"]: x for x in r4.get("timeline", [])}
dn = b4.get("deneb", {})
T.check("NR28 (v2) a session started by another session's message, with no prompt of the person, is there: its spans, the message as `assigned` and `prompt`, local host",
        dn.get("spans") == [[T0 + 1800, T0 + 2400], [T0 + 5400, T0 + 5400]] and dn.get("assigned", "").startswith("Dalla master, su richiesta di Ada")
        and (dn.get("prompt") or "").startswith("Dalla master") and dn.get("host") == "local" and dn.get("start") == T0 + 1800 and dn.get("end") == T0 + 5400,
        json.dumps(dn))
T.check("NR29 (v2) a session in a scratchpad (/tmp/claude-*/…/scratchpad) is not part of the night; the master's own session is there with role master",
        not any("scratchpad" in (x.get("dir") or "") for x in r4.get("timeline", [])) and [x["role"] for x in r4.get("timeline", []) if x.get("dir") == os.path.realpath(ws4)] == ["master"],
        str(sorted(b4)))
rg = b4.get("win-rigel", {})
mirror = st4 / "hosts" / "transcripts" / "win" / "R1.jsonl"
T.check("NR30 (v2) a session on win: its transcript copied into the local mirror, named by its agent-name, on the local folder of the same project, host win",
        rg.get("host") == "win" and rg.get("dir") == os.path.realpath(rigel) and mirror.is_file() and rg.get("spans") == [[T0 + 1200, T0 + 1200], [T0 + 4800, T0 + 4800]]
        and r4.get("sources", {}).get("host:win") == "ok", json.dumps(rg) + json.dumps(r4.get("sources")))
cards4 = {c["name"]: c for c in r4.get("projects", [])}
cd, cr, cm_, cs = (cards4.get(k, {}) for k in ("deneb", "rigel", "mira", "sirio"))
gd = (cd.get("goals") or [{}])[0]
T.check("NR31 (v2) the goal from docs/proposte with the night's date (an old proposal is not), its numbered points under «Cosa produrre», done at its report's FINE; where = done with the report's Esito",
        len(cd.get("goals", [])) == 1 and gd.get("title") == "Pubblicazione su Play" and [x["title"] for x in gd.get("points", [])] == ["Build firmata", "Scheda dello store", "Lista per Ada."]
        and gd.get("state") == "done" and gd.get("done_at") == T0 + 5500 and all(x["state"] == "done" and x["done_at"] == T0 + 5500 for x in gd["points"])
        and cd.get("where", {}).get("state") == "done" and cd["where"].get("text") == "AAB firmato e scheda pronta", json.dumps(cd.get("goals")) + json.dumps(cd.get("where")))
T.check("NR32 (v2) milestones: the commit, the push (reflog of the remote branch), the tag as release, the FINE, in time order",
        [(m["kind"], m["at"]) for m in cd.get("milestones", [])] == [("commit", T0 + 4200), ("push", T0 + 4800), ("release", T0 + 4800), ("fine", T0 + 5500)],
        json.dumps(cd.get("milestones")))
T.check("NR33 (v2) a project with no proposal: the goal is the assigning message; a report from win without FINE → partial, what is missing, the decision for Ada in needs_you",
        [x["source"] for x in cr.get("goals", [])] == ["assigned"] and cr["goals"][0]["title"] == "Rigel ad oltranza." and cr.get("hosts") == ["win"]
        and cr.get("where", {}).get("state") == "partial" and cr["where"].get("text") == "Tre stanze su cinque." and cr["where"].get("missing") == ["Rigel ad oltranza."]
        and cr["where"].get("needs_you") == ["Il nome della quarta stanza"], json.dumps(cr.get("goals")) + json.dumps(cr.get("where")))
T.check("NR34 (v2) a report that says it stopped → stopped with its reason; a final report with FINE closes the earlier one without it → done",
        cm_.get("where", {}).get("state") == "stopped" and cm_["where"].get("text") == "fermo, manca la chiave di firma"
        and cs.get("where", {}).get("state") == "done" and cs["where"].get("text") == "Tutto verde.", json.dumps([cm_.get("where"), cs.get("where")]))
T.check("NR35 (v2) the night's title counts the projects by where they got; date = the morning's",
        r4.get("title") == "4 progetti: 2 fatti, 1 in parte, 1 fermi" and r4.get("date") == DAY4, str(r4.get("title")) + " " + str(sorted(cards4)))
_spec4 = importlib.util.spec_from_file_location("cm_night_report4", T.SCRIPTS / "cm-night-report.py")
_old_cfg = os.environ.get("CC_SUPERVISOR_CONFIG")
os.environ["CC_SUPERVISOR_CONFIG"] = str(cfg4)
NR4 = importlib.util.module_from_spec(_spec4)
_spec4.loader.exec_module(NR4)
os.environ["CC_SUPERVISOR_CONFIG"] = _old_cfg
T.check("NR36 (v2) a worktree not named «<repo>-…» is a project of its own; a plain folder is itself",
        NR4.repo_of(str(kappa)) == os.path.realpath(kappa) and NR4.repo_of(str(vela)) == os.path.realpath(vela)
        and NR4.rel_under("W:\\s\\rigel\\docs\\notte\\x.md", "W:/s/rigel") == "docs/notte/x.md" and NR4.rel_under("W:/t/x", "W:/s") is None, "")
p = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-night-report.py"), "--now", str(NOW4), "--print", "--no-remote"], capture_output=True, text=True, env=ENV4, timeout=120)
r5 = json.loads(p.stdout) if p.returncode == 0 else {}
T.check("NR37 (v2) --no-remote: no host read, no win session", "host:win" not in r5.get("sources", {}) and not any(x.get("host") == "win" for x in r5.get("timeline", [])), json.dumps(r5.get("sources")))

T.finish()
