#!/usr/bin/env python3
"""Il contesto che supervisor aggiunge a ogni prompt e all'avvio della master e' un indice corto (19/11/2026,
supervisor-contesto-compatto-19-11; idea 2 da claude-mem, progressive disclosure). Oggi «Impegni» ripeteva otto voci
lunghe a quasi ogni prompt (la firma cambia a ogni passo); i dettagli sono a un comando, `supervisor task impegni`.

CC1 la misura: con otto impegni come quelli veri, il blocco intero di prima e' oltre il tetto (lo stampa il test)
CC2 a ogni prompt della master: il contesto aggiunto sta sotto hooks.prompt_context_max_chars (600), con il conto degli
    impegni, gli id e il comando per i dettagli
CC3 le voci che aspettano la persona restano visibili, con il loro id, in una riga
CC4 all'avvio della master (SessionStart): lo stesso indice corto
CC5 niente si perde per chi chiede: `supervisor task impegni` da' ancora tutte le voci, intere
CC6 tasks.commitments_full true torna al blocco intero (chi lo vuole)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
proj = tmp / "proj"
proj.mkdir()
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
state = tmp / "state"
state.mkdir()
ws = tmp / "ws"
ws.mkdir()
cfg = tmp / "config.json"


def write_cfg(**tasks):
    cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                               "default_account": "personale", "workspace": {"root": str(ws), "root_session_name": "master"},
                               "relay": {"enabled": False, "dir": str(tmp / "relay")},
                               "hooks": {"local_time": {"enabled": False}, "session_kernel": {"enabled": False}},
                               "tasks": dict({"commitments_prompt": True}, **tasks)}))


write_cfg()
talk = tmp / "talk"
talk.write_text("#!/bin/sh\nexit 0\n")
talk.chmod(0o755)
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CM_HOOK_TMUX_NAME")}
ENV.update({"CM_TASKS_DB": str(tmp / "tasks.db"), "CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home),
            "CM_TASK_TALK_BIN": str(talk), "CM_PROC_SCAN_PIDS": ""})
CLI = str(T.SCRIPTS / "supervisor")


def cm(*args, stdin=None):
    p = subprocess.run([CLI, "task", *args], input=stdin, capture_output=True, text=True, env=ENV, timeout=60)
    return p.returncode, p.stdout, p.stderr


TASKS = [("supervisor-master-prova-vera-19-11", "win:supervisor", "Prova sul vero di ogni casistica d'avvio della master dopo le correzioni"),
         ("supervisor-chiudi-salva-19-11", "win:supervisor", "Prima di chiudere, la sessione scrive il passaggio di consegne se serve"),
         ("app-chiudi-salva-19-11", "win:supervisor-app", "Il modal di chiusura con Salva e chiudi e Chiudi senza salvare"),
         ("supervisor-privato-19-11", "win:supervisor", "Il testo fra private non esce mai dalla sessione"),
         ("app-bozze-link-19-11", "win:supervisor-app", "I link alle carte riaperte puntano alla carta attuale"),
         ("supervisor-riassunto-fine-19-11", "win:supervisor", "Il riassunto automatico a fine sessione dalla trascrizione"),
         ("supervisor-cerca-lavoro-19-11", "win:supervisor", "La ricerca in tre passi nel lavoro passato"),
         ("pix-sito-preventivi-19-11", "pix-sito", "Il modulo dei preventivi del sito con la conferma per email")]
for tid, sess, title in TASKS:
    c = {"schema_version": 1, "task": {"id": tid, "title": title, "plan": "Il piano: " + title.lower() + ", con test rosso prima e release.",
                                       "where": {"project": str(proj), "session": sess, "host": "local", "account": None},
                                       "check": {"cmd": "true", "cwd": None, "timeout_s": 30}, "perimeter": ["src/**"], "lane": "open",
                                       "depends_on": [], "route": "session", "hold": False, "attempts_max": 3, "data_class": "internal",
                                       "requested_by": "test", "outcome": {"expected": title + ", provato e rilasciato", "kind": "custom", "proof": "test"}}}
    cm("add", "-", stdin=json.dumps(c))
import importlib.util  # noqa: E402
os.environ.update(ENV)
_spec = importlib.util.spec_from_file_location("cm_tasks_cc", T.SCRIPTS / "cm-tasks.py")
TK = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TK)
_con = TK.connect()
for tid, _s, _t in TASKS:   # gli impegni sono i compiti approvati e non compiuti
    TK.approve(_con, tid, "maintainer", "ok", None, None, None)
_con.close()
cm("pause", "supervisor-master-prova-vera-19-11", "--reason", "serve il login della persona nella distro di prova", "--waits", "person")
cm("pause", "pix-sito-preventivi-19-11", "--reason", "prova del modulo sul telefono", "--waits", "person")


def hook(ev, extra=None):
    p = dict({"session_id": "S-M", "cwd": str(ws), "source": "startup"}, **(extra or {}))
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hook.py"), ev], input=json.dumps(p), capture_output=True, text=True,
                          env=dict(ENV, CM_HOOK_TMUX_NAME="master"), timeout=60)


# ------------------------------------------------------------------ CC1: la misura di prima (il blocco intero)
write_cfg(commitments_full=True)
(state / "commitments-master.json").unlink(missing_ok=True)
full = hook("UserPromptSubmit", {"prompt": "che si fa adesso?"}).stdout
print(f"  (misura: contesto aggiunto a un prompt della master, blocco intero: {len(full)} caratteri, {full.count(chr(10))} righe)")
T.check("CC1 the measure: with eight real-looking commitments the full block of before is over the cap", len(full) > 600, str(len(full)))
T.check("CC6 tasks.commitments_full true gives back the full block", all(t[0] in full for t in TASKS) and "prossimo:" in full, full[:300])

# ------------------------------------------------------------------ CC2, CC3
write_cfg()
(state / "commitments-master.json").unlink(missing_ok=True)
out = hook("UserPromptSubmit", {"prompt": "che si fa adesso?"}).stdout
print(f"  (misura: lo stesso prompt con l'indice: {len(out)} caratteri)\n" + "\n".join("    | " + l for l in out.splitlines()))
T.check("CC2 at each prompt of the master the added context stays under hooks.prompt_context_max_chars (600), with the count, the ids and the command for the details",
        0 < len(out) <= 600 and "8" in out and "supervisor task impegni" in out and "app-bozze-link-19-11" in out, out)
mine = [l for l in out.splitlines() if "supervisor-master-prova-vera-19-11" in l and "pix-sito-preventivi-19-11" in l]
T.check("CC3 the items waiting for the person stay visible with their ids, in one line", len(mine) == 1, out)

# ------------------------------------------------------------------ CC4
(state / "commitments-master.json").unlink(missing_ok=True)
st = hook("SessionStart").stdout
imp = [l for l in st.splitlines() if "supervisor task impegni" in l or "Impegni" in l]
T.check("CC4 at the master's start (SessionStart): the same short index", imp and sum(len(l) for l in imp) <= 600 and "prossimo:" not in st, "\n".join(imp))

# ------------------------------------------------------------------ CC5
rc, lst, err = cm("impegni")
T.check("CC5 nothing is lost for who asks: `supervisor task impegni` still gives every item, whole", rc == 0 and all(t[0] in lst for t in TASKS) and "prossimo:" in lst, lst[:300] + err)

T.finish()
