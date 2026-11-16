-- 740: browse-list-rebuild hourly at :45 (was */15), and its registry row to match (#2505, spec #2503).
--
-- WHY. The rebuild is the largest single reader measured on Large: 96 runs in the 24 h read on 2026-11-14, 320 s
-- on average and 877 s at most; 23.5 % of shared-block reads in the one-hour pg_stat_statements diff of 11-13, and
-- 58 % of temp writes since the stats began. Hourly keeps 24 of the 96. Operator ruling 2026-11-14: hourly, paired
-- with a dirty_properties entry for every property the 6 h cap publishes. That half is code in the same PR
-- (scripts/recompute_property_stats._enqueue_published): each maintenance pass queues what browse_projection serves
-- and browse_list lacks, and its drain patches the row in, so a property the cap releases reaches Browse within a
-- pass (~30 s) instead of at the next */15 rebuild (up to ~22 min on Large). Every other change already patches
-- browse_list in the drain; what reaches Browse ONLY through this rebuild (a resolver re-place of an older property,
-- the operator's location corrections included; city proximity; tom_days; a patch a swap superseded) now waits up to
-- ~65 min instead of ~22.
--
-- :45. Clear of refresh-location-position (:07, at most 700 s in the same 24 h), place-served-counts (:37, which reads
-- browse_list, so it must not meet the swap) and recompute-city-proximity (:40); beside ingest-bulk-days (:50, 32 s on
-- average). By start minute over 48 h the :45 runs were the cheapest: 330 s against 360 s for all.
--
-- A NEW JOB, NOT ONLY A NEW SCHEDULE. Health judges a pg_cron job by its own history: the jobs check calls it late past
-- 3 x its 7-day median start gap, and the capacity arm reads that gap as its interval. Kept under its jobid, the hourly
-- job would read "no start for 46 min, over 3x its median start gap of 15 min" for a quarter of every hour, and be
-- judged against a 900 s interval, until hourly gaps were the median (~5.6 days). Unscheduled and scheduled again it
-- gets a fresh jobid: lateness is Not measured for its first 5 starts, then judged at 3 h. The price: the capacity
-- arm's relative reference (the job's lowest weekly p50 since 2026-10-17) starts over and is Not measured until a whole
-- week of hourly runs exists. Same name, command and owner; the old runs stay under the old jobid until the 90-day trim.
--
-- BUDGET. The stamp is the run's start (658), so between two runs its age reaches 60 min plus the build, at most the
-- command's 1800 s statement_timeout: 130 minutes, as the three hourly matview rows carry. One failed run reads stale
-- only while the run after it builds past 10 minutes; two always do.
--
-- APPLY after the PR's code is live on the realtime worker (the enqueue is what keeps new properties as fresh as
-- before), through apply_migration.yml. Idempotent: a re-run finds the job hourly and leaves it, and the UPDATE writes
-- only a differing row. No hot relation is locked: cron.job, and the one derived_artifacts row each rebuild stamps.
--
-- ROLLBACK (forward, a new migration): the same unschedule and cron.schedule with '*/15 * * * *' and this command, and
-- the registry row back to '*/15 * * * *' and 45 minutes. The enqueue needs none: it is right at any cadence.

set lock_timeout = '5s';

-- Guarded like 620 only where pg_cron is absent (the CI replay). Where it exists a failure re-raises: psql's
-- ON_ERROR_STOP then halts before the registry UPDATE below (statements autocommit, so a notice here would let the
-- row read hourly over a */15 job), and a lock timeout keeps the text apply_migration.yml's retry looks for.
do $cron$
begin
  perform cron.unschedule(jobid) from cron.job
   where jobname = 'browse-list-rebuild' and schedule <> '45 * * * *';
  perform cron.schedule(
    'browse-list-rebuild',
    '45 * * * *',
    $$set statement_timeout='1800s'; select public.rebuild_browse_list();$$
  );
exception when others then
  if to_regclass('cron.job') is not null then
    raise;
  end if;
  raise notice 'pg_cron unavailable; browse-list-rebuild not rescheduled (%)', sqlerrm;
end
$cron$;

update public.derived_artifacts
   set cadence = '45 * * * *', staleness_budget = interval '130 minutes'
 where name = 'browse_list'
   and (cadence, staleness_budget) is distinct from ('45 * * * *', interval '130 minutes');

do $post$
begin
  if to_regclass('cron.job') is null then
    raise notice '740: no pg_cron here; the schedule is not asserted';
  elsif not exists (select 1 from cron.job
                     where jobname = 'browse-list-rebuild' and schedule = '45 * * * *' and active
                       and command = $$set statement_timeout='1800s'; select public.rebuild_browse_list();$$) then
    raise exception '740: browse-list-rebuild is not scheduled at :45 under its 1800 s statement_timeout';
  end if;
  if exists (select 1 from public.derived_artifacts
              where name = 'browse_list'
                and (cadence, staleness_budget) is distinct from ('45 * * * *', interval '130 minutes')) then
    raise exception '740: derived_artifacts.browse_list does not read 45 * * * * and 130 minutes';
  end if;
end
$post$;
