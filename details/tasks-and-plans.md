# Tasks and plans

Back to the [README](../README.md).

A plan is a JSON file of units of work. Each unit names its folder, a check
that can fail (a command that exits 0 when the work is right), the files it may
touch, and a lane: open (local commits, tests, docs), wide (shared code) or
closed (push, release, publication). Edges exist only where one unit's output
really enters the next, and each edge says what passes. `supervisor plan
approve` records the approval once; `supervisor plan run` does the rest:
ready units run in parallel, each in a session of its folder (opened and closed
for it), a script unit without a session; the session's own outcome is
recorded, but only the check rerun by the engine marks a unit done. A red unit
goes back alone, with the reason and the narrowed perimeter, three times at
most; then it is failed, the units after it are cancelled and the root session
is told. The registry (`supervisor task`, SQLite next to the configuration)
keeps every task, result and approval, and the constraints that failed tasks
leave behind are attached to the next tasks of the same project.

The closed-lane guard (`tasks.guard`, off by default) is a PreToolUse hook on
Bash: `git push`, `gh release`, `npm publish` and `release.sh` pass only inside
a running task of an approved plan, or after a recorded ok. `release.sh` counts
only as the command (`bash release.sh 1.0`, `./release.sh`), not as an argument
(`grep … release.sh`). The hooks that block a push when the guard fails wait 30
seconds, so a loaded machine does not block it by timeout.

An ok on someone's behalf comes from their device. From a Claude session,
`supervisor task approve ID --by NAME --inbox MSG [--text NOTE]` is the only
way: only the root session can run it, `MSG` must be a relay message (phone,
watch, web app) in its inbox from the last hour, and the ok quotes that message.
The relay's own oks («Approve» on the phone) are recorded as before. Once an
hour, `supervisor guard run` closes the tasks open for more than 6 hours whose
check is green (`supervisor task auto-close`); a red check there leaves the task
as it is and counts no attempt.

Commitments (22/10/2026, after a day lost on an APK: the task closed `done`
on an intermediate step, the ok got lost in a hand-written handoff, nobody
noticed an «Approve» for 46 minutes). A task born from an ok carries
`outcome` in its contract: `expected` in the words of the ok, measurable, and
a `kind` (`release`, `install`, `push`, `deploy`, `merge`, `custom`); the
check must touch it (for each kind the registry knows the markers `check.cmd`
must contain, e.g. `gh release`, `git tag`, `dumpsys`, `origin/`; `custom`
needs `proof`), and `task add`, `task done` and the hourly auto-close refuse a
check on an intermediate step. `supervisor task amend ID --check CMD |
--outcome TEXT --kind K` corrects an open task; `task confirm ID --by B
(--text T | --inbox MSG)` records the person's confirmation with the rules of
`approve`, and `task confirmed ID` (exit 0 when it exists) is a valid check
for an `install` outcome. `supervisor task impegni [--json] [--brief]
[--stalled]` lists every approved task not yet done (an ok recorded or an
approved plan) and the ones waiting for an ok, with age, last step, idle time,
next step (`task step ID "text" --next "…"`), whose turn it is, pause with a
reason (`task pause ID --reason R [--waits person] [--since T]`, `task resume
ID`: visible, never an alarm; `--waits person` puts it in the app's «Per te»
with `waits.on = person`, `--since` the real start)
and `stalled` after `tasks.stall_hours` (2) without a step. The same list goes
to the master at every prompt and start (the full block when it changes, one
line otherwise; `tasks.commitments_prompt`), into the morning report, into
`/state.commitments` for the app (contract 1.76) and into the handoff
(`handoff --written` appends the «Impegni dal registro» section from the
registry; `restart arm` in the master writes it before the restart). Every
guard round `task stall-check` finds the stalled ones not yet alerted for
their last step: one summary to the master (`talk`) and, outside
`tasks.quiet_hours` (23-07), to the person (event `task_stalled`, Telegram as
a fallback). «Approve» from the app wakes the task's session with a `talk`,
tells the master and clears the session's «waiting for you». `supervisor
task closed [YYYY-MM-DD] [--json]` lists the tasks closed that day (green,
cancelled, failed) with one proof each (release, commit, file or the check)
and the earlier days with a count: the app's «Fatto» block (contract 1.77).

fable-director is optional. When it is installed and enabled in the session's
account, the unit's brief asks it to open its budget with the unit's check as
`--verify` and its files as `--paths`; the task contract
(`schemas/task-contract.v1.json`) is the same file in both plugins. Without it,
nothing changes: the engine reruns the check itself.

`supervisor timeline` shows what the sessions did, in time order: the
prompts, the tests they ran with their summary line, the folder's commits, the
«Esito:» lines and the registry's outcomes, from code. The phone reads the same
timeline (contract 1.29, op `timeline`) for its summary; there each session's
`project` is relative to the projects' root, as in `state.sessions[].project`.
