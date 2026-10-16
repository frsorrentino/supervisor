#!/usr/bin/env python3
"""supervisor talk — manda un prompt a un'altra sessione Claude e restituisce la sua risposta.
supervisor wait — aspetta che una sessione torni ferma e stampa la sua ultima risposta.

  supervisor talk <nome> "prompt" [--wait S] [--quiet S] [--no-wait] [--force] [--reopen]
                                     [--via auto|socket|tmux] [--no-native-hint]
  supervisor wait <nome> [--timeout S]

Due vie, scelte dai dati del registro peer (1.6, E6):

- **socket** (dal 09/09/2026, E6a/E6b): il registro `sessions/<pid>.json` dichiara
  il socket inbox della sessione e il file `.key` accanto porta il `peerToken`.
  Si consegna il messaggio nel formato catturato dal vivo (riga di auth + riga
  del messaggio) e si legge la risposta dal TRANSCRIPT (`projects/<slug>/<id>.jsonl`),
  non dallo schermo: niente righe spezzate, niente testo scorso via. Vale per
  qualunque account: il socket e' un file dello stesso utente.
- **tmux** (ripiego, = parla-con.sh): si digita nel riquadro e si legge lo
  schermo. Prima si controlla che nella casella non ci sia gia' testo DIGITATO
  (T16): il suggerimento grigio di Claude Code si riconosce da SGR 2 (`\\e[2m`),
  a volte non chiuso da `\\e[0m`; va letta l'ULTIMA riga con ❯; dopo ❯ c'e' uno
  spazio unificatore U+00A0. Escape, poi il testo con -l, poi Invio separato (T17).
  La fine si indovina dallo schermo fermo, con un tetto.

Chi legge da una sessione Claude dello stesso registro ha una via migliore:
`SendMessage` con `notify_when_idle` (T45). Lo script lo dice (talk.warn_native_channel)
e procede: non puo' chiamare un tool di sessione al posto tuo.
"""
import glob
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
sessions = _load("cm-sessions")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731


def die(key, code, **kw):
    print(M(key, **kw), file=sys.stderr)
    sys.exit(code)


def find(name):
    rows = sessions.collect(read_screen=False)
    for r in rows:
        if r["name"] == name or r["tmux"] == name:
            return r
    return None


def transcript_path(row):
    """<config_dir>/projects/<slug>/<sessionId>.jsonl — slug: ogni non alfanumerico → '-'."""
    acc = CFG["accounts"].get(row["account"]) or {}
    conf = cm.expand(acc.get("config_dir", "~/.claude"))
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(row["cwd"]))
    return Path(conf) / "projects" / slug / f"{row['session_id']}.jsonl"


def assistant_texts(path, after_offset):
    """Testi delle risposte assistant scritte dopo `after_offset` byte; ritorna (testi, nuovo offset)."""
    texts = []
    try:
        with open(path, "rb") as f:
            f.seek(after_offset)
            data = f.read()
    except OSError:
        return texts, after_offset
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant":
            continue
        for block in (d.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                texts.append(block["text"])
    return texts, after_offset + len(data)


def status_of(row):
    try:
        d = json.load(open(Path(row["registry"]) / f"{row['pid']}.json"))
        return d.get("status") or "?"
    except (OSError, ValueError):
        return "gone"


# ------------------------------------------------------------------ socket
def post_socket(row, text, from_name, from_mode):
    """E6b: auth con il peerToken del destinatario, poi il messaggio."""
    keys = glob.glob(str(Path(row["registry"]) / f"{row['pid']}.*.key"))
    token = ""
    if keys:
        try:
            token = json.load(open(keys[0])).get("peerToken", "")
        except (OSError, ValueError):
            pass
    own = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET", "")
    content = (f'<cross-session-message from="uds:{own}" from-name="{from_name}" from-mode="{from_mode}">\n'
               f"{text}\n</cross-session-message>")
    msg = {"msgV": 1, "msg_id": str(uuid.uuid4()), "type": "user",
           "message": {"role": "user", "content": content}, "priority": "next", "from": f"uds:{own}"}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(row["socket"])
    s.sendall((json.dumps({"type": "auth", "token": token}) + "\n" + json.dumps(msg) + "\n").encode())
    s.close()


def wait_reply(row, offset, max_wait, quiet):
    """Legge il transcript finche' la sessione torna idle (registro) o lo schermo del
    transcript resta fermo per `quiet` secondi; tetto `max_wait`."""
    start = time.time()
    texts = []
    last_change = time.time()
    seen_busy = False
    while True:
        new, offset = assistant_texts(row["_transcript"], offset)
        if new:
            texts += new
            last_change = time.time()
        st = status_of(row)
        if st == "busy":
            seen_busy = True
        if seen_busy and st == "idle" and time.time() - last_change > 1.5:
            break
        if st == "gone":
            break
        if time.time() - last_change > quiet and texts:
            break
        if time.time() - start > max_wait:
            print(M("talk.timeout", s=max_wait))
            break
        time.sleep(1)
    return texts


# ------------------------------------------------------------------ tmux
def tmux(*args):
    return sessions.tmux(*args)


def typed_text(tm):
    """T16: cio' che c'e' nella casella ESCLUSI i suggerimenti (SGR 2)."""
    raw = subprocess.run(sessions.TMUX + ["capture-pane", "-p", "-e", "-t", tm], capture_output=True, text=True).stdout
    lines = [l for l in raw.splitlines() if "❯" in l]
    if not lines:
        return ""
    line = lines[-1]
    line = re.sub(r"\x1b\[2m.*?\x1b\[0m", "", line)   # suggerimento chiuso
    line = re.sub(r"\x1b\[2m.*$", "", line)           # suggerimento fino a fine riga
    line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)   # altri attributi
    line = re.sub(r"^\s*❯[\s ]*", "", line)
    return re.sub(r"[\s ]*$", "", line)


def talk_tmux(row, text, force, max_wait, quiet, no_wait):
    tm = row["tmux"]
    if not tm:
        die("talk.no_tmux", 3, name=row["name"])
    residue = typed_text(tm)
    if residue and not force:
        print(M("talk.typed_text", name=tm, text=residue), file=sys.stderr)
        sys.exit(4)
    before = tmux("capture-pane", "-p", "-t", tm) or ""
    # Escape svuota il campo (T17) — solo quando c'e' un residuo da buttare (--force):
    # in Claude Code Esc a turno in corso INTERROMPE il turno, meglio non mandarlo a vuoto
    if residue:
        tmux("send-keys", "-t", tm, "Escape")
        time.sleep(0.6)   # oltre l'escape-time di tmux (500 ms): ESC + testo non deve diventare Meta+lettera
    tmux("send-keys", "-t", tm, "-l", text)
    time.sleep(0.4)
    tmux("send-keys", "-t", tm, "Enter")
    # 14/10 (spunti della valutazione di Kaya): un Invio perso lascia il prompt sulla riga e la sessione non parte.
    # Se dopo un attimo il testo e' ancora nella casella, Invio una volta sola, e lo si dice.
    time.sleep(1.5)
    head = " ".join(text.split())[:30]
    if head and " ".join(typed_text(tm).split()).startswith(head):
        tmux("send-keys", "-t", tm, "Enter")
        print(M("talk.enter_again", name=tm), file=sys.stderr)
    if no_wait:
        return []
    start = time.time()
    still = 0
    last = None
    while True:
        time.sleep(2)
        now = tmux("capture-pane", "-p", "-t", tm) or ""
        if now == last:
            still += 2
            if still >= quiet:
                break
        else:
            still, last = 0, now
        if time.time() - start >= max_wait:
            print(M("talk.timeout", s=max_wait))
            break
    after = tmux("capture-pane", "-p", "-t", tm) or ""
    b = set(before.splitlines())
    return ["\n".join(l for l in after.splitlines() if l.strip() and l not in b)[-4000:]]


# ------------------------------------------------------------------ main
def inbox():
    return _load("cm-inbox")


def sender_name():
    """Chi scrive: il nome tmux della sessione da cui parte il comando, o talk.from_name."""
    pane = os.environ.get("TMUX_PANE", "")
    if pane:
        out = tmux("display-message", "-p", "-t", pane, "#{session_name}")
        if out and out.strip():
            return out.strip()
    return CFG["talk"]["from_name"]


REOPEN_WAIT_S = 90


def reopen_session(name):
    """14/10 (dall'analisi dei concorrenti, «Parla» riapre e consegna): `talk --reopen` verso una sessione chiusa la
    rilancia dalla fotografia del registro, nella sua cartella e col suo account, con --resume <session_id> quando la
    trascrizione c'e' (come restore), altrimenti --continue; poi aspetta che sia viva e la riga torna a cmd_talk, che
    consegna come sempre. None se il rilancio non riesce: allora il messaggio resta in casella, come senza --reopen."""
    entry = None
    for key in ("good_file", "file"):
        try:
            d = json.loads(Path(cm.expand(CFG["registry"][key])).read_text())
        except (OSError, ValueError, KeyError):
            continue
        entry = next((s for s in d.get("sessioni") or [] if s.get("nome") == name), None)
        if entry:
            break
    if not entry or not os.path.isdir(entry.get("cartella") or ""):
        return None
    acc, sid, folder = entry.get("account") or "", entry.get("session_id") or "", entry["cartella"]
    conf = cm.expand(((CFG.get("accounts") or {}).get(acc) or {}).get("config_dir") or "")
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(folder))
    how = ["--resume", sid] if sid and conf and os.path.isfile(os.path.join(conf, "projects", slug, sid + ".jsonl")) else ["--continue"]
    cmd = [os.environ.get("CM_TALK_CM") or str(HERE / "supervisor"), "launch", folder, *how] + (["--account", acc] if acc else [])   # CM_TALK_CM: prove
    print(M("talk.reopening", name=name, how=" ".join(how)), file=sys.stderr)
    if subprocess.run(cmd, capture_output=True, text=True, timeout=300).returncode != 0:
        return None
    end = time.time() + REOPEN_WAIT_S
    while time.time() < end:
        row = find(name)
        if row:
            time.sleep(3)   # il prompt di Claude Code si disegna un attimo dopo il registro
            return find(name) or row
        time.sleep(2)
    return None


def cmd_talk(argv):
    if argv and argv[0] == "--status":
        return inbox().main(["status"] + argv[1:2])
    name = argv[0] if argv else ""
    text = argv[1] if len(argv) > 1 else ""
    if not name or not text:
        die("talk.usage", 2)
    max_wait = int(os.environ.get("ATTESA_MAX", CFG["talk"]["max_wait_s"]))
    quiet = int(os.environ.get("QUIETE", CFG["talk"]["quiet_s"]))
    force = os.environ.get("FORZA") == "si"
    no_wait = False
    reopen = False
    via = "auto"
    hint = CFG["talk"]["warn_native_channel"] and os.environ.get("NATIVO") != "no"
    a = argv[2:]
    i = 0
    while i < len(a):
        x = a[i]
        if x in ("--wait", "--attesa"):
            max_wait = int(a[i + 1]); i += 1
        elif x in ("--quiet", "--quiete"):
            quiet = int(a[i + 1]); i += 1
        elif x == "--no-wait":
            no_wait = True
        elif x in ("--force", "--forza"):
            force = True
        elif x == "--via":
            via = a[i + 1]; i += 1
        elif x == "--no-native-hint":
            hint = False
        elif x == "--reopen":
            reopen = True
        else:
            die("talk.unknown_option", 2, opt=x)
        i += 1

    # fase 2.2 del piano multi-PC: `HOST:nome` (o un nome lanciato con launch --host) va per la named pipe dell'host
    rs = _load("cm-rsession")
    if rs.resolve(name):
        return rs.cmd_talk(argv)
    row = find(name)
    if not row:
        # 23/09: una sessione nota ma chiusa riceve il messaggio quando riparte (casella persistente); un nome mai
        # visto resta un errore, perche' e' quasi sempre un refuso
        known, cwd = inbox().known_session(name)
        if known and reopen:
            row = reopen_session(name)
        if known and not row:
            rec = inbox().put(name, text, sender_name(), cwd)
            print(M("talk.saved", name=name, id=rec["id"]), file=sys.stderr)
            return 0
        if not row:
            print(M("talk.missing", name=name), file=sys.stderr)
            live = [r["tmux"] or r["name"] for r in sessions.collect(read_screen=False)]
            print("  " + " ".join(live), file=sys.stderr)
            sys.exit(3)
    if row["channel"] == "(questa)":
        die("talk.self", 2, name=name)
    if hint and row["channel"] == "nativo" and os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET"):
        print(M("talk.native_hint", name=name), file=sys.stderr)

    # la casella prima della consegna: se la sessione si chiude o il socket rifiuta, il messaggio non si perde
    rec = inbox().put(name, text, sender_name(), row.get("cwd") or "")
    use_socket = via == "socket" or (via == "auto" and row.get("socket") and os.path.exists(row["socket"]) and row.get("session_id"))
    if use_socket:
        row["_transcript"] = transcript_path(row)
        offset = row["_transcript"].stat().st_size if row["_transcript"].exists() else 0
        try:
            post_socket(row, text, CFG["talk"]["from_name"], CFG["talk"]["from_mode"])
        except OSError as e:
            if via == "socket":
                die("talk.socket_failed", 5, name=name, error=str(e))
            print(M("talk.socket_fallback", name=name, error=str(e)), file=sys.stderr)
            use_socket = False
        else:
            inbox().mark(rec["id"], "delivered", "socket")
            print(M("talk.sent", name=name, via="socket"), file=sys.stderr)
            if no_wait:
                return 0
            for t in wait_reply(row, offset, max_wait, quiet):
                print(t)
            return 0
    texts = talk_tmux(row, text, force, max_wait, quiet, no_wait)
    inbox().mark(rec["id"], "delivered", "tmux")
    print(M("talk.sent", name=name, via="tmux"), file=sys.stderr)
    for t in texts:
        print(t)
    return 0


def cmd_wait(argv):
    name = argv[0] if argv else ""
    if not name:
        die("wait.usage", 2)
    timeout = int(argv[argv.index("--timeout") + 1]) if "--timeout" in argv else 12 * 3600
    rs = _load("cm-rsession")
    if rs.resolve(name):
        return rs.cmd_wait(argv)
    row = find(name)
    if not row:
        die("talk.missing", 3, name=name)
    row["_transcript"] = transcript_path(row)
    offset = row["_transcript"].stat().st_size if row["_transcript"].exists() else 0
    start = time.time()
    while True:
        st = status_of(row)
        if st in ("idle", "gone"):
            break
        if time.time() - start > timeout:
            print(M("talk.timeout", s=timeout))
            return 1
        time.sleep(2)
    print(M("wait.idle", name=name, status=st))
    # goal nativo ancora aperto (`/goal`): idle con un goal non raggiunto = «Goal paused», non lavoro finito
    g = sessions.goal_of(row)
    if g and not g["met"]:
        print(M("wait.goal", goal=g["condition"], n=g["iterations"]))
    texts, _ = assistant_texts(row["_transcript"], offset)
    if not texts:
        texts, _ = assistant_texts(row["_transcript"], 0)
        texts = texts[-1:]
    for t in texts:
        print(t)
    return 0


# 08/10 (dal telefono, Stop → «nothing to stop»): da Claude Code 2.1.294 la riga di stato del turno non dice piu'
# «esc to interrupt» («● Slithering… (1m 9s · ↓ 2.7k tokens)»): conta anche lo spinner, glifo + «…» + «(Ns ·» o «tokens)»
BUSY = re.compile(r"(?im)esc to interrupt|^\s*[^\w\s]\s+\w[^\n]*…\s*\([^)\n]*(?:\b\d+[smh]\b|tokens)")


def turn_running(tm):
    """Il turno e' in corso: la riga di stato del turno (BUSY) nelle ultime righe dello schermo (la riga di stato di Claude Code;
    le righe piu' su sono conversazione, dove la frase puo' comparire come testo)."""
    return bool(BUSY.search("\n".join((tmux("capture-pane", "-p", "-t", tm) or "").rstrip().splitlines()[-12:])))


def cmd_interrupt(argv):
    """`supervisor interrupt NOME` (30/09, il tasto Stop del telefono): UN Esc, e solo a turno in corso. Due Esc al
    prompt fermo aprono il rewind; un Esc su un dialogo lo annulla: per questo prima si guarda lo schermo.
    Exit 0 fermato, 1 niente da fermare, 3 sessione non viva o senza tmux, 4 ancora in corso dopo l'attesa.
    `--wait S` (16/10, dal telefono: Stop subito dopo un invio → «nothing to stop»): il prompt e' consegnato ma il
    turno non e' ancora partito, e lo schermo non mostra la riga di stato; si aspetta fino a S secondi che compaia,
    poi UN Esc come sempre. L'Esc alla cieca no: al prompt fermo il secondo apre il rewind."""
    wait = 0.0
    if "--wait" in argv:
        i = argv.index("--wait")
        try:
            wait = max(0.0, min(30.0, float(argv[i + 1])))
        except (IndexError, ValueError):
            die("interrupt.usage", 2)
        argv = argv[:i] + argv[i + 2:]
    name = argv[0] if argv else ""
    if not name:
        die("interrupt.usage", 2)
    row = find(name)
    tm = (row or {}).get("tmux") or ""
    if not tm and subprocess.run(sessions.TMUX + ["has-session", "-t", f"={name}"], capture_output=True).returncode == 0:
        tm = name   # basta la sessione tmux: l'Esc va al suo pannello
    if not tm:
        die("interrupt.not_running", 3, name=name)
    end = time.time() + wait
    while not turn_running(tm):
        if time.time() >= end:
            die("interrupt.nothing", 1, name=name)
        time.sleep(0.25)
    tmux("send-keys", "-t", tm, "Escape")
    for _ in range(20):
        time.sleep(0.25)
        if not turn_running(tm):
            print(M("interrupt.stopped", name=name))
            return 0
    die("interrupt.still", 4, name=name)


PANEL_END = re.compile(r"(?i)\besc to (cancel|close|exit|go back)\b")


def _panel_bounds(lines):
    """(inizio, fine) del testo di un pannello aperto sopra la casella; None se non ce n'e' uno. Il pannello comincia
    sotto la sua riga di bordo «▔▔▔» e, finche' e' aperto, la casella «❯» non si vede sotto. Il piè di pagina «Esc to
    cancel» c'e' solo se il pannello sta nello schermo: con un pannello piu' alto (/cost a 32 righe, dal vivo il
    02/10) resta fuori, e il testo arriva fino all'ultima riga visibile."""
    border = max((i for i, l in enumerate(lines) if l.count("\u2594") >= 10), default=None)
    if border is None or any(l.lstrip().startswith("\u276f") for l in lines[border + 1:]):
        return None
    end = next((i for i in range(len(lines) - 1, border, -1) if PANEL_END.search(lines[i])), len(lines))
    return border + 1, end


def cmd_panel(argv):
    """`supervisor panel NOME [--wait S]` (02/10, dal telefono): un pannello che un comando slash ha aperto sopra la
    casella (/cost, /usage, /status…) resta aperto finche' qualcuno preme Esc, e dal telefono non si vede ne' si chiude.
    Aspetta fino a S secondi che compaia, ne legge il testo quando lo schermo smette di cambiare (il contenuto arriva
    in ritardo: «Scanning local sessions…»), lo stampa e lo chiude con UN Esc, mandato solo con il pannello visibile:
    un Esc a turno in corso lo interromperebbe (/context lavora come un turno e scrive nella conversazione).
    Exit 0 letto e chiuso, 1 nessun pannello, 3 sessione assente, 4 ancora aperto dopo l'Esc.
    Il pannello si riconosce dal bordo «▔▔▔» senza casella sotto (_panel_bounds), non dal piè di pagina."""
    name = argv[0] if argv else ""
    wait = float(argv[argv.index("--wait") + 1]) if "--wait" in argv else 6.0
    # prima il nome tmux (il relay passa quello): find() scorre tutte le sessioni e con la regia carica costa 20 s
    tm = name if name and subprocess.run(sessions.TMUX + ["has-session", "-t", f"={name}"], capture_output=True).returncode == 0 else ""
    if not tm:
        tm = (find(name) or {}).get("tmux") or ""
    if not tm:
        die("interrupt.not_running", 3, name=name)
    grab = lambda: (tmux("capture-pane", "-p", "-J", "-t", tm) or "").rstrip().splitlines()  # noqa: E731
    deadline = time.time() + wait
    lines = grab()
    while _panel_bounds(lines) is None:
        if time.time() > deadline:
            return 1
        time.sleep(0.5)
        lines = grab()
    still, last, stop = 0.0, lines, time.time() + 8
    while still < 1.5 and time.time() < stop:
        time.sleep(0.5)
        now = grab()
        still = still + 0.5 if now == last else 0.0
        last = now
    lines = last
    bounds = _panel_bounds(lines)
    if bounds is None:
        return 1
    text = "\n".join(l.rstrip() for l in lines[bounds[0]:bounds[1]] if l.strip())
    tmux("send-keys", "-t", tm, "Escape")
    for _ in range(12):
        time.sleep(0.25)
        if _panel_bounds(grab()) is None:
            print(text)
            return 0
    print(text)
    return 4


if __name__ == "__main__":
    prog = os.path.basename(sys.argv[0])
    args = sys.argv[1:]
    if args and args[0] == "interrupt":
        sys.exit(cmd_interrupt(args[1:]))
    if args and args[0] == "panel":
        sys.exit(cmd_panel(args[1:]))
    if args and args[0] in ("talk", "wait"):
        sub, args = args[0], args[1:]
    else:
        sub = "wait" if os.environ.get("CM_SUBCOMMAND") == "wait" else "talk"
    sys.exit(cmd_wait(args) if sub == "wait" else cmd_talk(args))
