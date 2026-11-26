-- 775_place_facts_artifact_host.sql -- the registry facts' artifact row follows its load to the runner (#2033).
--
-- #2033 moves `ruian_load` (scraper/jobs.py, location_data/ruian_load.py) to the job runner and deletes
-- location_registry_load.yml. Migration 738's `place_facts` row still reads that workflow's host and cron, and its
-- 40-day budget leaned on the 18 h GitHub delivery slack the jobs check adds to a `github-actions` row. On
-- `job-runner` there is no slack, so the budget becomes 40 days 18 hours and the fail line stays where 738 put it. The
-- cadence names the registry entry, as 702 and 731 did. Additive (three values); idempotent. A revert of the move
-- leaves this row reading `job-runner` with the same fail line.
-- tests/test_derived_artifacts_stamping.py::test_no_artifact_of_a_runner_job_is_hosted_on_github is the rail.

update public.derived_artifacts
   set host             = 'job-runner',
       cadence          = 'monthly (scraper/jobs.py ruian_load)',
       staleness_budget = interval '40 days 18 hours'
 where name = 'place_facts'
   and (host, cadence, staleness_budget)
       is distinct from ('job-runner', 'monthly (scraper/jobs.py ruian_load)', interval '40 days 18 hours');
