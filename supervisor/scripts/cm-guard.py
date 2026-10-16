#!/usr/bin/env python3
"""supervisor guard — guardia della quota: avvisa prima del muro, riprende da sola dopo il reset.

  supervisor guard run                 un giro (dal cron ogni guard.cron_minutes)
  supervisor guard install | uninstall | status

Ogni giro legge la quota dei due account dai file di fable-director (`quota.source`, gli stessi di
`supervisor quota`): finestra delle cinque ore, o settimanale se è l'unica. Sopra
`guard.warn_pct` manda UN avviso su Telegram per finestra (stesso bot del plugin), con l'ora del
reset. Poi ricorda l'ora del reset e, quando arriva: alle sessioni vive dell'account che nel
frattempo hanno avuto un turno fallito (`stop-failure` nel ledger: il muro della quota finisce lì)
manda «riprendi da dove eri» attraverso l'inbox (`supervisor talk`), e se la coda notturna non
è vuota rilancia `night run --send` (quella delle 02:00 era rimasta ferma per la quota). Stato in
`<state_dir>/guard.json`: niente doppi avvisi, niente doppie riprese.

Prove: CM_GUARD_NOW (epoch finto), CM_GUARD_CM (dispatcher finto), CM_CRONTAB_CMD, quota.source.
"""
import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
G = CFG["guard"]
CM_BIN = os.environ.get("CM_GUARD_CM") or str(HERE / "supervisor")


def now():
    return float(os.environ.get("CM_GUARD_NOW") or time.time())


def state_path():
    return Path(cm.expand(CFG["state_dir"])) / "guard.json"


def load_state():
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def save_state(st):
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(st, ensure_ascii=False, indent=1))


def log(line):
    p = Path(cm.expand(CFG["state_dir"])) / "guard.log"
    with open(p, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")


def quota_of(account):
    """(pct, reset_epoch, window) dell'account: le cinque ore se ci sono, altrimenti la settimana."""
    import hashlib
    conf = cm.expand(CFG["accounts"][account]["config_dir"])
    f = Path(cm.expand(CFG["quota"]["source"])) / f"quota-{hashlib.sha256(str(conf).encode()).hexdigest()[:8]}.json"
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        return None, None, ""
    if d.get("five_hour_used_pct") is not None:
        return float(d["five_hour_used_pct"]), d.get("five_hour_resets_at"), "5h"
    if d.get("weekly_used_pct") is not None:
        return float(d["weekly_used_pct"]), d.get("weekly_resets_at"), "week"
    return None, None, ""


def auto_switch_state():
    try:
        return json.loads((Path(cm.expand(CFG["state_dir"])) / "auto-switch.json").read_text())
    except (OSError, ValueError):
        return {}


def other_of(acc):
    others = [a for a in CFG["accounts"] if a != acc]
    return others[0] if len(others) == 1 else None


def switch_for(acc):
    """L'account su cui far partire una sessione NUOVA di `acc`: l'altro se `acc` e' sopra soglia (auto-switch.json, che
    scrive il giro della guardia) e l'altro non lo e'; altrimenti `acc`. Lo usa launch."""
    st = auto_switch_state()
    other = other_of(acc)
    if other and (st.get(acc) or {}).get("switched") and not (st.get(other) or {}).get("switched"):
        # se anche l'altro e' sopra soglia spostarsi non serve: resta dov'e'
        if (top_pct(other) or 0) < float((CFG.get("auto_switch") or {}).get("at_pct", 90)):
            return other
    return acc


def top_pct(acc):
    """La quota piu' alta dell'account (5 ore o settimana), o None."""
    import hashlib
    conf = cm.expand(CFG["accounts"][acc]["config_dir"])
    f = Path(cm.expand(CFG["quota"]["source"])) / f"quota-{hashlib.sha256(str(conf).encode()).hexdigest()[:8]}.json"
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    vals = [float(v) for v in (d.get("five_hour_used_pct"), d.get("weekly_used_pct")) if v is not None]
    return max(vals) if vals else None


def auto_switch_run(t):
    """Il giro dell'isteresi per gli account di auto_switch.accounts: switched quando la quota piu' alta (5 ore o
    settimana) arriva a at_pct; torna normale sotto back_pct e non prima di min_s dal cambio. Lo dice su Telegram."""
    A = CFG.get("auto_switch") or {}
    accs = [a for a in (A.get("accounts") or []) if a in CFG["accounts"]]
    if not accs:
        return
    import hashlib
    p = Path(cm.expand(CFG["state_dir"])) / "auto-switch.json"
    st = auto_switch_state()
    before = json.dumps(st, sort_keys=True)
    for acc in accs:
        conf = cm.expand(CFG["accounts"][acc]["config_dir"])
        f = Path(cm.expand(CFG["quota"]["source"])) / f"quota-{hashlib.sha256(str(conf).encode()).hexdigest()[:8]}.json"
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        vals = [float(v) for v in (d.get("five_hour_used_pct"), d.get("weekly_used_pct")) if v is not None]
        if not vals:
            continue
        pct, s = max(vals), st.setdefault(acc, {})
        if not s.get("switched") and pct >= float(A.get("at_pct", 90)) and other_of(acc):
            s.update(switched=True, since=t)
            notify(M("guard.switch_on", account=acc, pct=round(pct), other=other_of(acc)))
        elif s.get("switched") and pct < float(A.get("back_pct", 75)) and t - float(s.get("since") or 0) >= float(A.get("min_s", 1800)):
            s.update(switched=False, since=t)
            notify(M("guard.switch_off", account=acc, pct=round(pct)))
    if json.dumps(st, sort_keys=True) != before:
        p.write_text(json.dumps(st))


WEEK_S = 7 * 86400


def week_of(account):
    """(pct, reset) della finestra settimanale dell'account, o (None, None)."""
    import hashlib
    conf = cm.expand(CFG["accounts"][account]["config_dir"])
    f = Path(cm.expand(CFG["quota"]["source"])) / f"quota-{hashlib.sha256(str(conf).encode()).hexdigest()[:8]}.json"
    try:
        d = json.loads(f.read_text())
        return float(d["weekly_used_pct"]), float(d["weekly_resets_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def week_check(acc, s, t):
    """1.67 (14/10, dall'analisi dei concorrenti): due avvisi per finestra settimanale, ognuno una volta sola.
    week_unused: mancano meno di week_unused_hours al reset e la settimana e' sotto week_unused_pct — c'e' quota da
    usare prima che si azzeri (la notte). week_pace: al ritmo medio della settimana la quota finirebbe piu' di
    week_pace_margin_h ore prima del reset. Telegram e un evento all'app. True se ha avvisato."""
    pct, reset = week_of(acc)
    if pct is None or not reset or reset <= t:
        return False
    when = dt.datetime.fromtimestamp(reset).strftime("%a %H:%M")
    left_h = (reset - t) / 3600
    if left_h <= float(G.get("week_unused_hours", 12)) and pct < float(G.get("week_unused_pct", 60)) and s.get("week_unused_sent") != reset:
        s["week_unused_sent"] = reset
        title = M("relay.event_week_unused", account=acc, pct=round(100 - pct), t=when)
        notify(title)
        _load("cm-core").relay_event("week_unused", title, M("guard.week_unused_body", h=round(left_h)), account=acc)
        log(f"{acc}: settimana al {round(pct)}%, reset {when} → avviso quota non usata")
        return True
    elapsed = WEEK_S - (reset - t)
    if pct >= float(G.get("week_pace_min_pct", 30)) and elapsed > 3600 and pct < 100 and s.get("week_pace_sent") != reset:
        rate = pct / elapsed   # punti percentuali al secondo, media della settimana
        out_at = t + (100 - pct) / rate
        if out_at < reset - float(G.get("week_pace_margin_h", 12)) * 3600:
            s["week_pace_sent"] = reset
            title = M("relay.event_week_pace", account=acc, out=dt.datetime.fromtimestamp(out_at).strftime("%a %H:%M"), t=when)
            notify(title)
            _load("cm-core").relay_event("week_pace", title, M("guard.week_pace_body", pct=round(pct)), account=acc)
            log(f"{acc}: al ritmo di adesso la settimana finisce {title}")
            return True
    return False


def account_of_name(name):
    """L'account dal prefisso del nome tmux (accounts.<n>.tmux_prefix); senza prefisso, quello di default."""
    best = CFG["default_account"]
    for acc, a in CFG["accounts"].items():
        pfx = a.get("tmux_prefix") or ""
        if pfx and name.startswith(pfx):
            return acc
    return best


def live_sessions():
    """{nome tmux: session_id} delle sessioni vive nel registro peer (una cartella, o una per account)."""
    out = {}
    seen = set()
    for a in CFG["accounts"].values():
        d = Path(cm.expand(a.get("config_dir", ""))) / "sessions"
        rd = os.path.realpath(d)
        if rd in seen or not d.is_dir():
            continue
        seen.add(rd)
        for f in d.glob("*.json"):
            try:
                r = json.loads(f.read_text())
                pid = int(r.get("pid") or f.stem.split(".")[0])
            except (ValueError, OSError):
                continue
            if not os.path.exists(f"/proc/{pid}") and not os.environ.get("CM_GUARD_FAKE_PROC"):
                continue
            if r.get("name") and r.get("sessionId"):
                out[r["name"]] = r["sessionId"]
    return out


def failed_since(since_epoch):
    """session_id delle sessioni con un turno fallito (stop-failure) dopo `since_epoch`."""
    p = Path(cm.expand(CFG["state_dir"])) / "ledger.jsonl"
    out = set()
    try:
        rows = p.read_text().splitlines()
    except OSError:
        return out
    for line in rows:
        try:
            e = json.loads(line)
            if e.get("event") != "stop-failure":
                continue
            ts = dt.datetime.fromisoformat(e["ts"]).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
        if ts >= since_epoch and e.get("session_id"):
            out.add(e["session_id"])
    return out


def notify(text):
    """Telegram come SCORTA (16/09, passo 3 del ritiro del bot): se l'orologio e' accoppiato e sta ricevendo,
    la quota gliela dicono gli eventi del relay e qui si tace; altrimenti il telefono resta l'unico canale."""
    if _load("cm-core").watch_receiving():
        log("watch attivo: avviso non mandato su Telegram — " + text)
        return False
    bot = _load("cm-bot")
    if not bot.token():
        log("no telegram token: " + text)
        return False
    return bot.send(text) > 0


def hm(epoch):
    try:
        return dt.datetime.fromtimestamp(float(epoch)).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return "?"


def run():
    st = load_state()
    t = now()
    changed = False
    auto_switch_run(t)
    for acc in CFG["accounts"]:
        pct, reset, window = quota_of(acc)
        s = st.setdefault(acc, {})
        if week_check(acc, s, t):
            changed = True
        if pct is None:
            continue
        # 1. avviso, uno per finestra
        if pct >= float(G["warn_pct"]) and reset and s.get("warned_window") != reset:
            s["warned_window"] = reset
            s["resume_at"] = reset
            s["warned_at"] = t
            notify(M("guard.warn", account=acc, pct=round(pct), window=M("guard.window_5h") if window == "5h" else M("guard.window_week"), t=hm(reset)))
            log(f"{acc}: {round(pct)}% ({window}), reset {hm(reset)} → warned")
            changed = True
        # 2. reset arrivato: riprese
        ra = s.get("resume_at")
        if ra and t >= float(ra) and (pct < float(G["warn_pct"]) or reset != ra):
            failed = failed_since(float(s.get("warned_at") or 0))
            resumed = []
            asked = set(s.get("resume_names") or [])   # 1.58: «riprendi al reset» scelto dal telefono
            for name, sid in live_sessions().items():
                if account_of_name(name) != acc or (sid not in failed and name not in asked):
                    continue
                try:
                    subprocess.run([CM_BIN, "talk", name, str(G["resume_prompt"]), "--no-wait"], capture_output=True, text=True, timeout=60)
                    resumed.append(name)
                except (OSError, subprocess.TimeoutExpired):
                    continue
            night = False
            if G.get("night_after_reset", True):
                q = Path(cm.expand(CFG["night"]["queue_file"]))
                if q.is_file() and q.read_text().strip():
                    try:
                        subprocess.run([CM_BIN, "night", "run", "--send"], capture_output=True, text=True, timeout=int(G.get("night_timeout_s", 3600)))
                        night = True
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            back = M("guard.resumed", account=acc, n=len(resumed), names=", ".join(resumed) or "-", night=M("guard.night_yes") if night else M("guard.night_no"))
            # contratto 1.18: la ripresa anche all'app (l'avviso di soglia ce l'ha gia', dal diff dello stato del relay)
            _load("cm-core").relay_event("quota", M("guard.event_back_title", account=acc), f"reset {hm(ra)}\n{back}", account=acc)
            notify(back)
            log(f"{acc}: reset reached → resumed {resumed}, night={night}")
            s["resume_at"] = None
            s["resume_names"] = []
            s["resumed_at"] = t
            changed = True
    # 16/10/2026: una volta l'ora i compiti aperti da piu' di 6 ore col controllo verde si chiudono (`task auto-close`)
    stamp = state_path().with_name("auto-close.stamp")
    try:
        last = stamp.stat().st_mtime
    except OSError:
        last = 0
    if t - last >= 3600:
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.touch()
            os.utime(stamp, (t, t))
        except OSError:
            pass
        try:
            r = subprocess.run([CM_BIN, "task", "auto-close"], capture_output=True, text=True, timeout=900)
            if r.stdout.strip():
                log("auto-close: " + "; ".join(r.stdout.strip().splitlines()))
        except (OSError, subprocess.TimeoutExpired):
            pass
    if changed:
        save_state(st)
    return 0


# ------------------------------------------------------------------ cron
def cron_line():
    shim = cm.home() / ".local" / "bin" / "supervisor"
    m = max(1, int(G["cron_minutes"]))
    return f"{'* ' if m == 1 else f'*/{m} '}* * * * {shim} guard run >/dev/null 2>&1"


def crontab_read():
    return subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-l"], capture_output=True, text=True).stdout


def crontab_write(text):
    subprocess.run([os.environ.get("CM_CRONTAB_CMD", "crontab"), "-"], input=text, text=True, check=True)


def install():
    cur = crontab_read()
    if "supervisor guard run" in cur:
        print(M("guard.cron_present"))
        return 0
    crontab_write(cur.rstrip("\n") + ("\n" if cur.strip() else "") + "# supervisor guard: avviso quota e ripresa dopo il reset\n" + cron_line() + "\n")
    print(M("guard.cron_installed", line=cron_line()))
    return 0


def uninstall():
    cur = crontab_read()
    if "supervisor guard run" not in cur:
        print(M("guard.cron_absent"))
        return 0
    lines = [l for l in cur.splitlines() if "supervisor guard" not in l]
    crontab_write("\n".join(lines) + ("\n" if lines else ""))
    print(M("guard.cron_removed"))
    return 0


def status():
    print(M("guard.status_cron", state="yes" if "supervisor guard run" in crontab_read() else "no", line=cron_line()))
    st = load_state()
    for acc in CFG["accounts"]:
        pct, reset, window = quota_of(acc)
        s = st.get(acc, {})
        print(M("guard.status_account", account=acc, pct="-" if pct is None else f"{round(pct)}%", window=window or "-", reset=hm(reset) if reset else "-",
                warned="yes" if s.get("warned_window") == reset and reset else "no", resume=hm(s["resume_at"]) if s.get("resume_at") else "-"))
    return 0


def main(argv):
    verb = argv[0] if argv else "status"
    if verb == "run":
        return run()
    if verb == "switch-for":   # per launch: l'account su cui far partire una sessione nuova
        print(switch_for(argv[1] if len(argv) > 1 else ""))
        return 0
    if verb in ("install", "uninstall", "status"):
        return {"install": install, "uninstall": uninstall, "status": status}[verb]()
    print(M("guard.usage"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
