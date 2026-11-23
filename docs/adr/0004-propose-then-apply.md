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

**Amended 2026-11-01 (#2072, as built).** A proposal may only write into what its own run creates, so no write entry takes the
id of a collection, tag or stage: a new collection holds its members in one proposal, and "add to a collection" is not an entry
of its own, leaving four entries for the five operations. The applied marker is not a column on the run: `agent_runs` stays
read-only to browser roles by grant, and the marker must commit with the write under the user's login, so it is a table of its
own written only by definer functions that refuse any login but the API's tenant pool (migration 712). A proposal applies once;
an undone one does not apply again. Undo removes only rows carrying the Apply's own stamp and refuses in the user's words where
removing them would take the user's work.
