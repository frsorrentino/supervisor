#!/usr/bin/env python3
"""Contratto 1.106 (02/11/2026, compito supervisor-risultati-02-11): i risultati consegnati dalle sessioni (SendUserFile)
per «Da guardare» dell'app. IX indice persistente (scansione incrementale, consegne fallite fuori, sopravvive alla
chiusura), GR stesso lavoro e versioni, ST /state.results, SN result_seen, NO result_note (alla sessione come prompt dal
telefono, in casella se e' chiusa), FI op file sui file dell'indice, VW op results, FX fixture."""
import importlib.util
import json
import os
import re
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
conf = home / ".claude"
ws = tmp / "ws"
film = ws / "film"
site = ws / "sito"
for d in (conf / "projects", film / "out", site, tmp / "r"):
    d.mkdir(parents=True, exist_ok=True)
bin_ = tmp / "bin"
bin_.mkdir()
(bin_ / "ffprobe").write_text("#!/bin/sh\necho 40.0\n")   # la durata di ogni video: 40 s
(bin_ / "ffprobe").chmod(0o755)
cfg = tmp / "c.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "s"), "default_account": "personal",
                           "accounts": {"personal": {"config_dir": str(conf), "tmux_prefix": "cm-"}},
                           "workspace": {"root": str(ws), "root_session_name": "master", "project_dirs": ["."]}, "relay": {"dir": str(tmp / "r")}}))
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db"),
            "PATH": f"{bin_}:{os.environ.get('PATH', '')}", "LANG": "it_IT.UTF-8"})
os.environ.clear()
os.environ.update(ENV)


def load(n):
    sp = importlib.util.spec_from_file_location(n.replace("-", "_"), T.SCRIPTS / f"{n}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


RS = load("cm-results")
RL = load("cm-relay")
S = load("cm-relay-state")
RL._RS[0] = RS
now = time.time()


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def slug(p):
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(p))


def tr_path(cwd, sid):
    d = conf / "projects" / slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sid}.jsonl"


def send(tid, t, cwd, files, caption=None, side=False):
    inp = {"files": files, "status": "normal"}
    if caption is not None:
        inp["caption"] = caption
    return {"type": "assistant", "timestamp": iso(t), "cwd": str(cwd), "isSidechain": side,
            "message": {"content": [{"type": "text", "text": "ecco"}, {"type": "tool_use", "id": tid, "name": "SendUserFile", "input": inp}]}}


def result(tid, t, err=False):
    return {"type": "user", "timestamp": iso(t), "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": err, "content": "x"}]}}


def put(path, rows, append=False):
    with open(path, "a" if append else "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    t = max(time.mktime(time.strptime(r["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone for r in rows) + 1
    os.utime(path, (t, t))   # l'ora finta dello scan: una trascrizione chiusa si guarda se toccata dall'ultimo giro


mp4 = film / "out" / "bozza-leggera.mp4"
mp4.write_bytes(b"\0" * 1000)
png, html = film / "out" / "home.png", film / "out" / "home.html"
png.write_bytes(b"\x89PNG" + b"\0" * 50)
html.write_text("<html></html>")
pdf = site / "griglia.pdf"
pdf.write_bytes(b"%PDF" + b"\0" * 20)
SID_F, SID_S = "aaaaaaaa-1111-2222-3333-444444444444", "bbbbbbbb-1111-2222-3333-444444444444"
TF, TS = tr_path(film, SID_F), tr_path(site, SID_S)
put(TF, [
    send("toolu_v7aaaaaaaaaaaa", now - 3000, film, [str(mp4)], "Film, versione 7: la prima con la musica"),
    result("toolu_v7aaaaaaaaaaaa", now - 2999),
    send("toolu_failaaaaaaaaaa", now - 2900, film, [str(film / "out" / "rotto.mp4")], "Film, versione 99"),
    result("toolu_failaaaaaaaaaa", now - 2899, err=True),
    send("toolu_sideaaaaaaaaaa", now - 2800, film, [str(png)], "da un subagente", side=True),
    {"type": "assistant", "timestamp": iso(now - 2700), "cwd": str(film), "message": {"content": [
        {"type": "tool_use", "id": "toolu_writeaaaaaaaaa", "name": "Write", "input": {"file_path": str(png), "content": "x"}}]}},
    send("toolu_mockaaaaaaaaaa", now - 2600, film, [str(png), str(html)], "Home ridotta: di giorno, con Altro aperto"),
    send("toolu_v8aaaaaaaaaaaa", now - 2000, film, ["out/bozza-leggera.mp4"], "Film, versione 8: come la 7, senza la linea ambra a 36,7 s. Guarda questa."),
])
put(TS, [send("toolu_pdfaaaaaaaaaaa", now - 1500, site, [str(pdf)], "Griglia art. 18: foglio di calcolo e PDF")])
LIVE = [{"tmux": "cm-film", "name": "cm-film", "cwd": str(film), "session_id": SID_F, "account": "personal", "status": "idle"}]

# ------------------------------------------------------------------ IX indice
n = RS.scan(LIVE, now)
idx = json.loads((tmp / "r" / "results.json").read_text())
items = idx["items"]
T.check("results IX1 every SendUserFile delivery of the live and the closed transcripts is indexed: a failed send, a subagent's and a Write are not",
        n == 4 and sorted(x["path"].rsplit("/", 1)[-1] for x in items.values()) == ["bozza-leggera.mp4", "bozza-leggera.mp4", "griglia.pdf", "home.png"],
        json.dumps([(x["path"], x["caption"]) for x in items.values()], ensure_ascii=False))
by = {x["caption"].split(":")[0]: x for x in items.values()}
v7, v8, mock, grid = by["Film, versione 7"], by["Film, versione 8"], by["Home ridotta"], by["Griglia art. 18"]
T.check("results IX2 a relative path becomes the session's absolute path (the same string the transcript op lists), with mime and size",
        v8["path"] == str(film / "out" / "bozza-leggera.mp4") and v8["mime"] == "video/mp4" and v8["size"] == 1000, json.dumps(v8)[:300])
T.check("results IX3 the live session's short name and tmux; a closed transcript takes its folder's name",
        (v8["session"], v8["tmux"], grid["session"]) == ("film", "cm-film", "sito"), str((v8["session"], v8["tmux"], grid["session"])))
again = RS.scan(LIVE, now + 40)
put(TF, [send("toolu_v9aaaaaaaaaaaa", now + 50, film, [str(mp4)], "Film, versione 9")], append=True)
half = json.dumps(send("toolu_halfaaaaaaaaaa", now + 51, film, [str(mp4)], "Film, versione 10"), ensure_ascii=False) + "\n"
with open(TF, "a") as f:
    f.write(half[:40])   # una riga a meta': la sessione la sta ancora scrivendo
n9 = RS.scan(LIVE, now + 80)
T.check("results IX4 incremental: a second scan finds nothing, the next one only the new complete line (a half-written line waits)",
        again == 0 and n9 == 1 and len(json.loads((tmp / "r" / "results.json").read_text())["items"]) == 5, f"{again} {n9}")
with open(TF, "a") as f:
    f.write(half[40:])
T.check("results IX4b the line finished later is read whole", RS.scan(LIVE, now + 100) == 1, "")
d = json.loads((tmp / "r" / "results.json").read_text())
d["files"] = {}
(tmp / "r" / "results.json").write_text(json.dumps(d))
T.check("results IX5 reading a transcript again from the start never duplicates a delivery", RS.scan(LIVE, now + 120) == 0, "")

# ------------------------------------------------------------------ GR stesso lavoro, versioni
g = RS.view()["groups"]
fg = next(x for x in g if x["title"] == "Film")
T.check("results GR1 «versione N» in the caption: same work by title, version from the caption, versions the highest",
        [i["version"] for i in fg["items"]] == [10, 9, 8, 7] and fg["versions"] == 10 and all(i["versions"] == 10 for i in fg["items"]), json.dumps(fg)[:400])
it8 = next(i for i in fg["items"] if i["version"] == 8)
T.check("results GR2 changed = the session's sentence after «versione N», only from N > 1",
        it8["changed"] == "come la 7, senza la linea ambra a 36,7 s. Guarda questa." and next(i for i in fg["items"] if i["version"] == 7)["changed"] == "la prima con la musica"
        and RS.caption_parts("Film, versione 1: la prima", "x")[1] == 1, str(it8["changed"]))
put(TF, [send("toolu_mock2aaaaaaaaa", now + 200, film, [str(png), str(html)], "Home con le categorie in alto")], append=True)
RS.scan(LIVE, now + 240)
hg = next(x for x in RS.view()["groups"] if x["title"] == "Home ridotta")
T.check("results GR3 no «versione»: the same file sent again is the next version of the same work, the work's title kept",
        [i["version"] for i in hg["items"]] == [2, 1] and hg["items"][0]["caption"] == "Home con le categorie in alto", json.dumps(hg)[:400])
T.check("results GR4 kind: a video, a mockup (an image with its html page: the image first, the page in files), a document",
        (fg["kind"], hg["kind"], grid["kind"]) == ("video", "mockup", "document") and hg["items"][0]["mime"] == "image/png"
        and [f["path"] for f in hg["items"][0]["files"]] == [str(html)], json.dumps(hg["items"][0])[:300])
T.check("results GR5 a video's duration (ffprobe), null for the rest; a title never cut at «art. 18»",
        it8["duration_s"] == 40.0 and hg["items"][0]["duration_s"] is None and grid["title"] == "Griglia art. 18", str((it8["duration_s"], grid["title"])))

# ------------------------------------------------------------------ ST /state.results
st = RS.summary()
KEYS = ["id", "session", "title", "kind", "mime", "path", "size", "at", "duration_s", "version", "versions", "changed", "seen_at"]
T.check("results ST1 /state.results = {n_unseen, items}: only the latest version of each unseen work, newest first, with the contract's keys",
        list(st) == ["n_unseen", "items"] and st["n_unseen"] == 3 and [(x["title"], x["version"]) for x in st["items"]] == [("Home ridotta", 2), ("Film", 10), ("Griglia art. 18", 1)]
        and all(list(x) == KEYS for x in st["items"]), json.dumps(st, ensure_ascii=False)[:500])
for i in range(12):
    p = site / f"pagina-{i}.pdf"
    p.write_bytes(b"%PDF")
    put(TS, [send(f"toolu_many{i:02d}aaaaaaaa", now + 300 + i, site, [str(p)], f"Pagina {i}")], append=True)
RS.scan(LIVE, now + 400)
st = RS.summary()
T.check("results ST2 at most 10 items, n_unseen counts them all", len(st["items"]) == 10 and st["n_unseen"] == 15, str(st["n_unseen"]))
bs = S.build_state({"results": st}, int(now), fit=False)
big = S.build_state({"results": st, "projects": [{"path": f"/p/{i}", "name": f"progetto-{i:03d}-" + "x" * 60, "account": "a", "last_used": i} for i in range(200)]}, int(now))
T.check("results ST3 build_state carries it as given, fit_state never cuts it; absent with an empty index",
        bs.get("results") == st and big.get("results") == st and "results" not in S.build_state({}, int(now), fit=False), "")

# ------------------------------------------------------------------ SN result_seen
RL.push_async = lambda *a, **k: None
RL._json_cmd = lambda *a, **k: []
ok, t = RL.execute({"id": "c1", "op": "result_seen", "arg": v8["id"], "device": "phone"})[:2]
fg = next(x for x in RS.view()["groups"] if x["title"] == "Film")
seen_v = {i["version"]: i["seen_at"] for i in fg["items"]}
T.check("results SN1 seen on version 8 counts for 7 too, not for 9 and 10: the work stays in /state with its version 10",
        ok and seen_v[8] and seen_v[7] and seen_v[9] is None and seen_v[10] is None and fg["unseen"] is True and RS.summary()["n_unseen"] == 15, t + str(seen_v))
ok, t = RL.execute({"id": "c2", "op": "result_seen", "arg": fg["items"][0]["id"], "device": "phone"})[:2]
T.check("results SN2 seen on the latest version: the work leaves /state.results, n_unseen down by one",
        ok and RS.summary()["n_unseen"] == 14 and next(x for x in RS.view()["groups"] if x["title"] == "Film")["unseen"] is False, t)
ok, t = RL.execute({"id": "c3", "op": "result_seen", "arg": "nope", "device": "phone"})[:2]
T.check("results SN3 an unknown id is refused", not ok, t)
TF.unlink()
T.check("results SN4 the index outlives the transcript: deliveries, seen and versions stay when the session's file is gone",
        RS.scan([], now + 500) == 0 and next(x for x in RS.view()["groups"] if x["title"] == "Film")["versions"] == 10
        and RS.summary()["n_unseen"] == 14, "")

# ------------------------------------------------------------------ NO result_note
TALKS = []
RL.relay_talk = lambda name, text, *o: (TALKS.append((name, text, o)), (0, "consegnato"))[1]
ok, t = RL.execute({"id": "c4", "op": "result_note", "arg": v8["id"], "text": "la linea c'è ancora", "pos_s": 36.7, "device": "phone"})[:2]
name, text, opts = TALKS[-1] if TALKS else ("", "", ())
T.check("results NO1 the note reaches the session through the relay's talk (origin relay), with the phone's prefix, title, version of versions, the second and the file",
        ok and name == "cm-film" and text.startswith(RL.prefix_for({"device": "phone"}, "cm-film"))
        and f"«Film», versione 8 di 10 a 36,7 s ({mp4}): la linea c'è ancora" in text and "--no-wait" in opts, text)
notes = next(i for i in next(x for x in RS.view()["groups"] if x["title"] == "Film")["items"] if i["version"] == 8)["notes"]
T.check("results NO2 saved in the result's notes {at, text, pos_s}", len(notes) == 1 and list(notes[0]) == ["at", "text", "pos_s"]
        and notes[0]["text"] == "la linea c'è ancora" and notes[0]["pos_s"] == 36.7, str(notes))
RL.relay_talk = lambda name, text, *o: (TALKS.append((name, text, o)), (0, "salvato nella casella: supervisor talk --status ab12"))[1]
ok, t = RL.execute({"id": "c5", "op": "result_note", "arg": grid["id"], "text": "manca la colonna", "device": "phone"})[:2]
T.check("results NO3 a closed session: the note stays in its inbox, the answer says so, saved without a second",
        ok and "casella" in t and TALKS[-1][0] == "sito" and " a " not in TALKS[-1][1].split("(")[0].split("versione")[1]
        and RS.get(grid["id"])[0]["notes"][-1]["pos_s"] is None, t + " | " + TALKS[-1][1])
nt = len(TALKS)
bad = [RL.execute({"id": "c6", "op": "result_note", "arg": v8["id"], "text": "  ", "device": "phone"})[:2],
       RL.execute({"id": "c7", "op": "result_note", "arg": v8["id"], "text": "x", "pos_s": 41.5, "device": "phone"})[:2],
       RL.execute({"id": "c8", "op": "result_note", "arg": v8["id"], "text": "x", "pos_s": True, "device": "phone"})[:2],
       RL.execute({"id": "c9", "op": "result_note", "arg": "nope", "text": "x", "device": "phone"})[:2],
       RL.execute({"id": "ca", "op": "result_note", "arg": v8["id"], "text": "x" * 2001, "device": "phone"})[:2]]
T.check("results NO4 refused, nothing sent: empty text, a second past the video's end, a boolean second, an unknown id, over 2000 characters",
        all(not ok for ok, _ in bad) and len(TALKS) == nt, str(bad))
RL.relay_talk = lambda name, text, *o: (1, "talk failed: no such session")
ok, t = RL.execute({"id": "cb", "op": "result_note", "arg": v8["id"], "text": "non parte", "device": "phone"})[:2]
T.check("results NO5 talk fails: refused and not saved", not ok and all(n["text"] != "non parte" for n in RS.get(v8["id"])[0]["notes"]), t)

# ------------------------------------------------------------------ FI op file
ok, t = RL.execute({"id": "fileA", "op": "file", "session": "film", "arg": str(mp4), "_local": True})[:2]
loc = tmp / "r" / "local" / "file" / "fileA.json"
T.check("results FI1 op file serves a result's file from the index with the session closed (its transcript gone)",
        ok and loc.exists() and json.loads(loc.read_text())["path"] == str(mp4), t)
ok, t = RL.execute({"id": "fileB", "op": "file", "session": "film", "arg": str(html), "_local": True})[:2]
T.check("results FI2 the other files of a delivery too (the mockup's page)", ok, t)
other = film / "out" / "segreto.txt"
other.write_text("x")
ok, t = RL.execute({"id": "fileC", "op": "file", "session": "film", "arg": str(other), "_local": True})[:2]
T.check("results FI3 a path outside the index and the transcripts stays refused", not ok and not (tmp / "r" / "local" / "file" / "fileC.json").exists(), t)

# ------------------------------------------------------------------ VW op results
ok, t = RL.execute({"id": "v1", "op": "results"})[:2]
v = json.loads(t) if ok else {}
fg = next((x for x in v.get("groups", []) if x["title"] == "Film"), {})
T.check("results VW1 op results: {groups, n_unseen, more}; a group {title, project, session, kind, at, versions, unseen, items}, newest work first",
        ok and list(v) == ["groups", "n_unseen", "more"] and list(fg) == ["title", "project", "session", "kind", "at", "versions", "unseen", "items"]
        and fg["project"] == "film" and v["groups"][0]["at"] >= v["groups"][-1]["at"] and "results" in RL.PASSIVE_OPS, t[:300])
T.check("results VW2 every version with its notes, files and replaced; the state's keys first",
        list(fg["items"][0])[:13] == KEYS and list(fg["items"][0])[13:] == ["caption", "files", "notes", "replaced"]
        and next(i for i in fg["items"] if i["version"] == 8)["notes"][0]["pos_s"] == 36.7, json.dumps(fg["items"][0])[:300])
mp4.write_bytes(b"\0" * 2000)   # la sessione ha riscritto lo stesso file per la versione dopo
fg = next(x for x in RS.view()["groups"] if x["title"] == "Film")
T.check("results VW3 replaced: the file on disk is no longer the delivered one", all(i["replaced"] is True for i in fg["items"])
        and next(x for x in RS.view()["groups"] if x["title"] == "Griglia art. 18")["items"][0]["replaced"] is False, "")
small = RS.view(max_bytes=1500)
T.check("results VW4 over the page the oldest works leave first, more = true", small["more"] is True and small["groups"]
        and small["groups"][0]["at"] == RS.view()["groups"][0]["at"] and len(json.dumps(small).encode()) <= 1500, str(len(small["groups"])))

# ------------------------------------------------------------------ IX nascita dell'indice
(tmp / "r" / "results.json").unlink()
T3 = tr_path(site, "cccccccc-1111-2222-3333-444444444444")
put(T3, [send("toolu_oldaaaaaaaaaaa", now - 30 * 3600, site, [str(pdf)], "Ieri"), send("toolu_veryoldaaaaaa", now - 60 * 3600, site, [str(pdf)], "Tre giorni fa"),
         send("toolu_newaaaaaaaaaaa", now - 600, site, [str(pdf)], "Adesso")])
RS.scan([], now)
caps = {x["caption"]: x["seen_at"] for x in json.loads((tmp / "r" / "results.json").read_text())["items"].values()}
T.check("results IX6 a new index looks back 48 hours; what came more than a day before its birth is born seen",
        "Tre giorni fa" not in caps and caps.get("Ieri") and caps.get("Adesso") is None, str(caps))

# ------------------------------------------------------------------ FX fixture
F = {f: json.loads((T.ROOT / f"tests/fixtures/relay/{f}.json").read_text()) for f in ("state-1-question", "state-2-idle", "state-3-stale")}
fr = F["state-3-stale"].get("results") or {}
T.check("results FX1 state-3 carries results with the code's keys, and every state's ops end with the three result ops",
        list(fr) == ["n_unseen", "items"] and fr["items"] and all(list(x) == KEYS for x in fr["items"])
        and all(x["ops"][-3:] == ["results", "result_seen", "result_note"] for x in F.values()), json.dumps(fr)[:300])
cs = json.loads((T.ROOT / "tests/fixtures/relay/cmd-result-sample.json").read_text())
ops = {c["op"] for c in cs["cmd"]}
view_res = [json.loads(r["text"]) for r in cs["result"] if r["text"].startswith('{"groups"')]
T.check("results FX2 cmd-result-sample has the three ops and a results page with the code's keys",
        {"results", "result_seen", "result_note"} <= ops and view_res and list(view_res[0]) == ["groups", "n_unseen", "more"]
        and list(view_res[0]["groups"][0]) == list(fg) and list(view_res[0]["groups"][0]["items"][0]) == list(fg["items"][0]), "")
T.finish()
