#!/usr/bin/env python3
"""Una chiusura in corso ferma il /clear automatico; close aspetta la sessione ferma (24/11/2026, supervisor-close-autoclear-24-11).

Alle 18:55 `supervisor close pix-gerardosacco-com`, una sessione attaccata e ferma da 4 ore, ha stampato «passaggio di
consegne scritto (…), chiudo» e subito dopo «è aperta e non è ferma (busy): non la chiudo». Nel riquadro c'era «❯ /clear»:
l'autoclear di cm-handoff.py, dopo il turno che ha scritto il passaggio di consegne, le ha mandato /clear, perche'
clear_blocked non guardava closing/<tmux>.json di cm-closesave; e cm-close.sh leggeva lo stato appena finito salva().
Un /clear in quel momento apre anche una conversazione vuota: la ripresa deve restare quella col passaggio di consegne.

CC1 con closing/<tmux>.json (scritto da cm-closesave, come in `supervisor close`) il turno che scrive il passaggio di
    consegne non stacca /clear: on_stop dice «closing», il registro dell'autoclear lo dice
CC2 senza la chiusura (o con una piu' vecchia di un'ora) lo stesso turno stacca /clear come prima
CC3 l'esecutore staccato prima che la chiusura cominciasse: all'ultimo controllo la trova e non digita /clear
CC4 le sessioni di win (autoclear_remote): ferma, con un passaggio di consegne, ma in chiusura: niente /clear
CC5 cm-closesave settle: aspetta una sessione busy finche' si ferma (idle), al piu' --wait; una sempre busy resta busy alla
    scadenza; una domanda aperta (waiting) non si aspetta
CC6 cm-close.sh dopo il salvataggio legge lo stato con settle (non subito dopo salva); con --subito come prima
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
state = tmp / "state"
(state / "needs-you").mkdir(parents=True)
(tmp / "home").mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "accounts": {"personal": {"config_dir": str(tmp / "home" / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"},
                           "relay": {"dir": str(tmp / "relay")}, "restart": {"flag_file": str(state / "restart.json")}}))
SPAWN = tmp / "spawn.log"
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "CM_AUTOCLEAR_SPAWN": str(SPAWN), "CM_FEATURES_FILE": str(tmp / "features.json"),
                   "HOME": str(tmp / "home"), "CM_HOME": str(tmp / "home"), "CM_TASKS_DB": str(tmp / "t.db")})
for k in ("CM_RESTART_FLAG_FILE", "TMUX", "TMUX_PANE", "CLAUDECODE"):
    os.environ.pop(k, None)


def load(name):
    sp = importlib.util.spec_from_file_location(name.replace("-", "_") + "_cc", T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


HO = load("cm-handoff")
CS = load("cm-closesave")
NOW = time.time()
NAME = "pix-gerardosacco-com"


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def handoff_turn(tag):
    """Un passaggio di consegne appena scritto e la trascrizione del turno che l'ha scritto."""
    f = tmp / "handoffs" / f"{tag}.md"
    f.parent.mkdir(exist_ok=True)
    f.write_text("# Passaggio di consegne\n\nRipresa: continua.\n")
    t = time.time() - 30
    rows = [{"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": "prima di chiudere, il passaggio di consegne"}},
            {"type": "assistant", "timestamp": iso(t + 1), "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t0", "name": "Bash", "input": {"command": f"supervisor handoff --written {f}"}}]}},
            {"type": "user", "timestamp": iso(t + 2), "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t0", "content": "ok"}]}},
            {"type": "assistant", "timestamp": iso(t + 9), "message": {"role": "assistant", "content": [{"type": "text", "text": "Scritto, chiudo pure."}]}}]
    p = tmp / f"{tag}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(p), str(f)


def spawned():
    return [json.loads(x) for x in SPAWN.read_text().splitlines()] if SPAWN.exists() else []


def alog():
    p = state / "handoffs" / "autoclear.log"
    return p.read_text() if p.exists() else ""


# ------------------------------------------------------------------ CC1
CS.set_closing(NAME, "save", time.time(), time.time() + 180)
tr1, f1 = handoff_turn("cc1")
ok1, why1 = HO.on_stop(NAME, tr1)
T.check("CC1 with closing/<tmux>.json (written by cm-closesave as `supervisor close` does) the handoff turn starts no /clear: on_stop says «closing», the autoclear log says so",
        CS.closing_path(NAME).exists() and HO.clear_blocked(NAME, "") == "closing" and (ok1, why1) == (False, "closing")
        and not spawned() and f"{NAME} {f1}: no /clear (closing)" in alog(), f"{ok1} {why1} {spawned()} {alog()[-300:]}")

# ------------------------------------------------------------------ CC2
CS.clear_closing(NAME)
tr2, f2 = handoff_turn("cc2")
ok2, why2 = HO.on_stop(NAME, tr2)
CS.set_closing("vecchia", "save", time.time() - 7200, None)
os.utime(CS.closing_path("vecchia"), (time.time() - 7200, time.time() - 7200))
tr2b, f2b = handoff_turn("cc2b")
ok2b, _ = HO.on_stop("vecchia", tr2b)
T.check("CC2 without a closing (or with one older than an hour) the same turn starts /clear as before",
        ok2 is True and why2 == f2 and ok2b is True and [NAME, f2] in spawned() and ["vecchia", f2b] in spawned(), f"{ok2} {why2} {ok2b} {spawned()}")

# ------------------------------------------------------------------ CC3
real_load, real_run = HO._load, subprocess.run
sent = []
fake_tune = types.SimpleNamespace(tmux=lambda *a: types.SimpleNamespace(returncode=0), prompt_ready=lambda n: (True, "ok"), REMOTE={})
HO._load = lambda n: fake_tune if n == "cm-tune" else real_load(n)
subprocess.run = lambda cmd, *a, **k: sent.append(cmd) or types.SimpleNamespace(returncode=0, stdout="", stderr="")
try:
    rc3a = HO.clear_when_idle(NAME, f2, wait_s=5)   # nessuna chiusura: digita /clear
    sent_a = list(sent)
    sent.clear()
    CS.set_closing(NAME, "save", time.time(), time.time() + 180)   # la chiusura comincia dopo lo stacco
    rc3 = HO.clear_when_idle(NAME, f2, wait_s=5)
finally:
    HO._load, subprocess.run = real_load, real_run
T.check("CC3 the executor detached before the closing began: at the last check it finds it and types no /clear",
        rc3a == 0 and any("/clear" in c for c in sent_a) and rc3 == 1 and not sent and "blocked at the last check (closing)" in alog(),
        f"{rc3a} {sent_a} {rc3} {sent} {alog()[-300:]}")

# ------------------------------------------------------------------ CC4
WIN = "win-alfa"
tr4, f4 = handoff_turn("cc4")
hosts = types.SimpleNamespace(remote_session_rows=lambda: [{"name": WIN, "host": "win", "remote_name": "alfa", "status": "idle", "session_id": "S-W",
                                                            "transcript": tr4, "waiting": False}],
                              sync_transcript=lambda *a: None)
HO._load = lambda n: hosts if n == "cm-hosts" else real_load(n)
try:
    CS.set_closing(WIN, "save", time.time(), time.time() + 180)
    n4 = HO.autoclear_remote()
    CS.clear_closing(WIN)
    n4b = HO.autoclear_remote()
finally:
    HO._load = real_load
T.check("CC4 win sessions (autoclear_remote): idle, with a handoff, but closing: no /clear; once the closing is over, /clear",
        n4 == 0 and n4b == 1 and [WIN, f4] in spawned(), f"{n4} {n4b} {spawned()}")

# ------------------------------------------------------------------ CC5
count = tmp / "count"
fake_cm = tmp / "supervisor"
fake_cm.write_text(f"""#!/bin/sh
[ "$1" = sessions ] || exit 0
n=$(cat "{count}" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "{count}"
mode=$(cat "{tmp / 'mode'}")
st=idle; w=false
case "$mode" in
  busy3) [ $n -le 3 ] && st=busy ;;
  busy) st=busy ;;
  waiting) st=idle; w=true ;;
esac
echo "[{{\\"name\\": \\"{NAME}\\", \\"tmux\\": \\"{NAME}\\", \\"status\\": \\"$st\\", \\"waiting\\": $w}}]"
""")
fake_cm.chmod(0o755)


def settle(mode, wait):
    (tmp / "mode").write_text(mode)
    count.write_text("0")
    t = time.time()
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-closesave.py"), "settle", NAME, "--wait", str(wait)], capture_output=True, text=True,
                       env=dict(os.environ, CM_CLOSESAVE_CM=str(fake_cm)), timeout=60)
    return r.stdout.strip(), time.time() - t, int(count.read_text()), r.stderr[-200:]


s_a = settle("busy3", 20)
s_b = settle("busy", 2)
s_c = settle("waiting", 20)
T.check("CC5 cm-closesave settle: waits for a busy session until it stops (idle), at most --wait; one always busy stays busy at the deadline; an open question (waiting) is not waited for",
        s_a[0] == "idle" and s_a[2] == 4 and s_a[1] < 15 and s_b[0] == "busy" and 1.5 <= s_b[1] < 10 and s_c[0] == "waiting" and s_c[2] == 1,
        f"{s_a} {s_b} {s_c}")

# ------------------------------------------------------------------ CC6
src = (T.SCRIPTS / "cm-close.sh").read_text()
i_salva, i_attach = src.find('detto "$(salva "$BERSAGLIO")"'), src.find('if attaccata "$BERSAGLIO"; then')
block = src[i_attach:src.find('if [ "$stato" != idle ]; then', i_attach)]
T.check("CC6 cm-close.sh after the save reads the state with settle (not right after salva); with --subito as before",
        0 < i_salva < i_attach and 'cm-closesave.py" settle "$BERSAGLIO"' in block and 'if [ "$SALVATA" = si ]; then' in block
        and '[ "$SUBITO" = si ] || [ "$PROVA" = si ] || SALVATA=si' in src[i_salva:i_attach] and 'cm-sessions.py" --json' in block,
        block[:400])
T.rm(str(tmp))
T.finish()
