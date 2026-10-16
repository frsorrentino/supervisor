#!/usr/bin/env python3
"""Guardia della corsia chiusa (fase 1, 03/10/2026): hook PreToolUse su Bash.

Spenta o senza registro: nessun effetto (il comportamento di prima). Accesa: push, release e publish negati fuori
da un compito che li copre; permessi dentro un compito in corso di un piano approvato senza hold, o con un ok
registrato. Il testo fra virgolette e gli heredoc non contano."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="cm-guard-"))
proj = tmp / "proj"
(proj / "sub").mkdir(parents=True)
other = tmp / "other"
other.mkdir()
cfg = tmp / "config.json"
DB = tmp / "tasks.db"
# da un terminale (17/10): `task approve` da una sessione Claude vuole --inbox, e queste prove registrano ok a mano
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_APPROVE_VIA")}
ENV.update(CM_TASKS_DB=str(DB), CC_SUPERVISOR_CONFIG=str(cfg))


def config(guard):
    cfg.write_text(json.dumps({"language": "en", "tasks": {"guard": guard}}))


def hook(cmd, cwd=proj, *flags, script=None):
    p = subprocess.run([sys.executable, str(script or T.SCRIPTS / "cm-lane-guard.py"), *flags], capture_output=True, text=True, env=ENV, timeout=30,
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd), "hook_event_name": "PreToolUse"}))
    if p.returncode != 0:
        return "error"
    if not p.stdout.strip():
        return "allow"
    out = json.loads(p.stdout)["hookSpecificOutput"]
    return out["permissionDecision"] + ": " + out["permissionDecisionReason"]


def cm(*args, stdin=None):
    return subprocess.run([str(T.SCRIPTS / "supervisor"), *args], input=stdin, capture_output=True, text=True, env=ENV, timeout=60)


def node(nid, plan, project=proj, hold=False, lane="closed"):
    return {"id": nid, "title": f"publish {nid}", "plan": plan,
            "where": {"project": str(project), "session": None, "host": "local", "account": None},
            "check": {"cmd": "true", "cwd": None, "timeout_s": 10}, "perimeter": [], "lane": lane, "depends_on": [],
            "route": "session", "hold": hold, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}


# G1 spenta, o accesa senza registro: tutto come prima
config(False)
cm("task", "list")   # crea il registro
T.check("G1 guard off (the default): git push passes even with a registry", hook("git push origin main") == "allow", "")
config(True)
DB.unlink()
T.check("G1 guard on but no registry: git push passes, and no registry is created", hook("git push origin main") == "allow" and not DB.exists(), "")
cm("task", "list")

# G2 accesa, nessun compito: negati i comandi di pubblicazione, il resto passa
denied = {c: hook(c) for c in ("git push origin main", "gh release create v1.0", "npm publish", "bash release.sh 0.6.0", "git -C x push")}
T.check("G2 guard on, no task: push, gh release, npm publish, release.sh → deny with the reason", all(v.startswith("deny: Closed lane:") for v in denied.values()), str(denied))
allowed = {c: hook(c) for c in ("git status", "git push --dry-run", "bash release.sh 0.6.0 --check", "git commit -m 'then git push'",
                                "git commit -F - <<'EOF'\nnotes: run git push later\nEOF", "gh release view v1")}
T.check("G2 not publishing: status, --dry-run, release --check, «git push» inside a quoted message or a heredoc, gh release view → allow",
        all(v == "allow" for v in allowed.values()), str(allowed))
reads = {c: hook(c) for c in ("grep -n release.sh docs/notes.md", "cat release.sh", "sed -n 1,40p release.sh | grep VER", "git log -- release.sh")}
T.check("G2b release.sh named as an argument (grep, cat, sed, git log) is a read → allow; as the command it stays closed",
        all(v == "allow" for v in reads.values()) and hook("RELEASE_TESTS=wsl nice -n 19 bash release.sh 0.8.33").startswith("deny"), str(reads))

# G3 piano approvato: il compito in corso apre la corsia nella sua cartella (e sotto), non altrove
plan = {"schema_version": 1, "id": "rel", "title": "release", "nodes": [node("pub", "rel"), node("held", "rel", project=other, hold=True)]}
(tmp / "plan.json").write_text(json.dumps(plan))
r = cm("plan", "approve", str(tmp / "plan.json"), "--by", "maintainer", "--text", "ok il piano")
T.check("G3 approved plan, task not started yet → still denied", r.returncode == 0 and hook("git push").startswith("deny"), r.stdout + r.stderr)
cm("task", "start", "pub")
T.check("G3 the task of the approved plan is running → push allowed in its folder and below it",
        hook("git push") == "allow" and hook("git push", proj / "sub") == "allow", "")
cm("task", "start", "held")
T.check("G3 a task marked hold («do not publish yet») does not open the lane, even running in an approved plan", hook("git push", other).startswith("deny"), "")
T.check("G3 another folder → denied", hook("git push", tmp).startswith("deny"), "")
cm("task", "cancel", "pub")
T.check("G3 the task is closed → denied again", hook("git push").startswith("deny"), "")

# G4 fuori da un piano: un ok registrato apre la corsia
c = {"schema_version": 1, "task": node("solo", None)}
cm("task", "add", "-", stdin=json.dumps(c))
cm("task", "wait-ok", "solo", "--what", "tag v1", "--where", "github")
T.check("G4 awaiting ok (asked, nobody approved) → denied", hook("git push --tags").startswith("deny"), "")
cm("task", "approve", "solo", "--by", "maintainer (phone)", "--text", "ok")
T.check("G4 the ok recorded → allowed", hook("git push --tags") == "allow", "")

# G7 lane-guard-cd-target (09/10, chiesto dal maintainer): la cartella del comando, non solo quella della sessione.
# La sessione gira in tmp (come la master sulla radice); il compito «solo», con l'ok, e' in proj.
g7 = {c: hook(c, tmp) for c in (
    f"cd {proj} && git push", "cd proj && git push --tags", "cd proj/sub; git push", f"git -C {proj} push",
    "(cd proj && git push)", f'cd "{proj}" && git push', "cd ~ && cd " + str(proj) + " && git push")}
T.check("G7 lane-guard-cd-target: from the master's folder, cd <task folder> && git push (absolute, relative, below it, ;, subshell, quoted), git -C <dir> push → allow",
        all(v == "allow" for v in g7.values()), str(g7))
g7d = {c: hook(c, tmp) for c in (
    "git push", "cd proj && cd .. && git push", "cd other && git push", "cd $HOME && git push", "cd - && git push",
    "cd proj && git push && cd ../other && git push", f"cd {proj} && git -C ../other push", "cd proj && git status; cd .. ; git push", "cd proj && cd && git push")}
T.check("G7 lane-guard-cd-target: git push alone in the master's folder, a cd out of the task folder, a cd it cannot read, a second push elsewhere → deny",
        all(v.startswith("deny") for v in g7d.values()), str(g7d))

# G8 (09/10) il cd non leggibile lo dice: la sessione non deve chiedere un ok che c'e' gia'
g8 = {c: hook(c, d) for c, d in (("cd $HOME && git push", tmp), ("cd - && git push", tmp), ("cd $(git rev-parse --show-toplevel) && git push", proj),
                                   ("git -C $P push", proj), ("cd $X && cd proj && git push", tmp))}
g8ok = {c: hook(c, tmp) for c in ("cd $X && cd " + str(proj) + " && git push",)}
T.check("G8 a cd that cannot be read → deny saying so (explicit path, no new ok), even inside a covered folder; an absolute cd after it reads again",
        all(v.startswith("deny") and "cannot be read" in v and "do not ask for a new ok" in v for v in g8.values())
        and g8ok == {"cd $X && cd " + str(proj) + " && git push": "allow"} and "cannot be read" not in hook("cd other && git push", tmp), str(g8) + str(g8ok))

# G5 un registro rotto non blocca mai
DB.write_text("not a database")
T.check("G5 a broken registry → allow (the guard never breaks a session)", hook("git push") == "allow", "")

# G6 hooks.json registra la guardia su Bash
hooks = json.loads((T.PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
T.check("G6 hooks.json: PreToolUse on Bash runs cm-lane-guard.py through py.sh",
        any(h.get("matcher") == "Bash" and "cm-lane-guard.py" in h["hooks"][0]["command"] and "py.sh" in h["hooks"][0]["command"] for h in hooks.get("PreToolUse", [])), "")


# G9 (12/10, ok del maintainer delle 08:20): fail-closed solo per i push. Una seconda voce lancia la guardia con
# --strict, filtrata con `if` sui comandi che pubblicano e con onFailure «block»: li' un errore esce con 1 e Claude Code
# (2.1.295+) blocca il push; un comando che il filtro non prende non avvia lo script. Provato dal vivo il 12/10 con la
# guardia rotta: 2.1.296 blocca `git push` e `cd .. && git -C repo push`, `touch` passa; 2.1.294 ignora la chiave.
import fnmatch  # noqa: E402
import re  # noqa: E402
strict = [h for e in hooks.get("PreToolUse", []) if e.get("matcher") == "Bash" for h in e["hooks"] if "--strict" in h["command"]]
plain_ = [h for e in hooks.get("PreToolUse", []) if e.get("matcher") == "Bash" for h in e["hooks"]
          if "cm-lane-guard.py" in h["command"] and "--strict" not in h["command"]]
T.check("G9 hooks.json: the strict entries carry an `if` on Bash, onFailure block and --strict through py.sh; the lane guard stays fail-open, unfiltered",
        strict and all(re.fullmatch(r"Bash\(.+\)", h.get("if", "")) and h.get("onFailure") == "block" and "py.sh" in h["command"] for h in strict)
        and len(plain_) == 1 and "onFailure" not in plain_[0] and "if" not in plain_[0], str(strict) + str(plain_))
pats = [h["if"][5:-1] for h in strict]
pushes = ["git push origin main", "cd proj && git push", "git -C ../other push", "FOO=1 git push", "nice -n 19 bash release.sh 0.8.12",
          "./release.sh 1.0", "gh release create v1", "gh release upload v1 x.zip", "npm publish", "claude plugin publish ."]
others = ["git status", "echo hello", "ls -la", "git pull --ff-only", "python3 tests/guard-lane-verify.py", "gh pr list"]
T.check("G9b the strict entries wait 30 s (17/10: at load 27 the guard took more than 10 s and the timeout blocked the release)",
        all(h.get("timeout") == 30 for h in strict), str([h.get("timeout") for h in strict]))
T.check("G9 the `if` filters take every form the guard closes and none of the ordinary commands",
        all(any(fnmatch.fnmatchcase(c, p) for p in pats) for c in pushes) and not any(fnmatch.fnmatchcase(c, p) for c in others for p in pats),
        str([c for c in pushes if not any(fnmatch.fnmatchcase(c, p) for p in pats)]) + str([c for c in others if any(fnmatch.fnmatchcase(c, p) for p in pats)]))
T.check("G9 --strict: a broken registry → exit 1 (the push is blocked); without --strict → allow, as before",
        hook("git push", proj, "--strict") == "error" and hook("git push") == "allow", "")
broken = tmp / "broken-guard.py"
broken.write_text((T.SCRIPTS / "cm-lane-guard.py").read_text() + "\nthis is broken(\n")
T.check("G9 a broken guard script exits non-zero (onFailure blocks the push), with or without --strict",
        hook("git push", proj, "--strict", script=broken) == "error", "")
DB.unlink()
cm("task", "list")
T.check("G9 --strict with a working guard decides like the plain one: deny outside a task, a normal command allowed",
        hook("git push", other, "--strict").startswith("deny") and hook("echo hello", proj, "--strict") == "allow", "")

T.finish()
