-- 677: each advert's PORTAL, AREA BASIS and BLOCK KEY on the real-time lane's fingerprint row.
--
-- WHY. The pair veto's area clause gains a dial, `area_veto_basis_floor_exempt` (portal -> max
-- gap): realitymix prints "celková podlahová plocha" (`listings.area_basis = 'floor'`), which
-- may hold the cellar, balcony or terrace its partner leaves out, so an area gap up to the cap
-- is not a veto there (research: experiments/portal_columns.md section 3.2 and V2, 13 operator
-- SAME pairs / 9 properties released at 0 DIFFERENT with a 15 % cap). Retrieval vetoes a
-- neighbour on its stored guard columns without reading the advert (E17, E960), so the two
-- facts the rule reads must sit beside the five it already reads.
--
-- WHAT. `source` (`listings.source`) and `area_basis` (`listings.area_basis`) as the lane last
-- read them, written by every refresh. NULL on every row written before this migration: the
-- exemption does not fire for such a side (the veto stands, as under w31) until the lane
-- refreshes the advert. A settings switch to a row carrying the dial is a fresh `rt_seed`,
-- which rewrites every row of the generation, so nothing is backfilled here.
--
-- `block_key` (`fingerprint.block_key_of`: `c<cast_obce_kod>`, else `o<obec_kod>`, else empty) is
-- the block the advert's postings were keyed under. An inactive advert appends no snapshot, so a
-- later re-resolution of its location reached no feed and its keys stayed those of its first
-- claim (research: experiments/absent_attribution.md M3 and V3, 3,105 rows). The drift sweep
-- re-claims an in-scope advert whose stored key is not today's. NULL on a row written before this
-- migration: the sweep never selects it, and the fresh `rt_seed` fills every row.
--
-- Additive and idempotent; schema `autodedup` only (ruling D8). Adding nullable columns with no
-- default rewrites nothing.
--
-- APPLY UNDER THE LANE'S BRAKE, BEFORE THE CODE MERGES: `realtime_autodedup_interval_seconds` 0
-- and `autodedup.rt_lease` clear. The ALTER needs ACCESS EXCLUSIVE on `rt_fp`, and a running pass
-- holds row locks on it for up to 1,050 s, past the 5 s lock_timeout below. Until it is applied,
-- the code that reads the columns skips every pass (`store_absent`).

set lock_timeout = '5s';

alter table autodedup.rt_fp
  add column if not exists source text,
  add column if not exists area_basis text,
  add column if not exists block_key text;

comment on column autodedup.rt_fp.source is
  'listings.source as the lane last read it; the area veto''s basis exemption reads it. '
  'NULL until the lane refreshes the advert.';
comment on column autodedup.rt_fp.area_basis is
  'listings.area_basis as the lane last read it; the area veto''s basis exemption reads it. '
  'NULL until the lane refreshes the advert.';
comment on column autodedup.rt_fp.block_key is
  'The block key (c<cast_obce_kod> / o<obec_kod> / empty) the advert''s postings were keyed '
  'under; the drift sweep re-claims the advert when listing_location gives another. NULL until '
  'the lane refreshes the advert: never swept.';

reset lock_timeout;
