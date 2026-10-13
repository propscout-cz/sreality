-- 597_portal_exclusion.sql -- negative portal filters: "properties with no ad on Sreality".
--
-- THE RULE, TWO-SIDED (rule 16, MS19 + its 2026-10-13 amendment). A property matches the portal
-- filter when (a) it has an ad on one of the INCLUDED portals, judged by the status switch exactly
-- as migration 590 wrote it, and (b) it has NO ad on any EXCLUDED portal. Which ads count for (b)
-- is the operator's per-filter choice: every ad, live or ended (the default), or only the live
-- ones (`p_excluded_active_only`). It is independent of the status switch and of a broker filter:
-- an exclusion is a statement about the property, not about the ads a broker or status selects
-- (no broker is linked to any bazos, bezrealitky or maxima ad, so "this broker's ads, excluding
-- bazos" would exclude nothing). Several excluded portals mean none of them. Both portal lists on
-- the read models are coalesced, never NULL (590), so `not (list && excluded)` drops no row by NULL.
--
-- WHAT CHANGES. Views and functions only; no table, column or row is touched.
--   1. public.portal_status_matches gains two DEFAULTED arguments (p_excluded, p_excluded_active_only)
--      and one conjunct. Still inlinable (sql, immutable, invoker, no SET, no sublink).
--   2. browse_stats_properties / browse_map_cells: 590's bodies (md5-gated) with these edits only:
--        + two trailing parameters, portal_excluded_filter and portal_excluded_active_only, passed
--          to the rule;
--        - five parameters nothing sends and their dead arms: tag_ids, with_estimates (both resolve
--          to property ids client-side), city_index_rules (resolved to obec ids since W5),
--          city_proximity (retired in 436; the bodies only raised on it), districts_context_filter
--          (never read by either body);
--        - browse_map_cells' listing_ids_filter and its arm (no caller since W5; W6's draft 593
--          section 5 existed only to drop it).
--      A parameter list cannot change under CREATE OR REPLACE, so each old signature is DROPPED by
--      its exact types and the function re-created, its ACL restated (authenticated + service_role,
--      never public or anon), all in ONE transaction: the DROPs and CREATEs commit together or not
--      at all.
--
-- DEPLOY ORDER (PostgREST matches named arguments). Removing a parameter the DEPLOYED SPA still
-- sends breaks Stats and the map cells (PGRST202) until a new bundle deploys. So:
--   (1) PR #1788 (the SPA stops sending the five dead keys) is MERGED AND DEPLOYED first;
--   (2) this file is applied (apply_migration.yml, --ref the feature branch);
--   (3) the feature PR merges; its SPA sends the two new keys.
-- Between (2) and (3) the deployed SPA sends neither new key; both are defaulted, so nothing breaks.
--
-- W6 (draft #1732, migration 593, NOT applied) md5-gates browse_map_cells on 590's body and drops
-- listing_ids_filter: after this file it refuses at apply (safe) and must be rebased; its section 5
-- becomes redundant.
--
-- ROLLBACK: forward only (rule 1). A re-run is a no-op: the preconditions accept this file's own
-- bodies, the DROPs are IF EXISTS and the CREATEs replace.

set lock_timeout = '5s';

begin;

-- ---------------------------------------------------------------------------
-- 0. Preconditions: exactly one overload of each function, and its body is 590's
--    (md5 of prosrc, = the live bodies read 2026-10-13) or this file's own (a re-run).
-- ---------------------------------------------------------------------------
do $pre$
begin
  if (select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'portal_status_matches')
     not in ('13a3a95307bb616d225d7d57dbcc3c1f', '2811fc4473f372e5f4597fd1a8a5ab0f') then
    raise exception '597 refused: portal_status_matches is not 590''s body nor this file''s (or has two overloads)';
  end if;
  if (select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_stats_properties')
     not in ('f4d95769ccc20ab9968d0135b86cac26', '2f6f764d335b251707159073381fd589') then
    raise exception '597 refused: browse_stats_properties is not 590''s body nor this file''s (or has two overloads)';
  end if;
  if (select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_map_cells')
     not in ('0a1cf3a94fce99941adca7cbe1f375b9', '61785058aef74cec02f1b95c02b24a41') then
    raise exception '597 refused: browse_map_cells is not 590''s body nor this file''s (or has two overloads)';
  end if;
end
$pre$;

-- ---------------------------------------------------------------------------
-- 1. The old signatures go first, all three in this transaction: a 7-argument rule beside the
--    5-argument one would make every 5-argument call ambiguous ("function is not unique").
-- ---------------------------------------------------------------------------
drop function if exists public.browse_stats_properties(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], bigint[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], text[], jsonb, integer, integer, jsonb, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, boolean, bigint[], boolean);
drop function if exists public.browse_map_cells(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], bigint[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], text[], jsonb, integer, integer, jsonb, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, boolean, bigint[], bigint[], integer, boolean);
drop function if exists public.portal_status_matches(boolean, text[], text[], text[], text);

-- ---------------------------------------------------------------------------
-- 2. THE portal rule, two-sided. Inlinable: sql, immutable, invoker, no SET, no sublink; under
--    the RPCs' force_custom_plan both CASEs fold to one arm per call. The two new arguments are
--    defaulted, so the broker lookup's 5-argument call (toolkit/brokers.py) resolves unchanged.
-- ---------------------------------------------------------------------------
create or replace function public.portal_status_matches(
  p_is_active boolean, p_all_sources text[], p_active_sources text[], p_portals text[], p_status text,
  p_excluded text[] default null, p_excluded_active_only boolean default false)
returns boolean
language sql
immutable
parallel safe
as $$
  select case
    when coalesce(cardinality(p_portals), 0) = 0 then
      case p_status when 'active' then p_is_active
                    when 'inactive' then not p_is_active
                    else true end
    when p_status = 'active'   then p_active_sources && p_portals
    when p_status = 'inactive' then p_all_sources && p_portals and not (p_active_sources && p_portals)
    else p_all_sources && p_portals
  end
  and not coalesce(
    case when p_excluded_active_only then p_active_sources else p_all_sources end && p_excluded, false)
$$;

revoke execute on function public.portal_status_matches(boolean, text[], text[], text[], text, text[], boolean) from public, anon;
grant execute on function public.portal_status_matches(boolean, text[], text[], text[], text, text[], boolean) to authenticated, service_role;

comment on function public.portal_status_matches(boolean, text[], text[], text[], text, text[], boolean) is
  'MS19 (590) + migration 597: THE portal rule. A property matches portal set P when any of its '
  'ads is on P; status (any / active / inactive) is judged on those ads; with no portal, on the '
  'property. It must also have no ad on any excluded portal: any ad by default, only active ads '
  'when p_excluded_active_only. Pinned to the SPA and the Watchdog by tests/fixtures/portal_rule.json.';

-- ---------------------------------------------------------------------------
-- 3. The two aggregate RPCs: 590's bodies (md5 f4d95769... / 0a1cf3a9...)
--    with exactly the edits the header lists. New md5s: 2f6f764d... / 61785058....
-- ---------------------------------------------------------------------------
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
  portal_excluded_active_only boolean default false
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

revoke execute on function public.browse_stats_properties(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], boolean, text[], boolean) from public, anon;
grant execute on function public.browse_stats_properties(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], boolean, text[], boolean) to authenticated, service_role;

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
  portal_excluded_active_only boolean default false
)
returns jsonb
language plpgsql
stable
-- Mostly-NULL parameters: a generic plan would pick one shape and use it for every
-- cohort. Same setting, same reason, as browse_stats_properties.
set plan_cache_mode to 'force_custom_plan'
as $function$
declare
  -- The Czech Republic, used only when the cohort carries no bbox of its own.
  -- Real CZ bounds are lng 12.09..18.86, lat 48.55..51.06.
  c_cz_west  constant double precision := 12.0;
  c_cz_east  constant double precision := 18.9;
  c_cz_south constant double precision := 48.5;
  c_cz_north constant double precision := 51.1;
  c_cols constant integer := 20;
  c_rows constant integer := 13;
  v_w double precision;
  v_e double precision;
  v_s double precision;
  v_n double precision;
  v_cw double precision;
  v_ch double precision;
  v_result jsonb;
  v_total bigint;
begin
  v_w := coalesce(bbox_west,  c_cz_west);
  v_e := coalesce(bbox_east,  c_cz_east);
  v_s := coalesce(bbox_south, c_cz_south);
  v_n := coalesce(bbox_north, c_cz_north);
  -- A degenerate extent (a point bbox, or an inverted one from a bad URL) would
  -- divide by zero or produce a negative cell; fall back to the CZ box rather
  -- than raise, because the cohort predicate is still perfectly well defined.
  if not (v_e > v_w and v_n > v_s) then
    v_w := c_cz_west; v_e := c_cz_east; v_s := c_cz_south; v_n := c_cz_north;
  end if;
  v_cw := (v_e - v_w) / c_cols;
  v_ch := (v_n - v_s) / c_rows;

  with g as (
    select
      -- cx/cy are NULL together for a row outside the grid extent. Those rows
      -- collapse into ONE group, are reported as off_grid, and are still inside
      -- total -- never relocated onto an edge cell, never dropped.
      case when l.lat >= v_s and l.lat <= v_n and l.lng >= v_w and l.lng <= v_e
           then least(floor((l.lng - v_w) / v_cw)::int, c_cols - 1) end as cx,
      case when l.lat >= v_s and l.lat <= v_n and l.lng >= v_w and l.lng <= v_e
           then least(floor((l.lat - v_s) / v_ch)::int, c_rows - 1) end as cy,
      count(*)::int as n,
      -- The MEAN of the points in the cell, not the cell centre: the bubble
      -- then sits on the settlement instead of on a lattice vertex. Both
      -- columns are index keys, so this costs nothing and keeps Heap Fetches 0.
      avg(l.lat) as la,
      avg(l.lng) as lo
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
  select jsonb_build_object(
    'total',        coalesce((select sum(n) from g), 0),
    'off_grid',     coalesce((select sum(n) from g where cx is null), 0),
    'cell_lat_deg', v_ch,
    'cell_lng_deg', v_cw,
    'grid_west',  v_w, 'grid_east',  v_e,
    'grid_south', v_s, 'grid_north', v_n,
    'cells', coalesce(
      (select jsonb_agg(jsonb_build_object('lat', la, 'lng', lo, 'n', n) order by n desc)
         from g where cx is not null),
      '[]'::jsonb)
  )
  into v_result;

  v_total := (v_result->>'total')::bigint;
  -- At or below the budget the caller re-reads the cohort as points, so the
  -- cells are withheld rather than shipped-and-ignored. `clustered` is what the
  -- caller branches on; it is never inferred from the array being empty (an
  -- empty cohort is not a small one).
  return v_result || jsonb_build_object(
    'clustered', v_total > point_budget,
    'cells', case when v_total > point_budget then v_result->'cells' else 'null'::jsonb end
  );
end
$function$;

revoke execute on function public.browse_map_cells(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], integer, boolean, text[], boolean) from public, anon;
grant execute on function public.browse_map_cells(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], integer, boolean, text[], boolean) to authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 4. Post-conditions: one overload each, this file's bodies, the new parameters in, the
--    dropped ones out.
-- ---------------------------------------------------------------------------
do $post$
declare
  fn text;
begin
  if to_regprocedure('public.portal_status_matches(boolean, text[], text[], text[], text, text[], boolean)') is null
     or (select string_agg(md5(p.prosrc), ',') from pg_proc p
          where p.pronamespace = 'public'::regnamespace and p.proname = 'portal_status_matches')
        is distinct from '2811fc4473f372e5f4597fd1a8a5ab0f' then
    raise exception '597 did not land: portal_status_matches';
  end if;
  if (select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_stats_properties')
     is distinct from '2f6f764d335b251707159073381fd589'
     or (select string_agg(md5(p.prosrc), ',') from pg_proc p
          where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_map_cells')
     is distinct from '61785058aef74cec02f1b95c02b24a41' then
    raise exception '597 did not land: the aggregate RPCs are not this file''s bodies';
  end if;
  foreach fn in array array['browse_stats_properties', 'browse_map_cells'] loop
    if not exists (select 1 from pg_proc p where p.pronamespace = 'public'::regnamespace
                    and p.proname = fn and 'portal_excluded_filter' = any(p.proargnames)
                    and 'portal_excluded_active_only' = any(p.proargnames)
                    and not p.proargnames && array['tag_ids', 'districts_context_filter',
                      'city_index_rules', 'city_proximity', 'with_estimates', 'listing_ids_filter']) then
      raise exception '597 did not land: % has the wrong parameters', fn;
    end if;
  end loop;
end
$post$;

notify pgrst, 'reload schema';

commit;
