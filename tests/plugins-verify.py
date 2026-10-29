#!/usr/bin/env python3
"""cm-plugins.py: plugin e skill installati, per account, con l'ultima versione e l'aggiornamento (plugins_state,
contratto 1.95, 29/10/2026).

Idea del maintainer del 28/10 alle 23:35: una sezione dell'app con plugin e skill installati, versione attuale e ultima,
e i tasti Aggiorna e Aggiorna tutto.

P1 plugins_state: per ogni account i plugin di installed_plugins.json con versione installata e ultima — dalla voce del
   manifesto del marketplace, o dal plugin.json di una fonte locale; ignota (null) per una fonte remota senza versione;
   update true solo se l'ultima è più nuova (numeri) o diversa (uno sha)
P2 plugins_state: le skill dell'account — versione dal frontmatter (version o metadata.version), provenienza da
   ORIGINE.txt (repo e commit), «account» per la cartella synced, un link per una cartella collegata
P3 plugins_state: il riassunto per /state (quando si è controllato, gli aggiornamenti per account) sta in poche
   centinaia di byte
P4 plugins_state: check aggiorna i marketplace al più ogni 6 ore per account (`claude plugin marketplace update`, a bassa
   priorità; CLAUDE_CONFIG_DIR solo per l'account fuori da ~/.claude); force lo rifà subito
P5 plugins_state: update chiama `claude plugin update ID --json -s SCOPE` col suo account, mai -y: un comando del
   marketplace da confermare diventa «da confermare al PC»; un id non installato è rifiutato senza chiamare niente;
   update_all aggiorna solo quelli con update true e di scope user
P6 (29/10) un plugin di scope project si aggiorna dalla sua cartella (projectPath); update_all copre tutti quelli che il
   riassunto conta, e quello senza cartella lo dice
P7 (29/10) col PATH del demone (/usr/bin:/bin) claude si trova in ~/.local/bin
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
A, B = home / ".claude", home / ".claude-work"
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"language": "it", "state_dir": str(tmp / "state"),
                           "accounts": {"personal": {"config_dir": str(A)}, "work": {"config_dir": str(B)}}, "default_account": "personal"}))
log = tmp / "claude.log"
fake = tmp / "claude"
fake.write_text(f"""#!/bin/sh
echo "CCD=${{CLAUDE_CONFIG_DIR:-}} $*" >> {log}
pwd >> {log}.cwd
case "$*" in
  *"plugin update needsconf@"*) echo '{{"ok":false,"needsConfirmation":true,"shownCommand":{{"sha256":"abc"}}}}'; exit 3 ;;
  *"plugin update"*) echo '{{"ok":true}}' ;;
esac
exit 0
""")
fake.chmod(0o755)
os.environ.update({"CC_SUPERVISOR_CONFIG": str(cfg), "HOME": str(home), "CM_HOME": str(home), "CM_CLAUDE_BIN": str(fake)})


def market(acc, name, loc, plugins, src="github"):
    mk = acc / "plugins"
    mk.mkdir(parents=True, exist_ok=True)
    km = mk / "known_marketplaces.json"
    d = json.loads(km.read_text()) if km.exists() else {}
    d[name] = {"source": {"source": src, "repo": f"owner/{name}"}, "installLocation": str(loc), "lastUpdated": "2026-10-26T09:00:00.000Z"}
    km.write_text(json.dumps(d))
    (loc / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (loc / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"name": name, "plugins": plugins}))


def installed(acc, entries):
    (acc / "plugins").mkdir(parents=True, exist_ok=True)
    (acc / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        k: [dict({"scope": e[1], "version": e[0], "installPath": str(acc / "plugins" / "cache" / k / e[0])}, **({"projectPath": e[2]} if len(e) > 2 else {}))]
        for k, e in entries.items()}}))


projA = tmp / "work" / "app-a"
projA.mkdir(parents=True)


m1 = tmp / "mk" / "tools"
market(A, "tools", m1, [{"name": "alpha", "version": "1.4.0", "source": "./alpha"}, {"name": "beta", "source": "./beta"},
                        {"name": "gamma", "source": {"source": "url", "url": "https://example.org/g.git"}},
                        {"name": "needsconf", "version": "2.0.0", "source": "./n"}, {"name": "proj", "version": "9.0.0", "source": "./p"},
                        {"name": "lost", "version": "0.2.0", "source": "./l"}])
(m1 / "beta" / ".claude-plugin").mkdir(parents=True)
(m1 / "beta" / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "beta", "version": "0.3.0"}))
installed(A, {"alpha@tools": ("1.2.9", "user"), "beta@tools": ("0.3.0", "user"), "gamma@tools": ("abc123def456", "user"),
              "needsconf@tools": ("1.0.0", "user"), "proj@tools": ("1.0.0", "project", str(projA)),
              "lost@tools": ("0.1.0", "project", str(tmp / "work" / "gone"))})
market(B, "tools", tmp / "mk" / "tools-b", [{"name": "alpha", "version": "1.4.0", "source": "./alpha"}])
installed(B, {"alpha@tools": ("1.4.0", "user")})
sk = A / "skills"
(sk / "impeccable").mkdir(parents=True)
(sk / "impeccable" / "SKILL.md").write_text("---\nname: impeccable\nmetadata:\n  version: 4.8.0\n---\nbody\n")
(sk / "animate").mkdir()
(sk / "animate" / "SKILL.md").write_text("---\nname: animate\nversion: 1.2\n---\n")
(sk / "animate" / "ORIGINE.txt").write_text("1a2b3c4d 2026-10-01 someone/motion-skills skills/animate\n")
(sk / "synced" / "abc_def").mkdir(parents=True)
(sk / "synced" / "abc_def" / "SKILL.md").write_text("---\nname: docx\n---\n")
(tmp / "elsewhere" / "linked").mkdir(parents=True)
(tmp / "elsewhere" / "linked" / "SKILL.md").write_text("---\nname: linked\n---\n")
(sk / "linked").symlink_to(tmp / "elsewhere" / "linked")
(sk / "aggiorna.log").write_text("not a skill")

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("cm_plugins", T.SCRIPTS / "cm-plugins.py")
PL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PL)

st = PL.collect()
acc = {a["account"]: a for a in st["accounts"]}
pa = {p["id"]: p for p in acc["personal"]["plugins"]}
T.check("P1 plugins_state: both accounts, the plugins of each with installed and latest",
        set(acc) == {"personal", "work"} and set(pa) == {"alpha@tools", "beta@tools", "gamma@tools", "lost@tools", "needsconf@tools", "proj@tools"}
        and [p["id"] for p in acc["work"]["plugins"]] == ["alpha@tools"], json.dumps(st)[:400])
T.check("P1 plugins_state: latest from the manifest entry (alpha 1.4.0) or the local plugin.json (beta 0.3.0); a remote source with no version → null",
        (pa["alpha@tools"]["installed"], pa["alpha@tools"]["latest"], pa["alpha@tools"]["update"]) == ("1.2.9", "1.4.0", True)
        and (pa["beta@tools"]["latest"], pa["beta@tools"]["update"]) == ("0.3.0", False)
        and (pa["gamma@tools"]["latest"], pa["gamma@tools"]["update"]) == (None, None), json.dumps(pa)[:500])
T.check("P1 plugins_state: each plugin {id, name, marketplace, scope, installed, latest, update}; work's alpha is up to date",
        all(list(p) == ["id", "name", "marketplace", "scope", "installed", "latest", "update"] for a in st["accounts"] for p in a["plugins"])
        and acc["work"]["plugins"][0]["update"] is False and pa["proj@tools"]["scope"] == "project", json.dumps(acc["work"]))
sks = {s["name"]: s for s in acc["personal"]["skills"]}
T.check("P2 plugins_state: skills — version from the frontmatter (metadata.version or version), source from ORIGINE.txt, «account» for synced, a link's target; files that are not skills left out",
        set(sks) == {"impeccable", "animate", "docx", "linked"}
        and sks["impeccable"]["version"] == "4.8.0" and sks["impeccable"]["source"] == "local"
        and sks["animate"]["version"] == "1.2" and sks["animate"]["source"] == "someone/motion-skills@1a2b3c4d"
        and sks["docx"]["source"] == "account" and sks["linked"]["source"].startswith("link:") and sks["linked"]["version"] is None
        and all(list(s) == ["name", "version", "source"] for s in acc["personal"]["skills"]), json.dumps(acc["personal"]["skills"]))
sm = PL.summary(st)
T.check("P3 plugins_state: the /state summary — checked_at and, per account, the ids with an update — in a few hundred bytes",
        sm["accounts"] == [{"account": "personal", "updates": ["alpha@tools", "lost@tools", "needsconf@tools", "proj@tools"]}, {"account": "work", "updates": []}]
        and "checked_at" in sm and len(json.dumps(sm)) < 400, json.dumps(sm))

log.write_text("")
r1 = PL.check()
r2 = PL.check()
lines = log.read_text().splitlines()
T.check("P4 plugins_state: check runs `claude plugin marketplace update` once per account (CLAUDE_CONFIG_DIR only for the one outside ~/.claude); again within 6 hours → nothing",
        sorted(lines) == sorted(["CCD= plugin marketplace update", f"CCD={B} plugin marketplace update"]) and r1 is True and r2 is False, str(lines))
r3 = PL.check(force=True)
T.check("P4 plugins_state: force checks again at once", r3 is True and len(log.read_text().splitlines()) == 4, log.read_text())

log.write_text("")
u1 = PL.update("personal", "alpha@tools")
u2 = PL.update("personal", "needsconf@tools")
u3 = PL.update("personal", "nope@tools")
u4 = PL.update("work", "alpha@tools; rm -rf /")
lines = log.read_text().splitlines()
T.check("P5 plugins_state: update runs `claude plugin update ID --json -s user` with the account, never -y; ok and «from the new sessions» in the text",
        u1[0] is True and "alpha@tools" in u1[1] and lines[0] == "CCD= plugin update alpha@tools --json -s user" and not any(" -y" in l or "--yes" in l for l in lines),
        str((u1, lines)))
T.check("P5 plugins_state: a marketplace command to confirm → not ok, «da confermare al PC»; an id not installed or malformed → refused, nothing run",
        u2[0] is False and "PC" in u2[1] and u3[0] is False and u4[0] is False and len(lines) == 2, str((u2, u3, u4, lines)))
log.write_text("")
Path(f"{log}.cwd").write_text("")
up = PL.update("personal", "proj@tools")
ul = PL.update("personal", "lost@tools")
lines, cwds = log.read_text().splitlines(), Path(f"{log}.cwd").read_text().splitlines()
T.check("P6 (29/10, «Aggiorna tutto» always the same two) a project-scope plugin is updated from its project folder with -s project; one whose folder is gone → refused in plain words, nothing run",
        up[0] is True and lines == ["CCD= plugin update proj@tools --json -s project"] and cwds == [str(projA)]
        and ul[0] is False and str(tmp / "work" / "gone") in ul[1], str((up, ul, lines, cwds)))
log.write_text("")
Path(f"{log}.cwd").write_text("")
ua = PL.update_all("personal")
lines, cwds = log.read_text().splitlines(), Path(f"{log}.cwd").read_text().splitlines()
upd = [p["id"] for a in PL.collect()["accounts"] if a["account"] == "personal" for p in a["plugins"] if p["update"]]
T.check("P6 update_all works on every plugin the summary counts as updatable: the user ones from here, the project one from its folder, the lost one said; one line per plugin",
        [l.split(" plugin update ")[1].split(" ")[0] for l in lines] == ["alpha@tools", "needsconf@tools", "proj@tools"] and cwds[2] == str(projA)
        and ua[0] is False and all(pid in ua[1] for pid in upd) and sorted(upd) == ["alpha@tools", "lost@tools", "needsconf@tools", "proj@tools"],
        str((ua, lines, cwds, upd)))
# P7 (29/10, «i tasti aggiorna danno errore»): il relay gira con PATH=/usr/bin:/bin e claude sta in ~/.local/bin:
# «[Errno 2] No such file or directory: 'claude'»
lb = home / ".local" / "bin"
lb.mkdir(parents=True, exist_ok=True)
(lb / "claude").write_text(fake.read_text())
(lb / "claude").chmod(0o755)
_env7 = {k: os.environ.get(k) for k in ("CM_CLAUDE_BIN", "PATH")}
os.environ.pop("CM_CLAUDE_BIN", None)
os.environ["PATH"] = "/usr/bin:/bin"
log.write_text("")
try:
    u7 = PL.update("personal", "alpha@tools")
finally:
    for k, v in _env7.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
T.check("P7 plugins_state: with the daemon's PATH (/usr/bin:/bin) claude is found in ~/.local/bin, the update runs",
        u7[0] is True and log.read_text().splitlines() == ["CCD= plugin update alpha@tools --json -s user"], str((u7, log.read_text())))
T.rm(str(tmp))
T.finish()
