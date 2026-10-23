"""Input replays: publishing fed production history and incident shapes, then through the ladder."""

# Real inputs are aggregates exported read-only from production on 2026-10-19 (UTC to the second;
# no ids, no URLs, no error text). Refresh them read-only, never by hand, from the SQL below. A
# replay rebuilds, at each 10-minute tick, the rows check_publishing's statements would return
# then, runs the check on them and feeds its result to the ladder (toolkit.system_alerts.reconcile).
# An arm a replay does not feed answers nothing, so it reads Not measured, which the ladder ignores.
#
# tests/fixtures/health_queue_7d.csv (at,source,arm,waiting,oldest_s): the detail queue as the
# ingest and changes arms read it, at each tick of the seven days detail_queue_completions keeps.
# A completion is the interval its queue row waited (enqueued_at to completed_at; a given-up row
# leaves the arms at its given_up completion), a row still queued at the export waits until then,
# and a NEW row whose ad was held before it was queued is a photo re-fetch, never ingest. Ticks with
# nothing waiting are left out; the window_end row is the export's tick.
#   with iv as (
#     select c.source, case when c.priority = 0 then 'ingest' else 'changes' end as arm,
#            c.enqueued_at, c.completed_at as done_at
#       from detail_queue_completions c
#      where c.priority in (1, 2)
#         or (c.priority = 0 and not exists (select 1 from listings l where l.source = c.source
#                and l.source_id_native = c.native_id and l.first_seen_at < c.enqueued_at))
#     union all
#     select q.source, case when q.priority = 0 then 'ingest' else 'changes' end,
#            q.enqueued_at, null
#       from listing_detail_queue q
#      where not q.given_up and (q.priority in (1, 2) or (q.priority = 0 and not exists (
#            select 1 from listings l
#             where l.source = q.source and l.source_id_native = q.native_id)))),
#   g as (
#     select g.at, iv.source, iv.arm, iv.enqueued_at
#       from iv cross join lateral generate_series(
#              date_bin('10 minutes', iv.enqueued_at, timestamptz '2026-01-01')
#                  + interval '10 minutes',
#              coalesce(iv.done_at, now()), interval '10 minutes') g(at)
#      where g.at < coalesce(iv.done_at, 'infinity'))
#   select at, source, arm, count(*), floor(extract(epoch from at - min(enqueued_at)))::int
#     from g group by 1, 2, 3
#   union all
#   select date_bin('10 minutes', now(), timestamptz '2026-01-01'), '', 'window_end', 0, 0
#    order by 1, 2, 3;
#
# tests/fixtures/health_attach_28d.csv (at,source,ads_24h,ads_72h,no_property): the visible arm's
# property stage, the one stage that keeps history: per tick and portal, the ads first written
# 30 min-24 h (and 30 min-72 h) before it and those of them whose property was born after it. Only
# pairs with such an ad are kept, 2026-09-21 05:30Z to 2026-10-19 04:30Z; a median row is the
# portal's usual cohort (its 6-hourly median), the replay's figure for every other tick. A merged
# ad's survivor is older than the ad, so it counts as attached at once: the stage is understated.
#   with late as materialized (
#     select l.source, l.first_seen_at, p.created_at as attached_at
#       from listings l join properties p on p.id = l.property_id
#      where l.first_seen_at >= '2026-09-20'
#        and p.created_at > l.first_seen_at + interval '30 minutes'),
#   t as (select generate_series(timestamptz '2026-09-21 05:30Z', timestamptz '2026-10-19 04:30Z',
#                                interval '10 minutes') as at),
#   lt as (select t.at, a.source, count(*) as no_property
#            from t join late a on a.attached_at > t.at
#                              and a.first_seen_at < t.at - interval '30 minutes'
#                              and a.first_seen_at >= t.at - interval '24 hours'
#           group by 1, 2)
#   select lt.at, lt.source,
#          (select count(*) from listings a where a.source = lt.source
#              and a.first_seen_at >= lt.at - interval '24 hours'
#              and a.first_seen_at < lt.at - interval '30 minutes'),
#          (select count(*) from listings a where a.source = lt.source
#              and a.first_seen_at >= lt.at - interval '72 hours'
#              and a.first_seen_at < lt.at - interval '30 minutes'),
#          lt.no_property
#     from lt order by 1, 2;
#  and the median rows, over every sixth hour of the same window:
#   with t as (select generate_series(timestamptz '2026-09-21 00:00Z',
#                                     timestamptz '2026-10-19 00:00Z', interval '6 hours') as at),
#   c as (select t.at, s.source,
#                (select count(*) from listings a where a.source = s.source
#                    and a.first_seen_at >= t.at - interval '24 hours'
#                    and a.first_seen_at < t.at - interval '30 minutes') as n24,
#                (select count(*) from listings a where a.source = s.source
#                    and a.first_seen_at >= t.at - interval '72 hours'
#                    and a.first_seen_at < t.at - interval '30 minutes') as n72
#           from t cross join unnest(array['bazos', 'bezrealitky', 'ceskereality', 'idnes',
#                 'maxima', 'mmreality', 'realitymix', 'remax', 'sreality']) s(source))
#   select 'median', source, percentile_disc(0.5) within group (order by n24),
#          percentile_disc(0.5) within group (order by n72), 0
#     from c group by source order by source;
#
# tests/fixtures/health_dirty_35d.csv (run_at,oldest_dirty_s,dirty_rows): the oldest dirty
# property's age as the retired property_maintenance check measured it (min(marked_at), the fleet
# arm's own input) on each of its runs over the 35 days pipeline_check_results kept; a row with
# neither figure measured nothing. Frozen: the key retires with this wave.
#   select run_at, round((details->>'oldest_dirty_hours')::numeric * 3600), details->>'dirty_depth'
#     from pipeline_check_results where check_key = 'property_maintenance' order by run_at;
#
# Stated limits: browse_list membership, the moment an ad's location became servable and a
# photo's store time keep no history, so the visible arm's other stages, the stock arm and the
# photo arm are replayed only through the synthetic shapes below, each labelled with the figures
# it reproduces.

from __future__ import annotations

import datetime as _dt
import functools
from typing import Any

from location_data.claims_common import SOURCES
from scripts.checks import publishing as pub
from scripts.verify_pipeline import THRESHOLDS
from tests.scripts.test_checks_platform import _Conn
from tests.scripts.test_checks_platform_replay import _fixture, _incidents, _ticks, _told, _utc
from tests.toolkit.test_system_alerts import Told, _FakeConn as _Ladder

_MINUTE, _HOUR = _dt.timedelta(minutes=1), _dt.timedelta(hours=1)
PORTALS = sorted(SOURCES)
# The completions' retention edge (each portal's drain prunes its own past 7 days) plus the longest
# wait of the week: the reconstruction is complete from here.
_QUEUE_FROM = _utc("2026-10-12T12:00:00Z")
_ATTACH_FROM, _ATTACH_TO = _utc("2026-09-21T05:30:00Z"), _utc("2026-10-19T04:30:00Z")


def _publishing_at(at: _dt.datetime, **rows: Any) -> dict[str, Any]:
    """check_publishing at `at` on the rows a replay names; every other arm answers nothing."""
    answers: dict[str, Any] = {pub._QUEUE_SQL: [], pub._VISIBLE_SQL: [], pub._STOCK_SQL: [],
                               pub._PHOTOS_SQL: [], pub._DIRTY_SQL: [(0, None)]}
    answers.update({getattr(pub, name): value for name, value in rows.items()})
    return pub.check_publishing(_Conn(answers, now=at), THRESHOLDS)


def _cell(result: dict[str, Any], subject: str) -> dict[str, Any]:
    return next(c for c in result["details"]["cells"] if c["subject"] == subject)


def _onsets(ladder: _Ladder) -> list[Told]:
    return [t for t in ladder.told if t.kind == "onset"]


def _named(results: list[tuple[_dt.datetime, dict[str, Any]]], onset: Told) -> str:
    """What an onset names: each subject with its cell's message at the onset's pass."""
    result = dict(results)[onset.at]
    return "; ".join(f"{s}: {_cell(result, s)['message']}" for s in onset.subjects)


# --- ingest and changes: the detail queue's seven days ---------------------------------------


@functools.cache
def _queue_replay() -> tuple[_Ladder, list[tuple[_dt.datetime, dict[str, Any]]]]:
    waiting: dict[_dt.datetime, list[tuple[Any, ...]]] = {}
    end = _QUEUE_FROM
    for row in _fixture("health_queue_7d.csv"):
        at = _utc(row["at"])
        if row["arm"] == "window_end":
            end = at
            continue
        oldest = at - _dt.timedelta(seconds=int(row["oldest_s"]))
        waiting.setdefault(at, []).append((row["source"], row["arm"], int(row["waiting"]), 0,
                                           oldest))
    results = [(at, _publishing_at(at, _QUEUE_SQL=waiting.get(at, [])))
               for at in _ticks(_QUEUE_FROM, end, 10 * _MINUTE)]
    return _told("publishing", 600, results), results


def test_a_week_of_the_detail_queue_opens_nothing() -> None:
    """10-12..10-19: every real wait sits under the 3 h Watch line, so no cell even reads Watch."""
    ladder, results = _queue_replay()
    assert _incidents(ladder, "publishing") == []
    ages = [c["value"] for _, r in results for c in r["details"]["cells"]
            if c["subject"].endswith(("/ingest", "/changes"))]
    assert 1.5 < max(ages) < THRESHOLDS["publishing_queue_warn_h"]
    assert {c["status"] for _, r in results for c in r["details"]["cells"]
            if c["subject"].endswith(("/ingest", "/changes"))} == {"ok"}


def test_the_politeness_burst_of_10_13_stays_green() -> None:
    """bazos queued ~1,100 new ads at once on 10-13 and fetched them over two hours (the oldest
    waited 119 min): queued ads are ingest's, and its cell stays OK the whole time."""
    _, results = _queue_replay()
    burst = [_cell(r, "bazos/ingest") for at, r in results if at.date() == _dt.date(2026, 10, 13)]
    assert max(c["facts"]["waiting"] for c in burst) > 1_000
    assert max(c["value"] for c in burst) >= 1.9
    assert {c["status"] for c in burst} == {"ok"}


def test_a_starved_new_queue_reads_watch_at_3_h_and_tells_one_problem_at_6_h() -> None:
    """SYNTHETIC: no queue history outlives seven days. 2026-08-17 as migration 444's header and
    scraper/db.py record it: refresh starved one portal's acquisition, 15,064 discovered ads were
    never fetched and the oldest waited 216 h (nine days at zero). Here that portal's new ads pile
    up at that rate for nine days while the others drain, then its drain resumes."""
    t0 = _utc("2026-08-08T00:00:00Z")
    stop = t0 + 216 * _HOUR
    results = []
    for at in _ticks(t0, stop + 2 * _HOUR, 10 * _MINUTE):
        hours = (at - t0) / _HOUR
        queue = ([("sreality", "ingest", round(15_064 * hours / 216), 0, t0)]
                 if 0 < hours and at <= stop else [])
        results.append((at, _publishing_at(at, _QUEUE_SQL=queue)))
    cells = {at: _cell(r, "sreality/ingest") for at, r in results}
    assert cells[t0 + 3 * _HOUR]["status"] == "ok"
    assert cells[t0 + 3 * _HOUR + 10 * _MINUTE]["status"] == "warn"
    assert cells[t0 + 6 * _HOUR]["status"] == "warn"
    assert cells[t0 + 6 * _HOUR + 10 * _MINUTE]["status"] == "fail"
    assert cells[stop]["value"] == 216.0 and cells[stop]["facts"]["waiting"] == 15_064
    ladder = _told("publishing", 600, results)
    opened = _incidents(ladder, "publishing")
    assert [(i["subject"], i["first_seen_at"]) for i in opened] == [
        ("sreality/ingest", t0 + 6 * _HOUR + 10 * _MINUTE)]
    assert opened[0]["resolve_reason"] == "measured ok" and opened[0]["resolved_at"] > stop
    assert len(_onsets(ladder)) == 1


def test_six_portals_raising_together_open_six_cells_told_in_one_notice() -> None:
    """SYNTHETIC: the design's test (b). The drain dies and six portals' new ads age together (the
    other three have none queued); at the first tick past 6 h six cells open, and one onset notice
    names all six."""
    t0 = _utc("2026-10-20T00:00:00Z")
    six = PORTALS[:6]
    results = [(at, _publishing_at(at, _QUEUE_SQL=[(p, "ingest", 40, 0, t0) for p in six]
                                   if at > t0 else []))
               for at in _ticks(t0, t0 + 7 * _HOUR, 10 * _MINUTE)]
    ladder = _told("publishing", 600, results)
    opened = _incidents(ladder, "publishing")
    assert sorted(i["subject"] for i in opened) == sorted(f"{p}/ingest" for p in six)
    assert {i["first_seen_at"] for i in opened} == {t0 + 6 * _HOUR + 10 * _MINUTE}
    onsets = _onsets(ladder)
    assert len(onsets) == 1 and onsets[0].subjects == tuple(sorted(f"{p}/ingest" for p in six))
    assert all(f"{p}/ingest: the oldest new ad" in _named(results, onsets[0]) for p in six)


# --- visible: the property stage over 28 days ----------------------------------------------


@functools.cache
def _attach_replay() -> tuple[_Ladder, list[tuple[_dt.datetime, dict[str, Any]]]]:
    usual: dict[str, tuple[int, int, int]] = {}
    late: dict[_dt.datetime, dict[str, tuple[int, int, int]]] = {}
    for row in _fixture("health_attach_28d.csv"):
        counts = (int(row["ads_24h"]), int(row["ads_72h"]), int(row["no_property"]))
        if row["at"] == "median":
            usual[row["source"]] = counts
        else:
            late.setdefault(_utc(row["at"]), {})[row["source"]] = counts
    results = []
    for at in _ticks(_ATTACH_FROM, _ATTACH_TO, 10 * _MINUTE):
        rows: list[tuple[Any, ...]] = []
        for source, default in sorted(usual.items()):
            day, wide, bare = late.get(at, {}).get(source, default)
            rows += [(source, True, "visible", day - bare), (source, True, "no_property", bare),
                     (source, False, "visible", wide - day)]
        results.append((at, _publishing_at(at, _VISIBLE_SQL=rows)))
    return _told("publishing", 600, results), results


def test_the_property_stage_rings_only_while_the_daily_sweep_holds_new_ads_back() -> None:
    """09-21..10-19: the daily full sweep holds the maintenance lease for up to its 100-min budget,
    and new ads wait for their property until it lets go (the late ones are born together then,
    between 09:00 and 15:00Z, 41-100 min after their first write). Five onsets on four days, the
    last on 10-09, each closed by a measured OK within 80 min: real (new ads out of Browse for up
    to 100 min), and quiet since the sweep shortened."""
    opened = _incidents(_attach_replay()[0], "publishing")
    assert [(i["subject"], i["first_seen_at"]) for i in opened] == [
        ("ceskereality/visible", _utc("2026-10-02T11:40:00Z")),
        ("sreality/visible", _utc("2026-10-02T12:10:00Z")),
        ("ceskereality/visible", _utc("2026-10-05T12:40:00Z")),
        ("sreality/visible", _utc("2026-10-08T12:50:00Z")),
        ("bezrealitky/visible", _utc("2026-10-09T12:50:00Z"))]
    for inc in opened:
        assert 9 <= inc["first_seen_at"].hour <= 13
        assert inc["resolve_reason"] == "measured ok"
        assert inc["resolved_at"] - inc["first_seen_at"] <= 80 * _MINUTE


def test_the_property_stage_is_quiet_since_the_sweep_shortened() -> None:
    """The sweep of 10-18 ran 887 s, so stragglers wait minutes, not an hour: no visible cell has
    read Watch since 10-17."""
    _, results = _attach_replay()
    after = _utc("2026-10-17T00:00:00Z")
    assert {c["status"] for at, r in results if at >= after for c in r["details"]["cells"]
            if c["subject"].endswith("/visible")} <= {"ok", "unknown"}


# --- stock: the 09-14 Browse drop and the 09-17 location loss ---------------------------------


def _stock_rows(fleet: int, fleet_missing: int,
                portals: dict[str, tuple[int, int]]) -> list[tuple[Any, ...]]:
    """_STOCK_SQL's rows: each missing property's canonical ad has no servable location."""
    return [*((p, n, missing, 0, 0, 0) for p, (n, missing) in portals.items()),
            (None, fleet, fleet_missing, 0, 0, 0)]


def test_the_browse_drop_of_09_14_fails_the_stock_arm_from_its_first_rebuild() -> None:
    """SYNTHETIC: browse_list keeps no history. 2026-09-14: the full-resolve sweep at 06:42Z
    re-judged 595,816 ads with no admissible claim, and the rebuild after it left Browse 45,810 of
    ~350,000 active properties (location_data/resolver/resolve_db.py; migration 522's header):
    ~87 % missing, every one with its location not served. Healthy before and again from 14:30
    (the stock the design measured on 10-18: 20 of 362,883 missing)."""
    t0, drop, back = (_utc("2026-09-14T00:00:00Z"), _utc("2026-09-14T06:50:00Z"),
                      _utc("2026-09-14T14:30:00Z"))
    results = []
    for at in _ticks(t0, back + 2 * _HOUR, 10 * _MINUTE):
        share = 304_190 / 350_000 if drop <= at < back else 0.0
        portals = {p: (n, round(n * share)) for p, n in (("bazos", 50_598), ("sreality", 105_000))}
        rows = _stock_rows(350_000, round(350_000 * share) or 20, portals)
        results.append((at, _publishing_at(at, _STOCK_SQL=rows)))
    assert _cell(results[0][1], "fleet/stock")["status"] == "ok"
    ladder = _told("publishing", 600, results)
    opened = _incidents(ladder, "publishing")
    assert sorted(i["subject"] for i in opened) == ["bazos/stock", "fleet/stock", "sreality/stock"]
    assert {i["first_seen_at"] for i in opened} == {drop}
    assert {i["resolve_reason"] for i in opened} == {"measured ok"}
    onsets = _onsets(ladder)
    assert len(onsets) == 1
    named = _named(results, onsets[0])
    assert "fleet/stock: 304,190 of 350,000 active properties" in named
    assert "304,190 location not served" in named


def test_the_location_loss_of_09_17_fails_that_portals_stock_and_the_fleet_only() -> None:
    """SYNTHETIC: 2026-09-17 (W18-b), 29,545 of 50,598 live bazos ads went 'undetermined' with no
    point (location_data/resolver/resolve_db.py), so their properties left Browse at the next
    rebuild: bazos reads 58 % missing and the fleet 8 % of ~350,000; sreality is untouched."""
    t0, loss = _utc("2026-09-17T00:00:00Z"), _utc("2026-09-17T09:00:00Z")
    results = []
    for at in _ticks(t0, loss + 3 * _HOUR, 10 * _MINUTE):
        missing = 29_545 if at >= loss else 0
        rows = _stock_rows(350_000, missing or 20,
                           {"bazos": (50_598, missing), "sreality": (105_000, 0)})
        results.append((at, _publishing_at(at, _STOCK_SQL=rows)))
    opened = _incidents(_told("publishing", 600, results), "publishing")
    assert sorted(i["subject"] for i in opened) == ["bazos/stock", "fleet/stock"]
    assert {i["first_seen_at"] for i in opened} == {loss}
    assert _cell(results[-1][1], "sreality/stock")["status"] == "ok"


# --- photos: the throttle of 09-25..10-06 ------------------------------------------------------


def test_a_photo_throttle_fails_the_photo_arm_whatever_the_unstored_photos_reason() -> None:
    """SYNTHETIC: no photo keeps its store time. The iDNES throttle (09-25..10-06; migration 585):
    28,160 photos of 2,140 new ads given up on 429 (17,793) and 403 (10,367), and new ads with
    given-up photos went from 5 % to 52 %. Here 52 % of a day's ~7,600 new-ad photos are not
    stored, first on 429, then on a CDN refusing our transform (stored as source_unavailable,
    still not stored), then everything is stored again."""
    t0 = _utc("2026-09-25T00:00:00Z")
    phases = [(t0, 76, "429 Client Error: Too Many Requests"),
              (t0 + 6 * _HOUR, 3_952, "429 Client Error: Too Many Requests"),
              (t0 + 12 * _HOUR, 3_952, "source_unavailable: 400 Client Error: Bad Request"),
              (t0 + 18 * _HOUR, 0, None)]
    results = []
    for at in _ticks(t0, t0 + 20 * _HOUR, 10 * _MINUTE):
        _, unstored, reason = [p for p in phases if p[0] <= at][-1]
        row = ("idnes", 414, 0, 7_600, unstored, 7_600 - unstored, 40, reason,
               unstored if reason else None)
        results.append((at, _publishing_at(at, _PHOTOS_SQL=[row])))
    cells = {at: _cell(r, "idnes/photos") for at, r in results}
    assert cells[t0]["status"] == "ok"
    assert cells[t0 + 6 * _HOUR]["status"] == "fail"
    assert cells[t0 + 12 * _HOUR]["status"] == "fail"
    assert "top reason: source_unavailable: 400 Client Error: Bad Request" in (
        cells[t0 + 12 * _HOUR]["message"])
    opened = _incidents(_told("publishing", 600, results), "publishing")
    assert [(i["subject"], i["first_seen_at"]) for i in opened] == [
        ("idnes/photos", t0 + 6 * _HOUR)]
    assert opened[0]["resolved_at"] > t0 + 18 * _HOUR


# --- the fleet's dirty queue: 35 days of the retired check's input ---------------------------


@functools.cache
def _dirty_replay() -> tuple[_Ladder, list[tuple[_dt.datetime, dict[str, Any]]]]:
    results = []
    for row in _fixture("health_dirty_35d.csv"):
        if not row["dirty_rows"]:
            continue  # that run measured nothing
        at = _utc(row["run_at"])
        oldest = (at - _dt.timedelta(seconds=int(row["oldest_dirty_s"]))
                  if row["oldest_dirty_s"] else None)
        results.append((at, _publishing_at(at, _DIRTY_SQL=[(int(row["dirty_rows"]), oldest)])))
    return _told("publishing", 600, results), results


def test_thirty_five_days_of_the_dirty_queue_open_nothing() -> None:
    """09-14..10-19: the oldest dirty property never neared the 8 h Problem line. The one Watch is
    the 09-22 mass mark of 184,670 properties draining (4.2 h at 12:58Z), which the retired check
    failed at its 3 h line."""
    ladder, results = _dirty_replay()
    assert len(results) > 250
    assert _incidents(ladder, "publishing") == []
    watch = [(at, _cell(r, "fleet/dirty")["value"]) for at, r in results
             if _cell(r, "fleet/dirty")["status"] != "ok"]
    assert watch == [(_utc("2026-09-22T12:58:28Z"), 4.2)]
