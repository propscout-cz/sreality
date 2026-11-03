-- 721_location_w8_name_index_street_rows.sql -- W8-1: the gazetteer's street rows leave.
--
-- WHY. Location data quality programme, wave W8 (ticket #2362; docs/design/location-quality/PROGRAM.md).
-- ruian_name_index holds about 607,522 rows of entity_kind 'ulice' that nothing reads. Both readers
-- (location_data/resolver/resolve_db.py _ADMIN_BY_NAME_SQL, api/maps.py _PART_LOOKUP_SQL) join
-- ruian_admin_units on `u.level = n.entity_kind`, and the mirror loads no admin unit at level 'ulice'
-- (ruian_load.LEVEL_ORDER). Streets bind through ruian_streets.name_norm. The loader stopped writing
-- these rows in this PR (name_index.collect_rows), so this removes every version's copy once.
--
-- KEPT: the 'ulice' value of the ruian_level enum. Postgres cannot drop an enum value in place: it
-- would mean rebuilding the type under three tables, for a value no writer uses any more.
--
-- DESTRUCTIVE, on a REBUILDABLE table (migration 381): back up to R2 FIRST, read the receipt back:
--   gh workflow run backup_before_drop.yml -f label=w8-name-index-streets -f targets="ruian_name_index"
-- APPLY AFTER THE W8-1 DEPLOY IS CONFIRMED (Railway worker + api) and with NO registry load running:
-- the pre-W8 loader writes street rows into every version it rebuilds. Through apply_migration.yml
-- (psql -f, statement autocommit); the statement is idempotent, so a lock_timeout trip is rerun as is.
-- No VACUUM FULL: autovacuum reclaims the space.
-- Receipt: about 607,522 rows deleted.
-- VERIFY:
--   select count(*) from ruian_name_index where entity_kind = 'ulice';  -- 0

set lock_timeout = '5s';

delete from ruian_name_index where entity_kind = 'ulice';
