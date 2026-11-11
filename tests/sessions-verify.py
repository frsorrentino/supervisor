#!/usr/bin/env python3
"""Verifica cm-sessions.py contro un registro finto, processi veri e un tmux privato.

S1  voce del registro con pid vivo e procStart giusto → elencata (account dall'ambiente, T12)
S2  voce stantia (pid morto) → esclusa (T62)
S3  pid riusato (procStart diverso) → esclusa (T62)
S4  processo claude fuori registro → aggiunto dalla scansione di /proc con stato `?` (T11)
S5  VISTA: sessione tmux attaccata (client su pty) → aperta; senza client → STACCATA (T9)
S6  «aspetta una risposta» dallo schermo con DUE indizi (T13); un solo indizio → no
S7  flag dell'hook (state_dir/waiting/<session_id>) vince sullo schermo
S8  CANALE: registro del mio account → nativo; altro account, cartella separata → talk
S9  CANALE: cartella sessions condivisa via symlink → nativo anche per l'altro account (E1)
S10 CANALE: pid antenato di questo processo → (questa)
S11 --json: campi pid, account, name, status, tmux, link, channel
S12 le sessioni ferme e staccate vanno per prime; avviso server tmux morto
S13 NOME = nome tmux (quello che ogni comando accetta); se l'app l'ha rinominata (/rename: `name`
    del registro diverso) il nome dell'app segue fra parentesi (idea 9, 11/09)
S14 (S03) inglese esplicito: intestazione NAME/STATE, niente NOME/STATO/CARTELLA/ATTIVA-DA, niente questa|nativo|aspetta
S15 italiano esplicito: intestazione NOME/STATO
S16 una sessione in attesa di risposta mostra UNO stato («attesa»), non «busy» con «aspetta una risposta»
S17 il processo fuori registro ha un'etichetta leggibile invece del nome vuoto e del «?»
S18 senza `language` nella config: LANG it → italiano, LANG en → inglese, settings.json «italiano» → italiano anche con LANG en
S19 il valore esplicito vince sul rilevamento: `language: en` con LANG it → inglese
S23 idle da piu' di sessions.idle_hours → suggerimento di chiusura; --count-active per il tetto di launch
S24 il prompt suggerito in grigio dopo «❯» (suggestion), mai il testo digitato
S21 bassa priorita' dallo schermo («Working at lower priority»), goal nativo dal transcript (goal_status), nota in quota
S20 il server tmux (primo argomento «tmux», `…/claude` nella riga di comando) non e' un Claude fuori registro; il
    processo claude vero di S4 resta
S21 (S09, prova) experimental.codex acceso: il riquadro Codex (argv0 «codex») ha una riga con nome, cartella e stato dal
    rollout (task_started → busy, task_complete → idle); spento → nessuna riga
S22 VERSIONE: quella del binario in /proc/<pid>/exe vince sul registro; senza un exe che si chiami come una versione,
    quella del registro; le piu' vecchie della versione su disco (CM_CLAUDE_BIN) hanno `*` e la riga che lo spiega
S23 modello ripiegato dopo un messaggio segnalato (riga model_refusal_fallback nella trascrizione): `fallback` nel
    JSON e una nota con il modello di prima e il comando per tornare; tornati al modello di prima, niente
"""
import json
import os
import pty
import subprocess
import sys
import time

# S21 legge l'account di questo processo dal suo ambiente (/proc/<pid>/environ): lanciato da una sessione di un altro
# account, CLAUDE_CONFIG_DIR puntava a una cartella vera fuori dalla config del test e l'account non era dei due.
# Il test riparte senza, cosi' il suo ambiente iniziale e' pulito
if "CLAUDE_CONFIG_DIR" in os.environ:
    os.execve(sys.executable, [sys.executable, *sys.argv], {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"})
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

SCRIPT = T.SCRIPTS / "cm-sessions.py"


def proc_start(pid):
    return T.proc_start(pid)   # 11/11: Linux e macOS (cm-proc)


tmp = T.tmpdir()
home = Path(tmp) / "home"
(home / ".claude" / "sessions").mkdir(parents=True)
(home / ".claude-pixel" / "sessions").mkdir(parents=True)
(home / "ws" / "alfa").mkdir(parents=True)
(home / "ws" / "pix" / "beta").mkdir(parents=True)
cfg = Path(tmp) / "config.json"
cfg.write_text(json.dumps({
    "language": "it",
    "state_dir": str(Path(tmp) / "state"),
    "workspace": {"root": str(home / "ws")},
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "default_account": "personale",
}))
procs = []


def spawn(env_conf=None, argv0="sleep"):
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if env_conf:
        env["CLAUDE_CONFIG_DIR"] = env_conf
    p = subprocess.Popen(["bash", "-c", f'exec -a {argv0} sleep 300'], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    time.sleep(0.2)
    return p.pid


def entry(reg_dir, pid, name, cwd, tmux_name, status="idle", proc_start_v=None, started=None, sid=None):
    d = {"pid": pid, "name": name, "cwd": cwd, "status": status, "tmux": f"{tmux_name}:@0.%0",
         "startedAt": started or int(time.time() * 1000) - 3600_000, "procStart": proc_start_v or proc_start(pid),
         "sessionId": sid or f"sid-{name}", "bridgeSessionId": f"br_{name}", "messagingSocketPath": f"/tmp/{pid}.sock"}
    Path(reg_dir, f"{pid}.json").write_text(json.dumps(d))
    return d


def run(*args, extra=None):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg),
           "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_PROC_SCAN_PIDS": " ".join(str(p.pid) for p in procs)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra:
        env.update(extra)
    return subprocess.run([sys.executable, str(SCRIPT)] + list(args), capture_output=True, text=True, env=env, timeout=60)


with T.PrivateTmux() as tm:
    # sessioni tmux finte con una shell dentro
    for s in ("alfa", "pix-beta", "gamma"):
        tm("new-session", "-d", "-s", s, "-x", "100", "-y", "30", "bash", "--norc")
    time.sleep(0.5)
    # client su pty per "alfa": risulta attaccata (T9)
    master, slave = pty.openpty()
    client = subprocess.Popen(["tmux", "-L", tm.socket, "attach", "-t", "=alfa"], stdin=slave, stdout=slave, stderr=slave,
                              start_new_session=True, env=dict(os.environ, TERM=os.environ.get("TERM") or "xterm-256color"))   # senza TERM tmux non si attacca
    time.sleep(1)

    # S1: personale, vivo, registro del personale
    p1 = spawn()
    e1 = entry(home / ".claude" / "sessions", p1, "alfa", str(home / "ws" / "alfa"), "alfa", status="busy")
    # S8: professionale in cartella separata
    p2 = spawn(env_conf=str(home / ".claude-pixel"))
    entry(home / ".claude-pixel" / "sessions", p2, "pix-beta", str(home / "ws" / "pix" / "beta"), "pix-beta")
    # S2: stantia
    dead = subprocess.Popen(["sleep", "0.01"]); dead.wait()
    entry(home / ".claude" / "sessions", dead.pid, "morta", str(home), "morta", proc_start_v="1")
    # S3: pid vivo ma procStart sbagliato (pid riusato)
    p3 = spawn()
    entry(home / ".claude" / "sessions", p3, "riusata", str(home), "riusata", proc_start_v="424242")
    # S4: fuori registro, cmdline "claude"
    p4 = spawn(argv0="claude")
    # S20: un finto server tmux con la riga di comando della prima sessione dentro (come il vero, pid 1141 il 14/09)
    # perl e non python3: su macOS il python3 di sistema si rilancia e perde l'argv0 finto (11/11)
    p_tmux = subprocess.Popen(["bash", "-c", "exec -a tmux perl -e 'sleep 300' new-session -d -s master env /home/demo/.local/bin/claude --x"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p_tmux)
    # S20b: Claude Code 2.1.295 keeps each --bg session's terminal in a helper «claude bg-pty-host …» (seen 08/10)
    p_pty = subprocess.Popen(["bash", "-c", "exec -a claude perl -e 'sleep 300' bg-pty-host --bg-pty-host /tmp/x.sock 200 50"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p_pty)
    time.sleep(0.3)
    # S10: questo processo di test come antenato
    entry(home / ".claude" / "sessions", os.getpid(), "questa-prova", str(home), "gamma")

    r = run("--json")
    T.check("S run exit 0", r.returncode == 0, r.stderr[-500:])
    rows = json.loads(r.stdout) if r.returncode == 0 else []
    by = {x["name"] or f"pid{x['pid']}": x for x in rows}
    T.check("S1 live entry listed with status busy", by.get("alfa", {}).get("status") == "busy", str(list(by)))
    T.check("S1 account from environment (personale)", by.get("alfa", {}).get("account") == "personale", str(by.get("alfa")))
    T.check("S1 link from bridgeSessionId", by.get("alfa", {}).get("link") == "https://claude.ai/code/session_br_alfa", str(by.get("alfa")))
    T.check("S2 stale entry excluded", "morta" not in by, str(list(by)))
    if T.macos():   # su macOS procStart non si confronta (Claude Code non scrive li' il campo 22 di /proc): non si sa
        print("  skip S3 (macOS: a reused pid cannot be told from procStart)")
    else:
        T.check("S3 reused pid excluded", "riusata" not in by, str(list(by)))
    T.check("S4 unregistered claude process from /proc with status ?", by.get(f"pid{p4}", {}).get("status") == "?", str(list(by)))
    T.check("S20 the tmux server (argv0 tmux, …/claude in its command line) is not listed; the real claude process of S4 is", f"pid{p_tmux.pid}" not in by and f"pid{p4}" in by, str(list(by)))
    T.check("S20b the --bg terminal helper (claude bg-pty-host) is not a session", f"pid{p_pty.pid}" not in by, str(list(by)))
    T.check("S5 alfa attached (client on pty)", by.get("alfa", {}).get("attached") is True, str(by.get("alfa")))
    T.check("S5 pix-beta detached", by.get("pix-beta", {}).get("attached") is False, str(by.get("pix-beta")))
    T.check("S8 professionale in separate dir → talk", by.get("pix-beta", {}).get("channel") == "talk", str(by.get("pix-beta")))
    T.check("S8 personale → nativo", by.get("alfa", {}).get("channel") == "nativo", str(by.get("alfa")))
    T.check("S10 ancestor pid → (questa)", by.get("questa-prova", {}).get("channel") == "(questa)", str(by.get("questa-prova")))
    T.check("S11 json fields", all(k in by.get("alfa", {}) for k in ("pid", "account", "name", "status", "tmux", "link", "channel", "waiting")), str(by.get("alfa")))

    # S6: schermo con due indizi in pix-beta
    tm("send-keys", "-t", "pix-beta", "printf '\\n  1. Yes\\n  2. No\\n  Enter to select · Esc to cancel\\n'", "Enter")
    time.sleep(0.8)
    rows = json.loads(run("--json").stdout)
    by = {x["name"] or f"pid{x['pid']}": x for x in rows}
    T.check("S6 numbered list + 'to select' → waiting", by.get("pix-beta", {}).get("waiting") is True,
            "pane: " + repr(tm("capture-pane", "-p", "-t", "pix-beta").stdout[-300:]))
    T.check("S12 waiting+detached sorted first", rows and rows[0]["name"] == "pix-beta", str([x["name"] for x in rows]))
    # un solo indizio (solo "Esc to cancel", niente elenco) → no
    tm("send-keys", "-t", "gamma", "clear; printf '\\n  working...  Esc to cancel\\n'", "Enter")
    time.sleep(0.8)
    rows = json.loads(run("--json").stdout)
    by = {x["name"] or f"pid{x['pid']}": x for x in rows}
    T.check("S6 single hint → not waiting", by.get("questa-prova", {}).get("waiting") is False, str(by.get("questa-prova")))

    # S6-bis: stato `waiting` nel registro → aspetta (senza guardare lo schermo)
    e = json.loads((home / ".claude" / "sessions" / f"{p1}.json").read_text()); e["status"] = "waiting"
    (home / ".claude" / "sessions" / f"{p1}.json").write_text(json.dumps(e))
    rows = json.loads(run("--json", "--no-screen").stdout)
    by = {x["name"]: x for x in rows if x["name"]}
    T.check("S6-bis registry status waiting → waiting", by.get("alfa", {}).get("waiting") is True, str(by.get("alfa")))
    e["status"] = "busy"; (home / ".claude" / "sessions" / f"{p1}.json").write_text(json.dumps(e))

    # S7: flag dell'hook vince
    (Path(tmp) / "state" / "waiting").mkdir(parents=True)
    (Path(tmp) / "state" / "waiting" / "sid-alfa").write_text("AskUserQuestion")
    rows = json.loads(run("--json").stdout)
    by = {x["name"]: x for x in rows if x["name"]}
    T.check("S7 hook flag → waiting even with clean screen", by.get("alfa", {}).get("waiting") is True, str(by.get("alfa")))

    # S21 (25/09/2026): bassa priorita' dallo schermo, goal nativo dal transcript, nota in quota
    tm("send-keys", "-t", "gamma", "clear; printf '\\n  Working at lower priority \xc2\xb7 waiting for capacity\\n'", "Enter")
    time.sleep(0.8)
    import re as _re
    slug = _re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(home / "ws" / "alfa")))
    tr = home / ".claude" / "projects" / slug / "sid-alfa.jsonl"
    tr.parent.mkdir(parents=True, exist_ok=True)
    goal = lambda **kw: json.dumps({"type": "attachment", "uuid": "u", "attachment": dict({"type": "goal_status", "met": False, "sentinel": True, "condition": "tests green"}, **kw)}) + "\n"  # noqa: E731
    tr.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "x"}]}}) + "\n"
                  + goal() + goal(sentinel=False, iterations=2, met=False))
    rows = json.loads(run("--json").stdout)
    by = {x["name"]: x for x in rows if x["name"]}
    T.check("S21 «Working at lower priority» on the screen → low_priority «active»; a clean screen → «off»", by.get("questa-prova", {}).get("low_priority") == "active" and by.get("alfa", {}).get("low_priority") == "off", str({k: v.get("low_priority") for k, v in by.items()}))
    T.check("S21 the last goal_status attachment not met → goal with its condition and iterations; goal_status {text, since, met} for the watch", by.get("alfa", {}).get("goal") == "tests green" and by.get("alfa", {}).get("goal_iterations") == 2 and by.get("alfa", {}).get("goal_status") == {"text": "tests green", "since": None, "met": False}, str(by.get("alfa")))
    table = run().stdout
    T.check("S21 table: «a bassa priorità» and «goal: …» as notes", "a bassa priorità" in table and "goal: «tests green» (2 controlli)" in table, table)
    rows = json.loads(run("--json", "--no-screen").stdout)
    by = {x["name"]: x for x in rows if x["name"]}
    T.check("S21 --no-screen: low_priority null (not known), the goal still read", by.get("questa-prova", {}).get("low_priority") is None and by.get("alfa", {}).get("goal") == "tests green", str(by.get("alfa")))
    with open(tr, "a") as f:
        f.write(goal(sentinel=False, iterations=3, met=True))
    rows = json.loads(run("--json", "--no-screen").stdout)
    by = {x["name"]: x for x in rows if x["name"]}
    T.check("S21 goal met → no goal in the table, goal_status met true for the watch", by.get("alfa", {}).get("goal") == "" and (by.get("alfa", {}).get("goal_status") or {}).get("met") is True, str(by.get("alfa")))
    # l'offerta al muro del limite → «offered» e la nota
    tm("send-keys", "-t", "gamma", "clear; printf '\\n  Usage limit reached \xc2\xb7 /low-priority to continue now at lower priority \xc2\xb7 uses your weekly limit\\n'", "Enter")
    time.sleep(0.8)
    rows = json.loads(run("--json").stdout)
    by = {x["name"]: x for x in rows if x["name"]}
    T.check("S21 the offer line on the screen → low_priority «offered» and the note «proposto /low-priority»", by.get("questa-prova", {}).get("low_priority") == "offered" and "proposto /low-priority" in run().stdout, str(by.get("questa-prova")))
    tm("send-keys", "-t", "gamma", "clear; printf '\\n  Working at lower priority \xc2\xb7 waiting for capacity\\n'", "Enter")
    time.sleep(0.8)
    # S24 (contratto 1.23 del relay): il suggerimento grigio (SGR 2) dopo «❯» a casella vuota; testo digitato → None
    tm("send-keys", "-t", "gamma", "clear; printf '\\n❯ \\033[2mrun the full test suite\\033[0m\\n'", "Enter")
    time.sleep(0.8)
    sg = {x["name"]: x.get("suggestion") for x in json.loads(run("--json").stdout) if x["name"]}
    tm("send-keys", "-t", "gamma", "clear; printf '\\n❯ typed \\033[2mrun the full test suite\\033[0m\\n'", "Enter")
    time.sleep(0.8)
    sg2 = {x["name"]: x.get("suggestion") for x in json.loads(run("--json").stdout) if x["name"]}
    sg3 = {x["name"]: x.get("suggestion") for x in json.loads(run("--json", "--no-screen").stdout) if x["name"]}
    T.check("S24 dim text after «❯» → suggestion «run the full test suite»; with typed text before it → None; --no-screen → None",
            sg.get("questa-prova") == "run the full test suite" and sg2.get("questa-prova") is None and sg3.get("questa-prova") is None,
            f"{sg.get('questa-prova')!r} {sg2.get('questa-prova')!r}")
    tm("send-keys", "-t", "gamma", "clear; printf '\\n  Working at lower priority \xc2\xb7 waiting for capacity\\n'", "Enter")
    time.sleep(0.8)
    # quota: la nota per l'account con una sessione a bassa priorita' (file quota della statusline finto)
    import hashlib
    qsrc = home / ".claude" / "fable-director"; qsrc.mkdir(parents=True, exist_ok=True)
    acct = by.get("questa-prova", {}).get("account") or "personale"
    conf_dir = {"personale": str(home / ".claude"), "professionale": str(home / ".claude-pixel")}[acct]
    (qsrc / f"quota-{hashlib.sha256(conf_dir.encode()).hexdigest()[:8]}.json").write_text(json.dumps({"five_hour_used_pct": 10, "weekly_used_pct": 91, "weekly_resets_at": time.time() + 86400}))
    env_q = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"], "CM_PROC_SCAN_PIDS": " ".join(str(p.pid) for p in procs)}
    env_q.pop("CLAUDE_CONFIG_DIR", None)
    rq = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-quota.py")], capture_output=True, text=True, env=env_q, timeout=60)
    T.check("S21 quota: «N sessione/i in modalità a bassa priorità» for that account; nothing with --no-screen", rq.returncode == 0 and f"«{acct}»: 1 sessione/i in modalità a bassa priorità" in rq.stdout
            and "bassa priorità" not in subprocess.run([sys.executable, str(T.SCRIPTS / "cm-quota.py"), "--no-screen"], capture_output=True, text=True, env=env_q, timeout=60).stdout, rq.stdout + rq.stderr)
    tm("send-keys", "-t", "gamma", "clear", "Enter")
    time.sleep(0.5)

    # S23 (25/09/2026): idle da piu' di sessions.idle_hours → suggerimento di chiusura; --count-active per il tetto
    e = json.loads((home / ".claude-pixel" / "sessions" / f"{p2}.json").read_text())
    e["status"] = "idle"; e["statusUpdatedAt"] = int(time.time() * 1000) - 3 * 3600_000
    (home / ".claude-pixel" / "sessions" / f"{p2}.json").write_text(json.dumps(e))
    table = run("--no-screen").stdout
    T.check("S23 idle for 3 h → «idle da 3 h: se non serve, supervisor close pix-beta»", "idle da 3 h: se non serve, supervisor close pix-beta" in table, table)
    rows = json.loads(run("--json", "--no-screen").stdout)
    by = {x["name"]: x for x in rows if x["name"]}
    T.check("S23 json: status_age_min", by.get("pix-beta", {}).get("status_age_min") == 180, str(by.get("pix-beta")))
    e["status"] = "busy"; e["statusUpdatedAt"] = int(time.time() * 1000)
    (home / ".claude-pixel" / "sessions" / f"{p2}.json").write_text(json.dumps(e))
    e["status"] = "busy"
    # attive: alfa (waiting per il flag S7), pix-beta, questa-prova; fuori registro e master non contano
    entry(home / ".claude" / "sessions", spawn(), "master", str(home / "ws"), "master")
    rc = run("--count-active")
    # su macOS anche «riusata» (S3) conta: li' un pid riusato non si riconosce
    T.check("S23 --count-active: busy/idle/waiting in the registry, not the master, not the unregistered process", rc.stdout.strip() == ("4" if T.macos() else "3"), rc.stdout + rc.stderr + str([(x["name"], x["status"]) for x in rows]))

    # tabella
    r = run()
    T.check("S table renders header and rows", "PID" in r.stdout and "alfa" in r.stdout and "aperta" in r.stdout, r.stdout[:500])
    T.check("S table abandoned note", "nessuno la guarda" in r.stdout or "ferma" in r.stdout, r.stdout)
    T.check("S13 renamed in the app: tmux name first, app name in parentheses", "gamma (questa-prova)" in r.stdout, r.stdout)

    # S14-S19 (S03): alfa e' busy con il flag dell'hook (S7), p4 e' il processo fuori registro (S4)
    import re as _re

    def cfg_lang(fname, lang):
        d = json.loads(cfg.read_text()); d.pop("language", None)
        if lang:
            d["language"] = lang
        p = Path(tmp) / fname; p.write_text(json.dumps(d))
        return str(p)

    def head(res):
        return (res.stdout.splitlines() or [""])[0]
    en_cfg, it_cfg, auto_cfg = cfg_lang("cm-en.json", "en"), cfg_lang("cm-it.json", "it"), cfg_lang("cm-auto.json", None)
    r = run(extra={"CC_SUPERVISOR_CONFIG": en_cfg})
    T.check("S14 English (explicit): header NAME and STATE, none of NOME STATO CARTELLA ATTIVA-DA; no questa|nativo|aspetta anywhere",
            "NAME" in head(r) and "STATE" in head(r) and not any(w in head(r) for w in ("NOME", "STATO", "CARTELLA", "ATTIVA-DA")) and not _re.search(r"\(questa\)|\bnativo\b|aspetta", r.stdout), r.stdout[:900])
    r = run(extra={"CC_SUPERVISOR_CONFIG": it_cfg})
    T.check("S15 Italian (explicit): header NOME and STATO", "NOME" in head(r) and "STATO" in head(r), head(r))
    alfa_line = next((l for l in r.stdout.splitlines() if l.split()[2:3] == ["alfa"]), "")
    T.check("S16 a session waiting for an answer shows one state («attesa»), not busy next to «aspetta una risposta»", "attesa" in alfa_line and "busy" not in alfa_line, alfa_line or r.stdout[:600])
    unreg = [l for l in r.stdout.splitlines() if "(fuori registro)" in l]
    T.check("S17 the claude process outside the registry: a readable label instead of an empty name and «?»", bool(unreg) and " ? " not in unreg[0], "\n".join(unreg) or r.stdout[:600])
    st_json = home / ".claude" / "settings.json"
    saved_st = st_json.read_text() if st_json.exists() else None
    st_json.unlink(missing_ok=True)
    r_it = run(extra={"CC_SUPERVISOR_CONFIG": auto_cfg, "LANG": "it_IT.UTF-8"})
    r_en = run(extra={"CC_SUPERVISOR_CONFIG": auto_cfg, "LANG": "en_US.UTF-8"})
    st_json.write_text(json.dumps({"language": "italiano"}))
    r_set = run(extra={"CC_SUPERVISOR_CONFIG": auto_cfg, "LANG": "en_US.UTF-8"})
    r_win = run(extra={"CC_SUPERVISOR_CONFIG": en_cfg, "LANG": "it_IT.UTF-8"})
    st_json.write_text(saved_st) if saved_st is not None else st_json.unlink()
    T.check("S18 no language in the config: LANG it → Italian, LANG en → English, settings.json «italiano» → Italian even with LANG en",
            "NOME" in head(r_it) and "NAME" in head(r_en) and "NOME" in head(r_set), f"{head(r_it)} | {head(r_en)} | {head(r_set)}")
    T.check("S19 the explicit value wins over detection: language en with LANG it → English", "NAME" in head(r_win) and "NOME" not in head(r_win), head(r_win))

    # S21 (S09): una sessione Codex CLI in un riquadro tmux (argv0 «codex») con experimental.codex acceso → una riga con
    # nome, cartella e stato dal rollout; spento → nessuna riga
    (home / "ws" / "cx").mkdir(parents=True, exist_ok=True)
    tm("new-session", "-d", "-s", "cxs", "-c", str(home / "ws" / "cx"), "bash", "-c", "exec -a codex sleep 300")
    time.sleep(0.5)
    cx_pid = int(tm("list-panes", "-t", "cxs", "-F", "#{pane_pid}").stdout.strip())
    roll = home / ".codex" / "sessions" / "2026" / "09" / "14"
    roll.mkdir(parents=True, exist_ok=True)
    rf = roll / "rollout-2026-09-14T21-00-00-demo.jsonl"
    rf.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": str(home / "ws" / "cx")}}) + "\n"
                  + json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
    cx_cfg = cfg_lang("cm-codex.json", "en")
    d = json.loads(Path(cx_cfg).read_text()); d["experimental"] = {"codex": True}; Path(cx_cfg).write_text(json.dumps(d))
    scan = " ".join(str(p.pid) for p in procs) + f" {cx_pid}"

    def codex_rows(conf):
        return [x for x in json.loads(run("--json", "--no-screen", extra={"CC_SUPERVISOR_CONFIG": conf, "CM_PROC_SCAN_PIDS": scan}).stdout) if x.get("agent") == "codex"]
    cx = codex_rows(cx_cfg)
    T.check("S21 experimental.codex on: the Codex pane is listed with its tmux name, folder and the rollout's state (task_started → busy)",
            len(cx) == 1 and cx[0]["name"] == "cxs" and os.path.realpath(cx[0]["cwd"]) == os.path.realpath(str(home / "ws" / "cx")) and cx[0]["status"] == "busy", str(cx))
    with open(rf, "a") as f:
        f.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n")
    cx = codex_rows(cx_cfg)
    table = run("--no-screen", extra={"CC_SUPERVISOR_CONFIG": cx_cfg, "CM_PROC_SCAN_PIDS": scan}).stdout
    T.check("S21 task_complete → idle; the table shows it by name, not as «(unregistered)»", cx and cx[0]["status"] == "idle" and any(l.split()[2:3] == ["cxs"] and "idle" in l for l in table.splitlines()), str(cx) + table[:600])
    T.check("S21 experimental.codex off (default): no Codex row", codex_rows(en_cfg) == [], "")

    # S23: la trascrizione di alfa (account personale, cartella ws/alfa) con un ripiego dopo un messaggio segnalato
    import re as _re9
    tdir9 = home / ".claude" / "projects" / _re9.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(home / "ws" / "alfa"))
    tdir9.mkdir(parents=True, exist_ok=True)
    t9 = tdir9 / "sid-alfa.jsonl"
    rip = json.dumps({"type": "system", "subtype": "model_refusal_fallback", "trigger": "refusal", "direction": "retry",
                      "scope": "session", "originalModel": "claude-opus-5-5", "fallbackModel": "claude-sonnet-5",
                      "apiRefusalCategory": "cyber", "timestamp": "2026-09-22T19:59:00.000Z"}, separators=(",", ":"))
    turn = lambda m: json.dumps({"type": "assistant", "message": {"model": m}}, separators=(",", ":"))  # noqa: E731
    t9.write_text("\n".join([turn("claude-opus-5-5"), rip, turn("claude-sonnet-5")]) + "\n")
    by = {x["name"]: x for x in json.loads(run("--json", "--no-screen").stdout)}
    T.check("S23 --json: fallback {from, to, category, at} on the session that fell back, null on the others",
            by.get("alfa", {}).get("fallback") == {"from": "claude-opus-5-5", "to": "claude-sonnet-5", "category": "cyber", "at": 1790107140}
            and by.get("pix-beta", {}).get("fallback") is None, str(by.get("alfa", {}).get("fallback")))
    table = run("--no-screen").stdout
    line = next((l for l in table.splitlines() if l.split()[2:3] == ["alfa"]), "")
    T.check("S23 the table: the note names both models and how to go back (/model, supervisor model alfa <id of Opus 5.5>)",
            "ripiegata su claude-sonnet-5 al posto di claude-opus-5-5" in line and "supervisor model alfa claude-opus-5-5[1m]" in line, line or table[:900])
    t9.write_text("\n".join([turn("claude-opus-5-5"), rip, turn("claude-sonnet-5"), turn("claude-opus-5-5")]) + "\n")
    by = {x["name"]: x for x in json.loads(run("--json", "--no-screen").stdout)}
    T.check("S23 back on the model it had: no fallback", by.get("alfa", {}).get("fallback") is None, str(by.get("alfa")))
    t9.unlink()

    # S22: un processo che esegue davvero un binario chiamato «2.1.278» (come ~/.local/share/claude/versions/2.1.278)
    # mentre il registro dice 2.1.280, e uno col solo registro (2.1.279); su disco c'e' la 2.1.280
    import shutil
    vdir = Path(tmp) / "versions"
    vdir.mkdir()
    shutil.copy(shutil.which("sleep"), vdir / "2.1.278")
    (vdir / "2.1.280").write_text("")
    pv = subprocess.Popen([str(vdir / "2.1.278"), "300"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(pv)
    pr = spawn()
    time.sleep(0.2)
    for pid, name, ver in ((pv.pid, "vecchia-exe", "2.1.280"), (pr, "vecchia-reg", "2.1.279")):
        entry(home / ".claude" / "sessions", pid, name, str(home), name)
        d = json.loads((home / ".claude" / "sessions" / f"{pid}.json").read_text()); d["version"] = ver
        (home / ".claude" / "sessions" / f"{pid}.json").write_text(json.dumps(d))
    disk = {"CM_CLAUDE_BIN": str(vdir / "2.1.280")}
    by = {x["name"]: x for x in json.loads(run("--json", "--no-screen", extra=disk).stdout)}
    T.check("S22 the version comes from /proc/<pid>/exe and wins over the registry: 2.1.278, older than the 2.1.280 on disk",
            by.get("vecchia-exe", {}).get("version") == "2.1.278" and by["vecchia-exe"].get("outdated") is True, str(by.get("vecchia-exe")))
    T.check("S22 an exe not named like a version: the registry's version, still older than the disk",
            by.get("vecchia-reg", {}).get("version") == "2.1.279" and by["vecchia-reg"].get("outdated") is True, str(by.get("vecchia-reg")))
    T.check("S22 a session without any version is not marked", by.get("alfa", {}).get("version") == "" and by["alfa"].get("outdated") is False, str(by.get("alfa")))
    table = run("--no-screen", extra=disk).stdout
    line = next((l for l in table.splitlines() if l.split()[2:3] == ["vecchia-exe"]), "")
    T.check("S22 the table: VERSIONE column, `2.1.278*` on the row, the line that explains the mark with the disk's version",
            "VERSIONE" in table.splitlines()[0] and "2.1.278*" in line and "2.1.280 su disco" in table, table[:1200])
    same = run("--no-screen", extra={"CM_CLAUDE_BIN": str(vdir / "2.1.278")}).stdout
    T.check("S22 nothing older than the disk: no mark, no explanation", "*" not in same.split("\n\n")[0] and "su disco" not in same, same[:1200])
    for pid in (pv.pid, pr):
        (home / ".claude" / "sessions" / f"{pid}.json").unlink()

    # S9: cartella condivisa via symlink
    shutil.rmtree(home / ".claude-pixel" / "sessions")
    os.symlink(home / ".claude" / "sessions", home / ".claude-pixel" / "sessions")
    entry(home / ".claude" / "sessions", p2, "pix-beta", str(home / "ws" / "pix" / "beta"), "pix-beta")
    rows = json.loads(run("--json").stdout)
    by = {x["name"]: x for x in rows if x["name"]}
    T.check("S9 shared registry → professionale reachable natively", by.get("pix-beta", {}).get("channel") == "nativo", str(by.get("pix-beta")))
    T.check("S9 account still from environment", by.get("pix-beta", {}).get("account") == "professionale", str(by.get("pix-beta")))
    T.check("S9 shared dir read once (no duplicate rows)", sum(1 for x in rows if x["name"] == "pix-beta") == 1, str([x["name"] for x in rows]))

    client.kill()

# S12: server tmux morto → avviso
r = run("--json", extra={"CM_TMUX_ARGS": "-L cm-nonexistent-socket"})
T.check("S12 no tmux server: rows still listed from registry", r.returncode == 0 and "alfa" in r.stdout, r.stderr[-300:])
r = run(extra={"CM_TMUX_ARGS": "-L cm-nonexistent-socket"})
T.check("S12 tmux dead warning", "tmux" in r.stdout and ("non risponde" in r.stdout or "not responding" in r.stdout), r.stdout[-400:])

for p in procs:
    p.kill()
T.rm(tmp)
T.finish()
