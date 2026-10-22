"""Input replays: production history fed through the dedup check, then through the ladder."""

# Each fixture is an aggregate exported read-only from production on 2026-10-19 (UTC to the
# second; no ids, no holder strings, no emails, no block names). Refresh them read-only, never by
# hand, from the SQL below, where `hrs('<from>')` stands for `select generate_series(timestamp
# '<from>', date_trunc('hour', now() at time zone 'UTC'), interval '1 hour') as hr` and every hour,
# day or ten minutes is a UTC bucket's start. One replay runs hourly over the 28 days the exports
# hold, 2026-09-22 00:00Z to 10-19 09:00Z: at each tick it rebuilds the rows check_dedup's
# statements would return then, runs the check on them and feeds its result to the ladder
# (toolkit.system_alerts.reconcile). The lane's own stores enter at the Prague seed (10-16
# 16:28:01Z), the one build no fresh seed has wiped yet. The lease's 10-10 strand, which no store
# keeps, is a second, SYNTHETIC replay at ten-minute ticks (the last test).
#
# tests/fixtures/health_dedup_first_decisions.csv (hour,decided,decided_in_scope), 09:03:42Z, 66
# rows: the live generation's first decisions per hour since the seed, and those of ads in today's
# membership snapshot. Exported first: the next fresh seed deletes them.
#   with s as (select distinct listing_id from autodedup.rt_scope_ids where generation = 'rt'),
#        d as (select date_trunc('hour', f.first_decided_at at time zone 'UTC') as hr,
#                     count(*) as decided, count(s.listing_id) as decided_in_scope
#                from autodedup.rt_fp f left join s on s.listing_id = f.listing_id
#               where f.generation = 'rt' and f.first_decided_at >= '2026-10-16 16:27Z'
#               group by 1)
#   select h.hr, coalesce(d.decided, 0), coalesce(d.decided_in_scope, 0)
#     from hrs('2026-10-16 16:00') h left join d on d.hr = h.hr order by 1;
#
# tests/fixtures/health_dedup_scope_scans.csv (hour,scans,first_walks,rows_found,rows_found_first,
# overcap_scans), 09:05:50Z, 66 rows: block walks per hour, those that were their block's first
# (they sum to the lane scope's 120 blocks, 8 obec and 112 cast_obce: the apply scope's 8 town and
# 112 quarter blocks in the lane's spelling), the ads they returned, and walks cut at 20,000 ads.
#   with sc as (select s.scanned_at, s.rows_found, row_number() over (partition by s.block_key
#                      order by s.scanned_at, s.id) as nth
#                 from autodedup.rt_scope_scan s where s.generation = 'rt'),
#        h as (select date_trunc('hour', scanned_at at time zone 'UTC') as hr, count(*) as scans,
#                     count(*) filter (where nth = 1) as first_walks, sum(rows_found) as found,
#                     coalesce(sum(rows_found) filter (where nth = 1), 0) as found_first,
#                     count(*) filter (where rows_found >= 20000) as overcap
#                from sc group by 1)
#   select t.hr, coalesce(h.scans, 0), coalesce(h.first_walks, 0), coalesce(h.found, 0),
#          coalesce(h.found_first, 0), coalesce(h.overcap, 0)
#     from hrs('2026-10-16 16:00') t left join h on h.hr = t.hr order by 1;
#
# tests/fixtures/health_dedup_backlog.csv (hour,entered,backlog), 09:19:58Z, 66 rows: what
# RT_SCOPE_BACKLOG_SQL would have read at each hour's end, rebuilt from today's snapshot: an ad
# enters at its block's first walk at or after its location resolved (30 min of slack for the
# walk's transaction stamp) and waits until its first decision.
#   with sc as (select block_key, scanned_at from autodedup.rt_scope_scan where generation = 'rt'),
#        fs as (select block_key, min(scanned_at) as first_at from sc group by 1),
#        ent as (select i.listing_id, coalesce(min(sc.scanned_at), min(fs.first_at)) as entered_at
#                  from autodedup.rt_scope_ids i join fs on fs.block_key = i.block_key
#                  left join sc on sc.block_key = i.block_key and sc.scanned_at
#                       >= coalesce(i.resolved_at, '-infinity') - interval '30 minutes'
#                 where i.generation = 'rt' group by 1),
#        j as (select date_trunc('hour', e.entered_at at time zone 'UTC') as eh,
#                     date_trunc('hour', f.first_decided_at at time zone 'UTC') as dh, count(*) as n
#                from ent e left join autodedup.rt_fp f
#                  on f.generation = 'rt' and f.listing_id = e.listing_id group by 1, 2)
#   select t.hr, coalesce(sum(j.n) filter (where j.eh = t.hr), 0),
#          coalesce(sum(j.n) filter (where j.eh <= t.hr and (j.dh is null or j.dh > t.hr)), 0)
#     from hrs('2026-10-16 16:00') t cross join j group by 1 order by 1;
#
# tests/fixtures/health_dedup_arrivals.csv (hour,arrived,in_snapshot,decided_1h,decided_24h,
# undecided,by_offset), 09:08:29Z, 82 rows: the in-scope ads first seen per hour since 10-16, in
# scope by the lane's own predicate (the scope's codes against listing_location; the snapshot was
# last refreshed 10-18 21:24Z and holds no later arrival), those in the snapshot, decided within
# 1 h / 24 h, undecided at the export, and by_offset: the decided ones counted by the hour of their
# first decision, k = 0..24 hours after the arrival's hour, then 25 h or later.
#   with sc as (select array_agg((b->>'code')::bigint) filter (where b->>'grain' = 'obec') as ob,
#                      array_agg((b->>'code')::bigint)
#                        filter (where b->>'grain' = 'cast_obce') as co
#                 from autodedup.settings s, jsonb_array_elements(s.value) b
#                where s.key = 'rt_scope:rt'),
#        arr as (select l.first_seen_at as seen, f.first_decided_at as decided,
#                       exists (select 1 from autodedup.rt_scope_ids i
#                                where i.generation = 'rt' and i.listing_id = l.id) as in_snapshot
#                  from public.listings l join public.listing_location ll on ll.listing_id = l.id
#                 cross join sc left join autodedup.rt_fp f
#                    on f.generation = 'rt' and f.listing_id = l.id
#                 where l.source = any(array['bazos', 'bezrealitky', 'ceskereality', 'idnes',
#                                            'maxima', 'mmreality', 'realitymix', 'remax',
#                                            'sreality'])
#                   and l.first_seen_at >= '2026-10-16 00:00Z'
#                   and (ll.obec_kod = any(sc.ob) or ll.cast_obce_kod = any(sc.co))),
#        b as (select date_trunc('hour', seen at time zone 'UTC') as hr, count(*) as arrived,
#                     count(*) filter (where in_snapshot) as in_snapshot,
#                     count(*) filter (where decided <= seen + interval '1 hour') as decided_1h,
#                     count(*) filter (where decided <= seen + interval '24 hours') as decided_24h,
#                     count(*) filter (where decided is null) as undecided
#                from arr group by 1),
#        k as (select date_trunc('hour', seen at time zone 'UTC') as hr,
#                     least(25, greatest(0, floor(extract(epoch from
#                         date_trunc('hour', decided at time zone 'UTC')
#                         - date_trunc('hour', seen at time zone 'UTC')) / 3600)))::int as k,
#                     count(*) as n
#                from arr where decided is not null group by 1, 2)
#   select t.hr, coalesce(b.arrived, 0), coalesce(b.in_snapshot, 0), coalesce(b.decided_1h, 0),
#          coalesce(b.decided_24h, 0), coalesce(b.undecided, 0),
#          (select string_agg(coalesce(k.n, 0)::text, ' ' order by g.k)
#             from generate_series(0, 25) g(k) left join k on k.hr = t.hr and k.k = g.k)
#     from hrs('2026-10-16 00:00') t left join b on b.hr = t.hr order by 1;
#
# tests/fixtures/health_dedup_merges.csv (hour,source,groups,rows,live_rows), 09:11:56Z, 174 rows,
# an hour with no merge of a source left out: the engine's and operators' merge groups per hour of
# the 28 days (the engine's whole history: its first merge was 09-25), their ledger rows and the
# rows not undone at the export; a split's birth rows left out, its letter joins counted.
#   with fl as materialized (select coalesce((select e.id from public.property_merge_events e
#                                               where e.created_at < now() - interval '28 days'
#                                               order by e.id desc limit 1), 0) as id),
#        w as (select e.merge_group_id, e.source, e.created_at, e.undone_at
#                from public.property_merge_events e, fl
#               where e.id > fl.id and e.source = any(array['autodedup', 'operator'])
#                 and e.reason <> all(array['ingest_grouping', 'split_new'])
#                 and e.created_at >= now() - interval '28 days')
#   select date_trunc('hour', created_at at time zone 'UTC'), source,
#          count(distinct merge_group_id), count(*), count(*) filter (where undone_at is null)
#     from w group by 1, 2 order by 1, 2;
#
# tests/fixtures/health_dedup_undo.csv (day,source,kind,merged_day,groups,rows), 09:13:17Z, 8 rows:
# merge groups undone per day by the engine's own take-back (`unapply`) or any other hand
# (`operator`), with the day their group was merged.
#   with u as materialized (select merge_group_id, source, undone_at, undone_by
#                             from public.property_merge_events
#                            where source = any(array['autodedup', 'operator'])
#                              and reason <> all(array['ingest_grouping', 'split_new'])
#                              and undone_at >= now() - interval '28 days'),
#        g as (select x.merge_group_id,
#                     (select min(e.created_at) from public.property_merge_events e
#                       where e.merge_group_id = x.merge_group_id) as merged_at
#                from (select distinct merge_group_id from u) x)
#   select date_trunc('day', u.undone_at at time zone 'UTC'), u.source,
#          case when starts_with(u.undone_by, 'autodedup-unapply:') then 'unapply'
#               else 'operator' end,
#          date_trunc('day', g.merged_at at time zone 'UTC'), count(distinct u.merge_group_id),
#          count(*)
#     from u join g using (merge_group_id) group by 1, 2, 3, 4 order by 1, 2, 3, 4;
#
# tests/fixtures/health_dedup_settings.csv (key,replaced_at,interval_s,blocks,town,quarter,
# all_blocks,listing_ids,category_types,max_clusters_per_run), 09:14:12Z, 37 rows: every change of
# the brake and the apply scope, each key's whole history. The history keeps the OLD value: a row
# says its value held UNTIL replaced_at; a key's last row (no replaced_at) is in force at export.
#   with h as (select key, replaced_at, value from public.app_settings_history
#               where key = any(<keys>) and replaced_at >= now() - interval '28 days'
#              union all select key, null, value from public.app_settings where key = any(<keys>))
#   select key, replaced_at,
#          case when key = 'realtime_autodedup_interval_seconds' then value #>> '{}' end,
#          case when key = 'autodedup_apply_scope' and jsonb_typeof(value->'blocks') = 'array'
#               then jsonb_array_length(value->'blocks') end,
#          case when key = 'autodedup_apply_scope' then (select count(*) from
#               jsonb_array_elements_text(case when jsonb_typeof(value->'blocks') = 'array'
#               then value->'blocks' else '[]' end) b where split_part(b, ':', 1) = 'town') end,
#          <the same for 'quarter'>,
#          case when key = 'autodedup_apply_scope' then coalesce(value->>'all_blocks', '') end,
#          case when key = 'autodedup_apply_scope' then case when jsonb_typeof(value->'listing_ids')
#               = 'array' then jsonb_array_length(value->'listing_ids')::text
#               else coalesce(jsonb_typeof(value->'listing_ids'), 'absent') end end,
#          <the same for 'category_types'>,
#          case when key = 'autodedup_apply_scope'
#               then coalesce(value->>'max_clusters_per_run', '') end
#     from h order by replaced_at nulls last, key;
#  with <keys> = array['realtime_autodedup_interval_seconds', 'autodedup_apply_scope'].
#
# tests/fixtures/health_dedup_commits.csv (at,writes,commits,first_decisions), 09:15:54Z, 56 rows:
# per ten minutes of 10-19, the store rows whose last write fell in it, the distinct commit stamps
# among them (one per committed transaction that still owns a row: a lower bound) and the first
# decisions. The pass cursor rt_new read 07:55:08Z at the export: nothing committed after it.
#   with w as (select date_bin('10 minutes', updated_at at time zone 'UTC', '2026-01-01') as at,
#                     count(*) as writes, count(distinct updated_at) as commits
#                from autodedup.rt_fp where generation = 'rt' and updated_at >= '2026-10-19 00:00Z'
#               group by 1),
#        d as (select date_bin('10 minutes', first_decided_at at time zone 'UTC', '2026-01-01')
#                       as at, count(*) as first_decisions
#                from autodedup.rt_fp
#               where generation = 'rt' and first_decided_at >= '2026-10-19 00:00Z' group by 1)
#   select t.at, coalesce(w.writes, 0), coalesce(w.commits, 0), coalesce(d.first_decisions, 0)
#     from generate_series(timestamp '2026-10-19 00:00', date_bin('10 minutes', now() at time zone
#                          'UTC', '2026-01-01'), interval '10 minutes') t(at)
#     left join w on w.at = t.at left join d on d.at = t.at order by 1;
#
# tests/fixtures/health_dedup_rulings.csv (day,neg_pair_rows,neg_set_rows,same_pair_rows,
# unsure_pair_rows,spells_started,standing_negative_pairs,mnl_created), 09:17:25Z, 28 rows: your
# rulings per day, the pairs whose current run of negative rulings began that day, the pairs whose
# newest ruling at the day's end was negative, and the must_not_link rows stamped that day.
#   with r as (select (decided_at at time zone 'UTC')::date as day,
#                     count(*) filter (where kind = 'pair' and verdict = any(<neg>)) as neg_pair,
#                     count(*) filter (where kind = 'cluster' and verdict = any(<neg>)) as neg_set,
#                     count(*) filter (where kind = 'pair' and verdict = 'same') as same_pair,
#                     count(*) filter (where kind = 'pair' and verdict = 'unsure') as unsure_pair
#                from autodedup.verdicts where decided_at >= <day 0> group by 1),
#        spell as (select listing_lo, listing_hi, min(decided_at) as since
#                    from (select listing_lo, listing_hi, decided_at,
#                                 count(*) filter (where verdict <> all(<neg>)) over (partition
#                                   by listing_lo, listing_hi order by decided_at desc, id desc)
#                                   as words
#                            from autodedup.verdicts where kind = 'pair'
#                             and listing_lo is not null and listing_hi is not null) p
#                   where words = 0 group by 1, 2),
#        mn as (select (created_at at time zone 'UTC')::date as day, count(*) as n
#                 from autodedup.must_not_link where created_at >= <day 0> group by 1)
#   select d.day, coalesce(r.neg_pair, 0), coalesce(r.neg_set, 0), coalesce(r.same_pair, 0),
#          coalesce(r.unsure_pair, 0),
#          (select count(*) from spell s where (s.since at time zone 'UTC')::date = d.day),
#          (select count(*) filter (where x.verdict = any(<neg>))
#             from (select distinct on (listing_lo, listing_hi) verdict from autodedup.verdicts
#                    where kind = 'pair' and listing_lo is not null and listing_hi is not null
#                      and decided_at < (d.day + 1)::timestamp at time zone 'UTC'
#                    order by listing_lo, listing_hi, decided_at desc, id desc) x),
#          coalesce(mn.n, 0)
#     from generate_series(current_date - 27, current_date, interval '1 day') d(day)
#     left join r on r.day = d.day left join mn on mn.day = d.day order by 1;
#  with <neg> = ui_sql.NEGATIVE_VERDICTS spelled, <day 0> = the first day at 00:00Z, and
#  current_date the UTC date. The same export also read the rulings arm's own figures: 765
#  negatives, 1 pair together now, 0 breaches, 3 rulings on whole sets.
#
# Scalars the replay takes from the same reads: the engine's stamps (autodedup.settings keeps no
# history; (e)'s statement read them): rt_seed_version:rt e936 and rt_scope:rt at 10-16 16:28:01Z
# (= rt_calibration.built_at, the seed), rt_bootstrap:rt false at 10-18 20:08:18Z (the build's
# end), rt_engine:rt since 10-19 08:21:01Z (E969's digest; #1870's of about 04:25Z was overwritten);
# and the store's oldest first decision, 10-16 16:42:20Z (a census of rt_fp at 08:28Z), from
# which (a)'s first hour is spread.
#
# Stated limits. A fresh seed wipes the lane's stores and the engine's settings keep no history,
# so before the Prague seed the replay holds the seed version and the lane scope at the export's
# reading and reads no build (the builds of 10-02/03, 10-06, 10-10/13 and 10-14/15 are not on
# record and read as merging or idle; the 10-12/13 merge-pause Watch is not replayed), no commit
# and no arrival (those decisions were wiped): the decision lag reads Not measured there. The
# lease keeps one row, so no tick reads one; the 10-10 strand is replayed apart, SYNTHETIC.
# Decisions and arrivals are spread evenly across their hour (the first decision from 16:42:20Z);
# an ad is decided half way to its hour's end at offset 0 and in the middle of its offset's hour
# otherwise, an hour's oldest ads first, so p50/p90 are approximate; a pass commits in the middle
# of each hour of (a) with a first decision until 10-19, then of each ten minutes of (f) with a
# commit. Ads retired since the seed are missing (a lower bound); (b2) and (c) read today's
# snapshot and today's locations. A merge hour's groups are placed at its start and are live as
# at the export; an undone group sits in the first hour of its merge day that lost rows, counted
# split from the start of its undo day (an upper bound). The pairs together now and the breaches
# are the export's on every tick, the standing rulings those of the day before. No broker ruling
# was ever made (0 rows). Out-of-scope arrivals are not exported, so the window's size is the
# in-scope count (its 50,000 cap never binds).

from __future__ import annotations

import bisect
import datetime as _dt
import functools
from typing import Any

import pytest

from autodedup import incremental_sql as isql
from autodedup.apply import LANE_INTERVAL_SETTING, MERGE_SOURCE, SCOPE_SETTING
from autodedup.apply_sql import SETTING_SQL
from autodedup.incremental import SEED_VERSION, bootstrap_key, seed_version_key
from autodedup.incremental_lane import CURSOR_NEW, LEASE_TTL_S, engine_key, scope_setting_key
from autodedup.incremental_scope import parse_scope
from autodedup.ui_sql import LIVE_STREAM_SQL
from scripts.checks import dedup as dd
from scripts.verify_pipeline import THRESHOLDS
from tests.scripts.test_checks_platform import _Conn
from tests.scripts.test_checks_platform_replay import _fixture, _incidents, _ticks, _told, _utc

_KEY = "dedup"
_MINUTE, _HOUR, _DAY = (_dt.timedelta(minutes=1), _dt.timedelta(hours=1), _dt.timedelta(days=1))
_FLAG, _SEED_KEY = bootstrap_key(dd.GENERATION), seed_version_key(dd.GENERATION)
_LANE_KEY, _ENGINE_KEY = scope_setting_key(dd.GENERATION), engine_key(dd.GENERATION)
_START, _END = _utc("2026-09-22T00:00:00Z"), _utc("2026-10-19T09:00:00Z")
_SEED = _utc("2026-10-16T16:28:01Z")
_FIRST_DECISION = _utc("2026-10-16T16:42:20Z")
_BUILD_END = _utc("2026-10-18T20:08:18Z")
_ENGINE_SINCE = _utc("2026-10-19T08:21:01Z")
_TOGETHER, _BREACHES, _SET_RULINGS = 1, 0, 3
_DEALS = ("prodej", "pronajem")
# Made-up block codes: the exports count the scopes' blocks and never name one.
_TOWN, _QUARTER = 900_000, 910_000


def _day(text: str) -> _dt.datetime:
    return _utc(f"{text}T00:00:00Z")


def _arm(results: list[tuple[_dt.datetime, dict[str, Any]]],
         subject: str) -> list[tuple[_dt.datetime, dict[str, Any]]]:
    """One cell's readings, tick by tick."""
    return [(at, c) for at, r in results for c in r["details"]["cells"] if c["subject"] == subject]


# --- the brake and the apply scope (e) ------------------------------------------------------------


def _scope_value(row: dict[str, str]) -> dict[str, Any]:
    """The apply scope a row of (e) counts, its blocks spelled with made-up codes."""
    blocks = None
    if row["blocks"]:
        blocks = ([f"town:{_TOWN + i}" for i in range(int(row["town"]))]
                  + [f"quarter:{_QUARTER + i}" for i in range(int(row["quarter"]))])
    listing_ids = row["listing_ids"]
    return {"blocks": blocks, "category_types": list(_DEALS[:int(row["category_types"])]),
            "all_blocks": row["all_blocks"] == "true",
            "listing_ids": (None if listing_ids in ("null", "absent")
                            else list(range(1, int(listing_ids) + 1))),
            "max_clusters_per_run": int(row["max_clusters_per_run"])}


@functools.cache
def _settings() -> dict[str, list[tuple[_dt.datetime | None, Any]]]:
    """Per key, (replaced_at, the value that held until then) in time order; None = in force."""
    out: dict[str, list[tuple[_dt.datetime | None, Any]]] = {}
    for row in _fixture("health_dedup_settings.csv"):
        value = (int(row["interval_s"]) if row["key"] == LANE_INTERVAL_SETTING
                 else _scope_value(row))
        out.setdefault(row["key"], []).append(
            (_utc(row["replaced_at"]) if row["replaced_at"] else None, value))
    return out


def _in_force(key: str, at: _dt.datetime) -> Any:
    """A setting's value at `at`: the first change after it held until then."""
    return next(value for replaced, value in _settings()[key] if replaced is None or replaced > at)


def _history_rows(at: _dt.datetime, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    """_HISTORY_SQL's rows at `at`: the changes of the window so far, newest first."""
    since = at - params["days"] * _DAY
    rows = [(key, value, replaced) for key in params["keys"]
            for replaced, value in _settings().get(key, [])
            if replaced is not None and since <= replaced <= at]
    return sorted(rows, key=lambda row: row[2], reverse=True)


@functools.cache
def _lane_scope() -> list[dict[str, Any]]:
    """`rt_scope:rt` as the seed wrote it: the apply scope's area, spelled as the lane spells it."""
    _, now = _settings()[SCOPE_SETTING][-1]
    towns = sum(b.startswith("town:") for b in now["blocks"])
    return ([{"grain": "obec", "code": _TOWN + i} for i in range(towns)]
            + [{"grain": "cast_obce", "code": _QUARTER + i}
               for i in range(len(now["blocks"]) - towns)])


# --- the build (a, b, b2) and the pass commits (a, f) --------------------------------------------


@functools.cache
def _decisions() -> list[tuple[_dt.datetime, _dt.datetime, int]]:
    """(a)'s hours as (from, to, first decisions), spread evenly from the store's first one."""
    return [(max(_utc(r["hour"]), _FIRST_DECISION), _utc(r["hour"]) + _HOUR, int(r["decided"]))
            for r in _fixture("health_dedup_first_decisions.csv")]


def _decided(since: _dt.datetime, at: _dt.datetime) -> int:
    """_DECISIONS_SQL's count at `at`: the first decisions from `since` on."""
    total = 0.0
    for start, end, n in _decisions():
        lo, hi = max(start, since), min(end, at)
        if n and hi > lo:
            total += n * ((hi - lo) / (end - start))
    return round(total)


@functools.cache
def _hourly(name: str, column: str) -> list[tuple[_dt.datetime, int]]:
    """(the hour's end, its value) per row of an hourly fixture, in time order."""
    return [(_utc(r["hour"]) + _HOUR, int(r[column])) for r in _fixture(name)]


def _backlog(at: _dt.datetime) -> int:
    """RT_SCOPE_BACKLOG_SQL's count at `at`: (b2)'s reading at the end of the hour before."""
    return next((n for end, n in reversed(_hourly("health_dedup_backlog.csv", "backlog"))
                 if end <= at), 0)


def _walked(at: _dt.datetime) -> list[str]:
    """RT_SCOPE_SCAN_SEEN_SQL's blocks at `at`: as many as (b) had walked once by then."""
    n = sum(k for end, k in _hourly("health_dedup_scope_scans.csv", "first_walks") if end <= at)
    return parse_scope(_lane_scope()).label().split()[:n]


@functools.cache
def _commits() -> list[_dt.datetime]:
    """When passes committed: mid-hour of (a)'s hours with a decision, then mid-(f) bucket."""
    cut = _utc("2026-10-19T00:00:00Z")
    stamps = [start + (end - start) / 2 for start, end, n in _decisions() if n and end <= cut]
    stamps += [_utc(r["at"]) + 5 * _MINUTE for r in _fixture("health_dedup_commits.csv")
               if int(r["commits"])]
    return sorted(stamps)


def _last_commit(at: _dt.datetime) -> _dt.datetime | None:
    stamps = _commits()
    i = bisect.bisect_left(stamps, at)
    return stamps[i - 1] if i else None


# --- the merges (d) and the rulings (g) ---------------------------------------------------------


@functools.cache
def _merge_hours() -> list[tuple[str, _dt.datetime, int, int, int]]:
    """(d)'s rows as (source, hour, groups, rows, rows live at export)."""
    return [(r["source"], _utc(r["hour"]), int(r["groups"]), int(r["rows"]), int(r["live_rows"]))
            for r in _fixture("health_dedup_merges.csv")]


@functools.cache
def _undone() -> list[tuple[_dt.datetime, _dt.datetime, str, int]]:
    """The engine's undone groups as (merge hour, undo day, kind, groups)."""
    lost = [hour for source, hour, _, rows, live in _merge_hours()
            if source == MERGE_SOURCE and rows > live]
    out = []
    for r in _fixture("health_dedup_undo.csv"):
        if r["source"] == MERGE_SOURCE:
            merged = _day(r["merged_day"])
            hour = min(h for h in lost if merged <= h < merged + _DAY)
            out.append((hour, _day(r["day"]), r["kind"], int(r["groups"])))
    return out


def _merges_rows(at: _dt.datetime, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    """_MERGES_SQL's rows at `at`: per source, its groups of the span, placed at their hour."""
    old, young = at - params["old_d"] * _DAY, at - params["young_d"] * _DAY
    rows = []
    for source in params["sources"]:
        hours = [(hour, groups, live) for s, hour, groups, _, live in _merge_hours()
                 if s == source and old <= hour and hour + _HOUR <= at]
        if not hours:
            continue
        split = unapplied = 0
        if source == MERGE_SOURCE:
            for hour, undone, kind, groups in _undone():
                if old <= hour < young and undone <= at:
                    if kind == "unapply":
                        unapplied += groups
                    else:
                        split += groups
        rows.append((source, sum(g for h, g, _ in hours if h >= at - _DAY),
                     sum(g for h, g, _ in hours if h >= at - 7 * _DAY),
                     max((h for h, _, live in hours if live), default=None),
                     sum(g for h, g, _ in hours if h < young), split, unapplied))
    return rows


@functools.cache
def _standing() -> list[tuple[_dt.datetime, int]]:
    """(g)'s days as (the day's end, the pair rulings standing then)."""
    return [(_day(r["day"]) + _DAY, int(r["standing_negative_pairs"]))
            for r in _fixture("health_dedup_rulings.csv")]


def _rulings_row(at: _dt.datetime) -> tuple[int, int, int, int]:
    """_RULINGS_SQL's row at `at`: the day before's standing rulings, the export's other three."""
    days = _standing()
    negatives = next((n for end, n in reversed(days) if end <= at), days[0][1])
    return negatives, _TOGETHER, _BREACHES, _SET_RULINGS


# --- the arrivals (c) ---------------------------------------------------------------------------


@functools.cache
def _arrivals() -> list[tuple[int, _dt.datetime, _dt.datetime | None]]:
    """(c)'s ads as (made-up id, first seen, first decided), placed as the stated limits say."""
    ads: list[tuple[int, _dt.datetime, _dt.datetime | None]] = []
    for r in _fixture("health_dedup_arrivals.csv"):
        hour, n = _utc(r["hour"]), int(r["arrived"])
        offsets = [k for k, count in enumerate(int(x) for x in r["by_offset"].split())
                   for _ in range(count)]
        for i in range(n):
            seen = hour + _HOUR * ((i + 0.5) / n)
            decided = None
            if i < len(offsets):
                k = offsets[i]
                decided = seen + (hour + _HOUR - seen) / 2 if k == 0 else hour + _HOUR * (k + 0.5)
            ads.append((len(ads) + 1, seen, decided))
    return ads


def _floor(at: _dt.datetime, hours: int) -> int:
    """_ARRIVAL_FLOOR_SQL's id at `at`: the newest ad first seen more than `hours` before."""
    cut = at - hours * _HOUR
    return max((i for i, seen, _ in _arrivals() if seen < cut), default=0)


def _window(at: _dt.datetime, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    """RT_NEW_LISTINGS_SQL's row at `at`; before the Prague seed no decision is on record."""
    after, cut = params["after_id"], at - _dt.timedelta(seconds=params["lag"])
    ads = [] if at < _SEED else [(i, seen) for i, seen, _ in _arrivals()
                                 if i > after and seen <= cut][: params["window"]]
    return [(ads[-1][0] if ads else after, len(ads), [i for i, _ in ads], [s for _, s in ads])]


def _fp_rows(at: _dt.datetime, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    """RT_FP_READ_SQL's rows at `at`: the asked ads decided by then (first decision, column 14)."""
    asked = set(params["ids"])
    return [(i, *([None] * 13), decided.timestamp(), None) for i, _, decided in _arrivals()
            if i in asked and decided is not None and decided < at]


# --- the replay ---------------------------------------------------------------------------------


def _answers(at: _dt.datetime) -> dict[str, Any]:
    """Every statement's rows at `at`, as the exports hold them."""
    settings = {LANE_INTERVAL_SETTING: _in_force(LANE_INTERVAL_SETTING, at),
                SCOPE_SETTING: _in_force(SCOPE_SETTING, at)}
    control: dict[str, Any] = {_FLAG: _SEED <= at < _BUILD_END, _SEED_KEY: SEED_VERSION,
                               _LANE_KEY: _lane_scope()}
    stamps: dict[str, _dt.datetime] = {}
    if at >= _SEED:
        stamps.update({_FLAG: _SEED if at < _BUILD_END else _BUILD_END, _SEED_KEY: _SEED})
    if at >= _ENGINE_SINCE:
        control[_ENGINE_KEY] = {"digest": "(not exported)", "since": _ENGINE_SINCE.isoformat()}
    commit = _last_commit(at)
    if commit is not None:
        stamps[CURSOR_NEW] = commit
    return {
        isql.RT_STORE_PRESENT_SQL: [(True,)],
        SETTING_SQL: lambda p: [] if settings.get(p["key"]) is None else [(settings[p["key"]],)],
        dd._HISTORY_SQL: functools.partial(_history_rows, at),
        LIVE_STREAM_SQL: lambda p: [(k, control[k]) for k in p["keys"] if k in control],
        dd._STAMPS_SQL: lambda p: [(k, stamps[k]) for k in [*p["settings"], *p["cursors"]]
                                   if k in stamps],
        isql.RT_CALIBRATION_PRESENT_SQL: [(dd.GENERATION, "(not exported)", _SEED)],
        isql.RT_LEASE_READ_SQL: [],
        dd._WORKER_SQL: [],
        dd._MERGES_SQL: functools.partial(_merges_rows, at),
        dd._RULINGS_SQL: [_rulings_row(at)],
        dd._BROKER_SUPPRESSION_SQL: [(0, 0, 0)],
        dd._DECISIONS_SQL: lambda p: [(_decided(p["since"], at),)],
        isql.RT_SCOPE_BACKLOG_SQL: [(_backlog(at),)],
        isql.RT_SCOPE_SCAN_SEEN_SQL: [(key,) for key in _walked(at)],
        dd._ARRIVAL_FLOOR_SQL: lambda p: [(_floor(at, p["hours"]),)],
        isql.RT_NEW_LISTINGS_SQL: functools.partial(_window, at),
        isql.RT_FP_READ_SQL: functools.partial(_fp_rows, at),
    }


@functools.cache
def _replay() -> tuple[Any, list[tuple[_dt.datetime, dict[str, Any]]]]:
    """09-22..10-19 09:00Z hourly: check_dedup on the exports, told through the ladder."""
    results = [(at, dd.check_dedup(_Conn(_answers(at), now=at), THRESHOLDS))
               for at in _ticks(_START, _END, _HOUR)]
    return _told(_KEY, 600, results, cls="alarm"), results


# --- the inputs ----------------------------------------------------------------------------------


def test_the_fixtures_add_up_to_the_totals_their_exports_read() -> None:
    """(a) 156,539 first decisions, (c) 1,735 arrivals with 152 undecided, (d) 8,319 + 51 groups."""
    # Each hour of (c) sums its offsets and its undecided to its arrivals; of the engine's groups
    # seven were split by an operator and none was taken back by the engine.
    assert sum(n for _, _, n in _decisions()) == 156_539
    rows = _fixture("health_dedup_arrivals.csv")
    assert all(sum(map(int, r["by_offset"].split())) + int(r["undecided"]) == int(r["arrived"])
               for r in rows)
    assert len(_arrivals()) == 1_735 and sum(d is None for _, _, d in _arrivals()) == 152
    groups = {s: sum(g for source, _, g, _, _ in _merge_hours() if source == s)
              for s in (MERGE_SOURCE, "operator")}
    assert groups == {MERGE_SOURCE: 8_319, "operator": 51}
    assert sorted({kind for _, _, kind, _ in _undone()}) == ["operator"]
    assert sum(g for _, _, _, g in _undone()) == 7


@pytest.mark.parametrize(("at", "n", "undecided"), [("2026-10-19T00:00:00Z", 392, 16),
                                                     ("2026-10-19T02:00:00Z", 401, 27),
                                                     ("2026-10-19T08:00:00Z", 409, 26),
                                                     ("2026-10-19T09:00:00Z", 421, 55)])
def test_the_replayed_cohort_is_the_one_the_store_reads_as_of_each_hour(at: str, n: int,
                                                                        undecided: int) -> None:
    """The store's own as-of readings of 10-19: 16 of 392, 27 of 401, 26 of 409 and 55 of 421."""
    # Read live on 10-19 from rt_fp's and listings' exact stamps; the replay places its ads inside
    # their hours and its cohort is still the store's. 09:00Z is the first hour no excuse holds.
    tick = _utc(at)
    window = _window(tick, {"after_id": _floor(tick, 25), "lag": 3600, "window": 50_000})[0]
    cohort = [i for i, seen in zip(window[2], window[3]) if seen >= tick - 24 * _HOUR]
    decided = {row[0] for row in _fp_rows(tick, {"ids": cohort})}
    assert (len(cohort), len(cohort) - len(decided)) == (n, undecided)


# --- the ladder over 28 days ---------------------------------------------------------------------


def test_in_28_days_the_ladder_opens_one_incident_the_abort_loop_of_10_19() -> None:
    """The first real incident the check sees: 10-19 09:00Z, no pass committed since 07:55:08Z."""
    # Every pass aborted on its pair budget (failed_passes 0), so at 09:00Z 55 of the 421 in-scope
    # ads first seen 1-24 h before had no decision. Nothing else opens in 28 days: fourteen brake
    # windows, the Prague build and its 52.3 h merge pause, the 10-18/19 sitting and the 851 merge
    # groups that followed it.
    ladder, _ = _replay()
    opened = _incidents(ladder, _KEY)
    assert [(i["subject"], i["first_seen_at"]) for i in opened] == [("decision_lag", _END)]
    assert opened[0]["resolved_at"] is None


def test_every_other_reading_past_ok_is_the_prague_builds_merge_pause() -> None:
    """28 days of every arm: Watch only for the build's merge pause, Problem only for the loop."""
    # The state and the stream never read past OK, and no tick reads a lease (none is on record).
    _, results = _replay()
    flagged = [(at, c["subject"], c["status"]) for at, r in results
               for c in r["details"]["cells"] if c["status"] in ("warn", "fail")]
    pause = _ticks(_utc("2026-10-18T17:00:00Z"), _utc("2026-10-18T20:00:00Z"), _HOUR)
    assert flagged == [*((at, "merge_pause", "warn") for at in pause),
                       (_END, "decision_lag", "fail")]
    assert {c["message"] for _, c in _arm(results, "lease")} == {"no lease on record"}


def test_the_state_is_off_exactly_while_the_brake_held_and_building_through_the_prague_build(
) -> None:
    """Off exactly while (e) holds the brake at 0, building from the Prague seed to 20:08:18Z."""
    # The brakes include 09-22..26 12:59Z, the unattended 22 h of 10-02/03, the sittings of
    # 10-14..16 and 10-18 21:41Z..10-19 02:41Z; merging or idle as the apply scope names an area or
    # not (none 09-26 12:59-14:48Z).
    _, results = _replay()
    states = {at: c["facts"]["state"] for at, c in _arm(results, "state")}
    off = {at for at in states if _in_force(LANE_INTERVAL_SETTING, at) <= 0}
    assert {at for at, state in states.items() if state == "off"} == off
    assert {at for at, state in states.items() if state == "building"} == {
        at for at in states if _SEED <= at < _BUILD_END and at not in off}
    assert {states[_utc(at)] for at in ("2026-09-26T13:00:00Z", "2026-09-26T14:00:00Z")} == {
        "idle"}
    for at in ("2026-09-26T15:00:00Z", "2026-10-18T21:00:00Z", "2026-10-19T03:00:00Z"):
        assert states[_utc(at)] == "merging", at
    assert {c["status"] for _, c in _arm(results, "state")} == {"ok"}


# --- the Prague build --------------------------------------------------------------------------


def test_the_prague_build_reads_building_and_its_arm_never_a_problem() -> None:
    """10-16 16:41Z..10-18 20:08Z: building, never Problem, 19 h of waiting for the walk."""
    # The brake lifted 13 min after the seed. Wave one (60 blocks, 60,378 ads) drained by 21h at
    # 6,659-16,275 decisions an hour; then the daily walk cap held the other 60 blocks back and the
    # backlog read 0 for 19 hours (the design's ~90/h crawl was arrivals); wave two (95,193 ads,
    # walked 10-17 16:28Z) drained at 1,771-7,654 an hour. Its largest ETA, 50.7 h at the second
    # walk, never neared the 7 d Watch line.
    _, results = _replay()
    building = [(at, c) for at, c in _arm(results, "build") if c["facts"]["state"] == "building"]
    assert [at for at, _ in building] == _ticks(_utc("2026-10-16T17:00:00Z"),
                                                _utc("2026-10-18T20:00:00Z"), _HOUR)
    assert {c["status"] for _, c in building} == {"ok"}
    waiting = [at for at, c in building
               if c["message"].startswith("waiting for the walk of 60 blocks; building since")]
    assert waiting == _ticks(_utc("2026-10-16T22:00:00Z"), _utc("2026-10-17T16:00:00Z"), _HOUR)
    worst = max((c for _, c in building), key=lambda c: c["facts"]["eta_h"] or 0)
    assert worst["facts"]["eta_h"] == pytest.approx(50.7, abs=0.1)
    assert worst["facts"]["window_start"] == _utc("2026-10-17T14:00:00Z").isoformat()
    assert (worst["facts"]["backlog"], worst["facts"]["blocks_walked"]) == (87_580, 120)
    assert worst["message"].endswith("at least 2.1 d to go (a lower bound)")
    first = dict(building)[_utc("2026-10-16T17:00:00Z")]
    assert first["facts"]["window_start"] == _utc("2026-10-16T16:41:28Z").isoformat()
    assert first["facts"]["decisions"] == 7_467


def test_the_merge_pause_reads_watch_from_48_h_into_the_build_until_the_flag_flips() -> None:
    """The 52.3 h pause: Watch 48.5-51.5 h into the build (10-18 17-20Z), OK at the flag's flip."""
    # The engine merged last at 10-16 16:0xZ and next at 10-18 20:17Z; the arm counts from the
    # build's start (16:28:01Z) and reads OK again at 21:00Z, the flag flipped (20:08:18Z) and 69
    # groups merged. Watch is never told, so the ladder opens nothing for it.
    _, results = _replay()
    pause = dict(_arm(results, "merge_pause"))
    watch = [at for at, c in pause.items() if c["status"] == "warn"]
    assert watch == _ticks(_utc("2026-10-18T17:00:00Z"), _utc("2026-10-18T20:00:00Z"), _HOUR)
    assert [pause[at]["value"] for at in watch] == [48.5, 49.5, 50.5, 51.5]
    assert all("since the build began" in pause[at]["message"] for at in watch)
    eve = pause[_utc("2026-10-18T16:00:00Z")]
    assert (eve["status"], eve["value"]) == ("ok", 47.5)
    after = pause[_utc("2026-10-18T21:00:00Z")]
    assert after["status"] == "ok" and after["facts"]["state"] == "merging"


# --- the decision lag -----------------------------------------------------------------------------


def test_the_decision_lag_is_not_measured_through_the_build_and_the_brakes() -> None:
    """Not measured while building, braked, or excused by a lift or a build's end with passes."""
    # Building 10-16 17:00Z..10-18 20:00Z, braked 22:00Z..10-19 02:00Z, excused at 10-18 21:00Z and
    # 10-19 03:00-08:00Z by the brake lift (02:41:38Z) and the build's end while passes still
    # committed; before the seed no decision is on record.
    _, results = _replay()
    lag = [(at, c) for at, c in _arm(results, "decision_lag") if _SEED <= at < _END]
    assert {c["status"] for _, c in lag} == {"unknown"}
    by_state: dict[str, list[_dt.datetime]] = {}
    for at, c in lag:
        by_state.setdefault(c["facts"]["state"], []).append(at)
    assert {state: len(ticks) for state, ticks in by_state.items()} == {
        "building": 52, "merging": 7, "off": 5}
    for at, c in lag:
        state = c["facts"]["state"]
        if state == "building":
            assert c["message"] == "Not measured: building"
        elif state == "off":
            assert c["message"] == "Not measured: the lane is off; a brake is not lag"
        else:
            assert c["message"].startswith("Not measured: a brake lifted "), at
            assert "a build ended" in c["message"] and c["facts"]["stall_s"] < 3600
    before = [c for at, c in _arm(results, "decision_lag") if at < _SEED]
    assert before and {c["status"] for c in before} == {"unknown"}


def test_the_10_19_abort_loop_reads_problem_once_no_pass_committed_for_an_hour() -> None:
    """10-19 09:00Z: 55 of 421 undecided (13.1 %, lower bound 10.2 %), no commit for 65 min."""
    # The engine changed at about 04:25Z (E966-E968) and 08:21:01Z (E969); from 04:30Z passes
    # committed 2-9 writes per ten minutes and after 07:55:08Z none. At 08:00Z the lift and the
    # build's end still excuse the lag; at 09:00Z the stall overrides every excuse: Problem.
    _, results = _replay()
    lag = dict(_arm(results, "decision_lag"))
    eight = lag[_utc("2026-10-19T08:00:00Z")]
    assert eight["status"] == "unknown" and eight["facts"]["stall_s"] == 300
    onset = lag[_END]
    assert onset["status"] == "fail" and onset["value"] == 13.06
    assert (onset["facts"]["n"], onset["facts"]["undecided"]) == (421, 55)
    assert onset["facts"]["lower_bound"] == pytest.approx(0.1018, abs=1e-4)
    assert onset["facts"]["stall_s"] == 3_900
    assert onset["facts"]["excused"] == ["a brake lifted 6.3 h ago", "a build ended 12.9 h ago",
                                         "the engine changed 39 min ago"]
    assert onset["message"].startswith("55 of 421 in-scope ads first seen 1-24 h ago have no "
                                       "decision (13.1%, lower bound 10.2%); the rest were")
    assert onset["message"].endswith("; no pass has committed a new ad for 65 min, so the brake, "
                                     "build and engine change of the last 24 h do not excuse it "
                                     "(Problem over 10%)")
    _, last = results[-1]
    assert last["status"] == "fail" and last["message"].startswith("1 Problem — decision_lag: ")


# --- the merges and the rulings ----------------------------------------------------------------


def test_the_splits_arm_stays_ok_for_28_days() -> None:
    """Seven engine groups split by an operator in 28 days: OK from 10-02 16:00Z, worst 6 of 487."""
    # Not measured until the cohort merged 7-14 d before holds 100 groups (a week after the first
    # merges, 09-25 15h). The worst reading, 6 of 487 (1.2 %, lower bound 0.6 %) at 10-10 14:00Z,
    # sits at a third of the 2 % Watch line; at the export 0 of the 149 groups merged 10-05..10-12
    # were split, and the engine took none back itself.
    _, results = _replay()
    splits = _arm(results, "splits")
    first = next(at for at, c in splits if c["status"] != "unknown")
    assert first == _utc("2026-10-02T16:00:00Z")
    assert {c["status"] for at, c in splits if at >= first} == {"ok"}
    worst_at, worst = max(((at, c) for at, c in splits if at >= first),
                          key=lambda reading: reading[1]["facts"]["lower_bound"])
    assert worst_at == _utc("2026-10-10T14:00:00Z")
    assert (worst["facts"]["split"], worst["facts"]["groups"]) == (6, 487)
    assert worst["facts"]["lower_bound"] == pytest.approx(0.0057, abs=1e-4)
    final = dict(splits)[_END]
    assert (final["facts"]["groups"], final["facts"]["split"], final["value"]) == (149, 0, 0.0)
    state = dict(_arm(results, "state"))[_END]
    assert (state["facts"]["merges_24h_engine"], state["facts"]["merges_7d_engine"],
            state["facts"]["merges_7d_operator"]) == (941, 6_693, 16)


def test_the_rulings_hold_at_zero_breaches_for_28_days() -> None:
    """246 pair rulings stood on 09-22 and 765 from 10-17; no breach, and no broker ruling ever."""
    # The rulings of 10-04..06 and 10-13..16 lie between; the engine merged no pair you ruled apart
    # after your ruling.
    _, results = _replay()
    ads = _arm(results, "rulings/ads")
    assert {(c["status"], c["value"]) for _, c in ads} == {("ok", 0)}
    assert (ads[0][1]["facts"]["negatives"], ads[-1][1]["facts"]["negatives"]) == (246, 765)
    brokers = _arm(results, "rulings/brokers")
    assert {(c["status"], c["value"]) for _, c in brokers} == {("ok", 0)}


# --- the lease (SYNTHETIC) ------------------------------------------------------------------------

# rt_lease keeps one row, so no store holds a strand: the 10-10 one is rebuilt from the morning's
# record. Made-up hostnames; the stranded pass's start second is the real one.
_OLD_HOST, _NEW_HOST = "0a1b2c3d4e5f", "6a7b8c9d0e1f"
_OLD_BOOT = _utc("2026-10-09T23:25:00Z")
_NEW_BOOT = _utc("2026-10-10T06:05:00Z")
_STRAND = (_utc("2026-10-10T05:55:49Z"), _utc("2026-10-10T05:55:56Z"))


def _pass(host: str, start: _dt.datetime, *, taken_s: float = 1, held_s: float = 360,
          ) -> tuple[str, _dt.datetime, _dt.datetime]:
    """A worker pass's lease, `<host>:<pid>:<the second it started>`: (holder, taken, ended)."""
    return (f"{host}:1:{int(start.timestamp())}", start + _dt.timedelta(seconds=taken_s),
            start + _dt.timedelta(seconds=held_s))


@functools.cache
def _leases() -> tuple[tuple[str, _dt.datetime, _dt.datetime], ...]:
    """The old worker's passes, the strand held to its TTL, the new worker's passes after it."""
    start, taken = _STRAND
    expiry = taken + _dt.timedelta(seconds=LEASE_TTL_S)
    return (*(_pass(_OLD_HOST, start - k * 10 * _MINUTE) for k in (2, 1)),
            _pass(_OLD_HOST, start, taken_s=(taken - start).total_seconds(),
                  held_s=(expiry - start).total_seconds()),
            *(_pass(_NEW_HOST, expiry + _MINUTE + k * 10 * _MINUTE) for k in range(4)))


def _strand_answers(at: _dt.datetime) -> dict[str, Any]:
    """The 28-day replay's world at `at`, its lease and worker heartbeat the strand's."""
    holder, taken, ended = next(lease for lease in reversed(_leases()) if lease[1] <= at)
    boot = _OLD_BOOT if at < _NEW_BOOT else _NEW_BOOT
    return {**_answers(at), isql.RT_LEASE_READ_SQL: [(holder, taken, ended, ended > at)],
            dd._WORKER_SQL: [(at - _dt.timedelta(seconds=10), boot)]}


def test_the_10_10_lease_strand_reads_watch_and_never_problem() -> None:
    """SYNTHETIC: the 10-10 strand, 39 min of a dead pass's lease, reads Watch and never Problem."""
    # A pass took the lease at 05:55:56Z, Postgres restarted at 05:57:12Z and the release died with
    # the pass, so the lease waited 39 min for its 40-min TTL (06:35:56Z); the 06:05Z deploy booted
    # the worker anew and the lane's next pass took the lease a minute after the expiry (that
    # morning the S1 seed did, at about 06:37Z). At 10-minute ticks: OK at 06:00Z (the dead pass is
    # the live process's, which no store tells from a running one), Watch at 06:10, 06:20 and
    # 06:30Z (a gone worker's pass, past the 120 s grace), OK from 06:40Z.
    ticks = _ticks(_utc("2026-10-10T05:40:00Z"), _utc("2026-10-10T07:10:00Z"), 10 * _MINUTE)
    results = [(at, dd.check_dedup(_Conn(_strand_answers(at), now=at), THRESHOLDS))
               for at in ticks]
    lease = _arm(results, "lease")
    watch = [at for at, c in lease if c["status"] == "warn"]
    assert watch == _ticks(_utc("2026-10-10T06:10:00Z"), _utc("2026-10-10T06:30:00Z"),
                           10 * _MINUTE)
    assert {c["status"] for at, c in lease if at not in watch} == {"ok"}
    readings = dict(lease)
    assert readings[_utc("2026-10-10T06:00:00Z")]["message"] == (
        "the worker's own pass holds the lease (36 min left)")
    assert readings[_utc("2026-10-10T06:10:00Z")]["message"].startswith(
        "a pass of a worker that is gone holds the lease (26 min left, taken 14 min ago), so the "
        "worker's passes wait (Watch while anything but the live worker holds it past 120 s")
    assert readings[_utc("2026-10-10T06:40:00Z")]["message"].startswith(
        "the worker's own pass holds the lease")
    assert {c["facts"]["state"] for _, c in lease} == {"merging"}
    assert _incidents(_told(_KEY, 600, results, cls="alarm"), _KEY) == []
