# Part of cm-relay.py: the master's suggestions, today's usage, closing sessions from the app, kill and stop, the waits and the cards.
# Not a module of its own: cm-relay.py executes this file in its own namespace, at this point of its body, so the
# names here share globals with cm-relay.py (the tests patch them there). A comment, not a docstring: a docstring
# here would become cm-relay.py's __doc__. Split out on 24/11/2026 so that no file of the plugin is over the
# 256 KiB the plugin directory reads (supervisor-relay-256kb-24-11); the code is unchanged.

# ------------------------------------------------------------------ 1.84 (24/10): suggerimenti, quota di oggi, chiusure
def suggestions_list(now):
    """1.84: i suggerimenti della master (cm-suggest.py) per /state.suggestions; [] se non ce ne sono."""
    try:
        return _load("cm-suggest").for_state(int(now))
    except Exception:   # noqa: BLE001 — un extra: mai bloccare la push
        return []


def suggestion_act(cmd, arg):
    """1.84 (24/10, scheda «Suggerimenti della master»): `arg` = l'id, `action` accept | later | never. accept manda il
    prompt della voce alla master come un prompt dall'app, con l'etichetta «dai suggerimenti», e toglie la voce; later
    la nasconde fino al giro dopo della master; never non la ripropone piu'. 1.88 (27/10): choose con `choice`, una
    delle `options` della voce, manda il prompt con la scelta ({choice} nel prompt, o «Scelta: …» in fondo) e toglie la
    voce; discuss manda «Parliamone, senza farlo: <testo>» e la voce resta."""
    action, choice = str(cmd.get("action") or "").strip(), cmd.get("choice")
    code, hit = _load("cm-suggest").act(str(arg or "").strip(), action, choice=None if choice is None else str(choice))
    if code == "bad_action":
        return False, M("relay.cmd_suggestion_bad_action", action=action or "?")
    if code == "gone":   # 1.87 (27/10): tolto con suggest clear o scaduto mentre la scheda era sul telefono
        return True, M("relay.cmd_suggestion_gone")
    if code == "bad_choice":
        return False, M("relay.cmd_suggestion_bad_choice", choice=str(choice or ""))
    row = {"id": hit["id"], "key": hit.get("key"), "kind": hit.get("kind"), "source": hit.get("source") or "master", "action": code, "by": cmd.get("by")}
    if code == "chosen":
        row["choice"] = choice
    ledger_write("suggestion", **row)
    if code == "hidden":
        return True, M("relay.cmd_suggestion_later")
    if code == "never":
        return True, M("relay.cmd_suggestion_never")
    prompt = str(hit.get("prompt") or "")
    if code == "chosen":
        prompt = prompt.replace("{choice}", str(choice)) if "{choice}" in prompt else prompt + " " + M("relay.suggestion_choice", choice=choice)
    elif code == "discussed":
        prompt = M("relay.suggestion_discuss") + " " + str(hit.get("text") or "")
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    rc, out = relay_talk(master, prefix_for(cmd, master) + " " + M("relay.suggestion_label") + " " + prompt, "--no-wait")
    if rc != 0:
        return False, (out.splitlines() or ["talk failed"])[0]
    if saved_in_inbox(out):
        return True, M("relay.cmd_suggestion_inbox")
    aw = read_json(rdir() / "awaiting.json", {}); aw[master] = int(time.time()); write_json(rdir() / "awaiting.json", aw)
    return True, M({"chosen": "relay.cmd_suggestion_chosen", "discussed": "relay.cmd_suggestion_discussed"}.get(code, "relay.cmd_suggestion_sent"))


USAGE_CACHE_S = 180


def _midnight(now):
    t = time.localtime(now)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def usage_today(rows, now):
    """1.84 (24/10, B5): [{session, account, tokens, share}] — i token di oggi (dalla mezzanotte locale) di ogni
    sessione, letti dalle trascrizioni di ogni account (anche le sessioni chiuse oggi, la master e i loro subagenti), e
    la loro quota sul totale dell'account (0..1). Conta input, output e cache come li scrive l'API (usage di ogni
    messaggio, un messaggio una volta sola). Incrementale: rdir()/usage-today.json tiene per ogni file dove si era
    arrivati, e il conto si rifà al piu' ogni USAGE_CACHE_S; dal giorno dopo si riparte. Non e' esatto al token: le
    sessioni su un altro host non ci sono."""
    now = int(now)
    day0 = _midnight(now)
    cpath = rdir() / "usage-today.json"
    cache = read_json(cpath, {}) or {}
    if cache.get("day") != day0:
        cache = {"day": day0, "files": {}}
    if now - int(cache.get("at") or 0) >= USAGE_CACHE_S or not cache.get("out"):
        since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(day0))
        files = cache["files"]
        for acc, a in (CFG.get("accounts") or {}).items():
            base = Path(cm.expand(a.get("config_dir") or "~/.claude")) / "projects"
            try:
                found = [p for p in base.glob("*/*.jsonl")] + [p for p in base.glob("*/*/subagents/*.jsonl")]
            except OSError:
                continue
            for p in found:
                if not cm.transcript_in_scope(CFG, p):   # 12/11 (supervisor-ambito-sessioni-12-11): solo l'ambito
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_mtime < day0:
                    continue
                k = str(p)
                f = files.get(k) or {"acc": acc, "off": 0, "tokens": 0, "last": "", "cwd": "",
                                     "sid": p.parent.parent.name if p.parent.name == "subagents" else p.stem}
                if st.st_size < f["off"]:
                    f.update(off=0, tokens=0, last="")
                if st.st_size > f["off"]:
                    try:
                        with open(p, "rb") as fh:
                            fh.seek(f["off"])
                            chunk = fh.read()
                    except OSError:
                        continue
                    end = chunk.rfind(b"\n") + 1
                    for line in chunk[:end].splitlines():
                        if not f["cwd"] and b'"cwd"' in line:
                            try:
                                f["cwd"] = str(json.loads(line).get("cwd") or "")
                            except ValueError:
                                pass
                        if b'"usage"' not in line:
                            continue
                        try:
                            d = json.loads(line)
                        except ValueError:
                            continue
                        if str(d.get("timestamp") or "") < since:
                            continue
                        msg = d.get("message") or {}
                        u = msg.get("usage") if isinstance(msg, dict) else None
                        if not isinstance(u, dict):
                            continue
                        mid = str(msg.get("id") or d.get("requestId") or "")
                        if mid and mid == f["last"]:
                            continue   # lo stesso messaggio scritto a pezzi: un blocco per riga, lo stesso usage
                        f["last"] = mid
                        f["tokens"] += sum(int(u.get(x) or 0) for x in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
                    f["off"] += end
                files[k] = f
        by_sid = {r.get("session_id"): S.short_name(r.get("name") or r.get("tmux") or "", prefixes()) for r in rows or [] if r.get("session_id")}
        tot, per = {}, {}
        for f in files.values():
            if not f.get("tokens"):
                continue
            name = by_sid.get(f["sid"]) or (os.path.basename(f["cwd"].rstrip("/")) if f.get("cwd") else "") or f["sid"][:8]
            per[(name, f["acc"])] = per.get((name, f["acc"]), 0) + f["tokens"]
            tot[f["acc"]] = tot.get(f["acc"], 0) + f["tokens"]
        cache["out"] = [{"session": n, "account": acc, "tokens": t, "share": round(t / tot[acc], 3) if tot.get(acc) else 0}
                        for (n, acc), t in sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))]
        cache["at"] = now
        write_json(cpath, cache)
    return list(cache.get("out") or [])


CLOSED_KEEP_S = 7 * 86400   # 1.84: per quanto resta il salvataggio di una sessione chiusa dall'app
BG_SHELLS = ("bash", "sh", "zsh", "dash", "fish")


def closed_dir():
    return Path(cm.expand(CFG["state_dir"])) / "closed"


def closed_file(tm):
    return closed_dir() / f"{re.sub(r'[^A-Za-z0-9_.:-]', '_', tm)}.json"


def closed_saved(now=None):
    """1.84: {tmux: salvataggio} delle sessioni chiuse dall'app (op close, /exit) negli ultimi CLOSED_KEEP_S; i piu'
    vecchi vanno via."""
    now = float(now or time.time())
    out = {}
    d = closed_dir()
    if not d.is_dir():
        return out
    for f in d.glob("*.json"):
        rec = read_json(f, None)
        if not isinstance(rec, dict) or now - float(rec.get("closed_at") or 0) > CLOSED_KEEP_S:
            f.unlink(missing_ok=True)
            continue
        if rec.get("tmux"):
            out[rec["tmux"]] = rec
    return out


def git_dirty(cwd):
    """(dirty_n, i primi DIRTY_MAX percorsi) da `git status --porcelain`; (None, None) fuori da git o se git non risponde."""
    if not cwd or not os.path.isdir(cwd):
        return None, None
    try:
        r = subprocess.run(["git", "-C", cwd, "status", "--porcelain"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if r.returncode != 0:
        return None, None
    paths = [ln[3:].strip() for ln in r.stdout.splitlines() if len(ln) > 3]
    return len(paths), paths[:S.DIRTY_MAX]


def _children(pid):
    """I processi figli di pid: [(pid, nome del comando)]; [] se non si leggono (Linux e macOS: cm-proc)."""
    return _cm_proc().children(pid)


def _cmdline(pid):
    """Gli argomenti del processo pid; [] se non si leggono."""
    return _cm_proc().argv(pid)


def statusline_cmds(cwd=None):
    """28/10: i comandi della barra di stato (statusLine.command) nelle impostazioni degli account e del progetto."""
    files = [Path(cm.expand(a.get("config_dir") or "")) / "settings.json" for a in (CFG.get("accounts") or {}).values() if a.get("config_dir")]
    if cwd:
        files += [Path(cwd) / ".claude" / "settings.json", Path(cwd) / ".claude" / "settings.local.json"]
    out = set()
    for f in files:
        sl = (read_json(f, {}) or {}).get("statusLine") if f.is_file() else None
        if isinstance(sl, dict) and isinstance(sl.get("command"), str) and sl["command"].strip():
            out.add(sl["command"].strip())
    return out


def background_of(row):
    """1.84: la sessione ha un lavoro in sottofondo — un Bash in background o un processo figlio di una shell: fra i
    figli del processo claude una shell (i server MCP sono node o python, non shell). 23/11: anche per le sessioni su
    win, dalle shell figlie che porta lo stato dell'host (remote_background_pids).
    28/10: la barra di stato non conta — Claude Code la lancia ogni pochi secondi come «/bin/sh -c <statusLine.command>»,
    e la chiusura falliva a caso."""
    return bool(background_pids(row))


# 23/11 (supervisor-chiusura-remota-23-11): su win le shell degli strumenti di Claude Code si riconoscono dalla riga di
# comando — il Bash parte con «bash.exe -c "source …/shell-snapshots/snapshot-bash-…"», il PowerShell con «cmd.exe /d /s
# /c "chcp 65001 & powershell … $env:CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT"». Le altre shell figlie non sono sottofondo: la
# barra di stato («bash.exe -c "<statusLine.command>"», ogni pochi secondi) e i server MCP («cmd.exe /d /s /c "npx …"»).
# Visti il 23/11 su Claude Code 2.1.329 per Windows.
WIN_TOOL_SHELL = ("/shell-snapshots/", "CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT")


def remote_background_pids(row):
    """I pid del sottofondo di una sessione di un altro host, dalle shell figlie che porta lo stato dell'host (`shells`
    della riga, cm-remote.ps1); [] con l'aiutante di prima."""
    out = []
    for c in row.get("shells") or []:
        cmd = str(c.get("cmd") or "").replace("\\", "/")
        try:
            k = int(c.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if k > 0 and any(m in cmd for m in WIN_TOOL_SHELL):
            out.append(k)
    return out


def kill_background(row, pids):
    """«Chiudi comunque»: ferma i lavori in sottofondo. Qui SIGTERM; su un altro host (04/11 ne restava fuori)
    `taskkill /T /F`, l'albero intero (la release in wsl.exe sotto il bash.exe), e solo se quel pid e' ancora figlio
    del processo claude della sessione: il pid viene da uno stato di fino a un minuto prima."""
    if not pids:
        return
    host = row.get("host")
    if not host or host == "local":
        for k in pids:
            try:
                os.kill(k, signal.SIGTERM)
            except OSError:
                pass
        return
    parent = int(row.get("pid") or 0)
    script = ("foreach ($k in @(%s)) { $p = Get-CimInstance Win32_Process -Filter \"ProcessId=$k\" -ErrorAction SilentlyContinue; "
              "if ($p -and [int]$p.ParentProcessId -eq %d) { cmd /c \"taskkill /T /F /PID $k >nul 2>&1\" } }"
              % (",".join(str(int(k)) for k in pids), parent))
    try:
        rc, _, err = _hosts_mod().adapter(host).exec(script, timeout=60)
        if rc != 0:
            log(f"sottofondo di {row.get('name')} su {host} non fermato: rc {rc} {S.one_line(err or '')[:160]}")
    except Exception as e:   # noqa: BLE001 — host spento o trasporto caduto: la chiusura armata va avanti lo stesso
        log(f"sottofondo di {row.get('name')} su {host} non fermato: {S.one_line(str(e))[:160]}")


def background_pids(row):
    """I pid delle shell figlie del processo claude che sono lavoro in sottofondo (vedi background_of); [] se non ce ne
    sono. 04/11: «Chiudi comunque» le ferma. 23/11: su un altro host da `shells` (remote_background_pids)."""
    pid = row.get("pid")
    if row.get("host") and row.get("host") != "local":
        return remote_background_pids(row)
    if not pid:
        return []
    shells = [k for k, name in _children(pid) if name in BG_SHELLS]
    if not shells:
        return []
    sls = statusline_cmds(row.get("cwd"))
    return [k for k in shells if " ".join(_cmdline(k)[2:]).strip() not in sls]


def close_check(name, rows, allow_attached=False):
    """1.84: {name, closable, blocked_by, save} per una sessione (nome dello stato). blocked_by: busy, question,
    background, gone (non e' viva) o null. save: {outcome, next, session_id, dirty_n, dirty}, solo per una
    chiudibile.
    20/11/2026 (supervisor-chiudi-attaccate-20-11; il maintainer alle 16:46: «perché la sessione non si chiude?»): una sessione
    aperta al PC non blocca piu' la chiusura. Come `supervisor close` dal 07/10, se e' ferma si chiude chiudendo la sua
    scheda; al lavoro, con una domanda o col sottofondo resta come le altre (after, force). Dalla 1.84 «attached» la
    bloccava in ogni modo e dal telefono nessuna sessione con la finestra aperta si poteva chiudere. allow_attached resta
    per chi lo passa (/exit), senza effetto."""
    tm = tmux_of(name)
    row = next((r for r in rows if (r.get("tmux") or r.get("name")) == tm), None)
    if not row:
        return {"name": name, "closable": False, "blocked_by": "gone", "save": None}, None
    info = last_sessions().get(name) or {}
    if row.get("waiting") or row.get("status") == "waiting":
        why = "question"
    elif row.get("status") == "busy" or info.get("state") in ("busy", "awaiting"):
        why = "busy"
    elif background_of(row):
        why = "background"
    else:
        why = None
    out = {"name": name, "closable": why is None, "blocked_by": why}
    if why is None:
        n, dirty = git_dirty(row.get("cwd")) if not row.get("host") or row.get("host") == "local" else (None, None)
        # next: il primo seguito dell'ultimo esito (next_steps), altrimenti il prossimo del recap — 24/10: quello del
        # recap poteva essere di giorni prima
        steps = [x.get("text") for x in info.get("next_steps") or [] if x.get("text")]
        out["save"] = {"outcome": ((info.get("outcome") or {}).get("short")) or None, "next": (steps[0] if steps else info.get("next")) or None,
                       "session_id": row.get("session_id") or None, "dirty_n": n, "dirty": dirty}
    else:
        out["save"] = None
    return out, row


def close_names(arg, session):
    names = [S.one_line(x).strip() for x in str(arg or session or "").split(",")]
    return [x for x in dict.fromkeys(names) if x][:20]


def close_preview(cmd, arg, session=""):
    """1.84 (24/10, B4): op close_preview, solo lettura — per ogni sessione di `arg` («nome1,nome2») cosa verra'
    salvato e cosa impedisce la chiusura. Il testo e' il JSON {sessions: [...]}."""
    names = close_names(arg, session)
    if not names:
        return False, M("relay.cmd_close_none")
    rows = _json_cmd("sessions", "--json") or []
    return True, json.dumps({"sessions": [close_check(n, rows)[0] for n in names]}, ensure_ascii=False)


def save_closed(chk, row, by):
    """Il salvataggio di una sessione prima di chiuderla: <state_dir>/closed/<tmux>.json."""
    tm = row.get("tmux") or row.get("name")
    rec = dict(chk["save"] or {}, name=chk["name"], tmux=tm, cwd=row.get("cwd") or "", account=row.get("account") or "",
               closed_at=int(time.time()), by=by or None)
    write_json(closed_file(tm), rec)
    return rec


CLOSE_WHEN = ("now", "after", "force")
CLOSE_FORCE_S = 180   # «Chiudi comunque»: il tempo massimo del passaggio di consegne, poi si chiude lo stesso
CLOSE_ARMED_MAX_S = 24 * 3600   # una chiusura armata che non scatta in un giorno si lascia cadere
_CLOSE_LOCK = threading.Lock()


def close_armed_path():
    return rdir() / "close-armed.json"


def close_armed():
    """04/11 (supervisor-chiudi-esito-04-11): le chiusure armate dall'app, {tmux: {name, mode, at, by, deadline,
    session_id}}. mode after = appena la sessione si ferma; force = dopo il passaggio di consegne o alla scadenza."""
    d = read_json(close_armed_path(), {})
    return d if isinstance(d, dict) else {}


def close_one(n, row, chk, by, armed=None, handoff=None):
    """Chiude una sessione chiudibile: salva lo stato, `supervisor close`, una riga nel ledger. → None, o il motivo.
    19/11 (1.155): `handoff` = {file, reason} del salvataggio prima della chiusura; senza, closed.handoff false."""
    rec = save_closed(chk, row, by)
    rec.update(handoff=bool((handoff or {}).get("file")), handoff_file=(handoff or {}).get("file") or None, reason=(handoff or {}).get("reason") or None)
    write_json(closed_file(rec["tmux"]), rec)
    rc, out = run_cm("close", row.get("tmux") or row.get("name"), "--subito")   # 19/11: il salvataggio lo fa il relay prima
    if rc != 0:
        closed_file(rec["tmux"]).unlink(missing_ok=True)
        return (out.strip().splitlines() or ["close failed"])[0].strip()
    ledger_write("closed-from-app", session=rec["tmux"], session_id=rec.get("session_id") or "", by=by, dirty_n=rec.get("dirty_n"),
                 **({"armed": armed} if armed else {}))
    return None


def close_force_start(n, row, chk, by):
    """«Chiudi comunque»: ferma il turno (o la domanda) e i lavori in sottofondo, chiede un breve passaggio di consegne e
    arma la chiusura: scatta a fine di quel turno, o comunque dopo CLOSE_FORCE_S."""
    tm = row.get("tmux") or row.get("name")
    if chk["blocked_by"] in ("busy", "question"):
        run_cm("interrupt", tm, "--wait", "10")
    kill_background(row, background_pids(row))
    rc, _ = relay_talk(tm, M("relay.close_force_handoff"), "--no-wait")
    return rc == 0


def close_save_start(n, row):
    """1.155: «Salva e chiudi» su una sessione ferma: la richiesta del passaggio di consegne (cm-closesave.prompt)."""
    rc, _ = relay_talk(row.get("tmux") or row.get("name"), _load("cm-closesave").prompt(), "--no-wait")
    return rc == 0


def close_arm(n, row, mode, by, now):
    tm = row.get("tmux") or row.get("name")
    with _CLOSE_LOCK:
        d = close_armed()
        d[tm] = {"name": n, "mode": mode, "at": int(now), "by": by or None, "session_id": row.get("session_id") or "",
                 "deadline": int(now) + CLOSE_FORCE_S if mode in ("force", "save") else None}
        write_json(close_armed_path(), d)


def close_sessions(cmd, arg, session=""):
    """1.84 (24/10, B4 e ogni chiusura dall'app): op close — chiude solo le sessioni chiudibili, rifacendo i controlli
    adesso; prima di chiuderne una ne salva lo stato (esito, prossimo passo, file non committati, session_id, ora).
    Risponde con chi e' chiusa e chi no, col motivo. ok se almeno una e' chiusa.
    1.120 (04/11, supervisor-chiudi-esito-04-11, il maintainer: «l'invio a vuoto di chiudi quando la sessione è senza finestra»):
    `when` — now (o assente) come prima; after = una sessione occupata (al lavoro, con una domanda, con un lavoro in
    sottofondo) si chiude appena si ferma; force = «Chiudi comunque», confermato nell'app: ferma il turno e il sottofondo,
    chiede un breve passaggio di consegne e chiude a fine turno o dopo CLOSE_FORCE_S. ok anche se e' armata; il testo
    dice sempre l'esito, sessione per sessione."""
    when = str(cmd.get("when") or "now")
    if when not in CLOSE_WHEN:
        return False, M("relay.cmd_close_bad_when", when=S.one_line(when)[:20])
    # 1.155 (19/11, supervisor-chiudi-salva-19-11; l'utente alle 15:16: «Salva e chiudi» e «Chiudi senza salvare»): save
    # true = prima il passaggio di consegne (la sessione lo scrive se serve), poi la chiusura a fine di quel turno o alla
    # scadenza; una sessione occupata lo riceve appena si ferma. false o assente: come prima, closed.handoff false
    save = cmd.get("save") is True
    names = close_names(arg, session)
    if not names:
        return False, M("relay.cmd_close_none")
    rows = _json_cmd("sessions", "--json") or []
    done, armed, kept, now = [], [], [], time.time()
    for n in names:
        chk, row = close_check(n, rows)
        if save and chk["blocked_by"] not in ("gone", "attached") and row is not None and row.get("session_id"):
            if chk["closable"] and not close_save_start(n, row):
                kept.append(M("relay.cmd_close_kept", name=n, why=M("relay.close_why_force_failed")))
                continue
            close_arm(n, row, "save" if chk["closable"] else "save_wait", cmd.get("by"), now)
            armed.append(M("relay.cmd_close_saving", name=n, s=CLOSE_FORCE_S))
            continue
        if chk["closable"]:
            why = close_one(n, row, chk, cmd.get("by"))
            (kept.append(M("relay.cmd_close_kept", name=n, why=why)) if why else done.append(n))
            continue
        if when == "now" or chk["blocked_by"] in ("gone", "attached"):
            kept.append(M("relay.cmd_close_kept", name=n, why=M(f"relay.close_why_{chk['blocked_by']}")))
            continue
        if when == "force" and not close_force_start(n, row, chk, cmd.get("by")):
            kept.append(M("relay.cmd_close_kept", name=n, why=M("relay.close_why_force_failed")))
            continue
        close_arm(n, row, when, cmd.get("by"), now)
        armed.append(M("relay.cmd_close_armed" if when == "after" else "relay.cmd_close_forcing", name=n, s=CLOSE_FORCE_S))
    if done or armed:
        push_async()
    lines = ([M("relay.cmd_close_done", names=", ".join(done))] if done else []) + armed + kept
    return bool(done or armed), "\n".join(lines)


def close_tick(now=None):
    """1.120: le chiusure armate — dopo ogni push del demone (almeno una al minuto). after: si chiude appena close_check la
    trova chiudibile. force: a fine del turno del passaggio di consegne (un `stop` nel ledger dopo l'armo), o alla
    scadenza anche occupata (prima Esc). Una sessione che non c'e' piu', o armata da piu' di un giorno, esce."""
    if not _CLOSE_LOCK.acquire(blocking=False):
        return
    try:
        armed = close_armed()
        if not armed:
            return
        now = now or time.time()
        rows = _json_cmd("sessions", "--json") or []
        ledger = None
        changed = False
        for tm, a in list(armed.items()):
            n = a.get("name") or tm
            chk, row = close_check(n, rows)
            if row is None or chk["blocked_by"] == "gone" or now - float(a.get("at") or 0) > CLOSE_ARMED_MAX_S:
                armed.pop(tm); changed = True
                continue
            go = chk["closable"]
            if a.get("mode") == "save_wait":   # 1.155: occupata quando si e' chiesto di salvare; adesso e' ferma
                if go and close_save_start(n, row):
                    armed[tm] = dict(a, mode="save", at=int(now), deadline=int(now) + CLOSE_FORCE_S); changed = True
                continue
            if a.get("mode") in ("force", "save"):
                if ledger is None:
                    ledger = core.ledger_rows()
                ended = any(x.get("event") == "stop" and x.get("session_id") == a.get("session_id")
                            and S.epoch(x.get("ts") or "") >= float(a.get("at") or 0) for x in ledger) if a.get("session_id") else False
                late = now >= float(a.get("deadline") or 0)
                if late and not go and chk["blocked_by"] in ("busy", "question", "background"):
                    run_cm("interrupt", tm, "--wait", "10")
                    kill_background(row, background_pids(row))
                    chk = dict(chk, closable=True, blocked_by=None, save=chk.get("save") or {"outcome": None, "next": None,
                               "session_id": row.get("session_id") or None, "dirty_n": None, "dirty": None})
                    go = chk["blocked_by"] is None
                go = go and (ended or late)
            if not go:
                continue
            hand = None
            if a.get("mode") == "save":   # 1.155: il passaggio di consegne scritto dopo la richiesta; scaduto: lo si dice
                f = _load("cm-closesave").handoff_since(row.get("cwd"), float(a.get("at") or 0))
                hand = {"file": f, "reason": None if ended else M("close.reason_expired")}
                armed[tm] = dict(a, mode="exit"); write_json(close_armed_path(), armed); push_async()   # «Chiudo»
            why = close_one(n, row, chk, a.get("by"), armed=a.get("mode"), handoff=hand)
            if why:
                log(f"chiusura armata di {n} non riuscita: {why[:160]}")
            armed.pop(tm); changed = True
        if changed:
            write_json(close_armed_path(), armed)
            push_async()
    finally:
        _CLOSE_LOCK.release()


def closing_of(rows):
    """1.120: {nome dello stato: {mode, at, deadline}} delle chiusure armate, per state.sessions[].closing."""
    name = lambda r: S.short_name(r.get("name") or r.get("tmux") or "", prefixes())  # noqa: E731 — come build_session
    by_tm = {(r.get("tmux") or r.get("name")): name(r) for r in rows or []}
    out = {by_tm.get(tm, a.get("name") or tm): {"mode": "save" if a.get("mode") == "save_wait" else a.get("mode"), "at": a.get("at"),
                                                 "deadline": a.get("deadline")}
           for tm, a in close_armed().items()}
    try:   # 1.155: `supervisor close` dal PC, mentre salva e mentre chiude
        for tm, c in _load("cm-closesave").closing_all().items():
            out.setdefault(by_tm.get(tm, tm), c)
    except Exception:   # noqa: BLE001 — un'informazione in piu': mai far cadere la push
        pass
    return out


def sys_kill(cmd, arg):
    """1.90 (28/10): op sys_kill {arg: "pid:procStart"} — il pulsante «Chiudi» di un consiglio di `system`. Rifa' la misura
    adesso e chiude solo un processo che e' ancora fra i consigli con questa azione (un orfano, uno schema ammesso senza
    padre): mai un pid scritto a mano, mai uno riusato. HUP, TERM, KILL come gli orfani di cm-sessions."""
    arg = str(arg or "").strip()
    st = system_status(_json_cmd("sessions", "--json", "--no-screen") or [], {}, fresh=True)
    adv = next((a for a in (st or {}).get("advice") or [] if (a.get("action") or {}).get("op") == "sys_kill" and a["action"].get("arg") == arg), None)
    if not adv:
        return False, M("relay.cmd_sys_kill_gone", arg=arg or "-")
    pid, _, start = arg.partition(":")
    ok = _load("cm-sessions").close_orphan({"pid": int(pid), "proc_start": start}, float((CFG.get("sessions") or {}).get("orphan_wait_s", 3)))
    ledger_write("sys-kill", pid=int(pid), kind=adv["kind"], text=adv["text"], by=cmd.get("by"), ok=ok)
    push_async()
    return ok, M("relay.cmd_sys_kill_done" if ok else "relay.cmd_sys_kill_failed", pid=pid)


def sys_unit_stop(cmd, arg):
    """1.91 (28/10): op sys_unit_stop {arg: "unita1,unita2"} — «Ferma il ciclo»: le unita' systemd --user che lasciano
    processi bloccati sul ponte grafico. Rifa' la misura e ferma (`systemctl --user stop --no-block`) solo se le stesse
    unita' sono ancora nel consiglio stuck_loop, e solo quelle dell'elenco ammesso (system.stuck_units). Mai un riavvio di
    Linux: quello resta un testo."""
    arg = ",".join(u.strip() for u in str(arg or "").split(",") if u.strip())
    st = system_status(_json_cmd("sessions", "--json", "--no-screen") or [], {}, fresh=True)
    adv = next((a for a in (st or {}).get("advice") or [] if (a.get("action") or {}).get("op") == "sys_unit_stop" and a["action"].get("arg") == arg), None)
    if not adv:
        return False, M("relay.cmd_sys_unit_stop_gone", arg=arg or "-")
    units = arg.split(",")
    ok = SY.stop_units(units)
    ledger_write("sys-unit-stop", units=units, by=cmd.get("by"), ok=ok)
    push_async()
    return ok, M("relay.cmd_sys_unit_stop_done" if ok else "relay.cmd_sys_unit_stop_failed", units=", ".join(units))


def plugins_op(op, arg):
    """1.95 (29/10): plugins (lettura: l'elenco intero), plugin_check (controlla subito i marketplace, la risposta arriva
    con la push dopo), plugin_update {arg: "account:id"}, plugin_update_all {arg: account}. Mai -y (cm-plugins)."""
    arg = str(arg or "").strip()
    if op == "plugins":
        return True, json.dumps(_plugins().collect(), ensure_ascii=False)
    if op == "plugin_check":
        plugins_check_spawn(force=True)
        return True, M("relay.cmd_plugins_checking")
    if op == "plugin_update":
        acc, _, pid = arg.partition(":")
        ok, text = _plugins().update(acc, pid)
    else:
        ok, text = _plugins().update_all(arg)
    plugins_summary(fresh=True)
    push_async()
    return ok, text


def state_alias(rows=None):
    """04/11 (supervisor-approva-sessione-win-04-11, il maintainer: «perché la sua richiesta di approvazione non appare qui?»):
    {nome del registro → nome della sessione viva in state.sessions[].name}. Un compito dice la sua sessione col nome di
    quando e' nato («supervisor-app»), ma la sessione viva puo' chiamarsi «win:supervisor-app» (su win) o «pix-x» (nello
    stato «x»): l'app abbina gli ok alla scheda per nome e non li mostrava. Dalle righe vive dell'ultima push: il nome
    esatto di una sessione viva resta suo; un alias (tmux, nome corto, nome dopo «host:», remote_name) vale se e' di una
    sola sessione. 16/11 (supervisor-riall-1-dati-16-11): `rows`, le righe della push in corso; senza, quelle dell'ultima."""
    if rows is None:
        with _LIVE_LOCK:
            rows = list(_LIVE["rows"] or [])
    rows = [r for r in rows if r.get("status") != "dead"]
    name = lambda r: S.short_name(r.get("name") or r.get("tmux") or "", prefixes())  # noqa: E731 — come build_session
    exact = {name(r) for r in rows}
    alias = {}
    for r in rows:
        n = name(r)
        keys = {r.get("tmux") or "", r.get("name") or "", str(r.get("name") or "").split(":", 1)[-1], r.get("remote_name") or ""}
        keys |= {S.short_name(k, prefixes()) for k in list(keys)}
        for k in keys - {"", n}:
            alias.setdefault(k, set()).add(n)
    return {k: next(iter(v)) for k, v in alias.items() if len(v) == 1 and k not in exact}


ATTESE_CLOSED_S, ATTESE_CLOSED_MAX = 600, 10   # 1.151: quanto resta un id chiuso in state.attese.closed, e quanti
_ATTESE = {"mod": None, "reg": None}


def attese_mod():
    """cm-attese caricato una volta: la push lo usa a ogni giro."""
    if _ATTESE["mod"] is None:
        _ATTESE["mod"] = _load("cm-attese")
    return _ATTESE["mod"]


def attese_view(full, now, rows=None, write=True):
    """16/11/2026 (supervisor-riall-1-dati-16-11, contratto 1.151: la direzione A dell'analisi di supervisor-app, scelta
    dal manutentore): le attese sono la fonte unica di ogni voce per te, e si contano qui, sullo stato intero appena
    costruito. Da qui state.attese {rev, n, closed}, state.tocca e sessions[].tocca: il numero sono le righe. Una voce
    che c'era alla push di prima e ora non c'e' (chiusa da un dispositivo, al PC, dalla sessione o da sola) sta in closed
    per ATTESE_CLOSED_S, cosi' ogni dispositivo la toglie subito. Un registro che non si legge non chiude niente: valgono
    le sue voci dell'ultima lettura buona (senza una lettura buona, niente). La lista va in attese.json: l'op attese la
    rida' uguale."""
    AT = attese_mod()
    sd = Path(cm.expand(CFG["state_dir"]))
    try:
        reg = _memo("attese_registry", lambda: AT.registry_items(int(now)), ttl=60,
                    deps=[tasks_version, str(sd / "needs-you"), str(sd / "login-expiry.json")])
        _ATTESE["reg"] = copy.deepcopy(reg)
    except Exception as e:   # noqa: BLE001 — sqlite occupato, un registro rotto: le voci dell'ultima lettura buona
        log(f"attese: registro non letto ({e})")
        if _ATTESE["reg"] is None:
            return None
        reg = copy.deepcopy(_ATTESE["reg"])
    items = AT.collect(int(now), state=full, alias=state_alias(rows), registry=reg)
    try:
        held = _memo("attese_held", AT.held_tasks, ttl=60, deps=[tasks_version])
    except Exception as e:   # noqa: BLE001 — senza pause lette nessuna carta e' tenuta fuori
        log(f"attese: pause non lette ({e})")
        held = set()
    card_attention(full, items, held, now)
    rev = S.attese_rev(items)
    ids = [a["id"] for a in items]
    cur = set(ids)
    p = rdir() / "attese-state.json"
    prev = read_json(p, {}) or {}
    closed = [c for c in (prev.get("closed") or []) if isinstance(c, dict) and c.get("id") not in cur and now - float(c.get("at") or 0) < ATTESE_CLOSED_S]
    had = {c["id"] for c in closed}
    closed = (closed + [{"id": i, "at": int(now)} for i in prev.get("ids") or [] if i not in cur and i not in had])[-ATTESE_CLOSED_MAX:]
    if write and (prev.get("rev") != rev or prev.get("closed") != closed or prev.get("ids") != ids or not (rdir() / "attese.json").exists()):
        write_json(p, {"rev": rev, "ids": ids, "closed": closed, "at": int(now)})
        write_json(rdir() / "attese.json", {"at": int(now), "rev": rev, "items": items})
    S.link_steps(full.get("sessions"), cur)   # 1.152: via i Prossimi delle voci chiuse
    tot, by = S.tocca_of(items)
    full["attese"] = {"rev": rev, "n": len(items), "closed": closed}
    full["tocca"] = dict(tot, today=(full.get("tocca") or {}).get("today") or {"n": 0, "items": []})
    for s in full.get("sessions") or []:
        c, old = by.get(s.get("name")) or {}, s.get("tocca") or {}
        tc = {"prove": c.get("prove", 0), "decidi": c.get("decidi", 0), "rispondi": c.get("rispondi", 0),
              "guarda": int(old.get("guarda") or 0), "segui": int(old.get("segui") or 0)}   # guarda e segui: le loro fonti
        if any(tc.values()):
            s["tocca"] = tc
        else:
            s.pop("tocca", None)
    return items


CARD_TITLE_MAX = 80   # 1.160: il titolo di una voce «card» di attention (l'intero sta nella voce di attese)


def card_attention(full, items, held, now):
    """1.160 (23/11/2026, supervisor-promemoria-carte-23-11: una carta di app-silenzio-assenso-23-11 e' rimasta aperta
    cinque ore senza un promemoria, perche' «Serve te» aveva solo domande, turni falliti, quote e compiti rossi). Le carte
    di ok, di scelta e di prova di un compito non in pausa entrano in state.attention come kind «card» {session, ref = wid
    = l'id dell'attesa, task, card_kind ok|choice|prova, title, since, closes answer}, e la loro voce di attese ha
    blocking: true. Si chiudono quando la carta ha risposta, da qualsiasi via: la voce di attese esce, e con lei questa."""
    cards = AT_blocking(items, held)
    att = [x for x in full.get("attention") or [] if x.get("kind") != "card"]
    for a in cards:
        a["blocking"] = True
        att.append({"kind": "card", "session": a.get("session"), "ref": a["id"], "wid": a["id"], "task": a["task"], "card_kind": a["kind"],
                    "title": (a.get("title") or "")[:CARD_TITLE_MAX], "since": int(a.get("at") or now), "closes": "answer"})
    if att:
        full["attention"] = sorted(att, key=lambda x: (int(x.get("since") or 0), x.get("session") or ""))
    else:
        full.pop("attention", None)


def AT_blocking(items, held):
    return attese_mod().blocking_cards(items, held)


def commitments_list():
    """1.76 (22/10, P2): gli impegni del registro (`task impegni --json`: approvati non compiuti, e chi aspetta un ok)
    per /state.commitments, con `project` relativo come sessions[].project; fermi prima, al massimo COMMITMENTS_MAX."""
    root = cm.expand(CFG["workspace"]["root"])
    out, alias = [], state_alias()
    for x in _memo("impegni", lambda: _json_cmd("task", "impegni", "--json") or [], deps=[tasks_version]):
        if not isinstance(x, dict) or not x.get("task"):
            continue
        y = dict(x)
        y["project"] = S.project_of(x.get("project") or "", root)
        y["title"] = S.one_line(str(x.get("title") or ""))[:200]
        y["short"] = short_title(x.get("short") or y["title"])   # 1.79: one line in the app, never «…»
        if y.get("session"):   # 1.119: il nome della sessione viva, come state.sessions[].name
            y["session"] = alias.get(y["session"], S.short_name(str(y["session"]), prefixes()))
        out.append(y)
    return out[:S.COMMITMENTS_MAX]


