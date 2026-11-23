---
status: accepted
date: 2026-10-25
---
# Scheduled production jobs run on a Railway job runner; GitHub keeps CI, the canary and attended tools; the repo is private under the company organisation

The repo had been kept public because public repos get free GitHub Actions and the scrapers burned about
112,000 billable minutes a month (about $650 a month if private). The operator wants the code private.
We decided that every recurring production job (portal walks, sweeps, location intake, upkeep, CLIP) has
one definition in code (`scraper/jobs.py`), one database row that says when it is due and who holds it
(`scheduled_jobs`), and one usage-billed host: a Railway service, the job runner, run as two identical
replicas that claim due rows by compare-and-set and run each job as a killable subprocess with a run
history (`job_runs`). GitHub keeps only work that needs a branch (CI), a human click (attended forms) or
independence from Railway (the hourly canary), under a `propscout-cz` organisation on Enterprise Cloud whose
50,000 included minutes cover it. Pure SQL runs on pg_cron; GPU work rents RunPod pods.

**Considered and rejected:** one Railway cron service per job (no kill of a hung run, which then blocks its
own schedule until a human redeploys; no Health reporting; one service and one image build per job, about
38 at thirty portals); self-hosted GitHub runners on Railway (GitHub's throttled cron stays the scheduler,
every workflow file stays, no Docker for the schema check, PR code beside production secrets, a postponed
per-minute fee); walks as lanes inside the realtime worker (a thread cannot be killed; the worker already
peaks at 94 % of its memory); established orchestrators (each adds an always-on service, a broker or a SaaS
that rule 7 does not justify).

**Consequences:** cadence and budgets change by reviewed PR, never by a settings knob; pausing a job is a row
with an expiry; a merge that redeploys the runner cuts running walks, so walks treat SIGTERM as their
deadline and resume as one cycle; all full walks leave from Railway's egress, watched per portal; the
realtime worker's serial probe/drain loops become the next scaling limit at about fifteen portals.
