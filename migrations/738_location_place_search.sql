-- 738_location_place_search.sql -- own place search's read models: served counts, fly-to facts and a name index
-- that serves a prefix (spec #2441 Part 4, ticket #2449; rulings Q2 c, Q6 a, Q9, Q12 a).
--
-- WHY. Every place box asks Mapy for suggestions today. Own place search (location_data/place_search.py and
-- POST /places/suggest, the same PR) answers rows that are already chips, from the RÚIAN mirror. It ranks a row by the
-- properties served at that place (Q12 a: one ranking, any deal type, the same in every box; ruling 2026-11-08), shows
-- where to fly, and finds a name by prefix. Rule 25's net addition is ruled by Q2 c (operator, 2026-11-08); the
-- switch-over (#2453) deletes the Mapy suggest path it replaces, about 250 lines plus 150 test lines. The route ships
-- with no caller until then.
--
-- WHAT. Additive, five objects and one index swap:
--   1. place_served_rows(), the five RÚIAN id columns of browse_list's ACTIVE rows (property grain, rule 15). A plain
--      SQL function, never a FROM: rebuild_browse_list() drops browse_list and renames its successor into place every
--      15 minutes with no CASCADE (658), so a view or matview naming it would refuse every rebuild and freeze Browse.
--      browse_list_visible is a function for the same reason (537; docs/architecture.md, "Browse hides dismissed").
--      Its body is a string, not BEGIN ATOMIC, so no dependency on browse_list is recorded, and the name is looked up
--      when a refresh is planned, so a refresh that waited out the swap reads the new table. INVOKER, revoked from
--      every browser role.
--   2. place_served_counts(level ruian_level, code, n), refreshed hourly at :37 by pg_cron through the chokepoint
--      refresh_matview (578): one row per kraj, okres, obec, část obce and street code an active served property names.
--      browse_list already holds the status, consumer and publication rules (669), so `is_active` is the one filter.
--      Read only to rank; the route never returns a count.
--   3. place_facts(level, code, lat, lng): where to fly. kraj, okres and obec copy the authoritative representative
--      point at the current registry version (733's units arm); městská část, část obce and street take the mean of
--      their open doors, which is FILL's ST_Centroid(ST_Collect(doors)) (resolve_db _STREET_POINT_SQL and
--      _PART_POINT_SQL) held as a sum per group instead of every point. Grouped by RÚIAN code, so stable across SCD-2
--      rows. It is a FACT TABLE ABOUT REGISTER PLACES, NOT A SECOND LOCATION STORE (rule 25): it places no listing
--      or property, and listing_location stays the only location store. The registry load's finish step refreshes it
--      monthly (location_data/ruian_load.py); it is stamped below because it is born populated.
--   4. two derived_artifacts rows, and the :37 cron job.
--   5. ruian_name_index_norm_prefix (name_norm text_pattern_ops, registry_version_id) replaces 389's
--      ruian_name_index_norm_version (name_norm, registry_version_id), built CONCURRENTLY before the old one drops.
--      The prefix search needs LIKE 'p%', which a default-opclass btree cannot serve under ICU (prod's collation); the
--      text_pattern_ops family also holds `=`, so the resolver's _ADMIN_BY_NAME_SQL and api/maps.py keep an index.
--      Not partial. Net 0 indexes. Streets keep their trigram GIN.
--
-- PLAN. Measured on production 2026-11-08 (read-only EXPLAIN ANALYZE of item 2's SELECT): 569 ms, 291,995 active rows
-- into 40,947 groups, one seq scan, HashAggregate 4.5 MB in one batch. Item 3 reads the 3.0 M doors once (669 MB) and
-- the authoritative points at one version; its time is not measured, hence the statement_timeout below. The hourly
-- refresh holds a read lock on browse_list, which the rebuild's DROP waits for, so the cron command bounds it at 120 s.
--
-- PG17 runs a matview's create and refresh with search_path = pg_catalog, pg_temp, and an inlined SQL function's body
-- is parsed then: every relation in place_served_rows() is schema-qualified, and it carries no SET clause, which would
-- stop the planner inlining it.
--
-- GRANTS. Both matviews and the function are service_role only: the route reads as the API's service connection.
--
-- ADDITIVE. The only drops are an index whose replacement is built and valid first, and a cancelled build's leftover.
-- Apply through apply_migration.yml (psql -f, every statement autocommits; never the MCP, which wraps a transaction
-- that CREATE INDEX CONCURRENTLY refuses). Every statement is idempotent, so a lock_timeout retry re-runs the file
-- from statement 1. Not within 2 minutes after :00, :15, :30 or :45 (the browse_list rebuild holds browse_list), nor
-- at :07 or :25 (the two location audit refreshes, long transactions the concurrent build would have to wait out).
-- VERIFY:
--   select level, count(*), sum(n) from place_served_counts group by 1 order by 1;
--   select level, count(*) from place_facts group by 1 order by 1;  -- kraj 14, okres 77, obec ~6,258
--   select indexrelid::regclass, indisvalid, pg_get_indexdef(indexrelid) from pg_index
--    where indrelid = 'public.ruian_name_index'::regclass;           -- _norm_prefix valid, no _norm_version
--   select has_table_privilege('authenticated', 'public.place_served_counts', 'select'),
--          has_table_privilege('authenticated', 'public.place_facts', 'select');  -- false, false
--   select schedule, command from cron.job where jobname = 'refresh-place-served-counts';
--   then after the next :37, derived_artifacts.last_succeeded_at for place_served_counts moves, and the next
--   browse_list rebuild still stamps browse_list.
-- ROLLBACK (forward, a new migration): cron.unschedule('refresh-place-served-counts'); drop both matviews, the
--   function and their derived_artifacts rows; create index concurrently ruian_name_index_norm_version on
--   public.ruian_name_index (name_norm, registry_version_id).

set lock_timeout = '5s';
set statement_timeout = '900s';

-- Item 1. Qualified throughout: the body is parsed under the matview refresh's pg_catalog, pg_temp search_path.
create or replace function public.place_served_rows()
returns table (region_id bigint, okres_id bigint, obec_id bigint, cast_obce_id bigint, ulice_id bigint)
language sql
stable
security invoker
as $fn$
  select l.region_id, l.okres_id, l.obec_id, l.cast_obce_id, l.ulice_id
    from public.browse_list l
   where l.is_active
$fn$;

revoke execute on function public.place_served_rows() from public, anon, authenticated;

comment on function public.place_served_rows() is
  'The RÚIAN ids of active browse_list rows, for place_served_counts (mig 738). A function, not a FROM: '
  'browse_list is dropped and renamed into place every 15 minutes, which a dependent view or matview would block.';

-- Item 2. grouping() names the set a row belongs to; the other four ids are NULL in it, so coalesce is its code.
create materialized view if not exists public.place_served_counts as
select g.level, g.code, g.n
  from (select case when grouping(r.region_id) = 0 then 'kraj'
                    when grouping(r.okres_id) = 0 then 'okres'
                    when grouping(r.obec_id) = 0 then 'obec'
                    when grouping(r.cast_obce_id) = 0 then 'cast_obce'
                    else 'ulice' end::ruian_level as level,
               coalesce(r.region_id, r.okres_id, r.obec_id, r.cast_obce_id, r.ulice_id) as code,
               count(*) as n
          from public.place_served_rows() r
         group by grouping sets ((r.region_id), (r.okres_id), (r.obec_id), (r.cast_obce_id), (r.ulice_id))) g
 where g.code is not null;

create unique index if not exists place_served_counts_pk on public.place_served_counts (level, code);

-- Item 3.
create materialized view if not exists public.place_facts as
with
cur as (select id from public.registry_versions where is_current),
polygons as (
  select distinct on (u.level, u.code) u.level, u.code, g.representative_point as pt
    from public.ruian_admin_units u
    join public.ruian_admin_unit_geometries g on g.unit_id = u.id and g.purpose = 'authoritative'
                                             and g.registry_version_id = (select id from cur)
   where u.level in ('kraj', 'okres', 'obec') and u.valid_to is null
   order by u.level, u.code, u.valid_from desc),
doors as (
  select case when grouping(ap.ulice_kod) = 0 then 'ulice'
              when grouping(ap.cast_obce_kod) = 0 then 'cast_obce'
              else 'momc' end::ruian_level as level,
         coalesce(ap.ulice_kod, ap.cast_obce_kod, m.code) as code,
         avg(ST_Y(ap.geom)) as lat, avg(ST_X(ap.geom)) as lng
    from public.ruian_address_points ap
    left join public.ruian_admin_units m on m.id = ap.momc_unit_id
   where ap.valid_to is null and ap.geom is not null
   group by grouping sets ((ap.ulice_kod), (ap.cast_obce_kod), (m.code)))
select p.level, p.code, ST_Y(p.pt) as lat, ST_X(p.pt) as lng from polygons p
union all
select d.level, d.code, d.lat, d.lng from doors d where d.code is not null;

create unique index if not exists place_facts_pk on public.place_facts (level, code);

comment on materialized view public.place_facts is
  'Where own place search flies to (mig 738): a fact table about register places, not a location store (rule 25). '
  'Refreshed by the registry load''s finish step.';

-- Default privileges hand authenticated SELECT on a new relation (migration 299): take it back.
revoke all on public.place_served_counts from public, anon, authenticated;
grant select on public.place_served_counts to service_role;
revoke all on public.place_facts from public, anon, authenticated;
grant select on public.place_facts to service_role;

-- Item 4. The registry load's own cron (location_registry_load.yml); its producer is the file, never the workflow, and
-- the PR that moves ruian_load to the job runner re-hosts the row.
insert into public.derived_artifacts (name, producer, host, cadence, staleness_budget, is_serving)
values ('place_served_counts', 'refresh_matview', 'pg_cron', '37 * * * *', interval '130 minutes', true),
       ('place_facts', 'location_data/ruian_load.py', 'github-actions', '0 10 1 * *', interval '40 days', true)
on conflict (name) do nothing;

-- Born populated, so stamped now: Health would otherwise read "never succeeded" until the next monthly load.
select public.stamp_derived_artifact('place_facts', (select count(*) from public.place_facts), null);

do $cron$
begin
  perform cron.schedule(
    'refresh-place-served-counts',
    '37 * * * *',
    $$set statement_timeout='120s'; select public.refresh_matview('place_served_counts');$$
  );
exception when invalid_schema_name then
  raise notice 'pg_cron unavailable; refresh-place-served-counts not scheduled (%)', sqlerrm;
end
$cron$;

-- Item 5. A cancelled build leaves an INVALID index under the new name, which IF NOT EXISTS would keep: move it aside.
do $invalid$
begin
  if exists (select 1 from pg_index i
              where i.indexrelid = to_regclass('public.ruian_name_index_norm_prefix')
                and not i.indisvalid) then
    alter index public.ruian_name_index_norm_prefix rename to ruian_name_index_norm_prefix_invalid;
    raise notice '738: an INVALID ruian_name_index_norm_prefix from a cancelled build moved aside';
  end if;
end
$invalid$;

-- The builds take only SHARE UPDATE EXCLUSIVE and wait for every older transaction: no lock_timeout (703's idiom).
set lock_timeout = 0;
set statement_timeout = '1900s';

drop index concurrently if exists public.ruian_name_index_norm_prefix_invalid;

create index concurrently if not exists ruian_name_index_norm_prefix
  on public.ruian_name_index (name_norm text_pattern_ops, registry_version_id);

drop index concurrently if exists public.ruian_name_index_norm_version;

reset statement_timeout;
reset lock_timeout;

do $post$
begin
  if not exists (select 1 from pg_index i
                  where i.indexrelid = to_regclass('public.ruian_name_index_norm_prefix')
                    and i.indisvalid
                    and pg_get_indexdef(i.indexrelid) like '%name_norm text_pattern_ops, registry_version_id%') then
    raise exception '738 did not land: ruian_name_index_norm_prefix is missing, INVALID or not text_pattern_ops '
                    '-- re-run the file';
  end if;
  if to_regclass('public.ruian_name_index_norm_version') is not null then
    raise exception '738 did not land: ruian_name_index_norm_version still exists -- re-run the file';
  end if;
  if (select count(*) from pg_matviews m
       where m.schemaname = 'public' and m.matviewname in ('place_served_counts', 'place_facts') and m.ispopulated) <> 2
     or to_regclass('public.place_served_counts_pk') is null or to_regclass('public.place_facts_pk') is null then
    raise exception '738 did not land: a place matview is missing, unpopulated or without its unique index';
  end if;
  if has_table_privilege('authenticated', 'public.place_served_counts', 'select')
     or has_table_privilege('authenticated', 'public.place_facts', 'select')
     or has_table_privilege('anon', 'public.place_served_counts', 'select')
     or has_table_privilege('anon', 'public.place_facts', 'select')
     or has_function_privilege('authenticated', 'public.place_served_rows()', 'execute')
     or has_function_privilege('anon', 'public.place_served_rows()', 'execute') then
    raise exception '738 did not land: a browser role reaches a place read model';
  end if;
  if (select count(*) from public.derived_artifacts where name in ('place_served_counts', 'place_facts')) <> 2 then
    raise exception '738 did not land: a place matview has no derived_artifacts row';
  end if;
  -- The rebuild's DROP works only while nothing depends on browse_list: no view or matview (pg_rewrite) and no
  -- BEGIN ATOMIC function (pg_proc) may name it.
  if exists (select 1 from pg_depend d
              where d.refclassid = 'pg_class'::regclass and d.refobjid = 'public.browse_list'::regclass
                and d.classid in ('pg_rewrite'::regclass, 'pg_proc'::regclass)) then
    raise exception '738 refused: a view, matview or BEGIN ATOMIC function depends on browse_list, '
                    'so its rebuild would fail';
  end if;
  if to_regclass('cron.job') is null then
    raise notice '738: no pg_cron here; the schedule is not asserted';
  elsif not exists (select 1 from cron.job
                     where jobname = 'refresh-place-served-counts' and schedule = '37 * * * *' and active
                       and command like '%statement_timeout=''120s''%refresh_matview(''place_served_counts'')%') then
    raise exception '738 did not land: refresh-place-served-counts is not scheduled hourly at :37 under its timeout';
  end if;
end
$post$;
