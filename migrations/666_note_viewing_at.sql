-- 666_note_viewing_at.sql -- a VIEWING DATE on a note ("datum prohlídky", operator request 2026-10-23: add it in the
-- property page's Poznámky section; the phone app gets a Prohlídky tab, one day at a time). ADDITIVE: one nullable
-- column and one partial index; the body check loosens (every row it held still holds, nothing new is refused).
--
-- WHAT. A viewing is a note with a date: `property_notes.viewing_at` is when its author sees (or saw) the property in
-- person, and the note's text is what to check there and, afterwards, what they found. So a viewing follows every
-- rule a note already follows -- property grain, the account's own (RLS, 290), read across a share only on its
-- author's mark (610), carried by the notes carrier on a merge and routed with its ad on a split (rule 18; the split
-- copy keeps the date), written by an edit delegate as the owner (641) -- and needs no table, carrier, route family
-- or registry entry of its own.
-- BODY. A viewing may carry no text (the date says it): the check becomes "1..4000 characters, or empty when a
-- viewing date is set". One ALTER drops and re-adds it under the same name, so the table is never without it.
-- INDEX. The Prohlídky tab reads one account's viewings in date order: (account_id, viewing_at), partial.
-- 113 notes live (2026-10-23): the re-check scans nothing worth timing; lock_timeout anyway (the API writes here).
--
-- APPLY through apply_migration.yml (--ref the feature branch) BEFORE the API code merges (its note projection names
-- the column). Every statement is idempotent; a re-run is a no-op. ROLLBACK: forward only (rule 1); none needed --
-- the column is inert without the API.
-- Verify:
--   select count(*) filter (where viewing_at is not null), count(*) from property_notes;
--   select pg_get_constraintdef(oid) from pg_constraint where conname = 'property_notes_body_check';

set lock_timeout = '5s';

alter table public.property_notes add column if not exists viewing_at timestamptz;

comment on column public.property_notes.viewing_at is
  'When the author sees (or saw) the property in person: a viewing is a note with a date (migration 666). '
  'NULL = a plain note. The note text is optional on a viewing (property_notes_body_check).';

alter table public.property_notes
  drop constraint if exists property_notes_body_check,
  add constraint property_notes_body_check
    check (length(body) <= 4000 and (length(body) >= 1 or viewing_at is not null));

create index if not exists property_notes_viewings
  on public.property_notes (account_id, viewing_at)
  where viewing_at is not null;

do $post$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.property_notes'::regclass
                    and conname = 'property_notes_body_check'
                    and pg_get_constraintdef(oid) like '%viewing_at IS NOT NULL%') then
    raise exception '666: property_notes_body_check is not the viewing-aware check';
  end if;
  if to_regclass('public.property_notes_viewings') is null then
    raise exception '666: property_notes_viewings is missing';
  end if;
end
$post$;
