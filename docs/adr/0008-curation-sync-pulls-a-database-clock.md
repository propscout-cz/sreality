---
status: accepted
date: 2026-10-31
---
# Open windows follow curation changes by reading a clock the database advances, not by a push channel

A user's curation, and the shares that decide who sees it, change from other windows, the phone, the extension, a
delegate acting as the owner, and from merges and splits on the service role; no open window learned of any of it. We
chose one curation clock: a counter per (account, family) that only database triggers advance, at COMMIT, so every
writer is covered, including those that never pass through the API. Each signed-in window reads it every 5 s on the
desktop and every 15 s on the phone while visible (at once when shown), compares counters for inequality, and re-reads
only the reads it holds, through one table→read map and one read scheduler that its own writes use too. A re-read
stays owed until a read issued after the change has landed, and every read runs under the caller's row-level security,
so a revoked share stops at the next read. We rejected a Supabase Realtime push channel as the transport (at-most-once
delivery, a replication slot, a revocation window until the token refreshes), an event emitted by the API (blind to
merges, signup and cascades) and rendering from the signal itself. Because the clock adds the first code that runs at
COMMIT, the API first commits before it answers. Reopen the push channel only on a ruling that about 1 s is needed
(follow-up F7). Operator rulings Q1–Q5 of 2026-10-31; design `docs/design/curation-sync.md`, spec #2197.
