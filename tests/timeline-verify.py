#!/usr/bin/env python3
"""Cronologia delle sessioni (fase 2, 03/10/2026): prompt, test, commit, esiti e compiti in ordine di tempo, dal
codice. Trascrizioni sintetiche in una HOME finta, un repo git vero, `sessions` dalla supervisor finta."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="cm-timeline-"))
conf = tmp / ".claude"
proj = tmp / "ws" / "atlas"
closed = tmp / "ws" / "orbit"
for d in (proj, closed):
    d.mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "default_account": "personal", "accounts": {"personal": {"config_dir": str(conf), "tmux_prefix": "w-"}}}))
fake = tmp / "fake"
fake.mkdir()
ENV = dict(os.environ, CC_SUPERVISOR_CONFIG=str(cfg), CM_BIN=str(Path(__file__).resolve().parent / "lib" / "fake-cm-plan.py"),
           FAKE_CM_DIR=str(fake), CM_TASKS_DB=str(tmp / "tasks.db"), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
NOW = int(time.time())
PFX = json.loads((T.PLUGIN / "messages" / "it.json").read_text())["relay.prompt_prefix_phone"]
PFX_WEB = json.loads((T.PLUGIN / "messages" / "it.json").read_text())["relay.prompt_prefix_web"]
HANDOFF = json.loads((T.PLUGIN / "messages" / "it.json").read_text()).get("relay.prompt_handoff", "")   # 10/10: non c'era prima
VOICE = json.loads((T.PLUGIN / "messages" / "it.json").read_text()).get("relay.prompt_voice", "Dall'utente a voce, in modalità live:")


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def slug(p):
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(p)))


def line(kind, uid, t, content, cwd, **kw):
    d = {"type": kind, "uuid": uid, "timestamp": iso(t), "isSidechain": False, "cwd": str(cwd), "message": {"role": kind, "content": content}}
    if kind == "user" and "origin" not in kw:
        d["origin"] = {"kind": "human"}
    d.update(kw)
    return json.dumps(d, separators=(",", ":"))


def bash(uid, t, tid, cmd):
    return line("assistant", uid, t, [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}], proj)


def result(uid, t, tid, text, err=False):
    return line("user", uid, t, [{"type": "tool_result", "tool_use_id": tid, "is_error": err, "content": text}], proj, origin=None)


L = [
    line("user", "u0", NOW - 9 * 3600, "too old to show", proj),
    line("user", "u1", NOW - 3000, "aggiungi il campo IVA al checkout", proj),
    bash("a1", NOW - 2900, "t1", "cd x && python3 tests/checkout-verify.py 2>&1 | tail -3"),
    result("r1", NOW - 2890, "t1", "  OK  C1 vat\n\n12/12 OK\nShell cwd was reset to /x"),
    bash("a2", NOW - 2800, "t2", "python3 tests/cart-verify.py | tail -1"),
    result("r2", NOW - 2790, "t2", "3/4 OK, FAIL: C2 rounding"),
    bash("a3", NOW - 2700, "t3", "pytest -q tests/test_vat.py"),
    result("r3", NOW - 2690, "t3", "1 failed, 4 passed", err=True),
    bash("a4", NOW - 2600, "t4", "grep -n vat tests/checkout-verify.py"),
    bash("a5", NOW - 2500, "t5", "python3 - <<'EOF'\nimport pytest\nEOF"),
    line("assistant", "a6", NOW - 2400, [{"type": "text", "text": "Fatto.\n\nEsito: campo IVA aggiunto, checkout verde"}], proj),
    line("assistant", "a7", NOW - 2300, [{"type": "text", "text": "Esito: subagent says hi"}], proj, isSidechain=True),
    line("user", "p1", NOW - 2200, "x", proj, origin={"kind": "peer", "body": PFX + " controlla anche il carrello", "msg_id": "m1"}),
    line("user", "p2", NOW - 2190, "x", proj, origin={"kind": "peer", "body": PFX + " controlla anche il carrello", "msg_id": "m1"}),
    line("user", "p3", NOW - 2100, "x", proj, origin={"kind": "peer", "body": "another session talking", "msg_id": "m2"}),
    line("user", "p4", NOW - 2050, "x", proj, origin={"kind": "peer", "body": PFX_WEB + " rifai il totale", "msg_id": "m3"}),
    # un prompt dal telefono arrivato prima del rinomino (07/10): il prefisso diceva «claude-master»
    line("user", "p5", NOW - 2020, "x", proj, origin={"kind": "peer", "body": PFX.replace(HANDOFF, "").replace("supervisor", "claude-master") + " chiudo ora", "msg_id": "m4"}),
    # 1.42: un prompt a voce dalla modalità live, con le istruzioni vocali dopo il prefisso
    line("user", "p6", NOW - 2010, "x", proj, origin={"kind": "peer", "body": PFX + " " + VOICE + " quale sessione è vicina al rilascio?", "msg_id": "m5"}),
    # il riassunto di una compattazione: riga `user` senza origin, come nelle trascrizioni vere (07/10)
    line("user", "c1", NOW - 2000, "This session is being continued from a previous conversation that ran out of context.", proj,
         origin=None, isCompactSummary=True, isVisibleInTranscriptOnly=True),
]
d1 = conf / "projects" / slug(proj)
d1.mkdir(parents=True)
(d1 / "S-A.jsonl").write_text("\n".join(L) + "\n")
d2 = conf / "projects" / slug(closed)
d2.mkdir(parents=True)
(d2 / "S-O.jsonl").write_text(line("assistant", "o1", NOW - 1000, [{"type": "text", "text": "Esito: docs aggiornate"}], closed) + "\n")
(d2 / "S-OLD.jsonl").write_text(line("assistant", "o2", NOW - 20 * 3600, [{"type": "text", "text": "Esito: ancient"}], closed) + "\n")
os.utime(d2 / "S-OLD.jsonl", (NOW - 20 * 3600, NOW - 20 * 3600))
(fake / "sessions.json").write_text(json.dumps([{"tmux": "w-atlas", "name": "w-atlas", "cwd": str(proj), "account": "personal", "session_id": "S-A"}]))

# un repo vero: un commit nella finestra, lo stesso soggetto su un altro ramo (la release pubblica), uno vecchio
g = lambda *a, t=None: subprocess.run(["git", "-C", str(proj), *a], capture_output=True, text=True, env=dict(ENV, **({"GIT_COMMITTER_DATE": f"@{t}", "GIT_AUTHOR_DATE": f"@{t}"} if t else {})))  # noqa: E731
g("init", "-q", "-b", "main")
(proj / "a.txt").write_text("1"); g("add", "a.txt"); g("commit", "-qm", "old work", t=NOW - 10 * 3600)
(proj / "a.txt").write_text("2"); g("add", "a.txt"); g("commit", "-qm", "feat: VAT field", t=NOW - 2450)
g("checkout", "-qb", "public", "HEAD~1"); (proj / "b.txt").write_text("x"); g("add", "b.txt"); g("commit", "-qm", "feat: VAT field", t=NOW - 2440)
g("checkout", "-q", "main")

# un esito del registro per quella cartella
task = {"schema_version": 1, "task": {"id": "vat", "title": "IVA nel checkout", "plan": None, "where": {"project": str(proj), "session": None, "host": "local", "account": None},
        "check": {"cmd": "true", "cwd": None, "timeout_s": 10}, "perimeter": [], "lane": "open", "depends_on": [], "route": "session", "hold": False,
        "attempts_max": 3, "data_class": "internal", "requested_by": "test"}}
subprocess.run([str(T.SCRIPTS / "supervisor"), "task", "add", "-"], input=json.dumps(task), capture_output=True, text=True, env=ENV)
subprocess.run([str(T.SCRIPTS / "supervisor"), "task", "done", "vat"], capture_output=True, text=True, env=ENV)


def tl(*a):
    p = subprocess.run([str(T.SCRIPTS / "supervisor"), "timeline", *a], capture_output=True, text=True, env=ENV, timeout=120)
    return p.returncode, p.stdout, p.stderr


rc, out, err = tl("--since", "6h", "--json")
d = json.loads(out) if rc == 0 else {"sessions": []}
by = {s["session"]: s for s in d["sessions"]}
atlas = by.get("atlas", {"events": []})
kinds = [(e["kind"], e["text"]) for e in atlas["events"]]
T.check("TL1 the live session by its short name (prefix w- dropped), live, its folder; the closed one of the window by its folder name; the 20-hour-old one left out",
        rc == 0 and set(by) == {"atlas", "orbit"} and atlas.get("live") is True and atlas.get("project") == str(proj) and by["orbit"]["live"] is False
        and [e["text"] for e in by["orbit"]["events"]] == ["docs aggiornate"], out[:400] + err)
T.check("TL2 in time order: the prompt, three tests, the commit, the outcome, the phone's, the web app's and the voice prompts, the task (closed just now); nothing older than the window",
        [k for k, _ in kinds] == ["prompt", "test", "test", "test", "commit", "outcome", "prompt", "prompt", "prompt", "prompt", "task"] and atlas["events"] == sorted(atlas["events"], key=lambda e: e["at"])
        and not any("too old" in t or "old work" in t for _, t in kinds), str(kinds))
tests = [e for e in atlas["events"] if e["kind"] == "test"]
T.check("TL3 tests named by their suite, with the summary line: 12/12 OK green; «3/4 OK, FAIL» red although the command exited 0; pytest with is_error red",
        [(e["text"], e["ok"], e["ref"]) for e in tests] == [("checkout-verify.py", True, "12/12 OK"), ("cart-verify.py", False, "3/4 OK, FAIL: C2 rounding"),
                                                             ("pytest tests/test_vat.py", False, "1 failed, 4 passed")], str(tests))
T.check("TL4 not a test: a grep that names a -verify.py file, a heredoc that imports pytest", len(tests) == 3, "")
commit = [e for e in atlas["events"] if e["kind"] == "commit"]
T.check("TL5 commits from git log of the folder: the one in the window once (the same subject on another branch within 2 minutes is not repeated), with its hash",
        len(commit) == 1 and commit[0]["text"] == "feat: VAT field" and re.fullmatch(r"[0-9a-f]{7,}", commit[0]["ref"] or ""), str(commit))
outc = [e["text"] for e in atlas["events"] if e["kind"] == "outcome"]
T.check("TL6 the «Esito:» line of the session, not the subagent's", outc == ["campo IVA aggiunto, checkout verde"], str(outc))
prompts = [(e["text"], e["ref"]) for e in atlas["events"] if e["kind"] == "prompt"]
T.check("TL7 prompts: the one typed (ref null), the phone's through the relay once, prefix stripped (ref phone), the web app's (ref web, 1.36), one with the prefix from before the rename (phone), a voice prompt without the voice instructions (1.42, phone); another session's message and a compaction summary left out",
        prompts == [("aggiungi il campo IVA al checkout", None), ("controlla anche il carrello", "phone"), ("rifai il totale", "web"), ("chiudo ora", "phone"), ("quale sessione è vicina al rilascio?", "phone")], str(prompts))
tk = [e for e in atlas["events"] if e["kind"] == "task"]
T.check("TL8 the registry's accepted outcome for the folder", len(tk) == 1 and tk[0]["ok"] is True and tk[0]["ref"] == "vat" and tk[0]["text"].startswith("IVA nel checkout: check green"), str(tk))
rc, txt, _ = tl("atlas", "--since", "6h")
T.check("TL9 as text, one session by name: a header, then «HH:MM kind ✓/✗ text», the summary after the test, the hash after the commit",
        txt.startswith("atlas — ") and "orbit" not in txt and re.search(r"\d\d:\d\d test    ✗ cart-verify\.py — 3/4 OK, FAIL: C2 rounding", txt)
        and re.search(r"commit    feat: VAT field \[[0-9a-f]+\]", txt), txt)
p = subprocess.run([str(T.SCRIPTS / "supervisor"), "task", "board", "--timeline", "--since", "6h", "--json"], capture_output=True, text=True, env=ENV)
b = json.loads(p.stdout) if p.returncode == 0 else {}
T.check("TL10 task board --timeline carries the same timeline under the counts", b.get("counts", {}).get("done") == 1 and {s["session"] for s in b.get("timeline", {}).get("sessions", [])} == {"atlas", "orbit"}, p.stdout[:300] + p.stderr)
T.check("TL11 a bad --since → a plain error", tl("--since", "yesterday")[0] != 0, "")

# la chat del telefono e della web (11/10): il riassunto di una compattazione non e' un messaggio della persona
spec = __import__("importlib.util").util.spec_from_file_location("cm_core", T.SCRIPTS / "cm-core.py")
core = __import__("importlib.util").util.module_from_spec(spec)
spec.loader.exec_module(core)
chat = [e["text"] for e in core.transcript_entries(str(d1 / "S-A.jsonl"), 0, chat_only=True) if e["role"] == "user"]
T.check("TL12 the chat leaves out a compaction summary and keeps the prompt typed",
        "aggiungi il campo IVA al checkout" in chat and not any("being continued" in x for x in chat), str(chat))

T.finish()
