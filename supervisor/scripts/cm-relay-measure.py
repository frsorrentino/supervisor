# Part of cm-relay.py: the measurements (stats of push, commands and arrivals, phase 0) and the commands that read them.
# Not a module of its own: cm-relay.py executes this file in its own namespace, at this point of its body, so the
# names here share globals with cm-relay.py (the tests patch them there). A comment, not a docstring: a docstring
# here would become cm-relay.py's __doc__. Split out on 24/11/2026 so that no file of the plugin is over the
# 256 KiB the plugin directory reads (supervisor-relay-256kb-24-11); the code is unchanged.

# ------------------------------------------------------------------ misure (fase 0, 07/10)
_NUM = r"(-?[0-9]+,[0-9])"
STATS_PUSH = re.compile(r"^(\S+) push: \d+ sessioni, (\d+) eventi · origine (\S+)(?: · uguale, non ripubblicato)? · attesa " + _NUM + r" s · lock " + _NUM
                        + r" s · raccolta " + _NUM + r" s · pubblicazione " + _NUM + r" s · contorno " + _NUM
                        + r" s · ts→put " + _NUM + r" s · cpu " + _NUM + r"\+" + _NUM + r" s")
STATS_CMD = re.compile(r"^(\S+) cmd \S+: (\S+) .*· fila " + _NUM + r" s · esecuzione " + _NUM + r" s")
STATS_DONE = re.compile(r"^(\S+) cmd \S+: esito scritto · (\S+) · dall'arrivo " + _NUM + r" s")
STATS_ARRIVAL = re.compile(r"^(\S+) arrivo: \S+ " + _NUM + r" s dopo la push")


def _num(x):
    return float(x.replace(",", "."))


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else 0.0


def stats(rest):
    """`relay stats [--since T] [--until T]`: p50, p95 e massimo delle misure del log (push, eta' dello stato, comandi).
    T = HH:MM di oggi o un istante ISO (2026-10-07T16:20). Solo le righe con le misure (dalla fase 0 in poi)."""
    def bound(flag):
        if flag not in rest or rest.index(flag) + 1 >= len(rest):
            return None
        v = rest[rest.index(flag) + 1]
        return time.strftime("%Y-%m-%dT") + v if re.fullmatch(r"\d{1,2}:\d{2}", v) and len(v) == 5 else (
            time.strftime("%Y-%m-%dT") + "0" + v if re.fullmatch(r"\d:\d{2}", v) else v)
    since, until = bound("--since"), bound("--until")
    lines = []
    for p in (log_path().with_name(log_path().name + ".1"), log_path()):
        try:
            lines += p.read_text(errors="replace").splitlines()
        except OSError:
            pass
    keep = lambda ts: (since is None or ts >= since) and (until is None or ts < until)  # noqa: E731
    push, cmds, done, arr = [], {}, {}, []
    for l in lines:
        if (mm := STATS_PUSH.match(l)) and keep(mm.group(1)):
            g = [_num(x) for x in mm.groups()[3:]]
            push.append({"ts": mm.group(1), "origin": mm.group(3), "wait": g[0], "lock": g[1], "dur": g[1] + g[2] + g[3] + g[4],
                         "collect": g[2], "age": g[5], "cpu": g[6] + g[7]})
        elif (mm := STATS_DONE.match(l)) and keep(mm.group(1)):
            done.setdefault(mm.group(2), []).append(_num(mm.group(3)))
        elif (mm := STATS_CMD.match(l)) and keep(mm.group(1)):
            cmds.setdefault(mm.group(2), []).append((_num(mm.group(3)), _num(mm.group(4))))
        elif (mm := STATS_ARRIVAL.match(l)) and keep(mm.group(1)):
            arr.append(_num(mm.group(2)))
    row = lambda name, xs: f"  {name:<34} n {len(xs):>5}   p50 {_pct(xs, .5):7.1f}   p95 {_pct(xs, .95):7.1f}   max {max(xs) if xs else 0:7.1f}"  # noqa: E731
    out = [f"relay stats {since or 'inizio'} → {until or 'ora'}", "push"]
    if push:
        mins = {p["ts"][:16] for p in push}
        out.append(f"  {'push':<34} n {len(push):>5}   al minuto {len(push) / max(1, len(mins)):.2f} (minuti con push {len(mins)})")
        out += [row("durata (lock compreso) s", [p["dur"] for p in push]), row("attesa sul lock s", [p["lock"] for p in push]),
                row("attesa dalla richiesta s", [p["wait"] for p in push]), row("raccolta s", [p["collect"] for p in push]),
                row("cpu (propria + figli) s", [p["cpu"] for p in push]), row("ts → put (età alla partenza) s", [p["age"] for p in push])]
        orig = {}
        for p in push:
            orig[p["origin"]] = orig.get(p["origin"], 0) + 1
        out.append("  origine: " + ", ".join(f"{k} {v}" for k, v in sorted(orig.items())))
    else:
        out.append("  nessuna riga di misura")
    out += ["stato all'arrivo (stima da /seen)", row("dopo la push s", arr)]
    out.append("comandi")
    for op in sorted(set(cmds) | set(done)):
        xs = cmds.get(op, [])
        out.append(row(f"{op}: fila s", [x[0] for x in xs]))
        out.append(row(f"{op}: esecuzione s", [x[1] for x in xs]))
        if op in done:
            out.append(row(f"{op}: dall'arrivo all'esito s", done[op]))
    print("\n".join(out))
    return 0


def other_account(acc):
    """1.58: l'altro account configurato, o None se ce n'e' uno solo (o piu' di due: quale non si indovina)."""
    others = [a for a in CFG["accounts"] if a != acc]
    return others[0] if len(others) == 1 else None


def quota_window(acc):
    """1.58: (finestra, reset) del limite toccato da `acc`: «5h» se le cinque ore sono piene, altrimenti «week»."""
    q = (_json_cmd("quota", "--json", expect="{") or {}).get(acc) or {}
    if float(q.get("cinque_ore_pct") or 0) >= 99 or float(q.get("settimana_pct") or 0) < 99:
        return "5h", q.get("reset_cinque_ore")
    return "week", q.get("reset_settimanale")


# 1.85 (25/10/2026): il limite di un modello. Claude Code lo scrive nella trascrizione come messaggio <synthetic>
# («You've reached your Fable limit. Run /usage-credits to continue or switch models with /model.»); il payload di
# StopFailure porta solo il 429 generico («would exceed your account's rate limit»). Il nome comincia con la maiuscola:
# «reached your account's …» non e' un modello.
# 16/11/2026 (supervisor-blocco-permanente-16-11): «hit your Fable limit» della 2.1.323 e il giorno del reset («resets Nov 19,
# 3am (Europe/Rome)»): il lettore e' cm-relay-state.limit_info, lo stesso di talk
LIMIT_MODEL = S.LIMIT_MODEL_RE
OTHER_MODEL_ID, OTHER_MODEL_LABEL = "claude-opus-5-5[1m]", "Opus 5.5"


def other_model():
    """1.85: (id, etichetta) del modello su cui proseguire al limite di un altro: Opus dalla lista di tune.models."""
    m = next((m for m in ((CFG.get("tune") or {}).get("models") or []) if str(m.get("id") or "").startswith("claude-opus")), None)
    return (m["id"], m.get("label") or OTHER_MODEL_LABEL) if m else (OTHER_MODEL_ID, OTHER_MODEL_LABEL)


def reset_from_text(text, now=None):
    """1.85: l'istante del reset scritto nel messaggio del limite («resets 3:10pm», «resets at 23:10», dal 16/11 anche
    «resets Nov 19, 3am (Europe/Rome)»), il prossimo nel futuro; None se il messaggio non dice l'ora."""
    return S.limit_info(text, now)["at"]


def until_text(reset, now=None):
    """16/11/2026 (supervisor-blocco-permanente-16-11, il titolo chiaro chiesto dalla master): « fino alle 15:10» oggi,
    « fino al 19/11 alle 3» un altro giorno, "" senza l'ora; nell'ora di qui."""
    if not reset:
        return ""
    lt, nt = time.localtime(float(reset)), time.localtime(now or time.time())
    hm = f"{lt.tm_hour}:{lt.tm_min:02d}"
    t = str(lt.tm_hour) if not lt.tm_min else hm
    if (lt.tm_year, lt.tm_yday) == (nt.tm_year, nt.tm_yday):
        return M("relay.until_time", t=t, hm=hm)
    return M("relay.until_day", d=lt.tm_mday, m=lt.tm_mon, t=t, hm=hm)


def limit_text(text, transcript="", session_id="", tries=3, pause=1.0):
    """1.85: il testo del limite. Dal testo dell'hook se dice gia' il modello; altrimenti dall'ultimo messaggio
    dell'assistente nella trascrizione (`transcript`, o cercata per session_id), se e' <synthetic> e recente: l'hook
    puo' arrivare prima che la riga sia sul disco, quindi fino a `tries` letture."""
    if LIMIT_MODEL.search(text or ""):
        return text
    path = transcript
    if not path and session_id:
        hits = glob.glob(os.path.expanduser(f"~/.claude*/projects/*/{session_id}.jsonl"))
        path = hits[0] if hits else ""
    if not path:
        return text or ""
    for i in range(max(1, tries)):
        last = None
        try:
            with open(path, "rb") as f:
                f.seek(max(0, os.path.getsize(path) - 256 * 1024))
                for raw in f.read().decode("utf-8", "replace").splitlines():
                    if '"assistant"' not in raw:
                        continue
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    if d.get("type") == "assistant":
                        last = d
        except OSError:
            return text or ""
        msg = (last or {}).get("message") or {}
        if msg.get("model") == "<synthetic>":
            t = " ".join(str(x.get("text") or "") for x in (msg.get("content") or []) if isinstance(x, dict))
            try:
                age = time.time() - dt.datetime.fromisoformat(str(last.get("timestamp")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                age = 0
            if LIMIT_MODEL.search(t) and age < 600:
                return t
        if i + 1 < tries:
            time.sleep(pause)
    return text or ""


def quota_limit(tm, text="", transcript="", resets_at=None):
    """1.58 (14/10, dagli spunti della valutazione di Kaya): la sessione `tm` ha toccato il limite della quota (l'hook di
    StopFailure). Un evento quota_limit al telefono con l'ora del reset e le strade, una per tasto, che l'op
    quota_route esegue: other_account (handoff sull'altro account), at_reset (riprende da sola al reset).
    1.85 (25/10/2026): il limite di un modello («reached your Fable limit», dal testo dell'hook o dalla trascrizione)
    → window «model», other_model per primo (Opus, se il modello fermo non e' gia' Opus), at_reset solo se il messaggio
    dice l'ora; «pass» non si propone piu' (inutile al limite della quota, detto dal telefono). Uno per sessione e finestra di reset.
    16/11/2026 (supervisor-blocco-permanente-16-11): il testo della 2.1.323 («You've hit your Fable limit · resets Nov 19,
    3am (Europe/Rome)»), letto col giorno e il fuso; `resets_at` (epoch, il quotaLimits della voce) quando il testo non
    dice l'ora; anche per una sessione di win (hosts poll), senza «altro account»; il titolo in parole semplici («X è
    ferma: limite di Fable fino al 19/11 alle 3»); la voce di «Serve te» porta strade, modello e reset (l'attesa
    «limite») e resta finche' il blocco c'e' (attention_open)."""
    row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
    if not row:
        return 0
    acc = row.get("account") or ""
    remote = bool(row.get("host")) and row.get("host") != "local"
    info = S.limit_info(limit_text(text, transcript, row.get("session_id") or ""))
    model = info["model"]
    try:
        given = int(float(resets_at)) if resets_at else None
    except (TypeError, ValueError):
        given = None
    if model:
        window, reset = "model", info["at"] or given
        key = f"model:{model}:{reset or time.strftime('%Y-%m-%d %H')}"
    else:
        window, qreset = quota_window(acc)
        reset = info["at"] or given or qreset
        key = str(reset)
    p = rdir() / "quota-limits.json"
    seen = read_json(p, {})
    seen = seen if isinstance(seen, dict) else {}
    prev = seen.get(tm)
    if (prev.get("key") if isinstance(prev, dict) else prev) == key:
        return 0
    # 16/11: col reset, che «riprendi al reset» usa per il limite di un modello (prima: sempre quello della quota)
    seen[tm] = {"key": key, "reset": int(reset) if reset else None, "window": window, "model": model}
    write_json(p, seen)
    other = None if remote else other_account(acc)   # il passaggio a un altro account parte da qui: non per una sessione di win
    alt = other_model() if model and not model.lower().startswith("opus") else None
    name = S.short_name(tm, prefixes())
    until = until_text(reset)
    if model:
        title = M("relay.event_quota_model", name=name, model=model, until=until)
        actions = (["other_model"] if alt else []) + (["other_account"] if other else []) + (["at_reset"] if reset else [])
    else:
        title = M("relay.event_quota_5h" if window == "5h" else "relay.event_quota_week", name=name, until=until)
        actions = (["other_account"] if other else []) + ["at_reset"]
    attention_mark("quota_limit", name, name, title,
                   extra={"actions": actions, "other_model": alt[1] if alt else None, "reset_at": int(reset) if reset else None, "window": window})
    emit("quota_limit", title, "", ref=name, account=acc, session=name,
         extra={"reset_at": int(reset) if reset else None, "window": window, "other_account": other,
                "other_model": alt[1] if alt else None, "actions": actions})
    # 14/10 (cambio automatico al limite): di notte, per un account in auto_switch.accounts, la strada «altro account»
    # parte da sola; di giorno resta la scelta col tasto
    A = CFG.get("auto_switch") or {}
    lo, hi = (list(A.get("night_hours") or [0, 7]) + [0, 7])[:2]
    if other and acc in (A.get("accounts") or []) and int(lo) <= time.localtime().tm_hour < int(hi):
        ok, text = quota_route({}, name, tm, "other_account")[:2]
        log(f"quota_limit {name}: notte, altro account in automatico → {'ok' if ok else 'ERR'} {text}")
        emit("quota_limit", M("relay.event_quota_auto", name=name, text=text), "", ref=name, account=acc, session=name)
    return 0


NEEDS_CHECK_EVERY_S = 60     # 1.80: la condizione di una richiesta si prova al massimo una volta al minuto
NEEDS_CHECK_TIMEOUT_S = 10
NEEDS_ORPHAN_S = 86400       # il file di una sessione sparita da piu' di un giorno va via
NEEDS_START_GONE_S = 300     # 19/11: la richiesta d'avvio di una sessione che non c'e' piu' (chiusa senza partire) va via dopo 5 minuti


def _wake(tm, text):
    """Un `talk` staccato alla sessione (il bus non aspetta; chiusa → resta nella casella), come dopo «Approva»."""
    try:
        subprocess.Popen([CM_BIN, "talk", tm, text, "--no-wait"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def needs_check(cmd, cwd=None):
    """Il comando di `needs-you --until`: 0 = fatto. Mai piu' di NEEDS_CHECK_TIMEOUT_S; un errore vale «non ancora»."""
    try:
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        return subprocess.run(cmd, shell=True, cwd=cwd or None, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=NEEDS_CHECK_TIMEOUT_S, env=env).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def task_oks():
    """{compito: istante dell'ultimo ok di una persona} dal registro, in sola lettura; {} senza registro."""
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{_tasks_db_files()[0]}?mode=ro", uri=True, timeout=2)
        try:
            return {t: at for t, at in con.execute(
                "SELECT task, MAX(at) FROM approvals WHERE by_ IS NOT NULL AND (what IS NULL OR what != 'confirmed') GROUP BY task")}
        finally:
            con.close()
    except Exception:   # noqa: BLE001 — senza registro nessun ok: la richiesta resta
        return {}


def needs_you_of(rows, now=None):
    """1.68: {tmux: {reason, at, …}} dai file <state_dir>/needs-you/<session_id>.json che scrive `supervisor needs-you`
    (l'hook del prompt dopo li toglie). 1.80 (23/10/2026, «le richieste in attesa … scompaiano quando non ci sono
    piu'»): qui la vita della richiesta — scaduta (`expires_at`) → via, diario e avviso alla sessione; con `until` il
    comando si prova una volta al minuto e a 0 → via, diario e avviso; il file di una sessione che non c'e' piu' da
    un giorno → via."""
    d = Path(cm.expand(CFG["state_dir"])) / "needs-you"
    if not d.is_dir():
        return {}
    now = float(now or time.time())
    by_sid = {r.get("session_id"): (r.get("tmux") or r.get("name")) for r in rows if r.get("session_id") and r.get("status") != "dead"}
    row_by_sid = {r.get("session_id"): r for r in rows if r.get("session_id") and r.get("status") != "dead"}
    out = {}
    oks = None
    by_tmux = {}
    for r in rows:
        if r.get("status") != "dead" and r.get("tmux"):
            by_tmux.setdefault(r["tmux"], r)
    for f in d.glob("*.json"):
        if f.stem.startswith("start-"):
            # 19/11/2026 (supervisor-avvio-domanda-19-11): un avvio senza mani fermo su una domanda per la persona. La
            # sessione non ha ancora un session_id: si lega per nome tmux; partita (session_id), la richiesta si chiude
            rec = read_json(f, {})
            if not isinstance(rec, dict):
                continue
            tm = str(rec.get("session") or "")
            r = by_tmux.get(tm)
            try:
                old = now - f.stat().st_mtime
            except OSError:
                continue
            if r is not None and r.get("session_id"):
                f.unlink(missing_ok=True)
                ledger_write("needs-you-done", session=tm, reason=S.one_line(str(rec.get("reason") or ""))[:200], how="started")
            elif r is None and old > NEEDS_START_GONE_S:
                f.unlink(missing_ok=True)
                ledger_write("needs-you-pruned", file=f.name)
            elif r is not None and not (float(rec.get("expires_at") or 0) and now >= float(rec["expires_at"])):
                out[tm] = rec
            continue
        tm = by_sid.get(f.stem)
        if not tm:
            try:
                if now - f.stat().st_mtime > NEEDS_ORPHAN_S:
                    f.unlink(missing_ok=True)
                    ledger_write("needs-you-pruned", file=f.name)
            except OSError:
                pass
            continue
        rec = read_json(f, {})
        if not isinstance(rec, dict):
            continue
        reason = S.one_line(str(rec.get("reason") or ""))[:200]
        exp = float(rec.get("expires_at") or 0)
        if exp and now >= exp:
            f.unlink(missing_ok=True)
            ledger_write("needs-you-expired", session=tm, reason=reason)
            _wake(tm, M("relay.needs_you_expired", name=tm, reason=reason, when=time.strftime("%H:%M", time.localtime(now))))
            continue
        # 16/11/2026 (supervisor-blocco-permanente-16-11): un blocco letto dalla trascrizione (il login, source «block») va
        # via da solo quando la sessione ha un turno riuscito dopo la voce, senza svegliarla: e' gia' ripartita
        if rec.get("source") == "block":
            st = core.api_state(row_by_sid.get(f.stem))
            if st is not None and not st.get("block") and float(st.get("last_at") or 0) > float(rec.get("at") or 0):
                f.unlink(missing_ok=True)
                ledger_write("needs-you-done", session=tm, reason=reason, how="unblocked")
                continue
        # 17/11/2026 (supervisor-ok-propagazione-17-11): legata a un compito (needs-you --task), la richiesta si chiude
        # con l'ok a quel compito, da dove arrivi (app, inbox, CLI). Prima la spegneva solo «Approva» nell'app, e solo
        # se il nome della sessione nel registro era quello tmux: il 17/11 «ok push del link PDF» e' rimasta aperta
        task = str(rec.get("task") or "")
        if task:
            if oks is None:
                oks = task_oks()
            if float(oks.get(task) or 0) >= float(rec.get("at") or 0) > 0:
                f.unlink(missing_ok=True)
                ledger_write("needs-you-done", session=tm, reason=reason, how="ok", task=task)
                continue
        until = str(rec.get("until") or "")
        if until and now - float(rec.get("checked_at") or 0) >= NEEDS_CHECK_EVERY_S:
            rc = needs_check(until, rec.get("cwd"))
            if rc == 0:
                f.unlink(missing_ok=True)
                ledger_write("needs-you-done", session=tm, reason=reason, how="check")
                _wake(tm, M("relay.needs_you_done", name=tm, reason=reason, when=time.strftime("%H:%M", time.localtime(now))))
                continue
            rec["checked_at"], rec["last_rc"] = int(now), rc
            write_json(f, rec)
        out[tm] = rec
    return out


def attention_done(cmd, arg):
    """1.80 (23/10/2026): il tasto «Fatto» dell'app chiude una voce di «Serve te» che il relay non puo' verificare da
    solo (l'APK installato, un invito accettato, una cosa fatta a voce). {kind, ref (= arg), since}: needs_you → il
    file va via e la sessione riceve «fatto» in casella; turn_blocking → segnata chiusa (attention-dismissed.json,
    sessione → since) finche' il turno non cambia; turn_failed, quota_limit, task_red → tolte da attention.json."""
    kind = str(cmd.get("kind") or "").strip()
    ref = S.one_line(str(arg or cmd.get("ref") or "")).strip()[:120]
    when = time.strftime("%H:%M")
    if kind == "needs_you" and ref:
        rows = _json_cmd("sessions", "--json", "--no-screen") or []
        row = next((r for r in rows if (r.get("tmux") or r.get("name")) == ref and r.get("session_id")), None)
        f = Path(cm.expand(CFG["state_dir"])) / "needs-you" / f"{row['session_id']}.json" if row else None
        start = Path(cm.expand(CFG["state_dir"])) / "needs-you" / f"start-{''.join(c if c.isalnum() or c in '-_.' else '-' for c in ref)}.json"
        if (not f or not f.exists()) and start.exists():   # 19/11 (supervisor-avvio-domanda-19-11): la domanda d'avvio
            reason = S.one_line(str((read_json(start, {}) or {}).get("reason") or ""))[:200]
            start.unlink(missing_ok=True)
            ledger_write("needs-you-done", session=ref, reason=reason, how="app", by=from_device(cmd))
            return True, M("relay.cmd_attention_done", kind=kind, ref=ref)
        if not f or not f.exists():
            return False, M("relay.cmd_attention_unknown", kind=kind, ref=ref)
        reason = S.one_line(str((read_json(f, {}) or {}).get("reason") or ""))[:200]
        f.unlink(missing_ok=True)
        ledger_write("needs-you-done", session=ref, reason=reason, how="app", by=from_device(cmd))
        _wake(ref, M("relay.needs_you_app_done", person=M("tasks.person"), when=when, device=from_device(cmd), reason=reason))
        return True, M("relay.cmd_attention_done", kind=kind, ref=ref)
    if kind == "turn_blocking" and ref:
        p = rdir() / "attention-dismissed.json"
        d = read_json(p, {})
        d = d if isinstance(d, dict) else {}
        d[ref] = int(cmd.get("since") or time.time())
        write_json(p, d)
        ledger_write("attention-done", kind=kind, ref=ref, by=from_device(cmd))
        return True, M("relay.cmd_attention_done", kind=kind, ref=ref)
    if kind in ("turn_failed", "quota_limit", "task_red") and ref:
        attention_mark(kind, ref, ref, "", remove=True)
        ledger_write("attention-done", kind=kind, ref=ref, by=from_device(cmd))
        return True, M("relay.cmd_attention_done", kind=kind, ref=ref)
    return False, M("relay.cmd_attention_unknown", kind=kind or "?", ref=ref or "?")


REMIND_AFTER_S = (600, 1800)   # 1.68: promemoria di una voce di «Serve te» ancora aperta dopo 10 e dopo 30 minuti
# 1.160 (23/11/2026, supervisor-promemoria-carte-23-11): le soglie per tipo; una carta di un compito dopo 1 e dopo 3 ore
REMIND_AFTER_BY_KIND = {"card": (3600, 10800)}


def remind_after(kind):
    return REMIND_AFTER_BY_KIND.get(kind, REMIND_AFTER_S)


def quiet_now(t):
    """24/11 (supervisor-promemoria-notte-24-11): tasks.quiet_hours [da, a), la stessa lettura di cm-guard.quiet_now:
    di notte i promemoria di «Serve te» non suonano (la voce resta in «Serve te»)."""
    q = (CFG.get("tasks") or {}).get("quiet_hours") or []
    if len(q) != 2:
        return False
    h = dt.datetime.fromtimestamp(t).hour
    a, b = int(q[0]), int(q[1])
    return (a <= h < b) if a <= b else (h >= a or h < b)


def attention_reminders(full, now):
    """1.68 (14/10): per ogni voce di state.attention ancora aperta, un evento attention_reminder dopo 10 e dopo 30
    minuti, una volta ciascuno (reminders.json); le voci chiuse escono dal file. 1.160: le soglie per tipo
    (remind_after: una carta dopo 1 e dopo 3 ore) e, per ogni voce che c'era al giro di prima e ora no (chiusa da un
    dispositivo, dal terminale, dalla master o da sola), un evento attention_closed quieto con ref, wid e
    attention_kind: telefono e orologio tolgono la notifica gia' mostrata."""
    items = full.get("attention") or []
    p = rdir() / "reminders.json"
    sent = read_json(p, {})
    live = {}
    quiet = quiet_now(now)
    for x in items:
        key = f"{x.get('kind')}|{x.get('ref')}|{x.get('since')}"
        steps = remind_after(x.get("kind"))
        if key not in sent:
            # vista ora per la prima volta (dopo un riavvio, o una voce nata gia' vecchia): le soglie gia' passate non
            # suonano tutte insieme, contano solo quelle che si passano da qui in poi
            live[key] = [a for a in steps if now - float(x.get("since") or now) >= a]
            continue
        done = list(sent.get(key) or [])
        since = float(x.get("since") or now)
        due = [a for a in steps if a not in done and now - since >= a]
        # 24/11 (supervisor-promemoria-notte-24-11): nelle ore di quiete nessun promemoria e nessuna soglia segnata; una
        # o piu' soglie passate di notte suonano una volta sola, alla prima ora fuori dalla quiete, con l'eta' vera
        if due and not quiet:
            if any(quiet_now(since + a) for a in due):
                rings = [(due, int((now - since) // 60))]   # dopo la notte: una volta, con l'eta' vera
            else:
                rings = [([a], a // 60) for a in due]       # di giorno come prima: una per soglia
            for marks, mins in rings:
                done.extend(marks)
                emit("attention_reminder", M("relay.event_attention_reminder", min=mins, title=S.one_line(x.get("title") or "")[:120]), "",
                     ref=x.get("ref"), session=x.get("session"), extra={"attention_kind": x.get("kind"), "wid": x.get("wid")})
        live[key] = done
    open_now = {(str(x.get("kind")), str(x.get("ref"))) for x in items}
    for key in sent:
        kind, ref, _since = (key.split("|", 2) + ["", ""])[:3]
        if key in live or (kind, ref) in open_now:
            continue
        try:
            wid_ = ref if kind == "card" else S.wid_attention({"kind": kind, "ref": ref, "session": ref, "since": int(_since)})
        except (TypeError, ValueError):
            wid_ = None
        emit("attention_closed", M("relay.event_attention_closed"), "", ref=ref,
             extra={"attention_kind": kind, "wid": wid_, "quiet": True})
    if live != sent:
        write_json(p, live)


def attention_mark(kind, session, ref, title, now=None, remove=False, extra=None):
    """1.66 (14/10): le cose che aspettano la persona oltre alle domande — un turno fallito (1.61), una sessione ferma
    per la quota (1.58), un task rosso (1.57) — restano in attention.json finche' non si risolvono. `remove` toglie
    quella di quel tipo e riferimento (un task tornato verde). 16/11 (supervisor-blocco-permanente-16-11): `extra`, i
    campi in piu' della voce (per quota_limit: actions, other_model, reset_at, window)."""
    p = rdir() / "attention.json"
    items = [x for x in read_json(p, []) if isinstance(x, dict) and not (x.get("kind") == kind and x.get("ref") == ref)]
    if not remove:
        items.append(dict({k: v for k, v in (extra or {}).items() if k not in ("kind", "session", "ref", "title", "since")},
                          kind=kind, session=session, ref=ref, title=S.redact_preview(S.one_line(title))[:200], since=int(now or time.time())))
    write_json(p, items)


def attention_open(rows, ledger):
    """1.66: le voci di attention.json ancora aperte. Un turno fallito e una quota finita si chiudono al primo prompt
    dopo (la sessione e' ripartita) o quando la sessione non c'e' piu'; un task rosso quando torna verde (attention_mark)."""
    p = rdir() / "attention.json"
    items = [x for x in read_json(p, []) if isinstance(x, dict)]
    if not items:
        failures_settle(set())   # anche senza voci: un evento rimasto aperto da prima (16/10, la master)
        return []
    sid_of = {S.short_name(r.get("name") or r.get("tmux") or "", prefixes()): r.get("session_id") or "" for r in rows if r.get("status") != "dead"}
    row_of = {S.short_name(r.get("name") or r.get("tmux") or "", prefixes()): r for r in rows if r.get("status") != "dead"}
    keep = []
    for x in items:
        if x.get("kind") in ("turn_failed", "quota_limit"):
            sid = sid_of.get(x.get("session") or "")
            if not sid:
                continue
            since = int(x.get("since") or 0)
            # 16/11/2026 (supervisor-blocco-permanente-16-11): il limite resta finche' c'e'. Un prompt che riprende il
            # limite (la master che riprova) non lo chiude: lo chiude un turno riuscito dopo la voce, letto dalla
            # trascrizione (per win dallo stato dell'host). Quando non si sa (l'aiutante di win e' di prima) vale il prompt.
            st = core.api_state(row_of.get(x.get("session") or "")) if x.get("kind") == "quota_limit" else None
            if st is not None:
                if not st.get("block") and float(st.get("last_at") or 0) > since:
                    continue
            elif any(e.get("event") == "prompt" and e.get("session_id") == sid and S.epoch(e.get("ts")) > since for e in ledger):
                continue
        keep.append(x)
    if keep != items:
        write_json(p, keep)
    failures_settle({x.get("ref") for x in keep if x.get("kind") == "turn_failed"})
    return keep


def failures_settle(open_refs, now=None):
    """16/10 (dal telefono, «master: turno fallito» con Continua sotto una risposta riuscita, tornato dopo tre
    Continua): l'app tiene aperto il turno fallito finche' turn_started non supera il ts dell'evento, ma turn_started
    e' valorizzato solo a turno in corso (contratto in cm-relay-state): a turno finito torna null e la voce riappare.
    Quando la voce di attention.json si chiude, l'evento turn_failed su /events viene riscritto senza continue_text
    (l'app ignora un turn_failed senza testo) e con `settled_at`. `open_refs` = le sessioni con un turno fallito
    ancora aperto. Si guarda events.json solo quando cambia lui o l'insieme delle voci aperte."""
    p = local_dir() / "events.json"
    try:
        stamp = f"{p.stat().st_mtime_ns}|{','.join(sorted(r for r in open_refs if r))}"
    except OSError:
        return 0
    sp = rdir() / "failures-settled"
    try:
        if sp.read_text() == stamp:
            return 0
    except OSError:
        pass
    now = int(now or time.time())
    done = [dict(e, continue_text=None, settled_at=now) for e in read_json(p, {}).values()
            if e.get("kind") == "turn_failed" and (e.get("continue_text") or "").strip() and e.get("ref") not in open_refs]
    if done:
        k = None if local_only() else key()
        if k:
            try:
                rtdb("PATCH", "events", {e["key"]: C.encrypt(e, k) for e in done}, {"print": "silent"})
            except (urllib.error.URLError, OSError, ValueError) as ex:
                log(f"turn_failed chiusi: PATCH FAILED: {ex}")
                return 0   # si riprova al prossimo stato
        local_events_add(done, now)
        log(f"turn_failed chiusi: {', '.join(sorted({str(e.get('ref')) for e in done}))}")
        stamp = f"{p.stat().st_mtime_ns}|{stamp.split('|', 1)[1]}"
    sp.write_text(stamp)
    return len(done)


INTERRUPT_WAIT_S = 8   # 16/10: quanto lo Stop aspetta che parta il turno di un prompt consegnato da meno di un minuto
TURN_FAILED_GAP_S = 60   # 1.61: un evento per sessione al minuto, anche se Claude Code riprova e fallisce di fila


def turn_failed(tm, error):
    """1.61 (14/10, dall'analisi dei concorrenti): un turno finito in errore (529 overloaded, 500, autenticazione...)
    lasciava la sessione ferma come se avesse finito. Un evento turn_failed al telefono: il motivo in una riga, senza
    segreti (come le anteprime della 1.56), e `continue_text`, il testo che il tasto «continua» manda con l'op prompt.
    La quota ha il suo evento (1.58)."""
    p = rdir() / "turn-failed.json"
    seen = read_json(p, {})
    now = time.time()
    if now - float(seen.get(tm) or 0) < TURN_FAILED_GAP_S:
        return 0
    seen[tm] = now
    write_json(p, {k: v for k, v in seen.items() if now - float(v or 0) < 86400})
    row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None) or {}
    name = S.short_name(tm, prefixes())
    reason = S.redact_preview(S.short_of(error or "?", 160))   # 22/10: anche il testo dell'errore mostrato in chat
    attention_mark("turn_failed", name, name, M("relay.event_turn_failed", name=name, reason=reason))
    emit("turn_failed", M("relay.event_turn_failed", name=name, reason=reason), "", ref=name, account=row.get("account"), session=name,
         extra={"reason": reason, "continue_text": M("relay.continue_text")})
    return 0


PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def project_parents():
    """1.64: le cartelle madri ammesse per un progetto nuovo, quelle dei progetti pubblicati (inventory): una cartella
    creata li' compare subito fra i progetti."""
    return sorted({str(Path(p["path"]).parent) for p in inventory()})


def project_new(cmd, parent, name):
    """1.64 (14/10, dall'analisi dei concorrenti): una cartella nuova dal telefono, mai creata senza conferma (regola 8
    del kernel: mai creare cartelle per un refuso). `arg` = la cartella madre, una di project_parents(); `text` = il
    nome. Senza `confirm: true` non crea niente e risponde ok: false con il percorso completo da mostrare; con
    `confirm: true` crea la cartella e lancia la sessione (account dalla cartella, come launch), con il primo
    messaggio se c'e' `prompt`."""
    name = str(name or "").strip()
    if not PROJECT_NAME.fullmatch(name):
        return False, M("relay.cmd_project_bad_name", name=name or "?")
    real = os.path.realpath(str(parent or ""))
    if real not in {os.path.realpath(x) for x in project_parents()}:
        return False, M("relay.cmd_project_bad_parent", path=str(parent or "?"))
    path = Path(real) / name
    if path.exists():
        return False, M("relay.cmd_project_exists", path=str(path))
    if cmd.get("confirm") is not True:
        return False, M("relay.cmd_project_confirm", path=str(path))
    path.mkdir()
    rc, out = run_cm("launch", str(path), "--window")
    if rc != 0:
        return False, (out.splitlines() or ["launch failed"])[0]
    new = [r.get("tmux") or r.get("name") for r in (_json_cmd("sessions", "--json", "--no-screen") or [])
           if r.get("cwd") and os.path.realpath(r["cwd"]) == str(path)]
    extra = {"session": S.short_name(new[0], prefixes())} if new else {}
    prompt = " ".join(str(cmd.get("prompt") or "").split())
    if prompt and new:
        relay_talk(new[0], prefix_for(cmd, new[0]) + " " + prompt, "--no-wait")
    return True, M("relay.cmd_project_created", path=str(path), account=account_for_path(path)), extra


def quick_handoff(row):
    """1.58: un passaggio di consegne scritto dal relay, senza modello (la sessione ferma per la quota non puo' scriverlo):
    cartella, conversazione, le ultime richieste della persona, l'ultimo messaggio, il «prossimo» del recap. Il file
    in <relay.dir>/handoffs/; torna il percorso."""
    name = S.short_name(row.get("name") or row.get("tmux") or "?", prefixes())
    d = rdir() / "handoffs"
    d.mkdir(parents=True, exist_ok=True)
    asks = []
    path = core.transcript_of(row)
    if path:
        size = os.path.getsize(path)
        asks = [e["text"] for e in core.transcript_entries(path, max(0, size - 2 * 1024 * 1024), chat_only=True) if e["role"] == "user"][-3:]
    nx, _ = next_dated(row.get("cwd")) if row.get("cwd") else ("", None)
    lines = [f"# {M('relay.handoff_title', name=name)}", "", f"- cwd: {row.get('cwd') or '?'}", f"- session_id: {row.get('session_id') or '?'}",
             f"- account: {row.get('account') or '?'}", "", f"## {M('relay.handoff_asks')}", ""]
    lines += [f"- {S.one_line(a)[:600]}" for a in asks] or ["-"]
    lines += ["", f"## {M('relay.handoff_last')}", "", last_message(row, 3000) or "-"]
    if nx:
        lines += ["", f"## {M('relay.handoff_next')}", "", nx]
    f = d / f"{name}-{int(time.time())}.md"
    f.write_text("\n".join(lines) + "\n")
    return f


def quota_route(cmd, session, tm, arg):
    """1.58: una delle strade proposte da quota_limit. arg: «other_model» (1.85) | «other_account» | «at_reset» |
    «pass:<sessione>» (non piu' proposta dalla 1.85, accettata ancora)."""
    a = str(arg or "").strip()
    row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
    if not row:
        return False, M("relay.cmd_not_running", name=session)
    acc = row.get("account") or ""
    if a == "other_model":
        # 1.85: lo stesso percorso di `supervisor model` (ferma al prompt, selettore, conferma nel riquadro), poi «continua»
        mid, label = other_model()
        rc, out = run_cm("model", tm, mid)
        if rc != 0:
            return False, (out.splitlines() or ["model failed"])[0]
        push_async()
        rc, out = relay_talk(tm, prefix_for(cmd, tm) + " " + M("relay.quota_model_prompt"), "--no-wait")
        if rc != 0:
            return False, (out.splitlines() or ["talk failed"])[0]
        return True, M("relay.cmd_quota_model", name=session, model=label)
    if a == "at_reset":
        window, reset = quota_window(acc)
        # 16/11 (supervisor-blocco-permanente-16-11): il limite di un modello si rinnova alla sua ora (19/11 alle 3), non
        # a quella della quota: quota_limit l'ha scritta in quota-limits.json
        lim = read_json(rdir() / "quota-limits.json", {})
        lim = (lim.get(tm) if isinstance(lim, dict) else None) or {}
        if isinstance(lim, dict) and lim.get("window") == "model" and lim.get("reset"):
            reset = lim["reset"]
        if not reset:
            return False, M("relay.cmd_quota_no_reset", account=acc)
        gp = Path(cm.expand(CFG["state_dir"])) / "guard.json"
        g = read_json(gp, {})
        s_ = g.setdefault(acc, {})
        s_["resume_at"] = s_.get("resume_at") or reset
        s_["resume_names"] = sorted(set(s_.get("resume_names") or []) | {tm})
        write_json(gp, g)
        return True, M("relay.cmd_resume_at_reset", name=session, t=time.strftime("%H:%M", time.localtime(float(reset))))
    if a == "other_account":
        other = other_account(acc)
        if not other:
            return False, M("relay.cmd_quota_no_other")
        f = quick_handoff(row)
        before = {r.get("tmux") or r.get("name") for r in (_json_cmd("sessions", "--json", "--no-screen") or [])}
        rc, out = run_cm("launch", row.get("cwd") or "", "--window", "--account", other)
        if rc != 0:
            return False, (out.splitlines() or ["launch failed"])[0]
        here = os.path.realpath(row.get("cwd") or "")
        new = [r.get("tmux") or r.get("name") for r in (_json_cmd("sessions", "--json", "--no-screen") or [])
               if (r.get("tmux") or r.get("name")) not in before and r.get("cwd") and os.path.realpath(r["cwd"]) == here]
        if not new:
            return False, M("relay.cmd_launch_no_session", name=session)
        rc, out = relay_talk(new[0], prefix_for(cmd, new[0]) + " " + M("relay.handoff_prompt", name=session, path=str(f)), "--no-wait")
        return (rc == 0), (M("relay.cmd_quota_other", name=S.short_name(new[0], prefixes()), account=other) if rc == 0 else (out.splitlines() or ["talk failed"])[0]), \
            ({"session": S.short_name(new[0], prefixes())} if rc == 0 else {})
    if a.startswith("pass:"):
        target = tmux_of(a[5:].strip())
        if not target or not is_live(target) or target == tm:
            return False, M("relay.cmd_not_running", name=a[5:].strip() or "?")
        f = quick_handoff(row)
        rc, out = relay_talk(target, prefix_for(cmd, target) + " " + M("relay.handoff_prompt", name=session, path=str(f)), "--no-wait")
        return (rc == 0), (M("relay.cmd_quota_passed", name=a[5:].strip()) if rc == 0 else (out.splitlines() or ["talk failed"])[0])
    return False, M("relay.cmd_quota_bad", arg=a or "?")


def task_check(tm):
    """1.57 (14/10, dagli spunti della valutazione di Kaya): la sessione `tm` si e' fermata. Per ogni suo task running
    si riesegue il controllo (`task check`, senza cambiare stato) e un evento task_check va al telefono: verde o rosso,
    il titolo del task, la coda dell'uscita. Solo quando l'esito cambia dall'ultima volta (task-checks.json): una
    sessione si ferma a ogni turno e il telefono non deve ricevere lo stesso verde dieci volte."""
    tasks = [t for t in (_json_cmd("task", "list", "--json") or []) if isinstance(t, dict) and t.get("state") == "running" and t.get("session") == tm]
    if not tasks:
        return 0
    p = rdir() / "task-checks.json"
    seen = read_json(p, {})
    name = S.short_name(tm, prefixes())
    for t in tasks:
        try:
            rc, out = run_cm("task", "check", t["id"], "--json", timeout=300)
            res = json.loads(out.splitlines()[-1]) if out.strip().startswith("{") else {"green": False, "tail": out}
        except (subprocess.TimeoutExpired, ValueError):
            res = {"green": False, "tail": "timeout"}
        green = bool(res.get("green"))
        if seen.get(t["id"]) == green:
            continue
        seen[t["id"]] = green
        key_ = "relay.event_task_check_green" if green else "relay.event_task_check_red"
        attention_mark("task_red", name, t["id"], M(key_, task=t["id"]), remove=green)
        body = S.one_line(t.get("title") or "") + ("\n" + "\n".join(str(res.get("tail") or "").splitlines()[-3:]) if not green and res.get("tail") else "")
        emit("task_check", M(key_, task=t["id"]), body, ref=t["id"], session=name, extra={"ok": green})   # 1.78: l'icona la sceglie l'app da ok
        log(f"task_check {t['id']} ({name}): {'verde' if green else 'rosso'}")
    write_json(p, seen)
    return 0


