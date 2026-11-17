#!/usr/bin/env python3
"""supervisor recap — il diario della giornata dal ledger degli hook (N7).

  supervisor recap [--date YYYY-MM-DD | --since ORE] [--send] [--full] [--json]
  supervisor recap install | uninstall | status     cron serale (recap.cron_time, default 20:00)

Legge `<state_dir>/ledger.jsonl` (start, stop, waiting, end; lo scrive `cm-hook.py`) e il registro
peer di ogni account (chi è VIVO adesso, con il suo link Remote Control) e racconta la giornata
PER PROGETTO, non per sessione: i riavvii della stessa cartella si accorpano (turni sommati,
«riavviata N volte»). Tre gruppi, nell'ordine in cui ti servono: ⏳ ferme su una domanda (con lo
strumento e da quando), ● vive, ✓ chiuse oggi; le chiuse con zero turni non compaiono. Ogni
progetto vivo ha il link per aprirlo. L'ultimo messaggio dell'assistente si cita solo dove aiuta
(`recap.last`: none | short | full; short = solo per le ferme, 60 caratteri, mai righe con
indirizzi email o intestazioni di lettera). `--full` è il vecchio elenco per sessione.

Con --send lo manda su Telegram alle chat di `allowFrom` (stesso bot del plugin; sendMessage non
confligge col polling, solo getUpdates è esclusivo) in HTML: i link non occupano spazio.

Il *quanto è costato* non sta qui: sono le ricevute di fable-director. Qui c'è il *cosa è successo*.

Tempo massimo (15/11/2026, supervisor-recap-appeso-15-11: `supervisor diary` restava muto per piu' di 100 secondi, e
si fermava a mano): il recap non stampa niente finche' non ha il riassunto del modello, un `claude -p` che aveva 120
secondi, non veniva ricordato quando scadeva e veniva richiamato una seconda volta per docs/recap.md. Ora: il comando
intero ha `recap.max_s` secondi (150; 0 = senza limite), e allo scadere esce con 124 e una riga che dice a che punto
era; il riassunto ha `recap.summary_timeout_s` (60) e mai piu' di quanto resta, gira con l'input chiuso e in un gruppo
suo, ucciso intero allo scadere; se non arriva il recap esce lo stesso con l'ultimo messaggio di ogni progetto, lo
dice su stderr, e non richiede il modello per SUMMARY_RETRY_S (mezz'ora) ne' una seconda volta nello stesso giro.
Misurato sul Chromebook il 15/11: il solo avvio di `claude -p` costa 17 secondi, e il riassunto di quel giorno passava
i 120. Per questo il modello parte senza server MCP, strumenti, comandi slash e sessione su disco (SUMMARY_LEAN; se la
CLI non li conosce si riprova senza), e il recap con --send, quello del cron che nessuno aspetta, ha piu' tempo:
`recap.send_max_s` (420) per il comando e tutto quello che resta per il riassunto, anche dopo uno non arrivato. Il
riassunto che arriva li' resta nella cache del giorno, e il recap chiesto a mano dopo lo trova subito.
"""
import argparse
import datetime as dt
import html
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import OrderedDict
from pathlib import Path


def _cm_proc():
    """cm-proc: processi e misure per Linux e macOS (11/11, supervisor-macos-11-11)."""
    import importlib.util as _u
    spec = _u.spec_from_file_location("cm_proc", Path(__file__).resolve().parent / "cm-proc.py")
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
D = CFG["recap"]
MAX_S = 150               # recap.max_s: il tempo massimo del comando intero
SUMMARY_TIMEOUT_S = 60    # recap.summary_timeout_s: quello del riassunto del modello
SUMMARY_RESERVE_S = 15    # cio' che resta al recap dopo il riassunto (scrivere, mandare)
SUMMARY_RETRY_S = 1800    # dopo un riassunto non arrivato, per quanto non si richiede
SEND_MAX_S = 420          # recap.send_max_s: il tempo massimo con --send (il cron: nessuno aspetta, il riassunto serve)
# il modello del riassunto non usa strumenti: senza server MCP, strumenti, comandi slash e sessione su disco parte prima
SUMMARY_LEAN = ["--strict-mcp-config", "--tools", "", "--disable-slash-commands", "--no-session-persistence"]
RUN = {"t0": time.time(), "phase": "start", "asked": set()}


class TooLong(BaseException):
    """Il tempo massimo e' scaduto. BaseException: nessun `except Exception` lungo la strada lo deve inghiottire."""


def phase(name):
    RUN["phase"] = name


def max_s():
    key, default = ("send_max_s", SEND_MAX_S) if RUN.get("send") else ("max_s", MAX_S)
    try:
        return max(0, int(D.get(key, default)))
    except (TypeError, ValueError):
        return default


def run_capped(cmd, timeout, **kw):
    """L'uscita standard di `cmd`, o None se non finisce in `timeout` secondi. L'input e' chiuso (un `claude -p` con un
    input aperto lo aspetta), il comando ha un gruppo suo e allo scadere, o se il recap viene fermato, muore tutto."""
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                         errors="replace", start_new_session=True, **kw)
    try:
        out, _ = p.communicate(timeout=timeout)
        return out
    except subprocess.TimeoutExpired:
        return None
    finally:
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                p.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass


def ledger_path():
    return Path(cm.expand(CFG["state_dir"])) / "ledger.jsonl"


def read_events(since=None, day=None):
    """Gli eventi del ledger nell'intervallo: `day` (data locale) oppure dalle ultime `since` ore."""
    p = ledger_path()
    if not p.is_file():
        return []
    out = []
    homes = {}
    in_scope = cm.scope_filter(CFG)   # 17/11: le cartelle ammesse una volta per il ciclo
    for line in p.read_text().splitlines():
        try:
            e = json.loads(line)
            ts = dt.datetime.fromisoformat(e["ts"])
        except (ValueError, KeyError, TypeError):
            continue
        # la cartella di una sessione e' quella in cui NASCE (l'evento start), anche fuori dall'intervallo: gli hook
        # registrano la cartella corrente del momento, e una sessione che si sposta con `cd` sembrava un altro progetto
        if e.get("event") == "start" and e.get("session_id") and e.get("cwd"):
            homes[e["session_id"]] = e["cwd"]
        # 12/11 (supervisor-ambito-sessioni-12-11): una sessione fuori dall'ambito non entra nel recap
        if not in_scope(homes.get(e.get("session_id") or "") or e.get("cwd") or "", e.get("host")):
            continue
        if day is not None and ts.date() != day:
            continue
        if since is not None and ts < since:
            continue
        e["_ts"] = ts
        out.append(e)
    # 17/09/2026: la sessione della radice aveva lavorato dodici turni con la shell in <plugin>/scripts; il recap l'ha
    # presa per un progetto «scripts» e ci ha scritto docs/recap.md — dentro la cartella che la release impacchetta
    for e in out:
        home = homes.get(e.get("session_id") or "")
        if home and e.get("cwd") != home:
            e["cwd_seen"], e["cwd"] = e.get("cwd"), home
    return out


def live_registry():
    """sessionId → {bridge, status} per ogni sessione VIVA nel registro peer di ogni account
    (`<config_dir>/sessions/<pid>.json`); un file con pid morto non conta."""
    out = {}
    seen = set()
    for a in CFG["accounts"].values():
        d = Path(cm.expand(a.get("config_dir", ""))) / "sessions"
        rd = os.path.realpath(d)
        if rd in seen or not d.is_dir():   # symlink fra account (E1): una volta sola
            continue
        seen.add(rd)
        for f in d.glob("*.json"):
            try:
                r = json.loads(f.read_text())
                pid = int(r.get("pid") or f.stem.split(".")[0])
            except (ValueError, OSError):
                continue
            if not _cm_proc().exists(pid) and not os.environ.get("CM_DIARY_FAKE_PROC"):
                continue
            sid = r.get("sessionId")
            if sid:
                out[sid] = {"bridge": r.get("bridgeSessionId") or "", "status": r.get("status") or "", "name": r.get("name") or ""}
    return out


def project_name(cwd):
    root = cm.expand(CFG["workspace"]["root"]).rstrip("/")
    if cwd.rstrip("/") == root:
        return CFG["workspace"]["root_session_name"]
    return os.path.basename(cwd.rstrip("/")) or cwd


def summarize(events):
    """Per sessione (nell'ordine del primo evento): progetto, account, inizio, fine, turni, attese, ultimo."""
    sessions = OrderedDict()
    for e in events:
        sid = e.get("session_id") or f"pid-{e.get('pid')}"
        s = sessions.setdefault(sid, {"id": sid, "project": project_name(e.get("cwd", "")), "cwd": e.get("cwd", ""),
                                      "account": e.get("account", ""), "start": None, "end": None, "turns": 0,
                                      "waiting": [], "last": "", "last_ts": None, "alive": True, "first": e["_ts"], "lasts": []})
        ev = e.get("event")
        t = e["_ts"]
        if ev == "start":
            if s["start"] is None or t < s["start"]:
                s["start"] = t
            s["alive"] = True
            s["end"] = None
        elif ev == "end":
            s["end"] = t
            s["alive"] = False
        elif ev == "stop":
            s["turns"] += 1
            if (e.get("last") or "").strip():
                s["lasts"].append((e.get("last") or "").strip())
            if s["last_ts"] is None or t >= s["last_ts"]:
                s["last"] = (e.get("last") or "").strip()
                s["last_ts"] = t
            s["waiting_open"] = False
        elif ev == "waiting":
            s["waiting"].append((t, e.get("tool") or "?"))
            s["waiting_open"] = True
    return list(sessions.values())


def is_noise(cwd):
    """Sessioni che non sono lavoro: la cartella di stato (il `claude -p` di questo diario ci gira
    dentro e gli hook lo registrano), le cartelle nascoste, le excluded_dirs del workspace."""
    c = cwd.rstrip("/")
    if not c or c == cm.expand(CFG["state_dir"]).rstrip("/"):
        return True
    base = os.path.basename(c)
    return base.startswith(".") or base in set(CFG["workspace"].get("excluded_dirs") or [])


def group_projects(sessions, live):
    """Le sessioni della stessa cartella e account diventano UN progetto: riavvii accorpati."""
    groups = OrderedDict()
    for s in sessions:
        if is_noise(s["cwd"]):
            continue
        key = (s["account"], s["cwd"])
        g = groups.setdefault(key, {"project": s["project"], "account": s["account"], "cwd": s["cwd"],
                                    "start": None, "end": None, "turns": 0, "waiting": [], "sessions": 0,
                                    "alive": False, "waiting_now": None, "link": "", "last": "", "last_ts": None,
                                    "born_before": False, "name": "", "lasts": []})
        g["sessions"] += 1
        g["turns"] += s["turns"]
        g["lasts"] += s["lasts"]
        g["waiting"] += s["waiting"]
        if s["start"] is None:
            g["born_before"] = True
        elif g["start"] is None or s["start"] < g["start"]:
            g["start"] = s["start"]
        if s["end"] and (g["end"] is None or s["end"] > g["end"]):
            g["end"] = s["end"]
        if s["last_ts"] and (g["last_ts"] is None or s["last_ts"] >= g["last_ts"]):
            g["last"], g["last_ts"] = s["last"], s["last_ts"]
        reg = live.get(s["id"])
        alive = reg is not None if live is not None else s["alive"]
        if alive:
            g["alive"] = True
            g["link"] = (reg or {}).get("bridge") or g["link"]
            g["name"] = (reg or {}).get("name") or g.get("name", "")
            waiting_now = (reg or {}).get("status") == "waiting" or s.get("waiting_open")
            if waiting_now and s["waiting"]:
                g["waiting_now"] = s["waiting"][-1]
    for g in groups.values():
        if g["alive"]:
            g["end"] = None
        elif g["end"] is None:
            # finita senza evento end (uccisa, riavvio della macchina): l'ultima cosa che ha fatto
            g["end"] = g["last_ts"] or max((s["first"] for s in sessions if (s["account"], s["cwd"]) == (g["account"], g["cwd"])), default=None)
    return list(groups.values())


def tab_icons():
    """Le icone VERE delle schede del Terminale (ChromeOS + chrome-bridge): etichetta → emoji.
    Vuoto altrove o se il ponte non risponde: si ripiega sul registro dei colori."""
    cli = cm.expand(CFG["tile"].get("chrome_bridge_cli") or "")
    if not cli or CFG["terminal"].get("backend") != "chromeos" or not os.path.isfile(cli):
        return {}
    try:
        runner = [sys.executable, cli] if cli.endswith(".py") else ["node", cli]
        phase("icons")
        tabs = json.loads(run_capped(runner + ["get_tabs", "--format", "json"], 20) or "")
    except (OSError, ValueError):
        return {}
    out = {}
    for t in tabs if isinstance(tabs, list) else tabs.get("tabs", []):
        title = (t.get("title") or "").strip()
        if not title or " " not in title or not title.startswith(("\U0001F534", "\U0001F7E0", "\U0001F7E1", "\U0001F7E2", "\U0001F535", "\U0001F7E3", "\u26AA", "\U0001F7E5", "\U0001F7E7", "\U0001F7E8", "\U0001F7E9", "\U0001F7E6", "\U0001F7EA", "\u2B1C")):
            continue
        icon, _, label = title.partition(" ")
        out.setdefault(label.strip(), icon)
    return out


def color_registry():
    """nome tmux → indice di colore (tabs.color_registry: `nome<TAB>indice`)."""
    try:
        rows = Path(cm.expand(CFG["tabs"]["color_registry"])).read_text().splitlines()
    except OSError:
        return {}
    out = {}
    for r in rows:
        n, _, i = r.partition("\t")
        if n and i.strip().isdigit():
            out[n] = int(i)
    return out


def icon_for(g, icons, registry):
    """L'icona della scheda: forma = account, colore = quella vera se la sessione è viva
    (scheda o registro dei colori), altrimenti il neutro della forma."""
    acc = CFG["accounts"].get(g["account"]) or {}
    shape = acc.get("shape") or "circle"
    pal = CFG["tabs"]["colors"].get(shape) or ["●"]
    if g["alive"] and g.get("name"):
        pfx = acc.get("tmux_prefix") or ""
        label = g["name"][len(pfx):] if pfx and g["name"].startswith(pfx) else g["name"]
        if label in icons:
            return icons[label]
        if g["name"] in registry:
            return pal[registry[g["name"]] % len(pal)]
    return pal[-1]


def summaries(groups, label):
    """Per progetto, dalla giornata intera: COSA È STATO FATTO e il PROSSIMO PASSO, da TUTTI i
    messaggi di fine turno della sessione. Un modello economico (`recap.summary_model`) legge i
    messaggi e risponde con un JSON {progetto: {"fatto": frase, "prossimo": frase}}. Cache per
    giorno in <state_dir>/recap-summaries/<label>.json (chiave: progetto + numero di messaggi), così
    il recap delle 20:00 e un /recap dopo non ripagano. Senza modello (recap.summary = last, claude
    assente, errore) si ripiega sull'ultimo messaggio, senza «prossimo». Ritorna id(g) → (fatto, prossimo)."""
    cap = int(D.get("closed_chars", 90))
    out = {id(g): (quotable(g["last"], cap), "") for g in groups}
    if str(D.get("summary", "model")) != "model":
        return out
    todo = {g["project"]: g for g in groups if len(g["lasts"]) >= 1}
    if not todo:
        return out
    cache_p = Path(cm.expand(CFG["state_dir"])) / "recap-summaries" / (re.sub(r"[^0-9A-Za-z-]", "_", label) + ".json")
    try:
        cache = json.loads(cache_p.read_text())
    except (OSError, ValueError):
        cache = {}

    def unpack(v):   # v1: stringa (solo «fatto»); v2: {"fatto","prossimo"}; {"failed_at"}: non arrivato
        if isinstance(v, dict):
            return (str(v.get("fatto") or ""), str(v.get("prossimo") or ""))
        return (str(v or ""), "")

    def clip(t, n):
        t = (t or "").strip().rstrip(".")
        return t if len(t) <= n else t[:n] + "…"

    now = time.time()

    def failed_lately(v):   # 15/11: un riassunto non arrivato si ricorda, e per mezz'ora non si richiede
        return isinstance(v, dict) and v.get("failed_at") and now - float(v["failed_at"]) < SUMMARY_RETRY_S

    missing = {}
    for name, g in todo.items():
        key = f"{name}|{g['account']}|{len(g['lasts'])}"
        v = cache.get(key)
        if key in cache and not (isinstance(v, dict) and v.get("failed_at") and (RUN.get("send") or not failed_lately(v))):
            done, nxt = unpack(v)   # anche vuoto (il modello non ha risposto per questo nome): niente nuove chiamate
            if done:
                out[id(g)] = (done, nxt)
        elif key not in RUN["asked"]:   # nello stesso giro il modello si chiede una volta sola
            missing[name] = g
    if missing:
        lang = "italiano" if CFG.get("language") == "it" else "English"
        payload = {n: [l[:300] for l in g["lasts"][-12:]] for n, g in missing.items()}
        prompt = (f"Per ogni progetto qui sotto hai i messaggi di fine turno di una sessione di lavoro di oggi, in ordine. "
                  f"Per ciascuno scrivi in {lang}, senza markdown: \"fatto\" = UNA frase (max {cap} caratteri) su cosa è stato "
                  f"fatto nella giornata (il risultato, non il processo; niente saluti, niente «la sessione ha»); "
                  f"\"prossimo\" = il prossimo passo che i messaggi lasciano intendere (max {cap} caratteri), o stringa vuota se "
                  f"il lavoro è concluso o non si capisce. Rispondi SOLO con un oggetto JSON "
                  f"{{\"nome progetto\": {{\"fatto\": \"…\", \"prossimo\": \"…\"}}}}, stesse chiavi dell'input.\n\n"
                  + json.dumps(payload, ensure_ascii=False))
        claude = cm.claude_bin()   # dal cron il PATH non ha ~/.local/bin (14/09: riassunti vuoti dal 12/09)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}   # account di default (T68)
        try:
            limit = int(D.get("summary_timeout_s", SUMMARY_TIMEOUT_S))
        except (TypeError, ValueError):
            limit = SUMMARY_TIMEOUT_S
        def left():
            return int(max_s() - (time.time() - RUN["t0"]) - SUMMARY_RESERVE_S)

        if RUN.get("armed"):   # mai piu' di quanto resta al comando intero, meno il tempo per scrivere e mandare
            limit = max(1, left() if RUN.get("send") else min(limit, left()))
        phase("summary")
        RUN["asked"].update(f"{n}|{g['account']}|{len(g['lasts'])}" for n, g in missing.items())
        raw, got = None, {}
        try:
            t_ask = time.time()
            for extra in (SUMMARY_LEAN, []):
                wait = limit - int(time.time() - t_ask)
                if wait < 1:
                    break
                raw = run_capped([claude, "-p", prompt, "--model", str(D.get("summary_model", "haiku")), "--max-turns", "1"] + extra, wait,
                                 env=env, cwd=str(Path(cm.expand(CFG["state_dir"]))))
                m = re.search(r"\{.*\}", raw or "", re.S)
                if raw is None or m:   # scaduto, o una risposta: basta. Uscita senza JSON (una CLI che non conosce le
                    break              # opzioni leggere esce subito con un errore): si riprova senza
            got = json.loads(m.group(0)) if m else {}
        except (OSError, ValueError):
            got = {}
        if not isinstance(got, dict):
            got = {}
        if raw is None:   # non e' arrivato in tempo: il recap esce lo stesso, con l'ultimo messaggio di ogni progetto
            print(M("recap.summary_timeout", n=limit, min=SUMMARY_RETRY_S // 60), file=sys.stderr)
        for name, g in missing.items():
            done, nxt = unpack(got.get(name))
            key = f"{name}|{g['account']}|{len(g['lasts'])}"
            if done.strip():
                entry = {"fatto": clip(done, cap), "prossimo": clip(nxt, cap)}
                out[id(g)] = (entry["fatto"], entry["prossimo"])
                cache[key] = entry
            elif got:
                cache[key] = ""      # risposta arrivata ma senza questo nome: si ripiega e non si richiede
            elif raw is None:
                cache[key] = {"failed_at": int(now)}
        try:
            cache_p.parent.mkdir(parents=True, exist_ok=True)
            cache_p.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
        except OSError:
            pass
    return out


def hm(t):
    return t.strftime("%H:%M") if t else "?"


def link_url(bridge):
    b = bridge or ""
    if not b:
        return ""
    return f"https://claude.ai/code/{b}" if b.startswith("session_") else f"https://claude.ai/code/session_{b}"


def quotable(text, cap):
    """La prima riga sensata dell'ultimo messaggio, fuori dai blocchi di codice, mai una riga con
    un indirizzo email o l'intestazione di una lettera; troncata a `cap`."""
    outside = "\n".join(seg for i, seg in enumerate((text or "").split("```")) if i % 2 == 0)
    acks = r"(?i)^(fatto|va bene|annotato|ok|perfetto|ricevuto|certo|d'accordo|done|applicato)\b[.:!,]?\s*"
    for l in outside.splitlines():
        l = re.sub(r"[*`]+", "", l).strip().lstrip("#-• ").strip()   # niente markdown nel diario
        if not l or "@" in l or re.match(r"(?i)^(gentile|egregi|spett|buongiorno|salve)", l):
            continue
        # «Fatto.» / «Va bene.» in testa non dicono niente: via, resta il seguito (se c'è)
        for _ in range(2):
            l = re.sub(acks, "", l).strip()
        if len(l) < 4:
            continue
        return l if len(l) <= cap else l[:cap] + "…"
    return ""


def span(g):
    a = M("recap.before_today") if g["born_before"] and g["start"] is None else hm(g["start"])
    b = "" if g["alive"] else hm(g["end"])
    return f"{a}→{b}"


def between_sessions(events):
    """23/09 (fase 4 del design approvazioni-casella-registro): cosa e' successo FRA le sessioni, dal diario — i
    messaggi mandati con talk e non ancora consegnati (la coda degli ok e' stata tolta la sera stessa)."""
    sent, delivered = {}, set()
    for e in events or []:
        if e.get("event") == "talk":
            sent[e.get("id")] = e
        elif e.get("event") == "delivered":
            delivered.add(e.get("id"))
    lost = [e for i, e in sent.items() if i not in delivered]
    if not lost:
        return []
    return [M("recap.undelivered", n=len(lost), list=", ".join(f"{e.get('sender') or '?'} → {e.get('to') or '?'}" for e in lost[:5]))]


def render_short(groups, label, as_html=False, between=None, deadlines=None):
    """Il recap delle 20:00: totali, poi le scadenze in avviso, ⏳ ferme, ● vive, ✓ chiuse (senza le chiuse a zero turni).

    `deadlines` = (scadenze, errore) di cm-calendar, o None col calendario spento o per un giorno passato."""
    esc = html.escape if as_html else (lambda x: x)
    mode = str(D.get("last", "short"))
    cap = int(D["max_last_chars"])
    ferme = [g for g in groups if g["alive"] and g["waiting_now"]]
    vive = [g for g in groups if g["alive"] and not g["waiting_now"]]
    chiuse = [g for g in groups if not g["alive"] and (g["turns"] > 0 or not D.get("hide_zero_turns", True))]
    chiuse.sort(key=lambda g: g["end"] or dt.datetime.min, reverse=True)   # le ultime finite per prime
    n_turns = sum(g["turns"] for g in groups)
    lines = []
    title = M("recap.title_short", label=label, n=len(ferme) + len(vive) + len(chiuse), waits=len(ferme))
    lines.append(f"<b>{esc(title)}</b>" if as_html else title)

    icons, registry = (tab_icons() if (ferme or vive) else {}), color_registry()
    said = summaries(ferme + vive + chiuse, label)

    def shown(name, linked):
        """Senza link, un separatore invisibile dopo i punti: altrimenti Telegram trasforma
        «sito.com» in un link da solo."""
        return name if linked else name.replace(".", ".\u2060")

    def name_of(g, linked):
        url = link_url(g["link"]) if linked else ""
        if as_html:
            n = f"<b>{esc(shown(g['project'], bool(url)))}</b>"
            return f'<a href="{url}">{n}</a>' if url else n
        return shown(g["project"], bool(url)) + (f" ({url})" if url else "")

    if deadlines and (deadlines[0] or deadlines[1]):
        cal = _load("cm-calendar")
        if lines[-1] != "":
            lines.append("")
        lines.append(esc(M("recap.h_deadlines")))
        lines.append("")
        lines += [f"📅 {esc(cal.line(e))}" for e in deadlines[0]]
        if deadlines[1]:
            lines.append(esc(deadlines[1]))
        lines.append("")
    if ferme:
        if lines[-1] != "":
            lines.append("")
        lines.append(esc(M("recap.h_waiting")))
        lines.append("")
        for g in ferme:
            t, tool = g["waiting_now"]
            lines.append(f"{icon_for(g, icons, registry)} {name_of(g, True)} · {esc(tool)}")
            q = quotable(g["last"], 60 if mode == "short" else cap) if mode != "none" else ""
            if q:
                lines.append(f"     {esc(q)}")
            lines.append("")
    if vive:
        if lines[-1] != "":
            lines.append("")
        lines.append(esc(M("recap.h_alive")))
        lines.append("")
        for g in vive:
            sm, nxt = said.get(id(g), ("", ""))
            lines.append(f"{icon_for(g, icons, registry)} {name_of(g, True)}" + (f": {esc(sm)}" if sm else ""))
            if nxt:
                lines.append(f"     ↳ {esc(M('recap.next', text=nxt))}")
            lines.append("")
    if chiuse:
        if lines[-1] != "":
            lines.append("")
        lines.append(esc(M("recap.h_closed")))
        lines.append("")
        # soglia di sostanza (idea 3, 11/09): le chiuse sotto recap.min_turns, o senza frase, in UNA
        # riga «altro: a, b»: nelle giornate da quindici progetti il rumore sono le toccate e via
        minor = [g for g in chiuse if g["turns"] < int(D.get("min_turns") or 0) or not said.get(id(g), ("", ""))[0]]
        for g in chiuse:
            if g in minor:
                continue
            sm, _ = said.get(id(g), ("", ""))
            lines.append(name_of(g, False) + (f": {esc(sm)}" if sm else ""))
            lines.append("")
        if minor:
            lines.append(esc(M("recap.h_other", names=", ".join(shown(g["project"], False) for g in minor))))
            lines.append("")
    if between:
        if lines[-1] != "":
            lines.append("")
        lines.append(esc(M("recap.h_between")))
        lines.append("")
        lines += [esc(x) for x in between]
        lines.append("")
    if not (ferme or vive or chiuse):
        lines.append(esc(M("recap.empty")))
    per_acc = OrderedDict()
    for g in groups:
        a = per_acc.setdefault(g["account"], [0, 0])
        a[0] += 1
        a[1] += g["turns"]
    lines.append("")
    def shape_of(acc):
        sh = (CFG["accounts"].get(acc) or {}).get("shape") or "circle"
        return (CFG["tabs"]["colors"].get(sh) or ["●"])[-1]
    lines.append(esc(M("recap.totals_short", detail=" · ".join(f"{shape_of(k)} {k} {v[0]}" for k, v in per_acc.items()))))
    return "\n".join(lines)


def render_full(sessions, label):
    """Il vecchio elenco: una riga per sessione, tutto."""
    n_turns = sum(s["turns"] for s in sessions)
    n_wait = sum(len(s["waiting"]) for s in sessions)
    lines = [M("recap.title", label=label, n=len(sessions), turns=n_turns, waits=n_wait)]
    if not sessions:
        lines.append(M("recap.empty"))
        return "\n".join(lines)
    cap = int(D["max_last_chars"])
    for s in sessions:
        sp = f"{hm(s['start'])}→{M('recap.alive') if s['alive'] else hm(s['end'])}"
        waits = ""
        if s["waiting"]:
            waits = "  " + M("recap.waits", n=len(s["waiting"]), detail=", ".join(f"{tool} {hm(t)}" for t, tool in s["waiting"][-3:]))
        lines.append(f"{s['account']} · {s['project']:<24} {sp:<13} {M('recap.turns', n=s['turns'])}{waits}")
        q = quotable(s["last"], cap)
        if q:
            lines.append(f"   {M('recap.last')}: {q}")
    per_acc = OrderedDict()
    for s in sessions:
        a = per_acc.setdefault(s["account"], [0, 0])
        a[0] += 1
        a[1] += s["turns"]
    lines.append(M("recap.totals", detail=" · ".join(f"{k} {v[0]}/{v[1]}" for k, v in per_acc.items())))
    return "\n".join(lines)


def write_project_log(groups, said, day):
    """`recap.project_log` (default docs/recap.md, "" = spento): in ogni cartella di progetto una riga
    `- AAAA-MM-GG: frase`. Una sola riga per data: se la frase cambia (altri turni dopo le 20:00)
    la riga si riscrive, mai si raddoppia. Solo cartelle esistenti; `docs/` si crea."""
    rel = str(D.get("project_log") or "").strip()
    if not rel or day is None:
        return 0, rel
    stamp = day.isoformat()
    n = 0
    for g in groups:
        sentence, nxt = said.get(id(g), ("", ""))
        if not sentence or not g.get("cwd") or not os.path.isdir(g["cwd"]):
            continue
        if nxt:
            sentence = f"{sentence} · {M('recap.next', text=nxt)}"
        f = Path(g["cwd"]) / rel
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            old = f.read_text() if f.is_file() else ""
            line = f"- {stamp}: {sentence}"
            rows = [r for r in old.splitlines() if not r.startswith(f"- {stamp}:")]
            if old.strip() and rows and rows[0].startswith("- "):
                pass
            if not old.strip():
                rows = [f"# Recap di {g['project']}", ""]
            if rows and rows[-1] == line:
                continue
            f.write_text("\n".join(rows + [line]).rstrip("\n") + "\n")
            n += 1
        except OSError:
            continue
    return n, rel


def calendar_now():
    """(scadenze in avviso, errore) dal calendario; mai un'eccezione: il recap esce comunque."""
    try:
        phase("calendar")
        cal = _load("cm-calendar")
        return cal.deadlines() if cal.url() else None
    except Exception:   # noqa: BLE001
        return None


def to_app(text, day):
    """Il diario anche all'app, come evento `recap` del relay (contratto 1.18): lo stesso testo, senza l'HTML di
    Telegram; `ref` = il giorno, cosi' l'app tiene i diari dei giorni prima."""
    _load("cm-core").relay_event("recap", M("recap.event_title", d=day.strftime("%d/%m")), text, ref=day.isoformat())


def send(text, as_html):
    phase("send")
    bot = _load("cm-bot")
    if not bot.token():
        print(M("bot.no_token", path=CFG["bot"]["token_file"]), file=sys.stderr)
        return 1
    chats = bot.allowed_chats()
    if not chats:
        print(M("recap.no_chats"), file=sys.stderr)
        return 1
    n = bot.send(text, parse_mode="HTML" if as_html else None)
    print(M("recap.sent", n=n))
    return 0


# ------------------------------------------------------------------ cron
def cron_line():
    hh, _, mm = str(D["cron_time"]).partition(":")
    shim = cm.home() / ".local" / "bin" / "supervisor"
    return f"{int(mm or 0)} {int(hh or 20)} * * * {shim} recap --send >/dev/null 2>&1"


def crontab_read():
    return subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-l"], capture_output=True, text=True).stdout


def crontab_write(text):
    subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-"], input=text, text=True, check=True)


def install():
    cur = crontab_read()
    if re.search(r"supervisor (recap|diary) --send", cur):
        print(M("recap.cron_present"))
        return 0
    crontab_write(cur.rstrip("\n") + ("\n" if cur.strip() else "") + "# supervisor recap: il diario della giornata su Telegram\n" + cron_line() + "\n")
    print(M("recap.cron_installed", line=cron_line()))
    return 0


def uninstall():
    cur = crontab_read()
    if "supervisor recap --send" not in cur:
        print(M("recap.cron_absent"))
        return 0
    lines = [l for l in cur.splitlines() if "supervisor recap" not in l]
    crontab_write("\n".join(lines) + ("\n" if lines else ""))
    print(M("recap.cron_removed"))
    return 0


def status():
    print(M("recap.status_cron", state="yes" if re.search(r"supervisor (recap|diary) --send", crontab_read()) else "no", line=cron_line()))
    p = ledger_path()
    n = sum(1 for _ in p.open()) if p.is_file() else 0
    print(M("recap.status_ledger", path=p, n=n))
    return 0


def build(argv):
    """Il testo del diario (per il comando e per il bot). Ritorna (testo_pieno, testo_html)."""
    ap = argparse.ArgumentParser(prog="supervisor recap", add_help=True)
    ap.add_argument("--date", "--data", dest="date")
    ap.add_argument("--since", "--da", dest="since", type=float, help="ore")
    ap.add_argument("--send", "--invia", dest="send", action="store_true")
    ap.add_argument("--full", "--tutto", dest="full", action="store_true")
    ap.add_argument("--json", dest="as_json", action="store_true")
    a = ap.parse_args(argv)
    phase("ledger")
    if a.since:
        events = read_events(since=dt.datetime.now() - dt.timedelta(hours=a.since))
        label = M("recap.label_since", h=a.since)
    else:
        day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
        events = read_events(day=day)
        label = day.strftime("%d/%m/%Y")
    sessions = summarize(events)
    live = live_registry()
    groups = group_projects(sessions, live)
    a.events = events
    return a, sessions, groups, label


def main(argv):
    """Il comando, dentro il suo tempo massimo: allo scadere una riga chiara e uscita 124, mai un recap appeso."""
    if argv and argv[0] in ("install", "uninstall", "status"):
        return {"install": install, "uninstall": uninstall, "status": status}[argv[0]]()
    RUN["send"] = "--send" in argv or "--invia" in argv
    n = max_s()

    def stop(signum, frame):
        raise TooLong()

    armed = bool(n) and hasattr(signal, "SIGALRM")
    RUN.update(t0=time.time(), armed=armed)
    if armed:
        signal.signal(signal.SIGALRM, stop)
        signal.alarm(n)
    try:
        return run(argv)
    except TooLong:
        sys.stdout.flush()
        print(M("recap.too_long", n=n, phase=M("recap.phase_" + RUN["phase"])), file=sys.stderr)
        return 124
    finally:
        if armed:
            signal.alarm(0)


def run(argv):
    a, sessions, groups, label = build(argv)
    if a.as_json:
        def ser(g):
            return {**g, "start": g["start"].isoformat() if g["start"] else None, "end": g["end"].isoformat() if g["end"] else None,
                    "last_ts": g["last_ts"].isoformat() if g["last_ts"] else None,
                    "waiting": [(t.isoformat(), tool) for t, tool in g["waiting"]],
                    "waiting_now": [g["waiting_now"][0].isoformat(), g["waiting_now"][1]] if g["waiting_now"] else None,
                    "link": link_url(g["link"])}
        print(json.dumps([ser(g) for g in groups], ensure_ascii=False, indent=1))
        return 0
    day = None if a.since else (dt.date.fromisoformat(a.date) if a.date else dt.date.today())
    if a.full:
        text = render_full(sessions, label)
        print(text)
        if a.send and day:
            to_app(text, day)
        return send(text, False) if a.send else 0
    between = between_sessions(getattr(a, "events", None))
    deadlines = calendar_now() if (not day or day >= _load("cm-calendar").today()) else None
    plain = render_short(groups, label, as_html=False, between=between, deadlines=deadlines)
    print(plain)
    sys.stdout.flush()
    if day:
        said = summaries(groups, label)
        phase("log")
        n, rel = write_project_log(groups, said, day)
        if n:
            print(M("recap.log_written", n=n, file=rel))
    if a.send:
        if day:
            phase("send")
            to_app(plain, day)
        return send(render_short(groups, label, as_html=True, between=between, deadlines=deadlines), True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
