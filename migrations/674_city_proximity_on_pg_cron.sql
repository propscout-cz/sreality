-- 674_city_proximity_on_pg_cron.sql -- Actions exit W0 (#2012): the hourly city-proximity refresh moves from
-- recompute_city_proximity.yml onto pg_cron, beside a daily trim of pg_cron's own run history.
--
--   1. recompute_city_proximity(boolean): 636's body with two changes. Its `drop table if exists _prox_anchors`
--      raised a NOTICE in every fresh session (the anchor table drops on commit), and a NOTICE inside a pg_cron
--      command blocks pg_cron's single launcher until the command ends (658): the drop now runs only when the
--      table exists. And a transaction-scoped advisory lock serializes the hourly run with a full rebuild (the
--      two attended tools end with one, a hand run is another) -- what the workflow's concurrency group did.
--      EXECUTE leaves authenticated (granted since 142; nothing in the browser calls it).
--   2. pg_cron job recompute-city-proximity at :40 hourly (the workflow's minute) under the script's 20 min
--      statement_timeout. The workflow's last 48 runs to 2026-10-25: p50 7 s, p95 12 s, max 41 s.
--   3. pg_cron job cron-history-trim, daily 03:17: deletes cron.job_run_details rows older than 90 days (59 983
--      rows back to 2026-06-01 on 2026-10-25, never trimmed). 90 days, because Health's capacity check compares
--      each job with its lowest weekly p50 since CAPACITY_REFERENCE_FROM, which a rail keeps at most 90 days back.
--      Daily, because that check measures only a job with a finished run in the last 24 h.
--
-- Apply with apply_migration.yml (psql -f, statement autocommit): every statement is idempotent, and $pre$
-- accepts 636's body or this file's.

set statement_timeout = '60s';

do $pre$
begin
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
                 where p.pronamespace = 'public'::regnamespace and p.proname = 'recompute_city_proximity'), '')
       not in ('84947f47472f7dd1e15a0a1865ebc700', 'aebcd995a0618f8763c4ff9f21a0c33b') then
    raise exception '674 refused: recompute_city_proximity is not 636''s body nor this file''s, or has two overloads';
  end if;
end
$pre$;

-- ---------------------------------------------------------------------------
-- 1. recompute_city_proximity: 636's body, NOTICE-free and serialized.
-- ---------------------------------------------------------------------------
create or replace function recompute_city_proximity(p_full boolean default false)
returns integer
language plpgsql
as $$
declare
  n integer;
begin
  -- Transaction-scoped, so pooler-safe: the hourly run and a full rebuild never update the same rows at once.
  perform pg_advisory_xact_lock(hashtext('recompute_city_proximity'));

  -- Anchor set: obce big enough to satisfy the smallest pop threshold the UI
  -- offers (>=10000), plus the 206 curated cities that carry the qualitative
  -- indexes (latest revision). One GiST-indexed temp set keeps the per-property
  -- spatial probe over a few hundred rows instead of all ~6 250 obce.
  -- Only a second call in one transaction finds the table; `drop table if exists` would NOTICE in a fresh one.
  if to_regclass('pg_temp._prox_anchors') is not null then
    drop table pg_temp._prox_anchors;
  end if;
  create temp table _prox_anchors on commit drop as
  with latest as (select max(source_revision) as rev from city_index_values),
  idx as (
    select cc.admin_boundary_id as ab_id,
      max(civ.value) filter (where civ.index_name = 'pracovni_mista')    as jobs,
      max(civ.value) filter (where civ.index_name = 'stehovani_mladych') as youth,
      max(civ.value) filter (where civ.index_name = 'celkove_hodnoceni') as overall
    from curated_cities cc
    join city_index_values civ
      on civ.city_id = cc.id
     and civ.source_revision = (select rev from latest)
     and civ.index_name in ('pracovni_mista', 'stehovani_mladych', 'celkove_hodnoceni')
    where cc.admin_boundary_id is not null
    group by cc.admin_boundary_id
  )
  select ab.id as ab_id, ab.geom,
    case when ab.population >= 10000 then ab.population end as population,
    idx.jobs, idx.youth, idx.overall
  from admin_boundaries ab
  left join idx on idx.ab_id = ab.id
  where ab.level = 'obec' and ab.geom is not null
    and (ab.population >= 10000 or idx.ab_id is not null);

  create index on _prox_anchors using gist (geom);
  analyze _prox_anchors;

  update properties p set
    home_obec_pop    = s.home_obec_pop,
    near_pop_5km     = s.near_pop_5km,
    near_pop_15km    = s.near_pop_15km,
    near_jobs_5km    = s.near_jobs_5km,
    near_jobs_15km   = s.near_jobs_15km,
    near_youth_5km   = s.near_youth_5km,
    near_youth_15km  = s.near_youth_15km,
    near_overall_5km = s.near_overall_5km,
    near_overall_15km= s.near_overall_15km,
    city_proximity_computed_at = now()
  from (
    select p2.id, p2.location_listing_ref_id,
      hp.population as home_obec_pop,
      a5.pop  as near_pop_5km,  a15.pop  as near_pop_15km,
      a5.jobs as near_jobs_5km, a15.jobs as near_jobs_15km,
      a5.youth as near_youth_5km, a15.youth as near_youth_15km,
      a5.overall as near_overall_5km, a15.overall as near_overall_15km
    from properties p2
    join listing_location ll on ll.listing_id = p2.location_listing_ref_id
    left join lateral (
      select ab.population from admin_boundaries ab
      where ab.level = 'obec'
      order by ll.geom::geography <-> ab.geom
      limit 1
    ) hp on true
    left join lateral (
      select max(population) as pop, max(jobs) as jobs, max(youth) as youth, max(overall) as overall
      from _prox_anchors a where st_dwithin(ll.geom::geography, a.geom, 5000)
    ) a5 on true
    left join lateral (
      select max(population) as pop, max(jobs) as jobs, max(youth) as youth, max(overall) as overall
      from _prox_anchors a where st_dwithin(ll.geom::geography, a.geom, 15000)
    ) a15 on true
    where ll.geom is not null
      and (p_full or p2.city_proximity_computed_at is null)
  ) s
  where p.id = s.id
    -- Re-checked on a row a concurrent rollup updated: when its location ad moved, the row is
    -- skipped, and the rollup that moved it has already nulled its stamp for the next run.
    and p.location_listing_ref_id = s.location_listing_ref_id;

  get diagnostics n = row_count;
  return n;
end
$$;

comment on function public.recompute_city_proximity(boolean) is
  'Fill properties.home_obec_pop + near_{pop,jobs,youth,overall}_{5,15}km at the property''s LOCATION AD''s '
  'point (properties.location_listing_ref_id, migration 636). Incremental (city_proximity_computed_at IS '
  'NULL) unless p_full; a row whose location ad moved while the run computed it is skipped and stays NULL '
  'for the next run. Run hourly by pg_cron job recompute-city-proximity (migration 674); the full rebuild '
  '(scripts/recompute_city_proximity.py --full) ends the population and curated-city loads.';

revoke all on function public.recompute_city_proximity(boolean) from public, anon, authenticated;
grant execute on function public.recompute_city_proximity(boolean) to service_role;

-- ---------------------------------------------------------------------------
-- 2. + 3. The schedules. Guarded like 620: the CI replay has no pg_cron.
-- ---------------------------------------------------------------------------
do $cron$
begin
  perform cron.schedule(
    'recompute-city-proximity',
    '40 * * * *',
    $$set statement_timeout='1200s'; select public.recompute_city_proximity(false);$$
  );
  perform cron.schedule(
    'cron-history-trim',
    '17 3 * * *',
    $$set statement_timeout='300s'; delete from cron.job_run_details where start_time < now() - interval '90 days';$$
  );
exception when others then
  raise notice 'pg_cron unavailable; recompute-city-proximity and cron-history-trim not scheduled (%).', sqlerrm;
end
$cron$;

-- ---------------------------------------------------------------------------
-- Post-conditions.
-- ---------------------------------------------------------------------------
do $post$
begin
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
                 where p.pronamespace = 'public'::regnamespace and p.proname = 'recompute_city_proximity'), '')
       <> 'aebcd995a0618f8763c4ff9f21a0c33b' then
    raise exception '674 did not land: recompute_city_proximity is not this file''s body (or has two overloads)';
  end if;
  if has_function_privilege('anon', 'public.recompute_city_proximity(boolean)', 'execute')
     or has_function_privilege('authenticated', 'public.recompute_city_proximity(boolean)', 'execute') then
    raise exception '674: a browser role can execute recompute_city_proximity';
  end if;
  if to_regclass('cron.job') is null then
    raise notice '674: no pg_cron here; the schedules are not asserted';
  else
    if not exists (select 1 from cron.job
                    where jobname = 'recompute-city-proximity' and schedule = '40 * * * *' and active
                      and command like '%statement_timeout%recompute_city_proximity(false)%') then
      raise exception '674: recompute-city-proximity is not scheduled hourly under a statement_timeout';
    end if;
    if not exists (select 1 from cron.job
                    where jobname = 'cron-history-trim' and schedule = '17 3 * * *' and active
                      and command like '%statement_timeout%job_run_details%90 days%') then
      raise exception '674: cron-history-trim is not scheduled daily under a statement_timeout';
    end if;
  end if;
end
$post$;
