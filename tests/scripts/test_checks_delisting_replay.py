"""Input replays: production history fed through the delisting check, then through the ladder."""

# Each fixture is an aggregate exported read-only from production on 2026-10-19 (UTC to the
# second; no ids, no URLs, no error text). Refresh them read-only, never by hand, from the SQL
# below. A replay rebuilds, at each simulated hourly tick, the rows a check's SQL would return
# then, runs check_delisting on them and feeds its result to the ladder.
#
# tests/fixtures/health_outflow_28d.csv (hour,source,ads): ads made inactive per portal and hour
# from 2026-08-23, so the 28 days replayed from 09-21 each have their four weeks back. It holds the
# 10-10 zero-flip day: no stamp from 2026-10-09 21:39:52Z to 2026-10-11 06:04:18Z.
#   select date_trunc('hour', inactive_at), source, count(*)
#     from listings where inactive_at >= '2026-08-23' group by 1, 2 order by 1, 2;
#
# tests/fixtures/health_completions_7d.csv (arm,hour,source,priority,rows,gone,flipped,given_up,
# failed,active_again,enqueued_s,max_wait_s,new_ads,new_enqueued_s,verdict_s,dwell_max_s), the
# detail ledger's whole 7 days (exported 2026-10-19 05:13Z, from 10-12 01:08Z) in three arms. A
# *_s column is seconds from the row's hour (negative before it), max_wait_s and dwell_max_s are
# durations; an empty cell is no such row.
#  ledger: detail_queue_completions per completion hour, portal and priority: rows, the gone /
#  flipped / given_up outcomes (the rest are written), rows with a failed attempt, flipped ads
#  active at export, the oldest enqueue, the longest enqueue-to-completion wait, the NEW-priority
#  rows whose ad had no listings row at enqueue and the oldest of them, and for the flips their
#  oldest first unrefuted gone verdict (the earliest gone row within 12 h before the last one
#  before the flip, newer than a still-inactive ad's last sighting) and longest verdict-to-flip.
#   with c as (select c.*, date_trunc('hour', c.completed_at) as hour
#                from detail_queue_completions c),
#        fl as (select c.id, l.is_active,
#                      (select min(g.completed_at) from detail_queue_completions g
#                        where g.source = c.source and g.native_id = c.native_id
#                          and g.outcome = 'gone' and g.completed_at < c.completed_at
#                          and g.completed_at > lg.last_gone - interval '12 hours'
#                          and g.completed_at > coalesce(case when l.last_seen_at
#                              < c.completed_at then l.last_seen_at end, '-infinity')) first_gone
#                 from c left join listings l
#                   on l.source = c.source and l.source_id_native = c.native_id
#                cross join lateral (select max(g.completed_at) last_gone
#                                      from detail_queue_completions g
#                                     where g.source = c.source and g.native_id = c.native_id
#                                       and g.outcome = 'gone'
#                                       and g.completed_at < c.completed_at) lg
#                where c.outcome = 'flipped'),
#        nw as (select c.id from c left join listings l
#                 on l.source = c.source and l.source_id_native = c.native_id
#                where c.priority = 0 and (l.id is null or l.first_seen_at > c.enqueued_at))
#   select 'ledger', c.hour, c.source, c.priority, count(*),
#          count(*) filter (where outcome = 'gone'), count(*) filter (where outcome = 'flipped'),
#          count(*) filter (where outcome = 'given_up'), count(*) filter (where attempts > 0),
#          count(*) filter (where fl.is_active),  -- printed only where flipped > 0
#          floor(extract(epoch from min(c.enqueued_at) - c.hour)),
#          ceil(extract(epoch from max(c.completed_at - c.enqueued_at))),
#          count(nw.id),  -- printed only at priority 0
#          floor(extract(epoch from min(c.enqueued_at) filter (where nw.id is not null) - c.hour)),
#          floor(extract(epoch from min(fl.first_gone) - c.hour)),
#          ceil(extract(epoch from max(c.completed_at - fl.first_gone)))
#     from c left join fl on fl.id = c.id left join nw on nw.id = c.id
#    group by 2, 3, 4 order by 2, 3, 4;
#  pending: at 05:14Z, active ads with an unrefuted gone verdict (db.gone_evidence's reading), per
#  portal and first-verdict hour: rows = ads, verdict_s = the earliest.
#   with p as (select l.source, min(c.completed_at) first_gone
#                from detail_queue_completions c join listings l
#                  on l.source = c.source and l.source_id_native = c.native_id
#               where c.outcome = 'gone' and l.is_active
#                 and c.completed_at > coalesce(l.last_seen_at, '-infinity')
#               group by l.source, l.id)
#   select 'pending', date_trunc('hour', first_gone), source, null, count(*), null, null, null,
#          null, null, null, null, null, null,
#          floor(extract(epoch from min(first_gone) - date_trunc('hour', first_gone))), null
#     from p group by 2, 3 order by 2, 3;
#  queued: at 05:14Z, listing_detail_queue per enqueue hour, portal and priority: rows, given_up,
#  failed (attempts > 0), enqueued_s = the oldest.
#   select 'queued', date_trunc('hour', enqueued_at), source, priority, count(*), null, null,
#          count(*) filter (where given_up), count(*) filter (where attempts > 0), null,
#          floor(extract(epoch from min(enqueued_at) - date_trunc('hour', enqueued_at))),
#          null, null, null, null, null
#     from listing_detail_queue group by 2, 3, 4 order by 2, 3, 4;
#
# tests/fixtures/health_delisting_walks_40d.csv (started_at,source,duration_s,reached,deferred):
# every finished index walk since walk_reached_end was first written (2026-09-08 20:36Z), whether
# any of its entries reached the portal's end, and its entries' verify_deferred summed.
#   select r.started_at, r.source, ceil(extract(epoch from r.ended_at - r.started_at)),
#          bool_or(coalesce((c->>'walk_reached_end')::boolean, false))::int,
#          coalesce(sum((c->>'verify_deferred')::int), 0)
#     from scrape_runs r cross join lateral jsonb_array_elements(case when
#          jsonb_typeof(r.by_category) = 'array' then r.by_category end) c
#    where r.run_type = 'index' and r.ended_at is not null and r.started_at >= '2026-09-08 21:00'
#    group by r.id, r.source, r.started_at, r.ended_at order by 1, 2;
#
# Stated limits. inactive_at is cleared on a revival, so the outflow's older hours are net of
# revivals. "Active again" is read at export, not at each tick: a cohort's later revivals count
# early, a re-list among them, so the replay's precision is an upper bound. A cohort is read once
# its day lies in the ledger, on whole hours. The stuck arm sees the episodes that ended in a flip (10-12's flips, whose verdicts
# predate the ledger, carry none) and the verdicts pending at export; a verdict refuted by a
# sighting leaves no time in the ledger, so those episodes are missing, and each hour's flips
# count as stuck together once its oldest verdict passes the limit (an upper bound). The ledger
# keeps no claim history, so the replayed backlog reads no claimed row. Synthetic cohorts are
# labelled so where they are built.

from __future__ import annotations

import datetime as _dt
import functools
import itertools
from typing import Any
from unittest import mock

from scripts.checks import delisting as dl
from scripts.verify_pipeline import THRESHOLDS
from tests.scripts.test_checks_platform import _Conn
from tests.scripts.test_checks_platform_replay import _fixture, _incidents, _ticks, _told, _utc

_HOUR, _DAY = _dt.timedelta(hours=1), _dt.timedelta(days=1)
_KEY = "delisting"
_GAP = (_utc("2026-10-09T21:39:52Z"), _utc("2026-10-11T06:04:18Z"))
_EXPORTED = _utc("2026-10-19T05:00:00Z")
_LEDGER_FROM = _utc("2026-10-12T01:00:00Z")


def _int(text: str) -> int:
    return int(text) if text else 0


def _hourly(rows: list[tuple[str, _dt.datetime, int]]) -> tuple[_dt.datetime, dict[str, list[int]]]:
    """Per portal, prefix sums over a dense hourly axis from the earliest hour."""
    base = min(at for _, at, _ in rows)
    span = int((max(at for _, at, _ in rows) - base) / _HOUR) + 1
    dense: dict[str, list[int]] = {}
    for source, at, n in rows:
        dense.setdefault(source, [0] * span)[int((at - base) / _HOUR)] += n
    return base, {s: [0, *itertools.accumulate(v)] for s, v in sorted(dense.items())}


def _sum(prefix: list[int], base: _dt.datetime, start: _dt.datetime, end: _dt.datetime) -> int:
    """The hours in [start, end), both on the hour."""
    i, j = (min(len(prefix) - 1, max(0, int((x - base) / _HOUR))) for x in (start, end))
    return prefix[j] - prefix[i]


@functools.cache
def _fleet() -> tuple[str, ...]:
    """The portals the recorded window held. A portal onboarded since is absent from every
    fixture here, so the replay judges the fleet it recorded, never a later portal as unknown."""
    fleet = tuple(sorted({r["source"] for r in _fixture("health_delisting_walks_40d.csv")}))
    assert set(fleet) <= set(dl.SOURCES)
    return fleet


def _replay(ticks: list[_dt.datetime], answers: Any,
            thresholds: dict[str, Any] = THRESHOLDS) -> tuple[Any, list[tuple[Any, Any]]]:
    """check_delisting at each tick on `answers(at)`, told through the ladder."""
    with mock.patch.object(dl, "SOURCES", _fleet()):
        results = [(at, dl.check_delisting(_Conn(answers(at), now=at), thresholds))
                   for at in ticks]
    return _told(_KEY, 600, results), results


def _arm(results: list[tuple[Any, Any]], subject: str) -> list[tuple[_dt.datetime, dict[str, Any]]]:
    """One cell's readings, tick by tick."""
    return [(at, c) for at, r in results for c in r["details"]["cells"] if c["subject"] == subject]


# --- the evidence ----------------------------------------------------------------------------


@functools.cache
def _outflow() -> tuple[_dt.datetime, dict[str, list[int]]]:
    return _hourly([(r["source"], _utc(r["hour"]), int(r["ads"]))
                    for r in _fixture("health_outflow_28d.csv")])


@functools.cache
def _completions() -> list[dict[str, str]]:
    return _fixture("health_completions_7d.csv")


@functools.cache
def _flips(revive: tuple[str, str, float] | None = None) -> tuple[Any, ...]:
    """Flips and flips active again per portal and hour, prefix-summed."""
    # revive = (portal, day, share), SYNTHETIC: that day's flips set that share active again.
    flips, back = [], []
    for r in _completions():
        if r["arm"] == "ledger" and _int(r["flipped"]):
            at, n = _utc(r["hour"]), _int(r["flipped"])
            again = _int(r["active_again"])
            if revive and r["source"] == revive[0] and r["hour"].startswith(revive[1]):
                again = round(n * revive[2])
            flips.append((r["source"], at, n))
            back.append((r["source"], at, again))
    return _hourly(flips), _hourly(back)


@functools.cache
def _walks() -> list[tuple[str, _dt.datetime, _dt.datetime, bool, int]]:
    """(portal, start, end, reached the end, deferred) per finished walk."""
    rows = []
    for r in _fixture("health_delisting_walks_40d.csv"):
        start = _utc(r["started_at"])
        rows.append((r["source"], start, start + _dt.timedelta(seconds=int(r["duration_s"])),
                     r["reached"] == "1", int(r["deferred"])))
    return rows


def _walk_rows(at: _dt.datetime) -> list[tuple[Any, ...]]:
    """_WALKS_SQL's rows at `at`: the walks finished by then that started in the 14 days before."""
    since = at - dl._WALK_DAYS * _DAY
    return [(s, start, reached, deferred) for s, start, end, reached, deferred in _walks()
            if since < start and end <= at]


def _outflow_rows(at: _dt.datetime, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    """_OUTFLOW_SQL's rows at `at`: each week back's 24 h at every offset, this 24 h at 0 only."""
    base, prefix = _outflow()
    rows = []
    align = params["align_h"]
    for source, sums in prefix.items():
        for lag in params["lags"]:
            for off in range(-align, align + 1) if lag else (0,):
                end = at - lag * _DAY + off * _HOUR
                n = _sum(sums, base, end - _DAY, end)
                if n:
                    rows.append((source, lag, off, n))
    return rows


def _precision_rows(at: _dt.datetime, revive: tuple[str, str, float] | None,
                    params: dict[str, Any]) -> list[Any]:
    """_PRECISION_SQL's rows at `at`: each portal's day of flips from the young edge it was sent."""
    (base, flips), (_, back) = _flips(revive)
    rows = []
    for source, young_s in zip(params["sources"], params["young_s"]):
        young = at - _dt.timedelta(seconds=young_s)
        old = young - _dt.timedelta(seconds=params["span_s"])
        n = _sum(flips[source], base, old, young) if source in flips else 0
        if n and old >= _LEDGER_FROM:
            rows.append((source, n, _sum(back[source], base, old, young)))
    return rows


@functools.cache
def _episodes() -> dict[str, list[tuple[_dt.datetime, _dt.datetime | None, int]]]:
    """Per portal: (oldest first verdict, its flip hour or None while pending at export, ads)."""
    out: dict[str, list[tuple[_dt.datetime, _dt.datetime | None, int]]] = {}
    for r in _completions():
        hour = _utc(r["hour"])
        if r["arm"] == "ledger" and _int(r["flipped"]) and r["verdict_s"]:
            out.setdefault(r["source"], []).append(
                (hour + _dt.timedelta(seconds=int(r["verdict_s"])), hour, _int(r["flipped"])))
        elif r["arm"] == "pending":
            out.setdefault(r["source"], []).append(
                (hour + _dt.timedelta(seconds=int(r["verdict_s"])), None, _int(r["rows"])))
    return out


def _stuck_rows(at: _dt.datetime, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    """_STUCK_SQL's rows at `at` against the limits the check sent (see the stated limits)."""
    limits = dict(zip(params["sources"], params["limits_s"]))
    rows = []
    for source, episodes in sorted(_episodes().items()):
        open_ = [(first, n) for first, flip, n in episodes
                 if first <= at and (flip is None or flip >= at)]
        if not open_:
            continue
        limit = _dt.timedelta(seconds=limits.get(source, params["fallback_s"]))
        stuck = sum(n for first, n in open_ if first < at - limit)
        oldest = min(first for first, _ in open_)
        rows.append((source, sum(n for _, n in open_), stuck, (at - oldest).total_seconds()))
    return rows


@functools.cache
def _verify_waits() -> dict[str, list[tuple[_dt.datetime, _dt.datetime | None, int]]]:
    """Per portal: (oldest enqueue, completion hour or None while queued at export, rows)."""
    out: dict[str, list[tuple[_dt.datetime, _dt.datetime | None, int]]] = {}
    for r in _completions():
        if r["priority"] != str(dl.QUEUE_PRIORITY_VERIFY):
            continue
        hour = _utc(r["hour"])
        enqueued = hour + _dt.timedelta(seconds=int(r["enqueued_s"]))
        if r["arm"] == "ledger":
            out.setdefault(r["source"], []).append((enqueued, hour, _int(r["rows"])))
        elif r["arm"] == "queued" and _int(r["rows"]) > _int(r["given_up"]):
            out.setdefault(r["source"], []).append((enqueued, None, _int(r["rows"])))
    return out


def _verify_rows(at: _dt.datetime) -> list[tuple[Any, ...]]:
    """_VERIFY_SQL's rows at `at`: each hour's rows queued then, aged from that hour's oldest."""
    rows = []
    for source, waits in sorted(_verify_waits().items()):
        queued = [(enqueued, n) for enqueued, done, n in waits
                  if enqueued <= at and (done is None or done >= at)]
        if queued:
            rows.append((source, sum(n for _, n in queued), 0,
                         (at - min(enqueued for enqueued, _ in queued)).total_seconds()))
    return rows


# --- the replays -------------------------------------------------------------------------------


@functools.cache
def _outflow_replay(align_h: int | None = None) -> tuple[Any, list[tuple[Any, Any]]]:
    """09-21..10-19 hourly on the outflow alone (no walk, so stuck and deferrals are unread)."""
    ticks = _ticks(_utc("2026-09-21T00:00:00Z"), _EXPORTED, _HOUR)
    thresholds = dict(THRESHOLDS)
    if align_h is not None:
        thresholds["delisting_outflow_align_h"] = align_h
    return _replay(ticks, lambda at: {
        dl._WALKS_SQL: [], dl._OUTFLOW_SQL: functools.partial(_outflow_rows, at),
        dl._VERIFY_SQL: [], dl._PRECISION_SQL: []}, thresholds)


@functools.cache
def _ledger_replay(revive: tuple[str, str, float] | None = None) -> tuple[Any, list[Any]]:
    """10-12..10-19 hourly on the ledger's arms."""
    ticks = _ticks(_utc("2026-10-12T02:00:00Z"), _EXPORTED, _HOUR)
    return _replay(ticks, lambda at: {
        dl._WALKS_SQL: _walk_rows(at), dl._OUTFLOW_SQL: [], dl._VERIFY_SQL: _verify_rows(at),
        dl._PRECISION_SQL: functools.partial(_precision_rows, at, revive),
        dl._STUCK_SQL: functools.partial(_stuck_rows, at)})


@functools.cache
def _walks_replay() -> tuple[Any, list[tuple[Any, Any]]]:
    """09-23..10-19 hourly on the walks alone: two full weeks of finished walks at every tick."""
    ticks = _ticks(_utc("2026-09-23T00:00:00Z"), _EXPORTED, _HOUR)
    return _replay(ticks, lambda at: {dl._WALKS_SQL: _walk_rows(at), dl._OUTFLOW_SQL: [],
                                      dl._VERIFY_SQL: [], dl._PRECISION_SQL: [],
                                      dl._STUCK_SQL: []})


def _onsets(ladder: Any, arm: str) -> list[tuple[str, str]]:
    """(subject, onset hour) of every incident the replay opened on this arm, in onset order."""
    return [(i["subject"], i["first_seen_at"].strftime("%m-%d %H:%M"))
            for i in _incidents(ladder, _KEY) if i["subject"].endswith(f"/{arm}")]


# --- outflow ------------------------------------------------------------------------------------


def test_the_outflow_fixture_holds_the_zero_flip_gap() -> None:
    inside = [r for r in _fixture("health_outflow_28d.csv")
              if _GAP[0] < _utc(r["hour"]) and _utc(r["hour"]) + _HOUR <= _GAP[1]]
    assert inside == []


def test_the_zero_flip_day_rings_every_measured_portal_and_each_closes_on_an_ok() -> None:
    """10-10, 32 h without a flip; remax, mmreality and maxima sit under the floor: Not measured."""
    # mmreality's fixed-window median sat at the floor (50) and read Problem for two hours; the
    # median of its quietest windows sits under it, so it is Not measured, as remax always was.
    ladder, results = _outflow_replay()
    gap = [i for i in _incidents(ladder, _KEY) if _GAP[0] <= i["first_seen_at"] <= _GAP[1]]
    assert sorted(i["subject"] for i in gap) == [
        f"{p}/outflow" for p in ("bazos", "bezrealitky", "ceskereality", "idnes", "realitymix",
                                 "sreality")]
    assert all(i["resolved_at"] > _GAP[1] and i["resolve_reason"] == "measured ok" for i in gap)
    for portal in ("remax", "mmreality", "maxima"):
        empty = [(at, c) for at, c in _arm(results, f"{portal}/outflow")
                 if c["facts"]["inactive_24h"] == 0 and _GAP[0] <= at <= _GAP[1] + _DAY]
        assert empty and {c["status"] for _, c in empty} == {"unknown"}, portal


def test_three_quiet_stretches_open_no_outflow_incident() -> None:
    """09-21..09-28, 10-02..10-10 (10-05's late walk inside) and 10-14..10-19: 21 days of 28."""
    # The 28 days hold no onset-free 14 days: the real stalls of 09-28/29, 10-10 and 10-12/13 cut
    # them, so these stretches of 5-8 days are the design's 14-28 d quiet windows as the data
    # allows them, each from the last recovery to the next real onset.
    quiet = [(_utc("2026-09-21T00:00:00Z"), _utc("2026-09-28T20:00:00Z")),
             (_utc("2026-10-02T14:00:00Z"), _utc("2026-10-10T12:00:00Z")),
             (_utc("2026-10-14T03:00:00Z"), _EXPORTED)]
    opened = _incidents(_outflow_replay()[0], _KEY)
    assert [i for i in opened if any(a <= i["first_seen_at"] <= b for a, b in quiet)] == []


def test_every_other_outflow_onset_is_a_real_stall_that_closed_once_its_portal_caught_up() -> None:
    """09-28/29, ceskereality's 403 wall (09-29..10-02) and the dwell's 10-12/13 tail."""
    # Each portal held under a fifth of its quietest usual 24 h; the wall made no flip from 09-29
    # 07:00 to 10-02 10:00 and its catch-up burst read as a spike (Watch, 10-02/03). idnes's
    # 09-28/29 and sreality's 10-13 shares stayed above a fifth: Watch only (the next test).
    ladder, _ = _outflow_replay()
    outside = [i for i in _incidents(ladder, _KEY) if not _GAP[0] <= i["first_seen_at"] <= _GAP[1]]
    assert [(i["subject"], i["first_seen_at"].strftime("%m-%d")) for i in outside] == [
        ("realitymix/outflow", "09-28"), ("ceskereality/outflow", "09-29"),
        ("bazos/outflow", "10-12"), ("realitymix/outflow", "10-12"),
        ("mmreality/outflow", "10-12"), ("idnes/outflow", "10-13"), ("remax/outflow", "10-13")]
    assert all(i["resolve_reason"] == "measured ok" for i in outside)
    wall = next(i for i in outside if i["subject"] == "ceskereality/outflow")
    assert wall["resolved_at"].strftime("%m-%d") == "10-02"


def test_aligned_windows_silence_the_10_05_walk_shift_and_keep_every_stall_day() -> None:
    """realitymix's 10-05: a normal Monday whose midday walk ran 1 h 53 min late."""
    # On fixed windows (align 0) its burst left the window the baseline weeks held it in, and with
    # Sunday's 16 h walk lost, 87 ads against a median of 516 read Problem for an hour (544 flips
    # that day; 602-749 on 09-07, 09-14 and 09-21). Aligned by 2, 3 or 4 h, 10-05 stays OK and
    # the same 13 incidents open, by portal and day. No alignment (1 h neither) keeps idnes's
    # 09-29 00:00 Problem, 19.7 % where a baseline week's midnight burst entered a fixed window as
    # 10-05's left one, and only 1 h keeps sreality's 10-13 one (19.4-20.0 %): both stalls read
    # Watch, at 24-47 % and 20-49 % of their quietest windows, and their days still open
    # incidents on other portals. 3 h is kept: 2 h's incidents with an hour over 10-05's shift.
    fixed = _onsets(_outflow_replay(0)[0], "outflow")
    assert {("realitymix/outflow", "10-05 15:00"), ("idnes/outflow", "09-29 00:00"),
            ("sreality/outflow", "10-13 02:00")} <= set(fixed)
    by_day = {}
    for align in (2, 3, 4):
        onsets = _onsets(_outflow_replay(align)[0], "outflow")
        assert not [at for _, at in onsets if at.startswith("10-05")], align
        by_day[align] = sorted({(subject, at[:5]) for subject, at in onsets})
    assert len(by_day[3]) == 13 and by_day[2] == by_day[3] == by_day[4]
    assert THRESHOLDS["delisting_outflow_align_h"] == 3
    _, results = _outflow_replay()
    for portal, start, end in (("idnes", "2026-09-28T12:00:00Z", "2026-09-29T15:00:00Z"),
                               ("sreality", "2026-10-12T22:00:00Z", "2026-10-13T19:00:00Z")):
        stall = [c for at, c in _arm(results, f"{portal}/outflow") if _utc(start) <= at < _utc(end)]
        assert {c["status"] for c in stall} == {"ok", "warn"}, portal
        assert min(c["facts"]["share"] for c in stall) > THRESHOLDS["delisting_outflow_fail_share"]


# --- precision -----------------------------------------------------------------------------------


def test_the_first_post_dwell_cohorts_ring_precision_where_their_ads_came_back() -> None:
    """The 10-13 01-03Z flips (sreality 231 of 472 back, idnes 136 of 318) ring once they enter
    each portal's window (15-39 h, 20-44 h) and close a day on (they rang at 48 h, closed 10-16)."""
    ladder, results = _ledger_replay()
    assert _onsets(ladder, "precision") == [("sreality/precision", "10-13 20:00"),
                                            ("idnes/precision", "10-14 05:00")]
    for inc in _incidents(ladder, _KEY):
        onset = dict(_arm(results, inc["subject"]))[inc["first_seen_at"]]
        assert onset["status"] == "fail" and onset["facts"]["lower_bound"] > 0.15, onset
        assert inc["resolve_reason"] == "measured ok"
    assert {i["subject"]: i["resolved_at"].strftime("%m-%d %H:%M") for i in _incidents(
        ladder, _KEY)} == {"sreality/precision": "10-14 21:00", "idnes/precision": "10-15 00:00"}


def test_once_that_tail_aged_out_the_ledgers_week_opens_nothing() -> None:
    """From 10-16 the steady state: sreality ~5-8 % active again, every other portal lower."""
    # But for sreality's last 12 h: from 10-18 17:00 its window holds the 10-18 01Z wave (737
    # flips, 210 active at export, among them the whole sreality portfolios of the two agencies
    # the idnes Problem traced). Read at export, 28 h on, it counts returns the check at 17:00,
    # 16 h on, cannot see yet: a Watch the stated limits make an upper bound, and no incident.
    ladder, results = _ledger_replay()
    after = _utc("2026-10-16T04:00:00Z")
    assert [i for i in _incidents(ladder, _KEY) if i["first_seen_at"] >= after] == []
    late = [(at, c) for at, r in results if at >= after for c in r["details"]["cells"]
            if c["subject"].endswith("/precision")]
    watch = [(at, c["subject"]) for at, c in late if c["status"] not in {"ok", "unknown"}]
    assert late and {c["status"] for _, c in late} == {"ok", "unknown", "warn"}
    assert {subject for _, subject in watch} == {"sreality/precision"}
    assert min(at for at, _ in watch) == _utc("2026-10-18T17:00:00Z")


def test_a_day_revived_at_thirty_percent_rings_precision_as_it_matures() -> None:
    """SYNTHETIC (h): sreality's 10-16 flips set 30 % active again (the blink read 26-49 %)."""
    # The real week rings nothing then; this one rings on 10-17, as the day's flips enter the
    # 15-39 h window (it rang on 10-18 at 48 h), and every other incident is the real replay's.
    real, _ = _ledger_replay()
    revived, _ = _ledger_replay(("sreality", "2026-10-16", 0.30))
    extra = [o for o in _onsets(revived, "precision") if o not in _onsets(real, "precision")]
    assert len(extra) == 1 and extra[0][0] == "sreality/precision"
    assert "10-17 00:00" <= extra[0][1] < "10-18 00:00"


# --- stuck, verify backlog, deferrals ----------------------------------------------------------


def test_no_ad_waited_past_its_portals_confirmation_limit_all_week() -> None:
    """The closest call: a bazos verdict 40.8 h old on 10-16 against its 44.3 h limit."""
    _, results = _ledger_replay()
    stuck = [c for _, r in results for c in r["details"]["cells"]
             if c["subject"].endswith("/stuck")]
    assert stuck and {(c["status"], c["value"]) for c in stuck} == {("ok", 0)}
    closest = max(c["facts"]["oldest_s"] / c["facts"]["limit_s"] for c in stuck
                  if c["facts"]["oldest_s"] is not None)
    assert 0.9 < closest < 1


def test_the_presence_checks_never_waited_past_watch_through_the_bazos_burst() -> None:
    """The 10-13 burst (1,110 ads waiting at once, the oldest 119 min) is ingest's; checks waited 189 min at most."""
    _, results = _ledger_replay()
    backlog = [(at, c) for at, r in results for c in r["details"]["cells"]
               if c["subject"].endswith("/verify_backlog")]
    assert {c["status"] for _, c in backlog} == {"ok"}
    assert max(c["value"] for _, c in backlog) < THRESHOLDS["delisting_verify_warn_h"] * 60
    burst = [c for at, c in backlog if at.strftime("%m-%d") == "10-13"
             and c["subject"] == "bazos/verify_backlog"]
    assert burst and all(c["status"] == "ok" for c in burst)


def test_the_10_13_catch_up_reads_watch_on_its_deferrals_and_rings_nothing() -> None:
    """ceskereality's 10-13..15 catch-up deferred on two walks in a row, then three: no notice."""
    ladder, results = _walks_replay()
    flagged = [(at, c) for at, r in results for c in r["details"]["cells"]
               if c["subject"].endswith("/deferrals") and c["status"] != "ok"]
    assert flagged and {c["subject"] for _, c in flagged} == {"ceskereality/deferrals"}
    assert {c["status"] for _, c in flagged} == {"warn"}
    assert all(_utc("2026-10-13T00:00:00Z") <= at < _utc("2026-10-16T00:00:00Z")
               for at, _ in flagged)
    assert _incidents(ladder, _KEY) == []
