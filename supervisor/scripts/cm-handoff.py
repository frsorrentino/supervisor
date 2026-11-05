#!/usr/bin/env python3
"""supervisor handoff — il passaggio di consegne di una sessione su disco, per chi non ha fable-director.

  supervisor handoff --prepare [--here]   stampa PATH, SECTIONS e THEN: dove scrivere e con quali sezioni
  supervisor handoff --written PATH       registra il file scritto (lo annuncia il SessionStart dopo /clear) e vi
                                          aggiunge la sezione «Impegni dal registro» (P5, 22/10: mai scritta a mano)
  supervisor handoff --announce CWD       usato dal hook: la riga da mettere nel contesto, una volta sola
  supervisor handoff --snapshot CWD NAME  usato da `restart arm` nella master: la sezione impegni nel passaggio di
                                          consegne recente, o in handoffs/impegni-NAME-<data>-<ora>.md con il puntatore
  supervisor handoff --clear-when-idle NAME PATH   05/11: l'esecutore staccato del /clear dopo un passaggio di consegne
  supervisor handoff --autoclear-remote    05/11: dal giro di `hosts poll`: lo stesso per le sessioni di win

05/11/2026 (supervisor-clear-dopo-handoff-05-11, la persona: «alla fine dell'endoff in automatico effettua un clear»):
quando in un turno e' stato scritto un passaggio di consegne (`supervisor handoff --written PATH` o il --written di
fable-director, anche --here), a fine turno supervisor manda /clear alla sessione, come un prompt dell'app (digitato
nel riquadro, a casella vuota). Mai senza il file (o vuoto, o piu' vecchio del turno), con la sessione che aspetta una
risposta (in attesa, una richiesta needs-you, un «Tipo: aspetta»), con un riavvio armato o un prompt in coda.
L'annuncio dopo /clear non consuma piu' il puntatore alla prima lettura: torna a ogni /clear finche' la sessione che
l'ha avuto non scrive un prompt (05/11 15:13: un secondo /clear di fila trovava il puntatore gia' consumato).

Il tasto «Handoff» dell'app manda il comando che lo stato indica per la sessione (sessions[].handoff, contratto
1.51): «/fable-director:handoff --here» se la sessione ha fable-director, altrimenti «/supervisor:handoff --here»
(13/10, chiesto dal maintainer: senza fable-director il tasto mandava un comando che non esisteva). Stesso
distillato; dal 03/11 otto sezioni, l'ultima «Ripresa» (cosa e' fatto, cosa e' aperto, il prossimo passo esatto) e' quella
che il primo prompt dopo `restart` fa riassumere in chat prima di riprendere; niente controllo di budget, che e' di fable-director.

--here scrive in <cartella>/docs/handoff-AAAA-MM-GG.md (con -HHMM se c'e' gia'); senza, nella cartella di stato
(handoffs/<cartella>-AAAA-MM-GG-HHMM.md). Il puntatore all'ultimo sta in handoffs/last-<hash della cartella>.json e
vale ANNOUNCE_H ore: il SessionStart con source «clear» (o «startup») lo legge e lo dice una volta.
"""
import datetime
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load()
STATE = Path(cm.expand(CFG["state_dir"]))
ANNOUNCE_H = 6
SECTIONS = {
    "en": ["1. Goal and state", "2. Decisions", "3. Verified facts", "4. Open items", "5. Paths and commands",
           "6. Invoke next", "7. Date and staleness", "8. Resume"],
    "it": ["1. Obiettivo e stato", "2. Decisioni", "3. Fatti verificati", "4. Cose aperte", "5. Percorsi e comandi",
           "6. Da invocare per primi", "7. Data e scadenza", "8. Ripresa"],
}


def lang():
    return "it" if CFG.get("language") == "it" else "en"


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SECTION_RE = re.compile(r"\n+## (?:Impegni dal registro|Commitments from the registry) \(\d\d:\d\d\)\n.*\Z", re.S)


def commitments_section():
    """P5 (22/10/2026): la parte «impegni» del passaggio di consegne, dal registro e non a mano — il 21/10 alle 17:20 un ok
    del mattino era diventato «proposta non accolta» nel riassunto scritto dal modello. None senza registro (o con un
    errore: il file resta com'e'), '' con il registro senza impegni (la sezione vecchia cade)."""
    try:
        mod = _load("cm-tasks")
        con = mod.connect(create=False)
        if con is None:
            return None
        try:
            items = mod.commitments(con, git=False)
        finally:
            con.close()
        if not items:
            return ""
        title = cm.msg(CFG, "tasks.handoff_section")
        return f"\n## {title} ({time.strftime('%H:%M')})\n\n{mod.render_commitments(items)}\n"
    except Exception:   # noqa: BLE001 — un'informazione in piu': mai far fallire l'handoff
        return None


def with_section(text):
    """Il testo con la sezione impegni in fondo, una sola (quella di prima sostituita); senza registro com'era."""
    section = commitments_section()
    if section is None:
        return text
    return SECTION_RE.sub("", text).rstrip("\n") + "\n" + section


def pointer(cwd):
    h = hashlib.sha1(os.path.realpath(cwd).encode()).hexdigest()[:12]
    return STATE / "handoffs" / f"last-{h}.json"


def target(cwd, here, now):
    day = now.strftime("%Y-%m-%d")
    if here:
        d = Path(cwd) / "docs"
        p = d / f"handoff-{day}.md"
        if p.exists():
            p = d / f"handoff-{day}-{now.strftime('%H%M')}.md"
        return p
    slug = "".join(c if c.isalnum() or c in "-_." else "-" for c in Path(cwd).name) or "root"
    return STATE / "handoffs" / f"{slug}-{day}-{now.strftime('%H%M')}.md"


def prepare(argv, cwd):
    p = target(cwd, "--here" in argv, datetime.datetime.now())
    p.parent.mkdir(parents=True, exist_ok=True)
    print(f"PATH: {p}")
    print("SECTIONS: " + " | ".join(SECTIONS[lang()]))
    print(f"THEN: supervisor handoff --written {p}")
    return 0


def written(path, cwd):
    p = Path(path)
    if not p.is_file():
        print(f"supervisor handoff: {p} not found", file=sys.stderr)
        return 1
    text = with_section(p.read_text(encoding="utf-8", errors="replace"))
    p.write_text(text, encoding="utf-8")
    ptr = pointer(cwd)
    ptr.parent.mkdir(parents=True, exist_ok=True)
    ptr.write_text(json.dumps({"path": str(p), "at": int(time.time()), "cwd": os.path.realpath(cwd)}))
    words = len(text.split())
    print(f"{p} · {words} words, ~{int(words * 1.4)} tokens")
    return 0


def _prompted_since(sids, since):
    """Una sessione fra `sids` ha scritto un prompt (riga «prompt» del ledger) dopo `since`?"""
    try:
        with open(STATE / "ledger.jsonl", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 2_000_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return False
    for ln in reversed(lines):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        try:
            ts = datetime.datetime.fromisoformat(r.get("ts") or "").timestamp()
        except ValueError:
            continue
        if ts < since:
            return False
        if r.get("event") == "prompt" and r.get("session_id") in sids:
            return True
    return False


def announce(cwd, sid=None):
    """La riga per il SessionStart: solo un puntatore recente. 05/11: torna a ogni /clear finche' una sessione che l'ha
    avuto non scrive un prompt (allora il puntatore e' consumato); senza session_id, una volta sola come prima."""
    write_features()
    ptr = pointer(cwd)
    try:
        d = json.loads(ptr.read_text())
    except (OSError, ValueError):
        return ""
    if time.time() - int(d.get("at") or 0) > ANNOUNCE_H * 3600 or not Path(d.get("path") or "").is_file():
        ptr.unlink(missing_ok=True)
        return ""
    if d.get("by") == "fable-director":   # 05/11: lo scrive anche il --written di fable-director; l'annuncio e' suo
        return ""
    seen = [x for x in d.get("announced") or [] if isinstance(x, dict) and x.get("sid")]
    if not sid or (seen and _prompted_since({x["sid"] for x in seen}, min(int(x.get("at") or 0) for x in seen))):
        ptr.unlink(missing_ok=True)
        if sid:
            return ""
    else:
        d["announced"] = seen + [{"sid": sid, "at": int(time.time())}]
        tmp = ptr.with_name(f"{ptr.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, ptr)
    hm = time.strftime("%H:%M", time.localtime(int(d["at"])))
    if lang() == "it":
        return f"HANDOFF: prima di tutto leggi {d['path']} (scritto alle {hm} da questa sessione prima di /clear) e riprendi da lì."
    return f"HANDOFF: first read {d['path']} (written at {hm} by this session before /clear) and continue from there."


def snapshot(cwd, name):
    """restart arm dalla master: la sezione impegni nel passaggio di consegne recente di questa cartella (puntatore
    entro ANNOUNCE_H), altrimenti un file solo di impegni, annunciato dal SessionStart come un handoff."""
    section = commitments_section()
    if not section:
        return 0
    ptr = pointer(cwd)
    try:
        d = json.loads(ptr.read_text())
        recent = time.time() - int(d.get("at") or 0) <= ANNOUNCE_H * 3600 and Path(d.get("path") or "").is_file()
    except (OSError, ValueError):
        d, recent = {}, False
    if recent:
        p = Path(d["path"])
        p.write_text(with_section(p.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
        print(str(p))
        return 0
    now = datetime.datetime.now()
    p = STATE / "handoffs" / f"impegni-{name}-{now.strftime('%Y-%m-%d-%H%M')}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# {cm.msg(CFG, 'tasks.handoff_section')} — {name}, {now.strftime('%d/%m %H:%M')}\n{section}", encoding="utf-8")
    ptr.write_text(json.dumps({"path": str(p), "at": int(time.time()), "cwd": os.path.realpath(cwd)}))
    print(str(p))
    return 0


# ------------------------------------------------------------------ 05/11: /clear dopo il passaggio di consegne
WRITTEN_RE = re.compile(r"handoff(?:\.py)?[\"']?\s+(?:[^|;&\n]*?\s)?--written\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))")
AUTOCLEAR_WAIT_S = 120
TAIL_BYTES = 3_000_000


def _entries(transcript):
    try:
        with open(transcript, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            raw = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for ln in raw:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def _is_prompt(e):
    """Una riga della persona (o di un prompt dell'app): user con testo, non un tool_result, non meta."""
    if e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain"):
        return False
    c = (e.get("message") or {}).get("content")
    if isinstance(c, str):
        return bool(c.strip())
    return isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "text" for x in c) \
        and not any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c)


def _ts(e):
    try:
        return datetime.datetime.fromisoformat(str(e.get("timestamp") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def last_turn_handoff(transcript):
    """(path, inizio del turno, testo finale) se nell'ultimo turno un comando Bash ha registrato un passaggio di consegne
    (--written PATH di supervisor o di fable-director) e il file c'e', non vuoto e non piu' vecchio del turno; se no None."""
    es = _entries(transcript)
    start = next((i for i in range(len(es) - 1, -1, -1) if _is_prompt(es[i])), None)
    if start is None:
        return None
    t0 = _ts(es[start])
    path, final = None, ""
    for e in es[start + 1:]:
        if e.get("isSidechain"):
            continue
        for x in ((e.get("message") or {}).get("content") or []) if e.get("type") == "assistant" else []:
            if not isinstance(x, dict):
                continue
            if x.get("type") == "tool_use" and x.get("name") == "Bash":
                m = WRITTEN_RE.search(str((x.get("input") or {}).get("command") or ""))
                if m:
                    path = next(g for g in m.groups() if g)
            elif x.get("type") == "text" and str(x.get("text") or "").strip():
                final = str(x["text"])
    if not path:
        return None
    p = Path(os.path.expanduser(path))
    try:
        ok = p.is_file() and p.stat().st_size > 0 and p.stat().st_mtime >= t0 - 5
    except OSError:
        ok = False
    return (str(p), t0, final) if ok else None


WAITS_RE = re.compile(r"(?im)^\s*[*_]*(?:tipo|type)[*_]*\s*:\s*[*_]*(?:aspetta|waiting)\b")


def clear_blocked(name, final, waiting=False):
    """Il motivo per non mandare /clear, o None: la sessione aspetta (stato, needs-you, «Tipo: aspetta»), un riavvio armato."""
    if waiting:
        return "waiting"
    if WAITS_RE.search(final or ""):
        return "asks"
    for f in (STATE / "needs-you").glob("*.json") if (STATE / "needs-you").is_dir() else []:
        try:
            if json.loads(f.read_text()).get("name") == name:
                return "needs_you"
        except (OSError, ValueError):
            continue
    flag = os.environ.get("CM_RESTART_FLAG_FILE") or cm.expand((CFG.get("restart") or {}).get("flag_file") or "") or str(STATE / "restart.json")
    if Path(f"{flag[:-5] if flag.endswith('.json') else flag}-{name}.json").exists():
        return "restart_armed"
    return None


def _done_path():
    return STATE / "handoffs" / "autoclear.json"


def _log(text):
    try:
        (STATE / "handoffs").mkdir(parents=True, exist_ok=True)
        with open(STATE / "handoffs" / "autoclear.log", "a") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {text}\n")
    except OSError:
        pass


def claim(name, path):
    """Un /clear per passaggio di consegne: True la prima volta per (sessione, file), poi False."""
    d = {}
    try:
        d = json.loads(_done_path().read_text())
    except (OSError, ValueError):
        pass
    key = f"{name}|{path}"
    if key in d:
        return False
    d = {k: v for k, v in d.items() if time.time() - float(v) < 7 * 86400}
    d[key] = int(time.time())
    _done_path().parent.mkdir(parents=True, exist_ok=True)
    tmp = _done_path().with_name(f"autoclear.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, _done_path())
    return True


def spawn_clear(name, path):
    """Stacca l'esecutore (setsid): il turno e' finito ma la casella torna libera fra poco."""
    import subprocess
    if os.environ.get("CM_AUTOCLEAR_SPAWN"):   # i test: il comando si scrive, niente processo
        with open(os.environ["CM_AUTOCLEAR_SPAWN"], "a") as f:
            f.write(json.dumps([name, path]) + "\n")
        return
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--clear-when-idle", name, path],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def features_path():
    p = os.environ.get("CM_FEATURES_FILE")
    return Path(p) if p else Path.home() / ".claude" / "supervisor" / "features.json"


def write_features():
    """05/11: il segnale per fable-director che il /clear dopo un passaggio di consegne parte da solo (valido 24 h:
    riscritto a ogni SessionStart e Stop)."""
    f = features_path()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"autoclear_after_handoff": True, "updated_at": int(time.time())}))
        os.replace(tmp, f)
    except OSError:
        pass


def on_stop(name, transcript, waiting=False):
    """Dallo Stop hook (o dal giro di win): (True, path) se il /clear e' partito, (False, motivo) se no."""
    write_features()
    if not name or not transcript:
        return False, "no_session"
    hit = last_turn_handoff(transcript)
    if not hit:
        return False, "no_handoff"
    path, _, final = hit
    why = clear_blocked(name, final, waiting)
    if why:
        _log(f"{name} {path}: no /clear ({why})")
        return False, why
    if not claim(name, path):
        return False, "done"
    _log(f"{name} {path}: /clear when idle")
    spawn_clear(name, path)
    return True, path


def clear_when_idle(name, path, wait_s=AUTOCLEAR_WAIT_S):
    """L'esecutore: aspetta la casella vuota (al piu' wait_s), ricontrolla le guardie, digita /clear come un prompt dell'app."""
    import subprocess
    tune = _load("cm-tune")
    remote = None
    if tune.tmux("has-session", "-t", f"={name}").returncode != 0:   # una sessione di win: la sua console, come cm-tune
        hit = _load("cm-rsession").resolve(name)
        a = _load("cm-hosts").adapter(hit[0]) if hit else None
        if not hasattr(a, "session_console"):
            _log(f"{name}: no session to reach, no /clear")
            return 1
        remote = (a, hit[1])
        tune.REMOTE[name] = remote
        tune.GLYPHS = ("❯", ">")
        wait_s *= 3   # una lettura della console di la' costa qualche secondo
    end = time.time() + wait_s
    while time.time() < end:
        ok, why = tune.prompt_ready(name)
        if ok and getattr(tune, "pending_of", lambda n: {})(name):   # prima il cambio di modello o effort in attesa
            ok, why = False, "pending"
        if ok:
            break
        if why == "dialog":
            _log(f"{name}: a dialog is open, no /clear")
            return 1
        time.sleep(2)
    else:
        _log(f"{name}: never idle in {wait_s} s, no /clear")
        return 1
    if clear_blocked(name, ""):
        _log(f"{name}: blocked at the last check ({clear_blocked(name, '')}), no /clear")
        return 1
    if remote:
        try:
            remote[0].session_console(remote[1], ["text:/clear", "wait:200", "Enter"])
            rc = 0
        except Exception as e:   # noqa: BLE001 — la console di win non risponde: si dice nel log
            _log(f"{name}: console failed ({str(e)[:120]})")
            rc = 1
    else:   # digitato nel riquadro come un prompt dell'app: la casella deve essere vuota (talk lo controlla)
        rc = subprocess.run([str(HERE / "supervisor"), "talk", name, "/clear", "--via", "tmux", "--no-wait"], capture_output=True, text=True, timeout=60).returncode
    _log(f"{name}: /clear sent (rc {rc}) after {path}")
    return 0 if rc == 0 else 1


def autoclear_remote():
    """Le sessioni di win (gli hook li' non girano): una ferma, con la copia della trascrizione, il cui ultimo turno ha
    scritto un passaggio di consegne."""
    hm = _load("cm-hosts")
    n = 0
    for r in hm.remote_session_rows():
        if r.get("status") not in ("idle",) or r.get("unreachable") or not r.get("session_id"):
            continue
        try:
            hm.sync_transcript(r["host"], r["remote_name"], r["session_id"])
        except Exception:   # noqa: BLE001 — una copia mancata: si riprova al giro dopo
            continue
        ok, _ = on_stop(r["name"], r.get("transcript") or "", waiting=bool(r.get("waiting")))
        n += ok
    return n


def main(argv):
    cwd = os.getcwd()
    if argv[:1] == ["--clear-when-idle"] and len(argv) > 2:
        return clear_when_idle(argv[1], argv[2])
    if argv[:1] == ["--autoclear-remote"]:
        print(f"autoclear: {autoclear_remote()}")
        return 0
    if argv[:1] == ["--snapshot"] and len(argv) > 2:
        return snapshot(argv[1], argv[2])
    if argv[:1] == ["--prepare"]:
        return prepare(argv[1:], cwd)
    if argv[:1] == ["--written"] and len(argv) > 1:
        return written(argv[1], cwd)
    if argv[:1] == ["--announce"]:
        line = announce(argv[1] if len(argv) > 1 else cwd, argv[2] if len(argv) > 2 else None)
        if line:
            print(line)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
