"""Delisting: are ads that left their portal made inactive on time, at normal volume, and correctly?

`check_delisting` judges five arms per portal on the ledgers both runtimes write: the outflow (ads
made inactive in 24 h against the quietest and the busiest 24 h within a few hours of the same hour
one to four weeks earlier), the stuck confirmations (active ads whose first unrefuted gone verdict
has outlived the effective dwell and two of the portal's walk gaps), the verify backlog (the oldest
presence check waiting), the deferrals (walks in which a unit reached the end whose nominations the
flip cap held back) and the precision (the share of a 48-72 h old cohort of inactive ads that is
active again). Its limits come from delist_policy's constants and each portal's own walks; it names
no portal, takes the harness's `thresholds`, sends every statement through the per-check clock and
imports neither the harness nor the worker.
"""

from __future__ import annotations

import datetime as _dt
import statistics
from typing import Any

from scraper.db import COMPLETION_RETENTION_DAYS, QUEUE_PRIORITY_VERIFY
from location_data.claims_common import SOURCES
from scraper.delist_policy import GONE_DWELL, RECHECK_THROTTLE
from scripts.checks.clock import fetchall
from scripts.checks.common import ago, as_utc, cell, iso, judged, num, result, wilson_lower

# A second walk-path verdict confirms a removal GONE_DWELL after the first, but presence_candidates
# re-nominates no ad within RECHECK_THROTTLE of its last check, so the longer of the two binds.
_EFFECTIVE_DWELL_S = max(GONE_DWELL, RECHECK_THROTTLE).total_seconds()
# The same 24 h one to four weeks back: the same weekday and hour, so the weekend dip cancels out.
_OUTFLOW_LAGS_D = (7, 14, 21, 28)
# A cohort of fixed age: a blinking ad comes back within hours and the next walk sights it, so at
# 48-72 h its revival has happened; a mixed 7-day window kept counting flips too young to tell.
_COHORT_H = (48, 72)
# A portal with too few flips in the cohort is pooled back as far as the ledger keeps them.
_POOL_D = COMPLETION_RETENTION_DAYS
# The walk-start gap is read over two weeks of finished walks, as the scraping check's G is.
_WALK_DAYS = 14

# Each week back is read as its 24 h windows ending at every whole-hour offset within ±align_h of
# this hour (the current 24 h at offset 0 only): a walk run a few hours off its usual time moves its
# flip burst across a fixed window's edge, so a drop is judged on each week's quietest window and a
# spike on its busiest. One index range per lag, binned by the hour, is summed into the windows: a
# range per window (29 of them) made the planner seq-scan listings, 4.6 s against 0.12 s (10-19).
_OUTFLOW_SQL = """
with bins as (
    select l.source, w.lag_d,
           floor(extract(epoch from l.inactive_at - w.at) / 3600)::int as hour, count(*)::int as ads
      from (select lag_d, now() - make_interval(days => lag_d) as at
              from unnest(%(lags)s::int[]) as lag_d) as w
      join listings l on l.inactive_at >= w.at - make_interval(hours => 24 + %(align_h)s::int)
                     and l.inactive_at < w.at + make_interval(hours => %(align_h)s::int)
     group by 1, 2, 3)
select b.source, b.lag_d, o.off_h, sum(b.ads)::int
  from bins b
 cross join generate_series(-%(align_h)s::int, %(align_h)s::int) as o(off_h)
 where (b.lag_d > 0 or o.off_h = 0) and b.hour >= o.off_h - 24 and b.hour < o.off_h
 group by 1, 2, 3
"""

# One row per finished walk (a crashed walk keeps `[]` and yields no element): the start gaps bound
# the stuck limit; whether ANY entry reached the end, and the entries' deferrals, feed deferrals.
_WALKS_SQL = """
select r.source, r.started_at,
       bool_or(coalesce((c->>'walk_reached_end')::boolean, false)),
       coalesce(sum((c->>'verify_deferred')::int), 0)::int
  from scrape_runs r
 cross join lateral jsonb_array_elements(
           case when jsonb_typeof(r.by_category) = 'array' then r.by_category end) as c
 where r.run_type = 'index' and r.ended_at is not null
   and r.started_at > now() - make_interval(days => %(days)s)
 group by r.id, r.source, r.started_at
 order by r.source, r.started_at
"""

# Per ad first, as db.gone_evidence reads it: the earliest gone verdict newer than the ad's last
# sighting, so a refuted earlier verdict never hides a later one that still stands.
_STUCK_SQL = """
with pending as (
    select l.source, min(c.completed_at) as first_gone_at
      from detail_queue_completions c
      join listings l on l.source = c.source and l.source_id_native = c.native_id
     where c.outcome = 'gone' and l.is_active
       and c.completed_at > coalesce(l.last_seen_at, '-infinity'::timestamptz)
     group by l.source, l.id)
select p.source, count(*)::int,
       count(*) filter (where p.first_gone_at < now() - make_interval(
                            secs => coalesce(lim.limit_s, %(fallback_s)s::float8)))::int,
       extract(epoch from now() - min(p.first_gone_at))::float8
  from pending p
  left join unnest(%(sources)s::text[], %(limits_s)s::float8[]) as lim(source, limit_s)
    on lim.source = p.source
 group by p.source
"""

# Claimed or not: a dead drain's claim keeps aging until another drain reclaims it. The location
# refetch lane enqueues at the same priority, so its rows are counted here too.
_VERIFY_SQL = """
select source, count(*)::int, count(*) filter (where claimed_at is not null)::int,
       extract(epoch from now() - min(enqueued_at))::float8
  from listing_detail_queue
 where priority = %(priority)s and not given_up
 group by source
"""

# A plain range over the pruned 7-day ledger: the planner reads it whole (about 200k rows) more
# cheaply than per portal through (source, completed_at), which it prices about three times higher.
_PRECISION_SQL = """
select c.source,
       count(*) filter (where c.completed_at >= now() - make_interval(hours => %(to_h)s))::int,
       count(*) filter (where c.completed_at >= now() - make_interval(hours => %(to_h)s)
                          and l.is_active)::int,
       count(*)::int,
       count(*) filter (where l.is_active)::int
  from detail_queue_completions c
  left join listings l on l.source = c.source and l.source_id_native = c.native_id
 where c.outcome = 'flipped'
   and c.completed_at < now() - make_interval(hours => %(from_h)s)
   and c.completed_at >= now() - make_interval(days => %(pool_d)s)
 group by c.source
"""


def _p90(values: list[float]) -> float:
    """The 90th percentile, interpolated as Postgres' percentile_cont reads it."""
    ordered = sorted(values)
    pos = 0.9 * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _walks(conn: Any) -> dict[str, list[tuple[_dt.datetime, bool, int]]]:
    """Each portal's finished walks of the last two weeks: (start, reached the end, deferred)."""
    walks: dict[str, list[tuple[_dt.datetime, bool, int]]] = {}
    for source, started, reached, deferred in fetchall(conn, _WALKS_SQL, {"days": _WALK_DAYS}):
        walks.setdefault(source, []).append((as_utc(started), bool(reached), int(deferred or 0)))
    return {source: sorted(rows) for source, rows in sorted(walks.items())}


def _walk_gaps(walks: dict[str, list[tuple[_dt.datetime, bool, int]]]) -> dict[str, float]:
    """Each portal's p90 start-to-start gap between finished walks; the open gap never counts."""
    gaps: dict[str, float] = {}
    for source, rows in walks.items():
        starts = [start for start, _, _ in rows]
        between = [(b - a).total_seconds() for a, b in zip(starts, starts[1:])]
        if between:
            gaps[source] = _p90(between)
    return gaps


def _stuck_limit_s(gap_s: float, thresholds: dict[str, Any]) -> float:
    """The effective dwell + 2 x the p90 walk gap + the slack: a verdict older is stuck."""
    return _EFFECTIVE_DWELL_S + 2 * gap_s + float(thresholds["delisting_stuck_slack_h"]) * 3600


def _outflow_cell(portal: str, windows: dict[tuple[int, int], int], align_h: int,
                  thresholds: dict[str, Any]) -> dict[str, Any]:
    """Ads made inactive in 24 h against the windows around this hour one to four weeks back."""
    subject, current = f"{portal}/outflow", windows.get((0, 0), 0)
    weeks = [[windows.get((lag, off), 0) for off in range(-align_h, align_h + 1)]
             for lag in _OUTFLOW_LAGS_D]
    quiet = [min(sums) for sums in weeks]
    baseline, busy = statistics.median(quiet), statistics.median(max(sums) for sums in weeks)
    facts: dict[str, Any] = {"inactive_24h": current, "baseline": baseline, "baseline_busy": busy,
                             "align_h": align_h, "weeks_back": dict(zip(
                                 (f"{lag} d" for lag in _OUTFLOW_LAGS_D), quiet))}
    quietest = f"the quietest 24 h within ±{align_h} h of this hour, one to four weeks back"
    floor = float(thresholds["delisting_outflow_min_baseline"])
    if baseline < floor:
        return cell(subject, "unknown", f"Not measured: {quietest}, made a median of "
                    f"{baseline:,g} ads inactive, under the {floor:,g} this arm needs",
                    portal=portal, facts=facts)
    share = current / baseline
    facts["share"] = round(share, 3)
    fail = float(thresholds["delisting_outflow_fail_share"])
    warn = float(thresholds["delisting_outflow_warn_share"])
    spike = float(thresholds["delisting_outflow_spike_share"])
    spike_min = int(thresholds["delisting_outflow_spike_min"])
    said = (f"{current:,} ads made inactive in 24 h: {share:.0%} of the median {baseline:,g} "
            f"of {quietest}")
    conditions = []
    if share < fail:
        conditions.append(("fail", f"{said} (Problem under {fail:.0%})"))
    elif share < warn:
        conditions.append(("warn", f"{said} (Watch under {warn:.0%})"))
    elif current > spike * busy and current >= spike_min:
        conditions.append(("warn", f"{said}; {current / busy:.0%} of the median {busy:,g} of "
                                   f"the busiest such 24 h: a spike (Watch over {spike:.0%} and "
                                   f"{spike_min:,} ads)"))
    return judged(subject, conditions, said, value=current, unit="ads", portal=portal, facts=facts)


def _stuck_cell(portal: str, row: tuple[Any, ...] | None, gap_s: float | None,
                fallback_gap_s: float, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Active ads whose first unrefuted gone verdict outlived the dwell and two walk gaps."""
    subject = f"{portal}/stuck"
    pending, stuck, oldest_s = row if row is not None else (0, 0, None)
    pending, stuck, oldest_s = int(pending), int(stuck), num(oldest_s)
    gap = fallback_gap_s if gap_s is None else gap_s
    limit_s, slack_h = _stuck_limit_s(gap, thresholds), float(thresholds["delisting_stuck_slack_h"])
    facts = {"pending": pending, "stuck": stuck,
             "oldest_s": None if oldest_s is None else round(oldest_s), "limit_s": round(limit_s),
             "effective_dwell_s": round(_EFFECTIVE_DWELL_S), "walk_gap_p90_s": round(gap),
             "walk_gap_own": gap_s is not None}
    whose = ("its" if gap_s is not None else
             f"the slowest portal's (it has no two finished walks in {_WALK_DAYS} d)")
    waiting = "none waiting"
    if pending and oldest_s is not None:
        waiting = f"{pending:,} waiting, the oldest for {ago(oldest_s)}"
    limit = (f"limit: the {ago(_EFFECTIVE_DWELL_S)} effective dwell + 2 × {whose} p90 walk gap "
             f"of {ago(gap)} + {slack_h:g} h")
    said = (f"{stuck:,} active ads have waited past {ago(limit_s)} for their gone verdict to be "
            f"confirmed ({waiting}; {limit})")
    warn, fail = int(thresholds["delisting_stuck_warn"]), int(thresholds["delisting_stuck_fail"])
    conditions = []
    if stuck >= fail:
        conditions.append(("fail", f"{said} (Problem at {fail:,})"))
    elif stuck >= warn:
        conditions.append(("warn", f"{said} (Watch at {warn:,})"))
    ok = said if stuck else (f"no active ad has waited past {ago(limit_s)} for its gone verdict "
                             f"to be confirmed ({waiting}; {limit})")
    return judged(subject, conditions, ok, value=stuck, unit="ads", portal=portal, facts=facts)


def _verify_cell(portal: str, row: tuple[Any, ...] | None,
                 thresholds: dict[str, Any]) -> dict[str, Any]:
    """The age of the oldest presence check waiting, claimed or not."""
    subject = f"{portal}/verify_backlog"
    if row is None:
        return cell(subject, "ok", "no presence check is waiting", value=0, unit="min",
                    portal=portal, facts={"queued": 0, "claimed": 0})
    queued, claimed, oldest_s = int(row[0]), int(row[1]), float(row[2])
    said = (f"the oldest presence check has waited {ago(oldest_s)} ({queued:,} waiting, "
            f"{claimed:,} of them claimed)")
    warn_h = float(thresholds["delisting_verify_warn_h"])
    fail_h = float(thresholds["delisting_verify_fail_h"])
    conditions = []
    if oldest_s > fail_h * 3600:
        conditions.append(("fail", f"{said} (Problem past {fail_h:g} h)"))
    elif oldest_s > warn_h * 3600:
        conditions.append(("warn", f"{said} (Watch past {warn_h:g} h)"))
    return judged(subject, conditions, said, value=round(oldest_s / 60), unit="min", portal=portal,
                  facts={"queued": queued, "claimed": claimed, "oldest_s": round(oldest_s)})


def _deferrals_cell(portal: str, walks: list[tuple[_dt.datetime, bool, int]],
                    thresholds: dict[str, Any]) -> dict[str, Any]:
    """Walks in a row in which a unit reached the end, each deferring nominations; never Problem."""
    subject = f"{portal}/deferrals"
    # Any entry, where the coverage arm's portal-level walk needs every one: the runner nominates
    # per entry that reached the end, so only such an entry can defer, whatever its siblings did.
    ended = [(start, deferred) for start, reached, deferred in walks if reached]
    if not ended:
        return cell(subject, "unknown", f"Not measured: no unit reached the end in any walk of "
                    f"the last {_WALK_DAYS} d", portal=portal,
                    facts={"walks": len(walks), "walks_a_unit_reached_end": 0})
    streak = 0
    for _, deferred in reversed(ended):
        if not deferred:
            break
        streak += 1
    newest_at, newest = ended[-1]
    facts = {"streak": streak, "deferred_newest": newest, "newest_at": iso(newest_at),
             "deferred_in_window": sum(deferred for _, deferred in ended), "walks": len(walks),
             "walks_a_unit_reached_end": len(ended)}
    walks_in_row = int(thresholds["delisting_deferral_walks"])
    conditions = []
    if streak >= walks_in_row:
        conditions.append(("warn", f"the last {streak} walks in which a unit reached the end each "
                           f"had nominations deferred by the flip cap (newest: {newest:,}); they "
                           f"wait for the next walk, a throttle by design (Watch at {walks_in_row} "
                           "in a row)"))
    ok = (f"the newest walk in which a unit reached the end had {newest:,} nominations deferred "
          "by the flip cap to the next walk" if newest else
          "the newest walk in which a unit reached the end had no nomination deferred")
    return judged(subject, conditions, ok, value=streak, unit="walks", portal=portal, facts=facts)


def _precision_cell(portal: str, row: tuple[Any, ...] | None,
                    thresholds: dict[str, Any]) -> dict[str, Any]:
    """Of the ads made inactive 48-72 h ago, the share active again, judged on its Wilson bound."""
    subject = f"{portal}/precision"
    cohort, cohort_active, pooled, pooled_active = (int(x) for x in (row or (0, 0, 0, 0)))
    min_n = int(thresholds["delisting_precision_min_n"])
    young, old = _COHORT_H
    pool = f"{young // 24}-{_POOL_D} d"
    facts: dict[str, Any] = {"cohort": cohort, "cohort_active": cohort_active,
                             "pooled": pooled, "pooled_active": pooled_active}
    if cohort >= min_n:
        hits, n, window = cohort_active, cohort, f"{young}-{old} h"
    elif pooled >= min_n:
        hits, n, window = pooled_active, pooled, pool
    else:
        return cell(subject, "unknown", f"Not measured: {pooled:,} ads made inactive {pool} ago, "
                    f"under the {min_n} this arm needs", portal=portal, facts=facts)
    share, low = hits / n, wilson_lower(hits, n)
    facts.update(window=window, share=round(share, 4), lower_bound=round(low, 4))
    said = (f"{hits:,} of {n:,} ads made inactive {window} ago are active again ({share:.1%}, "
            f"lower bound {low:.1%})")
    warn = float(thresholds["delisting_precision_warn"])
    fail = float(thresholds["delisting_precision_fail"])
    conditions = []
    if low > fail:
        conditions.append(("fail", f"{said} (Problem over {fail:.0%})"))
    elif low > warn:
        conditions.append(("warn", f"{said} (Watch over {warn:.0%})"))
    return judged(subject, conditions, said, value=round(share * 100, 1), unit="%",
                  portal=portal, facts=facts)


def check_delisting(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Are ads that left their portal made inactive on time, at normal volume, and correctly?"""
    walks = _walks(conn)
    gaps = _walk_gaps(walks)
    align_h = int(thresholds["delisting_outflow_align_h"])
    outflow: dict[str, dict[tuple[int, int], int]] = {}
    for source, lag, off, ads in fetchall(conn, _OUTFLOW_SQL, {"lags": [0, *_OUTFLOW_LAGS_D],
                                                                "align_h": align_h}):
        outflow.setdefault(source, {})[int(lag), int(off)] = int(ads)
    verify = {row[0]: row[1:] for row in fetchall(conn, _VERIFY_SQL,
                                                   {"priority": QUEUE_PRIORITY_VERIFY})}
    precision = {row[0]: row[1:] for row in fetchall(
        conn, _PRECISION_SQL, {"from_h": _COHORT_H[0], "to_h": _COHORT_H[1], "pool_d": _POOL_D})}
    not_measured: dict[str, str] = {}
    stuck: dict[str, tuple[Any, ...]] | None = None
    fallback_gap = max(gaps.values(), default=0.0)
    if gaps:
        limits = {source: _stuck_limit_s(gap, thresholds) for source, gap in gaps.items()}
        stuck = {row[0]: row[1:] for row in fetchall(conn, _STUCK_SQL, {
            "sources": list(limits), "limits_s": list(limits.values()),
            "fallback_s": _stuck_limit_s(fallback_gap, thresholds)})}
    else:
        not_measured["stuck"] = (f"no portal has two finished walks in {_WALK_DAYS} d to take a "
                                 "walk gap from")
    portals = sorted(SOURCES)
    cells = []
    for portal in portals:
        cells.append(_outflow_cell(portal, outflow.get(portal, {}), align_h, thresholds))
        if stuck is not None:
            cells.append(_stuck_cell(portal, stuck.get(portal), gaps.get(portal), fallback_gap,
                                     thresholds))
        cells.append(_verify_cell(portal, verify.get(portal), thresholds))
        cells.append(_deferrals_cell(portal, walks.get(portal, []), thresholds))
        cells.append(_precision_cell(portal, precision.get(portal), thresholds))
    return result("delisting", cells, not_measured)
