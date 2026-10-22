#!/usr/bin/env python3
"""supervisor night — turno di notte: una coda di lavori non presidiati eseguiti con `claude -p` (N5).

  supervisor night add <cartella> "prompt" [--account N] [--model M] [--effort E] [--max-turns N]
  supervisor night list | remove <id> | clear
  supervisor night run [--dry-run] [--one] [--send]     esegue la coda (dal cron: night.cron_time)
  supervisor night install | uninstall | status

Ogni voce è una riga di `<state_dir>/night-queue.jsonl`, al massimo `night.max_queued` (la coda viaggia intera nello
stato del relay, che sta in 8 KB). `run` le prende in ordine, al massimo
`night.max_items_per_run` per giro, UNA alla volta, e prima di ognuna controlla la RAM libera
(`night.min_free_mb`, da /proc/meminfo) e la quota delle cinque ore dell'account
(`night.max_quota_pct`, dai file di fable-director letti da cm-quota): sotto soglia la voce resta in
coda con il motivo nel log. Il lavoro gira in `claude -p` nella cartella, con `--permission-mode`
e `--max-turns` da config, `CLAUDE_CODE_TOOL_MEMORY_LIMIT` contro le build impazzite, il retry watchdog sui 429/529
(`night.retry_watchdog_max_wait_ms`, 0 = spento; al limite d'uso aspetta il reset al massimo
`night.retry_watchdog_max_usage_limit_wait_ms`, ricontrollando il limite ogni
`night.retry_watchdog_usage_limit_recheck_ms`) e un tetto di
tempo (`night.item_timeout_s`). `claude -p` esce in stream-json: l'esito e' l'evento `result`, e dopo il risultato la
voce si chiude se per `night.after_result_s` non succede altro (un comando lasciato in background, una sveglia).
L'esito va in `<cartella>/<night.out_subdir>/<data>-<id>.md`
(prompt, argomenti, durata, output) e la voce passa in `night-done.jsonl`. Con --send il riassunto
del giro va su Telegram (stesso bot del plugin, sendMessage).

La coda si cambia anche dal telefono mentre `run` gira (contratto 1.17 del relay): ogni lettura-scrittura del file
passa da un lock, `run` rilegge la coda prima e dopo ogni voce invece di riscriverla dalla lettura iniziale, e la
voce in esecuzione porta `started` (epoch): `remove` la rifiuta.

Diverso da `queue` (un prompt per una sessione VIVA al prossimo Stop): qui le sessioni nascono
apposta e muoiono. Prove: CM_CLAUDE_BIN (claude finto), CM_NIGHT_FREE_MB (RAM finta), CM_CRONTAB_CMD.
"""
import contextlib
import datetime as dt
import fcntl
import importlib.util
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
N = CFG["night"]
CLAUDE = cm.claude_bin()   # parte dal cron delle 02:00: il PATH non ha ~/.local/bin


def queue_path():
    return Path(cm.expand(N["queue_file"]))


def done_path():
    return Path(cm.expand(N["done_file"]))


def log(line):
    p = Path(cm.expand(N["log"]))
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")


def read_jsonl(p):
    if not p.is_file():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def write_jsonl(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    os.replace(tmp, p)


@contextlib.contextmanager
def locked_queue():
    """La coda sotto lock esclusivo: (righe, salva). Il relay (add/remove dal telefono) e `run` si danno il turno."""
    q = queue_path()
    q.parent.mkdir(parents=True, exist_ok=True)
    with open(q.with_name(q.name + ".lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        yield read_jsonl(q), (lambda rows: write_jsonl(q, rows))


def deduce_account(d):
    """L'account dalla mappa delle cartelle (folder_map), altrimenti quello di default: cm-config.account_for."""
    return cm.account_for(CFG, d)[0]


# ------------------------------------------------------------------ coda
def add(argv):
    if len(argv) < 2:
        print(M("night.add_usage"), file=sys.stderr)
        return 2
    d = os.path.realpath(cm.expand(argv[0]))
    prompt = argv[1].strip()
    if not prompt:
        print(M("night.empty_prompt"), file=sys.stderr)
        return 2
    if not os.path.isdir(d):
        print(M("night.no_dir", dir=d), file=sys.stderr)
        return 2
    item = {"id": uuid.uuid4().hex[:8], "dir": d, "prompt": prompt, "account": "", "model": "", "effort": "",
            "max_turns": int(N["max_turns"]), "added": dt.datetime.now().isoformat(timespec="seconds")}
    rest = argv[2:]
    i = 0
    while i < len(rest):
        a = rest[i]
        v = rest[i + 1] if i + 1 < len(rest) else ""
        if a == "--account":
            item["account"] = v; i += 2
        elif a == "--model":
            item["model"] = v; i += 2
        elif a == "--effort":
            item["effort"] = v; i += 2
        elif a == "--max-turns":
            item["max_turns"] = int(v); i += 2
        else:
            print(M("launch.unknown_option", opt=a), file=sys.stderr)
            return 2
    if not item["account"]:
        item["account"] = deduce_account(d)
    if item["account"] not in CFG["accounts"]:
        print(M("launch.unknown_account", account=item["account"], known=", ".join(CFG["accounts"])), file=sys.stderr)
        return 3
    with locked_queue() as (rows, save):
        if len(rows) >= int(N["max_queued"]):
            print(M("night.full", n=len(rows), max=N["max_queued"]), file=sys.stderr)
            return 4
        rows.append(item)
        save(rows)
    print(M("night.added", id=item["id"], dir=d, account=item["account"], n=len(rows)))
    return 0


def list_queue():
    rows = read_jsonl(queue_path())
    if not rows:
        print(M("night.empty"))
        return 0
    for r in rows:
        print(f"  {r['id']}  {r['account']:<13} {os.path.basename(r['dir']):<24} {r['prompt'][:60]}")
    print(M("night.count", n=len(rows)))
    return 0


def remove(argv):
    if not argv:
        print(M("night.remove_usage"), file=sys.stderr)
        return 2
    with locked_queue() as (rows, save):
        hit = next((r for r in rows if r["id"] == argv[0]), None)
        if hit is None:
            print(M("night.not_found", id=argv[0]), file=sys.stderr)
            return 1
        if hit.get("started"):
            print(M("night.already_started", id=argv[0]), file=sys.stderr)
            return 5
        save([r for r in rows if r["id"] != argv[0]])
    print(M("night.removed", id=argv[0]))
    return 0


def clear():
    with locked_queue() as (_, save):
        save([])
    print(M("night.cleared"))
    return 0


# ------------------------------------------------------------------ guardie
def free_mb():
    if os.environ.get("CM_NIGHT_FREE_MB"):
        return int(os.environ["CM_NIGHT_FREE_MB"])
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 10 ** 6


def quota_pct(account):
    """La quota delle cinque ore dell'account (0-100) o None se non si sa."""
    try:
        q = _load("cm-quota")
        r = q.leggi(cm.expand(CFG["accounts"][account]["config_dir"]))
    except Exception:  # noqa: BLE001 — la quota e' un consiglio, non un requisito
        return None
    if r.get("stato") != "ok" or r.get("vecchia"):
        return None
    return r.get("cinque_ore_pct")


def blocked(item):
    """Il motivo per cui la voce NON parte adesso, o ''."""
    mb = free_mb()
    if mb < int(N["min_free_mb"]):
        return M("night.low_ram", mb=mb, min=N["min_free_mb"])
    pct = quota_pct(item["account"])
    if pct is not None and pct >= int(N["max_quota_pct"]):
        return M("night.quota_high", account=item["account"], pct=round(pct), max=N["max_quota_pct"])
    if not os.path.isdir(item["dir"]):
        return M("night.no_dir", dir=item["dir"])
    # registro unico delle prenotazioni (piano multi-PC 3.4): la voce conta come un lavoro pesante sulla regia
    why = offload().night_blocked(item["account"])
    if why:
        return why
    return ""


_OFF = None


def offload():
    global _OFF
    if _OFF is None:
        _OFF = _load("cm-offload")
    return _OFF


# ------------------------------------------------------------------ esecuzione
def account_env(account):
    env = dict(os.environ)
    conf = cm.expand(CFG["accounts"][account]["config_dir"])
    if os.path.realpath(conf) != os.path.realpath(cm.expand("~/.claude")):   # T68
        env["CLAUDE_CONFIG_DIR"] = conf
    else:
        env.pop("CLAUDE_CONFIG_DIR", None)
    if N["tool_memory_limit"]:
        env["CLAUDE_CODE_TOOL_MEMORY_LIMIT"] = str(N["tool_memory_limit"])
    # 09/10/2026 (Claude Code 2.1.295): on 429/529 the CLI keeps retrying instead of giving up after a few tries,
    # up to a total wait; below item_timeout_s, so an overloaded API at 3 a.m. delays an item instead of failing it
    if N.get("retry_watchdog_max_wait_ms"):
        env["CLAUDE_CODE_RETRY_WATCHDOG"] = "1"
        env["CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS"] = str(int(N["retry_watchdog_max_wait_ms"]))
        # 16/10/2026 (Claude Code 2.1.301): at a usage limit the watchdog waits for the reset only up to this, then ends
        # the request; a weekly limit can reset days later, and the item would hold the night until item_timeout_s
        if N.get("retry_watchdog_max_usage_limit_wait_ms"):
            env["CLAUDE_CODE_RETRY_WATCHDOG_MAX_USAGE_LIMIT_WAIT_MS"] = str(int(N["retry_watchdog_max_usage_limit_wait_ms"]))
        # 21/10/2026 (Claude Code 2.1.305): while waiting at a usage limit the watchdog re-checks it this often, so a
        # limit lifted before its reset (extra usage, a plan change) frees the item without waiting for the reset
        if N.get("retry_watchdog_usage_limit_recheck_ms"):
            env["CLAUDE_CODE_RETRY_WATCHDOG_USAGE_LIMIT_RECHECK_MS"] = str(int(N["retry_watchdog_usage_limit_recheck_ms"]))
    # 16/10/2026 (Claude Code 2.1.302): fork subagents are on by default also in `claude -p`; a fork re-pays the whole
    # context every turn, and at night nobody watches the bill. Off unless night.fork_subagent is true
    if not N.get("fork_subagent"):
        env["CLAUDE_CODE_FORK_SUBAGENT"] = "0"
    return env


def run_item(item, dry):
    cmd = [CLAUDE, "-p", item["prompt"], "--permission-mode", N["permission_mode"], "--max-turns", str(item["max_turns"])]
    if item.get("model"):
        cmd += ["--model", item["model"]]
    if item.get("effort"):
        cmd += ["--effort", item["effort"]]
    # 16/10/2026: l'esito dall'evento `result` (con -p, stream-json vuole --verbose). Dalla 2.1.295 l'uscita testuale
    # stampa la risposta di ogni turno, e la sua prima riga non e' piu' l'esito finale
    cmd += ["--output-format", "stream-json", "--verbose"]
    if dry:
        return {"rc": None, "out": "", "seconds": 0, "cmd": cmd}
    t0 = time.time()
    res = stream_item(cmd, item["dir"], account_env(item["account"]))
    res.update(seconds=round(time.time() - t0), cmd=cmd)
    return res


def stream_item(cmd, cwd, env):
    """`claude -p` in stream-json, letto evento per evento. Dalla 2.1.292 `claude -p` aspetta i comandi lasciati in
    background e le sveglie programmate prima di uscire: un server dimenticato o un ScheduleWakeup tenevano ferma la
    coda fino a item_timeout_s. Dopo un evento `result`, se per night.after_result_s non arriva altro, la voce si chiude
    (SIGTERM al suo gruppo di processi, figli compresi) e l'output lo dice; un turno che riparte (un comando in
    background finito) rimanda la chiusura. rc: quello del processo; 124 allo scadere di item_timeout_s; per una voce
    chiusa dopo il risultato 0, o 1 se il risultato era un errore. `out` = i testi dei `result`, uno per turno; senza
    `result` i testi dell'assistente; senza eventi riconosciuti l'uscita com'e'."""
    timeout_s, grace = int(N["item_timeout_s"]), int(N.get("after_result_s") or 0)
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, errors="replace", start_new_session=True)
    lines = queue.Queue()

    def pump(stream, tag):
        for line in stream:
            lines.put((tag, line))
        lines.put((tag, None))

    for stream, tag in ((p.stdout, "out"), (p.stderr, "err")):
        threading.Thread(target=pump, args=(stream, tag), daemon=True).start()
    raw, err, results, said = [], [], [], []
    deadline, quiet_since, ended, open_streams = time.time() + timeout_s, None, None, 2

    def take(tag, line):
        nonlocal quiet_since
        if tag == "err":
            err.append(line)
            return
        raw.append(line)
        try:
            ev = json.loads(line)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        if ev.get("type") == "result":
            results.append(ev)
            quiet_since = time.time()
            return
        quiet_since = None   # un turno in corso: niente chiusura
        if ev.get("type") == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "text" and str(b.get("text") or "").strip():
                    said.append(b["text"].strip())

    while open_streams:
        limit = min(deadline, quiet_since + grace) if quiet_since and grace else deadline
        try:
            tag, line = lines.get(timeout=max(0.05, limit - time.time()))
        except queue.Empty:
            if time.time() >= limit:
                ended = "timeout" if time.time() >= deadline else "after_result"
                break
            continue
        if line is None:
            open_streams -= 1
        else:
            take(tag, line)
    if not ended:
        try:
            p.wait(timeout=max(1.0, deadline - time.time()))
        except subprocess.TimeoutExpired:
            ended = "timeout"
    if ended:
        stop_group(p)
        end = time.time() + 2   # quello che era gia' scritto prima della chiusura
        while open_streams and time.time() < end:
            try:
                tag, line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                open_streams -= 1
            else:
                take(tag, line)
    texts = [str(r.get("result") or "").strip() for r in results if str(r.get("result") or "").strip()]
    out = "\n\n".join(texts or said) or "".join(raw)
    if "".join(err).strip():
        out += "\n[stderr]\n" + "".join(err)
    if ended == "timeout":
        rc, out = 124, out + "\n" + M("night.timeout", s=N["item_timeout_s"])
    elif ended == "after_result":
        rc, out = (1 if results[-1].get("is_error") else 0), out + "\n" + M("night.closed_after_result", s=grace)
    else:
        rc = p.returncode
    return {"rc": rc, "out": out, "final": texts[-1] if texts else "", "closed_after_result": ended == "after_result"}


def stop_group(p):
    """SIGTERM al gruppo della voce (claude e i comandi lasciati in background), poi SIGKILL a chi resta."""
    for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            p.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            pass


def write_report(item, res):
    out_dir = Path(item["dir"]) / N["out_subdir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", item["prompt"][:40]).strip("-").lower() or "lavoro"
    f = out_dir / f"{dt.date.today().isoformat()}-{item['id']}-{slug}.md"
    f.write_text(
        f"# Turno di notte {dt.date.today().isoformat()} — {os.path.basename(item['dir'])}\n\n"
        f"- id: `{item['id']}` · account: {item['account']} · modello: {item.get('model') or '-'} · effort: {item.get('effort') or '-'}\n"
        f"- max turni: {item['max_turns']} · durata: {res['seconds']} s · esito: rc={res['rc']}\n"
        f"- aggiunto: {item['added']}\n\n## Prompt\n\n{item['prompt']}\n\n## Output\n\n{res['out'].strip()}\n")
    return f


ESITO_RE = re.compile(r"^\**(?:esito|outcome)\**\s*:\s*\**", re.I)   # come cm-night-report


def summary_line(item, res, report):
    # 16/10: dal risultato finale (stream-json), la sua riga «Esito:» se c'e'; senza risultato, la prima riga dell'output
    src = [l.strip() for l in (res.get("final") or res["out"]).splitlines() if l.strip()]
    esito = next((ESITO_RE.sub("", l) for l in reversed(src) if ESITO_RE.match(l)), None) if res.get("final") else None
    first = (esito or (src[0] if src else ""))[:120]
    mark = "ok" if res["rc"] == 0 else "KO"
    return f"{mark} {os.path.basename(item['dir'])} ({item['account']}, {res['seconds']} s, rc={res['rc']}): {first}\n   {report}"


def refresh_report():
    """Il rapporto della pagina «Notte» rigenerato a fine giro (07/10: quello fatto a meta' notte non vedeva il resto).
    Facoltativo: se cade, il giro e' fatto lo stesso e il log lo dice."""
    cmd = [sys.executable, os.environ.get("CM_NIGHT_REPORT_BIN") or str(HERE / "cm-night-report.py")]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        log(f"night report: rc={p.returncode} " + " ".join((p.stdout or p.stderr).split())[:300])
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"night report: {e.__class__.__name__}")


def report():
    """21/10: il rapporto della notte a mano o dal cron di night.report_time, anche con la coda vuota (dal 07/10 il
    lavoro notturno gira in sessioni parallele e `run` usciva prima, con night.empty, senza scriverlo)."""
    cmd = [sys.executable, os.environ.get("CM_NIGHT_REPORT_BIN") or str(HERE / "cm-night-report.py")]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    log(f"night report: rc={p.returncode} " + " ".join((p.stdout or p.stderr).split())[:300])
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    return p.returncode


def run(argv):
    dry = "--dry-run" in argv or "--prova" in argv
    one = "--one" in argv
    send = "--send" in argv
    rows = read_jsonl(queue_path())
    if not rows:
        print(M("night.empty"))
        return 0
    cap = 1 if one else int(N["max_items_per_run"])
    lines = []
    ran = 0
    for first in rows:
        if ran >= cap:
            break
        with locked_queue() as (now_rows, save):
            # tolta dal telefono dopo la lettura iniziale: non si esegue
            item = next((r for r in now_rows if r["id"] == first["id"]), None)
            why = blocked(item) if item else ""
            if item and not why and not dry:
                # `started` resta anche se il giro cade a meta': il giro dopo la riprende e la rimarca
                item["started"] = int(time.time())
                save(now_rows)
        if item is None:
            continue
        if why:
            log(f"{item['id']} skipped: {why}")
            print(M("night.skipped", id=item["id"], dir=os.path.basename(item["dir"]), why=why))
            continue
        if dry:
            res = run_item(item, True)
            print(M("night.would_run", id=item["id"], dir=item["dir"], cmd=" ".join(res["cmd"][:1] + ["-p", "…"] + res["cmd"][3:])))
            ran += 1
            continue
        log(f"{item['id']} start: {item['dir']} ({item['account']})")
        with offload().night_lease(f"night {item['id']}"):
            res = run_item(item, False)
        report = write_report(item, res)
        log(f"{item['id']} done: rc={res['rc']} in {res['seconds']} s → {report}"
            + (" (closed after the result: the process stayed open)" if res.get("closed_after_result") else ""))
        with locked_queue() as (now_rows, save):
            save([r for r in now_rows if r["id"] != item["id"]])
        done_rows = read_jsonl(done_path())
        done_rows.append({**item, "rc": res["rc"], "seconds": res["seconds"], "report": str(report),
                          "finished": dt.datetime.now().isoformat(timespec="seconds")})
        write_jsonl(done_path(), done_rows)
        lines.append(summary_line(item, res, report))
        print(lines[-1])
        ran += 1
    if ran and not dry:
        refresh_report()
    if send and lines:
        text = M("night.summary_title", n=len(lines), left=len(read_jsonl(queue_path()))) + "\n" + "\n".join(lines)
        # contratto 1.18: il resoconto anche all'app, evento `night_report` del relay, prima di Telegram
        _load("cm-core").relay_event("night_report", M("night.event_title", n=len(lines), ok=sum(1 for l in lines if l.startswith("ok "))),
                                     text, ref=dt.date.today().isoformat())
        bot = _load("cm-bot")
        if bot.token():
            print(M("recap.sent", n=bot.send(text)))
        else:
            print(M("bot.no_token", path=CFG["bot"]["token_file"]), file=sys.stderr)
    return 0


# ------------------------------------------------------------------ cron
def cron_line():
    hh, _, mm = str(N["cron_time"]).partition(":")
    shim = cm.home() / ".local" / "bin" / "supervisor"
    return f"{int(mm or 0)} {int(hh or 2)} * * * {shim} night run --send >/dev/null 2>&1"


def report_cron_line():
    hh, _, mm = str(N.get("report_time") or "06:30").partition(":")
    shim = cm.home() / ".local" / "bin" / "supervisor"
    return f"{int(mm or 0)} {int(hh or 6)} * * * nice -n 19 {shim} night report >/dev/null 2>&1"


def crontab_read():
    return subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-l"], capture_output=True, text=True).stdout


def crontab_write(text):
    subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-"], input=text, text=True, check=True)


def install():
    """Le due righe: il turno di notte (night.cron_time) e il rapporto del mattino (night.report_time, 21/10); chi ha
    gia' la prima riceve solo la seconda."""
    cur = crontab_read()
    add = [(k, line) for k, line in (("supervisor night run", cron_line()), ("supervisor night report", report_cron_line())) if k not in cur]
    if not add:
        print(M("night.cron_present"))
        return 0
    text = cur.rstrip("\n") + ("\n" if cur.strip() else "")
    for k, line in add:
        text += ("# supervisor night: turno di notte (coda di lavori non presidiati)\n" if k.endswith("run") else
                 "# supervisor night: rapporto della notte per la pagina «Notte»\n") + line + "\n"
        print(M("night.cron_installed", line=line))
    crontab_write(text)
    return 0


def uninstall():
    cur = crontab_read()
    if "supervisor night run" not in cur:
        print(M("night.cron_absent"))
        return 0
    lines = [l for l in cur.splitlines() if "supervisor night" not in l]
    crontab_write("\n".join(lines) + ("\n" if lines else ""))
    print(M("night.cron_removed"))
    return 0


def status():
    cur = crontab_read()
    print(M("night.status_cron", state="yes" if "supervisor night run" in cur else "no", line=cron_line()))
    print(M("night.status_cron", state="yes" if "supervisor night report" in cur else "no", line=report_cron_line()))
    print(M("night.status_queue", n=len(read_jsonl(queue_path())), done=len(read_jsonl(done_path())), path=queue_path()))
    print(M("night.status_guards", mb=free_mb(), min=N["min_free_mb"], max=N["max_quota_pct"]))
    return 0


def main(argv):
    verb = argv[0] if argv else "list"
    rest = argv[1:]
    if verb == "add":
        return add(rest)
    if verb in ("list", "ls"):
        return list_queue()
    if verb == "remove":
        return remove(rest)
    if verb == "clear":
        return clear()
    if verb == "run":
        return run(rest)
    if verb == "report":
        return report()
    if verb == "install":
        return install()
    if verb == "uninstall":
        return uninstall()
    if verb == "status":
        return status()
    print(M("night.usage"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
