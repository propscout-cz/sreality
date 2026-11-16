-- 744_dead_indexes_and_lease_tables.sql -- hygiene (#2514, spec #2503): what nothing reads goes.
--
-- 1. FIFTEEN INDEXES, 643 MB, idx_scan 0 and last_idx_scan NULL (pg_stat_database.stats_reset is NULL: the index
--    counters were never reset; the oldest last_idx_scan of any index is 2026-08-12 16:03, so the window is at least
--    that long; the counters survived the 11-11 and 11-14 restarts: 293 indexes still carry a last scan from before
--    11-11, read 2026-11-15; all fifteen re-read at 0 scans, valid and constraint-free on 2026-11-16 08:35Z). Each is a
--    plain btree (not unique, no constraint, no foreign key), created by 2026-08-10, and named by no code path, view
--    or function body (grep + pg_proc + pg_depend, 2026-11-14). Every write to its table still maintains it:
--      listing_snapshots_content_hash_idx  281 MB  the snapshot append compares the latest hash as a value (rule 2)
--      properties_near_{pop,jobs,youth,overall}_{5,15}km_idx  318 MB  Browse filters these on browse_list; only a
--                                          watchdog renders over properties, and no watchdog sets one
--      properties_mf_yield_keyset_idx  11 MB  a frozen column: every view takes the yield from mf_reference()
--      portal_raw_pages_unparsed_idx   20 MB  no reader asks for unparsed pages
--      ruian_name_homonym             5.4 MB  no gazetteer read filters on homonym_count
--      firm_identities_domain_idx     3.7 MB  firms are matched on (source, source_firm_native)
--      broker_identities_email_idx    2.8 MB  no read looks a broker up by e-mail
--      listing_videos_sreality_id_idx   1 MB  videos are keyed on listing_id (R2)
--    KEPT although never read: brokers_seed_identity_idx and ruian_apc_version (each backs a foreign key's checks),
--    location_pin_audit_mv's three (the audit page; the matview's fate is its own ticket), dedup_pair_audit's eight and
--    listing_visual_matches' two (no write reaches those tables, so they cost nothing), every index under 1 MB (128,
--    5 MB in all, 127 on tables under 10k rows, where a planner never picks an index), browse_list's (rebuilt each
--    tick, so their counters say nothing), and every index read at all, however rarely.
-- 2. TWO LEASE TABLES, retired since 2026-11-03 (#2267, #2273: the maintenance lane and the broker resolve hold their
--    `scheduled_jobs` rows through scraper/job_lease.py): property_maintenance_lease (279) and broker_resolution_lock
--    (192). One released row each, last written about 2.9M transactions ago; no code, view, function, cron job or
--    foreign key names them. DESTRUCTIVE (database skill): the operator said yes to the drop on 2026-11-16 (#2524);
--    the R2 backup still runs first: gh workflow run backup_before_drop.yml -f label=lease-tables-744
--    -f targets="property_maintenance_lease broker_resolution_lock" (or the operator's explicit waiver of it in
--    favour of the revert file, which holds the two rows verbatim). autodedup.rt_lease, the third table of #2113, stays.
--
-- APPLY through apply_migration.yml (--ref the branch), never the MCP: DROP INDEX CONCURRENTLY cannot run in a
-- transaction block. ORDER: the operator's OK (and the backup) -> apply -> the receipt reads OK -> merge. Never merge
-- it unapplied: the hourly migration_drift alarm expects every object a recent migration drops to be gone. A concurrent drop
-- takes SHARE UPDATE EXCLUSIVE (no read or write waits on it) and then waits for every transaction holding a lock on
-- the table (the Browse rebuild reads properties for minutes), so the drops run with lock_timeout 0 under a 1900 s
-- budget (730's idiom). A cancelled drop leaves an INVALID index that writes still maintain, no worse than today; the
-- re-run finishes it. Every statement is idempotent. $pre$ refuses on production if an index here was read since.
--
-- ROLLBACK: migrations/reverts/744_revert_dead_indexes_and_lease_tables.sql (the original definitions, live
-- pg_get_indexdef, rebuilt CONCURRENTLY; the two tables with their grants, comment and rows).

set lock_timeout = '5s';

do $pre$
declare
  dropped text[] := array[
    'listing_snapshots_content_hash_idx',
    'properties_near_pop_5km_idx', 'properties_near_pop_15km_idx',
    'properties_near_jobs_5km_idx', 'properties_near_jobs_15km_idx',
    'properties_near_youth_5km_idx', 'properties_near_youth_15km_idx',
    'properties_near_overall_5km_idx', 'properties_near_overall_15km_idx',
    'properties_mf_yield_keyset_idx', 'portal_raw_pages_unparsed_idx', 'ruian_name_homonym',
    'firm_identities_domain_idx', 'broker_identities_email_idx', 'listing_videos_sreality_id_idx'];
  found text;
begin
  -- pg_depend does not see a function body; a view or a foreign key makes the plain DROP TABLE refuse.
  select string_agg(p.oid::regprocedure::text, ', ') into found
    from pg_proc p
   where p.prosrc ~ '\m(property_maintenance_lease|broker_resolution_lock)\M';
  if found is not null then raise exception '744 refused: % still names a lease table this file drops', found; end if;

  -- Production only: the replay container's counters say nothing.
  if (select count(*) from (select 1 from public.properties limit 100000) probe) = 100000 then
    select string_agg(indexrelname || ' (' || idx_scan || ')', ', ') into found
      from pg_stat_user_indexes
     where schemaname = 'public' and indexrelname = any (dropped) and idx_scan > 0;
    if found is not null then raise exception '744 refused: % read since the audit: find the reader', found; end if;
  end if;
end
$pre$;

drop table if exists public.property_maintenance_lease;
drop table if exists public.broker_resolution_lock;

set lock_timeout = 0;
set statement_timeout = '1900s';

drop index concurrently if exists public.listing_snapshots_content_hash_idx;
drop index concurrently if exists public.properties_near_pop_5km_idx;
drop index concurrently if exists public.properties_near_pop_15km_idx;
drop index concurrently if exists public.properties_near_jobs_5km_idx;
drop index concurrently if exists public.properties_near_jobs_15km_idx;
drop index concurrently if exists public.properties_near_youth_5km_idx;
drop index concurrently if exists public.properties_near_youth_15km_idx;
drop index concurrently if exists public.properties_near_overall_5km_idx;
drop index concurrently if exists public.properties_near_overall_15km_idx;
drop index concurrently if exists public.properties_mf_yield_keyset_idx;
drop index concurrently if exists public.portal_raw_pages_unparsed_idx;
drop index concurrently if exists public.ruian_name_homonym;
drop index concurrently if exists public.firm_identities_domain_idx;
drop index concurrently if exists public.broker_identities_email_idx;
drop index concurrently if exists public.listing_videos_sreality_id_idx;

reset statement_timeout;
reset lock_timeout;
