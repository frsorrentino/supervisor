#!/usr/bin/env python3
"""Verifica il SessionEnd staccato (Claude Code 2.1.287: tutti gli hook di session.end condividono 1,5 s, e con
`claude -p` cm-hook.py SessionEnd risultava «cancelled» in 2 run su circa 8): tmux privato, claude finto.

S1  detach.sh rientra entro 0,5 s anche se il lavoro dura 2 s, e il lavoro staccato riceve lo stdin intero
S2  hooks.json: SessionEnd passa da detach.sh, gli altri eventi no (le loro uscite servono alla sessione)
S3  /exit con il pannello chiuso SUBITO dopo il rientro dell'hook: la sessione esce lo stesso dalla fotografia
    (il nome tmux e' letto prima di staccarsi) e il ledger ha la riga «end»
S4  reason other attraverso detach.sh: la fotografia la tiene
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
for d in (".claude/sessions", "ws/zeta", "ws/eta", "ws/master"):
    (home / d).mkdir(parents=True)
reg, good, cfg, state = tmp / "registry.json", tmp / "good.json", tmp / "config.json", tmp / "state"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state),
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "registry": {"file": str(reg), "good_file": str(good)},
    "terminal": {"backend": "none"},
}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
DETACH = T.SCRIPTS / "detach.sh"


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CM_PROC_SCAN_PIDS": "",
         "TMPDIR": str(tmp)}
    e.update(extra)
    return e


def detached(args, stdin, **extra):
    t0 = time.monotonic()
    r = subprocess.run(["bash", str(DETACH)] + args, input=stdin, capture_output=True, text=True, env=env(**extra), timeout=30)
    return r, time.monotonic() - t0


def names(p):
    return sorted(s["nome"] for s in json.loads(p.read_text())["sessioni"]) if p.is_file() else None


def start(name):
    tm("new-session", "-d", "-s", name, "-x", "100", "-y", "30", "-c", str(home / "ws" / name),
       f"env CLAUDE_CONFIG_DIR='{home}/.claude' FAKE_CLAUDE_SCENARIO=plain '{FAKE}' --dangerously-skip-permissions -n {name}")


def ledger_ends():
    p = state / "ledger.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if '"end"' in l] if p.is_file() else []


with T.PrivateTmux() as tm:
    # S1: un lavoro lento (2 s) staccato; il wrapper torna subito, il lavoro arriva in fondo con lo stdin
    slow = tmp / "slow.py"
    out = tmp / "slow.out"
    slow.write_text("import sys, time\ndata = sys.stdin.read()\ntime.sleep(2)\nopen(sys.argv[1], 'w').write(data)\n")
    payload = json.dumps({"session_id": "s1", "reason": "other", "pad": "x" * 100000})
    r, t = detached([str(slow), str(out)], payload)
    T.check("S1 detach.sh returns within 0.5 s while the work takes 2 s", r.returncode == 0 and t < 0.5, f"rc={r.returncode} t={t:.2f}s {r.stderr[:200]}")
    T.wait_until(lambda: out.is_file() and out.stat().st_size >= len(payload), 15)
    T.check("S1 the detached work gets the whole stdin", out.is_file() and out.read_text() == payload, str(out.stat().st_size if out.is_file() else None))
    T.wait_until(lambda: not list(tmp.glob("cm-detach.*")), 5)   # rm segue l'uscita dello script
    T.check("S1 the temp copy of stdin is removed", not list(tmp.glob("cm-detach.*")), str(list(tmp.glob("cm-detach.*"))))

    # S2: hooks.json
    hooks = json.loads((T.PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    cmds = {ev: [h["command"] for g in groups for h in g["hooks"]] for ev, groups in hooks.items()}
    T.check("S2 SessionEnd runs cm-hook.py through detach.sh",
            any("detach.sh" in c and "cm-hook.py" in c for c in cmds["SessionEnd"]), str(cmds["SessionEnd"]))
    T.check("S2 no other event is detached (SessionStart, Stop, UserPromptSubmit print what the session reads)",
            not any("detach.sh" in c for ev, cs in cmds.items() if ev != "SessionEnd" for c in cs), json.dumps(cmds)[:300])

    # S3: /exit, pannello chiuso appena l'hook rientra
    start("zeta"); start("eta"); time.sleep(2)
    subprocess.run([str(T.SCRIPTS / "cm-registry.sh")], capture_output=True, text=True, env=env(), timeout=60)
    T.check("S3 zeta and eta in the snapshot", names(good) == ["eta", "zeta"], str(names(good)))
    hook = [str(T.SCRIPTS / "cm-hook.py"), "SessionEnd"]
    pane = tm("display-message", "-p", "-t", "zeta", "#{pane_id}").stdout.strip()
    r, t = detached(hook, json.dumps({"session_id": "z", "cwd": str(home / "ws" / "zeta"), "reason": "prompt_input_exit"}), TMUX_PANE=pane)
    tm("kill-session", "-t", "=zeta")   # prima che il figlio staccato chieda a tmux il nome
    T.check("S3 the SessionEnd hook returns within 0.5 s", r.returncode == 0 and t < 0.5, f"rc={r.returncode} t={t:.2f}s")
    T.wait_until(lambda: names(good) == ["eta"], 15)
    T.check("S3 /exit removes zeta from the snapshot although its pane closed at once", names(good) == ["eta"], str(names(good)))
    T.wait_until(lambda: any(e.get("session_id") == "z" or "z" in json.dumps(e) for e in ledger_ends()), 10)
    T.check("S3 the ledger has the «end» line", any('"z"' in json.dumps(e) for e in ledger_ends()), str(ledger_ends())[:300])

    # S4: reason other (finestra chiusa, crash) tiene la sessione
    pane = tm("display-message", "-p", "-t", "eta", "#{pane_id}").stdout.strip()
    r, t = detached(hook, json.dumps({"session_id": "e", "cwd": str(home / "ws" / "eta"), "reason": "other"}), TMUX_PANE=pane)
    tm("kill-session", "-t", "=eta")
    T.wait_until(lambda: any('"e"' in json.dumps(x) for x in ledger_ends()), 10)
    time.sleep(1.5)
    subprocess.run([str(T.SCRIPTS / "cm-registry.sh")], capture_output=True, text=True, env=env(), timeout=60)
    T.check("S4 reason other through detach.sh keeps eta in the snapshot", names(good) == ["eta"], str(names(good)))
T.rm(tmp)
T.finish()
