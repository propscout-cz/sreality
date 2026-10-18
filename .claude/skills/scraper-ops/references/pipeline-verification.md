# Pipeline verification harness — full reference

Detail supporting the short summary in the `scraper-ops` skill's "Pipeline verification"
section. Read the skill body first; this is the per-check rationale — what each check
watches, the incident that produced it, and how its thresholds were sized — and the ladder
that turns results into incidents and system alerts. Moved out of the skill body in the W9
measure-plausibility PR (the section had grown to 82 always-loaded lines), and again in wave 1
of the Health dashboard sprint (`docs/design/health-dashboard.md`), which cut it to a summary.
Words: a status is `ok` / `warn` / `fail` / `unknown`, shown as OK / Watch / Problem / Not
measured; a check is an alarm or a gauge; a cell is one verdict inside a check (`GLOSSARY.md`
§ Health).

**Pipeline verification harness** (`scripts/verify_pipeline.py`, migration 274, PR #703; the
registry and the four statuses since migration 619) — every check is declared once in `CHECKS`
(class, host, cadence, budget, label, unit, owner page) and projected into `health_checks` (only by a run
from main: a `workflow_dispatch` from a branch, `GITHUB_REF_NAME` ≠ main, measures and stores but never
projects, so a branch's `CHECKS` cannot delete main's keys; a local run without the variable counts as main); a run
writes one `pipeline_check_results` row per check it ran, carrying `details.message`,
`details.elapsed_ms` and, when the check emits them, `details.cells`. Both GitHub lanes run
only the checks that are DUE: newest row older than `cadence_s` less a slack of min(cadence /
10, 15 min), so a run that comes a few minutes short of a full cadence after the last one does
not skip a whole cadence. The two lanes are identical but for their crons — `llm_health.yml`
hourly, `verify_pipeline.yml` 6-hourly (it also takes a manual `dry_run`) — and both carry
`--exit-nonzero-on-fail`, so a failing alarm on either reds its run and GitHub emails (a gauge
never reds a run); they share one concurrency group (`pipeline-verification`), so they take
turns instead of measuring a check twice, and the daily `sreality_image_template` and weekly
`outbound_url_conformance` probes ride whichever lane finds them due. The URL probe keeps its own 120 s
budget (`PROBE_BUDGET_S` in `scripts/verify_outbound_urls.py`, its `Check.budget_s`): each HEAD waits
min(20 s, time left), URLs it did not reach are `details.unprobed` (with `details.budget_seconds`), never
failures, and none probed is `unknown`. A timeout, a not-run, an
error or a check's own could-not-measure path is `unknown` with its reason in `details`
(`timed_out`, `not_run`, `errored`, `skipped`). One "verified nothing" path still reads `warn`
until wave 6 replaces its check: `ppm2_median_shift`'s "compared NOTHING";
`location_town_coverage`'s "no listings read", `false_delist_share` with no portal at 50 flips
and `worker_lane_stall` with no heartbeat to read are Not measured since wave 1. The lanes
write no system alert: results reach the bell through the ladder below, the notification
system's third producer, `system_health` (`docs/architecture.md` rule #16). Until wave 2 a
`SECURITY DEFINER` dead-man-switch pg_cron function (`emit_verification_stale_alert`, the
migration-136 exception-guarded pattern) rings when the newest result row of any key is older
than 24 h. The harness exists because the pipeline
stalled silently for two days in 2026-07 (Anthropic credit exhaustion, 38k+ failed LLM calls) and
the only alarm was a failing GH Actions cron the operator happened to miss. Among the registered
checks: `llm_errors`, `llm_burn_rate`, `long_open_transaction` (every 6 h, from
migration 437: warns when the oldest `pg_stat_activity.xact_start` passes an hour, because
`refresh_llm_cost_rollups`' 3-hour trailing re-scan only absorbs a late arrival whose
transaction was shorter than that — `called_at` defaults to now() = transaction START; the
repair is `select refresh_llm_cost_rollups('-infinity');`), `db_saturation`, `worker_liveness`,
`dual_write_parity`, `property_maintenance` (last-complete-cycle stamp age + oldest
dirty-queue row — the 2026-08-06 sweep-death/stranded-lease incident; both axes O(1) reads,
never a properties scan) and `broker_resolution_freshness` (**three** axes over
`app_settings.broker_resolution_last_complete`, `broker_resolution_runs.ended_at` and
`dirty_broker_listings` — the 2026-08-12
E2E review found the daily broker sweep truncating on its budget every day while exiting 0)
and `broker_merge_suppression` (migration 401): active `broker_merge_suppressions` rows whose two
identities share one broker — the invariant the suppression rail exists to hold. `fail` on the
first violation, no warn tier. An operator NO (unmerge / dismissing a `contact_bridge_review`
candidate) writes a suppression row keyed on the durable identity pair; the sweep loads the active
set once and it gates BOTH `decide_merges` (the pair reaches neither auto-merge nor review —
including through the oversized-component downgrade) and `_apply_merges` (a whole component that
would newly co-locate a suppressed pair is dropped and logged — the transitive chain the pure layer
cannot see; the set is re-read inside that write transaction so a NO landing mid-sweep still binds).
An explicit operator merge LIFTS the suppressions it *brings together* (never deletes, and never a
pair already co-located — that would erase evidence of a bypass); `POST
/broker-review/suppressions/{id}/lift` is the manual counterpart and `GET
/broker-review/suppressions` the ledger. Per-sweep counts land in
`broker_resolution_runs.suppressed_pairs` (pairs, not merges: the rail blocks before grading) and
the `RESOLVE full merge done … suppressed=N` line. Kill switch: `broker_auto_merge_enabled`=false.
The property sweep axis ages a CYCLE (one run, or two through `app_settings.property_sweep_cursor`
since 2026-10-03); its thresholds (warn 52h, fail 56h) are sized for the two-run cycle, so a dead
sweep is flagged after about two days and the sweep's own RED exit is the faster alarm.
Note the broker sweep axis measures a rotation **lap**, not one run: attribution routinely
spends its whole `--max-seconds` budget, so `resolve_brokers` carries cumulative coverage in
`app_settings.broker_sweep_cursor` (`last_id` / `lap_swept` / `lap_started_at`) and stamps
completion when the lap closes; with no lap closed yet the check ages the OPEN lap, so a
rotation that never gets round reds instead of parking on the missing-stamp warn.
The third axis exists because that lap stamp is written right after attribution, ~17-25 min
BEFORE the tail (the auto-merge step, the three rollups, the matview, candidates,
`_finalize`'s dirty-clear), so a sweep whose tail dies still leaves a minutes-old stamp while
the leaderboard and rollups silently stop: `broker_finished_{warn,fail}_hours` (30/60) age the
last full run that actually reached `ended_at`, tighter than the lap's deliberately wide 52/84
because the tail runs on every sweep. Fail sits between ONE missed night (48h + the ~2.5h
spread in when a run finishes = ~50.5h, deliberately only a warn — the skipped sweep's own red
run already emails) and TWO (~69.5h). A NULL (no finished full run on record) is skipped, not
red — only the missing LAP stamp is the deploy-day warn.
Four **per-m² plausibility** checks joined in W9 of the measure-unification program
(migration 427, view `measure_plausibility_by_source`, one read per run shared by all four;
they are registered at a 6-hour cadence, not hourly — a 12 s
scan of the active corpus for a slow-moving signal). They exist because
`data_quality_by_source` tests 29 fields for `IS NOT NULL` and nothing else, so it was
structurally blind to BOTH defects that program fixed: a plot area sitting in the floor-area
column and a per-m² unit price sitting in `price_czk` are 100% non-NULL. Grain is
(source, category_main, category_type) — a portal alone pools sale flats with monthly rentals,
a category alone pools a broken portal with eight healthy ones. Each has a stock arm and a
trailing-7-day arm and alarms on the worse: the stock arm indicts a defect that has been
standing for months, the fresh arm catches a regression the week it ships instead of waiting
for the corpus to churn. **Every share is a ratio over rows that HAVE the inputs**, so a cell
with nothing to measure scores no arm — and a skipped arm would otherwise be indistinguishable
from a clean one. Two rails close that: `ppm2_measure_coverage` watches the denominator, and a
check that scored NO arm at all reports `unknown` with `value` null and `details.skipped`
("verified NOTHING"), never `ok`. If you ever see all four Not measured at once, the measure's
inputs stopped being written.
`area_vs_usable_divergence` — share of rows carrying BOTH areas that differ by >10% (the view's
material band). Only rows with both: bazos populates `usable_area` on 0 of 10 409 dum rows and
must read silent, not divergent, so the charter's literal `area_m2 IS DISTINCT FROM usable_area`
would have fired on every portal that publishes one field. `pozemek` is skipped by name — under
Option A `area_m2` IS the plot for land. Live pre-backfill: mmreality dum/prodej 99.7% (100% over
the trailing week) against 0.0% on every other portal; the milder realitymix byt convention sits
at 10.5%, which is why warn is 20% and not 5%.
`ppm2_basis_floor_share` — share of priced, area-bearing, basis-decidable rows that
`measure_price_per_m2` NULLs at its per-basis floor (rent < 1 000, sale non-land < 100 000).
An absolute LEVEL, not the "jump" the charter asked for: the unit-price masquerade never jumped,
it has been standing for the life of the portals carrying it. Live: ceskereality komercni/pronajem
20.0%, realitymix 19.0%, remax 11.3%, bazos 6.7% — against idnes and sreality at 0.5% in the same
cell, which is exactly the split between portals with and without a per-area price guard.
`ppm2_measure_coverage` — share of a cell's active rows the measure has NO INPUT for (no price,
or no positive area), skipping cells whose basis is undecidable (there an absent measure is the
specified answer). Rows the floor rejected are NOT counted — they have their inputs and
`ppm2_basis_floor_share` already indicts them. This is the axis that sees what the other three
structurally cannot: live, sreality publishes 27 174 active `pozemek` rows with `area_m2` NULL on
all of them (plot size is in `estate_area`), so those four cells produce no measure at all while
every other axis skips them and reads clean — and `data_quality_by_source` can't see it either,
grouping by (source, field) with no category grain (sreality `area_m2` reads 71.7% populated).
Severity splits by ARM: the stock arm can only **warn** (a standing, sanctioned gap nobody clears
today — warn 0.95, the five live dark cells are 0.995-1.000 and the next cell down is 0.894),
while the 7-day arm **fails** at 0.90 (that share among this week's arrivals is a parser
regression in flight; worst live scored 7d gap is 0.358). It is also the anti-silencing rail: a
portal that started writing NULL `area_m2` would make the divergence and floor axes go QUIET.
`ppm2_median_shift` — each cell's own median area and median Kč/m² against ITSELF a week ago,
read from this check's own `pipeline_check_results.details` row from 6-14 days back (no new table).
Deliberately NOT a cross-portal comparison: measured live, portal medians legitimately differ by up
to 19x on mix (idnes' rural land is 8.5x the peer Kč/m²), so a peer arm cannot separate a bug from
a catalogue. It cannot see a defect older than its baseline — that is what the two direct detectors
are for — but it fires at 3x on any new basis flip, and it will fire when the W2 backfill heals
mmreality (6.96x on area, 7.45x on the measure). Thresholds are sized on the noisiest real weekly
move measurable today, 1.90x. Each median is gated on **its own support in both weeks** — the rows
carrying that value, never `n_active`: 64 cells clear `n_active >= 200` but only 58/56 clear it on
area/Kč-m² support, and bezrealitky pozemek/prodej is 1 643 active rows with NINE areas whose spread
is 17.6x, so gating on cell size would red the tile and ring the bell on two ordinary delistings.
No baseline is `ok` for the first week after deploy and `warn` after,
so a check that has been erroring for a fortnight cannot read as green; a baseline that matched
nothing is `warn` ("compared NOTHING"), not a stable 1.00x; an empty view read (the
`is_platform_admin()` gate failing for the job's role) or an unreadable one (migration 427 not
applied yet) is `unknown` naming the cause, never `ok` and never four red tiles.

The six dedup-specific checks (street/geo debt, eligibility funnel,
merge latency, engine health, merge-precision sample) went with the engine, along with their
`pipeline_check_thresholds` rows.

## Walks, queues, lanes and migrations — rationale moved out of the skill (2026-10-18)

Moved here from the skill body when wave 1 cut its section to a summary.

**`acquisition_lag` + `walk_coverage` (2026-08-27)** close the ingestion blind spot: until then
every scraper health signal compared our data to our own data and rendered as a dot on a page,
so sreality ingested ZERO new listings for nine days without anything leaving the database.
`acquisition_lag` reads the oldest unclaimed never-fetched `listing_detail_queue` row per portal —
deliberately the QUEUE and not `listings.first_seen_at`, because a "no new rows in N hours" check
needs a baseline that the outage itself erodes (nine days of zeros makes zero the expected
value). `walk_coverage` is the only comparison against EXTERNAL truth: collected vs the portal's
advertised total from the latest COMPLETED index run's `by_category`, plus a truncation arm
(categories walked vs that portal's own 7-day best) because a budget-stopped walk leaves no
entry for the categories it never reached and so makes the gap look BETTER. remax and maxima
derive their total as `len(seen)` and mmreality reports none — all three are reported
`verifiable: false` rather than 100%. Its per-category twin, `walk_end_streak`, has its own
section below.

**`text_extraction_lag`** (field capture W7) is `acquisition_lag`'s twin one layer later —
oldest ELIGIBLE-unextracted advert + waiting count per portal, plus a wedge arm (rows eligible,
lane claiming none, or the lane absent from the heartbeat) and an abort arm (the provider
stopped the last pass: `fail` quoting its `fatal`, whatever the backlog's age or `claimed`;
2026-10-02..10 the lane claimed 500 a pass for eight days and wrote nothing) — and it is
computed from the text lane's OWN selector predicate, because the outage it replaces was a lane
and its three monitors disagreeing about who was eligible.

**`worker_lane_stall`** closes the gap `worker_liveness` structurally cannot see — a worker that
is ALIVE with a wedged lane. The realtime worker beat every 30 s for nine hours while its drain
lane completed ONE pass and its images lane completed 486; a pass was recorded only on
COMPLETION, so a hung lane and an idle lane published byte-identical state. The worker now
stamps when a pass BEGINS and the heartbeat resolves it to `in_flight_s`, and `_lane_loop`
bounds every pass with `LANE_PASS_TIMEOUT_SECONDS` (1800) — containment, not a diagnosis: it
stops one hang costing every later pass, and repeated timeouts on one lane are themselves the
diagnosis. Caveat worth knowing: a pass blocked inside `asyncio.to_thread` keeps running after
cancellation (Python cannot kill a thread), so the lane is freed but the thread is not — hence
the pass locks and the watchdog in the skill's worker section.

**`migration_drift`** closes a different silent gap: it probes the live catalog for the objects
the newest 25 migrations declare, so a migration merged but never applied is caught in one tick
instead of the 29 h it took on 2026-08-25 (see the `database` skill). It cannot see a
merged-but-unapplied migration that only redefines existing objects (the migration-438 class,
issue #1855).

**Thresholds.** Every other check's thresholds live in `DEFAULT_THRESHOLDS` beside their rationale,
overridable by `app_settings.pipeline_check_thresholds` until wave 3 deletes that path (every
override already equals its default); `field_fill_matrix` owns none (sized on its cohort's
measured drift).

## `field_fill_matrix` — fill AND validity per (source, field) (field capture W1)

`data_quality_by_source` tests 26 fields for `IS NOT NULL`. That is blind to the two defect
shapes the field-capture program exists to fix: a value that is PRESENT and outside every
canonical option list (13 live `condition` spellings against a filter list of 6, ~14k active rows
unreachable by any Browse filter), and a cell that has read 0.0 % since the parser was written
because it reads a key the portal never emits (remax `balkon`/`lodzie`, ceskereality `vybavení`,
`počet podlaží`). `scraper/field_census.py` owns both SQL statements and both pure reductions, so
the live check and the re-bless can never measure different things.

**The matrix reads the whole active stock; only the census samples.** A census is evidence about a
payload's key space, so the newest 1,000 rows per portal answer it (~1 s). The matrix is compared
against a file blessed weeks earlier, and a SAMPLED cohort cannot be compared with itself: the
newest-1,000 slice is a time window whose category mix rotates with whatever a portal's walk
happened to cover. Measured on the shipped code before this was fixed — mmreality's window went
from a balanced mix to 73 % commercial rentals in two days, moving `cellar` 40.2 % → 8.2 % and
`disposition` 41.4 % → 6.3 % with no parser touched, twice over the fail tier. So the matrix is one
aggregate pass over every `is_active` row: measured 12.2 s for all nine portals, inside the 45 s
per-check budget, and registered late in `CHECKS` (only `text_extraction_lag`, `floor_convention`
and the two outbound probes follow it) so the cheap checks are never
the ones that go unrun. It never expands a row into (field, value) pairs — that form costs 32 s
over the stock; per-value counts come from `count(*) filter (where col = '<canonical value>')`
interpolated from `toolkit/filter_registry.COLUMN_CANONICAL_VALUES`, keyed by COLUMN — which is also
where the statutory `energy_rating` 'G' share per portal comes from. 'G' stays an ordinary grade: W5
measured all nine censuses and no portal marks it as the unassessed placeholder, so R11's `unassessed`
member is struck. A field with no canon is counted, not enumerated — W5 gives `price_unit` one.

**The arms are RELATIVE to a blessed baseline** (`data/field_capture/fill_baseline.json`), never
to an absolute floor, because a floor cannot see a legitimately-zero cell or a partial break
(ceskereality's `furnished` key mismatch would sit at ~2.5 %, not 0 %). Sizing is the MEASURED
drift of that cohort, never a binomial SE (the stock is the population, not a draw): comparing
each cell's fill with the same fill over the rows already active a week earlier, across 90 cells
on 2026-09-21, the worst moved 4.1 pp in a WEEK (bezrealitky `disposition` 55.0 → 50.9) and the
median under 1 pp — a 6-hourly run sees ~0.15 pp of that. A fill drop ≥ 10 pp warns and ≥ 20 pp
fails, both only where the baseline was ≥ 10 % filled; below that the collapse arm binds instead —
a cell with 50+ blessed filled rows keeping under a quarter of them fails. An off-canon share
rising ≥ 5 pp warns, ≥ 15 pp fails. A cell that drifts past a threshold honestly is a re-bless,
and the message says so. The check carries NO `pipeline_check_thresholds` entries: the numbers
belong to the cohort, not to a taste, and the program adds no settings.

**A blessed cell that stops being measured is an offender.** The spine comes from
`portals.is_enabled` and from a source having active rows, so a portal disabled during an incident
would otherwise take its 26 cells out of the denominator and leave the check certifying the
silence — `ppm2_measure_coverage`'s lesson, applied here as: a whole source missing FAILS, a single
missing cell warns, and the message carries `cells_measured` of `cells_blessed`.

**Known, not judged.** The baseline records today's zeros, so the check is green on day one and
still names them every run (`details.zero_fill_cells`) until W2's attribute contract declares a
producer for each. That list is computed from the LIVE matrix, so the run that repairs a cell is
the run that stops naming it. Boolean cells never written `false` are reported the same way
(`details.booleans_never_false`) — whether silence means `false` or `unknown` is an absence
semantics the contract declares, so there is nothing yet to be right or wrong against.

**Who owns which field**, so the two fill instruments can never be read against each other:
`data_quality_by_source` keeps the seven probes with no attribute column (`geom`, `locality`,
`street`, `property_grouped`, `source_url`, the two condition levels), `field_fill_matrix` owns
the 26 `LISTING_COLUMNS` attributes, and the 19 they share are the view's to lose in a later wave.
Both now read every active row, so a shared cell cannot report two different numbers.

**The census half** is checked in per portal under `data/field_capture/census/` and re-blessed
with `python -m scraper.field_census --bless` — a reviewed diff, like the location contract's
golden. A stale census is the known blind spot (a portal renames a key and every gate built on the
census still agrees with itself), so staleness > 30 d is this check's last arm, a WARN. It is
deliberately NOT a pytest: a test keyed on the calendar reds `main` on a date, on a branch that
touched nothing.

## `floor_convention` — is a plausible integer on the RIGHT SCALE? (field capture W8)

The check no fill or validity measure can stand in for. `listings.floor` was a ~50/50 mix of two Czech
conventions until W8 — ground = 0 on idnes, bazos and ceskereality, ground = 1 on the other six — and
nothing in the repo could see it, because a storey one too high is perfectly typed, perfectly non-NULL
and perfectly plausible. There was **no floor check of any kind** before this one.

**The measure needs no labels and no LLM.** Two active `byt` adverts agreeing on price, area AND
disposition are the same flat often enough that the MEAN of their floor difference is a clean
convention signal; the check reports `mean(portal floor − idnes floor)` per portal. idnes is the
reference because its parser has always read the Czech word ("2. patro (3. NP)"). A key counts only
where its side holds ONE storey for it (`min(floor) = max(floor)`), so a repeated
price/area/disposition triple cannot smear the difference.

**Tiers: warn 0.35, fail 0.50, min 40 pairs.** Fail at half a storey is unreachable by noise and
reachable only by a portal on the other scale. The measure has a real noise floor — a sibling match is
not a proven duplicate — sized live at +0.10 on bazos. warn is 0.35 rather than 0.25 for ONE portal:
ceskereality reads +0.20 and only ~0.03 of that is sample conditioning (restricting the idnes side to
`floor >= 1` moves it +0.199 → +0.167); the rest is 925 pairs at exactly +1 against 92 below 0, an
unexplained one-storey sub-population tracked in `docs/design/field-capture/handover-autodedup-floor.md`
§ 6. Every converted portal lands inside 0.25 in simulation, so the wider tier protects that portal
alone and does not certify it. `min_pairs` 40 lets maxima score on its ~49 pairs and still refuses a
mean drawn from one or two adverts; a portal below the floor is reported but not scored, and if NO
portal is scored the check returns `unknown` with `value` None and `details.skipped` — "verified
nothing" is not a green.

**It is the most expensive check in the lane, and the most variable** — 27.0 / 35.1 / 11.0 / 16.1 s on
four `EXPLAIN (ANALYZE)` runs over ~34.7k pairs, against `field_fill_matrix`'s steady ~12 s — because
the plan is a BitmapAnd feeding a Bitmap Heap Scan that spills (~116k buffers, ~85 % of them `read` even
on a warm cluster, so it never stays cached and its cost tracks concurrent readers). Hence: registered
LAST among the DB checks, and read through `_fetchall` so it inherits the per-check
`SET LOCAL statement_timeout`. Cancelled, it reports `unknown` (`details.timed_out`), never a false green.
A new check above ~20 s starves the tail and needs its query reworked, not a wider `_CHECK_BUDGET_S`.

**It reads RED from the day it shipped** — the one check in the lane that does, deliberately. The six
ground = 1 portals read +0.82..+1.05 until the heal has re-derived them
(`python -m scripts.reparse --source <portal> --fields floor --write --allow-snapshot-deferral`, six
passes, runbook in the hand-over § 7). The failure message names those commands, and names them ONLY
for a portal whose contract cell declares `ground1`: telling the operator to re-derive ceskereality
(cell `ground0`) would shift 34,363 rows down one storey, most of them correct.
Since wave 1 it is a gauge (Q2): its standing red sends one system alert, then one reminder a day
until it is acknowledged on Health; wave 6 deletes the check (Q4).

## The `scrape_runs` crash contract (`portal_runner.run_phase`, W0.2)

Both scraper health arms read one row, so what that row records on a bad ending is the whole
question. `run_phase` is the single seam (rule #21): it opens the row (`scrape_run_start`, with
`source` taken off the portal, not a per-module constant), runs the phase, and records **how it
ended**.

- **`ended_at` means the phase COMPLETED.** Only the return path calls `scrape_run_finalize`.
- A phase that raises bumps `scrape_runs.errors` by 1 via `bump_scrape_run_counts` — *additive*,
  on top of whatever the drain already committed per chunk, because a crash is one more error
  event and not a replacement aggregate — and deliberately leaves `ended_at` NULL before
  re-raising. A crashed run therefore lights up **both** arms of `scraper_health_checks()`:
  `stuck` (which keys on `ended_at is null`) and `err_pct` (which keys on `errors`).
- It catches `BaseException`, not `Exception`: a SIGINT or `SystemExit` out of a phase leaves the
  run just as unfinished as a `CheckViolation`, and "did not finish" must never read as green.
- Crash recording is best-effort — a failing bump can never mask the original exception.

Reading a finished row:

| `ended_at` | `errors` | meaning |
| --- | --- | --- |
| set | 0 | clean run |
| set | > 0 | finished, with N failures — the honest count |
| NULL | > 0 | **crashed** |
| NULL | 0 | SIGKILLed, or still running |

Before W0.2 this lifecycle was copy-pasted into nine `*_main.py` files and finalized from a bare
`finally`, so a crash still landed `_finalize(run_id, {}, drain=True)` — and finalize under
`bump_already_applied` never writes `errors` at all. A hard crash thus recorded `ended_at` set and
`errors = 0`, indistinguishable from a clean run and invisible to both arms; the index lane's
mirror-image bug (`_finalize` returned early on an empty agg) wrote nothing at all. That is why the
six portals that fell over on 2026-08-26 showed no error count anywhere
(`docs/design/reliability-program.md`). Never re-add a per-portal copy.

## The three self-rules (W0 of the reliability program, 2026-08-27)

`docs/design/reliability-program.md` W0.1/W0.3/W0.4. All three came out of a single finding:
**the health system was not silent during the outages it was built for — it was loudly wrong.**

**1. Silence is not recovery.** `llm_errors` derives `currently_failing` purely from state:
`last_ok_at < last_err_at`. It used to additionally `and` in a 90-minute staleness window
(`min_live_at`), on the theory that a lone old error with no traffic since is not a live
outage. That is backwards. The producers here have circuit breakers — `toolkit/vision_batch.py`
stops the labelling pass on the first fatal provider error, and the autodedup judge lane aborts
its pass the same way — so once an outage is *total* the traffic stops, the
last error ages out of the window, and the check reads `ok`. Measured: OpenAI was
credit-exhausted for 11 days (63,547 error rows, **zero** successful calls) and the check read
`ok` for most of it, flipping `fail` at 14:02 and `ok` at 14:58 on unchanged inputs. Because
alerting is edge-triggered, that produced **114 alerts alternating onset with a literal
"✓ Recovered: llm_errors is healthy again"** for an outage that never recovered. Any
recency-window detector downstream of a circuit breaker is sampling a duty cycle, not a state.
Generalise it: a failure is superseded only by a newer success — of the SAME account. Credit
(`credit_live`) is judged per provider: the text lane calls OpenAI up to every 5 minutes, so
one comparison across providers would let it clear an empty Anthropic account within minutes.
The alert names the provider and the models and jobs that failed: through OpenAI's
2026-10-02..10 credit outage it said every paid LLM path was down and named no provider,
Anthropic-backed jobs included. The rate arm's `currently_failing` still reads the newest call
of any provider. The worker side of the same incident: a text-lane pass the provider stopped
was booked as a success (`failed_passes: 0`) — it is now a failed pass, and
`text_extraction_lag` fails on its `fatal` whatever `claimed` says (it had blamed backlog age).
The same rule, broken once more and closed in wave 1 of the Health dashboard sprint: a timed-out
check was `warn`, which `emit_transition_alerts` read as not failing, so 6 of
`text_extraction_lag`'s 7 timeouts in that outage closed its incident with a false "✓ Recovered"
(each came 6.4-9.6 h after the last `fail`, past the 6 h flap cooldown). A timeout is now
`unknown`, which the ladder never reads as a recovery.

The symmetric pathology from the same edge-triggered rule: `property_maintenance` was `fail`
continuously from 2026-08-20 13:08 UTC with its last alert at 11:37 UTC — six days red, six
days silent. One rule produces both — and one fix retires both: the reminder ladder in
`toolkit/system_alerts` (W3.4's `emit_transition_alerts`, replaced in wave 1 by `reconcile`; see
"The ladder" below), where all checks inherit it, never in any one check.

**2. A zero is ambiguous — name the arm.** `llm_burn_rate` had only upper arms ($90 warn /
$150 fail), and `_record_failure` writes `cost_usd=0.0`, so a **total outage drives 24h spend
to the maximally healthy number**: it reported `ok value=0.0` throughout the 11-day outage. It
now carries `details.arm`:
- `starved` → `fail`: a `called_for` lane with `attempts > 0 AND successes == 0 AND spend == 0`.
- `idle` → `ok`: nothing attempted at all. No check judges silence (see §4).
- `runaway`/`ok`: the pre-existing spend arms.

**Evaluated per `called_for`, and that is load-bearing.** A 24h aggregate arm is defeated by a
single unrelated cheap success — verified: one `summarize_region_dispositions` call held the
aggregate at $0.01 for ~24 of 30 sampled hours while the only recurring lane was completely
dead. The upper arms are separately **parked as currently unreachable** (they were sized for
dedup-vision burn deleted 2026-08-06; 24h spend is ~$0.01), recorded rather than silently left
as arms that cannot fire.

**3. The acute lane must degrade, not vanish.** `run_checks` computed *all* results before
`write_results` persisted *any*, inside `llm_health.yml`'s `timeout-minutes: 5`. A timeout
therefore wrote **zero rows and fired zero alerts** — blinding `db_saturation` and
`worker_liveness` at precisely the moment DB saturation would make checks slow. Now each result
is inserted the instant its check returns, and since wave 1 the lanes call no alert function at
all: the API's ladder reads the stored rows. Budgets: `_CHECK_BUDGET_S` (45 s) per check
(`Check.budget_s`; 120 s for the two outbound probes), capped by whatever remains of the LANE
budget — one per-check budget per DUE check; the summed budgets are proven to fit the 30-minute
job by `test_every_lane_budget_fits_its_job` (both lanes run 30-minute jobs since wave 1). The
full lane shared a 120 s budget until 2026-09-22, and in 13 of the 15 runs before that 7-17 of
its 25 checks were `not_run`.
Enforcement is **server-side** via `SET LOCAL statement_timeout`: the connection is autocommit
and shared by every check, so a thread we cannot cancel or a signal raised mid-query would
leave it wedged for everyone downstream. Postgres cancelling its own query is the only
mechanism that returns the connection clean. An overrun is `unknown` "timed out"
(`details.timed_out`), an unreached check `unknown` "not run" (`details.not_run`), and a check
that raised `unknown` with `details.errored` — "I could not measure this" is a different claim
from "this is broken", and fail-on-timeout would manufacture a wall of false reds exactly when
the operator needs to read the real one. Until wave 1 the first two were stored as `warn`
(which the old ladder read as "not failing", rule 1) and a raising check as `fail`.

**`workflow_poller_liveness`** (W0.1) closes the matching blind spot on the other ops surface.
`record_workflow_failures.py` deliberately excludes its own runs from `workflow_failures`, so a
dead poller cannot appear in the table it feeds — it simply stops accumulating rows, which is
byte-identical to a quiet week. The check keys on the age of
`app_settings.workflow_failures_cursor`: warn > 6h, fail > 12h, sized to clear the worst
observed inter-poll gap (the cron is `*/30` but the Actions throttle really runs it 80–256 min
apart). A missing cursor row is `warn`, not `fail` — it is also the legitimate first-run state,
and a check that is red the day it ships teaches the operator to ignore every check. Registered
at a 6-hour cadence (wave 1 replaced the lanes' `--only` lists with cadences).

Same wave, on the poller itself: when the page cap was hit, the cursor advanced to
`min(completions)`. Pages arrive newest-first, so hitting the cap means every run seen is
*newer* than `since` and the skipped runs are older than all of them — the next poll's
`completed_at < since` filter then dropped them **permanently**, which is why only 2 of the 6
portals that failed on 2026-08-26 were ever recorded. The in-code comment claiming it "crawls
to oldest-seen so the gap is picked up next poll" was false. The cursor now advances *only* on
a poll that reached back past `since`, and the page budget doubled to 10 pages / 1,000 runs
(≤10 Actions API requests per poll against a 1,000/hour per-repo budget).

**4. A threshold outlives the workload it was sized for** (W0.5, `llm_silence_fail_hours`
4h → 13h). `check_llm_liveness` documented its own premise: "the platform runs paid LLM
traffic continuously (dedup vision on the always-on worker) … p99 inter-call gap is ~1 min,
so the 4h default never trips in normal operation." That premise died on **2026-08-06**, when
the dedup decision engine was removed wholesale (rule 15) and took the continuous vision
traffic with it. The threshold stayed. The only recurring LLM producer left THEN was bazos
description enrichment (`enrich_bazos.yml`, itself deleted a month later — see below) —
condition scoring paused and every other LLM workflow dispatch-only — so the healthy
inter-call gap stopped being a minute and became a
cron period stretched by the Actions throttle (observed run-to-run gaps of 2.4–15.0 h over
Aug 27-30). The check has **no warn tier**, so every overshoot is a hard red: it fired
`fail value=4.195` and reddened "Monitoring: acute health (hourly)" **8 times between Aug 27
and Aug 30** while the pipeline was demonstrably healthy — 595 successful calls and 0 errors
in the trailing 24 h, $1.53 spent, last success minutes earlier. 13 h is 2× the 6 h nominal
cadence plus throttle slack, still inside one 6 h lane tick of a genuinely dead pipeline.

The transferable rule: **a threshold is a claim about a workload, so deleting the workload
invalidates the threshold.** When a producer is retired, grep the health harness for the
numbers that were sized on it — `DEFAULT_THRESHOLDS` carries its own rationale per key for
exactly this reason.

**And the second time, the check went with it.** Field-capture W0 (2026-09-21) deleted the
enrichment lane itself, leaving NO recurring LLM producer: everything left (autodedup judging,
labelling, estimations, URL parsing) is dispatch-driven or on demand. Measured over the 30 days
to 2026-09-21 the longest gap between `llm_calls` rows excluding that lane was **143.8 h**, and
`llm_liveness` had already logged 52 fails in 349 runs against a healthy pipeline. Re-sizing
13 h → 144 h would have bought a check that only speaks after six days of normal weekend quiet,
so `check_llm_liveness`, `_status_for_llm_silence` and `llm_silence_fail_hours` were all
deleted. The replacement is not a global recency threshold at all: field-capture R8 makes each
lane's health check read the lane's OWN eligibility predicate (oldest eligible-unextracted age
per source), which needs no historical baseline — the same shape as `check_acquisition_lag`.
Until W7 ships it, `llm_errors` still catches "calls are failing" (from state, not recency) and
`llm_burn_rate`'s starvation arm still catches "attempting and never succeeding".

---

## `ops_incidents` — the failure-signature producer (W3, migration 462)

**Fenced from cell incidents since wave 1 of the Health dashboard sprint (both of its
auto-resolve statements skip `signature LIKE 'cell:%'`, and the poller ignores `check:`
signatures: a failing check is the ladder's to tell), and retiring in wave 3.**

The verification harness measures the 29 registered checks and rings nothing itself; the ladder
below reads their results. Neither says anything about the ~40 workflow failures/day that never
touch a check: portal crashes, CI, backfills, LLM lanes. That is the seam the 2026-08-26 outage
fell into — the surface that could alert had nothing to say about ingest, and
`workflow_failures` (the surface that knew ingest was red) stores no failure reason of any
kind. W3 closes it WITHOUT a second bell: `ops_incidents` emits an ordinary `system_health`
`notification_dispatches` row and the shipped outbox delivers it.

**The key.** `scripts/failure_signature.py` (pure stdlib, no DB) normalizes an error into a
signature derived from the **error text only, never from `workflow_path`** — that asymmetry is
the whole mechanism, and it is why six portals failing on one `CheckViolation` are one row:

```
checkviolation|new row for relation listings violates check constraint listings_area_basis_check
check:property_maintenance|fail          # verify_pipeline's own CHECK line, one key per check (ignored by the poller since wave 1)
aborting|consecutive errors provider outage   # scripts that catch their own error and exit 1
step:run tests|exit:timed_out@.github/workflows/test.yml   # unreadable red — the ONE scoped key
```

Normalizer rails, each of which was a bug on the first pass: quoted identifiers survive the
digit-strip (the constraint name IS the key — a placeholder containing digits destroys it);
3-digit HTTP codes survive it too (else a 403 and a 500 collapse into `httperror|from`); only
line 1 of a psycopg message is read (line 2 is `DETAIL: Failing row contains (…)` — a whole
listing row); the exception class is matched by the dotted-module + CapWord grammar, never by
an `*Error` suffix allowlist (`CheckViolation`, `QueryCanceled`, `AdminShutdown`,
`AmbiguousFunction` and `InsufficientPrivilege` all fail that test).

**Two producers, one function.** `portal_runner.record_failure_signature(conn, exc, source=,
lane=)` is the single seam (rule 21):
- `portal_runner._record_run_crash` — the in-process chokepoint. The exception is in hand, so
  the signature is correct at t+0 with zero Actions-API cost. Writes on the SAME connection as
  the `scrape_runs.errors` bump, inside the existing best-effort try/except: a bookkeeping
  write must never mask the exception that got us here.
- `realtime_worker._record_lane_failure` — the probe and drain lanes call `portal_runner`
  directly (`run_id=None`, deliberately) and so bypass `run_phase` entirely. Before W3 a
  `CheckViolation` on the latency-critical drain produced a log line and nothing else, forever.
  **Always `await asyncio.to_thread(...)` it**, like every other DB touch in that file:
  `db.connect()` sleeps up to ~20s per source on a transient failure, and a bare call would
  stall the heartbeat lane during exactly the DB incident it exists to record.
- `record_workflow_failures.record_incidents` — the backstop, and the MAJORITY path (portal
  workflows are only ~22% of the failure corpus). For each NEWLY inserted failed run it reads
  the failed job's log and extracts the terminal error. Two budgets, both real:
  `MAX_LOG_FETCHES` per poll, and `INCIDENT_PASS_BUDGET_S` of wall clock — a fetch is up to 4
  round trips at `API_TIMEOUT_S`, so the fetch cap alone is a ~50-minute worst case inside a
  job with `timeout-minutes: 5`. The pass runs **after** `_write_cursor`, because a job kill is
  not an exception and the cursor advance is the input-coverage guarantee W3 rests on.

**One run is one failure (`ops_incident_runs`, migration 463).** Both producers see the same
Actions run — the chokepoint at t+0, the poller 80–256 minutes later — and the upsert bumps
`failure_count` unconditionally, so before 463 every portal crash counted **twice** and a LONE
crash crossed the measured onset threshold on its own. `claim_run(conn, run_id)` inserts the
run id and returns whether this caller won it; the poller claims **before** downloading, so a
run the chokepoint already recorded costs zero fetches too. That claim replaced an earlier
workflow-keyed signature-reuse map, which had no run correlation and folded an unrelated new
red (a timeout, say) into whatever incident last touched that workflow within the hour — the
real reason was then never derived and could never alert. `run_id=None` (the Railway worker)
always claims: it has no second observer. The ledger self-prunes at 30 days inside
`auto_resolve`.

**The log fetch has three hazards, all live-verified — do not simplify it.**
`/actions/jobs/{id}/logs` answers **302** to a SAS-signed Azure blob URL, and CPython's
`HTTPRedirectHandler` copies every header (`Authorization: Bearer` included) onto the redirect:
that leaks `GITHUB_TOKEN` to a third-party host *and* returns 401. So redirects are disabled and
the `Location` is re-requested **bare**. Azure **ignores suffix ranges** (`Range: bytes=-500`
came back 200 with the whole 27 KB body), so the tail is two requests: `bytes=0-0` to read the
length off `Content-Range`, then a real closed range. And the signed URL can 404 `BlobNotFound`
(retention, or logs not yet flushed) — that is a degraded incident, never a poller failure.
Never `tail` a log either: the last ~25 lines are always runner cleanup, so the extractor
anchors on the error and walks backwards.

**Alerting and closing.** Onset fires on `failure_count >= ops_incident_min_failures` (2) **or**
the signature spanning 2 distinct workflows, whichever first — the breadth arm matters because
the 2026-08-26 signature reached its 2nd workflow 8 minutes after onset while a same-workflow
2nd failure can be 164 minutes away under the Actions throttle. Exactly one dispatch per
incident (`sys:ops_incident:{id}:onset`), claimed with a conditional UPDATE before emitting so
two producers racing cannot both alert — **claim and emit share one explicit
`conn.transaction()`**, because every caller here is autocommit and a claim that committed
alone before a failed emit would set `alerted_at` with no dispatch, permanently silencing that
incident's only onset. Closing: every member workflow posting a newer
`workflow_run_health.last_success_at` (primary), `ops_incident_max_age_hours` (168 — the
backstop for retired/disabled/renamed workflows and for worker-origin incidents that have no
member workflow at all), or `toolkit.ops_incidents.resolve_incident` (manual; there is no admin
route yet). All three thresholds are scalars in `app_settings.pipeline_check_thresholds` —
`load_thresholds` merges only int/float out of that blob, so an array key would be silently
dropped and the code default would win forever.

**External delivery is still off.** `system_health_channels` is `[]`, so incidents ring the
in-app bell only. The flip is two `app_settings` rows plus a transport secret on the API
service — see the `toolkit-api` skill for `RESEND_API_KEY` / `TELEGRAM_BOT_TOKEN`;
`api/main.py` starts `outbox_loop` only when a transport `is_configured()`.

## The ladder — one incident per failing cell, told by the API (wave 1, 2026-10-18)

Wave 1 of the Health dashboard sprint (`docs/design/health-dashboard.md` § 3 and § 6) replaced
W3.4's escalation ladder. That ladder re-derived every incident from 30 days of
`pipeline_check_results` on every lane run (`check_states`, `_collapse`,
`_resolve_truncated_onset`, `AlertPolicy`) and read anything but `fail` as healthy once a 6 h flap
cooldown had passed — shorter than the lanes' real gap, so in 30 days 34 of 88 "✓ Recovered"
alerts were false and 70 onsets followed an unmeasured row — and its anchors drifted as rows aged
out of the window. It, its `alert_*` threshold keys, the day-keyed alerts and the weekly heartbeat
are gone; the GitHub lanes only measure.

**Where it runs.** `toolkit/system_alerts.reconcile(conn)` is called every 60 s by the API's
system-alerts loop (`api/system_alerts_loop.py`; `SYSTEM_ALERTS_INTERVAL_SECONDS = 60`, a
constant), started in `api/main.py`'s lifespan beside the watchdog matcher and off under the same
`NOTIFICATIONS_MATCHER_DISABLED` guard: telling lives in the process that serves the bell, whose
outbox delivers the rows once a transport is configured. A pass is one transaction under
`pg_try_advisory_xact_lock(hashtext('health_reconcile'))` — a second caller returns zeros and
writes nothing — and returns `{opened, reminded, recovered, closed_silently, notices}`, logged
when any is non-zero. Its values are module constants in `toolkit/system_alerts`, spelled once
(`ALARM_RUNGS_H`, `ALARM_WEEKLY_H`, `GAUGE_RUNG_H`, `RECOVERY_HOLD_MIN_S`,
`UNMEASURED_CLOSE_DAYS`, `MESSAGE_MAX_CHARS`); no setting overrides them.

**What it reads.** The registry (`health_checks`: key, class, cadence, staleness budget, label,
unit), the newest `pipeline_check_results` row per registered key written since the key's
`declared_at` (the old harness stored a check's own error as `fail`, so a key is Not measured
until the new harness measures it; `pipeline_checks_public` applies the same guard, so the page and the
ladder agree), and the open cell incidents. A result's cells are
`details.cells` when that is a non-empty list, otherwise one implicit cell whose subject is the
check key — every wave-1 check, since explicit cells arrive with the waves that rebuild each
check (wave 6 deletes the fallback). A result whose status is `unknown` is ignored whole, and so
is a result older than its key's staleness budget: it is Not measured for the ladder too, as it
is for the page's `effective_status`. A cell whose status is not `ok`, `warn` or `fail` is
ignored.

**One incident per failing cell.** One `ops_incidents` row per cell, signature
`cell:<key>:<subject>` — a prefix the signature engine never produces, and the open-signature
unique index allows one open row per cell. A measured `fail` opens it (`first_seen_at` =
`last_seen_at` = the result's `run_at`) or, when it is open, advances `last_seen_at` and clears
`ok_since`. A `warn` keeps an open incident open (clears `ok_since`) and opens nothing. A
measured `ok` starts `ok_since` at the first OK, and the incident resolves
(`resolve_reason = 'measured ok'`) only on an OK measured across the hold,
`max(2 × cadence, 30 min)`: the newest OK's `run_at` must be at least the hold after the first
OK; one lone OK never recovers. Two closes send nothing: an incident whose key left the registry
(`retired`), and a cell unmeasured for max(7 d, 2 × cadence) — its `last_seen_at` older than that
and its cell in no measured row inside it — which closes silently (`unmeasured 7d`; two weeks for
the weekly URL probe). Not measured never opens, recovers or ages an incident.

**What it tells.** Each notice is one `notification_dispatches` row written by
`emit_system_alert(conn, check_key, message, *, dedupe_key)`, which stamps `target_channels` from
`system_health_channels` (`[]` today: the bell only, Q1). Per check and pass at most one notice
of each kind, grouped across that check's cells. The reminder and recovery keys also carry the
incidents they tell (`{lowest id}x{count}`), so two groups of one check told in different passes
never share a key; an onset is keyed by its result's `run_at`, because one result opens all its
cells in one pass:

| kind | when | dedupe key |
| --- | --- | --- |
| onset | incidents opened this pass | `sys:{key}:onset:{run_at}` |
| reminder | open, not acknowledged, not recovering (`ok_since` null), a rung due | `sys:{key}:reminder:{rung}:{oldest first_seen_at}:{lowest id}x{count}` |
| recovery | incidents a measured OK closed this pass | `sys:{key}:recovery:{oldest first_seen_at}:{lowest id}x{count}` |

Rungs count from `first_seen_at`: an alarm reminds at 6 h, 24 h and 72 h, then weekly (rungs
`6h`, `24h`, `72h`, `w…`); a gauge every 24 h (`d…`), the operator's Q2 ruling: "ring when first
read, then once a day". A rung is due when `alerted_at` predates `first_seen_at + rung`, and only
the highest due rung fires, never a backlog. Messages are plain text whose first line names the
check and its state: onset `<label>: Problem — <subject>: <cell message>; …` (at most 1,500
characters), reminder `<label>: still Problem|Watch for N h — <subjects>`, recovery
`✓ <label>: <subjects> recovered after N h`. An onset sets `alert_count = 1, alerted_at = now()`;
a reminder adds one to `alert_count` and moves `alerted_at`.

**Read is not acknowledged.** Reading a notice in the bell sets
`notification_dispatches.seen_at`, which platform admins can do on SYSTEM rows since migration
619; it never acknowledges. The Acknowledge button on Health (wave 1 ships a minimal one on the
pipeline-checks card; the rebuilt page comes in wave 8) calls
`POST /admin/health/acknowledge {"key": …}`, which sets `ops_incidents.acknowledged_at` on that
key's open cell incidents, and only that stops their reminders; the recovery is still told.

**Idempotent by construction.** The loop re-reads the same newest rows every minute, so each step
is a no-op on repetition: `ON CONFLICT` on the open signature and on `dedupe_key`, `coalesce` on
`ok_since`, the `alerted_at` rung guard. `tests/toolkit/test_system_alerts.py` pins a second pass
over one state emitting nothing.

**Replay rail.** `tests/toolkit/test_system_alerts_replay.py` feeds 14 days of real results
(run-length-encoded aggregates in `tests/fixtures/health_replay_14d.csv`, no ids or URLs: the repo
is public) through `reconcile`, and asserts no recovery while a key's newest row is `unknown`,
one incident per maximal fail streak, and one onset plus its rungs, nothing else, for a key that
fails the whole window.

**Cutover.** After the deploy the ladder sends one grouped onset per currently failing check,
alarm or gauge (today three gauges: `location_town_coverage`, `ppm2_basis_floor_share`,
`text_extraction_lag`), once, each on its first pass after the new harness has measured it. The
861 old system alerts can now be cleared with "Mark all read".

**Dead-man switch, until wave 2.** Migration 274's `emit_verification_stale_alert` (pg_cron,
hourly) rings when the newest result row of any key is older than 24 h, under its own
`sys:verification_stale:{YYYY-MM-DD}` keys, a namespace the ladder never writes. Wave 2 replaces
it with `health_liveness()`, and the GitHub canary's delivery check replaces the deleted weekly
heartbeat as proof that alerts arrive.

## `location_payload_shape_drift` — can the location contracts still read new payloads?

`location_payload_shape_drift` — W4's standing payload-schema-version check (06 §6.4 W4 gate,
P6). Per source, the share of ACTIVE rows **first seen in the trailing 48 h** (`…_window_hours`) whose payload the
location contracts cannot read: sreality's `locality` object not post-cutover shape (the same
exhaustive SQL `CASE` at `scripts.verify_pipeline.SREALITY_SHAPE_CASE_SQL`, parity-tested against
`claims_common.sreality_payload_shape`), bezrealitky's `ruianId` KEY absent.
Why the fresh arm only, and why 48 h: a new row always gets a detail fetch within the hour, so after a
source-side cutover the share climbs as elapsed/window — one 6-hourly tick into 48 h reads 12.5% and
crosses fail; the same tick into a 7-day window reads 3.6% and the bell would wait ~1.4 days. Why it
exists: an unknown sreality shape classifies `absent` and the claim lane simply counts it — nobody is
told (the refetch cohort that used to chase it went with rule 25's one-lane fold). warn 3% / fail 10% / min 30 rows
(`location_payload_shape_drift_*` thresholds); `unknown` with null value and `details.skipped` when
no source reached min rows;
the message names the remedy per source (sreality: update `sreality_payload_shape` in
`claims_intake.py`, re-extract — the contract's `payload_schema_detector` block went with W1-c's
six-key contract shape; bezrealitky: restore `ruianId` in `_DETAIL_QUERY`, then refetch
the rows fetched meanwhile). Measured every 6 h (its registry cadence); an alarm.

## `sreality_image_template` — does sreality's CDN still serve our template?

`sreality_image_template` — one of the **two checks in the harness that leave the database** (the
weekly `outbound_url_conformance` probe is the other), and the reason it has to: the database
checks read a table we wrote, and a CDN that started refusing our transform writes nothing, so
there is no row to read. Sreality's image CDN is an
exact-template **allowlist**, not a transform language — the download path ships
`scraper/image_storage.IMAGE_TRANSFORM_OPS` (`res,1800,1800,1|shr,,20|jpg,80`, their
SQUARE_1800_JPG whole-frame template) and anything off the list 400s. Sreality re-cut that
catalogue once before with no notice; `_classify_image_failure` parks a 400 **terminally**
(`source_unavailable`, never retried), so a catalogue change would park every image in flight
and surface only as a download lane that quietly stopped storing bytes.
The probe, three calls behind one seam: GET the live v1 search (`INDEX_URL`, byt/prodej/CZ,
`limit=1`) with the client's browser headers for a FRESH `hash_id` (never a stored URL — that
proves only that the template worked the day we stored it), GET that listing's `DETAIL_URL`,
and take `advert_images[0]` through `parse_images`. **The detail endpoint, not the search
one**: v1 search returns `advert_images` as bare URL **strings** (`parse_images` skips every
non-dict, so reading images off the index returns `[]` and the probe would short-circuit to a
warn forever); only detail carries the `{url, width, height, order}` dicts — the same payload
the real download path consumes. Then request `image_storage.with_transform(url)` — the
**deployed** chain by construction, never a literal copy, so the probe cannot drift from the
code it guards — and measure the bytes with `image_storage.image_dimensions`. The seam,
`_fetch_sreality_probe(url, timeout, max_bytes)`, streams under two bounds: a 6 MB cap on
MEMORY and a monotonic wall-clock deadline of `connect + read` (`timeout=(5, 8)`) per call,
because requests' read timeout is per socket read — a peer trickling one chunk per 7.9 s would
otherwise hold the lane for minutes, and nothing outside can preempt it (the lane budget is
armed only as a Postgres `statement_timeout`, and `run_checks` tests the lane deadline only
BEFORE a check starts). Worst case 3 × 13 s = 39 s, inside its 120 s budget (`Check.budget_s`).
`fail` on CDN non-200 (the allowlist rejected us), a non-image or undecodable body, or a served
frame under `sreality_image_template_min_ratio` (0.9) of what the deployed `res` op should
yield **for that estate's own declared source size**, on EITHER axis. The size arm is the one
that matters — the silent downgrade answers **HTTP 200** with a smaller or cropped rendition,
which no status check would ever see, and the superseded `res,749,562,3` chain (the regression
the master-template work undid) served 749 px and cropped 3:2 to 4:3, which is why both axes
are compared. The verdict is RELATIVE, never an absolute px floor: `res,1800,1800,1` fits
inside the source and **never upscales**, so a listing whose first photo is a 640×480 original
comes back at 640×480 through a perfectly healthy template (~4% of live byt/prodej first
photos measure under 1000 px; a 1867×1400 source measures 1800×1349 against an expected 1350,
which is what the 0.9 tolerates). An estate that declares no size for the image is judged on
the HTTP + decodability arms only, and says so in its message.
A network error, a non-200/empty/idless index, an unreachable or non-200 detail, an imageless
estate, or an image that has left the sdn.cz host (where `with_transform` is a no-op, so
nothing would be exercised) is `unknown` with `details.skipped` ("verified NOTHING"), never
`fail`: the canary reached nothing, and a flaky lane must not read as "sreality rejected our
template". `value` is the measured width in px; `details` carries `image_url`, `transform`,
`detail_url`, `http_status`, `width`, `height`, `source_width`, `source_height`,
`expected_width`, `expected_height`, `min_ratio`, `bytes`, `cdn_elapsed_ms` (the harness stamps `elapsed_ms` for the whole check).
Registered after every database check on purpose (only the weekly URL probe follows it) — the
outbound checks are the ones the lane budget should drop first. The remedy when it reds: re-derive the
template from sreality's own frontend and update `IMAGE_TRANSFORM_OPS`.

**No lane of its own since wave 1.** `sreality_image_canary.yml` is deleted: the check's 24 h
cadence makes it due once a day on whichever GitHub lane runs next (either lane exits non-zero on
its `fail`), registered after the database checks so a slow CDN can never starve one. A CDN
403/429 (sreality's throttle) or a 5xx is `unknown` — it verified nothing; only a 4xx refusal, a
non-image body or a downgraded frame is a `fail`.

## `false_delist_share` — did a confirmed gone verdict close an ad the portal still shows? (2026-10-09)

Rule #3's hysteresis flips a listing only on a second gone verdict at least `GONE_DWELL`
(12 h) after the first, with no sighting between. The check reads the only durable trace of
a flip — `detail_queue_completions.outcome = 'flipped'` (migration 594; reactivation clears
`listings.inactive_at`) — and reports, per portal, the share of the last 7 days' flips whose
ad is active again. A revival after a confirmed verdict means the portal kept the ad down
longer than the dwell and brought it back, or its gone signal misfires; either way the
operator saw an active ad marked inactive. Sized on the pre-hysteresis ledger (one verdict
flipped): sreality 35%, mmreality 15%, remax 11%, bezrealitky 7%, idnes 6%, the rest under
2% — warn ≥5% (the dwell is meant to hold every portal under it), fail ≥20% (the old
sreality order of magnitude: the rule stopped working). A portal with fewer than 50 flips in
the window is listed in `details.unscored`, not scored; a window where no portal reached 50
is Not measured (`unknown`, `value: None`) — verified nothing — which is the expected state
in the first days after 594 ships. `value` is the worst scored share in percent;
`details.per_source` carries flips / active_again / share / scored. Measured every 6 h (its
registry cadence); an alarm. The remedy when it reds: read the flipped rows' pages
(`detail_queue_completions` → `listings.source_url`) before touching the dwell.

## `walk_end_streak` — how many walks in a row has a category nominated nothing? (2026-10-13)

Rule #3 nominates a category's unseen rows only from a walk that REACHED THE PORTAL'S END;
a walk that stops short (a slice over a page ceiling, a deadline, an error, a barren page)
nominates nothing, and that is correct for one walk. Repeated, it is a category whose gone
ads can never close. ceskereality byt/prodej did exactly that for 89 consecutive walks
(2026-09-16 11:51 → 10-13): one kraj sat over the site's 99-page ceiling with no subtype
axis, and 13,373 active rows accumulated against 9,771 on the portal. Each walk logged
`VERIFY skipped` at WARNING; Health's `index_completeness` failed but is a dashboard matview,
not an alert producer; and `walk_coverage` sums collected vs declared over the whole PORTAL,
so the category's 22% gap read ~4.6%, under its 5% warn — and its `categories_nominating`
count is reported but never moves the status.

The check reads `scrape_runs` through `_fetchall` (so the per-check `statement_timeout`
applies): one scan of finished `run_type='index'` rows started in the last 14 days
(`make_interval(days => %(days)s)`, bound from `_WALK_END_WINDOW_DAYS`), unnesting
`by_category` into one row per (run, source, category_main, category_type) with
`bool_and(walk_reached_end)`. Python counts the streak per category, newest walk first
(ties broken on the run id): each walked run that did not reach the end adds one, the first
that did closes it; a NULL verdict (a row older than the key) is neither. Warn at 3 walks,
fail at 6 — counted in walks because the gate is per walk, and GitHub fires the index crons
every ~6–9 h whatever the cron line says, so that is ~1 and ~2 days for a category walked
every run; idnes's rotated categories (~5 of 10 per run) take about twice as long.

A run that OMITTED the category (deadline-truncated, or a rotation) has no row, so the
streak skips it. Instead each cell carries `runs_since_walked` — its portal's finished walks
newer than the cell's last walk — and a cell its portal has not walked in its last 6 walks
reads **warn whatever its streak** ("<source> <cm>/<ct> not walked since <ts> (N <source>
walks since)"). That one rule covers two cases: a category starved by a deadline that never
reaches it (silence is not recovery — it must not read ok), and a category dropped from the
portal's config, which would otherwise hold the bell red for 14 days on a stale streak; a
warn opens nothing, though it keeps an open incident reminding. `walk_coverage`'s truncation
arm does NOT cover a never-started category: it compares the latest run's category count
with the 7-day best, only warns, and goes quiet once the truncation outlasts 7 days or a
rotation keeps the count stable.

`value` is the worst streak among categories still being walked; `details.per_category`
carries streak / walks / `runs_since_walked` / `last_walked_at` / `last_reached_end_at` /
`status` per cell (worst first). Offenders read "<source> <cm>/<ct> not at the portal's end
for N walks (last reached <ts>)", or "for at least N walks (not reached in 14 d)" when no
walk in the window reached it — the window cuts the count, so it is a floor. A window with
no verdict at all (no finished walk anywhere, or `walk_reached_end` gone from
`by_category`) is `unknown` with value None and `details.skipped`, "measured nothing" — never `ok`,
value 0. No alerting check here judges index-walk liveness, so read `scrape_runs` when it says that.

Blind spots, known: (1) two descriptors behind one label (bazos pozemek+zahrada, dum+chata,
the komercni sections, garaz+ostatni) count as reached when every one the run WALKED reached
the end, so a run that stops between them resets the label's streak; (2) a category out of
the window entirely (unwalked 14 days) drops out and reads ok; (3) a dispatched
`--max-pages` / `--limit` run, or a bazos `--locality/--radius-km` run, finalizes as an
index walk with `walk_reached_end=false` on every category it walked, so each one counts as
a miss — three between two crons turn the row amber — and a bazos dispatch narrowed to one
scope counts as a portal walk for `runs_since_walked` of every other category; `scrape_runs`
records no run arguments, so the check cannot tell them apart; (4) until this check emits
one cell per category it has a single implicit cell, so while one category holds it red a
SECOND that parks opens no new incident and is never named in a notice — read the Health
row's `details.offenders` for the full list. Measured every 6 h (its registry cadence)
beside `walk_coverage`; an alarm. When it rings: the category's `VERIFY skipped` line names
our stop, and the portal's own `SLICE` / `DESCENT` lines name the slice.
