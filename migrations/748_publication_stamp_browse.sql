-- 748_publication_stamp_browse.sql -- Browse reads the publication stamp alone
-- (operator ruling 2026-11-17; toolkit/publication.py; migration 747 stamped every property the clock had
-- published, and the maintenance pass now stamps every publication).
--
-- WHAT. ONE WHERE arm of browse_projection changes, pinned verbatim to toolkit.publication.PUBLISHED_PREDICATE
-- by tests/test_668_publication_gate.py, the latest definition held to it by
-- tests/test_browse_read_path_guardrail.py (migration 749 does the same for the location scorecard):
--       and coalesce(p.settled_at, p.first_seen_at + interval '6 hours') <= now()   -- before (669)
--       and p.settled_at IS NOT NULL                                                -- after
-- So a property the engine has not read yet is absent from Browse, the map and stats as it is from alerts,
-- and no surface reads the clock.
--
-- RECIPE (584 / 625 / 649 / 665 / 669): browse_projection restated whole from 669's body; no column changes,
-- so browse_list's shape is untouched. Under the list rebuild's advisory key (held to the commit). CREATE OR
-- REPLACE keeps the view's ACL and reloptions. No forced rebuild: the next hourly rebuild applies the rule.
--
-- APPLY ORDER -- AFTER the deploy, as 669 was: merge the PR, confirm the realtime-worker rollout
-- (scripts/rollout_wait.sh), THEN apply this file through apply_migration.yml (--ref main), never the MCP.
-- Why: under the OLD code only a settled property is stamped, so with these views every other new property
-- would stay out of Browse until the new stamper runs. With the new code live first, alerts already read
-- the stamp and this file only extends that to Browse; until it lands Browse still shows a waiting property
-- at its 6 h mark, as before. Not in a list rebuild's minutes (:00-:02: the advisory wait hits the lock
-- limit). Every statement is idempotent. ROLLBACK: forward only (rule 1).
-- Verify, after the next rebuild:
--   select count(*) from browse_list b join properties p on p.id = b.property_id where p.settled_at is null;  -- 0

set lock_timeout = '5s';
set statement_timeout = '120s';

do $pre$
declare
  v_left bigint;
begin
  select count(*) into v_left from public.properties
   where settled_at is null and first_seen_at <= now() - interval '7 days';
  if v_left > 0 then
    raise exception '748 refused: % properties older than 7 d carry no stamp (apply 747 first)', v_left;
  end if;
end
$pre$;

begin;

do $lock$
begin
  perform pg_advisory_xact_lock(hashtext('rebuild_browse_list'));
  perform set_config('gate748.pre', jsonb_build_object(
      'bp_acl', (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
                   from pg_class c where c.oid = 'public.browse_projection'::regclass),
      'cols', (select count(*) from pg_attribute a
                where a.attrelid = 'public.browse_projection'::regclass and a.attnum > 0 and not a.attisdropped))::text,
    true);
end
$lock$;

-- 669's body (the live definition), the published rule reading the stamp alone.
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
        limit 1) as tom_observed,
      p.newest_ad_at_web,
      ll.street_name collate "cs-CZ-x-icu" as street_name
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
    -- THE PUBLICATION RULE (rule 16, operator ruling 2026-10-23): settled by the pipeline, or CAP old.
    and p.settled_at IS NOT NULL
  ;

do $post$
declare
  v_def text := pg_get_viewdef('public.browse_projection'::regclass);
  v_pre jsonb := current_setting('gate748.pre')::jsonb;
begin
  -- pg_get_viewdef re-spells the WHERE (casts, parentheses), so each rule is checked by its tokens.
  if v_def not like '%settled_at IS NOT NULL%' or v_def like '%first_seen_at +%' then
    raise exception '748: browse_projection does not read the publication stamp alone';
  end if;
  if v_def not like '%ll.geom IS NOT NULL%' or v_def not like '%ll.country_status%foreign%' then
    raise exception '748: browse_projection lost the consumer rule (rule 25)';
  end if;
  if (select count(*) from pg_attribute a
       where a.attrelid = 'public.browse_projection'::regclass and a.attnum > 0 and not a.attisdropped)::text
     <> (v_pre->>'cols') then
    raise exception '748: browse_projection changed shape (WHERE-only restatement expected)';
  end if;
  if (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
        from pg_class c where c.oid = 'public.browse_projection'::regclass) <> (v_pre->>'bp_acl') then
    raise exception '748: browse_projection ACL or reloptions changed';
  end if;
end
$post$;

commit;

reset statement_timeout;
reset lock_timeout;
