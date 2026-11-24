#!/usr/bin/env python3
"""I messaggi di Telegram del mattino e della sera, e le voci degli altri script (17/11/2026, supervisor-avvisi-telegram-17-11).

  supervisor recap morning [--send]                       il mattino (dal cron alle recap.morning_time, 08:00)
  supervisor recap add --kind K "testo" [--date AAAA-MM-GG]   una voce per il recap delle 20 di quel giorno
  (supervisor recap --send, alle 20, manda evening_text)

Su Telegram tre tipi di messaggio e nient'altro, scelti dal maintainer il 17/11:
1. l'allarme, subito, solo se serve un'azione (relay fermo o push fallite da oltre 15 minuti: cm-relay; quota sopra il
   92%: cm-guard), due righe col link all'app;
2. il mattino (dal 20/11 in HTML, come il recap delle 20): le quote dei due account con l'ora di azzeramento, Firebase del mese, lo spazio dei server
   (recap.servers_file, lo scrive spazio-server.py), i monitoraggi sopra soglia e le scadenze del calendario;
3. il recap delle 20: uscito (i compiti chiusi oggi), ti aspetta (le voci aperte col link), stanotte (la coda della
   notte), la notte appena passata (night-done.jsonl: `night run --send` non manda piu' a parte) e le voci del giorno
   di `recap add` (i rilasci di Claude Code, l'adozione del lunedi').
Italiano, niente emoji, la prima riga dice cosa e' successo. Le voci di `recap add` stanno in
<state_dir>/recap-extra.jsonl ({at, day, kind, text}), tenute RECAP_EXTRA_DAYS giorni.
"""
import calendar
import datetime as dt
import hashlib
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
D = CFG.get("recap") or {}
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿⏩-⏺️‍]")
KIND_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,30}")
RECAP_EXTRA_DAYS = 30
WAIT_MAX = 8
SERVERS_FILE = "~/Desktop/workspaces/.claude/spazio-server.json"
SERVER_PCT = 80
SERVER_HIGH = {"alto", "critico", "attenzione", "pieno", "high", "critical", "warn", "warning", "alarm"}
FIREBASE_MONTH_GB = 10


def today():
    """Oggi, o CM_TODAY (YYYY-MM-DD) nei test, come cm-calendar.today() (24/11/2026, supervisor-test-oggi-24-11)."""
    t = os.environ.get("CM_TODAY")
    return dt.date.fromisoformat(t) if t else dt.date.today()


def within_today(now):
    """now, tenuto dentro il giorno di CM_TODAY quando e' impostato: un test che ha fissato il giorno non vede passare la
    mezzanotte ne' finire il mese fra la sua lettura della data e quella dello script."""
    t = os.environ.get("CM_TODAY")
    if not t:
        return now
    d = dt.date.fromisoformat(t)
    a = time.mktime(d.timetuple())
    return min(max(now, a), time.mktime((d + dt.timedelta(days=1)).timetuple()) - 1)


def clean(text):
    return " ".join(EMOJI.sub("", str(text or "")).split())


def state_dir():
    return Path(cm.expand(CFG["state_dir"]))


def day_label(d):
    return f"{M('avvisi.days').split(',')[d.weekday()]} {d.strftime('%d/%m')}"


def when(epoch):
    t = dt.datetime.fromtimestamp(float(epoch))
    return M("avvisi.when", day=day_label(t.date()), t=t.strftime("%H:%M"))


def num(x, digits=1):
    return f"{x:.{digits}f}".replace(".", ",") if M("avvisi.decimal") == "," else f"{x:.{digits}f}"


def plural(n, key):
    return M(f"avvisi.{key}_one" if n == 1 else f"avvisi.{key}", n=n)


def read_jsonl(p):
    out = []
    try:
        for line in Path(p).read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ recap add
def extra_path():
    return state_dir() / "recap-extra.jsonl"


def cmd_add(argv):
    kind, day, words = None, today(), []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--kind", "--tipo") and i + 1 < len(argv):
            kind, i = argv[i + 1], i + 2
            continue
        if a in ("--date", "--data") and i + 1 < len(argv):
            try:
                day = dt.date.fromisoformat(argv[i + 1])
            except ValueError:
                print(M("avvisi.add_bad_date", date=argv[i + 1]), file=sys.stderr)
                return 2
            i += 2
            continue
        words.append(a)
        i += 1
    text = clean(" ".join(words))[:300]
    if not kind or not KIND_RE.fullmatch(kind) or not text:
        print(M("avvisi.add_usage"), file=sys.stderr)
        return 2
    cut = (today() - dt.timedelta(days=RECAP_EXTRA_DAYS)).isoformat()
    rows = [r for r in read_jsonl(extra_path()) if str(r.get("day") or "") >= cut]
    rows.append({"at": int(time.time()), "day": day.isoformat(), "kind": kind, "text": text})
    p = extra_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(p)
    print(M("avvisi.added", kind=kind, day=day.strftime("%d/%m")))
    return 0


def extras(day):
    out = OrderedDict()
    for r in read_jsonl(extra_path()):
        if r.get("day") == day.isoformat() and r.get("kind") and r.get("text"):
            out.setdefault(r["kind"], []).append(clean(r["text"]))
    return out


def kind_label(kind):
    key = f"avvisi.kind_{kind}"
    own = cm.messages(CFG.get("language", "en")).get(key) or cm.messages("en").get(key)
    return own or kind.replace("-", " ").capitalize()


# ------------------------------------------------------------------ la sera
# 19/11/2026 (supervisor-recap-telegram-19-11, formato approvato dal maintainer alle 20:10): il recap del 19/11 era un
# paragrafo solo, coi titoli tagliati a 90 caratteri a meta' parola («… eventi Conversio,») e «gerardosacco.com:» ripetuto a
# ogni voce. Ora: una riga col giorno e i conteggi; TI ASPETTANO (al massimo WAIT_SHOW voci «progetto: cosa, cosa serve» col
# link); FATTI OGGI per progetto, una riga ciascuno coi titoli brevi, in un <blockquote expandable>; HTML di Telegram.
WAIT_SHOW = 5
DONE_TITLE_MAX = 60
WAIT_TITLE_MAX = 50
PREFIX_RE = re.compile(r"^[\w.:-]+:\s+")
TG_MAX = 4000


def esc(s):
    return html.escape(str(s or ""), quote=False)


def cut_words(text, n):
    """Parole intere entro n caratteri, mai una virgola, due punti o parentesi aperta in coda; mai «…»."""
    t = " ".join(str(text or "").split())
    if len(t) <= n:
        return t.rstrip(" ,;:(")
    c = t[:n + 1]
    c = c[:c.rfind(" ")] if " " in c else t[:n]
    return c.rstrip(" ,;:·-(")


def closed_on(day):
    """I compiti chiusi (done) quel giorno, dal registro: [{project, title, short}]; [] senza registro."""
    try:
        tk = _load("cm-tasks")
        con = tk.connect(create=False)
    except Exception:   # noqa: BLE001
        return []
    if con is None:
        return []
    a = int(time.mktime(day.timetuple()))
    b = a + 86400
    try:
        rows = con.execute("SELECT t.id, t.title, t.project, t.session, t.contract FROM closures c JOIN tasks t ON t.id=c.task "
                           "WHERE t.state='done' AND c.at>=? AND c.at<? ORDER BY c.at", (a, b)).fetchall()
    except Exception:   # noqa: BLE001
        rows = []
    finally:
        con.close()
    out = []
    for r in rows:
        try:
            short = (json.loads(r[4] or "{}").get("task") or {}).get("short")
        except (ValueError, AttributeError):
            short = None
        proj = os.path.basename(str(r[2] or "").rstrip("/")) or str(r[3] or "") or "-"
        out.append({"project": proj, "title": clean(r[1] or r[0]), "short": clean(short) if short else None})
    return out


def done_item(d):
    """Il titolo breve: task.short, altrimenti il titolo senza il prefisso «progetto: », a parola intera."""
    if d.get("short"):
        return d["short"]
    t = d["title"]
    m = PREFIX_RE.match(t)
    if m and len(t) > m.end():
        t = t[m.end():]
    return cut_words(t, DONE_TITLE_MAX)


def need_of(a):
    """Cosa serve alla voce, in poche parole."""
    k = str(a.get("kind") or "")
    own = cm.messages(CFG.get("language", "en")).get(f"avvisi.need_{k}") or cm.messages("en").get(f"avvisi.need_{k}")
    return own or M("avvisi.need_other")


def waiting():
    """Le voci aperte che aspettano la persona (Tocca a te), col link dell'app."""
    try:
        AT = _load("cm-attese")
        items = [a for a in AT.collect() if a.get("state", "open") == "open"]
    except Exception:   # noqa: BLE001 — senza registro ne' stato il recap esce lo stesso
        return []
    # 23/11/2026 (supervisor-promemoria-carte-23-11): prima le carte di ok, scelta e prova di un compito non in pausa
    try:
        first = {a["id"] for a in AT.blocking_cards(items, AT.held_tasks())}
    except Exception:   # noqa: BLE001
        first = set()
    return [a for a in items if a["id"] in first] + [a for a in items if a["id"] not in first]


def night_queue():
    return read_jsonl(cm.expand((CFG.get("night") or {}).get("queue_file") or "~/.cc-supervisor/night-queue.jsonl"))


def night_done(day):
    return [r for r in read_jsonl(state_dir() / "night-done.jsonl") if str(r.get("finished") or "").startswith(day.isoformat())]


def app_url():
    return str((CFG.get("relay") or {}).get("web_app_url") or "").rstrip("/")


def evening_text(day=None):
    """Il recap delle 20 in HTML di Telegram (parse_mode HTML)."""
    day = day or today()
    out, waits, queue, done, ex = closed_on(day), waiting(), night_queue(), night_done(day), extras(day)
    full = M("avvisi.days_full").split(",")[day.weekday()]
    head = [f"{full} {day.strftime('%d/%m')}"]
    if waits:
        head.append(plural(len(waits), "n_waiting"))
    head.append(plural(len(out), "n_done") if out else M("avvisi.nothing_done"))
    lines = [f"<b>{esc(' · '.join(head))}</b>"]
    if waits:
        lines += ["", f"<b>{esc(M('avvisi.wait_title'))}</b>"]
        for a in waits[:WAIT_SHOW]:
            proj = a.get("session") or ((a.get("decision") or {}).get("where")) or "supervisor"   # la Prima raccolta non ha sessione
            what = cut_words(a.get("title") or a.get("text") or a.get("kind"), WAIT_TITLE_MAX)
            link = a.get("link")
            tail = f' <a href="{html.escape(link)}">{esc(M("avvisi.open"))}</a>' if link else ""
            lines.append(f"• {esc(proj)}: {esc(what)}, {esc(need_of(a))}{tail}")
        if len(waits) > WAIT_SHOW:
            more = esc(M("avvisi.wait_more_app", n=len(waits) - WAIT_SHOW))
            lines.append(f'{more} <a href="{html.escape(app_url())}">{esc(M("avvisi.open_app"))}</a>' if app_url() else more)
    if out:
        groups = OrderedDict()
        for d in out:
            groups.setdefault(d["project"], []).append(done_item(d))
        body = [f"{esc(p)}: {' · '.join(esc(x) for x in xs)}" for p, xs in groups.items()]
        lines += ["", f"<b>{esc(M('avvisi.done_title'))}</b>", "<blockquote expandable>" + "\n".join(body) + "</blockquote>"]
    lines.append("")
    if queue:
        dirs = ", ".join(OrderedDict((os.path.basename(str(r.get("dir") or "").rstrip("/")) or "?", 1) for r in queue))
        lines.append(esc(M("avvisi.tonight", jobs=plural(len(queue), "jobs"), dirs=dirs)))
    else:
        lines.append(esc(M("avvisi.tonight_none")))
    if done:
        ok = sum(1 for r in done if r.get("rc") == 0)
        lines.append(esc(M("avvisi.night", jobs=plural(len(done), "jobs"), ok=plural(ok, "ok"))))
        for r in done:
            lines.append(esc(M("avvisi.night_item", mark="ok" if r.get("rc") == 0 else "KO", dir=os.path.basename(str(r.get("dir") or "").rstrip("/")) or "?",
                               prompt=cut_words(clean(r.get("prompt")), 70))))
    for kind, texts in ex.items():
        lines.append(esc(M("avvisi.extra", kind=kind_label(kind), items="; ".join(texts))))
    text = "\n".join(lines)
    if len(text) > TG_MAX and out:   # Telegram: 4096 al massimo; si accorciano i fatti, mai a meta' di un tag
        while len(text) > TG_MAX and len(body) > 1:
            body.pop()
            q = "<blockquote expandable>" + "\n".join(body + [esc(M("avvisi.done_more"))]) + "</blockquote>"
            text = re.sub(r"<blockquote expandable>.*?</blockquote>", lambda _m: q, text, count=1, flags=re.S)
    return text


# ------------------------------------------------------------------ le menzioni GitHub
# 19/11/2026 (supervisor-recap-telegram-19-11): le righe [MENZIONE] e [COMMENTO DA UTENTE] degli script del Chromebook
# (gh-notify-watch.py, issue-watch.py, segnalazioni.py) erano il corpo inglese della PR tagliato. `supervisor recap github`
# legge quel blocco sullo stdin e scrive una riga italiana: «progetto: chi ha fatto cosa su dove (di cosa si tratta) — link».
GH_HEAD = re.compile(r"^\[(?P<tag>[A-Z][A-Z -]+)\]\s+(?P<rest>.*)$")
GH_URL = re.compile(r"https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/(?P<kind>pull|issues|discussions)/(?P<num>\d+)(?P<frag>#\S*)?")
GH_REPO_IN_BODY = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)")
GH_REPO_TOK = re.compile(r"^(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:#(?P<num>\d+))?$")
GH_DATE_TOK = re.compile(r"^\d\d-\d\dT\d\d:\d\d")
GH_TYPES = {"Issue", "PullRequest", "Discussion", "Release", "Commit", "CheckSuite"}
# 20/11 (supervisor-recap-github-campi-20-11): i tipi che conosciamo; un blocco d'altro tipo passa com'e'
GH_KNOWN = {"MENZIONE", "RIFERIMENTO", "RISPOSTA-TUO-THREAD", "STATO", "CITAZIONE", "ISSUE DA UTENTE", "PR DA UTENTE", "COMMENTO DA UTENTE"}
# 20/11: il riassunto in pochi secondi. Senza le impostazioni dell'utente (--setting-sources project, nella cartella di stato
# che non ne ha) niente plugin ne' hook; niente MCP, strumenti, comandi, sessione salvata. Sul Chromebook claude -p senza
# queste opzioni impiegava 38-61 s a riga e a 60 s scadeva. --bare no: non legge l'accesso OAuth.
GH_LEAN = ["--setting-sources", "project", "--strict-mcp-config", "--tools", "", "--disable-slash-commands", "--no-session-persistence"]
GH_SUMMARY_TIMEOUT_S = 25
GH_ABOUT_MAX = 70


def repo_label(repo):
    """«hol-guard» → «HOL Guard»: le parole corte in maiuscolo, le altre con l'iniziale."""
    return " ".join(w.upper() if len(w) <= 3 else w.capitalize() for w in re.split(r"[-_]", repo) if w)


def github_owner():
    """Il login GitHub della persona: recap.github_owner, altrimenti `gh api user` una volta (in <state_dir>/github-owner)."""
    own = str((CFG.get("recap") or {}).get("github_owner") or "").strip()
    if own:
        return own
    p = state_dir() / "github-owner"
    try:
        return p.read_text().strip() or None
    except OSError:
        pass
    try:
        r = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True, timeout=10)
        login = r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        login = ""
    if login:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(login)
        except OSError:
            pass
    return login or None


def gh_summary(title, body):
    """Di cosa si tratta, in italiano, in poche parole (claude -p leggero, modello piccolo); None se non risponde in tempo."""
    prompt = M("avvisi.gh_prompt", title=title or "-", body=body[:600])
    base = [cm.claude_bin(), "-p", prompt, "--model", str((CFG.get("recap") or {}).get("summary_model") or "haiku"), "--max-turns", "1"]
    t0 = time.time()
    for extra in (GH_LEAN, []):
        left = GH_SUMMARY_TIMEOUT_S - (time.time() - t0)
        if left < 2:
            return None
        try:   # un gruppo di processi a se': allo scadere va via tutto (un figlio rimasto terrebbe aperta la pipe)
            p = subprocess.Popen(base + extra, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL,
                                 cwd=str(state_dir()), start_new_session=True)
        except OSError:
            return None
        try:
            stdout, stderr = p.communicate(timeout=left)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, 9)
            except OSError:
                pass
            p.communicate()
            return None
        out = " ".join((stdout or "").split()).strip(" .«»\"'")
        if p.returncode == 0 and out:
            return out if len(out) <= GH_ABOUT_MAX + 10 else None
        if "unknown option" not in (stderr or "").lower():   # una CLI che non conosce le opzioni leggere: si riprova senza
            return None
    return None


def parse_github(block):
    """{tag, repo, owner, num, author, title, url, kind, frag, body} di un blocco, o None se non e' un blocco GitHub che conosciamo."""
    rows = [l.strip() for l in str(block or "").strip().splitlines() if l.strip()]
    if not rows:
        return None
    m = GH_HEAD.match(rows[0])
    if not m or m.group("tag").strip() not in GH_KNOWN:
        return None
    tag, rest = m.group("tag").strip(), m.group("rest")
    title = ""
    if "«" in rest and "»" in rest[rest.index("«"):]:
        a, b = rest.index("«"), rest.rindex("»")
        title, rest = rest[a + 1:b].strip(), rest[:a] + " " + rest[b + 1:]
    owner = repo = num = author = None
    for t in rest.split():
        rm = GH_REPO_TOK.match(t)
        if repo is None and rm and "://" not in t:
            owner, repo, num = rm.group("owner"), rm.group("repo"), rm.group("num") or num
        elif re.fullmatch(r"#\d+", t):
            num = t[1:]
        elif GH_DATE_TOK.match(t) or t in GH_TYPES:
            continue
        elif author is None and repo is not None:
            author = t
    text = " ".join(rows[1:])
    ut = GH_URL.search(text)
    u = ut or GH_URL.search(rows[0])
    if not u:
        return None
    if author is None and ut:   # [MENZIONE]: «  autore https://… testo»
        before = text[:ut.start()].split()
        author = before[-1] if before else None
    body = text[ut.end():].strip() if ut else text
    return {"tag": tag, "owner": owner or u.group("owner"), "repo": repo or u.group("repo"), "num": num or u.group("num"), "author": author,
            "title": title, "url": u.group(0), "kind": u.group("kind"), "frag": u.group("frag"), "url_repo": u.group("repo"), "body": body}


def github_prefix(g, me):
    """Il progetto della riga: il repo del blocco se e' della persona; altrimenti un suo progetto linkato nel titolo o nel
    testo; altrimenti il repo del blocco. Senza sapere chi e' la persona: il primo repo linkato diverso da quello del link."""
    if me and g["owner"] == me:
        return g["repo"]
    links = GH_REPO_IN_BODY.findall(f"{g['title']} {g['body']}")
    if me:
        mine = next((r for o, r in links if o == me), None)
    else:
        mine = next((r for o, r in links if r != g["url_repo"]), None)
    return mine or g["repo"]


def github_line(block, as_html=False):
    """La riga italiana di un blocco GitHub che conosciamo; None se non lo riconosce (chi chiama lo passa com'e')."""
    g = parse_github(block)
    if not g:
        return None
    prefix = github_prefix(g, github_owner())
    who = g["author"] or M("avvisi.gh_someone")
    commented = bool(g["frag"]) or g["tag"].startswith("COMMENTO")
    is_pr = g["kind"] == "pull"
    label = repo_label(g["url_repo"])
    if g["tag"].startswith("COMMENTO"):
        what = M("avvisi.gh_commented_pr" if is_pr else "avvisi.gh_commented_issue", n=g["num"])
        line = f"{prefix}: {who} {what}"
        link_word = M("avvisi.gh_link_comment")
    else:
        key = ("avvisi.gh_commented_pr_on" if commented else "avvisi.gh_opened_pr") if is_pr else \
              ("avvisi.gh_commented_issue_on" if commented else "avvisi.gh_opened_issue")
        about = gh_summary(g["title"], g["body"])
        if not about:
            about = f"«{g['title']}»" if g["title"] else cut_words(g["body"], GH_ABOUT_MAX)
        line = f"{prefix}: {who} {M(key, repo=label)}" + (f" ({about})" if about else "")
        link_word = M("avvisi.gh_link_pr" if is_pr else "avvisi.gh_link_issue")
    line = " ".join(line.split())
    if as_html:
        return f'{esc(line)} — <a href="{html.escape(g["url"])}">{esc(link_word)}</a>'
    return f"{line} — {g['url']}"


def cmd_github(argv):
    """supervisor recap github [--html] < blocchi: una riga per blocco (separati da una riga vuota o da un nuovo «[TAG]»);
    un blocco che non riconosce esce com'e' (20/11: prima spariva, e il ripiego di watch-all.sh non scattava)."""
    raw = sys.stdin.read()
    blocks, cur = [], []
    for l in raw.splitlines():
        if GH_HEAD.match(l.strip()) and cur:
            blocks.append("\n".join(cur))
            cur = []
        if l.strip():
            cur.append(l)
    if cur:
        blocks.append("\n".join(cur))
    n = 0
    for b in blocks:
        line = github_line(b, "--html" in argv)
        print(line if line else (esc(b) if "--html" in argv else b))
        n += 1
    return 0 if n else 1


# ------------------------------------------------------------------ il mattino
def quota_file(acc):
    conf = cm.expand(CFG["accounts"][acc]["config_dir"])
    f = Path(cm.expand(CFG["quota"]["source"])) / f"quota-{hashlib.sha256(str(conf).encode()).hexdigest()[:8]}.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None


def quota_lines(now, key="avvisi.quota", none_key="avvisi.quota_none"):
    """Una riga per account: la settimana con il giorno e l'ora dell'azzeramento; le cinque ore solo sopra la soglia
    della guardia. Torna (righe, quante vicino alla soglia)."""
    near_pct = float((CFG.get("quota") or {}).get("warn_pct", 85))
    warn = float((CFG.get("guard") or {}).get("warn_pct", 92))
    lines, near = [], 0
    for acc in CFG["accounts"]:
        d = quota_file(acc)
        if not d:
            lines.append(M(none_key, account=acc))
            continue
        w, w_at = d.get("weekly_used_pct"), d.get("weekly_resets_at")
        h, h_at = d.get("five_hour_used_pct"), d.get("five_hour_resets_at")
        parts = []
        if w is not None:
            parts.append(M("avvisi.quota_week", pct=round(float(w)), when=when(w_at) if w_at else "-"))
        if h is not None and (w is None or float(h) >= warn):
            parts.append(M("avvisi.quota_5h", pct=round(float(h)), t=dt.datetime.fromtimestamp(float(h_at)).strftime("%H:%M") if h_at else "-"))
        if not parts:
            lines.append(M(none_key, account=acc))
            continue
        if max(float(x) for x in (w, h) if x is not None) >= near_pct:
            near += 1
        lines.append(M(key, account=acc, parts="; ".join(parts)))
    return lines, near


def firebase_line(now, key="avvisi.firebase"):
    """Firebase del mese: la stima del relay (usage-firebase.json, come doctor.firebase_usage) sommata sui giorni del mese."""
    rd = Path(cm.expand((CFG.get("relay") or {}).get("dir") or "~/.cc-supervisor/relay"))
    try:
        usage = json.loads((rd / "usage-firebase.json").read_text())
    except (OSError, ValueError):
        return None, False
    try:
        devices = len(json.loads((rd / "devices.json").read_text()) or {}) or 1
    except (OSError, ValueError):
        devices = 1
    today = dt.date.fromtimestamp(now)
    month = today.strftime("%Y-%m-")
    b = sum(float(r.get("result_bytes", 0)) + float(r.get("state_bytes", 0)) * devices
            for k, r in (usage or {}).items() if isinstance(r, dict) and str(k).startswith(month))
    gb = b / 1e9
    proj = gb * calendar.monthrange(today.year, today.month)[1] / max(1, today.day)
    name = M("avvisi.months").split(",")[today.month - 1]
    return M(key, gb=num(gb), limit=FIREBASE_MONTH_GB, month=name, pct=round(100 * gb / FIREBASE_MONTH_GB),
             proj=num(proj, 0)), proj > FIREBASE_MONTH_GB


def _pct(v):
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(v if v is not None else ""))
    return float(m.group(0).replace(",", ".")) if m else None


def servers_line():
    """Lo spazio dei server (recap.servers_file, campi livello, liberi_gb, uso, ora): solo quelli sopra
    recap.server_pct o con un livello d'allarme. Il file puo' essere {nome: {...}}, [{nome, ...}] o {"server(s)": ...}."""
    p = Path(cm.expand(D.get("servers_file") or SERVERS_FILE))
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None, 0
    if isinstance(data, dict):
        data = data.get("servers", data.get("server", data))
    items = []
    if isinstance(data, dict):
        items = [(str(k), v) for k, v in data.items() if isinstance(v, dict)]
    elif isinstance(data, list):
        items = [(str(v.get("nome") or v.get("name") or v.get("server") or v.get("host") or "?"), v) for v in data if isinstance(v, dict)]
    if not items:
        return None, 0
    lim = float(D.get("server_pct", SERVER_PCT))
    high = []
    for name, v in items:
        uso = _pct(v.get("uso"))
        if (uso is not None and uso >= lim) or str(v.get("livello") or "").lower() in SERVER_HIGH:
            free = _pct(v.get("liberi_gb"))
            high.append(M("avvisi.server_item", name=name, uso="?" if uso is None else round(uso),
                          free=("" if free is None else M("avvisi.server_free", gb=round(free)))))
    if not high:
        return M("avvisi.servers_ok", pct=round(lim)), 0
    rest = M("avvisi.servers_rest", pct=round(lim)) if len(high) < len(items) else ""
    return M("avvisi.servers", items=", ".join(high), rest=rest), len(high)


def monitors_line():
    try:
        mon = _load("cm-monitor")
        cs = mon.cards()
    except Exception:   # noqa: BLE001
        return None, 0
    if not cs:
        return None, 0
    hot = [clean(mon.line_of(c)) for c in cs if c.get("status") == "watch"]
    return (M("avvisi.monitors", items="; ".join(hot)) if hot else M("avvisi.monitors_none")), len(hot)


def deadlines_line():
    try:
        cal = _load("cm-calendar")
        if not cal.url():
            return None
        items, err = cal.deadlines()
    except Exception:   # noqa: BLE001
        return None
    if not items:
        return None
    return M("avvisi.deadlines", items="; ".join(clean(cal.line(e)) for e in items))


def server_items():
    """(voci sopra soglia, frase sugli altri o None, quante) dello spazio dei server, per il mattino in HTML."""
    p = Path(cm.expand(D.get("servers_file") or SERVERS_FILE))
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None, None, 0
    if isinstance(data, dict):
        data = data.get("servers", data.get("server", data))
    items = []
    if isinstance(data, dict):
        items = [(str(k), v) for k, v in data.items() if isinstance(v, dict)]
    elif isinstance(data, list):
        items = [(str(v.get("nome") or v.get("name") or v.get("server") or v.get("host") or "?"), v) for v in data if isinstance(v, dict)]
    if not items:
        return None, None, 0
    lim = float(D.get("server_pct", SERVER_PCT))
    high = []
    for name, v in items:
        uso = _pct(v.get("uso"))
        if (uso is not None and uso >= lim) or str(v.get("livello") or "").lower() in SERVER_HIGH:
            free = _pct(v.get("liberi_gb"))
            high.append(M("avvisi.server_item", name=name, uso="?" if uso is None else round(uso),
                          free=("" if free is None else M("avvisi.server_free", gb=round(free)))))
    if not high:
        return [], M("avvisi.servers_all_ok", pct=round(lim)), 0
    return high, (M("avvisi.servers_rest_line", pct=round(lim)) if len(high) < len(items) else None), len(high)


def is_firebase(c):
    """Il monitoraggio di Firebase (il consumo misurato da Firebase stesso): l'id o il titolo lo nominano."""
    return "firebase" in f"{c.get('id') or ''} {c.get('title') or ''}".lower()


def monitor_cards():
    try:
        return _load("cm-monitor").cards() or []
    except Exception:   # noqa: BLE001
        return []


def a_open(link):
    return f' <a href="{html.escape(link)}">{esc(M("avvisi.open"))}</a>' if link else ""


def morning_text(now=None):
    """20/11/2026 (supervisor-mattino-formattato-20-11, la segnalazione del maintainer alle 08:09): il mattino in HTML di
    Telegram come il recap delle 20 (parse_mode HTML): la prima riga in grassetto col giorno e quante voci sono vicino alla
    soglia, poi una sezione per blocco (QUOTE, FIREBASE, SERVER, MONITORAGGI, SCADENZE), una voce per riga, i link con un
    testo breve. Firebase una volta sola: il monitoraggio di Firebase, se c'e', e' la misura (Firebase stesso); senza, la
    stima del relay (solo cio' che il relay manda), detta come stima. Le scadenze: data, quando, cosa; mai l'id della
    pratica ne' la cartella, che restano alla master."""
    now = within_today(now or time.time())
    day = dt.date.fromtimestamp(now)
    near = 0
    blocks = []

    def section(title, items, tail=None):
        if items or tail:
            blocks.append("\n".join([f"<b>{esc(title)}</b>"] + [f"• {x}" for x in items] + ([tail] if tail else [])))

    q, n = quota_lines(now, "avvisi.quota_item", "avvisi.quota_none_item")
    near += n
    section(M("avvisi.quota_title"), [esc(x) for x in q])
    cards = monitor_cards()
    fbc = next((c for c in cards if is_firebase(c)), None)
    if fbc:   # la misura: le parti del suo testo una per riga, il link alla sua scheda
        text = fbc.get("verdict") or _load("cm-monitor").line_of(dict(fbc, link=None)).split(": ", 1)[-1]
        parts = [x.strip(" .") for x in re.split(r";\s+", str(text)) if x.strip(" .")]
        near += int(fbc.get("status") == "watch")
        section(M("avvisi.firebase_title"), [esc(x) for x in parts[:-1]] + [esc(parts[-1]) + a_open(fbc.get("link"))] if parts else [])
    else:
        fb, fb_high = firebase_line(now, "avvisi.firebase_estimate")
        if fb:
            near += int(fb_high)
            section(M("avvisi.firebase_title"), [esc(fb)])
    high, rest, n = server_items()
    near += n
    if high is not None:
        section(M("avvisi.servers_title"), [esc(x) for x in high], esc(rest) if rest else None)
    hot = [c for c in cards if c.get("status") == "watch" and c is not fbc]
    near += len(hot)
    section(M("avvisi.monitors_title"), [esc(f"{c['title']}: {c.get('verdict') or c.get('value') or ''}".rstrip(": ")) + a_open(c.get("link")) for c in hot])
    try:
        cal = _load("cm-calendar")
        items = cal.deadlines()[0] if cal.url() else []
    except Exception:   # noqa: BLE001
        cal, items = None, []
    section(M("avvisi.deadlines_title"),
            [esc(f"{dt.date.fromisoformat(e['date']).strftime('%d/%m')} ({cal.when(e)}) {e.get('summary') or '?'}") for e in items])
    # 23/11/2026 (supervisor-silenzio-assenso-23-11): i compiti chiusi per silenzio assenso (e quelli rimasti aperti col
    # controllo rosso) nelle ultime 24 ore
    try:
        tk = _load("cm-tasks")
        con = tk.connect(create=False)
        sil = tk.silences_since(con, int(now) - 86400) if con is not None else []
        if con is not None:
            con.close()
    except Exception:   # noqa: BLE001
        sil = []
    section(M("avvisi.silence_title"), [esc(M("avvisi.silence_done" if x["outcome"] == "done" else "avvisi.silence_red", short=x["short"],
                                              until=time.strftime("%d/%m %H:%M", time.localtime(int(x["until"]))))) for x in sil])
    full = M("avvisi.days_full").split(",")[day.weekday()]
    what = plural(near, "n_near") if near else M("avvisi.all_below")
    head = f"<b>{esc(full)} {day.strftime('%d/%m')} · {esc(what)}</b>"
    return "\n\n".join([head] + blocks)


def send(text, as_html=True):
    bot = _load("cm-bot")
    if not bot.token():
        print(M("bot.no_token", path=CFG["bot"]["token_file"]), file=sys.stderr)
        return 1
    print(M("recap.sent", n=bot.send(text, parse_mode="HTML" if as_html else None)))
    return 0


def cmd_morning(argv):
    text = morning_text()
    print(text)
    return send(text) if "--send" in argv or "--invia" in argv else 0


def main(argv):
    verb = argv[0] if argv else ""
    if verb == "add":
        return cmd_add(argv[1:])
    if verb in ("morning", "mattino"):
        return cmd_morning(argv[1:])
    if verb in ("evening", "sera"):
        print(evening_text())
        return 0
    if verb == "github":   # 19/11 (supervisor-recap-telegram-19-11): le menzioni GitHub in una riga italiana
        return cmd_github(argv[1:])
    print(M("avvisi.add_usage"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
