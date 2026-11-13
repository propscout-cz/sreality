-- 739_mapy_stage_a_drops.sql -- Mapy exit, Stage A (#2454; spec #2441 rev 2, Part 6): Mapy output leaves the database.
--   * curated_cities.centroid, source and source_confidence are Mapy's geocode of each curated city (206 rows, every
--     one source 'mapy_cz'), never backed up; the centroid's gist index goes with them. Nothing reads them: city
--     filters, city-quality chips and outlines read the city's RÚIAN code (obec_kod, 726) and the mirror's polygons,
--     and the seed writes index revisions for existing cities only.
--   * backup_w4, 511's copy of what 506 and 508 dropped (10 tables, about 594 MB), outlived its week and holds Mapy
--     answers. It goes uncopied (ruling 2026-11-07): its sources are held (every one of its 832,611 listing rows is
--     still in listings, rule 3; all 57,204 Mapy-affected listings keep their payload), and 50,062 of those listings'
--     55,538 pins fail the code's own licence ladder (location_data.claims_common.coordinate_verdict).
--   * The plain obec_kod index becomes unique (0 duplicates): a second curated city on one town is refused, so a new
--     city arrives as a one-line data migration checked against the register, never by name.
-- curated_cities_public is unchanged; 511, 726 and 729 are untouched. No CASCADE: RESTRICT refuses a drop while a view,
-- a matview or a foreign key still reads what goes, and DROP SCHEMA refuses while anything else is left in it. $pre$
-- adds the one check pg_depend cannot do: a function body naming source_confidence, the centroid index or backup_w4,
-- or naming curated_cities with a whole-word centroid or source (0 today). $post$ verifies on every replay and apply.
-- DESTRUCTIVE, backup waived (spec #2441 Part 6: no Mapy output is kept; backup_w4's sources are held). Applied by
-- apply_migration.yml while its PR is open, after the operator read the evidence query and said "yes, apply it", then
-- the PR merges. Neither seed writes a dropped column after the apply: the deployed one exits before it connects, the
-- new one never names them. One transaction: a lock_timeout trip rolls it all back; rerun as is.
-- VERIFY: select to_regnamespace('backup_w4'), to_regclass('public.curated_cities_centroid_idx');  -- both NULL

begin;
set local lock_timeout = '5s';

do $pre$
declare
  found text;
begin
  select string_agg(p.oid::regprocedure::text, ', ') into found
    from pg_proc p
   where p.pronamespace = 'public'::regnamespace
     and (p.prosrc ~ '\m(source_confidence|curated_cities_centroid_idx|backup_w4)\M'
          or (p.prosrc ~ '\mcurated_cities\M' and p.prosrc ~ '\m(centroid|source)\M'));
  if found is not null then raise exception '739 refused: % still name what this file drops', found; end if;
end
$pre$;

drop index if exists curated_cities_centroid_idx;
alter table curated_cities drop column if exists centroid,
                           drop column if exists source,
                           drop column if exists source_confidence;
create unique index if not exists curated_cities_obec_kod_key on curated_cities (obec_kod);
drop index if exists curated_cities_obec_kod_idx;

drop table if exists backup_w4.address_points;
drop table if exists backup_w4.address_points_revisions;
drop table if exists backup_w4.geocode_cache;
drop table if exists backup_w4.listings_cols_508;
drop table if exists backup_w4.mapy_affected;
drop table if exists backup_w4.mapy_affected_cache;
drop table if exists backup_w4.mapy_affected_props;
drop table if exists backup_w4.mapy_inventory_runs;
drop table if exists backup_w4.properties_cols_506;
drop table if exists backup_w4.properties_cols_508;
drop schema if exists backup_w4;

do $post$
begin
  if to_regnamespace('backup_w4') is not null then
    raise exception '739 did not land: schema backup_w4 is still here';
  end if;
  if exists (select 1 from pg_attribute
              where attrelid = 'public.curated_cities'::regclass and not attisdropped
                and attname in ('centroid', 'source', 'source_confidence')) then
    raise exception '739 did not land: a dropped curated_cities column is still here';
  end if;
  if coalesce(to_regclass('public.curated_cities_centroid_idx'), to_regclass('public.curated_cities_obec_kod_idx'))
       is not null then
    raise exception '739 did not land: a dropped index is still here';
  end if;
  if not exists (select 1 from pg_index i join pg_attribute a on a.attrelid = i.indrelid and a.attnum = i.indkey[0]
                  where i.indrelid = 'public.curated_cities'::regclass and i.indisunique and i.indnatts = 1
                    and i.indpred is null and a.attname = 'obec_kod') then
    raise exception '739 did not land: curated_cities.obec_kod has no unique index';
  end if;
  if (select string_agg(attname, ',' order by attnum) from pg_attribute
       where attrelid = 'public.curated_cities_public'::regclass and attnum > 0 and not attisdropped)
     is distinct from 'city_id,name,kraj_name,population,population_as_of_year,obec_kod' then
    raise exception '739 did not land: curated_cities_public changed';
  end if;
end
$post$;

commit;
