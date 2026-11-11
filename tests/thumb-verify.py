#!/usr/bin/env python3
"""Le anteprime delle consegne di Verifica (11/11/2026, supervisor-anteprime-documenti-11-11, contratto 1.136).

TH1 testo (md): le prime righe, al piu' TEXT_LINES e TEXT_MAX caratteri, kind text
TH2 PDF: la prima pagina in JPEG largo al piu' 240 px, sotto 30 KB (con pdftoppm); senza, kind none why no_tool
TH3 docx: soffice verso PDF e poi pdftoppm (con soffice); senza, none no_tool
TH4 PNG: rimpicciolito con PIL in JPEG
TH5 cache: la seconda richiesta non rifa' niente; un file cambiato si rifa'
TH6 un file sparito → none missing; una consegna di un altro computer → none remote; un tipo senza anteprima → none type
TH7 op thumb del relay: l'id di una consegna da' l'anteprima; un id sconosciuto e' un errore; con l'etag avuto → unchanged
TH8 lo scan dei risultati prepara le anteprime che mancano, al piu' tre per giro
TH9 gli esempi di cmd-result-sample …1360 (image), …1361 (text), …1362 (none), …1363 (id sconosciuto) hanno i campi del codice
"""
import base64
import importlib.util
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
rd = tmp / "relay"
rd.mkdir()
cfg = tmp / "c.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "s"), "workspace": {"root": str(home)},
                           "accounts": {"p": {"config_dir": str(home / ".claude")}}, "default_account": "p", "relay": {"dir": str(rd)}}))
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_RELAY_NO_PUSH": "1"})


def load(name, tag):
    sp = importlib.util.spec_from_file_location(tag, T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


TH = load("cm-thumb", "cm_thumb_t")
docs = tmp / "docs"
docs.mkdir()

# un PDF di una pagina, scritto a mano (niente strumenti per crearlo)
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
       b"4 0 obj<</Length 44>>stream\nBT /F1 36 Tf 72 700 Td (Preventivo) Tj ET\nendstream endobj\n"
       b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
       b"trailer<</Root 1 0 R>>\n%%EOF\n")
pdf = docs / "PREV-2026-034.pdf"
pdf.write_bytes(PDF)
md = docs / "nota.md"
md.write_text("\n".join(f"riga {i} " + "x" * 80 for i in range(1, 40)))
docx = docs / "determina.docx"
with zipfile.ZipFile(docx, "w") as z:
    z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
               '<Default Extension="xml" ContentType="application/xml"/>'
               '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body><w:p><w:r><w:t>Determina di affidamento</w:t></w:r></w:p></w:body></w:document>')
png = docs / "home.png"
try:
    from PIL import Image
    Image.new("RGB", (1200, 800), (30, 120, 200)).save(png)
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False
HAVE_PDFTOPPM, HAVE_SOFFICE = bool(TH._tool("pdftoppm")), bool(TH._tool("soffice") or TH._tool("libreoffice"))


def item(rid, path, **kw):
    return dict({"id": rid, "path": str(path), "host": "local", "kind": "document", "at": int(time.time())}, **kw)


def img_ok(t):
    if t.get("kind") != "image":
        return False
    data = base64.b64decode(t["data"])
    return data[:2] == b"\xff\xd8" and len(data) <= TH.MAX_BYTES and t.get("width") and t["width"] <= TH.WIDTH and t.get("mime") == "image/jpeg"


# TH1
t1 = TH.thumb(item("aaaa000000000001", md))
T.check("TH1 text: the first lines, at most TEXT_LINES and TEXT_MAX characters, kind text",
        t1["kind"] == "text" and t1["text"].startswith("riga 1 ") and len(t1["text"]) <= TH.TEXT_MAX and t1["text"].count("\n") < TH.TEXT_LINES
        and set(t1) == {"id", "path", "at", "kind", "text"}, json.dumps(t1)[:200])
# TH2
t2 = TH.thumb(item("aaaa000000000002", pdf))
T.check(f"TH2 PDF: the first page as a JPEG at most 240 px wide and under 30 KB (pdftoppm {'present' if HAVE_PDFTOPPM else 'missing: none no_tool'})",
        img_ok(t2) if HAVE_PDFTOPPM else (t2["kind"] == "none" and t2["why"] == "no_tool"), json.dumps({k: v for k, v in t2.items() if k != "data"}))
# TH3
t3 = TH.thumb(item("aaaa000000000003", docx))
T.check(f"TH3 docx: soffice to PDF, then the first page (soffice {'present' if HAVE_SOFFICE else 'missing: none no_tool'})",
        img_ok(t3) if (HAVE_SOFFICE and HAVE_PDFTOPPM) else (t3["kind"] == "none" and t3["why"] == "no_tool"), json.dumps({k: v for k, v in t3.items() if k != "data"}))
# TH4
if HAVE_PIL:
    t4 = TH.thumb(item("aaaa000000000004", png, kind="image"))
    T.check("TH4 PNG: made smaller with PIL, a JPEG under 30 KB, 240 px wide keeping the ratio", img_ok(t4) and t4["width"] == 240 and t4["height"] == 160,
            json.dumps({k: v for k, v in t4.items() if k != "data"}))
# TH5
cf = rd / "thumbs" / "aaaa000000000001.json"
m0 = cf.stat().st_mtime_ns
time.sleep(0.05)
t5 = TH.thumb(item("aaaa000000000001", md))
same = cf.stat().st_mtime_ns == m0
time.sleep(1.1)
md.write_text("nuova prima riga\nseconda")
t5b = TH.thumb(item("aaaa000000000001", md))
T.check("TH5 cache: the second request makes nothing again; a changed file is made again",
        same and t5 == t1 and t5b["text"] == "nuova prima riga\nseconda", f"{same} {t5b.get('text')}")
# TH6
t6a = TH.thumb(item("aaaa000000000006", docs / "sparito.pdf"))
t6b = TH.thumb(item("aaaa000000000007", pdf, host="win"))
(docs / "x.bin").write_bytes(b"\0" * 10)
t6c = TH.thumb(item("aaaa000000000008", docs / "x.bin"))
T.check("TH6 a missing file → none missing; another computer's delivery → none remote; a type with no preview → none type",
        (t6a["kind"], t6a["why"], t6b["kind"], t6b["why"], t6c["kind"], t6c["why"]) == ("none", "missing", "none", "remote", "none", "type"), f"{t6a} {t6b} {t6c}")

# TH7 — op thumb del relay, con un indice dei risultati vero
RS = load("cm-results", "cm_results_t")
idx = {"v": 1, "created": time.time(), "scan_at": time.time(), "items": {}}
for rid, path, kind in (("bbbb000000000001", md, "document"), ("bbbb000000000002", pdf, "document")):
    idx["items"][rid] = {"id": rid, "sid": "s", "tool_id": rid, "session": "preventivi", "host": "local", "project": "p", "key": rid, "title": "t",
                         "title_key": "t", "kind": kind, "mime": None, "path": str(path), "size": os.path.getsize(path), "mtime": None,
                         "at": int(time.time()), "duration_s": None, "version": 1, "changed": None, "caption": None, "files": [], "seen_at": None, "notes": []}
(rd / "results.json").write_text(json.dumps(idx))
RL = load("cm-relay", "cm_relay_thumb")
RL.CFG["language"] = "en"   # i testi del contratto sono in inglese
r_ok = RL.run_cmd({"id": "c1", "op": "thumb", "arg": "bbbb000000000001", "device": "web", "by": "x"})
r_un = RL.run_cmd({"id": "c2", "op": "thumb", "arg": "bbbb000000000001", "device": "web", "by": "x", "etag": r_ok.get("etag")})
r_no = RL.run_cmd({"id": "c3", "op": "thumb", "arg": "ffff000000000000", "device": "web", "by": "x"})
T.check("TH7 op thumb: a delivery's id gives its preview with etag; the same etag back → unchanged; an unknown id is an error; a slow read",
        r_ok["ok"] and json.loads(r_ok["text"])["kind"] == "text" and r_ok.get("etag") and r_un.get("unchanged") is True and r_un["text"] == ""
        and r_no["ok"] is False and "thumb" in RL.PASSIVE_OPS and "thumb" in RL.SLOW_READ_OPS and "thumb" in RL.OPS, f"{r_ok} {r_un} {r_no}")

# TH8 — lo scan prepara
shutil.rmtree(rd / "thumbs")
many = [item(f"cccc00000000000{i}", md) for i in range(5)]
n8 = TH.prepare(many)
made = sorted(p.stem for p in (rd / "thumbs").glob("*.json"))
n8b = TH.prepare(many)
T.check("TH8 the results scan prepares the missing previews, at most three per round; the next round the rest",
        n8 == 3 and len(made) == 3 and n8b == 2 and len(list((rd / "thumbs").glob("*.json"))) == 5, f"{n8} {made} {n8b}")

# TH9 — gli esempi del contratto
SAMPLE = json.loads((T.ROOT / "tests" / "fixtures" / "relay" / "cmd-result-sample.json").read_text())
res = {r["id"].split("-")[1]: r for r in SAMPLE["result"]}
cmds = {c["id"].split("-")[1]: c for c in SAMPLE["cmd"]}
try:
    b60, b61, b62 = (json.loads(res[k]["text"]) for k in ("1360", "1361", "1362"))
except (KeyError, ValueError):
    b60 = b61 = b62 = {}
T.check("TH9 the samples …1360 (image), …1361 (text), …1362 (none), …1363 (unknown id): op thumb, the code's fields, the etag of the text, the error as the code says it",
        [cmds.get(k, {}).get("op") for k in ("1360", "1361", "1362", "1363")] == ["thumb"] * 4
        and set(b60) == {"id", "path", "at", "kind", "mime", "data", "width", "height"} and b60.get("kind") == "image"
        and base64.b64decode(b60.get("data") or "")[:2] == b"\xff\xd8"
        and set(b61) == set(t1) and set(b62) == {"id", "path", "at", "kind", "why"} and b62.get("why") in ("no_tool", "missing", "remote", "type")
        and all(res[k]["etag"] == RL.etag_of(res[k]["text"]) for k in ("1360", "1361", "1362"))
        and res["1363"]["ok"] is False and res["1363"]["text"] == r_no["text"].replace("ffff000000000000", cmds["1363"]["arg"]),
        f"{sorted(b60)} {sorted(b62)} {res.get('1363')}")
T.rm(tmp)
T.finish()
