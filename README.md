# Supervisor for Claude Code

![Version](https://img.shields.io/github/v/release/frsorrentino/supervisor?label=version&color=blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green) ![Claude Code](https://img.shields.io/badge/Claude%20Code-plugin-8A5CF6)

**Run your Claude Code sessions from your phone and your watch: answer the one
that is waiting, read what the others did, and let a master session coordinate
them with you.** Automations keep the work moving while you are away, and
suggestions tell you what to pick up next. It supervises the sessions you
already run, on your own computer.

[![supervisor, the film: the session waiting for you comes first; one tap answers it from the phone. Demo data.](assets/readme/supervisor-film.gif)](https://www.francescosorrentino.com/plugins/supervisor)

The film runs 2 min 24 s; names and numbers in it are demo data.
[Try the demo in your browser, no install](https://supervisor.francescosorrentino.com/?demo&lang=en).

## The problem

With three or more Claude Code sessions open, most of the time goes into
finding the one that stopped on a question twenty minutes ago. Step away from
the desk and every session that needs a yes waits for you to come back. tmux
keeps them alive, but it does not know which one is waiting, which Claude
account a folder belongs to, or how to bring eight conversations back after a
reboot.

supervisor is for people who keep several sessions open in a terminal, with one
or two Claude accounts. If you open one session at a time, you do not need it.
It is an independent plugin, not made by Anthropic.

## Try it in 60 seconds

```bash
claude plugin install supervisor --marketplace frsorrentino/supervisor
~/.claude/plugins/marketplaces/supervisor-dev/supervisor/bin/supervisor init --yes --shim --shell
supervisor relay web
```

The first line installs the plugin ([requirements](#requirements)). The second
writes the configuration from what it finds on your machine and puts `supervisor`
in `~/.local/bin`. The third opens every session on one screen in this computer's
browser: no Firebase, no account, nothing leaves the computer.

## What you get

- **Answer from your phone and your watch.** A session that stops on a question
  reaches your phone, tablet or watch with its options as buttons. One tap
  answers it. You can also prompt it, read what it did, see the diff of its files,
  switch its model or effort, and answer the forms MCP servers open.
- **A master session that works with you.** One session on your workspace root
  launches, watches, answers and closes the others, and tells you which one
  needs you.
- **Every session on one screen, the blocked ones first.** Every account, in one
  list: busy, idle, or stopped on a question.
- **Automations while you are away.** At 20:00 one sentence per project on what
  was done and the next step. At 02:00 a queue of jobs runs one at a time while
  memory and quota allow, with a report in the morning. When a session hits a
  limit, the phone offers to go on with another model, on your other account if
  you have one, or to resume by itself at the reset.
- **Suggestions for what to pick up next.** An hourly script, with no model
  call, queues what was left open: the next step of an idle session, the files a
  closed session did not commit. The master adds its own ideas. The phone's home
  page shows three at a time, each with a button.
- **The machine's weight, explained.** The home page shows the load, the memory
  and what uses them. A button closes what is safe to close: a duplicate session
  without its terminal, a leftover background process, the restart loop of a
  ChromeOS service. Heavy builds and renders get the advice to move elsewhere.
- **Back after a reboot.** The first shell after a reboot offers every session
  again, each with its own conversation.

![One master session runs all the others: the root session launches, watches, answers and closes the parallel sessions of every project, from the terminal and from the wrist — beside it, the session list of the Wear OS app, rendered from the app's code, on the demo set.](assets/readme/card0-hero.png)

## Every day

| you want to… | you type |
|---|---|
| open a session on a project | `supervisor launch <folder>` (or just `claude` inside it) |
| see everything running | `supervisor sessions` |
| find what needs you | `supervisor next` |
| talk to another session | `supervisor talk <name> "…"` |
| answer the question a session is stuck on | `supervisor answer <name> --show` · `answer <name> 2` |
| close one | `supervisor close <name>` (refused while it works or asks) |
| restart this one, keep the conversation | `supervisor restart arm` |
| get everything back after a reboot | `supervisor restore` |

Inside a session the same things are slash commands (`/supervisor:sessions`,
`:launch`, `:close`, `:restart`, `:report`, `:quota`). Every command and flag:
[details/commands.md](details/commands.md).

![Sessions at a glance: the real `supervisor sessions` table on the demo set — PID, account, name, state, folder, window, channel, uptime — with the session waiting for an answer and nobody attached on the first row.](assets/readme/card1-sessions.png)

## On your phone and watch

**The web app**, on any phone or tablet. Your own Firebase project carries the
traffic encrypted end to end: Firebase sees only blobs. On the PC:

```bash
npm install -g firebase-tools && firebase login
supervisor relay setup     # your own Firebase project, guided; --dry-run shows the steps
supervisor relay install   # keeps the relay running
supervisor relay pair      # a QR for the first device; later ones: relay pair --add
```

On the phone, open [supervisor.francescosorrentino.com](https://supervisor.francescosorrentino.com)
and scan the QR with the app's camera button. The key stays in that browser.

**Android and Wear OS.** The native apps are in review on Google Play; until they
are published, use the web app. They pair with the same QR, and the phone passes
the key to the watch, which then works on its own.

![From your wrist: the Wear OS app on the demo set — a session's card, a question with its options, the quota — real screens rendered from the app's code.](assets/readme/card7-wrist.png)

**Telegram**, one way only: the 20:00 diary, the night's report, and the quota
warnings when no device is receiving them. Nothing from Telegram can start,
answer or stop a session.

How the relay works, its protocol and its rules: [details/relay.md](details/relay.md).

## What you are trusting

- **Permissions stay yours.** supervisor never turns on
  `--dangerously-skip-permissions` by itself. `init` tells you when most of your
  live sessions run with it, and you add it to `session.claude_args` if you
  want prompts sent from the phone, restarts and the night queue to run without
  stopping at each command.
- **The phone can act, and the plugin keeps a way back.** A paired device can
  answer and prompt a session. Before a «yes» from it reaches a session, the
  plugin takes a git checkpoint of that workspace. A device is paired only
  through `relay pair` on the PC (a QR, a line to paste or a link, valid for five
  minutes), and `relay pair --revoke` removes it.
- **Sessions on one account talk to each other; across two accounts only if you
  allow it**, with `crossSessionInbound: accept` in that account's settings.
- **No telemetry, no update check, no account of its own.** Errors of
  supervisor's own commands are recorded locally, anonymized, and leave the
  computer only as a GitHub issue you approve.

In full: [details/trust.md](details/trust.md) and [PRIVACY.md](PRIVACY.md).

## Requirements

| you need | for | which |
|---|---|---|
| Claude Code | everything | 2.1.263 or newer, signed in once on this machine; 2.1.292 for the one-line install in [Try it](#try-it-in-60-seconds) |
| `tmux` | everything | on the PATH |
| `python3` | everything | 3.8+ |
| `bash` | everything | on the PATH |
| A system | everything | Linux, macOS, ChromeOS or WSL2; not native Windows ([details/windows.md](details/windows.md)) |
| Firebase project, `firebase-tools` | phone and watch away from this computer | your own, set up by `relay setup` |
| A browser | web app | any phone or tablet; on iPhone and iPad, add it to the Home Screen and open it from there |
| Android, Wear OS | native apps | Android 13+ phone, Wear OS 4+ watch |

## Install

```bash
claude plugin marketplace add frsorrentino/supervisor
claude plugin install supervisor@supervisor-dev --scope user
S=~/.claude/plugins/marketplaces/supervisor-dev/supervisor/bin/supervisor
$S init --dry-run              # every value it would write, with its source, and what is missing
$S init --yes --shim --shell   # writes the config; `supervisor` in ~/.local/bin; `claude` opens a named tab
supervisor doctor              # PASS, INFO, WARN or FAIL, with the fix on each line
supervisor launch ~/projects/x --create   # your first session, in its own tab
```

Claude Code 2.1.275 or newer can also do the install from inside a session:
`/plugin install supervisor --marketplace frsorrentino/supervisor`. To undo
everything: `claude plugin uninstall supervisor`, remove the line `init --shell`
added to your `.bashrc` or `.zshrc`, and `supervisor recap uninstall` /
`night uninstall` / `guard uninstall` if you turned those on.

It runs every day on one workstation (ChromeOS, two accounts, up to eight
sessions) and was tried on a clean WSL2 install with Windows Terminal. The other
terminals are supported in code but not yet tried on real hardware. Terminals and
every configuration key: [details/configuration.md](details/configuration.md).

<details>
<summary>If something goes wrong</summary>

`supervisor doctor` is the first thing to run: each line that is not PASS says
what to do. These are the problems seen on clean installs.

- **`supervisor: command not found` right after `claude plugin install`.** The
  plugin's `bin/` is on the PATH only inside Claude Code. Run the first `init`
  by its full path, as above; `--shim` then writes `~/.local/bin/supervisor`.
- **`supervisor: command not found` after `init --shim`.** `~/.local/bin` is not
  on the PATH of this terminal: open a new one, or add
  `export PATH="$HOME/.local/bin:$PATH"` to `.bashrc` / `.zshrc`.
- **In WSL2, `claude` answers before you installed it.** That is the Windows
  Claude Code, reached through the Windows PATH; sessions need the one inside
  the distro. Install it with the Windows folders out of the PATH:
  `curl -fsSL https://claude.ai/install.sh | PATH=/usr/local/bin:/usr/bin:/bin bash`.
  The plain command would run the Windows `npm uninstall` and remove the Windows
  Claude Code; if that happened, `npm install -g @anthropic-ai/claude-code` in
  PowerShell brings it back.
- **The first `launch` says the session is waiting for you.** Claude Code is not
  signed in on this machine, or asks whether to trust the folder. Answer in the
  session's window (or `tmux attach -t =<name>`): supervisor never answers these
  by itself unless you list them in `session.dialog_patterns`.
- **`WINDOW NOT OPENED`.** The session is running; only the tab is missing. Open
  it with the `tmux attach` line that `launch` prints. Over ssh, or with no
  supported terminal, set `terminal.backend` to `none`.
- **doctor: `plugin_stale`, or the hooks behave like an old version.**
  `claude plugin update supervisor`, then restart the sessions.
- **Native Windows.** Disable the plugin there with
  `claude plugin disable supervisor`: without Git for Windows, from Claude Code
  2.1.299, its hooks block every permission request and every Bash command.

</details>

## How it works

Every session is a `tmux` session named after its folder, started with Claude
Code's Remote Control, so it also shows in the Claude app under the same name.
The plugin reads Claude Code's session registry, one per account, and talks to a
session through the inbox socket Claude Code already opens. Six small hooks keep the list fresh, stamp the local time on every
prompt, deliver queued prompts and run the restart you armed. State lives in
one folder; the configuration is one JSON file that `init` writes from your
machine. The only process the plugin adds is the relay's daemon, and only if
you turn it on.

<details>
<summary>For the plugin directory reviewer</summary>

- **`bin/supervisor`** is a five-line bash script that runs `scripts/supervisor`,
  the dispatcher. It sits in `bin/` so that, while the plugin is enabled,
  `supervisor …` is on the PATH of Claude's Bash tool and the skills can call it
  by name.
- **Every file is readable text**: bash and Python 3 scripts, JSON, Markdown. No
  compiled program, no bundled binary, no minified code; each script stays under
  256 KiB.
- **Installing downloads nothing and builds nothing.** `supervisor doctor`
  prints the install command of a missing tool and runs none.
- **Data.** It reads Claude Code's session registry, transcripts and settings,
  and the last lines of each session's tmux pane; it checks that
  `.credentials.json` exists to recognize an account folder and never opens it.
  It writes its state folder (`~/.local/state/cc-supervisor/`), report images in
  a project's `docs/segnalazioni/`, and, only after your yes to `init`, the shim,
  one shell line and crontab lines. What can leave the machine, and only when
  you turn it on, is listed in [PRIVACY.md](PRIVACY.md).

</details>

## More

- [Commands](details/commands.md): every command and flag.
- [Configuration](details/configuration.md): the config file, terminals, requirements.
- [What you get, in full](details/features.md) · [What you are trusting, in full](details/trust.md).
- [The relay](details/relay.md) · [More machines](details/more-machines.md) · [Tasks and plans](details/tasks-and-plans.md).
- [supervisor and Claude Code Projects](details/claude-code-projects.md) · [Windows](details/windows.md).

## Tests

```bash
for t in tests/*-verify.py; do python3 "$t"; done
```

55 suites and about 1,870 cases. None touches your real tmux, Claude or browser:
they use a private tmux server, a fake `claude` that draws the real dialogs and
writes the real registry files, a fake browser and a fake Telegram. Every defect found on the real machine became a test before it
was fixed.

## License

MIT — Francesco Sorrentino. The QR of `relay pair` is drawn with
[qrcodegen](https://www.nayuki.io/page/qr-code-generator-library) by Project
Nayuki (MIT), shipped in `scripts/qrcodegen.py`.
