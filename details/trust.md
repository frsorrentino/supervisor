# What you are trusting, in full

Back to the [README](../README.md).

Four things are worth knowing before you turn them on.

- **Sessions start with `--dangerously-skip-permissions`** if `init` sees that
  your existing sessions use it (it says so, with the source). Without it,
  every session asks you to confirm each command, as Claude Code does by
  default: nothing breaks, but a session stops at each command until someone
  presses "allow". What suffers is what runs with nobody at the keyboard:
  prompts and answers sent from the phone or from another session, a restart
  at the end of a turn, the night queue. Keep it where sessions must work
  unattended; remove it from `session.claude_args` where you prefer to
  confirm each command.
- **`crossSessionInbound: accept`** in an account's settings lets another
  session on this machine send prompts to that account's sessions. It is
  needed only for `talk` across accounts, and it means: whoever can run
  commands on this machine can talk to those sessions.
- **Telegram is one-way**: the plugin only sends, to the chats already in the
  `telegram` plugin's allow list, and reads nothing back — no message from
  anywhere can start, answer or stop a session. What can act on a session is
  the paired watch, and before a «yes» from the watch reaches a session the
  plugin takes a git checkpoint of its workspace.
- **Errors of supervisor's own commands are recorded, locally.** A
  `PostToolUseFailure` hook ([claude-observe](https://github.com/frsorrentino/claude-observe),
  in `supervisor/observe/`) writes each failed `supervisor` command
  (except the exit codes documented as normal, like a refused restart) and
  each watch command the relay could not carry out to
  `~/.local/state/claude-observe/supervisor.jsonl` (or under
  `$XDG_STATE_HOME`), a 0600 file on this computer; the same error again is
  one line with a count. Of a command only its name, subcommand and option
  names are kept: arguments become `<ARG>`, error texts are cleaned of the
  home path, emails, URL queries and secrets, and no parameter value is ever
  stored. Nothing leaves the computer on its own: the only way out is a
  GitHub issue, which Claude offers once a few errors have piled up, shows
  you anonymized, and sends only after your yes (with `gh`, or as a
  prefilled link you open). Turn it
  off with `{"enabled": false}` in `~/.config/claude-observe/config.json`;
  `{"propose": false}` keeps the file and stops the offers.

Each session is a Claude Code process (a few hundred MB each); the only
process the plugin adds is the relay's daemon, a few MB waiting on a socket;
the diary and the night queue are scheduled tasks that run for seconds.
