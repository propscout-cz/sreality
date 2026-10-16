-- 606_chip_rows_without_codes_deleted.sql -- DESTRUCTIVE (operator ruling 2026-10-16): the four stored
-- filters whose location chips carry no (level, code) are deleted, after a backup in the database.
--
-- WHY. Since 605 a location chip IS one RÚIAN unit, a level plus a code, and nothing re-reads a chip by
-- NAME any more: the API refuses a code-less chip (422), the SPA's URL parser drops one, and the
-- read-time name reader (`/maps/resolve-names`, `upgrade_district_chips`, the SPA's legacy-chip upgrade)
-- is deleted by the same PR. A name is not a unit ("Jihlava" is an obec AND an okres), so that reader
-- could only ever widen a chip. Exactly four rows still hold such chips (production, read 2026-10-16;
-- 0 anywhere else, 0 `locality` chips anywhere):
--   filter_presets 34fba568-989b-4ea1-9e67-605967bed14b and f2ece137-132e-495d-8c50-80740b35d85d
--     (the same five name-only kraj chips each);
--   notification_subscriptions 151f3dec-3833-402c-a81f-e0c8ca7d71d1 "Jihlava, HB, Humpolec" and
--     18ec76f9-ef6d-45b9-bdb6-f74eb17584d1 "Sreality dog mirror" (both inactive; 8 name-only chips and
--     778 dispatches between them).
-- The operator, 2026-10-16: "ignore any watchdogs or preset filters I have, you can just delete those
-- impacted ... implement the ideal way" -- so they are deleted, never stamped with guessed codes.
--
-- WHAT GOES. The two presets; the two subscriptions, and their notification_dispatches by ON DELETE
-- CASCADE (057). channel_sends.notification_id is ON DELETE SET NULL, which channel_sends_check (274)
-- forbids on a watchdog send: 0 sends reference these dispatches (2026-10-16) and step 2 refuses if one
-- appears. No other foreign key references either table.
--
-- BACKUP FIRST (rule 1; 511 / 573's pattern): schema backup_606, owner-only, holds a copy of every row
-- this file deletes. RESTORE: insert into public.filter_presets select * from backup_606.filter_presets;
-- the same for notification_subscriptions, THEN notification_dispatches. The operator drops schema
-- backup_606 once the street chip has been live for a week.
--
-- ONE TRANSACTION: the backup, the gate, the deletes and the post-conditions commit together or not at
-- all. IDEMPOTENT: each backup is CREATE TABLE IF NOT EXISTS ... AS (a re-run never re-copies or appends)
-- and the deletes are keyed on the four ids, so a re-run deletes nothing. On the CI replay none of the
-- rows exists: the backups are empty, the deletes touch nothing, the post-conditions hold.

set lock_timeout = '5s';

begin;

-- ---------------------------------------------------------------------------
-- 1. The backup. Copied, not moved; never granted to a browser role.
-- ---------------------------------------------------------------------------
create schema if not exists backup_606;
revoke all on schema backup_606 from anon, authenticated;

create table if not exists backup_606.filter_presets as
  select * from public.filter_presets
   where id in ('34fba568-989b-4ea1-9e67-605967bed14b', 'f2ece137-132e-495d-8c50-80740b35d85d');

create table if not exists backup_606.notification_subscriptions as
  select * from public.notification_subscriptions
   where id in ('151f3dec-3833-402c-a81f-e0c8ca7d71d1', '18ec76f9-ef6d-45b9-bdb6-f74eb17584d1');

create table if not exists backup_606.notification_dispatches as
  select * from public.notification_dispatches
   where subscription_id in ('151f3dec-3833-402c-a81f-e0c8ca7d71d1', '18ec76f9-ef6d-45b9-bdb6-f74eb17584d1');

alter table backup_606.filter_presets enable row level security;
alter table backup_606.notification_subscriptions enable row level security;
alter table backup_606.notification_dispatches enable row level security;
revoke all on all tables in schema backup_606 from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. THE GATE: every row about to go is in the backup, by id, and no delivery record would be orphaned
--    against channel_sends_check. After a completed apply nothing is left to delete and it passes.
-- ---------------------------------------------------------------------------
do $gate$
declare
  n_missing bigint;
  n_sends bigint;
begin
  select (select count(*) from public.filter_presets p
           where p.id in ('34fba568-989b-4ea1-9e67-605967bed14b', 'f2ece137-132e-495d-8c50-80740b35d85d')
             and not exists (select 1 from backup_606.filter_presets b where b.id = p.id))
       + (select count(*) from public.notification_subscriptions s
           where s.id in ('151f3dec-3833-402c-a81f-e0c8ca7d71d1', '18ec76f9-ef6d-45b9-bdb6-f74eb17584d1')
             and not exists (select 1 from backup_606.notification_subscriptions b where b.id = s.id))
       + (select count(*) from public.notification_dispatches d
           where d.subscription_id in ('151f3dec-3833-402c-a81f-e0c8ca7d71d1', '18ec76f9-ef6d-45b9-bdb6-f74eb17584d1')
             and not exists (select 1 from backup_606.notification_dispatches b where b.id = d.id))
    into n_missing;
  if n_missing > 0 then
    raise exception '606 refused: % row(s) about to be deleted are not in backup_606 -- drop schema '
                    'backup_606 and re-apply', n_missing;
  end if;
  select count(*) into n_sends
    from public.channel_sends cs
    join public.notification_dispatches d on d.id = cs.notification_id
   where d.subscription_id in ('151f3dec-3833-402c-a81f-e0c8ca7d71d1', '18ec76f9-ef6d-45b9-bdb6-f74eb17584d1');
  if n_sends > 0 then
    raise exception '606 refused: % channel_sends row(s) point at these dispatches; their SET NULL '
                    'would break channel_sends_check -- ask the operator', n_sends;
  end if;
end
$gate$;

-- ---------------------------------------------------------------------------
-- 3. The deletes. The dispatches follow their subscription (ON DELETE CASCADE).
-- ---------------------------------------------------------------------------
delete from public.filter_presets
 where id in ('34fba568-989b-4ea1-9e67-605967bed14b', 'f2ece137-132e-495d-8c50-80740b35d85d');

delete from public.notification_subscriptions
 where id in ('151f3dec-3833-402c-a81f-e0c8ca7d71d1', '18ec76f9-ef6d-45b9-bdb6-f74eb17584d1');

-- ---------------------------------------------------------------------------
-- 4. Post-conditions: no stored chip lacks a level (one of the five) and a positive integer code, and
--    nothing is left of the four rows. A preset's chips are read the way its readers read them
--    (`api/routes/filter_presets._coded_chips`, the SPA's `readPresetSpec`): `filters`, else a legacy
--    bare filter set. A stored `"filters": null` is jsonb null, not SQL NULL, hence no coalesce.
-- ---------------------------------------------------------------------------
do $post$
declare
  n_bad bigint;
  n_left bigint;
begin
  select count(*) into n_bad
    from (select (case when jsonb_typeof(p.filter_spec->'filters') = 'object'
                       then p.filter_spec->'filters' else p.filter_spec end)->'districts' as chips
            from public.filter_presets p
          union all
          select s.filter_spec->'districts' from public.notification_subscriptions s) f
   cross join lateral jsonb_array_elements(
           case when jsonb_typeof(f.chips) = 'array' then f.chips else '[]'::jsonb end) c
   where coalesce(c->>'level', '') not in ('kraj', 'okres', 'obec', 'cast_obce', 'ulice')
      or case when jsonb_typeof(c->'id') = 'number'
              then (c->'id')::numeric <= 0 or (c->'id')::numeric <> trunc((c->'id')::numeric)
              else true end;
  select (select count(*) from public.filter_presets
           where id in ('34fba568-989b-4ea1-9e67-605967bed14b', 'f2ece137-132e-495d-8c50-80740b35d85d'))
       + (select count(*) from public.notification_subscriptions
           where id in ('151f3dec-3833-402c-a81f-e0c8ca7d71d1', '18ec76f9-ef6d-45b9-bdb6-f74eb17584d1'))
       + (select count(*) from public.notification_dispatches
           where subscription_id in ('151f3dec-3833-402c-a81f-e0c8ca7d71d1', '18ec76f9-ef6d-45b9-bdb6-f74eb17584d1'))
    into n_left;
  if n_bad > 0 or n_left > 0 then
    raise exception '606 did not land: % stored chip(s) still lack a (level, code); % row(s) of the four '
                    'filters remain', n_bad, n_left;
  end if;
  raise notice '606: backup_606 holds % preset(s), % watchdog(s), % dispatch(es); 0 code-less chips stored',
    (select count(*) from backup_606.filter_presets),
    (select count(*) from backup_606.notification_subscriptions),
    (select count(*) from backup_606.notification_dispatches);
end
$post$;

commit;

reset lock_timeout;
