#!/usr/bin/env python3
"""La pagella unisce bug, bugfix e fix e dice gli effort (28/11/2026, supervisor-pagella-effort-28-11, contratto 1.167).

La persona dal telefono: «Unisci bug, bugfix e correzioni e poi aggiungi l'effort alla pagella». bug, bugfix e fix sono
lo stesso lavoro con tre etichette (il tipo del contratto); al conto diventano «fix», che l'app chiama «correzioni», anche
per le chiusure di prima e senza riscriverle. Ogni riga dice gli effort coi casi e, dove un effort ha almeno 10 casi,
difetti e percentuale di quell'effort. Registro temporaneo, chiusure scritte direttamente con ore fisse.

PE1 bug, bugfix e fix di un modello sono una riga sola «fix»; nel registro i tipi restano come sono stati scritti
PE2 by_effort: un elemento per effort, nell'ordine low, medium, high, xhigh, max; difetti e percentuale solo da 10 casi;
    la somma dei casi e' quella della riga; efforts (i conteggi) resta com'era
PE3 un effort non noto e' null, in fondo, mai dedotto; una riga sotto i 10 casi ha i suoi effort, senza numeri
PE4 il consiglio confronta le righe unite: prima nessuna aveva 10 casi
PE5 il contratto: details/relay.md ha la 1.167 con by_effort; la fixture conti-pagella-effort.json e' cio' che la pagella
    da' con questi dati; l'op conti del relay porta by_effort
"""
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
(home / ".claude").mkdir(parents=True)
root = tmp / "ws"
(root / "acme").mkdir(parents=True)
(tmp / "r").mkdir()
cfg = tmp / "c.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "s"), "accounts": {"personal": {"config_dir": str(home / ".claude")}}, "default_account": "personal",
                           "workspace": {"root": str(root), "root_session_name": "master", "project_dirs": ["."]}, "relay": {"dir": str(tmp / "r")}}))
DB = tmp / "t.db"
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(DB), "CM_RELAY_CM": "/bin/false"})
os.environ.update(ENV)
FIX = T.ROOT / "tests" / "fixtures" / "relay" / "conti-pagella-effort.json"


def load(name):
    sp = importlib.util.spec_from_file_location(name.replace("-", "_"), T.SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


TK = load("cm-tasks")
con = TK.connect()
AT = 1795000000   # ore fisse: la fixture e' cio' che la pagella da' con questi dati
_n = [0]


def closed(model, kind, effort, defect=False, state="done"):
    _n[0] += 1
    tid = f"t{_n[0]:03d}"
    con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)", (tid, None, str(root / "acme"), "s", f"compito {tid}", "open", state, "{}", AT, AT))
    con.execute("INSERT INTO closures (task, at, model, effort, kind) VALUES (?,?,?,?,?)", (tid, AT + _n[0], model, effort, kind))
    if defect:
        con.execute("INSERT INTO defects (task, defect_of, at) VALUES (?,?,?)", (f"fx-{tid}", tid, AT + 999))
    return tid


# claude-opus-5-5: 14 correzioni con tre etichette (12 high, 2 con un difetto; 2 medium), 5 funzioni
for i in range(12):
    closed("claude-opus-5-5", ("bug", "bugfix", "fix")[i % 3], "high", defect=i in (0, 1))
closed("claude-opus-5-5", "fix", "medium")
closed("claude-opus-5-5", "bugfix", "medium")
for i in range(5):
    closed("claude-opus-5-5", "feature", "low")
# claude-sonnet-5-5: 20 «bug» (17 medium, 3 senza effort), 12 con un difetto
for i in range(20):
    closed("claude-sonnet-5-5", "bug", "medium" if i < 17 else None, defect=i < 12)
closed(None, "fix", None)   # modello non noto: solo in unknown_model
con.commit()

pg = TK.pagella(con)
rows = {(r["model"], r["kind"]): r for r in pg["rows"]}
kinds = sorted({r["kind"] for r in pg["rows"]})
raw = sorted({r[0] for r in con.execute("SELECT DISTINCT kind FROM closures")})
op = rows.get(("claude-opus-5-5", "fix")) or {}
T.check("PE1 bug, bugfix and fix of one model are one row «fix»; the registry keeps the types as they were written",
        kinds == ["feature", "fix"] and op.get("n") == 14 and op.get("defects") == 2 and op.get("rate") == 0.143 and raw == ["bug", "bugfix", "feature", "fix"],
        f"kinds={kinds} op={op} raw={raw}")

be = op.get("by_effort")
T.check("PE2 by_effort: one item per effort, in the order low, medium, high, xhigh, max; defects and rate only from 10 cases; the cases add up to the row's; efforts (the counts) is unchanged",
        be == [{"effort": "medium", "n": 2, "enough": False, "defects": None, "rate": None},
               {"effort": "high", "n": 12, "enough": True, "defects": 2, "rate": 0.167}]
        and op.get("efforts") == {"high": 12, "medium": 2},
        f"by_effort={be} efforts={op.get('efforts')}")

so = rows.get(("claude-sonnet-5-5", "fix")) or {}
fe = rows.get(("claude-opus-5-5", "feature")) or {}
T.check("PE3 an effort not known is null, last, never deduced; a row under 10 cases has its efforts, with no numbers",
        so.get("by_effort") == [{"effort": "medium", "n": 17, "enough": True, "defects": 12, "rate": 0.706},
                                {"effort": None, "n": 3, "enough": False, "defects": None, "rate": None}]
        and so.get("efforts") == {"medium": 17}
        and fe.get("enough") is False and fe.get("by_effort") == [{"effort": "low", "n": 5, "enough": False, "defects": None, "rate": None}]
        and pg["unknown_model"] == 1,
        f"sonnet={so} feature={fe} unknown={pg['unknown_model']}")

T.check("PE4 the advice compares the merged rows: before, none of them had 10 cases",
        [(a["kind"], a["better"], a["worse"]) for a in pg["advice"]] == [("fix", "claude-opus-5-5", "claude-sonnet-5-5")], str(pg["advice"]))

doc = (T.ROOT / "details" / "relay.md").read_text(encoding="utf-8")
c167 = doc.split("Contract 1.167", 1)[1].split("\nContract 1.", 1)[0] if "Contract 1.167" in doc else ""
fx = json.loads(FIX.read_text(encoding="utf-8")) if FIX.exists() else None
RL = load("cm-relay")
ok, txt = RL.execute({"id": "a", "op": "conti", "arg": "2026-11"})[:2]
rv = json.loads(txt).get("pagella") if ok else None
T.check("PE5 the contract: details/relay.md has 1.167 with by_effort; the fixture conti-pagella-effort.json is what the report card gives with these data; the op conti carries by_effort",
        "by_effort" in c167 and "bugfix" in c167 and fx == {"pagella": pg} and rv == pg,
        f"c167={bool(c167)} fixture={'equal' if fx == {'pagella': pg} else json.dumps({'pagella': pg}, ensure_ascii=False)} relay={ok} {str(rv)[:200]}")

con.close()
T.rm(tmp)
T.finish()
