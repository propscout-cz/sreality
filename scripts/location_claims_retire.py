"""W3's purge: keep ONE `location_claims` row per (listing, claim type, licence class).

LAST RUN BEFORE MIGRATION 699; DELETED IN W3's PR-2 (#2223). Until W3 this script retired the
claims of retired contract versions; its one last run removes the ≈ 8.8 M rows that are a
second or later copy of one fact (99.68 % the same value restated under a newer contract
version), so 699 can put the unique key `location_claims_key` on what remains.

THE KEEPER is the row today's resolver reads: per key, the newest contract version at or below
the active one, then the newest observation, then the highest id —

    ORDER BY (pc.version <= act.version) DESC NULLS LAST, pc.version DESC NULLS FIRST,
             first_observed_at DESC, id DESC

so for 99.97 % of keys nothing the resolver reads changes and nothing is enqueued. A key whose
only rows sit under a retired version keeps one of them (≈ 15.7 k keys: sreality's v3 cast obce
that v4 stopped stating, bazos slug towns of listings with no reading): the old retire rail
counted LISTINGS and would have deleted those types one at a time — W18-b again. This one
cannot: a key never loses its keeper, and every batch re-counts the KEYS it left (`_KEYS_SQL`)
and stops the run (exit 3) if one is missing.

THE RECEIPT lists every listing whose answer may move: one that loses a row of its keeper's own
version (the resolver's window read those beside the keeper; ≈ 2.8 k listings). W3's rollout
re-mines exactly those (design § 4 step 5). Gzipped CSV, `--receipt`, uploaded by the workflow.

THE BACKUP is the workflow's first step: a whole-table `\\copy` to R2 before any row goes.

BOUNDED, PAUSED, RESUMABLE: 2,000 listings a statement (keyset over `listings.id`), each its own
transaction with `lock_timeout 5s` and `statement_timeout 120s`, 0.5 s between batches; a lock
lost is retried, a budget spent stops between batches and logs the cursor to resume from
(`--after-listing-id`); re-running re-derives the same keepers.

    python3 -m scripts.location_claims_retire --dry-run
    python3 -m scripts.location_claims_retire --confirm PURGE --receipt purge.csv.gz
"""

from __future__ import annotations

import argparse
import gzip
import logging
import sys
import time
from dataclasses import dataclass, field

import psycopg

from scraper.db import connect

LOG = logging.getLogger("location_claims_retire")

DEFAULT_LISTINGS = 2_000
DEFAULT_PAUSE_SECONDS = 0.5
DEFAULT_MAX_SECONDS = 20_000
PROGRESS_EVERY = 25
LOCK_RETRIES = 5
_LOCK_SQLSTATES = frozenset({"55P03", "40P01"})  # lock_not_available, deadlock_detected

_BATCH_TIMEOUTS_SQL = ("SET LOCAL lock_timeout = '5s'", "SET LOCAL statement_timeout = '120s'")

# ONE STATEMENT PER BATCH, the same for the dry run (`armed` false deletes nothing), so what a
# dry run counts is what the real run deletes. `keeper_version` is the version of the row the
# key keeps; a deleted row of that same version was read beside it, so its listing is collapsed.
_BATCH_SQL = """
    WITH batch AS (
        SELECT id FROM listings WHERE id > %(after)s ORDER BY id LIMIT %(listings)s
    ), ranked AS (
        SELECT c.id, c.listing_id, pc.version, act.version AS active_version,
               row_number() OVER w AS rn, first_value(pc.version) OVER w AS keeper_version
          FROM batch b
          JOIN location_claims c ON c.listing_id = b.id
          LEFT JOIN portal_contract_entries pce ON pce.id = c.contract_entry_id
          LEFT JOIN portal_contracts pc ON pc.id = pce.contract_id
          LEFT JOIN portal_contracts act ON act.source = pc.source AND act.is_active
        WINDOW w AS (PARTITION BY c.listing_id, c.claim_type, c.licence_class
                     ORDER BY (pc.version <= act.version) DESC NULLS LAST,
                              pc.version DESC NULLS FIRST, c.first_observed_at DESC, c.id DESC)
    ), deleted AS (
        DELETE FROM location_claims c USING ranked r
         WHERE c.id = r.id AND r.rn > 1 AND %(armed)s
        RETURNING c.id
    )
    SELECT (SELECT max(id) FROM batch),
           (SELECT count(*) FROM ranked WHERE rn = 1),
           (SELECT count(*) FROM ranked WHERE rn > 1),
           (SELECT count(*) FROM ranked WHERE rn = 1 AND version < active_version),
           (SELECT count(*) FROM deleted),
           (SELECT coalesce(array_agg(DISTINCT listing_id ORDER BY listing_id), '{}')
              FROM ranked WHERE rn > 1 AND version IS NOT DISTINCT FROM keeper_version)
"""

# THE SAFETY CHECK, in the batch's own transaction after its delete: the KEYS left for the
# batch's listing range. Fewer than the batch kept means a key lost its keeper; the batch rolls
# back and the run stops. `>=`, not `=`: the live intake may add a key meanwhile.
_KEYS_SQL = """
    SELECT count(*) FROM (SELECT DISTINCT listing_id, claim_type, licence_class
                            FROM location_claims
                           WHERE listing_id > %(after)s AND listing_id <= %(through)s) k
"""


class KeyLost(RuntimeError):
    """A batch would have left a key with no claim; nothing of it was committed."""


@dataclass
class Totals:
    keys: int = 0
    surplus: int = 0
    kept_retired: int = 0
    deleted: int = 0
    batches: int = 0
    cursor: int = 0
    collapsed: list[int] = field(default_factory=list)


def _batch(conn: psycopg.Connection, after: int, listings: int,
           armed: bool) -> tuple[int | None, int, int, int, int, list[int]]:
    """One batch transaction -> (through, keys, surplus, kept retired, deleted, collapsed)."""
    with conn.transaction():
        with conn.cursor() as cur:
            for timeout in _BATCH_TIMEOUTS_SQL:
                cur.execute(timeout)
            cur.execute(_BATCH_SQL, {"after": after, "listings": listings, "armed": armed})
            through, keys, surplus, retired, deleted, collapsed = cur.fetchone()
            if armed and through is not None:
                cur.execute(_KEYS_SQL, {"after": after, "through": through})
                left = int(cur.fetchone()[0])
                if left < int(keys):
                    raise KeyLost(f"listings ({after}, {through}]: {keys} keys kept, {left} "
                                  f"left after the delete; the batch rolled back")
    return (None if through is None else int(through), int(keys), int(surplus), int(retired),
            int(deleted), [int(i) for i in collapsed])


def purge(conn: psycopg.Connection, totals: Totals, *, armed: bool,
          listings: int = DEFAULT_LISTINGS, pause: float = DEFAULT_PAUSE_SECONDS,
          max_seconds: float = DEFAULT_MAX_SECONDS) -> bool:
    """Walk every listing from `totals.cursor`, adding to `totals` batch by batch (so a run
    that dies still reports what it committed). Returns whether the walk reached the end."""
    started = time.monotonic()
    while True:
        if time.monotonic() - started >= max_seconds:
            LOG.warning("PURGE budget spent after %d batches; resume with "
                        "--after-listing-id %d", totals.batches, totals.cursor)
            return False
        for attempt in range(1, LOCK_RETRIES + 1):
            try:
                result = _batch(conn, totals.cursor, listings, armed)
                break
            except psycopg.Error as exc:
                if exc.sqlstate not in _LOCK_SQLSTATES or attempt == LOCK_RETRIES:
                    raise
                LOG.warning("PURGE lock lost at cursor %d (attempt %d): %s",
                            totals.cursor, attempt, exc.sqlstate)
                time.sleep(2.0 * attempt)
        through, keys, surplus, retired, deleted, collapsed = result
        if through is None:
            LOG.info("PURGE walk complete at cursor %d", totals.cursor)
            return True
        totals.cursor = through
        totals.keys += keys
        totals.surplus += surplus
        totals.kept_retired += retired
        totals.deleted += deleted
        totals.collapsed.extend(collapsed)
        totals.batches += 1
        if totals.batches % PROGRESS_EVERY == 0:
            LOG.info("PURGE batch=%d cursor=%d keys=%d surplus=%d deleted=%d collapsed=%d "
                     "%.0fs", totals.batches, totals.cursor, totals.keys, totals.surplus,
                     totals.deleted, len(totals.collapsed), time.monotonic() - started)
        if pause:
            time.sleep(pause)


def write_receipt(path: str, listing_ids: list[int]) -> None:
    """The collapsed listings, one id a line, gzipped (W3 rollout step 5 re-mines them)."""
    with gzip.open(path, "wt", encoding="utf-8") as out:
        out.write("listing_id\n")
        out.writelines(f"{i}\n" for i in sorted(set(listing_ids)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--confirm", default="",
                        help="type PURGE to arm the delete; anything else is a dry run")
    parser.add_argument("--dry-run", action="store_true", help="walk and count; delete nothing")
    parser.add_argument("--listings-per-batch", type=int, default=DEFAULT_LISTINGS)
    parser.add_argument("--after-listing-id", type=int, default=0, help="resume cursor")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE_SECONDS)
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--receipt", default="", help="gzipped CSV of the collapsed listings")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    armed = args.confirm == "PURGE" and not args.dry_run
    LOG.info("PURGE %s from listing id %d, %d listings a batch",
             "ARMED" if armed else "dry run", args.after_listing_id, args.listings_per_batch)
    totals, complete, code = Totals(cursor=args.after_listing_id), False, 1
    try:
        with connect() as conn:
            complete = purge(conn, totals, armed=armed, listings=args.listings_per_batch,
                             pause=args.pause, max_seconds=args.max_seconds)
        code = 0 if complete else 2
    except KeyLost as exc:
        LOG.error("PURGE stopped: %s", exc)
        code = 3
    finally:
        if args.receipt:
            write_receipt(args.receipt, totals.collapsed)
    LOG.info("PURGE summary armed=%s complete=%s cursor=%d batches=%d keys=%d surplus=%d "
             "deleted=%d kept_retired_version=%d collapsed_listings=%d", armed, complete,
             totals.cursor, totals.batches, totals.keys, totals.surplus, totals.deleted,
             totals.kept_retired, len(set(totals.collapsed)))
    return code


if __name__ == "__main__":
    sys.exit(main())
