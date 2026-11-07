-- 721_location_w8_name_index_street_rows.sql -- W8-1 (#2362): the gazetteer's street rows leave.
-- Nothing reads them: both readers join ruian_admin_units on `u.level = n.entity_kind`, which holds no
-- 'ulice' unit, and streets bind through ruian_streets.name_norm. The loader stops writing them in this PR.
-- The enum keeps 'ulice' (Postgres cannot drop a value in place).
-- DESTRUCTIVE. Backup: backup_before_drop.yml run 41703654693, label w8-1-name-index-street-rows,
-- 869,165 rows. Apply through apply_migration.yml after the deploy, with no registry load running
-- (the old loader writes them back). Idempotent: rerun as is after a lock_timeout trip.
-- Receipt: 607,522 rows (11-03). VERIFY: select count(*) from ruian_name_index where entity_kind = 'ulice';  -- 0

set lock_timeout = '5s';

delete from ruian_name_index where entity_kind = 'ulice';
