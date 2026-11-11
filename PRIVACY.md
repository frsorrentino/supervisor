# Privacy — supervisor

_Last updated: 26 September 2026._

supervisor runs on your computer and has no telemetry, no analytics and no account of its own. This page says what it reads, what it writes on disk, and what leaves the machine, so you can decide what to turn on. Every outbound path below is off by default and uses a service you already own.

## What it reads

- Claude Code's own files, for every account it knows: the session registry (`sessions/*.json`), the session transcripts under `projects/` (to read a session's model, effort, context use, its last message, an open `/goal` or `AskUserQuestion`), `settings.json`, and the statusline JSON that another plugin writes for the quota. It checks whether `.credentials.json` exists to tell an account folder from an empty one; it never opens it.
- The screen of each session's tmux pane (the last lines of the terminal), to tell whether a session is waiting on a question, running at lower priority, or showing a picker.
- Its own configuration (`~/.config/cc-supervisor/config.json`) and state folder.

## What it writes on disk

- Its state folder (`~/.local/state/cc-supervisor/` by default): the sessions registry and «last good set», the durable inbox of prompts between sessions, the restart flags and log, the night queue, the relay's key and paired devices (0600), the diary of the day.
- In a project you send a report to: the image under `docs/segnalazioni/` of that project.
- With `init --shim` / `--shell` / `--cron` / `--tmux`, and only after you say yes: the `supervisor` command in `~/.local/bin`, one line in your shell file, crontab lines, a block in `.tmux.conf`.
- Before a «yes» from the watch reaches a session: a git checkpoint of that session's workspace (`git stash create`: a commit object with no ref, the working tree untouched).
- The local error log of supervisor's own commands (`~/.local/state/claude-observe/supervisor.jsonl`, 0600): command names and cleaned error texts, never argument values. `{"enabled": false}` in `~/.config/claude-observe/config.json` turns it off.

## What leaves the machine, and only if you turn it on

- **Relay for the Wear OS app** (`relay.enabled`): the state of your sessions — names, folders, status, the open question and its options, model and context use, quota, the day's diary, the night queue — and, on request from the watch, the last lines of a session's screen or its last message. Encrypted with AES-GCM under a key agreed by X25519 at pairing, written to a Firebase Realtime Database **in your own Firebase project** with your service-account key; the wake-up goes through Firebase Cloud Messaging. The database rules ship with the plugin. Anthropic and the plugin's author never receive it.
- **Telegram, one way** (`bot.enabled`): notices of a session's question (its text and options), the quota warnings, the evening diary and the night report, sent with Claude Code's `telegram` plugin to the chats already in its allow list. Nothing is read back.
- **A model call to summarize a long question** (`hooks.ask_notify.synth_model`, empty by default): the question's text goes to Claude through `claude -p` on your own account, to fit it on the watch.
- **Cloud sessions** (`supervisor cloud`, `follow`): the task text you type goes to Claude Code's cloud sessions on your account, as if you had typed it in Claude Code.
- **Observations** (`supervisor observe send`): the anonymized errors of the plugin's own commands, as one GitHub issue opened in your name with `gh` or from a link you open, or as a private vulnerability report; shown to you first, sent only after your yes. The anonymous route through the maintainers' endpoint exists only when `observe.endpoint` is set.

Nothing else is sent anywhere. The plugin does not phone home, has no update check, and reads no credential to send it elsewhere: the Telegram token and the Firebase key are read only to talk to the service they belong to.

## Retention

Everything above lives in files you own and can delete; the relay's `/state` is overwritten at every push and the events are pruned by the relay itself. Nothing is retained by the author.

## Changes and contact

Changes to this page are versioned in the [GitHub repository](https://github.com/frsorrentino/supervisor). Questions: an issue at <https://github.com/frsorrentino/supervisor/issues>; a security concern: [SECURITY.md](SECURITY.md).
