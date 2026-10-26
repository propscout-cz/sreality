# The job runner: verbs, log lines, outcomes, drills

The Railway job runner (`scraper/job_runner.py`, `scraper/job_process.py`; SQL in `scraper/job_lease.py`) runs every
`scraper/jobs.py` entry whose host is `job-runner` (`jobs.runner_jobs()`), each as its own subprocess. Design and
rulings: `docs/design/actions-exit/PROGRAM.md` § 4.2. It runs nothing until a move PR (#2025, #2027–#2033) flips an
entry's host. The service itself is #2025's.

## Verbs (`python -m scraper.job_runner <verb>`)

A bare call prints help and exits 2: on a box with production credentials it never starts claiming.

| Verb | What it does |
|---|---|
| `run` | The service (Railway's start command, exec form). Registers the registry at boot, retires only rows it registered, claims due jobs, beats, prunes `job_runs` after 90 days. |
| `once <job>` | One foreground claim, run and release of a runner entry if it is due or requested. No upsert, no heartbeat. Exit 0 ok (75 included), 143 interrupted, 1 otherwise (a timeout too). GitHub entries need #2033. |
| `request <job> [--by WHO] [-- ARGS]` | Files a request row; the runner runs it within seconds, even while the schedule is paused. ARGS are appended to the registry argv (argparse keeps a repeated flag's last value). A second request is refused while one waits. |
| `pause <job> --until <ISO\|now> --note TEXT [--by WHO]` | Pauses the schedule (a request still runs) on any live row, a `held()` lane's included. `--until now` lifts it and records who did. A past time is refused. |
| `list` | Every `scheduled_jobs` row with its state and latest run. |

Recipes:
- Run a job now with a smaller batch: `python -m scraper.job_runner request clip --by cli:me -- --limit 500`.
- Hold a job for a maintenance window: `pause property_sweep --until 2026-11-01T06:00 --note "migration 690"`.
- **After a move PR merges**, `gh run list --workflow=<deleted file> --json status,databaseId`; if a run is queued or
  running, pause the new row past that run's timeout, since a queued run in a non-cancelling group can still start.

## Log lines (Railway logs)

| Line | Meaning |
|---|---|
| `BOOT pid1=… version=… holder=… deployment=… heartbeat=… pooler_addresses=… jobs=N` | Once per start. `pid1=False` on Railway means the start command is not exec form; `pooler_addresses` above 2 breaks the drain's connect budget. |
| `JOBS register entries=… inserted=[…] adopted=[…] unretired=[…] retired=[…] skipped=[…]` | The boot upsert landed (retried every tick until it does; nothing is claimed before). |
| `JOBS skip job=… why=…` (ERROR) | The database refused that entry (a CHECK, a bad value): never claimed until a deploy fixes it. |
| `RUN start job=… run=… pid=… requested_by=… args=…` | A claim spawned. The child's own output follows as `[<job>#<run>] <line>`. |
| `RUN term job=<job>#<run> pid=… why=timeout\|deploy` | The guard sent SIGTERM to the leader. |
| `RUN kill job=… pid=… why=timeout\|deploy\|fence (…)` | The guard SIGKILLed the group: the grace ran out, or the lease's fence passed / a renewal was refused. |
| `RUN end job=… run=… outcome=… due=… exit=… peak_mb=…` | Released. |
| `LEASE lost job=…: …` | A renewal or a release was refused, or the reaper closed a lapsed run as `lost`. |
| `LEDGER <call> failed (connection lost): …` | A database call failed; the tick asks nothing more after a lost connection. A finished run waits in memory and is released when the database answers. |
| `STOP seen: forwarding SIGTERM to N children, SIGKILL in 90 s` | A deploy's SIGTERM. Claims stop; renewals and releases go on until nothing runs. |
| `STOP drain spent …` (ERROR) | Railway's 180 s ran out first; those leases lapse and the reaper closes them `lost`. |
| `TICK failed` / `WATCHDOG no clean tick for N s: killed N children, exiting` | A tick raised; after 300 s with no clean tick the guard kills every child and exits, and Railway restarts it. |

## Outcomes and next due time

The runner's own stop decides before the exit code.

| Run | `outcome` | Next `due_at` |
|---|---|---|
| spawn failed (`exit_code` NULL) | `failed` | backoff |
| stopped at `timeout_s`, whatever it exited | `timeout` | retry: backoff floored at `timeout_s` |
| exit 0 | `ok` | next slot |
| exit 75 | `ok` | now + 60 s |
| deploy stop, fence, refused renewal | `interrupted` | now (DB-heavy too) |
| any other exit, an unsent SIGKILL (likely the OOM killer) | `failed` | backoff: 60 s doubling, capped at the cadence and the next slot |
| lease lapsed (runner crash) | `lost` | now, then backoff on a second loss in a row |

A DB-heavy job's backoff, retry and lost waits go to its own hour (the next day at most). A pure request run never
moves `due_at` (its exit 75 pulls it to 60 s at most); a request that also took a due slot advances it.

## Reading `job_runs`

`exit_code` is signed: −9 / −15 = killed by that signal; 143 = the child exited after its own SIGTERM handling.
`peak_rss_mb` is the larger of the leader's `VmHWM` and the group's summed `VmRSS` (pool workers included; shared
pages count twice). `last_error` on `scheduled_jobs` names the cause and never carries log text. **Never paste
`log_tail` anywhere public: it can hold a connection string.**

```sql
select job_name, outcome, exit_code, peak_rss_mb, started_at, ended_at - started_at as took
  from job_runs order by id desc limit 20;
```

## W2-gate drills (PROGRAM W2)

- **Request run:** `request stale_images --by cli:me -- --limit 10`; a `RUN start … requested_by=cli:me` within 10 s,
  then `RUN end`, and the row's `due_at` unchanged.
- **Pause with expiry:** `pause rent_map --until <now + 15 min> --note drill`; no claim while paused; a due slot
  inside the window is taken once the pause ends.
- **Self-fence:** make one replica's database unreachable (or stop its renewals) while a job runs; `RUN kill …
  why=fence` must land before the lease's expiry (≤ 150 s after the last renewal), and the other replica claims
  the job only after it.
