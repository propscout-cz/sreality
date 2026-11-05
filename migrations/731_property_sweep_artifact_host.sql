-- 731_property_sweep_artifact_host.sql -- the property sweep's registry row follows its job to the runner (#2027).
--
-- #2027 moves the `property_sweep` job (scraper/jobs.py, scripts/recompute_property_stats.py) to the job runner and
-- deletes recompute_property_stats.yml. Migration 622's row still reads that workflow's host and cron, and its 38 h
-- budget leaned on the 18 h GitHub delivery slack the jobs check adds to a `github-actions` row. On `job-runner` there
-- is no slack, so the budget becomes 56 h: the fail line stays where 622 put it, sized for a two-run cycle. The
-- cadence names the registry entry, as 702 did for `map_overlay_tiles`. Additive (three values); idempotent. A revert
-- of the move leaves this row reading `job-runner` and 56 h: the same fail line.
-- tests/test_derived_artifacts_stamping.py::test_no_artifact_of_a_runner_job_is_hosted_on_github is the rail.

update public.derived_artifacts
   set host             = 'job-runner',
       cadence          = 'daily (scraper/jobs.py property_sweep)',
       staleness_budget = interval '56 hours'
 where name = 'property_sweep'
   and (host, cadence, staleness_budget)
       is distinct from ('job-runner', 'daily (scraper/jobs.py property_sweep)', interval '56 hours');
