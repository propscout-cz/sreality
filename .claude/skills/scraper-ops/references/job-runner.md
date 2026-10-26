# The job runner: verbs, log lines, outcomes, drills

The Railway job runner (`scraper/job_runner.py`, `scraper/job_process.py`; SQL in `scraper/job_lease.py`) runs every
`scraper/jobs.py` entry whose host is `job-runner` (`jobs.runner_jobs()`), each as its own subprocess. Design and
rulings: `docs/design/actions-exit/PROGRAM.md` § 4.2. It runs nothing until a move PR (#2025, #2027–#2033) flips an
entry's host. On Railway it is the service `job-runner` in the realtime-worker project, two replicas of 4 vCPU and
8 GB (`.railway/railway.ts` `jobRunner`; settings, drain and image: `docs/architecture.md` § Railway service
configuration).

## Verbs (`python -m scraper.job_runner <verb>`)

A bare call prints help and exits 2: on a box with production credentials it never starts claiming.

| Verb | What it does |
|---|---|
| `run` | The service (Railway's start command, exec form). Registers the registry at boot, retires only rows it registered, claims due jobs, beats (only after a claim pass completes, so a replica that cannot register or list due jobs goes red), prunes `job_runs` after 90 days. |
| `once <job>` | One foreground claim, run and release of a runner entry if it is due or requested. No upsert, no heartbeat. Exit 0 ok (75 included) or not due, 143 interrupted (a closed terminal and `Ctrl+\` are the stop too: the child has its own session and would outlive the runner), 1 otherwise (a timeout, or a database that did not answer within 60 s: "the database did not answer"). GitHub entries need #2033. |
| `request <job> [--by WHO] [-- ARGS]` | Files a request row; the runner runs it within seconds, even while the schedule is paused. ARGS are appended to the registry argv (argparse keeps a repeated flag's last value). A second request is refused while one waits. |
| `pause <job> --until <ISO\|now> --note TEXT [--by WHO]` | Pauses the schedule (a request still runs) on any live row, a `held()` lane's included. `--until now` lifts it and records who did. A past time is refused. |
| `list` | Every `scheduled_jobs` row with its state and latest run. |

On Railway the operator runs `list` and `pause` with `scripts/railway_iac_wizard.sh runner-list` and `runner-pause
<job> <until> <note>` (over `railway ssh` to job-runner; `pause` echoes the command and asks first, `--by operator`). A
request is #2026's "Jobs: run now" form, never the wizard. If ssh cannot reach the service, the agent runs the one-row
SQL with the operator's OK.

Recipes:
- Run a job now with a smaller batch: `python -m scraper.job_runner request clip --by cli:me -- --limit 500`.
- Hold a job for a maintenance window: `pause property_sweep --until 2026-11-01T06:00 --note "migration 690"`.
- **After a move PR merges**, `gh run list --workflow=<deleted file> --json status,databaseId`; if a run is queued or
  running, pause the new row past that run's timeout (`runner-pause`), since a queued run in a non-cancelling group
  can still start.

## Log lines (Railway logs)

| Line | Meaning |
|---|---|
| `BOOT pid1=… version=… holder=… deployment=… heartbeat=… pooler_addresses=… jobs=N memory_limit_mb=… cpus=…` | Once per start. `pid1=False` on Railway means the start command is not exec form; `pooler_addresses` above 2 breaks the drain's connect budget. The limits are the replica's cgroup's (a Railway replica: ≈ 7629 MiB, 4); every beat's `worker_heartbeats.details` carries them too. |
| `JOBS register entries=… inserted=[…] adopted=[…] unretired=[…] retired=[…] skipped=[…]` | The boot upsert landed (retried every tick until it does; nothing is claimed before). |
| `JOBS skip job=… why=…` (ERROR) | The database refused that entry (a CHECK, a bad value): never claimed until a deploy fixes it. |
| `RUN start job=… run=… pid=… requested_by=… args=…` | A claim spawned. The child's own output follows as `[<job>#<run>] <line>`. |
| `RUN term job=<job>#<run> pid=… why=timeout\|deploy` | The guard sent SIGTERM to the leader. |
| `RUN kill job=… pid=… why=timeout\|deploy\|fence (…)` | The guard SIGKILLed the group: the grace ran out, or the lease's fence passed / a renewal was refused. |
| `RUN end job=… run=… outcome=… due=… exit=… peak_mb=…` | Released. |
| `LEASE lost job=…: …` | A renewal or a release was refused, or the reaper closed a lapsed run as `lost`. Each claim is its own holder (`<BOOT holder>/<n>`), so a late release never touches a later run of the job. |
| `LEDGER <call> failed (connection lost): …` | A database call failed; the tick asks nothing more after a lost connection. A finished run waits in memory and is released when the database answers. |
| `STOP seen: forwarding SIGTERM to N children, SIGKILL in 90 s` | A deploy's SIGTERM. Claims stop, mid-phase too (a lease claimed as it lands is released `interrupted`, "deploy stop; not started"); renewals and releases go on until nothing runs. |
| `STOP drain spent …` (ERROR) | The runner's own 180 s drain ran out (Railway's SIGKILL follows at 250 s); those leases lapse and the reaper closes them `lost`. |
| `TICK failed` / `WATCHDOG no clean tick for N s: killed N children, exiting` | A tick raised; after 300 s with no clean tick the guard kills every child and exits, and Railway restarts it. The guard only queues its lines (`STOP`, `RUN term`, `RUN kill`) for a forwarder thread, and the exit's backstop writes nowhere, so a stalled log reader holds no signal and no exit (≤ 10 s). |

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
pages count twice). `last_error` on `scheduled_jobs` names the cause in `settle`'s order (none for an `ok` run, a
deploy or fence it outlived included; `killed by signal N` for a signal with no name) and never carries log text. **Never paste
`log_tail` anywhere public: it can hold a connection string.**

```sql
select job_name, outcome, exit_code, peak_rss_mb, started_at, ended_at - started_at as took
  from job_runs order by id desc limit 20;
```

## Runner-only operations outside e9's windows (#2025-R2, ruled by e9 2026-10-26)

Once W-1 has shown `sreality`'s deployment id unchanged by the job-runner apply, three kinds may run outside e9's
windows: (a) an apply whose every change targets `service.job-runner`; (b) a Redeploy or Deploy Latest Commit of
job-runner; (c) the gate-week drills below. Merges still wait for e9's light. Four conditions:
1. The wizard's target check stays on. A change to shared or project-level variables, to the worker service or to the
   API service books a window.
2. Before drills C and D, e9 gets a one-line FYI with the time. No brake is needed.
3. A job that writes autodedup tables, takes `rt_lease` or runs anything in `autodedup/` needs e9's window, as merges
   do.
4. If a runner operation ever moves `sreality`'s deployment id or restarts the worker, runner-only work stops and e9
   is told.

## W2-gate drills (PROGRAM W2; the gate ticket after #2025's move PR has the exact lines)

A deploy cut is a new deployment only: job-runner → Deployments → the ACTIVE deployment → ⋯ → Redeploy. Never Restart
(no doc says it honours the drain), never an older deployment (its boot retires the newer registry's rows).
- **A. Request run** (#2026's "Jobs: run now" form): `map_overlays --dry-run` and `rent_map` (live) each end `ok`
  with `requested_by` set, `peak_rss_mb` under its declaration (1024, 512), and `due_at` unchanged.
- **B. Pause with expiry:** `runner-pause stale_images <a :20 slot + 10 min> drill`; no claim while paused; that slot
  is claimed once the pause lapses (`stale_images`, not `rent_map`, whose grid is 30 days).
- **C. Deploy cut of a request** (e9 gets a one-line FYI with the time first, #2025-R2): Redeploy during a
  `map_overlays --dry-run` request; the run closes `interrupted` (exit 143 or −15) within 250 s of the SIGTERM and is
  not filed again. It also measures click-to-SIGTERM.
- **D. Re-claim** (the same FYI to e9 first): a Redeploy timed so its SIGTERM lands inside a due-slot `stale_images`
  run; the next run's `started_at` is ≤ 60 s after the cut run's `ended_at`. Retry at a later slot if the cut misses.
- **E. Self-fence** (in W2's gate, ruled #2025-R1: it moves to W3's gate only if drill A's `map_overlays --dry-run`
  runs under 60 s, since a renewal must then fall inside the run): during a `map_overlays --dry-run` request longer
  than 60 s, with the operator's explicit OK at drill time for its two production UPDATEs, the agent sets that row's
  `lease_expires_at` to the past; expect `RUN kill … why=fence` at the holder's next refused renewal (≤ 60 s), the run
  closed `lost`, and no second holder while its child lived. Restore the row's `due_at`, then one clean `--dry-run`
  request.
