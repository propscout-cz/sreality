-- 742_images_download_given_up.sql -- a photo whose download gave up carries the terminal reason 'download_given_up' (#2510).
--
-- WHY (spec #2503, ticket #2510). "Given up" was a counter threshold every reader re-derived (`download_attempts < 5`
-- in scraper.db.pending_image_downloads and toolkit.photo_assessment), while images_pending_actionable_idx (087) keeps
-- every row with storage_path NULL and unavailable_reason NULL. The given-up rows stayed in that index, so each claim
-- of the images lane (both tiers, about twice a minute) walked all of them to reach the few it can use. Measured
-- 2026-11-15: 26,648 of the index's 26,680 rows are given up (265 on active ads; the newest attempt 2026-10-26), and the
-- active tier read 23,232 buffers to return 24 rows ("Rows Removed by Filter: 26648").
--
-- WHAT. From the code in the same PR, scraper.db.mark_image_attempt writes unavailable_reason = 'download_given_up' in
-- the update that records the fifth failed attempt, beside the reasons already in use (listing_taken_down,
-- source_unavailable, not_an_image), and the readers drop their `download_attempts < 5`. This file labels the rows
-- already given up. A labelled row leaves the partial index, as every other terminal row does. No schema change: the
-- column is free text with no CHECK. The re-arm is unchanged: a detail refetch (listing_write._IMAGES_SQL,
-- db.record_images; refresh_stale_image_urls enqueues active ads with any unstored photo) resets download_attempts and
-- clears unavailable_reason on an unstored row, whatever the reason.
--
-- APPLY BEFORE THE MERGE, through apply_migration.yml --ref the branch (psql -f, statement autocommit). The new claim
-- no longer filters on attempts, so code deployed ahead of this file would give each of the 26.6k rows one more fetch.
-- A row that reaches five attempts between the apply and the deploy gets one more attempt and is then labelled.
-- Idempotent: a second run updates nothing. Not destructive. The new value existed nowhere before, so the inverse is
-- exact: update images set unavailable_reason = null where unavailable_reason = 'download_given_up'.
--
-- COST. One UPDATE of ~26.6k rows of a hot table. The planner reaches them through images_storage_path_idx (the
-- matching SELECT: 9.4 s, 39.7k blocks read, 171k index rows). Row locks only (ROW EXCLUSIVE on the table), and only the
-- detail drain's re-arm of these unstored rows writes them, so the 5 s lock_timeout makes the workflow retry, not queue.
set lock_timeout = '5s';
set statement_timeout = '600s';

update images
   set unavailable_reason = 'download_given_up'
 where storage_path is null
   and unavailable_reason is null
   and download_attempts >= 5;

reset statement_timeout;
reset lock_timeout;
