#!/usr/bin/env python3
"""Le funzioni PURE di cm-relay (fase 1 dell'app polso, design 12/09/2026 §1): da sorgenti gia' raccolte a
`/state` nella forma del contratto v1 (claude-master-watch/contract/state-*.json, copiato in
tests/fixtures/relay/), il diff fra due stati → `/events`, il tier di una domanda, il limite di 8 KB.
Niente I/O qui: cm-relay.py raccoglie e scrive, questo modulo costruisce. Testato in tests/relay-verify.py (R2, R3).

`src` (dict) per build_state:
  host, root (radice dei workspace), prefixes (prefissi tmux degli account), high_words
  rows: le sessioni (vive da `sessions --json` + sparite dalla fotografia): name, tmux, account, cwd, status
        (busy|idle|waiting|dead), waiting, session_id, link, attached, started_at (ms), visto_ts
  ledger: le righe del ledger (event, session_id, ts iso, last/tail/esito/watch)
  questions: {tmux: {kind, tool, text, options[etichette], asked_at}}
  quota: {account: {cinque_ore_pct, settimana_pct, reset_settimanale, vecchia}}
  projects: [{path, name, account}] · night: {queued, running, items} · recap: {date, items[{project, done, next}]}
  follow: nomi seguiti · awaiting: nomi in attesa di risposta a un prompt dal watch

Tempi di una sessione in `/state` (chiesto dall'app il 13/09/2026, qui perche' non si reinterpreti):
  `since`        la NASCITA della sessione (startedAt del registro) mentre lavora o e' ferma; l'istante della
                 domanda se aspetta; l'ultimo avvistamento se e' sparita. Non cambia a ogni cambio di stato.
  `turn_started` l'ultimo prompt (o ripresa), valorizzato SOLO mentre lo stato e' busy o awaiting: a turno
                 finito torna null.
  `outcome.at`   l'ultimo Stop: e' questo il movimento di una sessione ferma, e si aggiorna a ogni fine turno
                 perche' l'hook scrive una riga nel ledger ogni volta.
  Chi vuole «l'ultimo movimento» usa max(since, turn_started, outcome.at): non serve un campo in piu'.
  next: {tmux: «→ prossimo» dal recap del progetto} · tools: {tmux: tool in corso}
"""
import datetime as _dt
import json
import re

V = 1
ORDER = {"waiting": 0, "busy": 1, "awaiting": 1, "idle": 2, "gone": 3}
SHORT_MAX = 200   # 1.6: la riga «Watch:»/«Esito:» intera (a 60 il polso mostrava mezza frase; chiesto dall'app il 14/09)
KEEP_GONE = 3     # fit_state: le sessioni finite piu' recenti che restano quando lo stato non entra
PUBLISHED_MAX = 30   # 1.75: le pubblicazioni di oggi in /state; fit_state le scende a PUBLISHED_FIT, poi a 5
PUBLISHED_FIT = 10
RECAP_CUT = 80    # 1.7 fit_state: `done` e `next` del recap accorciati a fine parola invece di sparire per primi
HIGH_WORDS = ["rm -rf", "git push", "deploy", "DROP", "ssh", "sudo", "--force", "git reset --hard"]
LOW_TOOLS = {"Read", "Grep", "Glob", "WebFetch", "WebSearch", "LS", "TodoWrite"}
STATE_ICON = {"waiting": "❓", "busy": "▶", "awaiting": "▶", "idle": "✓", "gone": "✗"}
# contratto 1.1 (l'utente via master 12/09 16:27): il badge dell'orologio = forma dall'account, colore = quello della scheda
# del Terminale (cm-color), a prescindere dalla forma o dal cuore dell'emoji
COLORS = {"🟠": "#F5A623", "🟧": "#F5A623", "🧡": "#F5A623", "🟡": "#F4D03F", "🟨": "#F4D03F", "💛": "#F4D03F",
          "🔴": "#E74C3C", "🟥": "#E74C3C", "❤️": "#E74C3C", "❤": "#E74C3C", "🟢": "#2ECC71", "🟩": "#2ECC71", "💚": "#2ECC71",
          "🔵": "#3B82F6", "🟦": "#3B82F6", "💙": "#3B82F6", "🟣": "#9B59B6", "🟪": "#9B59B6", "💜": "#9B59B6",
          "⚪": "#BDC3C7", "⬜": "#BDC3C7", "🤍": "#BDC3C7", "🟤": "#8D6E63", "🟫": "#8D6E63", "🤎": "#8D6E63"}


def color_of(icon, colors=None):
    """«#RRGGBB» dell'emoji del badge (mappa fissa, o relay.colors), None se ignota o assente."""
    ic = str(icon or "").strip()
    return (colors or {}).get(ic) or COLORS.get(ic) or COLORS.get(ic.rstrip("\ufe0f")) or None


def epoch(ts):
    try:
        return int(_dt.datetime.fromisoformat(str(ts)[:19]).timestamp())
    except ValueError:
        return 0


SHARE_MAX_BYTES = 10000000   # 1.19: la busta cifrata di /share/<id> (lunghezza di `enc`), un file per comando; 07/10: da 1,5
# a 10 MB (sotto i 10.485.760 di una stringa RTDB), cioe' un file di circa 5,6 MB (due base64: enc ~ 16/9 del file)
SHARE_PARTS_MAX = 50 * 1024 * 1024   # 1.48: un file dal dispositivo a pezzi (/share/<id>/parts e meta)
SHARE_MULTI = 5   # 1.50: gli id di /share che un report solo puo' portare in `arg`, separati da virgola
FILE_ENC_MAX = 1500000   # 1.24: la busta di /file/<id> in un solo {v, enc}, verso telefono e orologio: resta 1,5 MB
EVENT_BODY_MAX = 4000   # 1.18: il testo di recap, night_report e quota (ripresa) negli eventi


def cut_lines(text, cap):
    """Il testo intero se ci sta; altrimenti le righe intere che stanno entro cap caratteri, senza «…». Una prima
    riga piu' lunga di cap si taglia a fine parola."""
    t = str(text or "").strip("\n")
    if len(t) <= cap:
        return t
    out = ""
    for line in t.split("\n"):
        nxt = line if not out else out + "\n" + line
        if len(nxt) > cap:
            break
        out = nxt
    return out or cut_words(t.split("\n", 1)[0], cap)


NIGHT_PROMPT_MAX = 160   # 1.17: il prompt di un lavoro della notte nello stato (intero nel file della coda)


def cut_words(text, cap):
    """Il testo su una riga, tagliato a fine parola entro cap caratteri, senza «…»; una parola sola piu' lunga si
    taglia a cap."""
    t = " ".join(str(text or "").split())
    if len(t) <= cap:
        return t
    head = t[:cap + 1]
    sp = head.rfind(" ")
    return (head[:sp] if sp > 0 else t[:cap]).rstrip()


def night_items(rows):
    """1.17: la coda di stanotte nell'ordine di esecuzione: {id, dir, name, prompt, added, started}. `added` e
    `started` in epoch (started null finche' il lavoro non parte). Il campo c'e' sempre, anche vuoto: all'app dice
    che il relay sa modificare la coda."""
    out = []
    for r in rows or []:
        d = str(r.get("dir") or "")
        out.append({"id": str(r.get("id") or ""), "dir": d, "name": str(r.get("name") or "") or d.rstrip("/").rsplit("/", 1)[-1],
                    "prompt": cut_words(r.get("prompt"), NIGHT_PROMPT_MAX),
                    "added": r["added"] if isinstance(r.get("added"), int) else (epoch(r.get("added")) or None),
                    "started": r["started"] if isinstance(r.get("started"), int) and r["started"] else None})
    return out


def strip_markdown(text):
    t = str(text or "")
    t = re.sub(r"`{1,3}", "", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?<!\w)[*_](.+?)[*_](?!\w)", r"\1", t)
    t = re.sub(r"^\s*#+\s*", "", t)
    return t.strip()


# 1.56 (14/10, dagli spunti della valutazione di Kaya): niente segreti sulla schermata di blocco. Le notifiche di
# telefono e orologio usano `preview`; il testo intero resta nei campi di sempre, cifrato, e l'app lo mostra aperta.
_SECRET_RES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"),   # intestazioni Authorization
    # chiave=valore e chiave: valore (variabili d'ambiente, JSON, URL), e le opzioni con lo spazio (--password x)
    re.compile(r"(?i)((?:[A-Za-z0-9_.-]*(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)s?[A-Za-z0-9_-]*)"
               r"[\"']?\s*[=:]\s*[\"']?|--?(?:pass(?:word|wd)?|secret|token|api[_-]?key|key|auth)\s+[\"']?)([^\s\"',;&|]{3,}[^\s\"',;&|.:!?)])"),   # il punto di fine frase resta
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),   # utente:password@ in un URL
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\bxox[abprs]-[A-Za-z0-9-]{10,}"
               r"|\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_-]{30,}|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9+/_-]{40,}={0,2}"),   # stringhe lunghe da chiave (hex, base64)
]
MASK = "•••"


def redact_preview(text):
    """Il testo con token, chiavi e password sostituiti da •••: per le anteprime delle notifiche, mai per il testo
    che l'app mostra aperta. Di proposito largo: un falso positivo nasconde una parola nell'anteprima, un falso
    negativo mette un segreto sulla schermata di blocco."""
    t = str(text or "")
    if not t:
        return t
    t = _SECRET_RES[0].sub(MASK, t)
    t = _SECRET_RES[1].sub(lambda m: m.group(1) + " " + MASK, t)
    t = _SECRET_RES[2].sub(lambda m: m.group(1) + MASK, t)
    for r in _SECRET_RES[3:]:
        t = r.sub(MASK, t)
    return t


def preview_of(title, body):
    """1.56: {"preview": {title, body}} di un evento senza segreti, per la notifica; {} se non c'e' niente da nascondere
    (l'app usa title e body: niente byte doppi nello stato)."""
    p = {"title": redact_preview(title), "body": redact_preview(body)}
    return {"preview": p} if (p["title"], p["body"]) != (title, body) else {}


def one_line(text):
    return " ".join(str(text or "").split())


def short_of(text, n=60):
    """≤ n caratteri senza segno di taglio: alla parola intera se possibile."""
    t = one_line(strip_markdown(text))
    if len(t) <= n:
        return t
    cut = t[:n]
    return cut[:cut.rfind(" ")].rstrip(" ,;:") if " " in cut[n // 2:] else cut


def watch_line(text):
    for l in reversed(str(text or "").splitlines()):
        m = re.match(r"^\s*\W*\s*watch\s*:\s*(.+)$", l, flags=re.I)
        if m:
            return strip_markdown(m.group(1))
    return ""


def without_watch(text):
    return "\n".join(l for l in str(text or "").splitlines() if not re.match(r"^\s*\W*\s*watch\s*:", l, flags=re.I)).strip()


def esito_line(text):
    for l in reversed(str(text or "").splitlines()):
        m = re.match(r"^\s*\W*\s*esito\s*:\s*(.+)$", l, flags=re.I)
        if m:
            return strip_markdown(m.group(1))
    return ""


def project_of(cwd, root):
    c, r = str(cwd or "").rstrip("/"), str(root or "").rstrip("/")
    if r and c.startswith(r + "/"):
        return c[len(r) + 1:]
    return c.rsplit("/", 1)[-1] if c else ""


def short_name(name, prefixes=()):
    for p in sorted((p for p in prefixes if p), key=len, reverse=True):
        if name.startswith(p):
            return name[len(p):]
    return name


def unique_names(rows, prefixes=()):
    """16/10/2026 (dal vivo il 15/10 alle 23:40: due `launch --bg` nella stessa cartella, due «pix-claude-plugins», e
    l'app in crash all'avvio sulla chiave doppia fino a mezzanotte): /state non pubblica mai due sessioni con lo stesso
    nome corto. Tiene il nome la viva prima della chiusa, poi quella in tmux, poi la piu' vecchia; le altre prendono -2,
    -3 come fa tmux, solo in `name`. `tmux` resta quello vero: un comando dal telefono raggiunge ancora una sessione in
    tmux; una --bg rinominata non si trova, e il relay lo dice."""
    def rank(ir):
        i, r = ir
        return (r.get("status") == "dead", not r.get("tmux"), int(r.get("started_at") or 0) or 1 << 62, i)
    seen = set()
    for _, r in sorted(enumerate(rows), key=rank):
        base = r.get("name") or r.get("tmux") or "?"
        nm, n = base, 2
        while short_name(nm, prefixes) in seen:
            nm, n = f"{base}-{n}", n + 1
        if nm != base:
            r["name"] = nm
        seen.add(short_name(nm, prefixes))
    return rows


def tier_of(kind, tool, text, high_words=None):
    """low | medium | high: parole pericolose nel testo → high; permessi su strumenti che scrivono e domande
    → medium; strumenti che leggono → low."""
    if kind != "permission":
        return "medium"   # una domanda o un piano: il testo puo' citare «deploy» senza essere un rm -rf
    t = str(text or "")
    for w in (high_words or HIGH_WORDS):
        if w.lower() in t.lower():
            return "high"
    if tool in LOW_TOOLS:
        return "low"
    return "medium"


def state_of(row, awaiting=()):
    if row.get("status") == "dead":
        return "gone"
    if row.get("waiting") or row.get("status") == "waiting":
        return "waiting"
    if (row.get("tmux") or row.get("name")) in awaiting:
        return "awaiting"
    if row.get("status") == "busy":
        return "busy"
    return "idle"


def order_sessions(sessions):
    return sorted(sessions, key=lambda s: (ORDER.get(s["state"], 9), s["name"]))


def _events_of(ledger, sid):
    return [r for r in ledger if r.get("session_id") == sid] if sid else []


NEXT_RE = re.compile(r"^\s*\W*\s*prossimi\s*:\s*(.*)$", re.I)
NEXT_MAX = 3


def next_steps(text):
    """1.38 (05/10/2026): le voci dell'ultima riga «Prossimi: a · !b · c» → [{text, blocking}], al massimo tre; un «!»
    davanti segna la voce che sblocca un lavoro fermo (un ok, una scelta) e non resta nel testo da mandare."""
    for line in reversed(str(text or "").splitlines()):
        m = NEXT_RE.match(strip_markdown(line))
        if m:
            out = []
            for it in m.group(1).split("·"):
                it = it.strip().strip("`").strip()
                blocking = it.startswith("!")
                it = it.lstrip("!").strip()
                if it:
                    out.append({"text": it, "blocking": blocking})
            return out[:NEXT_MAX]
    return []


DA_TE_RE = re.compile(r"^\s*[*_#>\s]*da te\b", re.I)
NUM_RE = re.compile(r"^\s*\d+[.)]\s+(.*\S)")


def da_te(text):
    """1.68 (14/10): la prima voce di un elenco numerato sotto una riga «Da te» (regola globale dell'utente: quello che
    deve fare fuori dalla chat, il piu' bloccante per primo), o ""."""
    lines = str(text or "").splitlines()
    for i, line in enumerate(lines):
        if DA_TE_RE.match(line):
            for nxt in lines[i + 1:i + 8]:
                m = NUM_RE.match(strip_markdown(nxt))
                if m:
                    return m.group(1).strip()
    return ""


def _unbang(text):
    """La riga «Prossimi:» senza i «!» davanti alle voci: un'app che legge ancora `outcome.full` manda il prompt pulito."""
    return "\n".join(re.sub(r"((?:^|·|:)\s*\**\s*`?)!+", r"\1", l) if NEXT_RE.match(strip_markdown(l)) else l for l in str(text).split("\n"))


def _outcome(events):
    stops = [r for r in events if r.get("event") == "stop"]
    if not stops:
        return None
    r = stops[-1]
    tail = str(r.get("tail") or r.get("last") or "")
    esito = str(r.get("esito") or "")
    full_src = tail if esito and esito.splitlines()[0] in tail else (esito + ("\n" if esito and tail else "") + tail)
    full = _unbang(strip_markdown(without_watch(full_src)))[:600]
    short = watch_line(str(r.get("watch") or "")) or watch_line(tail) or esito_line(esito) or esito_line(tail) or (full.splitlines() or [""])[-1]
    out = {"short": short_of(short, SHORT_MAX), "full": full, "at": epoch(r.get("ts"))}
    # 1.60 (14/10, chiesto dall'app per la 1.56): l'esito e' l'ultimo messaggio della sessione e puo' contenere un segreto;
    # la notifica dell'esito usa preview {short, full}, presente solo quando c'era qualcosa da nascondere
    pv = {"short": redact_preview(out["short"]), "full": redact_preview(out["full"])}
    if (pv["short"], pv["full"]) != (out["short"], out["full"]):
        out["preview"] = pv
    return out


def _finished(events):
    """1.37 (05/10): il compito della sessione e' chiuso — l'ultimo messaggio ha la riga «Esito:» e non la riga
    «Prossimi:» (regola 10 del kernel: senza un seguito naturale niente «Prossimi:»)."""
    stops = [r for r in events if r.get("event") == "stop"]
    if not stops:
        return False
    txt = str(stops[-1].get("tail") or stops[-1].get("last") or "")
    has_outcome = bool(stops[-1].get("esito")) or bool(esito_line(txt))
    return has_outcome and not re.search(r"(?mi)^\s*\W*\s*prossimi\s*:", txt)


def _turn_started(events, before=None):
    ts = [epoch(r.get("ts")) for r in events if r.get("event") in ("prompt", "start", "queue-pop")]
    if before is not None:
        ts = [t for t in ts if t <= before]
    return ts[-1] if ts else None


RECOMMENDED = re.compile(r"\s*\((?:recommended|consigliat[ao])\)\s*$", re.I)


def option_of(i, label, descriptions):
    """1.53 (14/10, dagli spunti della valutazione di Kaya): {n, label, description?, recommended?}. La spiegazione e' la
    description dell'opzione di AskUserQuestion (assente se non c'e'); «(Recommended)» in fondo all'etichetta diventa
    recommended: true e lascia l'etichetta pulita. Campi assenti = non scritti (lo stato ha un tetto di 8 KB)."""
    label = str(label)
    desc = descriptions.get(label) or descriptions.get(label.strip())
    clean = RECOMMENDED.sub("", label)
    if not desc:
        desc = next((d for lb, d in descriptions.items() if RECOMMENDED.sub("", lb) == clean), None)
    out = {"n": i + 1, "label": clean or label}
    if desc:
        out["description"] = " ".join(str(desc).split())
    if clean != label:
        out["recommended"] = True
    return out


def build_session(row, src):
    name = short_name(row.get("name") or row.get("tmux") or "?", src.get("prefixes") or ())
    tmux = row.get("tmux") or row.get("name") or ""
    st = state_of(row, src.get("awaiting") or ())
    events = _events_of(src.get("ledger") or [], row.get("session_id") or "")
    q = (src.get("questions") or {}).get(tmux) if st == "waiting" else None
    started = int(row["started_at"] / 1000) if row.get("started_at") else None
    if st == "waiting":
        asked = int((q or {}).get("asked_at") or 0) or started or 0
        since = asked
        turn = _turn_started(events, asked) if events else started
    elif st == "gone":
        since = int(row.get("visto_ts") or 0)
        turn = None
    else:
        since = started or (_turn_started(events) or 0)
        turn = _turn_started(events) if st in ("busy", "awaiting") else None
    question = None
    if q:
        options = [option_of(i, o, q.get("descriptions") or {}) for i, o in enumerate(q.get("options") or [])]
        kind = q.get("kind") or ("ask" if (q.get("tool") or "") == "AskUserQuestion" else "plan" if q.get("tool") == "ExitPlanMode" else "permission")
        question = {"id": f"q-{asked}-1", "kind": kind, "text": one_line(q.get("text") or ""), "options": options,

                    "tier": tier_of(kind, q.get("tool"), (q.get("text") or "") + " " + str(q.get("detail") or ""), src.get("high_words")),
                    "asked_at": asked}
        pv = redact_preview(question["text"])
        if pv != question["text"]:
            question["preview"] = pv   # 1.56: solo quando c'era qualcosa da nascondere
    rt = (src.get("runtime") or {}).get(tmux) or {}
    out = {
        "id": row.get("session_id") or tmux,
        "name": name,
        "account": row.get("account") or "",
        # 1.8: personal | work — l'app non deve piu' riconoscere l'account personale dal nome «personale»
        "account_kind": (src.get("account_kinds") or {}).get(row.get("account") or "") or None,
        "project": project_of(row.get("cwd"), src.get("root")),
        "state": st,
        "since": since,
        "turn_started": turn,
        "tool": (src.get("tools") or {}).get(tmux) if st in ("busy", "awaiting") else None,
        # 1.5: l'intento del comando in corso («Run the plugin test suite»), dove `tool` dice solo «Bash cd …»
        "tool_note": (src.get("tool_notes") or {}).get(tmux) if st in ("busy", "awaiting") else None,
        "link": row.get("link") or "",
        "attached": bool(row.get("attached")),
        "followed": tmux in (src.get("follow") or ()),
        "question": question,
        "outcome": _outcome(events) if st != "waiting" or not q else None,
        "next": (src.get("next") or {}).get(tmux) or None,
        # 1.2: quando e' stato scritto quel «prossimo» (la data della riga di recap, mezzanotte locale), cosi'
        # chi legge sa se e' di oggi o di tre giorni fa e puo' ordinarlo rispetto a outcome.at
        "next_at": (src.get("next_at") or {}).get(tmux) or None,
        # 1.11: cosa sta usando la sessione — modello ({id, label}), livello di effort e percentuale di
        # contesto consumata. Null quando non si leggono in modo affidabile: mai stimati
        "model": rt.get("model") or None,
        "effort": rt.get("effort") or None,
        "context": rt.get("context") if isinstance(rt.get("context"), int) else None,
        # 1.14 (22/09/2026): la sessione e' passata da sola a un modello piu' vecchio perche' le salvaguardie hanno
        # segnalato un messaggio (Opus 5.5): {from, to, category, at} finche' resta li', altrimenti null. Si torna con
        # l'op `model` o con /model; il segno sparisce al primo turno sul modello di prima
        "fallback": _fallback(rt.get("fallback")),
        # 1.1: icona della scheda (stabile per la vita della sessione) e il suo solo colore
        "icon": (src.get("icons") or {}).get(tmux) or None,
        "color": color_of((src.get("icons") or {}).get(tmux), src.get("colors")),
        # 1.16 (25/09/2026): la modalita' a bassa priorita' («off» | «offered» | «active», letta dallo schermo tmux:
        # Claude Code la tiene solo in memoria; null senza schermo o sessione gone) e il goal nativo di /goal
        # ({text, since, met} dall'ultimo goal_status del transcript; null senza goal)
        "low_priority": row.get("low_priority") if row.get("low_priority") in ("off", "offered", "active") else None,
        # 1.23: il prompt suggerito in grigio dopo «❯», solo per una sessione ferma al prompt (null altrimenti)
        "suggestion": (str(row.get("suggestion"))[:300] or None) if st == "idle" and row.get("suggestion") else None,
        "goal": _goal(row.get("goal_status")),
        # 1.51 (13/10/2026): il comando slash dell'handoff che il tasto dell'app fa scrivere alla sessione, gia' con gli
        # argomenti: «/fable-director:handoff --here» se la sessione ha fable-director, «/supervisor:handoff --here»
        # altrimenti, null se nessuno dei due (o non si sa: sessione su un altro host). Assente = relay prima della 1.51
        "handoff": (src.get("handoffs") or {}).get(tmux) or None,
        # 1.72 (15/10/2026): la pillola della testata, {files, added, removed, at} di cm-diff.counts (le modifiche della
        # cartella rispetto all'ultimo commit; fuori da git quelli dei passaggi della conversazione, decisione 3 dell'indice),
        # o null senza modifiche, con git che non ha risposto o per una sessione su un altro host
        "changes": (src.get("changes") or {}).get(tmux) or None,
    }
    # 1.37 (05/10/2026), solo quando ci sono (lo stato sta in 8 KB: un campo vuoto per sessione pesa): `advice`, il
    # consiglio di modello ed effort di fable-director {model, effort, reason, switch_cost_tokens, at, source, when,
    # differs}; `finished` true, ferma col compito chiuso; `duplicate_of`, la sessione di cui questa e' un doppione
    # (stessa cartella, stessa conversazione: il «-2» del 05/10). Assenti = null / false
    if (src.get("advice") or {}).get(tmux):
        out["advice"] = dict(src["advice"][tmux])
    if st == "idle" and _finished(events):
        out["finished"] = True
    if (src.get("duplicates") or {}).get(tmux):
        out["duplicate_of"] = src["duplicates"][tmux]
    # 1.38 (05/10/2026): le voci della riga «Prossimi:» dell'ultimo messaggio, [{text, blocking}], solo quando ci sono
    # e quando c'e' l'esito (non mentre la sessione e' ferma su una domanda)
    stops = [r for r in events if r.get("event") == "stop"]
    steps = next_steps(stops[-1].get("tail") or stops[-1].get("last") or "") if stops and out["outcome"] else []
    if steps:
        out["next_steps"] = steps
    # 1.68 (14/10, una sessione aspettava un login e risultava al lavoro): un turno che finisce con una voce
    # bloccante («Prossimi:» con «!», o un elenco «Da te») e' una domanda aperta: {text, at}, finche' la sessione e' ferma
    if st == "idle" and out["outcome"]:
        block = next((x["text"] for x in steps if x.get("blocking")), "") or da_te(stops[-1].get("tail") or stops[-1].get("last") or "")
        if block:
            out["turn_blocking"] = {"text": one_line(block)[:200], "at": out["outcome"]["at"]}
    # 1.68: la sessione ha detto `supervisor needs-you "<motivo>"`: {reason, at}, finche' non riceve il prompt dopo
    if (src.get("needs_you") or {}).get(tmux) and st != "gone":
        nyu = src["needs_you"][tmux]
        out["needs_you"] = {"reason": one_line(nyu.get("reason") or "")[:200], "at": int(nyu.get("at") or 0)}
    # 1.54 (14/10): la modalita' dei permessi (default, acceptEdits, plan, bypassPermissions...), dall'ultima riga della
    # trascrizione che la dice. 1.55: la domanda risolta al PC negli ultimi minuti, {id, text, at, by: "terminal"}.
    # Assenti = non si sa / nessuna
    if rt.get("permission_mode"):
        out["permission_mode"] = str(rt["permission_mode"])
    # 1.69 (15/10): la sessione puo' arrivare a bypassPermissions con Shift+Tab (partita con il permesso); assente = no
    if rt.get("permission_bypass"):
        out["permission_bypass"] = True
    if (src.get("resolved") or {}).get(name):
        out["resolved"] = dict(src["resolved"][name], by="terminal")
    # 1.62 (14/10, dall'analisi dei concorrenti): la sessione e' attaccata e qualcuno ha premuto un tasto nel suo
    # terminale negli ultimi AT_PC_S: chi e' al PC la vede gia', telefono e orologio non suonano. Senza il dato
    # (sessione su un altro host, tmux che non risponde) il campo manca e si suona: fail open
    act = row.get("pc_activity")
    if row.get("attached") and act and src.get("_now") and 0 <= float(src["_now"]) - float(act) <= AT_PC_S:
        out["at_pc"] = True
    return out


def _goal(g):
    if not isinstance(g, dict) or not str(g.get("text") or "").strip():
        return None
    since = g.get("since")
    return {"text": one_line(str(g["text"]))[:200], "since": int(since) if isinstance(since, (int, float)) and since else None,
            "met": bool(g.get("met"))}


def _int_or_none(v):
    try:
        return None if v is None else int(round(float(v)))
    except (TypeError, ValueError):
        return None


def kinds_of(accounts, default_account=""):
    """1.8: quale account e' personale e quale di lavoro (l'app lo deduceva dal nome «personale»). Vince
    `accounts.<nome>.kind` se c'e'; altrimenti l'account di default e' personal e gli altri work; uno solo e' personal."""
    names = list(accounts or {})
    out = {}
    for n in names:
        k = str((accounts[n] or {}).get("kind") or "").lower()
        out[n] = k if k in ("personal", "work") else ("personal" if len(names) == 1 or n == default_account else "work")
    return out


def build_quota(quota, kinds=None):
    out = {}
    for acc, q in (quota or {}).items():
        q = q or {}
        out[acc] = {"h5": _int_or_none(q.get("cinque_ore_pct", q.get("five_hour_used_pct"))),
                    "w7": _int_or_none(q.get("settimana_pct", q.get("weekly_used_pct"))),
                    "reset_w7": _int_or_none(q.get("reset_settimanale", q.get("weekly_resets_at"))),
                    # 1.3: quando riparte la finestra di 5 ore (il polso mostrava il reset settimanale sotto la
                    # percentuale delle 5 ore, e sembrava sbagliato)
                    "reset_h5": _int_or_none(q.get("reset_cinque_ore", q.get("five_hour_resets_at"))),
                    "stale": bool(q.get("vecchia", q.get("stale"))),
                    # 1.8: personale o di lavoro, senza dover conoscere il nome dell'account
                    "kind": (kinds or {}).get(acc) or None}
    return out


DEVICE_KINDS = ("phone", "watch", "tablet", "chromebook", "web")   # 1.39: web = la web app remota in un browser


def kind_of(name):
    """1.32: il tipo di un dispositivo accoppiato prima della 1.32, dal nome se lo dice («watch-pixel5» → watch,
    «Pixel Tablet» → tablet); altrimenti None e l'app mostra un'icona generica."""
    n = str(name or "").lower()
    for word, kind in (("watch", "watch"), ("tablet", "tablet"), ("chromebook", "chromebook"), ("phone", "phone")):
        if word in n:
            return kind
    return None


def build_devices(devices, seen):
    """1.32 (04/10, chiesto dall'app, scelto dal maintainer): [{uid, name, kind, seen}], uno per dispositivo accoppiato
    (devices.json, che e' /allowed), nell'ordine di accoppiamento. kind = quello scritto dal dispositivo nel pairing
    (dalla 1.32) o dedotto dal nome, null se non si sa; seen = epoch s dell'ultima lettura (/seen), null se mai."""
    seen = seen if isinstance(seen, dict) else {}
    rows = [(u, d) for u, d in (devices.items() if isinstance(devices, dict) else []) if isinstance(d, dict)]
    rows.sort(key=lambda x: x[1].get("paired_at") or 0)   # stabile: a parita' l'ordine del file
    out = []
    for u, d in rows:
        k = d.get("kind") if d.get("kind") in DEVICE_KINDS else kind_of(d.get("name"))
        t = seen.get(u)
        out.append({"uid": u, "name": str(d.get("name") or ""), "kind": k, "seen": int(t) if isinstance(t, (int, float)) and t > 0 else None})
    return out


AT_PC_S = 120


def build_state(src, now, fit=True):
    src = dict(src, _now=now)   # 1.62: build_session confronta l'attivita' al PC con l'ora della raccolta
    sessions = order_sessions([build_session(r, src) for r in (src.get("rows") or [])])
    # 1.13: last_used = l'ultima trascrizione di quella cartella (epoch s) o null; l'ordine resta per nome
    projects = sorted(({"path": p.get("path", ""), "name": p.get("name", ""), "account": p.get("account", ""),
                        "last_used": p.get("last_used") if isinstance(p.get("last_used"), int) else None}
                       for p in (src.get("projects") or [])), key=lambda p: p["name"])
    night = src.get("night") or {}
    recap = src.get("recap") or {}
    state = {
        "v": V,
        "ts": int(now),
        "host": src.get("host") or "",
        "sessions": sessions,
        "quota": build_quota(src.get("quota"), src.get("account_kinds")),
        "projects": projects,
        "night": {"queued": int(night.get("queued") or 0), "running": night.get("running") or None,
                  "items": night_items(night.get("items")),
                  # 1.49 (09/10): night_add takes «master» (workspace.root_session_name) as well as a project
                  **({"master": True} if night.get("master") else {}),
                  # 1.44 (08/10): l'ultimo rapporto della notte, {date, generated_at}; assente senza rapporti
                  **({"report": src["night_report"]} if src.get("night_report") else {})},
        "recap": {"date": recap.get("date") or "", "items": [{"project": i.get("project", ""), "done": i.get("done", ""), "next": i.get("next", "")} for i in (recap.get("items") or [])]},
        # 1.12: modelli ed effort che il polso puo' chiedere per una sessione; null se il relay non li conosce
        "choices": src.get("choices") or None,
        # 1.19: il relay sa ricevere «Condividi» (op report con /share); presente = l'app accende il pulsante
        # 1.28: any = ogni formato (non solo immagini): l'app mostra «File» nel menu del «+» solo con questo relay
        # 1.48: parts = il relay ricompone /share/<id>/parts + meta, fino a max_parts_bytes; senza, il nodo unico
        # 1.50: multi = un report porta fino a tanti id in `arg` (virgola) e la sessione riceve un messaggio solo
        "share": {"max_bytes": SHARE_MAX_BYTES, "any": True, "parts": True, "max_parts_bytes": SHARE_PARTS_MAX, "multi": SHARE_MULTI},
        # 1.21: le op di /cmd che questo relay esegue; l'app accende un pulsante solo se la sua op c'e' (Stop = interrupt)
        "ops": list(src.get("ops") or []),
        # 1.25: i comandi slash che il telefono puo' dare (senza «/»); l'app li propone scrivendo «/» nel campo
        "slash": list(src.get("slash") or []),
        # 1.32: i dispositivi accoppiati, per lo schema dei collegamenti dell'app
        "devices": build_devices(src.get("devices"), src.get("seen")),
        # 1.37: i compiti del registro che aspettano un ok ({task, title, what, where, deploy, requested_at}), dal
        # piu' vecchio; l'app li approva con l'op approve
        "approvals": [dict(a) for a in (src.get("approvals") or [])],
    }
    # 1.33: le azioni ricorrenti della master (box «Ricorrenti»); senza lista il campo manca e l'app non mostra il box
    if src.get("recurring"):
        state["recurring"] = [dict(x) for x in src["recurring"]][:8]
    # 1.66 (14/10): «Serve te», una lista sola dalla piu' vecchia: le domande aperte, i turni falliti, le sessioni ferme
    # per la quota, i task rossi. {kind: question|turn_failed|quota_limit|task_red, session, ref, title, since}. Per una
    # domanda ref = l'id della domanda e title la sua anteprima. Assente quando non c'e' niente
    att = [{"kind": "question", "session": s["name"], "ref": s["question"]["id"],
            "title": s["question"].get("preview") or s["question"]["text"][:200], "since": s["question"]["asked_at"]}
           for s in sessions if s.get("question")]
    att += [{"kind": "needs_you", "session": s["name"], "ref": s["name"], "title": redact_preview(s["needs_you"]["reason"]), "since": s["needs_you"]["at"]}
            for s in sessions if s.get("needs_you")]
    att += [{"kind": "turn_blocking", "session": s["name"], "ref": s["name"], "title": redact_preview(s["turn_blocking"]["text"]), "since": s["turn_blocking"]["at"]}
            for s in sessions if s.get("turn_blocking") and not s.get("needs_you")]
    att += [dict(x) for x in (src.get("attention") or [])]
    if att:
        state["attention"] = sorted(att, key=lambda x: (int(x.get("since") or 0), x.get("session") or ""))
    # 1.64: le cartelle madri ammesse per un progetto nuovo dal telefono (op project_new); assente = relay precedente
    if src.get("project_parents"):
        state["project_parents"] = list(src["project_parents"])
    # 1.75 (21/10, home a tre colonne della web): lo stato dei PC remoti, uno per host di cm-hosts, e le pubblicazioni
    # di oggi (release, push, deploy) dalla piu' recente; liste vuote quando non c'e' niente, assenti con un relay vecchio
    if "hosts_status" in src:
        state["hosts_status"] = [dict(h, sessions=list(h.get("sessions") or [])) for h in (src.get("hosts_status") or [])]
    if "published_today" in src:
        state["published_today"] = [dict(x) for x in (src.get("published_today") or [])][:PUBLISHED_MAX]
    # fit=False: lo stato intero, su cui il relay calcola gli eventi (23/09: gli eventi sullo stato tagliato davano
    # «Session closed» al polso per sessioni vive che fit_state aveva tolto per la dimensione)
    return fit_state(state, int(src.get("state_max_kb") or 8)) if fit else state


def size_of(state):
    return len(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode())


def cut_at_word(text, n):
    """≤ n caratteri, alla parola intera se possibile, a capo conservati (per `full`)."""
    t = str(text or "")
    if len(t) <= n:
        return t
    cut = t[:n]
    i = max(cut.rfind(" "), cut.rfind("\n"))
    return cut[:i].rstrip(" ,;:\n") if i > n // 2 else cut


def most_recent(projects, n):
    """1.26 (02/10): i progetti da tenere quando lo stato va tagliato: gli n usati piu' di recente (last_used, i null in
    fondo, poi il nome), restituiti nell'ordine per nome del contratto. Prima si tenevano i primi n in ordine alfabetico:
    dal vivo il 02/10, di 98 progetti al telefono ne arrivavano 5, nessuno recente."""
    ranked = sorted(projects, key=lambda p: (p.get("last_used") is None, -(p.get("last_used") or 0), p.get("name") or ""))
    keep = {id(p) for p in ranked[:n]}
    return [p for p in projects if id(p) in keep]


def fit_state(state, max_kb=8):
    """Sotto il tetto, togliendo in ordine: le sessioni finite piu' vecchie (restano le KEEP_GONE piu' recenti);
    i progetti oltre i primi dieci; `done` e `next` del recap a RECAP_CUT caratteri, a fine parola; `full` degli
    esiti a 300 caratteri, poi uguale a `short`; le voci del recap dal fondo, ma mai l'ultima; poi tutte le finite,
    i campi facoltativi delle sessioni e i progetti. MAI una sessione viva (06/10), mai la
    domanda: se non basta, lo stato resta sopra il tetto. 1.7 (14/09, dall'app): le voci del recap cadevano per prime, e con 9 sessioni e
    10 progetti il polso non aveva mai il recap («Ascolta il recap» assente). Prima ancora `full` cadeva per
    primo: con dieci sessioni finite ogni esito era la sola riga corta. Una sessione finita che esce dallo stato
    non genera eventi (events_between ignora le gone sparite e le gone ricomparse)."""
    cap = max_kb * 1024

    def fits():
        return size_of(state) <= cap
    if fits():
        return state
    gone = sorted((s for s in state["sessions"] if s["state"] == "gone"), key=lambda s: s.get("since") or 0)
    for s in gone[:max(0, len(gone) - KEEP_GONE)]:
        state["sessions"].remove(s)
        if fits():
            return state
    if len(state.get("published_today") or []) > PUBLISHED_FIT:   # 1.75: le piu' recenti restano
        state["published_today"] = state["published_today"][:PUBLISHED_FIT]
        if fits():
            return state
    state["projects"] = most_recent(state["projects"], 10)
    if fits():
        return state
    items = state["recap"]["items"]
    for it in items:
        it["done"], it["next"] = cut_at_word(it.get("done"), RECAP_CUT), cut_at_word(it.get("next"), RECAP_CUT)
    if fits():
        return state
    for n in (300, 0):
        for s in state["sessions"]:
            o = s.get("outcome")
            if o:
                o["full"] = cut_at_word(o["full"], n) if n else o["short"]
                if o.get("preview"):   # 1.60: l'anteprima segue lo stesso taglio
                    o["preview"]["full"] = cut_at_word(o["preview"]["full"], n) if n else o["preview"]["short"]
        if fits():
            return state
    # 23/09: prima di togliere una sessione viva, i campi facoltativi delle sessioni ferme (nulli per contratto quando
    # non si sanno) e i progetti oltre i primi cinque
    for s in state["sessions"]:
        if s.get("state") not in ("busy", "awaiting", "waiting"):
            s["tool_note"], s["next"], s["next_at"] = None, None, None
    if fits():
        return state
    state["projects"] = most_recent(state["projects"], 5)
    if fits():
        return state
    if state.get("published_today"):
        state["published_today"] = state["published_today"][:5]
        if fits():
            return state
    while not fits() and len(items) > 1:
        items.pop()
    if fits():
        return state
    # 06/10/2026 (regola del maintainer delle 10:57: nessuna sessione tolta senza consenso; rino e un'altra sessione
    # viva sparivano dall'elenco dell'app dopo i campi 1.37-1.39): una sessione VIVA non esce mai dallo stato. Prima
    # tutte le finite, poi i campi facoltativi delle sessioni e i progetti (l'elenco intero c'e' con l'op projects; l'ultima voce del
    # recap resta, regola 1.7); se non basta lo stato resta sopra il tetto, intero.
    for s in [x for x in state["sessions"] if x.get("state") == "gone"]:
        state["sessions"].remove(s)
        if fits():
            return state
    for s in state["sessions"]:
        s["suggestion"], s["goal"], s["tool_note"] = None, None, None
        if s.get("advice"):
            s["advice"]["reason"] = ""
        o = s.get("outcome")
        if o:
            o["full"] = o["short"]
            if o.get("preview"):
                o["preview"]["full"] = o["preview"]["short"]
    if fits():
        return state
    state["projects"] = []
    return state


# ------------------------------------------------------------------ eventi
def _by_name(state):
    return {s["name"]: s for s in (state or {}).get("sessions", [])}


def _hm(ts):
    return _dt.datetime.fromtimestamp(int(ts)).strftime("%H:%M") if ts else "-"


def events_between(prev, cur, now, seq=1, warn_pct=95):
    """Gli eventi dal diff di due stati (forma di events-sample.json): question (domanda nuova), answered
    (domanda sparita), outcome (esito nuovo o cambiato), gone (sessione sparita o passata a gone), launched
    (sessione nuova non gone), quota (soglia warn_pct attraversata). Torna (eventi, prossimo seq)."""
    out = []
    pv, cv = _by_name(prev), _by_name(cur)
    ts = int(now)

    def add(kind, s, title, body, ref=None):
        nonlocal seq
        out.append({"key": f"{ts}_{seq:03d}", "kind": kind, "session": s["name"] if s else None,
                    "account": s["account"] if s else None, "ts": ts, "title": title, "body": body, "ref": ref,
                    **preview_of(title, body), **({"quiet": True} if s and s.get("at_pc") else {})})   # 1.62
        seq += 1
    for name, s in cv.items():
        p = pv.get(name)
        if p is None and s["state"] != "gone":
            add("launched", s, f"▶ {name}", s.get("project") or "")
        pq, q = (p or {}).get("question"), s.get("question")
        if q and (not pq or pq.get("id") != q["id"]):
            add("question", s, f"❓ {name}", q["text"], q["id"])
        if pq and not q:
            add("answered", s, f"✓ {name}", "", pq.get("id"))
        po, o = (p or {}).get("outcome"), s.get("outcome")
        if o and p is not None and (not po or po.get("at") != o.get("at")):
            add("outcome", s, f"✓ {name}", o["short"])
        # 1.68: «ferma per te» e la fine turno bloccante, un evento quando compaiono o cambiano
        if s.get("needs_you") and (p is None or (p.get("needs_you") or {}).get("at") != s["needs_you"]["at"]):
            add("needs_you", s, f"✋ {name}", s["needs_you"]["reason"], name)
        elif s.get("turn_blocking") and p is not None and (p.get("turn_blocking") or {}) != s["turn_blocking"]:
            add("turn_blocking", s, f"✋ {name}", s["turn_blocking"]["text"], name)
        if s["state"] == "gone" and p is not None and p["state"] != "gone":
            add("gone", s, f"✗ {name}", "")
    for name, p in pv.items():
        if name not in cv and p["state"] != "gone":
            add("gone", p, f"✗ {name}", "")
    for acc, q in (cur or {}).get("quota", {}).items():
        pq = ((prev or {}).get("quota") or {}).get(acc) or {}
        for k, label in (("h5", "5 h"), ("w7", "settimana")):
            v, pvv = q.get(k), pq.get(k)
            if v is not None and v >= warn_pct and (pvv is None or pvv < warn_pct):
                # l'ora del reset della finestra che ha passato la soglia (prima: sempre quello settimanale)
                add("quota", None, f"⚠ {v} % {acc}", f"reset {_hm(q.get('reset_h5' if k == 'h5' else 'reset_w7'))}")
                out[-1]["account"] = acc
    return out, seq


def _fallback(fb):
    if not isinstance(fb, dict) or not fb.get("to"):
        return None
    at = fb.get("at")
    return {"from": fb.get("from") or None, "to": str(fb["to"]), "category": fb.get("category") or None,
            "at": int(at) if isinstance(at, (int, float)) and at else None}
