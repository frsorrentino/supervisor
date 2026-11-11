# Windows

Back to the [README](../README.md).

supervisor runs sessions in tmux and reads processes from `/proc`, so it
does not run on native Windows: use it inside WSL2, where it is Linux. On
native Windows, installed by mistake or through a shared settings file, disable
it: `claude plugin disable supervisor`.

Without Git for Windows it blocks Claude Code. Its hooks start with `bash` and
set no `shell`, and from Claude Code 2.1.299 a PreToolUse or PermissionRequest
hook that cannot start blocks the call instead of letting it through: every
permission request and every Bash command is refused, with no hint that
supervisor is the cause.

With Git for Windows it stays quiet instead of failing. Claude Code runs its
hooks through Git Bash; they start Python through `scripts/py.sh` (`python3`,
then `python`, then `py -3`, skipping the Microsoft Store alias) and stop at
once: the first session prints one line saying WSL2 is needed, later ones print
nothing, and with no Python at all each hook writes one line to its log and
exits 0. The `supervisor` command prints the same line and exits 3; only
`version` and `root` answer.
