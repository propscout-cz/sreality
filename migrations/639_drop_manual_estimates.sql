-- 639_drop_manual_estimates.sql -- the "Ruční odhady" (manual rental estimates) storage goes.
--
-- WHY. Operator ruling 2026-10-19 (Valuo sprint, ruling 7): the feature is retired end to end. Its code (the
-- property-page and phone block, api/manual_estimates.py and its four routes, POST /tools/get_manual_rental_estimates,
-- toolkit/manual_estimates.py, the agent tool) is deleted in the same PR, and 638 took the tool out of the skills.
-- Neither table has ever held a row (verified live 2026-10-19).
--
-- ===========================================================================
-- DESTRUCTIVE. Applied only with the operator's explicit OK and AFTER a backup:
--   gh workflow run backup_before_drop.yml -f label=manual-rental-estimates \
--     -f targets="manual_rental_estimates manual_rental_estimates_history"
-- and only AFTER the code is gone (the PR merged and its API rollout finished): an API still serving the old routes
-- would hit a missing relation. 638 is applied before the merge; this one after it. Forward only (rule 1): there is
-- no restore and none is wanted.
-- ===========================================================================
--
-- WHAT GOES (046, widened by 290/323/327/337/361/431/611)
--   1. view public.manual_rental_estimates_public.
--   2. table public.manual_rental_estimates_history.
--   3. table public.manual_rental_estimates, and with it its two history triggers, its three RLS policies, its
--      indexes, its identity sequence and its foreign keys (account_id -> accounts; on production also the
--      listing_id -> listings key that scripts/apply_r2_constraints.py added outside migrations).
--   4. function public.manual_rental_estimates_record_history(), dropped after the table that triggered it.
-- No CASCADE: anything else that still depends on these must fail the apply, not vanish with it.
-- After this file the unapplied 431 revert (migrations/reverts/431_revert_*.sql) no longer runs as written: its two
-- `alter policy manual_rental_estimates_admin_*` statements name this dropped table and abort its transaction, so
-- leave those two statements out if that revert is ever needed.
--
-- LOCKS. Dropping a table drops its foreign keys, which takes a brief lock on the referenced `listings` and
-- `accounts`. The 5 s lock_timeout makes the drop give up instead of queueing at the head of listings' lock queue;
-- on a timeout, re-run: every statement is idempotent (`if exists`), so the CI replay runs it too.

set lock_timeout = '5s';
set statement_timeout = '60s';

begin;

drop view if exists public.manual_rental_estimates_public;
drop table if exists public.manual_rental_estimates_history;
drop table if exists public.manual_rental_estimates;
drop function if exists public.manual_rental_estimates_record_history();

do $post$
begin
  if to_regclass('public.manual_rental_estimates') is not null
     or to_regclass('public.manual_rental_estimates_history') is not null
     or to_regclass('public.manual_rental_estimates_public') is not null then
    raise exception '639 did not land: a manual_rental_estimates relation still exists';
  end if;
  if exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
              where n.nspname = 'public' and p.proname = 'manual_rental_estimates_record_history') then
    raise exception '639 did not land: manual_rental_estimates_record_history() still exists';
  end if;
end
$post$;

commit;

reset statement_timeout;
reset lock_timeout;
