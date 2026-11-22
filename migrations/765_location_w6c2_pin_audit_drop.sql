-- 765_location_w6c2_pin_audit_drop.sql -- W6-c2: the old pin audit page's matview, its refresh and its registry row go.
--
-- WHY. Location data quality programme, wave W6-c2 (ticket #2361; docs/design/location-quality/PROGRAM.md). The
-- operator ruled on 2026-11-22, answering Q2, that the position audit on the Location quality page (W6-c1, migration
-- 733, location_position_mv) replaces the old pin audit page. The page and its lib left the SPA in this PR, and they
-- were the relation's only reader: on production (2026-11-22) no view, matview, function body, policy or pg_depend
-- entry names location_pin_audit_mv. DESTRUCTIVE, pre-approved by Q13 with an R2 backup.
--
-- WHAT.
--   pg_cron job refresh-location-pin-audit (25 * * * *, 719's refresh_matview command), first;
--   location_pin_audit_mv (12,273 rows, 40 MB) and its eight indexes, which go with it;
--   its derived_artifacts row (name 'location_pin_audit_mv'), which would otherwise read stale forever.
-- No CASCADE: a plain DROP refuses if anything still depends on the relation.
--
-- APPLY ORDER: AFTER this PR merges and its rollout is green (the browser read this relation directly, so the page
-- must be gone first), from main, through apply_migration.yml, after the R2 backup with its receipt read back. The
-- column form, because a pg_dump of a matview carries its definition and no rows:
--   gh workflow run backup_before_drop.yml -f label=w6c2-pin-audit-765 -f targets="location_pin_audit_mv:listing_id,property_id,sreality_id,source,source_id_native,source_url,category_main,category_type,disposition,area_m2,price_czk,is_active,first_seen_at,last_seen_at,display_label,country_status,granularity,match_confidence,resolver_version,resolved_at,has_row,has_claims,claims_now,sibling_has_pin,quality,state,refreshed_at"
-- Not at minute :25, when the old refresh runs. Until the apply, migration_drift warns
-- "Drop not applied" for this file. Every statement is idempotent: a re-run is a no-op.
-- VERIFY:
--   select to_regclass('public.location_pin_audit_mv');                                   -- null
--   select count(*) from cron.job where jobname = 'refresh-location-pin-audit';          -- 0
--   select count(*) from public.derived_artifacts where name = 'location_pin_audit_mv';  -- 0
-- ROLLBACK: forward only (rule 1). migrations/reverts/765_revert_location_w6c2_pin_audit_drop.sql holds the live
-- definition, indexes, grants, cron line and registry row of 2026-11-22, so the relation can be rebuilt.

set lock_timeout = '5s';
set statement_timeout = '120s';

do $cron$
begin
  if to_regclass('cron.job') is null then
    raise notice '765: no pg_cron here (the CI replay); nothing to unschedule';
  elsif exists (select 1 from cron.job where jobname = 'refresh-location-pin-audit') then
    perform cron.unschedule('refresh-location-pin-audit');
  end if;
end
$cron$;

drop materialized view if exists public.location_pin_audit_mv;
delete from public.derived_artifacts where name = 'location_pin_audit_mv';

do $post$
begin
  if to_regclass('public.location_pin_audit_mv') is not null then
    raise exception '765 did not land: location_pin_audit_mv is still there';
  end if;
  if exists (select 1 from public.derived_artifacts where name = 'location_pin_audit_mv') then
    raise exception '765 did not land: the location_pin_audit_mv registry row is still there';
  end if;
  if to_regclass('cron.job') is null then
    raise notice '765: no pg_cron here; the unschedule is not asserted';
  elsif exists (select 1 from cron.job where jobname = 'refresh-location-pin-audit') then
    raise exception '765 did not land: refresh-location-pin-audit is still scheduled';
  end if;
end
$post$;

reset statement_timeout;
reset lock_timeout;
