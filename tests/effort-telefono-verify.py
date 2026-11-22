#!/usr/bin/env python3
"""/effort dal telefono cambia l'effort della sessione, o dice in italiano perche' no (22/11/2026,
supervisor-effort-telefono-22-11).

Il 19/11 alle 20:04 il relay ha risposto «/effort non si è aperto in <progetto>». La causa: il piè del
selettore di /effort e' cambiato. Fino alla 2.1.26x diceva «s for this session only»; la 2.1.330 (letto nel suo binario)
usa lo stesso piè del selettore del modello, «←/→ to adjust · s to use this session only · Enter to save · Esc to
cancel», e cm-tune aspettava la frase vecchia: il selettore era aperto, l'attesa finiva e partiva l'Esc. Ha anche una
variante senza il tasto s, «Enter to use this session only». E su una sessione di win che non gira piu' il telefono
leggeva l'ultima riga di un traceback, «cm_adapters.HostError: remote: not running».

EF1 sessione locale col piè della 2.1.330: supervisor effort alfa low → uscita 0, «(this session only)» nel pannello,
    nessun default salvato; anche in un pannello stretto (34 colonne) dove il piè va a capo
EF2 la variante «Enter to use this session only» (niente tasto s): Invio, uscita 0, nessun default salvato
EF3 il piè vecchio («s for this session only») va ancora
EF4 una sessione di win (console finta col piè della 2.1.330): «s», la conferma «Set effort level to xHigh», uscita 0,
    mai Invio nel selettore
EF5 una sessione di win che non gira piu': una riga in italiano, uscita 1, nessun traceback
"""
import contextlib
import importlib.util
import io
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
(home / ".claude").mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "tune": {"timeout_s": 3, "file": str(tmp / "tuned.json")}}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
DEFAULT_LOG = tmp / "default.log"


def env(tm):
    return {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
            "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"]}


def tune(tm, *args):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tune.py"), *args], capture_output=True, text=True, env=env(tm), timeout=90)


def start(tm, name, scenario, cols=160):
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", name, "-x", str(cols), "-y", "40",
                    "env", f"FAKE_CLAUDE_SCENARIO={scenario}", "FAKE_CLAUDE_REGISTER=0", f"FAKE_CLAUDE_DEFAULT_LOG={DEFAULT_LOG}", str(FAKE)],
                   env=env(tm), check=True)
    time.sleep(2)


def pane(tm, name):
    return subprocess.run(["tmux", "-L", tm.socket, "capture-pane", "-p", "-J", "-t", name], capture_output=True, text=True).stdout


def defaults():
    return DEFAULT_LOG.read_text().splitlines() if DEFAULT_LOG.exists() else []


with T.PrivateTmux() as tm:
    start(tm, "alfa", "effort330")
    r = tune(tm, "effort", "alfa", "low")
    T.check("EF1 Claude Code 2.1.330 footer («s to use this session only»): exit 0, «(this session only)», no default saved",
            r.returncode == 0 and "Set effort level to low (this session only)" in pane(tm, "alfa") and defaults() == [], r.stdout + r.stderr + pane(tm, "alfa"))
    start(tm, "stretta", "effort330", cols=34)
    r = tune(tm, "effort", "stretta", "high")
    T.check("EF1 the same in a 34-column pane where the footer wraps", r.returncode == 0 and defaults() == [], r.stdout + r.stderr + pane(tm, "stretta"))
    start(tm, "invio", "effort330-enter")
    r = tune(tm, "effort", "invio", "medium")
    T.check("EF2 the footer with «Enter to use this session only» and no «s»: Enter, exit 0, no default saved",
            r.returncode == 0 and "Set effort level to medium (this session only)" in pane(tm, "invio") and defaults() == [], r.stdout + r.stderr + pane(tm, "invio"))
    start(tm, "vecchia", "plain")
    r = tune(tm, "effort", "vecchia", "max")
    T.check("EF3 the old footer («s for this session only») still works", r.returncode == 0 and "effort max" in r.stdout and defaults() == [], r.stdout + r.stderr)

    os.environ.update(env(tm))
    spec = importlib.util.spec_from_file_location("cm_tune_ef", T.SCRIPTS / "cm-tune.py")
    TU = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(TU)

    class Console:
        """La console di win col selettore di /effort della 2.1.330."""

        def __init__(self, gone=False):
            self.keys, self.state, self.typed, self.sel, self.gone, self.enter_in_picker = [], "prompt", "", 2, gone, False

        def screen(self):
            lv = ["low", "medium", "high", "xHigh", "max", "ultracode"]
            if self.state == "picker":
                return f"> \n   Effort\n   {'   '.join(lv)}\n   now: {lv[self.sel]}\n   ←/→ to adjust · s to use this session only · Enter to save · Esc to cancel\n"
            if self.state == "done":
                return f"  ⎿  Set effort level to {lv[self.sel]} (this session only): fake description\n> \n"
            return "> \n  ? for shortcuts\n"

        def session_console(self, name, keys=None, timeout=90):
            if self.gone:
                raise TU._load("cm-adapters").HostError("remote", "not running")
            for k in keys or []:
                self.keys.append(k)
                if self.state != "picker":
                    if k.startswith("text:"):
                        self.typed += k[5:]
                    elif k == "Enter" and self.typed == "/effort":
                        self.state, self.typed = "picker", ""
                elif k == "Left":
                    self.sel = max(0, self.sel - 1)
                elif k == "Right":
                    self.sel = min(5, self.sel + 1)
                elif k == "text:s":
                    self.state = "done"
                elif k == "Enter":
                    self.enter_in_picker = True
                elif k == "Esc":
                    self.state = "prompt"
            return {"pid": 4242, "screen": self.screen()}

    c4 = Console()
    TU.REMOTE["win-prova"] = (c4, "prova")
    TU.GLYPHS = ("❯", ">")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc4 = TU._set_effort("win-prova", "xhigh")
    T.check("EF4 a win session with the 2.1.330 footer: «s», the confirmation «Set effort level to xHigh», exit 0, never Enter in the picker",
            rc4 == 0 and c4.state == "done" and "text:s" in c4.keys and not c4.enter_in_picker, f"{rc4} {buf.getvalue()!r} {c4.keys}")

    c5 = Console(gone=True)
    real_load = TU._load

    class _RS:
        @staticmethod
        def resolve(name):
            return ("win", "prova")

    class _HS:
        @staticmethod
        def adapter(host):
            return c5

    TU._load = lambda n: _RS if n == "cm-rsession" else _HS if n == "cm-hosts" else real_load(n)
    buf5, err5 = io.StringIO(), ""
    try:
        with contextlib.redirect_stdout(buf5):
            rc5 = TU.main(["effort", "win-gone", "low"])
    except Exception as e:   # noqa: BLE001
        rc5, err5 = None, f"{type(e).__name__}: {e}"
    T.check("EF5 a win session that is no longer running: one Italian line, exit 1, no traceback",
            rc5 == 1 and not err5 and "non è più in esecuzione" in buf5.getvalue() and "HostError" not in buf5.getvalue(),
            f"{rc5} {err5!r} {buf5.getvalue()!r}")

T.rm(str(tmp))
T.finish()
