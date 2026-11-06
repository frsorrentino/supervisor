#!/usr/bin/env python3
"""supervisor bg — i lavori lunghi che sopravvivono alla sessione, e cio' che un riavvio ha interrotto (06/11/2026,
supervisor-riavvii-allineamenti-06-11).

  supervisor bg [--name ETICHETTA] -- COMANDO...   lancia COMANDO staccato (setsid), log e stato fuori dallo scratchpad
  supervisor bg list [--json]                      i lavori: in corso, finiti (rc), morti senza esito
  supervisor bg log ID [N]                         le ultime N righe del log (40)

Il 06/11 la release 0.8.135, lanciata in background da una sessione, e' morta con la sessione; il log stava nello
scratchpad (/tmp) ed e' sparito, e nessuno se n'e' accorto per 50 minuti. Un lavoro di `bg` vive in
<state_dir>/jobs/<id>/ (meta.json, out.log, rc.json a lavoro finito): sopravvive alla sessione (setsid, stdin chiuso),
il log resta dopo un riavvio del container, e a lavoro finito la sessione che l'ha lanciato riceve un `talk` con
l'esito (resta in casella se la sessione non c'e'). Un lavoro morto con la macchina (boot_id diverso, o pid sparito
senza rc.json) lo dice il SessionStart della sessione proprietaria, una volta.

Lo stesso SessionStart elenca i comandi in background (Bash con run_in_background) della conversazione che riprende,
o di quella di prima dopo un avvio nuovo, rimasti senza esito: Claude Code alla ripresa scrive solo una notifica
«didn't finish before the previous session ended» nella trascrizione, che non apre un turno (il 06/11 alle 10:45 la
sessione e' rimasta ferma al prompt). Ognuno si dice una volta (jobs/reported.json).
"""
import datetime
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
STATE = Path(cm.expand(CFG["state_dir"]))
JOBS = STATE / "jobs"
CM_BIN = os.environ.get("CM_BG_TALK_BIN") or str(HERE / "supervisor")
WINDOW_S = 12 * 3600      # i comandi lanciati nelle ultime 12 ore della conversazione: i piu' vecchi non si riprendono
REPORTED_KEEP_S = 14 * 86400
SHOW_MAX = 6


def boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def my_tmux_name():
    if os.environ.get("CM_HOOK_TMUX_NAME"):
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


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _read(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def _write(p, data):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, p)


# ------------------------------------------------------------------ bg
def job_state(d):
    """(stato, meta, rc): running | done | died."""
    meta = _read(d / "meta.json", {}) or {}
    rc = _read(d / "rc.json")
    if isinstance(rc, dict):
        return "done", meta, rc
    if meta.get("boot") and boot_id() and meta["boot"] != boot_id():
        return "died", meta, None
    if not alive(meta.get("pid")):
        return "died", meta, None
    return "running", meta, None


def all_jobs():
    if not JOBS.is_dir():
        return []
    out = []
    for d in sorted(JOBS.iterdir()):
        if d.is_dir() and (d / "meta.json").is_file():
            st, meta, rc = job_state(d)
            out.append(dict(meta, state=st, rc=(rc or {}).get("rc"), ended=(rc or {}).get("at"), log=str(d / "out.log")))
    return sorted(out, key=lambda j: j.get("started") or 0)


def start(label, argv, cwd=None):
    if not argv:
        print(M("bg.usage"), file=sys.stderr)
        return 2
    jid = time.strftime("%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    d = JOBS / jid
    d.mkdir(parents=True, exist_ok=True)
    cmd = argv[0] if len(argv) == 1 else shlex.join(argv)
    meta = {"id": jid, "label": label or cmd[:60], "cmd": cmd, "cwd": cwd or os.getcwd(), "owner": my_tmux_name(),
            "sid": os.environ.get("CLAUDE_CODE_SESSION_ID", ""), "started": time.time(), "boot": boot_id(), "pid": None}
    _write(d / "meta.json", meta)
    with open(d / "out.log", "ab") as log, open(os.devnull, "rb") as nul:
        p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_run", jid], stdin=nul, stdout=log, stderr=log,
                             cwd=meta["cwd"], start_new_session=True, env=os.environ.copy())
    meta["pid"] = p.pid
    _write(d / "meta.json", meta)
    print(M("bg.started", id=jid, label=meta["label"], log=d / "out.log"))
    return 0


def run_job(jid):
    """L'esecutore staccato: esegue il comando, scrive rc.json, avvisa la sessione proprietaria."""
    d = JOBS / jid
    meta = _read(d / "meta.json", {}) or {}
    t0 = time.time()
    try:
        rc = subprocess.run(["bash", "-c", meta["cmd"]], cwd=meta.get("cwd") or None, stdin=subprocess.DEVNULL).returncode
    except OSError as e:
        print(f"bg: {e}", flush=True)
        rc = 127
    _write(d / "rc.json", {"rc": rc, "at": time.time(), "secs": round(time.time() - t0)})
    owner = meta.get("owner") or ""
    if owner:
        text = M("bg.finished_talk", label=meta.get("label") or jid, rc=rc, mins=round((time.time() - t0) / 60), log=d / "out.log", tail=tail(d / "out.log", 3))
        try:
            subprocess.run([CM_BIN, "talk", owner, text, "--no-wait"], capture_output=True, text=True, timeout=60)
            _mark_reported([f"job:{jid}"])
        except (OSError, subprocess.SubprocessError):
            pass
    return 0


def tail(p, n):
    try:
        lines = Path(p).read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def hm(t):
    try:
        return datetime.datetime.fromtimestamp(float(t)).strftime("%d/%m %H:%M")
    except (TypeError, ValueError, OSError):
        return "?"


def cmd_list(as_json):
    jobs = all_jobs()
    if as_json:
        print(json.dumps(jobs, ensure_ascii=False))
        return 0
    if not jobs:
        print(M("bg.none"))
        return 0
    for j in jobs:
        st = {"running": M("bg.st_running"), "died": M("bg.st_died")}.get(j["state"]) or M("bg.st_done", rc=j.get("rc"))
        print(f"  {j['id']}  {hm(j.get('started'))}  {st:<14} {j.get('owner') or '-':<16} {j.get('label')}")
        print(f"      {j['log']}")
    return 0


# ------------------------------------------------------------------ interrotti
def _ts(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


NOTE_RE = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>.*?<status>([^<]+)</status>(?:.*?<summary>([^<]*)</summary>)?", re.S)


def interrupted(transcript):
    """I Bash con run_in_background della trascrizione rimasti senza esito: nessuna notifica di fine (completed,
    failed, killed), nessun TaskStop della sessione; la notifica «didn't finish before the previous session ended»
    che Claude Code scrive alla ripresa non e' un esito. Solo quelli lanciati nelle ultime WINDOW_S della conversazione.
    [{tool_use_id, task_id, description, command, at}]"""
    launched, task_of, ended, stopped_tasks, last = {}, {}, set(), set(), 0.0
    try:
        f = open(transcript, errors="replace")
    except OSError:
        return []
    with f:
        for raw in f:
            # solo le righe che possono contare (una trascrizione di 17 MB: 9 s a leggerle tutte, il SessionStart ne ha 10)
            if not ('"run_in_background"' in raw or "TaskStop" in raw or "backgroundTaskId" in raw or "<task-notification>" in raw):
                if '"timestamp":"' in raw:
                    i = raw.find('"timestamp":"') + 13
                    last = max(last, _ts(raw[i:raw.find('"', i)]) or 0)
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            at = _ts(d.get("timestamp")) or 0
            last = max(last, at)
            if d.get("isSidechain"):
                continue
            msg = d.get("message") or {}
            content = msg.get("content")
            if d.get("type") == "assistant" and isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict) or c.get("type") != "tool_use":
                        continue
                    inp = c.get("input") or {}
                    if c.get("name") == "Bash" and inp.get("run_in_background"):
                        launched[c.get("id")] = {"tool_use_id": c.get("id"), "description": str(inp.get("description") or "")[:120],
                                                 "command": str(inp.get("command") or "")[:300], "at": at}
                    elif c.get("name") == "TaskStop":
                        stopped_tasks.add(str(inp.get("task_id") or inp.get("shell_id") or ""))
            elif d.get("type") == "user":
                tur = d.get("toolUseResult")
                if isinstance(tur, dict) and tur.get("backgroundTaskId") and isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_result":
                            task_of[c.get("tool_use_id")] = tur["backgroundTaskId"]
                texts = [content] if isinstance(content, str) else [str(c.get("text") or "") for c in content or [] if isinstance(c, dict)]
                for tx in texts:
                    if "<task-notification>" not in tx:
                        continue
                    for m in NOTE_RE.finditer(tx):
                        status, summary = m.group(2).strip(), (m.group(3) or "")
                        if status == "stopped" and "didn't finish before the previous session ended" in summary:
                            continue
                        ended.add(m.group(1).strip())
    out = []
    for tid, x in launched.items():
        if tid in ended or task_of.get(tid) in stopped_tasks or (last and x["at"] and last - x["at"] > WINDOW_S):
            continue
        out.append(dict(x, task_id=task_of.get(tid, "")))
    return out


def _reported():
    data = _read(JOBS / "reported.json", {}) or {}
    t = time.time()
    return {k: v for k, v in data.items() if isinstance(v, (int, float)) and t - v < REPORTED_KEEP_S}


def _mark_reported(keys):
    data = _reported()
    for k in keys:
        data[k] = time.time()
    try:
        _write(JOBS / "reported.json", data)
    except OSError:
        pass


def start_block(owner, transcript, mark=True):
    """Il blocco del SessionStart: i comandi in background interrotti di `transcript` e i lavori di `bg` della sessione
    `owner` morti senza esito, mai detti prima. Stringa vuota se non c'e' niente."""
    rep = _reported()
    cmds = [x for x in (interrupted(transcript) if transcript else []) if f"bash:{x['tool_use_id']}" not in rep]
    dead = [j for j in all_jobs() if owner and j.get("owner") == owner and j["state"] == "died" and f"job:{j['id']}" not in rep]
    if not cmds and not dead:
        return ""
    lines = [M("bg.block_head", n=len(cmds) + len(dead))]
    for x in cmds[:SHOW_MAX]:
        lines.append(M("bg.block_cmd", at=hm(x["at"]), what=x["description"] or x["command"][:80], cmd=x["command"][:160]))
    for j in dead[:SHOW_MAX]:
        lines.append(M("bg.block_job", at=hm(j.get("started")), what=j.get("label"), log=j["log"]))
    if len(cmds) + len(dead) > 2 * SHOW_MAX:
        lines.append(M("bg.block_more", n=len(cmds) + len(dead) - 2 * SHOW_MAX))
    lines.append(M("bg.block_tail"))
    if mark:
        _mark_reported([f"bash:{x['tool_use_id']}" for x in cmds] + [f"job:{j['id']}" for j in dead])
    return "\n".join(lines)


def main(argv):
    if argv[:1] == ["_run"] and len(argv) > 1:
        return run_job(argv[1])
    if argv[:1] in (["list"], ["elenco"]):
        return cmd_list("--json" in argv)
    if argv[:1] == ["log"] and len(argv) > 1:
        d = JOBS / argv[1]
        if not d.is_dir():
            print(M("bg.unknown", id=argv[1]), file=sys.stderr)
            return 1
        print(tail(d / "out.log", int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else 40))
        return 0
    if argv[:1] == ["interrupted"] and len(argv) > 1:   # per restore e per i test: i comandi interrotti di una trascrizione
        unseen = "--unreported" in argv
        rep = _reported() if unseen else {}
        print(json.dumps([x for x in interrupted(argv[1]) if f"bash:{x['tool_use_id']}" not in rep], ensure_ascii=False))
        return 0
    label = None
    if argv[:1] == ["--name"] and len(argv) > 1:
        label, argv = argv[1], argv[2:]
    if argv[:1] != ["--"]:
        print(M("bg.usage"), file=sys.stderr)
        return 2
    return start(label, argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
