#!/usr/bin/env python3
"""supervisor — sessioni Claude su un altro host (piano multi-PC 4.2, fase 2.1; ok del maintainer 01/10/2026 23:43).

  supervisor launch <cartella> --host H|auto     (cm-launch.sh chiama: cm-rsession.py launch H CARTELLA BASE -- ARGS)
  supervisor host admit H <cartella>            la cartella puo' andare su H? exit 0 si', 1 no con la regola
  supervisor host fetch H <nome>                i commit fatti la' → ramo locale win/<nome>, il merge si fa qui
  supervisor host sessions                      le sessioni lanciate su altri host (registro locale)
  supervisor talk HOST:nome "prompt" [--wait S] [--quiet S] [--no-wait]   (fase 2.2: anche il solo nome registrato)
  supervisor wait HOST:nome [--timeout S]
  supervisor close HOST:nome [--force]          rifiuta una sessione busy o waiting senza --force; la cartella resta
  cm-rsession.py pick <cartella>                   l'host per --host auto ('' = la regia)
  cm-rsession.py propose <cartella>                la riga di proposta di launch quando la regia e' carica

Regola di ammissione (letta dal codice, mai a giudizio), tutte vere:
  0. l'host e' confermato, ha il ruolo `sessions` e un adattatore che sa lanciarle (oggi windows-native);
  a. l'account dedotto dalla cartella e' in `trust.accounts` dell'host;
  b. la cartella non e' sotto `scheduler.client_paths` (se l'host ha trust.clients false) e sta sotto la radice;
  c. il percorso relativo alla radice corrisponde a `hosts.H.sessions.allow` e a nessun `hosts.H.sessions.deny`
     (glob; vale anche per le sottocartelle di una voce);
  d. nessun segreto: `.cm-offload.json` senza `secrets: true` (con trust.secrets false), nessun file dai nomi di
     `scheduler.secret_globs` e nessun segreto noto nel commit che parte (la scansione di offload).
Il rifiuto nomina la condizione mancata.

Cosa va la' e cosa torna: la FOTOGRAFIA di HEAD (git archive, senza i file segreti), mai la storia: su H diventa un
repository nuovo col commit «base» e la cartella `hosts.H.sessions.root`/<percorso relativo>. Su H niente
credenziali git: si committa la', il push resta qui. `host fetch` porta indietro i commit come serie di patch
(format-patch) e le applica con `git am` in un worktree temporaneo sopra il commit di partenza: ramo win/<nome>,
il checkout condiviso non si tocca. Le modifiche non committate qui non partono (si dice); quelle non committate
la' restano la' (si dice).

talk, wait e close (fase 2.2): il messaggio entra prima nella casella locale (cm-inbox, come in locale), poi va nella
casella nativa della sessione la' (named pipe su Windows, stesso protocollo di post_socket) con il testo in base64;
la risposta si legge dal transcript la' dall'offset preso prima dell'invio, con lo stato del registro peer.

Prove: CM_RSESSION_ADAPTER=<file.py> (un modulo con adapter_for(name, host, cfg)), CM_LOADAVG, CM_MEMAVAIL_GB,
CM_NPROC.
"""
import datetime as dt
import fnmatch
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _cm_proc():
    """cm-proc: processi e misure per Linux e macOS (11/11, supervisor-macos-11-11)."""
    import importlib.util as _u
    spec = _u.spec_from_file_location("cm_proc", Path(__file__).resolve().parent / "cm-proc.py")
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

HERE = Path(__file__).resolve().parent


def _load(name, path=None):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path or HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load("cm-config")
CFG = cm.load(warn=False)
M = lambda k, **kw: cm.msg(CFG, k, **kw)  # noqa: E731
S = CFG["scheduler"]


class Refused(Exception):
    pass


def state_dir():
    return Path(cm.expand(CFG["state_dir"]))


def rec_path(name):
    return state_dir() / "rsessions" / f"{name}.json"


def hosts():
    return dict(CFG.get("hosts") or {})


def adapter_for(name, h):
    fake = os.environ.get("CM_RSESSION_ADAPTER")
    if fake:
        return _load("cm_rsession_fake", fake).adapter_for(name, h, CFG)
    return _load("cm-adapters").adapter_for(name, h, CFG)


def root():
    return os.path.realpath(cm.expand(CFG["workspace"]["root"]))


def under(path, base):
    path, base = os.path.realpath(path), os.path.realpath(cm.expand(base))
    return path == base or path.startswith(base.rstrip("/") + "/")


def matches(rel, pats):
    """La prima voce che copre `rel` o una sua cartella madre (deny `personali/kb` copre `personali/kb/x`)."""
    parts = rel.split("/")
    for p in pats or []:
        for i in range(len(parts), 0, -1):
            if fnmatch.fnmatchcase("/".join(parts[:i]), p.rstrip("/")):
                return p
    return None


def git(folder, *args, check=True):
    p = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise Refused(M("rsession.git_error", cmd=" ".join(args[:2]), err=p.stderr.strip()[:200]))
    return p.stdout


# ------------------------------------------------------------------ ammissione
def account_config_name(account):
    """La cartella base della config di un account (~/.claude-pixel -> .claude-pixel): sull'host sta nella sua home."""
    a = (CFG.get("accounts") or {}).get(account or "") or {}
    return os.path.basename(cm.expand(a.get("config_dir") or "~/.claude").rstrip("/")) or ".claude"


def admit(name, h, folder):
    """Solleva Refused con la condizione mancata; restituisce (account, percorso relativo alla radice)."""
    if not h:
        raise Refused(M("rsession.unknown_host", host=name, known=", ".join(sorted(hosts())) or "-"))
    if not h.get("confirmed_at"):
        raise Refused(M("rsession.not_confirmed", host=name))
    if "sessions" not in (h.get("roles") or []):
        raise Refused(M("rsession.no_role", host=name))
    if h.get("kind") != "windows-native":
        raise Refused(M("rsession.kind", host=name, kind=h.get("kind") or "?"))
    trust = h.get("trust") or {}
    account, _ = cm.account_for(CFG, folder)
    if account not in (trust.get("accounts") or []):
        raise Refused(M("rsession.rule_account", host=name, account=account or "?", accounts=", ".join(trust.get("accounts") or []) or "-"))
    if not trust.get("clients"):
        for cp in S.get("client_paths") or []:
            if under(folder, cp):
                raise Refused(M("rsession.rule_clients", host=name, path=cp))
    if not under(folder, root()):
        raise Refused(M("rsession.rule_root", root=root()))
    rel = os.path.relpath(os.path.realpath(folder), root()).replace(os.sep, "/")
    sess = h.get("sessions") or {}
    if not matches(rel, sess.get("allow")):
        raise Refused(M("rsession.rule_allow", host=name, rel=rel, allow=", ".join(sess.get("allow") or []) or "-"))
    d = matches(rel, sess.get("deny"))
    if d:
        raise Refused(M("rsession.rule_deny", host=name, rel=rel, pat=d))
    if not trust.get("secrets"):
        rf = Path(folder) / ".cm-offload.json"
        if rf.is_file():
            try:
                recipes = json.loads(rf.read_text()).get("recipes") or {}
            except (ValueError, AttributeError):
                recipes = {}
            if any(isinstance(r, dict) and (r.get("needs") or {}).get("secrets") for r in recipes.values()):
                raise Refused(M("rsession.rule_secrets_recipe", host=name))
    return account, rel


def snapshot_check(folder):
    """La fotografia che parte: HEAD della cartella, che deve essere la radice di un repository. Solleva Refused su un
    segreto (nome o contenuto). Restituisce (sha, file non committati che restano qui)."""
    if git(folder, "rev-parse", "--is-inside-work-tree", check=False).strip() != "true":
        raise Refused(M("rsession.not_git", dir=folder))
    top = git(folder, "rev-parse", "--show-toplevel").strip()
    if os.path.realpath(top) != os.path.realpath(folder):
        raise Refused(M("rsession.not_top", dir=folder, top=top))
    sha = git(folder, "rev-parse", "HEAD").strip()
    off = _load("cm-offload")
    globs = S["secret_globs"]
    scan = subprocess.run(["git", "-C", folder, "grep", "-I", "-l", "-E", "-e", off.SECRET_RE, sha, "--", "."]
                          + off.secret_pathspecs(globs), capture_output=True, text=True)
    hits = [l.split(":", 1)[1] if ":" in l else l for l in scan.stdout.splitlines()]
    if hits:
        raise Refused(M("rsession.rule_secrets_found", files=", ".join(hits[:5])))
    dirty = [l[3:] for l in git(folder, "status", "--porcelain").splitlines() if l.strip()]
    return sha, dirty


# ------------------------------------------------------------------ scelta automatica e proposta
def pressure():
    """(carico a 1 minuto, core, GB liberi, carica?) — soglie in scheduler.session_offload."""
    so = S.get("session_offload") or {}
    load = float(os.environ.get("CM_LOADAVG") or os.getloadavg()[0])
    cores = int(os.environ.get("CM_NPROC") or os.cpu_count() or 1)
    if os.environ.get("CM_MEMAVAIL_GB"):
        free = float(os.environ["CM_MEMAVAIL_GB"])
    else:
        avail = _cm_proc().memory()[1]
        free = avail / 1024 if avail is not None else 99.0
    loaded = load > cores * float(so.get("load_per_core", 1.0)) or free < float(so.get("free_ram_gb", 2))
    return load, cores, round(free, 1), loaded


def candidates(folder):
    out = []
    for name, h in sorted(hosts().items()):
        if name == "local":
            continue
        try:
            admit(name, h, folder)
            out.append(name)
        except Refused:
            pass
    return out


def cmd_pick(argv):
    folder = argv[0]
    load, cores, free, loaded = pressure()
    c = candidates(folder) if loaded else []
    print(c[0] if c else "")
    return 0


def cmd_propose(argv):
    folder = argv[0]
    load, cores, free, loaded = pressure()
    if not loaded:
        return 0
    c = candidates(folder)
    if c:
        print(M("rsession.propose", load=f"{load:.1f}", cores=cores, free=free, host=c[0]), file=sys.stderr)
    return 0


def cmd_admit(argv):
    if len(argv) < 2:
        print(M("rsession.usage"), file=sys.stderr)
        return 2
    name, folder = argv[0], os.path.realpath(argv[1])
    try:
        account, rel = admit(name, hosts().get(name), folder)
    except Refused as e:
        print(str(e))
        return 1
    print(M("rsession.admitted", host=name, rel=rel, account=account))
    return 0


# ------------------------------------------------------------------ lancio
def unique_name(a, base):
    live = {(s.get("entry") or {}).get("name") for s in (a.status().get("sessions") or []) if s.get("alive")}
    name, n = base, 2
    while name in live:
        name, n = f"{base}-{n}", n + 1
    return name


def cmd_launch(argv):
    """launch HOST CARTELLA BASE [--] claude_args…"""
    if len(argv) < 3:
        print(M("rsession.usage"), file=sys.stderr)
        return 2
    hname, folder, base = argv[0], os.path.realpath(argv[1]), argv[2]
    args = argv[3:]
    if args and args[0] == "--":
        args = args[1:]
    h = hosts().get(hname)
    try:
        account, rel = admit(hname, h, folder)
        sha, dirty = snapshot_check(folder)
    except Refused as e:
        print(f"supervisor launch: {e}", file=sys.stderr)
        return 6
    a = adapter_for(hname, h)
    sess = h.get("sessions") or {}
    rroot = (sess.get("root") or "%USERPROFILE%\\claude-sessions").rstrip("\\/")
    sep = "/" if "/" in rroot else "\\"
    rdir = rroot + sep + rel.replace("/", sep)
    name = unique_name(a, f"{hname}-{base}")
    print(M("rsession.starting", host=hname, name=name, rdir=rdir), file=sys.stderr)
    if dirty:
        print(M("rsession.dirty_here", n=len(dirty), files=", ".join(dirty[:5])), file=sys.stderr)
    info = a.session_prepare(rdir)
    if info.get("exists") and not info.get("marker"):
        print(M("rsession.foreign_dir", host=hname, rdir=rdir), file=sys.stderr)
        return 6
    if info.get("exists") and info.get("base") != sha:
        # 04/11 (supervisor-launch-win-base-04-11, trovato dalla master): la copia la' era sulla base del 25/10 e la
        # sessione e' partita su un codice vecchio, senza il ramo di qui. Una HEAD diversa: se la' non c'e' lavoro da
        # riportare (commit oltre la base, file non committati), la copia vecchia si mette da parte e se ne fa una nuova;
        # altrimenti ci si ferma e si dice come portarlo qui
        ahead, dirty_there = int(info.get("ahead") or 0), int(info.get("dirty") or 0)
        if ahead or dirty_there:
            print(M("rsession.stale_with_work", host=hname, rdir=rdir, base=str(info.get("base") or "?")[:10], head=sha[:10],
                    ahead=ahead, dirty=dirty_there), file=sys.stderr)
            return 6
        moved = a.session_retire(rdir, dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        if not moved.get("ok"):
            print(M("rsession.retire_failed", host=hname, rdir=rdir, error=str(moved.get("error") or "?")[:200]), file=sys.stderr)
            return 6
        print(M("rsession.stale_moved", host=hname, base=str(info.get("base") or "?")[:10], head=sha[:10], to=moved.get("to") or "?"), file=sys.stderr)
        info = {"exists": False}
    if info.get("exists"):
        base_sha = info["base"]
        print(M("rsession.reuse", host=hname, rdir=rdir, base=base_sha[:10], ahead=info.get("ahead", 0)), file=sys.stderr)
    else:
        off = _load("cm-offload")
        tmpd = Path(tempfile.mkdtemp(prefix="cm-rs-"))
        try:
            tar = tmpd / "snap.tar"
            subprocess.run(["git", "-C", folder, "archive", "--format=tar", "-o", str(tar), sha, "--", "."]
                           + off.secret_pathspecs(S["secret_globs"]), check=True, capture_output=True)
            who = (git(folder, "config", "user.name", check=False).strip(), git(folder, "config", "user.email", check=False).strip())
            a.session_seed(rdir, tar, sha, who)
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)
        base_sha = sha
    # 04/11: prima di avviare, la copia la' deve essere sulla HEAD di qui
    there = a.session_prepare(rdir)
    if there.get("base") != sha:
        print(M("rsession.base_mismatch", host=hname, rdir=rdir, base=str(there.get("base") or "?")[:10], head=sha[:10]), file=sys.stderr)
        return 6
    # 06/11/2026 (supervisor-win-altro-account-06-11): l'account della cartella con la sua config anche la' (stesso nome base)
    cdir = account_config_name(account)
    a.session_trust(rdir, cdir)
    a.session_start(name, rdir, list(args) + ["--remote-control", name, "-n", name], cdir)
    deadline = time.time() + float(sess.get("start_timeout_s") or 90)
    entry = None
    while time.time() < deadline:
        for s in a.status().get("sessions") or []:
            e = s.get("entry") or {}
            if s.get("alive") and e.get("name") == name:
                entry = e
                if e.get("bridgeSessionId"):
                    break
        if entry and entry.get("bridgeSessionId"):
            break
        time.sleep(3)
    rec = {"name": name, "host": hname, "dir": folder, "rel": rel, "remote_dir": rdir, "base": base_sha,
           "account": account, "started": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "session_id": (entry or {}).get("sessionId"), "link": None}
    if entry and entry.get("bridgeSessionId"):
        rec["link"] = "https://claude.ai/code/" + entry["bridgeSessionId"]
    p = rec_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    # 09/10: la sessione compare in sessions e nell'app solo con il sondatore degli host nel crontab
    try:
        if _load("cm-hosts").ensure_poller():
            print(M("rsession.poller_installed"), file=sys.stderr)
    except (OSError, subprocess.CalledProcessError) as e:
        print(M("rsession.poller_failed", error=e), file=sys.stderr)
    if not entry:
        print(M("rsession.no_registry", host=hname, name=name, s=int(sess.get("start_timeout_s") or 90)), file=sys.stderr)
        return 5
    # 21/10/2026 (Claude Code 2.1.304): una ripresa su un altro host puo' fermarsi sull'avviso del costo in quota, e
    # sull'host nessuno guarda: cm-resume-quota.py sceglie una conversazione nuova o chiede alla persona
    note = ""
    if any(x in args for x in ("--resume", "--continue", "-c", "-r")) and hasattr(a, "session_console"):
        try:
            rq = _load("cm-resume-quota")
            sid = args[args.index("--resume") + 1] if "--resume" in args and args.index("--resume") + 1 < len(args) else ""
            _, note = rq.check(name, False, float(rq.settings()["watch_s"]), sid, folder, account, term=_load("cm-answer").Remote(hname, name))
        except Exception as e:   # noqa: BLE001 — il lancio e' riuscito: la nota non lo fa fallire
            note = f"nota:      resume_quota: {e}"
    print(M("rsession.started", host=hname))
    print(f"  nome:      {name}")
    print(f"  account:   {account}")
    print(f"  cartella:  {folder}")
    print(f"  su {hname}:    {rdir} (base {base_sha[:10]})")
    print(f"  link:      {rec['link'] or M('rsession.link_later')}")
    if note:
        print("  " + note)
    print(M("rsession.howto", host=hname, name=name))
    return 0


# ------------------------------------------------------------------ ritorno
def cmd_fetch(argv):
    """fetch HOST NOME: i commit fatti la' dalla base → ramo win/NOME (worktree temporaneo, git am)."""
    if len(argv) < 2:
        print(M("rsession.usage"), file=sys.stderr)
        return 2
    hname, name = argv[0], argv[1]
    p = rec_path(name)
    if not p.is_file():
        print(M("rsession.no_record", name=name, dir=str(p.parent)), file=sys.stderr)
        return 3
    rec = json.loads(p.read_text())
    if rec["host"] != hname:
        print(M("rsession.other_host", name=name, host=rec["host"]), file=sys.stderr)
        return 3
    a = adapter_for(hname, hosts().get(hname))
    tmpd = Path(tempfile.mkdtemp(prefix="cm-rs-fetch-"))
    branch = f"{hname}/{name.removeprefix(hname + '-')}"
    wt = tmpd / "wt"
    try:
        # 05/11/2026 (supervisor-host-fetch-base-05-11): la' la storia puo' essere stata riallineata a origin (16:44, la
        # sessione di fable-director: reset su origin/main e un commit nuovo); allora la base del seme non c'e' piu' e le
        # patch dalla base salvata riportavano commit che qui ci sono gia' («already exists in index»). La base e' il primo
        # commit della storia di HEAD la' che e' o la base del seme o un commit che c'e' anche qui
        remote_base, local_base = None, rec["base"]
        if hasattr(a, "session_history"):
            hist = a.session_history(rec["remote_dir"])
            for h in hist.get("hashes") or []:
                if h == hist.get("root"):
                    break
                if subprocess.run(["git", "-C", rec["dir"], "cat-file", "-e", f"{h}^{{commit}}"], capture_output=True).returncode == 0:
                    remote_base = local_base = h
                    break
            else:
                print(M("rsession.diverged", host=hname, name=name, base=rec["base"][:10]), file=sys.stderr)
                return 4
        out = a.session_patches(rec["remote_dir"], tmpd / "series.mbox", remote_base) if remote_base else a.session_patches(rec["remote_dir"], tmpd / "series.mbox")
        if out.get("dirty"):
            print(M("rsession.dirty_there", host=hname, n=out["dirty"]), file=sys.stderr)
        if not out.get("count"):
            print(M("rsession.nothing_new", host=hname, name=name, base=local_base[:10]))
            return 0
        git(rec["dir"], "worktree", "add", "--detach", str(wt), local_base)
        am = subprocess.run(["git", "-C", str(wt), "am", "--keep-cr", "--committer-date-is-author-date", str(tmpd / "series.mbox")],
                            capture_output=True, text=True)
        if am.returncode != 0:
            subprocess.run(["git", "-C", str(wt), "am", "--abort"], capture_output=True)
            print(M("rsession.am_failed", err=(am.stderr or am.stdout).strip()[-400:]), file=sys.stderr)
            return 4
        head = git(wt, "rev-parse", "HEAD").strip()
        git(rec["dir"], "branch", "-f", branch, head)
    finally:
        if wt.exists():
            subprocess.run(["git", "-C", rec["dir"], "worktree", "remove", "--force", str(wt)], capture_output=True)
        subprocess.run(["git", "-C", rec["dir"], "worktree", "prune"], capture_output=True)
        shutil.rmtree(tmpd, ignore_errors=True)
    print(M("rsession.fetched", n=out["count"], host=hname, branch=branch, base=local_base[:10]))
    print(M("rsession.merge_howto", branch=branch))
    return 0


def cmd_sessions(argv):
    d = state_dir() / "rsessions"
    rows = sorted(d.glob("*.json")) if d.is_dir() else []
    if not rows:
        print(M("rsession.none"))
        return 0
    for f in rows:
        r = json.loads(f.read_text())
        print(f"{r['name']:<32} {r['host']:<8} {r['started'][:16]}  {r['dir']}  base {r['base'][:10]}  {r.get('link') or '-'}")
    return 0


# ------------------------------------------------------------------ talk, wait, close (fase 2.2)
def resolve(target):
    """(host, nome) per `HOST:nome` con HOST dichiarato, o per un nome registrato qui come sessione remota; None se no."""
    if ":" in target:
        h, n = target.split(":", 1)
        if h in hosts() and h != "local" and n:
            # 10/10: `HOST:nome` si pubblica senza il «HOST-» che launch mette davanti (win:video-supervisor per
            # win-video-supervisor): qui torna il nome vero, quando il record c'e'
            if not rec_path(n).is_file() and rec_path(f"{h}-{n}").is_file():
                n = f"{h}-{n}"
            return h, n
    if re.fullmatch(r"[A-Za-z0-9._-]+", target) and rec_path(target).is_file():
        try:
            return json.loads(rec_path(target).read_text())["host"], target
        except (OSError, ValueError, KeyError):
            return None
    return None


def cmd_resolve(argv):
    r = resolve(argv[0]) if argv else None
    if r:
        print(f"{r[0]} {r[1]}")
    return 0 if r else 1


def _opt(argv, name, default):
    return type(default)(argv[argv.index(name) + 1]) if name in argv else default


READ_TRIES, READ_TIMEOUT_S = 3, 45


def _read(a, name, offset, last=False):
    """08/11 (supervisor-wait-win-lettura-08-11): una lettura su win dura 2-3 s, ma ogni tanto lo script la' resta
    fermo fino al timeout mentre ssh risponde: tetto di READ_TIMEOUT_S a chiamata e READ_TRIES tentativi, poi
    l'errore sale a chi chiama."""
    for i in range(READ_TRIES):
        try:
            return a.session_read(name, offset, last=last, timeout=READ_TIMEOUT_S)
        except Exception as e:   # noqa: BLE001 — HostError dall'adattatore vero, altro dal finto
            err = e
            if i < READ_TRIES - 1:
                time.sleep(2)
    raise err


def _drain(a, name, offset):
    """I testi dall'offset fino in fondo: cm-session.js ne legge al massimo 8 MB a chiamata e dice more."""
    texts = []
    for _ in range(64):
        d = _read(a, name, offset)
        texts += d.get("texts") or []
        if not d.get("more") or d.get("offset", offset) == offset:
            break
        offset = d["offset"]
    return texts


def read_reply(a, name, offset, max_wait, quiet):
    """Come wait_reply di cm-talk: si legge finche' la sessione torna idle dopo essere stata busy (o il transcript
    resta fermo `quiet` secondi con del testo, o se ne va); tetto max_wait. (testi, errore): l'errore e' la lettura
    fallita (08/11: su win una lettura scadeva a 120 s dopo un post riuscito), i testi quelli letti fin li'."""
    start = last_change = time.time()
    texts, seen_busy = [], False
    while True:
        try:
            d = _read(a, name, offset)
        except Exception as e:   # noqa: BLE001 — HostError dall'adattatore vero, altro dal finto
            return texts, str(e)
        offset = d.get("offset", offset)
        if d.get("texts"):
            texts += d["texts"]
            last_change = time.time()
        st = d.get("status")
        seen_busy = seen_busy or st == "busy"
        if st == "gone" or (st == "idle" and (seen_busy or texts) and time.time() - last_change > 1.5):
            break
        if texts and time.time() - last_change > quiet:
            break
        if time.time() - start > max_wait:
            print(M("talk.timeout", s=max_wait))
            break
        time.sleep(2)
    return texts, None


def cmd_talk(argv):
    if len(argv) < 2:
        print(M("rsession.usage"), file=sys.stderr)
        return 2
    r = resolve(argv[0])
    if not r:
        print(M("rsession.not_remote", target=argv[0]), file=sys.stderr)
        return 3
    hname, name = r
    text, rest = argv[1], argv[2:]
    max_wait = _opt(rest, "--wait", int(CFG["talk"]["max_wait_s"]))
    quiet = _opt(rest, "--quiet", int(CFG["talk"]["quiet_s"]))
    inbox = _load("cm-inbox")
    who = os.environ.get("CM_TALK_SENDER") or CFG["talk"]["from_name"]   # 21/10: la firma e' chi scrive (cm-talk la mette)
    rec = inbox.put(name, text, who, "")
    a = adapter_for(hname, hosts().get(hname))
    try:
        d = a.session_post(name, text, who, "uds:" + os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET", ""))
    except Exception as e:   # noqa: BLE001 — l'adattatore vero solleva HostError, il finto altro: il messaggio resta in casella
        print(M("rsession.post_failed", host=hname, name=name, error=str(e)[-300:], id=rec["id"]), file=sys.stderr)
        return 5
    inbox.mark(rec["id"], "delivered", f"pipe:{hname}")
    print(M("talk.sent", name=f"{hname}:{name}", via=f"pipe ({hname})"), file=sys.stderr)
    if "--no-wait" in rest:
        return 0
    texts, err = read_reply(a, name, d.get("offset", 0), max_wait, quiet)
    for t in texts:
        print(t)
    if err:
        # 08/11 (supervisor-talk-win-consegnato-08-11): il messaggio e' arrivato, non si e' letta la risposta. Codice
        # suo (7), distinto dall'invio fallito (5), cosi' chi chiama non lo rimanda: due talk a win uscivano «unreachable»
        print(M("rsession.reply_unread", host=hname, name=name, error=err[-300:], id=rec["id"]), file=sys.stderr)
        return 7
    return 0


def cmd_wait(argv):
    r = resolve(argv[0]) if argv else None
    if not r:
        print(M("rsession.not_remote", target=argv[0] if argv else ""), file=sys.stderr)
        return 3
    hname, name = r
    timeout = _opt(argv, "--timeout", 12 * 3600)
    a = adapter_for(hname, hosts().get(hname))
    try:
        d = _read(a, name, "end")
        offset, start = d.get("offset", 0), time.time()
        while d.get("status") not in ("idle", "gone"):
            if time.time() - start > timeout:
                print(M("talk.timeout", s=timeout))
                return 1
            time.sleep(3)
            d = _read(a, name, offset)
        print(M("wait.idle", name=f"{hname}:{name}", status=d.get("status")))
        texts = _drain(a, name, offset)
        if not texts:
            texts = _read(a, name, 0, last=True).get("texts") or []
    except Exception as e:   # noqa: BLE001 — 08/11: mai un traceback, una riga e un codice suo (7, come talk)
        print(M("rsession.wait_unread", host=hname, name=name, error=str(e)[-300:]), file=sys.stderr)
        return 7
    for t in texts:
        print(t)
    return 0


def cmd_close(argv):
    r = resolve(argv[0]) if argv else None
    if not r:
        print(M("rsession.not_remote", target=argv[0] if argv else ""), file=sys.stderr)
        return 3
    hname, name = r
    a = adapter_for(hname, hosts().get(hname))
    try:
        d = a.session_close(name, force="--force" in argv)
    except Exception as e:   # noqa: BLE001
        msg = str(e)
        if "status busy" in msg or "status waiting" in msg:
            print(M("rsession.close_busy", host=hname, name=name, status=msg.rsplit(" ", 1)[-1]), file=sys.stderr)
            return 4
        print(M("rsession.close_failed", host=hname, name=name, error=msg[-300:]), file=sys.stderr)
        return 5
    p = rec_path(name)
    if p.is_file():
        rec = json.loads(p.read_text())
        rec["closed"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        p.write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    print(M("rsession.closed_already" if d.get("already") else "rsession.closed", host=hname, name=name))
    return 0


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    fn = {"launch": cmd_launch, "fetch": cmd_fetch, "admit": cmd_admit, "pick": cmd_pick, "propose": cmd_propose,
          "sessions": cmd_sessions, "resolve": cmd_resolve, "talk": cmd_talk, "wait": cmd_wait,
          "close": cmd_close}.get(argv[0])
    if not fn:
        print(__doc__)
        return 2
    return fn(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
