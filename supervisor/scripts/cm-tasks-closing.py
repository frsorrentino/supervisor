# Parte di cm-tasks.py, eseguita nel suo spazio dei nomi come cm-tasks-resettle.py: le chiusure da sole che aspettano la
# prova della persona, e `supervisor task reopen`.
#
# 28/11/2026 (supervisor-autoclose-prova-28-11). Alle 03:41 auto-close ha chiuso tre compiti col controllo verde e
# nessuna prova, il cui esito chiedeva la prova della persona («… Telefono e web. Prova di <nome>.»): uno era in pausa
# --waits external, che auto-close tratta come chiudibile. Il controllo guardava il lavoro (commit, build, release), non
# la prova. Ora un esito che chiede la prova della persona tiene aperto il compito finche' c'e' una prova ok in vigore o
# la sua conferma; e `task reopen` riapre un compito chiuso per errore. resettle ricontrolla solo i compiti con tutte
# le prove in vigore ok: li' una prova ok c'e' sempre.

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


def person_test_ok(con, tid):
    """Una prova ok in vigore, o la conferma della persona (task confirm, «Tutto va»): quello che vale per settle."""
    return bool(con.execute("SELECT 1 FROM prove WHERE task=? AND result='ok' AND superseded_at IS NULL LIMIT 1", (tid,)).fetchone()
                or con.execute("SELECT 1 FROM approvals WHERE task=? AND what='confirmed' AND by_ IS NOT NULL LIMIT 1", (tid,)).fetchone())


REOPEN_FROM = ("done", "failed", "cancelled", "superseded")


def reopen(con, tid, by, why):
    """Riapre un compito chiuso per errore: torna running se e' stato lavorato (un `task start`, un passo, una prova, un
    esito), altrimenti assigned. La chiusura esce dalla pagella (closures), un passo nel registro dice chi, da che stato e
    perche', e la sessione del compito lo sa. Gli esiti restano nella storia. (exit, testo, nuovo stato)."""
    row = get_task(con, tid)
    was = row["state"]
    if was not in REOPEN_FROM:
        return 3, f"task reopen {tid}: already open ({was}): nothing changed", was
    state = "running" if ever_worked(con, tid) else "assigned"
    t = now()
    set_state(con, tid, state)
    con.execute("DELETE FROM closures WHERE task=?", (tid,))
    con.execute("INSERT INTO steps (task, at, by_, kind, text, next) VALUES (?,?,?,'step',?,NULL)",
                (tid, t, by, M("tasks.reopened_step", by=by, was=was, why=why)[:500]))
    con.commit()
    sess = row["session"] or (_contract_task(row).get("where") or {}).get("session")
    if sess and sess != root_session():
        _talk(sess, M("tasks.reopened", task=tid, by=by, why=why, state=state))
    return 0, f"{tid}: reopened (was {was}) → {state}", state
