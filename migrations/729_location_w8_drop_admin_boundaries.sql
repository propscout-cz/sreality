-- 729_location_w8_drop_admin_boundaries.sql -- W8-4 (#2362): admin_boundaries retires onto the RÚIAN mirror.
-- The ruling of 2026-10-31 (docs/design/location-quality/PROGRAM.md): the legacy table is removed entirely; the ticket's
-- §7 ledger. W8-1 to W8-3 left these unread (tests/test_admin_boundaries_unread.py), and this drops them:
--   * admin_boundaries (19,423 rows, 61 MB) and admin_boundaries_public; their FKs (the self parent FK, and
--     price_stat_localities.obec_id's) go with them. 726 dropped curated_cities' FK.
--   * price_stat_localities (6,258 rows; ruian_translation holds its keys since 722) and city_population (206 rows;
--     obec_population since 725).
--   * entity_type and entity_id on price_stat_observations, price_stat_locality_no_data and price_stat_city_metrics
--     (made nullable and unread by 723).
--   * curated_cities.default_radius_m (D8), and the view columns only the pre-W8-3 SPA read: curated_cities_public's
--     admin_boundary_id, lat, lng and default_radius_m, price_stat_obce_picker_public's sreality_id. Both views are
--     re-created without them (the picker as 725 defines it), authenticated SELECT only.
-- No CASCADE. $pre$ refuses before anything is dropped while: 728 is not applied; a view or matview (pg_rewrite) reads
-- a dropped relation, or a dropped column other than through the two views this file re-creates; an FK from a table
-- that stays points at a dropped table; a function body names admin_boundar*, price_stat_localities, city_population
-- or default_radius_m; or a function body names curated_cities_public or price_stat_obce_picker_public, bar 726's
-- curated_cities_matching (md5-pinned), since lat, lng and sreality_id are too generic to scan for.
-- DESTRUCTIVE. First: gh workflow run backup_before_drop.yml -f label=w8-4-drops -f targets="admin_boundaries
-- price_stat_localities city_population curated_cities:id,default_radius_m
-- price_stat_observations:dataset_id,obec_id,category_type_cb,year,month,entity_type,entity_id
-- price_stat_locality_no_data:dataset_id,obec_id,entity_type,entity_id
-- price_stat_city_metrics:dataset_id,obec_id,entity_type,entity_id", receipt read back. Apply AFTER the deploy (the
-- SPA it ships reads none of the view columns) and after 728, through apply_migration.yml (psql -f). One
-- transaction: a lock_timeout trip rolls it all back; rerun as is.
-- Receipt: 19,423 + 6,258 + 206 rows dropped with their tables; 7 columns.
-- VERIFY: select to_regclass('public.admin_boundaries'), to_regclass('public.admin_boundaries_public'),
--                to_regclass('public.price_stat_localities'), to_regclass('public.city_population');  -- all NULL

begin;
set local lock_timeout = '5s';

do $pre$
declare
  gone regclass[] := array_remove(array[to_regclass('public.admin_boundaries'),
                                        to_regclass('public.admin_boundaries_public'),
                                        to_regclass('public.price_stat_localities'),
                                        to_regclass('public.city_population')], null);
  found text;
begin
  if to_regprocedure('public.price_stat_growth_shapes(bigint)') is not null then
    raise exception '729 refused: apply 728 first (price_stat_growth_shapes still reads admin_boundaries_public)';
  end if;
  -- Views and matviews over what goes; admin_boundaries_public goes too.
  select string_agg(distinct c.oid::regclass || ' <- ' || d.refobjid::regclass, ', ') into found
    from pg_depend d join pg_rewrite r on r.oid = d.objid join pg_class c on c.oid = r.ev_class
   where d.refobjid = any(gone) and c.oid <> d.refobjid and c.oid <> all(gone);
  if found is not null then raise exception '729 refused: % still read what this file drops', found; end if;
  -- Views over the dropped columns, but the two views this file re-creates.
  select string_agg(distinct c.oid::regclass || ' <- ' || d.refobjid::regclass || '.' || a.attname, ', ') into found
    from pg_depend d join pg_rewrite r on r.oid = d.objid join pg_class c on c.oid = r.ev_class
    join pg_attribute a on a.attrelid = d.refobjid and a.attnum = d.refobjsubid
   where c.oid not in ('public.curated_cities_public'::regclass, 'public.price_stat_obce_picker_public'::regclass)
     and ((d.refobjid in ('public.price_stat_observations'::regclass, 'public.price_stat_locality_no_data'::regclass,
                          'public.price_stat_city_metrics'::regclass) and a.attname in ('entity_type', 'entity_id'))
          or (d.refobjid = 'public.curated_cities'::regclass and a.attname = 'default_radius_m'));
  if found is not null then raise exception '729 refused: % still read a column this file drops', found; end if;
  -- Foreign keys into what goes from a table that stays.
  select string_agg(conrelid::regclass || '.' || conname, ', ') into found
    from pg_constraint where contype = 'f' and confrelid = any(gone) and conrelid <> all(gone);
  if found is not null then raise exception '729 refused: % still references what this file drops', found; end if;
  -- Function bodies, which pg_depend does not see into: one naming what goes.
  select string_agg(p.oid::regprocedure::text, ', ') into found
    from pg_proc p
   where p.pronamespace = 'public'::regnamespace
     and p.prosrc ~ '\m(admin_boundar\w*|price_stat_localities|city_population|default_radius_m)\M';
  if found is not null then raise exception '729 refused: % still name what this file drops', found; end if;
  -- And one reading either re-created view, whose dropped columns (lat, lng, sreality_id) are names too generic to
  -- scan for: only 726's curated_cities_matching, which reads city_id and obec_kod, may.
  select string_agg(p.oid::regprocedure::text, ', ') into found
    from pg_proc p
   where p.pronamespace = 'public'::regnamespace
     and p.prosrc ~ '\m(curated_cities_public|price_stat_obce_picker_public)\M'
     and not (p.oid is not distinct from to_regprocedure('public.curated_cities_matching(jsonb)')
              and md5(p.prosrc) = '65457e93264c589c86155d7312012a3d');
  if found is not null then raise exception '729 refused: % read a view this file re-creates narrower', found; end if;
end
$pre$;

drop view if exists price_stat_obce_picker_public;
create view price_stat_obce_picker_public as
  select u.code as id, u.level::text as level, u.name, p.code as parent_id, pop.population
    from registry_versions v
    join ruian_admin_unit_geometries g on g.registry_version_id = v.id and g.purpose = 'render'
    join ruian_admin_units u on u.id = g.unit_id
    left join ruian_admin_units p on p.id = u.parent_id and p.level in ('kraj', 'okres')
    left join obec_population pop on pop.obec_kod = u.code and u.level = 'obec'
                                 and pop.year = (select max(year) from obec_population)
   where v.is_current and u.level in ('kraj', 'okres', 'obec');
revoke all on price_stat_obce_picker_public from public, anon, authenticated;
grant select on price_stat_obce_picker_public to authenticated;

drop view if exists curated_cities_public;
create view curated_cities_public as
  select c.id as city_id, c.name, c.kraj_name, p.population, p.year as population_as_of_year, c.obec_kod
    from curated_cities c
    left join obec_population p on p.obec_kod = c.obec_kod and p.year = (select max(year) from obec_population);
revoke all on curated_cities_public from public, anon, authenticated;
grant select on curated_cities_public to authenticated;

alter table curated_cities drop column if exists default_radius_m;
alter table price_stat_observations drop column if exists entity_type, drop column if exists entity_id;
alter table price_stat_locality_no_data drop column if exists entity_type, drop column if exists entity_id;
alter table price_stat_city_metrics drop column if exists entity_type, drop column if exists entity_id;
drop table if exists city_population;
drop table if exists price_stat_localities;
drop view if exists admin_boundaries_public;
drop table if exists admin_boundaries;

do $post$
begin
  if coalesce(to_regclass('public.admin_boundaries'), to_regclass('public.admin_boundaries_public'),
              to_regclass('public.price_stat_localities'), to_regclass('public.city_population')) is not null then
    raise exception '729 did not land: a dropped relation is still here';
  end if;
  if exists (select 1 from pg_attribute
              where not attisdropped and attname in ('entity_type', 'entity_id', 'default_radius_m')
                and attrelid in ('public.price_stat_observations'::regclass, 'public.price_stat_locality_no_data'::regclass,
                                 'public.price_stat_city_metrics'::regclass, 'public.curated_cities'::regclass)) then
    raise exception '729 did not land: a dropped column is still here';
  end if;
  if has_table_privilege('anon', 'public.curated_cities_public', 'select')
     or has_table_privilege('anon', 'public.price_stat_obce_picker_public', 'select')
     or not has_table_privilege('authenticated', 'public.curated_cities_public', 'select')
     or not has_table_privilege('authenticated', 'public.price_stat_obce_picker_public', 'select') then
    raise exception '729: the two re-created views are not readable by authenticated alone';
  end if;
end
$post$;

notify pgrst, 'reload schema';

commit;
