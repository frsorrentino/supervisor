"""Il cuore condiviso di supervisor: quello che serve sia al relay del polso sia al bot.

Nasce il 16/09/2026 dal ritiro del bot interattivo di Telegram (passo 1 del piano): prima il relay
prendeva queste funzioni da cm-bot.py e da cm-bot-ui.py, cioe' l'app al polso dipendeva dal modulo di
Telegram. Qui non c'e' niente di Telegram: il registro degli eventi, il transcript di una sessione,
l'icona della scheda, il rilancio di una sessione chiusa e la lettura del transcript di Claude Code.

Ci sono anche le poche funzioni di testo che servivano agli avvisi (riga intera, nome corto, sintesi
della domanda): venivano da cm-bot-ui.py, che con il bot interattivo non esiste piu'.

La configurazione si carica PIGRA (_cfg, una volta sola): cosi' importare questo modulo non costa I/O.
Testato da tests/core-verify.py, e dal vivo dalle suite del relay e di answer.
"""
import functools
import importlib.util
import json
import os
import re
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
_CFG = None


def _cfg():
    global _CFG
    if _CFG is None:
        _CFG = cm.load(warn=False)
    return _CFG


def cm_bin():
    """Il dispatcher da usare: i test ne mettono uno finto (CM_CORE_CM, o quello del bot per le sue suite)."""
    return os.environ.get("CM_CORE_CM") or os.environ.get("CM_BOT_CM") or str(HERE / "supervisor")


def run_cm(*args):
    timeout = int((_cfg().get("bot") or {}).get("command_timeout_s") or 60)
    p = subprocess.run([cm_bin(), *args], capture_output=True, text=True, timeout=timeout)
    out = (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()
    return p.returncode, out


def prefixes():
    return [a.get("tmux_prefix") or "" for a in _cfg()["accounts"].values()]


_ICONS = {}


def icon_of(name):
    """L'icona della sessione (registro colori, stessa della scheda del Terminale); cm-color.sh la
    assegna se manca, con la logica di launch."""
    if not name:
        return ""
    if name not in _ICONS:
        try:
            r = subprocess.run([str(HERE / "cm-color.sh"), name], capture_output=True, text=True, timeout=10)
            _ICONS[name] = (r.stdout.split() or [""])[0] if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            _ICONS[name] = ""
    return _ICONS[name]


def _epoch(ts):
    import datetime as _dt
    try:
        return _dt.datetime.fromisoformat(str(ts)[:19]).timestamp()
    except ValueError:
        return 0.0


def ledger_rows():
    p = Path(cm.expand(_cfg()["state_dir"])) / "ledger.jsonl"
    out = []
    try:
        for line_ in p.read_text().splitlines():
            try:
                out.append(json.loads(line_))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def transcript_of(row):
    """Il transcript jsonl della sessione (come cm-talk): <config_dir>/projects/<slug>/<sessionId>.jsonl, o ""."""
    if row and row.get("host") and row.get("host") != "local":
        # 09/10: una sessione su un altro host → la copia locale del suo transcript (cm-hosts.sync_transcript)
        t = str(row.get("transcript") or "")
        return t if t and os.path.isfile(t) else ""
    if not row or not row.get("session_id") or not row.get("cwd"):
        return ""
    acc = _cfg()["accounts"].get(row.get("account") or "") or {}
    conf = cm.expand(acc.get("config_dir", "~/.claude"))
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(row["cwd"]))
    p = Path(conf) / "projects" / slug / f"{row['session_id']}.jsonl"
    return str(p) if p.is_file() else ""


def pending_ask(path, window=1024 * 1024):
    """09/10: la domanda aperta di un transcript — l'ultimo AskUserQuestion dell'assistente senza ancora il suo
    tool_result → (domanda, [etichette delle opzioni]) della prima domanda, o None. Per le sessioni di un altro host,
    dove non c'e' uno schermo tmux ne' l'hook di qui."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - window))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    asks, done = {}, set()
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        for c in ((d.get("message") or {}).get("content") or []) if isinstance((d.get("message") or {}).get("content"), list) else []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use" and c.get("name") == "AskUserQuestion":
                asks[c.get("id")] = c.get("input") or {}
            elif c.get("type") == "tool_result":
                done.add(c.get("tool_use_id"))
    open_ = [i for i in asks if i not in done]
    if not open_:
        return None
    qs = (asks[open_[-1]].get("questions") or [{}])
    q = qs[0] if qs and isinstance(qs[0], dict) else {}
    return str(q.get("question") or ""), [str((o or {}).get("label") or "") for o in (q.get("options") or []) if isinstance(o, dict)]


def remote_events(row, window=1024 * 1024):
    """10/10 (dal telefono: la card di una sessione su win senza anteprima): le righe che l'hook di qui scriverebbe nel
    ledger per una sessione locale, ricavate dalla copia del suo transcript — «prompt» all'ultimo messaggio della
    persona, «stop» con l'ultimo testo di Claude a turno finito (a turno in corso, quello prima del prompt). Cosi'
    esito, «Prossimi» e inizio del turno vengono dallo stesso codice delle sessioni locali."""
    p = transcript_of(row)
    if not p:
        return []
    try:
        ents = transcript_entries(p, max(0, os.path.getsize(p) - window), text_max=20000)
    except OSError:
        return []
    sid = row.get("session_id") or ""
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))  # noqa: E731
    users = [i for i, e in enumerate(ents) if e.get("role") == "user" and not e.get("queued") and e.get("at")]
    last_u = users[-1] if users else -1
    scope = ents[:last_u] if row.get("status") == "busy" and last_u >= 0 else ents
    said = [e for e in scope if e.get("role") == "assistant" and e.get("text") and e.get("at")]
    out = []
    if said:
        text = said[-1]["text"]
        stop = {"event": "stop", "session_id": sid, "ts": iso(said[-1]["at"]), "tail": text}
        if not re.search(r"(?mi)^\s*\W*\s*(?:esito|watch)\s*:", text):
            # senza riga «Esito:» la card mostrerebbe un pezzo a caso della fine: la prima frase, che dice l'esito
            first = next((l.strip() for l in text.splitlines() if l.strip()), "")
            stop["watch"] = "Watch: " + re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0]
        out.append(stop)
    if last_u >= 0:
        out.append({"event": "prompt", "session_id": sid, "ts": iso(ents[last_u]["at"])})
    return sorted(out, key=lambda e: e["ts"])


def reopen(name, run=None):
    """Rilancia una sessione sparita (✗) nella sua cartella, con il suo account e senza finestra (dal telefono o dal
    polso non serve; 15/09, dall'orologio: una sessione chiusa non si poteva riprendere). La conversazione: `--resume
    <id>` se la fotografia del registro ha l'id e il suo transcript esiste (con un id inesistente claude apre una
    conversazione VUOTA senza errore); altrimenti `--continue`, ma solo se nella cartella non c'e' un'altra sessione
    viva, perche' -c prende l'ultima conversazione della cartella, che puo' essere quella dell'altra. Rifiuta se una
    sessione viva ha gia' quel nome. Torna (ok, codice, campi): i testi li sceglie chi chiama (bot.reopen_*,
    relay.cmd_reopen_*). `run` e' il dispatcher di chi chiama: il relay ha il suo (CM_RELAY_CM)."""
    run = run or run_cm

    def _j(*args, expect):
        try:
            rc, out = run(*args)
            return json.loads(out) if rc == 0 and out.strip().startswith(expect) else None
        except (ValueError, subprocess.TimeoutExpired):
            return None

    def _here(r):
        return r.get("cwd") and os.path.realpath(r["cwd"]) == os.path.realpath(cwd)
    live = [r for r in (_j("sessions", "--json", expect="[") or []) if r.get("tmux") or r.get("name")]
    if any((r.get("tmux") or r.get("name")) == name for r in live):
        return False, "alive", {}
    s = next((x for x in (_j("registry", "--good", expect="{") or {}).get("sessioni", []) if x.get("nome") == name), None)
    if not s:
        return False, "unknown", {}
    cwd, acct, sid = s.get("cartella") or "", s.get("account") or "", s.get("session_id") or ""
    if not cwd or not os.path.isdir(cwd):
        return False, "no_dir", {"path": cwd or "?"}
    # --window esplicito (15/09, prova dal polso: la sessione ripartiva ma sul desktop non c'era): launch recupera il
    # display anche da un daemon nato da cron; senza desktop si lancia comunque, senza finestra
    args = ["launch", cwd, "--window"] + (["--account", acct] if acct else [])
    if sid and transcript_of({"session_id": sid, "cwd": cwd, "account": acct}):
        args += ["--resume", sid]; code = "done"
    else:
        same = [r for r in live if _here(r)]
        if same:
            return False, "ambiguous", {"n": len(same), "path": cwd}
        args += ["--continue"]; code = "done_last"
    try:
        rc, out = run(*args)
    except subprocess.TimeoutExpired:
        return False, "failed", {"line": "timeout"}
    if rc != 0:
        return False, "failed", {"line": (out.splitlines() or ["launch failed"])[0]}
    # launch prende il primo nome libero: se non e' il vecchio, il vecchio esce dalla fotografia (restava ✗ per sempre)
    before = {r.get("tmux") or r.get("name") for r in live}
    new = [r.get("tmux") or r.get("name") for r in (_j("sessions", "--json", expect="[") or [])
           if (r.get("tmux") or r.get("name")) not in before and _here(r)]
    if new and name not in new:
        run("registry", "--closed", name)
    return True, code, {"account": acct or "?", "new": new[0] if new else name}


# ------------------------------------------------------------------ modello, effort e contesto di una sessione
WINDOW_1M = 1_000_000
WINDOW_STD = 200_000


def _identity_in(lines):
    """L'ULTIMA voce di sistema col modello fra queste righe, o None."""
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if d.get("type") == "attachment":
            a = d.get("attachment") or {}
            if a.get("type") == "model" and (a.get("identity") or {}).get("modelId"):
                return a["identity"]
    return None


def _window_of(row, full_id, model_id, tokens):
    """La finestra di contesto della sessione, o None se non si sa (e allora `context` resta null: mai stimato).

    1.13.1 (17/09/2026, dall'app: master su Fable 5.1 risultava al 100 %, il terminale diceva 59 %). Il suffisso
    `[1m]` nell'id e' certo quando c'e', ma non tutti i modelli da 1M lo portano: la voce di sistema di Fable 5.1 e'
    «claude-fable-5-1» sia con la finestra da 1M sia con quella da 200k. In ordine di certezza:
    1. `[1m]` nell'id, o un id di relay.window_1m (da 1M senza dirlo: Haiku 5.5, 16/10/2026);
    2. la finestra che Claude Code stesso dichiara alla statusline (`context_window_size`), se un programma di
       statusline la salva per sessione in relay.window_hint_dir (fable-director lo fa: <session_id>.json con
       `ctx_size` e `model`) e il modello di quella fotografia e' quello dell'ultimo turno;
    3. piu' token della finestra standard: e' da 1M per forza, nessuna sessione ne tiene piu' della sua finestra;
    4. un id di relay.window_unmarked (modelli che non dicono la finestra): non si sa → None;
    5. altrimenti la finestra standard."""
    R = _cfg().get("relay") or {}
    if "[1m]" in full_id or model_id in (R.get("window_1m") or []):
        return WINDOW_1M
    hint_dir = R.get("window_hint_dir")
    sid = (row or {}).get("session_id") or ""
    if hint_dir and sid:
        try:
            snap = json.loads((Path(cm.expand(hint_dir)) / f"{sid}.json").read_text())
            size = int(snap.get("ctx_size") or 0)
            if size >= WINDOW_STD and str(snap.get("model") or "").split("[")[0] == model_id:
                return size
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    if tokens > WINDOW_STD:
        return WINDOW_1M
    if model_id in (R.get("window_unmarked") or []):
        return None
    return WINDOW_STD


def session_runtime(row, tail_bytes=512 * 1024, head_bytes=1024 * 1024):
    """Quello che la sessione sta usando: la trascrizione, corretta da un cambio fatto dal selettore (1.12) che la
    trascrizione non riporta ancora. Vedi _transcript_runtime per la lettura e _tuned per l'annotazione."""
    out = _transcript_runtime(row, tail_bytes, head_bytes)
    last_turn = out.pop("_ts", 0.0)
    return _tuned(row, out, last_turn)


def permission_mode(row, tail_bytes=512 * 1024):
    """1.54 (14/10, dagli spunti della valutazione di Kaya): la modalita' dei permessi della sessione. Claude Code la
    scrive in una riga {"type":"permission-mode","permissionMode":...} quando cambia e su ogni prompt della persona
    ("permissionMode" della riga user): vale l'ultima nella coda del file. None se non c'e'.

    1.69 (15/10): un cambio fatto con `supervisor permissions` (Shift+Tab) e' annotato in tune.file; vale finche' la
    trascrizione non scrive qualcosa di piu' recente dell'annotazione, poi vince di nuovo la trascrizione."""
    path = transcript_of(row)
    noted = _tuned_field(row, "permission_mode")
    try:
        mtime = os.path.getmtime(path) if path else 0.0
    except OSError:
        mtime = 0.0
    if noted and noted[1] > mtime:
        return noted[0]
    if not path:
        return None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            data = f.read()
    except OSError:
        return None
    found = re.findall(rb'"permissionMode":\s*"([A-Za-z]{1,40})"', data)
    return found[-1].decode() if found else None   # un nome nuovo di Claude Code passa com'e': l'app lo mostra come testo


def _tuned_field(row, key):
    """(valore, quando) di un campo annotato da cm-tune per la sessione in tune.file; None se non c'e'."""
    sid = (row or {}).get("session_id") or ""
    f = (_cfg().get("tune") or {}).get("file") or ""
    if not sid or not f:
        return None
    try:
        entry = (json.loads(Path(cm.expand(f)).read_text()) or {}).get(sid) or {}
    except (OSError, ValueError, AttributeError):
        return None
    return (str(entry[key]), float(entry.get("at") or 0)) if entry.get(key) else None


BYPASS_FLAGS = re.compile(r"(?:^|\s)(?:--dangerously-skip-permissions|--allow-dangerously-skip-permissions"
                          r"|--permission-mode[=\s]+bypassPermissions)(?=\s|$)")


def permission_bypass(row, mode=None):
    """1.69: True se la sessione puo' arrivare a bypassPermissions con Shift+Tab. Claude Code la mette nel giro solo
    se e' partita con uno di questi argomenti; o se ci e' gia'."""
    if mode == "bypassPermissions":
        return True
    pid = (row or {}).get("pid")
    if not pid or (row or {}).get("host"):   # il pid di una sessione su un altro host non e' un processo di qui
        return False
    try:
        cmd = Path(f"/proc/{int(pid)}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except (OSError, ValueError):
        return False
    return bool(BYPASS_FLAGS.search(cmd))


def _tuned(row, out, last_turn):
    """(contratto 1.12, 16/09) `supervisor model|effort` cambia la sessione dal suo selettore, ma la trascrizione
    lo scrive solo al turno successivo: fino ad allora vale l'annotazione in tune.file. Un turno piu' recente
    dell'annotazione riporta il valore vero, e da li' vince la trascrizione. Con un modello cambiato il contesto
    torna null: la finestra del turno vecchio non e' quella del modello nuovo, e il numero non si stima."""
    sid = (row or {}).get("session_id") or ""
    f = (_cfg().get("tune") or {}).get("file") or ""
    if not sid or not f:
        return out
    try:
        entry = (json.loads(Path(cm.expand(f)).read_text()) or {}).get(sid) or {}
    except (OSError, ValueError, AttributeError):
        return out
    if not entry or float(entry.get("at") or 0) <= last_turn:
        return out
    if isinstance(entry.get("model"), dict) and entry["model"].get("id"):
        out["model"] = {"id": entry["model"]["id"], "label": entry["model"].get("label") or None}
        out["context"] = None
    if entry.get("effort"):
        out["effort"] = str(entry["effort"])
    return out


def _turn_epoch(ts):
    import datetime as _dt
    try:
        return _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _transcript_runtime(row, tail_bytes, head_bytes):
    """(contratto 1.11, 16/09/2026) Cosa sta usando una sessione, letto dalla sua trascrizione:
    {"model": {"id","label"} | None, "effort": "high" | None, "context": 0-100 | None}.

    L'ultimo turno dell'assistente porta il modello (senza la finestra) e l'effort; una voce di sistema
    porta l'id completo e il nome («claude-opus-5[1m]», «Opus 5 (1M context)»), da cui si sa se la finestra
    e' da 1M o standard. Il contesto e' la somma dei token dell'ultimo turno (input + cache letta + cache
    scritta) sulla finestra. Se i due modelli non coincidono — un cambio di modello a meta' sessione — la
    finestra non e' certa e `context` resta ASSENTE invece di essere stimato: e' la regola chiesta dall'app.

    16/10/2026 (Claude Code 2.1.299, compattazione a sessione ferma): una conversazione oltre i 200k token lasciata
    ferma circa 54 minuti viene riassunta senza un turno nuovo. Se dopo l'ultimo turno c'e' un compact_boundary, il
    contesto e' il suo compactMetadata.postTokens, sulla finestra provata dai token di prima (preTokens): la master
    restava al 92 % per ore con 15k token veri, e l'app proponeva handoff e /clear. Un confine senza postTokens
    (CLI vecchie) lascia `context` null, mai il numero di prima."""
    out = {"model": None, "effort": None, "context": None}
    path = transcript_of(row)
    if not path:
        return out
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            data = f.read()
    except OSError:
        return out
    lines = data.split(b"\n")
    if size > tail_bytes and lines:
        lines = lines[1:]   # la prima riga della coda e' mozza
    assistant, boundary = None, None
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if boundary is None and d.get("type") == "system" and d.get("subtype") == "compact_boundary":
            boundary = d.get("compactMetadata") if isinstance(d.get("compactMetadata"), dict) else {}
            continue
        if d.get("type") == "assistant" and isinstance(d.get("message"), dict):
            # «<synthetic>» e' un turno che Claude Code scrive da se' (un errore dell'API, un'interruzione): non ha
            # ne' modello ne' token veri. Si risale all'ultimo turno vero (visto dal vivo il 17/09).
            if str(d["message"].get("model") or "").startswith("<"):
                continue
            assistant = d
            break
    if assistant is None:
        return out
    out["_ts"] = _turn_epoch(assistant.get("timestamp"))
    # 1.11.1 (16/09, dall'app: label e context sempre nulli dal vivo): la voce di sistema col modello la scrive
    # Claude Code all'AVVIO, quindi in una sessione lunga sta ben prima della coda — nella mia, a 221 KB su 4,5 MB.
    # Si guarda prima la coda (un cambio di modello a meta' sessione ne riscrive una li'), poi la testa. La testa e'
    # larga 1 MB perche' quella voce non e' la prima riga del file: viene dopo il prompt di sistema, i memo e gli
    # hook del progetto, e in questa sessione stava a 221 KB — con 128 KB restava fuori e il campo tornava vuoto.
    identity = _identity_in(lines)
    if identity is None and size > tail_bytes:
        try:
            with open(path, "rb") as f:
                head = f.read(head_bytes)
            identity = _identity_in(head.split(b"\n")[:-1])   # l'ultima riga della testa e' mozza
        except OSError:
            identity = None
    model_id = str(assistant["message"].get("model") or "")
    if assistant.get("effort"):
        out["effort"] = str(assistant["effort"])
    full_id = str((identity or {}).get("modelId") or "")
    same = bool(full_id) and full_id.split("[")[0] == model_id
    if same:
        label = str((identity or {}).get("marketingName") or "").split(" (")[0].strip()
        out["model"] = {"id": full_id, "label": label or None}
        usage = assistant["message"].get("usage") or {}
        tokens = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        if boundary is not None:   # compattata dopo l'ultimo turno
            window = _window_of(row, full_id, model_id, max(tokens, int(boundary.get("preTokens") or 0)))
            tokens = int(boundary.get("postTokens") or 0)
        else:
            window = _window_of(row, full_id, model_id, tokens)
        if tokens and window:
            out["context"] = max(0, min(100, round(tokens * 100 / window)))
    elif model_id:
        # modello cambiato a meta' sessione (o voce di sistema assente): l'id si sa, la finestra no
        out["model"] = {"id": model_id, "label": None}
    return out


FALLBACK_MARK = b'"subtype":"model_refusal_fallback"'


def _base_model(model_id):
    return str(model_id or "").split("[")[0]


FALLBACK_SCAN_CHUNK = 8 * 1024 * 1024


def _fallback_marks(path, f):
    """Le posizioni di FALLBACK_MARK nel file, in ordine. Afdc2b (14/10): la ricerca rifaceva ogni volta tutto il file con
    mmap.rfind, che e' lento (2,75 s su 89 MB a macchina ferma, 13 s sotto carico, contro 0,07 di bytes.rfind), e
    `sessions --json` la chiamava per ogni sessione a ogni push e quasi a ogni comando del relay: con la trascrizione
    della master a 85 MB la raccolta arrivava a 124 s e le push aspettavano fino a 37 minuti. Ora si legge a pezzi con
    bytes.find e solo la parte aggiunta dall'ultima volta (la trascrizione cresce in coda): cache in
    <state_dir>/fallback-scan.json, {path: {size, marks}}; un file piu' corto di prima si rilegge da capo."""
    cache_p = Path(cm.expand(_cfg()["state_dir"])) / "fallback-scan.json"
    path = os.path.realpath(path)
    try:
        cache = json.loads(cache_p.read_text())
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    size = os.fstat(f.fileno()).st_size
    c = cache.get(path) if isinstance(cache.get(path), dict) else {}
    done, marks = int(c.get("size") or 0), [int(x) for x in c.get("marks") or []]

    def edge(n):   # gli ultimi byte gia' letti e i primi del file: se cambiano, il file non e' solo cresciuto
        f.seek(0)
        h = f.read(64)
        f.seek(max(0, n - 64))
        return (h + f.read(min(64, n))).hex()
    if done > size or (done and edge(done) != c.get("edge")):
        done, marks = 0, []
    if done == size:
        return marks
    pos = max(0, done - len(FALLBACK_MARK) + 1)
    while pos < size:
        f.seek(pos)
        chunk = f.read(FALLBACK_SCAN_CHUNK + len(FALLBACK_MARK) - 1)
        if not chunk:
            break
        i = chunk.find(FALLBACK_MARK)
        while i >= 0:
            if pos + i not in marks:
                marks.append(pos + i)
            i = chunk.find(FALLBACK_MARK, i + 1)
        pos += FALLBACK_SCAN_CHUNK
    cache[path] = {"size": size, "marks": marks, "edge": edge(size)}
    if len(cache) > 300:   # le trascrizioni sparite escono per prime
        cache = {k: v for k, v in cache.items() if os.path.exists(k) or k == path}
    try:
        tmp = cache_p.with_name(f"{cache_p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cache))
        os.replace(tmp, cache_p)
    except OSError:
        pass
    return marks


def model_fallback(row, tail_bytes=512 * 1024):
    """La sessione e' passata a un altro modello perche' le salvaguardie hanno segnalato un messaggio? (22/09/2026)
    {"from", "to", "category", "at"} oppure None. Definizione comune con fable-director (hook PostModelSwitch).

    Da Opus 5.5 (Claude Code 2.1.280) un messaggio segnalato (bio, cyber) sposta la sessione su un modello piu'
    vecchio senza fermarla, e un avviso a schermo lo dice una volta sola. Claude Code scrive nella trascrizione una
    riga {"type":"system","subtype":"model_refusal_fallback", ...} con originalModel, fallbackModel, direction
    (retry | revert | sticky), scope (session | local; assente nelle versioni vecchie = session) e
    apiRefusalCategory, e la rilegge alla ripresa (letto nel binario 2.1.280: schema SDK e codice di resume; mai
    provocato un messaggio segnalato per vederla). E' ripiegata se l'ultima riga che non sia `local` non e' un
    `revert` e l'ultimo turno vero e' ancora sul suo fallbackModel: chi torna indietro con /model o
    `supervisor model` scrive turni sul modello di prima, e il segno sparisce da se'. Un cambio fatto da
    `supervisor model` dopo la riga, non ancora scritto in un turno, lo spegne subito (tune.file)."""
    path = transcript_of(row)
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            # la riga si cerca in tutto il file: il messaggio segnalato puo' essere di ore fa, ben prima della coda
            entry = None
            for i in reversed(_fallback_marks(path, f)):
                f.seek(i)
                tail = f.read(64 * 1024)
                f.seek(max(0, i - 64 * 1024))
                head = f.read(i - max(0, i - 64 * 1024))
                line = head[head.rfind(b"\n") + 1:] + tail.split(b"\n", 1)[0]
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "system" and d.get("subtype") == "model_refusal_fallback" and d.get("scope") != "local":
                    entry = d
                    break
            if entry is None:
                return None
            last = None
            size = os.fstat(f.fileno()).st_size
            f.seek(max(0, size - tail_bytes))
            for raw in reversed(f.read().split(b"\n")):
                if b'"type":"assistant"' not in raw:
                    continue
                try:
                    m = str((json.loads(raw).get("message") or {}).get("model") or "")
                except (ValueError, AttributeError):
                    continue
                if m and not m.startswith("<"):
                    last = m
                    break
    except OSError:
        return None
    to = str(entry.get("fallbackModel") or "")
    if entry.get("direction") == "revert" or not to or _base_model(last) != _base_model(to):
        return None
    at = int(_turn_epoch(entry.get("timestamp"))) or None
    sid = (row or {}).get("session_id") or ""
    f = (_cfg().get("tune") or {}).get("file") or ""
    if sid and f and at:
        try:
            tuned = (json.loads(Path(cm.expand(f)).read_text()) or {}).get(sid) or {}
        except (OSError, ValueError, AttributeError):
            tuned = {}
        if float(tuned.get("at") or 0) > at and (tuned.get("model") or {}).get("id"):
            return None
    return {"from": str(entry.get("originalModel") or "") or None, "to": to,
            "category": str(entry.get("apiRefusalCategory") or "") or None, "at": at}


# ------------------------------------------------------------------ il polso riceve? (scorta su Telegram)
def relay_dir():
    return Path(cm.expand((_cfg().get("relay") or {}).get("dir") or "~/.cc-supervisor/relay"))


def relay_down_for(now=None):
    """Da quanti secondi il polso non riceve: il relay scrive fallback.json {"since": epoch} quando una push o
    una sveglia FCM fallisce, e lo cancella appena ne va a buon fine una. 0 se tutto funziona (16/09, passo 3 del
    ritiro di Telegram: sotto la soglia si tace, perche' un singolo errore di rete si recupera da solo)."""
    try:
        since = float(json.loads((relay_dir() / "fallback.json").read_text()).get("since") or 0)
    except (OSError, ValueError, AttributeError):
        return 0.0
    return max(0.0, (now or time.time()) - since) if since else 0.0


def watch_receiving(now=None):
    """L'orologio e' accoppiato e riceve: c'e' un dispositivo e l'ultima push e' andata bene da poco (la stessa
    regola che teneva muto Telegram quando l'app avvisa gia' lei)."""
    fresh = float((_cfg().get("bot") or {}).get("watch_fresh_s") or 180)
    try:
        devices = json.loads((relay_dir() / "devices.json").read_text())
        last = json.loads((relay_dir() / "last-state.json").read_text())
    except (OSError, ValueError):
        return False
    return bool(devices) and (now or time.time()) - float(last.get("pushed_at") or 0) < fresh


def relay_event(kind, title, body, ref=None, account=None):
    """Lo stesso avviso che va a Telegram, anche all'app come evento del relay (contratto 1.18): diario, resoconto
    della notte, ripresa della quota. Con il relay spento non fa nulla; un errore del relay non ferma mai Telegram."""
    if not (_cfg().get("relay") or {}).get("enabled"):
        return False
    try:
        _load("cm-relay").emit(kind, title, body, ref=ref, account=account)
        return True
    except Exception as e:   # noqa: BLE001 — la chiave mancante, il bus giu', la libreria di cifratura assente
        try:
            with open(relay_dir() / "relay.log", "a") as f:
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} evento {kind} NON mandato: {e}\n")
        except OSError:
            pass
        return False


# ------------------------------------------------------------------ testo a misura di polso e di telefono
WIDTH = 22        # larghezza utile di uno schermo tondo: solo le etichette corte ci passano
MAX_LINES = 8     # righe di un avviso: oltre, si legge male sia sul polso sia nella bolla
NAME_MAX = 14     # il nome di una sessione in una riga, senza il prefisso dell'account


def short_name(name, prefixes=(), n=NAME_MAX):
    for p in sorted((p for p in prefixes if p), key=len, reverse=True):
        if name.startswith(p):
            name = name[len(p):]
            break
    return name[:n]


def line(text):
    """Una riga logica = una riga fisica (l'utente 12/09 11:14, screenshot di telefono e watch): spazi normalizzati,
    NESSUN taglio ne' a capo interno — la bolla di Telegram si allarga quanto la riga piu' lunga, e a 22
    caratteri restava a meta' schermo col testo mozzato. Solo le etichette dei bottoni passano da fit()."""
    return " ".join(str(text or "").split())


TOOL_INPUT_KEYS = ("command", "file_path", "pattern", "path", "query", "url", "prompt", "description")


def join(*parts):
    """Righe corte adiacenti dello stesso tipo unite con « · », cosi' la prima riga e' la piu' lunga possibile."""
    return " · ".join(line(p) for p in parts if line(p))


def question_gist(text, max_chars=88):
    """La domanda in breve, deterministica (l'utente 12/09 10:54: tagliata non si puo' rispondere): tutta se sta in
    max_chars; altrimenti l'ULTIMA frase interrogativa che ci sta (il contesto prima si lascia); altrimenti ""
    (serve una sintesi: la fa cm-answer col modello)."""
    t = " ".join(str(text or "").split())
    if len(t) <= max_chars:
        return t
    for s in reversed(re.findall(r"[^.!?]*\?", t)):
        s = s.strip()
        if s and len(s) <= max_chars:
            return s
    return ""


def tool_note(tool_input):
    """La `description` che Claude scrive accanto a un comando Bash: dice l'INTENTO («Run the plugin test suite»)
    dove il comando dice solo «cd …» (contratto 1.5, chiesto dal polso il 14/09). "" se non c'e'."""
    if isinstance(tool_input, dict):
        d = " ".join(str(tool_input.get("description") or "").split())
        return d[:120]
    return ""


def tool_line(name, tool_input, width=120):
    """«Bash git log --since yesterday»: il tool e l'input (fino a `width` caratteri, senza segno di taglio), come
    lo spinner del desktop."""
    detail = ""
    if isinstance(tool_input, dict):
        for k in TOOL_INPUT_KEYS:
            if tool_input.get(k):
                detail = " ".join(str(tool_input[k]).split())
                break
    elif tool_input:
        detail = " ".join(str(tool_input).split())
    return line(f"{name} {detail[:width]}")


ENTRY_TEXT_MAX = 20000   # 1.22: il testo di una voce della chat del telefono; oltre, `cut: true` (mai «…»). 08/10 (il maintainer,
# dall'app: una risposta di 10.721 caratteri arrivava tagliata a 4000, a meta' parola): da 4000 a 20000
ENTRY_BYTES_MAX = 40000   # e mai oltre 40 KB in UTF-8, cosi' una voce sola sta sempre in una pagina da 60 KB di /result
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
# 1.22 (30/09): i file che nella chat del telefono diventano un'anteprima. Write/Edit solo di media e documenti (il
# codice e le note .md si modificano di continuo: sarebbero rumore); SendUserFile qualunque file
FILE_EXTS = {"png", "jpg", "jpeg", "webp", "gif", "svg", "mp4", "webm", "mov", "m4v", "mp3", "wav", "m4a", "ogg",
             "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "csv", "html", "zip"}
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
_REPORT_IMAGE = re.compile(r"^\s*(?:image|immagine):\s*(/\S.*?)\s*$", re.M)
WIN_ABS = re.compile(r"[A-Za-z]:[\\/]|\\\\")   # un percorso assoluto di Windows (D:\… o \\server\…)
_RENDER_HTML = re.compile(r"^\s*html:\s*(/\S.*?\.html)\s*$", re.M)   # 03/10: `supervisor render` stampa la pagina fatta


def _file_ref(path, cwd):
    import mimetypes
    p = str(path or "").strip()
    if not p:
        return None
    if WIN_ABS.match(str(cwd or "")) or WIN_ABS.match(p):
        # 10/10: una sessione su win — D:\… e' gia' assoluto (prima diventava «cwd/D:\…»), il resto si unisce con «\»
        if not WIN_ABS.match(p) and cwd:
            p = str(cwd).rstrip("\\/") + "\\" + p.replace("/", "\\")
    elif not os.path.isabs(p) and cwd:
        p = os.path.join(cwd, p)
    try:
        size = os.path.getsize(p)
    except OSError:
        size = None
    return {"path": p, "mime": mimetypes.guess_type(p)[0] or "application/octet-stream", "size": size}


def _files_of(name, inp, cwd):
    if not isinstance(inp, dict):
        return []
    if name in WRITE_TOOLS:
        p = inp.get("file_path") or inp.get("notebook_path") or ""
        return [_file_ref(p, cwd)] if str(p).rsplit(".", 1)[-1].lower() in FILE_EXTS else []
    if name == "SendUserFile":
        ps = inp.get("files") or inp.get("file_paths") or [inp.get("file_path") or inp.get("path") or ""]   # dal vivo: files
        return [f for f in (_file_ref(x, cwd) for x in ps if isinstance(x, str)) if f]
    return []


def _human(d):
    """Una riga `user` scritta dalla persona: origin.kind «human» (senza origin, le versioni vecchie: testo che non e'
    un involucro <…> di comandi, notifiche o promemoria). Le meta, le sidechain, i risultati degli strumenti e i riassunti
    delle compattazioni no: questi sono righe user senza origin, e la chat li mostrava come un messaggio della persona."""
    if d.get("isMeta") or d.get("isSidechain") or d.get("isCompactSummary"):
        return False
    o = d.get("origin")
    if isinstance(o, dict):
        return o.get("kind") == "human"
    return True


_PREFIXES = None
_VOICE = None


def _prompt_prefixes():
    """[(prefisso, origin)] dei prompt mandati dal relay, in tutte le lingue: telefono, orologio, e quello senza
    dispositivo («remote»)."""
    global _PREFIXES
    if _PREFIXES is None:
        _PREFIXES = []
        for f in sorted((HERE.parent / "messages").glob("*.json")):
            try:
                m = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            # 1.36: «web» per la web app; il prefisso senza dispositivo di prima della 1.36 (diceva «polso») resta «remote»
            for key, origin in (("relay.prompt_prefix_phone", "phone"), ("relay.prompt_prefix_watch", "watch"), ("relay.prompt_prefix_web", "web"),
                                ("relay.prompt_prefix", "remote"), ("relay.prompt_prefix_legacy", "remote")):
                if m.get(key):
                    # 10/10: la frase sul passaggio alla master — quella per le sessioni remote, e senza (prima del 10/10)
                    h = m.get("relay.prompt_handoff") or ""
                    alt = [m.get("relay.prompt_handoff_remote") or "", m.get("relay.prompt_handoff_remote_legacy") or "", ""]   # «…legacy»: la frase remota delle 20:30 del 10/10
                    forms = [m[key]] + ([m[key].replace(h, a) for a in alt] if h and h in m[key] else [])
                    for form in forms:
                        _PREFIXES.append((form, origin))
                    # i prompt arrivati prima dei rinomini dicevano «claude-master» (fino al 07/10) e «team-supervisor»
                    # (fino al 08/10): le trascrizioni restano
                    old_form = forms[-1]
                    if "supervisor" in old_form:
                        for old in ("team-supervisor", "claude-master"):
                            _PREFIXES.append((old_form.replace("supervisor", old), origin))
        _PREFIXES.sort(key=lambda x: -len(x[0]))
    return _PREFIXES


def _voice_texts():
    """1.42: le istruzioni vocali che il relay aggiunge dopo il prefisso a un prompt della modalita' live, in tutte le lingue."""
    global _VOICE
    if _VOICE is None:
        _VOICE = []
        for f in sorted((HERE.parent / "messages").glob("*.json")):
            try:
                v = json.loads(f.read_text()).get("relay.prompt_voice")
            except (OSError, ValueError):
                continue
            if v:
                _VOICE.append(v)
    return _VOICE


def _origin_of(text):
    """(testo senza il prefisso del relay, origin): «phone», «watch», «web», «remote» (relay, dispositivo ignoto) o «pc»."""
    for p, origin in _prompt_prefixes():
        if text.startswith(p):
            rest = text[len(p):].strip()
            for v in _voice_texts():
                if rest.startswith(v):
                    rest = rest[len(v):].strip()
                    break
            return rest, origin
    return text, "pc"


def _clean(text):
    """Il testo di un messaggio della persona: senza promemoria; vuoto per gli involucri <…> e gli avvisi
    «[Request interrupted …]» di Claude Code. Una lista di blocchi (testo + immagini incollate) → (testo, n immagini)."""
    if isinstance(text, list):
        t = " ".join(str(b.get("text") or "") for b in text if isinstance(b, dict) and b.get("type") == "text")
        n = sum(1 for b in text if isinstance(b, dict) and b.get("type") == "image")
        return _clean(t)[0], n
    t = _REMINDER.sub("", str(text or "")).strip()
    if (t.startswith("<") and t.endswith(">")) or t.startswith(("[Request interrupted", "[Cross-session")):
        return "", 0
    return t, 0


def cut_text(t, n):
    """(testo, tagliato): al piu' n caratteri ed ENTRY_BYTES_MAX byte, tagliato all'ultima parola intera se ce n'e' una
    nell'ultimo quinto (08/10: il taglio cadeva a meta' parola, «e' la qualific»)."""
    if len(t) <= n and len(t.encode()) <= ENTRY_BYTES_MAX:
        return t, False
    c = t[:n]
    while len(c.encode()) > ENTRY_BYTES_MAX:
        c = c[:len(c) * 9 // 10]
    i = max(c.rfind(" "), c.rfind("\n"))
    if i >= len(c) * 4 // 5:
        c = c[:i]
    return c.rstrip(), True


def _entry(eid, role, text, at, tool=None, note=None, error=None, text_max=ENTRY_TEXT_MAX):
    t, cut = cut_text(str(text or ""), text_max)
    return {"id": eid, "role": role, "text": t, "at": at, "tool": tool, "note": note or None,
            "error": error, "cut": cut, "turn": None, "files": None, "origin": None, "queued": False}


def transcript_entries(path, offset=0, limit=None, text_max=ENTRY_TEXT_MAX, chat_only=False):
    """1.22 (30/09): la conversazione di una sessione come chat, dal transcript dopo `offset` byte. Voci in ordine:
    user (quello che la persona ha scritto), assistant (il testo di Claude), tool (una per chiamata: nome, dettaglio
    corto, la description come note, error dal tool_result, files = [{path, mime, size}] dei media e documenti scritti
    o mandati). `turn` = {started, ended, in, out} sull'ultima voce
    di un turno chiuso (system turn_duration; token sommati dagli usage dei messaggi del turno).
    1.27 (03/10, search): `limit` = si legge fino a quel byte; `text_max` = il testo intero invece dei 4000 caratteri;
    `chat_only` = solo voci user e assistant, e le righe dei soli strumenti si saltano senza decodificarle (sono quasi
    tutti i byte di una trascrizione)."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read() if limit is None else f.read(max(0, limit - offset))
    except OSError:
        return []
    lines = data.split(b"\n")
    if offset:
        lines = lines[1:]   # la prima riga puo' essere a meta'
    _entry = functools.partial(globals()["_entry"], text_max=text_max)
    out, by_tool, tokens, peer_seen, queued, taken, waiting_q, qkey = [], {}, [0, 0], set(), {}, [], [], {}

    def unqueue(e):
        """La voce non e' piu' in coda: resta con l'ora in cui e' stata scritta, e il suo testo non assorbe piu' una
        voce dopo uguale."""
        e["queued"] = False
        k = qkey.get(id(e))
        if k in queued:
            queued[k] = [x for x in queued[k] if x is not e]

    def human_entry(eid, raw, at):
        """Una voce della persona. Se lo stesso testo e' gia' in coda (enqueue visto prima), quella voce resta — con
        l'ora in cui e' stato scritto — e smette di essere `queued`: niente doppioni, niente ritardo."""
        t, imgs = _clean(raw)
        if not t and not imgs:
            return
        t, origin = _origin_of(t)
        waiting = queued.get(t)
        if waiting:
            e = waiting.pop(0)
            e["queued"] = False
            return
        e = dict(_entry(eid, "user", t, at), origin=origin)
        if imgs:
            e["note"] = f"{imgs} image(s)"
        out.append(e)

    def relayed(o):
        """Un prompt del relay consegnato dal socket arriva come messaggio `peer` (origin.body, due volte: in coda e
        poi come riga meta): e' della persona, dal telefono o dall'orologio. Gli altri peer (sessioni) no."""
        if not isinstance(o, dict) or o.get("kind") != "peer":
            return None
        t, origin = _origin_of(str(o.get("body") or "").strip())
        if origin == "pc" or not t or o.get("msg_id") in peer_seen:
            return None
        peer_seen.add(o.get("msg_id"))
        return t, origin
    for raw in lines:
        if not raw.strip():
            continue
        if chat_only and (b'"tool_result"' in raw or (b'"tool_use"' in raw and b'"type":"text"' not in raw)):
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        kind, uid, at = d.get("type"), str(d.get("uuid") or ""), int(_turn_epoch(d.get("timestamp"))) or None
        if d.get("isSidechain"):
            continue
        content = (d.get("message") or {}).get("content")
        if kind == "user":
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in by_tool:
                        e = by_tool[b["tool_use_id"]]
                        e["error"] = bool(b.get("is_error"))
                        if e["error"]:
                            e["files"] = None   # la scrittura non e' riuscita: nessun file da mostrare
                        elif e["tool"] == "Bash" and ("supervisor report" in e["text"] or "supervisor render" in e["text"]):
                            res = b.get("content")
                            res = res if isinstance(res, str) else "\n".join(str(x.get("text") or "") for x in (res or []) if isinstance(x, dict))
                            found = _REPORT_IMAGE.findall(res) if "supervisor report" in e["text"] else _RENDER_HTML.findall(res)
                            refs = [_file_ref(m, d.get("cwd")) for m in found]
                            e["files"] = [f for f in refs if f] or None
            rel = relayed(d.get("origin"))
            if rel:
                out.append(dict(_entry(f"{uid}.0", "user", rel[0], at), origin=rel[1]))
                continue
            if d.get("isMeta") and not d.get("isCompactSummary"):
                # afdc2b (14/10): un task programmato (CronCreate) passa dalla coda e arriva come riga meta: la sua voce
                # in coda e' entrata nel turno. Restava queued per sempre, l'app ancorava la chat li' e non vedeva piu'
                # le risposte dopo.
                t, _ = _clean(content)
                t, _ = _origin_of(t)
                if queued.get(t):
                    unqueue(queued[t][0])
                continue
            if not _human(d):
                continue
            human_entry(f"{uid}.0", content, at)
        elif kind == "attachment":
            a = d.get("attachment") or {}
            o = a.get("origin")
            rel = relayed(o) if a.get("type") == "queued_command" else None
            if rel:
                out.append(dict(_entry(f"{uid}.0", "user", rel[0], at), origin=rel[1]))
                continue
            if a.get("type") == "queued_command" and isinstance(o, dict) and o.get("kind") == "human":
                human_entry(f"{uid}.0", a.get("prompt"), at)
        elif kind == "assistant" and isinstance(content, list):
            u = (d.get("message") or {}).get("usage") or {}
            tokens[0] += int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
            tokens[1] += int(u.get("output_tokens") or 0)
            for i, b in enumerate(content):
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and str(b.get("text") or "").strip():
                    out.append(_entry(f"{uid}.{i}", "assistant", b["text"].strip(), at))
                elif b.get("type") == "tool_use":
                    name = str(b.get("name") or "?")
                    detail = tool_line(name, b.get("input"), width=200)[len(name):].strip()   # il nome e' gia' in `tool`
                    if not detail and isinstance(b.get("input"), dict) and b["input"].get("caption"):
                        detail = " ".join(str(b["input"]["caption"]).split())[:200]   # SendUserFile: la didascalia
                    e = _entry(f"{uid}.{i}", "tool", detail, at, tool=name, note=tool_note(b.get("input")))
                    e["files"] = _files_of(name, b.get("input"), d.get("cwd")) or None
                    out.append(e)
                    if b.get("id"):
                        by_tool[b["id"]] = e
        elif kind == "queue-operation" and d.get("operation") == "enqueue" and isinstance(d.get("content"), str):
            # scritto nella chat (terminale o app Claude) mentre il turno gira: in coda da subito, entra nel turno dopo
            t, _ = _clean(d["content"])
            t, origin = _origin_of(t)
            if t and origin == "pc":
                e = dict(_entry(f"q{int(_turn_epoch(d.get('timestamp')) * 1000)}", "user", t, at), origin=origin, queued=True)
                out.append(e)
                queued.setdefault(t, []).append(e)
                waiting_q.append(e)
                qkey[id(e)] = t
        elif kind == "queue-operation" and d.get("operation") in ("dequeue", "remove"):
            # tolta dalla coda: entra nel turno o e' stata cancellata. Se nessuna riga dopo la riconosce, a fine turno
            # non e' piu' in coda (afdc2b): una voce queued per sempre bloccava l'ancora della chat nell'app.
            waiting_q[:] = [x for x in waiting_q if x["queued"]]
            if waiting_q:
                taken.append(waiting_q.pop(0))
        elif kind == "system" and d.get("subtype") == "turn_duration":
            for e in taken:
                if e["queued"]:
                    unqueue(e)
            taken.clear()
            last = next((e for e in reversed(out) if e["role"] != "user"), None)
            if last is not None and last["turn"] is None and at:
                ms = int(d.get("durationMs") or 0)
                last["turn"] = {"started": at - ms // 1000 if ms else None, "ended": at, "in": tokens[0] or None, "out": tokens[1] or None}
            tokens = [0, 0]
    return [e for e in out if e["role"] != "tool"] if chat_only else out


def transcript_events(path, offset):
    """Gli eventi del transcript (jsonl di Claude Code) scritti dopo `offset` byte: ("user", testo),
    ("text", testo assistant), ("tool", nome, input). Torna (eventi, nuovo offset). Righe rotte ignorate."""
    events = []
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return events, offset
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        kind = d.get("type")
        content = (d.get("message") or {}).get("content")
        if kind == "user":
            if isinstance(content, str):
                events.append(("user", content))
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                        events.append(("user", b["text"]))
        elif kind == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text"):
                    events.append(("text", b["text"]))
                elif b.get("type") == "tool_use":
                    events.append(("tool", str(b.get("name") or "?"), b.get("input")))
    return events, offset + len(data)
