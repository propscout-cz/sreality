-- 779_reject_ledger_version_keys_nullable.sql -- the reject ledger's three version keys stop being required.
--
-- A stored pair's decision stands until one of its two ads is read again. A rejected pair's (rt_reject_ledger, E943)
-- also had to match the calibration digest, the feature version and the engine digest, so every deploy, re-cut or
-- settings switch voided every reject at once (10-22 crawl, 10-25 manual carry, the 2026-11-25 w34 switch: no pass
-- committed for a day). The lane now reads and writes the ledger by the stored pair's rule
-- (decided_at >= both sides' rt_fp.updated_at) and writes none of the three columns.
--
-- ADDITIVE, apply BEFORE the code merges and UNDER THE BRAKE (interval 0, lease free: the ACCESS EXCLUSIVE would
-- time out against a running pass). Strictly first: the new code on the old shape fails every pass that ledgers
-- a reject (not-null violation, after the work). The new UPSERT leaves the three columns as they were on a row it
-- rewrites, so from the deploy on they describe nothing: read decided_at, never them.
-- The new UPSERT names no version column, and the deployed one still
-- writes all three, so both run against this shape. Metadata only (no rewrite, no scan); ACCESS EXCLUSIVE for
-- an instant, hence the lock_timeout. Idempotent. The columns are dropped by a later migration, once the
-- code that stopped writing them is live. No row is deleted: the rows of older keys are judged by the same rule.
--
-- ROLLBACK: only with no NULL written yet:
--   alter table autodedup.rt_reject_ledger alter column calibration_digest set not null,
--     alter column feature_version set not null, alter column engine_digest set not null;

set lock_timeout = '5s';

alter table autodedup.rt_reject_ledger
  alter column calibration_digest drop not null,
  alter column feature_version    drop not null,
  alter column engine_digest      drop not null;

comment on table autodedup.rt_reject_ledger is
  'E943: pairs the real-time lane decided below store_floor. Such a reject is not stored in '
  'pairs, so without this row every later pass that wants the pair re-scores it. A pass skips '
  'a pair whose two decision digests match and that was decided no earlier than either '
  'side''s last refresh (rt_fp.updated_at): the rule a stored pair stands by. No version keys a row.';

reset lock_timeout;

do $post$
begin
  if exists (select 1 from information_schema.columns
              where table_schema = 'autodedup' and table_name = 'rt_reject_ledger'
                and column_name in ('calibration_digest', 'feature_version', 'engine_digest')
                and is_nullable = 'NO') then
    raise exception '779: a version key of rt_reject_ledger is still NOT NULL';
  end if;
end
$post$;
