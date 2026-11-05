#!/usr/bin/env python3
"""supervisor feedback — le note di claude-observe di ogni plugin installato, per il telefono (contratto 1.105, 02/11/2026).

Richiesta del maintainer del 28/10 alle 22:22, approvata il 02/11 alle 15:39 (compito supervisor-feedback-28-10): una
sezione dell'app per le note, con il consenso a un si' solo («Sempre in forma anonima») acceso o spento dal telefono e
l'invio da li'. Mai un invio senza consenso o senza un tocco dell'utente: le tre op del relay vogliono un `device`.

PLUGIN: quelli installati in un account (installed_plugins.json) che portano una copia di observe (observe/tool.json);
uno per nome, di due account vince la versione piu' nuova. Casella e config di observe sono comuni a tutte le copie.

COME: niente logica di observe rifatta qui. Ogni lettura e ogni invio caricano la copia di observe.py del plugin stesso, in
un processo a parte (`probe`): la config e il TOOL di observe si leggono all'import, e il demone del relay non deve
tenerli. Cosi' bozza, hash, pulizia, invio anonimo, consenso e conteggi locali sono quelli della versione installata.

- state(): {at, plugins: [{plugin, version, new, pending, security, anonymous, consent{on, since}, last_sent{at, url} |
  null, top: [{call, error, count}]}], notice}. new = da smistare, pending = cio' che un invio porterebbe (mai le
  security: hanno il percorso privato, non dal telefono), top = i tre errori piu' frequenti fra quelli, ripuliti come
  li manda observe. last_sent: l'ultimo invio dal telefono (feedback-sent.json nella state_dir), altrimenti l'URL
  dell'ultimo record inviato, senza ora. notice: l'avviso del suggerimento scritto. None senza plugin.
- consent(plugin, on): il tocco vale come il si' dell'utente; lo salva observe (tools.<plugin>.always_anonymous). Non
  manda niente da solo.
- send(plugin, hash): senza hash l'anteprima {plugin, n, title, body, hash}; con l'hash dell'anteprima l'invio anonimo
  di quel testo (report --send HASH --anonymous di observe: un hash diverso e' rifiutato).
- text(plugin, testo): un suggerimento scritto all'endpoint anonimo, ripulito; diventa una issue pubblica.
- app_draft(kind, testo, tech, shot) e app_send(hash, choice) (contratto 1.121, 04/11/2026, compito
  supervisor-feedback-app-04-11): il Feedback dell'app, «Suggerimento» o «Segnala un bug». Il primo passo lo registra come
  osservazione di supervisor-app (nota, nella casella di observe, con i dati tecnici e la schermata se la persona l'ha
  voluta) e rende la bozza anonimizzata, nel formato che il servizio anonimo accetta, con le tre scelte; il secondo, con
  l'hash della bozza, la manda: «Invia dal mio GitHub» = il link della issue precompilata (etichette app e il tipo) che
  l'app apre col GitHub della persona; «Invia anonimo» = il servizio di observe; «Non ora» = l'osservazione resta nella
  casella. 1.123 (05/11/2026): la schermata va su tutte e due le strade, caricata dal servizio; le issue e le schermate
  vanno nel repo PRIVATO frsorrentino/supervisor-feedback, perche' nessuno ripulisce quello che un'immagine mostra.

Uso: cm-feedback.py [--json]   (lo stato)
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)
TEXT_MAX = 4000
TOP = 3
CALL_MAX, ERROR_MAX = 60, 100   # /state sta in 8 KB


def msg(key, **kw):
    return cm.msg(CFG, key, **kw)


def _json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _vkey(v):
    return [int(x) for x in re.findall(r"\d+", str(v or ""))]


def roots():
    """[(nome, cartella del plugin, versione)] dei plugin installati che portano observe, uno per nome, per nome."""
    best = {}
    for _, a in CFG["accounts"].items():
        conf = Path(cm.expand(a["config_dir"]))
        inst = (_json(conf / "plugins" / "installed_plugins.json", {}) or {}).get("plugins") or {}
        for pid in sorted(inst):
            e = (inst[pid] or [{}])[0] or {}
            root = Path(str(e.get("installPath") or ""))
            tj = root / "observe" / "tool.json"
            if not e.get("installPath") or not tj.is_file():
                continue
            name = str((_json(tj, {}) or {}).get("name") or pid.partition("@")[0])
            v = str(e.get("version") or "")
            if name not in best or _vkey(v) > _vkey(best[name][2]):
                best[name] = (name, root, v)
    return [best[k] for k in sorted(best)]


def _root_of(plugin):
    return next((r for r in roots() if r[0] == plugin), None)


def sent_path():
    return Path(cm.expand(CFG["state_dir"])) / "feedback-sent.json"


def _remember_sent(plugin, url):
    p = sent_path()
    d = _json(p, {}) or {}
    d[plugin] = {"at": int(time.time()), "url": str(url or "")[:300] or None}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _probe(root, action, *args, stdin=None, timeout=120):
    """Il lavoro con la copia di observe del plugin, in un processo a parte: {ok, …} o {ok: False, error}."""
    try:
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "probe", str(root), action, *args], input=stdin or "",
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           env={k: v for k, v in os.environ.items() if k != "CLAUDE_OBSERVE_TOOL"})
        return json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {"ok": False, "error": (p.stderr.strip() or f"rc {p.returncode}")[-300:]}
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)[:300]}


def state():
    rs = roots()
    if not rs:
        return None
    sent = _json(sent_path(), {}) or {}
    plugins = []
    for name, root, v in rs:
        got = _probe(root, "row", timeout=60)
        if not got.get("ok"):
            continue
        row = got["row"]
        last = sent.get(name)
        row["last_sent"] = ({"at": last.get("at"), "url": last.get("url")} if isinstance(last, dict)
                            else {"at": None, "url": row.pop("_url")} if row.get("_url") else None)
        row.pop("_url", None)
        plugins.append({"plugin": name, "version": v or None, **{k: row[k] for k in ("new", "pending", "security", "anonymous", "consent", "last_sent", "top")}})
    if not plugins:
        return None
    return {"at": int(time.time()), "plugins": plugins, "notice": msg("feedback.notice")}


def consent(plugin, on):
    r = _root_of(plugin)
    if not r:
        return False, msg("feedback.no_plugin", plugin=plugin or "?")
    got = _probe(r[1], "consent", "on" if on else "off")
    if not got.get("ok"):
        return False, msg("feedback.no_endpoint", plugin=plugin) if got.get("error") == "no_endpoint" else str(got.get("error"))
    return True, msg("feedback.consent_on" if on else "feedback.consent_off", plugin=plugin)


def send(plugin, hash_=None):
    r = _root_of(plugin)
    if not r:
        return False, msg("feedback.no_plugin", plugin=plugin or "?")
    if not hash_:
        got = _probe(r[1], "preview")
        if not got.get("ok"):
            return False, msg("feedback.no_endpoint", plugin=plugin) if got.get("error") == "no_endpoint" else str(got.get("error"))
        return True, json.dumps({"plugin": plugin, **{k: got[k] for k in ("n", "title", "body", "hash")}}, ensure_ascii=False)
    got = _probe(r[1], "send", str(hash_))
    if not got.get("ok"):
        return False, msg("feedback.no_endpoint", plugin=plugin) if got.get("error") == "no_endpoint" else str(got.get("error"))
    _remember_sent(plugin, got.get("url"))
    return True, msg("feedback.sent", plugin=plugin, url=got.get("url") or "")


def text(plugin, body):
    r = _root_of(plugin)
    if not r:
        return False, msg("feedback.no_plugin", plugin=plugin or "?")
    body = str(body or "").strip()
    if not body:
        return False, msg("feedback.empty_text")
    if len(body) > TEXT_MAX:
        return False, msg("feedback.too_long", n=TEXT_MAX)
    got = _probe(r[1], "text", stdin=body)
    if not got.get("ok"):
        return False, msg("feedback.no_endpoint", plugin=plugin) if got.get("error") == "no_endpoint" else str(got.get("error"))
    _remember_sent(plugin, got.get("url"))
    return True, msg("feedback.sent", plugin=plugin, url=got.get("url") or "")


# ------------------------------------------------------------------ il Feedback dell'app (1.121)
APP = "supervisor-app"
APP_REPO = "frsorrentino/supervisor-feedback"   # privato, dove il servizio anonimo manda supervisor-app (OBSERVE_REPOS di report.php)
APP_KINDS = {"suggerimento": "suggestion", "bug": "bug"}   # il tipo dell'app → la parola della bozza; l'etichetta e' il tipo
APP_CHOICES = ("github", "anonymous", "later")
APP_TECH_KEYS, APP_TECH_VALUE = 10, 80
APP_DRAFT_TTL = 86400
HASH_RE = re.compile(r"[0-9a-f]{10}")


def app_dir():
    return Path(cm.expand(CFG["state_dir"])) / "feedback-app"


APP_GH_TTL = 600   # 1.124: quanto vale la risposta di gh sul repo privato


def github_ok(repo=None):
    """1.124: «Invia dal mio GitHub» apre una issue nel repo privato: funziona solo per chi lo legge. True solo se il gh di
    questo computer lo legge (gh api repos/<repo>: exit 0 = 200); 404, gh assente o senza login = False. La risposta resta
    APP_GH_TTL secondi in feedback-app/gh-ok.json; un gh che non risponde (timeout) non si ricorda."""
    repo = repo or APP_REPO
    f = app_dir() / "gh-ok.json"
    c = _json(f, None)
    if isinstance(c, dict) and c.get("repo") == repo and isinstance(c.get("ok"), bool) and 0 <= time.time() - float(c.get("at") or 0) < APP_GH_TTL:
        return c["ok"]
    try:
        ok = subprocess.run(["gh", "api", f"repos/{repo}", "--silent"], capture_output=True, timeout=15).returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        ok = False
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"repo": repo, "ok": ok, "at": int(time.time())}))
    except OSError:
        pass
    return ok


def _app_options(shot):
    return [{"id": "github", "label": msg("feedback.app_opt_github"), "description": msg("feedback.app_opt_github_desc_shot" if shot else "feedback.app_opt_github_desc", repo=APP_REPO)},
            {"id": "anonymous", "label": msg("feedback.app_opt_anon"), "description": msg("feedback.app_opt_anon_desc_shot" if shot else "feedback.app_opt_anon_desc")},
            {"id": "later", "label": msg("feedback.app_opt_later"), "description": msg("feedback.app_opt_later_desc")}]


def app_draft(kind, body, tech=None, shot=None):
    """Il primo passo: (True, JSON {hash, kind, label, title, body, screenshot, options[]}) o (False, ragione). shot =
    (estensione, bytes) solo col si' della persona, gia' controllato dal relay."""
    if kind not in APP_KINDS:
        return False, msg("feedback.app_bad_kind", kinds=", ".join(APP_KINDS))
    body = str(body or "").strip()
    if not body:
        return False, msg("feedback.app_empty")
    if len(body) > TEXT_MAX:
        return False, msg("feedback.app_long", n=TEXT_MAX)
    if tech is not None and not isinstance(tech, dict):
        return False, msg("feedback.app_bad_tech")
    tech = {str(k)[:20]: str(v)[:APP_TECH_VALUE] for k, v in list((tech or {}).items())[:APP_TECH_KEYS]
            if re.fullmatch(r"[a-z_]{1,20}", str(k)) and isinstance(v, (str, int, float)) and str(v).strip()}
    got = _probe(HERE.parent, "app_draft", stdin=json.dumps({"kind": kind, "text": body, "tech": tech}, ensure_ascii=False))
    if not got.get("ok"):
        return False, str(got.get("error"))
    d = app_dir()
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for f in d.glob("draft-*.json"):   # le bozze di ieri non servono piu'
        if now - f.stat().st_mtime > APP_DRAFT_TTL:
            f.unlink(missing_ok=True)
    shot_path = None
    if shot:
        shot_path = d / f"{got['rec']}.{shot[0]}"
        shot_path.write_bytes(shot[1])
        shot_path.chmod(0o600)
    h = got["hash"]
    (d / f"draft-{h}.json").write_text(json.dumps({"hash": h, "kind": kind, "title": got["title"], "body": got["body"], "rec": got["rec"],
                                                   "screenshot": str(shot_path) if shot_path else None, "at": int(now)}, ensure_ascii=False), encoding="utf-8")
    return True, json.dumps({"hash": h, "kind": kind, "label": kind, "title": got["title"], "body": got["body"], "screenshot": bool(shot_path),
                             "screenshot_notice": msg("feedback.app_shot_public") if shot_path else None,   # 1.123
                             "github_ok": github_ok(),   # 1.124: senza, l'app nasconde «Invia dal mio GitHub»
                             "options": _app_options(bool(shot_path))}, ensure_ascii=False)


def app_send(hash_, choice):
    """Il secondo passo, col tocco su una scelta: (True, JSON {choice, url, text}) o (False, ragione)."""
    if choice not in APP_CHOICES:
        return False, msg("feedback.app_bad_choice", choices=", ".join(APP_CHOICES))
    f = app_dir() / f"draft-{hash_}.json"
    dr = _json(f, None) if HASH_RE.fullmatch(str(hash_ or "")) else None
    if not isinstance(dr, dict) or time.time() - float(dr.get("at") or 0) > APP_DRAFT_TTL:
        return False, msg("feedback.app_no_draft")
    shot = bool(dr.get("screenshot"))
    url = None
    if choice == "later":
        text = msg("feedback.app_later")
    else:
        got = _probe(HERE.parent, "app_" + choice, stdin=json.dumps(dr, ensure_ascii=False))
        if not got.get("ok"):
            return False, str(got.get("error"))
        url, why = got.get("url"), got.get("shot_error")
        if choice == "anonymous":
            text = (msg("feedback.app_sent_anon_noshot", why=why, url=url or "") if shot and why
                    else msg("feedback.app_sent_anon_shot" if shot else "feedback.app_sent_anon", url=url or ""))
            _remember_sent(APP, url)
        else:
            text = msg("feedback.app_github_noshot", why=why) if shot and why else msg("feedback.app_github_shot" if shot else "feedback.app_github")
    f.unlink(missing_ok=True)   # una bozza si manda una volta sola
    return True, json.dumps({"choice": choice, "url": url, "text": text, "screenshot_url": got.get("shot_url") if choice != "later" else None},
                            ensure_ascii=False)


def _app_text(ob, d):
    """Titolo e corpo della bozza come li accetta il servizio anonimo (report.php: titolo «[plugin] field observations: »,
    prima riga «Collected on …», poi solo righe «- » o «  - note: »), ripuliti come una bozza di observe."""
    word = APP_KINDS[d["kind"]]
    clean = _cleaner(ob)
    lines = [ln.strip() for ln in clean(d["text"], TEXT_MAX).splitlines() if ln.strip()]
    pieces = [ln[i:i + 1900] for ln in lines for i in range(0, len(ln), 1900)] or ["…"]
    rows = [f"- {word}: {pieces[0]}"] + [f"  - note: {x}" for x in pieces[1:]]
    tech = " · ".join(f"{k} {clean(v, APP_TECH_VALUE)}" for k, v in (d.get("tech") or {}).items())
    if tech:
        rows.append(f"  - note: {tech}")
    head = re.sub(r"\s+", " ", pieces[0])
    title = f"[{APP}] field observations: {word} from the app, {head[:70]}{'…' if len(head) > 70 else ''}"
    body = "\n".join([f"Collected on {time.strftime('%Y-%m-%d')} by claude-observe (https://github.com/frsorrentino/claude-observe): "
                      f"a {word} written in the Supervisor app; parameter values are never stored.", "", *rows])
    title, _, body = ob.redact_out(f"{title}\n\n{body}", APP).partition("\n\n")
    return title.replace("\n", " ")[:300], body


APP_SHOT_MAX = 1048576   # il limite del servizio (report.php OBSERVE_IMAGE_MAX)
APP_SHOT_MIME = {"jpg": "image/jpeg", "png": "image/png"}


def _app_shot(d):
    """La schermata della bozza per il servizio: ((mime, bytes), None), (None, perche' no) o (None, None) senza schermata."""
    p = Path(d["screenshot"]) if d.get("screenshot") else None
    if p is None:
        return None, None
    mime = APP_SHOT_MIME.get(p.suffix.lstrip(".").lower())
    try:
        data = p.read_bytes() if mime else b""
    except OSError:
        data = b""
    if not data:
        return None, msg("feedback.app_shot_gone")
    if len(data) > APP_SHOT_MAX:
        return None, msg("feedback.app_shot_big")
    return (mime, data), None


def _app_mark(ob, rec, where):
    with ob.Box(APP) as box:
        for x in box.recs:
            if x.get("id") == rec:
                x["status"], x["reported"] = "reported", str(where or "")[:200]


# ------------------------------------------------------------------ probe: dentro il processo della copia di observe
def _observe(root):
    sp = importlib.util.spec_from_file_location("observe_copy", Path(root) / "observe" / "observe.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


def _quiet(fn, *a, **kw):
    """observe stampa i suoi messaggi: qui l'uscita e' una riga JSON. (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*a, **kw)
    return rc, out.getvalue(), err.getvalue()


def _cleaner(ob):
    """La pulizia di observe come la fa draft; una copia vecchia (prima di generalize) ha solo scrub."""
    if hasattr(ob, "generalize") and hasattr(ob, "own_names"):
        keep = ob.own_names()
        return lambda x, n: ob.generalize(ob.scrub(x or "", n), keep)
    return lambda x, n: ob.scrub(x or "", n)


def _endpoint(ob, name):
    return bool(ob.endpoint(name)) if hasattr(ob, "endpoint") else False


def _row(ob, name):
    pend = ob.pending(name)
    errs = sorted((r for r in pend if r.get("kind") != "note"), key=lambda r: -int(r.get("count") or 0))[:TOP]
    clean = _cleaner(ob)
    top = [{"call": clean(v.get("call"), CALL_MAX), "error": clean(v.get("error"), ERROR_MAX), "count": int(v.get("count") or 0)}
           for v in (ob.view(r) for r in errs)]
    if top:   # l'anonimizzatore una volta sola, una riga per errore; se cambia la forma, uno per campo
        lines = ob.redact_out("\n".join(f"{x['call']}\t{x['error']}" for x in top), name).split("\n")
        if len(lines) == len(top) and all("\t" in ln for ln in lines):
            for x, ln in zip(top, lines):
                x["call"], x["error"] = ln.split("\t", 1)
        else:
            for x in top:
                x["call"], x["error"] = ob.redact_out(x["call"], name), ob.redact_out(x["error"], name)
    since = ((ob.O.get("tools") or {}).get(name) or {}).get("always_anonymous")
    on = ob.consent(name) if hasattr(ob, "consent") else False
    done = [r for r in ob.read(ob.box_dir() / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.jsonl")
            if r.get("status") == "reported" and r.get("reported")]
    url = max(done, key=lambda r: float(r.get("last_seen") or 0))["reported"] if done else None
    return {"new": sum(1 for r in pend if (r.get("status") or "new") == "new"), "pending": len(pend),
            "security": len(ob.pending(name, security=True)), "anonymous": _endpoint(ob, name),
            "consent": {"on": bool(on), "since": str(since) if on and since else None}, "top": top, "_url": url}


def probe(root, action, args):
    ob = _observe(root)
    if action.startswith("app_"):
        return probe_app(ob, action, json.loads(sys.stdin.read() or "{}"))
    name = str(ob.TOOL.get("name") or "")
    t = ob.tools().get(name)
    if not t:
        return {"ok": False, "error": "disabled"}
    if action == "row":
        return {"ok": True, "row": _row(ob, name)}
    on = args[:1] == ["on"]
    if not _endpoint(ob, name) and not (action == "consent" and not on):   # spegnere vale sempre
        return {"ok": False, "error": "no_endpoint"}
    if action == "consent" and not hasattr(ob, "set_consent"):   # una copia di prima del 28/10
        return {"ok": False, "error": "no_endpoint"}
    if action == "consent":
        ob.set_consent(name, on)
        ob.bump(name, "always_on_phone" if on else "always_off_phone")
        return {"ok": True}
    if action == "preview":   # come report() senza --send: la stessa bozza, lo stesso hash
        recs = ob.pending(name)
        if not recs:
            return {"ok": True, "n": 0, "title": None, "body": None, "hash": None}
        target = ob.existing_issue(str(t.get("repo") or ""), recs) if ob.gh_ok() else None
        title, body, h = ob.draft(name, recs, target)
        ob.bump(name, "drafted_phone")
        return {"ok": True, "n": len(recs), "title": title, "body": body, "hash": h}
    if action == "send":
        ids = {r["id"] for r in ob.pending(name)}
        rc, out, err = _quiet(ob.report, name, args[0] if args else "", anonymous=True)
        if rc != 0:
            return {"ok": False, "error": (err.strip() or out.strip())[-300:]}
        urls = [r.get("reported") for r in ob.read(ob.box_dir() / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.jsonl") if r.get("id") in ids and r.get("reported")]
        return {"ok": True, "url": urls[0] if urls else None}
    if action == "text":
        raw = sys.stdin.read()
        clean = ob.redact_out(_cleaner(ob)(raw, TEXT_MAX), name)
        head = clean.strip().splitlines()[0] if clean.strip() else ""
        title = f"[{name}] suggestion from the app: {head[:70]}{'…' if len(head) > 70 else ''}"
        body = (f"Suggestion written in the Supervisor app on {time.strftime('%Y-%m-%d')} and sent anonymously by claude-observe "
                "(https://github.com/frsorrentino/claude-observe).\n\n" + clean)
        try:
            url = ob.send_anonymous(name, t, title, body, [], False)
        except (ValueError, OSError) as e:
            return {"ok": False, "error": str(e)[:300]}
        ob.bump(name, "text_sent_phone")
        return {"ok": True, "url": url}
    return {"ok": False, "error": f"action {action}?"}


def probe_app(ob, action, d):
    """1.121: i passi del Feedback dell'app con la copia di observe di supervisor stesso."""
    if action == "app_draft":
        title, body = _app_text(ob, d)
        key = ob.normalize(f"app {d['kind']} {d['text']}")
        r = ob.record(APP, ob.rid(APP, key), {"source": "app", "kind": "note", "call": f"app {d['kind']}",
                                              "error": ob.scrub(d["text"].strip().splitlines()[0], 500), "note": ob.scrub(d["text"], TEXT_MAX),
                                              "key": key, "account": ob.account_name(), "project": APP, "app_kind": d["kind"],
                                              "tech": d.get("tech") or {}})
        h = hashlib.sha256(f"app\n{title}\n\n{body}".encode()).hexdigest()[:10]
        return {"ok": True, "rec": r["id"], "title": title, "body": body, "hash": h}
    if action == "app_anonymous":
        if not ob.endpoint(APP):
            return {"ok": False, "error": msg("feedback.no_endpoint", plugin=APP)}
        img, why = _app_shot(d)
        try:   # 1.123: la schermata va col testo; il servizio la carica nel repo pubblico delle immagini e la linka
            url = ob.send_anonymous(APP, {"repo": APP_REPO}, d["title"], d["body"], [], False, labels=["app", d["kind"]], image=img)
        except (ValueError, OSError) as e:
            if not img:
                return {"ok": False, "error": str(e)[:300]}
            # un servizio che rifiuta l'immagine (il limite del giorno, un report.php di prima che prende solo i sei
            # campi): il testo parte lo stesso, una volta, e la risposta dice perche' senza la schermata
            why = str(e)[:200]
            try:
                url = ob.send_anonymous(APP, {"repo": APP_REPO}, d["title"], d["body"], [], False)
            except (ValueError, OSError) as e2:
                return {"ok": False, "error": str(e2)[:300]}
        _app_mark(ob, d.get("rec"), url)
        return {"ok": True, "url": url, "shot_error": why, "shot_url": None}
    if action == "app_github":
        img, why = _app_shot(d)
        body, shot_url = d["body"], None
        if img:   # 1.123: prima la schermata, dal servizio; il suo link e' gia' nel testo della issue
            if not ob.endpoint(APP):
                why = msg("feedback.no_endpoint", plugin=APP)
            else:
                try:
                    shot_url = ob.upload_image(APP, *img)
                    body += f"\n- screenshot: ![screenshot]({shot_url})"
                except (ValueError, OSError) as e:
                    why = str(e)[:200]
        url = ob.issue_link(APP_REPO, d["title"], body, limit=7400) + "&" + urllib.parse.urlencode({"labels": f"app,{d['kind']}"})
        _app_mark(ob, d.get("rec"), "github link")
        return {"ok": True, "url": url, "shot_error": why, "shot_url": shot_url}
    return {"ok": False, "error": f"action {action}?"}


def main(argv):
    if argv[:1] == ["probe"] and len(argv) >= 3:
        try:
            res = probe(argv[1], argv[2], argv[3:])
        except Exception as e:   # noqa: BLE001 — la risposta e' sempre una riga JSON
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if argv in ([], ["--json"]):
        print(json.dumps(state(), ensure_ascii=False, indent=None if argv else 1))
        return 0
    print(__doc__.strip().splitlines()[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
