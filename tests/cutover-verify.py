#!/usr/bin/env python3
"""Verifica cutover.sh su una HOME finta (CU1–CU6): dry-run che non tocca nulla, backup completo
con rollback.sh, .bashrc senza il segmento legacy ma con il blocco supervisor, .tmux.conf con
il blocco nuovo, settings senza l'hook ora locale, crontab con registry e senza registro.sh,
symlink sessions, config con i wrapper veri, rollback che rimette tutto byte per byte."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

root = Path(__file__).resolve().parent.parent
tmp = Path(T.tmpdir())
home = T.fake_home(tmp)
# la forma vera del .bashrc di questa macchina: roba prima, il segmento legacy fra le ancore,
# il blocco supervisor dopo
(home / ".bashrc").write_text(
    'export PATH="$HOME/.local/bin:$PATH"\n# fnm\neval "`fnm env`"\n\n'
    '# Ripristino dopo un riavvio: se il container e\' su da meno di 15 minuti\n'
    '_claude_segnaposto="$HOME/.claude/prossima-sessione"\n'
    '_claude_tmux() {\n  tmux attach -t "=$nome"\n}\n'
    'claude() { _claude_tmux "" "$@"; }\n'
    '# >>> claude-pixel (managed by configure-claude-api skill) >>>\n'
    'claude-pixel() { _claude_tmux "$HOME/.claude-pixel" "$@"; }\n'
    '# <<< claude-pixel <<<\n\n'
    '# >>> supervisor (prova in parallelo, 09/09/2026) >>>\n'
    '[ -f "$HOME/.config/cc-supervisor/shell.sh" ] && . "$HOME/.config/cc-supervisor/shell.sh"\n'
    '# <<< supervisor <<<\n')
(home / ".local" / "bin").mkdir(parents=True)
for b in ("affianca", "attacca", "colore-sessione", "unisci", "sposta", "riaffianca"):
    (home / ".local" / "bin" / b).write_text("#!/bin/sh\n")
shim = home / ".local" / "bin" / "supervisor"
shim.write_text("#!/bin/sh\nexec %s \"$@\"\n" % (root / "supervisor" / "scripts" / "supervisor"))
shim.chmod(0o755)
(home / ".claude" / "sessions").mkdir()
(home / ".claude" / "sessions" / "1.json").write_text("{}")
(home / ".claude-pixel" / "sessions").mkdir()
(home / ".claude-pixel" / "sessions" / "2.json").write_text("{}")
cron = tmp / "crontab"
cron.write_text("30 3 * * * /home/x/backup.sh\n*/5 * * * * /home/x/.claude/skills/nuova-sessione/registro.sh >/dev/null 2>&1\n")
fake_crontab = tmp / "crontab.sh"
# crontab finto ATOMICO: legge tutto lo stdin in un file temporaneo e poi lo rinomina, come il
# crontab vero (spool scritto per intero); un `cat > file` diretto troncava il file mentre
# `crontab -l` della stessa pipeline lo stava ancora leggendo (race, visto il 09/09 alle 22:55)
fake_crontab.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; elif [ "$1" = "-" ] || [ -z "$1" ]; then cat > "%s.tmp" && mv "%s.tmp" "%s"; else cp "$1" "%s"; fi\n' % (cron, cron, cron, cron, cron))
fake_crontab.chmod(0o755)
cfg = home / ".config" / "cc-supervisor" / "config.json"
cfg.parent.mkdir(parents=True)
cfg.write_text(json.dumps({
    "language": "it", "state_dir": "~/.claude", "plugin_root": str(root / "supervisor"),
    "accounts": {"personale": {"config_dir": "~/.claude", "shell_command": "claude"},
                 "professionale": {"config_dir": "~/.claude-pixel", "tmux_prefix": "pix-", "shell_command": "claude-pixel"}},
    "default_account": "personale",
    "shell": {"wrappers": {"cm": "personale", "cm-pro": "professionale"}, "restore_prompt": False},
    "registry": {"cron_minutes": 5},
    "tmux": {"keybindings": {"tile": "a", "merge": "u", "move_arrows": True}},
}, indent=2))
watched = [home / ".bashrc", home / ".tmux.conf", home / ".claude" / "settings.json", home / ".claude-pixel" / "settings.json", cfg]
before = {p: p.read_text() for p in watched}
cron_before = cron.read_text()
env = {**{k: v for k, v in os.environ.items() if k == "PATH"}, "HOME": str(home), "CM_HOME": str(home),
       "CM_CRONTAB_CMD": str(fake_crontab), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_SKIP_DOCTOR": "1",
       "CM_BIN": str(shim)}


def run(*args):
    return subprocess.run(["bash", str(root / "cutover.sh"), *args], capture_output=True, text=True, env=env, timeout=120)


r = run("--dry-run")
T.check("CU1 --dry-run touches nothing and exits 0",
        r.returncode == 0 and all(p.read_text() == t for p, t in before.items()) and (home / ".local" / "bin" / "affianca").exists()
        and not list((home / ".claude").glob("supervisor-legacy-*")) and "dry-run" in r.stdout, r.stdout + r.stderr)
r = run("--yes")
bk = next(iter((home / ".claude").glob("supervisor-legacy-*")), None)
T.check("CU2 --yes exits 0, backup folder with rollback.sh, the skill, the 6 scripts, crontab, config, sessions copy",
        r.returncode == 0 and bk is not None and (bk / "rollback.sh").exists() and (bk / "skills" / "nuova-sessione" / "lancia.sh").exists()
        and all((bk / "bin" / b).exists() for b in ("affianca", "attacca", "colore-sessione", "unisci", "sposta", "riaffianca"))
        and (bk / "crontab.txt").read_text() == cron_before and (bk / "config.json").exists() and (bk / "sessions-pixel" / "2.json").exists(),
        r.stdout + r.stderr)
rc = (home / ".bashrc").read_text()
T.check("CU3 .bashrc: legacy segment gone, supervisor block kept, the rest intact",
        "_claude_tmux" not in rc and "claude-pixel()" not in rc and "supervisor/shell.sh" in rc and "fnm env" in rc and rc.startswith('export PATH="$HOME/.local/bin:$PATH"'), rc)
tc = (home / ".tmux.conf").read_text()
T.check("CU3 .tmux.conf: mouse, status off, supervisor block, no affianca binds",
        "set -g mouse on" in tc and "set -g status off" in tc and "supervisor init --tmux" in tc and "supervisor tile" in tc and "affianca" not in tc, tc)
for acc in (".claude", ".claude-pixel"):
    d = json.loads((home / acc / "settings.json").read_text())
    h = d.get("hooks", {})
    T.check(f"CU4 {acc}: local-time hook removed, other keys kept",
            "ora locale" not in json.dumps(h) and ("mcpServers" in d if acc == ".claude" else True), json.dumps(d)[:300])
T.check("CU5 crontab: registry line in, registro.sh out, other lines kept",
        "supervisor registry" in cron.read_text() and "registro.sh" not in cron.read_text() and "backup.sh" in cron.read_text(), cron.read_text())
T.check("CU5 legacy scripts and skill gone from their places",
        not (home / ".local" / "bin" / "affianca").exists() and not (home / ".claude" / "skills" / "nuova-sessione").exists(), "")
T.check("CU5 ~/.claude-pixel/sessions is a symlink to ~/.claude/sessions",
        (home / ".claude-pixel" / "sessions").is_symlink() and (home / ".claude-pixel" / "sessions" / "1.json").exists(), "")
c = json.loads(cfg.read_text())
T.check("CU5 config: wrappers claude/claude-pixel, restore_prompt true, backup beside it",
        c["shell"]["wrappers"] == {"claude": "personale", "claude-pixel": "professionale"} and c["shell"]["restore_prompt"] is True and (cfg.parent / "config.json.bak-cutover").exists(), str(c["shell"]))
r = run("--yes")
T.check("CU6 a second --yes refuses (backup folder exists) or leaves everything as it is",
        r.returncode != 0 or not list((home / ".claude").glob("supervisor-legacy-*"))[1:], r.stdout + r.stderr)
r = subprocess.run(["bash", str(bk / "rollback.sh")], capture_output=True, text=True, env=env, timeout=60)
T.check("CU6 rollback restores every watched file byte for byte, the scripts, the skill, the crontab, the sessions folder",
        r.returncode == 0 and all(p.read_text() == t for p, t in before.items()) and (home / ".local" / "bin" / "affianca").exists()
        and (home / ".claude" / "skills" / "nuova-sessione" / "lancia.sh").exists() and cron.read_text() == cron_before
        and not (home / ".claude-pixel" / "sessions").is_symlink() and (home / ".claude-pixel" / "sessions" / "2.json").exists(),
        r.stdout + r.stderr)
T.rm(tmp)
T.finish()
