-- 596: the real-time lane's REJECT LEDGER (PROGRAM.md E943).
--
-- WHY. The lane stores a decided pair only when `store_score.storable` keeps it: the merge and
-- band zones, a row that carries evidence, and the reject tail at or above `store_floor` (0.02).
-- Everything below the floor is DELETED at write (`SqlStore.upsert_pairs`), so the next pass whose
-- probe-key neighbourhood (E71) wants that pair again finds no stored row and scores it again,
-- and again on every pass after. Measured 2026-10-12 14:19Z: a 35-advert pass wanted 157,323
-- pairs, scored 117,795 of them in 845.9 s and wrote 536 rows, so at least 95 % of its scoring
-- was a re-score of a reject it had already decided.
--
-- WHAT. One row per pair decided below the floor: the decision digest of each side (`dd_lo`,
-- `dd_hi`: the listing's facts and its gallery as the engine reads them), the calibration
-- digest, the feature version and the engine digest it was decided under (`engine_digest`: the
-- decision code's sources, the model and the settings, so a deploy that changes how a reject
-- scores re-opens every row). A later pass skips the pair only when all five still match, so
-- the ledger can only ever stand in for a decision that would come out the same. A pair the lane STORES has no ledger row (the write deletes it), so the two tables never
-- hold the same pair. No pruning: at most one row per pair, a re-decision overwrites it, a
-- changed digest leaves it inert, and a fresh seed empties it with the rest of the generation.
--
-- Additive and idempotent; schema `autodedup` only (ruling D8). Size: ~165 bytes a row with its
-- primary key (~182 with the engine digest); 380-460k rows (~70-84 MB) at the end of the current
-- build.

set lock_timeout = '5s';

create table if not exists autodedup.rt_reject_ledger (
  generation         text        not null,
  listing_lo         bigint      not null,
  listing_hi         bigint      not null,
  dd_lo              text        not null,
  dd_hi              text        not null,
  calibration_digest text        not null,
  feature_version    smallint    not null,
  engine_digest      text        not null,
  decided_at         timestamptz not null default now(),
  primary key (generation, listing_lo, listing_hi)
);

comment on table autodedup.rt_reject_ledger is
  'E943: pairs the real-time lane decided below store_floor. Such a reject is not stored in '
  'pairs, so without this row every later pass that wants the pair re-scores it. A pass skips '
  'a pair whose two decision digests, calibration digest, feature version and engine digest '
  'all still match.';

-- RLS posture: service-role only, zero policies, as migration 539 left every table of the lane.
alter table autodedup.rt_reject_ledger enable row level security;

revoke all on autodedup.rt_reject_ledger from anon, authenticated;

reset lock_timeout;
