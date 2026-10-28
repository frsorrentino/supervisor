#!/usr/bin/env python3
"""supervisor suggest — i suggerimenti della master per la home dell'app (contratto 1.84, 24/10/2026; coda 1.88, 27/10).

  supervisor suggest add --kind K --key KEY --text T --prompt P [--ttl 12h] [--rank 0-9] [--source script|master]
                         [--options "A|B"] [--after AAAA-MM-GG]
  supervisor suggest list [--json]
  supervisor suggest clear [--kind K]
  supervisor suggest reshow                 rimette in vista le voci in «Non ora» (all'inizio del giro della master)
  supervisor suggest kind on|off K          riaccende o spegne un tipo
  supervisor suggest fill                   ogni ora da crontab, con nice -n 19: le cose aperte in coda, e il riassunto
  supervisor suggest digest                 il riassunto per il giro della master

La master li prepara due volte al giorno (alle 8 e alle 19), lo script `fill` ogni ora. kind: recurring | cleanup |
deadline | quota (1.84), choice | followup | unsaved | waiting | idea | proposal (1.88). key: stabile per lo stesso
suggerimento, serve per «Mai». text: la frase per la persona, una riga, mai con «…». prompt: quello che riceve la master
se la persona accetta. --ttl (s, m, h, d; senza: 12 h): poi scade da solo. --rank: 0 in cima, 9 in fondo (senza: dal
tipo; una voce di prima vale 5). --options: da 2 a 4 etichette, solo per choice (il prompt puo' avere {choice}).
--after: la voce resta nel file e va nello stato da quel giorno; la durata conta da li'.

Un add della master con una key che c'e' gia' sostituisce la voce: id nuovo, di nuovo in vista. Un add --source script
la aggiorna: stesso id, stesso at, e una voce in «Non ora» resta nascosta fino a `reshow`. `fill` scrive solo voci sue
(source script: followup, unsaved, waiting), al massimo 8, e le toglie quando la condizione sparisce (resolved); non
tocca mai quelle della master, salta le key in «Mai» e i tipi spenti. Legge lo stato intero del relay
(<relay.dir>/local/state.json) e il registro dei compiti; niente git, niente trascrizioni, niente rete.

Il relay pubblica state.suggestions [{id, kind, key, text, at, expires_at, rank, source, options?}] (1.88: senza prompt),
per rank e poi dalla piu' recente, al massimo 12. L'op suggestion {arg: id, action}: accept manda il prompt alla master e
toglie la voce; later la nasconde fino al giro dopo; never mette la key in una lista che add e fill saltano in silenzio;
choose {choice} manda il prompt con la scelta (1.88); discuss manda «Parliamone, senza farlo» e la voce resta (1.88).
Un id che non c'e' piu' risponde ok «already gone» (1.87).

La misura (KPI 13): ogni voce chiusa va in `history` (14 giorni) — accepted, chosen, never, expired (scaduta senza
tocchi), resolved (la condizione dello script e' sparita: non conta). Tasso = accettate / (accettate + mai + scadute). Un
tipo con almeno 8 voci chiuse in 14 giorni e nessuna accettata si spegne; `kind on K` lo riaccende.

Il file sta accanto alla config (suggestions.json; CM_SUGGESTIONS, CM_RELAY_STATE, CM_SUGGEST_DIGEST per i test).
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
KINDS = ("recurring", "cleanup", "deadline", "quota", "choice", "followup", "unsaved", "waiting", "idea", "proposal", "observe")
SCRIPT_KINDS = ("followup", "unsaved", "waiting", "observe")   # 1.88: i tipi che scrive `fill`; 1.94: observe
RANK_OF = {"deadline": 2, "choice": 2, "followup": 4, "unsaved": 4, "quota": 5, "waiting": 5, "observe": 5,
           "proposal": 6, "idea": 6, "recurring": 6, "cleanup": 6}
RANK_NONE = 5   # una voce senza rank (scritta prima della 1.88)
SOURCES = ("script", "master")
KEY_MAX = 120
TEXT_MAX = 200
PROMPT_MAX = 2000
OPTION_MAX = 40
STATE_MAX = 12
DEFAULT_TTL_S = 12 * 3600
SCRIPT_TTL_S = 26 * 3600   # le voci di fill: rinnovate a ogni passaggio finche' la condizione regge
HISTORY_S = 14 * 86400
OFF_MIN = 8   # voci chiuse in 14 giorni, nessuna accettata: il tipo si spegne
DIGEST_MAX = 2048
ACCEPTED = ("accepted", "chosen")
COUNTED = ("accepted", "chosen", "never", "expired")
ACTIONS = {"accept": "accepted", "later": "hidden", "never": "never", "choose": "chosen", "discuss": "discussed"}
DURATION_RE = re.compile(r"^(\d+)([smhd])$")
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_CFG = []


def _cfg():
    if not _CFG:
        try:
            cm = _load("cm-config")
            _CFG.append((cm, cm.load(warn=False)))
        except Exception:   # noqa: BLE001 — senza config: inglese e default
            _CFG.append((None, {}))
    return _CFG[0]


def M(key, **kw):
    cm, cfg = _cfg()
    return cm.msg(cfg, key, **kw) if cm else key


def _expand(p):
    cm = _cfg()[0]
    return Path(cm.expand(p) if cm else os.path.expanduser(p))


def path():
    p = os.environ.get("CM_SUGGESTIONS")
    if p:
        return Path(p)
    c = (os.environ.get("CC_SUPERVISOR_CONFIG") or os.environ.get("TEAM_SUPERVISOR_CONFIG") or os.environ.get("CLAUDE_MASTER_CONFIG"))
    return (Path(c).parent if c else Path.home() / ".config" / "cc-supervisor") / "suggestions.json"


def state_path():
    """Lo stato intero che il relay scrive a ogni push (local_state_write), senza i tagli degli 8 KB."""
    p = os.environ.get("CM_RELAY_STATE")
    return Path(p) if p else _expand((_cfg()[1].get("relay") or {}).get("dir") or "~/.cc-supervisor/relay") / "local" / "state.json"


def digest_path():
    p = os.environ.get("CM_SUGGEST_DIGEST")
    return Path(p) if p else _expand(_cfg()[1].get("state_dir") or "~/.claude") / "suggest-digest.md"


def load():
    try:
        d = json.loads(path().read_text())
    except (OSError, ValueError):
        d = {}
    d = d if isinstance(d, dict) else {}
    return {"items": [x for x in d.get("items") or [] if isinstance(x, dict) and x.get("id")],
            "never": [str(k) for k in d.get("never") or []],
            "never_at": dict(d.get("never_at") or {}),
            "history": [h for h in d.get("history") or [] if isinstance(h, dict)],
            "kinds_on": dict(d.get("kinds_on") or {}), "kinds_off": dict(d.get("kinds_off") or {})}


def save(d, now=None):
    now = int(now or time.time())
    d["history"] = [h for h in d.get("history") or [] if int(h.get("at") or 0) > now - HISTORY_S]
    d["never_at"] = {k: v for k, v in (d.get("never_at") or {}).items() if int(v or 0) > now - HISTORY_S}
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, p)


def ledger_write(rows):
    """Le righe `suggestion` scritte qui (expired, resolved), nella forma del relay (cm-relay.ledger_write)."""
    if not rows:
        return
    try:
        p = _expand(_cfg()[1].get("state_dir") or "~/.claude") / "ledger.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            for r in rows:
                row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "suggestion", "session_id": "", "cwd": "", "account": "", "pid": os.getpid()}
                row.update(r)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def rank_of(x):
    r = x.get("rank")
    return int(r) if isinstance(r, int) else RANK_NONE


def source_of(x):
    return x.get("source") if x.get("source") in SOURCES else "master"


def close(d, x, action, now, rows=None):
    """Una voce chiusa nella storia (la misura); con rows anche la riga del ledger."""
    d["history"].append({"key": x.get("key"), "kind": x.get("kind"), "source": source_of(x), "action": action, "at": int(now)})
    if rows is not None:
        rows.append({"id": x.get("id"), "key": x.get("key"), "kind": x.get("kind"), "source": source_of(x), "action": action, "by": "suggest"})


def sweep(d, now, rows):
    """Via le voci scadute; quelle mai toccate contano come expired (una in «Non ora» e' stata toccata)."""
    keep = []
    for x in d["items"]:
        if int(x.get("expires_at") or 0) > now:
            keep.append(x)
        elif not x.get("hidden"):
            close(d, x, "expired", now, rows)
    changed = len(keep) != len(d["items"])
    d["items"] = keep
    return changed


def live(items, now):
    return [x for x in items if int(x.get("expires_at") or 0) > now]


def kind_off(d, now=None):
    """I tipi spenti: quelli spenti a mano (kind off) e quelli con almeno OFF_MIN voci chiuse negli ultimi 14 giorni,
    dopo l'ultimo kind on, e nessuna accettata."""
    now = int(now or time.time())
    off = set(d.get("kinds_off") or {})
    for k in KINDS:
        if k in off:
            continue
        since = max(now - HISTORY_S, int((d.get("kinds_on") or {}).get(k) or 0))
        hs = [h for h in d.get("history") or [] if h.get("kind") == k and h.get("action") in COUNTED and int(h.get("at") or 0) > since]
        if len(hs) >= OFF_MIN and not any(h["action"] in ACCEPTED for h in hs):
            off.add(k)
    return off


def for_state(now=None):
    """1.88: [{id, kind, key, text, at, expires_at, rank, source, options?}] delle voci non scadute, non nascoste e
    arrivate alla loro data, per rank e poi dalla piu' recente, al massimo 12; [] se non ce ne sono. Il prompt resta
    nel file: lo legge il relay quando arriva la risposta."""
    now = int(now or time.time())
    items = [x for x in live(load()["items"], now) if not x.get("hidden") and int(x.get("after") or 0) <= now]
    out = []
    for x in sorted(items, key=lambda x: (rank_of(x), -int(x.get("at") or 0), x["id"]))[:STATE_MAX]:
        y = {k: x.get(k) for k in ("id", "kind", "key", "text", "at", "expires_at")}
        y["rank"], y["source"] = rank_of(x), source_of(x)
        if x.get("options"):
            y["options"] = list(x["options"])
        out.append(y)
    return out


def act(sid, action, now=None, choice=None):
    """L'op suggestion: (codice, voce). codice: accepted | hidden | never | chosen | discussed | gone | bad_action |
    bad_choice. accept e choose tolgono la voce (chi chiama manda il prompt), later la nasconde, never la toglie e mette
    la sua key fra quelle da saltare, discuss la lascia dov'e'. gone (1.87): l'id non c'e' piu' — per l'app e' fatto."""
    if action not in ACTIONS:
        return "bad_action", None
    now = int(now or time.time())
    d = load()
    rows = []
    swept = sweep(d, now, rows)
    hit = next((x for x in d["items"] if x["id"] == sid and not x.get("hidden")), None)
    code = ACTIONS[action]
    if hit and action == "choose" and choice not in (hit.get("options") or []):
        code = "bad_choice"
    if not hit or code in ("bad_choice", "discussed"):
        if swept:
            save(d, now)
            ledger_write(rows)
        return ("gone", None) if not hit else (code, hit)
    if action == "later":
        hit["hidden"] = now
    else:
        d["items"] = [x for x in d["items"] if x["id"] != sid]
        close(d, hit, code, now)
        if action == "never":
            if hit.get("key") not in d["never"]:
                d["never"].append(hit["key"])
            d["never_at"][hit["key"]] = now
    save(d, now)
    ledger_write(rows)
    return code, hit


def upsert(d, now, kind, key, text, prompt, ttl_s, source="master", rank=None, options=None, after=None):
    """Una voce nella coda; l'id. Da script, con la stessa key: aggiornata sul posto (id, at e hidden restano)."""
    rank = RANK_OF.get(kind, RANK_NONE) if rank is None else rank
    expires = max(now, int(after or 0)) + ttl_s
    old = next((x for x in d["items"] if x.get("key") == key), None)
    if old and source == "script":
        old.update(kind=kind, text=text, prompt=prompt, expires_at=expires, rank=rank, source=source)
        for k, v in (("options", options), ("after", after)):
            if v:
                old[k] = v
            else:
                old.pop(k, None)
        return old["id"]
    sid = "s-" + hashlib.sha1(f"{key}\n{now}\n{text}".encode()).hexdigest()[:10]
    d["items"] = [x for x in d["items"] if x.get("key") != key]
    x = {"id": sid, "kind": kind, "key": key, "text": text, "prompt": prompt, "at": now, "expires_at": expires, "rank": rank, "source": source}
    if options:
        x["options"] = options
    if after:
        x["after"] = int(after)
    d["items"].append(x)
    return sid


# ------------------------------------------------------------------ fill (1.88): le cose aperte, a costo zero
def followup_key(session, step):
    return f"followup:{session}:{hashlib.sha1(str(step).encode()).hexdigest()[:8]}"


def label(project, fallback):
    """Il nome con cui la voce comincia (28/10): l'ultima parte del progetto, o `fallback` senza progetto."""
    return str(project or "").rstrip("/").rsplit("/", 1)[-1] or str(fallback)


def one_line(template, cut, **kw):
    """Il testo del modello su una riga entro TEXT_MAX: se non entra, `cut` si accorcia a fine parola (mai «…»)."""
    val = " ".join(str(kw[cut]).split())
    while True:
        text = " ".join(M(template, **dict(kw, **{cut: val})).split())
        if len(text) <= TEXT_MAX or " " not in val:
            return text[:TEXT_MAX]
        val = val.rsplit(" ", 1)[0].rstrip(",;:.")


def wanted(state, commitments, now):
    """Le voci che `fill` vuole in coda: [(kind, key, text, prompt, attesa in secondi)]. commitments None: il registro
    non risponde, e le attese esterne non si toccano."""
    sc = _cfg()[1].get("suggest") or {}
    stall_s, max_s = float(sc.get("stall_h", 4)) * 3600, float(sc.get("followup_max_h", 72)) * 3600
    master = str((_cfg()[1].get("workspace") or {}).get("root_session_name") or "master")
    out = []
    for s in state.get("sessions") or []:
        name = str(s.get("name") or "")
        if not name or name == master:
            continue
        idle = now - int(s.get("since") or now)
        project = label(s.get("project"), name)
        steps = [x for x in s.get("next_steps") or [] if isinstance(x, dict) and str(x.get("text") or "").strip()]
        if s.get("state") == "idle" and steps and stall_s <= idle <= max_s:
            step = next((x for x in steps if x.get("blocking")), steps[0])["text"].strip()
            out.append(("followup", followup_key(name, step), one_line("suggest.followup_text", "step", project=project, step=step),
                        M("suggest.followup_prompt", session=name, step=step), idle))
        cl = s.get("closed") or {}
        if s.get("state") == "gone" and int(cl.get("dirty_n") or 0) > 0:
            sid = cl.get("session_id") or int(cl.get("at") or 0)
            tpl = "suggest.unsaved_text_one" if int(cl["dirty_n"]) == 1 else "suggest.unsaved_text"
            out.append(("unsaved", f"unsaved:{name}:{sid}", one_line(tpl, "project", project=project, n=cl["dirty_n"]),
                        M("suggest.unsaved_prompt", session=name, n=cl["dirty_n"], session_id=cl.get("session_id") or "?"), now - int(cl.get("at") or now)))
    for c in commitments or []:
        pz = c.get("paused") or {}
        if pz.get("waits") == "external" and pz.get("recheck") and int(pz["recheck"]) <= now:
            reason = str(pz.get("reason") or c.get("title") or c.get("task"))
            project = label(c.get("project"), c.get("session") or c["task"])
            out.append(("waiting", f"waiting:task:{c['task']}", one_line("suggest.waiting_text", "reason", reason=reason, project=project),
                        M("suggest.waiting_prompt", reason=reason, task=c["task"]), now - int(pz["recheck"])))
    return out + observe_wanted(now)


def observe_wanted(now):
    """1.94 (28/10, «la raccolta del feedback deve lavorare con continuita'»): le note locali di claude-observe
    (<suggest.observe_dir>/<plugin>.jsonl, o CM_OBSERVE_DIR) si accumulavano senza che nessuno le leggesse — 190 per
    supervisor il 28/10. Una voce per plugin quando le note new sono almeno suggest.observe_min_new o la piu' vecchia ha
    almeno suggest.observe_days giorni; key stabile per settimana ISO; la piu' frequente per conteggio. Solo lettura."""
    sc = _cfg()[1].get("suggest") or {}
    d = Path(os.environ.get("CM_OBSERVE_DIR") or _expand(sc.get("observe_dir") or "~/.local/state/claude-observe"))
    skip = set(sc.get("observe_skip") or [])
    min_new, days = int(sc.get("observe_min_new", 10)), float(sc.get("observe_days", 3))
    week = time.strftime("%G-W%V", time.localtime(now))
    out = []
    for f in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
        plugin = f.stem
        if plugin in skip:
            continue
        new = []
        try:
            for line in f.read_text(errors="replace").splitlines():
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                if isinstance(x, dict) and x.get("status") == "new":
                    new.append(x)
        except OSError:
            continue
        if not new:
            continue
        oldest = min(float(x.get("first_seen") or now) for x in new)
        if len(new) < min_new and now - oldest < days * 86400:
            continue
        top = " ".join(str(max(new, key=lambda x: int(x.get("count") or 1)).get("error") or "?").split())
        since = time.strftime("%d/%m", time.localtime(oldest))
        out.append(("observe", f"observe:{plugin}:{week}",
                    one_line("suggest.observe_text", "top", plugin=plugin, n=len(new), since=since, top=top),
                    M("suggest.observe_prompt", plugin=plugin, n=len(new), since=since), int(now - oldest)))
    return out


def read_state():
    try:
        st = json.loads(state_path().read_text())
    except (OSError, ValueError):
        return None
    return st if isinstance(st, dict) and isinstance(st.get("sessions"), list) else None


def read_commitments(now):
    try:
        tk = _load("cm-tasks")
        con = tk.connect(create=False)
        if con is None:
            return []
        try:
            return tk.commitments(con, now)
        finally:
            con.close()
    except Exception:   # noqa: BLE001 — il registro non risponde: le attese esterne restano come sono
        return None


def fill(now=None, state=None, commitments=None):
    """Un passaggio dello script orario: {added, updated, kept, resolved, expired}, o None senza lo stato del relay
    (niente cambia: una voce non si toglie per mancanza di dati). Il file si riscrive solo se cambia qualcosa."""
    now = int(now or time.time())
    state = read_state() if state is None else state
    if state is None:
        return None
    if commitments is None:
        commitments = read_commitments(now)
    d = load()
    before = json.dumps(d, sort_keys=True)
    rows = []
    stats = {"added": 0, "updated": 0, "kept": 0, "resolved": 0, "expired": 0}
    n0 = len(d["history"])
    sweep(d, now, rows)
    stats["expired"] = len(d["history"]) - n0
    off = kind_off(d, now)
    smax = int((_cfg()[1].get("suggest") or {}).get("script_max", 8))
    want = [w for w in wanted(state, commitments, now) if w[1] not in d["never"] and w[0] not in off]
    want = sorted(want, key=lambda w: (RANK_OF[w[0]], -w[4], w[1]))[:smax]
    keys = {w[1] for w in want}
    untouched = set() if commitments is not None else {"waiting"}
    for x in [x for x in d["items"] if source_of(x) == "script" and x.get("key") not in keys and x.get("kind") not in untouched]:
        d["items"].remove(x)
        close(d, x, "resolved", now, rows)
        stats["resolved"] += 1
    mine = {x["key"]: x for x in d["items"]}
    for kind, key, text, prompt, _ in want:
        old = mine.get(key)
        if old and source_of(old) != "script":
            continue   # una voce della master con la stessa key: lo script non la tocca
        prev = json.dumps(old, sort_keys=True) if old else None
        upsert(d, now, kind, key, text, prompt[:PROMPT_MAX], SCRIPT_TTL_S, source="script")
        new = next(x for x in d["items"] if x["key"] == key)
        stats["added" if not old else ("kept" if json.dumps(new, sort_keys=True) == prev else "updated")] += 1
    if json.dumps(d, sort_keys=True) != before:
        save(d, now)
    ledger_write(rows)
    try:
        p = digest_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(digest(now) + "\n")
    except OSError:
        pass
    return stats


def digest(now=None):
    """Il riassunto per il giro della master, al massimo 2 KB: le voci in coda per tipo, il tasso di accettazione per
    tipo degli ultimi 14 giorni, i tipi spenti, le key in «Mai» degli ultimi 14 giorni. Mai i prompt."""
    now = int(now or time.time())
    d = load()
    items = live(d["items"], now)
    since = now - HISTORY_S
    none = M("suggest.digest_none")
    counts = {}
    for x in items:
        c = counts.setdefault(x.get("kind"), [0, 0])
        c[0] += 1
        c[1] += 1 if x.get("hidden") else 0
    queue = ", ".join(f"{k} {n}" + (f" ({h} {M('suggest.digest_hidden')})" if h else "") for k, (n, h) in sorted(counts.items())) or none
    rate = []
    for k in KINDS:
        hs = [h for h in d["history"] if h.get("kind") == k and h.get("action") in COUNTED and int(h.get("at") or 0) > since]
        if hs:
            rate.append(f"{k} {sum(h['action'] in ACCEPTED for h in hs)}/{len(hs)}")
    never = [k for k, at in sorted(d["never_at"].items(), key=lambda kv: -int(kv[1] or 0)) if int(at or 0) > since]
    lines = [M("suggest.digest_title", time=time.strftime("%d/%m %H:%M", time.localtime(now))),
             f"{M('suggest.digest_queue', n=len(items))}: {queue}",
             f"{M('suggest.digest_rate')}: {', '.join(rate) or none}",
             f"{M('suggest.digest_off')}: {', '.join(sorted(kind_off(d, now))) or none}",
             f"{M('suggest.digest_never')}: {', '.join(never) or none}"]
    for x in sorted(items, key=lambda x: (rank_of(x), -int(x.get("at") or 0))):
        lines.append(f"- [{x.get('kind')} {rank_of(x)}{' ' + M('suggest.digest_hidden') if x.get('hidden') else ''}] {x.get('text')}")
    out = ""
    for ln in lines:
        if len((out + ln + "\n").encode()) > DIGEST_MAX:
            break
        out += ln + "\n"
    return out.rstrip("\n")


def parse_duration(text):
    m = DURATION_RE.match(str(text or "").strip())
    return int(m.group(1)) * UNITS[m.group(2)] if m else None


def main(argv):
    args = list(argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    verb = args.pop(0)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]

    def opt(name):
        if name in args:
            i = args.index(name)
            if i + 1 >= len(args):
                sys.exit(f"suggest: {name} needs a value")
            v = args[i + 1]
            del args[i:i + 2]
            return v
        return None
    now = int(time.time())
    if verb == "add":
        kind, key, text, prompt, ttl = opt("--kind"), opt("--key"), opt("--text"), opt("--prompt"), opt("--ttl")
        rank, source, options, after = opt("--rank"), opt("--source") or "master", opt("--options"), opt("--after")
        if args or not kind or not key or not text or not prompt:
            sys.exit("usage: suggest add --kind K --key KEY --text T --prompt P [--ttl 12h] [--rank 0-9] [--source script|master] [--options \"A|B\"] [--after AAAA-MM-GG]")
        if kind not in KINDS:
            sys.exit(f"suggest: --kind is one of {', '.join(KINDS)}")
        key, text, prompt = key.strip(), " ".join(text.split()), prompt.strip()
        if not key or len(key) > KEY_MAX:
            sys.exit(f"suggest: --key 1..{KEY_MAX} characters")
        if not text or len(text) > TEXT_MAX or "…" in text:
            sys.exit(f"suggest: --text one line, 1..{TEXT_MAX} characters, never «…»")
        if not prompt or len(prompt) > PROMPT_MAX:
            sys.exit(f"suggest: --prompt 1..{PROMPT_MAX} characters")
        ttl_s = DEFAULT_TTL_S if ttl is None else parse_duration(ttl)
        if not ttl_s:
            sys.exit(f"suggest: --ttl wants a duration like 30m, 12h, 2d (not «{ttl}»)")
        if rank is not None and not re.fullmatch(r"\d", rank):
            sys.exit("suggest: --rank is 0..9")
        if source not in SOURCES:
            sys.exit("suggest: --source is script or master")
        opts = [o.strip() for o in options.split("|")] if options is not None else None
        if (opts is not None) != (kind == "choice"):
            sys.exit("suggest: --options goes with --kind choice, and a choice needs it")
        if opts is not None and not (2 <= len(opts) <= 4 and all(0 < len(o) <= OPTION_MAX for o in opts)):
            sys.exit(f"suggest: --options wants 2..4 labels of 1..{OPTION_MAX} characters, separated by |")
        after_at = None
        if after is not None:
            try:
                after_at = int(time.mktime(time.strptime(after, "%Y-%m-%d")))
            except ValueError:
                sys.exit(f"suggest: --after wants a day as AAAA-MM-GG (not «{after}»)")
        d = load()
        rows = []
        sweep(d, now, rows)
        if key in d["never"] or kind in kind_off(d, now):
            return 0   # «Mai» dall'app, o un tipo spento: saltato in silenzio
        print(upsert(d, now, kind, key, text, prompt, ttl_s, source=source, rank=None if rank is None else int(rank), options=opts, after=after_at))
        save(d, now)
        ledger_write(rows)
        return 0
    if verb == "list":
        d = load()
        items = sorted(live(d["items"], now), key=lambda x: (rank_of(x), -int(x.get("at") or 0)))
        if as_json:
            print(json.dumps({"items": items, "never": d["never"], "off": sorted(kind_off(d, now))}, ensure_ascii=False))
        else:
            print("\n".join(f"{x['id']}  {x['kind']:<9} r{rank_of(x)} {source_of(x):<6} {x['text']}"
                            + ("  [later]" if x.get("hidden") else "")
                            + (f"  [after {time.strftime('%Y-%m-%d', time.localtime(x['after']))}]" if int(x.get("after") or 0) > now else "")
                            for x in items) or "no suggestions")
        return 0
    if verb == "clear":
        kind = opt("--kind")
        if args or (kind is not None and kind not in KINDS):
            sys.exit("usage: suggest clear [--kind K]")
        d = load()
        rows = []
        sweep(d, now, rows)
        d["items"] = [x for x in d["items"] if kind and x.get("kind") != kind]
        save(d, now)
        ledger_write(rows)
        return 0
    if verb == "reshow":
        if args:
            sys.exit("usage: suggest reshow")
        d = load()
        n = sum(1 for x in d["items"] if x.pop("hidden", None))
        save(d, now)
        print(f"{n} shown again")
        return 0
    if verb == "kind":
        if len(args) != 2 or args[0] not in ("on", "off") or args[1] not in KINDS:
            sys.exit(f"usage: suggest kind on|off K (K one of {', '.join(KINDS)})")
        d = load()
        if args[0] == "on":
            d["kinds_off"].pop(args[1], None)
            d["kinds_on"][args[1]] = now
        else:
            d["kinds_off"][args[1]] = now
        save(d, now)
        return 0
    if verb == "fill":
        if args:
            sys.exit("usage: suggest fill")
        r = fill(now)
        print(json.dumps(r) if as_json else ("no relay state: nothing changed" if r is None else " ".join(f"{k} {v}" for k, v in r.items())))
        return 0
    if verb == "digest":
        print(digest(now))
        return 0
    sys.exit(f"suggest: unknown verb {verb}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
