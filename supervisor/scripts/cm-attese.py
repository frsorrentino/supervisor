#!/usr/bin/env python3
"""supervisor attese — ogni domanda che aspetta la persona, con un id stabile e un link (07/11/2026, contratto 1.130,
supervisor-attese-link-07-11).

  supervisor attese [--json]          le attese aperte, dalla piu' vecchia, ognuna col suo link
  supervisor attese show ID [--json]  una sola, con il messaggio della trascrizione dove e' stata spiegata

Un'attesa e' {id, type, kind, session, task, text, options, at, link, answer}:
  type   carta (dal registro: ok, scelta, pausa che aspetta la persona, prova, login) | chat (domanda in chat: la riga
         «Prossimi» con una voce «!», needs-you, una domanda AskUserQuestion)
  kind   ok | choice | pausa | prova | login | prossimi | needs_you | question
  answer {op, arg, ...}: l'op del relay che risponde, la stessa dei tasti della carta (approve, prova, decisione,
         prompt, attention_done, answer); null per il login, che rifa' la master
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


def _from_registry(now_):
    """Le carte del registro: ok e scelte, pause che aspettano la persona, login (da tk.decisions), prove da fare."""
    tk = _load("cm-tasks")
    con = tk.connect(create=False)
    if con is None:
        return []
    out = []
    try:
        d = tk.decisions(con, now_)
        for g in d.get("groups") or []:
            for it in g.get("items") or []:
                k = it.get("kind")
                if k == "ok":
                    kind = "choice" if it.get("options") else "ok"
                    a = {"id": wid_approval(it["task"], it.get("since")), "type": "carta", "kind": kind, "session": it.get("session"), "task": it["task"],
                         "text": it.get("what") or it.get("title"), "options": it.get("options") or None, "at": it.get("since"),
                         "answer": {"op": "approve", "arg": it["task"]}}
                    # la carta del registro: cosa esce e dove, cosa non e' provato, se e come si torna indietro (wait-ok)
                    a["card"] = {k: it.get(f) for k, f in (("what", "what"), ("where", "where_to"), ("untested", "untested"), ("undo", "undo"), ("undo_how", "undo_how"))
                                 if it.get(f)} or None
                    out.append(a)
                elif k == "pause":
                    out.append({"id": wid("pausa", it["task"], it.get("since")), "type": "carta", "kind": "pausa", "session": it.get("session"), "task": it["task"],
                                "text": it.get("reason"), "options": None, "at": it.get("since"), "answer": {"op": "decisione", "arg": g["key"]}})
                elif k == "login":
                    out.append({"id": wid("login", it.get("account"), it.get("expires_at")), "type": "carta", "kind": "login", "session": it.get("session"), "task": None,
                                "text": it.get("reason"), "options": None, "at": it.get("since"), "answer": None})
                elif k == "needs_you":
                    out.append({"id": wid("needs_you", int(it.get("since") or 0)), "type": "chat", "kind": "needs_you", "session": it.get("session"),
                                "task": it.get("task"), "text": it.get("reason"), "options": None, "at": it.get("since"),
                                "answer": {"op": "attention_done", "arg": it.get("session"), "kind": "needs_you"}})
        p = tk.prove_list(con, now_)
        by_task = {}
        for gr in p.get("groups") or []:
            for x in gr.get("prove") or []:
                by_task.setdefault(x["task"], []).append(x)
        for tid, xs in by_task.items():
            ids = sorted(x["id"] for x in xs)
            out.append({"id": wid("prova", tid, ids[0]), "type": "carta", "kind": "prova", "session": xs[0].get("session"), "task": tid,
                        "text": "; ".join(f"{x['dove']}: {x['fai']}" for x in xs)[:600], "options": None, "at": min(int(x.get("at") or 0) for x in xs),
                        "answer": {"op": "prova", "arg": ",".join(map(str, ids))}})
    finally:
        con.close()
    return out


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
        k = {"turn_blocking": "prossimi"}.get(x["kind"], x["kind"])
        s = sessions.get(x.get("session")) or {}
        a = {"id": i, "type": "chat", "kind": k, "session": x.get("session"), "task": None, "text": x.get("title"), "options": None, "at": x.get("since")}
        if k == "question":
            q = s.get("question") or {}
            opts = [o if isinstance(o, dict) else {"label": str(o)} for o in (q.get("options") or []) if o]
            a.update(options=opts or None, answer={"op": "answer", "arg": x.get("ref"), "session": x.get("session")})
        elif k == "prossimi":
            a.update(options=[st["text"] for st in s.get("next_steps") or [] if st.get("text")] or None,
                     answer={"op": "prompt", "arg": x.get("session"), "done": {"op": "attention_done", "arg": x.get("session"), "kind": "turn_blocking"}})
        else:
            a["answer"] = {"op": "attention_done", "arg": x.get("session"), "kind": "needs_you"}
        out.append(a)
    return out


LABELS = {"ok": "decide", "pausa": "decide", "login": "decide", "choice": "choose", "prova": "verify", "prossimi": "reply", "question": "reply", "needs_you": "reply"}


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


def _finish(a):
    """L'etichetta del tipo (label: decide | choose | verify | reply, e label_text nella lingua della config, «Decidi»,
    «Scegli», «Verifica», «Rispondi»), le opzioni come oggetti, card null fuori dalle carte di ok."""
    a["label"] = LABELS.get(a["kind"], "decide")
    a["label_text"] = M("attese.label_" + a["label"])
    a["options"] = _options(a.get("options"), 24 if a["kind"] == "choice" else None)
    a.setdefault("card", None)
    return a


def collect(now_=None, state=None):
    """Tutte le attese aperte, una per id, dalla piu' vecchia. Il registro prima: una needs-you del registro porta il
    compito, quella dello stato il nome della sessione come lo vede l'app."""
    now_ = int(now_ or time.time())
    state = _last_state() if state is None else state
    seen = {}
    try:
        reg = _from_registry(now_)
    except Exception:   # noqa: BLE001 — senza registro restano le domande in chat
        reg = []
    for a in reg + _from_state(state):
        if a["id"] in seen:
            if a["kind"] == "needs_you":   # lo stato sa il nome breve, il registro il compito
                seen[a["id"]]["session"] = a["session"] or seen[a["id"]]["session"]
            continue
        a["session"] = _short(a["session"]) if a.get("session") else None
        a["link"] = link(a["id"])
        _finish(a)
        seen[a["id"]] = a
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
    k = next((k for k in range(len(entries) - 1, -1, -1) if entries[k].get("role") == "tool" and rx.search(str(entries[k].get("text") or ""))), None)
    if k is None:
        return None
    for j in range(k + 1, len(entries)):
        if entries[j].get("role") == "user":
            break
        if j in texts:
            return entries[j]
    return next((entries[j] for j in reversed(texts) if j < k), None)


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


def origin(a, rows=None):
    """{session_id, id, text, at} della voce che spiega l'attesa: prima nella trascrizione della sua sessione, poi in
    quella della master (le carte chieste dalla master per un'altra sessione). None se non si trova."""
    core = _load("cm-core")
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
                    "text": text[:MSG_MAX], "key": key_sentence(text, a), "at": e.get("at")}
    return None


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
