---
description: Restart the current session at the end of this turn, resuming the conversation (--clean for a fresh context, --switch-account only when the user asks to change account)
---

```
supervisor restart arm ${ARGUMENTS}
```

Report the output as-is, then end the turn: the restart is executed by the Stop hook once this turn is over. Do not run anything else after arming. Use `--switch-account` only when the user's own message asks to change account: never propose or recommend it (not even as an option in a question), and name accounts by their owner, not by «personal/work». The command refuses it otherwise. With `--switch-account`, say explicitly that from the restart on the conversation context goes to the API under the other organization.
