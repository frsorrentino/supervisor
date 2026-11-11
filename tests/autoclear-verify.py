#!/usr/bin/env python3
"""/clear dopo il passaggio di consegne (05/11/2026, supervisor-clear-dopo-handoff-05-11). Tutto in una cartella temporanea.

AC1 l'ultimo turno ha registrato un passaggio di consegne: --written di supervisor e di fable-director (percorso fra
    virgolette, --here); un file di un turno di prima, vuoto o piu' vecchio del turno no
AC2 a fine turno: /clear staccato una volta per file; mai con «Tipo: aspetta», una richiesta needs-you, la sessione in
    attesa, un riavvio armato
AC3 lo Stop hook lo fa partire dopo la coda; un prompt in coda (decision block) no
AC4 l'annuncio dopo /clear: il caso della master del 05/11 (due /clear di fila: la riga arriva tutte e due le volte),
    consumato al primo prompt della sessione che l'ha avuto; senza session_id una volta sola come prima
AC5 l'esecutore: aspetta la casella vuota, digita /clear come un prompt dell'app; un dialogo aperto o mai libero: niente
AC6 le sessioni di win dal giro del sondatore: ferma, con la copia della trascrizione; occupata no
AC7 il comando handoff non chiede piu' alla persona di mandare /clear
(AC4 anche: un puntatore scritto da fable-director non si annuncia qui; features.json per fable-director)
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
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "accounts": {"personal": {"config_dir": str(tmp / "home" / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"}, "relay": {"dir": str(tmp / "relay")},
                           "restart": {"flag_file": str(state / "restart.json")}}))
SPAWN = tmp / "spawn.log"
FEAT = tmp / "features.json"
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "CM_AUTOCLEAR_SPAWN": str(SPAWN), "CM_FEATURES_FILE": str(FEAT), "HOME": str(tmp / "home"), "CM_TASKS_DB": str(tmp / "t.db")})
os.environ.pop("CM_RESTART_FLAG_FILE", None)
(tmp / "home").mkdir()


def load():
    sp = importlib.util.spec_from_file_location("cm_handoff_t", T.SCRIPTS / "cm-handoff.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


HO = load()
NOW = time.time()


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def transcript(name, turns):
    """turns: [(t, prompt, [comandi Bash], testo finale)]."""
    p = tmp / f"{name}.jsonl"
    rows = []
    for t, prompt, cmds, final in turns:
        rows.append({"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": prompt}})
        for i, c in enumerate(cmds):
            rows.append({"type": "assistant", "timestamp": iso(t + 1 + i), "message": {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": c}}]}})
            rows.append({"type": "user", "timestamp": iso(t + 1 + i), "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"}]}})
        rows.append({"type": "assistant", "timestamp": iso(t + 9), "message": {"role": "assistant", "content": [{"type": "text", "text": final}]}})
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(p)


def mkfile(name, text="# handoff\n\nRipresa: continua.\n", age=0):
    p = tmp / "handoffs" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(text)
    os.utime(p, (NOW - age, NOW - age))
    return p


def spawned():
    return [json.loads(x) for x in SPAWN.read_text().splitlines()] if SPAWN.exists() else []


# AC1
f1 = mkfile("sup-1.md")
f2 = mkfile("docs handoff.md")
t_sup = transcript("sup", [(NOW - 50, "scrivi l'handoff", [f"supervisor handoff --prepare", f"supervisor handoff --written {f1}"], "Fatto.\nEsito: handoff scritto")])
t_fd = transcript("fd", [(NOW - 50, "/fable-director:handoff --here", [f'python3 "/x/fable-director/scripts/handoff.py" --written "{f2}"'], "Scritto.")])
t_old = transcript("old", [(NOW - 500, "handoff", [f"supervisor handoff --written {f1}"], "ok"), (NOW - 50, "e adesso?", ["ls"], "niente")])
f_empty = mkfile("empty.md", text="")
t_empty = transcript("empty", [(NOW - 50, "handoff", [f"supervisor handoff --written {f_empty}"], "ok")])
f_stale = mkfile("stale.md", age=3600)
t_stale = transcript("stale", [(NOW - 50, "handoff", [f"supervisor handoff --written {f_stale}"], "ok")])
r1 = {k: HO.last_turn_handoff(v) for k, v in (("sup", t_sup), ("fd", t_fd), ("old", t_old), ("empty", t_empty), ("stale", t_stale))}
T.check("AC1 the last turn wrote a handoff: supervisor's --written and fable-director's (quoted path with a space)",
        r1["sup"] and r1["sup"][0] == str(f1) and r1["fd"] and r1["fd"][0] == str(f2), str(r1))
T.check("AC1 not a handoff of an earlier turn, an empty file, a file older than the turn", r1["old"] is None and r1["empty"] is None and r1["stale"] is None, str(r1))

# AC2
ok, why = HO.on_stop("alfa", t_sup)
T.check("AC2 at the end of the turn: one detached /clear for the session and the file", ok and spawned() == [["alfa", str(f1)]], str((ok, why, spawned())))
T.check("AC2 the same file again (a second Stop): nothing more", HO.on_stop("alfa", t_sup) == (False, "done") and len(spawned()) == 1, "")
t_ask = transcript("ask", [(NOW - 50, "handoff", [f"supervisor handoff --written {f1}"], "Scritto. Quale strada?\nEsito: handoff scritto\nTipo: aspetta")])
(state / "needs-you" / "x.json").write_text(json.dumps({"name": "gamma", "reason": "il codice"}))
(state / "restart-delta.json").write_text("{}")
got2 = [HO.on_stop("beta", t_ask)[1], HO.on_stop("gamma", t_sup)[1], HO.on_stop("delta", t_sup)[1], HO.on_stop("eps", t_sup, waiting=True)[1]]
T.check("AC2 no /clear when the turn asks (Tipo: aspetta), a needs-you is open, a restart is armed, the session is waiting",
        got2 == ["asks", "needs_you", "restart_armed", "waiting"] and len(spawned()) == 1, str(got2))
(state / "needs-you" / "x.json").unlink()
(state / "restart-delta.json").unlink()

# AC3
HOOK_ENV = dict(os.environ, CM_HOOK_TMUX_NAME="hooked", CM_HOOK_SESSION_ID="s-h")
f3 = mkfile("hook.md")
t_hook = transcript("hook", [(NOW - 50, "handoff", [f"supervisor handoff --written {f3}"], "Fatto")])
pay = {"session_id": "s-h", "cwd": str(tmp), "transcript_path": t_hook, "last_assistant_message": "Fatto", "stop_hook_active": False}
r3 = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "Stop"], input=json.dumps(pay), capture_output=True, text=True, env=HOOK_ENV, timeout=60)
T.check("AC3 the Stop hook starts it (no queue): the /clear for «hooked» after its handoff", ["hooked", str(f3)] in spawned(), r3.stderr[-400:] + r3.stdout[-200:])

# AC4 — il caso della master del 05/11: handoff alle 15:11, /clear alle 15:11:30 e alle 15:13:41, primo prompt alle 15:16
cwd = tmp / "ws"
cwd.mkdir()
fh = mkfile("workspaces-1510.md")
ptr = HO.pointer(str(cwd))
ptr.parent.mkdir(parents=True, exist_ok=True)
ptr.write_text(json.dumps({"path": str(fh), "at": int(NOW - 300), "cwd": str(cwd)}))
a1 = HO.announce(str(cwd), "s-1")
a2 = HO.announce(str(cwd), "s-2")
T.check("AC4 two /clear in a row with no prompt between (05/11 15:11 and 15:13): the HANDOFF line both times, the same file",
        a1.startswith("HANDOFF") and a2.startswith("HANDOFF") and str(fh) in a1 and str(fh) in a2 and ptr.exists(), str((a1, a2)))
with open(state / "ledger.jsonl", "a") as f:
    f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + 1)), "event": "prompt", "session_id": "s-2", "cwd": str(cwd)}) + "\n")
time.sleep(1.2)
a3 = HO.announce(str(cwd), "s-3")
T.check("AC4 once the session that got it wrote a prompt, a later /clear has no line and the pointer is consumed", a3 == "" and not ptr.exists(), a3)
ptr.write_text(json.dumps({"path": str(fh), "at": int(NOW - 300), "cwd": str(cwd)}))
T.check("AC4 without session_id: once, as before", HO.announce(str(cwd)).startswith("HANDOFF") and HO.announce(str(cwd)) == "", "")
ptr.write_text(json.dumps({"path": str(fh), "at": int(NOW - 60), "by": "fable-director"}))
T.check("AC4 a pointer written by fable-director's --written: not announced here (the line is fable-director's), the pointer stays",
        HO.announce(str(cwd), "s-9") == "" and ptr.exists(), "")
_ft = json.loads(FEAT.read_text())
T.check("AC4 features.json for fable-director: autoclear_after_handoff true, updated_at now", _ft["autoclear_after_handoff"] is True and abs(_ft["updated_at"] - time.time()) < 60, str(_ft))

# AC5 l'esecutore, con una casella finta
calls = []
real_run = subprocess.run


def fake_run(cmd, *a, **kw):
    calls.append(list(cmd))
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def tune_with(seq):
    it = iter(seq)
    return types.SimpleNamespace(prompt_ready=lambda n: next(it, seq[-1]), tmux=lambda *a: types.SimpleNamespace(returncode=0), REMOTE={}, GLYPHS=("❯",))


HO._load = lambda n: tune_with([(False, "busy"), (False, "busy"), (True, "")])
HO.time.sleep = lambda s: None
subprocess.run = fake_run
rc5 = HO.clear_when_idle("alfa", str(f1), wait_s=30)
T.check("AC5 waits for the empty prompt, then types /clear in the pane as an app prompt (talk --via tmux)",
        rc5 == 0 and calls and calls[-1][1:] == ["talk", "alfa", "/clear", "--via", "tmux", "--no-wait"], str(calls))
calls.clear()
HO._load = lambda n: tune_with([(False, "dialog")])
rc5b = HO.clear_when_idle("alfa", str(f1), wait_s=30)
HO._load = lambda n: tune_with([(False, "busy")])
t0 = [0.0]
HO.time.time = lambda: (t0.__setitem__(0, t0[0] + 10) or t0[0])
rc5c = HO.clear_when_idle("alfa", str(f1), wait_s=30)
HO.time.time = time.time
subprocess.run = real_run
T.check("AC5 a dialog open, or never idle within the wait: no /clear", rc5b == 1 and rc5c == 1 and not calls, str(calls))

# AC6 win
HO = load()
f6 = mkfile("win.md")
t_win = transcript("win", [(NOW - 50, "handoff", [f"supervisor handoff --written {f6}"], "Fatto")])
synced = []
fake_hosts = types.SimpleNamespace(
    remote_session_rows=lambda: [{"name": "win:app", "host": "win", "remote_name": "win-app", "session_id": "w1", "status": "idle", "transcript": t_win, "waiting": False},
                                 {"name": "win:busy", "host": "win", "remote_name": "win-busy", "session_id": "w2", "status": "busy", "transcript": t_win, "waiting": False}],
    sync_transcript=lambda h, n, s: synced.append(n))
HO._load = lambda n: fake_hosts
n6 = HO.autoclear_remote()
T.check("AC6 a win session idle whose last turn wrote a handoff: its transcript copied, one /clear; a busy one is not touched",
        n6 == 1 and synced == ["win-app"] and ["win:app", str(f6)] in spawned() and not any(x[0] == "win:busy" for x in spawned()), str((n6, synced, spawned())))

# AC7
md = (T.ROOT / "supervisor" / "commands" / "handoff.md").read_text()
T.check("AC7 the handoff command says supervisor sends /clear by itself, and no longer that the user sends it",
        "supervisor sends `/clear` by itself" in md and "the user sends it (terminal, phone or web app)" not in md, "")
T.finish()
