#!/usr/bin/env python3
"""supervisor timeline — la cronologia di cosa hanno fatto le sessioni (fase 2, 03/10/2026).

  supervisor timeline [NOME] [--since 6h|90m|2d] [--json] [--limit N]

Per ogni sessione (le vive, e le chiuse con la trascrizione toccata nella finestra), in ordine di tempo:
- prompt: quello che la persona ha chiesto (dalla trascrizione, come `transcript` del relay);
- test: un comando di test lanciato dalla sessione (suite *-verify.py, pytest, npm/yarn/pnpm test, go/cargo test,
  make test, release.sh --check), verde o rosso dall'esito dello strumento;
- commit: i commit della cartella nella finestra (`git log`, non la trascrizione: con `-q` l'hash non si vede);
- esito: la riga «Esito:» (o «Outcome:») con cui la sessione chiude il turno;
- task: gli esiti del registro dei compiti per quella cartella (fatto o rosso, dal controllo rieseguito);
- compact: una compattazione a sessione ferma (Claude Code 2.1.299, 16/10/2026): un compact_boundary «auto» preceduto
  da 50 minuti o piu' senza turni, con i token di prima e di dopo.
Tutto dal codice, nessun modello. `task board --timeline` e il riepilogo del telefono la usano.
"""
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
core = _load("cm-core")
CFG = cm.load(warn=False)
R = CFG.get("relay") or {}
TEST_RE = re.compile(r"(?:^|[\s;&|(/])(?:python3?\s+(?:-\S+\s+)*\S*-verify\.py|python3?\s+(?:-\S+\s+)*\"\$t\"|pytest\b|python3?\s+-m\s+pytest\b|(?:npm|yarn|pnpm)\s+(?:run\s+)?test\b"
                     r"|go\s+test\b|cargo\s+test\b|make\s+(?:test|check)\b|\S*release\.sh\s+\S+\s+--check\b|for\s+t\s+in\s+tests/)")
OUTCOME_RE = re.compile(r"^\s*(?:Esito|Outcome)\s*:\s*(.+)$", re.M)
TEXT_MAX = 160
FULL_PROMPTS = False   # cm-night-report sets it on its own copy: prompt events then carry the uncut text in "full"
WINDOWS = (4 * 1024 * 1024, 32 * 1024 * 1024, None)


def parse_since(s):
    m = re.fullmatch(r"\s*(\d+)\s*([mhd])\s*", str(s or "6h"))
    if not m:
        sys.exit("timeline: --since wants 90m, 6h or 2d")
    return int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]


def one_line(t, n=TEXT_MAX):
    t = " ".join(str(t or "").split())
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


SUMMARY_RE = re.compile(r"\b\d+/\d+ OK\b|\b\d+ passed\b|\b\d+ failed\b|^(?:ok|FAIL)\s|test result:|Tests:\s")
SUITE_RE = re.compile(r"[\w./-]*-verify\.py|pytest(?:\s+-\S+)*(?:\s+[\w./:]+)?|(?:npm|yarn|pnpm)\s+(?:run\s+)?test|go\s+test(?:\s+\S+)?|cargo\s+test"
                      r"|make\s+(?:test|check)|\S*release\.sh\s+\S+\s+--check|for\s+t\s+in\s+tests/\S*")


def bare(cmd):
    """Il comando senza heredoc e senza testo fra virgolette (come la guardia della corsia chiusa): un «pytest» dentro
    uno script o un messaggio non e' un test lanciato."""
    b = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?.*?^\1$", "", cmd, flags=re.S | re.M)
    return re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", b)


def test_name(cmd):
    """Le suite di un comando composto («tests/plan-verify.py, tests/tasks-verify.py»), non tutto il comando."""
    names = []
    for m in SUITE_RE.findall(bare(cmd)):
        n = m.rsplit("/", 1)[-1] if m.endswith("-verify.py") else " ".join(w for w in m.split() if not w.startswith("-"))
        if n not in names:
            names.append(n)
    return one_line(", ".join(names) or cmd)


def _result_text(b):
    c = b.get("content")
    return c if isinstance(c, str) else " ".join(str(x.get("text") or "") for x in (c or []) if isinstance(x, dict))


def human_prompt(out, at, content):
    """Un prompt della persona; con il prefisso del relay ref = il dispositivo («phone», «watch»). Lo stesso testo
    gia' visto (in coda e poi nel turno) una volta sola."""
    t, _ = core._clean(content)
    t, origin = core._origin_of(t)
    if t and not any(e["kind"] == "prompt" and e["text"] == one_line(t) for e in out[-20:]):
        out.append({"at": at, "kind": "prompt", "text": one_line(t), "ok": None, "ref": None if origin == "pc" else origin,
                    **({"full": t} if FULL_PROMPTS else {})})


IDLE_COMPACT_S = 50 * 60


def idle_compaction(lines, i, d):
    """Il testo dell'evento se il compact_boundary `d` (riga i) e' una compattazione a sessione ferma, altrimenti None.
    La 2.1.299 compatta una conversazione oltre i 200k token ferma da circa 54 minuti e la scrive con trigger «auto»,
    come quella a contesto pieno: la distingue solo la pausa fra l'ultimo messaggio e l'inizio del riassunto."""
    meta = d.get("compactMetadata") or {}
    if meta.get("trigger") != "auto":
        return None
    start = core._turn_epoch(d.get("timestamp")) - int(meta.get("durationMs") or 0) / 1000
    for raw in reversed(lines[max(0, i - 400):i]):
        if (b'"type":"assistant"' not in raw and b'"type":"user"' not in raw) or core.noise(raw):
            continue
        try:
            p = json.loads(raw)
        except ValueError:
            continue
        at = core._turn_epoch(p.get("timestamp")) if p.get("type") in ("user", "assistant") and not p.get("isSidechain") else 0
        if not at:
            continue
        if start - at < IDLE_COMPACT_S:
            return None
        k = lambda n: f"{round(int(n) / 1000)}k"   # noqa: E731
        sizes = f": {k(meta['preTokens'])} → {k(meta['postTokens'])} tokens" if meta.get("preTokens") and meta.get("postTokens") else ""
        return f"compacted after {int((start - at) // 60)} min idle{sizes}"
    return None


def transcript_events(path, since):
    """Prompt, test ed esiti di una trascrizione da `since` (epoch s). Si legge dalla coda e si allarga la finestra
    solo se la prima riga letta e' ancora dentro l'intervallo."""
    size = os.path.getsize(path)
    for window in WINDOWS:
        start = 0 if window is None or window >= size else size - window
        with open(path, "rb") as f:
            f.seek(start)
            lines = f.read().split(b"\n")
        if start:
            lines = lines[1:]
        first = None
        for raw in lines[:50]:
            if core.noise(raw):
                continue
            try:
                first = core._turn_epoch(json.loads(raw).get("timestamp"))
                if first:
                    break
            except ValueError:
                continue
        if not start or (first and first < since):
            break
    out, tests, peers = [], {}, set()
    for i, raw in enumerate(lines):
        if not raw.strip() or core.noise(raw) or not (b'"Bash"' in raw or b"tool_result" in raw or b"Esito" in raw or b"Outcome" in raw or b'"type":"user"' in raw
                                   or b'"peer"' in raw or b"queued_command" in raw or b'"compact_boundary"' in raw):
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        at = int(core._turn_epoch(d.get("timestamp")) or 0)
        if at < since or d.get("isSidechain"):
            continue
        if d.get("type") == "system" and d.get("subtype") == "compact_boundary":
            text = idle_compaction(lines, i, d)
            if text:
                out.append({"at": at, "kind": "compact", "text": text, "ok": None, "ref": None})
            continue
        content = (d.get("message") or {}).get("content")
        if d.get("type") == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and b.get("name") == "Bash":
                    cmd = str((b.get("input") or {}).get("command") or "")
                    if TEST_RE.search(bare(cmd)):
                        e = {"at": at, "kind": "test", "text": test_name(cmd), "ok": None, "ref": None}
                        out.append(e)
                        tests[b.get("id")] = e
                elif b.get("type") == "text":
                    for m in OUTCOME_RE.finditer(str(b.get("text") or "")):
                        out.append({"at": at, "kind": "outcome", "text": one_line(m.group(1)), "ok": None, "ref": None})
        o = d.get("origin") or ((d.get("attachment") or {}).get("origin") if d.get("type") == "attachment" else None)
        if isinstance(o, dict) and o.get("kind") == "peer" and o.get("msg_id") not in peers:
            # un prompt del relay arriva come messaggio peer con il prefisso del dispositivo: e' della persona
            t, origin = core._origin_of(str(o.get("body") or "").strip())
            if origin != "pc" and t:
                peers.add(o.get("msg_id"))
                out.append({"at": at, "kind": "prompt", "text": one_line(t), "ok": None, "ref": origin,
                            **({"full": t} if FULL_PROMPTS else {})})
            continue
        if d.get("type") == "user":
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in tests:
                        e = tests[b["tool_use_id"]]
                        e["ok"] = not b.get("is_error")
                        tail = [ln for ln in _result_text(b).splitlines() if SUMMARY_RE.search(ln)]
                        e["ref"] = one_line(tail[-1], 120) if tail else None
                        e["ok"] = e["ok"] and not any("FAIL" in ln.split(" OK", 1)[-1] or re.search(r"\bfailed\b", ln) for ln in tail)
            # il riassunto di una compattazione («This session is being continued…») e' una riga `user` senza origin,
            # ma non l'ha scritta la persona (07/10: spostava la finestra della pagina «Notte» alle 20:32)
            if core._human(d) and not d.get("isCompactSummary"):
                human_prompt(out, at, content)
        elif d.get("type") == "attachment":
            a = d.get("attachment") or {}
            if a.get("type") == "queued_command" and isinstance(a.get("origin"), dict) and a["origin"].get("kind") == "human":
                human_prompt(out, at, a.get("prompt"))   # scritto mentre il turno girava
    return out


def commits(project, since):
    try:
        p = subprocess.run(["git", "-C", project, "log", "--all", f"--since=@{since}", "--format=%h%x09%ct%x09%s"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out, seen = [], set()
    for line in p.stdout.splitlines() if p.returncode == 0 else []:
        h, ct, subj = (line.split("\t", 2) + ["", ""])[:3]
        if ct.isdigit() and not any(subj == s_ and abs(int(ct) - t_) < 120 for s_, t_ in seen):
            seen.add((subj, int(ct)))   # lo stesso commit su un altro ramo (il pubblico della release): una volta
            out.append({"at": int(ct), "kind": "commit", "text": one_line(subj), "ok": None, "ref": h})
    return out


def task_events(project, since):
    """Gli esiti accettati del registro (se c'e') per i compiti di quella cartella."""
    try:
        tk = _load("cm-tasks")
        con = tk.connect(create=False)
    except Exception:   # noqa: BLE001 — il registro e' facoltativo
        return []
    if con is None:
        return []
    rows = con.execute("SELECT r.task, r.outcome, r.reason, r.at, t.title, t.project FROM results r JOIN tasks t ON t.id = r.task "
                       "WHERE r.accepted = 1 AND r.at >= ?", (since,)).fetchall()
    real = os.path.realpath(project)
    return [{"at": r["at"], "kind": "task", "text": one_line(f"{r['title']}: {r['reason']}"), "ok": r["outcome"] == "green", "ref": r["task"]}
            for r in rows if os.path.realpath(r["project"]) == real]


_KPI = None


def wait_events(rdir, session, since):
    """1.96 (29/10): le attese della sessione verso la persona — domanda, permesso, needs-you — da <relay>/waits.jsonl,
    che il relay scrive a ogni push da oggi in poi (cm-kpi.record_waits): un evento wait all'inizio e uno alla fine."""
    global _KPI
    out = []
    _KPI = _KPI or _load("cm-kpi")
    for r in _KPI.wait_rows(_KPI._jsonl(Path(rdir) / "waits.jsonl")):   # la stessa attesa una volta sola (29/10)
        if r.get("session") != session or int(r.get("at") or 0) < since:
            continue
        kind = cm.msg(CFG, f"timeline.wait_{r.get('kind')}") if r.get("kind") in ("question", "permission", "needs_you") else str(r.get("kind"))
        key = "timeline.wait_start" if r.get("event") == "start" else "timeline.wait_end"
        mins = max(0, int(r["at"]) - int(r.get("since") or r["at"])) // 60
        out.append({"at": int(r["at"]), "kind": "wait", "text": one_line(cm.msg(CFG, key, kind=kind, min=mins)), "ok": None})
    return out


def sessions_in(since, name=None, live=None):
    """[(nome, viva, cartella, trascrizione)]: le vive con la loro trascrizione, poi le chiuse toccate da `since`.
    `live` = le righe di `sessions --json` gia' lette da chi chiama (il relay); senza, si chiedono alla CLI."""
    try:
        if live is not None:
            raise LookupError
        p = subprocess.run([os.environ.get("CM_BIN") or str(HERE / "supervisor"), "sessions", "--json", "--no-screen"],
                           capture_output=True, text=True, timeout=60)
        live = json.loads(p.stdout) if p.returncode == 0 else []
    except LookupError:
        pass
    except (OSError, ValueError, subprocess.TimeoutExpired):
        live = []
    prefixes = [a.get("tmux_prefix") or "" for a in (CFG.get("accounts") or {}).values()]
    S = _load("cm-relay-state")
    out, seen = [], set()
    for r in live:
        path = core.transcript_of(r)
        if not path:
            continue
        nm = S.short_name(r.get("name") or r.get("tmux") or "", prefixes)
        out.append((nm, True, r.get("cwd") or "", os.path.realpath(path)))
        seen.add(os.path.realpath(path))
    for acc in (CFG.get("accounts") or {}).values():
        for f in (Path(cm.expand(acc.get("config_dir") or "~/.claude")) / "projects").glob("*/*.jsonl"):
            try:
                if f.stat().st_mtime < since or os.path.realpath(str(f)) in seen:
                    continue
                with open(f, "rb") as h:
                    m = re.search(rb'"cwd":\s*("(?:[^"\\]|\\.)*")', h.read(65536))
            except OSError:
                continue
            cwd = json.loads(m.group(1)) if m else ""
            out.append((os.path.basename(cwd.rstrip("/")) or f.parent.name, False, cwd, os.path.realpath(str(f))))
    return [s for s in out if not name or s[0] == name]


def timeline(since_s, name=None, limit=40, now=None, live=None):
    now = now or time.time()
    since = int(now - since_s)
    rows, by_project = [], {}
    for nm, is_live, cwd, path in sessions_in(since, name, live):
        ev = transcript_events(path, since) + wait_events(Path(cm.expand(R.get("dir") or "~/.cc-supervisor/relay")), nm, since)
        if not ev and not is_live:
            continue
        rows.append({"session": nm, "live": is_live, "project": cwd, "events": ev})
        by_project.setdefault(os.path.realpath(cwd) if cwd else "", []).append(rows[-1])
    for proj, group in by_project.items():
        if not proj or not os.path.isdir(proj):
            continue
        extra = commits(proj, since) + task_events(proj, since)
        # commit e compiti sono della cartella: vanno alla sessione che lavorava li' in quel momento (la piu' vicina)
        for e in extra:
            best = min(group, key=lambda r: min((abs(x["at"] - e["at"]) for x in r["events"]), default=10 ** 9) - (10 ** 8 if r["live"] else 0))
            best["events"].append(e)
    for r in rows:
        r["events"] = [e for e in r["events"] if e["at"] <= now]   # niente dal futuro dell'istante chiesto
        r["events"].sort(key=lambda e: e["at"])
        r["events"] = r["events"][-limit:]
    rows.sort(key=lambda r: -(r["events"][-1]["at"] if r["events"] else 0))
    return {"since": since, "sessions": [r for r in rows if r["events"]]}


MARK = {True: "✓", False: "✗", None: " "}


def render(tl):
    out = []
    for r in tl["sessions"]:
        out.append(f"{r['session']}{'' if r['live'] else ' (closed)'} — {cm.contract(r['project'])}")
        for e in r["events"]:
            ref = f" [{e['ref']}]" if e["kind"] in ("commit", "task") and e["ref"] else f" — {e['ref']}" if e["kind"] == "test" and e["ref"] else ""
            out.append(f"  {time.strftime('%H:%M', time.localtime(e['at']))} {e['kind']:<7} {MARK[e['ok']]} {e['text']}{ref}")
        out.append("")
    return "\n".join(out).rstrip() or "nothing in the window"


def main(argv):
    args = list(argv)
    if args and args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    since, limit = "6h", 40
    if "--since" in args:
        i = args.index("--since"); since = args[i + 1]; del args[i:i + 2]
    if "--limit" in args:
        i = args.index("--limit"); limit = int(args[i + 1]); del args[i:i + 2]
    tl = timeline(parse_since(since), args[0] if args else None, limit)
    print(json.dumps(tl, ensure_ascii=False) if as_json else render(tl))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
