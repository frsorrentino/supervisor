---
description: Forward a screenshot and a text to the session of a project (archives the image in the project's docs, launches the session if absent)
---

If the user attached an image from the phone, Claude Code saved it on disk: use that path. Then:

```
supervisor report <project> <image path or -> "<text>" ${ARGUMENTS}
```

Report where the image was archived and which session received it. Do not wait for that session's answer unless asked: `supervisor wait <name>` waits for it.
