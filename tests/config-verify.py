#!/usr/bin/env python3
"""Verifica cm-config.py: default, merge, --sh, --get, init, doctor, shim.

C1  default senza file: language rilevata (settings.json, poi LANG, poi en — S03), root ~, un account `default`
C2  merge dal file + default guidati dalla lingua (prefisso ora locale)
C3  --get con chiave puntata, ~ espansa con CM_HOME
C4  --sh: export CM_* valutabili da bash (liste, dict annidati, TSV)
C5  --dump-defaults == config.example.json (sincronia)
C6  chiave sconosciuta nel file → avviso, exit 0; in --get → exit 1
C7  catalogo messaggi: stesse chiavi it/en, msg() con segnaposto
C8  init su macchina finta: backend, radice, due account, prefisso, wrapper, forma
C9  init: mappa cartelle, cartelle progetto, cartelle escluse
C10 init: stato legacy condiviso (D4) vs macchina pulita
C11 init: chrome-bridge cli, lingua dai settings
C12 init --yes scrive; secondo giro rifiuta senza --force; --dry-run non scrive
C15 init --tmux: blocco da tmux.keybindings; --yes accoda a ~/.tmux.conf una volta sola
C13 doctor: WARN hook duplicato, WARN chrome-bridge assente, FAIL account mancante
C13d doctor: relay.enabled → PASS/FAIL cryptography e crontab con il rimedio; relay spento → niente riga
C13g prerequisiti (13/10): sistema e gestore riconosciuti (apt, dnf, pacman, brew, ChromeOS, WSL2), comando di
     installazione per ciascuno, versioni minime di python3 e claude, il claude di Windows in WSL, init --dry-run li mostra
C13e doctor: dati dell'app Firebase per il QR (1.15) → WARN senza, PASS da relay.firebase_app o google-services.json
C14 shim: risolve la radice da installed_plugins.json ed esegue il dispatcher
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = T.tmpdir()
home = T.fake_home(tmp)
machine = T.fake_machine(tmp, home)
cfg_missing = Path(tmp) / "no-such-config.json"

# C1
r = T.run_config(["--get", "language"], home, cfg_missing)
# S03 (14/09/2026): senza `language` nella config vale la lingua rilevata come in init — la home finta ha
# «language: Italiano» in ~/.claude/settings.json → it; una home senza e senza LANG → en
T.check("C1 no language in the config: detected from ~/.claude/settings.json (Italiano) → it", r.stdout.strip() == "it", r.stdout + r.stderr)
home_nolang = T.fake_home(T.tmpdir(), language=None)
r = T.run_config(["--get", "language"], home_nolang, cfg_missing)
T.check("C1 no language anywhere (no settings language, no LANG) → en", r.stdout.strip() == "en", r.stdout + r.stderr)
r = T.run_config(["--get", "workspace.root"], home, cfg_missing)
T.check("C1 default root = HOME", r.stdout.strip() == str(home), r.stdout + r.stderr)
r = T.run_config(["--get", "accounts"], home, cfg_missing)
T.check("C1 default one account", '"default"' in r.stdout and '"config_dir"' in r.stdout, r.stdout + r.stderr)

# C2
cfg = Path(tmp) / "config.json"
cfg.write_text(json.dumps({"language": "it",
                           "accounts": {"personale": {"config_dir": "~/.claude"},
                                        "professionale": {"config_dir": "~/.claude-pixel", "tmux_prefix": "pix-"}},
                           "default_account": "personale",
                           "workspace": {"root": "~/Desktop/workspaces"},
                           "folder_map": [{"path": "~/Desktop/workspaces/agenzia", "account": "professionale"}],
                           "hooks": {"local_time": {"format": "%H:%M"}}}))
r = T.run_config(["--get", "hooks.local_time.prefix"], home, cfg)
T.check("C2 lang-driven default prefix", r.stdout.strip() == "[ora locale]", r.stdout + r.stderr)
r = T.run_config(["--get", "hooks.local_time.format"], home, cfg)
T.check("C2 file overrides lang default", r.stdout.strip() == "%H:%M", r.stdout + r.stderr)
r = T.run_config(["--get", "accounts.professionale.shape"], home, cfg)
T.check("C2 account field default (shape)", r.stdout.strip() == "square", r.stdout + r.stderr)
r = T.run_config(["--get", "accounts.personale.shape"], home, cfg)
T.check("C2 first account circle", r.stdout.strip() == "circle", r.stdout + r.stderr)
r = T.run_config(["--get", "report.subdir"], home, cfg)
T.check("C2 lang-driven report subdir", r.stdout.strip() == "docs/segnalazioni", r.stdout + r.stderr)

# C3
r = T.run_config(["--get", "accounts.professionale.config_dir"], home, cfg)
T.check("C3 ~ expanded", r.stdout.strip() == str(home / ".claude-pixel"), r.stdout + r.stderr)
r = T.run_config(["--get", "tabs.colors.circle"], home, cfg)
T.check("C3 list as json", r.stdout.strip().startswith("[") and "🔴" in r.stdout, r.stdout + r.stderr)

# C4
r = T.run_config(["--sh"], home, cfg)
sh = r.stdout
T.check("C4 --sh exit 0", r.returncode == 0, r.stderr)
T.check("C4 CM_LANGUAGE", "export CM_LANGUAGE='it'" in sh or 'export CM_LANGUAGE="it"' in sh, sh[:400])
T.check("C4 CM_ACCOUNTS_KEYS", "CM_ACCOUNTS_KEYS='personale professionale'" in sh, sh[:2000])
T.check("C4 nested account var expanded",
        f"CM_ACCOUNTS_PROFESSIONALE_CONFIG_DIR='{home}/.claude-pixel'" in sh, sh[:3000])
T.check("C4 folder map TSV", f"CM_FOLDER_MAP='{home}/Desktop/workspaces/agenzia\tprofessionale" in sh, sh[:3000])
T.check("C4 list space-joined", "CM_TABS_COLORS_CIRCLE='🔴 🟠 🟡 🟢 🔵 🟣 ⚪'" in sh, sh[:3000])
T.check("C4 bool as true/false", "CM_SESSION_REMOTE_CONTROL='true'" in sh, sh[:3000])
b = subprocess.run(["bash", "-c", f'eval "$(python3 {T.SCRIPTS}/cm-config.py --sh)"; echo "$CM_LANGUAGE|$CM_ACCOUNTS_PERSONALE_TMUX_PREFIX|$CM_TALK_QUIET_S"'],
                   capture_output=True, text=True,
                   env={"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg)})
T.check("C4 eval in bash", b.stdout.strip() == "it||6", b.stdout + b.stderr)

# C5
r = T.run_config(["--dump-defaults"], home, cfg_missing)
example = (T.PLUGIN / "config.example.json").read_text() if (T.PLUGIN / "config.example.json").exists() else ""
T.check("C5 config.example.json in sync with defaults", r.stdout.strip() == example.strip(),
        "run: cm-config.py --dump-defaults > supervisor/config.example.json")

# C6
bad = Path(tmp) / "bad.json"
bad.write_text(json.dumps({"language": "it", "nonexistent": 1, "talk": {"typo": 2}}))
r = T.run_config(["--get", "language"], home, bad)
T.check("C6 unknown keys warn, still works", r.returncode == 0 and "nonexistent" in r.stderr and "talk.typo" in r.stderr,
        r.stdout + r.stderr)
r = T.run_config(["--get", "talk.typo"], home, bad)
T.check("C6 --get unknown key exit 1", r.returncode == 1, r.stdout + r.stderr)

# C7
it = json.loads((T.PLUGIN / "messages" / "it.json").read_text())
en = json.loads((T.PLUGIN / "messages" / "en.json").read_text())
T.check("C7 same message keys it/en", set(it) == set(en), f"only it: {set(it)-set(en)}; only en: {set(en)-set(it)}")
r = T.run_config(["--messages"], home, cfg)
T.check("C7 --messages exports MSG_*", "export MSG_" in r.stdout and r.returncode == 0, r.stdout[:300] + r.stderr)
r = T.run_config(["--msg", "init.detected", "key=x", "value=y", "source=z"], home, cfg)
T.check("C7 --msg formats placeholders", "x" in r.stdout and "y" in r.stdout and "z" in r.stdout and "{" not in r.stdout,
        r.stdout + r.stderr)

# C8 init on the fake machine
r = T.run_config(["init", "--dry-run", "--json"], home, cfg_missing, machine)
T.check("C8 init exit 0", r.returncode == 0, r.stderr[-800:])
try:
    out = json.loads(r.stdout)
    detected, sources = out["config"], out["sources"]
except Exception as e:  # noqa: BLE001
    detected, sources = {}, {}
    T.check("C8 init --json parses", False, f"{e}: {r.stdout[:300]} {r.stderr[-300:]}")
acc = detected.get("accounts", {})
T.check("C8 backend chromeos", detected.get("terminal", {}).get("backend") == "chromeos", str(detected.get("terminal")))
T.check("C8 root from registry", detected.get("workspace", {}).get("root") == "~/Desktop/workspaces", str(detected.get("workspace")))
T.check("C8 two accounts", set(acc) == {"personale", "professionale"}, str(list(acc)))
T.check("C8 professionale config_dir", acc.get("professionale", {}).get("config_dir") == "~/.claude-pixel", str(acc))
T.check("C8 professionale prefix pix- (from processes)", acc.get("professionale", {}).get("tmux_prefix") == "pix-", str(acc))
T.check("C8 personale prefix empty", acc.get("personale", {}).get("tmux_prefix") == "", str(acc))
T.check("C8 shell_command claude-pixel (from .bashrc)", acc.get("professionale", {}).get("shell_command") == "claude-pixel", str(acc))
T.check("C8 shapes circle/square", acc.get("personale", {}).get("shape") == "circle" and acc.get("professionale", {}).get("shape") == "square", str(acc))
T.check("C8 default_account personale", detected.get("default_account") == "personale", str(detected.get("default_account")))
T.check("C8 every value has a source", all(k in sources for k in ("terminal.backend", "workspace.root", "accounts.professionale.tmux_prefix")), str(list(sources))[:300])
T.check("C8 no vendor name in plugin defaults", ("pixel" + "farm") not in T.run_config(["--dump-defaults"], home, cfg_missing).stdout.lower())   # a pezzi (S10)

# 06/10 (revisione della directory): il flag di bypass non si copia piu' da solo nella config; lo si segnala soltanto
T.check("C8 session.claude_args: the bypass flag of the live sessions is NOT copied (a plugin never turns bypass on), only pointed out in the source",
        not detected.get("session", {}).get("claude_args") and "--dangerously-skip-permissions" in str(sources.get("session.claude_args")), str(detected.get("session")) + str(sources.get("session.claude_args")))
# C9
fm = detected.get("folder_map", [])
T.check("C9 folder_map agenzia→professionale", any(e.get("path") == "~/Desktop/workspaces/agenzia" and e.get("account") == "professionale" for e in fm), str(fm))
T.check("C9 folder_map personali→personale", any(e.get("path") == "~/Desktop/workspaces/personali" and e.get("account") == "personale" for e in fm), str(fm))
pd = detected.get("workspace", {}).get("project_dirs", [])
T.check("C9 project_dirs from sessions", set(pd) >= {"personali", "agenzia/clienti", "agenzia/nostri"}, str(pd))
ex = detected.get("workspace", {}).get("excluded_dirs", [])
T.check("C9 excluded_dirs with _archivio/_prod_backups", {"_archivio", "_prod_backups", ".git", "node_modules"} <= set(ex), str(ex))

# C10
T.check("C10 legacy: registry NOT shared (legacy labels differ)", detected.get("registry", {}).get("file") == "~/.claude/sessions.json", str(detected.get("registry")))
T.check("C10 legacy: colors path shared", detected.get("tabs", {}).get("color_registry") == "~/.claude/colori-sessione", str(detected.get("tabs")))
T.check("C10 legacy: placeholder path", detected.get("tile", {}).get("placeholder_file") == "~/.claude/prossima-sessione", str(detected.get("tile", {}).get("placeholder_file")))
tmp2 = T.tmpdir()
home2 = T.fake_home(tmp2, legacy=False, second_account=False, workspace=False, language=None)
machine2 = T.fake_machine(tmp2, home2, chromeos=False, procs=[])
r2 = T.run_config(["init", "--dry-run", "--json"], home2, Path(tmp2) / "none.json", machine2)
try:
    d2 = json.loads(r2.stdout)["config"]
except Exception:  # noqa: BLE001
    d2 = {}
    T.check("C10 clean init parses", False, r2.stdout[:300] + r2.stderr[-300:])
T.check("C10 clean: state_dir default", d2.get("state_dir") == "~/.local/state/cc-supervisor", str(d2.get("state_dir")))
T.check("C10 clean: registry under state_dir", d2.get("registry", {}).get("file") == "~/.local/state/cc-supervisor/sessions.json", str(d2.get("registry")))
T.check("C10 clean: backend gnome (gnome-terminal in PATH)", d2.get("terminal", {}).get("backend") == "gnome", str(d2.get("terminal")))
T.check("C10 clean: one account named personal (lang en)", list(d2.get("accounts", {})) == ["personal"], str(d2.get("accounts")))
T.check("C10 clean: root = ~", d2.get("workspace", {}).get("root") == "~", str(d2.get("workspace")))

# C11
T.check("C11 chrome-bridge cli from .claude.json", detected.get("tile", {}).get("chrome_bridge_cli") == "~/cb/server/cli.js", str(detected.get("tile", {}).get("chrome_bridge_cli")))
T.check("C11 language it from settings", detected.get("language") == "it", str(detected.get("language")))
T.check("C11 shell wrappers", detected.get("shell", {}).get("wrappers") == {"claude": "personale", "claude-pixel": "professionale"}, str(detected.get("shell", {}).get("wrappers")))
T.check("C11 shell aliases italian", detected.get("shell", {}).get("aliases", {}).get("lancia") == "launch", str(detected.get("shell", {}).get("aliases")))
T.check("C11 tmux keybindings from .tmux.conf", detected.get("tmux", {}).get("keybindings") == {"tile": "a", "merge": "u", "move_arrows": True}, str(detected.get("tmux")))

# C12
target = Path(tmp) / "written" / "config.json"
r = T.run_config(["init", "--dry-run"], home, target, machine)
T.check("C12 --dry-run writes nothing", not target.exists() and r.returncode == 0, r.stderr[-300:])
T.check("C12 --dry-run prints value and source", "terminal.backend" in r.stdout and "chromeos" in r.stdout, r.stdout[:500])
r = T.run_config(["init", "--yes"], home, target, machine)
T.check("C12 --yes writes config", target.exists() and r.returncode == 0, r.stdout[-300:] + r.stderr[-300:])
written = json.loads(target.read_text()) if target.exists() else {}
T.check("C12 written config reloads identically", written.get("accounts", {}).get("professionale", {}).get("tmux_prefix") == "pix-", str(written.get("accounts")))
r = T.run_config(["init", "--yes"], home, target, machine)
T.check("C12 second init refuses without --force", r.returncode != 0 and "--force" in (r.stdout + r.stderr), r.stdout + r.stderr)
r = T.run_config(["init", "--yes", "--force"], home, target, machine)
T.check("C12 --force overwrites", r.returncode == 0, r.stderr[-300:])

# C13 doctor
r = T.run_config(["doctor"], home, target, machine)
T.check("C13 doctor exit 0 on fake machine", r.returncode == 0, r.stdout + r.stderr)
T.check("C13 WARN duplicate local-time hook (both accounts)", r.stdout.count("WARN") >= 2 and "ora locale" in r.stdout.lower() or "local-time" in r.stdout, r.stdout)
T.check("C13 INFO legacy scripts present", "legacy" in r.stdout.lower(), r.stdout)
T.check("C13 PASS chrome-bridge cli", "PASS" in r.stdout and "cli.js" in r.stdout, r.stdout)
broken = json.loads(target.read_text())
broken["accounts"]["professionale"]["config_dir"] = "~/.claude-nope"
broken["tile"]["chrome_bridge_cli"] = "~/nope/cli.js"
(Path(tmp) / "broken.json").write_text(json.dumps(broken))
r = T.run_config(["doctor"], home, Path(tmp) / "broken.json", machine)
T.check("C13 FAIL missing account dir → exit 1", r.returncode == 1 and "FAIL" in r.stdout and ".claude-nope" in r.stdout, r.stdout)
T.check("C13 WARN missing chrome-bridge cli", "WARN" in r.stdout and "nope/cli.js" in r.stdout, r.stdout)
r = T.run_config(["doctor"], home, cfg_missing, machine)
T.check("C13 WARN no config → suggests init", r.returncode == 0 and "init" in r.stdout, r.stdout)

# C13f (09/10) doctor: a confirmed host without the hosts poller in the crontab → WARN with «supervisor hosts install»
_cr = Path(tmp) / "crontab-c13f"
_fc = Path(tmp) / "crontab-c13f.sh"
_fc.write_text('#!/bin/sh\ncat "%s"\n' % _cr)
_fc.chmod(0o755)
_hc = json.loads(target.read_text())
_hc["hosts"] = {"win": {"kind": "windows-native", "transport": {"ssh": "win"}, "confirmed_at": "2026-10-02T00:10"}}
(Path(tmp) / "hosts.json").write_text(json.dumps(_hc))
_cr.write_text("")
r = T.run_config(["doctor"], home, Path(tmp) / "hosts.json", machine, extra_env={"CM_CRONTAB_CMD": str(_fc)})
T.check("C13f confirmed host, no poller line → WARN with the fix supervisor hosts install", "hosts_poller_missing" in r.stdout and "supervisor hosts install" in r.stdout, r.stdout)
_cr.write_text("* * * * * /x/supervisor hosts poll --cron >/dev/null 2>&1\n")
r = T.run_config(["doctor"], home, Path(tmp) / "hosts.json", machine, extra_env={"CM_CRONTAB_CMD": str(_fc)})
T.check("C13f with the poller line → PASS", "PASS hosts_poller_ok" in r.stdout, r.stdout)
r = T.run_config(["doctor"], home, target, machine, extra_env={"CM_CRONTAB_CMD": str(_fc)})
T.check("C13f no confirmed host → no poller row", "hosts_poller" not in r.stdout, r.stdout)

VER_CHECKOUT = json.loads((T.PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"]
# C13b doctor: plugin nella cache via `claude plugin list --json` (finto: CM_FAKE_PLUGIN_LIST)
pl = Path(tmp) / "plugins.json"
pl.write_text(json.dumps([{"id": "supervisor@supervisor-dev", "version": "0.0.1", "enabled": True}]))
r = T.run_config(["doctor"], home, target, machine, extra_env={"CM_FAKE_PLUGIN_LIST": str(pl)})
T.check("C13b cache older than the checkout → WARN with both versions and the update command", "WARN" in r.stdout and "0.0.1" in r.stdout and "claude plugin update supervisor" in r.stdout, r.stdout)
pl.write_text(json.dumps([{"id": "supervisor@supervisor-dev", "version": VER_CHECKOUT, "enabled": True, "noteDetails": "auto-update on"}]))
r = T.run_config(["doctor"], home, target, machine, extra_env={"CM_FAKE_PLUGIN_LIST": str(pl)})
T.check("C13b same version → PASS with the note", "PASS plugin_ok" in r.stdout and "auto-update on" in r.stdout, r.stdout)
pl.write_text(json.dumps([{"id": "supervisor@supervisor-dev", "version": VER_CHECKOUT, "enabled": True, "errorDetails": "hooks.json invalid"}]))
r = T.run_config(["doctor"], home, target, machine, extra_env={"CM_FAKE_PLUGIN_LIST": str(pl)})
T.check("C13b errorDetails → WARN quoting it", "WARN plugin_error" in r.stdout and "hooks.json invalid" in r.stdout, r.stdout)
pl.write_text("[]")
r = T.run_config(["doctor"], home, target, machine, extra_env={"CM_FAKE_PLUGIN_LIST": str(pl)})
T.check("C13b not installed → WARN with the install command", "WARN plugin_missing" in r.stdout and "claude plugin install" in r.stdout, r.stdout)

# C13c (S01, 14/09): lo shim c'e' ma ~/.local/bin non e' nel PATH → WARN con il rimedio; nel PATH → nessuna riga.
# Un file macchina a parte: fake_machine riscriverebbe quello condiviso.
shim13 = home / ".local" / "bin" / "supervisor"
shim13.parent.mkdir(parents=True, exist_ok=True)
shim13.write_text("#!/bin/sh\n")
m13 = json.loads(Path(machine).read_text())
for label, path_value in (("out", "/usr/bin:/bin"), ("in", f"/usr/bin:{home / '.local' / 'bin'}:/bin")):
    m13["env"] = dict(m13.get("env") or {}, PATH=path_value)
    (Path(tmp) / f"machine-c13c-{label}.json").write_text(json.dumps(m13))
r_out = T.run_config(["doctor"], home, target, Path(tmp) / "machine-c13c-out.json")
r_in = T.run_config(["doctor"], home, target, Path(tmp) / "machine-c13c-in.json")
T.check("C13c shim dir not in PATH → WARN naming ~/.local/bin with the remedy (export PATH)", "WARN shim_not_in_path" in r_out.stdout and "~/.local/bin" in r_out.stdout and 'export PATH="$HOME/.local/bin:$PATH"' in r_out.stdout, r_out.stdout)
T.check("C13c shim dir in PATH → no such line", "shim_not_in_path" not in r_in.stdout and "PASS shim_ok" in r_in.stdout, r_in.stdout)
shim13.unlink()

# C13d (24/09): le dipendenze del relay — cryptography e crontab — si vedono in doctor solo con relay.enabled.
# Macchina finta: "modules" e "which" dichiarano cosa c'e'.
m13d = json.loads(Path(machine).read_text())
cfg13d = json.loads(target.read_text())
cfg13d["relay"] = dict(cfg13d.get("relay") or {}, enabled=True)
(Path(tmp) / "relay-on.json").write_text(json.dumps(cfg13d))
m13d["which"] = [w for w in m13d["which"] if w != "crontab"] + ["apt-get"]; m13d["modules"] = []
(Path(tmp) / "machine-c13d-none.json").write_text(json.dumps(m13d))
r = T.run_config(["doctor"], home, Path(tmp) / "relay-on.json", Path(tmp) / "machine-c13d-none.json")
T.check("C13d relay on, both missing → FAIL crypto_missing and FAIL crontab_missing with the remedies, exit 1",
        r.returncode == 1 and "FAIL crypto_missing" in r.stdout and "FAIL crontab_missing" in r.stdout
        and "sudo apt install python3-cryptography" in r.stdout and "sudo apt install cron" in r.stdout, r.stdout)
m13d["which"] = m13d["which"] + ["crontab"]; m13d["modules"] = ["cryptography.hazmat.primitives.ciphers.aead"]
(Path(tmp) / "machine-c13d-all.json").write_text(json.dumps(m13d))
r = T.run_config(["doctor"], home, Path(tmp) / "relay-on.json", Path(tmp) / "machine-c13d-all.json")
T.check("C13d relay on, both present → PASS crypto_ok and PASS crontab_ok, exit 0", r.returncode == 0 and "PASS crypto_ok" in r.stdout and "PASS crontab_ok" in r.stdout, r.stdout)
r = T.run_config(["doctor"], home, target, Path(tmp) / "machine-c13d-none.json")
T.check("C13d relay off, both missing → INFO lines «only for the relay» with the remedy, no FAIL, exit 0",
        r.returncode == 0 and "INFO relay_crypto_missing" in r.stdout and "INFO relay_crontab_missing" in r.stdout
        and "FAIL crypto" not in r.stdout and "FAIL crontab" not in r.stdout, r.stdout)

# C13g (13/10): prerequisiti con il comando di installazione del sistema in uso. Dalla prova d'installazione da zero.
cfg13g = json.loads(target.read_text())
cfg13g["language"] = "en"; cfg13g["terminal"]["backend"] = "none"
(Path(tmp) / "c13g.json").write_text(json.dumps(cfg13g))


def doctor_on(name, **kw):
    mm = json.loads(Path(machine).read_text())
    mm.update(kw)
    (Path(tmp) / f"machine-c13g-{name}.json").write_text(json.dumps(mm))
    return T.run_config(["doctor"], home, Path(tmp) / "c13g.json", Path(tmp) / f"machine-c13g-{name}.json")

base = [w for w in json.loads(Path(machine).read_text())["which"] if w != "tmux"]
r = doctor_on("apt", which=base + ["apt-get"], exists=[])
T.check("C13g Linux + apt: tmux missing → FAIL with «sudo apt install tmux», system line, firebase INFO with npm",
        r.returncode == 1 and "FAIL tool_missing — tmux" in r.stdout and "fix: sudo apt install tmux" in r.stdout
        and "system: Linux · package manager: apt" in r.stdout and "npm install -g firebase-tools" in r.stdout, r.stdout)
r = doctor_on("dnf", which=base + ["dnf"], exists=[])
T.check("C13g dnf → «sudo dnf install tmux», cronie for crontab", "fix: sudo dnf install tmux" in r.stdout and "sudo dnf install cronie" in r.stdout, r.stdout)
r = doctor_on("pacman", which=base + ["pacman"], exists=[])
T.check("C13g pacman → «sudo pacman -S tmux», python-cryptography", "fix: sudo pacman -S tmux" in r.stdout and "sudo pacman -S python-cryptography" in r.stdout, r.stdout)
r = doctor_on("brew", which=base, exists=[], platform="darwin")
T.check("C13g macOS → brew, crontab is not a missing package", "system: macOS · package manager: brew" in r.stdout and "fix: brew install tmux" in r.stdout, r.stdout)
r = doctor_on("none", which=base, exists=[])
T.check("C13g no package manager → generic line naming the tool", "install tmux with your system's tools" in r.stdout, r.stdout)
r = doctor_on("cros", which=base + ["apt-get"])
T.check("C13g ChromeOS → system line names the Linux development environment, apt", "system: ChromeOS (Linux development environment) · package manager: apt" in r.stdout, r.stdout)
r = doctor_on("wslwin", which=base + ["tmux", "apt-get"], exists=[], env={"LANG": "en_US.UTF-8", "WSL_DISTRO_NAME": "Ubuntu"},
              paths={"claude": "/mnt/c/Users/u/AppData/Roaming/npm/claude"})
T.check("C13g WSL2 with the Windows claude → FAIL claude_windows with the native installer",
        r.returncode == 1 and "system: WSL2" in r.stdout and "FAIL claude_windows" in r.stdout and "curl -fsSL https://claude.ai/install.sh | PATH=/usr/local/bin:/usr/bin:/bin bash" in r.stdout, r.stdout)
r = doctor_on("old", which=base + ["tmux", "apt-get"], versions={"python3": "Python 3.6.9", "claude": "2.1.200 (Claude Code)", "tmux": "tmux 3.4"})
T.check("C13g too old: python3 3.6 and claude 2.1.200 → FAIL too_old, claude update",
        "FAIL too_old — python3 Python 3.6.9: needs 3.8 or newer" in r.stdout and "FAIL too_old — claude 2.1.200" in r.stdout and "fix: claude update" in r.stdout, r.stdout)
r = doctor_on("new", which=base + ["tmux", "apt-get"], versions={"python3": "Python 3.12.3", "claude": "2.1.296 (Claude Code)"})
T.check("C13g recent versions → PASS, no too_old", "too_old" not in r.stdout and "PASS tool_ok — claude 2.1.296" in r.stdout, r.stdout)
r = doctor_on("noterm", which=[w for w in base if w != "gnome-terminal"] + ["tmux", "apt-get"], exists=[], env={"LANG": "en_US.UTF-8", "WSL_DISTRO_NAME": "Ubuntu"})
r = T.run_config(["init", "--dry-run"], home, Path(tmp) / "absent.json", Path(tmp) / "machine-c13g-noterm.json")
T.check("C13g init --dry-run prints the prerequisites: WSL2 with no terminal → Windows Terminal via winget",
        r.returncode == 0 and "winget install Microsoft.WindowsTerminal" in r.stdout
        and "FAIL, " in r.stdout, r.stdout)

# C13e (24/09, contratto 1.15): i dati dell'app Firebase per il QR di `relay pair` — WARN senza, PASS da relay.firebase_app
# o da un google-services.json (il suo client Android; relay.app_package se ne ha piu' d'uno); relay spento: niente riga
r = T.run_config(["doctor"], home, Path(tmp) / "relay-on.json", Path(tmp) / "machine-c13d-all.json")
T.check("C13e relay on, no app data → WARN firebase_app_missing (relay pair shows no QR) with the remedy, exit 0", r.returncode == 0 and "WARN firebase_app_missing" in r.stdout and "relay.google_services" in r.stdout and "relay.firebase_app" in r.stdout, r.stdout)
cfg13e = json.loads((Path(tmp) / "relay-on.json").read_text())
cfg13e["relay"]["firebase_app"] = {"api_key": "AIza-x", "project_id": "demo-proj", "app_id": "1:1:android:a"}
(Path(tmp) / "relay-app.json").write_text(json.dumps(cfg13e))
r = T.run_config(["doctor"], home, Path(tmp) / "relay-app.json", Path(tmp) / "machine-c13d-all.json")
T.check("C13e relay.firebase_app complete → PASS firebase_app_ok with the project id (config)", r.returncode == 0 and "PASS firebase_app_ok" in r.stdout and "demo-proj" in r.stdout and "(config)" in r.stdout, r.stdout)
gs = Path(tmp) / "google-services.json"
gs.write_text(json.dumps({"project_info": {"project_number": "123", "project_id": "gs-proj", "firebase_url": "https://gs-proj-default-rtdb.europe-west1.firebasedatabase.app"},
                          "client": [{"client_info": {"mobilesdk_app_id": "1:123:android:other", "android_client_info": {"package_name": "com.example.other"}}, "api_key": [{"current_key": "AIza-other"}]},
                                     {"client_info": {"mobilesdk_app_id": "1:123:android:cmw", "android_client_info": {"package_name": "it.example.cmwatch"}}, "api_key": [{"current_key": "AIza-cmw"}]}]}))
cfg13e["relay"]["firebase_app"] = {}; cfg13e["relay"]["google_services"] = str(gs)
(Path(tmp) / "relay-gs.json").write_text(json.dumps(cfg13e))
r = T.run_config(["doctor"], home, Path(tmp) / "relay-gs.json", Path(tmp) / "machine-c13d-all.json")
import importlib.util as _ilu
_sp = _ilu.spec_from_file_location("cm_config", T.SCRIPTS / "cm-config.py"); _cmc = _ilu.module_from_spec(_sp); _sp.loader.exec_module(_cmc)
T.check("C13e a google-services.json with two Android clients and no relay.app_package → WARN saying to set relay.app_package", r.returncode == 0 and "WARN firebase_app_missing" in r.stdout and "relay.app_package" in r.stdout and _cmc.relay_firebase_app(cfg13e) == (None, "relay.app_file_many"), r.stdout)
cfg13e["relay"]["app_package"] = "it.example.cmwatch"
(Path(tmp) / "relay-gs.json").write_text(json.dumps(cfg13e))
r = T.run_config(["doctor"], home, Path(tmp) / "relay-gs.json", Path(tmp) / "machine-c13d-all.json")
app13e, src13e = _cmc.relay_firebase_app(cfg13e)
T.check("C13e relay.app_package set → PASS with the project of project_info and that client (not the other package): its app_id and api_key", r.returncode == 0 and "PASS firebase_app_ok" in r.stdout and "gs-proj" in r.stdout and app13e == {"api_key": "AIza-cmw", "project_id": "gs-proj", "app_id": "1:123:android:cmw"} and src13e == str(gs), r.stdout + str(app13e))
gs.write_text(json.dumps({"project_info": {"project_id": "gs-proj"}, "client": [{"client_info": {"mobilesdk_app_id": "1:123:android:only", "android_client_info": {"package_name": "it.example.only"}}, "api_key": [{"current_key": "AIza-only"}]}]}))
cfg13e["relay"]["app_package"] = ""
(Path(tmp) / "relay-gs.json").write_text(json.dumps(cfg13e))
T.check("C13e one Android client and no relay.app_package → that client", _cmc.relay_firebase_app(cfg13e) == ({"api_key": "AIza-only", "project_id": "gs-proj", "app_id": "1:123:android:only"}, str(gs)), str(_cmc.relay_firebase_app(cfg13e)))
cfg13e["relay"]["google_services"] = str(Path(tmp) / "no-such-google-services.json")
(Path(tmp) / "relay-gs-missing.json").write_text(json.dumps(cfg13e))
r = T.run_config(["doctor"], home, Path(tmp) / "relay-gs-missing.json", Path(tmp) / "machine-c13d-all.json")
T.check("C13e relay.google_services pointing to a missing file → WARN saying the path does not exist", r.returncode == 0 and "WARN firebase_app_missing" in r.stdout and "no-such-google-services.json" in r.stdout and "non esiste" in r.stdout, r.stdout)
gs.write_text(json.dumps({"project_info": {"project_id": "gs-proj"}, "client": []}))
r = T.run_config(["doctor"], home, Path(tmp) / "relay-gs.json", Path(tmp) / "machine-c13d-all.json")
T.check("C13e a google-services.json without any Android client → WARN (file bad)", r.returncode == 0 and "WARN firebase_app_missing" in r.stdout and "mobilesdk_app_id" in r.stdout, r.stdout)

# C14 shim
r = T.run_config(["init", "--shim", "--yes"], home, target, machine)
shim = home / ".local" / "bin" / "supervisor"
T.check("C14 --shim installs ~/.local/bin/supervisor", shim.exists() and os.access(shim, os.X_OK), r.stdout + r.stderr)
conf_dir = Path(tmp) / "confdir"
(conf_dir / "plugins").mkdir(parents=True)
(conf_dir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
    "supervisor@supervisor-dev": [{"scope": "user", "installPath": str(T.PLUGIN), "version": "0.1.0"}]}}))
env = {"PATH": os.environ["PATH"], "HOME": str(home), "CLAUDE_CONFIG_DIR": str(conf_dir)}
VER = json.loads((T.PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"]
b = subprocess.run([str(shim), "version"], capture_output=True, text=True, env=env)
T.check("C14 shim resolves root via installed_plugins.json", b.returncode == 0 and VER in b.stdout, b.stdout + b.stderr)
env2 = {"PATH": os.environ["PATH"], "HOME": str(home), "CC_SUPERVISOR_ROOT": str(T.PLUGIN)}
b = subprocess.run([str(shim), "version"], capture_output=True, text=True, env=env2)
T.check("C14 shim honours CC_SUPERVISOR_ROOT", b.returncode == 0 and VER in b.stdout, b.stdout + b.stderr)
env3 = {"PATH": os.environ["PATH"], "HOME": str(home), "CLAUDE_CONFIG_DIR": str(Path(tmp) / "empty")}
b = subprocess.run([str(shim), "version"], capture_output=True, text=True, env=env3)
T.check("C14 shim fails loudly when unresolved", b.returncode != 0 and "supervisor" in b.stderr, b.stdout + b.stderr)
b = subprocess.run([str(T.SCRIPTS / "supervisor"), "boh"], capture_output=True, text=True, env=env2)
T.check("C14 dispatcher: unknown subcommand → exit 2 with usage", b.returncode == 2 and "uso" in b.stderr.lower() or "usage" in b.stderr.lower(), b.stdout + b.stderr)

# C15 init --tmux
r = T.run_config(["init", "--tmux"], home, target, machine)
T.check("C15 --tmux prints tile/merge binds with the shim", r.returncode == 0 and "bind a run-shell -b \"$HOME/.local/bin/supervisor tile" in r.stdout and "bind u run-shell -b \"$HOME/.local/bin/supervisor merge" in r.stdout, r.stdout + r.stderr)
T.check("C15 --tmux prints the four arrows guarded by window_panes", r.stdout.count("if -F '#{==:#{window_panes},1}'") == 4 and "move left" in r.stdout and "select-pane -D" in r.stdout, r.stdout)
T.check("C15 --tmux prints, does not write", "supervisor" not in ((home / ".tmux.conf").read_text() if (home / ".tmux.conf").is_file() else ""), "")
noarrows = json.loads(target.read_text())
noarrows["tmux"]["keybindings"] = {"tile": "x", "merge": "", "move_arrows": False}
(Path(tmp) / "noarrows.json").write_text(json.dumps(noarrows))
r = T.run_config(["init", "--tmux"], home, Path(tmp) / "noarrows.json", machine)
T.check("C15 keybindings honoured (tile x, no merge, no arrows)", "bind x run-shell" in r.stdout and "merge" not in r.stdout and "select-pane" not in r.stdout, r.stdout)
r = T.run_config(["init", "--tmux", "--yes"], home, target, machine)
tc = (home / ".tmux.conf").read_text() if (home / ".tmux.conf").is_file() else ""
T.check("C15 --yes appends the block after the legacy one", r.returncode == 0 and tc.count("supervisor init --tmux") == 1 and tc.index("affianca") < tc.index("supervisor init --tmux"), r.stdout + r.stderr)
r = T.run_config(["init", "--tmux", "--yes"], home, target, machine)
T.check("C15 second --yes leaves one block", (home / ".tmux.conf").read_text().count("supervisor init --tmux") == 1 and r.returncode == 0, r.stdout)

T.rm(tmp)
T.rm(tmp2)
T.finish()
