#!/usr/bin/env python3
"""install.sh dice la verita' sull'aggiornamento del plugin (19/11/2026, supervisor-master-prova-vera-19-11).

La prova vera in una distribuzione WSL di prova: il marketplace puntava a una copia vecchia, `claude plugin update` usciva
0 senza cambiare niente e l'installatore scriveva «plugin updated» con la 0.8.146 ancora al suo posto; poi `setup` della
versione vecchia rifiutava le opzioni nuove.

IV1 la versione non cambia → «already at the latest version the marketplace offers (0.8.185)», mai «plugin updated»
IV2 la versione cambia → «plugin updated: 0.8.185 → 0.8.186»
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(T.tmpdir())
home = tmp / "home"
home.mkdir()
fb = tmp / "bin"
fb.mkdir()
ver, bump = tmp / "ver", tmp / "bump"
(fb / "claude").write_text(f"""#!/bin/sh
case "$1 $2" in
  "--version "*) echo "2.1.322 (Claude Code)" ;;
  "plugin list") printf 'Installed plugins:\\n\\n  > supervisor@supervisor-dev\\n    Version: %s\\n    Scope: user\\n' "$(cat "{ver}")" ;;
  "plugin update") [ -f "{bump}" ] && cp "{bump}" "{ver}" ;;
esac
exit 0
""")
(fb / "claude").chmod(0o755)


def install():
    env = {"PATH": f"{fb}:/usr/local/bin:/usr/bin:/bin", "HOME": str(home), "LANG": "en_US.UTF-8"}
    return subprocess.run(["bash", str(T.ROOT / "install.sh")], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120)


ver.write_text("0.8.185")
r = install()
T.check("IV1 the version does not change → «already at the latest version the marketplace offers (0.8.185)», never «plugin updated»",
        "already at the latest version the marketplace offers (0.8.185)" in r.stdout and "plugin updated" not in r.stdout, r.stdout[-900:])
bump.write_text("0.8.186")
r = install()
T.check("IV2 the version changes → «plugin updated: 0.8.185 → 0.8.186»", "plugin updated: 0.8.185 → 0.8.186" in r.stdout, r.stdout[-900:])

T.finish()
