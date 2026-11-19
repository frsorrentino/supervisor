# Windows

Back to the [README](../README.md).

## Installing on Windows

supervisor runs inside Ubuntu (WSL2), which Windows installs by itself. The quickest way is one line in PowerShell:

```powershell
irm https://raw.githubusercontent.com/frsorrentino/supervisor/main/install.ps1 | iex
```

If Ubuntu is already there, it runs the installer inside it. If not, it installs WSL with Ubuntu after asking
(Windows asks for the administrator's permission) and then says the three steps left.

By hand, four steps:

1. Open PowerShell as administrator (right click on the Start menu) and type `wsl --install -d Ubuntu`.
2. Restart the computer.
3. Open «Ubuntu» from the Start menu and choose a user name and a password.
4. In Ubuntu, paste `curl -fsSL https://raw.githubusercontent.com/frsorrentino/supervisor/main/install.sh | bash`.

Running `install.sh` from Git Bash gives the same steps; when Ubuntu is already there, it offers to go on inside it.
The installer runs with a clean PATH inside Ubuntu, so it never finds or touches the Windows Claude Code.

## On native Windows

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
