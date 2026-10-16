#!/usr/bin/env python3
"""Verifica le scadenze da un calendario .ics (cm-calendar.py, recap, next), con un feed inventato su un server locale.

CA1 parser: righe ripiegate, escape, DTSTART data / datetime UTC, TRIGGER -P3D / -P1W / -PT12H, case_property
CA2 recap: sezione SCADENZE con la scadenza a 3 giorni (VALARM -P3D), data, giorni mancanti, pratica → cartella;
    quella senza VALARM entra con remind_days; fuori finestra e passate no; la piu' vicina per prima
CA3 segreti: l'indirizzo col token sta solo nel file 0600; il token non esce in stdout, ne' nella cache (0600)
CA4 next: la scadenza compare come voce (priorita' 3) SOTTO una sessione ferma per te (needs-you, priorita' 1),
    con la cartella della pratica e il comando per aprirla
CA5 senza rete: il recap esce lo stesso (exit 0) con la riga «calendario non raggiungibile»; next non si rompe
CA6 senza il file dei segreti: niente sezione, niente voci, nessuna richiesta in rete
"""
import datetime as dt
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

TOKEN = "tok-FINTO-0123456789abcdef"
tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude/sessions", "ws/personali/alfa", "ws/pratiche/p001"):
    (home / d).mkdir(parents=True)
TODAY = dt.date.today()


def ics_date(n):
    return (TODAY + dt.timedelta(days=n)).strftime("%Y%m%d")


def ddmm(n):
    return (TODAY + dt.timedelta(days=n)).strftime("%d/%m")


# un calendario inventato: nessun dato vero
ICS = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//prova//calendario finto//IT",
    "BEGIN:VEVENT", "UID:ev1@prova", f"DTSTART;VALUE=DATE:{ics_date(3)}",
    "SUMMARY:Deposito memoria\\, pratica di prova con un titolo abbastanza lungo da essere r",
    " ipiegato", "X-STUDIO-CASE:P-001",
    "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-P3D", "END:VALARM", "END:VEVENT",
    "BEGIN:VEVENT", "UID:ev2@prova", f"DTSTART;VALUE=DATE:{ics_date(10)}", "SUMMARY:Lontana",
    "BEGIN:VALARM", "TRIGGER:-P7D", "END:VALARM", "END:VEVENT",
    "BEGIN:VEVENT", "UID:ev3@prova", f"DTSTART:{ics_date(2)}T120000Z", "SUMMARY:Senza allarme", "END:VEVENT",
    "BEGIN:VEVENT", "UID:ev4@prova", f"DTSTART;VALUE=DATE:{ics_date(-1)}", "SUMMARY:Passata",
    "BEGIN:VALARM", "TRIGGER:-P3D", "END:VALARM", "END:VEVENT",
    "BEGIN:VEVENT", "UID:ev5@prova", f"DTSTART;VALUE=DATE:{ics_date(5)}", "SUMMARY:Settimana",
    "X-STUDIO-CASE:P-999", "BEGIN:VALARM", "TRIGGER:-P1W", "END:VALARM", "END:VEVENT",
    "BEGIN:VEVENT", "UID:ev6@prova", f"DTSTART;VALUE=DATE:{ics_date(4)}", "SUMMARY:Allarme corto",
    "BEGIN:VALARM", "TRIGGER:-PT12H", "END:VALARM", "END:VEVENT",
    "END:VCALENDAR", ""])
HITS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        HITS.append(self.path)
        ok = self.path == f"/cal.ics?token={TOKEN}"
        out = ICS.encode() if ok else b"no"
        self.send_response(200 if ok else 403)
        self.send_header("Content-Type", "text/calendar")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


srv = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
s = socket.socket(); s.bind(("127.0.0.1", 0)); DEAD_PORT = s.getsockname()[1]; s.close()   # porta chiusa = senza rete


def setup(name, url):
    """Una configurazione con il suo state_dir e il suo file dei segreti (o nessuno, con url=None)."""
    d = tmp / name
    d.mkdir()
    secret = d / "calendar.env"
    if url is not None:
        secret.write_text(f"CALENDAR_ICS_URL={url}\n")
        secret.chmod(0o600)
    cfg = d / "config.json"
    cfg.write_text(json.dumps({
        "language": "it", "state_dir": str(d / "state"),
        "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
        "accounts": {"personale": {"config_dir": str(home / ".claude")}},
        "sessions": {"max_sessions": 50, "stall_min": 20, "recent_min": 5},
        "terminal": {"backend": "none"}, "tabs": {"color_registry": str(d / "colors")},
        "registry": {"file": str(d / "registry.json")},
        "recap": {"summary": "last", "project_log": ""},
        "calendar": {"url_file": str(secret), "remind_days": 3, "cache_min": 0, "timeout_s": 3,
                     "cases": {"P-001": "~/ws/pratiche/p001"}},
    }))
    return cfg, d / "state"


def env(cfg, **extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_DIARY_FAKE_PROC": "1", "CM_PROC_SCAN_PIDS": "", "TZ": "Europe/Rome"}
    e.update(extra)
    return e


def run(script, cfg, *args, **extra):
    return subprocess.run([sys.executable, str(T.SCRIPTS / script), *args], capture_output=True, text=True,
                          env=env(cfg, **extra), timeout=120)


ok_cfg, ok_state = setup("online", f"http://127.0.0.1:{srv.server_port}/cal.ics?token={TOKEN}")
off_cfg, off_state = setup("offline", f"http://127.0.0.1:{DEAD_PORT}/cal.ics?token={TOKEN}")
none_cfg, none_state = setup("spento", None)

# CA1: il parser da solo
os.environ.update(env(ok_cfg))
spec = importlib.util.spec_from_file_location("cm_calendar", T.SCRIPTS / "cm-calendar.py")
cal = importlib.util.module_from_spec(spec); spec.loader.exec_module(cal)
evs = {e["uid"]: e for e in cal.parse(ICS)}
T.check("CA1 parser: folded line joined, escaped comma, case property",
        evs["ev1@prova"]["summary"] == "Deposito memoria, pratica di prova con un titolo abbastanza lungo da essere ripiegato"
        and evs["ev1@prova"]["case"] == "P-001" and evs["ev1@prova"]["remind"] == 3, str(evs.get("ev1@prova")))
T.check("CA1 parser: UTC datetime → local date, no VALARM → remind None",
        evs["ev3@prova"]["date"] == TODAY + dt.timedelta(days=2) and evs["ev3@prova"]["remind"] is None, str(evs.get("ev3@prova")))
T.check("CA1 TRIGGER: -P3D 3, -P1W 7, -PT12H 1, -P1DT6H 2, after-start and absolute ignored",
        [cal.trigger_days(x) for x in ("-P3D", "-P1W", "-PT12H", "-P1DT6H", "PT15M", "P1D")] == [3, 7, 1, 2, None, None]
        and evs["ev5@prova"]["remind"] == 7 and evs["ev6@prova"]["remind"] == 1)

# CA2 + CA3: recap con il calendario raggiungibile
r = run("cm-recap.py", ok_cfg)
out = r.stdout
d3 = f"📅 {ddmm(3)} (tra 3 giorni) Deposito memoria, pratica di prova con un titolo abbastanza lungo da essere ripiegato · pratica P-001 → ~/ws/pratiche/p001"
T.check("CA2 recap: SCADENZE section with the 3-day deadline (VALARM -P3D), date, days left, case → folder",
        r.returncode == 0 and "SCADENZE" in out and d3 in out, out + r.stderr)
T.check("CA2 recap: no VALARM → remind_days (2 days, UTC datetime), -P1W lets the 5-day one in, case without a folder",
        f"📅 {ddmm(2)} (tra 2 giorni) Senza allarme" in out and f"📅 {ddmm(5)} (tra 5 giorni) Settimana · pratica P-999\n" in out + "\n", out)
T.check("CA2 recap: outside the window, past and short-alarm ones are left out; nearest first",
        "Lontana" not in out and "Passata" not in out and "Allarme corto" not in out
        and out.index("Senza allarme") < out.index("Deposito memoria") < out.index("Settimana"), out)
T.check("CA2 recap: the rest of the recap is still there, deadlines above it",
        out.startswith("Recap ") and "nessun evento nel ledger" in out and out.index("SCADENZE") < out.index("nessun evento nel ledger"), out)
cache = ok_state / "calendar.ics"
T.check("CA3 token never printed; cache written 0600 without the address",
        TOKEN not in out + r.stderr and cache.is_file() and (cache.stat().st_mode & 0o777) == 0o600 and TOKEN not in cache.read_text(),
        oct(cache.stat().st_mode) if cache.is_file() else "no cache")
r = run("cm-calendar.py", ok_cfg, "--json")
j = json.loads(r.stdout)
T.check("CA3 supervisor calendar --json: 3 deadlines, the case folder expanded, no token",
        [e["uid"] for e in j["deadlines"]] == ["ev3@prova", "ev1@prova", "ev5@prova"] and j["deadlines"][1]["dir"] == str(home / "ws" / "pratiche" / "p001")
        and j["error"] == "" and TOKEN not in r.stdout, r.stdout + r.stderr)

# CA5: senza rete
t0 = time.time()
r = run("cm-recap.py", off_cfg)
T.check("CA5 offline: recap exits 0 with «calendario non raggiungibile» and the usual body",
        r.returncode == 0 and "SCADENZE" in r.stdout and "calendario non raggiungibile" in r.stdout and "Deposito" not in r.stdout
        and TOKEN not in r.stdout + r.stderr, r.stdout + r.stderr)
T.check("CA5 offline: no wait beyond timeout_s", time.time() - t0 < 30, str(time.time() - t0))

# CA6: spento
n_hits = len(HITS)
r = run("cm-recap.py", none_cfg)
T.check("CA6 no secrets file: no SCADENZE section, no request", r.returncode == 0 and "SCADENZE" not in r.stdout and len(HITS) == n_hits, r.stdout + r.stderr)
r = run("cm-calendar.py", none_cfg)
T.check("CA6 supervisor calendar says it is off and where the file goes", "calendario spento" in r.stdout and "calendar.env" in r.stdout, r.stdout + r.stderr)


# CA4: next, con una sessione vera di tmux (server privato) ferma per te
def proc_start(pid):
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat[stat.rindex(")") + 2:].split()[19]


procs = []
with T.PrivateTmux() as tm:
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "seed", "bash", "--norc"], env=env(ok_cfg, CM_TMUX_ARGS=tm.env["CM_TMUX_ARGS"]), check=True)
    tm("new-session", "-d", "-s", "alfa", "-x", "100", "-y", "30", "bash", "--norc")
    e = dict(os.environ); e.pop("CLAUDE_CONFIG_DIR", None)
    p = subprocess.Popen(["sleep", "600"], env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    time.sleep(0.2)
    t = int((time.time() - 300 * 60) * 1000)   # idle da 5 ore: da sola non sarebbe in next
    (home / ".claude" / "sessions" / f"{p.pid}.json").write_text(json.dumps({
        "pid": p.pid, "name": "alfa", "cwd": str(home / "ws" / "personali" / "alfa"), "status": "idle", "tmux": "alfa:@0.%0",
        "startedAt": t - 3600_000, "statusUpdatedAt": t, "updatedAt": t, "procStart": proc_start(p.pid), "sessionId": "sid-alfa"}))
    for st in (ok_state, off_state):
        (st / "needs-you").mkdir(parents=True, exist_ok=True)
        (st / "needs-you" / "sid-alfa.json").write_text(json.dumps({"reason": "serve il codice di accesso", "at": int(time.time()), "name": "alfa"}))
    xe = {"CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"]}
    r = run("cm-next.py", ok_cfg, "--json", **xe)
    rows = json.loads(r.stdout or "[]")
    order = [(x["priority"], x.get("kind", "session"), x["name"]) for x in rows]
    T.check("CA4 next: the needs-you session first (1), the deadlines right under it (3), nearest first",
            [o[:2] for o in order[:4]] == [(1, "session"), (3, "deadline"), (3, "deadline"), (3, "deadline")]
            and order[0][2] == "alfa" and order[1][2] == "Senza allarme" and order[2][2].startswith("Deposito memoria"), str(order) + r.stderr)
    dl = rows[2] if len(rows) > 2 else {}
    T.check("CA4 next: the deadline row carries case, folder and the launch hint; the session row the needs-you reason",
            dl.get("case") == "P-001" and dl.get("dir") == str(home / "ws" / "pratiche" / "p001")
            and "tra 3 giorni" in dl.get("why", "") and "supervisor launch ~/ws/pratiche/p001" in dl.get("why", "")
            and "serve il codice di accesso" in rows[0]["why"], json.dumps(rows, ensure_ascii=False)[:800])
    r = run("cm-next.py", ok_cfg, **xe)
    lines = r.stdout.splitlines()
    T.check("CA4 next table: alfa on the first line, a deadline on the second", len(lines) >= 2 and lines[0].startswith("1  alfa")
            and lines[1].startswith("3  Senza allarme") and TOKEN not in r.stdout, r.stdout + r.stderr)
    r = run("cm-next.py", off_cfg, **xe)
    T.check("CA5 offline: next still lists the needs-you session, no deadline, exit 0",
            r.returncode == 0 and r.stdout.startswith("1  alfa") and "scadenza" not in r.stdout, r.stdout + r.stderr)
for p in procs:
    p.kill()
srv.shutdown()
T.finish()
