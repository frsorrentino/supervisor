#!/usr/bin/env python3
"""E6a (08/09/2026) — cattura il formato dei messaggi del socket inbox di una sessione.

Sposta il socket vero, si mette in ascolto al suo posto, registra ogni byte in
entrambe le direzioni e inoltra al socket vero. Uso:
    e6a-mitm-socket.py <percorso socket> <log>
Ctrl-C (o SIGTERM) ripristina il socket originale.

Formato catturato (due righe JSON, terminate da newline):
  {"type":"auth","token":"<peerToken del DESTINATARIO, dal file sessions/<pid>.<hash>.key>"}
  {"msgV":1,"msg_id":"<uuid>","type":"user","message":{"role":"user","content":
   "<cross-session-message from=\\"uds:<socket mittente>\\" from-name=\\"<nome>\\" from-mode=\\"bypass\\">\\n<testo>\\n</cross-session-message>"},
   "priority":"next","from":"uds:<socket mittente>"}
Attenzione: con il MITM attivo la sottoscrizione notify_when_idle fallisce (T63).
"""
import os, signal, socket, sys, threading, time

path, log = sys.argv[1], sys.argv[2]
orig = path + ".orig"
os.rename(path, orig)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(path)
os.chmod(path, 0o600)
srv.listen(8)


def restore(*_):
    try:
        os.unlink(path)
    except OSError:
        pass
    os.rename(orig, path)
    sys.exit(0)


signal.signal(signal.SIGTERM, restore)
signal.signal(signal.SIGINT, restore)


def pump(a, b, tag):
    with open(log, "ab") as f:
        while True:
            try:
                data = a.recv(65536)
            except OSError:
                break
            if not data:
                break
            f.write(f"\n--- {tag} {time.strftime('%H:%M:%S')} {len(data)}B ---\n".encode() + data)
            f.flush()
            try:
                b.sendall(data)
            except OSError:
                break
    for s in (a, b):
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


while True:
    c, _ = srv.accept()
    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    up.connect(orig)
    threading.Thread(target=pump, args=(c, up, "client->session"), daemon=True).start()
    threading.Thread(target=pump, args=(up, c, "session->client"), daemon=True).start()
