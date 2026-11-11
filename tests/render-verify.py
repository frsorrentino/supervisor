#!/usr/bin/env python3
"""supervisor render (03/10/2026): Markdown → una pagina HTML con il modello fisso; nessun HTML passa; la chat del
telefono mostra la pagina nella voce del comando (files, come le immagini di `report`)."""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="cm-render-"))
MD = """# Giornata del 03/10

Rilasciate **quattro versioni**, con `release.sh` e *calma*.

## Fatto
- ✓ 0.5.11 — ricerca
  - sotto-voce
- ✗ 0.6.2 — attende
- [x] fase 1
- [ ] voce c

1. primo
2. secondo

| versione | test |
|---|---|
| 0.5.11 | 194/194 |

> nota

```
<b>codice</b> & altro
```

---
[GitHub](https://github.com/x?a=1&b=2) [cattivo](javascript:alert(1)) <script>alert(1)</script> <img src=x onerror=alert(1)>
"""
(tmp / "r.md").write_text(MD)


def render(*a, cwd=tmp, stdin=None):
    p = subprocess.run([str(T.SCRIPTS / "supervisor"), "render", *a], cwd=cwd, input=stdin, capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout, p.stderr


rc, out, err = render("r.md")
m = re.match(r"html: (/\S+\.html)\n$", out)
page = Path(m.group(1)).read_text() if m else ""
T.check("RD1 default: a page in <cwd>/.claude-master-reports/<date-time>-<title>.html, «html: <absolute path>» on stdout, a .gitignore «*» beside it",
        rc == 0 and m and re.search(r"/\.claude-master-reports/\d{4}-\d\d-\d\d-\d{6}-giornata-del-03-10\.html$", m.group(1)) and (tmp / ".claude-master-reports" / ".gitignore").read_text() == "*\n", out + err)
T.check("RD2 one page, no outside resource: doctype, charset, viewport, inline style with light and dark colours, the first # as the only h1 (in the header), not repeated",
        page.startswith("<!doctype html>") and 'name="viewport"' in page and "prefers-color-scheme:dark" in page and "<link" not in page and "src=" not in page.split("</style>")[1].replace("src=x", "")
        and page.count("<h1>") == 1 and "<header><h1>Giornata del 03/10</h1>" in page, page[:300])
body = page.split("</header>", 1)[-1]
T.check("RD3 Markdown: bold, italic, inline code, ## as h2, nested list, ✓ green and ✗ red, checkboxes, ordered list, table, quote, code block, rule",
        all(x in body for x in ("<strong>quattro versioni</strong>", "<em>calma</em>", "<code>release.sh</code>", "<h2>Fatto</h2>", "<ul><li>sotto-voce</li></ul>",
                                '<span class="ok">✓</span>', '<span class="ko">✗</span>', '<li class="task">☑ fase 1</li>', '<li class="task">☐ voce c</li>',
                                "<ol><li>primo</li><li>secondo</li></ol>", "<th>versione</th>", "<td>194/194</td>", "<blockquote>nota</blockquote>", "<hr>")), body[:900])
T.check("RD4 nothing passes as HTML: <script>, an <img onerror>, <b> in a code block are text; a javascript: link stays text; an https link keeps its & escaped",
        "<script" not in page and "<img" not in page and "&lt;script&gt;" in body and "&lt;b&gt;codice&lt;/b&gt; &amp; altro" in body
        and 'href="javascript' not in page and '<a href="https://github.com/x?a=1&amp;b=2">GitHub</a>' in body, body[-500:])
rc, out, _ = render("-", "--title", "Titolo dato", "--meta", "master · supervisor", "--out", str(tmp / "x" / "y.html"), stdin="solo testo\n")
p2 = (tmp / "x" / "y.html").read_text() if rc == 0 else ""
T.check("RD5 from stdin with --title, --meta and --out (folders made)", rc == 0 and out.strip() == f"html: {tmp / 'x' / 'y.html'}" and "<h1>Titolo dato</h1><p>master · supervisor</p>" in p2 and "<p>solo testo</p>" in p2, out + p2[:200])
T.check("RD6 a missing file → exit 1 and the error, nothing written", render("nope.md")[0] == 1, "")

# la chat del telefono: la voce Bash di `supervisor render` porta la pagina in files (come `report` le immagini)
spec = __import__("importlib.util").util.spec_from_file_location("cm_core", T.SCRIPTS / "cm-core.py")
core = __import__("importlib.util").util.module_from_spec(spec)
spec.loader.exec_module(core)
tr = tmp / "t.jsonl"
tr.write_text("\n".join(json.dumps(x, separators=(",", ":")) for x in [
    {"type": "assistant", "uuid": "a1", "timestamp": "2026-10-03T19:00:00.000Z", "isSidechain": False, "cwd": str(tmp),
     "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "supervisor render recap.md --title Oggi"}}]}},
    {"type": "user", "uuid": "r1", "timestamp": "2026-10-03T19:00:01.000Z", "isSidechain": False, "cwd": str(tmp),
     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": False, "content": f"html: {m.group(1) if m else '/x.html'}\n"}]}},
    {"type": "assistant", "uuid": "a2", "timestamp": "2026-10-03T19:00:02.000Z", "isSidechain": False, "cwd": str(tmp),
     "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "cat notes.txt"}}]}},
    {"type": "user", "uuid": "r2", "timestamp": "2026-10-03T19:00:03.000Z", "isSidechain": False, "cwd": str(tmp),
     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "is_error": False, "content": "html: /etc/passwd.html"}]}},
]) + "\n")
fl = {e["id"]: e["files"] for e in core.transcript_entries(str(tr)) if e["role"] == "tool"}
T.check("RD7 transcript: the render command's entry carries the page (text/html, its size); a «html:» line from another command does not",
        fl.get("a1.0") and fl["a1.0"][0]["path"] == (m.group(1) if m else "") and fl["a1.0"][0]["mime"] == "text/html" and fl["a1.0"][0]["size"] == len(page.encode())
        and fl.get("a2.0") is None, str(fl))

T.finish()
