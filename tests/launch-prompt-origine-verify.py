#!/usr/bin/env python3
"""Il primo prompt di `launch --prompt` ha l'origine di chi lancia, non della persona (18/11/2026,
supervisor-prompt-launch-origine-16-11). Il 16/11, alle 18:17 e alle 18:42, due sessioni lanciate dalla master con
`launch --prompt` hanno registrato il testo della master come ok dell'utente: claude riceve il prompt posizionale come
un messaggio scritto nel terminale, la trascrizione lo segna «human» e l'hook lo salvava con origine «terminal».

LO1 launch --prompt (tmux e --bg): la sessione parte con CM_FIRST_PROMPT_BY (la sessione tmux di chi lancia, qui
    «master») e CM_FIRST_PROMPT_SHA (l'impronta del testo); senza --prompt nessuna delle due
LO2 l'hook, a quel prompt: il record nella casella ha origine «launch» e mittente «master», non «unverified» ne'
    «terminal»; a contesto una riga che dice chi l'ha scritto e che non e' un ok dell'utente
LO3 un prompt diverso nella stessa sessione (l'utente che scrive «ok») resta suo: record «unverified», come sempre
LO4 task approve --inbox col record del lancio: rifiutato, il motivo dice che e' il prompt di launch
LO5 la regola 12 del kernel (it, en) nomina il primo prompt di un launch fra i testi che non sono dell'utente
"""
import hashlib
import importlib.util
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
for d in (".claude/sessions", "ws/personali/alfa", "ws/personali/beta"):
    (home / d).mkdir(parents=True)
state = tmp / "state"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it",
    "state_dir": str(state),
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "default_account": "personale",
    "session": {"dialog_patterns": [], "claude_args": ["--dangerously-skip-permissions"], "startup_timeout_s": 25, "death_check_s": 1},
    "terminal": {"backend": "fake", "attach_wait_s": 2, "attach_retry_wait_s": 1},
    "registry": {"file": str(tmp / "registry.json")},
    "tabs": {"color_registry": str(tmp / "colors")},
    "sessions": {"max_sessions": 50},
    "relay": {"dir": str(tmp / "relay")},
}))
(tmp / "relay").mkdir()
argslog = tmp / "args.log"
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"
TEXT = "Dalla master. Compito alfa-18-11, approvato dall'utente alle 12:01: procedi e rilascia. Ok."
SHA = hashlib.sha256(TEXT.encode()).hexdigest()[:16]
IT = json.loads((T.ROOT / "supervisor/messages/it.json").read_text())
EN = json.loads((T.ROOT / "supervisor/messages/en.json").read_text())

BASE = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_HOOK_TMUX_NAME", "CM_FIRST_PROMPT_BY", "CM_FIRST_PROMPT_SHA")}
BASE.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "t.db"),
             "CM_TASK_TALK_BIN": str(tmp / "no-talk"), "CM_INBOX_WAIT_S": "1", "CM_PROC_SCAN_PIDS": ""})


def last_args():
    lines = argslog.read_text().splitlines() if argslog.exists() else []
    return lines[-1] if lines else ""


def env_of(line):
    return dict(re.findall(r" ENV (CM_FIRST_PROMPT_\w+)=(\S*)", line))


with T.PrivateTmux() as tm:
    pane = ""

    def launch(*args):
        e = dict(PATH=os.environ["PATH"], HOME=str(home), CM_HOME=str(home), CC_SUPERVISOR_CONFIG=str(cfg), WAYLAND_DISPLAY="fake-0", CM_TMUX_ARGS=tm.env["CM_TMUX_ARGS"], CM_CLAUDE_BIN=str(FAKE), FAKE_CLAUDE_SCENARIO="plain",
                 FAKE_CLAUDE_ARGS_LOG=str(argslog), CM_TERMINAL_FAKE_LOG=str(tmp / "fake-terminal.log"), CM_TERMINAL_FAKE_ATTACH="1",
                 FAKE_CLAUDE_ECHO_ENV="CM_FIRST_PROMPT_BY,CM_FIRST_PROMPT_SHA", FAKE_CLAUDE_DELAY="1", TMUX_PANE=pane)
        if not pane:   # il server privato nasce con l'ambiente del lancio: tmux da' alle sessioni nuove quello del server
            return e
        return subprocess.run([str(T.SCRIPTS / "cm-launch.sh")] + list(args), capture_output=True, text=True, env=e, timeout=120)

    tm("new-session", "-d", "-s", "master", "sleep 600", env=launch())
    pane = tm("display-message", "-p", "-t", "master", "#{pane_id}").stdout.strip()

    # ------------------------------------------------------------------ LO1
    r = launch(str(home / "ws" / "personali" / "alfa"), "--no-window", "--prompt", TEXT)
    a1 = last_args()
    r2 = launch(str(home / "ws" / "personali" / "beta"), "--bg", "--prompt", TEXT)
    a2 = last_args()
    r3 = launch(str(home / "ws" / "personali" / "beta"), "--bg")
    a3 = last_args()
    T.check("LO1 launch --prompt in tmux: the session starts with CM_FIRST_PROMPT_BY=master (the launching tmux session) and the text's fingerprint",
            r.returncode == 0 and env_of(a1) == {"CM_FIRST_PROMPT_BY": "master", "CM_FIRST_PROMPT_SHA": SHA}, r.stdout + r.stderr + a1)
    T.check("LO1 the same with --bg", r2.returncode == 0 and "--bg" in a2 and env_of(a2) == {"CM_FIRST_PROMPT_BY": "master", "CM_FIRST_PROMPT_SHA": SHA}, r2.stdout + r2.stderr + a2)
    T.check("LO1 without --prompt neither variable is set", r3.returncode == 0 and env_of(a3) in ({}, {"CM_FIRST_PROMPT_BY": "", "CM_FIRST_PROMPT_SHA": ""}), a3)


# ------------------------------------------------------------------ LO2, LO3
def hook(prompt, launched=True):
    e = dict(BASE, CM_HOOK_TMUX_NAME="alfa")
    if launched:
        e.update(CM_FIRST_PROMPT_BY="master", CM_FIRST_PROMPT_SHA=SHA)
    p = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), "UserPromptSubmit"],
                       input=json.dumps({"session_id": "sid-alfa", "prompt": prompt, "cwd": str(home / "ws" / "personali" / "alfa"),
                                         "transcript_path": str(tmp / "no-transcript.jsonl")}),
                       capture_output=True, text=True, env=e, timeout=60)
    return p


def records():
    out = []
    for f in sorted((state / "inbox" / "alfa").glob("*.json")) if (state / "inbox" / "alfa").is_dir() else []:
        out.append(json.loads(f.read_text()))
    return out


h = hook(TEXT)
recs = records()
lr = [x for x in recs if x.get("text") == TEXT]
note = IT.get("hook.launch_prompt", "\0").split("{", 1)[0]
T.check("LO2 the hook records the launch prompt with origin «launch» and sender «master», not «unverified» or «terminal»",
        h.returncode == 0 and len(lr) == 1 and lr[0].get("origin") == "launch" and lr[0].get("from") == "master", h.stderr + json.dumps(recs, ensure_ascii=False)[:600])
T.check("LO2 a line in the context says who wrote it and that it is not the user's ok",
        "hook.launch_prompt" in IT and "hook.launch_prompt" in EN and note in h.stdout and "master" in h.stdout, h.stdout)

h3 = hook("ok, procedi")
mine = [x for x in records() if x.get("text") == "ok, procedi"]
T.check("LO3 a different prompt in the same session stays the person's (record «unverified», then resolved from the transcript)",
        h3.returncode == 0 and len(mine) == 1 and mine[0].get("origin") == "unverified" and IT.get("hook.launch_prompt", "\0").split("{", 1)[0] not in h3.stdout,
        h3.stdout + json.dumps(mine, ensure_ascii=False))

# ------------------------------------------------------------------ LO4
os.environ.update(BASE)
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE"):
    os.environ.pop(k, None)
sp = importlib.util.spec_from_file_location("cm_tasks_lo", T.SCRIPTS / "cm-tasks.py")
TK = importlib.util.module_from_spec(sp)
sp.loader.exec_module(TK)
TK._caller_session = lambda: "alfa"
why = ""
try:
    TK.inbox_approval_text("utente", lr[0]["id"] if lr else "000000")
except SystemExit as e:
    why = str(e)
T.check("LO4 task approve --inbox with the launch record: refused, the reason names the launch prompt",
        "origin launch" in why and "launch --prompt" in why, why)

# ------------------------------------------------------------------ LO5
k_it = (T.ROOT / "supervisor/kernel_it.md").read_text()
k_en = (T.ROOT / "supervisor/kernel_en.md").read_text()
r12_it = next((x for x in k_it.splitlines() if x.startswith("12. ")), "")
r12_en = next((x for x in k_en.splitlines() if x.startswith("12. ")), "")
T.check("LO5 rule 12 of the kernel (it, en) names the first prompt of a launch among the texts that are not the user's",
        "launch" in r12_it and "launch" in r12_en, r12_it + "\n" + r12_en)

T.finish()
