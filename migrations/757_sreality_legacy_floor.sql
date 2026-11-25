-- Old sreality adverts stored one storey too high (#2552).
--
-- Rows stored before the client unwrapped the estate object hold the WRAPPED legacy response
-- (`raw_json ? 'items'`). The parser of that time wrote the number of the portal's own
-- "N. podlaží" text into `floor` unconverted, while the stored scale is ground = 0 and sreality
-- counts the ground storey as 1: "8. podlaží z celkem 8" was stored as 8 and reads back as
-- "9. podlaží z 8". Every such row is inactive, and no heal reaches it (`scripts/reparse.py`:
-- `parse_listing` raises on the wrapped shape and writes nothing), so nothing re-writes the old
-- value later either. A basement wording ("-1. podlaží", stored -1) is right as stored and is
-- not matched: the pattern takes an unsigned number only.
--
-- 23,498 rows on 22,911 properties (2026-11-22), 22,068 of them flats.
--
-- DESTRUCTIVE (overwrites stored values): operator OK 2026-11-22; backup
-- `backup_before_drop.yml` label `floors-757` (listings: id, floor, total_floors) taken first.
-- Idempotent, safe to re-run (unlike 753): after the write `floor` no longer equals the number
-- in the row's own text, so the row is not selected again.
--
-- No enqueue, by the merge coordinator's ruling: ~23,000 properties at once would sit in front
-- of new properties in the maintenance lane; these adverts are all inactive, and their
-- properties take the corrected storey at the 03:00 full sweep.
set statement_timeout = '10min';
set lock_timeout = '5s';

update public.listings l
   set floor = l.floor - 1
 where l.source = 'sreality'
   and not l.is_active
   and l.ad_reference is null
   and l.floor is not null
   and l.raw_json ? 'items'
   and l.floor = substring(
           (jsonb_path_query_first(l.raw_json, '$.items[*] ? (@.name == "Podlaží").value') #>> '{}')
           from '^(\d+)\.')::int;
