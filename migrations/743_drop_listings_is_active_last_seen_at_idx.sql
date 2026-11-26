-- 743_drop_listings_is_active_last_seen_at_idx.sql -- listings (#2511, spec #2503; operator ruling 2026-11-16: drop it).
--
-- WHY: listings_is_active_last_seen_at_idx (001's unnamed `(is_active, last_seen_at)`, 125 MB) is the one listings index
--   that holds last_seen_at. Every index-walk touch (scraper/db.py _touch_chunk_by_id) sets last_seen_at = now() on each
--   sighted ad, so no touch can be a HOT update: 1.40M of listings' 352.5M updates were HOT (0.4 %, cumulative counters,
--   read 2026-11-16). Without it, a touch of an active ad changes no indexed column (is_active and inactive_at are
--   rewritten to the values they already hold; listings has no trigger), so it is HOT-eligible wherever its page has
--   room (fillfactor stays 100).
--
-- READERS (production, 2XL, warm, EXPLAIN ANALYZE 2026-11-16; "without" = the plan once the planner cannot use it):
--   * The delisting orphan nomination, db.orphan_candidates, the only read whose last_seen_at bound is selective (a
--     BitmapOr): sreality 110 ms / 5,675 buffers -> 406 ms / 81,062 (1.29 s partly cold, 9,519 of them read from disk);
--     idnes 81 ms / 3,334 -> 396 ms / 80,910; the same rows. The count that runs first in the same call (db._count)
--     already reads those rows: 404 ms / 80,741. A failure is caught (VERIFY orphans failed: no orphan that cycle).
--   * Every other read that used it is a read of the whole active set: an index on is_active alone (any last_seen_at
--     bound it adds keeps every active row). Each such read switches to a bitmap scan of the partial
--     listings_subtype_active_idx (WHERE is_active), the same rows in about the same time. Read in the plans: Health
--     scraping's unnominated count (delisting) 821 ms / 368k buffers -> 944 ms / 179k; Coverage's held count 395 ms /
--     361k -> 444 ms / 176k; the location_position_mv refresh (4 a day); comparables with max_age_days (a BitmapAnd arm,
--     124.6k buffers either way); the worker's location-refetch lane (db.location_refetch_candidates, whose cost is its
--     per-row raw-page lookups); the parked admin condition-scoring count. Same results. That list is what a search
--     found, not a census: the class is the rule.
--   * presence_candidates, the in-dwell reads and the property rollup do not use it (PG 17: no skip scan, so a read
--     with no is_active predicate never could).
--   KEEP the partial `where is_active` indexes (listings_subtype_active_idx, listings_category_sub_cb_active_idx): they
--   now carry every whole-active-set read. Re-read those plans before dropping either.
--
-- APPLY through apply_migration.yml (--ref the branch), never the MCP: DROP INDEX CONCURRENTLY cannot run in a
-- transaction block. ORDER: apply -> the receipt reads OK -> merge. No code reads the index, so either order is safe
-- for the code; merged unapplied, the hourly migration_drift cell reads Watch until it is applied. A concurrent drop
-- takes SHARE UPDATE EXCLUSIVE (no read or write waits on it) and then waits for every transaction holding a lock on
-- listings, so it runs with lock_timeout 0 under a 1900 s budget (730's idiom); keep clear of the 03:00 daily sweep and
-- the :45 Browse rebuild. A drop cancelled in its first wait leaves an INVALID index that writes still maintain (no
-- worse than today); one cancelled in its second wait leaves a dead index that writes no longer maintain. Both are
-- harmless, and the idempotent re-run drops either.
--
-- ROLLBACK: forward only (rule 1), a new file holding the live definition of 2026-11-16, outside a transaction block
-- (it reads the 1.7 GB heap twice):
--   create index concurrently if not exists listings_is_active_last_seen_at_idx
--     on public.listings using btree (is_active, last_seen_at);

set lock_timeout = 0;
set statement_timeout = '1900s';

drop index concurrently if exists public.listings_is_active_last_seen_at_idx;

reset statement_timeout;
reset lock_timeout;
