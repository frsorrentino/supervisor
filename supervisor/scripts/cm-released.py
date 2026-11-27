#!/usr/bin/env python3
"""supervisor task released ID [--repo OWNER/REPO] [--json] — la release che porta un compito (27/11/2026).

Cerca l'id del compito nelle note di tutte le release pubblicate del repository, con `gh api --paginate` a pagine da
100: nessun limite di numero. Scrive il tag della prima release che lo nomina, la piu' vecchia; con --json
{task, repo, first, releases, searched}. Exit 0 trovata, 1 in nessuna, 2 uso sbagliato o gh che non risponde (il
motivo su stderr). Non legge e non crea il registro dei compiti.

Per i controlli dei contratti (supervisor-check-release-15-27-11). Prima si copiava da un contratto all'altro
`gh release list --limit N` e un `gh release view` per tag: dopo N uscite la release giusta non si vedeva piu' e il
controllo restava rosso per sempre (supervisor-prompt-launch-origine-16-11, uscito con la 0.8.182, rosso alla 0.8.222
col limite 15). L'id conta come parola intera: un id piu' lungo che lo contiene non vale. Le bozze non contano. Senza
--repo vale il remoto origin della cartella in cui gira, se e' su GitHub. gh e' quello del relay: relay.gh_bin della
config, predefinito «gh» nel PATH.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PER_PAGE = 100
TIMEOUT_S = 180
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")   # task.id dello schema del contratto
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
# una riga per release pubblicata: [tag, published_at, note], gia' filtrata da gh (nessun jq da installare)
JQ = '.[] | select(.draft | not) | [.tag_name, .published_at, (.body // "")] | @json'
USAGE = "usage: task released ID [--repo OWNER/REPO] [--json]"


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gh_bin():
    """Il gh del relay (relay.gh_bin della config, predefinito «gh») trovato nel PATH, o None."""
    try:
        cfg = _load("cm-config").load(warn=False)
    except Exception:   # noqa: BLE001 — una config che non si legge: vale il gh del PATH
        cfg = {}
    return shutil.which(str((cfg.get("relay") or {}).get("gh_bin") or "gh"))


def names(body, tid):
    """True se le note `body` nominano `tid` come parola intera (non dentro un id piu' lungo)."""
    return re.search(r"(?<![\w-])" + re.escape(tid) + r"(?![\w-])", body or "") is not None


def origin_repo(cwd=None):
    """OWNER/REPO del remoto origin della cartella, o None se non c'e' o non e' su GitHub."""
    try:
        p = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", p.stdout.strip()) if p.returncode == 0 else None
    return f"{m.group(1)}/{m.group(2)}" if m else None


def releases(repo, gh=None):
    """[(tag, published_at, note)] di tutte le release pubblicate di `repo`, dalla piu' vecchia. RuntimeError col motivo
    se gh manca, non risponde o risponde altro."""
    gh = gh or gh_bin()
    if not gh:
        raise RuntimeError("gh not found")
    try:
        p = subprocess.run([gh, "api", "--paginate", f"repos/{repo}/releases?per_page={PER_PAGE}", "--jq", JQ],
                           capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh api gave no answer in {TIMEOUT_S} s")
    except OSError as e:
        raise RuntimeError(str(e))
    if p.returncode != 0:
        raise RuntimeError((p.stderr.strip().splitlines() or [f"gh api exit {p.returncode}"])[-1])
    out = []
    for line in p.stdout.splitlines():
        if not line.strip():
            continue
        try:
            tag, at, body = json.loads(line)
        except (ValueError, TypeError):
            raise RuntimeError(f"gh api: unexpected line: {line[:120]}")
        out.append((str(tag), str(at or ""), str(body or "")))
    out.sort(key=lambda r: (r[1], r[0]))
    return out


def main(args, as_json=False):
    args = list(args)
    if "--json" in args:
        args.remove("--json")
        as_json = True
    repo = None
    if "--repo" in args:
        i = args.index("--repo")
        if i + 1 >= len(args):
            print(USAGE, file=sys.stderr)
            return 2
        repo = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1 or not ID_RE.match(args[0]) or (repo is not None and not REPO_RE.match(repo)):
        print(USAGE, file=sys.stderr)
        return 2
    tid = args[0]
    repo = repo or origin_repo()
    if not repo:
        print("task released: no --repo, and the origin of this folder is not on GitHub", file=sys.stderr)
        return 2
    try:
        rel = releases(repo)
    except RuntimeError as e:
        print(f"task released: {e}", file=sys.stderr)
        return 2
    hits = [tag for tag, _, body in rel if names(body, tid)]
    if as_json:
        print(json.dumps({"task": tid, "repo": repo, "first": hits[0] if hits else None, "releases": hits, "searched": len(rel)}))
    elif hits:
        print(hits[0])
    else:
        print(f"task released: {tid} is in none of the {len(rel)} releases of {repo}", file=sys.stderr)
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
