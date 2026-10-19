"""verify_pipeline.py — the health-check registry and the harness that measures it.

`CHECKS` declares every check once: key, page section (`domain`), class (an alarm or a
gauge), host, cadence, time budget, label, unit and owner page. A run from main projects its
host's rows into `health_checks` (`project_registry`), then `run_due` measures the checks that
are due by cadence (alarms first, each on its own wall clock) and stores one
`pipeline_check_results` row per result the moment it completes, its message and cells inside
`details`. A status is ok, warn, fail or unknown, and unknown means "not measured" (timed out,
errored, or nothing to score): never OK. A check the run's budget does not reach writes nothing
and stays due. Nothing here tells anyone — the API's ladder (`toolkit.system_alerts.reconcile`)
reads the stored results, opens one incident per failing cell and sends the notices — and no
result changes the exit code: the canary's assertions (`scripts/health_canary.py`) are the one
way a monitoring run goes red.

Thresholds live in `THRESHOLDS` below, each beside its basis; changing one is a PR. The canary
runs `main()` for `github-actions`; the worker's health lane calls `project_registry` and
`run_due` for `realtime-worker` itself.

There is deliberately NO total-silence LLM check: every LLM producer left is dispatch-driven or
on demand, so silence is the normal resting state (the longest gap in the 30 days to
2026-09-21 was 143.8 h, and the old `llm_liveness` failed 52 of 347 runs on a healthy
pipeline). `text_extraction_lag` watches the one recurring lane through its own predicate.

    python -m scripts.verify_pipeline            # the due checks of github-actions
    python -m scripts.verify_pipeline --dry-run  # the same, writing nothing

Needs only SUPABASE_DB_URL.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import sys
import time as _time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from location_data import location_steps
from location_data.resolver.version import RESOLVER_VERSION
from scraper import attribute_contract, field_census
from scraper.db import connect
from scripts.checks.clock import (
    CHECK_BUDGET_S as _CHECK_BUDGET_S,
    DEFAULT_STATEMENT_TIMEOUT_MS as _DEFAULT_STATEMENT_TIMEOUT_MS,
    LANE as _LANE,
    fetchall as _fetchall,
    fetchone as _fetchone,
    statement_timeout_clause as _statement_timeout_clause,
)
from scripts.checks.delisting import check_delisting
from scripts.checks.platform import (
    check_capacity,
    check_jobs,
    check_llm,
    check_migration_drift,
)
from scripts.checks.publishing import check_publishing
from scripts.checks.scraping import check_coverage, check_scraping
from scripts.verify_outbound_urls import PROBE_BUDGET_S, check_outbound_url_conformance
from toolkit.system_alerts import due_after_s as _due_after_s


# The SQL mirror of `location_data.claims_common.sreality_payload_shape`, inlined here when
# the refetch cohort was deleted (rule 25 W1-a) because this check is its only reader left.
SREALITY_SHAPE_CASE_SQL = """
    CASE
      WHEN jsonb_typeof(raw_json->'locality') IS DISTINCT FROM 'object' THEN 'absent'
      WHEN raw_json->'locality' ?| array['gps_lat','gps_lon','entity_type','inaccuracy_type','city','citypart'] THEN 'post_cutover'
      WHEN raw_json->'locality' ?| array['name','value','accuracy'] THEN 'legacy'
      ELSE 'absent'
    END
"""

LOG = logging.getLogger("verify_pipeline")

# Every threshold a check reads, each beside its basis: the one home, so changing one is a
# reviewed PR. Nothing overrides it at run time (app_settings.pipeline_check_thresholds is
# read by nothing since wave 3; migration 624 deletes the row).
THRESHOLDS: dict[str, Any] = {
    # Field-capture R8: how long a listing whose prose the text lane has not read has been
    # waiting. The lane runs every 5 minutes and its 20-minute SLO is for INFLOW; a backlog
    # drains at 500 rows a pass, and the age counts from first_seen_at, so a corpus re-read (a
    # new SCHEMA_VERSION, prompt or model: ~31 h at W1) reads FAIL until it drains — expected.
    # Warn at 48h, fail at 168h: a week of a portal's prose unread is the lane being dead, the
    # failure nothing else can see.
    "text_extraction_lag_warn_hours": 48,
    "text_extraction_lag_fail_hours": 168,
    # How many of the newest migrations to probe. 25 spans several weeks of this
    # repo's cadence, so a drift is caught long before the window rolls past it,
    # while keeping the probe to a few hundred catalog lookups.
    "migration_drift_window": 25,
    # Portal-URL contract (docs/design/portal-listing-url.md). ABSOLUTE counts of active
    # rows with no source_url, per source: the failure being watched — sreality adds a
    # sub-category code the closed codebook does not know and every new row of it
    # silently gets NULL — would never move a share against ~800k rows, but it moves a
    # count within a day. Crawler portals always emit a URL, so any NULL there is a
    # parser regression.
    "outbound_url_null_warn": 50,
    "outbound_url_null_fail": 500,
    # Weekly live conformance (scripts/verify_outbound_urls.py): a slug defect fails a
    # whole (category_main, category_sub_cb) cell together, a scattered 404 is a
    # delisting rule #3 has not caught up with — so the fail arm is CONCENTRATION.
    "outbound_url_conformance_warn_share": 0.05,
    "outbound_url_conformance_cell_min": 3,
    "outbound_url_conformance_cell_fail_share": 0.67,
    # The suppression rail is a binary invariant, not a gradient: one active
    # suppression whose two identities sit under the same broker means a NO the
    # operator recorded was bypassed. No warn tier — there is no "slightly merged".
    "broker_suppression_violations_fail": 1,
    # --- per-m2 measure plausibility (W9) -----------------------------------
    # Every number below is sized against a live measurement of production on
    # 2026-08-25, BEFORE the W2 backfill healed the two defects — so each one is
    # verified to fire on the bug it was written for and verified NOT to fire on
    # the healthy portals in the same cell. migrations/427 records the full table.
    #
    # Median shift is week-over-week within ONE (source, category_main,
    # category_type) cell, never across portals: cross-portal medians legitimately
    # differ by up to 19x (portal mix), so a peer comparison cannot separate a bug
    # from a rural land catalogue. The noisiest weekly move measurable today —
    # medians of the NEW-ARRIVAL cohort, which swings far harder than the stock
    # medians this check compares — is 1.47x on area and 1.90x on Kc/m2 across 21
    # cells. Warn sits just above that, fail at 3x. The mmreality heal will move
    # that cell 6.96x (area) and 7.45x (Kc/m2), so the gap is wide in both
    # directions: no false alarm from an ordinary week, and a basis flip cannot
    # sneak under it. 200 rows in BOTH weeks or the median is not compared — and
    # 200 rows means 200 rows CARRYING THAT MEDIAN, not 200 rows in the cell. The
    # two are wildly different live: 64 cells clear n_active >= 200, but only 58
    # clear it on area support and 56 on Kc/m2 support, so gating on cell size
    # would compare 6 and 8 cells' medians resting on as few as 9 values —
    # bezrealitky pozemek/prodej is 1 643 active rows, 9 areas, a 17.6x spread
    # across those 9, and two ordinary delistings move it past the 3x fail.
    "ppm2_median_shift_warn_ratio": 2.0,
    "ppm2_median_shift_fail_ratio": 3.0,
    "ppm2_median_shift_min_rows": 200,
    # Floor share is an absolute LEVEL, not a jump — the unit-price masquerade has
    # been stationary for the whole life of the portals carrying it, so there is no
    # jump to detect. Live: ceskereality komercni/pronajem 20.0%, realitymix 19.0%,
    # realitymix pozemek/pronajem 15.7%, remax 11.3%, bazos 6.7% — against idnes and
    # sreality at 0.5% on the same cell. fail 10% indicts the first four, warn 5%
    # ambers bazos, and the two portals with a working per-area guard stay green.
    "ppm2_basis_floor_share_warn": 0.05,
    "ppm2_basis_floor_share_fail": 0.10,
    "ppm2_basis_floor_min_rows": 100,
    # Location payload-shape drift (W4's standing P6 check): share of rows FIRST SEEN in
    # the trailing window whose payload has the shape the location contracts cannot
    # read — sreality's `locality` object not post-cutover, bezrealitky's `ruianId` key
    # absent. A fresh row always gets a detail fetch within the hour, so after a
    # source-side cutover the share climbs as elapsed/window: with 48 h, one 6-hourly
    # lane tick later it reads 12.5%, above fail — the bell rings on the first tick. A
    # 7-day window would need ~1.4 days to cross the same line. The standing noise is
    # the odd truncated payload (0 of 8,280 sampled on 2026-09-08), so warn sits at 3%.
    # min_rows 30 lets bezrealitky's ~5.9k-row inventory score most windows.
    "location_payload_shape_drift_warn": 0.03,
    "location_payload_shape_drift_fail": 0.10,
    "location_payload_shape_drift_min_rows": 30,
    "location_payload_shape_drift_window_hours": 48,
    # Area divergence: share of rows carrying BOTH areas whose values differ by more
    # than the view's 10% material band. RE-MEASURED 2026-09-17 (W21), because the
    # numbers this was sized against were taken before W1 and both had moved: the
    # ONLY non-zero cell in the whole corpus is now mmreality dum/prodej at 57.1% of
    # 2 638 pairs, and its 7d arm is 0.0% of 150 — the live parser has been right
    # since W1 and what remains is 1 515 legacy rows still carrying the plot as their
    # headline, which the W21 heal clears. realitymix byt, the 10.5% cell the 20%
    # warn tier was chosen to leave green, now reads 0.0%; every other cell is 0.0%.
    # The tiers are NOT tightened onto that floor while the one real offender is still
    # pending a heal — a threshold moved to fit a corpus mid-repair calibrates on the
    # repair, not on the noise. Revisit after the heal runs: with every cell at 0.0%
    # there is room to go to 5%/20%.
    "area_divergence_share_warn": 0.20,
    "area_divergence_share_fail": 0.40,
    "area_divergence_min_rows": 100,
    # Derived-sum side column (W21, migration 534): the share of rows carrying all
    # three areas whose `estate_area` equals `area_m2 + usable_area` within 1%. A side
    # column holding a SUM the page computed is not a measurement — mmreality shipped
    # exactly that for years by reading `totalArea` (= parcelArea + usableArea) as the
    # parcel, and neither a null-check nor the divergence arm above can see it (the
    # column is populated, and the headline was already the interior). Live 2026-09-17,
    # mmreality is the top two cells — dum/pronajem 5.5% of 55 rows (below the floor)
    # and dum/prodej 2.6% of 1 123, the worst SCORED cell — over a background where no
    # other cell exceeds 1.4%. Those are coincidences (the relation only bites where a
    # parcel happens to equal its interior), so read the tiers as a FORWARD guard sized
    # on a real ranking: warn at 5% is ~2x the worst scored cell and ~3.5x the
    # background, fail at 10% is a cell that cannot be coincidence.
    "estate_sum_share_warn": 0.05,
    "estate_sum_share_fail": 0.10,
    "estate_sum_min_rows": 100,
    # Coverage: the share of a cell's active rows the measure has NO INPUT for (no
    # price, or no positive area). Severity is a property of the ARM, not of the
    # number. The stock arm can only WARN: the live offenders were the four sreality
    # `pozemek` cells at 100.0% and bezrealitky pozemek/prodej at 99.5% — three parsers
    # kept the parcel out of `area_m2`, which is the only area the measure reads. W17
    # closed that (the plot reaches `derive_headline_area` from every portal, and a one-off
    # heal — since replaced by `scripts/reparse.py` — fixed the 52,183 stored rows), so those cells should
    # clear; a land cell with no portal area at all stays a visible gap by charter (a NULL
    # measure is never a guess), amber and named rather than a red tile nobody can clear.
    # The next
    # cell down is realitymix ostatni/pronajem at 89.4%, so 0.95 separates "this
    # cell has no measure" from ordinary portal incompleteness with 5.6pp of
    # headroom. The 7d arm FAILS at 0.90: a gap that large among the rows that
    # arrived this week is a parser regression in flight, and the worst live 7d gap
    # over cells with 200+ new rows is 0.358 — so the fail tier has 54pp of headroom
    # and stays reserved for the case the other three axes go QUIET on.
    "ppm2_coverage_gap_warn": 0.95,
    "ppm2_coverage_gap_fail_7d": 0.90,
    "ppm2_coverage_min_rows": 200,
    # Floor convention: the mean (portal storey − idnes storey) over sibling pairs.
    # A whole-storey offset is the defect (W8 measured +0.82..+1.05 on the six ground=1
    # portals against +0.20 ceskereality / +0.10 bazos, the two that were already
    # canonical), so the tiers sit between the two populations. The measure has a real
    # noise floor — a sibling pair is a price/area/disposition match, not a proven
    # duplicate — and it is NOT the same size on every portal: ceskereality reads +0.20
    # against bazos's +0.10. That gap is NOT sample conditioning, though the portal does
    # never state the ground storey (0 of its 34,363 floored rows read 0): restricting
    # the idnes side to floor >= 1 too moves it only +0.199 -> +0.167 (n=7,166 of 7,394,
    # measured 2026-09-22). What is left is a real sub-population — 925 of its pairs sit
    # at exactly +1 against 92 below 0, a 10:1 asymmetry bazos does not have (565:264) —
    # i.e. ceskereality carries an UNEXPLAINED one-storey defect on ~12 % of its pairs,
    # tracked as an open finding in the W8 hand-over §6. warn is 0.35 to hold that portal
    # amber-free while it is investigated, not because its reading is certified: simulated
    # on the live corpus the six converted portals land -0.16..+0.06, well inside 0.25, so
    # the wider tier protects exactly one portal and that portal is the open question.
    # fail at 0.5 is half a storey — unreachable by noise, reachable only by a portal on
    # the other scale. min_pairs 40 lets maxima score on its ~49 pairs and still refuses
    # a mean of one or two adverts.
    "floor_convention_delta_warn": 0.35,
    "floor_convention_delta_fail": 0.50,
    "floor_convention_min_pairs": 40,
    # --- jobs (platform alarm): every scheduled machine, one cell per job ------------------
    # One red loses a whole cycle at a 24 h gap: location_registry_load's lone 09-01 timeout.
    "jobs_gh_long_cycle_h": 24,
    # 35 of migration 462's 63 red streaks were lone; recompute_mf_yields' 3rd red came 09-13.
    "jobs_gh_streak_fail": 3,
    # Late past 3 x the job's own p90 start gap: GitHub delivers about a fifth of cron's runs.
    "jobs_gh_gap_p90_multiple": 3,
    # recent_starts accumulates from wave 3, so the p90 arm is Not measured until 10 starts.
    "jobs_gh_gap_min_starts": 10,
    # The canary's measured 13.3 h maximum gap plus margin; the old check's 46 fails were real.
    "jobs_poller_stale_h": 18,
    # pg_cron keeps time: 2 of a job's last 3 finished runs failing is the job, not a blip.
    "jobs_cron_fail_of_3": 2,
    # 3 median start gaps with no start is two missed cycles, never a scheduler's jitter.
    "jobs_cron_gap_multiple": 3,
    # Under 5 starts in 7 days a job's gap is unknown, so its lateness arm is Not measured.
    "jobs_cron_gap_min_starts": 5,
    # Never late or failing under 15 min: each of ~11 deploys a day pauses lanes under 2 min.
    "jobs_lane_floor_s": 900,
    # 15 min of worker uptime before a lane that has not woken or passed can be judged.
    "jobs_lane_boot_grace_s": 900,
    # Eight portals at up to 120 s each make ~16 min an honest drain pass (the old stall warn).
    "jobs_lane_inflight_warn_s": 1200,
    # LANE_PASS_TIMEOUT_SECONDS: past it the worker abandons the pass (a wedge sat nine hours).
    "jobs_lane_inflight_fail_s": 1800,
    # Hourly-or-slower lanes pass about once per boot, so any failing_since there is a Problem.
    "jobs_lane_daily_interval_s": 3600,
    # Half the passes since the previous result failed: autodedup's failed_passes 6 on 10-02.
    "jobs_lane_flap_share": 0.5,
    # ...over at least 3 passes, so one failure in two passes is not a flap.
    "jobs_lane_flap_min_passes": 3,
    # --- capacity (platform alarm): is the database, and the worker's memory, keeping up ----
    # browse-list-rebuild ran 15-28 x its ~9.9 s reference from 08-17: Watch at 3 x and 30 s.
    "capacity_p50_warn_multiple": 3,
    "capacity_p50_warn_floor_s": 30,
    # Problem at 4 x and 60 s; a trailing 28-day baseline read recovered by early September.
    "capacity_p50_fail_multiple": 4,
    "capacity_p50_fail_floor_s": 60,
    # Watch at a p95 of half the schedule interval: at twice that, runs overlap.
    "capacity_p95_interval_warn": 0.5,
    # 80 % of interval or statement_timeout: browse-list-rebuild hit 1,800 s 09-26..10-03.
    "capacity_p95_interval_fail": 0.8,
    # A whole post-reference week (one weekly p50) before the relative arm may judge.
    "capacity_reference_min_days": 7,
    # The first full day on the 2XL instance (p50 41-100 s); re-blessing is a dated PR. A rail fails once it is over 90 days old.
    "CAPACITY_REFERENCE_FROM": _dt.date(2026, 10, 17),
    # Timed-out health checks per 24 h: up to ~12 %/day in the saturation, 0 since 10-10.
    "capacity_timeout_share_warn": 0.05,
    "capacity_timeout_share_fail": 0.20,
    # The saturation month's oldest open transaction was 18.3 min.
    "capacity_open_xact_warn_min": 60,
    "capacity_open_xact_fail_min": 360,
    # Connections against max_connections: live 29 of 380.
    "capacity_connections_warn": 0.7,
    "capacity_connections_fail": 0.9,
    # The worker's peak RSS against its container limit: live 7.4 of 22.9 GB.
    "capacity_memory_warn": 0.8,
    "capacity_memory_fail": 0.9,
    # --- llm (platform alarm): are the providers serving, and is spend sane ----------------
    # 20 % of 20+ calls in 24 h while the provider's newest call errors; 5 of 5 onsets real.
    "llm_error_share_warn": 0.2,
    "llm_error_share_min_calls": 20,
    # 5+ attempts, no success in an hour: a refusing provider books $0 (the 11-day outage).
    "llm_starved_min_attempts": 5,
    # Normal spend is $22-32 a day; 130, not 90, is what production carried since mig 297.
    "llm_spend_24h_warn_usd": 130,
    "llm_spend_24h_fail_usd": 150,
    # A credit outage stays red after its callers back off: 72 h, never a 24 h window.
    "llm_lookback_h": 72,
    # --- scraping (portal alarm): is every portal still finding and fetching its ads ------
    # Watch past 3 G (the median walk-start gap, 14 d: crawlers 7.15-7.33 h, sreality 4.42 h).
    "scraping_walk_watch_gaps": 3,
    # Problem past 6 G (crawlers ~43 h, sreality 26.5 h); no unit crossed it in the 14-day replay.
    "scraping_walk_fail_gaps": 6,
    # Under 10 finished gaps in 14 d a portal is scored on the slowest portal's G, never dropped.
    "scraping_walk_fallback_min_gaps": 10,
    # A unit walked lately with no end-reached walk in 14 d is a Problem whatever its G.
    "scraping_walk_stale_days": 14,
    # "Lately" is 30 d: a unit absent from every walk for 30 d has left its portal's walk.
    "scraping_walk_recent_days": 30,
    # Inflow: Problem under 20 % of the same 24 h 7/14/21/28 d back (0 in a 14-day backtest).
    "scraping_inflow_fail_share": 0.2,
    # Watch under 50 %: same-window baselines absorb the 30-45 % weekend dip.
    "scraping_inflow_warn_share": 0.5,
    # A spike past 300 % of the baseline is Watch, never Problem.
    "scraping_inflow_spike_share": 3.0,
    # Under 20 a day 7-day windows are compared; under 20 a week the cell is Not measured.
    "scraping_inflow_min_baseline": 20,
    # Fetch errors, Wilson LB of the ids attempted in 24 h: Watch past 2 % (worst day 0.18 %).
    "scraping_fetch_err_warn": 0.02,
    # Problem past 10 %: the old 25 % false FAIL's poison id is 1 of ~6,060 ids (0.02 %) here.
    "scraping_fetch_err_fail": 0.10,
    # Under 200 ids the window widens to 7 d, else the cell is Not measured.
    "scraping_fetch_err_min_ids": 200,
    # Missing, declared − collected at an end-reached walk: Watch past 2 % (walks: 99.96-100 %).
    "scraping_missing_warn": 0.02,
    # Problem past 5 %: a parser dropping cards on a walk that still reaches the portal's end.
    "scraping_missing_fail": 0.05,
    # A unit declaring under 500 ads is not scored: 10 missing ads would read 2 % there.
    "scraping_missing_min_declared": 500,
    # --- publishing (portal alarm): do discovered ads reach Browse, with their photos -----
    # Ingest (oldest NEW row) and changes (CHANGED/FAILURE): Watch past 3 h (7-day max 122 min).
    "publishing_queue_warn_h": 3,
    # Problem past 6 h: a starved NEW queue (08-17) fails here; a politeness burst never does.
    "publishing_queue_fail_h": 6,
    # Visible: Wilson LB of new active ads not in browse_list past 2 % (2 of 2,374 on 10-17).
    "publishing_visible_warn": 0.02,
    # Problem past 10 %; queued ads are ingest's, so the 10-13 burst (1,110 ads waiting, oldest 119 min) stays green.
    "publishing_visible_fail": 0.10,
    # Under 50 ads the window widens to 72 h, else the cell is Not measured.
    "publishing_visible_min_n": 50,
    # Stock: active properties missing from browse_list past 0.1 % (live 20 of 362,883).
    "publishing_stock_warn": 0.001,
    # Problem when the Wilson LB is past 1 %: the 09-14 Browse drop read ~87 %.
    "publishing_stock_fail": 0.01,
    # The Problem arm needs at least 200 properties behind the share.
    "publishing_stock_min_n": 200,
    # Photos of ads first written 1-24 h ago: Wilson LB not stored past 2 % (1 of 30,137 today).
    "publishing_photos_unstored_warn": 0.02,
    # Problem past 10 %: the iDNES throttle took new ads' given-up photos from 5 % to 52 %.
    "publishing_photos_unstored_fail": 0.10,
    # Under 200 photos the photo arms are Not measured.
    "publishing_photos_min_n": 200,
    # Stored narrower than min_px: Watch past 10 % (today 0-2.8 %).
    "publishing_photos_narrow_warn": 0.10,
    # Problem past 50 %: a thumbnail-template regression drives it towards 100 %.
    "publishing_photos_narrow_fail": 0.50,
    # Narrow is under 600 px wide; stored medians sit at 1,199-1,600 px.
    "publishing_photos_min_px": 600,
    # The oldest dirty property past 4 h, above the sweep's 100-min lease hold (7-day max 3.4 h).
    "publishing_dirty_warn_h": 4,
    # Problem past 8 h: the 08-20 outage held dirty rows for 6 d; 09-15's 165 h was the sweep's age (the
    # dirty queue stayed under 1 h that day), so the dirty arm alone would have missed it: jobs' sweep cell has it.
    "publishing_dirty_fail_h": 8,
    # --- delisting (portal alarm): are gone ads made inactive, on time and correctly ------
    # Outflow: Problem under 20 % of the quietest 24 h within ±3 h of this hour 7/14/21/28 d back
    # (10-10: 0 against 400-1,900).
    "delisting_outflow_fail_share": 0.2,
    # Watch under 50 %; revivals clear inactive_at 48-72 h on, so the ratio reads up to ~7 % high.
    "delisting_outflow_warn_share": 0.5,
    # A spike past 300 % of the busiest windows' median (baseline_busy)...
    "delisting_outflow_spike_share": 3.0,
    # ...and of at least 500 ads is Watch, never Problem.
    "delisting_outflow_spike_min": 500,
    # Under a baseline of 50 a day the cell is Not measured: the smallest portal, by its volume.
    "delisting_outflow_min_baseline": 50,
    # Each week back is read ±3 h around this hour, a drop on its quietest 24 h, a spike on its
    # busiest: fixed windows read Problem when a walk ran 1 h 53 min late (10-05); 2-4 h open the
    # replay's same 13 incidents, 3 h with an hour to spare.
    "delisting_outflow_align_h": 3,
    # Stuck: active ads past effective dwell + 2 × the p90 walk gap + slack; Watch at 25 (live 0).
    "delisting_stuck_warn": 25,
    # Problem at 250: verdict to inactive runs at p90 27.8-35.2 h, under limits of 38.9-47.5 h.
    "delisting_stuck_fail": 250,
    # The hour of slack in that limit.
    "delisting_stuck_slack_h": 1,
    # The oldest VERIFY-priority row: Watch past 6 h (7-day maximum 203 min).
    "delisting_verify_warn_h": 6,
    # Problem past 24 h: a whole day of nominations unchecked.
    "delisting_verify_fail_h": 24,
    # Watch after 2 end-reached walks in a row with deferrals; never Problem (a throttle by design).
    "delisting_deferral_walks": 2,
    # Precision: Wilson LB of 48-72 h flips active again past 10 % (sreality ~7 %, rest ≤ 4.9 %).
    "delisting_precision_warn": 0.10,
    # Problem past 15 %: the blink cohorts read 26-49 %.
    "delisting_precision_fail": 0.15,
    # Under 50 flips the cohort is pooled over 2-7 d.
    "delisting_precision_min_n": 50,
    # --- coverage (portal gauge): do we hold what each portal shows -----------------------
    # Residual (H − P − D) / D: Watch past 2 % (0-0.15 % on idnes, sreality and bazos).
    "coverage_residual_warn": 0.02,
    # Problem past 5 %: ceskereality byt/prodej's 27-day hole read +37 %.
    "coverage_residual_fail": 0.05,
    # A unit declaring under 500 ads is not scored.
    "coverage_residual_min_declared": 500,
    # Cannot be nominated: Watch at 25 per portal (in-flight ads, ~9 on bazos, stay ok).
    "coverage_unnominated_warn": 25,
    # Problem at 100: 422 today, 124 on ceskereality and 298 on realitymix (a filed parser issue).
    "coverage_unnominated_fail": 100,
    # --- the registry ---------------------------------------------------------------------
    # GitHub delivers a scheduled run up to ~13.3 h late, so its keys stale at cadence + 18 h.
    "GITHUB_DELIVERY_SLACK_S": 18 * 3_600,
}

# --- per-check wall clock: scripts/checks/clock.py. The harness keeps its own spellings for
# the clock's names, read by the checks that still live here. ------------------------------
#
# Process start and the registry projection, reserved out of the harness step's limit on top
# of the lane budget (test_the_canary_job_fits_every_github_check_due_at_once).
_JOB_HEADROOM_S = 180.0


# --- per-m2 measure plausibility (W9) --------------------------------------
#
# The four checks below exist because the OTHER health surfaces cannot see the
# defects the per-m2 program fixed. `data_quality_by_source` tests 26 fields for
# IS NOT NULL; both defects produce 100% non-NULL values. A null-check cannot see
# a plot area sitting in a floor-area column, and it cannot see 136 Kc sitting in
# a price column. These read `measure_plausibility_by_source` (migration 427)
# instead, which measures what the value IS rather than whether it exists.


def _cell_key(cell: dict[str, Any]) -> str:
    """Stable (source, category_main, category_type) label — also the key the
    week-over-week baseline is stored under, so it must not change spelling."""
    return (
        f"{cell['source']}/{cell['category_main'] or '?'}/{cell['category_type'] or '?'}"
    )


_STATUS_SEVERITY: dict[str, int] = {"ok": 0, "warn": 1, "fail": 2}


def _worst_status(*statuses: str) -> str:
    """The most severe of several arms' statuses. A check reports the worst thing it
    saw, never an average — an `ok` arm beside a `fail` one certifies nothing."""
    return max(statuses, key=lambda st: _STATUS_SEVERITY.get(st, 0))


def _status_for_share(
    cells: list[dict[str, Any]],
    arms: list[tuple[str, str, str, float, float | None]],
    *,
    min_rows: int,
    skip_category_main: frozenset[str] = frozenset(),
) -> tuple[str, list[str], float, int]:
    """Worst-of a share metric over cells × arms; (status, offenders, worst, scored).

    `arms` is [(label, share_key, count_key, warn, fail)] — every one of these checks
    has two: the whole active stock, and the rows first seen in the trailing 7 days.
    The fresh arm is the one with low detection latency (a regression is ~100% of what
    arrived since it shipped, but only churn-fraction of the stock), the stock arm
    is the one that indicts a defect that has been standing for months. Alarming on
    the worse of the two is what makes the pair catch both a new regression and an
    old one. A cell under `min_rows` on an arm is skipped on that arm, not scored
    zero — a 12-row cell's share is noise, and treating it as clean would let a
    small portal hide.

    Each arm carries its OWN warn/fail because severity is not a property of the
    metric but of the arm: a standing coverage gap is amber (visible, named, nobody
    can fix it today), the SAME share among this week's arrivals is a regression that
    shipped and is red. `fail=None` means the arm can only amber.

    `scored` is the count of (cell, arm) pairs that actually produced a number, and it
    is the whole point of the return tuple: `worst` starts at 0.0, so a corpus where
    every arm was skipped is indistinguishable by value from a corpus that was
    measured and clean. Callers MUST refuse to report `ok` on scored == 0."""
    status = "ok"
    offenders: list[str] = []
    worst = 0.0
    scored = 0
    for cell in cells:
        if cell["category_main"] in skip_category_main:
            continue
        for label, share_key, count_key, warn, fail in arms:
            share, n = cell.get(share_key), cell.get(count_key)
            if share is None or n is None or n < min_rows:
                continue
            scored += 1
            worst = max(worst, float(share))
            if fail is not None and share >= fail:
                status = "fail"
            elif share >= warn:
                if status == "ok":
                    status = "warn"
            else:
                continue
            hard = fail is not None and share >= fail
            gate = "fail" if hard else "warn"
            offenders.append(
                f"{_cell_key(cell)} {label} {share:.1%} of {int(n)} "
                f"({gate} >= {fail if hard else warn:.0%})"
            )
    return status, offenders, worst, scored


def _status_for_median_shift(
    cells: list[dict[str, Any]],
    baseline: dict[str, Any] | None,
    history_days: float | None,
    thresholds: dict[str, Any],
) -> tuple[str, list[str], float]:
    """Week-over-week move of a cell's own median area and median Kc/m2.

    Compared against ITSELF a week ago, never against other portals: cross-portal
    medians differ by up to 19x for legitimate mix reasons (idnes' rural land is
    8.5x the peer Kc/m2 and is not a bug), so a peer arm would be permanently red
    on land and `ostatni` while adding nothing the two direct detectors catch. What
    this arm catches that they cannot is the NEXT basis regression, on the run after
    it ships, in a cell nobody thought to write a detector for.

    Each of the two medians is gated on its own support count in BOTH weeks — the rows
    that actually carry that value — and never on the cell's row count, which is a
    different and much larger number. Returns the number of comparisons actually made:
    `worst` starts at the identity ratio 1.0, so "compared 40 cells, all stable" and
    "compared nothing at all" are the same number and only the count separates them.

    A missing baseline is `ok` for the first week after deploy — there is nothing
    the operator could do about it and a permanent amber trains them to ignore the
    axis — but `warn` after that, because a check that has been erroring for two
    weeks also leaves no baseline and must not read as green."""
    warn_ratio = float(thresholds["ppm2_median_shift_warn_ratio"])
    fail_ratio = float(thresholds["ppm2_median_shift_fail_ratio"])
    min_rows = int(thresholds["ppm2_median_shift_min_rows"])
    if not baseline:
        if history_days is not None and history_days >= 8.0:
            return "warn", [
                "no usable baseline in the 6-14 day window although this check has "
                f"{history_days:.0f} days of history — it has been failing or not running"
            ], 0.0, 0
        return "ok", [], 0.0, 0

    status = "ok"
    offenders: list[str] = []
    worst = 1.0
    compared = 0
    for cell in cells:
        prev = baseline.get(_cell_key(cell))
        if not isinstance(prev, dict):
            continue
        for label, now_key, then_key, now_n_key, then_n_key in (
            ("median area_m2", "median_area_m2", "area", "n_area_valued", "n_area"),
            ("median Kc/m2", "median_price_per_m2", "ppm2", "n_ppm2_valued", "n_ppm2"),
        ):
            now_v, then_v = cell.get(now_key), prev.get(then_key)
            if not now_v or not then_v:
                continue
            # Gate each median on ITS OWN support in both weeks, never on n_active.
            # percentile_cont ignores NULLs, so a cell's median rests only on the rows
            # carrying that value: bezrealitky pozemek/prodej has 1 643 active rows and
            # 9 areas, and gating it on 1 643 compares two medians-of-9 whose live
            # spread is 17.6x — two ordinary delistings move it past the 3.0x fail and
            # ring the bell, with no parser change anywhere. A baseline row written
            # before the counts existed carries neither key; it is skipped rather than
            # falling back to `n`, which would silently restore the same false gate.
            now_n, then_n = cell.get(now_n_key), prev.get(then_n_key)
            if now_n is None or then_n is None or now_n < min_rows or then_n < min_rows:
                continue
            compared += 1
            ratio = max(float(now_v) / float(then_v), float(then_v) / float(now_v))
            worst = max(worst, ratio)
            if ratio >= fail_ratio:
                status = "fail"
            elif ratio >= warn_ratio:
                if status == "ok":
                    status = "warn"
            else:
                continue
            gate = "fail" if ratio >= fail_ratio else "warn"
            offenders.append(
                f"{_cell_key(cell)} {label} {float(now_v):,.0f} vs {float(then_v):,.0f} "
                f"a week ago ({ratio:.2f}x, {gate} >= "
                f"{fail_ratio if gate == 'fail' else warn_ratio:g}x)"
            )
    return status, offenders, worst, compared


# --- checks ----------------------------------------------------------------

_BROKER_SUPPRESSION_SQL = """
select
  (select count(*) from broker_merge_suppressions where lifted_at is null) as active,
  (select count(*) from broker_merge_suppressions where lifted_at is not null) as lifted,
  (select count(*) from broker_merge_suppressions s
     join broker_identities lo on lo.id = s.identity_lo
     join broker_identities hi on hi.id = s.identity_hi
    where s.lifted_at is null and lo.broker_id is not null
      and lo.broker_id = hi.broker_id) as violations
"""


def check_broker_merge_suppression(
    conn: Any, thresholds: dict[str, Any],
) -> dict[str, Any]:
    """Assert the one invariant the suppression rail exists to hold: two identities
    the operator separated (unmerge) or refused (dismiss) never end up under one
    broker again while the suppression is active. The nightly sweep re-derives its
    whole candidate set from broker_identity_contacts, so before the rail an undone
    merge simply came back the next night; a violation here means it was bypassed —
    a lift that should have been recorded, a merge path that skips the rail, or the
    apply-time backstop failing. An explicit operator merge LIFTS the suppression,
    so a legitimate override never shows up as one."""
    row = _fetchone(conn, _BROKER_SUPPRESSION_SQL)
    active, lifted, violations = (
        (0, 0, 0) if row is None
        else (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)))
    fail_at = int(thresholds["broker_suppression_violations_fail"])
    status = "fail" if violations >= fail_at else "ok"
    message = (
        f"{violations} active broker merge suppression(s) are co-located under one "
        "broker — an operator NO was bypassed; check broker_merge_suppressions "
        "against broker_identities.broker_id and the sweep's suppressed_pairs count."
        if status == "fail"
        else f"Broker merge suppressions holding ({active} active, {lifted} lifted)."
    )
    return {
        "check_key": "broker_merge_suppression",
        "status": status,
        "value": violations,
        "details": {"active_suppressions": active, "lifted": lifted,
                    "violations": violations},
        "message": message,
    }


_PLAUSIBILITY_COLS = (
    "source", "category_main", "category_type", "price_per_m2_basis", "n_active",
    "median_area_m2", "median_usable_area", "median_price_per_m2",
    "n_floor_eligible", "floor_null_share", "n_floor_eligible_7d", "floor_null_share_7d",
    "n_area_pairs", "area_divergence_share", "n_area_pairs_7d", "area_divergence_share_7d",
    "n_area_valued", "n_ppm2_valued", "n_active_7d",
    "measure_input_gap_share", "measure_input_gap_share_7d",
    # W21 (migration 534), appended to the view in that order.
    "n_estate_triples", "estate_sum_share",
)

_MEASURE_PLAUSIBILITY_SQL = f"""
select {', '.join(_PLAUSIBILITY_COLS)}
from measure_plausibility_by_source
order by source, category_main, category_type
"""

# One read per run, shared by all four measure checks. The view is a ~12 s
# sequential scan of 386k active listings with an external merge sort; reading it
# four times would quadruple that for four identical answers. Cleared at the top of
# run_checks so a long-lived process (or a test) never serves a stale corpus.
_PLAUSIBILITY_CACHE: dict[str, Any] = {}


def _plausibility_cells(conn: Any) -> tuple[list[dict[str, Any]], str | None]:
    """(cells, unavailable_reason). An unreadable view is reported, never raised:
    between merging this PR and applying migration 427 the relation does not exist
    yet, and three red tiles reading `relation does not exist` would be indistinguishable
    from three real defects: an unreadable source is Not measured, never a red."""
    if "cells" not in _PLAUSIBILITY_CACHE:
        try:
            rows = _fetchall(conn, _MEASURE_PLAUSIBILITY_SQL)
        except Exception as exc:  # noqa: BLE001 — view missing / not granted → warn, never false-fail
            _PLAUSIBILITY_CACHE["cells"], _PLAUSIBILITY_CACHE["error"] = [], str(exc)
        else:
            _PLAUSIBILITY_CACHE["cells"] = [
                {k: (float(v) if isinstance(v, (int, Decimal)) and not isinstance(v, bool) else v)
                 for k, v in zip(_PLAUSIBILITY_COLS, row)}
                for row in rows
            ]
            _PLAUSIBILITY_CACHE["error"] = None
    return _PLAUSIBILITY_CACHE["cells"], _PLAUSIBILITY_CACHE["error"]


def _inert_measure_check(check_key: str, reason: str | None) -> dict[str, Any]:
    """unknown, not ok: a check that cannot read its input has not certified anything."""
    detail = (
        f"measure_plausibility_by_source is unreadable: {reason}"
        if reason else
        "measure_plausibility_by_source returned no rows (no active listings, or the "
        "view's is_platform_admin() gate is failing for this job's role — it needs a "
        "rolbypassrls role with no JWT claims)"
    )
    message = (
        f"Per-m2 plausibility check '{check_key}' is INERT — {detail}. If migration 427 "
        "has not been applied yet, apply it."
    )
    return {"check_key": check_key, "status": "unknown", "value": None,
            "details": {"skipped": detail}, "message": message}


def _unmeasured_check(check_key: str, cells: int, needed: str) -> dict[str, Any]:
    """The other half of `_inert_measure_check`: the view answered, with rows, and not
    one of them could be scored. `_inert_measure_check` only fires on ZERO ROWS, which
    is the rarer accident — the live shape is rows whose measurable content is empty
    (sreality publishes 27k active `pozemek` rows with `area_m2` NULL on every one, so
    those cells carry no floor-eligible rows, no area pairs and no medians). Every arm
    skips them and `worst` never leaves its initial value, so the check would report
    `ok` with a message asserting a fact it never checked. A check that scored nothing
    has certified nothing, so it is unknown; `value` is None so the tile renders an
    em-dash rather than a 0 that reads as a measurement."""
    detail = (
        f"{cells} cell(s) read but not one has {needed} — nothing was verified"
    )
    return {
        "check_key": check_key, "status": "unknown", "value": None,
        "details": {"skipped": detail, "cells_read": cells, "arms_scored": 0},
        "message": (
            f"Per-m2 plausibility check '{check_key}' verified NOTHING — {detail}. "
            "This is a coverage failure, not a clean bill of health: check that the "
            "measure's inputs are still being written."
        ),
    }


def check_ppm2_basis_floor_share(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Watch the share of measurable rows the per-basis floor NULLs, per portal and
    basis. This is the detector for the unit-price masquerade: a portal that writes a
    per-m2 UNIT price into `price_czk` (136 Kc for a commercial rental) produces a
    perfectly non-NULL, perfectly typed number that every presence-based health surface
    calls healthy, and that only the floor inside `measure_price_per_m2` rejects. The
    denominator is rows that have a price, a positive area AND a decidable basis, so a
    portal that stops publishing prices is billed to `ppm2_measure_coverage` — the
    coverage arm — rather than accused of bad prices here."""
    cells, unavailable = _plausibility_cells(conn)
    if not cells:
        return _inert_measure_check("ppm2_basis_floor_share", unavailable)
    warn = float(thresholds["ppm2_basis_floor_share_warn"])
    fail = float(thresholds["ppm2_basis_floor_share_fail"])
    min_rows = int(thresholds["ppm2_basis_floor_min_rows"])
    status, offenders, worst, scored = _status_for_share(
        cells,
        [("of priced rows", "floor_null_share", "n_floor_eligible", warn, fail),
         ("of rows first seen in 7d", "floor_null_share_7d", "n_floor_eligible_7d",
          warn, fail)],
        min_rows=min_rows,
    )
    if not scored:
        return _unmeasured_check(
            "ppm2_basis_floor_share", len(cells),
            f"{min_rows}+ rows carrying a price, a positive area and a decidable basis")
    message = (
        f"{len(offenders)} portal/basis cell(s) lose too much of their price to the "
        f"per-basis floor (worst {worst:.1%}): " + "; ".join(offenders[:6])
        + " — a per-m2 unit price is being written into price_czk; check that portal's "
        "_parse_price against scraper/price_text.is_per_area_price."
        if offenders
        else f"Per-basis price floor healthy (worst {worst:.1%} of priced rows NULLed "
             f"across {scored} scored portal/basis arm(s))."
    )
    return {
        "check_key": "ppm2_basis_floor_share",
        "status": status,
        "value": round(worst * 100, 2),
        "details": {"worst_share": round(worst, 4), "warn": warn, "fail": fail,
                    "min_rows": min_rows, "offenders": offenders,
                    "cells_read": len(cells), "arms_scored": scored},
        "message": message,
    }


def check_area_vs_usable_divergence(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Watch the two ways an area column can be populated and still be the wrong number.

    ARM 1 — `area_m2` against `usable_area` on rows that carry both. The direct detector
    for a portal writing the PLOT into the headline floor-area column, which no presence
    check can see because the column is populated on every row. Measured only over rows
    carrying both areas (a portal that publishes one field is not divergent, it is silent)
    and only above the view's 10% material band (rounding and balcony conventions are not
    a basis error). `pozemek` is skipped by name: under Option A `area_m2` IS the plot for
    land, so divergence there is the correct answer, not a defect.

    ARM 2 (W21, migration 534) — `estate_area` against `area_m2 + usable_area`. A SIDE
    column can be wrong in a way arm 1 structurally cannot see: mmreality's parser read
    the page's own `totalArea` as the parcel for years, and `totalArea` is
    `parcelArea + usableArea` — a derived sum, not a measurement. The headline was already
    the interior, so arm 1 said nothing; the column was populated, so the null-check said
    nothing; and the plot came out 44-50% too large on 1 178 active houses. This arm states
    the invariant instead of the portal: a side column must not BE the sum of the other
    two. Land is skipped here too — `estate_area` is NULL on most land by design, so the
    arm has nothing to score there.

    The two arms carry separate thresholds and are reported separately, because 57% of
    divergence and 2.6% of sum-shaped rows are not the same scale of evidence and one
    `worst` over both would mislabel whichever number won. `value` is THE FAILING ARM's
    number: a tile whose headline figure comes from the clean arm while the status comes
    from the other one is a check reporting a fact it did not fail on."""
    cells, unavailable = _plausibility_cells(conn)
    if not cells:
        return _inert_measure_check("area_vs_usable_divergence", unavailable)
    warn = float(thresholds["area_divergence_share_warn"])
    fail = float(thresholds["area_divergence_share_fail"])
    min_rows = int(thresholds["area_divergence_min_rows"])
    status, offenders, worst, scored = _status_for_share(
        cells,
        [("of both-area rows", "area_divergence_share", "n_area_pairs", warn, fail),
         ("of both-area rows first seen in 7d", "area_divergence_share_7d",
          "n_area_pairs_7d", warn, fail)],
        min_rows=min_rows,
        skip_category_main=frozenset({"pozemek"}),
    )
    sum_warn = float(thresholds["estate_sum_share_warn"])
    sum_fail = float(thresholds["estate_sum_share_fail"])
    sum_min_rows = int(thresholds["estate_sum_min_rows"])
    sum_status, sum_offenders, sum_worst, sum_scored = _status_for_share(
        cells,
        [("of three-area rows whose estate_area IS area_m2 + usable_area",
          "estate_sum_share", "n_estate_triples", sum_warn, sum_fail)],
        min_rows=sum_min_rows,
        skip_category_main=frozenset({"pozemek"}),
    )
    if not scored and not sum_scored:
        return _unmeasured_check(
            "area_vs_usable_divergence", len(cells),
            f"{min_rows}+ non-land rows carrying BOTH area_m2 and usable_area")
    message = (
        f"{len(offenders)} portal/category cell(s) disagree between area_m2 and usable_area "
        f"(worst {worst:.1%}): " + "; ".join(offenders[:6])
        + " — area_m2 is carrying a different physical area than the headline one; check "
        "that parser against scraper/area.derive_headline_area."
        if offenders
        else f"area_m2 agrees with usable_area (worst {worst:.1%} of both-area rows "
             f"across {scored} scored portal/category arm(s))."
    )
    if sum_offenders:
        # Lead with the failing arm when it is the one that decided the status.
        if _STATUS_SEVERITY[sum_status] > _STATUS_SEVERITY[status]:
            message = (
                f"{len(sum_offenders)} portal/category cell(s) carry a DERIVED SUM in "
                f"estate_area (worst {sum_worst:.1%}): " + "; ".join(sum_offenders[:6])
                + " — that portal's parser is reading a page-computed total as the "
                "parcel; check its areas_from_params against the page's own parcel "
                f"label. (area_m2 vs usable_area is clean at worst {worst:.1%}.)"
            )
        else:
            message += (
                f" Also {len(sum_offenders)} cell(s) carry a DERIVED SUM in estate_area "
                f"(worst {sum_worst:.1%}): " + "; ".join(sum_offenders[:3])
                + " — that portal's parser is reading a page-computed total as the "
                "parcel; check its areas_from_params against the page's own parcel label."
            )
    # The headline number follows the SEVERITY, not the arm order: when only the
    # derived-sum arm is in trouble, reporting arm 1's clean 0.0% beside a `fail`
    # status is a tile that contradicts itself.
    overall = _worst_status(status, sum_status)
    value = (sum_worst if _STATUS_SEVERITY[sum_status] > _STATUS_SEVERITY[status]
             else worst)
    return {
        "check_key": "area_vs_usable_divergence",
        "status": overall,
        "value": round(value * 100, 2),
        "details": {"worst_share": round(worst, 4), "warn": warn, "fail": fail,
                    "value_arm": ("estate_sum"
                                  if _STATUS_SEVERITY[sum_status] > _STATUS_SEVERITY[status]
                                  else "area_vs_usable"),
                    "min_rows": min_rows, "offenders": offenders,
                    "cells_read": len(cells), "arms_scored": scored,
                    "estate_sum_worst_share": round(sum_worst, 4),
                    "estate_sum_warn": sum_warn, "estate_sum_fail": sum_fail,
                    "estate_sum_min_rows": sum_min_rows,
                    "estate_sum_offenders": sum_offenders,
                    "estate_sum_arms_scored": sum_scored,
                    "skipped_category_main": ["pozemek"]},
        "message": message,
    }


def check_ppm2_measure_coverage(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Watch the share of active rows the measure has NO INPUT for, per portal and basis.

    The other three axes are ratios over rows that HAVE the inputs, which makes every one
    of them blind in the same direction: a cell with nothing to measure scores no arm and
    is skipped, and a skipped arm is indistinguishable from a clean one. This is the arm
    that looks at the denominator. Live, `sreality` carries 27 174 active `pozemek` rows
    with `area_m2` NULL on all of them (the plot size is in `estate_area`), so four cells
    covering ~7% of the active corpus produce no per-m2 measure at all while the other
    three axes call them healthy — and `data_quality_by_source` cannot see it either,
    grouping by (source, field) with no category grain (sreality `area_m2` reads 71.7%
    populated overall).

    Rows the basis FLOOR rejected are not counted here — they have their inputs and
    `ppm2_basis_floor_share` already indicts them; billing one portal twice for one
    defect is how an operator learns to dismiss both tiles. Cells whose basis is
    undecidable are skipped entirely: there the absent measure is the specified answer.

    Severity splits by arm, not by size. A standing gap ambers — it is real, it is named,
    and nothing shipped today caused it. The same gap among the rows that arrived THIS
    WEEK is a parser regression in flight and fails: that is the case where the old
    behaviour was actively perverse, since a portal that stopped writing `area_m2` would
    have made the divergence and floor axes go QUIET."""
    cells, unavailable = _plausibility_cells(conn)
    if not cells:
        return _inert_measure_check("ppm2_measure_coverage", unavailable)
    warn = float(thresholds["ppm2_coverage_gap_warn"])
    fail_7d = float(thresholds["ppm2_coverage_gap_fail_7d"])
    min_rows = int(thresholds["ppm2_coverage_min_rows"])
    status, offenders, worst, scored = _status_for_share(
        cells,
        [("of active rows", "measure_input_gap_share", "n_active", warn, None),
         ("of rows first seen in 7d", "measure_input_gap_share_7d", "n_active_7d",
          fail_7d, fail_7d)],
        min_rows=min_rows,
    )
    if not scored:
        return _unmeasured_check(
            "ppm2_measure_coverage", len(cells),
            f"{min_rows}+ active rows under a decidable basis")
    message = (
        f"{len(offenders)} portal/basis cell(s) have no per-m2 measure to speak of "
        f"(worst {worst:.1%} of rows with no price or no area): " + "; ".join(offenders[:6])
        + " — the measure is undefined for this cohort; check that the portal writes "
        "area_m2 and price_czk for it (on land area_m2 IS the plot, since W17: a land cell "
        "that reads empty means the portal published no area, or the row predates the heal)."
        if offenders
        else f"The per-m2 measure resolves across the corpus (worst cell {worst:.1%} of "
             f"rows without inputs, across {scored} scored arm(s))."
    )
    return {
        "check_key": "ppm2_measure_coverage",
        "status": status,
        "value": round(worst * 100, 2),
        "details": {"worst_share": round(worst, 4), "warn": warn, "fail_7d": fail_7d,
                    "min_rows": min_rows, "offenders": offenders,
                    "cells_read": len(cells), "arms_scored": scored},
        "message": message,
    }


# The baseline is this check's OWN result row from 6-14 days ago — no new table, no new
# DDL. The canary measures this 6-hourly key two or three times a day, so the window
# holds ~15-25 candidates and a few missed runs cost nothing. Rows whose `details` carries no `cells` object (an errored run
# writes {"error": ...}) are skipped rather than read as an empty baseline. `history_days`
# separates "deployed three days ago" from "has recorded no usable baseline in two weeks",
# which must not both read as green.
_PPM2_BASELINE_SQL = """
select
  (select r.details -> 'cells'
     from pipeline_check_results r
    where r.check_key = 'ppm2_median_shift'
      and r.run_at < now() - interval '6 days'
      and r.run_at > now() - interval '14 days'
      and jsonb_typeof(r.details -> 'cells') = 'object'
    order by r.run_at desc
    limit 1) as baseline_cells,
  (select extract(epoch from (now() - min(r.run_at))) / 86400.0
     from pipeline_check_results r
    where r.check_key = 'ppm2_median_shift') as history_days
"""


def check_ppm2_median_shift(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Watch each (source, category_main, category_type) cell's own median area and median
    Kc/m2 week over week. The two direct detectors above catch the two defect shapes we
    have already seen; this is the general net for the shape we have not — any change that
    moves a portal's headline number without the market moving. It cannot see a defect that
    predates its own baseline, which is exactly why it is not the mmreality detector, and it
    WILL fire when the W2 backfill heals mmreality (6.96x on area) — an expected move, and
    the proof the axis is live."""
    cells, unavailable = _plausibility_cells(conn)
    if not cells:
        return _inert_measure_check("ppm2_median_shift", unavailable)
    min_rows = int(thresholds["ppm2_median_shift_min_rows"])
    row = _fetchone(conn, _PPM2_BASELINE_SQL)
    baseline = row[0] if row and isinstance(row[0], dict) else None
    history_days = float(row[1]) if row and row[1] is not None else None
    status, offenders, worst, compared = _status_for_median_shift(
        cells, baseline, history_days, thresholds)
    # Written for the NEXT run to compare against. `n_active >= min_rows` is only a size
    # prefilter (support can never exceed it) — the two SUPPORT counts travel with the
    # medians so next week's run can gate each median on the rows that actually carry it
    # in BOTH weeks. Keeps `details` a few KB.
    snapshot = {
        _cell_key(c): {
            "n": int(c["n_active"]),
            "area": round(c["median_area_m2"], 2) if c["median_area_m2"] else None,
            "ppm2": round(c["median_price_per_m2"], 2) if c["median_price_per_m2"] else None,
            "n_area": int(c["n_area_valued"] or 0),
            "n_ppm2": int(c["n_ppm2_valued"] or 0),
        }
        for c in cells if (c["n_active"] or 0) >= min_rows
    }
    if offenders:
        message = (
            f"{len(offenders)} per-m2 median(s) moved week-over-week beyond the plausible "
            f"band (worst {worst:.2f}x): " + "; ".join(offenders[:6])
            + " — either a parser changed what it writes, or a backfill just ran; confirm "
            "which before dismissing."
        )
    elif baseline is None:
        message = (
            "Per-m2 medians recorded; no week-old baseline to compare against yet (the "
            "first comparison lands one week after this check's first run)."
        )
    elif not compared:
        # A baseline exists and not one median could be matched against it. Reporting
        # "stable" here would be the loudest lie the axis can tell: it is exactly what a
        # regression that NULLs area_m2 or price_czk platform-wide looks like.
        status = "warn"
        message = (
            f"Per-m2 medians compared NOTHING against the {len(baseline)}-cell baseline "
            f"from a week ago: no cell has {min_rows}+ rows carrying a median in both "
            "weeks. Nothing was verified — check the measure's inputs."
        )
    else:
        message = (
            f"Per-m2 medians stable week-over-week (worst move {worst:.2f}x across "
            f"{compared} median(s) compared)."
        )
    return {
        "check_key": "ppm2_median_shift",
        "status": status,
        "value": round(worst, 3) if compared else None,
        "details": {"worst_ratio": round(worst, 3) if compared else None,
                    "offenders": offenders, "min_rows": min_rows,
                    "medians_compared": compared,
                    "baseline_cells": len(baseline or {}),
                    "history_days": round(history_days, 1) if history_days is not None else None,
                    "cells": snapshot},
        "message": message,
    }


# The floor convention, measured against another portal rather than against labels.
#
# `listings.floor` was a ~50/50 MIX of two Czech conventions until W8: idnes, bazos and
# ceskereality counted the ground storey 0, the other six counted it 1, so one flat read
# 3 from its idnes row and 4 from its sreality row in the same Browse list. Nothing in
# the repo could see it — there was no floor check of any kind — because a wrong-by-one
# storey is perfectly typed, perfectly non-NULL and perfectly plausible.
#
# The measure needs no labels and no LLM: two adverts agreeing on price, area AND
# disposition are the same flat often enough that the MEAN of their floor difference is a
# clean convention signal. idnes is the reference because it is the one portal whose
# parser has always read the Czech word ("2. patro (3. NP)"). A key counts only where the
# side holds ONE storey for it (min = max), so a repeated price/area/disposition triple
# cannot smear the difference.
#
# Measured on the live cluster over ~34.7k pairs: 27.0 / 35.1 / 11.0 / 16.1 s on four
# EXPLAIN (ANALYZE) runs across two days. It is the MOST EXPENSIVE check in the lane and
# by far the most VARIABLE — the plan is a BitmapAnd feeding a Bitmap Heap Scan that
# spills (57k exact + 51k lossy heap blocks, ~116k buffers, ~85 % of them `read` even on
# a warm cluster, so it never stays cached and its cost tracks whatever else is reading
# `listings`) — which is why it is registered LAST among the DB checks and runs
# under the same server-side `statement_timeout` as every other check: on a bad day it is
# cancelled and stored Not measured (`unknown`, timed out), never a false green.
_FLOOR_CONVENTION_SQL = """
WITH k AS MATERIALIZED (
    SELECT source, price_czk, area_m2, disposition, min(floor) AS f
      FROM listings
     WHERE is_active AND category_main = 'byt' AND floor IS NOT NULL
       AND price_czk IS NOT NULL AND area_m2 IS NOT NULL AND disposition IS NOT NULL
     GROUP BY 1, 2, 3, 4
    HAVING min(floor) = max(floor)
)
SELECT o.source, count(*) AS pairs, avg(o.f - r.f)::double precision AS mean_delta
  FROM k o JOIN k r USING (price_czk, area_m2, disposition)
 WHERE r.source = %(reference)s AND o.source <> %(reference)s
 GROUP BY 1
"""

_FLOOR_REFERENCE = "idnes"


def _declared_floor_convention(source: str) -> str | None:
    """The storey scale that portal's floor cell declares, or None for a source that
    has no contract row (a portal added to the DB before its cell exists)."""
    portal = attribute_contract.CONTRACT.get(source)
    return portal["floor"].convention if portal else None


def check_floor_convention(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Is every portal's `floor` on the SAME storey scale (ground = 0)?"""
    # The most expensive read of the lane; on the per-check clock like every statement, so a
    # bad day cancels it as Not measured instead of stalling the run.
    rows = _fetchall(conn, _FLOOR_CONVENTION_SQL, {"reference": _FLOOR_REFERENCE})

    warn = float(thresholds["floor_convention_delta_warn"])
    fail = float(thresholds["floor_convention_delta_fail"])
    min_pairs = int(thresholds["floor_convention_min_pairs"])

    per_source: dict[str, Any] = {}
    offenders: list[str] = []
    scale_offenders: list[str] = []
    status = "ok"
    worst = 0.0
    scored = 0
    for source, pairs, mean_delta in rows:
        delta = float(mean_delta)
        per_source[source] = {"pairs": int(pairs), "mean_delta": round(delta, 3)}
        if int(pairs) < min_pairs:
            continue
        scored += 1
        worst = max(worst, abs(delta))
        if abs(delta) >= fail:
            status = "fail"
        elif abs(delta) >= warn and status != "fail":
            status = "warn"
        if abs(delta) >= warn:
            offenders.append(f"{source} {delta:+.2f} ({pairs} pairs)")
            scale_offenders.append(source)

    if not scored:
        # Rows without a scored arm certify nothing: unknown, and `value` stays None so
        # the tile shows an em-dash rather than a 0 that reads as agreement.
        return {
            "check_key": "floor_convention", "status": "unknown", "value": None,
            "details": {"skipped": f"no portal reached {min_pairs} sibling pairs",
                        "per_source": per_source},
            "message": (
                "Floor-convention check verified NOTHING — no portal reached "
                f"{min_pairs} sibling pairs against {_FLOOR_REFERENCE}. That is a "
                "coverage failure, not a clean bill of health."),
        }

    if offenders:
        # The runbook only applies to a portal whose contract cell says `ground1` — the
        # heal is that conversion. Telling the operator to re-derive ceskereality (cell
        # `ground0`) would shift 34,363 CORRECT rows down one storey, which is exactly
        # what W8 and its hand-over forbid, so the two cases get different sentences.
        convertible = [s for s in scale_offenders
                       if _declared_floor_convention(s) == "ground1"]
        remedy = (
            "Heal it per docs/design/field-capture/handover-autodedup-floor.md §7 "
            "(`python -m scripts.reparse --source <portal> --fields floor --write "
            "--allow-snapshot-deferral`; the bare command is a dry run and `floor` is "
            f"hashed): {', '.join(convertible)}."
            if convertible else
            "None of them declares `ground1`, so re-deriving is NOT the remedy — the "
            "reading itself moved. Check that portal's parser and contract cell first."
        )
        message = (
            f"{len(offenders)} portal(s) are on a different storey scale than "
            f"{_FLOOR_REFERENCE} (worst {worst:.2f} storeys): " + "; ".join(offenders)
            + " — a mean near +1 is the ground=1 reading. " + remedy
        )
    else:
        message = (f"Every portal reads the same storey scale as {_FLOOR_REFERENCE} "
                   f"(worst |mean| {worst:.2f} across {scored} scored portals).")
    return {
        "check_key": "floor_convention",
        "status": status,
        "value": round(worst, 3),
        "details": {"per_source": per_source, "offenders": offenders,
                    "reference": _FLOOR_REFERENCE, "warn": warn, "fail": fail,
                    "min_pairs": min_pairs, "scored": scored},
        "message": message,
    }


# The R8 invariant, read off the oldest UN-EXTRACTED row: a "nothing extracted in N hours"
# alarm needs a baseline the outage itself erodes, while that row grows without bound and
# cannot be normalised away. The outage this replaces is the reason the program exists — the
# deleted enrichment lane selected 223 of 50,205 bazos rows for two months while llm_liveness read
# a global max(called_at), llm_burn_rate's starvation arm needed attempts > 0 and
# worker_lane_stall read only in_flight_s. Every one of those is blind to a lane that
# selects nothing, so this check reads the lane's OWN eligibility predicate
# (`description_extraction.ELIGIBLE_LAG_SQL`, built from `_eligible_where`) and never
# re-spells it: a re-spelling would recreate exactly the blindness it exists to end.
# How stale the oldest eligible row must be before "the lane claimed 0 last pass" is read
# as a wedge rather than as a quiet minute: 12 of the lane's own 5-minute intervals.
_TEXT_EXTRACT_WEDGE_HOURS = 1.0

_TEXT_EXTRACT_LANE_SQL = """
select details -> 'text_extract' as lane,
       extract(epoch from (now() - beat_at)) / 60.0 as beat_age_minutes
  from worker_heartbeats where worker = 'realtime-worker'
"""


def check_text_extraction_lag(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """How long a listing the text lane has not read has been waiting, per portal."""
    from toolkit import description_extraction as text_lane

    # Both reads live in toolkit/ on raw cursors: one transaction, re-armed before each with this
    # check's time left, binds them to its wall clock like every statement of its own.
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"SET LOCAL statement_timeout = '{_statement_timeout_clause()}'")
        model = text_lane.resolve_model(conn)
        cur.execute(f"SET LOCAL statement_timeout = '{_statement_timeout_clause()}'")
        scope = text_lane.lane_scope(conn)
    if not scope or model is None:
        # Not a failure and not a silence: no open gate and no `llm_text` entry IS the
        # lane being off, and it is reported as that rather than as health.
        return {
            "check_key": "text_extraction_lag", "status": "ok", "value": 0,
            "details": {"scope": {k: list(v) for k, v in scope.items()},
                        "model": model},
            "message": (
                "The text lane has nothing open to it"
                + ("" if scope else
                   " (no open contract gate and no `llm_text` entry — the lane reads "
                   "nothing and spends nothing)")
                + ("" if model else f"; app_settings.{text_lane.MODEL_SETTING} is unset")
                + " — no listing is eligible and none is waiting."
            ),
        }
    version = text_lane.extractor_version(model)
    rows = _fetchall(conn, text_lane.ELIGIBLE_LAG_SQL, {"version": version})
    latency = _fetchone(conn, text_lane.EXTRACTION_LATENCY_SQL)
    beat = _fetchone(conn, _TEXT_EXTRACT_LANE_SQL)

    warn_h = thresholds["text_extraction_lag_warn_hours"]
    fail_h = thresholds["text_extraction_lag_fail_hours"]
    per_source: dict[str, Any] = {}
    offenders: list[str] = []
    status = "ok"
    worst = 0.0
    waiting_total = 0
    for source, waiting, oldest_hours, median_hours in rows:
        oldest = float(oldest_hours or 0.0)
        waiting_total += int(waiting)
        per_source[source] = {
            "waiting": int(waiting),
            "oldest_hours": round(oldest, 2),
            "median_hours": round(float(median_hours or 0.0), 2),
        }
        worst = max(worst, oldest)
        if oldest >= fail_h:
            status = "fail"
            offenders.append(f"{source} {oldest:.1f}h ({waiting} waiting)")
        elif oldest >= warn_h:
            if status != "fail":
                status = "warn"
            offenders.append(f"{source} {oldest:.1f}h ({waiting} waiting)")

    # The wedge arm, and the one the thresholds cannot see: rows ARE eligible and the lane
    # is claiming none of them. Either it never registered (the estimation lane's fate —
    # absent from the heartbeat entirely, therefore invisible to every other monitor) or
    # it is running and selecting nothing, which is the 2026-07 outage exactly.
    lane = beat[0] if beat else None
    lane_last = (lane or {}).get("last") or {}
    claimed = int(lane_last.get("claimed") or 0)
    # `claimed` is the LAST pass only, and bazos arrives in bursts (1,940 rows a day over
    # ~147 distinct minutes), so a great many healthy 5-minute passes legitimately claim
    # nothing. Reading that as a wedge would make this a false-red generator — the exact
    # thing this file's docstring says it is here to replace. The durable form of the same
    # signal is the OLDEST eligible row: a lane that is claiming will always have taken
    # everything older than a few of its own intervals.
    wedged = waiting_total > 0 and (
        lane is None or (claimed == 0 and worst > _TEXT_EXTRACT_WEDGE_HOURS))
    if wedged:
        status = "fail"
        offenders.append(
            f"{waiting_total} eligible and the lane claimed "
            + ("nothing — it is not in the heartbeat at all" if lane is None
               else f"{claimed} on its last pass")
        )
    # The abort arm, needing neither an old row nor `claimed == 0`: the provider stopped the
    # last pass (credit, quota, key) and will stop the next alike. 2026-10-02..10 the lane
    # claimed 500 a pass and wrote nothing: the wedge arm never fired, and the check blamed
    # backlog age, never the provider. (Its "Recovered" flaps then were its own statement
    # timeouts, which never reach this arm.)
    fatal = " ".join(str(lane_last.get("fatal") or "").split())[:200]
    if fatal:
        status = "fail"
        offenders.insert(0, f"the provider stopped the lane's last pass ({fatal})")

    p99 = float(latency[1]) if latency and latency[1] is not None else None
    if offenders:
        message = (
            "Prose facts are not being extracted: " + "; ".join(offenders)
            + (f" — the provider refused {lane_last.get('model') or model}: fix that "
               f"account's credit or quota (app_settings.{text_lane.MODEL_SETTING} picks the "
               "model) or, for a key error, OPENAI_API_KEY on the realtime worker's Railway "
               "service." if fatal else
               " — check the realtime worker's text_extract lane and OPENAI_API_KEY on "
               "that Railway service.")
        )
    else:
        message = (
            f"Text extraction healthy (oldest unread advert {worst:.1f}h across "
            f"{len(per_source)} portal(s), {waiting_total} waiting"
            + (f", p99 {p99:.1f} min from first_seen_at against a "
               f"{text_lane.SLO_MINUTES} min SLO" if p99 is not None else "")
            + ")."
        )
    return {
        "check_key": "text_extraction_lag",
        "status": status,
        "value": round(worst, 2),
        "details": {
            "per_source": per_source,
            "offenders": offenders,
            "waiting_total": waiting_total,
            "model": model,
            "extractor_version": version,
            "scope": {k: list(v) for k, v in scope.items()},
            "lane_last": lane_last or None,
            "beat_age_minutes": round(float(beat[1]), 1) if beat and beat[1] else None,
            # Reported, never a threshold of its own — the lag arms above are what ring.
            "p99_minutes_from_first_seen": round(p99, 1) if p99 is not None else None,
            "slo_minutes": text_lane.SLO_MINUTES,
            "extractions_24h": int(latency[0]) if latency else 0,
        },
        "message": message,
    }


# --- what every portal STATES vs what we STORED (field capture W1) ----------
#
# `data_quality_by_source` tests 26 fields for IS NOT NULL and nothing else, so it
# cannot see the defect class this program exists to fix: a value that is present and
# outside every canonical option list (13 live `condition` spellings against a filter
# list of 6), or a cell that has read 0.0% since the parser was written because it
# reads a key the portal never emits (remax `balkon`, ceskereality `vybavení`). This
# check measures BOTH halves per (source, field), against a blessed baseline in
# `data/field_capture/` rather than against a threshold tuned per cell.
#
# The two instruments read the SAME cohort (every active row) on purpose, so they can
# never report two different fill rates for one cell. Who owns what: the view keeps the
# seven probes with no `listings` column of their own (`geom`, `locality`, `street`,
# `property_grouped`, the two condition levels, `source_url`); this check owns the 26
# LISTING_COLUMNS attributes; the 19 they share are the view's to lose in a later wave.
#
# It carries no `pipeline_check_thresholds` entries on purpose: its numbers are sized
# against the measured drift of that cohort (the arithmetic is in `scraper.field_census`),
# and the field-capture program adds no settings.


def check_field_fill_matrix(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Per (source, field): did fill COLLAPSE, or did off-canon validity get WORSE?

    Both arms are relative to the blessed baseline, never to an absolute floor, because
    a floor cannot see either live shape: a cell that is legitimately 0% (remax has no
    balcony key to read) or a partial break (ceskereality's `furnished` key mismatch
    would sit at ~2.5%, not 0%). The baseline records today's zeros and today's off-canon
    values as KNOWN, so day one is green and still says them out loud.

    A blessed cell the live matrix no longer produces is an offender of its own — the
    denominator arm the ppm2 checks learned to carry, because the spine comes from
    `portals.is_enabled` and from a source HAVING active rows, so the loudest possible
    failure (a portal stops writing everything) would otherwise subtract itself from the
    measurement and read as health.

    Third arm, free of SQL: the checked-in per-portal key census going stale. A portal
    that renames a key leaves the census — and every contract gate W2 will build on it —
    agreeing with itself about a key space that no longer exists. That is a warn here,
    deliberately not a CI test: a test keyed on the calendar reds `main` on a date rather
    than on a defect."""
    now = _dt.datetime.now(_dt.timezone.utc)
    rows = _fetchall(conn, field_census.FIELD_MATRIX_SQL)
    live = field_census.reduce_matrix(rows, generated_at=now)
    baseline = field_census.load_baseline()
    fails, warns = field_census.compare_to_baseline(
        live, baseline, inert=attribute_contract.inert_cells())
    stale = field_census.stale_censuses(field_census.load_censuses(), now=now)
    zero_fill = field_census.zero_fill_cells(live)
    never_false = field_census.booleans_never_false(live)
    measured, blessed = len(live["cells"]), len(baseline.get("cells") or {})
    # Which of the live zeros are KNOWN comes from the attribute contract's gap marker
    # and nowhere else — one declaration, not a baseline flag saying the same thing in a
    # second place. A zero whose cell still claims a producer is the loud case: the
    # portal states the fact, the contract says we read it, and nothing arrives.
    gaps = attribute_contract.known_gaps()
    inert = attribute_contract.inert_cells()
    undeclared = sorted(key for key in zero_fill if key not in gaps and key not in inert)

    status = "fail" if fails else ("warn" if warns or stale or undeclared else "ok")
    if fails or warns:
        message = (
            f"{len(fails) + len(warns)} (source, field) cell(s) moved against the blessed "
            f"baseline ({measured} measured of {blessed} blessed): "
            + "; ".join((fails + warns)[:6])
            + f" — re-bless with `{field_census.BLESS_COMMAND}` only once the move is "
            "confirmed to be a fix, not a regression."
        )
    elif stale or undeclared:
        notes = list(stale)
        if undeclared:
            notes.append(
                f"{len(undeclared)} zero-fill cell(s) whose contract cell still claims a "
                f"live producer ({', '.join(undeclared[:4])})"
            )
        message = (
            "Fill and validity are stable, but: " + "; ".join(notes)
            + f" — a stale census is re-run with `{field_census.BLESS_COMMAND}`; an "
            "undeclared zero is a cell that should be filling and is not."
        )
    else:
        message = (
            f"Fill and validity stable across {measured} of {blessed} blessed "
            f"(source, field) cells. {len(zero_fill)} cell(s) carry no value at all and "
            f"{len(never_false)} boolean cell(s) have never been written false — both "
            "recorded, neither judged: which of them is a gap needs W2's contract."
        )
    return {
        "check_key": "field_fill_matrix",
        "status": status,
        "value": len(fails) + len(warns),
        "details": {
            "offenders": fails + warns,
            "stale_census": stale,
            "cells_measured": measured,
            "cells_blessed": blessed,
            # The wave's whole point, reported every run until a contract claims them.
            # Both lists are LIVE, so a cell a later wave repairs stops being named.
            "zero_fill_cells": zero_fill,
            # Split by the contract, so "W4 will wire it" and "this should be filling
            # and is not" stop reading the same.
            "zero_fill_known_gaps": {k: gaps[k] for k in zero_fill if k in gaps},
            "zero_fill_undeclared": undeclared,
            "booleans_never_false": never_false,
            "baseline_generated_at": baseline.get("generated_at"),
        },
        "message": message,
    }


_LOCATION_PAYLOAD_SHAPE_DRIFT_SQL = f"""
    SELECT source, count(*) AS n, count(*) FILTER (WHERE unexpected) AS unexpected
    FROM (
      SELECT source,
             CASE WHEN source = 'sreality'
                  THEN ({SREALITY_SHAPE_CASE_SQL}) <> 'post_cutover'
                  ELSE NOT (raw_json ? 'ruianId')
             END AS unexpected
      FROM listings
      WHERE source IN ('sreality', 'bezrealitky') AND is_active
        AND first_seen_at > now() - make_interval(hours => %(window_hours)s)
    ) fresh
    GROUP BY source
"""

_LOCATION_SHAPE_REMEDY = {
    "sreality": ("the v1 API changed the shape of `locality`; W1 intake is enrolling every "
                 "new row into the refetch cohort, where a refetch cannot fix a NEW shape — "
                 "update the sreality payload shape check in claims_intake.py "
                 "(`sreality_payload_shape`) and re-extract"),
    "bezrealitky": ("new rows arrive without the `ruianId` key; the GraphQL detail query lost "
                    "the field W0 item 0m added — restore it in "
                    "scraper/bezrealitky_client._DETAIL_QUERY (adding it back has no "
                    "retroactive effect: the rows fetched meanwhile need a refetch)"),
}


def check_location_payload_shape_drift(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """W4's standing payload-schema-version check (06 §6.4 W4 gate, P6): the next
    source-side shape change must force re-extraction instead of silently degrading.
    Today a sreality payload of unknown shape classifies `absent`, is routed to the
    refetch cohort and burns five fetches before retiring as `error` — nobody is told.
    This check tells someone. Measured on rows first seen in the trailing window (48 h
    by default), per source: after a cutover the share climbs as elapsed/window, so the
    first 6-hourly tick already reads 12.5% and crosses fail — a 7-day window would sit
    under it for ~1.4 days."""
    warn = float(thresholds["location_payload_shape_drift_warn"])
    fail = float(thresholds["location_payload_shape_drift_fail"])
    min_rows = int(thresholds["location_payload_shape_drift_min_rows"])
    window_hours = int(thresholds["location_payload_shape_drift_window_hours"])
    rows = _fetchall(conn, _LOCATION_PAYLOAD_SHAPE_DRIFT_SQL, {"window_hours": window_hours})
    # `per_source`, not `cells`: that key is the health ladder's per-cell verdict contract.
    per_source = [{"source": s, "n": int(n), "unexpected": int(u),
                   "share": (int(u) / int(n)) if int(n) else None}
                  for s, n, u in rows]
    scored = [c for c in per_source if c["n"] >= min_rows]
    if not scored:
        detail = (f"{len(per_source)} source(s) read, none with {min_rows}+ rows first seen "
                  f"in {window_hours} h — nothing was verified")
        return {"check_key": "location_payload_shape_drift", "status": "unknown", "value": None,
                "details": {"skipped": detail, "per_source": per_source, "arms_scored": 0},
                "message": f"Location payload-shape drift verified NOTHING — {detail}."}
    worst = max(c["share"] for c in scored)
    status = "fail" if worst >= fail else "warn" if worst >= warn else "ok"
    offenders = [f"{c['source']}: {c['share']:.1%} of {c['n']} rows first seen in "
                 f"{window_hours}h — {_LOCATION_SHAPE_REMEDY[c['source']]}"
                 for c in scored if c["share"] >= warn]
    message = (
        f"{len(offenders)} source(s) are landing payloads the location contracts cannot "
        f"read (worst {worst:.1%}): " + "; ".join(offenders)
        if offenders
        else f"Location payload shapes stable (worst {worst:.1%} unexpected across "
             f"{len(scored)} scored source(s))."
    )
    return {
        "check_key": "location_payload_shape_drift",
        "status": status,
        "value": round(worst * 100, 2),
        "details": {"worst_share": round(worst, 4), "warn": warn, "fail": fail,
                    "min_rows": min_rows, "window_hours": window_hours,
                    "per_source": per_source, "arms_scored": len(scored),
                    "offenders": offenders},
        "message": message,
    }


# The location programme's one invariant (CLAUDE.md rule 25): every listing has an answer
# row, and every listing that is not foreign has a town (`obec_kod`). Per portal, absolute
# counts, red when either is not zero — this is the line the S2 contract rewrites drive to
# zero portal by portal and the guard that keeps it there. `country_status <> 'foreign'`
# deliberately counts `undetermined` and `disputed` as Czech: foreign is a determination the
# resolver makes, never a default for "no town found".
#
# `hidden_n` is the W5 consumer rule counted OUTSIDE the audit page (operator ruling
# 2026-09-13): how many listings the consumer surfaces refuse to serve because the store has
# no answer for them. It is the ONE definition
# (location_data.claims_common.SERVED_LOCATION_PREDICATE), negated — never a hand-written
# `geom IS NULL`, which would drift the day the rule gains an arm. Reported per portal and
# never a threshold: it is a WORKLOAD number, not a failure. The check's own red/green is
# `no_row + cz_no_town`.
#
# EVERY LISTING, no WHERE (W15, operator ruling 2026-09-14). This read used to be scoped to
# the served set — live listings, plus the display listing of a live property — and its four
# original counts additionally filtered on `l.is_active`. Both are gone: the location lane
# covers the whole database, so the denominator is the portal's whole corpus. THE PER-PORTAL
# SERIES THEREFORE CHANGED MEANING ON 2026-09-14 and the numbers before and after that date
# are not comparable; `town_share` is the readable one across the step.
#
# W16 (operator ruling 2026-09-14): `town_n` and `hidden_n` are the SHARED step flags,
# rendered from location_data/location_steps.py — the fourth spelling of "has a town" is
# gone. `town_n` therefore now carries dedup's obec RANK floor as well as `obec_kod`
# (measured: zero rows of movement on 2026-09-14, and from now on it is the number dedup can
# block on). `cz_no_town_n` stays the coarser "no obec_kod at all", because THAT is the
# contract gap the S2 rewrites drive to zero, and it is not a chain step.
_LOCATION_STEP_FLAGS = location_steps.step_flags_sql()

# The KÚ arm's one excuse (MF PR-B): an obec holding a KÚ the boundary loader recorded as
# `degenerate_boundary_geometry` at the current version has a hole no address point can be
# placed in, so its address-grain rows may lack a KÚ. `boundary_load_failed` is never one.
_DEGENERATE_KU_OBCE_SQL = """
    SELECT o.code
      FROM registry_load_discrepancies d
      JOIN ruian_admin_units k ON k.level = 'katastralni_uzemi' AND k.code = d.entity_code
                              AND k.valid_to IS NULL
      JOIN ruian_admin_units o ON o.id = k.parent_id
     WHERE d.entity_kind = 'katastralni_uzemi'
       AND d.discrepancy = 'degenerate_boundary_geometry'
       AND d.registry_version_id = (SELECT id FROM registry_versions WHERE is_current)"""

_LOCATION_TOWN_COVERAGE_SQL = f"""
    SELECT l.source,
           count(*)                                                   AS listings_n,
           count(*) FILTER (WHERE ll.listing_id IS NULL)              AS no_row_n,
           count(*) FILTER (WHERE ll.listing_id IS NOT NULL
                              AND ll.country_status <> 'foreign'
                              AND ll.obec_kod IS NULL)                AS cz_no_town_n,
           count(*) FILTER (WHERE {_LOCATION_STEP_FLAGS["has_town"]}) AS town_n,
           count(*) FILTER (WHERE NOT {_LOCATION_STEP_FLAGS["located"]})
                                                                      AS hidden_n,
           count(*) FILTER (WHERE ll.country_status = 'cz'
                              AND gr.is_address_grain
                              AND ll.resolver_version = %s
                              AND ll.katastr_kod IS NULL
                              AND ll.obec_kod NOT IN ({_DEGENERATE_KU_OBCE_SQL}))
                                                                      AS address_no_ku_n
      FROM listings l
      LEFT JOIN listing_location ll ON ll.listing_id = l.id
      LEFT JOIN location_granularity_rank gr ON gr.granularity = ll.granularity
     GROUP BY l.source
     ORDER BY l.source
"""


# Registry liveness, the second arm of the same check: every town above is a code of the
# CURRENT registry version, so a registry that stopped moving ages every answer silently. Its
# two failure shapes are both non-failures to Actions: a monthly run superseded by another
# pending `location-batch` run is cancelled, not failed, and a load killed or refused mid-way
# leaves an inert staged version. Red when the current vintage is over 40 days old (the
# monthly cadence plus a re-dispatch margin) or a NEWER version has sat staged over 24 h (the
# 6 h job plus a re-dispatch). An older staged version — one a later vintage overtook — is
# history, not a stall. No row at all is red too: nothing is current.
REGISTRY_MAX_AGE_DAYS = 40
REGISTRY_MAX_STAGED_HOURS = 24

_REGISTRY_LIVENESS_SQL = """
    SELECT c.label AS current_label, current_date - c.source_date AS age_days,
           s.label AS staged_label,
           extract(epoch FROM now() - s.loaded_at) / 3600 AS staged_hours
      FROM registry_versions c
      LEFT JOIN LATERAL (
            SELECT n.label, n.loaded_at
              FROM registry_versions n
             WHERE NOT n.is_current AND n.source_date > c.source_date
             ORDER BY n.loaded_at
             LIMIT 1) s ON true
     WHERE c.is_current
"""


def _registry_liveness(conn: Any) -> tuple[list[str], dict[str, Any]]:
    """(problems, details) of the registry-liveness arm; no problems when it is green."""
    row = _fetchone(conn, _REGISTRY_LIVENESS_SQL)
    if row is None:
        return ["no current registry version"], {"current": None}
    label, age_days, staged, staged_hours = row
    details = {"current": label, "age_days": int(age_days), "staged": staged,
               "staged_hours": None if staged_hours is None else round(float(staged_hours), 1)}
    problems = []
    if age_days > REGISTRY_MAX_AGE_DAYS:
        problems.append(f"current {label} is {int(age_days)} days old "
                        f"(> {REGISTRY_MAX_AGE_DAYS})")
    if staged_hours is not None and staged_hours > REGISTRY_MAX_STAGED_HOURS:
        problems.append(f"{staged} has sat staged {float(staged_hours):.0f} h "
                        f"(> {REGISTRY_MAX_STAGED_HOURS} h) — re-dispatch location_registry_load")
    return problems, details


def check_location_town_coverage(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Red when ANY listing has no answer row, or any non-foreign one has no town. Absolute
    counts, no threshold: the invariant is zero, and a number that is not zero names the
    portal whose contract has to change.

    The KÚ arm (MF PR-B) is the same kind of invariant: a Czech ADDRESS-GRAIN row
    (`location_granularity_rank.is_address_grain`, never the enum's order) resolved by the
    current `RESOLVER_VERSION` has a `katastr_kod`, because an address point lies in exactly
    one KÚ — so a non-zero count is the 2026-09-13 failure class (KÚ geometry missing at the
    registry version) caught directly. Rows still at an older version are the re-resolve's
    backlog, not a defect; an obec holding a degenerate KÚ is excused.

    `hidden` (W5) is the one number here that is not a defect: the listings the consumer rule
    refuses to serve. It rides the same read so the number the audit page shows is visible
    from the pipeline verifier too, and it never changes the status. The registry-liveness
    arm (`_registry_liveness`) turns it red on its own."""
    stalled, registry = _registry_liveness(conn)
    stall_note = f" Registry stalled: {'; '.join(stalled)}." if stalled else ""
    rows = _fetchall(conn, _LOCATION_TOWN_COVERAGE_SQL, (RESOLVER_VERSION,))
    cells = [{"source": s, "listings": int(n), "no_row": int(nr), "cz_no_town": int(nt),
              "town": int(t), "hidden": int(h), "address_no_ku": int(nk),
              "town_share": (int(t) / int(n)) if int(n) else None}
             for s, n, nr, nt, t, h, nk in rows]
    no_row = sum(c["no_row"] for c in cells)
    cz_no_town = sum(c["cz_no_town"] for c in cells)
    address_no_ku = sum(c["address_no_ku"] for c in cells)
    hidden = sum(c["hidden"] for c in cells)
    listings = sum(c["listings"] for c in cells)
    if not cells:
        # Nothing read is Not measured; a stalled registry is still a measured Problem.
        return {"check_key": "location_town_coverage",
                "status": "fail" if stalled else "unknown", "value": None,
                "details": {"skipped": "no listings read", "per_source": [],
                            "registry": registry},
                "message": "Location town coverage verified NOTHING — no listings read."
                           + stall_note}
    offenders = [f"{c['source']}: {c['no_row']:,} without a row, {c['cz_no_town']:,} Czech "
                 f"without a town (of {c['listings']:,})"
                 for c in cells if c["no_row"] or c["cz_no_town"]]
    hidden_by_source = "; ".join(
        f"{c['source']} {c['hidden']:,}"
        for c in sorted(cells, key=lambda c: -c["hidden"]) if c["hidden"]
    )
    missing = no_row + cz_no_town
    status = "fail" if missing or stalled or address_no_ku else "ok"
    message = (
        f"{missing:,} listings have no town "
        f"({no_row:,} without an answer row, {cz_no_town:,} Czech without obec_kod): "
        + "; ".join(offenders)
        if missing
        else f"Every one of {listings:,} listings has an answer row and every Czech one a town."
    )
    if address_no_ku:
        message += (
            f" {address_no_ku:,} Czech address-grain rows at {RESOLVER_VERSION} have no KÚ: "
            + "; ".join(f"{c['source']} {c['address_no_ku']:,}"
                        for c in cells if c["address_no_ku"])
            + "."
        )
    if hidden:
        message += (
            f" Consumers currently hide {hidden:,} unresolved listings ({hidden_by_source})."
        )
    return {
        "check_key": "location_town_coverage",
        "status": status,
        "value": missing,
        # `per_source`, not `cells`: that key is the health ladder's per-cell verdict contract.
        "details": {"listings": listings, "no_row": no_row, "cz_no_town": cz_no_town,
                    "address_no_ku": address_no_ku, "hidden": hidden, "per_source": cells,
                    "offenders": offenders, "registry": registry},
        "message": message + stall_note,
    }


_OUTBOUND_URL_COVERAGE_SQL = """
    SELECT source,
           count(*)                                                       AS active_n,
           count(*) FILTER (WHERE source_url IS NULL)                     AS null_n,
           count(*) FILTER (WHERE source_url IS NULL
                              AND first_seen_at > now() - interval '7 days') AS null_7d
    FROM listings
    WHERE is_active
    GROUP BY source
    ORDER BY source
"""


def check_outbound_url_coverage(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Active rows with no page URL, per portal — an ABSOLUTE count, not a share.

    A listing's `source_url` is a stored fact for all nine portals (W0 of
    docs/design/portal-listing-url.md): the eight crawlers emit the page they fetched,
    sreality assembles its canonical from a closed codebook. The one silent failure is
    sreality adding a sub-category code the codebook does not know — every new row of it
    gets NULL, the SPA shows no chip, and a percentage over ~800k rows never notices.
    A count of active NULLs does, within a day; `null_7d` names the fresh cohort so a
    standing archive gap (rows the reconciler declined) is not mistaken for a regression.
    The remedy is one codebook entry in scraper/sreality_url.py + a reconciler run.
    """
    rows = _fetchall(conn, _OUTBOUND_URL_COVERAGE_SQL)
    warn = int(thresholds["outbound_url_null_warn"])
    fail = int(thresholds["outbound_url_null_fail"])
    per_source = [
        {"source": src, "active_n": int(active), "null_n": int(nulls), "null_7d": int(fresh)}
        for src, active, nulls, fresh in rows
    ]
    worst = max(per_source, key=lambda r: r["null_n"], default=None)
    worst_n = worst["null_n"] if worst else 0
    status = "fail" if worst_n >= fail else "warn" if worst_n >= warn else "ok"
    offenders = [r for r in per_source if r["null_n"] >= warn]
    named = "; ".join(
        f"{r['source']} {r['null_n']} active rows without a URL ({r['null_7d']} first seen "
        f"in 7d)" for r in sorted(offenders, key=lambda r: -r["null_n"])[:4]
    )
    if offenders:
        message = (
            f"{named}. A sreality NULL means a sub-category code outside the closed "
            "codebook (scraper/sreality_url.py) — add the entry; the row fills on its "
            "next detail fetch. A crawler NULL is a parser regression."
        )
    else:
        message = "Every active listing on every portal carries its page URL."
    return {
        "check_key": "outbound_url_coverage",
        "status": status,
        "value": worst_n,
        "details": {"per_source": per_source, "warn": warn, "fail": fail},
        "message": message,
    }


# --- the registry ------------------------------------------------------------

HOSTS = ("realtime-worker", "github-actions", "pg_cron")
CLASSES = ("alarm", "gauge")
DOMAINS = ("scraping", "publishing", "delisting", "dedup", "coverage", "data_quality",
           "platform")

_GITHUB, _WORKER = "github-actions", "realtime-worker"
_TEN_MINUTES, _HALF_HOURLY = 600, 1_800
_HOURLY, _SIX_HOURLY, _WEEKLY = 3_600, 21_600, 604_800
# The worker and pg_cron keep time, so a key they host reads stale after 3 cadences; GitHub's
# keys get their cadence plus THRESHOLDS["GITHUB_DELIVERY_SLACK_S"].
_STALE_CADENCES = 3
# The one check that leaves the database waits on third-party round trips, not on Postgres:
# its budget is declared beside its HEAD loop, which stops itself at it (nothing outside can
# preempt a request).
_OUTBOUND_BUDGET_S = PROBE_BUDGET_S


@dataclass(frozen=True)
class Check:
    """One registered check: its body, how it is judged, where it runs and how often."""

    key: str
    fn: Callable[[Any, dict[str, Any]], dict[str, Any]]
    domain: str
    cls: str
    host: str
    cadence_s: int
    label: str
    unit: str = ""
    owner_path: str = ""
    budget_s: float = _CHECK_BUDGET_S

    @property
    def staleness_budget(self) -> _dt.timedelta:
        """How old the newest result may grow before the page reads the key Not measured."""
        if self.host == _GITHUB:
            return _dt.timedelta(seconds=self.cadence_s + THRESHOLDS["GITHUB_DELIVERY_SLACK_S"])
        return _dt.timedelta(seconds=self.cadence_s * _STALE_CADENCES)

    @property
    def due_after_s(self) -> int:
        """How old the newest result must be before a lane measures this check again."""
        # Spelled in the ladder, whose recovery hold is two of these intervals.
        return _due_after_s(self.cadence_s)


# `run_due` runs a host's due alarms first, then its due gauges, each class in this order, and
# a check its budget does not reach stays due for the next run, so the order is a priority
# inside each class: cheap invariants first, the expensive reads late and the outbound probe
# last. The page's `sort` is the position here.
CHECKS: tuple[Check, ...] = (
    # FIRST of the gauges: 2026-09-11 21:02 lost the last seven checks to the budget, this
    # one among them, and the invariant the location programme is measured by is one
    # indexed join.
    Check("location_town_coverage", check_location_town_coverage, "data_quality", "gauge",
          _GITHUB, _SIX_HOURLY, "Location coverage", unit="ads",
          owner_path="/location-quality"),
    # The worker's health lane measures these seven (scripts/checks/: platform, then the portal
    # checks). GitHub keeps what needs it: migration_drift reads a checkout, the outbound probe
    # an address that is not the scrapers'.
    Check("jobs", check_jobs, "platform", "alarm", _WORKER, _TEN_MINUTES, "Jobs",
          unit="cells"),
    Check("capacity", check_capacity, "platform", "alarm", _WORKER, _HALF_HOURLY, "Capacity",
          unit="cells"),
    Check("llm", check_llm, "platform", "alarm", _WORKER, _TEN_MINUTES, "LLM providers",
          unit="cells"),
    Check("scraping", check_scraping, "scraping", "alarm", _WORKER, _TEN_MINUTES, "Scraping",
          unit="cells"),
    Check("publishing", check_publishing, "publishing", "alarm", _WORKER, _TEN_MINUTES,
          "Publishing", unit="cells"),
    Check("delisting", check_delisting, "delisting", "alarm", _WORKER, _TEN_MINUTES,
          "Delisting", unit="cells"),
    Check("coverage", check_coverage, "coverage", "gauge", _WORKER, _HOURLY, "Coverage",
          unit="cells"),
    Check("broker_merge_suppression", check_broker_merge_suppression, "dedup", "alarm",
          _GITHUB, _HOURLY, "Broker merge rulings", unit="pairs",
          owner_path="/brokers/review"),
    Check("ppm2_median_shift", check_ppm2_median_shift, "data_quality", "alarm", _GITHUB,
          _SIX_HOURLY, "Kč/m² median shift", unit="×"),
    Check("ppm2_basis_floor_share", check_ppm2_basis_floor_share, "data_quality", "gauge",
          _GITHUB, _SIX_HOURLY, "Kč/m² price floor", unit="%"),
    Check("area_vs_usable_divergence", check_area_vs_usable_divergence, "data_quality",
          "gauge", _GITHUB, _SIX_HOURLY, "Area vs usable area", unit="%"),
    Check("ppm2_measure_coverage", check_ppm2_measure_coverage, "data_quality", "alarm",
          _GITHUB, _SIX_HOURLY, "Kč/m² coverage", unit="%"),
    Check("migration_drift", check_migration_drift, "platform", "alarm", _GITHUB, _HOURLY,
          "Migration drift", unit="files"),
    Check("location_payload_shape_drift", check_location_payload_shape_drift, "scraping",
          "alarm", _GITHUB, _SIX_HOURLY, "Location payload shape", unit="%"),
    Check("outbound_url_coverage", check_outbound_url_coverage, "data_quality", "gauge",
          _GITHUB, _SIX_HOURLY, "Ads with no portal link", unit="ads"),
    # The three expensive reads queue behind the cheap ones: field_fill_matrix ~12 s over the
    # whole active stock, text_extraction_lag ~10 s once a gate is open, floor_convention
    # 11-35 s (a cross-portal self-join over every active byt, the most variable read here).
    Check("field_fill_matrix", check_field_fill_matrix, "scraping", "alarm", _GITHUB,
          _SIX_HOURLY, "Field fill", unit="cells"),
    Check("text_extraction_lag", check_text_extraction_lag, "publishing", "gauge", _GITHUB,
          _SIX_HOURLY, "Description reading", unit="h"),
    Check("floor_convention", check_floor_convention, "data_quality", "gauge", _GITHUB,
          _SIX_HOURLY, "Floor convention"),
    # LAST of the alarms: the check that leaves the database, so a lane that runs out of budget
    # drops the outbound probe before any database alarm.
    Check("outbound_url_conformance", check_outbound_url_conformance, "scraping", "alarm",
          _GITHUB, _WEEKLY, "Portal links open the right page", unit="%",
          budget_s=_OUTBOUND_BUDGET_S),
)


# The hosts this harness projects: those with a Check (`project_registry` refuses any other).
# pg_cron's registry row is the watcher's own (migration 620); projecting that host would retire it.
_HARNESS_HOSTS = tuple(dict.fromkeys(c.host for c in CHECKS))

# --- the harness ---------------------------------------------------------------

# Only rows since the key's declared_at count, as for the page and the ladder: a newly registered
# or re-declared key is due in the very run that projects it, not a cadence later.
_DUE_SQL = """
select k.key,
       (select max(r.run_at) from pipeline_check_results r
         where r.check_key = k.key
           and r.run_at >= coalesce(h.declared_at, '-infinity'::timestamptz))
  from unnest(%(keys)s::text[]) as k(key)
  left join health_checks h on h.key = k.key
"""

_HEALTH_CHECKS_ROWS_SQL = """
select key, host, cadence_s, declared_at
  from health_checks
 where key = any(%(keys)s::text[])
"""

_HEALTH_CHECKS_UPSERT_SQL = """
insert into health_checks
       (key, domain, class, host, cadence_s, staleness_budget, label, unit, owner_path,
        sort, declared_at)
values (%(key)s, %(domain)s, %(class)s, %(host)s, %(cadence_s)s, %(staleness_budget)s,
        %(label)s, %(unit)s, %(owner_path)s, %(sort)s, %(declared_at)s)
on conflict (key) do update
   set domain = excluded.domain, class = excluded.class, host = excluded.host,
       cadence_s = excluded.cadence_s, staleness_budget = excluded.staleness_budget,
       label = excluded.label, unit = excluded.unit, owner_path = excluded.owner_path,
       sort = excluded.sort, declared_at = excluded.declared_at
"""

_HEALTH_CHECKS_RETIRE_SQL = """
delete from health_checks
 where host = %(host)s and not (key = any(%(keys)s::text[]))
"""


def due_checks(conn: Any, host: str, now: _dt.datetime) -> list[Check]:
    """This host's checks, in registry order, whose newest result since their declared_at (any
    status) is absent or at least `due_after_s` old."""
    mine = [c for c in CHECKS if c.host == host]
    if not mine:
        return []
    with conn.cursor() as cur:
        cur.execute(_DUE_SQL, {"keys": [c.key for c in mine]})
        newest = {key: run_at for key, run_at in cur.fetchall()}
    return [c for c in mine
            if newest.get(c.key) is None
            or (now - newest[c.key]).total_seconds() >= c.due_after_s]


def project_registry(conn: Any, host: str, now: _dt.datetime) -> dict[str, int]:
    """Upsert this host's CHECKS into health_checks; delete its rows that no Check declares."""
    if host not in _HARNESS_HOSTS:
        raise ValueError(f"no Check is hosted on {host!r}: its registry rows (the watcher's, "
                         "migration 620) are not this harness's to retire")
    declared = [(sort, c) for sort, c in enumerate(CHECKS) if c.host == host]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_HEALTH_CHECKS_ROWS_SQL, {"keys": [c.key for _, c in declared]})
        prior = {key: (row_host, cadence_s, declared_at)
                 for key, row_host, cadence_s, declared_at in cur.fetchall()}
        rows: list[dict[str, Any]] = []
        for sort, c in declared:
            old = prior.get(c.key)
            # declared_at anchors "not measured yet" on the page, so only a new key or a
            # changed host or cadence restarts it; a re-projection keeps the clock.
            kept = old is not None and old[0] == c.host and old[1] == c.cadence_s
            rows.append({
                "key": c.key, "domain": c.domain, "class": c.cls, "host": c.host,
                "cadence_s": c.cadence_s, "staleness_budget": c.staleness_budget,
                "label": c.label, "unit": c.unit, "owner_path": c.owner_path,
                "sort": sort, "declared_at": old[2] if kept else now,
            })
        if rows:
            cur.executemany(_HEALTH_CHECKS_UPSERT_SQL, rows)
        cur.execute(_HEALTH_CHECKS_RETIRE_SQL,
                    {"host": host, "keys": [c.key for c in CHECKS]})
        deleted = cur.rowcount
    return {"upserted": len(rows), "deleted": max(deleted or 0, 0)}


def _is_timeout_error(exc: BaseException) -> bool:
    """A statement cancelled by our own per-check budget, vs a genuinely broken check."""
    name = type(exc).__name__
    if name in ("QueryCanceled", "QueryCanceledError"):
        return True
    return "canceling statement due to statement timeout" in str(exc)


def _timed_out_result(key: str, budget_s: float, elapsed_s: float,
                      exc: BaseException) -> dict[str, Any]:
    """A check cancelled by its wall clock measured nothing: unknown, never fail.

    "I could not measure this" is a different claim from "I measured this and it is broken",
    and a lane under DB pressure would otherwise manufacture a wall of false reds at the
    exact moment the operator needs to read the real one."""
    return {
        "check_key": key,
        "status": "unknown",
        "value": None,
        "details": {"timed_out": True, "budget_seconds": round(budget_s, 1),
                    "elapsed_seconds": round(elapsed_s, 1), "error": str(exc)},
        "message": (
            f"Check '{key}' ran out of its {budget_s:.0f}s budget after {elapsed_s:.0f}s and "
            "was cancelled, so it is Not measured this run (never healthy). Usually DB "
            "pressure; if it persists, the check's query needs work."
        ),
    }


def _errored_result(key: str, exc: BaseException) -> dict[str, Any]:
    """A check that raised measured nothing: unknown, with the error kept."""
    return {
        "check_key": key,
        "status": "unknown",
        "value": None,
        "details": {"errored": True, "error": str(exc)},
        "message": f"Check '{key}' raised an error, so it is Not measured this run: {exc}",
    }


def lane_budget_for(checks: Sequence[Check]) -> float:
    """A lane owes every check it runs that check's own budget, so none can starve another."""
    return float(sum(c.budget_s for c in checks))


def run_checks(
    conn: Any, thresholds: dict[str, Any], *, checks: Sequence[Check],
    on_result: Callable[[dict[str, Any]], None] | None = None,
    lane_budget_s: float | None = None,
) -> list[dict[str, Any]]:
    """Run each check in isolation on its own wall clock, handing on_result each result as it
    lands; a check the lane budget does not reach gets no result, so it stays due."""
    results: list[dict[str, Any]] = []
    # The measure checks share one read of measure_plausibility_by_source per run, never
    # across runs.
    _PLAUSIBILITY_CACHE.clear()
    _LANE.reset()
    if lane_budget_s is not None:
        _LANE.start(lane_budget_s)
    try:
        for i, check in enumerate(checks):
            budget = _LANE.arm_for_check(check.budget_s)
            if budget <= 0:
                LOG.warning("lane budget %.0fs spent; not reached, so still due: %s",
                            lane_budget_s or 0.0, ", ".join(c.key for c in checks[i:]))
                break
            started = _time.monotonic()
            try:
                result = check.fn(conn, thresholds)
            except Exception as exc:  # noqa: BLE001 - one broken check never ends the lane
                if _is_timeout_error(exc):
                    spent = _time.monotonic() - started
                    LOG.error("check %s timed out after %.1fs of its %.0fs budget",
                              check.key, spent, budget)
                    result = _timed_out_result(check.key, budget, spent, exc)
                else:
                    LOG.exception("check %s errored", check.key)
                    result = _errored_result(check.key, exc)
            elapsed = _time.monotonic() - started
            if not isinstance(result.get("details"), dict):
                result["details"] = {}
            result["details"]["elapsed_ms"] = int(elapsed * 1000)
            results.append(result)
            # Persisted per check, so a job killed mid-lane keeps everything it measured.
            if on_result is not None:
                try:
                    on_result(result)
                except Exception:  # noqa: BLE001 - persisting one result must not end the lane
                    LOG.exception("could not persist result for check %s", check.key)
    finally:
        _LANE.reset()
    return results


def insert_result(conn: Any, result: dict[str, Any], run_at: _dt.datetime) -> None:
    """Persist ONE result; its message rides inside `details`, beside any cells it carries."""
    details = dict(result.get("details") or {})
    if result.get("message"):
        details["message"] = result["message"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pipeline_check_results (run_at, check_key, status, value, details) "
            "VALUES (%s, %s, %s, %s, %s::jsonb)",
            (run_at, result["check_key"], result["status"],
             result.get("value"), json.dumps(details)),
        )


def run_due(
    conn: Any, thresholds: dict[str, Any], host: str, *, now: _dt.datetime,
    on_result: Callable[[Any, dict[str, Any], _dt.datetime], None] = insert_result,
    lane_budget_s: float | None = None,
) -> list[dict[str, Any]]:
    """Measure this host's due checks, alarms before gauges, storing each result as it lands;
    `lane_budget_s` caps the pass (the worker lane's 360 s), else the due budgets sum."""
    # Due-ness is read from the stored results, so a restarted lane re-measures nothing, and
    # the sort is stable: registry order holds inside each class.
    due = sorted(due_checks(conn, host, now), key=lambda c: c.cls != "alarm")
    if due:
        LOG.info("DUE host=%s %d of %d checks: %s", host, len(due),
                 sum(1 for c in CHECKS if c.host == host), ", ".join(c.key for c in due))
    budget = lane_budget_for(due)
    if lane_budget_s is not None:
        budget = min(budget, lane_budget_s)
    return run_checks(conn, thresholds, checks=due,
                      on_result=lambda result: on_result(conn, result, now),
                      lane_budget_s=budget)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Select and compute the due checks and log them; write nothing "
                             "(no registry rows, no result rows).")
    parser.add_argument("--host", default=_GITHUB, choices=(_GITHUB,),
                        help="The host whose registry rows this run projects and whose due "
                             "checks it measures: GitHub's only (the worker's health lane "
                             "calls run_due itself).")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not os.environ.get("SUPABASE_DB_URL"):
        print("ERROR: SUPABASE_DB_URL is not set.", file=sys.stderr)
        return 2

    run_at = _dt.datetime.now(_dt.timezone.utc)
    written = 0
    with connect() as conn:
        # A dispatch from a branch measures with that branch's CHECKS but never projects them:
        # its registry would delete main's keys from the page until main's next run.
        ref = os.environ.get("GITHUB_REF_NAME", "main")
        if not args.dry_run and ref != "main":
            LOG.info("REGISTRY host=%s not projected: this run is from %s, not main",
                     args.host, ref)
        elif not args.dry_run:
            projected = project_registry(conn, args.host, run_at)
            LOG.info("REGISTRY host=%s upserted=%d deleted=%d",
                     args.host, projected["upserted"], projected["deleted"])

        def _persist(db: Any, result: dict[str, Any], at: _dt.datetime) -> None:
            """Log this result and, unless dry-running, store it the moment it completes."""
            nonlocal written
            LOG.info(
                "CHECK %s status=%s value=%s",
                result["check_key"], result["status"], result.get("value"),
            )
            if args.dry_run:
                return
            insert_result(db, result, at)
            written += 1

        results = run_due(conn, THRESHOLDS, args.host, now=run_at, on_result=_persist)
    LOG.info("verify_pipeline host=%s measured %d checks, wrote %d rows",
             args.host, len(results), written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
