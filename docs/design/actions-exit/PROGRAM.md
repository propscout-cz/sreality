# Actions exit: make the repo private, run production jobs where they are billed by use

Status: PROPOSED 2026-10-25, awaiting the operator's rulings (§ 8). Audit: 84 workflow files, 14 days of
run data, a 42-agent review (9 family audits, platform, pricing, CI; 3 architects, 3 judges; 8 claims × 3
adversarial refuters). Per-file verdicts: [dispositions.md](dispositions.md).

## 1. Short answer

Yes, the repo can go private, and yes, the scheduled jobs have to leave GitHub Actions first.

- Public repos get free, unmetered Actions. A private repo is billed per job-minute above a small pool
  (Free: 2,000 min/month, Pro: 3,000). This repo runs about 112,000 billable minutes a month
  (measured 18–24 Oct, per-job round-up). Private with nothing moved: about **$650/month**, and with a
  personal budget (which always stops hard) Actions, and the scraping on it, would halt on day 1–2.
- GitHub Pro ($4/month) is a hard prerequisite: on Free, a private repo loses branch protection and the
  five required checks that are the production gate (Railway auto-deploys `main`).
- Going private also removes secret scanning and push protection; no personal plan brings them back
  (§ 8, ruling 3). It does not recall what was public: 0 forks, 0 stars, but clones cannot be undone.
- The 2026-06-11 "keep public" ruling rested on free Actions; this program supersedes it.

After the sprint the bill is about **$150/month** (range $115–190; § 6), nearly all of it CI, which stays
on GitHub because the schema replay needs Docker and a user-owned repo has no cheaper gate. The
reference alternatives: $650 doing nothing, or a self-hosted CI runner pool (~$25, § 9 contingency).

## 2. North star

**Every recurring production job has one definition in code (the `JOBS` registry), one database row
that says when it is due and who holds it (`scheduled_jobs`), and one usage-billed host (Railway).
GitHub keeps only work that needs a branch, a human click, or independence from Railway. No step of the
move leaves a job without a working host or a Health cell.**

Every wave is tested against it: a change that adds a second scheduler, a second definition of a job, a
routing flag, or a portal name in shared code is off the path.

## 3. What is true today (verified 2026-10-25)

| Fact | Evidence |
| --- | --- |
| 84 workflow files, 34 with a `schedule:`; 50 dispatch/CI only | `ls .github/workflows` |
| ~89,000 wall-clock run-minutes/month; ~112,000 billable (per-job round-up) | 5,535 runs, 11–24 Oct, `/actions/runs` |
| CI alone: 24,544 billable min/month (test 16,119; migrations 4,139; frontend 2,969; docs-budget 1,082) | jobs API sample, 200 PRs + 797 commits to `main` in 14 days |
| Price stats self-chain ran 138 dispatches in 14 days (~14,700 min/month) against a weekly cron | `scrape_price_stats.yml`, SCRAPE_CHAIN_TOKEN |
| GitHub fires this repo's sub-hourly crons about 6×/day (`*/15` is fiction) | memory 2026-07, confirmed by run counts |
| The worker already does 96–99 % of every portal's detail drain; the GH drains claim 0 | `detail_queue_completions` 7-day shares |
| Worker memory: avg 7.4 GB, peak 21.5 GB of a 22.9 GB limit (autodedup pass 11.4 GB) | Railway metrics + heartbeat |
| Railway redeploys the image on ~8–11 merges/day; `drainingSeconds` 30; walks do not resume | `railway.worker.json`, git log |
| Required checks: pytest, schema-and-sql, vitest, build, docs-budget; `strict=false` | branch protection API |
| 13.2 GB of Actions artifacts (autodedup judge/export/score, a 1.2 GB claims backup) downloadable by any signed-in user | `/actions/artifacts` |
| Railway: Pro plan (24 GB/replica since 2026-10-16), builds free, usage $20/vCPU-mo + $10/GB-mo | memory 2026-10-16, docs.railway.com/pricing |
| Hosted Linux 2-core: $0.006/min (cut 2026-01-01); self-hosted free (fee postponed, not cancelled) | docs.github.com billing |

## 4. Target architecture

### 4.1 Where each class of job runs

| Class | Host after | Why |
| --- | --- | --- |
| CI (`test`, `migrations`, `build-extension`) | GitHub hosted | gates every merge; schema-and-sql needs Docker |
| Canary (`verify_pipeline`, hourly) | GitHub hosted, unchanged | the one alarm independent of Railway and pg_cron; its red run is the operator's e-mail |
| Attended tools (`apply_migration`, `backup_before_drop`, `reparse`, `reextract`, `autodedup`, labelling/exam/bake-off tools, parked condition tools, retire/boundaries/population/cities/export) | GitHub `workflow_dispatch`, same forms | need a branch ref, psql/pg_dump, a toolchain the image lacks, or a human click; ~1,300 min/month |
| Two rare schedules (`scrape_price_stats` weekly, `location_registry_load` monthly) | GitHub hosted | need Playwright / the `[geo]` extra; under $5/month each |
| Scheduled production (10 walks, property + broker sweeps, location intake + full resolve, stale images, rent map, map overlays; CLIP by ruling) | **Railway `batch-worker`**, a sibling service from the same image | usage-billed, fires on time, kills a hung job, one registry |
| Latency lanes | Railway `realtime-worker`, 17 → 16 lanes (`count_probe` deleted; images lane absorbs the inactive-image residue) | unchanged envelope |
| Pure SQL (`recompute_city_proximity(false)` hourly) | Supabase pg_cron | one plpgsql call; GitHub delivered it ~5×/day |
| GPU work (RunPod pods) | still dispatched from attended GitHub workflows | the pod fetches a presigned R2 `git archive` tarball instead of cloning the public repo |

### 4.2 The batch-worker (hosting shape B)

- `python -m scraper.batch_worker`, same Dockerfile, `restartPolicy ALWAYS`, `drainingSeconds` 180,
  memory limit 8 GB, `watchPatterns` limited to the paths batch jobs import.
- **One registry in code**: `JOBS = (Job(name, argv, interval_s, timeout_s, mem_mb), …)`. Walk entries are
  generated from `portal_factory.PORTAL_CLASSES` (a test pins `== REALTIME_SOURCES`), so the scheduler
  holds no portal names (rule 21). Per-portal walk cadence and budget become two generic `PortalLimits`
  fields, `walk_interval_s` and `walk_timeout_s`, layered from the existing per-portal
  `operational_limits` JSONB (no migration) and replacing today's cron lines and `--max-seconds` argv.
- Each job runs as a **subprocess** with the exact argv its YAML runs today. At `timeout_s` the scheduler
  sends SIGTERM (which a walk reads as its deadline, § 4.4), SIGKILL after the grace, waits for the child
  to exit, and only then releases the lease. Exit code and peak RSS are stamped into the heartbeat.
- **Aggregate memory admission**: a job starts only while the sum of running jobs' `mem_mb` stays at or
  below 70 % of the cgroup `memory.max`; `memory.current`/`memory.peak` are stamped per pass. Walks are
  I/O-bound (0.3–0.5 GB each); the location intake pool gets an explicit worker count.
- Supervisor, watchdog and heartbeat writer move out of `realtime_worker.py` into `scraper/lanes.py`,
  shared by both services (a pure move).
- Cadence knob per job: `app_settings.batch_<job>_interval_seconds` (absent = registry default,
  0 = stop). The lane-knob pattern; no flags.

### 4.3 One due-and-lease row per job

The existing generic lease table `location_jobs` (migration 384) is renamed `scheduled_jobs` and
`location_data/resolver/lease.py` moves to `scraper/job_lease.py`, shared by the resolver, the location
jobs and the batch-worker. Claim-if-due is one compare-and-set: claimable when free (or expired) and
`last_started_at` older than the interval after an ok run, or than `RETRY_AFTER_S` (900 s) after a failed
or cut run. TTL 180 s, renewed every 60 s. A cancelled release (deploy, cancel) no longer counts as a
failure. One `location-batch` row serialises intake, the RÚIAN load and the retire tool on any host,
replacing the GitHub concurrency group, the yield bash and the worker's yield-to-GitHub logic.

**The lease is also the cutover mechanism; there is no routing flag.** During W2 GitHub executes every
registry job when due. In W3 the batch-worker claims when due, and the GitHub matrix claims only a job
that is *overdue* (no start for 2× its interval): a fallback that runs only when the primary missed.
W4 deletes the GitHub schedule. Rolling one job back is reverting its registry entry; rolling the host
back is stopping the service.

### 4.4 Walks that survive a deploy (portal-agnostic seams, built first on GitHub)

- `portal_runner.run_phase` installs a SIGTERM/SIGINT handler that sets one shared `threading.Event`.
  `portal.deadline_reached()` returns true once set; `RateLimiter`/`LedgerRateLimiter` wait on the
  event instead of sleeping; `BasePortalClient._request` checks it before every attempt and backoff and
  caps each request timeout at the time left in the drain budget. The walk finalises `scrape_runs`
  within one short request and exits 143. A cut is a stop of OURS, so the cut scope nominates nothing
  (rule 3 unchanged).
- `by_category` is persisted when each category ends, in the same transaction as `bump_index_pages`,
  so a SIGKILL keeps the resume state.
- Resume: `run_index_walk` visits scope groups stalest-reached-end first (siblings adjacent) and skips
  groups whose reached-end is newer than the cut run's start. The source of truth is the existing
  `portal_index_slices` ledger (migration 454), lifted into `portal_runner`; idnes's own `_slice_order`
  is deleted, so one ledger remains.
- Honest cost: a cut costs the in-flight category plus a resume. The retried tail is measured in W3
  (proxy GB/day, orphan-nomination cadence per portal); if material, `watchPatterns` narrow further.

### 4.5 Health

- `job/<name>` cell per registry job, read from `scheduled_jobs`: age of last success against interval
  (host-aware while GitHub executes: p90 start gap × a multiple, as `gh/` cells do today), last outcome,
  consecutive failures, in-flight time against the row's own `timeout_s` (warn 2/3, problem 1×). The two
  global lane in-flight thresholds are deleted. A `location-batch` hold reads as ok/info.
- A forward migration gives every worker heartbeat its own liveness subject (`worker/<name>`), so
  acknowledging a batch-worker incident can never hide a realtime-worker death.
- `derived_artifacts` rows whose producer moves (`property_sweep`, `map_overlay_tiles`) are re-hosted,
  keeping property_sweep's 56 h fail line.
- `gh/<workflow>` cells and the `record_workflow_failures` poller stay for the two GitHub schedules and
  the CI push runs on `main`.

### 4.6 Secrets: two stores, each holding only what runs there

Railway project variables are the store of record for runtime secrets (`SUPABASE_DB_URL`,
`SUPABASE_DB_SESSION_URL`, `R2_*`, `SCRAPER_PROXY_URL` referenced by the batch-worker). GitHub keeps
what CI, the canary, the attended tools and the two rare schedules read; the duplicated set is listed
in the toolkit-api skill as "rotate in both". Deleted: `SCRAPE_CHAIN_TOKEN` (GitHub and Railway),
`WORKER_DISPATCH_TOKEN`, `GEMINI_API_KEY`. Added: `RAILWAY_TOKEN` for Railway's config plan/apply recipe.

### 4.7 Operator UX after the sprint

- Migrations, backups, reparse, autodedup: the same "Run workflow" clicks as today.
- Run a moved job now: Actions → "Jobs: run a scheduled job" → `job=walk_idnes` (args optional); one
  form replaces ~20 per-workflow forms.
- Stop or re-time a job: Settings key `batch_<job>_interval_seconds` (0 = stop).
- Logs of scheduled runs: the Railway batch-worker stream, each line prefixed `[job]`.
- Health shows `job/<name>` cells beside the lane cells.

## 5. Waves and gates

Each wave is one or more small PRs with a one-click revert; every PR records its pre-check SELECTs in
the body and regenerates `frontend/public/workflow-docs.json`. Any PR touching the realtime worker
merges only on the merge-window light.

### W0 — Prune while Actions are still free (27 files)
Delete 16 dead/one-shot workflows and their scripts (~2,850 lines + ~1,400 test lines); delete 8
redundant backstops of live worker lanes (pre-check: GH drains wrote ≤ 5 % of completions over 7 days,
queue < 1,000, no heartbeat gap > 300 s in 14 d); fold `images.yml` into the images lane (EXPLAIN-gated:
no full sort, < 1 s), `recompute_city_proximity` onto pg_cron (migration 672), `clip_retag` into
`clip_tag` as a step; stop the price-stats runaway (stalest-first ordering + a progress-gated chain on
the job's own `GITHUB_TOKEN`, no PAT); delete the dark `count_probe` lane and its GitHub dispatch path;
retention-days on every artifact, the retire backup to R2, artifacts purged to ≤ 1 GB; `SEED_MEMORY_MB`
measured from the cgroup. Rewrite the scraper-ops skill's drain/backstop sections and the
`realtime_drain_disabled_sources` semantics (it freezes a portal's queue; it no longer hands it to GitHub).
**Gate (72 h):** per-source pending p95 ≤ pre-W0 + 10 %; active pending-image age unchanged ± 10 % and
the inactive residue falling; settled-by-first-photo share ± 2 pp; `cron/recompute-city-proximity` ok 24 h;
no new non-ok cell; 57 workflow files; artifacts ≤ 1 GB; 0 hits for the deleted tokens.

### W1 — Walks that survive a cut, proven on GitHub first
The § 4.4 seams; `bezrealitky_main` gains `--index-only/--drain-only`; the four combined scrapers
(bezrealitky, maxima, mmreality, remax) become walk-only (the worker already drains them).
**Gate:** unit tests (SIGTERM mid-category → rc 143, `ended_at` set, cut group nominates nothing, next
walk starts at the stalest group, SIGTERM during a hung request finalises under `drainingSeconds`); one
cancelled realitymix walk finalises within 15 s; 7 days with 0 index runs `ended_at NULL` > 6 h and
nominations per walk ± 10 %.

### W2 — One registry, one lease, still executed by GitHub
Migration 673 (rename + temporary alias view, drop NOT NULL on the dead columns); `scraper/job_lease.py`
with claim-if-due; `scraper/batch_worker.py` (~200 lines; verbs `list`, `run`); `run_job.yml`
(dispatch form + a `*/15` due-gated matrix, max-parallel 6); `scraper/lanes.py` (pure move);
`job/<name>` health arm; the `location-batch` row; Railway config moved to `.railway/railway.ts` with
Railway's plan-on-PR/apply-on-merge recipe (Railway stops reading `railway.json` on 2026-12-01). Delete
the 17 scheduled YAMLs (1,923 lines), ~20 concurrency groups, the intake chain and yield, the resolve
`*/15` tick, the three `railway*.json`, the two global in-flight thresholds, the hand-kept 300-min
stuck-run cutoff.
**Gate:** each registry argv byte-equal to the YAML it replaces (PR body); `railway config plan` = 0
changes; 7 days: every `job/<name>` green, per-walk pages / reached-end / nominations ± 10 %, 0 double
holders, resolver + intake lanes green 48 h after the rename.

### W3 — Railway batch-worker live, GitHub fallback armed
Add the service; the GitHub matrix switches to overdue-only; re-host the two artifact rows.
**Gate per job (7 days):** Railway holds ≥ 90 % of due runs; `job/<name>` green; domain cells unchanged
(walks: per-unit pages, delisting, inflow, fetch-error Wilson bound, 403/429 share from Railway ≤ the
GitHub baseline; sweeps: artifacts fresh; intake: `location_town_coverage` not worse). **Service-wide:**
worker cell green; 0 `ended_at NULL` runs after ≥ 5 mid-walk deploys; aggregate peak ≤ 70 % of the limit;
proxy GB/day within the measured band.

### W4 — Disarm GitHub, one budget per job, delete the scaffolding
`run_job.yml` loses its schedule (stays as the console); walk argv drops `--max-seconds` (the
scheduler's SIGTERM at `walk_timeout_s` is the one budget); CLIP moves if ruling 4 passes (torch-CPU +
pinned weights in the image, one `clip` job, `clip_tag.yml` deleted) else stays as one unsharded job;
a test pins the schedule set to {verify_pipeline, scrape_price_stats, location_registry_load}; docs
(realtime-scrapers.md "cold lane" section → batch-worker; architecture rules 3/19/20 wording; CLAUDE.md
host lines; scraper-ops + toolkit-api skills).
**Gate (7 days, Railway the only scheduler):** every `job/<name>` green; pages per walk ± 10 % of W3;
if CLIP moved: lag p90 ≤ 4.5 h, 100-image re-embed identical within fp32 tolerance.

### W5 — Consolidate CI, prove the private-repo paths, flip
`test.yml` absorbs `frontend-build` and `docs-budget` as one `frontend` job (timeout sized for 2-vCPU
private runners, vitest `--maxWorkers=2`); PR-only `cancel-in-progress` on test + migrations (never on
`main`: for 64 % of merges the push run is the only test of the deployed tree); pytest to the 1-vCPU
runner only if 20 runs show p95 ≤ 11 min; `build-extension` job renamed `extension`; required contexts
→ pytest, schema-and-sql, frontend; RunPod pods fetch a presigned R2 tarball; scraper User-Agents stop
naming the GitHub URL; secret detection per ruling 3; CLAUDE.md / issue-tracker doc lose "PUBLIC";
gated destructive drops (ruling 6b) each behind a `backup_before_drop` receipt.
**Gate before the flip:** Pro active, budget set with alerts and the non-Actions migration fallback
documented (local `psql -f` + `apply_migration_receipt`, `pg_dump`); artifacts ≤ 1 GB; one RunPod pod
logs `step=fetch ok` from the tarball; history scan clean or every finding rotated/fingerprinted; one
autodedup `rt_seed` at the current widest scope finishes on a 2-vCPU/7 GB runner, else ruling 5 names
the larger-runner option; a deliberately failing draft PR is blocked under the new contexts.
**After the flip (72 h):** `private:true`; exactly 3 required contexts; Railway statuses for all four
services on the next merge; canary green; run-rate on the billing page ≤ $200/month; RunPod and
autodedup dispatches succeed.

## 6. Cost (USD per month; today = $0 on GitHub because the repo is public)

| Line | After | Basis |
| --- | --- | --- |
| GitHub Pro | 4 | branch protection on a private repo |
| CI (test incl. frontend+docs, migrations, extension) | 80–120 | ~23,300 billed min after merges and PR-only cancel; 2-vCPU private runners; $80 if pytest fits the 1-vCPU runner, $120 if not; minus 3,000 included |
| Canary | 1 | ~139 runs × 1 min |
| Attended tools | 8–15 | ~1,300 min typical; ~2,500 in a dedup or heal month |
| Rare GitHub schedules | 4–30 | weekly price stats + monthly registry load; $30 only if ruling 7 asks for the ≤ 8-day refresh |
| Railway batch-worker | 10–25 | ~22 process-hours/day of I/O-bound walks + sweeps; measured in W3 |
| CLIP | 15 (ruling 4 yes) / 21 (no) | Railway compute vs one unsharded GitHub job |
| realtime-worker delta, pg_cron, artifacts, secret scanning (option A) | 0 | |
| **Total** | **~150 (115–190)** | |
| Reference: private now, nothing moved | ~650 | ~112,000 billed min − 3,000, ×$0.006 |
| Reference: self-hosted CI-only pool on Railway (contingency) | ~25 | replaces the CI line; needs a runner image with native PG17 + PostGIS + pgvector; exposed to the postponed self-hosted fee |

The CI line is driven by PR volume (200 PRs per fortnight, mostly agent-made). It scales down linearly
with fewer or batched PRs; that lever belongs to the operator, not to this sprint.

## 7. Subtraction ledger

Deleted (unconditional): 46 workflow files (27 in W0, 17 in W2, 2 in W5; ~4,400 YAML lines);
~45 concurrency groups; every self-chain and PAT (`SCRAPE_CHAIN_TOKEN` on both hosts,
`WORKER_DISPATCH_TOKEN`); the `count_probe` lane and its dispatch path (~200 lines + tests); 11 one-shot
scripts (~2,850 lines) and their tests (~1,400); `--image-shard` / `--images-active-only` and the shard
predicate; four duplicate drain phases; the `location-batch` GitHub group, the intake chain and yield;
`railway.json` ×3; the two global in-flight thresholds and the hand-kept stuck-run cutoff; the per-walk
`--max-seconds` duplicate budget; idnes's own slice ordering; a duplicate tsc run, an npm install and the
doubled `build` context; the anonymous GitHub clone in RunPod pods; 13.2 GB of public artifacts.
By ruling: up to 9 more files (`clip_tag` + ruling 6), 6 dead lease columns + 1 index + 1 table (6b).

Added: `scraper/batch_worker.py` (~200), `scraper/job_lease.py` (moved, ~+40 net), `scraper/lanes.py`
(moved, 0 net), the § 4.4 seams (~60 incl. the client/ratelimiter event waits), two `PortalLimits`
fields, the `job/<name>` arm (~50), `run_job.yml` (~45 after W4), `railway_config.yml` (~35) +
`.railway/railway.ts`, one Railway service, one secret (`RAILWAY_TOKEN`), migrations 672/673 + the
liveness-subject and re-host migrations, ~17 `batch_<job>_interval_seconds` keys (absent by default),
tests. Temporary (deleted in W4): the `run_job.yml` schedule block and the overdue-only claim rule.
By ruling: a gitleaks job, torch in the image.

Net: files 84 → 40 workflows (31 if every ruling passes); live GitHub schedules 34 → 3; schedulers for
production work 2 → 1; Railway→GitHub couplings 2 → 1; worker lanes 17 → 16; Railway config files 3 → 1;
Railway services 3 → 4; roughly −9,000 lines net (estimate; counted per PR).

## 8. Rulings only the operator can make

1. **GitHub Pro + budget (yes/no):** upgrade `waiff` to Pro ($4/month, card on file); set a personal
   Actions budget of about $300/month (2× the forecast; a personal budget is a hard stop and would halt
   CI and the migration path) with 50/75/90 % alerts.
2. **When to flip (A/B):** A (recommended) at the end of W5, after the steady bill is measured, so
   every shadow and fallback run during the ~7–8 weeks is free. B: right after W0, paying ~$450–650/month
   prorated until W4. Also name the contact (e-mail or site) that replaces `github.com/waiff/sreality`
   in the scraper User-Agents.
3. **Secret scanning (A/B):** A ($0): a one-time gitleaks scan of the full history (rotate anything
   found), a gitleaks job on every PR over the PR's commit range, and a pre-commit/pre-push hook for
   agents and hand pushes; the two open alerts (third-party map keys in ceskereality fixtures) are
   replaced by placeholders. Residual: a secret pushed to a branch is found at PR time, not push time.
   B (~$23/month + a repo transfer): move the repo into an organisation on Team and buy Secret Protection,
   which keeps real push-time blocking.
4. **CLIP into the image (yes/no, rule 7):** torch-CPU + pinned weights (~+1 GB for api and worker too)
   so CLIP tag+retag runs on the batch-worker (~$15; lag from 2.5 h median to minutes, so properties
   settle and publish sooner). No: one unsharded hourly GitHub job (~$21).
5. **How moved jobs are operated (yes/no):** one "Jobs: run a scheduled job" form instead of ~20;
   scheduled logs in the Railway stream; stop/re-time via Settings. Migrations, backups, reparse and
   autodedup keep their forms. If an autodedup `rt_seed` outgrows the private 7 GB runner, choose the
   4-core larger runner (~$0.012/min, ~$3 per seed) or the E943/E944 memory work.
6. **Deletions needing an OK (per line):** (a) workflows: discover/aggregate/compare condition markers
   (rule 14's "six parked" becomes three), `label_proposal_backfill` (+ the Labeling page text),
   `backfill_unit_price_masquerade` (after its owed realitymix write), `dinov3_bakeoff_manifest`,
   `compute_image_phash` (decide the phash re-arm contract: a bounded refill in the images lane, or
   terminal-mark on inline failure), `backfill_idnes_brokers` (after re-measuring the 637-row gap);
   (b) drops, each after a `backup_before_drop` receipt: `sreality_count_probe_state`, the
   `location_jobs` alias view + six dead columns + index, `listings_w7_clear_backup`.
7. **Price-stats refresh cadence (A/B):** A: one weekly run (~$1/month; a full pass of all localities
   takes months). B: chain while any active dataset's oldest locality is older than 7 days (~95
   runs/month, ~$30). Or name datasets to deactivate.

## 9. Rejected alternatives

- Flip first, migrate later: ~$650/month during the move and a hard-stop budget that can halt CI mid-month.
- Self-hosted runner pool on Railway as the primary plan (shape D): moves only the machine; GitHub's
  throttled cron stays the scheduler, every YAML and group stays, production secrets and PR code share
  a fleet, and the postponed $0.002/min fee may return. Kept as a CI-only contingency (trigger: CI
  > $150/month for two months).
- Walks as more lanes in the realtime worker (shape A): the worker peaks at 94 % of memory; a thread
  cannot be killed; an OOM takes every latency lane down.
- One Railway cron service per job (shape C): ~30 configurations, no timeout, no heartbeat, undocumented
  behaviour on a mid-run deploy.
- A per-job cutover flag (`batch_worker_jobs`): a routing key is a flag; the lease already gives single
  execution, so overdue-only fallback claims replace it.
- Big-bang cutover; dual execution on both hosts (halves walk speed via the shared rate ledger);
  interval 0 as cutover (0 means stop on both hosts).
- pytest-xdist (new dependency); path-gated or skip-if-green required checks (a second rotting registry
  of what each suite reads); strict up-to-date merges (no merge queue on a user-owned private repo).
- pg_cron for anything beyond pure SQL; moving the DB gate or the canary off GitHub; rewriting the
  schema replay onto native Postgres on hosted runners.

## 10. What the adversarial pass changed

All eight load-bearing claims were refuted as first written and corrected here: the bill was restated
from $97 to $115–190 GitHub+Railway (2-vCPU private runners, price-stats refresh honesty); the walk stop
event now reaches the client's retry loop and the lease releases only after the child exits; resume
uses the one existing slice ledger instead of a second ordering; memory is gated on the aggregate cgroup,
not per child; `runpod_reap` is kept, `compute_image_phash` and `backfill_idnes_brokers` became rulings;
every worker gets its own liveness subject; push protection's loss is stated plainly with a push-time
replacement; the cutover flag was removed in favour of the lease; the ledger lists its temporary items
and the two new `PortalLimits` fields.
