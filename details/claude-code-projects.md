# supervisor and Claude Code Projects

Back to the [README](../README.md).

[Claude Code Projects](https://code.claude.com/docs/en/claude-projects) (public
beta on Pro and Max, rolling out gradually; not yet on Team or Enterprise) lets
one conversation coordinate a stream of tasks as parallel threads. Threads are
usually cloud sessions working on github.com repositories or on files you
upload, followed from claude.ai/code, the desktop app or the Claude mobile app,
with an Overview of what is waiting on you and what is ready for review. A
thread runs on your computer only through Remote Control, and a session you
started locally cannot be moved into a project.

supervisor starts from the other end: the sessions you open yourself, in a
terminal, on your machines.

- **Projects** fits work that lives on GitHub or in files you can upload, can
  run in the cloud, and should go on with your laptop closed.
- **supervisor** fits work that needs your computer: folders that are not on
  GitHub, tools, databases or devices on your machine, a second PC, more than
  one account, sessions you started in the terminal.
- They do not exclude each other: a session that supervisor launches has Remote
  Control on by default, so it shows in claude.ai/code and in the Claude app as
  well.
