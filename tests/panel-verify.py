#!/usr/bin/env python3
"""Verifica `supervisor panel` (02/10/2026): il pannello di un comando slash letto e chiuso, tmux privato.

PN1 pannello intero (bordo «▔▔▔», testo, «Esc to cancel»): testo fra bordo e piè di pagina, un Esc lo chiude, exit 0
PN2 pannello piu' alto dello schermo (/cost a 32 righe, dal vivo): il piè di pagina non si vede; il testo arriva fino
    all'ultima riga visibile e l'Esc lo chiude lo stesso (prima: nessun pannello riconosciuto, restava aperto)
PN3 nessun pannello (la casella «❯» sotto il bordo di un pannello gia' chiuso, o niente bordo): exit 1, nessun Esc
PN4 sessione inesistente: exit 3
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
BORDER = "▔" * 30


def fake(tm, name, lines, rows=30):
    """Una «sessione» che disegna `lines` e, al primo tasto, pulisce lo schermo e scrive CHIUSO (e il tasto ricevuto)."""
    script = tmp / f"{name}.sh"
    body = "".join(f"printf '%s\\n' {repr(l)}\n" for l in lines)
    script.write_text("clear\n" + body + "IFS= read -rsn1 k; clear; printf 'CHIUSO %q\\n' \"$k\"; sleep 60\n")
    tm("new-session", "-d", "-s", name, "-x", "100", "-y", str(rows), f"bash {script}")
    time.sleep(0.8)


def panel(tm, name, wait="2"):
    env = {"PATH": __import__("os").environ["PATH"], "HOME": str(tmp), "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"],
           "CC_SUPERVISOR_CONFIG": str(tmp / "nessuna.json"), "CM_PROC_SCAN_PIDS": ""}
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "panel", name, "--wait", wait],
                          capture_output=True, text=True, env=env, timeout=60)


def screen(tm, name):
    return tm("capture-pane", "-p", "-t", name).stdout


with T.PrivateTmux() as tm:
    fake(tm, "intero", ["intestazione", BORDER, "   Settings  Status   Usage", "   Total cost:  $0.42", "   Current week  14% used", "   Esc to cancel"])
    r = panel(tm, "intero")
    T.check("PN1 whole panel: text between the border and the footer, closed by one Esc, exit 0",
            r.returncode == 0 and "Total cost:  $0.42" in r.stdout and "14% used" in r.stdout and "Esc to cancel" not in r.stdout
            and "intestazione" not in r.stdout and "CHIUSO" in screen(tm, "intero"), r.stdout + r.stderr + screen(tm, "intero"))
    # come lo schermo vero a 32 righe: il bordo in alto, il fondo del pannello (e il piè di pagina) tagliato
    alto = ["intestazione", BORDER, "   Settings  Status   Usage"] + [f"   riga {i}" for i in range(1, 16)]
    fake(tm, "alto", alto, rows=20)
    r = panel(tm, "alto")
    T.check("PN2 panel taller than the screen (footer out of sight): text up to the last visible line, still closed by Esc",
            r.returncode == 0 and "riga 1\n" in r.stdout + "\n" and "riga 15" in r.stdout and "intestazione" not in r.stdout
            and "CHIUSO" in screen(tm, "alto"), r.stdout + r.stderr + screen(tm, "alto"))
    fake(tm, "chiuso", ["vecchio pannello", BORDER, "   testo di prima", "❯ "])
    fake(tm, "niente", ["nessun pannello qui", "❯ "])
    r1, r2 = panel(tm, "chiuso", "1"), panel(tm, "niente", "1")
    T.check("PN3 no panel (the prompt below an old border, or no border): exit 1, no Esc sent",
            r1.returncode == 1 and r2.returncode == 1 and "CHIUSO" not in screen(tm, "chiuso") and "CHIUSO" not in screen(tm, "niente"),
            f"{r1.returncode} {r2.returncode} " + screen(tm, "chiuso") + screen(tm, "niente"))
    r = panel(tm, "nessuna-sessione", "1")
    T.check("PN4 a session that does not exist: exit 3", r.returncode == 3, f"{r.returncode} {r.stderr}")
T.rm(tmp)
T.finish()
