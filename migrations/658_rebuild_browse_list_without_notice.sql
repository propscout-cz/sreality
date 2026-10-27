-- 658_rebuild_browse_list_without_notice.sql -- rebuild_browse_list() less its dead `drop table if exists
-- browse_list_next`: the previous run renamed that table, so the drop only raises a NOTICE, which flushes the
-- result early and blocks pg_cron's single launcher for the whole ~68 s rebuild, four times an hour.

do $pre$
begin
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
                 where p.pronamespace = 'public'::regnamespace and p.proname = 'rebuild_browse_list'), '')
       not in ('7c0103f3faedef16a2dfd3cfbf2168a0', '5eadd1b0e2e9ff48654ac1d8e892a0cf') then
    raise exception '658 refused: rebuild_browse_list() is not 649''s body nor this file''s, or has two overloads';
  end if;
end
$pre$;

-- 649's body, less the one statement.
-- ci-allow-ungated: rebuild_browse_list it reads property_merge_events only to re-patch browse_list_next; execute revoked from public, anon and authenticated (605's grants, asserted in 649's $post$)
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

do $post$
declare
  v_src text := (select prosrc from pg_proc where oid = to_regprocedure('public.rebuild_browse_list()'));
begin
  if v_src like '%drop table if exists browse_list_next%' or md5(v_src) <> '5eadd1b0e2e9ff48654ac1d8e892a0cf' then
    raise exception '658 did not land: rebuild_browse_list() still drops browse_list_next, or differs from this file';
  end if;
  if has_function_privilege('anon', 'public.rebuild_browse_list()', 'execute')
     or has_function_privilege('authenticated', 'public.rebuild_browse_list()', 'execute') then
    raise exception '658 did not land: rebuild_browse_list() is callable by a browser role';
  end if;
end
$post$;
