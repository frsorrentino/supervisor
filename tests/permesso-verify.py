#!/usr/bin/env python3
"""permesso_esplicito (02/11/2026, segnalazione del maintainer): una domanda di permesso dice cosa si approva. Il dialogo di
tests/fixtures/permission-dialog-real.txt e' lo schermo vero di Claude Code 2.1.316, con il percorso sostituito."""
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "s"), "accounts": {"personal": {"config_dir": str(tmp / "c")}}, "default_account": "personal",
                           "workspace": {"root": str(tmp)}, "relay": {"dir": str(tmp / "r")}}))
(tmp / "r").mkdir()
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(tmp), "CM_HOME": str(tmp)})


def load(n):
    sp = importlib.util.spec_from_file_location(n.replace("-", "_"), T.SCRIPTS / f"{n}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


A, RL = load("cm-answer"), load("cm-relay")
S = RL.S
real = (T.ROOT / "tests/fixtures/permission-dialog-real.txt").read_text()
d = A.parse(real)
T.check("permesso_esplicito the real dialog's question stays the one line, the box does not leak into it", d and d[1] == "Do you want to proceed?", str(d and d[1]))
T.check("permesso_esplicito still four options on the real dialog", d and len(d[2]) == 4, str(d and d[2]))
b = A.dialog_body(real)
T.check("permesso_esplicito real dialog: the description", b and b["description"] == "Crea il file di prova", str(b))
T.check("permesso_esplicito real dialog: the command, whole across the wrapped lines", b and b["command"].startswith("touch /tmp/demo-work/") and b["command"].endswith("really/probe-x"), str(b))
T.check("permesso_esplicito real dialog: title and tip stay out", b and not any("Bash command" in x or "Tip:" in x for x in b["lines"]), str(b))
agent = (" Tool use\n\n   Agent(Review Task 5)\n   │ Launch a new agent\n\n │ Hook PreToolUse:Agent requires confirmation for this tool:\n"
         " │ cost checkpoint.\n\n Do you want to proceed?\n ❯ 1. Yes\n   2. No\n\n Esc to cancel · Tab to amend\n")
ba = A.dialog_body(agent)
T.check("permesso_esplicito Agent: command and description, the hook only in the body", ba and ba["command"] == "Agent(Review Task 5)" and ba["description"] == "Launch a new agent" and any("Hook" in x for x in ba["lines"]), str(ba))
T.check("permesso_esplicito a «☐» question has no body", A.dialog_body(" ☐ Scelta\n\n Quale?\n ❯ 1. A\n   2. B\n\n Enter to select · Esc to cancel\n") is None, "")

RL.run_cm = lambda *a, **k: (0, json.dumps(b))
p = RL.permission_body("x", "Bash", {})
T.check("permesso_esplicito from the screen: description first, then the command", p["what"].startswith("Crea il file di prova: touch "), str(p))
p = RL.permission_body("x", "Bash", {"command": "git push origin main", "description": "Publish the docs"})
T.check("permesso_esplicito the hook's input wins: it has the whole command", p["what"] == "Publish the docs: git push origin main", str(p))
p = RL.permission_body("x", "Workflow", {"description": "Review the release", "script": "export const meta"})
T.check("permesso_esplicito Workflow: description and script", p["what"].startswith("Review the release: export"), str(p))
T.check("permesso_esplicito AskUserQuestion has none", RL.permission_body("x", "AskUserQuestion", {"question": "q"}) == {}, "")
RL.run_cm = lambda *a, **k: (1, "")
T.check("permesso_esplicito nothing known: nothing invented", RL.permission_body("x", "Bash", {}) == {}, "")


def src(what="Publish: git push https://u:TOKEN=abc123secret@h", body="Publish\nTOKEN=abc123secret"):
    return {"host": "h", "root": str(tmp), "prefixes": [], "ledger": [],
            "rows": [{"name": "atlas", "tmux": "atlas", "pid": 1, "status": "busy", "waiting": True, "session_id": "S", "account": "personal", "cwd": str(tmp)}],
            "questions": {"atlas": {"tool": "Bash", "text": "Do you want to proceed?", "options": ["Yes", "No"], "asked_at": 100, "what": what, "body": body}}}


st = S.build_state(src(), 200)
q = next((x["question"] for x in st["sessions"] if x.get("question")), None)
T.check("permesso_esplicito /state: what and body, past the preview mask", q and q["what"].startswith("Publish: git push") and "abc123secret" not in q["what"] + q["body"], str(q))
T.check("permesso_esplicito the attention entry shows what is approved", st["attention"][0]["title"] == q["what"], str(st.get("attention")))
ev, _ = S.events_between({"sessions": []}, {"sessions": [dict(st["sessions"][0])], "quota": {}}, 200)
qe = [e for e in ev if e["kind"] == "question"]
T.check("permesso_esplicito the notification's text is what is approved, not the question", qe and qe[0]["body"] == q["what"] and "proceed" not in qe[0]["body"], str(qe))
_big = dict(st, pad="x" * 9000)
_cap = json.loads(json.dumps(_big))
S.fit_state(_cap)
T.check("permesso_esplicito over the cap: body dropped by fit_state, what kept", "body" not in _cap["sessions"][0]["question"] and _cap["sessions"][0]["question"].get("what"), str(_cap["sessions"][0]["question"]))
st2 = S.build_state(dict(src(), questions={"atlas": {"tool": "AskUserQuestion", "text": "Quale?", "options": ["A", "B"], "asked_at": 100}}), 200)
T.check("permesso_esplicito a plain question gains no field", "what" not in st2["sessions"][0]["question"] and "body" not in st2["sessions"][0]["question"], str(st2["sessions"][0]["question"]))
_fx = json.loads((T.ROOT / "tests/fixtures/relay/question-permission.json").read_text())
T.check("permesso_esplicito the contract's sample has what the code sends", {"what", "body"} <= set(_fx["session_question"]) and _fx["event"]["body"] == _fx["session_question"]["what"], "")
T.finish()
