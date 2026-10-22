-- 613: a shared note reaches later shares (docs/design/collection-sharing.md §8 ruling 9).
--
-- THE RULING (operator, 2026-10-17, verbatim): "share all even historical notes that were marked
-- for sharing with new users added later to sharing". A note marked shared is readable by the
-- other party of EVERY share the author is party to whose collection holds the property, owner
-- or recipient, whenever that share was made.
--
-- WHY 610 HAD AN ANCHOR. Shares need no acceptance, so anyone who knows an author's email can
-- share a collection holding the author's property with them and read what the author marked
-- for someone else (the bait share). 610 counted a share the author RECEIVED only if it existed
-- when the mark turned on (`property_notes.shared_at`, trigger-stamped). The ruling accepts that
-- edge; what mitigates it now: the note composer names the audience the mark reaches, the
-- in-app share notification tells the author someone shared with them, unmarking is one click
-- and leaving is the two-step "Remove from my list".
--
-- WHAT CHANGES. The note policy is recreated without the time clause (same name, FOR SELECT TO
-- authenticated; dropped and created inside one transaction, so the table is never unpoliced),
-- then the anchor's trigger, function and column go. The other four 610 share policies stay.
-- No payload ever carried shared_at: the API, SPA and extension are untouched. A split's note
-- copy (toolkit/property_carriers.py) carries `shared` alone from this PR on.
--
-- DESTRUCTIVE BY CLASS — a column drop — ruled 2026-10-17; 0 rows carried a value (verified live:
-- no note marked shared, no shared_at set, no collection_shares row), nothing to back up.
--
-- APPLY ORDER: AFTER the merge and Railway's API + realtime-worker rollout, never before. The
-- split's note copy deployed before this PR still names shared_at, so applying first fails every
-- note-copying split until the rollout; this PR's copy runs on both schemas (610's trigger stamps
-- shared_at while it exists).
--
-- ROLLBACK: forward only (rule 1). Restoring the anchor is a new migration re-adding the column,
-- its trigger and the clause; marks made meanwhile would carry no consent time.

set lock_timeout = '5s';

begin;

drop policy property_notes_shared_read on property_notes;

create policy property_notes_shared_read on property_notes
  for select to authenticated
  using (shared and exists (
          select 1
            from collection_shares s
            join collection_properties cp on cp.collection_id = s.collection_id
           where cp.property_id = property_notes.property_id
             and property_notes.account_id in (s.account_id, s.recipient_account_id)));

drop trigger property_notes_shared_at_biu on property_notes;
drop function stamp_property_notes_shared_at();
alter table property_notes drop column shared_at;

comment on column property_notes.shared is
  'The author''s mark (migrations 610, 613): readable by the other party of any share the '
  'author is party to whose collection holds this property, whenever that share was made. '
  'Default private.';

do $$
declare
  expected constant jsonb := jsonb_build_object(
    'collection_shares.collection_shares_party', 'ALL',
    'collections.collections_shared_read', 'SELECT',
    'collection_properties.collection_properties_shared_read', 'SELECT',
    'property_note_attachments.property_note_attachments_shared_read', 'SELECT');
begin
  if not exists (select 1 from pg_policies
                  where schemaname = 'public' and tablename = 'property_notes'
                    and policyname = 'property_notes_shared_read'
                    and cmd = 'SELECT' and permissive = 'PERMISSIVE'
                    and roles = array['authenticated']::name[]
                    and position('shared_at' in qual) = 0
                    and position('shared' in qual) > 0
                    and position('collection_properties' in qual) > 0
                    and position('recipient_account_id' in qual) > 0) then
    raise exception '613: property_notes_shared_read is not FOR SELECT TO authenticated on the party rule without the time clause';
  end if;
  if (select count(*) from pg_policies
       where schemaname = 'public'
         and cmd = expected ->> (tablename || '.' || policyname)
         and permissive = 'PERMISSIVE'
         and roles = array['authenticated']::name[]) <> 4 then
    raise exception '613: the other four share policies drifted';
  end if;
  if exists (select 1 from pg_trigger
              where tgname = 'property_notes_shared_at_biu'
                and tgrelid = 'public.property_notes'::regclass)
     or to_regprocedure('public.stamp_property_notes_shared_at()') is not null
     or exists (select 1 from information_schema.columns
                 where table_schema = 'public' and table_name = 'property_notes'
                   and column_name = 'shared_at') then
    raise exception '613: the consent-time anchor (trigger, function or column) survived';
  end if;
end $$;

commit;
