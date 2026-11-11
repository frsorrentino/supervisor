#!/usr/bin/env python3
"""cm-thumb — l'anteprima di una consegna di «Verifica», fatta dal relay (11/11/2026, supervisor-anteprime-documenti-11-11:
nell'app i documenti mostravano solo l'icona generica; scaricare il file intero sul telefono per l'anteprima peserebbe su
Firebase).

Una per consegna (l'id dell'indice dei risultati, cm-results), creata una volta e tenuta in <relay.dir>/thumbs/<id>.json;
si rifa' solo se il file sul disco cambia (dimensione o data). Forme:
  image  {kind, mime: image/jpeg, data (base64), width, height}  JPEG largo al piu' WIDTH px, sotto MAX_BYTES
         PDF: la prima pagina con pdftoppm; documenti d'ufficio: soffice --headless verso PDF, poi pdftoppm;
         immagini: rimpicciolite con PIL; su macOS, se mancano gli altri, qlmanage -t
  text   {kind, text}  le prime righe (al piu' TEXT_LINES righe e TEXT_MAX caratteri) di txt, md, csv, json, log
  none   {kind, why}  niente strumento adatto, file sparito, consegna di un altro computer, tipo senza anteprima
Sempre con id, path, at (quando e' stata fatta). Solo file dell'indice dei risultati: mai un percorso scelto da fuori.
Gli strumenti girano con nice, un tempo massimo ciascuno e in una cartella temporanea che poi si toglie.

Uso: cm-thumb.py ID [--json]   (la CLI per le prove; il relay chiama thumb())
"""
import base64
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WIDTH = 240
MAX_BYTES = 30 * 1024
TEXT_LINES = 20
TEXT_MAX = 1200
TOOL_TIMEOUT_S = 60
PDF_EXT = {"pdf"}
OFFICE_EXT = {"doc", "docx", "odt", "rtf", "xls", "xlsx", "ods", "ppt", "pptx", "odp"}
TEXT_EXT = {"txt", "md", "csv", "json", "log", "tsv", "yaml", "yml"}
IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)


def cache_dir():
    d = Path(cm.expand((CFG.get("relay") or {}).get("dir") or "~/.cc-supervisor/relay")) / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ext(path):
    name = os.path.basename(str(path))
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _tool(name):
    for c in (shutil.which(name), f"/usr/bin/{name}", f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice" if name == "soffice" else ""):
        if c and os.access(c, os.X_OK):
            return c
    return None


def _run(cmd, cwd=None):
    try:
        return subprocess.run(["nice", "-n", "19"] + cmd, capture_output=True, timeout=TOOL_TIMEOUT_S, cwd=cwd,
                              stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _jpeg_from_image(src):
    """(bytes, w, h) di un JPEG largo al piu' WIDTH, sotto MAX_BYTES, o None senza PIL."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(src)
        im.load()
    except Exception:   # noqa: BLE001 — un'immagine che non si apre non ha anteprima
        return None
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        try:
            bg.paste(im, mask=im.convert("RGBA").split()[-1])
        except Exception:   # noqa: BLE001
            bg = im.convert("RGB")
        im = bg
    im.thumbnail((WIDTH, WIDTH * 2))
    for q in (75, 60, 45, 30):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= MAX_BYTES:
            return buf.getvalue(), im.size[0], im.size[1]
    return None


def _jpeg_size(data):
    """(w, h) dall'intestazione di un JPEG, senza PIL."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None, None
        marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + length
    return None, None


def _pdf_first_page(pdf, tmp):
    """La prima pagina di un PDF in JPEG con pdftoppm. (bytes, w, h) o None."""
    exe = _tool("pdftoppm")
    if not exe:
        return None
    out = os.path.join(tmp, "page")
    for q in (70, 50, 35):
        if not _run([exe, "-f", "1", "-l", "1", "-jpeg", "-jpegopt", f"quality={q}", "-scale-to", str(WIDTH), str(pdf), out]):
            return None
        got = sorted(Path(tmp).glob("page*.jpg"))
        if not got:
            return None
        data = got[0].read_bytes()
        got[0].unlink()
        if len(data) <= MAX_BYTES:
            w, h = _jpeg_size(data)
            return data, w, h
    return None


def _office_to_pdf(src, tmp):
    exe = _tool("soffice") or _tool("libreoffice")
    if not exe:
        return None
    # un profilo suo: soffice con un'altra istanza aperta (l'utente) non convertirebbe niente
    prof = Path(tmp) / "lo-profile"
    if not _run([exe, f"-env:UserInstallation=file://{prof}", "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)]):
        return None
    got = [p for p in Path(tmp).glob("*.pdf")]
    return got[0] if got else None


def _quicklook(src, tmp):
    """macOS: l'anteprima di Quick Look (qlmanage -t), poi in JPEG con PIL."""
    exe = _tool("qlmanage")
    if not exe or not _run([exe, "-t", "-s", str(WIDTH * 2), "-o", tmp, str(src)]):
        return None
    got = sorted(Path(tmp).glob("*.png"))
    return _jpeg_from_image(got[0]) if got else None


def _text_head(src):
    try:
        with open(src, "r", errors="replace") as f:
            lines = []
            for _ in range(TEXT_LINES):
                line = f.readline()
                if not line:
                    break
                lines.append(line.rstrip("\n"))
    except OSError:
        return None
    text = "\n".join(lines)
    return text[:TEXT_MAX]


def _sig(path):
    try:
        st = os.stat(path)
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return None


def make(item):
    """L'anteprima di una consegna (una voce dell'indice dei risultati). Non legge ne' scrive la cache."""
    rid, path = item.get("id"), item.get("path") or ""
    base = {"id": rid, "path": path, "at": int(time.time())}
    if item.get("host") not in (None, "", "local"):
        return dict(base, kind="none", why="remote")
    if not path or not os.path.isfile(path):
        return dict(base, kind="none", why="missing")
    ext = _ext(path)
    if ext in TEXT_EXT:
        text = _text_head(path)
        return dict(base, kind="text", text=text) if text is not None else dict(base, kind="none", why="unreadable")
    tmp = tempfile.mkdtemp(prefix="cm-thumb-")
    try:
        got = None
        if ext in IMAGE_EXT:
            got = _jpeg_from_image(path)
        elif ext in PDF_EXT:
            got = _pdf_first_page(path, tmp)
        elif ext in OFFICE_EXT:
            pdf = _office_to_pdf(path, tmp)
            got = _pdf_first_page(pdf, tmp) if pdf else None
        if got is None and sys.platform == "darwin" and (ext in IMAGE_EXT | PDF_EXT | OFFICE_EXT):
            got = _quicklook(path, tmp)
        if got is None:
            kind_known = ext in IMAGE_EXT | PDF_EXT | OFFICE_EXT
            return dict(base, kind="none", why="no_tool" if kind_known else "type")
        data, w, h = got
        return dict(base, kind="image", mime="image/jpeg", data=base64.b64encode(data).decode(), width=w, height=h)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def thumb(item, make_missing=True):
    """L'anteprima dalla cache, rifatta se il file e' cambiato; con make_missing False, None se non c'e' ancora."""
    rid = str(item.get("id") or "")
    if not rid or not rid.replace("-", "").isalnum():
        return None
    p = cache_dir() / f"{rid}.json"
    sig = _sig(item.get("path") or "")
    try:
        cached = json.loads(p.read_text())
        if cached.get("sig") == sig:
            cached.pop("sig", None)
            return cached
    except (OSError, ValueError):
        pass
    if not make_missing:
        return None
    t = make(item)
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(t, sig=sig), ensure_ascii=False))
        os.replace(tmp, p)
    except OSError:
        pass
    return t


def prepare(items, limit=3):
    """Lo scan dei risultati prepara le anteprime che mancano, al piu' `limit` per giro (dalla consegna piu' recente)."""
    done = 0
    for x in sorted(items, key=lambda v: -(v.get("at") or 0)):
        if done >= limit:
            break
        if thumb(x, make_missing=False) is None:
            thumb(x)
            done += 1
    return done


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    RS = _load("cm-results")
    x, _ = RS.get(argv[0])
    if not x:
        print(f"cm-thumb: no result {argv[0][:40]}", file=sys.stderr)
        return 1
    t = thumb(x)
    if "--json" in argv:
        print(json.dumps(t, ensure_ascii=False))
    else:
        print(t["kind"], t.get("why") or "", f"{len(t.get('data') or '')} base64" if t.get("data") else (t.get("text") or "")[:120])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
