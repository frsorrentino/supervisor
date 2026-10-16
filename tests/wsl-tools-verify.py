#!/usr/bin/env python3
"""Verifica tools-wsl-tests.sh con ssh e scp finti: l'esito viene solo dall'ultima riga «WSL-TESTS …» di win.

WT1  WSL che non parte (l'errore di win in UTF-16, ssh esce 0, nessuna suite) → uscita 3, «WSL-TESTS NO RESULT»
     con il motivo; mai 0 (il 15/10/2026 un verde senza test)
WT2  «WSL-TESTS GREEN» → uscita 0;  WT3  «WSL-TESTS RED» → uscita 1
WT4  ssh interrotto (niente uscita, codice 255) → uscita 3 con il codice di ssh
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
fake_ssh = tmp / "ssh"
fake_ssh.write_text("""#!/usr/bin/env bash
case "$*" in *New-Item*) exit 0 ;; esac
case "$FAKE_WSL" in
  down) printf 'T\\0i\\0m\\0e\\0o\\0u\\0t\\0.\\0\\r\\0\\n\\0C\\0o\\0d\\0i\\0c\\0e\\0 \\0e\\0r\\0r\\0o\\0r\\0e\\0:\\0 \\0W\\0s\\0l\\0/\\0S\\0e\\0r\\0v\\0i\\0c\\0e\\0/\\0C\\0r\\0e\\0a\\0t\\0e\\0V\\0m\\0\\r\\0\\n\\0'; exit 0 ;;
  green) echo "-- tests/x-verify.py"; echo "1/1 OK"; echo "WSL-TESTS GREEN"; exit 0 ;;
  red) echo "-- tests/x-verify.py"; echo "rc 1"; echo "WSL-TESTS RED"; exit 1 ;;
  cut) exit 255 ;;
esac
""")
fake_scp = tmp / "scp"
fake_scp.write_text("#!/usr/bin/env bash\nexit 0\n")
for f in (fake_ssh, fake_scp):
    f.chmod(0o755)


# 16/10/2026: lo strumento impacchetta i file tracciati (git ls-files) — un repo minimo in tmp, perche' in WSL l'albero
# arriva senza .git e il test, puntato alla radice, usciva 128 «not a git repository»
repo = tmp / "repo"
(repo / "tests").mkdir(parents=True)
(repo / "tests" / "x-verify.py").write_text("print('1/1 OK')\n")
subprocess.run(["git", "init", "-q", str(repo)], check=True)
subprocess.run(["git", "-C", str(repo), "add", "tests/x-verify.py"], check=True)


def run(mode):
    env = dict(os.environ, WSL_TESTS_SSH=str(fake_ssh), WSL_TESTS_SCP=str(fake_scp), FAKE_WSL=mode)
    return subprocess.run(["bash", str(T.ROOT / "tools-wsl-tests.sh"), str(repo), "tests/x-verify.py"], capture_output=True, text=True, env=env, timeout=120)


r = run("down")
T.check("WT1 WSL that does not start (UTF-16 error, ssh exit 0, no suite) → exit 3, «WSL-TESTS NO RESULT» with the reason",
        r.returncode == 3 and "WSL-TESTS NO RESULT" in r.stderr and "Timeout." in r.stderr and "GREEN" not in r.stdout, f"{r.returncode} {r.stdout[-300:]} {r.stderr[-300:]}")
r = run("green")
T.check("WT2 «WSL-TESTS GREEN» on the last line → exit 0", r.returncode == 0 and "WSL-TESTS GREEN" in r.stdout, f"{r.returncode} {r.stderr[-300:]}")
r = run("red")
T.check("WT3 «WSL-TESTS RED» → exit 1", r.returncode == 1 and "WSL-TESTS RED" in r.stdout, f"{r.returncode} {r.stderr[-300:]}")
r = run("cut")
T.check("WT4 ssh cut with no output (exit 255) → exit 3, the ssh code in the reason", r.returncode == 3 and "ssh exit 255" in r.stderr, f"{r.returncode} {r.stderr[-300:]}")
T.finish()
