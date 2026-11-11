#!/usr/bin/env python3
"""supervisor render — un riepilogo in Markdown diventa una pagina HTML con un modello fisso (03/10/2026).

  supervisor render FILE.md|- [--title T] [--meta "sessione · progetto"] [--out FILE.html]

Il modello scrive Markdown (circa un settimo dei token dell'HTML) e questo script fa il resto: una pagina sola,
senza risorse esterne, leggibile sul telefono, chiara e scura. Default: <cartella>/.claude-master-reports/
<data-ora>-<titolo>.html, con un .gitignore «*» (un riepilogo non finisce in un commit). Stampa «html: <percorso>»:
la chat del telefono (contratto 1.22) mostra il file nella voce del comando e il telefono lo apre con l'op `file`.

Markdown accettato: titoli #..###, paragrafi, **grassetto**, *corsivo*, `codice`, blocchi ```, elenchi - * 1.
(un livello sotto con due spazi), caselle - [ ] / - [x], citazioni >, ---, tabelle | a | b |, link [t](https://…).
Nessun HTML passa: tutto il testo e' neutralizzato. ✓ e ✗ a inizio voce si colorano.
"""
import html
import re
import sys
import time
from pathlib import Path

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1f23;--muted:#62666d;--line:#e3e0d8;--card:#ffffff;--code:#f1efe9;--accent:#3b5bdb;--ok:#2b8a3e;--ko:#c92a2a}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#e8e6e1;--muted:#9a9ea6;--line:#2c3036;--card:#1b1e23;--code:#23272d;--accent:#8ea2ff;--ok:#69db7c;--ko:#ff8787}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:20px 16px 48px}
header{border-bottom:1px solid var(--line);margin-bottom:16px;padding-bottom:10px}
header h1{font-size:1.35rem;line-height:1.25;margin:0 0 4px}header p{margin:0;color:var(--muted);font-size:.85rem}
h1{font-size:1.3rem}h2{font-size:1.12rem;margin-top:1.6em}h3{font-size:1rem;margin-top:1.3em}
a{color:var(--accent)}p,ul,ol,blockquote,pre,table{margin:.6em 0}
ul,ol{padding-left:1.3em}li{margin:.2em 0}li.task{list-style:none;margin-left:-1.3em}
code{background:var(--code);border-radius:4px;padding:.05em .3em;font:.88em ui-monospace,SFMono-Regular,Menlo,monospace}
pre{background:var(--code);border-radius:8px;padding:10px 12px;overflow-x:auto}pre code{background:none;padding:0}
blockquote{border-left:3px solid var(--line);margin-left:0;padding-left:12px;color:var(--muted)}
hr{border:0;border-top:1px solid var(--line);margin:1.4em 0}
.table{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.92rem;background:var(--card)}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:var(--code)}
.ok{color:var(--ok);font-weight:600}.ko{color:var(--ko);font-weight:600}
"""
LINK = re.compile(r"\[([^\]]+)\]\(((?:https?://|mailto:|#|\.{0,2}/)[^\s)\"<>]*)\)")


def inline(t):
    """Il testo di una riga: prima neutralizzato, poi codice, link, grassetto, corsivo."""
    parts = re.split(r"(`[^`]+`)", t)
    out = []
    for p in parts:
        if p.startswith("`") and p.endswith("`") and len(p) > 1:
            out.append(f"<code>{html.escape(p[1:-1])}</code>")
            continue
        s = html.escape(p, quote=False)
        s = LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
        s = re.sub(r"^(✓|✅)", r'<span class="ok">\1</span>', s)
        s = re.sub(r"^(✗|❌)", r'<span class="ko">\1</span>', s)
        out.append(s)
    return "".join(out)


def _cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


def to_html(md):
    lines = md.replace("\r\n", "\n").split("\n")
    out, i, para = [], 0, []

    def flush():
        if para:
            out.append("<p>" + "<br>".join(inline(x) for x in para) + "</p>")
            para.clear()
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
            continue
        if not s:
            flush(); i += 1; continue
        m = re.match(r"(#{1,3})\s+(.*)", s)
        if m:
            flush()
            n = max(2, len(m.group(1)))   # il titolo della pagina e' l'unico h1: # e ## → h2, ### → h3
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            i += 1; continue
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", s):
            flush(); out.append("<hr>"); i += 1; continue
        if s.startswith("|") and i + 1 < len(lines) and re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", lines[i + 1].strip()):
            flush()
            head = _cells(s)
            rows, j = [], i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(_cells(lines[j])); j += 1
            t = "<div class=\"table\"><table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
            t += "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(t + "</tbody></table></div>")
            i = j; continue
        if s.startswith(">"):
            flush()
            q, j = [], i
            while j < len(lines) and lines[j].strip().startswith(">"):
                q.append(lines[j].strip()[1:].strip()); j += 1
            out.append("<blockquote>" + "<br>".join(inline(x) for x in q) + "</blockquote>")
            i = j; continue
        if re.match(r"([-*]|\d+[.)])\s+", s):
            flush()
            j, html_list = i, []
            ordered = bool(re.match(r"\d+[.)]\s", s))
            tag = "ol" if ordered else "ul"
            html_list.append(f"<{tag}>")
            nested = False
            while j < len(lines) and lines[j].strip() and (re.match(r"\s*([-*]|\d+[.)])\s+", lines[j])):
                raw = lines[j]
                deep = len(raw) - len(raw.lstrip()) >= 2
                item = re.sub(r"^\s*([-*]|\d+[.)])\s+", "", raw)
                if deep and not nested:
                    html_list.append("<ul>"); nested = True
                elif not deep and nested:
                    html_list.append("</ul>"); nested = False
                cb = re.match(r"\[([ xX])\]\s+(.*)", item)
                if cb:
                    box = "☑" if cb.group(1).lower() == "x" else "☐"
                    html_list.append(f'<li class="task">{box} {inline(cb.group(2))}</li>')
                else:
                    html_list.append(f"<li>{inline(item)}</li>")
                j += 1
            if nested:
                html_list.append("</ul>")
            html_list.append(f"</{tag}>")
            out.append("".join(html_list))
            i = j; continue
        para.append(s)
        i += 1
    flush()
    return "\n".join(out)


def page(md, title, meta):
    return ("<!doctype html>\n<html lang=\"it\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head>\n<body><main>"
            f"<header><h1>{html.escape(title)}</h1><p>{html.escape(meta)}</p></header>\n{to_html(md)}\n</main></body></html>\n")


def slug(t):
    s = re.sub(r"[^\w]+", "-", t.lower(), flags=re.UNICODE).strip("-")
    return s[:50] or "riepilogo"


def main(argv):
    args = list(argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0 if args else 2

    def opt(name):
        if name in args:
            k = args.index(name)
            v = args[k + 1] if k + 1 < len(args) else ""
            del args[k:k + 2]
            return v
        return None
    title, meta, out = opt("--title"), opt("--meta"), opt("--out")
    src = args[0] if args else "-"
    try:
        md = sys.stdin.read() if src == "-" else Path(src).read_text()
    except OSError as e:
        print(f"render: {e}", file=sys.stderr)
        return 1
    if not title:
        m = re.search(r"^#\s+(.+)$", md, re.M)
        title = m.group(1).strip() if m else "Riepilogo"
        if m:
            md = md[:m.start()] + md[m.end():]   # il titolo va nella testata, non due volte
    meta = meta or time.strftime("%d/%m/%Y %H:%M")
    if out:
        dest = Path(out).expanduser().resolve()
    else:
        d = Path.cwd() / ".claude-master-reports"
        d.mkdir(exist_ok=True)
        if not (d / ".gitignore").exists():
            (d / ".gitignore").write_text("*\n")
        dest = d / f"{time.strftime('%Y-%m-%d-%H%M%S')}-{slug(title)}.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(page(md, title, meta))
    print(f"html: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
