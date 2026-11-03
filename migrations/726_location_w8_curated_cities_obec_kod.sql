-- 726_location_w8_curated_cities_obec_kod.sql -- W8-3 (#2362): a curated city is keyed by the RÚIAN town code it
-- already held, and reads its population and polygon by that code (rulings Q11, Q12b; defaults D3, D4, D6, D8).
--   1. curated_cities.admin_boundary_id is renamed obec_kod (206 of 206 set on 11-03), its FK into admin_boundaries
--      dropped (mirror codes repeat under SCD-2), its index renamed, and it becomes NOT NULL.
--   2. Hodonín (D3): linked to 581593, the village of 197 people in okres Blansko its seed geocode fell in; its stored
--      population (23,344) is the town 586021's. It is relinked to 586021. Receipt: 1 row.
--   3. curated_cities_public: the population is obec_population's latest year for obec_kod (the curated-city
--      population view of ruling Q11). obec_kod is appended; admin_boundary_id, lat, lng and default_radius_m stay
--      only for the deployed SPA (W8-4 drops them).
--   4. curated_city_polygons_public: the town's render polygon from ruian_unit_render (725), simplified as before.
--   5. curated_cities_matching: 436's body reading obec_kod.
--   6. recompute_city_proximity: 674's body with the anchors drawn by ruian_unit_render and peopled by
--      obec_population, and home_obec_pop the latest population of the town the location ad is bound to
--      (ll.obec_kod) instead of the polygon nearest its pin (D6; 43 of a random 1,000 placed properties move, 11-03).
-- DESTRUCTIVE (a column rename, an FK drop, a one-row relink). Backup first: gh workflow run backup_before_drop.yml
-- -f label=w8-3-curated-cities -f targets="curated_cities", receipt read back. Apply after 725 and
-- BEFORE the merge: the deployed SPA reads curated_cities_public, whose columns this keeps, and nothing deployed
-- names the table column but the seed and ingest_boundaries, neither dispatched until W8-4. One transaction: a
-- lock_timeout trip rolls it all back; rerun as is.
-- VERIFY: select population from curated_cities_public where name = 'Hodonín';  -- 23,344
--         select count(*), count(obec_kod) from curated_cities;  -- 206, 206

begin;
set local lock_timeout = '5s';

do $pre$
begin
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
                 where p.pronamespace = 'public'::regnamespace and p.proname = 'recompute_city_proximity'), '')
       not in ('aebcd995a0618f8763c4ff9f21a0c33b', '7026ea664e306768fbb00a12eddb900f') then
    raise exception '726 refused: recompute_city_proximity is not 674''s body nor this file''s, or has two overloads';
  end if;
end
$pre$;

alter table curated_cities drop constraint if exists curated_cities_admin_boundary_id_fkey;
do $rename$
begin
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'curated_cities' and column_name = 'admin_boundary_id') then
    alter table curated_cities rename column admin_boundary_id to obec_kod;
  end if;
end
$rename$;
alter index if exists curated_cities_admin_boundary_idx rename to curated_cities_obec_kod_idx;
update curated_cities set obec_kod = 586021 where obec_kod = 581593 and name = 'Hodonín';
alter table curated_cities alter column obec_kod set not null;

create or replace view curated_cities_public as
  select c.id as city_id, c.name, c.kraj_name,
         st_y(c.centroid::geometry) as lat, st_x(c.centroid::geometry) as lng, c.default_radius_m,
         p.population, p.year as population_as_of_year,
         c.obec_kod as admin_boundary_id,
         c.obec_kod
    from curated_cities c
    left join obec_population p on p.obec_kod = c.obec_kod and p.year = (select max(year) from obec_population);

create or replace view curated_city_polygons_public as
  select c.id as city_id,
         st_asgeojson(st_simplifypreservetopology(r.geom, 0.0005::double precision), 5) as geojson
    from curated_cities c
    join ruian_unit_render r on r.level = 'obec' and r.code = c.obec_kod;

create or replace function public.curated_cities_matching(p_index_rules jsonb)
returns setof bigint
language sql
stable
parallel safe
rows 60
as $function$
  select c.obec_kod
    from curated_cities_public c
   where not exists (
       select 1
         from jsonb_array_elements(coalesce(p_index_rules, '[]'::jsonb)) r
        where not exists (
          select 1
            from city_index_values_public v
           where v.city_id    = c.city_id
             and v.index_name = r->>'index_name'
             and case coalesce(r->>'op', '>=')
                   when '>=' then v.value >= (r->>'value')::numeric
                   when '<=' then v.value <= (r->>'value')::numeric
                   when '>'  then v.value >  (r->>'value')::numeric
                   when '<'  then v.value <  (r->>'value')::numeric
                   when '==' then v.value =  (r->>'value')::numeric
                   when '!=' then v.value <> (r->>'value')::numeric
                   else           v.value >= (r->>'value')::numeric
                 end
        )
     );
$function$;

comment on function public.curated_cities_matching(jsonb) is
  'W5 (migration 436): the ONE owner of city-quality rule evaluation. Returns the RÚIAN town codes '
  '(curated_cities.obec_kod, W8-3) of every curated city whose indexes satisfy every rule. '
  'All three consumers -- Browse, browse_stats_properties and the Watchdog matcher -- reduce '
  'to obec_id = ANY(...), a form with nothing left to diverge on (rule 16). Reads the _public '
  'views deliberately: the base tables are RLS-on with zero policies and would return zero '
  'rows for authenticated, silently emptying every cohort.';

create or replace function recompute_city_proximity(p_full boolean default false)
returns integer
language plpgsql
as $$
declare
  n integer;
  pop_year integer;
begin
  -- Transaction-scoped, so pooler-safe: the hourly run and a full rebuild never update the same rows at once.
  perform pg_advisory_xact_lock(hashtext('recompute_city_proximity'));
  pop_year := (select max(year) from obec_population);

  -- Anchor set: obce big enough to satisfy the smallest pop threshold the UI
  -- offers (>=10000), plus the curated cities that carry the qualitative
  -- indexes (latest revision), each drawn by its RÚIAN render polygon. One
  -- GiST-indexed temp set keeps the per-property spatial probe over a few
  -- hundred rows instead of all ~6 250 obce.
  -- Only a second call in one transaction finds the table; `drop table if exists` would NOTICE in a fresh one.
  if to_regclass('pg_temp._prox_anchors') is not null then
    drop table pg_temp._prox_anchors;
  end if;
  create temp table _prox_anchors on commit drop as
  with latest as (select max(source_revision) as rev from city_index_values),
  idx as (
    select cc.obec_kod,
      max(civ.value) filter (where civ.index_name = 'pracovni_mista')    as jobs,
      max(civ.value) filter (where civ.index_name = 'stehovani_mladych') as youth,
      max(civ.value) filter (where civ.index_name = 'celkove_hodnoceni') as overall
    from curated_cities cc
    join city_index_values civ
      on civ.city_id = cc.id
     and civ.source_revision = (select rev from latest)
     and civ.index_name in ('pracovni_mista', 'stehovani_mladych', 'celkove_hodnoceni')
    group by cc.obec_kod
  )
  select r.code as obec_kod, r.geom::geography as geom,
    case when op.population >= 10000 then op.population end as population,
    idx.jobs, idx.youth, idx.overall
  from ruian_unit_render r
  left join obec_population op on op.obec_kod = r.code and op.year = pop_year
  left join idx on idx.obec_kod = r.code
  where r.level = 'obec'
    and (op.population >= 10000 or idx.obec_kod is not null);

  create index on _prox_anchors using gist (geom);
  analyze _prox_anchors;

  update properties p set
    home_obec_pop    = s.home_obec_pop,
    near_pop_5km     = s.near_pop_5km,
    near_pop_15km    = s.near_pop_15km,
    near_jobs_5km    = s.near_jobs_5km,
    near_jobs_15km   = s.near_jobs_15km,
    near_youth_5km   = s.near_youth_5km,
    near_youth_15km  = s.near_youth_15km,
    near_overall_5km = s.near_overall_5km,
    near_overall_15km= s.near_overall_15km,
    city_proximity_computed_at = now()
  from (
    select p2.id, p2.location_listing_ref_id,
      hp.population as home_obec_pop,
      a5.pop  as near_pop_5km,  a15.pop  as near_pop_15km,
      a5.jobs as near_jobs_5km, a15.jobs as near_jobs_15km,
      a5.youth as near_youth_5km, a15.youth as near_youth_15km,
      a5.overall as near_overall_5km, a15.overall as near_overall_15km
    from properties p2
    join listing_location ll on ll.listing_id = p2.location_listing_ref_id
    -- The town the location ad is bound to (rule 25's one code), not the polygon nearest its pin.
    left join obec_population hp on hp.obec_kod = ll.obec_kod and hp.year = pop_year
    left join lateral (
      select max(population) as pop, max(jobs) as jobs, max(youth) as youth, max(overall) as overall
      from _prox_anchors a where st_dwithin(ll.geom::geography, a.geom, 5000)
    ) a5 on true
    left join lateral (
      select max(population) as pop, max(jobs) as jobs, max(youth) as youth, max(overall) as overall
      from _prox_anchors a where st_dwithin(ll.geom::geography, a.geom, 15000)
    ) a15 on true
    where ll.geom is not null
      and (p_full or p2.city_proximity_computed_at is null)
  ) s
  where p.id = s.id
    -- Re-checked on a row a concurrent rollup updated: when its location ad moved, the row is
    -- skipped, and the rollup that moved it has already nulled its stamp for the next run.
    and p.location_listing_ref_id = s.location_listing_ref_id;

  get diagnostics n = row_count;
  return n;
end
$$;

comment on function public.recompute_city_proximity(boolean) is
  'Fill properties.home_obec_pop + near_{pop,jobs,youth,overall}_{5,15}km at the property''s LOCATION AD''s '
  'point (properties.location_listing_ref_id, migration 636): home_obec_pop is the latest obec_population of the '
  'town the location ad is bound to (W8-3), the near_* maxes come from anchors drawn by the RÚIAN render polygons. '
  'Incremental (city_proximity_computed_at IS NULL) unless p_full; a row whose location ad moved while the run '
  'computed it is skipped and stays NULL for the next run. Run hourly by pg_cron job recompute-city-proximity '
  '(migration 674); the full rebuild (scripts/recompute_city_proximity.py --full) ends the population and '
  'curated-city loads.';

do $post$
begin
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
                 where p.pronamespace = 'public'::regnamespace and p.proname = 'recompute_city_proximity'), '')
       <> '7026ea664e306768fbb00a12eddb900f' then
    raise exception '726 did not land: recompute_city_proximity is not this file''s body (or has two overloads)';
  end if;
  if has_function_privilege('anon', 'public.recompute_city_proximity(boolean)', 'execute')
     or has_function_privilege('authenticated', 'public.recompute_city_proximity(boolean)', 'execute') then
    raise exception '726: a browser role can execute recompute_city_proximity';
  end if;
  if exists (select 1 from curated_cities where obec_kod is null) then
    raise exception '726: a curated city has no town code';
  end if;
end
$post$;

commit;
