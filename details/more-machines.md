# More machines

Back to the [README](../README.md).

One control machine, other computers doing the heavy work. You declare a host
by name and ssh alias; supervisor measures it and proposes what it can do:

```bash
supervisor host add laptop --ssh laptop     # pins the host key, asks before installing a 3-file helper
supervisor host doctor laptop               # OS, cores, RAM, GPU, WebGL, tools, a 20 s benchmark, bandwidth
supervisor host confirm laptop              # roles, limits and trust, as proposed or edited
supervisor offload ~/film --recipe render   # the exact commit, the declared assets, the result back
```

- **Measured, not guessed.** `doctor` runs one remote command that returns
  JSON: system, GPU (`nvidia-smi`, `lspci`, `Win32_VideoController`), WebGL
  from a headless Chrome (hardware or software renderer), tool versions, which
  Claude accounts are logged in, foreign credentials (named, never read), and a
  micro-benchmark relative to the control machine. It proposes roles
  (`compute`, `render`, `sessions`), per-host limits from a written formula,
  and trust `work` without secrets and without client folders. Until you
  confirm, the host gets nothing.
- **Jobs declare needs, not hosts.** A recipe in `<project>/.cm-offload.json`
  says role, threads, RAM, GPU, tools, operating systems and a command per host
  kind (`{threads}` takes the host's value). The scheduler filters hosts by
  trust and requirements, drops those without a free slot or above the load
  gate, and chooses: a light job stays here while this machine has room; a
  heavy one goes out when the measured time saved beats the copy cost (bytes to
  send ÷ measured bandwidth, plus setup). `--explain` prints the whole table;
  `--host` forces the choice but never trust or hard requirements.
- **What travels.** `git archive` of the exact commit (a dirty tree is refused,
  or sent as `git stash create` with `--dirty`); `.env*`, keys and credentials
  never leave, and a scan for private keys and known tokens blocks the send
  naming the file. Ignored assets travel once: the host keeps a content-addressed
  cache. Dependencies install once per lockfile hash. Results come back with
  their sha256 checked, into an ignored output folder or the state directory,
  never over a tracked file.
- **Surviving the link.** Jobs are detached so a dropped ssh does not kill
  them: `systemd-run --user` or `setsid` on Linux, a one-shot scheduled task on
  Windows. `offload wait <id>` blocks until the end, from a background shell.
  Since Claude Code 2.1.288 a background command's time limit (its `timeout`,
  two hours at most) applies only to unattended sessions (`-p`, Agent SDK, CI,
  cloud); for those, `wait` gives up by itself after 110 minutes (`--max-min`)
  with exit 4 and says the job is still running: run it again until it exits 0
  or 1. A terminal session has no limit.
- **Seeing it.** `hosts poll` (cron, every minute; every 30 s while something
  runs) writes one snapshot per host. `sessions` shows the other hosts'
  sessions as `HOST:name` and one line per host with load, RAM, disk and the
  heavy job's progress; `quota` merges the readings of every host and says
  where the freshest came from. An unreachable host is a line with the age of
  its last good read, never a hang.
- **A whole session elsewhere.** `launch <dir> --host win` starts a Claude
  session on a `windows-native` host with the `sessions` role, with Remote
  Control on, so it shows in `sessions` and in the app as `win:<name>`
  (read by the hosts poller in the crontab, which `launch --host` installs
  when it is missing and `doctor` checks). A rule in the config
  decides which folders may go, and every refusal names the condition it
  failed: the folder's account is in the host's `trust.accounts`; the folder is
  not in the client perimeter and is under the workspace root; it matches
  `hosts.H.sessions.allow` and no `deny` entry; no secrets (not in a recipe,
  not in the file names, not in the content). What goes there is a snapshot of
  HEAD (`git archive`, no secret files), never the history; the host has no git
  credentials. `host fetch win <name>` brings the commits made there back as
  patches on a local branch `win/<name>`, applied in a temporary worktree so the
  shared checkout is untouched; merge and push happen here. When this machine is
  loaded (1-minute load above its cores, or less than 2 GB free) `launch`
  suggests the host in one line; `--host auto` picks it.
  `talk win:<name> "…"`, `wait win:<name>` and `close win:<name>` reach it
  through its native inbox (the named pipe, the text in base64) and read the
  reply from its transcript there; `close` refuses a session mid-turn without
  `--force` and leaves the folder for `host fetch`.

Host kinds: `linux-tmux` (this machine or over ssh), `windows-native` (OpenSSH,
PowerShell 5.1+), `macos-tmux` and `wsl` over ssh. ssh options always go on the
command line with a dedicated `known_hosts`; `~/.ssh/config` is never touched.
Remote sessions start, talk and come back as above.
