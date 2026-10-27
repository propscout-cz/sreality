-- 682_health_job_cells.sql -- Actions exit W2 (#2026): every registered job-runner job gets its own Health cell,
-- job/<name>, from the pg_cron watcher, and the runner's fleet gets one, job-runner.
--
-- WHY (docs/design/actions-exit/PROGRAM.md § 4.2 Health, #2026): the watcher runs in the database, so it is the one
--   producer that survives a Railway outage. A job the runner owns is judged there: late, failing, in flight, held by
--   a sibling on its resource, waiting with no runner beating. The canary's failing_cells_have_incidents reads every
--   liveness cell, so a job's Problem opens an incident and e-mails through the existing canary (ruling R-1).
--
-- WHAT CHANGES. Additive only: two new functions and one function replace. No table, column, row, view or grant.
--   * public.health_span(double precision): a duration as Health reads it. Reads nothing.
--   * public.health_job_cells(interval): one job/<name> cell per registered live job-runner row (_DUE_SQL's predicate:
--     runner 'job-runner', registry_at set, retired_at null), one per entry a fresh replica's boot refused that has no
--     such row, and the job-runner fleet cell while live rows exist. Never 'unknown'. Security invoker: the definer
--     watcher calls it. The one seam later waves replace (#2028, #2029, #2033), alone.
--   * public.health_liveness() is re-created from the live body, 675's byte for byte (the precondition refuses any
--     other), with one block after the health-lane arm: the helper's cells, inside a subtransaction. When the helper
--     raises, the watcher writes one 'job-cells' Problem plus a listed 'unknown' placeholder for each open job/* and
--     job-runner incident, so the teller (toolkit/system_alerts) neither keeps, recovers nor retires them, and the
--     row reads 'fail', never 'unknown'.
--   EXECUTE on all three is revoked from public, anon and authenticated; the two new functions are registered UNGATED
--   (acl) in tests/_admin_gate_registry.py (no in-file annotation since 676).
--
-- APPLY through apply_migration.yml (--ref the feature branch) before merging. Safe in either order: the deployed code
-- reads nothing this changes, and with no registered job-runner row the helper writes no cell. The post-condition
-- runs the helper over a rolled-back probe row, so every per-row expression is evaluated at apply.
-- ROLLBACK: forward only (rule 1). Every statement is idempotent: a re-run passes the precondition (this file's own
-- body), and a lock-timeout retry re-runs the whole file.

set lock_timeout = '5s';

begin;

-- One overload, and its body is 675's or this file's own (a re-run): a watcher that drifted is never regressed.
do $pre$
begin
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'health_liveness'), '')
     not in ('adc5a21072276c4e8119b9a7ea11d2c4', 'b7534724a3aa390f0f8cf20f89bf6a38') then
    raise exception '682 refused: health_liveness() is neither 675''s body nor this file''s (a re-run), or has two '
                    'overloads: rebuild this file from the live body';
  end if;
end
$pre$;

-- A duration as Health reads it: s under 2 min, min under 2 h, h under 2 d, else d. Reads nothing.
create or replace function public.health_span(p_s double precision)
returns text
language sql
immutable
parallel safe
set search_path = public
as $span$
  select case when p_s is null then null
              when abs(p_s) < 120 then round(p_s)::bigint || ' s'
              when abs(p_s) < 7200 then round(p_s / 60)::bigint || ' min'
              when abs(p_s) < 172800 then round((p_s / 3600)::numeric, 1) || ' h'
              else round(p_s / 86400)::bigint || ' d' end
$span$;
revoke all on function public.health_span(double precision) from public, anon, authenticated;

-- One job/<name> cell per registered live job-runner row, one per entry a fresh replica refused with no such row, and
-- the job-runner fleet cell. Literals pinned by tests/scraper/test_job_registry.py: 'job-runner' = jobs.RUNNER,
-- 'job-runner/' = job_runner.HEARTBEAT_PREFIX, 600 = jobs.STUCK_RUN_GRACE_S. Never reads job_runs.log_tail: it can hold
-- a connection string.
create or replace function public.health_job_cells(p_dead interval)
returns jsonb
language sql
stable
set search_path = public
as $cells$
with live as (
  select j.*, extract(epoch from j.cadence)::float8 as every_s, coalesce(j.paused_until > now(), false) as paused
    from scheduled_jobs j
   where j.runner = 'job-runner' and j.registry_at is not null and j.retired_at is null
), fresh as (
  -- The replicas beating inside p_dead, each with its holder (a claim's holder is '<holder>/<n>') and the entries its
  -- boot refused.
  select w.worker, w.details ->> 'holder' as holder,
         case when jsonb_typeof(w.details -> 'skipped') = 'array' then w.details -> 'skipped' else '[]'::jsonb end
           as skipped
    from worker_heartbeats w
   where starts_with(w.worker, 'job-runner/') and w.beat_at >= now() - p_dead
), refused as (
  select e.name, jsonb_agg(f.worker order by f.worker) as replicas
    from fresh f cross join lateral jsonb_array_elements_text(f.skipped) e(name)
   where e.name <> ''          -- never a subject-less cell, which the teller and the canary would both drop
   group by e.name
), x as (
  select l.*, o.started_at as run_at,
         -- the reaper's own test: the open run's holder no longer holds a live lease
         o.id is not null and not (l.lease_holder is not distinct from o.holder
                                   and coalesce(l.lease_expires_at >= now(), false)) as run_lapsed,
         (select f.worker from fresh f where starts_with(o.holder, f.holder || '/') limit 1) as run_replica,
         c.outcome as last_run, c.requested_by as last_by,
         e.id = c.id as fail_is_last, e.outcome as fail_outcome, e.exit_code as fail_exit,
         e.peak_rss_mb, e.requested_by as fail_by, e.args as fail_args,
         h.job_name as held_by, extract(epoch from now() - h.started_at) as held_for_s, hs.timeout_s as held_budget_s,
         fs.first_at, ss.streak_since, rf.replicas as refused_by,
         -- Not judged on its history yet: no run has closed since the cadence before its due_at. Only _REGISTER_SQL
         -- sets due_at with no run (a new, adopted or un-retired row, at the first slot >= now() + timeout_s); every
         -- release and reap ends a run at or after due_at - cadence. It lasts through the first run, so a re-landed
         -- row's carried streak, timeout and last success never read Problem while its first slot runs.
         l.due_at is not null and not exists (
           select 1 from job_runs r
            where r.job_name = l.job_name and r.ended_at >= l.due_at - l.cadence - make_interval(secs => l.timeout_s))
           as fresh_slot
    from live l
    left join job_runs o on o.job_name = l.job_name and o.ended_at is null
    left join lateral (select r.id, r.outcome, r.requested_by from job_runs r
                        where r.job_name = l.job_name and r.ended_at is not null
                        order by r.started_at desc, r.id desc limit 1) c on true
    left join lateral (select r.id, r.outcome, r.exit_code, r.peak_rss_mb, r.requested_by, r.args from job_runs r
                        where r.job_name = l.job_name and r.outcome in ('failed', 'timeout', 'lost')
                        order by r.started_at desc, r.id desc limit 1) e on true
    left join lateral (select min(r.started_at) as first_at from job_runs r     -- the first SCHEDULED run
                        where r.job_name = l.job_name and r.requested_by is null) fs on true
    left join lateral (select min(r.started_at) as streak_since from job_runs r -- the current streak's first failure
                        where r.job_name = l.job_name and r.outcome in ('failed', 'timeout', 'lost')
                          and r.started_at > coalesce((select max(k.started_at) from job_runs k
                                                        where k.job_name = l.job_name and k.outcome = 'ok'),
                                                       '-infinity')) ss on true
    left join job_runs h on l.resource is not null and h.ended_at is null and h.resource = l.resource
                        and h.job_name <> l.job_name
    left join scheduled_jobs hs on hs.job_name = h.job_name
    left join refused rf on rf.name = l.job_name
), y as (
  select x.*,
         -- Never registry_at (every boot re-stamps it) and never a request run (a failed drill must not make a
         -- quarterly job late); a lapsed pause restarts the clock.
         greatest(case when x.fresh_slot then greatest(x.last_success_at, x.due_at - x.cadence)
                       else coalesce(x.last_success_at, coalesce(x.first_at, x.due_at) - x.cadence) end,
                  case when x.paused_until <= now() then x.paused_until end) as since,
         extract(epoch from now() - x.run_at) as flight_s,
         -- Due since its slot, or since a pause that lapsed after it; requested since the request.
         case when x.run_at is null
              then least(case when not x.paused and x.due_at <= now()
                              then greatest(x.due_at, case when x.paused_until <= now() then x.paused_until end) end,
                         x.requested_at) end
           as waiting_since
    from x
), j as (
  select y.*, extract(epoch from now() - y.since) as age_s, extract(epoch from now() - y.waiting_since) as waiting_s,
         y.every_s + y.timeout_s + w.s as late_warn_s,
         y.every_s + y.timeout_s + w.s + least(y.every_s, 86400) as late_fail_s, w.s as sibling_wait_s,
         case when y.fail_outcome = 'lost' then 'lost: the replica holding it stopped renewing its lease'
              when y.fail_is_last then coalesce(y.last_error, y.fail_outcome || coalesce(', exit ' || y.fail_exit, ''))
              else coalesce(y.fail_outcome || coalesce(', exit ' || y.fail_exit, ''), y.last_error, y.last_outcome)
         end || case when y.fail_by is not null
                     then ' (a request by ' || y.fail_by
                          || coalesce(', args ' || nullif(left(array_to_string(y.fail_args, ' '), 120), ''), '') || ')'
                     else '' end
           as cause
    from y cross join lateral (
      -- W: the longest run of a sibling on the same resource inside (since, now], an open one counted to now.
      select coalesce(max(extract(epoch from coalesce(r.ended_at, now()) - greatest(r.started_at, y.since))), 0) as s
        from job_runs r
       where y.resource is not null and r.resource = y.resource and r.job_name <> y.job_name
         and coalesce(r.ended_at, now()) > y.since) w
), conds as (                      -- every condition, as scripts/checks/common.judged keeps them
  select j.job_name, c.ord, c.level, c.text from j cross join lateral (values
    (1, case when j.refused_by is not null then 'fail' end,
        'refused at boot by ' || (select string_agg(e.w, ', ') from jsonb_array_elements_text(j.refused_by) e(w))
        || ' (JOBS skip in its log): no replica claims it'),
    -- At timeout_s the runner sends SIGTERM, SIGKILL 90 s later, and records the run 'timeout'; past the stuck-run
    -- grace the kill did not land.
    (2, case when j.flight_s > j.timeout_s + 600 then 'fail' when j.flight_s > j.timeout_s * 2.0 / 3 then 'warn' end,
        format('running for %s of its %s budget', health_span(j.flight_s), health_span(j.timeout_s))
        || case when j.flight_s > j.timeout_s + 600
                then '; past the runner''s 10 min stopping grace: nothing is stopping it'
                when j.flight_s > j.timeout_s then '; past it: the runner is stopping it' else '' end
        || case when j.run_lapsed then '; its lease lapsed: the replica holding it is gone' else '' end),
    -- Ruling R-3, "problem at the budget": the runner's own record of the stop, never a sample of the kill window.
    (3, case when j.paused or j.fresh_slot or j.last_run is distinct from 'timeout' then null else 'fail' end,
        format('its last run hit its %s budget and was stopped (timeout)', health_span(j.timeout_s))
        || case when j.last_by is not null then ' (a request by ' || j.last_by || ')' else '' end),
    (4, case when j.waiting_s is null or j.held_by is not null or j.waiting_s <= extract(epoch from p_dead) then null
             when exists (select 1 from fresh) then 'warn' else 'fail' end,
        case when j.requested_at is not null and j.requested_at = j.waiting_since
             then 'requested by ' || j.requested_by else 'due' end
        || ' ' || health_span(j.waiting_s) || ' ago and not started'
        || case when not exists (select 1 from fresh) then '; no job-runner replica has beaten for 10 min' else '' end),
    -- Three in a row is a Problem only once they span min(cadence, 1 h): the backoff retries in 60 s, 120 s, 240 s.
    (5, case when j.paused or j.fresh_slot or j.consecutive_failures = 0 then null
             when j.consecutive_failures >= 3
              and coalesce(j.streak_since, '-infinity') <= now() - least(j.cadence, interval '1 hour') then 'fail'
             else 'warn' end,
        case when j.consecutive_failures = 1 then 'its last run failed: ' || coalesce(j.cause, j.last_outcome)
             else format('%s failed runs in a row%s; the last: %s', j.consecutive_failures,
                         coalesce(', the first ' || health_span(extract(epoch from now() - j.streak_since))
                                  || ' ago', ''),
                         coalesce(j.cause, j.last_outcome)) end),
    (6, case when j.paused or j.age_s is null then null when j.age_s > j.late_fail_s then 'fail'
             when j.age_s > j.late_warn_s then 'warn' end,
        format('late: %s for %s (Watch past %s, Problem past %s)',
               case when j.last_success_at is null then 'never ran ok' else 'no ok run' end,
               health_span(j.age_s), health_span(j.late_warn_s), health_span(j.late_fail_s))),
    (7, case when j.paused and j.paused_until - now() > greatest(j.cadence, interval '7 days') then 'warn' end,
        format('paused for %s more by %s, longer than a cycle: its slots are skipped until then (%s)',
               health_span(extract(epoch from j.paused_until - now())), j.paused_by, j.pause_note))
  ) c(ord, level, text) where c.level is not null
)
select coalesce(jsonb_agg(cell order by subject), '[]'::jsonb) from (
  select 'job/' || j.job_name as subject, jsonb_build_object(
           'subject', 'job/' || j.job_name, 'portal', null, 'status', coalesce(w.level, 'ok'),
           'value', case when j.last_success_at is not null then round((j.age_s / 3600)::numeric, 1) end, 'unit', 'h',
           'message', j.job_name || ': ' || coalesce(w.text, case
             when j.run_at is not null then
               format('running for %s of its %s budget', health_span(j.flight_s), health_span(j.timeout_s))
               || coalesce(' on ' || j.run_replica, '')
               || case when j.run_lapsed then '; its lease lapsed: the replica holding it is gone' else '' end
               || case when j.requested_at is not null then '; a request by ' || j.requested_by || ' waits for it'
                       else '' end
             when j.waiting_since is not null and j.held_by is not null then
               format('waiting for %s, which holds %s (running %s of its %s budget)', j.held_by, j.resource,
                      health_span(j.held_for_s), health_span(j.held_budget_s))
             when j.requested_at is not null then
               format('requested by %s %s ago; starting', j.requested_by,
                      health_span(extract(epoch from now() - j.requested_at)))
             when j.paused then
               format('paused for %s more by %s: %s', health_span(extract(epoch from j.paused_until - now())),
                      j.paused_by, j.pause_note)
               || case when j.consecutive_failures > 0
                       then '; its last run failed: ' || coalesce(j.cause, j.last_outcome) else '' end
             when j.fresh_slot and j.due_at > now() and j.last_run is null and j.last_success_at is null then
               format('not run yet; first slot in %s (%s UTC)', health_span(extract(epoch from j.due_at - now())),
                      to_char(j.due_at at time zone 'UTC', 'YYYY-MM-DD HH24:MI'))
             when j.fresh_slot and j.due_at > now() then
               format('registered again; not run here since; first slot in %s (%s UTC); %s',
                      health_span(extract(epoch from j.due_at - now())),
                      to_char(j.due_at at time zone 'UTC', 'YYYY-MM-DD HH24:MI'),
                      coalesce('last ok ' || health_span(extract(epoch from now() - j.last_success_at)) || ' ago',
                               'never ran ok'))
             else
               coalesce('last ok ' || health_span(extract(epoch from now() - j.last_success_at)) || ' ago',
                        'never ran ok')
               || case when j.due_at > now() then '; next slot in ' || health_span(extract(epoch from j.due_at - now()))
                       else '; due now' end
               -- A pure request never moves due_at (job_lease._RELEASE_RUN_SQL), so a cut request is not re-filed.
               || case when j.last_run = 'interrupted' and j.last_by is null
                       then '; its last run was cut by a deploy and is due again at once'
                       when j.last_run = 'interrupted'
                       then '; a request by ' || j.last_by || ' was cut by a deploy; it is not re-filed'
                       else '' end
           end),
           'facts', jsonb_build_object(
             'runner', j.runner, 'cadence_s', j.every_s, 'timeout_s', j.timeout_s, 'resource', j.resource,
             'due_at', j.due_at, 'paused_until', j.paused_until, 'paused_by', j.paused_by, 'pause_note', j.pause_note,
             'requested_at', j.requested_at, 'requested_by', j.requested_by, 'last_success_at', j.last_success_at,
             'consecutive_failures', j.consecutive_failures, 'last_run', j.last_run, 'cause', j.cause,
             'exit_code', j.fail_exit, 'peak_rss_mb', j.peak_rss_mb, 'in_flight_s', round(j.flight_s),
             'run_replica', j.run_replica, 'held_by', j.held_by, 'sibling_wait_s', round(j.sibling_wait_s),
             'fresh_slot', j.fresh_slot, 'streak_since', j.streak_since,
             'late_warn_s', round(j.late_warn_s), 'late_fail_s', round(j.late_fail_s), 'refused_by', j.refused_by,
             'conditions', coalesce((select jsonb_agg(c.level || ': ' || c.text order by c.ord)
                                       from conds c where c.job_name = j.job_name), '[]'::jsonb))) as cell
    from j left join lateral (select c.level, c.text from conds c where c.job_name = j.job_name
                               order by (c.level = 'fail') desc, c.ord limit 1) w on true
  union all
  select 'job/' || r.name, jsonb_build_object(
           'subject', 'job/' || r.name, 'portal', null, 'status', 'fail', 'value', null, 'unit', 'h',
           'message', r.name || ': refused at boot by '
                      || (select string_agg(e.w, ', ') from jsonb_array_elements_text(r.replicas) e(w))
                      || ' (JOBS skip in its log): it has no row and runs nowhere',
           'facts', jsonb_build_object('refused_by', r.replicas))
    from refused r where not exists (select 1 from live l where l.job_name = r.name)
  union all
  select 'job-runner', jsonb_build_object(
           'subject', 'job-runner', 'portal', null,
           'status', case when exists (select 1 from fresh) then 'ok' else 'warn' end,
           'value', (select count(*) from fresh), 'unit', 'replicas',
           'message', case when exists (select 1 from fresh)
                           then format('job-runner: %s replicas beating; %s jobs registered',
                                       (select count(*) from fresh), (select count(*) from live))
                           else 'job-runner: no replica has beaten for 10 min (see the worker/job-runner/<id> '
                                'cells and Railway''s job-runner service); a registered job turns Problem 10 min '
                                'after its slot, a request 10 min after it was filed' end,
           'facts', jsonb_build_object('replicas', (select coalesce(jsonb_agg(f.worker order by f.worker), '[]'::jsonb)
                                                      from fresh f),
                                       'registered', (select count(*) from live)))
   where exists (select 1 from live)
) cells(subject, cell);
$cells$;
revoke all on function public.health_job_cells(interval) from public, anon, authenticated;

-- 675's function verbatim, but for the scheduled-jobs block after the health-lane arm.
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

  -- Scheduled jobs (Actions exit W2, #2026, migration 682): a job/<name> cell per registered live job-runner row, a
  -- refused entry's, and the job-runner fleet cell. A subtransaction: a defect costs these cells, never the watcher's.
  -- Each open job incident then gets a listed 'unknown' cell, which the teller neither keeps, recovers nor retires
  -- (toolkit/system_alerts._cells), and the 'job-cells' fail keeps the row 'fail', never 'unknown'.
  begin
    v_cells := v_cells || public.health_job_cells(c_worker_dead);
  exception when others then
    v_cells := v_cells || jsonb_build_object('subject', 'job-cells', 'portal', null, 'status', 'fail',
      'value', null, 'unit', '', 'message', 'the job cells could not be computed: ' || sqlerrm,
      'facts', jsonb_build_object('sqlstate', sqlstate))
      || coalesce((select jsonb_agg(jsonb_build_object(
                     'subject', o.subject, 'portal', null, 'status', 'unknown', 'value', null, 'unit', '',
                     'message', o.subject || ': not judged: the job cells could not be computed',
                     'facts', '{}'::jsonb) order by o.subject)
                     from (select substr(i.signature, length('cell:liveness:') + 1) as subject
                             from ops_incidents i
                            where i.resolved_at is null
                              and (starts_with(i.signature, 'cell:liveness:job/')
                                   or i.signature = 'cell:liveness:job-runner')) o), '[]'::jsonb);
  end;

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
  'The pg_cron watcher (migrations 620, 621, 633, 655, 675 and 682): judges each worker heartbeat as its own subject '
  '(worker/<name>), the worker''s health lane by its own pass in that heartbeat, each job-runner job (job/<name>) and '
  'the fleet (job-runner) through health_job_cells(), every registered alarm''s staleness and alarm cells failing '
  'with no incident (a result''s explicit verdict cells only, as the ladder reads them), and writes one '
  'pipeline_check_results row under key liveness. Never writes an incident or a notice: the teller does.';

-- create or replace keeps 675's owner and ACL; the revoke is re-stated so the post-condition's privilege check below is
-- self-evidently true.
revoke all on function public.health_liveness() from public, anon, authenticated;

do $post$
declare
  v_def text := pg_get_functiondef('public.health_liveness()'::regprocedure);
  n_before bigint;
  n_after bigint;
  v_cells jsonb;
  v_probe jsonb;
begin
  if strpos(v_def, 'public.health_job_cells(c_worker_dead)') = 0 or strpos(v_def, '''job-cells''') = 0 then
    raise exception '682: health_liveness() does not judge the scheduled jobs';
  end if;
  if strpos(v_def, '''subject'', ''worker/'' || w.worker') = 0 or strpos(v_def, 'details -> ''health''') = 0
     or strpos(v_def, '''unalerted''') = 0 or strpos(v_def, '''stale:''') = 0 or strpos(v_def, 'cell:liveness:') = 0
     or strpos(v_def, 'notification_matcher_lease') > 0 or strpos(v_def, '''subject'', ''api''') > 0 then
    raise exception '682: health_liveness() lost a worker, health lane, stale, unalerted or recovery arm';
  end if;
  if has_function_privilege('anon', 'public.health_liveness()', 'execute')
     or has_function_privilege('authenticated', 'public.health_liveness()', 'execute')
     or has_function_privilege('anon', 'public.health_job_cells(interval)', 'execute')
     or has_function_privilege('authenticated', 'public.health_job_cells(interval)', 'execute')
     or has_function_privilege('anon', 'public.health_span(double precision)', 'execute')
     or has_function_privilege('authenticated', 'public.health_span(double precision)', 'execute') then
    raise exception '682: a browser role can execute the watcher, health_job_cells() or health_span()';
  end if;
  v_cells := public.health_job_cells(interval '10 minutes');
  if jsonb_typeof(v_cells) <> 'array' or exists (
       select 1 from jsonb_array_elements(v_cells) c where c ->> 'status' not in ('ok', 'warn', 'fail')) then
    raise exception '682: health_job_cells() is not an array of ok, warn and fail cells: %', v_cells;
  end if;
  -- Kept, as 620's to 675's were: the canary and Health see the job cells at once.
  select count(*) into n_before from pipeline_check_results where check_key = 'liveness';
  perform public.health_liveness();
  select count(*) into n_after from pipeline_check_results where check_key = 'liveness';
  if n_after - n_before < 1 then  -- a cron tick may land beside a re-apply's own row
    raise exception '682: health_liveness() wrote no liveness row';
  end if;
  -- The row this call wrote, by its run_at (the function stamps now(), this transaction's start).
  select details -> 'cells' into v_cells from pipeline_check_results
   where check_key = 'liveness' and run_at = now() order by id desc limit 1;
  if exists (select 1 from jsonb_array_elements(v_cells) c where c ->> 'subject' = 'job-cells') then
    raise exception '682: the watcher could not compute the job cells: %',
      (select c ->> 'message' from jsonb_array_elements(v_cells) c where c ->> 'subject' = 'job-cells' limit 1);
  end if;
  -- A rolled-back probe evaluates every per-row expression (the laterals, the sibling wait, format(), the
  -- health_span casts, the conditions), which no registered row would before the first job moves. Never committed,
  -- so no runner sees it.
  begin
    insert into scheduled_jobs (job_name, cadence, runner, registry_at, offset_s, timeout_s, memory_mb, cpus, due_at,
                                resource, consecutive_failures, last_outcome, last_error)
    values ('_682_probe', interval '6 hours', 'job-runner', now(), 0, 1200, 256, 1, now() - interval '15 minutes',
            '_682_probe', 1, 'failed', 'exit 1');
    insert into job_runs (job_name, holder, resource, args, requested_by, started_at, ended_at, outcome, exit_code)
    values ('_682_probe', 'probe/a', '_682_probe', '{--probe}', 'probe', now() - interval '1 hour',
            now() - interval '50 minutes', 'failed', 1);
    insert into job_runs (job_name, holder, resource, started_at)
    values ('_682_probe', 'probe/b', '_682_probe', now() - interval '5 minutes');
    select e into v_probe from jsonb_array_elements(public.health_job_cells(interval '10 minutes')) e
     where e ->> 'subject' = 'job/_682_probe';
    if v_probe is null or v_probe ->> 'status' not in ('ok', 'warn', 'fail')
       or not starts_with(v_probe ->> 'message', '_682_probe: ') then
      raise exception '682: health_job_cells() does not judge a registered row: %', v_probe;
    end if;
    raise exception '682 probe rolled back';  -- the sentinel: undoes the three inserts
  exception when raise_exception then
    if sqlerrm <> '682 probe rolled back' then
      raise;
    end if;
  end;
  if exists (select 1 from scheduled_jobs where job_name = '_682_probe')
     or exists (select 1 from job_runs where job_name = '_682_probe') then
    raise exception '682: the probe row survived its rollback';
  end if;
end
$post$;

commit;
