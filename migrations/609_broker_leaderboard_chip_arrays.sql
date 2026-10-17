-- 609_broker_leaderboard_chip_arrays.sql -- the broker leaderboard takes Browse's location chips.
--
-- WHY. Operator ruling 2026-10-16 ("what you pick is what you filter"): a location chip is ONE
-- RUIAN unit at the level picked -- kraj, okres, obec, cast_obce, ulice -- tested as plain code
-- equality everywhere (605). The leaderboard knew three id lists, kraj / okres / obec, and no
-- exclude, so Brokers / Outreach dropped a quarter, a street or an excluded place: until this file
-- those pages had to refuse such a selection (fail closed) rather than show the country.
--
-- WHAT CHANGES. Two functions and the stored Outreach targets; no table, column or index.
--   0. district_level_codes(levels, ids, excluded, level, want_excluded): a level's codes from
--      the four chip arrays, include or exclude -- the one array builder both branches use.
--   1. broker_leaderboard: p_region_ids / p_okres_ids / p_obec_ids are REPLACED by the four parallel
--      chip arrays browse_stats_properties and browse_map_cells take -- districts_filter (the names,
--      which drive the unnest), districts_excluded_filter, districts_levels, districts_ids -- then
--      580's nine parameters unchanged. Body = 580's except:
--      (a) the LIVE branch's place predicate is the SQL twin of api/location_filter.district_where
--          over listing_location's five codes (kraj_kod ... ulice_kod): per level `code = any(that
--          level's include codes)`, and an exclude as NOT COALESCE(..., false), so a row whose code
--          is NULL survives it (tests/test_broker_leaderboard_chip_parity.py runs it over the shared
--          chip table);
--      (b) the FAST branch (the count book) answers only when every chip is an include at kraj /
--          okres / obec, one level per name -- the cells it has -- and derives 580's three id lists
--          from those chips (district_level_codes), so its cell arms and nested-chip suppression
--          read as before. Any other
--          chip set (a cast_obce, a street, an exclude) reads `listings` live. The two gates stay
--          exact complements (tests/test_broker_leaderboard_contract.py pins both strings).
--      CREATE OR REPLACE cannot change parameter types, so the old signature is DROPPED and 580's
--      three revokes restated in the same transaction: service-role only (299, Amendment A6).
--   2. outreach_campaigns.target: stored {region_ids, okres_ids, obec_ids} become chips in
--      target.districts ({name: the register unit's name, context: null, excluded: false, level, id});
--      api/outreach.select_targets reads nothing else. 0 campaigns live on 2026-10-17.
--
-- MEASURED (EXPLAIN ANALYZE on production, 2026-10-17, read-only). "first draft" = 605's per-row
-- unnest CASE blocks in the live branch; "this file" = the district_level_codes() form, measured
-- as the constants its calls fold to (the helper exists only once this file applies). The fold
-- itself, shown on an existing IMMUTABLE SQL function with a FROM body called through a pulled-up
-- subquery with bound parameters (PREPARE / EXPLAIN EXECUTE): Filter `kraj_name = 'Geometry'`;
-- under plan_cache_mode = force_generic_plan the call stays in the per-row filter instead.
--   path                                 580 shape         first draft          this file
--   kraj + min price 5M, byt/prodej      189 ms / 172k     1,187 ms / 1.30M     194 ms / 172k
--   one street, no category ("Vse")      --                2,508 ms / 821k      119 ms / 42.6k
--   one street, byt/prodej               --                1,770 ms / 963k      118 ms / 42.6k
--   kraj minus an obec, byt/prodej       --                --                   195 ms / 163k
--   kraj + obec pair (count book)        44 ms / 33.2k     44 ms / 33.2k        44 ms / 33.2k
--   no chips (national cell)             --                --                   100 ms / 14.8k
-- The first draft's per-row SubPlans could not be estimated, so the planner drove from
-- broker_identities -> listings; folded constants are estimated to the row, as 580's were.
-- NO INDEX: the arms OR five code columns and only ulice_kod is indexed (605), so no BitmapOr is
-- possible; listing_location is seq-scanned (40.7k buffers) in every live plan above, as in 580's.
--
-- APPLY through apply_migration.yml (--ref the feature branch) IMMEDIATELY BEFORE merging the code:
-- between the two the deployed API calls the dropped signature, so the leaderboard and the Outreach
-- target preview answer 500 -- they fail closed, never with a wrong board. The old API image can
-- still WRITE a campaign in that window, with the id lists this file rewrites; the new
-- api/outreach.select_targets refuses such a target (409, never the national list) and a re-run
-- of this file converts it.
-- ROLLBACK: forward only (rule 1). A re-run is a no-op: the precondition accepts this file's body.

set lock_timeout = '5s';

-- 0. The live body is the one this file was written against: 580's as applied (md5 of prosrc read
--    live 2026-10-17; the apply dropped its blank lines and two branch-header comments) or this
--    file's own (a re-run). Skipped in the CI schema-replay container (522 section 5's probe).
do $pre$
declare
  populated bool;
begin
  select count(*) = 100000 into populated
    from (select 1 from public.properties limit 100000) probe;
  if not populated then
    raise notice '609: replay container (corpus < 100k properties), precondition skipped';
    return;
  end if;
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'broker_leaderboard'), '')
     not in ('aabd5828e6bed3be596ae07a74c11216', '994f15ff0ee2fda42480924d1087a60c') then
    raise exception '609 refused: broker_leaderboard is not 580''s body nor this file''s (or has two overloads)';
  end if;
end
$pre$;

begin;

-- 1. The one array builder both branches share: a level's codes, include or exclude, in chip
--    order ('{}' when none). IMMUTABLE, so with constant arguments the planner evaluates it at
--    plan time and every arm reads `code = any('{...}')`, estimated like 580's id lists. The
--    arguments are constants in every custom plan, and the leaderboard's callers bind unnamed
--    statements (scraper.db.connect: prepare_threshold=None); a GENERIC plan would call it per
--    row. Not STRICT: a NULL excluded array means "nothing excluded" (Browse's coalesce(excl,
--    false)), and STRICT would turn every chip into no chip.
create or replace function public.district_level_codes(
  levels text[], ids bigint[], excluded boolean[], level text, want_excluded boolean)
returns bigint[]
language sql
immutable
parallel safe
as $codes$
  select coalesce(array_agg(c.id order by c.ord), '{}')
    from unnest(levels, ids, excluded) with ordinality as c(lvl, id, excl, ord)
   where c.lvl = level and coalesce(c.excl, false) = want_excluded
$codes$;

revoke all on function public.district_level_codes(text[], bigint[], boolean[], text, boolean)
  from public, anon, authenticated;
grant execute on function public.district_level_codes(text[], bigint[], boolean[], text, boolean)
  to service_role;

-- 2. broker_leaderboard: drop the 12-type signature, create the chip one, restate the ACL.
drop function if exists public.broker_leaderboard(
  bigint[], bigint[], bigint[], text, text, text, integer, bigint[], integer, boolean,
  text[], boolean);

create or replace function public.broker_leaderboard(
  districts_filter text[] default null::text[],
  districts_excluded_filter boolean[] default null::boolean[],
  districts_levels text[] default null::text[],
  districts_ids bigint[] default null::bigint[],
  p_category_main text default null::text,
  p_category_type text default null::text,
  p_metric text default 'active_property_count'::text,
  p_limit integer default 100,
  p_firm_ids bigint[] default null::bigint[],
  p_min_price_czk integer default null::integer,
  p_include_unpriced boolean default false,
  p_subtypes text[] default null::text[],
  p_include_unknown_subtype boolean default false
)
returns table(broker_id bigint, display_name text, primary_email text, primary_phone text,
              firm_id bigint, firm_name text, firm_domain text,
              listing_count bigint, property_count bigint,
              active_listing_count bigint, active_property_count bigint)
language sql
stable
as $function$
  with active_brokers as materialized (
    select b.id
    from brokers b
    where b.status = 'active'
      and (p_firm_ids is null or b.primary_firm_id = any(p_firm_ids))
  ),

  -- FAST BRANCH: exact precomputed cells (W4), for the shapes the count book has cells for:
  -- no live-only filter, and every chip an include at kraj / okres / obec, one level per name
  -- (609). `g` derives 580's three id lists from those chips -- constants once the planner
  -- folds district_level_codes() -- so the cell arms and the nested-chip suppression read
  -- as they did; "no chips" stays a parameter test, so a chip call drops the national arm.
  fast_raw as (
    select s.broker_id, s.listing_count, s.property_count,
           s.active_listing_count, s.active_property_count
    from broker_region_type_stats s
    cross join (
      select district_level_codes(districts_levels, districts_ids, districts_excluded_filter, 'kraj', false)
               as region_ids,
             district_level_codes(districts_levels, districts_ids, districts_excluded_filter, 'okres', false)
               as okres_ids,
             district_level_codes(districts_levels, districts_ids, districts_excluded_filter, 'obec', false)
               as obec_ids
    ) g
    where p_min_price_czk is null and p_subtypes is null
      and coalesce(districts_levels, '{}') <@ '{kraj,okres,obec}'
      and coalesce(districts_excluded_filter, '{}') <@ '{false}'
      and cardinality(districts_levels) is not distinct from cardinality(districts_filter)
      and s.category_main = coalesce(p_category_main, '*')
      and s.category_type = coalesce(p_category_type, '*')
      and (
        (coalesce(cardinality(districts_filter), 0) = 0
         and s.geo_level = 'cz')
        or (s.geo_level = 'region'
            and s.geo_id = any(g.region_ids))
        or (s.geo_level = 'okres'
            and s.geo_id = any(g.okres_ids)
            and not exists (
              select 1 from admin_boundaries ab
              where ab.id = s.geo_id
                and ab.parent_id = any(g.region_ids)))
        or (s.geo_level = 'obec'
            and s.geo_id = any(g.obec_ids)
            and not exists (
              select 1 from admin_boundaries ab
              left join admin_boundaries pa on pa.id = ab.parent_id
              where ab.id = s.geo_id
                and (ab.parent_id = any(g.okres_ids)
                     or pa.parent_id = any(g.region_ids))))
      )
  ),
  fast_agg as (
    select r.broker_id,
           sum(r.listing_count)::bigint          as listing_count,
           sum(r.property_count)::bigint         as property_count,
           sum(r.active_listing_count)::bigint   as active_listing_count,
           sum(r.active_property_count)::bigint  as active_property_count
    from fast_raw r
    join active_brokers ab on ab.id = r.broker_id
    group by r.broker_id
  ),
  fast_top as (
    select a.broker_id, a.listing_count, a.property_count,
           a.active_listing_count, a.active_property_count
    from fast_agg a
    order by case p_metric
               when 'listing_count'        then a.listing_count
               when 'property_count'       then a.property_count
               when 'active_listing_count' then a.active_listing_count
               else                             a.active_property_count
             end desc,
             a.broker_id
    limit greatest(1, least(p_limit, 2000))
  ),

  -- LIVE BRANCH: reads `listings` directly, for the filters the matview cannot
  -- express (price since 448, subtype since 469, and since 609 a chip it has no cell
  -- for: a cast_obce, a street, an exclude). 580's body, except the place predicate,
  -- which is api/location_filter.district_where's rendering: per level `code = any(that
  -- level's codes)`, an exclude as NOT COALESCE(..., false) so a NULL code survives it.
  live_priced as (
    select l.broker_identity_id,
           coalesce(l.property_id, -l.id) as property_key,
           (l.is_active and l.last_seen_at > now() - interval '7 days') as is_live
    from listings l
    join listing_location ll on ll.listing_id = l.id
    where (p_min_price_czk is not null or p_subtypes is not null
      or not coalesce(districts_levels, '{}') <@ '{kraj,okres,obec}'
      or not coalesce(districts_excluded_filter, '{}') <@ '{false}'
      or cardinality(districts_levels) is distinct from cardinality(districts_filter))
      and l.broker_identity_id is not null
      and ll.obec_kod is not null
      and (p_category_main is null or l.category_main = p_category_main)
      and (p_category_type is null or l.category_type = p_category_type)
      and (
        -- no include chip (no chips, or every one excluded) -- counted over the NAMES, as
        -- Browse does, so an include chip at a level no arm knows matches nothing
        coalesce(cardinality(districts_filter), 0) = 0
        or coalesce(districts_excluded_filter <@ '{true}'
                    and cardinality(districts_excluded_filter) >= cardinality(districts_filter), false)
        or ll.kraj_kod = any(district_level_codes(
             districts_levels, districts_ids, districts_excluded_filter, 'kraj', false))
        or ll.okres_kod = any(district_level_codes(
             districts_levels, districts_ids, districts_excluded_filter, 'okres', false))
        or ll.obec_kod = any(district_level_codes(
             districts_levels, districts_ids, districts_excluded_filter, 'obec', false))
        or ll.cast_obce_kod = any(district_level_codes(
             districts_levels, districts_ids, districts_excluded_filter, 'cast_obce', false))
        or ll.ulice_kod = any(district_level_codes(
             districts_levels, districts_ids, districts_excluded_filter, 'ulice', false))
      )
      and (
        not coalesce(true = any(districts_excluded_filter), false)
        or not coalesce(
          ll.kraj_kod = any(district_level_codes(
            districts_levels, districts_ids, districts_excluded_filter, 'kraj', true))
          or ll.okres_kod = any(district_level_codes(
            districts_levels, districts_ids, districts_excluded_filter, 'okres', true))
          or ll.obec_kod = any(district_level_codes(
            districts_levels, districts_ids, districts_excluded_filter, 'obec', true))
          or ll.cast_obce_kod = any(district_level_codes(
            districts_levels, districts_ids, districts_excluded_filter, 'cast_obce', true))
          or ll.ulice_kod = any(district_level_codes(
            districts_levels, districts_ids, districts_excluded_filter, 'ulice', true)),
          false)
      )
      and (
        p_min_price_czk is null
        or l.price_czk >= p_min_price_czk
        or (l.price_czk is null and p_include_unpriced)
      )
      and (
        p_subtypes is null
        or l.subtype = any(p_subtypes)
        or (l.subtype is null and p_include_unknown_subtype)
      )
  ),
  live_agg as (
    select bi.broker_id,
           count(*)                                                as listing_count,
           count(distinct p.property_key)                          as property_count,
           count(*) filter (where p.is_live)                       as active_listing_count,
           count(distinct p.property_key) filter (where p.is_live) as active_property_count
    from live_priced p
    join broker_identities bi on bi.id = p.broker_identity_id
    join active_brokers ab on ab.id = bi.broker_id
    group by bi.broker_id
  ),
  live_top as (
    select a.broker_id, a.listing_count, a.property_count,
           a.active_listing_count, a.active_property_count
    from live_agg a
    order by case p_metric
               when 'listing_count'        then a.listing_count
               when 'property_count'       then a.property_count
               when 'active_listing_count' then a.active_listing_count
               else                             a.active_property_count
             end desc,
             a.broker_id
    limit greatest(1, least(p_limit, 2000))
  ),

  combined as (
    select t.broker_id, b.display_name, b.primary_email, b.primary_phone,
           b.primary_firm_id      as firm_id,
           f.display_name         as firm_name,
           f.canonical_domain     as firm_domain,
           t.listing_count, t.property_count,
           t.active_listing_count, t.active_property_count
    from fast_top t
    join brokers b on b.id = t.broker_id
    left join firms f on f.id = b.primary_firm_id
    where p_min_price_czk is null and p_subtypes is null
      and coalesce(districts_levels, '{}') <@ '{kraj,okres,obec}'
      and coalesce(districts_excluded_filter, '{}') <@ '{false}'
      and cardinality(districts_levels) is not distinct from cardinality(districts_filter)
    union all
    select t.broker_id, b.display_name, b.primary_email, b.primary_phone,
           b.primary_firm_id      as firm_id,
           f.display_name         as firm_name,
           f.canonical_domain     as firm_domain,
           t.listing_count, t.property_count,
           t.active_listing_count, t.active_property_count
    from live_top t
    join brokers b on b.id = t.broker_id
    left join firms f on f.id = b.primary_firm_id
    where p_min_price_czk is not null or p_subtypes is not null
      or not coalesce(districts_levels, '{}') <@ '{kraj,okres,obec}'
      or not coalesce(districts_excluded_filter, '{}') <@ '{false}'
      or cardinality(districts_levels) is distinct from cardinality(districts_filter)
  )
  -- Explicit final ORDER BY, even though each branch already emits its rows in the right
  -- order and exactly one branch is ever non-empty: an incidental guarantee is not the
  -- same promise as an explicit one (migration 435's tiebreaker rationale).
  select * from combined
  order by case p_metric
             when 'listing_count'        then listing_count
             when 'property_count'       then property_count
             when 'active_listing_count' then active_listing_count
             else                             active_property_count
           end desc,
           broker_id
$function$;

revoke execute on function public.broker_leaderboard(
  text[], boolean[], text[], bigint[], text, text, text, integer, bigint[], integer, boolean,
  text[], boolean) from public;
revoke execute on function public.broker_leaderboard(
  text[], boolean[], text[], bigint[], text, text, text, integer, bigint[], integer, boolean,
  text[], boolean) from anon;
revoke execute on function public.broker_leaderboard(
  text[], boolean[], text[], bigint[], text, text, text, integer, bigint[], integer, boolean,
  text[], boolean) from authenticated;
-- Explicit, so the API's role does not depend on a default ACL (Supabase grants one; the CI
-- replay does not).
grant execute on function public.broker_leaderboard(
  text[], boolean[], text[], bigint[], text, text, text, integer, bigint[], integer, boolean,
  text[], boolean) to service_role;

-- 3. Outreach targets: the three id lists become chips, kraj first, each list in its stored order.
--    A code the register does not know keeps its code as its name (display only; the code filters).
with stored as (
  select c.id, k.lvl, k.rank, e.ord, e.code::bigint as code
    from public.outreach_campaigns c
    cross join (values ('region_ids', 'kraj', 1), ('okres_ids', 'okres', 2), ('obec_ids', 'obec', 3))
      as k(key, lvl, rank)
    cross join lateral jsonb_array_elements_text(
      case when jsonb_typeof(c.target -> k.key) = 'array' then c.target -> k.key else '[]'::jsonb end
    ) with ordinality as e(code, ord)
), chips as (
  select s.id,
         jsonb_agg(jsonb_build_object('name', coalesce(u.name, s.code::text), 'context', null,
                                      'excluded', false, 'level', s.lvl, 'id', s.code)
                   order by s.rank, s.ord) as districts
    from stored s
    left join lateral (
      select r.name from public.ruian_admin_units r
       where r.level::text = s.lvl and r.code = s.code and r.valid_to is null
       order by r.valid_from desc
       limit 1
    ) u on true
   group by s.id
)
update public.outreach_campaigns c
   set target = (c.target - 'region_ids' - 'okres_ids' - 'obec_ids')
                || jsonb_build_object('districts',
                     coalesce(c.target -> 'districts', '[]'::jsonb) || coalesce(ch.districts, '[]'::jsonb))
  from public.outreach_campaigns todo
  left join chips ch on ch.id = todo.id
 where c.id = todo.id
   and (todo.target ?| array['region_ids', 'okres_ids', 'obec_ids'] or not todo.target ? 'districts');

-- 4. Post-conditions: one overload, the chip parameters first, the five arms in both the include
--    and the exclude block, the helper's answers, service_role and no browser role can execute
--    either function, and every Outreach target is a chip list of (level, code)s.
do $post$
declare
  v_fn regprocedure := to_regprocedure('public.broker_leaderboard(text[], boolean[], text[], bigint[], '
    'text, text, text, integer, bigint[], integer, boolean, text[], boolean)');
  v_codes regprocedure := to_regprocedure(
    'public.district_level_codes(text[], bigint[], boolean[], text, boolean)');
  v_src text;
  v_names text[];
  v_lvl text;
  v_arm text;
begin
  if v_fn is null or (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
                                                     and proname = 'broker_leaderboard') <> 1 then
    raise exception '609: expected exactly one broker_leaderboard, the chip-array signature';
  end if;
  select prosrc, proargnames into v_src, v_names from pg_proc where oid = v_fn;
  if v_names[1:4] is distinct from
     array['districts_filter', 'districts_excluded_filter', 'districts_levels', 'districts_ids'] then
    raise exception '609: broker_leaderboard''s chip parameters are %', v_names[1:4];
  end if;
  foreach v_lvl in array array['kraj', 'okres', 'obec', 'cast_obce', 'ulice'] loop
    v_arm := format('ll.%s_kod = any(district_level_codes(', v_lvl);
    if (length(v_src) - length(replace(v_src, v_arm, ''))) / length(v_arm) <> 2 then
      raise exception '609: the % arm is not in both the include and the exclude block', v_lvl;
    end if;
  end loop;
  if v_codes is null
     or public.district_level_codes('{kraj,obec,kraj}', '{1,2,3}', '{f,f,t}', 'kraj', false) <> '{1}'
     or public.district_level_codes('{kraj,obec,kraj}', '{1,2,3}', '{f,f,t}', 'kraj', true) <> '{3}'
     or public.district_level_codes('{kraj}', '{1}', null, 'kraj', false) <> '{1}'
     or public.district_level_codes(null, null, null, 'kraj', false) <> '{}' then
    raise exception '609: district_level_codes does not answer as specified';
  end if;
  if has_function_privilege('anon', v_fn, 'execute')
     or has_function_privilege('authenticated', v_fn, 'execute')
     or not has_function_privilege('service_role', v_fn, 'execute')
     or has_function_privilege('anon', v_codes, 'execute')
     or has_function_privilege('authenticated', v_codes, 'execute')
     or not has_function_privilege('service_role', v_codes, 'execute') then
    raise exception '609: both functions must be executable by service_role and no browser role';
  end if;
  if exists (select 1 from public.outreach_campaigns
              where target ?| array['region_ids', 'okres_ids', 'obec_ids']
                 or jsonb_typeof(target -> 'districts') is distinct from 'array') then
    raise exception '609: an Outreach target still holds an id list, or no chip list';
  end if;
  if exists (select 1 from public.outreach_campaigns c, jsonb_array_elements(c.target -> 'districts') d
              where coalesce(d ->> 'level', '') not in ('kraj', 'okres', 'obec', 'cast_obce', 'ulice')
                 or coalesce((d ->> 'id')::bigint, 0) <= 0) then
    raise exception '609: an Outreach chip without a (level, code)';
  end if;
end
$post$;

commit;

reset lock_timeout;
