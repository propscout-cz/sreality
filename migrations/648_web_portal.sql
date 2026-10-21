-- 648_web_portal.sql -- NEW PROPERTY FROM A LINK, W3 (docs/design/new-property-from-link.md §3.4): the
-- tenth portal, `web` -- any other site, one pasted advert at a time. ADDITIVE: one row, two nullable
-- columns and two bodies restated with one line each; nothing is dropped, renamed or rewritten.
--
-- 1. The `portals` row (the per-portal config row of rule 21; migration 100's registry): kind
--    'scraper', enabled, supports_complete_walk (its walk is INDEXLESS and always reaches its end; the
--    adapter's own presence_candidates nominates its rechecks), a daily cadence, sorted last, no home
--    URL (no one site). `categories` NULL: scraper.portal._DEFAULTS holds its one agenda-wide
--    descriptor. `operational_limits` pins what the global layer (app_settings scraper_limits_global:
--    detail_workers 4, detail_rate 2.0) would otherwise lay over the baked default: two detail workers
--    at one fetch a second (web_client paces each HOST to one page per 10 s on top), and the shared
--    rate limiter, so the drain's 120 s deadline binds inside a chunk.
-- 2. properties.newest_ad_at_web (MS19, 588's shape): nullable, no default, catalog-only. The rollup
--    (scripts/recompute_property_stats.py) splices one date per PORTAL_OPTIONS code with no fallback.
-- 3. browse_list gains the column FIRST, then browse_projection is restated with p.newest_ad_at_web
--    appended LAST (584's recipe; 625 did the same for tom_observed): sync_browse_list's positional
--    insert and browse_list_visible()'s `l.*` must never see the table narrower than the view. The
--    body is 636's, which IS the live definition (md5(pg_get_viewdef) 9fb6da98b0d4d171079c51328e77a279,
--    read 2026-10-21). CREATE OR REPLACE keeps the view's ACL, reloptions and comment.
-- 4. rebuild_browse_list(): 630's body, which IS the live one (md5(prosrc)
--    606aa0ed24602b63e362f71e134f2198, read 2026-10-21), with the tenth partial index of 590's
--    pattern, built on browse_list_next and renamed with its nine siblings. CREATE OR REPLACE keeps its
--    ACL (service_role only) and posture (SECURITY DEFINER, search_path public).
-- No forced rebuild: no web advert exists (0 rows on 2026-10-21), so the column is NULL on every row
-- the next */15 tick copies, and that tick builds the index.
--
-- TWO TRANSACTIONS, in this order, because one would hold properties and browse_list together: the
-- rollup holds properties and then patches browse_list (sync_browse_list), so a single transaction
-- taking both is a deadlock with it. TX1 (the row and the properties column) takes one lock under
-- 588's 6 s limit; TX2 takes the list rebuild's advisory key (631/636's idiom: queued under the lock
-- limit, held to the commit, so a tick that fires meanwhile skips and no rebuild can swap in a table
-- built from the narrower view), then browse_list FIRST (sync_browse_list's own order), the view,
-- and the function. A failure between them leaves TX1's additions, which are inert; a re-run
-- completes TX2.
-- GATES. $pre$ (before TX1) and TX2's own gate refuse unless each body this file restates is the one
-- it was written against or this file's own (a re-run: the view already projects newest_ad_at_web /
-- the rebuild already builds its index). $post$ proves, in TX2: the column on all three relations;
-- browse_projection's and browse_list's column lists equal and ending in newest_ad_at_web; both
-- relations' ACLs and the view's reloptions as they were; the rebuild's posture and ACL; both web
-- index lines in its body; the portals row.
--
-- APPLY through apply_migration.yml (--ref the W3 branch) once the PR is green, IMMEDIATELY BEFORE it
-- merges (the rollup writes newest_ad_at_web with no fallback), never through the MCP (it wraps the
-- file in one transaction). Not in a list rebuild's minutes (:00-:02, :15-:17, :30-:32, :45-:47: the
-- advisory wait hits the lock limit). Every statement is idempotent; a re-run is a no-op.
-- ROLLBACK: forward only (rule 1); none needed. `update portals set is_enabled = false where source =
-- 'web'` stops the walk; the column and the index are inert without web adverts.
-- Verify:
--   select source, kind, is_enabled, scrape_cadence_minutes, operational_limits from portals
--    where source = 'web';                                                         -- one row
--   select count(*) from pg_attribute where attrelid = 'public.properties'::regclass
--      and attname like 'newest\_ad\_at\_%' and not attisdropped;                   -- 10

set lock_timeout = '6s';
set statement_timeout = '120s';

-- ---------------------------------------------------------------------------
-- 0. Preconditions, before any lock: the bodies this file restates.
-- ---------------------------------------------------------------------------
do $pre$
begin
  if md5(pg_get_viewdef('public.browse_projection'::regclass)) <> '9fb6da98b0d4d171079c51328e77a279'
     and not exists (select 1 from pg_attribute
                      where attrelid = 'public.browse_projection'::regclass
                        and attname = 'newest_ad_at_web' and not attisdropped) then
    raise exception '648 refused: browse_projection is not 636''s body nor this file''s (md5 %)',
      md5(pg_get_viewdef('public.browse_projection'::regclass));
  end if;
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
                 where p.pronamespace = 'public'::regnamespace and p.proname = 'rebuild_browse_list'), '')
       <> '606aa0ed24602b63e362f71e134f2198'
     and coalesce((select p.prosrc from pg_proc p
                    where p.oid = to_regprocedure('public.rebuild_browse_list()')), '')
         not like '%browse_list_next_newest_ad_at_web_idx%' then
    raise exception '648 refused: rebuild_browse_list() is not 630''s body nor this file''s, or has two overloads';
  end if;
end
$pre$;

-- ---------------------------------------------------------------------------
-- 1. TX1 -- the portals row and the property's tenth date.
-- ---------------------------------------------------------------------------
begin;

insert into public.portals
  (source, label, kind, home_url, sort_order, is_enabled, supports_complete_walk, categories,
   split_threshold, scrape_cadence_minutes, operational_limits, operational_limits_updated_by)
values
  ('web', 'Web', 'scraper', null, 50, true, true, null,
   null, 1440, '{"detail_workers": 2, "detail_rate": 1.0, "shared_rate_limiter": true}'::jsonb, 'migration_648_web_portal')
on conflict (source) do nothing;

alter table public.properties
  add column if not exists newest_ad_at_web timestamptz;

comment on column public.properties.newest_ad_at_web is 'MS19 (migration 648): first sighting of this property''s newest web ad (any other site, a pasted advert), active or not; NULL with none. Written by the rollup.';

commit;

-- ---------------------------------------------------------------------------
-- 2. TX2 -- the rebuild key, the table, the projection, the rebuild.
-- ---------------------------------------------------------------------------
set lock_timeout = '5s';

begin;

do $lock$
begin
  perform pg_advisory_xact_lock(hashtext('rebuild_browse_list'));
  if not exists (select 1 from pg_attribute
                  where attrelid = 'public.properties'::regclass
                    and attname = 'newest_ad_at_web' and not attisdropped) then
    raise exception '648 refused: properties.newest_ad_at_web is missing (TX1 did not commit)';
  end if;
  perform set_config('web648.pre', jsonb_build_object(
      'bp_acl', (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
                   from pg_class c where c.oid = 'public.browse_projection'::regclass),
      'bl_acl', (select coalesce(c.relacl::text, '-')
                   from pg_class c where c.oid = 'public.browse_list'::regclass),
      'fn', (select p.prosecdef::text || ' ' || coalesce(p.proconfig::text, '-') || ' '
                    || coalesce(p.proacl::text, '-')
               from pg_proc p where p.oid = to_regprocedure('public.rebuild_browse_list()')))::text,
    true);
end
$lock$;

-- browse_list FIRST: the positional sync_browse_list insert and browse_list_visible()'s `l.*` read
-- the table against the view's shape, so the table is never narrower than the view.
alter table public.browse_list add column if not exists newest_ad_at_web timestamptz;

-- 636's body (the live definition); p.newest_ad_at_web appended LAST.
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
      p.newest_ad_at_web
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

-- 630's body (the live definition); the tenth portal's partial index built and renamed with the nine.
-- ci-allow-ungated: rebuild_browse_list it reads property_merge_events only to re-patch browse_list_next; execute revoked from public, anon and authenticated (605's grants, asserted in $post$)
create or replace function public.rebuild_browse_list()
returns void
language plpgsql
security definer
set search_path to 'public'
as $function$
declare
  t0 timestamptz := clock_timestamp();
  n  bigint;
  v_since timestamptz;
  v_ids   bigint[];
  v_gone  bigint;
  v_back  bigint;
begin
  -- Transaction-scoped: released by commit, rollback OR cancel (W13).
  if not pg_try_advisory_xact_lock(hashtext('rebuild_browse_list')) then
    raise notice 'rebuild_browse_list: previous run still active, skipping tick';
    return;
  end if;

  -- The re-patch window: the start of the previous run less a margin (a skipped tick only widens it).
  select coalesce(max(complete_through), now()) - interval '15 minutes' into v_since
    from derived_artifacts where name = 'browse_list';

  execute 'drop table if exists browse_list_next';
  -- The map index is cheap only on a heap in its own order (2026-10-19); NULL lat/lng sort last.
  execute $q$
    create unlogged table browse_list_next as
    select * from browse_projection
    order by category_main, category_type, lat, lng
  $q$;
  execute 'create unique index browse_list_next_pk on browse_list_next (property_id)';
  execute 'create index browse_list_next_cat_first_seen_idx on browse_list_next (category_main, category_type, first_seen_at desc, property_id desc)';
  execute 'create index browse_list_next_obec_price_idx on browse_list_next (obec_id, category_type, price_czk, property_id, category_main, subtype, disposition, area_m2, is_active) where obec_id is not null';
  execute 'create index browse_list_next_okres_price_idx on browse_list_next (okres_id, category_type, price_czk, property_id, category_main, subtype, disposition, area_m2, is_active) where okres_id is not null';
  execute 'create index browse_list_next_region_price_idx on browse_list_next (region_id, category_type, price_czk, property_id, category_main, subtype, disposition, area_m2, is_active) where region_id is not null';
  execute 'create index browse_list_next_ulice_idx on browse_list_next (ulice_id) where ulice_id is not null';
  execute 'create index browse_list_next_newest_ad_at_sreality_idx on browse_list_next (category_main, category_type, newest_ad_at_sreality desc, property_id desc) where newest_ad_at_sreality is not null';
  execute 'create index browse_list_next_newest_ad_at_bazos_idx on browse_list_next (category_main, category_type, newest_ad_at_bazos desc, property_id desc) where newest_ad_at_bazos is not null';
  execute 'create index browse_list_next_newest_ad_at_idnes_idx on browse_list_next (category_main, category_type, newest_ad_at_idnes desc, property_id desc) where newest_ad_at_idnes is not null';
  execute 'create index browse_list_next_newest_ad_at_maxima_idx on browse_list_next (category_main, category_type, newest_ad_at_maxima desc, property_id desc) where newest_ad_at_maxima is not null';
  execute 'create index browse_list_next_newest_ad_at_ceskereality_idx on browse_list_next (category_main, category_type, newest_ad_at_ceskereality desc, property_id desc) where newest_ad_at_ceskereality is not null';
  execute 'create index browse_list_next_newest_ad_at_bezrealitky_idx on browse_list_next (category_main, category_type, newest_ad_at_bezrealitky desc, property_id desc) where newest_ad_at_bezrealitky is not null';
  execute 'create index browse_list_next_newest_ad_at_mmreality_idx on browse_list_next (category_main, category_type, newest_ad_at_mmreality desc, property_id desc) where newest_ad_at_mmreality is not null';
  execute 'create index browse_list_next_newest_ad_at_remax_idx on browse_list_next (category_main, category_type, newest_ad_at_remax desc, property_id desc) where newest_ad_at_remax is not null';
  execute 'create index browse_list_next_newest_ad_at_realitymix_idx on browse_list_next (category_main, category_type, newest_ad_at_realitymix desc, property_id desc) where newest_ad_at_realitymix is not null';
  execute 'create index browse_list_next_newest_ad_at_web_idx on browse_list_next (category_main, category_type, newest_ad_at_web desc, property_id desc) where newest_ad_at_web is not null';
  execute 'create index browse_list_next_map_idx on browse_list_next (category_main, category_type, lat, lng) include (property_id, is_active, price_czk, disposition, subtype, area_m2, last_seen_at, first_seen_at) where lat is not null and lng is not null';
  execute 'analyze browse_list_next';
  execute 'select count(*) from browse_list_next' into n;

  execute 'drop table if exists browse_list';
  -- The DROP holds the browse_list lock, so every change that patched the old table has committed
  -- and every later one queues for the new one. Re-patch every property an identity change named
  -- since v_since: survivor, retired, previous, and where the moved ad sits now.
  select coalesce(array_agg(distinct x.pid), '{}') into v_ids
    from property_merge_events e
    cross join lateral (values (e.survivor_property_id), (e.retired_property_id), (e.prev_property_id),
                               ((select a.property_id from listings a where a.id = e.listing_ref_id))) as x(pid)
   where greatest(e.created_at, e.undone_at) >= v_since and x.pid is not null;
  if cardinality(v_ids) > 0 then
    execute 'delete from browse_list_next where property_id = any($1)' using v_ids;
    get diagnostics v_gone = row_count;
    execute 'insert into browse_list_next select * from browse_projection where property_id = any($1)' using v_ids;
    get diagnostics v_back = row_count;
    n := n - v_gone + v_back;
  end if;
  execute 'alter table browse_list_next rename to browse_list';
  execute 'alter index browse_list_next_pk rename to browse_list_pk';
  execute 'alter index browse_list_next_cat_first_seen_idx rename to browse_list_cat_first_seen_idx';
  execute 'alter index browse_list_next_obec_price_idx rename to browse_list_obec_price_idx';
  execute 'alter index browse_list_next_okres_price_idx rename to browse_list_okres_price_idx';
  execute 'alter index browse_list_next_region_price_idx rename to browse_list_region_price_idx';
  execute 'alter index browse_list_next_ulice_idx rename to browse_list_ulice_idx';
  execute 'alter index browse_list_next_newest_ad_at_sreality_idx rename to browse_list_newest_ad_at_sreality_idx';
  execute 'alter index browse_list_next_newest_ad_at_bazos_idx rename to browse_list_newest_ad_at_bazos_idx';
  execute 'alter index browse_list_next_newest_ad_at_idnes_idx rename to browse_list_newest_ad_at_idnes_idx';
  execute 'alter index browse_list_next_newest_ad_at_maxima_idx rename to browse_list_newest_ad_at_maxima_idx';
  execute 'alter index browse_list_next_newest_ad_at_ceskereality_idx rename to browse_list_newest_ad_at_ceskereality_idx';
  execute 'alter index browse_list_next_newest_ad_at_bezrealitky_idx rename to browse_list_newest_ad_at_bezrealitky_idx';
  execute 'alter index browse_list_next_newest_ad_at_mmreality_idx rename to browse_list_newest_ad_at_mmreality_idx';
  execute 'alter index browse_list_next_newest_ad_at_remax_idx rename to browse_list_newest_ad_at_remax_idx';
  execute 'alter index browse_list_next_newest_ad_at_realitymix_idx rename to browse_list_newest_ad_at_realitymix_idx';
  execute 'alter index browse_list_next_newest_ad_at_web_idx rename to browse_list_newest_ad_at_web_idx';
  execute 'alter index browse_list_next_map_idx rename to browse_list_map_idx';
  execute 'grant select on browse_list to authenticated';
  execute 'revoke insert, update, delete, truncate on browse_list from anon, authenticated';

  if has_table_privilege('anon', 'browse_list', 'SELECT') then
    raise exception 'rebuild_browse_list: anon must never hold SELECT on browse_list -- refusing to publish this rebuild (see migration 374)';
  end if;

  update derived_artifacts
     set last_succeeded_at = now(),
         complete_through  = now(),
         last_duration_ms  = (extract(epoch from clock_timestamp() - t0) * 1000)::integer,
         last_rows         = n
   where name = 'browse_list';
  perform pg_notify('pgrst', 'reload schema');
end
$function$;

-- ---------------------------------------------------------------------------
-- 3. Post-conditions (the shape holds on the empty replay schema too).
-- ---------------------------------------------------------------------------
do $post$
declare
  v_pre jsonb := current_setting('web648.pre')::jsonb;
  v_rebuild oid := to_regprocedure('public.rebuild_browse_list()');
  rel text;
  v_bp text;
  v_bl text;
begin
  foreach rel in array array['public.properties', 'public.browse_list', 'public.browse_projection'] loop
    if not exists (select 1 from pg_attribute
                    where attrelid = rel::regclass and attname = 'newest_ad_at_web' and not attisdropped) then
      raise exception '648 did not land: % lacks newest_ad_at_web', rel;
    end if;
  end loop;
  select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum)
    into v_bp from pg_attribute a
   where a.attrelid = 'public.browse_projection'::regclass and a.attnum > 0 and not a.attisdropped;
  select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum)
    into v_bl from pg_attribute a
   where a.attrelid = 'public.browse_list'::regclass and a.attnum > 0 and not a.attisdropped;
  if v_bp is distinct from v_bl or v_bp not like '%, newest_ad_at_web timestamp with time zone' then
    raise exception '648 did not land: browse_list and browse_projection differ, or the date is not last';
  end if;
  if (select coalesce(c.relacl::text, '-') || ' ' || coalesce(c.reloptions::text, '-')
        from pg_class c where c.oid = 'public.browse_projection'::regclass) is distinct from v_pre ->> 'bp_acl'
     or (select coalesce(c.relacl::text, '-')
           from pg_class c where c.oid = 'public.browse_list'::regclass) is distinct from v_pre ->> 'bl_acl'
     or (select p.prosecdef::text || ' ' || coalesce(p.proconfig::text, '-') || ' '
                || coalesce(p.proacl::text, '-')
           from pg_proc p where p.oid = v_rebuild) is distinct from v_pre ->> 'fn' then
    raise exception '648 did not land: an ACL, a reloption or the rebuild''s posture changed';
  end if;
  if not (select prosecdef from pg_proc where oid = v_rebuild)
     or has_function_privilege('authenticated', v_rebuild, 'execute')
     or has_function_privilege('anon', v_rebuild, 'execute') then
    raise exception '648 did not land: rebuild_browse_list() is callable by a browser role';
  end if;
  if (select prosrc from pg_proc where oid = v_rebuild)
       not like '%create index browse_list_next_newest_ad_at_web_idx on browse_list_next (category_main, category_type, newest_ad_at_web desc, property_id desc) where newest_ad_at_web is not null%'
     or (select prosrc from pg_proc where oid = v_rebuild)
       not like '%alter index browse_list_next_newest_ad_at_web_idx rename to browse_list_newest_ad_at_web_idx%' then
    raise exception '648 did not land: rebuild_browse_list() does not build the web index';
  end if;
  if not exists (select 1 from public.portals
                  where source = 'web' and kind = 'scraper' and supports_complete_walk) then
    raise exception '648 did not land: no web scraper row in portals';
  end if;
end
$post$;

commit;

notify pgrst, 'reload schema';

reset statement_timeout;
reset lock_timeout;
