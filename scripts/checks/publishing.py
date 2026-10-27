"""Publishing: do discovered ads reach Browse once published, with their photos, and does Browse keep
holding what is servable? Per portal, `check_publishing` judges ingest (the oldest never-fetched
ad waiting in the detail queue), changes (the oldest repriced ad waiting), visible (new active ads
whose property is not in browse_list), stock (active properties missing from browse_list) and
photos (new ads' photos not stored, or stored narrow); for the fleet, stock, dirty (the oldest
property waiting for maintenance) and photo tags (stored photos CLIP tagged past the 45-min line).
Each not-in-Browse count is split by the stage that blocks it, judged on the property's location
ad and the publication rule as browse_projection judges them (a property not yet published is
expected, never a blocker). The portal list is imported and every statement runs on the
per-check clock; the module imports neither the harness nor the worker.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from location_data.claims_common import SOURCES, served_location_predicate
from scraper.db import QUEUE_PRIORITY_CHANGED, QUEUE_PRIORITY_FAILURE, QUEUE_PRIORITY_NEW
from scripts.checks.clock import fetchall, fetchone
from scripts.checks.common import (NEW_AD_H, ago, as_utc, cell, graded as _graded, iso, judged,
                                   now, result, wilson_lower)
from toolkit.publication import visible_since_sql

# A new ad gets its property within ~2 min, its location within ~10 min (p95 9.5 min) and its Browse
# row once published (settled, or at the 6 h cap -- the 'unpublished' stage below) and then at the next
# */15 rebuild, so one first written under 30 min ago is still on its way.
_VISIBLE_GRACE_MIN = 30
_WINDOW_H = NEW_AD_H
_WIDE_WINDOW_H = 72
# The images lane fetches a new ad's photos within minutes of its first write.
_PHOTOS_GRACE_H = 1

# The stages that keep an ad's property out of browse_list, in the order they are judged; the
# publication rule's hold and the rebuild's own lag are expected, facts not blockers (the jobs
# check judges the rebuild itself).
_STAGES = {
    "no_property": "no property",
    "property_not_active": "property not active",
    "location_not_served": "location not served",
    "not_in_browse_list": "not yet in Browse",
}
_UNPUBLISHED = "unpublished"
_WAITING = "waiting_for_rebuild"
_EXPECTED = {_UNPUBLISHED: "not yet published", _WAITING: "waiting for Browse's next rebuild"}

_REBUILT_CTE = """rebuilt as materialized (
    select max(last_succeeded_at) as at from derived_artifacts where name = 'browse_list')"""

# Judged on the property's LOCATION ad, as browse_projection is. A property the publication rule
# (toolkit/publication.py, migrations 668/669) had not published when the newest rebuild started
# is unpublished: still held, or released since, judged on that start, never on now(). A property
# born, or whose location ad's location was resolved, after that start cannot be in it yet:
# the resolver never patches browse_list, so such an ad waits for the next */15 rebuild.
_STAGE_TAIL = f"""
            when not {served_location_predicate("p.location_listing_ref_id")}
                then 'location_not_served'
            when {visible_since_sql("p")} > r.at then '{_UNPUBLISHED}'
            when p.created_at > r.at
                 or (select ll.resolved_at from listing_location ll
                      where ll.listing_id = p.location_listing_ref_id) > r.at
                then '{_WAITING}'
            else 'not_in_browse_list'"""

# Claimed or not: a claim whose drain died still ages by enqueued_at. A NEW-priority row whose
# ad is already held is a photo re-fetch of a known ad, never a missing one.
_QUEUE_SQL = """
select q.source, q.arm, count(*) as waiting, count(q.claimed_at) as claimed,
       min(q.enqueued_at) as oldest
  from (select d.source, d.claimed_at, d.enqueued_at,
               case when d.priority <> %(new)s then 'changes'
                    when exists (select 1 from listings l
                                  where l.source = d.source and l.source_id_native = d.native_id)
                        then 'known'
                    else 'ingest' end as arm
          from listing_detail_queue d
         where not d.given_up and d.priority = any(%(priorities)s::smallint[])) q
 group by q.source, q.arm
"""

# One short statement: browse_list's rebuild waits behind its readers. Driven per portal through
# listings_first_seen_source_idx (no skip scan), both windows read at once.
_VISIBLE_SQL = f"""
with {_REBUILT_CTE}
select a.source, a.first_seen_at >= now() - make_interval(hours => %(window_h)s::int) as recent,
       case when exists (select 1 from browse_list b where b.property_id = a.property_id)
                then 'visible'
            when a.property_id is null then 'no_property'
            when p.status <> 'active' then 'property_not_active'{_STAGE_TAIL}
       end as stage,
       count(*) as ads
  from listings a
  cross join rebuilt r
  left join properties p on p.id = a.property_id
 where a.source = any(%(sources)s::text[])
   and a.is_active
   and a.first_seen_at >= now() - make_interval(hours => %(wide_h)s::int)
   and a.first_seen_at < now() - make_interval(mins => %(grace_min)s::int)
 group by 1, 2, 3
"""

# One statement for every portal and the fleet. A portal's cell counts the properties holding an
# active ad of that portal; the fleet's counts every active property once.
_STOCK_SQL = f"""
with {_REBUILT_CTE},
missing as materialized (
    select p.id, case{_STAGE_TAIL}
           end as stage
      from properties p
      cross join rebuilt r
     where p.status = 'active' and p.is_active
       and not exists (select 1 from browse_list b where b.property_id = p.id)),
held as (
    select l.source, l.property_id
      from properties p
      join listings l on l.property_id = p.id and l.is_active
     where p.status = 'active' and p.is_active
     group by l.source, l.property_id)
select h.source, count(*) as properties,
       count(m.id) filter (where m.stage = 'location_not_served') as location_not_served,
       count(m.id) filter (where m.stage = 'not_in_browse_list') as not_in_browse_list,
       count(m.id) filter (where m.stage = '{_WAITING}') as waiting,
       count(m.id) filter (where m.stage = '{_UNPUBLISHED}') as unpublished
  from held h
  left join missing m on m.id = h.property_id
 group by h.source
union all
select null, (select count(*) from properties p where p.status = 'active' and p.is_active),
       count(*) filter (where stage = 'location_not_served'),
       count(*) filter (where stage = 'not_in_browse_list'),
       count(*) filter (where stage = '{_WAITING}'),
       count(*) filter (where stage = '{_UNPUBLISHED}')
  from missing
"""

# Not stored is storage_path NULL whatever the reason (pending, throttled, given up, a CDN that
# refuses our transform and lands as source_unavailable); only a taken-down ad's photos are out.
# The reason drops the URL a requests error carries.
_PHOTOS_SQL = """
with photo as (
    select a.source, a.id as listing_id, i.id as image_id, i.storage_path, i.stored_width,
           case when i.id is not null and i.storage_path is null then
               concat_ws(': ', i.unavailable_reason,
                         coalesce(left(regexp_replace(i.last_error, '\\s*for url:.*$', ''), 80),
                                  case when i.unavailable_reason is null
                                       then 'no attempt recorded' end))
           end as reason
      from listings a
      left join images i on i.listing_id = a.id
     where a.source = any(%(sources)s::text[])
       and a.first_seen_at >= now() - make_interval(hours => %(window_h)s::int)
       and a.first_seen_at < now() - make_interval(hours => %(grace_h)s::int)
       and i.unavailable_reason is distinct from 'listing_taken_down'),
top as (
    select distinct on (source) source, reason, count(*) as photos
      from photo
     where reason is not null
     group by source, reason
     order by source, count(*) desc, reason)
select p.source, count(distinct p.listing_id) as ads,
       count(distinct p.listing_id) filter (where p.image_id is null) as ads_without_photos,
       count(p.image_id) as photos,
       count(p.image_id) filter (where p.storage_path is null) as not_stored,
       count(p.image_id) filter (where p.storage_path is not null
                                   and p.stored_width is not null) as measured,
       count(p.image_id) filter (where p.storage_path is not null
                                   and p.stored_width < %(min_px)s::int) as narrow,
       t.reason, t.photos as reason_photos
  from photo p
  left join top t on t.source = p.source
 group by p.source, t.reason, t.photos
"""

_DIRTY_SQL = "select count(*), min(marked_at) from dirty_properties"

# The runner's clip job (#2032) and the model it vectors with (data/clip_taxonomy.json); a test
# pins all three.
_TAG_JOB, _TAG_RUNNER, _CLIP_MODEL = "clip", "job-runner", "openai/clip-vit-base-patch32"
_TAG_WINDOW_H = 24
# About 10 days of image ids at ~190k a day: the read stays a primary-key range, far below any
# photo stored today.
_TAG_ID_WINDOW = 2_000_000

# A photo stored (storage_path set; last_download_attempt_at is stamped with it) since the latest of
# a day ago, the clip row's runner join (never GitHub's era) and the end of its last pause (photos
# stored under a pause waited by design: the lift must open no incident), and more than the line
# ago. It is tagged once stamped AND vectored: a stamp with no vector (a terminal mark, an
# undecodable photo) is late for ever, and an untagged one ages to now(). greatest() skips NULL.
_TAGS_SQL = """
with job as (
    select registry_at, paused_until, paused_by, pause_note
      from scheduled_jobs
     where job_name = %(job)s and runner = %(runner)s
       and registry_at is not null and retired_at is null),
cohort as (
    select i.last_download_attempt_at as stored_at, i.clip_tagged_at is null as waiting,
           i.clip_tagged_at is not null and e.image_id is null as stamped_no_vector,
           extract(epoch from coalesce(case when e.image_id is not null then i.clip_tagged_at end,
                                       now()) - i.last_download_attempt_at) / 60.0 as lag_min
      from job j
      join images i
        on i.id > (select max(id) from images) - %(id_window)s::bigint
       and i.storage_path is not null
       and i.last_download_attempt_at >= greatest(now() - make_interval(hours => %(window_h)s::int),
                                                  j.registry_at,
                                                  case when j.paused_until <= now() then j.paused_until end)
       and i.last_download_attempt_at < now() - make_interval(mins => %(late_min)s::int)
      left join image_clip_embeddings e on e.image_id = i.id and e.model = %(model)s)
select j.registry_at, j.paused_until, j.paused_by, j.pause_note,
       a.photos, a.late, a.waiting, a.stamped_no_vector, a.p50_min, a.p90_min, a.p99_min,
       a.oldest_waiting
  from (select count(*) as photos,
               count(*) filter (where lag_min > %(late_min)s::int) as late,
               count(*) filter (where waiting) as waiting,
               count(*) filter (where stamped_no_vector) as stamped_no_vector,
               percentile_cont(0.5) within group (order by lag_min) as p50_min,
               percentile_cont(0.9) within group (order by lag_min) as p90_min,
               percentile_cont(0.99) within group (order by lag_min) as p99_min,
               min(stored_at) filter (where waiting) as oldest_waiting
          from cohort) a
  left join job j on true
"""


def _pct(share: float) -> str:
    return f"{share:.2%}"


def _stages(counts: dict[str, int]) -> str:
    return ", ".join(f"{counts[s]:,} {label}" for s, label in _STAGES.items() if counts.get(s))


def _expected(counts: dict[str, int]) -> str:
    return "".join(f"; {counts[s]:,} {label}" for s, label in _EXPECTED.items() if counts.get(s))


# --- ingest and changes -------------------------------------------------------------------


def _queue_cells(conn: Any, thresholds: dict[str, Any], at: _dt.datetime,
                 portals: list[str]) -> list[dict[str, Any]]:
    """Per portal, the oldest never-fetched ad and the oldest repriced ad still waiting."""
    params = {"new": QUEUE_PRIORITY_NEW,
              "priorities": [QUEUE_PRIORITY_NEW, QUEUE_PRIORITY_CHANGED, QUEUE_PRIORITY_FAILURE]}
    read = {(source, arm): (int(waiting), int(claimed), as_utc(oldest))
            for source, arm, waiting, claimed, oldest in fetchall(conn, _QUEUE_SQL, params)}
    warn_h, fail_h = (float(thresholds["publishing_queue_warn_h"]),
                      float(thresholds["publishing_queue_fail_h"]))
    cells = []
    for arm, what, wait in (("ingest", "new ad", "its first fetch"),
                            ("changes", "repriced ad", "its re-fetch")):
        for portal in portals:
            waiting, claimed, oldest = read.get((portal, arm), (0, 0, None))
            facts: dict[str, Any] = {"waiting": waiting, "claimed": claimed,
                                     "oldest_enqueued_at": iso(oldest)}
            if arm == "ingest":
                facts["known_ads_at_new_priority"] = read.get((portal, "known"), (0,))[0]
            if oldest is None:
                cells.append(cell(f"{portal}/{arm}", "ok", f"no {what} waiting for {wait}",
                                  value=0.0, unit="h", portal=portal, facts=facts))
                continue
            age = max((at - oldest).total_seconds(), 0.0)
            status = _graded(age / 3600, warn_h, fail_h)
            limit = {"fail": f" (Problem past {fail_h:g} h)",
                     "warn": f" (Watch past {warn_h:g} h)"}.get(status, "")
            cells.append(cell(
                f"{portal}/{arm}", status,
                f"the oldest {what} has waited {ago(age)} for {wait}{limit}: {waiting:,} waiting, "
                f"{claimed:,} claimed", value=round(age / 3600, 1), unit="h", portal=portal,
                facts=facts))
    return cells


# --- visible and stock --------------------------------------------------------------------


def _visible_cells(conn: Any, thresholds: dict[str, Any],
                   portals: list[str]) -> list[dict[str, Any]]:
    """Per portal, the share of new active ads whose property is not in browse_list."""
    params = {"sources": portals, "window_h": _WINDOW_H, "wide_h": _WIDE_WINDOW_H,
              "grace_min": _VISIBLE_GRACE_MIN}
    counts: dict[tuple[str, bool], dict[str, int]] = {}
    for source, recent, stage, ads in fetchall(conn, _VISIBLE_SQL, params):
        bucket = counts.setdefault((source, bool(recent)), {})
        bucket[stage] = bucket.get(stage, 0) + int(ads)
    min_n = int(thresholds["publishing_visible_min_n"])
    warn, fail = (float(thresholds["publishing_visible_warn"]),
                  float(thresholds["publishing_visible_fail"]))
    cells = []
    for portal in portals:
        recent = counts.get((portal, True), {})
        wide = dict(recent)
        for stage, ads in counts.get((portal, False), {}).items():
            wide[stage] = wide.get(stage, 0) + ads
        window, stages = next(((h, c) for h, c in ((_WINDOW_H, recent), (_WIDE_WINDOW_H, wide))
                               if sum(c.values()) >= min_n), (None, wide))
        n = sum(stages.values())
        facts: dict[str, Any] = {"window_h": window, "ads": n, "stages": stages}
        if window is None:
            cells.append(cell(f"{portal}/visible", "unknown", f"Not measured: {n} active ads "
                              f"first written {_VISIBLE_GRACE_MIN} min-{_WIDE_WINDOW_H} h ago, "
                              f"fewer than {min_n}", unit="%", portal=portal, facts=facts))
            continue
        blocked = sum(stages.get(s, 0) for s in _STAGES)
        lower = wilson_lower(blocked, n) or 0.0
        facts.update(blocked=blocked, lower_bound=round(lower, 4))
        cohort = f"active ads first written {_VISIBLE_GRACE_MIN} min-{window} h ago"
        expected = _expected(stages)
        if blocked:
            message = (f"{blocked:,} of {n:,} {cohort} are not in Browse ({_pct(blocked / n)}; "
                       f"lower bound {_pct(lower)}): {_stages(stages)}{expected}")
        else:
            message = f"none of {n:,} {cohort} is kept out of Browse{expected}"
        cells.append(cell(f"{portal}/visible", _graded(lower, warn, fail), message,
                          value=round(100 * blocked / n, 2), unit="%", portal=portal,
                          facts=facts))
    return cells


def _stock_cells(conn: Any, thresholds: dict[str, Any],
                 portals: list[str]) -> list[dict[str, Any]]:
    """Per portal and for the fleet, the share of active properties missing from browse_list."""
    read = {source: (int(n or 0), {"location_not_served": int(lns or 0),
                                   "not_in_browse_list": int(nib or 0),
                                   _WAITING: int(waiting or 0),
                                   _UNPUBLISHED: int(unpublished or 0)})
            for source, n, lns, nib, waiting, unpublished in fetchall(conn, _STOCK_SQL)}
    min_n = int(thresholds["publishing_stock_min_n"])
    warn, fail = (float(thresholds["publishing_stock_warn"]),
                  float(thresholds["publishing_stock_fail"]))
    cells = []
    for portal in [*portals, None]:
        subject = f"{portal}/stock" if portal else "fleet/stock"
        n, stages = read.get(portal, (0, {}))
        holding = (f"active properties holding an active ad of {portal}" if portal
                   else "active properties")
        if not n:
            cells.append(cell(subject, "unknown", f"Not measured: no {holding}", unit="%",
                              portal=portal))
            continue
        blocked = sum(stages.get(s, 0) for s in _STAGES)
        share, lower = blocked / n, wilson_lower(blocked, n) or 0.0
        facts = {"properties": n, "missing": blocked, "stages": stages,
                 "lower_bound": round(lower, 5)}
        expected = _expected(stages)
        missing = (f"{blocked:,} of {n:,} {holding} are missing from Browse ({_pct(share)}; "
                   f"lower bound {_pct(lower)}): {_stages(stages)}{expected}")
        conditions: list[tuple[str, str]] = []
        if n >= min_n and lower > fail:
            conditions.append(("fail", missing))
        elif share > warn:
            conditions.append(("warn", missing))
        ok = missing if blocked else f"none of {n:,} {holding} is missing from Browse{expected}"
        cells.append(judged(subject, conditions, ok, value=round(100 * share, 3), unit="%",
                            portal=portal, facts=facts))
    return cells


# --- photos -------------------------------------------------------------------------------


def _photo_cells(conn: Any, thresholds: dict[str, Any],
                 portals: list[str]) -> list[dict[str, Any]]:
    """Per portal, new ads' photos not stored, and stored narrower than the minimum width."""
    min_px = int(thresholds["publishing_photos_min_px"])
    params = {"sources": portals, "window_h": _WINDOW_H, "grace_h": _PHOTOS_GRACE_H,
              "min_px": min_px}
    read = {row[0]: row[1:] for row in fetchall(conn, _PHOTOS_SQL, params)}
    min_n = int(thresholds["publishing_photos_min_n"])
    warn, fail = (float(thresholds["publishing_photos_unstored_warn"]),
                  float(thresholds["publishing_photos_unstored_fail"]))
    narrow_warn, narrow_fail = (float(thresholds["publishing_photos_narrow_warn"]),
                                float(thresholds["publishing_photos_narrow_fail"]))
    cohort = f"ads first written {_PHOTOS_GRACE_H}-{_WINDOW_H} h ago"
    cells = []
    for portal in portals:
        ads, bare, photos, not_stored, measured, narrow, reason, reason_photos = (
            read.get(portal, (0, 0, 0, 0, 0, 0, None, None)))
        ads, bare, photos, not_stored, measured, narrow = (
            int(v or 0) for v in (ads, bare, photos, not_stored, measured, narrow))
        facts: dict[str, Any] = {"ads": ads, "ads_without_photos": bare, "photos": photos,
                                 "not_stored": not_stored, "stored_with_width": measured,
                                 "narrow": narrow, "top_reason": reason,
                                 "top_reason_photos": reason_photos}
        if photos < min_n:
            cells.append(cell(f"{portal}/photos", "unknown", f"Not measured: {photos} photos of "
                              f"{ads} {cohort}, fewer than {min_n}", unit="%", portal=portal,
                              facts=facts))
            continue
        share, lower = not_stored / photos, wilson_lower(not_stored, photos) or 0.0
        facts["lower_bound"] = round(lower, 4)
        top = f"; top reason: {reason} ({int(reason_photos or 0):,} photos)" if reason else ""
        conditions: list[tuple[str, str]] = []
        level = _graded(lower, warn, fail)
        if level != "ok":
            conditions.append((level, f"{not_stored:,} of {photos:,} photos of {cohort} are not "
                                      f"stored ({_pct(share)}; lower bound {_pct(lower)}){top}"))
        if measured >= min_n:
            narrow_share = narrow / measured
            facts["narrow_share"] = round(narrow_share, 4)
            level = _graded(narrow_share, narrow_warn, narrow_fail)
            if level != "ok":
                conditions.append((level, f"{narrow:,} of {measured:,} stored photos of {cohort} "
                                          f"are under {min_px} px wide ({_pct(narrow_share)}); "
                                          f"{not_stored:,} not stored ({_pct(share)}; lower bound "
                                          f"{_pct(lower)})"))
            narrow_ok = f"; {_pct(narrow_share)} stored under {min_px} px"
        else:
            facts["narrow_share"] = (f"Not measured: {measured} stored photos with a width, "
                                     f"fewer than {min_n}")
            narrow_ok = ""
        unstored_ok = (f"; {not_stored:,} are not ({_pct(share)}; lower bound {_pct(lower)})"
                       if not_stored else "")
        bare_ok = f"; {bare:,} of {ads:,} ads have no photo rows" if bare else ""
        ok = (f"{photos - not_stored:,} of {photos:,} photos of {cohort} are stored{unstored_ok}"
              f"{narrow_ok}{bare_ok}")
        cells.append(judged(f"{portal}/photos", conditions, ok, value=round(100 * share, 2),
                            unit="%", portal=portal, facts=facts))
    return cells


# --- the fleet's dirty queue ----------------------------------------------------------------


def _dirty_cell(conn: Any, thresholds: dict[str, Any], at: _dt.datetime) -> dict[str, Any]:
    """The oldest property waiting in dirty_properties for its maintenance and Browse row."""
    rows, oldest = fetchone(conn, _DIRTY_SQL)
    rows, oldest = int(rows or 0), as_utc(oldest)
    facts = {"dirty": rows, "oldest_marked_at": iso(oldest)}
    if oldest is None:
        return cell("fleet/dirty", "ok", "no property waiting for maintenance", value=0.0,
                    unit="h", facts=facts)
    age = max((at - oldest).total_seconds(), 0.0)
    warn_h, fail_h = (float(thresholds["publishing_dirty_warn_h"]),
                      float(thresholds["publishing_dirty_fail_h"]))
    status = _graded(age / 3600, warn_h, fail_h)
    why = {"fail": f" (Problem past {fail_h:g} h): property maintenance is not draining",
           "warn": f" (Watch past {warn_h:g} h)"}.get(status, "")
    return cell("fleet/dirty", status, f"the oldest property marked for maintenance has waited "
                f"{ago(age)}{why}; {rows:,} waiting", value=round(age / 3600, 1), unit="h",
                facts=facts)


# --- the fleet's photo tags -----------------------------------------------------------------


def _minutes(value: Any) -> float | None:
    return None if value is None else round(float(value), 1)


def _photo_tags_cell(conn: Any, thresholds: dict[str, Any], at: _dt.datetime) -> dict[str, Any]:
    """The share of stored photos CLIP tagged past the line, or not yet; at most Watch if paused."""
    late_min = int(thresholds["publishing_tag_late_min"])
    params = {"job": _TAG_JOB, "runner": _TAG_RUNNER, "model": _CLIP_MODEL,
              "id_window": _TAG_ID_WINDOW, "window_h": _TAG_WINDOW_H, "late_min": late_min}
    row = fetchone(conn, _TAGS_SQL, params) or (None,) * 12
    registry_at, paused_until = as_utc(row[0]), as_utc(row[1])
    paused_by, pause_note = row[2], row[3]
    photos, late, waiting, unvectored = (int(v or 0) for v in row[4:8])
    p50, p90, p99 = (_minutes(v) for v in row[8:11])
    oldest = as_utc(row[11])
    subject = "fleet/photo_tags"
    if registry_at is None:
        return cell(subject, "unknown", f"Not measured: {_TAG_JOB} is not on the job runner",
                    unit="%")
    day_back = at - _dt.timedelta(hours=_TAG_WINDOW_H)
    # A deliberate pause (the AC2 hold, a rollback brake) is finite and signed (migration 675): it
    # must open no incident, so it caps the cell at Watch, and once it ends the cohort starts afresh.
    paused = paused_until is not None and paused_until > at
    ended = paused_until if paused_until is not None and not paused else None
    since = max(t for t in (day_back, registry_at, ended) if t is not None)
    facts: dict[str, Any] = {
        "photos": photos, "late": late, "p50_min": p50, "p90_min": p90, "p99_min": p99,
        "waiting": waiting, "stamped_no_vector": unvectored,
        "oldest_waiting_stored_at": iso(oldest), "window_from": iso(since),
        "registry_at": iso(registry_at), "paused_until": iso(paused_until) if paused else None,
        "paused_by": paused_by if paused else None, "pause_note": pause_note if paused else None,
        "pause_ended_at": iso(ended)}
    if since == day_back:
        cohort = f"photos stored over {late_min} min and under {ago(_TAG_WINDOW_H * 3600)} ago"
    elif since == ended:
        cohort = (f"photos stored over {late_min} min ago since {_TAG_JOB}'s pause ended "
                  f"{ago((at - since).total_seconds())} ago")
    else:
        cohort = (f"photos stored over {late_min} min ago since {_TAG_JOB} joined the job runner "
                  f"{ago((at - since).total_seconds())} ago")
    pause = (f"paused by {paused_by} until {iso(paused_until)}: {pause_note}; "
             f"{waiting:,} photos waiting; " if paused else "")
    min_n = int(thresholds["publishing_tag_min_n"])
    if photos < min_n:
        return cell(subject, "unknown", f"Not measured: {pause}{photos:,} {cohort}, fewer than "
                    f"{min_n}", unit="%", facts=facts)
    share, lower = late / photos, wilson_lower(late, photos) or 0.0
    facts["lower_bound"] = round(lower, 4)
    warn, fail = (float(thresholds["publishing_tag_late_warn"]),
                  float(thresholds["publishing_tag_late_fail"]))
    level = _graded(lower, warn, fail)
    if paused and level == "fail":
        level = "warn"
    oldest_at = f", the oldest stored {ago((at - oldest).total_seconds())} ago" if oldest else ""
    stamped = f"; {unvectored:,} stamped with no vector" if unvectored else ""
    tail = (f": p50 {p50} min, p90 {p90} min, p99 {p99} min; {waiting:,} waiting{oldest_at}"
            f"{stamped}")
    if late:
        body = (f"{late:,} of {photos:,} {cohort} were tagged over {late_min} min after it, or "
                f"not yet ({_pct(share)}; lower bound {_pct(lower)}){tail}")
    else:
        body = f"all {photos:,} {cohort} were tagged within {late_min} min{tail}"
    conditions = [] if level == "ok" else [(level, f"{pause}{body}")]
    return judged(subject, conditions, f"{pause}{body}", value=round(100 * share, 2), unit="%",
                  facts=facts)


def check_publishing(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Do discovered ads reach Browse quickly, with their photos, and does Browse hold its stock?"""
    at = now(conn)
    portals = sorted(SOURCES)
    cells = [*_queue_cells(conn, thresholds, at, portals),
             *_visible_cells(conn, thresholds, portals),
             *_stock_cells(conn, thresholds, portals),
             *_photo_cells(conn, thresholds, portals),
             _dirty_cell(conn, thresholds, at),
             _photo_tags_cell(conn, thresholds, at)]
    return result("publishing", cells, {})
