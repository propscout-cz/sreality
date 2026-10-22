"""Input replays: production history through check_scraping and check_coverage, then the ladder."""

# Each fixture is an aggregate exported read-only from production on 2026-10-19 (UTC to the
# second; no ids, no URLs, no error text). Refresh them read-only, never by hand, from the SQL
# below. A replay rebuilds, at each simulated tick, the rows a check's SQL would return then, runs
# the check on them and feeds its result to the ladder (toolkit.system_alerts.reconcile).
#
# tests/fixtures/health_walks_28d.csv (source,started_at,duration_s,walked,unreached,short): every
# finished index walk started since 2026-09-14 (exported 05:33Z): the units it walked ('*' = the
# same as that portal's previous walk), the units that did not reach the portal's end, and each
# end-reached unit that collected fewer ads than its portal declared, as unit=declared/collected
# (a unit that collected every declared ad carries no totals).
#   with w as (select source, started_at, extract(epoch from ended_at - started_at)::int dur,
#                     by_category from scrape_runs
#               where run_type = 'index' and started_at >= '2026-09-14' and ended_at is not null
#                 and jsonb_typeof(by_category) = 'array' and jsonb_array_length(by_category) > 0),
#        u as (select w.source, w.started_at, w.dur, coalesce(e->>'category_main', 'none') || '-'
#                     || coalesce(e->>'category_type', 'none') unit,
#                     bool_and(coalesce((e->>'walk_reached_end')::boolean, false)) reached,
#                     sum((e->>'sreality_result_size')::bigint) declared,
#                     sum((e->>'collected')::bigint) collected
#                from w cross join lateral jsonb_array_elements(w.by_category) e
#               group by 1, 2, 3, 4),
#        r as (select source, started_at, dur, string_agg(unit, ' ' order by unit) walked,
#                     coalesce(string_agg(unit, ' ' order by unit) filter (where not reached), '')
#                         unreached,
#                     coalesce(string_agg(unit || '=' || declared || '/' || collected, ' '
#                                         order by unit)
#                              filter (where reached and collected < declared), '') short
#                from u group by 1, 2, 3)
#   select source, to_char(started_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'), dur,
#          case when walked = lag(walked) over (partition by source order by started_at)
#               then '*' else walked end, unreached, short
#     from r order by source, started_at;
#
# tests/fixtures/health_inflow_28d.csv (source,day,hours): ads written per portal and UTC hour
# (24 counts a day) from 2026-08-17 to 10-18, the 28 replayed days and their 35-day lead-in
# (exported 05:41Z). <portals> is array['bazos', 'bezrealitky', 'ceskereality', 'idnes', 'maxima',
# 'mmreality', 'realitymix', 'remax', 'sreality'].
#   with c as (select source, date_trunc('hour', first_seen_at) hour, count(*) n from listings
#               where source = any(<portals>) and first_seen_at >= '2026-08-17'
#                 and first_seen_at < '2026-10-19' group by 1, 2),
#        g as (select s.source, h.hour from unnest(<portals>) s(source)
#                cross join generate_series('2026-08-17'::timestamptz,
#                                           '2026-10-18 23:00'::timestamptz, '1 hour') h(hour))
#   select g.source, to_char(g.hour at time zone 'UTC', 'YYYY-MM-DD'),
#          string_agg(coalesce(c.n, 0)::text, ' ' order by g.hour)
#     from g left join c using (source, hour) group by 1, 2 order by 1, 2;
#
# tests/fixtures/health_coverage_28d.csv (source,unit,at,declared,held,in_dwell): at each UTC
# midnight 09-21..10-18, per unit declaring 500 or more, the declared total of its newest walk that
# reached the end and the active ads that same walk counted (by_category's active_db, summed over
# the unit's entries, or their largest when the entries repeat one count of the whole unit, the
# basis chosen per unit against the live count at export, 05:40Z); in_dwell is 0 before the dwell
# shipped (2026-10-09 22:44Z: until then a gone verdict flipped at once) and the unit's live
# in-dwell count after it.
#   with t as (select k, '2026-09-21'::timestamptz + make_interval(days => k) at
#                from generate_series(0, 27) k),
#        w as (select source, started_at, ended_at, by_category from scrape_runs
#               where run_type = 'index' and started_at >= '2026-08-20' and ended_at is not null
#                 and jsonb_typeof(by_category) = 'array' and jsonb_array_length(by_category) > 0),
#        wu as (select w.source, w.started_at, w.ended_at, coalesce(x->>'category_main', 'none')
#                      || '-' || coalesce(x->>'category_type', 'none') unit,
#                      bool_and(coalesce((x->>'walk_reached_end')::boolean, false)) reached,
#                      sum((x->>'sreality_result_size')::bigint) declared,
#                      sum((x->>'active_db')::bigint) adb_sum,
#                      max((x->>'active_db')::bigint) adb_max,
#                      bool_and(x->>'active_db' is not null) adb_ok
#                 from w cross join lateral jsonb_array_elements(w.by_category) x
#                group by 1, 2, 3, 4),
#        live as (select source, coalesce(category_main, 'none') || '-'
#                        || coalesce(category_type, 'none') unit, count(*) held
#                   from listings where is_active and source = any(<portals>) group by 1, 2),
#        pend as (select l.source, coalesce(l.category_main, 'none') || '-'
#                        || coalesce(l.category_type, 'none') unit, count(distinct l.id) pending
#                   from detail_queue_completions c
#                   join listings l on l.source = c.source and l.source_id_native = c.native_id
#                  where c.outcome = 'gone' and l.is_active
#                    and c.completed_at > coalesce(l.last_seen_at, '-infinity'::timestamptz)
#                  group by 1, 2),
#        basis as (select distinct on (wu.source, wu.unit) wu.source, wu.unit,
#                         abs(wu.adb_sum - l.held) <= abs(wu.adb_max - l.held) by_sum
#                    from wu join live l on l.source = wu.source and l.unit = wu.unit
#                   where wu.reached and wu.adb_ok
#                   order by wu.source, wu.unit, wu.started_at desc),
#        d as (select t.k, t.at, wu.source, wu.unit,
#                     (array_agg(wu.declared order by wu.started_at desc))[1] declared,
#                     (array_agg(case when b.by_sum then wu.adb_sum else wu.adb_max end
#                                order by wu.started_at desc))[1] held
#                from t join wu on wu.reached and wu.adb_ok and wu.ended_at <= t.at
#                 and wu.started_at > t.at - interval '30 days'
#                join basis b on b.source = wu.source and b.unit = wu.unit
#               group by 1, 2, 3, 4)
#   select d.source, d.unit, to_char(d.at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
#          d.declared, d.held,
#          case when d.at >= '2026-10-09 22:44Z' then coalesce(p.pending, 0) else 0 end
#     from d left join pend p on p.source = d.source and p.unit = d.unit
#    where d.declared >= 500 order by 1, 2, 3;
#
# tests/fixtures/health_completions_7d.csv is the delisting replay's (its header holds the SQL):
# the fetch-errors arm reads its `ledger` rows, completions per hour, portal and priority, `rows`
# and `failed` (a completion that carries an attempt).
#
# Stated limits. No queue history exists, so inflow counts written ads only: a stopped drain reads
# as lost discovery here (the 09-28/29 stall, a day before the worker froze), where the live arm adds the NEW rows still
# queued. Fetch errors see 7 days, count completions rather than distinct ids, and name no looping
# id (the ledger keeps no id). The missing arm reads a unit that collected all it declared Not
# measured (its totals are not exported); the seam each portal now records (total_is_count,
# declared_is_upper_bound) is applied to every historical walk from the adapter's own class.
# Listings keep no as-of holdings, so the residual's held count is the walk's own active_db and
# its in-dwell count today's; the unnominated arm keeps no history at all and reads 0 here.

from __future__ import annotations

import bisect
import datetime as _dt
import functools
import importlib
import statistics
from typing import Any, NamedTuple

from location_data.claims_common import SOURCES
from scraper.portal_factory import PORTAL_CLASSES
from scripts.checks import common
from scripts.checks import scraping as sc
from scripts.verify_pipeline import THRESHOLDS
from tests.scripts.test_checks_platform import _Conn
from tests.scripts.test_checks_platform_replay import _fixture, _incidents, _ticks, _told, _utc
from tests.toolkit.test_system_alerts import _FakeConn as _Ladder

_UTC = _dt.timezone.utc
_HOUR, _DAY = _dt.timedelta(hours=1), _dt.timedelta(days=1)
_START, _END = (_dt.datetime(2026, 9, 21, tzinfo=_UTC), _dt.datetime(2026, 10, 19, tzinfo=_UTC))


@functools.cache
def _seams() -> dict[str, tuple[bool, bool]]:
    """Each portal's walk seams, read off its adapter class: (total_is_count, upper bound)."""
    seams = {}
    for source, (module, name) in PORTAL_CLASSES.items():
        portal = getattr(importlib.import_module(module), name)
        seams[source] = (bool(getattr(portal, "total_is_count", False)),
                         bool(getattr(portal, "coverage_denominator_is_upper_bound", False)))
    return seams


# --- the walk ledger at a tick ----------------------------------------------------------------


class _Walk(NamedTuple):
    start: _dt.datetime
    end: _dt.datetime
    walked: frozenset[str]
    unreached: frozenset[str]
    short: dict[str, tuple[int, int]]


@functools.cache
def _walks() -> dict[str, list[_Walk]]:
    """Each portal's finished walks in end order (a portal walks one at a time)."""
    walks: dict[str, list[_Walk]] = {}
    for row in _fixture("health_walks_28d.csv"):
        mine = walks.setdefault(row["source"], [])
        walked = mine[-1].walked if row["walked"] == "*" else frozenset(row["walked"].split())
        short = {unit: (int(d), int(c)) for unit, d, c in
                 (entry.replace("=", "/").split("/") for entry in row["short"].split())}
        start = _utc(row["started_at"])
        mine.append(_Walk(start, start + _dt.timedelta(seconds=int(row["duration_s"])), walked,
                          frozenset(row["unreached"].split()), short))
    for mine in walks.values():
        mine.sort(key=lambda w: w.end)
    return walks


def _split(unit: str) -> tuple[str | None, str | None]:
    main, kind = unit.split("-")
    return (None if main == "none" else main, None if kind == "none" else kind)


def _walk_rows(at: _dt.datetime, declared: dict[tuple[str, str], int] | None = None
               ) -> list[tuple[Any, ...]]:
    """_WALKS_SQL's rows at `at`, as the check's own SQL would aggregate the fixture's walks."""
    recent, stale = at - 30 * _DAY, at - 14 * _DAY
    rows = []
    for source, walks in sorted(_walks().items()):
        started = [w.start for w in walks if recent < w.start <= at]
        done = [w for w in walks[:bisect.bisect_right([w.end for w in walks], at)]
                if w.start > recent]
        if not started:
            continue
        starts = sorted(w.start for w in done if w.start > stale)
        gaps = [(b - a).total_seconds() for a, b in zip(starts, starts[1:])]
        portal = (source, max(started), max((w.start for w in done), default=None),
                  statistics.median(gaps) if gaps else None, len(gaps),
                  max((w.start for w in done if not w.unreached), default=None))
        counted, upper = _seams().get(source, (False, False))
        units = sorted({u for w in done for u in w.walked})
        if not units:
            rows.append((*portal, None, None, None, None, None, 0, None, 0, 0, False, False, 0))
        for unit in units:
            mine = [w for w in done if unit in w.walked]
            ends = [w for w in mine if unit not in w.unreached]
            end = max(ends, key=lambda w: w.start) if ends else None
            d, c = end.short.get(unit, (None, None)) if end else (None, None)
            if d is None and declared is not None and end:
                d = c = declared.get((source, unit))
            rows.append((*portal, *_split(unit), max(w.start for w in mine),
                         end.start if end else None, d, 0, c, 1, 1, counted, upper, 0))
    return rows


# --- scraping: walks, inflow, fetch errors and missing, hourly over 28 days ---------------


@functools.cache
def _written() -> dict[str, dict[_dt.datetime, int]]:
    hours: dict[str, dict[_dt.datetime, int]] = {}
    for row in _fixture("health_inflow_28d.csv"):
        day = _utc(row["day"] + "T00:00:00Z")
        hours.setdefault(row["source"], {}).update(
            (day + _HOUR * h, int(n)) for h, n in enumerate(row["hours"].split()))
    return hours


def _inflow_rows(at: _dt.datetime, sources: list[str]) -> list[tuple[Any, ...]]:
    """_INFLOW_SQL's rows at an hour boundary: each hour's ads in whole days back."""
    days: dict[tuple[str, int], int] = {}
    for source in sources:
        for hour, n in _written().get(source, {}).items():
            back = (at - hour) / _HOUR  # the hour's ads are (back - 1, back] hours old
            if n and 1 <= back <= 35 * 24:
                key = (source, int((back - 1) // 24))
                days[key] = days.get(key, 0) + n
    return [("written", source, d, n) for (source, d), n in sorted(days.items())]


@functools.cache
def _ledger() -> list[tuple[_dt.datetime, str, int, int]]:
    return [(_utc(r["hour"]), r["source"], int(r["rows"]), int(r["failed"]))
            for r in _fixture("health_completions_7d.csv") if r["arm"] == "ledger"]


def _fetch_rows(at: _dt.datetime) -> list[tuple[Any, ...]]:
    """_FETCH_ERRORS_SQL's rows at an hour boundary, from the completions ledger's hours."""
    sums: dict[str, list[int]] = {}
    for hour, source, rows, failed in _ledger():
        if at - 7 * _DAY <= hour < at:
            day = hour >= at - _DAY
            acc = sums.setdefault(source, [0, 0, 0, 0])
            acc[0] += rows if day else 0
            acc[1] += failed if day else 0
            acc[2] += rows
            acc[3] += failed
    return [(source, *acc, None) for source, acc in sorted(sums.items())]


@functools.cache
def _scraping_replay() -> tuple[_Ladder, dict[_dt.datetime, dict[str, dict[str, Any]]]]:
    first_ledger = min(hour for hour, *_ in _ledger())
    results, cells = [], {}
    for at in _ticks(_START, _END, _HOUR):
        conn = _Conn({sc._WALKS_SQL: _walk_rows(at),
                      sc._INFLOW_SQL: _inflow_rows(at, sorted(SOURCES)),
                      sc._FETCH_ERRORS_SQL: _fetch_rows(at) if at > first_ledger else []},
                     now=at)
        result = sc.check_scraping(conn, THRESHOLDS)
        results.append((at, result))
        cells[at] = {c["subject"]: c for c in result["details"]["cells"]}
    return _told("scraping", 600, results), cells


def test_the_walks_arm_rings_only_the_two_real_walk_outages() -> None:
    """ceskereality byt/prodej's 27-day hole (no end-reached walk 09-16..10-13) and the portal's
    403 wall (no walk reached the end 09-29 05:55..10-02 10:30); every other gap stayed Watch."""
    ladder, cells = _scraping_replay()
    opened = _incidents(ladder, "scraping")
    assert not [i for i in opened if i["subject"].endswith("/walks")], "every portal kept walking"
    assert all(c["status"] == "ok" for read in cells.values() for s, c in read.items()
               if s.endswith("/walks")), "the portal-level walks cell reads OK on every tick"
    walks = [i for i in opened if "/walks/" in i["subject"]]
    assert {i["subject"].split("/")[0] for i in walks} == {"ceskereality"}
    (hole,) = [i for i in walks if i["subject"] == "ceskereality/walks/byt-prodej"]
    assert hole["first_seen_at"] == _START and hole["resolve_reason"] == "measured ok"
    assert _utc("2026-10-13T12:33:00Z") < hole["resolved_at"] < _utc("2026-10-13T18:00:00Z")
    wall = [i for i in walks if i["subject"] != "ceskereality/walks/byt-prodej"]
    assert len(wall) == 9 and len({i["first_seen_at"] for i in wall}) == 1
    assert _utc("2026-09-30T18:00:00Z") < wall[0]["first_seen_at"] < _utc("2026-10-01T03:00:00Z")
    assert all(_utc("2026-10-02T10:30:00Z") < i["resolved_at"] < _utc("2026-10-02T16:00:00Z")
               for i in wall)


def test_the_403_wall_is_told_in_one_grouped_notice() -> None:
    ladder, _ = _scraping_replay()
    wall = min(i["first_seen_at"] for i in _incidents(ladder, "scraping", "ceskereality/walks/")
               if i["subject"] != "ceskereality/walks/byt-prodej")
    onsets = [t for t in ladder.told if (t.kind, t.at) == ("onset", wall)]
    assert len(onsets) == 1
    assert sum(s.startswith("ceskereality/walks/") for s in onsets[0].subjects) == 9


def test_every_walk_onset_stands_on_six_walk_gaps_or_two_weeks() -> None:
    ladder, cells = _scraping_replay()
    for inc in _incidents(ladder, "scraping"):
        if "/walks/" not in inc["subject"]:
            continue
        cell = cells[inc["first_seen_at"]][inc["subject"]]
        age, gap = cell["value"], cell["facts"]["gap_h"]  # rounded to 0.1 h and 0.01 h
        assert age is None or age >= 6 * gap - 0.1 or age > 14 * 24, (inc, cell)


def test_transient_walk_gaps_watch_but_never_open() -> None:
    """bazos byt/pronajem's 32 h gap on 10-04/05 (the design's worst transient) is Watch only."""
    ladder, cells = _scraping_replay()
    watched = {at for at, read in cells.items()
               if read.get("bazos/walks/byt-pronajem", {}).get("status") == "warn"}
    assert any(_utc("2026-10-04T17:00:00Z") < at < _utc("2026-10-05T07:00:00Z") for at in watched)
    assert not _incidents(ladder, "scraping", "bazos/walks/")


def test_no_end_reached_walk_missed_two_percent_of_its_declared_ads() -> None:
    """Latest walks collected 99.96-100 % of declared; the worst shortfall replayed is 1.3 %."""
    ladder, cells = _scraping_replay()
    assert not [i for i in _incidents(ladder, "scraping") if "/missing/" in i["subject"]]
    shares = [c["value"] for read in cells.values() for s, c in read.items()
              if "/missing/" in s and c["status"] != "unknown"]
    assert shares and max(shares) < 2.0


def test_the_inflow_arm_rings_only_inside_the_late_september_outages() -> None:
    """Written ads fell under a fifth of their baseline only with the 403 wall (09-27..10-02), the
    one inflow incident live, and with the 09-28/29 stall (idnes, mmreality; a day before the 09-29/30 worker freeze, cause
    not established), an artefact
    of this replay: it has no queue history, while the live arm adds the NEW rows still queued,
    so a stopped drain is not lost discovery (live, a stopped drain is publishing's ingest past 6 h).
    The quiet 16 days after 10-03 open nothing."""
    ladder, cells = _scraping_replay()
    inflow = [i for i in _incidents(ladder, "scraping") if i["subject"].endswith("/inflow")]
    assert inflow
    for inc in inflow:
        assert _utc("2026-09-26T00:00:00Z") < inc["first_seen_at"] < _utc("2026-10-03T00:00:00Z")
        assert cells[inc["first_seen_at"]][inc["subject"]]["value"] <= 20, inc
    assert {i["subject"] for i in inflow} <= {"ceskereality/inflow", "idnes/inflow",
                                              "mmreality/inflow"}
    burst = [read["bazos/inflow"]["status"] for at, read in cells.items()
             if _utc("2026-10-13T00:00:00Z") <= at < _utc("2026-10-14T00:00:00Z")]
    assert burst and "fail" not in burst, "the 10-13 bazos burst is ingest's, never lost inflow"


def test_a_quiet_week_of_fetch_errors_opens_nothing() -> None:
    """The ledger's 7 days: the worst portal-day failed 0.85 % of its ids (sreality, 10-18; lower
    bound 0.60 %), far under the 2 % Watch line."""
    ladder, cells = _scraping_replay()
    assert not [i for i in _incidents(ladder, "scraping") if i["subject"].endswith("/fetch_errors")]
    judged = [c for read in cells.values() for s, c in read.items()
              if s.endswith("/fetch_errors") and c["status"] != "unknown"]
    assert judged and all(c["status"] == "ok" for c in judged)
    assert max(c["value"] for c in judged) < 1.0, "the value is the share, in percent"
    assert max(c["facts"]["lower_bound"] for c in judged) < 0.01


# --- coverage: the residual, daily over 28 days ---------------------------------------------


@functools.cache
def _held() -> dict[_dt.datetime, list[dict[str, str]]]:
    by_day: dict[_dt.datetime, list[dict[str, str]]] = {}
    for row in _fixture("health_coverage_28d.csv"):
        by_day.setdefault(_utc(row["at"]), []).append(row)
    return by_day


@functools.cache
def _coverage_replay() -> tuple[_Ladder, dict[_dt.datetime, dict[str, dict[str, Any]]]]:
    results, cells = [], {}
    for at, rows in sorted(_held().items()):
        declared = {(r["source"], r["unit"]): int(r["declared"]) for r in rows}
        held = [(r["source"], *_split(r["unit"]), int(r["held"]), int(r["in_dwell"]))
                for r in rows]
        conn = _Conn({sc._WALKS_SQL: _walk_rows(at, declared), sc._HELD_SQL: held,
                      sc._UNNOMINATED_SQL: [], common.TREND_SQL: []}, now=at)
        result = sc.check_coverage(conn, THRESHOLDS)
        results.append((at, result))
        cells[at] = {c["subject"]: c for c in result["details"]["cells"]}
    return _told("coverage", 3600, results, cls="gauge"), cells


def test_the_house_double_count_is_one_standing_incident() -> None:
    """ceskereality's two house scopes declare 13.7 k against 11.8 k held: Problem every day."""
    ladder, _ = _coverage_replay()
    (inc,) = _incidents(ladder, "coverage", "ceskereality/residual/dum-prodej")
    assert inc["first_seen_at"] == _START and inc["resolved_at"] is None


def test_the_hole_surfaces_when_its_first_end_reached_walk_lands_and_drains() -> None:
    """byt/prodej's first end-reached walk after its hole held ~40 % over declared (10-14); the
    walks arm owned the hole itself (its stale walk counted its own stale stock, +1.3 %)."""
    ladder, cells = _coverage_replay()
    (inc,) = _incidents(ladder, "coverage", "ceskereality/residual/byt-prodej")
    assert inc["first_seen_at"] == _utc("2026-10-14T00:00:00Z")
    assert cells[inc["first_seen_at"]]["ceskereality/residual/byt-prodej"]["value"] > 30
    drained = [read["ceskereality/residual/byt-prodej"]["status"] for at, read in cells.items()
               if at >= _utc("2026-10-16T00:00:00Z")]
    assert drained and set(drained) == {"warn"}, "Watch holds it open while the stock drains"


def test_quiet_weeks_of_holdings_open_nothing_new() -> None:
    ladder, _ = _coverage_replay()
    for start, end in (("2026-09-22", "2026-09-28"), ("2026-10-16", "2026-10-19")):
        assert [i for i in _incidents(ladder, "coverage")
                if _utc(f"{start}T00:00:00Z") <= i["first_seen_at"] < _utc(f"{end}T00:00:00Z")
                ] == []


def test_every_residual_onset_stands_on_a_twentieth_of_the_declared_total() -> None:
    ladder, cells = _coverage_replay()
    opened = _incidents(ladder, "coverage", "")
    assert opened
    for inc in opened:
        assert abs(cells[inc["first_seen_at"]][inc["subject"]]["value"]) > 5, inc
