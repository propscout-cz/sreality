-- 675_scheduled_jobs_expand.sql -- Actions exit W2 (#2022): the location lanes' lease table becomes the platform's
-- scheduled-jobs row, the run history is born, and every worker heartbeat is its own liveness subject. EXPAND only.
--
-- WHY (docs/design/actions-exit/PROGRAM.md § 4.2, ADR 0001): the job runner claims due rows by compare-and-set, so
--   one table says when each job is due and who holds it. `location_jobs` (384) already is that lease row (holder,
--   expiry, last start/success, outcome, failures); it is renamed and generalised instead of built twice.
--
-- WHAT CHANGES. No data is lost; nothing the running deployment reads disappears.
--   * location_jobs -> scheduled_jobs (its primary key index follows the name). A TEMPORARY alias view
--     `location_jobs` (security invoker, 384's fourteen columns only) keeps the deployed acquire / upsert / release
--     text working between this apply and the merge; it is auto-updatable, so INSERT .. ON CONFLICT and
--     UPDATE .. RETURNING pass through it. It drops with the dead columns (R6b).
--   * New columns, all nullable so the lease-only resolver row needs none of them: argv, offset_s (the slot's offset
--     from the epoch-anchored `cadence` grid; 384's `cadence` IS the cadence, never a second one), timeout_s,
--     memory_mb, cpus, due_at, paused_until + pause_note + paused_by (a pause is finite and signed), requested_at +
--     requested_args + requested_by (a manual run is a row), registry_at, retired_at, resource (jobs that must never
--     overlap share one name; a held sibling makes a claim fail).
--   * last_outcome gains timeout, interrupted and lost. concurrency_group stops being NOT NULL: nothing writes it any
--     more, and with enabled, alert_route and note it drops later behind a backup receipt (R6b).
--   * job_runs: one row per run, closed in place, pruned after 90 days by the runner. A partial unique index allows
--     one open run per job, and a second one allows one open run per resource: the database, not the runner,
--     guarantees a single running copy.
--   * The seven rows no code names any more (their lanes went in earlier waves; one, payload_archive_prune, was
--     never enabled) get retired_at, so the enabled flag is read by nothing: the lease now honours retired_at and
--     paused_until instead. location_resolve_incremental is the one live row and is untouched.
--   * health_liveness() is re-created from 655's body (live and byte-identical on 2026-10-25) with one change: a
--     worker cell's subject is `worker/<name>`, not `worker`, so two workers (the job runner's replicas) are two
--     subjects, each with its own incident. No incident is open on `cell:liveness:worker` today; one opened later
--     recovers through the existing recovery arm.
--
-- ci-allow-ungated: health_liveness it reads the health ledgers and the worker heartbeat and writes one pipeline_check_results row; execute revoked from public, anon and authenticated
-- ci-allow-ungated: location_jobs a temporary alias for the service role's deployed lease text; security invoker over an RLS table, every privilege revoked from public, anon and authenticated
--
-- APPLY through apply_migration.yml (--ref the feature branch) BEFORE merging: the merged code reads scheduled_jobs.
-- Applied first, the deployed worker and location_resolve.yml keep their lease through the alias view.
-- ROLLBACK: forward only (rule 1). Every statement is idempotent; a lock-timeout retry re-runs the whole file.

set lock_timeout = '5s';

begin;

do $rename$
begin
  if to_regclass('public.scheduled_jobs') is null then
    alter table public.location_jobs rename to scheduled_jobs;
    alter index public.location_jobs_pkey rename to scheduled_jobs_pkey;
  end if;
end
$rename$;

alter table scheduled_jobs
  add column if not exists argv           text[],
  add column if not exists offset_s       integer,
  add column if not exists timeout_s      integer,
  add column if not exists memory_mb      integer,
  add column if not exists cpus           integer,
  add column if not exists due_at         timestamptz,
  add column if not exists paused_until   timestamptz,
  add column if not exists pause_note     text,
  add column if not exists paused_by      text,
  add column if not exists requested_at   timestamptz,
  add column if not exists requested_args text[],
  add column if not exists requested_by   text,
  add column if not exists registry_at    timestamptz,
  add column if not exists retired_at     timestamptz,
  add column if not exists resource       text;

alter table scheduled_jobs alter column concurrency_group drop not null;

alter table scheduled_jobs drop constraint if exists location_jobs_last_outcome_check;
alter table scheduled_jobs drop constraint if exists scheduled_jobs_last_outcome_check;
alter table scheduled_jobs add constraint scheduled_jobs_last_outcome_check check (last_outcome in
  ('ok', 'failed', 'cancelled', 'skipped', 'running', 'timeout', 'interrupted', 'lost'));
alter table scheduled_jobs drop constraint if exists scheduled_jobs_pause_is_finite_and_signed;
alter table scheduled_jobs add constraint scheduled_jobs_pause_is_finite_and_signed check (
  paused_until is null or (paused_until < 'infinity' and pause_note is not null and paused_by is not null));
alter table scheduled_jobs drop constraint if exists scheduled_jobs_request_is_signed;
alter table scheduled_jobs add constraint scheduled_jobs_request_is_signed check (
  requested_at is null or requested_by is not null);
alter table scheduled_jobs drop constraint if exists scheduled_jobs_declarations_positive;
alter table scheduled_jobs add constraint scheduled_jobs_declarations_positive check (
  offset_s >= 0 and timeout_s > 0 and memory_mb > 0 and cpus > 0);

comment on table scheduled_jobs is
  'One row per scheduled job or leased lane (migration 384 as location_jobs, generalised by 675): a lease-row CAS '
  '(lease_holder, lease_expires_at) claimed by scraper/job_lease.py, due on the epoch-anchored cadence grid plus '
  'offset_s. Retired rows stay. Dead since 675, dropped behind a backup receipt: enabled, concurrency_group, '
  'alert_route, note, and the location_jobs_stale index over enabled.';

update scheduled_jobs
   set retired_at = now()
 where retired_at is null
   and job_name in ('location_claims_llm', 'location_claims_remine', 'location_claims_remine_archive',
                    'location_payload_backfill', 'location_refetch_cohort', 'payload_archive_prune',
                    'pin_collision_recompute');

create or replace view location_jobs with (security_invoker = true) as
select job_name, cadence, concurrency_group, runner, enabled, lease_holder, lease_expires_at, last_started_at,
       last_success_at, last_outcome, last_error, consecutive_failures, alert_route, note
  from scheduled_jobs;
revoke all on location_jobs from public, anon, authenticated;
comment on view location_jobs is
  'TEMPORARY alias of scheduled_jobs (migration 675) for the deployment that predates the rename; drops with the '
  'dead lease columns.';

create table if not exists job_runs (
  id           bigint generated always as identity primary key,
  job_name     text not null references scheduled_jobs (job_name),
  holder       text not null,
  version      text,
  resource     text,
  args         text[],
  requested_by text,
  started_at   timestamptz not null default now(),
  ended_at     timestamptz,
  outcome      text check (outcome in ('ok', 'failed', 'cancelled', 'skipped', 'timeout', 'interrupted', 'lost')),
  exit_code    integer,
  peak_rss_mb  integer,
  log_tail     text,
  constraint job_runs_closed_with_an_outcome check ((ended_at is null) = (outcome is null))
);
create unique index if not exists job_runs_one_open_per_job on job_runs (job_name) where ended_at is null;
create unique index if not exists job_runs_one_open_per_resource on job_runs (resource)
  where ended_at is null and resource is not null;
create index if not exists job_runs_by_job on job_runs (job_name, started_at desc);
alter table job_runs enable row level security;
revoke all on job_runs from public, anon, authenticated;
comment on table job_runs is
  'One row per run of a scheduled job (migration 675), opened by the claim and closed in place by the release or, '
  'as lost, by the next claim after its lease expired. One open run per job and per resource, by unique index. '
  'Pruned after 90 days by the job runner.';

-- 655's function verbatim, but for the worker cell's subject.
create or replace function public.health_liveness()
returns void
language plpgsql
security definer
set search_path = public
as $fn$
declare
  -- The worker beats every 30 s and its watchdog exits at 300 s; a deploy gap is under 2 min.
  c_worker_dead constant interval := interval '10 minutes';
  -- The health lane's 500 s pass budget (wave 6) plus its 60 s tick and the heartbeat's 30 s lag stays under it.
  c_lane_dead constant interval := interval '10 minutes';
  -- A cell failing for two cadences has had every 60 s teller pass in between to open its incident.
  c_unalerted_cadences constant integer := 2;
  t0 timestamptz := clock_timestamp();
  v_cells jsonb;
  v_dead text[];
  v_age numeric;
  v_folded text[] := '{}';
  v_lane jsonb;
  v_boot timestamptz;
  v_lane_pass timestamptz;
  v_lane_since timestamptz;
  v_lane_failing timestamptz;
  v_lane_flight numeric;
  v_status text;
begin
  insert into health_checks as hc
         (key, domain, class, host, cadence_s, staleness_budget, label, unit, owner_path, sort)
  values ('liveness', 'platform', 'alarm', 'pg_cron', 300, interval '15 minutes', 'Watchers', 'min', '', 999)
  on conflict (key) do update
     set domain = excluded.domain, class = excluded.class, host = excluded.host,
         cadence_s = excluded.cadence_s, staleness_budget = excluded.staleness_budget,
         label = excluded.label, unit = excluded.unit, owner_path = excluded.owner_path, sort = excluded.sort
   where (hc.domain, hc.class, hc.host, hc.cadence_s, hc.staleness_budget, hc.label, hc.unit, hc.owner_path, hc.sort)
         is distinct from (excluded.domain, excluded.class, excluded.host, excluded.cadence_s,
                           excluded.staleness_budget, excluded.label, excluded.unit, excluded.owner_path, excluded.sort);

  select coalesce(jsonb_agg(jsonb_build_object(
           'subject', 'worker/' || w.worker, 'portal', null,
           'status', case when w.age > c_worker_dead then 'fail' else 'ok' end,
           'value', round(extract(epoch from w.age) / 60, 1), 'unit', 'min',
           'message', format('%s last beat %s min ago', w.worker, round(extract(epoch from w.age) / 60)),
           'facts', jsonb_build_object('worker', w.worker, 'beat_at', w.beat_at))
           order by w.worker), '[]'::jsonb),
         coalesce(array_agg(w.worker) filter (where w.age > c_worker_dead), '{}'),
         round(extract(epoch from min(w.age)) / 60, 1)
    into v_cells, v_dead, v_age
    from (select worker, max(beat_at) as beat_at, now() - max(beat_at) as age
            from worker_heartbeats group by worker) w;
  if v_cells = '[]'::jsonb then
    v_cells := jsonb_build_array(jsonb_build_object(
      'subject', 'worker', 'portal', null, 'status', 'unknown', 'value', null, 'unit', 'min',
      'message', 'no worker on record', 'facts', '{}'::jsonb));
  end if;

  -- The health lane is judged by its own pass, from the worker's heartbeat (wave 3), never by result rows: it passes
  -- every 60 s but writes a result only for a due check (jobs and llm every 600 s, capacity every 1,800 s).
  select details -> 'health', started_at
    into v_lane, v_boot
    from worker_heartbeats
   where worker = 'realtime-worker';
  if jsonb_typeof(v_lane) = 'object' and 'realtime-worker' = any(v_dead) then
    v_folded := array_append(v_folded, 'health_lane');
  elsif jsonb_typeof(v_lane) = 'object' then
    v_lane_pass := (v_lane ->> 'last_pass_at')::timestamptz;
    -- Counted from the later of the last pass and the boot: 10 min after boot, as the design says, so a restart
    -- after a long outage (the seed carries the last pass across) is not a Problem in its first minute.
    v_lane_since := greatest(v_lane_pass, v_boot);
    v_lane_failing := (v_lane ->> 'failing_since')::timestamptz;
    -- A pass skipped behind an abandoned one that still holds the lane's lock is recorded as a pass yet completes
    -- nothing: in_flight_s carries that lock's age until the watchdog restarts the worker.
    v_lane_flight := (v_lane ->> 'in_flight_s')::numeric;
    v_cells := v_cells || jsonb_build_object(
      'subject', 'health_lane', 'portal', null,
      'status', case when now() - v_lane_since > c_lane_dead or now() - v_lane_failing > c_lane_dead
                       or v_lane_flight > extract(epoch from c_lane_dead) then 'fail' else 'ok' end,
      'value', round(extract(epoch from now() - v_lane_since) / 60, 1), 'unit', 'min',
      'message', case
        when now() - v_lane_failing > c_lane_dead
          then format('the worker''s health lane has been failing for %s min',
                      round(extract(epoch from now() - v_lane_failing) / 60))
               || coalesce(': ' || (v_lane ->> 'last_error'), '')
        when v_lane_flight > extract(epoch from c_lane_dead)
          then format('the worker''s health lane has had a pass in flight for %s min', round(v_lane_flight / 60))
        when now() - v_lane_since > c_lane_dead
          then format('the worker''s health lane has not completed a pass for %s min',
                      round(extract(epoch from now() - v_lane_since) / 60))
        when v_lane_pass is null
          then format('the worker''s health lane has not passed yet; the worker booted %s min ago',
                      round(extract(epoch from now() - v_boot) / 60))
        else format('the worker''s health lane passed %s min ago',
                    round(extract(epoch from now() - v_lane_pass) / 60)) end,
      'facts', jsonb_build_object('last_pass_at', v_lane_pass, 'failing_since', v_lane_failing,
                                  'last_error', v_lane ->> 'last_error', 'in_flight_s', v_lane_flight,
                                  'boot', v_boot));
  end if;

  -- A host is the worker of the same name (scraper/realtime_worker.WORKER_NAME), so a dead worker explains its keys.
  select v_cells || coalesce(jsonb_agg(jsonb_build_object(
           'subject', 'stale:' || s.key, 'portal', null, 'status', 'fail',
           'value', round(s.age_s / 3600, 1), 'unit', 'h',
           'message', format('%s not measured for %s (budget %s)', s.key,
             case when s.budget_s < 7200 then round(s.age_s / 60) || ' min' else round(s.age_s / 3600, 1) || ' h' end,
             case when s.budget_s < 7200 then round(s.budget_s / 60) || ' min' else round(s.budget_s / 3600) || ' h' end),
           'facts', jsonb_build_object('key', s.key, 'host', s.host, 'last_measured_at', s.last_measured_at,
                                       'budget_s', s.budget_s::integer))
           order by s.sort, s.key) filter (where not s.folded), '[]'::jsonb),
         v_folded || coalesce(array_agg('stale:' || s.key) filter (where s.folded), '{}')
    into v_cells, v_folded
    from (select hc.key, hc.host, hc.sort, r.run_at as last_measured_at, hc.host = any(v_dead) as folded,
                 extract(epoch from now() - greatest(r.run_at, hc.declared_at)) as age_s,
                 extract(epoch from hc.staleness_budget) as budget_s
            from health_checks hc
            -- Measured = ok/warn/fail since declared_at (619's `measured` arm): a key whose every run is Not
            -- measured (errored, timed out, skipped) goes stale from its last measurement, else from declared_at.
            left join lateral (select c.run_at from pipeline_check_results c
                                where c.check_key = hc.key and c.status <> 'unknown'
                                  and c.run_at >= hc.declared_at
                                order by c.run_at desc limit 1) r on true
           where hc.class = 'alarm' and hc.key <> 'liveness'
             and health_stale(greatest(r.run_at, hc.declared_at), hc.staleness_budget)) s;

  -- Failing cells are read exactly as toolkit/system_alerts._cells reads them, the explicit verdicts only (633: a
  -- result without one has no cell, never the key's own), or this would flag a cell the ladder never opens.
  with pair as (
    select hc.key, n.details as now_details, m.details as then_details
      from health_checks hc
      cross join lateral (select c.run_at, c.status, c.details from pipeline_check_results c
                           where c.check_key = hc.key and c.run_at >= hc.declared_at
                           order by c.run_at desc limit 1) n
      cross join lateral (select c.details from pipeline_check_results c
                           where c.check_key = hc.key and c.run_at >= hc.declared_at and c.status <> 'unknown'
                             and c.run_at <= now() - make_interval(secs => c_unalerted_cadences * hc.cadence_s)
                           order by c.run_at desc limit 1) m
     where hc.class = 'alarm' and hc.key <> 'liveness'
       and n.status = 'fail' and not health_stale(n.run_at, hc.staleness_budget)
  ), sampled as (
    select key, 'now' as at, now_details as details from pair
    union all
    select key, 'then', then_details from pair
  ), failing as (
    select r.key, r.at, e ->> 'subject' as subject
      from sampled r
      cross join lateral jsonb_array_elements(case when jsonb_typeof(r.details -> 'cells') = 'array'
                                                   then r.details -> 'cells' else '[]'::jsonb end) e
     where e ->> 'status' = 'fail' and coalesce(e ->> 'subject', '') <> ''
  ), unalerted as (
    select distinct f.key, f.subject
      from failing f
     where f.at = 'now'
       and exists (select 1 from failing t where t.key = f.key and t.at = 'then' and t.subject = f.subject)
       and not exists (select 1 from ops_incidents i
                        where i.resolved_at is null and i.signature = 'cell:' || f.key || ':' || f.subject)
  )
  select v_cells || coalesce((
           select jsonb_build_array(jsonb_build_object(
                    'subject', 'unalerted', 'portal', null, 'status', 'fail', 'value', count(*), 'unit', 'cells',
                    'message', format('%s: a measured Problem has no incident; the teller is not telling',
                                      string_agg(distinct u.key, ', ' order by u.key)),
                    'facts', jsonb_build_object(
                      'keys', jsonb_agg(distinct u.key order by u.key),
                      'cells', jsonb_agg(u.key || ':' || u.subject order by u.key, u.subject))))
             from unalerted u
           having count(*) > 0), '[]'::jsonb)
    into v_cells;

  -- An open incident is judged on every run so it can recover; a folded subject stays inert.
  select v_cells || coalesce(jsonb_agg(jsonb_build_object(
           'subject', o.subject, 'portal', null, 'status', 'ok', 'value', null, 'unit', '',
           'message', 'no longer failing', 'facts', '{}'::jsonb) order by o.subject), '[]'::jsonb)
    into v_cells
    from (select substr(i.signature, length('cell:liveness:') + 1) as subject
            from ops_incidents i
           where i.resolved_at is null and starts_with(i.signature, 'cell:liveness:')) o
   where o.subject <> all(v_folded)
     and not exists (select 1 from jsonb_array_elements(v_cells) c where c ->> 'subject' = o.subject);

  select case when bool_or(c ->> 'status' = 'fail') then 'fail'
              when bool_or(c ->> 'status' = 'warn') then 'warn'
              when bool_or(c ->> 'status' = 'unknown') then 'unknown'
              else 'ok' end
    into v_status
    from jsonb_array_elements(v_cells) c;

  insert into pipeline_check_results (run_at, check_key, status, value, details)
  select now(), 'liveness', v_status, v_age, jsonb_build_object(
           'cells', v_cells,
           'message', coalesce(string_agg(c ->> 'message', '; ')
                                 filter (where v_status <> 'ok' and c ->> 'status' = v_status),
                               'Watchers: all alive'),
           'elapsed_ms', round(extract(epoch from clock_timestamp() - t0) * 1000)::integer)
    from jsonb_array_elements(v_cells) c;
end
$fn$;
comment on function public.health_liveness() is
  'The pg_cron watcher (migrations 620, 621, 633, 655 and 675): judges each worker heartbeat as its own subject '
  '(worker/<name>), the worker''s health lane by its own pass in that heartbeat, every registered alarm''s staleness '
  'and alarm cells failing with no incident (a result''s explicit verdict cells only, as the ladder reads them), and '
  'writes one pipeline_check_results row under key liveness. Never writes an incident or a notice: the teller does.';

revoke all on function public.health_liveness() from public, anon, authenticated;

do $post$
declare
  v_def text := pg_get_functiondef('public.health_liveness()'::regprocedure);
  n_before bigint;
  n_after bigint;
begin
  if (select relkind from pg_class where oid = 'public.scheduled_jobs'::regclass) <> 'r'
     or (select relkind from pg_class where oid = 'public.location_jobs'::regclass) <> 'v' then
    raise exception '675: scheduled_jobs is not the table or location_jobs is not its alias view';
  end if;
  if exists (select 1 from scheduled_jobs where not enabled and retired_at is null) then
    raise exception '675: a disabled row is live, and the lease no longer reads enabled';
  end if;
  if strpos(v_def, '''subject'', ''worker/'' || w.worker') = 0 then
    raise exception '675: health_liveness() does not give each worker its own subject';
  end if;
  if strpos(v_def, 'details -> ''health''') = 0 or strpos(v_def, '''unalerted''') = 0
     or strpos(v_def, '''stale:''') = 0 or strpos(v_def, 'cell:liveness:') = 0 then
    raise exception '675: health_liveness() lost its health lane, stale, unalerted or recovery arm';
  end if;
  if has_function_privilege('anon', 'public.health_liveness()', 'execute')
     or has_function_privilege('authenticated', 'public.health_liveness()', 'execute')
     or has_table_privilege('authenticated', 'public.job_runs', 'select')
     or has_table_privilege('authenticated', 'public.location_jobs', 'select') then
    raise exception '675: a browser role can reach the watcher, the run history or the alias';
  end if;
  -- Kept, as 620's to 655's were: the canary and Health see the new subjects at once.
  select count(*) into n_before from pipeline_check_results where check_key = 'liveness';
  perform public.health_liveness();
  select count(*) into n_after from pipeline_check_results where check_key = 'liveness';
  if n_after - n_before < 1 then
    raise exception '675: health_liveness() wrote no liveness row';
  end if;
end
$post$;

commit;
