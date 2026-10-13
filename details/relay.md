# The relay: protocol, contract and setup

Back to the [README](../README.md). The setup in short is in [Try it](../README.md#on-your-phone-the-web-app).

## Relay for the Wear OS app

`supervisor relay` puts the PC on the bus of the native watch app
([github.com/frsorrentino/supervisor-app](https://github.com/frsorrentino/supervisor-app),
whose README has the watch side under «Set up»):
`relay push` publishes an encrypted `/state` (the v1 contract in
`tests/fixtures/relay/`) to Firebase RTDB, appends `/events` and wakes the watch
with FCM; `relay serve` listens on `/cmd` and runs the allow-listed commands
(answer, prompt, launch — with an optional first message — follow, resume, reopen, screen, allow_all, last, model, effort) through the CLI,
writing `/result`; `relay pair` shows a QR for the phone app and a six-digit
code for the watch, and agrees the AES key over X25519 with whichever answers
first. Setup: a Firebase project with RTDB and FCM, its
service account JSON in `~/.cc-supervisor/relay/service-account.json` (0600,
never in the repo), `relay.enabled`, `relay.firebase_url` and `relay.fcm_topic`
in the config, then `relay pair`, `relay install`. RTDB rules: `/state`,
`/events` and `/result` readable only by a uid present in `/allowed`, `/cmd`
and `/share/<id>` writable only by those, `/pair/<code>/watch` and `/pair/<id>/watch` writable by
an anonymous user; the PC writes with the service account. `relay push
--dry-run` prints the clear state without touching the network.

Contract 1.16 adds two fields to every session in `/state`: `low_priority`
(`off`, `offered` when Claude Code proposes `/low-priority` at the usage limit,
`active` when the session runs on past it, `null` when there is no screen to
read it from — that state lives only in the session's memory) and `goal`
(`{text, since, met}` from the last `goal_status` of the transcript, `null`
without a `/goal`).

Contract 1.17 lets the app edit tonight's queue: `night_add` (the folder of a
published project and the prompt, run as `supervisor night add`) and
`night_remove` (the job id), and `/state` carries `night.items`, the queue in
run order with each prompt cut to 160 characters. The field is there even when
the queue is empty: its absence tells the app to ask for an update. The queue
holds at most `night.max_queued` jobs (8), and a job that has started cannot be
removed.

Contract 1.18 sends the long texts to the app too, as events with the FCM
wake-up: `recap` (the 20:00 diary) and `night_report` (the night shift's
summary), with the Telegram text without markup, cut at a line end within 4000
characters, and the day in `ref`; the quota guard's resume at the reset is a
`quota` event like the threshold warning.

Contract 1.19 is «Share» from the phone: the phone writes an image, encrypted
like a command, in `/share/<id>` (at most 1.5 MB, JPEG or PNG), then sends
`report` with the session, the text and that id; the relay runs
`supervisor report <session folder> <image|-> "<text>" --session <name>` and
deletes the node, whatever the outcome (unread nodes go after 10 minutes).
`/state` carries `share: {max_bytes}` so the app knows it can.

Contract 1.21 adds the Stop button: `interrupt` with the session runs
`supervisor interrupt`, and `/state` carries `ops`, the command ops this relay
executes, so the app shows a button only when its op is there.

Contract 1.22 is the conversation for the phone's chat: `transcript` with `n`,
`n:before=<id>` or `n:after=<id>` answers with the session's entries read from
its transcript — what the person typed, Claude's text, one line per tool call
(with its description, whether it failed, and the media or documents it wrote or
sent), and the tokens and times of each closed turn — at most 60 KB a page.

Contract 1.23 adds `suggestion` to every session in `/state`: the prompt Claude
Code suggests in dim text after `❯` when the input is empty, read with its
colours from the pane, for a session at the prompt only — never typed text.

Contract 1.24 opens a file from the conversation on the phone: `file` with the
session and, as `arg`, a `path` exactly as `transcript` lists it in `files`.
The relay serves only a path listed in that session's transcript and writes it
to `/file/<command id>`, encrypted like `/share`, as `{mime, data}`. An image
over the 1.5 MB envelope is reduced to a JPEG; any other file over it is
refused. The device deletes the node after reading it; unread nodes go after
10 minutes. Refusals: «no session …», «not in the transcript», «missing or
unreadable», «too large: <bytes>».

Contract 1.25 sends slash commands from the phone: `slash` with the session, the
command without «/» as `arg` and its arguments as `text`. Only the commands in
`relay.slash_commands` (default compact, clear, exit, context, cost), listed in
`/state` as `slash`, are accepted. The relay types the command in the session's
pane like the person at the terminal; through the inbox socket it would arrive as
another session's message and not run. A busy session, or one on a dialog, is
refused («<name> is busy»), as is a command outside the list («not allowed: …»).
A command that opens a panel over the prompt (`/cost`, `/usage`) has its text
read and sent back in the result, then the panel is closed with one Esc
(`supervisor panel <name>`), so nothing stays open on the PC.

Contract 1.26 gives the phone the whole project list for «Launch»: `projects`
answers with every project of every account (the fields of `/state.projects`),
most recently used first, at most 60 KB. When `/state` must drop projects to
fit in 8 KB, it keeps the most recently used ones instead of the first by name.

Contract 1.27 lets the phone search every conversation: `search` with the text
(1 to 200 characters) looks through the user and assistant messages of the live
sessions and of those closed in the last 7 days, on both accounts, ignoring case
and accents. It answers at most 50 hits, newest first, each with the session,
the folder, the entry id of `transcript` to jump to, and a snippet of up to 160
characters with the word's position; past 50 hits, 60 KB or 10 s, `more` is true.

Contract 1.28 lets the phone send files of any format, not only images: a PDF,
a text, an archive lands in `.claude-master-inbox/` inside the session's folder
(ignored by git) and the session is told its name, type, size and path.

Contract 1.30 adds a device without unpairing the others: `relay pair --add`
keeps the relay's key and hands it to the new device encrypted inside the
pairing confirmation; the devices already paired stay (eight at most, contract 1.39: a browser with the web app counts as one). A plain
`relay pair` still replaces the key and every device.
From the phone the same happens without the PC's terminal (contract 1.31, op
`pair_add`): the relay opens the round by itself and the phone shows its QR to
the new device.
On a Chromebook the invite opens by itself in the app on the same machine
(contract 1.32, through adb to its Android), and `/state.devices` lists every
paired device with its kind and when it last read.
Contract 1.34 sends a file of the chat in parts of 1 MB, up to 25 MB, so a
long HTML report or a PDF opens on the phone as it is.
Contract 1.35 serves the web app on the same machine: with `relay.web.enabled`
the relay daemon listens on `127.0.0.1:<relay.web.port>` (8765 by default),
serves the built web app from `relay.web.dir` on `/` and a local API with the
contract's JSON in clear (state, events, a state stream, commands, files,
attachments) behind a local token. `supervisor relay web` prints the address
with the token and opens it in the browser.
Contract 1.36 labels what comes from the web app («via the web app»), and
contract 1.37 brings the master's help to the app: fable-director's advice of
model and effort for each session, the registry's tasks waiting for an ok with
an «Approve» button (op `approve`), «Save as decision» for the master's memory
(op `decision`), and the sessions that are finished or duplicated.
Contract 1.42 serves the app's live mode: a prompt dictated by voice
(`voice: true`) carries short spoken-answer instructions after the device's
prefix, and an approval given from the wrist (`via: "live"`) counts only with a
double confirmation (`confirmations` of 2 or more).

Contract 1.20 counts the phone as well as the watch before falling back to
Telegram. Each paired device writes `/seen/<uid>` (the Firebase server time)
when it reads `/state`; if no paired device has read an event within
`relay.telegram_fallback_after_s`, the event goes to Telegram once. Until some
device writes `/seen`, only the old rule applies (the bus refusing the push).

The QR (contract 1.15) carries the pairing id, the PC's public key, the host,
the expiry and the data the phone needs to join the Firebase project: the
app's API key, project id and Android app id, from `relay.firebase_app`, or read
from the `google-services.json` named in `relay.google_services` (its Android
client; `relay.app_package` picks one when the file has more than one); the
database URL and the FCM topic
come from the relay's own config. Without those data `relay pair` says so, shows
no QR and the six-digit code still works; `doctor` shows a WARN. The QR is
drawn in the terminal with half blocks, error correction L and a two-module
margin (about 70 columns wide; `relay pair --text` prints its JSON on one line
instead). The same
document sits under `/pair/<code>` and `/pair/<id>`; the first valid answer
wins, the other node is deleted, expiry and attempts are shared. The phone may
answer with `uids` (up to four) and `names`: every uid goes to `/allowed`, and
`devices.json` keeps each with its name.

`relay setup` builds the Firebase side, guided and idempotent, on the Firebase
CLI already logged in (`npm install -g firebase-tools`, `firebase login`): it
picks or creates the project, creates the Realtime Database instance
(`relay.setup_location`, europe-west1 by default), deploys the rules above,
enables anonymous sign-in, registers the Android app of `relay.app_package`
without a SHA fingerprint (the API key is not restricted: the Play signature
differs from the development one), saves its data in `relay.firebase_app` and
`<relay.dir>/google-services.json`, creates the firebase-adminsdk key at
`relay.service_account` (0600) and writes `relay.enabled`, `relay.firebase_url`
and `relay.fcm_topic` into the config in use, `CC_SUPERVISOR_CONFIG` included.
The database instance, anonymous sign-in and the key go through Google's REST
APIs with the CLI's own token; when that token is missing, those steps print
the exact console link and wait for Enter. On a project born a minute ago
Authentication has to be opened once in the console («Get started»), because
no API initializes it without billing: `relay setup` says so, waits, then
enables the provider. A key file that belongs to another project is never
replaced. `--dry-run` lists what would be done and writes nothing; `--yes` takes
the defaults; the end is a summary like `doctor`'s.

To try the app without touching the watch already paired, point
`CC_SUPERVISOR_CONFIG` at a trial config with its own `relay.dir`,
`relay.service_account` and `relay.firebase_url`: `relay pair`, `relay push` and
`relay serve` then work only there, and the main config's key, `devices.json`,
`/allowed` and crontab stay as they are.

Each session in `/state` carries the badge the watch draws: `icon`, the emoji
of its Terminal tab (stable for the session's life, the last known one once it
is gone), and `color`, the hue alone as `#RRGGBB` whatever the shape — the
watch takes the shape from the account (round for the personal one, square for
the others) and the colour from here, so a session looks the same on the PC, in
Telegram and on the wrist. `relay.colors` overrides the emoji-to-hex map.

Each session also carries what it is running on: its model (id and short name),
its effort level and how much of the context window it has used, read from its
transcript. They are null when they cannot be read, and the percentage is left
out rather than guessed when the model changed mid-session.

When a push cannot reach the bus, or the wake-up fails, the relay notes when the
outage started and, once it lasts longer than `relay.telegram_fallback_after_s`
(ten minutes by default), the notices go out on Telegram instead, until a push
works again.

## From the phone and from the wrist

The watch is where you act from: the relay puts this PC on its bus, and from
there a session can be answered, prompted, launched, followed, reopened or read
aloud. The phone gets what a watch cannot carry — the 20:00 diary, the night
report — and whatever the watch is not receiving.

```bash
# config.json: "bot": {"enabled": true}   — the token and chats of the `telegram` plugin
supervisor bot status       # token, allowed chats, log; nothing to keep alive
supervisor recap install    # the day's diary to those chats at 20:00 (recap.cron_time)
supervisor night install    # the overnight queue at 02:00 (night.cron_time); fill it with `night add`
supervisor relay setup      # the Firebase project, guided (--dry-run to see the steps)
supervisor relay pair       # once: a QR for the phone, a 6-digit code for the watch
supervisor relay install    # the relay's daemon and its keeper
```

Two limits, stated plainly. After a **reboot** nobody is logged in and
scheduled tasks do not run: the relay covers «sessions closed, machine awake»,
not «machine off» — on a Chromebook, set the power options so the machine does
not sleep while charging. And the diary reports *what happened*, not what it
cost: costs stay where they are measured (for me, fable-director).
