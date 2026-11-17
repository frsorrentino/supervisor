#!/usr/bin/env python3
"""Verifica l'account scelto con --account nei lanci su un altro host (17/11/2026, supervisor-host-account-forzato-17-11).

Prima cm-launch.sh non passava l'account a cm-rsession.py, e admit() lo ricavava sempre dalla cartella (folder_map):
`launch --host win --account professionale` su una cartella di personali/ partiva la' con .claude.

HA1 launch --host win --account professionale su personali/alfa: fiducia e avvio con .claude-pixel, record e riga
    «account» con professionale, lo stesso avviso del lancio locale (il percorso dice «personale»)
HA2 senza --account resta folder_map: .claude e personale
HA3 un host che ammette solo personale rifiuta --account professionale, e il rifiuto nomina l'account scelto
HA4 --host auto con la regia carica: pick sceglie l'host in base all'account scelto, non a quello della cartella
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
for d in (".claude/sessions", ".claude-pixel/sessions", "ws/personali/alfa", "ws/personali/zeta", "ws/pro"):
    (home / d).mkdir(parents=True, exist_ok=True)
winroot = tmp / "win"
winroot.mkdir()
WIN = {"kind": "windows-native", "transport": {"ssh": "win"}, "confirmed_at": "2026-10-01T23:50:00+02:00",
       "roles": ["compute", "render", "sessions"],
       "trust": {"level": "work", "accounts": ["personale", "professionale"], "secrets": False, "clients": False},
       "sessions": {"root": "W:/cm", "allow": ["personali/*", "pro/*"], "deny": [], "start_timeout_s": 4}}
cfg = tmp / "config.json"
base_cfg = {
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "default_account": "personale",
    "folder_map": [{"path": str(ws / "pro"), "account": "professionale"}, {"path": str(ws / "personali"), "account": "personale"}],
    "session": {"claude_args": ["--dangerously-skip-permissions"], "startup_timeout_s": 25, "death_check_s": 1},
    "terminal": {"backend": "none"},
    "registry": {"file": str(tmp / "registry.json")},
    "sessions": {"max_sessions": 0},
    "scheduler": {"client_paths": [], "session_offload": {"load_per_core": 1.0, "free_ram_gb": 2}},
    "hosts": {"win": WIN, "solo-personale": dict(WIN, trust=dict(WIN["trust"], accounts=["personale"]))},
}
cfg.write_text(json.dumps(base_cfg))
FAKE_AD = T.ROOT / "tests" / "lib" / "fake-rsession-adapter.py"
CRON = tmp / "crontab"
CRON.write_text("")
FAKE_CRON = tmp / "crontab.sh"
FAKE_CRON.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (CRON, CRON, CRON, CRON))
FAKE_CRON.chmod(0o755)


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CM_RSESSION_ADAPTER": str(FAKE_AD), "FAKE_WIN_ROOT": str(winroot),
         "CM_CRONTAB_CMD": str(FAKE_CRON), "CM_LOADAVG": "0.1", "CM_NPROC": "8", "CM_MEMAVAIL_GB": "5", "CM_PROC_SCAN_PIDS": "", "CM_LAUNCH_NO_TTY": "1",
         "GIT_AUTHOR_NAME": "Prova", "GIT_AUTHOR_EMAIL": "prova@example.com", "GIT_COMMITTER_NAME": "Prova",
         "GIT_COMMITTER_EMAIL": "prova@example.com"}
    e.update(extra)
    return e


def launch(*args, **extra):
    return subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), *args], capture_output=True, text=True, env=env(**extra), timeout=120)


def rs(*args, **extra):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-rsession.py"), *args], capture_output=True, text=True, env=env(**extra), timeout=120)


def g(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, env=env(), check=True).stdout


def repo(d):
    g(d, "init", "-q")
    (d / "README.md").write_text(d.name + "\n")
    g(d, "add", "README.md")
    g(d, "commit", "-q", "-m", "inizio")


def calls():
    p = winroot / "calls.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


alfa, zeta = ws / "personali" / "alfa", ws / "personali" / "zeta"
repo(alfa)
repo(zeta)

# HA1
r1 = launch(str(alfa), "--host", "win", "--account", "professionale", "--no-window")
c1 = [c for c in calls() if c["verb"] in ("trust", "start")]
recs = {p.name: json.loads(p.read_text()) for p in (tmp / "state" / "rsessions").glob("*.json")} if (tmp / "state" / "rsessions").is_dir() else {}
T.check("HA1 launch --host win --account professionale on a personali/ folder: trust and start there with .claude-pixel, the record and the «account» line say professionale",
        r1.returncode == 0 and [c["verb"] for c in c1] == ["trust", "start"] and all(c.get("config") == ".claude-pixel" for c in c1)
        and any(v.get("account") == "professionale" for v in recs.values()) and "account:   professionale" in r1.stdout,
        f"rc={r1.returncode} {json.dumps(c1)} {recs} {r1.stdout[-400:]} {r1.stderr[-400:]}")
T.check("HA1 the same warning as a local launch: the path says «personale», you asked «professionale»",
        "«personale»" in r1.stderr and "«professionale»" in r1.stderr and "Procedo come hai chiesto" in r1.stderr, r1.stderr[-400:])

# HA2
n2 = len(calls())
r2 = launch(str(zeta), "--host", "win", "--no-window")
c2 = [c for c in calls()[n2:] if c["verb"] in ("trust", "start")]
T.check("HA2 without --account folder_map still decides: .claude and personale, no warning",
        r2.returncode == 0 and c2 and all(c.get("config") == ".claude" for c in c2) and "account:   personale" in r2.stdout
        and "Procedo come hai chiesto" not in r2.stderr, f"rc={r2.returncode} {json.dumps(c2)} {r2.stdout[-300:]} {r2.stderr[-300:]}")

# HA3
n3 = len(calls())
r3 = launch(str(zeta), "--host", "solo-personale", "--account", "professionale", "--no-window")
T.check("HA3 a host that admits only personale refuses --account professionale (exit 6), the refusal names the chosen account, nothing starts there",
        r3.returncode == 6 and "«professionale»" in r3.stderr and "regola a" in r3.stderr
        and not [c for c in calls()[n3:] if c["verb"] == "start"], f"rc={r3.returncode} {r3.stderr[-400:]}")

# HA4
p_pers = rs("pick", str(zeta), "--account", "personale", CM_LOADAVG="12")
del base_cfg["hosts"]["win"]
cfg.write_text(json.dumps(base_cfg))
p_pro = rs("pick", str(zeta), "--account", "professionale", CM_LOADAVG="12")
p_none = rs("pick", str(zeta), CM_LOADAVG="12")
T.check("HA4 pick with the control machine loaded follows the chosen account: solo-personale for personale, none for professionale; without --account the folder's personale",
        p_pers.stdout.strip() == "solo-personale" and p_pro.stdout.strip() == "" and p_none.stdout.strip() == "solo-personale",
        f"{p_pers.stdout!r} {p_pro.stdout!r} {p_none.stdout!r} {p_pro.stderr[-200:]}")

T.rm(tmp)
T.finish()
