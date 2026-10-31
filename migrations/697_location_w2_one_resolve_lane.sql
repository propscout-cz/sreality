-- 697_location_w2_one_resolve_lane.sql
--
-- Location data quality programme, W2: ONE resolve lane (operator ruling Q9, 2026-10-31).
-- The Railway worker is the only runtime of the resolver drain; it sweeps the corpus itself
-- whenever its (resolver version, registry label) pair changes, evidence before backfill,
-- active listings before inactive. The GitHub `location_resolve.yml` lane, the lane's on/off
-- flag and every hand dispatch are deleted with it (docs/design/location-quality/PROGRAM.md).
--
--   1. dirty_locations.reason: 8 -> 3. Code writes claim_insert (claims_intake, contracts.retract),
--      operator_edit (operator_corrections) and full_sweep (the sweep); the other five
--      (resolution_written, registry_version, policy_version, collision_recompute,
--      property_grouping) are written by nothing. A row a hand step left under a dead label
--      carries no evidence, so it is relabelled backfill before the CHECK shrinks.
--   2. The claim order — (reason = 'full_sweep'), next_eligible_at, listing_id — gets the index
--      that serves it; the old (next_eligible_at, enqueued_at) index served a range plus a sort.
--   3. The lease row names its one runtime.
--   4. The lane's flag is gone: `realtime_location_resolve_interval_seconds` is the one control
--      (absent = 15 s, 0 = idle). Receipt of the deleted row: realtime_location_resolve_enabled =
--      true, set by hand 2026-09-10 10:33Z, never seeded. Pre-approved destructive steps (Q13).
--
-- APPLY AFTER THE W2 DEPLOY IS CONFIRMED, never before the merge: the old worker reads the flag
-- (absent = dark), and the GitHub backstop is already gone on main. The new code needs none of
-- this file to run: the ORDER BY plans with or without the index and the CHECK is a subset.
-- The queue holds 0 rows on a quiet day and at most ~13k, so the plain CREATE INDEX is
-- milliseconds; do not apply mid-sweep.

begin;

set local lock_timeout = '5s';

update dirty_locations set reason = 'full_sweep'
 where reason not in ('claim_insert', 'operator_edit', 'full_sweep');

alter table dirty_locations drop constraint if exists dirty_locations_reason_check;
alter table dirty_locations add constraint dirty_locations_reason_check
  check (reason in ('claim_insert', 'operator_edit', 'full_sweep'));

create index if not exists dirty_locations_claim
  on dirty_locations ((reason = 'full_sweep'), next_eligible_at, listing_id);
drop index if exists dirty_locations_due;

update scheduled_jobs set runner = 'railway-realtime-worker'
 where job_name = 'location_resolve_incremental' and runner = 'github_actions';

delete from app_settings where key = 'realtime_location_resolve_enabled';

commit;
