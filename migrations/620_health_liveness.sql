-- 620_health_liveness.sql -- the pg_cron watcher: health_liveness() judges the watchers every five minutes.
--
-- WHY (docs/design/health-dashboard.md, wave 2 and the `liveness` check):
--   a dead worker, a stopped check or a broken teller must be noticed by a runtime that depends on none of them. The
--   worker's heartbeat had one reader, a GitHub check sampled about five times a day against a 5-minute limit, and the
--   274 dead-man switch watched max(run_at) over every key for 24 h and never fired. pg_cron runs beside the data,
--   outside the worker and the API: it judges both and every registered alarm's freshness, and writes ONE result row.
--   The API's teller (toolkit/system_alerts.reconcile) opens and tells its incidents; the GitHub canary watches this row
--   and the teller. Who watches whom: pg_cron the worker and the checks, the canary pg_cron and the teller.
--
-- WHAT CHANGES. Additive only.
--   1. public.health_liveness() upserts its own registry row (liveness, pg_cron, every 300 s, budget 15 min) and
--      inserts one pipeline_check_results row. Its cells, read by the ladder unchanged (`cell:liveness:<subject>`):
--        worker       one per worker_heartbeats row: Problem when the last beat is over 10 min old; Not measured when
--                     no worker is on record.
--        health_lane  only once health_checks holds realtime-worker keys (wave 3): Problem when none of them has had a
--                     result for 10 min.
--        stale:<key>  an alarm whose newest measured result (ok/warn/fail since declared_at, 619's rule) is past
--                     its staleness budget; never measured: counted from declared_at, so a key whose every run is
--                     Not measured is still told by someone. A host is the worker of the same name: while that worker's cell fails, its keys
--                     and its health lane fold into it instead.
--        api          notification_matcher_lease: Problem when expires_at is more than
--                     2 x notifications_matcher_interval_seconds + 60 s in the past; Not measured while the lease is
--                     released (a clean API shutdown); OK and `off` at interval 0.
--        unalerted    alarm cells measured failing now and 2 cadences ago with no open incident (the ladder's rules:
--                     rows since declared_at, a stale or unmeasured newest row is inert): the teller is not telling.
--      An open liveness incident whose subject this run neither judges nor folds gets an OK cell, so it recovers on a
--      measured OK instead of waiting for the 7-day silent close. Status = the worst cell (fail > warn > unknown > ok);
--      value = the newest beat's age in minutes. It writes no incident and no notice: those are the teller's.
--   2. pg_cron: 'health-liveness' every 5 min, its 60 s statement_timeout set in the command (migration 371: a
--      function cannot raise its own budget); 'emit-verification-stale-alert' unscheduled (its function goes in 624).
--
-- ci-allow-ungated: health_liveness it reads the health ledgers and writes one pipeline_check_results row; execute revoked from public, anon and authenticated
--
-- APPLY through apply_migration.yml (--ref the feature branch) before merging wave 2: the canary's second assertion
-- skips itself while the registry has no `liveness` key, and the wave-1 teller already treats `liveness` like any
-- registered check. The post-condition calls the function
-- once and KEEPS that row: it measures the live state exactly as the first cron tick would five minutes later, so the
-- canary and /health see liveness at once. Expect a row every 5 minutes and an onset only for a cell that really fails.
-- ROLLBACK: forward only (rule 1). Every statement is idempotent; a lock-timeout retry re-runs the whole file.

set lock_timeout = '5s';

begin;

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
  v_lane_n integer;
  v_lane_at timestamptz;
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

  select count(*), greatest(max(hc.declared_at), max(r.run_at))
    into v_lane_n, v_lane_at
    from health_checks hc
    left join lateral (select c.run_at from pipeline_check_results c
                        where c.check_key = hc.key order by c.run_at desc limit 1) r on true
   where hc.host = 'realtime-worker';
  if v_lane_n > 0 and 'realtime-worker' = any(v_dead) then
    v_folded := array_append(v_folded, 'health_lane');
  elsif v_lane_n > 0 then
    v_cells := v_cells || jsonb_build_object(
      'subject', 'health_lane', 'portal', null,
      'status', case when now() - v_lane_at > c_lane_dead then 'fail' else 'ok' end,
      'value', round(extract(epoch from now() - v_lane_at) / 60, 1), 'unit', 'min',
      'message', format(case when now() - v_lane_at > c_lane_dead
                             then 'the worker''s health lane has not measured for %s min'
                             else 'the worker''s health lane measured %s min ago' end,
                        round(extract(epoch from now() - v_lane_at) / 60)),
      'facts', jsonb_build_object('keys', v_lane_n, 'newest_at', v_lane_at));
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
  'The pg_cron watcher (migration 620): judges the worker heartbeat, the worker''s health lane, every registered '
  'alarm''s staleness, the API''s matcher lease and alarm cells failing with no incident, and writes one '
  'pipeline_check_results row under key liveness. Never writes an incident or a notice: the teller does.';

revoke all on function public.health_liveness() from public, anon, authenticated;

-- Guarded like 274: the CI replay has no pg_cron. A real failure here is caught by the post-condition below.
do $cron$
begin
  perform cron.schedule(
    'health-liveness',
    '*/5 * * * *',
    $$set statement_timeout='60s'; select public.health_liveness();$$
  );
  perform cron.unschedule(jobid) from cron.job where jobname = 'emit-verification-stale-alert';
exception when others then
  raise notice 'pg_cron unavailable; health-liveness not scheduled (%). Run health_liveness() on another scheduler.', sqlerrm;
end
$cron$;

do $post$
declare
  n_before bigint;
  n_after bigint;
begin
  if has_function_privilege('anon', 'public.health_liveness()', 'execute')
     or has_function_privilege('authenticated', 'public.health_liveness()', 'execute') then
    raise exception '620: a browser role can execute health_liveness()';
  end if;
  if to_regclass('cron.job') is null then
    raise notice '620: no pg_cron here; the schedule is not asserted';
  else
    if not exists (select 1 from cron.job
                    where jobname = 'health-liveness' and schedule = '*/5 * * * *' and active
                      and command like '%statement_timeout%health_liveness()%') then
      raise exception '620: health-liveness is not scheduled every 5 minutes under a statement_timeout';
    end if;
    if exists (select 1 from cron.job where jobname = 'emit-verification-stale-alert') then
      raise exception '620: emit-verification-stale-alert is still scheduled';
    end if;
  end if;
  select count(*) into n_before from pipeline_check_results where check_key = 'liveness';
  perform public.health_liveness();
  select count(*) into n_after from pipeline_check_results where check_key = 'liveness';
  if n_after - n_before < 1 then  -- a cron tick may land beside a re-apply's own row
    raise exception '620: health_liveness() wrote no liveness row';
  end if;
  if not exists (select 1 from health_checks
                  where key = 'liveness' and host = 'pg_cron' and class = 'alarm' and cadence_s = 300
                    and staleness_budget = interval '15 minutes') then
    raise exception '620: the liveness registry row is missing or not (pg_cron, alarm, 300 s, 15 min)';
  end if;
end
$post$;

commit;
