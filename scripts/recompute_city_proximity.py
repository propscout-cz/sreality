"""Fill properties.home_obec_pop + near_{pop,jobs,youth,overall}_{5,15}km.

Thin driver around the SQL function `recompute_city_proximity(p_full)`
(migration 142). The function does a small-anchor spatial join per property to
precompute the city-proximity columns Browse / Watchdog filter on, so those
filters are plain indexed-column predicates instead of a per-request spatial
RPC (no anon 3s statement-timeout).

The hourly incremental call runs on pg_cron (job recompute-city-proximity,
migration 674): properties whose `city_proximity_computed_at` is NULL — new
ones, and ones whose location ad changed since their figures were computed (the
property rollup clears the stamp). This script is the FULL rebuild (--full), the
last step of the population load and the curated-city seed, since those shift
the precomputed maxes of every property; without --full it makes one
incremental call by hand. Only properties whose location ad has a map point are
computed; the rest keep their figures. Requires SUPABASE_DB_URL.

--full walks the placeable properties in id batches, one short transaction each:
clear the batch's stamp, then let the incremental call refill it. One statement
over all ~975k of them would run near an hour and hold every row lock it took
until it ended.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

import psycopg

LOG = logging.getLogger("recompute_city_proximity")

FULL_BATCH = 10_000
_TIMEOUT_SQL = "set local statement_timeout = '20min'"

# The next batch of properties the function can place (a map point at the location ad), by id.
_RESTAMP_BATCH_SQL = """
with batch as (
    select p.id
      from properties p
      join listing_location ll on ll.listing_id = p.location_listing_ref_id
     where p.id > %(after)s and ll.geom is not null
     order by p.id
     limit %(size)s)
update properties p set city_proximity_computed_at = null
  from batch
 where p.id = batch.id
returning p.id
"""


def _recompute(cur: Any) -> int:
    cur.execute("select recompute_city_proximity(false)")
    return int(cur.fetchone()[0])


def incremental(conn: Any) -> int:
    """One incremental call (what pg_cron runs hourly); returns the rows written."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_TIMEOUT_SQL)
        return _recompute(cur)


def full_rebuild(conn: Any, batch_size: int = FULL_BATCH) -> int:
    """Recompute every placeable property, one batch per transaction; returns the rows written."""
    after, written = 0, 0
    while True:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_TIMEOUT_SQL)
            cur.execute(_RESTAMP_BATCH_SQL, {"after": after, "size": batch_size})
            ids = [row[0] for row in cur.fetchall()]
            if not ids:
                return written
            written += _recompute(cur)
        after = max(ids)
        LOG.info("full rebuild: through property %d, %d written", after, written)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="recompute_city_proximity")
    p.add_argument(
        "--full", action="store_true",
        help="Rebuild every property (default: only rows not yet computed)",
    )
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    dsn = os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        LOG.error("SUPABASE_DB_URL is not set")
        return 2

    with psycopg.connect(dsn, prepare_threshold=None, autocommit=True) as conn:
        updated = full_rebuild(conn) if args.full else incremental(conn)
    LOG.info("recompute_city_proximity(full=%s): updated %d properties", args.full, updated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
