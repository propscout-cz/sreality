"""Portal checks on the walk ledger: scraping (an alarm) and coverage (a gauge), on the worker.

`check_scraping` asks whether every portal still finds and fetches its ads, in four arms:
walks (per portal, its newest finished walk; per unit, the hours since the start of the newest
walk that reached the portal's end, against the portal's own walk-start gap G), inflow (ads
discovered in 24 h against the same window one to four weeks back), fetch errors (the attempted
detail ids that failed, judged on their Wilson lower bound) and missing (the share of a unit's
declared total its newest end-reached walk did not collect). `check_coverage` asks whether we
hold what each portal shows: per unit the residual of active ads, in-dwell ads set aside,
against the declared total, and per portal the active ads no walk can nominate, each with its
rolling 30-day trend (common.trend). Only GitHub walks write scrape_runs, so the arms that must
see the worker's work read the queue, the completions ledger and listings instead. Every portal
in SOURCES gets its cells whether or not it walked. No portal is named here (rule 21): what
differs per portal is a seam its walks record in every by_category entry.
"""

from __future__ import annotations

import datetime as _dt
import statistics
from typing import Any, NamedTuple

from location_data.claims_common import SOURCES
from scraper.db import QUEUE_PRIORITY_NEW, nominable_predicate
from scraper.portal_runner import DECLARED_TOTAL_KEY
from scripts.checks.clock import fetchall
from scripts.checks.common import (
    ago,
    as_utc,
    cell,
    graded,
    iso,
    judged,
    now,
    num,
    result,
    wilson_lower,
    with_trends,
)

# The four baselines: the same window 7, 14, 21 and 28 days earlier (the weekday cycle).
_BASELINE_WEEKS = 4
# An id given up this often in 7 d is re-armed and failing on a loop: named, never excluded.
_POISON_GIVE_UPS = 3

# Index walks of the recent window, one row per (portal, unit) a finished walk recorded, each
# carrying its portal's facts (a portal whose runs never finished keeps one row with no unit; one
# with no run in the window is added from SOURCES, so none drops out). A unit reached the end in
# a walk when every entry of its label did; G is the median start gap of the portal's finished
# walks in the stale window (the open gap never enters it). Entries carry no scope key and labels
# repeat, so a unit's totals sum its entries.
_WALKS_SQL = f"""
with runs as (
    select source, started_at, by_category,
           case when ended_at is not null and jsonb_typeof(by_category) = 'array'
                then jsonb_array_length(by_category) > 0 else false end as finished
      from scrape_runs
     where run_type = 'index'
       and started_at > now() - make_interval(days => %(recent_days)s::int)),
units as (
    select r.source, r.started_at,
           e ->> 'category_main' as category_main, e ->> 'category_type' as category_type,
           bool_and(coalesce((e ->> 'walk_reached_end')::boolean, false)) as reached,
           sum((e ->> '{DECLARED_TOTAL_KEY}')::numeric) as declared,
           count(*) filter (where e ->> '{DECLARED_TOTAL_KEY}' is null
                              or (e ->> '{DECLARED_TOTAL_KEY}')::numeric < 0) as undeclared,
           sum((e ->> 'collected')::numeric) as collected,
           count(*) as entries,
           count(*) filter (where e -> 'total_is_count' is not null) as recorded,
           bool_or(coalesce((e ->> 'total_is_count')::boolean, false)) as counted,
           bool_or(coalesce((e ->> 'declared_is_upper_bound')::boolean, false)) as upper_bound,
           sum(coalesce((e ->> 'verify_deferred')::numeric, 0)) as deferred
      from runs r
      cross join lateral jsonb_array_elements(
            case when r.finished then r.by_category else '[]'::jsonb end) as e
     group by 1, 2, 3, 4),
portals as (
    select source, max(started_at) as started_at,
           max(started_at) filter (where finished) as finished_at
      from runs
     group by source),
spacing as (
    select source, percentile_cont(0.5) within group (order by gap_s) as gap_s,
           count(gap_s) as gaps
      from (select source,
                   extract(epoch from started_at - lag(started_at)
                           over (partition by source order by started_at)) as gap_s
              from runs
             where finished
               and started_at > now() - make_interval(days => %(stale_days)s::int)) g
     group by source),
ends as (
    select source, max(started_at) as all_reached_at
      from (select source, started_at, bool_and(reached) as all_reached
              from units
             group by source, started_at) w
     where all_reached
     group by source),
walked as (
    select source, category_main, category_type, max(started_at) as walked_at
      from units
     group by 1, 2, 3),
newest as (
    select distinct on (source, category_main, category_type)
           source, category_main, category_type, started_at, declared, undeclared, collected,
           entries, recorded, counted, upper_bound, deferred
      from units
     where reached
     order by source, category_main, category_type, started_at desc)
select p.source, p.started_at, p.finished_at, s.gap_s, coalesce(s.gaps, 0), e.all_reached_at,
       w.category_main, w.category_type, w.walked_at, n.started_at, n.declared,
       coalesce(n.undeclared, 0), n.collected, coalesce(n.entries, 0), coalesce(n.recorded, 0),
       coalesce(n.counted, false), coalesce(n.upper_bound, false), coalesce(n.deferred, 0)
  from portals p
  left join spacing s on s.source = p.source
  left join ends e on e.source = p.source
  left join walked w on w.source = p.source
  left join newest n on n.source = w.source
        and n.category_main is not distinct from w.category_main
        and n.category_type is not distinct from w.category_type
 order by p.source, w.category_main, w.category_type
"""

# Discovery per portal and whole day back: written ads by first_seen_at (the write time), and
# NEW-priority queue rows not yet written by enqueued_at, so a stopped drain is not read as lost
# discovery. Queue rows leave on their write, so only listings reach the baselines. The bucket is
# the age of the DISCOVERY (a timestamp subtraction carries whole days), not time on market.
_INFLOW_SQL = """
select 'written', source,
       extract(day from now() - first_seen_at)::int as days_ago, count(*)
  from listings
 where source = any(%(sources)s)
   and first_seen_at > now() - make_interval(days => %(days)s::int)
 group by 1, 2, 3
union all
select 'queued', q.source,
       extract(day from now() - q.enqueued_at)::int as days_ago, count(*)
  from listing_detail_queue q
 where q.source = any(%(sources)s) and q.priority = %(new)s
   and q.enqueued_at > now() - interval '7 days'
   and not exists (select 1 from listings l
                    where l.source = q.source and l.source_id_native = q.native_id)
 group by 1, 2, 3
"""

# The detail ids each portal attempted, on both runtimes: every terminal completion, and every
# live queue row that has failed (it carries no per-attempt stamp, so a row counts in a window
# when it was claimed or enqueued inside it). An id failed when any of those carries attempts.
_FETCH_ERRORS_SQL = """
with attempted as (
    select c.source, c.native_id, c.completed_at as at, c.attempts > 0 as failed
      from detail_queue_completions c
     where c.source = any(%(sources)s) and c.completed_at > now() - interval '7 days'
    union all
    select q.source, q.native_id, greatest(q.claimed_at, q.enqueued_at), true
      from listing_detail_queue q
     where q.source = any(%(sources)s) and q.attempts > 0
       and greatest(q.claimed_at, q.enqueued_at) > now() - interval '7 days'),
ids as (
    select source, native_id,
           bool_or(at > now() - interval '24 hours') as recent,
           bool_or(failed and at > now() - interval '24 hours') as recent_failed,
           bool_or(failed) as failed
      from attempted
     group by source, native_id),
poison as (
    select source, native_id, count(*) as times
      from detail_queue_completions
     where source = any(%(sources)s) and outcome = 'given_up'
       and completed_at > now() - interval '7 days'
     group by source, native_id
    having count(*) >= %(poison)s)
select i.source, count(*) filter (where i.recent), count(*) filter (where i.recent_failed),
       count(*), count(*) filter (where i.failed),
       (select jsonb_agg(jsonb_build_array(p.native_id, p.times)
                         order by p.times desc, p.native_id)
          from poison p
         where p.source = i.source)
  from ids i
 group by i.source
"""

# Active ads per portal and category, and those with a gone verdict newer than their last
# sighting (in dwell: the page read gone once and the flip waits out the dwell).
_HELD_SQL = """
with held as (
    select source, category_main, category_type, count(*) as held
      from listings
     where is_active and source = any(%(sources)s)
     group by 1, 2, 3),
pending as (
    select l.source, l.category_main, l.category_type, count(distinct l.id) as pending
      from detail_queue_completions c
      join listings l on l.source = c.source and l.source_id_native = c.native_id
     where c.outcome = 'gone' and c.source = any(%(sources)s) and l.is_active
       and c.completed_at > coalesce(l.last_seen_at, '-infinity'::timestamptz)
     group by 1, 2, 3)
select h.source, h.category_main, h.category_type, h.held, coalesce(p.pending, 0)
  from held h
  left join pending p on p.source = h.source
        and p.category_main is not distinct from h.category_main
        and p.category_type is not distinct from h.category_type
"""

# Active ads unseen since before the start of their category's newest end-reached walk (with no
# such walk, their portal's newest walk in which every entry reached the end) that the walks' own
# predicate, db.nominable_predicate judged at that start, still calls nominable: no walk is
# nominating them (an ad the drain finished inside the recheck throttle before it was skipped).
_UNNOMINATED_SQL = f"""
with units as (
    select * from unnest(%(sources)s::text[], %(mains)s::text[], %(types)s::text[],
                         %(starts)s::timestamptz[])
           as u(source, category_main, category_type, start_at)),
portals as (
    select * from unnest(%(portal_sources)s::text[], %(portal_starts)s::timestamptz[])
           as p(source, start_at)),
unseen as (
    select l.source, l.source_id_native, l.last_seen_at, u.start_at is null as unwalked,
           coalesce(u.start_at, p.start_at) as walk_start
      from listings l
      join portals p on p.source = l.source
      left join units u on u.source = l.source
            and u.category_main is not distinct from l.category_main
            and u.category_type is not distinct from l.category_type
     where l.is_active
       and coalesce(l.last_seen_at, '-infinity'::timestamptz) < coalesce(u.start_at, p.start_at))
select l.source, count(*), count(*) filter (where l.unwalked), min(l.last_seen_at)
  from unseen l
 where {nominable_predicate("l.walk_start")}
 group by l.source
"""


class _Portal(NamedTuple):
    source: str
    started_at: _dt.datetime | None
    finished_at: _dt.datetime | None
    gap_s: float | None
    gaps: int
    all_reached_at: _dt.datetime | None


class _Unit(NamedTuple):
    source: str
    category_main: str | None
    category_type: str | None
    walked_at: _dt.datetime
    end_at: _dt.datetime | None
    declared: int | None
    undeclared: int
    collected: int | None
    entries: int
    recorded: int
    counted: bool
    upper_bound: bool
    deferred: int

    @property
    def labelled(self) -> bool:
        return self.category_main is not None or self.category_type is not None

    @property
    def label(self) -> str:
        """An entry with no labels is the orphan scope's (rule 3; web's whole agenda)."""
        if not self.labelled:
            return "orphans"
        return f"{self.category_main or 'none'}-{self.category_type or 'none'}"


def _ledger(conn: Any, thresholds: dict[str, Any]) -> tuple[dict[str, _Portal], list[_Unit]]:
    """Every portal with its walks of the recent window, and every unit those walks recorded."""
    rows = fetchall(conn, _WALKS_SQL, {
        "recent_days": int(thresholds["scraping_walk_recent_days"]),
        "stale_days": int(thresholds["scraping_walk_stale_days"])})
    portals: dict[str, _Portal] = {}
    units: list[_Unit] = []
    for row in rows:
        source = row[0]
        portals.setdefault(source, _Portal(source, as_utc(row[1]), as_utc(row[2]), num(row[3]),
                                           int(row[4] or 0), as_utc(row[5])))
        if row[8] is None:
            continue
        units.append(_Unit(source, row[6], row[7], as_utc(row[8]), as_utc(row[9]),
                           None if row[10] is None else int(row[10]), int(row[11] or 0),
                           None if row[12] is None else int(row[12]), int(row[13] or 0),
                           int(row[14] or 0), bool(row[15]), bool(row[16]), int(row[17] or 0)))
    for source in SOURCES:
        portals.setdefault(source, _Portal(source, None, None, None, 0, None))
    return portals, units


def _undeclared(unit: _Unit, floor: int, recent_days: int) -> str | None:
    """Why the unit's declared total cannot be judged, or None when it can."""
    if unit.end_at is None:
        return f"no walk in {recent_days} d reached the portal's end"
    if unit.undeclared or unit.declared is None:
        return "an entry of its newest end-reached walk declared no total"
    if unit.counted:
        return "declared is a count of what was seen"
    if unit.recorded < unit.entries:
        return "not yet recorded by this walk's adapter"
    if unit.declared < floor:
        return f"its declared total {unit.declared:,} is under the {floor:,} floor"
    return None


# --- scraping: walks ----------------------------------------------------------------------


def _walk_cells(portals: dict[str, _Portal], units: list[_Unit], thresholds: dict[str, Any],
                at: _dt.datetime) -> list[dict[str, Any]]:
    """Per portal its newest finished walk; per unit its newest end-reached walk against G."""
    min_gaps = int(thresholds["scraping_walk_fallback_min_gaps"])
    own = {p.source: p.gap_s for p in portals.values() if p.gap_s and p.gaps >= min_gaps}
    slowest = max(own.values(), default=None)
    recent_days = int(thresholds["scraping_walk_recent_days"])
    cells = []
    for source, portal in sorted(portals.items()):
        walked = [u for u in units if u.source == source]
        cells.append(_portal_walks_cell(portal, len(walked), recent_days, at))
        gap = own.get(source, slowest)
        basis = "its own" if source in own else "the slowest portal's"
        cells += [_walk_cell(u, portal, gap, basis, thresholds, at) for u in walked]
    return cells


def _portal_walks_cell(portal: _Portal, walked: int, recent_days: int,
                       at: _dt.datetime) -> dict[str, Any]:
    """Problem with no finished walk in the window, else OK: its incident closes on that OK."""
    subject = f"{portal.source}/walks"
    if walked:
        age = (at - portal.finished_at).total_seconds()
        return cell(subject, "ok", f"{walked:,} {'unit' if walked == 1 else 'units'} walked in "
                    f"{recent_days} d; the newest finished walk started {ago(age)} ago",
                    value=round(age / 3600, 1), unit="h", portal=portal.source,
                    facts={"finished_at": iso(portal.finished_at), "units": walked})
    if portal.started_at is None:
        why = f"no walk started in {recent_days} d"
    else:
        why = (f"no finished walk in {recent_days} d: the newest walk started "
               f"{ago((at - portal.started_at).total_seconds())} ago and recorded no unit")
    return cell(subject, "fail", why, unit="h", portal=portal.source,
                facts={"started_at": iso(portal.started_at)})


def _walk_cell(unit: _Unit, portal: _Portal, gap: float | None, basis: str,
               thresholds: dict[str, Any], at: _dt.datetime) -> dict[str, Any]:
    """Hours since the start of the unit's newest end-reached walk, in walk gaps and days."""
    subject = f"{unit.source}/walks/{unit.label}"
    stale_days = int(thresholds["scraping_walk_stale_days"])
    watch = float(thresholds["scraping_walk_watch_gaps"])
    fail = float(thresholds["scraping_walk_fail_gaps"])
    facts = {"walked_at": iso(unit.walked_at), "end_reached_at": iso(unit.end_at),
             "gap_h": None if gap is None else round(gap / 3600, 2), "gap_basis": basis,
             "finished_gaps_14d": portal.gaps}
    fields: dict[str, Any] = {"unit": "h", "portal": unit.source, "facts": facts}
    if unit.end_at is None:
        return cell(subject, "fail", f"no walk in {thresholds['scraping_walk_recent_days']} d "
                    f"reached the portal's end (last walked "
                    f"{ago((at - unit.walked_at).total_seconds())} ago)", **fields)
    age = (at - unit.end_at).total_seconds()
    newest = f"its newest end-reached walk started {ago(age)} ago"
    fields["value"] = round(age / 3600, 1)
    if age > stale_days * 86400:
        return judged(subject, [("fail", f"no end-reached walk in {stale_days} d: {newest}")],
                      newest, **fields)
    if gap is None:
        return cell(subject, "unknown", f"Not measured: no portal has "
                    f"{thresholds['scraping_walk_fallback_min_gaps']} finished walk gaps in "
                    f"{stale_days} d to set a walk gap; {newest}", **fields)
    versus = f"{basis} walk gap of {ago(gap)}"
    conditions: list[tuple[str, str]] = []
    if age > fail * gap:
        conditions.append(("fail", f"{newest}, past {fail:g}× {versus}"))
    elif age > watch * gap:
        conditions.append(("warn", f"{newest}, past {watch:g}× {versus}"))
    return judged(subject, conditions, f"{newest} (Watch past {watch:g}× {versus})", **fields)


# --- scraping: inflow ---------------------------------------------------------------------


def _inflow_cells(conn: Any, sources: list[str],
                  thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    """Per portal, ads discovered in 24 h against the median of the same window 1-4 weeks back."""
    days = 7 * (_BASELINE_WEEKS + 1)
    counts: dict[tuple[str, str], dict[int, int]] = {}
    for kind, source, days_ago, n in fetchall(conn, _INFLOW_SQL, {
            "sources": sources, "days": days, "new": QUEUE_PRIORITY_NEW}):
        counts.setdefault((kind, source), {})[int(days_ago)] = int(n)
    weeks = range(1, _BASELINE_WEEKS + 1)
    cells = []
    for source in sources:
        written, queued = counts.get(("written", source), {}), counts.get(("queued", source), {})
        day = (written.get(0, 0), queued.get(0, 0), [written.get(7 * k, 0) for k in weeks])
        week = (sum(written.get(d, 0) for d in range(7)), sum(queued.get(d, 0) for d in range(7)),
                [sum(written.get(7 * k + d, 0) for d in range(7)) for k in weeks])
        cells.append(_inflow_cell(source, day, week, thresholds))
    return cells


def _inflow_cell(source: str, day: tuple[int, int, list[int]],
                 week: tuple[int, int, list[int]], thresholds: dict[str, Any]) -> dict[str, Any]:
    """The 24 h window against its baseline, or the 7-day one when a day's baseline is thin."""
    floor = float(thresholds["scraping_inflow_min_baseline"])
    window, (wrote, waiting, baselines) = "24 h", day
    if statistics.median(baselines) < floor:
        window, (wrote, waiting, baselines) = "7 d", week
    baseline = statistics.median(baselines)
    facts = {"window": window, "written": wrote, "queued": waiting, "baselines": baselines}
    median = f"{baseline:,.1f}".removesuffix(".0")
    if baseline < floor:
        return cell(f"{source}/inflow", "unknown", f"Not measured: a median of {median} ads a "
                    f"week discovered 1-4 weeks back, under {floor:g}", unit="%", portal=source,
                    facts=facts)
    found = wrote + waiting
    share = found / baseline
    versus = (f"{found:,} ads discovered in {window} ({wrote:,} written, {waiting:,} queued), "
              f"{share:.0%} of the {median} median of the same {window} 1-4 weeks back")
    conditions: list[tuple[str, str]] = []
    if share < float(thresholds["scraping_inflow_fail_share"]):
        conditions.append(("fail", versus))
    elif share < float(thresholds["scraping_inflow_warn_share"]):
        conditions.append(("warn", versus))
    elif share > float(thresholds["scraping_inflow_spike_share"]):
        conditions.append(("warn", f"a spike: {versus}"))
    return judged(f"{source}/inflow", conditions, versus, value=round(share * 100, 1), unit="%",
                  portal=source, facts={**facts, "baseline": baseline})


# --- scraping: fetch errors ---------------------------------------------------------------


def _fetch_error_cells(conn: Any, sources: list[str],
                       thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    """Per portal, the share of attempted detail ids that failed, judged on its lower bound."""
    read = {row[0]: row[1:] for row in fetchall(conn, _FETCH_ERRORS_SQL, {
        "sources": sources, "poison": _POISON_GIVE_UPS})}
    min_ids = int(thresholds["scraping_fetch_err_min_ids"])
    warn = float(thresholds["scraping_fetch_err_warn"])
    fail = float(thresholds["scraping_fetch_err_fail"])
    cells = []
    for source in sources:
        ids_24h, failed_24h, ids_7d, failed_7d, poison = read.get(source, (0, 0, 0, 0, None))
        looped = [(str(native_id), int(times)) for native_id, times in poison or []]
        facts: dict[str, Any] = {"ids_24h": int(ids_24h), "failed_24h": int(failed_24h),
                                 "ids_7d": int(ids_7d), "failed_7d": int(failed_7d),
                                 "given_up_repeatedly": [n for n, _ in looped]}
        named = ""
        if looped:
            more = f" and {len(looped) - 5} more" if len(looped) > 5 else ""
            named = (f"; given up {_POISON_GIVE_UPS}+ times in 7 d: "
                     + ", ".join(f"{n} ({t}×)" for n, t in looped[:5]) + more)
        if int(ids_24h) >= min_ids:
            window, n, hits = "24 h", int(ids_24h), int(failed_24h)
        elif int(ids_7d) >= min_ids:
            window, n, hits = "7 d", int(ids_7d), int(failed_7d)
        else:
            cells.append(cell(f"{source}/fetch_errors", "unknown",
                              f"Not measured: {int(ids_7d):,} detail ids attempted in 7 d, under "
                              f"{min_ids:,}{named}", unit="%", portal=source, facts=facts))
            continue
        lower = wilson_lower(hits, n) or 0.0
        facts.update(window=window, lower_bound=round(lower, 5))
        cells.append(cell(f"{source}/fetch_errors", graded(lower, warn, fail),
                          f"{hits:,} of {n:,} detail ids attempted in {window} failed "
                          f"({hits / n:.2%}; lower bound {lower:.2%}){named}",
                          value=round(100 * hits / n, 2), unit="%", portal=source, facts=facts))
    return cells


# --- scraping: missing --------------------------------------------------------------------


def _missing_cell(unit: _Unit, thresholds: dict[str, Any]) -> dict[str, Any] | None:
    """The share of a unit's declared total its newest end-reached walk did not collect; no cell
    when that total cannot be judged, which the coverage residual says once (its question)."""
    if _undeclared(unit, int(thresholds["scraping_missing_min_declared"]),
                   int(thresholds["scraping_walk_recent_days"])):
        return None
    subject = f"{unit.source}/missing/{unit.label}"
    facts = {"walk_started_at": iso(unit.end_at), "declared": unit.declared,
             "collected": unit.collected, "entries": unit.entries,
             "declared_is_upper_bound": unit.upper_bound}
    declared, collected = int(unit.declared or 0), int(unit.collected or 0)
    share = (declared - collected) / declared
    return cell(subject, graded(share, float(thresholds["scraping_missing_warn"]),
                               float(thresholds["scraping_missing_fail"])),
                f"collected {collected:,} of {declared:,} declared at its newest end-reached walk "
                f"({share:.2%} missing)", value=round(share * 100, 2), unit="%",
                portal=unit.source, facts=facts)


def check_scraping(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Is every portal still finding and fetching its ads? Walks, inflow, fetch errors, missing."""
    at = now(conn)
    portals, units = _ledger(conn, thresholds)
    sources = sorted(SOURCES)
    cells = _walk_cells(portals, units, thresholds, at)
    cells += _inflow_cells(conn, sources, thresholds)
    cells += _fetch_error_cells(conn, sources, thresholds)
    cells += [c for c in (_missing_cell(u, thresholds) for u in units) if c]
    return result("scraping", cells, {})


# --- coverage ----------------------------------------------------------------------------


def _residual_cell(unit: _Unit, held: tuple[int, int],
                   thresholds: dict[str, Any]) -> dict[str, Any]:
    """(H − P − D) / D: active ads, in-dwell ads set aside, against the declared total."""
    subject = f"{unit.source}/residual/{unit.label}"
    active, pending = held
    facts = {"walk_started_at": iso(unit.end_at), "declared": unit.declared, "held": active,
             "in_dwell": pending, "declared_is_upper_bound": unit.upper_bound}
    why = _undeclared(unit, int(thresholds["coverage_residual_min_declared"]),
                      int(thresholds["scraping_walk_recent_days"]))
    if why:
        return cell(subject, "unknown", f"Not measured: {why}", unit="%", portal=unit.source,
                    facts=facts)
    declared = int(unit.declared or 0)
    residual = (active - pending - declared) / declared
    return cell(subject, graded(abs(residual), float(thresholds["coverage_residual_warn"]),
                               float(thresholds["coverage_residual_fail"])),
                f"{active:,} active ads, {pending:,} of them in dwell, against {declared:,} "
                f"declared at its newest end-reached walk ({residual:+.2%})",
                value=round(residual * 100, 2), unit="%", portal=unit.source, facts=facts)


def _unnominated_cells(conn: Any, portals: dict[str, _Portal], units: list[_Unit],
                       thresholds: dict[str, Any], at: _dt.datetime) -> list[dict[str, Any]]:
    """Per portal, the active ads no walk is nominating, less that walk's deferrals."""
    reached = [u for u in units if u.end_at is not None]
    rows = fetchall(conn, _UNNOMINATED_SQL, {
        "sources": [u.source for u in reached], "mains": [u.category_main for u in reached],
        "types": [u.category_type for u in reached], "starts": [u.end_at for u in reached],
        "portal_sources": list(portals), "portal_starts": [p.all_reached_at
                                                           for p in portals.values()]})
    read = {row[0]: row[1:] for row in rows}
    warn = int(thresholds["coverage_unnominated_warn"])
    fail = int(thresholds["coverage_unnominated_fail"])
    cells = []
    for source, portal in sorted(portals.items()):
        subject = f"{source}/unnominated"
        if portal.all_reached_at is None and not any(u.source == source for u in reached):
            cells.append(cell(subject, "unknown", f"Not measured: no walk in "
                              f"{thresholds['scraping_walk_recent_days']} d reached the portal's "
                              "end", unit="ads", portal=source))
            continue
        ads, unwalked, oldest = read.get(source, (0, 0, None))
        deferred = sum(u.deferred for u in reached if u.source == source)
        count = max(0, int(ads) - deferred)
        oldest = as_utc(oldest)
        facts = {"unseen": int(ads), "deferred": deferred, "no_walked_category": int(unwalked),
                 "oldest_seen_at": iso(oldest)}
        status = "fail" if count >= fail else "warn" if count >= warn else "ok"
        parts = [f"{count:,} active ads no walk is nominating"
                 + (f" ({deferred:,} deferred by the flip cap set aside)" if deferred else "")]
        if unwalked:
            parts.append(f"{int(unwalked):,} in a category no end-reached walk covers")
        if oldest:
            parts.append(f"the oldest last seen {ago((at - oldest).total_seconds())} ago")
        cells.append(cell(subject, status, "; ".join(parts), value=count, unit="ads",
                          portal=source, facts=facts))
    return cells


def check_coverage(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Do we hold what each portal shows? Residual per unit, unnominated ads per portal."""
    at = now(conn)
    portals, units = _ledger(conn, thresholds)
    held = {(s, m, t): (int(h), int(p)) for s, m, t, h, p in
            fetchall(conn, _HELD_SQL, {"sources": sorted(SOURCES)})}
    # A residual needs a declared total; an entry with no labels has none.
    cells = [_residual_cell(u, held.get((u.source, u.category_main, u.category_type), (0, 0)),
                            thresholds) for u in units if u.labelled]
    cells += _unnominated_cells(conn, portals, units, thresholds, at)
    with_trends(conn, "coverage", cells, at)
    return result("coverage", cells, {})
