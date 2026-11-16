#!/usr/bin/env python3
"""supervisor <comando> --help — l'aiuto di un sottocomando: l'intestazione del suo script, senza eseguirlo.

  cm-help.py FILE [COMANDO]

Lo chiama il dispatcher quando il primo argomento dopo il sottocomando e' -h o --help (16/11/2026,
supervisor-help-esegue-16-11: `supervisor restore --help` eseguiva il ripristino, `launch -h` prendeva «-h» per una
cartella, `relay --help` stampava l'uso come errore). L'aiuto e' la documentazione che lo script porta in testa: la
docstring del modulo per un .py, il blocco di commenti dopo la prima riga per uno script di shell. Il file si legge e
basta: non viene importato ne' eseguito.
"""
import ast
import sys
from pathlib import Path


def header(path):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if str(path).endswith(".py"):
        try:
            doc = ast.get_docstring(ast.parse(text))
        except SyntaxError:
            doc = None
        if doc:
            return doc
    out = []
    for line in text.splitlines()[1:] if text.startswith("#!") else text.splitlines():
        if not line.startswith("#"):
            if out or line.strip():
                break
            continue
        out.append(line[2:] if line.startswith("# ") else line[1:])
    return "\n".join(out).strip()


def main(argv):
    if not argv:
        print("usage: cm-help.py FILE [COMMAND]", file=sys.stderr)
        return 2
    try:
        text = header(argv[0])
    except OSError as e:
        print(f"supervisor help: {e}", file=sys.stderr)
        return 1
    if not text:
        print(f"supervisor {argv[1] if len(argv) > 1 else ''}: no help text".strip(), file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
