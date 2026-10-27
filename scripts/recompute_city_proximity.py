"""The FULL city-proximity rebuild (--full): the last step of the population load and the curated-city seed.

The hourly incremental `recompute_city_proximity(false)` runs on pg_cron (migration 674); a population or
city-index load shifts the precomputed maxes of every property, which only this walk recomputes: the placeable
properties in id batches, one short transaction each (restamp the batch, let the incremental call refill it),
since one statement over ~975k of them would run near an hour holding every row lock it took. Without --full,
one incremental call by hand. Requires SUPABASE_DB_URL.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

import psycopg

LOG = logging.getLogger("recompute_city_proximity")

FULL_BATCH = 5_000
DEADLOCK_ATTEMPTS = 3
_TIMEOUT_SQL = "set local statement_timeout = '20min'"
# The function takes this lock itself; a batch takes it FIRST, before its restamp's row locks, or the hourly
# pg_cron run could hold it while waiting on a restamped row (a deadlock).
_LOCK_SQL = "select pg_advisory_xact_lock(hashtext('recompute_city_proximity'))"

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


def _fill_unstamped(cur: Any) -> int:
    cur.execute("select recompute_city_proximity(false)")
    return int(cur.fetchone()[0])


def incremental(conn: Any) -> int:
    """One incremental call (what pg_cron runs hourly); returns the rows written."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_TIMEOUT_SQL)
        return _fill_unstamped(cur)


def _batch(conn: Any, after: int, size: int) -> tuple[list[int], int]:
    """One batch in one transaction, retried whole when the property rollup wins a deadlock."""
    attempt = 1
    while True:
        try:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(_TIMEOUT_SQL)
                cur.execute(_LOCK_SQL)
                cur.execute(_RESTAMP_BATCH_SQL, {"after": after, "size": size})
                ids = [row[0] for row in cur.fetchall()]
                return ids, (_fill_unstamped(cur) if ids else 0)
        except psycopg.errors.DeadlockDetected:
            if attempt == DEADLOCK_ATTEMPTS:
                raise
            attempt += 1
            LOG.warning("full rebuild: batch after property %d lost a deadlock; retrying", after)


def full_rebuild(conn: Any, batch_size: int = FULL_BATCH) -> int:
    """Recompute every placeable property, one batch per transaction; returns the rows written."""
    after, written = 0, 0
    while True:
        ids, n = _batch(conn, after, batch_size)
        if not ids:
            return written
        after, written = max(ids), written + n
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
