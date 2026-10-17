-- 613_drop_legacy_backfill_claim.sql
-- The first-signup claim table is dropped (operator ruling 2026-10-17).
--
-- legacy_backfill_claim was migration 294's one-row ticket: the first signup
-- took it and inherited every pre-tenancy row, every later signup found it
-- taken and got a fresh board. Migration 612 removed the branch that read it
-- and used the row one last time, to give the orphan note its owner. Nothing
-- reads the table now; the guards below refuse to run before 612 has landed.
--
-- BACKUP (the table's whole content, read live before the drop):
--   claim_key  = 'legacy_backfill_v1'
--   account_id = e93e2b77-56f1-4a56-903b-204e4f0e4e90   (the operator's account)
--   claimed_at = 2026-07-11 22:24:50.070494+00
-- Destructive under the database skill (DROP TABLE), operator-approved.
-- Forward only (rule 1): recovery = CREATE TABLE (migration 294) + one INSERT
-- of the row above.

set lock_timeout = '5s';

begin;

do $$
begin
  if pg_get_functiondef('public.handle_new_user()'::regprocedure)
       like '%legacy_backfill_claim%' then
    raise exception '613: apply 612 first (handle_new_user still reads the claim)';
  end if;
  if exists (select 1 from property_notes where account_id is null) then
    raise exception '613: apply 612 first (the orphan note is still unowned)';
  end if;
end $$;

drop table if exists legacy_backfill_claim;

commit;
