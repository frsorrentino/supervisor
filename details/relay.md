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
