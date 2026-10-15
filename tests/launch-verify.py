#!/usr/bin/env python3
"""Verifica cm-launch.sh con il claude finto, un tmux privato e il backend terminale `fake`.

L14 tetto sessions.max_sessions: 5 al lavoro → exit 7 senza terminale, --force procede; la master non passa dal tetto
L1  percorso relativo → exit 2; cartella inesistente → exit 4; --create la crea
L2  argv: claude_args da config, --remote-control NOME -n NOME, account dedotto da folder_map (CLAUDE_CONFIG_DIR)
L3  nome: radice → root_session_name; `sito.com` → `sito-com` (T2); seconda sessione → -2; prefisso dell'account
L4  account forzato contro il percorso → avviso e procede (T49); --aziendale → secondo account con avviso
L5  --continue → -c; --resume con id inesistente → exit 4 con l'elenco (T10); con id vero → --resume ID
L6  dialogo trust risposto (Down+Enter, T3); trust in ritardo (T4); trust + bypass in sequenza (T61)
L6b dialog_patterns vuoto (il default dal 07/10) e schermata di accesso: nessun tasto premuto, launch smette di aspettare
    e dice che la sessione aspetta la persona (prova d'installazione da zero, 13/10)
L7  schermo lento (T5): avvia lo stesso con nota; sessione morta subito → exit 5
L8  finestra: backend fake attacca → «attaccata»; --no-window → nessuna apertura; T8 secondo tentativo
L15 accounts.<nome>.remote_control prevale su session.remote_control (anche al contrario)
L9  --bg: `--bg -n NOME`, niente --remote-control, niente tmux (T47)
L9b (16/10/2026) un nome portato da una sessione VIVA del registro (una --bg, senza tmux) e' preso: la nuova prende -2,
    anche in tmux; la voce di un processo morto non lo tiene, ne' quella di uno zombie (il rilancio di restart)
L10 profilo: argomenti, modello, env nel processo (N8); profilo ignoto → exit 2
L11 registro aggiornato al lancio (T52) e link dal registro peer (1.2)
L12 PATH del cron (T81, 16:30 dell'11/09: «claude non trovato nel PATH» dal bot): con PATH=/usr/bin:/bin e senza
    CM_CLAUDE_BIN, launch trova ~/.local/bin/claude (cm-lib antepone ~/.local/bin); senza nessun claude, exit 3
    con il messaggio che dice i percorsi provati
L13 display (14/09): sotto tmux senza DISPLAY/WAYLAND_DISPLAY/XDG_RUNTIME_DIR e con wayland-0 vivo → la finestra
    si apre (backend gnome a secco); senza socket → salta (T57); fuori tmux (cron, daemon) anche col socket → salta
L14 (S09, prova) --agent codex: spento (experimental.codex false) → exit 2; acceso → Codex CLI in tmux col nome della
    cartella, senza gli argomenti di Claude; agente sconosciuto → exit 2
L14b il dialogo di fiducia di Codex disegnato 4 s dopo la prima riga viene risposto; pronta solo sul segno di Codex pronto
"""
import json
import re
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
for d in (".claude/sessions", ".claude-pixel/sessions", "ws/personali/alfa", "ws/personali/sito.com", "ws/pro/beta"):
    (home / d).mkdir(parents=True)
reg = tmp / "registry.json"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it",
    "state_dir": str(tmp / "state"),
    "workspace": {"root": str(home / "ws"), "root_session_name": "master"},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "default_account": "personale",
    "folder_map": [{"path": str(home / "ws" / "pro"), "account": "professionale"},
                   {"path": str(home / "ws" / "personali"), "account": "personale"}],
    "session": {"dialog_patterns": ["trust (this|the) folder", "Is this a project you", "Bypass Permissions mode"], "claude_args": ["--dangerously-skip-permissions"], "startup_timeout_s": 25, "death_check_s": 1},
    "terminal": {"backend": "fake", "attach_wait_s": 6, "attach_retry_wait_s": 4},
    "registry": {"file": str(reg)},
    "profiles": {"scan": {"args": ["--permission-prompts", "none"], "model": "sonnet", "effort": "low",
                          "env": {"CLAUDE_CODE_TOOL_MEMORY_LIMIT": "2g"}, "window": False}},
    "tabs": {"color_registry": str(tmp / "colors")},
    "sessions": {"max_sessions": 50},   # il tetto si prova a parte (L14) con config-cap.json: qui le sessioni finte si accumulano
}))
cfg_cap = tmp / "config-cap.json"
cfg_cap.write_text(json.dumps(dict(json.loads(cfg.read_text()), sessions={"max_sessions": 5})))
argslog = tmp / "args.log"
fakelog = tmp / "fake-terminal.log"
FAKE = T.ROOT / "tests" / "lib" / "fake-claude.sh"


scenfile = tmp / "scenario"


def env_base(scenario="plain"):
    scenfile.write_text(scenario)
    return {"FAKE_CLAUDE_SCENARIO_FILE": str(scenfile),"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
           "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_CLAUDE_BIN": str(FAKE), "FAKE_CLAUDE_SCENARIO": scenario,
           "FAKE_CLAUDE_ARGS_LOG": str(argslog), "CM_TERMINAL_FAKE_LOG": str(fakelog), "CM_TERMINAL_FAKE_ATTACH": "1",
           "WAYLAND_DISPLAY": "fake-0", "FAKE_CLAUDE_ECHO_ENV": "CLAUDE_CODE_TOOL_MEMORY_LIMIT", "FAKE_CLAUDE_DELAY": "3"}


def run(*args, scenario="plain", extra=None, timeout=90):
    env = env_base(scenario)
    env.pop("TMUX", None)
    if extra:
        env.update(extra)
    return subprocess.run([str(T.SCRIPTS / "cm-launch.sh")] + list(args), capture_output=True, text=True, env=env, timeout=timeout)


def last_args():
    return argslog.read_text().strip().split("\n")[-1] if argslog.exists() else ""


with T.PrivateTmux() as tm:
    r = run("relativa")
    T.check("L1 relative path → exit 2", r.returncode == 2, r.stderr)
    r = run(str(home / "ws" / "nope"))
    T.check("L1 missing dir → exit 4", r.returncode == 4 and "--create" in r.stderr, r.stderr)
    r = run(str(home / "ws" / "personali" / "nuova"), "--create", "--no-window")
    T.check("L1 --create makes the dir and launches", r.returncode == 0 and (home / "ws" / "personali" / "nuova").is_dir(), r.stdout + r.stderr)
    pane_env = subprocess.run(["bash", "-c", f"tr '\\0' '\\n' < /proc/$(tmux -L {tm.socket} list-panes -t nuova -F '#{{pane_pid}}')/environ"],
                              capture_output=True, text=True).stdout
    T.check("T68 default-dir account → CLAUDE_CONFIG_DIR NOT set in the session", "CLAUDE_CONFIG_DIR=" not in pane_env, pane_env[:300])
    T.check("L2 argv has claude_args, --remote-control NAME and -n NAME",
            "--dangerously-skip-permissions" in last_args() and "--remote-control nuova" in last_args() and "-n nuova" in last_args(), last_args())
    T.check("L8 --no-window → no terminal open", not fakelog.exists() or "nuova" not in fakelog.read_text(), fakelog.read_text() if fakelog.exists() else "")
    T.check("L11 registry written with the session", reg.exists() and '"nuova"' in reg.read_text(), reg.read_text() if reg.exists() else "missing")
    T.check("L11 link from the peer registry", "link:      https://claude.ai/code/session_01FAKE" in r.stdout, r.stdout)
    # L14 (25/09/2026): tetto sessions.max_sessions — 5 sessioni al lavoro nel registro → senza terminale exit 7 con
    # conteggio e memoria libera; --force procede
    keep = []
    for i in range(5):
        pr = subprocess.Popen(["sleep", "300"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); keep.append(pr); time.sleep(0.1)
        st = Path(f"/proc/{pr.pid}/stat").read_text(); st = st[st.rindex(")") + 2:].split()[19]
        (home / ".claude" / "sessions" / f"{pr.pid}.json").write_text(json.dumps({"pid": pr.pid, "name": f"finta{i}", "cwd": str(home), "status": "idle", "tmux": f"finta{i}:@0.%0",
                                                                                  "startedAt": int(time.time() * 1000), "procStart": st, "sessionId": f"sid-finta{i}"}))
    r = run(str(home / "ws" / "personali" / "sesta"), "--no-window", "--create", extra={"CM_LAUNCH_NO_TTY": "1", "CC_SUPERVISOR_CONFIG": str(cfg_cap)})
    T.check("L14 5 sessions at work, no terminal → exit 7, the count, the cap and the free memory in the message, nothing launched",
            r.returncode == 7 and (lambda m: bool(m) and int(m.group(1)) >= 5)(re.search(r"(\d+) sessioni già al lavoro", r.stderr)) and "tetto sessions.max_sessions = 5" in r.stderr and "memoria libera" in r.stderr and "--force" in r.stderr
            and not tm("has-session", "-t", "=sesta").returncode == 0, r.stdout + r.stderr)
    r = run(str(home / "ws"), "--no-window", extra={"CM_LAUNCH_NO_TTY": "1", "CC_SUPERVISOR_CONFIG": str(cfg_cap)})
    T.check("L14 the master (workspace root) is not held by the cap, even with 5 at work and no terminal (the app's Reopen)",
            r.returncode == 0 and "sessioni già al lavoro" not in r.stderr, r.stdout + r.stderr)
    r = run(str(home / "ws" / "personali" / "sesta"), "--no-window", "--create", "--force", extra={"CM_LAUNCH_NO_TTY": "1", "CC_SUPERVISOR_CONFIG": str(cfg_cap)})
    T.check("L14 --force → launched", r.returncode == 0 and tm("has-session", "-t", "=sesta").returncode == 0, r.stdout + r.stderr)
    for pr in keep:
        pr.kill(); (home / ".claude" / "sessions" / f"{pr.pid}.json").unlink(missing_ok=True)
    r = run(str(home / "ws" / "personali" / "tardi"), "--no-window", "--create", scenario="bridge-late", extra={"FAKE_CLAUDE_DELAY": "3"})
    T.check("L11b bridgeSessionId arriving late (3 s) is waited for: link printed, no «not yet» line", r.returncode == 0 and "link:      https://claude.ai/code/session_01FAKE" in r.stdout and "telefono" not in r.stdout, r.stdout + r.stderr)

    r = run(str(home / "ws" / "pro" / "beta"), "--no-window")
    T.check("L2 account deduced from folder_map → professionale", r.returncode == 0 and "professionale" in r.stdout and "dedotto" in r.stdout, r.stdout + r.stderr)
    T.check("L3 tmux name carries the account prefix", tm("has-session", "-t", "=pix-beta").returncode == 0, tm("list-sessions").stdout)
    T.check("T68 second account → CLAUDE_CONFIG_DIR passed", "ENV CLAUDE_CONFIG_DIR=" + str(home / ".claude-pixel") in
            subprocess.run(["bash", "-c", f"tr '\\0' '\\n' < /proc/$(tmux -L {tm.socket} list-panes -t pix-beta -F '#{{pane_pid}}')/environ | grep -E '^CLAUDE_CONFIG_DIR=' | sed 's/^/ENV /'"],
                           capture_output=True, text=True).stdout, "environ check")
    reg_pro = list((home / ".claude-pixel" / "sessions").glob("*.json"))
    T.check("L2 launched with the account's CLAUDE_CONFIG_DIR (registered there)", len(reg_pro) == 1, str(reg_pro))

    r = run(str(home / "ws" / "personali" / "sito.com"), "--no-window")
    T.check("L3 dots sanitized: sito.com → sito-com", tm("has-session", "-t", "=sito-com").returncode == 0, tm("list-sessions").stdout)
    r = run(str(home / "ws" / "personali" / "sito.com"), "--no-window")
    T.check("L3 second session on the same folder → -2", tm("has-session", "-t", "=sito-com-2").returncode == 0, tm("list-sessions").stdout)
    r = run(str(home / "ws"), "--no-window")
    T.check("L3 workspace root → master", tm("has-session", "-t", "=master").returncode == 0, tm("list-sessions").stdout)

    r = run(str(home / "ws" / "personali" / "alfa"), "--professionale", "--no-window")
    T.check("L4 forced account against the path → warning, proceeds", r.returncode == 0 and "attenzione" in r.stderr.lower() and tm("has-session", "-t", "=pix-alfa").returncode == 0, r.stderr + r.stdout)
    r = run(str(home / "ws" / "personali" / "alfa"), "--aziendale", "--no-window")
    T.check("L4 --aziendale → second account with legacy-flag warning", r.returncode == 0 and "aziendale" in r.stderr and tm("has-session", "-t", "=pix-alfa-2").returncode == 0, r.stderr)

    r = run(str(home / "ws" / "personali" / "alfa"), "--continue", "--no-window")
    T.check("L5 --continue → -c", r.returncode == 0 and " -c " in f" {last_args()} ", last_args())
    r = run(str(home / "ws" / "personali" / "alfa"), "--resume", "deadbeef", "--no-window")
    T.check("L5 --resume with unknown id → exit 4", r.returncode == 4 and "deadbeef" in r.stderr, r.stderr)
    slug = str((home / "ws" / "personali" / "alfa").resolve())
    import re
    slug = re.sub(r"[^A-Za-z0-9]", "-", slug)
    conv = home / ".claude" / "projects" / slug
    conv.mkdir(parents=True)
    (conv / "abc123.jsonl").write_text("{}\n")
    r = run(str(home / "ws" / "personali" / "alfa"), "--resume", "abc123", "--no-window")
    T.check("L5 --resume with existing id → --resume abc123", r.returncode == 0 and "--resume abc123" in last_args(), last_args() + r.stderr)
    r = run(str(home / "ws" / "personali" / "alfa"), "--continue", "--resume", "abc123", "--no-window")
    T.check("L5 --continue and --resume together → exit 2", r.returncode == 2, r.stderr)

    r = run(str(home / "ws" / "personali" / "alfa"), "--no-window", scenario="trust")
    T.check("L6 trust dialog answered (session alive, registered)", r.returncode == 0 and "link:" in r.stdout, r.stdout + r.stderr)
    r = run(str(home / "ws" / "personali" / "alfa"), "--no-window", scenario="trust-late")
    T.check("L6 late trust dialog answered (T4)", r.returncode == 0 and "link:" in r.stdout, r.stdout + r.stderr)
    r = run(str(home / "ws" / "personali" / "alfa"), "--no-window", scenario="trust,bypass")
    T.check("L6 trust then bypass dialogs both answered (T61)", r.returncode == 0 and "link:" in r.stdout, r.stdout + r.stderr)

    r = run(str(home / "ws" / "personali" / "alfa"), "--no-window", scenario="slow")
    T.check("L7 slow screen: launched anyway with a note or registered", r.returncode == 0, r.stdout + r.stderr)
    r = run(str(home / "ws" / "personali" / "alfa"), "--no-window", scenario="die")
    T.check("L7 dies right after start → exit 5", r.returncode == 5, r.stdout + r.stderr)

    r = run(str(home / "ws" / "personali" / "alfa"))
    alog = fakelog.with_name(fakelog.name + ".attach")
    T.check("L8 fake terminal attaches → reported attached", r.returncode == 0 and "attaccata" in r.stdout,
            r.stdout + r.stderr + "\nATTACH LOG: " + (alog.read_text(errors="replace")[-600:] if alog.exists() else "(none)")
            + "\nSESSIONS: " + tm("list-sessions", "-F", "#{session_name} #{session_attached}").stdout)
    T.check("L8 terminal asked to open NAME ephemeral", "open alfa" in fakelog.read_text() and "ephemeral" in fakelog.read_text(), fakelog.read_text())
    r = run(str(home / "ws" / "personali" / "beta"), "--create", extra={"CM_TERMINAL_FAKE_ATTACH": "0"})
    T.check("L8 no client attaches → two attempts, reported NOT attached", r.returncode == 0 and fakelog.read_text().count("open beta") == 2 and "NON APERTA" in r.stdout, r.stdout + fakelog.read_text())

    r = run(str(home / "ws" / "personali" / "alfa"), "--bg")
    T.check("L9 --bg: argv has --bg and -n, no --remote-control, no tmux session", r.returncode == 0 and "--bg" in last_args() and "--remote-control" not in last_args()
            and "background" in r.stdout.lower(), last_args() + r.stdout)
    # L9b (16/10/2026, dal vivo il 15/10 alle 23:40): una sessione --bg sta solo nel registro di Claude Code, non in tmux;
    # un secondo `launch --bg` nella stessa cartella riprendeva il suo nome (due «pix-claude-plugins», l'app in crash)
    vivo = subprocess.Popen(["sleep", "120"])
    (home / ".claude-pixel" / "sessions" / f"{vivo.pid}.json").write_text(json.dumps(
        {"pid": vivo.pid, "sessionId": "bg-1", "cwd": str(home / "ws" / "pro" / "delta"), "kind": "bg", "name": "pix-delta"}, separators=(",", ":")))
    r = run(str(home / "ws" / "pro" / "delta"), "--bg", "--create")
    T.check("L9b --bg while a live --bg session of the registry (no tmux) holds the name: the new one takes -2",
            r.returncode == 0 and "-n pix-delta-2" in last_args() and "pix-delta-2" in r.stdout, last_args() + r.stdout + r.stderr)
    r = run(str(home / "ws" / "pro" / "delta"), "--no-window")
    T.check("L9b a tmux launch skips the live --bg session's name too", r.returncode == 0 and "-n pix-delta-2" in last_args(), last_args() + r.stdout + r.stderr)
    vivo.kill(); vivo.wait()
    r = run(str(home / "ws" / "pro" / "delta"), "--bg")
    T.check("L9b the registry entry of a dead process does not hold the name", r.returncode == 0 and "-n pix-delta " in last_args() + " ", last_args() + r.stderr)
    # uno zombie (uscito, non ancora raccolto dal padre) non e' vivo: restart rilancia proprio in quell'istante
    zombie = subprocess.Popen(["true"])
    time.sleep(0.5)
    (home / ".claude-pixel" / "sessions" / f"{zombie.pid}.json").write_text(json.dumps(
        {"pid": zombie.pid, "sessionId": "bg-2", "cwd": str(home / "ws" / "pro" / "zeta"), "kind": "interactive", "name": "pix-zeta"}, separators=(",", ":")))
    was_zombie = "Z" in Path(f"/proc/{zombie.pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    r = run(str(home / "ws" / "pro" / "zeta"), "--bg", "--create")
    zombie.wait()
    T.check("L9b the registry entry of a zombie (exited, not yet reaped) does not hold the name", was_zombie and r.returncode == 0 and "-n pix-zeta " in last_args() + " ",
            f"zombie={was_zombie} " + last_args() + r.stderr)

    # L15 (30/09): accounts.<nome>.remote_control prevale su session.remote_control, nei due sensi
    base15 = json.loads(cfg.read_text())
    c15 = dict(base15, accounts={"personale": base15["accounts"]["personale"], "professionale": dict(base15["accounts"]["professionale"], remote_control=False)})
    cfg15 = tmp / "config-rc.json"; cfg15.write_text(json.dumps(c15))
    r = run(str(home / "ws" / "pro" / "rc-off"), "--create", "--no-window", extra={"CC_SUPERVISOR_CONFIG": str(cfg15)})
    a_off = last_args()
    r2 = run(str(home / "ws" / "personali" / "rc-on"), "--create", "--no-window", extra={"CC_SUPERVISOR_CONFIG": str(cfg15)})
    T.check("L15 accounts.professionale.remote_control false → no --remote-control for that account; personale keeps the global true",
            r.returncode == 0 and "--remote-control" not in a_off and "-n pix-rc-off" in a_off and r2.returncode == 0 and "--remote-control rc-on" in last_args(), a_off + " | " + last_args() + r.stderr[-200:])
    c15b = dict(base15, session=dict(base15["session"], remote_control=False),
                accounts={"personale": base15["accounts"]["personale"], "professionale": dict(base15["accounts"]["professionale"], remote_control=True)})
    cfg15.write_text(json.dumps(c15b))
    r = run(str(home / "ws" / "pro" / "rc-on2"), "--create", "--no-window", extra={"CC_SUPERVISOR_CONFIG": str(cfg15)})
    a_on = last_args()
    r2 = run(str(home / "ws" / "personali" / "rc-off2"), "--create", "--no-window", extra={"CC_SUPERVISOR_CONFIG": str(cfg15)})
    T.check("L15 session.remote_control false, accounts.professionale.remote_control true → only professionale gets --remote-control",
            r.returncode == 0 and "--remote-control pix-rc-on2" in a_on and r2.returncode == 0 and "--remote-control" not in last_args(), a_on + " | " + last_args())

    r = run(str(home / "ws" / "personali" / "alfa"), "--profile", "scan")
    T.check("L10 profile args/model/effort in argv, window off", r.returncode == 0 and "--permission-prompts none" in last_args() and "--model sonnet" in last_args()
            and "--effort low" in last_args() and "open alfa" not in fakelog.read_text().split("open beta")[-1], last_args() + r.stdout)
    T.check("L10 profile env reaches the process", "ENV CLAUDE_CODE_TOOL_MEMORY_LIMIT=2g" in last_args(), last_args())
    r = run(str(home / "ws" / "personali" / "alfa"), "--profile", "nope")
    T.check("L10 unknown profile → exit 2", r.returncode == 2 and "scan" in r.stderr, r.stderr)

    # L12: PATH minimale come nel cron, claude solo in ~/.local/bin (link al claude finto)
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    (home / ".local" / "bin" / "claude").symlink_to(FAKE)
    scenfile.write_text("plain")
    e12 = {k: v for k, v in env_base("plain").items() if k != "CM_CLAUDE_BIN"}
    e12["PATH"] = "/usr/bin:/bin"
    r = subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), str(home / "ws" / "personali" / "alfa"), "--no-window"], capture_output=True, text=True, env=e12, timeout=90)
    T.check("L12 cron PATH: claude found in ~/.local/bin, session launched", r.returncode == 0 and "nome tmux" in r.stdout, r.stdout + r.stderr)
    (home / ".local" / "bin" / "claude").unlink()
    r = subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), str(home / "ws" / "personali" / "alfa"), "--no-window"], capture_output=True, text=True, env=e12, timeout=90)
    T.check("L12 no claude anywhere → exit 3, the message names ~/.local/bin/claude", r.returncode == 3 and ".local/bin/claude" in r.stderr, r.stdout + r.stderr)
    # L13: backend gnome (soggetto a T57) a secco: il comando stampato finisce nell'uscita di launch
    import socket
    cfg13 = tmp / "config-l13.json"
    c13 = json.loads(cfg.read_text()); c13["terminal"] = {"backend": "gnome", "attach_wait_s": 1, "attach_retry_wait_s": 1, "headless_skip": True}
    cfg13.write_text(json.dumps(c13))
    rt13, x13 = tmp / "runtime13", tmp / "x11-13"
    rt13.mkdir(); x13.mkdir()

    def run13(folder, under_tmux, *extra):
        (home / "ws" / "personali" / folder).mkdir(exist_ok=True)
        e = {k: v for k, v in env_base("plain").items() if k not in ("WAYLAND_DISPLAY", "CM_TERMINAL_FAKE_ATTACH", "TMUX")}
        e.update(CC_SUPERVISOR_CONFIG=str(cfg13), CM_TERMINAL_DRY_RUN="1", XDG_RUNTIME_DIR=str(rt13), CM_X11_SOCKET_DIR=str(x13))
        if under_tmux:
            e["TMUX"] = "/tmp/tmux-fake/default,1,0"
        return subprocess.run([str(T.SCRIPTS / "cm-launch.sh"), str(home / "ws" / "personali" / folder), *extra], capture_output=True, text=True, env=e, timeout=90)

    r = run13("senzasocket", True)
    T.check("L13 under tmux, no display, no socket → cm-terminal skips (T57)", r.returncode == 0 and "skip: headless" in r.stdout and "gnome-terminal" not in r.stdout, r.stdout + r.stderr)
    srv = socket.socket(socket.AF_UNIX); srv.bind(str(rt13 / "wayland-0"))
    r = run13("consocket", True)
    T.check("L13 under tmux, no display, wayland-0 alive → display recovered, the tab opens", r.returncode == 0 and "gnome-terminal --title consocket" in r.stdout and "skip: headless" not in r.stdout, r.stdout + r.stderr)
    r = run13("dacron", False)
    T.check("L13 outside tmux (cron, daemons) the socket is not enough → still skips (T57)", r.returncode == 0 and "skip: headless" in r.stdout and "gnome-terminal" not in r.stdout, r.stdout + r.stderr)
    r = run13("dacronfinestra", False, "--window")
    T.check("L13 outside tmux with an EXPLICIT --window (reopen from the watch, 15/09) and wayland-0 alive → display recovered, the tab opens", r.returncode == 0 and "gnome-terminal --title dacronfinestra" in r.stdout and "skip: headless" not in r.stdout, r.stdout + r.stderr)
    srv.close(); (rt13 / "wayland-0").unlink()   # close non toglie il file del socket: -S lo vedrebbe ancora
    r = run13("dacronsenza", False, "--window")
    T.check("L13 explicit --window with no compositor socket → launched anyway, the window skipped (T57)", r.returncode == 0 and "skip: headless" in r.stdout and "gnome-terminal" not in r.stdout, r.stdout + r.stderr)
    # L14 (S09): --agent codex e' una prova spenta di default; accesa, lancia Codex CLI in tmux senza gli argomenti di Claude
    r = run(str(home / "ws" / "personali" / "alfa"), "--agent", "codex", "--no-window")
    T.check("L14 --agent codex with experimental.codex off → exit 2 naming the option", r.returncode == 2 and "experimental.codex" in r.stderr, r.stdout + r.stderr)
    # L6b (13/10): config senza dialog_patterns e un claude fermo sulla schermata di accesso, che registra ogni tasto
    keys = tmp / "signin-keys.log"
    fake_signin = tmp / "fake-signin.sh"
    fake_signin.write_text('#!/bin/bash\necho " Welcome to Claude Code"\necho " How do you want to sign in?"\n'
                           'echo " ❯ 1. Claude account with subscription"\necho "   2. Anthropic Console account"\n'
                           'while IFS= read -rsn1 k; do printf "%%q\\n" "$k" >> "%s"; done\nexec sleep 300\n' % keys)
    fake_signin.chmod(0o755)
    cfg6b = tmp / "config-l6b.json"
    c6b = json.loads(cfg.read_text()); c6b["session"] = dict(c6b["session"], dialog_patterns=[])
    cfg6b.write_text(json.dumps(c6b))
    (home / "ws" / "personali" / "signin").mkdir(exist_ok=True)
    t0 = time.time()
    r = run(str(home / "ws" / "personali" / "signin"), "--no-window", extra={"CC_SUPERVISOR_CONFIG": str(cfg6b), "CM_CLAUDE_BIN": str(fake_signin)})
    took = time.time() - t0
    T.check("L6b empty dialog_patterns + sign-in screen → no key pressed, launch stops waiting and says the session waits for the person",
            r.returncode == 0 and not (keys.is_file() and keys.read_text().strip()) and "aspetta te" in r.stdout and took < 20,
            f"rc={r.returncode} took={took:.0f}s keys={keys.read_text() if keys.is_file() else ''}\n" + r.stdout + r.stderr)
    fake_codex = tmp / "fake-codex.sh"
    fake_codex.write_text('#!/bin/sh\necho "$@" > "%s"\necho ">_ OpenAI Codex (v0-demo)"\nexec sleep 300\n' % (tmp / "codex-args.log"))
    fake_codex.chmod(0o755)
    cfg14 = tmp / "config-l14.json"
    c14 = json.loads(cfg.read_text()); c14["experimental"] = {"codex": True}
    cfg14.write_text(json.dumps(c14))
    (home / "ws" / "personali" / "cxdemo").mkdir(exist_ok=True)
    r = run(str(home / "ws" / "personali" / "cxdemo"), "--agent", "codex", "--no-window", extra={"CC_SUPERVISOR_CONFIG": str(cfg14), "CM_CODEX_BIN": str(fake_codex)})
    T.check("L14 experimental.codex on → Codex CLI in tmux under the folder's name, without Claude's arguments (no -n, no remote control)",
            r.returncode == 0 and "cxdemo" in r.stdout and tm("has-session", "-t", "=cxdemo").returncode == 0 and (tmp / "codex-args.log").read_text().strip() == "", r.stdout + r.stderr)
    # L14b (dal vivo 14/09): Codex scrive «> You are in …» e il dialogo di fiducia anche 4 s dopo (macchina carica); launch
    # deve aspettarlo e rispondere (un Invio), e dichiarare pronta la sessione solo sul segno di Codex pronto
    trusted = tmp / "codex-trusted"
    fake_trust = tmp / "fake-codex-trust.sh"
    fake_trust.write_text('#!/bin/sh\necho "> You are in $PWD"\nsleep 4\necho "  Do you trust the contents of this directory?"\n'
                          'echo "› 1. Yes, continue"\necho "  2. No, quit"\nread ans\necho yes > "%s"\nclear\necho "› Ask Codex to do anything"\nexec sleep 300\n' % trusted)
    fake_trust.chmod(0o755)
    (home / "ws" / "personali" / "cxtrust").mkdir(exist_ok=True)
    r = run(str(home / "ws" / "personali" / "cxtrust"), "--agent", "codex", "--no-window", extra={"CC_SUPERVISOR_CONFIG": str(cfg14), "CM_CODEX_BIN": str(fake_trust)})
    T.check("L14b the trust dialog drawn a moment after the first line is answered (one Enter) before the session is declared ready",
            r.returncode == 0 and trusted.is_file() and "Ask Codex to do anything" in (tm("capture-pane", "-p", "-t", "cxtrust").stdout or ""), r.stdout + r.stderr + (tm("capture-pane", "-p", "-t", "cxtrust").stdout or ""))
    r = run(str(home / "ws" / "personali" / "alfa"), "--agent", "gemini", "--no-window", extra={"CC_SUPERVISOR_CONFIG": str(cfg14)})
    T.check("L14 unknown agent → exit 2 naming it", r.returncode == 2 and "gemini" in r.stderr, r.stderr)



def fake_attachers():
    """I finti attaccatori di cm-terminal.sh (backend fake) lanciati da questo test, riconosciuti dal loro log."""
    found = []
    for d in Path("/proc").iterdir():
        try:
            if d.name.isdigit() and f"CM_TERMINAL_FAKE_LOG={fakelog}".encode() in (d / "environ").read_bytes().split(b"\0") \
                    and (d / "cmdline").read_bytes().startswith(b"python3\0-\0"):
                found.append(int(d.name))
        except OSError:
            pass
    return found


# 11/10: col server tmux chiuso il finto attaccatore deve uscire da solo (ne restavano 22, accesi per giorni)
T.check("L15 the fake terminal's attach helpers exit once the tmux server is gone",
        T.wait_until(lambda: not fake_attachers(), 10), str(fake_attachers()))
T.rm(str(tmp))
T.finish()
