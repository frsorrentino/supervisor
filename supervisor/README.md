# Supervisor for Claude Code

**Run several Claude Code sessions on one computer without losing track of them.** Each project gets its own terminal tab with a name and a colour; one list shows what is running; you can send a prompt from one session to another, or from your phone; a restart keeps the conversation; and after a reboot, the first shell you open offers to bring every session back. One session, the **master** on your workspace root, runs all the others: it launches, watches, answers and closes the rest.

![One master session runs all the others: the root session launches, watches, answers and closes the parallel sessions of every project, from the terminal and from the wrist.](https://raw.githubusercontent.com/frsorrentino/supervisor/main/assets/readme/card0-hero.png)

**Who it is for:** people who keep three or more Claude Code sessions open at once, on one machine, with one or two Claude accounts. If you open one session at a time, you do not need it. It runs daily on one workstation (ChromeOS, two accounts, up to eight sessions); the other terminals are supported in code but not yet tried on real hardware.

## What you get

| you want to… | you type |
|---|---|
| open a session on a project | `supervisor launch <folder>` (or just `claude` inside it) |
| see everything running, of every account | `supervisor sessions` |
| find what needs you | `supervisor next` |
| talk to another session | `supervisor talk <name> "…"` |
| answer the question another session is stuck on | `supervisor answer <name> 2` |
| see what a session is doing, from the phone | `supervisor screen <name>` |
| close one, restart this one keeping the conversation | `supervisor close <name>` · `supervisor restart arm` |
| switch another session's model or effort, that session only | `supervisor model <name> <model>` · `effort <name> <level>` |
| get everything back after a reboot | `supervisor restore` (the shell offers it) |
| arrange the windows (ChromeOS) | `supervisor tile` · `merge` · `layout save mattina` |

Inside a session the same things are slash commands (`/supervisor:sessions`, `:launch`, `:close`, `:restart`, `:report`, `:quota`), and the `sessions` skill teaches the agent how to resolve a half-typed folder name, that it may create a folder only if you asked, and how to reach another session. Optional, each off by default: a relay that puts your sessions on the wrist with the [Wear OS app](https://github.com/frsorrentino/claude-master-watch), one-way notices on Telegram, a quota guard, an evening diary and an overnight queue.

## Requirements

Claude Code 2.1.263 or newer, `tmux`, `python3` 3.8+ and `bash`, on Linux, macOS, ChromeOS or WSL2. Not on native Windows: disable it there (`claude plugin disable supervisor`). With Git for Windows its hooks stay quiet (one line at the first session) and the command says to use WSL2; without it, from Claude Code 2.1.299, its hooks block every permission request and every Bash command. A terminal from the supported list (ChromeOS Terminal, gnome-terminal, kitty, iTerm2, Terminal.app, Windows Terminal). The window commands on ChromeOS also need [chrome-bridge](https://github.com/frsorrentino/chrome-bridge). Setup after the install: `supervisor init --dry-run` shows every value it found on your machine, `init --yes --shim --shell` writes the config, `supervisor doctor` says what is left.

## What it runs

Eight small hooks (a session starts or ends, a prompt is sent, a permission is asked, a tool fails, a turn ends) run Python scripts from the plugin folder: they keep the session list fresh, stamp the local time on every prompt, deliver queued prompts and run the restart you armed. The `supervisor` command is a shell dispatcher over the same scripts. Every session is a `tmux` session started with Claude Code's Remote Control; the plugin reads the session registry Claude Code already keeps and talks to a session through the inbox socket Claude Code already opens. Sessions start with `--dangerously-skip-permissions` only if `init` sees that yours already do, and it says so.

## Data handling

- **Reads:** Claude Code's session registry, transcripts and settings for each account, and the last lines of each session's tmux pane. It checks that `.credentials.json` exists to recognize an account folder; it never opens it.
- **Writes on disk:** its state folder (`~/.local/state/cc-supervisor/`), the report images in a project's `docs/segnalazioni/`, and, only after your yes to `init`, the shim, one shell line, crontab lines.
- **Leaves the machine only if you turn it on:** the encrypted session state to *your own* Firebase project for the watch (`relay.enabled`), one-way notices through Claude Code's `telegram` plugin (`bot.enabled`), a `claude -p` call on your account to shorten a long question (`hooks.ask_notify.synth_model`), and the anonymized error report you approve in `observe send`.
- **No telemetry, no update check, no account of its own.** Full page: [PRIVACY.md](https://github.com/frsorrentino/supervisor/blob/main/PRIVACY.md). Security concerns: [SECURITY.md](https://github.com/frsorrentino/supervisor/blob/main/SECURITY.md).

## More

The full README, with a card per function, the configuration reference, the terminal backends and the complete command table, is at [github.com/frsorrentino/supervisor](https://github.com/frsorrentino/supervisor#readme). Thirty test suites, about 800 cases, run on a private tmux with a fake `claude`; none touches your real sessions.

MIT license.
