---
description: The complete supervisor install from inside Claude Code — config, shell, cron, relay, the master open and seen by the web app; asks only the projects folder and whether you want the app on your phone; safe to run again
---

Install supervisor end to end with `supervisor setup`, then fix what it reports.

1. Ask the user two questions, in their language, in one message, and wait:
   - the projects folder (the master opens there); offer the default that `supervisor setup --dry-run --yes` prints on its «configuration» line;
   - whether they want the web app on their phone (it needs their own Firebase project and a Google sign-in).
   If the user already gave them in ${ARGUMENTS}, do not ask again.
2. Run, with the folder they chose:

   ```
   supervisor setup --yes --root <folder> --phone no
   ```

   Report every line with ✗ or ! as-is, with its fix. Apply a fix yourself only when it is a supervisor command or a
   file of this user's home; a line with `sudo` is for the user: show it and ask them to run it with `! <command>`.
   Then run the same `supervisor setup` line again: it picks up where it stopped.
3. If they want the phone: the Google sign-in and the QR need their terminal. Tell them to type
   `! supervisor setup --phone yes` (or to run it in a terminal), and stay to read its output.
4. At the end say, from the output and not from memory: whether the master is open and seen open by the web app,
   the web app's address on this computer, and what is left. If something failed, give them the block between
   «--- to send to whoever helps you ---» and «--- end ---» (or its Italian form) to send on.
