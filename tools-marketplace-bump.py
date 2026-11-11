#!/usr/bin/env python3
"""La voce supervisor del marketplace fsorrentino allineata alla release (author tooling, non va nello zip).

    python3 tools-marketplace-bump.py <versione> <sha pubblico> [cartella del marketplace]

16/10/2026, il maintainer alle 08:01: «Allinea a ogni release». release.sh la chiama dopo l'installazione: porta la voce
`supervisor` di <cartella>/.claude-plugin/marketplace.json a ref v<versione> e sha <sha>, committa SOLO quel file
(«chore(marketplace): supervisor v<versione>»: il repo del marketplace e' condiviso, altri vi hanno modifiche in corso)
e fa push del ramo corrente. Cartella predefinita: ../fable-director/fable-director-marketplace accanto a questo repo
(o MARKETPLACE_DIR). Senza la cartella (un'altra macchina) stampa la voce da incollare e esce 0. Prove: release-verify RL6.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120)


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: tools-marketplace-bump.py <version> <public sha> [marketplace folder]")
    ver, sha = sys.argv[1], sys.argv[2]
    folder = Path(sys.argv[3] if len(sys.argv) > 3 else os.environ.get("MARKETPLACE_DIR") or HERE.parent / "fable-director" / "fable-director-marketplace")
    entry = {"source": "git-subdir", "url": "https://github.com/frsorrentino/supervisor.git", "path": "supervisor", "ref": f"v{ver}", "sha": sha}
    mj = folder / ".claude-plugin" / "marketplace.json"
    if not mj.is_file():
        print(f"Marketplace fsorrentino: {mj} not found here — entry to paste: {json.dumps(entry)}")
        return 0
    top = git("rev-parse", "--show-toplevel", cwd=folder).stdout.strip()
    rel = os.path.relpath(mj.resolve(), top)
    if git("status", "--porcelain", "--", rel, cwd=top).stdout.strip():
        sys.exit(f"FAIL: {mj} has uncommitted changes — commit or drop them, then: python3 tools-marketplace-bump.py {ver} {sha}")
    # revisione finale (16/10): solo dal ramo pubblicato — un checkout lasciato su un altro ramo riceverebbe il commit e
    # il push, e il marketplace letto dagli utenti resterebbe alla versione vecchia senza che nessuno se ne accorga
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=top).stdout.strip()
    default = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=top).stdout.strip().removeprefix("origin/")
    if (branch != default) if default else (branch not in ("main", "master")):
        sys.exit(f"FAIL: {top} is on branch «{branch}», not the published «{default or 'main/master'}» — switch back, then: python3 tools-marketplace-bump.py {ver} {sha}")
    pull = git("pull", "--ff-only", "-q", cwd=top)
    if pull.returncode != 0:
        sys.exit(f"FAIL: git pull --ff-only in {top}: {pull.stderr.strip()[:300]} — fix it, then: python3 tools-marketplace-bump.py {ver} {sha}")
    d = json.loads(mj.read_text())
    hit = [x for x in d.get("plugins", []) if x.get("name") == "supervisor"]
    if len(hit) != 1:
        sys.exit(f"FAIL: {len(hit)} supervisor entries in {mj}, expected 1")
    src = hit[0].setdefault("source", {})
    if (src.get("ref"), src.get("sha")) == (f"v{ver}", sha):
        print(f"Marketplace fsorrentino: supervisor already at v{ver} ({sha[:7]})")
        return 0
    src.update(ref=f"v{ver}", sha=sha)
    mj.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    c = git("commit", "-q", "-m", f"chore(marketplace): supervisor v{ver}", "--", rel, cwd=top)
    if c.returncode != 0:
        sys.exit(f"FAIL: git commit in {top}: {(c.stderr or c.stdout).strip()[:300]}")
    p = git("push", "-q", "origin", "HEAD", cwd=top)
    if p.returncode != 0:
        sys.exit(f"FAIL: git push in {top} (the commit is there, push it by hand): {p.stderr.strip()[:300]}")
    print(f"Marketplace fsorrentino: supervisor → v{ver} ({sha[:7]}), committed and pushed in {top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
