#!/usr/bin/env python3
"""supervisor cerca — cerca nel lavoro passato, in tre passi (20/11/2026, supervisor-cerca-lavoro-19-11; idea 3 da claude-mem).

  supervisor cerca TESTO [--limit N] [--json]   1. l'indice: una riga per risultato, con id, data, sessione, tipo, estratto
  supervisor cerca --timeline ID [--n N]        2. le voci intorno, della stessa sessione (o dello stesso progetto, per un commit)
  supervisor cerca --dettagli ID                3. il testo intero, con sessione, cartella e data
  supervisor cerca --index                      aggiorna l'indice e basta (il relay lo lancia con nice e ionice ogni cerca.index_every_s)

L'indice e' SQLite FTS5 in <state_dir>/cerca/index.db, incrementale: di ogni trascrizione dei progetti (gli account di
config, nell'ambito delle sessioni) legge solo le righe nuove. Entrano i prompt della persona, la risposta finale di ogni
turno (l'ultimo testo prima del prompt dopo: un turno che continua aggiorna la sua, non ne aggiunge un'altra), la sua riga
«Esito:» e i commit dei progetti. Mai l'uscita degli strumenti, le risposte intermedie, i subagenti, ne' il testo <private>.
Maiuscole e accenti non contano (unicode61 remove_diacritics). Ogni ricerca aggiorna prima l'indice (pochi millisecondi
quando non c'e' niente di nuovo).
"""
import datetime as dt
import fcntl
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_ce", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
STATE = Path(cm.expand(CFG["state_dir"]))
DB = STATE / "cerca" / "index.db"
ESITO_RE = re.compile(r"(?im)^\s*[*_#> ]*(?:esito|outcome)[*_]*\s*:\s*[*_]*\s*(.+)$")
LIMIT = 20
TIMELINE_N = 5
COMMITS_MAX = 500
SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, off INTEGER NOT NULL DEFAULT 0, turn TEXT, turn_at INTEGER);
CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, at INTEGER, session TEXT,
                                 sid TEXT, project TEXT, entry TEXT, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS docs_sid ON docs (sid, at);
CREATE INDEX IF NOT EXISTS docs_project ON docs (project, at);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, content='docs', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN INSERT INTO fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN INSERT INTO fts(fts, rowid, text) VALUES ('delete', old.id, old.text); END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
  INSERT INTO fts(fts, rowid, text) VALUES ('delete', old.id, old.text); INSERT INTO fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TABLE IF NOT EXISTS commits (project TEXT PRIMARY KEY, last TEXT);
"""


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _epoch(ts):
    try:
        return int(dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def _prompt_text(d):
    """Il testo di una riga user scritta dalla persona, o None (strumenti, meta, sidechain, involucri di comandi)."""
    if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain") or d.get("isCompactSummary") or d.get("toolUseResult") is not None:
        return None
    o = d.get("origin")
    if isinstance(o, dict) and o.get("kind") != "human":
        return None
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return None
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str):
        return None
    c = re.sub(r"<system-reminder>.*?</system-reminder>", "", c, flags=re.S).strip()
    if not c or c.startswith("<"):
        return None
    return c


def transcripts():
    out = []
    for a in (CFG.get("accounts") or {}).values():
        for f in (Path(cm.expand(a.get("config_dir") or "~/.claude")) / "projects").glob("*/*.jsonl"):
            try:
                if cm.transcript_in_scope(CFG, f):
                    out.append(f)
            except Exception:   # noqa: BLE001
                out.append(f)
    return out


def session_names():
    """{session_id: nome tmux} dal registro (righe start ed end che lo portano)."""
    out = {}
    try:
        with open(STATE / "ledger.jsonl", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8_000_000))
            for ln in f.read().decode("utf-8", "replace").splitlines():
                if '"tmux"' not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("session_id") and r.get("tmux"):
                    out[r["session_id"]] = r["tmux"]
    except OSError:
        pass
    return out


def _put(con, key, kind, at, session, sid, project, entry, text):
    text = cm.private_strip(text)
    con.execute("INSERT INTO docs (key, kind, at, session, sid, project, entry, text) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET at=excluded.at, entry=excluded.entry, text=excluded.text WHERE docs.text != excluded.text",
                (key, kind, at, session, sid, project, entry, text))


def index_file(con, path, names):
    """Le righe nuove di una trascrizione: prompt, risposta finale del turno, la sua riga «Esito:»."""
    p = str(path)
    try:
        size = os.path.getsize(p)
    except OSError:
        return 0
    row = con.execute("SELECT off, turn, turn_at FROM files WHERE path=?", (p,)).fetchone()
    off, turn, turn_at = (row["off"], row["turn"], row["turn_at"]) if row else (0, None, None)
    if size < off:   # riscritta: da capo
        off, turn, turn_at = 0, None, None
    if size == off:
        return 0
    n = 0
    with open(p, "rb") as f:
        f.seek(off)
        data = f.read()
    end = data.rfind(b"\n")
    if end < 0:
        return 0
    for ln in data[:end + 1].splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        sid = str(d.get("sessionId") or Path(p).stem)
        cwd = str(d.get("cwd") or "")
        sess = names.get(sid) or os.path.basename(cwd.rstrip("/")) or Path(p).parent.name
        at = _epoch(d.get("timestamp"))
        uid = str(d.get("uuid") or "")
        pt = _prompt_text(d)
        if pt is not None:
            turn, turn_at = uid, at
            _put(con, f"{sid}:{uid}", "prompt", at, sess, sid, cwd, f"{uid}.0", pt)
            n += 1
            continue
        if d.get("type") != "assistant" or d.get("isSidechain") or not turn:
            continue
        content = (d.get("message") or {}).get("content")
        for i, b in enumerate(content if isinstance(content, list) else []):
            if isinstance(b, dict) and b.get("type") == "text" and str(b.get("text") or "").strip():
                t = b["text"].strip()
                _put(con, f"{sid}:{turn}:r", "reply", at, sess, sid, cwd, f"{uid}.{i}", t)
                m = ESITO_RE.search(t)
                if m:
                    _put(con, f"{sid}:{turn}:e", "outcome", at, sess, sid, cwd, f"{uid}.{i}", m.group(1).strip(" *_"))
                else:
                    con.execute("DELETE FROM docs WHERE key=?", (f"{sid}:{turn}:e",))
                n += 1
    con.execute("INSERT INTO files (path, off, turn, turn_at) VALUES (?,?,?,?) ON CONFLICT(path) DO UPDATE SET off=excluded.off, "
                "turn=excluded.turn, turn_at=excluded.turn_at", (p, off + end + 1, turn, turn_at))
    return n


def _head(proj):
    """Lo sha di HEAD con una sola git rev-parse, o None (repository senza commit, git che non risponde)."""
    try:
        r = subprocess.run(["git", "-C", proj, "rev-parse", "-q", "--verify", "HEAD"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = r.stdout.strip()
    return sha if r.returncode == 0 and sha else None


def index_commits(con):
    """I commit nuovi delle cartelle che l'indice conosce (quelle che sono repository git). 20/11/2026
    (supervisor-cerca-indice-leggero-20-11): git log solo dove HEAD e' cambiato dall'ultimo giro; prima rileggeva a ogni giro
    la storia di tutti i progetti (fino a circa 170) e il Chromebook rallentava a ondate."""
    n = 0
    projects = [r[0] for r in con.execute("SELECT DISTINCT project FROM docs WHERE kind != 'commit' AND project != ''")]
    for proj in projects:
        if not os.path.isdir(os.path.join(proj, ".git")):
            continue
        last = (con.execute("SELECT last FROM commits WHERE project=?", (proj,)).fetchone() or [None])[0]
        head = _head(proj)
        if not head or head == last:
            continue
        try:
            out = subprocess.run(["git", "-C", proj, "log", "--format=%H%x1f%ct%x1f%s", f"-n{COMMITS_MAX}", head], capture_output=True,
                                 text=True, timeout=30).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        rows = [l.split("\x1f") for l in out.splitlines() if l.count("\x1f") == 2]
        if not rows:
            continue
        for sha, ct, subj in rows:
            if sha == last:
                break
            _put(con, f"commit:{proj}:{sha}", "commit", int(ct), os.path.basename(proj.rstrip("/")), "", proj, sha[:12], subj)
            n += 1
        con.execute("INSERT INTO commits (project, last) VALUES (?,?) ON CONFLICT(project) DO UPDATE SET last=excluded.last", (proj, rows[0][0]))
    return n


class Lock:
    def __enter__(self):
        DB.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(DB.parent / ".lock", "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        self.f.close()


def update():
    """L'indice al passo: le righe nuove di ogni trascrizione, poi i commit. Torna quante voci ha toccato."""
    with Lock():
        con = connect()
        try:
            names = session_names()
            n = sum(index_file(con, f, names) for f in transcripts())
            n += index_commits(con)
            con.commit()
            return n
        finally:
            con.close()


def kind_label(k):
    return M(f"cerca.kind_{k}")


def when(at):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else "-"


def query_of(text):
    """Le parole come termini FTS5 fra virgolette (niente sintassi dalla persona); tutte devono esserci."""
    words = re.findall(r"\w+", text, re.U)
    return " ".join('"' + w.replace('"', "") + '"' for w in words)


def search(text, limit=LIMIT):
    q = query_of(text)
    if not q:
        return []
    con = connect()
    try:
        rows = con.execute("SELECT d.id, d.kind, d.at, d.session, d.project, d.sid, snippet(fts, 0, '', '', ' ', 14) AS snip "
                           "FROM fts JOIN docs d ON d.id = fts.rowid WHERE fts MATCH ? ORDER BY d.at DESC, d.id DESC LIMIT ?", (q, limit)).fetchall()
    finally:
        con.close()
    return [{"id": r["id"], "at": r["at"], "date": when(r["at"]), "session": r["session"], "kind": kind_label(r["kind"]),
             "project": r["project"], "sid": r["sid"], "snippet": " ".join(str(r["snip"] or "").split())} for r in rows]


def doc(con, i):
    return con.execute("SELECT * FROM docs WHERE id=?", (int(i),)).fetchone()


def timeline(i, n=TIMELINE_N):
    con = connect()
    try:
        d = doc(con, i)
        if not d:
            return None
        where, arg = ("sid=?", d["sid"]) if d["sid"] else ("project=? AND kind='commit'", d["project"])
        before = con.execute(f"SELECT * FROM docs WHERE {where} AND (at < ? OR (at = ? AND id < ?)) ORDER BY at DESC, id DESC LIMIT ?",
                             (arg, d["at"], d["at"], d["id"], n)).fetchall()
        after = con.execute(f"SELECT * FROM docs WHERE {where} AND (at > ? OR (at = ? AND id > ?)) ORDER BY at, id LIMIT ?",
                            (arg, d["at"], d["at"], d["id"], n)).fetchall()
        return d, list(reversed(before)), after
    finally:
        con.close()


def _line(r, mark=False):
    t = " ".join(str(r["text"] or "").split())
    t = t if len(t) <= 140 else t[:140].rsplit(" ", 1)[0]
    return f"{'→ ' if mark else '  '}#{r['id']} {when(r['at'])} · {kind_label(r['kind'])} · {t}"


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]

    def opt(name, default=None):
        if name in args:
            k = args.index(name)
            v = args[k + 1] if k + 1 < len(args) else None
            del args[k:k + 2]
            return v
        return default
    tl, dd = opt("--timeline"), opt("--dettagli") or opt("--details")
    limit, n = int(opt("--limit", LIMIT) or LIMIT), int(opt("--n", TIMELINE_N) or TIMELINE_N)
    try:
        update()
    except sqlite3.OperationalError as e:
        print(M("cerca.no_fts", error=e), file=sys.stderr)
        return 3
    if "--index" in args:
        return 0
    if tl:
        r = timeline(tl, n)
        if not r:
            print(M("cerca.not_found", id=tl), file=sys.stderr)
            return 1
        d, before, after = r
        if as_json:
            print(json.dumps([dict(x) for x in before + [d] + after], ensure_ascii=False))
            return 0
        print(M("cerca.timeline_head", session=d["session"] or "-", sid=d["sid"] or "-", project=d["project"] or "-"))
        for x in before:
            print(_line(x))
        print(_line(d, True))
        for x in after:
            print(_line(x))
        return 0
    if dd:
        con = connect()
        try:
            d = doc(con, dd)
        finally:
            con.close()
        if not d:
            print(M("cerca.not_found", id=dd), file=sys.stderr)
            return 1
        if as_json:
            print(json.dumps(dict(d), ensure_ascii=False))
            return 0
        print(M("cerca.details_head", id=d["id"], kind=kind_label(d["kind"]), when=when(d["at"]), session=d["session"] or "-",
                project=d["project"] or "-", sid=d["sid"] or "-", entry=d["entry"] or "-"))
        print()
        print(d["text"])
        return 0
    text = " ".join(args)
    hits = search(text, limit)
    if as_json:
        print(json.dumps(hits, ensure_ascii=False))
        return 0
    if not hits:
        print(M("cerca.none", text=text))
        return 1
    for h in hits:
        print(f"#{h['id']} {h['date']} · {h['session']} · {h['kind']} · {h['snippet']}")
    print(M("cerca.next"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
