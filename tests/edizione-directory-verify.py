#!/usr/bin/env python3
"""L'edizione per directory (26/11/2026, supervisor-directory-pacchetti-23-11): la copia che va nella directory dei
plugin di Anthropic, ferma sul tag directory-0.8.217 preso da public v0.8.217.

Il portale bloccava Supervisor su «May change which code a package manager installs or runs» (LAUNCHER_REGISTRY_REDIRECT)
in remote/ e sui comandi codificati (powershell -EncodedCommand) che cm-adapters.py manda a un host Windows. Scelta
del maintainer: un'edizione senza quelle parti, tolte e non nascoste. Hook, bin/ e script restano come sono.

ED1 supervisor/remote/ non c'e'
ED2 cm-adapters.py: niente -EncodedCommand, niente adattatori ssh e Windows nativo
ED3 un host dichiarato che non sia cloud (ssh, Windows) → HostError("refused") con la nota dell'edizione; anche
    detect_kind; `local` e cloud si creano, e l'aiutante locale risponde refused senza cercare file
ED4 cm-attach.sh in chiaro: tmux scritto per nome, ogni -t unito al suo valore (come DP4 del plugin completo)
ED5 nessun file sotto supervisor/ cita ancora il percorso remote/ come codice da eseguire (PLUGIN / "remote", cm-remote.*)
"""
import importlib.util
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

PLUGIN = T.ROOT / "supervisor"
T.check("ED1 supervisor/remote/ is not in the edition", not (PLUGIN / "remote").exists(), "")

src = (T.SCRIPTS / "cm-adapters.py").read_text(encoding="utf-8")
code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
T.check("ED2 cm-adapters.py: no -EncodedCommand, no ssh or Windows adapters",
        "EncodedCommand" not in src and not any(re.match(r"class (SSH|SSHPosix|MacOS|WSL|WindowsNative)\b", ln) for ln in code), "")

spec = importlib.util.spec_from_file_location("cm_adapters_ed", T.SCRIPTS / "cm-adapters.py")
ad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ad)
cfg = {"state_dir": str(T.tmpdir())}
refused = []
for name, host in (("box", {"kind": "linux-tmux", "transport": {"ssh": "box"}}), ("win", {"kind": "windows-native"}),
                   ("mac", {"kind": "macos-tmux"})):
    try:
        ad.adapter_for(name, host, cfg)
    except ad.HostError as e:
        refused.append(e.code == "refused" and "directory edition" in e.detail)
try:
    ad.detect_kind("box", {}, cfg)
    dk = False
except ad.HostError as e:
    dk = e.code == "refused"
loc = ad.adapter_for("local", {}, cfg)
cl = ad.adapter_for("cl", {"kind": "cloud"}, cfg)
try:
    loc.status()
    ls = False
except ad.HostError as e:
    ls = e.code == "refused"
T.check("ED3 other hosts and detect_kind → refused with the edition note; local and cloud still built, the local helper refused",
        refused == [True, True, True] and dk and ls and loc.helper_ok() == (False, {}) and cl.kind == "cloud", f"{refused} {dk} {ls}")

att = [ln for ln in (T.SCRIPTS / "cm-attach.sh").read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
bad = [ln for ln in att if "CM_TMUX_BIN" in ln or re.search(r"-t \"", ln)]
T.check("ED4 cm-attach.sh in plain words: tmux by name, every -t joined to its value", not bad, "\n".join(bad))

hits = []
for p in PLUGIN.rglob("*"):
    if p.is_file() and p.suffix in (".py", ".sh", ".json", ".js", ".ps1"):
        for i, ln in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            s = ln.strip()
            if s.startswith("#"):
                continue
            if re.search(r"""PLUGIN\s*/\s*"remote"|["'/](cm-remote\.(sh|ps1)|cm-session\.js|cm-console\.ps1|cm-login\.js)["']""", s) and "edition" not in s:
                hits.append(f"{p.relative_to(PLUGIN)}:{i}: {s[:120]}")
T.check("ED5 no file under supervisor/ still runs something from remote/", not hits, "\n".join(hits[:20]))
T.finish()
