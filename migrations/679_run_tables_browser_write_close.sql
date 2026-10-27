-- 679_run_tables_browser_write_close.sql -- the browser can no longer insert or rewrite run rows (agent platform M0, #2058).
--
-- Migration 291 granted INSERT and UPDATE on estimation_runs and building_runs to `authenticated` table-wide,
-- so a signed-in browser could INSERT its own runs through PostgREST (skipping the API's metering gate and
-- rule 12's create_estimation_run) and UPDATE any column of its own runs (rewriting an immutable result).
-- Every run write goes through the API on the service-role connection; the ONE browser-role write is the
-- yield scenario PATCH (`PATCH /estimations/{id}/scenario`, tenant_conn), so `scenario` is the one column
-- `authenticated` may UPDATE (651's shape for notification_dispatches.seen_at). The insert policies and
-- building_runs' update policy guarded grants that no longer exist; they are dropped so a future re-grant
-- fails closed instead of reviving them. Revoking a table-level privilege also revokes its column-level twins,
-- so the order below is the whole posture.
--
-- Safe in either order against the deployed code: no browser or extension code writes either table, and the
-- scenario PATCH keeps its column. No data changes.
-- ROLLBACK: forward only (rule 1). Every statement is idempotent; a lock-timeout retry re-runs the file. The
-- previous state is 291's grants and policy text.

set lock_timeout = '5s';

begin;

revoke insert, update on public.estimation_runs from authenticated;
revoke insert, update on public.building_runs   from authenticated;
grant update (scenario) on public.estimation_runs to authenticated;

drop policy if exists estimation_runs_tenant_insert on public.estimation_runs;
drop policy if exists building_runs_tenant_insert   on public.building_runs;
drop policy if exists building_runs_tenant_update   on public.building_runs;

commit;

do $post$
declare
  col text;
begin
  if has_table_privilege('authenticated', 'public.estimation_runs', 'INSERT')
     or has_table_privilege('authenticated', 'public.building_runs', 'INSERT')
     or has_table_privilege('authenticated', 'public.building_runs', 'UPDATE')
     or not has_column_privilege('authenticated', 'public.estimation_runs', 'scenario', 'UPDATE') then
    raise exception '679 did not land: authenticated must UPDATE estimation_runs.scenario and write nothing else on the run tables';
  end if;
  for col in select attname from pg_attribute
              where attrelid = 'public.estimation_runs'::regclass and attnum > 0 and not attisdropped
                and attname <> 'scenario' loop
    if has_column_privilege('authenticated', 'public.estimation_runs', col, 'UPDATE') then
      raise exception '679 did not land: authenticated may still UPDATE estimation_runs.%', col;
    end if;
  end loop;
  if exists (select 1 from pg_policies
              where schemaname = 'public' and tablename in ('estimation_runs', 'building_runs')
                and cmd = 'INSERT') then
    raise exception '679 did not land: a run-table INSERT policy survives';
  end if;
end
$post$;

reset lock_timeout;
