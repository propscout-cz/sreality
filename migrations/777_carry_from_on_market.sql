-- The carry record keeps whether the property a row came from was on the market at the merge (#2583, spec #2574).
--
-- Back on the market must be told to a collection that held a property OFF the market and not to one whose
-- membership a merge moved from a live property (api/notifications._HELD_AT_THE_RETURN). That is a fact of
-- the merge instant, and the retired property row cannot keep it (a detach revives the row and it moves on),
-- so it goes on the append-only record the merge already writes per moved or folded curation row:
--
--   property_merge_carries + from_on_market boolean: the from-property's is_active as the merge saw it,
--   written by toolkit/property_identity._CARRY_SQL in the merge transaction, never updated. Nullable, no
--   default: catalog-only, no rewrite, no backfill. NULL = a row older than this column: unknown.
--
-- Order: BEFORE the code of #2669 deploys (its INSERT names the column). Additive; nothing reads it until then.
-- Undo: drop the column (after reverting the code).

set lock_timeout = '5s';

alter table public.property_merge_carries add column if not exists from_on_market boolean;

comment on column public.property_merge_carries.from_on_market is
  'Whether the property this row came from (from_property_id) was on the market as the merge saw it '
  '(migration 777): its is_active before the retire. Written once in the merge transaction; NULL on rows '
  'older than the column. Back on the market reads it (api/notifications._HELD_AT_THE_RETURN).';
