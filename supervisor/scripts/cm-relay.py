#!/usr/bin/env python3
"""supervisor relay — il PC sul bus dell'app Wear OS (fase 1 del design docs/plans/2026-09-12-app-polso-design.md).

  supervisor relay push [--dry-run|--async]   costruisce /state dalle sorgenti esistenti (sessioni, ledger, domande,
                                                  quota, recap, notte, inventario), lo cifra e lo scrive su Firebase RTDB;
                                                  scrive /events (dal diff con la push precedente) e sveglia l'orologio
                                                  con FCM; --dry-run stampa il JSON in chiaro e non tocca la rete;
                                                  --async torna subito e pusha entro relay.debounce_s (piu' richieste
                                                  ravvicinate = una push)
  supervisor relay pair [--timeout S] [--text] QR per il telefono (1.15) e codice a 6 cifre per l'orologio, X25519 su
                                                  /pair/<id> e /pair/<code>, chiave in <relay.dir>/key, uid accettati in
                                                  /allowed e devices.json; --text stampa il JSON del QR invece di disegnarlo
  supervisor relay serve                       il daemon: stream SSE su /cmd, esegue (allow-list), /result, ripubblica
  supervisor relay ensure|status|install|uninstall|off
  supervisor relay setup [--project ID] [--dry-run] [--yes]   il progetto Firebase, guidato e idempotente (cm-relay-setup)
  supervisor relay stats [--since T] [--until T]   p50/p95/max delle misure del log: durata, CPU e lock delle push,
                                                  eta' dello stato, fila ed esito dei comandi (T = HH:MM di oggi o ISO)

Ogni documento sul bus e' {"v":1,"enc":…} (cm-relay-crypto); la forma di /state e' il contratto v1 dell'app
(tests/fixtures/relay/state-*.json, costruito da cm-relay-state). Niente SDK Firebase: REST + SSE con urllib, token
OAuth2 dal service account (<relay.dir>/service-account.json, 0600, mai nel repo).

Prove: CM_RELAY_CM (dispatcher da usare per sessions/registry/quota/answer/talk/launch/screen), relay.firebase_url,
relay.token_url e relay.fcm_url sul Firebase finto (tests/lib/cm_test.fake_rtdb).
Prove isolate (1.15): con CC_SUPERVISOR_CONFIG che punta a una configurazione di prova (relay.dir, service_account e
firebase_url suoi) pair, push e serve lavorano solo li' — chiave, devices.json, /allowed e crontab della configurazione
principale non si toccano (test R5c); cosi' le prove dell'app non scollegano l'orologio vero.
"""
import base64
import binascii
import fcntl
import glob
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import resource
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
S = _load("cm-relay-state")
short_title = _load("cm-core").short_title   # 1.79: the short title of a task or an agenda row
C = _load("cm-relay-crypto")
DF = _load("cm-diff")   # 1.72: il diff dal telefono (voci, op diff e changes, pillola)
core = _load("cm-core")   # il cuore condiviso (16/09): ledger, transcript, icona, reopen — niente Telegram
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
R = CFG["relay"]
CM_BIN = os.environ.get("CM_RELAY_CM") or str(HERE / "supervisor")
BACKOFF = [1, 2, 5, 15, 30]
OPS = ("answer", "prompt", "launch", "follow", "unfollow", "resume", "reopen", "screen", "allow_all", "last", "model", "effort", "night_add", "night_remove", "report", "interrupt", "transcript", "file", "slash", "projects", "search", "timeline", "pair_add", "approve", "attention_done", "decision", "unpair", "night", "agenda", "agenda_set", "quota_route", "project_new", "permission_mode", "changes", "diff", "studio", "nights", "done", "elicit", "suggestion", "close_preview", "close")
LAST_MAX = 4000   # 1.4: l'ultimo messaggio per la lettura vocale — oltre, l'ascolto non regge


class RelayError(Exception):
    pass


# ------------------------------------------------------------------ file e log
def rdir():
    p = Path(cm.expand(R.get("dir") or "~/.cc-supervisor/relay"))
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def local_dir(sub=""):
    """1.35: la copia locale per l'API su 127.0.0.1 (cm-relay-web) — state.json, events.json, file/, share/."""
    p = rdir() / "local" / sub if sub else rdir() / "local"
    p.mkdir(parents=True, exist_ok=True)
    return p


def local_state_write(state):
    """Lo stato pubblicato, in chiaro, per /api/state e lo stream: scritto prima del bus, cosi' la web app locale
    resta aggiornata anche quando Firebase non risponde."""
    p = local_dir() / "state.json"
    tmp = p.with_suffix(".tmp")
    write_json(tmp, state)
    os.replace(tmp, p)


def local_events_add(events, now):
    """Gli eventi andati sul bus, anche in chiaro per /api/events; via quelli oltre relay.events_days."""
    p = local_dir() / "events.json"
    cur = read_json(p, {})
    cur.update({e["key"]: e for e in events})
    limit = now - float(R.get("events_days") or 7) * 86400
    cur = {k: e for k, e in cur.items() if float(e.get("ts") or 0) >= limit}
    tmp = p.with_suffix(".tmp")
    write_json(tmp, cur)
    os.replace(tmp, p)


def prune_local(now):
    """I file chiesti e gli allegati locali che nessuno ha usato entro SHARE_TTL_S, come /share e /file sul bus."""
    for sub in ("file", "share"):
        d = rdir() / "local" / sub
        for f in (d.iterdir() if d.is_dir() else []):
            try:
                if now - f.stat().st_mtime > SHARE_TTL_S:
                    f.unlink()
            except OSError:
                pass


def fallback_path():
    return rdir() / "fallback.json"


def fallback_mark(now):
    """Segna da quando il polso non riceve piu' (push o sveglia FCM fallite) e torna da quanti secondi dura."""
    d = read_json(fallback_path(), {})
    since = float(d.get("since") or 0)
    if not since:
        since = now
        write_json(fallback_path(), {"since": since})
    return max(0.0, now - since)


def fallback_clear():
    """Il polso riceve di nuovo: la scorta si spegne."""
    try:
        fallback_path().unlink()
    except OSError:
        pass


SEEN_SKEW_S = 60   # 1.20: /seen porta l'ora del server Firebase, gli eventi quella del PC: un minuto di tolleranza


def seen_devices():
    """1.20: {uid: epoch s} dell'ultima lettura di ogni dispositivo accoppiato (/seen/<uid>, scritto da orologio e
    telefono con l'ora del server). None se il bus non risponde."""
    try:
        raw = rtdb("GET", "seen") or {}
    except (urllib.error.URLError, OSError, ValueError, RelayError):
        return None
    devs = read_json(rdir() / "devices.json", {})
    out = {}
    for u, v in (raw.items() if isinstance(raw, dict) else []):
        if devs and u not in devs:
            continue   # un uid revocato da un pairing nuovo non conta
        if isinstance(v, (int, float)) and v > 0:
            out[u] = v / 1000 if v > 1e11 else float(v)
    return out


SEEN_CACHE_S = 60


def seen_cached(now):
    """1.32: /seen per state.devices senza una lettura del bus a ogni push: la si rifa' al massimo una volta al minuto
    (seen-cache.json); se il bus non risponde, l'ultima letta."""
    p = rdir() / "seen-cache.json"
    c = read_json(p, {})
    if now - float(c.get("at") or 0) < SEEN_CACHE_S and isinstance(c.get("seen"), dict):
        return c["seen"]
    got = seen_devices()
    if got is None:
        return c.get("seen") if isinstance(c.get("seen"), dict) else {}
    write_json(p, {"at": now, "seen": got})
    arrival_log(c.get("seen") if isinstance(c.get("seen"), dict) else {}, got)
    device_activity("arrival", got)
    return got


def device_activity_path():
    return rdir() / "device-activity.json"


def device_activity(field, times):
    """1.89 (28/10): l'ultimo segno di vita di ogni dispositivo accoppiato in device-activity.json, {uid: {arrival,
    cmd}} — field arrival (una lettura nuova di /seen) o cmd (un comando con il suo uid); times {uid: epoch s}. Fuori da
    relay.log, che ruota; un uid non accoppiato non entra, uno revocato esce."""
    devs = read_json(devices_path(), {})
    if not isinstance(devs, dict) or not devs:
        return
    p = device_activity_path()
    cur = read_json(p, {})
    cur = {u: v for u, v in (cur.items() if isinstance(cur, dict) else []) if u in devs and isinstance(v, dict)}
    changed = False
    for uid, at in times.items():
        if uid in devs and isinstance(at, (int, float)) and at > float(cur.get(uid, {}).get(field) or 0):
            cur.setdefault(uid, {})[field] = float(at)
            changed = True
    if changed:
        write_json(p, cur)


def arrival_log(old, new):
    """Fase 0 (07/10): per ogni dispositivo con una ricevuta nuova, quanto dopo l'ultima PUT di /state precedente.
    E' una stima: /seen porta l'ora del server Firebase, le PUT quella del PC."""
    pubs = sorted(x for x in read_json(rdir() / "pub-history.json", []) if isinstance(x, (int, float)))
    for uid, at in sorted(new.items()):
        if old.get(uid) == at:
            continue
        before = [x for x in pubs if x <= at + 2]
        if before and at - before[-1] < SEEN_SKEW_S * 10:
            log(f"arrivo: {uid[:6]} {_s(max(0.0, at - before[-1]))} s dopo la push")


def unseen_notice(events, now):
    """1.20 (29/09): il ripiego su Telegram quando il bus funziona ma NESSUN dispositivo legge. Gli eventi aspettano in
    pending.json; uno e' visto se un dispositivo ha scritto /seen dopo la sua ora; se nessuno l'ha visto entro
    relay.telegram_fallback_after_s va su Telegram, una volta. Finche' nessun dispositivo scrive /seen (un'app che non
    lo conosce) resta il solo ripiego sul bus che non prende: mai un doppione per chi non sa dire di aver letto."""
    pend_p = rdir() / "pending.json"
    pend = read_json(pend_p, []) + [{"key": e["key"], "ts": e["ts"], "title": e.get("title"), "body": e.get("body")} for e in events]
    after = float(R.get("telegram_fallback_after_s") or 0)
    seen = seen_devices()
    if seen is None:
        write_json(pend_p, pend)
        return 0
    if not seen or not after:
        pend_p.unlink(missing_ok=True)
        return 0
    last = max(seen.values())
    pend = [x for x in pend if last + SEEN_SKEW_S < float(x["ts"])]
    late = [x for x in pend if now - float(x["ts"]) >= after]
    write_json(pend_p, [x for x in pend if x not in late])
    if not late:
        return 0
    lines = [M("relay.fallback_unseen", min=int((now - last) // 60))]
    lines += [" ".join(str(x) for x in (e.get("title"), (e.get("body") or "").split("\n")[0]) if x) for e in late]
    log(f"nessun dispositivo ha letto {len(late)} eventi: ripiego su Telegram")
    try:
        return _load("cm-bot").send("\n".join(lines), watch_quiet=False)
    except Exception as ex:   # noqa: BLE001 — la scorta non deve mai far fallire una push
        log(f"fallback FAILED: {ex}")
        return 0


def fallback_notice(events, down_s):
    """Su Telegram quello che il polso non ha ricevuto, ma solo se il guasto dura da piu' di
    relay.telegram_fallback_after_s (0 = mai): un errore di rete di un minuto si recupera da solo, e un doppione
    per ogni singhiozzo era il difetto che il ritiro del bot doveva togliere. Torna le chat raggiunte."""
    after = float(R.get("telegram_fallback_after_s") or 0)
    if not after or down_s < after or not events:
        return 0
    lines = [M("relay.fallback_notice", min=int(down_s // 60))]
    lines += [" ".join(str(x) for x in (e.get("title"), e.get("body")) if x) for e in events]
    try:
        return _load("cm-bot").send("\n".join(lines), watch_quiet=False)
    except Exception as ex:   # noqa: BLE001 — la scorta non deve mai far fallire una push
        log(f"fallback FAILED: {ex}")
        return 0


LOG_MAX_BYTES = 5 * 1024 * 1024   # 07/10: il log arrivava a 7,7 MB; a 5 MB diventa relay.log.1 (uno solo)


def log_path():
    return Path(cm.expand(R.get("log") or "")) if R.get("log") else rdir() / "relay.log"


def log(line):
    p = log_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            if p.stat().st_size > LOG_MAX_BYTES:
                os.replace(p, p.with_name(p.name + ".1"))
        except OSError:
            pass
        with open(p, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")
    except OSError:
        pass


def read_json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def write_json(p, obj, mode=0o600):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, p)


def _claim(to, text):
    """88c86e (16/10): il segno d'origine lasciato prima di `talk`/`report` (cm-inbox.claim_origin); None se non riesce."""
    try:
        return _load("cm-inbox").claim_origin(to, text, "relay")
    except Exception:   # noqa: BLE001 — senza segno resta il ripiego di mark_origin
        return None


def relay_talk(name, text, *opts):
    """`supervisor talk` per un messaggio del relay, poi origin «relay» sul suo record nella casella di chi riceve
    (0319ab, 14/10): `talk` firma col nome della sessione che ha avviato il demone, e la master non distingueva un ok
    dal telefono da un messaggio di quella sessione. La CLI scrive sempre «session»."""
    t0 = time.time()
    claim = _claim(name, text)
    rc, out = run_cm("talk", name, text, *opts)
    if claim:
        claim.unlink(missing_ok=True)   # non consumato (talk non ha scritto nulla): non vale per il prossimo
    try:   # resta come ripiego: un record scritto senza il segno (talk vecchio) diventa «relay» qui
        _load("cm-inbox").mark_origin(name, text, t0, "relay")
    except Exception:   # noqa: BLE001 — il messaggio e' partito: l'origine e' un'informazione in piu'
        pass
    return rc, out


def run_cm(*args, timeout=None, env=None):
    p = subprocess.run([CM_BIN, *args], capture_output=True, text=True, timeout=int(timeout or R.get("command_timeout_s") or 120),
                       env=dict(os.environ, **env) if env else None)
    out = (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()
    return p.returncode, out


# ------------------------------------------------------------------ Firebase: REST, SSE, FCM
def sa_path():
    return Path(cm.expand(R.get("service_account") or "")) if R.get("service_account") else rdir() / "service-account.json"


def sa_project():
    return str(read_json(sa_path(), {}).get("project_id") or "")


def token():
    p = sa_path()
    if not p.is_file():
        raise RelayError(M("relay.no_sa", path=str(p)))
    return C.sa_token(p, R.get("token_url") or "https://oauth2.googleapis.com/token", rdir() / "token.json")


def base_url():
    u = str(R.get("firebase_url") or "").rstrip("/")
    if not u:
        raise RelayError(M("relay.no_url"))
    return u


def local_only():
    """13/10 (chiesto dal maintainer): senza relay.firebase_url il relay serve solo la web app locale di `relay web`:
    lo stato e gli eventi restano in <relay.dir>/local, niente bus, niente FCM, niente Telegram di scorta."""
    return not str(R.get("firebase_url") or "").strip()


_SSL_CTX = []


def ssl_ctx():
    """16/10 (relay al 50% di CPU): un contesto SSL per tutto il processo. urlopen senza contesto ne crea uno a ogni
    chiamata e rilegge i certificati di sistema: ~100 ms di CPU per chiamata su questo PC."""
    if not _SSL_CTX:
        _SSL_CTX.append(ssl.create_default_context())
    return _SSL_CTX[0]


def rtdb(method, path, obj=None, query=None, timeout=20):
    """Una chiamata REST a RTDB: GET/PUT/PATCH/DELETE su /<path>.json?access_token=… Torna il JSON (o None)."""
    q = {"access_token": token()}
    q.update(query or {})
    url = f"{base_url()}/{path.strip('/')}.json?{urllib.parse.urlencode(q)}"
    data = json.dumps(obj, ensure_ascii=False).encode() if obj is not None or method in ("PUT", "PATCH") else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None


def key():
    k = C.load_key(rdir())
    if not k:
        raise RelayError(M("relay.no_key"))
    return k


def put_encrypted(path, obj):
    return rtdb("PUT", path, C.encrypt(obj, key()), {"print": "silent"})


def fcm_send(data):
    """Un messaggio dati FCM sul topic (HTTP v1): sveglia l'orologio, che poi fa una GET di /state."""
    proj = sa_project()
    if not proj:
        return False
    url = f"{(R.get('fcm_url') or 'https://fcm.googleapis.com').rstrip('/')}/v1/projects/{proj}/messages:send"
    body = {"message": {"topic": str(R.get("fcm_topic") or "watch"), "data": {k: str(v) for k, v in data.items() if v is not None},
                        "android": {"priority": "high"}}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token()}"})
    with urllib.request.urlopen(req, timeout=20, context=ssl_ctx()) as r:
        r.read()
    return True


# ------------------------------------------------------------------ sorgenti di /state
def prefixes():
    return [a.get("tmux_prefix") or "" for a in CFG["accounts"].values()]


def account_for_path(path):
    """L'account dedotto dalla cartella come cm-launch.sh: cm-config.account_for, altrimenti il default."""
    return cm.account_for(CFG, path)[0]


def inventory():
    """Le cartelle lanciabili (come la skill e il bot): <radice>/<project_dir>/* e, per «.», anche il livello sotto."""
    ws = CFG["workspace"]
    root = Path(cm.expand(ws["root"]))
    excluded = set(ws.get("excluded_dirs") or [])
    out = []
    for pd in (ws.get("project_dirs") or ["."]):
        base = root if pd in (".", "") else root / pd
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if not d.is_dir() or d.name in excluded or d.name.startswith("."):
                continue
            out.append({"path": str(d), "name": d.name, "account": account_for_path(d), "last_used": last_used_of(d, account_for_path(d))})
            if pd in (".", ""):
                for dd in sorted(d.iterdir()):
                    if dd.is_dir() and dd.name not in excluded and not dd.name.startswith("."):
                        out.append({"path": str(dd), "name": dd.name, "account": account_for_path(dd), "last_used": last_used_of(dd, account_for_path(dd))})
    return out


def last_used_of(path, account):
    """(1.13) Quando la cartella e' stata usata l'ultima volta: la trascrizione piu' recente di Claude Code per quel
    percorso, nel config dell'account (epoch s), o None. Una lettura di cartella per progetto: l'app ordina i progetti
    dal piu' recente invece che per nome."""
    acc = (CFG.get("accounts") or {}).get(account or "") or {}
    d = Path(cm.expand(acc.get("config_dir") or "~/.claude")) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(path)))
    try:
        times = [f.stat().st_mtime for f in d.iterdir() if f.suffix == ".jsonl"]
    except OSError:
        return None
    return int(max(times)) if times else None


def _json_cmd(*args, expect="["):
    try:
        rc, out = run_cm(*args)
        return json.loads(out) if rc == 0 and out.strip().startswith(expect) else None
    except (ValueError, subprocess.TimeoutExpired):
        return None


def waiting_info(session_id):
    """Il file waiting/<sid> dell'hook: JSON {tool, input} (dal 0.4.0) o il solo nome del tool."""
    if not session_id:
        return {}
    p = Path(cm.expand(CFG["state_dir"])) / "waiting" / session_id
    try:
        raw = p.read_text().strip()
    except OSError:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {"tool": str(d)}
    except ValueError:
        return {"tool": raw}


def clear_waiting(session_id):
    """Via il flag dell'hook dopo una risposta andata a buon fine: l'hook lo toglie solo al prompt dopo (o allo Stop),
    e fino ad allora la sessione restava «waiting» con la domanda gia' risposta (14/09: answered e outcome partiti
    3 minuti dopo la risposta dal polso)."""
    if session_id:
        (Path(cm.expand(CFG["state_dir"])) / "waiting" / session_id).unlink(missing_ok=True)


def question_of(name, tool):
    """(testo intero, [etichette]) dallo schermo via `answer NAME --show` (cm-answer: piè di pagina esclusi)."""
    try:
        rc, out = run_cm("answer", name, "--show")
    except subprocess.TimeoutExpired:
        return "", []
    if rc != 0:
        return "", []
    q, opts = "", []
    for l in out.splitlines():
        m = re.match(r"^\s*(?:❯)?\s*(\d+)\.\s+(.*\S)\s*$", l)
        if m:
            opts.append(m.group(2).strip())
        elif " — " in l and not q:
            q = l.split(" — ", 1)[1].strip()
            head, sep, rest = q.partition(": ")
            if sep and (tool == "AskUserQuestion" or head == "-"):
                q = rest.strip()
    # «-» e' il segnaposto di --show per un pezzo che manca: mai come testo (07/10, «-: -» sul polso)
    return ("" if q == "-" else q), opts


def followed():
    return set(read_json(rdir() / "follow.json", []))


def awaiting(ledger=None, rows=None):
    """I nomi in attesa di risposta a un prompt dal polso. Una voce SCADE quando la sessione ha finito un turno
    dopo il prompt (uno stop nel ledger con ts successivo) o dopo relay.awaiting_max_s (30 min): senza questo
    una sessione restava «awaiting» per sempre, con il turno e il tool di ore prima (dal vivo, 14/09 08:22)."""
    p = rdir() / "awaiting.json"
    aw = read_json(p, {})
    if aw:
        led = ledger if ledger is not None else core.ledger_rows()
        ids = {(r.get("tmux") or r.get("name")): (r.get("session_id") or "") for r in (rows or [])}
        cap = float(R.get("awaiting_max_s") or 1800)
        now = time.time()
        keep = {}
        for name, since in aw.items():
            since = float(since or 0)
            if now - since > cap:
                continue
            sid = ids.get(name) or ""
            stops = [S.epoch(r.get("ts")) for r in led if r.get("session_id") == sid and r.get("event") == "stop"] if sid else []
            if any(t > since for t in stops):
                continue   # il turno e' finito: la risposta e' arrivata (o l'ha chiusa un altro canale)
            keep[name] = since
        if keep != aw:
            write_json(p, keep)
        aw = keep
    return set(aw)


def next_dated(cwd):
    """(«prossimo» del progetto, epoch della sua riga di recap) da docs/recap.md: la riga e' «- AAAA-MM-GG: …»,
    quindi la data c'e' sempre; senza data, None."""
    rel = str((CFG.get("recap") or {}).get("project_log") or "").strip()
    if not rel or not cwd:
        return "", None
    try:
        rows = [l for l in (Path(cwd) / rel).read_text().splitlines() if l.startswith("- ")]
    except OSError:
        return "", None
    if not rows:
        return "", None
    last = rows[-1]
    m = re.search(r"(?:prossimo|next): (.+)$", last)
    text = m.group(1).strip() if m else re.sub(r"^- \d{4}-\d{2}-\d{2}: ", "", last).strip()
    md = re.match(r"^- (\d{4})-(\d{2})-(\d{2}):", last)
    at = None
    if md:
        import datetime as _dt
        at = int(_dt.datetime(int(md.group(1)), int(md.group(2)), int(md.group(3))).timestamp())
    return text, at


def recap_today(now):
    label = time.strftime("%d_%m_%Y", time.localtime(now))
    cache = read_json(Path(cm.expand(CFG["state_dir"])) / "recap-summaries" / f"{label}.json", {})
    best = {}
    for k, v in (cache or {}).items():
        proj = str(k).split("|")[0]
        n = int(str(k).split("|")[-1]) if str(k).split("|")[-1].isdigit() else 0
        done, nxt = (v.get("fatto", ""), v.get("prossimo", "")) if isinstance(v, dict) else (str(v or ""), "")
        if done and (proj not in best or n > best[proj][0]):
            best[proj] = (n, done, nxt)
    items = [{"project": p, "done": d, "next": nx} for p, (_, d, nx) in sorted(best.items())]
    return {"date": time.strftime("%Y-%m-%d", time.localtime(now)), "items": items}


def night_max():
    return int((CFG.get("night") or {}).get("max_queued") or 8)


def night_master():
    """1.49 (09/10, asked by the app for the Recap cards, approved by the maintainer): the master's folder as a night
    target — workspace.root, named workspace.root_session_name, with the account its folder maps to. night_add takes
    that name as arg, like a project path."""
    d = os.path.realpath(cm.expand(CFG["workspace"]["root"]))
    return {"path": d, "name": CFG["workspace"].get("root_session_name") or "master", "account": account_for_path(d)}


def night_queue():
    try:
        p = Path(cm.expand(CFG["night"]["queue_file"]))
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    except (OSError, ValueError, KeyError):
        rows = []
    # 1.49: a job in the master's folder is named after the master, not after the folder
    mst = night_master()
    rows = [dict(r, name=mst["name"]) if os.path.realpath(str(r.get("dir") or "")) == mst["path"] else r for r in rows]
    running = next((r.get("name") or os.path.basename(r.get("dir") or "") or "job" for r in rows if r.get("started")), None)
    # 1.17: la coda intera, nell'ordine di esecuzione; il prompt ridotto (S.night_items) per stare negli 8 KB
    return {"queued": len(rows), "running": running, "items": rows, "master": True}


def tool_of(row):
    """(tool in corso, nota) dall'ultimo tool_use nella coda del transcript. La nota e' la `description` che
    Claude scrive per un comando Bash: dice l'intento dove il comando dice «cd …» (1.5). I percorsi di
    Read/Edit/Write si rendono assoluti rispetto alla cartella della sessione, cosi' chi legge sa quale progetto
    viene toccato."""
    try:
        path = core.transcript_of(row)
        if not path:
            return None, ""
        size = os.path.getsize(path)
        events, _ = core.transcript_events(path, max(0, size - 65536))
        tools = [e for e in events if e[0] == "tool"]
        if not tools:
            return None, ""
        name, inp = tools[-1][1], tools[-1][2]
        if isinstance(inp, dict):
            for k in ("file_path", "path"):
                v = str(inp.get(k) or "")
                if v and not v.startswith("/") and row.get("cwd"):
                    inp = dict(inp); inp[k] = os.path.normpath(os.path.join(row["cwd"], v))
        return core.tool_line(name, inp), core.tool_note(inp)
    except Exception:   # il transcript e' un extra: mai bloccare la push
        return None, ""


PILL_MIN_S = 60   # 1.72: la pillola di una sessione si ricalcola al piu' una volta al minuto


def pill_from_passages(row, prev=None):
    """Decisione 3 dell'indice (16/10): la pillola di una cartella fuori da git, {files, added, removed} dei passaggi di
    modifica della conversazione corrente (le righe non servono: tetto 0), o None se non ce ne sono. → (valore, ricordo).
    Revisione finale: la trascrizione cresce solo in coda, quindi si riprende da `prev` = {path, end, paths, added,
    removed} (end = il byte dopo l'ultima riga intera contata) e si leggono solo i byte nuovi; si conta a fine turno,
    quando i risultati degli strumenti sono gia' scritti."""
    path = core.transcript_of(row)
    if not path:
        return None, None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, None
    p = prev if prev and prev.get("path") == path and 0 < int(prev.get("end") or 0) <= size else {"path": path, "end": 0, "paths": [], "added": 0, "removed": 0}
    start = int(p["end"])
    with open(path, "rb") as f:
        f.seek(start)
        cut = f.read(size - start).rfind(b"\n")
    end = start + cut + 1 if cut >= 0 else start
    paths, added, removed = set(p["paths"]), int(p["added"]), int(p["removed"])
    if end > start:
        # da start-1 (il «\n» che chiude l'ultima riga contata): transcript_entries salta il primo pezzo, qui vuoto
        for e in core.transcript_entries(path, max(0, start - 1), limit=end, diff_cap=(0, 0)):
            if e.get("diff"):
                paths.add(e["diff"]["path"])
                added += e["diff"]["added"]
                removed += e["diff"]["removed"]
    memo = {"path": path, "end": end, "paths": sorted(paths), "added": added, "removed": removed}
    return ({"files": len(paths), "added": added, "removed": removed} if paths else None), memo


def changes_pills(rows, ledger, now):
    """1.72 (15/10): sessions[].changes, la pillola «3 file · +40 −12» della testata: {files, added, removed, at} o None
    (nessuna modifica, git che non ha risposto). Solo i conteggi (cm-diff.counts: --numstat piu' i file nuovi; fuori da
    git, decisione 3 dell'indice del 16/10, quelli dei passaggi di Claude nella conversazione corrente, come la vista
    «transcript» che la pillola apre), per le sessioni vive di questa macchina: a fine turno (uno stop nel ledger dopo l'ultimo calcolo) e al piu'
    una volta ogni PILL_MIN_S per sessione; una fine turno arrivata prima del minuto si vede alla raccolta completa dopo.
    Una sessione chiusa tiene l'ultimo valore; una su un altro host e' None. Il ricordo e' in <relay.dir>/changes-pill.json,
    {tmux: {cwd, stop, checked, value}}; due sessioni nella stessa cartella fanno un calcolo solo."""
    path = rdir() / "changes-pill.json"
    cache = read_json(path, {})
    fresh, out, done = {}, {}, {}
    last_stop = {}   # 16/10: l'ultimo stop di ogni sessione in un giro solo (era un giro del ledger per riga)
    for x in ledger:
        if x.get("event") == "stop":
            sid = x.get("session_id")
            last_stop[sid] = max(last_stop.get(sid, 0), S.epoch(x.get("ts")))
    for r in rows:
        tm = r.get("tmux") or r.get("name") or ""
        cwd = str(r.get("cwd") or "")
        if not tm or not cwd or (r.get("host") and r.get("host") != "local"):
            continue
        c = cache.get(tm) or {}
        if r.get("status") != "dead":
            stop = last_stop.get(r.get("session_id"), 0)
            due = c.get("cwd") != cwd or stop > int(c.get("stop") or 0)
            if due and now - float(c.get("checked") or 0) >= PILL_MIN_S:
                tx = None
                try:
                    git_ = DF.repo_of(cwd) is not None
                    key = cwd if git_ else (cwd, tm)   # fuori da git ogni sessione ha la sua conversazione
                    if key not in done:
                        done[key], tx = (DF.counts(cwd), None) if git_ else pill_from_passages(r, c.get("tx"))
                except Exception:   # noqa: BLE001 — git che non risponde o un'uscita strana: la pillola e' un extra
                    key, tx = cwd, None
                    done[key] = None
                c = {"cwd": cwd, "stop": stop, "checked": int(now), "value": dict(done[key], at=int(now)) if done[key] else None}
                if tx:
                    c["tx"] = tx   # fuori da git: fin dove la trascrizione e' gia' contata
        if c:
            fresh[tm] = c
            out[tm] = c.get("value")
    if fresh != cache:
        write_json(path, fresh)
    return out


# ------------------------------------------------------------------ 1.75: stato dei PC remoti e pubblicazioni di oggi
_HM = []


def _hosts_mod():
    if not _HM:
        _HM.append(_load("cm-hosts"))
    return _HM[0]


GIB = 1024 ** 3


def _host_error(snap, now, stale_s):
    """Il testo in chiaro di un host giu', o "" se la lettura e' buona e fresca. Il sondatore (cron) rilegge ogni
    poll_idle_s a riposo: una lettura piu' vecchia di due giri vuol dire sondatore fermo, e l'host non si sa."""
    if not snap or not snap.get("read_at"):
        return "no reading yet"
    if snap.get("error"):
        code, detail = str(snap.get("error")), str(snap.get("detail") or "")
        if code == "unreachable":
            return "ssh: timeout" if "timeout" in detail else S.one_line(f"ssh: {detail or 'unreachable'}")[:80]
        if code == "hostkey":
            return "ssh: host key changed"
        return S.one_line(f"{code}: {detail}" if detail else code)[:80]
    age = now - float(snap["read_at"])
    if age > stale_s:
        return f"no reading for {int(age // 60)} min"
    return ""


def hosts_status(rows, now):
    """1.75 (21/10, home a tre colonne della web): una voce per host di cm-hosts diverso da local, dallo snapshot che
    il sondatore (`hosts poll`, cron) scrive al piu' ogni minuto: nessun ssh qui. {name, up, at, error, cpu, ram_used,
    ram_total, disk_free, disk_total, sessions}. cpu = load_pct/100 (su Windows il carico istantaneo dei processori,
    su POSIX il loadavg di un minuto sui thread), fra 0 e 1; ram e disco in byte; il disco e' quello della radice
    delle sessioni (D: su win), null con un aiutante di prima della 1.75. Host giu': error in chiaro, numeri null."""
    hm = _hosts_mod()
    stale = 2 * int(hm.S.get("poll_idle_s") or 600)
    out = []
    for name, h in hm.hosts().items():
        if name == "local" or not hm.confirmed(h) or h.get("kind") == "cloud":
            continue
        snap = hm.read_json(hm.snap_path(name), {}) or {}
        st = snap.get("status") or {}
        err = _host_error(snap, now, stale)
        sess = sorted({S.short_name(r.get("name") or "", prefixes()) for r in rows
                       if r.get("host") == name and r.get("status") != "dead" and r.get("name")})
        e = {"name": name, "up": not err, "at": int(snap["read_at"]) if snap.get("read_at") else None, "error": err or None,
             "cpu": None, "ram_used": None, "ram_total": None, "disk_free": None, "disk_total": None, "sessions": sess}
        if not err:
            if isinstance(st.get("load_pct"), (int, float)):
                e["cpu"] = round(min(1.0, max(0.0, st["load_pct"] / 100)), 2)
            tot = st.get("ram_total_b") or (round(st["ram_gb"] * GIB) if isinstance(st.get("ram_gb"), (int, float)) else None)
            free = st.get("ram_free_b") if st.get("ram_free_b") is not None else (round(st["ram_free_gb"] * GIB) if isinstance(st.get("ram_free_gb"), (int, float)) else None)
            if tot:
                e["ram_total"] = int(tot)
                e["ram_used"] = int(max(0, tot - free)) if free is not None else None
            d = st.get("disk") if isinstance(st.get("disk"), dict) else {}
            if d.get("total_b"):
                e["disk_free"], e["disk_total"] = int(d.get("free_b") or 0), int(d["total_b"])
        out.append(e)
    return out


PUBLISHED_MAX = 30
PUBLISHED_EVERY_S = 60   # le pubblicazioni si ricontano al piu' una volta al minuto, fuori dal percorso della push
DEPLOY_CMD = re.compile(r"\bfirebase\b[^;&|\n]*\bdeploy\b")
DEPLOY_DONE = "Deploy complete!"


def _midnight(now):
    return dt.datetime.combine(dt.datetime.fromtimestamp(now).date(), dt.time()).timestamp()


def published_path():
    return rdir() / "published-today.json"


def published_cached(now):
    """1.75: published_today per /state, dalla copia su disco; se e' vecchia di un minuto (o di ieri) la ricalcola un
    processo staccato (`published-refresh`), uno alla volta. Una copia di ieri non vale: oggi, finche' il primo
    conteggio non c'e', la lista e' vuota."""
    c = read_json(published_path(), {}) or {}
    if c.get("day") != dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d"):
        items = []
    else:
        items = c.get("items") or []
    if now - float(c.get("at") or 0) >= PUBLISHED_EVERY_S:
        lock = rdir() / "published-refresh.lock"
        try:
            fresh = lock.exists() and now - lock.stat().st_mtime < 300
            if not fresh:
                lock.touch()
                subprocess.Popen([sys.executable, str(HERE / "cm-relay.py"), "published-refresh"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
    return [dict(x) for x in items][:PUBLISHED_MAX]


def _git(repo, *args):
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def _web_of(remote_url):
    """La pagina del repository per un remoto GitHub (ssh o https), altrimenti None: nessun link indovinato."""
    m = re.match(r"^(?:git@github\.com:|ssh://git@github\.com/|https://(?:[^@/]+@)?github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?$", str(remote_url or "").strip())
    return f"https://github.com/{m.group(1)}" if m else None


def git_published(repo, project, start, end):
    """I push (il reflog dei rami remoti: «update by push») e i tag della finestra (come i traguardi del rapporto della
    notte), un tag solo se il suo commit sta gia' su un ramo remoto. Un ramo spinto piu' volte conta una volta, l'ultima."""
    out = []
    webs = {}
    refs = _git(repo, "for-each-ref", "--format=%(refname)%09%(creatordate:unix)", "refs/remotes", "refs/tags") or ""
    for line in refs.splitlines():
        ref, _, t = line.partition("\t")
        if ref.startswith("refs/tags/"):
            tag = ref[len("refs/tags/"):]
            if t.isdigit() and start <= int(t) <= end and (_git(repo, "branch", "-r", "--contains", ref) or "").strip():
                remote = (_git(repo, "remote") or "origin").split()[:1] or ["origin"]
                web = webs.setdefault(remote[0], _web_of((_git(repo, "remote", "get-url", remote[0]) or "").strip()))
                out.append({"at": int(t), "kind": "release", "project": project, "text": re.sub(r"^v(?=\d)", "", tag),
                            "url": f"{web}/releases/tag/{tag}" if web else None})
            continue
        if ref.endswith("/HEAD") or not t.isdigit() or int(t) < start - 86400:
            continue
        last = 0
        for ln in (_git(repo, "reflog", "show", "--date=unix", "--format=%gd%x09%gs", ref) or "").splitlines():
            sel, _, subj = ln.partition("\t")
            m = re.search(r"\{(\d+)\}", sel)
            if m and start <= int(m.group(1)) <= end and "push" in subj:
                last = max(last, int(m.group(1)))
        if last:
            remote, _, branch = ref[len("refs/remotes/"):].partition("/")
            web = webs.setdefault(remote, _web_of((_git(repo, "remote", "get-url", remote) or "").strip()))
            out.append({"at": last, "kind": "push", "project": project, "text": f"{remote}/{branch}",
                        "url": f"{web}/tree/{branch}" if web else None})
    return out


def _deploy_text(cmd, result):
    m = re.search(r"--only[ =](\S+)", cmd)
    what = m.group(1).strip("'\"").replace(":", " ").replace(",", ", ") if m else "firebase"
    p = re.search(r"console\.firebase\.google\.com/project/([\w-]+)", result)
    if p and p.group(1) not in what:
        what = f"{what} {p.group(1)}"
    return what[:60]


def transcript_deploys(path, start, scan):
    """I `firebase deploy` lanciati da una sessione con Bash e finiti con «Deploy complete!» nel risultato: solo
    quelli, niente da indovinare (un deploy fallito, uno dentro uno script o un `npm run` non contano). `scan` e' il
    ricordo di questa trascrizione ({offset, pending, items}): si legge solo la parte nuova, fino all'ultimo a capo."""
    size = os.path.getsize(path)
    if size < int(scan.get("offset") or 0):
        scan.clear()
    off = int(scan.get("offset") or 0)
    pending, items = scan.setdefault("pending", {}), scan.setdefault("items", [])
    with open(path, "rb") as f:
        f.seek(off)
        data = f.read()
    end = data.rfind(b"\n")
    if end < 0:
        return items
    for raw in data[:end].split(b"\n"):
        if not (b"deploy" in raw or (pending and b"tool_result" in raw)):
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        at = int(core._turn_epoch(d.get("timestamp")) or 0)
        content = (d.get("message") or {}).get("content")
        if at < start or d.get("isSidechain") or not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if d.get("type") == "assistant" and b.get("type") == "tool_use" and b.get("name") == "Bash":
                cmd = str((b.get("input") or {}).get("command") or "")
                if DEPLOY_CMD.search(_load_tl().bare(cmd)):
                    pending[str(b.get("id"))] = [cmd, d.get("cwd") or ""]
            elif d.get("type") == "user" and b.get("type") == "tool_result" and str(b.get("tool_use_id")) in pending:
                cmd, cwd = pending.pop(str(b["tool_use_id"]))
                res = _load_tl()._result_text(b)
                if not b.get("is_error") and DEPLOY_DONE in res:
                    u = re.search(r"Hosting URL:\s*(https?://\S+)", res)
                    items.append({"at": at, "kind": "deploy", "cwd": cwd, "text": _deploy_text(cmd, res), "url": u.group(1) if u else None})
    scan["offset"] = off + end + 1
    return items


_TL = []


def _load_tl():
    if not _TL:
        _TL.append(_load("cm-timeline"))
    return _TL[0]


def published_today(now=None):
    """1.75: [{at, kind: release|push|deploy, project, text, url}] dalla mezzanotte locale, la piu' recente per prima, al
    piu' PUBLISHED_MAX. Fonti, tutte con l'ora: i push e i tag di git nei repository delle sessioni vive di questa
    macchina e delle cartelle usate oggi; i deploy Firebase visti nelle trascrizioni toccate oggi (transcript_deploys).
    Le sessioni su un altro host restano fuori: i loro repository stanno la'. Il ricordo delle trascrizioni gia' lette
    e' in <relay.dir>/published-scan.json, ricominciato ogni giorno."""
    now = now or time.time()
    start = _midnight(now)
    day = dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    root = cm.expand(CFG["workspace"]["root"])
    live = [r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if not r.get("host") or r.get("host") == "local"]
    dirs = [str(r.get("cwd") or "") for r in live] + [p["path"] for p in inventory() if (p.get("last_used") or 0) >= start]
    repos = {}
    for d in dirs:
        if not d or d in repos.values() or not os.path.isdir(d):
            continue
        common = (_git(d, "rev-parse", "--path-format=absolute", "--git-common-dir") or "").strip()
        if common:
            top = str(Path(common).parent) if Path(common).name == ".git" else (_git(d, "rev-parse", "--show-toplevel") or d).strip()
            repos.setdefault(common, top)
    items = []
    for top in repos.values():
        items += git_published(top, S.project_of(top, root), start, now)
    scan_p = rdir() / "published-scan.json"
    scan = read_json(scan_p, {}) or {}
    if scan.get("day") != day:
        scan = {"day": day, "files": {}}
    files = scan.setdefault("files", {})
    for _, _, cwd, path in _load_tl().sessions_in(start, live=live):
        try:
            for x in transcript_deploys(path, start, files.setdefault(path, {})):
                items.append({"at": x["at"], "kind": "deploy", "project": S.project_of(x.get("cwd") or cwd, root), "text": x["text"], "url": x["url"]})
        except OSError:
            continue
    write_json(scan_p, scan)
    best = {}
    for x in items:
        k = (x["kind"], x["project"], x["text"])
        if x["at"] >= start and (k not in best or x["at"] > best[k]["at"]):
            best[k] = x
    out = sorted(best.values(), key=lambda x: (-x["at"], x["kind"], x["project"], x["text"]))[:PUBLISHED_MAX]
    return [{"at": x["at"], "kind": x["kind"], "project": x["project"], "text": x["text"], "url": x["url"]} for x in out]


def published_refresh():
    """Il processo staccato di published_cached: ricalcola e scrive la copia, poi toglie il lucchetto."""
    now = time.time()
    try:
        items = published_today(now)
        write_json(published_path(), {"day": dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d"), "at": now, "items": items})
        return 0
    except Exception as e:   # noqa: BLE001 — un extra della home: mai un errore rumoroso
        log(f"published-refresh: {e}")
        old = read_json(published_path(), {}) or {}   # l'ora del tentativo: niente ricalcolo a ogni push dopo un errore
        write_json(published_path(), dict(old, at=now))
        return 1
    finally:
        (rdir() / "published-refresh.lock").unlink(missing_ok=True)


def collect_sources(now=None):
    now = now or time.time()
    # senza nome ne' tmux e' un processo claude fuori registro (un `claude -p`, il recap): non e' una sessione da polso
    live = [r for r in (_json_cmd("sessions", "--json") or []) if (r.get("tmux") or r.get("name"))]
    good = _json_cmd("registry", "--good", expect="{") or {}
    alive_names = {r.get("tmux") for r in live}
    # 30/09: una sessione chiusa con lo stesso nome corto di una viva (stessa cartella, l'altro account: «x» e
    # «pix-x») non entra — sul polso sarebbero due «x», e il nome del contratto portava alla tmux sbagliata
    alive_short = {S.short_name(r.get("tmux") or r.get("name") or "", prefixes()) for r in live}
    rows = list(live)
    for s in good.get("sessioni", []):
        if s.get("nome") and s["nome"] not in alive_names and os.path.isdir(s.get("cartella") or "") \
                and S.short_name(s["nome"], prefixes()) not in alive_short:
            rows.append({"tmux": s["nome"], "name": s["nome"], "status": "dead", "cwd": s.get("cartella", ""), "account": s.get("account", ""),
                         "waiting": False, "session_id": s.get("session_id") or "", "link": "", "attached": False,
                         "visto_ts": S.epoch(s.get("visto", "")) if s.get("visto") else 0})
    # 1.84 (24/10): una sessione chiusa dall'app esce dalla fotografia del registro (`close` → registry --closed), ma
    # resta nello stato come gone, con il suo salvataggio, per «Riprendi»
    listed = {r.get("tmux") for r in rows}
    for tm_, rec in closed_saved(now).items():
        if tm_ not in listed and os.path.isdir(rec.get("cwd") or "") and S.short_name(tm_, prefixes()) not in alive_short:
            rows.append({"tmux": tm_, "name": tm_, "status": "dead", "cwd": rec.get("cwd", ""), "account": rec.get("account", ""),
                         "waiting": False, "session_id": rec.get("session_id") or "", "link": "", "attached": False,
                         "visto_ts": int(rec.get("closed_at") or 0)})
    # 16/10: mai due sessioni con lo stesso nome nello stato (l'app le indicizza per nome), prima delle chiavi qui sotto
    S.unique_names(rows, prefixes())
    ledger = core.ledger_rows()
    # 10/10: le sessioni su un altro host non hanno l'hook di qui: le loro righe dalla copia del transcript
    for r in rows:
        if r.get("host") and r.get("host") != "local":
            ledger += core.remote_events(r)
    aw = awaiting(ledger, rows)
    # 1.1: l'icona della scheda per ogni sessione viva (cm-color: registro stabile), per le sparite l'ultima nota
    _last = read_json(rdir() / "last-state.json", {})
    last_icons = {s_.get("name"): s_.get("icon") for s_ in (_last.get("full") or _last.get("state") or {}).get("sessions", []) if s_.get("icon")}
    # il primo avvistamento di una domanda senza evento waiting nel ledger: l'asked_at della push precedente
    last_asked = {n: (s_.get("question") or {}).get("asked_at") for n, s_ in last_sessions().items() if s_.get("question")}
    icons = {}
    questions, nexts, nexts_at, tools, notes = {}, {}, {}, {}, {}
    for r in rows:
        tm = r.get("tmux") or r.get("name") or ""
        if r.get("status") == "dead":
            ic = last_icons.get(S.short_name(r.get("name") or tm, prefixes()))
        else:
            ic = core.icon_of(tm)
        if ic:
            icons[tm] = ic
        # il flag dell'hook vale anche se `sessions --json` non l'ha ancora visto (stessa regola di cm-sessions)
        if r.get("status") != "dead" and not r.get("waiting") and waiting_info(r.get("session_id") or ""):
            r["waiting"] = True
        # 10/10: per una sessione su un altro host il recap sta nella cartella di qui da cui e' partita
        nx, nx_at = next_dated(r.get("local_dir") or r.get("cwd")) if (r.get("local_dir") or r.get("cwd")) else ("", None)
        if nx:
            nexts[tm] = nx
            if nx_at:
                nexts_at[tm] = nx_at
        if S.state_of(r) == "waiting" and r.get("host") and r.get("host") != "local":
            # 09/10: una sessione su un altro host non ha ne' schermo tmux ne' hook qui: la domanda dal suo transcript
            # (la copia che il sondatore porta qui quando la sessione si ferma)
            wi, inp = {}, {}
            pa = core.pending_ask(core.transcript_of(r))
            tool = "AskUserQuestion" if pa else ""
            text, opts = pa or ("", [])
        elif S.state_of(r) == "waiting":
            wi = waiting_info(r.get("session_id") or "")
            tool = str(wi.get("tool") or "")
            inp = wi.get("input") if isinstance(wi.get("input"), dict) else {}
            text, opts = question_of(tm, tool)
            # il dialogo si disegna un attimo dopo l'hook: si riprova, ma solo quando il payload dell'hook non
            # ha gia' la domanda (altrimenti si ripiega subito su quello e la push non aspetta nessuno)
            for _ in range(3):
                if opts or not tm or inp.get("options") or inp.get("question") or inp.get("command"):
                    break
                time.sleep(1)
                text, opts = question_of(tm, tool)
        if S.state_of(r) == "waiting":
            if not opts and inp.get("options"):
                # schermo non ancora disegnato (o sessione senza tmux): domanda e opzioni dal payload dell'hook
                text, opts = str(inp.get("question") or text or ""), [str(o) for o in inp["options"]]
            elif not text and inp.get("question"):
                text = str(inp["question"])
            if not text and not opts and not tool:
                # 14/09 (dall'app): attesa vista solo sullo schermo (waits_on_screen), senza file dell'hook e senza
                # nulla da estrarre → non e' una domanda: il polso mostrava «?» a «0 m». La sessione torna com'era
                # secondo il ledger (turno aperto = busy, altrimenti idle); mai «?» come testo.
                r["waiting"] = False
                r["status"] = turn_status(ledger, r.get("session_id") or "")
            else:
                asked = [S.epoch(x.get("ts")) for x in ledger if x.get("session_id") == r.get("session_id") and x.get("event") == "waiting"]
                first_seen = last_asked.get(S.short_name(r.get("name") or tm, prefixes()))
                detail = wi.get("input") if isinstance(wi.get("input"), (str, dict)) else ""
                questions[tm] = {"tool": tool, "text": text or f"{tool} {json.dumps(detail, ensure_ascii=False)[:200]}",
                                 "options": opts, "asked_at": asked[-1] if asked else int(first_seen or now),
                                 "detail": json.dumps(detail, ensure_ascii=False) if detail else "",
                                 # 1.53: etichetta → spiegazione, dal payload dell'hook (AskUserQuestion)
                                 "descriptions": {str(lb): str(ds) for lb, ds in zip(inp.get("options") or [], inp.get("descriptions") or []) if ds}}
        if S.state_of(r) != "waiting" and (r.get("status") == "busy" or tm in aw):
            # anche le «awaiting» (prompt dal polso in corso): senza questo la card restava senza attivita'
            t, note = tool_of(r)
            if t:
                tools[tm] = t
            if note:
                notes[tm] = note
    # 1.11 (16/09): modello, effort e contesto di ogni sessione viva, letti dalla sua trascrizione
    runtime = {}
    for r in rows:
        if r.get("status") == "dead" or not r.get("session_id"):
            continue
        try:
            rt = core.session_runtime(r)
            # 1.14 (22/09): il ripiego dopo un messaggio segnalato, gia' letto da `sessions --json`
            rt["fallback"] = r.get("fallback") or None
            rt["permission_mode"] = core.permission_mode(r)   # 1.54
            rt["permission_bypass"] = core.permission_bypass(r, rt["permission_mode"])   # 1.69
            runtime[r.get("tmux") or r.get("name") or ""] = rt
        except Exception:   # noqa: BLE001 — un extra: mai bloccare la push
            pass
    inv = inventory()
    return {
        "host": str(R.get("host") or socket.gethostname()),
        "root": cm.expand(CFG["workspace"]["root"]), "prefixes": prefixes(), "high_words": R.get("tier_high") or None,
        "account_kinds": S.kinds_of(CFG["accounts"], CFG.get("default_account") or ""),
        "state_max_kb": R.get("state_max_kb") or 8,
        "rows": rows, "ledger": ledger, "questions": questions,
        "quota": _json_cmd("quota", "--json", expect="{") or {},
        "projects": inv, "night": night_queue(), "night_report": night_report_latest(), "recap": recap_today(now), "ops": list(OPS), "slash": slash_allowed(),
        "follow": followed(), "awaiting": aw, "next": nexts, "next_at": nexts_at, "tools": tools,
        "devices": read_json(devices_path(), {}), "seen": seen_cached(now), "activity": read_json(device_activity_path(), {}), "recurring": _load("cm-recurring").for_state(),
        "icons": icons, "colors": R.get("colors") or None, "tool_notes": notes, "runtime": runtime,
        "advice": advice_of(rows, now), "duplicates": duplicates_of(rows), "approvals": approvals_list(),
        "attention": attention_open(rows, ledger), "needs_you": needs_you_of(rows),
        "attention_dismissed": read_json(rdir() / "attention-dismissed.json", {}),   # 1.80: i turn_blocking chiusi dall'app
        "handoffs": handoffs_of(rows), "resolved": resolved_of(questions, rows, now), "project_parents": sorted({str(Path(x["path"]).parent) for x in inv}),
        "changes": changes_pills(rows, ledger, now),   # 1.72
        "hosts_status": hosts_status(rows, now), "published_today": published_cached(now),   # 1.75
        "commitments": commitments_list(),   # 1.76
        "elicitations": elicitations_list(rows, now),   # 1.83
        "suggestions": suggestions_list(now), "usage_today": usage_today(rows, now), "closed": closed_saved(now),   # 1.84
        # 1.12: le scelte valide per il polso, dalla config (tune.models / tune.efforts)
        "choices": {"models": [{"id": m["id"], "label": m.get("label") or m["id"]} for m in ((CFG.get("tune") or {}).get("models") or []) if m.get("id")],
                    "efforts": list((CFG.get("tune") or {}).get("efforts") or [])},
    }


# ------------------------------------------------------------------ 1.51: il comando dell'handoff per sessione
HANDOFF_FD = "/fable-director:handoff --here"
HANDOFF_SV = "/supervisor:handoff --here"


def handoffs_of(rows):
    """{tmux: comando o None}. Dalle impostazioni dell'account della sessione (enabledPlugins): fable-director
    abilitato → il suo handoff; altrimenti supervisor abilitato → il nostro; altrimenti None. Una sessione su un altro
    host: None, le sue impostazioni non si leggono da qui. Solo letture di file, una per account."""
    plugins = {}

    def enabled(account):
        if account not in plugins:
            accs = CFG.get("accounts") or {}
            acc = accs.get(account or "") or accs.get(CFG.get("default_account") or "") or {}
            p = Path(cm.expand(acc.get("config_dir") or "~/.claude")) / "settings.json"
            try:
                ep = json.loads(p.read_text()).get("enabledPlugins") or {}
            except (OSError, ValueError):
                ep = {}
            plugins[account] = {k.split("@", 1)[0] for k, v in ep.items() if v is True}
        return plugins[account]
    out = {}
    for r in rows:
        tm = r.get("tmux") or r.get("name") or ""
        if r.get("host") not in (None, "", "local"):
            out[tm] = None
            continue
        on = enabled(r.get("account") or "")
        out[tm] = HANDOFF_FD if "fable-director" in on else HANDOFF_SV if "supervisor" in on else None
    return out


# ------------------------------------------------------------------ 1.37: consiglio, doppioni, approvazioni
ADVICE_REASON_MAX = 200
DEPLOY_RE = re.compile(r"\b(prod|production|produzione|deploy|live)\b", re.I)


def advice_of(rows, now):
    """{tmux: advice} dalla statusline di fable-director (formato concordato il 05/10): il suo file per sessione
    <relay.advice_dir>/<session_id>.json, chiave "advice" = {model, effort, reason, switch_cost_tokens, at, source,
    when («now» | «next_task»), differs}. Vale se e' piu' recente di relay.advice_max_age_s e se modello ed effort
    sono fra le scelte del polso (tune.models, tune.efforts): un consiglio che l'op model non saprebbe applicare non
    si mostra. Senza file o senza chiave: null, mai un consiglio inventato."""
    d = Path(cm.expand(R.get("advice_dir") or "~/.claude/fable-director/sessions"))
    max_age = float(R.get("advice_max_age_s") or 21600)
    tune = CFG.get("tune") or {}
    # 07/10 (dall'app): fable-director scrive l'id base («claude-opus-5-5»), le scelte possono avere la finestra
    # («claude-opus-5-5[1m]»): si confronta l'id senza il suffisso «[…]», come Tune.sameModel nell'app
    base = lambda mid: str(mid or "").split("[")[0]  # noqa: E731
    models = {base(m.get("id")) for m in (tune.get("models") or [])}
    efforts = set(tune.get("efforts") or [])
    out = {}
    for r in rows:
        sid = str(r.get("session_id") or "")
        if r.get("status") == "dead" or not re.fullmatch(r"[A-Za-z0-9-]{1,80}", sid):
            continue
        snap = read_json(d / f"{sid}.json", None)
        a = snap.get("advice") if isinstance(snap, dict) else None
        if not isinstance(a, dict):
            continue
        try:
            at, cost = int(a.get("at") or 0), int(a.get("switch_cost_tokens") or 0)
        except (TypeError, ValueError):
            continue
        if now - at > max_age or base(a.get("model")) not in models or a.get("effort") not in efforts:
            continue
        out[r.get("tmux") or r.get("name") or ""] = {"model": a["model"], "effort": a["effort"],
                                                     "reason": S.one_line(str(a.get("reason") or ""))[:ADVICE_REASON_MAX],
                                                     "switch_cost_tokens": max(cost, 0), "at": at, "source": "fable-director",
                                                     "when": a.get("when") if a.get("when") in ("now", "next_task") else "next_task",
                                                     "differs": bool(a.get("differs"))}
    return out


def duplicates_of(rows):
    """{tmux: nome corto dell'originale} per le sessioni vive con la stessa cartella e la stessa conversazione di
    un'altra aperta prima (il «-2» del 05/10)."""
    first, out = {}, {}
    live = [r for r in rows if r.get("status") != "dead" and r.get("session_id") and r.get("cwd")]
    for r in sorted(live, key=lambda r: (r.get("started_at") or 0, r.get("tmux") or "")):
        k = (os.path.realpath(r["cwd"]), r["session_id"])
        if k in first:
            out[r.get("tmux") or r.get("name") or ""] = S.short_name(first[k], prefixes())
        else:
            first[k] = r.get("tmux") or r.get("name") or ""
    return out


def approvals_list():
    """I compiti del registro in attesa di ok, con la richiesta di wait-ok: cosa esce e dove. deploy = la
    destinazione o la cosa nomina la produzione. 1.81 (23/10): `options`, le etichette di una scelta (wait-ok
    --options), solo quando ci sono: l'app mostra un tasto per opzione al posto di «Approva»."""
    out = []
    for t in _json_cmd("task", "list", "--state", "awaiting_ok", "--json") or []:
        req = t.get("request") or {}
        what, where = str(req.get("what") or ""), str(req.get("where") or "")
        a = {"task": t.get("id"), "title": S.one_line(str(t.get("title") or ""))[:200], "what": what[:300], "where": where[:300],
             "deploy": bool(DEPLOY_RE.search(what + " " + where)), "requested_at": int(req.get("at") or 0) or None}
        opts = req.get("options")
        if isinstance(opts, list) and opts:
            a["options"] = [S.one_line(str(o))[:24] for o in opts[:4]]
        out.append(a)
    return sorted(out, key=lambda a: (a["requested_at"] or 0, a["task"] or ""))


def elicitations_list(rows, now):
    """1.83 (24/10): le domande aperte dei server MCP (cm-elicit.py, scritte dall'hook Elicitation), con il nome della
    sessione dello stato: dal session_id del registro, altrimenti il nome tmux scritto dall'hook."""
    el = _load("cm-elicit")
    name = lambda r: S.short_name(r.get("name") or r.get("tmux") or "", prefixes())  # noqa: E731 — come build_session
    by_sid = {r.get("session_id"): name(r) for r in rows or [] if r.get("session_id")}
    by_tm = {r.get("tmux"): name(r) for r in rows or [] if r.get("tmux")}
    return [el.for_state(r, by_sid.get(r.get("session_id")) or by_tm.get(r.get("session")) or r.get("session") or None)
            for r in el.pending(cm.expand(CFG["state_dir"]), int(now))]


def elicit_answer(cmd, arg):
    """1.83 (24/10): la risposta del telefono a una domanda di un server MCP — `arg` = l'id, `action` accept|decline|
    cancel, `content` i valori per chiave. Solo dai dispositivi accoppiati e dalla web app locale, come ogni op: il
    file di risposta porta origine «relay» e l'hook della sessione la passa al server."""
    ok, key, kw = _load("cm-elicit").answer(cm.expand(CFG["state_dir"]), arg, cmd.get("action"), cmd.get("content"),
                                            cmd.get("by"), cmd.get("device"), int(time.time()))
    return ok, M("relay." + key, **kw)


# ------------------------------------------------------------------ 1.84 (24/10): suggerimenti, quota di oggi, chiusure
def suggestions_list(now):
    """1.84: i suggerimenti della master (cm-suggest.py) per /state.suggestions; [] se non ce ne sono."""
    try:
        return _load("cm-suggest").for_state(int(now))
    except Exception:   # noqa: BLE001 — un extra: mai bloccare la push
        return []


def suggestion_act(cmd, arg):
    """1.84 (24/10, scheda «Suggerimenti della master»): `arg` = l'id, `action` accept | later | never. accept manda il
    prompt della voce alla master come un prompt dall'app, con l'etichetta «dai suggerimenti», e toglie la voce; later
    la nasconde fino al giro dopo della master; never non la ripropone piu'. 1.88 (27/10): choose con `choice`, una
    delle `options` della voce, manda il prompt con la scelta ({choice} nel prompt, o «Scelta: …» in fondo) e toglie la
    voce; discuss manda «Parliamone, senza farlo: <testo>» e la voce resta."""
    action, choice = str(cmd.get("action") or "").strip(), cmd.get("choice")
    code, hit = _load("cm-suggest").act(str(arg or "").strip(), action, choice=None if choice is None else str(choice))
    if code == "bad_action":
        return False, M("relay.cmd_suggestion_bad_action", action=action or "?")
    if code == "gone":   # 1.87 (27/10): tolto con suggest clear o scaduto mentre la scheda era sul telefono
        return True, M("relay.cmd_suggestion_gone")
    if code == "bad_choice":
        return False, M("relay.cmd_suggestion_bad_choice", choice=str(choice or ""))
    row = {"id": hit["id"], "key": hit.get("key"), "kind": hit.get("kind"), "source": hit.get("source") or "master", "action": code, "by": cmd.get("by")}
    if code == "chosen":
        row["choice"] = choice
    ledger_write("suggestion", **row)
    if code == "hidden":
        return True, M("relay.cmd_suggestion_later")
    if code == "never":
        return True, M("relay.cmd_suggestion_never")
    prompt = str(hit.get("prompt") or "")
    if code == "chosen":
        prompt = prompt.replace("{choice}", str(choice)) if "{choice}" in prompt else prompt + " " + M("relay.suggestion_choice", choice=choice)
    elif code == "discussed":
        prompt = M("relay.suggestion_discuss") + " " + str(hit.get("text") or "")
    master = str((CFG.get("workspace") or {}).get("root_session_name") or "master")
    rc, out = relay_talk(master, prefix_for(cmd, master) + " " + M("relay.suggestion_label") + " " + prompt, "--no-wait")
    if rc != 0:
        return False, (out.splitlines() or ["talk failed"])[0]
    if saved_in_inbox(out):
        return True, M("relay.cmd_suggestion_inbox")
    aw = read_json(rdir() / "awaiting.json", {}); aw[master] = int(time.time()); write_json(rdir() / "awaiting.json", aw)
    return True, M({"chosen": "relay.cmd_suggestion_chosen", "discussed": "relay.cmd_suggestion_discussed"}.get(code, "relay.cmd_suggestion_sent"))


USAGE_CACHE_S = 180


def _midnight(now):
    t = time.localtime(now)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def usage_today(rows, now):
    """1.84 (24/10, B5): [{session, account, tokens, share}] — i token di oggi (dalla mezzanotte locale) di ogni
    sessione, letti dalle trascrizioni di ogni account (anche le sessioni chiuse oggi, la master e i loro subagenti), e
    la loro quota sul totale dell'account (0..1). Conta input, output e cache come li scrive l'API (usage di ogni
    messaggio, un messaggio una volta sola). Incrementale: rdir()/usage-today.json tiene per ogni file dove si era
    arrivati, e il conto si rifà al piu' ogni USAGE_CACHE_S; dal giorno dopo si riparte. Non e' esatto al token: le
    sessioni su un altro host non ci sono."""
    now = int(now)
    day0 = _midnight(now)
    cpath = rdir() / "usage-today.json"
    cache = read_json(cpath, {}) or {}
    if cache.get("day") != day0:
        cache = {"day": day0, "files": {}}
    if now - int(cache.get("at") or 0) >= USAGE_CACHE_S or not cache.get("out"):
        since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(day0))
        files = cache["files"]
        for acc, a in (CFG.get("accounts") or {}).items():
            base = Path(cm.expand(a.get("config_dir") or "~/.claude")) / "projects"
            try:
                found = [p for p in base.glob("*/*.jsonl")] + [p for p in base.glob("*/*/subagents/*.jsonl")]
            except OSError:
                continue
            for p in found:
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_mtime < day0:
                    continue
                k = str(p)
                f = files.get(k) or {"acc": acc, "off": 0, "tokens": 0, "last": "", "cwd": "",
                                     "sid": p.parent.parent.name if p.parent.name == "subagents" else p.stem}
                if st.st_size < f["off"]:
                    f.update(off=0, tokens=0, last="")
                if st.st_size > f["off"]:
                    try:
                        with open(p, "rb") as fh:
                            fh.seek(f["off"])
                            chunk = fh.read()
                    except OSError:
                        continue
                    end = chunk.rfind(b"\n") + 1
                    for line in chunk[:end].splitlines():
                        if not f["cwd"] and b'"cwd"' in line:
                            try:
                                f["cwd"] = str(json.loads(line).get("cwd") or "")
                            except ValueError:
                                pass
                        if b'"usage"' not in line:
                            continue
                        try:
                            d = json.loads(line)
                        except ValueError:
                            continue
                        if str(d.get("timestamp") or "") < since:
                            continue
                        msg = d.get("message") or {}
                        u = msg.get("usage") if isinstance(msg, dict) else None
                        if not isinstance(u, dict):
                            continue
                        mid = str(msg.get("id") or d.get("requestId") or "")
                        if mid and mid == f["last"]:
                            continue   # lo stesso messaggio scritto a pezzi: un blocco per riga, lo stesso usage
                        f["last"] = mid
                        f["tokens"] += sum(int(u.get(x) or 0) for x in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
                    f["off"] += end
                files[k] = f
        by_sid = {r.get("session_id"): S.short_name(r.get("name") or r.get("tmux") or "", prefixes()) for r in rows or [] if r.get("session_id")}
        tot, per = {}, {}
        for f in files.values():
            if not f.get("tokens"):
                continue
            name = by_sid.get(f["sid"]) or (os.path.basename(f["cwd"].rstrip("/")) if f.get("cwd") else "") or f["sid"][:8]
            per[(name, f["acc"])] = per.get((name, f["acc"]), 0) + f["tokens"]
            tot[f["acc"]] = tot.get(f["acc"], 0) + f["tokens"]
        cache["out"] = [{"session": n, "account": acc, "tokens": t, "share": round(t / tot[acc], 3) if tot.get(acc) else 0}
                        for (n, acc), t in sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))]
        cache["at"] = now
        write_json(cpath, cache)
    return list(cache.get("out") or [])


CLOSED_KEEP_S = 7 * 86400   # 1.84: per quanto resta il salvataggio di una sessione chiusa dall'app
BG_SHELLS = ("bash", "sh", "zsh", "dash", "fish")


def closed_dir():
    return Path(cm.expand(CFG["state_dir"])) / "closed"


def closed_file(tm):
    return closed_dir() / f"{re.sub(r'[^A-Za-z0-9_.:-]', '_', tm)}.json"


def closed_saved(now=None):
    """1.84: {tmux: salvataggio} delle sessioni chiuse dall'app (op close, /exit) negli ultimi CLOSED_KEEP_S; i piu'
    vecchi vanno via."""
    now = float(now or time.time())
    out = {}
    d = closed_dir()
    if not d.is_dir():
        return out
    for f in d.glob("*.json"):
        rec = read_json(f, None)
        if not isinstance(rec, dict) or now - float(rec.get("closed_at") or 0) > CLOSED_KEEP_S:
            f.unlink(missing_ok=True)
            continue
        if rec.get("tmux"):
            out[rec["tmux"]] = rec
    return out


def git_dirty(cwd):
    """(dirty_n, i primi DIRTY_MAX percorsi) da `git status --porcelain`; (None, None) fuori da git o se git non risponde."""
    if not cwd or not os.path.isdir(cwd):
        return None, None
    try:
        r = subprocess.run(["git", "-C", cwd, "status", "--porcelain"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if r.returncode != 0:
        return None, None
    paths = [ln[3:].strip() for ln in r.stdout.splitlines() if len(ln) > 3]
    return len(paths), paths[:S.DIRTY_MAX]


def _children(pid):
    """I processi figli di pid: [(pid, nome del comando)]; [] se /proc non risponde."""
    out = []
    try:
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                stat = Path(f"/proc/{d}/stat").read_text()
            except OSError:
                continue
            rest = stat[stat.rfind(")") + 2:].split()
            if len(rest) > 1 and rest[1] == str(pid):
                out.append((int(d), stat[stat.find("(") + 1:stat.rfind(")")]))
    except OSError:
        pass
    return out


def _cmdline(pid):
    """Gli argomenti del processo pid; [] se /proc non risponde."""
    try:
        return [a for a in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0") if a]
    except OSError:
        return []


def statusline_cmds(cwd=None):
    """28/10: i comandi della barra di stato (statusLine.command) nelle impostazioni degli account e del progetto."""
    files = [Path(cm.expand(a.get("config_dir") or "")) / "settings.json" for a in (CFG.get("accounts") or {}).values() if a.get("config_dir")]
    if cwd:
        files += [Path(cwd) / ".claude" / "settings.json", Path(cwd) / ".claude" / "settings.local.json"]
    out = set()
    for f in files:
        sl = (read_json(f, {}) or {}).get("statusLine") if f.is_file() else None
        if isinstance(sl, dict) and isinstance(sl.get("command"), str) and sl["command"].strip():
            out.add(sl["command"].strip())
    return out


def background_of(row):
    """1.84: la sessione ha un lavoro in sottofondo — un Bash in background o un processo figlio di una shell: fra i
    figli del processo claude una shell (i server MCP sono node o python, non shell). Solo per le sessioni di qui.
    28/10: la barra di stato non conta — Claude Code la lancia ogni pochi secondi come «/bin/sh -c <statusLine.command>»,
    e la chiusura falliva a caso."""
    pid = row.get("pid")
    if not pid or (row.get("host") and row.get("host") != "local"):
        return False
    shells = [k for k, name in _children(pid) if name in BG_SHELLS]
    if not shells:
        return False
    sls = statusline_cmds(row.get("cwd"))
    return any(" ".join(_cmdline(k)[2:]).strip() not in sls for k in shells)


def close_check(name, rows, allow_attached=False):
    """1.84: {name, closable, blocked_by, save} per una sessione (nome dello stato). blocked_by: busy, question,
    background, attached, gone (non e' viva) o null. save: {outcome, next, session_id, dirty_n, dirty}, solo per una
    chiudibile. allow_attached: /exit si digita nel riquadro e vale come la persona al terminale, anche se attaccata."""
    tm = tmux_of(name)
    row = next((r for r in rows if (r.get("tmux") or r.get("name")) == tm), None)
    if not row:
        return {"name": name, "closable": False, "blocked_by": "gone", "save": None}, None
    info = last_sessions().get(name) or {}
    if row.get("waiting") or row.get("status") == "waiting":
        why = "question"
    elif row.get("status") == "busy" or info.get("state") in ("busy", "awaiting"):
        why = "busy"
    elif background_of(row):
        why = "background"
    elif row.get("attached") and not allow_attached:
        why = "attached"
    else:
        why = None
    out = {"name": name, "closable": why is None, "blocked_by": why}
    if why is None:
        n, dirty = git_dirty(row.get("cwd")) if not row.get("host") or row.get("host") == "local" else (None, None)
        # next: il primo seguito dell'ultimo esito (next_steps), altrimenti il prossimo del recap — 24/10: quello del
        # recap poteva essere di giorni prima
        steps = [x.get("text") for x in info.get("next_steps") or [] if x.get("text")]
        out["save"] = {"outcome": ((info.get("outcome") or {}).get("short")) or None, "next": (steps[0] if steps else info.get("next")) or None,
                       "session_id": row.get("session_id") or None, "dirty_n": n, "dirty": dirty}
    else:
        out["save"] = None
    return out, row


def close_names(arg, session):
    names = [S.one_line(x).strip() for x in str(arg or session or "").split(",")]
    return [x for x in dict.fromkeys(names) if x][:20]


def close_preview(cmd, arg, session=""):
    """1.84 (24/10, B4): op close_preview, solo lettura — per ogni sessione di `arg` («nome1,nome2») cosa verra'
    salvato e cosa impedisce la chiusura. Il testo e' il JSON {sessions: [...]}."""
    names = close_names(arg, session)
    if not names:
        return False, M("relay.cmd_close_none")
    rows = _json_cmd("sessions", "--json") or []
    return True, json.dumps({"sessions": [close_check(n, rows)[0] for n in names]}, ensure_ascii=False)


def save_closed(chk, row, by):
    """Il salvataggio di una sessione prima di chiuderla: <state_dir>/closed/<tmux>.json."""
    tm = row.get("tmux") or row.get("name")
    rec = dict(chk["save"] or {}, name=chk["name"], tmux=tm, cwd=row.get("cwd") or "", account=row.get("account") or "",
               closed_at=int(time.time()), by=by or None)
    write_json(closed_file(tm), rec)
    return rec


def close_sessions(cmd, arg, session=""):
    """1.84 (24/10, B4 e ogni chiusura dall'app): op close — chiude solo le sessioni chiudibili, rifacendo i controlli
    adesso; prima di chiuderne una ne salva lo stato (esito, prossimo passo, file non committati, session_id, ora).
    Risponde con chi e' chiusa e chi no, col motivo. ok se almeno una e' chiusa."""
    names = close_names(arg, session)
    if not names:
        return False, M("relay.cmd_close_none")
    rows = _json_cmd("sessions", "--json") or []
    done, kept = [], []
    for n in names:
        chk, row = close_check(n, rows)
        if not chk["closable"]:
            kept.append(M("relay.cmd_close_kept", name=n, why=M(f"relay.close_why_{chk['blocked_by']}")))
            continue
        rec = save_closed(chk, row, cmd.get("by"))
        rc, out = run_cm("close", row.get("tmux") or row.get("name"))
        if rc != 0:
            closed_file(rec["tmux"]).unlink(missing_ok=True)
            kept.append(M("relay.cmd_close_kept", name=n, why=(out.strip().splitlines() or ["close failed"])[0].strip()))
            continue
        ledger_write("closed-from-app", session=rec["tmux"], session_id=rec.get("session_id") or "", by=cmd.get("by"), dirty_n=rec.get("dirty_n"))
        done.append(n)
    if done:
        push_async()
    lines = ([M("relay.cmd_close_done", names=", ".join(done))] if done else []) + kept
    return bool(done), "\n".join(lines)


def commitments_list():
    """1.76 (22/10, P2): gli impegni del registro (`task impegni --json`: approvati non compiuti, e chi aspetta un ok)
    per /state.commitments, con `project` relativo come sessions[].project; fermi prima, al massimo COMMITMENTS_MAX."""
    root = cm.expand(CFG["workspace"]["root"])
    out = []
    for x in _json_cmd("task", "impegni", "--json") or []:
        if not isinstance(x, dict) or not x.get("task"):
            continue
        y = dict(x)
        y["project"] = S.project_of(x.get("project") or "", root)
        y["title"] = S.one_line(str(x.get("title") or ""))[:200]
        y["short"] = short_title(x.get("short") or y["title"])   # 1.79: one line in the app, never «…»
        out.append(y)
    return out[:S.COMMITMENTS_MAX]


# ------------------------------------------------------------------ push
def _cpu():
    """(CPU propria, CPU dei figli attesi) in secondi, per la riga di misura della push."""
    a, b = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return a.ru_utime + a.ru_stime, b.ru_utime + b.ru_stime


def _s(x):
    return f"{x:.1f}".replace(".", ",")


def push(dry_run=False, now=None, origin="diretta", requested=None, stamp=None, light=False):
    """Una push. Il lock copre raccolta, eventi e PUT; FCM, avvisi e pulizie (m["post"]) girano dopo averlo lasciato
    (08/10, fase 1). `stamp`: la richiesta del ripiego senza demone; se dopo il lock push-request ha un timbro piu'
    nuovo, la push la fa chi l'ha scritto e questa esce senza raccogliere (None)."""
    if dry_run:
        return _push(True, now)
    # una push alla volta: due in parallelo leggono lo stesso «stato precedente» e scrivono due volte gli stessi
    # eventi (dal vivo 16:22 del 12/09: la domanda e' arrivata due volte sul bus)
    t0, cpu0 = time.time(), _cpu()
    m = {}
    lock = open(str(rdir() / "push.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        m["locked"] = time.time()
        if stamp is not None:
            try:
                if (rdir() / "push-request").read_text().strip() != stamp:
                    return None   # una richiesta piu' recente: la sua push copre anche questa
            except OSError:
                pass
        st = _light(m) if light else None
        if st is None:
            st = _push(False, now, m)
        else:
            origin = "battito"
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()
    if m.get("post"):
        m["post"]()
    # 07/10 (piano prestazioni, fase 0): una riga di misura per push, letta da `relay stats`
    end, cpu1 = time.time(), _cpu()
    wait = t0 - requested if requested else 0.0
    log(f"push: {len(st['sessions'])} sessioni, {m.get('events', 0)} eventi · origine {origin} · attesa {_s(wait)} s"
        f" · lock {_s(m['locked'] - t0)} s · raccolta {_s(m['collected'] - m['locked'])} s"
        f" · pubblicazione {_s(m['published'] - m['collected'])} s · contorno {_s(end - m['published'])} s"
        f" · ts→put {_s(m['put_at'] - m['ts'])} s · cpu {_s(cpu1[0] - cpu0[0])}+{_s(cpu1[1] - cpu0[1])} s")
    return st


BEAT_FULL_IDLE_S = 300   # 08/10 (battito leggero): una raccolta completa almeno ogni 5 minuti a sessioni ferme,
BEAT_FULL_BUSY_S = 120   # ogni 2 se una lavora (la nota dello strumento in corso e il contesto cambiano senza hook)


_TASKS_DB = None


def sources_print():
    """L'impronta delle sorgenti dello stato che cambiano senza un hook che chieda la push: date e dimensioni di
    attese, registro delle sessioni, coda della notte, compiti, rapporti della notte, file del relay e suggerimenti
    (27/10: senza, un suggest clear restava sull'app fino alla push completa, e «Non ora» dava «unknown suggestion»;
    con gli id vivi anche uno scaduto esce subito). Il ledger no:
    cambia di continuo (ogni lettura della chat ci scrive una riga, 08/10: nessun battito leggero dal vivo), e ogni
    sua riga che cambia lo stato (start, prompt, waiting, stop, end, un comando che non e' una lettura) arriva con
    un hook o un comando che chiede gia' la push completa."""
    sd = Path(cm.expand(CFG["state_dir"]))
    reg = CFG.get("registry") or {}
    paths = [sd / "waiting", Path(cm.expand(reg.get("file") or "~/.claude/sessions.json")),
             Path(cm.expand(reg.get("good_file") or "~/.claude/sessions-good.json")),
             Path(cm.expand((CFG.get("night") or {}).get("queue_file") or "~/.claude/night-queue.jsonl")), night_report_dir(),
             rdir() / "follow.json", rdir() / "awaiting.json", rdir() / "devices.json"]
    try:   # 1.87 (27/10): i suggerimenti — il file per add, clear e i tasti, gli id vivi per quelli che scadono
        sg = _load("cm-suggest")
        paths.append(sg.path())
        live_ids = ",".join(x["id"] for x in sg.for_state())
    except Exception:   # noqa: BLE001 — l'impronta non deve mai fermare il battito
        live_ids = "-"
    global _TASKS_DB
    if _TASKS_DB is None:   # un caricamento solo: _load riesegue il modulo a ogni chiamata
        _TASKS_DB = _load("cm-tasks").db_path()
    paths += [_TASKS_DB, _TASKS_DB.with_name(_TASKS_DB.name + "-wal")]
    out = []
    for p in paths:
        try:
            st = p.stat()
            out.append(f"{p.name}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            out.append(f"{p.name}:-")
    try:   # le attese: la cartella cambia data quando un file entra o esce, non quando cambia
        out += sorted(f"{e.name}:{e.stat().st_mtime_ns}" for e in os.scandir(sd / "waiting"))
    except OSError:
        pass
    out.append("suggestions:" + live_ids)
    return ";".join(out)


def beat_path():
    return rdir() / "beat.json"


def _light(m):
    """08/10 (chiesto dal maintainer: 681 push del cron su 689 senza eventi, ~6,5 s di CPU ciascuna). Il battito del cron
    ripubblica lo stato gia' pronto, con il solo published_at nuovo, se le sorgenti non sono cambiate dall'ultima raccolta
    e quella raccolta e' abbastanza recente; altrimenti None e si fa la push completa. Dentro il lock della push."""
    b = read_json(beat_path(), {})
    last = read_json(rdir() / "last-state.json", {})
    state, full = last.get("state"), last.get("full")
    if not isinstance(state, dict) or not isinstance(full, dict) or not b.get("fp"):
        return None
    busy = any(s_.get("state") in ("busy", "waiting") for s_ in state.get("sessions") or [])
    if time.time() - float(b.get("full_at") or 0) > (BEAT_FULL_BUSY_S if busy else BEAT_FULL_IDLE_S) or sources_print() != b["fp"]:
        return None
    m["ts"] = float(state.get("ts") or time.time())
    m["collected"] = time.time()
    m["put_at"] = time.time()
    state["published_at"] = full["published_at"] = round(m["put_at"], 3)
    try:
        local_state_write(full)
    except OSError as ex:
        log(f"stato locale non scritto: {ex}")
    if not local_only():
        rtdb("PUT", "state", C.encrypt(state, key()), {"print": "silent"})
    m["published"] = time.time()
    m["events"] = 0
    pub_history_add(m["put_at"])
    last.update(state=state, full=full)
    write_json(rdir() / "last-state.json", last)
    return state


def _push(dry_run=False, now=None, m=None):
    m = {} if m is None else m
    now = now or time.time()
    m["ts"] = now
    fp = None if dry_run else sources_print()   # prima della raccolta: un cambiamento durante la raccolta non si perde
    src = collect_sources(now)
    # gli eventi si calcolano sullo stato INTERO; il taglio per la dimensione tocca solo cio' che si pubblica (23/09:
    # una sessione viva tolta da fit_state diventava «gone» e il polso diceva «Session closed»)
    full = S.build_state(src, now, fit=False)
    state = S.fit_state(copy.deepcopy(full), int(src.get("state_max_kb") or 8))
    names = {}
    for r in src["rows"]:   # le vive vengono prima: a parita' di nome corto vince la sessione viva
        names.setdefault(S.short_name(r.get("name") or r.get("tmux") or "?", src["prefixes"]), r.get("tmux") or r.get("name"))
    if dry_run:
        print(json.dumps(state, ensure_ascii=False, indent=1))
        return state
    last_p = rdir() / "last-state.json"
    last = read_json(last_p, {})
    events, seq = S.events_between(last.get("full") or last.get("state") or {}, full, now, int(last.get("seq") or 0) + 1,
                                   warn_pct=float((CFG.get("guard") or {}).get("warn_pct") or 95))
    # 1.43 (08/10): published_at, l'istante della pubblicazione (ts resta quello del campionamento); le app ci
    # calcolano la freschezza e, con published_at - ts, la lentezza del PC
    m["put_at"] = time.time()
    state["published_at"] = full["published_at"] = round(m["put_at"], 3)
    try:
        local_state_write(full)   # 06/10: in locale lo stato intero, senza i tagli degli 8 KB (non esce dalla macchina)
    except OSError as ex:
        log(f"stato locale non scritto: {ex}")
    k = None if local_only() else key()
    m["collected"] = time.time()
    try:
        if k:
            rtdb("PUT", "state", C.encrypt(state, k), {"print": "silent"})
        if events and k:
            rtdb("PATCH", "events", {e["key"]: C.encrypt(e, k) for e in events}, {"print": "silent"})
        local_events_add(events, now)   # anche senza eventi nuovi: toglie i vecchi
    except (urllib.error.URLError, OSError, ValueError) as ex:
        # il bus non prende: il polso non sta ricevendo. Si segna da quando, e se dura si passa da Telegram
        down = fallback_mark(now)
        log(f"push FAILED ({int(down)} s): {ex}")
        fallback_notice(events, down)
        raise
    m["published"] = time.time()
    m["events"] = len(events)
    pub_history_add(m["put_at"])
    write_json(last_p, {"state": state, "full": full, "seq": seq - 1, "pushed_at": now, "names": names})
    write_json(beat_path(), {"fp": fp, "full_at": now})

    def post():
        # 08/10 (fase 1): fuori dal lock della push. Le pulizie una volta ogni PRUNE_EVERY_S, non a ogni push
        if prune_due(now):
            if not local_only():
                prune_events(now)
                prune_share(now)
                prune_share(now, "file")
            prune_local(now)
        try:
            note_tail_paths(src["rows"], full)   # 1.70: la API locale apre /api/tail/<id> da qui
        except Exception as ex:   # noqa: BLE001
            log(f"tail paths FAILED: {ex}")
        if local_only():
            return
        try:
            push_tails(src["rows"], full, now)
        except Exception as ex:   # noqa: BLE001 — la chat fresca e' un extra: mai far cadere la push
            log(f"tail FAILED: {ex}")
        try:
            attention_reminders(full, now)
        except Exception as ex:   # noqa: BLE001 — un promemoria in meno, mai una push in meno
            log(f"reminders FAILED: {ex}")
        woken = True
        for e in events:
            try:
                # 1.62: «quiet» anche nella sveglia, cosi' l'app sa senza leggere l'evento che non deve suonare
                fcm_send({"kind": e["kind"], "session": e["session"], "ts": e["ts"], "key": e["key"], **({"quiet": "1"} if e.get("quiet") else {})})
            except (urllib.error.URLError, OSError, ValueError) as ex:
                woken = False
                log(f"fcm FAILED: {ex}")
        if events and not woken:
            # lo stato e' sul bus ma la sveglia non parte: l'orologio se ne accorge solo quando lo si guarda
            fallback_notice(events, fallback_mark(now))
        else:
            fallback_clear()
            unseen_notice(events, now)
    m["post"] = post
    return state


PRUNE_EVERY_S = 600

TAIL_N = 30                 # 1.59: le ultime voci della chat di ogni sessione viva, in /tail/<id>
TAIL_MAX_BYTES = 32000      # il JSON in chiaro di un nodo; oltre si tolgono le voci piu' vecchie (more = true)
TAIL_MIN_S = 10             # al piu' una scrittura ogni 10 s per sessione mentre lavora: il traffico resta basso
TAIL_WINDOW = 2 * 1024 * 1024


def tail_key(sid):
    """La chiave RTDB di una sessione: il suo id di sessions[] con i caratteri che RTDB non accetta cambiati in «_»."""
    return re.sub(r"[.#$\[\]/]", "_", str(sid))


def tail_body(name, sid, path, st, now):
    """Il corpo di /tail/<id> (1.59) e di GET /api/tail/<id> (1.70): {session, id, tx, at, entries, more}, le ultime
    TAIL_N voci della chat entro TAIL_MAX_BYTES. tx = dimensione e mtime della trascrizione."""
    entries = core.transcript_entries(path, max(0, st.st_size - TAIL_WINDOW), diff_cap=(DF.ENTRY_LINES, DF.ENTRY_BYTES))
    page, more = entries[-TAIL_N:], len(entries) > TAIL_N or st.st_size > TAIL_WINDOW
    page = [dict(e, diff=DF.redact_diff(e["diff"])) if e.get("diff") else e for e in page]   # 1.72: solo le voci che escono
    body = {"session": name, "id": sid, "tx": f"{st.st_size}-{st.st_mtime_ns}", "at": int(now), "entries": page, "more": bool(more)}
    while body["entries"] and len(json.dumps(body, ensure_ascii=False).encode()) > TAIL_MAX_BYTES:
        body["entries"] = body["entries"][1:]
        body["more"] = True
    return body


def note_tail_paths(rows, full):
    """1.70 (15/10, da supervisor-app: la web locale rileggeva la chat con l'op transcript, circa 6 s di PC ognuna): a
    ogni push, per ogni sessione viva con una trascrizione, {id: {session, path}} in local/tail-paths.json. La API
    locale risponde a GET /api/tail/<id> leggendo da qui, senza raccogliere le sessioni. Si riscrive solo se cambia."""
    by_tm = {r.get("tmux") or r.get("name"): r for r in rows if r.get("status") != "dead"}
    paths = {}
    for s_ in full.get("sessions") or []:
        if s_.get("state") == "gone":
            continue
        row = next((r for tm, r in by_tm.items() if r.get("session_id") == s_.get("id") or tm == s_.get("id")), None)
        path = core.transcript_of(row) if row else ""
        if path:
            paths[str(s_["id"])] = {"session": s_["name"], "path": str(path)}
    f = rdir() / "local" / "tail-paths.json"
    if read_json(f, None) != paths:
        f.parent.mkdir(parents=True, exist_ok=True)
        write_json(f, paths)


def push_tails(rows, full, now):
    """1.59 (14/10, il manutentore dal telefono: «quando cambio una chat vedo per qualche secondo il testo vecchio»): per ogni
    sessione viva le ultime TAIL_N voci della chat (lo stesso formato dell'op transcript) in /tail/<id>, cifrate come
    /state: {session, id, tx, at, entries, more}. Si riscrive solo quando la trascrizione e' cambiata (tx = dimensione
    e mtime del file) e al piu' ogni TAIL_MIN_S; una sessione ferma non costa niente. L'app apre la chat da qui e poi
    chiede le voci dopo l'ultima con `transcript n:after=<id>`, come prima. tail-seen.json = {chiave: [tx, epoch]}."""
    k = key()
    seen = read_json(rdir() / "tail-seen.json", {})
    by_tm = {r.get("tmux") or r.get("name"): r for r in rows if r.get("status") != "dead"}
    out, live = {}, set()
    for s_ in full.get("sessions") or []:
        if s_.get("state") == "gone":
            continue
        row = next((r for tm, r in by_tm.items() if r.get("session_id") == s_.get("id") or tm == s_.get("id")), None)
        path = core.transcript_of(row) if row else ""
        if not path:
            continue
        kk = tail_key(s_["id"])
        live.add(kk)
        try:
            st = os.stat(path)
        except OSError:
            continue
        tx = f"{st.st_size}-{st.st_mtime_ns}"
        prev = seen.get(kk) or [None, 0]
        if prev[0] == tx or now - float(prev[1] or 0) < TAIL_MIN_S:
            continue
        out[kk] = C.encrypt(tail_body(s_["name"], s_["id"], path, st, now), k)
        seen[kk] = [tx, now]
    if out:
        rtdb("PATCH", "tail", out, {"print": "silent"})
    # le sessioni finite: via il loro nodo e il ricordo (una gone si riapre dalla trascrizione, come prima)
    gone = [x for x in seen if x not in live]
    if gone:
        rtdb("PATCH", "tail", {x: None for x in gone}, {"print": "silent"})
        for x in gone:
            seen.pop(x, None)
    if out or gone:
        write_json(rdir() / "tail-seen.json", seen)


def prune_due(now):
    """Le pulizie di /events, /share, /file e della copia locale: al piu' una volta ogni PRUNE_EVERY_S (08/10, fase 1:
    prima a ogni push, con il lock in mano)."""
    p = rdir() / "prune-at"
    try:
        if now - p.stat().st_mtime < PRUNE_EVERY_S:
            return False
    except OSError:
        pass
    try:
        p.touch()
    except OSError:
        pass
    return True


PUB_HISTORY_N = 300


def pub_history_add(at):
    """Gli istanti delle ultime PUT di /state: con /seen danno l'eta' dello stato all'arrivo (fase 0, 07/10)."""
    p = rdir() / "pub-history.json"
    h = [x for x in read_json(p, []) if isinstance(x, (int, float))]
    write_json(p, (h + [round(at, 2)])[-PUB_HISTORY_N:])


def emit(kind, title, body, ref=None, account=None, now=None, session=None, extra=None):
    """1.18 (29/09): un evento che non nasce dal diff dello stato — il diario delle 20:00 (`recap`), il resoconto
    della notte (`night_report`), la ripresa della quota all'azzeramento (`quota`) — su /events con la sveglia FCM,
    come gli altri. `body` e' il testo di Telegram senza markup, tagliato a fine riga entro 4000 caratteri. Il seq
    viene da last-state.json sotto il lock delle push, cosi' la chiave non si scontra con gli eventi del diff."""
    now = int(now or time.time())
    k = None if local_only() else key()
    lock = open(str(rdir() / "push.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        last_p = rdir() / "last-state.json"
        last = read_json(last_p, {})
        seq = int(last.get("seq") or 0) + 1
        ev = {"key": f"{now}_{seq:03d}", "kind": kind, "session": session, "account": account, "ts": now,
              "title": S.one_line(title), "body": S.cut_lines(body, S.EVENT_BODY_MAX), "ref": ref}
        ev.update(S.preview_of(ev["title"], ev["body"]))   # 1.56: preview solo se c'era qualcosa da nascondere
        ev.update(extra or {})   # 1.58: i campi propri di un tipo (quota_limit: reset_at, window, other_account, actions)
        if k:
            rtdb("PATCH", "events", {ev["key"]: C.encrypt(ev, k)}, {"print": "silent"})
        local_events_add([ev], now)
        last["seq"] = seq
        write_json(last_p, last)
    finally:
        lock.close()
    try:
        if not k:
            raise OSError("local only")
        fcm_send({"kind": kind, "session": session, "ts": now, "key": ev["key"]})
    except (urllib.error.URLError, OSError, ValueError) as ex:
        log(f"fcm FAILED ({kind}): {ex}")
    log(f"evento {kind}: {ev['title']}")
    return ev


def prune_events(now):
    """Via gli eventi oltre relay.events_days (la chiave inizia con l'epoch)."""
    limit = now - float(R.get("events_days") or 7) * 86400
    try:
        keys = rtdb("GET", "events", None, {"shallow": "true"}) or {}
    except (urllib.error.URLError, OSError, ValueError):
        return
    old = {k: None for k in keys if str(k).split("_")[0].isdigit() and int(str(k).split("_")[0]) < limit}
    if old:
        try:
            rtdb("PATCH", "events", old, {"print": "silent"})
        except (urllib.error.URLError, OSError, ValueError):
            pass


def push_async(origin="cmd"):
    """Torna subito: scrive la richiesta con il suo istante e stacca un figlio che dorme relay.debounce_s e
    pusha solo se nel frattempo nessun'altra richiesta l'ha superato (piu' hook ravvicinati = una push).
    `origin` (hook, cron, web, cmd) finisce nella riga di misura della push."""
    push_request(origin)
    if serve_alive():
        return   # 08/10 (fase 1): la fa il worker del demone, nessun processo in fila
    stamp = f"{time.time():.6f}"
    (rdir() / "push-request").write_text(stamp)
    subprocess.Popen([sys.executable, str(HERE / "cm-relay.py"), "push", "--delayed", stamp, "--origin", origin], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def push_req_dir():
    d = rdir() / "push-req"
    d.mkdir(exist_ok=True)
    return d


def push_request(origin):
    """Una richiesta di push per il worker del demone: tocca <relay.dir>/push-req/<origine> (hook, cron, cmd, web).
    La data di modifica e' la richiesta; il cron la scrive con un `touch`, senza avviare Python."""
    try:
        (push_req_dir() / re.sub(r"[^a-z0-9_-]", "", origin.lower() or "x")[:20]).touch()
    except OSError:
        pass


PUSH_MAX_WAIT_S = 10
PUSH_RETRY_S = (5, 15, 30)


class PushWorker(threading.Thread):
    """08/10 (piano prestazioni, fase 1): le push del demone, una alla volta. Una richiesta e' una data di modifica in
    push-req/; quelle arrivate durante una push diventano UNA push dopo (al piu' una in corso e una in attesa).
    Debounce relay.debounce_s dall'ultima richiesta, ma mai oltre PUSH_MAX_WAIT_S dalla prima; una push fallita si
    riprova (5, 15, 30 s) finche' passa, e intanto le richieste nuove restano in attesa, non perse."""

    def __init__(self):
        super().__init__(name="relay-push", daemon=True)
        self.stop = threading.Event()

    def pending(self, after=0.0):
        """(la richiesta piu' recente, la sua origine, le origini arrivate dopo `after`)."""
        newest, origin, since = 0.0, "", set()
        try:
            for e in os.scandir(push_req_dir()):
                t = e.stat().st_mtime
                if t > after:
                    since.add(e.name)
                if t > newest:
                    newest, origin = t, e.name
        except OSError:
            pass
        return newest, origin, since

    def run(self):
        handled, first, fails = 0.0, None, 0
        debounce = float(R.get("debounce_s") or 2)
        while not self.stop.wait(0.25):
            newest, origin, since = self.pending(handled)
            if newest <= handled:
                continue
            now = time.time()
            first = first or now
            if now - newest < debounce and now - first < PUSH_MAX_WAIT_S:
                continue
            start = time.time()
            try:
                # 08/10 (battito leggero): se nel frattempo ha chiesto solo il cron, la push prova prima a ripubblicare
                # lo stato gia' pronto
                push(origin=origin or "?", requested=min(first, newest), light=since <= {"cron"})
            except Exception as e:   # noqa: BLE001 — il worker non deve morire: la richiesta resta, si riprova
                log(f"push (worker) FAILED: {str(e)[:200]}")
                self.stop.wait(PUSH_RETRY_S[min(fails, len(PUSH_RETRY_S) - 1)])
                fails += 1
                continue
            handled, first, fails = start, None, 0


def push_delayed(stamp, origin="?"):
    time.sleep(float(R.get("debounce_s") or 2))
    try:
        if (rdir() / "push-request").read_text().strip() != stamp:
            return 0   # una richiesta piu' recente pushera' lei
    except OSError:
        pass
    # il relay puo' essere stato spento durante l'attesa: chi spegne si aspetta che nessuno scriva piu'
    if not (cm.load(warn=False).get("relay") or {}).get("enabled"):
        log("push (delayed): relay spento durante l'attesa, niente scrittura")
        return 0
    if serve_alive():
        return 0   # il demone e' ripartito durante l'attesa: la richiesta e' gia' in push-req/, la fa lui
    try:
        push(origin=origin, requested=float(stamp), stamp=stamp)
    except (RelayError, urllib.error.URLError, OSError, ValueError) as e:
        log(f"push (delayed) FAILED: {e}")
        return 1
    return 0


# ------------------------------------------------------------------ pair
def devices_path():
    return rdir() / "devices.json"


def sweep_stale_pairs():
    """Toglie da /pair i codici di ieri: quelli scaduti e quelli rimasti con la sola conferma `ok`
    dopo un accoppiamento riuscito. Silenziosa: se la rete non c'e', il pairing parte lo stesso."""
    try:
        nodes = rtdb("GET", "pair")
    except (urllib.error.URLError, OSError, ValueError, RelayError):
        return
    if not isinstance(nodes, dict):
        return
    now = time.time()
    for code, node in nodes.items():
        if not isinstance(node, dict):
            continue
        exp = node.get("exp")
        stale = (isinstance(exp, (int, float)) and exp < now) or (set(node) <= {"ok"})
        if stale:
            try:
                rtdb("DELETE", f"pair/{code}")
                log(f"pair: spazzato il codice {code} (scaduto o gia' confermato)")
            except (urllib.error.URLError, OSError, ValueError, RelayError):
                pass


def firebase_app():
    """(dati dell'app Firebase, origine) per il QR, o (None, motivo): vedi cm-config.relay_firebase_app."""
    return cm.relay_firebase_app(CFG)


def qr_payload(pair_id, pub, host, exp, app, add=False):
    """Il documento del QR (contratto 1.15, tests/fixtures/relay/pair-qr.json): v, i (id del nodo /pair/<id>), c (pc_pub),
    h (host), e (exp) e f, i dati che l'app del telefono usa per entrare nel progetto Firebase: k chiave API, p id del
    progetto, a id dell'app Android, d URL del database (relay.firebase_url: il bus che il relay scrive), t topic FCM."""
    doc = {"v": 1, "i": pair_id, "c": pub, "h": host, "e": int(exp),
           "f": {"k": app["api_key"], "p": app["project_id"], "a": app["app_id"], "d": base_url(), "t": str(R.get("fcm_topic") or "watch")}}
    if add:
        doc["m"] = "add"   # 1.30: un dispositivo in piu' — la chiave del relay arriva cifrata nella conferma, non si deriva
    return doc


def qr_lines(text, margin=2):
    """Il QR di `text` come righe di testo per il terminale: mezzi blocchi (due moduli per carattere, uno sopra e uno
    sotto), margine di `margin` moduli chiari (2: le fotocamere dei telefoni lo leggono e il QR resta stretto sul
    terminale), correzione L senza rialzo automatico (R3, 25/09: col payload reale, 349 byte, la M dava versione 14
    e 73 moduli, la L da' versione 12 e 65; il contratto non fissa il livello). I moduli chiari sono blocchi pieni e quelli scuri spazi, come `qrencode -t UTF8`: su un terminale
    scuro il QR viene nero su bianco, che e' come lo legge una fotocamera. Generatore: qrcodegen (Project Nayuki,
    MIT, scripts/qrcodegen.py), nessuna dipendenza nuova."""
    Q = _load("qrcodegen")
    qr = Q.QrCode.encode_segments(Q.QrSegment.make_segments(text), Q.QrCode.Ecc.LOW, boostecl=False)
    n = qr.get_size()

    def dark(x, y):
        return 0 <= x < n and 0 <= y < n and qr.get_module(x, y)

    lines = []
    for y in range(-margin, n + margin, 2):
        row = []
        for x in range(-margin, n + margin):
            top, bottom = dark(x, y), dark(x, y + 1)
            row.append(" " if top and bottom else "\u2584" if top else "\u2580" if bottom else "\u2588")
        lines.append("".join(row))
    return lines


def pair_accept(priv, node, w, host):
    """La risposta in /pair/<node>/watch: {watch_pub, uid, name, check} e, dal telefono (1.15), `uids` (fino a 4) e
    `names` (uid → nome). Se `check` e' HMAC della chiave concordata sulla stringa del nodo (il codice, o l'id del
    QR), torna (chiave, conferma `ok`, dispositivi {uid: {name, paired_at}}); altrimenti None. Senza `uids` un
    solo dispositivo, come prima."""
    import hmac as _hmac
    try:
        k = C.shared_key(priv, str(w["watch_pub"]))
    except Exception as e:   # noqa: BLE001 — chiave pubblica malformata
        log(f"pair: watch_pub non valida ({e})"); return None
    if not _hmac.compare_digest(C.check_code(k, node), str(w.get("check") or "")):
        return None
    uid, name = str(w["uid"]), str(w.get("name") or "watch")
    uids = [str(u) for u in (w.get("uids") if isinstance(w.get("uids"), list) else []) if isinstance(u, str) and u.strip()]
    uids = list(dict.fromkeys([uid] + uids))   # il mittente sempre, senza doppioni, nell'ordine dato
    if len(uids) > 4:
        log(f"pair: {len(uids)} uid, tengo i primi 4"); uids = uids[:4]
    names = w.get("names") if isinstance(w.get("names"), dict) else {}
    now = int(time.time())
    # 1.32: il tipo lo dice il dispositivo (kind per se', kinds = uid → kind per quelli che porta con se')
    kinds = w.get("kinds") if isinstance(w.get("kinds"), dict) else {}
    devices = {}
    for u in uids:
        dk = w.get("kind") if u == uid else kinds.get(u)   # non `k`: e' la chiave concordata
        devices[u] = dict({"name": str(names.get(u) or (name if u == uid else "watch")), "paired_at": now},
                          **({"kind": dk} if dk in S.DEVICE_KINDS else {}))
    return k, {"host": host, "check": C.check_code(k, node + ":pc")}, devices


def add_ok(ok, k_pair, relay_key):
    """1.30 (04/10, chiesto dalla sessione dell'app per il tablet): la conferma di un pairing `--add` porta la chiave
    del relay che c'e' gia', cifrata (AES-GCM, la stessa busta {v, enc} di /state) con la chiave concordata del giro
    (HKDF di X25519, come `check`). In chiaro dentro la busta: {"key": "<64 cifre hex>"}."""
    return dict(ok, key=C.encrypt({"key": relay_key.hex()}, k_pair))


PAIR_MAX_DEVICES = 8   # 1.39 (05/10): erano 4; telefono, orologio, tablet e Chromebook li riempivano, e i browser restavano fuori
ADB_TIMEOUT_S = 8


def pair_link(line):
    """1.32: l'invito come link dell'app — cmwatch://pair?q=<base64url della riga di --text, senza padding>."""
    import base64 as _b64
    return "cmwatch://pair?q=" + _b64.urlsafe_b64encode(line.encode()).rstrip(b"=").decode()


WEB_APP_URL = "https://supervisor.francescosorrentino.com"


def web_pair_link(line):
    """13/10 (chiesto dal maintainer): lo stesso invito per la web app — <relay.web_app_url>/#pair=<base64url della riga
    di --text, senza padding>, che parseInvite della web app legge. relay.web_app_url = "" lo spegne."""
    import base64 as _b64
    origin = str(R.get("web_app_url", WEB_APP_URL) or "").rstrip("/")
    return origin + "/#pair=" + _b64.urlsafe_b64encode(line.encode()).rstrip(b"=").decode() if origin else ""


def open_on_android(line):
    """1.32 (04/10, zero tocchi sul Chromebook, scelto dal maintainer): se un adb vede l'Android della stessa macchina
    (ARC, relay.adb_serial = emulator-5554) apre l'invito nell'app con `am start`; l'app chiede comunque conferma.
    Timeout brevi: l'adb shell dell'ARC a volte si blocca e si sblocca solo con `adb reconnect`, che si prova una
    volta. relay.adb = il binario (default /usr/bin/adb, poi quello nel PATH: quello dell'SDK in ~/android-sdk e' di
    un'altra architettura). Torna (aperto, motivo)."""
    adb = str(R.get("adb") or "") or ("/usr/bin/adb" if os.path.exists("/usr/bin/adb") else (shutil.which("adb") or ""))
    serial = str(R.get("adb_serial") or "emulator-5554")
    pkg = str(R.get("app_package") or "")
    if not adb or not pkg:
        return False, "no adb" if not adb else "no relay.app_package"

    def run(*a, t=ADB_TIMEOUT_S):
        try:
            p = subprocess.run([adb, "-s", serial, *a], capture_output=True, text=True, timeout=t, stdin=subprocess.DEVNULL)
            return p.returncode, (p.stdout + p.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            return -1, e.__class__.__name__
    rc, out = run("get-state", t=4)
    if rc != 0 or out != "device":
        return False, f"{serial}: {out or rc}"
    cmd = ("shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", f"'{pair_link(line)}'", pkg)
    for attempt in (1, 2):
        rc, out = run(*cmd)
        if rc == 0 and "Error" not in out:
            return True, serial
        if attempt == 1:
            run("reconnect", t=5)
    return False, (out.splitlines() or [str(rc)])[-1][:120]


def osc52(text):
    """1.32, ripiego: la riga negli appunti del sistema con la sequenza OSC 52, che il Terminale di ChromeOS (e quasi
    tutti i terminali) passa agli appunti condivisi con Android; in Crostini non ci sono wl-copy ne' xclip. Solo se
    l'uscita e' un terminale; dentro tmux la sequenza va incapsulata."""
    import base64 as _b64
    if not sys.stdout.isatty():
        return False
    seq = f"\033]52;c;{_b64.b64encode(text.encode()).decode()}\a"
    if os.environ.get("TMUX"):
        seq = "\033Ptmux;" + seq.replace("\033", "\033\033") + "\033\\"
    sys.stdout.write(seq)
    sys.stdout.flush()
    return True
PAIR_INVITE_TTL_S = 300


def unpair(arg):
    """1.39 (05/10, chiesto dall'app: «Scollega questo browser»): un dispositivo fuori da /allowed, da devices.json e
    da /seen. Il database lo legge solo chi e' in /allowed, quindi da quel momento non legge piu' niente; la chiave
    resta agli altri. Mai l'ultimo: senza dispositivi il relay non lo comanda piu' nessuno."""
    uid = str(arg or "").strip()
    devs = read_json(devices_path(), {})
    try:
        allowed = rtdb("GET", "allowed") or {}
    except (urllib.error.URLError, OSError, ValueError):
        allowed = {}
    known = set(devs) | set(allowed)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", uid) or uid not in known:
        return False, M("relay.cmd_unpair_unknown", uid=uid or "?")
    if len(known) <= 1:
        return False, M("relay.cmd_unpair_last")
    rtdb("DELETE", f"allowed/{uid}")
    rtdb("DELETE", f"seen/{uid}")
    name = str((devs.get(uid) or {}).get("name") or uid)
    devs.pop(uid, None)
    write_json(devices_path(), devs)
    log(f"unpair: {name} (uid {uid}) tolto, ne restano {len(known) - 1}")
    return True, M("relay.cmd_unpaired", name=name)


def pair_add_invite():
    """1.31 (04/10, dal telefono, chiesto dal maintainer): un dispositivo in piu' senza stare al PC. Il telefono (gia'
    accoppiato: il comando arriva cifrato con la chiave) chiede l'invito; il relay lancia da solo `relay pair --add
    --text` in un processo a parte e risponde con il QR e il codice, che il telefono mostra al tablet. Il resto e' la
    1.30: stessa stretta di mano, chiave consegnata cifrata nella conferma, /allowed come unione. Solo il relay scrive
    /allowed e consegna la chiave. text = JSON {qr, code, exp}; qr = il documento del QR (con m «add») o null senza i
    dati dell'app Firebase. Rifiuti: un pairing gia' aperto, nessuna chiave salvata, gia' PAIR_MAX_DEVICES dispositivi."""
    if not C.load_key(rdir()):
        return False, M("relay.pair_add_no_key")
    try:
        before = set(read_json(devices_path(), {})) | set(rtdb("GET", "allowed") or {})
    except (urllib.error.URLError, OSError, ValueError):
        before = set(read_json(devices_path(), {}))
    if len(before) >= PAIR_MAX_DEVICES:
        return False, M("relay.cmd_pair_add_full", max=PAIR_MAX_DEVICES)
    lock = rdir() / "pair-invite.pid"
    try:
        old = int(lock.read_text().strip())
        os.kill(old, 0)
        return False, M("relay.cmd_pair_add_busy")
    except (OSError, ValueError):
        pass
    out = rdir() / "pair-invite.out"
    with open(out, "w") as f:
        p = subprocess.Popen([sys.executable, str(HERE / "cm-relay.py"), "pair", "--add", "--text", "--timeout", str(PAIR_INVITE_TTL_S)],
                             stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    lock.write_text(str(p.pid))
    deadline = time.time() + 20
    qr, code = None, None
    while time.time() < deadline and p.poll() is None:
        for ln in out.read_text().splitlines():
            if ln.startswith("{") and qr is None:
                try:
                    qr = json.loads(ln)
                except ValueError:
                    pass
            m = re.search(r"\b(\d{6})\b", ln) if not ln.startswith("{") else None
            if m:
                code = m.group(1)
        if code:
            break
        time.sleep(0.3)
    if not code:
        rc = p.poll()
        tail = (out.read_text().strip().splitlines() or ["?"])[-1]
        if rc is None:
            p.terminate()
        return False, M("relay.cmd_pair_add_failed", why=tail[:160])
    log(f"pair_add: invito aperto dal telefono, pid {p.pid}, codice {code}")
    exp = int(qr["e"]) if isinstance(qr, dict) and qr.get("e") else int(time.time() + PAIR_INVITE_TTL_S)
    return True, json.dumps({"qr": qr, "code": code, "exp": exp}, ensure_ascii=False)


def pair(timeout=None, text=False, add=False):
    """Codice a 6 cifre e QR sullo schermo (1.15): lo stesso {pc_pub, host, exp} in /pair/<code> (l'orologio, con il
    codice) e in /pair/<id> (il telefono, che legge il QR); il relay interroga tutti e due, vince la prima risposta
    valida in /pair/<…>/watch e l'altro nodo si cancella; scadenza e tentativi sono in comune. La chiave e'
    HKDF(X25519); `check` (HMAC della chiave sulla stringa del nodo) prova che l'altra parte ha derivato la stessa
    chiave. /allowed = {uid: true} per ogni uid accettato (il pairing nuovo revoca i vecchi, la chiave e' una sola),
    devices.json sul PC. Senza i dati dell'app Firebase il QR non compare, lo dice, e il codice funziona. `text`
    stampa il JSON del QR su una riga invece di disegnarlo. Esce 0 ok, 2 dopo relay.pair_attempts check sbagliati,
    3 allo scadere di relay.pair_ttl_s.
    1.30 `add`: un dispositivo IN PIU' (il tablet accanto a telefono e orologio). Nodi e QR portano il segno
    `mode`/`m` = «add»; la stretta di mano e il `check` sono gli stessi, ma la chiave del relay resta quella di oggi e
    arriva al dispositivo nuovo cifrata nella conferma (`add_ok`); /allowed e devices.json sono l'unione con i
    dispositivi di prima (al massimo 4: oltre, la conferma e' {"error": "full"} ed esce 4); eventi e risultati
    restano. Senza una chiave salvata non parte (esce 2): non c'e' niente a cui aggiungersi."""
    import base64 as _b64
    import secrets as _secrets
    relay_key = C.load_key(rdir()) if add else None
    if add and not relay_key:
        print(M("relay.pair_add_no_key")); return 2
    ttl = float(timeout or R.get("pair_ttl_s") or 300)
    max_attempts = int(R.get("pair_attempts") or 5)
    code = f"{_secrets.randbelow(10 ** 6):06d}"
    pair_id = _b64.urlsafe_b64encode(_secrets.token_bytes(16)).rstrip(b"=").decode()   # 22 caratteri, validi come chiave RTDB
    nodes = [code, pair_id]
    sweep_stale_pairs()
    paired = False
    priv, pub = C.pair_keys()
    exp = int(time.time() + ttl)
    host = str(R.get("host") or socket.gethostname())
    app, why = firebase_app()
    for node in nodes:
        rtdb("PUT", f"pair/{node}", dict({"pc_pub": pub, "host": host, "exp": exp}, **({"mode": "add"} if add else {})), {"print": "silent"})
    if app:
        line = json.dumps(qr_payload(pair_id, pub, host, exp, app, add), ensure_ascii=False, separators=(",", ":"))
        if text:
            print(line)
        else:
            rows = qr_lines(line)
            try:
                cols = os.get_terminal_size().columns
            except OSError:
                cols = 0
            if cols and cols < len(rows[0]):
                print(M("relay.pair_qr_narrow", cols=cols, need=len(rows[0])))
            print(M("relay.pair_scan"))
            print("\n".join(rows))
    else:
        print(M("relay.pair_no_qr", why=M(why, path=cm.expand(R.get("google_services") or ""), package=R.get("app_package") or "")))
    if add:
        # 1.32: il codice a 6 cifre serve solo all'orologio; con --add si dice cosa fare nell'app
        print(M("relay.pair_code_add", code=code, min=max(1, int(round(ttl / 60)))))
        if app:
            opened, why_not = open_on_android(line)
            if opened:
                print(M("relay.pair_opened_android", serial=why_not))
            elif osc52(line):
                print(M("relay.pair_clipboard", why=why_not))
            log(f"pair --add: invito {'aperto su ' + why_not if opened else 'non aperto (' + why_not + ')'}")
    else:
        print(M("relay.pair_code", code=code))
    if app and web_pair_link(line):
        print(M("relay.pair_web_link", link=web_pair_link(line), min=max(1, int(round(ttl / 60)))))
    sys.stdout.flush()
    log(f"pair: codice {code}, id {pair_id}, scade {exp}" + ("" if app else f", senza QR ({why})"))
    attempts = 0
    try:
        while time.time() < exp:
            for node in nodes:
                try:
                    w = rtdb("GET", f"pair/{node}/watch")
                except (urllib.error.URLError, OSError, ValueError) as e:
                    log(f"pair: lettura fallita ({e}), riprovo"); continue
                if not (isinstance(w, dict) and w.get("watch_pub") and w.get("uid")):
                    continue
                got = pair_accept(priv, node, w, host)
                if got and add:
                    k, ok, devices = got
                    try:
                        before = set(read_json(devices_path(), {})) | set(rtdb("GET", "allowed") or {})
                    except (urllib.error.URLError, OSError, ValueError):
                        before = set(read_json(devices_path(), {}))
                    union = before | set(devices)
                    if len(union) > PAIR_MAX_DEVICES:
                        rtdb("PUT", f"pair/{node}", {"error": "full"}, {"print": "silent"})
                        print(M("relay.pair_add_full", n=len(union), max=PAIR_MAX_DEVICES))
                        log(f"pair --add: {len(union)} dispositivi, oltre {PAIR_MAX_DEVICES}: rifiutato")
                        paired = True   # la risposta {"error"} resta per il dispositivo, come la conferma
                        for other in nodes:
                            if other != node:
                                try:
                                    rtdb("DELETE", f"pair/{other}")
                                except (urllib.error.URLError, OSError, ValueError):
                                    pass
                        return 4
                    merged = dict(read_json(devices_path(), {}), **devices)
                    write_json(devices_path(), merged)
                    rtdb("PUT", "allowed", {u: True for u in union}, {"print": "silent"})
                    rtdb("PUT", f"pair/{node}", {"ok": add_ok(ok, k, relay_key)}, {"print": "silent"})
                    for other in nodes:
                        if other != node:
                            try:
                                rtdb("DELETE", f"pair/{other}")
                            except (urllib.error.URLError, OSError, ValueError):
                                pass
                    shown = ", ".join(f"{d['name']} (uid {u})" for u, d in devices.items())
                    print(M("relay.pair_ok_add", devices=shown, n=len(union)))
                    log(f"pair --add: ok su /pair/{'<code>' if node == code else '<id>'}: {shown}; {len(union)} dispositivi")
                    paired = True
                    return 0
                if got:
                    k, ok, devices = got
                    C.save_key(rdir(), k)
                    write_json(devices_path(), devices)
                    rtdb("PUT", "allowed", {u: True for u in devices}, {"print": "silent"})
                    # chiave nuova: gli eventi e i risultati cifrati con la vecchia non si aprono piu' → via
                    for stale in ("events", "result"):
                        try:
                            rtdb("DELETE", stale)
                        except (urllib.error.URLError, OSError, ValueError):
                            pass
                    rtdb("PUT", f"pair/{node}", {"ok": ok}, {"print": "silent"})
                    # La conferma deve restare leggibile finche' l'altra parte non la prende: l'orologio interroga
                    # /pair/<code>/ok una volta al secondo (FirebaseTransport.pollMs) e un solo secondo di vita
                    # bastava a farlo arrivare tardi — PC accoppiato, orologio fermo su «Code not accepted» (19/09).
                    # Quindi: si tolgono subito le chiavi del giro, cosi' sullo stesso nodo non si puo' iniziare
                    # un'altra stretta di mano (la PUT qui sopra riscrive il nodo intero: via pc_pub, watch, host,
                    # exp), resta solo `ok`, che il prossimo `pair` spazza via (vedi sweep_stale_pairs); l'altro
                    # nodo del giro (1.15) se ne va subito.
                    for other in nodes:
                        if other != node:
                            try:
                                rtdb("DELETE", f"pair/{other}")
                            except (urllib.error.URLError, OSError, ValueError):
                                pass
                    shown = ", ".join(f"{d['name']} (uid {u})" for u, d in devices.items())
                    print(M("relay.pair_ok", devices=shown, path=str(C.key_path(rdir()))))
                    log(f"pair: ok su /pair/{'<code>' if node == code else '<id>'}: {shown}")
                    paired = True
                    return 0
                attempts += 1
                log(f"pair: check sbagliato ({attempts}/{max_attempts})")
                rtdb("DELETE", f"pair/{node}/watch")
                if attempts >= max_attempts:
                    print(M("relay.pair_failed", n=attempts)); return 2
            time.sleep(1)
        print(M("relay.pair_timeout", s=int(ttl))); return 3
    finally:
        if not paired:   # accoppiato: /pair/<nodo>/ok resta per l'altra parte, lo spazza il pair successivo
            for node in nodes:
                try:
                    rtdb("DELETE", f"pair/{node}")
                except (urllib.error.URLError, OSError, ValueError, RelayError):
                    pass


# ------------------------------------------------------------------ esecuzione dei comandi (allow-list)
# in cm-relay-cmds.py (25/10: ogni script sotto i 256 KiB della directory), eseguito qui nello stesso namespace
_CMDS = HERE / "cm-relay-cmds.py"
exec(compile(_CMDS.read_text(encoding="utf-8"), str(_CMDS), "exec"))


# ------------------------------------------------------------------ serve (SSE su /cmd)
def serve_pid_path():
    return rdir() / "serve.pid"


def serve_status_path():
    return rdir() / "serve.json"


def serve_alive():
    try:
        pid = int(serve_pid_path().read_text().strip() or 0)
        if pid > 0:
            os.kill(pid, 0)
            return pid
    except (OSError, ValueError):
        pass
    return 0


def serve_stop():
    pid = serve_alive()
    if not pid:
        return False
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    for _ in range(30):
        time.sleep(0.1)
        if not serve_alive():
            break
    return True


def sse_lines(resp):
    """Gli eventi (event, data) di uno stream SSE, riga per riga."""
    ev, data = None, []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "":
            if ev is not None:
                yield ev, "\n".join(data)
            ev, data = None, []


def commands_from(ev, payload):
    """{id: doc} dai `put`/`patch` sul nodo /cmd."""
    if ev not in ("put", "patch"):
        return {}
    try:
        d = json.loads(payload or "null") or {}
    except ValueError:
        return {}
    path, data = str(d.get("path") or "/"), d.get("data")
    if path in ("", "/"):
        return dict(data) if isinstance(data, dict) else {}
    cid = path.strip("/").split("/")[0]
    if ev == "put" and "/" not in path.strip("/"):
        return {cid: data}
    return {}


PASSIVE_OPS = ("transcript", "screen", "last", "file", "projects", "search", "timeline", "pair_add", "night", "agenda", "changes", "diff", "studio", "nights", "done", "close_preview")   # letture: non cambiano lo stato, vengono dopo i comandi dell'utente


def in_order(cmds):
    """I comandi nell'ordine in cui il telefono o l'orologio li ha dati (`issued`, poi l'id): le chiavi di /cmd sono
    uuid casuali, e dopo una riconnessione (una all'ora per il token) il `put` iniziale li porta tutti insieme in
    ordine di chiave — un prompt poteva partire dopo la risposta che lo seguiva (30/09, dal telefono)."""
    try:
        k = key()
    except RelayError:
        return list(cmds.items())

    def clear(item):
        doc = item[1]
        try:
            c = C.decrypt(doc, k) if isinstance(doc, dict) and "enc" in doc else doc
            return c if isinstance(c, dict) else {}
        except (ValueError, TypeError, AttributeError):
            return {}
    items = [(cid, doc, clear((cid, doc))) for cid, doc in cmds.items()]
    # prima i comandi dell'utente, poi le letture; dentro ciascun gruppo nell'ordine in cui sono stati dati
    items.sort(key=lambda x: (str(x[2].get("op") or "") in PASSIVE_OPS, float(x[2].get("issued") or 0), x[0]))
    # di piu' letture uguali della stessa sessione vale l'ultima: il telefono si e' gia' scordato le altre
    newest = {}
    for cid, _, c in items:
        if str(c.get("op") or "") in PASSIVE_OPS:
            newest[(c.get("op"), c.get("session"))] = cid
    out = []
    for cid, doc, c in items:
        if str(c.get("op") or "") in PASSIVE_OPS and newest.get((c.get("op"), c.get("session"))) != cid:
            superseded(cid, k)
            continue
        out.append((cid, doc))
    return out


def superseded(cid, k):
    """Una lettura rimpiazzata da una piu' recente della stessa sessione: risultato breve e /cmd pulito, cosi' nulla
    resta appeso."""
    try:
        rtdb("PUT", f"result/{cid}", C.encrypt({"id": cid, "ok": False, "text": M("relay.cmd_superseded"), "at": int(time.time())}, k), {"print": "silent"})
        rtdb("DELETE", f"cmd/{cid}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"cmd {cid}: superseded non scritto ({e})")


def ledger_write(event, **fields):
    """Una riga nel registro comune (<state_dir>/ledger.jsonl), stessa forma dell'hook: cosi' il recap e le
    diagnosi vedono anche i comandi arrivati dal polso (design §5: «`by` dice chi ha risposto, il registro lo annota»)."""
    try:
        p = Path(cm.expand(CFG["state_dir"])) / "ledger.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, "session_id": "", "cwd": "", "account": "", "pid": os.getpid()}
        row.update(fields)
        with open(p, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


_PENDING = {"cid": None, "done": None}   # il comando del bus in esecuzione, per mark_done_now


def mark_done_now():
    """17/10/2026: segna eseguito il comando del bus in corso, su disco, PRIMA delle pulizie (la cancellazione di
    /share dopo un report): un riavvio del demone in quel momento lasciava /cmd e il comando non segnato, e al ritorno
    la segnalazione partiva una seconda volta. Senza un comando del bus in corso (API locale) non fa nulla."""
    cid, done = _PENDING["cid"], _PENDING["done"]
    if not cid or done is None or cid in done:
        return
    done.append(cid)
    del done[:-500]
    write_json(rdir() / "done-cmds.json", done)


def handle_cmd(cid, doc, done, arrived=None):
    if doc is None:
        return False
    if cid in done:
        # gia' eseguito (Riprova dal polso, o la riconnessione che rimanda tutto /cmd): via senza rieseguire
        try:
            rtdb("DELETE", f"cmd/{cid}")
        except (urllib.error.URLError, OSError, ValueError):
            pass
        return False
    k = key()
    try:
        cmd = C.decrypt(doc, k) if isinstance(doc, dict) and "enc" in doc else doc
    except ValueError as e:
        log(f"cmd {cid}: non decifrabile ({e}), scartato"); cmd = None
    if not isinstance(cmd, dict):
        try:
            rtdb("DELETE", f"cmd/{cid}")
        except (urllib.error.URLError, OSError, ValueError):
            pass
        done.append(cid); return True
    cmd.pop("_local", None)   # solo l'API locale lo mette
    cmd.setdefault("id", cid)
    _PENDING.update(cid=cid, done=done)
    try:
        result = run_cmd(cmd, arrived)
    finally:
        _PENDING.update(cid=None, done=None)
    t_put = time.time()
    try:
        rtdb("PUT", f"result/{cid}", C.encrypt(result, k), {"print": "silent"})
        rtdb("DELETE", f"cmd/{cid}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"cmd {cid}: result non scritto ({e})")
    else:
        if arrived and str(cmd.get("op") or "") not in PASSIVE_OPS:
            # fase 0 (07/10): l'attesa vera di un prompt o di una risposta, dall'arrivo sullo stream all'esito sul bus
            log(f"cmd {cid}: esito scritto · {cmd.get('op')} · dall'arrivo {_s(time.time() - arrived)} s · put {_s(time.time() - t_put)} s")
    if cid not in done:
        done.append(cid)
    del done[:-500]
    write_json(rdir() / "done-cmds.json", done)
    return True


EXEC_LOCK = threading.Lock()   # 1.35: i comandi dal bus e quelli dell'API locale, uno alla volta come prima


def run_cmd(cmd, arrived=None):
    """Esegue un Cmd (gia' in chiaro, con `id`) e ne ritorna il CmdResult: log, registro, osservazioni e push dopo
    i comandi che cambiano lo stato. Lo usano il daemon sul bus e l'API locale. `arrived` = quando e' arrivato dallo
    stream (fase 0, 07/10: la fila prima dell'esecuzione)."""
    cid = str(cmd.get("id") or "")
    t_in = time.time()
    with EXEC_LOCK:
        t_run = time.time()
        res = execute(cmd)
    t_done = time.time()
    try:
        issued = float(cmd.get("issued") or 0)
    except (TypeError, ValueError):
        issued = 0.0
    timing = (f" · fila {_s(t_run - (arrived or t_in))} s · esecuzione {_s(t_done - t_run)} s"
              + (f" · issued {_s(t_run - issued)} s (orologio del dispositivo)" if issued > 1e9 else ""))
    ok, text = res[0], res[1]
    extra = res[2] if len(res) > 2 and isinstance(res[2], dict) else {}   # 1.13: {"session": nome} dopo un launch
    by = str(cmd.get("by") or "?")
    if isinstance(cmd.get("uid"), str):   # 1.89: chi l'ha mandato, per l'ultimo segno di vita del dispositivo
        try:
            device_activity("cmd", {cmd["uid"]: t_in})
        except (OSError, ValueError):
            pass
    # 15/10: per una risposta, la domanda a cui risponde (1.52) o «senza id»: dal log si vede se l'app la manda
    asked = (f" · domanda {cmd.get('question')}" if cmd.get("question") else " · senza id domanda") if cmd.get("op") in ("answer", "allow_all") else ""
    log(f"cmd {cid}: {cmd.get('op')} {cmd.get('session') or ''} da {by}{' (locale)' if cmd.get('_local') else ''}{asked} → {'ok' if ok else 'ERR'} {str(text)[:80]}{timing}")
    row = last_sessions().get(str(cmd.get("session") or "")) or {}
    # 17/10/2026: le letture (PASSIVE_OPS) non vanno nel registro: non cambiano niente, e ogni apertura della chat ne
    # scriveva una riga; un errore resta nel log e nelle osservazioni
    if str(cmd.get("op") or "") not in PASSIVE_OPS:
        ledger_write("watch-cmd", op=str(cmd.get("op") or ""), name=str(cmd.get("session") or ""), by=by, ok=bool(ok),
                     text=str(text)[:200], session_id=row.get("id") or "", account=row.get("account") or "")
    if not ok:   # osservazioni (23/09): un comando del polso fallito finisce nella casella di supervisor (claude-observe)
        try:
            spec = importlib.util.spec_from_file_location("cm_observe", HERE.parent / "observe" / "observe.py")
            obs = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(obs)
            obs.record_external("relay", f"relay {cmd.get('op') or ''}", str(text), str(cmd.get("session") or ""))
        except Exception:   # noqa: BLE001 — un'informazione in piu': mai far cadere il comando
            pass
    # 30/09 (dal vivo): la push sincrona dopo OGNI comando costava ~6 s, anche dopo una lettura della chat, e i prompt
    # restavano in fila dietro le letture fino a scadere sul telefono. Le letture non cambiano lo stato: niente push;
    # gli altri comandi la chiedono in background (debounce) e il daemon passa subito al comando dopo
    if str(cmd.get("op") or "") not in PASSIVE_OPS:
        try:
            push_async()
        except OSError as e:
            log(f"push dopo il comando FAILED: {e}")
    return {"id": cid, "ok": bool(ok), "text": str(text), **extra, "at": int(time.time())}


# ------------------------------------------------------------------ web app e API locale (1.35)
W = None   # cm-relay-web, caricato solo quando serve


def web_cfg():
    return R.get("web") or {}


class LocalApi:
    """Quello che cm-relay-web chiede al relay: la copia locale di stato ed eventi, i comandi, file e allegati."""

    def state_stamp(self):
        try:
            st = (rdir() / "local" / "state.json").stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def state(self):
        st = read_json(rdir() / "local" / "state.json", None)
        if st is None:   # appena acceso: il primo battito arriva entro un minuto, si anticipa
            try:
                push_async("web")
            except OSError:
                pass
        return st

    def events(self, since):
        evs = [e for e in read_json(rdir() / "local" / "events.json", {}).values() if float(e.get("ts") or 0) > since]
        return sorted(evs, key=lambda e: str(e.get("key") or ""), reverse=True)

    def cmd(self, cmd):
        cmd = dict(cmd)
        cmd["id"] = str(cmd.get("id") or "") or str(uuid.uuid4())
        cmd["_local"] = True
        cmd.setdefault("by", "web")
        cmd.setdefault("device", "web")   # 1.36: dall'API locale arriva solo la web app
        return run_cmd(cmd)

    _tails = {}   # id → l'ultimo corpo di /api/tail, finche' tx non cambia

    def tail(self, sid):
        """1.70: il corpo di /tail/<id> per la API locale, o None se la sessione non e' viva o non ha trascrizione."""
        hit = read_json(rdir() / "local" / "tail-paths.json", {}).get(sid)
        if not hit:
            return None
        try:
            st = os.stat(hit["path"])
        except (OSError, KeyError, TypeError):
            return None
        old = self._tails.get(sid)
        if old and old["tx"] == f"{st.st_size}-{st.st_mtime_ns}":
            return old
        body = tail_body(hit.get("session") or sid, sid, hit["path"], st, time.time())
        self._tails[sid] = body
        return body

    def file(self, fid):
        p = rdir() / "local" / "file" / f"{fid}.json"
        if not p.is_file() or time.time() - p.stat().st_mtime > SHARE_TTL_S:
            return None
        return read_json(p, None)

    def share(self, sid, blob):
        d = local_dir("share")
        (d / f"{sid}.bin").write_bytes(blob["data"])
        os.chmod(d / f"{sid}.bin", 0o600)
        write_json(d / f"{sid}.json", {"mime": blob["mime"], "name": blob.get("name"), "size": len(blob["data"])})


def web_start():
    """Dentro `relay serve`, se relay.web.enabled: la porta occupata non ferma il daemon del bus."""
    global W
    w = web_cfg()
    if not w.get("enabled"):
        return None
    W = W or _load("cm-relay-web")
    try:
        srv = W.start(int(w.get("port") or 8765), W.load_token(rdir()), cm.expand(w["dir"]) if w.get("dir") else "", LocalApi(),
                      str(R.get("web_app_url", WEB_APP_URL) or ""), rdir() / "web-cache")
    except OSError as e:
        log(f"web: porta {w.get('port')} non disponibile ({e})")
        return None
    log(f"web: http://127.0.0.1:{srv.server_address[1]}/")
    return srv


def reload_relay_cfg():
    global CFG, R
    try:
        CFG = cm.load(warn=False)
        R = CFG["relay"]
    except (OSError, ValueError, KeyError):
        pass


def web_enable_local():
    """`relay web` senza Firebase e col relay spento: relay.enabled e relay.web.enabled a true nel config in uso (il
    resto del file com'e'), e R ricaricato. Solo quando relay.firebase_url e' vuoto: un relay spento apposta resta spento."""
    global CFG, R
    target = cm.config_path()
    try:
        raw = json.loads(target.read_text()) if target.is_file() else {}
    except ValueError:
        raw = {}
    relay = raw.get("relay") if isinstance(raw.get("relay"), dict) else {}
    relay["enabled"] = True
    relay["web"] = dict(relay.get("web") if isinstance(relay.get("web"), dict) else {}, enabled=True)
    raw["relay"] = relay
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
    CFG = cm.load(warn=False)
    R = CFG["relay"]


def web(rest):
    """`relay web [--no-open]`: l'indirizzo della web app locale col token; lo apre nel browser (xdg-open, su
    ChromeOS il Chrome vero) se il daemon la sta servendo."""
    global W
    w = web_cfg()
    if not w.get("enabled") or not R.get("enabled"):
        if R.get("enabled") or not local_only():
            if not w.get("enabled"):
                print(M("relay.web_disabled")); return 2
            print(M("relay.disabled")); return 2
        web_enable_local()   # 13/10: la prova da un minuto, senza Firebase — accende relay e web nel config
        print(M("relay.web_local_on", path=str(cm.config_path())))
        w = web_cfg()
    W = W or _load("cm-relay-web")
    port = int(w.get("port") or 8765)
    link = W.url(port, W.load_token(rdir()))
    if not serve_alive():
        ensure()
    print(link)
    if "--no-open" not in rest:
        opener = os.environ.get("CM_RELAY_OPEN") or shutil.which("xdg-open") or shutil.which("garcon-url-handler")
        if opener:
            subprocess.Popen([opener, link], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        else:
            print(M("relay.web_no_opener"), file=sys.stderr)
    return 0


def serve():
    if not R.get("enabled"):
        print(M("relay.disabled")); return 2
    lock = open(str(rdir() / "serve.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(M("relay.serve_locked")); log("serve: lock preso da un altro serve, esco"); return 4
    serve_pid_path().write_text(str(os.getpid()))
    started = time.time()
    import signal

    def _stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _stop); signal.signal(signal.SIGINT, _stop)
    log(f"serve: avvio pid {os.getpid()}")
    search_warm_async()   # 04/10: la prima ricerca dal telefono trova la cache gia' piena
    push_request("serve")   # all'avvio una push, poi quelle chieste da hook, cron e comandi
    worker = PushWorker()
    worker.start()
    web_srv = web_start()   # 1.35: la web app e l'API locale, se relay.web.enabled
    done = list(read_json(rdir() / "done-cmds.json", []))
    st_ = {"pid": os.getpid(), "started": started, "last_cmd_ts": None, "last_cmd": "", "served": 0, "reconnects": 0,
           "code": code_print()}   # 08/10: l'impronta del codice con cui gira, per `ensure`
    # subito su file: finche' lo stream regge il ciclo non torna qui, e `relay status` mostrerebbe i numeri del
    # processo precedente (visto dal vivo il 14/09: «46 riconnessioni» su un daemon appena avviato)
    try:
        serve_status_path().write_text(json.dumps(st_))
    except OSError:
        pass
    tries = 0
    try:
        while local_only():   # 13/10: solo la web app locale; i comandi arrivano dall'API su 127.0.0.1
            time.sleep(30)
            reload_relay_cfg()   # dopo `relay setup` il bus parte senza riavviare il daemon
        while True:
            try:
                url = f"{base_url()}/cmd.json?{urllib.parse.urlencode({'access_token': token()})}"
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=float(R.get("serve_timeout_s") or 50) + 40) as resp:
                    if tries:
                        log("serve: riconnesso")
                    tries = 0
                    for ev, payload in sse_lines(resp):
                        arrived = time.time()
                        if ev == "auth_revoked":
                            log("serve: auth_revoked, nuovo token"); (rdir() / "token.json").unlink(missing_ok=True); break
                        for cid, doc in in_order(commands_from(ev, payload)):
                            if handle_cmd(cid, doc, done, arrived):
                                st_["last_cmd_ts"] = time.time(); st_["last_cmd"] = cid; st_["served"] += 1
                                try:
                                    serve_status_path().write_text(json.dumps(st_))
                                except OSError:
                                    pass
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    (rdir() / "token.json").unlink(missing_ok=True)
                delay = BACKOFF[min(tries, len(BACKOFF) - 1)]; tries += 1; st_["reconnects"] += 1
                log(f"serve: HTTP {e.code}, riconnessione fra {delay}s"); time.sleep(delay)
            except (urllib.error.URLError, OSError, ValueError, RelayError) as e:
                delay = BACKOFF[min(tries, len(BACKOFF) - 1)]; tries += 1; st_["reconnects"] += 1
                log(f"serve: rete assente ({str(e)[:80]}), riconnessione fra {delay}s"); time.sleep(delay)
            try:
                serve_status_path().write_text(json.dumps(st_))
            except OSError:
                pass
    finally:
        log("serve: stop")
        worker.stop.set()
        if web_srv:
            web_srv.stop()
        try:
            serve_pid_path().unlink()
        except OSError:
            pass
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()
    return 0


CODE_FILES = ("cm-relay.py", "cm-relay-cmds.py", "cm-relay-state.py", "cm-relay-crypto.py", "cm-relay-web.py", "cm-core.py", "cm-diff.py")
STALE_S = 180


def code_print():
    """L'impronta del codice del relay su disco (dimensione e data dei file che il demone carica): se il demone vivo ne
    ha una diversa, gira con codice vecchio (08/10: 62 minuti di stato fermo, cron e hook gia' col codice nuovo)."""
    out = []
    for n in CODE_FILES:
        try:
            st = (HERE / n).stat()
            out.append(f"{n}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            pass
    return ";".join(out)


def guard_path():
    return rdir() / "guard.json"


def _hm(t):
    return time.strftime("%H:%M", time.localtime(t))


def guard(now=None):
    """08/10 (chiesto dal maintainer dopo lo stato fermo dalle 10:47 alle 11:49): la guardia che `ensure` fa ogni minuto.
    Se l'ultimo stato pubblicato ha piu' di STALE_S, push diretta con origine «guardia»; se il lock delle push e' preso
    da piu' di STALE_S, non aspetta e lo dice. Un guasto si avvisa una volta su Telegram appena visto e, quando lo stato
    riparte, con l'evento `relay_stale` (1.45) che dice da quando, perche' e come si e' sbloccato."""
    now = now or time.time()
    g = read_json(guard_path(), {})
    last = read_json(rdir() / "last-state.json", {})
    pub = float((last.get("state") or {}).get("published_at") or last.get("pushed_at") or 0)
    if not pub:
        return
    if now - pub <= STALE_S:
        if g.get("since"):
            cause = g.get("cause") or "push"
            body = M(f"relay.stale_{cause}", since=_hm(g["since"]), at=_hm(g.get("fixed_at") or pub),
                     lock=int((now - float(g.get("lock_since") or now)) // 60)) + " " + M("relay.stale_ok")
            try:
                emit("relay_stale", M("relay.stale_title", min=max(1, int((pub - g["since"]) // 60))), body)
            except Exception as e:   # noqa: BLE001 — l'avviso in piu' non deve far cadere la guardia
                log(f"guardia: evento relay_stale non mandato ({str(e)[:120]})")
            guard_path().unlink(missing_ok=True)
        elif g and now - float(g.get("restarted_at") or 0) > 600:
            guard_path().unlink(missing_ok=True)
        return
    g.setdefault("since", pub)
    g.setdefault("cause", "push")
    lock = open(str(rdir() / "push.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        busy = False
        fcntl.flock(lock, fcntl.LOCK_UN)
    except OSError:
        busy = True
    finally:
        lock.close()
    if busy:
        g.setdefault("lock_since", now)
        if now - float(g["lock_since"]) > STALE_S:
            g["cause"] = "lock"
            log(f"guardia: stato fermo da {int(now - pub)} s, lock delle push occupato da {int(now - g['lock_since'])} s")
    else:
        g.pop("lock_since", None)
        log(f"guardia: stato fermo da {int(now - pub)} s, push diretta")
        try:
            push(origin="guardia")
            g["fixed_at"] = time.time()
        except Exception as e:   # noqa: BLE001
            log(f"push (guardia) FAILED: {str(e)[:200]}")
    if not g.get("told") and (g["cause"] != "lock" or now - float(g.get("lock_since") or now) > STALE_S):
        text = M("relay.stale_title", min=max(1, int((now - g["since"]) // 60))) + "\n" + M(
            f"relay.stale_{g['cause']}", since=_hm(g["since"]), at=_hm(g.get("fixed_at") or now),
            lock=int((now - float(g.get("lock_since") or now)) // 60))
        try:
            _load("cm-bot").send(text, watch_quiet=False)
        except Exception as e:   # noqa: BLE001 — senza bot resta il log e, alla ripresa, l'evento
            log(f"guardia: avviso Telegram non mandato ({str(e)[:120]})")
        g["told"] = True
    write_json(guard_path(), g)


def ensure():
    if not R.get("enabled"):
        return 0
    pid = serve_alive()
    if pid:
        code = read_json(serve_status_path(), {}).get("code")
        if not code or code == code_print():
            print(M("relay.serve_alive", pid=pid))
            guard()
            return 0
        # il demone gira con il codice di prima: cron e hook usano gia' quello nuovo (08/10, 62 minuti di stato fermo)
        log(f"serve: riavviato, codice cambiato (pid {pid})")
        g = read_json(guard_path(), {})
        g.update(cause="code", restarted_at=time.time())
        write_json(guard_path(), g)
        serve_stop()
    lp = rdir() / "relay.log"
    with open(lp, "a") as out:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve"], stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         start_new_session=True, env=os.environ.copy())
    for _ in range(30):
        time.sleep(0.1)
        if serve_alive():
            break
    pid = serve_alive()
    print(M("relay.serve_started", pid=pid) if pid else M("relay.serve_stopped"))
    return 0 if pid else 1


def crontab_read():
    return subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-l"], capture_output=True, text=True).stdout


def crontab_write(text):
    subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-"], input=text, text=True, check=True)


def cron_lines():
    shim = cm.home() / ".local" / "bin" / "supervisor"
    # 08/10 (fase 1): il battito e' un `touch` della richiesta, senza avviare Python; la push la fa il demone
    req = rdir() / "push-req" / "cron"
    return [f"* * * * * {shim} relay ensure >/dev/null 2>&1", f"* * * * * mkdir -p {req.parent} && touch {req} # supervisor relay battito"]


def install():
    if not R.get("enabled"):
        print(M("relay.disabled")); return 2
    cur = crontab_read()
    lines = [l for l in cur.splitlines() if "supervisor relay" not in l]
    lines += ["# supervisor relay: l'orologio (ascolto dei comandi, battito di /state ogni minuto)"] + cron_lines()
    crontab_write("\n".join(lines) + "\n")
    print(M("relay.cron_installed", s=int(R.get("heartbeat_s") or 60)))
    ensure()
    return 0


def uninstall():
    if serve_stop():
        print(M("relay.serve_stopped"))
    cur = crontab_read()
    if "supervisor relay" in cur:
        lines = [l for l in cur.splitlines() if "supervisor relay" not in l]
        crontab_write("\n".join(lines) + ("\n" if lines else ""))
        print(M("relay.cron_removed"))
    return 0


def off():
    """Pausa: ferma il daemon (il cron resta; `ensure` lo rialza al prossimo minuto solo se relay.enabled)."""
    print(M("relay.serve_stopped") if serve_stop() else M("relay.serve_alive", pid=0).split("(")[0].strip())
    return 0


def status():
    pid = serve_alive()
    last = read_json(rdir() / "last-state.json", {})
    st_ = read_json(serve_status_path(), {})
    serve_s = (f"VIVO pid {pid}, comandi {st_.get('served', 0)}, riconnessioni {st_.get('reconnects', 0)}" if pid else "fermo")
    print(M("relay.status", state="abilitato" if R.get("enabled") else "spento", url=R.get("firebase_url") or "-",
            key="ok" if C.load_key(rdir()) else "assente (relay pair)", sa="ok" if sa_path().is_file() else f"assente ({sa_path()})",
            serve=serve_s, last=(time.strftime("%H:%M:%S", time.localtime(last["pushed_at"])) if last.get("pushed_at") else "-"),
            cron="yes" if "supervisor relay" in crontab_read() else "no"))
    return 0


# ------------------------------------------------------------------ misure (fase 0, 07/10)
_NUM = r"(-?[0-9]+,[0-9])"
STATS_PUSH = re.compile(r"^(\S+) push: \d+ sessioni, (\d+) eventi · origine (\S+) · attesa " + _NUM + r" s · lock " + _NUM
                        + r" s · raccolta " + _NUM + r" s · pubblicazione " + _NUM + r" s · contorno " + _NUM
                        + r" s · ts→put " + _NUM + r" s · cpu " + _NUM + r"\+" + _NUM + r" s")
STATS_CMD = re.compile(r"^(\S+) cmd \S+: (\S+) .*· fila " + _NUM + r" s · esecuzione " + _NUM + r" s")
STATS_DONE = re.compile(r"^(\S+) cmd \S+: esito scritto · (\S+) · dall'arrivo " + _NUM + r" s")
STATS_ARRIVAL = re.compile(r"^(\S+) arrivo: \S+ " + _NUM + r" s dopo la push")


def _num(x):
    return float(x.replace(",", "."))


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else 0.0


def stats(rest):
    """`relay stats [--since T] [--until T]`: p50, p95 e massimo delle misure del log (push, eta' dello stato, comandi).
    T = HH:MM di oggi o un istante ISO (2026-10-07T16:20). Solo le righe con le misure (dalla fase 0 in poi)."""
    def bound(flag):
        if flag not in rest or rest.index(flag) + 1 >= len(rest):
            return None
        v = rest[rest.index(flag) + 1]
        return time.strftime("%Y-%m-%dT") + v if re.fullmatch(r"\d{1,2}:\d{2}", v) and len(v) == 5 else (
            time.strftime("%Y-%m-%dT") + "0" + v if re.fullmatch(r"\d:\d{2}", v) else v)
    since, until = bound("--since"), bound("--until")
    lines = []
    for p in (log_path().with_name(log_path().name + ".1"), log_path()):
        try:
            lines += p.read_text(errors="replace").splitlines()
        except OSError:
            pass
    keep = lambda ts: (since is None or ts >= since) and (until is None or ts < until)  # noqa: E731
    push, cmds, done, arr = [], {}, {}, []
    for l in lines:
        if (mm := STATS_PUSH.match(l)) and keep(mm.group(1)):
            g = [_num(x) for x in mm.groups()[3:]]
            push.append({"ts": mm.group(1), "origin": mm.group(3), "wait": g[0], "lock": g[1], "dur": g[1] + g[2] + g[3] + g[4],
                         "collect": g[2], "age": g[5], "cpu": g[6] + g[7]})
        elif (mm := STATS_DONE.match(l)) and keep(mm.group(1)):
            done.setdefault(mm.group(2), []).append(_num(mm.group(3)))
        elif (mm := STATS_CMD.match(l)) and keep(mm.group(1)):
            cmds.setdefault(mm.group(2), []).append((_num(mm.group(3)), _num(mm.group(4))))
        elif (mm := STATS_ARRIVAL.match(l)) and keep(mm.group(1)):
            arr.append(_num(mm.group(2)))
    row = lambda name, xs: f"  {name:<34} n {len(xs):>5}   p50 {_pct(xs, .5):7.1f}   p95 {_pct(xs, .95):7.1f}   max {max(xs) if xs else 0:7.1f}"  # noqa: E731
    out = [f"relay stats {since or 'inizio'} → {until or 'ora'}", "push"]
    if push:
        mins = {p["ts"][:16] for p in push}
        out.append(f"  {'push':<34} n {len(push):>5}   al minuto {len(push) / max(1, len(mins)):.2f} (minuti con push {len(mins)})")
        out += [row("durata (lock compreso) s", [p["dur"] for p in push]), row("attesa sul lock s", [p["lock"] for p in push]),
                row("attesa dalla richiesta s", [p["wait"] for p in push]), row("raccolta s", [p["collect"] for p in push]),
                row("cpu (propria + figli) s", [p["cpu"] for p in push]), row("ts → put (età alla partenza) s", [p["age"] for p in push])]
        orig = {}
        for p in push:
            orig[p["origin"]] = orig.get(p["origin"], 0) + 1
        out.append("  origine: " + ", ".join(f"{k} {v}" for k, v in sorted(orig.items())))
    else:
        out.append("  nessuna riga di misura")
    out += ["stato all'arrivo (stima da /seen)", row("dopo la push s", arr)]
    out.append("comandi")
    for op in sorted(set(cmds) | set(done)):
        xs = cmds.get(op, [])
        out.append(row(f"{op}: fila s", [x[0] for x in xs]))
        out.append(row(f"{op}: esecuzione s", [x[1] for x in xs]))
        if op in done:
            out.append(row(f"{op}: dall'arrivo all'esito s", done[op]))
    print("\n".join(out))
    return 0


def other_account(acc):
    """1.58: l'altro account configurato, o None se ce n'e' uno solo (o piu' di due: quale non si indovina)."""
    others = [a for a in CFG["accounts"] if a != acc]
    return others[0] if len(others) == 1 else None


def quota_window(acc):
    """1.58: (finestra, reset) del limite toccato da `acc`: «5h» se le cinque ore sono piene, altrimenti «week»."""
    q = (_json_cmd("quota", "--json", expect="{") or {}).get(acc) or {}
    if float(q.get("cinque_ore_pct") or 0) >= 99 or float(q.get("settimana_pct") or 0) < 99:
        return "5h", q.get("reset_cinque_ore")
    return "week", q.get("reset_settimanale")


# 1.85 (25/10/2026): il limite di un modello. Claude Code lo scrive nella trascrizione come messaggio <synthetic>
# («You've reached your Fable limit. Run /usage-credits to continue or switch models with /model.»); il payload di
# StopFailure porta solo il 429 generico («would exceed your account's rate limit»). Il nome comincia con la maiuscola:
# «reached your account's …» non e' un modello.
LIMIT_MODEL = re.compile(r"reached your ([A-Z][\w.\-]*(?: [\w.\-]+){0,2}) limit")
LIMIT_RESET = re.compile(r"\bresets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\b", re.I)
OTHER_MODEL_ID, OTHER_MODEL_LABEL = "claude-opus-5-5[1m]", "Opus 5.5"


def other_model():
    """1.85: (id, etichetta) del modello su cui proseguire al limite di un altro: Opus dalla lista di tune.models."""
    m = next((m for m in ((CFG.get("tune") or {}).get("models") or []) if str(m.get("id") or "").startswith("claude-opus")), None)
    return (m["id"], m.get("label") or OTHER_MODEL_LABEL) if m else (OTHER_MODEL_ID, OTHER_MODEL_LABEL)


def reset_from_text(text, now=None):
    """1.85: l'istante del reset scritto nel messaggio del limite («resets 3:10pm», «resets at 23:10»), il prossimo nel
    futuro in ora locale; None se il messaggio non dice l'ora."""
    m = LIMIT_RESET.search(text or "")
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    now = time.time() if now is None else now
    lt = time.localtime(now)
    for d in (0, 1):
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + d, h, mi, 0, 0, 0, -1))
        if t > now:
            return int(t)
    return None


def limit_text(text, transcript="", session_id="", tries=3, pause=1.0):
    """1.85: il testo del limite. Dal testo dell'hook se dice gia' il modello; altrimenti dall'ultimo messaggio
    dell'assistente nella trascrizione (`transcript`, o cercata per session_id), se e' <synthetic> e recente: l'hook
    puo' arrivare prima che la riga sia sul disco, quindi fino a `tries` letture."""
    if LIMIT_MODEL.search(text or ""):
        return text
    path = transcript
    if not path and session_id:
        hits = glob.glob(os.path.expanduser(f"~/.claude*/projects/*/{session_id}.jsonl"))
        path = hits[0] if hits else ""
    if not path:
        return text or ""
    for i in range(max(1, tries)):
        last = None
        try:
            with open(path, "rb") as f:
                f.seek(max(0, os.path.getsize(path) - 256 * 1024))
                for raw in f.read().decode("utf-8", "replace").splitlines():
                    if '"assistant"' not in raw:
                        continue
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    if d.get("type") == "assistant":
                        last = d
        except OSError:
            return text or ""
        msg = (last or {}).get("message") or {}
        if msg.get("model") == "<synthetic>":
            t = " ".join(str(x.get("text") or "") for x in (msg.get("content") or []) if isinstance(x, dict))
            try:
                age = time.time() - dt.datetime.fromisoformat(str(last.get("timestamp")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                age = 0
            if LIMIT_MODEL.search(t) and age < 600:
                return t
        if i + 1 < tries:
            time.sleep(pause)
    return text or ""


def quota_limit(tm, text="", transcript=""):
    """1.58 (14/10, dagli spunti della valutazione di Kaya): la sessione `tm` ha toccato il limite della quota (l'hook di
    StopFailure). Un evento quota_limit al telefono con l'ora del reset e le strade, una per tasto, che l'op
    quota_route esegue: other_account (handoff sull'altro account), at_reset (riprende da sola al reset).
    1.85 (25/10/2026): il limite di un modello («reached your Fable limit», dal testo dell'hook o dalla trascrizione)
    → window «model», other_model per primo (Opus, se il modello fermo non e' gia' Opus), at_reset solo se il messaggio
    dice l'ora; «pass» non si propone piu' (inutile al limite della quota, detto dal telefono). Uno per sessione e finestra di reset."""
    row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
    if not row:
        return 0
    acc = row.get("account") or ""
    mm = LIMIT_MODEL.search(limit_text(text, transcript, row.get("session_id") or ""))
    model = mm.group(1).strip() if mm else None
    if model:
        window, reset = "model", reset_from_text(mm.string)
        key = f"model:{model}:{reset or time.strftime('%Y-%m-%d %H')}"
    else:
        window, reset = quota_window(acc)
        key = reset
    p = rdir() / "quota-limits.json"
    seen = read_json(p, {})
    if seen.get(tm) == key:
        return 0
    seen[tm] = key
    write_json(p, seen)
    other = other_account(acc)
    alt = other_model() if model and not model.lower().startswith("opus") else None
    name = S.short_name(tm, prefixes())
    when = time.strftime("%H:%M", time.localtime(float(reset))) if reset else "?"
    if model:
        title = M("relay.event_quota_model_reset" if reset else "relay.event_quota_model", name=name, model=model, t=when)
        actions = (["other_model"] if alt else []) + (["other_account"] if other else []) + (["at_reset"] if reset else [])
    else:
        title = M("relay.event_quota_limit", name=name, window=M("guard.window_5h") if window == "5h" else M("guard.window_week"), t=when)
        actions = (["other_account"] if other else []) + ["at_reset"]
    attention_mark("quota_limit", name, name, title)
    emit("quota_limit", title, "", ref=name, account=acc, session=name,
         extra={"reset_at": int(reset) if reset else None, "window": window, "other_account": other,
                "other_model": alt[1] if alt else None, "actions": actions})
    # 14/10 (cambio automatico al limite): di notte, per un account in auto_switch.accounts, la strada «altro account»
    # parte da sola; di giorno resta la scelta col tasto
    A = CFG.get("auto_switch") or {}
    lo, hi = (list(A.get("night_hours") or [0, 7]) + [0, 7])[:2]
    if other and acc in (A.get("accounts") or []) and int(lo) <= time.localtime().tm_hour < int(hi):
        ok, text = quota_route({}, name, tm, "other_account")[:2]
        log(f"quota_limit {name}: notte, altro account in automatico → {'ok' if ok else 'ERR'} {text}")
        emit("quota_limit", M("relay.event_quota_auto", name=name, text=text), "", ref=name, account=acc, session=name)
    return 0


NEEDS_CHECK_EVERY_S = 60     # 1.80: la condizione di una richiesta si prova al massimo una volta al minuto
NEEDS_CHECK_TIMEOUT_S = 10
NEEDS_ORPHAN_S = 86400       # il file di una sessione sparita da piu' di un giorno va via


def _wake(tm, text):
    """Un `talk` staccato alla sessione (il bus non aspetta; chiusa → resta nella casella), come dopo «Approva»."""
    try:
        subprocess.Popen([CM_BIN, "talk", tm, text, "--no-wait"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def needs_check(cmd, cwd=None):
    """Il comando di `needs-you --until`: 0 = fatto. Mai piu' di NEEDS_CHECK_TIMEOUT_S; un errore vale «non ancora»."""
    try:
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        return subprocess.run(cmd, shell=True, cwd=cwd or None, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=NEEDS_CHECK_TIMEOUT_S, env=env).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def needs_you_of(rows, now=None):
    """1.68: {tmux: {reason, at, …}} dai file <state_dir>/needs-you/<session_id>.json che scrive `supervisor needs-you`
    (l'hook del prompt dopo li toglie). 1.80 (23/10/2026, «le richieste in attesa … scompaiano quando non ci sono
    piu'»): qui la vita della richiesta — scaduta (`expires_at`) → via, diario e avviso alla sessione; con `until` il
    comando si prova una volta al minuto e a 0 → via, diario e avviso; il file di una sessione che non c'e' piu' da
    un giorno → via."""
    d = Path(cm.expand(CFG["state_dir"])) / "needs-you"
    if not d.is_dir():
        return {}
    now = float(now or time.time())
    by_sid = {r.get("session_id"): (r.get("tmux") or r.get("name")) for r in rows if r.get("session_id") and r.get("status") != "dead"}
    out = {}
    for f in d.glob("*.json"):
        tm = by_sid.get(f.stem)
        if not tm:
            try:
                if now - f.stat().st_mtime > NEEDS_ORPHAN_S:
                    f.unlink(missing_ok=True)
                    ledger_write("needs-you-pruned", file=f.name)
            except OSError:
                pass
            continue
        rec = read_json(f, {})
        if not isinstance(rec, dict):
            continue
        reason = S.one_line(str(rec.get("reason") or ""))[:200]
        exp = float(rec.get("expires_at") or 0)
        if exp and now >= exp:
            f.unlink(missing_ok=True)
            ledger_write("needs-you-expired", session=tm, reason=reason)
            _wake(tm, M("relay.needs_you_expired", name=tm, reason=reason, when=time.strftime("%H:%M", time.localtime(now))))
            continue
        until = str(rec.get("until") or "")
        if until and now - float(rec.get("checked_at") or 0) >= NEEDS_CHECK_EVERY_S:
            rc = needs_check(until, rec.get("cwd"))
            if rc == 0:
                f.unlink(missing_ok=True)
                ledger_write("needs-you-done", session=tm, reason=reason, how="check")
                _wake(tm, M("relay.needs_you_done", name=tm, reason=reason, when=time.strftime("%H:%M", time.localtime(now))))
                continue
            rec["checked_at"], rec["last_rc"] = int(now), rc
            write_json(f, rec)
        out[tm] = rec
    return out


def attention_done(cmd, arg):
    """1.80 (23/10/2026): il tasto «Fatto» dell'app chiude una voce di «Serve te» che il relay non puo' verificare da
    solo (l'APK installato, un invito accettato, una cosa fatta a voce). {kind, ref (= arg), since}: needs_you → il
    file va via e la sessione riceve «fatto» in casella; turn_blocking → segnata chiusa (attention-dismissed.json,
    sessione → since) finche' il turno non cambia; turn_failed, quota_limit, task_red → tolte da attention.json."""
    kind = str(cmd.get("kind") or "").strip()
    ref = S.one_line(str(arg or cmd.get("ref") or "")).strip()[:120]
    when = time.strftime("%H:%M")
    if kind == "needs_you" and ref:
        rows = _json_cmd("sessions", "--json", "--no-screen") or []
        row = next((r for r in rows if (r.get("tmux") or r.get("name")) == ref and r.get("session_id")), None)
        f = Path(cm.expand(CFG["state_dir"])) / "needs-you" / f"{row['session_id']}.json" if row else None
        if not f or not f.exists():
            return False, M("relay.cmd_attention_unknown", kind=kind, ref=ref)
        reason = S.one_line(str((read_json(f, {}) or {}).get("reason") or ""))[:200]
        f.unlink(missing_ok=True)
        ledger_write("needs-you-done", session=ref, reason=reason, how="app", by=from_device(cmd))
        _wake(ref, M("relay.needs_you_app_done", person=M("tasks.person"), when=when, device=from_device(cmd), reason=reason))
        return True, M("relay.cmd_attention_done", kind=kind, ref=ref)
    if kind == "turn_blocking" and ref:
        p = rdir() / "attention-dismissed.json"
        d = read_json(p, {})
        d = d if isinstance(d, dict) else {}
        d[ref] = int(cmd.get("since") or time.time())
        write_json(p, d)
        ledger_write("attention-done", kind=kind, ref=ref, by=from_device(cmd))
        return True, M("relay.cmd_attention_done", kind=kind, ref=ref)
    if kind in ("turn_failed", "quota_limit", "task_red") and ref:
        attention_mark(kind, ref, ref, "", remove=True)
        ledger_write("attention-done", kind=kind, ref=ref, by=from_device(cmd))
        return True, M("relay.cmd_attention_done", kind=kind, ref=ref)
    return False, M("relay.cmd_attention_unknown", kind=kind or "?", ref=ref or "?")


REMIND_AFTER_S = (600, 1800)   # 1.68: promemoria di una voce di «Serve te» ancora aperta dopo 10 e dopo 30 minuti


def attention_reminders(full, now):
    """1.68 (14/10): per ogni voce di state.attention ancora aperta, un evento attention_reminder dopo 10 e dopo 30
    minuti, una volta ciascuno (reminders.json); le voci chiuse escono dal file."""
    items = full.get("attention") or []
    p = rdir() / "reminders.json"
    sent = read_json(p, {})
    live = {}
    for x in items:
        key = f"{x.get('kind')}|{x.get('ref')}|{x.get('since')}"
        if key not in sent:
            # vista ora per la prima volta (dopo un riavvio, o una voce nata gia' vecchia): le soglie gia' passate non
            # suonano tutte insieme, contano solo quelle che si passano da qui in poi
            live[key] = [a for a in REMIND_AFTER_S if now - float(x.get("since") or now) >= a]
            continue
        done = list(sent.get(key) or [])
        for after in REMIND_AFTER_S:
            if after not in done and now - float(x.get("since") or now) >= after:
                done.append(after)
                emit("attention_reminder", M("relay.event_attention_reminder", min=after // 60, title=S.one_line(x.get("title") or "")[:120]), "",
                     ref=x.get("ref"), session=x.get("session"), extra={"attention_kind": x.get("kind")})
        live[key] = done
    if live != sent:
        write_json(p, live)


def attention_mark(kind, session, ref, title, now=None, remove=False):
    """1.66 (14/10): le cose che aspettano la persona oltre alle domande — un turno fallito (1.61), una sessione ferma
    per la quota (1.58), un task rosso (1.57) — restano in attention.json finche' non si risolvono. `remove` toglie
    quella di quel tipo e riferimento (un task tornato verde)."""
    p = rdir() / "attention.json"
    items = [x for x in read_json(p, []) if isinstance(x, dict) and not (x.get("kind") == kind and x.get("ref") == ref)]
    if not remove:
        items.append({"kind": kind, "session": session, "ref": ref, "title": S.redact_preview(S.one_line(title))[:200], "since": int(now or time.time())})
    write_json(p, items)


def attention_open(rows, ledger):
    """1.66: le voci di attention.json ancora aperte. Un turno fallito e una quota finita si chiudono al primo prompt
    dopo (la sessione e' ripartita) o quando la sessione non c'e' piu'; un task rosso quando torna verde (attention_mark)."""
    p = rdir() / "attention.json"
    items = [x for x in read_json(p, []) if isinstance(x, dict)]
    if not items:
        failures_settle(set())   # anche senza voci: un evento rimasto aperto da prima (16/10, la master)
        return []
    sid_of = {S.short_name(r.get("name") or r.get("tmux") or "", prefixes()): r.get("session_id") or "" for r in rows if r.get("status") != "dead"}
    keep = []
    for x in items:
        if x.get("kind") in ("turn_failed", "quota_limit"):
            sid = sid_of.get(x.get("session") or "")
            if not sid or any(e.get("event") == "prompt" and e.get("session_id") == sid and S.epoch(e.get("ts")) > int(x.get("since") or 0) for e in ledger):
                continue
        keep.append(x)
    if keep != items:
        write_json(p, keep)
    failures_settle({x.get("ref") for x in keep if x.get("kind") == "turn_failed"})
    return keep


def failures_settle(open_refs, now=None):
    """16/10 (dal telefono, «master: turno fallito» con Continua sotto una risposta riuscita, tornato dopo tre
    Continua): l'app tiene aperto il turno fallito finche' turn_started non supera il ts dell'evento, ma turn_started
    e' valorizzato solo a turno in corso (contratto in cm-relay-state): a turno finito torna null e la voce riappare.
    Quando la voce di attention.json si chiude, l'evento turn_failed su /events viene riscritto senza continue_text
    (l'app ignora un turn_failed senza testo) e con `settled_at`. `open_refs` = le sessioni con un turno fallito
    ancora aperto. Si guarda events.json solo quando cambia lui o l'insieme delle voci aperte."""
    p = local_dir() / "events.json"
    try:
        stamp = f"{p.stat().st_mtime_ns}|{','.join(sorted(r for r in open_refs if r))}"
    except OSError:
        return 0
    sp = rdir() / "failures-settled"
    try:
        if sp.read_text() == stamp:
            return 0
    except OSError:
        pass
    now = int(now or time.time())
    done = [dict(e, continue_text=None, settled_at=now) for e in read_json(p, {}).values()
            if e.get("kind") == "turn_failed" and (e.get("continue_text") or "").strip() and e.get("ref") not in open_refs]
    if done:
        k = None if local_only() else key()
        if k:
            try:
                rtdb("PATCH", "events", {e["key"]: C.encrypt(e, k) for e in done}, {"print": "silent"})
            except (urllib.error.URLError, OSError, ValueError) as ex:
                log(f"turn_failed chiusi: PATCH FAILED: {ex}")
                return 0   # si riprova al prossimo stato
        local_events_add(done, now)
        log(f"turn_failed chiusi: {', '.join(sorted({str(e.get('ref')) for e in done}))}")
        stamp = f"{p.stat().st_mtime_ns}|{stamp.split('|', 1)[1]}"
    sp.write_text(stamp)
    return len(done)


INTERRUPT_WAIT_S = 8   # 16/10: quanto lo Stop aspetta che parta il turno di un prompt consegnato da meno di un minuto
TURN_FAILED_GAP_S = 60   # 1.61: un evento per sessione al minuto, anche se Claude Code riprova e fallisce di fila


def turn_failed(tm, error):
    """1.61 (14/10, dall'analisi dei concorrenti): un turno finito in errore (529 overloaded, 500, autenticazione...)
    lasciava la sessione ferma come se avesse finito. Un evento turn_failed al telefono: il motivo in una riga, senza
    segreti (come le anteprime della 1.56), e `continue_text`, il testo che il tasto «continua» manda con l'op prompt.
    La quota ha il suo evento (1.58)."""
    p = rdir() / "turn-failed.json"
    seen = read_json(p, {})
    now = time.time()
    if now - float(seen.get(tm) or 0) < TURN_FAILED_GAP_S:
        return 0
    seen[tm] = now
    write_json(p, {k: v for k, v in seen.items() if now - float(v or 0) < 86400})
    row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None) or {}
    name = S.short_name(tm, prefixes())
    reason = S.redact_preview(S.short_of(error or "?", 160))   # 22/10: anche il testo dell'errore mostrato in chat
    attention_mark("turn_failed", name, name, M("relay.event_turn_failed", name=name, reason=reason))
    emit("turn_failed", M("relay.event_turn_failed", name=name, reason=reason), "", ref=name, account=row.get("account"), session=name,
         extra={"reason": reason, "continue_text": M("relay.continue_text")})
    return 0


PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def project_parents():
    """1.64: le cartelle madri ammesse per un progetto nuovo, quelle dei progetti pubblicati (inventory): una cartella
    creata li' compare subito fra i progetti."""
    return sorted({str(Path(p["path"]).parent) for p in inventory()})


def project_new(cmd, parent, name):
    """1.64 (14/10, dall'analisi dei concorrenti): una cartella nuova dal telefono, mai creata senza conferma (regola 8
    del kernel: mai creare cartelle per un refuso). `arg` = la cartella madre, una di project_parents(); `text` = il
    nome. Senza `confirm: true` non crea niente e risponde ok: false con il percorso completo da mostrare; con
    `confirm: true` crea la cartella e lancia la sessione (account dalla cartella, come launch), con il primo
    messaggio se c'e' `prompt`."""
    name = str(name or "").strip()
    if not PROJECT_NAME.fullmatch(name):
        return False, M("relay.cmd_project_bad_name", name=name or "?")
    real = os.path.realpath(str(parent or ""))
    if real not in {os.path.realpath(x) for x in project_parents()}:
        return False, M("relay.cmd_project_bad_parent", path=str(parent or "?"))
    path = Path(real) / name
    if path.exists():
        return False, M("relay.cmd_project_exists", path=str(path))
    if cmd.get("confirm") is not True:
        return False, M("relay.cmd_project_confirm", path=str(path))
    path.mkdir()
    rc, out = run_cm("launch", str(path), "--window")
    if rc != 0:
        return False, (out.splitlines() or ["launch failed"])[0]
    new = [r.get("tmux") or r.get("name") for r in (_json_cmd("sessions", "--json", "--no-screen") or [])
           if r.get("cwd") and os.path.realpath(r["cwd"]) == str(path)]
    extra = {"session": S.short_name(new[0], prefixes())} if new else {}
    prompt = " ".join(str(cmd.get("prompt") or "").split())
    if prompt and new:
        relay_talk(new[0], prefix_for(cmd, new[0]) + " " + prompt, "--no-wait")
    return True, M("relay.cmd_project_created", path=str(path), account=account_for_path(path)), extra


def quick_handoff(row):
    """1.58: un passaggio di consegne scritto dal relay, senza modello (la sessione ferma per la quota non puo' scriverlo):
    cartella, conversazione, le ultime richieste della persona, l'ultimo messaggio, il «prossimo» del recap. Il file
    in <relay.dir>/handoffs/; torna il percorso."""
    name = S.short_name(row.get("name") or row.get("tmux") or "?", prefixes())
    d = rdir() / "handoffs"
    d.mkdir(parents=True, exist_ok=True)
    asks = []
    path = core.transcript_of(row)
    if path:
        size = os.path.getsize(path)
        asks = [e["text"] for e in core.transcript_entries(path, max(0, size - 2 * 1024 * 1024), chat_only=True) if e["role"] == "user"][-3:]
    nx, _ = next_dated(row.get("cwd")) if row.get("cwd") else ("", None)
    lines = [f"# {M('relay.handoff_title', name=name)}", "", f"- cwd: {row.get('cwd') or '?'}", f"- session_id: {row.get('session_id') or '?'}",
             f"- account: {row.get('account') or '?'}", "", f"## {M('relay.handoff_asks')}", ""]
    lines += [f"- {S.one_line(a)[:600]}" for a in asks] or ["-"]
    lines += ["", f"## {M('relay.handoff_last')}", "", last_message(row, 3000) or "-"]
    if nx:
        lines += ["", f"## {M('relay.handoff_next')}", "", nx]
    f = d / f"{name}-{int(time.time())}.md"
    f.write_text("\n".join(lines) + "\n")
    return f


def quota_route(cmd, session, tm, arg):
    """1.58: una delle strade proposte da quota_limit. arg: «other_model» (1.85) | «other_account» | «at_reset» |
    «pass:<sessione>» (non piu' proposta dalla 1.85, accettata ancora)."""
    a = str(arg or "").strip()
    row = next((r for r in (_json_cmd("sessions", "--json", "--no-screen") or []) if (r.get("tmux") or r.get("name")) == tm), None)
    if not row:
        return False, M("relay.cmd_not_running", name=session)
    acc = row.get("account") or ""
    if a == "other_model":
        # 1.85: lo stesso percorso di `supervisor model` (ferma al prompt, selettore, conferma nel riquadro), poi «continua»
        mid, label = other_model()
        rc, out = run_cm("model", tm, mid)
        if rc != 0:
            return False, (out.splitlines() or ["model failed"])[0]
        push_async()
        rc, out = relay_talk(tm, prefix_for(cmd, tm) + " " + M("relay.quota_model_prompt"), "--no-wait")
        if rc != 0:
            return False, (out.splitlines() or ["talk failed"])[0]
        return True, M("relay.cmd_quota_model", name=session, model=label)
    if a == "at_reset":
        window, reset = quota_window(acc)
        if not reset:
            return False, M("relay.cmd_quota_no_reset", account=acc)
        gp = Path(cm.expand(CFG["state_dir"])) / "guard.json"
        g = read_json(gp, {})
        s_ = g.setdefault(acc, {})
        s_["resume_at"] = s_.get("resume_at") or reset
        s_["resume_names"] = sorted(set(s_.get("resume_names") or []) | {tm})
        write_json(gp, g)
        return True, M("relay.cmd_resume_at_reset", name=session, t=time.strftime("%H:%M", time.localtime(float(reset))))
    if a == "other_account":
        other = other_account(acc)
        if not other:
            return False, M("relay.cmd_quota_no_other")
        f = quick_handoff(row)
        before = {r.get("tmux") or r.get("name") for r in (_json_cmd("sessions", "--json", "--no-screen") or [])}
        rc, out = run_cm("launch", row.get("cwd") or "", "--window", "--account", other)
        if rc != 0:
            return False, (out.splitlines() or ["launch failed"])[0]
        here = os.path.realpath(row.get("cwd") or "")
        new = [r.get("tmux") or r.get("name") for r in (_json_cmd("sessions", "--json", "--no-screen") or [])
               if (r.get("tmux") or r.get("name")) not in before and r.get("cwd") and os.path.realpath(r["cwd"]) == here]
        if not new:
            return False, M("relay.cmd_launch_no_session", name=session)
        rc, out = relay_talk(new[0], prefix_for(cmd, new[0]) + " " + M("relay.handoff_prompt", name=session, path=str(f)), "--no-wait")
        return (rc == 0), (M("relay.cmd_quota_other", name=S.short_name(new[0], prefixes()), account=other) if rc == 0 else (out.splitlines() or ["talk failed"])[0]), \
            ({"session": S.short_name(new[0], prefixes())} if rc == 0 else {})
    if a.startswith("pass:"):
        target = tmux_of(a[5:].strip())
        if not target or not is_live(target) or target == tm:
            return False, M("relay.cmd_not_running", name=a[5:].strip() or "?")
        f = quick_handoff(row)
        rc, out = relay_talk(target, prefix_for(cmd, target) + " " + M("relay.handoff_prompt", name=session, path=str(f)), "--no-wait")
        return (rc == 0), (M("relay.cmd_quota_passed", name=a[5:].strip()) if rc == 0 else (out.splitlines() or ["talk failed"])[0])
    return False, M("relay.cmd_quota_bad", arg=a or "?")


def task_check(tm):
    """1.57 (14/10, dagli spunti della valutazione di Kaya): la sessione `tm` si e' fermata. Per ogni suo task running
    si riesegue il controllo (`task check`, senza cambiare stato) e un evento task_check va al telefono: verde o rosso,
    il titolo del task, la coda dell'uscita. Solo quando l'esito cambia dall'ultima volta (task-checks.json): una
    sessione si ferma a ogni turno e il telefono non deve ricevere lo stesso verde dieci volte."""
    tasks = [t for t in (_json_cmd("task", "list", "--json") or []) if isinstance(t, dict) and t.get("state") == "running" and t.get("session") == tm]
    if not tasks:
        return 0
    p = rdir() / "task-checks.json"
    seen = read_json(p, {})
    name = S.short_name(tm, prefixes())
    for t in tasks:
        try:
            rc, out = run_cm("task", "check", t["id"], "--json", timeout=300)
            res = json.loads(out.splitlines()[-1]) if out.strip().startswith("{") else {"green": False, "tail": out}
        except (subprocess.TimeoutExpired, ValueError):
            res = {"green": False, "tail": "timeout"}
        green = bool(res.get("green"))
        if seen.get(t["id"]) == green:
            continue
        seen[t["id"]] = green
        key_ = "relay.event_task_check_green" if green else "relay.event_task_check_red"
        attention_mark("task_red", name, t["id"], M(key_, task=t["id"]), remove=green)
        body = S.one_line(t.get("title") or "") + ("\n" + "\n".join(str(res.get("tail") or "").splitlines()[-3:]) if not green and res.get("tail") else "")
        emit("task_check", M(key_, task=t["id"]), body, ref=t["id"], session=name, extra={"ok": green})   # 1.78: l'icona la sceglie l'app da ok
        log(f"task_check {t['id']} ({name}): {'verde' if green else 'rosso'}")
    write_json(p, seen)
    return 0


# ------------------------------------------------------------------ CLI
def main(argv):
    cmd = argv[0] if argv else ""
    rest = argv[1:]
    if cmd == "push":
        if "--dry-run" in rest:
            try:
                push(dry_run=True)
            except (subprocess.TimeoutExpired, OSError) as e:
                print(str(e), file=sys.stderr); return 1
            return 0
        if not R.get("enabled"):
            print(M("relay.disabled")); return 2
        origin = rest[rest.index("--origin") + 1] if "--origin" in rest and rest.index("--origin") + 1 < len(rest) else ""
        if "--delayed" in rest:
            return push_delayed(rest[rest.index("--delayed") + 1], origin or "?")
        if "--async" in rest:
            push_async(origin or "cron"); return 0
        try:
            st = push()
        except (RelayError, urllib.error.URLError, OSError, ValueError) as e:
            print(f"relay push: {e}", file=sys.stderr); log(f"push FAILED: {e}"); return 1
        print(M("relay.pushed", n=len(st["sessions"])))
        return 0
    if cmd == "stats":
        return stats(rest)
    if cmd == "published-refresh":   # 1.75: lanciato da published_cached, staccato
        return published_refresh()
    if cmd == "turn-failed":   # 1.61: lanciato dall'hook di StopFailure, in background
        return turn_failed(rest[0], rest[1] if len(rest) > 1 else "") if rest and R.get("enabled") else 0
    if cmd == "quota-limit":   # 1.58: lanciato dall'hook di StopFailure, in background
        return quota_limit(rest[0], rest[1] if len(rest) > 1 else "", rest[2] if len(rest) > 2 else "") if rest and R.get("enabled") else 0
    if cmd == "task-check":   # 1.57: lanciato dall'hook di Stop, in background
        return task_check(rest[0]) if rest and R.get("enabled") else 0
    if cmd == "setup":   # il progetto Firebase, guidato (R2, 24/09): la sua CLI, non cryptography ne' crontab
        return _load("cm-relay-setup").main(rest)
    if cmd == "pair" and "--revoke" in rest:
        # 24/10 (la master, per il dispositivo «Studio»): la revoca dal PC, la stessa dell'op unpair dell'app
        i = rest.index("--revoke")
        if i + 1 >= len(rest) or len(rest) != 2:
            print(M("relay.pair_usage"), file=sys.stderr); return 2
        try:
            ok, text = unpair(rest[i + 1])
        except (RelayError, urllib.error.URLError, OSError, ValueError) as e:
            print(f"relay pair --revoke: {e}", file=sys.stderr); return 1
        print(text, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    if cmd == "pair":
        # 04/10 (dall'app): `pair --help` avviava un pairing vero. L'aiuto, e un argomento sconosciuto, prima di tutto
        known, i = {"--text", "--add"}, 0
        while i < len(rest):
            if rest[i] == "--timeout" and i + 1 < len(rest):
                i += 2; continue
            if rest[i] in ("-h", "--help") or rest[i] not in known:
                print(M("relay.pair_usage"), file=sys.stdout if rest[i] in ("-h", "--help") else sys.stderr)
                return 0 if rest[i] in ("-h", "--help") else 2
            i += 1
    if cmd in ("pair", "install"):
        # prima di chiedere o scrivere qualcosa: le dipendenze che finora si scoprivano solo come
        # errore (ImportError di cryptography, crontab assente). Esce 5 con il comando da lanciare.
        if not R.get("enabled"):
            print(M("relay.disabled")); return 2
        missing = cm.relay_deps_missing(cm.Machine(), os.environ.get("CM_CRONTAB_CMD", "crontab"))
        if missing:
            for dep in missing:
                print(M(f"relay.dep_{dep}_missing"), file=sys.stderr)
            return 5
    if cmd == "pair":
        tmo = float(rest[rest.index("--timeout") + 1]) if "--timeout" in rest else None
        try:
            return pair(tmo, text="--text" in rest, add="--add" in rest)
        except (RelayError, urllib.error.URLError, OSError, ValueError) as e:
            print(f"relay pair: {e}", file=sys.stderr); return 1
    if cmd == "serve":
        return serve()
    if cmd == "web":
        return web(rest)
    if cmd == "ensure":
        rc = ensure()
        try:   # 1.63: dal cron ogni minuto, i messaggi programmati per le sessioni ferme (senza voci in coda: niente)
            qd = Path(cm.expand(CFG["state_dir"])) / "queue"
            if qd.is_dir() and any(qd.iterdir()):
                run_cm("queue", "--due", timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return rc
    if cmd == "status":
        return status()
    if cmd == "install":
        return install()
    if cmd == "uninstall":
        return uninstall()
    if cmd == "off":
        return off()
    print(M("relay.usage"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
