-- 633_health_explicit_cells.sql -- the watcher reads explicit cells only; the data-quality capture stops.
--
-- WHY (docs/design/health-dashboard.md, wave 6; its build contract's D4):
--   wave 6 deletes the ladder's implicit cell: a measured result with no verdict cell stood for one cell named by its
--   key (toolkit/system_alerts._cells). Every check now writes its own cells, OK cells included, and a result without
--   one opens, keeps and recovers nothing. health_liveness()'s unalerted arm spelled the same fallback in SQL (620,
--   re-created by 621), so it would flag "a measured Problem has no incident" for a cell the ladder never opens.
--   measure_plausibility_by_source's comment names three checks this wave retires. The capture-data-quality pg_cron
--   job (migrations 176, 179, 548) fills data_quality_snapshots, which the data_quality gauge replaces (ruling Q5 keeps
--   the table as frozen history for wave 9), and it failed 24 of its runs in the 30 days to 2026-10-19 (statement
--   timeouts at 900 s, pg_cron's job startup timeout), each one a red in the jobs check's cron cell.
--
-- WHAT CHANGES. Additive only.
--   1. public.health_liveness() is re-created from 621's body (live on 2026-10-19, byte for byte) with one arm
--      changed: unalerted reads a sampled result's failing explicit cells only, as the ladder does; the fallback to
--      the key for a failing result without a verdict cell goes, with the then-status only it read. One comment is
--      restated (c_lane_dead's basis: the lane's pass budget is 500 s since wave 6); every other arm is 621's.
--   2. The comment on measure_plausibility_by_source names its wave-6 readers (and the gauge that skips land's
--      divergence) and the scan's measured cost; the rest is 534's.
--   3. The capture-data-quality pg_cron job is unscheduled, by job id looked up from its name, so a re-run is a
--      no-op. data_quality_snapshots and data_quality_by_source stay, unwritten, for wave 9. The table's one reader,
--      the old Health page's dashboard-only field_null_drift rung (scraper_health_checks_mv, migration 354), finds no
--      capture under 20 h old from 20 h after the last one and reads "no baseline yet" until wave 8 replaces that page.
--   Nothing is dropped and no grant changes.
--
-- ci-allow-ungated: health_liveness it reads the health ledgers and the worker heartbeat and writes one pipeline_check_results row; execute revoked from public, anon and authenticated
--
-- APPLY through apply_migration.yml (--ref the feature branch) immediately before merging wave 6. Until the merge
-- deploys, main's ladder still opens the key's own cell for a failing result without cells, which this arm no longer
-- reads: in that window a teller outage on such a key goes unflagged (and nothing is flagged falsely). Applied after
-- the merge instead, 621's arm would read a failing alarm row without cells as unalerted, a false Problem; no alarm's
-- newest row was such a fail on 2026-10-19. The post-condition calls the watcher once and keeps that row, as 620's and
-- 621's did.
-- ROLLBACK: forward only (rule 1). Every statement is idempotent; a lock-timeout retry re-runs the whole file.

set lock_timeout = '5s';

begin;

-- 621's function verbatim, but for its unalerted arm (WHAT CHANGES 1) and c_lane_dead's comment.
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
  'The pg_cron watcher (migrations 620, 621 and 633): judges the worker heartbeat, the worker''s health lane by its '
  'own pass in that heartbeat, every registered alarm''s staleness, the API''s matcher lease and alarm cells failing '
  'with no incident (a result''s explicit verdict cells only, as the ladder reads them), and writes one '
  'pipeline_check_results row under key liveness. Never writes an incident or a notice: the teller does.';

-- create or replace keeps 621's owner and ACL; the revoke is re-stated so the post-condition's privilege check below is
-- self-evidently true.
revoke all on function public.health_liveness() from public, anon, authenticated;

-- 534's text, but for its readers (the sentence naming them, and who skips land's divergence) and the scan's cost.
comment on view public.measure_plausibility_by_source is
  'Per (source, category_main, category_type) plausibility of the per-m2 measure over '
  'ACTIVE listings: the medians, the share of measurable rows the basis floor NULLs, and '
  'the share of rows whose area_m2 diverges from usable_area by more than 10%. Read by '
  'scripts/checks/quality.py once per health pass (the pass memo, scripts/checks/clock.PASS): '
  'the parsing alarm''s measures of new ads and median shift, and the data_quality gauge''s '
  'basis floor, area divergence, estate sum and measure gap (migration 633). Complements '
  'data_quality_by_source, which tests presence only and is structurally blind to a '
  'populated-but-wrong value. Divergence is measured ONLY over rows carrying both areas '
  '(n_area_pairs) and only above a 10% relative band; for category_main = ''pozemek'' area_m2 '
  'is the PLOT by design (Option A) so divergence there is expected and the data_quality gauge '
  'skips those cells. Also publishes the DENOMINATORS every one of those ratios is blind to: '
  'n_area_valued / n_ppm2_valued are the support of the two medians (gate a median on its own '
  'support, never on n_active), and measure_input_gap_share is the share of active rows the '
  'measure has no input for -- the arm that makes a cell with nothing to measure say so instead '
  'of reading clean. W21 appended estate_sum_share: the share of rows carrying all three areas '
  'whose estate_area equals area_m2 + usable_area within 1%, i.e. a side column holding a '
  'DERIVED SUM instead of a measurement -- the class of defect mmreality shipped by reading a '
  'page-computed totalArea as the parcel, which neither a null-check nor the headline-vs-usable '
  'arm can see. ~12 s cold sequential scan (3.5 s warm on 2026-10-19): read it once per health '
  'pass, never per row.';

-- By job id looked up from its name, 620's idiom: cron.unschedule(name) raises when the job is absent, the id form
-- selects no rows, so a re-run is a no-op. Guarded like 620: the CI replay has no pg_cron. A real failure here is
-- caught by the post-condition below.
do $cron$
begin
  perform cron.unschedule(jobid) from cron.job where jobname = 'capture-data-quality';
exception when others then
  raise notice 'pg_cron unavailable; capture-data-quality not unscheduled (%)', sqlerrm;
end
$cron$;

do $post$
declare
  v_def text := pg_get_functiondef('public.health_liveness()'::regprocedure);
  v_comment text := coalesce(obj_description('public.measure_plausibility_by_source'::regclass, 'pg_class'), '');
  n_before bigint;
  n_after bigint;
begin
  if strpos(v_def, 'jsonb_build_array(r.key)') > 0 or strpos(v_def, 'then_status') > 0 then
    raise exception '633: health_liveness() still reads a failing result without a verdict cell as its key''s cell';
  end if;
  if strpos(v_def, 'details -> ''health''') = 0 or strpos(v_def, '''unalerted''') = 0 then
    raise exception '633: health_liveness() lost its health lane or its unalerted cell';
  end if;
  if has_function_privilege('anon', 'public.health_liveness()', 'execute')
     or has_function_privilege('authenticated', 'public.health_liveness()', 'execute') then
    raise exception '633: a browser role can execute health_liveness()';
  end if;
  if strpos(v_comment, 'scripts/checks/quality.py') = 0
     or strpos(v_comment, 'ppm2_median_shift') > 0 or strpos(v_comment, 'ppm2_basis_floor_share') > 0
     or strpos(v_comment, 'area_vs_usable_divergence') > 0 then
    raise exception '633: the measure_plausibility_by_source comment does not name its wave-6 readers';
  end if;
  if to_regclass('cron.job') is null then
    raise notice '633: no pg_cron here; the unschedule is not asserted';
  elsif exists (select 1 from cron.job where jobname = 'capture-data-quality') then
    raise exception '633: capture-data-quality is still scheduled';
  end if;
  -- Kept, as 620's and 621's were: the canary and Health see the new rule at once.
  select count(*) into n_before from pipeline_check_results where check_key = 'liveness';
  perform public.health_liveness();
  select count(*) into n_after from pipeline_check_results where check_key = 'liveness';
  if n_after - n_before < 1 then  -- a cron tick may land beside a re-apply's own row
    raise exception '633: health_liveness() wrote no liveness row';
  end if;
end
$post$;

commit;
