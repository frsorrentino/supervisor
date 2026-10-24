#!/usr/bin/env python3
"""Le domande dei server MCP sul telefono (24/10/2026, contratto 1.83; proposta docs/ricerche del 24/10, sezione 1).

Un server MCP che chiede un dato a meta' di uno strumento (elicitation) fa partire l'hook `Elicitation` di Claude Code;
cm-hook.py lo passa qui. Senza telefono accoppiato, con la funzione spenta (`hooks.elicitation.enabled`) o con una
domanda che il telefono non sa mostrare, l'hook non stampa niente e la domanda compare nel terminale, come prima.
Altrimenti:

- la domanda va in <state_dir>/elicit/<id>.json, il relay la pubblica in /state.elicitations e manda l'avviso;
- i campi vengono dallo schema: un `enum` (o `oneOf` con `const`) diventa un tasto per valore, un booleano Si'/No,
  testo e numeri un campo da scrivere; un tipo che non conosciamo → la domanda resta al terminale;
- l'hook aspetta <id>.answer.json fino a `hooks.elicitation.wait_s` (120 s); la risposta la scrive solo il relay,
  dall'op `elicit` di un dispositivo accoppiato (origine «relay»), dopo averla controllata contro i campi;
- senza risposta in tempo l'hook non stampa niente e la domanda torna al terminale. Mai una risposta da soli: nessuna
  regola permanente, nessun default riempito qui.

Modo URL (un login nel browser): la scheda mostra il link; l'hook non aspetta e lascia il dialogo. ElicitationResult
(la risposta data al terminale) toglie la scheda della sessione.
"""
import json
import os
import secrets
import sys
import time
from pathlib import Path

WAIT_S = 120
WAIT_MAX_S = 140
POLL_S = 0.5
MAX_FIELDS = 8
MAX_OPTIONS = 12
TEXT_MAX = 2000
ACTIONS = ("accept", "decline", "cancel")
FORMATS = ("email", "uri", "date", "date-time")


def _short(x, n):
    return " ".join(str(x or "").split())[:n]


def field_of(key, prop, required):
    """Un campo del contratto da una proprieta' dello schema, None se il telefono non sa mostrarla."""
    if not isinstance(prop, dict):
        return None
    f = {"key": str(key)[:64], "title": _short(prop.get("title") or key, 80), "required": bool(required)}
    if prop.get("description"):
        f["description"] = _short(prop["description"], 300)
    t = prop.get("type")
    opts = None
    if isinstance(prop.get("oneOf"), list):   # enum con titoli: [{const, title}]
        if not all(isinstance(o, dict) and isinstance(o.get("const"), str) for o in prop["oneOf"]):
            return None
        opts = [{"value": o["const"], "label": _short(o.get("title") or o["const"], 40)} for o in prop["oneOf"]]
    elif isinstance(prop.get("enum"), list):
        vals = prop["enum"]
        if not all(isinstance(v, str) for v in vals):
            return None
        names = prop.get("enumNames") if isinstance(prop.get("enumNames"), list) and len(prop["enumNames"]) == len(vals) else vals
        opts = [{"value": v, "label": _short(n, 40)} for v, n in zip(vals, names)]
    if opts is not None:
        if not 1 <= len(opts) <= MAX_OPTIONS or (t not in (None, "string")):
            return None
        f["kind"], f["options"] = "choice", opts
        if isinstance(prop.get("default"), str) and any(o["value"] == prop["default"] for o in opts):
            f["default"] = prop["default"]
        return f
    if t == "boolean":
        f["kind"] = "boolean"
        if isinstance(prop.get("default"), bool):
            f["default"] = prop["default"]
        return f
    if t in ("number", "integer"):
        f["kind"] = t
        for src, dst in (("minimum", "min"), ("maximum", "max")):
            v = prop.get(src)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                f[dst] = v
        d = prop.get("default")
        if isinstance(d, (int, float)) and not isinstance(d, bool):
            f["default"] = d
        return f
    if t == "string":
        f["kind"] = "text"
        for src, dst in (("minLength", "min_length"), ("maxLength", "max_length")):
            v = prop.get(src)
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                f[dst] = v
        if prop.get("format") in FORMATS:
            f["format"] = prop["format"]
        if isinstance(prop.get("default"), str):
            f["default"] = prop["default"][:TEXT_MAX]
        return f
    return None


def fields_of(schema):
    """I campi del contratto dallo schema `requested_schema`; None se anche un solo campo non si puo' mostrare
    (la domanda resta tutta al terminale: mai una risposta a meta')."""
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        return None
    props = schema.get("properties") or {}
    if not isinstance(props, dict) or not props or len(props) > MAX_FIELDS:
        return None
    req = set(schema.get("required") or []) if isinstance(schema.get("required"), list) else set()
    out = []
    for k, p in props.items():
        f = field_of(k, p, k in req)
        if f is None:
            return None
        out.append(f)
    return out


def check_content(fields, content):
    """(contenuto pulito, None) o (None, (chiave del messaggio, argomenti)): i valori del telefono contro i campi."""
    if not isinstance(content, dict):
        content = {}
    known = {f["key"] for f in fields}
    extra = sorted(k for k in content if k not in known)
    if extra:
        return None, ("elicit_unknown_field", {"key": extra[0]})
    out = {}
    for f in fields:
        k, v = f["key"], content.get(f["key"])
        if v is None or v == "":
            if f["required"]:
                return None, ("elicit_required", {"key": k})
            continue
        kind = f["kind"]
        if kind == "choice":
            vals = [o["value"] for o in f["options"]]
            if v not in vals:
                return None, ("elicit_bad_choice", {"key": k, "options": ", ".join(o["label"] for o in f["options"])})
        elif kind == "boolean":
            if not isinstance(v, bool):
                return None, ("elicit_bad_boolean", {"key": k})
        elif kind in ("number", "integer"):
            try:
                n = float(v) if not isinstance(v, bool) else None
            except (TypeError, ValueError):
                n = None
            if n is None or n != n or n in (float("inf"), float("-inf")):
                return None, ("elicit_bad_number", {"key": k})
            if kind == "integer":
                if n != int(n):
                    return None, ("elicit_bad_integer", {"key": k})
                n = int(n)
            elif n == int(n) and isinstance(v, int):
                n = int(n)
            if ("min" in f and n < f["min"]) or ("max" in f and n > f["max"]):
                return None, ("elicit_out_of_range", {"key": k, "min": f.get("min", "-"), "max": f.get("max", "-")})
            v = n
        else:
            if not isinstance(v, str):
                return None, ("elicit_bad_text", {"key": k})
            v = v[:TEXT_MAX]
            if len(v) < f.get("min_length", 0) or len(v) > f.get("max_length", TEXT_MAX):
                return None, ("elicit_bad_length", {"key": k, "min": f.get("min_length", 0), "max": f.get("max_length", TEXT_MAX)})
        out[k] = v
    return out, None


def _dir(state_dir):
    return Path(state_dir) / "elicit"


def _write(path, obj):
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _read(path):
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def paired(relay_cfg):
    """Il telefono c'e': relay acceso e almeno un dispositivo in devices.json."""
    if not (relay_cfg or {}).get("enabled"):
        return False
    d = Path(os.path.expanduser(str(relay_cfg.get("dir") or "~/.cc-supervisor/relay")))
    devs = _read(d / "devices.json")
    return bool(devs)


def request_of(p, session, now, wait_s):
    """La domanda per /state, o None se resta al terminale (schema che il telefono non sa mostrare, modo ignoto)."""
    mode = p.get("mode") or "form"
    r = {"id": "e-" + secrets.token_hex(6), "session": session, "session_id": str(p.get("session_id") or ""),
         "server": _short(p.get("mcp_server_name") or "?", 80), "message": str(p.get("message") or "")[:1000],
         "mode": mode, "asked_at": int(now), "expires_at": int(now + wait_s)}
    if p.get("elicitation_id"):
        r["elicitation_id"] = str(p["elicitation_id"])[:200]
    if mode == "url":
        url = str(p.get("url") or "")
        if not url.startswith(("https://", "http://")):
            return None
        r["url"] = url[:2000]
        return r
    if mode != "form":
        return None
    fields = fields_of(p.get("requested_schema"))
    if fields is None:
        return None
    r["fields"] = fields
    return r


def hook(p, state_dir, cfg_hook, relay_cfg, session, push, now=None, sleep=time.sleep, clock=time.time):
    """L'hook Elicitation: (testo da stampare o "", esito per il ledger). `push` chiede una push al relay."""
    if not (cfg_hook or {}).get("enabled", True):
        return "", "off"
    if not paired(relay_cfg):
        return "", "unpaired"
    wait_s = max(10, min(int((cfg_hook or {}).get("wait_s") or WAIT_S), WAIT_MAX_S))   # l'hook ha 150 s in hooks.json
    now = clock() if now is None else now
    r = request_of(p, session, now, wait_s)
    if r is None:
        return "", "terminal"
    d = _dir(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    req, ans = d / f"{r['id']}.json", d / f"{r['id']}.answer.json"
    _write(req, r)
    push()
    if r["mode"] == "url":   # il link sul telefono; il dialogo resta al terminale, la scheda scade o la toglie il risultato
        return "", "url"
    out, outcome = "", "timeout"
    try:
        while clock() < r["expires_at"]:
            a = _read(ans) if ans.exists() else None
            if a and a.get("origin") == "relay" and a.get("id") == r["id"] and a.get("action") in ACTIONS:
                if a["action"] == "accept":
                    content, err = check_content(r["fields"], a.get("content"))
                    if err is None:
                        out = json.dumps({"hookSpecificOutput": {"hookEventName": "Elicitation", "action": "accept", "content": content}}, ensure_ascii=False)
                        outcome = "accept"
                else:
                    out = json.dumps({"hookSpecificOutput": {"hookEventName": "Elicitation", "action": a["action"]}})
                    outcome = a["action"]
                break
            sleep(POLL_S)
    finally:
        req.unlink(missing_ok=True)
        ans.unlink(missing_ok=True)
        push()
    return out, outcome


def clear_session(state_dir, session_id):
    """ElicitationResult: la domanda e' stata risposta al terminale → via le schede di quella sessione."""
    d = _dir(state_dir)
    n = 0
    for f in d.glob("e-*.json") if d.is_dir() else []:
        if f.name.endswith(".answer.json"):
            continue
        r = _read(f)
        if r and r.get("session_id") == session_id:
            f.unlink(missing_ok=True)
            n += 1
    return n


def pending(state_dir, now):
    """Le domande aperte per /state, dalla piu' vecchia; quelle scadute (hook morto, link mai aperto) si cancellano."""
    d = _dir(state_dir)
    out = []
    for f in sorted(d.glob("e-*.json")) if d.is_dir() else []:
        if f.name.endswith(".answer.json") or f.name.endswith(".tmp"):
            continue
        r = _read(f)
        if not r or int(r.get("expires_at") or 0) <= now:
            f.unlink(missing_ok=True)
            (d / f"{f.stem}.answer.json").unlink(missing_ok=True)
            continue
        out.append(r)
    return sorted(out, key=lambda r: (r.get("asked_at") or 0, r.get("id") or ""))


def for_state(r, session_name):
    """La voce di /state.elicitations: niente session_id ne' elicitation_id (restano sul PC)."""
    e = {"id": r["id"], "session": session_name, "server": r.get("server") or "?", "message": r.get("message") or "",
         "mode": r.get("mode") or "form", "asked_at": int(r.get("asked_at") or 0), "expires_at": int(r.get("expires_at") or 0)}
    if e["mode"] == "url":
        e["url"] = r.get("url") or ""
    else:
        e["fields"] = r.get("fields") or []
    return e


def answer(state_dir, eid, action, content, by, device, now):
    """L'op `elicit` del relay: (ok, chiave del messaggio, argomenti). Scrive <id>.answer.json con origine «relay»
    solo per una domanda aperta in modo form e con valori validi; l'hook la legge e risponde al server."""
    eid = str(eid or "").strip()
    d = _dir(state_dir)
    if not (eid.startswith("e-") and eid[2:].isalnum() and len(eid) <= 40):
        return False, "elicit_not_waiting", {"id": eid or "?"}
    r = _read(d / f"{eid}.json")
    if not r or int(r.get("expires_at") or 0) <= now:
        return False, "elicit_not_waiting", {"id": eid}
    server = r.get("server") or "?"
    if r.get("mode") == "url":
        return False, "elicit_is_link", {"id": eid, "server": server}
    if action not in ACTIONS:
        return False, "elicit_bad_action", {"id": eid, "action": str(action or "?")[:20]}
    clean = None
    if action == "accept":
        clean, err = check_content(r.get("fields") or [], content)
        if err:
            return False, err[0], dict(err[1], id=eid)
    ans = d / f"{eid}.answer.json"
    if ans.exists():
        return False, "elicit_already", {"id": eid}
    _write(ans, {"id": eid, "origin": "relay", "action": action, **({"content": clean} if clean is not None else {}),
                 "by": str(by or "app")[:80], "device": str(device or "")[:20], "at": int(now)})
    return True, {"accept": "elicit_accepted", "decline": "elicit_declined", "cancel": "elicit_cancelled"}[action], {"id": eid, "server": server}


if __name__ == "__main__":
    sys.stderr.write("cm-elicit.py: modulo di cm-hook.py e cm-relay.py, non un comando\n")
    sys.exit(2)
