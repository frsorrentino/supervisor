#!/usr/bin/env python3
"""Verifica release.sh --check (RL1–RL5): preflight fermo su versione/badge incoerenti, suite
eseguita quando tutto combacia. Nessun push, nessun tag: --check si ferma prima. Gira su una
copia del repo in tmp con una suite finta al posto di quella vera (che altrimenti girerebbe
dentro sé stessa)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

root = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="cm-release-"))
copy = tmp / "repo"
# .claude (16/10/2026): i worktree delle sessioni stanno in .claude/worktrees, con i loro docs/; nella copia entravano
# nell'albero e il controllo privacy della release diventava rosso
shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", "tests", ".claude"))
(copy / "tests").mkdir()
marker = tmp / "marker"
(copy / "tests" / "zz-fake-verify.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
subprocess.run(["git", "init", "-q"], cwd=copy, check=True)
subprocess.run(GIT + ["add", "-A"], cwd=copy, check=True)
subprocess.run(GIT + ["commit", "-q", "-m", "x"], cwd=copy, check=True)
pj = copy / "supervisor" / ".claude-plugin" / "plugin.json"
ver = json.loads(pj.read_text())["version"]


def run(v):
    # la copia del repo sta in una cartella temporanea: la fonte di claude-observe e' quella accanto al repo vero
    return subprocess.run(["bash", "release.sh", v, "--check"], cwd=copy, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "CLAUDE_OBSERVE_SRC": str(root.parent / "claude-observe")})


r = run("9.9.9")
T.check("RL1 plugin.json not at version → FAIL, suite not run",
        r.returncode != 0 and "plugin.json" in r.stdout + r.stderr and not marker.exists(), r.stdout + r.stderr)
r = run(ver)
T.check("RL2 consistent version → preflight passes, the suite runs, stops with CHECK OK",
        r.returncode == 0 and marker.exists() and "CHECK OK" in r.stdout, r.stdout[-600:] + r.stderr[-300:])
marker.unlink(missing_ok=True)   # RL2 fallito: il controllo sopra lo dice, il test prosegue
readme = copy / "README.md"
readme.write_text(readme.read_text().replace(f"version-{ver}-blue", "version-0.0.0-blue"))
r = run(ver)
T.check("RL3 README badge off → FAIL naming the badge, suite not run",
        r.returncode != 0 and "badge" in (r.stdout + r.stderr).lower() and not marker.exists(), r.stdout + r.stderr)
readme.write_text(readme.read_text().replace("version-0.0.0-blue", f"version-{ver}-blue"))
# RL4: la voce della versione che si rilascia ancora «unreleased» (15/09/2026: la 0.4.7 e' uscita con titolo
# e tag «v0.4.7 — unreleased», e il tag non si corregge)
cl = copy / "CHANGELOG.md"
dated = cl.read_text()
head = next(l for l in dated.splitlines() if l.startswith(f"- **{ver} — "))
cl.write_text(dated.replace(head, f"- **{ver} — unreleased.**", 1))
r = run(ver)
T.check("RL4 CHANGELOG entry of the version being released still «unreleased» → FAIL naming it, suite not run",
        r.returncode != 0 and "unreleased" in r.stdout + r.stderr and not marker.exists(), r.stdout + r.stderr)
marker.unlink(missing_ok=True)   # se RL4 ha fatto girare la suite, il marker non deve far passare RL4b da solo
cl.write_text(dated.replace(head, head + "\n\n- **9.9.8 — unreleased.**", 1))
r = run(ver)
T.check("RL4 an «unreleased» entry of ANOTHER version does not block: preflight passes, CHECK OK",
        r.returncode == 0 and marker.exists() and "CHECK OK" in r.stdout, r.stdout[-600:] + r.stderr[-300:])
# RL5 (17/09/2026): lo zip impacchetta la cartella del plugin dal disco — un file non tracciato li' dentro uscirebbe
# nel pacchetto pubblico (un docs/recap.md scritto per errore sotto scripts/, visto per caso prima della 0.4.13)
marker.unlink(missing_ok=True)
stray = copy / "supervisor" / "scripts" / "docs" / "recap.md"
stray.parent.mkdir(parents=True)
stray.write_text("privato\n")
r = run(ver)
T.check("RL5 an untracked file under the plugin folder → FAIL naming it, suite not run",
        r.returncode != 0 and "untracked" in r.stdout and "scripts/docs/recap.md" in r.stdout and not marker.exists(), r.stdout[-400:] + r.stderr[-200:])
shutil.rmtree(stray.parent)
(copy / "note-fuori-dal-plugin.txt").write_text("x\n")
r = run(ver)
T.check("RL5 an untracked file OUTSIDE the plugin folder does not block (it never ships): CHECK OK",
        r.returncode == 0 and "CHECK OK" in r.stdout, r.stdout[-400:] + r.stderr[-200:])
# RL6 (16/10/2026, il maintainer alle 08:01: «Allinea a ogni release»): la release aggiorna da se' la voce supervisor del
# marketplace fsorrentino (ref vX, sha del commit pubblico), committa solo marketplace.json e fa push
mk = tmp / "mkt"
bare = tmp / "mkt-remote.git"
subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
subprocess.run(["git", "clone", "-q", str(bare), str(mk)], check=True, capture_output=True)
(mk / "fable-director-marketplace" / ".claude-plugin").mkdir(parents=True)
mj = mk / "fable-director-marketplace" / ".claude-plugin" / "marketplace.json"
other = {"name": "fable-director", "source": {"source": "git-subdir", "url": "https://example.invalid/fd.git", "path": "fd", "ref": "v1.0.0", "sha": "1" * 40}}
sup = {"name": "supervisor", "source": {"source": "git-subdir", "url": "https://github.com/frsorrentino/supervisor.git", "path": "supervisor", "ref": "v0.8.26", "sha": "2" * 40},
       "description": "Supervisor — più sessioni"}
mj.write_text(json.dumps({"name": "fsorrentino", "plugins": [other, sup]}, indent=2, ensure_ascii=False) + "\n")
(mk / "unrelated.txt").write_text("a\n")
subprocess.run(GIT + ["add", "-A"], cwd=mk, check=True)
subprocess.run(GIT + ["commit", "-q", "-m", "init"], cwd=mk, check=True)
subprocess.run(["git", "push", "-q", "origin", "HEAD"], cwd=mk, check=True, capture_output=True)
(mk / "unrelated.txt").write_text("someone else's work in progress\n")
bump = lambda *a: subprocess.run(["python3", str(root / "tools-marketplace-bump.py"), *a], capture_output=True, text=True, timeout=60,  # noqa: E731
                                 env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
r6 = bump("0.8.27", "3" * 40, str(mk / "fable-director-marketplace"))
d6 = json.loads(mj.read_text())
remote6 = subprocess.run(["git", "--git-dir", str(bare), "log", "-1", "--format=%s", "--name-only"], capture_output=True, text=True).stdout.split()
T.check("RL6 the marketplace entry: supervisor → ref v0.8.27 and the public sha, the other plugin and the description untouched, the file's format kept",
        r6.returncode == 0 and d6["plugins"][1]["source"] == dict(sup["source"], ref="v0.8.27", sha="3" * 40) and d6["plugins"][0] == other
        and d6["plugins"][1]["description"] == sup["description"] and mj.read_text() == json.dumps(d6, indent=2, ensure_ascii=False) + "\n", r6.stdout + r6.stderr)
T.check("RL6 committed alone as «chore(marketplace): supervisor v0.8.27» and pushed; someone else's uncommitted file stays out and as it was",
        remote6 == ["chore(marketplace):", "supervisor", "v0.8.27", "fable-director-marketplace/.claude-plugin/marketplace.json"]
        and (mk / "unrelated.txt").read_text() == "someone else's work in progress\n", " ".join(remote6))
r6b = bump("0.8.27", "3" * 40, str(mk / "fable-director-marketplace"))
r6c = bump("0.8.28", "4" * 40, str(tmp / "no-such-marketplace"))
T.check("RL6 already at that version → nothing to commit, exit 0; no marketplace folder (another machine) → it prints the entry to paste and exits 0",
        r6b.returncode == 0 and "already" in r6b.stdout and r6c.returncode == 0 and '"ref": "v0.8.28"' in r6c.stdout and "4" * 40 in r6c.stdout, r6b.stdout + r6c.stdout + r6c.stderr)
subprocess.run(["git", "checkout", "-q", "-b", "feature-x"], cwd=mk, check=True)
r6d = bump("0.8.29", "5" * 40, str(mk / "fable-director-marketplace"))
remote6d = subprocess.run(["git", "--git-dir", str(bare), "log", "--all", "-1", "--format=%s"], capture_output=True, text=True).stdout.strip()
T.check("RL6 the marketplace checkout on another branch (not the published one) → FAIL naming it, nothing committed or pushed",
        r6d.returncode != 0 and "feature-x" in r6d.stdout + r6d.stderr and json.loads(mj.read_text())["plugins"][1]["source"]["ref"] == "v0.8.27"
        and remote6d == "chore(marketplace): supervisor v0.8.27", r6d.stdout + r6d.stderr + remote6d)
subprocess.run(["git", "checkout", "-q", "-"], cwd=mk, check=True)
T.check("RL6 release.sh calls the bump after the install, with the public sha", "tools-marketplace-bump.py \"$VER\" \"$PUB\"" in (root / "release.sh").read_text(), "")
shutil.rmtree(tmp)
T.finish()
