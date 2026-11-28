# Parte di cm-tasks.py, eseguita nel suo spazio dei nomi come cm-tasks-resettle.py: le chiusure da sole che aspettano la
# prova della persona, e `supervisor task reopen`.
#
# 28/11/2026 (supervisor-autoclose-prova-28-11). Alle 03:41 auto-close ha chiuso tre compiti col controllo verde e
# nessuna prova, il cui esito chiedeva la prova della persona («… Telefono e web. Prova di <nome>.»): uno era in pausa
# --waits external, che auto-close tratta come chiudibile. Il controllo guardava il lavoro (commit, build, release), non
# la prova. Ora un esito che chiede la prova della persona tiene aperto il compito finche' c'e' una prova ok in vigore o
# la sua conferma; e `task reopen` riapre un compito chiuso per errore. resettle ricontrolla solo i compiti con tutte
# le prove in vigore ok: li' una prova ok c'e' sempre.
#
# 28/11/2026 (supervisor-reopen-prove-28-11): la riapertura supera le prove ok di prima e le conferme scritte prima
# non valgono piu': un compito riaperto si richiude da solo solo con una prova nuova ok o una conferma nuova.

# «Prova di <Nome>.» (la forma dei contratti della master: un nome con la maiuscola, mai scritto qui), «provato da
# <Nome>», «prova della persona / dell'utente / del manutentore»; in inglese «<Name>'s test», «tested by the person».
# «una prova di carico» (minuscola dopo «di») non e' la persona
PERSON_TEST_RES = (
    re.compile(r"\b[Pp]rova di [A-ZÀ-Ý]"),
    re.compile(r"\bprovat[oaie] da [A-ZÀ-Ý]"),
    re.compile(r"\b(?:prova|test)\s+(?:della persona|dell['’]utente|del manutentore)\b", re.I),
    re.compile(r"\b[A-Z][a-z]+['’]s test\b|\btest(?:ed)? by the (?:person|user|maintainer)\b|\bthe (?:person|user|maintainer) tests\b"),
)
# le voci di outcome.proof («commit, build, deploy web, prova»): una voce che e' la prova della persona
PERSON_TEST_ITEMS = {"prova", "la prova", "prova della persona", "prova dell'utente", "test della persona", "person test", "the person's test"}


def needs_person_test(task):
    """L'esito del contratto chiede la prova della persona (outcome.expected o outcome.proof)."""
    o = task.get("outcome") or {}
    text = " ".join(str(o.get(k) or "") for k in ("expected", "proof"))
    if any(r.search(text) for r in PERSON_TEST_RES):
        return True
    items = {x.strip().lower().rstrip(".") for x in re.split(r"[,;]", str(o.get("proof") or ""))}
    return bool(items & PERSON_TEST_ITEMS)


def reopen_last(con, tid):
    """L'ultima riapertura del compito, {at, approval_n}, o None: le conferme scritte prima (n <= approval_n) non valgono."""
    return con.execute("SELECT at, approval_n FROM reopens WHERE task=? ORDER BY n DESC LIMIT 1", (tid,)).fetchone()


def person_confirmed(con, tid):
    """La conferma della persona (task confirm, «Tutto va», tutte le prove ok) scritta dopo l'ultima riapertura: quella
    che leggono settle e `task confirmed`."""
    last = reopen_last(con, tid)
    return bool(con.execute("SELECT 1 FROM approvals WHERE task=? AND what='confirmed' AND by_ IS NOT NULL AND n>? LIMIT 1",
                            (tid, last["approval_n"] if last else 0)).fetchone())


def person_test_ok(con, tid):
    """Una prova ok in vigore, o la conferma della persona dopo l'ultima riapertura: quello che vale per settle."""
    return (bool(con.execute("SELECT 1 FROM prove WHERE task=? AND result='ok' AND superseded_at IS NULL LIMIT 1", (tid,)).fetchone())
            or person_confirmed(con, tid))


REOPEN_FROM = ("done", "failed", "cancelled", "superseded")


def reopen(con, tid, by, why):
    """Riapre un compito chiuso per errore: torna running se e' stato lavorato (un `task start`, un passo, una prova, un
    esito), altrimenti assigned. La chiusura esce dalla pagella (closures), un passo nel registro dice chi, da che stato e
    perche', e la sessione del compito lo sa. Gli esiti restano nella storia. (exit, testo, nuovo stato, prove superate).

    28/11/2026 (supervisor-reopen-prove-28-11): alle 15:48 la master ha riaperto un compito che non funzionava e alle 15:50
    resettle l'ha richiuso, perche' la sua prova ok di prima era ancora in vigore e il controllo verde. Ora le prove ok in
    vigore passano superate (restano nella storia, prova_one le mostra ok) e una riga di `reopens` segna da dove contano
    le conferme della persona: settle, resettle e auto-close aspettano una prova nuova ok o una conferma nuova."""
    row = get_task(con, tid)
    was = row["state"]
    if was not in REOPEN_FROM:
        return 3, f"task reopen {tid}: already open ({was}): nothing changed", was, []
    state = "running" if ever_worked(con, tid) else "assigned"
    t = now()
    gone = [r["n"] for r in con.execute("SELECT n FROM prove WHERE task=? AND result='ok' AND superseded_at IS NULL ORDER BY n", (tid,)).fetchall()]
    set_state(con, tid, state)
    con.execute("DELETE FROM closures WHERE task=?", (tid,))
    con.execute("UPDATE prove SET superseded_at=? WHERE task=? AND result='ok' AND superseded_at IS NULL", (t, tid))
    con.execute("INSERT INTO reopens (task, at, by_, was, why, approval_n) VALUES (?,?,?,?,?,(SELECT COALESCE(MAX(n), 0) FROM approvals))",
                (tid, t, by, was, why))
    nums = ", ".join(f"#{n}" for n in gone)
    step = M("tasks.reopened_step", by=by, was=was, why=why) + (M("tasks.reopened_prove_step", nums=nums) if gone else "")
    con.execute("INSERT INTO steps (task, at, by_, kind, text, next) VALUES (?,?,?,'step',?,NULL)", (tid, t, by, step[:500]))
    con.commit()
    sess = row["session"] or (_contract_task(row).get("where") or {}).get("session")
    if sess and sess != root_session():
        _talk(sess, M("tasks.reopened", task=tid, by=by, why=why, state=state) + " "
              + M("tasks.reopened_wait", gone=M("tasks.reopened_gone", nums=nums) if gone else ""))
    return 0, f"{tid}: reopened (was {was}) → {state}" + (f"; tests {nums} superseded" if gone else ""), state, gone
