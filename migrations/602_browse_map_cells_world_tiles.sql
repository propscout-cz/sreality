-- 602 — the Browse map counts by WORLD TILE (docs/design/map-counts.md).
--
-- WHAT. browse_map_cells keeps its 74-parameter signature (598's, verbatim: the SPA builds
-- ONE argument object for it and browse_stats_properties) and its cohort predicate (598's,
-- byte for byte; tests/test_602_browse_map_cells_world_tiles_migration.py). Only the
-- aggregate changes. 439's grid was 20 x 13 cells laid over the caller's bbox: the cell size
-- depended on the window (439's "64-px cell rule" held on a 1280 px map only), the number a
-- place showed changed under a pan (Náměstí Míru 922 -> 1 262 after 100 px, measured
-- 2026-10-15), and the SPA painted every cell with the cluster bubble ramp, so a dense
-- cohort tiled the map with discs. Now a count belongs to a WORLD TILE: the fixed square
-- grid every web map uses, level L cutting the world into 2^L x 2^L squares (MapLibre's
-- 512 px world, so a level-L tile is 512 * 2^(zoom - L) px). The same formula lives in
-- frontend/src/lib/mapCounts.ts; tests/fixtures/map_tile_lattice.json pins both to one truth.
--
-- HOW. One index-only pass over properties_map_mv_cover (category_main, category_type, lat,
-- lng): the g CTE projects nothing but l.lat and l.lng (tests/test_browse_map_cells_plan.py),
-- grouped into level-18 tiles (~100 m at lat 50). Within point_budget the caller re-reads the
-- cohort as points and the cells are withheld (strict threshold; an empty cohort is not a
-- small one). Otherwise the level-18 groups, held in three arrays, are coarsened level by
-- level (x >> k, y >> k) until at most 1 024 tiles are occupied: bounded by construction, no
-- LIMIT, no sampling, and never a second pass over the base rows (a count(distinct) fan-out
-- over every level measured 2 to 4 s with 23 to 47 MB of temp; this cascade stays in memory).
-- The answer is {total, clustered, level, cells: [{lat, lng, n}]}, lat/lng the tile CENTRE
-- rounded to 5 decimals (~1 m), so the SPA regroups cells at its own level by the same formula
-- and draws one mark per tile. off_grid, the CZ box, the edge clamps and the six grid keys are
-- gone: every row is in exactly one tile (latitude clamped to +-85.05112878 inside the
-- expression, so a bad coordinate cannot raise). 439's "why not PostGIS" was beside the
-- point: arithmetic on the two index columns is what keeps the scan index-only, and world
-- tiles are exactly that arithmetic.
--
-- GATE. Refuses unless the live body is 598's (md5 of prosrc, read live 2026-10-15) or this
-- file's own (a re-run). CREATE OR REPLACE keeps the ACL; nothing is dropped.
--
-- RECEIPT (after apply_migration.yml, SELECT-only):
--   select md5(p.prosrc) from pg_proc p where p.proname = 'browse_map_cells';  -- e4af960109d1ede2f942f447baf259f6
--   select r->>'level', jsonb_array_length(r->'cells'), (r->>'total')::int,
--          (select sum((c->>'n')::int) from jsonb_array_elements(r->'cells') c)
--   from public.browse_map_cells(category_main_filter => array['byt'],
--          category_type_filter => 'pronajem', point_budget => 2000) r;
--   -- cells <= 1024, sum = total. EXPLAIN (ANALYZE, BUFFERS) of the g CTE: Index Only Scan,
--   -- Heap Fetches 0 (outside the :07-:10 / :37-:40 rebuild minutes), temp written 0.

set lock_timeout = '5s';

begin;

-- ---------------------------------------------------------------------------
-- 0. Precondition: exactly one overload, and its body is 598's or this file's own.
-- ---------------------------------------------------------------------------
do $pre$
begin
  if (select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_map_cells')
     not in ('8bf9e3adea58d433a72881386ef25df1', 'e4af960109d1ede2f942f447baf259f6') then
    raise exception '602 refused: browse_map_cells is not 598''s body nor this file''s (or has two overloads)';
  end if;
end
$pre$;

-- ---------------------------------------------------------------------------
-- 1. The same signature, a new body. CREATE OR REPLACE keeps the ACL.
-- ---------------------------------------------------------------------------
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
  property_ids_excluded bigint[] default null::bigint[]
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
  v_occupied bigint;
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
              -- a street / POI / address pick carries its containing obec code
              when lvl = 'locality'                  then l.obec_id      = admin_id
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
              -- a street / POI / address pick carries its containing obec code
              when lvl = 'locality'                  then l.obec_id      = admin_id
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

  -- Coarsen level by level from the level-18 groups (x >> k, y >> k), never re-reading the
  -- base rows, until at most c_max_cells tiles are occupied. Level 0 is one tile, so this
  -- always terminates bounded.
  loop
    select count(*) into v_occupied
    from (select 1 from unnest(v_x, v_y) as r(x, y)
          group by r.x >> (18 - v_level), r.y >> (18 - v_level)) t;
    exit when v_occupied <= c_max_cells or v_level = 0;
    v_level := v_level - 1;
  end loop;

  -- The tile CENTRE (5 decimals, ~1 m): the SPA regroups cells at its own level with the
  -- same formula, so a centre must fall inside its own tile and its parents, which it does.
  select jsonb_agg(jsonb_build_object(
           'lat', round(degrees(atan(sinh(pi() * (1 - 2 * (t.ty + 0.5) / 2 ^ v_level))))::numeric, 5),
           'lng', round(((t.tx + 0.5) / 2 ^ v_level * 360 - 180)::numeric, 5),
           'n', t.n) order by t.n desc)
    into v_cells
  from (
    select r.x >> (18 - v_level) as tx, r.y >> (18 - v_level) as ty, sum(r.n)::int as n
    from unnest(v_x, v_y, v_n) as r(x, y, n)
    group by 1, 2
  ) t;

  return jsonb_build_object('total', v_total, 'clustered', true, 'level', v_level,
                            'cells', coalesce(v_cells, '[]'::jsonb));
end
$function$;

-- ---------------------------------------------------------------------------
-- 2. Post-condition: one overload, this file's body, 598's signature.
-- ---------------------------------------------------------------------------
do $post$
begin
  if (select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_map_cells')
     is distinct from 'e4af960109d1ede2f942f447baf259f6' then
    raise exception '602 post-condition failed: browse_map_cells is not this file''s body (or has two overloads)';
  end if;
  if not exists (select 1 from pg_proc p
                 where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_map_cells'
                   and p.pronargs = 74 and p.proargnames[p.pronargs::int] = 'property_ids_excluded') then
    raise exception '602 post-condition failed: the signature is not 598''s';
  end if;
end
$post$;

notify pgrst, 'reload schema';

commit;
