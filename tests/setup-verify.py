#!/usr/bin/env python3
"""`supervisor setup` su una home pulita (11/11/2026, supervisor-installazione-semplice-11-11: un collega aveva installato coi
comandi del README e la web app mostrava la master chiusa mentre era aperta).

Una HOME vuota (CM_HOME), il claude finto, un tmux privato, un crontab finto; nessun relay vero, nessuna finestra, nessuna
rete. Il demone che setup accende gira sulla cartella del relay della HOME finta ed e' fermato alla fine.

SU1 --dry-run non scrive niente: nessuna configurazione, nessun crontab, nessuna riga nel .bashrc
SU2 setup --yes --root ws: configurazione scritta con quella cartella; shim in ~/.local/bin; una riga nel .bashrc;
    crossSessionInbound «accept» nei settings dell'account (con la copia); relay e web app accesi in locale, nessun
    Firebase; nel crontab il registro e il relay (ensure e battito); demone vivo
SU3 la master e' aperta sulla cartella dei progetti, e lo stato che la web app legge la mostra viva (non «gone»)
SU4 il riepilogo dice l'indirizzo della web app e come aggiungere il telefono; senza --phone il telefono non e' chiesto
SU5 ripetibile: un secondo setup tiene la configurazione, non aggiunge righe al .bashrc ne' al crontab, non apre una
    seconda master; esce 0
SU6 una cartella che non esiste e' rifiutata prima di toccare qualcosa
SU7 nessuna chiave doppia nei messaggi (11/11: le chiavi di setup avevano sovrascritto quattro testi di relay setup)
SU8 prima dei passi: senza l'accesso a Claude Code lo dice subito, col rimedio, e non apre una master ferma sulla schermata di
    accesso; --phone yes senza la CLI di Firebase dice la riga esatta; a errore il blocco da mandare a chi aiuta, senza il
    token della web app ne' la cartella della home
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "progetti"
for d in (home / ".claude", ws / "alfa", tmp / "bin", tmp / "run"):
    d.mkdir(parents=True, exist_ok=True)
(home / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark"}))
crontab_file = tmp / "crontab.txt"
fake_cron = tmp / "bin" / "fake-crontab"
fake_cron.write_text(f'#!/bin/sh\nif [ "$1" = "-l" ]; then cat {crontab_file} 2>/dev/null; exit 0; fi\ncat > {crontab_file}\n')
fake_cron.chmod(0o755)
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
claude = tmp / "bin" / "claude"   # quello che init, doctor e prima-raccolta trovano nel PATH: mai il claude vero
claude.write_text('#!/bin/sh\ncase "$1" in --version) echo "2.1.320 (Claude Code)"; exit 0 ;; plugin) echo "[]"; exit 0 ;;\n'
                  f'  auth) if [ -f {tmp}/logged ]; then echo \'{{"loggedIn": true}}\'; else echo \'{{"loggedIn": false}}\'; fi; exit 0 ;; esac\n'
                  f'exec {FAKE} "$@"\n')
(tmp / "logged").write_text("")
claude.chmod(0o755)
ENV = {"PATH": f"{tmp / 'bin'}:/usr/local/bin:/usr/bin:/bin", "HOME": str(home), "CM_HOME": str(home), "SHELL": "/bin/bash",
       "LANG": "it_IT.UTF-8", "CM_CRONTAB_CMD": str(fake_cron), "CM_CLAUDE_BIN": str(FAKE), "FAKE_CLAUDE_SCENARIO": "plain",
       "CM_TMUX_ARGS": "-L setup-verify", "TMUX_TMPDIR": str(tmp / "run"), "XDG_RUNTIME_DIR": str(tmp / "run"),
       "CM_RELAY_OPEN": "true", "TZ": "Europe/Rome", "CM_SETUP_CRON_RUNNING": "1", "CM_FIREBASE_CMD": str(tmp / "bin" / "no-firebase")}
CLI = str(T.SCRIPTS / "supervisor")
conf = home / ".config" / "cc-supervisor" / "config.json"
bashrc = home / ".bashrc"


def setup(*a, timeout=600):
    return subprocess.run([CLI, "setup", *a], capture_output=True, text=True, env=ENV, timeout=timeout, stdin=subprocess.DEVNULL)


def cron():
    return crontab_file.read_text() if crontab_file.is_file() else ""


def tmux(*a):
    return subprocess.run(["tmux", "-L", "setup-verify", *a], capture_output=True, text=True, env=ENV, timeout=20)


def relay_dir():
    try:
        return Path(str(json.loads(conf.read_text()).get("relay", {}).get("dir") or "~/.cc-supervisor/relay").replace("~", str(home), 1))
    except (OSError, ValueError):
        return home / ".cc-supervisor" / "relay"


def stop_daemon():
    try:
        os.kill(int((relay_dir() / "serve.pid").read_text()), 15)
    except (OSError, ValueError):
        pass


import collections  # noqa: E402
for lang in ("it", "en"):   # SU7: le chiavi di setup accanto a quelle di relay setup (setup.*): mai una sopra l'altra
    pairs = json.loads((T.SCRIPTS.parent / "messages" / f"{lang}.json").read_text(), object_pairs_hook=lambda p: p)
    dup = [k for k, n in collections.Counter(k for k, _ in pairs).items() if n > 1]
    T.check(f"SU7 messages/{lang}.json has no key twice (a second one would silently replace the first)", not dup, str(dup))

try:
    # SU6
    r6 = setup("--yes", "--root", str(home / "non-c-e"))
    T.check("SU6 a folder that does not exist is refused (exit 2) before anything is written",
            r6.returncode == 2 and "non esiste" in r6.stderr and not conf.exists() and not cron(), r6.stdout + r6.stderr)

    # SU1
    r1 = setup("--yes", "--dry-run", "--root", str(ws))
    T.check("SU1 --dry-run writes nothing: no config, no crontab, no .bashrc line; it says what it would do",
            r1.returncode == 0 and not conf.exists() and not cron() and not bashrc.exists() and "scriverei" in r1.stdout
            and "aprirei la master" in r1.stdout, r1.stdout + r1.stderr)

    # SU2
    r2 = setup("--yes", "--root", str(ws), "--no-window")
    out2 = r2.stdout + r2.stderr
    cfg = json.loads(conf.read_text()) if conf.is_file() else {}
    st = json.loads((home / ".claude" / "settings.json").read_text())
    pid = (relay_dir() / "serve.pid").read_text().strip() if (relay_dir() / "serve.pid").is_file() else ""
    T.check("SU2 the config is written with the projects folder; the shim is in ~/.local/bin",
            conf.is_file() and Path(str(cfg.get("workspace", {}).get("root", "")).replace("~", str(home), 1)) == ws
            and (home / ".local" / "bin" / "supervisor").is_file(), out2[-1500:])
    T.check("SU2 one block in .bashrc: ~/.local/bin on the PATH and the shell integration",
            bashrc.is_file() and bashrc.read_text().count("# supervisor (supervisor setup)") == 1 and ".local/bin" in bashrc.read_text()
            and "cc-supervisor/shell.sh" in bashrc.read_text(), bashrc.read_text() if bashrc.is_file() else "-")
    T.check("SU2 crossSessionInbound accept in the account's settings, the other keys kept, a copy .bak-supervisor",
            st.get("crossSessionInbound") == "accept" and st.get("theme") == "dark" and (home / ".claude" / "settings.json.bak-supervisor").is_file(), str(st))
    T.check("SU2 relay and web app on in local mode (no Firebase); the crontab has the registry and the relay (ensure, beat); the daemon is alive",
            cfg.get("relay", {}).get("enabled") is True and cfg["relay"].get("web", {}).get("enabled") is True
            and not str(cfg["relay"].get("firebase_url") or "").strip() and "supervisor registry" in cron() and "relay ensure" in cron()
            and "relay battito" in cron() and pid.isdigit() and Path(f"/proc/{pid}").exists(), f"{cfg.get('relay')} | {cron()} | pid {pid}")

    # SU3
    alive = tmux("has-session", "-t", "=master").returncode == 0
    local = relay_dir() / "local" / "state.json"
    try:
        ss = {s["name"]: s["state"] for s in json.loads(local.read_text()).get("sessions") or []}
    except (OSError, ValueError, KeyError):
        ss = {}
    T.check("SU3 the master is open on the projects folder, and the state the web app reads shows it alive (not gone)",
            alive and ss.get("master") not in (None, "gone") and "✓ master: master aperta" in out2 and "stato della web app: la vede aperta" in out2,
            f"tmux {alive} | {ss} | {out2[-1500:]}")

    # SU4
    tail = [x.strip() for x in r2.stdout.split("Riepilogo")[-1].strip().splitlines()]
    T.check("SU4 with no error the summary is three lines: the web app's address, what to do tomorrow, how to add the phone; the phone was not asked",
            r2.returncode == 0 and tail[-3].startswith("web app su questo computer: http://127.0.0.1:")
            and tail[-2].startswith("domani:") and tail[-1] == "telefono: supervisor setup --phone yes"
            and all(x.startswith("! ") for x in tail[:-3]) and "shim_not_in_path" not in out2   # avvisi solo prima delle tre righe; il PATH appena messo non e' un avviso
            and "Prima di cominciare:" in out2 and "Claude Code: accesso fatto" in out2 and "Firebase" not in out2, str([("Prima" in out2), ("accesso fatto" in out2), ("Firebase" in out2), ("shim_not_in_path" in out2), r2.returncode]) + "\n".join(tail))

    # SU5
    cron_before, rc_before = cron(), bashrc.read_text()
    r5 = setup("--yes", "--no-window")
    out5 = r5.stdout + r5.stderr
    sessions = tmux("list-sessions", "-F", "#{session_name}").stdout.split()
    T.check("SU5 repeatable: the second run keeps the config and the root, adds nothing to .bashrc or the crontab, opens no second master, exits 0",
            r5.returncode == 0 and cron() == cron_before and bashrc.read_text() == rc_before and sessions.count("master") == 1
            and not any(s.startswith("master-") for s in sessions) and "già presente" in out5 and "master è già aperta" in out5
            and Path(str(json.loads(conf.read_text())["workspace"]["root"]).replace("~", str(home), 1)) == ws,
            f"rc {r5.returncode} | {sessions} | {out5[-1500:]}")
    T.check("SU2 the first run exited 0", r2.returncode == 0, out2[-800:])

    # SU8 (11/11): prima dei passi, l'accesso a Claude Code e la CLI di Firebase per il telefono
    (tmp / "logged").unlink()
    tmux("kill-session", "-t", "=master")
    r8 = setup("--yes", "--no-window", "--phone", "yes")
    out8 = r8.stdout + r8.stderr
    pre8 = out8.split("Prima di cominciare:")[1].split("\n\n")[0] if "Prima di cominciare:" in out8 else ""
    T.check("SU8 not signed in to Claude Code: said BEFORE the steps with the fix, the master is not opened (no session stuck on the sign-in screen), exit 1",
            r8.returncode == 1 and "claude auth login" in pre8 and "✗ master: non aperta" in out8 and tmux("has-session", "-t", "=master").returncode != 0, out8[-1500:])
    T.check("SU8 --phone yes without the Firebase CLI: said before the steps with the exact line (no Node), the phone step not run",
            "curl -sL https://firebase.tools | bash" in pre8 and "✗ telefono: non fatto" in out8 and "relay setup" not in out8, pre8)
    T.check("SU8 on failure: the block to send to whoever helps, with versions and the failed steps, without the web app's token or the home path",
            "--- da mandare a chi ti aiuta ---" in out8 and "--- fine ---" in out8 and "fail master" in out8
            and "?t=" not in out8.split("--- da mandare a chi ti aiuta ---")[1] and str(home) not in out8.split("--- da mandare a chi ti aiuta ---")[1], out8[-900:])
finally:
    stop_daemon()
    tmux("kill-server")
T.rm(tmp)
T.finish()
