#!/usr/bin/env python3
"""supervisor setup — l'installazione completa in un comando, guidata e ripetibile (11/11/2026,
supervisor-installazione-semplice-11-11: un collega aveva installato coi comandi del README e la web app mostrava la master
chiusa mentre era aperta).

  supervisor setup [--root DIR] [--phone yes|no] [--sessions yes|no] [--yes] [--no-window] [--dry-run]

Chiede due cose sole (con --yes o senza terminale prende i default, e il telefono resta fuori): la cartella dei progetti e
se vuoi la web app sul telefono. Le sessioni passate la prima raccolta le legge solo con --sessions yes. Poi, in ordine, ogni passo con il suo esito:
  1 configurazione   init con shim, file della shell, cron del registro e tasti di tmux; la configurazione che c'e' resta
                     (--root la cambia, con una copia .bak)
  2 shell            una riga nel .bashrc o .zshrc: ~/.local/bin nel PATH e l'integrazione della shell (una volta sola)
  3 messaggi         crossSessionInbound «accept» nei settings.json degli account: senza, la master tiene in attesa i
                     messaggi del telefono (copia .bak-supervisor la prima volta)
  4 relay            la web app sul PC (relay web, senza Firebase) e il cron che tiene vivo il demone (relay install)
  5 master           aperta sulla cartella dei progetti, se non lo e' gia'
  6 stato            la web app la vede viva: si aspetta che lo stato pubblicato la mostri aperta (al piu' 30 s)
  7 prima raccolta   le sezioni piene subito, senza modello; le sessioni passate solo con --sessions yes
  8 telefono         con il si': relay setup (il tuo progetto Firebase), relay install, relay pair (il QR)
  9 doctor           cosa resta da sistemare, con il comando su ogni riga
Alla fine il riepilogo: cosa e' fatto, l'indirizzo della web app, i passi dopo. Esce 0 se nessun passo e' fallito.
Nessuna installazione di pacchetti: quello che manca lo dice doctor col suo comando.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLI = str(HERE / "supervisor")
RC_MARK = "# supervisor (supervisor setup)"
STATE_WAIT_S = 30
YES = ("y", "yes", "s", "si", "sì")


def _load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")


def M(k, **kw):
    try:
        cfg = cm.load(warn=False)
    except (OSError, ValueError):
        cfg = {"language": cm.detect_language()[0]}
    return cm.msg(cfg, k, **kw)


def run(*args, timeout=600, stdin=subprocess.DEVNULL, capture=True):
    """(rc, testo) di un comando di supervisor; stdin chiuso: nessun passo si ferma a chiedere, salvo il telefono."""
    try:
        p = subprocess.run([CLI, *args], stdin=stdin, capture_output=capture, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, M("install.timeout", cmd=" ".join(args), s=timeout)
    except OSError as e:
        return 127, str(e)
    return p.returncode, ((p.stdout or "") + ("\n" + p.stderr if (p.stderr or "").strip() else "")).strip() if capture else ""


def first_line(text, default=""):
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    return lines[-1] if any(x.startswith("Traceback") for x in lines) else (lines[0] if lines else default)


class Steps:
    def __init__(self, dry):
        self.dry, self.rows = dry, []

    def add(self, status, key, text):
        """status: ok | done (gia' a posto) | skip | warn | fail."""
        self.rows.append((status, key, text))
        mark = {"ok": "✓", "done": "✓", "skip": "·", "warn": "!", "fail": "✗"}[status]
        print(f"  {mark} {M('install.step_' + key)}: {text}", flush=True)

    def failed(self):
        return any(s == "fail" for s, _, _ in self.rows)


def ask(question, default, interactive):
    if not interactive:
        return default
    try:
        got = input(f"{question} ").strip()
    except EOFError:
        return default
    return got or default


# ------------------------------------------------------------------ i passi
def step_config(st, root, root_given):
    target = cm.config_path()
    existed = target.is_file()
    if st.dry:
        st.add("skip", "config", M("install.dry_config", path=target, root=root))
        return
    rc, out = run("init", "--yes", "--shim", "--shell", "--cron", "--tmux")
    if rc != 0 or not target.is_file():
        st.add("fail", "config", first_line(out, f"init rc {rc}"))
        return
    raw = json.loads(target.read_text())
    ws = raw.setdefault("workspace", {})
    old = ws.get("root")
    if (not existed or root_given) and root and cm.expand(old or "") != cm.expand(root):
        if existed:
            shutil.copy(target, str(target) + ".bak")
        ws["root"] = cm.contract(cm.expand(root))
        target.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
    cfg = cm.load(warn=False)
    st.add("done" if existed else "ok", "config", M("install.config_kept" if existed else "install.config_written", path=cm.contract(str(target)),
                                                  root=cfg["workspace"]["root"]))


def rc_file():
    sh = os.path.basename(os.environ.get("SHELL") or "")
    return cm.home() / (".zshrc" if sh == "zsh" else ".bashrc")


def step_shell(st):
    p = rc_file()
    try:
        cur = p.read_text() if p.is_file() else ""
    except OSError as e:
        st.add("warn", "shell", str(e)); return
    if RC_MARK in cur or "cc-supervisor/shell.sh" in cur:
        st.add("done", "shell", M("install.shell_present", path=cm.contract(str(p)))); return
    if st.dry:
        st.add("skip", "shell", M("install.dry_shell", path=cm.contract(str(p)))); return
    block = (f"\n{RC_MARK}\ncase \":$PATH:\" in *\":$HOME/.local/bin:\"*) ;; *) export PATH=\"$HOME/.local/bin:$PATH\" ;; esac\n"
             "[ -f \"$HOME/.config/cc-supervisor/shell.sh\" ] && . \"$HOME/.config/cc-supervisor/shell.sh\"\n")
    with open(p, "a") as f:
        f.write(block)
    st.add("ok", "shell", M("install.shell_added", path=cm.contract(str(p))))


def step_inbound(st):
    cfg = cm.load(warn=False)
    changed, kept = [], []
    for name, a in (cfg.get("accounts") or {}).items():
        d = Path(cm.expand(a.get("config_dir") or "~/.claude"))
        if not d.is_dir():
            continue
        p = d / "settings.json"
        try:
            raw = json.loads(p.read_text()) if p.is_file() else {}
        except ValueError:
            st.add("warn", "inbound", M("install.inbound_bad", path=cm.contract(str(p)))); continue
        if raw.get("crossSessionInbound") == "accept":
            kept.append(name); continue
        if st.dry:
            changed.append(name); continue
        if p.is_file() and not Path(str(p) + ".bak-supervisor").exists():
            shutil.copy(p, str(p) + ".bak-supervisor")
        raw["crossSessionInbound"] = "accept"
        p.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
        changed.append(name)
    if changed:
        st.add("skip" if st.dry else "ok", "inbound", M("install.inbound_set", accounts=", ".join(changed)))
    else:
        st.add("done", "inbound", M("install.inbound_present", accounts=", ".join(kept) or "-"))


def step_relay(st):
    """La web app sul PC e il demone tenuto vivo dal cron. Con Firebase gia' configurato resta com'e'."""
    if st.dry:
        st.add("skip", "relay", M("install.dry_relay")); return None
    rc, out = run("relay", "web", "--no-open", timeout=120)
    link = next((x.strip() for x in out.splitlines() if x.strip().startswith("http")), None)
    if rc != 0:
        st.add("fail", "relay", first_line(out, f"relay web rc {rc}")); return None
    rc2, out2 = run("relay", "install", timeout=120)
    if rc2 != 0:
        st.add("warn", "relay", M("install.relay_no_cron", line=first_line(out2, f"relay install rc {rc2}")))
    elif sys.platform != "darwin" and not cron_running():
        # WSL e i contenitori hanno spesso crontab ma nessun cron acceso: il demone batte da se', ma dopo un riavvio
        # nessuno lo riaccende
        st.add("warn", "relay", M("install.relay_cron_stopped", fix=cron_fix()))
    else:
        st.add("ok", "relay", M("install.relay_ok"))
    return link


def cron_running():
    if os.environ.get("CM_SETUP_CRON_RUNNING") in ("0", "1"):   # i test
        return os.environ["CM_SETUP_CRON_RUNNING"] == "1"
    P = _load("cm-proc")   # 11/11: Linux e macOS
    return any(P.comm(pid) in ("cron", "crond") for pid in P.pids())


def cron_fix():
    sudo = "" if os.geteuid() == 0 else "sudo "
    return f"{sudo}systemctl enable --now cronie" if shutil.which("dnf") or shutil.which("pacman") else f"{sudo}service cron start"


def live_rows():
    rc, out = run("sessions", "--json", "--no-screen", timeout=90)
    try:
        return json.loads(out) if rc == 0 else []
    except ValueError:
        return []


def master_name():
    return str((cm.load(warn=False).get("workspace") or {}).get("root_session_name") or "master")


# ------------------------------------------------------------------ prima dei passi (11/11, la master: «controlla tutto
# subito dopo le due domande»): l'accesso a Claude Code e, per il telefono, la CLI di Firebase e il suo accesso
FIREBASE_INSTALL = "curl -sL https://firebase.tools | bash"   # la CLI autonoma: niente Node ne' npm


def claude_bin():
    for c in (os.environ.get("CM_SETUP_CLAUDE"), shutil.which("claude"), str(cm.home() / ".local" / "bin" / "claude")):
        if c and os.path.isfile(c) and os.access(c, os.X_OK) and not c.startswith("/mnt/"):   # /mnt: il claude di Windows
            return c
    return None


def claude_env():
    cfg = cm.load(warn=False) if cm.config_path().is_file() else None
    if cfg:
        a = (cfg.get("accounts") or {}).get(cfg.get("default_account")) or {}
        d = cm.expand(a.get("config_dir") or "~/.claude")
        if os.path.realpath(d) != os.path.realpath(cm.expand("~/.claude")):
            return dict(os.environ, CLAUDE_CONFIG_DIR=d)
    return dict(os.environ)


def claude_logged(cl):
    """True, False, o None se non si sa (un Claude Code senza `auth status`)."""
    try:
        p = subprocess.run([cl, "auth", "status", "--json"], capture_output=True, text=True, timeout=30, env=claude_env(), stdin=subprocess.DEVNULL)
        return bool(json.loads(p.stdout).get("loggedIn"))
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None


def firebase_bin():
    if os.environ.get("CM_FIREBASE_CMD"):   # le prove: quella e nessun'altra
        c = os.environ["CM_FIREBASE_CMD"]
        return c if os.path.isfile(c) and os.access(c, os.X_OK) else None
    for c in (shutil.which("firebase"), "/usr/local/bin/firebase"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def firebase_user(fb):
    try:
        p = subprocess.run([fb, "login:list"], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, NODE_NO_WARNINGS="1"))
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"Logged in as (\S+)", p.stdout + p.stderr)
    return m.group(1) if m else None


def headless():
    if sys.platform == "darwin":
        return False
    return bool(os.environ.get("WSL_DISTRO_NAME") or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")))


def interactive_run(cmd):
    """Un comando che vuole la persona (accesso, sudo): stdin e uscite sul terminale."""
    try:
        return subprocess.run(cmd, shell=isinstance(cmd, str), env=claude_env() if not isinstance(cmd, str) else None).returncode
    except OSError:
        return 127


def preflight(phone, interactive, dry):
    """{claude, phone}: True se si puo' andare avanti, altrimenti il motivo. Chiede e lancia accessi e installazioni solo
    con un terminale; senza, dice la riga."""
    print(M("install.pre_header"))
    out = {"claude": True, "phone": True}
    cl = claude_bin()
    if not cl:
        out["claude"] = M("install.pre_claude_missing")
        print(f"  ✗ {out['claude']}")
    else:
        logged = claude_logged(cl)
        if logged is False and interactive and not dry and ask(M("install.pre_claude_login_ask") + " [Y/n]", "y", True).lower() in YES + ("",):
            interactive_run([cl, "auth", "login"])
            logged = claude_logged(cl)
        if logged is False:
            out["claude"] = M("install.pre_claude_no_login")
            print(f"  ✗ {out['claude']}")
        else:
            print(f"  ✓ {M('install.pre_claude_ok')}")
    if phone:
        fb = firebase_bin()
        if not fb and interactive and not dry and ask(M("install.pre_fb_install_ask", line=FIREBASE_INSTALL) + " [Y/n]", "y", True).lower() in YES + ("",):
            interactive_run(FIREBASE_INSTALL)
            fb = firebase_bin()
        if not fb:
            out["phone"] = M("install.pre_fb_missing", line=FIREBASE_INSTALL)
        else:
            user = firebase_user(fb)
            if not user and interactive and not dry and ask(M("install.pre_fb_login_ask") + " [Y/n]", "y", True).lower() in YES + ("",):
                interactive_run([fb, "login"] + (["--no-localhost"] if headless() else []))
                user = firebase_user(fb)
            if not user:
                out["phone"] = M("install.pre_fb_no_login", cmd="firebase login" + (" --no-localhost" if headless() else ""))
            else:
                print(f"  ✓ {M('install.pre_fb_ok', user=user)}")
        if out["phone"] is not True:
            print(f"  ✗ {out['phone']}")
    print()
    return out


def step_master(st, window, pre=True):
    name = master_name()
    root = cm.expand(cm.load(warn=False)["workspace"]["root"])
    if any((r.get("tmux") or r.get("name")) == name and r.get("status") != "dead" for r in live_rows()):
        st.add("done", "master", M("install.master_alive", name=name)); return True
    if st.dry:
        st.add("skip", "master", M("install.dry_master", root=cm.contract(root))); return False
    if pre is not True:   # senza l'accesso a Claude Code la master si fermerebbe sulla schermata di accesso
        st.add("fail", "master", M("install.master_blocked", why=pre)); return False
    if not os.path.isdir(root):
        st.add("fail", "master", M("install.master_no_dir", root=cm.contract(root))); return False
    rc, out = run("launch", root, *([] if window else ["--no-window"]), timeout=300)
    if rc != 0:
        st.add("fail", "master", first_line(out, f"launch rc {rc}")); return False
    waits = re.search(r"(?i)aspetta|waiting for you|sign in|accedi", out or "")
    st.add("warn" if waits else "ok", "master", M("install.master_waits" if waits else "install.master_opened", name=name))
    return True


def published_master():
    """Lo stato della master nello stato che la web app legge (relay.dir/local/state.json, o last-state con Firebase)."""
    cfg = cm.load(warn=False)
    rd = Path(cm.expand((cfg.get("relay") or {}).get("dir") or "~/.cc-supervisor/relay"))
    name = master_name()
    for p, key in ((rd / "local" / "state.json", None), (rd / "last-state.json", "state")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        d = d.get(key) if key else d
        for s in (d or {}).get("sessions") or []:
            if s.get("name") == name or s.get("tmux") == name:
                return s.get("state")
    return None


def step_state(st, wait_s=STATE_WAIT_S):
    if st.dry:
        st.add("skip", "state", M("install.dry_state")); return
    run("relay", "push", timeout=120)   # una push adesso: il battito arriverebbe entro un minuto
    end = time.time() + wait_s
    seen = None
    while time.time() < end:
        seen = published_master()
        if seen and seen != "gone":
            st.add("ok", "state", M("install.state_ok", state=seen)); return
        time.sleep(1)
    st.add("fail", "state", M("install.state_stale", state=seen or "-", s=wait_s))


def step_raccolta(st, sessions):
    if st.dry:
        st.add("skip", "raccolta", M("install.dry_raccolta")); return
    rc, out = run("prima-raccolta", "--sessions", "si" if sessions else "no", timeout=900)
    st.add("ok" if rc == 0 else "warn", "raccolta", M("install.raccolta_ok") if rc == 0 else first_line(out, f"prima-raccolta rc {rc}"))


def step_phone(st, phone, interactive, pre=True):
    """Firebase guidato fino al QR: relay setup, relay install, relay pair. Vogliono la persona (login Google, il QR)."""
    cfg = cm.load(warn=False)
    has_fb = bool(str((cfg.get("relay") or {}).get("firebase_url") or "").strip())
    rd = Path(cm.expand((cfg.get("relay") or {}).get("dir") or "~/.cc-supervisor/relay"))
    try:
        paired = bool(json.loads((rd / "devices.json").read_text()))
    except (OSError, ValueError):
        paired = False
    if has_fb and paired:
        st.add("done", "phone", M("install.phone_paired")); return
    if not phone:
        st.add("skip", "phone", M("install.phone_skipped")); return
    if st.dry:
        st.add("skip", "phone", M("install.phone_needs_terminal")); return
    if pre is not True:
        st.add("fail", "phone", M("install.phone_blocked", why=pre)); return
    if not interactive:
        st.add("warn", "phone", M("install.phone_needs_terminal")); return
    for args in ([] if has_fb else [("relay", "setup")]) + [("relay", "install"), ("relay", "pair")]:
        print(M("install.phone_running", cmd="supervisor " + " ".join(args)), flush=True)
        rc, _ = run(*args, timeout=3600, stdin=None, capture=False)
        if rc != 0:
            st.add("fail", "phone", M("install.phone_failed", cmd="supervisor " + " ".join(args), rc=rc)); return
    st.add("ok", "phone", M("install.phone_ok"))


def step_doctor(st):
    rc, out = run("doctor", timeout=180)
    lines, bad, show = out.splitlines(), [], []
    try:   # il PATH l'ha appena messo il passo della shell: vale nei terminali nuovi, non in quello da cui si e' lanciato
        rc_has = RC_MARK in rc_file().read_text()
    except OSError:
        rc_has = False
    for i, x in enumerate(lines):
        if rc_has and x.strip().startswith("WARN shim_not_in_path"):
            continue
        if re.match(r"(FAIL|WARN)\b", x.strip()):
            bad.append(x.strip())
            show.append(x.strip())
            if i + 1 < len(lines) and lines[i + 1].startswith("     ") and not re.match(r"\s*(PASS|INFO|WARN|FAIL)\b", lines[i + 1]):
                show.append("  " + lines[i + 1].strip())   # il rimedio della riga
    if not bad:
        st.add("ok", "doctor", M("install.doctor_clean")); return
    fails = [x for x in bad if x.startswith("FAIL")]
    st.add("warn" if not fails else "fail", "doctor", M("install.doctor_left", fails=len(fails), warns=len(bad) - len(fails)))
    for x in show:
        print(f"      {x}")


def report(st):
    """Le righe da mandare a chi aiuta: sistema, versioni, i passi non andati. Senza token ne' indirizzi della web app;
    la home e' «~»."""
    def ver(*cmd):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip().splitlines()
            return out[0][:60] if out else "-"
        except (OSError, subprocess.SubprocessError):
            return "-"
    sysname = ver("uname", "-sr") + (" WSL" if os.environ.get("WSL_DISTRO_NAME") else "")
    head = (f"supervisor {ver(CLI, 'version')} · {sysname} · python {sys.version.split()[0]} · {ver('tmux', '-V')} · "
            f"claude {ver(os.environ.get('CM_CLAUDE_BIN') or 'claude', '--version')}")
    rows = [f"{s} {k}: {t}" for s, k, t in st.rows if s in ("warn", "fail")]
    h = str(cm.home())
    return "\n".join([head] + rows).replace(h, "~")


def summary(st, link, paired):
    """Senza errori tre righe: l'indirizzo della web app, cosa fare domani, il telefono. Gli avvisi, se ci sono, prima."""
    print("\n" + M("install.summary_header"))
    for s, k, t in st.rows:
        if s in ("warn", "fail"):
            print(f"  {'✗' if s == 'fail' else '!'} {M('install.step_' + k)}: {t}")
    if link:
        print(M("install.summary_link", link=link))
    print(M("install.summary_tomorrow"))
    print(M("install.summary_more_devices" if paired else "install.summary_phone"))
    if st.failed():
        print("\n" + M("install.summary_rerun"))
        print(M("install.report_header"))
        print(report(st))
        print(M("install.report_footer"))


def main(argv):
    args = list(argv)
    if any(a in ("-h", "--help") for a in args):
        print(__doc__); return 0

    def opt(name):
        if name in args:
            i = args.index(name)
            v = args[i + 1] if i + 1 < len(args) else None
            del args[i:i + 2]
            return v
        return None
    root, phone_a, sess_a = opt("--root"), opt("--phone"), opt("--sessions")
    yes, dry, window = "--yes" in args, "--dry-run" in args, "--no-window" not in args
    args = [a for a in args if a not in ("--yes", "--dry-run", "--no-window")]
    if args:
        print(M("install.usage"), file=sys.stderr); return 2
    for v in (phone_a, sess_a):
        if v is not None and v.lower() not in YES + ("n", "no"):
            print(M("install.usage"), file=sys.stderr); return 2
    interactive = sys.stdin.isatty() and not yes
    # 11/11 (la prova su una WSL pulita): ~/.local/bin, dove stanno lo shim e il Claude Code appena installato, entra nel
    # PATH solo nei terminali nuovi; senza, doctor e i passi trovavano il Claude Code di Windows (/mnt/c/...)
    lb = str(cm.home() / ".local" / "bin")
    if lb not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = lb + os.pathsep + os.environ.get("PATH", "")
    print(M("install.header"))
    if cm.config_path().is_file():
        default_root = cm.load(warn=False)["workspace"]["root"]
    else:
        default_root = cm.contract(cm.expand(cm.detect(cm.Machine())[0]["workspace"]["root"]))
    root_given = root is not None
    if root is None:
        root = ask(M("install.ask_root", default=default_root), default_root, interactive)
    if not os.path.isdir(cm.expand(root)):
        print(M("install.root_missing", root=root), file=sys.stderr); return 2
    if phone_a is None:
        phone = ask(M("install.ask_phone") + " [y/N]", "n", interactive).lower() in YES
    else:
        phone = phone_a.lower() in YES
    sessions = (sess_a or "no").lower() in YES   # 11/11 (il maintainer: «semplificare al massimo»): due domande sole; questa e' un'opzione
    pre = preflight(phone, interactive, dry)
    st = Steps(dry)
    step_config(st, root, root_given or not cm.config_path().is_file())
    if st.failed():
        summary(st, None, False); return 1
    step_shell(st)
    step_inbound(st)
    link = step_relay(st)
    if step_master(st, window, pre["claude"]):
        step_state(st)
    step_raccolta(st, sessions)
    step_phone(st, phone, interactive, pre["phone"])
    step_doctor(st)
    summary(st, link, any(k == "phone" and s in ("ok", "done") for s, k, _ in st.rows))
    return 1 if st.failed() else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
