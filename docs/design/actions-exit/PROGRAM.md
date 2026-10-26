# Actions exit: make the repo private, run recurring work where it is billed by use

Status: APPROVED 2026-10-25 (rulings R1–R27 below), build starts with W0. Audit: 84 workflow files, 14 days
of run data, two review panels (42 agents on the audit and first design; 20 agents on the from-scratch
design: four architects, three judges, twelve refuters). Per-file verdicts: [dispositions.md](dispositions.md).
Decision record: [ADR 0001](../../adr/0001-scheduled-jobs-on-a-railway-job-runner.md).

## 1. Short answer

The repo goes private, after the recurring jobs have left GitHub Actions.

- Public repos get free Actions. A private one is billed per job-minute above the plan's pool. This repo
  runs about 112,000 billable minutes a month; private with nothing moved it would cost about $650/month.
- The repo moves into a company organisation (`propscout`) on **GitHub Enterprise Cloud** ($21/month,
  50,000 included minutes). CI, the canary and the click-to-run tools stay on GitHub inside that pool.
- Every scheduled production job moves to one Railway service, the **job runner**. The flip happens when
  GitHub's billed run-rate has stayed under 50,000 minutes a month for seven days, so the GitHub bill is
  never more than the seat.
- Going private removes secret scanning and push protection on GitHub's side; gitleaks replaces them.
  Code cloned while public cannot be recalled (0 forks, 0 stars today).
- Steady cost after the sprint: Enterprise $21 + Railway $12–25. The 2026-06-11 "keep public" ruling rested
  on free Actions; this program supersedes it.

## 2. North star

**Every recurring production job has one definition in code (the `scraper/jobs.py` registry), one database
row that says when it is due and who holds it (`scheduled_jobs`), and one usage-billed host (the Railway
job runner). GitHub keeps only work that needs a branch, a human click, or independence from Railway. No
step of the move leaves a job without a working host or a Health cell.**

Tested against every wave: a change that adds a second scheduler, a second definition of a job, a routing
flag, a settings knob that means "stop", or a portal name in shared scheduler code is off the path.

## 3. What is true today (verified 2026-10-25)

| Fact | Evidence |
| --- | --- |
| 84 workflow files, 34 with a `schedule:`; 50 dispatch/CI only | `ls .github/workflows` |
| ~89,000 wall-clock run-minutes/month; ~112,000 billable (per-job round-up) | 5,535 runs, 11–24 Oct |
| CI: 24,544 billable min/month (test 16,119; migrations 4,139; frontend 2,969; docs-budget 1,082) | jobs API; 200 PRs + 797 commits to `main` in 14 days |
| Price-stats self-chain: 138 dispatches in 14 days (~14,700 min/month) on a weekly cron | `scrape_price_stats.yml` |
| GitHub fires sub-hourly crons about 6×/day; the worker drains 96–99 % of every portal's queue | run counts; `detail_queue_completions` |
| Worker memory: avg 7.4 GB, peak 21.5 of 22.9 GB (an autodedup pass is 11.4 GB) | Railway metrics, heartbeat |
| Merges redeploy the image ~8–11×/day; `drainingSeconds` 30; a cut walk leaves `ended_at NULL` and does not resume | `railway.worker.json`, `portal_runner.py` |
| No SIGTERM handler in `portal_runner`, `portal` or any `*_main.py`; the worker cannot kill a pass (a thread) | code read |
| `location_jobs` (mig 384) is already a CAS lease table: holder, expiry, last start/success, outcome, failures | `location_data/resolver/lease.py` |
| Required checks: pytest, schema-and-sql, vitest, build, docs-budget; `strict=false` | branch protection API |
| 13.2 GB of Actions artifacts downloadable by any signed-in user | `/actions/artifacts` |
| Railway: Pro, builds free, usage $20/vCPU-mo + $10/GB-mo; cron services: 5-min minimum, skip-if-running, never terminated, mid-run deploy undocumented, `railway cron run` by hand | docs.railway.com |
| GitHub: Linux 2-core $0.006/min; Enterprise Cloud self-serve by card, metered per member, 50,000 min; Secret Protection $19/committer on Team or Enterprise; self-hosted runners free, fee postponed; private hosted runners are 2 vCPU / 7 GB | docs.github.com, github.com/pricing |
| Repo transfer keeps issues, PRs, secrets, webhooks, deploy keys; redirects old URLs; retires the old name; fine-grained PATs do not follow; Railway's GitHub App must be reinstalled on the organisation | docs.github.com, Railway forum |

## 4. Target architecture

### 4.1 Where each class runs (decided by the shape of the job, never job by job)

| Class | Host after | Why |
| --- | --- | --- |
| WALKS (10 portal index walks), SWEEPS (property, broker, full location resolve), INTAKE (location claims), UPKEEP (stale image URLs, rent map, map overlays, the RÚIAN registry load), ML-CPU (CLIP tag+retag) | **Railway job runner** | process-shaped work that needs an on-time slot, a kill at its budget, one lock, a Health cell, and a by-hand run from the same machine, secrets and IP |
| SQL (`recompute_city_proximity(false)` hourly) | Supabase pg_cron | one plpgsql call on the database's own clock |
| GPU (autodedup seeds, DINOv3, bake-offs) | RunPod pods, dispatched from attended GitHub forms | each run is a spending decision a human makes; the pod fetches a presigned R2 archive, never the repo |
| ATTENDED (migrations, backups, reparse, reextract, labelling, exams, boundaries, population, cities, retire, export, parked condition tools) | GitHub `workflow_dispatch` forms, unchanged | branch ref before merge, psql/pg_dump, artifacts, a typed confirm |
| CI (`test` incl. frontend + docs-budget, `migrations`, `build-extension`) | GitHub-hosted runners | PR heads; schema-and-sql needs Docker |
| CANARY (`verify_pipeline`, hourly) | GitHub-hosted, unchanged | the one alarm independent of Railway and pg_cron; its red run is the operator's e-mail |
| Price stats (weekly, needs Playwright) | GitHub schedule, by ruling; ticks through the same claim path so it has a row and a cell | browser toolchain |
| LATENCY lanes | Realtime worker, 17 → 16 lanes (`count_probe` deleted; images lane absorbs the inactive-image residue) | warm loops, not jobs |

The placement rules an agent applies to any new recurring job, in order: PR/branch code or Docker → CI;
raises the alarm when Railway or Postgres is down → the canary only; a human decides each run → an
attended form; one SQL call under ~5 min with no egress → pg_cron; needs a GPU → a RunPod pod; a
continuous loop under ~5 min cadence with warm state → a worker lane; everything else → one registry line
on the job runner. A missing toolchain is fixed in the runner image (one Dockerfile ARG), never by a
different host; only a browser or an operator ruling keeps a scheduled job on GitHub. Self-hosted GitHub
runners are a CI-only contingency (trigger: CI above the Enterprise pool two months running), never for
production.

### 4.2 The job runner (the textbook distributed-cron pattern, ~575 lines of stdlib Python)

- **One table**: `scheduled_jobs` = `location_jobs` renamed (migration, with a temporary alias view). Added:
  `argv`, `every_s`, `offset_s`, `timeout_s`, `memory_mb`, `cpus`, `due_at`, `paused_until` (finite, with a
  note and author), `requested_at/args/by`, `registry_at`, `retired_at`, a `resource` column for jobs that
  must never overlap (intake, the RÚIAN load, the retire tool share `location-heavy`; it replaces the GitHub
  `location-batch` group). `last_outcome` gains `timeout`, `interrupted`, `lost`. The dead columns
  (`enabled`, `concurrency_group`, `alert_route`, `note`) drop under R6b behind a backup receipt.
- **One history table**: `job_runs` (append-only, 90-day prune): holder, version (`RAILWAY_GIT_COMMIT_SHA`),
  args, requester, started/ended, outcome, exit code, peak RSS, a 64-line log tail, and
  `UNIQUE (job_name) WHERE ended_at IS NULL`: one open run per job, guaranteed by the database.
- **One registry in code** (`scraper/jobs.py`, ~100 lines, W2 #2023): `Job(name, argv, every_s, offset_s,
  timeout_s, memory_mb, cpus, host, resource, db_heavy)`; `every_s` is upserted as 384's `cadence`. Walk
  entries are generated from `portal_factory.PORTAL_CLASSES` with two code-only per-portal values, the
  Portal class attributes `walk_every_s` and `walk_timeout_s` (never in the DB-overridable limits layer), so
  shared scheduler code names no portal (rule 21). Every job's entry exists from W2 with a `host`:
  `job-runner`, `github-actions` (the canary and price stats), or `github-actions-until-moved` (a workflow
  still schedules it; its values are the move's targets). **A scheduler reads `runner_jobs()` only**
  (`host == job-runner`; `declared()` is the censuses'); it upserts, schedules, claims and accepts a request
  only for those names, adopts a `registry_at IS NULL` row one of them names (lease state and history kept),
  logs and skips a row it cannot reconcile instead of aborting at boot, and retires only rows with
  `registry_at IS NOT NULL AND runner = 'job-runner'` that its registry no longer names. Whole-day grids
  are phased so a slot falls at, or up to 3 days after, the calendar run it replaces. CI pins
  (`tests/scraper/test_job_registry.py`): every argv parses; walk set = `PORTAL_CLASSES`; static co-tenancy
  (the largest `RUNNER_MAX_CONCURRENT` memory declarations, one per resource, ≤ 70 % of a replica); no two
  DB-heavy jobs in one hour (a wait for a shared resource and the kill grace counted); the API's stuck-run
  cutoff (`STUCK_RUN_CUTOFF_MINUTES`) exceeds the longest walk timeout (the walks are the `scrape_runs`
  writers) plus `STUCK_RUN_GRACE_S`; the GitHub schedule set is exactly {verify_pipeline,
  scrape_price_stats}; and, computed from the workflow files with no hand-kept ledger, every job runs on
  exactly one host.
- **One service** (`python -m scraper.job_runner`), **two identical replicas**, 8 GB / 4 vCPU each,
  `restartPolicy ALWAYS`, `drainingSeconds` 180, built from the same Dockerfile with `ARG EXTRAS=api,clip,geo`
  so CPU torch, the pinned CLIP weights (revision 3d74acf9) and the geo wheels live in this image only.
  `watchPatterns` cover the trees jobs import. Variables are references to shared project variables.
- **Scheduling**: anchored, staggered slots on the database clock (`due_at` from `every_s` + `offset_s`);
  missed slots are skipped, never replayed; a new row's first slot is no earlier than `now() + timeout_s`
  so it never overlaps a GitHub run still in flight at cutover. Claim-if-due is one compare-and-set, tried
  oldest `due_at` first so a job waits at most one sibling's run for its resource; the lease TTL is 180 s,
  renewed every 60 s, with self-fencing before expiry.
- **Execution**: each job is a subprocess with its exact argv and `JOB_CPUS` exported (CLIP threads and the
  intake pool read it instead of `os.cpu_count()`); at `timeout_s` SIGTERM (a walk reads it as its
  deadline), SIGKILL after a grace; the lease is released only after the child exits; exit code and peak
  RSS are stamped; children carry `oom_score_adj +500` so an OOM kills the job, not the runner.
- **Retries**: an interrupted run (deploy, kill) is due again at once and is claimed by the new deployment
  within seconds; exit 75 ("more remains") re-dues after 60 s and replaces every self-chain and PAT; failed
  runs back off exponentially, capped at the cadence; a lost run (lease expired) is re-run once, then backs off.
- **Manual runs and pauses**: `python -m scraper.job_runner request <job> -- <args>` writes a request row the
  runner executes on the same host; one request-only GitHub form "Jobs: run now" does the same for the
  operator; `pause <job> --until <date> --note` sets `paused_until`. Nothing is a flag; a pause always ends.
- **Health**: a `job/<name>` cell per row, computed in SQL by the pg_cron watcher (so it survives a Railway
  outage), judged from `last_success_at` against the row's own cadence and `timeout_s` (warn at 2/3,
  problem at 1×), showing the cause (exit code or signal) from `job_runs`; a `held by sibling` state for the
  `location-heavy` resource; one `job-runner` heartbeat row per replica for liveness, each worker with its
  own liveness subject (new migration). The two global in-flight thresholds are deleted.
- **Deploys**: `portal_runner.run_phase` installs a SIGTERM/SIGINT handler that sets one shared stop event;
  `portal.deadline_reached()` reads it; the rate limiters wait on it; `BasePortalClient._request` checks it
  before every attempt and backoff and caps each request at the drain budget left. `by_category` persists
  per category in the same transaction as `bump_index_pages`. A cut walk resumes as part of **one cycle**: the
  resumed run skips scope groups whose reached-end is newer than the cycle's anchor (the first fragment's
  start), siblings stay adjacent, and the orphan-nomination step runs only in the fragment that closes the
  cycle over all of the portal's scopes with `last_seen_at >= anchor` as "seen" (rule 3 intact). Source of
  truth: the existing `portal_index_slices` ledger lifted into `portal_runner`; idnes's own `_slice_order`
  is deleted.
- **Secrets**: Railway shared variables are the store of record for runtime; GitHub keeps what CI, the
  canary and the attended forms read; the duplicated set is listed once in the toolkit-api skill. Deleted:
  `SCRAPE_CHAIN_TOKEN` (both hosts), `WORKER_DISPATCH_TOKEN`, `GEMINI_API_KEY`. Added: `RAILWAY_TOKEN` for
  Railway's config plan/apply recipe; a new fine-grained token under the organisation for price-stats
  "Run now".

### 4.3 Honest limits (from the refuters)

- The runner scales to 30 portals with no per-portal code and no new Railway object, but "adding a portal"
  still means a client, a parser, an adapter and config rows elsewhere; and the **realtime worker's serial
  probe/drain loops** must become per-portal passes before about 15 portals. That is a named follow-up
  wave on the scraper track, not this sprint.
- Railway adds failure modes GitHub did not have: deploy cuts (bounded by the cycle resume; the retried
  tail is measured per walk in W4 as proxy GB/day and orphan-nomination cadence) and one egress IP for every
  walk (gated per walk by the 403/429 share; the remedy is a per-portal proxy or static outbound IPs).
- A merge still redeploys the runner ~8–11 times a day; `watchPatterns` narrow it, and batching merges is
  the operator's lever.
- The per-failure table (hung job, OOM, deploy mid-run, portal block, Railway outage, DB stall), today vs
  this design, is kept in the W2 PR body; against today the design ties or wins on all but Railway-outage,
  where batch work waits as the latency lanes already do, and the canary alarms.

## 5. Waves and gates

Each wave is one or more small PRs with a one-click revert; every PR records its pre-check SELECTs and
regenerates `frontend/public/workflow-docs.json`. PRs touching the realtime worker merge only on the
merge-window light. The repo stays public until W6, so every run during the move is free.

### W0 — Organisation first, then prune while Actions are free
Operator steps (guided by a wizard script): create the `propscout` organisation on Enterprise Cloud
("personal accounts" type, metered, no trial), transfer the repo, reinstall Railway's GitHub App on the
organisation and re-link the three services, one merge posts green statuses; recreate the price-stats token
under the organisation; every peer session is told before and after. Coordination: after the propscout.cz
domain cutover has deployed (sreality-f3) and PR #2002 has merged (sreality-e6/e9).
Code: delete 27 workflows (16 dead/one-shot incl. the six R2 one-offs, 8 redundant backstops, 3 folds) and
11 one-shot scripts with their tests; fold `images.yml` into the images lane (EXPLAIN-gated), city
proximity onto pg_cron (migration 672 + a weekly `cron.job_run_details` cleanup), `clip_retag` into
`clip_tag`; stop the price-stats runaway (stalest-first ordering + a progress-gated chain on the job's own
`GITHUB_TOKEN`); delete the `count_probe` lane and its GitHub dispatch; retention-days on every artifact,
the retire backup to R2, artifacts purged to ≤ 1 GB; `SEED_MEMORY_MB` from the cgroup; the RunPod pod
fetches a presigned R2 `git archive` tarball; User-Agent contact
`propscout/1.0 (+https://app.propscout.cz; info@propscout.cz)`.
**Gate (72 h):** deploys green from the new owner; per-source pending p95 ≤ pre-W0 + 10 %; image freshness
unchanged ± 10 % and the inactive residue falling; settled-by-first-photo ± 2 pp; `cron/recompute-city-
proximity` ok 24 h; no new non-ok cell; 57 workflow files; artifacts ≤ 1 GB; 0 hits for the deleted tokens.

### W1 — Walks that survive a cut, proven on GitHub first
The § 4.2 deploy seams and the cycle resume; `bezrealitky_main` gains `--index-only/--drain-only`; the four
combined scrapers (bezrealitky, maxima, mmreality, remax) become walk-only; the `JOB_CPUS` helper.
**Gate:** unit tests (SIGTERM mid-category → rc 143, `ended_at` set, the cut group nominates nothing, the
next fragment starts at the stalest group and only the closing fragment nominates orphans, SIGTERM during a
hung request finalises under the drain budget); one cancelled realitymix walk finalises within 15 s; 7 days
with 0 index runs `ended_at NULL` > 6 h and nominations per cycle ± 10 %.

### W2 — The runner, live with the smallest jobs
One migration (rename + alias view, the new columns, `resource`, `job_runs` with its unique open-run index,
per-worker liveness subjects); `scraper/job_lease.py` (moved + claim/renew/fence/release), `scraper/jobs.py`,
`scraper/job_runner.py`, the `job/<name>` SQL arm, the request-only `run_job.yml`; the `job-runner` service
added to `.railway/railway.ts`. **Scope change 2026-10-25 (operator ruling Q24, agent-platform grill):** the
move of the three existing services to that file with Railway's plan-on-PR/apply-on-merge recipe, and the
deletion of the three `railway*.json`, is the agent-platform programme's M0 (PR #2004, `MVP.md` § M0, due
within the week, hard cutoff 2026-12-01); #2025 reuses the file and lands it for all four only if M0 has not
landed by then. First jobs: stale_images, rent_map, map_overlays. Each later PR flips its entries' host to
`job-runner` and deletes the matching YAMLs; the pairing census fails either half alone. A move PR folds or
drops each other step of the file it deletes, and says which in its body. A move never lowers `timeout_s`. A
long-cadence entry's slot falls at, or up to 3 days after, the calendar run it replaces (the 30-day grids
drift ~5 days a year earlier), else the PR re-phases `offset_s`. After merge, `gh run list --workflow=<file>
--json status,databaseId` shows any queued or running run of the deleted file; if one exists, the new row
is paused (`job_runner pause --until`) past that run's timeout, since a queued run in a non-cancelling group
can start after the merge. A new row's first slot comes after `now() + timeout_s`; rollback is a revert.
**Gate (7 days):** every job cell green; ≥ 3 deploys survived with interrupted runs re-claimed within 1 min;
one request run, one pause with expiry and one self-fence drill exercised; 0 unique-index violations, 0
double holders; resolver and intake lanes green 48 h after the rename; `railway config plan` = 0 changes.

### W3 — Sweeps and intake
property_sweep (keeping its 56 h fail line), broker_sweep, location_full_resolve, location_intake (exit 75
for backlogs; the pool width from `JOB_CPUS`); the intake self-chain, its yield bash, the `location-batch`
GitHub group and the last `SCRAPE_CHAIN_TOKEN` users deleted; the `location_resolve` `*/15` tick deleted.
**Gate (7 days):** derived artifacts fresh; `location_town_coverage` not worse; sweep durations ± 20 %; a
backlog drains at least as fast as the chain did; no two DB-heavy jobs overlap; intake reads `held by
sibling` during a registry load instead of red.

### W4 — The walks
In ascending duration, 2–3 per PR: web, maxima, bezrealitky, remax, mmreality, sreality (hourly, R24),
realitymix, ceskereality, bazos, idnes last. Walk argv drops `--max-seconds`; the runner's SIGTERM at the
walk timeout is the one budget.
**Gate per walk (7 days):** pages, reached-end and nominations per cycle ± 10 %; 403/429 share from
Railway ≤ the GitHub baseline; 0 `ended_at NULL` after ≥ 5 mid-walk deploys; proxy GB/day within its band;
delisting and coverage cells unchanged; aggregate peak ≤ 70 % of a replica.

### W5 — CLIP, the registry load, the GitHub tick, the leftovers
`clip` every 15 min in the runner image (one unsharded run, exit 75 for backlogs; `clip_tag.yml`, the shard
plumbing and the weights cache deleted); `ruian_load` moves to the runner (geo wheels in the build);
price_stats ticks daily through `job_runner once` on GitHub so it has a row and a cell; `location_refetch`
reads its due time from its row (`first_delay_seconds` deleted); a test pins the GitHub schedule set.
**Gate:** CLIP lag p90 ≤ 45 min; a 100-image re-embed identical within fp32 tolerance; both GitHub-hosted
jobs show cells; `location_refetch` runs once a day across ≥ 3 deploys; **the flip gate: 7 days of billed
GitHub run-rate under 50,000 min/month.**

### W6 — Consolidate CI, prove the private paths, flip
`test.yml` absorbs `frontend-build` and `docs-budget` as one `frontend` job (vitest `--maxWorkers=2`, timeout
sized for 2-vCPU runners); PR-only `cancel-in-progress` on test + migrations; `build-extension` job renamed
`extension`; required contexts → pytest, schema-and-sql, frontend; gitleaks: one history scan, a PR job over
the PR's commit range, a commit/push hook; the two fixture map keys replaced by placeholders; LICENSE notice
(all rights reserved); Dependabot alerts on; docs lose "PUBLIC" (CLAUDE.md, issue-tracker, scraper-ops);
R6a deletions; R6b drops behind receipts; the Actions budget set at $100 with alerts.
**Gate before the flip:** the W5 flip gate holds; artifacts ≤ 1 GB; one pod logs `step=fetch ok` from the
tarball; history scan clean or every finding rotated/fingerprinted; one autodedup `rt_seed` at the current
widest scope finishes on a 2-vCPU/7 GB runner, else R5 decides; a deliberately failing draft PR is blocked.
**After the flip (72 h):** `private:true`; exactly 3 required contexts; Railway statuses for all four
services on the next merge; canary green; billing page run-rate inside the pool; RunPod and autodedup
dispatches succeed; the canary and `realtime-scrapers.md` glossary entries updated.

## 6. Cost (USD per month)

| Line | After | Basis |
| --- | --- | --- |
| GitHub Enterprise Cloud, one seat | 21 | 50,000 included minutes; branch protection; organisation ownership |
| GitHub Actions overage | 0 | CI 23–42k + attended 1.3–2.5k + canary ≤ 0.7k + ticks ~0.5k = 25–46k, inside the pool |
| Railway job runner (walks, sweeps, intake, upkeep, CLIP, two idle replicas) | 12–25 | time-weighted at $0.0278/vCPU-h and $0.0139/GB-h; measured from `job_runs` peak RSS in W2–W5 |
| pg_cron, artifacts, secret scanning (gitleaks) | 0 | |
| Residential proxy | unchanged + a 5–10 % retried tail on proxied walks | measured in W4 |
| **Total** | **~35–50** | versus ~$650 private with nothing moved |

The flip never precedes the W5 gate, so no month is billed above the seat.

## 7. Subtraction ledger

Deleted: 46 workflow files (27 in W0, 17 across W2–W5, `clip_tag` in W5, 2 in W6; ~4,400 YAML lines);
~45 concurrency groups; every self-chain and PAT; the `count_probe` lane and its dispatch (~200 lines +
tests); 11 one-shot scripts (~2,850 lines) and their tests (~1,400); the image shard flags; four duplicate
drain phases; the `location-batch` GitHub group, the intake chain and yield; the `location_resolve` tick;
`railway*.json` ×3; the two global in-flight thresholds and the hand-kept stuck-run cutoff; the per-walk
`--max-seconds` duplicate; idnes's own slice ordering; the `LOCATION_INTAKE_WORKERS` override; the 4-way
CLIP shard matrix and weights cache; `location_refetch`'s first-delay machinery; the 18 h GitHub slack on
re-hosted artifact rows; the anonymous GitHub clone in RunPod pods; 13.2 GB of public artifacts. By ruling:
up to 9 more files (R6a) and the dead lease columns, alias view and index (R6b).
Never built (dropped from the first design): the shared `lanes.py` move, 17 interval knobs, the due-gated
GitHub matrix, the overdue-only fallback rule, the disarm wave, runtime memory admission.

Added: the job-runner service (two replicas) and ~5 Dockerfile lines (one ARG); columns on the reused
`scheduled_jobs`; `job_runs`; `scraper/jobs.py` (~100), `scraper/job_runner.py` (~220), `scraper/job_lease.py`
(moved, ~+60); the `job/<name>` SQL arm (~60); the W1 seams (~60); `run_job.yml` (~30, request-only);
`.railway/railway.ts` (~80); two code-only per-portal walk values; ~250 lines of tests; one `job-runner`
heartbeat row per replica; one pg_cron history cleanup; `RAILWAY_TOKEN`; a LICENSE notice.

Net: workflow files 84 → ~38 (31 if R6a passes in full); live GitHub schedules 34 → 2; production schedulers
2 → 1; Railway services 3 → 4; Railway config files 3 → 1; worker lanes 17 → 16; roughly −8,500 to −9,000 lines.

## 8. Rulings (operator, 2026-10-25)

R1 shape approved · R2 organisation `propscout` on Enterprise Cloud, transfer in W0 after the domain cutover ·
R3 flip only after the move, gated on 7 days under 50,000 billed min/month · R4 CLIP moves (R23: torch only
in the runner's build) · R5 operator UX of moved jobs: one request form, Railway logs, pause by row ·
R6a six parked/one-off workflows + `compute_image_phash` (bounded refill in the images lane) +
`backfill_idnes_brokers` (after re-measuring the 637-row gap) deleted; R6b drops behind receipts ·
R7 price stats: one weekly run · R8 glossary terms adopted · R9 User-Agent contact
`propscout/1.0 (+https://app.propscout.cz; info@propscout.cz)` · R10 LICENSE notice + Dependabot alerts ·
R11 Enterprise, bought directly · R12 no walk throttling · R13 gitleaks, Secret Protection left open ·
R14 Actions budget $100 at the flip · R18 CI volume cuts out of scope · R19 ADR 0001 · R20–R21 job runner
replaces shape B; flip gate as above · R22 approved · R23 CLIP in the runner build only · R24 sreality walk
hourly · R25 RÚIAN load moves to the runner · R26 CLI + request-only form; Health buttons when asked ·
R27 two replicas.
**Still open, decided when measured:** autodedup `rt_seed` on a private 2-vCPU/7 GB runner (W6 gate): a
4-core larger runner (~$3 per seed), the E943/E944 memory work, or a request-only job on a heavy runner pool.

## 9. Rejected alternatives

- **One Railway cron service per job**: 18 services today, ~38 at 30 portals, each rebuilding on every
  matching merge (~150 builds/day); Railway never stops a hung run and skips every later tick until a human
  redeploys; no Health reporting; mid-run deploy behaviour undocumented; runs with arguments would still
  need a lease row, a Health arm and a form, which is most of a runner spread over 18 objects.
- **Self-hosted GitHub runners on Railway**: GitHub's throttled cron stays the scheduler; every YAML and
  group stays; no Docker, so no schema-and-sql; PR code beside production secrets; an admin registration
  credential; the postponed fee may return; Enterprise's pool already covers CI. CI-only contingency.
- **Shape B batch-worker as first written**: one container, a supervisor shared with the realtime worker
  (coupling batch PRs to the worker's merge window), 17 interval knobs (flags in disguise), manual runs
  executing on GitHub (a second host and egress), a cutover flag.
- **Walks as realtime-worker lanes**: a thread cannot be killed; the worker peaks at 94 % of memory; one OOM
  takes every latency lane down.
- **Established orchestrators** (APScheduler, Celery, Prefect/Dagster, Temporal, Kubernetes CronJobs,
  Railway Functions): each fails at least one of: deletes more than it adds, no new always-on service or
  SaaS, pooler-safe, can hard-kill, Health-readable, operable by agents (rule 7).
- **pg_cron as the clock for Python jobs**; runtime aggregate memory admission; a GitHub break-glass
  executor for scheduled jobs; a 4-hour `drainingSeconds` as the deploy answer; path-gated or skip-if-green
  CI checks; strict up-to-date merges; pytest-xdist; flipping before the move.

## 10. What the two adversarial passes changed

First panel (shape B): the bill restated upward; the stop event pushed into the client's retry loop and the
lease released only after the child exits; resume from the one existing slice ledger; aggregate memory gating;
`runpod_reap` kept and two deletions turned into rulings; per-worker liveness subjects; push protection's
loss stated plainly with a push-time replacement; the cutover flag removed; the ledger's temporary items
listed. Second panel (from scratch): the cycle-based resume so a cut walk still reaches the portal's end
before nominating; the per-failure table instead of a bare interventions count; job cells computed by the
pg_cron watcher so Health survives a Railway outage; scaling claims scoped to the runner with the worker's
serial drain loop named as the next limit; walk cadence kept code-only; the `location-heavy` resource
column restored in place of the deleted GitHub group; `location_refetch` and `sold_comps` moved toward the
registry; runtime memory admission replaced by a static co-tenancy test plus `oom_score_adj`.
