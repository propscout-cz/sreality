-- 725_location_w8_readers_on_mirror.sql -- W8-3 (#2362): the readers of admin_boundaries move onto the RÚIAN mirror.
-- Ruling Q12b (docs/design/location-quality/PROGRAM.md): the mirror is the only polygon store, and a unit-keyed dataset
-- carries a RÚIAN code and borrows its polygon from the mirror at render time. Ruling Q11: one obec_population table
-- keyed by RÚIAN code and year.
--   1. ruian_unit_render(level, code, name, geom): a unit's render polygon at the CURRENT registry version, the one
--      polygon read. It joins by version, not valid_to, so a unit closed and reopened by an SCD-2 load reads one row
--      before the publish and one after. Owner-rights; the service role alone reads it.
--   2. obec_population(obec_kod, year): ČSÚ population by town code and year (no FK: mirror codes repeat under SCD-2).
--      Backfill: the 6,254 rows admin_boundaries holds (year 2026). scripts/load_obec_population.py writes it from now.
--   3. price_stat_obce_picker_public: the register's kraj/okres/obec tree at the current version, by the same rule as
--      ruian_unit_render (a unit drawn at the current version), so a load that closes and reopens units before its
--      publish never empties or doubles it (#2378); a parent outside the tree (a kraj's NUTS-2 region) reads NULL, the
--      population is the latest year's. sreality_id becomes NULL (the deployed SPA selects it; 729 drops it). Same
--      columns, so CREATE OR REPLACE keeps its grant.
--   4. sold_coverage: the town is the one whose lossless pip pieces at the current version cover the point
--      (resolve_db.CONTAINING_OBEC_SQL's shape), named by the register. Same signature, ACL and SECURITY DEFINER.
--   5. price_stat_growth: 425's body without its EXISTS on admin_boundaries_public; obec_id is a register code by
--      construction (723), so the check filtered nothing.
-- ADDITIVE in schema (a new view, a new table, three bodies replaced in place, no column or row dropped), but not in
-- behaviour: sold_coverage answers by the mirror's town (the pip pieces, no longer the 55 m-simplified legacy polygon)
-- and the picker's sreality_id reads NULL. Apply BEFORE the merge: every wire shape is kept.
-- Idempotent: rerun as is after a lock_timeout trip.
-- Receipt: obec_population 6,254 rows; the picker 6,349 (14 + 77 + 6,258).
-- VERIFY: select count(*), max(year) from obec_population;  -- 6,254, 2026
--         select count(*) from price_stat_obce_picker_public;  -- 6,349

set lock_timeout = '5s';

create or replace view ruian_unit_render as
  select u.level, u.code, u.name, g.geom
    from registry_versions v
    join ruian_admin_unit_geometries g on g.registry_version_id = v.id and g.purpose = 'render'
    join ruian_admin_units u on u.id = g.unit_id
   where v.is_current;
revoke all on ruian_unit_render from public, anon, authenticated;
grant select on ruian_unit_render to service_role;

create table if not exists obec_population (
  obec_kod   bigint      not null,
  year       integer     not null,
  population integer     not null,
  source     text        not null default 'csu',
  loaded_at  timestamptz not null default now(),
  primary key (obec_kod, year)
);
alter table obec_population enable row level security;
revoke all on obec_population from anon, authenticated;
grant select, insert, update on obec_population to service_role;
revoke delete, truncate on obec_population from service_role;

insert into obec_population (obec_kod, year, population)
select b.id, b.population_year, b.population
  from admin_boundaries b
 where b.level = 'obec' and b.population is not null and b.population_year is not null
on conflict do nothing;

create or replace view price_stat_obce_picker_public as
  select u.code as id, u.level::text as level, u.name, p.code as parent_id, pop.population,
         null::integer as sreality_id
    from registry_versions v
    join ruian_admin_unit_geometries g on g.registry_version_id = v.id and g.purpose = 'render'
    join ruian_admin_units u on u.id = g.unit_id
    left join ruian_admin_units p on p.id = u.parent_id and p.level in ('kraj', 'okres')
    left join obec_population pop on pop.obec_kod = u.code and u.level = 'obec'
                                 and pop.year = (select max(year) from obec_population)
   where v.is_current and u.level in ('kraj', 'okres', 'obec');

create or replace function public.sold_coverage(
  p_lat double precision,
  p_lng double precision
)
returns table (
  obec_kod            bigint,
  obec_name           text,
  fetched_at          timestamptz,
  record_count        integer,
  source_total        integer,
  truncated           boolean,
  last_attempt_at     timestamptz,
  last_attempt_status text
)
language sql
stable
security definer
set search_path = public, extensions
as $$
  with cell as (
    select u.code as obec_kod, u.name as obec_name
    from registry_versions v
    join ruian_admin_unit_geometries g on g.registry_version_id = v.id and g.purpose = 'pip'
    join ruian_admin_units u on u.id = g.unit_id
    where v.is_current
      and u.level = 'obec'
      and st_covers(g.geom, st_setsrid(st_makepoint(p_lng, p_lat), 4326))
    limit 1
  )
  select c.obec_kod, c.obec_name,
         ok.fetched_at, ok.record_count, ok.source_total, ok.truncated,
         try.fetched_at, try.status
  from cell c
  left join lateral (
    -- an `ok` row carries `error` only when the page cap cut its walk short (sold_fetch)
    select f.fetched_at, f.record_count, f.source_total, f.error is not null as truncated
    from sold_transaction_fetches f
    where f.obec_kod = c.obec_kod and f.status = 'ok'
    order by f.fetched_at desc
    limit 1
  ) ok on true
  left join lateral (
    select f.fetched_at, f.status
    from sold_transaction_fetches f
    where f.obec_kod = c.obec_kod
    order by f.fetched_at desc
    limit 1
  ) try on true
$$;

create or replace function price_stat_growth(
  p_dataset_id bigint,
  p_from text default null,
  p_to text default null
)
returns table(
  obec_id bigint, locality_name text,
  sale_latest_price integer, sale_cagr_pct double precision, sale_min_active integer,
  rent_latest_price integer, rent_cagr_pct double precision, rent_min_active integer,
  gross_yield_pct double precision, yield_change_pp_pa double precision
)
language sql stable as $function$
  with bounds as (
    select
      case when p_from is null then null
           else split_part(p_from, '-', 1)::int * 12
                + split_part(p_from, '-', 2)::int - 1 end as from_idx,
      case when p_to is null then null
           else split_part(p_to, '-', 1)::int * 12
                + split_part(p_to, '-', 2)::int - 1 end as to_idx
  ),
  obs as (
    select o.obec_id, o.locality_name, o.category_type_cb,
           (o.year * 12 + o.month - 1) as ymi, o.price, o.active_count
      from price_stat_observations_public o, bounds b
     where o.dataset_id = p_dataset_id
       and o.price is not null and o.price > 0
       and o.obec_id is not null
       and (b.from_idx is null or (o.year * 12 + o.month - 1) >= b.from_idx)
       and (b.to_idx is null or (o.year * 12 + o.month - 1) <= b.to_idx)
  ),
  agg as (
    select obec_id, max(locality_name) as locality_name, category_type_cb,
           min(ymi) as start_ymi, max(ymi) as end_ymi,
           least(
             (array_agg(active_count order by ymi))[1],
             (array_agg(active_count order by ymi desc))[1]
           ) as min_active,
           (array_agg(price order by ymi))[1] as start_price,
           (array_agg(price order by ymi desc))[1] as end_price
      from obs group by obec_id, category_type_cb
  ),
  piv as (
    select obec_id,
           max(locality_name) as locality_name,
           max(end_price)   filter (where category_type_cb = 1) as sale_end,
           max(start_price) filter (where category_type_cb = 1) as sale_start,
           max(end_ymi)     filter (where category_type_cb = 1) as sale_end_ymi,
           min(start_ymi)   filter (where category_type_cb = 1) as sale_start_ymi,
           max(min_active)  filter (where category_type_cb = 1) as sale_min_active,
           max(end_price)   filter (where category_type_cb = 2) as rent_end,
           max(start_price) filter (where category_type_cb = 2) as rent_start,
           max(end_ymi)     filter (where category_type_cb = 2) as rent_end_ymi,
           min(start_ymi)   filter (where category_type_cb = 2) as rent_start_ymi,
           max(min_active)  filter (where category_type_cb = 2) as rent_min_active
      from agg group by obec_id
  )
  select
    p.obec_id,
    p.locality_name,
    p.sale_end::int,
    case when p.sale_end_ymi - p.sale_start_ymi >= 12 and p.sale_start > 0
         then (power(p.sale_end::numeric / p.sale_start,
                     12.0 / (p.sale_end_ymi - p.sale_start_ymi)) - 1) * 100 end,
    p.sale_min_active::int,
    p.rent_end::int,
    case when p.rent_end_ymi - p.rent_start_ymi >= 12 and p.rent_start > 0
         then (power(p.rent_end::numeric / p.rent_start,
                     12.0 / (p.rent_end_ymi - p.rent_start_ymi)) - 1) * 100 end,
    p.rent_min_active::int,
    case when p.sale_end > 0 and p.rent_end is not null
         then round((12.0 * p.rent_end / p.sale_end * 100)::numeric, 2)::double precision end,
    case when p.sale_end > 0 and p.sale_start > 0
              and p.rent_end is not null and p.rent_start is not null
              and greatest(p.sale_end_ymi, p.rent_end_ymi)
                  - least(p.sale_start_ymi, p.rent_start_ymi) >= 12
         then ((12.0 * p.rent_end / p.sale_end * 100)
               - (12.0 * p.rent_start / p.sale_start * 100))
              / ((greatest(p.sale_end_ymi, p.rent_end_ymi)
                  - least(p.sale_start_ymi, p.rent_start_ymi)) / 12.0) end
  from piv p;
$function$;
