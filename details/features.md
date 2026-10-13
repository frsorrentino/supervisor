# What you get, in full

Back to the [README](../README.md). The cards there are the short version of these sections.

## Sessions at a glance

`supervisor sessions` lists every session of every account: busy, idle, or
stopped on a question waiting for you, whether a tab is attached, which
channel reaches it. A detached session stuck on a question is work standing
still, not work in progress: it goes first. `supervisor next` picks the one
that needs you most. Three more notes on a row, read without asking anyone:
«at lower priority» when the session runs past its limit in `/low-priority`
mode (slow but alive; it shows only on the session's own screen, so the note
needs its tmux pane), «goal: …» when a native `/goal` is still open (from the
transcript), and «idle for N h» with the close command when a session has done
nothing for `sessions.idle_hours` hours. `quota` counts the lower-priority
sessions per account under its table; `wait` says when it returns on an open
goal (idle with a goal not met is «Goal paused», not finished work).

The `work-` in front of two names is the tmux prefix of the `work` account in the
demo set: every account has its own, set in `accounts.<name>.tmux_prefix`.

## Launch by a piece of a name

`supervisor launch atlas` resolves the folder under your workspace root,
deduces the account (personal or work, by a map you set once: a warning, never
a refusal), stops on the trust and bypass dialogs for you to accept them (or
answers them, if you list them in `session.dialog_patterns`), and opens the session as a
tab of the Terminal window you already have, with no start tab beside it. A
typo never becomes a folder: `--create` only when you ask, with the full path
shown first. Typing `claude` inside a project folder does the same. Past
`sessions.max_sessions` sessions at work (5 by default; busy or idle, the
master excluded) `launch` stops with the count and the free memory and asks;
without a terminal it exits 7 and says to close an idle session or repeat with
`--force`. It never closes anything on its own.

## Talk. Answer its question.

`supervisor talk ledger-api "deploy done?"` delivers a prompt to another
session, on either account, and reads the answer back from its transcript.
`supervisor answer ledger-api --show` reads the question a session is stuck on,
options numbered; `answer ledger-api 1` answers it: a message in the inbox never
unblocks a dialog, this does. The phone gets the same question with its
options as buttons the moment the session stops.

## Survive a reboot with everything

The first shell after a reboot offers to bring every session back, each with
its own conversation. Two lists feed it: the registry, reconciled every five
minutes, and the «last good set», which grows with the sessions and never
shrinks on its own — two windows closed by hand before the reboot cannot make
a session disappear from the proposal. `supervisor restore --dry-run` shows
what would come back, with the source and the date of each.

## Restart in place, keep talking

`supervisor restart arm` restarts the current session when its turn ends
and resumes where it was, because hooks, settings and plugins are read at
startup only. It keeps the model too: a new default model reaches a resumed
session only through `/model`, while `launch` starts on it. One flag per session, so eight sessions can arm in the same
minute; `restart list` shows them. `--clean` starts fresh with the memory
intact, `--switch-account` continues the same conversation on the other
account. A kill from inside a tool would land mid-turn: the hook is the only
safe instant.

When a long session gets expensive, a handoff plus `/clear` keeps what matters
for a fraction of the cost. `/supervisor:handoff --here` writes a short
distillate of the session (goal, decisions, verified facts, open items, paths,
what to invoke next) into the project's `docs/`. After `/clear`, the next start
points the session to that file once. The app's «Handoff» button sends the
command each session has. The optional companion plugin
[fable-director](https://github.com/frsorrentino/fable-director) has its own
`/fable-director:handoff`, which also checks that no budget is open and that
the task is at a verified boundary. Sessions where it is enabled use that one.

## Your screen, arranged

On ChromeOS, `supervisor tile` gives every session an app window of its
own and arranges them: equal columns, a grid when they get narrow, and with an
odd count that includes the master, the master big on the left and the others
stacked on the right. `merge` brings them back as tabs of one window, `move
destra` sends them to another monitor, `layout save mattina` remembers a
layout. The Terminal's start tab is evicted, never left beside a session.

## From your wrist

The native Wear OS app is out in **beta**:
[github.com/frsorrentino/supervisor-app](https://github.com/frsorrentino/supervisor-app).
Sign up as a tester ([Try it](../README.md#try-it)), or build it yourself from the app's
README. The relay runs on your PC, with your own Firebase. The list shows every
session with its state and icon; a tap opens its card — state, outcome, next
step; a question comes with its options, and one tap answers it. A list that is
no longer fresh says so and never looks live, the watch is paired once with a
6-digit code from your PC, and it buzzes for questions, outcomes and sessions
that end.

The plugin works without the app: everything above happens in the terminal
either way. watchOS is not supported. Telegram only sends notices (below).

## Telegram, one-way

Telegram sends; nobody answers it. The interactive bot — bare-word commands,
inline keyboards, session cards, «follow», the long-polling daemon and the 08:00
digest — was retired on 16/09/2026: the watch does all of it in real time, and
Telegram had become a second copy of every notice. What goes out now is what a
watch cannot carry or will not receive:

- the day's diary at 20:00 and the night shift's report, which are long texts;
- the quota warnings, when no watch is receiving them;
- a question a session is stuck on, when the watch is not paired, or the relay
  or the network is down — the phone is then the only channel left.

`supervisor bot status` shows the token, the allowed chats and the log; there
is no daemon to keep alive any more. The morning «what waits for you» lives on
the watch, which shows without pause who is waiting on a question and who is
idle.

## Guard, diary, night

`supervisor guard` warns once per window when an account passes 95 % of
its quota and, at the reset, sends «resume where you were» to the sessions
that hit the wall. At 20:00 the recap: one sentence per project on what was
done and the next step, the ones waiting on a question first, the same line
appended to the project's `docs/recap.md`. At 02:00 the night queue runs one
job at a time, only while free memory and quota allow, leaving a report in the
project. What waits for you in the morning is on the watch, which never stops
showing it.

## Doctor, for both accounts

`supervisor doctor` checks both accounts: the plugin in the cache at the
right version, the hooks wired in, the relay's daemon alive, the cron lines
present; each line says what to do. Run it after every update: hooks run
from the cache, and a live session keeps its old version until it restarts.

**Why not plain tmux?** tmux keeps sessions alive; it does not know which
Claude account a folder belongs to, which session is waiting on a question,
how to ask another session something and read the answer, or how to bring
eight conversations back after a reboot. That is the part this plugin adds,
on top of tmux, not instead of it.
