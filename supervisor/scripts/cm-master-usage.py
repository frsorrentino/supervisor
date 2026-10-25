#!/usr/bin/env python3
"""supervisor quota --master — il consumo della master per ora e per origine (25/10/2026).

  supervisor quota --master [--json] [--days N] [--suggest]

Legge le trascrizioni della master (la cartella radice del workspace, ogni account, i subagenti compresi), pesa l'usage
di ogni risposta in token input-equivalenti come le ricevute di fable-director (input 1, output 5, cache read 0,1 o la
tariffa del modello, scrittura in cache 1,25 o 2 per quella di un'ora) e assegna ogni turno alla sua origine:

- automazione: una voce dell'elenco delle automazioni concordate (automations.json del plugin, piu' quello accanto
  alla config che lo completa o lo corregge per nome), riconosciuta dal testo del messaggio che la avvia; un turno
  pianificato (CronCreate) che non e' in elenco diventa «non in elenco: <inizio del testo>» e fa scattare un avviso;
- richiesta: dal telefono, dalla web app, una segnalazione, un suggerimento accettato, il terminale;
- sessione: un messaggio di un'altra sessione (from-name);
- altro: i risultati dei lavori in sottofondo e quello che non si riconosce.

Le compattazioni sono un'automazione a parte: la chiamata del riassunto (stimata: il contesto di prima letto dalla
cache e il riassunto scritto) e la prima risposta dopo, che riscrive la cache.

Zero token di modello. Incrementale: <state_dir>/master-usage.json tiene per ogni file dove si era arrivati e i conti
per ora; si tengono 35 giorni. Se l'elenco delle automazioni cambia, il conto si rifa' da capo.

--suggest: gli avvisi diventano suggerimenti «quota» (supervisor suggest add, key stabile): le automazioni oltre un
quarto del consumo dei giorni letti, un'automazione che nelle ultime 24 ore ha piu' del doppio della sua media
giornaliera, un'automazione non in elenco.
"""
import datetime as dt
import glob
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)

KEEP_DAYS = 35
AUTO_SHARE = 0.25        # avviso: le automazioni oltre un quarto del consumo
DOUBLE = 2.0             # avviso: un'automazione oltre il doppio della sua media giornaliera nelle ultime 24 ore
DOUBLE_MIN_SHARE = 0.01  # ...se pesa almeno l'1 % del periodo (sotto e' rumore)
EQ_DEFAULT = {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_create": 1.25, "cache_create_1h": 2.0}
EQ_MODELS = {"claude-fable-5-1": {"cache_read": 0.025}, "claude-opus-5-5": {"cache_read": 0.05}, "claude-sonnet-5-5": {"cache_read": 0.05}}
PEER = re.compile(r'<cross-session-message\b[^>]*\bfrom-name="([^"]*)"[^>]*>\s*(.*?)\s*(?:</cross-session-message>|$)', re.S)
PHONE, WEB = "Dall'utente via telefono", "Dall'utente via web app"


# ------------------------------------------------------------------ pesi
def economics():
    """I moltiplicatori di fable-director (model-economics.json del plugin installato, poi quello dell'utente), se
    ci sono; altrimenti quelli scritti qui, dallo stesso listino."""
    d, m = dict(EQ_DEFAULT), {k: dict(v) for k, v in EQ_MODELS.items()}
    files = sorted(glob.glob(os.path.expanduser("~/.claude/plugins/cache/*/fable-director/*/model-economics.json")), key=os.path.getmtime)
    for f in files[-1:] + [os.path.expanduser("~/.claude/fable-director/model-economics.json")]:
        try:
            e = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            continue
        d.update({k: float(v) for k, v in (e.get("default") or {}).items() if k in EQ_DEFAULT})
        for name, mult in (e.get("models") or {}).items():
            m.setdefault(name, {}).update({k: float(v) for k, v in (mult or {}).items() if k in EQ_DEFAULT})
    return d, m


ECON = None


def mult(model):
    global ECON
    if ECON is None:
        ECON = economics()
    d, models = ECON
    out = dict(d)
    best = max((k for k in models if str(model or "").startswith(k)), key=len, default=None)
    if best:
        out.update(models[best])
    return out


def eq_of(usage, model):
    """Token input-equivalenti di un usage (come fd-telemetry.eq_tokens)."""
    m = mult(model)
    n = lambda k: int(usage.get(k) or 0)  # noqa: E731
    cc = n("cache_creation_input_tokens")
    cc1 = min(int(((usage.get("cache_creation") or {}) if isinstance(usage.get("cache_creation"), dict) else {}).get("ephemeral_1h_input_tokens") or 0), cc)
    return int(n("input_tokens") * m["input"] + n("output_tokens") * m["output"] + n("cache_read_input_tokens") * m["cache_read"]
               + (cc - cc1) * m["cache_create"] + cc1 * m["cache_create_1h"])


# ------------------------------------------------------------------ automazioni concordate
def automations_paths():
    c = (os.environ.get("CC_SUPERVISOR_CONFIG") or os.environ.get("TEAM_SUPERVISOR_CONFIG") or os.environ.get("CLAUDE_MASTER_CONFIG"))
    local = Path(os.environ.get("CM_AUTOMATIONS") or ((Path(c).parent if c else Path.home() / ".config" / "cc-supervisor") / "automations.json"))
    return HERE.parent / "automations.json", local


def automations():
    """[{name, match, origin?, since, agreed_by, what}]: quelle del plugin, completate o corrette per nome da quelle
    accanto alla config. `match` e' una regex sul testo del messaggio (per un messaggio di un'altra sessione, il testo
    dentro la busta); `origin` (facoltativo) il turnOrigin della trascrizione; `builtin: compact` le compattazioni."""
    out = {}
    for p in automations_paths():
        try:
            items = json.loads(p.read_text()).get("automations") or []
        except (OSError, ValueError, AttributeError):
            continue
        for a in items:
            if isinstance(a, dict) and a.get("name"):
                out[a["name"]] = dict(out.get(a["name"]) or {}, **a)
    return [a for a in out.values() if not a.get("disabled")]


def _text(content):
    if isinstance(content, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content):
            return None
        return " ".join(str(x.get("text") or "") for x in content if isinstance(x, dict) and x.get("type") == "text")
    return str(content or "")


def classify(row, text, autos):
    """(kind, name) dell'origine di un turno, o None se la riga non apre un turno (allegati, avvisi, riassunti)."""
    origin = row.get("turnOrigin")
    if row.get("isCompactSummary") or (row.get("isMeta") and not origin):
        return None
    t = text.strip()
    if not origin and (not t or t.startswith(("[Request interrupted", "<local-command", "<command-name>", "[Image"))):
        return None
    peer = PEER.search(t)
    sender, body = (peer.group(1), peer.group(2)) if peer else (None, t)
    for a in autos:
        if a.get("builtin") or not a.get("match"):
            continue
        if a.get("origin") and a["origin"] != origin:
            continue
        try:
            if re.search(a["match"], body) or (not peer and re.search(a["match"], t)):
                return "automazione", a["name"]
        except re.error:
            continue
    if body.startswith(PHONE):
        return "richiesta", "telefono"
    if body.startswith(WEB):
        return "richiesta", "web app"
    if re.match(r"Segnalazione del \d", body):
        return "richiesta", "segnalazione"
    if "[dai suggerimenti]" in body[:200]:
        return "richiesta", "suggerimento accettato"
    if origin == "human" or (not origin and not peer):
        return "richiesta", "terminale"
    if peer:
        return "sessione", sender or "?"
    if origin == "scheduled":
        return "automazione", "non in elenco: " + " ".join(body.split())[:40]
    if origin == "task_notification":
        return "altro", "lavori in sottofondo"
    return "altro", origin or "?"


# ------------------------------------------------------------------ lettura incrementale
def state_path():
    return Path(cm.expand(CFG.get("state_dir") or "~/.local/state/cc-supervisor")) / "master-usage.json"


def master_dirs():
    root = os.path.realpath(cm.expand((CFG.get("workspace") or {}).get("root") or "~"))
    slug = re.sub(r"[^A-Za-z0-9]", "-", root)
    dirs = []
    for a in (CFG.get("accounts") or {}).values():
        d = Path(cm.expand(a.get("config_dir") or "~/.claude")) / "projects" / slug
        if d.is_dir() and d not in dirs:
            dirs.append(d)
    return dirs


def _hour(ts):
    """«2026-10-25T20» in ora locale da un timestamp ISO della trascrizione."""
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%dT%H")
    except ValueError:
        return None


def _add(buckets, hour, kind, name, runs=0, eq=0):
    if not hour:
        return
    b = buckets.setdefault(hour, {}).setdefault(f"{kind}\t{name}", [0, 0])
    b[0] += runs
    b[1] += int(eq)


def scan(now=None, cache=None):
    """Aggiorna e restituisce la cache: {files: {path: {off, last, cur, model, after_compact}}, turns: {sid: [[ts,
    kind, name], …]}, buckets: {ora: {"kind\\tname": [turni, eq]}}}."""
    now = time.time() if now is None else now
    p = state_path()
    if cache is None:
        try:
            cache = json.loads(p.read_text())
        except (OSError, ValueError):
            cache = {}
    autos = automations()
    sig = hashlib.sha1(json.dumps(autos, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    if cache.get("autos") != sig:   # l'elenco e' cambiato: i turni gia' contati si riassegnano da capo
        cache = {"autos": sig}
    files, turns, buckets = cache.setdefault("files", {}), cache.setdefault("turns", {}), cache.setdefault("buckets", {})
    oldest = now - KEEP_DAYS * 86400
    mains, subs = [], []
    for d in master_dirs():
        mains += [Path(x) for x in glob.glob(str(d / "*.jsonl"))]
        subs += [Path(x) for x in glob.glob(str(d / "*" / "subagents" / "*.jsonl"))]
    for path in mains + subs:   # i file principali prima: i subagenti prendono l'origine del turno che li ha avviati
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_mtime < oldest:
            continue
        sub = path.parent.name == "subagents"
        sid = path.parent.parent.name if sub else path.stem
        k = str(path)
        f = files.get(k) or {"off": 0, "last": "", "cur": None, "model": "", "after_compact": False}
        if st.st_size < f["off"]:
            f = {"off": 0, "last": "", "cur": None, "model": "", "after_compact": False}
        if st.st_size > f["off"]:
            try:
                with open(path, "rb") as fh:
                    fh.seek(f["off"])
                    chunk = fh.read()
            except OSError:
                continue
            end = chunk.rfind(b"\n") + 1
            for line in chunk[:end].splitlines():
                if b'"user"' not in line and b'"usage"' not in line and b'compact_boundary' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ts = row.get("timestamp")
                hour = _hour(ts)
                typ = row.get("type")
                if typ == "system" and row.get("subtype") == "compact_boundary":
                    meta = row.get("compactMetadata") or {}
                    est = int(meta.get("preTokens") or 0) * mult(f["model"])["cache_read"]
                    _add(buckets, hour, "automazione", "compattazioni", runs=1, eq=est)
                    f["after_compact"] = True
                    continue
                if typ == "user" and not sub:
                    if row.get("isCompactSummary"):
                        _add(buckets, hour, "automazione", "compattazioni", eq=len(_text(row["message"].get("content")) or "") / 4 * mult(f["model"])["output"])
                        continue
                    text = _text((row.get("message") or {}).get("content"))
                    if text is None:
                        continue
                    c = classify(row, text, autos)
                    if c:
                        f["cur"] = list(c)
                        _add(buckets, hour, c[0], c[1], runs=1)
                        turns.setdefault(sid, []).append([ts, c[0], c[1]])
                    continue
                if typ != "assistant":
                    continue
                msg = row.get("message") or {}
                u = msg.get("usage") if isinstance(msg, dict) else None
                if not isinstance(u, dict):
                    continue
                mid = str(msg.get("id") or row.get("requestId") or "")
                if mid and mid == f["last"]:
                    continue   # lo stesso messaggio scritto a pezzi
                f["last"] = mid
                model = str(msg.get("model") or "")
                if model and not model.startswith("<"):
                    f["model"] = model
                eq = eq_of(u, model or f["model"])
                if f.get("after_compact") and not sub:
                    f["after_compact"] = False
                    _add(buckets, hour, "automazione", "compattazioni", eq=eq)
                    continue
                cur = f["cur"]
                if sub and not cur:
                    cur = next(([t[1], t[2]] for t in reversed(turns.get(sid) or []) if str(t[0]) <= str(ts)), None)
                    f["cur"] = cur
                kind, name = cur or ("altro", "prima del periodo")
                _add(buckets, hour, kind, name, eq=eq)
            f["off"] += end
        files[k] = f
    cut = time.strftime("%Y-%m-%dT%H", time.localtime(oldest))
    cache["buckets"] = {h: v for h, v in buckets.items() if h >= cut}
    iso_cut = dt.datetime.fromtimestamp(oldest, dt.timezone.utc).strftime("%Y-%m-%dT%H")
    cache["turns"] = {s: [t for t in v if str(t[0]) >= iso_cut][-500:] for s, v in turns.items()}
    cache["turns"] = {s: v for s, v in cache["turns"].items() if v}
    cache["files"] = {k: v for k, v in files.items() if os.path.exists(k)}
    cache["at"] = int(now)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False))
        tmp.replace(p)
    except OSError:
        pass
    return cache


# ------------------------------------------------------------------ resoconto
def report(cache, days=7, now=None):
    now = time.time() if now is None else now
    since = time.strftime("%Y-%m-%dT%H", time.localtime(now - days * 86400))
    day_ago = time.strftime("%Y-%m-%dT%H", time.localtime(now - 86400))
    autos = {a["name"]: a for a in automations()}
    hours, per, last24 = [], {}, {}
    for h in sorted(cache.get("buckets") or {}):
        if h < since:
            continue
        tot, auto = 0, 0
        for key, (runs, eq) in cache["buckets"][h].items():
            kind, name = key.split("\t", 1)
            o = per.setdefault(key, {"name": name, "kind": kind, "runs": 0, "eq": 0})
            o["runs"] += runs
            o["eq"] += eq
            tot += eq
            auto += eq if kind == "automazione" else 0
            if h >= day_ago:
                last24[key] = last24.get(key, 0) + eq
        hours.append({"hour": h, "eq": tot, "automazioni": auto})
    total = sum(o["eq"] for o in per.values())
    origins = sorted(per.values(), key=lambda o: (o["kind"] != "automazione", -o["eq"], o["name"]))
    for o in origins:
        o["share"] = round(o["eq"] / total, 4) if total else 0
        o["last_24h"] = last24.get(f"{o['kind']}\t{o['name']}", 0)
        a = autos.get(o["name"]) if o["kind"] == "automazione" else None
        o["known"] = bool(a) if o["kind"] == "automazione" else None
        if a:
            o.update({x: a.get(x) for x in ("since", "agreed_by", "what") if a.get(x)})
    auto_eq = sum(o["eq"] for o in origins if o["kind"] == "automazione")
    alerts = []
    if total and auto_eq / total > AUTO_SHARE:
        alerts.append({"key": "quota:master-automazioni", "text": f"Le automazioni pesano il {round(100 * auto_eq / total)} % del consumo della master in {days} giorni",
                       "prompt": f"Guarda supervisor quota --master --days {days} e proponimi quali automazioni alleggerire o diradare."})
    for o in origins:
        if o["kind"] != "automazione" or not total:
            continue
        mean = o["eq"] / days
        if o["known"] and o["last_24h"] > DOUBLE * mean and o["eq"] / total >= DOUBLE_MIN_SHARE and days > 1:
            alerts.append({"key": f"quota:master-raddoppio:{o['name']}"[:120], "text": f"«{o['name']}» ha consumato più del doppio della sua media nelle ultime 24 ore"[:200],
                           "prompt": f"Guarda supervisor quota --master: perché «{o['name']}» è raddoppiata nelle ultime 24 ore? Proponimi come riportarla alla media."})
        if not o["known"]:
            h = hashlib.sha1(o["name"].encode()).hexdigest()[:8]
            alerts.append({"key": f"quota:master-fuori-elenco:{h}", "text": f"Un'automazione non in elenco gira sulla master: {o['name'][15:]}"[:200].rstrip(),
                           "prompt": f"In supervisor quota --master c'è un'automazione non in elenco («{o['name']}»). Dimmi da dove parte e se va aggiunta all'elenco o fermata."})
    return {"days": days, "total_eq": total, "automations_eq": auto_eq, "automations_share": round(auto_eq / total, 4) if total else 0,
            "hours": hours, "origins": origins, "alerts": alerts}


def fmt(n):
    n = float(n)
    return f"{n / 1e6:.1f} M".replace(".", ",") if n >= 1e6 else (f"{n / 1e3:.0f} k" if n >= 1e3 else f"{n:.0f}")


def text_report(r):
    out = [f"Master, ultimi {r['days']} giorni: {fmt(r['total_eq'])} token eq; automazioni {round(100 * r['automations_share'])} %"]
    for kind, label in (("automazione", "Automazioni concordate"), ("richiesta", "Richieste"), ("sessione", "Sessioni"), ("altro", "Altro")):
        rows = [o for o in r["origins"] if o["kind"] == kind]
        if not rows:
            continue
        out.append(label)
        for o in rows:
            extra = f"  (dal {o['since']}, {o['agreed_by']})" if o.get("since") and o.get("agreed_by") else ""
            out.append(f"  {o['name'][:40]:<40} {o['runs']:>5} turni  {fmt(o['eq']):>8}  {100 * o['share']:5.1f} %  24 h {fmt(o['last_24h'])}{extra}")
    today = [h for h in r["hours"] if h["hour"] >= time.strftime("%Y-%m-%dT00")]
    if today:
        out.append("Oggi per ora: " + ", ".join(f"{h['hour'][-2:]} {fmt(h['eq'])}" for h in today))
    for a in r["alerts"]:
        out.append("AVVISO " + a["text"])
    return "\n".join(out)


def suggest(alerts):
    """Gli avvisi come suggerimenti «quota» (supervisor suggest add: «Mai» dall'app li salta da solo)."""
    n = 0
    for a in alerts:
        p = subprocess.run([sys.executable, str(HERE / "cm-suggest.py"), "add", "--kind", "quota", "--key", a["key"], "--text", a["text"],
                            "--prompt", a["prompt"], "--ttl", "12h"], capture_output=True, text=True)
        n += p.returncode == 0 and bool(p.stdout.strip())
    return n


def main(argv):
    days = 7
    if "--days" in argv:
        i = argv.index("--days")
        try:
            days = max(1, min(KEEP_DAYS - 1, int(argv[i + 1])))
        except (IndexError, ValueError):
            print("quota --master: --days wants a number of days", file=sys.stderr)
            return 2
    r = report(scan(), days)
    if "--suggest" in argv:
        r["suggested"] = suggest(r["alerts"])
    if "--json" in argv:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(text_report(r))
        if "--suggest" in argv:
            print(f"suggerimenti pubblicati: {r['suggested']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
