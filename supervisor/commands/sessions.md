---
description: Every Claude session running on this machine, of every account — status, window, channel to reach it (for the phone, where there is no terminal)
---

Run and report the output AS-IS (it is already formatted; do not summarize):

```
supervisor sessions ${ARGUMENTS}
```

Only allowed addition: if a row says a session is stuck on a question with nobody watching, one sentence on what to do (`supervisor close --abandoned --dry-run`, or attach and answer).
