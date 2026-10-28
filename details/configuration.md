# Configuration

Back to the [README](../README.md).


One file for the whole machine, `~/.config/cc-supervisor/config.json`, written
by `supervisor init` from what it finds: your accounts and their folders,
the workspace root, the terminal you use, the language of the messages. You
rarely touch it; when you do, `supervisor doctor` tells you if something no
longer adds up. A minimal example for two accounts:

```json
{
  "workspace": {"root": "~/Desktop/workspaces"},
  "accounts": {
    "personal": {"config_dir": "~/.claude", "shell_command": "claude"},
    "work":     {"config_dir": "~/.claude-work", "tmux_prefix": "work-", "shell_command": "claude-work"}
  },
  "default_account": "personal",
  "folder_map": [{"path": "~/Desktop/workspaces/clients", "account": "work"}],
  "session": {"claude_args": ["--dangerously-skip-permissions"]},
  "terminal": {"backend": "chromeos"}
}
```

| key | what it decides |
|---|---|
| `accounts.<name>` | one entry per Claude account: its config folder, the tab's shape, a prefix for its session names, the shell command that opens it, and `remote_control` (true/false) to override `session.remote_control` for that account |
| `folder_map` · `workspace.root` | which folders belong to which account; the root opens the `master` session |
| `session.claude_args` · `session.link_wait_s` | flags every session starts with; how long `launch` waits for the Remote Control link (20 s) |
| `sessions.max_sessions` · `sessions.idle_hours` | past this many sessions at work `launch` asks (5); after this many idle hours `sessions` suggests closing (2) |
| `sessions.orphan_grace_s` · `sessions.orphan_wait_s` | a claude left without its tmux pane is closed after this many seconds (120), each signal of HUP, TERM, KILL waits this long (3) |
| `system.*` | the relay's machine monitor : load per core yellow/red (1.0/2.0), memory available yellow/red (1536/1024 MB), the context of an idle session worth closing (50%), a heavy job's CPU (50%), the patterns of processes without a parent that «Close» may end (`kill_patterns`), the heavy jobs to move to another host (`heavy_patterns`), the waits of processes stuck on ChromeOS's graphic bridge (`stuck_wchan`) and the units «Stop the loop» may stop (`stuck_units`, from `stuck_min_restarts` restarts) |
| `terminal.backend` | `chromeos`, `gnome`, `kitty`, `iterm2`, `macos-terminal`, `wt`, `none` |
| `shell.*` · `tmux.keybindings` · `tile.*` | the wrappers and aliases `init --shell` generates, the three keys of the `.tmux.conf` block, the window layout rules |
| `bot.*` · `recap.*` · `night.*` · `guard.*` | one-way Telegram, the evening diary, the night queue, the quota guard: all off or empty until you turn them on |
| `language` | `it` or `en` |

The full list with defaults: [`supervisor/config.example.json`](../supervisor/config.example.json).

## Terminal backends

| backend | tried on real hardware | notes |
|---|---|---|
| `chromeos` | **yes**, daily | ChromeOS Terminal; tabs, colours, `tile`/`merge`/`move`/`layout` |
| `none` | yes | no tab: sessions live in tmux, `tmux attach -t =<name>` |
| `gnome` | no | `gnome-terminal --tab`; written from the docs, covered only by tests with a fake terminal |
| `kitty` | no | same |
| `iterm2` · `macos-terminal` | no | same, through AppleScript |
| `wt` | yes, a clean WSL2 install | Windows Terminal, from a Linux shell under WSL; `launch` opened the session in its own tab |

On an untried backend the first launch is the test: `launch` says whether the
tab attached and, if it did not, prints the `tmux attach` line so nothing is
lost. If it fails, `terminal.backend: none` keeps everything else working.
Arranging windows exists only on ChromeOS through chrome-bridge, and Chrome
only sees a monitor that has a window on it: `move` tells you to drag one there.

## Requirements

- tmux (tested with 3.3a), python3 ≥ 3.8, bash; Claude Code ≥ 2.1.263.
- `crossSessionInbound: accept` in an account's settings, only to receive
  `talk` from the other account.
- [chrome-bridge](https://github.com/frsorrentino/chrome-bridge) ≥ 1.16.1, only for the window commands on ChromeOS.
- `python3-cryptography` and `crontab`, only for the relay of the Wear OS app
  (`doctor` checks both when `relay.enabled` is true; `relay pair` and
  `relay install` stop before doing anything if one is missing).
- Claude Code's `telegram` plugin, only to send: the diary, the night summary,
  the quota warnings and the watch's backup.

## Windows

supervisor does not run on native Windows: use it inside WSL2. Installed there
by mistake, disable it with `claude plugin disable supervisor`. With Git for
Windows it stays quiet (the `supervisor` command prints one line and exits 3);
without it, from Claude Code 2.1.299, its hooks block every permission request
and every Bash command. [How](windows.md).
