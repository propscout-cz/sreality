-- 730_health_checks_large_compute_indexes.sql
--
-- Two indexes for reads that walked a whole table, found when the operator ran the database on the Large compute
-- (8 GB) for 7 h on 2026-11-04 and the hot tables no longer fit in memory. Additive; nothing reads a new column.
--
--   1. property_merge_events_created_idx: the dedup check's id floor (scripts/checks/dedup.py _MERGES_SQL) is the
--      newest merge row older than 14 d + 1 h. No index led with created_at, so it walked the primary key backwards
--      through the whole span: 209,565 rows, 54,869 buffers on 11-05, half the check's statement. With this index it
--      is one descent (`where created_at < X order by created_at desc limit 1`).
--   2. location_claim_batches_running_idx: the location-intake-fast lane's yield read, every minute
--      (location_data/claims_intake.py _RUNNING_BATCH_SQL: the newest 'running' batch of a lane started within 65 min).
--      The never-pruned table (74k rows) had no index on lane or outcome, so the read scanned all of lcb_version (689
--      buffers) to find nothing. Partial on outcome = 'running' (137 rows, the stamps of killed runs, which never
--      finish), keyed for its ORDER BY id DESC, with started_at carried so the stale stamps are skipped inside the
--      index. A batch's finish (outcome leaves 'running') is no longer a HOT update: about one a minute.
--
-- APPLY through apply_migration.yml (--ref the branch) BEFORE the code merges: without index 1 the new floor is a
-- parallel seq scan of the 426 MB heap every ten minutes (EXPLAIN, 11-05), worse than the walk it replaces. Never
-- through the MCP: CREATE INDEX CONCURRENTLY cannot run in a transaction block, and every MCP path wraps its payload
-- in one. The builds take only SHARE UPDATE EXCLUSIVE (no read or write waits on them), scan the heap twice and wait
-- for every older transaction, so they run with lock_timeout 0 under a 1900 s budget (703's idiom); a cancelled
-- build's INVALID leftover is moved aside and dropped CONCURRENTLY on the re-run. Every statement is idempotent.
--
-- ROLLBACK: DROP INDEX CONCURRENTLY public.property_merge_events_created_idx;
--           DROP INDEX CONCURRENTLY public.location_claim_batches_running_idx;

set lock_timeout = '5s';

do $invalid$
begin
  if exists (select 1 from pg_index i
              where i.indexrelid = to_regclass('public.property_merge_events_created_idx')
                and not i.indisvalid) then
    alter index public.property_merge_events_created_idx rename to property_merge_events_created_idx_invalid;
    raise notice '730: an INVALID property_merge_events_created_idx from a cancelled build moved aside';
  end if;
  if exists (select 1 from pg_index i
              where i.indexrelid = to_regclass('public.location_claim_batches_running_idx')
                and not i.indisvalid) then
    alter index public.location_claim_batches_running_idx rename to location_claim_batches_running_idx_invalid;
    raise notice '730: an INVALID location_claim_batches_running_idx from a cancelled build moved aside';
  end if;
end
$invalid$;

set lock_timeout = 0;
set statement_timeout = '1900s';

drop index concurrently if exists public.property_merge_events_created_idx_invalid;
drop index concurrently if exists public.location_claim_batches_running_idx_invalid;

create index concurrently if not exists property_merge_events_created_idx
  on public.property_merge_events (created_at);

create index concurrently if not exists location_claim_batches_running_idx
  on public.location_claim_batches (lane, id desc) include (started_at)
  where outcome = 'running';

reset statement_timeout;
reset lock_timeout;
