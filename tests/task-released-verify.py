#!/usr/bin/env python3
"""`supervisor task released ID`: la release che porta un compito, cercata fra tutte le release (27/11/2026,
supervisor-check-release-15-27-11). Il controllo di supervisor-prompt-launch-origine-16-11 cercava l'id con
`gh release list --limit 15` e un `gh release view` per tag: la correzione e' uscita con la 0.8.182 e il suo test passa,
ma alla 0.8.222 quella release era fuori dalle ultime 15 e il controllo restava rosso. Tutto con un gh finto.

TR0 il controllo di prima, come si copiava nei contratti (limite 15), e' rosso su un compito uscito 230 release fa
TR1 task released lo trova oltre la prima pagina dell'API (250 release, l'id nella ventesima): exit 0 e il tag; gh una
    volta sola, a pagine da 100
TR2 nominato da due release vale la piu' vecchia e --json le elenca; un id piu' lungo che lo contiene non conta, una
    bozza nemmeno (exit 1, detto su stderr)
TR3 gh che fallisce o che manca, un id o un --repo sbagliati: exit 2 col motivo; senza --repo vale l'origin su GitHub
    della cartella, fuori da un repository GitHub exit 2; il registro dei compiti non nasce
TR4 il registro lo accetta come controllo di un esito release (task add) e task check lo riesegue verde
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

TID = "demo-task-old-16-11"
REPO = "demo/proj"
tmp = Path(T.tmpdir())
bin_ = tmp / "bin"
bin_.mkdir()
data, log, fail = tmp / "releases.json", tmp / "gh-calls.jsonl", tmp / "gh-fail"

# 250 release pubblicate, dalla piu' nuova come le da' GitHub, piu' una bozza in cima
rel = []
for i in range(250):
    body = f"Changes of 0.9.{i}\n\nTasks: demo-other-{i}"
    if i == 19:
        body = f"Changes of 0.9.19\n\nTasks: {TID}, demo-other-19"
    elif i == 120:
        body = f"Follow-up of the fix ({TID}) after a report"
    elif i == 200:   # fuori dalle ultime 15: il controllo di prima, che cerca una sottostringa, lo prenderebbe per buono
        body = f"Tasks: {TID}b"
    rel.append({"tag_name": f"v0.9.{i}", "published_at": f"2026-{10 + i // 200:02d}-{1 + i % 200 // 8:02d}T{i % 8 * 3:02d}:00:00Z",
                "draft": False, "body": body})
rel.reverse()
rel.insert(0, {"tag_name": "v0.9.250", "published_at": None, "draft": True, "body": "Tasks: demo-task-draft-only"})
data.write_text(json.dumps(rel))
gh = bin_ / "gh"
gh.write_text(f"""#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
with open({str(log)!r}, "a") as f:
    f.write(json.dumps(a) + "\\n")
if os.path.exists({str(fail)!r}):
    sys.stderr.write("HTTP 502: Bad Gateway (https://api.github.com/repos/demo/proj/releases?per_page=100)\\n")
    sys.exit(1)
rel = json.load(open({str(data)!r}))
opt = lambda k: a[a.index(k) + 1] if k in a else None
if a[:1] == ["api"]:
    path = next(x for x in a if x.startswith("repos/"))
    if "--paginate" not in a or not path.endswith("/releases?per_page=100") or not opt("--jq"):
        sys.exit(8)
    for r in rel:   # cio' che --jq tiene: una riga JSON [tag, published_at, note] per release pubblicata
        if not r["draft"]:
            print(json.dumps([r["tag_name"], r["published_at"], r["body"] or ""], separators=(",", ":")))
elif a[:2] == ["release", "list"]:
    print(json.dumps([{{"tagName": r["tag_name"]}} for r in rel if not r["draft"]][:int(opt("--limit") or 30)]))
elif a[:2] == ["release", "view"]:
    r = next((r for r in rel if r["tag_name"] == a[2]), None)
    if r is None:
        sys.exit(1)
    print(r["body"])
else:
    sys.exit(9)
""")
gh.chmod(0o755)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"relay": {"gh_bin": str(gh)}}))
db = tmp / "tasks.db"
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update(HOME=str(tmp), CM_HOME=str(tmp), CC_SUPERVISOR_CONFIG=str(cfg), CM_TASKS_DB=str(db), PATH=f"{bin_}:{os.environ.get('PATH', '')}")
CLI = str(T.SCRIPTS / "supervisor")


def released(*args, cwd=None, env=None):
    p = subprocess.run([CLI, "task", "released", *args], capture_output=True, text=True, env=dict(ENV, **(env or {})), cwd=cwd or str(tmp), timeout=120)
    return p.returncode, p.stdout, p.stderr


def calls():
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


# TR0 il difetto: il controllo copiato nei contratti guarda solo le ultime 15 release
OLD = ("import json,subprocess\n"
       "g=lambda *a: subprocess.run(list(a),capture_output=True,text=True,timeout=180).stdout\n"
       f"tags=json.loads(g('gh','release','list','-R','{REPO}','--limit','15','--json','tagName'))\n"
       f"assert any('{TID}' in g('gh','release','view',x['tagName'],'-R','{REPO}','--json','body','-q','.body') for x in tags), 'release'\n")
p = subprocess.run([sys.executable, "-c", OLD], capture_output=True, text=True, env=ENV, timeout=120)
T.check("TR0 the old check (gh release list --limit 15, one gh release view per tag) is red on a task released 230 releases ago",
        p.returncode != 0 and "AssertionError: release" in p.stderr, f"rc={p.returncode} {p.stderr[-300:]}")

# TR1 la release giusta, oltre la prima pagina e oltre le ultime 15
log.unlink(missing_ok=True)
rc, out, err = released(TID, "--repo", REPO)
cs = calls()
T.check("TR1 task released finds the release past the API's first page (250 releases, the id in the 20th): exit 0 and its tag; gh called once, 100 a page",
        rc == 0 and out == "v0.9.19\n" and len(cs) == 1 and cs[0][:3] == ["api", "--paginate", f"repos/{REPO}/releases?per_page=100"] and "--jq" in cs[0],
        f"rc={rc} out={out!r} err={err[-300:]} calls={cs}")

# TR2 la piu' vecchia, --json, la parola intera, le bozze
rc, out, err = released(TID, "--repo", REPO, "--json")
try:
    j = json.loads(out)
except ValueError:
    j = {}
rc_b, out_b, _ = released(TID + "b", "--repo", REPO)
rc_d, out_d, err_d = released("demo-task-draft-only", "--repo", REPO)
rc_n, out_n, err_n = released("demo-task", "--repo", REPO)
T.check("TR2 named by two releases → the oldest; --json {task, repo, first, releases oldest first, searched}; a longer id that contains it does not count, nor a draft (exit 1, said on stderr)",
        rc == 0 and j == {"task": TID, "repo": REPO, "first": "v0.9.19", "releases": ["v0.9.19", "v0.9.120"], "searched": 250}
        and rc_b == 0 and out_b == "v0.9.200\n" and rc_d == 1 and out_d == "" and "none of the 250 releases of demo/proj" in err_d
        and rc_n == 1 and out_n == "",
        f"rc={rc} j={j} b={rc_b}{out_b!r} d={rc_d}{out_d!r}{err_d[-200:]} n={rc_n}{out_n!r}{err_n[-200:]}")

# TR3 gli errori dicono il motivo; l'origin della cartella; nessun registro
fail.write_text("1")
rc_f, out_f, err_f = released(TID, "--repo", REPO)
fail.unlink()
cfg_none = tmp / "config-nogh.json"
cfg_none.write_text(json.dumps({"relay": {"gh_bin": str(tmp / "no" / "gh")}}))
rc_m, _, err_m = released(TID, "--repo", REPO, env={"CC_SUPERVISOR_CONFIG": str(cfg_none)})
rc_u1, _, err_u1 = released("bad id!", "--repo", REPO)
rc_u2, _, err_u2 = released(TID, "--repo", "demo")
rc_u3, _, _ = released(TID, "other", "--repo", REPO)
repo_dir = tmp / "checkout"
repo_dir.mkdir()
subprocess.run(["git", "init", "-q", str(repo_dir)], check=True)
subprocess.run(["git", "-C", str(repo_dir), "remote", "add", "origin", "git@github.com:demo/proj.git"], check=True)
log.unlink(missing_ok=True)
rc_o, out_o, err_o = released(TID, cwd=str(repo_dir))
cs_o = calls()
plain = tmp / "plain"
plain.mkdir()
rc_p, _, err_p = released(TID, cwd=str(plain))
T.check("TR3 gh failing or missing, a wrong id or --repo: exit 2 with the reason; without --repo the folder's origin on GitHub, outside one exit 2; no task registry is created",
        rc_f == 2 and "HTTP 502: Bad Gateway" in err_f and rc_m == 2 and "gh not found" in err_m
        and rc_u1 == rc_u2 == rc_u3 == 2 and "usage: task released ID" in err_u1 and "usage: task released ID" in err_u2
        and rc_o == 0 and out_o == "v0.9.19\n" and len(cs_o) == 1 and f"repos/{REPO}/releases?per_page=100" in cs_o[0]
        and rc_p == 2 and "not on GitHub" in err_p and not db.exists(),
        f"f={rc_f}{err_f[-150:]} m={rc_m}{err_m[-150:]} u={rc_u1},{rc_u2},{rc_u3} o={rc_o}{out_o!r}{err_o[-150:]} {cs_o} p={rc_p}{err_p[-150:]} db={db.exists()}")

# TR4 un controllo del registro per un esito release
proj = tmp / "proj"
proj.mkdir()


def contract(tid, cmd):
    return {"schema_version": 1, "task": {
        "id": tid, "title": f"unit {tid}", "plan": None,
        "where": {"project": str(proj), "session": None, "host": "local", "account": None},
        "check": {"cmd": cmd, "cwd": None, "timeout_s": 120}, "perimeter": ["src/**"], "lane": "open",
        "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal",
        "requested_by": "test", "outcome": {"expected": "the release with the fix is out", "kind": "release"}}}


def task(*args, stdin=None):
    p = subprocess.run([CLI, "task", *args], input=stdin, capture_output=True, text=True, env=ENV, timeout=120)
    return p.returncode, p.stdout, p.stderr


rc_a, out_a, err_a = task("add", "-", stdin=json.dumps(contract("t-rel", f"'{CLI}' task released {TID} --repo {REPO}")))
rc_x, _, err_x = task("add", "-", stdin=json.dumps(contract("t-none", "true")))
rc_c, out_c, err_c = task("check", "t-rel")
T.check("TR4 the registry takes it as the check of a release outcome (task add exit 0; a check that touches nothing is still refused) and task check reruns it green",
        rc_a == 0 and out_a.strip() == "t-rel" and rc_x == 2 and "does not touch the outcome" in err_x and rc_c == 0,
        f"add={rc_a}{out_a!r}{err_a[-200:]} none={rc_x}{err_x[-200:]} check={rc_c}{out_c[-200:]}{err_c[-200:]}")

T.rm(tmp)
T.finish()
