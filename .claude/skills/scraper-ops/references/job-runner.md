# The job runner: verbs, log lines, outcomes, drills

The Railway job runner (`scraper/job_runner.py`, `scraper/job_process.py`; SQL in `scraper/job_lease.py`) runs every
`scraper/jobs.py` entry whose host is `job-runner` (`jobs.runner_jobs()`), each as its own subprocess. Design and
rulings: `docs/design/actions-exit/PROGRAM.md` § 4.2. Since #2025's move PR it runs `stale_images`, `rent_map` and
`map_overlays`, and since #2032 `clip`, whose workflows those PRs deleted; each later move PR (#2027–#2031,
#2033) flips more entries' host. On
Railway it is the service `job-runner` in the realtime-worker project, two replicas of 4 vCPU and 8 GB
(`.railway/railway.ts` `jobRunner`; settings, drain and image: `docs/architecture.md` § Railway service
configuration). Its Health cells and the "Jobs: run now" form are #2026's (below).

## Verbs (`python -m scraper.job_runner <verb>`)

A bare call prints help and exits 2: on a box with production credentials it never starts claiming.

| Verb | What it does |
|---|---|
| `run` | The service (Railway's start command, exec form). Registers the registry at boot (`registry_at` is when a row joined: new, adopted or un-retired an hour or more after its retirement; a later boot keeps it, and so does migration 682's trigger whatever image boots; an un-retire under the hour, an older image's blink, keeps `due_at` too, so a cut run or a missed slot stays due), retires only rows it registered, claims due jobs, beats (only after a claim pass completes, so a replica that cannot register or list due jobs goes red), prunes `job_runs` after 90 days. |
| `once <job>` | One foreground claim, run and release of a runner entry if it is due or requested. No upsert, no heartbeat. Exit 0 ok (75 included) or not due, 143 interrupted (a closed terminal and `Ctrl+\` are the stop too: the child has its own session and would outlive the runner), 1 otherwise (a timeout, or a database that did not answer within 60 s: "the database did not answer"). GitHub entries need #2033. |
| `request <job> [--by WHO] [-- ARGS]` | Files a request row; the runner runs it within seconds, even while the schedule is paused. ARGS are appended to the registry argv (argparse keeps a repeated flag's last value), so they override the job's own flags unchecked: `clip --workers 64` or `stale_images --limit 10000000` bypass the co-tenancy census, which counts declared memory only. A second request is refused while one waits. The operator's form is "Jobs: run now" (below). |
| `pause <job> --until <ISO\|now> --note TEXT [--by WHO]` | Pauses the schedule (a request still runs) on any row, a `held()` lane's and a retired one's included (an un-retire keeps it). `--until now` lifts it and records who did. A past time is refused. |
| `list` | Every `scheduled_jobs` row with its state and latest run. |

On Railway the operator runs `list` and `pause` with `scripts/railway_iac_wizard.sh runner-list` and `runner-pause
<job> <until> <note>` (over `railway ssh` to job-runner; `pause` echoes the command and asks first, `--by operator`). A
request is #2026's "Jobs: run now" form, never the wizard. If ssh cannot reach the service, the agent runs the one-row
SQL with the operator's OK.

Recipes:
- Run a job now with a smaller batch: `python -m scraper.job_runner request clip --by cli:me -- --limit 500`.
- Hold a job for a maintenance window: `pause property_sweep --until 2026-11-01T06:00 --note "migration 690"`.
- **clip's re-embed proof** (AC2 of #2032; the procedure for every torch, transformers, Pillow or numpy bump):
  with `clip`'s schedule paused (`runner-pause clip <until> "AC2 pending"`), `request clip --by <who> --
  --verify-reembed`. It runs under the pause, writes nothing, and prints one `CLIP_VERIFY … pass=… why=…` line;
  `why=download` is inconclusive (re-request), any other failure keeps the pause. Lift the pause on a pass.
- **clip's exits:** 75 when its 1500 s budget cut a run that was making progress, or a re-tag campaign is still
  open (due again in 60 s, so a backlog chains); 0 when a re-select finds under one chunk, or when downloads failed
  but something was marked (`why=errors`); 1 when it downloaded and marked nothing (`why=r2`) or an embed failure
  was not one image's (`why=embed`); 2 when `SUPABASE_DB_URL` or an `R2_*` is missing. The last line is
  `CLIP_JOB exit=<code> why=…`.
- **After a move PR merges**, `gh run list --workflow=<deleted file> --json status,databaseId`; if a run is queued or
  running, pause the new row past that run's timeout (`runner-pause`), since a queued run in a non-cancelling group
  can still start. A re-land within an hour of its revert's deploy keeps the row's `due_at`, and its boot claims a
  slot GitHub ran in between: pause the retired row before that deploy (the slot then runs once the pause ends).
- **clip's move (#2032) held nothing for a GitHub run in flight:** both writers upsert idempotently and stamp last,
  so an overlap corrupts nothing (a rewritten vector moves about 1e-6 per value); the "AC2 pending" pause is what
  held its first slot.

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

## Health: the `job/<name>` cell (migration 682, #2026)

The pg_cron watcher writes one `job/<name>` cell per registered job-runner row (`runner = 'job-runner'`,
`registry_at` set), live or retired under an hour (a blink, judged whole), from `public.health_job_cells()`, every 5 min, so it
survives a Railway outage. A job cell is never `unknown`. C is the row's cadence, T its `timeout_s`, W the longest run
of a sibling on its resource seen since its clock started. The worst condition wins; every condition is in
`facts.conditions`.

| State | Status | Message (after `<job>: `) |
|---|---|---|
| Running, under ⅔·T | ok | `running for N of its T budget on job-runner/ab12cd34` (+ `; a request by X waits for it`) |
| Running past ⅔·T / past T / past T + 10 min | Watch / Watch / **Problem** | `running for …`; past T `; past it: the runner is stopping it`; past T + `STUCK_RUN_GRACE_S` `; past the runner's 10 min stopping grace: nothing is stopping it` |
| The latest closed run is `timeout` (ruling R-3) | **Problem** until a later run closes | `its last run hit its T budget and was stopped (timeout)`; a request's adds ` (a request by X): a request is never retried; a new request or its next slot (in N) clears it` |
| Failures in a row (its failed runs since `registry_at`) | Watch at 1; **Problem** at 3 once the streak's first failure is min(C, 1 h) old | `its last run failed: <cause>` / `3 failed runs in a row, the first N ago; the last: <cause>` |
| Late since its clock | Watch past C + T + W; **Problem** past that + min(C, 1 d) | `late: no ok run for N (Watch past …, Problem past …)` or `never ran ok` |
| Due or requested, not started for 10 min | Watch while a replica beats; **Problem** when none has for 10 min | `due N ago and not started` / `requested by X N ago and not started` (+ `; no job-runner replica has beaten for 10 min`) |
| Refused at boot (a replica's `JOBS skip`) | **Problem** | `refused at boot by job-runner/ab12cd34 (JOBS skip in its log): no replica claims it`; with no row: `… it has no row and runs nowhere` |
| Paused | ok (late, failing and timeout suppressed) | `paused for N more by X: note` (+ `; its last run failed: <cause>`); paused longer than greater(C, 7 d) is Watch, `…longer than a cycle: its slots are skipped until then` |
| Waiting for a sibling on its resource | ok | `waiting for B, which holds location-heavy (running N of its M budget)` (only while it waits) |
| Requested, under 10 min | ok | `requested by X N ago; starting` |
| New, adopted or un-retired, before its first slot | ok (history not judged until a run since closes) | `not run yet; first slot in N (… UTC)` (no trace of a run: no `job_runs` row, and no last ok, outcome or failure count on the row, which the 90-day prune keeps) / `not run since it joined the runner; first slot in N (…); before it joined: last ok M ago` (or `never ran ok`: a re-land, or an adopted lane row) |
| Otherwise | ok | `last ok N ago; next slot in M` or `; due now`; an interrupted run (a deploy stop or a fence) adds `; its last run was interrupted and is due again at once`, or only `; its last run was interrupted` once its `due_at` is ahead (an image that re-slots an un-retired row, main's or a revert, moved it through a blink) (a request: `; a request by X was interrupted; it is not re-filed`) |

- **A row is judged on its runs since `registry_at`**, when it joined the runner: stamped when the row is new,
  adopted or un-retired `REJOIN_AFTER_S` (1 h) or more after its retirement (a revert's re-land), and kept by every
  later boot (`_REGISTER_SQL`) and by migration 682's trigger, so a boot that re-stamps it (an older image with the
  job, a revert) moves nothing, nor does a blink: an older image without the job retires the row, and the latest
  booting again within the hour un-retires it with its streak, timeout and clock judged whole and its `due_at`
  kept (a cut run, a retry or a missed slot stays due; so does a slot GitHub ran under a revert re-landed within
  the hour, which the move recipe pauses). Its
  streak, last run, cause and clock read only those runs, and it waits for its first slot (ok, history not judged)
  until one of them closes, so the counter, timeout and last ok un-retiring carries from before a revert never read Problem,
  however soon its first slot (a deploy-cut first run reads ok, one failure Watch). A steady row whose only ok run
  was pruned (map_overlays: 91 d cadence, 90 d prune) is not waiting: its `last_success_at` falls after
  `registry_at`. Applied after #2025 PR-2 deployed, 682 first joins each live row, and each one an older image
  retired under an hour ago (a blink), at its first run (main's register had re-stamped it at every boot); apply it
  before PR-2's first deploy, whichever merges first.
- **The clock** is the last ok run since `registry_at`, else the first *scheduled* run since (`requested_by IS NULL`)
  minus C, else the first slot minus C, and a pause that lapsed restarts it. Never a request run. Known edge: a run
  that served a request and took its due slot keeps `requested_by` (`job_runs` has no due slot), so a
  never-succeeded job whose first run was one reads late up to one cycle later, never earlier.
- **The cause** is the latest `failed`/`timeout`/`lost` run's: `lost: the replica holding it stopped renewing its
  lease`; `scheduled_jobs.last_error` when that failure is the latest closed run; else `<outcome>, exit <code>`; a
  request's cause adds `(a request by github:x, args …)`. `log_tail` is never read.
- **Requests count as the job's runs** (#2024's release): a `--dry-run` request makes the job look freshly done and
  resets the streak; a mistyped argument is a failed run (Watch until the next ok one; re-request it corrected). One
  that makes the run outrun `timeout_s` is a recorded timeout, a **Problem** until a later run closes, and a request is
  never retried: on rent_map or map_overlays that is the next slot, up to 30 or 91 d away, unless you re-request it
  with arguments that fit the budget.
- **`job-runner`** (the fleet, present while any live row exists, or one retired under an hour ago; M counts only the live ones, so a blink reads `0 jobs registered`): ok `N replicas beating; M jobs registered`, or Watch
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
  rent_map and map_overlays carry no self-budget), and so is W5's clip (`--max-seconds 1500` against 3300: a full
  run, with its last chunk and its exit, ends by about 1600 s, under the 2200 s line).
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
