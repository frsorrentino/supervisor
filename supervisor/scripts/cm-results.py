#!/usr/bin/env python3
"""supervisor results — i risultati consegnati dalle sessioni, per «Da guardare» dell'app (contratto 1.106, 02/11/2026).

Compito supervisor-risultati-02-11, chiesto da supervisor-app per app-da-guardare-02-11 e approvato dal maintainer il
02/11 alle 18:58. Un risultato e' un file mandato con SendUserFile: finora stava solo nella trascrizione della sessione.

INDICE: <relay.dir>/results.json, persistente — i risultati, il visto e le note sopravvivono alla chiusura della
sessione e alla pulizia delle trascrizioni. Lo riempie `scan`, un processo staccato a bassa priorita' che il relay
lancia al piu' ogni SCAN_EVERY_S: legge di ogni trascrizione solo i byte nuovi (files[path].off) e solo le righe che
nominano SendUserFile (o un tool_result in errore: una consegna fallita non e' un risultato). La prima volta guarda le
trascrizioni toccate nelle ultime BACKFILL_S; le consegne piu' vecchie di un giorno rispetto alla nascita dell'indice
nascono viste (seen_at = la nascita), cosi' il telefono non si riempie di cose gia' guardate al PC.

UNA CONSEGNA = un risultato: il file principale (video, poi immagine, poi pagina html, poi il primo) in path/mime/size, gli
altri della stessa chiamata in `files`. kind: video | mockup (c'e' una pagina html, di solito con la sua immagine) | image
| document | other.

STESSO LAVORO: nello stesso progetto, una consegna con «versione N» nella didascalia va col gruppo dello stesso titolo
(il testo prima di «versione»); senza, col gruppo la cui ultima consegna ha lo stesso file. version = il numero della
didascalia, altrimenti quello prima + 1; versions = il piu' alto del gruppo. changed = il resto della didascalia dopo
«versione N» (la frase della sessione), solo da N > 1. Le sessioni riscrivono spesso lo stesso file a ogni versione:
`replaced` (in `results`) dice che il file sul disco non e' piu' quello consegnato.

VISTO: si segna su una consegna e vale anche per le versioni prima di lei. /state porta solo l'ultima versione di ogni
lavoro non visto, al piu' STATE_MAX, la piu' recente per prima; n_unseen le conta tutte.

Uso: cm-results.py scan        (le righe di `sessions --json` su stdin, se ci sono)
     cm-results.py [--json]    (lo stato per /state)
"""
import contextlib
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)

SCAN_EVERY_S = 30
BACKFILL_S = 48 * 3600
BORN_SEEN_S = 86400
KEEP_S = 90 * 86400          # una consegna oltre i 90 giorni esce dall'indice
FILE_KEEP_S = 3 * 86400      # il punto di lettura di una trascrizione ferma da 3 giorni si dimentica (gli id non si ripetono)
STATE_MAX = 10
TITLE_MAX, CHANGED_MAX, CAPTION_MAX = 80, 300, 500
NOTE_MAX = 2000
NOTES_KEEP = 50
VIDEO_EXT = {"mp4", "webm", "mov", "m4v", "mkv"}
DOC_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "csv", "md", "txt", "rtf"}
_ERR = re.compile(rb'"is_error":\s*true')
_VERSION = re.compile(r"^(?P<title>.*?)[\s,;:.—–-]*\b(?:versione|version|vers\.|ver\.)\s*(?P<n>\d{1,4})\b(?P<rest>.*)$", re.I | re.S)


def _load(name):
    sp = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


def rdir():
    p = Path(cm.expand((CFG.get("relay") or {}).get("dir") or "~/.cc-supervisor/relay"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def index_path():
    return rdir() / "results.json"


def _read():
    try:
        d = json.loads(index_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d):
    p = index_path()
    tmp = p.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False))
    os.replace(tmp, p)


@contextlib.contextmanager
def _locked():
    """Un solo scrittore: la scansione staccata e le op del relay (visto, nota) leggono, cambiano e riscrivono qui dentro."""
    with open(rdir() / "results.lock", "a") as h:
        fcntl.flock(h, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(h, fcntl.LOCK_UN)


def _epoch(ts):
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _cut(s, n):
    s = " ".join(str(s or "").split())
    if len(s) <= n:
        return s
    cut = s[:n + 1].rsplit(" ", 1)[0] if " " in s[:n + 1] else s[:n]
    return cut.rstrip(" ,;:.—–-")


def _norm(s):
    return " ".join(re.sub(r"[^\w]+", " ", str(s or "").lower()).split())


def _ext(p):
    return re.split(r"[\\/]", str(p))[-1].rsplit(".", 1)[-1].lower() if "." in str(p) else ""


def _stem(p):
    name = re.split(r"[\\/]", str(p))[-1]
    return " ".join(re.sub(r"[-_]+", " ", name.rsplit(".", 1)[0]).split()) or name


def kind_of(refs):
    exts = [_ext(f["path"]) for f in refs]
    mimes = [str(f.get("mime") or "") for f in refs]
    if any(m.startswith("video/") or e in VIDEO_EXT for m, e in zip(mimes, exts)):
        return "video"
    if "html" in exts or "htm" in exts:
        return "mockup"
    if any(m.startswith("image/") for m in mimes):
        return "image"
    if any(e in DOC_EXT for e in exts):
        return "document"
    return "other"


def primary_of(refs, kind):
    def score(f):
        m, e = str(f.get("mime") or ""), _ext(f["path"])
        if kind == "video":
            return 0 if (m.startswith("video/") or e in VIDEO_EXT) else 1
        if kind in ("mockup", "image"):
            return 0 if m.startswith("image/") else (1 if e in ("html", "htm") else 2)
        return 0
    return min(range(len(refs)), key=lambda i: (score(refs[i]), i))


def caption_parts(caption, primary):
    """(titolo, versione dichiarata | None, il resto dopo «versione N») dalla didascalia."""
    c = " ".join(str(caption or "").split())
    m = _VERSION.match(c)
    if m:
        title = _cut(m.group("title"), TITLE_MAX)
        rest = re.sub(r"^[\s,;:.—–-]+", "", m.group("rest") or "")
        return title, int(m.group("n")), rest
    # il primo pezzo: fino a «: », a un punto prima di una maiuscola (non «art. 18») o a un trattino lungo
    first = re.split(r":\s|\.\s+(?=[A-ZÀ-Ý«])|\.$|\s[—–]\s", c, maxsplit=1)[0] if c else ""
    return _cut(first, TITLE_MAX) or _stem(primary), None, ""


def _duration(path):
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True, timeout=15)
        return round(float(p.stdout.strip()), 1) if p.returncode == 0 and p.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _mtime(path):
    try:
        return int(os.path.getmtime(path))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ scansione
def _read_new(path, off):
    """(righe nuove complete, nuovo punto di lettura). Una riga a meta' resta per il giro dopo."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], off
    if size < off:   # trascrizione riscritta: si ricomincia (gli id non si ripetono)
        off = 0
    if size == off:
        return [], off
    out, pos = [], off
    with open(path, "rb") as f:
        f.seek(off)
        tail = b""
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            buf = tail + chunk
            cut = buf.rfind(b"\n")
            if cut < 0:
                tail = buf
                continue
            for line in buf[:cut].split(b"\n"):
                if b"SendUserFile" in line or (b'"is_error"' in line and _ERR.search(line)):
                    out.append(line)
            pos += len(buf[:cut + 1])
            tail = buf[cut + 1:]
    return out, pos


def _deliveries(lines, core, cwd_default):
    """([consegne], {tool_use_id in errore})."""
    found, failed = [], set()
    for raw in lines:
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        if d.get("type") == "user":
            failed.update(str(b.get("tool_use_id")) for b in content
                          if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"))
            continue
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "SendUserFile" and b.get("id")):
                continue
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            refs = core._files_of("SendUserFile", inp, d.get("cwd") or cwd_default)
            if refs:
                found.append({"tool_id": str(b["id"]), "at": _epoch(d.get("timestamp")), "cwd": d.get("cwd") or cwd_default,
                              "caption": str(inp.get("caption") or ""), "refs": refs})
    return found, failed


def _group_key(items, project, title, declared, path):
    same = [x for x in items.values() if x.get("project") == project]
    if declared is not None and title:
        for x in same:
            if x.get("title_key") == _norm(title):
                return x["key"]
    latest = {}
    for x in same:
        if x["key"] not in latest or x["at"] > latest[x["key"]]["at"]:
            latest[x["key"]] = x
    for k, x in latest.items():
        if x.get("path") == path:
            return k
    return f"{project}|{_norm(title) if declared is not None and title else path}"


def _add(items, sid, sess, dl, root, S):
    rid = hashlib.sha1(f"{sid}/{dl['tool_id']}".encode()).hexdigest()[:16]
    if rid in items:
        return None
    refs = dl["refs"]
    kind = kind_of(refs)
    i = primary_of(refs, kind)
    prim = refs[i]
    project = S.project_of(dl["cwd"] or sess["cwd"], root)
    title, declared, rest = caption_parts(dl["caption"], prim["path"])
    key = _group_key(items, project, title, declared, prim["path"])
    group = sorted((x for x in items.values() if x["key"] == key), key=lambda x: x["at"])
    if declared is None and group:
        title = group[-1]["title"]   # senza «versione N» il titolo resta quello del lavoro
    if not title:
        title = group[-1]["title"] if group else _stem(prim["path"])
    version = declared if declared is not None else ((group[-1]["version"] + 1) if group else 1)
    local = not sess.get("host") or sess.get("host") == "local"
    items[rid] = {
        "id": rid, "sid": sid, "tool_id": dl["tool_id"], "session": sess["name"], "tmux": sess.get("tmux") or sess["name"],
        "host": sess.get("host") or "local", "project": project, "key": key, "title": title, "title_key": _norm(title),
        "kind": kind, "mime": prim.get("mime"), "path": prim["path"], "size": prim.get("size"),
        "mtime": _mtime(prim["path"]) if local else None, "at": int(dl["at"]),
        "duration_s": _duration(prim["path"]) if kind == "video" and local else None,
        "version": version, "changed": _cut(rest, CHANGED_MAX) or None if (declared or 0) > 1 else None,
        "caption": _cut(dl["caption"], CAPTION_MAX) or None,
        "files": [f for j, f in enumerate(refs) if j != i], "seen_at": None, "notes": []}
    return rid


def scan(live=None, now=None):
    """Legge i byte nuovi delle trascrizioni vive e di quelle chiuse toccate da poco. Ritorna quante consegne nuove."""
    now = now or time.time()
    core, TL, S = _load("cm-core"), _load("cm-timeline"), _load("cm-relay-state")
    prefixes = [a.get("tmux_prefix") or "" for a in (CFG.get("accounts") or {}).values()]
    root = cm.expand(CFG["workspace"]["root"])
    if live is None:
        try:
            p = subprocess.run([str(HERE / "supervisor"), "sessions", "--json", "--no-screen"], capture_output=True, text=True, timeout=60)
            live = json.loads(p.stdout) if p.returncode == 0 else []
        except (OSError, ValueError, subprocess.TimeoutExpired):
            live = []
    by_tr = {}
    for r in live:
        t = core.transcript_of(r)
        if t:
            by_tr[os.path.realpath(t)] = {"name": S.short_name(r.get("name") or r.get("tmux") or "", prefixes),
                                          "tmux": r.get("tmux") or r.get("name"), "host": r.get("host") or "local", "cwd": r.get("cwd") or ""}
    snap = _read()
    created = float(snap.get("created") or now)
    offs = dict(snap.get("files") or {})
    since = min(float(snap.get("scan_at") or now - BACKFILL_S), now - SCAN_EVERY_S) - 120
    found = []
    for name, _alive, cwd, path in TL.sessions_in(max(since, now - BACKFILL_S), live=live):
        rec = offs.get(path) or {}
        lines, pos = _read_new(path, int(rec.get("off") or 0))
        offs[path] = {"off": pos, "at": int(now)}
        if not lines:
            continue
        sess = by_tr.get(path) or {"name": name, "tmux": None, "host": "local", "cwd": cwd}
        dls, failed = _deliveries(lines, core, cwd)
        found.append((Path(path).stem, sess, [x for x in dls if x["tool_id"] not in failed and x["at"]], failed))
    with _locked():
        d = _read()
        d.setdefault("v", 1)
        created = float(d.get("created") or created)
        d["created"] = created
        items = d.setdefault("items", {})
        new = 0
        for sid, sess, dls, failed in found:
            for x in [k for k, v in items.items() if v.get("sid") == sid and v.get("tool_id") in failed]:
                items.pop(x, None)
            for dl in sorted(dls, key=lambda x: x["at"]):
                if dl["at"] < created - BACKFILL_S:
                    continue
                rid = _add(items, sid, sess, dl, root, S)
                if rid:
                    new += 1
                    if dl["at"] < created - BORN_SEEN_S:
                        items[rid]["seen_at"] = int(created)
        for k in [k for k, v in items.items() if now - float(v.get("at") or 0) > KEEP_S]:
            items.pop(k, None)
        d["files"] = {p: o for p, o in {**(d.get("files") or {}), **offs}.items()
                      if now - float(o.get("at") or 0) < FILE_KEEP_S and os.path.exists(p)}
        d["scan_at"] = int(now)
        _write(d)
    return new


# ------------------------------------------------------------------ letture
def _groups(items):
    g = {}
    for x in items.values():
        g.setdefault(x["key"], []).append(x)
    for v in g.values():
        v.sort(key=lambda x: (x["at"], x["version"]))
    return g


def _state_item(x, versions):
    return {"id": x["id"], "session": x["session"], "title": x["title"], "kind": x["kind"], "mime": x["mime"], "path": x["path"],
            "size": x["size"], "at": x["at"], "duration_s": x["duration_s"], "version": x["version"], "versions": versions,
            "changed": x["changed"], "seen_at": x["seen_at"]}


def summary(now=None):
    """/state.results: {n_unseen, items} — l'ultima versione di ogni lavoro non visto, la piu' recente per prima, al piu'
    STATE_MAX. None con l'indice vuoto."""
    items = _read().get("items") or {}
    if not items:
        return None
    heads = []
    for v in _groups(items).values():
        last = v[-1]
        if last.get("seen_at") is None:
            heads.append(_state_item(last, max(x["version"] for x in v)))
    heads.sort(key=lambda x: -x["at"])
    return {"n_unseen": len(heads), "items": heads[:STATE_MAX]}


def _replaced(x):
    if x.get("host") not in (None, "local"):
        return None
    m = _mtime(x["path"])
    if m is None:
        return True
    try:
        size = os.path.getsize(x["path"])
    except OSError:
        return True
    return bool((x.get("size") is not None and size != x["size"]) or (x.get("mtime") is not None and m > x["mtime"] + 2))


def view(max_bytes=60000):
    """op results: {groups, n_unseen, more}. groups[]: {title, project, session, kind, at, versions, unseen, items}, il
    lavoro toccato per ultimo per primo; items[] dalla versione piu' nuova: i campi di /state piu' caption, files, notes,
    replaced. Oltre max_bytes escono i lavori piu' vecchi (more = true)."""
    items = _read().get("items") or {}
    out = []
    for v in _groups(items).values():
        versions = max(x["version"] for x in v)
        rows = [dict(_state_item(x, versions), caption=x.get("caption"), files=x.get("files") or [], notes=x.get("notes") or [],
                     replaced=_replaced(x)) for x in reversed(v)]
        last = v[-1]
        out.append({"title": last["title"], "project": last["project"], "session": last["session"], "kind": last["kind"],
                    "at": last["at"], "versions": versions, "unseen": last.get("seen_at") is None, "items": rows})
    out.sort(key=lambda g: -g["at"])
    n_unseen = sum(1 for g in out if g["unseen"])
    more = False
    while out and len(json.dumps({"groups": out, "n_unseen": n_unseen, "more": True}, ensure_ascii=False).encode()) > max_bytes:
        out.pop()
        more = True
    return {"groups": out, "n_unseen": n_unseen, "more": more}


def get(rid):
    """(la consegna, il numero piu' alto del suo lavoro) o (None, 0)."""
    items = _read().get("items") or {}
    x = items.get(str(rid or ""))
    if not x:
        return None, 0
    return x, max(y["version"] for y in items.values() if y["key"] == x["key"])


def find_path(path):
    """La consegna piu' recente che ha `path` fra i suoi file, o None: l'op file serve solo i file dell'indice."""
    hits = [x for x in (_read().get("items") or {}).values()
            if x.get("path") == path or any(f.get("path") == path for f in x.get("files") or [])]
    return max(hits, key=lambda x: x["at"]) if hits else None


def seen(rid, now=None):
    """Visto su una consegna e sulle versioni prima di lei. False se l'id non c'e'."""
    now = int(now or time.time())
    with _locked():
        d = _read()
        items = d.get("items") or {}
        x = items.get(str(rid or ""))
        if not x:
            return False
        for y in items.values():
            if y["key"] == x["key"] and (y["at"], y["version"]) <= (x["at"], x["version"]) and y.get("seen_at") is None:
                y["seen_at"] = now
        _write(d)
    return True


def add_note(rid, text, pos_s, now=None):
    with _locked():
        d = _read()
        x = (d.get("items") or {}).get(str(rid or ""))
        if not x:
            return None
        note = {"at": int(now or time.time()), "text": text, "pos_s": pos_s}
        x["notes"] = (x.get("notes") or [])[-(NOTES_KEEP - 1):] + [note]
        _write(d)
    return note


def scan_due(now=None):
    return (now or time.time()) - float(_read().get("scan_at") or 0) >= SCAN_EVERY_S


def main(argv):
    if argv and argv[0] == "scan":
        live = None
        if not sys.stdin.isatty():
            try:
                raw = sys.stdin.read()
                live = json.loads(raw) if raw.strip() else None
            except ValueError:
                live = None
        try:
            os.nice(19)
        except OSError:
            pass
        n = scan(live)
        print(n)
        return 0
    print(json.dumps(summary(), ensure_ascii=False, indent=None if "--json" in argv else 2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
