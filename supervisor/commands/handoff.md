---
description: Write a short handoff of this session to disk (goal, decisions, verified facts, open items, paths, what to invoke next) so that after /clear the session continues from it; --here writes it into the project's docs/
---

A long session pays its whole context again at every turn. A handoff on disk plus `/clear` keeps what matters for a fraction of that. Use it at a verified boundary (tests green, commit done), not in the middle of a change. If the fable-director plugin is active in this session, prefer `/fable-director:handoff`: it does the same with its budget checks. Do exactly this:

1. Run:

```
supervisor handoff --prepare ${ARGUMENTS}
```

2. Write the file at the `PATH:` it printed, with the seven `SECTIONS:` as H2 headings, in that order. Rules:
   - about 2k tokens at most; facts with numbers, commands and paths; no narrative, no restating what the code or the git log already shows;
   - «Decisions» only for choices someone would otherwise argue again; «Open items» lists the proposals the user did not take up and the open questions;
   - «Invoke next» names the skills, commands or files the next session should open first;
   - «Date and staleness» carries today's date and what would make this handoff stale (a release, a file, a decision).

3. Run the `THEN:` command it printed (`supervisor handoff --written PATH`): it records the file, appends the «Impegni dal registro» section from the task registry (never write that section yourself: the registry is the source) and prints its size.

4. Tell the user in two lines: the path, and that sending `/clear` is enough — at the next start supervisor points the session to this file once. You cannot run `/clear` yourself: the user sends it (terminal, phone or web app).
