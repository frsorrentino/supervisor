#!/usr/bin/env python3
"""supervisor rendiconto — cosa si e' fatto per un cliente in un mese (fase C, contratto 1.103, 02/11/2026).

  supervisor rendiconto [PROGETTO] --mese AAAA-MM [--json]

Il cliente e' la cartella. Compiti chiusi verdi dal registro, commit e tag dalla sua storia git, in sola lettura. Nessuna
chiamata a un modello. Solo cio' che e' misurato: niente ore fatturabili. Il testo lo scrive la master su richiesta; la
bozza la manda la persona: nessun codice qui invia niente. Senza PROGETTO: un cliente per riga col numero di lavori.
"""
import importlib.util
import json
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
COMMITS_MAX = 40
MONTHS_MAX = 12


def M(_k, **kw):
    return cm.msg(CFG, _k, **kw)


def _tasks():
    spec = importlib.util.spec_from_file_location("cm_tasks", HERE / "cm-tasks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def bounds(month):
    y, m = map(int, month.split("-"))
    start = time.mktime((y, m, 1, 0, 0, 0, 0, 0, -1))
    end = time.mktime((y + (m == 12), m % 12 + 1, 1, 0, 0, 0, 0, 0, -1))
    return int(start), int(end)


def good_month(month):
    return bool(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""))


def this_month(now=None):
    return time.strftime("%Y-%m", time.localtime(now or time.time()))


def closed_rows():
    """I compiti chiusi verdi: (progetto, id, titolo, quando, tipo di esito). Senza registro, niente."""
    tk = _tasks()
    con = tk.connect(create=False)
    if con is None:
        return []
    out = []
    for r in con.execute("SELECT t.id, t.title, t.project, t.contract, (SELECT MAX(at) FROM results x WHERE x.task=t.id AND x.outcome='green' AND x.accepted=1) a "
                         "FROM tasks t WHERE t.state='done'"):
        if r["a"] and r["project"]:
            kind = ((json.loads(r["contract"])["task"].get("outcome") or {}).get("kind")) or None
            out.append((os.path.realpath(r["project"]), r["id"], r["title"], int(r["a"]), kind))
    return out


def resolve(project):
    """Un percorso, oppure il percorso relativo o il nome che l'app conosce."""
    p = cm.expand(project)
    if os.path.isabs(p):   # dal vivo, 02/11: un nome relativo non si cerca dalla cartella corrente
        return os.path.realpath(p) if os.path.isdir(p) else None
    root = cm.expand(CFG["workspace"]["root"])
    p = os.path.join(root, project)
    if os.path.isdir(p):
        return os.path.realpath(p)
    for pd in (CFG["workspace"].get("project_dirs") or ["."]):
        p = os.path.join(root, pd, project)
        if os.path.isdir(p):
            return os.path.realpath(p)
    # il solo nome di una cartella piu' in fondo: quella dei compiti chiusi, se e' una sola
    named = {r[0] for r in closed_rows() if Path(r[0]).name == project}
    return named.pop() if len(named) == 1 else None


def _draft_file():
    return Path(cm.expand(CFG["state_dir"])) / "rendiconti.json"


def _drafts():
    try:
        return json.loads(_draft_file().read_text())
    except (OSError, ValueError):
        return {}


def clients(month):
    start, end = bounds(month)
    by = {}
    for project, *_rest, in closed_rows():
        at = _rest[2]
        if start <= at < end:
            by[project] = by.get(project, 0) + 1
    dr = _drafts()
    return [{"project": p, "name": Path(p).name, "n": n, "draft_at": dr.get(f"{p}|{month}")} for p, n in sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))]


def months(now=None):
    seen = {time.strftime("%Y-%m", time.localtime(r[3])) for r in closed_rows()}
    seen.add(this_month(now))
    return sorted(seen, reverse=True)[:MONTHS_MAX]


def _git(project, *a):
    try:
        p = subprocess.run(["git", "-C", project, *a], capture_output=True, text=True, timeout=30)
        return p.stdout if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def detail(project, month):
    start, end = bounds(month)
    tasks = sorted(({"task": i, "title": t, "at": a, "outcome_kind": k} for pr, i, t, a, k in closed_rows() if pr == project and start <= a < end), key=lambda x: -x["at"])
    log = _git(project, "log", "--no-merges", f"--since=@{start}", f"--until=@{end - 1}", "--format=%h\t%ct\t%s")
    commits = []
    for line in (log or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append({"hash": parts[0], "at": int(parts[1]), "subject": parts[2]})
    tags = _git(project, "for-each-ref", "refs/tags", "--format=%(refname:short)\t%(creatordate:unix)") or ""
    rel = sorted(({"tag": t, "at": int(a)} for t, a in (x.split("\t") for x in tags.splitlines() if x.count("\t") == 1 and x.split("\t")[1].isdigit()) if start <= int(a) < end), key=lambda x: -x["at"])
    return {"project": project, "name": Path(project).name, "mese": month, "measured": {"tasks": len(tasks), "commits": len(commits), "releases": len(rel)},
            "tasks": tasks, "commits": commits[:COMMITS_MAX], "commits_more": len(commits) > COMMITS_MAX, "releases": rel, "git": log is not None}


def view(month=None, project=None):
    month = month if good_month(month) else this_month()
    p = resolve(project) if project else None
    return {"at": int(time.time()), "mese": month, "mesi": months(), "clienti": clients(month), "dettaglio": detail(p, month) if p else None}


def request_draft(project, month):
    """(ok, il prompt per la master): segna quando e' stata chiesta. La bozza non parte da qui verso nessuno."""
    if not good_month(month):
        return False, M("conti.bad_month")
    p = resolve(project) if project else None
    if not p:
        return False, M("conti.no_client", project=project)
    d = _drafts()
    d[f"{p}|{month}"] = int(time.time())
    _draft_file().parent.mkdir(parents=True, exist_ok=True)
    _draft_file().write_text(json.dumps(d))
    return True, M("conti.draft_prompt", project=p, month=month)


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    args = list(argv)
    as_json = "--json" in args
    if as_json:
        args.remove("--json")
    month = this_month()
    if "--mese" in args:
        i = args.index("--mese")
        month = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    if not good_month(month) or len(args) > 1:
        print("usage: rendiconto [PROGETTO] --mese AAAA-MM [--json]", file=sys.stderr)
        return 2
    if not args:
        rows = clients(month)
        print(json.dumps(rows, ensure_ascii=False) if as_json else "\n".join(f"{x['n']:>4}  {x['name']}" for x in rows))
        return 0
    p = resolve(args[0])
    if not p:
        print(M("conti.no_client", project=args[0]), file=sys.stderr)
        return 2
    d = detail(p, month)
    if as_json:
        print(json.dumps(d, ensure_ascii=False))
        return 0
    print(f"{d['name']} {month}: {d['measured']['tasks']} compiti, {d['measured']['commits']} commit, {d['measured']['releases']} uscite")
    for t in d["tasks"]:
        print(f"  {t['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
