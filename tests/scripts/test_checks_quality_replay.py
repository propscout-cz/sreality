"""Input replays: production history fed through parsing and the quality gauges, then the ladder."""

# Each fixture is an aggregate exported read-only from production on 2026-10-19 (UTC to the
# second; counts, shares, medians and category names, no ids, URLs or ad text). Refresh them
# read-only, never by hand, from the SQL below (each export wrapped its rows as CSV text beside
# that text's md5, computed server-side, and the file was written once the two md5s agreed). The
# checks run at their own cadence over the window the exports hold, parsing and data_quality every
# 6 h, location every 6 h from the export on, text_extraction hourly, and each result reaches the
# ladder (toolkit.system_alerts.reconcile) as production feeds it. pipeline_check_results is pruned
# at 35 days, so no replay reaches further back than 09-14.
#
# tests/fixtures/health_quality_churn_28d.csv (day,source,snapshots,first_snapshots,resnapshots,
# active_n,active_at), 14:14:53Z, 261 rows: listing_snapshots per portal and UTC day, an ad's first
# snapshot apart from its later ones, and the portal's active ads at the day's last
# data_quality_snapshots capture (10-19 is partial, to the export).
#   with s as (select l.source, date_trunc('day', s.scraped_at at time zone 'UTC') as day,
#                     pr.prev is null as is_first
#                from listing_snapshots s join listings l on l.id = s.listing_id
#                left join lateral (select 1 as prev from listing_snapshots p
#                                    where p.listing_id = s.listing_id
#                                      and p.scraped_at < s.scraped_at limit 1) pr on true
#               where s.scraped_at >= '2026-09-21' and s.scraped_at < now()),
#        agg as (select day, source, count(*) as snaps, count(*) filter (where is_first) as firsts
#                  from s group by 1, 2),
#        act as (select distinct on (date_trunc('day', captured_at at time zone 'UTC'), source)
#                       date_trunc('day', captured_at at time zone 'UTC') as day, source, n_active,
#                       captured_at
#                  from data_quality_snapshots
#                 where field = 'price_czk' and captured_at >= '2026-09-21'
#                 order by 1, 2, captured_at desc)
#   select g.day, p.source, coalesce(a.snaps, 0), coalesce(a.firsts, 0),
#          coalesce(a.snaps - a.firsts, 0), t.n_active, t.captured_at
#     from generate_series(timestamp '2026-09-21', date_trunc('day', now() at time zone 'UTC'),
#                          interval '1 day') g(day)
#    cross join (select source from portals where kind = 'scraper' and is_enabled) p
#     left join agg a on a.day = g.day and a.source = p.source
#     left join act t on t.day = g.day and t.source = p.source order by 1, 2;
#
# tests/fixtures/health_quality_measure_view_now.csv (quality.PLAUSIBILITY_COLUMNS), 14:18:35Z, 108
# rows: the measure view exactly as parsing and data_quality read it (active ads only), shares to
# 6 dp and medians to 2 dp.
#   select <quality.PLAUSIBILITY_COLUMNS> from measure_plausibility_by_source
#    order by source, category_main nulls first, category_type nulls first;
#
# tests/fixtures/health_quality_measure_gaps_28d.csv (at,source,category_main,category_type,stock_n,
# gap_stock_n,arrivals_7d,gap_7d_n), 14:20:04Z, 2,958 rows: the view's gap inputs at the end of
# each UTC day 09-21..10-18 and at the export, for the 102 cells whose basis is decidable: ads in
# the stock, those with no price or no positive area, and the same over ads first seen in 7 d.
#   with b as (select source, category_main as cat, category_type as deal,
#                     (price_czk is not null and area_m2 > 0) as elig, first_seen_at as t0,
#                     case when is_active then 'infinity'::timestamptz else inactive_at end as t1
#                from listings
#               where (is_active or inactive_at > timestamptz '2026-09-22 00:00+00')
#                 and measure_price_per_m2_basis(category_main, category_type) is not null),
#        ends as (select generate_series(timestamptz '2026-09-22 00:00+00',
#                                        timestamptz '2026-10-19 00:00+00', interval '1 day') as at
#                 union all select now())
#   select e.at, b.source, b.cat, b.deal, count(*), count(*) - count(*) filter (where b.elig),
#          count(*) filter (where b.t0 > e.at - interval '7 days'),
#          count(*) filter (where b.t0 > e.at - interval '7 days')
#            - count(*) filter (where b.t0 > e.at - interval '7 days' and b.elig)
#     from b join ends e on b.t0 <= e.at and b.t1 > e.at
#    group by 1, 2, 3, 4 order by 1, 2, 3 nulls first, 4 nulls first;
#
# tests/fixtures/health_quality_stock_medians_6w.csv (at,source,category_main,category_type,basis,n,
# area,ppm2,n_area,n_ppm2), 14:21:03Z, 754 rows: the stock's median area and per-m² price (in the
# unit of `basis`) at seven weekly instants 09-07..10-19, with each median's support.
#   with b as (select source, category_main as cat, category_type as deal, area_m2::numeric as area,
#                     measure_price_per_m2(price_czk::numeric, area_m2::numeric, category_main,
#                                          category_type) as ppm2,
#                     first_seen_at as t0,
#                     case when is_active then 'infinity'::timestamptz else inactive_at end as t1
#                from listings where is_active or inactive_at > timestamptz '2026-09-07 00:00+00'),
#        ends as (select generate_series(timestamptz '2026-09-07 00:00+00',
#                                        timestamptz '2026-10-19 00:00+00',
#                                        interval '7 days') as at)
#   select e.at, b.source, b.cat, b.deal, measure_price_per_m2_basis(b.cat, b.deal), count(*),
#          round(percentile_cont(0.5) within group (order by b.area)::numeric, 2),
#          round(percentile_cont(0.5) within group (order by b.ppm2)::numeric, 2),
#          count(b.area), count(b.ppm2)
#     from b join ends e on b.t0 <= e.at and b.t1 > e.at
#    group by 1, 2, 3, 4 order by 1, 2, 3 nulls first, 4 nulls first;
#
# tests/fixtures/health_quality_legacy_medians_35d.csv (run_at,source,category_main,category_type,
# basis,n,area,ppm2,n_area,n_ppm2), 14:13:01Z, 2,310 rows: the view's medians as the retiring
# ppm2_median_shift stored them at its last measured run of each UTC day 09-15..10-19 (none on
# 09-17 and 09-18), cells of at least 200 active ads, '?' a missing category; basis added here.
#   with runs as (select distinct on (date_trunc('day', run_at at time zone 'UTC')) run_at,
#                        details -> 'cells' as cells
#                   from pipeline_check_results
#                  where check_key = 'ppm2_median_shift'
#                    and jsonb_typeof(details -> 'cells') = 'object'
#                  order by date_trunc('day', run_at at time zone 'UTC'), run_at desc)
#   select r.run_at, split_part(e.key, '/', 1), split_part(e.key, '/', 2),
#          split_part(e.key, '/', 3),
#          measure_price_per_m2_basis(nullif(split_part(e.key, '/', 2), '?'),
#                                     nullif(split_part(e.key, '/', 3), '?')),
#          e.value ->> 'n', e.value ->> 'area', e.value ->> 'ppm2', e.value ->> 'n_area',
#          e.value ->> 'n_ppm2'
#     from runs r cross join lateral jsonb_each(r.cells) e order by 1, 2, 3, 4;
#
# tests/fixtures/health_quality_location_now.csv (source,ads,no_row,cz_no_town,town,hidden,
# address_no_ku,ads_le_24h,missing_le_24h,inactive,inactive_no_row,inactive_cz_no_town,
# inactive_address_no_ku), 14:22:01Z, 9 rows: per portal the location gauge's counts over active
# ads first written more than 24 h ago, the younger ones publishing judges, and the inactive
# stock (a dated fact the gauge never reads). The same statement read the registry: current
# ruian:2026-09-30, source date 09-30, loaded 10-01 16:55:41Z, 19 d old, nothing newer staged.
#   with base as (select l.source, l.is_active,
#                        l.first_seen_at < now() - make_interval(hours => 24) as aged,
#                        ll.listing_id is null as no_row,
#                        (ll.listing_id is not null and ll.country_status <> 'foreign'
#                         and ll.obec_kod is null) as cz_no_town,
#                        <has_town> as town, <located> as located,
#                        (ll.country_status = 'cz' and gr.is_address_grain
#                         and ll.resolver_version = 'resolver:v5.7' and ll.katastr_kod is null
#                         and ll.obec_kod not in (<quality._DEGENERATE_KU_OBCE_SQL>)) as no_ku
#                   from listings l left join listing_location ll on ll.listing_id = l.id
#                   left join location_granularity_rank gr on gr.granularity = ll.granularity)
#   select source, count(*) filter (where is_active and aged),
#          ... and no_row, ... and cz_no_town, ... and coalesce(town, false), ... and not located,
#          ... and coalesce(no_ku, false), count(*) filter (where is_active and not aged),
#          count(*) filter (where is_active and not aged and (no_row or cz_no_town)),
#          the same four counts over not is_active
#     from base group by source order by 1;
#  with <has_town> and <located> = location_data.location_steps.step_flags_sql()'s two flags.
#
# tests/fixtures/health_quality_text_lane_28d.csv (day,source,arrivals,active_now,read_1h,read_24h,
# read_ever,read_v2,given_up,p50_min,p95_min,readings_ok,readings_failed), 14:23:14Z, 29 rows: per
# UTC day, the lane's scope (bazos) ads with a description first seen that day, those active at
# the export, those with an error-free reading at any version within 1 h, 24 h, ever and at a
# SCHEMA_VERSION 2 version, those given up (unread, a failed row at 5 attempts), minutes to the
# first reading (p50, p95), and the scope's readings by created_at day (a retry updates in place,
# so a late success is booked on its first attempt's day).
#   with arr as (select l.source, date_trunc('day', l.first_seen_at at time zone 'UTC') as day,
#                       l.first_seen_at as seen, l.is_active, r.*
#                  from listings l cross join lateral (
#                       select min(e.created_at) filter (where e.extracted -> 'error' is null)
#                                as first_ok,
#                              min(e.created_at) filter (where e.extracted -> 'error' is null
#                                and e.extractor_version like '2:%') as first_ok_v2,
#                              bool_or(e.extracted -> 'error' is not null
#                                and coalesce((e.extracted ->> 'attempts')::int, 1) >= 5)
#                                as gave_up
#                         from listing_description_enrichments e where e.listing_id = l.id) r
#                 where l.source = any(<scope>) and l.description <> ''
#                   and l.first_seen_at >= '2026-09-21' and l.first_seen_at < now())
#   select day, source, count(*), count(*) filter (where is_active),
#          count(*) filter (where first_ok <= seen + interval '1 hour'),
#          count(*) filter (where first_ok <= seen + interval '24 hours'), count(first_ok),
#          count(first_ok_v2), count(*) filter (where first_ok is null and gave_up),
#          percentile_cont(0.5) and (0.95) within group (order by extract(epoch from
#            first_ok - seen) / 60), <the scope's error-free and failed rows by created_at day>
#     from arr group by 1, 2 (on a grid of every day 09-21..10-19);
#  with <scope> = ARRAY(the active llm_text contract entries' sources UNION ALL the open gates).
#
# tests/fixtures/health_quality_text_stock_now.csv (source,waiting_active,waiting_inactive,
# oldest_active_h,readings_24h,readings_24h_current,failed_24h,given_up_current), 14:23:57Z, 1 row:
# what the lane's own predicate holds waiting, split active and inactive, and its readings of 24 h.
#   select l.source, count(*) filter (where l.is_active), count(*) filter (where not l.is_active),
#          max(now() - l.first_seen_at) filter (where l.is_active), <the readings of 24 h, at any
#          version and at this one, the failed ones, the given up at this version>
#     from listings l where <description_extraction._eligible_where(_OPEN)> group by 1;
#  with %(version)s = '2:ea016577:gpt-5.6-luna' (extractor_version of app_settings' model).
#
# tests/fixtures/health_quality_text_lag_runs_35d.csv (run_at,status,artifact,waiting_total,
# readings_24h,extractor_version), 14:24:22Z, 96 rows: every retained run of the retiring
# text_extraction_lag, the only record of what the lane held waiting over time.
#   select run_at, status, <artifact>, details ->> 'waiting_total', details ->> 'extractions_24h',
#          details ->> 'extractor_version'
#     from pipeline_check_results where check_key = 'text_extraction_lag' order by run_at;
#
# tests/fixtures/health_quality_payload_now.csv (source,ads_48h,unreadable_48h,ads_28d,
# unreadable_28d), 14:25:15Z, 3 rows: active ads first seen in 48 h and in 28 d per raw_json
# portal, and those whose location payload claims_common.PAYLOAD_SHAPE_PREDICATES cannot read.
#   select l.source, count(*) filter (where l.first_seen_at > now() - interval '48 hours'),
#          count(*) filter (where l.first_seen_at > now() - interval '48 hours' and not readable),
#          count(*), count(*) filter (where not readable)
#     from listings l
#    where l.source = any(<the three portals>) and l.is_active
#      and l.first_seen_at > now() - interval '28 days'
#    group by 1 order by 1;
#  with readable = case l.source when <portal> then coalesce(<its predicate>, false) ... end.
#
# tests/fixtures/health_quality_legacy_runs_35d.csv (check_key,run_at,status,value,artifact,
# elapsed_ms), 14:12:12Z, 1,047 rows: every retained run of the nine retiring keys, a harness
# artifact the legacy harness stored as warn named apart (not_run, skipped, timed_out).
#   select check_key, run_at, status, value, <artifact>, details ->> 'elapsed_ms'
#     from pipeline_check_results where check_key = any(<the nine keys>) order by 1, 2;
#  with <artifact> = the first of 'not_run', 'timed_out', 'skipped', 'errored', 'error' that
#  `details` holds as a key, else ''.
#
# Scalars the replays take from the same day's live reads (10-19 13:11-13:19Z): the description
# lane's two gaps between readings, 09-20 20:56Z -> 09-22 19:03Z (46.1 h, the W1 cutover) and
# 10-02 06:57Z -> 10-12 15:44Z (248.8 h, the provider-credit outage); and the active ads with no
# portal link, 11, all sreality, none first written in 7 d (13:18:52Z).
#
# SYNTHETIC, each where no durable input exists or the export stops:
#  - fill and vocabulary: no per-day field matrix is kept (data_quality_snapshots is frozen and
#    holds 26 columns, not the matrix's fields), so every tick reads the matrix the 10-19 re-bless
#    wrote (234 cells over 9 portals) as the live one, and the key censuses are the re-blessed
#    files;
#  - payload shape: a rewrite replaces raw_json, so every tick reads the 14:25:15Z counts in 48 h
#    (sreality 0 of 1,798 unreadable, bezrealitky 0 of 170, mmreality 0 of 124; 0 of 25,183, 2,424
#    and 1,882 over 28 d);
#  - the churn lead-in: a tick's 28 windows reach back before 09-21, which was not exported, so a
#    day before 09-21 reads as the portal's median day of the 28 complete ones (idnes 754,
#    sreality 2,159.5, ceskereality 325, bazos 288.5, realitymix 271, bezrealitky 89, mmreality 76,
#    remax 56, maxima 3);
#  - data_quality: the floor, divergence and estate-sum columns exist only as of the view's export,
#    so every tick holds them (the retiring ppm2_basis_floor_share failed all 92 of its measured
#    runs in the 35 days, its worst cell at 13.05-14.39 %, so the condition stood throughout); the
#    measure-gap columns are the daily series; the link counts are 13:18:52Z's on every tick;
#  - location: day one is the 14:22:01Z reading (43 ads with no town: ceskereality 39, realitymix 3,
#    mmreality 1); it holds through the gauge's day-one reminder (24 h), and from the pass after
#    it (30 h in) every one of them has a town (the synthetic zero);
#  - the description lane's reading times (no per-ad stamp was exported): an arrival day's ads are
#    spread evenly over it; outside the two gaps its read_1h ads are read 5 min after first seen and
#    the rest evenly from 1 h to the delay that puts the day's p95 where the export read it (24 h
#    when read_1h is 95 % or more); an ad whose reading would fall in a gap is read after the
#    restart, newest first, at the restart day's own pace (09-22: 12,750 readings in the 4.95 h
#    after 19:03Z; 10-12: 6,164 in the 8.27 h after 15:44Z). The stock's active part is the
#    arrivals the model holds unread (a lower bound: a version bump's re-read of the active stock is
#    on no record); before the retiring check's first count of what waits (09-22 21:24:47Z) those
#    arrivals are the whole count and the readings in 24 h are none until 19:03Z, then the W1
#    lane's pace since;
#  - the synthetic incidents: (a) from 10-08 00:00Z every new sreality byt/prodej ad loses its price
#    (1,804 ads first seen in 7 d on 10-08, 41 with no measure; the cohort loses its measure at a
#    seventh a day, the stock with it); (b) sreality's 10-10 re-snapshots at 4x its median day
#    (8,638 for 687) and its 10-16 ones at 7x (15,116.5 for 956).
#
# Stated limits. The reconstructions read today's prices and areas (a heal since an instant shows
# at it), so the weekly medians cannot show a heal; the retiring check's stored medians can, and the
# heal replay reads them in the view's place from 09-16. A tick holds the newest daily or weekly
# instant at or before it. An ad delisted and revived counts active through its spell. The churn
# windows spread each day's re-snapshots evenly over it; a reading at any version stands for one
# at the SCHEMA_VERSION in force then (the 09-30 schema bump's re-read of recent arrivals is not on
# record); the text flow's p95 fact is not replayed. The 35-day prune bounds every replay.

from __future__ import annotations

import bisect
import datetime as _dt
import functools
import json
import statistics
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

from location_data.claims_common import SOURCES
from scraper import field_census
from scripts.checks import clock, common
from scripts.checks import quality as qu
from scripts.verify_pipeline import THRESHOLDS
from tests.scripts.test_checks_parsing import _matrix
from tests.scripts.test_checks_platform import _Conn
from tests.scripts.test_checks_platform_replay import _fixture, _incidents, _ticks, _told, _utc
from tests.toolkit.test_system_alerts import _FakeConn as _Ladder
from toolkit import description_extraction as text_lane
from toolkit.measures import ppm2_basis
from toolkit.system_alerts import reconcile

_MINUTE, _HOUR, _DAY = (_dt.timedelta(minutes=1), _dt.timedelta(hours=1), _dt.timedelta(days=1))
_SIX_HOURS = 6 * _HOUR
_Grain = tuple[str, str | None, str | None]
_Result = tuple[_dt.datetime, dict[str, Any]]

_GAPS, _WEEKLY = "health_quality_measure_gaps_28d.csv", "health_quality_stock_medians_6w.csv"
_LEGACY_MEDIANS = "health_quality_legacy_medians_35d.csv"
_START, _END = _utc("2026-09-22T00:00:00Z"), _utc("2026-10-19T12:00:00Z")
_HEAL_START = _utc("2026-09-16T00:00:00Z")
_MODEL = "gpt-5.6-luna"
# The synthetic incidents (a) and (b) of the header.
_BROKEN, _BROKEN_SINCE = ("sreality", "byt", "prodej"), _utc("2026-10-08T00:00:00Z")
_STORMS = (("sreality", "2026-10-10", 4.0), ("sreality", "2026-10-16", 7.0))


def _part(text: str) -> str | None:
    """A fixture's category part: empty (or the legacy store's '?') is a missing one."""
    return None if text in ("", "?") else text


def _cells(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["subject"]: c for c in result["details"]["cells"]}


def _arm(results: list[_Result], subject: str) -> list[tuple[_dt.datetime, dict[str, Any]]]:
    """One cell's readings, tick by tick."""
    return [(at, c) for at, r in results for c in r["details"]["cells"] if c["subject"] == subject]


def _flagged(results: list[_Result], part: str = "") -> list[tuple[_dt.datetime, str, str]]:
    """Every Watch or Problem reading whose subject holds `part`, tick by tick."""
    return [(at, c["subject"], c["status"]) for at, r in results for c in r["details"]["cells"]
            if c["status"] in ("warn", "fail") and part in c["subject"]]


def _onsets(ladder: _Ladder, key: str) -> list[tuple[str, _dt.datetime, _dt.datetime | None]]:
    """Every incident the replay opened for `key`: (subject, opened, resolved)."""
    return [(i["subject"], i["first_seen_at"], i["resolved_at"]) for i in _incidents(ladder, key)]


def _notices(ladder: _Ladder, key: str, kind: str) -> list[_dt.datetime]:
    """When the ladder told `key` one kind of rung (onset, reminder, recovery)."""
    return [t.at for t in ladder.told if (t.key, t.kind) == (key, kind)]


# --- a check's own history, as pipeline_check_results answers it ----------------------------------


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class _History:
    """A check's stored results (each through JSON, as jsonb stores it), answering its own reads."""

    def __init__(self) -> None:
        self.results: list[_Result] = []

    def store(self, at: _dt.datetime, result: dict[str, Any]) -> None:
        self.results.append((at, json.loads(json.dumps(result))))

    def trend(self, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        """common.TREND_SQL: the newest measured result's cells."""
        measured = [r for _, r in self.results if r["status"] != "unknown"]
        return [(measured[-1]["details"]["cells"],)] if measured else []

    def lookback(self, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        """common.TREND_LOOKBACK_SQL: per subject the newest measured series it lacks."""
        held: dict[str, Any] = {}
        for at, r in self.results:
            if r["status"] == "unknown" or at < params["since"]:
                continue
            for c in r["details"]["cells"]:
                series = c["facts"].get("trend")
                if c["subject"] not in params["known"] and isinstance(series, list):
                    held[c["subject"]] = series
        return sorted(held.items())

    def median_baseline(self, at: _dt.datetime, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        """quality._MEDIAN_BASELINE_SQL: the first run, and each median cell's newest reading
        6-14 d old, whatever its status."""
        first = self.results[0][0] if self.results else None
        young, old = at - params["young_d"] * _DAY, at - params["old_d"] * _DAY
        newest: dict[str, tuple[Any, ...]] = {}
        for run_at, r in self.results:
            if not old < run_at < young:
                continue
            for c in r["details"]["cells"]:
                facts = c["facts"]
                if params["arm"] in c["subject"] and _number(facts.get("median")):
                    n = facts["n"] if _number(facts.get("n")) else None
                    newest[c["subject"]] = (run_at, facts["median"],
                                            facts.get("price_per_m2_basis"), n)
        rows = [(first, subject, *reading) for subject, reading in sorted(newest.items())]
        return rows or [(first, None, None, None, None, None)]

    def answers(self) -> dict[str, Any]:
        return {common.TREND_SQL: self.trend, common.TREND_LOOKBACK_SQL: self.lookback}


@contextmanager
def _pass() -> Iterator[None]:
    """One health pass, as run_checks opens it: the pass memo on, then off."""
    clock.PASS.start()
    try:
        yield
    finally:
        clock.PASS.reset()


@functools.cache
def _fleet() -> tuple[str, ...]:
    """The portals the recorded window held. Every portal onboarded since is absent from these
    fixtures, so the replay judges the fleet it recorded, never a later portal as one gone dark."""
    fleet = tuple(sorted({row["source"] for row in _fixture("health_quality_measure_view_now.csv")}))
    assert set(fleet) <= set(SOURCES)
    return fleet


def _replay(ticks: list[_dt.datetime], check: Callable[..., dict[str, Any]],
            answers: Callable[[_dt.datetime, _History], dict[str, Any]]) -> list[_Result]:
    """`check` at each tick on its rows, each result stored before the next tick reads it."""
    history = _History()
    with mock.patch.object(qu, "SOURCES", _fleet()):
        for at in ticks:
            with _pass():
                result = check(_Conn({**history.answers(), **answers(at, history)}, now=at),
                               THRESHOLDS)
            history.store(at, result)
    return history.results


# --- the measure view (parsing, data_quality) -----------------------------------------------------


@functools.cache
def _series(name: str, stamp: str) -> tuple[tuple[_dt.datetime, dict[_Grain, dict[str, str]]], ...]:
    """A fixture's rows by instant and (portal, category, deal), in time order."""
    out: dict[_dt.datetime, dict[_Grain, dict[str, str]]] = {}
    for row in _fixture(name):
        grain = (row["source"], _part(row["category_main"]), _part(row["category_type"]))
        out.setdefault(_utc(row[stamp]), {})[grain] = row
    return tuple(sorted(out.items()))


def _held(series: tuple[tuple[_dt.datetime, Any], ...], at: _dt.datetime) -> Any:
    """The newest instant's rows at or before `at`; none before the first."""
    i = bisect.bisect_right([instant for instant, _ in series], at)
    return series[i - 1][1] if i else {}


def _order(grain: _Grain) -> tuple[Any, ...]:
    """The view's own order: source, then category and deal, a missing one last."""
    source, main, deal = grain
    return source, main is None, main or "", deal is None, deal or ""


def _view_row(grain: _Grain, values: dict[str, Any]) -> tuple[Any, ...]:
    """A PLAUSIBILITY_SQL row: the basis the measure function resolves, `values`, the rest empty."""
    source, main, deal = grain
    row = dict.fromkeys(qu.PLAUSIBILITY_COLUMNS)
    row.update(source=source, category_main=main, category_type=deal,
               price_per_m2_basis=ppm2_basis(main, deal), **values)
    return tuple(row[column] for column in qu.PLAUSIBILITY_COLUMNS)


def _gap_values(row: dict[str, str], broken_d: float | None) -> dict[str, Any]:
    """A gap row as the view's columns; `broken_d` days into the synthetic collapse, every new
    ad since it has lost its measure (the cohort of 7 d whole after a week, the stock beyond)."""
    stock, gap = int(row["stock_n"]), int(row["gap_stock_n"])
    arrivals, gap_7d = int(row["arrivals_7d"]), int(row["gap_7d_n"])
    if broken_d is not None:
        lost = (arrivals - gap_7d) / 7
        gap_7d, gap = gap_7d + round(min(broken_d, 7) * lost), gap + round(broken_d * lost)
    return {"n_active": stock, "n_active_7d": arrivals,
            "measure_input_gap_share": gap / stock if stock else None,
            "measure_input_gap_share_7d": gap_7d / arrivals if arrivals else None}


def _parsing_view(at: _dt.datetime, medians: str, broken: bool) -> list[tuple[Any, ...]]:
    """The view at `at`: the gaps of the newest day's end, the medians of `medians`' newest run."""
    gaps = _held(_series(_GAPS, "at"), at)
    held = _held(_series(medians, "run_at" if medians == _LEGACY_MEDIANS else "at"), at)
    rows = []
    for grain in sorted(set(gaps) | set(held), key=_order):
        values: dict[str, Any] = {}
        if grain in gaps:
            broken_d = ((at - _BROKEN_SINCE) / _DAY if broken and grain == _BROKEN
                        and at >= _BROKEN_SINCE else None)
            values.update(_gap_values(gaps[grain], broken_d))
        if grain in held:
            row = held[grain]
            values.update(median_price_per_m2=float(row["ppm2"]) if row["ppm2"] else None,
                          n_ppm2_valued=int(row["n_ppm2"] or 0))
            values.setdefault("n_active", int(row["n"]))
        rows.append(_view_row(grain, values))
    return rows


# --- churn ----------------------------------------------------------------------------------------

_CHURN = "health_quality_churn_28d.csv"
_CHURN_FROM, _CHURN_EXPORT = _utc("2026-09-21T00:00:00Z"), _utc("2026-10-19T14:14:53Z")


@functools.cache
def _median_day() -> dict[str, float]:
    """Each portal's median re-snapshots a day over the 28 complete days on record (the lead-in)."""
    days: dict[str, list[int]] = {}
    for row in _fixture(_CHURN):
        if _utc(f"{row['day']}T00:00:00Z") + _DAY <= _CHURN_EXPORT:
            days.setdefault(row["source"], []).append(int(row["resnapshots"]))
    return {source: statistics.median(counts) for source, counts in days.items()}


@functools.cache
def _churn_spans(storms: tuple[tuple[str, str, float], ...]
                 ) -> dict[str, tuple[tuple[_dt.datetime, _dt.datetime, float], ...]]:
    """Per portal, (from, to, re-snapshots) per stretch: the lead-in, then each exported day (a
    storm's day at its multiple of the median day), the last one cut at the export."""
    storm = {(source, day): factor for source, day, factor in storms}
    spans: dict[str, list[tuple[_dt.datetime, _dt.datetime, float]]] = {}
    for row in _fixture(_CHURN):
        source, start = row["source"], _utc(f"{row['day']}T00:00:00Z")
        factor = storm.get((source, row["day"]))
        count = factor * _median_day()[source] if factor else float(row["resnapshots"])
        spans.setdefault(source, []).append((start, min(start + _DAY, _CHURN_EXPORT), count))
    lead = _CHURN_FROM - 60 * _DAY
    return {source: ((lead, _CHURN_FROM, 60 * _median_day()[source]), *days)
            for source, days in spans.items()}


@functools.cache
def _captures() -> dict[str, tuple[tuple[_dt.datetime, int], ...]]:
    """Per portal, its active ads at each day's last capture, in time order."""
    out: dict[str, list[tuple[_dt.datetime, int]]] = {}
    for row in _fixture(_CHURN):
        if row["active_n"]:
            out.setdefault(row["source"], []).append((_utc(row["active_at"]), int(row["active_n"])))
    return {source: tuple(sorted(rows)) for source, rows in out.items()}


def _active(source: str, at: _dt.datetime) -> int:
    """A portal's active ads at the newest capture at or before `at` (the first one before it)."""
    rows = _captures()[source]
    i = bisect.bisect_right([stamp for stamp, _ in rows], at)
    return rows[max(i - 1, 0)][1]


def _resnaps(spans: tuple[tuple[_dt.datetime, _dt.datetime, float], ...], lo: _dt.datetime,
             hi: _dt.datetime) -> float:
    """The re-snapshots of (lo, hi], each stretch's spread evenly over it."""
    total = 0.0
    for start, end, count in spans:
        overlap = (min(hi, end) - max(lo, start)) / (end - start)
        total += count * max(overlap, 0.0)
    return total


def _churn_rows(at: _dt.datetime, storms: tuple[tuple[str, str, float], ...]
                ) -> list[tuple[Any, ...]]:
    """quality._CHURN_SQL's rows at `at`: per portal its active ads and its 29 windows."""
    rows: list[tuple[Any, ...]] = []
    for source, spans in sorted(_churn_spans(storms).items()):
        ads = _active(source, at)
        counted = [(k, round(_resnaps(spans, at - (k + 1) * _DAY, at - k * _DAY)))
                   for k in range(qu._CHURN_DAYS + 1)]
        rows += [(source, ads, k, n) for k, n in counted if n] or [(source, ads, None, None)]
    return rows


# --- parsing --------------------------------------------------------------------------------------


@functools.cache
def _payload() -> tuple[tuple[str, int, int], ...]:
    return tuple((r["source"], int(r["ads_48h"]), int(r["unreadable_48h"]))
                 for r in _fixture("health_quality_payload_now.csv"))


@functools.cache
def _parsing(medians: str = _WEEKLY, synthetic: bool = False) -> tuple[_Ladder, list[_Result]]:
    """check_parsing every 6 h on the exports (from 09-16 on the retiring check's medians, else
    from 09-22 on the weekly ones), told through the ladder; `synthetic` adds (a) and (b)."""
    storms = _STORMS if synthetic else ()
    matrix = _matrix(field_census.load_baseline())

    def answers(at: _dt.datetime, history: _History) -> dict[str, Any]:
        return {field_census.FIELD_MATRIX_SQL: matrix,
                qu.PLAUSIBILITY_SQL: _parsing_view(at, medians, synthetic),
                qu._MEDIAN_BASELINE_SQL: functools.partial(history.median_baseline, at),
                qu._PAYLOAD_SQL: list(_payload()), qu._CHURN_SQL: _churn_rows(at, storms)}

    start = _HEAL_START if medians == _LEGACY_MEDIANS else _START
    results = _replay(_ticks(start, _END, _SIX_HOURS), qu.check_parsing, answers)
    return _told("parsing", 21_600, results), results


# --- data_quality ---------------------------------------------------------------------------------

_VIEW_AT = _utc("2026-10-19T14:18:35Z")
_INTEGERS = {"n_active", "n_floor_eligible", "n_floor_eligible_7d", "n_area_pairs",
             "n_area_pairs_7d", "n_area_valued", "n_ppm2_valued", "n_active_7d", "n_estate_triples"}
_TEXT_COLUMNS = {"source", "category_main", "category_type", "price_per_m2_basis"}


def _value(column: str, text: str) -> Any:
    if text == "":
        return None
    return text if column in _TEXT_COLUMNS else int(text) if column in _INTEGERS else float(text)


@functools.cache
def _view_now() -> tuple[dict[str, Any], ...]:
    """The view as exported, by column."""
    return tuple({column: _value(column, text) for column, text in row.items()}
                 for row in _fixture("health_quality_measure_view_now.csv"))


def _url_rows() -> list[tuple[Any, ...]]:
    """quality._URL_SQL's rows: the view's active ads per portal, 11 sreality ads with no link."""
    active: dict[str, int] = {}
    for row in _view_now():
        active[row["source"]] = active.get(row["source"], 0) + row["n_active"]
    return [(s, active[s], 11 if s == "sreality" else 0, 0) for s in _fleet()]


def _quality_view(at: _dt.datetime) -> list[tuple[Any, ...]]:
    """The exported view, its stock gap per decidable cell the daily series' at `at`."""
    gaps = _held(_series(_GAPS, "at"), at)
    rows = []
    for row in _view_now():
        grain = (row["source"], row["category_main"], row["category_type"])
        values = dict(row)
        if grain in gaps:
            stock, gap = int(gaps[grain]["stock_n"]), int(gaps[grain]["gap_stock_n"])
            values.update(n_active=stock, measure_input_gap_share=gap / stock if stock else None)
        rows.append(tuple(values[column] for column in qu.PLAUSIBILITY_COLUMNS))
    return rows


@functools.cache
def _quality() -> tuple[_Ladder, list[_Result]]:
    """check_data_quality every 6 h over the 28 days, told through the ladder as a gauge."""
    def answers(at: _dt.datetime, history: _History) -> dict[str, Any]:
        return {qu.PLAUSIBILITY_SQL: _quality_view(at), qu._URL_SQL: _url_rows()}

    results = _replay(_ticks(_START, _END, _SIX_HOURS), qu.check_data_quality, answers)
    return _told("data_quality", 21_600, results, cls="gauge"), results


# --- location_town_coverage -----------------------------------------------------------------------

_LOCATION_AT = _utc("2026-10-19T14:22:01Z")
_ZERO_FROM = _LOCATION_AT + _DAY + _SIX_HOURS
_REGISTRY_SOURCE_DAY = _dt.date(2026, 9, 30)


@functools.cache
def _location_now() -> tuple[tuple[Any, ...], ...]:
    """_LOCATION_TOWN_COVERAGE_SQL's rows as exported."""
    return tuple((r["source"], *(int(r[c]) for c in ("ads", "no_row", "cz_no_town", "town",
                                                      "hidden", "address_no_ku")))
                 for r in _fixture("health_quality_location_now.csv"))


def _location_rows(at: _dt.datetime) -> list[tuple[Any, ...]]:
    """The export's counts until the synthetic zero, then every ad with a row has its town."""
    if at < _ZERO_FROM:
        return list(_location_now())
    return [(s, ads, 0, 0, ads, 0, 0) for s, ads, *_ in _location_now()]


@functools.cache
def _location() -> tuple[_Ladder, list[_Result]]:
    """check_location_town_coverage every 6 h for three days from the export."""
    def answers(at: _dt.datetime, history: _History) -> dict[str, Any]:
        age = (at.date() - _REGISTRY_SOURCE_DAY).days
        return {qu._LOCATION_TOWN_COVERAGE_SQL: _location_rows(at),
                qu._REGISTRY_LIVENESS_SQL: [("ruian:2026-09-30", age, None, None)]}

    ticks = _ticks(_LOCATION_AT, _LOCATION_AT + 3 * _DAY, _SIX_HOURS)
    results = _replay(ticks, qu.check_location_town_coverage, answers)
    return _told("location_town_coverage", 21_600, results, cls="gauge"), results


# --- text_extraction: the reading model -----------------------------------------------------------

_LANE_GAPS = ((_utc("2026-09-20T20:56:00Z"), _utc("2026-09-22T19:03:00Z")),
              (_utc("2026-10-02T06:57:00Z"), _utc("2026-10-12T15:44:00Z")))
_ARRIVALS_EXPORT = _utc("2026-10-19T14:23:14Z")
_SLOT = 10 * _MINUTE
# An ordinary day's median minutes to the first reading is 3.4-7.1.
_PROMPT = 5 * _MINUTE
_TEXT_START, _TEXT_END = _utc("2026-09-22T00:00:00Z"), _utc("2026-10-19T14:00:00Z")


@functools.cache
def _lane_days() -> dict[_dt.datetime, dict[str, str]]:
    return {_utc(f"{r['day']}T00:00:00Z"): r for r in _fixture("health_quality_text_lane_28d.csv")}


def _gap_day(day: _dt.datetime) -> bool:
    return any(start < day + _DAY and day < end for start, end in _LANE_GAPS)


def _restart_pace(restart: _dt.datetime) -> float:
    """Readings an hour after a gap: the restart day's readings over its hours left."""
    day = restart.replace(hour=0, minute=0)
    return int(_lane_days()[day]["readings_ok"]) / ((day + _DAY - restart) / _HOUR)


def _slow_parts(row: dict[str, str]) -> tuple[tuple[float, _dt.timedelta], ...]:
    """An ordinary day's reading delays: its read_1h ads promptly, the rest evenly from 1 h to the
    delay that puts its p95 where the export read it (24 h when read_1h holds 95 % or more)."""
    arrivals, fast = int(row["arrivals"]), int(row["read_1h"])
    if fast >= arrivals:
        return ((1.0, _PROMPT),)
    share = fast / arrivals
    p95 = float(row["p95_min"])
    last = 60 + (p95 - 60) * (1 - share) / (0.95 - share) if share < 0.95 else 1_440.0
    steps = 12
    slow = tuple(((1 - share) / steps, _MINUTE * (60 + (last - 60) * (k + 0.5) / steps))
                 for k in range(steps))
    return ((share, _PROMPT), *slow)


@functools.cache
def _arrivals() -> tuple[tuple[_dt.datetime, float, tuple[tuple[float, _dt.datetime], ...]], ...]:
    """Every ten minutes' arrivals as (first seen, ads, (share, read at) parts), by the model."""
    slots = []
    for day, row in sorted(_lane_days().items()):
        end = min(day + _DAY, _ARRIVALS_EXPORT)
        n = int((end - day) / _SLOT)
        slots += [(day + _SLOT * (k + 0.5), int(row["arrivals"]) / n, day) for k in range(n)]
    reads: list[tuple[tuple[float, _dt.datetime], ...]] = []
    backlog: dict[_dt.datetime, list[int]] = {}
    for i, (seen, _, day) in enumerate(slots):
        gap = next((g for g in _LANE_GAPS if g[0] <= seen + _PROMPT < g[1]), None)
        if gap is not None:
            backlog.setdefault(gap[1], []).append(i)
            reads.append(())
        elif _gap_day(day):
            # A gap day's own counts describe its gap, not its pace.
            reads.append(((1.0, seen + _PROMPT),))
        else:
            reads.append(tuple((share, seen + delay) for share, delay in
                               _slow_parts(_lane_days()[day])))
    for restart, waiting in backlog.items():
        pace, done = _restart_pace(restart), 0.0
        for i in sorted(waiting, key=lambda k: slots[k][0], reverse=True):
            done += slots[i][1]
            reads[i] = ((1.0, restart + _HOUR * ((done - slots[i][1] / 2) / pace)),)
    return tuple((seen, ads, parts) for (seen, ads, _), parts in zip(slots, reads))


def _unread(at: _dt.datetime, lo: _dt.datetime | None = None,
            hi: _dt.datetime | None = None) -> tuple[float, float]:
    """The ads first seen before `at` (in [lo, hi) when given) and those of them still unread."""
    seen_n = unread = 0.0
    for seen, ads, parts in _arrivals():
        if seen >= at:
            break
        if lo is not None and not lo <= seen < hi:
            continue
        seen_n += ads
        unread += ads * sum(share for share, read in parts if read > at)
    return seen_n, unread


@functools.cache
def _stock_runs() -> tuple[tuple[_dt.datetime, int, int], ...]:
    """The retiring check's measured counts of what waits and its readings in 24 h."""
    return tuple((_utc(r["run_at"]), int(r["waiting_total"]), int(r["readings_24h"]))
                 for r in _fixture("health_quality_text_lag_runs_35d.csv") if r["waiting_total"])


def _stock_row(at: _dt.datetime) -> tuple[int, int, int]:
    """ELIGIBLE_LAG_SQL's row at `at`: the newest retiring count, its active part the model's
    unread arrivals (a lower bound); before the first count the arrivals unread wait and the
    readings are the W1 lane's own since its restart."""
    _, active = _unread(at)
    runs = [run for run in _stock_runs() if run[0] <= at]
    if runs:
        _, waiting, readings = runs[-1]
    else:
        restart = _LANE_GAPS[0][1]
        waiting = round(active)
        readings = round(_restart_pace(restart) * max((at - restart) / _HOUR, 0.0))
    return waiting, min(round(active), waiting), readings


def _flow_rows(at: _dt.datetime) -> list[tuple[Any, ...]]:
    """EXTRACTION_LATENCY_SQL's rows at `at`: arrivals first written 1-24 h ago, read or not."""
    arrived, unread = _unread(at, at - 24 * _HOUR, at - _HOUR)
    n = round(arrived)
    return [("bazos", n, n - round(unread), 0, None)] if n else []


@functools.cache
def _text() -> tuple[_Ladder, list[_Result]]:
    """check_text_extraction hourly over the 28 days, told through the ladder as a gauge."""
    def answers(at: _dt.datetime, history: _History) -> dict[str, Any]:
        return {text_lane.MODEL_SQL: [(_MODEL,)], text_lane.LLM_TEXT_SOURCES_SQL: [("bazos",)],
                text_lane.EXTRACTION_LATENCY_SQL: _flow_rows(at),
                text_lane.ELIGIBLE_LAG_SQL: [_stock_row(at)]}

    results = _replay(_ticks(_TEXT_START, _TEXT_END, _HOUR), qu.check_text_extraction, answers)
    return _told("text_extraction", 3_600, results, cls="gauge"), results


# --- the retiring keys ----------------------------------------------------------------------------

# Their class as CHECKS registered them until wave 6, every one 6-hourly on GitHub.
_RETIRING = {"ppm2_median_shift": "alarm", "ppm2_measure_coverage": "alarm",
             "location_payload_shape_drift": "alarm", "field_fill_matrix": "alarm",
             "ppm2_basis_floor_share": "gauge", "area_vs_usable_divergence": "gauge",
             "outbound_url_coverage": "gauge", "text_extraction_lag": "gauge",
             "floor_convention": "gauge"}


@functools.cache
def _retiring() -> tuple[_Ladder, dict[str, list[str]]]:
    """The nine keys' measured runs (harness artifacts left out) through today's ladder, each a
    single-subject row the test-side fake names by its key; and every run's status by key."""
    ladder = _Ladder()
    for key, cls in _RETIRING.items():
        ladder.register(key, cls=cls, cadence_s=21_600,
                        budget_s=21_600 + THRESHOLDS["GITHUB_DELIVERY_SLACK_S"])
    statuses: dict[str, list[str]] = {}
    for row in sorted(_fixture("health_quality_legacy_runs_35d.csv"), key=lambda r: r["run_at"]):
        statuses.setdefault(row["check_key"], []).append(row["artifact"] or row["status"])
        if row["artifact"]:
            continue
        at = _utc(row["run_at"])
        ladder.result(row["check_key"], at, row["status"],
                      value=float(row["value"]) if row["value"] else None)
        reconcile(ladder, now=at)
    return ladder, statuses


def _modelled(day: _dt.datetime) -> tuple[int, int]:
    """The model's read_1h and read_24h for one arrival day."""
    within = {1: 0.0, 24: 0.0}
    for seen, ads, parts in _arrivals():
        if day <= seen < day + _DAY:
            for hours in within:
                within[hours] += ads * sum(share for share, read in parts
                                           if read - seen <= hours * _HOUR)
    return round(within[1]), round(within[24])


# --- the inputs -----------------------------------------------------------------------------------


def test_the_fixtures_hold_the_rows_and_totals_their_exports_read() -> None:
    """Every fixture's row count; the gap cells all decidable, each weekly basis the one the
    measure function's Python face resolves; 43 ads with no town; 218 legacy harness artifacts."""
    counts = {name: len(_fixture(f"health_quality_{name}.csv")) for name in (
        "churn_28d", "measure_view_now", "measure_gaps_28d", "stock_medians_6w",
        "legacy_medians_35d", "location_now", "text_lane_28d", "text_stock_now",
        "text_lag_runs_35d", "payload_now", "legacy_runs_35d")}
    assert counts == {"churn_28d": 261, "measure_view_now": 108, "measure_gaps_28d": 2_958,
                      "stock_medians_6w": 754, "legacy_medians_35d": 2_310, "location_now": 9,
                      "text_lane_28d": 29, "text_stock_now": 1, "text_lag_runs_35d": 96,
                      "payload_now": 3, "legacy_runs_35d": 1_047}
    gaps = _series(_GAPS, "at")
    assert len(gaps) == 29 and {len(cells) for _, cells in gaps} == {102}
    assert all(ppm2_basis(main, deal) for _, cells in gaps for _, main, deal in cells)
    weekly = [(grain, row) for _, cells in _series(_WEEKLY, "at") for grain, row in cells.items()]
    assert all(_part(row["basis"]) == ppm2_basis(main, deal) for (_, main, deal), row in weekly)
    no_town = {source: no_row + missing for source, _, no_row, missing, *_ in _location_now()
               if no_row + missing}
    assert no_town == {"ceskereality": 39, "mmreality": 1, "realitymix": 3}
    artifacts = [r["artifact"] for r in _fixture("health_quality_legacy_runs_35d.csv")
                 if r["artifact"]]
    assert {a: artifacts.count(a) for a in set(artifacts)} == {"not_run": 125, "skipped": 58,
                                                               "timed_out": 35}


def test_the_reading_model_reproduces_each_days_read_counts() -> None:
    """Outside the lane's two gaps the model reads every day's read_1h and read_24h exactly; on
    the five gap days that differ it is within 245 ads of the export."""
    # The W1 lane's first hours read new ads slower than its restart pace (09-22: 175 within 1 h,
    # modelled 420), and 10-02's lane slowed before its last reading at 06:57Z (174 read, 383).
    differ = {}
    for day, row in sorted(_lane_days().items()):
        fixture = (int(row["read_1h"]), int(row["read_24h"]))
        if _modelled(day) != fixture:
            assert _gap_day(day), day
            differ[day.date().isoformat()] = (_modelled(day), fixture)
    assert differ == {"2026-09-21": ((0, 348), (0, 548)),
                      "2026-09-22": ((420, 1_681), (175, 1_681)),
                      "2026-10-02": ((383, 383), (174, 174)),
                      "2026-10-11": ((0, 230), (0, 291)),
                      "2026-10-12": ((849, 2_224), (922, 2_224))}


def test_every_check_emits_every_subject_on_every_tick_and_every_result_carries_cells() -> None:
    """An omitted subject strands its incident for 7 days: no replay ever drops one."""
    # The heal replay is left out: its view gains the gap series' cells when that starts (09-22).
    for (_, results), size in ((_parsing(), 541), (_quality(), 231), (_location(), 10),
                               (_text(), 2)):
        assert {frozenset(_cells(r)) for _, r in results} == {frozenset(_cells(results[0][1]))}
        assert len(_cells(results[0][1])) == size
        assert all(r["status"] != "unknown" and r["details"]["cells"] for _, r in results)


# --- parsing over 28 days -------------------------------------------------------------------------


def test_in_28_days_parsing_opens_only_the_idnes_repricing_waves() -> None:
    """Not quiet: the churn rule as ruled opens idnes's re-pricing waves of 09-24 and 10-01/02."""
    # The export reads idnes's re-snapshots of 09-24 (6,761), 10-01 (8,935) and 10-02 (5,006) at
    # 8.97x, 11.85x and 6.64x its median day of 754 (a read beside the export: 97-99.7 % of them
    # price changes, median 0.57 %; INFERRED the FX-conversion jitter of #683 moving just past its
    # 0.5 % tolerance on exchange-rate days). The 6x line was set for exactly such a storm, so
    # each wave opens one incident; 10-17's (4,456) peaks at 5.91x and stays Watch. A ruling is
    # owed (name them, make the arm FX-aware, or move the line); this pin moves with it.
    ladder, _ = _parsing()
    assert _onsets(ladder, "parsing") == [
        ("idnes/churn", _utc("2026-09-24T18:00:00Z"), _utc("2026-09-26T12:00:00Z")),
        ("idnes/churn", _utc("2026-10-01T12:00:00Z"), _utc("2026-10-04T06:00:00Z"))]
    assert _notices(ladder, "parsing", "onset") == [_utc("2026-09-24T18:00:00Z"),
                                                    _utc("2026-10-01T12:00:00Z")]
    assert len(_notices(ladder, "parsing", "recovery")) == 2


def test_every_other_reading_past_ok_in_28_days_is_a_churn_watch() -> None:
    """Fill, vocabulary, census, measures, medians and payload never leave OK or Not measured."""
    # ceskereality's catch-up after its two empty days (1,244 re-snapshots, 3.83x) and bazos on
    # 10-15 (883, 3.07x) are the only Watch readings beside idnes's ramps.
    _, results = _parsing()
    flagged = _flagged(results)
    assert {subject for _, subject, _ in flagged} == {"idnes/churn", "ceskereality/churn",
                                                       "bazos/churn"}
    assert [f for f in flagged if f[1] != "idnes/churn"] == [
        (_utc("2026-10-03T00:00:00Z"), "ceskereality/churn", "warn"),
        (_utc("2026-10-15T00:00:00Z"), "bazos/churn", "warn")]
    idnes = [status for _, subject, status in flagged if subject == "idnes/churn"]
    assert (idnes.count("fail"), idnes.count("warn")) == (10, 11)


def test_the_churn_arm_reads_idnes_against_its_own_median_day() -> None:
    """The peak, 10-02 00:00Z: 8,935 re-snapshots against a median day of 754, 11.85x."""
    _, results = _parsing()
    churn = dict(_arm(results, "idnes/churn"))
    peak = churn[_utc("2026-10-02T00:00:00Z")]
    assert (peak["status"], peak["value"]) == ("fail", 11.85)
    assert (peak["facts"]["resnaps_24h"], peak["facts"]["median_resnaps_28d"]) == (8_935, 754)
    assert peak["message"].endswith("(Problem past 6×): a volatile hashed field may be "
                                    "re-snapshotting the stock, or a heal's refetch wave ran")
    late = churn[_utc("2026-10-18T00:00:00Z")]
    assert (late["status"], late["value"], late["facts"]["resnaps_24h"]) == ("warn", 5.91, 4_456)


def test_the_measures_arm_stays_level_with_each_cells_stock_for_28_days() -> None:
    """The relative rule reads no Watch on 28 days of standing gaps; the closest is 9.1 pp."""
    # realitymix ostatni/prodej: 42 of its 55 ads first seen in 7 d with no measure (lower bound
    # 63.7 %) against 54.5 % of its 970 active ads. Cells under 50 new ads a week are Not measured.
    _, results = _parsing()
    measures = [(at, c) for at, r in results for c in r["details"]["cells"]
                if "/measures/" in c["subject"]]
    statuses = [c["status"] for _, c in measures]
    assert (statuses.count("ok"), statuses.count("unknown"), len(statuses)) == (6_101, 5_221,
                                                                                 11_322)
    worst = max((c for _, c in measures if c["status"] == "ok"), key=lambda c: c["value"])
    assert (worst["subject"], worst["value"]) == ("realitymix/measures/ostatni-prodej", 9.1)
    assert (worst["facts"]["missing_7d"], worst["facts"]["ads_7d"], worst["facts"]["ads"]) == (
        42, 55, 970)


def test_the_median_arm_is_not_measured_for_six_days_then_ok_on_the_weekly_medians() -> None:
    """Not measured until 09-28 06:00Z, then every judged cell OK week over week, worst 1.10x."""
    # 09-28 00:00Z is the sixth day, but no run of the check is yet strictly older than 6 d.
    # Afterwards 65 or 66 of the 102 median cells carry 200 ads in both weeks; the rest say so.
    _, results = _parsing()
    first = _utc("2026-09-28T06:00:00Z")
    medians = [(at, c) for at, r in results for c in r["details"]["cells"]
               if "/median/" in c["subject"]]
    early = [c for at, c in medians if at < first]
    assert {c["status"] for c in early} == {"unknown"}
    assert all(c["message"].startswith("Not measured: this check has ") for at, c in medians
               if at < _utc("2026-09-28T00:00:00Z"))
    assert {c["message"] for at, c in medians if at == _utc("2026-09-28T00:00:00Z")} == {
        "Not measured: no reading of this median 6-14 d ago"}
    later = [(at, c) for at, c in medians if at >= first]
    assert {c["status"] for _, c in later} == {"ok", "unknown"}
    assert all("under the 200 this arm needs in both" in c["message"] for _, c in later
               if c["status"] == "unknown")
    judged = {at: 0 for at, _ in later}
    for at, c in later:
        judged[at] += c["status"] == "ok"
    assert set(judged.values()) == {65, 66}
    assert max(c["value"] for _, c in later if c["status"] == "ok") == 1.104


def test_fill_vocabulary_census_and_payload_read_ok_on_their_synthetic_inputs() -> None:
    """SYNTHETIC: the 10-19 re-bless's matrix and the 14:25:15Z payload counts on every tick."""
    # 234 fill cells and 90 vocabulary cells over the 9 portals, the census of the re-blessed key
    # files, and the three raw_json portals' 0 unreadable of 1,798, 170 and 124 new ads.
    _, results = _parsing()
    per_tick = {arm: {sum(arm in s for s in _cells(r)) for _, r in results}
                for arm in ("/fill/", "/vocabulary/", "/payload")}
    assert per_tick == {"/fill/": {234}, "/vocabulary/": {90}, "/payload": {3}}
    for _, r in results:
        for subject, c in _cells(r).items():
            if subject == "census" or any(arm in subject for arm in per_tick):
                assert c["status"] == "ok", (subject, c["message"])


# --- parsing: the synthetic incidents -------------------------------------------------------------


def test_a_synthetic_collapse_of_new_ads_measures_reads_watch_in_a_day_and_opens_in_two() -> None:
    """SYNTHETIC (a): every new sreality byt/prodej ad from 10-08 00:00Z loses its price."""
    # 24 h in, 290 of the 1,804 ads first seen in 7 d have no measure (lower bound 14.5 %) against
    # 3.9 % of the stock: 10.5 pp, Watch. 2.25 d in, 610 of 1,823 (31.3 %) against 5.4 %: 25.9 pp,
    # Problem, and the alarm opens one incident that stays open to the replay's end.
    ladder, results = _parsing(synthetic=True)
    readings = _arm(results, "sreality/measures/byt-prodej")
    watch = next(at for at, c in readings if c["status"] == "warn")
    problem = next(at for at, c in readings if c["status"] == "fail")
    assert (watch, problem) == (_utc("2026-10-09T00:00:00Z"), _utc("2026-10-10T06:00:00Z"))
    assert {c["status"] for at, c in readings if at < watch} == {"ok"}
    onset = dict(readings)[problem]
    assert (onset["value"], onset["facts"]["missing_7d"], onset["facts"]["ads_7d"]) == (
        25.9, 610, 1_823)
    assert onset["message"].endswith("25.9 pp above the 5.4% of all 21,412 active ads (Problem "
                                     "past 25 pp)")
    assert ("sreality/measures/byt-prodej", problem, None) in _onsets(ladder, "parsing")


def test_a_synthetic_storm_at_4x_reads_watch_and_opens_nothing_one_at_7x_opens() -> None:
    """SYNTHETIC (b): sreality at 4x its median day on 10-10 and 7x on 10-16."""
    # Under D6's lines (Watch past 3x, Problem past 6x) a 4x storm reads Watch, and an alarm's
    # Watch is never told, so it opens nothing. 10-11 00:00Z holds the whole storm: 8,638
    # against the trailing median day of 2,160, 4.00x. The 7x day (15,116) reads 6.49x against
    # its own trailing median of 2,328 (the 4x storm among its days): Problem for one tick, open
    # 10-17 00:00Z, recovered at 10-18 06:00Z.
    ladder, results = _parsing(synthetic=True)
    churn = dict(_arm(results, "sreality/churn"))
    flagged = [(at, c["status"]) for at, c in churn.items() if c["status"] != "ok"]
    assert flagged == [(_utc(f"2026-10-{stamp}:00:00Z"), status) for stamp, status in (
        ("10T18", "warn"), ("11T00", "warn"), ("11T06", "warn"), ("16T12", "warn"),
        ("16T18", "warn"), ("17T00", "fail"), ("17T06", "warn"), ("17T12", "warn"))]
    for at, read in (("2026-10-11T00:00:00Z", (4.0, 8_638, 2_160)),
                     ("2026-10-17T00:00:00Z", (6.49, 15_116, 2_328))):
        storm = churn[_utc(at)]
        facts = storm["facts"]
        assert (storm["value"], facts["resnaps_24h"], facts["median_resnaps_28d"]) == read
    real, _ = _parsing()
    assert _onsets(ladder, "parsing") == [*_onsets(real, "parsing"), (
        "sreality/measures/byt-prodej", _utc("2026-10-10T06:00:00Z"), None), (
        "sreality/churn", _utc("2026-10-17T00:00:00Z"), _utc("2026-10-18T06:00:00Z"))]


def test_the_synthetic_incidents_touch_only_their_own_subjects() -> None:
    """Every other cell of the synthetic replay reads exactly as the real one."""
    real = dict(_parsing()[1])
    changed = set()
    for at, r in _parsing(synthetic=True)[1]:
        before = _cells(real[at])
        changed |= {s for s, c in _cells(r).items()
                    if (c["status"], c["value"]) != (before[s]["status"], before[s]["value"])}
    assert changed == {"sreality/measures/byt-prodej", "sreality/churn"}


# --- parsing: the 09-17 heals on the retiring check's own medians --------------------------------


def test_the_09_17_heals_read_as_one_grouped_onset_once_the_arm_has_six_days() -> None:
    """Five medians moved 3.2-6.9x by heals: one onset naming all five, 09-22 06:00Z."""
    # The view's medians as ppm2_median_shift stored them, from 09-16 (its first retained day):
    # W19's spaced thousands (#1503, 09-17 05:46Z: "5 870 m²" had read 870) on the land of bazos
    # (800.22 -> 116.66 Kč/m² of plot), ceskereality, realitymix and remax, and W21's mmreality
    # plot (#1511; dum/prodej 12,036 -> 42,622 Kč/m²). No run was measured on 09-17/18, so the
    # store shows the steps from 09-19. They landed inside the arm's first 6 d, so it reads them
    # on its seventh and holds Problem until the 6-14 d window holds post-heal medians: a heal
    # reads as a shift (stated limit). The 10-12/13 bazos dip (98.64, 83.69) reads 1.49x, OK.
    ladder, results = _parsing(_LEGACY_MEDIANS)
    opened, recovered = _utc("2026-09-22T06:00:00Z"), _utc("2026-09-26T18:00:00Z")
    healed = ["bazos/median/pozemek-prodej", "ceskereality/median/pozemek-prodej",
              "mmreality/median/dum-prodej", "realitymix/median/pozemek-prodej",
              "remax/median/pozemek-prodej"]
    assert [i for i in _onsets(ladder, "parsing") if "/median/" in i[0]] == [
        (subject, opened, recovered) for subject in healed]
    assert {(at, s) for at, s, _ in _flagged(results, "/median/")} == {
        (at, s) for at in _ticks(opened, _utc("2026-09-26T00:00:00Z"), _SIX_HOURS) for s in healed}
    first = _cells(dict(results)[opened])
    assert {s: first[s]["value"] for s in healed} == {
        "bazos/median/pozemek-prodej": 6.859, "ceskereality/median/pozemek-prodej": 3.739,
        "mmreality/median/dum-prodej": 3.541, "realitymix/median/pozemek-prodej": 3.452,
        "remax/median/pozemek-prodej": 3.161}
    assert first["bazos/median/pozemek-prodej"]["message"].startswith(
        "median 117 Kč/m² pozemku against 800 Kč/m² pozemku 6.2 d ago (6.86×")
    assert _notices(ladder, "parsing", "onset")[0] == _utc("2026-09-22T06:00:00Z")
    dip = max((c for at, r in results for s, c in _cells(r).items()
               if s == healed[0] and at > recovered), key=lambda c: c["value"])
    assert (dip["status"], dip["value"]) == ("ok", 1.486)


# --- data_quality ---------------------------------------------------------------------------------


def test_data_quality_rings_once_on_the_realitymix_floor_and_reminds_once_a_day() -> None:
    """D8's ship-time red, held over 28 days: one incident, one onset, a reminder a day."""
    # SYNTHETIC in time (the floor columns exist only as of 14:18:35Z): realitymix komercni/
    # pronajem, 473 of 4,141 priced ads with an area NULLed by the floor (lower bound 10.5 %). Its
    # three Watch cells (bazos komercni/pronajem, realitymix pozemek/pronajem, remax komercni/
    # pronajem) are never told.
    ladder, results = _quality()
    assert _onsets(ladder, "data_quality") == [("floor/realitymix/komercni-pronajem", _START, None)]
    assert _notices(ladder, "data_quality", "onset") == [_utc("2026-09-22T00:00:00Z")]
    reminders = _notices(ladder, "data_quality", "reminder")
    assert reminders == [_utc("2026-09-22T00:00:00Z") + k * _DAY for k in range(1, 28)]
    flagged = {(s, status) for _, s, status in _flagged(results)}
    assert flagged == {("floor/realitymix/komercni-pronajem", "fail"),
                       ("floor/bazos/komercni-pronajem", "warn"),
                       ("floor/realitymix/pozemek-pronajem", "warn"),
                       ("floor/remax/komercni-pronajem", "warn")}
    red = _cells(results[-1][1])["floor/realitymix/komercni-pronajem"]
    assert red["message"].startswith("473 of 4,141 priced ads with an area lose their per-m² price "
                                     "to the floor (11.4%; lower bound 10.5%) (Problem above 10%)")


def test_the_measure_gap_arm_reads_no_watch_on_the_daily_stock_gaps() -> None:
    """28 days of the stock's own gaps: nothing near 95 % with 200 ads behind it."""
    # The highest scored lower bound is realitymix ostatni/pronajem, 254 of 291 (83.0 %); remax's
    # two cells with no category (96.9 % of 98 for sale) stay Not measured under 200 ads.
    _, results = _quality()
    gaps = [(at, c) for at, r in results for c in r["details"]["cells"]
            if c["subject"].startswith("measure_gap/")]
    statuses = [c["status"] for _, c in gaps]
    assert (statuses.count("ok"), statuses.count("unknown"), len(statuses)) == (
        7_770, 3_552, 11_322)
    worst = max((c for _, c in gaps if c["status"] == "ok"),
                key=lambda c: c["facts"]["lower_bound"])
    assert (worst["subject"], worst["facts"]["lower_bound"]) == (
        "measure_gap/realitymix/ostatni-pronajem", 0.8297)
    assert {c["status"] for _, c in gaps if c["subject"] == "measure_gap/remax/?-prodej"} == {
        "unknown"}


# --- the gauges at ship ---------------------------------------------------------------------------

_STOCK_AT = _utc("2026-10-19T14:23:57Z")


def _ship() -> dict[str, tuple[_Ladder, list[_Result]]]:
    """Each gauge on its own export: the view, the location counts and the lane's waiting split."""
    view = [tuple(row[c] for c in qu.PLAUSIBILITY_COLUMNS) for row in _view_now()]
    (stock,) = _fixture("health_quality_text_stock_now.csv")
    waiting = int(stock["waiting_active"]) + int(stock["waiting_inactive"])
    lane = (waiting, int(stock["waiting_active"]), int(stock["readings_24h"]))
    runs = {
        "data_quality": _replay([_VIEW_AT], qu.check_data_quality, lambda at, h: {
            qu.PLAUSIBILITY_SQL: view, qu._URL_SQL: _url_rows()}),
        "location_town_coverage": _replay([_LOCATION_AT], qu.check_location_town_coverage,
                                          lambda at, h: {
            qu._LOCATION_TOWN_COVERAGE_SQL: list(_location_now()),
            qu._REGISTRY_LIVENESS_SQL: [("ruian:2026-09-30", 19, None, None)]}),
        "text_extraction": _replay([_STOCK_AT], qu.check_text_extraction, lambda at, h: {
            text_lane.MODEL_SQL: [(_MODEL,)], text_lane.LLM_TEXT_SOURCES_SQL: [("bazos",)],
            text_lane.EXTRACTION_LATENCY_SQL: _flow_rows(at), text_lane.ELIGIBLE_LAG_SQL: [lane]}),
    }
    cadence = {"data_quality": 21_600, "location_town_coverage": 21_600, "text_extraction": 3_600}
    return {key: (_told(key, cadence[key], results, cls="gauge"), results)
            for key, results in runs.items()}


def test_at_ship_the_gauges_problems_are_the_realitymix_floor_and_the_43_ads_with_no_town() -> None:
    """D8 and D9's expected reds and nothing else: text_extraction reads OK on its exports."""
    # data_quality reproduces the ship-time count its build read (231 cells: 1 Problem, 3 Watch,
    # 163 OK, 64 Not measured); the lane waits on 55,020 archived ads at 12,845 readings a day,
    # 4.3 d.
    ship = _ship()
    opened = {key: [s for s, _, _ in _onsets(ladder, key)] for key, (ladder, _) in ship.items()}
    assert opened == {"data_quality": ["floor/realitymix/komercni-pronajem"],
                      "location_town_coverage": ["ceskereality/town", "mmreality/town",
                                                 "realitymix/town"],
                      "text_extraction": []}
    quality = ship["data_quality"][1][0][1]
    statuses = [c["status"] for c in quality["details"]["cells"]]
    assert {s: statuses.count(s) for s in set(statuses)} == {"fail": 1, "warn": 3, "ok": 163,
                                                            "unknown": 64}
    text = _cells(ship["text_extraction"][1][0][1])
    assert {s: c["status"] for s, c in text.items()} == {"flow/bazos": "ok", "stock": "ok"}
    assert text["stock"]["message"].startswith(
        "55,020 ads wait for a description reading (0 active, 55,020 inactive")
    assert text["stock"]["value"] == 4.3


# --- location_town_coverage -----------------------------------------------------------------------


def test_the_location_gauge_rings_the_43_ads_once_and_recovers_on_a_synthetic_zero() -> None:
    """Day one opens three cells in one onset; the synthetic zero recovers them in one notice."""
    # SYNTHETIC from day two: the 14:22:01Z reading holds through the day-one reminder (24 h), and
    # from the pass after it (30 h in) every one of the 43 has a town. Recovery needs an OK held
    # 11.5 h, so the third OK pass closes all three.
    ladder, results = _location()
    zero = _ZERO_FROM
    recovered = zero + 2 * _SIX_HOURS
    assert _onsets(ladder, "location_town_coverage") == [
        (subject, _LOCATION_AT, recovered)
        for subject in ("ceskereality/town", "mmreality/town", "realitymix/town")]
    three = ("ceskereality/town", "mmreality/town", "realitymix/town")
    assert [(t.kind, t.subjects, t.at) for t in ladder.told] == [
        ("onset", three, _LOCATION_AT), ("reminder", three, _LOCATION_AT + _DAY),
        ("recovery", three, recovered)]
    assert [(r["status"], r["value"]) for at, r in results if at < zero] == [("fail", 3)] * 5
    assert {(r["status"], r["value"]) for at, r in results if at >= zero} == {("ok", 0)}
    red = _cells(results[0][1])["ceskereality/town"]
    assert red["message"] == ("39 of 48,744 active ads first written more than 24 h ago have no "
                              "town: 0 with no location row, 39 not foreign with no town (rule 25: "
                              "red until zero)")
    assert _cells(results[0][1])["registry"]["message"] == (
        "the current registry ruian:2026-09-30 is 19 d old, nothing staged")


# --- text_extraction over 28 days -----------------------------------------------------------------


def test_in_28_days_the_text_gauge_opens_only_the_two_times_the_lane_stopped() -> None:
    """The W1 cutover and the provider-credit outage, each on its flow and its stock cell."""
    # Besides the provider-credit outage the export holds the W1 cutover, 46.1 h with no
    # reading (09-20 20:56Z -> 09-22 19:03Z) while 1,930 ads of 09-21 waited: the flow and the
    # stock read Problem from the first tick and recover once the W1 lane drained them. The outage
    # (10-02 06:57Z -> 10-12 15:44Z) reads Problem on the flow from 10-02 15:00Z (402 of 1,368 new
    # ads unread) and on the stock from the first count with no reading in 24 h (10-03 16:28Z).
    ladder, _ = _text()
    assert _onsets(ladder, "text_extraction") == [
        ("flow/bazos", _TEXT_START, _utc("2026-09-22T22:00:00Z")),
        ("stock", _TEXT_START, _utc("2026-09-22T22:00:00Z")),
        ("flow/bazos", _utc("2026-10-02T15:00:00Z"), _utc("2026-10-12T20:00:00Z")),
        ("stock", _utc("2026-10-03T17:00:00Z"), _utc("2026-10-13T17:00:00Z"))]
    assert len(_notices(ladder, "text_extraction", "onset")) == 3
    assert len(_notices(ladder, "text_extraction", "recovery")) == 3


def test_the_flow_reads_watch_only_on_the_outages_first_hours() -> None:
    """The slow days after the outage and the two re-reads read OK: no flow Watch elsewhere."""
    # Under the reading model: 10-01 (606 of 1,555 read after an hour, the schema-2 re-read),
    # 10-13..15 (673, 399 and 374) and 10-18 stay OK, their slow ads read within their p95; Watch
    # only on 10-02 10:00-14:00Z, as the outage began.
    _, results = _text()
    flow = _arm(results, "flow/bazos")
    watch = [at for at, c in flow if c["status"] == "warn"]
    assert watch == _ticks(_utc("2026-10-02T10:00:00Z"), _utc("2026-10-02T14:00:00Z"), _HOUR)
    problem = [at for at, c in flow if c["status"] == "fail"]
    assert problem == [*_ticks(_TEXT_START, _utc("2026-09-22T19:00:00Z"), _HOUR),
                       *_ticks(_utc("2026-10-02T15:00:00Z"), _utc("2026-10-12T17:00:00Z"), _HOUR)]


def test_the_stock_reads_watch_around_the_outage_and_problem_through_it() -> None:
    """ETA past 14 d as the readings fell and rose again; Problem with none in 24 h."""
    # 10-02 12:47Z: 80,620 waiting at 3,879 a day, 20.8 d; 10-03 16:28Z: none read in 24 h while
    # active ads waited; 10-13 00:47Z: 89,494 at 6,164, 14.5 d; 10-13 14:20Z: 6.0 d, OK.
    _, results = _text()
    changes, status = [], None
    for at, c in _arm(results, "stock"):
        if c["status"] != status:
            changes.append((at, c["status"], c["value"]))
            status = c["status"]
    assert changes == [(_TEXT_START, "fail", None), (_utc("2026-09-22T20:00:00Z"), "ok", 0.3),
                       (_utc("2026-10-02T13:00:00Z"), "warn", 20.8),
                       (_utc("2026-10-03T17:00:00Z"), "fail", None),
                       (_utc("2026-10-13T01:00:00Z"), "warn", 14.5),
                       (_utc("2026-10-13T15:00:00Z"), "ok", 6.0)]


def test_a_gauge_trend_rolls_one_point_a_prague_day_over_the_replay() -> None:
    """D11 on a real series: 28 Prague days of the flow, the outage's ten red days in it."""
    # A day's point is its last reading, so 10-12 (OK from 18:00Z) is green; worse_7d compares
    # with the point dated a week back.
    _, results = _text()
    flow = _cells(results[-1][1])["flow/bazos"]["facts"]
    assert len(flow["trend"]) == 28 and flow["trend"][0][0] == "2026-09-22"
    red = [day for day, _, status in flow["trend"] if status == "fail"]
    assert red == [f"2026-10-{d:02d}" for d in range(2, 12)]
    worse = {at.date().isoformat(): _cells(r)["flow/bazos"]["facts"]["worse_7d"]
             for at, r in results if at.hour == 12}
    assert (worse["2026-10-03"], worse["2026-10-09"], worse["2026-10-13"]) == (True, False, False)


# --- the retiring keys ----------------------------------------------------------------------------


def test_the_retiring_keys_measured_history_through_todays_ladder() -> None:
    """35 days of the nine keys: six incidents and 68 notices, none of them a parser regression."""
    # Each measured run is one cell named by its key (test-side; the ladder invents none since
    # wave 6). ppm2_median_shift's one incident is the 09-17 heals; field_fill_matrix's the 09-22
    # residue cleared by a re-bless; ppm2_basis_floor_share stood open from 09-15 with a reminder a
    # day; area_vs_usable_divergence the standing mmreality rows healed 09-17; text_extraction_lag
    # read the archive's age, so it was already open when the 10-02 outage began and never
    # announced it, where the new gauge opens the outage at 10-02 15:00Z.
    ladder, statuses = _retiring()
    opened = {key: [(o, r) for _, o, r in _onsets(ladder, key)] for key in _RETIRING}
    assert {key: len(v) for key, v in opened.items() if v} == {
        "ppm2_median_shift": 1, "field_fill_matrix": 1, "ppm2_basis_floor_share": 1,
        "area_vs_usable_divergence": 1, "text_extraction_lag": 2}
    assert opened["ppm2_median_shift"] == [(_utc("2026-09-19T15:56:22Z"),
                                            _utc("2026-09-26T05:07:11Z"))]
    lag = opened["text_extraction_lag"]
    assert lag[-1][1] is None and lag[-1][0] < _utc("2026-10-02T06:57:00Z")
    assert len(ladder.told) == 68
    assert statuses["ppm2_basis_floor_share"].count("fail") == 92
