-- 657_status_is_the_propertys.sql -- a row's status is its property's, on every surface.
--
-- WHY. Operator report 2026-10-21 (two screenshots): Browse on Neaktivní + Sreality drew one ACTIVE property among
-- the inactive ones, on the cards and on the map. The rule did it by design: MS19 (590, two-sided since 597) judged
-- the status switch on the selected portal's ads, so "inactive on sreality" admitted a property still live on
-- another portal (2,184 such on sreality, 3,771 on idnes, read live 2026-10-22), while every surface DRAWS the
-- property's is_active -- the card, its dates, the pin, the legend. 634 had answered the same report in Stats by
-- spreading the ads' status into the pivot (a lens, a turnover set-aside, a known broker gap). Operator ruling
-- 2026-10-22: the status is the PROPERTY's, everywhere; a portal (or a broker) asks for an ad there in that same
-- status. One status, the one the row is drawn with.
--
-- WHAT CHANGES. Functions only, same signatures (the ACLs stay; nothing is dropped).
--   portal_status_matches: two independent conjuncts instead of the four-arm CASE -- the status on the property's
--     is_active, and the portal side over active_sources under `active`, all_sources otherwise (an ended
--     property's ads have all ended). Only `inactive` + a portal changes: a property live elsewhere is out.
--     browse_map_cells reads it unchanged.
--   browse_stats_properties: 648's statement with 634's three lens edits taken back -- `filtered` drops the two
--     source arrays, `keyed` reads l.is_active, `sample` no longer sets turnover rows aside (the lens and the
--     property can no longer disagree). Everything else is 648's, byte for byte
--     (tests/test_657_status_is_the_propertys.py).
--
-- DEPLOY ORDER. Either order is safe; apply right after the deploy that carries the SPA and the broker lookup
-- (frontend/src/lib/queries.ts applyPortalRule, toolkit/brokers.py). In between, Stats and the map cells answer
-- the old rule for inactive + a portal while the list and the count answer this one (1-3 % of that cohort).
--
-- ROLLBACK: forward only (rule 1); restating 597's rule body and 648's statement restores the old answers. A re-run
-- is a no-op: the preconditions accept this file's own bodies and both CREATEs replace.

set lock_timeout = '5s';
set statement_timeout = '300s';

begin;

-- ---------------------------------------------------------------------------
-- 0. Preconditions: one overload each, and its body is the live one this file was written against
--    (597's rule, 648's pivot; pg_proc 2026-10-22) or this file's own (a re-run).
-- ---------------------------------------------------------------------------
do $pre$
begin
  if (select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'portal_status_matches')
     not in ('2811fc4473f372e5f4597fd1a8a5ab0f', '8b40b72dc192800af3752c70d67ca341') then
    raise exception '657 refused: portal_status_matches is not 597''s body nor this file''s (or has two overloads)';
  end if;
  if (select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_stats_properties')
     not in ('d7e671e5e589b972e30e7ca8a63ad8b6', 'ab2c1af34d972302689b935e8499425e') then
    raise exception '657 refused: browse_stats_properties is not 648''s body nor this file''s (or has two overloads)';
  end if;
end
$pre$;

-- ---------------------------------------------------------------------------
-- 1. THE portal rule. Inlinable: sql, immutable, invoker, no SET, no sublink; under the RPCs'
--    force_custom_plan each CASE folds to one arm per call.
-- ---------------------------------------------------------------------------
create or replace function public.portal_status_matches(
  p_is_active boolean, p_all_sources text[], p_active_sources text[], p_portals text[], p_status text,
  p_excluded text[] default null, p_excluded_active_only boolean default false)
returns boolean
language sql
immutable
parallel safe
as $$
  select case p_status when 'active' then p_is_active
                       when 'inactive' then not p_is_active
                       else true end
  and (coalesce(cardinality(p_portals), 0) = 0
       or case when p_status = 'active' then p_active_sources else p_all_sources end && p_portals)
  and not coalesce(
    case when p_excluded_active_only then p_active_sources else p_all_sources end && p_excluded, false)
$$;

comment on function public.portal_status_matches(boolean, text[], text[], text[], text, text[], boolean) is
  'THE portal rule (MS19, 590; two-sided since 597; status since 657). The status (any / active / inactive) '
  'is the property''s own. A portal set P asks for an ad on P in that same status: an active ad under active, '
  'any ad otherwise (an ended property has only ended ads). And no ad on an excluded portal: any ad by default, '
  'only active ads when p_excluded_active_only. Pinned to the SPA and the Watchdog by tests/fixtures/portal_rule.json.';

-- ---------------------------------------------------------------------------
-- 2. The Stats pivot: 648's statement without 634's lens.
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
  portal_excluded_active_only boolean default false,
  tom_observed_only boolean default false,
  -- migration 626: the chart spec (see the header); null = refused as bad_spec.
  spec jsonb default null
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
  pipeline_out bigint[] default null::bigint[],
  energy_rating_filter text[] default null::text[]
)
returns jsonb
language plpgsql
stable
set plan_cache_mode to 'force_custom_plan'
as $function$
declare
  v_min_n     constant int := 5;
  v_max_cells constant int := 2000;
  v_rows text[] := coalesce((select array_agg(x order by ord)
                               from jsonb_array_elements_text(case when jsonb_typeof(spec->'rows') = 'array' then spec->'rows' else '[]'::jsonb end)
                                    with ordinality as t(x, ord)), '{}'::text[]);
  v_cols text[] := coalesce((select array_agg(x order by ord)
                               from jsonb_array_elements_text(case when jsonb_typeof(spec->'cols') = 'array' then spec->'cols' else '[]'::jsonb end)
                                    with ordinality as t(x, ord)), '{}'::text[]);
  v_measure text := spec->>'measure';
  v_bad text;
  v_masks int[];
  r1 text; r2 text; r3 text; r4 text; r5 text; r6 text; c1 text;
begin
  -- The spec contract (toolkit.stats_registry.spec_guard_sql: validate_spec's rules): refuse, never guess.
  if spec is null then v_bad := 'spec missing';
  elsif jsonb_typeof(spec) <> 'object' then v_bad := 'spec must be an object';
  elsif (spec ? 'rows' and jsonb_typeof(spec->'rows') <> 'array')
     or (spec ? 'cols' and jsonb_typeof(spec->'cols') <> 'array') then v_bad := 'rows and cols must be arrays';
  elsif coalesce(array_length(v_rows, 1), 0) > 6 then v_bad := 'at most 6 row dimensions';
  elsif coalesce(array_length(v_cols, 1), 0) > 1 then v_bad := 'at most 1 column dimension';
  elsif v_measure is null or v_measure not in ('tom_days', 'area', 'price', 'ppm2') then v_bad := 'unknown measure';
  elsif exists (select 1 from unnest(v_rows || v_cols) d where d not in ('disposition', 'area_band_5', 'area_band_10', 'condition', 'ppm2_band_s', 'ppm2_band_l', 'price_band_s', 'price_band_m', 'price_band_l', 'kraj', 'okres', 'obec', 'cast_obce', 'ulice', 'building_type', 'furnished', 'has_balcony', 'has_lift', 'has_parking', 'terrace', 'cellar', 'garage')) then v_bad := 'unknown dimension';
  elsif (select count(*) from unnest(v_rows || v_cols) d) <> (select count(distinct d) from unnest(v_rows || v_cols) d) then v_bad := 'a dimension may appear once';
  end if;
  if v_bad is not null then
    return jsonb_build_object('refused', 'bad_spec', 'reason', v_bad, 'cells', '[]'::jsonb);
  end if;
  r1 := v_rows[1]; r2 := v_rows[2]; r3 := v_rows[3]; r4 := v_rows[4]; r5 := v_rows[5]; r6 := v_rows[6]; c1 := v_cols[1];
  -- grouping(k1..k6) of the levels this spec uses: level L keeps k1..kL, so its mask is 2^(6-L) - 1.
  v_masks := (select array_agg((2 ^ (6 - lvl))::int - 1) from generate_series(0, coalesce(array_length(v_rows, 1), 0)) lvl);
  return (
  with filtered as (
    select l.listing_id, l.is_active, l.category_main, l.category_type, l.price_per_m2_basis, l.disposition, l.condition, l.area_m2, l.price_czk, l.price_per_m2, l.tom_days, l.building_type, l.furnished, l.has_balcony, l.has_lift, l.has_parking, l.terrace, l.cellar, l.garage, l.region_id, l.okres_id, l.obec_id, l.cast_obce_id, l.ulice_id
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
      and (energy_rating_filter   is null or array_length(energy_rating_filter, 1) is null or l.energy_rating = any(energy_rating_filter))
      and (
        condition_match_filter is null or array_length(condition_match_filter, 1) is null
        or l.condition = any(condition_match_filter)
        or ('__unknown__' = any(condition_match_filter)
            and (l.condition is null or not (l.condition = any(array['projekt','ve_vystavbe','novostavba','po_rekonstrukci','velmi_dobry','dobry','udrzovany','v_rekonstrukci','pred_rekonstrukci','spatny','k_demolici']))))
      )
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
  ),
  keyed as (
    select l.is_active, l.category_main, l.category_type, l.price_per_m2_basis,
      public.stats_dim_key(r1, l.category_main, l.price_per_m2_basis, l.disposition, l.condition, l.area_m2, l.price_czk, l.price_per_m2, l.building_type, l.furnished, l.has_balcony, l.has_lift, l.has_parking, l.terrace, l.cellar, l.garage, l.region_id, l.okres_id, l.obec_id, l.cast_obce_id, l.ulice_id) as k1,
      public.stats_dim_key(r2, l.category_main, l.price_per_m2_basis, l.disposition, l.condition, l.area_m2, l.price_czk, l.price_per_m2, l.building_type, l.furnished, l.has_balcony, l.has_lift, l.has_parking, l.terrace, l.cellar, l.garage, l.region_id, l.okres_id, l.obec_id, l.cast_obce_id, l.ulice_id) as k2,
      public.stats_dim_key(r3, l.category_main, l.price_per_m2_basis, l.disposition, l.condition, l.area_m2, l.price_czk, l.price_per_m2, l.building_type, l.furnished, l.has_balcony, l.has_lift, l.has_parking, l.terrace, l.cellar, l.garage, l.region_id, l.okres_id, l.obec_id, l.cast_obce_id, l.ulice_id) as k3,
      public.stats_dim_key(r4, l.category_main, l.price_per_m2_basis, l.disposition, l.condition, l.area_m2, l.price_czk, l.price_per_m2, l.building_type, l.furnished, l.has_balcony, l.has_lift, l.has_parking, l.terrace, l.cellar, l.garage, l.region_id, l.okres_id, l.obec_id, l.cast_obce_id, l.ulice_id) as k4,
      public.stats_dim_key(r5, l.category_main, l.price_per_m2_basis, l.disposition, l.condition, l.area_m2, l.price_czk, l.price_per_m2, l.building_type, l.furnished, l.has_balcony, l.has_lift, l.has_parking, l.terrace, l.cellar, l.garage, l.region_id, l.okres_id, l.obec_id, l.cast_obce_id, l.ulice_id) as k5,
      public.stats_dim_key(r6, l.category_main, l.price_per_m2_basis, l.disposition, l.condition, l.area_m2, l.price_czk, l.price_per_m2, l.building_type, l.furnished, l.has_balcony, l.has_lift, l.has_parking, l.terrace, l.cellar, l.garage, l.region_id, l.okres_id, l.obec_id, l.cast_obce_id, l.ulice_id) as k6,
      public.stats_dim_key(c1, l.category_main, l.price_per_m2_basis, l.disposition, l.condition, l.area_m2, l.price_czk, l.price_per_m2, l.building_type, l.furnished, l.has_balcony, l.has_lift, l.has_parking, l.terrace, l.cellar, l.garage, l.region_id, l.okres_id, l.obec_id, l.cast_obce_id, l.ulice_id) as kc,
      case v_measure when 'tom_days' then l.tom_days::numeric when 'area' then l.area_m2::numeric when 'price' then l.price_czk::numeric when 'ppm2' then l.price_per_m2 end as v
    from filtered l
  ),
  cohort as (
    select count(*)::int as filtered_n,
           min(category_main) as cat_lo, max(category_main) as cat_hi,
           min(category_type) as type_lo, max(category_type) as type_hi,
           bool_and(is_active) as all_active, bool_or(is_active) as any_active,
           min(price_per_m2_basis) as basis_lo, max(price_per_m2_basis) as basis_hi
    from keyed
  ),
  -- The sample: every property that has the measure, a category and a value on EVERY chosen dimension (ruling 8).
  sample as (
    select * from keyed
    where v is not null and category_main is not null and category_type is not null
      and (r1 is null or k1 is not null) and (r2 is null or k2 is not null) and (r3 is null or k3 is not null) and (r4 is null or k4 is not null) and (r5 is null or k5 is not null) and (r6 is null or k6 is not null) and (c1 is null or kc is not null)
  ),
  cells as (
    select kc, k1, k2, k3, k4, k5, k6, grouping(k1, k2, k3, k4, k5, k6) as g,
           count(*)::int as n, avg(v) as mean,
           percentile_cont(array[0.10, 0.25, 0.50, 0.75, 0.90]) within group (order by v) as q
    from sample
    group by kc, rollup(k1, k2, k3, k4, k5, k6)
  ),
  used as (select * from cells where g = any(v_masks)),
  verdict as (
    select c.*, (select count(*)::int from sample) as sample_n, (select count(*)::int from used) as n_cells,
           case
             when c.filtered_n = 0 then 'empty'
             when c.cat_lo <> c.cat_hi or c.type_lo <> c.type_hi then 'mixed_category'
             -- ruling 3: ONE status in the SAMPLE (the cohort's `status` fact above may still be mixed).
             when v_measure in ('tom_days') and (select bool_or(is_active) and not bool_and(is_active) from sample) then 'mixed_status'
             when (select count(*) from used) > v_max_cells then 'too_many_cells'
           end as refused
    from cohort c
  )
  select jsonb_build_object(
    'spec', jsonb_build_object('rows', to_jsonb(v_rows), 'cols', to_jsonb(v_cols), 'measure', v_measure),
    'filtered', x.filtered_n,
    'sample', x.sample_n,
    'excluded', x.filtered_n - x.sample_n,
    'category_main', case when x.cat_lo = x.cat_hi then x.cat_lo end,
    'category_type', case when x.type_lo = x.type_hi then x.type_lo end,
    'status', case when x.filtered_n = 0 then null when x.all_active then 'active' when not x.any_active then 'inactive' else 'mixed' end,
    'basis', case when x.basis_lo = x.basis_hi then x.basis_lo end,
    'min_n', v_min_n,
    'refused', x.refused,
    -- Every place key among the result cells, resolved to its register name (header, WHAT CHANGES 4).
    'places', case when x.refused is null then coalesce((
      select jsonb_agg(jsonb_build_object('dim', p.dim, 'key', p.key, 'name', n.name) order by p.dim, p.key)
      from (
        select distinct d.dim, d.key
          from used u
          cross join lateral (values (r1, u.k1), (r2, u.k2), (r3, u.k3), (r4, u.k4), (r5, u.k5), (r6, u.k6), (c1, u.kc)) d(dim, key)
         where d.key is not null and d.dim in ('kraj', 'okres', 'obec', 'cast_obce', 'ulice')
      ) p
      left join public.ruian_place_names_public n on n.level = p.dim and n.code = p.key::bigint), '[]'::jsonb) else '[]'::jsonb end,
    'cells', case when x.refused is null then coalesce((
      select jsonb_agg(jsonb_build_object(
        'keys', to_jsonb(array_remove(array[u.k1, u.k2, u.k3, u.k4, u.k5, u.k6], null)),
        'col', u.kc,
        'n', u.n,
        'mean',   case when u.n >= v_min_n then round(u.mean::numeric, 2) end,
        'median', case when u.n >= v_min_n then round(u.q[3]::numeric, 2) end,
        'p10',    case when u.n >= v_min_n then round(u.q[1]::numeric, 2) end,
        'p25',    case when u.n >= v_min_n then round(u.q[2]::numeric, 2) end,
        'p75',    case when u.n >= v_min_n then round(u.q[4]::numeric, 2) end,
        'p90',    case when u.n >= v_min_n then round(u.q[5]::numeric, 2) end
      ) order by u.g desc, u.kc nulls first, u.k1, u.k2, u.k3, u.k4, u.k5, u.k6)
      from used u), '[]'::jsonb) else '[]'::jsonb end
  )
  from verdict x
  );
end
$function$;

-- ---------------------------------------------------------------------------
-- 3. Post-conditions: one overload each, this file's bodies, invoker, the ACLs unchanged.
-- ---------------------------------------------------------------------------
do $post$
declare
  fn text;
  fid oid;
begin
  if (select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'portal_status_matches')
     is distinct from '8b40b72dc192800af3752c70d67ca341' then
    raise exception '657 did not land: portal_status_matches is not this file''s body';
  end if;
  if (select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_stats_properties')
     is distinct from 'ab2c1af34d972302689b935e8499425e' then
    raise exception '657 did not land: browse_stats_properties is not this file''s body';
  end if;
  foreach fn in array array['portal_status_matches', 'browse_stats_properties'] loop
    select p.oid into fid from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname = fn;
    if (select p.prosecdef from pg_proc p where p.oid = fid) then
      raise exception '657 did not land: % must stay security invoker', fn;
    end if;
    if has_function_privilege('anon', fid, 'execute')
       or not has_function_privilege('authenticated', fid, 'execute')
       or not has_function_privilege('service_role', fid, 'execute') then
      raise exception '657 did not land: % is not executable by exactly authenticated + service_role', fn;
    end if;
  end loop;
end
$post$;

commit;
