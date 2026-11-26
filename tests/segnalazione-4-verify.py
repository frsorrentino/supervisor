#!/usr/bin/env python3
"""Le osservazioni ancora aperte della issue #4, il rapporto di claude-observe dal Mac di un collega (note dall'11 al 25/11
su 0.8.146-0.8.150) (26/11/2026, supervisor-issue4-osservazioni-26-11). I nomi qui sono finti.

S1 night: senza nessun rapporto della notte l'op night non e' fra le `ops` dello stato (il 12/11 «no night report yet»
   614 volte in un minuto), con un rapporto si'; «no night report yet» e' uno stato normale, non una nota
S2 stati normali nuovi: la domanda cambiata dopo la risposta, un file che non c'e' piu' sul disco, un suggerimento con la
   master chiusa; i difetti veri del 22/11 restano note (non chiusa perche' attaccata al PC, nessuna sessione, opzioni
   che la domanda non ha)
S3 un'op fallita va fra le note senza il nome della sessione (domini di clienti): <session> al suo posto
S4 le note gia' raccolte: una volta sola, il nome della sessione (project) diventa <session> in errore, chiave e
   esempio; le note gia' mandate non si toccano; la seconda volta niente
S5 la domanda dice cosa ha: answer --show elenca le voci fisse («+ Chat about this»), il relay le legge (foot) e dice
   se c'e' un «don't ask again» (allow_all), cosi' l'app non offre opzioni che mancano
S6 un file nominato dalla conversazione ma sparito dal disco: «non c'e' piu' sul disco», non «illeggibile», e nessuna nota
S7 un suggerimento accettato con la master chiusa: torna nella lista e il messaggio dice di riaprirla (prima talk
   rispondeva «sessione master inesistente» e il suggerimento era gia' tolto)
S8 fuori da tmux (il Mac prima del 19/11): needs-you e restart dicono cosa fare invece, non solo che manca tmux
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
(ws / "personali" / "alfa").mkdir(parents=True)
(home / ".claude" / "sessions").mkdir(parents=True)
state, rdir, store = tmp / "state", tmp / "relay", tmp / "observe"
for d in (state, rdir, store):
    d.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({
    "language": "it", "state_dir": str(state), "default_account": "personale",
    "workspace": {"root": str(ws), "root_session_name": "master", "project_dirs": ["personali"]},
    "accounts": {"personale": {"config_dir": str(home / ".claude")}},
    "relay": {"enabled": False, "dir": str(rdir), "duplicate_prompt_s": 0},
    "registry": {"file": str(tmp / "registry.json")}, "tabs": {"color_registry": str(tmp / "colors")},
}))
ocfg = tmp / "observe.json"
ocfg.write_text(json.dumps({"dir": str(store), "anonymizer": str(tmp / "none.py")}))
fake_cm = tmp / "supervisor"
fake_cm.write_text("""#!/bin/sh
case "$1" in
  sessions) echo '[]' ;;
  registry) echo '{"sessioni": []}' ;;
  *) echo "supervisor $1: not here" >&2; exit 1 ;;
esac
""")
fake_cm.chmod(0o755)
for k in ("TMUX", "TMUX_PANE", "CLAUDECODE", "CLAUDE_OBSERVE_TOOL", "XDG_STATE_HOME", "CM_HOOK_TMUX_NAME"):
    os.environ.pop(k, None)
os.environ.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_RELAY_CM": str(fake_cm),
                   "CM_PROC_SCAN_PIDS": "", "CLAUDE_OBSERVE_CONFIG": str(ocfg), "CM_RELAY_NO_PUSH": "1", "LANG": "it_IT.UTF-8"})


def load(name, tag="_s4"):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + tag, T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RL = load("cm-relay")
RL.push_async = lambda *a, **k: None
OBS = RL._observe()
CLIENT = "cliente-esempio1897-it"


def notes():
    return [r for r in OBS.all_records() if r.get("source") == "relay"]


def empty_box():
    for f in store.glob("*"):
        f.unlink()


# S1
no_report = "night" not in RL.offered_ops()
nd = home / ".team-supervisor" / "night-report"
nd.mkdir(parents=True)
(nd / "2026-11-25.json").write_text(json.dumps({"date": "2026-11-25", "generated_at": 1}))
with_report = "night" in RL.offered_ops()
T.check("S1 night: no night report → night is not among the ops of the state; with one it is; «no night report yet» is a normal state",
        no_report and with_report and RL.normal_state("no night report yet") and RL.normal_state("relay night no night report yet"),
        f"{no_report} {with_report}")

# S2
M = RL.M
NOISE = [M("relay.cmd_question_changed", name="beta"), M("relay.cmd_file_gone", name="foto.png"),
         M("relay.cmd_suggestion_master_closed", name="master")]
REAL = ["beta not closed: it is attached to the PC", "beta not closed: a job is running in the background", "no session beta",
        "no «don't ask again» option on this question", "questa domanda non ha la voce «Chat about this»", "missing or unreadable"]
wrong = [t for t in NOISE if not RL.normal_state(t)] + [f"REAL:{t}" for t in REAL if RL.normal_state(t)]
T.check("S2 new normal states (question changed, file gone, master closed for a suggestion); the real defects of 22/11 stay notes",
        not wrong and "{" not in "".join(NOISE), " | ".join(wrong) + " | " + " | ".join(NOISE))

# S3
empty_box()
r3 = RL.run_cmd({"id": "c-s3", "op": "slash", "session": CLIENT, "arg": "/clear", "by": "phone"})
n3 = notes()
T.check("S3 a failed op goes to the notes without the session name: <session> instead (the app still gets its text)",
        r3 and r3["ok"] is False and CLIENT in r3["text"] and n3 and all(CLIENT not in json.dumps(n) .replace(f'"project": "{CLIENT}"', "") for n in n3)
        and any("<session>" in str(n.get("error")) for n in n3)
        and hasattr(RL, "scrub_names") and RL.scrub_names(f"win:{CLIENT} not closed: it is attached to the PC", f"win:{CLIENT}") == "<session> not closed: it is attached to the PC"
        and RL.scrub_names(f"no session {CLIENT}", CLIENT) == "no session <session>"
        and RL.scrub_names(f"{CLIENT}.json e {CLIENT}x", CLIENT) == f"{CLIENT}.json e {CLIENT}x",
        f"{r3 and r3['text']!r} | {[(n.get('call'), n.get('error')) for n in n3]}")

# S4
empty_box()
OBS.record_external("relay", "relay close", f"{CLIENT} not closed: it is attached to the PC", CLIENT)
OBS.record_external("relay", "relay slash", "no session workspaces-11", "workspaces-11")
OBS.record_external("relay", "relay answer", "questa domanda non ha la voce «Chat about this»", "alfa")
with OBS.Box(OBS.TOOL["name"]) as box:
    for x in box.recs:
        if "workspaces-11" in str(x.get("error")):
            x["status"] = "reported"
flag = rdir / "notes-names-scrubbed"
flag.unlink(missing_ok=True)
got = RL.scrub_note_names() if hasattr(RL, "scrub_note_names") else "missing"
again = RL.scrub_note_names() if hasattr(RL, "scrub_note_names") else "missing"
by = {n.get("call"): n for n in notes()}
c, s, a = by.get("relay close") or {}, by.get("relay slash") or {}, by.get("relay answer") or {}
T.check("S4 the notes already collected: once, the session name becomes <session> (error and key); a note already sent is not touched; the second time nothing",
        got == 1 and again is None and CLIENT not in json.dumps({k: v for k, v in c.items() if k != "project"})
        and c.get("error") == "<session> not closed: it is attached to the PC" and "workspaces-11" in str(s.get("error"))
        and a.get("error") == "questa domanda non ha la voce «Chat about this»",
        f"{got} {again} | {c} | {s.get('error')}")

# S5
A = load("cm-answer")
SCREEN = """ ☐ Deploy

Pubblico adesso?

❯ 1. Sì
     va in produzione
  2. No
  3. Type something.
  4. Chat about this

Enter to select · ↑/↓ to navigate · Esc to cancel
"""


class FakeTerm:
    def __init__(self, scr):
        self.scr = scr

    def screen(self):
        return self.scr


A.TERMS["alfa"] = FakeTerm(SCREEN)
A.TERMS["gamma"] = FakeTerm(SCREEN.replace("  4. Chat about this\n", ""))
buf, buf2 = io.StringIO(), io.StringIO()
with redirect_stdout(buf):
    A.show("alfa")
with redirect_stdout(buf2):
    A.show("gamma")
shown, shown2 = buf.getvalue(), buf2.getvalue()
real_run_cm = RL.run_cm
RL.run_cm = lambda *a, **k: (0, {"alfa": shown, "gamma": shown2}.get(a[1], ""))
q1, o1 = RL.question_of("alfa", "AskUserQuestion")
f1 = dict(getattr(RL, "_FOOT", {})).get("alfa")
q2, o2 = RL.question_of("gamma", "AskUserQuestion")
f2 = dict(getattr(RL, "_FOOT", {})).get("gamma")
RL.run_cm = real_run_cm
perm = ["Yes", "Yes, and don't ask again for git push", "No"]
src = (T.SCRIPTS / "cm-relay.py").read_text()
T.check("S5 the question says what it has: answer --show lists the fixed entries, the relay reads them (foot) and the «don't ask again» (allow_all)",
        "+ Chat about this" in shown and "+ Type something." in shown and "+ Chat about this" not in shown2
        and o1 == ["Sì", "No"] and f1 == ["type", "chat"] and f2 == ["type"] and q1 == "Pubblico adesso?"
        and hasattr(RL, "ALLOW_ALL_RE") and any(RL.ALLOW_ALL_RE.search(o) for o in perm) and not any(RL.ALLOW_ALL_RE.search(o) for o in o1)
        and 'questions[tm]["allow_all"]' in src and 'questions[tm]["foot"]' in src,
        f"{shown!r} | {o1} {f1} {f2} {q1!r}")

# S6
empty_box()
gone = tmp / "scratch" / "foto.png"


class FakeResults:
    @staticmethod
    def find_path(p):
        return {"host": "local"} if p == str(gone) else None


RL.results_mod = lambda: FakeResults
r6 = RL.run_cmd({"id": "c-s6", "op": "file", "session": "alfa", "arg": str(gone), "by": "phone"})
T.check("S6 a file named by the conversation but gone from the disk: «non c'è più sul disco», not «unreadable», no note",
        r6 and r6["ok"] is False and "foto.png non c'è più sul disco" in r6["text"] and notes() == [], f"{r6 and r6['text']!r} | {notes()}")

# S7
empty_box()
SG = load("cm-suggest", "_s7")
d = SG.load()
SG.upsert(d, 1795680000, "followup", "k-s7", "Riprendi il rilascio", "riprendi il rilascio", 86400)
SG.save(d, 1795680000)
sid = next(x["id"] for x in SG.load()["items"] if x.get("key") == "k-s7")
r7 = RL.run_cmd({"id": "c-s7", "op": "suggestion", "arg": sid, "action": "accept", "by": "phone"})
back = [x for x in SG.load()["items"] if x.get("id") == sid]
hist = [h for h in SG.load()["history"] if h.get("key") == "k-s7"]
T.check("S7 a suggestion accepted with the master closed: back in the list (not lost), not counted as accepted, the message says to reopen the master, no note",
        r7 and r7["ok"] is False and "master non è aperta" in r7["text"] and "resta nella lista" in r7["text"] and len(back) == 1 and not hist and notes() == [],
        f"{r7 and r7['text']!r} {back} {hist} {notes()}")

# S8
env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
r8a = subprocess.run([str(T.SCRIPTS / "supervisor"), "needs-you", "serve una scelta"], capture_output=True, text=True, env=env, timeout=60)
r8b = subprocess.run([str(T.SCRIPTS / "supervisor"), "restart", "arm"], capture_output=True, text=True, env=env, timeout=60)
o8a, o8b = r8a.stdout + r8a.stderr, r8b.stdout + r8b.stderr
T.check("S8 outside tmux: needs-you says to ask in the chat and how to get the session into the app; restart says to reopen it with launch",
        r8a.returncode == 3 and "chiedi qui nella chat" in o8a and "supervisor launch" in o8a
        and r8b.returncode == 3 and "riaprila con `supervisor launch" in o8b,
        f"rc {r8a.returncode} {o8a[-300:]!r} | rc {r8b.returncode} {o8b[-300:]!r}")

T.rm(str(tmp))
T.finish()
