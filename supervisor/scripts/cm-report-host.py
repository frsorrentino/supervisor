#!/usr/bin/env python3
"""L'immagine di `supervisor report` nella cartella di una sessione su un altro host (27/11/2026, supervisor-report-win-27-11).

Uso: cm-report-host.py NOME IMMAGINE TESTO

NOME e' una sessione su un altro host (`HOST:nome`, o il nome di una sessione lanciata con launch --host). L'immagine va
nella sua cartella sull'host, in <report.subdir>/<data>-<slug>.<ext>, con la stessa via degli allegati che il relay manda a
una sessione su un altro host (Place e report_image_save, contratto 1.50): l'adattatore dell'host la scrive la', e qui si
stampa il percorso sull'host, quello che la sessione apre. Prima report la lasciava solo nel progetto di questo computer,
e la sessione riceveva un percorso che sull'host non si apre (la master l'ha copiata a mano, il 27/11 alle 21:27).
La riga della sessione e' quella di `sessions --json` (cm-hosts.remote_session_rows, l'ultimo giro degli host); se
quel giro non la vede (una sessione nata la' da poco), un giro forzato di quell'host prima di rinunciare.
Exit 0 scritta; 1 NOME non e' una sessione viva su un altro host; 2 uso; 3 l'host non l'ha presa (il motivo su stderr).
"""
import contextlib
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_rh", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def row_of(rows, name, resolved):
    """La riga della sessione remota `name` fra quelle degli host: per nome («HOST:base» o il nome sull'host), o per
    (host, nome) di cm-rsession.resolve; None se non c'e'."""
    host, rname = resolved if resolved else (None, None)
    for r in rows:
        if r.get("host") in (None, "", "local"):
            continue
        if name in (r.get("name"), r.get("remote_name")):
            return r
        if host and r.get("host") == host and rname in (r.get("remote_name"), str(r.get("name") or "").split(":", 1)[-1]):
            return r
    return None


def main(argv):
    if len(argv) != 3:
        print("usage: cm-report-host.py NAME IMAGE TEXT", file=sys.stderr)
        return 2
    # su stdout solo il percorso, che cm-report.sh mette nel prompt: quel che stampano i moduli va su stderr
    with contextlib.redirect_stdout(sys.stderr):
        rc, dest = save(*argv)
    if rc == 0:
        print(dest)
    return rc


def save(name, img, text):
    """(0, percorso sull'host) o (codice, None) col motivo gia' su stderr."""
    RL = _load("cm-relay")
    hosts, resolved = _load("cm-hosts"), _load("cm-rsession").resolve(name)
    row = row_of(hosts.remote_session_rows(), name, resolved)
    if not row and resolved:
        try:
            hosts.poll_one(resolved[0], force=True)
        except Exception:   # noqa: BLE001 — host irraggiungibile: resta «non la vede»
            pass
        row = row_of(hosts.remote_session_rows(), name, resolved)
    place = RL.Place(row) if row else None
    if not place or not place.host or not place.ok():
        print(RL.M("report.host_no_session", name=name), file=sys.stderr)
        return 1, None
    try:
        data = Path(img).read_bytes()
        return 0, RL.report_image_save(place, text, Path(img).suffix.lstrip(".").lower() or "png", data)
    except Exception as e:   # noqa: BLE001 — l'host non risponde, la copia non riesce
        print(RL.M("report.host_not_saved", name=name, error=str(e)[:200]), file=sys.stderr)
        return 3, None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
