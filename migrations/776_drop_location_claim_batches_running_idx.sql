-- 776_drop_location_claim_batches_running_idx.sql -- migration 730's yield index loses its one reader (#2419).
--
-- 730 built location_claim_batches_running_idx for one read: the worker's minute lane yielding to a running GitHub
-- intake (claims_intake._RUNNING_BATCH_SQL, every minute). #2028 (PR #2414, deployed 2026-11-26 10:49Z) moved the
-- intake to the job runner and deleted that read with the yield. No other query can use the index: it is partial on
-- outcome = 'running', and every remaining read of the table filters outcome IN ('ok', 'stopped', 'failed') or
-- updates by id (claims_intake.py); no view, function, matview or cron job names the table. It still costs a write:
-- a batch's finish (outcome leaving 'running') is not a HOT update while it exists, about one a minute.
--
-- Not data: the index is rebuilt from the table by 730's own statement (ROLLBACK below). APPLY through
-- apply_migration.yml (--ref the branch), never the MCP: DROP INDEX CONCURRENTLY cannot run in a transaction block.
-- The drop takes SHARE UPDATE EXCLUSIVE (no read or write waits on it) and waits for older transactions on the
-- table, so lock_timeout 0 under a bounded statement_timeout, as 730. Idempotent; 730's moved-aside INVALID
-- leftover goes too.
--
-- ROLLBACK: create index concurrently if not exists location_claim_batches_running_idx
--             on public.location_claim_batches (lane, id desc) include (started_at) where outcome = 'running';

set lock_timeout = 0;
set statement_timeout = '600s';

drop index concurrently if exists public.location_claim_batches_running_idx;
drop index concurrently if exists public.location_claim_batches_running_idx_invalid;

reset statement_timeout;
reset lock_timeout;

do $post$
begin
  if to_regclass('public.location_claim_batches_running_idx') is not null
     or to_regclass('public.location_claim_batches_running_idx_invalid') is not null then
    raise exception '776: location_claim_batches_running_idx is still there';
  end if;
end
$post$;
