#!/usr/bin/env python3
"""Il modello nello stato anche senza turni, e un cambio a sessione occupata (05/11/2026, supervisor-modello-stato-05-11).
Tutto in una cartella temporanea, niente tmux vero.

MS1 il SessionStart: il `model` del payload (Claude Code 2.1.318 lo manda) nel ledger e annotato in tune.file per la
    sessione, con l'etichetta di tune.models; una trascrizione senza turni lo da' nello stato; il primo turno vince
MS2 la statusline (relay.window_hint_dir, <session_id>.json): effort e modello quando mancano, mai sopra la trascrizione
MS3 un cambio a sessione occupata: in attesa (tune-pending), annotato subito come scelto, rc 0 e il testo «a fine turno»;
    un dialogo aperto, un valore sbagliato o l'esecutore stesso (CM_TUNE_APPLYING) non mettono in attesa
MS4 l'esecutore: aspetta la casella vuota, applica modello poi effort coi comandi di sempre, toglie l'attesa; ancora
    occupata (rc 3): resta in attesa
MS5 lo Stop hook lo stacca per la sua sessione, una volta (lock); il /clear dopo un handoff aspetta che il cambio sia fatto
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
state.mkdir()
hint = tmp / "hint"
hint.mkdir()
TUNED = tmp / "tuned.json"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "accounts": {"personal": {"config_dir": str(tmp / "home" / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"},
                           "relay": {"dir": str(tmp / "relay"), "window_hint_dir": str(hint)}, "tune": {"file": str(TUNED)}}))
SPAWN = tmp / "spawn.log"
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(tmp / "home"), "CM_TASKS_DB": str(tmp / "t.db"), "CM_TUNE_SPAWN": str(SPAWN),
                   "CM_AUTOCLEAR_SPAWN": str(tmp / "ac.log"), "CM_FEATURES_FILE": str(tmp / "feat.json"), "CM_IMPORTANCE_FILE": str(tmp / "imp.json")})
(tmp / "home" / ".claude").mkdir(parents=True)


def load(n):
    sp = importlib.util.spec_from_file_location(n.replace("-", "_") + "_t", T.SCRIPTS / f"{n}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def tr(name, rows):
    p = tmp / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(p)


CORE = load("cm-core")
NOW = time.time()

# MS1
pay = {"session_id": "s-start", "cwd": str(tmp), "source": "clear", "model": "claude-fable-5-1", "hook_event_name": "SessionStart"}
r1 = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "SessionStart"], input=json.dumps(pay), capture_output=True, text=True, timeout=60,
                    env=dict(os.environ, CM_HOOK_TMUX_NAME="alfa"))
led = [json.loads(x) for x in (tmp / "home" / ".claude" / "ledger.jsonl").read_text().splitlines()] if (tmp / "home" / ".claude" / "ledger.jsonl").exists() else \
      [json.loads(x) for x in (state / "ledger.jsonl").read_text().splitlines()]
tuned = json.loads(TUNED.read_text())
T.check("MS1 SessionStart: the payload's model in the ledger and noted in tune.file for the session, with its label",
        any(x.get("event") == "start" and x.get("model") == "claude-fable-5-1" for x in led)
        and tuned["s-start"]["model"] == {"id": "claude-fable-5-1", "label": "Fable 5.1"}, r1.stderr[-300:] + json.dumps(tuned))
t_empty = tr("empty", [{"type": "user", "timestamp": iso(NOW), "message": {"role": "user", "content": "ciao"}}])
row = {"session_id": "s-start", "transcript": t_empty, "tmux": "alfa"}
CORE.transcript_of = lambda r: r.get("transcript")
rt = CORE.session_runtime(row)
T.check("MS1 a transcript with no turn yet: the state has the model of the SessionStart", rt["model"] == {"id": "claude-fable-5-1", "label": "Fable 5.1"}, str(rt))
t_turn = tr("turn", [{"type": "user", "timestamp": iso(NOW + 5), "message": {"role": "user", "content": "ciao"}},
                     {"type": "assistant", "timestamp": iso(NOW + 10), "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "ok"}],
                                                                                   "usage": {"input_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}])
rt2 = CORE.session_runtime({"session_id": "s-start", "transcript": t_turn, "tmux": "alfa"})
T.check("MS1 a newer turn wins: the transcript's model", (rt2["model"] or {}).get("id", "").startswith("claude-opus-5-5"), str(rt2))

# MS2
(hint / "s-hint.json").write_text(json.dumps({"session_id": "s-hint", "model": "claude-opus-5-5", "effort": "high"}))
rt3 = CORE.session_runtime({"session_id": "s-hint", "transcript": t_empty, "tmux": "beta"})
T.check("MS2 no turn and no note: model and effort from the statusline snapshot", rt3["model"] == {"id": "claude-opus-5-5", "label": "Opus 5.5"} and rt3["effort"] == "high", str(rt3))
(hint / "s-start.json").write_text(json.dumps({"session_id": "s-start", "model": "claude-sonnet-5-5", "effort": "low"}))
rt4 = CORE.session_runtime({"session_id": "s-start", "transcript": t_empty, "tmux": "alfa"})
rt5 = CORE.session_runtime({"session_id": "s-hint", "transcript": t_empty, "tmux": "beta", "host": "win"})
T.check("MS2 never over a note (the SessionStart model stays), only the missing effort; a session of another host not read here",
        rt4["model"]["id"] == "claude-fable-5-1" and rt4["effort"] == "low" and rt5["model"] is None, str((rt4, rt5)))

# MS3
TU = load("cm-tune")
TU.sessions.collect = lambda **k: [{"tmux": "gamma", "name": "gamma", "session_id": "s-g"}]
TU.wide = lambda n: __import__("contextlib").nullcontext()
TU.prompt_ready = lambda n: (False, "busy")
rc_m = TU.set_model("gamma", "claude-fable-5-1")
rc_e = TU.set_effort("gamma", "xhigh")
pend = TU.pending_of("gamma")
tg = json.loads(TUNED.read_text()).get("s-g") or {}
T.check("MS3 busy: model and effort wait (tune-pending), noted at once as chosen, rc 0",
        rc_m == 0 and rc_e == 0 and pend.get("model") == "claude-fable-5-1" and pend.get("effort") == "xhigh"
        and tg.get("model", {}).get("id") == "claude-fable-5-1" and tg.get("effort") == "xhigh", str((rc_m, rc_e, pend, tg)))
TU.pending_path("gamma").unlink()
TU.prompt_ready = lambda n: (False, "dialog")
rc_d = TU.set_model("gamma", "claude-fable-5-1")
TU.prompt_ready = lambda n: (False, "busy")
rc_b = TU.set_model("gamma", "gpt-9")
os.environ["CM_TUNE_APPLYING"] = "1"
rc_a = TU.set_model("gamma", "claude-fable-5-1")
del os.environ["CM_TUNE_APPLYING"]
T.check("MS3 a dialog open (rc 3), a wrong value (rc 2), the executor itself busy again (rc 3): nothing waits",
        (rc_d, rc_b, rc_a) == (3, 2, 3) and not TU.pending_of("gamma"), str((rc_d, rc_b, rc_a)))

# MS4
TU.save_pending("delta", model="claude-opus-5-5[1m]", effort="high")
seq = iter([(False, "busy"), (True, "")])
TU.prompt_ready = lambda n: next(seq, (True, ""))
TU.time.sleep = lambda s: None
calls = []
real = subprocess.run
subprocess.run = lambda cmd, *a, **kw: (calls.append(cmd[-3:]), types.SimpleNamespace(returncode=0, stdout="ok", stderr=""))[1]
rc4 = TU.apply_pending("delta", wait_s=30)
T.check("MS4 the executor waits for the empty prompt, then model and effort with the usual commands; the wait is gone",
        rc4 == 0 and calls == [["model", "delta", "claude-opus-5-5[1m]"], ["effort", "delta", "high"]] and not TU.pending_of("delta"), str((rc4, calls, TU.pending_of("delta"))))
TU.save_pending("eps", model="claude-fable-5-1")
TU.prompt_ready = lambda n: (True, "")
subprocess.run = lambda cmd, *a, **kw: types.SimpleNamespace(returncode=3, stdout="busy", stderr="")
rc4b = TU.apply_pending("eps", wait_s=30)
subprocess.run = real
T.check("MS4 busy again when typing (rc 3): the change keeps waiting", rc4b == 3 and TU.pending_of("eps").get("model") == "claude-fable-5-1", str(rc4b))

# MS5
TU.pending_path("eps").with_suffix(".lock").unlink(missing_ok=True)
pay5 = {"session_id": "s-e", "cwd": str(tmp), "transcript_path": t_empty, "last_assistant_message": "fatto", "stop_hook_active": False}
for _ in range(2):
    subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "Stop"], input=json.dumps(pay5), capture_output=True, text=True, timeout=60,
                   env=dict(os.environ, CM_HOOK_TMUX_NAME="eps", CM_HOOK_SESSION_ID="s-e"))
T.check("MS5 the Stop hook starts the executor for its own session, once (the lock)", SPAWN.exists() and SPAWN.read_text().split() == ["eps"], SPAWN.read_text() if SPAWN.exists() else "")
HO = load("cm-handoff")
pend_seq = iter([{"model": "x"}, {"model": "x"}, {}])
HO._load = lambda n: types.SimpleNamespace(prompt_ready=lambda nm: (True, ""), pending_of=lambda nm: next(pend_seq, {}),
                                           tmux=lambda *a: types.SimpleNamespace(returncode=0), REMOTE={}, GLYPHS=("❯",))
HO.time.sleep = lambda s: None
sent = []
subprocess.run = lambda cmd, *a, **kw: (sent.append(cmd), types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
rc5 = HO.clear_when_idle("eps", "/x.md", wait_s=60)
subprocess.run = real
T.check("MS5 the /clear after a handoff waits for the change to be applied, then goes", rc5 == 0 and len(sent) == 1 and next(pend_seq, "end") == "end", str(sent))
T.finish()
