-- 673_broker_stats_producer_worker_only.sql -- the broker leaderboard's registry row stops naming a deleted workflow.
--
-- Actions-exit W0 (#2010) deleted `broker_resolution.yml`, the throttled GitHub backstop of the worker's
-- broker_maintenance lane (migration 579 named both as the producer). The lane is now the only runtime of
-- `scripts.resolve_brokers.run_incremental_pass`, so the producer is that script alone; host
-- (`realtime-worker`), cadence and the 8 h budget are unchanged. Additive (one text value); idempotent.
-- tests/test_derived_artifacts_stamping.py::test_every_registry_row_s_producer_exists is the rail.

update derived_artifacts
   set producer = 'scripts/resolve_brokers.py'
 where name = 'broker_region_type_stats'
   and producer is distinct from 'scripts/resolve_brokers.py';
