#!/usr/bin/env python3
"""Il testo fra <private> e </private> non esce mai dalla sessione (19/11/2026, supervisor-privato-19-11; idea 4 da
claude-mem). Caso vero: il codice del login che la persona deve mandare in chat. La sessione lo vede (la trascrizione di Claude
Code non si tocca); supervisor lo toglie, con «[privato]» al suo posto, prima di ogni scrittura su disco o invio fuori:
casella, registro (ledger: recap, esiti, stato), domande in attesa, chat dell'app, Telegram, passaggi di consegne.

PV1 il filtro: «ok <private>…</private> procedi» → «ok [privato] procedi»; piu' blocchi; maiuscole; un <private> senza
    chiusura toglie fino alla fine; senza tag il testo resta uguale
PV2 hook UserPromptSubmit: il record della casella e le righe del registro non portano il segreto
PV3 hook Stop: l'esito e la coda della risposta nel registro non portano il segreto (la risposta lo ripete)
PV4 la chat dell'app (transcript_entries): ne' il messaggio della persona ne' la risposta portano il segreto
PV5 Telegram: il messaggio inviato non porta il segreto
PV6 supervisor handoff --written: il file del passaggio di consegne non porta il segreto
PV7 la trascrizione di Claude Code resta com'e': la sessione vede quello che la persona ha scritto
"""
import datetime
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

SECRET = "codice-login-482913"
tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
alfa = ws / "personali" / "alfa"
alfa.mkdir(parents=True)
(home / ".claude").mkdir(parents=True)
state = tmp / "state"
tg = tmp / "tg"
tg.mkdir()
(tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
(tg / "access.json").write_text(json.dumps({"allowFrom": ["1001"]}))
API, CALLS, _ = T.fake_telegram()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(tmp / "relay")},
    "bot": {"api_base": API, "token_file": str(tg / ".env"), "access_file": str(tg / "access.json")},
    "hooks": {"local_time": {"enabled": False}, "session_kernel": {"enabled": False}},
}))
BASE = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_HOOK_TMUX_NAME", "CM_FIRST_PROMPT_BY", "CM_FIRST_PROMPT_SHA")}
BASE.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_PROC_SCAN_PIDS": "", "CM_HOOK_TMUX_NAME": "alfa"})
os.environ.update(BASE)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_pv", T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def everything(d):
    """Tutto il testo dei file sotto d (casella, registro, waiting...)."""
    out = []
    for p in Path(d).rglob("*"):
        if p.is_file():
            try:
                out.append(p.read_text(errors="replace"))
            except OSError:
                pass
    return "\n".join(out)


# ------------------------------------------------------------------ PV1
cm = load("cm-config")
f = getattr(cm, "private_strip", None)
cases = {f"ok <private>{SECRET}</private> procedi": "ok [privato] procedi",
         f"a <private>x</private> b <PRIVATE>{SECRET}</PRIVATE> c": "a [privato] b [privato] c",
         f"prima\n<private>\n{SECRET}\n</private>\ndopo": "prima\n[privato]\ndopo",
         f"senza chiusura <private>{SECRET} e il resto": "senza chiusura [privato]",
         "niente tag qui": "niente tag qui"}
got = {k: (f(k) if f else None) for k in cases}
T.check("PV1 the filter: blocks become «[privato]», several blocks, any case, an unclosed tag strips to the end, no tag no change",
        got == cases, json.dumps(got, ensure_ascii=False))

# ------------------------------------------------------------------ PV2, PV3
tr = tmp / "S-P.jsonl"


def hook(ev, payload):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), ev], input=json.dumps(payload), capture_output=True, text=True,
                          env=BASE, timeout=60)


prompt = f"il codice del login e' <private>{SECRET}</private>, usalo"
h = hook("UserPromptSubmit", {"session_id": "S-P", "prompt": prompt, "cwd": str(alfa), "transcript_path": str(tr)})
box = everything(state / "inbox")
T.check("PV2 UserPromptSubmit: the inbox record and the registry carry «[privato]», never the secret",
        h.returncode == 0 and "[privato]" in box and SECRET not in box and SECRET not in everything(state), box[-300:] + h.stderr[-300:])
h = hook("Stop", {"session_id": "S-P", "cwd": str(alfa), "transcript_path": str(tr),
                  "last_assistant_message": f"Fatto con <private>{SECRET}</private>.\n\nEsito: login fatto con <private>{SECRET}</private>"})
T.check("PV3 Stop: the outcome and the reply's tail in the registry never carry the secret (the reply repeats it)",
        h.returncode == 0 and SECRET not in everything(state), [l for l in everything(state).splitlines() if SECRET in l][:2])

# ------------------------------------------------------------------ PV4, PV7
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
rows = [{"type": "user", "uuid": "u1", "timestamp": now, "sessionId": "S-P", "message": {"role": "user", "content": prompt}},
        {"type": "assistant", "uuid": "a1", "timestamp": now, "sessionId": "S-P",
         "message": {"role": "assistant", "content": [{"type": "text", "text": f"Uso <private>{SECRET}</private> adesso."}]}}]
tr.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
core = load("cm-core")
res = core.transcript_entries(str(tr))
ent = json.dumps(res, ensure_ascii=False)
T.check("PV4 the app's chat (transcript_entries): neither the person's message nor the reply carries the secret",
        SECRET not in ent and "[privato]" in ent, ent[:400])
T.check("PV7 Claude Code's transcript stays as it is: the session sees what the person wrote", SECRET in tr.read_text(), "")

# ------------------------------------------------------------------ PV5
bot = load("cm-bot")
bot.send(f"risposta: <private>{SECRET}</private> ok")
sent = json.dumps(CALLS.get("sendMessage"), ensure_ascii=False)
T.check("PV5 Telegram: the message sent never carries the secret", CALLS.get("sendMessage") and SECRET not in sent and "[privato]" in sent, sent[:300])

# ------------------------------------------------------------------ PV6
hf = alfa / "docs" / "handoff-2026-11-19.md"
hf.parent.mkdir()
hf.write_text(f"# Passaggio di consegne\n\nIl login usa <private>{SECRET}</private>.\n")
r = subprocess.run([str(T.SCRIPTS / "supervisor"), "handoff", "--written", str(hf)], capture_output=True, text=True, env=dict(BASE, PWD=str(alfa)),
                   cwd=str(alfa), timeout=60)
T.check("PV6 supervisor handoff --written: the handoff file never carries the secret", SECRET not in hf.read_text() and "[privato]" in hf.read_text(),
        hf.read_text()[-200:] + r.stderr[-200:])

T.finish()
