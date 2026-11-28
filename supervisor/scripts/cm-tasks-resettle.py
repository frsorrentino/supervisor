# Parte di cm-tasks.py, eseguita nel suo spazio dei nomi come le parti di cm-relay: `supervisor task resettle`.
#
# 27/11/2026 (supervisor-rossi-ricontrollo-27-11). Dal telefono alle 21:46: «Quelli rossi rimangono qui anche se sono
# superati». Due compiti dell'app con tutte le prove ok erano rossi dalle 20:50 per «ultima build non verde»; alle 21:47
# la CI era verde, ma restavano in «Da provare › Controllo rosso» finche' la master non li ha chiusi a mano. settle()
# parte solo quando arriva l'esito di una prova, e auto-close guarda solo i compiti aperti da piu' di 6 ore. Ora la
# guardia, nel suo giro dei 10 minuti, lancia `task resettle`: i compiti con tutte le prove ok e il controllo rosso
# rieseguono il controllo, al piu' una volta ogni 10 minuti ciascuno. Verde: si chiudono come con settle (done, e la
# sessione e la master lo sanno). Rosso: nessun tentativo speso, nessun avviso; un passo prova-red nuovo solo se il
# motivo e' cambiato, cosi' l'app mostra quello vero.

RESETTLE_S = 600          # al piu' un controllo ogni 10 minuti per compito
RESETTLE_BUDGET_S = 600   # dopo 10 minuti un giro non comincia altri controlli: il resto tocca al giro dopo


def resettle_path():
    """Quando ogni compito e' stato ricontrollato l'ultima volta: {id: epoch}, accanto al registro."""
    return Path(str(db_path()) + ".resettle.json")


def _resettle_seen():
    try:
        d = json.loads(resettle_path().read_text())
        return {str(k): int(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _resettle_save(seen):
    p = resettle_path()
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(json.dumps(seen, sort_keys=True))
        os.replace(tmp, p)
    except OSError:
        pass


def _last_checked(con, x, seen):
    """L'ultima volta che il controllo di questo compito ha girato: il ricontrollo, il passo prova-red, o l'ultima prova
    segnata ok (dopo l'ultima il settle gira da se': il ricontrollo aspetta i suoi 10 minuti anche li')."""
    live = _live_prove(con, x["task"])
    return max([seen.get(x["task"], 0), int(x.get("at") or 0)] + [int(r["result_at"] or 0) for r in live])


def resettle(con, now_=None, interval=RESETTLE_S, budget=RESETTLE_BUDGET_S):
    """I compiti aperti con tutte le prove ok e il controllo rosso (i `red` di prove_list), il controllo rieseguito: al
    piu' una volta ogni `interval` secondi per compito, prima quelli aspettano da piu' tempo. Ognuno col lucchetto di
    settle, preso senza aspettare: se un settle sta girando, il giro finisce qui e riprende al prossimo. Torna
    [{task, outcome: green|red|busy, reason}] dei compiti toccati."""
    import fcntl
    t = int(now_ or now())
    t0 = time.monotonic()
    seen = _resettle_seen()
    red = prove_list(con, t)["red"]
    todo = sorted(((x, _last_checked(con, x, seen)) for x in red), key=lambda p: (p[1], p[0]["task"]))
    out = []
    for x, last in todo:
        tid = x["task"]
        if t - last < interval:
            continue
        if time.monotonic() - t0 > budget:
            break
        lockf = open(str(db_path()) + ".settle.lock", "a")
        try:
            try:
                fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                out.append({"task": tid, "outcome": "busy", "reason": None})
                break
            row = get_task(con, tid)   # riletto col lucchetto: un settle puo' averlo chiuso nel frattempo
            live = _live_prove(con, tid)
            task = _contract_task(row)
            if row["state"] not in OPEN_STATES or not live or any(r["result"] != "ok" for r in live) or outcome_errors(task):
                continue
            # come auto-close: una carta senza risposta o una pausa per la persona aspettano lei, niente chiusura da sola
            if open_card(con, tid) or con.execute("SELECT 1 FROM pauses WHERE task=? AND resumed_at IS NULL AND COALESCE(waits, '') != 'external'", (tid,)).fetchone():
                continue
            rc, tail = run_check(task)
            seen[tid] = int(now())
            if rc == 0:
                try:
                    done(con, tid, checked=(rc, tail))
                except SystemExit as e:   # done rifiuta (stato cambiato): resta com'era
                    out.append({"task": tid, "outcome": "red", "reason": str(e.code)[:300]})
                    continue
                _tell(row, M("tasks.prove_closed", task=tid))
                out.append({"task": tid, "outcome": "green", "reason": None})
                continue
            why = (tail.splitlines() or [""])[-1][:300] or f"exit {rc}"
            if why != x.get("reason"):
                ts = now()
                con.execute("INSERT INTO steps (task, at, by_, kind, text, next) VALUES (?,?,NULL,'prova-red',?,?)", (tid, ts, why, M("tasks.prove_red_next")))
                con.execute("UPDATE tasks SET updated_at=? WHERE id=?", (ts, tid))
                con.commit()
            out.append({"task": tid, "outcome": "red", "reason": why})
        finally:
            lockf.close()
    open_ids = {x["task"] for x in red}
    _resettle_save({k: v for k, v in seen.items() if k in open_ids and not any(o["task"] == k and o["outcome"] == "green" for o in out)})
    return out


def _resettle_log(text):
    """Le righe del giro in <state_dir>/guard.log, nella forma di quelle della guardia (lancia il giro staccato)."""
    cm_, cfg = _cfg()
    if not cm_:
        return
    try:
        with open(Path(cm_.expand(cfg["state_dir"])) / "guard.log", "a") as f:
            for line in text.splitlines():
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} resettle: {line}\n")
    except (OSError, KeyError):
        pass


def render_resettle(out):
    lines = []
    for o in out:
        if o["outcome"] == "green":
            lines.append(f"{o['task']}: green — every test ok, check green: closed")
        elif o["outcome"] == "red":
            lines.append(f"{o['task']}: red — {o['reason']}; no attempt spent, the task stays open")
        else:
            lines.append(f"{o['task']}: a settle is running; the next round goes on")
    return "\n".join(lines)
