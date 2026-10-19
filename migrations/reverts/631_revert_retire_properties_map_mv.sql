-- 631_revert_retire_properties_map_mv.sql
--
-- REVERT for 631_retire_properties_map_mv.sql. SHIPPED UNAPPLIED.
--
-- A FULL RESTORE OF DERIVED OBJECTS, NO DATA (R10): everything 631 dropped is rebuilt here from
-- migration history, and nothing 631 dropped held stored data. It restores:
--   rebuild_properties_map_mv()        522's body verbatim (522:277-328)   md5(prosrc) e18ea5161c8af9ceab5b07a62b6dc37d
--   properties_map_mv + its 2 indexes  522's shape (the select the rebuild runs, properties_map_mv_pk and
--                                      properties_map_mv_cover), SELECT for authenticated, never anon
--   properties_map_visible(...)        629's body and ACL verbatim (629:154-209, 213-214)
--                                                                           md5(prosrc) 9c1351bbc86f7c1c7a0ec4a7d08db1df
--   pg_cron job browse-map-rebuild     522's schedule (7,37 * * * *) and its 1500 s statement_timeout
--   derived_artifacts row              440's declaration (freshness NULL until the forced rebuild stamps it)
--   recompute_property_mf(bigint[])    507's body verbatim (507:380-522)   md5(prosrc) 1ab31fca5867b88494692e322cac2d7c
--   recompute_mf_gross_yields()        507's body verbatim (507:524-667)   md5(prosrc) 6c031b2362ed463369361c8320af30a0
-- 631's $pre$ refused unless each live body had exactly these hashes, so the restore is byte for byte;
-- $post$ re-hashes all four. The ACLs are the ones read live 2026-10-19 (pg_proc.proacl), restated
-- explicitly rather than left to default privileges (since 612 a new function in public is not
-- executable by PUBLIC or authenticated): the rebuild by service_role only; properties_map_visible and
-- both MF functions by authenticated and service_role; never PUBLIC or anon.
--
-- Lives in migrations/reverts/ like every revert in this build: the CI schema replay applies
-- `ls migrations/*.sql | sort` (NOT recursive), so a revert beside its forward migration would be
-- applied right after it and silently undo it in every replayed environment; and
-- tests/test_migration_numbers.py forbids a duplicate number above 304.
--
-- ORDER INSIDE THE ONE TRANSACTION. The rebuild function, then the matview WITH NO DATA (so the map
-- function's body, which names it, compiles), then properties_map_visible, the job, the registry row
-- (BEFORE the rebuild: its stamp is a plain UPDATE that matches nothing without the row, 440's lesson),
-- the MF functions, and last the ONE forced rebuild (~61 s), which builds the matview through its
-- blue-green swap, stamps the row and grants SELECT. A failure anywhere leaves nothing half-restored.
--
-- WHAT APPLYING THIS COSTS, stated so it is never done casually: the rebuild again (~61 s twice an
-- hour, ~22 GB of WAL a day, R2), a second copy of browse_projection, and two dead MF writers back
-- in the catalog. On its own it changes nothing a user sees: since W1 the SPA
-- reads pins and tile counts from browse_list. Putting the map back on the matview also needs W1's
-- SPA reverted and 630's revert applied (630's revert refuses while properties_map_mv is gone, so
-- THIS file runs first). Prefer fixing forward.
--
-- APPLY by hand (apply_migration.yml takes only a numbered file in migrations/):
--   psql -X -v ON_ERROR_STOP=1 "$SUPABASE_DB_URL" -f migrations/reverts/631_revert_retire_properties_map_mv.sql
-- on the session pooler (the transaction pooler caps a statement near 2 min). A re-run is a no-op but
-- for one more rebuild: IF NOT EXISTS / CREATE OR REPLACE, the job upserts by name, the row is ON
-- CONFLICT DO NOTHING.

-- ci-allow-dynamic: rebuild_properties_map_mv blue-greens properties_map_mv through EXECUTE'd DDL on every
-- tick (276/277, 522); its body is restored verbatim and the scanner cannot read a dollar-quoted body.
-- ci-allow-ungated: rebuild_properties_map_mv SECURITY DEFINER maintenance, execute held by service_role only
-- (its live ACL, asserted in $post$); it reads browse_projection and writes only its own matview and stamp.

set lock_timeout = '5s';
set statement_timeout = '120s';

begin;

-- ---------------------------------------------------------------------------
-- 0. Preconditions: the projection carries every column the indexes name, and no other overload of a
--    restored name exists (a defaulted twin would make every PostgREST call ambiguous).
-- ---------------------------------------------------------------------------
do $pre$
declare
  v_other text;
begin
  if to_regclass('public.browse_projection') is null then
    raise exception '631 revert refused: browse_projection is missing; the matview is a copy of it';
  end if;
  if (select count(*) from pg_attribute
       where attrelid = 'public.browse_projection'::regclass and not attisdropped
         and attname in ('property_id', 'category_main', 'category_type', 'lat', 'lng', 'sreality_id',
                         'price_czk', 'disposition', 'subtype', 'area_m2', 'last_seen_at',
                         'first_seen_at', 'is_active')) <> 13 then
    raise exception '631 revert refused: browse_projection lacks a column 522''s two indexes name';
  end if;
  select string_agg(p.oid::regprocedure::text, ', ') into v_other
    from pg_proc p
   where p.pronamespace = 'public'::regnamespace
     and p.proname in ('properties_map_visible', 'rebuild_properties_map_mv',
                       'recompute_mf_gross_yields', 'recompute_property_mf')
     and p.oid <> all (array_remove(array[
           to_regprocedure('public.properties_map_visible(boolean, text, bigint[], bigint[], text, bigint[], bigint[], text, bigint[], bigint[])'),
           to_regprocedure('public.rebuild_properties_map_mv()'),
           to_regprocedure('public.recompute_mf_gross_yields()'),
           to_regprocedure('public.recompute_property_mf(bigint[])')]::oid[], null));
  if v_other is not null then
    raise exception '631 revert refused: % exist(s) under a restored name with another signature', v_other;
  end if;
end
$pre$;

-- ---------------------------------------------------------------------------
-- 1. The rebuild, 522's body verbatim; its live ACL (owner and service_role).
-- ---------------------------------------------------------------------------
create or replace function public.rebuild_properties_map_mv()
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
  if not pg_try_advisory_xact_lock(hashtext('rebuild_properties_map_mv')) then
    raise notice 'rebuild_properties_map_mv: previous run still active, skipping tick';
    return;
  end if;

  execute 'drop materialized view if exists properties_map_mv_next';
  execute $q$
    create materialized view properties_map_mv_next as
    select * from browse_projection
    where lat is not null and lng is not null
    order by category_main, category_type, lat, lng
  $q$;
  execute 'create unique index properties_map_mv_next_pk on properties_map_mv_next (property_id)';
  execute $q$
    create index properties_map_mv_next_cover on properties_map_mv_next
      (category_main, category_type, lat, lng)
      include (sreality_id, price_czk, disposition, subtype, area_m2,
               last_seen_at, first_seen_at, is_active)
  $q$;
  execute 'analyze properties_map_mv_next';
  execute 'select count(*) from properties_map_mv_next' into n;

  execute 'drop materialized view if exists properties_map_mv';
  execute 'alter materialized view properties_map_mv_next rename to properties_map_mv';
  execute 'alter index properties_map_mv_next_pk rename to properties_map_mv_pk';
  execute 'alter index properties_map_mv_next_cover rename to properties_map_mv_cover';
  execute 'grant select on properties_map_mv to authenticated';

  if has_table_privilege('anon', 'properties_map_mv', 'SELECT') then
    raise exception 'rebuild_properties_map_mv: anon must never hold SELECT on properties_map_mv -- refusing to publish this rebuild (see migration 374)';
  end if;

  update derived_artifacts
     set last_succeeded_at = now(),
         complete_through  = now(),
         last_duration_ms  = (extract(epoch from clock_timestamp() - t0) * 1000)::integer,
         last_rows         = n
   where name = 'properties_map_mv';
  perform pg_notify('pgrst', 'reload schema');
end
$function$;

revoke all on function public.rebuild_properties_map_mv() from public, anon, authenticated;
grant execute on function public.rebuild_properties_map_mv() to service_role;

-- ---------------------------------------------------------------------------
-- 2. The matview in 522's shape, its two indexes and its grant. WITH NO DATA: the forced rebuild
--    (section 7) builds it, by the same select, through its swap.
-- ---------------------------------------------------------------------------
create materialized view if not exists public.properties_map_mv as
  select * from public.browse_projection
  where lat is not null and lng is not null
  order by category_main, category_type, lat, lng
with no data;
create unique index if not exists properties_map_mv_pk on public.properties_map_mv (property_id);
create index if not exists properties_map_mv_cover on public.properties_map_mv
  (category_main, category_type, lat, lng)
  include (sreality_id, price_czk, disposition, subtype, area_m2,
           last_seen_at, first_seen_at, is_active);
revoke all on public.properties_map_mv from anon;
grant select on public.properties_map_mv to authenticated;

-- ---------------------------------------------------------------------------
-- 3. The map's pin source, 629's body and ACL verbatim.
-- ---------------------------------------------------------------------------
create or replace function public.properties_map_visible(
  -- migration 537's exclusion, now a parameter so the SPA reads ONE source for both lenses.
  hide_dismissed boolean default true,
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
returns setof public.browse_projection
language sql
stable
as $$
  select l.* from public.properties_map_mv l
  where (not hide_dismissed or not exists (
        select 1 from public.property_dismissals_public d where d.property_id = l.property_id))
      -- migration 629: the curated-set scope. Three sets, three sides each, one clause shape,
      -- judged through the *_public membership views under the caller's RLS. A NULL side is
      -- off and its subquery never runs; `{}` on a unit list constrains nothing.
      and (tags_set is distinct from 'in' or exists (
            select 1 from public.property_tags_public c where c.property_id = l.property_id))
      and (tags_set is distinct from 'out' or not exists (
            select 1 from public.property_tags_public c where c.property_id = l.property_id))
      and (tags_in is null or array_length(tags_in, 1) is null or l.property_id in (
            select c.property_id from public.property_tags_public c where c.tag_id = any(tags_in)
            group by c.property_id
            having count(distinct c.tag_id) = (select count(distinct u) from unnest(tags_in) u)))
      and (tags_out is null or not exists (
            select 1 from public.property_tags_public c
            where c.property_id = l.property_id and c.tag_id = any(tags_out)))
      and (collections_set is distinct from 'in' or exists (
            select 1 from public.collection_properties_public c where c.property_id = l.property_id))
      and (collections_set is distinct from 'out' or not exists (
            select 1 from public.collection_properties_public c where c.property_id = l.property_id))
      and (collections_in is null or array_length(collections_in, 1) is null or exists (
            select 1 from public.collection_properties_public c
            where c.property_id = l.property_id and c.collection_id = any(collections_in)))
      and (collections_out is null or not exists (
            select 1 from public.collection_properties_public c
            where c.property_id = l.property_id and c.collection_id = any(collections_out)))
      and (pipeline_set is distinct from 'in' or exists (
            select 1 from public.property_pipeline_public c where c.property_id = l.property_id))
      and (pipeline_set is distinct from 'out' or not exists (
            select 1 from public.property_pipeline_public c where c.property_id = l.property_id))
      and (pipeline_in is null or array_length(pipeline_in, 1) is null or exists (
            select 1 from public.property_pipeline_public c
            where c.property_id = l.property_id and c.stage_id = any(pipeline_in)))
      and (pipeline_out is null or not exists (
            select 1 from public.property_pipeline_public c
            where c.property_id = l.property_id and c.stage_id = any(pipeline_out)))
$$;

revoke execute on function public.properties_map_visible(boolean, text, bigint[], bigint[], text, bigint[], bigint[], text, bigint[], bigint[]) from public, anon;
grant execute on function public.properties_map_visible(boolean, text, bigint[], bigint[], text, bigint[], bigint[], text, bigint[], bigint[]) to authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 4. The job, 522's schedule and budget. Guarded (513's idiom): the CI replay has no pg_cron; where it
--    exists a failure here fails the revert.
-- ---------------------------------------------------------------------------
do $cron$
begin
  if to_regclass('cron.job') is null then
    raise notice '631 revert: no pg_cron here; browse-map-rebuild not scheduled. Schedule it on whatever scheduler runs the rebuilds.';
    return;
  end if;
  perform cron.schedule(
    'browse-map-rebuild',
    '7,37 * * * *',
    $$set statement_timeout='1500s'; select public.rebuild_properties_map_mv();$$
  );
end
$cron$;

-- ---------------------------------------------------------------------------
-- 5. The registry row, 440's declaration, before the rebuild that stamps it.
-- ---------------------------------------------------------------------------
insert into public.derived_artifacts
  (name, producer, host, cadence, staleness_budget, is_serving)
values ('properties_map_mv', 'rebuild_properties_map_mv', 'pg_cron', '7,37 * * * *', interval '90 minutes', true)
on conflict (name) do nothing;

-- ---------------------------------------------------------------------------
-- 6. The two MF writers, 507's bodies verbatim (dead as before: nothing schedules or calls them, R11);
--    their live ACL.
-- ---------------------------------------------------------------------------
create or replace function public.recompute_property_mf(p_ids bigint[] default null)
returns integer
language plpgsql
as $function$
declare
  n integer;
begin
  with cand as (
    select
      p.id, p.category_main,
      -- W4-a: the property's place is its representative listing's resolved
      -- point. `properties.geom/ku_id/obec_id` stopped being written in this
      -- wave (recompute_property_stats lost best_geo) and are dropped in W4-c.
      ll.geom as pt, ll.obec_kod as obec_id,
      p.current_price_czk as price_czk, p.area_m2,
      p.has_balcony, p.terrace, p.furnished, p.garage, p.has_lift,
      p.building_type,
      (p.condition = 'novostavba') as is_nov,
      case
        when p.disposition ~ '^[[:space:]]*[01]' then 1
        when p.disposition ~ '^[[:space:]]*2'    then 2
        when p.disposition ~ '^[[:space:]]*3'    then 3
        when p.disposition ~ '^[[:space:]]*[4-9]' then 4
        else null
      end as vk
    from properties p
    left join listing_location ll on ll.listing_id = p.repr_listing_ref_id
    where p.category_type = 'prodej'
      and p.status = 'active'
      and (p_ids is null or p.id = any(p_ids))
  ),
  -- W4-a: the katastr key is no longer a stored column. It is a point-in-polygon
  -- against the RUIAN katastr layer, computed ONCE here -- AFTER the eligibility
  -- predicates that used to sit in `matched`, so the PIP runs over the sale flats
  -- that can match a rent-map cell at all, not over every sale row in the corpus.
  terr as (
    select c.*, public.ruian_katastr_code(c.pt) as ku_id
    from cand c
    where c.category_main = 'byt'
      and c.vk is not null
      and c.price_czk >= 100000
      and c.area_m2 is not null and c.area_m2 >= 12
  ),
  matched as (
    select
      c.id, c.price_czk, c.area_m2, c.vk, c.is_nov,
      c.has_balcony, c.terrace, c.furnished, c.garage, c.has_lift,
      c.building_type,
      coalesce(vku.ruian_code, vob.ruian_code)           as ruian_code,
      coalesce(vku.level, vob.level)                      as level,
      case when vku.ruian_code is not null
           then vku.ku_name else vob.obec_name end        as terr_name,
      coalesce(vku.kraj, vob.kraj)                        as kraj,
      coalesce(vku.source_revision, vob.source_revision)  as source_revision,
      case
        when vku.ruian_code is not null
          then case when c.is_nov then vku.ref_rent_novostavba_per_m2
                    else vku.ref_rent_per_m2 end
        else case when c.is_nov then vob.ref_rent_novostavba_per_m2
                  else vob.ref_rent_per_m2 end
      end                                                 as base
    from terr c
    left join rent_map_values_public vku
      on vku.vk = c.vk and vku.ruian_code = c.ku_id
    left join rent_map_values_public vob
      on vob.vk = c.vk and vob.ruian_code = c.obec_id
    where (vku.ruian_code is not null or vob.ruian_code is not null)
  ),
  adj as (
    select
      m.id,
      coalesce(sum(a.czk_per_m2) filter (where
           (a.attribute = 'balcony'   and m.has_balcony)
        or (a.attribute = 'terrace'   and m.terrace)
        or (a.attribute = 'furnished' and m.furnished = 'ano')
        or (a.attribute = 'garage'    and m.garage)
        or (a.attribute = 'elevator'  and m.has_lift)
        or (a.attribute = 'other_material' and m.is_nov
            and m.building_type is not null
            and m.building_type not in ('panel', 'cihla'))
      ), 0) as adj_sum,
      coalesce(jsonb_agg(
        jsonb_build_object('attribute', a.attribute, 'czk_per_m2', a.czk_per_m2)
        order by a.attribute
      ) filter (where
           (a.attribute = 'balcony'   and m.has_balcony)
        or (a.attribute = 'terrace'   and m.terrace)
        or (a.attribute = 'furnished' and m.furnished = 'ano')
        or (a.attribute = 'garage'    and m.garage)
        or (a.attribute = 'elevator'  and m.has_lift)
        or (a.attribute = 'other_material' and m.is_nov
            and m.building_type is not null
            and m.building_type not in ('panel', 'cihla'))
      ), '[]'::jsonb) as adj_items
    from matched m
    join rent_map_adjustments_public a
      on a.vk = m.vk and a.is_novostavba = m.is_nov
    group by m.id
  ),
  computed as (
    select
      m.id,
      round((m.base + coalesce(a.adj_sum, 0)) * m.area_m2)::integer as rent_czk,
      round((m.base + coalesce(a.adj_sum, 0)) * m.area_m2 * 12
            / m.price_czk * 100, 2) as yield_pct,
      jsonb_build_object(
        'territory', jsonb_build_object(
          'ruian_code', m.ruian_code, 'level', m.level,
          'name', m.terr_name, 'kraj', m.kraj),
        'vk', m.vk,
        'is_novostavba', m.is_nov,
        'source_revision', m.source_revision,
        'base_per_m2', m.base,
        'adjustments', coalesce(a.adj_items, '[]'::jsonb),
        'adjustments_sum_per_m2', coalesce(a.adj_sum, 0),
        'total_per_m2', m.base + coalesce(a.adj_sum, 0),
        'area_m2', m.area_m2,
        'monthly_rent_czk',
          round((m.base + coalesce(a.adj_sum, 0)) * m.area_m2)::integer
      ) as detail
    from matched m
    left join adj a on a.id = m.id
    where m.base is not null
  ),
  final as (
    select c.id, comp.rent_czk, comp.yield_pct, comp.detail
    from cand c
    left join computed comp on comp.id = c.id
  )
  update properties p
    set mf_reference_rent_czk = f.rent_czk,
        mf_gross_yield_pct    = f.yield_pct,
        mf_reference_rent     = f.detail
  from final f
  where p.id = f.id
    and (p.mf_reference_rent_czk is distinct from f.rent_czk
         or p.mf_gross_yield_pct is distinct from f.yield_pct
         or p.mf_reference_rent is distinct from f.detail);

  get diagnostics n = row_count;
  return n;
end;
$function$;

create or replace function public.recompute_mf_gross_yields()
returns integer
language plpgsql
as $function$
declare
  n integer;
begin
  with cand as (
    select
      l.sreality_id, l.category_main,
      -- W4-a: territory from `listing_location`, not from the trigger-filled
      -- `listings.ku_id` / `obec_id` (both dropped in W4-c).
      ll.geom as pt, ll.obec_kod as obec_id, l.price_czk, l.area_m2,
      l.has_balcony, l.terrace, l.furnished, l.garage, l.has_lift,
      l.building_type,
      (l.condition = 'novostavba') as is_nov,
      case
        when l.disposition ~ '^[[:space:]]*[01]' then 1
        when l.disposition ~ '^[[:space:]]*2'    then 2
        when l.disposition ~ '^[[:space:]]*3'    then 3
        when l.disposition ~ '^[[:space:]]*[4-9]' then 4
        else null
      end as vk
    from listings l
    left join listing_location ll on ll.listing_id = l.id
    where l.category_type = 'prodej'
  ),
  -- W4-a: the katastr key is no longer a stored column. It is a point-in-polygon
  -- against the RUIAN katastr layer, computed ONCE here -- AFTER the eligibility
  -- predicates that used to sit in `matched`, so the PIP runs over the sale flats
  -- that can match a rent-map cell at all, not over every sale row in the corpus.
  terr as (
    select c.*, public.ruian_katastr_code(c.pt) as ku_id
    from cand c
    where c.category_main = 'byt'
      and c.vk is not null
      and c.price_czk >= 100000
      and c.area_m2 is not null and c.area_m2 >= 12
  ),
  matched as (
    select
      c.sreality_id, c.price_czk, c.area_m2, c.vk, c.is_nov,
      c.has_balcony, c.terrace, c.furnished, c.garage, c.has_lift,
      c.building_type,
      coalesce(vku.ruian_code, vob.ruian_code)           as ruian_code,
      coalesce(vku.level, vob.level)                      as level,
      case when vku.ruian_code is not null
           then vku.ku_name else vob.obec_name end        as terr_name,
      coalesce(vku.kraj, vob.kraj)                        as kraj,
      coalesce(vku.source_revision, vob.source_revision)  as source_revision,
      case
        when vku.ruian_code is not null
          then case when c.is_nov then vku.ref_rent_novostavba_per_m2
                    else vku.ref_rent_per_m2 end
        else case when c.is_nov then vob.ref_rent_novostavba_per_m2
                  else vob.ref_rent_per_m2 end
      end                                                 as base
    from terr c
    left join rent_map_values_public vku
      on vku.vk = c.vk and vku.ruian_code = c.ku_id
    left join rent_map_values_public vob
      on vob.vk = c.vk and vob.ruian_code = c.obec_id
    where (vku.ruian_code is not null or vob.ruian_code is not null)
  ),
  adj as (
    select
      m.sreality_id,
      coalesce(sum(a.czk_per_m2) filter (where
           (a.attribute = 'balcony'   and m.has_balcony)
        or (a.attribute = 'terrace'   and m.terrace)
        or (a.attribute = 'furnished' and m.furnished = 'ano')
        or (a.attribute = 'garage'    and m.garage)
        or (a.attribute = 'elevator'  and m.has_lift)
        or (a.attribute = 'other_material' and m.is_nov
            and m.building_type is not null
            and m.building_type not in ('panel', 'cihla'))
      ), 0) as adj_sum,
      coalesce(jsonb_agg(
        jsonb_build_object('attribute', a.attribute, 'czk_per_m2', a.czk_per_m2)
        order by a.attribute
      ) filter (where
           (a.attribute = 'balcony'   and m.has_balcony)
        or (a.attribute = 'terrace'   and m.terrace)
        or (a.attribute = 'furnished' and m.furnished = 'ano')
        or (a.attribute = 'garage'    and m.garage)
        or (a.attribute = 'elevator'  and m.has_lift)
        or (a.attribute = 'other_material' and m.is_nov
            and m.building_type is not null
            and m.building_type not in ('panel', 'cihla'))
      ), '[]'::jsonb) as adj_items
    from matched m
    join rent_map_adjustments_public a
      on a.vk = m.vk and a.is_novostavba = m.is_nov
    group by m.sreality_id
  ),
  computed as (
    select
      m.sreality_id,
      round((m.base + coalesce(a.adj_sum, 0)) * m.area_m2)::integer as rent_czk,
      round((m.base + coalesce(a.adj_sum, 0)) * m.area_m2 * 12
            / m.price_czk * 100, 2) as yield_pct,
      jsonb_build_object(
        'territory', jsonb_build_object(
          'ruian_code', m.ruian_code, 'level', m.level,
          'name', m.terr_name, 'kraj', m.kraj),
        'vk', m.vk,
        'is_novostavba', m.is_nov,
        'source_revision', m.source_revision,
        'base_per_m2', m.base,
        'adjustments', coalesce(a.adj_items, '[]'::jsonb),
        'adjustments_sum_per_m2', coalesce(a.adj_sum, 0),
        'total_per_m2', m.base + coalesce(a.adj_sum, 0),
        'area_m2', m.area_m2,
        'monthly_rent_czk',
          round((m.base + coalesce(a.adj_sum, 0)) * m.area_m2)::integer
      ) as detail
    from matched m
    left join adj a on a.sreality_id = m.sreality_id
    where m.base is not null
  ),
  final as (
    select c.sreality_id, comp.rent_czk, comp.yield_pct, comp.detail
    from cand c
    left join computed comp on comp.sreality_id = c.sreality_id
  )
  update listings l
    set mf_reference_rent_czk = f.rent_czk,
        mf_gross_yield_pct    = f.yield_pct,
        mf_reference_rent     = f.detail
  from final f
  where l.sreality_id = f.sreality_id
    and (l.mf_reference_rent_czk is distinct from f.rent_czk
         or l.mf_gross_yield_pct is distinct from f.yield_pct
         or l.mf_reference_rent is distinct from f.detail);

  get diagnostics n = row_count;

  -- properties.mf_* is now computed from the golden record, NOT mirrored from the
  -- representative child's per-listing mf_*.
  perform public.recompute_property_mf(null);

  return n;
end;
$function$;

revoke all on function public.recompute_property_mf(bigint[]) from public, anon;
grant execute on function public.recompute_property_mf(bigint[]) to authenticated, service_role;
revoke all on function public.recompute_mf_gross_yields() from public, anon;
grant execute on function public.recompute_mf_gross_yields() to authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 7. ONE forced rebuild (522's idiom): queue for the key, then the function's own try-lock succeeds
--    against this transaction (advisory locks are re-entrant), so it cannot skip. It replaces the empty
--    matview with the built one, stamps the row and grants SELECT.
-- ---------------------------------------------------------------------------
set statement_timeout = '1500s';

do $force_map$
begin
  perform pg_advisory_xact_lock(hashtext('rebuild_properties_map_mv'));
  perform public.rebuild_properties_map_mv();
end
$force_map$;

set statement_timeout = '120s';

-- ---------------------------------------------------------------------------
-- 8. Post-conditions: every body byte for byte, every ACL as read live, the matview built and stamped
--    in this transaction, the job scheduled, and the pin source answers.
-- ---------------------------------------------------------------------------
do $post$
declare
  v_mv oid := to_regclass('public.properties_map_mv');
  v_visible oid := to_regprocedure('public.properties_map_visible(boolean, text, bigint[], bigint[], text, bigint[], bigint[], text, bigint[], bigint[])');
  v_rebuild oid := to_regprocedure('public.rebuild_properties_map_mv()');
  v_yields oid := to_regprocedure('public.recompute_mf_gross_yields()');
  v_property_mf oid := to_regprocedure('public.recompute_property_mf(bigint[])');
begin
  if v_mv is null or not (select c.relispopulated from pg_class c where c.oid = v_mv) then
    raise exception '631 revert did not land: properties_map_mv is missing or was never built';
  end if;
  if (select array_agg(c.relname::text order by c.relname)
        from pg_index i join pg_class c on c.oid = i.indexrelid
       where i.indrelid = v_mv)
     is distinct from array['properties_map_mv_cover', 'properties_map_mv_pk'] then
    raise exception '631 revert did not land: properties_map_mv does not carry 522''s two indexes';
  end if;
  if not has_table_privilege('authenticated', v_mv, 'select') or has_table_privilege('anon', v_mv, 'select') then
    raise exception '631 revert did not land: properties_map_mv''s grants are not authenticated-only';
  end if;
  if coalesce((select md5(prosrc) from pg_proc where oid = v_visible), '') <> '9c1351bbc86f7c1c7a0ec4a7d08db1df'
     or coalesce((select md5(prosrc) from pg_proc where oid = v_rebuild), '') <> 'e18ea5161c8af9ceab5b07a62b6dc37d'
     or coalesce((select md5(prosrc) from pg_proc where oid = v_yields), '') <> '6c031b2362ed463369361c8320af30a0'
     or coalesce((select md5(prosrc) from pg_proc where oid = v_property_mf), '') <> '1ab31fca5867b88494692e322cac2d7c' then
    raise exception '631 revert did not land: a restored body is not its source (629, 522, 507, 507)';
  end if;
  if not (select prosecdef from pg_proc where oid = v_rebuild)
     or (select prosecdef from pg_proc where oid = v_visible)
     or has_function_privilege('authenticated', v_rebuild, 'execute')
     or has_function_privilege('anon', v_rebuild, 'execute')
     or not has_function_privilege('service_role', v_rebuild, 'execute')
     or not has_function_privilege('authenticated', v_visible, 'execute')
     or has_function_privilege('anon', v_visible, 'execute')
     or not has_function_privilege('authenticated', v_yields, 'execute')
     or has_function_privilege('anon', v_yields, 'execute')
     or not has_function_privilege('authenticated', v_property_mf, 'execute')
     or has_function_privilege('anon', v_property_mf, 'execute') then
    raise exception '631 revert did not land: a security posture or an ACL is not the one read live 2026-10-19';
  end if;
  if not exists (select 1 from public.derived_artifacts
                  where name = 'properties_map_mv' and last_succeeded_at = now()) then
    raise exception '631 revert did not land: the forced rebuild did not stamp the properties_map_mv row';
  end if;
  if to_regclass('cron.job') is null then
    raise notice '631 revert: no pg_cron here; the job is not asserted';
  elsif not exists (select 1 from cron.job j
                     where j.jobname = 'browse-map-rebuild' and j.schedule = '7,37 * * * *'
                       and j.command = $c$set statement_timeout='1500s'; select public.rebuild_properties_map_mv();$c$) then
    raise exception '631 revert did not land: browse-map-rebuild is not scheduled as 522 scheduled it';
  end if;
  perform 1 from public.properties_map_visible() limit 1;
end
$post$;

notify pgrst, 'reload schema';

commit;

reset statement_timeout;
reset lock_timeout;
