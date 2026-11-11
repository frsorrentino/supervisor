#!/usr/bin/env python3
"""Il diff dal telefono (contratto 1.72, 15/10/2026): quello che e' cambiato nei file, per l'app.

Il diff di un passaggio di modifica (Edit, MultiEdit, Write, NotebookEdit) dal `toolUseResult` della trascrizione:
`entry_diff` → {path, kind, added, removed, hunks, cut}. cm-core lo mette in `diff` delle voci di `transcript` e di
/tail (ENTRY_LINES righe); l'op `diff` lo da' intero (FULL_LINES).

La vista «Modifiche» di una cartella: `view` con git (tutto rispetto a HEAD, i file nuovi non ignorati compresi; i file
ignorati non si leggono mai; ogni chiamata a git ha GIT_TIMEOUT_S secondi), `view_from_passages` fuori da git (i
passaggi della conversazione) → {source, repo, base, files, totals, cut, at}. CM_GIT_BIN sostituisce git (le prove).
`counts` da' i soli conteggi, per la pillola di /state.

Regole (contratto 1.72, details/relay.md): ogni riga passa da redact_preview (cm-relay-state, la regola delle anteprime)
col segno in testa intatto; dei nomi sensibili solo nome, stato e conteggi. Prove: tests/diff-verify.py.
"""
import difflib
import fnmatch
import functools
import importlib.util
import os
import re
import stat
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENTRY_LINES = 200          # le righe di un passaggio nella voce; il resto con l'op diff
ENTRY_BYTES = 16000        # e i byte: una voce sola sta sempre in /tail (32 KB) e in una pagina di transcript (60 KB)
FULL_LINES = 5000          # un passaggio intero (op diff) o un file solo (changes file=)
RESULT_BYTES = 1000000     # i byte delle righe in un risultato: la busta cifrata resta sotto 1,5 MB, come /file
SENSITIVE = (".env*", "*.pem", "*.key", "id_*", "*.p12", "credentials*")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
WIN_ABS = re.compile(r"[A-Za-z]:[\\/]|\\\\")   # un percorso assoluto di Windows: una sessione su win
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_STATE = None


def _state():
    """cm-relay-state (redact_preview), caricato alla prima riga da mascherare."""
    global _STATE
    if _STATE is None:
        spec = importlib.util.spec_from_file_location("cm_relay_state", HERE / "cm-relay-state.py")
        _STATE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_STATE)
    return _STATE


# ------------------------------------------------------------------ hunk
def hunk(old_start, old_lines, new_start, new_lines, lines):
    return {"old_start": old_start, "old_lines": old_lines, "new_start": new_start, "new_lines": new_lines, "lines": lines}


def clean_lines(lines):
    """Le righe come le vuole il contratto: «-», «+» o spazio in testa. Via «\\ No newline at end of file» e il «\\r»
    finale delle righe CRLF (sul telefono sarebbe un segno strano)."""
    out = []
    for raw in lines:
        s = str(raw)
        if s.endswith("\r"):
            s = s[:-1]
        if s[:1] in ("-", "+", " "):
            out.append(s)
    return out


def hunks_from_patch(patch):
    """structuredPatch di Claude Code ({oldStart, oldLines, newStart, newLines, lines}) → hunk del contratto."""
    out = []
    for h in patch if isinstance(patch, list) else []:
        if not isinstance(h, dict):
            continue
        lines = clean_lines(h.get("lines") or [])
        if lines:
            out.append(hunk(int(h.get("oldStart") or 0), int(h.get("oldLines") or 0), int(h.get("newStart") or 0),
                            int(h.get("newLines") or 0), lines))
    return out


def hunks_from_text(text):
    """Un file nuovo: tutte le righe come aggiunte, in un hunk solo (@@ -0,0 +1,n @@). Split su «\\n» soltanto:
    splitlines() spezzerebbe anche su \\x0c e \\u2028."""
    lines = str(text).split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = clean_lines("+" + s for s in lines)
    return [hunk(0, 0, 1, len(lines), lines)] if lines else []


def hunks_from_sources(old, new):
    """NotebookEdit non scrive structuredPatch: il diff della cella (old_source → new_source), numerato nella cella."""
    a = str(old).split("\n") if old else []
    b = str(new).split("\n") if new else []
    out, h = [], None
    for s in difflib.unified_diff(a, b, n=3, lineterm=""):
        m = HUNK_RE.match(s)
        if m:
            h = hunk(int(m[1]), int(m[2] if m[2] is not None else 1), int(m[3]), int(m[4] if m[4] is not None else 1), [])
            out.append(h)
        elif h is not None:
            h["lines"].append(s)
    for x in out:
        x["lines"] = clean_lines(x["lines"])
    return out


def count(hunks):
    """(aggiunte, tolte) di tutte le righe."""
    lines = [s for h in hunks for s in h["lines"]]
    return sum(1 for s in lines if s[:1] == "+"), sum(1 for s in lines if s[:1] == "-")


def cut_hunks(hunks, max_lines, max_bytes):
    """(hunk, tagliato): al piu' max_lines righe e max_bytes byte UTF-8, in ordine. L'hunk tagliato tiene la sua
    testata (old_start, old_lines, new_start, new_lines dicono l'hunk intero); quelli dopo cadono."""
    out, n, size = [], 0, 0
    for h in hunks:
        keep = []
        for s in h["lines"]:
            b = len(s.encode("utf-8")) + 1
            if n >= max_lines or size + b > max_bytes:
                if keep:
                    out.append(dict(h, lines=keep))
                return out, True
            keep.append(s)
            n += 1
            size += b
        out.append(dict(h, lines=keep))
    return out, False


def sensitive(path):
    """Un nome da segreti (.env*, *.pem, *.key, id_*, *.p12, credentials*): solo nome, stato e conteggi."""
    name = re.split(r"[\\/]", str(path or ""))[-1].lower()
    return any(fnmatch.fnmatchcase(name, p) for p in SENSITIVE)


@functools.lru_cache(maxsize=20000)
def redact_line(line):
    """Una riga di diff con la regola delle anteprime (1.56), il segno in testa escluso: «+» e «-» sono nella classe
    delle stringhe lunghe da chiave e sparirebbero insieme al segreto. In cache: /tail rilegge le stesse righe."""
    return line[:1] + _state().redact_preview(line[1:])


def redact_hunks(hunks):
    return [dict(h, lines=[redact_line(s) for s in h["lines"]]) for h in hunks]


def redact_diff(d):
    """Il `diff` di una voce con le righe mascherate; None resta None."""
    return dict(d, hunks=redact_hunks(d["hunks"])) if d else d


# ------------------------------------------------------------------ il passaggio di una voce
def entry_diff(name, inp, tur, cwd, max_lines=ENTRY_LINES, max_bytes=ENTRY_BYTES):
    """1.72: il diff di un passaggio dal toolUseResult della sua riga, {path, kind, added, removed, hunks, cut}, o None
    (un errore, una trascrizione di prima di structuredPatch). kind: edit | create (Write di un file nuovo, tutte le
    righe di content) | overwrite. added e removed contano tutte le righe, anche quelle tolte dal taglio. Le righe NON
    sono ancora mascherate: lo fa chi le pubblica (redact_diff), solo per le voci che escono."""
    if name not in WRITE_TOOLS or not isinstance(tur, dict):
        return None
    inp = inp if isinstance(inp, dict) else {}
    path = str(tur.get("filePath") or tur.get("notebook_path") or inp.get("file_path") or inp.get("notebook_path") or "")
    if not path:
        return None
    if cwd and not os.path.isabs(path) and not WIN_ABS.match(path):
        path = os.path.normpath(os.path.join(str(cwd), path))
    if name == "NotebookEdit":
        if tur.get("error"):
            return None
        old = "" if tur.get("edit_mode") == "insert" else tur.get("old_source") or ""
        new = "" if tur.get("edit_mode") == "delete" else tur.get("new_source") or ""
        kind, hunks = "edit", hunks_from_sources(old, new)
    elif name == "Write" and tur.get("type") == "create":
        kind, hunks = "create", hunks_from_text(tur.get("content") or "")
    elif isinstance(tur.get("structuredPatch"), list):
        kind, hunks = ("overwrite" if name == "Write" else "edit"), hunks_from_patch(tur["structuredPatch"])
    else:
        return None
    added, removed = count(hunks)
    hunks, cut = ([], False) if sensitive(path) else cut_hunks(hunks, max_lines, max_bytes)
    return {"path": path, "kind": kind, "added": added, "removed": removed, "hunks": hunks, "cut": cut}


# ------------------------------------------------------------------ la vista «Modifiche» (op changes)
GIT_BIN = os.environ.get("CM_GIT_BIN") or "git"
GIT_TIMEOUT_S = 5          # ogni chiamata a git; oltre, «git did not answer in 5 s»
VIEW_FILES = 50            # la vista: al piu' 50 file...
VIEW_LINES = 2000          # ...e 2000 righe in tutto
BIG_FILE = 1024 * 1024     # oltre, solo nome, stato e conteggi
GIT_CFG = ["-c", "core.quotePath=false", "-c", "diff.suppressBlankEmpty=false"]   # nomi in UTF-8, righe vuote con lo spazio
STATUS = {"A": "added", "D": "deleted", "R": "renamed", "C": "added"}   # il resto (M, T, U) e' modified


class GitTimeout(Exception):
    """git non ha risposto entro GIT_TIMEOUT_S secondi."""


class NotListed(Exception):
    """`file=` con un percorso che non e' fra le modifiche: non si legge niente."""


def git(args, cwd):
    """stdout (bytes) di `git -C cwd …`, o None se git esce con errore o manca. GitTimeout oltre GIT_TIMEOUT_S.
    GIT_OPTIONAL_LOCKS=0: una lettura non prende index.lock, e il commit di una sessione nella stessa cartella non
    trova il lock occupato da noi."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        p = subprocess.run([GIT_BIN, "-C", str(cwd), *args], capture_output=True, timeout=GIT_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        raise GitTimeout() from None
    except OSError:
        return None
    return p.stdout if p.returncode == 0 else None


def _dec(b):
    return b.decode("utf-8", "replace")


def _raw_numstat(out):
    """`diff --raw --numstat -z -M`: ([(lettera, vecchio, nuovo)] nell'ordine di git, {nuovo: (aggiunte, tolte)})."""
    toks, raw, num, i = out.split(b"\0"), [], {}, 0
    while i < len(toks):
        t = toks[i]
        if not t:
            i += 1
        elif t.startswith(b":"):
            st = _dec(t.split()[-1][:1])
            if st in ("R", "C"):
                raw.append((st, _dec(toks[i + 1]), _dec(toks[i + 2])))
                i += 3
            else:
                raw.append((st, _dec(toks[i + 1]), _dec(toks[i + 1])))
                i += 2
        else:
            a, r, p = t.split(b"\t", 2)
            if p:
                new, i = _dec(p), i + 1
            else:
                new, i = _dec(toks[i + 2]), i + 3
            num[new] = (_dec(a), _dec(r))
    return raw, num


def _lines_of(root, rel):
    """(righe, binario) di un file non tracciato, letto a blocchi e senza seguire i link: un link conta una riga,
    il suo bersaglio, come in git. Binario = un byte nullo nei primi 8000 (la regola di git)."""
    p = os.path.join(root, rel)
    try:
        st = os.lstat(p)
    except OSError:
        return 0, False
    if stat.S_ISLNK(st.st_mode):
        return 1, False
    if not stat.S_ISREG(st.st_mode):
        return 0, False
    n, last = 0, b""
    try:
        with open(p, "rb") as f:
            head = f.read(8000)
            if b"\0" in head:
                return 0, True
            b = head
            while b:
                n += b.count(b"\n")
                last = b[-1:]
                b = f.read(65536)
    except OSError:
        return 0, False
    return n + (1 if last and last != b"\n" else 0), False


def _text_of(root, rel):
    """Il testo di un file non tracciato (fino a BIG_FILE), o il bersaglio se e' un link: mai il file a cui punta."""
    p = os.path.join(root, rel)
    try:
        if os.path.islink(p):
            return os.readlink(p)
        with open(p, "rb") as f:
            return _dec(f.read(BIG_FILE + 1)[:BIG_FILE])
    except OSError:
        return None


def _inventory(root, base):
    """Tutti i file cambiati rispetto a HEAD, file nuovi non ignorati compresi, in ordine di percorso:
    [{path, old, status, added, removed, untracked}]. Senza commit (base None) ogni file e' nuovo."""
    files = []
    if base:
        out = git([*GIT_CFG, "diff", "--raw", "--numstat", "-z", "-M", "HEAD"], root)
        if out is None:
            return None
        raw, num = _raw_numstat(out)
        for st, old, new in raw:
            a, r = num.get(new, ("0", "0"))
            status = STATUS.get(st, "modified")
            if a == "-" and status in ("added", "modified"):
                status = "binary"
            files.append({"path": new, "old": old, "status": status, "added": int(a) if a.isdigit() else 0,
                          "removed": int(r) if r.isdigit() else 0, "untracked": False})
        listing = git(["ls-files", "-z", "--others", "--exclude-standard"], root)
    else:
        listing = git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], root)
    # revisione finale (16/10): contare le righe dei file nuovi e' Python, fuori dai 5 s di git — stesso tempo massimo,
    # cosi' un npm install prima del .gitignore non ferma la pillola (che gira nella pubblicazione dello stato) ne' la vista
    deadline = time.monotonic() + GIT_TIMEOUT_S
    for p in sorted({_dec(x) for x in (listing or b"").split(b"\0") if x}):
        if time.monotonic() > deadline:
            raise GitTimeout()
        n, binary = _lines_of(root, p)
        files.append({"path": p, "old": p, "status": "binary" if binary else "added", "added": n, "removed": 0, "untracked": True})
    files.sort(key=lambda f: f["path"])
    return files


def _unquote(s):
    """Un nome fra virgolette alla C, come lo scrive git per «"», «\\» e i caratteri di controllo."""
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        return s
    esc = {"n": 10, "t": 9, "\\": 92, '"': 34, "a": 7, "b": 8, "f": 12, "r": 13, "v": 11}
    out, body, i = bytearray(), s[1:-1], 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body) and body[i + 1] in esc:
            out.append(esc[body[i + 1]])
            i += 2
        elif c == "\\" and body[i + 1:i + 4].isdigit():
            out.append(int(body[i + 1:i + 4], 8))
            i += 4
        else:
            out += c.encode("utf-8")
            i += 1
    return out.decode("utf-8", "replace")


def _patch_path(s, prefix):
    s = s[:-1] if s.endswith("\t") else s   # git mette un TAB dopo un nome con spazi
    if s == "/dev/null":
        return None
    s = _unquote(s)
    return s[len(prefix):] if s.startswith(prefix) else s


def parse_patch(text):
    """{percorso: [hunk]} dall'output di `git diff`: il percorso da «+++ b/…», o da «--- a/…» per un file eliminato.
    Un hunk prende esattamente le righe che la sua testata conta; «\\» e il «\\r» finale non entrano."""
    out, path, old_p, h, need = {}, None, None, None, [0, 0]
    for line in text.split("\n"):
        if h is not None and (need[0] > 0 or need[1] > 0):
            if line.startswith("\\"):
                continue
            s = line[:-1] if line.endswith("\r") else line
            s = s or " "
            if s[0] == " ":
                need[0] -= 1
                need[1] -= 1
            elif s[0] == "-":
                need[0] -= 1
            elif s[0] == "+":
                need[1] -= 1
            else:
                h = None
                continue
            h["lines"].append(s)
            continue
        h = None
        if line.startswith("diff --git "):
            path, old_p = None, None
        elif line.startswith("--- "):
            old_p = _patch_path(line[4:], "a/")
        elif line.startswith("+++ "):
            new_p = _patch_path(line[4:], "b/")
            path = new_p if new_p is not None else old_p
        elif path is not None:
            m = HUNK_RE.match(line)
            if m:
                ol, nl = int(m[2] if m[2] is not None else 1), int(m[4] if m[4] is not None else 1)
                h, need = hunk(int(m[1]), ol, int(m[3]), nl, []), [ol, nl]
                out.setdefault(path, []).append(h)
    return out


def _size(p):
    try:
        st = os.lstat(p)
    except OSError:
        return None
    return st.st_size if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode) else None


def _shows_lines(f):
    """Un file della vista porta righe? No per i binari, i nomi sensibili (anche il vecchio nome di un rinominato) e i
    file oltre BIG_FILE."""
    return f["status"] != "binary" and not sensitive(f["path"]) and not sensitive(f.get("old")) \
        and (f["size"] is None or f["size"] <= BIG_FILE)


def _pick(files, want):
    """I file da mostrare: quello chiesto con file= (NotListed se non e' fra le modifiche: non si legge niente), o i
    primi VIEW_FILES."""
    if want is None:
        return files[:VIEW_FILES]
    listed = [f for f in files if f["path"] == want]
    if not listed:
        raise NotListed(want)
    return listed


def _assemble(listed, files, want, now, source, repo, base, full_of):
    """La forma del contratto: un file solo (want) fino a FULL_LINES righe, altrimenti VIEW_LINES in tutto, e mai oltre
    RESULT_BYTES; righe mascherate. full_of(f) da' gli hunk interi di un file che ne puo' mostrare."""
    lines_left, bytes_left, out = (FULL_LINES if want is not None else VIEW_LINES), RESULT_BYTES, []
    for f in listed:
        hunks, cut = [], False
        if _shows_lines(f):
            hunks, cut = cut_hunks(full_of(f), lines_left, bytes_left)
            cut = cut or bool(f.get("trunc"))   # un passaggio gia' tagliato a FULL_LINES (fuori da git)
            lines_left -= sum(len(h["lines"]) for h in hunks)
            bytes_left -= sum(len(s.encode("utf-8")) + 1 for h in hunks for s in h["lines"])
        out.append({"path": f["path"], "status": f["status"], "added": f["added"], "removed": f["removed"],
                    "size": f["size"], "hunks": redact_hunks(hunks), "cut": cut})
    scope = listed if want is not None else files
    return {"source": source, "repo": repo, "base": base, "files": out,
            "totals": {"files": len(scope), "added": sum(f["added"] for f in scope), "removed": sum(f["removed"] for f in scope)},
            "cut": (want is None and len(files) > VIEW_FILES) or any(f["cut"] for f in out), "at": int(now or time.time())}


def repo_of(cwd):
    """(radice, hash corto di HEAD o None) della cartella, o None fuori da git (o senza git)."""
    out = git(["rev-parse", "--show-toplevel"], cwd)
    if out is None:
        return None
    root = _dec(out).strip()
    head = git(["rev-parse", "--verify", "-q", "--short", "HEAD"], root)
    return root, ((_dec(head).strip() or None) if head else None)


def counts(cwd):
    """La pillola di /state senza l'ora: {files, added, removed}, o None fuori da git o senza modifiche."""
    rb = repo_of(cwd)
    files = _inventory(*rb) if rb else None
    if not files:
        return None
    return {"files": len(files), "added": sum(f["added"] for f in files), "removed": sum(f["removed"] for f in files)}


def view(cwd, want=None, now=None):
    """La vista «Modifiche» con git, o None fuori da git (o senza git). GitTimeout se una chiamata supera
    GIT_TIMEOUT_S; NotListed se `want` non e' fra le modifiche. Il diff si chiede solo per i file mostrati."""
    rb = repo_of(cwd)
    files = _inventory(*rb) if rb else None
    if files is None:
        return None
    root, base = rb
    for f in files:
        f["size"] = None if f["status"] == "deleted" else _size(os.path.join(root, f["path"]))
    listed = _pick(files, want)
    tracked = [f for f in listed if not f["untracked"] and _shows_lines(f)]
    patch = {}
    if tracked:
        paths = sorted({p for f in tracked for p in (f["path"], f["old"])})
        out = git(["--literal-pathspecs", *GIT_CFG, "diff", "HEAD", "--no-color", "--no-ext-diff", "--no-textconv",
                   "--src-prefix=a/", "--dst-prefix=b/", "-M", "-U3", "--", *paths], root)
        patch = parse_patch(_dec(out or b""))

    def full_of(f):
        if f["untracked"]:
            text = _text_of(root, f["path"])
            return hunks_from_text(text) if text else []
        return patch.get(f["path"], [])
    return _assemble(listed, files, want, now, "git", root, base, full_of)


def view_from_passages(diffs, cwd, want=None, now=None):
    """Fuori da git (source «transcript»): per ogni file i passaggi della conversazione corrente in ordine, ciascuno
    con il suo diff; i totali sono la somma. `path` relativo alla cartella della sessione quando ci sta dentro."""
    byp = {}
    for d in diffs:
        p = str(d.get("path") or "")
        rel = os.path.relpath(p, cwd) if cwd and os.path.isabs(p) and (p + "/").startswith(str(cwd).rstrip("/") + "/") else p
        f = byp.get(rel)
        if f is None:
            f = byp[rel] = {"path": rel, "old": rel, "status": "added" if d.get("kind") == "create" else "modified",
                            "added": 0, "removed": 0, "size": _size(p) if os.path.isabs(p) else None, "hunks": []}
        f["added"] += int(d.get("added") or 0)
        f["removed"] += int(d.get("removed") or 0)
        f["hunks"] += d.get("hunks") or []
        f["trunc"] = f.get("trunc") or bool(d.get("cut"))
    files = list(byp.values())
    return _assemble(_pick(files, want), files, want, now, "transcript", None, None, lambda f: f["hunks"])
