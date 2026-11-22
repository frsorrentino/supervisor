#!/usr/bin/env python3
"""`supervisor <comando> --help` stampa l'aiuto e non esegue mai il comando (16/11/2026, supervisor-help-esegue-16-11).

Dalla segnalazione di un collega: `supervisor restore --help` eseguiva il ripristino (non ha rilanciato niente solo
perche' mancava un terminale per la conferma), `launch -h` prendeva «-h» per una cartella, `relay --help` stampava
l'uso come errore. Home temporanea, tmux privato, claude finto, un registro con una sessione da ripristinare.

HE1  restore --help (e -h): l'aiuto di restore, uscita 0, nessuna sessione rilanciata, claude mai chiamato
HE2  launch -h e --help: l'aiuto di launch, uscita 0, non «serve un percorso assoluto, ricevuto: -h»
HE3  relay --help, wait --help, inbox --help: l'aiuto, uscita 0, niente su stderr
HE4  OGNI sottocomando del dispatcher con --help: uscita 0, un testo su stdout, nessun Traceback, nessuna sessione
     tmux nata, claude mai chiamato; con gli alias italiani lo stesso aiuto
HE5  help, --help, version restano com'erano; un sottocomando che non esiste con --help resta un errore (uscita 2);
     --help non in prima posizione e' l'aiuto anche lui (dal 22/11, supervisor-help-ignorato-22-11: prima arrivava al
     comando, e `task list --help` stampava l'elenco dei compiti)
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude/sessions", "ws/alfa", "bin"):
    (home / d).mkdir(parents=True)
argslog = tmp / "args.log"
fake = home / "bin" / "claude"
fake.write_text(f'#!/bin/sh\necho "$@" >> "{argslog}"\nexit 0\n')
fake.chmod(0o755)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "terminal": {"backend": "none"},
    "registry": {"file": str(tmp / "registry.json"), "good_file": str(tmp / "registry-good.json")},
    "tabs": {"color_registry": str(tmp / "colors")},
    "relay": {"enabled": False, "dir": str(tmp / "relay")},
}))
(tmp / "registry.json").write_text(json.dumps({"salvato": "x", "sessioni": [{"nome": "alfa", "cartella": str(home / "ws" / "alfa"), "account": "personale"}]}))
CLI = T.SCRIPTS / "supervisor"


def cli(*args):
    env = {"PATH": f"{home}/bin:{os.environ['PATH']}", "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
           "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(fake), "CM_PROC_SCAN_PIDS": "", "CM_RELAY_NO_PUSH": "1",
           "CM_MONITORS": str(tmp / "monitors"), "CM_TASKS_DB": str(tmp / "t.db"), "LC_ALL": "C"}
    try:
        return subprocess.run([str(CLI), *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=40)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, -9, "", "TIMEOUT: the command ran instead of printing its help")


def sessions():
    return sorted(x for x in tm("list-sessions", "-F", "#{session_name}").stdout.split() if x != "seed")


def claude_calls():
    return argslog.read_text().splitlines() if argslog.exists() else []


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "seed", "bash", "--norc"], check=True,
                   env={"PATH": os.environ["PATH"], "HOME": str(home)})

    # HE1
    for flag in ("--help", "-h"):
        r = cli("restore", flag)
        T.check(f"HE1 restore {flag}: restore's help on stdout, exit 0, no session relaunched and claude never called",
                r.returncode == 0 and "supervisor restore" in r.stdout and "--dry-run" in r.stdout and sessions() == [] and claude_calls() == []
                and not (tmp / "state" / "restore").exists(), f"{r.returncode} {r.stdout[:200]} {r.stderr[:300]} {sessions()} {claude_calls()}")

    # HE2
    for flag in ("-h", "--help"):
        r = cli("launch", flag)
        T.check(f"HE2 launch {flag}: launch's help, exit 0, not «serve un percorso assoluto, ricevuto: -h»",
                r.returncode == 0 and "--account" in r.stdout and "--no-window" in r.stdout and "ricevuto" not in r.stdout + r.stderr and not r.stderr.strip() and sessions() == [],
                f"{r.returncode} {r.stdout[:200]} {r.stderr[:300]}")

    # HE3
    for cmd, word in (("relay", "relay"), ("wait", "wait"), ("inbox", "inbox")):
        r = cli(cmd, "--help")
        T.check(f"HE3 {cmd} --help: its help on stdout, exit 0, nothing on stderr",
                r.returncode == 0 and word in r.stdout.lower() and len(r.stdout.strip().splitlines()) >= 2 and not r.stderr.strip(), f"{r.returncode} {r.stdout[:200]} {r.stderr[:300]}")

    # HE4: ogni sottocomando che il dispatcher conosce
    text = CLI.read_text(encoding="utf-8")
    body = text[text.index('case "$cmd" in\n  init)'):]
    rows = re.findall(r"^  ([a-z0-9-]+(?:\|[a-z0-9-]+)*)\)", body, re.M)
    names = [r.split("|") for r in rows if not r.startswith(("help", "version", "root"))]
    bad, n = [], 0
    for group in names:
        outs = []
        for name in group:
            r = cli(name, "--help")
            n += 1
            outs.append(r.stdout)
            if r.returncode != 0 or len(r.stdout.strip()) < 20 or "Traceback" in r.stderr or sessions() or claude_calls():
                bad.append(f"{name}: rc {r.returncode}, {len(r.stdout)} chars, {r.stderr.strip()[:120]!r}, sessions {sessions()}, claude {claude_calls()[:1]}")
                for s_ in sessions():
                    tm("kill-session", "-t", f"={s_}")
                argslog.unlink(missing_ok=True)
        if len(set(outs)) > 1:
            bad.append(f"{'|'.join(group)}: the aliases print different help")
    T.check(f"HE4 every subcommand of the dispatcher ({len(names)} of them, {n} names with the aliases) with --help: exit 0, a help text, no Traceback, no tmux session born, claude never called",
            len(names) >= 60 and not bad, f"{len(names)} | " + " ;; ".join(bad[:8]))

    # HE5
    r_h, r_hh, r_v = cli("help"), cli("--help"), cli("version")
    r_no = cli("non-esiste", "--help")
    r_pos = cli("sessions", "--json", "--help")
    T.check("HE5 help, --help and version are as they were; an unknown subcommand with --help is still an error (exit 2)",
            r_h.returncode == 0 and r_h.stdout.startswith("uso: supervisor <") and r_hh.stdout == r_h.stdout and re.fullmatch(r"\d+\.\d+\.\d+", r_v.stdout.strip() or "")
            and r_no.returncode == 2 and "non-esiste" in r_no.stderr, f"{r_h.stdout[:60]} | {r_v.stdout} | {r_no.returncode} {r_no.stderr[:120]}")
    T.check("HE5 --help that is not the first argument is the help too (since 22/11): sessions --json --help prints the help, not JSON",
            r_pos.returncode == 0 and "supervisor sessions" in r_pos.stdout and not r_pos.stdout.lstrip().startswith("["), f"{r_pos.returncode} {r_pos.stdout[:160]} {r_pos.stderr[:160]}")

T.rm(str(tmp))
T.finish()
