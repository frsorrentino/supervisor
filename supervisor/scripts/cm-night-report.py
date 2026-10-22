#!/usr/bin/env python3
"""supervisor night report — i dati della pagina «Notte» (v2, 21/10/2026; v1 07/10/2026).

  cm-night-report.py [--now EPOCH|ISO] [--out-dir DIR] [--json-only] [--print] [--no-remote]

Un JSON fisso costruito dal codice, senza modello, con quello che e' successo nella finestra notturna:
- in alto quello che serve alla persona: le domande ferme (stato del relay), gli ok del registro (awaiting_ok) e le voci
  «!» della riga «Prossimi:»;
- la cronologia: un elemento per lavoro della coda `night` (night-done.jsonl, night-queue.jsonl, l'esito .md) o per
  sessione, con inizio, fine, esito e gli intervalli di lavoro (`spans`); le sessioni contano anche senza un prompt
  della persona (lanciate dalla master con un messaggio), quelle degli host remoti (win) si leggono via ssh, quelle
  negli scratchpad (/tmp/claude-*) no;
- una scheda per progetto (il repo: i worktree ci stanno dentro): le parti da TASKS.md, dal piano .md con le caselle o
  dai compiti del registro, gli obiettivi della notte (docs/proposte/<data>-*.md), i resoconti (docs/notte), i
  traguardi (commit, push, tag, «FINE»), gli intervalli delle sessioni e `where`, dove e' arrivato il progetto.

La finestra parte dal primo piano della notte (docs/proposte con la data della notte, scritto fra le 20:00 e le 06:00);
senza piani, dall'ultimo prompt della persona della sera (terminale o relay; non i `claude -p`), non prima delle 20:00
e non oltre 14 ore. Finisce al primo messaggio della persona dopo le 06:00 (dal telefono o alla master), o adesso.
Senza --out-dir scrive <data>.json e <data>.html in ~/.team-supervisor/night-report/. L'HTML e' un'anteprima statica
dello stesso JSON. Specifica: docs/plans/2026-10-07-pagina-notte-dati.md e docs/proposte/2026-10-21-notte-riepilogo.md
(fuori dal repo pubblico).
"""
import datetime as dt
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
SCHEMA = "team-supervisor/night-report"
V = 2   # 21/10: obiettivi, resoconti, traguardi, intervalli e `where` per progetto; sessioni senza prompt e remote
EVENING_H, MORNING_H, MAX_H = 20, 6, 14
TEXT_MAX = 200
PARTS_MAX = 80
EVENTS_MAX = 40
NEXT_MAX_AGE_S = 86400   # next_at e' la mezzanotte della riga di recap: vale quello di ieri o di oggi
BOX_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[([ xX~/!])\]\s+(.+?)\s*$")
BOX_STATE = {" ": "todo", "x": "done", "X": "done", "~": "running", "/": "running", "!": "blocked"}
TASK_STATE = {"done": "done", "running": "running", "failed": "blocked", "awaiting_ok": "blocked",
              "proposed": "todo", "assigned": "todo", "approved": "todo"}
SDK_RE = re.compile(rb'"entrypoint"\s*:\s*"sdk-cli"')
ESITO_RE = re.compile(r"^\**(?:esito|outcome)\**\s*:\s*\**", re.I)
STOPPED_RE = re.compile(r"\b(?:ferm[oaie]|bloccat[oaie]|blocco|interrott[oaie]|fallit[oaie]|impossibile|non riesco|"
                        r"non (?:ho )?(?:potuto|fatto|scritto)|stopped|blocked|could not|couldn't)\b", re.I)
REFLOG_NEW = ("commit", "rebase", "cherry-pick", "revert")   # voci del reflog che creano un commit (non merge, reset, checkout)
GAP_S = 20 * 60          # una pausa piu' lunga spezza l'intervallo di lavoro di una sessione
RUNNING_S = 10 * 60      # una sessione viva attiva negli ultimi 10 minuti della finestra sta ancora lavorando
SCRATCH_RE = re.compile(r"^/tmp/claude-\d+/|/scratchpad(?:/|$)")   # le prove negli scratchpad non sono lavoro della notte
TS_RE = re.compile(rb'"timestamp":"(\d{4}-\d\d-\d\dT[^"]+)"')
DATED_RE = re.compile(r"^(\d{4}-\d\d-\d\d)-(.+)\.md$")
GOAL_SKIP_RE = re.compile(r"^(?:vincoli|aggiornament|causa|contesto|note|per l.app|riferimenti|perch|domande|rischi)", re.I)
GOAL_WORK_RE = re.compile(r"^(?:cosa (?:fare|produrre)|obiettiv|passi|lavoro)", re.I)
DECISION_RE = re.compile(r"(?:decisioni|da te\b|tocca a\b|serve a\b|serve da\b|da fare per)", re.I)

_TL = None


def tl_mod():
    global _TL
    if _TL is None:
        _TL = _load("cm-timeline")
        _TL.FULL_PROMPTS = True   # timeline[].prompt wants up to PROMPT_MAX characters, the events stop at 160
    return _TL


def one_line(t, n=TEXT_MAX):
    t = " ".join(str(t or "").split())
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


PROMPT_MAX = 400   # timeline[].prompt (1.46): what the item was asked to do, before its outcome


def prompt_of(text):
    """The prompt as the app shows it: markdown removed line by line, on one line, cut at a whole word within
    PROMPT_MAX characters; None when there is none."""
    RS = _load("cm-relay-state")
    lines = [RS.strip_markdown(re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", l)) for l in str(text or "").splitlines()]
    return RS.cut_words(" ".join(lines), PROMPT_MAX) or None


def real(p):
    return os.path.realpath(p) if p else ""


# ------------------------------------------------------------------ finestra
def window(now, messages, plans=()):
    """{start, end, start_source, end_source, floor, last_message, morning_message}. `messages` = [{at, session, origin,
    ...}] dei prompt della persona; `plans` = gli istanti dei piani della notte (docs/proposte). Con un piano fra il
    pavimento e le 06:00 la notte parte dal primo (21/10: i piani approvati dalle 00:24 mentre la persona scriveva
    ancora alla master fino all'01:14); senza, dall'ultimo prompt della sera. Finisce al primo messaggio della
    persona dopo le 06:00 dal telefono o alla master (le prove che una sessione manda a un'altra sono «pc»)."""
    t = dt.datetime.fromtimestamp(now)
    eve = t.replace(hour=EVENING_H, minute=0, second=0, microsecond=0)
    if t.hour < EVENING_H:
        eve -= dt.timedelta(days=1)
    morning = int((eve + dt.timedelta(days=1)).replace(hour=MORNING_H).timestamp())
    ev, cap = int(eve.timestamp()), int(now - MAX_H * 3600)
    floor, source = (ev, "floor_20") if ev >= cap else (cap, "cap_14h")
    upto = min(int(now), morning)
    cands = [m for m in messages if floor <= m["at"] <= upto]
    last = max(cands, key=lambda m: m["at"]) if cands else None
    plan = min((int(x) for x in plans if floor <= x <= upto), default=None)
    root = CFG["workspace"].get("root_session_name") or "master"
    wake = [m for m in messages if morning <= m["at"] <= now and (m.get("origin") != "pc" or m.get("session") == root or m.get("root"))]
    first = min(wake, key=lambda m: m["at"]) if wake else None
    start, ssrc = (plan, "night_plan") if plan is not None else (last["at"], "last_message") if last else (floor, source)
    return {"start": start, "end": first["at"] if first else int(now), "start_source": ssrc,
            "end_source": "morning_message" if first else "now", "floor": floor, "morning": morning,
            "last_message": last, "morning_message": first}


def is_sdk(path):
    """Una trascrizione di `claude -p` (coda notturna, lavori lanciati dalla master): i suoi prompt non sono della persona."""
    try:
        with open(path, "rb") as f:
            return bool(SDK_RE.search(f.read(65536)))
    except OSError:
        return False


def live_rows():
    try:
        p = subprocess.run([os.environ.get("CM_BIN") or str(HERE / "supervisor"), "sessions", "--json", "--no-screen"],
                           capture_output=True, text=True, timeout=60)
        return json.loads(p.stdout) if p.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def person_messages(since, now, live):
    TL = tl_mod()
    ws_root = real(cm.expand(CFG["workspace"]["root"]))
    out = []
    for nm, _, cwd, path in TL.sessions_in(since, live=live):
        if is_sdk(path):
            continue
        for e in TL.transcript_events(path, since):
            if e["kind"] == "prompt" and e["at"] <= now:
                out.append({"at": e["at"], "session": nm, "origin": e["ref"] or "pc", "text": one_line(e["text"]),
                            **({"root": True} if cwd and real(cwd) == ws_root else {})})
    return out


# ------------------------------------------------------------------ sessioni della notte
def scratch(cwd):
    return bool(SCRATCH_RE.search(real(cwd)))


def norm_spans(spans):
    out = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] <= GAP_S:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def scan(path, since, until):
    """{spans, assigned, name} di una trascrizione fra since e until: gli intervalli di lavoro (righe con l'ora, spezzate
    da pause oltre GAP_S), i messaggi delle altre sessioni (la master che assegna il lavoro: 21/10, ads-widget e
    watchface non avevano un prompt della persona e sparivano) e il nome dato alla sessione (agent-name)."""
    core = tl_mod().core
    times, assigned, name = [], [], None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return {"spans": [], "assigned": [], "name": None}
    for raw in data.split(b"\n"):
        if core.noise(raw):   # 22/10: un api-request ha un timestamp, e contava come attivita'
            continue
        head = raw[:64]
        if b'"agent-name"' in head or b'"custom-title"' in head:
            try:
                d = json.loads(raw)
                name = d.get("agentName") or d.get("customTitle") or name
            except ValueError:
                pass
            continue
        i = raw.rfind(b'"timestamp":"')   # quello della riga e' l'ultimo: prima ci sono il messaggio e i suoi blocchi
        m = TS_RE.match(raw, i) if i >= 0 else None
        if not m:
            continue
        at = int(core._turn_epoch(m.group(1).decode()) or 0)
        if not since <= at <= until:
            continue
        times.append(at)
        if b'"kind":"peer"' in raw:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            o = d.get("origin") or ((d.get("attachment") or {}).get("origin") if d.get("type") == "attachment" else None)
            if isinstance(o, dict) and o.get("kind") == "peer":
                text, origin = core._origin_of(str(o.get("body") or "").strip())
                if origin == "pc" and text and not any(x["at"] == at and x["text"] == one_line(text, PROMPT_MAX) for x in assigned):
                    assigned.append({"at": at, "from": o.get("name"), "text": one_line(text, PROMPT_MAX)})
    return {"spans": norm_spans([[t, t] for t in times]), "assigned": assigned, "name": name}


def cwd_of(path):
    try:
        with open(path, "rb") as h:
            m = re.search(rb'"cwd":\s*("(?:[^"\\]|\\.)*")', h.read(262144))
    except OSError:
        return ""
    return json.loads(m.group(1)) if m else ""


def rel_under(path, root):
    """Il pezzo di `path` sotto `root`, con le barre in avanti; None se non ci sta. Senza badare a maiuscole e barre
    (D:\\cm-sessions\\personali\\bluey sotto D:/cm-sessions)."""
    p, r = str(path).replace("\\", "/").rstrip("/"), str(root).replace("\\", "/").rstrip("/")
    return p[len(r) + 1:] if p.lower().startswith(r.lower() + "/") else None


def remote_sessions(start, sources):
    """21/10: le sessioni lanciate con `launch --host` (win-bluey, win-albo-crotone): le trascrizioni scritte nella
    finestra, copiate nella copia locale di cm-hosts (mirror_path) se cambiate, e i .md di docs/notte e docs/proposte
    delle loro cartelle. → ([(nome, viva, cartella di qui, trascrizione, host)], {cartella di qui: [(file, host)]})."""
    H = _load("cm-hosts")
    ws_root = real(cm.expand(CFG["workspace"]["root"]))
    rows, docs = [], {}
    for hname, h in H.hosts().items():
        root = str((h.get("sessions") or {}).get("root") or "")
        if hname == "local" or not root or not H.confirmed(h, hname):
            continue
        try:
            a = H.adapter(hname)
            if not hasattr(a, "night_list"):
                sources[f"host:{hname}"] = "unsupported"
                continue
            got, dirs = a.night_list(re.sub(r"[^A-Za-z0-9]", "-", root), [], start), {}
            for t in got["transcripts"]:
                sid = re.split(r"[\\/]", t["path"])[-1].removesuffix(".jsonl")
                dest = H.mirror_path(hname, sid)
                if not (dest.is_file() and dest.stat().st_size == int(t["size"])):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dest.with_name(dest.name + ".night")
                    a.file_get(t["path"], str(tmp))
                    os.replace(tmp, dest)
                cwd = cwd_of(dest)
                rel = rel_under(cwd, root) if cwd else None
                if not rel:
                    continue
                here = os.path.join(ws_root, rel)
                dirs[cwd] = here
                rows.append((f"{hname}-{os.path.basename(rel)}", False, here, str(dest), hname))
            if dirs:
                for f in a.night_list("", sorted(dirs), start)["docs"]:
                    cwd = next((c for c in dirs if rel_under(f["path"], c)), None)
                    if cwd is None:
                        continue
                    dest = Path(cm.expand(CFG["state_dir"])) / "hosts" / "night-docs" / hname / rel_under(cwd, root) / rel_under(f["path"], cwd)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    a.file_get(f["path"], str(dest))
                    os.utime(dest, (int(f["mtime"]), int(f["mtime"])))
                    docs.setdefault(real(dirs[cwd]), []).append((str(dest), hname))
            sources[f"host:{hname}"] = "ok"
        except Exception as e:   # noqa: BLE001 — un host spento non ferma il rapporto
            sources[f"host:{hname}"] = f"error: {one_line(e, 120)}"
    return rows, docs


def night_rows(start, end, live, sources, remote=True):
    """Una riga per trascrizione con lavoro nella finestra: {session, live, project, events, spans, assigned, host,
    path}. Anche senza prompt della persona; mai quelle degli scratchpad; le remote dopo le locali."""
    TL = tl_mod()
    entries = [(nm, lv, cwd, path, "local") for nm, lv, cwd, path in TL.sessions_in(start, live=live)]
    docs = {}
    if remote:
        rem, docs = remote_sessions(start, sources)
        entries += rem
    out, seen = [], set()
    for nm, lv, cwd, path, host in entries:
        if not cwd or scratch(cwd) or path in seen:
            continue
        seen.add(path)
        sc = scan(path, start, end)
        if not sc["spans"]:
            continue
        ev = [e for e in TL.transcript_events(path, start) if e["at"] <= end]
        out.append({"session": nm if host == "local" else (sc["name"] or nm), "live": lv, "project": cwd, "events": ev,
                    "spans": sc["spans"], "assigned": sc["assigned"], "host": host, "path": path})
    by = {}
    for r in out:
        by.setdefault(real(r["project"]), []).append(r)
    for proj, group in by.items():
        if os.path.isdir(proj):
            for e in TL.task_events(proj, start):
                if e["at"] <= end:
                    min(group, key=lambda r: min((abs(a - e["at"]) for s_ in r["spans"] for a in s_), default=10 ** 9)).setdefault("events", []).append(e)
    return out, docs


_REPO = {}


def repo_of(p):
    """Il progetto di una cartella: la cartella principale del repo per un worktree che si chiama «<repo>-…» (21/10:
    supervisor-notte-stato e' il worktree di supervisor), la cartella stessa altrimenti (supervisor-app e' un worktree
    di team-supervisor-app-watch, ma e' un progetto suo)."""
    p = real(p)
    if p and p not in _REPO:
        out = git_out(p, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir") if os.path.isdir(p) else None
        gd, common = ((out or "").split("\n") + ["", ""])[:2]
        main = os.path.dirname(common) if common.endswith("/.git") else ""
        _REPO[p] = main if main and gd != common and os.path.basename(p).startswith(os.path.basename(main) + "-") else p
    return _REPO.get(p, p)


# ------------------------------------------------------------------ fonti
def iso_epoch(s):
    try:
        return int(dt.datetime.fromisoformat(str(s)).timestamp())
    except ValueError:
        return None


def read_jsonl(p):
    out = []
    for line in Path(p).read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def report_output(path):
    """Il testo sotto «## Output» dell'esito .md del turno di notte, o None."""
    try:
        with open(path, errors="replace") as f:
            text = f.read(262144)
    except OSError:
        return None
    _, sep, out = text.partition("\n## Output\n")
    return out if sep else None


def job_outcome(rc, out):
    """(esito, dettaglio) di un lavoro della coda. rc=0 dice solo che `claude -p` e' uscito: d17d7b00 (07/10) e' uscito
    con 0 fermo prima del piano, senza codice. Conta la riga «Esito:» che il lavoro scrive per ultima: se dice che e'
    fermo o bloccato, l'esito e' `stopped`. Senza «Esito:» vale la prima riga dell'output, con la stessa regola."""
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    esito = next((ln for ln in reversed(lines) if ESITO_RE.match(ln)), None)
    detail = one_line(ESITO_RE.sub("", esito) if esito else (lines[0] if lines else "")) or None
    if rc != 0:
        return "failed", detail
    if STOPPED_RE.search(esito or (lines[0] if lines else "")):
        return "stopped", detail
    return "ok", detail


def night_jobs(start, now, sources):
    N = CFG["night"]
    jobs, queue = [], []
    done_p, queue_p = Path(cm.expand(N["done_file"])), Path(cm.expand(N["queue_file"]))
    if done_p.is_file():
        for r in read_jsonl(done_p):
            end = iso_epoch(r.get("finished"))
            if end is None or not start <= end <= now:
                continue
            begin = int(r["started"]) if r.get("started") else end - int(r.get("seconds") or 0)
            rep = r.get("report") or None
            outcome, detail = job_outcome(r.get("rc"), report_output(rep) if rep else None)
            jobs.append({"kind": "night_job", "id": r.get("id"), "title": one_line(f"{os.path.basename(r.get('dir') or '')} — {r.get('prompt') or ''}"),
                         "project": real(r.get("dir")), "start": begin, "end": end, "outcome": outcome,
                         "detail": detail, "report": rep if rep and os.path.isfile(rep) else None,
                         "rc": r.get("rc"), "prompt": prompt_of(r.get("prompt"))})
        sources["night_done"] = "ok"
    else:
        sources["night_done"] = "missing"
    if queue_p.is_file():
        for r in read_jsonl(queue_p):
            if r.get("started"):
                jobs.append({"kind": "night_job", "id": r.get("id"), "title": one_line(f"{os.path.basename(r.get('dir') or '')} — {r.get('prompt') or ''}"),
                             "project": real(r.get("dir")), "start": int(r["started"]), "end": None, "outcome": "running",
                             "detail": None, "report": None, "rc": None, "prompt": prompt_of(r.get("prompt"))})
            else:
                queue.append({"id": r.get("id"), "project": real(r.get("dir")), "prompt": one_line(r.get("prompt")), "added": r.get("added")})
        sources["night_queue"] = "ok"
    else:
        sources["night_queue"] = "missing"
    return jobs, queue


def registry(sources):
    try:
        con = _load("cm-tasks").connect(create=False)
    except Exception as e:   # noqa: BLE001 — il registro e' facoltativo
        sources["registry"] = f"error: {one_line(e, 120)}"
        return None
    sources["registry"] = "ok" if con is not None else "missing"
    return con


def approvals(con):
    if con is None:
        return []
    out = []
    for r in con.execute("SELECT id, title, project FROM tasks WHERE state='awaiting_ok' ORDER BY updated_at"):
        a = con.execute("SELECT what, where_, at FROM approvals WHERE task=? AND by_ IS NULL ORDER BY n DESC LIMIT 1", (r["id"],)).fetchone()
        out.append({"task": r["id"], "title": one_line(r["title"]), "project": real(r["project"]),
                    "what": one_line(a["what"]) if a else None, "where": one_line(a["where_"]) if a else None,
                    "requested_at": a["at"] if a else None})
    return out


def commitments_of(con, now):
    """1.76 (22/10, P2): gli impegni del registro nel resoconto del mattino — le stesse voci di `task impegni --json`."""
    if con is None:
        return []
    try:
        return _load("cm-tasks").commitments(con, now_=now, git=False)
    except Exception:   # noqa: BLE001 — il registro e' facoltativo
        return []


def relay_state(sources):
    d = Path(cm.expand((CFG.get("relay") or {}).get("dir") or "~/.cc-supervisor/relay"))
    for f, keys in ((d / "last-state.json", ("full", "state")), (d / "local" / "state.json", (None,))):
        if not f.is_file():
            continue
        try:
            raw = json.loads(f.read_text())
        except (OSError, ValueError) as e:
            sources["relay_state"] = f"error: {one_line(e, 120)}"
            return {}
        for k in keys:
            st = raw.get(k) if k else raw
            if isinstance(st, dict) and isinstance(st.get("sessions"), list):
                sources["relay_state"] = "ok"
                return st
    sources["relay_state"] = "missing"
    return {}


def state_project(s):
    p = s.get("project") or ""
    if p and not os.path.isabs(p):
        p = os.path.join(cm.expand(CFG["workspace"]["root"]), p)
    return real(p)


def attention(state, con):
    questions, unblock = [], []
    for s in state.get("sessions") or []:
        q = s.get("question")
        if q:
            questions.append({"session": s.get("name"), "project": state_project(s), "id": q.get("id"), "kind": q.get("kind"),
                              "text": one_line(q.get("text")), "options": q.get("options") or [], "tier": q.get("tier"),
                              "asked_at": q.get("asked_at")})
        for st in s.get("next_steps") or []:
            if st.get("blocking"):
                unblock.append({"session": s.get("name"), "project": state_project(s), "text": one_line(st.get("text"))})
    return {"questions": questions, "approvals": approvals(con), "unblock": unblock}


# ------------------------------------------------------------------ cronologia
def git_out(proj, *args):
    try:
        p = subprocess.run(["git", "-C", proj, *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def made_here(proj, since):
    """Gli hash dei commit creati in questo worktree dalla finestra in poi: il reflog di HEAD e' per worktree."""
    out = git_out(proj, "reflog", "show", "--date=unix", "--format=%H%x09%gd%x09%gs", "HEAD") or ""
    hs = set()
    for line in out.splitlines():
        h, sel, subj = (line.split("\t", 2) + ["", ""])[:3]
        m = re.search(r"\{(\d+)\}", sel)
        if m and int(m.group(1)) < since:
            break
        if subj.split(" ", 1)[0].rstrip(":") in REFLOG_NEW:
            hs.add(h[:7])
    return hs


def of_job(r, jobs):
    """La trascrizione `claude -p` di un lavoro della coda: stessa cartella, dentro l'intervallo del lavoro (±2 minuti)."""
    ev = [e for e in r["events"] if e["kind"] not in ("commit", "task")] or r["events"]
    if r.get("spans"):
        begin, end = r["spans"][0][0], r["spans"][-1][1]
    elif ev:
        begin, end = ev[0]["at"], ev[-1]["at"]
    else:
        return False
    proj = real(r["project"])
    return any(j["project"] == proj and j["start"] - 120 <= begin and end <= (j["end"] or 10 ** 12) + 120 for j in jobs)


def merge_rows(rows, jobs):
    """Una riga per sessione: le conversazioni della stessa sessione nella stessa cartella (handoff, /clear, la chiusa
    e la viva) diventano una, tranne quelle dei lavori della coda, che restano del loro lavoro; una riga con gli stessi
    eventi propri di un'altra (la stessa trascrizione letta due volte) si scarta."""
    by, out, seen = {}, [], {}
    for r in rows:
        k = (r["session"], real(r["project"]))
        if of_job(r, jobs):
            out.append(dict(r, events=list(r["events"])))
            continue
        if k in by:
            m = by[k]
            m["events"] = sorted({(e["at"], e["kind"], e["text"]): e for e in m["events"] + r["events"]}.values(), key=lambda e: e["at"])
            m["live"] = m["live"] or r["live"]
            m["spans"] = norm_spans(m.get("spans", []) + r.get("spans", []))
            m["assigned"] = sorted(m.get("assigned", []) + r.get("assigned", []), key=lambda x: x["at"])
            continue
        by[k] = dict(r, events=list(r["events"]))
        out.append(by[k])
    kept = []
    for r in sorted(out, key=lambda r: not r["live"]):
        own = tuple((e["at"], e["kind"], e["text"]) for e in r["events"] if e["kind"] not in ("commit", "task"))
        if own and own in seen:
            continue
        seen[own] = r
        kept.append(r)
    return kept


def assign_commits(rows, since, now):
    """I commit di un repo una volta sola, anche con piu' worktree (`git log --all` li vede da tutti): vanno al
    worktree che li ha creati (reflog), alla sua sessione piu' vicina nel tempo; se nessuno li ha creati qui (un
    fast-forward), alla sessione piu' vicina fra tutte quelle del repo."""
    TL, groups = tl_mod(), {}
    for r in rows:
        r["events"] = [e for e in r["events"] if e["kind"] != "commit"]
        p = real(r["project"])
        common = git_out(p, "rev-parse", "--path-format=absolute", "--git-common-dir") if p and os.path.isdir(p) else None
        if common:
            groups.setdefault(common.strip(), []).append(r)
    for group in groups.values():
        projs = sorted({real(r["project"]) for r in group})
        made = {p: made_here(p, since) for p in projs}
        cs = {}
        for p in projs:
            for c in TL.commits(p, since):
                if c["at"] > now:
                    continue   # niente dal futuro dell'istante chiesto, come la cronologia
                cs.setdefault(c["ref"], c)
        for h, c in cs.items():
            owner = [p for p in projs if any(x.startswith(h) or h.startswith(x) for x in made[p])]
            pool = [r for r in group if real(r["project"]) in owner] or group
            best = min(pool, key=lambda r: min((abs(x["at"] - c["at"]) for x in r["events"]), default=10 ** 9) - (10 ** 8 if r["live"] else 0))
            best["events"].append(c)
    for r in rows:
        r["events"].sort(key=lambda e: e["at"])
    return [r for r in rows if r["events"] or r.get("spans")]


def running(r, end):
    """Una sessione viva che ha lavorato negli ultimi RUNNING_S della finestra, o la cui ultima riga e' un prompt."""
    return bool(r["live"] and ((r.get("spans") and r["spans"][-1][1] >= end - RUNNING_S) or (r["events"] and r["events"][-1]["kind"] == "prompt")))


def session_outcome(ev, live, busy=None):
    if busy if busy is not None else (live and ev and ev[-1]["kind"] == "prompt"):
        return "running"
    checks = [e for e in ev if e["kind"] in ("test", "task") and e["ok"] is not None]
    if checks and checks[-1]["ok"] is False:
        return "failed"
    if any(e["kind"] == "outcome" for e in ev) or (checks and checks[-1]["ok"]):
        return "ok"
    return "unknown"


def session_items(rows, jobs, end):
    ws_root = real(cm.expand(CFG["workspace"]["root"]))
    items = []
    for r in rows:
        ev, spans = r["events"], r.get("spans") or []
        if not (ev or spans) or of_job(r, jobs):
            continue   # la sessione `claude -p` di un lavoro della coda: e' quel lavoro, non una riga in piu'
        d = real(r["project"])
        # dagli intervalli: gli eventi assegnati dopo (l'esito accettato dalla master al mattino) non allungano la sessione
        begin, last = (spans[0][0], spans[-1][1]) if spans else (ev[0]["at"], ev[-1]["at"])
        outcome = session_outcome(ev, r["live"], running(r, end))
        outs = [e["text"] for e in ev if e["kind"] == "outcome"]
        asg = (r.get("assigned") or [None])[0]
        first = next((e.get("full") or e["text"] for e in ev if e["kind"] == "prompt"), None)
        items.append({"kind": "session", "id": r["session"], "title": r["session"], "project": repo_of(d), "dir": d,
                      "host": r.get("host") or "local", "role": "master" if d == ws_root else None, "start": begin,
                      "end": None if outcome == "running" else last, "spans": spans, "outcome": outcome,
                      "detail": outs[-1] if outs else one_line(ev[-1]["text"]) if ev else (asg["text"] if asg else None),
                      "report": None, "live": bool(r["live"]), "assigned": asg["text"] if asg else None,
                      "prompt": prompt_of(first or (asg["text"] if asg else None)),
                      "counts": {k: sum(1 for e in ev if e["kind"] == k[:-1]) for k in ("prompts", "tests", "commits")}})
    return items


# ------------------------------------------------------------------ schede
def boxes(text):
    out = []
    for ln in text.splitlines():
        m = BOX_RE.match(ln)
        if m:
            out.append({"title": one_line(m.group(2), 160), "state": BOX_STATE[m.group(1)]})
    return out


def parts_of(proj, start, now, con):
    """(fonte, file, parti): TASKS.md, poi il piano .md con caselle toccato nella finestra, poi il registro."""
    f = Path(proj) / "TASKS.md"
    if f.is_file():
        try:
            p = boxes(f.read_text(errors="replace"))
        except OSError:
            p = []
        if p:
            return "TASKS.md", str(f), p
    plans = []
    for f in (Path(proj) / "docs" / "plans").glob("*.md"):
        try:
            mt = f.stat().st_mtime
        except OSError:
            continue
        if start <= mt <= now:
            plans.append((mt, f))
    for _, f in sorted(plans, reverse=True):
        try:
            p = boxes(f.read_text(errors="replace"))
        except OSError:
            continue
        if p:
            return "plan", str(f), p
    if con is not None:
        rows = [r for r in con.execute("SELECT title, state, project FROM tasks ORDER BY created_at, id") if real(r["project"]) == proj and r["state"] in TASK_STATE]
        if rows:
            return "registry", None, [{"title": one_line(r["title"], 160), "state": TASK_STATE[r["state"]]} for r in rows]
    return None, None, []


def clean_title(t, n=160):
    """Il titolo di una voce: la parte in grassetto se la voce comincia con una, senza i due punti; senza markdown."""
    m = re.match(r"\s*\*\*(.+?)\*\*", t)
    t = m.group(1).rstrip(" :.") if m else re.sub(r"[*_`]", "", t)
    return one_line(re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t), n)


def sections(text):
    """[(titolo ##, [righe])] e il titolo # del file."""
    title, secs = None, []
    for ln in text.splitlines():
        if title is None and ln.startswith("# "):
            title = ln[2:].strip()
        m = re.match(r"^##\s+(.+?)\s*$", ln)
        if m:
            secs.append((m.group(1), []))
        elif secs:
            secs[-1][1].append(ln)
    return title, secs


def numbered(lines):
    return [clean_title(m.group(1)) for m in (re.match(r"^\d+[.)]\s+(.+)$", ln) for ln in lines) if m]


def read_goal(path):
    """Un obiettivo della notte da docs/proposte: {title, file, at, points}. I punti: i titoli ## numerati se sono
    almeno due, poi le voci numerate di «Cosa fare/produrre», poi i titoli ## che non sono vincoli o contesto."""
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return None
    title, secs = sections(text)
    heads = [h for h, _ in secs]
    nums = [clean_title(re.sub(r"^\d+[.)]\s*", "", h)) for h in heads if re.match(r"^\d+[.)]\s", h)]
    work = next((numbered(b) for h, b in secs if GOAL_WORK_RE.match(h) and numbered(b)), [])
    rest = [clean_title(h) for h in heads if not GOAL_SKIP_RE.match(h) and not GOAL_WORK_RE.match(h)]
    pts = nums if len(nums) >= 2 else work or rest or numbered(text.splitlines())
    return {"title": clean_title(re.sub(r"\s*\((?:richiesta|segnalazione)[^)]*\)\s*$", "", title or Path(path).stem), 200),
            "file": str(path), "at": int(os.path.getmtime(path)), "points": [{"title": t} for t in pts[:PARTS_MAX]]}


def read_report(path):
    """Un resoconto di docs/notte: {file, title, at, fine, fine_at, esito, stopped, decisions}. «FINE» e' l'ultima
    riga non vuota; l'esito e' l'ultima riga «Esito:» o il primo paragrafo della sezione «## Esito»; le decisioni sono
    le voci delle sezioni per la persona («Decisioni aperte per …», «Da te»)."""
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return None
    title, secs = sections(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    fine = bool(lines) and lines[-1].strip("*_ ") == "FINE"
    esito = next((ESITO_RE.sub("", ln).strip("* ") for ln in reversed(lines) if ESITO_RE.match(ln)), None)
    if not esito:
        body = next((b for h, b in secs if re.match(r"^(?:esito|outcome)\b", h, re.I)), [])
        para = []
        for ln in body:
            if ln.strip():
                para.append(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", ln.strip()))
            elif para:
                break
        esito = " ".join(para) or None
    if not esito:   # senza esito, il primo paragrafo del file che non sia un titolo
        para = []
        for ln in text.splitlines():
            if ln.strip() and not ln.startswith("#"):
                para.append(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", ln.strip()))
            elif para:
                break
        esito = " ".join(para) or None
    decisions = [clean_title(m.group(1)) for h, b in secs if DECISION_RE.search(h)
                 for m in (re.match(r"^(?:[-*+]|\d+[.)])\s+(.+)$", ln) for ln in b) if m]
    at = int(os.path.getmtime(path))
    esito = one_line(_load("cm-relay-state").strip_markdown(esito), 300) if esito else None
    return {"file": str(path), "title": clean_title(re.sub(r"^Notte\s+[\d/]+\s*[—-]\s*", "", title or Path(path).stem), 200),
            "at": at, "fine": fine, "fine_at": at if fine else None, "esito": esito,
            "stopped": bool(esito and STOPPED_RE.search(esito)), "decisions": decisions[:10]}


def night_files(dirs, remote, days, floor, end):
    """{proposte: [file], notte: [file]} con la data della notte nel nome; per nome il piu' recente (la copia di win
    di un resoconto e' piu' avanti di quella seminata qui). Le proposte scritte dopo la fine della notte no."""
    best = {}
    cand = [(f, "local") for d in dirs for k in ("proposte", "notte") for f in (Path(d) / "docs" / k).glob("*.md")]
    for f, host in cand + [(Path(f), h) for f, h in remote]:
        m = DATED_RE.match(f.name)
        try:
            mt = f.stat().st_mtime
        except OSError:
            continue
        kind = f.parent.name
        if not m or m.group(1) not in days or mt < floor or (kind == "proposte" and mt > end):
            continue
        k = (kind, f.name)
        if k not in best or mt > best[k][1]:
            best[k] = (f, mt, host)
    out = {"proposte": [], "notte": []}
    for (kind, _), (f, _, host) in sorted(best.items(), key=lambda x: x[1][1]):
        out[kind].append((str(f), host))
    return out


def slug_of(f):
    m = DATED_RE.match(os.path.basename(f))
    return m.group(2) if m else Path(f).stem


def match_reports(goals, reports):
    """Ogni resoconto al suo obiettivo: lo stesso nome, poi un obiettivo il cui testo lo nomina, poi un pezzo del nome
    in comune (cc-2.1.305 e claude-code-2.1.305), poi l'unico obiettivo. → i resoconti senza obiettivo."""
    texts, slugs = [], []
    for g in goals:   # un obiettivo dal messaggio di una sessione non ha file: vale il suo titolo
        try:
            texts.append(Path(g["file"]).read_text(errors="replace").lower() if g["file"] else g["title"].lower())
        except OSError:
            texts.append("")
        slugs.append(slug_of(g["file"]).lower() if g["file"] else "")
    toks = lambda sl: {t for t in sl.split("-") if len(t) >= 4 and t not in ("notte", "funzioni", "lavoro")}  # noqa: E731
    loose = []
    for r in reports:
        sl = slug_of(r["file"]).lower()
        i = next((i for i, x in enumerate(slugs) if x and x == sl), None)
        i = i if i is not None else next((i for i, x in enumerate(texts) if sl in x or sl.replace("-", " ") in x), None)
        i = i if i is not None else next((i for i, x in enumerate(slugs) if toks(x) & toks(sl)), None)
        i = i if i is not None else (0 if len(goals) == 1 else None)
        g = goals[i] if i is not None else None
        (g["reports"] if g else loose).append(r)
    return loose


def assigned_goals(rows):
    """Senza proposte, gli obiettivi sono i messaggi con cui un'altra sessione ha dato il lavoro (21/10: Bluey su win
    non aveva una proposta, solo il messaggio della master), il primo di ogni sessione."""
    out = []
    for r in sorted(rows, key=lambda r: (r.get("assigned") or [{"at": 0}])[0]["at"]):
        a = (r.get("assigned") or [None])[0]
        if a:
            t = re.sub(r"^Dalla master[^:]{0,80}:\s*", "", a["text"])
            out.append({"title": one_line(re.split(r"(?<=[.!?])\s", t, 1)[0], 200), "file": None, "at": a["at"], "points": [],
                        "host": r.get("host") or "local", "source": "assigned", "session": r["session"], "reports": []})
    return out


def goal_state(g, busy, worked):
    reps = g["reports"]
    if any(r["stopped"] for r in reps):
        return "stopped", None
    if reps and all(r["fine"] or r.get("closed_by") for r in reps):
        return "done", max(r["fine_at"] or 0 for r in reps) or max(r["closed_at"] for r in reps)
    if busy:
        return "running", None
    return ("partial" if reps or worked else "todo"), None


def git_marks(repo, start, end):
    """I push (il reflog dei rami remoti: «update by push») e i tag della finestra: [{at, kind, text, ref}]."""
    out = []
    refs = git_out(repo, "for-each-ref", "--format=%(refname)%09%(creatordate:unix)", "refs/remotes", "refs/tags") or ""
    for line in refs.splitlines():
        ref, _, t = line.partition("\t")
        if ref.startswith("refs/tags/"):
            if t.isdigit() and start <= int(t) <= end:
                out.append({"at": int(t), "kind": "release", "text": ref[len("refs/tags/"):], "ok": None, "ref": ref})
            continue
        if ref.endswith("/HEAD") or not t.isdigit() or int(t) < start - 86400:
            continue
        for ln in (git_out(repo, "reflog", "show", "--date=unix", "--format=%gd%x09%gs", ref) or "").splitlines():
            sel, _, subj = ln.partition("\t")
            m = re.search(r"\{(\d+)\}", sel)
            if m and start <= int(m.group(1)) <= end and "push" in subj:
                out.append({"at": int(m.group(1)), "kind": "push", "text": ref.split("/", 3)[-1], "ok": None, "ref": ref})
    return out


def where_of(goals, loose, waiting, busy, ev):
    """Dove e' arrivato il progetto: {state, text, missing, needs_you}. state: stopped (aspetta la persona o un
    resoconto dice fermo), running, done (ogni obiettivo con il suo resoconto finito), partial, unknown (niente
    obiettivi ne' resoconti: solo eventi)."""
    reps = [r for g in goals for r in g["reports"]] + loose
    needs = list(dict.fromkeys(waiting + [d for r in reps for d in r["decisions"]]))[:10]
    last = max(reps, key=lambda r: (r["fine"], r["at"])) if reps else None   # il resoconto che chiude, se c'e'
    stopped = [r for r in reps if r["stopped"]]
    if waiting:
        return {"state": "stopped", "text": waiting[0], "missing": [], "needs_you": needs}
    if stopped:
        return {"state": "stopped", "text": stopped[-1]["esito"], "missing": [g["title"] for g in goals if g["state"] != "done"], "needs_you": needs}
    if busy:
        return {"state": "running", "text": last["esito"] if last else (one_line(ev[-1]["text"]) if ev else None), "missing": [], "needs_you": needs}
    if goals or loose:
        missing = [g["title"] for g in goals if g["state"] != "done"] + [f"resoconto senza FINE: {r['title']}" for r in loose if not (r["fine"] or r.get("closed_by"))]
        text = last["esito"] if last else ([e["text"] for e in ev if e["kind"] == "outcome"] or [one_line(ev[-1]["text"]) if ev else None])[-1]
        return {"state": "partial" if missing else "done", "text": text, "missing": missing, "needs_you": needs}
    outs = [e["text"] for e in ev if e["kind"] == "outcome"]
    return {"state": "unknown", "text": outs[-1] if outs else (one_line(ev[-1]["text"]) if ev else None), "missing": [], "needs_you": needs}


def project_cards(rows, items, att, state, win, con, remote_docs):
    start, end, floor = win["start"], win["end"], win["floor"]
    ws_root = real(cm.expand(CFG["workspace"]["root"]))
    days = {dt.date.fromtimestamp(floor).isoformat(), (dt.date.fromtimestamp(floor) + dt.timedelta(days=1)).isoformat()}
    groups = {}
    for r in rows:
        repo = repo_of(r["project"])
        if repo and repo != ws_root:
            g = groups.setdefault(repo, {"dirs": {repo}, "rows": []})
            g["dirs"].add(real(r["project"]))
            g["rows"].append(r)
    for x in items + att["questions"] + att["approvals"] + att["unblock"]:
        repo = repo_of(x.get("project"))
        if repo and repo != ws_root:
            groups.setdefault(repo, {"dirs": {repo}, "rows": []})["dirs"].add(real(x["project"]))
    nexts = {}
    for s in state.get("sessions") or []:
        # il «→ prossimo» viene dal recap del progetto: uno di giorni fa non e' il prossimo passo di stanotte
        if s.get("next") and (s.get("next_at") or 0) >= floor - NEXT_MAX_AGE_S:
            nexts.setdefault(repo_of(state_project(s)), one_line(s["next"]))
    cards = []
    for repo, g in groups.items():
        if not os.path.isdir(repo):
            continue
        dirs = [repo] + sorted(g["dirs"] - {repo})
        src, f, parts = next((x for x in (parts_of(d, start, end, con) for d in dirs) if x[2]), (None, None, []))
        mine = lambda x: repo_of(x.get("project")) == repo   # noqa: E731
        waiting = [f"domanda: {q['text']}" for q in att["questions"] if mine(q)] \
            + [f"ok: {a['title']}" for a in att["approvals"] if mine(a)] \
            + [f"!: {u['text']}" for u in att["unblock"] if mine(u)]
        todo = next((x["title"] for x in parts if x["state"] == "todo"), None)
        ev = sorted({(e["at"], e["kind"], e["text"]): e for r in g["rows"] for e in r["events"] if e["kind"] != "prompt"}.values(), key=lambda e: e["at"])
        busy = any(running(r, end) for r in g["rows"])
        spans = sorted(({"session": r["session"], "host": r.get("host") or "local", "start": a, "end": b} for r in g["rows"] for a, b in r.get("spans") or []),
                       key=lambda x: (x["start"], x["session"]))
        files = night_files(dirs, [x for d in dirs for x in remote_docs.get(d, [])], days, floor, end)
        goals = [dict(x, host=h, source="proposta", reports=[]) for x, h in ((read_goal(f_), h_) for f_, h_ in files["proposte"]) if x] \
            or assigned_goals(g["rows"])
        reports = [dict(x, host=h) for x, h in ((read_report(f_), h_) for f_, h_ in files["notte"]) if x]
        # 21/10 (fable-director): un resoconto con «FINE» chiude quelli senza scritti prima (il resoconto finale)
        for r in reports:
            last = max((x for x in reports if x["fine"] and x["at"] >= r["at"]), key=lambda x: x["at"], default=None)
            if not r["fine"] and last:
                r["closed_by"], r["closed_at"] = last["file"], last["fine_at"]
        loose = match_reports(goals, reports)
        for gl in goals:
            gl["state"], gl["done_at"] = goal_state(gl, busy, bool(spans))
            gl["points"] = [dict(pt, state=gl["state"], done_at=gl["done_at"]) for pt in gl["points"]]
        marks = [e for e in ev if e["kind"] == "commit"] + git_marks(repo, start, end) \
            + [{"at": r["fine_at"], "kind": "fine", "text": r["title"], "ok": True, "ref": r["file"]} for r in reports if r["fine"]]
        if not (parts or waiting or ev or goals or reports or sum(x["end"] - x["start"] for x in spans) >= 60):
            continue   # una sessione che nella finestra ha solo ricevuto prompt: niente da mostrare nella scheda
        cards.append({"name": os.path.basename(repo.rstrip("/")) or repo, "path": repo, "worktrees": dirs[1:],
                      "hosts": sorted({x["host"] for x in spans}), "parts_source": src, "parts_file": f,
                      "parts": parts[:PARTS_MAX], "parts_total": len(parts), "waiting_on": waiting,
                      "next": nexts.get(repo) or todo, "events": ev[-EVENTS_MAX:], "spans": spans, "goals": goals,
                      "reports": loose, "milestones": sorted(marks, key=lambda e: e["at"])[-EVENTS_MAX:],
                      "where": where_of(goals, loose, waiting, busy, ev)})
    cards.sort(key=lambda c: (-len(c["waiting_on"]), -(max([c["events"][-1]["at"] if c["events"] else 0] + [x["end"] for x in c["spans"]])), c["name"]))
    return cards


def plan_times(entries, floor, morning):
    """Gli istanti dei piani della notte: le proposte con la data della notte nel nome scritte fra il pavimento e le
    06:00, nei repo delle sessioni toccate dalla sera."""
    days = {dt.date.fromtimestamp(floor).isoformat(), (dt.date.fromtimestamp(floor) + dt.timedelta(days=1)).isoformat()}
    out = []
    for repo in {repo_of(cwd) for _, _, cwd, _ in entries if cwd and not scratch(cwd)}:
        for f in (Path(repo) / "docs" / "proposte").glob("*.md"):
            m = DATED_RE.match(f.name)
            try:
                mt = int(f.stat().st_mtime)
            except OSError:
                continue
            if m and m.group(1) in days and floor <= mt <= morning:
                out.append(mt)
    return out


def night_title(cards):
    n = {k: sum(1 for c in cards if c["where"]["state"] == k) for k in ("done", "partial", "stopped", "running")}
    bits = [f"{n['done']} fatti" if n["done"] else "", f"{n['partial']} in parte" if n["partial"] else "",
            f"{n['stopped']} fermi" if n["stopped"] else "", f"{n['running']} in corso" if n["running"] else ""]
    bits = [b for b in bits if b]
    return f"{len(cards)} progetti" + (": " + ", ".join(bits) if bits else "") if cards else "nessun lavoro"


# ------------------------------------------------------------------ rapporto
def build(now=None, remote=True):
    now = int(now or time.time())
    sources = {}
    live = live_rows()
    floor = window(now, [])["floor"]
    try:
        msgs = person_messages(floor, now, live)
        sources["transcripts"] = "ok"
    except Exception as e:   # noqa: BLE001 — senza prompt la finestra parte dal pavimento
        msgs, sources["transcripts"] = [], f"error: {one_line(e, 120)}"
    try:
        plans = plan_times(tl_mod().sessions_in(floor, live=live), floor, window(now, [])["morning"])
    except Exception:   # noqa: BLE001 — senza piani vale l'ultimo prompt della sera
        plans = []
    win = window(now, msgs, plans)
    start, end = win["start"], win["end"]
    jobs, queue = night_jobs(start, end, sources)
    remote_docs = {}
    try:
        rows, remote_docs = night_rows(start, end, live, sources, remote)
        rows = assign_commits(merge_rows(rows, jobs), start, end)
        sources["timeline"] = "ok"
    except Exception as e:   # noqa: BLE001
        rows, sources["timeline"] = [], f"error: {one_line(e, 120)}"
    con = registry(sources)
    state = relay_state(sources)
    att = attention(state, con)
    commitments = commitments_of(con, now)
    items = sorted(jobs + session_items(rows, jobs, end), key=lambda x: (x["start"], x["id"] or ""))
    cards = project_cards(rows, items, att, state, win, con, remote_docs)
    return {"schema": SCHEMA, "v": V, "generated_at": int(time.time()), "date": dt.date.fromtimestamp(win["morning"]).isoformat(),
            "title": night_title(cards), "window": win, "attention": att, "commitments": commitments, "timeline": items, "queue": queue,
            "projects": cards, "sources": sources}


# ------------------------------------------------------------------ anteprima HTML
COLOR = {"ok": "#2e9e5b", "failed": "#d64545", "stopped": "#e0a020", "running": "#3b82f6", "unknown": "#9aa3ad"}
PART_MARK = {"done": "✓", "running": "▶", "blocked": "■", "todo": "○"}
GOAL_CLASS = {"done": "done", "running": "running", "stopped": "blocked", "partial": "todo", "todo": "todo"}
WHERE_LABEL = {"done": "fatto", "partial": "in parte", "stopped": "fermo", "running": "in corso", "unknown": "solo eventi"}
CSS = """
body{font:15px/1.45 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1d232b}
main{max-width:980px;margin:0 auto;padding:20px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:26px 0 10px}
.sub{color:#5b6670;font-size:13px}
.box{background:#fff;border:1px solid #e1e5ea;border-radius:10px;padding:12px 14px;margin:8px 0}
.att{border-left:4px solid #e0a020}.att b{display:block}
.opt{display:inline-block;border:1px solid #c9d1d9;border-radius:14px;padding:1px 10px;margin:4px 6px 0 0;font-size:13px}
.lane{position:relative;height:26px;margin:4px 0;background:#eef1f4;border-radius:5px}
.bar{position:absolute;top:3px;height:20px;border-radius:4px;min-width:4px}
.lab{display:flex;justify-content:space-between;font-size:13px;margin-top:10px}
.axis{position:relative;height:16px;font-size:11px;color:#5b6670}.axis span{position:absolute;transform:translateX(-50%)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:10px}
.card h3{margin:0 0 6px;font-size:15px}.parts{list-style:none;padding:0;margin:6px 0}
.parts li{font-size:13px}.done{color:#2e9e5b}.running{color:#3b82f6}.blocked{color:#d64545}.todo{color:#5b6670}
.ev{font-size:12px;color:#3c4650;margin:2px 0}.empty{color:#5b6670;font-style:italic}
@media (prefers-color-scheme:dark){body{background:#14181d;color:#e4e8ec}.box{background:#1d232b;border-color:#2c343e}
.lane{background:#262e38}.sub,.axis,.todo,.empty{color:#9aa3ad}.ev{color:#c2c9d0}}
"""


def hm(t):
    return time.strftime("%H:%M", time.localtime(t)) if t else "…"


def render_html(rep):
    e = html.escape
    w = rep["window"]
    span = max(1, w["end"] - w["start"])
    lm = w.get("last_message")
    out = [f"<!doctype html><html lang=\"it\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
           f"<title>Notte {e(rep['date'])}</title><style>{CSS}</style></head><body><main>",
           f"<h1>Notte del {e(rep['date'])}</h1><div class=\"sub\">{e(rep.get('title') or '')} · dalle {hm(w['start'])} alle {hm(w['end'])} · inizio: {e(w['start_source'])}"
           + (f" ({e(lm['session'])}, {e(lm['origin'])}: «{e(lm['text'][:80])}»)" if lm else "") + "</div>"]
    a = rep["attention"]
    out.append("<h2>Serve a te</h2>")
    if not (a["questions"] or a["approvals"] or a["unblock"]):
        out.append("<div class=\"box empty\">Niente in sospeso.</div>")
    for q in a["questions"]:
        opts = "".join(f"<span class=\"opt\">{e(str(o.get('n')))} {e(str(o.get('label')))}</span>" for o in q["options"])
        out.append(f"<div class=\"box att\"><b>Domanda · {e(q['session'] or '')}</b>{e(q['text'])}<div>{opts}</div></div>")
    for x in a["approvals"]:
        out.append(f"<div class=\"box att\"><b>Ok · {e(x['title'])}</b>{e(x['what'] or '')}" + (f" → {e(x['where'])}" if x["where"] else "") + "</div>")
    for u in a["unblock"]:
        out.append(f"<div class=\"box att\"><b>Da sbloccare · {e(u['session'] or '')}</b>{e(u['text'])}</div>")
    # 1.76 (22/10, P2): gli impegni — approvati e non compiuti — nel resoconto del mattino, i fermi evidenziati
    out.append("<h2>Impegni</h2>")
    cms = rep.get("commitments") or []
    if not cms:
        out.append("<div class=\"box empty\">Nessun impegno aperto.</div>")
    for x in cms:
        turn = "aspetta l'utente" if (x.get("waits") or {}).get("on") == "person" else f"tocca a {(x.get('waits') or {}).get('on') or 'master'}"
        idle = int(x.get("idle_s") or 0)
        out.append(f"<div class=\"box{' att' if x.get('stalled') else ''}\"><b>{e(x['task'])} · {e(x.get('session') or 'master')} · {e(x['state'])}</b>"
                   + (f"approvato {hm(x['approved_at'])} · " if x.get("approved_at") else "") + f"fermo da {idle // 3600} h {(idle % 3600) // 60:02d} · prossimo: {e(x.get('next') or '')} · {e(turn)}"
                   + (f"<div class=\"ev blocked\">PAUSA: {e(x['paused']['reason'])}</div>" if x.get("paused") else "")
                   + (f"<div class=\"ev\">esito: {e(x['expected'])}</div>" if x.get("expected") else "") + "</div>")
    out.append("<h2>Cronologia</h2><div class=\"box\">")
    ticks, t0 = [], (w["start"] // 3600 + 1) * 3600
    for t in range(t0, w["end"], 3600 if span <= 8 * 3600 else 7200):
        ticks.append(f"<span style=\"left:{(t - w['start']) * 100 / span:.2f}%\">{hm(t)}</span>")
    out.append(f"<div class=\"axis\">{''.join(ticks)}</div>")
    if not rep["timeline"]:
        out.append("<div class=\"empty\">Nessun lavoro nella finestra.</div>")
    for it in rep["timeline"]:
        s = max(it["start"], w["start"])
        end = it["end"] or w["end"]
        kind = "notte" if it["kind"] == "night_job" else "sessione" + (f" su {it['host']}" if it.get("host") not in (None, "local") else "")
        segs = [(max(a, w["start"]), b) for a, b in it.get("spans") or []] or [(s, end)]
        bars = "".join(f"<div class=\"bar\" style=\"left:{(a - w['start']) * 100 / span:.2f}%;width:{min(max(0.6, (b - a) * 100 / span), 100 - (a - w['start']) * 100 / span):.2f}%;"
                       f"background:{COLOR.get(it['outcome'], '#9aa3ad')}\"></div>" for a, b in segs)
        out.append(f"<div class=\"lab\"><span><b>{e(it['title'])}</b> · {kind}</span><span>{hm(it['start'])}–{hm(it['end'])} · {e(it['outcome'])}</span></div>"
                   f"<div class=\"lane\" title=\"{e(it['detail'] or '')}\">{bars}</div>"
                   + (f"<div class=\"ev\">{e(it['detail'])}</div>" if it["detail"] else ""))
    if rep["queue"]:
        out.append(f"<div class=\"ev\">In coda: {len(rep['queue'])} — " + e(" · ".join(os.path.basename(q["project"]) for q in rep["queue"])) + "</div>")
    out.append("</div><h2>Progetti</h2><div class=\"cards\">")
    for c in rep["projects"]:
        parts = "".join(f"<li class=\"{p['state']}\">{PART_MARK[p['state']]} {e(p['title'])}</li>" for p in c["parts"])
        more = f"<li class=\"todo\">… altre {c['parts_total'] - len(c['parts'])}</li>" if c["parts_total"] > len(c["parts"]) else ""
        src = {"TASKS.md": "da TASKS.md", "plan": "dal piano", "registry": "dal registro"}.get(c["parts_source"], "solo eventi")
        evs = "".join(f"<div class=\"ev\">{hm(x['at'])} {e(x['kind'])} {'✓' if x['ok'] else '✗' if x['ok'] is False else ''} {e(x['text'])}</div>" for x in c["events"][-8:])
        wh = c.get("where") or {}
        goals = "".join(f"<li class=\"{GOAL_CLASS.get(g['state'], 'todo')}\">{PART_MARK.get(GOAL_CLASS.get(g['state']), '○')} {e(g['title'])}"
                        + (f" · {hm(g['done_at'])}" if g.get("done_at") else "") + "</li>" for g in c.get("goals") or [])
        out.append(f"<div class=\"box card\"><h3>{e(c['name'])}</h3><div class=\"sub\">{src}</div>"
                   + (f"<div class=\"ev {GOAL_CLASS.get(wh.get('state'), 'todo')}\"><b>{e(WHERE_LABEL.get(wh.get('state'), ''))}</b>"
                      + (f" — {e(wh['text'])}" if wh.get("text") else "") + "</div>" if wh else "")
                   + "".join(f"<div class=\"ev\">manca: {e(x)}</div>" for x in wh.get("missing") or [])
                   + "".join(f"<div class=\"ev blocked\">da te: {e(x)}</div>" for x in wh.get("needs_you") or [])
                   + (f"<ul class=\"parts\">{goals}</ul>" if goals else "")
                   + "".join(f"<div class=\"ev blocked\">aspetta {e(x)}</div>" for x in c["waiting_on"])
                   + (f"<div class=\"ev\">→ {e(c['next'])}</div>" if c["next"] else "")
                   + (f"<ul class=\"parts\">{parts}{more}</ul>" if parts else "") + (evs or "<div class=\"empty\">nessun evento</div>") + "</div>")
    out.append("</div><p class=\"sub\">Fonti: " + e(", ".join(f"{k} {v}" for k, v in rep["sources"].items()))
               + f" · generato alle {hm(rep['generated_at'])}</p></main></body></html>")
    return "\n".join(out)


# ------------------------------------------------------------------ CLI
def parse_now(s):
    if re.fullmatch(r"\d+", s):
        return int(s)
    try:
        return int(dt.datetime.fromisoformat(s).timestamp())
    except ValueError:
        sys.exit("night report: --now wants an epoch or an ISO time")


def write(p, text):
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


def main(argv):
    args = list(argv)
    if args and args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    now, out_dir = None, Path(cm.expand("~/.team-supervisor/night-report"))
    if "--now" in args:
        i = args.index("--now"); now = parse_now(args[i + 1]); del args[i:i + 2]
    if "--out-dir" in args:
        i = args.index("--out-dir"); out_dir = Path(cm.expand(args[i + 1])); del args[i:i + 2]
    rep = build(now, remote="--no-remote" not in args)
    text = json.dumps(rep, ensure_ascii=False, indent=1)
    if "--print" in args:
        print(text)
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    write(out_dir / f"{rep['date']}.json", text + "\n")
    print(out_dir / f"{rep['date']}.json")
    if "--json-only" not in args:
        write(out_dir / f"{rep['date']}.html", render_html(rep))
        print(out_dir / f"{rep['date']}.html")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
