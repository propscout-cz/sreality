-- A sale's building and unit, read from its own key (#2592, spec #2590).
--
-- A reas.cz record's key is `<transferId>_unit_<buildingId>-<cp>-<unitNo>` or
-- `<transferId>_building_<buildingId>` (scraper/reas_parser.py). `buildingId` is the RÚIAN building
-- code (checked against the register, 2026-11-22: 3 of 3), so a sale already names its building and,
-- for a flat, its unit; nothing read them. Two generated columns state them once, for every row held
-- (2,460 on 2026-11-22: 1,653 unit keys, 807 building keys, none of another shape) and every later
-- one, with no writer to keep in step.
--
-- `unit_label` is the cadastre's own form, "<č.p.>/<unit number>".
--
-- Additive; the table is small, so the rewrite the stored columns cause is brief.
set lock_timeout = '5s';

alter table public.sold_transactions
  add column if not exists building_code bigint generated always as (
    case when source = 'reas'
         then (substring(source_record_id from '^\d+_(?:unit|building)_(\d+)'))::bigint end) stored,
  add column if not exists unit_label text generated always as (
    case when source = 'reas'
         then replace(substring(source_record_id from '^\d+_unit_\d+-(\d+-.+)$'), '-', '/') end) stored;

create index if not exists sold_transactions_building_idx
  on public.sold_transactions (building_code, sold_at desc) where building_code is not null;
