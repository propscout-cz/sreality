-- 660_property_attachments.sql -- PHOTOS W1 (#1759): one attachment store per property.
--
-- WHY. A file a user adds to a property (a note's PDF, a photo taken in the app) is the property's, a note
-- optional. 592 made it note-grain, so a photo with no note had nowhere to live and a deleted note took its
-- files along. Operator rulings 2026-10-22: property-grain with an optional note; a deleted note keeps the
-- property's files; under collection and pipeline shares files follow the note rules (no new rule).
--
-- WHAT CHANGES.
--   1. A backup of every row first (606's pattern, schema backup_660). OWED: drop it a week after the deploy.
--   2. property_note_attachments becomes property_attachments, with its sequence, indexes, constraints,
--      trigger and policies.
--   3. property_id (NOT NULL) backfilled from the note and indexed; note_id optional, keyed (note_id,
--      account_id) -> property_notes ON DELETE SET NULL (note_id): a deleted note leaves its files, and a file
--      names only its own account's note (592's trigger guaranteed that; now the key does). The same bytes
--      once per account and property (a merge folds a twin, toolkit/property_carriers.py).
--   4. A note's files follow the note (AFTER UPDATE OF property_id on property_notes), unless that account
--      already holds the same bytes there.
--   5. account_id comes from the API's acting account: 592's derive-from-the-note trigger goes. RLS:
--      tenant_rw on acting_account_ids() as 641 left it; shared_read stays "readable where its note is"
--      (610), so a file of an unmarked note, or with no note, reaches no share party. Grants as 592.
--   6. A COMPATIBILITY VIEW property_note_attachments (security_invoker, auto-updatable) and a BEFORE INSERT
--      trigger filling property_id and account_id from the note when a writer leaves them out, so the API
--      deployed before this PR keeps uploading, listing and deleting until the deploy. OWED: a later
--      migration drops the view, the trigger and its function once the PR is live.
--
-- APPLY BEFORE THE MERGE. psql -f, statement autocommit: every statement is idempotent, re-run on a lock
-- timeout. The post-check raises unless every backed-up row is here, the view stands and both policies are in.
-- Verify:
--   select (select count(*) from backup_660.property_note_attachments) as backup,
--          (select count(*) from property_attachments) as now,
--          (select count(*) from property_note_attachments) as via_view,
--          (select count(*) from property_attachments where property_id is null) as no_property,
--          (select relkind from pg_class where oid = 'public.property_note_attachments'::regclass) as view_kind;
-- ci-allow-dynamic: property_attachments -- renames only (constraints, indexes, the sequence), nothing created

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

-- 2. The new name, for the table and everything named after it (the note key and the note-grain unique go
-- in 3, so they are dropped under their old names first).
do $$
declare
  r record;
begin
  if to_regclass('public.property_attachments') is null then
    alter table public.property_note_attachments rename to property_attachments;
  end if;
  alter table public.property_attachments
    drop constraint if exists property_note_attachments_note_id_sha256_hex_key,
    drop constraint if exists property_note_attachments_note_id_fkey;
  for r in select conname as old from pg_constraint
            where conrelid = 'public.property_attachments'::regclass and contype in ('c', 'f')
              and conname like 'property\_note\_attachments\_%' loop
    execute format('alter table public.property_attachments rename constraint %I to %I',
                   r.old, replace(r.old, 'property_note_attachments', 'property_attachments'));
  end loop;
  for r in select c.relname as old from pg_index i join pg_class c on c.oid = i.indexrelid
            where i.indrelid = 'public.property_attachments'::regclass
              and c.relname like 'property\_note\_attachments\_%' loop
    execute format('alter index public.%I rename to %I',
                   r.old, replace(r.old, 'property_note_attachments', 'property_attachments'));
  end loop;
  if to_regclass('public.property_note_attachments_id_seq') is not null then
    alter sequence public.property_note_attachments_id_seq rename to property_attachments_id_seq;
  end if;
  if exists (select 1 from pg_trigger where tgname = 'property_note_attachments_delegated_edit') then
    alter trigger property_note_attachments_delegated_edit on public.property_attachments
      rename to property_attachments_delegated_edit;
  end if;
end
$$;

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

-- 5. Tenancy under the new names; between a drop and its create the table is deny-all (fail closed).
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
  'Files a user adds to a property (migrations 592, 660), a note optional. Bytes in R2 (custom-attachments/), '
  'originals never altered or deleted, display copies under thumb/w1080/. Carried by '
  'toolkit/property_carriers.py; a note''s files follow the note.';

-- 6. The compatibility view for the API deployed before this PR.
create or replace view property_note_attachments with (security_invoker = true) as
  select id, note_id, account_id, storage_key, filename, mime_type, byte_size, sha256_hex, created_at,
         property_id
    from property_attachments;
revoke all on property_note_attachments from anon, authenticated;
grant select, insert, delete on property_note_attachments to authenticated;
comment on view property_note_attachments is
  'Migration 660: property_attachments under its old name for the API deployed before PHOTOS W1; dropped later.';

do $post$
begin
  if exists (select 1 from backup_660.property_note_attachments b
              where not exists (select 1 from property_attachments a where a.id = b.id))
     or (select relkind from pg_class where oid = 'public.property_note_attachments'::regclass) <> 'v'
     or (select count(*) from pg_policies where schemaname = 'public' and tablename = 'property_attachments')
        <> 2
     or not exists (select 1 from pg_policies where schemaname = 'public'
                     and policyname = 'property_attachments_shared_read') then
    raise exception '660: a backed-up row is missing, the view is not one, or the two policies are not in';
  end if;
end
$post$;

notify pgrst, 'reload schema';
