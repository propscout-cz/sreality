-- 719_location_audit_waterfall_drop.sql -- W1 finish: the audit waterfall and the pin-audit counters leave.
--
-- WHY. Location data quality programme, wave W1 (ticket #2218; docs/design/location-quality/PROGRAM.md). Ruling Q7:
-- nothing measured twice. The location_town_coverage scorecard (migration 698) holds the one set of location numbers;
-- W1's frontend half deleted the only readers of these objects, the audit page's waterfall panel and overview counters.
-- Operator ruling W1-c, 2026-11-01: drop them.
--
-- WHAT.
--   location_audit_waterfall (table, RLS policy, indexes, grant), its derived_artifacts row, and
--   refresh_location_audit_waterfall(), its producer;
--   location_pin_audit_summary(), the overview counters' RPC;
--   refresh_location_pin_audit_mv(), whose only remaining job was that refresh plus its one stamp: pg_cron's
--   refresh-location-pin-audit now calls the chokepoint public.refresh_matview (migration 578), which refreshes
--   CONCURRENTLY and stamps the artifact.
-- KEPT: location_pin_audit_mv and its indexes, the audit LIST's relation, until W6 (design: w1-page-reads-cells.md).
--
-- DESTRUCTIVE, on derived objects only: the table is a 9-row hourly rewrite, its backup the receipt of its rows in
-- this PR's body. Nothing on main reads these objects, and production's pg_proc, views and
-- pg_depend name none of them but the two producers dropped here.
--
-- APPLY: only on the operator's "yes, apply it". Not around minute :25, when the old job runs. Every statement is
-- idempotent, so a lock_timeout retry resumes from statement 1.
-- VERIFY:
--   select command from cron.job where jobname = 'refresh-location-pin-audit';   -- the chokepoint
--   select producer from derived_artifacts where name = 'location_pin_audit_mv'; -- refresh_matview
--   select to_regclass('public.location_audit_waterfall');                       -- null
--   then after the next :25, derived_artifacts.last_succeeded_at for location_pin_audit_mv moves.

set lock_timeout = '5s';

do $cron$
begin
  perform cron.schedule(
    'refresh-location-pin-audit',
    '25 * * * *',
    $$set statement_timeout='900s'; select public.refresh_matview('location_pin_audit_mv');$$
  );
exception when others then
  raise notice 'pg_cron unavailable; refresh-location-pin-audit not re-pointed (%)', sqlerrm;
end
$cron$;

update derived_artifacts set producer = 'refresh_matview' where name = 'location_pin_audit_mv';
delete from derived_artifacts where name = 'location_audit_waterfall';

drop function if exists refresh_location_pin_audit_mv();
drop function if exists refresh_location_audit_waterfall();
drop function if exists location_pin_audit_summary();
drop table if exists location_audit_waterfall;
