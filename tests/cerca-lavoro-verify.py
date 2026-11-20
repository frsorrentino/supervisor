#!/usr/bin/env python3
"""Cerca nel lavoro passato (20/11/2026, supervisor-cerca-lavoro-19-11; idea 3 da claude-mem, mem-search). «Cosa abbiamo fatto
su X e quando»: un indice SQLite FTS5 incrementale su tutte le conversazioni dei progetti (prompt della persona, risposte finali
di ogni turno, righe «Esito:», commit), mai l'uscita degli strumenti, mai il testo <private>; la ricerca in tre passi.

CL1 `supervisor cerca TESTO`: l'indice dei risultati, una riga ciascuno con id, data, sessione, tipo e un estratto
CL2 `supervisor cerca --timeline ID`: le voci intorno, della stessa sessione, in ordine di tempo, con quella cercata segnata
CL3 `supervisor cerca --dettagli ID`: il testo intero con sessione, cartella e data
CL4 mai il testo <private>: non si trova, e i dettagli mostrano «[privato]»
CL5 incrementale: un secondo giro legge solo le righe nuove (nessun doppione); un turno che continua aggiorna la sua risposta
    finale invece di aggiungerne un'altra
CL6 i commit dei progetti: si trovano col tipo commit
CL7 mai l'uscita degli strumenti (tool_result) ne' le risposte intermedie di un turno
CL8 maiuscole e accenti non contano («perche» trova «perché»)
CL9 la skill insegna i tre passi (cerca, --timeline, --dettagli)
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

SECRET = "codice-segreto-55120"
tmp = Path(T.tmpdir())
home = tmp / "home"
ws = home / "ws"
alfa = ws / "personali" / "alfa"
alfa.mkdir(parents=True)
state = tmp / "state"
state.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(state), "default_account": "personale",
                           "workspace": {"root": str(ws), "root_session_name": "master"},
                           "accounts": {"personale": {"config_dir": str(home / ".claude")}},
                           "relay": {"enabled": False, "dir": str(tmp / "relay")}}))
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR")}
ENV.update({"HOME": str(home), "CM_HOME": str(home), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_PROC_SCAN_PIDS": "",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
CLI = str(T.SCRIPTS / "supervisor")
proj_dir = home / ".claude" / "projects" / str(alfa).replace("/", "-")
proj_dir.mkdir(parents=True)
tr = proj_dir / "S-ALFA-1.jsonl"
T0 = dt.datetime(2026, 11, 18, 10, 0, tzinfo=dt.timezone.utc)
N = [0]


def ts(minutes):
    return (T0 + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def row(kind, content, minutes, **kw):
    N[0] += 1
    d = {"type": kind, "uuid": f"{kind[0]}{N[0]}", "timestamp": ts(minutes), "sessionId": "S-ALFA-1", "cwd": str(alfa),
         "message": {"role": kind, "content": content}}
    if kind == "user":
        d["origin"] = {"kind": "human"}
    d.update(kw)
    with open(tr, "a", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return d["uuid"]


def txt(t):
    return [{"type": "text", "text": t}]


row("user", "prepara il listino di dicembre per il negozio", 0)
row("assistant", txt("Leggo i prezzi."), 1)
row("assistant", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat prezzi.csv"}}], 1)
row("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "zebrafantasma 12 euro"}], 2, toolUseResult={"ok": True})
row("assistant", txt("Listino di dicembre pronto in docs/listino.md.\n\nEsito: listino di dicembre pronto, 42 prodotti"), 3)
row("user", f"il codice del fornitore è <private>{SECRET}</private>, usalo per l'ordine", 10)
row("assistant", txt("Ordine preparato col codice del fornitore."), 11)
row("user", "perché il totale non torna?", 20)
row("assistant", txt("Il totale non tornava per l'IVA al 22% applicata due volte: corretto."), 21)

subprocess.run(["git", "init", "-q", str(alfa)], check=True, env=ENV)
(alfa / "listino.md").write_text("x\n")
subprocess.run(["git", "-C", str(alfa), "add", "."], check=True, env=ENV)
subprocess.run(["git", "-C", str(alfa), "commit", "-q", "-m", "listino: prezzi di dicembre con IVA corretta"], check=True, env=ENV)


def cerca(*a):
    return subprocess.run([CLI, "cerca", *a], capture_output=True, text=True, env=ENV, timeout=180)


r = cerca("listino dicembre")
ids = re.findall(r"^#(\d+)\b", r.stdout, re.M)
T.check("CL1 `supervisor cerca TESTO`: one line per result with id, date, session, kind and a snippet",
        r.returncode == 0 and len(ids) >= 2 and re.search(r"^#\d+ 2026-11-18 \d\d:\d\d · alfa · (prompt|risposta|esito)", r.stdout, re.M) is not None
        and "listino" in r.stdout.lower(), r.stdout[-600:] + r.stderr[-400:])

j = json.loads(cerca("totale", "--json").stdout or "[]")
tid = j[0]["id"] if j else None
tl = cerca("--timeline", str(tid)) if tid else None
lines = (tl.stdout if tl else "").splitlines()
marked = [l for l in lines if l.startswith("→") or l.startswith(">")]
order = [l for l in lines if re.match(r"^[ →>]*#\d+", l)]
T.check("CL2 --timeline ID: the entries around it, same session, in time order, the one asked marked",
        bool(tl) and tl.returncode == 0 and len(order) >= 4 and len(marked) == 1 and f"#{tid}" in marked[0]
        and lines.index(marked[0]) > 0, (tl.stdout if tl else "") + (tl.stderr if tl else ""))

full = [x for x in json.loads(cerca("IVA", "--json").stdout or "[]") if x.get("kind") == "risposta"]
dd = cerca("--dettagli", str(full[0]["id"])) if full else None
T.check("CL3 --dettagli ID: the whole text with session, folder and date",
        bool(dd) and dd.returncode == 0 and "Il totale non tornava per l'IVA al 22% applicata due volte: corretto." in dd.stdout
        and str(alfa) in dd.stdout and "2026-11-18" in dd.stdout, (dd.stdout if dd else "") + (dd.stderr if dd else ""))

s1 = cerca(SECRET)
pv = [x for x in json.loads(cerca("fornitore", "--json").stdout or "[]") if x.get("kind") == "prompt"]
dp = cerca("--dettagli", str(pv[0]["id"])) if pv else None
T.check("CL4 never the <private> text: it is not found, and the details show «[privato]»",
        not re.search(r"^#\d+", s1.stdout, re.M) and bool(dp) and SECRET not in dp.stdout and "[privato]" in dp.stdout,
        s1.stdout[-300:] + (dp.stdout if dp else ""))

n_before = len(json.loads(cerca("listino", "--json", "--limit", "100").stdout or "[]"))
row("user", "aggiungi il listino di gennaio", 30)
row("assistant", txt("Parto dal listino di dicembre."), 31)
mid = json.loads(cerca("listino", "--json", "--limit", "100").stdout or "[]")   # il turno e' ancora aperto: l'indice lo vede a meta'
row("assistant", txt("Listino di gennaio pronto.\n\nEsito: listino di gennaio pronto"), 32)
after = json.loads(cerca("listino", "--json", "--limit", "100").stdout or "[]")
gen = [x for x in after if "gennaio" in x.get("snippet", "")]
replies_gen = [x for x in gen if x.get("kind") == "risposta"]
T.check("CL5 incremental: only the new lines (no duplicates); a continued turn updates its final reply instead of adding one",
        len(after) > n_before and len({x["id"] for x in after}) == len(after) and len(replies_gen) == 1
        and "Parto dal listino" not in json.dumps(after, ensure_ascii=False), json.dumps(after, ensure_ascii=False)[:900])

c = json.loads(cerca("IVA corretta", "--json").stdout or "[]")
T.check("CL6 the projects' commits: found with kind commit", any(x.get("kind") == "commit" and "listino: prezzi di dicembre" in x.get("snippet", "") for x in c),
        json.dumps(c, ensure_ascii=False)[:500])

z = cerca("zebrafantasma")
lp = cerca("Leggo i prezzi")
T.check("CL7 never the tools' output nor a turn's intermediate replies", not re.search(r"^#\d+", z.stdout, re.M) and not re.search(r"^#\d+", lp.stdout, re.M),
        z.stdout + lp.stdout)

p = cerca("PERCHE")
T.check("CL8 case and accents do not matter («PERCHE» finds «perché»)", bool(re.search(r"^#\d+ .*prompt", p.stdout, re.M)), p.stdout)

sk = T.ROOT / "supervisor" / "skills" / "cerca" / "SKILL.md"
st = sk.read_text(encoding="utf-8") if sk.is_file() else ""
T.check("CL9 the skill teaches the three steps (cerca, --timeline, --dettagli)",
        st.startswith("---") and "supervisor cerca" in st and "--timeline" in st and "--dettagli" in st, st[:300])
T.finish()
