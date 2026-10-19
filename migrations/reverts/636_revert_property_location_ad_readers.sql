-- 636_revert_property_location_ad_readers.sql
--
-- REVERT for 636_property_location_ad_readers.sql. SHIPPED UNAPPLIED.
--
-- Lives in migrations/reverts/ for the same two verified reasons as every revert in this
-- build: the CI schema replay applies `ls migrations/*.sql | sort` (NOT recursive), so a
-- revert beside its forward migration would be applied right after it and silently undo the
-- change in every replayed environment; and tests/test_migration_numbers.py globs the same
-- way and forbids a duplicate number above 304.
--
-- It restores, verbatim, the three bodies 636 replaced, and drops the CHECK 636 added:
--   browse_projection            625's body   md5(pg_get_viewdef) 80fde37bd22abb86d5e93bcc0c41fa38
--   properties_public            617's body   md5(pg_get_viewdef) 600434e950ee7db543213167e9e069f7
--   recompute_city_proximity     508's body   md5(prosrc)         6b7931e8b358e78e1b784d05b50ff982
--                                (with 142's comment)
--   properties_location_ad_present   dropped
-- browse_projection and the function keep their columns and signature, so CREATE OR REPLACE keeps
-- their ACLs. properties_public cannot: CREATE OR REPLACE never drops a column, and 636 appended
-- location_listing_ref_id. So it is renamed aside, 617's body is created under its name with 617's
-- grants (SELECT for authenticated; postgres and service_role from the schema's default ACL, the
-- live set read 2026-10-20), its one dependant pipeline_board_public is re-pointed with its own body
-- (618's, verbatim: its definition names properties_public, so the re-create binds the new view; same
-- columns, so CREATE OR REPLACE keeps its ACL; WITH (security_invoker) restated, since CREATE OR
-- REPLACE resets a view's options to the ones it names), and the renamed view is dropped (no CASCADE:
-- the plain DROP refuses if anything else still names it). NULLS NOTHING: the city-proximity values
-- 636's reset sent for recomputation stay as the location ad placed them; a full run of
-- recompute_city_proximity.yml (--full) puts every property back at its canonical ad.
--
-- WHAT APPLYING THIS COSTS, stated plainly so it is never done casually:
--   * Browse (from the next */15 tick), the property page and the watchdog place every property at its
--     canonical ad again: about 13.8k properties move, rings come back where a dot was (15100 among them).
--   * Nothing keeps the two pointers together any more (the CHECK is gone).
-- ORDER: revert W4's code first and let it deploy (the watchdog's served rule reads
-- properties_public.location_listing_ref_id; the rollup's proximity reset follows the location ad), then
-- this file. It refuses once a later migration has replaced any of the three bodies, once
-- pipeline_board_public is not 618's body, and while anything else depends on properties_public.
-- APPLY: psql -f (or apply_migration.yml with this path), not in a list rebuild's minutes (:00-:02, :15-:17,
-- :30-:32, :45-:47): it takes the rebuild's advisory key and ACCESS EXCLUSIVE locks on both views and on
-- properties (the DROP CONSTRAINT) under the 5 s lock limit; re-run on a timeout. A re-run after it landed
-- re-creates properties_public once more and otherwise changes nothing.

begin;

set local lock_timeout = '5s';
set local statement_timeout = '120s';

do $pre$
declare
  v_bp text := md5(pg_get_viewdef('public.browse_projection'::regclass));
  v_pp text := md5(pg_get_viewdef('public.properties_public'::regclass));
  v_fn text := coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
                  where p.pronamespace = 'public'::regnamespace and p.proname = 'recompute_city_proximity'), '');
  v_tied text;
begin
  perform pg_advisory_xact_lock(hashtext('rebuild_browse_list'));

  if v_bp not in ('9fb6da98b0d4d171079c51328e77a279', '80fde37bd22abb86d5e93bcc0c41fa38') then
    raise exception '636 revert refused: browse_projection is not 636''s body (a later migration replaced it; md5 %)', v_bp;
  end if;
  if v_pp not in ('18efda1aeb70ff4414780381969e5372', '600434e950ee7db543213167e9e069f7') then
    raise exception '636 revert refused: properties_public is not 636''s body (a later migration replaced it; md5 %)', v_pp;
  end if;
  if v_fn not in ('84947f47472f7dd1e15a0a1865ebc700', '6b7931e8b358e78e1b784d05b50ff982') then
    raise exception '636 revert refused: recompute_city_proximity is not 636''s body (a later migration replaced it, or two overloads; md5 %)', v_fn;
  end if;
  if md5(pg_get_viewdef('public.pipeline_board_public'::regclass)) is distinct from '06c950df3e18e0f58c4be6b06973600e' then
    raise exception '636 revert refused: pipeline_board_public is not 618''s body, which this file restates';
  end if;

  select string_agg(pg_describe_object(d.classid, d.objid, d.objsubid), ', ') into v_tied
    from pg_depend d
   where d.deptype = 'n'
     and ((d.refclassid = 'pg_class'::regclass and d.refobjid = 'public.properties_public'::regclass
           and not (d.classid = 'pg_rewrite'::regclass
                    and (select r.ev_class from pg_rewrite r where r.oid = d.objid)
                        in ('public.properties_public'::regclass, 'public.pipeline_board_public'::regclass)))
       or (d.refclassid = 'pg_type'::regclass
           and d.refobjid = (select c.reltype from pg_class c where c.oid = 'public.properties_public'::regclass)));
  if v_tied is not null then
    raise exception '636 revert refused: % depend(s) on properties_public, which this file re-creates', v_tied;
  end if;

  perform set_config('one_pin636_revert.pre', jsonb_build_object(
      'pp_cols', (select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum)
                    from pg_attribute a
                   where a.attrelid = 'public.properties_public'::regclass and a.attnum > 0 and not a.attisdropped
                     and a.attname <> 'location_listing_ref_id'),
      'pp_acl', (select string_agg(x::text, ',' order by x::text) || ' ' || coalesce(c.reloptions::text, '-')
                   from pg_class c, unnest(c.relacl) x where c.oid = 'public.properties_public'::regclass
                  group by c.reloptions),
      'board_acl', (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
                      from pg_class c where c.oid = 'public.pipeline_board_public'::regclass),
      'bp_acl', (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
                   from pg_class c where c.oid = 'public.browse_projection'::regclass),
      'fn', (select l.lanname::text || ' ' || p.provolatile::text || ' ' || p.prosecdef::text || ' '
                    || coalesce(p.proconfig::text, '-') || ' ' || coalesce(p.proacl::text, '-') || ' '
                    || pg_get_function_arguments(p.oid) || ' -> ' || pg_get_function_result(p.oid)
               from pg_proc p join pg_language l on l.oid = p.prolang
              where p.oid = to_regprocedure('public.recompute_city_proximity(boolean)')))::text, true);
end
$pre$;

-- ---------------------------------------------------------------------------
-- 1. browse_projection: 625's body, verbatim.
-- ---------------------------------------------------------------------------
create or replace view browse_projection as
  select
      p.id as property_id,
      p.repr_listing_id as sreality_id,
      p.first_seen_at,
      p.last_seen_at,
      p.is_active,
      p.category_main,
      p.category_type,
      p.current_price_czk as price_czk,
      p.area_m2,
      p.disposition,
      st_y(ll.geom) as lat,
      st_x(ll.geom) as lng,
      p.has_balcony,
      p.has_parking,
      p.has_lift,
      p.building_type,
      p.condition,
      p.energy_rating,
      p.estate_area,
      p.usable_area,
      p.garden_area,
      p.category_sub_cb,
      p.furnished,
      p.terrace,
      p.cellar,
      p.garage,
      p.parking_lots,
      p.ownership,
      measure_tom_days(p.is_active, p.first_seen_at, p.last_seen_at) as tom_days,
      measure_price_per_m2(p.current_price_czk::numeric, p.area_m2, p.category_main, p.category_type) as price_per_m2,
      p.building_condition_level,
      p.apartment_condition_level,
      p.source,
      mf.mf_reference_rent_czk,
      mf.mf_gross_yield_pct,
      p.home_obec_pop,
      p.near_pop_5km,
      p.near_pop_15km,
      p.near_jobs_5km,
      p.near_jobs_15km,
      p.near_youth_5km,
      p.near_youth_15km,
      p.near_overall_5km,
      p.near_overall_15km,
      p.subtype,
      p.last_change_at,
      ll.obec_kod  as obec_id,
      ll.okres_kod as okres_id,
      ll.kraj_kod  as region_id,
      p.price_change_count,
      p.price_change_count_30d,
      p.price_change_count_90d,
      p.price_change_count_365d,
      p.total_price_change_pct,
      p.repr_listing_ref_id as listing_id,
      (select l.source_id_native from listings l where l.id = p.repr_listing_ref_id) as source_id_native,
      measure_price_per_m2_basis(p.category_main, p.category_type) as price_per_m2_basis,
      location_display_label(ll.street_name, ll.house_number_cp, ll.house_number_co,
                             ll.obec_name, ll.cast_obce_name, ll.country_code,
                             ll.country_status) as display_label,
      ll.cast_obce_kod as cast_obce_id,
      ll.uncertainty_radius_m,
      gr.rank::int as granularity_rank,
      public.plot_area_m2(p.category_main, p.area_m2::numeric,
                          p.estate_area::numeric) as plot_area_m2,
      ll.ulice_kod as ulice_id,
      coalesce(p.all_sources, '{}'::text[]) as all_sources,
      coalesce(p.active_sources, '{}'::text[]) as active_sources,
      p.source_count,
      p.newest_ad_at_sreality,
      p.newest_ad_at_bazos,
      p.newest_ad_at_idnes,
      p.newest_ad_at_maxima,
      p.newest_ad_at_ceskereality,
      p.newest_ad_at_bezrealitky,
      p.newest_ad_at_mmreality,
      p.newest_ad_at_remax,
      p.newest_ad_at_realitymix,
      (select measure_tom_observed(k.source, k.category_main, k.category_type, k.first_seen_at)
         from listings k where k.property_id = p.id
        order by (k.category_main is null or k.category_type is null), k.first_seen_at, k.id
        limit 1) as tom_observed
  from properties p
       left join listing_location ll on ll.listing_id = p.repr_listing_ref_id
       left join location_granularity_rank gr on gr.granularity = ll.granularity
       left join lateral public.mf_reference(
           p.category_main, p.category_type, p.disposition, p.area_m2, p.current_price_czk,
           p.condition, p.has_balcony, p.terrace, p.furnished, p.garage, p.has_lift,
           p.building_type, ll.obec_kod, ll.katastr_kod, ll.country_status) mf on true
  where p.status = 'active'::text
    -- THE CONSUMER RULE (rule 25), read off the label join (522).
    and (ll.geom IS NOT NULL OR ll.country_status = 'foreign')
  ;

comment on view public.browse_projection is null;

-- ---------------------------------------------------------------------------
-- 2. properties_public: 617's body, verbatim, under a fresh view (the header says why).
-- ---------------------------------------------------------------------------
alter view public.properties_public rename to properties_public_636_reverted;

create or replace view properties_public as
  select
      p.id as property_id,
      p.repr_listing_id as sreality_id,
      p.first_seen_at,
      p.last_seen_at,
      p.is_active,
      p.category_main,
      p.category_type,
      p.current_price_czk as price_czk,
      l.price_unit,
      p.area_m2,
      p.disposition,
      st_y(ll.geom) as lat,
      st_x(ll.geom) as lng,
      l.floor,
      l.total_floors,
      p.has_balcony,
      p.has_parking,
      p.has_lift,
      p.building_type,
      p.condition,
      p.energy_rating,
      p.estate_area,
      p.usable_area,
      p.garden_area,
      p.category_sub_cb,
      p.furnished,
      p.terrace,
      p.cellar,
      p.garage,
      p.parking_lots,
      p.ownership,
      l.broker_name,
      null::text as broker_email,
      null::text as broker_phone,
      measure_tom_days(p.is_active, p.first_seen_at, p.last_seen_at) as tom_days,
      measure_price_per_m2(p.current_price_czk::numeric, p.area_m2, p.category_main, p.category_type) as price_per_m2,
      p.building_condition_level,
      p.apartment_condition_level,
      l.description,
      p.source_count,
      p.price_drop_count,
      p.price_rise_count,
      p.max_price_drop_pct,
      p.stats_computed_at,
      p.source,
      mf.mf_reference_rent_czk,
      mf.mf_gross_yield_pct,
      ll.obec_name as obec,
      p.home_obec_pop,
      p.near_pop_5km,
      p.near_pop_15km,
      p.near_jobs_5km,
      p.near_jobs_15km,
      p.near_youth_5km,
      p.near_youth_15km,
      p.near_overall_5km,
      p.near_overall_15km,
      p.subtype,
      p.last_change_at,
      ll.obec_kod  as obec_id,
      ll.okres_kod as okres_id,
      ll.kraj_kod  as region_id,
      p.price_change_count,
      p.price_change_count_30d,
      p.price_change_count_90d,
      p.price_change_count_365d,
      p.total_price_change_pct,
      mf.mf_reference_rent,
      p.repr_listing_ref_id as listing_id,
      l.source_id_native,
      measure_price_per_m2_basis(p.category_main, p.category_type) as price_per_m2_basis,
      location_display_label(ll.street_name, ll.house_number_cp, ll.house_number_co,
                             ll.obec_name, ll.cast_obce_name, ll.country_code,
                             ll.country_status) as display_label,
      ll.cast_obce_kod as cast_obce_id,
      ll.ulice_kod as ulice_id,
      coalesce(p.all_sources, '{}'::text[]) as all_sources,
      coalesce(p.active_sources, '{}'::text[]) as active_sources
  from properties p
       left join listings l on l.id = p.repr_listing_ref_id
       left join listing_location ll on ll.listing_id = p.repr_listing_ref_id
       left join lateral public.mf_reference(
           p.category_main, p.category_type, p.disposition, p.area_m2, p.current_price_czk,
           p.condition, p.has_balcony, p.terrace, p.furnished, p.garage, p.has_lift,
           p.building_type, ll.obec_kod, ll.katastr_kod, ll.country_status) mf on true
  where p.status = 'active'::text;

revoke all on public.properties_public from public, anon, authenticated;
grant select on public.properties_public to authenticated;

-- 618's body, verbatim: re-binds the board to the restored properties_public.
create or replace view pipeline_board_public
with (security_invoker = true) as
select
  pp.property_id,
  pp.stage_id,
  pp.board_position,
  pp.entered_stage_at,
  pp.added_at,
  p.sreality_id,
  p.source,
  p.source_id_native,
  p.listing_id,
  p.category_main,
  p.disposition,
  p.subtype,
  p.area_m2,
  p.price_czk,
  p.mf_gross_yield_pct,
  p.total_price_change_pct,
  p.price_change_count,
  p.obec_id,
  p.okres_id,
  p.region_id,
  p.obec,
  p.is_active,
  p.category_type,
  p.price_per_m2,
  p.price_per_m2_basis,
  p.display_label,
  p.cast_obce_id,
  p.ulice_id
from property_pipeline_public pp
left join properties_public p on p.property_id = pp.property_id;

drop view public.properties_public_636_reverted;

-- ---------------------------------------------------------------------------
-- 3. recompute_city_proximity: 508's body, verbatim, with 142's comment.
-- ---------------------------------------------------------------------------
create or replace function recompute_city_proximity(p_full boolean default false)
returns integer
language plpgsql
as $$
declare
  n integer;
begin
  -- Anchor set: obce big enough to satisfy the smallest pop threshold the UI
  -- offers (>=10000), plus the 206 curated cities that carry the qualitative
  -- indexes (latest revision). One GiST-indexed temp set keeps the per-property
  -- spatial probe over a few hundred rows instead of all ~6 250 obce.
  drop table if exists _prox_anchors;
  create temp table _prox_anchors on commit drop as
  with latest as (select max(source_revision) as rev from city_index_values),
  idx as (
    select cc.admin_boundary_id as ab_id,
      max(civ.value) filter (where civ.index_name = 'pracovni_mista')    as jobs,
      max(civ.value) filter (where civ.index_name = 'stehovani_mladych') as youth,
      max(civ.value) filter (where civ.index_name = 'celkove_hodnoceni') as overall
    from curated_cities cc
    join city_index_values civ
      on civ.city_id = cc.id
     and civ.source_revision = (select rev from latest)
     and civ.index_name in ('pracovni_mista', 'stehovani_mladych', 'celkove_hodnoceni')
    where cc.admin_boundary_id is not null
    group by cc.admin_boundary_id
  )
  select ab.id as ab_id, ab.geom,
    case when ab.population >= 10000 then ab.population end as population,
    idx.jobs, idx.youth, idx.overall
  from admin_boundaries ab
  left join idx on idx.ab_id = ab.id
  where ab.level = 'obec' and ab.geom is not null
    and (ab.population >= 10000 or idx.ab_id is not null);

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
    select p2.id,
      hp.population as home_obec_pop,
      a5.pop  as near_pop_5km,  a15.pop  as near_pop_15km,
      a5.jobs as near_jobs_5km, a15.jobs as near_jobs_15km,
      a5.youth as near_youth_5km, a15.youth as near_youth_15km,
      a5.overall as near_overall_5km, a15.overall as near_overall_15km
    from properties p2
    join listing_location ll on ll.listing_id = p2.repr_listing_ref_id
    left join lateral (
      select ab.population from admin_boundaries ab
      where ab.level = 'obec'
      order by ll.geom::geography <-> ab.geom
      limit 1
    ) hp on true
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
  where p.id = s.id;

  get diagnostics n = row_count;
  return n;
end
$$;

comment on function recompute_city_proximity(boolean) is
  'Fill properties.home_obec_pop + near_{pop,jobs,youth,overall}_{5,15}km. '
  'Incremental (city_proximity_computed_at IS NULL) unless p_full. '
  'Run by recompute_city_proximity.yml and after a population / city-index load.';

-- ---------------------------------------------------------------------------
-- 4. The CHECK. Last: its ACCESS EXCLUSIVE lock on properties is held only to the commit.
-- ---------------------------------------------------------------------------
alter table public.properties drop constraint if exists properties_location_ad_present;

-- ---------------------------------------------------------------------------
-- 5. Post-conditions.
-- ---------------------------------------------------------------------------
do $post$
declare
  v_pre jsonb := current_setting('one_pin636_revert.pre')::jsonb;
begin
  if md5(pg_get_viewdef('public.browse_projection'::regclass)) is distinct from '80fde37bd22abb86d5e93bcc0c41fa38'
     or md5(pg_get_viewdef('public.properties_public'::regclass)) is distinct from '600434e950ee7db543213167e9e069f7'
     or md5(pg_get_viewdef('public.pipeline_board_public'::regclass)) is distinct from '06c950df3e18e0f58c4be6b06973600e'
     or coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
          where p.pronamespace = 'public'::regnamespace and p.proname = 'recompute_city_proximity'), '')
        is distinct from '6b7931e8b358e78e1b784d05b50ff982' then
    raise exception '636 revert did not land: a restored body is not 625''s, 617''s, 618''s or 508''s';
  end if;
  if (select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum)
        from pg_attribute a
       where a.attrelid = 'public.properties_public'::regclass and a.attnum > 0 and not a.attisdropped)
     is distinct from v_pre ->> 'pp_cols' then
    raise exception '636 revert did not land: properties_public is not its columns before 636';
  end if;
  if (select string_agg(x::text, ',' order by x::text) || ' ' || coalesce(c.reloptions::text, '-')
        from pg_class c, unnest(c.relacl) x where c.oid = 'public.properties_public'::regclass
       group by c.reloptions) is distinct from v_pre ->> 'pp_acl'
     or (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
           from pg_class c where c.oid = 'public.pipeline_board_public'::regclass) is distinct from v_pre ->> 'board_acl'
     or (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
           from pg_class c where c.oid = 'public.browse_projection'::regclass) is distinct from v_pre ->> 'bp_acl'
     or (select l.lanname::text || ' ' || p.provolatile::text || ' ' || p.prosecdef::text || ' '
                || coalesce(p.proconfig::text, '-') || ' ' || coalesce(p.proacl::text, '-') || ' '
                || pg_get_function_arguments(p.oid) || ' -> ' || pg_get_function_result(p.oid)
           from pg_proc p join pg_language l on l.oid = p.prolang
          where p.oid = to_regprocedure('public.recompute_city_proximity(boolean)')) is distinct from v_pre ->> 'fn' then
    raise exception '636 revert did not land: an ACL, a view option or the function''s posture differs from before the revert';
  end if;
  if to_regclass('public.properties_public_636_reverted') is not null
     or exists (select 1 from pg_constraint
                 where conrelid = 'public.properties'::regclass and conname = 'properties_location_ad_present')
     or obj_description('public.browse_projection'::regclass, 'pg_class') is not null then
    raise exception '636 revert did not land: the renamed view, the CHECK or 636''s comment is still there';
  end if;
  perform * from public.properties_public limit 1;
  perform * from public.pipeline_board_public limit 1;
end
$post$;

notify pgrst, 'reload schema';

commit;
