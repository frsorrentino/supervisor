#!/usr/bin/env python3
"""`supervisor report` verso una sessione su un altro host: l'immagine va anche la' (27/11/2026, supervisor-report-win-27-11).

Alle 21:27 la master ha inoltrato a win:supervisor-app una segnalazione con la schermata: report la copiava solo nel
progetto di questo computer, e la sessione su win riceveva un percorso che la' non si apre; la master l'ha copiata a
mano. Ora l'immagine si scrive nella cartella della sessione sull'host con la stessa via degli allegati del relay
(Place e report_image_save, contratto 1.50), e nel prompt va il percorso dell'host. L'host e' finto
(tests/lib/fake-rsession-adapter.py: il disco W: e' una cartella, i messaggi si annotano in calls.jsonl).

RW1 --session win:supervisor-app: exit 0; l'immagine sull'host, nella cartella della sessione, <report.subdir>/<data>-
    <slug>.png, gli stessi byte; il prompt arrivato la' nomina il percorso dell'host e non quello di qui; qui resta
    l'archivio di prima, e l'uscita dice tutti e due i percorsi
RW2 l'host non prende l'immagine (la copia scade): exit 7, il motivo detto, nessun prompt mandato
RW3 un nome remoto che nemmeno un giro nuovo dell'host vede: exit 7, detto, nessun prompt e nessuna copia
RW4 senza immagine («-») verso la sessione remota: il testo arriva come prima, nessuna copia
RW5 una sessione nata sull'host dopo l'ultimo giro: un giro forzato di quell'host la trova, e l'immagine arriva
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
proj = ws / "personali" / "supervisor-app"
for d in (home / ".claude" / "sessions", proj):
    d.mkdir(parents=True, exist_ok=True)
winroot = tmp / "win"
winroot.mkdir()
WIN = {"kind": "windows-native", "transport": {"ssh": "win"}, "confirmed_at": "2026-10-01T23:50:00+02:00",
       "roles": ["compute", "render", "sessions"],
       "trust": {"level": "work", "accounts": ["personale"], "secrets": False, "clients": False},
       "sessions": {"root": "W:/cm", "allow": ["personali/*"], "deny": [], "start_timeout_s": 4}}
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(ws), "root_session_name": "master", "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}}, "default_account": "personale",
    "registry": {"file": str(tmp / "registry.json")}, "terminal": {"backend": "none"},
    "relay": {"enabled": False, "dir": str(tmp / "relay")},
    "hosts": {"win": WIN},
}))
# una sessione Remote Control nata su win (non da launch --host), come win:supervisor-app
(winroot / "registry.json").write_text(json.dumps([
    {"name": "supervisor-app", "sessionId": "sid-supervisor-app", "cwd": "W:\\cm-sessions\\personali\\supervisor-app",
     "status": "idle", "pid": 4242, "startedAt": int(time.time() * 1000), "bridgeSessionId": "session_FAKEAPP"}]))
ENV = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
       "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CM_RSESSION_ADAPTER": str(T.ROOT / "tests" / "lib" / "fake-rsession-adapter.py"),
       "FAKE_WIN_ROOT": str(winroot), "CM_PROC_SCAN_PIDS": "", "CM_LAUNCH_NO_TTY": "1"}
poll = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hosts.py"), "poll", "win", "--force"], capture_output=True, text=True, env=ENV, timeout=120)
img = tmp / "schermata.png"
img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes" * 64)
TEXT = "Il bottone compra non va"
SUB = ("docs", "segnalazioni")   # report.subdir di default con language «it» (cm-config)


def report(*args, **extra):
    """La riga di report, con `days`: i giorni fra una lettura dell'orologio prima e una dopo (T.days), dove cercare
    quello che lo script ha scritto nel nome del file (a cavallo della mezzanotte sono due)."""
    t0 = time.time()
    r = subprocess.run([str(T.SCRIPTS / "cm-report.sh"), *args], capture_output=True, text=True, env=dict(ENV, **extra), timeout=180)
    r.days = T.days(t0, time.time())
    return r


def day_of(r, *parts, name):
    """Il giorno di r.days per cui <parts>/<giorno>-<name> c'e'; l'ultimo se nessuno (il controllo poi lo dice)."""
    return next((d for d in r.days if Path(*parts, f"{d}-{name}").is_file()), r.days[-1])


def calls():
    p = winroot / "calls.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# RW1 l'immagine sull'host e il percorso dell'host nel prompt
n0 = len(calls())
r = report(str(proj), str(img), TEXT, "--session", "win:supervisor-app")
new = calls()[n0:]
posts = [c for c in new if c["verb"] == "post" and c.get("name") == "supervisor-app"]
day = day_of(r, winroot, "cm-sessions", "personali", "supervisor-app", *SUB, name="il-bottone-compra-non-va.png")
host_path = "W:\\cm-sessions\\personali\\supervisor-app\\" + "\\".join(SUB) + f"\\{day}-il-bottone-compra-non-va.png"
on_host = winroot.joinpath("cm-sessions", "personali", "supervisor-app", *SUB, f"{day}-il-bottone-compra-non-va.png")
here = proj.joinpath(*SUB, f"{day_of(r, proj, *SUB, name='il-bottone-compra-non-va.png')}-il-bottone-compra-non-va.png")   # il suo giorno: qui e la' sono due letture
text = posts[-1]["text"] if posts else ""
T.check("RW1 --session win:supervisor-app: exit 0; the image on the host in the session's folder, <report.subdir>/<date>-<slug>.png, same bytes; the prompt that reached it names the host's path, never the one here; the archive here stays and the output says both paths",
        poll.returncode == 0 and r.returncode == 0 and on_host.is_file() and on_host.read_bytes() == img.read_bytes()
        and len(posts) == 1 and f"Screenshot allegato, leggilo: {host_path}" in text and str(here) not in text and TEXT in text
        and here.is_file() and f"immagine: {here}" in r.stdout and f"sull'host: {host_path}" in r.stdout
        and any(c["verb"] == "file_put" and c["path"] == host_path for c in new),
        f"poll={poll.returncode}{poll.stderr[-200:]} rc={r.returncode} out={r.stdout[-400:]} err={r.stderr[-400:]} host={on_host.is_file()} posts={[p['text'][-200:] for p in posts]}")

# RW2 l'host non la prende: nessun prompt con un percorso che la' non si apre
n0 = len(calls())
r = report(str(proj), str(img), "Seconda schermata", "--session", "win:supervisor-app", FAKE_WIN_PUT_FAIL="1")
new = calls()[n0:]
T.check("RW2 the host does not take the image (the copy times out): exit 7, the reason said, no prompt sent",
        r.returncode == 7 and "non è arrivata sull'host di «win:supervisor-app»" in r.stderr and "connection timed out" in r.stderr
        and not [c for c in new if c["verb"] == "post"],
        f"rc={r.returncode} err={r.stderr[-400:]} calls={[c['verb'] for c in new]}")

# RW3 un nome remoto che nemmeno un giro nuovo dell'host vede
n0 = len(calls())
r = report(str(proj), str(img), "Terza schermata", "--session", "win:nessuna")
new = calls()[n0:]
T.check("RW3 a remote name not even a fresh poll of the host sees: exit 7, said, no prompt and no copy",
        r.returncode == 7 and "«win:nessuna» non è fra le sessioni vive sugli altri host" in r.stderr
        and not [c for c in new if c["verb"] in ("post", "file_put")],
        f"rc={r.returncode} err={r.stderr[-400:]} calls={[c['verb'] for c in new]}")

# RW4 senza immagine: come prima
n0 = len(calls())
r = report(str(proj), "-", "Solo testo", "--session", "win:supervisor-app")
new = calls()[n0:]
posts = [c for c in new if c["verb"] == "post"]
T.check("RW4 with no image («-») to the remote session: the text arrives as before, no copy",
        r.returncode == 0 and len(posts) == 1 and "Solo testo" in posts[0]["text"] and "Screenshot" not in posts[0]["text"]
        and not [c for c in new if c["verb"] == "file_put"],
        f"rc={r.returncode} err={r.stderr[-300:]} calls={[c['verb'] for c in new]}")

# RW5 una sessione nata sull'host dopo l'ultimo giro (appena lanciata la'): il giro forzato la trova
reg = json.loads((winroot / "registry.json").read_text())
reg.append({"name": "supervisor-web", "sessionId": "sid-supervisor-web", "cwd": "W:\\cm-sessions\\personali\\supervisor-web",
            "status": "idle", "pid": 4243, "startedAt": int(time.time() * 1000), "bridgeSessionId": "session_FAKEWEB"})
(winroot / "registry.json").write_text(json.dumps(reg))
n0 = len(calls())
r = report(str(proj), str(img), "Quarta schermata", "--session", "win:supervisor-web")
new = calls()[n0:]
day = day_of(r, winroot, "cm-sessions", "personali", "supervisor-web", *SUB, name="quarta-schermata.png")
web_path = "W:\\cm-sessions\\personali\\supervisor-web\\" + "\\".join(SUB) + f"\\{day}-quarta-schermata.png"
posts = [c for c in new if c["verb"] == "post"]
T.check("RW5 a session born on the host after the last poll: one forced poll of that host finds it, and the image arrives",
        r.returncode == 0 and winroot.joinpath("cm-sessions", "personali", "supervisor-web", *SUB, f"{day}-quarta-schermata.png").is_file()
        and len(posts) == 1 and f"Screenshot allegato, leggilo: {web_path}" in posts[0]["text"],
        f"rc={r.returncode} err={r.stderr[-300:]} calls={[c['verb'] for c in new]} posts={[p['text'][-160:] for p in posts]}")

T.rm(tmp)
T.finish()
