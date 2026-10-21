-- 646_valuo_valuation_adverts.sql -- The photos of a Valuo valuation's comparables, kept as Valuo said them.
--
-- WHY. Valuo v3's market-value answer names the comparables it used (`result.records[]`, docs.valuo.com) but
-- carries no photos; `POST /v1/advert/detail` answers them per advert (`images[]` as storage paths under the
-- token's include_raw_images entitlement, `hashed_images[]` without it). api/valuo.py makes that ONE call after
-- a successful valuation with records, for at most ten of them, before the row's terminal UPDATE, and stores
-- Valuo's array here verbatim beside `response` (migration 637), so a row stays one replayable record of what
-- Valuo said. NULL = not fetched (no records, a row from before this column) or the call failed: a failed photo
-- call never fails the valuation.
--
-- Additive. APPLY BEFORE MERGING (apply_migration.yml): api/valuo.py reads and writes the column. Idempotent:
-- a re-apply is a no-op. Adding a nullable column with no default is a catalog-only change; lock_timeout keeps
-- it from queueing behind a long reader.
-- Verify:
--   select data_type from information_schema.columns
--    where table_schema = 'public' and table_name = 'valuo_valuations' and column_name = 'adverts';  -- jsonb

set lock_timeout = '5s';

alter table public.valuo_valuations add column if not exists adverts jsonb;
comment on column public.valuo_valuations.adverts is
  'Valuo''s verbatim POST /v1/advert/detail answer for this valuation''s comparables (at most ten, its order). '
  'NULL = not fetched or the call failed. Written only by api/valuo.py, in the same terminal UPDATE as response.';
