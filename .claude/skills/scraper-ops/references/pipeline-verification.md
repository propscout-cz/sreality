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
`details.elapsed_ms` and, when the check emits them, `details.cells`. A run measures only the
checks that are DUE: newest row older than `cadence_s` less a slack of min(cadence / 10, 15 min)
(`toolkit/system_alerts.due_after_s`, the interval the recovery hold doubles), so a run that
comes a few minutes short of a full cadence after the last one does not skip a whole cadence. Only
rows written since the key's `declared_at` count, as for the page and the ladder, so a newly
registered or re-declared key is due in the run that projects it. Since wave 3 the platform alarms `jobs`, `capacity` and
`llm` are hosted on the worker and measured by its health lane (below); every other key is `github-actions`-hosted and
measured by the middle step of the one hourly canary, `verify_pipeline.yml` (it also takes a manual `dry_run`; its
concurrency group, `pipeline-verification`, keeps two runs from measuring a check twice); the
daily `sreality_image_template` and weekly `outbound_url_conformance` probes ride whichever run
finds them due. No result reds a run (`--exit-nonzero-on-fail` is gone since wave 2): the canary's
own assertions do, below. The URL probe keeps its own 120 s budget
(`PROBE_BUDGET_S` in `scripts/verify_outbound_urls.py`, its `Check.budget_s`): each HEAD's connect
and read timeouts are min(20 s, time left); DNS is not bounded, so one request can run past it, and
the job's headroom absorbs that. It probes its draw in an order shuffled by the ISO week, so a slow
sreality cannot starve the crawler portals; URLs it did not reach are `details.unprobed` (with
`details.budget_seconds`), never failures, and a probe that reached fewer than half of its draw is
`unknown` ("probed N of M …" in `details.skipped`). A timeout, an error or a check's own could-not-measure
path is `unknown` with its reason in `details` (`timed_out`, `errored`, `skipped`); a check the
run's budget never reached gets no row at all and stays due. One "verified nothing" path still reads `warn`
until wave 6 replaces its check: `ppm2_median_shift`'s "compared NOTHING";
`location_town_coverage`'s "no listings read" and `false_delist_share` with no portal at 50 flips
are Not measured since wave 1. The harness
writes no system alert: results reach the bell through the ladder below, the notification
system's third producer, `system_health` (`docs/architecture.md` rule #16). Since wave 2 the
pg_cron watcher `health_liveness()` (migration 620) judges whether the worker, the API, the
ladder and every alarm are alive, and the canary checks the watcher and the ladder from GitHub
("Hosts, the watcher and the canary" below). The harness exists because the pipeline
stalled silently for two days in 2026-07 (Anthropic credit exhaustion, 38k+ failed LLM calls) and
the only alarm was a failing GH Actions cron the operator happened to miss. Among the registered
checks: `property_maintenance` (last-complete-cycle stamp age + oldest
dirty-queue row — the 2026-08-06 sweep-death/stranded-lease incident; both axes O(1) reads,
never a properties scan) and `broker_merge_suppression` (migration 401): active `broker_merge_suppressions` rows whose two
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
Wave 3 retired eight platform keys into the three checks the worker measures (below): `llm_errors`
and `llm_burn_rate` into `llm`; `long_open_transaction` and `db_saturation` into `capacity` (the
latter's failure-rate arm into the `jobs` check's pg_cron cells); `worker_lane_stall`,
`workflow_poller_liveness` and `broker_resolution_freshness` into `jobs` (33 of the last's 34
onsets were edge flaps; `broker_maintenance` is a lane cell and `broker_region_type_stats` an
artifact cell); and `dual_write_parity` (Q6) went with the R2 dual-write it guarded. Their
thresholds and tests went with them.
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

**Lane cells** (the `jobs` check's `lane/<lane>` since wave 3; `worker_lane_stall` before it) close
the gap the watcher's `worker` cell (and `worker_liveness` before it) structurally cannot see — a
worker that is ALIVE with a wedged lane. The realtime worker
beat every 30 s for nine hours while its drain lane completed ONE pass and its images lane
completed 486; a pass was recorded only on
COMPLETION, so a hung lane and an idle lane published byte-identical state. The worker now
stamps when a pass BEGINS and the heartbeat resolves it to `in_flight_s`, and `_lane_loop`
bounds every pass with `LANE_PASS_TIMEOUT_SECONDS` (1800) — containment, not a diagnosis: it
stops one hang costing every later pass, and repeated timeouts on one lane are themselves the
diagnosis. Caveat worth knowing: a pass blocked inside `asyncio.to_thread` keeps running after
cancellation (Python cannot kill a thread), so the lane is freed but the thread is not — hence
the pass locks and the watchdog in the skill's worker section. `worker_lane_stall` judged
`in_flight_s` alone, so a lane failing every pass read OK (autodedup showed `failed_passes` 6 on
10-02 while it did); a lane cell also judges lateness, a failing spell and flapping (`jobs`, below).

**`migration_drift`** closes a different silent gap: it probes the live catalog for the objects
the newest 25 migrations declare, so a migration merged but never applied is caught in one tick
instead of the 29 h it took on 2026-08-25 (see the `database` skill). It cannot see a
merged-but-unapplied migration that only redefines existing objects (the migration-438 class,
issue #1855). Only the GitHub checkout has the files, so it stays on the canary for good, hourly
(cadence 3,600 s since wave 3, 21,600 s before); a run that finds none (the worker image has no
`migrations/` folder) is `unknown` with `details.skipped` "no migration files on this host", never
`ok`. Since wave 3 it lives in `scripts/checks/platform.py`, and its probe SQL
(`MIGRATION_OBJECT_PROBE_SQL`, `SAFE_IDENT`) in `scripts/migration_objects.py`, shared with
`scripts/apply_migration_receipt.py`.

**Thresholds.** Every other check's thresholds live in one dict, `THRESHOLDS`
(`scripts/verify_pipeline.py`), each entry with a one-line basis comment, passed to every check by
`main()` and by the worker's health lane. Since wave 3 nothing overrides them: `DEFAULT_THRESHOLDS`,
`load_thresholds` and its merge of `app_settings.pipeline_check_thresholds` are deleted (every
override equalled its default; migration 624 deletes the row), so a threshold changes in a PR, with
its basis. `field_fill_matrix` owns none (sized on its cohort's measured drift), and the watcher's
live as constants inside `health_liveness()` (below).

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
and the message says so. The check carries NO `THRESHOLDS` entries: the numbers
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
- Since wave 3 the crash path writes no incident (the signature engine is retired): the run goes
  red, and the poller hands its reason line to the `jobs` check's `gh/<stem>` cell.

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

**1. Silence is not recovery.** `llm_errors` (retired in wave 3; the `llm` check keeps the rule)
derived `currently_failing` purely from state: `last_ok_at < last_err_at`. It used to additionally
`and` in a 90-minute staleness window
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
Generalise it: a failure is superseded only by a newer success — of the SAME account. Credit is
judged per provider: the text lane calls OpenAI up to every 5 minutes, so one comparison across
providers would let it clear an empty Anthropic account within minutes. Through OpenAI's
2026-10-02..10 credit outage the old alert said every paid LLM path was down and named no provider,
Anthropic-backed jobs included, and its rate arm read the newest call of ANY provider, so a stray
error after a top-up could hold it red for ~24 h; the `llm` check judges both per provider (below).
The worker side of the same incident: a text-lane pass the provider stopped
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

**2. A zero is ambiguous — name the arm.** `llm_burn_rate` (retired in wave 3 into the `llm`
check) had only upper arms ($90 warn / $150 fail), and `_record_failure` writes `cost_usd=0.0`, so
a **total outage drives 24h spend to the maximally healthy number**: it reported `ok value=0.0`
throughout the 11-day outage. It gained named arms, which the `llm` check keeps as cells:
- starvation → Problem: a (provider, `called_for`) with ≥ 5 attempts and 0 successes in the last
  hour (the old arm: a `called_for` with `attempts > 0 AND successes == 0 AND spend == 0` in 24 h).
- idle → OK: a provider with no calls in 72 h, fact `state=idle`. No check judges silence (see §4).
- spend: `fleet/spend`, the 24 h sum against Watch $130 and Problem $150.

**Evaluated per `called_for`, and that is load-bearing.** A 24h aggregate arm is defeated by a
single unrelated cheap success — verified: one `summarize_region_dispositions` call held the
aggregate at $0.01 for ~24 of 30 sampled hours while the only recurring lane was completely
dead. The spend arms, parked as unreachable while 24 h spend was ~$0.01 (2026-08), are live again:
normal spend is $22-32 a day, and wave 1 moved the Watch default from $90 to the operator's $130.

**3. The acute lane must degrade, not vanish.** `run_checks` computed *all* results before
`write_results` persisted *any*, inside `llm_health.yml`'s `timeout-minutes: 5`. A timeout
therefore wrote **zero rows and fired zero alerts** — blinding `db_saturation` and
`worker_liveness` at precisely the moment DB saturation would make checks slow. Now each result
is inserted the instant its check returns, and since wave 1 the harness calls no alert function
at all: the API's ladder reads the stored rows. Budgets: `_CHECK_BUDGET_S` (45 s) per check
(`Check.budget_s`; 120 s for the two outbound probes), capped by whatever remains of the RUN
budget — one per-check budget per DUE check (`lane_budget_for`); the summed budgets are proven
to fit, with the 180 s headroom, the harness step's 21-minute limit (the poller step has 5, the job
35 with 8 minutes for setup and the assertions) by `test_the_canary_job_fits_every_github_check_due_at_once`.
The full lane shared
a 120 s budget until 2026-09-22, and in 13 of the 15 runs before that 7-17 of its 25 checks were
never reached.
Enforcement is **server-side** via `SET LOCAL statement_timeout`: the connection is autocommit
and shared by every check, so a thread we cannot cancel or a signal raised mid-query would
leave it wedged for everyone downstream. Postgres cancelling its own query is the only
mechanism that returns the connection clean. **Since wave 2 the budget is a real wall clock per
check:** `_LaneBudget` (`scripts/checks/clock.py`, which the harness aliases) holds a per-check deadline, `monotonic() + min(budget_s, run time left)`,
and `_statement_timeout_clause()` gives every statement the time left to it, `max(1 s,
min(check deadline, run deadline) − now)`. Until then one timeout of the whole budget was armed
for each statement, so a check with N statements could take N × its budget (`dual_write_parity`:
p50 57 s on its 45 s). A check that has spent its budget between statements gets 1 s on the next
one, is cancelled, and is stored `unknown` with `details.timed_out`, `budget_seconds` (its
budget) and `elapsed_seconds` (what it spent). Every check that read through a raw cursor now
reads through `_fetchone` / `_fetchall` (`scripts/checks/clock.py`; SQL unchanged), so the clock binds it too; a check whose
statements must see one snapshot keeps one `conn.transaction()` and re-arms its `SET LOCAL`
before each statement. A check that raised is `unknown` with `details.errored` — "I could not
measure this" is a different claim from "this is broken", and fail-on-timeout would manufacture
a wall of false reds exactly when the operator needs to read the real one. A check the run never
reached gets **no row**: `run_checks` stops when the run budget is spent and logs the keys it
did not reach, which stay due for the next run (on the worker, its next 60 s tick).
Due-ness reads the newest row of any status, so the wave-1 `unknown` "not run" row
(`details.not_run`) made an unreached check wait a whole cadence for its next try. Until wave 1
timeouts and not-runs were stored as `warn` (which the old ladder read as "not failing", rule 1)
and a raising check as `fail`.

**The poller cell** (`gh/poller` in `jobs` since wave 3; `workflow_poller_liveness`, W0.1, before
it) closes the matching blind spot on the other ops surface. A dead poller cannot appear in
`workflow_failures`, the table it feeds — it simply stops accumulating rows, which is
byte-identical to a quiet week. The cell keys on the age of `app_settings.workflow_failures_cursor`:
Problem past 18 h, the canary's measured 13.3 h maximum gap plus margin (the old check's 6 h / 12 h
were sized on the poller's own `*/30` cron, which the Actions throttle ran 80–256 min apart; its
46 fails 09-18..10-05 were real poller outages), and while it is stale every `gh/*` cell reads Not
measured, because their facts stopped being refreshed. Measured on the worker, it no longer rides
the run whose poll it judges. At 330–484 workflow runs a day (2026-10-15..17), even a 13 h canary
gap is ~270 runs, far inside the poller's 1,000-run page budget below.

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
numbers that were sized on it — `THRESHOLDS` carries its own basis per key for exactly this
reason.

**And the second time, the check went with it.** Field-capture W0 (2026-09-21) deleted the
enrichment lane itself, leaving NO recurring LLM producer: everything left (autodedup judging,
labelling, estimations, URL parsing) is dispatch-driven or on demand. Measured over the 30 days
to 2026-09-21 the longest gap between `llm_calls` rows excluding that lane was **143.8 h**, and
`llm_liveness` had already logged 52 fails in 349 runs against a healthy pipeline. Re-sizing
13 h → 144 h would have bought a check that only speaks after six days of normal weekend quiet,
so `check_llm_liveness`, `_status_for_llm_silence` and `llm_silence_fail_hours` were all
deleted. The replacement is not a global recency threshold at all: field-capture R8 makes each
lane's health check read the lane's OWN eligibility predicate (oldest eligible-unextracted age
per source), which needs no historical baseline — the same shape as `check_acquisition_lag`
(field-capture W7 shipped it as `text_extraction_lag`). Beside it the `llm` check catches "calls
are failing" per provider (from state, not recency) and "attempting and never succeeding".

---

## The ladder — one incident per failing cell, told by the API (wave 1, 2026-10-18)

Wave 1 of the Health dashboard sprint (`docs/design/health-dashboard.md` § 3 and § 6) replaced
W3.4's escalation ladder. That ladder re-derived every incident from 30 days of
`pipeline_check_results` on every lane run (`check_states`, `_collapse`,
`_resolve_truncated_onset`, `AlertPolicy`) and read anything but `fail` as healthy once a 6 h flap
cooldown had passed — shorter than the lanes' real gap, so in 30 days 34 of 88 "✓ Recovered"
alerts were false and 70 onsets followed an unmeasured row — and its anchors drifted as rows aged
out of the window. It, its `alert_*` threshold keys, the day-keyed alerts and the weekly heartbeat
are gone; the harness only measures.

**Where it runs.** `toolkit/system_alerts.reconcile(conn)` is called every 60 s by the API's
system-alerts loop (`api/system_alerts_loop.py`; `SYSTEM_ALERTS_INTERVAL_SECONDS = 60`, a
constant), started in `api/main.py`'s lifespan beside the watchdog matcher and off under the same
`NOTIFICATIONS_MATCHER_DISABLED` guard: telling lives in the process that serves the bell, whose
outbox delivers the rows once a transport is configured. A pass is one transaction under
`pg_try_advisory_xact_lock(hashtext('health_reconcile'))` — a second caller returns zeros and
writes nothing — and returns `{opened, reminded, recovered, closed_silently, notices}`, logged
when any is non-zero. Its values are module constants in `toolkit/system_alerts`, spelled once
(`ALARM_RUNGS_H`, `ALARM_WEEKLY_H`, `GAUGE_RUNG_H`, `DUE_SLACK_MAX_S`, `RECOVERY_HOLD_MIN_S`,
`UNMEASURED_CLOSE_DAYS`, `MESSAGE_MAX_CHARS`); no setting overrides them. The harness's due
selection imports `due_after_s` from it, so the due slack and the hold built on it cannot drift.

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
`cell:<key>:<subject>`; the open-signature unique index allows one open row per cell. It is the one
incident model since wave 3: the reliability program's signature engine (`toolkit/ops_incidents.py`,
migration 462: one incident per error-text signature of a red run or a crash) is deleted with its
claim ledger and `failure_signature`'s signature tiers, and migration 621 closed its open rows
(`resolve_reason = 'retired: signature engine'`, every row whose signature does not start `cell:`).
A measured `fail` opens it (`first_seen_at` = `last_seen_at` = the result's `run_at`) or, when it
is open, advances `last_seen_at` and clears `ok_since`. A `warn` keeps an open incident open (clears
`ok_since`) and opens nothing. A
measured `ok` starts `ok_since` at the first OK, and the incident resolves
(`resolve_reason = 'measured ok'`) only on an OK measured across the hold, two cadences less the
due slack, never under 30 min (`max(2 × due_after_s(cadence), 30 min)`: a lane measures a check
up to the slack early, so two measured intervals can sum to under two cadences): the newest OK's
`run_at` must be at least the hold after the first OK; one lone OK never recovers. Two closes send
nothing: an incident whose key left the registry (`retired`), and a cell unmeasured for max(7 d,
2 × cadence) — its `last_seen_at` older than that and its cell in no measured row inside it
written since the key's `declared_at` — which closes silently (`unmeasured 7d`; two weeks for the
weekly URL probe). Not measured never opens, recovers or ages an incident.

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

**Delivery.** `system_health_channels` is `[]` (Q1), so notices ring the in-app bell only; the flip
is two `app_settings` rows plus a transport secret on the API service (the `toolkit-api` skill:
`RESEND_API_KEY` / `TELEGRAM_BOT_TOKEN`; `api/main.py` starts `outbox_loop` only when a transport
`is_configured()`). Ready since wave 3: the outbox's `compose_message` sends a `system_health` row
with its message as the body, as subject the text before the first ` — ` (the messages above are
one line of up to 1,500 characters) or else its first 120 characters ("System alert" when
empty), and the link `{SPA_BASE_URL}/health#<key>` when the dedupe key starts `sys:<key>:`, else
`{SPA_BASE_URL}/health`. Health's pipeline-check rows carry `id=<key>`, and because they render
after their fetch (so the browser's own jump finds nothing), `PipelineChecksSection` scrolls to
`location.hash` once its rows exist (the `EstimationsBlock` pattern): the link lands on the check.
It replaced the fixed Czech subject and the `/notifications` link.

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
fails the whole window. Its registry copy is host-aware since wave 3 (3 × cadence on the worker,
cadence + 18 h on GitHub); the worker's three checks have input replays of their own (below).

**Cutover.** After the deploy the ladder sends one grouped onset per currently failing check,
alarm or gauge (today three gauges: `location_town_coverage`, `ppm2_basis_floor_share`,
`text_extraction_lag`), once, each on its first pass after the new harness has measured it. The
861 old system alerts can now be cleared with "Mark all read".

**Wave 3's cutover.** The canary's first projection from main after the merge deletes the eight
retired keys' registry rows, so the ladder closes their open cell incidents silently as `retired`
(the `llm_errors` incident open at merge included), and the new `llm` check, measured within
minutes of the worker's deploy, opens a fresh onset for an outage still under way (the two can be
open together for up to one canary gap). Legacy `sys:ops_incident:*` notices stay in the bell as
they are.

## Hosts, the watcher and the canary — who watches whom (wave 2, 2026-10-18)

Wave 2 of the Health dashboard sprint (`docs/design/health-dashboard.md` § 3 and § 6) gives each
runtime the health system depends on an observer that does not run inside it. A check's host
(`Check.host`, mirrored into `health_checks.host`) is where it is measured, and each host projects
and deletes only its own registry rows:

- **`github-actions`**: 20 keys since wave 3 (all 28 in wave 2), measured by the canary's middle
  step at their cadences; waves 4–6 move the rest to the worker as they rebuild them. Two stay for
  good: `migration_drift` needs the repository checkout, and the weekly URL probe needs an address
  other than the scrapers'.
- **`pg_cron`**: `liveness`, the one watcher — a SQL function, not a `Check`, that upserts its own
  registry row.
- **`realtime-worker`**: `jobs`, `capacity` and `llm` since wave 3, measured by the worker's health
  lane (below).

**`run_due`.** `verify_pipeline.run_due(conn, thresholds, host, *, now, on_result=insert_result, lane_budget_s=None)`
reads which of the host's checks are due from the database (`due_checks`, so a restart re-runs
nothing), runs the alarms first and then the gauges, registry order inside each class, through
`run_checks` under the summed budgets of the due checks (`lane_budget_for`), capped by `lane_budget_s` when given, and hands each result
to `on_result` as it lands; `main()` projects the registry and calls it. The per-check wall clock,
and why an unreached check gets no row, are self-rule 3 above.

**The watcher: `public.health_liveness()`** (migration 620). A plpgsql `SECURITY DEFINER` function
with a pinned `search_path` and execute revoked from `public`, `anon` and `authenticated`,
scheduled as the pg_cron job `health-liveness` (`*/5 * * * *`) with a 60 s `statement_timeout`
set in the cron command (the migration-371 idiom: a function cannot raise its own budget). Each
call upserts its registry row (`liveness`: platform, alarm, host `pg_cron`, cadence 300 s,
staleness budget 15 min, label "Watchers", unit `min`, `declared_at` kept) and inserts exactly one
`pipeline_check_results` row. It writes no incident and no notice: the API's ladder turns its
failing cells into incidents like any check's, so they keep their reminders while the worker is
dead. Its cells judge what must not judge itself:

| cell | Problem when | basis |
| --- | --- | --- |
| `worker` | a worker's newest heartbeat is more than 10 min old (with no heartbeat row at all, `unknown`: "no worker on record") | beats come every 30 s, the worker's watchdog exits at 300 s, a deploy gap is under 2 min |
| `health_lane` | the health lane's own pass, read from its heartbeat entry (`details -> 'health'`, migration 621): no completed pass for 10 min, counted from the later of `last_pass_at` and the worker's `started_at` (the boot seed carries the last pass across a restart, so a restart is judged 10 min after boot, not in its first minute), a pass in flight for 10 min (`in_flight_s`: a pass skipped behind an abandoned one still records a pass) or failing for 10 min (`failing_since`, the message quoting `last_error`); `value` = minutes on that clock; emitted once the heartbeat carries a `health` entry, and folded into a failing `worker` cell. Never result rows (620 judged them): the lane writes one only for a due check, so a restart near a cycle's end read as a Problem | 2 × the lane's 300 s pass budget |
| `stale:<key>` | an alarm other than `liveness` whose newest measured result (ok/warn/fail written since its `declared_at`) is past its staleness budget (`public.health_stale`); with no measured result it counts from `declared_at`, so a key whose every run is Not measured is still told; facts `key`, `host`, `last_measured_at`, `budget_s` | the budget is 3 × cadence on the worker and pg_cron, cadence + 18 h on GitHub, whose crons arrive up to ~13 h late |
| `api` | the watchdog matcher's lease (`notification_matcher_lease`) is more than 2 × `notifications_matcher_interval_seconds` (300 by default) + 60 s past its `expires_at`; a NULL `expires_at` (a clean shutdown released it) is `unknown`; interval 0 (matcher off) is `ok` with the state fact `off` | the matcher renews the lease every pass (~300.5 s apart) in the API process that also runs the ladder's loop; the lease lasts 15 min (`api/notifications._MATCHER_LEASE_TTL`), so a crashed API reads Problem about 26 min after its last renewal |
| `unalerted` | a cell fails in an alarm's newest result (in budget, written since `declared_at`) and also failed in its newest measured result at least 2 cadences older, and has no open `cell:<key>:<subject>` incident; cells are read as the ladder reads them (explicit `details.cells`, else the implicit cell named by the key); one cell, `value` = the cells counted, its facts naming the keys and cells | a fail that has stood for two cadences has had every 60 s ladder pass in between to open its incident; like the ladder, it counts no stale or unmeasured result |

While a worker's cell fails, the keys hosted on it (a host is named like its worker,
`realtime-worker`) and its `health_lane` cell fold into it instead of each getting a cell, so a
dead worker is one Problem, not one per check. Every open liveness incident whose subject is
neither failing nor folded this run gets an `ok` cell ("no longer failing"), so a `stale:<key>`
measured again or an `unalerted` that got its incident recovers on a measured OK, told once,
instead of closing silently after 7 days unmeasured. The row's status is its worst cell (fail >
warn > unknown > ok), its `value` the newest worker's heartbeat age in minutes (null without one),
and `details` = `{cells, message, elapsed_ms}`, the message joining the messages of the cells at
the row's status, or "Watchers: all alive" when it is ok. Its limits are structural constants
inside the function, not tunables. The apply's post-condition calls it once and keeps that row,
so the canary and Health see liveness at once. It replaces two guards: `worker_liveness`, a
5-minute heartbeat limit that GitHub sampled about every 4.9 h (it caught an outage of length D
with a probability of about D / 4.9 h), deleted with `worker_stale_fail_minutes` — the ladder
closes any open `cell:worker_liveness:*` incident silently as `retired` once its registry row is
gone; and migration 274's dead-man job `emit-verification-stale-alert` (24 h on the newest row of
any key; it never fired), which 620 unschedules and 624 drops.

**The canary: `verify_pipeline.yml`**, "Monitoring: health canary (hourly)": cron `15 * * * *`
plus a manual dispatch whose `dry_run` input reaches the harness step only. GitHub delivers about
five of its runs a day (median gap ~4.9 h). One job — 35 minutes while it carries the legacy keys (the poller step 5, the harness step 21 for
its budgets plus 180 s headroom, 8 for setup and the assertions; 20 budgets summing to 1,050 s since
wave 3, 28 and 1,410 s before),
concurrency group `pipeline-verification` (never cancelled in progress), permissions
`contents: read` and `actions: read`, env `SUPABASE_DB_URL` and `GITHUB_TOKEN` — in three steps:

1. **Record failed workflow runs**: `python -m scripts.record_workflow_failures`, moved in wave 2
   from the deleted `monitor_workflow_failures.yml` with that job's 5-minute limit on the step, so
   a wedged poll cannot eat the checks' time; since wave 3 it records production runs with their
   reason lines and workflow facts ("The poller", below).
2. **Measure the GitHub-hosted checks**: `python -m scripts.verify_pipeline --host github-actions`
   (`--dry-run` when asked), `if: ${{ !cancelled() }}` under its own 21-minute limit, so a poller failure never skips the
   checks (a cancel does, and an overrun fails this step rather than the job).
3. **Assert the watchers and the teller are alive**: `python -m scripts.health_canary`,
   `if: always()`, the last step and the only one whose non-zero exit is meant to red the run. A
   poller or harness crash reds it too, as does the harness step overrunning its 21 minutes, and rightly: the
   canary did not complete.

`scripts/health_canary.py` connects with `scraper.db.connect()` and runs one bounded SQL per
assertion, each under `SET LOCAL statement_timeout = '10s'`; pure functions turn the rows into
reasons, so its tests need no database. Each failure prints one GitHub error annotation,
`::error::CANARY FAIL <name>: <reason>`, listed on the run's summary page; a clean run prints
`CANARY OK`. Exit 2 when the database does not answer (any read failing or cancelled), 1 when any
assertion fails, 0 otherwise:

| assertion | fails when | why |
| --- | --- | --- |
| `db_answers` | `select 1` cannot connect or answer within 10 s (exit 2, the error printed) | every other assertion reads the database |
| `liveness_fresh` | the newest `liveness` row is 30 min old (`LIVENESS_STALE_MIN`), or there is still none 30 min after the key was registered ("pg_cron has never written a liveness row"); skipped with a `CANARY SKIP` note while `health_checks` has no `liveness` key, so the canary can merge before migration 620 is applied | the watcher runs every 5 min: 30 min is six missed ticks |
| `failing_cells_have_incidents` | a liveness cell that has failed in every row for 15 min or more (`TELLER_GRACE_MIN`; the cells read by the ladder's own rule) has no open `cell:liveness:<subject>` incident | the ladder, the API's 60 s loop, is not running, so nothing the watcher finds reaches anyone; 15 min outlasts an API deploy |
| `alarm_notices_handled` | an alarm's `system_health` notice keyed `sys:<key>:onset:…` or `sys:<key>:reminder:…`, dispatched in the last 12 h (`NOTICE_WINDOW_H`) and at least 15 min ago (`NOTICE_GRACE_MIN`), is unhandled: not seen, not delivered (no `channel_sends` row `sent` or `delivered` for it), and its key still has an open, unacknowledged cell incident; the line names the key, the notice's age and its kind | with the bell the only channel (Q1), this is how an alarm nobody read reaches the operator |

The fourth assertion matches the dedupe-key prefixes only (a grouped key carries its incident ids
as a tail) and never looks at a recovery or a `sys:ops_incident:*` notice. Gauge notices are never
asserted: the operator ruled that a gauge rings in the bell once a day, and the email path is for
alarms. The 15 min grace covers the outbox's 120 s interval and its first retry once a channel is
configured. So an alarm nobody reads reddens every canary run from 15 min to 12 h after each of its
notices, and GitHub emails a red scheduled run to whoever last changed its cron line: at the first
canary run after the onset, and again after each reminder (6 h, 24 h, 72 h, then weekly), until
the notice is read or delivered, the incident acknowledged, or the cell recovered. This replaces
`--exit-nonzero-on-fail`, deleted with `llm_health.yml` and the 6-hourly lane: those lanes
reddened a run whenever it measured a failing alarm, read or not, and could never speak for a
check measured elsewhere — the watcher's, or the worker's.

**Who watches whom.**
- **pg_cron** (the watcher) watches the worker (its heartbeat), the API (the matcher's lease), the
  ladder (`unalerted`) and every alarm's freshness, the canary's GitHub-hosted keys included: a
  canary that stops running is told in the bell once its hourly keys pass cadence + 18 h.
- **The API** (the ladder) tells what the watcher and the checks find, and keeps reminding about a
  dead worker because it does not run in the worker.
- **GitHub** (the canary) watches pg_cron (`liveness_fresh`), the ladder
  (`failing_cells_have_incidents`) and whether anyone heard (`alarm_notices_handled`); its failure
  email needs neither Supabase's scheduler nor Railway.
- **The worker** (its health lane, since wave 3) runs the platform checks: `jobs` watches every
  production GitHub workflow (through the canary's poller, whose cursor is its `gh/poller` cell),
  every pg_cron job (`health-liveness` included), every worker lane and every derived artifact.
  The watcher's `health_lane` cell watches the lane; waves 4–6 move the remaining checks to it.

**Known gap.** A clean API shutdown leaves the lease NULL, which reads Not measured, not Problem:
that is every deploy's normal state until the new process's first pass claims the lease. An API
that stops cleanly and never comes back is therefore no liveness Problem by itself; it shows only
once a liveness cell fails and finds no incident, which the canary's third assertion then emails.

## The health lane — the worker measures its own checks (wave 3, 2026-10-19)

Wave 3 of the Health dashboard sprint (`docs/design/health-dashboard.md` § 6 and its "As built"
note) gives the worker its first checks. The lane `health` in `scraper/realtime_worker.py`,
registered in `LANES` after `text_extract`, wakes every `HEALTH_TICK_SECONDS` (60 s, a constant
like `text_extract`'s: no settings row and no kill switch, so a brake is a deploy) and runs one pass
at a time under `_HEALTH_PASS_LOCK`. Each pass, `_health_sync()` runs in a thread on its own
`db.connect()`:

- on the process's first pass, `verify_pipeline.project_registry(conn, "realtime-worker", now)`, so
  every deploy re-projects the worker's keys and deletes its undeclared ones, touching no other
  host's rows;
- every pass, `verify_pipeline.run_due(conn, THRESHOLDS, "realtime-worker", now=now,
  lane_budget_s=HEALTH_PASS_BUDGET_SECONDS)` (300 s), which measures the due worker-hosted checks,
  each on its own clock (self-rule 3);
- once per 24 h in each process, the 35-day prune the operator ruled (Q5): `delete from
  pipeline_check_results where run_at < now() - interval '35 days'`, 10,000 rows at a time by
  `ctid`, until none is left.

Its heartbeat `last` is `{"measured": [keys], "pruned": n}`. The worker imports
`scripts.verify_pipeline` inside the pass (the image carries `scripts/`; `migrations/` is
dockerignored, which only `migration_drift` reads, and that check stays on GitHub);
`verify_pipeline.main()` takes `--host github-actions` only, and the worker never calls it. The
three keys are platform alarms with the 45 s per-check budget and a staleness budget of 3 × cadence
(30 min for `jobs` and `llm`, 90 min for `capacity`), so the watcher's `stale:<key>` tells a stopped
worker check within 5 min of that, its `health_lane` cell (no completed pass, a pass in flight or a
failing spell for 10 min, 2 × the pass budget) tells a stopped lane, and the lane is a `lane/health`
job cell like any other.

## `jobs` — is every scheduled machine running and succeeding? (wave 3)

`check_jobs` (`scripts/checks/platform.py`; platform alarm on the worker, cadence 600 s, label
"Jobs", unit "cells") gives one job cell per scheduled machine on every runtime, with the subjects
`gh/<workflow file stem>`, `gh/poller`, `cron/<jobname>`, `lane/<lane>`, `lane/<lane>/<portal>` (its
`portal` field set) and `artifact/<name>`, so the ladder opens one incident per failing job and one
grouped notice per pass lists every newly failing job with its reason. It replaces
`worker_lane_stall`, `workflow_poller_liveness`, `broker_resolution_freshness` and
`db_saturation`'s failure-rate arm (above).

**GitHub workflows**, from the poller's tables (below). A cell exists for every
`workflow_run_health` row with `on_main = true` — a workflow whose file left `main` has none, and a
row the poller has not stamped yet (NULL) has none yet — and only a production run is a job's record
(event `schedule`, or `push` on `main`; a row written before wave 3 has `event` NULL and never
counts). A workflow whose `state` is not `active` (`disabled_manually`, …) reads OK with the fact
`state`. The canary has no cell: GitHub emails its red runs.

| reads | when | basis |
| --- | --- | --- |
| Problem | the newest production run failed and the workflow's median start gap is at least 24 h (`jobs_gh_long_cycle_h`); Not measured until 2 scheduled starts are recorded, one to two cycles for a weekly or monthly job, because `recent_starts` accumulates with no bootstrap | one red loses a whole cycle: `location_registry_load`'s single 09-01 timeout left ~91.6 % of katastr polygons missing for weeks |
| Problem | 3 consecutive production reds since `last_success_at` (`jobs_gh_streak_fail`) | in migration 462's corpus 35 of 63 red streaks were a lone failure; `recompute_mf_yields`' 3rd red came at ~15:00 on 09-13 |
| Problem | the time since `last_success_at` exceeds 3 × the p90 of the workflow's own start gaps (`jobs_gh_gap_p90_multiple`); it needs 10 `recent_starts` (`jobs_gh_gap_min_starts`), and until then this arm is Not measured inside the cell's facts, never a verdict; the time is counted to the poller's cursor, not to now: the cursor (the newest completion a whole poll has seen) is how far the workflow's runs are known | each workflow is judged by its own rhythm, not by one global clock |
| Watch | a lone red | main-branch CI goes red after ~10 % of merges |

`recent_starts` holds scheduled starts only ("The poller", below): a push has no cadence to be
overdue against. So for a workflow that only runs on push, the long-cycle and overdue arms are Not
measured, a lone red is Watch and 3 reds in a row are a Problem; its push runs still keep its
`last_success_at`, `state` and `on_main`, and its reds still count.

A failing cell's message carries the run's reason line and its link. The four stale streaks the
investigation found get no cell: `recompute_mf_yields`, `autodedup_realtime` and
`validate_vision_models` left `main`, and `dinov3_embed_backfill` runs on dispatch only.
**`gh/poller`** is Problem when the poller's cursor is older than 18 h (`jobs_poller_stale_h`; its
basis is "The poller cell", above), and while it is, every `gh/*` cell reads Not measured.

**pg_cron jobs.** A `cron/<jobname>` cell for every `cron.job` row, the watcher's own
`health-liveness` included (an inactive job reads OK with the fact `active=false`), judged over
finished runs only for the failure arms (`end_time IS NOT NULL`; the lateness arm counts every start):

| reads | when |
| --- | --- |
| Problem | 2 of the last 3 runs failed (`jobs_cron_fail_of_3`) |
| Problem | no run started for 3 × the job's median start gap over 7 days (`jobs_cron_gap_multiple`); it needs 5 starts (`jobs_cron_gap_min_starts`), and until then this arm is Not measured |
| Watch | any failure in 24 h |

When the `cron` tables cannot be read, or `cron.job` reads empty, the pg_cron arm is Not measured
with the reason, never OK: pg_cron's row security returns zero rows to the wrong role, so an empty
read is a failed measurement, not a quiet scheduler. No migration grants anything for it; the
worker's role already reads the cron tables, and the check reads them defensively.

**Worker lanes**, from the newest `worker_heartbeats` row: its `details` per lane, and the worker's
uptime from `started_at`. One `lane/<lane>` cell per lane carries the WORST of its conditions, each
named in `facts.conditions`; the `heartbeat` lane has no cell (the watcher judges the beat), and
`health` is judged like any lane.

| reads | when |
| --- | --- |
| OK, `off` | `interval_s` ≤ 0, or the newest pass's `last.skipped == "slice<=0"` (what a drain or images pass with a slice ≤ 0 records; any other `skipped`, autodedup's included, is not off) |
| Problem, never woke | `interval_s` is None and the worker has been up more than 15 min (`jobs_lane_boot_grace_s`) |
| Not measured | the lane's entry has no `interval_s` key at all, which only a worker from before wave 3 writes (the seeded None, the key present and null, is "never woke"); this guards the first deploy, while the old process's beat can still be the newest row |
| Problem, late | `now − last_pass_at` > max(3 × `interval_s`, `interval_s` + 2 × `last_duration_s`, 15 min) (`jobs_lane_floor_s`), once the worker has been up longer than that bound; a lane with no pass yet counts from the worker's start; a lane with no pass in this process counts from the worker's boot, or from this check's previous measurement when the lane read off then (switched on after boot) |
| Problem, failing | `failing_since` older than max(3 × `interval_s`, 15 min), or set at all when `interval_s` ≥ 1 h (`jobs_lane_daily_interval_s`; a daily lane passes about once per boot) |
| Problem, in flight | a pass running more than 1,800 s (`jobs_lane_inflight_fail_s`, `LANE_PASS_TIMEOUT_SECONDS`) |
| Watch | a pass running more than 1,200 s (`jobs_lane_inflight_warn_s`); the newest pass failed (`failing_since` set, not yet old); flapping: since this check's previous result, at least half of the lane's attempts failed (`jobs_lane_flap_share`, 0.5), that is `failed_passes` grew at least as much as `passes`, which counts completed passes only, over at least 3 attempts (`jobs_lane_flap_min_passes`) |
| no cell | the worker has been up under 15 min and the lane has not passed in this process yet |

A portal failing inside the probe or drain lane has its own cell, `lane/<lane>/<portal>`, from
`portal_errors`: Problem once its `failing_since` is older than max(3 × the lane's interval,
15 min), Watch before. Basis: the lane bounds rest on 7 days of Railway logs read at build; the only
stored episode is autodedup's 10-02 stall, when the lane showed `failed_passes` 6 while
`worker_lane_stall` read OK.

**What the heartbeat carries** (`scraper/realtime_worker.py`, wave 3). `_amain` registers every
lane from the module constant `LANES` — `probe`, `drain`, `images`, `count_probe`, `maintenance`,
`broker_maintenance`, `estimation`, `location_resolve`, `location_intake_fast`, `location_refetch`,
`sold_comps`, `text_extract`, `health`, `autodedup`, `heartbeat` — seeds each with `interval_s`
None before any task starts, and asserts that the names it registers equal `LANES`; nothing imports
the worker to learn the list. `_lane_loop` writes `interval_s` on every wake, the idle branch
included, so an off lane reads off, not absent. Per lane: `interval_s`, `passes`, `failed_passes`,
`last_pass_at`, `last_duration_s`, `started_at`, `in_flight_s`, `last`, `failing_since`,
`last_error` (at most 300 characters) and, on the probe and drain lanes, `portal_errors`
(`{portal: {failing_since, last_error}}`); `last_failure_at` is gone. A pass fails exactly where it
failed before — a raise (`last_error` = `<Type>: <message>`), the 1,800 s timeout (`pass exceeded
1800 s`), `text_extract`'s `fatal`, autodedup's refusal — and nothing new counts as a failure:
`_record_pass_failed` sets `failing_since` once and overwrites `last_error`; `_record_pass` clears
both and carries `interval_s`, `portal_errors` and every key it does not set itself. The heartbeat
lane records its own pass (`{"beat": true}`) like any lane, so no reader special-cases it. A probe
or drain pass reports the portals it attempted and those that raised (a probe portal whose every
category failed counts as raised, `every category failed`): a success deletes that portal's entry,
a raise sets or keeps it, and a portal not attempted keeps it unless the lane's disabled-sources
setting now holds it, which deletes it. `_record_lane_failure`, a blocking incident write per
failing portal, is gone. **At boot**, before the first beat, `_seed_from_previous_beat(state)` reads
this worker's previous `worker_heartbeats.details` in one bounded attempt (`connect_timeout` 10, no
retry; a miss seeds nothing and logs once) and copies, per lane in `LANES`, every key except
`started_at`, `in_flight_s`, `interval_s` and `last_failure_at`, so about 11 deploys a day reset no
counter, failing spell or portal error, and a lane that never wakes in the new process reads
"never woke", not late against its old interval.

**Derived artifacts.** An `artifact/<name>` cell for every `derived_artifacts` row: Problem past its
`staleness_budget` (`public.health_stale(last_succeeded_at, staleness_budget)`), except that a row
whose `host` is `github-actions` is stale only past the budget plus `GITHUB_DELIVERY_SLACK_S`
(18 h), because GitHub delivers a cron late by hours (54 of the canary's last 65 scheduled gaps
exceeded 6 h: median 7.2 h, longest 13.5 h, 09-28..10-18); Not measured ("never succeeded") while `last_succeeded_at` is NULL. Facts: `producer`, `host`,
`cadence`, `last_succeeded_at`, `last_rows`, `last_duration_ms`, `budget_s`, `slack_s` and
`is_serving` (a fact, never a verdict). Each budget is at least 3 cadences (`browse_list` 45 min on
`*/15`); every live row was inside its budget at build.

## The poller — production runs, reason lines and workflow facts (wave 3)

`scripts/record_workflow_failures.py`, the canary's first step, writes the facts the `jobs` check
judges for GitHub; it judges nothing itself.

**Production runs only.** A job's record is its production runs: event `schedule`, or `push` on
`main`. Pull-request, dispatch and branch runs are attended and stay in Settings › GitHub Actions,
so a workflow that dispatches itself is recorded by its cron run only: its chained hops are
dispatch runs. The canary (`MONITOR_WORKFLOW_NAME`, matched by its display name) is skipped for
failures and successes alike, so its own runs never reach the tables; GitHub's email is their path.
A poll whose `GITHUB_REF_NAME` is set and is not `main` (a dispatch from a branch) writes nothing
and says so in one log line, the registry projection's rule. The cursor
(`app_settings.workflow_failures_cursor`), the cancelled gate (a never-started supersession cancel
is not a failure) and `_advance_cursor` are unchanged.

**What it writes.** For every production run it sees, the workflow's `workflow_run_health` row is
upserted, born on a failure too: `last_success_at` by `greatest()` from its successes; `state` from
one `GET /repos/{repo}/actions/workflows` per poll (`active`, `disabled_manually`, …); `on_main`,
whether the workflow file is in the checkout; and `recent_starts`, the stored array merged with the
SCHEDULED starts this poll saw (event `schedule`, every conclusion; a push has no cadence to be
overdue against), newest 20 kept. It accumulates with no history bootstrap, so the start-gap arm is
Not measured until a workflow has 10 scheduled starts, about two weeks for a daily job, and the
long-cycle arm until it has 2. Each new failure goes into `workflow_failures` with `event`,
`head_branch` and `reason`, the reason line of its failed job's log tail (`LOG_TAIL_BYTES`),
fetched for newly inserted rows within `MAX_LOG_FETCHES` (25 logs) and `REASON_PASS_BUDGET_S`
(90 s) per poll; a row past either budget keeps `reason` NULL and is not retried, so its cell shows
the run link without a reason. Migration 621 added those three columns and `state`, `on_main` and
`recent_starts`.

**The reason line.** `failure_signature.reason_line(text)`, at most 300 characters (None when the
log has none), is the one line that explains a red run: (1) the LAST `CHECK <key> status=fail`
line; else (2) the last exception line — `<CapWord>Error: …`, `<CapWord>Exception: …` or a
traceback's final line — outside `##[group]` … `##[endgroup]` blocks and not starting with runner
boilerplate (Image, Version, Commit, Region, Cloud, Contents, Metadata, Packages, Actions, `Note:
switching to`); else (3) the last `##[error]` line. The Actions timestamp prefix is stripped and the
rest of the line kept. The signature engine's exception-first tier took the last CapWord-colon line
anywhere, so runner setup lines such as `Metadata: read` beat every CHECK line: it never produced a
single CHECK signature. The fixture `tests/fixtures/workflow_job_116299577551.txt` (a 2026-10-15
monitoring run's job log, scrubbed of tokens) pins that it yields its `CHECK llm_errors
status=fail` line, not `Metadata: read`. Since wave 2 only the canary prints CHECK lines, and the
poller skips it, so for today's workflows the reason is usually tier 2's exception.

**The log fetch has three hazards, all live-verified — do not simplify it.**
`/actions/jobs/{id}/logs` answers **302** to a SAS-signed Azure blob URL, and CPython's
`HTTPRedirectHandler` copies every header (`Authorization: Bearer` included) onto the redirect:
that leaks `GITHUB_TOKEN` to a third-party host *and* returns 401. So redirects are disabled and
the `Location` is re-requested **bare**. Azure **ignores suffix ranges** (`Range: bytes=-500`
came back 200 with the whole 27 KB body), so the tail is two requests: `bytes=0-0` to read the
length off `Content-Range`, then a real closed range. And the signed URL can 404 `BlobNotFound`
(retention, or logs not yet flushed) — that row keeps `reason` NULL, never a poller failure.
Never `tail` a log either: the last ~25 lines are always runner cleanup, so the extractor
anchors on the error and walks backwards.

## `capacity` — is the database, and the worker's memory, keeping up? (wave 3)

`check_capacity` (`scripts/checks/platform.py`; platform alarm on the worker, cadence 1,800 s,
label "Capacity", unit "cells"):

| cell | measures | Watch | Problem |
| --- | --- | --- | --- |
| `cron/<jobname>` | the 24 h p50 and p95 duration of its finished runs, against its reference, its interval (the median start gap) and its `statement_timeout` (parsed from the `set statement_timeout='NNNs'` prefix of `cron.job.command`; with none, that arm is not judged) | p50 ≥ 3 × reference and ≥ 30 s, or p95 ≥ 50 % of the interval | p50 ≥ 4 × reference and ≥ 60 s, or p95 ≥ 80 % of the interval or of the statement_timeout |
| `check_timeouts/<host>` | one cell per host that measures through the harness (`github-actions`, `realtime-worker`): the share of the last 24 h's `pipeline_check_results` rows of that host's keys with `details ? 'timed_out'`, so one host's timeouts are not averaged into the other's; the watcher's `pg_cron`-hosted key is left out, and a host with no result in 24 h is Not measured | > 5 % | > 20 % |
| `open_transaction` | the oldest `xact_start` in `pg_stat_activity`, autovacuum and the check's own backend excluded; Not measured while the view hides a session (below) | > 60 min | > 6 h |
| `connections` | client connections against `max_connections`; Not measured while the view hides a session (below) | > 70 % | > 90 % |
| `worker_memory` | the autodedup lane's `last.peak_rss_mb` over its `memory_limit_mb`, from a pass of this process only: Not measured ("no autodedup pass since the worker booted"; facts `last_pass_at` and `boot`) while the lane's `last_pass_at` is NULL or older than the heartbeat's `started_at`, because the boot seed carries `last` across a restart (`text_extraction_lag` reads the text lane's), so until then it is the dead process's peak | > 0.8 | > 0.9 |
| `db_restart` | `pg_postmaster_start_time()`, a fact | — | — (always OK) |

**A hidden session is not a quiet one.** `pg_stat_activity` shows a session the reading role may not
inspect with its `backend_type` NULL, so `connections` and `open_transaction` also count those
rows, and while there is any, both cells are Not measured ("pg_stat_activity hides other sessions
from this role"): a count or an oldest transaction over the visible sessions alone reads low and
passes.

**The reference cannot be eroded by the incident.** A job's reference is its lowest weekly p50 of
finished runs since `THRESHOLDS["CAPACITY_REFERENCE_FROM"]`, a blessed date: 2026-10-17, the first
full day on the 2XL instance (p50 41-100 s). Before 7 days of post-reference history
(`capacity_reference_min_days`) the relative arm is Not measured; re-blessing after a deliberate
change is a dated PR that moves the date, and a rail in `tests/scripts/test_verify_pipeline.py`
fails once the date is more than 90 days old, so that PR is forced, not remembered. Why not a trailing baseline: `browse-list-rebuild`'s
daily p50 ran 151-277 s from 08-17 against a lowest weekly p50 of ~9.9 s (ratio 15-28) and hit
1,800 s timeouts 09-26..10-03, while a trailing 28-day baseline rose from 25.7 s (08-18) to 49.5 s
(08-31), 176.6 s (09-15) and 242 s (10-09), so it would have read "recovered" by early September.
A known, stated limit: a cron tick that skips itself is indistinguishable from a run and biases p50
down, the reference with it, which only makes the relative arm stricter.

**The other cells' basis.** Check timeouts ran up to ~12 % a day during the saturation and 0 since
10-10, measured on the GitHub population (the only host before wave 3); the worker's cell uses the
same limits. The oldest open transaction peaked at 18.3 min in the saturation month; it matters
beyond load because `refresh_llm_cost_rollups`' 3-hour trailing re-scan absorbs only a late arrival
whose transaction was shorter than that (`called_at` defaults to now(), the transaction START; the
repair is `select refresh_llm_cost_rollups('-infinity');`). Connections read 29 of 380 and the
worker's memory 7.4 of 22.9 GB at build. It replaces `db_saturation` (a failure rate only, OK on
all 61 runs of the saturation week) and `long_open_transaction` (never non-OK in 196 runs, with no
fail tier).

## `llm` — are the providers serving, and is spend sane? (wave 3)

`check_llm` (`scripts/checks/platform.py`; platform alarm on the worker, cadence 600 s, label "LLM
providers", unit "cells"), over `llm_calls` (24-72 h, ~2 s):

| cell | Problem | Watch |
| --- | --- | --- |
| `<provider>/credit` | the newest error matching the credit patterns `llm_errors` used, quoted verbatim, is newer than that provider's own newest success, over a 72 h lookback (`llm_lookback_h`); an authentication failure does not count | — |
| `<provider>/<called_for>` | at least 5 attempts and 0 successes in the last hour (`llm_starved_min_attempts`) | a 24 h error share of at least 20 % over at least 20 calls (`llm_error_share_warn`, `llm_error_share_min_calls`) while the provider's newest call is an error |
| `fleet/spend` | 24 h `cost_usd` above $150 (`llm_spend_24h_fail_usd`) | above $130 (`llm_spend_24h_warn_usd`) |

A provider with no calls in 72 h has one cell, OK, with the fact `state=idle`. Basis: 5 of 5 onsets
of the old checks since 08-27 were real; normal spend is $22-32 a day. The 72 h lookback keeps a
credit outage red after its callers back off (self-rule 1: a circuit breaker stops the traffic, and
a recency window then reads the silence as recovery); judging per provider means one provider's
success no longer masks another's outage; starvation per (provider, `called_for`) means one cheap
success elsewhere cannot hold an aggregate healthy (self-rule 2). It replaces `llm_errors` (an
any-provider rate gate over a 24 h window) and `llm_burn_rate` with its starved and idle arms, and
spells the spend once (the code default was $90 against the settings row's $130 until wave 1).
`text_extraction_lag`'s abort arm, a third detector of the same outage, goes with that key in
wave 6. Until then the alarms tell a provider's credit outage twice, by its `<provider>/credit` cell
and by the `jobs` check's `lane/text_extract` cell, because the text lane's provider-fatal pass is
a failed pass.

**Replay rails.** `tests/scripts/test_checks_platform_replay.py` replays aggregate INPUTS exported
read-only from production — `tests/fixtures/health_jobs_14d.csv`, `health_capacity_cron_8w.csv`
(weekly p50, p95 and start gap per pg_cron job from 2026-08-10, a week before the 08-17
saturation) and `health_llm_14d.csv` (hourly calls, errors, credit errors and cost per provider
and `called_for`), each with its refresh SQL in the test's header — through the checks and the
ladder. The incident-shaped windows fire: the 09-13 `recompute_mf_yields` reds, the 10-02 and
10-18 OpenAI outages, and the 08-17 → 10-10 `browse-list-rebuild` saturation, replayed on a
reference date overridden to 08-10 (the blessed 10-17 postdates it): its incident opens inside that
reference week through the p95 arm, before the relative arm can judge, and the sticky reference
holds it red to the end. The quiet windows are what the data allows: a full fortnight for llm
(09-14..09-27); 8 days for pg_cron (10-11..10-18, after the saturation's tail); one week for
capacity (from 10-12), with the relative arm Not measured on the blessed reference; none for GitHub
workflows, because only each workflow's newest success was stored before wave 3. The worker lanes
and the derived artifacts keep no history, so only their hermetic tests judge them.
`tests/scripts/test_checks_platform.py` pins every rule above on fake connections, which answer a
statement by a fragment of its text, so the pg_cron SQL also runs for real: in CI's replay database
(no pg_cron) a rolled-back transaction creates a stub `cron` schema (`job`, `job_run_details`),
seeds runs and executes `_CRON_JOBS_SQL`, `_CAPACITY_CRON_SQL` and `_CAPACITY_REFERENCE_SQL`
through psycopg with their real parameters, asserting that only finished runs count, that the
7-day window counts back from the job's newest start, "2 of the last 3" and whole-week reference
buckets; the artifact read runs there too, with the GitHub delivery slack the check sends. That
rail, `tests/test_platform_cron_sql_live.py`, is one `migrations.yml` step after 621's
(`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`), skipped locally.

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
Registered after every database check on purpose (only the weekly URL probe follows it), so the
two outbound checks are the last alarms a run reaches (since wave 2 the gauges run after every
alarm) and the first alarms a spent run budget drops. The remedy when it reds: re-derive the
template from sreality's own frontend and update `IMAGE_TRANSFORM_OPS`.

**No workflow of its own since wave 1.** `sreality_image_canary.yml` is deleted: the check's 24 h
cadence makes it due once a day on the next canary run, after the database alarms so a slow CDN
can never starve one; its `fail` is told by the ladder like any alarm's (no run goes red on a
result since wave 2). A CDN 403/429 (sreality's throttle) or a 5xx is `unknown` — it verified
nothing; only a 4xx refusal, a non-image body or a downgraded frame is a `fail`.

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
