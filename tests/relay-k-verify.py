#!/usr/bin/env python3
"""La K dell'app: il testo dell'esito di ogni turno e i risultati di oggi (19/11/2026, supervisor-relay-k-19-11; chiesto da
supervisor-app per la K scelta dal maintainer). Senza, la riga del turno diceva «riuscito, senza esito scritto» e
«Risultati» non compariva. Additivo in op kpi, `v` resta 1.

RK1 turns[].text: la riga «Esito:» del turno (senza «Esito:»), tagliata entro 120 caratteri a parola intera, senza «…» (l'app sfuma la fine);
    turns[].entry: l'id della voce della chat (transcript_entries) del messaggio finale del turno
RK2 senza «Esito:», text e' la prima riga dell'ultimo testo del turno; entry quella voce
RK3 results: un'immagine e un file mandati con SendUserFile (kind image / file, path, entry della chiamata), un link
    (kind link, url) e una release (kind release, url /releases/tag/), dal piu' recente; mai i link all'app stessa
RK4 al massimo 20 risultati per sessione; i risultati di ieri no
RK5 gli id delle voci sono quelli che la chat dell'app usa (cm-core.transcript_entries)
RK6 details/relay.md descrive text, entry e results
"""
import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
cwd = home / "ws" / "alfa"
(cwd / "out").mkdir(parents=True)
(cwd / "out" / "mockup.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
(cwd / "out" / "listino.pdf").write_bytes(b"%PDF-1.4\n" + b"0" * 64)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "default_account": "personale",
                           "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
                           "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                           "relay": {"enabled": False, "dir": str(tmp / "relay"), "web_app_url": "https://app.example.org"}}))
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg)})

NOW = dt.datetime.now(dt.timezone.utc)
# i turni di «oggi» stanno nei primi secondi del giorno locale: mai a cavallo della mezzanotte (il test girava alle 00:15)
DAY0 = dt.datetime.now().replace(hour=0, minute=0, second=1, microsecond=0).astimezone(dt.timezone.utc)


def ts(min_ago):
    return (DAY0 + dt.timedelta(seconds=40 - min_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(uid, text, m):
    return {"type": "user", "uuid": uid, "timestamp": ts(m), "sessionId": "S", "cwd": str(cwd), "origin": {"kind": "human"},
            "message": {"role": "user", "content": text}}


def asst(uid, blocks, m, mid=None):
    return {"type": "assistant", "uuid": uid, "timestamp": ts(m), "sessionId": "S", "cwd": str(cwd),
            "message": {"id": mid or uid, "role": "assistant", "content": blocks, "usage": {"input_tokens": 10, "output_tokens": 5}}}


def tool_result(uid, tid, m):
    return {"type": "user", "uuid": uid, "timestamp": ts(m), "sessionId": "S", "cwd": str(cwd), "toolUseResult": {"ok": True},
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}}


LONG = ("listino di dicembre pronto con le note e i totali controllati riga per riga, piu' il riepilogo per il cliente "
        "e le modifiche al file condiviso del gruppo")
yesterday = (dt.datetime.now() - dt.timedelta(days=1)).replace(hour=12)
rows = [
    {"type": "user", "uuid": "y0", "timestamp": yesterday.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "sessionId": "S",
     "origin": {"kind": "human"}, "message": {"role": "user", "content": "ieri"}},
    {"type": "assistant", "uuid": "ya", "timestamp": yesterday.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "sessionId": "S",
     "message": {"id": "ya", "role": "assistant", "content": [{"type": "text", "text": "vecchio https://ieri.example.com/x"}]}},
    user("u1", "prepara il listino", 30),
    asst("a1", [{"type": "text", "text": f"Fatto.\n\n**Esito:** {LONG}"}], 29),
    user("u2", "mandami il mockup", 20),
    asst("a2", [{"type": "text", "text": "Lo mando."},
                {"type": "tool_use", "id": "t1", "name": "SendUserFile", "input": {"files": ["out/mockup.png", "out/listino.pdf"], "caption": "Mockup"}}], 19),
    tool_result("r1", "t1", 19),
    asst("a3", [{"type": "text", "text": "Ecco [l'anteprima](https://preview.example.com/p/42) e la release "
                                         "https://github.com/o/r/releases/tag/v1.2.\nLa carta: https://app.example.org/#/attesa/w123"}], 18),
]
tr = tmp / "S.jsonl"
tr.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
# RK4: un'altra sessione con 25 link in un turno solo
many = [rows[0], rows[1], user("v1", "metti i link", 10),
        asst("b1", [{"type": "text", "text": "Link:\n" + "\n".join(f"- https://l{i}.example.com/" for i in range(25))}], 9)]
tr2 = tmp / "S2.jsonl"
tr2.write_text("\n".join(json.dumps(r) for r in many) + "\n")

spec = importlib.util.spec_from_file_location("cm_kpi_rk", T.SCRIPTS / "cm-kpi.py")
K = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K)
now = NOW.timestamp()
out = K.kpi_turns_today([("alfa", str(tr), "idle")], now)
s = out[0] if out else {}
turns = s.get("turns") or []
t1 = turns[0] if turns else {}
T.check("RK1 turns[].text: the «Esito:» line without the prefix, cut within 120 at a whole word, no «…»; entry the final message's entry",
        str(t1.get("text", "")).startswith("listino di dicembre pronto") and "…" not in t1["text"] and len(t1["text"]) <= 120
        and LONG.startswith(t1["text"]) and LONG[len(t1["text"])] == " " and not t1["text"].endswith((",", " "))
        and t1.get("entry") == "a1.0", json.dumps(t1, ensure_ascii=False))
t2 = turns[1] if len(turns) > 1 else {}
T.check("RK2 without «Esito:», text is the first line of the turn's last text (a markdown link shows its words); entry that entry",
        t2.get("text") == "Ecco l'anteprima e la release https://github.com/o/r/releases/tag/v1.2." and t2.get("entry") == "a3.0",
        json.dumps(t2, ensure_ascii=False))
res = s.get("results") or []
by = {(r.get("kind"), r.get("path") or r.get("url")): r for r in res}
img = by.get(("image", str(cwd / "out" / "mockup.png")))
pdf = by.get(("file", str(cwd / "out" / "listino.pdf")))
lnk = by.get(("link", "https://preview.example.com/p/42"))
rel = by.get(("release", "https://github.com/o/r/releases/tag/v1.2"))
T.check("RK3 results: image and file from SendUserFile (entry of the call), a link and a release (entry of the message); never the app's own links",
        bool(img and pdf and lnk and rel) and img.get("entry") == "a2.1" and lnk.get("entry") == "a3.0" and lnk.get("label") == "l'anteprima"
        and all("app.example.org" not in str(r.get("url")) for r in res) and all(r.get("label") and r.get("at") for r in res),
        json.dumps(res[-6:], ensure_ascii=False)[:900])
ats = [r.get("at") for r in res]
many_res = (K.kpi_turns_today([("beta", str(tr2), "idle")], now) or [{}])[0].get("results") or []
T.check("RK4 at most 20 results per session, newest first; yesterday's are not there",
        len(many_res) == 20 and ats == sorted(ats, reverse=True) and len(res) == 4
        and all("ieri.example.com" not in str(r.get("url")) for r in res + many_res), f"{len(many_res)} {len(res)} {ats[:5]}")
spec = importlib.util.spec_from_file_location("cm_core_rk", T.SCRIPTS / "cm-core.py")
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
ids = {e.get("id") for e in C.transcript_entries(str(tr))}
T.check("RK5 the entries are the ids the app's chat uses (cm-core.transcript_entries)",
        {"a1.0", "a3.0", "a2.1"} <= ids and t1.get("entry") in ids, sorted(x for x in ids if x)[:20])
doc = (T.ROOT / "details" / "relay.md").read_text(encoding="utf-8")
T.check("RK6 details/relay.md describes text, entry and results of kpi_turns_today",
        "supervisor-relay-k-19-11" in doc and "`results`" in doc and "`entry`" in doc and "`text`" in doc, "")
T.finish()
