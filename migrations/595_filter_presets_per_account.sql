-- 595_filter_presets_per_account.sql
--
-- Browse filter presets become per account (ticket #1761, Phone app map #1759, W1's first
-- slice). Migration 290 already gave `filter_presets` its `account_id` column, RLS and the
-- `filter_presets_tenant_rw` policy, and 294's first-signup backfill stamped every preset that
-- existed then; but the `/filter-presets` routes kept the static bundle token and the
-- service-role connection, so the policy never applied and a preset saved since would land
-- with a NULL owner. The routes move onto the caller's JWT + `tenant_conn` in the same PR
-- (`api/routes/filter_presets.py`); this migration closes the schema side.
--
-- Live state read 2026-10-12 before writing this: 7 presets, 0 with a NULL account_id, all
-- owned by account e93e2b77-56f1-4a56-903b-204e4f0e4e90 — the operator's personal account,
-- the one that won `legacy_backfill_claim` ('legacy_backfill_v1') on 2026-07-11. The newest
-- preset predates that claim, so no preset has yet been saved through the old NULL-owner path.
--
-- The code does not read anything this migration adds (RLS + the policy are 290's), so it is
-- safe to apply after the merge as well as before; applied after, step 1 also catches any
-- preset the old service-role route saves in between.
--
-- Additive, statement-autocommit (apply_migration.yml runs `psql -f`), every step idempotent.

set lock_timeout = '5s';

-- 1. Any ownerless preset is the operator's: presets were a single-operator surface until this
--    change. A literal account, not a runtime guess (there are two platform admins).
update filter_presets
   set account_id = 'e93e2b77-56f1-4a56-903b-204e4f0e4e90'
 where account_id is null;

-- 2. Re-assert 290's tenant policy in place (ALTER POLICY: no unpoliced window, cannot drop
--    `to authenticated`, and it fails loudly if the policy were ever missing).
alter table filter_presets enable row level security;

alter policy filter_presets_tenant_rw on public.filter_presets
  to authenticated
  using (account_id in (select current_account_ids()))
  with check (account_id in (select current_account_ids()));

-- 3. Every preset has an owner now; the schema enforces it from here on.
alter table filter_presets alter column account_id set not null;

comment on column filter_presets.account_id is
    'The owning account (migration 595: NOT NULL). Set by POST /filter-presets from the '
    'caller''s JWT (tenant_pool.require_account_id); reads, updates and deletes are scoped by '
    'the filter_presets_tenant_rw policy (migration 290).';
