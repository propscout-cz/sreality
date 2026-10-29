"""Engine: does production honour your rulings, and is the engine deciding and merging?

`check_dedup` reads the engine's state (off, building, merging or idle: a fact on every cell, never
a status) and judges nine cells: the state, the live stream, the build's progress, the merge pause
a build causes, the decision lag of new ads, the lane's lease, your rulings on ad pairs and on
brokers, and the share of the engine's merges an operator split. It reads the live engine's stores
(generation GENERATION) and only those property_merge_events rows the engine or an operator wrote,
by a positive source filter (rule 15); the one ledger read without that filter is _MERGES_SQL's id
floor, which fixes an id boundary and reads no row's content. The engine's statements, keys and
predicates are imported, never copied. It takes the harness's `thresholds`, sends every statement
through the per-check clock and imports neither the harness nor the worker.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from autodedup.apply import (
    LANE_INTERVAL_SETTING,
    MERGE_SOURCE,
    SCOPE_SETTING,
    UNAPPLY_BY_PREFIX,
    Scope as ApplyScope,
    scope_fields,
)
from autodedup.apply_sql import SETTING_SQL
from autodedup.incremental import (
    GENERATION,
    SEED_VERSION,
    bootstrap_key,
    seed_current,
    seed_version_key,
    setting_flag,
    stream_live,
)
from autodedup.incremental_lane import (
    CURSOR_NEW,
    LEASE_TTL_S,
    engine_key,
    pass_scope,
    read_scope_setting,
    scope_setting_key,
)
from autodedup.incremental_scope import ScopeError
from autodedup.incremental_sql import (
    RT_CALIBRATION_PRESENT_SQL,
    RT_FP_READ_SQL,
    RT_LEASE_READ_SQL,
    RT_NEW_LISTINGS_SQL,
    RT_SCOPE_BACKLOG_SQL,
    RT_SCOPE_SCAN_SEEN_SQL,
    RT_STORE_PRESENT_SQL,
)
from autodedup.rt_lease import DISPATCH_TTL_S, NAME as LEASE_NAME
from autodedup.ui_sql import LIVE_STREAM_SQL, NEGATIVE_VERDICTS
from scripts.checks.clock import fetchall, fetchone
from scripts.checks.common import (
    BEAT_FRESH_S,
    WORKER,
    ago,
    as_utc,
    cell,
    iso,
    judged,
    now,
    oneline,
    previous_result,
    result,
)
from toolkit.measures import wilson_lower
from toolkit.property_identity import BIRTH_REASONS

_KEY = "dedup"
_OFF, _BUILDING, _MERGING, _IDLE = "off", "building", "merging", "idle"
# property_merge_events.source of the two hands this check reads; 'operator' has no constant.
_OPERATOR = "operator"
_SOURCES = (MERGE_SOURCE, _OPERATOR)
# The decision-lag cohort: in-scope ads first seen this many hours ago (the young edge gives the
# engine an hour, well past its feeds' 5-min settle lag and a pass's 17.5-min deadline).
_LAG_WINDOW_H = (1, 24)
# The splits cohort: engine merge groups this many days old, so an operator had a week to notice.
_SPLITS_COHORT_D = (7, 14)
# A deploy hands the lease over within about a minute (the old container's SIGTERM release).
_LEASE_GRACE_S = 120
# How far back the brake and the apply scope's history is read for the state's start.
_HISTORY_D = 28
# An id floor sits this much older than its window: one transaction stamps all its rows with its
# own start, so a merge or a drain flush committed late still lands above the floor.
_FLOOR_SLACK_H = 1
# More arrivals than this in a day (about eight days of the corpus's ~5,900) cut the cohort short.
_LAG_WINDOW_ROWS = 50_000
# A lane on and out of its build commits a pass every few minutes (a pass is cut at 1,050 s); an
# hour in which no pass committed a new ad is a stall, so a brake, build, seed or engine change no
# longer excuses the lag.
_COMMIT_STALL_S = 3600
# A decision-lag Problem leaves an open reading (open_reading, opened_at, recovering_since) that
# is judged through the excuses, which would otherwise hide its OK for up to a day. It closes at
# the reading after an OK stored this long after recovering_since, on the stored run_at (measured
# time, as the ladder's): the ladder recovers on a second OK at least _hold(600) = 1,800 s after its
# first, and one 600 s cadence more covers a ladder that took its first OK a reading late.
_RECOVERY_S = 2400
# The ladder closes an incident nobody measured for a week (UNMEASURED_CLOSE_DAYS), and the open
# reading closes with it.
_OPEN_READING_MAX_S = 7 * 86400
# The two lease holders that are not a worker's pass: incremental_lane.run_rt_seed and
# apply._Writer spell them inline (no constant to import; tests pin both writers).
_SEED_HOLDER, _DISPATCH_HOLDER = "rt_seed:", "dispatch:"
# A worker pass's holder, `<host>:<pid>:<the second its pass started>`: the shape
# incremental_sql._RT_LEASE_PREDECESSOR_WHERE matches (a test pins the two against each other).
_PASS_HOLDER = re.compile(r"[^:]+:[0-9]+:([0-9]{1,18})")
# RT_FP_READ_SQL's `first_decided_epoch` column (a test pins its place in the select list).
_FP_DECIDED_COLUMN = 14

# The brake and the apply scope, newest change first. Writers do not stamp app_settings.updated_at,
# so every age of either comes from this trigger-kept history, whose `value` is the OLD value: it
# held until `replaced_at`.
_HISTORY_SQL = """
select h.key, h.value, h.replaced_at
  from public.app_settings_history h
 where h.key = any(%(keys)s::text[])
   and h.replaced_at >= now() - make_interval(days => %(days)s::int)
 order by h.replaced_at desc, h.id desc
"""

# When the build flag and the seed version were written, and when a pass last committed a new ad:
# the engine's own stamps, which the lane's settings read does not return. The lane rewrites the
# new feed's cursor only for a window that held rows (its claim stages it `if size:`), so with no
# new listing at all for an hour the stamp ages while passes commit: last_commit_age_s then counts
# from the last new ad, not the last pass, and the lag arm, its excuses lapsed, judges a cohort the
# caught-up feed has decided.
_STAMPS_SQL = """
select s.key, s.updated_at
  from autodedup.settings s
 where s.key = any(%(settings)s::text[])
union all
select c.name, c.updated_at
  from autodedup.scan_cursor c
 where c.name = any(%(cursors)s::text[])
"""

# The live worker's boot: a pass holder that started before it belongs to a process that is gone.
_WORKER_SQL = """
select w.beat_at, w.started_at
  from public.worker_heartbeats w
 where w.worker = %(worker)s::text
"""

# First decisions of the live generation since a moment (autodedup_rt_fp_decided_idx).
_DECISIONS_SQL = """
select count(*)::bigint
  from autodedup.rt_fp f
 where f.generation = %(generation)s::text
   and f.first_decided_at >= %(since)s::timestamptz
"""

# The newest listing older than the window and its slack: first_seen_at is the insert time, so
# every ad first seen inside the window sits above it (one day of rows, backward on listings_pkey;
# no index serves first_seen_at alone).
_ARRIVAL_FLOOR_SQL = """
select coalesce((select l.id
                   from public.listings l
                  where l.first_seen_at < now() - make_interval(hours => %(hours)s::int)
                  order by l.id desc
                  limit 1), 0)
"""

# Merge groups of the engine and of operators over the splits cohort's span, a split's birth rows
# left out. Nothing indexes created_at or source, so a backward primary-key scan finds the newest
# row older than the span and the id range above it is read; the span is then judged on each
# group's own stamp, because a created_at predicate on the rows made the planner AND in a whole
# scan of the legacy (listing_id, created_at) index: 249 ms against 50 ms (EXPLAIN, 10-19).
_MERGES_SQL = """
with floor as materialized (
    select coalesce((select e.id
                       from public.property_merge_events e
                      where e.created_at < now() - make_interval(days => %(old_d)s::int,
                                                                  hours => %(slack_h)s::int)
                      order by e.id desc
                      limit 1), 0) as id
), groups as (
    select e.source, e.merge_group_id, min(e.created_at) as merged_at,
           bool_or(e.undone_at is not null
                   and not starts_with(coalesce(e.undone_by, ''), %(unapply_by)s::text)) as split,
           bool_or(e.undone_at is not null
                   and starts_with(coalesce(e.undone_by, ''), %(unapply_by)s::text)) as unapplied,
           max(e.created_at) filter (where e.undone_at is null) as newest_live
      from public.property_merge_events e
     where e.id > (select f.id from floor f)
       and e.source = any(%(sources)s::text[])
       and e.reason <> all(%(birth_reasons)s::text[])
     group by e.source, e.merge_group_id
)
select g.source,
       count(*) filter (where g.merged_at >= now() - interval '24 hours')::int,
       count(*) filter (where g.merged_at >= now() - interval '7 days')::int,
       max(g.newest_live),
       count(*) filter (where g.merged_at < now() - make_interval(days => %(young_d)s::int))::int,
       count(*) filter (where g.merged_at < now() - make_interval(days => %(young_d)s::int)
                          and g.split)::int,
       count(*) filter (where g.merged_at < now() - make_interval(days => %(young_d)s::int)
                          and g.unapplied)::int
  from groups g
 where g.merged_at >= now() - make_interval(days => %(old_d)s::int)
 group by g.source
"""

# Your standing negative rulings on ad pairs as the engine obeys them (apply.Negatives): the
# newest pair ruling in NEGATIVE_VERDICTS, dated from the start of its current negative run (a
# re-ruling with another note appends a newer row), or a must_not_link row of any source, dated
# by its own stamp where no such ruling stands. A breach: both ads on one property now, and a
# live merge row of the engine's on either ad newer than the ruling. Together-now reads listings
# alone, whatever joined the pair; negative set rulings are counted, never judged.
_RULINGS_SQL = """
with spell as (
    select p.listing_lo, p.listing_hi, min(p.decided_at) as ruled_at
      from (select v.listing_lo, v.listing_hi, v.decided_at,
                   count(*) filter (where v.verdict <> all(%(negatives)s::text[]))
                       over (partition by v.listing_lo, v.listing_hi
                             order by v.decided_at desc, v.id desc) as words_since
              from autodedup.verdicts v
             where v.kind = 'pair'
               and v.listing_lo is not null and v.listing_hi is not null) p
     where p.words_since = 0
     group by p.listing_lo, p.listing_hi
), standing as materialized (
    select s.listing_lo, s.listing_hi, s.ruled_at
      from spell s
    union all
    select m.listing_lo, m.listing_hi, m.created_at
      from autodedup.must_not_link m
     where not exists (select 1
                         from spell s
                        where s.listing_lo = m.listing_lo and s.listing_hi = m.listing_hi)
), together as (
    select t.listing_lo, t.listing_hi, t.ruled_at
      from standing t
      join public.listings a on a.id = t.listing_lo
      join public.listings b on b.id = t.listing_hi
     where a.property_id = b.property_id
), sets as (
    select distinct on (n.set_key) n.verdict
      from (select v.verdict, v.decided_at, v.id,
                   case when v.member_ids is null then 'key ' || v.cluster_key::text
                        else 'set ' || array(select distinct u.id
                                               from unnest(v.member_ids) as u(id)
                                              order by u.id)::text
                   end as set_key
              from autodedup.verdicts v
             where v.kind = 'cluster') n
     order by n.set_key, n.decided_at desc, n.id desc
)
select (select count(*) from standing)::int,
       count(*)::int,
       count(*) filter (where exists (
           select 1
             from public.property_merge_events e
            where e.listing_ref_id in (t.listing_lo, t.listing_hi)
              and e.undone_at is null
              and e.source = any(%(sources)s::text[])
              and e.created_at > t.ruled_at))::int,
       (select count(*) from sets x where x.verdict = any(%(negatives)s::text[]))::int
  from together t
"""

# Moved verbatim from verify_pipeline's broker_merge_suppression check (wave 5): an active
# suppression whose two identities sit under one broker means an operator's no was bypassed.
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


@dataclass(frozen=True)
class _Engine:
    """What the arms read: the clock, the brake, the build, both scopes and the engine's stamps."""

    at: _dt.datetime
    present: bool
    interval: int
    state: str
    since: _dt.datetime | None
    control: dict[str, Any]
    scope_blocks: int | None
    scope_all: bool
    seeded: bool | None
    build_start: _dt.datetime | None
    build_end: _dt.datetime | None
    last_lift: _dt.datetime | None
    seeded_at: _dt.datetime | None
    engine_since: _dt.datetime | None
    last_commit: _dt.datetime | None

    def age(self, moment: _dt.datetime | None) -> float | None:
        return None if moment is None else (self.at - moment).total_seconds()


@dataclass(frozen=True)
class _Merges:
    """One source's merge groups: 24 h, 7 d, its newest live merge, and the splits cohort."""

    day: int = 0
    week: int = 0
    newest: _dt.datetime | None = None
    cohort: int = 0
    split: int = 0
    unapplied: int = 0


def _decoded(value: Any) -> Any:
    """An app_settings value as the engine's own reader decodes it (JSON text is parsed)."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _interval(value: Any) -> int:
    """The lane's interval as apply.lane_interval reads the row: absent or unreadable is 0."""
    value = _decoded(value)
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _apply_scope(value: Any) -> tuple[ApplyScope | None, str | None]:
    """The apply scope as the reconcile's gate reads it (apply.scope_closed): the scope, and why it
    admits no live merge (None while it does)."""
    value = _decoded(value)
    if value is not None and not isinstance(value, Mapping):
        return None, f"{SCOPE_SETTING} must be a JSON object, got {type(value).__name__}"
    try:
        scope = ApplyScope(**scope_fields(value))
    except ValueError as exc:
        return None, str(exc)
    return scope, "; ".join(scope.live_problems()) or None


def _changes(history: list[tuple[Any, ...]], key: str, parse: Callable[[Any], Any],
             current: Any) -> list[tuple[_dt.datetime, Any, Any]]:
    """One setting's changes in the history read, newest first: (when, before, after)."""
    out, after = [], current
    for name, value, at in history:
        if name == key:
            before = parse(value)
            out.append((as_utc(at), before, after))
            after = before
    return out


def _newest(changes: list[tuple[_dt.datetime, Any, Any]],
            test: Callable[[Any, Any], bool]) -> _dt.datetime | None:
    return next((at for at, before, after in changes if test(before, after)), None)


def _latest(*moments: _dt.datetime | None) -> _dt.datetime | None:
    return max((m for m in moments if m is not None), default=None)


def _setting(conn: Any, key: str) -> Any:
    row = fetchone(conn, SETTING_SQL, {"key": key})
    return None if row is None else row[0]


def _engine_since(recorded: Any) -> _dt.datetime | None:
    """Since when the engine has run under its digest (`rt_engine`'s own stamp), when readable."""
    try:
        return as_utc(recorded.get("since")) if isinstance(recorded, Mapping) else None
    except (TypeError, ValueError):
        return None


def _read_engine(conn: Any) -> _Engine:
    """The state (off, building, merging, idle in that precedence) and the stamps behind it."""
    at = now(conn)
    present = bool((fetchone(conn, RT_STORE_PRESENT_SQL) or (False,))[0])
    interval = _interval(_setting(conn, LANE_INTERVAL_SETTING))
    scope, closed = _apply_scope(_setting(conn, SCOPE_SETTING))
    history = fetchall(conn, _HISTORY_SQL, {"keys": [LANE_INTERVAL_SETTING, SCOPE_SETTING],
                                            "days": _HISTORY_D})
    flag_key, seed_key = bootstrap_key(GENERATION), seed_version_key(GENERATION)
    control = {str(key): value for key, value in fetchall(conn, LIVE_STREAM_SQL, {"keys": [
        flag_key, seed_key, scope_setting_key(GENERATION), engine_key(GENERATION)]})}
    stamps = {str(name): as_utc(stamp) for name, stamp in fetchall(
        conn, _STAMPS_SQL, {"settings": [flag_key, seed_key], "cursors": [CURSOR_NEW]})}
    building = setting_flag(control.get(flag_key))
    brake = _changes(history, LANE_INTERVAL_SETTING, _interval, interval)
    gate = _changes(history, SCOPE_SETTING, lambda value: _apply_scope(value)[1] is None,
                    closed is None)
    last_lift = _newest(brake, lambda before, after: before <= 0 < after)
    build_start = stamps.get(flag_key) if building else None
    build_end = stamps.get(flag_key) if flag_key in control and not building else None
    if interval <= 0:
        state, since = _OFF, _newest(brake, lambda before, after: before > 0 >= after)
    elif building:
        state, since = _BUILDING, _latest(build_start, last_lift)
    else:
        state = _MERGING if closed is None else _IDLE
        turned = _newest(gate, lambda before, after: before != after)
        since = _latest(build_end, last_lift, turned)
    seeded = None
    if present and state != _OFF:
        seeded = fetchone(conn, RT_CALIBRATION_PRESENT_SQL, {"generation": GENERATION}) is not None
    return _Engine(
        at=at, present=present, interval=interval, state=state, since=since, control=control,
        scope_blocks=None if scope is None else len(scope.blocks or ()),
        scope_all=bool(scope and scope.all_blocks), seeded=seeded, build_start=build_start,
        build_end=build_end, last_lift=last_lift, seeded_at=stamps.get(seed_key),
        engine_since=_engine_since(control.get(engine_key(GENERATION))),
        last_commit=stamps.get(CURSOR_NEW))


def _merges(conn: Any) -> dict[str, _Merges]:
    """Merge groups per source (the engine's and operators'), a split's births left out."""
    young, old = _SPLITS_COHORT_D
    rows = fetchall(conn, _MERGES_SQL, {
        "old_d": old, "slack_h": _FLOOR_SLACK_H, "young_d": young, "sources": list(_SOURCES),
        "birth_reasons": sorted(BIRTH_REASONS), "unapply_by": UNAPPLY_BY_PREFIX})
    return {str(r[0]): _Merges(int(r[1] or 0), int(r[2] or 0), as_utc(r[3]), int(r[4] or 0),
                               int(r[5] or 0), int(r[6] or 0)) for r in rows}


def _percentile(values: list[float], q: float) -> float | None:
    """`percentile_cont(q)`: linear between the two nearest ranks; None over nothing."""
    if not values:
        return None
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _when(engine: _Engine, moment: _dt.datetime | None) -> str:
    seconds = engine.age(moment)
    return "not on record" if seconds is None else f"{ago(seconds)} ago"


def _n(count: int, one: str, many: str) -> str:
    """The words that agree with a count: _n(1, "pair was", "pairs were") is "pair was"."""
    return one if count == 1 else many


def _state_cell(engine: _Engine, merges: dict[str, _Merges]) -> dict[str, Any]:
    """The state is a fact, never a status: this cell is OK in every state."""
    ours, theirs = merges.get(MERGE_SOURCE, _Merges()), merges.get(_OPERATOR, _Merges())
    newest, commit = engine.age(ours.newest), engine.age(engine.last_commit)
    facts = {"state": engine.state, "state_since": iso(engine.since),
             "interval_s": engine.interval, "scope_blocks": engine.scope_blocks,
             "scope_all_blocks": engine.scope_all,
             "seed_version": engine.control.get(seed_version_key(GENERATION)),
             "newest_merge_age_s": None if newest is None else round(newest),
             "last_commit_age_s": None if commit is None else round(commit),
             "merges_24h_engine": ours.day, "merges_7d_engine": ours.week,
             "merges_24h_operator": theirs.day, "merges_7d_operator": theirs.week}
    since = (f"since {ago(engine.age(engine.since))} ago" if engine.since is not None else
             f"(no change of the brake, the build or the apply scope in {_HISTORY_D} d)")
    said = f"{engine.state} {since}"
    if engine.state in (_MERGING, _IDLE) and commit is not None and commit > _COMMIT_STALL_S:
        said += f"; no pass has committed a new ad for {ago(commit)}"
    return cell("state", "ok", said, facts=facts)


def _stream_cell(engine: _Engine) -> dict[str, Any]:
    """Watch while the lane runs but its stream cannot go live; never Problem."""
    stored = engine.control.get(seed_version_key(GENERATION))
    facts = {"state": engine.state, "seed_version": stored, "seed_version_needed": SEED_VERSION,
             "seeded": engine.seeded, "store_present": engine.present,
             "live": stream_live(engine.control, GENERATION)}
    if engine.state == _OFF:
        return cell("stream", "ok", "off: the stream is judged while the lane runs", facts=facts)
    conditions = []
    if not engine.present:
        conditions.append(("warn", "the engine's store is absent (a migration is missing), so "
                                   "every pass skips (Watch)"))
    elif not engine.seeded:
        conditions.append(("warn", "the engine has no seed, so every pass skips until it is "
                                   "seeded (Watch)"))
    elif not seed_current(engine.control, GENERATION):
        conditions.append(("warn", f"the engine's store was built by seed {stored or 'none'} and "
                                   f"this code needs {SEED_VERSION}: it decides but merges "
                                   "nothing until it is seeded again (Watch)"))
    ok = ("building: the stream goes live when the build ends" if engine.state == _BUILDING else
          f"the stream is live (seed {SEED_VERSION})")
    return judged("stream", conditions, ok, facts=facts)


def _lane_blocks(engine: _Engine) -> set[str] | None:
    """The lane scope's blocks as its walks key them; None for the whole corpus or no scope."""
    try:
        scope = pass_scope(read_scope_setting(engine.control, GENERATION))
    except ScopeError:
        return None
    return None if scope.whole_corpus else set(scope.label().split())


def _togo(eta_h: float) -> str:
    """The build's ETA as the page says it: "at least N to go", or "under a minute to go"."""
    return "under a minute to go" if eta_h * 3600 < 60 else f"at least {ago(eta_h * 3600)} to go"


def _build_cell(conn: Any, engine: _Engine, thresholds: dict[str, Any]) -> dict[str, Any]:
    """While building: Problem at no decision in the window with ads waiting, Watch past the ETA."""
    facts: dict[str, Any] = {"state": engine.state}
    if engine.state != _BUILDING:
        waits = engine.state == _OFF and setting_flag(engine.control.get(bootstrap_key(
            GENERATION)))
        return cell("build", "ok", "off: the build waits for the brake to lift" if waits else
                    f"{engine.state}: not building", facts=facts)
    if not engine.present:
        return cell("build", "unknown", "Not measured: the engine's store is absent", facts=facts)
    window_h = float(thresholds["dedup_build_window_h"])
    start = _latest(engine.at - _dt.timedelta(hours=window_h), engine.build_start,
                    engine.last_lift)
    hours = (engine.at - start).total_seconds() / 3600
    decided = int((fetchone(conn, _DECISIONS_SQL, {"generation": GENERATION, "since": start})
                   or (0,))[0] or 0)
    backlog = int((fetchone(conn, RT_SCOPE_BACKLOG_SQL, {"generation": GENERATION}) or (0,))[0]
                  or 0)
    walked = {str(row[0]) for row in fetchall(conn, RT_SCOPE_SCAN_SEEN_SQL,
                                               {"generation": GENERATION})}
    blocks = _lane_blocks(engine)
    unwalked = None if blocks is None else len(blocks - walked)
    rate = decided / hours if hours > 0 else 0.0
    eta_h = backlog / rate if rate > 0 else None
    facts.update(window_h=round(hours, 2), window_start=iso(start), decisions=decided,
                 rate_per_h=round(rate, 1), backlog=backlog,
                 eta_h=None if eta_h is None else round(eta_h, 1),
                 blocks_total=None if blocks is None else len(blocks),
                 blocks_walked=None if blocks is None else len(blocks) - unwalked,
                 build_started_at=iso(engine.build_start), last_lift_at=iso(engine.last_lift))
    waiting = f"{backlog:,} {_n(backlog, 'ad waits', 'ads wait')}"
    said = (f"building since {_when(engine, engine.build_start)}: {decided:,} "
            f"{_n(decided, 'decision', 'decisions')} in the last {hours:.1f} h ({rate:,.0f} an "
            f"hour), {waiting} in the walked blocks")
    if unwalked:
        said += f", {unwalked:,} of {len(blocks):,} blocks not walked yet"
    eta_d = float(thresholds["dedup_build_eta_warn_d"])
    conditions = []
    if decided == 0 and backlog > 0 and hours >= window_h:
        conditions.append(("fail", f"no decision in {hours:.1f} h while {waiting} and the lane "
                                   f"runs (Problem at 0 in {window_h:g} h)"))
    elif eta_h is not None and eta_h > eta_d * 24:
        conditions.append(("warn", f"{said}: {_togo(eta_h)}, a lower bound "
                                   f"(Watch past {eta_d:g} d)"))
    if backlog == 0 and unwalked:
        ok = f"waiting for the walk of {unwalked:,} {_n(unwalked, 'block', 'blocks')}; {said}"
    elif eta_h is not None:
        ok = f"{said}: {_togo(eta_h)} (a lower bound)"
    else:
        ok = said
    return judged("build", conditions, ok, value=round(rate, 1), unit="decisions/h", facts=facts)


def _pause_cell(engine: _Engine, merges: dict[str, _Merges],
                thresholds: dict[str, Any]) -> dict[str, Any]:
    """While building: the engine's newest merge, counted from the build start; never Problem."""
    facts: dict[str, Any] = {"state": engine.state}
    if engine.state != _BUILDING:
        return cell("merge_pause", "ok", f"{engine.state}: a build pauses the engine's merges, "
                    "and it is not building", facts=facts)
    newest = merges.get(MERGE_SOURCE, _Merges()).newest
    since = _latest(engine.build_start, newest) or engine.at
    pause = engine.age(since)
    facts.update(pause_s=round(pause), newest_merge_at=iso(newest),
                 build_started_at=iso(engine.build_start))
    origin = ("its newest merge" if newest is not None and newest >= since else
              "the build began")
    said = (f"the engine has merged nothing for {ago(pause)}, since {origin}; a build pauses its "
            "merges and yours go on")
    warn_h = float(thresholds["dedup_pause_warn_h"])
    conditions = []
    if pause > warn_h * 3600:
        conditions.append(("warn", f"{said} (Watch past {warn_h:g} h, never Problem)"))
    return judged("merge_pause", conditions, said, value=round(pause / 3600, 1), unit="h",
                  facts=facts)


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _excuses(engine: _Engine, since: _dt.datetime) -> list[tuple[str, str]]:
    """What inside the window is not lag, as (kind, when): a brake, build, seed or engine change."""
    marks = (("brake", "a brake lifted", engine.last_lift),
             ("build", "a build ended", engine.build_end),
             ("seed", "a seed began", engine.seeded_at),
             ("engine change", "the engine changed", engine.engine_since))
    return [(kind, f"{words} {_when(engine, at)}") for kind, words, at in marks
            if at is not None and at >= since]


def _not_lag(excuses: list[tuple[str, str]]) -> str:
    """The reading of a lag the excuses explain."""
    return (f"Not measured: {_and([why for _, why in excuses])}, inside the last "
            f"{_LAG_WINDOW_H[1]} h; brakes, builds, seeds and engine changes are not lag")


def _stored_at(value: Any) -> _dt.datetime | None:
    """A moment this check stored in a fact; None when absent or unreadable."""
    try:
        return as_utc(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _open_reading(conn: Any,
                  engine: _Engine) -> tuple[_dt.datetime, _dt.datetime | None] | None:
    """The open reading a lag Problem left, as (opened_at, recovering_since); None once closed."""
    stored, cells = previous_result(conn, _KEY)
    held = cells.get("decision_lag") or {}
    facts = held.get("facts") if isinstance(held.get("facts"), Mapping) else {}
    failed = held.get("status") == "fail"
    if not failed and facts.get("open_reading") is not True:
        return None
    opened = _stored_at(facts.get("opened_at")) or (stored if failed else None)
    recovering = _stored_at(facts.get("recovering_since"))
    if opened is None or engine.age(opened) > _OPEN_READING_MAX_S:
        return None
    # A seed after it cut a new generation, whose cohort is not the one that read the Problem.
    if any(at is not None and at > opened for at in (engine.seeded_at, engine.build_start)):
        return None
    if (held.get("status") == "ok" and recovering is not None and stored is not None
            and (stored - recovering).total_seconds() >= _RECOVERY_S):
        return None
    return opened, recovering


def _lag_cell(conn: Any, engine: _Engine, thresholds: dict[str, Any]) -> dict[str, Any]:
    """The decision lag, and the open reading its excuses must not keep from recovering."""
    memory = _open_reading(conn, engine)
    lag = _lag_reading(conn, engine, thresholds, memory is not None)
    status = lag["status"]
    if memory is None and status != "fail":
        return lag
    opened, recovering = memory or (engine.at, None)
    # The ladder's own recovery clock: an OK starts it, a Watch or Problem stops it, and a Not
    # measured reading leaves it as it was.
    if status in ("warn", "fail"):
        recovering = None
    elif status == "ok":
        recovering = recovering or engine.at
    lag["facts"].update(open_reading=True, opened_at=iso(opened))
    if recovering is not None:
        lag["facts"]["recovering_since"] = iso(recovering)
    return lag


def _lag_reading(conn: Any, engine: _Engine, thresholds: dict[str, Any],
                 open_reading: bool) -> dict[str, Any]:
    """While merging or idle: the in-scope ads first seen 1-24 h ago with no decision."""
    subject, (young_h, old_h) = "decision_lag", _LAG_WINDOW_H
    facts: dict[str, Any] = {"state": engine.state}
    if engine.state == _OFF:
        return cell(subject, "unknown", "Not measured: the lane is off; a brake is not lag",
                    facts=facts)
    if engine.state == _BUILDING:
        return cell(subject, "unknown", "Not measured: building", facts=facts)
    if not engine.present:
        return cell(subject, "unknown", "Not measured: the engine's store is absent",
                    facts=facts)
    try:
        scope = pass_scope(read_scope_setting(engine.control, GENERATION))
    except ScopeError as exc:
        return cell(subject, "unknown", f"Not measured: the lane's scope cannot be read "
                    f"({oneline(exc, 120)})", facts=facts)
    since = engine.at - _dt.timedelta(hours=old_h)
    excuses = _excuses(engine, since)
    stall = engine.age(_latest(engine.last_commit, engine.last_lift, engine.build_end))
    facts.update(excused=[why for _, why in excuses],
                 stall_s=None if stall is None else round(stall))
    stalled = stall is not None and stall > _COMMIT_STALL_S
    excused = bool(excuses) and not stalled
    if excused and not open_reading:
        return cell(subject, "unknown", _not_lag(excuses), facts=facts)
    floor = fetchone(conn, _ARRIVAL_FLOOR_SQL, {"hours": old_h + _FLOOR_SLACK_H})
    window = fetchone(conn, RT_NEW_LISTINGS_SQL, {
        "after_id": int(floor[0] or 0) if floor else 0, "lag": young_h * 3600,
        "window": _LAG_WINDOW_ROWS, **scope.params()})
    if window is not None and int(window[1] or 0) >= _LAG_WINDOW_ROWS:
        return cell(subject, "unknown", f"Not measured: more than {_LAG_WINDOW_ROWS:,} ads "
                    f"arrived in {old_h} h, past the window this arm reads", facts=facts)
    ids, stamps = (window[2] or [], window[3] or []) if window else ([], [])
    arrived: dict[int, _dt.datetime] = {}
    for listing_id, stamp in zip(ids, stamps):
        stamp = as_utc(stamp)
        if stamp is not None and stamp >= since:
            arrived[int(listing_id)] = stamp
    decided: dict[int, Any] = {}
    if arrived:
        decided = {int(row[0]): row[_FP_DECIDED_COLUMN] for row in fetchall(
            conn, RT_FP_READ_SQL, {"generation": GENERATION, "ids": sorted(arrived)})}
    n = len(arrived)
    undecided = sum(1 for listing_id in arrived if listing_id not in decided)
    waits = [(float(epoch) - arrived[listing_id].timestamp()) / 60
             for listing_id, epoch in decided.items()
             if listing_id in arrived and epoch is not None]
    p50, p90 = _percentile(waits, 0.5), _percentile(waits, 0.9)
    facts.update(n=n, undecided=undecided, p50_min=None if p50 is None else round(p50, 1),
                 p90_min=None if p90 is None else round(p90, 1))
    seen = f"first seen {young_h}-{old_h} h ago"
    min_n = int(thresholds["dedup_lag_min_n"])
    if n < min_n:
        return cell(subject, "unknown", f"Not measured: {n:,} in-scope {_n(n, 'ad', 'ads')} "
                    f"{seen}, under the {min_n} this arm needs", facts=facts)
    share, low = undecided / n, wilson_lower(undecided, n)
    facts.update(share=round(share, 4), lower_bound=round(low, 4))
    warn, fail = float(thresholds["dedup_lag_warn"]), float(thresholds["dedup_lag_fail"])
    if excused and low > warn:
        # Through its excuses an open reading can only recover: what they leave undecided is
        # their own backlog, not lag, so it never reads Watch or Problem there.
        return cell(subject, "unknown", _not_lag(excuses), facts=facts)
    said = (f"{undecided:,} of {n:,} in-scope ads {seen} {_n(undecided, 'has', 'have')} no "
            f"decision ({share:.1%}, lower bound {low:.1%})")
    if p50 is not None:
        said += f"; the rest were decided p50 {p50:.0f} / p90 {p90:.0f} min after arrival"
    kinds = [kind for kind, _ in excuses]
    if excused:
        said += (f"; judged through the {_and(kinds)} of the last {old_h} h so the open reading "
                 "can recover")
    elif excuses:
        said += (f"; no pass has committed a new ad for {ago(stall)}, so the {_and(kinds)} of the "
                 f"last {old_h} h {_n(len(kinds), 'does', 'do')} not excuse it")
    conditions = []
    if low > fail:
        conditions.append(("fail", f"{said} (Problem over {fail:.0%})"))
    elif low > warn:
        conditions.append(("warn", f"{said} (Watch over {warn:.0%})"))
    return judged(subject, conditions, said, value=round(share * 100, 2), unit="%", facts=facts)


def _holder(holder: Any) -> tuple[str, int | None]:
    """A lease holder's kind, and for a worker's pass the second that pass started."""
    text = str(holder or "")
    match = _PASS_HOLDER.fullmatch(text)
    if match:
        return "pass", int(match.group(1))
    if text.startswith(_SEED_HOLDER):
        return "seed", None
    if text.startswith(_DISPATCH_HOLDER):
        return "dispatch", None
    return ("other" if text else "none"), None


def _held_cell(conn: Any, engine: _Engine, kind: str, epoch: int | None, taken: _dt.datetime,
               expires: _dt.datetime, facts: dict[str, Any]) -> dict[str, Any]:
    """A live lease: OK while the live worker's pass holds it, Watch past the grace otherwise."""
    remaining = max(0.0, (expires - engine.at).total_seconds())
    facts["remaining_s"] = round(remaining)
    left = f"{ago(remaining)} left"
    if kind == "pass":
        beat = fetchone(conn, _WORKER_SQL, {"worker": WORKER})
        beat_at, started = (as_utc(beat[0]), as_utc(beat[1])) if beat else (None, None)
        fresh = beat_at is not None and (engine.at - beat_at).total_seconds() <= BEAT_FRESH_S
        facts["worker_started_at"] = iso(started) if fresh else None
        if not fresh or started is None:
            return cell("lease", "ok", f"a pass holds the lease ({left}); with no fresh start of "
                        "the worker on record, whose pass it is is not judged",
                        value=round(remaining), unit="s", facts=facts)
        if epoch is not None and epoch >= int(started.timestamp()):
            return cell("lease", "ok", f"the worker's own pass holds the lease ({left})",
                        value=round(remaining), unit="s", facts=facts)
        foreign_since, who = started, "a pass of a worker that is gone"
    else:
        foreign_since = taken
        who = {"seed": "a seed", "dispatch": "a batch run"}.get(kind, "an unknown holder")
    foreign = (engine.at - foreign_since).total_seconds()
    facts["foreign_s"] = round(foreign)
    said = (f"{who} holds the lease ({left}, taken {_when(engine, taken)}), so the worker's "
            "passes wait")
    conditions = []
    if foreign > _LEASE_GRACE_S:
        conditions.append(("warn", f"{said} (Watch while anything but the live worker holds it "
                                   f"past {_LEASE_GRACE_S} s; it ends by itself or is released)"))
    else:
        said += f"; a deploy hands it over within {_LEASE_GRACE_S} s"
    return judged("lease", conditions, said, value=round(remaining), unit="s", facts=facts)


def _free_cell(engine: _Engine, kind: str, taken: _dt.datetime | None, expires: _dt.datetime,
               facts: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, Any]:
    """An ended lease: Problem when the lane is on and out of its build but no pass took it."""
    past = max(0.0, (engine.at - expires).total_seconds())
    ttl = LEASE_TTL_S if kind == "pass" else DISPATCH_TTL_S
    span = None if taken is None else (expires - taken).total_seconds()
    # A braked or building lane leaves the row as old as the brake or the build, so the wait is
    # counted from when the lane could take it again (the clock the lag's stall rule uses).
    origin, since = max(((words, at) for words, at in (
        ("it ended", expires), ("the brake lifted", engine.last_lift),
        ("the build ended", engine.build_end)) if at is not None), key=lambda mark: mark[1])
    untaken = max(0.0, (engine.at - since).total_seconds())
    facts.update(past_expiry_s=round(past), untaken_s=round(untaken), untaken_since=iso(since),
                 ended_by=None if span is None else "expiry" if abs(span - ttl) < 1 else "release")
    fail_min = float(thresholds["dedup_lease_fail_past_expiry_min"])
    said = f"no pass holds the lease; it ended {ago(past)} ago"
    if origin != "it ended":
        said += f", before {origin} {ago(untaken)} ago"
    conditions = []
    # An unseeded lane skips before its take: the stream cell names that, as Watch.
    if (engine.state in (_MERGING, _IDLE) and engine.seeded
            and untaken > engine.interval + fail_min * 60):
        conditions.append(("fail", f"no pass has taken the lease in the {ago(untaken)} since "
                                   f"{origin}, while the lane runs every {engine.interval} s: the "
                                   f"lane stopped taking it (Problem past {fail_min:g} min beyond "
                                   "that wait)"))
    return judged("lease", conditions, said, value=round(untaken / 60), unit="min", facts=facts)


def _lease_cell(conn: Any, engine: _Engine, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Who holds the lane's lease, and whether passes still take it."""
    facts: dict[str, Any] = {"state": engine.state, "holder_kind": "none"}
    if not engine.present:
        return cell("lease", "unknown", "Not measured: the engine's store is absent", facts=facts)
    row = fetchone(conn, RT_LEASE_READ_SQL, {"name": LEASE_NAME})
    if row is None:
        return cell("lease", "ok", "no lease on record", facts=facts)
    taken, expires = as_utc(row[1]), as_utc(row[2])
    kind, epoch = _holder(row[0])
    facts.update(holder_kind=kind, taken_at=iso(taken), expires_at=iso(expires))
    if bool(row[3]):
        return _held_cell(conn, engine, kind, epoch, taken, expires, facts)
    return _free_cell(engine, kind, taken, expires, facts, thresholds)


def _rulings_cell(conn: Any, engine: _Engine, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Pairs you ruled apart that the engine merged after your ruling: Problem at one."""
    row = fetchone(conn, _RULINGS_SQL, {"negatives": list(NEGATIVE_VERDICTS),
                                        "sources": [MERGE_SOURCE]})
    negatives, together, breaches, sets = (int(x or 0) for x in (row or (0, 0, 0, 0)))
    facts = {"state": engine.state, "breaches": breaches, "negatives": negatives,
             "together_now": together, "set_rulings_unjudged": sets}
    fail = int(thresholds["dedup_rulings_fail"])
    standing = (f"{negatives:,} pair {_n(negatives, 'ruling', 'rulings')} of yours "
                f"{_n(negatives, 'stands', 'stand')}")
    others = together - breaches
    if others > 0:
        standing += (f"; {others:,} other such {_n(others, 'pair shares', 'pairs share')} a "
                     "property now, merged before your ruling or by another hand: a split for you "
                     "to make")
    if sets:
        standing += (f"; {sets:,} {_n(sets, 'ruling on a whole set', 'rulings on whole sets')} "
                     "cannot be judged pair by pair")
    conditions = []
    if breaches >= fail:
        conditions.append(("fail", f"{breaches:,} {_n(breaches, 'pair', 'pairs')} you ruled apart "
                                   f"{_n(breaches, 'was', 'were')} merged by the engine after "
                                   f"your ruling ({standing}) (Problem at {fail})"))
    ok = f"no pair you ruled apart was merged by the engine after your ruling ({standing})"
    return judged("rulings/ads", conditions, ok, value=breaches, unit="pairs", facts=facts)


def _brokers_cell(conn: Any, engine: _Engine, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Brokers you kept apart that share one broker again: Problem at one."""
    row = fetchone(conn, _BROKER_SUPPRESSION_SQL)
    active, lifted, violations = (int(x or 0) for x in (row or (0, 0, 0)))
    facts = {"state": engine.state, "active": active, "lifted": lifted, "violations": violations}
    fail = int(thresholds["dedup_rulings_fail"])
    conditions = []
    if violations >= fail:
        conditions.append(("fail", f"{violations:,} broker {_n(violations, 'pair', 'pairs')} you "
                                   f"kept apart {_n(violations, 'sits', 'sit')} under one broker "
                                   "again while your no stands: the rail was bypassed "
                                   f"(Problem at {fail})"))
    ok = f"your broker rulings hold ({active:,} standing, {lifted:,} lifted)"
    return judged("rulings/brokers", conditions, ok, value=violations, unit="pairs", facts=facts)


def _splits_cell(engine: _Engine, merges: dict[str, _Merges],
                 thresholds: dict[str, Any]) -> dict[str, Any]:
    """Of the engine's merge groups 7-14 d old, the share an operator split; never Problem."""
    young, old = _SPLITS_COHORT_D
    ours = merges.get(MERGE_SOURCE, _Merges())
    facts: dict[str, Any] = {"state": engine.state, "groups": ours.cohort, "split": ours.split,
                             "unapplied": ours.unapplied}
    min_groups = int(thresholds["dedup_splits_min_groups"])
    if ours.cohort < min_groups:
        return cell("splits", "unknown", f"Not measured: {ours.cohort:,} "
                    f"{_n(ours.cohort, 'group', 'groups')} the engine merged {young}-{old} d ago, "
                    f"under the {min_groups} this arm needs", facts=facts)
    share, low = ours.split / ours.cohort, wilson_lower(ours.split, ours.cohort)
    facts.update(share=round(share, 4), lower_bound=round(low, 4))
    said = (f"{ours.split:,} of {ours.cohort:,} groups the engine merged {young}-{old} d ago "
            f"{_n(ours.split, 'was', 'were')} split by an operator ({share:.1%}; lower bound "
            f"{low:.1%})")
    if ours.unapplied:
        said += f"; {ours.unapplied:,} more the engine took back itself, not counted"
    warn = float(thresholds["dedup_splits_warn"])
    conditions = []
    if low > warn:
        conditions.append(("warn", f"{said} (Watch over {warn:.0%}, never Problem)"))
    return judged("splits", conditions, said, value=round(share * 100, 2), unit="%", facts=facts)


def check_dedup(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Does production honour your rulings, and is the engine deciding and merging?"""
    engine = _read_engine(conn)
    merges = _merges(conn)
    cells = [_state_cell(engine, merges), _stream_cell(engine),
             _build_cell(conn, engine, thresholds), _pause_cell(engine, merges, thresholds),
             _lag_cell(conn, engine, thresholds), _lease_cell(conn, engine, thresholds),
             _rulings_cell(conn, engine, thresholds), _brokers_cell(conn, engine, thresholds),
             _splits_cell(engine, merges, thresholds)]
    return result(_KEY, cells, {})
