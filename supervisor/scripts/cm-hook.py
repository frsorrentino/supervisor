#!/usr/bin/env python3
"""Hook di sessione di supervisor: un solo script, un evento per invocazione.

    cm-hook.py SessionStart | SessionEnd | UserPromptSubmit | Stop | StopFailure | PermissionRequest
               | Elicitation | ElicitationResult

Legge il payload JSON dell'hook da stdin (session_id, cwd, transcript_path, ...)
e fa POCO, in fretta: ogni evento e' un processo in piu' per ogni turno di ogni
sessione. Tutto cio' che scrive sta in `state_dir`:

- registry (1.3): SessionStart/SessionEnd aggiornano il registro delle sessioni
  subito (il cron resta come riconciliazione). SessionEnd NON scatta al riavvio
  della macchina: le voci restano, ed e' esattamente cio' che serve a `restore`.
- waiting/<session_id> (1.8): PermissionRequest lo scrive con il nome del tool
  (AskUserQuestion, un permesso...), UserPromptSubmit lo cancella. `sessions` lo
  legge dopo lo `status: waiting` del registro peer.
- ledger.jsonl (N7): una riga per evento — cosa e' successo, per il diario.
- kernel (D6): SessionStart stampa `kernel_<lingua>.md` (regole di comportamento,
  ~250 token) a startup/resume/clear/compact: dopo una compattazione il testo
  iniettato puo' non esserci piu'.
- ora locale (T46): UserPromptSubmit stampa `[prefisso] <giorno data ora>`.
- Stop: se un riavvio e' armato lo passa a `cm-restart.sh hook`; se la coda della
  sessione (queue/<session_id>) ha voci e `stop_hook_active` e' falso, ne consegna
  UNA con {"decision":"block","reason":...} (E5) — con tetto per turno e scadenza,
  mai un loop.
- StopFailure: ledger + il riavvio armato resta visibile come fallito.
- Elicitation (24/10/2026, contratto 1.83): la domanda di un server MCP va al telefono (cm-elicit.py) e l'hook aspetta
  la risposta fino a `hooks.elicitation.wait_s`; senza telefono, spenta o senza risposta → niente in uscita e il
  dialogo nel terminale. ElicitationResult (risposta data al terminale) toglie la scheda.

Windows nativo (test del 27/09/2026): niente tmux, niente /proc, i .sh non si eseguono da Python. Ogni evento esce
subito con 0 PRIMA di caricare la config; solo il primo SessionStart su quella macchina dice, in una riga, che serve
WSL2. Prima il kernel veniva iniettato (inutilizzabile) e lo Stop dava un traceback a ogni turno.
"""
import os
import sys


def windows_notice(argv):
    """Il primo SessionStart stampa una riga (va nel contesto: il modello la riferisce), poi silenzio. Solo os.path:
    gira prima di ogni altro import."""
    if (argv[:1] or [""])[0] != "SessionStart":
        return 0
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    mark = os.path.join(base, "cc-supervisor", "windows-notice-shown")
    if os.path.exists(mark):
        return 0
    try:
        os.makedirs(os.path.dirname(mark), exist_ok=True)
        open(mark, "w", encoding="utf-8").close()
    except OSError:
        pass
    sys.stdout.write("supervisor: needs Linux or macOS with tmux; on Windows use it inside WSL2. Its hooks are off "
                     "on native Windows (`claude plugin disable supervisor` removes it).\n")
    return 0


if os.name == "nt" and __name__ == "__main__":
    sys.exit(windows_notice(sys.argv[1:]))

import datetime
import importlib.util
import json
import re
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731   (1.68: la riga «Ti aspettano» nella master)
STATE = Path(cm.expand(CFG["state_dir"]))

DAYS = {"it": ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"],
        "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}


def payload():
    try:
        return json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (ValueError, OSError):
        return {}


def ledger(event, p, **extra):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        row = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "event": event,
               "session_id": p.get("session_id", ""), "cwd": p.get("cwd", ""),
               "account": account_name(), "pid": os.getppid()}
        row.update(extra)
        with open(STATE / "ledger.jsonl", "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def account_name():
    conf = os.path.realpath(os.environ.get("CLAUDE_CONFIG_DIR") or cm.expand("~/.claude"))
    for name, a in CFG["accounts"].items():
        if os.path.realpath(cm.expand(a["config_dir"])) == conf:
            return name
    return CFG["default_account"]


def registry_update(closed=""):
    """`closed` = nome tmux di una sessione chiusa APPOSTA (/exit): esce dalla fotografia «ultimo
    insieme buono»; una finestra chiusa a mano o un crash non la toccano (11/09/2026)."""
    try:
        subprocess.Popen([str(HERE / "cm-registry.sh")] + (["--closed", closed] if closed else []),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)   # staccato: l'hook non aspetta
    except OSError:
        pass


def my_tmux_name():
    if os.environ.get("CM_HOOK_TMUX_NAME"):   # detach.sh read it before letting go: after /exit the pane may be gone
        return os.environ["CM_HOOK_TMUX_NAME"]
    pane = os.environ.get("TMUX_PANE", "")
    if not pane:
        return ""
    try:
        r = subprocess.run(["tmux"] + os.environ.get("CM_TMUX_ARGS", "").split() + ["display-message", "-p", "-t", pane, "#{session_name}"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def ask_notify(p):
    """L'avviso sul telefono (idea 1, 11/09): `cm-answer.py --notify` staccato con il payload su stdin;
    aspetta lui che il dialogo sia sullo schermo, l'hook torna subito."""
    if not (CFG["hooks"].get("ask_notify") or {}).get("enabled"):
        return
    try:
        pr = subprocess.Popen([sys.executable, str(HERE / "cm-answer.py"), "--notify"], stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        pr.stdin.write(json.dumps(p).encode())
        pr.stdin.close()
    except (OSError, ValueError):
        pass


def local_time():
    h = CFG["hooks"]["local_time"]
    now = datetime.datetime.now()
    day = DAYS.get(CFG["language"], DAYS["en"])[now.weekday()]
    return f"{h['prefix']} " + now.strftime(h["format"]).replace(now.strftime("%A"), day)


def recent_recap(p):
    """Le ultime righe del recap del progetto (`<cwd>/<recap.project_log>`, scritto ogni sera da
    `supervisor recap`): la sessione nasce sapendo cosa è successo nei giorni scorsi, ~100 token
    invece di rileggere i transcript. Niente file, niente riga."""
    rc = CFG.get("recap") or {}
    rel = str(rc.get("project_log") or "").strip()
    n = int(rc.get("startup_lines") or 0)
    cwd = p.get("cwd") or ""
    if not rel or n <= 0 or not cwd or p.get("source", "startup") not in ("startup", "resume", "clear", "compact", ""):
        return ""
    f = Path(cwd) / rel
    try:
        rows = [l for l in f.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
    except OSError:
        return ""
    if not rows:
        return ""
    head = "RECAP RECENTE" if CFG.get("language") == "it" else "RECENT RECAP"
    return f"{head} ({rel}):\n" + "\n".join(rows[-n:])


def kernel_text(p):
    if not CFG["hooks"]["session_kernel"]["enabled"]:
        return ""
    src = p.get("source", "startup")
    if src not in ("startup", "resume", "clear", "compact", "fork", ""):
        return ""
    f = HERE.parent / f"kernel_{CFG['language']}.md"
    if not f.is_file():
        f = HERE.parent / "kernel_en.md"
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def pop_queue(p):
    """Coda 2.5: una voce per Stop, mai con stop_hook_active (il turno viene gia' da un block)."""
    q = STATE / "queue" / p.get("session_id", "-")
    if p.get("stop_hook_active") or not q.is_file():
        return None
    try:
        items = [json.loads(l) for l in q.read_text().splitlines() if l.strip()]
    except ValueError:
        return None
    now = time.time()
    items = [i for i in items if not i.get("expires") or i["expires"] > now]
    if not items:
        q.unlink(missing_ok=True)
        return None
    # 1.63: una voce con `at` aspetta la sua ora; si consegna la prima gia' dovuta
    item = next((i for i in items if not i.get("at") or i["at"] <= now), None)
    if item is None:
        q.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n")
        return None
    rest = [i for i in items if i is not item]
    if rest:
        q.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in rest) + "\n")
    else:
        q.unlink(missing_ok=True)
    return item


def waiting_for_you(now=None):
    """1.68 (14/10): solo nella master, a ogni prompt, una riga con chi aspetta la persona — «Ti aspettano: <sessione> —
    <motivo>, da N min» — dalla lista «Serve te» dell'ultimo stato del relay, cosi' la master la riporta anche quando si
    parla d'altro. Niente se la lista e' vuota o se questa non e' la master."""
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    if my_tmux_name() != master:
        return ""
    R = CFG.get("relay") or {}
    try:
        last = json.loads((Path(cm.expand(R.get("dir") or "~/.cc-supervisor/relay")) / "last-state.json").read_text())
    except (OSError, ValueError):
        return ""
    items = [x for x in ((last.get("full") or last.get("state") or {}).get("attention") or []) if x.get("session") != master]
    if not items:
        return ""
    now = now or time.time()
    parts = [M("hook.waiting_item", name=x.get("session") or "?", what=str(x.get("title") or x.get("kind") or "")[:90],
               min=max(0, int((now - float(x.get("since") or now)) // 60))) for x in items[:5]]
    more = M("hook.waiting_more", n=len(items) - 5) if len(items) > 5 else ""
    return M("hook.waiting_for_you", items="; ".join(parts) + more)


PROMPT_SKIP = ("<cross-session-message", "<task-notification", "[Cross-session", "<system-reminder", "[supervisor queue]", "<local-command", "/")
PROMPT_RECENT_S = 600
PROMPT_KEEP_S = 86400
PROMPT_OWN_ORIGINS = ("terminal", "cron", "unverified")   # i record che scrive questo hook
PRUNE_EVERY_S = 3600


def record_person_prompt(p):
    """22/10/2026 (ok-in-sessione, the maintainer from the web at 13:34: «l'ok nella sessione deve essere valido come gli
    altri»): ogni messaggio scritto dalla persona in questa sessione va nella sua casella con origine «terminal», gia'
    consegnato, cosi' `task approve --inbox` lo puo' citare. L'origine (23/10) la dice la riga `user` della trascrizione,
    che Claude Code scrive dopo gli hook: il record nasce «unverified» e lo risolve ib.resolve piu' tardi. Mai i testi delle altre sessioni (cross-session, notifiche, comandi) ne' un prompt che coincide con un record
    recente scritto da altri (il relay e `talk` scrivono il record prima di consegnare: quello non e' la persona); i
    record di questo hook non contano (23/10, 11:15: «ok a tutti e tre» due volte in tre minuti, il secondo perso).
    Costo (23/10, 10:47: hook annullato a 5 s, prompt perso): stat dei file, lettura solo dei recenti, potatura una
    volta l'ora."""
    text = str(p.get("prompt") or "").strip()
    if not text or text.startswith(PROMPT_SKIP):
        return
    name = my_tmux_name()
    if not name:
        return
    try:
        ib = inbox()
        now = time.time()
        mine = " ".join(ib.mask_pin(text).split())   # i record sono mascherati («ok ••••»)
        d = ib.box(name)
        stale = []   # i prompt precedenti ancora «unverified»: la loro riga user ormai c'e'
        for f in d.glob("*.json"):
            try:
                if now - f.stat().st_mtime > PROMPT_RECENT_S:
                    continue
                r = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if r.get("origin") in PROMPT_OWN_ORIGINS:
                if r.get("origin") == "unverified" and r.get("transcript"):
                    stale.append(r)
                continue
            other = " ".join(str(r.get("text") or "").split())
            if other == mine or (len(mine) >= 20 and other.endswith(mine)):
                return
        # 23/10 16:22 (regressione della 0.8.46): Claude Code 2.1.308 scrive la riga `user` della trascrizione DOPO gli
        # hook, quindi qui l'origine non si sa: il record nasce «unverified» con la via della trascrizione e lo risolve
        # ib.resolve (al prompt seguente, allo Stop, nell'elenco e dentro task approve --inbox)
        rec = ib.put(name, text[:4000], "terminal", p.get("cwd") or "", origin="unverified", quiet=True, transcript=p.get("transcript_path") or None)
        ib.mark(rec["id"], "delivered", "terminal")
        for r in stale:
            ib.resolve(r)
        stamp = d / ".pruned"
        try:
            fresh = now - stamp.stat().st_mtime < PRUNE_EVERY_S
        except OSError:
            fresh = False
        if not fresh:
            ib.prune(name, {o: PROMPT_KEEP_S for o in PROMPT_OWN_ORIGINS})   # il resto 7 giorni (ib.KEEP_S)
            stamp.touch()
    except Exception:   # noqa: BLE001 — un'informazione in piu': mai far cadere il prompt
        pass


def commitments_block():
    """P2 (22/10/2026): solo nella master, a ogni prompt e a ogni avvio, gli impegni dal registro — gli approvati non
    compiuti con eta', fermo da, prossimo passo, chi aspetta chi, pausa — letti da sqlite (niente git, niente figli:
    l'hook ha 5 s). Niente se la lista e' vuota, se il registro non c'e' o se tasks.commitments_prompt e' falso."""
    T_ = CFG.get("tasks") or {}
    if not T_.get("commitments_prompt", True):
        return ""
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    if my_tmux_name() != master:
        return ""
    try:
        spec = importlib.util.spec_from_file_location("cm_tasks", HERE / "cm-tasks.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        con = mod.connect(create=False)
        if con is None:
            return ""
        try:
            items = mod.commitments(con, git=False, stall_s=float(T_.get("stall_hours", 2) or 2) * 3600, person=M("tasks.person"))
        finally:
            con.close()
        if not items:
            return ""
        # 22/10 00:35 (la master): il blocco intero solo quando cambia (firma senza le durate), altrimenti una riga
        sig = json.dumps(mod.signature(items), ensure_ascii=False)
        f = STATE / f"commitments-{master}.json"
        try:
            same = f.read_text() == sig
        except OSError:
            same = False
        if same:
            return M("tasks.unchanged", n=len(items), stalled=sum(1 for x in items if x["stalled"]))   # mai `k=`: M e' lambda k
        try:
            STATE.mkdir(parents=True, exist_ok=True)
            f.write_text(sig)
        except OSError:
            pass
        return mod.render_commitments(items, brief=True, max_lines=int(T_.get("commitments_max_lines", 8) or 8), person=M("tasks.person"))
    except Exception:   # noqa: BLE001 — un'informazione in piu': mai far cadere il prompt
        return ""


def task_check_on_stop():
    """1.57 (14/10): se questa sessione ha un task running, il relay riesegue il suo controllo e dice al telefono verde
    o rosso. Qui solo una lettura del registro (sqlite, millisecondi) e un figlio staccato: l'hook non aspetta."""
    R = CFG.get("relay") or {}
    if not R.get("enabled"):
        return
    name = my_tmux_name()
    if not name:
        return
    try:
        import sqlite3
        spec = importlib.util.spec_from_file_location("cm_tasks", HERE / "cm-tasks.py")
        db = Path(os.environ.get("CM_TASKS_DB") or "")
        if not db.name:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            db = mod.db_path()
        if not db.exists():
            return
        con = sqlite3.connect(str(db), timeout=2)
        hit = con.execute("SELECT 1 FROM tasks WHERE state='running' AND session=? LIMIT 1", (name,)).fetchone()
        con.close()
    except Exception:   # noqa: BLE001 — un extra: mai far fallire lo Stop
        return
    if hit:
        subprocess.Popen([sys.executable, str(HERE / "cm-relay.py"), "task-check", name], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def relay_push():
    """L'orologio (0.4.0): chiede una push al relay, solo con relay.enabled; torna subito (debounce nel relay).
    08/10 (piano prestazioni, fase 1): con il demone vivo basta toccare <relay.dir>/push-req/hook, il suo worker
    la fa; solo senza demone parte `cm-relay.py push --async` (il percorso di prima)."""
    R = CFG.get("relay") or {}
    if not R.get("enabled"):
        return
    d = Path(cm.expand(R.get("dir") or "~/.cc-supervisor/relay"))
    try:
        (d / "push-req").mkdir(parents=True, exist_ok=True)
        (d / "push-req" / "hook").touch()
        pid = int((d / "serve.pid").read_text().strip() or 0)
        if pid > 0:
            os.kill(pid, 0)
            return
    except (OSError, ValueError):
        pass
    try:
        subprocess.Popen([sys.executable, str(HERE / "cm-relay.py"), "push", "--async", "--origin", "hook"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def waiting_summary(p):
    """Cosa si aspetta: il tool e un input ridotto (comando, file, domanda), per il tier e la scheda del polso."""
    ti = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else {}
    keep = {}
    for k in ("command", "file_path", "pattern", "url", "description"):
        if ti.get(k):
            keep[k] = str(ti[k])[:300]
    qs = ti.get("questions")
    if isinstance(qs, list) and qs and isinstance(qs[0], dict):
        keep["question"] = str(qs[0].get("question") or "")[:600]
        keep["header"] = str(qs[0].get("header") or "")[:60]
        # le etichette delle opzioni: il relay le usa quando lo schermo non e' ancora disegnato (16:15 del 12/09:
        # la push partiva 3 s dopo l'hook, `answer --show` non trovava il dialogo e il polso riceveva il JSON grezzo)
        keep["options"] = [str(o.get("label") or "")[:80] for o in (qs[0].get("options") or []) if isinstance(o, dict)][:8]
        # 1.53 (14/10): la riga che spiega ogni opzione, nello stesso ordine (sotto il tasto, sul telefono)
        keep["descriptions"] = [str(o.get("description") or "")[:200] for o in (qs[0].get("options") or []) if isinstance(o, dict)][:8]
    return {"tool": p.get("tool_name", "?"), "input": keep}


def inbox():
    spec = importlib.util.spec_from_file_location("cm_inbox", HERE / "cm-inbox.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def elicit_mod():
    spec = importlib.util.spec_from_file_location("cm_elicit", HERE / "cm-elicit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    ev = argv[0] if argv else ""
    p = payload()
    sid = p.get("session_id", "")
    if ev == "SessionStart":
        registry_update()
        (STATE / "waiting" / sid).unlink(missing_ok=True) if sid else None
        ledger("start", p, source=p.get("source", ""))
        k = kernel_text(p)
        if k:
            sys.stdout.write(k + "\n")
        r = recent_recap(p)
        if r:
            sys.stdout.write(r + "\n")
        c = commitments_block()   # P2: a ogni giro di regia, dal registro
        if c:
            sys.stdout.write(c + "\n")
        if p.get("source", "startup") in ("clear", "startup", ""):
            try:   # 13/10: l'handoff di /supervisor:handoff scritto prima di /clear, una volta sola
                spec = importlib.util.spec_from_file_location("cm_handoff", HERE / "cm-handoff.py")
                ho = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(ho)
                line = ho.announce(p.get("cwd") or os.getcwd())
                if line:
                    sys.stdout.write(line + "\n")
            except Exception:   # noqa: BLE001 — un'informazione in piu': mai far cadere l'avvio
                pass
        try:   # 23/09: i messaggi arrivati mentre la sessione era chiusa (casella persistente)
            box = inbox().deliver_block(my_tmux_name(), p.get("cwd") or os.getcwd(), "session-start")
            if box:
                sys.stdout.write(box + "\n")
        except Exception:   # noqa: BLE001 — un'informazione in piu': mai far cadere l'avvio
            pass
        relay_push()
    elif ev == "SessionEnd":
        explicit = p.get("reason", "") in ("prompt_input_exit", "logout")
        registry_update(closed=my_tmux_name() if explicit else "")
        ledger("end", p, reason=p.get("reason", ""))
        relay_push()
    elif ev == "UserPromptSubmit":
        if sid:
            (STATE / "waiting" / sid).unlink(missing_ok=True)
            (STATE / "needs-you" / f"{sid}.json").unlink(missing_ok=True)   # 1.68: il prompt dopo chiude «ferma per te»
        ledger("prompt", p)   # l'inizio del turno (turn_started per il polso)
        record_person_prompt(p)   # 22/10: l'ok scritto qui vale (casella della sessione, origine terminal)
        relay_push()   # 08/10 (fase 1): un prompt dalla tastiera arrivava alle app solo al giro del cron
        if CFG["hooks"]["local_time"]["enabled"]:
            sys.stdout.write(local_time() + "\n")
        line = waiting_for_you()
        if line:
            sys.stdout.write(line + "\n")
        c = commitments_block()   # P2 (22/10): gli impegni dal registro, non dai messaggi delle sessioni
        if c:
            sys.stdout.write(c + "\n")
    elif ev == "PermissionRequest":
        if sid:
            (STATE / "waiting").mkdir(parents=True, exist_ok=True)
            # JSON {tool, input} dal 0.4.0 (il relay ne ricava il tier); chi legge solo l'esistenza non cambia
            (STATE / "waiting" / sid).write_text(json.dumps(waiting_summary(p), ensure_ascii=False))
        ledger("waiting", p, tool=p.get("tool_name", ""))
        ask_notify(p)
        relay_push()
    elif ev == "Elicitation":
        el = elicit_mod()
        out, outcome = el.hook(p, STATE, CFG["hooks"].get("elicitation") or {}, CFG.get("relay") or {}, my_tmux_name(), relay_push)
        ledger("elicitation", p, server=str(p.get("mcp_server_name") or "")[:80], mode=p.get("mode") or "form", outcome=outcome)
        if out:
            sys.stdout.write(out + "\n")
    elif ev == "ElicitationResult":
        if sid and elicit_mod().clear_session(STATE, sid):
            relay_push()
    elif ev == "PostToolUse":
        # il tool e' partito: la domanda o il permesso hanno avuto risposta (tastiera, telefono o polso) → via il flag e
        # un push, cosi' l'orologio chiude la domanda in pochi secondi (14/09, dall'app: restava aperta fino a fine turno).
        # Parte a OGNI chiamata di strumento: senza flag non fa niente (niente push, niente avviso). Un'uscita prima di
        # caricare la config non valeva la pena: misurata a 0,09 s contro 0,09, il tempo e' l'avvio di python.
        flag = STATE / "waiting" / sid if sid else None
        if not flag or not flag.exists():
            return 0
        flag.unlink(missing_ok=True)
        relay_push()
    elif ev == "Stop":
        # a fine turno nessun dialogo e' aperto: il flag restava fino al prompt dopo e il relay teneva la sessione
        # «waiting» con la domanda gia' risposta (14/09: answered e outcome partiti 3 minuti tardi)
        if sid:
            (STATE / "waiting" / sid).unlink(missing_ok=True)
        try:   # 23/10 16:22: i prompt del turno, «unverified» all'hook, si risolvono ora dalla trascrizione
            tm_name = my_tmux_name()
            if tm_name:
                inbox().resolve_box(tm_name)
        except Exception:   # noqa: BLE001
            pass
        msg = p.get("last_assistant_message") or ""
        last = msg[:300]
        # per il polso (regola 9): l'ultima riga di testo e, se c'e', la riga «Esito:» — i primi 300
        # caratteri non la contengono mai, sta in coda al messaggio
        righe = [l.strip() for l in msg.splitlines() if l.strip() and not l.strip().startswith("```")]
        tail = "\n".join(righe)[-600:]   # la CODA del messaggio (piu' righe): l'ultima riga sola non basta al polso
        esito = next((l[:200] for l in reversed(righe) if l.lower().lstrip("*_#> ").startswith("esito")), "")
        # la riga «Watch:» (12/09/2026): l'esito nudo per lo smartwatch, chiesto dal prefisso del bot
        watch = next((l[:200] for l in reversed(righe) if l.lower().lstrip("*_#> ").startswith("watch")), "")
        ledger("stop", p, last=last, tail=tail, esito=esito, watch=watch)
        relay_push()
        task_check_on_stop()
        if CFG["hooks"]["restart_stop"]["enabled"]:
            r = subprocess.run([str(HERE / "cm-restart.sh"), "hook"], input=json.dumps(p), capture_output=True, text=True,
                               env={**os.environ, "CM_HOOK_SESSION_ID": sid})
            if r.stdout.strip():
                sys.stdout.write(r.stdout)
                return 0
        item = pop_queue(p)
        if item:
            ledger("queue-pop", p, text=item.get("text", "")[:120])
            sys.stdout.write(json.dumps({"decision": "block",
                                         "reason": f"[supervisor queue] {item.get('text', '')}"}, ensure_ascii=False) + "\n")
            return 0
        # 23/09: i messaggi arrivati mentre la sessione lavorava e non ha potuto riceverli (casella persistente)
        if not p.get("stop_hook_active"):
            try:
                box = inbox().deliver_block(my_tmux_name(), p.get("cwd") or os.getcwd(), "stop")
            except Exception:   # noqa: BLE001
                box = ""
            if box:
                sys.stdout.write(json.dumps({"decision": "block", "reason": box}, ensure_ascii=False) + "\n")
    elif ev == "StopFailure":
        # 22/10/2026 (0.8.41, nota sul changelog 2.1.306): in 65 stop-failure del ledger error_details era sempre vuoto e il
        # motivo vero stava in last_assistant_message («the API error string itself»), che nessuno leggeva
        details = " ".join(str(p.get("error_details") or p.get("last_assistant_message") or "").split())[:300]
        ledger("stop-failure", p, error=str(p.get("error", ""))[:200], details=details[:200])
        # 1.58 (14/10): un turno fermato dal limite della quota → il telefono propone le tre strade
        err = (str(p.get("error", "")) + (": " + details if details else "")).strip()
        name = my_tmux_name() if (CFG.get("relay") or {}).get("enabled") else ""
        # 1.85 (25/10/2026): il testo dell'errore e la trascrizione al relay. Il limite di un modello («You've reached your
        # Fable limit») sta nel messaggio <synthetic> della trascrizione: qui arriva solo il 429 generico
        # (l'ultimo messaggio dell'assistente conta solo con la frase del limite: puo' essere la risposta di prima)
        last = " ".join(str(p.get("last_assistant_message") or "").split())[:300]
        last = last if re.search(r"(reached|hit) your .{1,40} limit", last) else ""
        said = (err + (" " + last if last and last not in err else "")).strip()
        if name and re.search(r"rate.?limit|usage.?limit|limit reached|(reached|hit) your .{1,40} limit|quota", said, re.I):
            subprocess.Popen([sys.executable, str(HERE / "cm-relay.py"), "quota-limit", name, said[:500], str(p.get("transcript_path") or "")],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        elif name:
            # 1.61 (14/10): ogni altro turno fallito (529 overloaded, 500, accesso) al telefono, con il motivo e «continua»
            subprocess.Popen([sys.executable, str(HERE / "cm-relay.py"), "turn-failed", name, err[:500]], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        subprocess.run([str(HERE / "cm-restart.sh"), "failed"], input=json.dumps(p), capture_output=True, text=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
