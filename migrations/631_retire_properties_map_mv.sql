-- 631_retire_properties_map_mv.sql -- the map's matview retires: pins and tile counts read browse_list
-- (ONE PIN sprint, W2; docs/design/one-pin/PROGRAM.md "W2", rulings R2, R10 and R11).
--
-- ===========================================================================
-- DESTRUCTIVE, with the operator's OK (2026-10-19): R2 retires the matview and accepts browse_list's
-- UNLOGGED bound; R11 drops the two dead MF functions under the same OK. NO BACKUP (R10): everything
-- below is a derived object, rebuilt from migration history, and no stored data is lost. The restore
-- is migrations/reverts/631_revert_retire_properties_map_mv.sql, which recreates each object from the
-- source this file's $pre$ holds the live one to (the four bodies by md5, byte for byte).
-- ===========================================================================
--
-- WHY EACH IS DEAD SINCE 630 (W1, #1887; applied, merged and its SPA live 2026-10-19):
--   properties_map_mv            browse_projection's rows with a point, rebuilt by pg_cron at :07/:37 (~61 s,
--                                ~22 GB of WAL a day, R2) and never patched, so after a merge the map stayed
--                                stale for up to 31 min. Since 630, browse_map_cells counts its tiles from
--                                browse_list, and W1's SPA reads its pins from browse_list: nothing reads it.
--   properties_map_visible(...)  629's pin source over the matview (10 defaulted parameters). W1's SPA stopped
--                                calling it; no view or function calls it (live, 2026-10-19).
--   rebuild_properties_map_mv()  522's blue-green rebuild of the matview, and the pg_cron job
--   + job browse-map-rebuild     browse-map-rebuild (7,37 * * * *) that calls it: they rebuild nothing else.
--   derived_artifacts row        440's freshness row for that rebuild; left behind, it would read stale forever.
--   recompute_mf_gross_yields()  507:524 and 507:380, the last bodies of the MF writers (133, 257; R11). No pg_cron job,
--   recompute_property_mf(bigint[])  no workflow since 8b0327b2 (2026-09-26) deleted recompute_mf_yields.yml, no
--                                Python, script, check or database caller (live, 2026-10-19). Browse's MF rent and
--                                yield come from browse_projection's mf lateral (625); the on-demand reference-rent
--                                path is untouched; the stored properties.mf_* columns stay with PR-F.
--
-- WHAT RUNS, in this order, in ONE transaction (lock_timeout 5 s, statement_timeout 120 s):
--   0. $pre$, 618's guard. It takes the map rebuild's advisory key first (522's hashtext key), so no tick is
--      mid-swap and a tick that fires before commit skips. Then it refuses while anything outside this list
--      still names or depends on what goes:
--        * a view or matview definition (pg_views, pg_matviews), or a function body in any non-catalog schema
--          (pg_proc.prosrc; pg_depend does not track a plpgsql or sql body), naming properties_map_mv,
--          properties_map_visible, recompute_property_mf or recompute_mf_gross_yields, other than the four
--          functions this file drops;
--        * a pg_depend entry on the matview or its row type other than its own indexes and its internal
--          entries (row type, _RETURN rule, TOAST table, array type), or any entry on the four functions;
--        * a pg_cron job other than browse-map-rebuild calling any of them.
--      It also refuses unless each object is the one the revert restores: the four bodies by md5(prosrc)
--      (read live 2026-10-19, each equal to its file: 629, 522, 507, 507), the matview's two indexes (522), the
--      job's schedule and command (522) and the registry row's declaration (440).
--      It REPORTS, never refuses on, the job's cron.job_run_details rows that never finished. Live 2026-10-19:
--      one row reads 'connecting' with a NULL start time, a run pg_cron never launched. The unschedule removes
--      the job either way; the row stays as pg_cron history, which no Health cell reads again (the jobs and
--      capacity cells join cron.job, scripts/checks/platform.py).
--   1. cron.unschedule('browse-map-rebuild'), only if the job exists (the CI replay has no pg_cron).
--   2. DROP properties_map_visible by 629's exact signature, then rebuild_properties_map_mv(), then the matview
--      (properties_map_mv_pk and properties_map_mv_cover go with it), then DELETE its derived_artifacts row.
--   3. DROP recompute_mf_gross_yields() (the caller), then recompute_property_mf(bigint[]).
--   4. $post$, 630's idiom: the matview, the four functions (any overload), the job and the row are gone;
--      browse_map_cells is one overload whose body reads browse_list and names no map object, and one call
--      answers; the pin source browse_list_visible stands. Then notify pgrst (delivered at commit).
-- No CASCADE: the guard proves nothing else depends, and a plain DROP refuses otherwise.
--
-- APPLY through apply_migration.yml (--ref the W2 branch) once the PR is green, any time after W1's SPA bundle
-- is live (it is), so no new page load calls a dropped function; since 630 no code on main reads what goes.
-- Prefer just before the merge, so the PR, the apply and the deploy land together. 630's file stays
-- re-runnable after this one (its $pre$ gates browse_map_cells' body, which this file leaves alone). Not at
-- :07-:09 or :37-:39: a map rebuild holds the advisory key for ~61 s, the wait hits the 5 s lock limit and the
-- workflow's lock-timeout retry re-runs the file. Not in a list swap minute (:01-:02, :16-:17, :31-:32,
-- :46-:47): $post$ calls browse_map_cells, which reads browse_list under the same limit.
-- OPEN TABS: a tab loaded before W1's deploy and never reloaded still calls properties_map_visible. Its pins
-- vanish (PostgREST answers 404) until it reloads; its list does not. Named in PROGRAM.md W2, accepted.
-- HEALTH: the cron/browse-map-rebuild cell disappears on its own (its cells come from live cron.job,
-- scripts/checks/platform.py), and the registry's map row goes with this file's DELETE.
-- A re-run is a no-op: every guard accepts an absent object and every DROP is IF EXISTS.
-- ROLLBACK: migrations/reverts/631_revert_retire_properties_map_mv.sql (derived objects only, no data).

set lock_timeout = '5s';
set statement_timeout = '120s';

begin;

-- ---------------------------------------------------------------------------
-- 0. Preconditions, before any DROP.
-- ---------------------------------------------------------------------------
do $pre$
declare
  v_mv oid := to_regclass('public.properties_map_mv');
  v_visible oid := to_regprocedure('public.properties_map_visible(boolean, text, bigint[], bigint[], text, bigint[], bigint[], text, bigint[], bigint[])');
  v_rebuild oid := to_regprocedure('public.rebuild_properties_map_mv()');
  v_yields oid := to_regprocedure('public.recompute_mf_gross_yields()');
  v_property_mf oid := to_regprocedure('public.recompute_property_mf(bigint[])');
  v_dropped oid[];
  v_bad text;
  v_stray text;
begin
  -- The rebuild's own key, queued under the 5 s lock limit: held until commit.
  perform pg_advisory_xact_lock(hashtext('rebuild_properties_map_mv'));

  v_dropped := array_remove(array[v_visible, v_rebuild, v_yields, v_property_mf], null);

  -- Each object is the one the revert restores (an absent one passes: a re-run).
  select string_agg(x, '; ') into v_bad from (
    select 'properties_map_visible(...) is not 629''s body' as x
     where coalesce((select md5(prosrc) from pg_proc where oid = v_visible),
                    '9c1351bbc86f7c1c7a0ec4a7d08db1df') <> '9c1351bbc86f7c1c7a0ec4a7d08db1df'
    union all
    select 'rebuild_properties_map_mv() is not 522''s body'
     where coalesce((select md5(prosrc) from pg_proc where oid = v_rebuild),
                    'e18ea5161c8af9ceab5b07a62b6dc37d') <> 'e18ea5161c8af9ceab5b07a62b6dc37d'
    union all
    select 'recompute_mf_gross_yields() is not 507''s body'
     where coalesce((select md5(prosrc) from pg_proc where oid = v_yields),
                    '6c031b2362ed463369361c8320af30a0') <> '6c031b2362ed463369361c8320af30a0'
    union all
    select 'recompute_property_mf(bigint[]) is not 507''s body'
     where coalesce((select md5(prosrc) from pg_proc where oid = v_property_mf),
                    '1ab31fca5867b88494692e322cac2d7c') <> '1ab31fca5867b88494692e322cac2d7c'
    union all
    select 'properties_map_mv does not carry exactly 522''s two indexes'
     where v_mv is not null
       and (select array_agg(c.relname::text order by c.relname)
              from pg_index i join pg_class c on c.oid = i.indexrelid
             where i.indrelid = v_mv)
           is distinct from array['properties_map_mv_cover', 'properties_map_mv_pk']
    union all
    select 'the properties_map_mv registry row is not 440''s declaration'
     where exists (select 1 from public.derived_artifacts d
                    where d.name = 'properties_map_mv'
                      and (d.producer, d.host, d.cadence, d.staleness_budget, d.is_serving)
                          is distinct from ('rebuild_properties_map_mv', 'pg_cron', '7,37 * * * *',
                                            interval '90 minutes', true))
  ) mismatch;
  if v_bad is not null then
    raise exception '631 refused: % (the revert would not restore it as it is live)', v_bad;
  end if;

  select string_agg(x, ', ' order by x) into v_bad from (
    -- a view or matview naming what goes
    select format('view %I.%I', v.schemaname, v.viewname) as x
      from pg_views v
     where v.schemaname not in ('pg_catalog', 'information_schema')
       and v.definition ~ '(properties_map_mv|properties_map_visible|recompute_property_mf|recompute_mf_gross_yields)'
    union all
    select format('materialized view %I.%I', m.schemaname, m.matviewname)
      from pg_matviews m
     where m.definition ~ '(properties_map_mv|properties_map_visible|recompute_property_mf|recompute_mf_gross_yields)'
    union all
    -- a function body naming what goes, other than the four this file drops
    select format('function %s', p.oid::regprocedure)
      from pg_proc p
     where p.pronamespace not in ('pg_catalog'::regnamespace, 'information_schema'::regnamespace)
       and p.prosrc ~ '(properties_map_mv|properties_map_visible|recompute_property_mf|recompute_mf_gross_yields)'
       and p.oid <> all (v_dropped)
    union all
    -- anything on the matview or its row type but its own indexes and its internal entries; anything on the
    -- four functions
    select pg_describe_object(d.classid, d.objid, d.objsubid)
      from pg_depend d
     where d.deptype <> 'i'
       and ((d.refclassid = 'pg_class'::regclass and d.refobjid = v_mv
             and not (d.classid = 'pg_class'::regclass
                      and d.objid in (select i.indexrelid from pg_index i where i.indrelid = v_mv)))
         or (d.refclassid = 'pg_type'::regclass
             and d.refobjid = (select c.reltype from pg_class c where c.oid = v_mv))
         or (d.refclassid = 'pg_proc'::regclass and d.refobjid = any (v_dropped)))
  ) deps;
  if v_bad is not null then
    raise exception '631 refused: % still name(s) or depend(s) on what this file drops', v_bad;
  end if;

  if to_regclass('cron.job') is null then
    raise notice '631: no pg_cron here (the CI replay); the job checks are skipped';
    return;
  end if;
  select string_agg(j.jobname, ', ' order by j.jobname) into v_bad
    from cron.job j
   where j.jobname <> 'browse-map-rebuild'
     and j.command ~ '(properties_map|recompute_property_mf|recompute_mf_gross_yields)';
  if v_bad is not null then
    raise exception '631 refused: pg_cron job(s) % call what this file drops', v_bad;
  end if;
  if exists (select 1 from cron.job j
              where j.jobname = 'browse-map-rebuild'
                and (j.schedule, j.command) is distinct from
                    ('7,37 * * * *', $c$set statement_timeout='1500s'; select public.rebuild_properties_map_mv();$c$)) then
    raise exception '631 refused: browse-map-rebuild is not 522''s schedule and command (the revert restores 522''s)';
  end if;

  select string_agg(format('run %s %s, start %s', r.runid, r.status, coalesce(r.start_time::text, 'NULL')),
                    '; ' order by r.runid)
    into v_stray
    from cron.job_run_details r
   where r.jobid in (select j.jobid from cron.job j where j.jobname = 'browse-map-rebuild')
     and r.status in ('starting', 'connecting', 'sending', 'running');
  if v_stray is not null then
    raise notice '631: browse-map-rebuild run(s) that never finished: % (this file holds the rebuild''s key, so none is a live rebuild). The unschedule removes the job either way; the rows stay as pg_cron history', v_stray;
  end if;
end
$pre$;

-- ---------------------------------------------------------------------------
-- 1. The job first, so no tick ever fires against a dropped function. Guarded: the CI replay has no pg_cron.
-- ---------------------------------------------------------------------------
do $cron$
begin
  if to_regclass('cron.job') is null then
    raise notice '631: no pg_cron here; nothing to unschedule';
  elsif exists (select 1 from cron.job where jobname = 'browse-map-rebuild') then
    perform cron.unschedule('browse-map-rebuild');
  end if;
end
$cron$;

-- ---------------------------------------------------------------------------
-- 2. The pin source, the rebuild, the matview (its two indexes go with it), its registry row.
-- ---------------------------------------------------------------------------
drop function if exists public.properties_map_visible(boolean, text, bigint[], bigint[], text, bigint[], bigint[], text, bigint[], bigint[]);
drop function if exists public.rebuild_properties_map_mv();
drop materialized view if exists public.properties_map_mv;
delete from public.derived_artifacts where name = 'properties_map_mv';

-- ---------------------------------------------------------------------------
-- 3. The two dead MF functions (R11), the caller first.
-- ---------------------------------------------------------------------------
drop function if exists public.recompute_mf_gross_yields();
drop function if exists public.recompute_property_mf(bigint[]);

-- ---------------------------------------------------------------------------
-- 4. Post-conditions: everything above is gone, and the map's two reads survive on browse_list.
-- ---------------------------------------------------------------------------
do $post$
declare
  v_left text;
begin
  if to_regclass('public.properties_map_mv') is not null then
    raise exception '631 did not land: properties_map_mv is still there';
  end if;
  select string_agg(p.oid::regprocedure::text, ', ') into v_left
    from pg_proc p
   where p.pronamespace = 'public'::regnamespace
     and p.proname in ('properties_map_visible', 'rebuild_properties_map_mv',
                       'recompute_mf_gross_yields', 'recompute_property_mf');
  if v_left is not null then
    raise exception '631 did not land: % still exist(s)', v_left;
  end if;
  if exists (select 1 from public.derived_artifacts where name = 'properties_map_mv') then
    raise exception '631 did not land: the properties_map_mv registry row is still there';
  end if;
  if to_regclass('cron.job') is null then
    raise notice '631: no pg_cron here; the unschedule is not asserted';
  elsif exists (select 1 from cron.job where jobname = 'browse-map-rebuild') then
    raise exception '631 did not land: browse-map-rebuild is still scheduled';
  end if;
  if (select count(*) from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_map_cells') <> 1
     or not exists (select 1 from pg_proc p
                     where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_map_cells'
                       and p.prosrc ~ '\mfrom\s+browse_list\M'
                       and p.prosrc !~ '(properties_map_mv|properties_map_visible)') then
    raise exception '631 did not land: browse_map_cells is not one overload reading browse_list';
  end if;
  if not exists (select 1 from pg_proc p
                  where p.pronamespace = 'public'::regnamespace and p.proname = 'browse_list_visible') then
    raise exception '631 did not land: browse_list_visible, the pin source, is missing';
  end if;
  perform public.browse_map_cells(category_main_filter => array['byt'], category_type_filter => 'prodej',
                                  bbox_west => 14.40, bbox_south => 50.05, bbox_east => 14.45,
                                  bbox_north => 50.10, point_budget => 0);
end
$post$;

notify pgrst, 'reload schema';

commit;

reset statement_timeout;
reset lock_timeout;
