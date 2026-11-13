#!/usr/bin/env python3
"""Le prove di un compito per canale (13/11/2026, supervisor-prove-canali-13-11).

prova_add ritirava ogni prova del compito con una build diversa, anche quelle dell'altro canale: in un'app ogni compito ha
la web («web-0.2.N (sha)») e la pre-release Android («auto-AAAA-MM-GG-HHMM-sha»), e la prova n. 685 dell'app aveva
ritirato la n. 673 della web. Ora una build nuova ritira solo le prove dello stesso canale.

PC1 prova web, poi prova app sullo stesso compito: tutte e due aperte
PC2 una prova web nuova ritira solo la web vecchia; l'app resta
PC3 una pre-release nuova ritira solo la pre-release vecchia; la web resta
PC4 un tag vX.Y.Z nuovo ritira il tag vecchio, non la web ne' l'app
PC5 stessa build: stessa schermata e stessa azione sostituisce; un'altra azione resta accanto
PC6 un nome di cui non si sa il canale ritira tutto, come prima; un nome con due canali ritira tutti e due
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
proj = tmp / "proj"
proj.mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"), "accounts": {"personal": {"config_dir": str(tmp / ".claude")}},
                           "default_account": "personal", "workspace": {"root": str(tmp), "root_session_name": "master"},
                           "relay": {"dir": str(tmp / "relay")}}))
os.environ.update({"HOME": str(tmp), "CM_HOME": str(tmp), "CC_SUPERVISOR_CONFIG": str(cfg), "CM_TASKS_DB": str(tmp / "tasks.db")})
os.environ.pop("CLAUDECODE", None)
sp = importlib.util.spec_from_file_location("cm_tasks_pc", T.SCRIPTS / "cm-tasks.py")
TK = importlib.util.module_from_spec(sp)
sp.loader.exec_module(TK)

con = TK.connect()
_t = TK.now()
con.execute("INSERT INTO tasks (id, title, project, session, state, created_at, updated_at, contract) VALUES (?,?,?,?,?,?,?,?)",
            ("pc", "unit pc", str(proj), "sess", "running", _t, _t, "{}"))
con.commit()


def add(build, dove="home", fai="guarda la scheda"):
    return TK.prova_add(con, "pc", build, dove, fai, "la scheda c'e'", by="test")


def open_ns():
    return sorted(r["n"] for r in con.execute("SELECT n FROM prove WHERE task='pc' AND superseded_at IS NULL"))


WEB1, WEB2 = "web-0.2.2132 (a72884fc)", "web-0.2.2138 (b1c2d3e4)"
APP1, APP2 = "auto-2026-11-12-1700-e1a0d99a", "auto-2026-11-13-0048-dc88527b"
w1 = add(WEB1)
a1 = add(APP1)
T.check("PC1 a web test, then an app test on the same task: both stay open", open_ns() == [w1, a1], str(open_ns()))
w2 = add(WEB2)
T.check("PC2 a newer web test retires only the older web test; the app test stays", open_ns() == [a1, w2], str(open_ns()))
a2 = add(APP2)
T.check("PC3 a newer pre-release test retires only the older pre-release test; the web test stays", open_ns() == [w2, a2], str(open_ns()))
v1 = add("v0.8.160")
v2 = add("v0.8.161")
T.check("PC4 a newer vX.Y.Z tag retires the older tag, neither the web nor the app test", open_ns() == [w2, a2, v2] and v1 not in open_ns(), str(open_ns()))
w3 = add(WEB2)
w4 = add(WEB2, fai="scegli un'opzione")
T.check("PC5 same build: same screen and action replaces it, another action stays beside it", open_ns() == [a2, v2, w3, w4], str(open_ns()))
u1 = add("Pixel 11 Pro XL")
T.check("PC6 a build name of unknown channel retires every other build, as before", open_ns() == [u1], str(open_ns()))
w5 = add(WEB1)
a5 = add(APP1)
both = add("web-0.2.2140 · auto-2026-11-13-0900-0a1b2c3d")
T.check("PC6 a build naming two channels retires the tests of both", open_ns() == [both] and w5 and a5, str(open_ns()))
T.check("PC6 build_channels reads the channel from the name",
        TK.build_channels(str(proj), WEB1) == {"web"} and TK.build_channels(str(proj), APP1) == {"app"}
        and TK.build_channels(str(proj), "v0.8.161") == {"tag"} and TK.build_channels(str(proj), "Pixel 11") == set()
        and TK.build_channels(str(proj), "web-0.2.2132 · app-0.2.2129") == {"web", "app"}, "")
T.finish()
