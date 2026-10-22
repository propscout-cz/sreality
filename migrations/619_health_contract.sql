-- 619_health_contract.sql -- the health contract: four statuses, one check registry, cell incidents, readable system alerts.
--
-- WHY (docs/design/health-dashboard.md, wave 1):
--   1. one vocabulary: a check that was not measured is stored as `unknown`, never as `warn`, and never reads OK;
--   2. one registry: health_checks declares every check's class, host and cadence; the view serves its keys only;
--   3. per-cell incidents: ops_incidents gains the recovery hold's anchor and the operator's acknowledge;
--   4. platform admins can mark system alerts read (seen_at); reading never acknowledges an incident.
-- pipeline_check_history_public is untouched (wave 9 decides).
--
-- WHAT CHANGES. Additive only.
--   1. pipeline_check_results.status admits 'unknown' (a superset; every stored row validates). The CHECK is swapped
--      NOT VALID in its own short transaction and validated outside it, so the scan holds SHARE UPDATE EXCLUSIVE only.
--   2. health_checks: each host projects and prunes only its own rows (scripts/verify_pipeline.project_registry for
--      github-actions). RLS on, no policy, nothing granted to a browser role.
--   3. health_stale(stamp, budget), the one staleness predicate. No SET search_path, so the planner inlines it; the
--      view calls it as the CALLER and functions are born private since 612, hence the grant to authenticated.
--   4. ops_incidents.ok_since and acknowledged_at, both nullable.
--   5. notification_dispatches: system alerts carry the SYSTEM account since migration 364 (account_id is NOT NULL,
--      never NULL), and 292's tenant arm never reaches them. A second permissive UPDATE arm admits SYSTEM rows for a
--      platform admin (431's hoisted gate), and browser UPDATE narrows to the two columns the tenant routes write
--      (seen_at, estimation_run_id); merges run as the service role.
--   6. pipeline_checks_public: one row per registered key, 422's six columns first and unchanged, then the registry,
--      last_measured_at, effective_status and incidents (cell incidents open, or closed within 14 days). A key with
--      results but no registry row is no longer served (the frozen outbound_url_parity leaves the page).
--
-- APPLY through apply_migration.yml (--ref the feature branch) immediately before merging wave 1: until the first
-- registry projection the view serves no rows (the old page reads "No pipeline checks recorded yet."), so dispatch
-- one non-dry verify_pipeline.yml run once the merge has deployed. Never run this branch's verify_pipeline non-dry
-- before the merge: the old ladder on main reads unknown as healthy and would close incidents and re-send onsets.
-- After the deploy, expect one grouped onset per currently failing check (today three gauges: location_town_coverage,
-- ppm2_basis_floor_share, text_extraction_lag), each on the first reconcile pass after the new harness measures it.
-- ROLLBACK: forward only (rule 1). Every statement is idempotent; a lock-timeout retry re-runs the whole file.

set lock_timeout = '5s';

begin;
alter table public.pipeline_check_results
  drop constraint if exists pipeline_check_results_status_check;
alter table public.pipeline_check_results
  add constraint pipeline_check_results_status_check
  check (status in ('ok', 'warn', 'fail', 'unknown')) not valid;
commit;

alter table public.pipeline_check_results
  validate constraint pipeline_check_results_status_check;

begin;

create table if not exists public.health_checks (
  key              text        primary key,
  domain           text        not null,
  class            text        not null check (class in ('alarm', 'gauge')),
  host             text        not null,
  cadence_s        integer     not null check (cadence_s > 0),
  staleness_budget interval    not null,
  label            text        not null,
  unit             text        not null default '',
  owner_path       text        not null default '',
  sort             integer     not null default 0,
  declared_at      timestamptz not null default now()
);

alter table public.health_checks enable row level security;
revoke all on public.health_checks from anon, authenticated;

comment on table public.health_checks is
  'The health check registry (migration 619): one row per declared check with its page section (domain), class '
  '(alarm | gauge), host, cadence, staleness budget, label, unit, owner page and order. Each host projects and '
  'prunes only its own rows (scripts/verify_pipeline.project_registry for github-actions); service-role writes. '
  'The browser reads it only through pipeline_checks_public.';

create or replace function public.health_stale(stamp timestamptz, budget interval)
returns boolean
language sql
stable
as $$ select stamp is null or stamp < now() - budget $$;

comment on function public.health_stale(timestamptz, interval) is
  'The one staleness predicate (migration 619): true when stamp is NULL or older than budget. '
  'No SET search_path: a SET clause blocks inlining.';

revoke execute on function public.health_stale(timestamptz, interval) from public, anon;
grant execute on function public.health_stale(timestamptz, interval) to authenticated;

alter table public.ops_incidents
  add column if not exists ok_since timestamptz,
  add column if not exists acknowledged_at timestamptz;

comment on column public.ops_incidents.ok_since is
  'When the cell was first measured OK since it last failed or warned (toolkit/system_alerts.reconcile); NULL '
  'while it fails or warns. The recovery hold runs from here.';
comment on column public.ops_incidents.acknowledged_at is
  'When the operator acknowledged this incident on Health (POST /admin/health/acknowledge); it silences reminders. '
  'Reading a notice in the bell (notification_dispatches.seen_at) never sets it.';

revoke update on public.notification_dispatches from authenticated;
grant update (seen_at, estimation_run_id) on public.notification_dispatches to authenticated;

drop policy if exists notification_dispatches_admin_update on public.notification_dispatches;
create policy notification_dispatches_admin_update on public.notification_dispatches
  for update to authenticated
  using (account_id = '00000000-0000-0000-0000-000000000000'::uuid and (select is_platform_admin()))
  with check (account_id = '00000000-0000-0000-0000-000000000000'::uuid and (select is_platform_admin()));

-- starts_with, not LIKE: every check key holds `_`, which LIKE reads as a wildcard.
-- Rows older than the key's registration (declared_at) are not served: the page and the ladder share that rule, so a
-- verdict the old harness stored (an error as `fail`, no message) never shows as current after the cutover.
create or replace view public.pipeline_checks_public as
select * from (
  select hc.key as check_key,
         latest.run_at,
         latest.status,
         latest.value,
         latest.details,
         latest.created_at,
         hc.label,
         hc.unit,
         hc.domain,
         hc.class,
         hc.host,
         hc.cadence_s,
         hc.owner_path,
         hc.sort,
         hc.declared_at,
         measured.run_at as last_measured_at,
         case
           when latest.run_at is null or latest.status = 'unknown' then 'unknown'
           when health_stale(latest.run_at, hc.staleness_budget) then 'unknown'
           else latest.status
         end as effective_status,
         coalesce((
           select jsonb_agg(jsonb_build_object(
                    'id', i.id,
                    'subject', substr(i.signature, length('cell:' || hc.key || ':') + 1),
                    'first_seen_at', i.first_seen_at,
                    'last_seen_at', i.last_seen_at,
                    'resolved_at', i.resolved_at,
                    'acknowledged_at', i.acknowledged_at,
                    'notice_count', i.alert_count,
                    'last_notice_at', i.alerted_at)
                  order by i.first_seen_at)
             from ops_incidents i
            where starts_with(i.signature, 'cell:' || hc.key || ':')
              and (i.resolved_at is null or i.resolved_at > now() - interval '14 days')
         ), '[]'::jsonb) as incidents
    from health_checks hc
    left join lateral (
      select c.run_at, c.status, c.value, c.details, c.created_at
        from pipeline_check_results c
       where c.check_key = hc.key
         and c.run_at >= hc.declared_at
       order by c.run_at desc
       limit 1
    ) latest on true
    left join lateral (
      select c.run_at
        from pipeline_check_results c
       where c.check_key = hc.key
         and c.run_at >= hc.declared_at
         and c.status <> 'unknown'
       order by c.run_at desc
       limit 1
    ) measured on true
) __admin_gate
where is_platform_admin();

revoke all on public.pipeline_checks_public from anon;
grant select on public.pipeline_checks_public to authenticated;

do $$
begin
  if not (select relrowsecurity from pg_class where oid = 'public.health_checks'::regclass)
     or has_table_privilege('authenticated', 'public.health_checks', 'SELECT')
     or has_table_privilege('anon', 'public.health_checks', 'SELECT') then
    raise exception '619: health_checks must be RLS-on and unreadable by browser roles';
  end if;
  if (select array_agg(a.attname::text order by a.attname)
        from pg_attribute a
       where a.attrelid = 'public.notification_dispatches'::regclass and a.attnum > 0 and not a.attisdropped
         and has_column_privilege('authenticated', a.attrelid, a.attnum, 'UPDATE'))
     is distinct from array['estimation_run_id', 'seen_at'] then
    raise exception '619: authenticated may UPDATE only seen_at and estimation_run_id on notification_dispatches';
  end if;
  if not exists (select 1 from pg_policies
                  where schemaname = 'public' and tablename = 'notification_dispatches'
                    and policyname = 'notification_dispatches_admin_update'
                    and cmd = 'UPDATE' and permissive = 'PERMISSIVE'
                    and roles = array['authenticated']::name[]
                    and qual like '%00000000-0000-0000-0000-000000000000%'
                    and with_check like '%00000000-0000-0000-0000-000000000000%') then
    raise exception '619: notification_dispatches_admin_update is not FOR UPDATE TO authenticated on SYSTEM rows';
  end if;
  if not coalesce((select convalidated and pg_get_constraintdef(oid) like '%''unknown''%'
                     from pg_constraint
                    where conrelid = 'public.pipeline_check_results'::regclass
                      and conname = 'pipeline_check_results_status_check'), false) then
    raise exception '619: the status CHECK is not a validated superset admitting unknown';
  end if;
end $$;

commit;
