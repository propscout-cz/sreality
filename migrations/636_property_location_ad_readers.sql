-- 636_property_location_ad_readers.sql -- the database's property readers place a property at its LOCATION
-- AD, and the database keeps that pointer present (ONE PIN sprint, W4; docs/design/one-pin/PROGRAM.md "The
-- definition" and "W4", rulings R1, R3, R5, R7-R9; GLOSSARY "Location ad").
--
-- WHY. 635 stored each property's location ad (properties.location_listing_ref_id: its most precisely
-- located ad, often the canonical ad, not always), but nothing reads it: Browse, the property page, the
-- watchdog and city proximity still place a property at its canonical ad, so where the canonical ad is a
-- street ring and another of its ads sits on an address point (property 15100: canonical ad 40102 at
-- `street`, location ad 13193960 at `address_point`) the map draws the ring. This file moves the three
-- database readers onto the pointer. The canonical ad keeps everything that is not location: price,
-- category, area, photos, link, the per-field facts, listing_id and source_id_native (R3).
--
-- WHAT CHANGES. ADDITIVE: nothing is dropped; one CHECK, one appended view column, one derived stamp nulled.
--   1. browse_projection: 625's body verbatim with ONE edit, the listing_location join key
--      p.repr_listing_ref_id -> p.location_listing_ref_id. The point, label, precision rank and radius,
--      the five RUIAN codes, the MF lateral (it reads ll) and the served rule follow; listing_id and
--      source_id_native stay the canonical ad's. The 76 output columns keep their names, types and order,
--      which is what makes CREATE OR REPLACE legal here and keeps every dependant valid: browse_list
--      (`select * from browse_projection`, its rebuild's CTAS and re-patch insert, 630) and
--      browse_list_visible(...) (`returns setof browse_projection`, 629). No forced rebuild: the next */15
--      tick carries the change into Browse.
--   2. properties_public: 617's body verbatim with the same one edit (the ad-facts join `l` stays on the
--      canonical ad) and location_listing_ref_id APPENDED last, the one column change CREATE OR REPLACE
--      allows; the watchdog's served rule names it. Its one dependant, pipeline_board_public (618), names
--      its columns, so it neither breaks nor gains one. CREATE OR REPLACE keeps the ACL (live 2026-10-20:
--      postgres and service_role all, authenticated SELECT) and the empty reloptions (no WITH clause here
--      or before).
--   3. recompute_city_proximity(boolean): 508's body with the join key moved and the outer UPDATE gaining
--      `and p.location_listing_ref_id = s.location_listing_ref_id`. Under READ COMMITTED the UPDATE re-checks
--      that on a row a concurrent rollup changed, so a run that races a location change writes nothing for
--      that row (the rollup nulled its stamp; the next run computes it). Same signature, plpgsql, volatile,
--      invoker, no SET: CREATE OR REPLACE keeps its ACL (live: postgres, authenticated, service_role EXECUTE).
--   4. properties_location_ad_present CHECK ((repr_listing_ref_id is null) = (location_listing_ref_id is
--      null)): a property has a location ad exactly when it has a canonical ad, i.e. when it has an ad. Both
--      writers set the two pointers in one statement and the no-ads reset nulls both (W3). It is added NOT
--      VALID as the transaction's LAST statement, so its ACCESS EXCLUSIVE lock is held only to the commit,
--      and VALIDATED after the commit in a statement of its own, under SHARE UPDATE EXCLUSIVE: the 1.01M-row
--      scan blocks no read and no write (619's idiom; validated inside the transaction, the ADD's lock would
--      be held through the scan, which is a plain ADD CHECK).
--   5. city_proximity_computed_at := NULL on the active properties whose location ad is not their canonical
--      ad (14,061, 14,054 of them stamped, 2026-10-20): their proximity was computed at the canonical ad's
--      point; the hourly recompute_city_proximity.yml (:40) recomputes them from the location ad. It runs
--      after the commit, in a statement of its own: inside the transaction its row locks would meet the
--      ALTER's lock in a deadlock with any rollup writing one of those rows, and after the commit every
--      run that starts reads the new body.
--
-- GATES. $pre$ takes the list rebuild's advisory key first (631's idiom: queued under the 5 s lock limit,
-- held to the commit; a tick that fires meanwhile skips, so neither view swap nor the ALTER waits behind
-- its minute-long read of properties). It refuses while any property has one pointer without the other,
-- while a recompute_city_proximity call is running (its old body would stamp the canonical ad's values
-- after this commit), and unless each body it restates is the one it was written against or this file's
-- own (a re-run), with one overload:
--   browse_projection            md5(pg_get_viewdef)  80fde37bd22abb86d5e93bcc0c41fa38 (625) -> 9fb6da98b0d4d171079c51328e77a279
--   properties_public            md5(pg_get_viewdef)  600434e950ee7db543213167e9e069f7 (617) -> 18efda1aeb70ff4414780381969e5372
--   recompute_city_proximity     md5(prosrc)          6b7931e8b358e78e1b784d05b50ff982 (508) -> 84947f47472f7dd1e15a0a1865ebc700
-- The three old hashes were read live 2026-10-20; the function's equals migrations/508's text, and the
-- two views' equal the live definitions re-hashed offline. The two new view hashes are the live definitions
-- with this file's edits applied as text ($post$ is their proof); the function's is this file's text.
-- pg_get_viewdef prints a function unqualified when the session's search_path reaches it: production
-- ("$user", public, extensions) and the CI replay (PostGIS in public) both print st_y / st_x bare, so one
-- hash serves both (the CI replay is the proof for the replay). $pre$ also keeps each object's column
-- list, ACL, reloptions and posture for $post$, which proves, in the transaction: the three new hashes;
-- browse_projection's columns unchanged and properties_public's unchanged but for location_listing_ref_id
-- bigint, last; every ACL, reloption and posture unchanged; both views join listing_location on the
-- location ad; the CHECK's definition; and one smoke, property 15100 through browse_projection at its
-- location ad (point, rank and label compared with listing_location read at its location_listing_ref_id,
-- so a parallel merge cannot fail it; on 2026-10-20 that was 50.05995, 14.46709, rank 100, "Pod Sychrovem
-- II 850/43, Praha"), its listing_id still the canonical ad, skipped with a notice where 15100 has no ads
-- (the CI replay). $post_check$, after the
-- VALIDATE: the CHECK is validated.
--
-- APPLY through apply_migration.yml (--ref the W4 branch) once the PR is green, IMMEDIATELY BEFORE it
-- merges (the watchdog code reads properties_public.location_listing_ref_id; the rollup's proximity reset
-- moves to the location ad in the same PR), never through the MCP (it wraps the whole file in one
-- transaction, so the reset and the VALIDATE would run under the ALTER's lock). Not in a list rebuild's
-- minutes (:00-:02, :15-:17, :30-:32, :45-:47: the advisory wait hits the 5 s limit) nor while
-- recompute_city_proximity.yml runs (from :40; $pre$ refuses). Every statement is idempotent and the
-- workflow's lock-timeout retry re-runs the file: the gates accept this file's bodies, the CHECK is added
-- only where absent, VALIDATE on a validated CHECK is a no-op, and the reset only re-nulls stamps.
-- On "deadlock detected" (a reader holding properties, then reading properties_public), re-dispatch: the
-- workflow's retry does not match it, and the transaction rolled back. A lock timeout in the reset or the
-- VALIDATE (after the commit) only needs a re-run.
-- ROLLBACK: migrations/reverts/636_revert_property_location_ad_readers.sql (the three bodies verbatim from
-- 625, 617 and 508; drops the CHECK; nulls nothing).

set lock_timeout = '5s';
set statement_timeout = '120s';

begin;

-- ---------------------------------------------------------------------------
-- 0. Preconditions, and the pre-images $post$ compares against.
-- ---------------------------------------------------------------------------
do $pre$
declare
  v_bp text := md5(pg_get_viewdef('public.browse_projection'::regclass));
  v_pp text := md5(pg_get_viewdef('public.properties_public'::regclass));
  v_fn text := coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
                  where p.pronamespace = 'public'::regnamespace and p.proname = 'recompute_city_proximity'), '');
  v_runs bigint;
begin
  perform pg_advisory_xact_lock(hashtext('rebuild_browse_list'));

  if v_bp not in ('80fde37bd22abb86d5e93bcc0c41fa38', '9fb6da98b0d4d171079c51328e77a279') then
    raise exception '636 refused: browse_projection is not 625''s body nor this file''s (md5 %)', v_bp;
  end if;
  if v_pp not in ('600434e950ee7db543213167e9e069f7', '18efda1aeb70ff4414780381969e5372') then
    raise exception '636 refused: properties_public is not 617''s body nor this file''s (md5 %)', v_pp;
  end if;
  if v_fn not in ('6b7931e8b358e78e1b784d05b50ff982', '84947f47472f7dd1e15a0a1865ebc700') then
    raise exception '636 refused: recompute_city_proximity is not 508''s body nor this file''s, or has two overloads (md5 %)', v_fn;
  end if;

  -- Only while 508's body is live: a re-run after the commit must not refuse the reset and the VALIDATE.
  select count(*) into v_runs
    from pg_stat_activity
   where v_fn = '6b7931e8b358e78e1b784d05b50ff982'
     and pid <> pg_backend_pid() and state <> 'idle' and backend_type = 'client backend'
     and query ~* 'select\s+(\*\s+from\s+)?(public\.)?recompute_city_proximity\(';
  if v_runs > 0 then
    raise exception '636 refused: % backend(s) are running recompute_city_proximity; apply after the run ends', v_runs;
  end if;

  if exists (select 1 from public.properties
              where (repr_listing_ref_id is null) <> (location_listing_ref_id is null)) then
    raise exception '636 refused: a property has a canonical ad without a location ad, or the reverse (W3''s sweep and fill are not complete)';
  end if;

  perform set_config('one_pin636.pre', jsonb_build_object(
      'bp_cols', (select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum)
                    from pg_attribute a
                   where a.attrelid = 'public.browse_projection'::regclass and a.attnum > 0 and not a.attisdropped),
      'pp_cols', (select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum)
                    from pg_attribute a
                   where a.attrelid = 'public.properties_public'::regclass and a.attnum > 0 and not a.attisdropped),
      'bp_acl', (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
                   from pg_class c where c.oid = 'public.browse_projection'::regclass),
      'pp_acl', (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
                   from pg_class c where c.oid = 'public.properties_public'::regclass),
      'fn', (select l.lanname::text || ' ' || p.provolatile::text || ' ' || p.prosecdef::text || ' '
                    || coalesce(p.proconfig::text, '-') || ' ' || coalesce(p.proacl::text, '-') || ' '
                    || pg_get_function_arguments(p.oid) || ' -> ' || pg_get_function_result(p.oid)
               from pg_proc p join pg_language l on l.oid = p.prolang
              where p.oid = to_regprocedure('public.recompute_city_proximity(boolean)')))::text, true);
end
$pre$;

-- ---------------------------------------------------------------------------
-- 1. browse_projection: 625's body; only the listing_location join key moves to the location ad.
--    listing_id and source_id_native stay the canonical ad's (the card's link to its advert).
-- ---------------------------------------------------------------------------
create or replace view browse_projection as
  select
      p.id as property_id,
      p.repr_listing_id as sreality_id,
      p.first_seen_at,
      p.last_seen_at,
      p.is_active,
      p.category_main,
      p.category_type,
      p.current_price_czk as price_czk,
      p.area_m2,
      p.disposition,
      st_y(ll.geom) as lat,
      st_x(ll.geom) as lng,
      p.has_balcony,
      p.has_parking,
      p.has_lift,
      p.building_type,
      p.condition,
      p.energy_rating,
      p.estate_area,
      p.usable_area,
      p.garden_area,
      p.category_sub_cb,
      p.furnished,
      p.terrace,
      p.cellar,
      p.garage,
      p.parking_lots,
      p.ownership,
      measure_tom_days(p.is_active, p.first_seen_at, p.last_seen_at) as tom_days,
      measure_price_per_m2(p.current_price_czk::numeric, p.area_m2, p.category_main, p.category_type) as price_per_m2,
      p.building_condition_level,
      p.apartment_condition_level,
      p.source,
      mf.mf_reference_rent_czk,
      mf.mf_gross_yield_pct,
      p.home_obec_pop,
      p.near_pop_5km,
      p.near_pop_15km,
      p.near_jobs_5km,
      p.near_jobs_15km,
      p.near_youth_5km,
      p.near_youth_15km,
      p.near_overall_5km,
      p.near_overall_15km,
      p.subtype,
      p.last_change_at,
      ll.obec_kod  as obec_id,
      ll.okres_kod as okres_id,
      ll.kraj_kod  as region_id,
      p.price_change_count,
      p.price_change_count_30d,
      p.price_change_count_90d,
      p.price_change_count_365d,
      p.total_price_change_pct,
      p.repr_listing_ref_id as listing_id,
      (select l.source_id_native from listings l where l.id = p.repr_listing_ref_id) as source_id_native,
      measure_price_per_m2_basis(p.category_main, p.category_type) as price_per_m2_basis,
      location_display_label(ll.street_name, ll.house_number_cp, ll.house_number_co,
                             ll.obec_name, ll.cast_obce_name, ll.country_code,
                             ll.country_status) as display_label,
      ll.cast_obce_kod as cast_obce_id,
      ll.uncertainty_radius_m,
      gr.rank::int as granularity_rank,
      public.plot_area_m2(p.category_main, p.area_m2::numeric,
                          p.estate_area::numeric) as plot_area_m2,
      ll.ulice_kod as ulice_id,
      coalesce(p.all_sources, '{}'::text[]) as all_sources,
      coalesce(p.active_sources, '{}'::text[]) as active_sources,
      p.source_count,
      p.newest_ad_at_sreality,
      p.newest_ad_at_bazos,
      p.newest_ad_at_idnes,
      p.newest_ad_at_maxima,
      p.newest_ad_at_ceskereality,
      p.newest_ad_at_bezrealitky,
      p.newest_ad_at_mmreality,
      p.newest_ad_at_remax,
      p.newest_ad_at_realitymix,
      (select measure_tom_observed(k.source, k.category_main, k.category_type, k.first_seen_at)
         from listings k where k.property_id = p.id
        order by (k.category_main is null or k.category_type is null), k.first_seen_at, k.id
        limit 1) as tom_observed
  from properties p
       left join listing_location ll on ll.listing_id = p.location_listing_ref_id
       left join location_granularity_rank gr on gr.granularity = ll.granularity
       left join lateral public.mf_reference(
           p.category_main, p.category_type, p.disposition, p.area_m2, p.current_price_czk,
           p.condition, p.has_balcony, p.terrace, p.furnished, p.garage, p.has_lift,
           p.building_type, ll.obec_kod, ll.katastr_kod, ll.country_status) mf on true
  where p.status = 'active'::text
    -- THE CONSUMER RULE (rule 25), read off the label join (522).
    and (ll.geom IS NOT NULL OR ll.country_status = 'foreign')
  ;

comment on view public.browse_projection is
  'Browse''s one projection, one row per active property that is served (rule 25); browse_list is rebuilt '
  'from it every 15 minutes. Its place (lat, lng, display_label, granularity_rank, uncertainty_radius_m, '
  'the five RUIAN codes), the MF reference rent and yield and the served rule read the property''s '
  'LOCATION AD, properties.location_listing_ref_id (migration 636); listing_id, source_id_native and every '
  'other column are the property''s own or its CANONICAL ad''s (repr_listing_ref_id).';

-- ---------------------------------------------------------------------------
-- 2. properties_public: 617's body; the same join key, and location_listing_ref_id appended last.
-- ---------------------------------------------------------------------------
create or replace view properties_public as
  select
      p.id as property_id,
      p.repr_listing_id as sreality_id,
      p.first_seen_at,
      p.last_seen_at,
      p.is_active,
      p.category_main,
      p.category_type,
      p.current_price_czk as price_czk,
      l.price_unit,
      p.area_m2,
      p.disposition,
      st_y(ll.geom) as lat,
      st_x(ll.geom) as lng,
      l.floor,
      l.total_floors,
      p.has_balcony,
      p.has_parking,
      p.has_lift,
      p.building_type,
      p.condition,
      p.energy_rating,
      p.estate_area,
      p.usable_area,
      p.garden_area,
      p.category_sub_cb,
      p.furnished,
      p.terrace,
      p.cellar,
      p.garage,
      p.parking_lots,
      p.ownership,
      l.broker_name,
      null::text as broker_email,
      null::text as broker_phone,
      measure_tom_days(p.is_active, p.first_seen_at, p.last_seen_at) as tom_days,
      measure_price_per_m2(p.current_price_czk::numeric, p.area_m2, p.category_main, p.category_type) as price_per_m2,
      p.building_condition_level,
      p.apartment_condition_level,
      l.description,
      p.source_count,
      p.price_drop_count,
      p.price_rise_count,
      p.max_price_drop_pct,
      p.stats_computed_at,
      p.source,
      mf.mf_reference_rent_czk,
      mf.mf_gross_yield_pct,
      ll.obec_name as obec,
      p.home_obec_pop,
      p.near_pop_5km,
      p.near_pop_15km,
      p.near_jobs_5km,
      p.near_jobs_15km,
      p.near_youth_5km,
      p.near_youth_15km,
      p.near_overall_5km,
      p.near_overall_15km,
      p.subtype,
      p.last_change_at,
      ll.obec_kod  as obec_id,
      ll.okres_kod as okres_id,
      ll.kraj_kod  as region_id,
      p.price_change_count,
      p.price_change_count_30d,
      p.price_change_count_90d,
      p.price_change_count_365d,
      p.total_price_change_pct,
      mf.mf_reference_rent,
      p.repr_listing_ref_id as listing_id,
      l.source_id_native,
      measure_price_per_m2_basis(p.category_main, p.category_type) as price_per_m2_basis,
      location_display_label(ll.street_name, ll.house_number_cp, ll.house_number_co,
                             ll.obec_name, ll.cast_obce_name, ll.country_code,
                             ll.country_status) as display_label,
      ll.cast_obce_kod as cast_obce_id,
      ll.ulice_kod as ulice_id,
      coalesce(p.all_sources, '{}'::text[]) as all_sources,
      coalesce(p.active_sources, '{}'::text[]) as active_sources,
      p.location_listing_ref_id
  from properties p
       left join listings l on l.id = p.repr_listing_ref_id
       left join listing_location ll on ll.listing_id = p.location_listing_ref_id
       left join lateral public.mf_reference(
           p.category_main, p.category_type, p.disposition, p.area_m2, p.current_price_czk,
           p.condition, p.has_balcony, p.terrace, p.furnished, p.garage, p.has_lift,
           p.building_type, ll.obec_kod, ll.katastr_kod, ll.country_status) mf on true
  where p.status = 'active'::text;

comment on view public.properties_public is
  'One row per active property: the watchdog''s relation and the property page''s. Its place (lat, lng, '
  'obec, display_label, the five RUIAN codes) and the MF reference rent read the property''s LOCATION AD, '
  'location_listing_ref_id, the last column (migration 636); listing_id and the ad facts (price_unit, '
  'floor, total_floors, broker_name, description, source_id_native) are its CANONICAL ad''s.';

-- ---------------------------------------------------------------------------
-- 3. recompute_city_proximity: 508's body at the location ad, its UPDATE guarded against a moved one.
-- ---------------------------------------------------------------------------
create or replace function recompute_city_proximity(p_full boolean default false)
returns integer
language plpgsql
as $$
declare
  n integer;
begin
  -- Anchor set: obce big enough to satisfy the smallest pop threshold the UI
  -- offers (>=10000), plus the 206 curated cities that carry the qualitative
  -- indexes (latest revision). One GiST-indexed temp set keeps the per-property
  -- spatial probe over a few hundred rows instead of all ~6 250 obce.
  drop table if exists _prox_anchors;
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
  'for the next run. Run by recompute_city_proximity.yml and after a population / city-index load.';

-- ---------------------------------------------------------------------------
-- 4. The CHECK, NOT VALID, last in the transaction (validated in 7.).
-- ---------------------------------------------------------------------------
do $check$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.properties'::regclass
                    and conname = 'properties_location_ad_present') then
    alter table public.properties add constraint properties_location_ad_present
      check ((repr_listing_ref_id is null) = (location_listing_ref_id is null)) not valid;
  end if;
end
$check$;

-- ---------------------------------------------------------------------------
-- 5. Post-conditions of the transaction.
-- ---------------------------------------------------------------------------
do $post$
declare
  v_pre jsonb := current_setting('one_pin636.pre')::jsonb;
  v_bp_def text := pg_get_viewdef('public.browse_projection'::regclass);
  v_pp_def text := pg_get_viewdef('public.properties_public'::regclass);
  v_pp_cols text;
  v_check text;
  v_ads bigint;
  v_lat double precision;
  v_lng double precision;
  v_rank integer;
  v_label text;
  v_listing bigint;
  v_want_lat double precision;
  v_want_lng double precision;
  v_want_rank integer;
  v_want_label text;
begin
  if md5(v_bp_def) is distinct from '9fb6da98b0d4d171079c51328e77a279' then
    raise exception '636 did not land: browse_projection is not this file''s body (md5 %)', md5(v_bp_def);
  end if;
  if md5(v_pp_def) is distinct from '18efda1aeb70ff4414780381969e5372' then
    raise exception '636 did not land: properties_public is not this file''s body (md5 %)', md5(v_pp_def);
  end if;
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'recompute_city_proximity'), '')
     is distinct from '84947f47472f7dd1e15a0a1865ebc700' then
    raise exception '636 did not land: recompute_city_proximity is not this file''s body (or has two overloads)';
  end if;

  if (select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum)
        from pg_attribute a
       where a.attrelid = 'public.browse_projection'::regclass and a.attnum > 0 and not a.attisdropped)
     is distinct from v_pre ->> 'bp_cols' then
    raise exception '636 did not land: browse_projection''s columns changed (browse_list and browse_list_visible are typed on them)';
  end if;
  select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum)
    into v_pp_cols
    from pg_attribute a
   where a.attrelid = 'public.properties_public'::regclass and a.attnum > 0 and not a.attisdropped;
  if v_pp_cols is distinct from
       case when v_pre ->> 'pp_cols' like '%, location_listing_ref_id bigint' then v_pre ->> 'pp_cols'
            else (v_pre ->> 'pp_cols') || ', location_listing_ref_id bigint' end then
    raise exception '636 did not land: properties_public is not its old columns plus location_listing_ref_id bigint, last';
  end if;

  if (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
        from pg_class c where c.oid = 'public.browse_projection'::regclass) is distinct from v_pre ->> 'bp_acl'
     or (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
           from pg_class c where c.oid = 'public.properties_public'::regclass) is distinct from v_pre ->> 'pp_acl'
     or (select l.lanname::text || ' ' || p.provolatile::text || ' ' || p.prosecdef::text || ' '
                || coalesce(p.proconfig::text, '-') || ' ' || coalesce(p.proacl::text, '-') || ' '
                || pg_get_function_arguments(p.oid) || ' -> ' || pg_get_function_result(p.oid)
           from pg_proc p join pg_language l on l.oid = p.prolang
          where p.oid = to_regprocedure('public.recompute_city_proximity(boolean)')) is distinct from v_pre ->> 'fn' then
    raise exception '636 did not land: an ACL, a view option or the function''s posture changed';
  end if;

  if position('ll.listing_id = p.location_listing_ref_id' in v_bp_def) = 0
     or position('ll.listing_id = p.repr_listing_ref_id' in v_bp_def) > 0
     or position('ll.listing_id = p.location_listing_ref_id' in v_pp_def) = 0
     or position('ll.listing_id = p.repr_listing_ref_id' in v_pp_def) > 0
     or position('l.id = p.repr_listing_ref_id' in v_pp_def) = 0 then
    raise exception '636 did not land: a view does not read listing_location at the location ad (or properties_public lost its canonical ad join)';
  end if;

  select pg_get_constraintdef(k.oid) into v_check
    from pg_constraint k
   where k.conrelid = 'public.properties'::regclass and k.conname = 'properties_location_ad_present';
  if v_check is null
     or regexp_replace(v_check, ' NOT VALID$', '')
        <> 'CHECK (((repr_listing_ref_id IS NULL) = (location_listing_ref_id IS NULL)))' then
    raise exception '636 did not land: properties_location_ad_present reads "%"', coalesce(v_check, 'missing');
  end if;

  select count(*) into v_ads from public.listings where property_id = 15100;
  if v_ads = 0 then
    raise notice '636: smoke skipped, property 15100 has no ads here (the CI replay)';
  else
    select b.lat, b.lng, b.granularity_rank, b.display_label, b.listing_id
      into v_lat, v_lng, v_rank, v_label, v_listing
      from public.browse_projection b
     where b.property_id = 15100;
    select st_y(ll.geom), st_x(ll.geom), gr.rank::int,
           location_display_label(ll.street_name, ll.house_number_cp, ll.house_number_co,
                                  ll.obec_name, ll.cast_obce_name, ll.country_code,
                                  ll.country_status)
      into v_want_lat, v_want_lng, v_want_rank, v_want_label
      from public.properties p
      join public.listing_location ll on ll.listing_id = p.location_listing_ref_id
      left join public.location_granularity_rank gr on gr.granularity = ll.granularity
     where p.id = 15100;
    if (v_lat, v_lng, v_rank, v_label) is distinct from (v_want_lat, v_want_lng, v_want_rank, v_want_label) then
      raise exception '636 did not land: browse_projection places property 15100 at (%, %), rank %, "%"; its location ad is at (%, %), rank %, "%"',
        v_lat, v_lng, v_rank, v_label, v_want_lat, v_want_lng, v_want_rank, v_want_label;
    end if;
    if v_listing is distinct from (select p.repr_listing_ref_id from public.properties p where p.id = 15100) then
      raise exception '636 did not land: browse_projection.listing_id of 15100 (%) is not its canonical ad', v_listing;
    end if;
    raise notice '636: property 15100 at (%, %), rank %, "%"; listing_id % (the canonical ad)',
      v_lat, v_lng, v_rank, v_label, v_listing;
  end if;
end
$post$;

notify pgrst, 'reload schema';

commit;

-- ---------------------------------------------------------------------------
-- 6. The one-time proximity reset, after the commit (the header says why).
-- ---------------------------------------------------------------------------
update public.properties
   set city_proximity_computed_at = null
 where status = 'active'
   and location_listing_ref_id <> repr_listing_ref_id
   and city_proximity_computed_at is not null;

-- ---------------------------------------------------------------------------
-- 7. VALIDATE: its own statement, SHARE UPDATE EXCLUSIVE (reads and writes continue).
-- ---------------------------------------------------------------------------
set statement_timeout = '900s';

alter table public.properties validate constraint properties_location_ad_present;

set statement_timeout = '120s';

do $post_check$
begin
  if not coalesce((select k.convalidated from pg_constraint k
                    where k.conrelid = 'public.properties'::regclass
                      and k.conname = 'properties_location_ad_present'), false) then
    raise exception '636 did not land: properties_location_ad_present is not validated; re-run the file';
  end if;
end
$post_check$;

reset statement_timeout;
reset lock_timeout;
