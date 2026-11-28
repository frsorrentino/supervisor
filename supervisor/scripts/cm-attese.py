#!/usr/bin/env python3
"""supervisor attese — ogni domanda che aspetta la persona, con un id stabile e un link (07/11/2026, contratto 1.130,
supervisor-attese-link-07-11).

  supervisor attese [--json]          le attese aperte, dalla piu' vecchia, ognuna col suo link
  supervisor attese show ID [--json]  una sola, con il messaggio della trascrizione dove e' stata spiegata

Un'attesa e' {id, type, kind, session, task, text, options, at, link, answer}:
  type   carta (dal registro: ok, scelta, pausa che aspetta la persona, prova, login) | chat (domanda in chat: la riga
         «Prossimi» con una voce «!», needs-you, una domanda AskUserQuestion)
  kind   ok | choice | pausa | prova | login | prossimi | needs_you | question; 1.150 limite; 1.151 (16/11/2026,
         supervisor-riall-1-dati-16-11: le attese sono la fonte unica di ogni voce per te) nonva (una prova segnalata «Non
         va», chiusa con «Ora va»), rosso (il compito rosso della guardia, task_red; dal 1.165 non i compiti con le
         prove tutte ok e il controllo rosso, che si chiudono da soli), turno (un turno fallito, «Continua»), raccolta
         (la Prima raccolta, senza sessione). Ogni voce del registro porta il suo gruppo di decisione (decision: chiave,
         «Non ora» e «Lascia»); una prova il dettaglio di ciascuna (tests, build). La sessione e' il nome dello stato.
  answer {op, arg, ...}: l'op del relay che risponde, la stessa dei tasti della carta (approve, prova, decisione,
         prompt, attention_done, answer); null per il login, che rifa' la master
  title  una riga per gli elenchi; card.deploy; actions [{label, style, cmd, text_field?, recommended?}] (1.131)
  msg    solo in `show` e nell'op attesa: {session_id, id, text, at}, la voce della trascrizione (l'id e' quello delle voci
         di transcript, «<uuid>.<i>»: l'app ci salta con transcript «n:around=<id>»); null se non si trova

L'id nasce dalla chiave di cio' che aspetta (compito e ora della richiesta, sessione e ora del turno, id della domanda):
la stessa domanda ha lo stesso id in ogni lettura, una domanda nuova ne ha uno nuovo. Lo calcolano tutti allo stesso
modo (stato del relay, op, hook, questa CLI) con wid().

Il link: <relay.web_app_url>/#/attesa/<id>; l'app Android apre lo stesso indirizzo. relay.web_app_url = "" lo spegne
(link null).
"""
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB_APP_URL = "https://supervisor.francescosorrentino.com"   # come cm-relay.WEB_APP_URL
ID_RE = re.compile(r"w[0-9a-f]{10}")
TAIL_BYTES = 6 * 1024 * 1024   # la coda della trascrizione in cui si cerca la spiegazione
MSG_MAX = 1500


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731


# ------------------------------------------------------------------ id e link
S = _load("cm-relay-state")
wid, wid_approval, wid_attention = S.wid, S.wid_approval, S.wid_attention


def link(i):
    base = str((CFG.get("relay") or {}).get("web_app_url", WEB_APP_URL) or "").rstrip("/")
    return f"{base}/#/attesa/{i}" if base and i else None


# ------------------------------------------------------------------ le attese
def _short(name):
    """Il nome della sessione come lo mostra l'app (senza il prefisso tmux dell'account, come cm-relay.prefixes)."""
    prefixes = [a.get("tmux_prefix") or "" for a in (CFG.get("accounts") or {}).values()]
    for p in sorted((p for p in prefixes if p), key=len, reverse=True):
        if str(name or "").startswith(p):
            return str(name)[len(p):]
    return name


def _test(x, state):
    """Una prova come la mostra Verifica: {id, build, dove, fai, atteso, files, state todo|ko, text, at}."""
    return {"id": x["id"], "build": x.get("build"), "dove": x.get("dove"), "fai": x.get("fai"), "atteso": x.get("atteso"),
            "files": [file_item(f) for f in x.get("files") or []] or None, "state": state, "text": x.get("text") if state == "ko" else None,
            "at": x.get("at")}


def registry_items(now_):
    """Le carte del registro: ok e scelte, pause che aspettano la persona, needs-you e login (da tk.decisions), prove da
    fare. 1.151: con quello che stava solo nelle op decisioni e prove: il gruppo di decisione di ogni voce (chiave per
    «Non ora» e «Lascia», titolo, peso, cosa sblocca, qui o al PC, la nota), l'inizio del piano, il dettaglio di ogni
    prova (build, dove, fai, atteso), le prove segnalate «Non va» e i compiti col controllo rosso. Un errore del registro
    sale a chi chiama: il relay tiene allora le voci di prima, senza chiuderne nessuna per finta."""
    tk = _load("cm-tasks")
    con = tk.connect(create=False)
    if con is None:
        return []
    out = []
    try:
        d = tk.decisions(con, now_)
        for g in d.get("groups") or []:
            dec = {"key": g["key"], "title": g.get("title"), "cause": g.get("cause"), "where": g.get("where"), "weight": g.get("weight"),
                   "unblocks": g.get("unblocks") or [], "note": g.get("note"), "since": g.get("since")}
            for it in g.get("items") or []:
                k = it.get("kind")
                common = {"decision": dec, "plan_start": it.get("plan_start"), "asks": it.get("asks"), "where": it.get("where")}
                if k == "ok":
                    kind = "choice" if it.get("options") else "ok"
                    a = {"id": wid_approval(it["task"], it.get("since")), "type": "carta", "kind": kind, "session": it.get("session"), "task": it["task"],
                         "text": it.get("what") or it.get("title"), "options": it.get("options") or None, "at": it.get("since"),
                         "answer": {"op": "approve", "arg": it["task"]}, **common, "precedent": it.get("precedent"), "also": it.get("also")}
                    # la carta del registro: cosa esce e dove, cosa non e' provato, se e come si torna indietro (wait-ok)
                    a["_short"] = it.get("short")
                    a["card"] = {k: it.get(f) for k, f in (("what", "what"), ("where", "where_to"), ("untested", "untested"), ("undo", "undo"), ("undo_how", "undo_how"))
                                 if it.get(f)} or None
                    a["files"] = it.get("files") or None   # 1.137: i file da guardare (wait-ok --files)
                    if it.get("silence_until") and kind == "ok":   # 1.159: silenzio assenso, la scadenza
                        a["silence_until"] = int(it["silence_until"])
                    a["silence_ok"] = kind == "ok" and it.get("silence_ok") is True   # 1.160: la regola del registro
                    a["_advice"] = it.get("advice")   # 1.149: il consiglio di chi chiede
                    a["_card"] = tk.card_of(con, it["task"])   # 1.137: la scheda e i suoi giri
                    out.append(a)
                elif k == "pause":
                    out.append({"id": wid("pausa", it["task"], it.get("since")), "type": "carta", "kind": "pausa", "session": it.get("session"), "task": it["task"],
                                "text": it.get("reason"), "options": None, "at": it.get("since"), "answer": {"op": "decisione", "arg": g["key"]}, "_short": it.get("short"),
                                "_prova": bool(it.get("prova")), **common, "prova": it.get("prova"), "undo": it.get("undo"), "undo_how": it.get("undo_how")})
                elif k == "login":
                    out.append({"id": wid("login", it.get("account"), it.get("expires_at")), "type": "carta", "kind": "login", "session": it.get("session"), "task": None,
                                "text": it.get("reason"), "options": None, "at": it.get("since"), "answer": None, **common, "account": it.get("account"),
                                "expires_at": it.get("expires_at")})
                elif k == "needs_you":
                    out.append({"id": wid("needs_you", int(it.get("since") or 0)), "type": "chat", "kind": "needs_you", "session": it.get("session"),
                                "task": it.get("task"), "text": it.get("reason"), "options": None, "at": it.get("since"),
                                "answer": {"op": "attention_done", "arg": it.get("session"), "kind": "needs_you"}, **common})
        p = tk.prove_list(con, now_)
        by_task = {}
        for gr in p.get("groups") or []:
            for x in gr.get("prove") or []:
                by_task.setdefault(x["task"], []).append(x)
        for tid, xs in by_task.items():
            xs = sorted(xs, key=lambda x: x["id"])
            ids = [x["id"] for x in xs]
            fs = {}
            for x in xs:   # 1.137: i file delle prove del compito, una volta ciascuno
                for f in x.get("files") or []:
                    fs.setdefault(f.get("path"), f)
            out.append({"id": wid("prova", tid, ids[0]), "type": "carta", "kind": "prova", "session": xs[0].get("session"), "task": tid, "_short": xs[0].get("short"),
                        "files": list(fs.values()) or None,
                        "text": "; ".join(f"{x['dove']}: {x['fai']}" for x in xs)[:600], "options": None, "at": min(int(x.get("at") or 0) for x in xs),
                        "answer": {"op": "prova", "arg": ",".join(map(str, ids))},
                        # 1.151: ogni prova, con la sua build: l'app dice «installa prima» per quelle di un'altra versione
                        "tests": [_test(x, "todo") for x in xs], "build": max(xs, key=lambda x: (int(x.get("at") or 0), x["id"])).get("build")})
            out[-1]["silence_ok"] = all(x.get("silence_ok") is True for x in xs)   # 1.160: la regola del registro
            if all(x.get("silence_until") for x in xs):   # 1.159: la scadenza della voce = l'ultima delle sue prove
                out[-1]["silence_until"] = max(int(x["silence_until"]) for x in xs)
        for x in p.get("ko") or []:   # 1.151: segnalata «Non va»; si chiude con «Ora va» o «Non serve piu'»
            out.append({"id": wid("nonva", x["id"]), "type": "carta", "kind": "nonva", "session": x.get("session"), "task": x["task"], "_short": x.get("short"),
                        "files": [f for f in x.get("files") or []] or None, "text": x.get("text"), "options": None, "at": x.get("at"),
                        "answer": {"op": "prova", "arg": str(x["id"]), "result": "ok"}, "tests": [_test(x, "ko")], "build": x.get("build")})
        # 1.165 (28/11/2026, supervisor-verifiche-rosse-frasi-28-11): i compiti con le prove tutte ok e il controllo rosso
        # (p["red"]) non sono piu' voci per la persona: li ricontrolla resettle e si chiudono da soli. Prima erano «rosso»,
        # col testo del controllo («AssertionError: …») in Ti aspetta. Resta il rosso della guardia (task_red, da _from_state)
    finally:
        con.close()
    return out


_from_registry = registry_items   # il nome di prima

CARD_KINDS = ("ok", "choice", "prova")   # 1.160: le carte che bloccano un compito in corso


def held_tasks():
    """1.160 (23/11/2026, supervisor-promemoria-carte-23-11): i compiti con una pausa aperta (la persona o un'attesa
    esterna): le loro carte non sono un compito in corso che aspetta. Un insieme vuoto senza registro."""
    tk = _load("cm-tasks")
    con = tk.connect(create=False)
    if con is None:
        return set()
    try:
        return {r[0] for r in con.execute("SELECT DISTINCT task FROM pauses WHERE resumed_at IS NULL")}
    finally:
        con.close()


def blocking_cards(items, held):
    """1.160: le voci di attese che sono una carta di ok, di scelta o di prova di un compito non in pausa."""
    return [a for a in items if a.get("type") == "carta" and a.get("kind") in CARD_KINDS and a.get("task") and a["task"] not in held]


def raccolta_items(now_):
    """1.151: la Prima raccolta come voce senza sessione, finche' la domanda e' aperta (come op prima_raccolta.ask)."""
    try:
        rc = _load("cm-raccolta")
        s = rc.read_status() or {}
        if not rc.asking(s):
            return []
        q = M("raccolta.consent_question", days=rc.DAYS)
    except Exception:   # noqa: BLE001 — una voce in piu': mai far cadere le altre
        return []
    return [{"id": wid("raccolta", str(s.get("consent")), int(s.get("ask_after") or 0), int(s.get("asked_v") or 0)), "type": "carta", "kind": "raccolta",
             "session": None, "task": None, "text": f"{q}\n{M('raccolta.consent_note')}", "options": None, "at": int(s.get("ask_after") or 0),
             "answer": {"op": "prima_raccolta_azione", "action": "consent", "value": "si"}, "_short": q}]


def _canon(name, alias=None, names=()):
    """1.151: il nome della sessione come in state.sessions[].name: quello esatto; l'alias del relay («win:x» per un
    compito registrato per «x»); il nome senza il prefisso dell'account; il solo nome dello stato che finisce con «:x»."""
    if not name or name in names:
        return name
    if alias and name in alias:
        return alias[name]
    s = _short(name)
    if s in names:
        return s
    hits = [n for n in names if str(n).split(":", 1)[-1] == s]
    return hits[0] if len(hits) == 1 else s


def _last_state():
    try:
        last = json.loads((Path(cm.expand((CFG.get("relay") or {}).get("dir") or "~/.cc-supervisor/relay")) / "last-state.json").read_text())
    except (OSError, ValueError):
        return {}
    return last.get("full") or last.get("state") or {}


def _from_state(state):
    """Le domande in chat dallo stato del relay: AskUserQuestion, needs-you, un turno chiuso con una voce «!»."""
    sessions = {s.get("name"): s for s in state.get("sessions") or []}
    out = []
    for x in state.get("attention") or []:
        i = wid_attention(x)
        if not i:
            continue
        k = {"turn_blocking": "prossimi", "quota_limit": "limite", "turn_failed": "turno", "task_red": "rosso"}.get(x["kind"], x["kind"])
        s = sessions.get(x.get("session")) or {}
        a = {"id": i, "type": "chat", "kind": k, "session": x.get("session"), "task": None, "text": x.get("title"), "options": None, "at": x.get("since")}
        if k == "question":
            q = s.get("question") or {}
            opts = [o if isinstance(o, dict) else {"label": str(o)} for o in (q.get("options") or []) if o]
            # l'op answer vuole il numero dell'opzione in arg e l'id della domanda in question (contratto 1.55)
            a.update(options=opts or None, answer={"op": "answer", "session": x.get("session"), "question": x.get("ref")})
        elif k == "turno":   # 1.151: il turno fallito, «Continua» come il tasto dell'evento turn_failed
            a["answer"] = {"op": "prompt", "session": x.get("session"), "arg": M("relay.continue_text")}
        elif k == "rosso":   # 1.151: il compito rosso della guardia (ref = il compito)
            a.update(type="chat", task=x.get("ref"), answer={"op": "attention_done", "arg": x.get("ref"), "kind": "task_red"})
        elif k == "limite":
            # 16/11/2026 (supervisor-blocco-permanente-16-11): una sessione ferma su un limite, coi tasti del quota_limit
            # (le strade nell'ordine dell'evento) e «Fatto»; il titolo e' quello chiaro del relay
            a.update(answer={"op": "quota_route", "session": x.get("session"), "arg": (x.get("actions") or ["at_reset"])[0]},
                     _routes=list(x.get("actions") or []), _other_model=x.get("other_model"))
        elif k == "prossimi":
            a.update(options=[st["text"] for st in s.get("next_steps") or [] if st.get("text")] or None,
                     answer={"op": "prompt", "arg": x.get("session"), "done": {"op": "attention_done", "arg": x.get("session"), "kind": "turn_blocking"}})
        else:
            a["answer"] = {"op": "attention_done", "arg": x.get("session"), "kind": "needs_you"}
        out.append(a)
    return out


LABELS = {"ok": "decide", "pausa": "decide", "login": "decide", "limite": "decide", "turno": "decide", "raccolta": "decide", "nonva": "verify", "rosso": "verify", "choice": "choose", "prova": "verify", "prossimi": "reply", "question": "reply", "needs_you": "reply"}


def _options(opts, cut=None):
    """[{n, label, value, recommended?, description?}]: label pulita (senza «(consigliata)»), value = quello che l'op di
    risposta vuole: l'etichetta com'e' scritta nella richiesta; per una carta di scelta (cut=24) come la confronta approve
    (approvals_list: una riga, 24 caratteri)."""
    out = []
    for i, o in enumerate(opts or []):
        if isinstance(o, dict):   # una domanda dello stato: gia' {n, label, recommended?, description?}
            x = dict(o, n=i + 1, value=o.get("label"))
        else:
            x = dict(S.option_of(i, str(o), {}), value=S.one_line(str(o))[:cut] if cut else str(o))
        out.append(x)
    return out or None


DEPLOY_RE = re.compile(r"\b(prod|production|produzione|deploy|live)\b", re.I)   # come cm-relay.DEPLOY_RE (approvals[].deploy)
TITLE_MAX = 120


def _act(label_key, cmd, style="secondary", text_field=None, **extra):
    """Un tasto della scheda: {label, style, cmd, text_field?, ...}. cmd e' il comando del relay gia' pronto (op, session,
    arg e i campi dell'op); l'app aggiunge id, issued, by e device. text_field: il campo di cmd che la persona scrive
    (il motivo di un «Non va», la risposta libera); assente = il tasto manda cmd com'e'."""
    out = {"label": M("attese.act_" + label_key) if label_key else extra.pop("label"), "style": style, "cmd": dict({"session": None}, **cmd)}
    if text_field:
        out["text_field"] = text_field
    out.update(extra)
    return out


def _actions(a, prova=False):
    """I tasti dell'attesa; 26/11/2026 (supervisor-standby-26-11, contratto 1.163): su una voce di un compito (ok, scelta, pausa, prova) anche
    «Stand by», l'op standby con on true: l'app resta generica."""
    out = _actions_of(a, prova)
    if a.get("task") and a["kind"] in ("ok", "choice", "pausa", "prova"):
        out.append(_act("standby", {"op": "standby", "arg": a["task"], "on": True}))
    return out


def _actions_of(a, prova=False):
    """1.131 (07/11, supervisor-attese-campi-07-11, chiesto da supervisor-app): i tasti espliciti dell'attesa, nell'ordine
    in cui la scheda li mostra; le opzioni prima, la consigliata col segno. [] per il login (lo rifa' la master)."""
    k, an, opts = a["kind"], a.get("answer") or {}, a.get("options") or []
    rec = lambda o: {"recommended": True} if o.get("recommended") else {}  # noqa: E731
    # 1.151: «Non ora» (24 ore) e «Lascia perdere» (col motivo) del gruppo di decisione, come nell'op decisioni
    key = (a.get("decision") or {}).get("key")
    later = [_act("not_now", {"op": "decisione", "arg": key, "action": "not_now", "hours": 24})] if key else []
    drop = [_act("drop", {"op": "decisione", "arg": key, "action": "drop"}, text_field="reason")] if key else []
    if k == "ok":
        return _actions_ok(a) + later + drop
    if k == "choice":
        return _actions_choice(a, opts, rec) + later + drop
    if k == "login":
        return later
    if k == "nonva":
        n = str((a.get("tests") or [{}])[0].get("id") or an.get("arg") or "")
        return [_act("now_works", {"op": "prova", "arg": n, "result": "ok"}, "primary"), _act("retire", {"op": "prova", "arg": n, "result": "ritirata"})]
    if k == "rosso":
        return [_act("done", {"op": "attention_done", "arg": a.get("task"), "kind": "task_red"})] if a.get("type") == "chat" else []
    if k == "turno":
        return [_act("continue", {"op": "prompt", "session": a.get("session"), "arg": an.get("arg")}, "primary"),
                _act("done", {"op": "attention_done", "arg": a.get("session"), "kind": "turn_failed"})]
    if k == "raccolta":
        return [_act(None, {"op": "prima_raccolta_azione", "action": "consent", "value": "si"}, "primary", label=M("raccolta.consent_yes")),
                _act(None, {"op": "prima_raccolta_azione", "action": "consent", "value": "later"}, label=M("raccolta.consent_later"))]
    if k == "needs_you" and key:
        done = {"op": "attention_done", "arg": a.get("session"), "kind": "needs_you"}
        return [_act(None, {"op": "prompt", "session": a.get("session"), "arg": o["value"]}, "option", label=o["label"]) for o in opts] + \
               [_act("reply", {"op": "prompt", "session": a.get("session")}, text_field="arg"), _act("done", done)] + later + drop
    return _actions_rest(a, k, an, opts, rec, prova)


def _actions_ok(a):
    # 1.149: il consiglio segna il tasto consigliato, «Approva» o, se chi chiede consiglia di no, «Chiudi senza ok»
    adv = a.get("advice") or {}
    yes, no = ({"recommended": True} if adv.get("approve") is True else {}), ({"recommended": True} if adv.get("approve") is False else {})
    return [_act("approve", {"op": "approve", "arg": a["task"], "text": "ok"}, "primary", **yes),
            _act("close", {"op": "approval_close", "arg": a["task"]}, text_field="text", **no)]


def _actions_choice(a, opts, rec):
    # 1.137: «Nessuna, rifare» col motivo, prima di «Chiudi senza ok»
    return [_act(None, {"op": "approve", "arg": a["task"], "text": "ok", "choice": o["value"]}, "primary" if o.get("recommended") else "option", label=o["label"], **rec(o))
            for o in opts] + [_act("redo", {"op": "approval_redo", "arg": a["task"]}, text_field="text"),
                              _act("close", {"op": "approval_close", "arg": a["task"]}, text_field="text")]


def _actions_rest(a, k, an, opts, rec, prova):
    if k == "prova":
        ids = str(an.get("arg") or "")
        return [_act("works", {"op": "prova", "arg": ids, "result": "ok"}, "primary"),
                _act("fails", {"op": "prova", "arg": ids.split(",")[0], "result": "ko"}, text_field="text")]
    if k == "pausa":
        # una pausa che aspetta una prova: «Va» (la conferma della persona) e «Non va»; le altre (un accesso, una
        # decisione a voce): la risposta alla sessione, «Non ora», «Lascia perdere» con il motivo
        key = an.get("arg")
        if prova:
            return [_act("works", {"op": "decisione", "arg": key, "action": "provato"}, "primary"),
                    _act("fails", {"op": "decisione", "arg": key, "action": "non_va"}, text_field="reason"),
                    _act("not_now", {"op": "decisione", "arg": key, "action": "not_now"})]
        # 1.163: «Ok», la risposta della persona come `task ok` (prima solo «Rispondi», un messaggio alla sessione: l'ok non
        # si registrava)
        return [_act("ok", {"op": "decisione", "arg": key, "action": "ok"}, "primary")] + \
               ([_act("reply", {"op": "prompt", "session": a.get("session")}, text_field="arg")] if a.get("session") else []) + \
               [_act("not_now", {"op": "decisione", "arg": key, "action": "not_now"}),
                _act("drop", {"op": "decisione", "arg": key, "action": "drop"}, text_field="reason")]
    if k == "question":
        return [_act(None, {"op": "answer", "session": a.get("session"), "arg": str(o["n"]), "question": an.get("question")}, "primary" if o.get("recommended") else "option",
                     label=o["label"], **rec(o)) for o in opts]
    if k == "limite":
        # 16/11 (supervisor-blocco-permanente-16-11): una strada per tasto (quota_route), la prima e' quella in evidenza
        out = []
        for r in a.pop("_routes", None) or []:
            label = {"other_model": M("attese.act_other_model", model=a.get("_other_model") or "Opus"), "other_account": M("attese.act_other_account"),
                     "at_reset": M("attese.act_at_reset")}.get(r)
            if label:
                out.append(_act(None, {"op": "quota_route", "session": a.get("session"), "arg": r}, "secondary" if out else "primary", label=label))
        a.pop("_other_model", None)
        return out + [_act("done", {"op": "attention_done", "arg": a.get("session"), "kind": "quota_limit"})]
    if k in ("prossimi", "needs_you"):
        done = {"op": "attention_done", "arg": a.get("session"), "kind": "turn_blocking" if k == "prossimi" else "needs_you"}
        return [_act(None, {"op": "prompt", "session": a.get("session"), "arg": o["value"]}, "option", label=o["label"]) for o in opts] + \
               [_act("reply", {"op": "prompt", "session": a.get("session")}, text_field="arg"), _act("done", done)]
    return []


def _option_details(options, details):
    """Le opzioni (gia' oggetti) con la riga di pro e contro (`description`) e i file di ciascuna, per indice."""
    for i, o in enumerate(options or []):
        d = (details or [])[i] if details and i < len(details) else {}
        if d.get("description"):
            o["description"] = d["description"]
        o.setdefault("description", None)
        o["files"] = [file_item(f) for f in d.get("files") or []] or None
    return options


def advice_view(adv, options):
    """1.149 (16/11/2026): il consiglio di chi chiede, come lo vede l'app: {text, option, approve} o null. `text` la
    motivazione in una riga; `option` il value dell'opzione consigliata di una scelta (null per un ok); `approve` true o
    false per un ok (null per una scelta). Segna anche le opzioni: recommended true sulla consigliata e solo su quella."""
    if not isinstance(adv, dict) or not adv.get("text"):
        return None
    out = {"text": S.one_line(str(adv["text"]))[:200], "option": None, "approve": adv.get("approve") if not options else None}
    if options and adv.get("option"):
        want = S.one_line(str(adv["option"]))[:24]
        hit = next((o for o in options if o.get("value") == want), None)
        if hit is not None:
            out["option"] = hit["value"]
            for o in options:
                if o is hit:
                    o["recommended"] = True
                else:
                    o.pop("recommended", None)
    return out


def round_view(g):
    """Un giro della scheda come lo vede l'app: {id, at, text, description, options, files, advice, state, reason, by, answered_at, choice}."""
    opts = _options(g.get("options"), 24) if g.get("options") else None
    det = g.get("detail") or {}
    adv = advice_view(g.get("advice"), opts)
    return {"id": g["wid"], "at": g["at"], "text": g.get("what"), "description": det.get("description"), "options": _option_details(opts, det.get("options")), "advice": adv,
            "files": [file_item(f) for f in g.get("files") or []] or None, "state": g["state"], "reason": g.get("reason"), "by": g.get("by"),
            "answered_at": g.get("answered_at"), "choice": g.get("choice")}


def task_of_round(i):
    """20/11 (app-bozze-link-19-11): il compito del giro con questo id (archivio del registro), o None."""
    try:
        tk = _load("cm-tasks")
        con = tk.connect(create=False)
    except Exception:   # noqa: BLE001
        return None
    if con is None:
        return None
    try:
        return tk.find_round(con, i)
    finally:
        con.close()


def card_id_of(i):
    """20/11 (app-bozze-link-19-11): il card_id (1.137) della carta che contiene il giro `i`, o None (non e' un giro)."""
    try:
        tk = _load("cm-tasks")
        con = tk.connect(create=False)
    except Exception:   # noqa: BLE001
        return None
    if con is None:
        return None
    try:
        tid = tk.find_round(con, i)
        card = tk.card_of(con, tid, i) if tid else None
        return card["card_id"] if card else None
    finally:
        con.close()


def card_rounds(a):
    """I giri della scheda prima di quello della voce, dal piu' vecchio ([] al primo giro, e per le voci che non sono carte)."""
    if a.get("type") != "carta" or a.get("kind") not in ("ok", "choice") or not a.get("task"):
        return []
    tk = _load("cm-tasks")
    con = tk.connect(create=False)
    if con is None:
        return []
    try:
        card = tk.card_of(con, a["task"], a["id"])
    finally:
        con.close()
    return [round_view(g) for g in (card or {}).get("rounds", []) if g["wid"] != a["id"]]


def archived(i):
    """1.137 (archivio): la scheda di qualsiasi giro, anche dopo la risposta. Il giro piu' recente della scheda fa da voce:
    aperto = la voce di collect con i giri di prima; chiuso = la voce ricostruita dal registro, actions [] e `answered`.
    None se l'id non e' di nessun giro."""
    tk = _load("cm-tasks")
    con = tk.connect(create=False)
    if con is None:
        return None
    try:
        tid = tk.find_round(con, i)
        card = tk.card_of(con, tid, i) if tid else None
        row = con.execute("SELECT title, session, contract FROM tasks WHERE id=?", (tid,)).fetchone() if tid else None
    finally:
        con.close()
    if not card:
        return None
    last = card["rounds"][-1]
    if last["state"] == "open":
        a = find(last["wid"])
        if a is not None:
            a["rounds"] = [round_view(g) for g in card["rounds"][:-1]]
            return a
    v = round_view(last)
    try:
        short = tk.short_of(json.loads(row["contract"]).get("task") or {}) if row else None
    except (ValueError, TypeError, AttributeError):
        short = None
    a = {"id": last["wid"], "type": "carta", "kind": "choice" if last.get("options") else "ok", "session": _short(row["session"]) if row and row["session"] else None,
         "task": tid, "text": v["text"], "options": v["options"], "at": last["at"], "answer": None,
         "card": {k: last.get(f) for k, f in (("what", "what"), ("where", "where"), ("untested", "untested"), ("undo", "undo"), ("undo_how", "undo_how")) if last.get(f)} or None,
         "link": link(last["wid"]), "files": v["files"], "description": v["description"], "advice": v["advice"], "card_id": card["card_id"], "state": last["state"],
         "answered": {"by": last.get("by"), "at": last.get("answered_at"), "choice": last.get("choice"), "reason": last.get("reason")} if last["state"] != "replaced" else None,
         "rounds": [round_view(g) for g in card["rounds"][:-1]]}
    a["label"] = LABELS.get(a["kind"], "decide")
    a["label_text"] = M("attese.label_" + a["label"])
    if a["card"] is not None:
        a["card"]["deploy"] = bool(DEPLOY_RE.search(f"{a['card'].get('what') or ''} {a['card'].get('where') or ''}"))
    a["title"] = (" ".join(str(short or v["text"] or "").split())[:TITLE_MAX]) or None
    a["actions"] = []
    for k_ in ("decision", "plan_start", "asks", "where", "tests", "build", "precedent", "also"):   # 1.151: chiusa, i campi ci sono, null
        a[k_] = None
    return a


def _finish(a):
    """L'etichetta del tipo (label: decide | choose | verify | reply, e label_text nella lingua della config, «Decidi»,
    «Scegli», «Verifica», «Rispondi»), le opzioni come oggetti, card null fuori dalle carte di ok. 1.131: `title`, una riga
    per gli elenchi (il nome breve del compito per le carte, la domanda per la chat), card.deploy (la cosa o il posto
    nominano la produzione, come approvals[].deploy) e `actions`."""
    # 1.151: una pausa che aspetta una prova sta in Verifica, come contava state.tocca (prove)
    a["label"] = "verify" if a["kind"] == "pausa" and a.get("_prova") else LABELS.get(a["kind"], "decide")
    a["label_text"] = M("attese.label_" + a["label"])
    a["options"] = _options(a.get("options"), 24 if a["kind"] == "choice" else None)
    for k_ in ("decision", "plan_start", "asks", "where", "tests", "build"):   # 1.151: sempre presenti, null quando non servono
        a.setdefault(k_, None)
    a.setdefault("card", None)
    a.setdefault("files", None)   # 1.137: [{path, name, mime, size, kind, session}] o null; la lettura `file` li scarica (arg = path)
    if a["files"]:
        a["files"] = [file_item(f) for f in a["files"]]
    # 1.137 (la scheda di scelta): descrizione, riga e file di ogni opzione, id stabile della scheda, stato e risposta
    card = a.pop("_card", None)
    cur = (card or {}).get("rounds", [None])[-1] if card else None
    a["description"] = ((cur or {}).get("detail") or {}).get("description")
    _option_details(a["options"], ((cur or {}).get("detail") or {}).get("options"))
    # 1.149: il consiglio di chi chiede (null fuori dalle carte e per quelle nate prima); segna l'opzione o il tasto
    a["advice"] = advice_view(a.pop("_advice", None), a["options"]) if a["kind"] in ("ok", "choice") else None
    a["card_id"] = card["card_id"] if card else a["id"]
    a.setdefault("state", "open")
    a.setdefault("answered", None)
    if a["card"] is not None:
        a["card"]["deploy"] = bool(DEPLOY_RE.search(f"{a['card'].get('what') or ''} {a['card'].get('where') or ''}"))
    short = a.pop("_short", None)
    t = short if a["type"] == "carta" and short else a.get("text")
    a["title"] = (" ".join(str(t or "").split())[:TITLE_MAX]) or None
    a["actions"] = _actions(a, a.pop("_prova", False))
    return a


def collect(now_=None, state=None, alias=None, registry=None):
    """Tutte le attese aperte, una per id, dalla piu' vecchia. Il registro prima: una needs-you del registro porta il
    compito, quella dello stato il nome della sessione come lo vede l'app. 1.151: `alias` (del relay, state_alias) e i
    nomi dello stato danno a ogni voce la sessione col nome dello stato; `registry` le voci del registro gia' lette (il
    relay le tiene se il registro non si legge); la Prima raccolta; il compito rosso della guardia (attention task_red)
    sulla voce rossa del registro dello stesso compito, col suo «Fatto»."""
    now_ = int(now_ or time.time())
    state = _last_state() if state is None else state
    if registry is None:
        try:
            registry = registry_items(now_)
        except Exception:   # noqa: BLE001 — senza registro restano le domande in chat
            registry = []
    names = {s_.get("name") for s_ in state.get("sessions") or [] if s_.get("name")}
    seen = {}
    red = {}
    for a in [dict(x) for x in registry] + raccolta_items(now_) + _from_state(state):
        if a["id"] in seen:
            if a["kind"] == "needs_you":   # lo stato sa il nome breve, il registro il compito
                seen[a["id"]]["session"] = _canon(a["session"], alias, names) or seen[a["id"]]["session"]
            continue
        if a["kind"] == "rosso" and a.get("task") in red:   # la guardia e le prove dicono lo stesso compito rosso
            red[a["task"]]["actions"].append(_act("done", {"op": "attention_done", "arg": a["task"], "kind": "task_red"}))
            continue
        a["session"] = _canon(a["session"], alias, names) if a.get("session") else None
        a["link"] = link(a["id"])
        _finish(a)
        a["link"] = link(a.get("card_id") or a["id"])   # 20/11 (app-bozze-link-19-11): lo stesso link fra i giri della carta
        seen[a["id"]] = a
        if a["kind"] == "rosso" and a.get("task"):
            red[a["task"]] = a
    return sorted(seen.values(), key=lambda a: (int(a.get("at") or 0), a["id"]))


# ------------------------------------------------------------------ da dove viene
def _anchor_re(a):
    """La chiamata che ha aperto l'attesa, nel testo della voce «tool» (il comando, i primi 200 caratteri)."""
    t = re.escape(str(a.get("task") or ""))
    if a["kind"] in ("ok", "choice"):
        return re.compile(rf"\btask\s+wait-ok\s+{t}\b")
    if a["kind"] == "pausa":
        return re.compile(rf"\btask\s+pause\s+{t}\b")
    if a["kind"] == "prova":
        return re.compile(rf"\btask\s+prova\s+(?:add\s+)?{t}\b")
    if a["kind"] == "needs_you":
        return re.compile(r"\bsupervisor\s+needs-you\s+(?!--clear)")
    return None


def origin_in(entries, a):
    """La voce che spiega l'attesa, tra le voci di una trascrizione (core.transcript_entries), o None.
    prossimi: l'ultimo testo dell'assistente con «Prossimi»; question: la chiamata AskUserQuestion; le altre: il primo
    testo dell'assistente dopo la chiamata che le ha aperte (la risposta che chiude quel turno), altrimenti l'ultimo
    prima."""
    texts = [k for k, e in enumerate(entries) if e.get("role") == "assistant" and e.get("text")]
    if a["kind"] == "prossimi":
        k = next((k for k in reversed(texts) if re.search(r"(?im)^\W*(prossimi|next)\s*:", entries[k]["text"])), texts[-1] if texts else None)
        return entries[k] if k is not None else None
    if a["kind"] == "question":
        k = next((k for k in range(len(entries) - 1, -1, -1) if entries[k].get("tool") == "AskUserQuestion"), None)
        return entries[k] if k is not None else None
    rx = _anchor_re(a)
    if rx is None:
        return None
    # 1.137 (12/11, supervisor-attesa-allegati-12-11): una richiesta con file viene dal messaggio che li ha mandati (la
    # voce che li elenca nei suoi files, SendUserFile o una scrittura): il testo dell'assistente in quel turno
    # (12/11 15:00, la master: la scheda di app-mockup-sposta-master-12-11 mostrava il testo delle 14:42 invece della
    # domanda delle 14:41) la domanda era la didascalia di SendUserFile, una voce «tool»: anche quella e' un messaggio
    # alla persona, e se manda i file della richiesta e' lei la voce d'origine
    msgs = sorted(set(texts) | {k for k, e in enumerate(entries) if e.get("tool") == "SendUserFile" and e.get("text")})
    want = {os.path.realpath(str(f.get("path"))) for f in a.get("files") or [] if isinstance(f, dict) and f.get("path")}
    want_names = {os.path.basename(p) for p in want}
    if want:
        f = next((k for k in range(len(entries) - 1, -1, -1)
                  if any(os.path.realpath(str(x.get("path") or "")) in want or os.path.basename(str(x.get("path") or "")) in want_names
                         for x in entries[k].get("files") or [] if isinstance(x, dict))), None)
        if f is not None:
            return entries[f] if f in msgs else _turn_text(entries, msgs, f, a)
    k = next((k for k in range(len(entries) - 1, -1, -1) if entries[k].get("role") == "tool" and rx.search(str(entries[k].get("text") or ""))), None)
    if k is None:
        return None
    return _turn_text(entries, msgs, k, a)


def _turn_text(entries, texts, k, a):
    """Il testo (dell'assistente, o la didascalia di SendUserFile) che spiega l'attesa nel turno della voce k. Prima (12/11) era il primo testo dopo k: per
    una carta della master era spesso una riga di passaggio («Ho messo le scelte nel registro…»). Ora, fra i testi del
    turno (dalla riga della persona prima di k a quella dopo), quello con piu' parole in comune con la domanda e le sue
    opzioni; a pari merito, o senza parole in comune, il primo dopo k, altrimenti l'ultimo prima."""
    lo = next((j for j in range(k, -1, -1) if entries[j].get("role") == "user"), -1)
    hi = next((j for j in range(k + 1, len(entries)) if entries[j].get("role") == "user"), len(entries))
    turn = [j for j in texts if lo < j < hi]
    after = [j for j in turn if j > k]
    first = after[0] if after else next((j for j in reversed(texts) if j < k), None)
    want = {w.casefold() for w in _WORD_RE.findall(" ".join([str(a.get("text") or "")] + [str(o.get("label") if isinstance(o, dict) else o) for o in a.get("options") or []]))}
    if want and turn:
        score = lambda j: len(want & {w.casefold() for w in _WORD_RE.findall(str(entries[j].get("text") or ""))})  # noqa: E731
        best = max(turn, key=lambda j: (score(j), j == first, -abs(j - k)))
        if score(best) > (score(first) if first is not None and first in turn else 0):
            return entries[best]
    return entries[first] if first is not None else None


_SENT_SPLIT = re.compile(r"(?<=[.?!])\s+|\n+")   # la fine di frase e' un segno seguito da uno spazio: «1.0» resta intera
_WORD_RE = re.compile(r"\w{4,}")


def key_sentence(text, a):
    """La frase chiave del messaggio d'origine, da evidenziare: la frase con piu' parole in comune con la domanda e le sue
    opzioni; a pari merito quella con il «?»; nessuna parola in comune = la prima domanda, altrimenti la prima frase."""
    sents = [" ".join(x.split()) for x in _SENT_SPLIT.split(str(text or "")) if x.strip(" -*·")]
    sents = [x for x in sents if not re.match(r"(?i)^\W*(prossimi|next|tipo|type|watch|esito|outcome)\s*:", x)] or sents
    if not sents:
        return None
    want = {w.casefold() for w in _WORD_RE.findall(" ".join([str(a.get("text") or "")] + [str(o.get("label") if isinstance(o, dict) else o) for o in a.get("options") or []]))}
    score = lambda x: (len(want & {w.casefold() for w in _WORD_RE.findall(x)}), x.endswith("?"))  # noqa: E731
    best = max(sents, key=score)
    if score(best)[0] == 0:
        best = next((x for x in sents if x.endswith("?")), sents[0])
    return best[:300]


def _rows():
    """Le righe del registro delle sessioni, senza leggere gli schermi."""
    try:
        return _load("cm-sessions").collect(read_screen=False)
    except Exception:   # noqa: BLE001 — senza registro niente trascrizione: msg null
        return []


_CORE = []


def _core():
    if not _CORE:
        _CORE.append(_load("cm-core"))
    return _CORE[0]


def origin(a, rows=None):
    """{session_id, id, text, at} della voce che spiega l'attesa: prima nella trascrizione della sua sessione, poi in
    quella della master (le carte chieste dalla master per un'altra sessione). None se non si trova."""
    core = _core()   # 17/11: caricato una volta, non a ogni voce aperta
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    rows = rows if rows is not None else _rows()
    want = [a.get("session"), master] if a.get("session") != master else [master]
    for name in [w for w in want if w]:
        row = next((r for r in rows if name in (r.get("name"), r.get("tmux"), _short(r.get("name") or r.get("tmux") or ""))), None)
        path = core.transcript_of(row) if row else None
        if not path:
            continue
        try:
            size = Path(path).stat().st_size
            entries = core.transcript_entries(path, max(0, size - TAIL_BYTES))
        except OSError:
            continue
        e = origin_in(entries, a)
        if e:
            text = str(e.get("text") or "")
            return {"session": _short(name), "session_id": row.get("session_id"), "id": e["id"], "uuid": str(e["id"]).split(".")[0],
                    "text": text[:MSG_MAX], "key": key_sentence(text, a), "at": e.get("at"),
                    "_files": [f for f in e.get("files") or [] if isinstance(f, dict) and f.get("path")]}
    return None


def file_item(f, session=None):
    """1.137: un file da guardare {path, name, mime, size, kind, session}: session null = dichiarato nella richiesta
    (`file` lo serve senza sessione), altrimenti la sessione nella cui trascrizione compare (`file` con quella sessione)."""
    import mimetypes
    p = str(f.get("path"))
    mime = f.get("mime") or mimetypes.guess_type(p)[0] or "application/octet-stream"
    ext = p.rsplit(".", 1)[-1].lower() if "." in p else ""
    kind = f.get("kind") or ("image" if mime.startswith("image/") else "document" if (mime.startswith("text/") or mime in ("application/pdf", "application/json")
                                                                                      or ext in ("pdf", "md", "html", "htm", "txt", "csv", "doc", "docx", "odt", "rtf", "xls", "xlsx", "ods", "ppt", "pptx", "odp", "svg")) else "other")
    return {"path": p, "name": f.get("name") or re.split(r"[\\/]", p)[-1], "mime": mime, "size": f.get("size"), "kind": kind, "session": session}


def with_files(a, msg):
    """La voce con i suoi file: quelli dichiarati (session null); senza, quelli del messaggio d'origine (SendUserFile, una
    scrittura), con la sessione della sua trascrizione. Toglie il campo interno di msg."""
    got = (msg or {}).pop("_files", None) if isinstance(msg, dict) else None
    if a.get("files"):
        a["files"] = [file_item(f) for f in a["files"]]
    elif got:
        a["files"] = [file_item(f, msg.get("session")) for f in got]
    return a


def find(i, now_=None, state=None):
    return next((a for a in collect(now_, state) if a["id"] == i), None)


# ------------------------------------------------------------------ CLI
def main(argv):
    as_json = "--json" in argv
    args = [x for x in argv if x != "--json"]
    if args and args[0] in ("show", "mostra"):
        if len(args) != 2 or not ID_RE.fullmatch(args[1]):
            print(M("attese.usage"), file=sys.stderr)
            return 2
        a = find(args[1])
        if a is None:
            print(M("attese.gone", id=args[1]), file=sys.stderr)
            return 1
        a["msg"] = origin(a)
        with_files(a, a["msg"])
        if as_json:
            print(json.dumps(a, ensure_ascii=False, indent=2))
        else:
            print(M("attese.line", kind=a["kind"], session=a.get("session") or "-", text=a.get("text") or "", link=a.get("link") or a["id"]))
            if a["msg"]:
                print(M("attese.msg", id=a["msg"]["id"], text=a["msg"]["key"] or a["msg"]["text"]))
        return 0
    if args:
        print(M("attese.usage"), file=sys.stderr)
        return 2
    items = collect()
    if as_json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    if not items:
        print(M("attese.none"))
        return 0
    for a in items:
        print(M("attese.line", kind=a["kind"], session=a.get("session") or "-", text=" ".join(str(a.get("text") or "").split())[:120], link=a.get("link") or a["id"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
