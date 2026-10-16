-- 604: each advert's DECISION DIGEST on the real-time lane's fingerprint row (PROGRAM.md E960).
--
-- WHY. A pass re-read the facts (listing, gallery, tags) of its claim's whole probe-key
-- neighbourhood and of every pair endpoint (E71), so its read grew with the store: a 500-advert
-- claim read 39.3k of the 39.7k adverts on 2026-10-15. Retrieval needs only a neighbour's posted
-- keys (`autodedup.fp_key`) and guard columns (this table), and the reject ledger (E943,
-- migration 596) needs only each side's decision digest. Kept here, a side the pass does not
-- decide is never read.
--
-- WHAT. `dd`: `incremental.decision_digest` of the advert's facts as the lane last read them,
-- written by every refresh and by a pass that read an advert without refreshing it (a claim
-- whose digest moved without its fingerprint, or a row written before this column). NULL on
-- every row written before it: the lane reads such an advert once, when a ledger lookup needs
-- its digest, and writes it.
--
-- Additive and idempotent; schema `autodedup` only (ruling D8). Adding a nullable column with
-- no default rewrites nothing. ~17 bytes a row (40k rows today, 157k for Prague).
--
-- APPLY UNDER THE LANE'S BRAKE, BEFORE THE CODE MERGES: `realtime_autodedup_interval_seconds` 0
-- and `autodedup.rt_lease` clear. The ALTER needs ACCESS EXCLUSIVE on `rt_fp`, and a running pass
-- holds row locks on it for up to 1,050 s, past the 5 s lock_timeout below. Until it is applied,
-- the code that reads the column skips every pass (`store_absent`).

set lock_timeout = '5s';

alter table autodedup.rt_fp add column if not exists dd text;

comment on column autodedup.rt_fp.dd is
  'E960: decision_digest of the advert''s facts as the lane last read them; the reject '
  'ledger''s key for a side a pass does not read. NULL until the lane reads the advert again.';

reset lock_timeout;
