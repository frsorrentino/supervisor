#!/usr/bin/env python3
"""Le righe che la directory di Anthropic leggeva come «cambia il codice che un gestore di pacchetti installa o
esegue» (LAUNCHER_REGISTRY_REDIRECT) sono scritte in chiaro, e fanno quello che facevano (23/11/2026,
supervisor-directory-pacchetti-23-11).

Il portale non dà la riga. Per remote/cm-remote.ps1 dice «Write the words out instead of the brace list»; per
remote/cm-remote.sh e scripts/cm-attach.sh «Write the command's verb and setting name as plain words, and join each
option to its value with =». Le righe sospette: le variabili del lavoro lette da env.txt con il nome in una variabile
(`$k, $v = ...` tra graffe, `export "$k=$v"`), deps.txt letto allo stesso modo, la lettura di ~/.npmrc con le opzioni
staccate dai valori, e in cm-attach il binario di tmux preso da una variabile con le opzioni staccate dai valori.

DP1 cm-remote.ps1: nessuna lista tra graffe del tipo `{ $k, $v = ... }`; env.txt e deps.txt letti per nome
DP2 cm-remote.sh: niente `IFS='='` con `export "$k=$v"`; deps.txt letto con nomi in chiaro
DP3 la lettura di ~/.npmrc (sh) ha le opzioni lunghe unite al valore con =
DP4 cm-attach.sh: tmux scritto per nome, niente `${CM_TMUX_BIN`, ogni `-t` unito al suo valore
DP5 run-job fa quello che faceva: le variabili di env.txt (anche con = nel valore e vuote) arrivano al comando,
    le dipendenze girano una volta e finiscono in cache, il secondo lavoro con la stessa chiave le collega

Seconda passata (23/11/2026): il portale sulla 0.8.198 da' ancora due righe, in cm-remote.ps1 e in cm-remote.sh
(cm-attach.sh e' passato). Restavano i lanciatori generici delle versioni degli strumenti: `Ver $exe $arg` in
PowerShell (con `cmd /c` e l'eseguibile in una variabile, dentro la lista `tools = @{ ... }`) e `ver` in sh (`"$@"`),
piu' tar e la lettura di gh con le opzioni staccate dai valori.
DP6 cm-remote.ps1: niente funzione Ver, niente `cmd /c` con l'eseguibile in una variabile; ogni strumento chiamato per
    nome (node.exe --version, ...) e la lista tools costruita riga per riga
DP7 cm-remote.sh: niente `"$@"` che lancia un comando qualsiasi; ogni strumento chiamato per nome
DP8 cm-remote.sh: tar con le opzioni lunghe unite al valore (--file=, --directory=), la lettura di gh con le opzioni lunghe
DP9 probe (sh) da' le stesse versioni di prima: quelle che gli strumenti stampano chiamati a mano; unpack e pack vanno
"""
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

REMOTE = T.ROOT / "supervisor" / "remote"
ps1 = (REMOTE / "cm-remote.ps1").read_text(encoding="utf-8")
sh = (REMOTE / "cm-remote.sh").read_text(encoding="utf-8")
att = (T.SCRIPTS / "cm-attach.sh").read_text(encoding="utf-8")


def code(text):
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]


brace = [ln for ln in code(ps1) if re.search(r"\{\s*\$\w+\s*,\s*\$\w+\s*=", ln)]
T.check("DP1 cm-remote.ps1: no brace list `{ $k, $v = ... }`", not brace, "\n".join(brace))
T.check("DP1 cm-remote.ps1: env.txt and deps.txt read by name", "$name" in ps1 and "$dp['key']" not in ps1, "")

bad = [ln for ln in code(sh) if "IFS='='" in ln or 'export "$k=$v"' in ln or "s/^key=//p" in ln]
T.check("DP2 cm-remote.sh: env.txt and deps.txt read with plain names", not bad, "\n".join(bad))

npm = [ln for ln in code(sh) if ".npmrc" in ln and "addc" in ln]
T.check("DP3 cm-remote.sh: ~/.npmrc read with long options joined by =",
        len(npm) == 1 and "--regexp=_authToken" in npm[0] and " -q" not in npm[0], "\n".join(npm))

lines = code(att)
var_bin = [ln for ln in lines if "CM_TMUX_BIN" in ln]
loose_t = [ln for ln in lines if re.search(r"-t \"", ln)]
T.check("DP4 cm-attach.sh: tmux by name, every -t joined to its value", not var_bin and not loose_t,
        "\n".join(var_bin + loose_t))

ps_code = code(ps1)
launch = [ln for ln in ps_code if re.search(r"function Ver\b|\bVer \w+ '|cmd /c \"`\"\$", ln)]
T.check("DP6 cm-remote.ps1: no generic launcher (Ver, cmd /c with the program in a variable)", not launch, "\n".join(launch))
T.check("DP6 cm-remote.ps1: each tool called by name, the tools list built line by line",
        all(f"{x} --version" in ps1 for x in ("node.exe", "python.exe", "git.exe", "tar.exe", "claude.exe"))
        and "ffmpeg.exe -version" in ps1 and not re.search(r"tools = @\{", ps1), "")
sh_code = code(sh)
dollar_at = [ln for ln in sh_code if '"$@" 2>&1' in ln or re.search(r"\$\(ver ", ln)]
T.check("DP7 cm-remote.sh: no generic launcher (\"$@\"), each tool called by name", not dollar_at
        and all(x in sh for x in ("node --version", "python3 --version", "git --version", "tmux -V", "claude --version",
                                  "ffmpeg -version", "tar --version")), "\n".join(dollar_at))
short_tar = [ln for ln in sh_code if re.search(r"\btar -[a-zA-Z]", ln)]
gh = [ln for ln in sh_code if "hosts.yml" in ln and "addc" in ln]
T.check("DP8 cm-remote.sh: tar with long options joined by =, gh read with long options",
        not short_tar and "--directory=src" in sh and len(gh) == 1 and "--regexp=oauth_token" in gh[0], "\n".join(short_tar + gh))

root = Path(T.tmpdir())


def job(jid, env_lines):
    d = root / "jobs" / jid
    (d / "src").mkdir(parents=True)
    (d / "cmd.txt").write_text('printf "A=%s|E=%s|S=%s\\n" "$A" "${E-unset}" "$S"; ls deps-dir\n')
    (d / "env.txt").write_text(env_lines)
    (d / "deps.txt").write_text("key=k1\ndir=deps-dir\ncmd=mkdir deps-dir && touch deps-dir/installed && echo deps-ran\n")
    t = time.time()
    r = subprocess.run(["sh", str(REMOTE / "cm-remote.sh"), str(root), "run-job", jid], capture_output=True, text=True, timeout=60)
    return r, (d / "log.txt").read_text() if (d / "log.txt").exists() else "", (d / "status.json").read_text(), time.time() - t


r1, log1, st1, _ = job("j1", "A=b=c\nE=\nS=con spazi\n")
T.check("DP5 run-job: env.txt values reach the command (= in the value, empty, spaces), deps run once",
        "A=b=c|E=|S=con spazi" in log1 and "deps-ran" in log1 and "installed" in log1 and '"state":"done"' in st1
        and (root / "cache" / "deps" / "k1" / ".ok").exists(), f"{r1.stdout}{r1.stderr}\n{log1}\n{st1}")
r2, log2, st2, _ = job("j2", "A=x\n")
T.check("DP5 the second job with the same key links the cached deps without running them",
        "A=x|E=unset|" in log2 and "deps-ran" not in log2 and "installed" in log2 and '"state":"done"' in st2,
        f"{r2.stdout}{r2.stderr}\n{log2}\n{st2}")

import json as _json  # noqa: E402
import shutil  # noqa: E402

pr = subprocess.run(["sh", str(REMOTE / "cm-remote.sh"), str(root / "probe"), "probe"], capture_output=True, text=True, timeout=120)
tools = _json.loads(pr.stdout.split("@@CM-JSON@@")[-1].strip().splitlines()[0])["tools"] if "@@CM-JSON@@" in pr.stdout else {}


def by_hand(*argv):
    if not shutil.which(argv[0]):
        return None
    out = subprocess.run(list(argv), capture_output=True, text=True).stdout.splitlines() or [""]
    m = re.search(r"[0-9]+(\.[0-9]+)+", out[0])
    return m.group(0) if m else None


want = {"node": by_hand("node", "--version"), "git": by_hand("git", "--version"), "tar": by_hand("tar", "--version"),
        "python": by_hand("python3", "--version")}
T.check("DP9 probe (sh): the tools' versions are the ones they print by hand",
        bool(tools) and all(tools.get(k) == v for k, v in want.items()) and set(tools) >= {"node", "ffmpeg", "python", "git", "tmux", "claude", "tar", "chrome"},
        f"{tools} {want} {pr.stderr[-300:]}")

# unpack e pack: tar con le opzioni lunghe fa quello che faceva
jd = root / "jobs" / "jp"
(jd / "pk" / "sub").mkdir(parents=True)
(jd / "pk" / "sub" / "a.txt").write_text("uno")
subprocess.run(["tar", "-cf", str(jd / "src.tar"), "-C", str(jd / "pk"), "sub"], check=True)
subprocess.run(["tar", "-cf", str(jd / "bundle.tar"), "-C", str(jd), "src.tar"], check=True)
up = subprocess.run(["sh", str(REMOTE / "cm-remote.sh"), str(root), "unpack", "jp"], capture_output=True, text=True, timeout=60)
pk = subprocess.run(["sh", str(REMOTE / "cm-remote.sh"), str(root), "pack", "jp", "sub"], capture_output=True, text=True, timeout=60)
lst = subprocess.run(["tar", "-tf", str(jd / "out.tar")], capture_output=True, text=True).stdout.split()
T.check("DP9 unpack extracts src.tar into src/, pack puts the asked paths in out.tar",
        (jd / "src" / "sub" / "a.txt").read_text() == "uno" and '"ok":true' in up.stdout and '"ok":true' in pk.stdout and "sub/a.txt" in lst,
        up.stdout + up.stderr + pk.stdout + pk.stderr + str(lst))

T.rm(str(root))
T.finish()
