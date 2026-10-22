-- 660_property_attachments.sql -- PHOTOS W1 (#1759): one attachment store per property.
--
-- WHY. A file a user adds to a property (a note's PDF, a photo taken in the app) is one kind of record:
-- the property's, with an optional note. 592 made it note-grain (a row named its note, never a property),
-- so a photo with no note had nowhere to live and a deleted note took its files with it. Operator rulings
-- 2026-10-22: attachments become property-grain with an optional note; a deleted note no longer deletes the
-- property's files; under collection and pipeline shares files follow the note rules (no new rule).
--
-- WHAT CHANGES.
--   1. A backup of every row first (606's pattern: schema backup_660, owner-only). OWED: drop backup_660 once
--      W1 has been live a week.
--   2. property_note_attachments is renamed property_attachments, with its sequence, indexes, constraints,
--      trigger and policies.
--   3. property_id (NOT NULL, -> properties) is backfilled from each row's note and indexed (property_id,
--      created_at). note_id becomes nullable; the note FK becomes (note_id, account_id) -> property_notes
--      (id, account_id) ON DELETE SET NULL (note_id): a deleted note leaves its files on the property, and a
--      file can only name a note of its own account (what 592's trigger guaranteed, now structural). The
--      same bytes are held once per account and property: unique (account_id, property_id, sha256_hex)
--      replaces (note_id, sha256_hex); a merge folds a collision (toolkit/property_carriers.py).
--   4. A note's files follow it: an AFTER UPDATE OF property_id trigger on property_notes moves them with
--      the note (a merge, a split's note move), unless that account already holds the same bytes there.
--   5. account_id is written by the API from the acting account: 592's derive-from-the-note trigger goes.
--      RLS: tenant_rw on acting_account_ids() (641's, restated under the new name); shared_read stays "a
--      file is readable where its note is" (610), so a file of an unmarked note and a file with no note are
--      read by no share party -- the note rule, unchanged. Grants as 592.
--   6. A COMPATIBILITY VIEW named property_note_attachments (security_invoker, auto-updatable) plus a
--      BEFORE INSERT trigger that fills property_id and account_id from the note when a writer leaves them
--      out: the API deployed before this PR keeps uploading, listing and deleting until the deploy. OWED:
--      a later migration drops the view, the fill trigger and its function once the PR is live.
--
-- APPLY BEFORE THE MERGE (the PR's code reads property_attachments). psql -f, statement autocommit: every
-- statement is idempotent, re-run the file on a lock timeout. The post-check raises unless every backed-up
-- row is still here, the view stands and both policies are in place.
-- Verify:
--   select (select count(*) from backup_660.property_note_attachments) as backup,
--          (select count(*) from property_attachments) as now,
--          (select count(*) from property_note_attachments) as via_view,
--          (select count(*) from property_attachments where property_id is null) as no_property,
--          (select relkind from pg_class where oid = 'public.property_note_attachments'::regclass) as view_kind;

set lock_timeout = '5s';

-- 1. The backup: copied, never moved; never granted to a browser role.
create schema if not exists backup_660;
revoke all on schema backup_660 from anon, authenticated;
do $$
begin
  if to_regclass('backup_660.property_note_attachments') is null then
    create table backup_660.property_note_attachments as select * from public.property_note_attachments;
  end if;
end
$$;
alter table backup_660.property_note_attachments enable row level security;
revoke all on all tables in schema backup_660 from anon, authenticated;

-- 2. The new name, for the table and everything named after it.
do $$
begin
  if to_regclass('public.property_attachments') is null then
    alter table public.property_note_attachments rename to property_attachments;
  end if;
  if exists (select 1 from pg_trigger where tgname = 'property_note_attachments_delegated_edit'
              and tgrelid = 'public.property_attachments'::regclass) then
    alter trigger property_note_attachments_delegated_edit on public.property_attachments
      rename to property_attachments_delegated_edit;
  end if;
  if exists (select 1 from pg_constraint where conname = 'property_note_attachments_account_id_fkey'
              and conrelid = 'public.property_attachments'::regclass) then
    alter table public.property_attachments
      rename constraint property_note_attachments_account_id_fkey to property_attachments_account_id_fkey;
  end if;
  if exists (select 1 from pg_constraint where conname = 'property_note_attachments_filename_check'
              and conrelid = 'public.property_attachments'::regclass) then
    alter table public.property_attachments
      rename constraint property_note_attachments_filename_check to property_attachments_filename_check;
  end if;
  if exists (select 1 from pg_constraint where conname = 'property_note_attachments_byte_size_check'
              and conrelid = 'public.property_attachments'::regclass) then
    alter table public.property_attachments
      rename constraint property_note_attachments_byte_size_check to property_attachments_byte_size_check;
  end if;
  if exists (select 1 from pg_constraint where conname = 'property_note_attachments_sha256_hex_check'
              and conrelid = 'public.property_attachments'::regclass) then
    alter table public.property_attachments
      rename constraint property_note_attachments_sha256_hex_check to property_attachments_sha256_hex_check;
  end if;
end
$$;
alter sequence if exists public.property_note_attachments_id_seq rename to property_attachments_id_seq;
alter index if exists public.property_note_attachments_pkey rename to property_attachments_pkey;
alter index if exists public.property_note_attachments_note_idx rename to property_attachments_note_idx;
alter index if exists public.property_note_attachments_account_id_idx
  rename to property_attachments_account_id_idx;

-- 3 + 6. property_id, filled from the note for a writer that leaves it out (the deployed API, until the
-- deploy), then backfilled and required.
alter table property_attachments add column if not exists property_id bigint references properties(id);

create or replace function property_attachments_fill_from_note()
returns trigger language plpgsql as $$
begin
  if new.note_id is not null and (new.property_id is null or new.account_id is null) then
    select coalesce(new.property_id, n.property_id), coalesce(new.account_id, n.account_id)
      into new.property_id, new.account_id
      from property_notes n where n.id = new.note_id;
  end if;
  return new;
end;
$$;
revoke all on function property_attachments_fill_from_note() from public, anon, authenticated;
create or replace trigger property_attachments_fill_from_note_bi
  before insert on property_attachments
  for each row execute function property_attachments_fill_from_note();

-- 5. 592's trigger overwrote account_id from the note on every insert (a file with no note: NULL).
drop trigger if exists property_note_attachments_account_id_biu on property_attachments;
drop function if exists sync_property_note_attachments_account_id();

update property_attachments a set property_id = n.property_id
  from property_notes n
 where n.id = a.note_id and a.property_id is null;
alter table property_attachments alter column property_id set not null;
create index if not exists property_attachments_property_idx on property_attachments (property_id, created_at);

create unique index if not exists property_notes_id_account_id_key on property_notes (id, account_id);
alter table property_attachments
  drop constraint if exists property_note_attachments_note_id_sha256_hex_key,
  drop constraint if exists property_note_attachments_note_id_fkey,
  drop constraint if exists property_attachments_note_fkey,
  add constraint property_attachments_note_fkey foreign key (note_id, account_id)
    references property_notes (id, account_id) on delete set null (note_id),
  alter column note_id drop not null;
create unique index if not exists property_attachments_account_property_sha256_key
  on property_attachments (account_id, property_id, sha256_hex);

-- 4. A note's files go where the note goes.
create or replace function property_attachments_follow_note()
returns trigger language plpgsql as $$
begin
  update property_attachments a set property_id = new.property_id
   where a.note_id = new.id and a.property_id <> new.property_id
     and not exists (select 1 from property_attachments x
                      where x.property_id = new.property_id and x.account_id = a.account_id
                        and x.sha256_hex = a.sha256_hex);
  return null;
end;
$$;
revoke all on function property_attachments_follow_note() from public, anon, authenticated;
create or replace trigger property_notes_files_follow
  after update of property_id on property_notes
  for each row when (old.property_id is distinct from new.property_id)
  execute function property_attachments_follow_note();

-- 5. Tenancy, under the new names. Dropped and created per statement: between the two the table is
-- deny-all for browser roles (fail closed), never open.
drop policy if exists property_note_attachments_tenant_rw on property_attachments;
drop policy if exists property_attachments_tenant_rw on property_attachments;
create policy property_attachments_tenant_rw on property_attachments
  for all to authenticated
  using (account_id in (select acting_account_ids()))
  with check (account_id in (select acting_account_ids()));
drop policy if exists property_note_attachments_shared_read on property_attachments;
drop policy if exists property_attachments_shared_read on property_attachments;
create policy property_attachments_shared_read on property_attachments
  for select to authenticated
  using (note_id in (select id from property_notes));
revoke all on property_attachments from anon, authenticated;
grant select, insert, delete on property_attachments to authenticated;

comment on table property_attachments is
  'Files a user adds to a property (migrations 592, 660): property-grain, an optional note. Bytes in R2 '
  '(custom-attachments/...), originals never altered or deleted, display copies under thumb/w1080/. A '
  'merge carries them (toolkit/property_carriers.py); a note''s files follow the note.';

-- 6. The compatibility view for the API deployed before this PR.
create or replace view property_note_attachments with (security_invoker = true) as
  select id, note_id, account_id, storage_key, filename, mime_type, byte_size, sha256_hex, created_at,
         property_id
    from property_attachments;
revoke all on property_note_attachments from anon, authenticated;
grant select, insert, delete on property_note_attachments to authenticated;
comment on view property_note_attachments is
  'Migration 660: the old name of property_attachments for the API deployed before PHOTOS W1; dropped by a '
  'later migration once that PR is live.';

do $post$
begin
  if exists (select 1 from backup_660.property_note_attachments b
              where not exists (select 1 from property_attachments a where a.id = b.id)) then
    raise exception '660: a backed-up attachment is missing from property_attachments';
  end if;
  if (select relkind from pg_class where oid = 'public.property_note_attachments'::regclass) <> 'v' then
    raise exception '660: property_note_attachments is not the compatibility view';
  end if;
  if (select count(*) from pg_policies where schemaname = 'public' and tablename = 'property_attachments'
        and policyname in ('property_attachments_tenant_rw', 'property_attachments_shared_read')) <> 2
     or exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'property_attachments'
                  and policyname like 'property_note_attachments%') then
    raise exception '660: property_attachments does not carry exactly its two renamed policies';
  end if;
end
$post$;

notify pgrst, 'reload schema';
