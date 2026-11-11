#!/usr/bin/env python3
"""ssh finto per i test degli adattatori (CM_SSH_BIN): esegue il comando remoto in locale con `sh -c`, con HOME e
cartella corrente in FAKE_SSH_HOME, come farebbe sshd. Simula anche:
  FAKE_SSH_DOWN=1        host spento: «Connection timed out», exit 255
  FAKE_SSH_HOSTKEY=bad   chiave dell'host cambiata: «Host key verification failed.», exit 255
  FAKE_SSH_LOG=FILE      una riga JSON per chiamata con argv (per controllare le opzioni passate)
`-O exit` (chiusura del master) esce 0 senza fare nulla."""
import json
import os
import subprocess
import sys
import time

argv = sys.argv[1:]
if os.environ.get("FAKE_SSH_LOG"):
    with open(os.environ["FAKE_SSH_LOG"], "a") as f:
        f.write(json.dumps(argv) + "\n")
if os.environ.get("FAKE_SSH_DOWN"):
    time.sleep(0.2)
    print("ssh: connect to host fake port 22: Connection timed out", file=sys.stderr)
    sys.exit(255)
if os.environ.get("FAKE_SSH_HOSTKEY") == "bad":
    print("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
          "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\nHost key verification failed.", file=sys.stderr)
    sys.exit(255)
i, rest = 0, []
while i < len(argv):
    a = argv[i]
    if a in ("-o", "-p", "-i", "-l", "-O"):
        if a == "-O":
            sys.exit(0)
        i += 2
        continue
    if a.startswith("-"):
        i += 1
        continue
    rest = argv[i:]
    break
if len(rest) < 2:
    print("fake-ssh: no command", file=sys.stderr)
    sys.exit(255)
home = os.environ.get("FAKE_SSH_HOME") or os.environ["HOME"]
env = dict(os.environ, HOME=home)
p = subprocess.run(["sh", "-c", " ".join(rest[1:])], cwd=home, env=env)
sys.exit(p.returncode)
