#!/usr/bin/env python3
"""Il testo di una prova riportata su una build nuova (13/11/2026, supervisor-prove-riporto-testo-13-11).

La regola 1 di tidy riporta una prova sulla build piu' nuova e aggiornava la versione solo in fai e atteso, e solo quella
della build appena lasciata. Il dove restava com'era: sulla pre-release 0.2.2147 una prova diceva ancora «app 0.2.2143»,
un'altra «app Android 0.2.2134», rimasta indietro di piu' riporti. Ora dove, fai e atteso nominano la versione nuova dove
la danno al canale della prova; quella data all'altro canale («Web gia' alla 0.2.N» in una prova dell'app) resta.

RT1 da tag a tag (auto-… → auto-…): «app 0.2.1» nel dove (indietro di due riporti) diventa la nuova, «Installa la 0.2.3» e
    «app Android 0.2.3» pure; «Web gia' alla 0.2.3» resta
RT2 da web a web: «web 0.2.4» nel dove e nel fai diventa la nuova; «app 0.2.3» resta
RT3 un testo che non nomina versioni resta com'e'
RT4 una prova gia' sulla build piu' nuova col testo rimasto a una versione di prima del suo canale si allinea sul posto
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"personal": {"config_dir": str(tmp / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"},
                           "relay": {"dir": str(tmp / "relay")}}))
os.environ.update({"HOME": str(tmp), "CM_HOME": str(tmp), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "tasks.db")})
os.environ.pop("CLAUDECODE", None)
sp = importlib.util.spec_from_file_location("cm_tasks_rt", T.SCRIPTS / "cm-tasks.py")
TK = importlib.util.module_from_spec(sp)
sp.loader.exec_module(TK)

# sei commit: N = numero di commit, come la web e la build Android di un'app; auto-a sul 3, auto-b sul 6
repo = tmp / "app"
repo.mkdir()
GENV = dict(os.environ, GIT_AUTHOR_NAME="maintainer", GIT_AUTHOR_EMAIL="m@example.com", GIT_COMMITTER_NAME="maintainer",
            GIT_COMMITTER_EMAIL="m@example.com")


def git(*a, when=None):
    e = dict(GENV, **({"GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when} if when else {}))
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=e, check=True).stdout


git("init", "-q", "-b", "main")
sha = {}
for i in range(1, 7):
    git("commit", "-q", "--allow-empty", "-m", f"c{i}", when=f"2026-11-0{i}T10:00:00")
    sha[i] = git("rev-parse", "HEAD").strip()
git("tag", f"auto-a-{sha[3][:8]}", sha[3])
git("tag", f"auto-b-{sha[6][:8]}", sha[6])
AA, AB = f"auto-a-{sha[3][:8]}", f"auto-b-{sha[6][:8]}"
W4, W5 = f"web-0.2.4 ({sha[4][:8]})", f"web-0.2.5 ({sha[5][:8]})"

con = TK.connect()
_t = TK.now()
for tid in ("rt-app", "rt-web", "rt-web2", "rt-plain", "rt-now"):
    con.execute("INSERT INTO tasks (id, title, project, session, state, created_at, updated_at, contract) VALUES (?,?,?,?,?,?,?,?)",
                (tid, f"unit {tid}", str(repo), "sess", "running", _t, _t, "{}"))
con.commit()

TK.prova_add(con, "rt-app", AA, "Telefono, app 0.2.1: Home", "Installa la 0.2.3 dalla Home. Web già alla 0.2.3",
             "app Android 0.2.3 mostra la scheda", by="test")
TK.prova_add(con, "rt-web", W4, "web 0.2.4: Home", "Ricarica la web 0.2.4; app 0.2.3 resta", "si vede", by="test")
TK.prova_add(con, "rt-web2", W5, "Home", "Ricarica", "si vede", by="test")
TK.prova_add(con, "rt-plain", AA, "Home", "Si apre la scheda", "si apre", by="test")
n_now = TK.prova_add(con, "rt-now", AB, "app 0.2.2, telefono", "Installa la 0.2.6. Web già alla 0.2.4", "si apre", by="test")
TK.tidy(con)


def live(tid):
    return [tuple(r) for r in con.execute("SELECT build, dove, fai, atteso FROM prove WHERE task=? AND superseded_at IS NULL", (tid,))]


T.check("RT1 tag to tag: the test moves to the newest pre-release; «app 0.2.1» in where (two carries behind), «Installa la 0.2.3» and «app Android 0.2.3» name 0.2.6; «Web già alla 0.2.3» stays",
        live("rt-app") == [(AB, "Telefono, app 0.2.6: Home", "Installa la 0.2.6 dalla Home. Web già alla 0.2.3", "app Android 0.2.6 mostra la scheda")],
        str(live("rt-app")))
T.check("RT2 web to web: the test moves to the newest web; «web 0.2.4» in where and in what to do names 0.2.5; «app 0.2.3» stays",
        live("rt-web") == [(W5, "web 0.2.5: Home", "Ricarica la web 0.2.5; app 0.2.3 resta", "si vede")], str(live("rt-web")))
T.check("RT3 a text naming no version is carried unchanged", live("rt-plain") == [(AB, "Home", "Si apre la scheda", "si apre")], str(live("rt-plain")))
T.check("RT4 a test already on the newest pre-release whose text was left at an older version of its channel is aligned in place, same test number; the other channel stays",
        [tuple(r) for r in con.execute("SELECT n, build, dove, fai FROM prove WHERE task='rt-now' AND superseded_at IS NULL")]
        == [(n_now, AB, "app 0.2.6, telefono", "Installa la 0.2.6. Web già alla 0.2.4")], str(live("rt-now")))
T.finish()
