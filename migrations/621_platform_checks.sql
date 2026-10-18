-- 621_platform_checks.sql -- platform checks: the poller's production facts, and the signature engine's incidents retired.
--
-- WHY (docs/design/health-dashboard.md, wave 3 and the `jobs` check):
--   the `jobs` check judges each GitHub workflow by its production runs only (event schedule, or push on main): a daily
--   job's single red fails (it loses a whole cycle), as do three reds in a row, and any other lone red is Watch. The
--   canary's poller must therefore store the event and branch of each red run and the one log line that explains it (the
--   reason line), and per workflow whether it is enabled, whether its file is still on main and when its recent
--   scheduled runs started. The ops_incidents signature engine (462) retires in the same wave: the ladder's cell
--   incidents (`cell:<key>:<subject>`) are the one incident model left.
--
-- WHAT CHANGES. Additive only.
--   1. workflow_failures gains reason, event and head_branch. A row with event NULL predates wave 3 and is never a
--      job's record.
--   2. workflow_run_health gains state, on_main and recent_starts. A row with on_main NULL has no cell yet.
--   3. Every open incident outside the cell namespace closes as 'retired: signature engine', with no notice: the
--      engine's recovery notice goes with its code, and its notices stay in the bell as they are.
--   4. public.health_liveness() is re-created from 620's body with one cell changed: health_lane judges the lane's own
--      pass in the worker heartbeat (`details -> 'health'`, the design's rule): Problem with no completed pass for 10
--      min (counted from the later of the last pass and the boot), a pass in flight for 10 min, or failing for 10 min. 620 judged result rows, which the
--      lane writes only for a DUE check (jobs and llm every 600 s, capacity every 1,800 s), so gaps of 540-600 s are
--      normal and a deploy restart near a cycle's end crossed 10 min: a false Problem with an onset notice.
--   Nothing is dropped and no grant changes. ops_incident_runs, the engine's columns, ops_incidents_open_signature_uq and
--   the ops_incidents table comment are 624's.
--
-- ci-allow-ungated: health_liveness it reads the health ledgers and the worker heartbeat and writes one pipeline_check_results row; execute revoked from public, anon and authenticated
--
-- APPLY through apply_migration.yml (--ref the feature branch) immediately before merging wave 3. Until the merge
-- deploys, main's poller and worker still run the engine: a signature that fails again in that window reopens as a fresh
-- row and may send one more legacy onset, and 624 repeats statement 3 for it. The new columns stay NULL until the wave-3
-- poller's first run on main. The heartbeat has no `health` entry until the wave-3 worker deploys, so until then there
-- is no health_lane cell, as under 620 before any worker-hosted key; the post-condition calls the watcher once and
-- keeps that row, as 620's did.
-- ROLLBACK: forward only (rule 1). Every statement is idempotent; a lock-timeout retry re-runs the whole file.

set lock_timeout = '5s';

begin;

alter table public.workflow_failures
  add column if not exists reason text,
  add column if not exists event text,
  add column if not exists head_branch text;

comment on column public.workflow_failures.reason is
  'The reason line (migration 621): the one line of the failed job''s log that explains the red run '
  '(scripts/failure_signature.reason_line, at most 300 characters), shown in its jobs cell. NULL when the run fell '
  'past the poller''s log budget (no retry) or its log held no such line.';
comment on column public.workflow_failures.event is
  'The run''s GitHub event (migration 621). The poller records production runs only: schedule, or push on main. '
  'A row with event NULL predates wave 3 and is never a job''s record.';
comment on column public.workflow_failures.head_branch is
  'The run''s branch (migration 621): a push counts as a production run only on main.';

alter table public.workflow_run_health
  add column if not exists state text,
  add column if not exists on_main boolean,
  add column if not exists recent_starts timestamptz[];

comment on column public.workflow_run_health.state is
  'The workflow''s state in the Actions workflows API (migration 621): active, disabled_manually, '
  'disabled_inactivity, ... A workflow that is not active reads off in its jobs cell.';
comment on column public.workflow_run_health.on_main is
  'Whether the workflow file is in main''s checkout at the poller''s last run (migration 621). Only true has a jobs '
  'cell; NULL has not been seen yet.';
comment on column public.workflow_run_health.recent_starts is
  'The newest 20 scheduled run starts the poller has seen, of every conclusion (migration 621). Accumulated from '
  'wave 3 on, never bootstrapped: the jobs check''s start-gap arm is Not measured below 10.';

-- starts_with, not LIKE (619's idiom). 4 rows on 2026-10-18, the oldest open since 2026-09-02; the 4 open cell
-- incidents keep their rows.
update public.ops_incidents
   set resolved_at = now(), resolve_reason = 'retired: signature engine'
 where resolved_at is null
   and not starts_with(signature, 'cell:');

-- 620's function verbatim, but for its health_lane cell (WHAT CHANGES 4) and that cell's variables.
create or replace function public.health_liveness()
returns void
language plpgsql
security definer
set search_path = public
as $fn$
declare
  -- The worker beats every 30 s and its watchdog exits at 300 s; a deploy gap is under 2 min.
  c_worker_dead constant interval := interval '10 minutes';
  -- Two of the health lane's 300 s pass budgets.
  c_lane_dead constant interval := interval '10 minutes';
  -- The matcher renews its 15-min lease every pass (~300.5 s apart). Problem two missed passes and a minute past its
  -- expiry, so a crashed API's successor, which must wait the expiry out, does not trip it.
  c_lease_passes constant integer := 2;
  c_lease_slack_s constant integer := 60;
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
  v_interval numeric;
  v_lease timestamptz;
  v_slack interval;
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
           'subject', 'worker', 'portal', null,
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

  -- 300 is the API's own default (api/notifications._read_interval_seconds), also for a value it cannot read.
  select case when jsonb_typeof(value) = 'number' then trunc((value #>> '{}')::numeric)
              when value #>> '{}' ~ '^\s*-?\d+\s*$' then (value #>> '{}')::numeric end
    into v_interval
    from app_settings where key = 'notifications_matcher_interval_seconds';
  v_interval := coalesce(v_interval, 300);
  v_slack := make_interval(secs => (c_lease_passes * v_interval + c_lease_slack_s)::float8);
  select expires_at into v_lease from notification_matcher_lease where id = 1;
  v_cells := v_cells || (jsonb_build_object('subject', 'api', 'portal', null, 'unit', 'min') || case
    when v_interval <= 0 then jsonb_build_object(
      'status', 'ok', 'value', null, 'message', 'the API''s matcher is off (interval 0)',
      'facts', jsonb_build_object('state', 'off'))
    when v_lease is null then jsonb_build_object(
      'status', 'unknown', 'value', null, 'message', 'lease released; a clean API shutdown',
      'facts', jsonb_build_object('state', 'released'))
    when v_lease < now() - v_slack then jsonb_build_object(
      'status', 'fail', 'value', round(extract(epoch from now() - v_lease) / 60, 1),
      'message', format('the API''s background loops have not renewed their lease; it expired %s min ago',
                        round(extract(epoch from now() - v_lease) / 60)),
      'facts', jsonb_build_object('state', 'expired', 'expires_at', v_lease, 'interval_s', v_interval))
    else jsonb_build_object(
      'status', 'ok', 'value', greatest(0, round(extract(epoch from now() - v_lease) / 60, 1)),
      'message', case when v_lease >= now() then 'the API''s background loops hold their lease'
                      else format('the API''s lease expired %s min ago; a Problem after %s min',
                                  round(extract(epoch from now() - v_lease) / 60),
                                  round(extract(epoch from v_slack) / 60)) end,
      'facts', jsonb_build_object('state', 'held', 'expires_at', v_lease, 'interval_s', v_interval))
  end);

  -- Failing cells are read exactly as toolkit/system_alerts._cells reads them (the explicit verdicts, else the key),
  -- or this would flag a cell the ladder never opens.
  with pair as (
    select hc.key, n.details as now_details, m.status as then_status, m.details as then_details
      from health_checks hc
      cross join lateral (select c.run_at, c.status, c.details from pipeline_check_results c
                           where c.check_key = hc.key and c.run_at >= hc.declared_at
                           order by c.run_at desc limit 1) n
      cross join lateral (select c.status, c.details from pipeline_check_results c
                           where c.check_key = hc.key and c.run_at >= hc.declared_at and c.status <> 'unknown'
                             and c.run_at <= now() - make_interval(secs => c_unalerted_cadences * hc.cadence_s)
                           order by c.run_at desc limit 1) m
     where hc.class = 'alarm' and hc.key <> 'liveness'
       and n.status = 'fail' and not health_stale(n.run_at, hc.staleness_budget)
  ), sampled as (
    select key, 'now' as at, 'fail' as status, now_details as details from pair
    union all
    select key, 'then', then_status, then_details from pair
  ), failing as (
    select r.key, r.at, s.subject
      from sampled r
      cross join lateral jsonb_array_elements_text(coalesce(
        (select coalesce(jsonb_agg(e ->> 'subject') filter (where e ->> 'status' = 'fail'), '[]'::jsonb)
           from jsonb_array_elements(case when jsonb_typeof(r.details -> 'cells') = 'array'
                                          then r.details -> 'cells' else '[]'::jsonb end) e
          where e ->> 'status' in ('ok', 'warn', 'fail') and coalesce(e ->> 'subject', '') <> ''
         having count(*) > 0),
        case when r.status = 'fail' then jsonb_build_array(r.key) else '[]'::jsonb end)) s(subject)
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
  'The pg_cron watcher (migrations 620 and 621): judges the worker heartbeat, the worker''s health lane by its own '
  'pass in that heartbeat, every registered alarm''s staleness, the API''s matcher lease and alarm cells failing with '
  'no incident, and writes one pipeline_check_results row under key liveness. Never writes an incident or a notice: '
  'the teller does.';

-- create or replace keeps 620's owner, ACL and comment; the revoke is re-stated so the post-condition's privilege
-- check below is self-evidently true.
revoke all on function public.health_liveness() from public, anon, authenticated;

do $post$
declare
  n_before bigint;
  n_after bigint;
begin
  if (select count(*)
        from (values ('public.workflow_failures'::regclass, 'reason', 'text'),
                     ('public.workflow_failures'::regclass, 'event', 'text'),
                     ('public.workflow_failures'::regclass, 'head_branch', 'text'),
                     ('public.workflow_run_health'::regclass, 'state', 'text'),
                     ('public.workflow_run_health'::regclass, 'on_main', 'boolean'),
                     ('public.workflow_run_health'::regclass, 'recent_starts', 'timestamp with time zone[]'))
             as want(rel, col, typ)
        join pg_attribute a
          on a.attrelid = want.rel and a.attname = want.col and not a.attisdropped
       where format_type(a.atttypid, a.atttypmod) = want.typ) <> 6 then
    raise exception '621: a workflow_failures or workflow_run_health column is missing or of the wrong type';
  end if;
  if exists (select 1 from public.ops_incidents
              where resolved_at is null and not starts_with(signature, 'cell:')) then
    raise exception '621: an open incident outside the cell namespace survived the retirement';
  end if;
  if exists (select 1
               from unnest(array['public.workflow_failures', 'public.workflow_run_health',
                                 'public.ops_incidents']::regclass[]) as t(rel)
              cross join unnest(array['anon', 'authenticated']::name[]) as r(role)
              join pg_class c on c.oid = t.rel
             where not c.relrowsecurity
                or has_any_column_privilege(r.role, t.rel::oid, 'SELECT, INSERT, UPDATE, REFERENCES')
                or has_table_privilege(r.role, t.rel::oid, 'DELETE, TRUNCATE, TRIGGER')) then
    raise exception '621: workflow_failures, workflow_run_health and ops_incidents must stay RLS-on and closed to browser roles';
  end if;
  if has_function_privilege('anon', 'public.health_liveness()', 'execute')
     or has_function_privilege('authenticated', 'public.health_liveness()', 'execute') then
    raise exception '621: a browser role can execute health_liveness()';
  end if;
  if strpos(pg_get_functiondef('public.health_liveness()'::regprocedure), 'details -> ''health''') = 0 then
    raise exception '621: health_liveness() does not judge the health lane from its heartbeat entry';
  end if;
  -- Kept, as 620's was: the canary and Health see the new rule at once.
  select count(*) into n_before from pipeline_check_results where check_key = 'liveness';
  perform public.health_liveness();
  select count(*) into n_after from pipeline_check_results where check_key = 'liveness';
  if n_after - n_before < 1 then  -- a cron tick may land beside a re-apply's own row
    raise exception '621: health_liveness() wrote no liveness row';
  end if;
end
$post$;

commit;
