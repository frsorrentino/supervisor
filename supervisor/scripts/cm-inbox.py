#!/usr/bin/env python3
"""supervisor inbox — la casella persistente dei messaggi fra sessioni (23/09/2026).

Design: docs/plans/2026-09-23-approvazioni-casella-registro-design.md, sezione 2. Il 23/09 questa sessione si e'
chiusa due volte mentre altre le rispondevano, e le risposte sono andate perse: `talk` consegnava solo a una sessione
viva. Ora ogni messaggio si scrive su disco PRIMA di consegnarlo; quello che non arriva resta `pending` e lo consegna
la destinataria stessa: allo Stop di un turno (se era viva ma occupata) o al SessionStart (se era chiusa).

  supervisor inbox [NOME] [--all]     i messaggi (in attesa; --all anche consegnati e scaduti)
  supervisor inbox status ID          lo stato di un messaggio (anche: supervisor talk --status ID)
  supervisor inbox cancel ID          annulla un messaggio ancora in attesa (stato cancelled, il file resta);
                                         uno gia' consegnato o scaduto non si tocca (01/10/2026, dall'app: un /exit
                                         rimasto in casella per una sessione chiusa prima di riceverlo)

File: <state_dir>/inbox/<nome-tmux>/<id>.json — {id, to, to_cwd, from, text, created, expires, status, delivered_at, via, origin}.
`origin`: «relay» per i messaggi del relay (dal telefono, dall'orologio, dalla web), «terminal» per i messaggi che la
persona scrive nella sessione (li salva l'hook UserPromptSubmit, 22/10/2026: un ok scritto in una sessione vale come
gli altri), «cron» per i prompt del cron della sessione stessa e «unverified» per un prompt che la trascrizione non
attribuisce (23/10/2026: li salva lo stesso hook, mai buoni per un ok), «session» per tutti gli altri.
Ritenzione (23/10/2026, «ok 7 giorno per supervisor»): i record restano 7 giorni, quelli dell'hook (terminal, cron,
unverified) un giorno; pota l'hook, una volta l'ora, al prompt della sessione (`prune`).
Identita' della destinataria: il nome tmux (resta uguale nei riavvii) e, se nota, la cartella: una sessione nuova con
lo stesso nome in un'altra cartella non riceve la posta della vecchia.
"""
import datetime
import hashlib
import importlib.util
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)
STATE = Path(cm.expand(CFG["state_dir"]))
EXPIRES_S = float((CFG.get("inbox") or {}).get("expires_h") or 48) * 3600
KEEP_S = 7 * 86400   # 23/10/2026: ritenzione della casella, ogni origine (i prompt della persona un giorno: cm-hook.py)
SHOW_MAX, SHOW_BYTES = 5, 4096


def M(key, **kw):
    return cm.msg(CFG, key, **kw)


def box(to):
    return STATE / "inbox" / re.sub(r"[^A-Za-z0-9_.-]", "_", to)


def ledger(event, **kw):
    """Il registro delle attivita' (sezione 3): una riga nel diario, solo la prima riga del testo."""
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        row = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "event": event, **kw}
        with open(STATE / "ledger.jsonl", "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def first_line(text, n=200):
    return (str(text or "").strip().splitlines() or [""])[0][:n]


CLAIM_TTL_S = 120


def claims_dir():
    return STATE / "inbox-claims"


def _text_key(text):
    return hashlib.sha256(str(text).encode("utf-8", "surrogateescape")).hexdigest()[:16] if text is not None else "*"


def claim_origin(to, text, origin="relay"):
    """88c86e (16/10): il relay segnava «relay» DOPO il suo `talk` (mark_origin), e chi leggeva il record nel frattempo
    (la master, appena consegnato) vedeva «session». Ora il relay lascia un segno PRIMA di `talk`: put() lo consuma e
    scrive l'origine giusta gia' alla creazione. `text` None = il prossimo messaggio per `to` (la segnalazione, il cui
    testo lo compone cm-report.sh). La CLI non ha un'opzione per farlo: lo fa solo il processo del relay."""
    d = claims_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{secrets.token_hex(6)}.json"
    with open(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        json.dump({"to": to, "key": _text_key(text), "origin": origin, "at": time.time()}, f)
    return p


def _take_claim(to, text):
    """L'origine del segno che corrisponde a questo messaggio (il piu' vecchio, ancora valido), consumato; o None."""
    now, key, best = time.time(), _text_key(text), None
    for p in sorted(claims_dir().glob("*.json")) if claims_dir().is_dir() else []:
        try:
            c = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if now - float(c.get("at") or 0) > CLAIM_TTL_S:
            p.unlink(missing_ok=True)
            continue
        if c.get("to") == to and c.get("key") in (key, "*") and (best is None or c["at"] < best[1]["at"]):
            best = (p, c)
    if best is None:
        return None
    try:
        best[0].unlink()   # consumato una volta sola: due put concorrenti non lo prendono entrambi
    except OSError:
        return None
    return best[1].get("origin")


def put(to, text, sender, to_cwd="", origin="session", quiet=False):
    """quiet (22/10): nessuna riga «talk» nel diario — i messaggi della persona salvati dall'hook non sono un talk."""
    if origin == "session":
        try:
            origin = _take_claim(to, text) or origin
        except OSError:
            pass
    d = box(to)
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    rec = {"id": secrets.token_hex(3), "to": to, "to_cwd": os.path.realpath(to_cwd) if to_cwd else "", "from": sender,
           "text": text, "created": now, "expires": now + EXPIRES_S, "status": "pending", "delivered_at": None, "via": None,
           # 0319ab (14/10): «relay» solo per cio' che consegna il relay (talk_as dentro il suo processo), mai dalla CLI
           "origin": origin}
    p = d / f"{rec['id']}.json"
    with open(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        json.dump(rec, f, ensure_ascii=False)
    if not quiet:
        ledger("talk", sender=sender, to=to, id=rec["id"], text=first_line(text))
    return rec


def prune(to, ages=None, default_s=None):
    """Potatura della casella. 22/10: un file per prompt della persona, mai per sempre; 23/10 (il maintainer dalla web
    alle 13:20, «ok 7 giorno per supervisor»): ritenzione di 7 giorni per tutto il resto, i record del relay e delle
    altre sessioni e quelli vecchi senza origine (la casella della master: 1500 file, 950 senza origine). `ages`:
    origine → secondi; le altre origini durano `default_s` (KEEP_S). Legge solo i file con mtime oltre la soglia piu'
    corta (23/10: la lettura di tutta la casella a ogni prompt faceva scadere l'hook)."""
    ages = dict(ages or {})
    default_s = KEEP_S if default_s is None else default_s
    now = time.time()
    cut = now - min([default_s] + list(ages.values()))
    for p in box(to).glob("*.json"):
        try:
            if p.stat().st_mtime >= cut:
                continue
            rec = json.loads(p.read_text())
            if not isinstance(rec, dict):
                continue
            if float(rec.get("created") or 0) < now - ages.get(rec.get("origin"), default_s):
                p.unlink()
        except (OSError, ValueError):
            continue


def mark_origin(to, text, since, origin):
    """0319ab (14/10): il relay consegna con `supervisor talk`, che scrive sempre origin «session»; subito dopo il relay
    marca come suo il record appena scritto: stesso destinatario e testo, creato da `since` in poi. Torna l'id o None.
    La CLI non ha un modo per scrivere un'origine diversa. `text` None (21:43, la segnalazione: il testo lo compone
    cm-report.sh) = il record piu' recente da `since` in poi, chiunque l'abbia scritto."""
    best = None
    for r in messages(to, include_all=True):
        if (text is None or r.get("text") == text) and float(r.get("created") or 0) >= since - 1 and (best is None or r["created"] > best["created"]):
            best = r
    if best is None:
        return None
    p = _path_of(best["id"])
    if not p:
        return None
    try:
        rec = json.loads(p.read_text())
        rec["origin"] = origin
        tmp = p.with_name(p.name + ".tmp")
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, p)
    except (OSError, ValueError, TypeError):
        return None
    return best["id"]


def _path_of(rec_id):
    for p in (STATE / "inbox").glob(f"*/{rec_id}.json"):
        return p
    return None


def load(rec_id):
    p = _path_of(rec_id)
    try:
        return json.loads(p.read_text()) if p else None
    except (OSError, ValueError):
        return None


def mark(rec_id, status, via=None):
    p = _path_of(rec_id)
    if not p:
        return None
    try:
        rec = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if rec.get("status") == status:
        return rec
    rec["status"] = status
    if status == "delivered":
        rec["delivered_at"], rec["via"] = time.time(), via
        ledger("delivered", to=rec["to"], id=rec_id, sender=rec.get("from"), via=via)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False))
    os.replace(tmp, p)
    return rec


def messages(to, include_all=False):
    out = []
    for p in sorted(box(to).glob("*.json"), key=lambda x: x.stat().st_mtime):
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if rec.get("status") == "pending" and rec.get("expires", 0) < time.time():
            rec = mark(rec["id"], "expired") or rec
        if include_all or rec.get("status") == "pending":
            out.append(rec)
    return sorted(out, key=lambda r: r.get("created", 0))


def pending_for(to, cwd=""):
    """I messaggi in attesa per questa sessione: stesso nome tmux e, se il messaggio conosce la cartella, stessa
    cartella (o una sua sottocartella)."""
    if not to:
        return []
    real = os.path.realpath(cwd) if cwd else ""
    out = []
    for r in messages(to):
        want = r.get("to_cwd") or ""
        if want and real and not (real == want or real.startswith(want + os.sep)):
            continue
        out.append(r)
    return out


def when(ts):
    return time.strftime("%d/%m %H:%M", time.localtime(float(ts or 0)))


def deliver_block(to, cwd, via):
    """Il testo da mettere nel contesto della destinataria, e i messaggi segnati consegnati. Al massimo SHOW_MAX
    messaggi o SHOW_BYTES: gli altri restano in attesa e il testo dice come leggerli."""
    recs = pending_for(to, cwd)
    if not recs:
        return ""
    lines, size, shown = [M("inbox.header", n=len(recs))], 0, 0
    for r in recs:
        piece = M("inbox.item", sender=r.get("from") or "?", when=when(r.get("created")), id=r["id"]) + "\n" + str(r.get("text") or "")
        if shown >= SHOW_MAX or (shown and size + len(piece.encode()) > SHOW_BYTES):
            break
        lines.append(piece)
        size += len(piece.encode())
        shown += 1
        mark(r["id"], "delivered", via)
    if shown < len(recs):
        lines.append(M("inbox.more", n=len(recs) - shown, name=to))
    return "\n\n".join(lines)


def known_session(name):
    """(nota?, cartella): una sessione che il registro conosce, anche se ora e' chiusa."""
    for key in ("good_file", "file"):
        try:
            d = json.loads(Path(cm.expand(CFG["registry"][key])).read_text())
        except (OSError, ValueError, KeyError):
            continue
        for s in d.get("sessioni") or []:
            if s.get("nome") == name:
                return True, s.get("cartella") or ""
    return False, ""


def main(argv):
    if argv and argv[0] == "status":
        rec = load(argv[1]) if len(argv) > 1 else None
        if not rec:
            print(M("inbox.unknown", id=argv[1] if len(argv) > 1 else ""), file=sys.stderr)
            return 1
        print(M("inbox.status", id=rec["id"], to=rec["to"], status=rec["status"],
                when=when(rec.get("delivered_at") or rec.get("created")), via=rec.get("via") or "-"))
        return 0
    if argv and argv[0] == "cancel":
        rec = load(argv[1]) if len(argv) > 1 else None
        if not rec:
            print(M("inbox.unknown", id=argv[1] if len(argv) > 1 else ""), file=sys.stderr)
            return 1
        if rec.get("status") != "pending":
            print(M("inbox.not_pending", id=rec["id"], to=rec["to"], status=rec["status"]), file=sys.stderr)
            return 1
        mark(rec["id"], "cancelled")
        ledger("cancelled", to=rec["to"], id=rec["id"], sender=rec.get("from"))
        print(M("inbox.cancelled", id=rec["id"], to=rec["to"]))
        return 0
    include_all = "--all" in argv
    names = [a for a in argv if not a.startswith("--")] or sorted(p.name for p in (STATE / "inbox").glob("*") if p.is_dir())
    any_ = False
    for n in names:
        for r in messages(n, include_all):
            any_ = True
            print(f"{r['id']}  {r['status']:<9} {when(r.get('created'))}  {r.get('from') or '?'} → {n}: {first_line(r.get('text'), 100)}")
    if not any_:
        print(M("inbox.none"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
