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

Uso: cm-feedback.py [--json]   (lo stato)
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import time
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
