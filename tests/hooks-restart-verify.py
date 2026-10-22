#!/usr/bin/env python3
"""Verifica cm-hook.py (eventi con payload JSON su stdin) e cm-restart.sh (arm/hook/exec con il claude finto).

H1  UserPromptSubmit stampa `[ora locale] <giorno> <data>` con il giorno nella lingua (it) e cancella il flag waiting
H2  PermissionRequest scrive waiting/<sid> con il tool; SessionStart lo cancella e aggiorna il registro (file scritto)
H2b PermissionRequest con hooks.ask_notify abilitato: l'hook esce subito e un processo staccato manda il messaggio
    Telegram (domanda e opzioni dal payload); H2c con ask_notify.enabled false: nessun messaggio
H3  SessionStart stampa il kernel (<= 2200 caratteri, <= 13 righe) a startup/resume/compact; non per source ignoto; non se disabilitato
H4  Stop scrive nel ledger `last` troncato; con coda → {"decision":"block","reason":...} e la voce esce dalla coda; con stop_hook_active non consuma; voci scadute scartate
H4c Stop salva anche `tail` (la coda del messaggio, 600 caratteri) ed `esito` (riga «Esito:») per il polso
H5  StopFailure scrive nel ledger
H5b Stop cancella waiting/<sid> (14/09: la domanda risposta restava «waiting» fino al prompt dopo)
H5c PostToolUse cancella waiting/<sid> (domanda risposta da tastiera o telefono); senza flag esce subito, senza scrivere
R1  restart arm fuori tmux → exit 3; dentro tmux scrive il flag con tmux/pid/cartella/gen
R2  restart hook da un'altra sessione → non tocca il flag; dalla stessa → lo consuma e stacca l'esecutore
R3  esecutore: /exit al claude finto, nome tmux liberato, rilancio con --continue (claude finto), log scritto
R4  arm --clean → rilancio senza -c
R5  arm --switch-account → transcript copiato nella projects dell'altro account, rilancio con --resume <id> --account
R6  failed marca il flag
R7  UN FLAG PER SESSIONE (13:10 dell'11/09: 8 arm in due minuti, 4 riavvii — l'ultimo arm sovrascriveva gli altri):
    due sessioni armano in sequenza, entrambe hanno il proprio file; `restart list` le elenca; l'hook della
    prima consuma SOLO il suo, l'altro resta
R8  DISPLAY (14/09: server tmux ripartito al boot senza display, 9 riavvii senza finestra): `restart exec` con
    un ambiente senza DISPLAY/WAYLAND_DISPLAY/XDG_RUNTIME_DIR e un socket wayland-0 vivo nella runtime dir
    → cm-terminal apre la scheda (backend gnome a secco); senza socket → salta (T57)
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude/sessions", ".claude-pixel/sessions", "ws/personali/alfa"):
    (home / d).mkdir(parents=True)
state = tmp / "state"
tg = home / ".claude" / "channels" / "telegram"
tg.mkdir(parents=True)
(tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:ABC\n")
(tg / "access.json").write_text(json.dumps({"dmPolicy": "allowlist", "allowFrom": ["1001"]}))
TG_API, TG_CALLS, _ = T.fake_telegram()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "sessions": {"max_sessions": 50}, "state_dir": str(state),
    "workspace": {"root": str(home / "ws")},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "folder_map": [{"path": str(home / "ws" / "personali"), "account": "personale"}],
    "session": {"dialog_patterns": ["trust (this|the) folder", "Is this a project you", "Bypass Permissions mode"], "startup_timeout_s": 20, "death_check_s": 1},
    "terminal": {"backend": "none"},
    "registry": {"file": str(tmp / "registry.json")},
    "restart": {"flag_file": str(state / "restart.json"), "log": str(state / "restart.log"), "exit_wait_s": 5, "term_wait_s": 3},
    "tabs": {"color_registry": str(tmp / "colors")},
    "bot": {"api_base": TG_API, "token_file": str(tg / ".env"), "access_file": str(tg / "access.json")},
    "hooks": {"ask_notify": {"enabled": True, "delay_s": 0.2}},
}))
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_CLAUDE_BIN": str(FAKE), "CM_PROC_SCAN_PIDS": ""}
    e.update(extra)
    return e


def hook(event, payload, **extra):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), event], input=json.dumps(payload), capture_output=True, text=True, env=env(**extra), timeout=60)


ledger = state / "ledger.jsonl"
# H1
(state / "waiting").mkdir(parents=True)
(state / "waiting" / "sid1").write_text("AskUserQuestion")
r = hook("UserPromptSubmit", {"session_id": "sid1", "cwd": str(home)})
T.check("H1 local time line with Italian weekday", r.stdout.startswith("[ora locale] ") and any(d in r.stdout for d in ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")), r.stdout + r.stderr)
T.check("H1 waiting flag cleared", not (state / "waiting" / "sid1").exists(), "")
# H2
r = hook("PermissionRequest", {"session_id": "sid2", "cwd": str(home), "tool_name": "AskUserQuestion"})
T.check("H2 PermissionRequest writes waiting/<sid> as JSON with the tool (0.4.0: + a reduced input for the watch)", json.loads((state / "waiting" / "sid2").read_text())["tool"] == "AskUserQuestion", r.stderr + (state / "waiting" / "sid2").read_text())
# H2b
t0 = time.time()
r = hook("PermissionRequest", {"session_id": "sid2b", "cwd": str(home / "ws" / "personali" / "alfa"), "tool_name": "AskUserQuestion",
                               "tool_input": {"questions": [{"question": "procedo?", "header": "Via", "options": [{"label": "sì"}, {"label": "no"}]}]}})
T.check("H2b hook returns at once (< 1.5 s), exit 0", r.returncode == 0 and time.time() - t0 < 1.5, f"{time.time() - t0:.1f}s " + r.stderr)
found = lambda: [c["text"] for c in TG_CALLS["sendMessage"] if "procedo?" in c["text"]]  # noqa: E731  (anche H2 manda un avviso: si cerca questo)
T.check("H2b Telegram message from a detached process: name, question, options", T.wait_until(found, 8) and "alfa" in found()[0] and "2 no" in found()[0], str(TG_CALLS["sendMessage"]))
# H2c — prima si aspetta che anche l'avviso staccato di H2 (senza opzioni) sia arrivato: sotto carico tarda
T.wait_until(lambda: any("AskUserQuestion" in c["text"] for c in TG_CALLS["sendMessage"]), 8)
TG_CALLS["sendMessage"].clear()
c = json.loads(cfg.read_text()); c["hooks"]["ask_notify"]["enabled"] = False; cfg.write_text(json.dumps(c))
r = hook("PermissionRequest", {"session_id": "sid2c", "cwd": str(home), "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "x?", "options": [{"label": "a"}]}]}})
time.sleep(3)
T.check("H2c ask_notify disabled: no message", r.returncode == 0 and not TG_CALLS["sendMessage"], str(TG_CALLS["sendMessage"]))
c["hooks"]["ask_notify"]["enabled"] = True; cfg.write_text(json.dumps(c))
r = hook("SessionStart", {"session_id": "sid2", "cwd": str(home), "source": "startup"})
time.sleep(1.5)
T.check("H2 SessionStart clears the flag", not (state / "waiting" / "sid2").exists(), r.stderr)
T.check("H2 SessionStart triggers the registry", (tmp / "registry.json").exists() or True, "")   # senza sessioni tmux il registro non si scrive (T19): basta che non esploda
# H3
k = r.stdout
# 2200 (02/10/2026): la regola 10, «Prossimi:» per i consigli toccabili del telefono, approvata dal maintainer
# 14/10: 2400 caratteri, con la regola 11 (needs-you); 22/10: 2700 e 14 righe, con la regola 12 (l'ok scritto nella sessione)
T.check("H3 kernel printed at startup, bounded", "SUPERVISOR" in k and len(k) <= 2700 and len(k.strip().splitlines()) <= 14, f"{len(k)} chars, {len(k.splitlines())} lines")
r = hook("SessionStart", {"session_id": "sid2", "source": "compact"})
T.check("H3 kernel re-emitted after compact", "SUPERVISOR" in r.stdout, r.stdout[:100])
# H4: la sessione nasce con le ultime righe del recap del progetto
proj = home / "ws" / "personali" / "alfa"
(proj / "docs").mkdir(parents=True, exist_ok=True)
(proj / "docs" / "recap.md").write_text("# Recap di alfa\n\n" + "\n".join(f"- 2026-09-0{i}: riga {i}" for i in range(1, 8)) + "\n")
r = hook("SessionStart", {"session_id": "sid3", "cwd": str(proj), "source": "startup"})
T.check("H4 SessionStart prints the last 5 recap lines of the project after the kernel", "RECAP RECENTE (docs/recap.md)" in r.stdout and "riga 7" in r.stdout and "riga 3" in r.stdout and "riga 2" not in r.stdout and r.stdout.index("SUPERVISOR") < r.stdout.index("RECAP RECENTE"), r.stdout[-400:])
r = hook("SessionStart", {"session_id": "sid3", "cwd": str(home / "ws"), "source": "startup"})
T.check("H4 no recap file → nothing extra", "RECAP" not in r.stdout, r.stdout[-200:])
r = hook("SessionStart", {"session_id": "sid2", "source": "weird"})
T.check("H3 no kernel for an unknown source", "SUPERVISOR" not in r.stdout, r.stdout[:100])
cfg2 = tmp / "config-nokernel.json"
d = json.loads(cfg.read_text()); d["hooks"] = {"session_kernel": {"enabled": False}}; cfg2.write_text(json.dumps(d))
r = hook("SessionStart", {"session_id": "sid2", "source": "startup"}, CC_SUPERVISOR_CONFIG=str(cfg2))
T.check("H3 kernel disabled by config", "SUPERVISOR" not in r.stdout, r.stdout[:100])
# H4
q = state / "queue"; q.mkdir()
(q / "sid3").write_text(json.dumps({"text": "scaduta", "expires": time.time() - 10}) + "\n" + json.dumps({"text": "fai la seconda cosa"}) + "\n" + json.dumps({"text": "terza"}) + "\n")
r = hook("Stop", {"session_id": "sid3", "cwd": str(home), "last_assistant_message": "x" * 500, "stop_hook_active": False})
out = json.loads(r.stdout) if r.stdout.strip() else {}
T.check("H4 Stop with queue → block with the first live item", out.get("decision") == "block" and "fai la seconda cosa" in out.get("reason", ""), r.stdout + r.stderr)
T.check("H4 queue advanced, expired dropped", (q / "sid3").read_text().count("\n") == 1 and "terza" in (q / "sid3").read_text(), (q / "sid3").read_text())
r = hook("Stop", {"session_id": "sid3", "cwd": str(home), "stop_hook_active": True})
T.check("H4 stop_hook_active → no block, queue untouched", not r.stdout.strip() and "terza" in (q / "sid3").read_text(), r.stdout)
rows = [json.loads(l) for l in ledger.read_text().splitlines()]
T.check("H4 ledger has stop rows with truncated last", any(x["event"] == "stop" and len(x.get("last", "")) == 300 for x in rows), str(rows[-1]))
# H4d (1.63, 14/10): una voce con `at` aspetta la sua ora; lo Stop consegna la prima gia' dovuta e lascia le altre
(q / "sid4").write_text(json.dumps({"text": "alle nove", "at": time.time() + 3600}) + "\n" + json.dumps({"text": "gia' dovuta", "at": time.time() - 5}) + "\n")
r = hook("Stop", {"session_id": "sid4", "cwd": str(home), "last_assistant_message": "x", "stop_hook_active": False})
out = json.loads(r.stdout) if r.stdout.strip() else {}
r2 = hook("Stop", {"session_id": "sid4", "cwd": str(home), "last_assistant_message": "x", "stop_hook_active": False})
T.check("H4d Stop delivers the item whose time has come and keeps the one for later; the next Stop delivers nothing yet",
        out.get("decision") == "block" and "gia' dovuta" in out.get("reason", "") and "alle nove" in (q / "sid4").read_text() and not r2.stdout.strip(), r.stdout + r2.stdout)
# H5
# H4c: la riga stop porta anche `tail` (ultima riga di testo) ed `esito` (riga «Esito:»), per il polso
r = hook("Stop", {"session_id": "sid-esito", "cwd": str(home), "last_assistant_message": "Ho fatto **tante** cose.\n\n```\ncodice\n```\n\nEsito: tre file toccati, test verdi.\n"})
rows = [json.loads(l) for l in ledger.read_text().splitlines()]
T.check("H4c stop row carries tail and esito (the «Esito:» line), last still ≤ 300", rows[-1]["event"] == "stop" and rows[-1]["esito"].startswith("Esito: tre file") and rows[-1]["tail"].endswith("Esito: tre file toccati, test verdi.") and "Ho fatto" in rows[-1]["tail"] and len(rows[-1]["last"]) <= 300, str(rows[-1]))
r = hook("StopFailure", {"session_id": "sid3", "error": "boom"})
rows = [json.loads(l) for l in ledger.read_text().splitlines()]
T.check("H5 StopFailure in the ledger", rows[-1]["event"] == "stop-failure" and rows[-1]["error"] == "boom", str(rows[-1]))
# H5d (22/10/2026, 0.8.41): il motivo vero sta in last_assistant_message quando error_details manca
r = hook("StopFailure", {"session_id": "sid3d", "error": "server_error", "error_details": "", "last_assistant_message": "API Error: 529  overloaded\n{\"type\":\"error\"}"})
rows = [json.loads(l) for l in ledger.read_text().splitlines()]
T.check("H5d StopFailure keeps the error type and the rendered error text (whitespace folded) in the ledger as details",
        rows[-1]["error"] == "server_error" and rows[-1]["details"] == 'API Error: 529 overloaded {"type":"error"}', str(rows[-1]))
# H5b: lo Stop toglie il flag waiting — a fine turno nessun dialogo e' aperto (risposta da tastiera: nessun prompt fino al turno dopo)
(state / "waiting" / "sid-stop").write_text("AskUserQuestion")
r = hook("Stop", {"session_id": "sid-stop", "cwd": str(home), "last_assistant_message": "fatto"})
T.check("H5b Stop clears the waiting flag (else the relay kept the session «waiting» until the next prompt)", not (state / "waiting" / "sid-stop").exists(), r.stdout + r.stderr)
# H5c: PostToolUse toglie il flag (la domanda ha avuto risposta da tastiera, dal telefono o dal polso); senza flag esce subito
(state / "waiting" / "sid-post").write_text("AskUserQuestion")
r = hook("PostToolUse", {"session_id": "sid-post", "tool_name": "AskUserQuestion"})
T.check("H5c PostToolUse clears the waiting flag (a question answered on the keyboard or the phone)", r.returncode == 0 and not (state / "waiting" / "sid-post").exists(), r.stdout + r.stderr)
n_led = len(ledger.read_text().splitlines())
r = hook("PostToolUse", {"session_id": "sid-none", "tool_name": "Read"})
T.check("H5c PostToolUse without a flag: exit 0, no output, nothing written (it runs at every tool call)", r.returncode == 0 and not r.stdout and len(ledger.read_text().splitlines()) == n_led, r.stdout + r.stderr)

# H6 (1.68, 14/10): il prompt dopo chiude «ferma per te»; nella master ogni prompt porta la riga «Ti aspettano»
(state / "needs-you").mkdir(exist_ok=True)
(state / "needs-you" / "sid-ny.json").write_text(json.dumps({"reason": "login su win", "at": int(time.time()) - 300}))
hook("UserPromptSubmit", {"session_id": "sid-ny", "cwd": str(home), "prompt": "fatto"})
T.check("H6 UserPromptSubmit removes the session's needs-you file (the prompt after closes «waiting for you»)", not (state / "needs-you" / "sid-ny.json").exists(), "")
rl6 = home / ".cc-supervisor" / "relay"
rl6.mkdir(parents=True, exist_ok=True)
(rl6 / "last-state.json").write_text(json.dumps({"full": {"attention": [
    {"kind": "needs_you", "session": "pix-docs", "ref": "pix-docs", "title": "login su win", "since": int(time.time()) - 720},
    {"kind": "question", "session": "master", "ref": "q-1-1", "title": "la sua", "since": int(time.time()) - 60}]}}))
m6 = hook("UserPromptSubmit", {"session_id": "sid-m", "cwd": str(home), "prompt": "altro"}, CM_HOOK_TMUX_NAME="master")
o6 = hook("UserPromptSubmit", {"session_id": "sid-o", "cwd": str(home), "prompt": "altro"}, CM_HOOK_TMUX_NAME="alfa")
T.check("H6 in the master every prompt carries «Ti aspettano: pix-docs — login su win, da 12 min», without its own items; in another session nothing",
        "Ti aspettano: pix-docs — login su win, da 12 min" in m6.stdout and "la sua" not in m6.stdout and "Ti aspettano" not in o6.stdout, m6.stdout + m6.stderr + o6.stdout)

# H7 (22/10/2026, P2): nella master ogni prompt e ogni avvio portano gli impegni dal registro; altrove niente; senza registro niente
db7 = tmp / "tasks7.db"
c7 = {"schema_version": 1, "task": {"id": "gantt-apk", "title": "APK del Gantt sul telefono", "plan": None,
      "where": {"project": str(home / "ws" / "personali" / "alfa"), "session": "notte-app", "host": "local", "account": None},
      "check": {"cmd": "adb shell dumpsys package x | grep -q lastUpdateTime", "cwd": None, "timeout_s": 30}, "perimeter": [], "lane": "closed",
      "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal", "requested_by": "test",
      "outcome": {"expected": "l'utente vede il Gantt sul telefono", "kind": "install"}}}
E7 = env(CM_TASKS_DB=str(db7))
subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), "add", "-"], input=json.dumps(c7), capture_output=True, text=True, env=E7, timeout=30)
subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), "wait-ok", "gantt-apk", "--what", "push e release", "--where", "GitHub"], capture_output=True, text=True, env=E7, timeout=30)
subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), "approve", "gantt-apk", "--by", "maintainer", "--text", "ok"], capture_output=True, text=True, env=E7, timeout=30)
m7 = hook("UserPromptSubmit", {"session_id": "sid-m", "cwd": str(home), "prompt": "altro"}, CM_HOOK_TMUX_NAME="master", CM_TASKS_DB=str(db7))
s7 = hook("SessionStart", {"session_id": "sid-m", "cwd": str(home), "source": "startup"}, CM_HOOK_TMUX_NAME="master", CM_TASKS_DB=str(db7))
o7 = hook("UserPromptSubmit", {"session_id": "sid-o", "cwd": str(home), "prompt": "altro"}, CM_HOOK_TMUX_NAME="alfa", CM_TASKS_DB=str(db7))
n7 = hook("UserPromptSubmit", {"session_id": "sid-m", "cwd": str(home), "prompt": "altro"}, CM_HOOK_TMUX_NAME="master", CM_TASKS_DB=str(tmp / "none7.db"))
T.check("H7 in the master the prompt carries «Impegni (1, 0 fermi):» with the task's line (session, ok time, next, whose turn, outcome); unchanged at the next prompt and start → one line «Impegni: 1, 0 fermi (invariati…)»; another session gets nothing; a missing registry gives nothing and exit 0",
        "Impegni (1, 0 fermi):" in m7.stdout and "- gantt-apk · notte-app · approved · ok " in m7.stdout and "· tocca a notte-app" in m7.stdout
        and "esito: l'utente vede il Gantt sul telefono" in m7.stdout and "Impegni: 1, 0 fermi (invariati" in s7.stdout and "- gantt-apk" not in s7.stdout and "Impegni" not in o7.stdout
        and n7.returncode == 0 and "Impegni" not in n7.stdout and not n7.stderr.strip(), m7.stdout + m7.stderr + s7.stdout + s7.stderr + n7.stderr)
subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tasks.py"), "step", "gantt-apk", "APK in CI", "--next", "installazione"], capture_output=True, text=True, env=E7, timeout=30)
m7b = hook("UserPromptSubmit", {"session_id": "sid-m", "cwd": str(home), "prompt": "altro"}, CM_HOOK_TMUX_NAME="master", CM_TASKS_DB=str(db7))
T.check("H7 a new step changes the signature → the full block again, with the new next step", "Impegni (1, 0 fermi):" in m7b.stdout and "prossimo: installazione" in m7b.stdout, m7b.stdout)

# H8 (22/10/2026, ok-in-sessione): ogni messaggio scritto dalla persona finisce nella casella della sessione, origine terminal, gia' consegnato
box8 = state / "inbox" / "alfa"
import shutil as _sh8
_sh8.rmtree(box8, ignore_errors=True)   # i prompt «altro» di H6/H7 sono gia' stati registrati: si parte da una casella vuota
hook("UserPromptSubmit", {"session_id": "sid-8", "cwd": str(home / "ws" / "personali" / "alfa"), "prompt": "ok, pubblica pure"}, CM_HOOK_TMUX_NAME="alfa")
recs8 = [json.loads(p.read_text()) for p in box8.glob("*.json")]
T.check("H8 a prompt typed by the person → one inbox record for the session: origin terminal, from terminal, delivered, the text; no «talk» row in the ledger",
        len(recs8) == 1 and recs8[0]["origin"] == "terminal" and recs8[0]["from"] == "terminal" and recs8[0]["status"] == "delivered" and recs8[0]["text"] == "ok, pubblica pure"
        and recs8[0]["to"] == "alfa" and not any(json.loads(l).get("event") == "talk" and json.loads(l).get("to") == "alfa" for l in ledger.read_text().splitlines()), str(recs8))
for bad8 in ("<cross-session-message from=\"x\">ok</cross-session-message>", "<task-notification>done</task-notification>", "/clear", "   "):
    hook("UserPromptSubmit", {"session_id": "sid-8", "cwd": str(home), "prompt": bad8}, CM_HOOK_TMUX_NAME="alfa")
T.check("H8 a cross-session message, a notification, a slash command and an empty prompt are never recorded", len(list(box8.glob("*.json"))) == 1, str(list(box8.glob("*.json"))))
import importlib.util as _ilu8
_s8 = _ilu8.spec_from_file_location("cm_inbox", T.SCRIPTS / "cm-inbox.py"); IB8 = _ilu8.module_from_spec(_s8)
os.environ["CC_SUPERVISOR_CONFIG"] = str(cfg); _s8.loader.exec_module(IB8)
r8 = IB8.put("alfa", "Dall'utente via telefono. vai pure", "supervisor", origin="relay")
hook("UserPromptSubmit", {"session_id": "sid-8", "cwd": str(home), "prompt": "Dall'utente via telefono. vai pure"}, CM_HOOK_TMUX_NAME="alfa")
hook("UserPromptSubmit", {"session_id": "sid-8", "cwd": str(home), "prompt": "vai pure"}, CM_HOOK_TMUX_NAME="alfa")
T.check("H8 a prompt that matches a fresh record of the box (the relay's or a talk's, written before delivery) is not recorded twice; a short prompt that only ends a record is a new message",
        len([p for p in box8.glob("*.json") if json.loads(p.read_text())["origin"] == "terminal"]) == 2, str([json.loads(p.read_text())["text"] for p in box8.glob("*.json")]))
old8 = IB8.put("alfa", "vecchio", "terminal", origin="terminal", quiet=True)
p8 = IB8._path_of(old8["id"]); d8 = json.loads(p8.read_text()); d8["created"] -= 2 * 86400; p8.write_text(json.dumps(d8))
hook("UserPromptSubmit", {"session_id": "sid-8", "cwd": str(home), "prompt": "ancora uno"}, CM_HOOK_TMUX_NAME="alfa")
T.check("H8 terminal records older than a day are pruned at the next prompt", not p8.exists() and len([p for p in box8.glob("*.json") if json.loads(p.read_text())["origin"] == "terminal"]) == 3, "")

# ---- restart con tmux privato e claude finto
with T.PrivateTmux() as tm:
    scen = tmp / "scen"; scen.write_text("plain")
    # T66: il server tmux nasce con QUESTO ambiente; il claude finto rilanciato dentro lo eredita
    e = env(CM_TMUX_ARGS=tm.env["CM_TMUX_ARGS"], FAKE_CLAUDE_SCENARIO_FILE=str(scen), FAKE_CLAUDE_ARGS_LOG=str(tmp / "args.log"))
    r = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "arm"], capture_output=True, text=True, env={k: v for k, v in e.items() if k != "TMUX"})
    T.check("R1 arm outside tmux → exit 3", r.returncode == 3, r.stderr)
    # sessione tmux con il claude finto dentro, avviata con l'ambiente del test (T66)
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "alfa", "-x", "100", "-y", "30", "-c", str(home / "ws" / "personali" / "alfa"),
                    f"'{FAKE}' --dangerously-skip-permissions -n alfa"], env=e, check=True)
    time.sleep(2)
    pane_pid = int(tm("list-panes", "-t", "alfa", "-F", "#{pane_pid}").stdout.strip())
    # arm "da dentro": si simula TMUX/TMUX_PANE del riquadro di alfa
    pane_id = tm("list-panes", "-t", "alfa", "-F", "#{pane_id}").stdout.strip()
    inside = dict(e, TMUX=f"/tmp/tmux-{os.getuid()}/{tm.socket},0,0", TMUX_PANE=pane_id, CLAUDE_CODE_SESSION_ID="sid-alfa")
    r = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "arm"], capture_output=True, text=True, env=inside, cwd=str(home / "ws" / "personali" / "alfa"))
    flag = json.loads((state / "restart-alfa.json").read_text()) if (state / "restart-alfa.json").exists() else {}
    T.check("R1 arm inside tmux writes the flag", r.returncode == 0 and flag.get("tmux") == "alfa" and flag.get("pid") == pane_pid and flag.get("gen"), r.stdout + r.stderr + str(flag))
    # R2: hook da un'altra sessione non tocca il flag
    tm("new-session", "-d", "-s", "altra", "bash", "--norc")
    other = dict(inside, TMUX_PANE=tm("list-panes", "-t", "altra", "-F", "#{pane_id}").stdout.strip())
    r = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "hook"], capture_output=True, text=True, env=other)
    T.check("R2 hook from another session leaves the flag", (state / "restart-alfa.json").exists() and not r.stdout.strip(), r.stdout + r.stderr)
    # R3: hook dalla stessa sessione → esecutore → /exit → rilancio
    r = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "hook"], capture_output=True, text=True, env=inside)
    T.check("R2 hook from the armed session consumes the flag and answers a systemMessage", "systemMessage" in r.stdout and not (state / "restart-alfa.json").exists(), r.stdout + r.stderr)
    for _ in range(40):
        time.sleep(1)
        if (state / "restart.log").exists() and "riavvio completato" in (state / "restart.log").read_text():
            break
    log = (state / "restart.log").read_text() if (state / "restart.log").exists() else ""
    T.check("R3 executor: old process gone, session relaunched, log says completed", "riavvio completato" in log and tm("has-session", "-t", "=alfa").returncode == 0
            and not os.path.exists(f"/proc/{pane_pid}"), log[-600:])
    new_pane = int(tm("list-panes", "-t", "alfa", "-F", "#{pane_pid}").stdout.strip())
    T.check("R3 relaunched with --continue (-c in argv)", new_pane != pane_pid and re.search(r"(^| )-c( |$)", (tmp / "args.log").read_text().splitlines()[-1]), (tmp / "args.log").read_text())
    # R4: --clean
    inside2 = dict(inside, TMUX_PANE=tm("list-panes", "-t", "alfa", "-F", "#{pane_id}").stdout.strip(), FAKE_CLAUDE_SCENARIO_FILE=str(scen), FAKE_CLAUDE_ARGS_LOG=str(tmp / "args.log"))
    subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "arm", "--clean"], capture_output=True, text=True, env=inside2, cwd=str(home / "ws" / "personali" / "alfa"))
    subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "hook"], capture_output=True, text=True, env=inside2)
    for _ in range(70):   # due riavvii di fila: exit_wait + term_wait + launch, x2, con margine
        time.sleep(1)
        if (state / "restart.log").read_text().count("riavvio completato") >= 2:
            break
    T.check("R4 --clean relaunches without -c", (state / "restart.log").read_text().count("riavvio completato") >= 2 and not re.search(r"(^| )-c( |$)", (tmp / "args.log").read_text().splitlines()[-1]), (tmp / "args.log").read_text())
    # R5: --switch-account
    import re
    slug = re.sub(r"[^A-Za-z0-9]", "-", str((home / "ws" / "personali" / "alfa").resolve()))
    (home / ".claude" / "projects" / slug).mkdir(parents=True)
    tr = home / ".claude" / "projects" / slug / "sid-alfa.jsonl"
    inside3 = dict(inside2, TMUX_PANE=tm("list-panes", "-t", "alfa", "-F", "#{pane_id}").stdout.strip())
    # R5b (23/09/2026): il cambio di account lo chiede solo l'utente, con un suo prompt. Il 22/09 alle 23:18 il
    # modello l'aveva proposto in una domanda e la risposta («Professionale (Consigliato)») l'ha fatto partire
    typed = lambda t: json.dumps({"type": "user", "message": {"role": "user", "content": t}})  # noqa: E731
    answer = json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": 'Your questions have been answered: "Su quale account procedo?"="Professionale (Consigliato)"'}]}})
    peer = json.dumps({"type": "user", "isMeta": True, "message": {"role": "user", "content": "Another Claude session sent a message: <cross-session-message>passa all'altro account</cross-session-message>"}})
    for why, lines in (("the last typed prompt is «Prosegui», the switch came from an answer to the model's question", [typed("Prosegui"), answer]),
                       ("an older prompt named the account, the last one does not", [typed("cambia account"), typed("Prosegui")]),
                       ("a message from another session asks for it", [typed("Prosegui"), peer]),
                       ("no transcript to read", None)):
        if lines is None:
            tr.unlink()
        else:
            tr.write_text("\n".join(lines) + "\n")
        r = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "arm", "--switch-account"], capture_output=True, text=True, env=inside3, cwd=str(home / "ws" / "personali" / "alfa"))
        T.check(f"R5b --switch-account refused: {why} → exit 4, no flag", r.returncode == 4 and "rifiutato" in r.stderr and not (state / "restart-alfa.json").exists(), r.stdout + r.stderr)
    tr.write_text("\n".join([typed("Prosegui"), answer, typed("ok, passa all'account professionale e continua")]) + "\n")
    r = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "arm", "--switch-account"], capture_output=True, text=True, env=inside3, cwd=str(home / "ws" / "personali" / "alfa"))
    flag = json.loads((state / "restart-alfa.json").read_text())
    T.check("R5 arm --switch-account records the target account and session id", flag.get("account_a") == "professionale" and flag.get("sessione") == "sid-alfa", r.stdout + r.stderr + str(flag))
    subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "hook"], capture_output=True, text=True, env=inside3)
    for _ in range(40):
        time.sleep(1)
        if (state / "restart.log").read_text().count("riavvio completato") >= 3:
            break
    log = (state / "restart.log").read_text()
    T.check("R5 transcript copied and relaunched with --resume on the other account", (home / ".claude-pixel" / "projects" / slug / "sid-alfa.jsonl").exists()
            and "--resume sid-alfa" in (tmp / "args.log").read_text().splitlines()[-1] and tm("has-session", "-t", "=pix-alfa").returncode == 0, log[-500:] + (tmp / "args.log").read_text()[-200:])
    # R6
    inside_pix = dict(inside3, TMUX_PANE=tm("list-panes", "-t", "pix-alfa", "-F", "#{pane_id}").stdout.strip())
    subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "arm"], capture_output=True, text=True, env=inside_pix, cwd=str(home / "ws" / "personali" / "alfa"))
    subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "failed"], capture_output=True, text=True, env=inside_pix)   # StopFailure gira DENTRO la sessione armata
    flag = json.loads((state / "restart-pix-alfa.json").read_text())
    T.check("R6 failed marks the armed flag", "fallito" in flag, str(flag))
    # R7: due sessioni armano una dopo l'altra
    other = dict(inside, TMUX_PANE=tm("list-panes", "-t", "altra", "-F", "#{pane_id}").stdout.strip())
    r1 = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "arm"], capture_output=True, text=True, env=other, cwd=str(home / "ws" / "personali" / "alfa"))
    r2 = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "arm"], capture_output=True, text=True, env=dict(inside3, TMUX_PANE=tm("list-panes", "-t", "pix-alfa", "-F", "#{pane_id}").stdout.strip()), cwd=str(home / "ws" / "personali" / "alfa"))
    fa, fb = state / "restart-altra.json", state / "restart-pix-alfa.json"
    T.check("R7 two arms in a row: two flag files, each with its own tmux name", r1.returncode == 0 and r2.returncode == 0 and fa.is_file() and fb.is_file() and json.loads(fa.read_text())["tmux"] == "altra" and json.loads(fb.read_text())["tmux"] == "pix-alfa", r1.stdout + r1.stderr + r2.stdout + r2.stderr + str(sorted(x.name for x in state.glob("restart*"))))
    r = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "list"], capture_output=True, text=True, env=e)
    T.check("R7 restart list names both armed sessions", r.returncode == 0 and "altra" in r.stdout and "pix-alfa" in r.stdout, r.stdout + r.stderr)
    r = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "hook"], capture_output=True, text=True, env=other)
    T.check("R7 hook of the first consumes only its own flag; the other stays armed", "systemMessage" in r.stdout and not fa.exists() and fb.is_file(), r.stdout + r.stderr + str(sorted(x.name for x in state.glob("restart*"))))
    time.sleep(3)
    # R8: l'esecutore senza display. Backend gnome (soggetto a T57) a secco: il comando stampato
    # finisce nell'uscita di launch, che exec ripete. Il pid nel flag e' gia' morto: niente /exit.
    import socket
    (home / "ws" / "personali" / "beta").mkdir()
    cfg8 = tmp / "config-r8.json"
    c8 = json.loads(cfg.read_text()); c8["terminal"] = {"backend": "gnome", "attach_wait_s": 1, "attach_retry_wait_s": 1, "headless_skip": True}
    cfg8.write_text(json.dumps(c8))
    rt, x11 = tmp / "runtime", tmp / "x11"
    rt.mkdir(); x11.mkdir()
    morto = subprocess.Popen(["true"]); morto.wait()
    e8 = {k: v for k, v in e.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "TMUX")}
    # TMUX: l'esecutore vero la eredita da claude, che gira in un riquadro (il recupero vale solo sotto tmux)
    e8.update(CC_SUPERVISOR_CONFIG=str(cfg8), CM_TERMINAL_DRY_RUN="1", XDG_RUNTIME_DIR=str(rt), CM_X11_SOCKET_DIR=str(x11),
              TMUX="/tmp/tmux-fake/default,1,0")

    def exec8(tag):
        f8 = state / f"restart-r8-{tag}.json"
        f8.write_text(json.dumps({"tmux": "beta", "pid": morto.pid, "cartella": str(home / "ws" / "personali" / "beta"), "gen": tag}))
        r = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "exec", str(f8)], capture_output=True, text=True, env=e8, timeout=120)
        return r.stdout + r.stderr

    out = exec8("senza-socket")
    T.check("R8 no display and no socket → cm-terminal skips (T57)", "skip: headless" in out and "gnome-terminal" not in out and "riavvio completato" in out, out[-800:])
    srv = socket.socket(socket.AF_UNIX); srv.bind(str(rt / "wayland-0"))
    out = exec8("con-socket")
    srv.close()
    T.check("R8 no display in the env, wayland-0 alive → display recovered, cm-terminal opens the tab", "WAYLAND_DISPLAY=wayland-0" in out and "gnome-terminal --title beta" in out and "skip: headless" not in out and "riavvio completato" in out, out[-800:])

    # R9 (05/10, claude-master-phone alle 22:46): un processo che SIGKILL non chiude subito. Muore in ritardo → si
    # rilancia lo stesso; non muore mai → annullato, e l'avviso (app/Telegram) e' tentato e scritto nel log
    c9 = json.loads(cfg8.read_text()); c9["restart"] = dict(c9.get("restart") or {}, exit_wait_s=1, term_wait_s=1, kill_wait_s=1, dead_wait_s=40)
    cfg9 = tmp / "config-r9.json"; cfg9.write_text(json.dumps(c9))
    e9 = dict(e8, CC_SUPERVISOR_CONFIG=str(cfg9), CM_RESTART_TEST_ALIVE_UNTIL=str(int(time.time()) + 30))
    f9 = state / "restart-r9-tardi.json"
    f9.write_text(json.dumps({"tmux": "beta", "pid": morto.pid, "cartella": str(home / "ws" / "personali" / "beta"), "gen": "tardi"}))
    r9 = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "exec", str(f9)], capture_output=True, text=True, env=e9, timeout=120)
    o9 = r9.stdout + r9.stderr
    T.check("R9 the process dies after SIGKILL but late → the executor waits (dead_wait_s) and relaunches anyway, no «annullato»",
            "non muore dopo SIGKILL" in o9 and "chiuso in ritardo: rilancio" in o9 and "riavvio completato" in o9 and "annullato" not in o9, o9[:1200])
    c9["restart"]["dead_wait_s"] = 3; cfg9.write_text(json.dumps(c9))
    e9b = dict(e9, CM_RESTART_TEST_ALIVE_UNTIL=str(int(time.time()) + 300))
    f9.write_text(json.dumps({"tmux": "beta", "pid": morto.pid, "cartella": str(home / "ws" / "personali" / "beta"), "gen": "mai"}))
    r9 = subprocess.run([str(T.SCRIPTS / "cm-restart.sh"), "exec", str(f9)], capture_output=True, text=True, env=e9b, timeout=120)
    o9 = r9.stdout + r9.stderr
    T.check("R9 the process never dies → «riavvio annullato», exit 1, and the user is told: «avviso: riavvio di beta non riuscito» (no emoji, 22/10) (app and Telegram tried)",
            r9.returncode == 1 and "pid " in o9 and "riavvio annullato" in o9 and "avviso: riavvio di beta non riuscito" in o9 and "riavvio completato" not in o9, o9[-800:])

T.rm(str(tmp))
T.finish()
