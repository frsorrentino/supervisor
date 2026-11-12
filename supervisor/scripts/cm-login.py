"""cm-login — la scadenza del login degli account (04/11/2026, contratto 1.117).

Claude Code 2.1.318: `claude auth status` dice loginExpiresAt. `refresh()` la legge per ogni account (CLAUDE_CONFIG_DIR
della sua config_dir) in <state_dir>/login-expiry.json; `due()` dice quelli che scadono entro 7 giorni: Tocca a te ne
fa una voce sulla master, che rifa' il login (la persona detta il codice di 6 cifre). Nessuna chiamata a un modello:
solo `claude auth status`. Lo lancia la guardia (`task login-check`).
"""
import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_CFG = []


def _cfg():
    if not _CFG:
        try:
            spec = importlib.util.spec_from_file_location("cm_config_login", HERE / "cm-config.py")
            cm = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cm)
            _CFG.append((cm, cm.load(warn=False)))
        except Exception:   # noqa: BLE001 — senza config: niente account
            _CFG.append((None, {}))
    return _CFG[0]


def M(key, **kw):
    cm, cfg = _cfg()
    return cm.msg(cfg, key, **kw) if cm else key


def now():
    return int(time.time())


WARN_S = 7 * 86400
FILE = "login-expiry.json"


def _path():
    cm_, cfg = _cfg()
    return Path(cm_.expand(cfg["state_dir"])) / FILE if cm_ and cfg.get("state_dir") else None


def _iso_epoch(v):
    return int(dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())


def claude_bin(cm_=None):
    """Claude Code anche dal cron (12/11/2026, supervisor-login-scaduto-fermo-11-11): col PATH ridotto del cron (/usr/bin:/bin)
    `claude` non si trovava, ogni lettura falliva e teneva quella vecchia, e un «login scaduto» restava giorni dopo il
    rinnovo. Prima CM_CLAUDE_BIN e il PATH, poi dove lo mette l'installatore e Homebrew."""
    home = cm_.home() if cm_ else Path.home()
    for c in (os.environ.get("CM_CLAUDE_BIN"), shutil.which("claude"), str(home / ".local" / "bin" / "claude"),
              "/usr/local/bin/claude", "/opt/homebrew/bin/claude"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return "claude"


def refresh():
    """1.117 (04/11/2026, Claude Code 2.1.318: `claude auth status` dice loginExpiresAt): la scadenza del login di ogni
    account in <state_dir>/login-expiry.json, {at, accounts: {nome: {label, logged_in, expires_at}}}. Un account che non
    risponde (claude assente, JSON rotto, tempo scaduto) tiene la lettura di prima: mai una voce falsa per un errore."""
    cm_, cfg = _cfg()
    p = _path()
    if p is None:
        return {}
    try:
        old = json.loads(p.read_text()).get("accounts") or {}
    except (OSError, ValueError, AttributeError):
        old = {}
    bin_ = claude_bin(cm_)
    out = {}
    for name, acc in (cfg.get("accounts") or {}).items():
        env = dict(os.environ, CLAUDE_CONFIG_DIR=cm_.expand(acc.get("config_dir") or "~/.claude"))
        try:
            d = json.loads(subprocess.run([bin_, "auth", "status"], capture_output=True, text=True, timeout=30, env=env).stdout)
            exp = d.get("loginExpiresAt")
            out[name] = {"label": acc.get("label") or name, "logged_in": bool(d.get("loggedIn")), "expires_at": _iso_epoch(exp) if exp else None}
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
            if name in old:
                out[name] = old[name]
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": now(), "accounts": out}, ensure_ascii=False))
    os.replace(tmp, p)
    return out


def due(now_=None):
    """1.117: gli account il cui login scade entro WARN_S (o e' gia' scaduto, o non c'e'): [{account, label,
    expires_at, expired}], dalla lettura di refresh. Un account senza scadenza nota e collegato non c'e'."""
    now_ = int(now_ or now())
    p = _path()
    try:
        accs = json.loads(p.read_text()).get("accounts") or {} if p else {}
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for name, a in accs.items():
        exp = a.get("expires_at")
        if not a.get("logged_in") or (exp is not None and exp - now_ <= WARN_S):
            out.append({"account": name, "label": a.get("label") or name, "expires_at": exp, "expired": not a.get("logged_in") or (exp is not None and exp <= now_)})
    return sorted(out, key=lambda x: (x["expires_at"] or 0, x["account"]))


def text(x):
    if not x.get("expires_at"):
        return M("tasks.login_none", account=x["label"])
    w = dt.datetime.fromtimestamp(x["expires_at"])
    return M("tasks.login_expired" if x["expired"] else "tasks.login_due", account=x["label"], date=w.strftime("%d/%m"), time=w.strftime("%H:%M"))
