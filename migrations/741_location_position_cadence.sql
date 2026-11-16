-- 741_location_position_cadence.sql -- the position snapshot four times a day, not hourly (#2506, spec #2503).
--
-- WHY. location_position_mv (migration 733) recomputes every ad's point kind in full, ~417k rows: 97 s a run on 2XL,
-- 378 s on Large (mean of 189 runs), 600-900 s between 00 and 07Z, and one 900 s statement timeout (2026-11-13 04:07).
-- Hourly, it was ~83 % of the refresh_matview time in the measured hour. Two views read it and nothing else does (no
-- name in api/, toolkit/, scraper/ or scripts/): location_position_audit_public, the admin list on Location Quality, and
-- location_quality_by_source, whose `position` grid the six-hourly location_town_coverage gauge stores as a fact and
-- never judges. Between snapshots an ad re-resolved since reads `unclassified` (0-196 of ~400k in every reading since
-- 2026-11-06, hourly).
--
-- WHAT. Two values; the command text is 733's, verbatim.
--   1. pg_cron `refresh-location-position`: '7 * * * *' -> '7 8,13,18,23 * * *' (UTC; gaps 5, 5, 5 and 9 h; nothing in
--      00-07Z; the minute stays off the old pin-audit job's :25). Unscheduled and scheduled anew, not upserted in place:
--      the jobs check measures a job's lateness against its median start gap over its newest 7 days, so keeping job 36's
--      hourly history would read "no start for 3 h, over 3x its 1 h gap" for about six days. The new job id starts that
--      history fresh ("not measured" until 5 starts). A re-run finds the target schedule and changes nothing.
--   2. derived_artifacts.location_position_mv: the cadence names the schedule; the budget 130 min -> 14 h 30 min covers
--      any ONE missed run: the longest two gaps in a row are 14 h (a missed 23:07 leaves 18:07 -> 08:07, a missed 08:07
--      leaves 23:07 -> 13:07), plus a run of at most 900 s. Two missed runs in a row read stale, as two did before.
--
-- APPLY. Through apply_migration.yml (psql -f, statement autocommit; both statements idempotent). Independent of the code
-- deploy: either order. Apply outside :07-:22: a running refresh stamps the same derived_artifacts row, and if the UPDATE
-- meets lock_timeout the schedule is already changed while the budget is still 130 min; re-run it then (the cron half
-- is a no-op). ROLLBACK (forward, a new migration): schedule '7 * * * *' again and restore 130 minutes.
-- VERIFY:
--   select jobid, schedule, command from cron.job where jobname = 'refresh-location-position';  -- a new jobid
--   select cadence, staleness_budget from derived_artifacts where name = 'location_position_mv';
--   then after the next slot, derived_artifacts.last_succeeded_at for location_position_mv moves.

set lock_timeout = '5s';

do $cron$
begin
  if to_regclass('cron.job') is null then
    raise notice '741: no pg_cron here; refresh-location-position not rescheduled';
  elsif not exists (select 1 from cron.job
                     where jobname = 'refresh-location-position' and schedule = '7 8,13,18,23 * * *') then
    perform cron.unschedule(jobid) from cron.job where jobname = 'refresh-location-position';
    perform cron.schedule(
      'refresh-location-position',
      '7 8,13,18,23 * * *',
      $$set statement_timeout='900s'; select public.refresh_matview('location_position_mv');$$
    );
  end if;
end
$cron$;

update public.derived_artifacts
   set cadence          = '7 8,13,18,23 * * *',
       staleness_budget = interval '14 hours 30 minutes'
 where name = 'location_position_mv'
   and (cadence, staleness_budget) is distinct from ('7 8,13,18,23 * * *', interval '14 hours 30 minutes');
