#!/usr/bin/env python3
"""supervisor sessions — quali sessioni Claude girano, di quale account, dove.

Serve perche' due account sono due inquilini separati su claude.ai: l'app mostra
le sessioni di quello con cui sei loggato e le altre spariscono dall'elenco pur
essendo vive sulla macchina. Qui si vedono tutte.

Fonte primaria (dal 09/09/2026, E1/F1): il registro ufficiale
`<config_dir>/sessions/<pid>.json` di OGNI account in configurazione — stato
(`busy`/`idle`/`shell`), riquadro tmux esatto, nome, link Remote Control, socket
dei peer. Due account possono condividere la cartella (symlink): si legge una
volta sola (realpath) e l'account si deduce dall'ambiente del processo (T12).

Riconciliazione con /proc (T11, T62): il registro invecchia — dopo un riavvio
restano i file dei pid morti, e un pid puo' essere riusato — quindi una voce
vale solo se `/proc/<pid>` esiste E `procStart` coincide con il campo 22 di
`/proc/<pid>/stat`. I processi claude NON registrati (versioni vecchie, `-p`
bare, server tmux morto) si aggiungono dalla scansione di /proc come faceva
sessioni.sh, con stato `?`.

VISTA: `aperta`/`STACCATA` da `tmux list-sessions` (T9: display-message risolve
session_attached rispetto al client corrente e da uno script risponde 0).
«aspetta una risposta»: prima il flag scritto dall'hook PermissionRequest
(fase 4, E2), poi la lettura dello schermo con DUE indizi (T13): elenco numerato
E «to select|to navigate|Tab to amend». Nome nudo in capture-pane (T1/T54).

CANALE: `(questa)` se il pid e' un antenato di questo processo; `nativo` se la
voce sta nel registro che il MIO account legge (stessa cartella, anche via
symlink: E1); `talk` altrimenti. E' la regola di precedenza della skill, fatta
dai dati e non a intuito.

VERSIONE (22/09/2026): quella che il processo ESEGUE, dal nome del binario in
`/proc/<pid>/exe` (`~/.local/share/claude/versions/2.1.280`); se non si legge,
quella del registro, scritta all'avvio. Un `*` marca le sessioni piu' vecchie
della versione su disco (dove punta `claude` nel PATH): `claude update` e
`claude --version` guardano il disco, e il 22/09 dicevano 2.1.280 mentre le
quattro sessioni vive giravano sulla 2.1.278.

MODELLO RIPIEGATO (22/09/2026): da Opus 5.5 un messaggio segnalato dalle
salvaguardie (bio, cyber) sposta la sessione su un modello piu' vecchio senza
fermarla. `fallback` = {from, to, category, at} finche' la sessione e' ancora
sul modello di ripiego, letto dalla trascrizione (cm-core.model_fallback, stessa
definizione di fable-director); nella tabella, la nota dice come tornare.

Uso: cm-sessions.py [--json] [--watch [SECONDI]] [--no-screen]
"""
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)

CFG = cm.load(warn=False)
TMUX = ["tmux"] + (os.environ.get("CM_TMUX_ARGS", "").split()
                   or (["-L", CFG["tmux"]["socket"]] if CFG["tmux"].get("socket") else []))
CLAUDE_CMD = re.compile(r"(^|/)claude( |$)")
# Claude Code 2.1.295 (08/10/2026): each --bg session's terminal lives in a helper «claude bg-pty-host …», and the
# background manager is «claude daemon …»; neither is a session (the session itself is listed from the registry)
CLAUDE_HELPER = re.compile(r"(^|/)claude (.* )?--bg-pty-host( |$)|(^|/)claude daemon( |$)")
CODEX_CMD = re.compile(r"(^|/)codex( |$)")   # S09: prova, solo con experimental.codex
VERSION = re.compile(r"\d+(?:\.\d+)+")


def tmux(*args):
    try:
        r = subprocess.run(TMUX + list(args), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def proc_start(pid):
    """Campo 22 di /proc/<pid>/stat: istante di avvio in tick. Distingue un pid riusato."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # il nome del processo sta fra parentesi e puo' contenere spazi: si taglia dopo l'ultima ')'
    return stat[stat.rindex(")") + 2:].split()[19]


def proc_environ(pid):
    env = {}
    try:
        for kv in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
            k, _, v = kv.partition(b"=")
            if k:
                env[k.decode(errors="replace")] = v.decode(errors="replace")
    except OSError:
        pass
    return env


def proc_cmdline(pid):
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        return ""


def is_tmux(pid, cmd):
    """Il server o un client tmux: primo argomento o comm che comincia con «tmux». Il server porta nella sua riga di
    comando quella della prima sessione (`tmux new-session … /claude …`) e CLAUDE_CMD da solo lo prendeva per un
    Claude fuori registro (14/09/2026: «(fuori registro)» col pid del server)."""
    try:
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        comm = ""
    return os.path.basename(cmd.split(" ", 1)[0]).startswith("tmux") or comm.startswith("tmux")


def codex_state(cwd):
    """S09 (prova): busy/idle di una sessione Codex CLI dal suo rollout (~/.codex/sessions/AAAA/MM/GG/rollout-*.jsonl,
    il primo evento e' session_meta con la cartella): l'ultimo fra task_started e task_complete. Senza rollout (nessun
    prompt ancora) e' idle. Le domande in attesa non stanno nel rollout: «waiting» qui non si sa."""
    root = Path(os.environ.get("CODEX_HOME") or (cm.home() / ".codex")) / "sessions"
    files = sorted(glob.glob(str(root / "*" / "*" / "*" / "rollout-*.jsonl")), key=os.path.getmtime, reverse=True)[:40]
    for f in files:
        try:
            lines = Path(f).read_text(errors="replace").splitlines()
            meta = json.loads(lines[0]).get("payload") or {}
        except (OSError, ValueError, IndexError):
            continue
        if os.path.realpath(meta.get("cwd") or "") != os.path.realpath(cwd or ""):
            continue
        for line in reversed(lines):
            if '"task_complete"' in line:
                return "idle"
            if '"task_started"' in line:
                return "busy"
        return "idle"
    return "idle"


def version_of(path):
    """«2.1.280» se il file (risolti i link) si chiama come una versione, altrimenti ""."""
    name = os.path.basename(os.path.realpath(path).removesuffix(" (deleted)")) if path else ""
    return name if VERSION.fullmatch(name) else ""


def proc_version(pid, registered=""):
    """La versione che il processo esegue (/proc/<pid>/exe); se non si legge, quella del registro."""
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        exe = ""
    return version_of(exe) or (registered if isinstance(registered, str) and VERSION.fullmatch(registered) else "")


def disk_version():
    """La versione che una sessione nuova avrebbe: dove punta `claude` nel PATH (CM_CLAUDE_BIN nei test)."""
    return version_of(os.environ.get("CM_CLAUDE_BIN") or shutil.which("claude") or "")


def older(v, than):
    return bool(v and than) and tuple(map(int, v.split("."))) < tuple(map(int, than.split(".")))


def proc_cwd(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def proc_ppid(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def is_ancestor(pid):
    """Il pid e' un antenato di questo processo? (la sessione da cui si guarda)"""
    cur = os.getpid()
    for _ in range(64):
        if cur == pid:
            return True
        cur = proc_ppid(cur)
        if not cur or cur <= 1:
            return False
    return False


def accounts_by_dir():
    return {os.path.realpath(cm.expand(a["config_dir"])): name for name, a in CFG["accounts"].items()}


def my_config_dir():
    return os.path.realpath(os.environ.get("CLAUDE_CONFIG_DIR") or cm.expand("~/.claude"))


def account_of_pid(pid, hint):
    conf = proc_environ(pid).get("CLAUDE_CONFIG_DIR")
    by_dir = accounts_by_dir()
    if conf:
        return by_dir.get(os.path.realpath(conf), os.path.basename(conf))
    return by_dir.get(os.path.realpath(cm.expand("~/.claude")), hint or CFG["default_account"])


def registry_dirs():
    """(realpath della cartella sessions, nome account) per ogni account; condivise una volta sola."""
    seen = {}
    for name, a in CFG["accounts"].items():
        d = os.path.join(cm.expand(a["config_dir"]), "sessions")
        rp = os.path.realpath(d)
        if not os.path.isdir(rp):
            continue
        if rp in seen:
            seen[rp] = None          # condivisa: l'account non si deduce dalla cartella
        else:
            seen[rp] = name
    return seen


def registry_entries():
    out = []
    for rp, hint in registry_dirs().items():
        for f in sorted(glob.glob(os.path.join(rp, "*.json"))):
            try:
                d = json.load(open(f))
            except (OSError, ValueError):
                continue
            pid = d.get("pid")
            if not isinstance(pid, int):
                continue
            # T62: file stantio (pid morto) o pid riusato (procStart diverso)
            ps = proc_start(pid)
            if ps is None or (d.get("procStart") and str(d["procStart"]) != ps):
                continue
            d["_registry"] = rp
            d["_hint"] = hint
            out.append(d)
    return out


def tmux_panes():
    """{pane_pid: session_name} — per i processi fuori registro e per il ripiego."""
    out = {}
    for line in (tmux("list-panes", "-a", "-F", "#{session_name} #{pane_pid}") or "").splitlines():
        s, _, p = line.rpartition(" ")
        if p.strip().isdigit():
            out[int(p)] = s
    return out


def tmux_client_activity():
    """1.62 (14/10): {sessione tmux: epoch dell'ultimo tasto premuto in un client attaccato a quella sessione}. La
    sessione attaccata ma lasciata li' non conta: serve un tasto recente (client_activity)."""
    out = {}
    for line in (tmux("list-clients", "-F", "#{client_session} #{client_activity}") or "").splitlines():
        s, _, t = line.rpartition(" ")
        if s and t.strip().isdigit():
            out[s] = max(out.get(s, 0), int(t))
    return out


def tmux_attached():
    out = {}
    for line in (tmux("list-sessions", "-F", "#{session_name} #{session_attached}") or "").splitlines():
        s, _, n = line.rpartition(" ")
        out[s] = n.strip().isdigit() and int(n) > 0
    return out


WAIT_LIST = re.compile(r"^[[:space:]]*[0-9]+\.[[:space:]]".replace("[[:space:]]", r"\s"), re.M)
WAIT_HINT = re.compile(r"to select|to navigate|Tab to amend")


# Modalita' a bassa priorita' (`/low-priority`, Claude Code 2.1.282): stato solo in memoria della sessione, niente
# nel registro peer, nel JSON della statusline o nel transcript. L'unica traccia leggibile da fuori e' lo schermo:
# la riga di stato «Lower priority until …» e il banner «Working at lower priority · waiting for capacity»
# (testi di default del flag tengu_toasty_breeze; «Continuing now at lower priority» e' la conferma del comando).
LOWPRI_RE = re.compile(r"Lower priority until|Working at lower priority|Continuing now at lower priority|Lower-priority mode is (back on|on)")
# l'offerta (contratto 1.16): al muro del limite Claude Code stampa la riga «/low-priority to continue now at lower
# priority · uses your weekly limit» o la voce di menu «Continue now at lower priority» (testi del flag, default)
LOWPRI_OFFER_RE = re.compile(r"/low-priority to continue now at lower priority|Continue now at lower priority")


def screen_of(tmux_name, lines=20):
    """Le ultime `lines` righe SCRITTE del riquadro (le vuote in coda, schermo non pieno, non contano).
    Nome nudo: capture-pane non accetta `=` (T54). Stringa vuota senza riquadro o senza tmux."""
    if not tmux_name:
        return ""
    screen = tmux("capture-pane", "-p", "-t", tmux_name)
    if not screen:
        return ""
    return "\n".join(screen.rstrip("\n").splitlines()[-lines:])


def waits_on_screen(tmux_name, screen=None):
    """T13: due indizi insieme."""
    tail = screen_of(tmux_name) if screen is None else screen
    # 21/10/2026 (Claude Code 2.1.304): l'avviso sul costo della ripresa; al ritorno su una sessione ferma le opzioni
    # non hanno numero, e il pie' di pagina («Enter to confirm») non e' quello delle domande
    if tail and "Resume this conversation?" in tail and "5-hour usage" in tail:
        return True
    return bool(tail and WAIT_LIST.search(tail) and WAIT_HINT.search(tail))


_ESC = re.compile(r"\x1b\[([0-9;:]*)([A-Za-z])")
# 08/10 (dal telefono, Stop → «nothing to stop»): da Claude Code 2.1.294 la riga di stato del turno non dice piu'
# «esc to interrupt» («● Slithering… (1m 9s · ↓ 2.7k tokens)»): conta anche lo spinner, glifo + «…» + «(Ns ·» o «tokens)»
WORKING = re.compile(r"(?im)esc to interrupt|^\s*[^\w\s]\s+\w[^\n]*…\s*\([^)\n]*(?:\b\d+[smh]\b|tokens)")   # turno in corso: nessun suggerimento da offrire
_OPTION_LINE = re.compile(r"^\s*\d+\.\s")
_PLACEHOLDER = re.compile(r'^Try ".*"$')


def _dim_after(params, dim):
    """Lo stato «attenuato» dopo una sequenza SGR: 2 accende, 22 e 0 (o vuota) spengono; i colori 38/48 con i loro
    argomenti (5;n o 2;r;g;b) si saltano, cosi' il 2 di un colore RGB non e' preso per l'attenuato."""
    ps = [p for p in params.replace(":", ";").split(";")]
    i = 0
    while i < len(ps):
        p = ps[i]
        if p in ("38", "48", "58"):
            i += 3 if (i + 1 < len(ps) and ps[i + 1] == "5") else 5 if (i + 1 < len(ps) and ps[i + 1] == "2") else 1
            continue
        if p in ("", "0", "22"):
            dim = False
        elif p == "2":
            dim = True
        i += 1
    return dim


def suggestion_on_screen(tmux_name, colored=None):
    """1.23 (30/09): il prompt suggerito che Claude Code scrive in grigio (SGR 2) dopo «❯» a casella vuota, o None.
    Mai il testo digitato: basta un carattere non attenuato dopo «❯» e non c'e' suggerimento. None anche a turno in
    corso, su un dialogo (le righe «❯ 1. …» sono opzioni) e per il segnaposto «Try "…"» delle sessioni nuove."""
    if colored is None:
        colored = tmux("capture-pane", "-p", "-e", "-t", tmux_name) if tmux_name else ""
    lines = (colored or "").rstrip("\n").splitlines()[-15:]
    plain = [_ESC.sub("", l) for l in lines]
    if any(WORKING.search(l) for l in plain):
        return None
    idx = next((i for i in range(len(plain) - 1, -1, -1) if plain[i].lstrip().startswith("❯")), None)
    if idx is None or _OPTION_LINE.match(plain[idx].lstrip()[1:]):
        return None
    line = lines[idx]
    pos, dim, seen, out = line.index("❯") + 1, False, False, []
    # lo stato SGR prima di «❯» vale anche dopo: si ricostruisce dall'inizio della riga
    for m in _ESC.finditer(line[:pos]):
        if m.group(2) == "m":
            dim = _dim_after(m.group(1), dim)
    i = pos
    while i < len(line):
        m = _ESC.match(line, i)
        if m:
            if m.group(2) == "m":
                dim = _dim_after(m.group(1), dim)
            i = m.end()
            continue
        ch = line[i]
        if dim:
            out.append(ch)
        elif not ch.isspace():
            return None   # testo digitato dall'utente
        elif out:
            out.append(" ")
        i += 1
    text = " ".join("".join(out).replace("\u00a0", " ").split())
    return text if text and not _PLACEHOLDER.match(text) else None


def low_priority_on_screen(tmux_name, screen=None):
    """Contratto 1.16: «active» se lo schermo mostra la modalita' a bassa priorita' in corso, «offered» se mostra
    l'offerta al muro del limite, «off» altrimenti; None senza schermo da leggere (non si sa)."""
    tail = screen_of(tmux_name) if screen is None else screen
    if not tail:
        return None
    if LOWPRI_RE.search(tail):
        return "active"
    if LOWPRI_OFFER_RE.search(tail):
        return "offered"
    return "off"


def transcript_of(row):
    """<config_dir>/projects/<slug>/<sessionId>.jsonl — slug: ogni non alfanumerico → '-' (come cm-talk)."""
    acc = CFG["accounts"].get(row.get("account") or "") or {}
    if not row.get("session_id") or not row.get("cwd"):
        return None
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(row["cwd"]))
    return Path(cm.expand(acc.get("config_dir", "~/.claude"))) / "projects" / slug / f"{row['session_id']}.jsonl"


def goal_of(row, tail_bytes=512 * 1024):
    """Il goal nativo (`/goal`) della sessione, letto dal transcript: Claude Code lo persiste come attachment
    {"type": "goal_status", "condition", "met", "failed", "sentinel", "iterations"} e alla ripresa lo ricostruisce
    dall'ULTIMO di questi (2.1.282). Torna {"condition", "iterations"} se l'ultimo non e' met ne' failed, altrimenti
    None. Si legge solo la coda del file: il goal e' quasi sempre recente, e i transcript pesano decine di MB."""
    path = transcript_of(row)
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(max(0, path.stat().st_size - tail_bytes))
            data = f.read()
    except OSError:
        return None
    last, since = None, None
    for line in data.split(b"\n"):
        if b'"goal_status"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        att = d.get("attachment") if d.get("type") == "attachment" else None
        if isinstance(att, dict) and att.get("type") == "goal_status":
            if att.get("sentinel") and not att.get("met"):
                # la sentinella «goal impostato»: la sua data e' il since del contratto 1.16
                since = _epoch_of(d.get("timestamp")) or since
            last = att
    if not last or last.get("failed"):
        return None
    cond = last.get("condition")
    if not isinstance(cond, str) or not cond.strip():
        return None
    if last.get("met") and last.get("sentinel"):
        return None   # la sentinella «goal tolto» (/goal clear): nessun goal
    return {"condition": cond.strip(), "iterations": int(last.get("iterations") or 0), "since": since, "met": bool(last.get("met"))}


def _epoch_of(ts):
    """ISO 8601 del transcript («2026-09-25T14:03:11.123Z») → epoch in secondi; None se non si legge."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        from datetime import datetime, timezone
        t = ts.replace("Z", "+00:00")
        return int(datetime.fromisoformat(t).astimezone(timezone.utc).timestamp())
    except ValueError:
        return None


def waiting_flag(session_id):
    """Flag scritto dall'hook PermissionRequest (fase 4). Assente = non si sa."""
    if not session_id:
        return None
    f = Path(cm.expand(CFG["state_dir"])) / "waiting" / session_id
    return f.is_file() or None


def etime(started_ms):
    if not started_ms:
        return "-"
    s = max(0, int(time.time() - started_ms / 1000))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{d}d{h:02d}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def short_cwd(cwd):
    root = cm.expand(CFG["workspace"]["root"])
    home = str(cm.home())
    if cwd == root:
        return "ws:."
    if cwd.startswith(root + "/"):
        return "ws:" + cwd[len(root) + 1:]
    if cwd.startswith(home + "/"):
        return "~/" + cwd[len(home) + 1:]
    return cwd


def collect(read_screen=True):
    panes = tmux_panes()
    attached = tmux_attached()
    mine = my_config_dir()
    my_registry = os.path.realpath(os.path.join(mine, "sessions"))
    rows = []
    seen_pids = set()
    disk = disk_version()
    for d in registry_entries():
        pid = d["pid"]
        seen_pids.add(pid)
        tm = (d.get("tmux") or "").split(":", 1)[0] or panes.get(pid) or panes.get(proc_ppid(pid) or -1) or ""
        row = {
            "pid": pid,
            "name": d.get("name") or "",
            "account": account_of_pid(pid, d.get("_hint")),
            "cwd": d.get("cwd") or proc_cwd(pid),
            "tmux": tm,
            "status": d.get("status") or "?",
            "session_id": d.get("sessionId") or "",
            # bridgeSessionId porta gia' il prefisso "session_" (dal vivo, 09/09/2026)
            "link": ("https://claude.ai/code/session_" + str(d["bridgeSessionId"]).removeprefix("session_")) if d.get("bridgeSessionId") else "",
            "started_at": d.get("startedAt"),
            "socket": d.get("messagingSocketPath") or "",
            "registry": d["_registry"],
            "version": proc_version(pid, d.get("version")),
        }
        row["attached"] = attached.get(tm) if tm else None
        # «aspetta una risposta»: prima il registro (status `waiting`, visto dal vivo il 09/09/2026
        # con un dialogo aperto), poi il flag dell'hook, infine lo schermo (T13)
        flag = waiting_flag(row["session_id"])
        screen = screen_of(tm) if read_screen else ""
        row["waiting"] = (row["status"] == "waiting") or (bool(flag) if flag is not None else (waits_on_screen(tm, screen) if read_screen else False))
        # bassa priorita' (lenta ma viva) e goal nativo: informazione, mai azione
        row["low_priority"] = low_priority_on_screen(tm, screen) if read_screen else None
        row["suggestion"] = suggestion_on_screen(tm) if read_screen and tm else None
        a = status_age_min(row)
        row["status_age_min"] = round(a) if a is not None else None
        g = goal_of(row)
        # `goal`: il testo del goal ANCORA APERTO (per la tabella e per wait); `goal_status`: la forma del contratto
        # 1.16 per il polso, anche quando e' raggiunto ({text, since, met})
        row["goal"] = g["condition"] if g and not g["met"] else ""
        row["goal_iterations"] = g["iterations"] if g and not g["met"] else 0
        row["goal_status"] = {"text": g["condition"], "since": g["since"], "met": g["met"]} if g else None
        row["channel"] = ("(questa)" if is_ancestor(pid)
                          else "nativo" if row["registry"] == my_registry else "talk")
        rows.append(row)

    # ripiego /proc (T11): processi claude che il registro non conosce.
    # CM_PROC_SCAN_PIDS (test) limita la scansione a pid noti: i test non devono
    # vedere le sessioni vere della macchina.
    scan = os.environ.get("CM_PROC_SCAN_PIDS")
    candidates = [int(x) for x in scan.split() if x.isdigit()] if scan is not None else \
        [int(os.path.basename(d)) for d in glob.glob("/proc/[0-9]*")]
    for pid in candidates:
        if pid in seen_pids:
            continue
        cmd = proc_cmdline(pid)
        if (CFG.get("experimental") or {}).get("codex") and pid in panes and CODEX_CMD.search(cmd) and not is_tmux(pid, cmd):
            # S09 (prova): il processo del riquadro tmux e' Codex CLI (il binario vero e' un suo figlio: non si conta due
            # volte); niente registro peer, lo stato viene dal rollout
            tm, cwd = panes[pid], proc_cwd(pid)
            seen_pids.add(pid)
            rows.append({"pid": pid, "name": tm, "account": "codex", "cwd": cwd, "tmux": tm, "status": codex_state(cwd),
                         "session_id": "", "link": "", "started_at": None, "socket": "", "registry": "", "agent": "codex",
                         "attached": attached.get(tm), "waiting": False, "channel": "tmux"})
            continue
        if not CLAUDE_CMD.search(cmd) or CLAUDE_HELPER.search(cmd) or is_tmux(pid, cmd) or "shell-snapshots" in cmd or (" -c " in cmd and "pwd -P" in cmd):
            continue
        tm = panes.get(pid) or panes.get(proc_ppid(pid) or -1) or ""
        seen_pids.add(pid)
        rows.append({
            "pid": pid, "name": "", "account": account_of_pid(pid, None), "cwd": proc_cwd(pid),
            "tmux": tm, "status": "?", "session_id": "", "link": "", "started_at": None, "socket": "",
            "registry": "", "attached": attached.get(tm) if tm else None,
            "waiting": waits_on_screen(tm) if read_screen else False,
            "channel": "(questa)" if is_ancestor(pid) else "talk",
            "version": proc_version(pid),
        })
    activity = tmux_client_activity()
    for r in rows:
        r["outdated"] = older(r.get("version", ""), disk)
        r["pc_activity"] = activity.get(r.get("tmux") or "") or None   # 1.62
    core = None
    for r in rows:
        r["fallback"] = None
        if not r.get("session_id"):
            continue
        try:
            if core is None:
                spec = importlib.util.spec_from_file_location("cm_core", HERE / "cm-core.py")
                core = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(core)
            r["fallback"] = core.model_fallback(r)
        except Exception:   # noqa: BLE001 — un'informazione in piu': mai far cadere l'elenco
            pass
    # chi aspetta senza nessuno davanti va per primo: e' lavoro fermo, non in corso
    rows.sort(key=lambda r: (not (r["waiting"] and not r["attached"]), r["account"], r["tmux"] or r["name"]))
    return rows


_HOSTS = None


def hosts_mod():
    global _HOSTS
    if _HOSTS is None:
        spec = importlib.util.spec_from_file_location("cm_hosts", HERE / "cm-hosts.py")
        _HOSTS = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_HOSTS)
    return _HOSTS


def remote_rows():
    """Le sessioni degli altri host (piano multi-PC 4.3), lette dagli snapshot del sondatore: mai dal trasporto, cosi'
    `sessions` resta veloce anche con un host spento. Solo per la tabella e --json: talk, next e park restano locali."""
    if not CFG.get("hosts"):
        return []
    try:
        return hosts_mod().remote_session_rows()
    except Exception:   # noqa: BLE001 — le righe remote sono in piu': mai far cadere l'elenco locale
        return []


def status_age_min(row):
    try:
        d = json.load(open(Path(row["registry"]) / f"{row['pid']}.json"))
        t = d.get("statusUpdatedAt") or d.get("updatedAt")
        return (time.time() - t / 1000) / 60 if t else None
    except (OSError, ValueError, TypeError):
        return None


def quota_warnings():
    """Account con la settimana oltre quota.warn_pct (dai file della statusline, come `quota`)."""
    import hashlib
    out = []
    src = Path(cm.expand(CFG["quota"]["source"]))
    for name, a in CFG["accounts"].items():
        h = hashlib.sha256(str(cm.expand(a["config_dir"])).encode()).hexdigest()[:8]
        try:
            d = json.load(open(src / f"quota-{h}.json"))
        except (OSError, ValueError):
            continue
        pct = d.get("weekly_used_pct")
        if pct is not None and pct >= CFG["quota"]["warn_pct"] and (d.get("weekly_resets_at") or 0) > time.time():
            out.append((name, round(pct)))
    return out


def render(rows):
    m = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
    lines = []
    # S03 (14/09/2026): intestazione, canali e stato nella lingua della config (prima fissi in italiano). Nel JSON
    # `channel` resta «(questa)»/«nativo»/«talk»: talk, next e park lo confrontano.
    hdr = " ".join(f"{c:<{w}}" for c, w in zip(m("sessions.columns").split(), (8, 13, 22, 6, 24, 9, 9, 9, 9)))
    channel = {"(questa)": m("sessions.channel_self"), "nativo": m("sessions.channel_native"), "remoto": m("sessions.channel_remote")}
    lines.append(hdr)
    lines.append("-" * len(hdr))
    abandoned = 0
    for r in rows:
        vista = "-" if r["attached"] is None else (m("sessions.open") if r["attached"] else m("sessions.detached"))
        note = ""
        # 2.4 stallo: busy da piu' di sessions.stall_min senza cambi di stato — informazione, mai azione
        age = status_age_min(r)
        if r["status"] == "busy" and age is not None and age >= CFG["sessions"]["stall_min"]:
            note = "  <- " + m("sessions.stalled", min=round(age))
        # tetto (25/09/2026): idle da piu' di sessions.idle_hours → suggerimento di chiusura, mai chiusa da sola
        if r["status"] == "idle" and not r["waiting"] and age is not None and age >= CFG["sessions"]["idle_hours"] * 60:
            note = "  <- " + m("sessions.idle_long", hours=round(age / 60), name=r["tmux"] or r["name"])
        if r["waiting"]:
            if r["attached"]:
                note = "  <- " + m("sessions.waiting")
            else:
                note = "  <- " + m("sessions.abandoned")
                abandoned += 1
        fb = r.get("fallback")
        if fb:
            back = next((mm["id"] for mm in CFG["tune"]["models"] if mm["id"].split("[")[0] == str(fb.get("from") or "").split("[")[0]), "")
            note += "  <- " + m("sessions.fallback", to=fb["to"], was=fb.get("from") or "?",
                                why=f" {fb['category']}" if fb.get("category") else "",
                                back=f"/model · supervisor model {r['tmux'] or r['name']} {back}" if back and (r["tmux"] or r["name"]) else "/model")
        # NOME = il nome tmux, quello che answer/talk/close accettano; /rename nell'app cambia solo
        # `name` nel registro peer (provato l'11/09/2026): lo si mostra accanto, mai al posto
        shown = r["tmux"] or r["name"]
        if r["tmux"] and r["name"] and r["name"] != r["tmux"]:
            shown = f"{r['tmux']} ({r['name']})"
        # S03: uno stato solo (prima «busy» e «<- aspetta una risposta» insieme); il processo fuori registro (T11)
        # ha un'etichetta invece del nome vuoto e del «?»
        state = r["status"]
        if r.get("host"):
            if r.get("unreachable"):
                note += "  <- " + m("sessions.remote_unreachable", age=round(r["read_age_s"] / 60))
            elif r.get("read_age_s", 0) > 180:
                note += "  <- " + m("sessions.remote_age", age=round(r["read_age_s"] / 60))
        elif not r["registry"] and r.get("agent") != "codex":
            shown, state = shown or m("sessions.unregistered"), "-"
        if r["waiting"]:
            state = m("sessions.state_waiting")
        if r.get("low_priority") == "active":
            note += "  <- " + m("sessions.low_priority")
        elif r.get("low_priority") == "offered":
            note += "  <- " + m("sessions.low_priority_offered")
        if r.get("goal"):
            note += "  <- " + m("sessions.goal", goal=r["goal"][:60] + ("…" if len(r["goal"]) > 60 else ""), n=r.get("goal_iterations") or 0)
        lines.append(f"{r['pid']:<8} {r['account'][:13]:<13} {shown[:22]:<22} {state[:6]:<6} "
                     f"{short_cwd(r['cwd'])[:24]:<24} {vista:<9} {channel.get(r['channel'], r['channel']):<9} {etime(r['started_at']):<9} "
                     f"{(r.get('version') or '-') + ('*' if r.get('outdated') else ''):<9}{note}")
    if not rows:
        lines.append("  " + m("sessions.none"))
    if CFG.get("hosts"):
        try:
            hl = hosts_mod().host_summary_lines()
        except Exception:   # noqa: BLE001
            hl = []
        if hl:
            lines += [""] + ["  " + x for x in hl]
    if abandoned:
        lines += ["", "  " + m("sessions.abandoned_hint", n=abandoned)]
    if any(r.get("outdated") for r in rows):
        lines += ["", "  " + m("sessions.outdated", version=disk_version())]
    if tmux("list-sessions") is None:
        lines += ["", "  " + m("sessions.tmux_dead")]
    # 2.3 quota a soglia: consiglio, mai automatismo
    for acc, q in quota_warnings():
        lines += ["", "  " + m("sessions.quota_warn", account=acc, pct=q, threshold=CFG["quota"]["warn_pct"])]
    lines += ["", "  " + m("sessions.footer_native"), "  " + m("sessions.footer_talk"),
              "  " + m("sessions.footer_launch"), "  " + m("sessions.footer_attach")]
    return "\n".join(lines) + "\n"


def active_count(rows):
    """Le sessioni «al lavoro» per il tetto di launch: busy o idle (non «?» ne' gone), esclusa la master
    (workspace.root_session_name) e i processi fuori registro; Codex non conta (e' un'altra memoria)."""
    root = CFG["workspace"].get("root_session_name") or "master"
    return sum(1 for r in rows if r.get("registry") and not r.get("host") and r.get("agent") != "codex" and r["status"] in ("busy", "idle", "waiting")
               and (r["tmux"] or r["name"]) != root)


def main(argv):
    if "--count-active" in argv:   # usato da launch (tetto sessions.max_sessions): solo il numero
        print(active_count(collect(read_screen=False)))
        return 0
    if "--quota-warn" in argv:   # usato da launch: stampa l'avviso per UN account, se serve
        acc = argv[argv.index("--quota-warn") + 1]
        for a, q in quota_warnings():
            if a == acc:
                print(cm.msg(CFG, "sessions.quota_warn", account=a, pct=q, threshold=CFG["quota"]["warn_pct"]))
        return 0
    read_screen = "--no-screen" not in argv
    if "--json" in argv:
        print(json.dumps(collect(read_screen) + remote_rows(), ensure_ascii=False, indent=1))
        return 0
    if "--watch" in argv:
        i = argv.index("--watch")
        every = int(argv[i + 1]) if len(argv) > i + 1 and argv[i + 1].isdigit() else 5
        try:
            while True:
                out = render(collect(read_screen) + remote_rows())
                sys.stdout.write("\033[H\033[2J" + time.strftime("%H:%M:%S") + "\n" + out)
                sys.stdout.flush()
                time.sleep(every)
        except KeyboardInterrupt:
            return 0
    sys.stdout.write(render(collect(read_screen) + remote_rows()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
