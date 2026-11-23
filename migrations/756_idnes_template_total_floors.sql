-- idnes building totals read out of the page's own unfilled template (#2552).
--
-- Until #2555 the idnes parser read "1" from the literal `%1$s podlaží` the portal prints when
-- it has no storey count. The reparse seam never blanks a stored value, so those rows kept
-- `total_floors = 1`. The literal template in the stored page names them (about 260 rows of the
-- 25,967 idnes rows that hold 1); a real one-storey building has no such template and is left.
--
-- DESTRUCTIVE (overwrites stored values): operator OK 2026-11-22; the backup
-- `backup_before_drop.yml` label `floors-753` (listings: id, floor, total_floors, 06:45Z) holds
-- every value this clears, since it clears only the value 1.
-- Idempotent: it tests `total_floors = 1` and the template, so a lock retry is safe. The
-- statement detoasts each candidate's stored page (about 2.5 min), above the database's 2 min
-- default `statement_timeout`, hence the explicit one.
set statement_timeout = '10min';
set lock_timeout = '5s';

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
