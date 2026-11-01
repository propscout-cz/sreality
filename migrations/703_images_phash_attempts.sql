-- 703_images_phash_attempts.sql
--
-- Actions exit R6a (#2018): the hourly compute_image_phash job leaves GitHub for a bounded retry inside the worker's
-- images lane (scraper/image_phash.py, retry_unhashed). The job had no memory of a failure: its last run logged
-- pending=203 hashed=0 errors=203, each `image file is truncated`, and the same rows came back every hour. The retry
-- counts each row's failures and stops at a cap.
--
--   1. images.phash_attempts: failures of the retry on this row. A constant default is catalog-only (no heap rewrite);
--      the ALTER's ACCESS EXCLUSIVE is momentary and the 5 s limit fails fast instead of queueing the table behind it.
--   2. images_phash_retry_idx, the retry's only path to its rows: no index serves `phash IS NULL` on the 15.8 M row,
--      3.3 GB heap, and the statement runs every 60 s. It holds only rows with work left (about 200 at the build, then
--      a stored image's inline miss until it is hashed or capped), so an idle pass reads an empty index.
--      5 is scraper.image_phash.PHASH_MAX_ATTEMPTS (a test pins them); changing the cap means a new index.
--
-- APPLY through apply_migration.yml (--ref the branch) BEFORE the code merges (the lane reads the column every pass),
-- never through the MCP: CREATE INDEX CONCURRENTLY cannot run in a transaction block, and every MCP path wraps its
-- payload in one. The build takes only SHARE UPDATE EXCLUSIVE (no read or write waits on it), scans the heap twice and
-- waits for every older transaction, so it runs with lock_timeout 0 under a 1900 s budget (635's idiom); a cancelled
-- build's INVALID leftover is moved aside and dropped CONCURRENTLY on the re-run. Every statement is idempotent.
--
-- ROLLBACK: DROP INDEX CONCURRENTLY public.images_phash_retry_idx; ALTER TABLE public.images DROP COLUMN phash_attempts;
-- A capped row is retried again by: UPDATE images SET phash_attempts = 0 WHERE id = ...;

set lock_timeout = '5s';

alter table public.images add column if not exists phash_attempts smallint not null default 0;

do $invalid$
begin
  if exists (select 1 from pg_index i
              where i.indexrelid = to_regclass('public.images_phash_retry_idx')
                and not i.indisvalid) then
    alter index public.images_phash_retry_idx rename to images_phash_retry_idx_invalid;
    raise notice '703: an INVALID images_phash_retry_idx from a cancelled build moved aside';
  end if;
end
$invalid$;

set lock_timeout = 0;
set statement_timeout = '1900s';

drop index concurrently if exists public.images_phash_retry_idx_invalid;

create index concurrently if not exists images_phash_retry_idx
  on public.images (id)
  where phash is null and storage_path is not null and phash_attempts < 5;
