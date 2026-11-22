#!/usr/bin/env python3
"""Una segnalazione lunga dal telefono arriva intera alla sessione del progetto (22/11/2026, supervisor-segnalazione-lunga-22-11).

Il 19/11 alle 16:11 il relay ha rifiutato una segnalazione: «text too long: at most 4000 characters». Ora il tetto e'
20000, e cm-talk scrive il testo nel pannello a pezzi: tmux rifiuta un comando oltre il suo messaggio interno (~16 KB).

SL1 op report con un testo di 6000 caratteri: ok, e `supervisor report` riceve il testo intero (non «text too long»)
SL2 op report con 20001 caratteri: rifiutata col tetto nuovo (20000), niente consegnato
SL3 la consegna vera in tmux: cm-talk scrive 6000 caratteri, e 20000 con lettere accentate (oltre 16 KB), in un pannello
    che legge in modo raw: arriva tutto, uguale, e l'Invio dopo
"""
import importlib.util
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
alfa = home / "ws" / "personali" / "alfa"
alfa.mkdir(parents=True)
(home / ".claude" / "sessions").mkdir(parents=True)
state, rdir = tmp / "state", tmp / "relay"
state.mkdir()
rdir.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(home / "ws"), "root_session_name": "master", "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(rdir), "duplicate_prompt_s": 0},
    "registry": {"file": str(tmp / "registry.json")}, "tabs": {"color_registry": str(tmp / "colors")},
}))
got = tmp / "report-args.json"
fake_cm = tmp / "supervisor"
fake_cm.write_text(f"""#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
if a[:1] == ["sessions"]:
    print(json.dumps([{{"name": "alfa", "tmux": "alfa", "cwd": {str(alfa)!r}, "status": "idle", "host": "local", "account": "personale"}}]))
elif a[:1] == ["registry"]:
    print('{{"sessioni": []}}')
elif a[:1] == ["report"]:
    open({str(got)!r}, "w").write(json.dumps(a))
    print("consegnata ad alfa")
""")
fake_cm.chmod(0o755)
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE"):
    os.environ.pop(k, None)
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_RELAY_CM": str(fake_cm),
                   "CM_PROC_SCAN_PIDS": "", "CM_RELAY_NO_PUSH": "1", "CLAUDE_OBSERVE_CONFIG": str(tmp / "observe.json")})


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_sl", T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RL = load("cm-relay")
RL.push_async = lambda *a, **k: None
RL.report_origin = lambda cmd: ""
if hasattr(RL, "C") and hasattr(RL.C, "report_origin"):
    RL.C.report_origin = lambda cmd: ""

LONG = ("Segnalazione dal telefono, riga %03d: il bottone non risponde e la pagina si ricarica da sola. " * 1)
text6 = "".join(LONG % i for i in range(70))[:6000]
assert len(text6) == 6000

# SL1
got.unlink(missing_ok=True)
ok1, t1 = RL.execute({"op": "report", "session": "alfa", "text": text6, "by": "phone"})[:2]
args = json.loads(got.read_text()) if got.exists() else []
T.check("SL1 op report with 6000 characters: ok, and `supervisor report` gets the whole text (not «text too long»)",
        ok1 is True and "too long" not in str(t1) and len(args) > 3 and args[3].startswith(text6) and len(args[3]) >= 6000,
        f"{ok1} {str(t1)[:160]!r} | {[len(x) for x in args]}")

# SL2
got.unlink(missing_ok=True)
ok2, t2 = RL.execute({"op": "report", "session": "alfa", "text": "x" * 20001, "by": "phone"})[:2]
T.check("SL2 op report with 20001 characters: refused with the new ceiling (20000), nothing delivered",
        ok2 is False and "20000" in str(t2) and not got.exists(), f"{ok2} {str(t2)[:160]!r} {got.exists()}")

# SL3
out = tmp / "pane.txt"
reader = tmp / "reader.py"
reader.write_text(f"""import os, sys, termios, tty
fd = sys.stdin.fileno()
tty.setraw(fd)
with open({str(out)!r}, "ab", buffering=0) as f:
    while True:
        b = os.read(fd, 65536)
        if not b:
            break
        f.write(b)
""")
with T.PrivateTmux() as tm:
    os.environ["CM_TMUX_ARGS"] = tm.env["CM_TMUX_ARGS"]
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "alfa", "-x", "200", "-y", "50", sys.executable, str(reader)], check=True)
    time.sleep(1)
    TK = load("cm-talk")
    text20 = ("perché è già così — città, più, giù; " * 700)[:20000]
    bad = []
    for label, t in (("6000", text6), ("20000 accented", text20)):
        out.write_bytes(b"")
        err = ""
        try:
            TK.talk_tmux({"tmux": "alfa", "name": "alfa"}, t, False, 5, True, True)
        except SystemExit as e:
            err = f"exit {e.code}"
        deadline = time.time() + 20
        while time.time() < deadline and len(out.read_bytes()) < len(t.encode()):
            time.sleep(0.5)
        time.sleep(1)
        raw = out.read_bytes().decode("utf-8", "replace")
        body = raw.rstrip("\r\n")
        if err or body != t or not raw[len(t):].startswith("\r"):
            bad.append(f"{label}: {err} sent {len(t)} got {len(body)} ({len(raw.encode())} bytes) tail {raw[-20:]!r}")
    T.check("SL3 the real delivery in tmux: 6000 characters, and 20000 accented ones (over 16 KB), arrive whole and equal, then Enter",
            not bad, " | ".join(bad))

T.rm(str(tmp))
T.finish()
