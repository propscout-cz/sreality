# The job runner: verbs, log lines, outcomes, drills

The Railway job runner (`scraper/job_runner.py`, `scraper/job_process.py`; SQL in `scraper/job_lease.py`) runs every
`scraper/jobs.py` entry whose host is `job-runner` (`jobs.runner_jobs()`), each as its own subprocess. Design and
rulings: `docs/design/actions-exit/PROGRAM.md` § 4.2. It runs nothing until a move PR (#2025, #2027–#2033) flips an
entry's host. The service itself is #2025's. Its Health cells and the "Jobs: run now" form are #2026's (below).

## Verbs (`python -m scraper.job_runner <verb>`)

A bare call prints help and exits 2: on a box with production credentials it never starts claiming.

| Verb | What it does |
|---|---|
| `run` | The service (Railway's start command, exec form). Registers the registry at boot (`registry_at` is when a row joined: new, adopted or un-retired; a later boot keeps it), retires only rows it registered, claims due jobs, beats (only after a claim pass completes, so a replica that cannot register or list due jobs goes red), prunes `job_runs` after 90 days. |
| `once <job>` | One foreground claim, run and release of a runner entry if it is due or requested. No upsert, no heartbeat. Exit 0 ok (75 included) or not due, 143 interrupted (a closed terminal and `Ctrl+\` are the stop too: the child has its own session and would outlive the runner), 1 otherwise (a timeout, or a database that did not answer within 60 s: "the database did not answer"). GitHub entries need #2033. |
| `request <job> [--by WHO] [-- ARGS]` | Files a request row; the runner runs it within seconds, even while the schedule is paused. ARGS are appended to the registry argv (argparse keeps a repeated flag's last value), so they override the job's own flags unchecked: `clip --workers 64` or `stale_images --limit 10000000` bypass the co-tenancy census, which counts declared memory only. A second request is refused while one waits. The operator's form is "Jobs: run now" (below). |
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
| `LEASE lost job=…: …` | A renewal or a release was refused, or the reaper closed a lapsed run as `lost`. Each claim is its own holder (`<BOOT holder>/<n>`), so a late release never touches a later run of the job. |
| `LEDGER <call> failed (connection lost): …` | A database call failed; the tick asks nothing more after a lost connection. A finished run waits in memory and is released when the database answers. |
| `STOP seen: forwarding SIGTERM to N children, SIGKILL in 90 s` | A deploy's SIGTERM. Claims stop, mid-phase too (a lease claimed as it lands is released `interrupted`, "deploy stop; not started"); renewals and releases go on until nothing runs. |
| `STOP drain spent …` (ERROR) | Railway's 180 s ran out first; those leases lapse and the reaper closes them `lost`. |
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

## Health: the `job/<name>` cell (migration 682, #2026)

The pg_cron watcher writes one `job/<name>` cell per live registered job-runner row (`runner = 'job-runner'`,
`registry_at` set, not retired: the due list's predicate), from `public.health_job_cells()`, every 5 min, so it
survives a Railway outage. A job cell is never `unknown`. C is the row's cadence, T its `timeout_s`, W the longest run
of a sibling on its resource seen since its clock started. The worst condition wins; every condition is in
`facts.conditions`.

| State | Status | Message (after `<job>: `) |
|---|---|---|
| Running, under ⅔·T | ok | `running for N of its T budget on job-runner/ab12cd34` (+ `; a request by X waits for it`) |
| Running past ⅔·T / past T / past T + 10 min | Watch / Watch / **Problem** | `running for …`; past T `; past it: the runner is stopping it`; past T + `STUCK_RUN_GRACE_S` `; past the runner's 10 min stopping grace: nothing is stopping it` |
| The latest closed run is `timeout` (ruling R-3) | **Problem** until a later run closes | `its last run hit its T budget and was stopped (timeout)`; a request's adds ` (a request by X): a request is never retried; a new request or its next slot (in N) clears it` |
| Failures in a row (`consecutive_failures`; a row that joined after a gap: its failed runs since) | Watch at 1; **Problem** at 3 once the streak's first failure is min(C, 1 h) old | `its last run failed: <cause>` / `3 failed runs in a row, the first N ago; the last: <cause>` |
| Late since its clock | Watch past C + T + W; **Problem** past that + min(C, 1 d) | `late: no ok run for N (Watch past …, Problem past …)` or `never ran ok` |
| Due or requested, not started for 10 min | Watch while a replica beats; **Problem** when none has for 10 min | `due N ago and not started` / `requested by X N ago and not started` (+ `; no job-runner replica has beaten for 10 min`) |
| Refused at boot (a replica's `JOBS skip`) | **Problem** | `refused at boot by job-runner/ab12cd34 (JOBS skip in its log): no replica claims it`; with no row: `… it has no row and runs nowhere` |
| Paused | ok (late, failing and timeout suppressed) | `paused for N more by X: note` (+ `; its last run failed: <cause>`); paused longer than greater(C, 7 d) is Watch, `…longer than a cycle: its slots are skipped until then` |
| Waiting for a sibling on its resource | ok | `waiting for B, which holds location-heavy (running N of its M budget)` (only while it waits) |
| Requested, under 10 min | ok | `requested by X N ago; starting` |
| New, adopted or un-retired, before its first slot | ok (history not judged) | `not run yet; first slot in N (… UTC)` / `registered again; not run here since; first slot in N (…); last ok M ago` |
| Otherwise | ok | `last ok N ago; next slot in M` or `; due now`; a deploy-cut run adds `; its last run was cut by a deploy and is due again at once` (a cut request: `; a request by X was cut by a deploy; it is not re-filed`) |

- **The clock** is the last ok run, else the first *scheduled* run (`requested_by IS NULL`) minus C, else the first
  slot minus C, and a pause that lapsed restarts it. Never a request run. A row that **joined after a gap**
  (`registry_at`, stamped when a row is new, adopted or un-retired and kept by later boots; no ok run since and no run
  of it in the C + T before) is judged on its runs since the join: its clock is its first scheduled run since minus C,
  else its first slot minus C, and its streak counts its failed runs since, so the success and streak un-retiring
  carries from before a revert never read Problem (a deploy-cut first run reads ok, one failure Watch). A row whose
  runs go on through `registry_at` is judged whole. Known edge: a run that served a request and took its due slot
  keeps `requested_by` (`job_runs` has no due slot), so a never-succeeded job whose first run was one reads late up
  to one cycle later, never earlier.
- **The cause** is the latest `failed`/`timeout`/`lost` run's: `lost: the replica holding it stopped renewing its
  lease`; `scheduled_jobs.last_error` when that failure is the latest closed run; else `<outcome>, exit <code>`; a
  request's cause adds `(a request by github:x, args …)`. `log_tail` is never read.
- **Requests count as the job's runs** (#2024's release): a `--dry-run` request makes the job look freshly done and
  resets the streak; a mistyped argument is a failed run (Watch until the next ok one; re-request it corrected). One
  that makes the run outrun `timeout_s` is a recorded timeout, a **Problem** until a later run closes, and a request is
  never retried: on rent_map or map_overlays that is the next slot, up to 30 or 91 d away, unless you re-request it
  with arguments that fit the budget.
- **`job-runner`** (the fleet, present while any live row exists): ok `N replicas beating; M jobs registered`, or Watch
  when no replica has beaten for 10 min; never a Problem (a cleanly stopped or deleted runner deletes its beats; it
  stays Watch until a job comes due + 10 min). Each replica keeps its own `worker/job-runner/<id>` cell (migration 675).
- **`job-cells`**: a Problem if the helper raised (`the job cells could not be computed: …`), with an `unknown`
  `<subject>: not judged` cell per open `job/*` incident, so the teller neither recovers nor retires them.
- **`job-runner/registry`** (the worker's `jobs` check, `scripts/checks/platform.py`): every name in
  `jobs.runner_jobs()` (the code the worker runs: main's) needs a live registered row. Missing past
  `jobs_runner_register_grace_s` (3600 s) from the worker's boot, read missing at the previous measurement too: a
  **Problem**, "the runner is not running this registry" (its build failed or was skipped, it refused the entry, or an
  older image booted and retired it): read Railway's job-runner deployments and the `BOOT … jobs=N` line. Missing at
  one read only: Watch (in a deploy overlap the one worker heartbeat row can be the predecessor's). A live row the code
  does not declare: Watch. Inside the grace: ok `waiting for the runner's deploy`.
- **For the move PRs (#2027, #2028, #2029): the ⅔ line.** A job whose own budget sits above ⅔ of its `timeout_s`
  reads Watch on every healthy run that uses it: location_intake runs `--max-seconds 2400` against T 3300 (⅔ = 2200)
  and property_sweep `--max-seconds 6000` against 7800 (⅔ = 5200). Each move PR sets `timeout_s` ≥ 1.5× the job's own
  budget, lowers the budget, or asks the operator. A walk stopped at its own timeout records `timeout`, a Problem: W4
  keeps `walk_timeout_s` above each walk's p95. W2's three jobs are clear (stale_images runs 21-30 s of 1200;
  rent_map and map_overlays carry no self-budget).
- One job's cell off: `pause <job> --until <t> --note <why>`. The whole arm, forward only: a migration replacing
  `health_job_cells(interval)` with `select '[]'::jsonb`.

## Jobs: run now, click by click

The GitHub form `.github/workflows/run_job.yml` files a request through the `request` verb (`--by github:<you>`) and
runs nothing on GitHub; the runner starts the job within seconds once it and its resource are free.

1. github.com → the repo → **Actions** → **Jobs: run now** (left list) → **Run workflow** (right).
2. Leave "Use workflow from" on `main`.
3. Type the job's name as Health shows it after `job/` (e.g. `map_overlays`); anything but `a-z`, `0-9` and `_` is
   refused before Python runs. Optional args, e.g. `--dry-run` (quote a value with spaces; an unmatched quote is
   refused before Python runs, nothing filed); they override the job's own flags unchecked.
4. **Run workflow**.
5. Open the run and read its one summary line: `request filed for <job>`, or why not (`<job> runs on
   github-actions-until-moved` until its move PR lands; `a request by … has waited since …`; `not a job name`; `args: an unmatched quote, nothing filed`). Then
   watch `job/<name>` on Health. Green means asked, not done.

## W2-gate drills (PROGRAM W2)

- **Request run:** through the form (above) with `job=stale_images`, `args=--limit 10`, or
  `request stale_images --by cli:me -- --limit 10`; a `RUN start … requested_by=github:<you>` within 10 s, then
  `RUN end`, and the row's `due_at` unchanged.
- **Pause with expiry:** `pause rent_map --until <now + 15 min> --note drill`; no claim while paused; a due slot
  inside the window is taken once the pause ends.
- **Self-fence:** make one replica's database unreachable (or stop its renewals) while a job runs; `RUN kill …
  why=fence` must land before the lease's expiry (≤ 150 s after the last renewal), and the other replica claims
  the job only after it.
