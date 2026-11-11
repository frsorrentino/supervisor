#!/usr/bin/env python3
"""adb finto per relay-verify: registra ogni chiamata in FAKE_ADB_LOG; FAKE_ADB_MODE = ok | offline | hang."""
import os
import sys
import time

with open(os.environ.get("FAKE_ADB_LOG", "/dev/null"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
mode = os.environ.get("FAKE_ADB_MODE", "ok")
args = sys.argv[1:]
if "get-state" in args:
    print("device" if mode in ("ok", "hang") else "offline")
elif "shell" in args:
    if mode == "hang":
        time.sleep(60)
    print("Starting: Intent { act=android.intent.action.VIEW }\nStatus: ok")
