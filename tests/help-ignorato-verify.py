#!/usr/bin/env python3
"""Con --help in qualunque posizione, o con un'opzione sconosciuta, nessun comando fa il lavoro vero (22/11/2026,
supervisor-help-ignorato-22-11).

Nota HIGH del 29/09: `supervisor relay setup --help` ha ripubblicato le regole del database e riscritto config.json; il
21/11 `task list --help` ha stampato l'elenco dei compiti. Il dispatcher prendeva --help solo subito dopo il comando,
e un'opzione che lo script non conosceva veniva ignorata. Home temporanea, tmux privato, claude e firebase finti.

HI1  relay setup --help (e -h): l'aiuto di relay setup, uscita 0, firebase mai chiamato, config.json identico
HI2  task list --help: l'aiuto di task, uscita 0, il compito «Scrivi hello.txt» non stampato
HI3  task list --opzione-inesistente: uscita 2, la riga in italiano con l'opzione, nessun compito stampato;
     relay setup --boh lo stesso, firebase mai chiamato
HI4  OGNI sottocomando del dispatcher con «prova --help»: uscita 0, l'aiuto, niente sessioni, claude mai chiamato;
     con «--opzione-che-non-esiste»: uscita 2, l'opzione nominata su stderr, niente sessioni, claude mai chiamato
HI5  le opzioni vere passano: sessions --json resta JSON; ogni opzione lunga scritta nell'aiuto di un comando e' accettata
     dal controllo; --<account> per launch; dopo «--» non si controlla niente
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
fblog = tmp / "firebase.log"
fake = home / "bin" / "claude"
fake.write_text(f'#!/bin/sh\necho "$@" >> "{argslog}"\nexit 0\n')
fake.chmod(0o755)
fb = home / "bin" / "firebase"
fb.write_text(f'#!/bin/sh\necho "$@" >> "{fblog}"\necho "[]"\nexit 0\n')
fb.chmod(0o755)
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
CFG0 = cfg.read_bytes()
(tmp / "registry.json").write_text(json.dumps({"salvato": "x", "sessioni": [{"nome": "alfa", "cartella": str(home / "ws" / "alfa"), "account": "personale"}]}))
CLI = T.SCRIPTS / "supervisor"
ARGS = T.SCRIPTS / "cm-args.py"


def env():
    return {"PATH": f"{home}/bin:{os.environ['PATH']}", "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
            "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(fake), "CM_PROC_SCAN_PIDS": "", "CM_RELAY_NO_PUSH": "1",
            "CM_FIREBASE_CMD": str(fb), "CM_MONITORS": str(tmp / "monitors"), "CM_TASKS_DB": str(tmp / "t.db"), "LC_ALL": "C"}


def cli(*args, stdin=None):
    try:
        return subprocess.run([str(CLI), *args], input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                              capture_output=True, text=True, env=env(), timeout=40)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, -9, "", "TIMEOUT: the command ran instead of stopping")


def check_args(cmd, script, *args):
    return subprocess.run([sys.executable, str(ARGS), cmd, str(T.SCRIPTS / script), *args], capture_output=True, text=True, env=env(), timeout=30)


def sessions():
    return sorted(x for x in tm("list-sessions", "-F", "#{session_name}").stdout.split() if x != "seed")


def calls(f):
    return f.read_text().splitlines() if f.exists() else []


def reset():
    for s_ in sessions():
        tm("kill-session", "-t", f"={s_}")
    argslog.unlink(missing_ok=True)
    fblog.unlink(missing_ok=True)


with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "seed", "bash", "--norc"], check=True,
                   env={"PATH": os.environ["PATH"], "HOME": str(home)})

    c = {"schema_version": 1, "task": {"id": "hi-hello", "title": "Scrivi hello.txt", "plan": "p",
                                       "where": {"project": str(home / "ws" / "alfa"), "session": None, "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": str(tmp), "timeout_s": 30}, "perimeter": ["x"], "lane": "open", "depends_on": [],
                                       "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
                                       "outcome": {"expected": "x", "kind": "custom", "proof": "x"}}}
    r_add = cli("task", "add", "-", stdin=json.dumps(c))
    r_ls = cli("task", "list")
    T.check("HI0 the task is there: task list prints «Scrivi hello.txt»", r_add.returncode == 0 and "Scrivi hello.txt" in r_ls.stdout,
            f"{r_add.returncode} {r_add.stderr[:200]} | {r_ls.stdout[:200]}")

    # HI1
    for flag in ("--help", "-h"):
        reset()
        r = cli("relay", "setup", flag)
        T.check(f"HI1 relay setup {flag}: the help of relay setup, exit 0, firebase never called, config.json untouched",
                r.returncode == 0 and "supervisor relay setup" in r.stdout and "--dry-run" in r.stdout and calls(fblog) == []
                and cfg.read_bytes() == CFG0 and not (tmp / "relay").exists(),
                f"{r.returncode} {r.stdout[:200]!r} {r.stderr[:300]!r} firebase {calls(fblog)[:3]} cfg {cfg.read_bytes() == CFG0}")

    # HI2
    r = cli("task", "list", "--help")
    T.check("HI2 task list --help: task's help, exit 0, the task list not printed",
            r.returncode == 0 and "supervisor task" in r.stdout and "Scrivi hello.txt" not in r.stdout, f"{r.returncode} {r.stdout[:300]!r} {r.stderr[:200]!r}")

    # HI3
    r = cli("task", "list", "--opzione-inesistente")
    T.check("HI3 task list --opzione-inesistente: exit 2, the Italian line naming the option, no task printed",
            r.returncode == 2 and "opzione sconosciuta" in r.stderr and "--opzione-inesistente" in r.stderr and "Scrivi hello.txt" not in r.stdout,
            f"{r.returncode} {r.stdout[:200]!r} {r.stderr[:300]!r}")
    reset()
    r = cli("relay", "setup", "--boh")
    T.check("HI3 relay setup --boh: exit 2, firebase never called, config.json untouched",
            r.returncode == 2 and "--boh" in r.stderr and calls(fblog) == [] and cfg.read_bytes() == CFG0, f"{r.returncode} {r.stderr[:300]!r} {calls(fblog)[:3]}")

    # HI4
    text = CLI.read_text(encoding="utf-8")
    body = text[text.index('case "$cmd" in\n  init)'):]
    rows = re.findall(r"^  ([a-z0-9-]+(?:\|[a-z0-9-]+)*)\)(.*)$", body, re.M)
    names = [r_[0].split("|")[0] for r_ in rows if "$S/" in r_[1] and not r_[0].startswith(("help", "version", "root"))]
    bad_h, bad_o = [], []
    for name in names:
        reset()
        r = cli(name, "prova", "--help")
        if r.returncode != 0 or len(r.stdout.strip()) < 20 or "Traceback" in r.stderr or sessions() or calls(argslog) or calls(fblog):
            bad_h.append(f"{name}: rc {r.returncode} {r.stderr.strip()[:100]!r} sessions {sessions()} claude {calls(argslog)[:1]}")
        reset()
        r = cli(name, "prova", "--opzione-che-non-esiste")
        if r.returncode != 2 or "--opzione-che-non-esiste" not in r.stderr or sessions() or calls(argslog) or calls(fblog):
            bad_o.append(f"{name}: rc {r.returncode} {r.stderr.strip()[:100]!r} sessions {sessions()} claude {calls(argslog)[:1]}")
    reset()
    T.check(f"HI4 every subcommand ({len(names)}) with «prova --help»: exit 0, its help, nothing run", len(names) >= 60 and not bad_h,
            f"{len(names)} | " + " ;; ".join(bad_h[:8]))
    T.check(f"HI4 every subcommand ({len(names)}) with «prova --opzione-che-non-esiste»: exit 2 naming it, nothing run", len(names) >= 60 and not bad_o,
            f"{len(names)} | " + " ;; ".join(bad_o[:8]))

    # HI5
    r = cli("sessions", "--json")
    T.check("HI5 sessions --json still prints JSON", r.returncode == 0 and r.stdout.lstrip().startswith("["), f"{r.returncode} {r.stdout[:160]!r} {r.stderr[:200]!r}")
    refused = []
    for name, script in sorted({(r_[0].split("|")[0], m.group(1)) for r_ in rows for m in [re.search(r"\$S/([A-Za-z0-9_.\/-]+)", r_[1])] if m}):
        f = T.SCRIPTS / script
        if not f.is_file():
            continue
        h = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-help.py"), str(f), name], capture_output=True, text=True).stdout
        opts = sorted(set(re.findall(r"(?<![A-Za-z0-9_-])--[a-z][a-z0-9-]*", h)))
        if opts and check_args(name, script, *opts).returncode != 0:   # una chiamata per comando; una per opzione solo se serve
            refused += [f"{name} {o}" for o in opts if check_args(name, script, o).returncode != 0]
    T.check("HI5 every long option written in a command's help is accepted by the check", not refused, " ".join(refused[:20]))
    r1, r2, r3 = check_args("launch", "cm-launch.sh", "/x", "--personale", "--no-window"), check_args("bg", "cm-jobs.py", "--", "ls", "--whatever"), \
        check_args("launch", "cm-launch.sh", "/x", "--aziendale-finto")
    T.check("HI5 --<account> passes for launch, nothing is checked after «--», an unknown one is still refused",
            r1.returncode == 0 and r2.returncode == 0 and r3.returncode == 2, f"{r1.returncode} {r1.stderr[:120]} | {r2.returncode} | {r3.returncode}")

T.rm(str(tmp))
T.finish()
