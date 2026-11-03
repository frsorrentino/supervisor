#!/usr/bin/env python3
"""supervisor task — il registro dei compiti (fase 1, 03/10/2026).

  supervisor task defect-of ID ID_CORRETTO   defect_of: ID corregge un difetto di ID_CORRETTO (come task add --defect-of)
  supervisor task pagella                    per modello e tipo di lavoro, quanti «fatto» hanno poi avuto un difetto; JSON
  supervisor task add FILE|- [--topic T]     un contratto {schema_version, task}: validato, con i vincoli
                                                pertinenti allegati (stesso progetto, stesso argomento)
  supervisor task list [--state S] [--plan P] [--json]
  supervisor task show ID [--json]
  supervisor task start ID [--session NOME]  in corso (la guardia della corsia chiusa lo cerca)
  supervisor task result ID FILE|-           l'esito che la sessione manda indietro (non e' accettazione):
                                                {"task": ID, "attempt": N, "outcome": "green|red", "reason": "…",
                                                 "proof": {"kind": "check|commit|link", "value": "…"}, "at": EPOCH}
  supervisor task check ID [--json]           riesegue il controllo senza cambiare niente: exit 0 verde, 1 rosso
  supervisor task done ID [--constraint T]   riesegue il controllo: verde = fatto; rosso = un tentativo in
                                                piu', e al terzo «fallito». Exit 0 verde, 1 rosso
  supervisor task wait-ok ID --what W --where D [--options "A|B"] [--untested T] [--undo si|no|in-parte] [--undo-how T] [--also ID2,ID3]
                                                attende ok; --also (1.99): i compiti a parte previsti dal piano, elencati nella
                                                carta; l'ok della carta arriva a loro citandola, e una regola permanente non la
                                                approva mai; 1.99 (ok informato): --untested cosa non e' provato, --undo se si
                                                torna indietro, --undo-how come; senza, la card dice «non dichiarato»; (corsia chiusa fuori da un piano
                                                approvato); --options (1.81, 23/10): il compito chiede una scelta, da
                                                2 a 4 etichette di 1-24 caratteri: l'app mostra un tasto per opzione e
                                                l'ok arriva con --choice; una regola permanente non sceglie mai
  supervisor task approve ID --by B --inbox MSG [--text NOTA] [--what W] [--where D] [--choice C]
                                                da una sessione Claude l'ok a nome di una persona passa cosi': la
                                                sessione che ha ricevuto l'ok (casella sua: supervisor inbox NOME --all)
                                                lo registra per qualunque compito, anche di un'altra sessione (23/10
                                                18:53: «se io scrivo un ok è sempre valido»); vale un messaggio
                                                dell'ultima ora scritto nel suo terminale (origin terminal, dalla
                                                trascrizione) o arrivato dal relay; mai cron o session; un record ancora
                                                unverified si aspetta fino a 15 s, poi «try again» (la sessione riprova)
  supervisor task approve ID --by B --pin P [--text NOTA] [--what W] [--where D] [--choice C]
                                                facoltativo (23/10 17:14): con il PIN della persona (supervisor pin set)
                                                l'ok vale anche da una shell; il PIN non si scrive da nessun'altra parte
  supervisor task tidy [--dry-run] [--json] | --archive | --today   1.113: le voci superate escono da sole da Tocca a te
                                                («superata», mai «confermata»): 1 prova di una build che una pre-release piu'
                                                nuova dello stesso progetto contiene; 2 pausa o richiesta di un compito chiuso;
                                                3 richiesta doppia sullo stesso compito (resta la piu' recente); 4 prova senza
                                                passi da 7 giorni, compito non in corso: in archivio. Le attese di un'azione
                                                della persona non scadono mai da sole
  supervisor task auto-close [--hours 6] [--json]   chiude i compiti avviati, non in pausa, aperti da piu' di 6 ore col controllo verde
  supervisor task cancel ID
  supervisor task board [--json] [--timeline [--since 6h]]   stati ed esiti dal codice; con --timeline la cronologia
  supervisor task constraint add (--project P | --topic T) TESTO
  supervisor task constraint list [--project P] [--topic T]

Impegni (22/10/2026: un compito nato da un ok resta visibile finche' il suo esito non e' verde):
  supervisor task impegni [--json] [--stalled] [--brief]   gli approvati non compiuti (e chi aspetta un ok), con eta',
                                                ultimo passo, prossimo passo, chi aspetta chi, pausa; --brief per l'hook
  supervisor task step ID "testo" [--next "prossimo passo"] [--by B]   un passo fatto (e il prossimo)
  supervisor task pause ID --reason R [--by B] [--waits person|external] [--recheck 2d|ISO] [--since EPOCH|ISO]
                       [--cause C] [--undo si|no|in-parte] [--undo-how T] [--al-pc|--da-qui]
                       [--prova [--release TAG] [--web VERSIONE]]
                                                1.107: --prova = la persona deve provare qualcosa (con --waits person):
                                                il tag della pre-release e la versione web, se ci sono; l'app la mostra
                                                fra le prove e la chiude con «Tutto va» o «Non va»
                                                con --waits person il motivo dice in parole cosa si chiede alla persona, mai solo «aspetta l'ok»: la card lo mostra
                                                1.99: --cause la stessa causa per piu' compiti (senza, vale il testo della
                                                pausa); --al-pc / --da-qui dove si puo' fare (senza, dalle parole del motivo)
                                                «salvo altre priorita'», visibile, mai in allarme; --waits person = la pausa
  supervisor task resume ID                     aspetta l'utente (waits.on = person), --since = l'inizio vero (contratto 1.77)
                                                --waits external = aspetta fuori (un ticket, un cliente) fino a --recheck,
                                                poi `suggest fill` lo ripropone (1.88)
  supervisor task closed [AAAA-MM-GG] [--json]  i compiti chiusi quel giorno (oggi) con la prova (release, commit, file o
                                                controllo) e i sei giorni prima con un conteggio; exit 3 senza registro
  supervisor task amend ID [--check CMD] [--timeout S] [--outcome TESTO] [--kind K] [--proof P]
                                                corregge il contratto di un compito aperto (validato di nuovo)
  supervisor task short ID "testo" [--by B]     il titolo breve (task.short, 1-28 caratteri, una riga nell'app: contratto
                                                1.79); senza, impegni e closed ne ricavano uno dal titolo (parole intere)
  supervisor task confirm ID --by B (--inbox MSG | --pin P)   la conferma della persona (come approve); `task
  supervisor task confirmed ID                  confirmed ID` esce 0 se c'e': un controllo valido per un esito install
  supervisor task standing add --by B --inbox MSG --project DIR --kind K
                                                (23/10 20:00, «Quando ci sono aggiornamenti di supervisor, effettuali
                                                direttamente»): un ok permanente della persona per i compiti di DIR
                                                con outcome.kind K; cita un messaggio della persona nella casella di
                                                chi chiama (origin terminal o relay, senza limite d'ora); al wait-ok
                                                il compito coperto si approva da solo citando messaggio e regola
  supervisor task standing list [--json] | remove N   le regole permanenti; la revoca (resta nel registro)
  supervisor task stall-check [--json]          gli impegni fermi da piu' di tasks.stall_hours non ancora avvisati
                                                (task-alerts.json accanto al registro); lo chiama guard run

Fase A del programma del 01/11 (contratto 1.99):
  supervisor task prova ID --build TAG --dove SCHERMATA --fai T --atteso T [--by B]
                                SCHERMATA con «Portami lì» nell'app: queue, box-aspetta, box-lavoro, box-esiti, lavori, launch, night, search, settings, homeorder, usage, home;
                                le altre si scrivono ma non portano li'. Dal web si puo' dare ok anche a una build Android
                                (il device resta nel registro): una prova che vale solo sul telefono lo dice in --fai
                                                una prova per la persona, scritta da chi fa la release: mai credenziali ne' URL
                                                privati. Una build nuova ritira le prove di prima del compito; stessa build,
                                                dove e fai sostituisce. L'esito nasce solo da un tocco sul dispositivo (op prova)
  supervisor task prove [--json]                le prove da fare per build, quelle che non vanno, e i compiti con tutte le prove
                                                ok e il controllo rosso
  supervisor task settle ID                     tutte le prove ok: riesegue il controllo, verde chiude; rosso resta aperto, lo
                                                dice a sessione e master e non conta tentativi. Altrimenti exit 3, non cambia niente
  supervisor task decisioni [--json]            cio' che aspetta la persona per causa: pause, ok, needs-you; le fattibili da qui
                                                prima, poi le piu' pesanti (peso = quanti compiti sblocca)
  supervisor task cause ID [ID...] --cause C [--note FRASE] [--by B]
                                                unisce pause con la stessa causa scritta in parole diverse e dice cosa sbloccano
  supervisor task add FILE|- --defect-of ID     il compito corregge un difetto di ID: il precedente sulle card dell'ok

Il contratto e' schemas/task-contract.v1.json, condiviso con fable-director (copiato, mai importato). Il
registro e' SQLite in ~/.config/cc-supervisor/tasks.db (CM_TASKS_DB per i test). Nessun «fatto» a giudizio:
solo il controllo rieseguito e verde. Senza registro nessun altro comando di supervisor cambia.
Esito atteso (task.outcome, facoltativo): le parole dell'ok e un kind; il controllo deve toccarlo (OUTCOME_MARKERS),
altrimenti add e done lo rifiutano: un compito non si chiude su un passo intermedio.
"""
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE.parent / "schemas" / "task-contract.v1.json"
STATES = ("proposed", "assigned", "running", "awaiting_ok", "approved", "done", "failed", "cancelled")
OPEN_STATES = ("proposed", "assigned", "running", "awaiting_ok", "approved")
PROOF_TAIL = 12
# 22/10/2026 (P1, dalla giornata persa sull'APK del Gantt): per ogni kind di esito, i segni che il controllo deve
# contenere per toccare l'ultimo passo e non uno intermedio; custom non ne ha e vuole `proof`
OUTCOME_MARKERS = {
    "release": ("gh release", "git tag", "tags/", "releases/", "version", "ls-remote", "api.github.com"),
    "push": ("origin/", "ls-remote", "@{u}", "merge-base", "rev-list"),
    "merge": ("merge-base", "is-ancestor", "origin/"),
    "install": ("dumpsys", "lastupdatetime", "pm list", "pm path", "versioncode", "task confirmed"),
    "deploy": ("curl", "http://", "https://", "firebase", "wget", "ssh "),
    "custom": (),
}
STALL_HOURS = 2
STEP_RANK = {"state": 0, "approval": 1, "result": 2, "amend": 3, "step": 3, "pause": 4, "commit": 5}
SHORT_MAX = 28   # contratto 1.79: il titolo breve, una riga nell'app


def short_of(task):
    """task.short, o un ripiego dal titolo: parole intere fino a SHORT_MAX, mai «…» (cm-core.short_title)."""
    s = " ".join(str(task.get("short") or "").split())
    return s[:SHORT_MAX] if s else _load("cm-core").short_title(task.get("title") or "", SHORT_MAX)


# ------------------------------------------------------------------ schema
def schema():
    return json.loads(SCHEMA_PATH.read_text())


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def validate(value, node=None, root=None, path="$"):
    """Gli errori (lista di stringhe) di `value` rispetto allo schema: solo le parole chiave che lo schema usa
    (type, const, enum, required, properties, additionalProperties false, items, min/maxLength, minimum,
    maximum, minItems, pattern, $ref locale). Nessuna dipendenza: il plugin gira con python3 nudo."""
    root = root or schema()
    node = root if node is None else node
    if "$ref" in node:
        node = root["$defs"][node["$ref"].rsplit("/", 1)[-1]]
    errs = []
    t = node.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_is(value, x) for x in ts):
            return [f"{path}: expected {'|'.join(ts)}"]
    if "const" in node and value != node["const"]:
        errs.append(f"{path}: must be {node['const']!r}")
    if "enum" in node and value not in node["enum"]:
        errs.append(f"{path}: one of {', '.join(map(str, node['enum']))}")
    if isinstance(value, str):
        if len(value) < node.get("minLength", 0) or len(value) > node.get("maxLength", 10 ** 9):
            # 03/11 (observe supervisor-857757be): con i limiti, perche' chi scrive sappia di quanto accorciare
            mx = node.get("maxLength")
            errs.append(f"{path}: length out of range ({len(value)}, allowed {node.get('minLength', 0)}-{mx})" if mx is not None
                        else f"{path}: length out of range ({len(value)}, at least {node.get('minLength', 0)})")
        if "pattern" in node and not re.search(node["pattern"], value):
            errs.append(f"{path}: does not match {node['pattern']}")
    if _is(value, "number"):
        if "minimum" in node and value < node["minimum"] or "maximum" in node and value > node["maximum"]:
            errs.append(f"{path}: out of range")
    if isinstance(value, list):
        if len(value) < node.get("minItems", 0):
            errs.append(f"{path}: at least {node['minItems']} item(s)")
        for i, x in enumerate(value):
            errs += validate(x, node.get("items") or {}, root, f"{path}[{i}]")
    if isinstance(value, dict):
        props = node.get("properties") or {}
        for k in node.get("required") or []:
            if k not in value:
                errs.append(f"{path}.{k}: required")
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], root, f"{path}.{k}")
            elif node.get("additionalProperties") is False:
                errs.append(f"{path}.{k}: not allowed")
    return errs


def check_syntax_errors(task):
    """31/10: il controllo gira con /bin/sh (shell=True). Provato a mano in bash passava, anche con <(...), e cadeva solo
    a `task done` con «Syntax error»: lo si dice all'ingresso. Solo la sintassi, niente viene eseguito."""
    cmd = str((task.get("check") or {}).get("cmd") or "")
    try:
        p = subprocess.run(["/bin/sh", "-n", "-c", cmd], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if p.returncode == 0:
        return []
    why = (p.stderr.strip().splitlines() or ["syntax error"])[-1]
    return [f"$.task.check.cmd: the check runs with /bin/sh, which refuses it ({why}); for bash syntax wrap it in bash -c '…'"]


def outcome_errors(task):
    """P1 (22/10): il controllo deve toccare l'esito atteso. [] senza outcome (il compito vale come prima)."""
    oc = task.get("outcome")
    if not oc:
        return []
    kind = oc.get("kind")
    if kind == "custom":
        return [] if str(oc.get("proof") or "").strip() else ["$.task.outcome.proof: required with kind custom"]
    marks = OUTCOME_MARKERS.get(kind) or ()
    cmd = str((task.get("check") or {}).get("cmd") or "").lower()
    if any(m in cmd for m in marks):
        return []
    return [f"$.task.check.cmd: does not touch the outcome (kind {kind}): expected one of " + ", ".join(marks)]


# ------------------------------------------------------------------ registro
def db_path():
    p = os.environ.get("CM_TASKS_DB")
    if p:
        return Path(p)
    c = (os.environ.get("CC_SUPERVISOR_CONFIG") or os.environ.get("TEAM_SUPERVISOR_CONFIG") or os.environ.get("CLAUDE_MASTER_CONFIG"))
    base = Path(c).parent if c else Path.home() / ".config" / "cc-supervisor"
    return base / "tasks.db"


SQL = """
CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, title TEXT, file TEXT, approved_by TEXT, approved_at INTEGER,
    approved_text TEXT, created_at INTEGER);
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, plan TEXT, project TEXT, session TEXT, title TEXT, lane TEXT,
    state TEXT, contract TEXT, created_at INTEGER, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS results (n INTEGER PRIMARY KEY, task TEXT, attempt INTEGER, outcome TEXT, reason TEXT,
    proof TEXT, perimeter TEXT, at INTEGER, accepted INTEGER);
CREATE TABLE IF NOT EXISTS approvals (n INTEGER PRIMARY KEY, task TEXT, plan TEXT, by_ TEXT, at INTEGER, text TEXT,
    what TEXT, where_ TEXT, options TEXT, choice TEXT);
CREATE TABLE IF NOT EXISTS constraints (n INTEGER PRIMARY KEY, project TEXT, topic TEXT, text TEXT, source TEXT,
    at INTEGER);
CREATE TABLE IF NOT EXISTS steps (n INTEGER PRIMARY KEY, task TEXT, at INTEGER, by_ TEXT, kind TEXT, text TEXT, next TEXT);
CREATE TABLE IF NOT EXISTS pauses (n INTEGER PRIMARY KEY, task TEXT, at INTEGER, by_ TEXT, reason TEXT, resumed_at INTEGER);
CREATE TABLE IF NOT EXISTS standing (n INTEGER PRIMARY KEY, project TEXT, kind TEXT, by_ TEXT, text TEXT, inbox_id TEXT,
    created_at INTEGER, revoked_at INTEGER);
CREATE TABLE IF NOT EXISTS prove (n INTEGER PRIMARY KEY, task TEXT, build TEXT, dove TEXT, fai TEXT, atteso TEXT, at INTEGER, by_ TEXT,
    result TEXT, result_at INTEGER, result_by TEXT, result_text TEXT, superseded_at INTEGER);
CREATE TABLE IF NOT EXISTS defects (task TEXT PRIMARY KEY, defect_of TEXT, at INTEGER);
CREATE TABLE IF NOT EXISTS closures (task TEXT PRIMARY KEY, at INTEGER, model TEXT, effort TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS tidy (n INTEGER PRIMARY KEY, at INTEGER, task TEXT, kind TEXT, rule INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS decision_snooze (key TEXT PRIMARY KEY, at INTEGER, by_ TEXT, until INTEGER, refs TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS cause_notes (key TEXT PRIMARY KEY, note TEXT, at INTEGER, by_ TEXT);
"""


def connect(create=True):
    p = db_path()
    if not create and not p.exists():
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SQL)
    cols = {r[1] for r in con.execute("PRAGMA table_info(pauses)")}   # 1.77 (22/10): una pausa che aspetta la persona, dall'inizio vero
    if "waits" not in cols:
        con.execute("ALTER TABLE pauses ADD COLUMN waits TEXT")
        con.execute("ALTER TABLE pauses ADD COLUMN since INTEGER")
        con.commit()
    if "recheck" not in cols:   # 1.88 (27/10): un'attesa esterna e quando ricontrollarla
        con.execute("ALTER TABLE pauses ADD COLUMN recheck INTEGER")
        con.commit()
    if "cause" not in cols:   # 1.99 (01/11): la causa comune, se si torna indietro, dove si puo' fare
        for c in ("cause", "undo", "undo_how", "at_pc"):
            con.execute(f"ALTER TABLE pauses ADD COLUMN {c} TEXT")
        con.commit()
    if "prova" not in cols:   # 1.107 (03/11): la pausa aspetta una prova della persona: {release, web} in JSON
        con.execute("ALTER TABLE pauses ADD COLUMN prova TEXT")
        con.commit()
    cols = {r[1] for r in con.execute("PRAGMA table_info(approvals)")}   # 1.81 (23/10): le opzioni di una scelta e quella presa
    if "options" not in cols:
        con.execute("ALTER TABLE approvals ADD COLUMN options TEXT")
        con.execute("ALTER TABLE approvals ADD COLUMN choice TEXT")
        con.commit()
    if "untested" not in cols:   # 1.99 (01/11, ok informato)
        for c in ("untested", "undo", "undo_how"):
            con.execute(f"ALTER TABLE approvals ADD COLUMN {c} TEXT")
        con.commit()
    if "also" not in cols:   # 1.99 (01/11, compito dal piano)
        con.execute("ALTER TABLE approvals ADD COLUMN also TEXT")
        con.commit()
    return con


def now():
    return int(time.time())


def get_task(con, tid):
    r = con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not r:
        sys.exit(f"task: no task {tid}")
    return r


def set_state(con, tid, state):
    con.execute("UPDATE tasks SET state=?, updated_at=? WHERE id=?", (state, now(), tid))


def constraints_for(con, project, topic):
    rows = con.execute("SELECT text FROM constraints WHERE (project IS NOT NULL AND project=?) OR (topic IS NOT NULL AND topic=?) "
                       "ORDER BY n", (project, topic or "")).fetchall()
    return [r["text"] for r in rows]


def _live_tmux():
    try:
        r = subprocess.run(["tmux"] + os.environ.get("CM_TMUX_ARGS", "").split() + ["list-sessions", "-F", "#{session_name}"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(r.stdout.split()) if r.returncode == 0 else set()


def tmux_session(name):
    """Il nome tmux di una sessione scritta col nome breve. `supervisor sessions` mostra il nome senza il prefisso
    dell'account («internationalyoungclub-com» per «pix-internationalyoungclub-com»): il 02/11 una sessione l'ha
    copiato nel contratto, e talk, casella e ok la cercavano con un nome che non c'e' (talk: «is not running»). Si
    corregge solo se il nome non e' una sessione viva e una sola viva e' prefisso+nome; altrimenti resta com'e'."""
    if not name:
        return name
    live = _live_tmux()
    if name in live:
        return name
    pfx = [a.get("tmux_prefix") for a in ((_cfg()[1] or {}).get("accounts") or {}).values() if a.get("tmux_prefix")]
    hits = [p + name for p in pfx if not name.startswith(p) and p + name in live]
    return hits[0] if len(hits) == 1 else name


def add(con, contract, topic=None):
    """(id, errori). Il contratto entra solo se valido; i vincoli pertinenti si aggiungono a task.constraints."""
    errs = validate(contract)
    if not errs:
        errs = outcome_errors(contract["task"])   # P1: un controllo che non tocca l'esito non entra
    if not errs:
        errs = check_syntax_errors(contract["task"])
    if errs:
        return None, errs
    t = contract["task"]
    t["where"]["session"] = tmux_session(t["where"]["session"])
    if con.execute("SELECT 1 FROM tasks WHERE id=?", (t["id"],)).fetchone():
        return None, [f"$.task.id: {t['id']} already in the registry"]
    have = list(t.get("constraints") or [])
    t["constraints"] = have + [c for c in constraints_for(con, t["where"]["project"], topic) if c not in have]
    if not t["constraints"]:
        t.pop("constraints")
    state = "assigned" if t["where"]["session"] else "proposed"
    con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                (t["id"], t["plan"], t["where"]["project"], t["where"]["session"], t["title"], t["lane"], state,
                 json.dumps(contract, ensure_ascii=False), now(), now()))
    con.commit()
    return t["id"], []


def attempts(con, tid):
    return con.execute("SELECT COUNT(*) FROM results WHERE task=? AND accepted IS NOT NULL", (tid,)).fetchone()[0]


def check_env():
    """L'ambiente del controllo: quello di chi lo lancia, con ~/.local/bin e /usr/local/bin nel PATH. Dal cron, dal
    demone del relay e dagli hook il PATH e' /usr/bin:/bin e `supervisor`, `claude`, `gh` non si trovano: un controllo
    giusto usciva 127 e il compito restava rosso (02/11, aggirato col percorso intero; stessa regola di cm-lib.sh, T81)."""
    env = dict(os.environ)
    have = env.get("PATH", "").split(":")
    add = [d for d in (os.path.expanduser("~/.local/bin"), "/usr/local/bin") if d not in have]
    env["PATH"] = ":".join(add + [d for d in have if d])
    return env


def run_check(task):
    """(rc, coda dell'uscita): il controllo del contratto, nella sua cartella e col suo timeout."""
    chk = task["check"]
    cwd = chk.get("cwd") or task["where"]["project"]
    try:
        p = subprocess.run(chk["cmd"], shell=True, cwd=cwd, capture_output=True, text=True, timeout=chk["timeout_s"], env=check_env())
        out = (p.stdout + p.stderr).rstrip().splitlines()
        return p.returncode, "\n".join(out[-PROOF_TAIL:])
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {chk['timeout_s']} s"
    except OSError as e:
        return 127, str(e)


def plain_runtime(rt):
    """Dal vivo, 02/11: `model` e' {id, label}, `effort` una stringa o a sua volta un oggetto; nel registro vanno gli id."""
    def one(v):
        v = v.get("id") if isinstance(v, dict) else v
        return str(v) if v else None
    return one(rt.get("model")), one(rt.get("effort"))


def closing_runtime(session):
    """(modello, effort) della sessione del compito, dalla sua trascrizione; (None, None) se non si sa: mai dedotto."""
    if not session:
        return None, None
    try:
        out = subprocess.run([str(Path(__file__).resolve().parent / "supervisor"), "sessions", "--json"], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL).stdout
        row = next((r for r in json.loads(out) if r.get("name") == session or r.get("tmux") == session), None)
        if not row:
            return None, None
        rt = _load("cm-core").session_runtime(row) or {}
        return plain_runtime(rt)
    except Exception:   # noqa: BLE001 — un extra: mai far fallire la chiusura
        return None, None


PAGELLA_MIN = 10   # sotto i 10 casi niente numeri


def pagella(con):
    """1.103: per modello e tipo di lavoro, quanti «fatto» hanno poi avuto un difetto (un legame defect_of vero). Sotto i 10
    casi `defects` e `rate` sono null. `advice`: solo con una differenza vera sullo stesso tipo, due proporzioni a z >= 1.96."""
    cells = {}
    for r in con.execute("SELECT c.task, c.model, c.kind, c.effort FROM closures c JOIN tasks t ON t.id=c.task WHERE t.state='done' AND c.model IS NOT NULL"):
        x = cells.setdefault((r["model"], r["kind"] or "?"), {"n": 0, "defects": 0, "efforts": {}})
        x["n"] += 1
        x["defects"] += 1 if con.execute("SELECT 1 FROM defects WHERE defect_of=?", (r["task"],)).fetchone() else 0
        if r["effort"]:
            x["efforts"][r["effort"]] = x["efforts"].get(r["effort"], 0) + 1
    rows = []
    for (model, kind), x in sorted(cells.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        ok = x["n"] >= PAGELLA_MIN
        rows.append({"model": model, "kind": kind, "n": x["n"], "enough": ok, "defects": x["defects"] if ok else None,
                     "rate": round(x["defects"] / x["n"], 3) if ok else None, "efforts": x["efforts"]})
    advice = []
    enough = [r for r in rows if r["enough"]]
    for a in enough:
        for b in enough:
            if a["kind"] != b["kind"] or a["rate"] >= b["rate"]:
                continue
            p = (a["defects"] + b["defects"]) / (a["n"] + b["n"])
            se = (p * (1 - p) * (1 / a["n"] + 1 / b["n"])) ** 0.5
            if se and (b["rate"] - a["rate"]) / se >= 1.96:
                advice.append({"kind": a["kind"], "better": a["model"], "worse": b["model"], "rate_better": a["rate"], "rate_worse": b["rate"]})
    first = con.execute("SELECT MIN(at) a FROM closures").fetchone()["a"]
    unknown = con.execute("SELECT COUNT(*) c FROM closures WHERE model IS NULL").fetchone()["c"]
    return {"since": first, "min": PAGELLA_MIN, "rows": rows, "advice": advice, "unknown_model": unknown}


def done(con, tid, constraint=None, checked=None):
    """Accettazione dal codice: riesegue il controllo (`checked` = il suo esito appena preso, per non rifarlo). (verde, result)."""
    row = get_task(con, tid)
    if row["state"] not in OPEN_STATES:
        sys.exit(f"task {tid}: already {row['state']}")
    task = json.loads(row["contract"])["task"]
    oe = outcome_errors(task)
    if oe:   # P1: entrato prima della regola o cambiato: niente done su un passo intermedio (task amend lo corregge)
        print("\n".join(oe), file=sys.stderr)
        sys.exit(3)
    rc, tail = checked or run_check(task)
    n = attempts(con, tid) + 1
    green = rc == 0
    last = (tail.splitlines() or [""])[-1][:300]
    res = {"task": tid, "attempt": min(n, 3), "outcome": "green" if green else "red",
           "reason": "check green" if green else f"check exit {rc}: {last or 'no output'}",
           "proof": {"kind": "check", "value": f"$ {task['check']['cmd']}\n{tail}\n(exit {rc})", "rc": rc}, "at": now()}
    con.execute("INSERT INTO results (task, attempt, outcome, reason, proof, perimeter, at, accepted) VALUES (?,?,?,?,?,?,?,1)",
                (tid, res["attempt"], res["outcome"], res["reason"], json.dumps(res["proof"], ensure_ascii=False), None, res["at"]))
    if green:
        set_state(con, tid, "done")
        model, effort = closing_runtime(row["session"])   # 1.103: la pagella parte da qui, all'indietro solo dove il legame e' certo
        con.execute("INSERT OR REPLACE INTO closures (task, at, model, effort, kind) VALUES (?,?,?,?,?)", (tid, res["at"], model, effort, task.get("type")))
    elif n >= task["attempts_max"]:
        set_state(con, tid, "failed")
    if constraint or (not green and n >= task["attempts_max"]):
        con.execute("INSERT INTO constraints (project, topic, text, source, at) VALUES (?,?,?,?,?)",
                    (task["where"]["project"], None, constraint or f"{task['title']}: {res['reason']}", tid, now()))
    con.commit()
    return green, res


def record_result(con, tid, result):
    """L'esito mandato dalla sessione: registrato, mai accettazione (accepted NULL); lo stato non cambia."""
    get_task(con, tid)
    errs = validate(result, {"$ref": "#/$defs/result"})
    if errs:
        return errs
    if result["task"] != tid:
        return [f"$.task: {result['task']} is not {tid}"]
    con.execute("INSERT INTO results (task, attempt, outcome, reason, proof, perimeter, at, accepted) VALUES (?,?,?,?,?,?,?,NULL)",
                (tid, result["attempt"], result["outcome"], result["reason"], json.dumps(result["proof"], ensure_ascii=False),
                 json.dumps(result.get("perimeter")) if result.get("perimeter") else None, result["at"]))
    con.commit()
    return []


OPTIONS_MAX, OPTION_LEN = 4, 24   # 1.81 (23/10): le opzioni di una scelta, un tasto ciascuna nell'app


def parse_options(s):
    """1.81 (23/10/2026, supervisor-app «scelte con tasti»): «A|B» → ["A", "B"]: da 2 a 4 etichette distinte di 1-24
    caratteri, senza «|» ne' a capo. L'app mostra un tasto per etichetta al posto di «Approva»."""
    opts = [" ".join(o.split()) for o in str(s or "").split("|")]
    if not 2 <= len(opts) <= OPTIONS_MAX or any(not o or len(o) > OPTION_LEN for o in opts) or len(set(opts)) != len(opts):
        sys.exit(f"task wait-ok --options: 2 to {OPTIONS_MAX} distinct labels of 1-{OPTION_LEN} characters separated by «|», e.g. \"Strada A|Strada B\"")
    return opts


def request_options(con, tid):
    """Le opzioni dell'ultima richiesta di ok del compito (wait-ok --options), o None."""
    a = con.execute("SELECT options FROM approvals WHERE task=? AND by_ IS NULL ORDER BY n DESC LIMIT 1", (tid,)).fetchone()
    return json.loads(a["options"]) if a and a["options"] else None


def parse_also(con, tid, s):
    """wait-ok --also: «ID2,ID3», compiti che esistono gia' e sono aperti; esce con il motivo. Dichiararli non scrive nessun ok."""
    ids = list(dict.fromkeys(x.strip() for x in str(s).split(",") if x.strip()))
    if not ids:
        sys.exit("task wait-ok --also: the ids of the tasks, separated by commas")
    for x in ids:
        if x == tid:
            sys.exit(f"task wait-ok --also: {x} is the task itself")
        if get_task(con, x)["state"] not in OPEN_STATES:
            sys.exit(f"task wait-ok --also: {x} is closed")
    return ids


def request_also(con, tid):
    """I compiti dichiarati nell'ultima carta del compito, con l'ora della carta: ([id], at)."""
    a = con.execute("SELECT also, at FROM approvals WHERE task=? AND by_ IS NULL ORDER BY n DESC LIMIT 1", (tid,)).fetchone()
    return (json.loads(a["also"]), a["at"]) if a and a["also"] else ([], None)


def also_approve(con, parent, ids, by, text):
    """L'ok della carta ai compiti che dichiarava: cita il padre. Niente catena (un figlio non passa l'ok ai suoi), niente
    scelta al posto della persona, e un compito chiuso o gia' approvato resta com'e'."""
    for x in ids:
        r = con.execute("SELECT plan, state, session FROM tasks WHERE id=?", (x,)).fetchone()
        if not r or r["state"] not in OPEN_STATES or request_options(con, x) \
                or con.execute("SELECT 1 FROM approvals WHERE task=? AND by_ IS NOT NULL AND (what IS NULL OR what<>'confirmed')", (x,)).fetchone():
            continue
        con.execute("INSERT INTO approvals (task, plan, by_, at, text) VALUES (?,?,?,?,?)", (x, r["plan"], by, now(), f"ok of {parent}, whose card lists this task: {text}"[:900]))
        if r["state"] == "awaiting_ok":
            set_state(con, x, "approved")
        if r["session"]:
            _talk(r["session"], M("tasks.also_ok", task=x, parent=parent))


def approve(con, tid, by, text, what=None, where=None, choice=None, ok_at=None, inherit=True):
    """`ok_at` = quando la persona ha scritto l'ok, se non adesso (--inbox): il collegamento vale solo se la carta era gia' li'.
    `inherit` False: l'ok vale per il padre solo (un'app che non ha mostrato l'elenco)."""
    row = get_task(con, tid)
    also, card_at = request_also(con, tid)
    also = also if also and inherit and row["state"] == "awaiting_ok" and (ok_at is None or card_at <= ok_at) else []
    opts = request_options(con, tid)   # 1.81: un compito che chiede una scelta si approva con una delle sue opzioni
    if opts and choice not in opts:
        sys.exit(f"task approve {tid}: " + (f"choice {choice} not among {', '.join(opts)}" if choice else f"choose one of {', '.join(opts)} (--choice)"))
    if choice and not opts:
        sys.exit(f"task approve {tid}: no options to choose from")
    con.execute("INSERT INTO approvals (task, plan, by_, at, text, what, where_, choice) VALUES (?,?,?,?,?,?,?,?)",
                (tid, row["plan"], by, now(), text, what, where, choice))
    if row["state"] == "awaiting_ok":
        set_state(con, tid, "approved")
    also_approve(con, tid, also, by, text)
    con.commit()


def confirm(con, tid, by, text):
    """P1 (22/10): la conferma della persona («lo vedo sul telefono»); `task confirmed ID` la legge: e' un controllo valido per
    un esito install. Lo stato non cambia."""
    con.execute("INSERT INTO approvals (task, plan, by_, at, text, what, where_) VALUES (?,?,?,?,?,'confirmed',NULL)",
                (tid, get_task(con, tid)["plan"], by, now(), text))
    con.commit()


INBOX_MAX_AGE_S = 3600
INBOX_WAIT_S = float(os.environ.get("CM_INBOX_WAIT_S") or 15)   # 23/10 18:53: attesa della riga della trascrizione
AUTO_CLOSE_H = 6


def _caller_session():
    """Il nome tmux della sessione che chiama (TMUX_PANE), o ''."""
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return ""
    try:
        r = subprocess.run(["tmux"] + os.environ.get("CM_TMUX_ARGS", "").split() + ["display-message", "-p", "-t", pane, "#{session_name}"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def inbox_approval_text(by, rec_id, note=None, task_session=None):
    """L'ok a nome della persona registrato da una sessione Claude cita un messaggio della persona arrivato nella
    casella della sessione stessa nell'ultima ora; il testo dell'ok e' copiato da li', non riscritto. Storia: 16/10
    (the maintainer from the phone, «Sì, tutte e due») solo la master con un messaggio del relay; 22/10 anche la
    sessione del compito, con un messaggio scritto nel suo terminale (origin «terminal», lo salva l'hook) o dal relay;
    23/10 11:10 «terminal» anche per la master; 23/10 17:14 il PIN al posto di «terminal»; 23/10 18:53 (the maintainer
    from the phone: «se io scrivo un ok è sempre valido») la regola di oggi: QUALUNQUE sessione registra un ok che ha
    ricevuto (casella sua: `to` = chi chiama), per QUALUNQUE compito, anche di un'altra sessione; valgono «terminal»
    (risolto dalla trascrizione: Claude Code la scrive dopo gli hook, quindi un record ancora «unverified» si aspetta
    fino a INBOX_WAIT_S) e «relay»; mai «cron», mai «session» (il testo di un'altra sessione), mai un record che la
    trascrizione attribuisce a un'altra sessione. Il buco del `tmux send-keys` lo chiude la guardia PreToolUse
    (cm-lane-guard.py). Torna il testo o esce con il motivo; «try again» e' per la sessione, che riprova: alla persona
    non arriva mai un errore sul suo ok."""
    caller = _caller_session()
    ib = _load("cm-inbox")
    rec = ib.load(rec_id)
    if not rec:
        sys.exit(f"task approve --inbox: no message {rec_id} in the inbox")
    if rec.get("to") != caller:
        sys.exit(f"task approve --inbox: message {rec_id} is for {rec.get('to')}, not for {caller or 'this shell'}: only the session that received an ok records it")
    deadline = time.time() + INBOX_WAIT_S
    while rec.get("origin") == "unverified":
        # 23/10 16:22: Claude Code scrive la riga `user` della trascrizione dopo gli hook; di solito al momento
        # dell'uso c'e' gia', altrimenti si aspetta un po' (18:53: mai un errore alla persona)
        rec = ib.resolve(rec)
        if rec is None:
            sys.exit(f"task approve --inbox: message {rec_id} did not come from the person (origin peer): the transcript shows it as another session's or the system's")
        if rec.get("origin") != "unverified" or time.time() >= deadline:
            break
        time.sleep(1)
    origin = str(rec.get("origin") or "?")
    if origin not in ("relay", "terminal"):
        why = {"cron": "a prompt of the session's own cron is not the person",
               "unverified": f"the transcript does not show the prompt yet (waited {INBOX_WAIT_S:g} s): try again in a few seconds; if it keeps failing the record has no transcript (an older hook wrote it): ask the person to write the ok again"}.get(
            origin, "an ok comes from the person (the terminal or their device), never from another session's text")
        sys.exit(f"task approve --inbox: message {rec_id} did not come from the person (origin {origin}): {why}")
    age = time.time() - float(rec.get("created") or 0)
    if age > INBOX_MAX_AGE_S:
        sys.exit(f"task approve --inbox: message {rec_id} is {int(age // 60)} minutes old (at most {INBOX_MAX_AGE_S // 60})")
    quote = " ".join(str(rec.get("text") or "").split())[:400]
    when = time.strftime("%d/%m %H:%M", time.localtime(float(rec["created"])))
    return f"{by}, {when}: «{quote}» (inbox {rec_id}, origin {origin}, to {caller})" + (f" — {note}" if note else "")


def pin_approval_text(by, pin, note=None):
    """23/10/2026 17:14 (the maintainer from the web: «ok PIN fisso», after «serve soluzione sicura»): the ok typed as
    «ok 1234» in any terminal or chat; the proof is the PIN (cm-pin.py: hash on disk, five wrong in fifteen minutes lock
    it), not the terminal, so any session records it, a plain shell too. The PIN never reaches the registry, the ledger
    or an error message. Returns (holder, text) or exits with the reason."""
    caller = _caller_session()
    ok, what = _load("cm-pin").verify(pin, caller or "a shell")
    if not ok:
        sys.exit(f"task approve --pin: {what}")
    if by and by != what:
        sys.exit(f"task approve --pin: the PIN belongs to {what}, not to {by}")
    when = time.strftime("%d/%m %H:%M")
    return what, f"{what}, {when}: PIN (via {caller or 'a terminal'})" + (f" — {note}" if note else "")


def standing_text(by, rec_id):
    """23/10/2026 20:00 (the maintainer from the phone at 19:58: «Quando ci sono aggiornamenti di supervisor, effettuali
    direttamente»): un ok permanente cita un messaggio della persona come un ok normale (la casella di chi chiama,
    origine terminal o relay, risolta dalla trascrizione), ma la sua eta' non conta: il messaggio stesso e' l'ok."""
    caller = _caller_session()
    ib = _load("cm-inbox")
    rec = ib.load(rec_id)
    if not rec:
        sys.exit(f"task standing add: no message {rec_id} in the inbox")
    if rec.get("to") != caller:
        sys.exit(f"task standing add: message {rec_id} is for {rec.get('to')}, not for {caller or 'this shell'}: only the session that received it records it")
    if rec.get("origin") == "unverified":
        rec = ib.resolve(rec)
        if rec is None:
            sys.exit(f"task standing add: message {rec_id} did not come from the person (origin peer)")
    origin = str(rec.get("origin") or "?")
    if origin not in ("relay", "terminal"):
        sys.exit(f"task standing add: message {rec_id} did not come from the person (origin {origin}): a standing ok is the person's own message, never a cron prompt or another session's text")
    quote = " ".join(str(rec.get("text") or "").split())[:400]
    when = time.strftime("%d/%m %H:%M", time.localtime(float(rec["created"])))
    return f"{by}, {when}: «{quote}» (inbox {rec_id}, origin {origin}, to {caller})"


def standing_match(con, row):
    """La regola permanente che copre il compito: la cartella della regola e' quella del compito o la contiene, e il
    kind e' quello dell'esito (outcome.kind). None se nessuna."""
    task = json.loads(row["contract"])["task"]
    kind = str((task.get("outcome") or {}).get("kind") or "")
    if not kind:
        return None
    proj = os.path.realpath(row["project"] or (task.get("where") or {}).get("project") or "")
    for r in con.execute("SELECT * FROM standing WHERE revoked_at IS NULL ORDER BY n").fetchall():
        rp = os.path.realpath(r["project"])
        if r["kind"] == kind and (proj == rp or proj.startswith(rp.rstrip("/") + "/")):
            return r
    return None


def standing_apply(con, tid):
    """Al wait-ok: se una regola copre il compito, l'approvazione nasce subito, a nome della persona, citando il
    messaggio e la regola. Torna la regola o None."""
    if request_options(con, tid) or request_also(con, tid)[0]:   # 1.81: una scelta la fa la persona, mai una regola; 1.99: anche una carta con altri compiti
        return None
    r = standing_match(con, get_task(con, tid))
    if r is None:
        return None
    approve(con, tid, r["by_"], f"{r['text']} — standing ok n{r['n']} for {r['kind']} in {r['project']}")
    return r


def ever_worked(con, tid):
    """Una traccia di lavoro: in corso adesso, un `task start`, un passo o una prova scritti, un esito mandato dalla sessione."""
    if get_task(con, tid)["state"] == "running":
        return True
    return bool(con.execute("SELECT 1 FROM steps WHERE task=? AND kind IN ('start', 'step', 'prova') UNION ALL "
                            "SELECT 1 FROM results WHERE task=? LIMIT 1", (tid, tid)).fetchone())


TIDY_STALE_S = 7 * 86400
TIDY_ARCHIVE_MAX = 30


def _git(project, *a):
    return subprocess.run(["git", "-C", str(project), *a], capture_output=True, text=True, timeout=20)


def newer_build(project, tag, cache):
    """1.113: un tag dello stesso progetto piu' nuovo di `tag` che lo contiene (la pre-release nuova porta il commit della
    vecchia), o None. Senza repo, senza il tag o con git che non risponde: None, cioe' niente si chiude."""
    key = (str(project), tag)
    if key in cache:
        return cache[key]
    res = None
    try:
        tk = ("tags", str(project))
        if tk not in cache:
            out = _git(project, "for-each-ref", "--sort=-creatordate", "--format=%(refname:short)\t%(creatordate:unix)", "refs/tags").stdout
            cache[tk] = [(t, int(ts)) for t, ts in (x.split("\t") for x in out.splitlines() if "\t" in x)]
        when = dict(cache[tk])
        if tag in when:
            for t, ts in cache[tk]:   # dal piu' nuovo
                if ts <= when[tag]:
                    break
                if _git(project, "merge-base", "--is-ancestor", tag, t).returncode == 0:
                    res = t
                    break
    except (OSError, subprocess.SubprocessError, ValueError):
        res = None
    cache[key] = res
    return res


def _last_activity(con, tid, row):
    vals = [row["created_at"] or 0]
    for q in ("SELECT MAX(at) FROM steps WHERE task=?", "SELECT MAX(at) FROM prove WHERE task=?", "SELECT MAX(at) FROM pauses WHERE task=?"):
        v = con.execute(q, (tid,)).fetchone()[0]
        if v:
            vals.append(v)
    return max(vals)


def tidy(con, now_=None, dry=False):
    """1.113 (03/11/2026, «le decisioni superate devono andare da sole: semplificare, non dare compiti in piu'»): le voci di
    Tocca a te che non chiedono piu' niente escono da sole. La chiusura si chiama «superata»: chiude la pausa o ritira la
    prova, lascia un passo e una riga in `tidy`; MAI una conferma della persona (pagella e controlli restano veri), mai
    un compito chiuso. Una pausa che aspetta un'azione della persona senza una prova non scade mai. Torna le chiusure."""
    now_ = int(now_ or now())
    out, cache = [], {}

    def log(tid, kind, rule, text):
        out.append({"task": tid, "kind": kind, "rule": rule, "text": text, "at": now_})
        if not dry:
            con.execute("INSERT INTO tidy (at, task, kind, rule, text) VALUES (?,?,?,?,?)", (now_, tid, kind, rule, text[:500]))

    def close_pause(tid, rule, text):
        log(tid, "pause", rule, text)
        if not dry:
            con.execute("UPDATE pauses SET resumed_at=? WHERE task=? AND resumed_at IS NULL", (now_, tid))
            con.execute("INSERT INTO steps (task, at, by_, kind, text, next) VALUES (?,?,'supervisor','step',?,NULL)", (tid, now_, text[:500]))

    rows = {r["id"]: r for r in con.execute("SELECT * FROM tasks").fetchall()}
    # 2: pause rimaste aperte su compiti chiusi
    for p in con.execute("SELECT DISTINCT task FROM pauses WHERE resumed_at IS NULL").fetchall():
        r = rows.get(p["task"])
        if r is not None and r["state"] not in OPEN_STATES:
            close_pause(p["task"], 2, M("tasks.tidy_closed", state=r["state"]))
    # 1 e 4: le pause di prova dei compiti aperti
    for tid, r in rows.items():
        if r["state"] not in OPEN_STATES:
            continue
        pz = con.execute("SELECT * FROM pauses WHERE task=? AND resumed_at IS NULL ORDER BY n DESC LIMIT 1", (tid,)).fetchone()
        if not pz or pz["waits"] != "person":
            continue
        pv = prova_of(pz, r["project"])
        if not pv:
            continue   # un'attesa che non e' una prova (login, casella, una scelta) non scade mai da sola
        nb = newer_build(r["project"], pv["release"], cache) if pv.get("release") and r["project"] else None
        if nb:
            close_pause(tid, 1, M("tasks.tidy_newer", old=pv["release"], new=nb))
        elif r["state"] != "running" and now_ - _last_activity(con, tid, r) >= TIDY_STALE_S:
            close_pause(tid, 4, M("tasks.tidy_stale", days=TIDY_STALE_S // 86400))
    # 1 e 4: le prove del banco ancora da fare
    for pr in con.execute("SELECT * FROM prove WHERE superseded_at IS NULL AND result IS NULL").fetchall():
        r = rows.get(pr["task"])
        if r is None:
            continue
        if r["state"] not in OPEN_STATES:
            rule, text = 2, M("tasks.tidy_closed", state=r["state"])
        else:
            nb = newer_build(r["project"], pr["build"], cache) if r["project"] else None
            if nb:
                rule, text = 1, M("tasks.tidy_newer", old=pr["build"], new=nb)
            elif r["state"] != "running" and now_ - _last_activity(con, pr["task"], r) >= TIDY_STALE_S:
                rule, text = 4, M("tasks.tidy_stale", days=TIDY_STALE_S // 86400)
            else:
                continue
        log(pr["task"], "prova", rule, text)
        if not dry:
            con.execute("UPDATE prove SET superseded_at=? WHERE n=?", (now_, pr["n"]))
    # 2 e 3: le richieste di needs-you (file della sessione): di un compito chiuso; doppie sullo stesso compito
    cm_, cfg = _cfg()
    d = Path(cm_.expand(cfg["state_dir"])) / "needs-you" if cm_ else None
    recs = []
    for f in sorted(d.glob("*.json")) if d is not None and d.is_dir() else []:
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("task"):
            recs.append((f, rec))
    newest = {}
    for f, rec in recs:
        r = rows.get(rec["task"])
        if r is not None and r["state"] not in OPEN_STATES:
            log(rec["task"], "needs_you", 2, M("tasks.tidy_closed", state=r["state"]))
            if not dry:
                f.unlink(missing_ok=True)
            continue
        newest.setdefault(rec["task"], []).append((float(rec.get("at") or 0), f, rec))
    for tid, lst in newest.items():
        lst.sort(key=lambda x: -x[0])
        for _, f, rec in lst[1:]:
            log(tid, "needs_you", 3, M("tasks.tidy_double"))
            if not dry:
                f.unlink(missing_ok=True)
    if not dry:
        con.commit()
    return out


def tidy_rows(con, since=0, rule=None, limit=None):
    """Le chiusure di `tidy` da `since`, dalla piu' recente: {task, short, kind, rule, text, at}."""
    q = "SELECT * FROM tidy WHERE at>=?" + (" AND rule=?" if rule else "") + " ORDER BY at DESC, n DESC" + (f" LIMIT {int(limit)}" if limit else "")
    out = []
    for x in con.execute(q, (since, rule) if rule else (since,)).fetchall():
        r = con.execute("SELECT * FROM tasks WHERE id=?", (x["task"],)).fetchone()
        out.append({"task": x["task"], "short": short_of(_contract_task(r)) if r else None, "kind": x["kind"], "rule": x["rule"], "text": x["text"], "at": x["at"]})
    return out


def auto_close(con, hours=AUTO_CLOSE_H):
    """16/10/2026: i compiti aperti da piu' di `hours` ore, avviati e non in pausa, col controllo verde si chiudono da soli (lo stesso `done`:
    il controllo rieseguito, mai un giudizio). Un controllo rosso qui non conta come tentativo: resta com'era.
    Torna [(id, verde)] dei compiti controllati."""
    cutoff = now() - int(hours * 3600)
    out = []
    for r in con.execute("SELECT id, contract FROM tasks WHERE state IN (%s) AND created_at <= ? ORDER BY created_at"
                         % ",".join("?" * len(OPEN_STATES)), (*OPEN_STATES, cutoff)).fetchall():
        task = json.loads(r["contract"])["task"]
        if outcome_errors(task):   # P1: un controllo che non tocca l'esito non chiude niente da solo
            continue
        if prove_pending(con, r["id"]):   # 1.99: una prova da fare tiene aperto il compito: la persona deve ancora vederla
            continue
        # 02/11 (chiusi alle 03:38 tre compiti approvati a lavoro non fatto): un controllo verde non dice che il lavoro c'e'.
        # Una pausa ferma il compito; un ok della persona non e' lavoro. `task done` a mano resta com'era.
        # Una pausa --waits external resta chiudibile: e' il controllo a dire che l'evento e' arrivato.
        if con.execute("SELECT 1 FROM pauses WHERE task=? AND resumed_at IS NULL AND COALESCE(waits, '') != 'external'", (r["id"],)).fetchone() or not ever_worked(con, r["id"]):
            continue
        rc, _ = run_check(task)
        if rc == 0:
            done(con, r["id"])
        out.append((r["id"], rc == 0))
    return out


# ------------------------------------------------------------------ impegni (22/10/2026, P2 e P3)
_CFG = []


def _cfg():
    if not _CFG:
        try:
            cm = _load("cm-config")
            _CFG.append((cm, cm.load(warn=False)))
        except Exception:   # noqa: BLE001 — senza config: inglese e default
            _CFG.append((None, {}))
    return _CFG[0]


def M(key, **kw):
    cm, cfg = _cfg()
    return cm.msg(cfg, key, **kw) if cm else key


def tasks_cfg():
    return (_cfg()[1].get("tasks") or {})


def stall_seconds():
    try:
        return float(tasks_cfg().get("stall_hours", STALL_HOURS)) * 3600
    except (TypeError, ValueError):
        return STALL_HOURS * 3600


def alerts_path():
    p = os.environ.get("CM_TASKS_ALERTS")
    return Path(p) if p else db_path().with_name("task-alerts.json")


def _plan_approved(con, plan):
    if not plan:
        return None
    r = con.execute("SELECT approved_by, approved_at FROM plans WHERE id=? AND approved_at IS NOT NULL", (plan,)).fetchone()
    return (r["approved_by"], r["approved_at"]) if r else None


def commitments(con, now_=None, git=True, stall_s=None, person=None):
    """P2: gli impegni — i compiti aperti con un ok registrato (approvals.by_) o un piano approvato, piu' quelli in
    awaiting_ok (approved false: la sessione aspetta la persona). Per ognuno eta' dall'ok, ultimo passo (stato, ok,
    esito, task step, pausa, amend: solo il registro — 22/10 00:43: un commit del progetto contava come passo di OGNI
    compito di quella cartella, e la vista dell'hook non coincideva con quella del CLI; `git` resta per compatibilita' e
    non fa niente), fermo da, prossimo passo (il --next dell'ultimo passo o dallo stato), chi aspetta chi, esito atteso,
    ultimo controllo noto, pausa, fermo (approvato, non in pausa, fermo da almeno stall_s). Fermi prima, poi dal piu' fermo."""
    now_ = int(now_ or now())
    stall_s = stall_seconds() if stall_s is None else stall_s
    person = person or M("tasks.person")
    out = []
    q = "SELECT * FROM tasks WHERE state IN (%s) ORDER BY created_at, id" % ",".join("?" * len(OPEN_STATES))
    for r in con.execute(q, OPEN_STATES).fetchall():
        task = json.loads(r["contract"])["task"]
        appr = con.execute("SELECT by_, at, what FROM approvals WHERE task=? AND by_ IS NOT NULL AND (what IS NULL OR what<>'confirmed') "
                           "ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
        plan = _plan_approved(con, r["plan"])
        approved = bool(appr or plan)
        if not approved and r["state"] != "awaiting_ok":
            continue
        req = con.execute("SELECT what, where_, at FROM approvals WHERE task=? AND by_ IS NULL ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
        approved_at = appr["at"] if appr else (plan[1] if plan else None)
        approved_by = appr["by_"] if appr else (plan[0] if plan else None)
        steps = [{"at": int(r["updated_at"] or r["created_at"] or 0), "kind": "state", "text": r["state"]}]
        if appr:
            steps.append({"at": int(appr["at"]), "kind": "approval", "text": appr["what"] or appr["by_"]})
        res = con.execute("SELECT outcome, reason, at FROM results WHERE task=? ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
        if res:
            steps.append({"at": int(res["at"]), "kind": "result", "text": f"{res['outcome']}: {res['reason']}"})
        st = con.execute("SELECT at, kind, text, next FROM steps WHERE task=? ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
        if st:
            steps.append({"at": int(st["at"]), "kind": st["kind"] or "step", "text": st["text"]})
        pz = con.execute("SELECT at, by_, reason, waits, since, recheck FROM pauses WHERE task=? AND resumed_at IS NULL ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
        if pz:
            steps.append({"at": int(pz["at"]), "kind": "pause", "text": pz["reason"]})
        last = max(steps, key=lambda s: (s["at"], STEP_RANK.get(s["kind"], 3)))
        session = r["session"] or task["where"].get("session") or "master"
        if r["state"] == "awaiting_ok":
            waits = {"who": session, "on": "person"}
            nxt = M("tasks.next_ok", person=person, what=(req["what"] if req and req["what"] else "")).rstrip(": ")
        else:
            waits = {"who": "person", "on": session}
            nxt = M(f"tasks.next_{r['state']}", session=session, task=r["id"])
        if st and st["next"]:
            nxt = st["next"]
        if pz and pz["waits"] == "person":   # 1.77: in pausa per qualcosa che solo la persona puo' fare → «Per te» nell'app
            waits = {"who": session, "on": "person"}
        idle = max(0, now_ - last["at"])
        out.append({"task": r["id"], "title": r["title"], "short": short_of(task), "project": r["project"], "session": r["session"], "state": r["state"],
                    "lane": r["lane"], "plan": r["plan"], "approved": approved, "approved_at": approved_at, "approved_by": approved_by,
                    "age_s": max(0, now_ - int(approved_at or r["created_at"] or now_)), "last_step": last, "idle_s": idle,
                    "next": nxt, "waits": waits,
                    "expected": (task.get("outcome") or {}).get("expected"), "outcome_kind": (task.get("outcome") or {}).get("kind"),
                    "check": {"outcome": res["outcome"], "at": int(res["at"])} if res else None,
                    "paused": {"reason": pz["reason"], "at": int(pz["since"] or pz["at"]), "by": pz["by_"], "waits": pz["waits"],
                               "recheck": pz["recheck"]} if pz else None,   # 1.88: waits person | external | null
                    "stalled": bool(approved and not pz and idle >= stall_s)})
    out.sort(key=lambda x: (not x["stalled"], -x["idle_s"], x["task"]))
    return out


# ------------------------------------------------------------------ fase A del 01/11/2026 (contratto 1.99)
UNDO_VALUES = {"si": "si", "sì": "si", "yes": "si", "no": "no", "in-parte": "in-parte", "partly": "in-parte"}
INFO_MAX = 300
CAUSE_MAX = 120
SNOOZE_DEFAULT_H = 24
# Dove si puo' fare, se la sessione non l'ha detto: un accesso, un'impronta o un pannello vogliono il PC
AT_PC_RE = re.compile(r"\b(login|log in|accedi|accesso|accessi|password|credenzial\w*|impronta|fingerprint|pannello|panel|al pc|sul pc|at the pc|on the pc)\b", re.I)


def parse_undo(args, verb):
    """(undo, undo_how) da --undo e --undo-how; esce con il motivo su un valore sconosciuto o su un come senza un se."""
    undo, how = _opt(args, "--undo"), _opt(args, "--undo-how")
    if undo is not None and str(undo).strip().lower() not in UNDO_VALUES:
        sys.exit(f"task {verb}: --undo takes si, no or in-parte")
    if how is not None and undo is None:
        sys.exit(f"task {verb}: --undo-how goes with --undo")
    if how is not None and len(how) > INFO_MAX:
        sys.exit(f"task {verb}: --undo-how over {INFO_MAX} characters")
    return (UNDO_VALUES[str(undo).strip().lower()] if undo is not None else None), (" ".join(how.split()) if how else None)


def _contract_task(row):
    return json.loads(row["contract"])["task"]


def _live_prove(con, tid):
    """Le prove in vigore di un compito: quelle non ritirate da una build nuova."""
    return con.execute("SELECT * FROM prove WHERE task=? AND superseded_at IS NULL ORDER BY n", (tid,)).fetchall()


def prove_pending(con, tid):
    """Le prove in vigore che non sono ok."""
    return [r for r in _live_prove(con, tid) if r["result"] != "ok"]


def prova_add(con, tid, build, dove, fai, atteso, by=None):
    """Una prova per la persona. Per versione: una build nuova ritira tutte quelle di prima del compito (un ok su una build
    vecchia non dice niente della nuova); stessa build, dove e fai sostituisce. Nessun esito: quello nasce solo da prove_mark."""
    row = get_task(con, tid)
    if row["state"] not in OPEN_STATES:
        sys.exit(f"task {tid}: already {row['state']}")
    build, dove, fai, atteso = (" ".join(str(x).split()) for x in (build, dove, fai, atteso))
    if not (1 <= len(build) <= 80 and 1 <= len(dove) <= 80 and 1 <= len(fai) <= INFO_MAX and 1 <= len(atteso) <= INFO_MAX):
        sys.exit(f"task prova: --build and --dove 1-80 characters, --fai and --atteso 1-{INFO_MAX}")
    t = now()
    con.execute("UPDATE prove SET superseded_at=? WHERE task=? AND superseded_at IS NULL AND (build<>? OR (dove=? AND fai=?))", (t, tid, build, dove, fai))
    cur = con.execute("INSERT INTO prove (task, build, dove, fai, atteso, at, by_) VALUES (?,?,?,?,?,?,?)", (tid, build, dove, fai, atteso, t, by))
    con.execute("INSERT INTO steps (task, at, by_, kind, text, next) VALUES (?,?,?,?,?,NULL)", (tid, t, by, "prova", f"{build} · {dove}: {fai}"[:500]))
    con.execute("UPDATE tasks SET updated_at=? WHERE id=?", (t, tid))
    con.commit()
    return cur.lastrowid


def prova_info(con, n):
    """La prova `n` se si puo' ancora toccare: in vigore, di un compito aperto. Altrimenti None."""
    r = con.execute("SELECT p.*, t.state AS task_state FROM prove p JOIN tasks t ON t.id=p.task WHERE p.n=? AND p.superseded_at IS NULL", (n,)).fetchone()
    return r if r and r["task_state"] in OPEN_STATES else None


def prove_mark(con, ids, result, by, where, text=None):
    """L'esito di una o piu' prove. La chiama SOLO il relay, per un tocco su un dispositivo (op prova): la CLI non ha una
    strada per arrivarci, come non ne ha una sessione per dire ok. Tutto o niente. Quando tutte le prove in vigore di un compito
    sono ok, registra la conferma della persona: e' quella che `task confirmed` legge. Torna {error} o {complete: [compiti]}."""
    rows = []
    for n in ids:
        r = prova_info(con, n)
        if r is None or (result == "ok" and r["result"] == "ok"):
            return {"error": n}
        rows.append(r)
    t = now()
    for r in rows:
        con.execute("UPDATE prove SET result=?, result_at=?, result_by=?, result_text=? WHERE n=?", (result, t, by, text, r["n"]))
    complete = []
    if result == "ok":
        for tid in sorted({r["task"] for r in rows}):
            if not prove_pending(con, tid):
                nums = ", ".join(str(x["n"]) for x in _live_prove(con, tid))
                con.execute("INSERT INTO approvals (task, plan, by_, at, text, what, where_) VALUES (?,?,?,?,?,'confirmed',NULL)",
                            (tid, get_task(con, tid)["plan"], by, t, f"{by}, {time.strftime('%d/%m %H:%M', time.localtime(t))}: prove {nums} ok ({where}) (origin relay)"))
                complete.append(tid)
    con.commit()
    return {"complete": complete}


def root_session():
    return str((_cfg()[1].get("workspace") or {}).get("root_session_name") or "master")


def _talk(name, text):
    """Un avviso staccato: chi chiude o non chiude un compito non aspetta nessuno. Sessione chiusa: resta in casella."""
    try:
        subprocess.Popen([os.environ.get("CM_TASK_TALK_BIN") or str(HERE / "supervisor"), "talk", name, text, "--no-wait"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def _tell(row, text):
    """La sessione del compito e la master."""
    sess = row["session"] or (_contract_task(row).get("where") or {}).get("session")
    for name in dict.fromkeys(x for x in (sess, root_session()) if x):
        _talk(name, text)


def settle(con, tid):
    """Dopo l'ultima prova ok: il controllo decide. Verde: chiude con done. Rosso: resta aperto, lo dice, nessun tentativo
    speso (la persona ha fatto la sua parte: non e' un fallimento della sessione) e la pausa per la persona si chiude, il
    prossimo passo e' della sessione. (exit, testo)."""
    row = get_task(con, tid)
    live = _live_prove(con, tid)
    if row["state"] not in OPEN_STATES:
        return 3, f"task settle {tid}: already {row['state']}"
    # 1.107: una pausa di prova chiusa con «Tutto va» (decisione provato) non ha righe di prova: vale la conferma della persona
    confirmed = con.execute("SELECT 1 FROM approvals WHERE task=? AND what='confirmed' AND by_ IS NOT NULL LIMIT 1", (tid,)).fetchone()
    if (live and any(r["result"] != "ok" for r in live)) or (not live and not confirmed):
        return 3, f"task settle {tid}: not every test is ok"
    task = _contract_task(row)
    oe = outcome_errors(task)
    rc, tail = (2, "; ".join(oe)) if oe else run_check(task)
    if rc == 0:
        done(con, tid, checked=(rc, tail))
        _tell(row, M("tasks.prove_closed", task=tid))
        return 0, f"{tid}: green — every test ok, check green"
    why = (tail.splitlines() or [""])[-1][:300] or f"exit {rc}"
    t = now()
    con.execute("UPDATE pauses SET resumed_at=? WHERE task=? AND resumed_at IS NULL", (t, tid))
    con.execute("INSERT INTO steps (task, at, by_, kind, text, next) VALUES (?,?,NULL,'prova-red',?,?)", (tid, t, why, M("tasks.prove_red_next")))
    con.execute("UPDATE tasks SET updated_at=? WHERE id=?", (t, tid))
    con.commit()
    _tell(row, M("tasks.prove_red", task=tid, why=why))
    return 1, f"{tid}: red — every test ok, check exit {rc}: {why}; the task stays open"


def prove_list(con, now_=None):
    """{at, n, builds, groups, ko, red}: le prove da fare per build, dalla build piu' recente; quelle che non vanno; i compiti con
    tutte le prove ok ancora aperti (reason null = il controllo sta girando)."""
    out, ko, red = {}, [], []
    q = "SELECT * FROM tasks WHERE state IN (%s) AND id IN (SELECT task FROM prove WHERE superseded_at IS NULL) ORDER BY created_at, id" % ",".join("?" * len(OPEN_STATES))
    for row in con.execute(q, OPEN_STATES).fetchall():
        task = _contract_task(row)
        short = short_of(task)
        live = _live_prove(con, row["id"])
        for r in live:
            base = {"id": r["n"], "task": row["id"], "short": short, "build": r["build"], "dove": r["dove"], "fai": r["fai"], "atteso": r["atteso"]}
            if r["result"] == "ko":
                ko.append(dict(base, text=r["result_text"], at=r["result_at"]))
            elif r["result"] is None:
                g = out.setdefault(r["build"], {"build": r["build"], "n": 0, "at": 0, "tasks": [], "prove": []})
                g["n"] += 1
                g["at"] = max(g["at"], r["at"])
                if row["id"] not in g["tasks"]:
                    g["tasks"].append(row["id"])
                g["prove"].append(dict(base, title=row["title"], session=row["session"] or (task.get("where") or {}).get("session"), project=row["project"], at=r["at"]))
        if live and all(r["result"] == "ok" for r in live):
            st = con.execute("SELECT text, at FROM steps WHERE task=? AND kind='prova-red' AND at>=? ORDER BY n DESC LIMIT 1", (row["id"], max(r["result_at"] for r in live))).fetchone()
            red.append({"task": row["id"], "short": short, "reason": st["text"] if st else None, "at": st["at"] if st else None})
    groups = sorted(out.values(), key=lambda g: (-g["at"], g["build"]))
    return {"at": int(now_ or now()), "n": sum(g["n"] for g in groups), "builds": len(groups), "groups": groups, "ko": ko, "red": red}


def precedent_of(con, tid):
    """Il precedente di una card: solo un difetto vero collegato (--defect-of), citato per nome; mai una somiglianza."""
    r = con.execute("SELECT defect_of FROM defects WHERE task=?", (tid,)).fetchone()
    other, rel = (r["defect_of"], "fixes") if r else (None, None)
    if not other:
        r = con.execute("SELECT task FROM defects WHERE defect_of=? ORDER BY at DESC, task LIMIT 1", (tid,)).fetchone()
        other, rel = (r["task"], "defect") if r else (None, None)
    row = con.execute("SELECT * FROM tasks WHERE id=?", (other,)).fetchone() if other else None
    return {"task": other, "short": short_of(_contract_task(row)), "relation": rel} if row else None


def _decision_key(prefix, text):
    return hashlib.sha1(f"{prefix}:{' '.join(str(text).casefold().split())}".encode()).hexdigest()[:12]


def _at_pc(at_pc, text):
    """«pc» o «here»: quello che la sessione ha dichiarato, altrimenti dalle parole."""
    return at_pc if at_pc in ("pc", "here") else ("pc" if AT_PC_RE.search(str(text or "")) else "here")


def _needs_you_open(now_):
    """Le richieste di `supervisor needs-you` non scadute. La loro vita (--until, chiusura) resta del relay."""
    cm_, cfg = _cfg()
    if not cm_:
        return []
    d = Path(cm_.expand(cfg["state_dir"])) / "needs-you"
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("reason") and not (rec.get("expires_at") and now_ >= float(rec["expires_at"])):
            out.append(rec)
    return out


PLAN_START = 300
ASK_OK = re.compile(r"(?i)\b(ok|okay|approv\w+|conferm\w+|via libera)\b")
ASK_CHOICE = re.compile(r"(?i)\b(scelt\w+|scegli\w*|choice|choose|quale|which)\b")


def explain(con_task, raw_title, mask):
    """1.104 (la persona, 02/11 08:04: «non ricordo cosa era stato proposto»): {title, plan_start} dalla maschera, senza modello."""
    plan = " ".join(str(con_task.get("plan") or "").split())
    if len(plan) > PLAN_START:
        cut = plan[:PLAN_START]
        plan = cut[:cut.rfind(" ")] if " " in cut else cut
    return {"title": mask(raw_title or ""), "plan_start": mask(plan) or None}


def asks_of(where, text):
    """Cosa aspetta la voce: pc (un'azione al PC), choice, ok, altrimenti other. Dalle sue parole, come «al PC»."""
    if where == "pc":
        return "pc"
    t = str(text or "")
    return "choice" if ASK_CHOICE.search(t) else "ok" if ASK_OK.search(t) else "other"


def decisions(con, now_=None, snoozed=False):
    """Cinque minuti: cio' che aspetta la persona, per causa. Una voce per pausa con waits person, per ok da dare, per
    needs-you. La chiave del gruppo e' --cause, altrimenti il testo; un ok fa gruppo da solo. Peso = i compiti che sblocca:
    i suoi e chi ne dipende. Un compito con una prova da fare sta in «Da provare», non qui. `snoozed`: anche i «Non ora»."""
    now_ = int(now_ or now())
    rows = {r["id"]: r for r in con.execute("SELECT * FROM tasks WHERE state IN (%s)" % ",".join("?" * len(OPEN_STATES)), OPEN_STATES).fetchall()}
    contracts = {k: _contract_task(r) for k, r in rows.items()}
    try:
        mask = _load("cm-relay-state").redact_preview
    except Exception:   # noqa: BLE001 — senza maschera il piano non esce
        mask = lambda _x: ""  # noqa: E731
    deps = {}
    for k, t in contracts.items():
        for d in t.get("depends_on") or []:
            deps.setdefault(d.get("task"), set()).add(k)
    items = []
    for tid, r in rows.items():
        t = contracts[tid]
        base = {"task": tid, "short": short_of(t), **explain(t, r["title"], mask), "session": r["session"] or (t.get("where") or {}).get("session"), "project": r["project"]}
        if r["state"] == "awaiting_ok":
            a = con.execute("SELECT * FROM approvals WHERE task=? AND by_ IS NULL ORDER BY n DESC LIMIT 1", (tid,)).fetchone()
            items.append((("o", tid), None, dict(base, kind="ok", asks="choice" if a and a["options"] else "ok", what=a["what"] if a else None, where_to=a["where_"] if a else None,
                                                 options=json.loads(a["options"]) if a and a["options"] else None, since=int(a["at"]) if a else int(r["updated_at"]),
                                                 where="here", untested=a["untested"] if a else None, undo=a["undo"] if a else None, undo_how=a["undo_how"] if a else None,
                                                 precedent=precedent_of(con, tid), also=json.loads(a["also"]) if a and a["also"] else None, needs_you=None)))
            continue
        pz = con.execute("SELECT * FROM pauses WHERE task=? AND resumed_at IS NULL ORDER BY n DESC LIMIT 1", (tid,)).fetchone()
        if pz and pz["waits"] == "person" and not prove_pending(con, tid):
            key = ("c", pz["cause"]) if pz["cause"] else ("t", pz["reason"])
            items.append((key, pz["cause"], dict(base, kind="pause", prova=prova_of(pz, r["project"]), asks=asks_of(_at_pc(pz["at_pc"], pz["reason"]), pz["reason"]), reason=pz["reason"], since=int(pz["since"] or pz["at"]), where=_at_pc(pz["at_pc"], pz["reason"]),
                                                 undo=pz["undo"], undo_how=pz["undo_how"], needs_you=None)))
    by_task = {it[2]["task"]: it[2] for it in items}
    for rec in _needs_you_open(now_):
        tid, reason = rec.get("task"), " ".join(str(rec["reason"]).split())
        if tid in by_task:
            by_task[tid]["needs_you"] = reason
            continue
        known = rows.get(tid)
        items.append((("n", f"{rec.get('name')} {reason}"), None, {"kind": "needs_you", **(explain(contracts[tid], known["title"], mask) if known else {"title": None, "plan_start": None}),
                                                                  "asks": asks_of(_at_pc(None, reason), reason), "task": tid if tid in rows else None, "session": rec.get("name"), "reason": reason,
                                                                  "since": int(rec.get("at") or now_), "where": _at_pc(None, reason)}))
    groups = {}
    for key, cause, it in items:
        k = _decision_key(*key)
        g = groups.setdefault(k, {"key": k, "title": cause or it.get("reason") or it.get("what") or it.get("title"), "cause": cause, "items": []})
        g["items"].append(it)
    marks = {r["key"]: r for r in con.execute("SELECT * FROM decision_snooze WHERE until>?", (now_,)).fetchall()}
    notes = {r["key"]: r["note"] for r in con.execute("SELECT key, note FROM cause_notes").fetchall()}
    out, hidden = [], 0
    for k, g in groups.items():
        unblocks, todo = set(), [i["task"] for i in g["items"] if i.get("task")]
        while todo:
            x = todo.pop()
            if x not in unblocks:
                unblocks.add(x)
                todo += [d for d in deps.get(x, ()) if d in rows]
        g["unblocks"] = sorted(unblocks)
        g["weight"] = max(1, len(unblocks))
        g["where"] = "pc" if any(i["where"] == "pc" for i in g["items"]) else "here"
        g["since"] = min(i["since"] for i in g["items"])
        g["note"] = notes.get(k)
        g["refs"] = sorted(i.get("task") or f"needs:{i['session']}" for i in g["items"])
        m = marks.get(k)
        if m and set(g["refs"]) <= set(json.loads(m["refs"])):   # un compito nuovo nel gruppo lo riporta su
            hidden += 1
            if not snoozed:
                continue
        out.append(g)
    out.sort(key=lambda g: (g["where"] != "here", -g["weight"], g["since"], g["key"]))
    return {"at": now_, "n": len(out), "here": sum(1 for g in out if g["where"] == "here"), "tasks": len({t for g in out for t in g["unblocks"]}), "snoozed": hidden, "groups": out}


PROVA_VAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
# le pause scritte prima della 1.107 dicono la prova a parole: «… la prova dell'utente sul telefono, pre-release
# slot-subito-2026-11-01; web 0.2.1663 …». Se ne ricava il campo solo con la parola «prova» e un tag o una versione
_PROVA_WORD_RE = re.compile(r"\bprov[aeio]", re.I)
_PROVA_REL_RE = re.compile(r"\bpre-?release\s+((?=[A-Za-z0-9._-]*\d)[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9])", re.I)   # un tag ha una cifra: «la pre-release che le contiene» no
_PROVA_WEB_RE = re.compile(r"\b(?:web|PWA)\s+v?(\d+\.\d+\.\d+)\b", re.I)


def prova_of(pz, project):
    """1.107: {project, release, web} della prova che la pausa aspetta, o None. Prima il campo scritto con --prova, poi,
    per le pause scritte prima, le parole del motivo."""
    raw = pz["prova"] if "prova" in pz.keys() else None
    if raw:
        try:
            d = json.loads(raw)
            return {"project": project, "release": d.get("release"), "web": d.get("web")}
        except ValueError:
            pass
    reason = pz["reason"] or ""
    if not _PROVA_WORD_RE.search(reason):
        return None
    rel, web = _PROVA_REL_RE.search(reason), _PROVA_WEB_RE.search(reason)
    if not rel and not web:
        return None
    return {"project": project, "release": rel.group(1) if rel else None, "web": web.group(1) if web else None}


def decision_tested(con, tid, by, text, works):
    """1.107, dal relay: «Tutto va» (works) registra la conferma della persona (quella che `task confirmed` legge) e chiude la
    pausa; «Non va» chiude la pausa e lascia il motivo nei passi: il compito torna alla sua sessione."""
    t = now()
    if works:
        con.execute("INSERT INTO approvals (task, plan, by_, at, text, what, where_) VALUES (?,?,?,?,?,'confirmed',NULL)",
                    (tid, get_task(con, tid)["plan"], by, t, text))
    con.execute("UPDATE pauses SET resumed_at=? WHERE task=? AND resumed_at IS NULL", (t, tid))
    con.execute("INSERT INTO steps (task, at, by_, kind, text, next) VALUES (?,?,?,'step',?,NULL)", (tid, t, by, text[:500]))
    con.execute("UPDATE tasks SET updated_at=? WHERE id=?", (t, tid))
    con.commit()


def decision_snooze(con, key, refs, by, hours, reason=None):
    """«Non ora», dal relay: il gruppo sparisce per `hours` ore, finche' non ci entra un compito nuovo."""
    t = now()
    con.execute("INSERT OR REPLACE INTO decision_snooze (key, at, by_, until, refs, reason) VALUES (?,?,?,?,?,?)",
                (key, t, by, t + int(hours * 3600), json.dumps(refs, ensure_ascii=False), reason))
    con.commit()


def decision_drop(con, tid, by, text):
    """«Lascia perdere», dal relay: il compito cancellato, la pausa chiusa, la ragione nel registro."""
    t = now()
    con.execute("UPDATE pauses SET resumed_at=? WHERE task=? AND resumed_at IS NULL", (t, tid))
    con.execute("INSERT INTO steps (task, at, by_, kind, text, next) VALUES (?,?,?,'dropped',?,NULL)", (tid, t, by, text[:500]))
    set_state(con, tid, "cancelled")
    con.commit()


def cause_set(con, ids, cause, note=None, by=None):
    """La master unisce pause con la stessa causa scritta in parole diverse e dice cosa sbloccano; il motivo resta."""
    cause = " ".join(str(cause).split())
    if not 1 <= len(cause) <= CAUSE_MAX:
        sys.exit(f"task cause: --cause 1-{CAUSE_MAX} characters")
    for tid in ids:
        get_task(con, tid)
        if not con.execute("SELECT 1 FROM pauses WHERE task=? AND resumed_at IS NULL", (tid,)).fetchone():
            sys.exit(f"task cause: {tid} has no open pause")
    for tid in ids:
        con.execute("UPDATE pauses SET cause=? WHERE task=? AND resumed_at IS NULL", (cause, tid))
    if note:
        con.execute("INSERT OR REPLACE INTO cause_notes (key, note, at, by_) VALUES (?,?,?,?)", (_decision_key("c", cause), " ".join(note.split())[:INFO_MAX], now(), by))
    con.commit()


def render_decisions(d):
    if not d["groups"]:
        return M("tasks.decisions_none", snoozed=d["snoozed"])
    lines = [M("tasks.decisions_header", n=d["n"], tasks=d["tasks"], here=d["here"], snoozed=d["snoozed"])]
    for g in d["groups"]:
        lines.append(M("tasks.decisions_line", where=M(f"tasks.where_{g['where']}"), weight=g["weight"], title=str(g["title"])[:120],
                       tasks=", ".join(i.get("task") or str(i.get("session")) for i in g["items"])))
        if g["note"]:
            lines.append("  " + g["note"])
    return "\n".join(lines)


def render_prove(p):
    if not (p["groups"] or p["ko"] or p["red"]):
        return M("tasks.prove_none")
    lines = []
    for g in p["groups"]:
        lines.append(f"{g['build']} ({g['n']}):")
        lines += [f"  #{x['id']} {x['task']} · {x['dove']}: {x['fai']} → {x['atteso']}" for x in g["prove"]]
    lines += [f"KO #{x['id']} {x['task']}: {x['text']}" for x in p["ko"]]
    lines += [f"RED {x['task']}: {x['reason'] or '…'}" for x in p["red"]]
    return "\n".join(lines)


# ------------------------------------------------------------------ chiusi per giorno (contratto 1.77, 22/10/2026)
_REMOTES = {}


def repo_url(project):
    """https://github.com/<owner>/<repo> del remoto origin del progetto, o None (cache per cartella, nessun fetch)."""
    if project in _REMOTES:
        return _REMOTES[project]
    url = None
    try:
        p = subprocess.run(["git", "-C", project, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5)
        m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", p.stdout.strip()) if p.returncode == 0 else None
        url = f"https://github.com/{m.group(1)}/{m.group(2)}" if m else None
    except (OSError, subprocess.SubprocessError):
        url = None
    _REMOTES[project] = url
    return url


_TAG_RE = re.compile(r"(?:git tag (?:-l|--list)\s+|gh release (?:view|create)\s+|tags/)[\"']?([A-Za-z0-9][\w.-]*)")   # anche impegni-2026-10-22-b
_HASH_RE = re.compile(r"(?<![\w/])([0-9a-f]{7,40})(?![\w/])")
_FILE_RE = re.compile(r"(?<![\w/])((?:docs|details)/[\w./-]+\.md)\b")
_VER_RE = re.compile(r"(?<![\w.])v?(\d+\.\d+(?:\.\d+)*)(?![\w.]*\d)")


def proof_of(task, result):
    """La prova del risultato, una sola, in quest'ordine (richiesta dell'app, 22/10): la release o il tag se il controllo
    ne cita uno; il commit se il controllo o l'esito ne citano uno; il file se l'esito cita un resoconto; altrimenti la
    prima riga del controllo. {kind, value, url}."""
    cmd = str((task.get("check") or {}).get("cmd") or "")
    texts = [cmd, str((task.get("outcome") or {}).get("expected") or ""), str((result or {}).get("reason") or ""), str(((result or {}).get("proof") or {}).get("value") or "")]
    repo = repo_url(task["where"]["project"])
    m = _TAG_RE.search(cmd)
    tag = m.group(1) if m else None
    if tag and not any(c.isdigit() for c in tag):
        # 24/10 (dall'app: «release v» col link a releases/tag/v): `gh release view "v$V"` da' solo «v», la versione sta
        # in una variabile. Si cerca nell'esito atteso o nel motivo del risultato; senza, si passa al commit o al controllo
        v = next((x.group(1) for t in texts[1:3] for x in [_VER_RE.search(t)] if x), None)
        tag = (tag + v if tag == "v" else v) if v else None
    if tag:
        return {"kind": "release", "value": tag, "url": f"{repo}/releases/tag/{tag}" if repo else None}
    for t in texts:
        m = _HASH_RE.search(t)
        if m and not m.group(1).isdigit():
            return {"kind": "commit", "value": m.group(1)[:7], "url": f"{repo}/commit/{m.group(1)}" if repo else None}
    for t in texts[1:]:
        m = _FILE_RE.search(t)
        if m:
            return {"kind": "file", "value": m.group(1), "url": None}
    return {"kind": "check", "value": cmd.splitlines()[0][:300] if cmd else "", "url": None}


def closed(con, day=None, now_=None):
    """{date, rows, days, more}: i compiti chiusi nel giorno `day` (ISO, oggi se None) — done verde, cancellati, falliti —
    dal piu' recente, con la prova; `days` = fino a sei giorni prima con almeno un chiuso, con il conteggio."""
    import datetime as dt
    now_ = int(now_ or now())
    d0 = dt.date.fromisoformat(day) if day else dt.date.fromtimestamp(now_)
    by_day = {}
    for r in con.execute("SELECT * FROM tasks WHERE state IN ('done','failed','cancelled')").fetchall():
        res = con.execute("SELECT outcome, reason, proof, at FROM results WHERE task=? AND accepted=1 ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
        at = int(res["at"]) if res and r["state"] != "cancelled" else int(r["updated_at"] or r["created_at"] or 0)
        task = json.loads(r["contract"])["task"]
        result = {"reason": res["reason"], "proof": json.loads(res["proof"])} if res else None
        day_ = dt.date.fromtimestamp(at)
        by_day.setdefault(day_, []).append({"task": r["id"], "title": r["title"], "short": short_of(task), "project": r["project"], "session": r["session"], "at": at,
                                            "outcome": "green" if r["state"] == "done" else r["state"], "proof": proof_of(task, result)})
    rows = sorted(by_day.get(d0, []), key=lambda x: -x["at"])
    days = [{"date": d.isoformat(), "n": len(v)} for d, v in sorted(by_day.items(), reverse=True) if d < d0][:6]
    return {"date": d0.isoformat(), "rows": rows, "days": days, "more": False}


def duration(s):
    s = max(0, int(s))
    if s < 3600:
        return M("tasks.dur_min", n=s // 60)
    if s < 48 * 3600:
        return M("tasks.dur_hm", h=s // 3600, m=(s % 3600) // 60)
    return M("tasks.dur_dh", d=s // 86400, h=(s % 86400) // 3600)


def _hm(epoch):
    return time.strftime("%d/%m %H:%M", time.localtime(int(epoch))) if epoch else "?"


def render_commitments(items, brief=False, max_lines=None, person=None):
    """Il testo: «Impegni (N, K fermi):» e una riga per voce; --brief taglia a max_lines voci (tasks.commitments_max_lines)."""
    person = person or M("tasks.person")
    if not items:
        return M("tasks.none")
    head = M("tasks.header", n=len(items), k=sum(1 for x in items if x["stalled"]))
    lines = []
    for x in items:
        # 22/10 00:35 (la master): «aspetta: l'utente → master» non si capiva; a chi tocca il prossimo passo, e basta
        turn = M("tasks.turn_person", person=person) if x["waits"]["on"] == "person" else M("tasks.turn_session", session=x["waits"]["on"])
        key_ = "tasks.line" if x["approved"] else "tasks.line_unapproved"
        line = M(key_, task=x["task"], session=x["session"] or "master", state=x["state"], when=_hm(x["approved_at"] or (x["last_step"] or {}).get("at")),
                 age=duration(x["age_s"]), idle=duration(x["idle_s"]), next=x["next"], turn=turn)
        if x["paused"]:
            line += M("tasks.pause_tag", reason=x["paused"]["reason"][:80])
        if x["expected"]:
            line += M("tasks.outcome_tag", expected=x["expected"])
        lines.append(line)
    if brief:
        n = max_lines if max_lines is not None else int(tasks_cfg().get("commitments_max_lines", 8) or 8)
        if len(lines) > n:
            lines = lines[:n] + [M("tasks.more", n=len(lines) - n)]
    return "\n".join([head] + lines)


def signature(items):
    """Cio' che conta come «cambiato» per l'hook della master (22/10 00:35: il blocco intero entrava a ogni messaggio in
    arrivo, sei volte in un turno): id, stato, fermo, pausa, prossimo passo e l'ora dell'ultimo passo; mai le durate."""
    return [[x["task"], x["state"], x["stalled"], (x["paused"] or {}).get("reason"), x["next"], (x["last_step"] or {}).get("at")] for x in items]


def stall_check(con):
    """P3: gli impegni fermi non ancora avvisati per questo ultimo passo (task-alerts.json: {task: last_step.at}); un
    passo nuovo riarma l'avviso, un impegno uscito dai fermi esce dal file."""
    items = [x for x in commitments(con) if x["stalled"]]
    p = alerts_path()
    try:
        sent = json.loads(p.read_text())
    except (OSError, ValueError):
        sent = {}
    new = [x for x in items if sent.get(x["task"]) != x["last_step"]["at"]]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({x["task"]: x["last_step"]["at"] for x in items}))
    except OSError:
        pass
    return new


def amend(con, tid, check=None, timeout_s=None, expected=None, kind=None, proof=None, by=None):
    """Corregge il contratto di un compito aperto; validato di nuovo (schema e regola dell'esito). [] o gli errori."""
    row = get_task(con, tid)
    if row["state"] not in OPEN_STATES:
        sys.exit(f"task {tid}: already {row['state']}")
    contract = json.loads(row["contract"])
    t = contract["task"]
    changes = []
    if check:
        t["check"]["cmd"] = check
        changes.append(f"check: {check}")
    if timeout_s is not None:
        t["check"]["timeout_s"] = timeout_s
        changes.append(f"timeout_s: {timeout_s}")
    if expected is not None or kind is not None or proof is not None:
        o = dict(t.get("outcome") or {})
        if expected is not None:
            o["expected"] = expected
        if kind is not None:
            o["kind"] = kind
        if proof is not None:
            o["proof"] = proof
        t["outcome"] = o
        changes.append(f"outcome ({o.get('kind')}): {o.get('expected')}")
    if not changes:
        sys.exit("usage: task amend ID [--check CMD] [--timeout S] [--outcome TEXT] [--kind K] [--proof P]")
    errs = validate(contract) or outcome_errors(t) or check_syntax_errors(t)
    if errs:
        return errs
    con.execute("UPDATE tasks SET contract=?, updated_at=? WHERE id=?", (json.dumps(contract, ensure_ascii=False), now(), tid))
    con.execute("INSERT INTO steps (task, at, by_, kind, text, next) VALUES (?,?,?,?,?,NULL)", (tid, now(), by, "amend", "; ".join(changes)))
    con.commit()
    return []


def set_short(con, tid, text, by=None):
    """Il titolo breve di un compito aperto (contratto 1.79): 1-SHORT_MAX caratteri su una riga; [] o gli errori."""
    row = get_task(con, tid)
    if row["state"] not in OPEN_STATES:
        sys.exit(f"task {tid}: already {row['state']}")
    short = " ".join(str(text or "").split())
    if not 1 <= len(short) <= SHORT_MAX:
        return [f"$.task.short: 1-{SHORT_MAX} characters on one line, got {len(short)}"]
    contract = json.loads(row["contract"])
    contract["task"]["short"] = short
    errs = validate(contract)
    if errs:
        return errs
    con.execute("UPDATE tasks SET contract=?, updated_at=? WHERE id=?", (json.dumps(contract, ensure_ascii=False), now(), tid))
    con.execute("INSERT INTO steps (task, at, by_, kind, text, next) VALUES (?,?,?,?,?,NULL)", (tid, now(), by, "amend", f"short: {short}"))
    con.commit()
    return []


def board(con):
    rows = [dict(r) for r in con.execute("SELECT id, plan, project, session, title, lane, state, updated_at FROM tasks ORDER BY updated_at DESC")]
    counts = {s: 0 for s in STATES}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    last = {}
    for r in con.execute("SELECT task, outcome, reason, attempt, at FROM results ORDER BY n"):
        last[r["task"]] = {"outcome": r["outcome"], "reason": r["reason"], "attempt": r["attempt"], "at": r["at"]}
    for r in rows:
        r["last"] = last.get(r["id"])
    plans = {}
    for r in rows:
        if r["plan"]:
            p = plans.setdefault(r["plan"], {s: 0 for s in STATES})
            p[r["state"]] += 1
    return {"counts": counts, "plans": plans,
            "awaiting_ok": [r for r in rows if r["state"] == "awaiting_ok"],
            "running": [r for r in rows if r["state"] == "running"],
            "failed": [r for r in rows if r["state"] == "failed"],
            "tasks": rows}


# ------------------------------------------------------------------ CLI
def _read(src):
    try:
        return json.loads(sys.stdin.read() if src == "-" else Path(src).read_text())
    except (OSError, ValueError) as e:
        sys.exit(f"task: cannot read {src}: {e}")


def _opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            sys.exit(f"task: {name} needs a value")
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


def _flag(args, name):
    if name in args:
        args.remove(name)
        return True
    return False


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    verb, args = argv[0], list(argv[1:])
    as_json = _flag(args, "--json")
    if verb == "closed" and not db_path().exists():   # 1.77: l'app chiede «senza registro: ok false», senza crearne uno
        print("no task registry", file=sys.stderr)
        return 3
    con = connect()
    if verb == "pagella":
        print(json.dumps(pagella(con), ensure_ascii=False))
        return 0
    if verb == "defect-of":   # 1.103: il legame a posteriori, solo se certo
        if len(args) != 2:
            sys.exit("usage: task defect-of ID ID_CORRETTO")
        get_task(con, args[0])
        get_task(con, args[1])
        con.execute("INSERT OR REPLACE INTO defects (task, defect_of, at) VALUES (?,?,?)", (args[0], args[1], now()))
        con.commit()
        return 0
    if verb == "add":
        topic, defect_of = _opt(args, "--topic"), _opt(args, "--defect-of")
        if len(args) != 1:
            sys.exit("usage: task add FILE|- [--topic T] [--defect-of ID]")
        if defect_of:
            get_task(con, defect_of)   # 1.99: il difetto di un compito che c'e'
        tid, errs = add(con, _read(args[0]), topic)
        if errs:
            print("\n".join(errs), file=sys.stderr)
            return 2
        if defect_of:
            con.execute("INSERT OR REPLACE INTO defects (task, defect_of, at) VALUES (?,?,?)", (tid, defect_of, now()))
            con.commit()
        print(tid)
        return 0
    if verb == "list":
        st, plan = _opt(args, "--state"), _opt(args, "--plan")
        q, p = "SELECT id, plan, state, lane, title, project FROM tasks WHERE 1=1", []
        if st:
            q += " AND state=?"; p.append(st)
        if plan:
            q += " AND plan=?"; p.append(plan)
        rows = [dict(r) for r in con.execute(q + " ORDER BY created_at, id", p)]
        if as_json:
            # contratto 1.37 (05/10): la richiesta di ok (wait-ok: cosa esce e dove) per «Da approvare» nell'app
            for r in rows:
                a = con.execute("SELECT what, where_, at, options FROM approvals WHERE task=? AND by_ IS NULL ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
                r["request"] = {"what": a["what"], "where": a["where_"], "at": a["at"]} if a else None
                if a and a["options"]:   # 1.81: le opzioni di una scelta
                    r["request"]["options"] = json.loads(a["options"])
                if a:   # 1.99: ok informato, solo quando ci sono
                    b = con.execute("SELECT untested, undo, undo_how FROM approvals WHERE task=? AND by_ IS NULL ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
                    r["request"].update({k: b[k] for k in ("untested", "undo", "undo_how") if b[k]})
                    also = request_also(con, r["id"])[0]
                    if also:
                        r["request"]["also"] = [{"task": x, "short": short_of(_contract_task(get_task(con, x)))} for x in also]
                    pre = precedent_of(con, r["id"])
                    if pre:
                        r["request"]["precedent"] = pre
        print(json.dumps(rows, ensure_ascii=False) if as_json else "\n".join(f"{r['id']}  {r['state']:<11} {r['lane']:<6} {r['title']}" for r in rows))
        return 0
    if verb == "show":
        row = get_task(con, args[0])
        out = dict(row)
        out["contract"] = json.loads(row["contract"])
        out["results"] = [dict(r, proof=json.loads(r["proof"])) for r in con.execute("SELECT * FROM results WHERE task=? ORDER BY n", (args[0],))]
        out["approvals"] = [dict(r, options=json.loads(r["options"]) if r["options"] else None) for r in con.execute("SELECT * FROM approvals WHERE task=? ORDER BY n", (args[0],))]
        out["steps"] = [dict(r) for r in con.execute("SELECT * FROM steps WHERE task=? ORDER BY n", (args[0],))]
        out["pauses"] = [dict(r) for r in con.execute("SELECT * FROM pauses WHERE task=? ORDER BY n", (args[0],))]
        out["prove"] = [dict(r) for r in con.execute("SELECT * FROM prove WHERE task=? ORDER BY n", (args[0],))]
        d = con.execute("SELECT defect_of FROM defects WHERE task=?", (args[0],)).fetchone()
        out["defect_of"] = d["defect_of"] if d else None
        print(json.dumps(out, ensure_ascii=False, indent=None if as_json else 2))
        return 0
    if verb == "impegni":
        stalled_only, brief = _flag(args, "--stalled"), _flag(args, "--brief")
        items = commitments(con)
        if stalled_only:
            items = [x for x in items if x["stalled"]]
        print(json.dumps(items, ensure_ascii=False) if as_json else render_commitments(items, brief=brief))
        return 0
    if verb == "prova":
        build, dove, fai, atteso, by = _opt(args, "--build"), _opt(args, "--dove"), _opt(args, "--fai"), _opt(args, "--atteso"), _opt(args, "--by")
        if len(args) != 1 or not (build and dove and fai and atteso):
            sys.exit("usage: task prova ID --build TAG --dove SCHERMATA --fai T --atteso T [--by B]\n"
                     "SCHERMATA con «Portami lì» nell'app: queue, box-aspetta, box-lavoro, box-esiti, lavori, launch, night, search, settings, homeorder, usage, home; una prova che vale solo sul telefono lo dice in --fai")
        print(prova_add(con, args[0], build, dove, fai, atteso, by))
        return 0
    if verb == "prove":
        p = prove_list(con)
        print(json.dumps(p, ensure_ascii=False) if as_json else render_prove(p))
        return 0
    if verb == "settle":
        if len(args) != 1:
            sys.exit("usage: task settle ID")
        rc, text = settle(con, args[0])
        print(text, file=sys.stderr if rc == 3 else sys.stdout)
        return rc
    if verb == "decisioni":
        d = decisions(con)
        print(json.dumps(d, ensure_ascii=False) if as_json else render_decisions(d))
        return 0
    if verb == "cause":
        cause, note, by = _opt(args, "--cause"), _opt(args, "--note"), _opt(args, "--by")
        if not args or not cause:
            sys.exit('usage: task cause ID [ID...] --cause C [--note "frase"] [--by B]')
        cause_set(con, args, cause, note, by)
        return 0
    if verb == "stall-check":
        new = stall_check(con)
        print(json.dumps(new, ensure_ascii=False) if as_json else (render_commitments(new) if new else ""))
        return 0
    if verb == "step":
        nxt, by = _opt(args, "--next"), _opt(args, "--by")
        if len(args) < 2:
            sys.exit('usage: task step ID "text" [--next "next step"] [--by B]')
        get_task(con, args[0])
        con.execute("INSERT INTO steps (task, at, by_, kind, text, next) VALUES (?,?,?,?,?,?)",
                    (args[0], now(), by, "step", " ".join(args[1:])[:500], (nxt or "")[:300] or None))
        con.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), args[0]))
        con.commit()
        return 0
    if verb == "pause":
        reason, by, waits, since = _opt(args, "--reason"), _opt(args, "--by"), _opt(args, "--waits"), _opt(args, "--since")
        recheck, cause = _opt(args, "--recheck"), _opt(args, "--cause")
        undo, undo_how = parse_undo(args, "pause")
        at_pc = "pc" if _flag(args, "--al-pc") else ("here" if _flag(args, "--da-qui") else None)
        is_prova, release, web = _flag(args, "--prova"), _opt(args, "--release"), _opt(args, "--web")
        if not args or not reason:
            sys.exit("usage: task pause ID --reason R [--by B] [--waits person|external] [--recheck 2d|ISO] [--since EPOCH|ISO] [--cause C] [--undo si|no|in-parte] [--undo-how T] [--al-pc|--da-qui] [--prova [--release TAG] [--web VERSION]]")
        if (release or web) and not is_prova:
            sys.exit("task pause: --release and --web go with --prova")
        if is_prova and waits not in ("person", "user"):
            sys.exit("task pause: --prova goes with --waits person (the person tests)")
        for v, what in ((release, "--release"), (web, "--web")):
            if v is not None and not PROVA_VAL_RE.fullmatch(v):
                sys.exit(f"task pause: {what} is one word (letters, digits, . _ -), at most 80 characters")
        prova_json = json.dumps({"release": release, "web": web}) if is_prova else None
        cause = " ".join(cause.split()) if cause else None
        if cause and len(cause) > CAUSE_MAX:
            sys.exit(f"task pause: --cause over {CAUSE_MAX} characters")
        if waits not in (None, "person", "user", "external"):
            sys.exit("task pause: --waits takes person or external")
        if recheck is not None and waits != "external":
            sys.exit("task pause: --recheck goes with --waits external")
        since_at = recheck_at = None
        if since:
            try:
                since_at = int(since) if since.isdigit() else int(__import__("datetime").datetime.fromisoformat(since).timestamp())
            except ValueError:
                sys.exit("task pause: --since wants an epoch or an ISO time (2026-10-18T09:00)")
        if recheck:
            m = re.match(r"^(\d+)([mhd])$", recheck.strip())
            try:
                recheck_at = (now() + int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)] if m
                              else int(__import__("datetime").datetime.fromisoformat(recheck).timestamp()))
            except ValueError:
                sys.exit("task pause: --recheck wants a duration (30m, 6h, 2d) or an ISO time (2026-11-02T09:00)")
        row = get_task(con, args[0])
        if row["state"] not in OPEN_STATES:
            sys.exit(f"task {args[0]}: already {row['state']}")
        con.execute("UPDATE pauses SET resumed_at=? WHERE task=? AND resumed_at IS NULL", (now(), args[0]))
        con.execute("INSERT INTO pauses (task, at, by_, reason, resumed_at, waits, since, recheck, cause, undo, undo_how, at_pc, prova) VALUES (?,?,?,?,NULL,?,?,?,?,?,?,?,?)",
                    (args[0], now(), by, reason[:300], waits if waits == "external" else ("person" if waits else None), since_at, recheck_at, cause, undo, undo_how, at_pc, prova_json))
        con.commit()
        return 0
    if verb == "closed":
        day = args[0] if args and not args[0].startswith("-") else None
        if day:
            try:
                __import__("datetime").date.fromisoformat(day)
            except ValueError:
                sys.exit("usage: task closed [AAAA-MM-GG] [--json]")
        out = closed(con, day)
        if as_json:
            print(json.dumps(out, ensure_ascii=False))
        else:
            print(f"{out['date']}: {len(out['rows'])}")
            for x in out["rows"]:
                print(f"- {x['task']} · {x['outcome']} · {time.strftime('%H:%M', time.localtime(x['at']))} · {x['proof']['kind']}: {x['proof']['value'][:80]}")
            for d in out["days"]:
                print(f"  {d['date']}: {d['n']}")
        return 0
    if verb == "resume":
        get_task(con, args[0])
        con.execute("UPDATE pauses SET resumed_at=? WHERE task=? AND resumed_at IS NULL", (now(), args[0]))
        con.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), args[0]))
        con.commit()
        return 0
    if verb == "amend":
        check, to = _opt(args, "--check"), _opt(args, "--timeout")
        expected, kind, proof, by = _opt(args, "--outcome"), _opt(args, "--kind"), _opt(args, "--proof"), _opt(args, "--by")
        if not args:
            sys.exit("usage: task amend ID [--check CMD] [--timeout S] [--outcome TEXT] [--kind K] [--proof P]")
        try:
            to = int(to) if to is not None else None
        except ValueError:
            sys.exit("task amend: --timeout wants seconds")
        errs = amend(con, args[0], check, to, expected, kind, proof, by)
        if errs:
            print("\n".join(errs), file=sys.stderr)
            return 2
        return 0
    if verb == "short":
        by = _opt(args, "--by")
        if len(args) != 2:
            sys.exit('usage: task short ID "testo" [--by B]')
        errs = set_short(con, args[0], args[1], by)
        if errs:
            print("\n".join(errs), file=sys.stderr)
            return 2
        return 0
    if verb == "confirmed":
        get_task(con, args[0])
        hit = con.execute("SELECT 1 FROM approvals WHERE task=? AND what='confirmed' AND by_ IS NOT NULL LIMIT 1", (args[0],)).fetchone()
        hit = hit and not prove_pending(con, args[0])   # 1.99: una prova da fare: la conferma di prima non copre questa build
        print(M("tasks.confirmed_yes" if hit else "tasks.confirmed_no", task=args[0]))
        return 0 if hit else 1
    if verb == "start":
        sess = _opt(args, "--session")
        row = get_task(con, args[0])
        if row["state"] not in OPEN_STATES:
            sys.exit(f"task {args[0]}: already {row['state']}")
        if sess:
            con.execute("UPDATE tasks SET session=? WHERE id=?", (sess, args[0]))
        set_state(con, args[0], "running")
        # 02/11: lo stato cambia dopo (wait-ok, approve): che sia partito deve restare scritto
        con.execute("INSERT INTO steps (task, at, by_, kind, text, next) VALUES (?,?,?,?,?,NULL)", (args[0], now(), sess, "start", "started"))
        con.commit()
        return 0
    if verb == "result":
        # 03/11 (observe supervisor-6753df0c e altre 4): la forma del JSON con ogni errore, anche con stdin vuoto; prima
        # una sessione l'ha indovinata un campo per volta in cinque tentativi
        if len(args) != 2:
            sys.exit("usage: task result ID FILE|-\n" + M("tasks.result_shape"))
        try:
            data = _read(args[1])
        except SystemExit as e:
            print(f"{e}\n{M('tasks.result_shape')}", file=sys.stderr)
            return 1
        errs = record_result(con, args[0], data)
        if errs:
            print("\n".join(errs + [M("tasks.result_shape")]), file=sys.stderr)
            return 2
        return 0
    if verb == "check":
        # 14/10 (spunti della valutazione di Kaya): il controllo rieseguito SENZA cambiare stato ne' contare tentativi,
        # per dire al telefono verde o rosso quando la sessione si ferma. Solo `done` accetta.
        task = json.loads(get_task(con, args[0])["contract"])["task"]
        rc, tail = run_check(task)
        res = {"task": args[0], "green": rc == 0, "rc": rc, "tail": tail, "title": task.get("title") or ""}
        print(json.dumps(res, ensure_ascii=False) if as_json else f"{args[0]}: {'green' if rc == 0 else 'red'} (exit {rc})")
        return 0 if rc == 0 else 1
    if verb == "done":
        constraint = _opt(args, "--constraint")
        green, res = done(con, args[0], constraint)
        print(json.dumps(res, ensure_ascii=False) if as_json else f"{args[0]}: {res['outcome']} — {res['reason']}")
        return 0 if green else 1
    if verb == "wait-ok":
        what, where, options = _opt(args, "--what"), _opt(args, "--where"), _opt(args, "--options")
        untested, also = _opt(args, "--untested"), _opt(args, "--also")
        undo, undo_how = parse_undo(args, "wait-ok")
        if not what or not where or not args:
            sys.exit("usage: task wait-ok ID --what W --where D [--options \"A|B\"] [--untested T] [--undo si|no|in-parte] [--undo-how T] [--also ID2,ID3]")
        also = parse_also(con, args[0], also) if also is not None else None
        if untested is not None and len(untested) > INFO_MAX:
            sys.exit(f"task wait-ok: --untested over {INFO_MAX} characters")
        untested = " ".join(untested.split()) if untested else None
        opts = parse_options(options) if options is not None else None   # 1.81: il compito chiede una scelta
        get_task(con, args[0])
        con.execute("INSERT INTO approvals (task, plan, by_, at, text, what, where_, options, untested, undo, undo_how, also) VALUES (?,?,NULL,?,NULL,?,?,?,?,?,?,?)",
                    (args[0], None, now(), what, where, json.dumps(opts, ensure_ascii=False) if opts else None, untested, undo, undo_how, json.dumps(also) if also else None))   # la richiesta: by NULL finche' nessuno approva
        set_state(con, args[0], "awaiting_ok")
        con.commit()
        r = standing_apply(con, args[0])   # 23/10 20:00: un ok permanente della persona copre il compito?
        if r is not None:
            print(f"{args[0]}: approved by standing ok n{r['n']} ({r['by_']}, {r['kind']} in {r['project']})")
        return 0
    if verb == "standing":
        sub = args[0] if args else "list"
        if sub == "add":
            by, rec_id, project, kind = _opt(args, "--by"), _opt(args, "--inbox"), _opt(args, "--project"), _opt(args, "--kind")
            if not (by and rec_id and project and kind):
                sys.exit("usage: task standing add --by B --inbox MSG --project DIR --kind K   (K = outcome.kind: release, deploy, install…)")
            text = standing_text(by, rec_id)
            cur = con.execute("INSERT INTO standing (project, kind, by_, text, inbox_id, created_at, revoked_at) VALUES (?,?,?,?,?,?,NULL)",
                              (os.path.realpath(os.path.expanduser(project)), kind, by, text, rec_id, now()))
            con.commit()
            print(f"standing ok n{cur.lastrowid}: {kind} in {project} — {text}")
            return 0
        if sub == "remove":
            if len(args) != 2 or not args[1].isdigit():
                sys.exit("usage: task standing remove N")
            n = con.execute("UPDATE standing SET revoked_at=? WHERE n=? AND revoked_at IS NULL", (now(), int(args[1]))).rowcount
            con.commit()
            if not n:
                sys.exit(f"task standing remove: no standing ok n{args[1]}")
            print(f"standing ok n{args[1]} revoked")
            return 0
        if sub == "list":
            rows = con.execute("SELECT * FROM standing WHERE revoked_at IS NULL ORDER BY n").fetchall()
            if as_json:
                print(json.dumps([dict(r) for r in rows], ensure_ascii=False))
                return 0
            if not rows:
                print("no standing ok")
                return 0
            for r in rows:
                print(f"n{r['n']}  {r['kind']:<8} {r['project']}  {r['text']}")
            return 0
        sys.exit("usage: task standing add --by B --inbox MSG --project DIR --kind K | list [--json] | remove N")
    if verb in ("approve", "confirm"):
        by, text, rec_id, pin, choice = _opt(args, "--by"), _opt(args, "--text"), _opt(args, "--inbox"), _opt(args, "--pin"), _opt(args, "--choice")
        if not (by or pin) or not args:
            sys.exit(f"usage: task {verb} ID --by B (--inbox MSG [--text NOTE] | --pin P [--text NOTE])" + (" [--what W] [--where D] [--choice C]" if verb == "approve" else ""))
        ok_at = None
        row = get_task(con, args[0])
        if pin:
            by, text = pin_approval_text(by, pin, text)   # 23/10 17:14: la prova e' il PIN, da qualunque sessione o shell
        elif rec_id:
            text = inbox_approval_text(by, rec_id, text, row["session"] or json.loads(row["contract"])["task"]["where"].get("session"))
            ok_at = float(_load("cm-inbox").load(rec_id)["created"])   # 1.99: la carta deve essere nata prima del messaggio
        else:
            # 01/11 (ok_variabile): prima bastava una variabile d'ambiente, o togliere CLAUDECODE, e una sessione
            # scriveva un ok a nome della persona. Da riga di comando ora non c'e' un --text che basti, da nessuna shell. Il tocco su un
            # dispositivo passa dal processo del relay, senza questa CLI
            sys.exit(f"task {verb}: an ok needs --inbox MSG (a message of the person from the last hour in this session's inbox) or --pin P; a device's ok arrives through the relay")
        if verb == "confirm":
            confirm(con, args[0], by, text)
            return 0
        approve(con, args[0], by, text, _opt(args, "--what"), _opt(args, "--where"), choice, ok_at=ok_at)
        return 0
    if verb == "tidy":
        if _flag(args, "--archive"):
            res = tidy_rows(con, 0, 4, TIDY_ARCHIVE_MAX)
        elif _flag(args, "--today"):
            t = time.localtime()
            res = tidy_rows(con, int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))))
        else:
            res = tidy(con, dry=_flag(args, "--dry-run"))
        print(json.dumps(res, ensure_ascii=False) if as_json else "\n".join(f"{x['task']}: {x['kind']} — regola {x['rule']}: {x['text']}" for x in res))
        return 0
    if verb == "auto-close":
        hours = float(_opt(args, "--hours", AUTO_CLOSE_H))
        res = auto_close(con, hours)
        print(json.dumps([{"task": t, "green": g} for t, g in res]) if as_json else "\n".join(f"{t}: {'done' if g else 'red, left open'}" for t, g in res))
        return 0
    if verb == "cancel":
        get_task(con, args[0])
        set_state(con, args[0], "cancelled")
        con.commit()
        return 0
    if verb == "board":
        b = board(con)
        since = _opt(args, "--since", "6h")
        if _flag(args, "--timeline"):
            # fase 2 (03/10): sotto i conteggi, la cronologia delle sessioni (prompt, test, commit, esiti, compiti)
            spec = importlib.util.spec_from_file_location("cm_timeline", HERE / "cm-timeline.py")
            tl_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tl_mod)
            b["timeline"] = tl_mod.timeline(tl_mod.parse_since(since))
        if as_json:
            print(json.dumps(b, ensure_ascii=False))
            return 0
        print("  ".join(f"{s} {n}" for s, n in b["counts"].items() if n) or "no tasks")
        for key in ("awaiting_ok", "running", "failed"):
            for r in b[key]:
                last = r["last"]
                print(f"{key:<11} {r['id']}  {r['title']}" + (f"  [{last['outcome']} #{last['attempt']}: {last['reason']}]" if last else ""))
        if "timeline" in b:
            print("\n" + tl_mod.render(b["timeline"]))
        return 0
    if verb == "constraint":
        sub = args.pop(0) if args else ""
        project, topic = _opt(args, "--project"), _opt(args, "--topic")
        if sub == "add":
            if not args or not (project or topic):
                sys.exit("usage: task constraint add (--project P | --topic T) TEXT")
            con.execute("INSERT INTO constraints (project, topic, text, source, at) VALUES (?,?,?,?,?)",
                        (project, topic, " ".join(args), None, now()))
            con.commit()
            return 0
        if sub == "list":
            rows = [dict(r) for r in con.execute("SELECT * FROM constraints ORDER BY n")
                    if (not project or r["project"] == project) and (not topic or r["topic"] == topic)]
            print(json.dumps(rows, ensure_ascii=False) if as_json else "\n".join(f"{r['project'] or '#' + str(r['topic'])}: {r['text']}" for r in rows))
            return 0
        sys.exit("usage: task constraint add|list")
    sys.exit(f"task: unknown verb {verb}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
