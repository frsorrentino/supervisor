#!/usr/bin/env python3
"""Verifica cm-diff.py (contratto 1.72, il diff dal telefono). Niente rete, niente tmux: funzioni pure, e git vero in
una cartella temporanea per la vista.

D1  Edit con structuredPatch → il diff del contratto (la voce a8.0 della fixture)
D2  Write di un file nuovo: tutte le righe di content, senza «\\r»
D3  Write su un file esistente; percorso relativo; «\\ No newline»; percorso di Windows
D4  taglio a 200 righe con la testata dell'hunk intera; added conta tutto; col tetto dell'op diff il passaggio intero
D5  nomi sensibili: solo i conteggi
D6  NotebookEdit: le righe della cella
D7  nessun diff: errore, trascrizione vecchia, tool che non scrive
D8  la maschera delle anteprime su ogni riga, col segno in testa
RF5 una riga minificata da 40 KB: la voce sta ancora in /tail e in una pagina
D9  la vista con git da una sottocartella: radice, base, stati, conteggi, chiavi del contratto
D10 segreti: righe mascherate, id_rsa solo conteggi, un link mai seguito
D11 file=: solo un percorso fra le modifiche
D12 limiti: 50 file e 2000 righe, 5000 con file=
D13 un file oltre 1 MB
D14 un repository senza commit
D15 fuori da git, o senza git
D16 git che non risponde: GitTimeout
D17 fuori da git: i passaggi della conversazione, per file
RF1 nomi con spazi, accenti, virgolette · RF2 rinominati · RF3 CRLF e «\\ No newline» · RF4 merge con un conflitto
D18 la pillola: i conteggi della vista, None fuori da git e senza modifiche
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

spec = importlib.util.spec_from_file_location("cm_diff", T.SCRIPTS / "cm-diff.py")
DF = importlib.util.module_from_spec(spec)
spec.loader.exec_module(DF)
HUNK = {"old_start": 12, "old_lines": 3, "new_start": 12, "new_lines": 4,
        "lines": [" export function total(cart: Cart) {", "-  return cart.items.reduce((s, i) => s + i.price, 0)",
                  "+  const sub = cart.items.reduce((s, i) => s + i.price * i.qty, 0)", "+  return sub - (cart.giftCard?.amount ?? 0)", " }"]}
SP = [{"oldStart": 12, "oldLines": 3, "newStart": 12, "newLines": 4, "lines": HUNK["lines"]}]

d1 = DF.entry_diff("Edit", {"file_path": "/home/dev/storefront/src/checkout/totals.ts"},
                   {"filePath": "/home/dev/storefront/src/checkout/totals.ts", "structuredPatch": SP, "originalFile": None}, "/home/dev/storefront")
want1 = {"path": "/home/dev/storefront/src/checkout/totals.ts", "kind": "edit", "added": 2, "removed": 1, "hunks": [HUNK], "cut": False}
T.check("D1 Edit with structuredPatch → the contract's diff (entry a8.0 of the fixture): path, kind edit, added and removed, the hunk, cut false, keys in order",
        d1 == want1 and list(d1) == ["path", "kind", "added", "removed", "hunks", "cut"]
        and list(d1["hunks"][0]) == ["old_start", "old_lines", "new_start", "new_lines", "lines"], json.dumps(d1))
d2 = DF.entry_diff("Write", {"file_path": "notes/new.md"}, {"type": "create", "filePath": "/w/notes/new.md", "content": "uno\r\ndue\r\n\r\ntre", "structuredPatch": []}, "/w")
T.check("D2 Write of a new file: kind create, every line of content added in one hunk @@ -0,0 +1,n @@, no CR at the end (a CRLF file)",
        d2 == {"path": "/w/notes/new.md", "kind": "create", "added": 4, "removed": 0, "cut": False,
               "hunks": [{"old_start": 0, "old_lines": 0, "new_start": 1, "new_lines": 4, "lines": ["+uno", "+due", "+", "+tre"]}]}, json.dumps(d2))
d3 = DF.entry_diff("Write", {"file_path": "b.txt"}, {"type": "update", "structuredPatch": [
    {"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1, "lines": ["-a", "\\ No newline at end of file", "+b\r"]}]}, "/w")
dw = DF.entry_diff("Edit", {}, {"filePath": "D:\\work\\a.txt", "structuredPatch": SP}, "D:\\work")
T.check("D3 Write over an existing file: kind overwrite; a relative path joined to the session's folder; «\\ No newline at end of file» and the CR dropped; a Windows path (a session on win) left as it is",
        d3 == {"path": "/w/b.txt", "kind": "overwrite", "added": 1, "removed": 1, "cut": False,
               "hunks": [{"old_start": 1, "old_lines": 1, "new_start": 1, "new_lines": 1, "lines": ["-a", "+b"]}]} and dw["path"] == "D:\\work\\a.txt", json.dumps([d3, dw["path"]]))
TUR4 = {"type": "create", "filePath": "/w/big.txt", "content": "\n".join(f"riga {i}" for i in range(250)) + "\n"}
d4, d4f = DF.entry_diff("Write", {}, TUR4, "/w"), DF.entry_diff("Write", {}, TUR4, "/w", DF.FULL_LINES, DF.RESULT_BYTES)
n4 = sum(len(h["lines"]) for h in d4["hunks"])
T.check("D4 a passage over 200 lines: the hunk cut at 200 keeps its header, cut true, added counts all 250; with the op diff budget (5000) the whole passage",
        n4 == 200 and d4["cut"] is True and d4["added"] == 250 and d4["hunks"][0]["new_lines"] == 250 and d4["hunks"][0]["lines"][-1] == "+riga 199"
        and sum(len(h["lines"]) for h in d4f["hunks"]) == 250 and d4f["cut"] is False, str((n4, d4["cut"], d4["added"])))
d5 = [DF.entry_diff("Edit", {}, {"filePath": f"/w/{n}", "structuredPatch": SP}, "/w")
      for n in (".env.local", "certs/server.pem", "id_ed25519", "config/credentials.json", "app.key", "store.p12")]
T.check("D5 a sensitive name (.env*, *.pem, id_*, credentials*, *.key, *.p12): counts only, no lines, cut false; docs/env.md is not one",
        all(x["hunks"] == [] and x["cut"] is False and (x["added"], x["removed"]) == (2, 1) for x in d5) and DF.sensitive("docs/env.md") is False, json.dumps(d5[0]))
d6 = DF.entry_diff("NotebookEdit", {"notebook_path": "/w/n.ipynb"}, {"new_source": "a\nB\nc", "old_source": "a\nb\nc", "edit_mode": "replace", "error": "", "notebook_path": "/w/n.ipynb"}, "/w")
d6i = DF.entry_diff("NotebookEdit", {}, {"new_source": "x = 1", "old_source": None, "edit_mode": "insert", "error": "", "notebook_path": "/w/n.ipynb"}, "/w")
T.check("D6 NotebookEdit (it writes no structuredPatch): the cell's lines, numbered inside the cell; an inserted cell all added",
        d6["kind"] == "edit" and d6["hunks"] == [{"old_start": 1, "old_lines": 3, "new_start": 1, "new_lines": 3, "lines": [" a", "-b", "+B", " c"]}]
        and d6i["hunks"][0]["lines"] == ["+x = 1"] and (d6i["added"], d6i["removed"]) == (1, 0), json.dumps([d6, d6i]))
none7 = [DF.entry_diff("Edit", {}, "Error: String to replace not found", "/w"), DF.entry_diff("Edit", {"file_path": "/w/a"}, {"filePath": "/w/a"}, "/w"),
         DF.entry_diff("Read", {"file_path": "/w/a"}, {"filePath": "/w/a", "structuredPatch": SP}, "/w"),
         DF.entry_diff("NotebookEdit", {}, {"error": "Notebook file does not exist.", "notebook_path": "/w/n.ipynb"}, "/w")]
T.check("D7 no diff (None): a failed tool (its result is a string), a result without structuredPatch (older transcripts), a tool that does not write, a NotebookEdit with an error",
        none7 == [None] * 4, str(none7))
r8 = [DF.redact_line(x) for x in ("+QmFzZTY0IGVuY29kZWQgc3RyaW5nIGZvciB0ZXN0aW5nIHB1cnBvc2Vz", "-API_KEY=sk-abcdefghijklmnopqrstuv",
                                   '   "integrity": "sha512-' + "A" * 60 + '==",', "+DATABASE_URL=postgres://app:hunter22@db/app")]
T.check("D8 every diff line goes through the preview mask with its sign kept: a long base64 line, a key in a variable, a lockfile integrity, a password in a URL; the canonical hunk is untouched",
        r8 == ["+•••", "-API_KEY=•••", '   "integrity": "•••",', "+DATABASE_URL=postgres://•••@db/app"] and DF.redact_diff(want1) == want1 and DF.redact_diff(None) is None, str(r8))
mini = "var a=" + "x" * 40000 + ";"
TUR5 = {"filePath": "/w/dist/app.min.js", "structuredPatch": [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1, "lines": ["-" + mini, "+" + mini.replace("x", "y", 1)]}]}
dm, dmf = DF.entry_diff("Edit", {}, TUR5, "/w"), DF.entry_diff("Edit", {}, TUR5, "/w", DF.FULL_LINES, DF.RESULT_BYTES)
entry5 = {"id": "x.0", "role": "tool", "text": "/w/dist/app.min.js", "at": 1, "tool": "Edit", "note": None, "error": False, "cut": False,
          "turn": None, "files": None, "origin": None, "queued": False, "diff": DF.redact_diff(dm)}
T.check("RF5 an Edit of a minified file (one line of 40 KB): the entry keeps its counts and drops the lines past 16,000 bytes (cut true), so it fits /tail (32 KB) and a page; the op diff budget (1 MB) carries both lines",
        (dm["added"], dm["removed"], dm["hunks"], dm["cut"]) == (1, 1, [], True) and len(json.dumps(entry5, ensure_ascii=False).encode()) < 32000
        and sum(len(h["lines"]) for h in dmf["hunks"]) == 2 and dmf["cut"] is False, str((dm["cut"], len(json.dumps(entry5)))))
# D9-D17, RF1-RF4: la vista «Modifiche» su repository veri, in una cartella temporanea
FIX = T.ROOT / "tests" / "fixtures" / "relay"
TMP = Path(tempfile.mkdtemp(prefix="cm-diff-"))
G = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "init.defaultBranch=main"]


def repo(name):
    d = TMP / name
    d.mkdir()
    subprocess.run(G + ["init", "-q", str(d)], check=True)
    subprocess.run(["git", "-C", str(d), "config", "core.autocrlf", "false"], check=True)   # le righe CRLF restano CRLF
    return d


def put(d, rel, data):
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())


def commit(d, *rels):
    subprocess.run(G + ["-C", str(d), "add", "--", *rels], check=True)
    subprocess.run(G + ["-C", str(d), "commit", "-q", "-m", "c"], check=True)


s = repo("storefront")
OLD_TS = "".join(f"// {i}\n" for i in range(1, 12)) + "export function total(cart: Cart) {\n  return cart.items.reduce((s, i) => s + i.price, 0)\n}\n"
put(s, "src/checkout/totals.ts", OLD_TS)
put(s, ".env.local", "API_KEY=abc\n")
put(s, "assets/logo.png", b"\x89PNG\0" + b"a" * 100)
put(s, "old.txt", "bye\n")
put(s, ".gitignore", "*.log\n")
commit(s, "src/checkout/totals.ts", ".env.local", "assets/logo.png", "old.txt", ".gitignore")
put(s, "src/checkout/totals.ts", OLD_TS.replace("  return cart.items.reduce((s, i) => s + i.price, 0)\n",
                                                "  const sub = cart.items.reduce((s, i) => s + i.price * i.qty, 0)\n  return sub - (cart.giftCard?.amount ?? 0)\n"))
put(s, "tests/checkout/totals.test.ts", "import { total } from '../../src/checkout/totals'\n\ntest('gift card lowers the total', () => {\n"
                                        "  expect(total({ items: [{ price: 10, qty: 2 }], giftCard: { amount: 5 } })).toBe(15)\n})\n")
put(s, ".env.local", "API_KEY=xyz\n")
put(s, "assets/logo.png", b"\x89PNG\0" + b"b" * 200)
(s / "old.txt").unlink()
put(s, "debug.log", "TOKEN=never-read\n")
v9 = DF.view(str(s / "src"), None, 1792100000)
head9 = subprocess.run(["git", "-C", str(s), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
f9 = {f["path"]: f for f in v9["files"]}
FX = json.loads((FIX / "changes-sample.json").read_text())
T.check("D9 the view with git, asked from a subfolder: repo = the root, base = short HEAD, every change against HEAD with the new file not ignored, the ignored one absent; the contract's keys in order",
        v9["source"] == "git" and v9["repo"] == str(s.resolve()) and v9["base"] == head9 and v9["at"] == 1792100000
        and list(f9) == [".env.local", "assets/logo.png", "old.txt", "src/checkout/totals.ts", "tests/checkout/totals.test.ts"]
        and list(v9) == list(FX) and all(list(f) == list(FX["files"][0]) for f in v9["files"]), json.dumps(v9)[:400])
ts9 = f9["src/checkout/totals.ts"]
T.check("D9 statuses and counts as git gives them (--numstat, «-» of the binary → 0): modified 2/1 with its hunk (3 lines of context: it starts at 10), the new test file 5/0 in one hunk as in the fixture, .env.local counts only, the binary 0/0 with its size, the deleted file all «-» with size null",
        (ts9["status"], ts9["added"], ts9["removed"]) == ("modified", 2, 1) and ts9["hunks"][0]["old_start"] == 10 and ts9["hunks"][0]["lines"][3:6] == HUNK["lines"][1:4]
        and f9["tests/checkout/totals.test.ts"]["hunks"] == FX["files"][1]["hunks"] and f9["tests/checkout/totals.test.ts"]["status"] == "added"
        and (f9[".env.local"]["status"], f9[".env.local"]["added"], f9[".env.local"]["removed"], f9[".env.local"]["hunks"]) == ("modified", 1, 1, [])
        and (f9["assets/logo.png"]["status"], f9["assets/logo.png"]["added"], f9["assets/logo.png"]["hunks"], f9["assets/logo.png"]["size"]) == ("binary", 0, [], 205)
        and (f9["old.txt"]["status"], f9["old.txt"]["size"], f9["old.txt"]["hunks"][0]["lines"]) == ("deleted", None, ["-bye"])
        and v9["totals"] == {"files": 5, "added": 8, "removed": 3} and v9["cut"] is False, json.dumps(f9)[:600])
k = repo("keys")
put(k, "config/app.yml", "name: shop\n")
put(k, "id_rsa", "old\n")
commit(k, "config/app.yml", "id_rsa")
put(k, "config/app.yml", "name: shop\ntoken: abcdef123456\nintegrity: sha512-" + "Q" * 64 + "\n")
put(k, "id_rsa", "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n-----END OPENSSH PRIVATE KEY-----\n")
outside = TMP / "outside-secret.txt"
outside.write_text("SECRET-CONTENT-NEVER-SENT\n")
os.symlink(outside, k / "notes.txt")
v10 = DF.view(str(k), None, 1)
f10 = {f["path"]: f for f in v10["files"]}
T.check("D10 secrets: every line masked with its sign kept (a token, a long hash); id_rsa tracked and modified gives name, status and counts only; an untracked link to a file outside the repo shows its target, never the content",
        f10["config/app.yml"]["hunks"][0]["lines"] == [" name: shop", "+token: •••", "+integrity: •••"]
        and f10["id_rsa"]["hunks"] == [] and (f10["id_rsa"]["added"], f10["id_rsa"]["removed"]) == (3, 1)
        and f10["notes.txt"]["hunks"][0]["lines"] == [DF.redact_line("+" + str(outside))] and "SECRET-CONTENT" not in json.dumps(v10), json.dumps(v10)[:500])
refused11 = []
for bad in ("../../etc/passwd", "debug.log"):
    try:
        DF.view(str(s), bad, 1)
        refused11.append("read")
    except DF.NotListed:
        refused11.append("refused")
one11 = DF.view(str(s), "src/checkout/totals.ts", 1)
T.check("D11 file=<path>: only a path among the changes; one outside the repo or ignored is refused before anything is read; the one file alone, with its own totals",
        refused11 == ["refused", "refused"] and [f["path"] for f in one11["files"]] == ["src/checkout/totals.ts"]
        and one11["totals"] == {"files": 1, "added": 2, "removed": 1} and one11["cut"] is False, str((refused11, one11["totals"])))
m = repo("many")
put(m, "keep.txt", "k\n")
commit(m, "keep.txt")
for i in range(60):
    put(m, f"gen/f{i:02d}.txt", "".join(f"x{j}\n" for j in range(50)))
put(m, "gen/zz-long.txt", "".join(f"y{j}\n" for j in range(6000)))
v12, w12, l12 = DF.view(str(m), None, 1), DF.view(str(m), "gen/f55.txt", 1), DF.view(str(m), "gen/zz-long.txt", 1)
shown12 = sum(len(h["lines"]) for f in v12["files"] for h in f["hunks"])
T.check("D12 limits: 61 new files → the first 50 listed, 2000 lines in all (the listed files past them with cut true and no lines), cut true, totals over all 61; file= of a file past the first 50 gives it whole; a file of 6000 lines stops at 5000 with cut true",
        len(v12["files"]) == 50 and shown12 == 2000 and v12["cut"] is True and v12["totals"] == {"files": 61, "added": 9000, "removed": 0}
        and v12["files"][40]["cut"] is True and v12["files"][40]["hunks"] == []
        and sum(len(h["lines"]) for h in w12["files"][0]["hunks"]) == 50 and w12["cut"] is False
        and sum(len(h["lines"]) for h in l12["files"][0]["hunks"]) == 5000 and l12["cut"] is True and l12["files"][0]["added"] == 6000,
        str((len(v12["files"]), shown12, v12["totals"])))
b = repo("big")
put(b, "dump.sql", "a\n")
commit(b, "dump.sql")
put(b, "dump.sql", "insert into t values (1);\n" * 45000)
f13 = DF.view(str(b), None, 1)["files"][0]
T.check("D13 a file over 1 MB: name, status and the counts of --numstat, no lines, cut false",
        f13["size"] > DF.BIG_FILE and (f13["status"], f13["added"], f13["removed"], f13["hunks"], f13["cut"]) == ("modified", 45000, 1, [], False), str(f13["size"]))
n = repo("fresh")
put(n, "a.txt", "1\n2\n")
subprocess.run(G + ["-C", str(n), "add", "a.txt"], check=True)
put(n, "b.txt", "x\n")
v14 = DF.view(str(n), None, 1)
T.check("D14 a repository without commits: base null and every file new (staged or not), all its lines added",
        v14["base"] is None and [(f["path"], f["status"], f["added"]) for f in v14["files"]] == [("a.txt", "added", 2), ("b.txt", "added", 1)]
        and v14["totals"] == {"files": 2, "added": 3, "removed": 0}, json.dumps(v14)[:300])
plain = TMP / "plain"
plain.mkdir()
_bin = DF.GIT_BIN
DF.GIT_BIN = str(TMP / "no-such-git")
nogit15 = DF.view(str(s))
DF.GIT_BIN = _bin
T.check("D15 outside git, or without git → None (the relay falls back to the transcript)", DF.view(str(plain)) is None and nogit15 is None, "")
slow = TMP / "slow-git"
slow.write_text("#!/bin/sh\nexec sleep 5\n")
slow.chmod(0o755)
_bin, _to = DF.GIT_BIN, DF.GIT_TIMEOUT_S
DF.GIT_BIN, DF.GIT_TIMEOUT_S = str(slow), 0.5
t16 = time.time()
try:
    DF.view(str(s))
    r16 = "answered"
except DF.GitTimeout:
    r16 = "timeout"
el16 = time.time() - t16
DF.GIT_BIN, DF.GIT_TIMEOUT_S = _bin, _to
T.check("D16 git that does not answer within GIT_TIMEOUT_S (5 s, 0.5 here) → GitTimeout, without waiting for it",
        r16 == "timeout" and el16 < 3 and DF.GIT_TIMEOUT_S == 5, f"{r16} {el16:.1f} s")
p1 = DF.entry_diff("Write", {}, {"type": "create", "filePath": "/w/site/a.md", "content": "uno\ndue\n"}, "/w/site", DF.FULL_LINES, DF.RESULT_BYTES)
p2 = DF.entry_diff("Edit", {}, {"filePath": "/w/site/a.md", "structuredPatch": [{"oldStart": 2, "oldLines": 1, "newStart": 2, "newLines": 1, "lines": ["-due", "+DUE"]}]},
                   "/w/site", DF.FULL_LINES, DF.RESULT_BYTES)
p3 = DF.entry_diff("Edit", {}, {"filePath": "/w/site/.env", "structuredPatch": [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1, "lines": ["-K=1", "+K=2"]}]},
                   "/w/site", DF.FULL_LINES, DF.RESULT_BYTES)
p4 = DF.entry_diff("Edit", {}, {"filePath": "/etc/hosts.local", "structuredPatch": [{"oldStart": 1, "oldLines": 0, "newStart": 1, "newLines": 1, "lines": ["+127.0.0.1 shop"]}]},
                   "/w/site", DF.FULL_LINES, DF.RESULT_BYTES)
v17 = DF.view_from_passages([p1, p2, p3, p4], "/w/site", None, 7)
T.check("D17 outside git (source transcript): per file the conversation's passages in order, one entry per file, totals = the sum, repo and base null; a path inside the folder relative, outside absolute; a sensitive name counts only; file= one file",
        (v17["source"], v17["repo"], v17["base"], v17["at"]) == ("transcript", None, None, 7)
        and [(f["path"], f["status"], f["added"], f["removed"]) for f in v17["files"]] == [("a.md", "added", 3, 1), (".env", "modified", 1, 1), ("/etc/hosts.local", "modified", 1, 0)]
        and [h["lines"] for h in v17["files"][0]["hunks"]] == [["+uno", "+due"], ["-due", "+DUE"]] and v17["files"][1]["hunks"] == []
        and v17["totals"] == {"files": 3, "added": 5, "removed": 2} and DF.view_from_passages([p1, p2], "/w/site", "a.md", 7)["totals"]["files"] == 1, json.dumps(v17)[:500])
q = repo("names")
NAMES = ("caffè latte.txt", 'we"ird.txt', "spazio finale .md")
for nm in NAMES:
    put(q, nm, "a\n")
commit(q, *NAMES)
for nm in NAMES:
    put(q, nm, "b\n")
put(q, "relazione attività.md", "nuova\n")
vq = DF.view(str(q), None, 1)
T.check("RF1 names with spaces, accents and quotes: the path exact in UTF-8 (not git's «\"caff\\303\\250…\"»), each diff on its own file",
        {f["path"]: f["hunks"][0]["lines"] for f in vq["files"]} == {"caffè latte.txt": ["-a", "+b"], 'we"ird.txt': ["-a", "+b"], "spazio finale .md": ["-a", "+b"], "relazione attività.md": ["+nuova"]},
        json.dumps(vq["files"], ensure_ascii=False)[:400])
mv = repo("moves")
put(mv, "old name.txt", "uno\ndue\ntre\nquattro\ncinque\n")
put(mv, "same.txt", "x\ny\n")
commit(mv, "old name.txt", "same.txt")
subprocess.run(G + ["-C", str(mv), "mv", "old name.txt", "nuovo nome.txt"], check=True)
put(mv, "nuovo nome.txt", "uno\ndue\nTRE\nquattro\ncinque\n")
subprocess.run(G + ["-C", str(mv), "mv", "same.txt", "moved.txt"], check=True)
fm = {f["path"]: f for f in DF.view(str(mv), None, 1)["files"]}
T.check("RF2 renamed files: one entry each under the new path (not deleted + added), status renamed; with an edit its counts and only the changed lines; a pure rename +0 −0 without lines",
        sorted(fm) == ["moved.txt", "nuovo nome.txt"] and (fm["nuovo nome.txt"]["status"], fm["nuovo nome.txt"]["added"], fm["nuovo nome.txt"]["removed"]) == ("renamed", 1, 1)
        and [x for x in fm["nuovo nome.txt"]["hunks"][0]["lines"] if x[0] != " "] == ["-tre", "+TRE"]
        and (fm["moved.txt"]["status"], fm["moved.txt"]["added"], fm["moved.txt"]["removed"], fm["moved.txt"]["hunks"]) == ("renamed", 0, 0, []), json.dumps(fm)[:400])
c = repo("crlf")
put(c, "win.txt", "uno\r\ndue\r\n")
put(c, "end.txt", "a\nb")
commit(c, "win.txt", "end.txt")
put(c, "win.txt", "uno\r\nDUE\r\n")
put(c, "end.txt", "a\nB")
vc = DF.view(str(c), None, 1)
T.check("RF3 CRLF lines and «\\ No newline at end of file»: no CR at the end of a line, no «\\» line, the counts as git gives them",
        {f["path"]: f["hunks"][0]["lines"] for f in vc["files"]} == {"end.txt": [" a", "-b", "+B"], "win.txt": [" uno", "-due", "+DUE"]}
        and [(f["added"], f["removed"]) for f in vc["files"]] == [(1, 1), (1, 1)], repr([f["hunks"] for f in vc["files"]]))
mg = repo("merge")
put(mg, "f.txt", "l1\nl2\nl3\n")
commit(mg, "f.txt")
subprocess.run(G + ["-C", str(mg), "checkout", "-q", "-b", "other"], check=True)
put(mg, "f.txt", "l1\nOTHER\nl3\n")
commit(mg, "f.txt")
subprocess.run(G + ["-C", str(mg), "checkout", "-q", "main"], check=True)
put(mg, "f.txt", "l1\nMAIN\nl3\n")
commit(mg, "f.txt")
subprocess.run(G + ["-C", str(mg), "merge", "-q", "other"], capture_output=True)
vg = DF.view(str(mg), None, 1)
T.check("RF4 a repository in the middle of a merge with a conflict: the view works, the file is modified with the conflict markers as added lines",
        [(f["path"], f["status"], f["added"], f["removed"]) for f in vg["files"]] == [("f.txt", "modified", 4, 0)]
        and vg["files"][0]["hunks"][0]["lines"] == [" l1", "+<<<<<<< HEAD", " MAIN", "+=======", "+OTHER", "+>>>>>>> other", " l3"], json.dumps(vg["files"])[:300])

# D18: la pillola di /state
clean = repo("clean")
put(clean, "a.txt", "a\n")
commit(clean, "a.txt")
T.check("D18 counts (the pill of /state): {files, added, removed} = the view's totals, the new files' lines included; None outside git and with nothing changed",
        DF.counts(str(s)) == v9["totals"] and DF.counts(str(plain)) is None and DF.counts(str(clean)) is None, str(DF.counts(str(s))))
# D19 (revisione finale, 16/10): i file non tracciati si leggono in Python, fuori dai 5 s di git — anche loro hanno un
# tempo massimo: un npm install prima del .gitignore non deve fermare la pillola ne' la vista
u = repo("untracked")
put(u, "keep.txt", "k\n")
commit(u, "keep.txt")
for i in range(30):
    put(u, f"node_modules/p{i}/index.js", "x\n")
_lo, _to19 = DF._lines_of, DF.GIT_TIMEOUT_S
DF._lines_of = lambda root, rel: (time.sleep(0.05), _lo(root, rel))[1]
DF.GIT_TIMEOUT_S = 0.3
t19 = time.time()
try:
    DF.counts(str(u))
    r19 = "answered"
except DF.GitTimeout:
    r19 = "timeout"
el19 = time.time() - t19
DF._lines_of, DF.GIT_TIMEOUT_S = _lo, _to19
T.check("D19 counting the untracked files has the same deadline as git (GitTimeout past GIT_TIMEOUT_S), so a huge untracked tree cannot hold the pill or the view",
        r19 == "timeout" and el19 < 1.0 and DF.counts(str(u)) == {"files": 30, "added": 30, "removed": 0}, f"{r19} {el19:.2f} s")
T.finish()
