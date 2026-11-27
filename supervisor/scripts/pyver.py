# pyver.py — exits 0 when the Python running it is 3.8 or newer. py.sh runs it to probe each interpreter
# (27/11/2026, supervisor-directory-hook-credenziali-26-11: the directory's check does not read an inline
# `python3 -c` program, so the probe lives in a file of the plugin).
import sys

sys.exit(0 if sys.hexversion >= 0x03080000 else 1)
