-- Stored storeys that today's reading rules no longer produce (#2552).
--
-- The reparse seam never blanks a stored value, so two populations outlived the rule that
-- wrote them:
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
--   * idnes `total_floors`. 247 rows read "1" out of the page's own unfilled template,
--     `%1$s podlaží` (fixed in #2555). The literal template in the stored page names them.
--
-- DESTRUCTIVE (overwrites stored values): operator OK 2026-11-22, backup
-- `backup_before_drop.yml` label `floors-753` (listings: id, floor, total_floors) taken first.
-- Run the bazos heal straight after applying. Each statement is idempotent, and each enqueues
-- the properties it touched in its own statement (rule 20).
set lock_timeout = '5s';

with cleared as (
    update public.listings l
       set floor = null
     where l.source = 'bazos'
       and l.floor is not null
       and not exists (
           select 1 from public.portal_raw_payloads y
            where y.source = 'bazos'
              and y.source_id_native = l.source_id_native
              and y.http_status = 410)
 returning l.property_id
)
insert into public.dirty_properties (property_id)
select distinct property_id from cleared where property_id is not null
on conflict (property_id) do update set marked_at = now();

with cleared as (
    update public.listings l
       set total_floors = null
      from public.portal_raw_pages p
     where l.source = 'idnes'
       and l.total_floors = 1
       and p.source = 'idnes'
       and p.source_id_native = l.source_id_native
       and p.page_kind = 'detail'
       and position('%1$s podlaží' in p.html) > 0
 returning l.property_id
)
insert into public.dirty_properties (property_id)
select distinct property_id from cleared where property_id is not null
on conflict (property_id) do update set marked_at = now();
