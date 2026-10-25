---
status: accepted
date: 2026-10-25
---
# In the MVP an agent writes nothing; it proposes, the user applies

Agents must be able to act on the app (collections, notes, pipeline cards, watchdogs, draft e-mails). The alternatives
were an approval state machine inside the run (park, bind the arguments, resume, consume once), autonomous reversible
writes with an Undo, or propose-then-apply. We chose propose-then-apply: a run ends with proposed actions, each a
registry write entry with validated arguments, rendered as a server-drawn diff; the user's Apply runs that entry under
their own tenant connection, once; each write declares an inverse for Undo. The executor therefore holds no path that
writes a user's rows, which removes the approval machinery from the MVP. Autonomous reversible writes are the next
ruling, not this one. Operator ruling Q4 of 2026-10-25.
