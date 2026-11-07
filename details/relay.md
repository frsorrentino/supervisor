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
with the token and opens it in the browser. Without `relay.firebase_url` (13/10,
0.8.15) the relay runs in local mode: `relay web` turns on `relay.enabled` and
`relay.web.enabled` when the relay is off, the daemon keeps state and events only
in `<relay.dir>/local` (no bus, no FCM, no Telegram fallback), and without
`relay.web.dir` the web app comes from `relay.web_app_url`, each file fetched once
into `<relay.dir>/web-cache`: the files under `assets/` once, everything else
(`index.html`, `sw.js`, the manifest) again at every request, from the cache when
the site is unreachable. A relay off with `relay.firebase_url` set stays off.
Contract 1.36 labels what comes from the web app («via the web app»), and
contract 1.37 brings the master's help to the app: fable-director's advice of
model and effort for each session, the registry's tasks waiting for an ok with
an «Approve» button (op `approve`), «Save as decision» for the master's memory
(op `decision`), and the sessions that are finished or duplicated.
Contract 1.42 serves the app's live mode: a prompt dictated by voice
(`voice: true`) carries short spoken-answer instructions after the device's
prefix, and an approval given from the wrist (`via: "live"`) counts only with a
double confirmation (`confirmations` of 2 or more).

Contract 1.72 shows from the phone what changed in the files (15/10/2026,
additive, `v` stays 1). Every entry of `transcript` and of `/tail/<id>` carries
`diff`: null, or for a Write, Edit, MultiEdit or NotebookEdit with a structured
result `{path, kind, added, removed, hunks, cut}`. `path` is absolute; `kind`
is `edit`, `create` (a Write of a new file: every line of its content added) or
`overwrite`; `hunks` are `[{old_start, old_lines, new_start, new_lines,
lines}]`, every line starting with `-`, `+` or a space (no «\ No newline at
end of file», no trailing CR); `added` and `removed` count the whole passage;
`cut` is true past 200 lines or 16,000 bytes. A NotebookEdit diffs the cell,
numbered inside it. Op `diff` (`arg` = the entry's id) gives the passage whole,
up to 5000 lines and 1,000,000 bytes. Op `changes` (`arg` null, or
`file=<path>` with a `path` exactly as listed) answers `{source, repo, base,
files, totals, cut, at}`: with git, everything in the session's repository
against HEAD, new files not ignored included (`source` git, `repo` the root,
`base` the short hash of HEAD, null without commits, when every file is new);
outside git, or for a session on another host, the passages of the current
conversation by file, in order (`source` transcript, `repo` and `base` null,
`path` relative to the session's folder when inside it). `files` are `[{path,
status, added, removed, size, hunks, cut}]`, `status` modified, added,
deleted, renamed or binary, `size` null for a deleted file; at most 50 files
and 2000 lines (5000 with `file=`), `cut` past them; an empty list means
nothing changed. Binary files, files over 1 MB and sensitive names (`.env*`,
`*.pem`, `*.key`, `id_*`, `*.p12`, `credentials*`) come with name, status and
counts only, in the passages too; ignored files are never read; a link shows
its target. Every line goes through the preview mask (1.56) with its sign
kept. Each git call has 5 s: past it «git did not answer in 5 s». Other
refusals: «no session <name>», «<path> is not among the changes», «changes
<arg>: expected nothing or file=<path>», «no entry <id> in the transcript»,
«no diff for <id>», «diff <arg>: expected the id of a transcript entry».
`/state` carries `sessions[].changes`, `{files, added, removed, at}` or null
(nothing changed, git silent, a session on another host); outside git it
counts the passages of the current conversation, so the pill opens the
`transcript` view there too. It is counted when a turn ends and at most once
a minute per session; a closed session keeps its last value. `ops`
lists `changes` and `diff`, both reads.
Contract 1.73 serves the app's Studio page (18/10/2026, additive, `v` stays
1). Op `studio` (`session` and `arg` null) is a read on request, never in
`/state`; `ops` lists it. It answers `{cases, deadlines, outcomes, error}`:
`cases` are the entries of `calendar.cases` in the file's order, `{id, name,
project}`, `project` as in `sessions[].project`; a value can be the folder or
`{"dir": folder, "name": name}`, and without a name it is the folder's last
piece. `deadlines` are the calendar's events from today to 60 days ahead,
the nearest first, `{uid, summary, date, days_left, due, case}`: `due` is true
inside the event's reminder window, `case` is the event's case id or empty.
`outcomes` are, for each folder of the cases (once, even when several cases
share it), the last 3 «Esito:» lines of its sessions, live or closed, from
the last 60 days, newest first, `{project, session, at, short}`; `session`
is the live session's name, otherwise the folder's. `error` is
cm-calendar's text when the feed does not answer (the deadlines then come
from the cached copy, or none). With the calendar off the answer is still
ok, with the cases and outcomes, no deadlines and `error` «calendar off».
A `uid` can repeat (recurring events): `uid` with `date` is the key.
A `prompt` with `scope: "studio"` gets, after the device's prefix, a
`[studio]` … `[/studio]` block — the cases with their folders, the deadlines
of the next 60 days, the line of `calendar.studio_rule` when it is set — a
blank line, then the text. An unknown scope is ignored. `transcript` and the
timeline drop the block, so the entry ends with the text sent.
Contract 1.74 serves the app's Night page (21/10/2026, additive, `v` stays
1). Op `nights` (`session` and `arg` null) is a read on request, never in
`/state`; `ops` lists it last. It answers `{nights: [{date, title,
generated_at, v}]}`, the reports on disk newest first, at most 60; `title` is
the report's short line («9 progetti: 7 fatti, 2 in parte»), null for a v1
report; with no report `nights` is empty and the answer is still ok. `night`
with a date opens one of them. The report itself is now `v` 2 (fixture
`night-report-sample.json`), a superset of v1: `title`; `window.end_source`
(`now` or `morning_message`, the person's first message after 06:00 from a
device or to the master), `window.morning`, `window.morning_message`, and
`start_source` `night_plan` when the night starts at its first plan
(`docs/proposte` with the night's date); session items carry `dir`, `host`
(`local` or the host's name), `role` (`master` or null), `spans` (`[[start,
end], …]`, work split by pauses over 20 minutes) and `assigned` (the message
another session gave it the work with, or null), and a session counts even
without a prompt from the person, sessions in a scratchpad never; `project`
is the repo, a worktree named `<repo>-…` included. Each project adds
`worktrees`, `hosts`, `spans` (`{session, host, start, end}`), `goals`
(`{title, file, at, points: [{title, state, done_at}], host, source:
proposta|assigned, reports, state, done_at}`), `reports` (the night's
`docs/notte` reports matched to no goal: `{file, title, at, fine, fine_at,
esito, stopped, decisions, host}`), `milestones` (`{at, kind: commit|push|
release|fine, text, ok, ref}`) and `where`, `{state: done|partial|stopped|
running|unknown, text, missing, needs_you}`. A goal's points carry its state
and time: the report does not say which point was done when.
Contract 1.75 serves the web's three-column home (21/10/2026, additive, `v`
stays 1). `/state` carries two new lists, absent with an older relay.
`hosts_status` has one entry per confirmed host of `supervisor hosts` (local
and cloud left out), `{name, up, at, error, cpu, ram_used, ram_total,
disk_free, disk_total, sessions}`, read from the snapshot the poller (`hosts
poll`, cron) writes at most once a minute while something runs there and every
10 minutes otherwise: `/state` never opens an ssh. `at` is the reading's epoch;
`cpu` is `load_pct` / 100 between 0 and 1 (on Windows the processors' current
load, on POSIX the one-minute load average over the threads); RAM and disk are
in bytes, the disk being the one holding the host's sessions root (`D:` on
win). The helper reports `ram_total_b`, `ram_free_b` and `disk` since 1.75; a
helper from before gives RAM from its GB figures and the disk null. `up` false
when the last reading failed («ssh: timeout»), when there is none («no reading
yet») or when it is older than two idle rounds («no reading for N min», the
poller stopped): `error` says which, every number is null. `sessions` are the
names of the live sessions on that host as in `sessions[].name`.
`published_today` is `[{at, kind, project, text, url}]` since local midnight,
newest first, at most 30 (`fit_state` cuts it to 10, then 5, before projects
and sessions). `kind` release: a tag of the day whose commit is on a remote
branch, `text` the tag without a leading «v», `url` the GitHub release page.
`kind` push: a remote branch updated by a push today (its reflog), once per
branch at its last push, `text` «origin/branch», `url` the branch on GitHub.
`kind` deploy: a `firebase deploy` run by a session with Bash whose result says
«Deploy complete!», `text` the `--only` targets (or «firebase») with the
Firebase project, `url` the Hosting URL; a failed deploy, one wrapped in a
script or `npm run`, and the Firebase MCP tool do not count. `url` is null for
a non-GitHub remote. The repositories are those of this machine's live
sessions and of the folders used today; sessions on another host stay out.
The list is counted by a detached `cm-relay.py published-refresh` at most once
a minute and kept in `<relay.dir>/published-today.json`; a copy from another
day is not served, so the first `/state` of the day may carry an empty list.

Contract 1.76 keeps the commitments visible (22/10/2026, additive, `v` stays
1; proposals P2-P4 of 21/10, approved by the maintainer at 23:58). `/state`
carries `commitments`, the task registry's view (`supervisor task impegni
--json`): every open task with a recorded ok or an approved plan, plus the
ones waiting for an ok, stalled first, then by idle time; absent with an older
relay, `[]` when there is nothing, cut to 10 then 5 by `fit_state` before
projects and sessions; the cut keeps first the tasks with an ok in
`approvals`, drops the paused ones first and leaves the rest in this order. Each entry: `task`, `title`, `project` (as
`sessions[].project`), `session` (who works on it, or null), `state`
(`proposed|assigned|running|awaiting_ok|approved`), `lane`, `plan`,
`approved`, `approved_at`, `approved_by`, `age_s` (since the ok, or since the
task when not approved), `last_step` (`{at, kind: state|approval|result|step|
amend|pause, text}`: only registry events, never a commit), `idle_s`, `next`
(the `--next` of the last `task step`, else from the state), `waits` (`{who,
on}`, `person` for the user: an `awaiting_ok` task waits on the person, every
other one on its session), `expected` and `outcome_kind` (the contract's
`outcome`, or null), `check` (`{outcome, at}` of the last recorded result, or
null), `paused` (`{reason, at, by}` or null, from `task pause`) and `stalled`
(approved, not paused, idle for `tasks.stall_hours`, 2 by default). An entry
leaves when `task done` is green or the task is cancelled. `/events` adds
`task_stalled`: one summary per guard round, `ref` the first task, `session`
the one whose turn it is, a line per task in `body`; in `tasks.quiet_hours`
(23-07) the person gets none and the morning report carries them. «Approve»
(op `approve`, unchanged) now also wakes the task's session with a `talk`
(«the user approved X at HH:MM: what. Go on, and close with task done X»),
tells the master with another `talk` when she is not the one, and removes the
session's «waiting for you» (`needs-you/<sid>.json`); the night report v2
carries `commitments` with the same entries. The app's request
(`docs/richieste/2026-10-22-impegni-approva.md` in supervisor-app) also asked
for `release`/`installed_at` fields, per-commitment events, `covers[]` on the
approvals and pause/step ops: the registry does not know a release or an
installation, the list in `/state` replaces per-item events, the «covers»
proposal is a later step, and pause/step stay CLI verbs of the sessions and
the master for now.

Contract 1.77 serves the «Fatto» block of the app's single «Lavori» page
(22/10/2026, mockup approved by the maintainer at 12:43; additive, `v` stays
1). Op `done` (a read, last in `ops`): `arg` a day `YYYY-MM-DD`, null for
today; `text` is `{date, rows, days, more}` from `supervisor task closed`.
`rows` are the tasks closed that day (`task done` green, auto-close,
`cancel`, the third red), newest first: `{task, title, project (as
sessions[].project), session, at (epoch of the closing), outcome:
green|cancelled|failed, proof}`. `proof` is one `{kind, value, url}` chosen
in this order: `release` when the check names a tag (`git tag -l v…`, `gh
release view …`, `tags/…`; `url` the GitHub release page when origin is on
GitHub), `commit` when the check or the outcome names a hash (short hash,
`url` the commit page), `file` when the outcome names a `docs/…md` report,
else `check` with the check's first line; `url` null when unknown. `days`
lists up to six earlier days with at least one closed task, `{date, n}`,
newest first. Rows beyond 60 KB are cut from the end and `more` is true. A
bad day is refused; without a registry `ok` is false, «no task registry». A
pause that waits for the person (`task pause ID --reason R --waits person
[--since T]`) shows in `commitments` with `waits = {who: the session, on:
person}` while it lasts, `paused.at` the real start when `--since` says it
(else the pause's record), never stalled; `task resume` brings `waits` back.

Contract 1.78 removes every emoji from the texts the relay and the plugin
generate for the app and the notifications (22/10/2026, the maintainer from
the web at 13:36: «non voglio emoji nell'app o nelle notifiche, solo svg o
fontawesome»; additive, `v` stays 1). Event titles are the plain name or
text (`ledger-api`, `atlas-shop`, `96 % personal`, `state stopped for 11
min`), the bodies of the recap, the night report and the quota events carry
no marks (`ok`/`KO` before a night job's name), the guard's and `needs-you`'s
messages, the stall and task-check titles, the question notices on Telegram
and the wrist lose their leading symbol. The app picks the icon from `kind`;
`task_check` events add `ok` (true when the check is green) because their
title no longer says it; the `preview.title` of an event follows the title.
The sessions' `icon` (the tab's colour circle) is data, not decoration, and
stays. Fixtures: `events-sample.json` and `local-api.json` without emoji,
`task_check` with `ok`.

Contract 1.79 gives every row of the app's «Lavori» page a short title
(22/10/2026, the maintainer's ok from the phone at 21:16 on the mockup of the
«In corso» block; additive, `v` stays 1): one line, at most 28 characters,
and the app never writes «…». Each entry of `/state.commitments` and each row
of op `done` carries `short` right after `title`: the task's `task.short`
when the contract has it (`supervisor task short ID "text"`, or `short` in
the contract given to `task add`, 1-28 characters), else one derived from the
title: spaces normalised, a leading `name: ` prefix without spaces dropped
(the project or the session, which the entry carries apart), whole words up
to 28, no comma, colon, open parenthesis, article or preposition left at the
end; a first word longer than 28 is cut at 28. The rows of op `agenda` (and
the row `agenda_set` answers with) carry `short` the same way: column 8 of
`relay.agenda_file` when written (`state, scope, blocks, title, ref, detail,
until, short`; the master writes it), else derived from the title. Fixtures:
`state-2-idle.json` and `state-3-stale.json` (one explicit short, the others
derived), `cmd-result-sample.json` (…0386, …0470-…0474, …0760),
`night-report-sample.json`.

Contract 1.80 (23/10/2026, the maintainer: «le richieste in attesa appaiano
solo quando ci sono davvero e scompaiano quando non ci sono più») gives every
`attention` item a `closes` field — `answer` (a question), `prompt` (the
session's next prompt, or its end), `check` (the relay verifies it: a
`needs-you --until` command run once a minute, a red task) — and, for a
`needs_you` item, `expires_at` (`--expires`, 24 h without); the session's
`needs_you` carries `until: true` and `expires_at` too. A request that comes
true, lapses, or belongs to a session gone for a day disappears by itself,
with a ledger row and (for the first two) a message in the session's inbox.
The new op `attention_done {kind, ref, since?}` is the app's «Done» button:
`needs_you` → the file goes and the session hears «done» from the person;
`turn_blocking` → closed for that turn (`attention-dismissed.json`), a new
turn shows it again; `turn_failed`, `quota_limit`, `task_red` → removed.
Without 1.80 the app works as before: the new fields are additive.

Contract 1.81 (23/10/2026, the app's «scelte con tasti»: a task that asks a
choice shows one button per option instead of «Approve») adds `options` to an
`approvals[]` entry — two to four short labels (1-24 characters), from
`supervisor task wait-ok ID --what W --where D --options "A|B"`, absent when
the task asks only an ok — and `choice` to the op `approve`: one of those
labels, required when the entry has `options`, refused when it has none. The
registry records the ok with the choice (`task approve ID … --choice B`), the
result reads «approved <task> (B)», and the waiting session hears the choice
in its inbox («…: <what>; scelta: B. Prosegui…»). A label not among the
options → «<task>: choice X not among A, B»; a missing one → «<task>: choose
one of A, B». A standing ok never picks: a task with `options` always waits
for the person. Fixtures: `state-2-idle.json` (an approval with `options`),
`cmd-result-sample.json` (an `approve` with `choice`, one refused). Without
1.81 the app works as before: the field is additive.

Names (23/10/2026): a session on a remote host is `win:bluey` in
`sessions[].name` and `win-bluey` in `sessions[].remote_name` — the real
name on that host, the one the registry, `talk` and the lane guard use.
Inside `/state` the first one holds: `commitments[].session`, `waits` and the
`attention` items from the registry carry the state's name, so the app joins
on `sessions[].name` and nothing else.

Contract 1.82 (24/10/2026, the maintainer: «Nel riepilogo della notte, ogni
scheda deve essere cliccabile e portarmi alla sessione che ha lavorato»):
every `timeline` item of the night report carries `session_id` when it is
known — for a `session`, the id of the conversation that worked last (the
transcript's file name, a remote one's local copy too); for a `night_job`,
the id of its `claude -p` conversation (same folder, inside the job's
interval) — absent otherwise, never null. The op `transcript` also reads a
closed conversation: `session` null and `sid` = that id; the relay looks for
`<sid>.jsonl` under `projects/` of every account, then among the local copies
of the remote sessions, and answers as always (`{entries, more}`, same `arg`);
unknown → «no transcript for <sid>». Read only: nothing can be prompted.
Fixtures: `night-report-sample.json` (a closed session and a night job with
`session_id`), `cmd-result-sample.json` (a `transcript` with `sid`, one
refused).

Contract 1.83 (24/10/2026, the maintainer approved the proposal of 24/10:
«le domande dei server MCP sul telefono»): an MCP server that asks for input in
the middle of a tool call (elicitation) reaches the phone. The plugin's
`Elicitation` hook (every server) writes the question to
`<state_dir>/elicit/<id>.json` and waits up to `hooks.elicitation.wait_s`
(120 s, at most 140; the hook has 150): `/state.elicitations` lists the open
ones, oldest first — `{id, session, server, message, mode, asked_at,
expires_at}` plus `url` (mode `url`) or `fields` (mode `form`) — and the event
`elicitation` (title the server, body the question, ref the id) rings the
phone. A field is `{key, title, required, kind}` with `kind` one of `choice`
(`options` `[{value, label}]`, one button each: from `enum`, `enumNames` or
`oneOf` with `const`), `boolean` (Yes/No), `text` (`min_length`,
`max_length`, `format` email | uri | date | date-time), `number` or `integer`
(`min`, `max`); `description` and `default` when the schema has them. The new
op `elicit {arg: id, action: accept | decline | cancel, content: {key:
value}}` answers: the relay checks the values against the fields and writes
`<id>.answer.json` with origin `relay`; the hook passes it to Claude Code
(`hookSpecificOutput` with the action and the content). Results: «answered
<server> (<id>)», «declined …», «cancelled …»; refused: «<id>: no question
waiting» (unknown, expired or answered), «<id>: <server> asks to open a link,
answer in the terminal», «<id>: <key> is required», «<id>: <key> must be one
of A, B», «… must be true or false», «… must be a number», «… must be a
whole number», «… must be between X and Y», «… must be X to Y characters»,
«<id>: no field <key>», «<id>: already answered». Nothing answers by itself:
no standing ok, no default filled in. The question stays in the terminal —
no output from the hook — without a paired device, with
`hooks.elicitation.enabled` false, with a schema the phone cannot show (an
array, an object, more than 8 fields, more than 12 options) and when nobody
answers in time; then the card disappears. Mode `url` (a sign-in in the
browser): the card shows the link, the hook does not wait and the dialog
stays; `ElicitationResult` (an answer given in the terminal) removes the
session's cards, otherwise they lapse at `expires_at`. Fixtures:
`state-2-idle.json` (a form with the four kinds and a link),
`events-sample.json` (an `elicitation` event), `cmd-result-sample.json` (an
`elicit` accepted, two refused). Without 1.83 the app works as before: the
field and the event are additive.

Contract 1.84 (24/10/2026, asked by the app after the maintainer chose «Tutto» on
the mockup of the home page: the master's suggestions, true states, closing with the
state saved, today's quota per session). All additive, `v` stays 1.

- **Suggestions.** `supervisor suggest add --kind K --key KEY --text T --prompt P
  [--ttl 12h]`, `suggest list [--json]`, `suggest clear [--kind K]`. The master
  writes them twice a day (8:00 and 19:00, from the registry and the diary). `kind`
  is `recurring`, `cleanup`, `deadline` or `quota`; `key` is stable for the same
  suggestion and is what «Never» remembers; `text` is one line and never carries
  «…»; `prompt` is what the master receives. `/state.suggestions` lists
  `{id, kind, key, text, prompt, at, expires_at}`, newest first, at most 6 (`[]`
  when there are none; fit_state cuts them to 3; since 1.88 a queue of 12 without
  `prompt`, see below). The op `suggestion {arg: id,
  action: accept | later | never}`: accept sends `prompt` to the master like a
  prompt from the app, labelled «[dai suggerimenti]», and drops the item; later
  hides it until the next round (an `add` with the same key brings it back, with
  the new text); never drops it and puts its key on a list `add` then skips
  silently (exit 0, no output). Results «sent to master», «hidden until the next
  round», «never again», and since 1.87 «already gone» (ok) for an id no longer
  there. The file is `suggestions.json` next to the config.
- **Today's quota per session.** `/state.usage_today` lists `{session, account,
  tokens, share}`, biggest first, at most 12 (fit_state: 5). `tokens` are the
  tokens since local midnight from the transcripts of every account (input,
  output, cache, each message once; sessions closed today, the master and the
  subagents included), `share` their part of the account's total (0..1). The count
  is incremental (`usage-today.json` in the relay folder keeps where each file was
  read up to) and redone at most every 3 minutes; sessions on another host are not
  in it.
- **needs-you tied to a task.** `supervisor needs-you "<what>" --task ID`: when the
  task has a live session (its registry session, else `where.session`), the
  request goes on that session's `needs_you`, with `task` and `from` (who asked);
  otherwise it stays on the asker, with `task`. Both fields are optional.
- **Closing with the state saved.** The op `close_preview {arg: "a,b"}` (a passive
  read) answers `{sessions: [{name, closable, blocked_by, save}]}`: `blocked_by` is
  `busy`, `question`, `background` (a shell among the claude process's children: a
  Bash in the background or a job it started), `attached`, `gone` (not running) or
  null; `save` (only when closable) is `{outcome, next, session_id, dirty_n,
  dirty}`, with `dirty` the first 20 paths of `git status --porcelain` and null
  outside git. The op `close {arg: "a,b"}` checks again, saves each closable
  session to `<state_dir>/closed/<tmux>.json` (outcome, next, dirty, session_id,
  closed_at, by), then runs `supervisor close`; the text says who is closed and who
  is not, with the reason; ok when at least one is closed. `/exit` from the app (op
  `slash`) takes the same road: the checks (an attached session is fine there: the
  command is typed in its pane), the save, then the command.
- **Closed sessions and «Resume».** Every `gone` session carries `closed`: always
  `{at, session_id}`; from a save also `outcome`, `next`, `dirty_n`, `dirty`
  (fit_state empties `dirty` before cutting sessions, `dirty_n` stays). A session
  closed at the PC carries only what the relay knows, `at` and `session_id`, while
  its outcome and next stay in the session's own fields. A save keeps the session
  in the state for 7 days even after `close` drops it from the registry. `reopen`
  of a saved session runs `launch --resume <closed.session_id>`; without that
  conversation on disk it refuses («the saved conversation … is not on disk: use
  launch»), never `--continue`.

Fixtures: `state-1-question.json` (`suggestions` empty, the gone `orbit-docs` with
`closed {at, session_id}`), `state-2-idle.json` (three suggestions of different
kinds, four `usage_today` rows on two accounts, a `needs_you` with `task` and
`from`), `state-3-stale.json` (a gone session closed from the app with two
uncommitted files), `cmd-result-sample.json` (a suggestion accepted, one already
gone; a `close_preview` and one with no session; a `close` with one session kept
and one of a busy session only, refused).

Contract 1.85 (25/10/2026, asked by the maintainer from the phone: at the limit of
one model the quota stop offered «another session», not «go on with Opus»).
Additive, `v` stays 1.

- **The limit of a model.** Claude Code writes it in the transcript as a
  `<synthetic>` message («You've reached your Fable limit. Run /usage-credits to
  continue or switch models with /model.»); the StopFailure hook only gets the
  generic 429. The hook now passes the error text and the transcript path, and the
  relay reads the last assistant message when the text names no model. Then the
  `quota_limit` event has `window: "model"`, the title «<name>: Fable limit
  reached» (with «, reset at HH:MM» only when the message says the time), and the
  new field `other_model`: the label of the model to go on with («Opus 5.5»),
  null when the stopped model is already Opus. `actions` is `other_model` first,
  then `other_account` when there is one, then `at_reset` only with a time.
- **The op `quota_route {arg: "other_model"}`** switches the session to
  `claude-opus-5-5[1m]` the way `supervisor model` does (stopped at the prompt,
  the picker, the confirmation in the pane), then sends it «continua»; result
  «<name> continues with Opus 5.5», or the first line of the refusal.
- **No more `pass`.** No `quota_limit` proposes `pass` any more (another session
  does not help when the quota is over): the 5-hour and weekly windows offer
  `other_account` and `at_reset`. The op still accepts `pass:<session>` from an
  older app.

Fixture: `events-sample.json` (the 5-hour `quota_limit` without `pass`; a new
one with `window: "model"`, `other_model: "Opus 5.5"`, `actions: ["other_model",
"other_account"]`, `reset_at: null`).

Contract 1.86 (26/10/2026, for the charts 2A and 2B of the app's Usage page,
chosen by the maintainer on the mockups of 25/10). Additive, `v` stays 1.

- **The week's hourly samples.** `quota.<account>.w7_points` is `{start, steps}`:
  `start` = `reset_w7` minus 7 days (epoch s); `steps` = `[[hour, pct], …]`, the
  hour counted from `start` and the weekly percentage (integer) at the last
  reading of that hour, an entry only when the percentage changes: it holds until
  the next entry (an hour with no reading is an hour the quota did not move, as
  far as this PC knows). A reading's time is the time the status line wrote it,
  not the time the relay looked: an idle account adds nothing. Samples start with
  this version: no history before it. The week before drops at the reset. At most
  168 entries; fit_state never drops it. Absent without a reset or without
  samples.
- **Where the week came from.** `quota.<account>.w7_origins` is `{sessions,
  automations, requests}` in points of `w7` (one decimal, the sum is `w7`): every
  transcript of the account since the reset, each turn given its origin with the
  classification of `supervisor quota --master` — `requests` the maintainer's
  (phone, web app, terminal, reports, accepted suggestions), `automations` the
  agreed ones of `automations.json` (the evening recap included), the scheduled
  turns not in the list and the compactions, `sessions` a message of another
  session and the scripted runs (`claude -p`); the results of background work stay
  with the turn that started them. Weighed in input-equivalent tokens like the
  fable-director receipts. Absent until the first reading of the week's
  transcripts is complete (it is split over several collections, at most 4 s
  each), and without a reset. Sessions on another host are not counted.

Fixture: `state-2-idle.json` (`personal` with `w7_points` — steps at hours 0, 2,
3, 24 and 25 — and `w7_origins`; `work` with neither: no samples, the count not
complete).

Contract 1.87 (27/10/2026, from the phone: after a suggestion was cleared on the
PC the card stayed on the home page, and «Not now» on it answered «unknown
suggestion»). `v` stays 1.

- **A suggestion no longer there is already gone.** The op `suggestion` on an id
  that is not in `suggestions.json` any more (cleared with `suggest clear`,
  expired, already answered from another device) answers `ok: true`, «already
  gone»: the app drops the card, as after «Not now».
- **The state follows the suggestions at once.** `suggestions.json` and the ids
  of the live suggestions are part of the push fingerprint: an `add`, a `clear`,
  an answer or an expiry republishes `/state` at the next beat, instead of the
  light beat resending the old list until the next full collection.
- **The master's usage alerts are notifications.** `supervisor quota --master
  --notify` (before: `--suggest`, which made them `quota` suggestions with Accept,
  Not now and Never) sends the new alerts of a round as one `week_pace` event
  (1.67: quiet, no buttons, opens Usage), `account: null`, `ref:
  "master-usage"`: one alert is the title; several are counted in the title and
  listed in the body, one per line, then `supervisor quota --master`. The same
  alert at most once every 24 hours.

Fixture: `cmd-result-sample.json` (the suggestion answered «later» on an id no
longer there: `ok: true`, «already gone»).

Contract 1.88 (27/10/2026, the suggestions queue, approved by the maintainer from
the phone on the design of 26/10: three suggestions at a time, the next one as soon
as one is used, new kinds, the master's own proposals). Additive, `v` stays 1.

- **New kinds.** `kind` adds `choice` (a choice waiting outside the registry, with
  `options`), `followup` (a next step left halfway in an idle session), `unsaved`
  (files a closed session did not commit), `waiting` (an external wait past its
  recheck), `idea` (an idea with a date) and `proposal` (something new the master
  proposes). An app that does not know a kind shows it with a neutral icon and the
  label «Suggestion», never as `quota`.
- **The queue.** `/state.suggestions` lists up to 12 items (fit_state: 6), ordered
  by `rank` (0-9, 0 on top) then newest first. Every item is `{id, kind, key, text,
  at, expires_at, rank, source}` plus `options` (2 to 4 labels) on a `choice`;
  `source` is `script` or `master`; an item written before 1.88 has rank 5 and
  source master. `prompt` is no longer in the state: the relay reads it from
  `suggestions.json` when the answer comes. The app keeps showing three (the
  variety rule, at most two of the same kind among the three, is the app's); when
  one is used the next push brings the next one of the queue. An item with a date
  (`suggest add --after`) is not in the state before that day.
- **The op `suggestion`** adds `action: choose` with `choice: <label>` (one of the
  item's `options`; the master gets the prompt with the choice: «sent to master with
  the choice»; a label outside them answers `ok: false`, «suggestion: «X» is not one
  of its options», and the item stays) and `action: discuss` (the master gets «Let's
  talk it over, without doing it: <text>», labelled «[dai suggerimenti]», «sent to
  master to talk it over»; the item stays). The app sends `discuss` instead of the
  prompt «Parliamone» it composed itself, so the ledger sees it.
- **«Not now» and the hourly script.** `suggest fill` runs every hour: it refreshes
  its own items in place (same id, same `at`), so an item in «Not now» stays hidden
  until the master's round (`suggest reshow`), as before. Its items last 26 hours and
  are renewed while the condition holds; when the condition is gone the item leaves
  as `resolved`.
- **The ledger.** Every `suggestion` row carries `kind` and `source` (and `choice`
  on `chosen`); `action` is `accepted`, `hidden`, `never`, `chosen`, `discussed`, or
  from the script `expired` (gone untouched) and `resolved`. The 14-day acceptance
  rate per kind (accepted and chosen over accepted, chosen, never and expired) is in
  the master's digest (`suggest digest`); the KPI field is not in 1.88.
- **CLI.** `suggest add --rank --source --options --after`, `suggest fill`,
  `suggest reshow`, `suggest kind on|off K`, `suggest digest`; `task pause ID
  --waits external --recheck 2d|ISO` (an external wait; `impegni --json` carries
  `paused.waits` and `paused.recheck`).

Fixtures: `state-3-stale.json` (one item for each new kind: the `choice` with
`options`, the three of the script with `source: "script"`; state-2 is already
close to its 8 KB, so the new kinds live in state-3), `state-2-idle.json` (the
three items with `rank` and `source`, without `prompt`, by rank),
`cmd-result-sample.json` (a `choose` outside the options refused, a `discuss`, a
`choose` accepted).

Contract 1.89 (28/10/2026, the last sign of life of every paired device,
accepted by the maintainer from the suggestions: «the list of devices in the app
shows for each the last arrival of a push, with Revoke beside it»). Additive, `v`
stays 1.

- **`Cmd.uid`** (optional): the Firebase uid of the device that sends the command,
  the same uid it paired with (`/allowed`). Phone, watch and the web app put it in
  every command, inside the encrypted document (bus and local API alike). The
  relay keeps the time of the last command per uid; a uid that is not paired is
  ignored, and a command without `uid` works as before.
- **`/state.devices[].cmd_at`** (epoch s): the last command received with that
  device's uid. Present only when there is one: an app that does not send `uid`
  yet leaves its device without it.
- **`/state.devices[].seen`** is now also the last arrival kept by the relay: the
  latest of `/seen/<uid>` and the arrival stored the last time `/seen` changed, so
  a device whose `/seen` node is missing still shows when it last read.
- Both live in `device-activity.json` next to `devices.json` (`{uid: {arrival,
  cmd}}`), not in `relay.log`, so they survive its rotation; a revoked device
  leaves the file at the next write. The last sign of life is the later of `seen`
  and `cmd_at`: the app computes it. Revoke is the existing op `unpair`.
- `cmd_at` reaches `/state` with the next full push (a command that changes
  something asks for one; a read waits for the next).

Fixture: `state-3-stale.json` (the phone with `cmd_at`; the browser with `cmd_at`
and a `seen` from the relay's arrival, no `/seen`; the Pixel 7 with a `seen` from
the relay's arrival only).

Contract 1.130 (07/11/2026, the maintainer at 20:16: «ok … ti aspetta (o decidi)», the mockup approved at 21:04; task supervisor-attese-link-07-11, the card is app-scheda-ti-aspetta-07-11). Additive, `v` stays 1.

- **A «wait»** is any question waiting for the person, with a stable id: `w` and 10 hex digits from its key (task and time of the request, session and time of the turn, question id), the same in every read, a new one for a new request. `type` `carta` (from the registry: ok, choice, pause waiting for the person, proof, login) or `chat` (a turn closed with a «Next:» item marked «!», a `needs-you`, an AskUserQuestion).
- **Link**: `<relay.web_app_url>/#/attesa/<id>`; the Android app opens the same address. `relay.web_app_url = ""` gives `link: null`. The master's «Ti aspettano» line (UserPromptSubmit hook) now lists the ok cards too and puts each item's link; the master copies it into its «ti aspetta X» reminders.
- **`/state`**: `wid` on `attention[]` entries that are questions (`question`, `needs_you`, `turn_blocking`; absent on the others) and on every `approvals[]` entry. The `decisioni` items (ok, pause, needs_you, login) and the `prove` items carry `wid` too (one per task for the proofs).
- **New op `attese`** (a passive read): `{at, items}`, the open waits from the oldest. Each item: `{id, type, kind, label, label_text, session, task, text, options, at, card, link, answer}`.
  - `kind` `ok | choice | pausa | prova | login | prossimi | needs_you | question`; `label` `decide | choose | verify | reply` and `label_text` in the config's language («Decidi», «Scegli», «Verifica», «Rispondi»).
  - `options` `[{n, label, value, recommended?, description?}]` or null: `label` clean, `recommended` true for «(Recommended)»/«(consigliata)» at the end, `value` what the answering op wants (the label as written in the request).
  - `card` for ok and choice cards: `{what, where, untested, undo, undo_how}`, the fields wait-ok has (absent keys = not written), null otherwise.
  - `at` the time of the question (how long it has waited = now − at).
  - `answer` the op that answers, the same as the card's buttons: `{op: approve, arg: task}` (with `choice` for a choice), `{op: prova, arg: "n,n"}`, `{op: decisione, arg: group key}`, `{op: answer, arg: question id, session}`, `{op: prompt, arg: session, done: {op: attention_done, …}}` for a «Next:» (the chosen option is the prompt), `{op: attention_done, arg: session, kind: needs_you}`; null for a login (the master does it).
- **New op `attesa`** (a passive read, slow lane): `arg` the id. The item as in `attese` plus `msg`, where the question was explained: `{session, session_id, id, uuid, text, key, at}`. `id` is the transcript entry id (`<uuid>.<i>`, the same as in `transcript`: «Open in the transcript» asks `transcript` with `n:around=<id>`, and `session_id` for a closed session), `uuid` the message's uuid, `text` up to 1500 characters, `key` the sentence to highlight (the one sharing most words with the question and its options). `msg` is null when the transcript has no trace of it (an old request, a session on another host without a local copy). An invalid id or a wait that is no longer open is an error («<id>: this question is no longer waiting …»).
- Fixtures: `state-1/2/3` (`wid` on attention and approvals, `attese` and `attesa` in `ops` after `approval_close`; some test texts shorter to keep the 8 KB cap), `cmd-result-sample.json` (…1300 `attese`, …1301 `attesa` with `msg`, …1302 a wait no longer open).

Contract 1.129 (06/11/2026, the maintainer at 15:06: the choice cards must always close and never stay stuck; task supervisor-approval-close-06-11). Additive, `v` stays 1.

- **New op `approval_close`**, from a touch like `approve`: `arg` the task id (an entry of `approvals`), `text` the reason (optional, at most 300 characters). The relay withdraws the request for an ok without approving it: the registry records it as superseded («carta di ok chiusa da <by> senza ok (superata): <reason> (dal telefono)», in the task's steps and in the tidying log, never as an ok), the task goes back to `running` (or `assigned` when it has no session), and its session and the master get a `talk` saying the card was closed without an ok. The entry leaves `approvals` at the next push.
- **Answer**: ok with `{task, closed, state, text}`. `closed` is false when the task no longer waited for an ok (already approved, done, cancelled, or the card already closed): still ok, so the app removes the card in every case. Only an unknown or invalid id is an error («<id>: compito sconosciuto»).
- Fixture: `cmd-result-sample.json` (…1290 a choice card closed, back to `running`; …1291 a task already approved, `closed: false`; …1292 an unknown task, the error). Added after 0.8.137 at the app's request.

Contract 1.128 (06/11/2026, the maintainer: «dobbiamo migliorare la gestione dei riavvii improvvisi e gli allineamenti»; task supervisor-riavvii-allineamenti-06-11). Additive, `v` stays 1.

- **New event kind `plugin_stale`**: an account's supervisor plugin cache is behind the checkout (what `supervisor doctor` calls `plugin_stale`: the sessions' hooks run the old version, the commands the new one). The guard checks every 10 minutes and sends it once per (account, cache version, checkout version), after the mismatch has lasted 30 minutes (a release moves the checkout a few minutes before it installs). `account` and `ref` are the account, `session` null, `title` «Plugin supervisor indietro: <account>», `body` the versions and the command that aligns them. The master gets the same text with a `talk`; Telegram is the fallback as for the other guard events. An app that does not know the kind can show it as a plain notice.

No fixture change: an event has the shape of the others in `events-sample.json`.

Contract 1.127 (06/11/2026, the maintainer: «ho usato la ricerca, ma mi apre semplicemente la master sull'ultimo messaggio … la ricerca avrebbe dovuto riportarmi a quel punto del transcript della chat»; task supervisor-trascrizione-attorno-06-11). Additive, `v` stays 1.

- **op `transcript` takes `n:around=<id>`**: the `n` entries around that entry (the `entry` of a `search` hit), about half before and half after; near the start or the end the page moves to the other side, so it still holds `n` entries when there are. The entry may be in an earlier conversation of the same session (1.126): the page then carries the separators it crosses. The answer adds `anchor` (the id asked for) and `more_after` (true when newer entries follow); `more` keeps its meaning (older entries before). From there the app scrolls with `before=<first id>` and `after=<last id>`, which follow the chain both ways. An id that is not there is refused with «no entry <id> in the transcript», as `before=` is; pages without `around` carry no new key.
- **`search` hits gain `sid`** (the conversation's id). A hit in an earlier conversation of a live session (before its `/clear`, a restart or a resume) now carries that session's name and `live: true`, so the app opens the session and asks `around`. For a hit with `live: false` the app asks `op transcript` with `sid` and `session: null` (1.82), also with `around`.
- With `around` the page is trimmed to 60 KB from the end farther from the entry, which always stays.

Fixture: `cmd-result-sample.json` (…1240 a search hit before a `/clear` of a live session, …1241 the page around it, with the separator; …0170 hits now carry `sid`).

Contract 1.126 (05/11/2026, the maintainer: «con il clear automatico rimangono visibili solo i prompt inviati e non i transcript di risposta»; task supervisor-catena-trascrizioni-05-11). Additive, `v` stays 1.

- **op `transcript` follows the session across its conversations.** After a `/clear` (also the automatic one after a handoff), a restart or a resume, a session writes a new transcript; until now the chat showed only that one. Now, when a page reaches the start of the current conversation, or the id asked for is in an earlier one, the relay goes on into the conversations before, of the same session, up to 7 days (at most 12). A chat open across a `/clear` that asks `after=<id of before>` gets the new entries, no `reset`.
- **A new entry role, `separator`**, between two conversations: `{id, role: "separator", reason: "clear"|"startup"|"resume"|"restart"|"compact", at (epoch of the new conversation's start, from the PC), memory: "none"|"handoff"|"all"|"summary", handoff (the file announced, or null), text}`. `memory` is what the session remembers from there on: nothing after a `/clear` or a clean restart, only the handoff when one was announced, all after a resume. A compaction inside a conversation gives a lighter one (`reason: "compact"`, `memory: "summary"`) at its place. `text` is a readable line in the relay's language («/clear alle 18:52: da qui la sessione ricorda solo il passaggio di consegne»), so an app that does not know the role can still show it.
- The chain comes from this computer's session ledger; sessions on win have no chain yet (no hooks there).

Fixture: `cmd-result-sample.json` (…1230 a page across an automatic `/clear` after a handoff, with its separator).

Contract 1.125 (05/11/2026, the maintainer: «All'avvio di una sessione, o al clear o al riavvio, le informazioni sul modello mancano nell'header … se si cambia modello durante un turno, l'operazione riesca»; task supervisor-modello-stato-05-11). No new key, `v` stays 1.

- **`sessions[].model` and `effort` are there before the first turn.** After a start, a `/clear` or a restart the transcript has no turn yet. The model now comes from Claude Code's own SessionStart payload (`model`, measured in 2.1.318), noted for the session until a newer turn says it; the effort, and the model when that is missing, from what Claude Code gives the status line (fable-director's snapshot in `relay.window_hint_dir`). Never over a turn or a change from the selector. A session of win gets them at its first turn, as before (no hooks there).
- **op `model` and `effort` on a busy session succeed**: answered ok with «<name> is working: model <label> is chosen and applies at the end of the turn, before the next prompt» (effort alike). The change waits, `sessions[].model`/`effort` show the chosen value at once, and supervisor applies it as soon as the turn ends (the Stop hook here; the hosts poller for win), with the same checks as before. A session on a dialog or with text typed is still refused with its reason (rc 3); the effort refusal sample …0121 is now this answer.

Fixture: `cmd-result-sample.json` (…0121 effort on a busy session, ok with the pending text; …1220 model on a busy session).

Contract 1.124 (05/11/2026, from the master for the app: with the private repository «Invia dal mio GitHub» works only for who can read it; task supervisor-feedback-github-ok-05-11). Additive, `v` stays 1.

- **The draft of `feedback_app` gains `github_ok`** (bool, before `options`): true only when the GitHub account of the relay's computer can read frsorrentino/supervisor-feedback (`gh api repos/frsorrentino/supervisor-feedback` answers 200); false on 404, without gh or without its login. The answer is remembered 10 minutes; a gh that does not answer gives false and is asked again next time. The app shows «Invia dal mio GitHub» only with `github_ok: true`, and hides it when the key is missing (a relay before 1.124). `options` does not change.

Fixture: `cmd-result-sample.json` (…1210 the draft with `github_ok: true`).

Contract 1.123 (05/11/2026, the maintainer: «Nel Feedback l'immagine scelta probabilmente non arriva su GitHub: l'invio anonimo la scarta e da GitHub va aggiunta a mano. trova rimedio»; task supervisor-feedback-immagine-05-11). Additive, `v` stays 1.

- **op `feedback_app`, no new field in the command**: the screenshot still comes as `share` with `screenshot_ok: true` (1.121), as a JPEG or PNG. It now reaches the issue on both roads. Over 1 MB it does not go: the app should send it already reduced (1600 px, JPEG at 85 %).
- **Where it goes**: issues and screenshots go to the **private** repository frsorrentino/supervisor-feedback (the maintainer's choice, 05/11 12:51): nobody cleans what an image shows, so only the maintainer sees it. Before, the issues went to the public frsorrentino/supervisor.
- **The draft** gains `screenshot_notice`: with a screenshot, the line the sheet shows before the buttons («La schermata la vede solo il manutentore, nel repo privato dei feedback, così com'è: nessuno ne toglie nomi o dati. Guardala prima di inviare»); null without. The descriptions of `github` and `anonymous` say the screenshot goes with it and the repository is private; `github` says it works only for who can read it.
- **`anonymous`**: the screenshot goes to observe's service with the text, and the labels `app` and the kind; the service removes its metadata, stores it with a random name under `images/` of the private repository and links it at the end of the issue.
- **`github`**: the screenshot is uploaded first, the same way; `url` is the link of the issue already written on the private repository, its body ending with `- screenshot: ![screenshot](<link>)`. The person sends it without attaching anything; a person who cannot read the repository gets GitHub's 404, so the app may keep this button for the maintainer only.
- **The answer** gains `screenshot_url`: the image's link for `github`, null otherwise (for `anonymous` the link is in the issue). When the screenshot could not go (over 1 MB, the service's limit of the day, a service that refuses the image), the send still goes, without it (for `anonymous` the text is sent once more alone), and `text` says so and why.

Fixture: `cmd-result-sample.json` (…1210 the draft with `screenshot_notice`, …1211 anonymous with the screenshot, …1212 github with `screenshot_url` and the link in the body, …1213 later with `screenshot_url` null).

Contract 1.122 (05/11/2026, from the master: at 09:50 rule 1 of the tidying took tests #102-#106 of a pre-release out of «Prova» because a newer one contains it, and closed the pauses of four tasks never tested; at 10:29 an unrelated task closed green on the newer pre-release would have closed them as superseded; task supervisor-supera-prove-05-11). No new key, `v` stays 1.

- **Rule 1 carries a test over instead of taking it away.** When a newer tag of the same project contains the build of a test still to do, a test of the bench is retired and the same test (task, `dove`, `fai`, `atteso`) is written on the newer build: a new id in op `prove`, so an id the app showed before is refused as gone and the next read has the new one. A pause with `prova` stays open: its `prova.release` becomes the newer tag and its `reason` names it (the old tag in the text replaced, or «· si prova sulla pre-release <new>» added). The row in `state.tocca.today` says «portata avanti» (it), «carried over» (en).
- **A task closes as `superseded` only with a test of its own ok on the pre-release that carried it**, and nothing taking it up again (no open or new pause, no test to do, no step of its session). Another task of the project closed green on that pre-release closes nothing: its tests speak of its own work. A pause with a test is answered as before («Tutto va» / «Non va»), never closed by rule 1.

Contract 1.121 (04/11/2026, the maintainer from the phone: a Feedback button in the app for whoever uses it, by the observe road; task supervisor-feedback-app-04-11). Additive, `v` stays 1.

- **New op `feedback_app`**, only from a touch (`device`, as every feedback op), in two steps.
- **First step, the draft**: `kind` (`suggerimento` or `bug`), `text` (at most 4000 characters), `tech` (optional, the technical data the app adds by itself: `{key: value}`, keys `[a-z_]{1,20}`, at most 10, values cut at 80), `share` (optional, the id of a `/share` upload with the screenshot, as for «Condividi») together with `screenshot_ok: true`, the person's yes; a `share` without it is refused and the upload deleted. The relay records an observation of `supervisor-app` in observe's box (a note, `source: "app"`, `app_kind`, `tech`; the screenshot, if any, kept with it on the relay's computer, never sent) and answers ok with a JSON text: `{hash, kind, label, title, body, screenshot, options}`. `title` and `body` are the anonymized draft (home, file names, emails, secrets cleaned as in observe), in the form the anonymous service accepts. `options` are the three buttons, in order: `{id: "github", label: "Invia dal mio GitHub", description}`, `{id: "anonymous", label: "Invia anonimo", description}`, `{id: "later", label: "Non ora", description}`; the labels in the relay's language, the descriptions say when the screenshot does not go.
- **Second step, the send**: `hash` (the draft's) and `choice` (`github`, `anonymous` or `later`). Answers ok with `{choice, url, text}`. `github`: `url` is the link of the issue already written on frsorrentino/supervisor (title, body, labels `app` and the kind) for the app to open with the person's GitHub, where the person can attach the screenshot. `anonymous`: the draft goes to observe's anonymous service, never the screenshot (the service takes text only); `url` is the issue's. `later`: nothing goes, `url` null, the observation stays. A draft goes once and lasts a day: the same hash again, or an unknown one, is refused («write the feedback again»).
- **Where the issues go**: the public repo frsorrentino/supervisor, where the anonymous service already sends `supervisor-app`. From GitHub they carry the labels `app` and `bug` or `suggerimento` (applied by GitHub when the person can label the repo); the anonymous service puts its own labels (`from-observe`, `anonymous`) and the kind is in the title.

Fixture: `cmd-result-sample.json` (`feedback_app` …1210 the draft with a screenshot, …1211 anonymous, …1212 github, …1213 later, …1214 a screenshot without the yes, refused); every state fixture's `ops` has `feedback_app` after `feedback_text`.

Contract 1.120 (04/11/2026, the maintainer from the web app: «dobbiamo risolvere l'invio a vuoto di chiudi quando la sessione è senza finestra»; the master's proposal accepted at 13:23). Additive, `v` stays 1.

- **op `close` takes `when`**: `now` (or absent) as before; `after` = a session that is working, on a question or with a job in the background closes as soon as it stops; `force` = «Chiudi comunque», which the app confirms first: the relay sends Esc to the turn (or the question), stops the background shells, asks the session for a short handoff, and closes at the end of that turn or after 180 s anyway. An armed session answers ok with «<name> closes as soon as it finishes» or «<name>: stopped, writing the handoff; it closes at the end of the turn or within 180 s». An attached session or one that is not running is never armed: it stays with its reason. An unknown `when` is refused with the valid values. The text always says the outcome, one line per session.
- **`state.sessions[].closing`** `{mode: "after"|"force", at, deadline}` (deadline null for after) while a closing is armed; absent otherwise. It goes when the session closes (then it is in `closed`), stops running, or after a day.
- **`/exit` (op `slash`) for a session without a window** goes the way of `close` (`supervisor close`), with its checks, the save and the outcome; nothing is typed in the pane. With a window it is typed as before.

Fixture: `cmd-result-sample.json` (`close` …1120 with when after, …1121 with when force).

Contract 1.119 (04/11/2026, the maintainer from the web app: «after reloading the PWA the transcript messages are lost, only the user's remain»). No new key, `v` stays 1.

- **`transcript` never answers an empty page with ok for a missing file.** A live session whose transcript cannot be found (its row, then its id in every account) is refused with «no transcript yet for <name>»; until now it was `{"entries": [], "more": false}` with ok, which the app took for the whole conversation and put in place of the entries it had. A session that has just started is refused the same way until its first turn: the app keeps what it shows and asks again.
- The short name the app shows (`x` for the tmux `pix-x`) finds its session when the names map of the last push does not translate it; the exact name always wins, and a short name two sessions share finds none.
- The local copy of a transcript on another host is made by one reading at a time: two readings together appended the same bytes twice.
- The relay.log line of a `transcript` says the page asked for (`· 50`, `· 50:after=<id>`).
- **`commitments[].session` and the keys of `sessions[].tocca` are the name of the live session** (`sessions[].name`): a task registered for `x` while only `win:x` runs is on `win:x`, a task of `pix-x` on `x`, so its «Approva» is on that card (supervisor-approva-sessione-win-04-11). The exact live name always wins; a name two live sessions share, a closed session or one never seen keep the registry name.

Contract 1.118 (04/11/2026, from the master: five pre-releases out overnight and «Prova» empty, the test written only in the steps). No new key, `v` stays 1.

- **A release with no test registered is noticed.** Every 10 minutes the guard runs `task unproven`: an open task, not paused and not superseded, with no test in force, whose commit (its id in the message) came out for the first time in a pre-release of the last 24 hours (the first tag containing it; the newest is named) or, without a tag, in a web published today (a deploy the relay saw, the commit between the previous deploy and this one). The session of the task (the master when it has none) is told once per release, with the `task prova` command to register it; the item then reaches «Prova» as usual.

Contract 1.117 (04/11/2026, from the master: Claude Code 2.1.318 `claude auth status` reports `loginExpiresAt`). Additive, `v` stays 1.

- **A new item kind in op `decisioni`, `login`**: an account whose login expires within 7 days, has expired, or is logged out. `{kind: "login", task: null, session: <the master's name>, account, label, expires_at (epoch, null when logged out), reason, since (the start of the 7 days, or the expiry once past), where: "here", asks: "other", title: null, plan_start: null}`; its group's `refs` is `["login:<account>"]`. `reason` says the date and time and what to do: write to the master «rifai il login di <label>» and keep ready the 6-digit code that arrives by email. No command for the person: the master does the login. The item counts in `state.tocca.decidi` and in the master's `sessions[].tocca`.
- The guard reads `claude auth status` of each account once an hour (every 10 minutes while an item is open) into `<state_dir>/login-expiry.json`; after the new login the item goes at the next reading. An account that does not answer keeps the reading before. «Non ora» works as on any group; «Lascia perdere» only tells the master.

Fixture: `cmd-result-sample.json` (`decisioni` …0901 with a login group).

Contract 1.116 (04/11/2026, from the master: a dependency closed as superseded). No new key, `v` stays 1.

- **A dependency is met by `done` and by `superseded`**: the work came out in a pre-release tested and closed, so the plan engine starts what waits on it, and a plan whose nodes are all done or superseded ends ok. Report card and KPIs still count only `done`.

Contract 1.115 (04/11/2026, from the master: some forty tasks of supervisor-app left running with no pause and no test after rule 1 of the tidying). Additive, `v` stays 1.

- **A new task state, `superseded`.** A task whose test rule 1 closed (a newer pre-release of the same project contains its build) and that nothing took up again since (no open or new pause, no test to do, no step of its session) closes as `superseded` when the task that carries that pre-release is closed green; at once if it already is. If that task is itself superseded, the newer pre-release that closed it is followed. An owner still open, failed or cancelled closes nothing. `superseded` is never `done`: no confirmation, no row in the report card, not in the closed-task counts of the KPIs; it is not an open state.
- **op `done`** (`task closed`): a row of a superseded task has `outcome: "superseded"`, its `at` the time of the closing.
- **`state.tocca.today.items[].kind`** gains `task` (rule 1): the task closed as superseded, among today's closings by themselves.
- Board counts (`task board`) gain the key `superseded`.

Fixture: `cmd-result-sample.json` (`done` …0760 with a row `outcome: "superseded"`).

Contract 1.114 (04/11/2026, from the master: four tasks «stalled» behind supervisor-app at work). No new key, `v` stays 1.

- **`state.commitments[].stalled`** (and `task impegni`, the master's hook, the night report) is measured on the session, as `task stall-check` since 0.8.115: a task of a session that made a step or a result on any task, or a commit in the folders of its tasks, within `tasks.stall_hours` is not stalled; it waits in the queue behind a session at work. `idle_s` and `last_step` stay those of the task.

Contract 1.113 (03/11/2026, the maintainer: «le decisioni superate devono andare da sole: semplificare, non dare compiti in più»). Additive, `v` stays 1.

- **Tidying, every 10 minutes (`task tidy`).** What no longer asks anything leaves «Tocca a te» by itself: (1) a test whose build a newer tag of the same project contains (`git merge-base --is-ancestor`), both a pause with `prova` and a test of the bench; (2) a pause, a test or a needs-you request of a task already done, closed or cancelled; (3) two needs-you requests on the same task: the newest stays; (4) tests only: no step for 7 days and the task not running, into the archive. A wait for an action of the person without a test never expires by itself. The closing is «superata»: the pause closes or the test is retired, with a step that says why; never a confirmation of the person, never a closed task (since 1.115 a task closes, as `superseded`, when the pre-release of rule 1 is closed green).
- **`state.tocca`**: `{n, prove, decidi, today}`, one count for home, menu and registry, from the same items as `sessions[].tocca` (1.111) and the sessionless ones: `prove` the tests of op `prove` plus the items of op `decisioni` with `prova`; `decidi` the other items of op `decisioni`; `n` their sum. `today`: `{n, items[≤10]}`, the closings of the tidying since midnight, newest first, `{task, short, kind ∈ pause|prova|needs_you, rule ∈ 1-4, at}`. Absent with a relay that does not compute it.
- **op `prove`**: `archived`, at most 30, newest first: `{task, short, kind, rule (4), text, at}`.

Fixture: `cmd-result-sample.json` (`prove` with `archived`); `state-2-idle.json` (`tocca` with two closings of today, a pause of rule 1 and a test of rule 2, since 0.8.114; other texts of that fixture were shortened to stay under the 8 KB cap).

Contract 1.112 (03/11/2026, reported by the maintainer: «cos'è questo messaggio che appare spesso?»). Additive, `v` stays 1.

- **A read replaced by a newer one** (several reads of the same op and session waiting together: only the newest runs) gets at once a result with `ok: false`, `superseded: true` and the text `relay.cmd_superseded` in the relay's language. `superseded` is present only there: a client closes that request in silence instead of showing an error, without reading the text.

Fixture: `cmd-result-sample.json` (a superseded read).

Contract 1.111 (03/11/2026, asked by the app for the session's single menu). Additive, `v` stays 1.

- **`state.sessions[].tocca`**: `{prove, decidi, guarda, segui}`, integers, only when at least one is not 0; absent otherwise and with an older relay. Counted as the pages count them, by the session's name (as `sessions[].name`): `prove` the tests to do of op `prove` plus the items of op `decisioni` that carry `prova` (1.107); `decidi` the other items of op `decisioni`; `guarda` the works of that session not yet seen in the results index (all of them, not only the ten of `state.results`); `segui` the items of op `seguiti` left behind (`rimasto`, stale) with that session, as of the hourly round.

Fixture: `state-2-idle.json` (atlas-shop). To stay under the 8 KB cap the fixture's commitments carry shorter `next` texts.

Contract 1.110 (03/11/2026, asked by the app: the win tile only while win works, the Chromebook's disk otherwise). Additive, `v` stays 1.

- **op `kpi`, `kpi_health.local_disk`**: `{free, total}` in bytes, the disk of this computer (the one holding the sessions' root), or null when it cannot be read.
- **`kpi_health.hosts[]`**: `working` (true or false) and `working_why`, the reasons in this order: `job` (a heavy job of `supervisor offload`/`heavy` running there, not queued), `session` (a session of that host busy), `cpu` (its CPU at or above `system.host_busy_cpu`, 0.25 by default, the same fraction as `cpu`). A host that is down does not work.

Fixture: `cmd-result-sample.json` (`kpi`).

Contract 1.109 (03/11/2026, asked by the app for the KPI bar). Additive, `v` stays 1.

- **op `kpi`, `kpi_health.commands`**: `{p50_s, p95_s, n}` or null: the response time of today's commands (the local day), from the device's `issued` to the result written on the bus, for every command of the bus (reads too); not the local API. `p95_s` at the nearest rank. A sample a little under zero (the device's clock ahead, up to 5 s) counts as 0; over an hour, or further under zero, is left out. Null with no commands today.

Fixture: `cmd-result-sample.json` (`kpi`).

Contract 1.108 (03/11/2026, asked by the app for the KPI bar). Additive, `v` stays 1.

- **op `kpi`, `kpi_health.latency`**: `p95_s`, beside `p50_s` and `max_s`, over the same samples (the last hour, from the push to a device's read): the value at the nearest rank, never interpolated; null with no samples.

Fixture: `cmd-result-sample.json` (`kpi`).

Contract 1.107 (03/11/2026, asked by the app for «Da provare» merged into the newest pre-release; `v` stays 1). The tests a pause waits for become fields, and «Da decidere» can close them.

- **op `decisioni`, every `items[]` of `kind` `pause`**: `prova`, `{project, release, web}` or null. Not null when the pause waits for the person to try something: `project` as the item's (as `sessions[].project`), `release` the pre-release tag or null, `web` the web version or null. A session writes it with `task pause ID --reason R --waits person --prova [--release TAG] [--web VERSION]`. A pause written before carries it only if its words say «prova» and name a pre-release (`pre-release <tag>`) or a web version (`web <x.y.z>`, `PWA <x.y.z>`); otherwise null.
- **op `decisione`**, two more actions, only a touch, on the group's items of `kind` `pause` (oks and needs-you stay where they are; a group with none is `ok: false`):
  - `action: "provato"` («Tutto va»): the person's confirmation on each of those tasks, with origin relay like a touch on Approve (`task confirmed` reads it), the pause closed, the session told; then each task's check runs in a process of its own: green closes it, red leaves it open with the session, as for the test bench. The answer does not wait for the checks.
  - `action: "non_va"` («Non va»), `reason` required (up to 300 characters): no confirmation, the pause closed, the reason recorded and sent to each task's session; the task goes back to it.
- To merge several groups (the same project's tests) the app sends one `decisione` per group key.

Fixture: `cmd-result-sample.json` (a pause with `prova` and one with `prova` null in `decisioni`, a `provato` and a `non_va`).

Contract 1.106 (02/11/2026, asked by the app for its «Da guardare» section). Additive, `v` stays 1. A result is a file a session sent with SendUserFile.

- **The index.** `<relay.dir>/results.json`, persistent: results, seen and notes outlive the session and its transcript. A detached scan at low priority, at most every 30 s, reads only the new bytes of the live transcripts and of the closed ones touched since the last scan, and only the lines naming SendUserFile; a send whose tool_result is an error is not a result, nor is a subagent's. A new index looks back 48 hours; what came more than a day before its birth is born seen. Deliveries older than 90 days leave the index. Sessions on another host are read from their local transcript copy.
- **One delivery, one result.** `path`, `mime`, `size`: the main file (a video, else an image, else an html page, else the first); the other files of the same send are in `files` (op `results` only). `kind`: `video`, `mockup` (an html page, usually with its image), `image`, `document`, `other`. `duration_s`: a video's length (ffprobe), else null.
- **Same work, versions.** In the same project, a caption with «versione N» / «version N» joins the work with the same title (the words before it), and `version` is N; without it, a send of the file the work's latest version sent is the next version (previous + 1) and keeps the work's title. `versions`: the highest version of the work. `changed`: the rest of the caption after «versione N», only for N > 1, otherwise null. Sessions often rewrite the same file for each version: `replaced` (op `results`) says the file on disk is no longer the one delivered.
- **`state.results`**: `{n_unseen, items}`, absent while the index is empty. `items[]`, at most 10, newest first: the latest version of each work not yet seen, `{id, session, title, kind, mime, path, size, at, duration_s, version, versions, changed, seen_at}`. `n_unseen` counts every such work. fit_state never cuts it, like `feedback`.
- **op `results`** (a passive read): `{groups, n_unseen, more}`. `groups[]`, the work touched last first: `{title, project, session, kind, at, versions, unseen, items}`; `items[]` from the newest version: the keys of `state.results` then `caption`, `files[{path, mime, size}]`, `notes[{at, text, pos_s}]`, `replaced` (true, false, or null for a file on another host). Past the 60 KB page the oldest works leave first, `more` true.
- **op `result_seen`**: `arg` the id. Seen counts for that version and the ones before it; the latest seen takes the work out of `state.results`.
- **op `result_note`**: `arg` the id, `text` up to 2000 characters, `pos_s` optional (a number ≥ 0, at most the video's length + 1). It reaches the session like any prompt from the phone, through the relay's `talk` (origin `relay`, the device prefix): «Nota su «Film», versione 8 di 8 a 36,7 s (path): text». With the session closed it stays in its inbox and the answer says so. Saved in the result's `notes` only once `talk` took it.
- **op `file`** also serves a path that is a result's file in the index, with the session closed too: the same paths a transcript listed, never a new one.

Fixture: `state-3-stale.json` (one result), `cmd-result-sample.json`.

Contract 1.105 (02/11/2026, asked by the maintainer on 28/10: a section of the app for the notes of claude-observe). Additive, `v` stays 1. Nothing is ever sent without the consent or a touch: the three ops refuse a command without `device`.

- **`state.feedback`**: `{at, plugins, notice}`, absent when no installed plugin carries a copy of claude-observe. One row per plugin (by name; of two accounts the newer version), read with that plugin's own copy of `observe.py`, at most every 5 minutes or right after a `feedback_*` op. `plugins[]`: `{plugin, version, new, pending, security, anonymous, consent{on, since}, last_sent{at, url} | null, top[{call, error, count}]}`. `new`: records not yet sorted; `pending`: what a send would carry; `security`: counted apart, never sent from the phone (they have a private path). `anonymous`: the anonymous service is available for this plugin. `consent.since`: when the yes was given (ISO), null when off. `last_sent`: the last send from the app, otherwise the URL of the last reported record with `at` null. `top`: the three most frequent errors among `pending`, cleaned as observe sends them (call ≤ 60, error ≤ 100 characters). fit_state never cuts `feedback`: it is small, and a real state with its live sessions stays over the cap anyway. `notice`: the line the app shows above a written suggestion.
- **op `feedback_consent`**, only a touch: `arg` the plugin, `on` true or false. The touch is the user's yes to «Always anonymous»: observe saves it (`tools.<plugin>.always_anonymous`), and from then on new errors leave anonymously, never the notes. Turning it on sends nothing by itself. Refused when the anonymous service is not available.
- **op `feedback_send`**, only a touch: `arg` the plugin. Without `hash`: the preview `{plugin, n, title, body, hash}` (n 0 and the rest null when nothing waits), nothing sent. With the preview's `hash`: the anonymous send of exactly that text; a different hash is refused.
- **op `feedback_text`**, only a touch: `arg` the plugin, `text` up to 4000 characters. A written suggestion, cleaned like the notes, to the anonymous service: it becomes a public issue. The answer gives its URL.

Fixture: `state-3-stale.json` (one plugin), `cmd-result-sample.json`.

Contract 1.104 (02/11/2026, reported by the maintainer: a card saying only «aspetta l'ok» does not say what was proposed). Additive, `v` stays 1.

- **op `decisioni`, every `items[]`**: `title`, the task's whole title, `plan_start`, the start of its plan (at most 300 characters, cut at a word), both through the preview mask and null with no task; `asks`: `ok`, `choice`, `pc` (an action at the PC), `other`, so the app picks the right button. For a card it comes from the request, otherwise from the words of the pause.

Fixture: `cmd-result-sample.json`.

Contract 1.103 (02/11/2026, phase C of the approved program: the accounts). Additive, `v` stays 1; nothing new in `/state`.

- **op `conti`** (a passive read): `arg` the month `YYYY-MM` (none: this month), `project` optional, the client whose detail is wanted. `{at, mese, mesi, clienti, dettaglio, pagella}`. `clienti[]`: `{project, name, n, draft_at}`, one per folder with closed tasks in the month. `dettaglio`, null without `project`: `{project, name, mese, measured{tasks, commits, releases}, tasks[{task, title, at, outcome_kind}], commits[{hash, at, subject}], commits_more, releases[{tag, at}], git}`. No hours: only what is measured. `pagella`: `{since, min, rows[{model, kind, n, enough, defects, rate, efforts}], advice[{kind, better, worse, rate_better, rate_worse}], unknown_model}`. With `enough` false, `defects` and `rate` are null: no numbers under `min` (10) cases. `advice` only with a real difference on the same kind of work. It starts from the release; closures with no known model only count in `unknown_model`.
- **op `conti_azione`**, only a touch: `arg` the month, `project` the client. The master gets the request and writes the draft in its answer. No code sends it to anyone.

Fixture: `cmd-result-sample.json`.

Contract 1.102 (02/11/2026, phase B of the approved program: what stays outside). Additive, `v` stays 1.

- **`state.seguiti`**: `{rimasto, altrove, promemoria}`, three counts: items behind, positives with no task yet, reminders waiting. Absent when all zero. The count of items behind comes from the hourly `suggest fill`.
- **op `seguiti`** (a passive read): `{at, rimasto, altrove, promemoria, promemoria_scattati}`. `rimasto[]`: `{product, key, title, where, stale, why, session, aligned_version, aligned_at, task}`; `stale` true, false, or null when it cannot be known (`why`: `no_git`, `no_project`, `check_timeout`); `why` `paths:N` or `check`. `altrove[]`: `{id, text, complete, at, total, done, positive, errors, running, task}`: an error is never a negative, and a partial run does not replace a complete one (`running` says one is under way). `promemoria[]`: `{id, text, created_at, expires_at, at_time}`. `promemoria_scattati[]`: `{id, text, outcome, at}`, `outcome` `fired` or `expired`.
- **op `seguiti_azione`**, only a touch (`device`): `action` `allinea` with `product` and `key` (opens a task for the item's session, whose id is the result's text), `ok` with `product` and `key` («Va bene così»: aligned with this version), `remind_remove` with `key`.
- **Reminders.** A fired one is a `suggestions[]` entry of the new kind `reminder`, key `remind:<id>`, accepted as any other: its prompt reaches the master only on Accept. Event `reminder` once: `title` the text, `ref` the suggestion's id.
- The dictated reminder goes to the master, which runs `supervisor remind add`. A fix found by `altrove` goes through `altrove card`, one task awaiting the ok.

Fixture: `state-3-stale.json`, `cmd-result-sample.json`.

Contract 1.101 (02/11/2026, reported by the maintainer: a card saying only «Do you want to proceed?»). Additive, `v` stays 1.

- **`sessions[].question`, `kind: "permission"`**: `what`, one line (at most 200 characters) saying what is approved, the
  command's description first and then the command, for any tool, Agent and Workflow too; `body`, the dialog's text (at
  most 600 characters), the first thing `fit_state` drops. Both pass through the preview mask. Absent when nothing is known:
  the app keeps `text`.
- **Notification and `attention[]`**: the `question` event's `body` and the entry's `title` are `what` when present.

Fixture: `question-permission.json`, from the text of a real dialog (Claude Code 2.1.316).

Contract 1.100 (02/11/2026, the start at full speed, function 10 of the approved program). Additive, `v` stays 1.

- **`state.prima_raccolta`**: `{status, done, total, ask, first_round_at}`. `status`: `running`, `ask` (only the
  consent is missing), `interrupted`, `done`. Absent if it never ran, and once done with the first round made.
- **op `prima_raccolta`** (a passive read): `{at, status, done, total, consent, ask_text, cost, sections,
  first_round}`. `status` can also be `none`. `consent`: null, true, false; `ask_text` is the question while null.
  `cost` is 0: the collection calls no model. `sections[]`: `{key, dove, n, text, why, when}`, keys `machine`,
  `hosts`, `quota`, `plugins`, `projects`, `live`, `sessions`, `suggestions`, `night`, `recap`. With `n` 0, `text`
  is null and `why` and `when` say why it is empty and when it fills. `dove` is a screen with «Portami lì».
  `first_round`: `{at, cost}`, the sentence to show before the touch.
- **op `prima_raccolta_azione`**, only a touch (`device`, as `prova`): `action` `start`, `consent` with `value` `si`
  or `no`, `first_round` (once; a second touch answers ok and sends nothing).
- The collection starts by itself at the first pairing, if it never ran. An app that knows nothing of this still
  gets a fuller home.
- Follow-ups from past sessions are `suggestions[]` of kind `followup`, source `script`, keys `raccolta:<id>`;
  they expire in 72 hours.

Fixture: `state-3-stale.json`, `cmd-result-sample.json`. The relay sends `at` on every result (`run_cmd`): the six
samples of 1.99 lacked it, the fixture was wrong.

Contract 1.99 (01/11/2026, phase A of the program the maintainer approved at 20:35: decide less
and better). Additive, `v` stays 1; an older app sees two more `/state` keys, four more ops and
more fields on `approvals[]`, and ignores them.

**Touches.** `prova` and `decisione` count only as a touch: `device` is `phone`, `watch` or `web`
(any other value, or none, is `ok: false`), and `via: "live"` needs `confirmations` of at least
2, as `approve`. There is no CLI for the outcome of a test: a session writes a test and can never
confirm it. Rule 12 of the kernel is unchanged in what it allows.

**Test bench (`banco_prova`).**

- **`state.prove`**: `{n, builds, red, ko}`, the home's row; absent when all are zero. `n` is
  what is left to test.
- **op `prove`** (a passive read, never in `/state`): `{at, n, builds, groups, ko, red}`.
  `groups[]`, newest build first: `{build, n, at, tasks, prove}`; `prove[]`: `{id, task, short,
  title, session, project, build, dove, fai, atteso, at}`. `dove` is a free key the app maps to
  a screen. `ko[]`: `{id, task, short, build, dove, fai, atteso, text, at}`, what the person said
  does not work. `red[]`: `{task, short, reason, at}`, every test ok and the task still open:
  `reason` is the red check's last line, null while the check runs.
- **By version.** `build` is the tag the session wrote. The relay does not compare it with what
  is installed: the app shows the tests of its own build and says to install first for the
  others. A new build for a task retires that task's earlier tests, ok ones too.
- **op `prova`**: `arg` = one id, or ids separated by commas for «Tutte ok»: the ids the app
  showed, so a test written meanwhile is not confirmed unseen; an unknown one refuses the whole
  touch. `result: "ok"` or `"ko"`. With `ko`: one id and `text` (up to 2000 characters, the
  dictation) are required; the text reaches the master quoting task, build and screen, and the
  master opens a linked task. The screenshot goes with the existing op `report` to the master,
  right after. An `ok` on a test marked `ko` is accepted.
- **Closing.** When every test of a task is ok the person's confirmation is recorded with origin
  relay (`task confirmed` reads it), and the task's check runs in a process of its own: the
  result of `prova` does not wait for it. Green closes the task. Red leaves it open, spends no
  attempt, closes the pause for the person, tells session and master, and puts it in `red`.

**Five minutes (`cinque_minuti`).**

- **`state.decisions`**: `{n, here, tasks, snoozed}`, the home's row; absent when all are zero.
- **op `decisioni`** (a passive read): `{at, n, here, tasks, snoozed, groups}`. `groups[]`,
  doable from here first, then the heaviest, then the oldest: `{key, title, cause, items,
  unblocks, weight, where, since, note, refs}`. `weight` is how many tasks it unblocks, its own
  and their dependents. `where` is `here` or `pc`. `note` is the master's sentence or null.
- **`items[]`** by `kind`. `pause`: `{task, short, title, session, project, reason, since, where,
  undo, undo_how, needs_you}`. `ok`: the same without `reason`, with `what, where_to, options,
  untested, precedent`. `needs_you`: `{task, session, reason, since, where}`, `task` null for a
  request with no task. A task with a test to do stays in the test bench, not here.
- **op `decisione`**: `arg` = `key`, `action: "not_now"` (`hours` optional, 1-168, default 24;
  `reason` optional: the group comes back when the time is up or a new task joins it) or `"drop"`
  (`reason` required: the group's tasks are cancelled, the needs-you cleared, sessions and master
  told). Ok, choice and done stay `approve` and `attention_done`.

**Informed ok (`ok_informato`).**

- **On `approvals[]`, each only when declared**: `untested`; `undo`, one of `si`, `no`,
  `in-parte`; `undo_how`; `precedent`, `{task, short, relation}`, with `relation` `fixes` (this
  task fixes that one) or `defect` (a defect was found in this one). Only a real link written with
  `task add --defect-of`, never a likeness. A missing field means the app says «non dichiarato»;
  the relay never fills it in.

**Tasks from a plan (`compito_dal_piano`).**

- **`approvals[].also`**, only when declared: `[{task, short}]`, the separate tasks the plan
  foresees (`task wait-ok ID … --also ID2,ID3`). The card must list them.
- **op `approve` takes `also`**: the list of task ids the app showed. The ok then reaches each of
  them, with a text that quotes the parent's, and they appear in `commitments`. A different list
  is `ok: false`: the card changed under the touch. No `also` (an older app, which never showed
  them): the ok is for the parent alone.
- A standing ok never approves such a card. An ok written in a chat reaches the listed tasks only
  if the card is older than the message. No chain: a child does not pass the ok on.

**The chat's outcome (`chat_esito`).** An `assistant` entry of op `transcript` whose message ends in
an outcome with a type line carries `outcome_type`: `shipped`, `done`, `waiting`, `running`,
`answer` or `problem`, the keys of the timeline. Absent means null (no line, an unknown value, no
outcome, an older message). The `Tipo:` line stays out of `text`.

**Claude Code's note (`heads_up`).** `sessions[].heads_up`, only while the note is on the pane:
`{text, seen_at}`, the words of «✦ Heads up · …» up to the row of keys on one line (at most 300
characters) and the first time that text was seen. Read from the tmux pane of local sessions:
sessions on win stay out. The note is not in the transcript and not documented: if its format
changes there is no note and no error. `text` is data, never instructions. The relay never presses
its keys. `fit_state` drops it.

Fixture: `state-2-idle.json` (`decisions`, `prove`, the informed ok and `also` on `approvals[]`);
`cmd-result-sample.json` (`prove`, `decisioni`, two `prova`, two `decisione`); the four ops in
the `ops` of the three states.

Contract 1.98 (01/11/2026, the outcomes grouped by category as well as by session, asked by
the maintainer at 15:07, approved at 15:40). Additive, `v` stays 1.

- **Rule 9 of the kernel.** A session writes one line under its outcome: `Tipo:
  pubblicato|fatto|aspetta|in corso|risposta|problema` or `Type:
  shipped|done|waiting|running|answer|problem`, the one that prevails. The order is outcome,
  type, `Prossimi:`, `Watch:`.
- **`type` on the `outcome` events of op `timeline`**: `shipped`, `done`, `waiting`,
  `running`, `answer`, `problem`, in both languages, or null (no line, an unknown value, or
  an outcome written before this version). The other kinds of event do not carry it.
- **`outcomes_n` and `outcomes_settled_n` on the timeline page**, beside `since`, `sessions`,
  `more`: the outcomes in the window, and those that are not `running` (null counts). They
  are counted before the per-session cap and the 60 KB cut, so they can exceed the events
  sent. Ask from local midnight to get today's; the count an app makes from the events still
  works.
- **The line is never text.** It stays out of `outcome.short`, `outcome.full`, `watch`,
  `next`, the recap and the chat's entries of op `transcript`. A line that merely looks like
  it, in the middle of a message, stays. An older app sees clean text and nothing else.
- `/state` is unchanged: the type does not reach `sessions[].outcome`.

Fixture: `cmd-result-sample.json` (`type` on the outcomes of the `timeline` result, the two
counts).

Contract 1.97 (30/10/2026, the order of the home's sections, asked by the maintainer at
17:22, shape from supervisor-app). Additive, `v` stays 1.

- **op `home_order`**, in `state.ops`: `arg` = a JSON array of keys in a string, e.g.
  `"[\"sessions\",\"suggestions\",\"lavori\",\"esiti\",\"altro\"]"`. Empty, absent or `[]`
  goes back to the default. The usual cmd-result; not an array of strings, duplicates, an
  empty key, a key over 32 characters or over 16 keys = `ok: false`, the saved order stays.
- **`state.home_order`**: an array of strings; absent = the app's default. One order for
  all devices, it survives restarts (`<relay.dir>/home-order.json`). A change reaches
  `/state` with the push that follows the command.
- The relay does not check the list of keys: it keeps what it gets, so a new section needs
  no release. The app ignores unknown keys and appends the missing ones. Today's keys, in
  default order: sessions, suggestions, lavori, esiti, usage, system, plugins, closed (from
  31/10/2026 the rows of Usage, System, Plugins and Closed move one by one); the scoreboard
  and «Ti aspettano» have no key and stay on top. In an order already saved, the old key
  `altro` is expanded by the app in its place, in that order.

Fixture: `state-2-idle.json` (`home_order`); `home_order` in the `ops` of the three states.

Contract 1.96 (29/10/2026, the numbers of the KPI proposals of 25/10, approved by the
maintainer at 21:47 for after the quota reset of 29/10). Additive, `v` stays 1.

- **op `kpi`** (a passive read, never in `/state`: today's turns alone pass 8 KB):
  `{at, kpi_tasks, kpi_checks_week, kpi_turns_today, kpi_health, kpi_suggestions_7d,
  kpi_waits}`. A block whose source cannot be read is null.
- **`kpi_tasks`** (proposal 8): `{open, waiting_you, stalled, closed_today, tasks}`,
  each task `{task, short, state, age_s, attempts, attempts_max, stalled, who}` —
  `attempts` are the red checks already spent, at most 12 tasks.
- **`kpi_checks_week`** (9): seven `{date, green, red}`, the oldest first, every check
  rerun of a task.
- **`kpi_turns_today`** (10): per live session with turns today `{session, turns}`,
  each turn `{start, end, outcome, tokens, cache_read}`: `outcome` ok, fail (an API
  error in the turn), question (the last turn of a session waiting) or running (the
  last, session working); `tokens` = input + cache writes + output, once per message,
  `cache_read` apart. At most 30 turns per session.
- **`kpi_health`** (12): `{latency: {p50_s, p95_s, max_s, n}, commands, local_disk, sessions_here, sessions_max,
  hosts: [{name, up, cpu, ram_pct, disk_pct}]}` — latency from the push to a device's
  read over the last hour (a sample at each new `/seen`), the live sessions of this
  computer against `sessions.max_sessions`, the other hosts from `hosts_status`.
- **`kpi_suggestions_7d`** (13): `{accepted, discussed, later, never, expired}` over
  seven days; `accepted` counts the choices.
- **`kpi_waits`** (17): `{now: [{session, kind, since}], week: [{date, median_min,
  n}]}` — who waits for the person now (`kind` question, permission, needs_you) and
  the median minutes of the waits closed each day.
- **Waits in the timeline.** From this version the relay writes, at every push, the
  start and the end of each wait (question, permission, needs-you) per session in
  `waits.jsonl`; the timeline shows them as events of kind `wait`. Nothing before is
  rebuilt.
- **Proposal 3, the limit of one model, is not there:** Claude Code passes the status
  line only `five_hour` and `seven_day`.

Fixtures: `cmd-result-sample.json` (one `kpi` result), `kpi` last in every `ops`.

Contract 1.95 (29/10/2026, the maintainer's idea of 28/10: «a section of the app with
the installed plugins and skills, the current version and the latest, and Update and
Update all»). Additive, `v` stays 1.

- **`/state.plugins`**: `{checked_at, accounts: [{account, updates: [id]}]}` — when the
  marketplaces were last checked (epoch s, null if never) and, per account, the plugin
  ids with an update. Small on purpose: the whole list does not fit the 8 KB.
- **op `plugins`** (a passive read): `{checked_at, accounts: [{account, plugins,
  skills}]}`. Each plugin `{id, name, marketplace, scope, installed, latest, update}`:
  `latest` from the marketplace's manifest (its entry's version, or the plugin.json of
  a local source), null when the source does not say; `update` true when `latest` is
  newer (numbers) or different (a commit), null when `latest` is unknown. Each skill
  `{name, version, source}`: `version` from its SKILL.md (null if absent); `source`
  `account` (synced from the account), `<repo>@<commit>` (from its ORIGINE.txt),
  `link:<target>` or `local`. Skills are not updated from the app.
- **op `plugin_check`**: checks the marketplaces now (`claude plugin marketplace
  update` per account, low priority); the result says it started, the versions come
  with the next state. The relay does the same by itself at most every
  `plugins.check_hours` (6).
- **op `plugin_update {arg: "account:id"}`** and **`plugin_update_all {arg:
  account}`** (the user-scope plugins with an update): `claude plugin update ID --json
  -s SCOPE` for that account, never `-y`. When the marketplace asks to confirm a
  command, the result is not ok and says to confirm it at the PC. An update applies
  to new sessions. The text has one line per plugin.

Fixtures: `state-3-stale.json` (`plugins`, one update on `personal`), the four ops last
in every `ops`, `cmd-result-sample.json` (the list, a check, an update, one that needs
the PC, update all with nothing to do).

Contract 1.94 (28/10/2026, asked by the maintainer: «the feedback collection has to
work continuously»). Additive, `v` stays 1.

- **A new suggestion kind `observe`**, written by the hourly `suggest fill`, source
  `script`, rank 5: the local notes of claude-observe of one plugin need sorting —
  at least 10 new notes, or the oldest new one 3 days old. The text is a question
  with the plugin first («chrome-bridge: 12 new error notes since 10/09, the most
  frequent «No tab with id»: shall I hand them to its session for sorting?»); the
  key is `observe:<plugin>:<ISO week>`, so it stays the same within a week; Accept
  sends the master a prompt that hands the sorting to the plugin's session. It
  leaves the queue when the plugin has no new note left.

Fixture: `state-3-stale.json` (one `observe` item after the external wait).

Contract 1.93 (28/10/2026, from the local notes of claude-observe: «no entry … in
the transcript» 525 times, «X is not running» and «X: no transcript» on dozens of
sessions). Additive, `v` stays 1.

- **`transcript {arg: "n:after=<id>"}` with an id that is no longer in the
  transcript** (a restart, a `/clear`, a new conversation in the same session)
  answers ok with the last `n` entries and **`reset: true`**: the app replaces its
  list. Before, every poll of an open chat failed. `reset` is present only then;
  `before=<id>` with an unknown id still fails.
- **`transcript` by name of a session that is no longer running** reads its last
  conversation from the registry, read-only, as `sid` does since 1.82; a name the
  registry does not know still answers «… is not running».
- **`transcript` of a live session that has no transcript yet** (it starts with
  the first turn) answers ok with `{entries: [], more: false}` instead of «no
  transcript».

Contract 1.92 (28/10/2026, from supervisor-app's live view: red at 6.7-7.3 per core
with no advice, or only one about a chrome-bridge at 0% CPU, and nothing said what
was heavy). Additive, `v` stays 1; no new field.

- **A new advice kind `load`**: the load is yellow or red and no advice explains
  it (none above 25% of a core). The text names the three processes using the most
  CPU, or says the load comes from waits (disk, network, stuck processes) when they
  use little; level = `load_level`; no action.
- **`stuck` says when it is the cause**: when the raw load would be yellow or red
  and without the stuck processes it is not, the text says the load comes from them
  and not from work, with the raw figure, and the advice goes before the other
  yellows.
- **Order**: the reds first, then `stuck_loop`, then by real weight — the CPU of
  the process behind the advice — so a process at 0% is never presented first as
  the cause of the weight.

Contract 1.91 (28/10/2026, processes stuck on ChromeOS's graphic bridge, a defect
of 1.90 at its first live reading: red with a load of 49 on 8 cores and no advice).
Additive, `v` stays 1. The load was not real: 48 processes in state D (sommelier and
notificationd, waiting in `virtwl_ioctl_ptr` or `do_vfd_close`), left by three
`systemd --user` units in a restart loop since the morning. A process in D counts in
the load average but uses no CPU and does not die even with SIGKILL; only restarting
Linux frees it.

- **`/state.system.stuck`**: `{count, since, rss_mb, units}` for the processes in D
  on the graphic bridge (`system.stuck_wchan`), or null. `since` is the oldest one's
  start (epoch s); `units` the systemd units they belong to, sorted.
- **`load_per_core`** and `load_level` now leave those processes out:
  `(load1 - stuck.count) / cores`. `load1` stays the raw figure.
- **Two new advice kinds.** `stuck_loop` (red, first): the allowed units
  (`system.stuck_units`: `sommelier-x@*`, `cros-notificationd`, `sommelier@*`) that
  have stuck processes, at least `system.stuck_min_restarts` (5) restarts, are still
  running, and have no stop already queued; action `{op: "sys_unit_stop", arg:
  "unit1,unit2", label: «Stop the loop»}`. `stuck` (yellow): how many, since when,
  how many MB, and that only restarting Linux frees them, which closes every
  session; no action. Linux is never restarted by the relay.
- **op `sys_unit_stop {arg: "unit1,unit2"}`**: the relay reads the machine again and
  runs `systemctl --user stop --no-block` on each unit only if the same list is
  still advised; otherwise «… is no longer among the advice: nothing stopped». The
  text lists the units; written in the ledger as `sys-unit-stop`.

Fixtures: `state-3-stale.json` (48 stuck since 12:00, the loop of three units, the
stuck advice, load 65.4 raw and 2.17 per core without them; the `heavy_job` example
left out to stay at five), `sys_unit_stop` last in every `ops`,
`cmd-result-sample.json` (a `sys_unit_stop` that stops, the same one again, refused).

Contract 1.90 (28/10/2026, how heavy the machine is and what to do about it, asked
by the maintainer from the web app: «a system monitor with advice on the home page
about what makes the machine heavy»). Additive, `v` stays 1.

- **`/state.system`** (present with this relay, absent when the reading fails):
  `{at, cores, load1, load_per_core, mem_total_mb, mem_available_mb, level,
  load_level, mem_level, consumers, advice}`. `load_per_core` is the 1-minute
  load over the cores; `mem_available_mb` is what the Linux container can still
  give (`MemAvailable`). `level`, `load_level` and `mem_level` are `green`,
  `yellow` or `red`, `level` the worse of the two: load per core from 1.0 yellow
  and 2.0 red, memory available under 1536 MB yellow and 1024 MB red
  (`system.*` in the config).
- **`consumers`**: always four groups, `claude`, `chrome_bridge`, `relay`,
  `other`, each `{group, count, cpu, rss_mb}`, the most CPU first. `cpu` is in
  percent of one core, summed over the group's processes: the difference of their
  CPU time between two pushes (the first push after a start uses the average since
  each process was born).
- **`advice`**: at most 5, the reds first, each `{id, kind, level, text, action}`.
  `id` is stable while the advice lasts (`kind:key`). `kind` is `orphan` (a claude
  left without its tmux), `orphan_proc` (a process without a parent that matches
  one of the allowed patterns: a `chrome-bridge --launch` server, an MCP server
  older than 30 minutes above 10% CPU), `stale` (sessions idle for
  `sessions.idle_hours` with a context of 50% or more), `too_many`
  (`sessions.max_sessions` live sessions or more, the master excluded: the two
  stillest idle for an hour at least), `heavy_job` (a build, a render, a test run
  above 50% CPU) or `low_memory` (available under 1024 MB). `text` is ready to
  show. `action` is `{op, arg, label}` for a button, or null when the advice is
  only words (`heavy_job` and `low_memory`: «move it to win»). `stale` and
  `too_many` use the existing op `close` with the state's names in `arg`;
  `orphan` and `orphan_proc` use the new op `sys_kill`. Never the master, an
  attached session, the relay or a process outside the patterns.
- **op `sys_kill {arg: "pid:procStart"}`**: the relay reads the machine again and
  closes the process only if the same `pid:procStart` is still advised with this
  action (HUP, then TERM, then KILL); otherwise it refuses with «… is no longer
  among the advice: nothing closed». The text says the pid; written in the ledger
  as `sys-kill`.
- `fit_state` cuts the advice to the first two, then empties `consumers`, right
  after the oldest gone sessions.

Fixtures: `state-3-stale.json` (red: a chrome-bridge without a parent, an ffmpeg
at 180%, memory at 0.8 GB, a master without its tmux); `state-1-question.json`
and `state-2-idle.json` without `system` (an older relay); `sys_kill` in every
`ops`; `cmd-result-sample.json` (a `sys_kill` that closes, the same one again,
refused).

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
