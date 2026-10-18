-- 625_tom_observed.sql
--
-- "NEWLY LISTED ONLY": a second time-on-market measure, one definition, one filter.
--
-- WHY. measure_tom_days (617) counts from OUR first sighting. That is the ad's true start only
-- when the sighting was a steady-state discovery: the walk saw the ad appear. When a walk first
-- covers a portal's category, or catches up after an outage or a coverage gap, it discovers the
-- whole existing stock in one sweep, and every ad in that sweep gets a first_seen_at that is too
-- late -- its TOM is left-censored. The two portals that publish a listing date prove the size of
-- it (read live 2026-10-18): on ordinary days our sighting lags the portal by a median 0.56 d
-- (bazos) / 0.69 d (ceskereality); on sweep days by 15.8 d / 77.0 d (p90 47 d / 471 d). Sweeps are
-- not one go-live date per portal: sreality's largest (31,771 ads, 2026-06-12) was a land-category
-- expansion six weeks after launch, bazos's 2026-09-09 sweep backfilled a category first seen
-- 2026-08-07, and 2026-08-27 appears on every portal (the coverage sprint reaching pages earlier
-- walks never did). So the unit is (portal, category, day).
--
-- THE RULE (ingest_bulk_days, one definition, re-derived nightly): a (portal, category) day is a
-- SWEEP when it is that pair's first day of sightings, or when its sightings exceed
-- greatest(100, 4 x the pair's median daily count). An ad is OBSERVED FROM THE START
-- (measure_tom_observed = true) when its first sighting fell on no sweep day. Residue, stated: a
-- small pair's spill-over days after its first day (< 100 ads, < 4 x median) pass as ordinary.
--
-- WHAT IS PUBLISHED. Only browse_projection (-> browse_list, properties_map_mv) gains the column,
-- as the property's EARLIEST ad's answer (its first sighting is the property's TOM start): the
-- Browse list, count, pins, Stats and map counts filter it; estimation cohorts compile the
-- function over `listings` (toolkit.measures.tom_observed_sql). properties_public / listings_public
-- are untouched: the Watchdog agenda carries no TOM filter and no detail surface renders the flag.
--
-- ORDER (584's recipe for an appended column, then 597/598's for a defaulted RPC parameter):
--   0. preconditions (md5 of every body restated, read live 2026-10-18; skipped in the replay);
--   1. both rebuild locks, queued, then fail fast;
--   2. TX A: the bulk-day set (a matview, unique-indexed, refreshed nightly) and the measure;
--   3. TX B: browse_list gains the column FIRST (sync_browse_list's positional insert, and
--      browse_list_visible()'s `l.*`, must never see the table narrower than the view), then the
--      projection with the column appended LAST, then properties_map_visible() bridged with
--      `null::boolean as tom_observed` while the matview is narrow;
--   4. the map rebuilt (forced: this session holds its key), 590's source restored, one TX;
--   5. the list rebuilt the same way, so the column is filled from the one definition;
--   6. the two aggregate RPCs re-signed with `tom_observed_only boolean default false` (a new
--      DEFAULTED parameter may precede the SPA: it keeps calling without it) and the predicate
--      `(not tom_observed_only or l.tom_observed)` beside the tom_days bounds;
--   7. the nightly refresh (pg_cron; guarded, the replay has none); 8. post-conditions; 9. unlock.
-- Re-runnable: every step is idempotent and the gates accept this file's own bodies.
-- ROLLBACK: none needed (additive): drop the parameter by re-creating 605's RPC bodies, the
-- column by 590's recipe; the matview and the function may stay.
--
-- ci-allow-dynamic: properties_map_visible -- step 3's bridge is created only while
-- properties_map_mv lacks tom_observed (so a re-run stays idempotent), which needs EXECUTE; step 4
-- replaces it with 590's static body in the same file and the static revoke/grant restate the ACL.

set statement_timeout = '900s';

-- ---------------------------------------------------------------------------
-- 0. Preconditions.
-- ---------------------------------------------------------------------------
do $pre$
declare
  populated bool;
begin
  select count(*) = 100000 into populated
    from (select 1 from public.properties limit 100000) probe;
  if not populated then
    raise notice '625: replay container (corpus < 100k properties), preconditions skipped';
    return;
  end if;
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_stats_properties'), '')
     not in ('becfd9b38a85398a6f67d4b0875b6e05', '6b102dd7960cced4ffbbf31127048428') then
    raise exception '625 refused: browse_stats_properties is not 605''s body nor this file''s (or has two overloads)';
  end if;
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_map_cells'), '')
     not in ('23e6d08a0c743d1a74bbfe9a66c16a76', '7ae1999dccd5619b634c3a92d923e2c1') then
    raise exception '625 refused: browse_map_cells is not 605''s body nor this file''s (or has two overloads)';
  end if;
  if coalesce((select md5(prosrc) from pg_proc
                 where oid = to_regprocedure('public.properties_map_visible()')), '')
     not in ('87ac5ed4f4c1eaaef58a06c9a4e42071', 'd959c6c42bf17ef41692676d0da7a500') then
    raise exception '625 refused: properties_map_visible() is neither 590''s body nor this file''s bridge';
  end if;
  if coalesce((select md5(prosrc) from pg_proc
                 where oid = to_regprocedure('public.rebuild_browse_list()')), '') <> 'be7b0ec2297d269f495a42fdcc474517'
     or coalesce((select md5(prosrc) from pg_proc
                 where oid = to_regprocedure('public.rebuild_properties_map_mv()')), '') <> 'e18ea5161c8af9ceab5b07a62b6dc37d' then
    raise exception '625 refused: a rebuild function is not the body this file forces (605 / 522)';
  end if;
end
$pre$;

-- ---------------------------------------------------------------------------
-- 1. Both rebuild locks first, queued; then fail fast.
-- ---------------------------------------------------------------------------
set statement_timeout = '1900s';
set lock_timeout = 0;

select pg_advisory_lock(hashtext('rebuild_browse_list'));
select pg_advisory_lock(hashtext('rebuild_properties_map_mv'));

set lock_timeout = '5s';
set statement_timeout = '900s';

-- ---------------------------------------------------------------------------
-- 2. TX A -- the bulk-day set and the measure.
-- ---------------------------------------------------------------------------
begin;

create materialized view if not exists public.ingest_bulk_days as
with days as (
  select source, category_main, category_type,
         (first_seen_at at time zone 'UTC')::date as day, count(*)::integer as sightings
  from public.listings
  where category_main is not null and category_type is not null
  group by 1, 2, 3, 4
), pairs as (
  select source, category_main, category_type, min(day) as first_day,
         percentile_cont(0.5) within group (order by sightings) as median_sightings
  from days group by 1, 2, 3
)
select d.source, d.category_main, d.category_type, d.day, d.sightings,
       (d.day = p.first_day) as is_first_day, p.median_sightings
from days d
join pairs p using (source, category_main, category_type)
where d.day = p.first_day or d.sightings > greatest(100, 4 * p.median_sightings)
with data;

create unique index if not exists ingest_bulk_days_key
  on public.ingest_bulk_days (source, category_main, category_type, day);

comment on materialized view public.ingest_bulk_days is
  'The sweep days (migration 625): one row per (portal, category, UTC day) on which the walk '
  'discovered the existing stock rather than saw ads appear -- the pair''s first day of sightings, '
  'or a day with more than greatest(100, 4 x the pair''s median daily sightings). Validated on the '
  'two portals that publish a listing date (2026-10-18): our sighting lags the portal by a median '
  '0.6 d on ordinary days and 16-77 d on these. Refreshed nightly (pg_cron refresh-ingest-bulk-days); '
  'read only through measure_tom_observed(). The extra columns say why a day is here.';

revoke all on public.ingest_bulk_days from public, anon;
grant select on public.ingest_bulk_days to authenticated, service_role;

create or replace function public.measure_tom_observed(
  p_source text,
  p_category_main text,
  p_category_type text,
  p_first_seen_at timestamptz
)
 returns boolean
 language sql
 stable parallel safe
as $function$
    select not exists (
        select 1 from public.ingest_bulk_days b
        where b.source = p_source
          and b.category_main = p_category_main
          and b.category_type = p_category_type
          and b.day = (p_first_seen_at at time zone 'UTC')::date
    )
$function$;

comment on function public.measure_tom_observed(text, text, text, timestamptz) is
  'THE "observed from the start" measure (migration 625): true when the ad''s first sighting fell on '
  'no sweep day of its (portal, category) in ingest_bulk_days, so measure_tom_days counts from the '
  'day the ad appeared; false when the sighting was part of a sweep and the TOM is a lower bound on '
  'an older start. An ad with no category cannot be judged and reads true. A property takes its '
  'EARLIEST ad''s answer (browse_projection). Python face: toolkit.measures.tom_observed_sql. No SET '
  'search_path: a SET clause blocks inlining.';

revoke all on function public.measure_tom_observed(text, text, text, timestamptz)
  from public, anon, authenticated;
grant execute on function public.measure_tom_observed(text, text, text, timestamptz)
  to authenticated, service_role;

commit;

-- ---------------------------------------------------------------------------
-- 3. TX B -- the table, then the projection, then the map's bridge.
-- ---------------------------------------------------------------------------
begin;

alter table public.browse_list add column if not exists tom_observed boolean;

-- 617's body; tom_observed appended.
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
        order by k.first_seen_at, k.id limit 1) as tom_observed
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

revoke all on browse_projection from anon, authenticated;
grant select on browse_projection to authenticated;

do $bridge$
begin
  if not exists (select 1 from pg_attribute
                  where attrelid = 'public.properties_map_mv'::regclass
                    and attname = 'tom_observed' and not attisdropped) then
    execute $fn$
create or replace function public.properties_map_visible()
returns setof public.browse_projection
language sql
stable
as $$
  select m.*, null::boolean as tom_observed from public.properties_map_mv m
  where not exists (
    select 1 from public.property_dismissals_public d where d.property_id = m.property_id)
$$
$fn$;
  end if;
end
$bridge$;

revoke execute on function public.properties_map_visible() from public, anon;
grant execute on function public.properties_map_visible() to authenticated, service_role;

commit;

-- ---------------------------------------------------------------------------
-- 4. The map, rebuilt (forced: this session holds the key), then 590's source, verbatim.
-- ---------------------------------------------------------------------------
set lock_timeout = 0;
set statement_timeout = '3600s';

begin;

select public.rebuild_properties_map_mv();

create or replace function public.properties_map_visible()
returns setof public.browse_projection
language sql
stable
as $$
  select m.* from public.properties_map_mv m
  where not exists (
    select 1 from public.property_dismissals_public d where d.property_id = m.property_id)
$$;

revoke execute on function public.properties_map_visible() from public, anon;
grant execute on function public.properties_map_visible() to authenticated, service_role;

commit;

-- ---------------------------------------------------------------------------
-- 5. The list, rebuilt (forced the same way) from the projection.
-- ---------------------------------------------------------------------------
select public.rebuild_browse_list();

-- ---------------------------------------------------------------------------
-- 6. The two aggregate RPCs, one transaction: 605's bodies with the parameter appended and the
--    predicate beside the tom_days bounds. New md5s: 6b102dd7960cced4ffbbf31127048428 / 7ae1999dccd5619b634c3a92d923e2c1.
-- ---------------------------------------------------------------------------
set lock_timeout = '5s';
set statement_timeout = '900s';

begin;

drop function if exists public.browse_stats_properties(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], boolean, text[], boolean, bigint[]);
drop function if exists public.browse_map_cells(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], integer, boolean, text[], boolean, bigint[]);

create or replace function public.browse_stats_properties(
  districts_filter text[] default null::text[],
  dispositions_filter text[] default null::text[],
  price_min_filter integer default null::integer,
  price_max_filter integer default null::integer,
  area_min_filter integer default null::integer,
  area_max_filter integer default null::integer,
  active_only_filter boolean default false,
  last_seen_min_days integer default null::integer,
  last_seen_max_days integer default null::integer,
  first_seen_min_days integer default null::integer,
  first_seen_max_days integer default null::integer,
  tom_days_min integer default null::integer,
  tom_days_max integer default null::integer,
  has_balcony_filter boolean default null::boolean,
  has_lift_filter boolean default null::boolean,
  has_parking_filter boolean default null::boolean,
  inactive_only_filter boolean default false,
  furnished_filter text[] default null::text[],
  terrace_filter boolean default null::boolean,
  cellar_filter boolean default null::boolean,
  garage_filter boolean default null::boolean,
  category_sub_cb_filter integer default null::integer,
  building_type_filter text[] default null::text[],
  category_main_filter text[] default null::text[],
  category_type_filter text default null::text,
  bbox_west double precision default null::double precision,
  bbox_south double precision default null::double precision,
  bbox_east double precision default null::double precision,
  bbox_north double precision default null::double precision,
  ownership_filter text[] default null::text[],
  estate_area_min_filter double precision default null::double precision,
  estate_area_max_filter double precision default null::double precision,
  usable_area_min_filter double precision default null::double precision,
  usable_area_max_filter double precision default null::double precision,
  parking_lots_min_filter integer default null::integer,
  garden_area_min_filter double precision default null::double precision,
  garden_area_max_filter double precision default null::double precision,
  condition_match_filter text[] default null::text[],
  city_pop_min integer default null::integer,
  city_pop_max integer default null::integer,
  price_per_m2_min double precision default null::double precision,
  price_per_m2_max double precision default null::double precision,
  portal_filter text[] default null::text[],
  mf_gross_yield_pct_min double precision default null::double precision,
  mf_gross_yield_pct_max double precision default null::double precision,
  near_pop_5km_min integer default null::integer,
  near_pop_15km_min integer default null::integer,
  near_jobs_5km_min double precision default null::double precision,
  near_jobs_15km_min double precision default null::double precision,
  near_youth_5km_min double precision default null::double precision,
  near_youth_15km_min double precision default null::double precision,
  near_overall_5km_min double precision default null::double precision,
  near_overall_15km_min double precision default null::double precision,
  districts_excluded_filter boolean[] default null::boolean[],
  subtype_filter text[] default null::text[],
  recently_added_days integer default null::integer,
  recently_changed_days integer default null::integer,
  obec_ids_filter bigint[] default null::bigint[],
  districts_levels text[] default null::text[],
  districts_ids bigint[] default null::bigint[],
  building_condition_level_min integer default null::integer,
  building_condition_level_max integer default null::integer,
  apartment_condition_level_min integer default null::integer,
  apartment_condition_level_max integer default null::integer,
  price_change_count_min integer default null::integer,
  price_change_window_days integer default null::integer,
  total_price_change_pct_filter double precision default null::double precision,
  include_no_price boolean default false,
  property_ids_filter bigint[] default null::bigint[],
  hide_dismissed boolean default false,
  -- migration 597: the portals a property must have no ad on (see the header).
  portal_excluded_filter text[] default null::text[],
  portal_excluded_active_only boolean default false,
  -- migration 598: the property-id denylist (see the header).
  property_ids_excluded bigint[] default null::bigint[],
  tom_observed_only boolean default false
)
returns jsonb
language plpgsql
stable
set plan_cache_mode to 'force_custom_plan'
as $function$
begin
  return (
  with filtered as (
    select l.sreality_id, l.first_seen_at, l.last_seen_at, l.is_active, l.price_czk, l.area_m2, l.disposition, l.tom_days, l.price_per_m2, l.category_main, l.category_type
    from browse_list l
    where
          public.portal_status_matches(l.is_active, l.all_sources, l.active_sources, portal_filter,
            case when active_only_filter then 'active' when inactive_only_filter then 'inactive' else 'any' end,
            portal_excluded_filter, portal_excluded_active_only)
      and (last_seen_max_days is null or l.last_seen_at >= now() - (last_seen_max_days || ' days')::interval)
      and (last_seen_min_days is null or l.last_seen_at <= now() - (last_seen_min_days || ' days')::interval)
      and (first_seen_max_days is null or l.first_seen_at >= now() - (first_seen_max_days || ' days')::interval)
      and (first_seen_min_days is null or l.first_seen_at <= now() - (first_seen_min_days || ' days')::interval)
      and (recently_added_days   is null or l.first_seen_at  >= now() - (recently_added_days   || ' days')::interval)
      and (recently_changed_days is null or l.last_change_at >= now() - (recently_changed_days || ' days')::interval)
      and (tom_days_min is null or l.tom_days >= tom_days_min)
      and (tom_days_max is null or l.tom_days <= tom_days_max)
      and (not tom_observed_only or l.tom_observed)
      and (category_main_filter   is null or array_length(category_main_filter, 1) is null or l.category_main = any(category_main_filter))
      and (category_type_filter   is null or l.category_type   = category_type_filter)
      and (
        districts_filter is null or array_length(districts_filter, 1) is null
        or not exists (
          select 1 from unnest(districts_filter,
                 coalesce(districts_excluded_filter, array_fill(false, array[array_length(districts_filter, 1)]))
               ) with ordinality as t(needle, excl, ord)
          where not coalesce(excl, false)
        )
        or exists (
          select 1 from unnest(districts_filter,
                 coalesce(districts_excluded_filter, array_fill(false, array[array_length(districts_filter, 1)])),
                 coalesce(districts_levels, array_fill(null::text, array[array_length(districts_filter, 1)])),
                 coalesce(districts_ids, array_fill(null::bigint, array[array_length(districts_filter, 1)]))
               ) with ordinality as t(needle, excl, lvl, admin_id, ord)
          where not coalesce(excl, false)
            and case
              -- ONE code predicate (W3 S3): a chip is a level plus a RUIAN code,
              -- matched with plain equality. No ILIKE, no place_search_text, no
              -- context narrow. A chip with no code matches NOTHING (the `else`
              -- and the null guard) -- a saved filter we cannot resolve must
              -- never widen a cohort.
              when admin_id is null                  then false
              when lvl = 'kraj'                      then l.region_id    = admin_id
              when lvl = 'okres'                     then l.okres_id     = admin_id
              when lvl = 'obec'                      then l.obec_id      = admin_id
              when lvl = 'cast_obce'                 then l.cast_obce_id = admin_id
              when lvl = 'ulice'                     then l.ulice_id     = admin_id
              else false
            end
        )
      )
      and (
        districts_filter is null or array_length(districts_filter, 1) is null
        or not exists (
          select 1 from unnest(districts_filter,
                 coalesce(districts_excluded_filter, array_fill(false, array[array_length(districts_filter, 1)])),
                 coalesce(districts_levels, array_fill(null::text, array[array_length(districts_filter, 1)])),
                 coalesce(districts_ids, array_fill(null::bigint, array[array_length(districts_filter, 1)]))
               ) with ordinality as t(needle, excl, lvl, admin_id, ord)
          where coalesce(excl, false)
            and case
              -- ONE code predicate (W3 S3): a chip is a level plus a RUIAN code,
              -- matched with plain equality. No ILIKE, no place_search_text, no
              -- context narrow. A chip with no code matches NOTHING (the `else`
              -- and the null guard) -- a saved filter we cannot resolve must
              -- never widen a cohort.
              when admin_id is null                  then false
              when lvl = 'kraj'                      then l.region_id    = admin_id
              when lvl = 'okres'                     then l.okres_id     = admin_id
              when lvl = 'obec'                      then l.obec_id      = admin_id
              when lvl = 'cast_obce'                 then l.cast_obce_id = admin_id
              when lvl = 'ulice'                     then l.ulice_id     = admin_id
              else false
            end
        )
      )
      and (dispositions_filter    is null or l.disposition     = any(dispositions_filter))
      and (price_min_filter       is null or (include_no_price and l.price_czk is null) or l.price_czk >= price_min_filter)
      and (price_max_filter       is null or (include_no_price and l.price_czk is null) or l.price_czk <= price_max_filter)
      and (area_min_filter        is null or l.area_m2        >= area_min_filter)
      and (area_max_filter        is null or l.area_m2        <= area_max_filter)
      and (price_per_m2_min is null or l.price_per_m2 >= price_per_m2_min)
      and (price_per_m2_max is null or l.price_per_m2 <= price_per_m2_max)
      and (mf_gross_yield_pct_min is null or l.mf_gross_yield_pct >= mf_gross_yield_pct_min)
      and (mf_gross_yield_pct_max is null or l.mf_gross_yield_pct <= mf_gross_yield_pct_max)
      and (has_balcony_filter     is null or l.has_balcony     = has_balcony_filter)
      and (has_lift_filter        is null or l.has_lift        = has_lift_filter)
      and (has_parking_filter     is null or l.has_parking     = has_parking_filter)
      and (
        furnished_filter is null or array_length(furnished_filter, 1) is null
        or l.furnished = any(furnished_filter)
        or ('__unknown__' = any(furnished_filter)
            and (l.furnished is null or not (l.furnished = any(array['ano','ne','castecne']))))
      )
      and (terrace_filter         is null or l.terrace         = terrace_filter)
      and (cellar_filter          is null or l.cellar          = cellar_filter)
      and (garage_filter          is null or l.garage          = garage_filter)
      and (category_sub_cb_filter is null or l.category_sub_cb = category_sub_cb_filter)
      and (subtype_filter is null or array_length(subtype_filter, 1) is null or l.subtype = any(subtype_filter))
      and (building_type_filter   is null or array_length(building_type_filter, 1) is null or l.building_type = any(building_type_filter))
      and (condition_match_filter is null or array_length(condition_match_filter, 1) is null or l.condition = any(condition_match_filter))
      and (
        ownership_filter is null or array_length(ownership_filter, 1) is null
        or l.ownership = any(ownership_filter)
        or ('__unknown__' = any(ownership_filter)
            and (l.ownership is null or not (l.ownership = any(array['osobni','druzstevni','statni','jine']))))
      )
      and (estate_area_min_filter  is null or l.plot_area_m2  >= estate_area_min_filter)
      and (estate_area_max_filter  is null or l.plot_area_m2  <= estate_area_max_filter)
      and (usable_area_min_filter  is null or l.usable_area   >= usable_area_min_filter)
      and (usable_area_max_filter  is null or l.usable_area   <= usable_area_max_filter)
      and (parking_lots_min_filter is null or l.parking_lots  >= parking_lots_min_filter)
      and (garden_area_min_filter  is null or l.garden_area   >= garden_area_min_filter)
      and (garden_area_max_filter  is null or l.garden_area   <= garden_area_max_filter)
      and (bbox_west  is null or l.lng >= bbox_west)
      and (bbox_east  is null or l.lng <= bbox_east)
      and (bbox_south is null or l.lat >= bbox_south)
      and (bbox_north is null or l.lat <= bbox_north)
      and (building_condition_level_min  is null or l.building_condition_level  >= building_condition_level_min)
      and (building_condition_level_max  is null or l.building_condition_level  <= building_condition_level_max)
      and (apartment_condition_level_min is null or l.apartment_condition_level >= apartment_condition_level_min)
      and (apartment_condition_level_max is null or l.apartment_condition_level <= apartment_condition_level_max)
      and (price_change_count_min is null or
           (case when price_change_window_days = 30  then l.price_change_count_30d
                 when price_change_window_days = 90  then l.price_change_count_90d
                 when price_change_window_days = 365 then l.price_change_count_365d
                 else l.price_change_count end) >= price_change_count_min)
      and (total_price_change_pct_filter is null or total_price_change_pct_filter = 0
           or (total_price_change_pct_filter < 0 and l.total_price_change_pct <= total_price_change_pct_filter)
           or (total_price_change_pct_filter > 0 and l.total_price_change_pct >= total_price_change_pct_filter))
      and (obec_ids_filter is null or l.obec_id = any(obec_ids_filter))
      and (property_ids_filter is null or l.property_id = any(property_ids_filter))
      and (property_ids_excluded is null or not (l.property_id = any(property_ids_excluded)))
      and (not hide_dismissed or not exists (
            select 1 from property_dismissals_public d where d.property_id = l.property_id))
      and (city_pop_min is null or l.home_obec_pop >= city_pop_min)
      and (city_pop_max is null or l.home_obec_pop <= city_pop_max)
      and (near_pop_5km_min      is null or l.near_pop_5km      >= near_pop_5km_min)
      and (near_pop_15km_min     is null or l.near_pop_15km     >= near_pop_15km_min)
      and (near_jobs_5km_min     is null or l.near_jobs_5km     >= near_jobs_5km_min)
      and (near_jobs_15km_min    is null or l.near_jobs_15km    >= near_jobs_15km_min)
      and (near_youth_5km_min    is null or l.near_youth_5km    >= near_youth_5km_min)
      and (near_youth_15km_min   is null or l.near_youth_15km   >= near_youth_15km_min)
      and (near_overall_5km_min  is null or l.near_overall_5km  >= near_overall_5km_min)
      and (near_overall_15km_min is null or l.near_overall_15km >= near_overall_15km_min)
      and true
  ),
  price_pct as (select percentile_cont(0.25) within group (order by price_czk)::int as p25, percentile_cont(0.50) within group (order by price_czk)::int as p50, percentile_cont(0.75) within group (order by price_czk)::int as p75 from filtered where price_czk is not null),
  ppm2_pct as (select percentile_cont(0.25) within group (order by price_per_m2)::int as p25, percentile_cont(0.50) within group (order by price_per_m2)::int as p50, percentile_cont(0.75) within group (order by price_per_m2)::int as p75 from filtered where price_per_m2 is not null),
  ppm2_basis as (select case when count(distinct b) = 0 then null when count(distinct b) = 1 then min(b) else 'mixed' end as basis from (select measure_price_per_m2_basis(category_main, category_type) as b from filtered where price_per_m2 is not null) t),
  disposition_dist as (select coalesce(disposition, 'unspecified') as disposition, count(*)::int as n, count(price_per_m2)::int as ppm2_n, min(price_per_m2)::int as ppm2_min, percentile_cont(0.25) within group (order by price_per_m2)::int as ppm2_p25, percentile_cont(0.50) within group (order by price_per_m2)::int as ppm2_median, percentile_cont(0.75) within group (order by price_per_m2)::int as ppm2_p75, max(price_per_m2)::int as ppm2_max from filtered group by disposition order by n desc, disposition asc),
  price_cuts as (select percentile_cont(0.10) within group (order by price_czk) as cut_10, percentile_cont(0.25) within group (order by price_czk) as cut_25, percentile_cont(0.45) within group (order by price_czk) as cut_45, percentile_cont(0.55) within group (order by price_czk) as cut_55, percentile_cont(0.75) within group (order by price_czk) as cut_75, percentile_cont(0.90) within group (order by price_czk) as cut_90, count(*)::int as priced_total from filtered where price_czk is not null),
  price_bands as (select f.price_czk, f.tom_days, case when f.price_czk <= c.cut_10 then 1 when f.price_czk <= c.cut_25 then 2 when f.price_czk <= c.cut_45 then 3 when f.price_czk <= c.cut_55 then 4 when f.price_czk <= c.cut_75 then 5 when f.price_czk <= c.cut_90 then 6 else 7 end as bucket, c.priced_total from filtered f, price_cuts c where f.price_czk is not null),
  band_definitions(bucket, p_lo, p_hi) as (values (1, 0, 10), (2, 10, 25), (3, 25, 45), (4, 45, 55), (5, 55, 75), (6, 75, 90), (7, 90, 100)),
  band_stats as (select d.bucket, d.p_lo, d.p_hi, count(b.price_czk)::int as n, max(b.priced_total) as priced_total, min(b.price_czk)::int as price_min, max(b.price_czk)::int as price_max, count(b.tom_days)::int as tom_n, min(b.tom_days)::int as tom_min, percentile_cont(0.25) within group (order by b.tom_days) filter (where b.tom_days is not null) as tom_p25, percentile_cont(0.50) within group (order by b.tom_days) filter (where b.tom_days is not null) as tom_median, percentile_cont(0.75) within group (order by b.tom_days) filter (where b.tom_days is not null) as tom_p75, max(b.tom_days)::int as tom_max, avg(b.tom_days) filter (where b.tom_days is not null) as tom_mean from band_definitions d left join price_bands b on b.bucket = d.bucket group by d.bucket, d.p_lo, d.p_hi order by d.bucket)
  select jsonb_build_object(
    'total', (select count(*)::int from filtered),
    'new_7d', (select count(*)::int from filtered where first_seen_at >= now() - interval '7 days'),
    'new_30d', (select count(*)::int from filtered where first_seen_at >= now() - interval '30 days'),
    'price', (select case when p50 is null then null else jsonb_build_object('p25', p25, 'p50', p50, 'p75', p75) end from price_pct),
    'ppm2', (select case when p50 is null then null else jsonb_build_object('p25', p25, 'p50', p50, 'p75', p75) end from ppm2_pct),
    'ppm2_basis', (select basis from ppm2_basis),
    'dispositions', coalesce((select jsonb_agg(jsonb_build_object('disposition', disposition, 'n', n, 'ppm2_box', case when ppm2_n > 0 then jsonb_build_object('n', ppm2_n, 'min', ppm2_min, 'p25', ppm2_p25, 'median', ppm2_median, 'p75', ppm2_p75, 'max', ppm2_max) else null end)) from disposition_dist), '[]'::jsonb),
    'price_band_velocity', coalesce((select jsonb_agg(jsonb_build_object('bucket', bs.bucket, 'p_lo', bs.p_lo, 'p_hi', bs.p_hi, 'n', bs.n, 'pct_share', case when bs.priced_total is null or bs.priced_total = 0 then null else round(bs.n * 100.0 / bs.priced_total, 1) end, 'price_min', bs.price_min, 'price_max', bs.price_max, 'tom_box', case when bs.tom_n > 0 then jsonb_build_object('n', bs.tom_n, 'min', bs.tom_min, 'p25', round(bs.tom_p25::numeric, 1), 'median', round(bs.tom_median::numeric, 1), 'mean', round(bs.tom_mean::numeric, 1), 'p75', round(bs.tom_p75::numeric, 1), 'max', bs.tom_max) else null end) order by bs.p_lo) from band_stats bs), '[]'::jsonb)
  )
  );
end
$function$;

revoke execute on function public.browse_stats_properties(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], boolean, text[], boolean, bigint[], boolean) from public, anon;
grant execute on function public.browse_stats_properties(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], boolean, text[], boolean, bigint[], boolean) to authenticated, service_role;

create or replace function public.browse_map_cells(
  districts_filter text[] default null::text[],
  dispositions_filter text[] default null::text[],
  price_min_filter integer default null::integer,
  price_max_filter integer default null::integer,
  area_min_filter integer default null::integer,
  area_max_filter integer default null::integer,
  active_only_filter boolean default false,
  last_seen_min_days integer default null::integer,
  last_seen_max_days integer default null::integer,
  first_seen_min_days integer default null::integer,
  first_seen_max_days integer default null::integer,
  tom_days_min integer default null::integer,
  tom_days_max integer default null::integer,
  has_balcony_filter boolean default null::boolean,
  has_lift_filter boolean default null::boolean,
  has_parking_filter boolean default null::boolean,
  inactive_only_filter boolean default false,
  furnished_filter text[] default null::text[],
  terrace_filter boolean default null::boolean,
  cellar_filter boolean default null::boolean,
  garage_filter boolean default null::boolean,
  category_sub_cb_filter integer default null::integer,
  building_type_filter text[] default null::text[],
  category_main_filter text[] default null::text[],
  category_type_filter text default null::text,
  bbox_west double precision default null::double precision,
  bbox_south double precision default null::double precision,
  bbox_east double precision default null::double precision,
  bbox_north double precision default null::double precision,
  ownership_filter text[] default null::text[],
  estate_area_min_filter double precision default null::double precision,
  estate_area_max_filter double precision default null::double precision,
  usable_area_min_filter double precision default null::double precision,
  usable_area_max_filter double precision default null::double precision,
  parking_lots_min_filter integer default null::integer,
  garden_area_min_filter double precision default null::double precision,
  garden_area_max_filter double precision default null::double precision,
  condition_match_filter text[] default null::text[],
  city_pop_min integer default null::integer,
  city_pop_max integer default null::integer,
  price_per_m2_min double precision default null::double precision,
  price_per_m2_max double precision default null::double precision,
  portal_filter text[] default null::text[],
  mf_gross_yield_pct_min double precision default null::double precision,
  mf_gross_yield_pct_max double precision default null::double precision,
  near_pop_5km_min integer default null::integer,
  near_pop_15km_min integer default null::integer,
  near_jobs_5km_min double precision default null::double precision,
  near_jobs_15km_min double precision default null::double precision,
  near_youth_5km_min double precision default null::double precision,
  near_youth_15km_min double precision default null::double precision,
  near_overall_5km_min double precision default null::double precision,
  near_overall_15km_min double precision default null::double precision,
  districts_excluded_filter boolean[] default null::boolean[],
  subtype_filter text[] default null::text[],
  recently_added_days integer default null::integer,
  recently_changed_days integer default null::integer,
  obec_ids_filter bigint[] default null::bigint[],
  districts_levels text[] default null::text[],
  districts_ids bigint[] default null::bigint[],
  building_condition_level_min integer default null::integer,
  building_condition_level_max integer default null::integer,
  apartment_condition_level_min integer default null::integer,
  apartment_condition_level_max integer default null::integer,
  price_change_count_min integer default null::integer,
  price_change_window_days integer default null::integer,
  total_price_change_pct_filter double precision default null::double precision,
  include_no_price boolean default false,
  property_ids_filter bigint[] default null::bigint[],
  -- The one parameter browse_stats_properties does not have: above this many
  -- mappable rows the answer is cells; at or below it the caller re-reads the
  -- cohort as points. Measured: a 2,000-row point read is 153 blocks and
  -- ~906 KB, which is the ceiling this threshold buys.
  point_budget integer default 2000,
  -- migration 537: exclude the caller's dismissed properties (RLS-scoped).
  hide_dismissed boolean default false,
  -- migration 597: the portals a property must have no ad on (see the header).
  portal_excluded_filter text[] default null::text[],
  portal_excluded_active_only boolean default false,
  -- migration 598: the property-id denylist (see the header).
  property_ids_excluded bigint[] default null::bigint[],
  tom_observed_only boolean default false
)
returns jsonb
language plpgsql
stable
-- Mostly-NULL parameters: a generic plan would pick one shape and use it for every
-- cohort. Same setting, same reason, as browse_stats_properties.
set plan_cache_mode to 'force_custom_plan'
as $function$
declare
  -- Bounded by construction: the finest tile level with at most this many occupied tiles.
  c_max_cells constant integer := 1024;
  v_x integer[];
  v_y integer[];
  v_n integer[];
  v_total bigint;
  v_level integer := 18;
  v_cells jsonb;
begin
  -- ONE index-only pass over the cover index: the target list reads nothing but l.lat and
  -- l.lng (tests/test_browse_map_cells_plan.py). Level-18 world tiles, ~100 m at lat 50.
  with g as (
    select
      least(greatest(floor((l.lng + 180) / 360 * 262144), 0), 262143)::int as x,
      least(greatest(floor((1 - ln((1 + sin(radians(least(greatest(l.lat, -85.05112878), 85.05112878)))) / (1 - sin(radians(least(greatest(l.lat, -85.05112878), 85.05112878))))) / (2 * pi())) / 2 * 262144), 0), 262143)::int as y,
      count(*)::int as n
    from properties_map_mv l
    where
          public.portal_status_matches(l.is_active, l.all_sources, l.active_sources, portal_filter,
            case when active_only_filter then 'active' when inactive_only_filter then 'inactive' else 'any' end,
            portal_excluded_filter, portal_excluded_active_only)
      and (last_seen_max_days is null or l.last_seen_at >= now() - (last_seen_max_days || ' days')::interval)
      and (last_seen_min_days is null or l.last_seen_at <= now() - (last_seen_min_days || ' days')::interval)
      and (first_seen_max_days is null or l.first_seen_at >= now() - (first_seen_max_days || ' days')::interval)
      and (first_seen_min_days is null or l.first_seen_at <= now() - (first_seen_min_days || ' days')::interval)
      and (recently_added_days   is null or l.first_seen_at  >= now() - (recently_added_days   || ' days')::interval)
      and (recently_changed_days is null or l.last_change_at >= now() - (recently_changed_days || ' days')::interval)
      and (tom_days_min is null or l.tom_days >= tom_days_min)
      and (tom_days_max is null or l.tom_days <= tom_days_max)
      and (not tom_observed_only or l.tom_observed)
      and (category_main_filter   is null or array_length(category_main_filter, 1) is null or l.category_main = any(category_main_filter))
      and (category_type_filter   is null or l.category_type   = category_type_filter)
      and (
        districts_filter is null or array_length(districts_filter, 1) is null
        or not exists (
          select 1 from unnest(districts_filter,
                 coalesce(districts_excluded_filter, array_fill(false, array[array_length(districts_filter, 1)]))
               ) with ordinality as t(needle, excl, ord)
          where not coalesce(excl, false)
        )
        or exists (
          select 1 from unnest(districts_filter,
                 coalesce(districts_excluded_filter, array_fill(false, array[array_length(districts_filter, 1)])),
                 coalesce(districts_levels, array_fill(null::text, array[array_length(districts_filter, 1)])),
                 coalesce(districts_ids, array_fill(null::bigint, array[array_length(districts_filter, 1)]))
               ) with ordinality as t(needle, excl, lvl, admin_id, ord)
          where not coalesce(excl, false)
            and case
              -- ONE code predicate (W3 S3): a chip is a level plus a RUIAN code,
              -- matched with plain equality. No ILIKE, no place_search_text, no
              -- context narrow. A chip with no code matches NOTHING (the `else`
              -- and the null guard) -- a saved filter we cannot resolve must
              -- never widen a cohort.
              when admin_id is null                  then false
              when lvl = 'kraj'                      then l.region_id    = admin_id
              when lvl = 'okres'                     then l.okres_id     = admin_id
              when lvl = 'obec'                      then l.obec_id      = admin_id
              when lvl = 'cast_obce'                 then l.cast_obce_id = admin_id
              when lvl = 'ulice'                     then l.ulice_id     = admin_id
              else false
            end
        )
      )
      and (
        districts_filter is null or array_length(districts_filter, 1) is null
        or not exists (
          select 1 from unnest(districts_filter,
                 coalesce(districts_excluded_filter, array_fill(false, array[array_length(districts_filter, 1)])),
                 coalesce(districts_levels, array_fill(null::text, array[array_length(districts_filter, 1)])),
                 coalesce(districts_ids, array_fill(null::bigint, array[array_length(districts_filter, 1)]))
               ) with ordinality as t(needle, excl, lvl, admin_id, ord)
          where coalesce(excl, false)
            and case
              -- ONE code predicate (W3 S3): a chip is a level plus a RUIAN code,
              -- matched with plain equality. No ILIKE, no place_search_text, no
              -- context narrow. A chip with no code matches NOTHING (the `else`
              -- and the null guard) -- a saved filter we cannot resolve must
              -- never widen a cohort.
              when admin_id is null                  then false
              when lvl = 'kraj'                      then l.region_id    = admin_id
              when lvl = 'okres'                     then l.okres_id     = admin_id
              when lvl = 'obec'                      then l.obec_id      = admin_id
              when lvl = 'cast_obce'                 then l.cast_obce_id = admin_id
              when lvl = 'ulice'                     then l.ulice_id     = admin_id
              else false
            end
        )
      )
      and (dispositions_filter    is null or l.disposition     = any(dispositions_filter))
      and (price_min_filter       is null or (include_no_price and l.price_czk is null) or l.price_czk >= price_min_filter)
      and (price_max_filter       is null or (include_no_price and l.price_czk is null) or l.price_czk <= price_max_filter)
      and (area_min_filter        is null or l.area_m2        >= area_min_filter)
      and (area_max_filter        is null or l.area_m2        <= area_max_filter)
      and (price_per_m2_min is null or l.price_per_m2 >= price_per_m2_min)
      and (price_per_m2_max is null or l.price_per_m2 <= price_per_m2_max)
      and (mf_gross_yield_pct_min is null or l.mf_gross_yield_pct >= mf_gross_yield_pct_min)
      and (mf_gross_yield_pct_max is null or l.mf_gross_yield_pct <= mf_gross_yield_pct_max)
      and (has_balcony_filter     is null or l.has_balcony     = has_balcony_filter)
      and (has_lift_filter        is null or l.has_lift        = has_lift_filter)
      and (has_parking_filter     is null or l.has_parking     = has_parking_filter)
      and (
        furnished_filter is null or array_length(furnished_filter, 1) is null
        or l.furnished = any(furnished_filter)
        or ('__unknown__' = any(furnished_filter)
            and (l.furnished is null or not (l.furnished = any(array['ano','ne','castecne']))))
      )
      and (terrace_filter         is null or l.terrace         = terrace_filter)
      and (cellar_filter          is null or l.cellar          = cellar_filter)
      and (garage_filter          is null or l.garage          = garage_filter)
      and (category_sub_cb_filter is null or l.category_sub_cb = category_sub_cb_filter)
      and (subtype_filter is null or array_length(subtype_filter, 1) is null or l.subtype = any(subtype_filter))
      and (building_type_filter   is null or array_length(building_type_filter, 1) is null or l.building_type = any(building_type_filter))
      and (condition_match_filter is null or array_length(condition_match_filter, 1) is null or l.condition = any(condition_match_filter))
      and (
        ownership_filter is null or array_length(ownership_filter, 1) is null
        or l.ownership = any(ownership_filter)
        or ('__unknown__' = any(ownership_filter)
            and (l.ownership is null or not (l.ownership = any(array['osobni','druzstevni','statni','jine']))))
      )
      and (estate_area_min_filter  is null or l.plot_area_m2  >= estate_area_min_filter)
      and (estate_area_max_filter  is null or l.plot_area_m2  <= estate_area_max_filter)
      and (usable_area_min_filter  is null or l.usable_area   >= usable_area_min_filter)
      and (usable_area_max_filter  is null or l.usable_area   <= usable_area_max_filter)
      and (parking_lots_min_filter is null or l.parking_lots  >= parking_lots_min_filter)
      and (garden_area_min_filter  is null or l.garden_area   >= garden_area_min_filter)
      and (garden_area_max_filter  is null or l.garden_area   <= garden_area_max_filter)
      and (bbox_west  is null or l.lng >= bbox_west)
      and (bbox_east  is null or l.lng <= bbox_east)
      and (bbox_south is null or l.lat >= bbox_south)
      and (bbox_north is null or l.lat <= bbox_north)
      and (building_condition_level_min  is null or l.building_condition_level  >= building_condition_level_min)
      and (building_condition_level_max  is null or l.building_condition_level  <= building_condition_level_max)
      and (apartment_condition_level_min is null or l.apartment_condition_level >= apartment_condition_level_min)
      and (apartment_condition_level_max is null or l.apartment_condition_level <= apartment_condition_level_max)
      and (price_change_count_min is null or
           (case when price_change_window_days = 30  then l.price_change_count_30d
                 when price_change_window_days = 90  then l.price_change_count_90d
                 when price_change_window_days = 365 then l.price_change_count_365d
                 else l.price_change_count end) >= price_change_count_min)
      and (total_price_change_pct_filter is null or total_price_change_pct_filter = 0
           or (total_price_change_pct_filter < 0 and l.total_price_change_pct <= total_price_change_pct_filter)
           or (total_price_change_pct_filter > 0 and l.total_price_change_pct >= total_price_change_pct_filter))
      and (obec_ids_filter is null or l.obec_id = any(obec_ids_filter))
      and (property_ids_filter is null or l.property_id = any(property_ids_filter))
      and (property_ids_excluded is null or not (l.property_id = any(property_ids_excluded)))
      and (not hide_dismissed or not exists (
            select 1 from property_dismissals_public d where d.property_id = l.property_id))
      and (city_pop_min is null or l.home_obec_pop >= city_pop_min)
      and (city_pop_max is null or l.home_obec_pop <= city_pop_max)
      and (near_pop_5km_min      is null or l.near_pop_5km      >= near_pop_5km_min)
      and (near_pop_15km_min     is null or l.near_pop_15km     >= near_pop_15km_min)
      and (near_jobs_5km_min     is null or l.near_jobs_5km     >= near_jobs_5km_min)
      and (near_jobs_15km_min    is null or l.near_jobs_15km    >= near_jobs_15km_min)
      and (near_youth_5km_min    is null or l.near_youth_5km    >= near_youth_5km_min)
      and (near_youth_15km_min   is null or l.near_youth_15km   >= near_youth_15km_min)
      and (near_overall_5km_min  is null or l.near_overall_5km  >= near_overall_5km_min)
      and (near_overall_15km_min is null or l.near_overall_15km >= near_overall_15km_min)
      and true
    group by 1, 2
  )
  select array_agg(x), array_agg(y), array_agg(n), coalesce(sum(n), 0)
    into v_x, v_y, v_n, v_total
  from g;

  -- At or below the budget the caller re-reads the cohort as points, so the cells are
  -- withheld rather than shipped-and-ignored. `clustered` is what the caller branches on;
  -- it is never inferred from the array (an empty cohort is not a small one).
  if v_total <= point_budget then
    return jsonb_build_object('total', v_total, 'clustered', false, 'level', null, 'cells', null);
  end if;

  -- Coarsen IN PLACE, level by level (x >> 1, y >> 1, counts summed), until at most
  -- c_max_cells tiles are occupied: each pass groups the previous level's groups, never the
  -- base rows, and the arrays shrink as they go. Level 0 is one tile, so this always
  -- terminates bounded.
  loop
    exit when coalesce(cardinality(v_x), 0) <= c_max_cells or v_level = 0;
    select array_agg(t.x), array_agg(t.y), array_agg(t.n)
      into v_x, v_y, v_n
    from (
      select r.x >> 1 as x, r.y >> 1 as y, sum(r.n)::int as n
      from unnest(v_x, v_y, v_n) as r(x, y, n)
      group by 1, 2
    ) t;
    v_level := v_level - 1;
  end loop;

  -- The tile CENTRE (5 decimals, ~1 m): the SPA regroups cells at its own level with the
  -- same formula, so a centre must fall inside its own tile and its parents, which it does.
  select jsonb_agg(jsonb_build_object(
           'lat', round(degrees(atan(sinh(pi() * (1 - 2 * (r.y + 0.5) / 2 ^ v_level))))::numeric, 5),
           'lng', round(((r.x + 0.5) / 2 ^ v_level * 360 - 180)::numeric, 5),
           'n', r.n) order by r.n desc)
    into v_cells
  from unnest(v_x, v_y, v_n) as r(x, y, n);

  return jsonb_build_object('total', v_total, 'clustered', true, 'level', v_level,
                            'cells', coalesce(v_cells, '[]'::jsonb));
end
$function$;

revoke execute on function public.browse_map_cells(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], integer, boolean, text[], boolean, bigint[], boolean) from public, anon;
grant execute on function public.browse_map_cells(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], integer, boolean, text[], boolean, bigint[], boolean) to authenticated, service_role;

commit;

-- ---------------------------------------------------------------------------
-- 7. The nightly refresh. Guarded like 620: the CI replay has no pg_cron.
-- ---------------------------------------------------------------------------
do $cron$
begin
  perform cron.schedule(
    'refresh-ingest-bulk-days',
    '40 3 * * *',
    $$set statement_timeout='300s'; refresh materialized view concurrently public.ingest_bulk_days;$$
  );
exception when others then
  raise notice 'pg_cron unavailable; refresh-ingest-bulk-days not scheduled (%). Refresh ingest_bulk_days on another scheduler.', sqlerrm;
end
$cron$;

-- ---------------------------------------------------------------------------
-- 8. Post-conditions (the shape holds on the empty replay schema too).
-- ---------------------------------------------------------------------------
do $post$
declare
  rel text;
  populated bool;
begin
  foreach rel in array array['public.browse_projection', 'public.browse_list', 'public.properties_map_mv'] loop
    if not exists (select 1 from pg_attribute
                    where attrelid = rel::regclass and attname = 'tom_observed' and not attisdropped) then
      raise exception '625: % lacks tom_observed', rel;
    end if;
  end loop;
  if (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
        and proname in ('browse_stats_properties', 'browse_map_cells')
        and pg_get_function_identity_arguments(oid) like '%tom_observed_only boolean%') <> 2 then
    raise exception '625: an aggregate RPC lacks tom_observed_only';
  end if;
  if (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
        and proname in ('browse_stats_properties', 'browse_map_cells')) <> 2 then
    raise exception '625: an aggregate RPC has two overloads';
  end if;
  if coalesce((select md5(prosrc) from pg_proc
                 where oid = to_regprocedure('public.properties_map_visible()')), '') <> '87ac5ed4f4c1eaaef58a06c9a4e42071' then
    raise exception '625: properties_map_visible() is not 590''s body after the map rebuild';
  end if;
  select count(*) = 100000 into populated
    from (select 1 from public.properties limit 100000) probe;
  if populated and (select count(*) from public.ingest_bulk_days) < 50 then
    raise exception '625: ingest_bulk_days holds % rows; expected the ~210 sweep days read on 2026-10-18',
      (select count(*) from public.ingest_bulk_days);
  end if;
end
$post$;

-- ---------------------------------------------------------------------------
-- 9. Hand the locks back; PostgREST re-reads the widened relations.
-- ---------------------------------------------------------------------------
select pg_advisory_unlock(hashtext('rebuild_browse_list'));
select pg_advisory_unlock(hashtext('rebuild_properties_map_mv'));

select pg_notify('pgrst', 'reload schema');

reset statement_timeout;
reset lock_timeout;
