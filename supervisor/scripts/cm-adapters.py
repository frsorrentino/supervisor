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

Il lavoro vero lo fa l'aiutante remoto (remote/cm-remote.sh o remote/cm-remote.ps1), versionato col plugin: stessi
verbi, stesso protocollo (una riga sentinella, poi UNA riga JSON sullo stdout). L'adattatore sceglie solo come
chiamarlo e come copiare i file.

Trasporto SSH: opzioni sempre da riga di comando, ~/.ssh/config non si tocca; known_hosts dedicato in state_dir con
StrictHostKeyChecking=yes (la chiave si fissa al primo `host add`, poi un cambio fallisce chiuso); connessione
riusata (ControlMaster) cosi' un sondaggio costa poco. Prove: CM_SSH_BIN e CM_SCP_BIN al posto di ssh e scp.
"""
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
REMOTE = PLUGIN / "remote"
SENT = "@@CM-JSON@@"
HELPER_DIR = ".claude-master-remote"   # nella home remota
HELPER_FILES = {"posix": ["cm-remote.sh", "cm-bench.js", "cm-webgl.html"],
                "windows": ["cm-remote.ps1", "cm-bench.js", "cm-webgl.html"]}
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


def local_helper_shas(family):
    return {f: sha256_file(REMOTE / f) for f in HELPER_FILES[family]}


def clixml_text(err):
    """Il testo leggibile di uno stderr CLIXML di PowerShell (i blocchi <S S="Error">, con _x000D__x000A_ a capo)."""
    if "#< CLIXML" not in err:
        return err.strip()
    parts = re.findall(r'<S S="Error">(.*?)</S>', err, re.S)
    t = "".join(parts).replace("_x000D__x000A_", "\n").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return t.strip()


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


# ------------------------------------------------------------------ trasporto
class SSH:
    def __init__(self, alias, state_dir, connect_timeout=4, first_contact=False):
        self.alias = alias
        self.state_dir = Path(state_dir)
        self.connect_timeout = int(connect_timeout)
        self.first_contact = first_contact
        self.ssh_bin = os.environ.get("CM_SSH_BIN") or "ssh"
        self.scp_bin = os.environ.get("CM_SCP_BIN") or "scp"

    def known_hosts(self):
        return self.state_dir / "known_hosts"

    def control_dir(self):
        """Il socket di ControlMaster deve stare sotto ~104 byte (limite dei socket Unix, macOS il piu' stretto): con
        uno state_dir lungo si ripiega su /tmp (spike del 27/09/2026: 'ControlPath too long')."""
        d = self.state_dir / "ssh"
        if len(str(d)) + 42 > 100:
            d = Path(tempfile.gettempdir()) / f"cm-ssh-{os.getuid()}"
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        return d

    def opts(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={self.connect_timeout}",
                "-o", "ControlMaster=auto", "-o", f"ControlPath={self.control_dir()}/%C", "-o", "ControlPersist=60",
                "-o", "ServerAliveInterval=2", "-o", "ServerAliveCountMax=2",
                "-o", f"UserKnownHostsFile={self.known_hosts()}", "-o", "GlobalKnownHostsFile=/dev/null",
                "-o", "StrictHostKeyChecking=" + ("accept-new" if self.first_contact else "yes"),
                "-o", "LogLevel=ERROR"]

    def _check(self, rc, err):
        if rc == 255:
            if HOSTKEY.search(err):
                raise HostError("hostkey", err.strip().splitlines()[-1] if err.strip() else "")
            if UNREACHABLE.search(err) or not err.strip():
                raise HostError("unreachable", err.strip().splitlines()[-1] if err.strip() else "")

    def run(self, remote_cmd, timeout=60, input=None):
        argv = [self.ssh_bin] + self.opts() + [self.alias, remote_cmd]
        try:
            # senza input, stdin chiuso: ereditato, ssh passava al comando remoto le risposte che `host doctor`
            # aspettava dalla tastiera (O9c, visto con le suite in WSL il 13/10)
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input,
                               stdin=subprocess.DEVNULL if input is None else None, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            raise HostError("unreachable", f"timeout {timeout}s")
        self._check(p.returncode, p.stderr)
        return p

    def put(self, local, remote, timeout=3600):
        argv = [self.scp_bin, "-q"] + self.opts() + [str(local), f"{self.alias}:{remote}"]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        self._check(p.returncode, p.stderr)
        if p.returncode != 0:
            raise HostError("remote", f"scp put {local}: {p.stderr.strip()}")

    def get(self, remote, local, timeout=3600):
        argv = [self.scp_bin, "-q"] + self.opts() + [f"{self.alias}:{remote}", str(local)]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        self._check(p.returncode, p.stderr)
        if p.returncode != 0:
            raise HostError("remote", f"scp get {remote}: {p.stderr.strip()}")

    def close(self):
        subprocess.run([self.ssh_bin] + self.opts() + ["-O", "exit", self.alias], capture_output=True, timeout=10)


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
        want = local_helper_shas(self.family)
        have = self.helper_version()
        return all(have.get(f) == s for f, s in want.items()), have

    def probe(self, bench=False, webgl=False):
        args = (["--bench"] if bench else []) + (["--webgl"] if webgl else [])
        return self.call("probe", *args, timeout=240 if (bench or webgl) else 90)

    def status(self):
        return self.call("status", timeout=60)

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
    """L'host `local`: l'aiutante gira dalla copia del plugin, niente installazione e niente trasporto."""
    kind = "linux-tmux"
    remote = False

    def helper(self, verb, *args, timeout=120):
        try:
            p = subprocess.run(["sh", str(REMOTE / "cm-remote.sh"), self.root, verb, *args], capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise HostError("remote", f"{verb} timeout {timeout}s")
        d = parse_helper(p.stdout)
        if d is None:
            raise HostError("helper", p.stderr.strip()[-300:])
        return d

    def put(self, local, remote_abs):
        shutil.copyfile(local, remote_abs)

    def get(self, remote_abs, local):
        shutil.copyfile(remote_abs, local)

    def exec(self, cmd, timeout=30):
        p = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def install_helper(self):
        return True

    def helper_ok(self):
        return True, local_helper_shas("posix")


class SSHPosix(Adapter):
    """linux-tmux, macos-tmux e wsl raggiunti via ssh: l'aiutante e' cm-remote.sh in ~/.claude-master-remote."""
    kind = "linux-tmux"

    def __init__(self, name, host, cfg, first_contact=False):
        super().__init__(name, host, cfg)
        self.ssh = SSH((host.get("transport") or {}).get("ssh") or name, self.state_dir, self.timeout, first_contact)

    def helper(self, verb, *args, timeout=120):
        cmd = "sh ~/%s/cm-remote.sh %s" % (HELPER_DIR, " ".join(shlex.quote(a) for a in (self.root, verb, *args)))
        p = self.ssh.run(cmd, timeout=timeout)
        d = parse_helper(p.stdout)
        if d is None:
            raise HostError("helper", (p.stderr or p.stdout).strip()[-300:])
        return d

    def put(self, local, remote_abs):
        self.ssh.put(local, remote_abs)

    def get(self, remote_abs, local):
        self.ssh.get(remote_abs, local)

    def exec(self, cmd, timeout=30):
        p = self.ssh.run("sh -c " + shlex.quote(cmd), timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def install_helper(self):
        rc, _, err = self.exec(f"mkdir -p ~/{HELPER_DIR}")
        if rc != 0:
            raise HostError("remote", err)
        for f in HELPER_FILES["posix"]:
            self.ssh.put(REMOTE / f, f"{HELPER_DIR}/{f}")
        return True


class MacOS(SSHPosix):
    kind = "macos-tmux"


class WSL(SSHPosix):
    kind = "wsl"


class WindowsNative(Adapter):
    """Windows nativo via OpenSSH: ogni chiamata e' `powershell -EncodedCommand` (base64 UTF-16: nessun problema di
    virgolette qualunque sia la shell di sshd, cmd o PowerShell); l'aiutante e' cm-remote.ps1 in
    %USERPROFILE%\\.claude-master-remote; i file solo con scp."""
    kind = "windows-native"
    family = "windows"

    def __init__(self, name, host, cfg, first_contact=False):
        super().__init__(name, host, cfg)
        self.ssh = SSH((host.get("transport") or {}).get("ssh") or name, self.state_dir, self.timeout, first_contact)

    @staticmethod
    def encoded(script):
        pre = "$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false);"
        b = base64.b64encode((pre + script).encode("utf-16-le")).decode()
        return f"powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {b}"

    @staticmethod
    def q(s):
        return "'" + str(s).replace("'", "''") + "'"

    def helper(self, verb, *args, timeout=120):
        script = "& (Join-Path $env:USERPROFILE %s) %s" % (self.q(HELPER_DIR + "\\cm-remote.ps1"),
                                                         " ".join(self.q(a) for a in (self.root, verb, *args)))
        p = self.ssh.run(self.encoded(script), timeout=timeout)
        d = parse_helper(p.stdout)
        if d is None:
            raise HostError("helper", clixml_text(p.stderr)[-300:] or p.stdout.strip()[-300:])
        return d

    @staticmethod
    def scp_path(p):
        return str(p).replace("\\", "/")

    def put(self, local, remote_abs):
        self.ssh.put(local, self.scp_path(remote_abs))

    def get(self, remote_abs, local):
        self.ssh.get(self.scp_path(remote_abs), local)

    def exec(self, cmd, timeout=30):
        p = self.ssh.run(self.encoded(cmd), timeout=timeout)
        return p.returncode, p.stdout, clixml_text(p.stderr)

    def install_helper(self):
        rc, _, err = self.exec(f"New-Item -ItemType Directory -Force -Path (Join-Path $env:USERPROFILE '{HELPER_DIR}') | Out-Null")
        if rc != 0:
            raise HostError("remote", err)
        for f in HELPER_FILES["windows"]:
            self.ssh.put(REMOTE / f, f"{HELPER_DIR}/{f}")
        return True


    # --- sessioni Claude (piano multi-PC 4.2, fase 2.1): chiamate da cm-rsession.py
    verbs = Adapter.verbs | {"launch_session"}
    # i byte della fotografia restano quelli di qui: con core.autocrlf input il commit «base» di la' normalizzava i
    # file CRLF in LF e le patch di ritorno non si applicavano piu' sulla base di qui (prova reale del 02/10, 00:45)
    SEED_GIT_CONFIG = {"core.autocrlf": "false"}

    def ps_json(self, script, timeout=120):
        """Esegue uno script e legge l'ULTIMA riga dello stdout come JSON (il resto e' rumore di git o di PowerShell)."""
        rc, out, err = self.exec(script, timeout=timeout)
        lines = [l for l in (out or "").splitlines() if l.strip().startswith("{")]
        try:
            d = json.loads(lines[-1]) if lines else None
        except ValueError:
            d = None
        if d is None:
            raise HostError("remote", (err or out or f"rc {rc}").strip()[-300:])
        if d.get("error"):
            raise HostError("remote", d["error"])
        return d

    def rpath(self, p):
        """Un percorso remoto per scp: %USERPROFILE%\\x diventa relativo alla home (scp parte da li')."""
        p = str(p)
        if p.upper().startswith("%USERPROFILE%"):
            p = p[len("%USERPROFILE%"):].lstrip("\\/")
        return p.replace("\\", "/")

    _PS_DIR = "$d = [Environment]::ExpandEnvironmentVariables(%s); $m = Join-Path $d '.cm-session.json';"

    def session_prepare(self, rdir):
        """{exists, marker, base, root, ahead}: la cartella c'e'? e' nostra (.cm-session.json)? quanti commit oltre la base."""
        return self.ps_json(self._PS_DIR % self.q(rdir) + r"""
$r = @{ exists = [bool](Test-Path $d); marker = $false }
if (Test-Path $m) {
  $j = Get-Content $m -Raw | ConvertFrom-Json
  $r.marker = $true; $r.base = $j.base; $r.root = $j.root
  $r.ahead = [int](git -C $d rev-list --count "$($j.root)..HEAD" 2>$null)
}
ConvertTo-Json -InputObject $r -Compress""")

    def session_seed(self, rdir, tar, sha, who):
        """La fotografia diventa un repository nuovo: estrazione, git init, commit «base», .cm-session.json (escluso)."""
        seed = self.rpath(rdir.rstrip("\\/") + ".cm-seed.tar")
        rc, _, err = self.exec(self._PS_DIR % self.q(rdir) + "New-Item -ItemType Directory -Force -Path (Split-Path -Parent $d) | Out-Null")
        if rc != 0:
            raise HostError("remote", err)
        self.put(tar, seed)
        name, email = who
        seedcfg = "\n".join(f"git -C $d config {k} {v}" for k, v in self.SEED_GIT_CONFIG.items())
        return self.ps_json(self._PS_DIR % self.q(rdir) + f"""
$t = [Environment]::ExpandEnvironmentVariables({self.q(rdir.rstrip(chr(92) + '/') + '.cm-seed.tar')})
if (-not (Test-Path $t)) {{ $t = Join-Path $env:USERPROFILE {self.q(seed)} }}
New-Item -ItemType Directory -Force -Path $d | Out-Null
tar -xf $t -C $d; if ($LASTEXITCODE) {{ ConvertTo-Json -Compress @{{ error = "tar rc $LASTEXITCODE" }}; exit 1 }}
Remove-Item -Force $t
git -C $d init -q
{seedcfg}
git -C $d config user.name {self.q(name or 'supervisor')}
git -C $d config user.email {self.q(email or 'supervisor@localhost')}
Add-Content -Path (Join-Path $d '.git\\info\\exclude') -Value '.cm-session.json'
git -C $d add -A
git -C $d commit -q -m {self.q('supervisor: base ' + sha + ' from the control machine')}
if ($LASTEXITCODE) {{ ConvertTo-Json -Compress @{{ error = "git commit rc $LASTEXITCODE" }}; exit 1 }}
$root = (git -C $d rev-parse HEAD).Trim()
$j = @{{ base = {self.q(sha)}; root = $root; created = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }}
[IO.File]::WriteAllText($m, (ConvertTo-Json -InputObject $j -Compress))
ConvertTo-Json -Compress @{{ ok = $true; root = $root }}""", timeout=600)

    # la fiducia nella cartella si scrive con node (c'e' su ogni host con Claude Code): PowerShell 5.1 riscrivendo
    # .claude.json ne cambierebbe la forma (profondita' 2 di default, array singoli); scrittura atomica con rename
    _TRUST_JS = (
        "const fs=require('fs'),p=require('path'),os=require('os');"
        "const f=p.join(os.homedir(),'.claude.json');const d=JSON.parse(fs.readFileSync(f,'utf8'));"
        "const k=process.argv[2].replace(/\\\\/g,'/');d.projects=d.projects||{};"
        "d.projects[k]=Object.assign({},d.projects[k]||{},{hasTrustDialogAccepted:true});"
        "if(!fs.existsSync(f+'.cm-bak'))fs.copyFileSync(f,f+'.cm-bak');"
        "const t=f+'.cm-tmp';fs.writeFileSync(t,JSON.stringify(d,null,2));fs.renameSync(t,f);"
        "console.log(JSON.stringify({ok:true,key:k}))")

    def session_trust(self, rdir):
        js = base64.b64encode(self._TRUST_JS.encode()).decode()
        return self.ps_json(self._PS_DIR % self.q(rdir) + f"""
$js = Join-Path $env:TEMP 'cm-trust.js'
[IO.File]::WriteAllText($js, [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{js}')))
node $js $d""")

    def session_start(self, name, rdir, args):
        """Un'attivita' Interactive sull'utente collegato al desktop: la console visibile su quel desktop e' il terminale
        di Claude Code (come CCwincompat). Senza nessuno collegato non parte: lo si dice."""
        def cq(a):
            a = str(a)
            return '"' + a.replace('"', '""') + '"' if re.search(r'[\s&|<>^()"]', a) else a
        line = "claude " + " ".join(cq(a) for a in args)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise HostError("remote", f"bad session name {name}")
        return self.ps_json(self._PS_DIR % self.q(rdir) + f"""
$u = (Get-CimInstance Win32_ComputerSystem).UserName
if (-not $u) {{ ConvertTo-Json -Compress @{{ error = 'nobody is signed in to the desktop: an interactive session cannot start' }}; exit 1 }}
$sd = Join-Path $env:USERPROFILE '{HELPER_DIR}\\sessions'
New-Item -ItemType Directory -Force -Path $sd | Out-Null
$cmdf = Join-Path $sd '{name}.cmd'
[IO.File]::WriteAllText($cmdf, "@echo off`r`ntitle {name}`r`ncd /d `"$d`"`r`n" + {self.q(line)} + "`r`n")
$a = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$cmdf`"" -WorkingDirectory $d
$pr = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'cm-session-{name}' -TaskPath '\\claude-master\\' -Action $a -Principal $pr -Settings $s -Force -ErrorAction Stop | Out-Null
Start-ScheduledTask -TaskPath '\\claude-master\\' -TaskName 'cm-session-{name}' -ErrorAction Stop
ConvertTo-Json -Compress @{{ ok = $true; user = $u; task = 'cm-session-{name}' }}""")

    def session_patches(self, rdir, local_mbox):
        """{count, dirty}: i commit oltre la base come serie di patch, scritti byte per byte (cmd /c: la pipe di
        PowerShell 5.1 ricodificherebbe il testo), poi copiati qui con scp."""
        out_rel = f"{HELPER_DIR}/out/{hashlib.sha1(rdir.encode()).hexdigest()[:12]}.mbox"
        d = self.ps_json(self._PS_DIR % self.q(rdir) + f"""
if (-not (Test-Path $m)) {{ ConvertTo-Json -Compress @{{ error = "no .cm-session.json in $d" }}; exit 1 }}
$j = Get-Content $m -Raw | ConvertFrom-Json
$n = [int](git -C $d rev-list --count "$($j.root)..HEAD")
$dirty = @(git -C $d status --porcelain | Where-Object {{ $_ }}).Count
if ($n -gt 0) {{
  $o = Join-Path $env:USERPROFILE {self.q(out_rel.replace('/', chr(92)))}
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $o) | Out-Null
  cmd /c "git -C `"$d`" format-patch --stdout $($j.root)..HEAD > `"$o`""
}}
ConvertTo-Json -Compress @{{ count = $n; dirty = $dirty }}""", timeout=300)
        if d.get("count"):
            self.get(out_rel, local_mbox)
        return d

    # --- talk, wait e close verso le sessioni di questo host (fase 2.2): tutto in node (cm-session.js), passato in
    # base64 a ogni chiamata come il testo: niente da installare, nessuna virgoletta persa fra PowerShell e node
    def node_session(self, *args, timeout=120):
        js = base64.b64encode((REMOTE / "cm-session.js").read_bytes()).decode()
        argv = " ".join(self.q(a) for a in args)
        return self.ps_json(f"""
$js = Join-Path $env:TEMP 'cm-session.js'
[IO.File]::WriteAllText($js, [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{js}')))
node $js {argv}""", timeout=timeout)

    def session_post(self, name, text, from_name, from_addr):
        b64 = base64.b64encode(text.encode()).decode()
        return self.node_session("post", name, b64, from_name, from_addr or "uds:")

    def session_read(self, name, offset, last=False):
        return self.node_session("read", name, str(offset), *(["last"] if last else []))

    def session_tail(self, name, offset, max_bytes=2 * 1024 * 1024):
        """09/10: {sid, cwd, status, alive, size, offset, data} — i byte del transcript dall'offset, in base64."""
        return self.node_session("tail", name, str(int(offset)), str(int(max_bytes)), timeout=180)

    def file_size(self, path):
        """10/10 (dal telefono: un file di una sessione su win «missing or unreadable»): i byte di un file di la',
        -1 se non c'e' o non e' un file."""
        d = self.ps_json(f"""
$p = {self.q(path)}
if (Test-Path -LiteralPath $p -PathType Leaf) {{ ConvertTo-Json -Compress @{{ size = (Get-Item -LiteralPath $p).Length }} }}
else {{ ConvertTo-Json -Compress @{{ size = -1 }} }}""")
        return int(d.get("size", -1))

    def file_get(self, path, local):
        """10/10: un file di la' (percorso assoluto, D:\\… o D:/…) copiato qui con scp."""
        self.ssh.get("/" + str(path).replace("\\", "/").lstrip("/"), local)

    def file_put(self, local, path):
        """10/10 (dal telefono: un allegato per una sessione su win): un file di qui scritto la' (percorso assoluto),
        creando la cartella."""
        parent = re.split(r"[\\/](?=[^\\/]*$)", str(path))[0]
        rc, _, err = self.exec(f"New-Item -ItemType Directory -Force -Path {self.q(parent)} | Out-Null")
        if rc != 0:
            raise HostError("remote", err.strip()[-200:] or f"mkdir {parent}")
        self.ssh.put(local, "/" + str(path).replace("\\", "/").lstrip("/"))

    def session_console(self, name, keys=None, timeout=90):
        """09/10: lo schermo della console della sessione (e prima i tasti, se ci sono), via cm-console.ps1 lanciato
        come attivita' Interactive nella sessione del desktop: da sshd AttachConsole non ci arriva. Tasti: «Up»,
        «Down», «Enter», «Esc», «text:…», «wait:ms». → {pid, screen}."""
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise HostError("remote", f"bad session name {name}")
        ps = base64.b64encode((REMOTE / "cm-console.ps1").read_bytes()).decode()
        kb = base64.b64encode(json.dumps(list(keys or [])).encode()).decode()
        tag = uuid.uuid4().hex[:10]
        d = self.ps_json(f"""
$R = Join-Path $env:USERPROFILE '{HELPER_DIR}'
New-Item -ItemType Directory -Force -Path (Join-Path $R 'out') | Out-Null
$ps = Join-Path $R 'cm-console.ps1'
[IO.File]::WriteAllText($ps, [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{ps}')))
$o = Join-Path $R 'out\console-{tag}.json'
$u = (Get-CimInstance Win32_ComputerSystem).UserName
if (-not $u) {{ ConvertTo-Json -Compress @{{ error = 'nobody is signed in to the desktop: the console cannot be reached' }}; exit 1 }}
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ps`" -Name {name} -Out `"$o`" -KeysB64 {kb}"
$pr = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'cm-console-{tag}' -TaskPath '\claude-master\' -Action $a -Principal $pr -Force -ErrorAction Stop | Out-Null
Start-ScheduledTask -TaskPath '\claude-master\' -TaskName 'cm-console-{tag}' -ErrorAction Stop
for ($i = 0; $i -lt 240 -and -not (Test-Path $o); $i++) {{ Start-Sleep -Milliseconds 250 }}
Start-Sleep -Milliseconds 200
Unregister-ScheduledTask -TaskPath '\claude-master\' -TaskName 'cm-console-{tag}' -Confirm:$false -ErrorAction SilentlyContinue
if (Test-Path $o) {{ (Get-Content $o -Raw).Trim(); Remove-Item -Force $o }} else {{ ConvertTo-Json -Compress @{{ error = 'console: no answer in 60 s' }} }}""", timeout=timeout)
        return {"pid": d.get("pid"), "screen": base64.b64decode(d.get("screen_b64") or "").decode("utf-8", "replace")}

    def session_close(self, name, force=False):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise HostError("remote", f"bad session name {name}")
        d = self.node_session("close", name, *(["force"] if force else []))
        self.exec(f"Unregister-ScheduledTask -TaskPath '\\claude-master\\' -TaskName 'cm-session-{name}' -Confirm:$false "
                  f"-ErrorAction SilentlyContinue; Remove-Item -Force (Join-Path $env:USERPROFILE '{HELPER_DIR}\\sessions\\{name}.cmd') "
                  f"-ErrorAction SilentlyContinue")
        return d

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
    """L'adattatore di un host dichiarato (o di `local`)."""
    if name == "local":
        a = LocalPosix(name, host or {}, cfg)
        if os.uname().sysname == "Darwin":
            a.kind = "macos-tmux"
        return a
    kind = (host or {}).get("kind") or ""
    tr = (host or {}).get("transport") or {}
    if kind == "cloud" or tr.get("cloud"):
        return Cloud(name, host, cfg)
    cls = {"windows-native": WindowsNative, "macos-tmux": MacOS, "wsl": WSL}.get(kind, SSHPosix)
    return cls(name, host, cfg, first_contact=first_contact)


def detect_kind(name, host, cfg, first_contact=False):
    """Il tipo di un host ssh quando l'utente non l'ha dato: `uname -s` risponde su POSIX; su Windows risponde la
    PowerShell (o cmd) di sshd con un errore, e `$PSVersionTable` si'. Nessuna scrittura."""
    tmp = SSHPosix(name, host, cfg, first_contact=first_contact)
    p = tmp.ssh.run("uname -s 2>/dev/null || echo WINDOWS", timeout=30)
    out = (p.stdout or "").strip()
    if out.startswith("Linux"):
        p2 = tmp.ssh.run("grep -qi microsoft /proc/version && echo wsl || echo linux", timeout=30)
        return "wsl" if "wsl" in p2.stdout else "linux-tmux"
    if out.startswith("Darwin"):
        return "macos-tmux"
    p3 = tmp.ssh.run(WindowsNative.encoded("Write-Output ('CMWIN ' + $PSVersionTable.PSVersion.Major)"), timeout=30)
    if "CMWIN" in p3.stdout:
        return "windows-native"
    raise HostError("remote", f"unknown kind: {out[:80]} {clixml_text(p3.stderr)[:120]}")
