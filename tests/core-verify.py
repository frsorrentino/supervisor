#!/usr/bin/env python3
"""Verifica cm-core.py: il cuore condiviso fra il relay del polso e gli avvisi.

Sono le funzioni PURE (niente rete, niente tmux): la lettura del transcript di Claude Code
(CO1-CO2), il tool in corso e la sua nota (CO3), e il testo degli avvisi (CO4-CO6). Nasce il
16/09/2026 dalle parti ancora vive di bot-ui-verify, quando il bot interattivo di Telegram e
cm-bot-ui.py sono stati ritirati.
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

spec = importlib.util.spec_from_file_location("cm_core", T.SCRIPTS / "cm-core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

# CO1-CO2 transcript di Claude Code
tp = Path(tempfile.mkdtemp(prefix="cm-core-")) / "t.jsonl"
with open(tp, "w") as f:
    f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "fai X"}}) + "\n")
    f.write("riga rotta\n")
    f.write(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Cerco il codice…"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "git log --oneline", "description": "Storia"}}]}}) + "\n")
    f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "..."}]}}) + "\n")
ev, off = core.transcript_events(str(tp), 0)
T.check("CO1 transcript events: user text, assistant text, tool_use (name + input); tool_result and broken lines ignored; offset = file size",
        [e[:2] for e in ev] == [("user", "fai X"), ("text", "Cerco il codice…"), ("tool", "Bash")] and off == tp.stat().st_size, str(ev))
ev2, off2 = core.transcript_events(str(tp), off)
T.check("CO2 from the offset: nothing new; a missing file gives ([], offset)",
        ev2 == [] and off2 == off and core.transcript_events(str(tp) + ".nope", 5) == ([], 5), str(ev2))

# CO3 tool in corso e nota
T.check("CO3 tool_line: tool and input whole, no cut mark; file_path for Read; plain text when no known key",
        core.tool_line("Bash", {"command": "git log --oneline"}) == "Bash git log --oneline"
        and core.tool_line("Read", {"file_path": "/a/b.py"}) == "Read /a/b.py"
        and core.tool_line("X", "y z") == "X y z", core.tool_line("Bash", {"command": "git log --oneline"}))
T.check("CO3 tool_note: the Bash description on one line, 120 chars; empty without it",
        core.tool_note({"command": "x", "description": "  Run  the suite\n"}) == "Run the suite"
        and core.tool_note({"command": "x"}) == "" and core.tool_note("x") == "", core.tool_note({"description": " a  b "}))

# CO4-CO6 testo degli avvisi
T.check("CO4 line: spaces normalized, no wrap and no cut (the bubble is as wide as its longest line)",
        core.line("  a   b\nc ") == "a b c", core.line("  a   b\nc "))
T.check("CO4 join: short lines joined with « · », empty parts dropped",
        core.join("a", "", "b") == "a · b" and core.join("", "") == "", core.join("a", "", "b"))
T.check("CO5 short_name: the account prefix stripped (longest first), cut at 14",
        core.short_name("work-ledger-api", ["work-"]) == "ledger-api"
        and core.short_name("a" * 20) == "a" * 14, core.short_name("work-ledger-api", ["work-"]))
long_q = "Il PC non accetta connessioni in entrata e l'orologio non ha un client. Da dove passano i comandi?"
T.check("CO6 question_gist: whole when it fits; else the last interrogative sentence that fits; else empty",
        core.question_gist("Vuoi A o B?") == "Vuoi A o B?"
        and core.question_gist(long_q) == "Da dove passano i comandi?"
        and core.question_gist("x" * 200) == "", core.question_gist(long_q))
# CO7 modello, effort e contesto dalla trascrizione (contratto 1.11)
proj = Path(tempfile.mkdtemp(prefix="cm-core-run-"))
conf = proj / "conf"
cwd = proj / "lavoro"
cwd.mkdir(parents=True)
import re as _re
slug = _re.sub(r"[^A-Za-z0-9]", "-", str(cwd.resolve()))
tdir = conf / "projects" / slug
tdir.mkdir(parents=True)
tpath = tdir / "S-1.jsonl"


def scrivi(model_full, model_turn, effort, tokens):
    with open(tpath, "w") as f:
        f.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {
            "modelId": model_full, "marketingName": "Opus 5 (1M context)" if "[1m]" in model_full else "Sonnet 5"}}}) + "\n")
        f.write(json.dumps({"type": "assistant", "effort": effort, "message": {"model": model_turn, "usage": {
            "input_tokens": tokens[0], "cache_read_input_tokens": tokens[1], "cache_creation_input_tokens": tokens[2]}}}) + "\n")


core._CFG = {"accounts": {"personal": {"config_dir": str(conf)}}, "state_dir": str(proj), "relay": {"dir": str(proj / "relay")}, "bot": {}}
row = {"session_id": "S-1", "cwd": str(cwd), "account": "personal"}
scrivi("claude-opus-5[1m]", "claude-opus-5", "high", (100, 299_900, 0))
rt = core.session_runtime(row)
T.check("CO7 model with its window and label, effort, context over the 1M window (300k tokens → 30 %)",
        rt["model"] == {"id": "claude-opus-5[1m]", "label": "Opus 5"} and rt["effort"] == "high" and rt["context"] == 30, str(rt))
# Opus 5.5 (2.1.280, 22/09/2026): voce di sistema e turno come nel transcript vero di quel giorno
with open(tpath, "w") as f:
    f.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {
        "modelId": "claude-opus-5-5[1m]", "marketingName": "Opus 5.5 (1M context)"}}}) + "\n")
    f.write(json.dumps({"type": "assistant", "effort": "xhigh", "message": {"model": "claude-opus-5-5", "usage": {
        "input_tokens": 100, "cache_read_input_tokens": 299_900, "cache_creation_input_tokens": 0}}}) + "\n")
rt = core.session_runtime(row)
T.check("CO7 Opus 5.5: claude-opus-5-5[1m] is read as the 1M window (300k tokens → 30 %), label «Opus 5.5»",
        rt["model"] == {"id": "claude-opus-5-5[1m]", "label": "Opus 5.5"} and rt["effort"] == "xhigh" and rt["context"] == 30, str(rt))
scrivi("claude-sonnet-5", "claude-sonnet-5", "medium", (0, 40_000, 10_000))
rt = core.session_runtime(row)
T.check("CO7 standard window: 50k of 200k → 25 %; the label comes from the system entry",
        rt["model"] == {"id": "claude-sonnet-5", "label": "Sonnet 5"} and rt["effort"] == "medium" and rt["context"] == 25, str(rt))
scrivi("claude-opus-5[1m]", "claude-sonnet-5", "high", (0, 100_000, 0))
rt = core.session_runtime(row)
T.check("CO7 the model changed mid-session (the two entries disagree): the id is kept, the window is not, so context is absent — never estimated",
        rt["model"] == {"id": "claude-sonnet-5", "label": None} and rt["context"] is None and rt["effort"] == "high", str(rt))
# CO8 (1.11.1, 16/09, dall'app: dal vivo label e context erano sempre nulli): la voce di sistema col modello sta
# all'AVVIO della conversazione, quindi fuori dalla coda in una sessione lunga. Si deve leggere anche la testa.
lungo = tdir / "S-2.jsonl"
with open(lungo, "w") as f:
    riempi = json.dumps({"type": "user", "message": {"role": "user", "content": "x" * 500}})
    # come dal vivo: la voce del modello non e' la prima riga — viene dopo prompt di sistema, memo e hook
    for _ in range(600):   # ~300 KB prima della voce, come nella sessione vera (221 KB)
        f.write(riempi + "\n")
    f.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {
        "modelId": "claude-opus-5[1m]", "marketingName": "Opus 5 (1M context)"}}}) + "\n")
    for _ in range(3000):   # oltre 1,5 MB dopo: la voce finisce fuori dalla coda letta
        f.write(riempi + "\n")
    f.write(json.dumps({"type": "assistant", "effort": "high", "message": {"model": "claude-opus-5", "usage": {
        "input_tokens": 0, "cache_read_input_tokens": 500_000, "cache_creation_input_tokens": 0}}}) + "\n")
rt = core.session_runtime({"session_id": "S-2", "cwd": str(cwd), "account": "personal"})
T.check("CO8 long transcript, the entry 300 KB in (not the first line, as live): read from the head, label and the 1M window survive (500k → 50 %)",
        rt["model"] == {"id": "claude-opus-5[1m]", "label": "Opus 5"} and rt["context"] == 50 and rt["effort"] == "high", str(rt))
# e se il modello cambia a meta', la voce nuova sta nella CODA e deve vincere su quella della testa
with open(lungo, "a") as f:
    f.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {
        "modelId": "claude-sonnet-5", "marketingName": "Sonnet 5"}}}) + "\n")
    f.write(json.dumps({"type": "assistant", "effort": "medium", "message": {"model": "claude-sonnet-5", "usage": {
        "input_tokens": 0, "cache_read_input_tokens": 50_000, "cache_creation_input_tokens": 0}}}) + "\n")
rt = core.session_runtime({"session_id": "S-2", "cwd": str(cwd), "account": "personal"})
T.check("CO8 the model changed mid-session: the entry in the tail wins over the one in the head (200k window → 25 %)",
        rt["model"] == {"id": "claude-sonnet-5", "label": "Sonnet 5"} and rt["context"] == 25, str(rt))
# CO9 (1.12, 16/09): un cambio dal selettore vale subito nello stato, finche' la sessione non scrive un turno piu'
# recente dell'annotazione; poi vince di nuovo la trascrizione
import datetime as _dt9
t0 = 1789500000.0
with open(tdir / "S-3.jsonl", "w") as f:
    f.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {"modelId": "claude-opus-5[1m]", "marketingName": "Opus 5 (1M context)"}}}) + "\n")
    f.write(json.dumps({"type": "assistant", "effort": "high", "timestamp": _dt9.datetime.fromtimestamp(t0, _dt9.timezone.utc).isoformat().replace("+00:00", "Z"),
                        "message": {"model": "claude-opus-5", "usage": {"input_tokens": 0, "cache_read_input_tokens": 400_000, "cache_creation_input_tokens": 0}}}) + "\n")
tuned = proj / "tuned.json"
core._CFG["tune"] = {"file": str(tuned)}
row3 = {"session_id": "S-3", "cwd": str(cwd), "account": "personal"}
tuned.write_text(json.dumps({"S-3": {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "low", "at": t0 + 30}}))
rt = core.session_runtime(row3)
T.check("CO9 a change made from the picker after the last turn wins: new model and effort, context null (the old window is not the new model's)",
        rt == {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "low", "context": None}, str(rt))
tuned.write_text(json.dumps({"S-3": {"effort": "low", "at": t0 + 30}}))
rt = core.session_runtime(row3)
T.check("CO9 an effort-only change keeps the model and its context", rt == {"model": {"id": "claude-opus-5[1m]", "label": "Opus 5"}, "effort": "low", "context": 40}, str(rt))
tuned.write_text(json.dumps({"S-3": {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "low", "at": t0 - 30}}))
rt = core.session_runtime(row3)
T.check("CO9 a turn newer than the note: the transcript wins again", rt == {"model": {"id": "claude-opus-5[1m]", "label": "Opus 5"}, "effort": "high", "context": 40}, str(rt))
core._CFG.pop("tune", None)
# CO10 (1.13.1, 17/09, dall'app: master su Fable 5.1 al 100 % col terminale al 59 %): l'id di Fable non dice la finestra
def fable(tokens, sid="S-4"):
    with open(tdir / f"{sid}.jsonl", "w") as f:
        f.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {"modelId": "claude-fable-5-1", "marketingName": "Fable 5.1"}}}) + "\n")
        f.write(json.dumps({"type": "assistant", "effort": "high", "message": {"model": "claude-fable-5-1", "usage": {"input_tokens": 2, "cache_read_input_tokens": tokens - 2, "cache_creation_input_tokens": 0}}}) + "\n")
    return core.session_runtime({"session_id": sid, "cwd": str(cwd), "account": "personal"})
hints = proj / "hints"; hints.mkdir()
core._CFG["relay"] = {"dir": str(proj / "relay"), "window_unmarked": ["claude-fable-5-1"], "window_hint_dir": str(hints)}
rt = fable(585_566)
T.check("CO10 more tokens than the standard window and no [1m] in the id → the window is 1M (585k → 59 %, not 100)", rt["context"] == 59 and rt["model"] == {"id": "claude-fable-5-1", "label": "Fable 5.1"}, str(rt))
rt = fable(120_000)
T.check("CO10 an id that does not tell its window, under 200k tokens and no hint → context null, never a guess", rt["context"] is None and rt["effort"] == "high", str(rt))
(hints / "S-4.json").write_text(json.dumps({"session_id": "S-4", "model": "claude-fable-5-1", "ctx_size": 1000000}))
rt = fable(120_000)
T.check("CO10 the window Claude Code declared to the statusline, saved per session → 120k of 1M = 12 %", rt["context"] == 12, str(rt))
(hints / "S-4.json").write_text(json.dumps({"session_id": "S-4", "model": "claude-opus-5", "ctx_size": 1000000}))
rt = fable(120_000)
T.check("CO10 a hint taken under another model is not used", rt["context"] is None, str(rt))
core._CFG["relay"] = {"dir": str(proj / "relay")}
# CO11 (17/09, dal vivo): un turno «<synthetic>» in coda (errore dell'API) non e' il modello della sessione
scrivi("claude-sonnet-5", "claude-sonnet-5", "medium", (0, 40_000, 10_000))
with open(tpath, "a") as f:
    f.write(json.dumps({"type": "assistant", "message": {"model": "<synthetic>", "usage": {"input_tokens": 0}}}) + "\n")
rt = core.session_runtime(row)
T.check("CO11 a trailing «<synthetic>» turn is skipped: the last real turn gives model, effort and context",
        rt == {"model": {"id": "claude-sonnet-5", "label": "Sonnet 5"}, "effort": "medium", "context": 25}, str(rt))
# CO15 (16/10/2026, Claude Code 2.1.299): la compattazione a sessione ferma riassume senza un turno nuovo. Le righe come
# nella trascrizione della master del 14/10 alle 23:07: compact_boundary, riassunto, allegati (anche il modello), nessun
# turno dell'assistente; il contesto e' compactMetadata.postTokens sulla finestra provata da preTokens
core._CFG["relay"] = {"dir": str(proj / "relay"), "window_unmarked": ["claude-fable-5-1"]}


def compattata(meta, after=()):
    with open(tdir / "S-15.jsonl", "w") as f:
        f.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {"modelId": "claude-fable-5-1", "marketingName": "Fable 5.1"}}}) + "\n")
        f.write(json.dumps({"type": "assistant", "effort": "xhigh", "timestamp": "2026-10-14T22:12:00.000Z", "message": {"model": "claude-fable-5-1", "usage": {
            "input_tokens": 13, "cache_read_input_tokens": 915_000, "cache_creation_input_tokens": 600}}}) + "\n")
        f.write(json.dumps({"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted", "timestamp": "2026-10-14T23:07:08.730Z",
                            "compactMetadata": meta}) + "\n")
        f.write(json.dumps({"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": "This session is being continued…"}}) + "\n")
        f.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {"modelId": "claude-fable-5-1", "marketingName": "Fable 5.1"}}}) + "\n")
        for line in after:
            f.write(json.dumps(line) + "\n")
    return core.session_runtime({"session_id": "S-15", "cwd": str(cwd), "account": "personal"})


rt = compattata({"trigger": "auto", "preTokens": 915_613, "durationMs": 65373, "postTokens": 52_000})
T.check("CO15 compacted after the last turn: postTokens on the 1M window that preTokens proves (52k → 5 %, not 92 %)",
        rt == {"model": {"id": "claude-fable-5-1", "label": "Fable 5.1"}, "effort": "xhigh", "context": 5}, str(rt))
rt = compattata({"trigger": "auto", "preTokens": 915_613})
T.check("CO15 a boundary without postTokens (older CLIs): context null, never the number from before", rt["context"] is None and rt["model"]["id"] == "claude-fable-5-1", str(rt))
rt = compattata({"trigger": "auto", "preTokens": 915_613, "postTokens": 52_000}, after=[{"type": "assistant", "effort": "xhigh", "message": {
    "model": "claude-fable-5-1", "usage": {"input_tokens": 10, "cache_read_input_tokens": 300_000, "cache_creation_input_tokens": 0}}}])
T.check("CO15 a turn after the boundary: that turn counts again (300k → 30 %)", rt["context"] == 30, str(rt))
# CO16 (16/10/2026): Haiku 5.5 ha 1M senza [1m] nell'id: relay.window_1m, nei default
core._CFG["relay"] = {"dir": str(proj / "relay"), "window_1m": ["claude-haiku-5-5"]}
with open(tdir / "S-16.jsonl", "w") as f:
    f.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {"modelId": "claude-haiku-5-5", "marketingName": "Haiku 5.5"}}}) + "\n")
    f.write(json.dumps({"type": "assistant", "effort": "medium", "message": {"model": "claude-haiku-5-5", "usage": {
        "input_tokens": 0, "cache_read_input_tokens": 120_000, "cache_creation_input_tokens": 0}}}) + "\n")
rt = core.session_runtime({"session_id": "S-16", "cwd": str(cwd), "account": "personal"})
T.check("CO16 Haiku 5.5 under 200k tokens: the 1M window from relay.window_1m (120k → 12 %, not 60 %), label «Haiku 5.5»",
        rt == {"model": {"id": "claude-haiku-5-5", "label": "Haiku 5.5"}, "effort": "medium", "context": 12}, str(rt))
T.check("CO16 the defaults: claude-haiku-5-5 in relay.window_1m; Sonnet 5.5 and Haiku 5.5 in tune.models",
        "claude-haiku-5-5" in core.cm.DEFAULTS["relay"]["window_1m"]
        and [m["id"] for m in core.cm.DEFAULTS["tune"]["models"]][-2:] == ["claude-sonnet-5-5", "claude-haiku-5-5"], str(core.cm.DEFAULTS["tune"]["models"]))
core._CFG["relay"] = {"dir": str(proj / "relay")}
# CO12 (22/09/2026, Opus 5.5): il ripiego dopo un messaggio segnalato. Le righe come le scrive Claude Code 2.1.280
# (schema letto nel binario; nessun messaggio segnalato provocato per vederle)
row9 = {"session_id": "S-9", "cwd": str(cwd), "account": "personal"}
t9 = tdir / "S-9.jsonl"


def turno(model, ts="2026-09-22T20:00:00.000Z"):
    return json.dumps({"type": "assistant", "timestamp": ts, "message": {"model": model, "usage": {"input_tokens": 10}}}, separators=(",", ":"))


def ripiego(ts="2026-09-22T19:59:00.000Z", **kw):
    d = {"parentUuid": "p", "isSidechain": False, "type": "system", "subtype": "model_refusal_fallback",
         "content": "Opus 5.5's safeguards flagged this message.", "level": "warning", "trigger": "refusal", "direction": "retry",
         "scope": "session", "originalModel": "claude-opus-5-5", "fallbackModel": "claude-opus-5", "requestId": "req_1",
         "apiRefusalCategory": "cyber", "timestamp": ts}
    d.update(kw)
    return json.dumps({k: v for k, v in d.items() if v is not None}, separators=(",", ":"))


def fb(*lines):
    t9.write_text("\n".join(lines) + "\n")
    return core.model_fallback(row9)


core._CFG.setdefault("tune", {})["file"] = ""
T.check("CO12 a session-scope fallback and the last turn on the fallback model → from, to, category, at",
        fb(turno("claude-opus-5-5"), ripiego(), turno("claude-opus-5")) == {"from": "claude-opus-5-5", "to": "claude-opus-5", "category": "cyber", "at": 1790107140},
        str(fb(turno("claude-opus-5-5"), ripiego(), turno("claude-opus-5"))))
T.check("CO12 scope absent (older CLIs) counts as session", fb(ripiego(scope=None), turno("claude-opus-5")) is not None, "")
T.check("CO12 scope local (a subagent, /btw, a fork) → the session model is unchanged: None",
        fb(turno("claude-opus-5-5"), ripiego(scope="local", fallbackModel="claude-opus-5"), turno("claude-opus-5-5")) is None, "")
T.check("CO12 a local fallback after a session one does not hide it",
        fb(ripiego(), ripiego(scope="local", ts="2026-09-22T20:01:00.000Z"), turno("claude-opus-5")) is not None, "")
T.check("CO12 back on the original model (/model or supervisor model) → None", fb(ripiego(), turno("claude-opus-5"), turno("claude-opus-5-5")) is None, "")
T.check("CO12 direction revert → None", fb(ripiego(direction="revert"), turno("claude-opus-5")) is None, "")
T.check("CO12 a trailing «<synthetic>» turn is skipped", fb(ripiego(), turno("claude-opus-5"), turno("<synthetic>")) is not None, "")
riempi9 = json.dumps({"type": "user", "message": {"content": "x" * 900}})
T.check("CO12 the flagged message hours ago, far before the 512 KB tail: still found",
        fb(ripiego(), *([riempi9] * 800), turno("claude-opus-5")) is not None, str(t9.stat().st_size))
T.check("CO12 no fallback line, empty transcript, no transcript → None",
        fb(turno("claude-opus-5")) is None and (t9.write_text("") or core.model_fallback(row9)) is None
        and core.model_fallback({"session_id": "nope", "cwd": str(cwd), "account": "personal"}) is None, "")
tuned9 = proj / "tuned9.json"
tuned9.write_text(json.dumps({"S-9": {"at": 1790107200, "model": {"id": "claude-opus-5-5[1m]", "label": "Opus 5.5"}}}))
core._CFG["tune"]["file"] = str(tuned9)
T.check("CO12 supervisor model back after the fallback, no new turn yet → None", fb(ripiego(), turno("claude-opus-5")) is None, "")
# CO14 (afdc2b, 14/10): la ricerca della riga di ripiego legge solo la parte aggiunta dall'ultima volta
scan = Path(core.cm.expand(core._cfg()["state_dir"])) / "fallback-scan.json"
core._CFG["tune"]["file"] = ""
fb(turno("claude-opus-5-5"))
cached = json.loads(scan.read_text()).get(os.path.realpath(str(t9))) or {}
with open(t9, "a") as f:
    f.write(ripiego() + "\n" + turno("claude-opus-5") + "\n")
grown = core.model_fallback(row9)
cached2 = json.loads(scan.read_text()).get(os.path.realpath(str(t9))) or {}
T.check("CO14 the scan is cached by size; after an append only the new part is read and the new fallback line is found",
        cached.get("marks") == [] and cached.get("size") == len(turno("claude-opus-5-5")) + 1 and grown and grown["to"] == "claude-opus-5"
        and len(cached2.get("marks") or []) == 1 and cached2.get("size") == t9.stat().st_size, str(cached) + str(cached2) + str(grown))
T.check("CO14 a file rewritten (not only grown) is read again from the start: the old marks do not survive",
        fb(turno("claude-opus-5-5"), turno("claude-opus-5")) is None and (json.loads(scan.read_text()).get(os.path.realpath(str(t9))) or {}).get("marks") == [], scan.read_text()[:300])
core._CFG["tune"]["file"] = ""
T.check("CO7 no transcript → the three fields are absent, without an error",
        core.session_runtime({"session_id": "nope", "cwd": str(cwd), "account": "personal"}) == {"model": None, "effort": None, "context": None}, "")

# CO13 (difetto afdc2b, 14/10): un task programmato (CronCreate) entra in coda, esce e arriva come riga user isMeta.
# La voce restava queued per sempre: l'app ancora la chat alla voce prima della prima queued e chiedeva sempre le
# stesse voci, e le risposte della master delle 13:33 e delle 16:10 non arrivavano mai al telefono.
tq = Path(tempfile.mkdtemp(prefix="cm-core-")) / "q.jsonl"


def qline(**d):
    return json.dumps(d) + "\n"


with open(tq, "w") as f:
    f.write(qline(type="queue-operation", operation="enqueue", timestamp="2026-10-14T11:33:11.731Z", content="[Post su X] segui la ricetta"))
    f.write(qline(type="queue-operation", operation="dequeue", timestamp="2026-10-14T11:33:11.950Z"))
    f.write(qline(type="user", isMeta=True, uuid="m1", timestamp="2026-10-14T11:33:12.336Z", message={"role": "user", "content": "[Post su X] segui la ricetta"}))
    f.write(qline(type="assistant", uuid="a1", timestamp="2026-10-14T11:33:20.000Z", message={"content": [{"type": "text", "text": "Post pronto."}]}))
    f.write(qline(type="system", subtype="turn_duration", timestamp="2026-10-14T11:33:21.000Z", durationMs=9000))
    # tolto dalla coda (consegnato in un altro modo o cancellato) e mai rivisto: finito il turno non e' piu' in coda
    f.write(qline(type="queue-operation", operation="enqueue", timestamp="2026-10-14T11:34:00.000Z", content="altro messaggio"))
    f.write(qline(type="queue-operation", operation="remove", timestamp="2026-10-14T11:34:01.000Z"))
    f.write(qline(type="assistant", uuid="a2", timestamp="2026-10-14T11:34:05.000Z", message={"content": [{"type": "text", "text": "Risposta delle 13:34."}]}))
    f.write(qline(type="system", subtype="turn_duration", timestamp="2026-10-14T11:34:06.000Z", durationMs=5000))
    # ancora davvero in coda: resta queued
    f.write(qline(type="queue-operation", operation="enqueue", timestamp="2026-10-14T11:35:00.000Z", content="ancora in coda"))
eq = core.transcript_entries(str(tq), 0)
qs = {e["text"]: e["queued"] for e in eq if e["role"] == "user"}
T.check("CO13 a scheduled task (enqueue, dequeue, isMeta user line) stops being queued; one taken off the queue stops being queued when the turn ends; one still in the queue stays queued; no duplicate",
        qs == {"[Post su X] segui la ricetta": False, "altro messaggio": False, "ancora in coda": True}
        and sum(1 for e in eq if e["role"] == "user") == 3, str([(e["id"], e["role"], e["queued"], e["text"]) for e in eq]))

# CO14 (contratto 1.72, 15/10): il diff dei passaggi di modifica nelle voci della chat, dal toolUseResult della riga del
# risultato; una riga con due risultati non dice di chi e' il toolUseResult
t14 = Path(tempfile.mkdtemp(prefix="cm-core-")) / "d.jsonl"
SP14 = [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1, "lines": ["-a", "+b"]}]
with open(t14, "w") as f:
    f.write(qline(type="assistant", uuid="w1", timestamp="2026-10-15T10:00:00.000Z", cwd="/r", message={"content": [
        {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": "new.md", "content": "uno\ndue\n"}}]}))
    f.write(qline(type="user", uuid="r1", timestamp="2026-10-15T10:00:01.000Z", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
                  toolUseResult={"type": "create", "filePath": "/r/new.md", "content": "uno\ndue\n", "structuredPatch": []}))
    f.write(qline(type="assistant", uuid="w2", timestamp="2026-10-15T10:00:02.000Z", cwd="/r", message={"content": [
        {"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "/r/a.txt", "old_string": "a", "new_string": "b"}},
        {"type": "tool_use", "id": "t3", "name": "Edit", "input": {"file_path": "/r/c.txt", "old_string": "a", "new_string": "b"}}]}))
    f.write(qline(type="user", uuid="r2", timestamp="2026-10-15T10:00:03.000Z", message={"content": [
        {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}, {"type": "tool_result", "tool_use_id": "t3", "content": "ok"}]},
        toolUseResult={"filePath": "/r/a.txt", "structuredPatch": SP14}))
    f.write(qline(type="assistant", uuid="w4", timestamp="2026-10-15T10:00:04.000Z", cwd="/r", message={"content": [
        {"type": "tool_use", "id": "t4", "name": "Edit", "input": {"file_path": "/r/a.txt", "old_string": "zz", "new_string": "b"}}]}))
    f.write(qline(type="user", uuid="r4", timestamp="2026-10-15T10:00:05.000Z", message={"content": [
        {"type": "tool_result", "tool_use_id": "t4", "is_error": True, "content": "String to replace not found"}]}, toolUseResult="Error: String to replace not found"))
e14 = {e["id"]: e for e in core.transcript_entries(str(t14), 0, diff_cap=(200, 16000))}
plain14 = core.transcript_entries(str(t14), 0)
T.check("CO14 (1.72) with diff_cap a Write of a new file gets diff (create, the path from the result); a line carrying two results gives none; a failed Edit none; without diff_cap every entry has diff null, as its last key",
        e14["w1.0"]["diff"] == {"path": "/r/new.md", "kind": "create", "added": 2, "removed": 0, "cut": False,
                                "hunks": [{"old_start": 0, "old_lines": 0, "new_start": 1, "new_lines": 2, "lines": ["+uno", "+due"]}]}
        and e14["w2.0"]["diff"] is None and e14["w2.1"]["diff"] is None and e14["w4.0"]["diff"] is None and e14["w4.0"]["error"] is True
        and all(e["diff"] is None for e in plain14) and list(plain14[0])[-1] == "diff", json.dumps(e14["w1.0"]["diff"]))
# CO14b (17/10): changes_op fuori da git legge solo le scritture — transcript_diffs da' gli stessi diff delle voci
d14 = core.transcript_diffs(str(t14), (200, 16000))
real14 = sorted(Path.home().glob(".claude/projects/*/*.jsonl"), key=lambda p: p.stat().st_mtime)[-3:]
same_real = all(core.transcript_diffs(str(p), (200, 16000)) == [e["diff"] for e in core.transcript_entries(str(p), 0, diff_cap=(200, 16000)) if e.get("diff")] for p in real14)
T.check("CO14b transcript_diffs = the non-null diffs of transcript_entries with the same cap (the fixture, and the newest real transcripts if any)",
        d14 == [e14["w1.0"]["diff"]] and same_real, json.dumps(d14)[:300])
# CO15 (16/10, relay al 50% di CPU): il ledger si legge in modo incrementale — il demone teneva 50 MB e 132k righe
# riparsate a ogni push. Solo i byte nuovi; le righe watch-cmd (il registro dei comandi del relay, nessuno stato le
# legge) non si decodificano; una riga a meta' aspetta il suo «\n»; un file riscritto o sostituito si rilegge da capo.
_ld = Path(tempfile.mkdtemp(prefix="cm-core-ledger-"))
_cfg15 = core._CFG
core._CFG = dict(_cfg15 or {}, state_dir=str(_ld))
_lp = _ld / "ledger.jsonl"
_lp.write_text(json.dumps({"event": "start", "session_id": "A"}) + "\n" + json.dumps({"event": "watch-cmd", "op": "transcript"}) + "\n"
               + "rotta\n" + json.dumps({"event": "stop", "session_id": "A"}) + "\n" + '{"event": "prompt", "sess')
r1 = [(x["event"], x["session_id"]) for x in core.ledger_rows()]
_loads, _n15 = core.json.loads, []
with open(_lp, "a") as f:
    f.write('ion_id": "A"}\n' + json.dumps({"event": "watch-cmd", "op": "agenda"}) + "\n" + json.dumps({"event": "stop", "session_id": "B"}) + "\n")
core.json.loads = lambda raw, *a, **k: (_n15.append(raw), _loads(raw, *a, **k))[1]
try:
    r2 = [(x["event"], x["session_id"]) for x in core.ledger_rows()]
finally:
    core.json.loads = _loads
T.check("CO15 ledger_rows: watch-cmd and broken lines left out, a half-written last line waits; the next call decodes only the new lines (2) and returns all",
        r1 == [("start", "A"), ("stop", "A")] and r2 == r1 + [("prompt", "A"), ("stop", "B")] and len(_n15) == 2, str([r1, r2, len(_n15)]))
_lp.write_text(json.dumps({"event": "stop", "session_id": "Z"}) + "\n" + json.dumps({"event": "prompt", "session_id": "Z", "pad": "x" * 400}) + "\n")
r3 = [(x["event"], x["session_id"]) for x in core.ledger_rows()]
_lp2 = _ld / "new.jsonl"
_lp2.write_text(json.dumps({"event": "start", "session_id": "N"}) + "\n")
os.replace(_lp2, _lp)
r4 = [(x["event"], x["session_id"]) for x in core.ledger_rows()]
_l4 = core.ledger_rows()
_l4.append({"event": "x"})
r5 = len(core.ledger_rows())
core._CFG = _cfg15
T.check("CO15 a ledger rewritten in place (longer) or replaced by another file is read from the start; the list returned is a copy",
        r3 == [("stop", "Z"), ("prompt", "Z")] and r4 == [("start", "N")] and r5 == 1, str([r3, r4, r5]))


# CO16 (contratto 1.73, 18/10): un prompt con scope «studio» porta dopo il prefisso il blocco [studio]…[/studio] del
# relay; la voce della chat e la timeline lo tolgono, come le istruzioni vocali: resta il testo mandato
_pre16, _org16 = core._prompt_prefixes()[0]
_blk16 = "[studio]\nPratiche:\n- Harbor lease dispute → ~/ws/personal/harbor-lease\n[/studio]"
_v16 = core._voice_texts()[0]
T.check("CO16 (1.73) prefix + [studio] block + blank line + text → the text and the origin; with the voice instructions before the block too; "
        "an unclosed block and a block typed on the PC stay as they are",
        core._origin_of(f"{_pre16} {_blk16}\n\nwhat is due this week?") == ("what is due this week?", _org16)
        and core._origin_of(f"{_pre16} {_v16} {_blk16}\n\nwhat is due?") == ("what is due?", _org16)
        and core._origin_of(f"{_pre16} [studio]\nno end") == ("[studio]\nno end", _org16)
        and core._origin_of(f"{_blk16}\n\nhi") == (f"{_blk16}\n\nhi", "pc"), str(core._origin_of(f"{_pre16} {_blk16}\n\nwhat is due this week?")))

# CO-N (22/10/2026, relay-filtro-api-request): i record api-request della trascrizione (dalla 2.1.300) si scartano dal
# prefisso di riga, prima di json.loads, anche se dentro hanno «"type":"user"», «Bash», «Esito» e un timestamp
T.check("CO-N noise(): api-request and api-request-blob lines, bytes or str; a user line that mentions api-request in its text is not noise",
        core.noise(b'{"type":"api-request","sessionId":"x"}') and core.noise('{"type": "api-request-blob","x":1}') and core.noise(b'  {"type":"api-request"}')
        and not core.noise(b'{"type":"user","message":{"content":"api-request"}}') and not core.noise(b"") and not core.noise("{}"), "")
_tn = tdir / "S-N.jsonl"
_u = {"type": "user", "uuid": "u-n1", "timestamp": "2026-10-22T01:00:00.000Z", "cwd": str(tdir), "message": {"role": "user", "content": "ciao"}}
_a = {"type": "assistant", "uuid": "a-n1", "timestamp": "2026-10-22T01:00:05.000Z", "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "Esito: fatto"}]}}
_api = {"type": "api-request", "timestamp": "2026-10-22T01:30:00.000Z", "request": {"messages": [dict(_u, uuid="u-fake"), {"type": "user", "content": [{"type": "tool_result", "content": "Esito: finto"}]}],
        "tools": [{"name": "Bash", "input": {"command": "rm -rf /"}}]}}
_tn.write_text("\n".join(json.dumps(x) for x in (_u, _a)) + "\n")
_ev1, _ = core.transcript_events(_tn, 0)
_tn.write_text("\n".join(json.dumps(x) for x in (_api, _u, _api, _a, _api)) + "\n")
_ev2, _ = core.transcript_events(_tn, 0)
T.check("CO-N transcript_events: three api-request lines around the same user and assistant lines add no event", _ev1 == _ev2 and len(_ev1) >= 1, str((_ev1, _ev2))[:300])

T.finish()
