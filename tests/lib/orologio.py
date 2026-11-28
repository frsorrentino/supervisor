#!/usr/bin/env python3
"""Un test con l'orologio di questo processo a un'ora data di oggi (28/11/2026, supervisor-ar5-orario-28-11).

Uso: orologio.py HH:MM TEST.py [ARGOMENTI...]

time.time(), time.localtime() e time.strftime(fmt) senza un'ora partono dalle HH:MM:00 di oggi e avanzano col tempo
vero; il test gira in questo processo (runpy), con __file__ e sys.argv suoi, e l'uscita e' la sua. I processi figli
hanno l'orologio vero, con la stessa data: un'ora vicina alla mezzanotte resta nel giorno di oggi solo qui dentro.
Serve a provare un test all'ora in cui cadeva (AR5 di agenda-raffica-verify alle 23:50), senza libfaketime.
"""
import runpy
import sys
import time as _t


def main(argv):
    if len(argv) < 2 or ":" not in argv[0]:
        print("usage: orologio.py HH:MM TEST.py [ARGS...]", file=sys.stderr)
        return 2
    hh, mm = (int(x) for x in argv[0].split(":", 1))
    real_time, real_localtime, real_strftime = _t.time, _t.localtime, _t.strftime
    y, m, d = real_localtime()[:3]
    t0, base = real_time(), _t.mktime((y, m, d, hh, mm, 0, 0, 0, -1))

    def fake_time():
        return base + (real_time() - t0)

    def fake_localtime(secs=None):
        return real_localtime(fake_time() if secs is None else secs)

    def fake_strftime(fmt, t=None):
        return real_strftime(fmt, fake_localtime() if t is None else t)

    _t.time, _t.localtime, _t.strftime = fake_time, fake_localtime, fake_strftime
    sys.argv = argv[1:]
    runpy.run_path(argv[1], run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
