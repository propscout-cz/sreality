"""The property rollup -- attach stragglers, then recompute each property from its adverts.

1. Attach stragglers: every `property_id IS NULL` listing (the batched detail-drain writes them)
   is born a bare singleton (`scraper.db.NEW_SINGLETONS_SQL`, the one birth path) and brought
   current by `properties_changed` in the same transaction. No cross-listing matching, ever (rule 15).
2. Recompute, ONE RULE PER FIELD (docs/design/merge-sprint/PROGRAM.md MS11). The canonical advert
   is rank 1 of `property_canonical_listings(property_id)` (migration 588, MS5: active, a map
   point, earliest first seen among active / latest last seen among inactive, trust, id). Every
   advert field is its own: price, area (no fallback), layout, category, source, both derived
   condition levels (rule 14, parked), furnished, and `repr_listing_ref_id`, through
   which the read models take description, photos, broker and link. The house type and the
   condition are each the value MOST adverts state, a tie going to the canonical order (#2574: the
   canonical advert alone decided, so a switch of it moved a property in and out of a filter with
   nothing changed on the market): the house type among the adverts of the canonical advert's
   category, the condition among the active adverts, or among all when none is active. The storey and the
   above-ground count are each the value MOST adverts state, a tie going to the advert first in
   the canonical order (#2552: 2,400 of 17,134 multi-advert flats held adverts that disagreed,
   and the canonical one decided alone). The location advert
   (`location_listing_ref_id`) is rank 1 of the same function's location order (migration 635:
   a map point, then determined foreign, then the finest rung, then the canonical order), set in
   the same statement as the canonical advert, so both are set or neither is; every read model
   takes a property's place from it. The six amenities are a union over every advert and the
   energy rating the best class any advert states (MS6); every other physical fact is the first
   non-empty value in the canonical order. The price
   figures are the canonical advert's lineage (MS10): its own and its same-portal predecessors'
   `listing_price_steps` plus one handover step per link, the total compounded. Lifecycle: any advert active, min/max seen, newest snapshot;
   the portal lists and one newest-advert date per offered portal (MS19). `repr_since` is
   stamped when the canonical advert changes, or when a property the full sweep reset to no ads
   gains one (the price alerts start there). `city_proximity_computed_at` is cleared when the
   LOCATION advert changes (a property reset to no ads gaining one included), so the hourly city
   job recomputes the figures from the location advert's point (an advert without one keeps the
   earlier figures until it gains one: the job reads only a point); the job's own UPDATE refuses
   a row whose location advert moved while it ran (migration 636). The broker book
   (`broker_book`, migration 692: one row per property and canonical broker) is stored from its
   one view beside every rollup, in the same transaction.
3. Settle (after the drain): stamp `settled_at` once on each property the pipeline is done with
   and patch its Browse row at once (`_settle_fresh`, migrations 668/747). The stamp IS publication
   (`toolkit/publication.py`). The incremental pass opens by queueing what is published and served
   but not yet in Browse (`_enqueue_published`: a place resolved after the stamp, a lost patch), so
   its drain patches it in. The full sweep, which holds the lease the pass needs, settles, queues
   and drains after each batch (`_drain_published`).

Batched by property-id range so each statement stays well under the
transaction-pooler statement timeout. autocommit=True means each batch
commits independently -- a timeout preserves completed batches.

Liveness (2026-08-06 incident): the maintenance lease is a SHORT (15 min) TTL
heartbeat-renewed every batch/slice — never a runtime-sized grant — so a
SIGKILL at any point freezes maintenance for minutes, not hours. The full
sweep also takes a --max-seconds wall-clock budget and CLEAN-STOPS at a batch
boundary when it runs out: finalize what was covered, save a resume cursor,
release the lease. The next run continues that CYCLE from the cursor instead
of id 1 (2026-10-03: four daily runs in a row stopped near id 540k of 927k,
each restarting at 1, so the tail went days without a reconcile). The first
stop of a cycle exits 0 with a warning; a resumed run that stops again, or a
run that stops before its first batch (nothing to resume), exits RED, a
`failed` run on its job cell rather than a kill at timeout_s. The jobs
check's `artifact/property_sweep` cell (scripts/checks/platform.py) watches
the completion stamp (derived_artifacts, migration 622) independently.

Two run modes (Phase 3 -- real-time properties):

  * --incremental (by hand; the realtime worker's maintenance lane runs it every 30 s): attach new stragglers + recompute
    ONLY the properties queued in `dirty_properties` by the writers + settle. O(changes).
  * full (default, daily reconcile, the job runner's property_sweep): attach (again after
    every batch) + recompute EVERY property (one cycle, resumed across runs when the budget
    cuts it) + reconcile childless + clear the queue. The self-healing backstop
    for anything the incremental pass missed.

Usage (typically via the jobs above):

    python -m scripts.recompute_property_stats --batch-size 2000       # full
    python -m scripts.recompute_property_stats --incremental            # dirty-set

Required env var: SUPABASE_DB_URL.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from location_data.claims_common import served_location_predicate
from scraper import db, job_lease
from toolkit.browse_read_model import sync_browse_list
from toolkit.filter_registry import PORTAL_OPTIONS
from toolkit.publication import CAP_SQL, published_predicate

LOG = logging.getLogger("recompute_property_stats")


def _sigterm_to_systemexit(signum: int, frame: Any) -> None:
    raise SystemExit(143)


# One attach handles at most this many births (a pass, or one turn of the full sweep's loop):
# every portal lands new rows NULL, so a backlog after a worker freeze must not birth,
# recompute and browse-sync everything in one transaction. No ORDER BY, so the planner keeps
# listings_property_id_idx; NEW_SINGLETONS_SQL sorts the ids anyway.
STRAGGLER_BATCH = 2000
_STRAGGLERS_SQL = "SELECT id FROM listings WHERE property_id IS NULL LIMIT %(limit)s"

# MS19: one date per portal Browse offers (`PORTAL_OPTIONS`; migrations 588 and 648 hold the columns), the
# first sighting of the property's newest advert there, active or not. A portal added to the
# registry fails tests/test_recompute_property_stats.py until a migration gives it its column.
# The statement below is an f-string that splices these in: a literal brace in it is doubled.
_NEWEST_AD_AT_AGG = "\n".join(
    f"        max(k.first_seen_at) FILTER (WHERE k.source = '{o.value}') AS newest_ad_at_{o.value},"
    for o in PORTAL_OPTIONS)

# What the rollup writes, column by column. The SET list and the guard that skips a row whose
# rollup is unchanged (no dead tuple, WAL or index entries for it) are rendered from this one dict,
# so they cannot drift; `stats_computed_at` is stamped beside them, so only when a row changes. The
# expressions read the stored row, as a SET does (the `p.` in `repr_since`, `city_proximity_computed_at`).
_ROLLUP = {
    "is_active": "r.is_active",
    "source_count": "r.source_count",
    "first_seen_at": "r.first_seen_at",
    "last_seen_at": "r.last_seen_at",
    "repr_listing_id": "c.sreality_id",
    "repr_since": "CASE WHEN p.repr_listing_ref_id <> c.id OR p.source_count = 0 THEN now() "
                  "ELSE p.repr_since END",
    "city_proximity_computed_at": "CASE WHEN p.location_listing_ref_id IS DISTINCT FROM la.id "
                                  "THEN NULL ELSE p.city_proximity_computed_at END",
    "repr_listing_ref_id": "c.id",
    "location_listing_ref_id": "la.id",
    "category_main": "c.category_main",
    "category_type": "c.category_type",
    "category_sub_cb": "c.category_sub_cb",
    "subtype": "sd.subtype",
    "disposition": "c.disposition",
    "area_m2": "c.area_m2",
    "current_price_czk": "c.price_czk",
    "condition": "sd.condition",
    "building_condition_level": "c.building_condition_level",
    "apartment_condition_level": "c.apartment_condition_level",
    "furnished": "c.furnished",
    "source": "c.source",
    "all_sources": "r.all_sources",
    "active_sources": "r.active_sources",
    "has_lift": "r.has_lift",
    "has_balcony": "r.has_balcony",
    "has_parking": "r.has_parking",
    "terrace": "r.terrace",
    "garage": "r.garage",
    "cellar": "r.cellar",
    "usable_area": "r.usable_area",
    "estate_area": "r.estate_area",
    "garden_area": "r.garden_area",
    "parking_lots": "r.parking_lots",
    "building_type": "r.building_type",
    "ownership": "r.ownership",
    "energy_rating": "r.energy_rating",
    "floor": "sy.floor",
    "total_floors": "sy.total_floors",
    "price_drop_count": "coalesce(ph.drops, 0)",
    "price_rise_count": "coalesce(ph.rises, 0)",
    "max_price_drop_pct": "ph.max_drop_pct",
    "price_change_count": "coalesce(ph.changes, 0)",
    "price_change_count_30d": "coalesce(ph.changes_30d, 0)",
    "price_change_count_90d": "coalesce(ph.changes_90d, 0)",
    "price_change_count_365d": "coalesce(ph.changes_365d, 0)",
    "total_price_change_pct": "CASE WHEN cs.price_points >= 2 AND cs.first_price > 0 "
                              "THEN (cs.last_price - cs.first_price)::numeric / cs.first_price * 100 END",
    "last_change_at": "coalesce(ch.last_change_at, r.first_seen_at)",
    **{f"newest_ad_at_{o.value}": f"r.newest_ad_at_{o.value}" for o in PORTAL_OPTIONS},
}
_ROLLUP_SET = "\n".join(f"      {column} = {value}," for column, value in _ROLLUP.items())
_ROLLUP_CHANGED = (f"({', '.join(f'p.{column}' for column in _ROLLUP)})\n"
                   f"        IS DISTINCT FROM ({', '.join(_ROLLUP.values())})")

_RECOMPUTE_BATCH_SQL = f"""
    WITH batch AS (
      SELECT id FROM properties WHERE id >= %(lo)s AND id < %(hi)s
    ),
    -- Every advert of the batch's properties in THE canonical order (migration 588, MS5); rank 1
    -- is the canonical advert, and location rank 1 the location advert (migration 635). Both
    -- orders are spelled there and nowhere else.
    kids AS (
      SELECT l.*, o.canonical_rank, o.location_rank
      FROM batch b
      CROSS JOIN LATERAL property_canonical_listings(b.id) o
      JOIN listings l ON l.id = o.listing_id
    ),
    canon AS (
      SELECT * FROM kids WHERE canonical_rank = 1
    ),
    loc AS (
      SELECT * FROM kids WHERE location_rank = 1
    ),
    -- Lifecycle and the portals over every advert, active or not; the six amenities are a union
    -- (yes when any advert says yes) and the energy rating the best class any advert states
    -- (MS6); every other physical fact is the first non-empty value in the canonical order.
    child_agg AS (
      SELECT
        k.property_id              AS pid,
        bool_or(k.is_active)       AS is_active,
        count(*)                   AS source_count,
        min(k.first_seen_at)       AS first_seen_at,
        max(k.last_seen_at)        AS last_seen_at,
        array_agg(DISTINCT k.source ORDER BY k.source) AS all_sources,
        coalesce(array_agg(DISTINCT k.source ORDER BY k.source) FILTER (WHERE k.is_active),
                 ARRAY[]::text[]) AS active_sources,
{_NEWEST_AD_AT_AGG}
        bool_or(k.has_lift)        AS has_lift,
        bool_or(k.has_balcony)     AS has_balcony,
        bool_or(k.has_parking)     AS has_parking,
        bool_or(k.terrace)         AS terrace,
        bool_or(k.garage)          AS garage,
        bool_or(k.cellar)          AS cellar,
        (array_agg(k.usable_area ORDER BY k.canonical_rank) FILTER (WHERE k.usable_area IS NOT NULL))[1] AS usable_area,
        (array_agg(k.estate_area ORDER BY k.canonical_rank) FILTER (WHERE k.estate_area IS NOT NULL))[1] AS estate_area,
        (array_agg(k.garden_area ORDER BY k.canonical_rank) FILTER (WHERE k.garden_area IS NOT NULL))[1] AS garden_area,
        (array_agg(k.parking_lots ORDER BY k.canonical_rank) FILTER (WHERE k.parking_lots IS NOT NULL))[1] AS parking_lots,
        (array_agg(k.building_type ORDER BY k.canonical_rank) FILTER (WHERE k.building_type IS NOT NULL))[1] AS building_type,
        (array_agg(k.ownership ORDER BY k.canonical_rank) FILTER (WHERE k.ownership IS NOT NULL))[1] AS ownership,
        min(k.energy_rating)       AS energy_rating
      FROM kids k
      GROUP BY k.property_id
    ),
    -- The storey and the above-ground count: the value most adverts state, a tie going to the
    -- advert first in the canonical order.
    storey AS (
      SELECT v.pid,
        (array_agg(v.value ORDER BY v.ads DESC, v.first_rank) FILTER (WHERE v.fact = 'floor'))[1] AS floor,
        (array_agg(v.value ORDER BY v.ads DESC, v.first_rank) FILTER (WHERE v.fact = 'total'))[1] AS total_floors
      FROM (
        SELECT k.property_id AS pid, f.fact, f.value,
               count(*) AS ads, min(k.canonical_rank) AS first_rank
        FROM kids k
        CROSS JOIN LATERAL (VALUES ('floor', k.floor), ('total', k.total_floors)) f(fact, value)
        WHERE f.value IS NOT NULL
        GROUP BY k.property_id, f.fact, f.value
      ) v
      GROUP BY v.pid
    ),
    -- The house type and the condition: the value most adverts state, a tie going to the advert
    -- first in the canonical order. A house type belongs to one category, so only the adverts of
    -- the canonical advert's category vote; for the condition the active adverts vote, and every
    -- advert only when no active one states it.
    stated AS (
      SELECT v.pid,
        (array_agg(v.value ORDER BY v.ads DESC, v.first_rank) FILTER (WHERE v.fact = 'subtype'))[1] AS subtype,
        (array_agg(v.value ORDER BY v.live DESC, CASE WHEN v.live > 0 THEN 0 ELSE v.ads END DESC, v.first_rank)
           FILTER (WHERE v.fact = 'condition'))[1] AS condition
      FROM (
        SELECT k.property_id AS pid, f.fact, f.value, count(*) AS ads,
               count(*) FILTER (WHERE k.is_active) AS live, min(k.canonical_rank) AS first_rank
        FROM kids k
        JOIN canon c ON c.property_id = k.property_id
        CROSS JOIN LATERAL (VALUES
          ('subtype', CASE WHEN k.category_main IS NOT DISTINCT FROM c.category_main THEN k.subtype END),
          ('condition', k.condition)) f(fact, value)
        WHERE f.value IS NOT NULL
        GROUP BY k.property_id, f.fact, f.value
      ) v
      GROUP BY v.pid
    ),
    -- The canonical advert and its same-portal predecessors (MS10): each predecessor is the advert
    -- on that portal last seen latest, strictly before the later link was first seen, so adverts
    -- that ran at the same time never link; first_seen_at falls along the chain, so it ends.
    lineage AS (
      WITH RECURSIVE link AS (
        SELECT c.property_id AS pid, c.id, c.source, c.first_seen_at, 0 AS hop FROM canon c
        UNION ALL
        SELECT k.pid, pre.id, k.source, pre.first_seen_at, k.hop + 1
        FROM link k
        CROSS JOIN LATERAL (
          SELECT l.id, l.first_seen_at FROM listings l
          WHERE l.property_id = k.pid AND l.source = k.source
            AND l.last_seen_at < k.first_seen_at AND l.first_seen_at < k.first_seen_at
          ORDER BY l.last_seen_at DESC, l.id DESC
          LIMIT 1
        ) pre
      )
      SELECT * FROM link
    ),
    -- Each priced link's first and last price over its own snapshots, dated by the first.
    members AS (
      SELECT g.pid, g.hop,
        (array_agg(s.price_czk ORDER BY s.scraped_at, s.id))[1]           AS first_price,
        min(s.scraped_at)                                                 AS first_at,
        (array_agg(s.price_czk ORDER BY s.scraped_at DESC, s.id DESC))[1] AS last_price,
        count(*)                                                          AS price_points
      FROM lineage g
      JOIN listing_snapshots s ON s.listing_id = g.id
      WHERE s.price_czk IS NOT NULL
      GROUP BY g.pid, g.hop
    ),
    -- The steps the price figures count: each link's OWN steps (`listing_price_steps`, migration
    -- 559, the one step definition the alerts read too) plus one handover step into each priced
    -- link from the last price of the next older priced link, dated at the newer link's first.
    steps AS (
      SELECT g.pid, ps.scraped_at, ps.price_czk, ps.prev_price_czk
      FROM lineage g
      JOIN listing_price_steps ps ON ps.listing_id = g.id
      UNION ALL
      SELECT h.pid, h.first_at, h.first_price, h.prev_price_czk
      FROM (SELECT m.pid, m.first_at, m.first_price,
                   lead(m.last_price) OVER (PARTITION BY m.pid ORDER BY m.hop) AS prev_price_czk
            FROM members m) h
      WHERE h.first_price <> h.prev_price_czk
    ),
    -- Each step dated by its own scraped_at. The windowed counts decay as events age out, so they
    -- are only as fresh as the last recompute of the row -- the daily full sweep is the bound.
    price_hist AS (
      SELECT
        ps.pid,
        count(*) FILTER (WHERE ps.price_czk < ps.prev_price_czk) AS drops,
        count(*) FILTER (WHERE ps.price_czk > ps.prev_price_czk) AS rises,
        count(*)                                                 AS changes,
        count(*) FILTER (WHERE ps.scraped_at >= now() - interval '30 days')  AS changes_30d,
        count(*) FILTER (WHERE ps.scraped_at >= now() - interval '90 days')  AS changes_90d,
        count(*) FILTER (WHERE ps.scraped_at >= now() - interval '365 days') AS changes_365d,
        max((ps.prev_price_czk - ps.price_czk)::numeric / ps.prev_price_czk * 100)
          FILTER (WHERE ps.price_czk < ps.prev_price_czk)        AS max_drop_pct
      FROM steps ps
      GROUP BY ps.pid
    ),
    -- The headline delta, compounded over those steps: it telescopes to the oldest priced link's
    -- first price against the canonical advert's last. (No literal percent sign in this comment on
    -- purpose -- prose percent inside executed SQL is an `incomplete placeholder` crash in
    -- psycopg; tests/test_sql_placeholders.py guards it.) NULL under two priced snapshots or with
    -- an unpriced canonical advert.
    lineage_span AS (
      SELECT pid,
        (array_agg(first_price ORDER BY hop DESC))[1]     AS first_price,
        (array_agg(last_price) FILTER (WHERE hop = 0))[1] AS last_price,
        sum(price_points)                                 AS price_points
      FROM members
      GROUP BY pid
    ),
    -- Last content change = the newest snapshot of any advert (snapshots are content-change
    -- only, rule 2): the "recently changed" timestamp Browse filters on (migration 158).
    changes AS (
      SELECT l.property_id AS pid, max(s.scraped_at) AS last_change_at
      FROM listing_snapshots s
      JOIN listings l ON l.id = s.listing_id
      JOIN batch b ON b.id = l.property_id
      GROUP BY l.property_id
    )
    UPDATE properties p SET
{_ROLLUP_SET}
      stats_computed_at   = now()
    FROM child_agg r
    JOIN canon c ON c.property_id = r.pid
    JOIN loc la ON la.property_id = r.pid
    LEFT JOIN storey sy ON sy.pid = r.pid
    LEFT JOIN stated sd ON sd.pid = r.pid
    LEFT JOIN price_hist ph ON ph.pid = r.pid
    LEFT JOIN lineage_span cs ON cs.pid = r.pid
    LEFT JOIN changes ch ON ch.pid = r.pid
    WHERE p.id = r.pid
      AND {_ROLLUP_CHANGED}
"""

# Dirty-set recompute (Phase 3), derived from the batch SQL by scoping its `batch` CTE
# to an explicit id array instead of an id range, so `properties_changed` (the drain,
# the identity writers, straggler-attach) recomputes exactly its properties with the
# identical body: deriving it, never re-writing it, means it cannot drift from full.
_RECOMPUTE_SCOPED_SQL = _RECOMPUTE_BATCH_SQL.replace(
    "SELECT id FROM properties WHERE id >= %(lo)s AND id < %(hi)s",
    "SELECT id FROM properties WHERE id = ANY(%(ids)s)",
)

# The broker book (migration 692): the view's rows of the batch's properties, stored. The view
# is the one spelling of a row; the scope is a qual on its grouping column, so it applies below
# the aggregate. A row the view no longer holds (its ads moved, re-attributed or re-pointed to
# another broker) is deleted, and only a row that differs is rewritten, so a quiescent batch
# writes nothing. Scoped exactly like the rollup above, and derived from its range form the same way.
_BROKER_BOOK_BATCH_SQL = """
    WITH spells AS (
      SELECT property_id, broker_id, is_active, all_sources, active_sources,
             started_at, ended_at, tom_observed
      FROM broker_book_projection
      WHERE property_id >= %(lo)s AND property_id < %(hi)s
    ), gone AS (
      DELETE FROM broker_book b
      WHERE property_id >= %(lo)s AND property_id < %(hi)s
        AND NOT EXISTS (SELECT 1 FROM spells v WHERE v.property_id = b.property_id
                          AND v.broker_id = b.broker_id)
    )
    INSERT INTO broker_book AS b
      (property_id, broker_id, is_active, all_sources, active_sources,
       started_at, ended_at, tom_observed)
    SELECT * FROM spells
    ORDER BY property_id, broker_id
    ON CONFLICT (property_id, broker_id) DO UPDATE
      SET is_active = EXCLUDED.is_active, all_sources = EXCLUDED.all_sources,
          active_sources = EXCLUDED.active_sources, started_at = EXCLUDED.started_at,
          ended_at = EXCLUDED.ended_at, tom_observed = EXCLUDED.tom_observed
      WHERE (b.is_active, b.all_sources, b.active_sources, b.started_at, b.ended_at,
             b.tom_observed)
            IS DISTINCT FROM (EXCLUDED.is_active, EXCLUDED.all_sources, EXCLUDED.active_sources,
                              EXCLUDED.started_at, EXCLUDED.ended_at, EXCLUDED.tom_observed)
"""
_BROKER_BOOK_SCOPED_SQL = _BROKER_BOOK_BATCH_SQL.replace(
    "property_id >= %(lo)s AND property_id < %(hi)s", "property_id = ANY(%(ids)s)")

# Claim a marked_at-ordered slice of the dirty queue, but only rows dirtied at or
# before a run-start cutoff. A property re-dirtied DURING the run gets a fresh
# marked_at (via the writers' ON CONFLICT DO UPDATE), so it is not deleted below --
# it survives, claimed again by this run if the stamp is at or before the cutoff,
# else by the next pass. A stamp is never later than the statement writing it, so only
# a transaction begun before the cutoff can stamp at or before it: the working set is
# finite and the drain loop always terminates.
_CLAIM_DIRTY_SQL = """
    SELECT property_id, marked_at FROM dirty_properties
    WHERE marked_at <= %(cutoff)s
    ORDER BY marked_at
    LIMIT %(limit)s
"""

# Dequeue a claimed row only while it carries the mark it was claimed with (compare-and-set):
# any re-stamp since the claim survives. Not `marked_at <= cutoff`: a writer whose transaction
# began before the cutoff but committed after the slice read its adverts re-stamps with its
# START, at or before the cutoff -- the dequeue would wait on its row lock, re-check the new
# stamp and drop it, leaving the property recomputed from the state before that write until the
# daily sweep (an attribution chunk is one transaction over every portal: resolve_brokers).
_DELETE_DIRTY_SQL = """
    DELETE FROM dirty_properties d
    USING unnest(%(ids)s::bigint[], %(marks)s::timestamptz[]) AS c(property_id, marked_at)
    WHERE d.property_id = c.property_id AND d.marked_at = c.marked_at
"""

# The full sweep's one dirty clear: rows queued at or before the run's start (anything dirtied
# mid-run survives for the next pass) in the id range THIS run recomputed. Never wider: ids
# above a budget stop were not recomputed, and ids below a resumed run's start were recomputed
# by an earlier run and may be dirty again -- clearing either would erase their recompute
# signal until the next cycle instead of the next incremental pass minutes later. A complete
# walk from id 1 passes [1, max_id + 1), which is the old global pre-cutoff delete for every
# property it walked: the FK makes each queued row name a property, ids are bigserial from 1
# and never deleted, and max_id is read after the cutoff. The one row the global delete also
# dropped was dirt stamped before the cutoff on a property born after max_id was read -- one
# the walk never recomputed, so keeping it is the fix, not a loss.
_CLEAR_DIRTY_SWEPT_SQL = (
    "DELETE FROM dirty_properties "
    "WHERE marked_at <= %(cutoff)s AND property_id >= %(lo)s AND property_id < %(hi)s"
)

# The batch statement starts from the ads, so a property whose ads all moved away keeps its
# last recompute, canonical ad included. A merge retires its loser (nothing serves a merged-away
# row); any other childless property is reset to "no ads". Its last advert facts stay for its
# page; with no canonical ad, the consumer rule (rule 25, read off that ad's location) keeps it
# out of Browse, the map and the Watchdog. Only a row that still differs is written, so the
# count is what this run reset. Only this reset writes `source_count` 0: the batch statement
# reads it to stamp the handover when the property gains an ad again (a NULL handle compares
# to nothing), and the R2 parity check skips it (both handles NULL on purpose).
_NO_ADS = {
    "is_active": "false", "repr_listing_ref_id": "NULL", "repr_listing_id": "NULL",
    "location_listing_ref_id": "NULL",
    "source_count": "0", "all_sources": "ARRAY[]::text[]", "active_sources": "ARRAY[]::text[]",
    **{f"newest_ad_at_{o.value}": "NULL" for o in PORTAL_OPTIONS},
}
_RECONCILE_CHILDLESS_SQL = f"""
    UPDATE properties p SET ({', '.join(_NO_ADS)}) = ({', '.join(_NO_ADS.values())})
    WHERE p.status = 'active'
      AND NOT EXISTS (SELECT 1 FROM listings l WHERE l.property_id = p.id)
      AND ({', '.join(f'p.{c}' for c in _NO_ADS)}) IS DISTINCT FROM ({', '.join(_NO_ADS.values())})
"""

# The resume cursor (2026-10-03: four daily runs in a row stopped on budget near id 540k of
# 927k and each restarted at id 1, so the id tail went days without a reconcile). A budget stop
# saves where the next run continues the cycle; the run that completes it deletes the row in
# the stamp's transaction. A cycle that began more than _CURSOR_MAX_AGE ago starts over at id 1
# instead, or the stamp would vouch for a reconcile whose lower half is days old. The age is
# judged when a run resumes, not when it stamps, so a stamp's oldest recompute can be
# _CURSOR_MAX_AGE plus that run's own length (~2h: budget + one in-flight batch) older than
# the stamp: the artifact's complete_through is the stamp time, not the cycle's start.
_CURSOR_MAX_AGE = "36 hours"

_READ_SWEEP_CURSOR_SQL = """
    SELECT (value->>'next_lo')::bigint, (value->>'runs')::int, value->>'cycle_started_at',
           (value->>'cycle_started_at')::timestamptz > now() - %(max_age)s::interval
      FROM app_settings WHERE key = 'property_sweep_cursor'
"""

_SAVE_SWEEP_CURSOR_SQL = """
    INSERT INTO app_settings (key, value, updated_by)
    VALUES ('property_sweep_cursor',
            jsonb_build_object(
                'next_lo', %(next_lo)s::bigint,
                'cycle_started_at', %(cycle_started_at)s::timestamptz,
                'runs', %(runs)s::int),
            'recompute_property_stats')
    ON CONFLICT (key) DO UPDATE
      SET value = excluded.value, updated_at = now(),
          updated_by = excluded.updated_by
"""

_DELETE_SWEEP_CURSOR_SQL = "DELETE FROM app_settings WHERE key = 'property_sweep_cursor'"


def properties_changed(conn: Any, property_ids: Iterable[int]) -> None:
    """Make what is derived from these properties true now, inside the caller's transaction: the
    rollup and the broker book (the sweep's own bodies, scoped) and their Browse rows. THE
    after-step of the dirty drain, of both
    identity writers (`toolkit.property_identity`) and of every birth (`_attach_stragglers`, under
    `_bounded`); it opens no transaction, sets no ceiling and never enqueues
    `dirty_properties` (the drain calls it on ids it is about to dequeue).

    The order is load-bearing: `browse_projection` reads the recomputed `properties` row, and the
    book selects adverts by their property AFTER the writer moved them (an identity writer
    passes both sides, so the side an advert left loses its book row). The Browse
    patch is best-effort (`sync_browse_list` unwinds only itself) and a fast path, not a guarantee:
    without it a recompute waited a measured mean 11.7 min (worst 36.6) for the then */15 wholesale
    rebuild (hourly since migration 740), and a patch committed while a rebuild is in flight (61.9 s
    of every 900 s, measured 2026-10-19) lands on the doomed table: since migration 630 the rebuild
    re-patches identity changes before its swap, and any other patch is superseded until the next
    rebuild (a new property's until the next pass's `_enqueue_published`). A full
    drain slice (2000 ids, only after a backlog;
    the live depth is a couple of dozen) costs ~100-270 ms of ROW EXCLUSIVE on `browse_list`, the
    lock the rebuild's drop waits behind. That is the uncontended cost. The rebuild's drop runs
    with lock_timeout 0, so it queues behind any in-flight `browse_list` reader (up to 120 s on
    the API role), and the patch queues behind it while the caller still holds the row locks of
    the properties its rollup changed; a merge or split of one of them waits too. The drain bounds that wait
    (`_DRAIN_LOCK_TIMEOUT`); the identity writers inherit their caller's lock_timeout.
    """
    ids = sorted({int(p) for p in property_ids if p is not None})
    if not ids:
        return
    with conn.cursor() as cur:
        cur.execute(_RECOMPUTE_SCOPED_SQL, {"ids": ids})
        cur.execute(_BROKER_BOOK_SCOPED_SQL, {"ids": ids})
    sync_browse_list(conn, ids)


@contextmanager
def _bounded(conn: Any) -> Iterator[None]:
    """A transaction under the batch ceiling (SET LOCAL no-ops in autocommit)."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = '{_BATCH_STATEMENT_TIMEOUT}'")
        yield


def _recompute_batch(conn: Any, lo: int, hi: int, hold: job_lease.Hold | None = None) -> int:
    """One sweep batch, [lo, hi), in its own `_bounded` transaction, so each batch still commits
    independently (a killed sweep keeps every finished batch): the rollup, then the broker book
    under `_BOOK_STATEMENT_TIMEOUT`; returns the properties the rollup wrote."""
    params = {"lo": lo, "hi": hi}
    with _bounded(conn), conn.cursor() as cur:
        cur.execute(_RECOMPUTE_BATCH_SQL, params)
        recomputed = cur.rowcount or 0
        cur.execute(f"SET LOCAL statement_timeout = '{_BOOK_STATEMENT_TIMEOUT}'")
        cur.execute(_BROKER_BOOK_BATCH_SQL, params)
        _fence(conn, hold)
        return recomputed


def _reconcile_childless(conn: Any) -> int:
    with conn.cursor() as cur:
        cur.execute(_RECONCILE_CHILDLESS_SQL)
        return cur.rowcount or 0


def _batch_ranges(max_id: int, batch_size: int, start: int = 1) -> Iterator[tuple[int, int]]:
    """Yield half-open [lo, hi) id ranges covering start..max_id inclusive."""
    if max_id < start or batch_size < 1:
        return
    for lo in range(start, max_id + 1, batch_size):
        yield lo, lo + batch_size


def _attach_stragglers(conn: Any, limit: int = STRAGGLER_BATCH, hold: job_lease.Hold | None = None) -> int:
    """Give up to `limit` property_id-NULL listings their own singleton, brought current at birth
    by `properties_changed`. No cross-listing matching happens here, ever (CLAUDE.md rule 15)."""
    # Birth, link and after-step in ONE transaction: a replay after a failure (db.
    # run_resilient replays this op) finds the stragglers still unlinked, never a linked bare
    # row that Browse could show before its recompute. The browse patch is best-effort in its
    # own savepoint (sync_browse_list), so it can never abort the birth.
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(_STRAGGLERS_SQL, {"limit": limit})
            stragglers = [int(r[0]) for r in cur.fetchall()]
        born = db.create_singleton_properties(conn, stragglers) if stragglers else []
        if born:
            with _bounded(conn):
                properties_changed(conn, born)
            _fence(conn, hold)
    return len(born)


# The drain slice's ceiling on any one lock wait. Its Browse patch can queue behind a rebuild's
# swap, holding the row locks of the slice's properties its rollup changed (properties_changed's docstring),
# and the slice is mostly recently-dirtied properties — the ones the autodedup lane (5 s
# lock_timeout) and a split (5 s, busy 409) are likeliest to touch. A patch that times out
# unwinds only its savepoint and the next rebuild carries the row; a recompute that times out
# rolls the slice back and the next pass replays it. The drain, the published enqueue and the
# settle step only: the full sweep and the straggler attach share `_bounded` and keep the session
# default.
_DRAIN_LOCK_TIMEOUT = "5s"


# Rule 20's mark for the changes that dirty nothing: a place resolved after the stamp (the resolver
# never patches browse_list) and a settle patch a rebuild's swap superseded. This is
# browse_projection's WHERE (status, the served rule on the location ad, the publication rule) less
# what browse_list already holds, over properties first seen within the cap plus two hours. No
# watermark: a row drops out once its patch lands. Anything older waits for the hourly rebuild
# (pg_cron, migration 740).
_ENQUEUE_PUBLISHED_SQL = f"""
    INSERT INTO dirty_properties (property_id)
    SELECT p.id FROM properties p
     WHERE p.first_seen_at > now() - {CAP_SQL} - interval '2 hours'
       AND p.status = 'active'
       AND {served_location_predicate("p.location_listing_ref_id")}
       AND {published_predicate("p")}
       AND NOT EXISTS (SELECT 1 FROM browse_list b WHERE b.property_id = p.id)
    ON CONFLICT (property_id) DO NOTHING
"""


def _enqueue_published(conn: Any, hold: job_lease.Hold | None = None) -> None:
    """Queue every property published but not yet in Browse (`_ENQUEUE_PUBLISHED_SQL`), ahead of the
    pass's cutoff, so this pass's drain patches it in. It reads `browse_list`, so it waits behind a
    rebuild's swap no longer than the drain does. A database error costs this step only (the next pass
    retries, the hourly rebuild meanwhile); a lost lease still stops it."""
    import psycopg

    try:
        with _bounded(conn), conn.cursor() as cur:
            cur.execute(f"SET LOCAL lock_timeout = '{_DRAIN_LOCK_TIMEOUT}'")
            cur.execute(_ENQUEUE_PUBLISHED_SQL)
            _fence(conn, hold)
    except psycopg.Error as exc:
        LOG.warning("MAINTENANCE published enqueue failed, the next pass retries: %s", exc)


def _drain_published(conn: Any, batch_size: int, hold: job_lease.Hold | None = None) -> int:
    """The full sweep's Browse half, run after each of its batches: the sweep holds the maintenance
    lease for its whole run (20 min on 2XL, its whole 6,000 s budget on Large, 2026-11-13) and the
    lane skips meanwhile, so publication, and every change the drain carries, would otherwise wait
    for the run's end. The order: settle, queue, cut off, drain."""
    _settle_fresh(conn, hold)
    _enqueue_published(conn, hold)
    with conn.cursor() as cur:
        cur.execute("SELECT now()")
        cutoff = cur.fetchone()[0]
    return _drain_dirty(conn, batch_size, cutoff, hold)


def _drain_dirty(
    conn: Any, batch_size: int, cutoff: Any,
    hold: job_lease.Hold | None = None,
) -> int:
    """Bring every property queued at/before `cutoff` current, one claimed slice at a time.

    Each slice runs `properties_changed` in ONE `_bounded` transaction under
    `_DRAIN_LOCK_TIMEOUT`, then dequeues the rows it claimed, each only while it still carries its
    claimed mark: a crash between the two replays the slice, every statement is idempotent, and a
    row re-dirtied mid-run survives. Always terminates -- only rows with marked_at <= cutoff are
    claimable, and only a transaction begun before the cutoff can stamp one (`_CLAIM_DIRTY_SQL`).

    `hold` is renewed once per claimed slice so a long drain — e.g. the backlog
    after a maintenance freeze, or the nine-portal enqueue volume post-#971 —
    heartbeats its 15-min lease instead of silently outliving it, and fences
    each slice's commit.
    """
    total = 0
    while True:
        if hold is not None:
            _renew_lease(conn, hold)
        with conn.cursor() as cur:
            cur.execute(_CLAIM_DIRTY_SQL, {"cutoff": cutoff, "limit": batch_size})
            claimed = cur.fetchall()
        if not claimed:
            break
        ids = [int(r[0]) for r in claimed]
        with _bounded(conn):
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL lock_timeout = '{_DRAIN_LOCK_TIMEOUT}'")
            properties_changed(conn, ids)
            _fence(conn, hold)
        with conn.cursor() as cur:
            cur.execute(_DELETE_DIRTY_SQL, {"ids": ids, "marks": [r[1] for r in claimed]})
        total += len(ids)
    return total


# The publication stamp (migrations 668/747, operator rulings 2026-10-23 and 2026-11-17): a property
# is served once it carries `settled_at` (`toolkit/publication.py`), and this statement alone
# writes it. Everything is read off what the pipeline has already written, never a judgement of
# our own.
# NEVER stamped while an ad on it AWAITS THE ENGINE'S READ: an ad with no `rt_fp` row in the
# engine's generation whose place is not determined yet (the served rule; the scope cannot be
# judged) or lies inside the engine's scope (`rt_scope`, the lane's own predicate on
# `listing_location` by primary key). Such an ad may be a re-list of a property already told, and
# only the engine can say; no clock ends this wait (dedup:unread_arrivals alarms on it). An ad
# outside the scope is never read and never waited for.
# Otherwise stamped when SETTLED:
#   - its first photo is stored, or none of its ads has a photo to wait for;
#   - the engine has read every ad (so none is outside the scope);
#   - the engine holds no pending question about any of them (a pair held for evidence);
#   - no engine group puts one of its ads with an ad on another property, or on none yet: the
#     merge that would retire it, or fold another into it, has not landed;
# or when CAP has passed since it was first seen AND since the engine first read each of its ads
# (`rt_fp.first_decided_at`): the one bounded wait, for photos and for an engine decision, and the
# whole wait outside the scope (ruling 4, 2026-10-23: no special case there). A decision can only
# be waited for once the engine has read the ad, so a late read restarts the wait: counted from
# first sight alone, an ad read after its cap would be published with no decision wait at all.
# Every unstamped property is a candidate, off `properties_unsettled_idx` (migration 747): the
# fresh ones and those waiting for the engine. ONE query judges and locks: the row lock lands only
# on the rows it stamps, never on every candidate; a property a merge, a split or a drain slice's
# changed rollup holds is skipped (SKIP LOCKED) and taken next pass; the engine's tables are read,
# never locked. Stamped once: no later change takes it back.
_UNREAD_SQL = """NOT EXISTS (SELECT 1 FROM autodedup.rt_fp f
                                            WHERE f.generation = %(generation)s
                                              AND f.listing_id = l.id)"""
_SETTLE_SQL = f"""
    WITH settled AS (
      SELECT p.id FROM properties p
      WHERE p.settled_at IS NULL
        AND NOT EXISTS (SELECT 1 FROM listings l
                         WHERE l.property_id = p.id
                           AND {_UNREAD_SQL}
                           AND (NOT {served_location_predicate("l.id")}
                                OR %(all_scope)s::boolean
                                OR EXISTS (SELECT 1 FROM listing_location ll
                                            WHERE ll.listing_id = l.id
                                              AND (ll.obec_kod = any(%(obec)s::bigint[])
                                                   OR ll.cast_obce_kod = any(%(cast_obce)s::bigint[])
                                                   OR ll.okres_kod = any(%(okres)s::bigint[])))))
        AND ((p.first_seen_at <= now() - {CAP_SQL}
              AND NOT EXISTS (SELECT 1 FROM listings l
                                JOIN autodedup.rt_fp f
                                  ON f.generation = %(generation)s AND f.listing_id = l.id
                               WHERE l.property_id = p.id
                                 AND f.first_decided_at > now() - {CAP_SQL}))
             OR ((EXISTS (SELECT 1 FROM listings l JOIN images i ON i.listing_id = l.id
                           WHERE l.property_id = p.id AND i.storage_path IS NOT NULL)
                  OR NOT EXISTS (SELECT 1 FROM listings l JOIN images i ON i.listing_id = l.id
                                  WHERE l.property_id = p.id))
                 AND NOT EXISTS (SELECT 1 FROM listings l
                                  WHERE l.property_id = p.id
                                    AND {_UNREAD_SQL})
                 AND NOT EXISTS (SELECT 1 FROM listings l
                                   JOIN autodedup.pairs x
                                     ON x.generation = %(generation)s AND x.decision = %(held)s
                                    AND (x.listing_lo = l.id OR x.listing_hi = l.id)
                                  WHERE l.property_id = p.id)
                 AND NOT EXISTS (SELECT 1 FROM listings l
                                   JOIN autodedup.cluster_members m
                                     ON m.generation = %(generation)s AND m.listing_id = l.id
                                   JOIN autodedup.cluster_members o
                                     ON o.generation = m.generation AND o.cluster_key = m.cluster_key
                                   JOIN listings ol ON ol.id = o.listing_id
                                  WHERE l.property_id = p.id
                                    AND ol.property_id IS DISTINCT FROM p.id)))
      FOR NO KEY UPDATE OF p SKIP LOCKED
    )
    UPDATE properties p SET settled_at = now()
      FROM settled s
     WHERE p.id = s.id AND p.settled_at IS NULL
    RETURNING p.id
"""

_ENGINE_SCOPE_SQL = "SELECT s.value FROM autodedup.settings s WHERE s.key = %s"


def _engine_scope(conn: Any) -> dict[str, Any]:
    """The four parameters of the engine's scope predicate, as its lane takes them. No scope row:
    the engine reads nothing (a pass without one refuses itself), so no ad awaits it. A row that
    cannot be parsed raises `ScopeError`: the caller stamps nothing rather than guess."""
    from autodedup.incremental import GENERATION
    from autodedup.incremental_lane import parse_scope, scope_setting_key

    with conn.cursor() as cur:
        cur.execute(_ENGINE_SCOPE_SQL, (scope_setting_key(GENERATION),))
        row = cur.fetchone()
    if row is None or row[0] is None:
        return {"all_scope": False, "obec": [], "cast_obce": [], "okres": []}
    return parse_scope(row[0]).params()


def _settle_fresh(conn: Any, hold: job_lease.Hold | None = None) -> int:
    """Stamp the properties the pipeline is done with (`_SETTLE_SQL`), then patch their Browse
    rows in; returns how many it stamped. The stamp commits ALONE: it row-locks what it stamps, and
    the engine's reconcile merges under a 5 s lock_timeout, so a merge waits behind one statement at
    most (autodedup session, 2026-10-23). The patch is `sync_browse_list` on its own -- no recompute
    (a stamp changes no rolled-up fact), no row lock, best-effort (the next pass's
    `_enqueue_published` carries a lost patch). A database error in the stamp, or an engine scope
    that cannot be read, costs this step only (the next pass retries; nothing is published
    meanwhile), never the pass; a lost lease still stops it."""
    import psycopg

    from autodedup.incremental import EVIDENCE_HOLD_REASON, GENERATION
    from autodedup.incremental_scope import ScopeError

    try:
        scope = _engine_scope(conn)
        if hold is not None:
            _renew_lease(conn, hold)
        with _bounded(conn):
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL lock_timeout = '{_DRAIN_LOCK_TIMEOUT}'")
                cur.execute(_SETTLE_SQL, {"generation": GENERATION, "held": EVIDENCE_HOLD_REASON,
                                          **scope})
                ids = [int(r[0]) for r in cur.fetchall()]
            _fence(conn, hold)
    except (psycopg.Error, ScopeError) as exc:
        LOG.warning("MAINTENANCE settle failed, the next pass retries: %s", exc)
        return 0
    sync_browse_list(conn, ids)
    return len(ids)


def _max_property_id(conn: Any) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT coalesce(max(id), 0) FROM properties")
        return int(cur.fetchone()[0])


def _read_sweep_cursor(conn: Any) -> tuple[Any, ...] | None:
    """The saved cursor as (next_lo, runs, cycle_started_at, younger than _CURSOR_MAX_AGE)."""
    import psycopg

    try:
        with conn.cursor() as cur:
            cur.execute(_READ_SWEEP_CURSOR_SQL, {"max_age": _CURSOR_MAX_AGE})
            return cur.fetchone()
    except psycopg.DataError as exc:
        # The Settings page edits any app_settings row as raw JSON, and a value the casts reject
        # is a DataError run_resilient never retries: it would red every run until someone fixed
        # the row by hand. Like resolve_brokers._sweep_state, treat it as no cursor instead.
        LOG.warning("RECOMPUTE property_sweep_cursor is unreadable (%s): treated as absent, so "
                    "a fresh cycle starts at id 1 and its first save replaces the row", exc)
        return None


def _resume_point(saved: tuple[Any, ...] | None, max_id: int) -> tuple[int, int, Any] | None:
    """(next_lo, runs, cycle_started_at) if the saved cursor can continue its cycle, else None."""
    if saved is None:
        return None
    next_lo, runs, cycle_started_at, young = saved
    if not young or next_lo is None or not (1 < next_lo <= max_id):
        return None
    return int(next_lo), int(runs or 0), cycle_started_at


# ONE LEASE serializes EVERY property-maintenance writer: the realtime worker's
# maintenance lane, the daily full sweep, AND a hand --incremental run. It is the
# `property_maintenance` row of scheduled_jobs, held through scraper/job_lease.py,
# its holder prefixed with the mode (`incremental:` / `full:`). Incremental callers
# TRY the lease and skip when held (the next tick is seconds away); the daily full
# sweep WAITS (bounded) until it holds it — the backstop must not be skipped.
#
# ONE short TTL, HEARTBEAT-renewed between transactions. The full sweep used to take
# a single 3-hour grant sized to its whole runtime and release it in a `finally` —
# but a timeout kill (then GitHub's SIGINT→SIGTERM→SIGKILL, now the runner's SIGKILL
# 90 s after its SIGTERM) can beat the release round-trip, so every timeout stranded
# the lease and froze ALL property maintenance (worker lane + both crons) for hours
# (2026-08-06 incident: 5 kills in 4 days, each a multi-hour freeze).
# Cleanup-on-death cannot be relied on; cheap-death can: every writer takes the same
# 15-minute TTL and renews it between batches, so a kill at ANY point strands the
# row for at most 15 minutes. A renewal refuses a lapsed lease, so the TTL less
# job_lease.FENCE_MARGIN_S must comfortably exceed one batch's worst case — a sweep
# batch is the rollup under the explicit _BATCH_STATEMENT_TIMEOUT (10 min) ceiling
# plus the broker book under _BOOK_STATEMENT_TIMEOUT (2 min) — 15 min is that
# margin, not a renewal cadence. Every write transaction ends with job_lease.fence,
# so none commits under a lease that lapsed mid-batch; the dirty queue's claim and
# dequeue are single keyed statements a replay repeats.
MAINTENANCE_LEASE = "property_maintenance"
_LEASE_TTL_S = 900
# Both describe the row only, for whichever writer creates it first.
_LEASE_CADENCE = "30 seconds"
_LEASE_RUNNER = "railway-realtime-worker"
# A dispatched sweep that cannot get the lease within this budget fails RED
# instead of burning its whole job retrying (observed 2026-08-06: a 30-min run
# spent 100% of its budget waiting on a dead holder's 3h grant).
# Every holder now renews a 15-min TTL, so 20 min of waiting means something
# is genuinely wrong, not merely slow.
_MAX_LEASE_WAIT_SECONDS = 1200.0

# Ceiling for --max-seconds. The property_sweep entry's timeout_s
# (scraper/jobs.py) is sized as budget + one in-flight batch + prelude +
# finalize headroom FOR THIS CEILING; an unclamped request argument above it
# would let the runner kill a healthy sweep before its clean-stop fires — the
# exact mode this script exists to eliminate. Raising the ceiling means
# raising timeout_s in the same change.
#
# 6000s (100 min), raised from 4200s: the six sweeps to 2026-08-10 measured
# 3502/3777/4044/3890/3626s — 83-96% of the old 4200s ceiling, i.e. one bad day
# from clean-stopping RED with the corpus still growing (~2k properties/day).
# 6000s puts the observed worst case (4044s) at ~67% and leaves a full 30 min of
# growth headroom. Sized against that regime deliberately: the 2026-08-12 sweep
# finished in 508s (avg batch 1.6s vs 11-13s) after the 08-11 Supabase restart,
# but one post-restart datapoint is not a new baseline.
_MAX_BUDGET_SECONDS = 6000.0

# Per-recompute-statement ceiling, applied via SET LOCAL inside an explicit
# transaction (the repo's layered-timeout pattern; SET LOCAL no-ops without
# conn.transaction() on an autocommit connection). The pooler's ~2-min default
# proved too tight for the post-#971 batch SQL on deep-history batches: the
# 2026-08-06 10:09Z run's FIRST batch (ids 1-2001, the oldest sreality
# listings) was killed at ~3.5 min while later-id batches run in seconds.
# Together with _BOOK_STATEMENT_TIMEOUT it MUST stay comfortably under _LEASE_TTL_S
# (15 min): renewal fires as the first statement of each batch attempt, so ONE
# batch's worst case (its two statements, `_recompute_batch`) is the longest
# possible renewal gap. (Per ATTEMPT, deliberately — see
# _BATCH_RESILIENT_ATTEMPTS: renewing only once per loop iteration would let a
# retried batch outlive its own lease.)
_BATCH_STATEMENT_TIMEOUT = "10min"
# The broker book's ceiling inside the same batch transaction (migrations 691, 692). Its
# statement reads only the batch's attributed adverts; the ceiling exists to keep one
# attempt (rollup + book) under the lease TTL. Broker board W0 measures its real cost.
_BOOK_STATEMENT_TIMEOUT = "2min"

# Retry budget for ONE recompute batch (db.run_resilient defaults to 4). A batch
# runs under the 10-min ceiling above, so four attempts could burn 40 minutes
# inside a single loop iteration — the deadline is only checked at a batch
# BOUNDARY, so that would sail past the runner's timeout_s and re-create
# the kill mid-batch this script exists to eliminate. Two attempts keeps the
# useful part of the budget (a dropped connection or a passing lock wait replays
# once) and bounds the in-flight worst case at 2 x (_BATCH_STATEMENT_TIMEOUT +
# _BOOK_STATEMENT_TIMEOUT), which is what the runner's timeout_s is sized
# for. A range that times out twice is the poisoned range the error log names,
# not a blip. 24 min also exceeds _LEASE_TTL_S, which is why the lease renewal runs
# INSIDE the retried op (main()'s `renewed`) rather than once per loop iteration.
_BATCH_RESILIENT_ATTEMPTS = 2


def _new_holder(kind: str) -> str:
    return f"{kind}:{job_lease.holder_id()}"


def _renew_lease(conn: Any, hold: job_lease.Hold) -> None:
    """Heartbeat between transactions; raises if it misses: the lease lapsed mid-work, this process stalled >15 min."""
    if not job_lease.renew(conn, hold):
        raise job_lease.LeaseLost(
            "maintenance lease lost mid-work (it lapsed or another writer took "
            "it) — aborting rather than recomputing concurrently"
        )


def _fence(conn: Any, hold: job_lease.Hold | None) -> None:
    """The last statement of a transaction under the lease; a caller with no lease (a test) has nothing to fence."""
    if hold is not None:
        job_lease.fence(conn, hold)


@contextmanager
def _maintenance_lease(
    conn: Any, kind: str, *, wait_s: float = 0.0,
    reconnect: Callable[[], Any] | None = None,
) -> Iterator[job_lease.Hold | None]:
    """Yields this writer's hold on the maintenance lease, or None when another
    writer holds it (past `wait_s`) or it is paused. A release on a dead `conn` falls
    back to `reconnect` when given, else to `held`'s own one-attempt connection."""
    fallback = {} if reconnect is None else {"connect": reconnect}
    with job_lease.held(conn, MAINTENANCE_LEASE, cadence=_LEASE_CADENCE, runner=_LEASE_RUNNER,
                        ttl_s=_LEASE_TTL_S, holder=_new_holder(kind), wait_s=wait_s, **fallback) as hold:
        yield hold


def run_incremental_pass(conn: Any, batch_size: int = 2000) -> dict[str, Any]:
    """ONE incremental property-maintenance pass — THE shared implementation
    behind `--incremental` and the realtime worker's
    maintenance lane: queue what is published but not in Browse (`_enqueue_published`), attach new stragglers,
    then bring the dirty set current through `properties_changed` (recompute, broker book,
    `browse_list` patch), so a change reaches Browse on this lane's cadence rather than at the
    next hourly wholesale rebuild, then stamp the properties the pipeline is done with
    (`_settle_fresh`): the stamp is publication.
    Serialized by the maintenance lease; a caller that
    finds the lease held returns {"skipped": True} — the concurrent pass is
    doing the same work, and the next tick is seconds away. A pass normally
    runs seconds; the 15-minute lease is a wide margin, and its expiry
    self-heals a crashed holder.
    """
    with _maintenance_lease(conn, "incremental") as hold:
        if hold is None:
            return {"skipped": True, "attached": 0, "recomputed": 0, "settled": 0}
        _enqueue_published(conn, hold)
        with conn.cursor() as cur:
            cur.execute("SELECT now()")
            cutoff = cur.fetchone()[0]
        attached = _attach_stragglers(conn, hold=hold)
        recomputed = _drain_dirty(conn, batch_size, cutoff, hold)
        settled = _settle_fresh(conn, hold)
        return {"skipped": False, "attached": attached, "recomputed": recomputed,
                "settled": settled}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch-size", type=int, default=2000,
        help="Properties recomputed per statement (default 2000).",
    )
    parser.add_argument(
        "--incremental", action="store_true",
        help="Dirty-set mode: attach new stragglers + recompute only queued "
             "properties + settle fresh ones. Default is the full "
             "sweep over every property (the daily reconcile backstop).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report straggler + dirty + property counts and the resume cursor, "
             "then exit without writing.",
    )
    parser.add_argument(
        "--max-seconds", type=float, default=6000.0,
        help="Full-sweep wall-clock budget (default 6000). On exhaustion the "
             "sweep clean-stops at a batch boundary, finalizes only what it "
             "covered, saves where the next run resumes the cycle, and releases "
             "the lease; a resumed run that runs out again, or a run that runs out "
             "before its first batch, exits RED (1) — a "
             "visible failure instead of a kill mid-batch at the runner's timeout_s. "
             f"Clamped to {int(_MAX_BUDGET_SECONDS)}s: the job runner's "
             "timeout_s is sized for that ceiling, and a larger budget would "
             "let the runner kill the job before the clean-stop fires "
             "(raising both requires editing scraper/jobs.py).",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # SIGTERM (the job runner's stop at a deploy or timeout_s, 90 s before its
    # SIGKILL) defaults to instant death — no finally, lease stranded. Route it
    # through SystemExit so the release path gets its chance; the short renewed
    # TTL is the guarantee for when even this loses the race.
    signal.signal(signal.SIGTERM, _sigterm_to_systemexit)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.batch_size < 1:
        print("ERROR: --batch-size must be >= 1.", file=sys.stderr)
        return 2

    # Explicit check before db.connect(): database_url() would raise a bare
    # RuntimeError instead of this friendly message + exit 2.
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("ERROR: SUPABASE_DB_URL is not set.", file=sys.stderr)
        return 2

    mode = "incremental" if args.incremental else "full"
    LOG.info(
        "RECOMPUTE config mode=%s batch_size=%d dry_run=%s",
        mode, args.batch_size, args.dry_run,
    )

    def reconnect() -> Any:
        return db.connect(db_url)

    started_at = time.monotonic()
    # db.connect() instead of a bare psycopg.connect(): same autocommit +
    # prepare_threshold=None, PLUS TCP keepalives and a 3-attempt handshake retry.
    # The full sweep holds this ONE connection for up to the whole budget, so a
    # pooler recycle or a Supabase restart (2026-08-11, an AdminShutdown 338s in)
    # used to kill the run outright.
    with db.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT now()")
            cutoff = cur.fetchone()[0]

        def step(op: Callable[[Any], Any], label: str,
                 attempts: int | None = None) -> Any:
            """db.run_resilient with the conn rebinding its docstring demands (it may
            hand back a FRESH connection after a pooler drop). Every op below is
            idempotent — recompute statements are pure latest-wins recomputes and the
            dirty-clear, cursor and stamp are keyed writes, so a replay re-commits
            identically."""
            nonlocal conn
            budget = {} if attempts is None else {"attempts": attempts}
            result, conn = db.run_resilient(
                conn, op, reconnect=reconnect, label=label, **budget)
            return result

        if args.dry_run:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM listings WHERE property_id IS NULL")
                stragglers = int(cur.fetchone()[0])
                cur.execute("SELECT count(*) FROM dirty_properties")
                dirty = int(cur.fetchone()[0])
                cur.execute("SELECT count(*) FROM properties")
                properties = int(cur.fetchone()[0])
            saved = step(_read_sweep_cursor, "sweep.cursor")
            LOG.info(
                "RECOMPUTE dry-run mode=%s stragglers=%d dirty=%d properties=%d "
                "cursor(next_lo, runs, cycle_started_at, younger than %s)=%s; exit",
                mode, stragglers, dirty, properties, _CURSOR_MAX_AGE, saved,
            )
            return 0

        # Incremental: attach new stragglers, recompute only the queued (dirty)
        # properties, then settle. The full-table sweep is the daily reconcile.
        # Shared implementation with the realtime worker's maintenance lane.
        if args.incremental:
            stats = run_incremental_pass(conn, args.batch_size)
            elapsed = time.monotonic() - started_at
            if stats["skipped"]:
                LOG.info(
                    "RECOMPUTE incremental skipped: another maintenance pass "
                    "holds the lock (worker lane or daily sweep)",
                )
                return 0
            LOG.info(
                "RECOMPUTE incremental done attached=%d recomputed=%d settled=%d elapsed=%.1fs",
                stats["attached"], stats["recomputed"], stats["settled"], elapsed,
            )
            return 0

        # The full sweep WAITS for the lease (it is the daily backstop and must
        # not be skipped) — but boundedly, failing RED rather than burning the
        # whole job against a stuck holder.
        incomplete_at: int | None = None

        with _maintenance_lease(conn, "full", wait_s=_MAX_LEASE_WAIT_SECONDS,
                                reconnect=reconnect) as hold:
            if hold is None:
                raise RuntimeError(
                    f"maintenance lease still held or paused after {_MAX_LEASE_WAIT_SECONDS:.0f}s "
                    "of waiting — failing RED instead of burning the job budget; every "
                    "holder renews a 15-min TTL, so this indicates a real fault"
                )

            # Renewal is the FIRST statement of a retried op, not a separate step before it.
            # _BATCH_RESILIENT_ATTEMPTS lets one batch occupy two attempts (24 min with the book's
            # ceiling), which would blow past the 15-min TTL and break this module's invariant that
            # one attempt's worst case is the longest possible renewal gap. Re-asserting per attempt
            # also works on the FRESH connection after a pooler drop; a genuinely lost lease raises
            # LeaseLost, which run_resilient re-raises immediately (not an OperationalError -> not
            # transient).
            def renewed(op: Callable[[Any], Any]) -> Callable[[Any], Any]:
                def run(c: Any) -> Any:
                    _renew_lease(c, hold)
                    return op(c)
                return run

            def attach(c: Any) -> int:
                return _attach_stragglers(c, hold=hold)

            def drain(c: Any) -> int:
                return _drain_published(c, args.batch_size, hold)

            # attempts=2, like sweep.batch: a doomed attach reds in ~4 min instead of ~8.
            # Bounded batches until one comes back short: each is its own transaction.
            attached = 0
            while True:
                batch = step(renewed(attach), "sweep.attach", attempts=2)
                attached += batch
                if batch < STRAGGLER_BATCH:
                    break
            LOG.info("RECOMPUTE stragglers attached=%d", attached)

            budget = min(args.max_seconds, _MAX_BUDGET_SECONDS)
            if budget < args.max_seconds:
                LOG.warning(
                    "RECOMPUTE --max-seconds %.0f clamped to %.0f — the "
                    "job runner's timeout_s is sized for this ceiling; raise "
                    "both together in scraper/jobs.py",
                    args.max_seconds, budget,
                )
            deadline = started_at + budget
            max_id = step(_max_property_id, "sweep.max_id")
            saved = step(_read_sweep_cursor, "sweep.cursor")
            resume = _resume_point(saved, max_id)
            if resume is not None:
                start_lo, runs, cycle_started_at = resume
                LOG.info(
                    "RECOMPUTE resuming the cycle started %s at id %d (run %d of it)",
                    cycle_started_at, start_lo, runs + 1,
                )
            else:
                if saved is not None:
                    LOG.warning(
                        "RECOMPUTE ignoring the saved cursor %s (cycle older than %s, "
                        "or next_lo outside 2-%d); a fresh cycle starts at id 1",
                        saved, _CURSOR_MAX_AGE, max_id,
                    )
                # A fresh cycle starts when this run did: its cutoff, by the database clock.
                start_lo, runs, cycle_started_at = 1, 0, cutoff
            ranges = list(_batch_ranges(max_id, args.batch_size, start_lo))
            total_batches = len(ranges)
            batches = recomputed = drained = 0

            for lo, hi in ranges:
                # Budget clean-stop (the detail drains' --max-seconds pattern):
                # stop batching with enough headroom left to finalize + release,
                # instead of being killed mid-statement at the runner's timeout_s.
                batch_started = time.monotonic()
                if batch_started >= deadline:
                    incomplete_at = lo
                    break
                def recompute(c: Any, lo: int = lo, hi: int = hi) -> int:
                    return _recompute_batch(c, lo, hi, hold)

                try:
                    recomputed += step(renewed(recompute), "sweep.batch",
                                       attempts=_BATCH_RESILIENT_ATTEMPTS)
                except Exception:
                    # Name the poisoned range before dying — the 10:09Z run's
                    # log showed only a bare QueryCanceled with no way to tell
                    # WHICH ids need investigating.
                    LOG.error(
                        "RECOMPUTE batch=%d-%d FAILED after %.1fs",
                        lo, hi, time.monotonic() - batch_started,
                    )
                    raise
                batches += 1
                # One line per batch, deliberately: ~311 lines/day buys the
                # per-range cost profile that diagnosing the post-#971 SQL
                # (and any future creep toward the budget) depends on.
                LOG.info(
                    "RECOMPUTE batch=%d-%d %.1fs (%d/%d)",
                    lo, hi, time.monotonic() - batch_started,
                    batches, total_batches,
                )
                # Births wait on this lease; newborns sit above max_id, so no batch revisits them.
                # A failed birth costs only latency (the worker's lane births once the lease is
                # free), so it never stops the backstop sweep; a lost lease still does.
                try:
                    attached += step(renewed(attach), "sweep.attach", attempts=2)
                except job_lease.LeaseLost:
                    raise
                except Exception as exc:  # noqa: BLE001 - see above
                    LOG.warning("RECOMPUTE sweep.attach failed after batch=%d-%d: %s", lo, hi, exc)
                # The lane's Browse half, on the same terms (`_drain_published`): what the cap
                # published and every queued change reach Browse within a batch, not at the run's
                # end or the hourly rebuild (#2505).
                try:
                    drained += step(renewed(drain), "sweep.drain", attempts=2)
                except job_lease.LeaseLost:
                    raise
                except Exception as exc:  # noqa: BLE001 - as the attach above
                    LOG.warning("RECOMPUTE sweep.drain failed after batch=%d-%d: %s", lo, hi, exc)

            # The finalize block is wrapped too: a drop here would throw away an
            # otherwise-complete sweep's completion stamp and red the health check.
            if incomplete_at is None:
                def reconcile(c: Any) -> int:
                    with c.transaction():
                        childless = _reconcile_childless(c)
                        job_lease.fence(c, hold)
                    return childless

                reconciled = step(renewed(reconcile), "sweep.reconcile")
                if reconciled:
                    LOG.info(
                        "RECOMPUTE reconciled childless=%d (reset to no ads)",
                        reconciled,
                    )

                # Fixed before the op, so a replay after an unseen COMMIT stamps the same facts.
                duration_ms = int((time.monotonic() - started_at) * 1000)

                def _finalize(conn: Any) -> None:
                    # This run recomputed [start_lo, max_id]: clear that range's dirt,
                    # stamp the completed cycle (the jobs check's O(1) liveness read;
                    # complete cycles only — an incomplete one leaving the stamp stale IS
                    # the alarm condition), and delete the cursor in the same
                    # transaction, so a stamp never coexists with it.
                    with conn.transaction(), conn.cursor() as cur:
                        cur.execute(_CLEAR_DIRTY_SWEPT_SQL, {
                            "cutoff": cutoff, "lo": start_lo, "hi": max_id + 1})
                        db.stamp_derived_artifact(conn, "property_sweep", rows=recomputed,
                                                  duration_ms=duration_ms)
                        cur.execute(_DELETE_SWEEP_CURSOR_SQL)
                        job_lease.fence(conn, hold)

                step(renewed(_finalize), "sweep.finalize")
            else:
                # Only [start_lo, incomplete_at) was recomputed: clear its dirt only, save
                # where the next run resumes, and leave _reconcile_childless to the run
                # that completes the cycle (its targets are near-zero in practice).
                def _stop(c: Any) -> None:
                    with c.transaction(), c.cursor() as cur:
                        cur.execute(_CLEAR_DIRTY_SWEPT_SQL, {
                            "cutoff": cutoff, "lo": start_lo, "hi": incomplete_at})
                        cur.execute(_SAVE_SWEEP_CURSOR_SQL, {
                            "next_lo": incomplete_at,
                            "cycle_started_at": cycle_started_at, "runs": runs + 1})
                        job_lease.fence(c, hold)

                # A fresh run stopped before batch 1 swept nothing to clear, and a cursor at
                # id 1 is never resumed (_resume_point needs 1 < next_lo): save nothing.
                if incomplete_at > 1:
                    step(renewed(_stop), "sweep.stop")

    elapsed = time.monotonic() - started_at
    if incomplete_at == 1:
        # The budget clock starts before the lease wait and the straggler attach, so a backlog
        # can spend it all before batch 1. Nothing was recomputed and no cursor saved, so there
        # is no cycle for the next run to continue: RED, or every such day would exit green
        # having reconciled nothing.
        LOG.error(
            "RECOMPUTE budget exhausted after %.0fs before the first batch (the lease wait and "
            "the straggler attach above ran first): nothing recomputed and no cursor saved, so "
            "the next run starts a fresh cycle at id 1; exiting RED",
            elapsed,
        )
        return 1
    if incomplete_at is not None and resume is None:
        # One continuation is tolerated: the cursor is saved and the next run finishes the
        # cycle from it, stamping it complete like any other.
        LOG.warning(
            "RECOMPUTE budget exhausted after %.0fs: swept ids %d to %d of %d (%d/%d batches); "
            "the next run resumes this cycle at id %d",
            elapsed, start_lo, incomplete_at - 1, max_id, batches, total_batches, incomplete_at,
        )
        return 0
    if incomplete_at is not None:
        # RED on purpose: a resumed run that runs out again means the cycle needs three or
        # more runs — genuinely too slow, not a partial success, so its job cell shows a
        # `failed` run (on GitHub a timeout kill landed as `cancelled` and alerted nobody,
        # which is how 5 dead sweeps went unnoticed for 4 days). The id tail above
        # `incomplete_at` keeps its pre-sweep stats until a cycle completes; the jobs
        # check's `artifact/property_sweep` cell tracks that staleness.
        LOG.error(
            "RECOMPUTE budget exhausted after %.0fs: swept ids %d to %d of %d (%d/%d batches) "
            "in run %d of the cycle started %s, so the cycle needs three or more runs; "
            "cursor saved at id %d (resumed by a run within %s of the cycle's start, else "
            "the next run starts over at id 1); exiting RED — investigate per-batch cost "
            "first (see the progress logs); raising the budget past %.0fs requires editing "
            "BOTH --max-seconds and the job runner's timeout_s (scraper/jobs.py)",
            elapsed, start_lo, incomplete_at - 1, max_id, batches, total_batches,
            runs + 1, cycle_started_at, incomplete_at, _CURSOR_MAX_AGE,
            _MAX_BUDGET_SECONDS,
        )
        return 1
    LOG.info(
        "RECOMPUTE done max_property_id=%d from_id=%d cycle_runs=%d batches=%d "
        "avg_batch_s=%.1f attached=%d drained=%d elapsed=%.1fs",
        max_id, start_lo, runs + 1, batches, elapsed / batches if batches else 0.0,
        attached, drained, elapsed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
