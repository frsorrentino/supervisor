#!/usr/bin/env python3
"""Adattatori degli host di supervisor (piano docs/plans/2026-09-27-multi-pc.md, 2.1-2.2).

Un'interfaccia sola per ogni tipo di host. Il resto di supervisor (host, scheduler, offload, sondatore) usa solo
questi verbi e non sa se parla con la macchina locale, con un Linux via ssh o con un Windows nativo:

  probe(bench, webgl)   misure (1.2) in un dict
  exec(cmd, timeout)    comando breve e sincrono nella shell nativa: (rc, stdout, stderr)
  status()              snapshot: carico, RAM, disco, lavori, registro peer delle sessioni, file quota
  sync(job, build)      porta sull'host sorgente, asset mancanti e file di controllo; la cartella remota
  detach(job)           lancia il lavoro in modo che sopravviva al trasporto; id remoto
  fetch(job, paths, dest)  riporta i risultati, con lo sha256 remoto di ogni file
  cancel(job) · clean(job)

Edizione per directory (26/11/2026, supervisor-directory-pacchetti-23-11): questa copia non ha remote/ (l'aiutante
remoto e i suoi script) ne' gli adattatori ssh e Windows nativo. Restano `local` e cloud; ogni verbo che servirebbe
all'aiutante, e ogni altro host, risponde HostError("refused") con EDITION_NOTE. Il plugin completo sta nel
marketplace fsorrentino.

"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

# 24/11/2026 (supervisor-lancio-ambiente-24-11): le variabili che Claude Code mette nei suoi figli; copia di CM_CHILD_ENV in cm-lib.sh
CHILD_ENV = tuple("CLAUDECODE CLAUDE_CODE_BRIDGE_SESSION_ID CLAUDE_CODE_CHILD_NOTES_DIR CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_EFFORT_LEVEL CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_CODE_MESSAGING_TOKEN CLAUDE_CODE_SESSION_ATTENDED CLAUDE_CODE_SESSION_ID CLAUDE_CODE_SSE_PORT CLAUDE_EFFORT CLAUDE_ENV_FILE CLAUDE_PID CLAUDE_PLUGIN_DATA CLAUDE_PLUGIN_ROOT CLAUDE_PROJECT_DIR".split())

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
SENT = "@@CM-JSON@@"
HELPER_DIR = ".claude-master-remote"   # nella home remota (solo per i messaggi)
EDITION_NOTE = "directory edition: other hosts and the remote helper are in the full plugin (marketplace fsorrentino)"
KINDS = ("linux-tmux", "macos-tmux", "windows-native", "wsl", "cloud")
UNREACHABLE = re.compile(r"timed out|No route to host|Connection refused|Could not resolve|Network is unreachable|"
                         r"Connection closed by|Connection reset|kex_exchange_identification|Host is down", re.I)
HOSTKEY = re.compile(r"Host key verification failed|REMOTE HOST IDENTIFICATION HAS CHANGED|host key .* is not known", re.I)


class HostError(Exception):
    """Errore di un host, con un codice che i messaggi traducono: unreachable, hostkey, helper, remote, refused."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()




def parse_helper(stdout):
    """Il JSON dopo la sentinella; None se manca (aiutante assente, shell rotta)."""
    if SENT not in stdout:
        return None
    tail = stdout.split(SENT, 1)[1].strip().splitlines()
    if not tail:
        return None
    try:
        return json.loads(tail[0])
    except ValueError:
        return None


# ------------------------------------------------------------------ adattatori
class Adapter:
    kind = ""
    family = "posix"
    remote = True
    verbs = {"probe", "exec", "status", "sync", "detach", "fetch", "cancel", "clean"}

    def __init__(self, name, host, cfg):
        self.name, self.host, self.cfg = name, host or {}, cfg
        sch = cfg.get("scheduler") or {}
        self.root = self.host.get("remote_root") or (sch.get("remote_root") or {}).get(self.family) or "~/claude-work"
        self.state_dir = Path(os.path.expanduser(cfg.get("state_dir") or "~/.local/state/cc-supervisor"))
        if os.environ.get("CM_HOME") and str(cfg.get("state_dir", "")).startswith("~"):
            self.state_dir = Path(os.environ["CM_HOME"]) / str(cfg["state_dir"])[2:]
        self.timeout = int(sch.get("ssh_connect_timeout_s", 4))

    # --- da implementare per tipo
    def helper(self, verb, *args, timeout=120):
        raise NotImplementedError

    def put(self, local, remote_abs):
        raise NotImplementedError

    def get(self, remote_abs, local):
        raise NotImplementedError

    def exec(self, cmd, timeout=30):
        raise NotImplementedError

    def install_helper(self):
        raise NotImplementedError

    # --- verbi comuni, uguali per tutti (il protocollo dell'aiutante e' lo stesso)
    def call(self, verb, *args, timeout=120):
        d = self.helper(verb, *args, timeout=timeout)
        if not d.get("ok"):
            raise HostError("remote", d.get("error") or verb)
        return d

    def helper_version(self):
        try:
            return self.helper("version", timeout=30).get("files") or {}
        except HostError as e:
            if e.code in ("unreachable", "hostkey"):
                raise
            return {}

    def helper_ok(self):
        return False, {}

    def probe(self, bench=False, webgl=False):
        args = (["--bench"] if bench else []) + (["--webgl"] if webgl else [])
        return self.call("probe", *args, timeout=240 if (bench or webgl) else 90)

    def status(self):
        # 1.75: the disk reported is the one of the sessions root (D: on win), not of the job root
        sroot = (self.host.get("sessions") or {}).get("root")
        return self.call("status", *([str(sroot)] if sroot else []), timeout=60)

    def sync(self, job, build_bundle):
        """3.2: cartella del lavoro, manifesto degli asset, solo gli sha che mancano, un bundle, estrazione.
        `build_bundle(missing_shas)` prepara il bundle locale e ne restituisce il percorso (lo fa cm-offload)."""
        prep = self.call("prepare", job["id"])
        rdir, sep = prep["dir"], prep.get("sep") or "/"
        job["remote_dir"], job["remote_sep"] = rdir, sep
        missing = []
        if job.get("manifest"):
            self.put(job["manifest"], rdir + sep + "manifest.txt")
            missing = self.call("missing", job["id"]).get("missing") or []
        bundle = build_bundle(missing)
        self.put(bundle, rdir + sep + "bundle.tar")
        self.call("unpack", job["id"], timeout=1800)
        return {"dir": rdir, "missing": missing, "free_gb": prep.get("free_gb")}

    def detach(self, job):
        return self.call("detach", job["id"]).get("id")

    def fetch(self, job, paths, dest):
        d = self.call("pack", job["id"], *paths, timeout=1800)
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        tar_local = dest.parent / f".cm-out-{job['id']}.tar"
        self.get(d["tar"], tar_local)
        subprocess.run(["tar", "-xf", str(tar_local), "-C", str(dest)], check=True)
        tar_local.unlink()
        seen, out = set(), []
        for f in d.get("files") or []:   # un aiutante vecchio poteva elencare due volte lo stesso file
            if f["path"] not in seen:
                seen.add(f["path"])
                out.append(f)
        return out

    def cancel(self, job):
        return self.call("cancel", job["id"])

    def clean(self, job=None, older_days=None):
        if older_days is not None:
            return self.call("clean", "--older", str(older_days))
        return self.call("clean", job["id"])


class LocalPosix(Adapter):
    """L'host `local`. Nell'edizione per directory l'aiutante (remote/cm-remote.sh) non c'e': misure, lavori e
    sessioni su un altro host restano del plugin completo, e ogni verbo lo dice con HostError("refused")."""
    kind = "linux-tmux"
    remote = False

    def helper(self, verb, *args, timeout=120):
        raise HostError("refused", EDITION_NOTE)

    def put(self, local, remote_abs):
        shutil.copyfile(local, remote_abs)

    def get(self, remote_abs, local):
        shutil.copyfile(remote_abs, local)

    def exec(self, cmd, timeout=30):
        p = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def install_helper(self):
        raise HostError("refused", EDITION_NOTE)

    def helper_ok(self):
        return False, {}


class Cloud(Adapter):
    """Le sessioni cloud di Claude Code: niente exec ne' sync (ogni lavoro li' consumerebbe token). Le sessioni
    arrivano nella v2 (2.5); qui l'adattatore esiste perche' lo scheduler lo scarti con la sua regola."""
    kind = "cloud"
    verbs = {"launch_session", "post", "status"}

    def helper(self, verb, *args, timeout=120):
        raise HostError("refused", f"cloud: {verb}")

    def status(self):
        return {"ok": True, "at": int(time.time()), "jobs": {}, "sessions": [], "quota": []}


def adapter_for(name, host, cfg, first_contact=False):
    """L'adattatore di un host dichiarato (o di `local`). Nell'edizione per directory solo `local` e cloud: gli
    altri host (ssh, Windows nativo) restano del plugin completo."""
    if name == "local":
        a = LocalPosix(name, host or {}, cfg)
        if os.uname().sysname == "Darwin":
            a.kind = "macos-tmux"
        return a
    kind = (host or {}).get("kind") or ""
    tr = (host or {}).get("transport") or {}
    if kind == "cloud" or tr.get("cloud"):
        return Cloud(name, host, cfg)
    raise HostError("refused", EDITION_NOTE)


def detect_kind(name, host, cfg, first_contact=False):
    """Nell'edizione per directory nessun host ssh: il tipo non si misura."""
    raise HostError("refused", EDITION_NOTE)
