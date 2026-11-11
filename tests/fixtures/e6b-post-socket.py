#!/usr/bin/env python3
"""E6b — consegna un messaggio nel socket inbox di una sessione, da script.

Uso: post.py <nome sessione> "<testo>" [--from-name NOME] [--from-mode bypass|default]
Legge il registro (`~/.claude/sessions/<pid>.json` + `.key`), apre il socket e
manda le due righe catturate in E6a: auth con il peerToken del DESTINATARIO
(dal file .key), poi il messaggio.
"""
import glob, json, os, socket, sys, uuid

name, text = sys.argv[1], sys.argv[2]
from_name = "supervisor-script"
from_mode = "bypass"
a = sys.argv[3:]
if "--from-name" in a:
    from_name = a[a.index("--from-name") + 1]
if "--from-mode" in a:
    from_mode = a[a.index("--from-mode") + 1]

reg = os.path.expanduser("~/.claude/sessions")
target = None
for f in glob.glob(f"{reg}/*.json"):
    d = json.load(open(f))
    if d.get("name") == name and os.path.exists(f"/proc/{d.get('pid')}"):
        target = d
        break
if not target:
    sys.exit(f"sessione '{name}' non trovata nel registro")
keys = glob.glob(f"{reg}/{target['pid']}.*.key")
token = json.load(open(keys[0]))["peerToken"] if keys else ""
sock_path = target["messagingSocketPath"]
own = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET", "")
content = f'<cross-session-message from="uds:{own}" from-name="{from_name}" from-mode="{from_mode}">\n{text}\n</cross-session-message>'
msg = {"msgV": 1, "msg_id": str(uuid.uuid4()), "type": "user",
       "message": {"role": "user", "content": content}, "priority": "next", "from": f"uds:{own}"}
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(sock_path)
payload = json.dumps({"type": "auth", "token": token}) + "\n" + json.dumps(msg) + "\n"
s.sendall(payload.encode())
s.settimeout(3)
try:
    reply = s.recv(4096)
except OSError:
    reply = b""
s.close()
print("sent to", name, "pid", target["pid"], "socket", sock_path)
print("reply:", reply.decode(errors="replace")[:300] or "(none)")
