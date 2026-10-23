#!/usr/bin/env python3
"""supervisor task — il registro dei compiti (fase 1, 03/10/2026).

  supervisor task add FILE|- [--topic T]     un contratto {schema_version, task}: validato, con i vincoli
                                                pertinenti allegati (stesso progetto, stesso argomento)
  supervisor task list [--state S] [--plan P] [--json]
  supervisor task show ID [--json]
  supervisor task start ID [--session NOME]  in corso (la guardia della corsia chiusa lo cerca)
  supervisor task result ID FILE|-           l'esito che la sessione manda indietro (non e' accettazione)
  supervisor task check ID [--json]           riesegue il controllo senza cambiare niente: exit 0 verde, 1 rosso
  supervisor task done ID [--constraint T]   riesegue il controllo: verde = fatto; rosso = un tentativo in
                                                piu', e al terzo «fallito». Exit 0 verde, 1 rosso
  supervisor task wait-ok ID --what W --where D   attende ok (corsia chiusa fuori da un piano approvato)
  supervisor task approve ID --by B --text T [--what W] [--where D]
  supervisor task approve ID --by B --pin P [--text NOTA] [--what W] [--where D]
                                                (23/10 17:14) l'ok con il PIN della persona («ok 1234» in qualunque
                                                terminale o chat): da ogni sessione e anche da una shell, la prova e'
                                                il PIN (supervisor pin set); il PIN non si scrive da nessun'altra parte
  supervisor task approve ID --by B --inbox MSG [--text NOTA] [--what W] [--where D]
                                                da una sessione Claude l'ok a nome di una persona passa solo cosi':
                                                dalla master o (22/10) dalla sessione del compito, citando un messaggio
                                                della persona a lei dell'ultima ora arrivato dal relay (la chat
                                                dell'app, origin relay); mai terminal (dal 23/10 17:14 serve il PIN:
                                                chiunque digita nel pane, tmux send-keys), cron, unverified o session
  supervisor task auto-close [--hours 6] [--json]   chiude i compiti aperti da piu' di 6 ore col controllo verde
  supervisor task cancel ID
  supervisor task board [--json] [--timeline [--since 6h]]   stati ed esiti dal codice; con --timeline la cronologia
  supervisor task constraint add (--project P | --topic T) TESTO
  supervisor task constraint list [--project P] [--topic T]

Impegni (22/10/2026: un compito nato da un ok resta visibile finche' il suo esito non e' verde):
  supervisor task impegni [--json] [--stalled] [--brief]   gli approvati non compiuti (e chi aspetta un ok), con eta',
                                                ultimo passo, prossimo passo, chi aspetta chi, pausa; --brief per l'hook
  supervisor task step ID "testo" [--next "prossimo passo"] [--by B]   un passo fatto (e il prossimo)
  supervisor task pause ID --reason R [--by B] [--waits person] [--since EPOCH|ISO]
                                                «salvo altre priorita'», visibile, mai in allarme; --waits person = la pausa
  supervisor task resume ID                     aspetta l'utente (waits.on = person), --since = l'inizio vero (contratto 1.77)
  supervisor task closed [AAAA-MM-GG] [--json]  i compiti chiusi quel giorno (oggi) con la prova (release, commit, file o
                                                controllo) e i sei giorni prima con un conteggio; exit 3 senza registro
  supervisor task amend ID [--check CMD] [--timeout S] [--outcome TESTO] [--kind K] [--proof P]
                                                corregge il contratto di un compito aperto (validato di nuovo)
  supervisor task short ID "testo" [--by B]     il titolo breve (task.short, 1-28 caratteri, una riga nell'app: contratto
                                                1.79); senza, impegni e closed ne ricavano uno dal titolo (parole intere)
  supervisor task confirm ID --by B (--text T | --inbox MSG)   la conferma della persona (come approve); `task
  supervisor task confirmed ID                  confirmed ID` esce 0 se c'e': un controllo valido per un esito install
  supervisor task stall-check [--json]          gli impegni fermi da piu' di tasks.stall_hours non ancora avvisati
                                                (task-alerts.json accanto al registro); lo chiama guard run

Il contratto e' schemas/task-contract.v1.json, condiviso con fable-director (copiato, mai importato). Il
registro e' SQLite in ~/.config/cc-supervisor/tasks.db (CM_TASKS_DB per i test). Nessun «fatto» a giudizio:
solo il controllo rieseguito e verde. Senza registro nessun altro comando di supervisor cambia.
Esito atteso (task.outcome, facoltativo): le parole dell'ok e un kind; il controllo deve toccarlo (OUTCOME_MARKERS),
altrimenti add e done lo rifiutano: un compito non si chiude su un passo intermedio.
"""
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
            errs.append(f"{path}: length out of range")
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
    what TEXT, where_ TEXT);
CREATE TABLE IF NOT EXISTS constraints (n INTEGER PRIMARY KEY, project TEXT, topic TEXT, text TEXT, source TEXT,
    at INTEGER);
CREATE TABLE IF NOT EXISTS steps (n INTEGER PRIMARY KEY, task TEXT, at INTEGER, by_ TEXT, kind TEXT, text TEXT, next TEXT);
CREATE TABLE IF NOT EXISTS pauses (n INTEGER PRIMARY KEY, task TEXT, at INTEGER, by_ TEXT, reason TEXT, resumed_at INTEGER);
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


def add(con, contract, topic=None):
    """(id, errori). Il contratto entra solo se valido; i vincoli pertinenti si aggiungono a task.constraints."""
    errs = validate(contract)
    if not errs:
        errs = outcome_errors(contract["task"])   # P1: un controllo che non tocca l'esito non entra
    if errs:
        return None, errs
    t = contract["task"]
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


def run_check(task):
    """(rc, coda dell'uscita): il controllo del contratto, nella sua cartella e col suo timeout."""
    chk = task["check"]
    cwd = chk.get("cwd") or task["where"]["project"]
    try:
        p = subprocess.run(chk["cmd"], shell=True, cwd=cwd, capture_output=True, text=True, timeout=chk["timeout_s"])
        out = (p.stdout + p.stderr).rstrip().splitlines()
        return p.returncode, "\n".join(out[-PROOF_TAIL:])
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {chk['timeout_s']} s"
    except OSError as e:
        return 127, str(e)


def done(con, tid, constraint=None):
    """Accettazione dal codice: riesegue il controllo. (verde, result)."""
    row = get_task(con, tid)
    if row["state"] not in OPEN_STATES:
        sys.exit(f"task {tid}: already {row['state']}")
    task = json.loads(row["contract"])["task"]
    oe = outcome_errors(task)
    if oe:   # P1: entrato prima della regola o cambiato: niente done su un passo intermedio (task amend lo corregge)
        print("\n".join(oe), file=sys.stderr)
        sys.exit(3)
    rc, tail = run_check(task)
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


def approve(con, tid, by, text, what=None, where=None):
    row = get_task(con, tid)
    con.execute("INSERT INTO approvals (task, plan, by_, at, text, what, where_) VALUES (?,?,?,?,?,?,?)",
                (tid, row["plan"], by, now(), text, what, where))
    if row["state"] == "awaiting_ok":
        set_state(con, tid, "approved")
    con.commit()


INBOX_MAX_AGE_S = 3600
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
    """16/10/2026 (the maintainer from the phone, «Sì, tutte e due»): l'ok a nome di una persona registrato da una sessione vale solo se
    cita un messaggio della persona arrivato nella casella nell'ultima ora; il testo dell'ok e' copiato da li', non
    riscritto. Due vie: la master con un messaggio del relay (telefono, orologio, web) indirizzato a lei; dal 22/10
    (the maintainer from the web at 13:34: «l'ok nella sessione deve essere valido come gli altri», quattro ok ripetuti
    nell'app in un giorno) anche la sessione del compito con un messaggio indirizzato a lei, scritto dalla persona nel
    terminale (origin «terminal», lo salva l'hook) o arrivato dal relay. Dal 23/10 (the maintainer in the master's
    terminal at 11:10: «l'ok da terminale ha lo stesso valore di quello da app») anche la master accetta «terminal»;
    l'hook distingue i prompt della persona da quelli del cron («cron») e da quelli che non sa attribuire
    («unverified»): mai buoni. Mai un testo di un'altra sessione (origin «session»). Dal 23/10 17:14 (the maintainer
    from the web: «ok PIN fisso») nemmeno «terminal» basta: chiunque digita nel pane (tmux send-keys); l'ok dal
    terminale passa con --pin (pin_approval_text), qui resta solo «relay». Torna il testo o esce con il motivo."""
    cm = _load("cm-config")
    cfg = cm.load(warn=False)
    master = str((cfg.get("workspace") or {}).get("root_session_name") or "master")
    caller = _caller_session()
    allowed = {master} | ({task_session} if task_session else set())
    if caller not in allowed:
        who = f"only the master session ({master})" + (f" or the task's session ({task_session})" if task_session else "")
        sys.exit(f"task approve --inbox: {who} records an ok on someone's behalf; this is {caller or 'not a tmux session'}")
    ib = _load("cm-inbox")
    rec = ib.load(rec_id)
    if not rec:
        sys.exit(f"task approve --inbox: no message {rec_id} in the inbox")
    if rec.get("origin") == "unverified":
        # 23/10 16:22: Claude Code scrive la riga `user` della trascrizione dopo gli hook; al momento dell'uso c'e'
        rec = ib.resolve(rec)
        if rec is None:
            sys.exit(f"task approve --inbox: message {rec_id} did not come from the person (origin peer): the transcript shows it as another session's or the system's")
    if rec.get("to") != caller:
        sys.exit(f"task approve --inbox: message {rec_id} is for {rec.get('to')}, not for {caller}")
    origin = str(rec.get("origin") or "?")
    if origin != "relay":
        why = {"terminal": "a text typed in a terminal proves nothing (anyone can type into the pane: tmux send-keys); since 23/10 17:14 an ok from a terminal needs the PIN: ask them to write «ok <PIN>» and record it with --pin",
               "cron": "a prompt of the session's own cron is not the person",
               "unverified": "the transcript does not show the prompt (yet): try again in a few seconds, or ask them to write it again"}.get(
            origin, "an ok comes from the person's device (the app), never from another session's text")
        sys.exit(f"task approve --inbox: message {rec_id} did not come from the person's device (origin {origin}): {why}")
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


def auto_close(con, hours=AUTO_CLOSE_H):
    """16/10/2026: i compiti aperti da piu' di `hours` ore col controllo verde si chiudono da soli (lo stesso `done`:
    il controllo rieseguito, mai un giudizio). Un controllo rosso qui non conta come tentativo: resta com'era.
    Torna [(id, verde)] dei compiti controllati."""
    cutoff = now() - int(hours * 3600)
    out = []
    for r in con.execute("SELECT id, contract FROM tasks WHERE state IN (%s) AND created_at <= ? ORDER BY created_at"
                         % ",".join("?" * len(OPEN_STATES)), (*OPEN_STATES, cutoff)).fetchall():
        task = json.loads(r["contract"])["task"]
        if outcome_errors(task):   # P1: un controllo che non tocca l'esito non chiude niente da solo
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
        pz = con.execute("SELECT at, by_, reason, waits, since FROM pauses WHERE task=? AND resumed_at IS NULL ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
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
                    "paused": {"reason": pz["reason"], "at": int(pz["since"] or pz["at"]), "by": pz["by_"]} if pz else None,
                    "stalled": bool(approved and not pz and idle >= stall_s)})
    out.sort(key=lambda x: (not x["stalled"], -x["idle_s"], x["task"]))
    return out


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


def proof_of(task, result):
    """La prova del risultato, una sola, in quest'ordine (richiesta dell'app, 22/10): la release o il tag se il controllo
    ne cita uno; il commit se il controllo o l'esito ne citano uno; il file se l'esito cita un resoconto; altrimenti la
    prima riga del controllo. {kind, value, url}."""
    cmd = str((task.get("check") or {}).get("cmd") or "")
    texts = [cmd, str((task.get("outcome") or {}).get("expected") or ""), str((result or {}).get("reason") or ""), str(((result or {}).get("proof") or {}).get("value") or "")]
    repo = repo_url(task["where"]["project"])
    m = _TAG_RE.search(cmd)
    if m:
        return {"kind": "release", "value": m.group(1), "url": f"{repo}/releases/tag/{m.group(1)}" if repo else None}
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
    errs = validate(contract) or outcome_errors(t)
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
    if verb == "add":
        topic = _opt(args, "--topic")
        if len(args) != 1:
            sys.exit("usage: task add FILE|- [--topic T]")
        tid, errs = add(con, _read(args[0]), topic)
        if errs:
            print("\n".join(errs), file=sys.stderr)
            return 2
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
                a = con.execute("SELECT what, where_, at FROM approvals WHERE task=? AND by_ IS NULL ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
                r["request"] = {"what": a["what"], "where": a["where_"], "at": a["at"]} if a else None
        print(json.dumps(rows, ensure_ascii=False) if as_json else "\n".join(f"{r['id']}  {r['state']:<11} {r['lane']:<6} {r['title']}" for r in rows))
        return 0
    if verb == "show":
        row = get_task(con, args[0])
        out = dict(row)
        out["contract"] = json.loads(row["contract"])
        out["results"] = [dict(r, proof=json.loads(r["proof"])) for r in con.execute("SELECT * FROM results WHERE task=? ORDER BY n", (args[0],))]
        out["approvals"] = [dict(r) for r in con.execute("SELECT * FROM approvals WHERE task=? ORDER BY n", (args[0],))]
        out["steps"] = [dict(r) for r in con.execute("SELECT * FROM steps WHERE task=? ORDER BY n", (args[0],))]
        out["pauses"] = [dict(r) for r in con.execute("SELECT * FROM pauses WHERE task=? ORDER BY n", (args[0],))]
        print(json.dumps(out, ensure_ascii=False, indent=None if as_json else 2))
        return 0
    if verb == "impegni":
        stalled_only, brief = _flag(args, "--stalled"), _flag(args, "--brief")
        items = commitments(con)
        if stalled_only:
            items = [x for x in items if x["stalled"]]
        print(json.dumps(items, ensure_ascii=False) if as_json else render_commitments(items, brief=brief))
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
        if not args or not reason:
            sys.exit("usage: task pause ID --reason R [--by B] [--waits person] [--since EPOCH|ISO]")
        if waits not in (None, "person", "user"):
            sys.exit("task pause: --waits takes person")
        since_at = None
        if since:
            try:
                since_at = int(since) if since.isdigit() else int(__import__("datetime").datetime.fromisoformat(since).timestamp())
            except ValueError:
                sys.exit("task pause: --since wants an epoch or an ISO time (2026-10-18T09:00)")
        row = get_task(con, args[0])
        if row["state"] not in OPEN_STATES:
            sys.exit(f"task {args[0]}: already {row['state']}")
        con.execute("UPDATE pauses SET resumed_at=? WHERE task=? AND resumed_at IS NULL", (now(), args[0]))
        con.execute("INSERT INTO pauses (task, at, by_, reason, resumed_at, waits, since) VALUES (?,?,?,?,NULL,?,?)",
                    (args[0], now(), by, reason[:300], "person" if waits else None, since_at))
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
        con.commit()
        return 0
    if verb == "result":
        errs = record_result(con, args[0], _read(args[1]))
        if errs:
            print("\n".join(errs), file=sys.stderr)
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
        what, where = _opt(args, "--what"), _opt(args, "--where")
        if not what or not where:
            sys.exit("usage: task wait-ok ID --what W --where D")
        get_task(con, args[0])
        con.execute("INSERT INTO approvals (task, plan, by_, at, text, what, where_) VALUES (?,?,NULL,?,NULL,?,?)",
                    (args[0], None, now(), what, where))   # la richiesta: by NULL finche' nessuno approva
        set_state(con, args[0], "awaiting_ok")
        con.commit()
        return 0
    if verb in ("approve", "confirm"):
        by, text, rec_id, pin = _opt(args, "--by"), _opt(args, "--text"), _opt(args, "--inbox"), _opt(args, "--pin")
        if not (by or pin) or not (text or rec_id or pin) or not args:
            sys.exit(f"usage: task {verb} ID --by B (--text T | --inbox MSG [--text NOTE] | --pin P [--text NOTE])" + (" [--what W] [--where D]" if verb == "approve" else ""))
        row = get_task(con, args[0])
        if pin:
            by, text = pin_approval_text(by, pin, text)   # 23/10 17:14: la prova e' il PIN, da qualunque sessione o shell
        elif rec_id:
            text = inbox_approval_text(by, rec_id, text, row["session"] or json.loads(row["contract"])["task"]["where"].get("session"))
        elif os.environ.get("CLAUDECODE") and os.environ.get("CM_APPROVE_VIA") != "relay":
            # una sessione Claude (anche la master) non scrive un ok a nome d'altri senza la prova: il PIN della persona
            # o il messaggio del relay; il relay eredita CLAUDECODE da chi l'ha avviato, e si dichiara con CM_APPROVE_VIA
            sys.exit(f"task {verb}: from a Claude session an ok needs --pin P (the person's PIN) or --inbox MSG (a relay message from the last hour)")
        if verb == "confirm":
            # P1 (22/10): la conferma della persona («lo vedo sul telefono»), registrata con le regole dell'ok; `task
            # confirmed ID` la legge: e' un controllo valido per un esito install. Lo stato non cambia
            con.execute("INSERT INTO approvals (task, plan, by_, at, text, what, where_) VALUES (?,?,?,?,?,'confirmed',NULL)",
                        (args[0], row["plan"], by, now(), text))
            con.commit()
            return 0
        approve(con, args[0], by, text, _opt(args, "--what"), _opt(args, "--where"))
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
