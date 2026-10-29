-- 689: the index-slice ledger goes; the walk cycle reads scrape_runs.by_category (#2020, operator
-- ruling 2026-10-29).
--
-- portal_index_slices (migration 454) remembered when each idnes slice was last walked, so a walk
-- cut by its budget began next time where coverage was oldest. That memory is now the runner's,
-- for every portal: each walked unit's by_category entry, persisted as the unit ends in the same
-- statement as its page count, carries its nomination scope and its reached-end verdict, and
-- scraper/portal_runner orders the next run least recently walked group first and resumes a cut
-- walk cycle from it. Health's walks checks already read the same entries. Two records of "this
-- unit reached the end" could disagree; one cannot.
--
-- Live on 2026-10-29: 300 rows (idnes 150, written until this PR; ceskereality 140 and mmreality
-- 10, unread since 2026-10-19). No function, view or materialized view names the table.
--
-- DESTRUCTIVE: applied only after a backup of the table (backup_before_drop.yml, label
-- portal-index-slices, to R2), and only once the PR that removes its last reader and writer
-- (scraper/db.py, scraper/idnes_main.py) is live and no idnes walk started before it is running.

set lock_timeout = '5s';

drop table if exists public.portal_index_slices;

reset lock_timeout;
