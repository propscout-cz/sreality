-- 669_publication_gate_view.sql -- browse_projection serves a property only once it is PUBLISHED: settled
-- by the pipeline (properties.settled_at, migration 668) or 6 h old (operator ruling 2026-10-23, Q1: the gate
-- governs Browse, the map and stats, not alerts alone; Q2: the cap is 6 h; Q4: one cap everywhere).
--
-- WHAT. ONE WHERE arm, the published rule, rendered from toolkit/publication.py and pinned verbatim by
-- tests/test_668_publication_gate.py (the latest-definition rail, as the consumer rule is pinned):
--       and coalesce(p.settled_at, p.first_seen_at + interval '6 hours') <= now()
-- The watchdog producers render the same rule on properties_public (api/notifications._build_match_clauses,
-- rule 16: Watchdog and Browse share one definition of "matches"); the publishing health check judges its
-- 'unpublished' stage by it. Detail-by-id surfaces (properties_public: the property page, the extension,
-- the boards, push rendering) stay ungated, as rule 25 keeps them for the location rule.
--
-- RECIPE (584 / 625 / 649 / 665): browse_projection restated whole from 665's body with the arm appended to
-- the WHERE; no column changes, so browse_list's shape is untouched and no table step precedes it. Under the
-- list rebuild's advisory key (held to the commit). CREATE OR REPLACE keeps the view's ACL and reloptions.
-- No forced rebuild: the next */15 rebuild applies the rule -- a property the cap releases enters
-- browse_list on the rebuild after its 6 h mark (mean ~9 min, worst measured ~40 min); a settled one is
-- patched in at once by the maintenance pass's properties_changed.
--
-- APPLY ORDER -- the REVERSE of the usual additive order, on purpose: merge the PR, confirm Railway's
-- realtime-worker rollout (gh api .../commits/<sha>/status), THEN apply this file through apply_migration.yml
-- (--ref main), never the MCP (it wraps the file in one transaction). Why: the code that ships with this
-- file needs nothing it creates, but the OLD code still running during a rollout would read the gated view
-- wrongly -- the publishing health check had no 'unpublished' stage and would count every held property as
-- a 'not yet in Browse' blocker on six portals, open ops_incidents and page the operator. With the new code
-- live first, the hold is already in force for alerts and the stamper is stamping; this file only extends
-- it to Browse. Not in a list rebuild's minutes (:00-:02, :15-:17, :30-:32, :45-:47: the advisory wait hits
-- the lock limit). Every statement is idempotent; a re-run is a no-op. ROLLBACK: forward only (rule 1).
-- Verify, after the next */15 rebuild:
--   select count(*) from browse_list b join properties p on p.id = b.property_id
--     where p.settled_at is null and p.first_seen_at > now() - interval '6 hours';  -- 0

set lock_timeout = '5s';
set statement_timeout = '120s';

-- ---------------------------------------------------------------------------
-- 0. Precondition, before any lock: the body this file restates is 665's (the live one), or already carries
--    the rule (a re-run). 668 must have landed (the column the rule names).
-- ---------------------------------------------------------------------------
do $pre$
declare
  v_md5 text := md5(pg_get_viewdef('public.browse_projection'::regclass));
  v_has_rule boolean := pg_get_viewdef('public.browse_projection'::regclass) like '%settled_at%';
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'properties' and column_name = 'settled_at') then
    raise exception '669 refused: properties.settled_at is missing (apply 668 first)';
  end if;
  if v_md5 <> 'a7a3e59d2cc97cc2c6bc6a50eab17dda' and not v_has_rule then
    raise exception '669 refused: browse_projection is not 665''s body nor this file''s (md5 %)', v_md5;
  end if;
end
$pre$;

begin;

do $lock$
begin
  perform pg_advisory_xact_lock(hashtext('rebuild_browse_list'));
  perform set_config('gate669.pre', jsonb_build_object(
      'bp_acl', (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
                   from pg_class c where c.oid = 'public.browse_projection'::regclass),
      'cols', (select count(*) from pg_attribute a
                where a.attrelid = 'public.browse_projection'::regclass and a.attnum > 0 and not a.attisdropped))::text,
    true);
end
$lock$;

-- 665's body (the live definition), the published rule appended to the WHERE.
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
    and coalesce(p.settled_at, p.first_seen_at + interval '6 hours') <= now()
  ;

-- ---------------------------------------------------------------------------
-- Post-conditions (they hold on the empty replay schema too).
-- ---------------------------------------------------------------------------
do $post$
declare
  v_pre jsonb := current_setting('gate669.pre')::jsonb;
  v_def text := pg_get_viewdef('public.browse_projection'::regclass);
begin
  if v_def not like '%settled_at%' then
    raise exception '669: browse_projection does not carry the published rule';
  end if;
  if v_def not like '%(ll.geom IS NOT NULL OR ll.country_status = ''foreign'')%' then
    raise exception '669: browse_projection lost the consumer rule (rule 25)';
  end if;
  if (select count(*) from pg_attribute a
       where a.attrelid = 'public.browse_projection'::regclass and a.attnum > 0 and not a.attisdropped)::text
     <> (v_pre->>'cols') then
    raise exception '669: browse_projection changed shape (WHERE-only restatement expected)';
  end if;
  if (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
        from pg_class c where c.oid = 'public.browse_projection'::regclass) <> (v_pre->>'bp_acl') then
    raise exception '669: browse_projection ACL or reloptions changed';
  end if;
end
$post$;

commit;
