-- 683_rent_map_cells_producer.sql -- the rent map's registry row names its scripts, not a workflow (#2025).
--
-- Migration 565 declared `rent_map_cells`' producer as `api/rent_map.py + fetch_rent_map.yml`. A workflow is a host,
-- and the Actions exit (#2007) moves hosts: #2025 moves the `rent_map` job (scraper/jobs.py, scripts/fetch_rent_map.py)
-- to the job runner and deletes fetch_rent_map.yml. The script is the producer on either host, so neither that move
-- nor its revert touches this row again. host (`api-request`) and the 40-day budget are unchanged; the cadence text
-- names the registry entry instead of the workflow's cron. Additive (two text values); idempotent.
-- tests/test_derived_artifacts_stamping.py::test_no_registry_producer_names_a_workflow_file is the rail.

update public.derived_artifacts
   set producer = 'api/rent_map.py + scripts/fetch_rent_map.py',
       cadence  = 'on-demand + monthly (scraper/jobs.py rent_map)'
 where name = 'rent_map_cells'
   and producer is distinct from 'api/rent_map.py + scripts/fetch_rent_map.py';
