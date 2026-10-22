-- 626_stats_dashboard.sql -- Browse > Stats becomes a dashboard: one pivot, one key, one saved board.
--
-- WHY. Operator ruling 2026-10-19 (docs/design/stats-dashboard.md). The Stats tab's four fixed cards are
-- replaced by charts the user composes: dimensions on rows (nested, six levels) and one column, a measure in
-- the values, drawn as a table, a line, a bar or a horizontal box. The 2026-10-18 audit (235 agents, 70
-- verified findings) found the fixed cards' box axes squashed by raw extremes, their turnover boxes pooling
-- live ages with finished durations, two unit bugs and two hand-copied chart implementations; they are
-- deleted, not patched. The sample is always the Browse cohort at PROPERTY grain, and every chart says how
-- much of it it stands on (filtered / sample / excluded for missing data).
--
-- WHAT CHANGES.
--   1. public.stats_dashboards: one row per account, the ordered list of saved charts (jsonb). RLS reads for
--      the account; writes go through the API (PUT /stats/dashboard, tenant connection, same grants as
--      filter_presets: the tenant role IS `authenticated`).
--   2. public.stats_dim_key(dim, ...21 scalars): the ONE dimension vocabulary in SQL, rendered from
--      toolkit/stats_registry.py (tests/test_626_stats_dashboard.py pins the text). SQL-language IMMUTABLE so
--      the seven calls per row inline. A key is the raw canonical value: an enum member, ano/ne, a RUIAN code,
--      a band's lower bound as text. NULL = the property has no value on that dimension.
--   3. browse_stats_properties re-signed: 625's seventy-four cohort parameters verbatim (the WHERE is 625's,
--      byte for byte) plus `spec jsonb default null` = {rows: [dim...], cols: [dim], measure}. The body is new:
--      one rollup pass (six fixed row slots, one column slot) over the sample gives every nesting level's
--      cells in one sort -- measured live 2026-10-19 (read-only prototypes): five dimensions plus a column over
--      60k active byt/prodej properties 381 ms; two dimensions over 139k byt/prodej rows (any status) 493 ms;
--      the old four-card body took 1.1-5.0 s. A cell with fewer than ten properties ships n and no statistics
--      (ruling 6); statistics are mean, median, p10, p25, p75, p90, rounded to two places. Refusals, never
--      guesses: `empty`, `mixed_category` (more than one category_main or category_type in the cohort),
--      `mixed_status` (a turnover measure over live and delisted rows together in the SAMPLE, ruling 3;
--      the cohort's `status` fact may still read mixed), `too_many_cells` (above 2,000), `bad_spec`. The
--      signature changes, so the old one is DROPPED and the ACL restated in the same transaction (database
--      skill: re-signing the Browse RPCs). browse_map_cells is not touched.
--   4. public.ruian_place_names_public(level, code, name): the open RUIAN register's names for the five
--      levels a place dimension keys on (kraj, okres, obec, cast_obce from ruian_admin_units; ulice from
--      ruian_streets), one row per (level, code), granted SELECT to authenticated. A place cell keys on the
--      CODE browse_list already carries (region_id .. ulice_id; rule 25's one code predicate), so two
--      towns or streets that share a name stay two cells, and the pivot reads no listing location at all;
--      the payload's `places` list resolves each key to its name through this view on the <= 2,000 result
--      cells. WHY A VIEW: the pivot is SECURITY INVOKER (the SPA calls it as `authenticated`, and
--      hide_dismissed reads the caller's own RLS'd dismissals) and the register mirror (381) is revoked from
--      browser roles, so a direct join would fail with "permission denied". Rule 25 clause: this is a
--      location read surface added by a PR that deletes none, so the PR body says why (the operator asked
--      for kraj / okres / mesto / ctvrt / ulice as chart dimensions, 2026-10-19) and it stands on the
--      operator's ruling. It exposes the public register only -- no listing, no address.
--
-- APPLY through apply_migration.yml (--ref the feature branch) IMMEDIATELY BEFORE merging the SPA that
-- sends `spec`; the old SPA reads a payload without its keys for the 3-5 min of the Railway rollout (dashes,
-- no wrong number). No worker restart: no merge window needed for the apply itself.
-- ROLLBACK: forward only (rule 1). A re-run is a no-op: the precondition accepts this file's own body.

set lock_timeout = '5s';
set statement_timeout = '300s';

-- ---------------------------------------------------------------------------
-- 0. Precondition: browse_stats_properties is 625's live body (md5 read live 2026-10-19) or this file's own.
--    Skipped in the CI schema-replay container (522 §5's probe: corpus < 100k properties).
-- ---------------------------------------------------------------------------
do $pre$
declare
  populated bool;
begin
  select count(*) = 100000 into populated
    from (select 1 from public.properties limit 100000) probe;
  if not populated then
    raise notice '626: replay container (corpus < 100k properties), precondition skipped';
    return;
  end if;
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_stats_properties'), '')
     not in ('6b102dd7960cced4ffbbf31127048428', 'a69d5fb1b9f1ae2321d8952683919757') then
    raise exception '626 refused: browse_stats_properties is not 625''s body nor this file''s (or has two overloads)';
  end if;
end
$pre$;

-- ---------------------------------------------------------------------------
-- 1. The saved board. One row per account; the API validates every chart against the registry.
-- ---------------------------------------------------------------------------
create table if not exists public.stats_dashboards (
  account_id uuid primary key references public.accounts(id) on delete cascade,
  charts     jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now()
);
comment on table public.stats_dashboards is
  'Browse > Stats: the account''s ordered list of saved charts ({id, view, rows, cols, measure, stat}), '
  'validated by toolkit.stats_registry.validate_chart through PUT /stats/dashboard. One row per account.';
alter table public.stats_dashboards enable row level security;
revoke all on public.stats_dashboards from public, anon, authenticated;
grant select, insert, update, delete on public.stats_dashboards to authenticated;
drop policy if exists stats_dashboards_tenant_rw on public.stats_dashboards;
create policy stats_dashboards_tenant_rw on public.stats_dashboards
  for all to authenticated
  using (account_id in (select current_account_ids()))
  with check (account_id in (select current_account_ids()));

-- ---------------------------------------------------------------------------
-- 2. The dimension key and the pivot, one transaction.
-- ---------------------------------------------------------------------------
begin;

create or replace view public.ruian_place_names_public as
  select * from (
    select distinct on (u.level, u.code) u.level::text as level, u.code, u.name
      from public.ruian_admin_units u
     where u.level in ('kraj', 'okres', 'obec', 'cast_obce') and u.retired_at is null
     order by u.level, u.code, u.last_version_id desc nulls last
  ) a
  union all
  select * from (
    select distinct on (s.code) 'ulice'::text as level, s.code, s.name
      from public.ruian_streets s
     order by s.code, s.last_version_id desc nulls last
  ) b;
comment on view public.ruian_place_names_public is
  'The RUIAN register''s name for a (level, code) a place dimension keys on: kraj, okres, obec, cast_obce, ulice '
  '(migration 626). Owner rights on purpose: the register mirror (381) is dark to browser roles and the pivot '
  'runs as its caller. Public register data only.';
revoke all on public.ruian_place_names_public from public, anon, authenticated;
grant select on public.ruian_place_names_public to authenticated, service_role;

create or replace function public.stats_dim_key(
  dim text,
  category_main text,
  basis text,
  disposition text,
  condition text,
  area_m2 numeric,
  price_czk integer,
  price_per_m2 numeric,
  building_type text,
  furnished text,
  has_balcony boolean,
  has_lift boolean,
  has_parking boolean,
  terrace boolean,
  cellar boolean,
  garage boolean,
  region_id bigint,
  okres_id bigint,
  obec_id bigint,
  cast_obce_id bigint,
  ulice_id bigint
)
returns text
language sql
immutable
parallel safe
as $function$
  select case dim
    when 'disposition' then disposition
    when 'area_band_5' then case when category_main = 'byt' then case when area_m2 is null then null else (floor(area_m2 / 5) * 5)::text end end
    when 'area_band_10' then case when category_main = 'byt' then case when area_m2 is null then null else (floor(area_m2 / 10) * 10)::text end end
    when 'condition' then condition
    when 'ppm2_band_s' then case when price_per_m2 is null then null else (floor(price_per_m2 / (case when basis = 'rent_monthly_czk_m2' then 25 else 5000 end)) * (case when basis = 'rent_monthly_czk_m2' then 25 else 5000 end))::text end
    when 'ppm2_band_l' then case when price_per_m2 is null then null else (floor(price_per_m2 / (case when basis = 'rent_monthly_czk_m2' then 50 else 10000 end)) * (case when basis = 'rent_monthly_czk_m2' then 50 else 10000 end))::text end
    when 'price_band_s' then case when price_czk is null then null else (floor(price_czk / (case when basis = 'rent_monthly_czk_m2' then 1000 else 250000 end)) * (case when basis = 'rent_monthly_czk_m2' then 1000 else 250000 end))::text end
    when 'price_band_m' then case when price_czk is null then null else (floor(price_czk / (case when basis = 'rent_monthly_czk_m2' then 2500 else 500000 end)) * (case when basis = 'rent_monthly_czk_m2' then 2500 else 500000 end))::text end
    when 'price_band_l' then case when price_czk is null then null else (floor(price_czk / (case when basis = 'rent_monthly_czk_m2' then 5000 else 1000000 end)) * (case when basis = 'rent_monthly_czk_m2' then 5000 else 1000000 end))::text end
    when 'kraj' then region_id::text
    when 'okres' then okres_id::text
    when 'obec' then obec_id::text
    when 'cast_obce' then cast_obce_id::text
    when 'ulice' then ulice_id::text
    when 'building_type' then building_type
    when 'furnished' then furnished
    when 'has_balcony' then case has_balcony when true then 'ano' when false then 'ne' end
    when 'has_lift' then case has_lift when true then 'ano' when false then 'ne' end
    when 'has_parking' then case has_parking when true then 'ano' when false then 'ne' end
    when 'terrace' then case terrace when true then 'ano' when false then 'ne' end
    when 'cellar' then case cellar when true then 'ano' when false then 'ne' end
    when 'garage' then case garage when true then 'ano' when false then 'ne' end
  end
$function$;
revoke all on function public.stats_dim_key(text, text, text, text, text, numeric, integer, numeric, text, text, boolean, boolean, boolean, boolean, boolean, boolean, bigint, bigint, bigint, bigint, bigint) from public, anon, authenticated;
grant execute on function public.stats_dim_key(text, text, text, text, text, numeric, integer, numeric, text, text, boolean, boolean, boolean, boolean, boolean, boolean, bigint, bigint, bigint, bigint, bigint) to authenticated, service_role;

drop function if exists public.browse_stats_properties(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], boolean, text[], boolean, bigint[], boolean);

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
  tom_observed_only boolean default false,
  -- migration 626: the chart spec (see the header); null = refused as bad_spec.
  spec jsonb default null
)
returns jsonb
language plpgsql
stable
set plan_cache_mode to 'force_custom_plan'
as $function$
declare
  v_min_n     constant int := 10;
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

revoke execute on function public.browse_stats_properties(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], boolean, text[], boolean, bigint[], boolean, jsonb) from public, anon;
grant execute on function public.browse_stats_properties(text[], text[], integer, integer, integer, integer, boolean, integer, integer, integer, integer, integer, integer, boolean, boolean, boolean, boolean, text[], boolean, boolean, boolean, integer, text[], text[], text, double precision, double precision, double precision, double precision, text[], double precision, double precision, double precision, double precision, integer, double precision, double precision, text[], integer, integer, double precision, double precision, text[], double precision, double precision, integer, integer, double precision, double precision, double precision, double precision, double precision, double precision, boolean[], text[], integer, integer, bigint[], text[], bigint[], integer, integer, integer, integer, integer, integer, double precision, boolean, bigint[], boolean, text[], boolean, bigint[], boolean, jsonb) to authenticated, service_role;

commit;

-- ---------------------------------------------------------------------------
-- 3. Post-conditions (they hold on the empty replay schema too).
-- ---------------------------------------------------------------------------
do $post$
declare
  n int;
  src text;
begin
  select count(*), min(p.prosrc) into n, src from pg_proc p
   where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_stats_properties';
  if n <> 1 or position('rollup(k1, k2, k3, k4, k5, k6)' in src) = 0 or position('tom_observed_only' in src) = 0 then
    raise exception '626 did not land: browse_stats_properties (% overloads) lacks the pivot or lost 625''s cohort', n;
  end if;
  if (select pronargs from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_stats_properties') <> 75 then
    raise exception '626 did not land: browse_stats_properties does not take 625''s 74 parameters plus spec';
  end if;
  if not has_table_privilege('authenticated', 'public.ruian_place_names_public', 'select')
     or has_table_privilege('anon', 'public.ruian_place_names_public', 'select') then
    raise exception '626 did not land: ruian_place_names_public is not readable by authenticated alone';
  end if;
  if not has_function_privilege('authenticated', 'public.stats_dim_key(text, text, text, text, text, numeric, integer, numeric, text, text, boolean, boolean, boolean, boolean, boolean, boolean, bigint, bigint, bigint, bigint, bigint)', 'execute') then
    raise exception '626 did not land: authenticated cannot execute stats_dim_key';
  end if;
  if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'stats_dashboards'
                   and policyname = 'stats_dashboards_tenant_rw') then
    raise exception '626 did not land: stats_dashboards has no tenant policy';
  end if;
end
$post$;

select pg_notify('pgrst', 'reload schema');

reset statement_timeout;
reset lock_timeout;
