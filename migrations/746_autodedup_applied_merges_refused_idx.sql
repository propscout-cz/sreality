-- 746_autodedup_applied_merges_refused_idx.sql -- autodedup.applied_merges (#2525, spec #2503; the operator's
-- "proceed with #2525" of 2026-11-16, read as the recommended option: keep E41's refused-set read, index it).
--
-- WHY: once a pass the engine reads the chokepoint refusals of its candidates' member sets
--   (autodedup/apply_sql.py LEDGER_HISTORY_SQL, E41: `not dry_run and outcome = 'refused' and generation = $1 and
--   member_ids && $2`). No index serves `outcome = 'refused'`, so production plans it as a parallel seq scan of the
--   339 MB heap (344,173 rows, 337,305 of them generation 'rt'; plain EXPLAIN, 2026-11-16) to return nothing: 0 refused
--   rows ever. Gate #2527 read it at 1.0 s a call warm on 2XL (249 calls, 249 s, 0 blocks read in 2.2 h, ~113 s/h);
--   since the pg_stat_statements reset of 2026-08-11 it ran 14,244 times for 141,221 s (mean 9.9 s, max 109 s) and
--   read 23.1M blocks from disk.
--
-- THE INDEX: a partial btree on (generation) under the query's two constant predicates, spelled as the query spells
--   them. The predicate names no parameter, so the planner proves it from the query text in a custom plan and a
--   generic (prepared) one alike; generation is the index condition. Empty today: one 8 kB page. Only an insert of a
--   live refused row (a category refusal at the chokepoint, rare) adds an entry. The ledger's two updates
--   (apply_sql.py: undone_at, undone_by, undo_result) change no column it names, so no update loses HOT eligibility.
--   Replayed locally (PG 17, the table and its indexes, 344k rows in production's generation and outcome mix, a
--   151-id array): before, a parallel scan, 39.9k buffers, 0.52-0.56 s; after, `Index Scan using
--   autodedup_applied_merges_refused_idx`, Index Cond generation, 2 buffers, 0.04 ms, custom and generic plan alike.
--   No code changes: the read's text stays byte-identical. RC_OUTCOME_HISTORY_SQL (every outcome) is not served by it.
--   tests/test_autodedup_refused_read_plan.py EXPLAINs the read on the replayed schema, bound and generic.
--
-- APPLY through apply_migration.yml (--ref the branch), never the MCP: CREATE INDEX CONCURRENTLY cannot run in a
-- transaction block, and every MCP path wraps its payload in one. WHEN: inside the merge coordinator's brake for the
-- Large retry, BEFORE the operator clicks the resize (still on 2XL, the engine stopped, so the build waits on no
-- engine transaction and Large starts with the read already free), and finished, receipt OK, before that click. A
-- resize restarting the database mid-build leaves an INVALID index (the re-run below handles it, the work is lost),
-- and a build during the trial changes what the trial measures. Window missed: apply after the trial ends, never
-- during it. ORDER: apply -> the receipt reads OK -> the coordinator's light -> merge. No code names the index, so
-- either order is safe for the code; merged unapplied, the hourly migration_drift cell reads Watch until applied.
-- The build takes SHARE UPDATE EXCLUSIVE (the engine's ledger writes do not wait on it), scans the heap twice and
-- waits for every older transaction, so it runs with lock_timeout 0 under a 1900 s budget (730's idiom); a cancelled
-- build's INVALID leftover is moved aside and dropped CONCURRENTLY on the re-run. Every statement is idempotent.
-- $post$ refuses an index that is not valid (the receipt probes existence only).
--
-- ROLLBACK: drop index concurrently if exists autodedup.autodedup_applied_merges_refused_idx;

set lock_timeout = '5s';

do $invalid$
begin
  if exists (select 1 from pg_index i
              where i.indexrelid = to_regclass('autodedup.autodedup_applied_merges_refused_idx')
                and not i.indisvalid) then
    alter index autodedup.autodedup_applied_merges_refused_idx
      rename to autodedup_applied_merges_refused_idx_invalid;
    raise notice '746: an INVALID autodedup_applied_merges_refused_idx from a cancelled build moved aside';
  end if;
end
$invalid$;

set lock_timeout = 0;
set statement_timeout = '1900s';

drop index concurrently if exists autodedup.autodedup_applied_merges_refused_idx_invalid;

create index concurrently if not exists autodedup_applied_merges_refused_idx
  on autodedup.applied_merges (generation)
  where outcome = 'refused' and not dry_run;

reset statement_timeout;
reset lock_timeout;

do $post$
begin
  if not exists (select 1 from pg_index i
                  where i.indexrelid = to_regclass('autodedup.autodedup_applied_merges_refused_idx')
                    and i.indisvalid) then
    raise exception '746: autodedup.autodedup_applied_merges_refused_idx is missing or INVALID';
  end if;
end
$post$;
