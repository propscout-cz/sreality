-- 630_revert_one_browse_read_model.sql
--
-- REVERT for 630_one_browse_read_model.sql. SHIPPED UNAPPLIED.
--
-- Lives in migrations/reverts/ for the same two verified reasons as every revert in this
-- build: the CI schema replay applies `ls migrations/*.sql | sort` (NOT recursive), so a
-- revert beside its forward migration would be applied right after it and silently undo the
-- change in every replayed environment; and tests/test_migration_numbers.py globs the same
-- way and forbids a duplicate number above 304.
--
-- It restores, verbatim, the two bodies 630 replaced:
--   browse_map_cells        629's body (reads properties_map_mv)   md5(prosrc) 7406f8566504c33efc7fe4273b4f76cc
--   rebuild_browse_list()   605's body (605:650-722)               md5(prosrc) be7b0ec2297d269f495a42fdcc474517
-- Same signatures, so CREATE OR REPLACE keeps both ACLs. The post-condition re-hashes both.
--
-- WHAT APPLYING THIS COSTS, stated plainly so it is never done casually:
--   * the tile counts go back to properties_map_mv, stale for up to 31 minutes after every merge;
--   * a merge, detach or split that commits inside the list rebuild's copy-to-swap window (6.9 %
--     of wall-clock) is lost again until the next swap;
--   * the next list rebuild after this revert builds no browse_list_map_idx, so a map read on
--     browse_list loses its index.
-- ORDER: W1's SPA reads the pins from browse_list. Revert that code (git revert, deploy) together
-- with this file: whichever lands first, the pins and the tile counts read different relations
-- until the other does. It cannot apply once 631 has dropped properties_map_mv (the precondition
-- refuses). Prefer fixing forward.

begin;

set local lock_timeout = '5s';

do $pre$
begin
  if to_regclass('public.properties_map_mv') is null then
    raise exception '630 revert refused: properties_map_mv is gone (631 applied); 629''s map body would read nothing';
  end if;
  if coalesce((select md5(prosrc) from pg_proc
                 where oid = to_regprocedure('public.rebuild_browse_list()')), '')
     not in ('b422be1accf77776408c63adfc8a69a5', 'be7b0ec2297d269f495a42fdcc474517') then
    raise exception '630 revert refused: rebuild_browse_list() is not 630''s body (a later migration replaced it)';
  end if;
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_map_cells'), '')
     not in ('81b08b6aeb070b697155aba9d38fedd0', '7406f8566504c33efc7fe4273b4f76cc') then
    raise exception '630 revert refused: browse_map_cells is not 630''s body (a later migration replaced it, or two overloads)';
  end if;
end
$pre$;

-- 1. browse_map_cells back to 629's body.
drop index if exists public.property_merge_events_recent_idx;

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
,
  -- migration 629: the curated-set scope, three sets x three sides (see the header).
  tags_set text default null,
  tags_in bigint[] default null::bigint[],
  tags_out bigint[] default null::bigint[],
  collections_set text default null,
  collections_in bigint[] default null::bigint[],
  collections_out bigint[] default null::bigint[],
  pipeline_set text default null,
  pipeline_in bigint[] default null::bigint[],
  pipeline_out bigint[] default null::bigint[]
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
      -- migration 629: the curated-set scope. Three sets, three sides each, one clause shape,
      -- judged through the *_public membership views under the caller's RLS. A NULL side is
      -- off and its subquery never runs; `{}` on a unit list constrains nothing.
      and (tags_set is distinct from 'in' or exists (
            select 1 from property_tags_public c where c.property_id = l.property_id))
      and (tags_set is distinct from 'out' or not exists (
            select 1 from property_tags_public c where c.property_id = l.property_id))
      and (tags_in is null or array_length(tags_in, 1) is null or l.property_id in (
            select c.property_id from property_tags_public c where c.tag_id = any(tags_in)
            group by c.property_id
            having count(distinct c.tag_id) = (select count(distinct u) from unnest(tags_in) u)))
      and (tags_out is null or not exists (
            select 1 from property_tags_public c
            where c.property_id = l.property_id and c.tag_id = any(tags_out)))
      and (collections_set is distinct from 'in' or exists (
            select 1 from collection_properties_public c where c.property_id = l.property_id))
      and (collections_set is distinct from 'out' or not exists (
            select 1 from collection_properties_public c where c.property_id = l.property_id))
      and (collections_in is null or array_length(collections_in, 1) is null or exists (
            select 1 from collection_properties_public c
            where c.property_id = l.property_id and c.collection_id = any(collections_in)))
      and (collections_out is null or not exists (
            select 1 from collection_properties_public c
            where c.property_id = l.property_id and c.collection_id = any(collections_out)))
      and (pipeline_set is distinct from 'in' or exists (
            select 1 from property_pipeline_public c where c.property_id = l.property_id))
      and (pipeline_set is distinct from 'out' or not exists (
            select 1 from property_pipeline_public c where c.property_id = l.property_id))
      and (pipeline_in is null or array_length(pipeline_in, 1) is null or exists (
            select 1 from property_pipeline_public c
            where c.property_id = l.property_id and c.stage_id = any(pipeline_in)))
      and (pipeline_out is null or not exists (
            select 1 from property_pipeline_public c
            where c.property_id = l.property_id and c.stage_id = any(pipeline_out)))
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

-- 2. rebuild_browse_list() back to 605's body.
create or replace function public.rebuild_browse_list()
returns void
language plpgsql
security definer
set search_path to 'public'
as $function$
declare
  t0 timestamptz := clock_timestamp();
  n  bigint;
begin
  -- Transaction-scoped: released by commit, rollback OR cancel (W13).
  if not pg_try_advisory_xact_lock(hashtext('rebuild_browse_list')) then
    raise notice 'rebuild_browse_list: previous run still active, skipping tick';
    return;
  end if;

  execute 'drop table if exists browse_list_next';
  execute $q$
    create unlogged table browse_list_next as
    select * from browse_projection
    order by category_main, category_type, first_seen_at
  $q$;
  execute 'create unique index browse_list_next_pk on browse_list_next (property_id)';
  execute 'create index browse_list_next_cat_first_seen_idx on browse_list_next (category_main, category_type, first_seen_at desc, property_id desc)';
  execute 'create index browse_list_next_obec_price_idx on browse_list_next (obec_id, category_type, price_czk, property_id, category_main, subtype, disposition, area_m2, is_active) where obec_id is not null';
  execute 'create index browse_list_next_okres_price_idx on browse_list_next (okres_id, category_type, price_czk, property_id, category_main, subtype, disposition, area_m2, is_active) where okres_id is not null';
  execute 'create index browse_list_next_region_price_idx on browse_list_next (region_id, category_type, price_czk, property_id, category_main, subtype, disposition, area_m2, is_active) where region_id is not null';
  execute 'create index browse_list_next_ulice_idx on browse_list_next (ulice_id) where ulice_id is not null';
  execute 'create index browse_list_next_newest_ad_at_sreality_idx on browse_list_next (category_main, category_type, newest_ad_at_sreality desc, property_id desc) where newest_ad_at_sreality is not null';
  execute 'create index browse_list_next_newest_ad_at_bazos_idx on browse_list_next (category_main, category_type, newest_ad_at_bazos desc, property_id desc) where newest_ad_at_bazos is not null';
  execute 'create index browse_list_next_newest_ad_at_idnes_idx on browse_list_next (category_main, category_type, newest_ad_at_idnes desc, property_id desc) where newest_ad_at_idnes is not null';
  execute 'create index browse_list_next_newest_ad_at_maxima_idx on browse_list_next (category_main, category_type, newest_ad_at_maxima desc, property_id desc) where newest_ad_at_maxima is not null';
  execute 'create index browse_list_next_newest_ad_at_ceskereality_idx on browse_list_next (category_main, category_type, newest_ad_at_ceskereality desc, property_id desc) where newest_ad_at_ceskereality is not null';
  execute 'create index browse_list_next_newest_ad_at_bezrealitky_idx on browse_list_next (category_main, category_type, newest_ad_at_bezrealitky desc, property_id desc) where newest_ad_at_bezrealitky is not null';
  execute 'create index browse_list_next_newest_ad_at_mmreality_idx on browse_list_next (category_main, category_type, newest_ad_at_mmreality desc, property_id desc) where newest_ad_at_mmreality is not null';
  execute 'create index browse_list_next_newest_ad_at_remax_idx on browse_list_next (category_main, category_type, newest_ad_at_remax desc, property_id desc) where newest_ad_at_remax is not null';
  execute 'create index browse_list_next_newest_ad_at_realitymix_idx on browse_list_next (category_main, category_type, newest_ad_at_realitymix desc, property_id desc) where newest_ad_at_realitymix is not null';
  execute 'analyze browse_list_next';
  execute 'select count(*) from browse_list_next' into n;

  execute 'drop table if exists browse_list';
  execute 'alter table browse_list_next rename to browse_list';
  execute 'alter index browse_list_next_pk rename to browse_list_pk';
  execute 'alter index browse_list_next_cat_first_seen_idx rename to browse_list_cat_first_seen_idx';
  execute 'alter index browse_list_next_obec_price_idx rename to browse_list_obec_price_idx';
  execute 'alter index browse_list_next_okres_price_idx rename to browse_list_okres_price_idx';
  execute 'alter index browse_list_next_region_price_idx rename to browse_list_region_price_idx';
  execute 'alter index browse_list_next_ulice_idx rename to browse_list_ulice_idx';
  execute 'alter index browse_list_next_newest_ad_at_sreality_idx rename to browse_list_newest_ad_at_sreality_idx';
  execute 'alter index browse_list_next_newest_ad_at_bazos_idx rename to browse_list_newest_ad_at_bazos_idx';
  execute 'alter index browse_list_next_newest_ad_at_idnes_idx rename to browse_list_newest_ad_at_idnes_idx';
  execute 'alter index browse_list_next_newest_ad_at_maxima_idx rename to browse_list_newest_ad_at_maxima_idx';
  execute 'alter index browse_list_next_newest_ad_at_ceskereality_idx rename to browse_list_newest_ad_at_ceskereality_idx';
  execute 'alter index browse_list_next_newest_ad_at_bezrealitky_idx rename to browse_list_newest_ad_at_bezrealitky_idx';
  execute 'alter index browse_list_next_newest_ad_at_mmreality_idx rename to browse_list_newest_ad_at_mmreality_idx';
  execute 'alter index browse_list_next_newest_ad_at_remax_idx rename to browse_list_newest_ad_at_remax_idx';
  execute 'alter index browse_list_next_newest_ad_at_realitymix_idx rename to browse_list_newest_ad_at_realitymix_idx';
  execute 'grant select on browse_list to authenticated';
  execute 'revoke insert, update, delete, truncate on browse_list from anon, authenticated';

  if has_table_privilege('anon', 'browse_list', 'SELECT') then
    raise exception 'rebuild_browse_list: anon must never hold SELECT on browse_list -- refusing to publish this rebuild (see migration 374)';
  end if;

  update derived_artifacts
     set last_succeeded_at = now(),
         complete_through  = now(),
         last_duration_ms  = (extract(epoch from clock_timestamp() - t0) * 1000)::integer,
         last_rows         = n
   where name = 'browse_list';
  perform pg_notify('pgrst', 'reload schema');
end
$function$;

do $post$
begin
  if (select md5(prosrc) from pg_proc where oid = to_regprocedure('public.rebuild_browse_list()'))
       is distinct from 'be7b0ec2297d269f495a42fdcc474517'
     or (select string_agg(md5(p.prosrc), ',') from pg_proc p
          where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_map_cells')
       is distinct from '7406f8566504c33efc7fe4273b4f76cc' then
    raise exception '630 revert did not land: the restored bodies are not 605''s / 629''s';
  end if;
end
$post$;

notify pgrst, 'reload schema';

commit;
