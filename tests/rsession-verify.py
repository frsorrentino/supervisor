#!/usr/bin/env python3
"""Verifica le sessioni su un altro host (cm-rsession.py, launch --host, host fetch; piano multi-PC 4.2, fase 2.1).
L'host e' finto (tests/lib/fake-rsession-adapter.py): una cartella locale con il suo registro peer.

RS1 ammissione: personali/x si'; pro/x no (account); personali/kb e personali/kb/sub no (deny); cartella fuori da
    allow no; client_paths no; host non confermato, senza ruolo sessions o di altro tipo no; ricetta con segreti no
RS2 launch --host win: fotografia di HEAD senza .env, repository nuovo col commit base, fiducia, avvio con
    --remote-control e -n win-<nome> dopo gli argomenti di config, link dal registro, record locale; il tetto locale
    non conta; le modifiche non committate restano qui e lo si dice
RS3 un segreto nel contenuto (chiave privata) → rifiuto con il file, niente parte
RS4 host fetch: i commit fatti la' (anche un file CRLF e uno binario) → ramo win/<nome> sopra la base, autore
    conservato; il checkout condiviso non cambia, nessun worktree resta; senza commit nuovi lo dice
RS5 secondo lancio sulla stessa cartella: la cartella la' si riusa (niente nuova fotografia), nome win-<nome>-2
RS6 cartella gia' presente la' ma non nostra → rifiuto; sessione che non si registra → exit 5 con il messaggio
RS8 talk win:<nome> (e il solo nome registrato): il testo arriva intero (virgolette, backtick, $, a capo, accenti),
    la risposta si legge dal transcript la'; la casella locale lo segna consegnato via pipe; --no-wait torna subito
RS9 wait win:<nome>: torna quando e' idle e stampa l'ultima risposta
RS10 close win:<nome>: busy → rifiuto (exit 4) senza --force; con --force chiusa, record segnato; di nuovo: «non era
     in esecuzione»; close --dry-run non chiude
RS11 talk a una sessione chiusa: exit 5, il messaggio resta nella casella in attesa
RS7 proposta e scelta automatica: regia carica → riga di proposta (il lancio resta in locale); --host auto carica →
    remoto, scarica → locale; una cartella non ammessa non e' mai proposta
"""
import json
import re
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
for d in (".claude/sessions", ".claude-pixel/sessions", "ws/personali/alfa", "ws/personali/kb/sub", "ws/pro/beta",
          "ws/altro/gamma", "ws/personali/cliente-x", "ws/personali/ricetta", "ws/personali/segreto", "ws/personali/locale"):
    (home / d).mkdir(parents=True, exist_ok=True)
winroot = tmp / "win"
winroot.mkdir()
WIN = {"kind": "windows-native", "transport": {"ssh": "win"}, "confirmed_at": "2026-10-01T23:50:00+02:00",
       "roles": ["compute", "render", "sessions"],
       "trust": {"level": "work", "accounts": ["personale"], "secrets": False, "clients": False},
       "sessions": {"root": "W:/cm", "allow": ["personali/*"], "deny": ["personali/kb", "personali/docs"],
                    "start_timeout_s": 4}}
cfg = tmp / "config.json"
base_cfg = {
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(ws), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "default_account": "personale",
    "folder_map": [{"path": str(ws / "pro"), "account": "professionale"}, {"path": str(ws / "personali"), "account": "personale"}],
    "session": {"dialog_patterns": ["trust (this|the) folder", "Is this a project you", "Bypass Permissions mode"], "claude_args": ["--dangerously-skip-permissions"], "startup_timeout_s": 25, "death_check_s": 1},
    "terminal": {"backend": "none"},
    "registry": {"file": str(tmp / "registry.json")},
    "sessions": {"max_sessions": 0},   # tetto locale a zero: un lancio remoto non ne tiene conto
    "scheduler": {"client_paths": [str(ws / "personali" / "cliente-x")], "session_offload": {"load_per_core": 1.0, "free_ram_gb": 2}},
    "hosts": {"win": WIN,
              "spento": dict(WIN, confirmed_at=None),
              "senza": dict(WIN, roles=["compute"]),
              "linux": dict(WIN, kind="linux-tmux")},
}
cfg.write_text(json.dumps(base_cfg))
FAKE_AD = T.ROOT / "tests" / "lib" / "fake-rsession-adapter.py"
CRON = tmp / "crontab"   # 09/10: launch --host mette il sondatore degli host: mai nel crontab vero
CRON.write_text("")
FAKE_CRON = tmp / "crontab.sh"
FAKE_CRON.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s.tmp" && mv "%s.tmp" "%s"; fi\n' % (CRON, CRON, CRON, CRON))
FAKE_CRON.chmod(0o755)
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CM_RSESSION_ADAPTER": str(FAKE_AD), "FAKE_WIN_ROOT": str(winroot),
         "CM_CRONTAB_CMD": str(FAKE_CRON), "CM_LOADAVG": "0.1", "CM_NPROC": "8", "CM_MEMAVAIL_GB": "5", "CM_PROC_SCAN_PIDS": "", "CM_LAUNCH_NO_TTY": "1",
         "GIT_AUTHOR_NAME": "Prova", "GIT_AUTHOR_EMAIL": "prova@example.com", "GIT_COMMITTER_NAME": "Prova",
         "GIT_COMMITTER_EMAIL": "prova@example.com"}
    e.update(extra)
    return e


def rs(*args, **extra):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-rsession.py"), *args], capture_output=True, text=True, env=env(**extra), timeout=120)


def launch(*args, **extra):
    return subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), *args], capture_output=True, text=True, env=env(**extra), timeout=120)


def g(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, env=env(), check=True).stdout


def repo(d, files):
    g(d, "init", "-q")
    g(d, "config", "user.name", "Ada Prova")
    g(d, "config", "user.email", "ada@example.com")
    for k, v in files.items():
        (d / k).parent.mkdir(parents=True, exist_ok=True)
        (d / k).write_text(v)
    g(d, "add", "-A")
    g(d, "commit", "-q", "-m", "inizio")


def calls():
    p = winroot / "calls.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


# RS1
alfa = ws / "personali" / "alfa"
repo(alfa, {"README.md": "alfa\n", "src/a.txt": "uno\n", ".env": "TOKEN=x\n"})
(alfa / "base-crlf.txt").write_bytes(b"uno\r\ndue\r\n")
g(alfa, "add", "base-crlf.txt")
g(alfa, "commit", "-q", "-m", "un file CRLF nella base")
(ws / "personali" / "ricetta" / ".cm-offload.json").write_text(json.dumps({"recipes": {"r": {"needs": {"secrets": True}}}}))
cases = [("win", alfa, 0, "ammessa"), ("win", ws / "pro" / "beta", 1, "account"), ("win", ws / "personali" / "kb", 1, "personali/kb"),
         ("win", ws / "personali" / "kb" / "sub", 1, "personali/kb"), ("win", ws / "altro" / "gamma", 1, "allow"),
         ("win", ws / "personali" / "cliente-x", 1, "clienti"), ("spento", alfa, 1, "confermato"), ("senza", alfa, 1, "sessions"),
         ("linux", alfa, 1, "windows-native"), ("win", ws / "personali" / "ricetta", 1, "segreti"), ("boh", alfa, 1, "boh")]
bad = []
for h, d, want, word in cases:
    r = rs("admit", h, str(d))
    if r.returncode != want or word not in (r.stdout + r.stderr):
        bad.append(f"{h} {d.name}: rc {r.returncode} «{(r.stdout + r.stderr).strip()[:160]}»")
T.check("RS1 admission: the four conditions, each refusal names its rule", not bad, "\n".join(bad))

# RS2
(alfa / "nuovo-non-committato.txt").write_text("resta qui\n")
r = launch(str(alfa), "--host", "win", "--no-window")
out = r.stdout + r.stderr
rdir = winroot / "cm" / "personali" / "alfa"
starts = [c for c in calls() if c["verb"] == "start"]
rec = tmp / "state" / "rsessions" / "win-alfa.json"
T.check("RS2 launch --host win: exit 0, name win-alfa, link from the host's registry, local record",
        r.returncode == 0 and "win-alfa" in out and "https://claude.ai/code/session_FAKE0" in out and rec.is_file(), out)
T.check("RS2 (09/10) the hosts poller was missing → put in the crontab and said, once",
        CRON.read_text().count("hosts poll --cron") == 1 and "sondatore degli host non era nel crontab" in out, CRON.read_text() + out)
T.check("RS2 the snapshot of HEAD becomes a new repository there: base commit, .cm-session.json, no .env, no uncommitted file",
        (rdir / "README.md").is_file() and (rdir / "src" / "a.txt").is_file() and not (rdir / ".env").exists()
        and not (rdir / "nuovo-non-committato.txt").exists() and (rdir / ".cm-session.json").is_file()
        and g(rdir, "log", "--format=%s").strip().startswith("supervisor: base " + g(alfa, "rev-parse", "HEAD").strip()), out)
T.check("RS2 trust written before the start; args: config's claude_args, then --remote-control win-alfa -n win-alfa",
        [c["verb"] for c in calls() if c["verb"] in ("trust", "start")] == ["trust", "start"]
        and starts and starts[0]["args"] == ["--dangerously-skip-permissions", "--remote-control", "win-alfa", "-n", "win-alfa"], json.dumps(starts))
T.check("RS2 the local cap (max_sessions 0) does not hold a remote launch; uncommitted files: said, not sent",
        "sessioni già al lavoro" not in out and "nuovo-non-committato.txt" in out, out)

# RS3
seg = ws / "personali" / "segreto"
repo(seg, {"chiave.txt": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n"})
n0 = len(calls())
r = launch(str(seg), "--host", "win", "--no-window")
T.check("RS3 a private key in a tracked file → refused naming the file, nothing sent",
        r.returncode == 6 and "chiave.txt" in r.stderr and not any(c["verb"] == "seed" for c in calls()[n0:]), r.stdout + r.stderr)

# RS4
(rdir / "src" / "a.txt").write_text("uno\ndue\n")
(rdir / "win.txt").write_bytes(b"riga uno\r\nriga due\r\n")
(rdir / "img.bin").write_bytes(bytes(range(256)) * 4)
(rdir / "base-crlf.txt").write_bytes(b"uno\r\ndue\r\ntre da win\r\n")   # un file CRLF gia' nella base, cambiato la'
subprocess.run(["git", "-C", str(rdir), "add", "-A"], check=True)
subprocess.run(["git", "-C", str(rdir), "commit", "-q", "-m", "lavoro fatto su win", "--author", "Ada Prova <ada@example.com>"], check=True)
(rdir / "sporco.txt").write_text("non committato\n")
head_before = g(alfa, "rev-parse", "HEAD").strip()
branch_before = g(alfa, "branch", "--show-current").strip()
r = rs("fetch", "win", "win-alfa")
out = r.stdout + r.stderr
ok_branch = subprocess.run(["git", "-C", str(alfa), "rev-parse", "--verify", "-q", "win/alfa"], capture_output=True, text=True).returncode == 0
T.check("RS4 host fetch: exit 0, branch win/alfa with the commit on top of the base, author kept; dirty files there said",
        r.returncode == 0 and ok_branch and g(alfa, "log", "-1", "--format=%s|%an", "win/alfa").strip() == "lavoro fatto su win|Ada Prova"
        and g(alfa, "rev-parse", "win/alfa~1").strip() == head_before and "1" in out and "non committat" in out, out)
T.check("RS4 CRLF and binary files arrive byte for byte, also a CRLF file of the base changed there",
        subprocess.run(["git", "-C", str(alfa), "show", "win/alfa:win.txt"], capture_output=True).stdout == b"riga uno\r\nriga due\r\n"
        and subprocess.run(["git", "-C", str(alfa), "show", "win/alfa:img.bin"], capture_output=True).stdout == bytes(range(256)) * 4
        and subprocess.run(["git", "-C", str(alfa), "show", "win/alfa:base-crlf.txt"], capture_output=True).stdout == b"uno\r\ndue\r\ntre da win\r\n", out)
T.check("RS4 the shared checkout is untouched: same HEAD, same branch, no worktree left",
        g(alfa, "rev-parse", "HEAD").strip() == head_before and g(alfa, "branch", "--show-current").strip() == branch_before
        and len(g(alfa, "worktree", "list").strip().splitlines()) == 1, g(alfa, "worktree", "list"))
(rdir / "sporco.txt").unlink()
seg2 = ws / "personali" / "locale"
repo(seg2, {"x.txt": "x\n"})
r0 = launch(str(seg2), "--host", "win", "--no-window")
r = rs("fetch", "win", "win-locale")
T.check("RS4 no new commits there → says so, exit 0, no branch", r0.returncode == 0 and r.returncode == 0 and "nessun commit" in r.stdout
        and subprocess.run(["git", "-C", str(seg2), "rev-parse", "--verify", "-q", "win/locale"], capture_output=True).returncode != 0, r0.stdout + r0.stderr + r.stdout + r.stderr)

# RS5
n0 = len(calls())
r = launch(str(alfa), "--host", "win", "--no-window")
T.check("RS5 second launch on the same folder: the folder there is reused (no new snapshot), name win-alfa-2",
        r.returncode == 0 and "win-alfa-2" in r.stdout and not any(c["verb"] == "seed" for c in calls()[n0:]) and "riuso" in r.stderr.lower(), r.stdout + r.stderr)

# RS6
(winroot / "cm" / "personali" / "kb2").mkdir(parents=True)
kb2 = ws / "personali" / "kb2"
kb2.mkdir()
repo(kb2, {"a": "a\n"})
r = launch(str(kb2), "--host", "win", "--no-window")
T.check("RS6 a folder already there but not ours → refused, nothing sent", r.returncode == 6 and "non è di supervisor" in r.stderr, r.stdout + r.stderr)
nr = ws / "personali" / "noreg"
nr.mkdir()
repo(nr, {"a": "a\n"})
r = launch(str(nr), "--host", "win", "--no-window", FAKE_WIN_NOREG="1")
T.check("RS6 a session that never registers → exit 5 saying where to look", r.returncode == 5 and "win" in r.stderr and "4" in r.stderr, r.stdout + r.stderr)

# RS7
r = rs("propose", str(alfa), CM_LOADAVG="12", CM_NPROC="8")
r2 = rs("propose", str(ws / "pro" / "beta"), CM_LOADAVG="12")
r3 = rs("propose", str(alfa))
r4 = rs("propose", str(alfa), CM_MEMAVAIL_GB="1.2")
T.check("RS7 loaded control machine (load > cores, or free RAM < 2 GB) → one proposal line naming --host win; not for a refused folder; none when idle",
        "--host win" in r.stderr and "12.0" in r.stderr and r2.stderr == "" and r3.stderr == "" and "--host win" in r4.stderr, r.stderr + "|" + r2.stderr + "|" + r3.stderr + "|" + r4.stderr)
p1 = rs("pick", str(alfa), CM_LOADAVG="12").stdout.strip()
p2 = rs("pick", str(alfa)).stdout.strip()
p3 = rs("pick", str(ws / "pro" / "beta"), CM_LOADAVG="12").stdout.strip()
T.check("RS7 pick: win when loaded, the control machine when idle or when the folder is not admitted", p1 == "win" and p2 == "" and p3 == "", f"{p1!r} {p2!r} {p3!r}")
gam = ws / "personali" / "gamma2"
gam.mkdir()
repo(gam, {"a": "a\n"})
r = launch(str(gam), "--host", "auto", "--no-window", CM_LOADAVG="12")
T.check("RS7 --host auto with the control machine loaded → launched on win", r.returncode == 0 and "win-gamma2" in r.stdout, r.stdout + r.stderr)
with T.PrivateTmux() as tm:
    cfg.write_text(json.dumps(dict(base_cfg, sessions={"max_sessions": 50})))
    loc = {"CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(FAKE), "FAKE_CLAUDE_SCENARIO": "plain"}
    r = launch(str(ws / "personali" / "locale"), "--host", "auto", "--no-window", **loc)
    T.check("RS7 --host auto with the control machine idle → launched here, in tmux", r.returncode == 0 and tm("has-session", "-t", "=locale").returncode == 0, r.stdout + r.stderr)
    r = launch(str(ws / "personali" / "alfa"), "--no-window", CM_LOADAVG="12", **loc)
    T.check("RS7 plain launch with the control machine loaded: launched here, with the proposal line",
            r.returncode == 0 and "--host win" in r.stderr and tm("has-session", "-t", "=alfa").returncode == 0, r.stdout + r.stderr)
# RS8-RS11 (fase 2.2): talk, wait, close
cfg.write_text(json.dumps(base_cfg))
TALK = [sys.executable, str(T.SCRIPTS / "cm-talk.py"), "talk"]
CLOSE = [str(T.SCRIPTS / "cm-close.sh")]
tricky = 'Rispondi "OK" con `echo $HOME` e l\'apostrofo;\nseconda riga: perché è così?'
r = subprocess.run(TALK + ["win:win-alfa", tricky, "--wait", "30"], capture_output=True, text=True, env=env(), timeout=90)
posts = [c for c in calls() if c["verb"] == "post"]
inbox_recs = [json.loads(p.read_text()) for p in (tmp / "state" / "inbox" / "win-alfa").glob("*.json")] if (tmp / "state" / "inbox" / "win-alfa").exists() else []
T.check("RS8 talk win:win-alfa: the text arrives whole (quotes, backtick, $, newline, accents), the reply comes from the transcript there",
        r.returncode == 0 and posts and posts[-1]["text"] == tricky and ("RISPOSTA: " + tricky) in r.stdout, r.stdout + r.stderr)
T.check("RS8 the local inbox has it, delivered via pipe:win", any(x["text"] == tricky and x["status"] == "delivered" and x.get("via") == "pipe:win" for x in inbox_recs), json.dumps(inbox_recs)[:400])
T.check("RS8 without a sender the signature is talk.from_name", posts and posts[-1]["from_name"] == "supervisor", str(posts[-1:]))
r = subprocess.run(TALK + ["win:win-alfa", "firmato", "--no-wait"], capture_output=True, text=True, env=env(CM_TALK_SENDER="master"), timeout=60)
_p8 = [c for c in calls() if c["verb"] == "post"]
T.check("RS8b (21/10) the sender's name signs the message there too (CM_TALK_SENDER, set by cm-talk from the tmux session)",
        r.returncode == 0 and _p8 and _p8[-1]["text"] == "firmato" and _p8[-1]["from_name"] == "master", str(_p8[-1:]) + r.stderr)
r = subprocess.run(TALK + ["win-alfa", "secondo", "--no-wait"], capture_output=True, text=True, env=env(), timeout=60)
T.check("RS8 the bare registered name works too; --no-wait returns without the reply", r.returncode == 0 and "RISPOSTA" not in r.stdout and calls()[-1]["verb"] == "post", r.stdout + r.stderr)
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "wait", "win:win-alfa", "--timeout", "30"], capture_output=True, text=True, env=env(), timeout=90)
T.check("RS9 wait win:win-alfa: back at idle with the last reply", r.returncode == 0 and "RISPOSTA: secondo" in r.stdout, r.stdout + r.stderr)
r = subprocess.run(CLOSE + ["win:win-alfa"], capture_output=True, text=True, env=env(FAKE_WIN_STATUS="win-alfa:busy"), timeout=60)
T.check("RS10 close of a busy remote session without --force → exit 4, says why", r.returncode == 4 and "busy" in r.stderr and "--force" in r.stderr, r.stdout + r.stderr)
nclose = sum(c["verb"] == "close" for c in calls())
r = subprocess.run(CLOSE + ["--dry-run", "win:win-alfa"], capture_output=True, text=True, env=env(), timeout=60)
T.check("RS10 close --dry-run says it would close and does not call the host", r.returncode == 0 and "win:win-alfa" in r.stdout
        and sum(c["verb"] == "close" for c in calls()) == nclose, r.stdout + r.stderr)
r = subprocess.run(CLOSE + ["win:win-alfa", "--force"], capture_output=True, text=True, env=env(FAKE_WIN_STATUS="win-alfa:busy"), timeout=60)
recj = json.loads((tmp / "state" / "rsessions" / "win-alfa.json").read_text())
T.check("RS10 close --force closes it; the local record says closed", r.returncode == 0 and "chiusa" in r.stdout and recj.get("closed"), r.stdout + r.stderr)
r = subprocess.run(CLOSE + ["win:win-alfa"], capture_output=True, text=True, env=env(), timeout=60)
T.check("RS10 closing it again: «was not running»", r.returncode == 0 and "non era in esecuzione" in r.stdout, r.stdout + r.stderr)
r = subprocess.run(TALK + ["win:win-alfa", "dopo la chiusura"], capture_output=True, text=True, env=env(), timeout=60)
inbox_recs = [json.loads(p.read_text()) for p in (tmp / "state" / "inbox" / "win-alfa").glob("*.json")]
T.check("RS11 talk to a closed remote session → exit 5, the message waits in the inbox", r.returncode == 5
        and any(x["text"] == "dopo la chiusura" and x["status"] == "pending" for x in inbox_recs), r.stdout + r.stderr)

# RS12 remote-session-chat (09/10, dal telefono: la chat di una sessione su win vuota, la sua domanda senza
# risposta): la copia locale del transcript, la domanda letta da li', la risposta con i tasti nella console di la'
_reg = json.loads((winroot / "registry.json").read_text())
_reg.append({"name": "win-chat", "sessionId": "sid-win-chat", "cwd": "W:/cm/personali/chat", "status": "waiting", "pid": 4242})
(winroot / "registry.json").write_text(json.dumps(_reg))
_ask = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "AskUserQuestion", "input": {"questions": [
    {"question": "Quale colore per la copertina?", "header": "Colore", "multiSelect": False,
     "options": [{"label": "Rosso", "description": "caldo"}, {"label": "Verde"}, {"label": "Blu"}]}]}}]}}
_tr = winroot / "transcript-win-chat.jsonl"
_tr.write_text(json.dumps({"type": "user", "timestamp": "2026-10-09T20:30:00.000Z", "message": {"role": "user", "content": "prepara la copertina"}}) + "\n"
               + json.dumps({"type": "assistant", "timestamp": "2026-10-09T20:30:05.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "Ti chiedo il colore."}]}}) + "\n"
               + json.dumps(dict(_ask, timestamp="2026-10-09T20:30:06.000Z")) + "\n")
(winroot / "dialog-win-chat.json").write_text(json.dumps({"id": "tu1", "header": "Colore", "question": "Quale colore per la copertina?",
                                                          "options": ["Rosso", "Verde", "Blu"], "cursor": 1}))
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hosts.py"), "poll", "win", "--force"], capture_output=True, text=True, env=env(), timeout=120)
_mirror = tmp / "state" / "hosts" / "transcripts" / "win" / "sid-win-chat.jsonl"
T.check("RS12 remote-session-chat: the poller copies the transcript of a session waiting on win, byte for byte",
        _mirror.is_file() and _mirror.read_bytes() == _tr.read_bytes(), r.stdout + r.stderr)
PROBE = """
import importlib.util, json, sys
def L(n):
    s = importlib.util.spec_from_file_location(n.replace('-', '_'), sys.argv[1] + '/' + n + '.py'); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
hm, core = L('cm-hosts'), L('cm-core')
if len(sys.argv) > 2:
    hm.sync_transcript('win', 'win-chat', 'sid-win-chat')   # come la chat chiesta dal telefono
row = next(r for r in hm.remote_session_rows() if r['remote_name'] == 'win-chat')
rs = L('cm-rsession')
t = core.transcript_of(row)
st = L('cm-relay-state')
ev = core.remote_events(row)
print(json.dumps({'transcript': t, 'ask': core.pending_ask(t), 'entries': [e['text'] for e in core.transcript_entries(t, 0)],
                  'events': ev, 'outcome': st._outcome(ev), 'local_dir': row.get('local_dir'), 'name': row['name'],
                  'resolve': [rs.resolve('win:chat'), rs.resolve('win:win-chat')]}))
"""
def probe(sync=False):
    p = subprocess.run([sys.executable, "-c", PROBE, str(T.SCRIPTS)] + (["sync"] if sync else []), capture_output=True, text=True, env=env(), timeout=60)
    return json.loads(p.stdout) if p.returncode == 0 else {"error": p.stderr[-400:]}
(tmp / "state" / "rsessions").mkdir(parents=True, exist_ok=True)
(tmp / "state" / "rsessions" / "win-chat.json").write_text(json.dumps({"name": "win-chat", "host": "win", "dir": str(ws / "personali" / "chat")}))
pr = probe()
T.check("RS12 remote-session-chat (10/10): the card's preview — prompt and stop lines from the copy, the outcome built as for a local session; local_dir from the launch record for «Prossimi»",
        sorted(e["event"] for e in pr.get("events", [])) == ["prompt", "stop"]
        and (pr.get("outcome") or {}).get("short") == "Ti chiedo il colore." and pr.get("local_dir") == str(ws / "personali" / "chat"), json.dumps(pr)[:600])
T.check("RS12 remote-session-chat (10/10): with its launch record the session is published as win:chat, not win:win-chat; both names resolve to win-chat there",
        pr.get("name") == "win:chat" and pr.get("resolve") == [["win", "win-chat"], ["win", "win-chat"]], json.dumps(pr)[:300])
T.check("RS12 remote-session-chat: the row win:win-chat reads its conversation from the copy, and the open question with its options from the transcript",
        pr.get("transcript") == str(_mirror) and pr.get("ask") == ["Quale colore per la copertina?", ["Rosso", "Verde", "Blu"]]
        and "Ti chiedo il colore." in pr.get("entries", []), json.dumps(pr)[:500])
ANSWER = [sys.executable, str(T.SCRIPTS / "cm-answer.py")]
r = subprocess.run(ANSWER + ["win:win-chat", "--show"], capture_output=True, text=True, env=env(), timeout=60)
T.check("RS12 remote-session-chat: answer win:win-chat --show reads the dialog from the console there (cursor «>»)",
        r.returncode == 0 and "Quale colore per la copertina?" in r.stdout and "2. Verde" in r.stdout and "4." not in r.stdout, r.stdout + r.stderr)
n0 = len(calls())
r = subprocess.run(ANSWER + ["win:win-chat", "3"], capture_output=True, text=True, env=env(), timeout=60)
cons = [c["keys"] for c in calls()[n0:] if c["verb"] == "console" and c["keys"]]
d = json.loads((winroot / "dialog-win-chat.json").read_text())
T.check("RS12 remote-session-chat: answer win:win-chat 3 → Down, Down, checked on the screen, then Enter: option 3 chosen there",
        r.returncode == 0 and cons == [["Down", "Down"], ["Enter"]] and d.get("chosen") == 3 and "Blu" in r.stdout, r.stdout + r.stderr + json.dumps(cons))
pr = probe(sync=True)
T.check("RS12 remote-session-chat: after the answer, the chat asked again brings the tool_result into the copy and no question is open",
        pr.get("ask") is None and _mirror.read_bytes() == _tr.read_bytes(), json.dumps(pr)[:300])
# un cambio nelle sessioni remote chiede una push al relay (09/10: una sessione chiusa restava «al lavoro» nell'app)
cfg.write_text(json.dumps(dict(base_cfg, relay={"enabled": True, "dir": str(tmp / "relay")})))
_req = tmp / "relay" / "push-req" / "hosts"
subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hosts.py"), "poll", "win", "--force"], capture_output=True, text=True, env=env(), timeout=120)
_req.unlink(missing_ok=True)
subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hosts.py"), "poll", "win", "--force"], capture_output=True, text=True, env=env(), timeout=120)
_quiet = not _req.exists()
_reg = json.loads((winroot / "registry.json").read_text())
for _e in _reg:
    if _e["name"] == "win-chat":
        _e["closed"] = True
(winroot / "registry.json").write_text(json.dumps(_reg))
subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hosts.py"), "poll", "win", "--force"], capture_output=True, text=True, env=env(), timeout=120)
T.check("RS12 remote-session-chat: a remote session closed → the poller asks the relay for a push (push-req/hosts); nothing changed → no request",
        _quiet and _req.exists(), str((_quiet, _req.exists())))
cfg.write_text(json.dumps(base_cfg))
# 10/10: model ed effort di una sessione su win passano dalla console di la' (il finto non ha il selettore: uscita 4)
n0 = len(calls())
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-tune.py"), "effort", "win:chat", "high"], capture_output=True, text=True, env=env(), timeout=200)
sent = [c["keys"] for c in calls()[n0:] if c["verb"] == "console" and c.get("name") == "win-chat" and c["keys"]]
T.check("RS12 remote-session-chat (10/10): effort win:chat goes to the console of win-chat (/effort typed, then Enter), not «no session»; no picker there → exit 4",
        r.returncode == 4 and sent and sent[0][:1] == ["text:/effort"] and "Enter" in sent[0], r.stdout + r.stderr + json.dumps(sent)[:300])
r = subprocess.run(ANSWER + ["win:win-nessuna", "1"], capture_output=True, text=True, env=env(), timeout=60)
T.check("RS12 remote-session-chat: a remote name with nothing to answer → no keys sent, exit 1", r.returncode == 1
        and not [c for c in calls() if c["verb"] == "console" and c.get("name") == "win-nessuna" and c["keys"]], r.stdout + r.stderr)

# RS13 remote-session-file (10/10, dal telefono: i file di una sessione su win «missing or unreadable»): il relay
# prende il file dall'host — misura la', copia qui — e da li' la strada di sempre; troppo grande o assente → rifiuto
(winroot / "cm" / "personali" / "chat" / "docs").mkdir(parents=True, exist_ok=True)
(winroot / "cm" / "personali" / "chat" / "docs" / "film.mp4").write_bytes(b"\x00film" * 1000)
_reg = json.loads((winroot / "registry.json").read_text())
for _e in _reg:
    if _e["name"] == "win-chat":
        _e["closed"] = False
(winroot / "registry.json").write_text(json.dumps(_reg))
with open(_tr, "a") as _f:
    for _fp in ("W:\\cm\\personali\\chat\\docs\\film.mp4", "W:\\cm\\personali\\chat\\docs\\manca.mp4"):
        _f.write(json.dumps({"type": "assistant", "timestamp": "2026-10-10T15:00:00.000Z", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "w" + _fp[-7:-4], "name": "Write", "input": {"file_path": _fp, "content": "x"}}]}}) + "\n")
FPROBE = """
import importlib.util, json, sys
def L(n):
    s = importlib.util.spec_from_file_location(n.replace('-', '_'), sys.argv[1] + '/' + n + '.py'); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
hm, core, rl = L('cm-hosts'), L('cm-core'), L('cm-relay')
hm.sync_transcript('win', 'win-chat', 'sid-win-chat')
row = next(r for r in hm.remote_session_rows() if r['remote_name'] == 'win-chat')
rl._json_cmd = lambda *a, **k: [dict(row, tmux=row['name'])]
files = [fr.get('path') for e in core.transcript_entries(core.transcript_of(row), 0) for fr in (e.get('files') or [])]
out = {'files': files}
for tag, path in (('ok', files[0] if files else ''), ('missing', files[1] if len(files) > 1 else '')):
    out[tag] = rl.file_open({'id': 'f' + tag, '_local': True}, row['name'], row['name'], path)
ref = rl.local_dir('file') / 'fok.json'
out['ref'] = json.loads(ref.read_text()) if ref.is_file() else None
rl.FILE_PARTS_MAX = 100
out['big'] = rl.file_open({'id': 'fbig', '_local': True}, row['name'], row['name'], files[0] if files else '')
print(json.dumps(out))
"""
subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hosts.py"), "poll", "win", "--force"], capture_output=True, text=True, env=env(), timeout=120)
cfg.write_text(json.dumps(dict(base_cfg, relay={"enabled": True, "dir": str(tmp / "relay")})))
p = subprocess.run([sys.executable, "-c", FPROBE, str(T.SCRIPTS)], capture_output=True, text=True, env=env(), timeout=120)
cfg.write_text(json.dumps(base_cfg))
fr = json.loads(p.stdout) if p.returncode == 0 else {"error": p.stderr[-600:]}
_ref = fr.get("ref") or {}
T.check("RS13 remote-session-file: a file listed in the conversation of win:chat is measured and copied from there; the local web gets the copy, same bytes, mime from the name",
        (fr.get("ok") or [False])[0] is True and _ref.get("name") == "film.mp4" and _ref.get("mime") == "video/mp4"
        and Path(_ref.get("path") or "/nonexistent").read_bytes() == b"\x00film" * 1000, json.dumps(fr)[:700])
T.check("RS13 remote-session-file: a listed file missing there → unreadable; over FILE_PARTS_MAX there → too large, nothing copied",
        (fr.get("missing") or [True])[0] is False and (fr.get("big") or [True])[0] is False and "5000" in str((fr.get("big") or ["", ""])[1])
        and len([c for c in calls() if c["verb"] == "file_get"]) == 1, json.dumps(fr)[:700])

# RS15 remote-master-handoff (10/10, il maintainer: una sessione su win non poteva passare un compito alla master): il poller
# inoltra alla master i blocchi «Per la master:» dei testi dell'assistente, una volta sola; mai tool o messaggi utente
_reg = json.loads((winroot / "registry.json").read_text())
_reg.append({"name": "win-fwd", "sessionId": "sid-win-fwd", "cwd": "W:/cm/personali/fwd", "status": "idle", "pid": 4343})
(winroot / "registry.json").write_text(json.dumps(_reg))
def _a15(uid, blocks, ago=0):
    import datetime as _dt15
    ts = (_dt15.datetime.now(_dt15.timezone.utc) - _dt15.timedelta(seconds=ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return json.dumps({"type": "assistant", "uuid": uid, "timestamp": ts, "message": {"role": "assistant", "content": blocks}}) + "\n"
(winroot / "transcript-win-fwd.jsonl").write_text(
    json.dumps({"type": "user", "uuid": "u1", "message": {"role": "user", "content": "Per la master: dal messaggio utente, non deve partire"}}) + "\n"
    + json.dumps({"type": "user", "uuid": "u2", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Per la master: da una pagina web, non deve partire"}]}}) + "\n"
    + _a15("a1", [{"type": "text", "text": "Ho finito il montaggio.\n\nPer la master:\nrenderizza il film in 4K\ncon l'audio nuovo.\n\nEsito: montaggio pronto\nProssimi: render · audio\nWatch: montaggio pronto"}]))
_inbox15 = tmp / "state" / "inbox" / "master"
_rg15 = tmp / "registry.json"   # la master nota al registro e chiusa: il messaggio resta nella sua casella (talk esce 0)
_rd15 = json.loads(_rg15.read_text()) if _rg15.is_file() else {}
_rd15.setdefault("sessioni", []).append({"nome": "master", "cartella": str(ws)})
_rg15.write_text(json.dumps(_rd15))
def _fw15():
    return sorted(json.loads(x.read_text())["text"] for x in _inbox15.glob("*.json")) if _inbox15.is_dir() else []
_n15 = len(_fw15())
r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hosts.py"), "poll", "win", "--force"], capture_output=True, text=True, env=env(), timeout=120)
_got15 = [t for t in _fw15() if "win:win-fwd" in t]
T.check("RS15 remote-master-handoff: the block «Per la master:» of an assistant text goes to the master once, headed «Da win:win-fwd, HH:MM:», without the Esito/Prossimi/Watch lines; the user message and the tool result with the marker do not",
        len(_got15) == 1 and re.match(r"Da win:win-fwd, \d\d:\d\d:\nPer la master:\nrenderizza il film in 4K\ncon l'audio nuovo\.$", _got15[0]) is not None
        and not any("non deve partire" in t for t in _fw15()), r.stdout + r.stderr + json.dumps(_fw15())[-600:])
subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hosts.py"), "poll", "win", "--force"], capture_output=True, text=True, env=env(), timeout=120)
with open(winroot / "transcript-win-fwd.jsonl", "a") as _f15:
    # 21:17 del 10/10: la copia indietro porta dopo il punto letto un blocco scritto prima dell'attivazione — non parte
    _f15.write(_a15("a0", [{"type": "text", "text": "**Per la master: il blocco vecchio**\nda non inoltrare"}], ago=86400))
    _f15.write(_a15("a2", [{"type": "text", "text": "For the master: second task\nin two lines"}]))
_reg = json.loads((winroot / "registry.json").read_text())
for _e in _reg:
    if _e["name"] == "win-fwd":
        _e["status"] = "busy"
(winroot / "registry.json").write_text(json.dumps(_reg))
subprocess.run([sys.executable, str(T.SCRIPTS / "cm-hosts.py"), "poll", "win", "--force"], capture_output=True, text=True, env=env(), timeout=120)
_got15 = [t for t in _fw15() if "win:win-fwd" in t]
T.check("RS15 remote-master-handoff: polled again → the first block not sent twice (key on the message uuid, in forwarded-win.json); a new block in English at the next turn → sent too; a bold block written before the channel was on, reaching the copy late → not sent",
        len(_got15) == 2 and sum("renderizza" in t for t in _got15) == 1 and not any("blocco vecchio" in t for t in _fw15()) and any(t.endswith("For the master: second task\nin two lines") for t in _got15)
        and "a1:0:2" in (tmp / "state" / "hosts" / "forwarded-win.json").read_text(), json.dumps(_got15)[-600:])
# RS14 remote-session-report (10/10, dal telefono: «le sessioni win non ricevono allegati?», «no session win:chat»): un
# report verso una sessione su win scrive gli allegati la', nella sua cartella, e le manda un messaggio solo con talk
RPROBE = """
import importlib.util, json, sys, base64
def L(n):
    s = importlib.util.spec_from_file_location(n.replace('-', '_'), sys.argv[1] + '/' + n + '.py'); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
hm, rl = L('cm-hosts'), L('cm-relay')
row = next(r for r in hm.remote_session_rows() if r['remote_name'] == 'win-chat')
rl._json_cmd = lambda *a, **k: [dict(row, tmux=row['name'])]
talks = []
rl.run_cm = lambda *a, **k: (talks.append(list(a)), (0, 'sent'))[1]
d = rl.local_dir('share')
def put(sid, mime, data, name=None):
    (d / (sid + '.json')).write_text(json.dumps(dict({'mime': mime}, **({'name': name} if name else {}))))
    (d / (sid + '.bin')).write_bytes(data)
put('s1', 'image/png', b'PNG-one')
put('s2', 'application/pdf', b'%PDF-x', 'offerta.pdf')
put('s3', 'image/png', b'PNG-two')
out = {'one': rl.share_report({'arg': 's1', 'text': 'il menu', '_local': True, 'device': 'phone'}, row['name'], row['name']),
       'two': rl.share_report({'arg': 's2,s3', 'text': 'offerta e schermo', '_local': True, 'device': 'phone'}, row['name'], row['name']),
       'talks': talks, 'left': sorted(x.name for x in d.iterdir())}
print(json.dumps(out))
"""
cfg.write_text(json.dumps(dict(base_cfg, relay={"enabled": True, "dir": str(tmp / "relay")})))
p = subprocess.run([sys.executable, "-c", RPROBE, str(T.SCRIPTS)], capture_output=True, text=True, env=env(), timeout=120)
cfg.write_text(json.dumps(base_cfg))
rr = json.loads(p.stdout) if p.returncode == 0 else {"error": p.stderr[-800:]}
_chat = winroot / "cm" / "personali" / "chat"
_seg = sorted((_chat / "docs").rglob("*il-menu*.png"))
_inb = sorted((_chat / ".claude-master-inbox").glob("*-offerta.pdf")) if (_chat / ".claude-master-inbox").is_dir() else []
_tk = rr.get("talks") or []
T.check("RS14 remote-session-report: a report with an image to win:chat → the image written there in its folder, ok with its path there (not «no session»)",
        (rr.get("one") or [False])[0] is True and len(_seg) == 1 and _seg[0].read_bytes() == b"PNG-one" and "il-menu" in str((rr.get("one") or ["", ""])[1]), json.dumps(rr)[:800])
T.check("RS14 remote-session-report: two attachments → the PDF in the inbox there (with its .gitignore), the image in the report folder; ONE talk to win:chat per report, with the paths there; the local copies gone",
        (rr.get("two") or [False])[0] is True and len(_inb) == 1 and _inb[0].read_bytes() == b"%PDF-x" and (_chat / ".claude-master-inbox" / ".gitignore").is_file()
        and len(_tk) == 2 and all(t[0] == "talk" and t[1] == "win:chat" for t in _tk) and "W:\\cm\\personali\\chat\\.claude-master-inbox\\" in _tk[1][2]
        and _tk[1][2].index("offerta.pdf") < _tk[1][2].index(".png") and not rr.get("left"), json.dumps(rr)[:800])

T.rm(tmp)
T.finish()
