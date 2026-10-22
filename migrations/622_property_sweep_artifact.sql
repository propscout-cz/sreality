-- 622_property_sweep_artifact.sql -- the daily property sweep becomes a derived artifact the jobs check judges.
--
-- WHY (docs/design/health-dashboard.md, wave 4): the full sweep's completion was an app_settings row,
--   property_sweep_last_complete, read by one harness check (property_maintenance: warn 52 h, fail 56 h). Wave 4
--   retires that check, and the sweep stamps a derived_artifacts row instead (Corollary E, migrations 437/440/441),
--   so the jobs check's artifact cell judges it like every other produced artifact. Nothing reads the app_settings
--   row after this wave.
--
-- WHAT CHANGES. Additive only.
--   1. derived_artifacts gains property_sweep: producer scripts/recompute_property_stats.py, which stamps it through
--      scraper.db's stamp helper inside the transaction that clears the swept dirt and deletes the resume cursor
--      (a complete cycle only: a budget stop stamps nothing); host github-actions; cadence '15 4 * * *', the
--      recompute_property_stats.yml cron. The budget is 38 h because the jobs check adds GitHub's 18 h delivery slack
--      to every github-actions artifact: the cell fails past 56 h, today's fail line, sized for a cycle split across
--      two daily runs. An artifact cell has no Watch tier, so today's 52 h warn is gone. complete_through is the
--      stamp time (the helper's rule), not the cycle's start.
--   2. The row is seeded from app_settings.property_sweep_last_complete (its completed_at as both last_succeeded_at
--      and complete_through), so the cell reads on rather than Not measured until the first cycle after the deploy.
--      The seed never moves the row backwards. The CI replay has no such app_settings row: there it is a no-op.
--   Nothing is dropped and no grant changes; the app_settings row stays, unread, for wave 9.
--
-- APPLY through apply_migration.yml (--ref the feature branch) immediately before merging wave 4: the stamp helper
-- is a silent no-op on an unregistered name, and migration_drift cannot see an insert-only file. Until the merge
-- deploys, main's sweep still writes only the app_settings row; if a daily sweep completes in that window, re-apply
-- this file and statement 2 moves the seed forward to it.
-- ROLLBACK: forward only (rule 1). Every statement is idempotent; a lock-timeout retry re-runs the whole file.

set lock_timeout = '5s';

begin;

insert into public.derived_artifacts (name, producer, host, cadence, staleness_budget, is_serving)
values ('property_sweep', 'scripts/recompute_property_stats.py', 'github-actions', '15 4 * * *',
        interval '38 hours', true)
on conflict (name) do nothing;

update public.derived_artifacts d
   set last_succeeded_at = (s.value->>'completed_at')::timestamptz,
       complete_through = (s.value->>'completed_at')::timestamptz
  from public.app_settings s
 where s.key = 'property_sweep_last_complete'
   and d.name = 'property_sweep'
   and coalesce(d.last_succeeded_at, '-infinity') < (s.value->>'completed_at')::timestamptz;

do $post$
begin
  if not exists (select 1 from public.derived_artifacts
                  where name = 'property_sweep'
                    and producer = 'scripts/recompute_property_stats.py'
                    and host = 'github-actions'
                    and cadence = '15 4 * * *'
                    and staleness_budget = interval '38 hours'
                    and is_serving) then
    raise exception '622: the property_sweep artifact is missing or not as declared';
  end if;
  if not (select c.relrowsecurity from pg_class c where c.oid = 'public.derived_artifacts'::regclass)
     or exists (select 1 from unnest(array['anon', 'authenticated']::name[]) as r(role)
                 where has_any_column_privilege(r.role, 'public.derived_artifacts',
                                                'SELECT, INSERT, UPDATE, REFERENCES')
                    or has_table_privilege(r.role, 'public.derived_artifacts', 'DELETE, TRUNCATE, TRIGGER')) then
    raise exception '622: derived_artifacts must stay RLS-on and closed to browser roles';
  end if;
end
$post$;

commit;
