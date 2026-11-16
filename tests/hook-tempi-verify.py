#!/usr/bin/env python3
"""I tempi degli hook (16/11/2026, supervisor-hook-lenti-16-11).

Dai dati di /doctor su una macchina carica (50 trascrizioni, dal 06/11 al 16/11): 27 scadenze a 5 s su PreToolUse Bash,
16 a 20 s sullo Stop, 6 a 10 s su UserPromptSubmit. Misurato su una home vuota: PreToolUse e PostToolUse avviavano
python a ogni chiamata di strumento per poi non fare niente; piu' di meta' dello Stop era cm-restart.sh, lanciato anche
senza un riavvio armato, e il resto leggeva la trascrizione e caricava moduli che non devono rispondere a nessuno.

Qui ogni hook gira col comando scritto in hooks.json, su un payload con i campi di quello vero (misurato il 13/11 sulla
2.1.325) e una trascrizione di 6 MB, in una home temporanea. Le soglie sono in unita' di B, il tempo su questa macchina
di un python che carica i moduli di base (il migliore di 5), cosi' valgono su una macchina veloce e su una lenta:

    PreToolUse Bash/Monitor, comando normale    <= 0,7 B   e nessun python avviato
    PostToolUse, nessuna domanda aperta         <= 0,7 B   e nessun python avviato
    Stop                                        <= 5 B     (prima circa 16 B), una sola chiamata a tmux, mai cm-restart.sh
    UserPromptSubmit                            <= 6 B
    SessionStart                                <= 8 B

HT1  PreToolUse Bash e Monitor, un comando normale: sotto soglia, nessun python; e' un solo comando per strumento senza
     filtro `if` (gli altri sei partono solo su push, release e publish)
HT2  la guardia decide come prima: `tmux send-keys` negato (python parte), `git push` arriva alla guardia, un `ls` passa
HT3  PostToolUse senza domanda aperta: sotto soglia, nessun python; con la domanda aperta python parte e la chiude; senza
     la cache della cartella di stato si passa da python, come prima
HT4  Stop: sotto soglia; UNA chiamata a tmux (prima sei) e nessuna a cm-restart.sh senza riavvio armato; con un riavvio
     armato lo script parte
HT5  il resto dello Stop gira dopo, staccato (StopLate): i prompt da risolvere, il passaggio di consegne, modello ed effort;
     non parte per intero quando lo Stop ha consegnato qualcosa
HT6  UserPromptSubmit e SessionStart sotto soglia
HT7  i tempi veri: ogni esecuzione scrive una riga in hook-times.jsonl (ev, ms, boot_ms, ph); `supervisor hook-times`
     li riassume per evento
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

LIMITS = {"pre": 0.7, "post": 0.7, "stop": 5.0, "prompt": 6.0, "start": 8.0}   # in unita' di B

tmp = Path(T.tmpdir())
home = tmp / "home"
state = tmp / "state"
for d in (home / ".claude" / "projects" / "p", home / "ws" / "alfa", state, tmp / "bin"):
    d.mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "workspace": {"root": str(home / "ws")},
                           "accounts": {"p": {"config_dir": str(home / ".claude")}}, "relay": {"enabled": False},
                           "restart": {"flag_file": str(state / "restart.json"), "log": str(state / "restart.log")}}))
# una trascrizione di 6 MB: quello che lo Stop leggeva in linea
tr = home / ".claude" / "projects" / "p" / "s-tempi.jsonl"
line = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "x" * 1900}]}, "sessionId": "s-tempi"})
with open(tr, "w") as f:
    for _ in range(3000):
        f.write(line + "\n")
# contatori: un python e un tmux finti davanti a quelli veri
pylog, tmlog, rslog = tmp / "py.log", tmp / "tmux.log", tmp / "restart.log"
real_py = subprocess.run(["bash", "-c", "command -v python3"], capture_output=True, text=True).stdout.strip()
(tmp / "bin" / "pycount").write_text(f'#!/bin/sh\necho "$*" >> "{pylog}"\nexec "{real_py}" "$@"\n')
(tmp / "bin" / "tmux").write_text(f'#!/bin/sh\necho "$*" >> "{tmlog}"\ncase "$*" in *display-message*) echo alfa ;; esac\nexit 0\n')
for x in ("pycount", "tmux"):
    (tmp / "bin" / x).chmod(0o755)
ENV = {"PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
       "CLAUDE_PLUGIN_ROOT": str(T.PLUGIN), "CC_SUPERVISOR_PY": str(tmp / "bin" / "pycount"), "TMUX_PANE": "%1", "CM_RELAY_NO_PUSH": "1",
       "XDG_CACHE_HOME": str(tmp / "cache"), "LC_ALL": "C"}
HOOKS = json.loads((T.PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]


def cmds(event, matcher=None, plain=True):
    """I comandi di hooks.json per un evento (di supervisor, non di claude-observe); plain = senza filtro `if`."""
    out = []
    for g in HOOKS.get(event) or []:
        if matcher is not None and g.get("matcher") != matcher:
            continue
        for h in g["hooks"]:
            if "/observe/" in h["command"] or bool(h.get("if")) == plain:
                continue
            out.append(h["command"])
    return out


def payload(event, **extra):
    """I campi del payload vero di Claude Code 2.1.325 (misurato il 13/11), piu' quelli dell'evento."""
    p = {"session_id": "s-tempi", "transcript_path": str(tr), "cwd": str(home / "ws" / "alfa"), "prompt_id": "ab7c0bcf-1de6-4dab-a509-a9faa37dffc9",
         "permission_mode": "auto", "hook_event_name": event}
    p.update(extra)
    return json.dumps(p)


def run(command, data, env=None):
    t = time.time()
    r = subprocess.run(["bash", "-c", command], input=data, capture_output=True, text=True, env=env or ENV, timeout=60)
    return r, (time.time() - t) * 1000


def best(command, data, n=5, env=None):
    return min(run(command, data, env)[1] for _ in range(n))


def count(f):
    return len(f.read_text().splitlines()) if f.exists() else 0


def rows(ev=None):
    f = state / "hook-times.jsonl"
    out = [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
    return [r for r in out if ev is None or r.get("ev") == ev]


def base(n=3):
    """B adesso: un python che carica i moduli di base, il migliore di n."""
    out = []
    for _ in range(n):
        t = time.time()
        subprocess.run([real_py, "-c", "import json, subprocess, pathlib, datetime"])
        out.append((time.time() - t) * 1000)
    return min(out)


def ratio(command, data, limit, env=None):
    """Il tempo del comando in unita' di B, con B misurato subito prima (il carico della macchina cambia mentre il test
    gira: una suite accanto, un altro processo). Fino a tre giri: basta uno sotto la soglia; altrimenti il migliore."""
    got = []
    for _ in range(3):
        b = base()
        got.append(best(command, data, env=env) / b)
        if got[-1] <= limit:
            break
    return min(got)


B = base(5)
BASH = payload("PreToolUse", tool_name="Bash", tool_input={"command": "ls -la && grep -rn TODO src | head"})

# un primo giro di ogni evento che scrive la cache per cm-fast.sh (come fa la prima SessionStart di una sessione)
start_cmd = cmds("SessionStart")[0]
run(start_cmd, payload("SessionStart", source="startup", model="claude-fable-5-2"))

# HT1
pre_b, pre_m = cmds("PreToolUse", "Bash"), cmds("PreToolUse", "Monitor")
n0 = count(pylog)
t_pre = ratio(pre_b[0], BASH, LIMITS["pre"]) if pre_b else 9e9
t_mon = ratio(pre_m[0], payload("PreToolUse", tool_name="Monitor", tool_input={"command": "tail -f build.log"}), LIMITS["pre"]) if pre_m else 9e9
T.check(f"HT1 PreToolUse Bash and Monitor on an ordinary command: under {LIMITS['pre']} B and no python started (B = {B:.0f} ms here)",
        len(pre_b) == 1 and len(pre_m) == 1 and t_pre <= LIMITS["pre"] and t_mon <= LIMITS["pre"] and count(pylog) == n0,
        f"Bash {t_pre:.2f} B, Monitor {t_mon:.2f} B, limit {LIMITS['pre']} B, python started {count(pylog) - n0} times")
strict = cmds("PreToolUse", "Bash", plain=False)
T.check("HT1 one unfiltered command per tool; the other six entries have an `if` filter (push, release, publish) and do not start on an ordinary command",
        len(strict) == 6 and all("--strict" in c for c in strict), f"{len(pre_b)} plain, {len(strict)} filtered")

# HT2
r_keys, _ = run(pre_b[0], payload("PreToolUse", tool_name="Bash", tool_input={"command": "tmux send-keys -t master 'ok' Enter"}))
n1 = count(pylog)
r_push, _ = run(pre_b[0], payload("PreToolUse", tool_name="Bash", tool_input={"command": "cd repo && git push origin main"}))
n2 = count(pylog)
r_ls, _ = run(pre_b[0], BASH)
T.check("HT2 the guard decides as before: `tmux send-keys` is denied, `git push` reaches the guard (python starts), an `ls` passes with no output",
        '"permissionDecision": "deny"' in r_keys.stdout and "send-keys" in r_keys.stdout and n2 == n1 + 1 and r_push.returncode == 0
        and r_ls.returncode == 0 and not r_ls.stdout.strip() and count(pylog) == n2, f"{r_keys.stdout[:160]} | push: {n2 - n1} python, rc {r_push.returncode} | ls: {r_ls.stdout!r}")

# HT3
post = cmds("PostToolUse")
POST = payload("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}, tool_response={"stdout": "a\nb", "stderr": ""})
n0 = count(pylog)
t_post = ratio(post[0], POST, LIMITS["post"]) if post else 9e9
quiet = count(pylog) - n0
(state / "waiting").mkdir(exist_ok=True)
(state / "waiting" / "s-tempi").write_text("{}")
run(post[0], POST)
closed = not (state / "waiting" / "s-tempi").exists()
n1 = count(pylog)
env_nocache = dict(ENV, XDG_CACHE_HOME=str(tmp / "no-cache"))
run(post[0], POST, env_nocache)
T.check(f"HT3 PostToolUse with no open question: under {LIMITS['post']} B and no python started; with one open python starts and closes it; with no cache of the state folder it goes through python, as before",
        len(post) == 1 and t_post <= LIMITS["post"] and quiet == 0 and closed and n1 >= n0 + 1 and count(pylog) == n1 + 1,
        f"{t_post:.2f} B, limit {LIMITS['post']} B, python {quiet} in the quiet runs, closed {closed}, no cache: {count(pylog) - n1} python")

# HT4
stop = cmds("Stop")
STOP = payload("Stop", stop_hook_active=False, last_assistant_message="Fatto: tre file corretti.\nEsito: verde")
shim = T.SCRIPTS / "cm-restart.sh"
tm0 = count(tmlog)
r_stop, _ = run(stop[0], STOP)
tm_one = count(tmlog) - tm0
t_stop = ratio(stop[0], STOP, LIMITS["stop"])
T.check(f"HT4 Stop: under {LIMITS['stop']} B, and ONE call to tmux for the session's name (six before); no output when nothing waits",
        len(stop) == 1 and t_stop <= LIMITS["stop"] and tm_one == 1 and r_stop.returncode == 0 and not r_stop.stdout.strip(),
        f"{t_stop:.2f} B, limit {LIMITS['stop']} B, tmux calls in one Stop: {tm_one}, out {r_stop.stdout[:80]!r} {r_stop.stderr[-200:]}")
ph = (rows("Stop") or [{}])[-1].get("ph") or {}
(state / "restart-alfa.json").write_text(json.dumps({"tmux": "beta"}))   # armato da un'altra sessione: lo script parte, e lo lascia stare
run(stop[0], STOP)
ph_armed = (rows("Stop") or [{}])[-1].get("ph") or {}
(state / "restart-alfa.json").unlink()
T.check("HT4 cm-restart.sh runs only with a restart armed: no «restart» phase in an ordinary Stop, one when a flag is there",
        "restart" not in ph and shim.exists() and "restart" in ph_armed, f"{ph} | {ph_armed}")

# HT5
ok_late = T.wait_until(lambda: any((r.get("ph") or {}).get("resolve") is not None or r.get("ev") == "StopLate" for r in rows("StopLate")), timeout=15)
late = rows("StopLate")
T.check("HT5 the rest of the Stop runs afterwards, detached (StopLate), and writes its own row of times",
        ok_late and len(late) >= 1 and all(r["ev"] == "StopLate" for r in late), str(late[-2:]))
n_late = len(late)
(state / "queue").mkdir(exist_ok=True)
qcmd = [str(T.SCRIPTS / "supervisor"), "queue", "add", "s-tempi", "continua col passo due"]
subprocess.run(qcmd, capture_output=True, text=True, env=ENV, timeout=60)
r_q, _ = run(stop[0], STOP)
T.wait_until(lambda: len(rows("StopLate")) > n_late, timeout=15)
T.check("HT5 a Stop that hands something over (here an item of the queue, when there is one) still answers at once and still starts StopLate",
        r_q.returncode == 0 and len(rows("StopLate")) > n_late, f"{r_q.stdout[:120]!r} {len(rows('StopLate'))} > {n_late}")

# HT6
prompt = cmds("UserPromptSubmit")
t_prompt = ratio(prompt[0], payload("UserPromptSubmit", prompt="continua pure"), LIMITS["prompt"])
t_start = ratio(start_cmd, payload("SessionStart", source="resume", model="claude-fable-5-2"), LIMITS["start"])
T.check(f"HT6 UserPromptSubmit under {LIMITS['prompt']} B and SessionStart (a resume, with the 6 MB transcript) under {LIMITS['start']} B",
        t_prompt <= LIMITS["prompt"] and t_start <= LIMITS["start"], f"prompt {t_prompt:.2f} B (limit {LIMITS['prompt']}), start {t_start:.2f} B (limit {LIMITS['start']})")

# HT7
all_rows = rows()
evs = {r["ev"] for r in all_rows}
T.check("HT7 every run writes a row in hook-times.jsonl: ev, ms, boot_ms, ph; the events measured here are all there",
        {"SessionStart", "Stop", "StopLate", "UserPromptSubmit", "PostToolUse"} <= evs
        and all(isinstance(r.get("ms"), int) and isinstance(r.get("boot_ms"), int) and isinstance(r.get("ph"), dict) and r["ms"] >= r["boot_ms"] >= 0 for r in all_rows),
        f"{sorted(evs)} {all_rows[-1] if all_rows else None}")
r_t = subprocess.run([str(T.SCRIPTS / "supervisor"), "hook-times", "--json"], capture_output=True, text=True, env=ENV, timeout=60)
summ = {x["ev"]: x for x in json.loads(r_t.stdout)} if r_t.returncode == 0 and r_t.stdout.strip().startswith("[") else {}
r_tt = subprocess.run([str(T.SCRIPTS / "supervisor"), "hook-times"], capture_output=True, text=True, env=ENV, timeout=60)
T.check("HT7 supervisor hook-times sums them up per event: runs, median, 90%, maximum, the dearest phase",
        summ.get("Stop", {}).get("n", 0) >= 3 and summ["Stop"]["median_ms"] <= summ["Stop"]["p90_ms"] <= summ["Stop"]["max_ms"]
        and "MEDIANA" in r_tt.stdout and any(x.startswith("Stop ") for x in r_tt.stdout.splitlines()), f"{summ.get('Stop')} {r_tt.stdout[:300]} {r_t.stderr[-200:]}")

print(f"  --  B = {B:.0f} ms; PreToolUse {t_pre:.2f} B, Monitor {t_mon:.2f} B, PostToolUse {t_post:.2f} B, Stop {t_stop:.2f} B, UserPromptSubmit {t_prompt:.2f} B, SessionStart {t_start:.2f} B")
T.rm(str(tmp))
T.finish()
