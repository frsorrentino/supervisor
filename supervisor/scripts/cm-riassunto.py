#!/usr/bin/env python3
"""supervisor riassunto — il riassunto a fine sessione (19/11/2026, supervisor-riassunto-fine-19-11; idea 1 da claude-mem).

  supervisor riassunto scan            il giro: le sessioni finite senza passaggio di consegne, una per una
  supervisor riassunto session SID     una sessione sola (il SessionEnd la lancia staccata, con nice)

Una sessione con lavoro vero (almeno riassunto.min_prompts prompt nel registro) che finisce senza passaggio di consegne
scritto dopo il suo inizio lascia nella sua cartella docs/riassunto-<data>-<sid8>.md: fatti, decisioni, comandi riusciti,
cose aperte. Lo scrive `claude -p` con un modello medio (riassunto.model) e l'account della cartella (folder_map), da un
digest corto: i prompt della persona dalla trascrizione, gli esiti del registro, i commit della cartella fatti durante la
sessione. Mai il testo <private> (private_strip sul digest e sul file). Il puntatore del passaggio di consegne della
cartella (by «riassunto») fa si' che il SessionStart dopo lo annunci; un passaggio di consegne vero non viene mai coperto.

Finita vuol dire: una riga «end» nel registro (anche /clear), oppure nessuna riga da piu' di riassunto.idle_min minuti e
nessun processo con quel session_id (una finestra chiusa di colpo non manda il SessionEnd). Solo le sessioni finite dopo
la prima accensione (meno riassunto.window_h ore): mai un arretrato di mesi al primo giro. Un tetto al giorno
(riassunto.max_per_day), il costo nel registro (riga «riassunto», cost_usd dall'uscita json di claude -p).
Il giro parte dal relay (al piu' ogni riassunto.scan_every_s) e dal SessionEnd. Prove: CM_CLAUDE_BIN, CM_BIN.
"""
import datetime
import fcntl
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
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_rs", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
STATE = Path(cm.expand(CFG["state_dir"]))
R = CFG.get("riassunto") or {}
LEAN = ["--strict-mcp-config", "--tools", "", "--disable-slash-commands", "--no-session-persistence"]
PROMPT_CHARS = 400
MAX_PROMPTS = 25
MAX_COMMITS = 20
LEDGER_TAIL = 8_000_000


def opt(key, default):
    v = R.get(key, default)
    try:
        return type(default)(v)
    except (TypeError, ValueError):
        return default


def ts_of(row):
    try:
        return datetime.datetime.fromisoformat(row.get("ts") or "").timestamp()
    except (TypeError, ValueError):
        return None


def ledger_rows():
    try:
        with open(STATE / "ledger.jsonl", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - LEDGER_TAIL))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and ts_of(r) is not None:
            out.append(r)
    return out


def ledger(**row):
    row = dict({"ts": datetime.datetime.now().isoformat(timespec="seconds"), "event": "riassunto"}, **row)
    try:
        with open(STATE / "ledger.jsonl", "a") as f:
            f.write(json.dumps(cm.private_deep(row), ensure_ascii=False) + "\n")
    except OSError:
        pass


def since_stamp():
    """La prima accensione meno window_h: le sessioni finite prima non si riassumono mai (niente arretrato)."""
    p = STATE / "riassunti" / "since"
    try:
        return float(p.read_text().strip())
    except (OSError, ValueError):
        v = time.time() - opt("window_h", 2.0) * 3600
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(v))
        return v


def sessions_from(rows):
    """{sid: {cwd, account, start, last, end, prompts, transcript, esiti, done}} dal registro."""
    S = {}
    for r in rows:
        sid = r.get("session_id") or ""
        if not sid:
            continue
        s = S.setdefault(sid, {"sid": sid, "cwd": "", "account": "", "start": None, "last": 0, "end": None, "prompts": 0,
                               "transcript": "", "esiti": [], "done": False})
        t = ts_of(r)
        ev = r.get("event")
        if ev == "riassunto":
            s["done"] = True
            continue
        s["cwd"] = s["cwd"] or r.get("cwd") or ""
        s["account"] = s["account"] or r.get("account") or ""
        s["start"] = t if s["start"] is None else min(s["start"], t)
        s["last"] = max(s["last"], t)
        if ev == "prompt":
            s["prompts"] += 1
        elif ev == "end":
            s["end"] = t
            s["transcript"] = r.get("transcript") or s["transcript"]
        elif ev == "stop" and r.get("esito"):
            s["esiti"].append(str(r["esito"])[:200])
    return S


def done_today(rows):
    today = datetime.date.today().isoformat()
    return sum(1 for r in rows if r.get("event") == "riassunto" and r.get("path") and str(r.get("ts", "")).startswith(today))


def live_sids():
    """I session_id delle sessioni vive (None se non si sa: allora una sessione senza «end» non si tocca)."""
    try:
        p = subprocess.run([os.environ.get("CM_BIN") or str(HERE / "supervisor"), "sessions", "--json", "--no-screen"],
                           capture_output=True, text=True, timeout=60)
        rows = json.loads(p.stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return {r.get("session_id") for r in rows if isinstance(r, dict) and r.get("session_id")}


def handoff_after(cwd, start):
    """Un passaggio di consegne vero scritto dopo l'inizio: il puntatore della cartella (non il nostro) o docs/handoff-*.md."""
    try:
        H = _load("cm-handoff")
        d = json.loads(H.pointer(cwd).read_text())
        if d.get("by") != "riassunto" and int(d.get("at") or 0) >= start:
            return True
    except Exception:   # noqa: BLE001
        pass
    docs = Path(cwd) / "docs"
    try:
        return any(p.stat().st_mtime >= start for p in docs.glob("handoff-*.md"))
    except OSError:
        return False


def out_path(s):
    day = datetime.date.fromtimestamp(s["end"] or s["last"]).isoformat()
    return Path(s["cwd"]) / "docs" / f"riassunto-{day}-{s['sid'][:8]}.md"


def eligible(s, live=None, now=None):
    """(si', perche' no)."""
    now = now or time.time()
    if s["done"] or out_path(s).exists():
        return False, "done"
    if not s["cwd"] or not Path(s["cwd"]).is_dir():
        return False, "cwd"
    try:
        if Path(s["cwd"]).resolve() == STATE.resolve() or STATE.resolve() in Path(s["cwd"]).resolve().parents:
            return False, "state"
    except OSError:
        pass
    if s["prompts"] < opt("min_prompts", 4):
        return False, "short"
    finished = s["end"] or s["last"]
    if finished < since_stamp():
        return False, "old"
    if not s["end"]:
        if now - s["last"] < opt("idle_min", 60) * 60:
            return False, "open"
        if live is None or s["sid"] in live:
            return False, "live"
    if handoff_after(s["cwd"], s["start"] or 0):
        return False, "handoff"
    return True, ""


def find_transcript(s):
    if s["transcript"] and Path(s["transcript"]).is_file():
        return Path(s["transcript"])
    dirs = [cm.expand(a.get("config_dir") or "") for a in (CFG.get("accounts") or {}).values()]
    for d in dirs + [str(cm.home() / ".claude")]:
        for p in Path(d).glob(f"projects/*/{s['sid']}.jsonl"):
            return p
    return None


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return ""
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def prompts_of(path):
    out = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("type") != "user" or r.get("isMeta") or r.get("isSidechain"):
            continue
        t = _text_of((r.get("message") or {}).get("content"))
        t = re.sub(r"<system-reminder>.*?</system-reminder>", "", t, flags=re.S).strip()
        if not t or t.startswith(("<command-", "<local-command", "Caveat:")):
            continue
        t = cm.private_strip(t)
        out.append(" ".join(t.split())[:PROMPT_CHARS])
    return out[-MAX_PROMPTS:]


def commits_of(cwd, start, end):
    fmt = lambda t: datetime.datetime.fromtimestamp(t).isoformat(timespec="seconds")  # noqa: E731
    try:
        p = subprocess.run(["git", "-C", cwd, "log", f"--since={fmt(start - 60)}", f"--until={fmt(end + 60)}", "--format=%h %s",
                            f"-n{MAX_COMMITS}"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [cm.private_strip(l) for l in p.stdout.splitlines() if l.strip()] if p.returncode == 0 else []


def digest(s):
    tr = find_transcript(s)
    pr = prompts_of(tr) if tr else []
    end = s["end"] or s["last"]
    parts = [M("riassunto.digest_head", cwd=s["cwd"], start=time.strftime("%d/%m %H:%M", time.localtime(s["start"] or end)),
               end=time.strftime("%H:%M", time.localtime(end)))]
    parts.append(M("riassunto.digest_prompts") + "\n" + "\n".join(f"- {p}" for p in pr) if pr else "")
    es = [cm.private_strip(e) for e in s["esiti"][-10:]]
    parts.append(M("riassunto.digest_esiti") + "\n" + "\n".join(f"- {e}" for e in es) if es else "")
    cs = commits_of(s["cwd"], s["start"] or end, end)
    parts.append(M("riassunto.digest_commits") + "\n" + "\n".join(f"- {c}" for c in cs) if cs else "")
    return cm.private_strip("\n\n".join(p for p in parts if p)), len(pr)


def ask(s, text):
    """(testo, costo in dollari o None, secondi). claude -p leggero con l'account della cartella, fuori dal progetto."""
    acc, _ = cm.account_for(CFG, s["cwd"])
    acc = acc or s["account"] or CFG.get("default_account")
    a = (CFG.get("accounts") or {}).get(acc) or {}
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CONFIG_DIR", "TMUX", "TMUX_PANE")}
    if a.get("config_dir"):
        env["CLAUDE_CONFIG_DIR"] = cm.expand(a["config_dir"])
    run_dir = STATE / "riassunti"
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt = M("riassunto.prompt") + "\n\n" + text
    base = [cm.claude_bin(), "-p", prompt, "--model", str(R.get("model") or "sonnet"), "--max-turns", "1", "--output-format", "json"]
    t0 = time.time()
    for extra in (LEAN, []):
        try:
            p = subprocess.run(base + extra, capture_output=True, text=True, env=env, cwd=str(run_dir), stdin=subprocess.DEVNULL,
                               timeout=opt("timeout_s", 240))
        except (OSError, subprocess.TimeoutExpired):
            return None, None, round(time.time() - t0, 1)
        out = (p.stdout or "").strip()
        try:
            j = json.loads(out)
        except ValueError:
            j = None
        if isinstance(j, dict) and j.get("result") and not j.get("is_error"):
            cost = j.get("total_cost_usd")
            return str(j["result"]), (float(cost) if isinstance(cost, (int, float)) else None), round(time.time() - t0, 1)
        if p.returncode == 0 and out and j is None:
            return out, None, round(time.time() - t0, 1)
    return None, None, round(time.time() - t0, 1)


def point(cwd, path):
    """Il puntatore della cartella: il SessionStart dopo lo annuncia (cm-handoff.announce, by «riassunto»)."""
    try:
        H = _load("cm-handoff")
        ptr = H.pointer(cwd)
        ptr.parent.mkdir(parents=True, exist_ok=True)
        ptr.write_text(json.dumps({"path": str(path), "at": int(time.time()), "cwd": os.path.realpath(cwd), "by": "riassunto"}))
    except Exception:   # noqa: BLE001 — il file c'e' comunque
        pass


def write_one(s):
    text, n = digest(s)
    if not n:
        return None, "no prompts"
    body, cost, secs = ask(s, text)
    if not body:
        ledger(session_id=s["sid"], cwd=s["cwd"], path=None, error="claude -p", secs=secs)
        return None, "claude -p"
    path = out_path(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    end = s["end"] or s["last"]
    head = M("riassunto.file_head", name=Path(s["cwd"]).name, day=time.strftime("%d/%m/%Y", time.localtime(end)),
             start=time.strftime("%H:%M", time.localtime(s["start"] or end)), end=time.strftime("%H:%M", time.localtime(end)),
             sid=s["sid"], model=str(R.get("model") or "sonnet"))
    path.write_text(cm.private_strip(head + "\n\n" + body.strip()) + "\n", encoding="utf-8")
    point(s["cwd"], path)
    ledger(session_id=s["sid"], cwd=s["cwd"], path=str(path), cost_usd=cost, model=str(R.get("model") or "sonnet"), secs=secs)
    return path, ""


class Lock:
    def __enter__(self):
        (STATE / "riassunti").mkdir(parents=True, exist_ok=True)
        self.f = open(STATE / "riassunti" / ".lock", "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        self.f.close()


def run(only=None):
    if not R.get("enabled", True):
        print(M("riassunto.off"))
        return 0
    with Lock():
        rows = ledger_rows()
        S = sessions_from(rows)
        todo = [S[only]] if only else sorted(S.values(), key=lambda s: s["end"] or s["last"])
        if only and only not in S:
            print(M("riassunto.none"))
            return 0
        need_live = any(not s["end"] for s in todo)
        live = live_sids() if need_live else set()
        made, left = 0, opt("max_per_day", 6) - done_today(rows)
        for s in todo:
            ok, why = eligible(s, live)
            if not ok:
                continue
            if left <= 0:
                print(M("riassunto.cap", n=opt("max_per_day", 6)))   # domani si riprova: nessuna riga che la chiuda
                break
            path, err = write_one(s)
            if path:
                made += 1
                left -= 1
                print(M("riassunto.done", path=path))
            elif err:
                print(f"{s['sid'][:8]}: {err}", file=sys.stderr)
        if not made:
            print(M("riassunto.none"))
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    if argv[0] == "scan":
        return run()
    if argv[0] == "session" and len(argv) > 1:
        return run(argv[1])
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
