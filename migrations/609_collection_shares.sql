-- 609: collection sharing, view-only (docs/design/collection-sharing.md §3.1).
--
-- NORTH STAR. A share is ONE row, (collection, recipient account), and it widens only what the
-- recipient can READ. No write rule widens: the owner's FOR ALL `*_tenant_rw` policy on every
-- curation table is untouched, and every policy added here is FOR SELECT. PostgreSQL ORs
-- permissive policies per command, and an UPDATE/DELETE only touches rows that pass the
-- UPDATE/DELETE policy's USING, so a recipient's write by id matches no row (the API's
-- write-is-the-gate turns that into a 404).
--
-- THE SHARE ROW. `collection_shares.account_id` is the OWNER, stamped by trigger from the
-- collection (292's function, renamed here and reused — triggers bind by OID, so the existing
-- collection_properties trigger follows the rename). Browser roles get SELECT + DELETE and NO
-- INSERT: account uuids are learnable by any signed-in user (manual_rental_estimates is
-- read-all and carries account_id), so a raw-uuid share through supabase-js would turn a uuid
-- into an email via share_party_emails(). Shares are created only by the API's share route,
-- on the service role, after it resolved the recipient by confirmed email. Either party may
-- DELETE the row (the party policy's USING): the owner revokes, the recipient leaves.
--
-- THE CHILD TRIGGER STILL FAILS CLOSED. A recipient inserting a member row now SEES the shared
-- parent, so the trigger stamps the OWNER's account instead of NULL; WITH CHECK refuses either
-- (42501), nothing is written.
--
-- CONSENT MARK. `property_notes.shared` is the author's statement "the people I share this
-- property with may read this note"; default private. A party X reads a note by author A on
-- property P iff it is marked, and some share between X and A names a collection that currently
-- holds P: either A OWNS that share (A chose X), or A is its RECIPIENT and the share already
-- existed when A marked the note (`shared_at`, stamped by trigger when the mark turns on,
-- cleared when it turns off). Shares need no acceptance, so without that clause anyone who
-- knows A's email could share a collection with A and read every note A ever marked for
-- someone else; with it, a recipient's mark consents only to the audience A could see at the
-- time (re-marking consents again). collection_shares is RLS-filtered to the caller's own
-- party rows, so co-recipients never see each other's notes. A NULL-author note (one
-- pre-tenancy row) never matches: `NULL IN (...)` is not true. Attachments follow their note.
-- No recursion (42P17): the party policy reads only its own columns; every other new policy
-- points INTO collection_shares and never back.
--
-- THE NOTIFICATION KIND. `collection_share` is the fourth notification_dispatches producer:
-- the collection is the subject, change_kind 'shared', stamped to the RECIPIENT. The trigger
-- keeps the supplied account for this kind (tenants hold no INSERT on the table, so it can
-- only come from the service-role share route) and raises when none is supplied.
--
-- TWO LABEL FUNCTIONS. `account_email(uuid)` is the ONE label definition (the oldest member's
-- live auth email, the signup name as fallback); it reads auth.users and is never
-- browser-callable. `share_party_emails()` is the only browser door to it: SECURITY DEFINER,
-- it returns the labels of the caller's COUNTERPARTIES only (the other side of each share the
-- caller is party to), never the caller's own and never a stranger's. Neither stores an email;
-- the one snapshot is the share notification's message (the owner's email at share time).
--
-- ENUMERATION. The API answers an unknown or unconfirmed email with one 422; accepted on
-- purpose for a closed platform (spec §3.4). Revisit if signup opens.
--
-- NOT HERE. No view is created or replaced (the *_public views are security_invoker and
-- inherit through their base tables); no carrier entry (the share names no property; the
-- `shared` mark and its `shared_at` ride the note through a merge, and a split's copy writes
-- both); current_account_ids() is
-- untouched — a share is never membership.
--
-- LOCKS. One transaction. property_notes gains a column with a constant default and a
-- nullable one (metadata only) plus a row trigger; notification_dispatches' two CHECKs are swapped for supersets (a validating scan of a
-- small table). The lock_timeout makes a stuck apply fail instead of queueing behind a reader.
--
-- ROLLBACK: forward only (rule 1). A re-run fails at the rename and, being one transaction,
-- changes nothing.

set lock_timeout = '5s';

begin;

alter function sync_collection_properties_account_id() rename to sync_account_id_from_collection;

create table collection_shares (
  collection_id        bigint      not null references collections(id) on delete cascade,
  account_id           uuid        not null references accounts(id) on delete cascade,
  recipient_account_id uuid        not null references accounts(id) on delete cascade,
  created_at           timestamptz not null default now(),
  primary key (collection_id, recipient_account_id),
  constraint collection_shares_not_self check (recipient_account_id <> account_id)
);
create index collection_shares_recipient_idx on collection_shares (recipient_account_id);
create trigger collection_shares_account_id_bi
  before insert on collection_shares
  for each row execute function sync_account_id_from_collection();

alter table collection_shares enable row level security;
revoke all on collection_shares from anon, authenticated;
grant select, delete on collection_shares to authenticated;
create policy collection_shares_party on collection_shares
  for all to authenticated
  using (account_id in (select current_account_ids())
         or recipient_account_id in (select current_account_ids()))
  with check (account_id in (select current_account_ids()));

comment on table collection_shares is
  'A read-only grant of one collection to one account (migration 609). account_id = the owner, '
  'stamped from the collection by trigger. Created only by the API (no browser INSERT); either '
  'party may delete (owner revokes, recipient leaves). Widens reads only, never writes.';

alter table property_notes add column shared boolean not null default false;
alter table property_notes add column shared_at timestamptz;
comment on column property_notes.shared is
  'The author''s consent mark (migration 609): readable by the other party of a share the '
  'author is party to whose collection holds this property (as recipient, only of a share '
  'older than shared_at). Default private.';
comment on column property_notes.shared_at is
  'When the shared mark last turned on (migration 609; trigger-stamped, NULL while private). '
  'A share the author RECEIVED shows the note only if it already existed at this moment.';

create function stamp_property_notes_shared_at()
returns trigger language plpgsql set search_path = '' as $$
begin
  if not new.shared then
    new.shared_at := null;
  elsif tg_op = 'INSERT' then
    new.shared_at := coalesce(new.shared_at, now());
  elsif not old.shared then
    new.shared_at := now();
  else
    new.shared_at := old.shared_at;
  end if;
  return new;
end;
$$;
revoke execute on function stamp_property_notes_shared_at() from public, anon;
create trigger property_notes_shared_at_biu
  before insert or update on property_notes
  for each row execute function stamp_property_notes_shared_at();

create policy collections_shared_read on collections
  for select to authenticated
  using (id in (select collection_id from collection_shares));

create policy collection_properties_shared_read on collection_properties
  for select to authenticated
  using (collection_id in (select collection_id from collection_shares));

create policy property_notes_shared_read on property_notes
  for select to authenticated
  using (shared and exists (
          select 1
            from collection_shares s
            join collection_properties cp on cp.collection_id = s.collection_id
           where cp.property_id = property_notes.property_id
             and (property_notes.account_id = s.account_id
                  or (property_notes.account_id = s.recipient_account_id
                      and s.created_at <= property_notes.shared_at))));

create policy property_note_attachments_shared_read on property_note_attachments
  for select to authenticated
  using (note_id in (select id from property_notes));

alter table notification_dispatches drop constraint notification_dispatches_source_ck;
alter table notification_dispatches add constraint notification_dispatches_source_ck check (
     (source_kind = 'watchdog'           and subscription_id is not null and collection_id is null)
  or (source_kind = 'collection_monitor' and collection_id   is not null and subscription_id is null)
  or (source_kind = 'collection_share'   and collection_id   is not null and subscription_id is null)
  or (source_kind = 'system_health'      and subscription_id is null     and collection_id is null));

alter table notification_dispatches drop constraint notification_dispatches_change_kind_ck;
alter table notification_dispatches add constraint notification_dispatches_change_kind_ck check (
  change_kind in ('new', 'price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source',
                  'broker_change', 'system_alert', 'shared'));

create or replace function sync_notification_dispatches_account_id()
returns trigger language plpgsql as $$
begin
  if new.source_kind = 'watchdog' then
    select account_id into new.account_id from notification_subscriptions where id = new.subscription_id;
  elsif new.source_kind = 'collection_monitor' then
    select account_id into new.account_id from collections where id = new.collection_id;
  elsif new.source_kind = 'collection_share' then
    if new.account_id is null then
      raise exception 'collection_share dispatch needs its recipient account';
    end if;
  else
    new.account_id := '00000000-0000-0000-0000-000000000000';
  end if;
  return new;
end;
$$;

-- ci-allow-ungated: account_email reads auth.users but no browser role may EXECUTE it (revoked below; asserted at the end)
create function account_email(target uuid)
returns text language sql stable set search_path = '' as $$
  select coalesce(
    (select u.email
       from public.account_members m
       join auth.users u on u.id = m.user_id
      where m.account_id = target
      order by m.created_at, m.user_id
      limit 1),
    (select a.name from public.accounts a where a.id = target))
$$;
revoke execute on function account_email(uuid) from public, anon, authenticated;

-- Counterparty-scoped labels only (it reads auth.users solely through account_email); pinned by
-- a live equality test.
create function share_party_emails()
returns table (account_id uuid, email text)
language sql stable security definer set search_path = '' as $$
  select distinct p.id, public.account_email(p.id)
    from public.collection_shares s
    cross join lateral (values (s.account_id), (s.recipient_account_id)) p(id)
   where (s.account_id in (select public.current_account_ids())
          or s.recipient_account_id in (select public.current_account_ids()))
     and p.id not in (select public.current_account_ids())
$$;
revoke execute on function share_party_emails() from public, anon, authenticated;
grant execute on function share_party_emails() to authenticated;

do $$
declare
  expected constant jsonb := jsonb_build_object(
    'collection_shares.collection_shares_party', 'ALL',
    'collections.collections_shared_read', 'SELECT',
    'collection_properties.collection_properties_shared_read', 'SELECT',
    'property_notes.property_notes_shared_read', 'SELECT',
    'property_note_attachments.property_note_attachments_shared_read', 'SELECT');
  found_count int;
begin
  if not (select relrowsecurity from pg_class where oid = 'public.collection_shares'::regclass) then
    raise exception '609: RLS is off on collection_shares';
  end if;
  select count(*) into found_count
    from pg_policies
   where schemaname = 'public'
     and cmd = expected ->> (tablename || '.' || policyname)
     and permissive = 'PERMISSIVE'
     and roles = array['authenticated']::name[];
  if found_count <> 5 then
    raise exception '609: expected 5 share policies (cmd + roles = {authenticated}), found %', found_count;
  end if;
  if (select count(*) from pg_policies where schemaname = 'public' and tablename = 'collection_shares') <> 1 then
    raise exception '609: collection_shares must carry exactly one policy';
  end if;
  if has_table_privilege('authenticated', 'public.collection_shares', 'INSERT')
     or has_table_privilege('authenticated', 'public.collection_shares', 'UPDATE')
     or has_table_privilege('anon', 'public.collection_shares', 'SELECT') then
    raise exception '609: collection_shares grants drifted (no INSERT/UPDATE for authenticated, nothing for anon)';
  end if;
  if has_function_privilege('authenticated', 'public.account_email(uuid)', 'EXECUTE')
     or has_function_privilege('anon', 'public.account_email(uuid)', 'EXECUTE')
     or has_function_privilege('anon', 'public.share_party_emails()', 'EXECUTE')
     or not has_function_privilege('authenticated', 'public.share_party_emails()', 'EXECUTE') then
    raise exception '609: label function grants drifted';
  end if;
  if (select count(*) from pg_constraint
       where conname in ('notification_dispatches_source_ck', 'notification_dispatches_change_kind_ck')
         and conrelid = 'public.notification_dispatches'::regclass) <> 2
     or not exists (select 1 from pg_constraint
                     where conname = 'collection_shares_not_self'
                       and conrelid = 'public.collection_shares'::regclass) then
    raise exception '609: constraints missing';
  end if;
  if not exists (select 1 from pg_trigger
                  where tgname = 'property_notes_shared_at_biu'
                    and tgrelid = 'public.property_notes'::regclass) then
    raise exception '609: the shared_at trigger is missing';
  end if;
  if to_regprocedure('public.sync_collection_properties_account_id()') is not null
     or to_regprocedure('public.sync_account_id_from_collection()') is null then
    raise exception '609: the collection account trigger function was not renamed';
  end if;
end $$;

commit;
