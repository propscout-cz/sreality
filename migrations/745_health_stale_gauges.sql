-- 745_health_stale_gauges.sql -- Health (#2512, spec #2503): the pg_cron watcher's stale:<key> cell judges every
-- registered check, gauge or alarm, on that key's own staleness budget.
--
-- WHY: since 620 the stale arm read class = 'alarm' only, so a gauge that stopped being measured just read Not measured
--   on the page and no one was told. On Large the location scorecard (location_town_coverage, rule 25's gauge, every
--   6 h) ran out of its budget in 7 of its last 8 runs: 34.8 h unmeasured on 11-12/13 and 30.9 h at 11-14 23:07Z, with
--   no incident; data_quality went 23.2 h. Each key already carries a budget derived from its own cadence
--   (health_checks.staleness_budget, projected by scripts/verify_pipeline.py: three cadences on the worker, cadence +
--   18 h on GitHub), so a 6-hourly gauge stales at 18 h, never at an alarm's 30 min.
--
-- WHAT CHANGES. One function replace: no table, column, view or grant. Rows written: the post-condition's one liveness
-- row, kept as 620's to 682's were (its probe row is rolled back).
--   * public.health_liveness() is re-created from the live body, 682's byte for byte (the precondition refuses any
--     other), with the stale arm's `hc.class = 'alarm' and` gone and one comment line added. The unalerted arm keeps its
--     class filter. A stale gauge's cell is a liveness cell like an alarm's: one incident, reminded and e-mailed by
--     the canary like an alarm's, folded into its worker's cell while that worker is dead, closed on a measured OK.
--   Read on production 11-14 23:07Z: 1 gauge stale now (location_town_coverage, 30.9 h of 18 h); over 21 days, with
--   text_extraction at the 6 h cadence #2512 gives it, 4 closed gaps past budget, one per gauge, besides that open one
--   (7 for text_extraction alone at its old 1 h cadence).
--
-- APPLY through apply_migration.yml (--ref the feature branch) after the worker deploys #2512's registry: the code
-- reads nothing this changes, so either order is safe, but applied first, text_extraction still stales at its old
-- 3 h budget. Applied, the next watcher tick (every 5 min) tells location_town_coverage if it is still unmeasured.
-- ROLLBACK: forward only (rule 1): a new file re-creating 682's body. Every statement is idempotent: a re-run passes the
-- precondition (this file's own body), and a lock-timeout retry re-runs the whole file.

set lock_timeout = '5s';

begin;

-- One overload, and its body is 682's or this file's own (a re-run): a watcher that drifted is never regressed.
do $pre$
begin
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'health_liveness'), '')
     not in ('b7534724a3aa390f0f8cf20f89bf6a38', '67ce571d382f2e390ef175673ba3489c') then
    raise exception '745 refused: health_liveness() is neither 682''s body nor this file''s (a re-run), or has two '
                    'overloads: rebuild this file from the live body';
  end if;
end
$pre$;

-- 682's function verbatim, but for the stale arm's class filter.
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
            -- Every class since 745: a gauge stales on its own budget too (three of its cadences on the worker).
            left join lateral (select c.run_at from pipeline_check_results c
                                where c.check_key = hc.key and c.status <> 'unknown'
                                  and c.run_at >= hc.declared_at
                                order by c.run_at desc limit 1) r on true
           where hc.key <> 'liveness'
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
  'The pg_cron watcher (migrations 620, 621, 633, 655, 675, 682 and 745): judges each worker heartbeat as its own '
  'subject (worker/<name>), the worker''s health lane by its own pass in that heartbeat, each job-runner job '
  '(job/<name>) and the fleet (job-runner) through health_job_cells(), every registered check''s staleness (alarm or '
  'gauge, since 745) and alarm cells failing with no incident (a result''s explicit verdict cells only, as the ladder '
  'reads them), and writes one pipeline_check_results row under key liveness. Never writes an incident or a notice: '
  'the teller does.';

-- create or replace keeps 682's owner and ACL; the revoke is re-stated so the post-condition's privilege check below is
-- self-evidently true.
revoke all on function public.health_liveness() from public, anon, authenticated;

do $post$
declare
  v_def text := pg_get_functiondef('public.health_liveness()'::regprocedure);
  n_before bigint;
  n_after bigint;
  v_cells jsonb;
begin
  if strpos(v_def, 'hc.class = ''alarm'' and hc.key <> ''liveness''
             and health_stale(') > 0 then
    raise exception '745: health_liveness() still judges the staleness of alarms only';
  end if;
  if strpos(v_def, 'public.health_job_cells(c_worker_dead)') = 0 or strpos(v_def, '''subject'', ''worker/'' || w.worker') = 0
     or strpos(v_def, 'details -> ''health''') = 0 or strpos(v_def, '''unalerted''') = 0
     or strpos(v_def, '''stale:''') = 0 or strpos(v_def, 'cell:liveness:') = 0 then
    raise exception '745: health_liveness() lost a worker, health lane, job, stale, unalerted or recovery arm';
  end if;
  if has_function_privilege('anon', 'public.health_liveness()', 'execute')
     or has_function_privilege('authenticated', 'public.health_liveness()', 'execute') then
    raise exception '745: a browser role can execute the watcher';
  end if;
  -- A rolled-back probe: a gauge declared two days ago and never measured is told, on its own one-hour budget.
  begin
    insert into health_checks (key, domain, class, host, cadence_s, staleness_budget, label, declared_at)
    values ('_745_probe', 'platform', 'gauge', 'github-actions', 1200, interval '1 hour', '_745_probe',
            now() - interval '2 days');
    perform public.health_liveness();
    select details -> 'cells' into v_cells from pipeline_check_results
     where check_key = 'liveness' and run_at = now() order by id desc limit 1;
    if not exists (select 1 from jsonb_array_elements(v_cells) c
                    where c ->> 'subject' = 'stale:_745_probe' and c ->> 'status' = 'fail') then
      raise exception '745: the watcher does not tell a stale gauge: %', v_cells;
    end if;
    raise exception '745 probe rolled back';  -- the sentinel: undoes the probe's writes
  exception when raise_exception then
    if sqlerrm <> '745 probe rolled back' then
      raise;
    end if;
  end;
  if exists (select 1 from health_checks where key = '_745_probe') then
    raise exception '745: the probe row survived its rollback';
  end if;
  -- Kept, as 620's to 682's were: the canary and Health see the stale gauges at once.
  select count(*) into n_before from pipeline_check_results where check_key = 'liveness';
  perform public.health_liveness();
  select count(*) into n_after from pipeline_check_results where check_key = 'liveness';
  if n_after - n_before < 1 then  -- a cron tick may land beside a re-apply's own row
    raise exception '745: health_liveness() wrote no liveness row';
  end if;
end
$post$;

commit;
