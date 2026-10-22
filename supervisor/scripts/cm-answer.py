#!/usr/bin/env python3
"""supervisor answer — rispondere alla domanda (AskUserQuestion, permesso) di un'altra sessione.

  supervisor answer <nome> --show              la domanda aperta e le sue opzioni numerate
  supervisor answer <nome> <n> [<n> ...] [--text "..."]
                                                   sceglie l'opzione n (piu' numeri = piu' domande di fila);
                                                   --text per l'opzione «Type something.»

Il dialogo di Claude Code e' un menu nel terminale: si legge lo schermo della sessione tmux
(capture-pane, nome nudo: T1), si conta dove sta il cursore «❯», si manda Su/Giu' fino
all'opzione voluta, si verifica sullo schermo e si conferma con Invio: e' la stessa strada di
`launch` per i dialoghi di fiducia (T3). Provato dal vivo il 10/09/2026: «Down, Enter» → «→ Blu»
e la sessione riparte. Un messaggio nell'inbox NON risponde a un dialogo aperto.

Senza «Enter to select» sullo schermo non c'e' nessuna domanda: esce senza toccare tasti. Il dialogo di permesso
con la conferma chiesta da un hook finisce invece con «Esc to cancel · Tab to amend»: vale anche quello, ma solo
con il cursore «❯» su una voce numerata (30/09, dal vivo: il permesso dell'Agent arrivava al telefono senza opzioni).
E' il pezzo che manca al telefono: dalla master, «rispondi 2 a progetto-x» → `supervisor answer progetto-x 2`.

  cm-answer.py --notify   (dall'hook PermissionRequest, staccato, payload JSON su stdin)

L'avviso sul telefono quando una sessione si ferma (idea 1 dell'11/09/2026, dopo ccgram/ccbot):
aspetta `hooks.ask_notify.delay_s` che il dialogo sia disegnato, legge lo schermo della sessione
(nome dal TMUX_PANE dell'hook), manda alle chat Telegram autorizzate la domanda, le opzioni numerate
e «rispondi N a NOME»; senza schermo (fuori tmux) usa il tool_input del payload. Testo, non tastiera
inline: un tap arriverebbe al plugin telegram, non a noi (stesso bot). Niente token → silenzio.
"""
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
sessions = _load("cm-sessions")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
# il cursore e' «❯»; nella console di Windows Claude Code lo disegna «>» (09/10, sessioni su un altro host)
OPTION = re.compile(r"^\s*(❯|>)?\s*(\d+)\.\s+(.*\S)\s*$")
# la casella davanti all'intestazione: «☐» qui, «[ ]» (o «[✓]») nella console di Windows (09/10, dal vivo su win)
HEADER_BOX = re.compile(r"^\s*\[[ x✓✔]\]\s+(?=\S)")
FOOTER = "Enter to select"
FOOTER_PERMISSION = "Esc to cancel"   # «Esc to cancel · Tab to amend»: il dialogo di permesso (Bash, Agent, …)
# le voci fisse in coda al dialogo di AskUserQuestion: non sono opzioni (via master 12/09, screenshot dell'utente)
FOOT_OPTIONS = ("type something.", "chat about this")
# ...ma si possono scegliere per nome: `answer NOME --text "..."` (senza numero) e `answer NOME --chat` (15/09, dal polso)
FOOT_KIND = {"type": "type something", "chat": "chat about this"}


def items(text):
    """Tutte le voci numerate del dialogo, opzioni e pie' di pagina: [(n, label, corrente)]."""
    return [(int(m.group(2)), m.group(3).strip(), bool(m.group(1))) for m in map(OPTION.match, text.splitlines()) if m]


def tmux(*args):
    return subprocess.run(sessions.TMUX + list(args), capture_output=True, text=True)


def screen(name):
    return TERMS[name].screen() if name in TERMS else tmux("capture-pane", "-p", "-t", name).stdout


class Tmux:
    """Lo schermo e i tasti di una sessione di qui: tmux capture-pane e send-keys."""
    def __init__(self, name):
        self.name = name

    def screen(self):
        return tmux("capture-pane", "-p", "-t", self.name).stdout

    def keys(self, seq):
        for k in seq:
            if k.startswith("text:"):
                tmux("send-keys", "-t", self.name, "-l", k[5:])
            else:
                tmux("send-keys", "-t", self.name, k)
            time.sleep(0.15)
        time.sleep(0.3)
        return self.screen()


class Remote:
    """09/10: una sessione su un altro host (`HOST:nome`, o un nome registrato come sessione remota): schermo e tasti
    della sua console attraverso l'adattatore (su Windows cm-console.ps1). Una chiamata manda i tasti e rilegge."""
    def __init__(self, host, name):
        self.host, self.name = host, name
        self.adapter = _load("cm-hosts").adapter(host)

    def screen(self):
        return self.adapter.session_console(self.name)["screen"]

    def keys(self, seq):
        return self.adapter.session_console(self.name, list(seq))["screen"]


TERMS = {}


def term_for(name):
    """Tmux o Remote per il nome dato, o None se non c'e' nessuna sessione con quel nome."""
    if tmux("has-session", "-t", f"={name}").returncode == 0:
        return Tmux(name)
    hit = _load("cm-rsession").resolve(name)
    if hit and hasattr(_load("cm-hosts").adapter(hit[0]), "session_console"):
        return Remote(*hit)
    return None


def background(name):
    """17/10/2026: una sessione viva del registro senza tmux (lanciata con --bg) non ha uno schermo da leggere ne'
    tasti da mandare; la sua console e' solo `claude attach`, che prende il terminale. Si dice questo, non «nessuna
    sessione»."""
    return any((r["name"] == name or r["tmux"] == name) and not r["tmux"] for r in sessions.collect(read_screen=False))


def parse(text):
    """(header, domanda, [(n, label, corrente, descrizione)], footer presente). Le opzioni sono le righe
    «n. label»; la descrizione sotto (indentata, senza numero) si raccoglie (12/09: sul polso e' il testo che
    i tasti non possono mostrare)."""
    lines = text.splitlines()
    if not any(FOOTER in l for l in lines) and not (any(FOOTER_PERMISSION in l for l in lines)
                                                    and any((m := OPTION.match(l)) and m.group(1) for l in lines)):
        return None
    options, header, question = [], "", ""
    for i, l in enumerate(lines):
        m = OPTION.match(l)
        if m:
            if m.group(3).strip().lower().rstrip(".") in [f.rstrip(".") for f in FOOT_OPTIONS]:
                break   # da qui in poi solo pie' di pagina
            options.append((int(m.group(2)), m.group(3).strip(), bool(m.group(1)), ""))
            continue
        if options and l.strip() and l.startswith("  ") and FOOTER not in l and FOOTER_PERMISSION not in l and not l.strip().startswith("─"):
            n, lab, cur, desc = options[-1]
            options[-1] = (n, lab, cur, (desc + " " + l.strip()).strip())
            continue
        if "☐" in l or "☑" in l or HEADER_BOX.match(l):
            header = HEADER_BOX.sub("", l.replace("☐", "").replace("☑", "")).strip()
            # la domanda puo' stare su piu' righe (con «│» davanti): si uniscono fino alla riga vuota o alla
            # prima opzione (12/09: la seconda riga della domanda della master andava persa)
            qlines = []
            for x in lines[i + 1:]:
                xs = x.strip().lstrip("│").strip()
                if OPTION.match(x):
                    break
                if not xs:
                    if qlines:
                        break
                    continue
                qlines.append(xs)
            question = " ".join(qlines)
    if options and not header and not question:
        # il dialogo di permesso non ha «☐»: la domanda sono le righe subito sopra la prima opzione, fino alla riga
        # vuota (07/10, dal polso: la domanda della master arrivava come «-: -»)
        first = next(i for i, l in enumerate(lines) if OPTION.match(l))
        qlines = []
        for x in reversed(lines[:first]):
            xs = x.strip().lstrip("│").strip()
            if not xs or xs.startswith("─"):
                break
            qlines.insert(0, xs)
        question = " ".join(qlines)
    return header, question, options, True


def show(name):
    d = parse(screen(name))
    if not d:
        print(M("answer.no_question", name=name))
        return 1
    header, question, options, _ = d
    print(M("answer.question", name=name, header=header or "-", question=question or "-"))
    for n, label, cur, _d in options:
        print(f"  {'❯' if cur else ' '} {n}. {label}")
    return 0


def choose(name, n, text, foot=""):
    """Sceglie l'opzione n; con `foot` («type» o «chat») la voce fissa in coda al dialogo, che parse non conta fra
    le opzioni: «Type something.» con il testo, o «Chat about this» (15/09, dal polso)."""
    scr = screen(name)
    d = parse(scr)
    if not d:
        print(M("answer.no_question", name=name))
        return 1
    _, question, options, _ = d
    if foot:
        want = FOOT_KIND[foot]
        n = next((x[0] for x in items(scr) if x[1].lower().rstrip(".") == want), 0)
        if not n:
            print(M("answer.no_foot", label=want.capitalize()))
            return 2
    else:
        numbers = [o[0] for o in options]
        if n not in numbers:
            print(M("answer.no_option", n=n, options=", ".join(str(x) for x in numbers)))
            return 2
    current = next((x[0] for x in items(scr) if x[2]), n)
    step = "Down" if n > current else "Up"
    t = TERMS.get(name) or Tmux(name)
    scr2 = t.keys([step] * abs(n - current)) if n != current else scr
    if not parse(scr2) or not any(x[0] == n and x[2] for x in items(scr2)):
        print(M("answer.cursor_lost", n=n))
        return 3
    label = next(x[1] for x in items(scr2) if x[0] == n)
    if text and re.search(r"(?i)type something", label):
        # «Type something.» e' un campo in riga: col cursore sopra si scrive e Invio conferma il testo (dal vivo il
        # 15/09: «❯ 3. verde»). Prima si mandava Invio e poi il testo: il testo non arrivava mai alla domanda.
        text = " ".join(text.split())   # un a capo confermerebbe a meta'
        typed_scr = t.keys(["text:" + text])
        time.sleep(0.1)
        typed = next((x[1] for x in items(typed_scr) if x[0] == n and x[2]), "")
        if not typed or not text.startswith(typed[:20].rstrip("…").strip()):
            print(M("answer.cursor_lost", n=n))
            return 3
        label = text
    t.keys(["Enter"])
    time.sleep(1.0)
    print(M("answer.chosen", name=name, n=n, label=label, question=question or "-"))
    return 0


def my_tmux_name():
    """Il nome della sessione tmux del pane in cui gira l'hook (TMUX_PANE), o ""."""
    pane = os.environ.get("TMUX_PANE", "")
    if not pane:
        return ""
    r = tmux("display-message", "-p", "-t", pane, "#{session_name}")
    return r.stdout.strip() if r.returncode == 0 else ""


def _detail(tool_input):
    for k in ("command", "file_path", "path", "url", "pattern", "prompt", "description"):
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:160]
    return ""


def icon_of(name):
    try:
        r = subprocess.run([str(HERE / "cm-color.sh"), name], capture_output=True, text=True, timeout=10)
        return (r.stdout.split() or [""])[0] if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def clean_label(label):
    """«Firebase RTDB + FCM (Recommended)» → «Firebase RTDB + FCM»: sul polso lo spazio serve al nome."""
    return re.sub(r"\s*\((?:recommended|consigliat[oa])\)\s*$", "", str(label or ""), flags=re.I).strip()


def synth_question(question, ui):
    """La domanda COMPLETA e di senso compiuto per il polso (l'utente 12/09 10:54): il succo deterministico
    (ui.question_gist) se sta in hooks.ask_notify.synth_max_chars; altrimenti una sintesi col modello
    (`claude -p`, hooks.ask_notify.synth_model, come il recap); altrimenti il testo com'e' (chi lo mostra lo
    manda a capo e lo tronca)."""
    a = CFG["hooks"].get("ask_notify") or {}
    mx = int(a.get("synth_max_chars") or 88)
    q = " ".join(str(question or "").split())
    g = ui.question_gist(q, mx)
    if g or not q:
        return g
    model = str(a.get("synth_model") or "")
    if model:
        claude = cm.claude_bin()   # anche dai daemon partiti dal cron, senza ~/.local/bin nel PATH
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}   # account di default (T68)
        try:
            p = subprocess.run([claude, "-p", M("answer.synth_prompt", n=mx - 8, question=q), "--model", model, "--max-turns", "1"],
                               capture_output=True, text=True, timeout=int(a.get("synth_timeout_s") or 20), env=env,
                               cwd=str(Path(cm.expand(CFG["state_dir"]))))
            s = " ".join(p.stdout.split()).strip("«»\"' ")
            if p.returncode == 0 and 0 < len(s) <= mx + 20:
                return s[:mx]
        except (OSError, subprocess.SubprocessError):
            pass
    return q


def notify_lines(p, name, on_screen, ui):
    """La resa da polso (≤ 8 righe da ≤ 22): «❓ nome», la domanda COMPLETA (il suo succo, o una sintesi) su
    ≤ 4 righe, le opzioni «n etichetta» una per riga. Torna (righe, etichette)."""
    tool = p.get("tool_name") or "?"
    ti = p.get("tool_input") or {}
    questions = ti.get("questions") if isinstance(ti, dict) else None
    q0 = (questions or [{}])[0] if isinstance(questions, list) and questions else {}
    header, question, options = "", "", []
    descs = []
    if on_screen:
        header, question, options, _ = on_screen
        descs = [d for _, _, _, d in options]
        options = [label for _, label, _, _ in options]
    # il payload ha la domanda intera e non spezzata a righe: vince sullo schermo (che da' i numeri delle opzioni)
    question = str(q0.get("question") or "") or question
    if isinstance(q0.get("options"), list):
        pl = [(str(o.get("label") or ""), str(o.get("description") or "")) for o in q0["options"] if isinstance(o, dict)]
        if not options:
            options, descs = [l for l, _ in pl], [d for _, d in pl]
        elif not any(descs):
            by_label = {clean_label(l): d for l, d in pl}
            descs = [by_label.get(clean_label(o), "") for o in options]
    options = [clean_label(o) for o in options]
    descs = (descs + [""] * len(options))[:len(options)]
    icon = icon_of(name)
    header = header or str(q0.get("header") or "")
    # larghezza piena (l'utente 12/09 11:14): righe intere, la prima la piu' lunga («❓ 🔴 master · Trasporto»)
    lines = [ui.join(f"{icon} {ui.short_name(name)}".replace("  ", " "), header)]
    if question or options:
        lines.append(ui.line(synth_question(question, ui) or "?"))
        # il testo dice solo quello che i tasti non dicono (l'utente 12/09 11:12): le opzioni si elencano se hanno
        # una descrizione, se sono piu' di tre (i tasti sono due + Apri) o se non ci saranno tasti (senza tmux)
        if any(descs) or len(options) > 3 or not name or not on_screen:
            lines += [ui.join(f"{i + 1} {o}", d) for i, (o, d) in enumerate(zip(options, descs))]
    else:
        lines.append(ui.line(f"{tool} {_detail(ti)}"))
    # oltre 8 righe se servono: la domanda intera vale piu' del tetto (il polso scorre)
    return lines[:max(ui.MAX_LINES, 5 + 3 * len(options))], options


def notify_text(p, name, on_screen):
    """Il messaggio: domanda e opzioni dallo schermo (fedeli ai numeri che `answer` userà); senza
    schermo, dal tool_input; per un permesso, il tool e il suo dettaglio."""
    tool = p.get("tool_name") or "?"
    ti = p.get("tool_input") or {}
    questions = ti.get("questions") if isinstance(ti, dict) else None
    q0 = (questions or [{}])[0] if isinstance(questions, list) and questions else {}
    header, question, options = "", "", []
    if on_screen:
        header, question, options, _ = on_screen
        options = [(n, label) for n, label, _, _ in options]
    header = header or str(q0.get("header") or "")
    question = question or str(q0.get("question") or "")
    if not options and isinstance(q0.get("options"), list):
        options = [(i + 1, str(o.get("label") or "")) for i, o in enumerate(q0["options"]) if isinstance(o, dict)]
    lines = []
    if question or options:
        lines.append(M("answer.notify_question", name=name, hq=f"{header}: {question}" if header else question))
    else:
        lines.append(M("answer.notify_tool", name=name, tool=tool, detail=_detail(ti)))
    lines += [f"{n}. {label}" for n, label in options]
    if isinstance(questions, list) and len(questions) > 1:
        lines.append(M("answer.notify_more", n=len(questions) - 1))
    return "\n".join(lines)


def notify():
    bot = _load("cm-bot")
    if not bot.token():
        return 0
    # dal 16/09 (passo 4 del ritiro di Telegram): la domanda la annuncia l'orologio. Telegram parla solo quando il
    # polso NON sta ricevendo (non accoppiato, oppure relay o rete giu'): li' il telefono e' l'unico canale rimasto.
    if _load("cm-core").watch_receiving():
        return 0
    try:
        p = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (ValueError, OSError):
        p = {}
    chats = sorted(bot.allowed_chats())
    if not chats:
        return 0
    name = my_tmux_name()
    on_screen = None
    if name:
        time.sleep(float((CFG["hooks"].get("ask_notify") or {}).get("delay_s") or 0))
        on_screen = parse(screen(name))
    shown = name or Path(p.get("cwd") or "").name or "?"
    ui = _load("cm-core")   # le funzioni di testo (riga, sintesi della domanda, nome corto)
    lines, labels = notify_lines(p, shown, on_screen, ui)
    if name:
        lines.append(ui.line(M("answer.notify_hint", n=2 if len(labels) >= 2 else 1, name=name)))
    text = "\n".join(lines)
    for c in chats:
        reply_id = bot.reply(c, text)   # notifica NORMALE: una domanda aspetta l'utente
        if reply_id is None:
            bot.log(f"notify to {c} FAILED")
    hook = _load("cm-hook")
    hook.ledger("ask-notified", p, tool=p.get("tool_name", ""), tmux=name, chats=len(chats))
    bot.log(M("answer.notify_sent", n=len(chats), name=shown))
    return 0


def main(argv):
    if argv[:1] == ["--notify"]:
        return notify()
    if len(argv) < 2:
        print(M("answer.usage"), file=sys.stderr)
        return 2
    name = argv[0]
    t = term_for(name)
    if t is None:
        print(M("answer.background" if background(name) else "answer.no_session", name=name), file=sys.stderr)
        return 1
    if isinstance(t, Remote):
        TERMS[name] = t
    if argv[1] == "--show":
        return show(name)
    text = ""
    picks = []
    chat = False
    rest = argv[1:]
    i = 0
    while i < len(rest):
        if rest[i] == "--text" and i + 1 < len(rest):
            text = rest[i + 1]; i += 2
        elif rest[i] == "--chat":
            chat = True; i += 1
        elif rest[i].isdigit():
            picks.append(int(rest[i])); i += 1
        else:
            print(M("answer.usage"), file=sys.stderr)
            return 2
    if not picks:
        # senza numero: la voce fissa in coda, «Chat about this» o «Type something.» + il testo
        if not chat and not text.strip():
            print(M("answer.usage"), file=sys.stderr)
            return 2
        rc = choose(name, 0, "" if chat else text, "chat" if chat else "type")
        if rc:
            return rc
        time.sleep(0.8)
        picks = []
    for n in picks:
        rc = choose(name, n, text)
        if rc:
            return rc
        time.sleep(0.8)   # una domanda successiva ha bisogno di un attimo per comparire
    left = parse(screen(name))
    if left:
        print(M("answer.more", name=name))
        return show(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
