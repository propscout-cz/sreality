-- 665_browse_street_sort.sql -- Browse sorts by STREET NAME (operator request 2026-10-23: "in the browse view, allow
-- sorting by street name"). ADDITIVE: one column on browse_list and browse_projection; nothing dropped or rewritten.
--
-- WHAT. browse_projection gains `street_name` -- the location ad's resolved street (listing_location.street_name, the
-- same value display_label opens with) -- appended LAST, under the Czech ICU collation, so ORDER BY and the keyset
-- cursor's `gt`/`lt` both compare the way a Czech reader expects (Č after C, Ch after H; the database default en-US
-- folds Č into C and Ch into C). The rebuild's `create table as select * from browse_projection` carries the column's
-- collation onto browse_list_next (checked live 2026-10-23 on a temp CTAS).
-- RULE 25: a new location field on browse_list needs an operator ruling; the request above is that ruling. It is a
-- SORT KEY, never a display (every display stays location_display_label) and never a filter (the street filter stays
-- `ulice_id = any(codes)`). 131,410 of 348,021 active properties carry a street (2026-10-23); the rest sort last both
-- ways (nullsFirst:false, the nullable keyset shape).
-- NO INDEX: EXPLAIN ANALYZE live 2026-10-23, the top-N heapsort under this collation took ~14 ms over 67k active
-- flats for sale and ~55 ms over all 348k active properties.
--
-- RECIPE (584 / 625 / 649): browse_list gains the column FIRST, then browse_projection is restated with it appended
-- LAST -- sync_browse_list's positional insert and browse_list_visible()'s `l.*` must never see the table narrower than
-- the view. Under the list rebuild's advisory key (held to the commit), so no rebuild can swap in a table built from
-- the narrower view. The body is 649's, which IS the live definition (md5(pg_get_viewdef) a35da3bde09152af531d8390cf16cac0,
-- read 2026-10-23 and matched against a temp view of 649's text). CREATE OR REPLACE keeps the view's ACL and reloptions.
-- No forced rebuild: the column reads NULL until the next */15 rebuild (or a sync of that property) fills it.
--
-- APPLY through apply_migration.yml (--ref the feature branch) BEFORE the SPA code merges (its sort names the column),
-- never through the MCP (it wraps the file in one transaction). Not in a list rebuild's minutes (:00-:02, :15-:17,
-- :30-:32, :45-:47: the advisory wait hits the lock limit). Every statement is idempotent; a re-run is a no-op.
-- ROLLBACK: forward only (rule 1); none needed -- the column is inert without the SPA sort.
-- Verify:
--   select count(*) filter (where street_name is not null), count(*) from browse_list where is_active;

set lock_timeout = '5s';
set statement_timeout = '120s';

-- ---------------------------------------------------------------------------
-- 0. Precondition, before any lock: the body this file restates is the live one, or this file's (a re-run).
-- ---------------------------------------------------------------------------
do $pre$
begin
  if md5(pg_get_viewdef('public.browse_projection'::regclass))
       not in ('a35da3bde09152af531d8390cf16cac0', 'a7a3e59d2cc97cc2c6bc6a50eab17dda') then
    raise exception '665 refused: browse_projection is not 649''s body nor this file''s (md5 %)',
      md5(pg_get_viewdef('public.browse_projection'::regclass));
  end if;
end
$pre$;

begin;

do $lock$
begin
  perform pg_advisory_xact_lock(hashtext('rebuild_browse_list'));
  perform set_config('street665.pre', jsonb_build_object(
      'bp_acl', (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
                   from pg_class c where c.oid = 'public.browse_projection'::regclass),
      'bl_acl', (select coalesce(c.relacl::text, '-')
                   from pg_class c where c.oid = 'public.browse_list'::regclass))::text,
    true);
end
$lock$;

-- browse_list FIRST: the positional sync_browse_list insert and browse_list_visible()'s `l.*` read the table against
-- the view's shape, so the table is never narrower than the view.
alter table public.browse_list add column if not exists street_name text collate "cs-CZ-x-icu";

-- 649's body (the live definition); ll.street_name appended LAST.
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
  ;

-- ---------------------------------------------------------------------------
-- Post-conditions (the shape holds on the empty replay schema too).
-- ---------------------------------------------------------------------------
do $post$
declare
  v_pre jsonb := current_setting('street665.pre')::jsonb;
  rel text;
  v_bp text;
  v_bl text;
begin
  foreach rel in array array['public.browse_list', 'public.browse_projection'] loop
    if not exists (select 1 from pg_attribute a join pg_collation c on c.oid = a.attcollation
                    where a.attrelid = rel::regclass and a.attname = 'street_name' and not a.attisdropped
                      and c.collname = 'cs-CZ-x-icu') then
      raise exception '665 did not land: % lacks street_name under the Czech collation', rel;
    end if;
  end loop;
  select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum)
    into v_bp from pg_attribute a
   where a.attrelid = 'public.browse_projection'::regclass and a.attnum > 0 and not a.attisdropped;
  select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum)
    into v_bl from pg_attribute a
   where a.attrelid = 'public.browse_list'::regclass and a.attnum > 0 and not a.attisdropped;
  if v_bp is distinct from v_bl or v_bp not like '%, newest_ad_at_web timestamp with time zone, street_name text' then
    raise exception '665 did not land: browse_list and browse_projection differ, or street_name is not last';
  end if;
  if (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
        from pg_class c where c.oid = 'public.browse_projection'::regclass) is distinct from v_pre ->> 'bp_acl'
     or (select coalesce(c.relacl::text, '-')
           from pg_class c where c.oid = 'public.browse_list'::regclass) is distinct from v_pre ->> 'bl_acl' then
    raise exception '665 did not land: an ACL or a reloption changed';
  end if;
end
$post$;

commit;

notify pgrst, 'reload schema';

reset statement_timeout;
reset lock_timeout;
