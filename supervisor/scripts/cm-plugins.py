#!/usr/bin/env python3
"""supervisor plugins — plugin e skill installati, per account, con l'ultima versione e l'aggiornamento (contratto 1.95,
29/10/2026).

Idea del maintainer del 28/10 alle 23:35: una sezione dell'app che elenchi plugin e skill installati con versione attuale e
ultima disponibile, con i tasti Aggiorna e Aggiorna tutto.

PLUGIN, per ogni account (config_dir): <config_dir>/plugins/installed_plugins.json dice cosa e' installato (id
nome@marketplace, scope, versione); l'ultima versione viene dal manifesto del marketplace
(<installLocation>/.claude-plugin/marketplace.json, da known_marketplaces.json): la `version` della voce, o il
plugin.json di una fonte locale (./cartella). Una fonte remota senza versione (url, git-subdir) → null: non si inventa.
update = l'ultima e' piu' nuova (numeri) o diversa (uno sha); null se l'ultima non si sa.

SKILL: <config_dir>/skills/*/SKILL.md — versione dal frontmatter (`version` o `metadata.version`); provenienza da
ORIGINE.txt (`<commit> <data> <repo> …`, lo scrive aggiorna-skill.py), «account» per la cartella synced (le skill
sincronizzate dall'account), «link:<destinazione>» per una cartella collegata, altrimenti «local». Le skill a se' non si
aggiornano dall'app.

CHECK: `claude plugin marketplace update` per account al piu' ogni plugins.check_hours ore (6), con nice; force (op
plugin_check) lo rifa' subito. CLAUDE_CONFIG_DIR solo per l'account fuori da ~/.claude (T68).

UPDATE: `claude plugin update ID --json -s SCOPE`, mai -y: -y accetterebbe un comando dichiarato dal marketplace, codice
d'altri; quando Claude Code lo chiede, l'esito dice di confermare al PC. Vale dalle sessioni nuove.

Uso: cm-plugins.py [--json] | check [--force] | update ACCOUNT ID | update-all ACCOUNT
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cm_config", HERE / "cm-config.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)
CFG = cm.load(warn=False)
ID_RE = re.compile(r"[A-Za-z0-9._-]{1,80}@[A-Za-z0-9._-]{1,80}")


def msg(key, **kw):
    return cm.msg(CFG, key, **kw)


def _json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def accounts():
    """[(nome, config_dir)] nell'ordine della config."""
    return [(n, Path(cm.expand(a["config_dir"]))) for n, a in CFG["accounts"].items()]


def _vkey(v):
    parts = re.findall(r"\d+", str(v))
    return [int(x) for x in parts] if parts and re.fullmatch(r"[\d.]+", str(v)) else None


def newer(latest, installed):
    """True se `latest` e' piu' nuova; per versioni non numeriche (uno sha) basta che sia diversa; None se non si sa."""
    if not latest or not installed:
        return None
    a, b = _vkey(latest), _vkey(installed)
    if a is not None and b is not None:
        return a > b
    return str(latest) != str(installed)


def latest_versions(conf):
    """{id: versione o None} dai manifesti dei marketplace dell'account."""
    out = {}
    for mname, m in (_json(conf / "plugins" / "known_marketplaces.json", {}) or {}).items():
        loc = Path(str(m.get("installLocation") or ""))
        man = _json(loc / ".claude-plugin" / "marketplace.json", {}) or {}
        for p in man.get("plugins") or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            v = p.get("version")
            src = p.get("source")
            if not v and isinstance(src, str) and src.startswith("./"):
                v = (_json(loc / src / ".claude-plugin" / "plugin.json", {}) or {}).get("version")
            out[f"{p['name']}@{mname}"] = str(v) if v else None
    return out


def _frontmatter(text):
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    top, meta, inside = {}, {}, False
    for line in m.group(1).splitlines():
        if re.match(r"^metadata:\s*$", line):
            inside = True
            continue
        k = re.match(r"^(\s*)([A-Za-z_]+):\s*(.*)$", line)
        if not k:
            continue
        if k.group(1) and inside:
            meta[k.group(2)] = k.group(3).strip().strip("\"'")
        else:
            inside = False
            top[k.group(2)] = k.group(3).strip().strip("\"'")
    return {"version": top.get("version") or meta.get("version") or None, "name": top.get("name")}


def skills(conf):
    """[{name, version, source}] dalle cartelle di <conf>/skills, synced compresa; ordinate per nome."""
    out = []
    root = conf / "skills"
    if not root.is_dir():
        return out
    dirs = [(d, None) for d in sorted(root.iterdir()) if d.is_dir() and d.name != "synced"]
    if (root / "synced").is_dir():
        dirs += [(d, "account") for d in sorted((root / "synced").iterdir()) if d.is_dir()]
    for d, src in dirs:
        sm = d / "SKILL.md"
        if not sm.is_file():
            continue
        try:
            fm = _frontmatter(sm.read_text(errors="replace")[:4000])
        except OSError:
            continue
        if src is None:
            org = d / "ORIGINE.txt"
            if d.is_symlink():
                src = "link:" + os.readlink(d)
            elif org.is_file():
                f = org.read_text(errors="replace").split()
                src = f"{f[2]}@{f[0][:8]}" if len(f) >= 3 else "local"
            else:
                src = "local"
        out.append({"name": fm.get("name") or d.name, "version": fm.get("version"), "source": src})
    return sorted(out, key=lambda s: s["name"])


def collect():
    """{checked_at, accounts: [{account, plugins: […], skills: […]}]} — solo letture di file."""
    out = []
    for name, conf in accounts():
        inst = (_json(conf / "plugins" / "installed_plugins.json", {}) or {}).get("plugins") or {}
        lat = latest_versions(conf)
        plugins = []
        for pid in sorted(inst):
            e = (inst[pid] or [{}])[0]
            pname, _, mk = pid.partition("@")
            v = str(e.get("version") or "") or None
            plugins.append({"id": pid, "name": pname, "marketplace": mk, "scope": e.get("scope") or "user", "installed": v,
                            "latest": lat.get(pid), "update": newer(lat.get(pid), v)})
        out.append({"account": name, "plugins": plugins, "skills": skills(conf)})
    return {"checked_at": int(_json(stamp_path(), {}).get("at") or 0) or None, "accounts": out}


def summary(st):
    """Il blocco piccolo di /state: quando si e' controllato e, per account, gli id con un aggiornamento."""
    return {"checked_at": st.get("checked_at"),
            "accounts": [{"account": a["account"], "updates": [p["id"] for p in a["plugins"] if p["update"]]} for a in st["accounts"]]}


def stamp_path():
    return Path(cm.expand(CFG.get("state_dir") or "~/.local/state/cc-supervisor")) / "plugins-check.json"


def _env(conf):
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if conf.resolve() != Path(os.path.expanduser("~/.claude")).resolve():
        env["CLAUDE_CONFIG_DIR"] = str(conf)
    return env


def _claude():
    return os.environ.get("CM_CLAUDE_BIN") or "claude"


def check(force=False):
    """`claude plugin marketplace update` per account, al piu' ogni plugins.check_hours ore. True se ha controllato."""
    hours = float((CFG.get("plugins") or {}).get("check_hours", 6))
    st = _json(stamp_path(), {})
    if not force and time.time() - float(st.get("at") or 0) < hours * 3600:
        return False
    for _, conf in accounts():
        try:
            subprocess.run(["nice", "-n", "19", _claude(), "plugin", "marketplace", "update"], env=_env(conf),
                           capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError):
            pass
    p = stamp_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": time.time()}))
    return True


def _find(account, pid):
    conf = dict(accounts()).get(account)
    if conf is None or not ID_RE.fullmatch(str(pid or "")):
        return None, None
    acc = next((a for a in collect()["accounts"] if a["account"] == account), None)
    return conf, next((p for p in (acc or {}).get("plugins") or [] if p["id"] == pid), None)


def update(account, pid):
    """(ok, testo) per un plugin di un account; mai -y."""
    conf, p = _find(account, pid)
    if not p:
        return False, msg("plugins.not_installed", id=str(pid)[:80], account=str(account)[:40])
    try:
        r = subprocess.run([_claude(), "plugin", "update", pid, "--json", "-s", p["scope"]], env=_env(conf),
                           capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return False, msg("plugins.update_failed", id=pid, why=str(e)[:120])
    last = (r.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        res = json.loads(last)
    except ValueError:
        res = {}
    if res.get("needsConfirmation") or res.get("shownCommand"):
        return False, msg("plugins.needs_pc", id=pid)
    if r.returncode != 0 or res.get("ok") is False:
        why = (res.get("error") or r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}").splitlines()[0][:160]
        return False, msg("plugins.update_failed", id=pid, why=why)
    return True, msg("plugins.updated", id=pid)


def update_all(account):
    """Ogni plugin di scope user con un aggiornamento; ok se tutti riescono. Una riga per plugin."""
    acc = next((a for a in collect()["accounts"] if a["account"] == account), None)
    if acc is None:
        return False, msg("plugins.not_installed", id="*", account=str(account)[:40])
    todo = [p["id"] for p in acc["plugins"] if p["update"] and p["scope"] == "user"]
    if not todo:
        return True, msg("plugins.nothing")
    res = [update(account, pid) for pid in todo]
    return all(ok for ok, _ in res), "\n".join(t for _, t in res)


def main(argv):
    if argv[:1] == ["check"]:
        print("checked" if check(force="--force" in argv) else "recent")
        return 0
    if argv[:1] == ["update"] and len(argv) == 3:
        ok, text = update(argv[1], argv[2])
        print(text)
        return 0 if ok else 1
    if argv[:1] == ["update-all"] and len(argv) == 2:
        ok, text = update_all(argv[1])
        print(text)
        return 0 if ok else 1
    st = collect()
    if "--json" in argv:
        print(json.dumps(st, ensure_ascii=False, indent=1))
        return 0
    for a in st["accounts"]:
        print(f"== {a['account']}")
        for p in a["plugins"]:
            mark = "  ← " + str(p["latest"]) if p["update"] else ""
            print(f"  {p['id']:<45} {p['installed'] or '?':<14}{mark}")
        print(f"  skill: {len(a['skills'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
