-- Stored building counts that the same advert's own storey contradicts (#2552, decision B).
--
-- `total_floors` is the above-ground podlaží count and `floor` is ground = 0, so the top
-- storey is `total_floors - 1`. A portal pair at or past the count ("3. podlaží z 1") is the
-- seller's typing, mostly the flat's own storeys entered as the building's. Since this PR the
-- shared reader (`scraper.floor.total_floors_from_portal`) reads such a count as absence, on
-- every structured portal; this clears the rows written before the rule. The portal's own
-- value stays in each advert's stored page and snapshots.
--
-- Measured 2026-11-22: 11,422 adverts, 2,945 active (idnes 4,998, realitymix 4,433,
-- sreality 1,712, mmreality 160, bezrealitky 61, remax 51, bazos 7).
--
-- DESTRUCTIVE (overwrites stored values): operator OK 2026-11-22 ("b yes"), backup
-- `backup_before_drop.yml` label `floors-760` (listings: id, floor, total_floors) taken first.
-- Idempotent: a second run finds no row.
--
-- Applied from main AFTER the reading rule is live (the coordinator's order): the other way
-- round, the old parser writes the count back at the advert's next content fetch.
--
-- Enqueues its properties (rule 20; at most ~11,400, under the maintenance lane's line).
set statement_timeout = '10min';
set lock_timeout = '5s';

with cleared as (
    update public.listings l
       set total_floors = null
     where l.floor is not null
       and l.total_floors is not null
       and l.floor >= l.total_floors
 returning l.property_id)
insert into public.dirty_properties (property_id)
select distinct property_id from cleared where property_id is not null
on conflict (property_id) do update set marked_at = now();
