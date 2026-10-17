-- 608: `account_id` NOT NULL on `collections` and `tags`.
--
-- WHY. Migration 290 added `account_id` to the six curation tables while they already held rows,
-- before the first-signup backfill (294's `backfill_legacy_account_id`) could stamp them, so the
-- column had to be nullable. Two of the six have been NOT NULL since: `notification_subscriptions`
-- (364) and `filter_presets` (595). This file closes two more. Every writer supplies the account
-- (read 2026-10-17):
--   * api/curation.py create_collection / create_tag: `account_id` is a REQUIRED parameter,
--     resolved by `tenant_pool.require_account_id` at the route edge;
--   * the signup trigger's `monitoring` collection insert, stamped with the new account.
-- The tenancy reference (database skill, references/tenancy.md) already called these columns
-- NOT NULL; from this migration that is true for these two.
--
-- NOT HERE. `property_notes`: one pre-tenancy row (id 2, 2026-07-14) still has no account; PR
-- #1829's migration attributes it (and its merge carry row), and the NOT NULL waits on the
-- operator's ruling about the split preview's bare-note case that PR names.
-- `manual_rental_estimates`: its `account_id` is provenance only (290: reads are platform-wide,
-- `ON DELETE SET NULL`), and an admin with no account membership records NULL by design
-- (tests/api/test_account_scope_census.py names that write).
--
-- DATA. Live 2026-10-16: 0 NULL rows in `collections` and `tags`. The CI replay simulates the
-- first signup before 295 (migrations.yml), so 211's seed collection is claimed there too and
-- reaches this file owned. Any NULL stops the apply rather than guessing an owner.
--
-- LOCKS. SET NOT NULL scans the table under ACCESS EXCLUSIVE; both tables hold <= 15 rows, so the
-- scan is instant, and the lock_timeout makes a stuck apply fail instead of queueing behind a
-- long reader. One transaction: the column is never NOT NULL on one table and not the other.
--
-- ROLLBACK: forward only (rule 1). A re-run is a no-op: nothing is NULL, so the checks pass and
-- SET NOT NULL on a NOT NULL column changes nothing.

set lock_timeout = '5s';

begin;

do $$
begin
  if exists (select 1 from collections where account_id is null) then
    raise exception 'collections still has NULL account_id rows';
  end if;
  if exists (select 1 from tags where account_id is null) then
    raise exception 'tags still has NULL account_id rows';
  end if;
end $$;

alter table collections alter column account_id set not null;
alter table tags        alter column account_id set not null;

commit;
