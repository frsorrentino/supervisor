#!/usr/bin/env python3
"""scp finto (CM_SCP_BIN): copia in locale; `alias:percorso` relativo sta sotto FAKE_SSH_HOME, assoluto e' se stesso.
FAKE_SSH_DOWN=1 come in fake-ssh.py. FAKE_SCP_LOG=FILE: una riga per copia (per contare i byte trasferiti)."""
import os
import shutil
import sys
import time

if os.environ.get("FAKE_SSH_DOWN"):
    time.sleep(0.2)
    print("ssh: connect to host fake port 22: Connection timed out", file=sys.stderr)
    sys.exit(255)
argv, i, files = sys.argv[1:], 0, []
while i < len(argv):
    if argv[i] in ("-o", "-P", "-i"):
        i += 2
        continue
    if argv[i].startswith("-"):
        i += 1
        continue
    files.append(argv[i])
    i += 1
home = os.environ.get("FAKE_SSH_HOME") or os.environ["HOME"]


def local(p):
    if ":" in p and not p.startswith("/"):
        p = p.split(":", 1)[1]
        return p if p.startswith("/") else os.path.join(home, p)
    return p


src, dst = local(files[0]), local(files[1])
if os.path.isdir(dst):
    dst = os.path.join(dst, os.path.basename(src))
shutil.copyfile(src, dst)
if os.environ.get("FAKE_SCP_LOG"):
    with open(os.environ["FAKE_SCP_LOG"], "a") as f:
        f.write(f"{files[0]}\t{files[1]}\t{os.path.getsize(src)}\n")
