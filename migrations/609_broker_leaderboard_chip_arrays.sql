-- 609_broker_leaderboard_chip_arrays.sql -- the broker leaderboard takes Browse's location chips.
--
-- WHY. Operator ruling 2026-10-16 ("what you pick is what you filter"): a location chip is ONE
-- RUIAN unit at the level picked -- kraj, okres, obec, cast_obce, ulice -- tested as plain code
-- equality everywhere (605). The leaderboard knew three id lists, kraj / okres / obec, and no
-- exclude, so Brokers / Outreach dropped a quarter, a street or an excluded place: until this file
-- those pages had to refuse such a selection (fail closed) rather than show the country.
--
-- WHAT CHANGES. One function and the stored Outreach targets; no table, column or index.
--   1. broker_leaderboard: p_region_ids / p_okres_ids / p_obec_ids are REPLACED by the four parallel
--      chip arrays browse_stats_properties and browse_map_cells take -- districts_filter (the names,
--      which drive the unnest), districts_excluded_filter, districts_levels, districts_ids -- then
--      580's nine parameters unchanged. Body = 580's except:
--      (a) the LIVE branch's place predicate is 605's include + exclude blocks over
--          listing_location's five codes (kraj_kod, okres_kod, obec_kod, cast_obce_kod, ulice_kod),
--          the predicate api/location_filter.district_where renders (an exclude keeps a row whose
--          code is NULL; tests/test_broker_leaderboard_chip_parity.py runs it over the shared table);
--      (b) the FAST branch (the count book) answers only when every chip is an include at kraj /
--          okres / obec, one level per name -- the cells it has -- and derives 580's three id lists
--          from those chips, so its cell arms and nested-chip suppression read as before. Any other
--          chip set (a cast_obce, a street, an exclude) reads `listings` live. The two gates stay
--          exact complements (tests/test_broker_leaderboard_contract.py pins both strings).
--      CREATE OR REPLACE cannot change parameter types, so the old signature is DROPPED and 580's
--      three revokes restated in the same transaction: service-role only (299, Amendment A6).
--   2. outreach_campaigns.target: stored {region_ids, okres_ids, obec_ids} become chips in
--      target.districts ({name: the register unit's name, context: null, excluded: false, level, id});
--      api/outreach.select_targets reads nothing else. 0 campaigns live on 2026-10-17.
--
-- NO INDEX (EXPLAIN ANALYZE of this body, live 2026-10-17, byt/prodej). One street chip: the fast
-- branch is pruned at plan time; the live one takes 1.77 s / 963k buffers, 877k of them the
-- listings-per-broker loop every price-filtered call already pays, plus a 40.7k-buffer seq scan of
-- listing_location. The chip CASE is a per-row filter, as 580's OR'd id lists were, so no code index
-- (605's ulice_kod one included) is reachable from it. A kraj + obec pair reads the count book
-- exactly as 580 did (33,177 buffers, 44 ms, both).
--
-- APPLY through apply_migration.yml (--ref the feature branch) IMMEDIATELY BEFORE merging the code:
-- between the two the deployed API calls the dropped signature, so the leaderboard and the Outreach
-- target preview answer 500 -- they fail closed, never with a wrong board.
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
     not in ('aabd5828e6bed3be596ae07a74c11216', '48616df4b1010a91bf2b923c446ff6ed') then
    raise exception '609 refused: broker_leaderboard is not 580''s body nor this file''s (or has two overloads)';
  end if;
end
$pre$;

begin;

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
  -- (609). `g` derives 580's three id lists from those chips, so the cell arms and the
  -- nested-chip suppression read as they did; "no chips" stays a parameter test, so the
  -- planner still drops the national arm from a chip call (an InitPlan count would not).
  fast_raw as (
    select s.broker_id, s.listing_count, s.property_count,
           s.active_listing_count, s.active_property_count
    from broker_region_type_stats s
    cross join (
      select array(select c.id from unnest(districts_levels, districts_ids, districts_excluded_filter) as c(lvl, id, excl)
                    where c.lvl = 'kraj'  and not coalesce(c.excl, false)) as region_ids,
             array(select c.id from unnest(districts_levels, districts_ids, districts_excluded_filter) as c(lvl, id, excl)
                    where c.lvl = 'okres' and not coalesce(c.excl, false)) as okres_ids,
             array(select c.id from unnest(districts_levels, districts_ids, districts_excluded_filter) as c(lvl, id, excl)
                    where c.lvl = 'obec'  and not coalesce(c.excl, false)) as obec_ids
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
  -- for: a cast_obce, a street, an exclude). 580's body, except the place predicate.
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
              -- ONE code predicate (W3 S3), Browse's (605) over listing_location's five codes:
              -- a chip is a level plus a RUIAN code, plain equality; no code matches nothing.
              when admin_id is null                  then false
              when lvl = 'kraj'                      then ll.kraj_kod      = admin_id
              when lvl = 'okres'                     then ll.okres_kod     = admin_id
              when lvl = 'obec'                      then ll.obec_kod      = admin_id
              when lvl = 'cast_obce'                 then ll.cast_obce_kod = admin_id
              when lvl = 'ulice'                     then ll.ulice_kod     = admin_id
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
              -- ONE code predicate (W3 S3), Browse's (605) over listing_location's five codes:
              -- a chip is a level plus a RUIAN code, plain equality; no code matches nothing.
              when admin_id is null                  then false
              when lvl = 'kraj'                      then ll.kraj_kod      = admin_id
              when lvl = 'okres'                     then ll.okres_kod     = admin_id
              when lvl = 'obec'                      then ll.obec_kod      = admin_id
              when lvl = 'cast_obce'                 then ll.cast_obce_kod = admin_id
              when lvl = 'ulice'                     then ll.ulice_kod     = admin_id
              else false
            end
        )
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

-- 2. Outreach targets: the three id lists become chips, kraj first, each list in its stored order.
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

-- 3. Post-conditions: one overload, the chip parameters first, both chip blocks carry the five arms,
--    no browser role can execute it, and every Outreach target is a chip list of (level, code)s.
do $post$
declare
  v_fn regprocedure := to_regprocedure('public.broker_leaderboard(text[], boolean[], text[], bigint[], '
    'text, text, text, integer, bigint[], integer, boolean, text[], boolean)');
  v_src text;
  v_names text[];
  v_lvl text;
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
    if (length(v_src) - length(replace(v_src, format('then ll.%s_kod', v_lvl), '')))
       / length(format('then ll.%s_kod', v_lvl)) <> 2 then
      raise exception '609: the % arm is not in both chip blocks', v_lvl;
    end if;
  end loop;
  if has_function_privilege('anon', v_fn, 'execute')
     or has_function_privilege('authenticated', v_fn, 'execute') then
    raise exception '609: a browser role can execute broker_leaderboard';
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
