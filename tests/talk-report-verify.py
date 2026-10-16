#!/usr/bin/env python3
"""Verifica cm-talk.py (socket e tmux), wait e cm-report.sh.

T1  socket: il messaggio arriva nel formato catturato (auth con il peerToken del destinatario + riga user) — E6a/E6b
T2  socket: la risposta si legge dal transcript (blocchi text delle righe assistant) fino all'idle
T3  socket: --no-wait consegna e torna subito; sessione inesistente → exit 3; (questa) → exit 2
T4  tmux: testo digitato nella casella → rifiuto (exit 4); FORZA=si / --force lo butta via (T16)
T5  tmux: il filtro dei suggerimenti (SGR 2, non chiuso, U+00A0) — funzione typed_text su schermate sintetiche
T6  tmux: consegna via Escape / send-keys -l / Enter e lettura del diff dello schermo (T17)
T7  wait: torna quando il registro dice idle e stampa l'ultima risposta; T7b con un goal aperto nel transcript lo dice
R1  report: progetto per nome esatto, prefisso, sottostringa; immagine copiata in <subdir>/<data>-<slug>.<ext> con chmod 644
R2  report: consegna alla sessione via talk --no-wait; --no-launch archivia e basta se la sessione manca
R3  report: sessione assente → la lancia (claude finto) e consegna
I1  interrupt: un solo Esc e solo a turno in corso («esc to interrupt»); niente turno → exit 1; sessione assente → 3
R4  report: cartella come percorso assoluto; --session consegna a quella sessione, exit 6 se non c'e'
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude/sessions", "ws/personali/shopacme", "ws/personali/shopfront", "ws/personali/blog"):
    (home / d).mkdir(parents=True)
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws"), "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "session": {"dialog_patterns": ["trust (this|the) folder", "Is this a project you", "Bypass Permissions mode"], "claude_args": ["--dangerously-skip-permissions"], "startup_timeout_s": 20, "death_check_s": 1},
    "terminal": {"backend": "none"},
    "registry": {"file": str(tmp / "registry.json")},
    "talk": {"quiet_s": 3, "max_wait_s": 20},
    "tabs": {"color_registry": str(tmp / "colors")},
}))
(tmp / "state").mkdir()
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


def proc_start(pid):
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat[stat.rindex(")") + 2:].split()[19]


def env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
         "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(FAKE), "CM_PROC_SCAN_PIDS": ""}
    e.update(extra)
    return e


def talk(*args, **extra):
    return subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "talk"] + list(args), capture_output=True, text=True, env=env(**extra), timeout=120)


# --- finto destinatario su socket: registra le righe, scrive una risposta nel transcript, cambia stato
class FakePeer:
    def __init__(self, name, cwd):
        self.name, self.cwd = name, cwd
        # un processo vivo che NON sia un antenato del test (altrimenti il canale sarebbe «(questa)»)
        self.proc = subprocess.Popen(["sleep", "600"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.2)
        self.pid = self.proc.pid
        self.sock_path = str(tmp / f"{name}.sock")
        self.sid = f"sid-{name}"
        reg = home / ".claude" / "sessions"
        self.reg_json = reg / f"{self.pid}.json"
        self.reg_json.write_text(json.dumps({"pid": self.pid, "name": name, "cwd": cwd, "status": "idle", "tmux": "blog:@0.%0",
                                             "startedAt": int(time.time() * 1000), "procStart": proc_start(self.pid),
                                             "sessionId": self.sid, "messagingSocketPath": self.sock_path}))
        (reg / f"{self.pid}.abc.key").write_text(json.dumps({"peerToken": "tok-" + name, "procStart": proc_start(self.pid)}))
        import re
        slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd))
        self.transcript = home / ".claude" / "projects" / slug / f"{self.sid}.jsonl"
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        self.transcript.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "vecchia risposta"}]}}) + "\n")
        self.received = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.sock_path)
        self.srv.listen(4)
        threading.Thread(target=self.serve, daemon=True).start()

    def set_status(self, st):
        d = json.loads(self.reg_json.read_text()); d["status"] = st; self.reg_json.write_text(json.dumps(d))

    def serve(self):
        while True:
            c, _ = self.srv.accept()
            data = b""
            c.settimeout(2)
            try:
                while not data.endswith(b"\n") or data.count(b"\n") < 2:
                    chunk = c.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            except OSError:
                pass
            c.close()
            self.received.append(data.decode())
            # simula: busy, poi risposta nel transcript, poi idle
            self.set_status("busy")
            time.sleep(1)
            with open(self.transcript, "a") as f:
                f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm"}]}}) + "\n")
                f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "pong dal peer finto"}]}}) + "\n")
            time.sleep(1)
            self.set_status("idle")


with T.PrivateTmux() as tm:
    # T66: il server tmux eredita l'ambiente del PRIMO comando: va avviato con la HOME finta,
    # altrimenti il claude finto lanciato dentro registra nel registro VERO di questa macchina
    subprocess.run(["tmux", "-L", tm.socket, "new-session", "-d", "-s", "blog", "bash", "--norc"], env=env(), check=True)
    peer = FakePeer("blog", str(home / "ws" / "personali" / "blog"))
    # T1/T2
    r = talk("blog", "ping E6", CLAUDE_CODE_MESSAGING_SOCKET="/tmp/me.sock")
    T.check("T1 talk exit 0 via socket", r.returncode == 0 and "via socket" in r.stderr, r.stderr)
    lines = peer.received[-1].split("\n") if peer.received else []
    auth = json.loads(lines[0]) if lines else {}
    msg = json.loads(lines[1]) if len(lines) > 1 else {}
    T.check("T1 auth line carries the recipient's peerToken", auth == {"type": "auth", "token": "tok-blog"}, str(auth))
    T.check("T1 message line in the captured format", msg.get("msgV") == 1 and msg.get("type") == "user" and msg.get("priority") == "next"
            and msg.get("from") == "uds:/tmp/me.sock" and 'from-mode="bypass"' in msg["message"]["content"] and "ping E6" in msg["message"]["content"], str(msg)[:300])
    T.check("T2 reply read from the transcript (text blocks only)", "pong dal peer finto" in r.stdout and "hmm" not in r.stdout and "vecchia" not in r.stdout, r.stdout)
    # T3
    r = talk("blog", "fire and forget", "--no-wait")
    T.check("T3 --no-wait returns at once", r.returncode == 0 and "pong" not in r.stdout, r.stdout + r.stderr)
    r = talk("nessuna", "x")
    T.check("T3 missing session → exit 3 with the list", r.returncode == 3 and "blog" in r.stderr, r.stderr)

    # T5: filtro dei suggerimenti
    mod_src = f'''
import importlib.util, sys
spec = importlib.util.spec_from_file_location("t", "{T.SCRIPTS}/cm-talk.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import re
def filt(line):
    line = re.sub(r"\\x1b\\[2m.*?\\x1b\\[0m", "", line); line = re.sub(r"\\x1b\\[2m.*$", "", line)
    line = re.sub(r"\\x1b\\[[0-9;]*[A-Za-z]", "", line); line = re.sub(r"^\\s*\\u276f[\\s\\u00a0]*", "", line); return re.sub(r"[\\s\\u00a0]*$", "", line)
print(repr(filt("\\x1b[38;5;246m❯\\xa0 \\x1b[39m")))                     # vuota con U+00A0
print(repr(filt("❯ ciao \\x1b[2msuggerimento\\x1b[0m")))                 # digitato + suggerimento chiuso
print(repr(filt("❯ \\x1b[2msolo suggerimento fino a fine riga")))        # non chiuso
'''
    r = subprocess.run([sys.executable, "-c", mod_src], capture_output=True, text=True, env=env())
    out = r.stdout.strip().split("\n")
    T.check("T5 empty box with U+00A0 → empty", out[0] == "''", r.stdout + r.stderr)
    T.check("T5 typed + closed suggestion → typed only", out[1] == "'ciao'", r.stdout)
    T.check("T5 unterminated suggestion → empty", out[2] == "''", r.stdout)

    # T4/T6: sessione tmux con il claude finto (nessun socket nel registro → via tmux)
    tm("new-session", "-d", "-s", "shopacme", "-x", "120", "-y", "30", "-c", str(home / "ws" / "personali" / "shopacme"),
       f"env CLAUDE_CONFIG_DIR='{home}/.claude' FAKE_CLAUDE_REGISTER=0 '{FAKE}' -n shopacme")
    time.sleep(2)
    # il registro peer del finto e' spento: cm-sessions lo vede da /proc (cmdline con 'claude'?) — no: nome del fake e' fake-claude.sh.
    # Si registra a mano SENZA socket, con il nome tmux giusto.
    pane_pid = int(tm("list-panes", "-t", "shopacme", "-F", "#{pane_pid}").stdout.strip())
    (home / ".claude" / "sessions" / f"{pane_pid}.json").write_text(json.dumps({"pid": pane_pid, "name": "shopacme", "cwd": str(home / "ws" / "personali" / "shopacme"),
        "status": "idle", "tmux": "shopacme:@0.%0", "startedAt": int(time.time() * 1000), "procStart": proc_start(pane_pid), "sessionId": "sid-joy"}))
    tm("send-keys", "-t", "shopacme", "-l", "testo lasciato a meta")
    time.sleep(0.5)
    r = talk("shopacme", "ciao")
    T.check("T4 typed text in the box → refused (exit 4)", r.returncode == 4 and "testo lasciato a meta" in r.stderr, r.stderr + r.stdout)
    r = talk("shopacme", "ciao via tmux", "--force", "--quiet", "3", "--wait", "15")
    # il claude finto non svuota la casella con Esc (lo fa Claude Code, T17): il residuo resta attaccato all'eco
    T.check("T6 forced: delivered via tmux and the echo comes back from the screen", r.returncode == 0 and "via tmux" in r.stderr and "ciao via tmux" in r.stdout, r.stdout + r.stderr)

    # T7 wait
    peer.set_status("busy")
    threading.Timer(2, lambda: peer.set_status("idle")).start()
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "wait", "blog", "--timeout", "20"], capture_output=True, text=True, env=env(), timeout=60)
    T.check("T7 wait returns on idle and prints the last reply", r.returncode == 0 and "idle" in r.stdout and "pong dal peer finto" in r.stdout, r.stdout + r.stderr)
    # T7b (25/09/2026): con un goal nativo ancora aperto nel transcript, wait lo dice (idle = «Goal paused», non finita)
    with open(peer.transcript, "a") as f:
        f.write(json.dumps({"type": "attachment", "attachment": {"type": "goal_status", "met": False, "sentinel": False, "iterations": 4, "condition": "la suite passa"}}) + "\n")
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "wait", "blog", "--timeout", "20"], capture_output=True, text=True, env=env(), timeout=60)
    T.check("T7b wait with an open goal → «goal ancora aperto: «…» (4 controlli finora)»", r.returncode == 0 and "goal ancora aperto: «la suite passa» (4 controlli finora)" in r.stdout, r.stdout + r.stderr)
    with open(peer.transcript, "a") as f:
        f.write(json.dumps({"type": "attachment", "attachment": {"type": "goal_status", "met": True, "sentinel": False, "iterations": 5, "condition": "la suite passa"}}) + "\n")
    r = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "wait", "blog", "--timeout", "20"], capture_output=True, text=True, env=env(), timeout=60)
    T.check("T7b goal met → no goal line", r.returncode == 0 and "goal ancora aperto" not in r.stdout, r.stdout + r.stderr)

    # R1/R2 report
    img = tmp / "shot.PNG"
    img.write_bytes(b"\x89PNG fake")
    r = subprocess.run([str(T.SCRIPTS / "cm-report.sh"), "blog", str(img), "Il bottone compra non funziona!"], capture_output=True, text=True, env=env(), timeout=120)
    dest = home / "ws" / "personali" / "blog" / "docs" / "segnalazioni"
    files = list(dest.glob("*")) if dest.exists() else []
    T.check("R1 image archived as <date>-<slug>.png with 644", r.returncode == 0 and len(files) == 1 and files[0].name.endswith("-il-bottone-compra-non-funziona.png")
            and oct(files[0].stat().st_mode)[-3:] == "644", r.stdout + r.stderr + str(files))
    T.check("R2 delivered to the project session via socket with the image path", "consegnata" in r.stdout and any(str(files[0]) in x for x in peer.received[-1:]) if files else False, r.stdout + (peer.received[-1] if peer.received else ""))
    # 16/10/2026: «più» at the 40th character — the slug is cut by characters, the name stays UTF-8, the line goes out
    r = subprocess.run([str(T.SCRIPTS / "cm-report.sh"), "blog", str(img), "I blocchi che contengono testo un po' più esteso"], capture_output=True, text=True, env=env(), timeout=120)
    names = [f.name for f in dest.glob("*-i-blocchi-*")]
    T.check("R1b slug cut by characters: «…-un-po-più.png», a valid UTF-8 name", r.returncode == 0 and len(names) == 1 and names[0].endswith("-i-blocchi-che-contengono-testo-un-po-più.png"), r.stdout + r.stderr + str(names))
    want = f"Screenshot allegato, leggilo: {dest / names[0]}" if names else "?"
    for _ in range(50):
        got = [x for x in peer.received if want in x or want in x.encode().decode("unicode_escape", "ignore").encode("latin-1", "ignore").decode("utf-8", "ignore")]
        if got:
            break
        time.sleep(0.1)
    T.check("R2b the delivered message carries «Screenshot allegato, leggilo: <path>»", bool(got), str(peer.received[-2:]))
    m = subprocess.run(["python3", str(T.SCRIPTS / "cm-config.py"), "--msg", "report.prompt_image", b"path=/x/pi\xc3.jpg"], capture_output=True, env=env(), timeout=30)
    T.check("R2b --msg with a non-UTF-8 path: exit 0, the bytes kept", m.returncode == 0 and m.stdout.strip().endswith(b"/x/pi\xc3.jpg"), repr(m.stdout) + repr(m.stderr))
    r = subprocess.run([str(T.SCRIPTS / "cm-report.sh"), "shopf", "-", "solo testo", "--no-launch"], capture_output=True, text=True, env=env(), timeout=60)
    T.check("R1 prefix match shopf → shopfront; --no-launch with no session → archived only", r.returncode == 0 and "shopfront" in r.stdout and "non" in r.stdout.lower(), r.stdout + r.stderr)
    r = subprocess.run([str(T.SCRIPTS / "cm-report.sh"), "nessuno", "-", "x"], capture_output=True, text=True, env=env(), timeout=60)
    T.check("R1 unknown project → exit 3", r.returncode == 3, r.stdout + r.stderr)
    # R3: sessione assente → lancia (claude finto, --no-window) e consegna via tmux
    scen = tmp / "scen"; scen.write_text("plain")
    r = subprocess.run([str(T.SCRIPTS / "cm-report.sh"), "shopfront", "-", "lancia e consegna"], capture_output=True, text=True,
                       env=env(FAKE_CLAUDE_SCENARIO_FILE=str(scen), WAYLAND_DISPLAY=""), timeout=180)
    T.check("R3 missing session launched and text delivered", r.returncode == 0 and tm("has-session", "-t", "=shopfront").returncode == 0 and "consegnata" in r.stdout, r.stdout + r.stderr)
    screen = tm("capture-pane", "-p", "-t", "shopfront").stdout
    T.check("R3 the fake session shows the delivered text", "lancia e consegna" in screen, screen[-400:])
    # I1 (30/09, il tasto Stop): un solo Esc e solo a turno in corso. La sessione finta mostra «esc to interrupt»,
    # legge un tasto (l'Esc) e passa a «idle»; un secondo Esc scriverebbe SECOND
    tm("new-session", "-d", "-s", "busyone", "-x", "120", "-y", "20",
       "bash -c 'printf \"* Working… (12s · esc to interrupt)\\n\"; read -rsn1; clear; echo idle; read -rsn1; echo SECOND; sleep 300'")
    time.sleep(0.8)
    ri = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "interrupt", "busyone"], capture_output=True, text=True, env=env(), timeout=60)
    time.sleep(0.8)
    scr_i = tm("capture-pane", "-p", "-t", "busyone").stdout
    T.check("I1 interrupt during a turn → one Esc, exit 0 «busyone: fermata»; the pane went idle and got no second Esc",
            ri.returncode == 0 and "fermata" in ri.stdout and "idle" in scr_i and "SECOND" not in scr_i, ri.stdout + ri.stderr + scr_i[-200:])
    ri = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "interrupt", "busyone"], capture_output=True, text=True, env=env(), timeout=60)
    time.sleep(0.5)
    T.check("I1 no turn running → exit 1 «niente da fermare», no key sent (no rewind on an idle prompt)",
            ri.returncode == 1 and "niente da fermare" in ri.stderr and "SECOND" not in tm("capture-pane", "-p", "-t", "busyone").stdout, ri.stderr)
    ri = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "interrupt", "nessuna-tale"], capture_output=True, text=True, env=env(), timeout=60)
    T.check("I1 a session that does not exist → exit 3", ri.returncode == 3, ri.stderr)
    tm("kill-session", "-t", "=busyone")
    # I1b (08/10, dal telefono: «nothing to stop» a turno in corso): Claude Code 2.1.294 non scrive piu' «esc to interrupt»
    tm("new-session", "-d", "-s", "busynew", "-x", "120", "-y", "20",
       "bash -c 'printf \"● Slithering… (1m 9s · ↓ 2.7k tokens)\\n────\\n❯\\n────\\n⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent\\n\"; read -rsn1; clear; echo idle; sleep 300'")
    time.sleep(0.8)
    ri = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "interrupt", "busynew"], capture_output=True, text=True, env=env(), timeout=60)
    time.sleep(0.8)
    T.check("I1b the 2.1.294 status line («● Slithering… (1m 9s · ↓ 2.7k tokens)», no «esc to interrupt») is a turn running → one Esc, exit 0",
            ri.returncode == 0 and "fermata" in ri.stdout and "idle" in tm("capture-pane", "-p", "-t", "busynew").stdout, ri.stdout + ri.stderr)
    tm("kill-session", "-t", "=busynew")
    # I1c (16/10, dal telefono: Stop subito dopo un invio → «nothing to stop»): il turno parte un attimo dopo
    tm("new-session", "-d", "-s", "lateone", "-x", "120", "-y", "20",
       "bash -c 'echo idle; sleep 1.5; clear; printf \"* Working… (1s · ↓ 10 tokens)\\n\"; read -rsn1; clear; echo stopped; sleep 300'")
    time.sleep(0.5)
    rn = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "interrupt", "lateone"], capture_output=True, text=True, env=env(), timeout=60)
    ri = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-talk.py"), "interrupt", "lateone", "--wait", "6"], capture_output=True, text=True, env=env(), timeout=60)
    time.sleep(0.8)
    T.check("I1c a turn not started yet: without --wait → exit 1 and no Esc; with --wait 6 it waits for the status line, then one Esc, exit 0",
            rn.returncode == 1 and ri.returncode == 0 and "fermata" in ri.stdout and "stopped" in tm("capture-pane", "-p", "-t", "lateone").stdout, rn.stderr + ri.stdout + ri.stderr)
    tm("kill-session", "-t", "=lateone")
    # R4 (contratto 1.19 del relay, «Condividi» dal telefono): la cartella come percorso assoluto e la sessione per nome
    shop = home / "ws" / "personali" / "shopfront"
    r = subprocess.run([str(T.SCRIPTS / "cm-report.sh"), str(shop), "-", "nessuna sessione", "--session", "inesistente"], capture_output=True, text=True, env=env(), timeout=60)
    T.check("R4 --session of a session that does not exist → exit 6, nothing launched", r.returncode == 6 and "inesistente" in r.stderr and tm("has-session", "-t", "=inesistente").returncode != 0, r.stdout + r.stderr)
    r = subprocess.run([str(T.SCRIPTS / "cm-report.sh"), str(shop), "-", "per percorso e nome", "--session", "shopfront"], capture_output=True, text=True, env=env(), timeout=120)
    time.sleep(1)
    T.check("R4 absolute folder + --session → delivered to that session", r.returncode == 0 and "consegnata" in r.stdout and "per percorso e nome" in tm("capture-pane", "-p", "-t", "shopfront").stdout, r.stdout + r.stderr)

peer.proc.kill()
T.rm(str(tmp))
T.finish()
