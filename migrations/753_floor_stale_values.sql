-- Stored bazos storeys that today's reading rules no longer produce (#2552).
--
-- APPLIED 2026-11-22 07:05Z (apply_migration run 44996386764). NEVER RE-RUN: the heal wrote the
-- storeys back right after it (reparse run 44996694296), and a second run would blank every one
-- of them again.
--
-- The reparse seam never blanks a stored value, so this population outlived the rule that
-- wrote it (the idnes one is migration 756):
--
--   * bazos `floor`. Since #2559 a storey worded only as "patro" is not read (it names no
--     scale: against the idnes twin the old reading was right in 50 % and one high in 46 %).
--     The parser is the ONLY writer of `listings.floor`, and every bazos row keeps the page it
--     was parsed from, so the value is cleared here and the existing heal
--     (`reparse.yml source=bazos fields=floor`) writes back exactly what the parser reads
--     today. No second copy of the reading rule, no list of rows. Left alone: a row whose
--     stored page is the portal's deleted-advert page (migration 519 stamped those 410),
--     which the heal could not refill.
--
-- DESTRUCTIVE (overwrites stored values): operator OK 2026-11-22, backup
-- `backup_before_drop.yml` label `floors-753` (listings: id, floor, total_floors) taken first.
-- The bazos heal runs straight after applying.
--
-- The bazos statement does NOT enqueue its properties, by the merge coordinator's ruling: the
-- enqueue would put ~80,000 properties in front of the maintenance lane at once, recompute
-- them to a blank storey, and the heal would then mark most of them again. Without it a
-- property keeps its old storey until the heal writes the advert back and enqueues it, paced;
-- the ~11,000 adverts that stay blank get no heal write, so their properties take the
-- corrected storey at the 03:00 full sweep.
set lock_timeout = '5s';

update public.listings l
   set floor = null
 where l.source = 'bazos'
   and l.floor is not null
   and not exists (
       select 1 from public.portal_raw_payloads y
        where y.source = 'bazos'
          and y.source_id_native = l.source_id_native
          and y.http_status = 410);
