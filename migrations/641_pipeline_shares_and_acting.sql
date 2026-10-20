-- 641: pipeline sharing, and acting as the owner (docs/design/pipeline-sharing.md §3.1).
--
-- NORTH STAR. Sharing a pipeline lets its recipient ACT AS the owner, on the owner's curation only,
-- one request at a time, through one identity function. A share is one row (owner, recipient, mode).
--
-- TWO QUESTIONS, TWO FUNCTIONS. current_account_ids() (286) stays the untouched answer to "who is the
-- caller": billing, estimations, watchdog, presets, notifications and both share tables keep it.
-- acting_account_ids() answers "whom does the caller act FOR on curation", and only the eleven
-- curation policies switch to it (ALTER POLICY, never drop: no unpoliced window): collections, tags,
-- property_notes, collection_properties, property_tags, property_note_attachments,
-- property_artifact_links, property_dismissals, property_pipeline, pipeline_stages,
-- property_pipeline_events. Without the header the two functions return the same rows.
--
-- THE HEADER AND THE METHOD. PostgREST exposes every request's headers (JSON, lower-case keys) and
-- method as the transaction-local GUCs request.headers / request.method; the API's tenant
-- transaction sets the same two next to request.jwt.claims. `x-act-as: <owner account>` names the
-- owner. The function grants it when that owner shared their pipeline with the caller: edit mode
-- always, read mode only on GET/HEAD, so a read grant's POST/PATCH/DELETE sees no rows (UPDATE and
-- DELETE touch nothing, INSERT fails WITH CHECK). A missing method counts as a write.
--
-- READ MODE IS THE BOARD ONLY (ruling 2). A read grant reaches the three pipeline tables
-- (pipeline_stages, property_pipeline, property_pipeline_events), whose policies ask
-- acting_account_ids(true); the other eight ask acting_account_ids() and so act for an EDIT grant
-- only: a read recipient never reads the owner's notes, files, tags, collections, dismissals or
-- artifact links.
--
-- REPLACE, NEVER UNION. While acting, the eleven tables show the owner's rows and nothing of the
-- caller's own, so the card map, the entry stage, the dismissal rule and Browse's curated scope stay
-- single-account by construction. FAIL CLOSED: a header naming an account that did not share (or whose
-- share was revoked) yields an EMPTY identity, never the caller's own rows. The collection share rows
-- are the caller's own consent, so collection_shares_party gains `not acting()`: the four
-- *_shared_read arms add nothing while acting, and a delegate neither sees nor revokes the owner's
-- shares. pipeline_shares' party policy carries the same clause.
--
-- THE SHARE ROW. Browser roles get SELECT + DELETE, no INSERT (account uuids are learnable; the API
-- creates the grant on the service role after resolving a confirmed email, as for collections).
-- Either party deletes: the owner revokes, the recipient leaves.
--
-- THE LEDGER. Every delegated write is stamped in delegated_edits (actor user + account, owner,
-- relation, row key, op, time) by ONE trigger function on the eleven tables, which fires only while
-- acting; the PK column names reach it as trigger arguments. No column lands on a curation table.
-- Only the owner reads the ledger. latest_delegated_edit() is the ONE definition of a row's stamp:
-- its latest delegated edit, unless the row changed after it (the owner's own later write shows
-- nothing). The board views and the API projections call it. The label door for the actor is
-- share_party_emails(), now over both share kinds and, for the owner, every actor in their ledger
-- (a revoked delegate stays named). An actor's account deletion nulls the actor, never the history.
--
-- ONE NOTIFICATION KIND. source_kind 'collection_share' becomes 'share' for both kinds
-- (collection_id set for a collection share, NULL for a pipeline share); the existing rows move.
-- APPLY INSIDE THE MERGE WINDOW, just before the API deploy: the API on main still writes
-- 'collection_share', which this CHECK refuses from here on.
--
-- acting_account_ids(for_board) RETURNS TABLE (account_id uuid): a single-column set, so a policy's
-- `in (select acting_account_ids())` reads it exactly like current_account_ids(), and the API reads
-- `select account_id from acting_account_ids()`. `union`, not `union all`: a caller with two
-- memberships that both hold a share from one owner still acts for ONE account.
--
-- ROLLBACK: forward only (rule 1).

set lock_timeout = '5s';

begin;

create table pipeline_shares (
  account_id           uuid        not null references accounts(id) on delete cascade,
  recipient_account_id uuid        not null references accounts(id) on delete cascade,
  mode                 text        not null check (mode in ('read', 'edit')),
  created_at           timestamptz not null default now(),
  primary key (account_id, recipient_account_id),
  constraint pipeline_shares_not_self check (recipient_account_id <> account_id)
);
create index pipeline_shares_recipient_idx on pipeline_shares (recipient_account_id);

create function acting() returns boolean
language sql stable set search_path = '' as $$
  select coalesce(nullif(current_setting('request.headers', true), '')::json ->> 'x-act-as', '') <> ''
$$;
revoke all on function acting() from public, anon, authenticated;
grant execute on function acting() to authenticated, service_role;

create function acting_account_ids(for_board boolean default false) returns table (account_id uuid)
language sql stable security definer set search_path = public as $$
  with me as (
    select am.account_id
      from account_members am
     where am.user_id = nullif(nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub', '')::uuid),
  asked as (
    select nullif(nullif(current_setting('request.headers', true), '')::json ->> 'x-act-as', '')::uuid as id)
  select me.account_id from me, asked where asked.id is null
  union
  select s.account_id
    from pipeline_shares s, asked
   where s.account_id = asked.id
     and s.recipient_account_id in (select me.account_id from me)
     and (s.mode = 'edit'
          or (for_board and coalesce(current_setting('request.method', true), '') in ('GET', 'HEAD')))
$$;
revoke all on function acting_account_ids(boolean) from public, anon, authenticated;
grant execute on function acting_account_ids(boolean) to authenticated;

alter table pipeline_shares enable row level security;
revoke all on pipeline_shares from anon, authenticated;
grant select, delete on pipeline_shares to authenticated;
create policy pipeline_shares_party on pipeline_shares
  for all to authenticated
  using ((account_id in (select current_account_ids())
          or recipient_account_id in (select current_account_ids()))
         and not acting())
  with check (account_id in (select current_account_ids()));

comment on table pipeline_shares is
  'A grant of one account''s pipeline (and its curation) to another account (migration 641): the '
  'recipient may send x-act-as: <account_id> and act as the owner: read mode reads the board (the '
  'three pipeline tables), edit mode reads and writes all eleven curation tables. Created only by '
  'the API (no browser INSERT); '
  'either party may delete (owner revokes, recipient leaves).';

alter policy collections_tenant_rw on collections
  using (account_id in (select acting_account_ids()))
  with check (account_id in (select acting_account_ids()));
alter policy tags_tenant_rw on tags
  using (account_id in (select acting_account_ids()))
  with check (account_id in (select acting_account_ids()));
alter policy property_notes_tenant_rw on property_notes
  using (account_id in (select acting_account_ids()))
  with check (account_id in (select acting_account_ids()));
alter policy collection_properties_tenant_rw on collection_properties
  using (account_id in (select acting_account_ids()))
  with check (account_id in (select acting_account_ids()));
alter policy property_tags_tenant_rw on property_tags
  using (account_id in (select acting_account_ids()))
  with check (account_id in (select acting_account_ids()));
alter policy property_note_attachments_tenant_rw on property_note_attachments
  using (account_id in (select acting_account_ids()))
  with check (account_id in (select acting_account_ids()));
alter policy property_artifact_links_tenant_rw on property_artifact_links
  using (account_id in (select acting_account_ids()))
  with check (account_id in (select acting_account_ids()));
alter policy property_dismissals_tenant_rw on property_dismissals
  using (account_id in (select acting_account_ids()))
  with check (account_id in (select acting_account_ids()));
alter policy property_pipeline_tenant_rw on property_pipeline
  using (account_id in (select acting_account_ids(true)))
  with check (account_id in (select acting_account_ids(true)));
alter policy pipeline_stages_tenant_rw on pipeline_stages
  using (account_id in (select acting_account_ids(true)))
  with check (account_id in (select acting_account_ids(true)));
alter policy property_pipeline_events_tenant_rw on property_pipeline_events
  using (account_id in (select acting_account_ids(true)))
  with check (account_id in (select acting_account_ids(true)));

alter policy collection_shares_party on collection_shares
  using ((account_id in (select current_account_ids())
          or recipient_account_id in (select current_account_ids()))
         and not acting());

create table delegated_edits (
  id               bigint      generated always as identity primary key,
  at               timestamptz not null default now(),
  actor_user_id    uuid        not null,
  actor_account_id uuid        references accounts(id) on delete set null,
  owner_account_id uuid        not null references accounts(id) on delete cascade,
  relation         text        not null,
  row_key          text        not null,
  op               text        not null check (op in ('insert', 'update', 'delete'))
);
create index delegated_edits_owner_idx on delegated_edits (owner_account_id, at desc);
create index delegated_edits_row_idx on delegated_edits (relation, row_key, at desc);
alter table delegated_edits enable row level security;
revoke all on delegated_edits from anon, authenticated;
grant select on delegated_edits to authenticated;
create policy delegated_edits_owner_read on delegated_edits
  for select to authenticated
  using (owner_account_id in (select current_account_ids()));

comment on table delegated_edits is
  'Append-only ledger of every write made while acting as another account (migration 641): who '
  '(the JWT sub and the share''s recipient account), as whom (the owner), which row (relation + '
  'primary key joined by '':''), what (op) and when. Written only by stamp_delegated_edit(); the '
  'owner reads it, nobody else.';

-- The actor account is the share's recipient party, the one share_party_emails() labels for the owner.
create function stamp_delegated_edit() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_row   jsonb := case when tg_op = 'DELETE' then to_jsonb(old) else to_jsonb(new) end;
  v_owner uuid  := (select a.account_id from acting_account_ids() a limit 1);
begin
  insert into delegated_edits (actor_user_id, actor_account_id, owner_account_id, relation, row_key, op)
  values (
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')::uuid,
    (select s.recipient_account_id
       from pipeline_shares s
      where s.account_id = v_owner
        and s.recipient_account_id in (select current_account_ids())
      order by s.created_at, s.recipient_account_id
      limit 1),
    v_owner,
    tg_table_name,
    (select string_agg(v_row ->> k.col, ':' order by k.n) from unnest(tg_argv) with ordinality k(col, n)),
    lower(tg_op));
  return null;
end;
$$;
revoke all on function stamp_delegated_edit() from public, anon, authenticated;

-- A row's stamp, defined once: its latest delegated edit, or nothing when the row changed after it
-- (changed_at: the row's updated_at, so the owner's later write clears it, and a re-created row
-- with a reused key starts clean). Invoker rights: the ledger's owner-read policy decides who sees it.
create function latest_delegated_edit(of_relation text, of_row text, changed_at timestamptz)
returns table (actor_account_id uuid, at timestamptz)
language sql stable set search_path = '' as $$
  select d.actor_account_id, d.at
    from public.delegated_edits d
   where d.relation = of_relation
     and d.row_key = of_row
     and (changed_at is null or d.at >= changed_at)
   order by d.at desc, d.id desc
   limit 1
$$;
revoke all on function latest_delegated_edit(text, text, timestamptz) from public, anon, authenticated;
grant execute on function latest_delegated_edit(text, text, timestamptz) to authenticated;

create trigger collections_delegated_edit
  after insert or update or delete on collections
  for each row when (acting()) execute function stamp_delegated_edit('id');
create trigger tags_delegated_edit
  after insert or update or delete on tags
  for each row when (acting()) execute function stamp_delegated_edit('id');
create trigger property_notes_delegated_edit
  after insert or update or delete on property_notes
  for each row when (acting()) execute function stamp_delegated_edit('id');
create trigger collection_properties_delegated_edit
  after insert or update or delete on collection_properties
  for each row when (acting()) execute function stamp_delegated_edit('collection_id', 'property_id');
create trigger property_tags_delegated_edit
  after insert or update or delete on property_tags
  for each row when (acting()) execute function stamp_delegated_edit('property_id', 'tag_id');
create trigger property_note_attachments_delegated_edit
  after insert or update or delete on property_note_attachments
  for each row when (acting()) execute function stamp_delegated_edit('id');
create trigger property_artifact_links_delegated_edit
  after insert or update or delete on property_artifact_links
  for each row when (acting()) execute function stamp_delegated_edit('account_id', 'property_id');
create trigger property_dismissals_delegated_edit
  after insert or update or delete on property_dismissals
  for each row when (acting()) execute function stamp_delegated_edit('id');
create trigger property_pipeline_delegated_edit
  after insert or update or delete on property_pipeline
  for each row when (acting()) execute function stamp_delegated_edit('account_id', 'property_id');
create trigger pipeline_stages_delegated_edit
  after insert or update or delete on pipeline_stages
  for each row when (acting()) execute function stamp_delegated_edit('id');
create trigger property_pipeline_events_delegated_edit
  after insert or update or delete on property_pipeline_events
  for each row when (acting()) execute function stamp_delegated_edit('id');

-- 610's body over both share kinds; still the caller's counterparties only, never the caller. The
-- last arm names, to an OWNER only, every actor in their own ledger, so a stamp outlives a revoke.
create or replace function share_party_emails()
returns table (account_id uuid, email text)
language sql stable security definer set search_path = '' as $$
  select p.id, public.account_email(p.id)
    from (select c.account_id, c.recipient_account_id from public.collection_shares c
          union all
          select q.account_id, q.recipient_account_id from public.pipeline_shares q) s
    cross join lateral (values (s.account_id), (s.recipient_account_id)) p(id)
   where (s.account_id in (select public.current_account_ids())
          or s.recipient_account_id in (select public.current_account_ids()))
     and p.id not in (select public.current_account_ids())
  union
  select d.actor_account_id, public.account_email(d.actor_account_id)
    from public.delegated_edits d
   where d.owner_account_id in (select public.current_account_ids())
     and d.actor_account_id is not null
     and d.actor_account_id not in (select public.current_account_ids())
$$;
revoke all on function share_party_emails() from public, anon, authenticated;
grant execute on function share_party_emails() to authenticated;

alter table notification_dispatches drop constraint notification_dispatches_source_ck;

create or replace function sync_notification_dispatches_account_id()
returns trigger language plpgsql as $$
begin
  if new.source_kind = 'watchdog' then
    select account_id into new.account_id from notification_subscriptions where id = new.subscription_id;
  elsif new.source_kind = 'collection_monitor' then
    select account_id into new.account_id from collections where id = new.collection_id;
  elsif new.source_kind = 'share' then
    if new.account_id is null then
      raise exception 'share dispatch needs its recipient account';
    end if;
  else
    new.account_id := '00000000-0000-0000-0000-000000000000';
  end if;
  return new;
end;
$$;

-- The keys take the kind too ('share:<collection>:<recipient>' -> 'share:collection:<collection>:<recipient>'),
-- the shape api/shares.py writes from here on, so a re-share of an already-notified collection stays silent.
update notification_dispatches
   set source_kind = 'share',
       dedupe_key  = 'share:collection:' || substr(dedupe_key, length('share:') + 1)
 where source_kind = 'collection_share';

alter table notification_dispatches add constraint notification_dispatches_source_ck check (
     (source_kind = 'watchdog'           and subscription_id is not null and collection_id is null)
  or (source_kind = 'collection_monitor' and collection_id   is not null and subscription_id is null)
  or (source_kind = 'share'              and subscription_id is null)
  or (source_kind = 'system_health'      and subscription_id is null     and collection_id is null));

-- The board views: their latest bodies (377, 618) with the row's stamp (latest_delegated_edit) APPENDED.
-- The ledger's own policy decides who sees a stamp (the owner). Scalar subqueries, not a lateral
-- join: a reader that never selects the stamp (Browse's curated scope tests card existence on
-- property_pipeline_public, 629/630) never evaluates them.
create or replace view pipeline_stages_public
with (security_invoker = true) as
  select ps.id, ps.key, ps.label, ps.position, ps.color, ps.is_terminal, ps.is_entry, ps.code,
         (select l.actor_account_id
            from latest_delegated_edit('pipeline_stages', ps.id::text, ps.updated_at) l)
           as edited_by_account_id,
         (select l.at
            from latest_delegated_edit('pipeline_stages', ps.id::text, ps.updated_at) l)
           as edited_at
  from pipeline_stages ps
  where ps.archived_at is null;

revoke all on public.pipeline_stages_public from public, anon, authenticated;
grant select on public.pipeline_stages_public to authenticated;

create or replace view property_pipeline_public
with (security_invoker = true) as
  select pp.property_id, pp.stage_id, ps.key as stage_key, ps.label as stage_label,
         ps.position as stage_position, ps.color as stage_color, ps.is_terminal,
         pp.board_position, pp.entered_stage_at, pp.added_at, pp.updated_at,
         ps.code as stage_code,
         (select l.actor_account_id
            from latest_delegated_edit('property_pipeline',
                                       pp.account_id::text || ':' || pp.property_id::text,
                                       pp.updated_at) l)
           as edited_by_account_id,
         (select l.at
            from latest_delegated_edit('property_pipeline',
                                       pp.account_id::text || ':' || pp.property_id::text,
                                       pp.updated_at) l)
           as edited_at
  from property_pipeline pp
  join pipeline_stages ps on ps.id = pp.stage_id;

revoke all on public.property_pipeline_public from public, anon, authenticated;
grant select on public.property_pipeline_public to authenticated;

create or replace view pipeline_board_public
with (security_invoker = true) as
  select
    pp.property_id,
    pp.stage_id,
    pp.board_position,
    pp.entered_stage_at,
    pp.added_at,
    p.sreality_id,
    p.source,
    p.source_id_native,
    p.listing_id,
    p.category_main,
    p.disposition,
    p.subtype,
    p.area_m2,
    p.price_czk,
    p.mf_gross_yield_pct,
    p.total_price_change_pct,
    p.price_change_count,
    p.obec_id,
    p.okres_id,
    p.region_id,
    p.obec,
    p.is_active,
    p.category_type,
    p.price_per_m2,
    p.price_per_m2_basis,
    p.display_label,
    p.cast_obce_id,
    p.ulice_id,
    pp.edited_by_account_id,
    pp.edited_at
  from property_pipeline_public pp
  left join properties_public p on p.property_id = pp.property_id;

revoke all on public.pipeline_board_public from public, anon, authenticated;
grant select on public.pipeline_board_public to authenticated;

do $$
declare
  board constant text[] := array[
    'property_pipeline.property_pipeline_tenant_rw',
    'pipeline_stages.pipeline_stages_tenant_rw',
    'property_pipeline_events.property_pipeline_events_tenant_rw'];
  curation constant text[] := array[
    'collections.collections_tenant_rw',
    'tags.tags_tenant_rw',
    'property_notes.property_notes_tenant_rw',
    'collection_properties.collection_properties_tenant_rw',
    'property_tags.property_tags_tenant_rw',
    'property_note_attachments.property_note_attachments_tenant_rw',
    'property_artifact_links.property_artifact_links_tenant_rw',
    'property_dismissals.property_dismissals_tenant_rw',
    'property_pipeline.property_pipeline_tenant_rw',
    'pipeline_stages.pipeline_stages_tenant_rw',
    'property_pipeline_events.property_pipeline_events_tenant_rw'];
  acting_ids uuid[];
  own_ids    uuid[];
  member     uuid;
begin
  if not (select relrowsecurity from pg_class where oid = 'public.pipeline_shares'::regclass)
     or not (select relrowsecurity from pg_class where oid = 'public.delegated_edits'::regclass) then
    raise exception '641: RLS is off on pipeline_shares or delegated_edits';
  end if;

  if (select count(*) from pg_policies
       where schemaname = 'public'
         and tablename || '.' || policyname = any (curation)
         and cmd = 'ALL' and permissive = 'PERMISSIVE'
         and roles = array['authenticated']::name[]
         and position('current_account_ids' in qual || with_check) = 0
         and case when tablename || '.' || policyname = any (board)
                  then position('acting_account_ids(true)' in qual) > 0
                       and position('acting_account_ids(true)' in with_check) > 0
                  else position('acting_account_ids()' in qual) > 0
                       and position('acting_account_ids()' in with_check) > 0
              end) <> 11 then
    raise exception '641: the eleven curation policies do not all read acting_account_ids() (the board three: (true))';
  end if;
  if (select count(*) from pg_policies
       where schemaname = 'public'
         and position('acting_account_ids' in coalesce(qual, '') || coalesce(with_check, '')) > 0) <> 11 then
    raise exception '641: a policy outside the eleven curation tables reads acting_account_ids()';
  end if;

  if not exists (select 1 from pg_policies
                  where schemaname = 'public' and tablename = 'collection_shares'
                    and policyname = 'collection_shares_party' and cmd = 'ALL'
                    and roles = array['authenticated']::name[]
                    and position('not acting()' in lower(qual)) > 0
                    and position('current_account_ids()' in qual) > 0) then
    raise exception '641: collection_shares_party does not hide shares while acting';
  end if;
  if (select count(*) from pg_policies where schemaname = 'public' and tablename = 'pipeline_shares') <> 1
     or not exists (select 1 from pg_policies
                     where schemaname = 'public' and tablename = 'pipeline_shares'
                       and policyname = 'pipeline_shares_party' and cmd = 'ALL'
                       and permissive = 'PERMISSIVE' and roles = array['authenticated']::name[]
                       and position('not acting()' in lower(qual)) > 0) then
    raise exception '641: pipeline_shares must carry exactly its party policy';
  end if;
  if (select count(*) from pg_policies where schemaname = 'public' and tablename = 'delegated_edits') <> 1
     or not exists (select 1 from pg_policies
                     where schemaname = 'public' and tablename = 'delegated_edits'
                       and policyname = 'delegated_edits_owner_read' and cmd = 'SELECT'
                       and roles = array['authenticated']::name[]) then
    raise exception '641: delegated_edits must carry exactly its owner-read policy';
  end if;

  if has_table_privilege('authenticated', 'public.pipeline_shares', 'INSERT')
     or has_table_privilege('authenticated', 'public.pipeline_shares', 'UPDATE')
     or not has_table_privilege('authenticated', 'public.pipeline_shares', 'SELECT')
     or not has_table_privilege('authenticated', 'public.pipeline_shares', 'DELETE')
     or has_table_privilege('anon', 'public.pipeline_shares', 'SELECT')
     or has_table_privilege('authenticated', 'public.delegated_edits', 'INSERT')
     or has_table_privilege('authenticated', 'public.delegated_edits', 'UPDATE')
     or has_table_privilege('authenticated', 'public.delegated_edits', 'DELETE')
     or not has_table_privilege('authenticated', 'public.delegated_edits', 'SELECT')
     or has_table_privilege('anon', 'public.delegated_edits', 'SELECT') then
    raise exception '641: share or ledger table grants drifted';
  end if;
  if not has_function_privilege('authenticated', 'public.acting()', 'EXECUTE')
     or not has_function_privilege('authenticated', 'public.acting_account_ids(boolean)', 'EXECUTE')
     or not has_function_privilege('authenticated', 'public.latest_delegated_edit(text, text, timestamptz)', 'EXECUTE')
     or has_function_privilege('anon', 'public.latest_delegated_edit(text, text, timestamptz)', 'EXECUTE')
     or not has_function_privilege('authenticated', 'public.share_party_emails()', 'EXECUTE')
     or has_function_privilege('anon', 'public.acting()', 'EXECUTE')
     or has_function_privilege('anon', 'public.acting_account_ids(boolean)', 'EXECUTE')
     or has_function_privilege('anon', 'public.share_party_emails()', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.stamp_delegated_edit()', 'EXECUTE')
     or has_function_privilege('anon', 'public.stamp_delegated_edit()', 'EXECUTE') then
    raise exception '641: function grants drifted';
  end if;

  -- No header: the two identity functions agree for every member (up to 50), and acting() is false.
  -- A header naming an account that shared nothing: acting() is true and the identity is EMPTY.
  -- set_config(..., true) is transaction-local: nothing outlives this migration's commit.
  perform set_config('request.headers', '{}', true);
  if public.acting() then
    raise exception '641: acting() is true with no x-act-as header';
  end if;
  for member in select distinct am.user_id from account_members am order by am.user_id limit 50 loop
    perform set_config('request.jwt.claims', json_build_object('sub', member)::text, true);
    select array_agg(a.account_id order by a.account_id) into acting_ids from public.acting_account_ids() a;
    select array_agg(c.id order by c.id) into own_ids from public.current_account_ids() c(id);
    if acting_ids is distinct from own_ids then
      raise exception '641: acting_account_ids() differs from current_account_ids() for user % with no header', member;
    end if;
    select array_agg(a.account_id order by a.account_id) into acting_ids from public.acting_account_ids(true) a;
    if acting_ids is distinct from own_ids then
      raise exception '641: acting_account_ids(true) differs from current_account_ids() for user % with no header', member;
    end if;
    perform set_config('request.headers', json_build_object('x-act-as', gen_random_uuid())::text, true);
    if not public.acting() or exists (select 1 from public.acting_account_ids(true)) then
      raise exception '641: a header naming no share must yield an empty identity (user %)', member;
    end if;
    perform set_config('request.headers', '{}', true);
  end loop;
  perform set_config('request.jwt.claims', '{}', true);

  if (select count(*) from pg_trigger
       where tgfoid = 'public.stamp_delegated_edit()'::regprocedure and not tgisinternal) <> 11 then
    raise exception '641: expected the ledger trigger on exactly the eleven curation tables';
  end if;

  if (select count(*) from pg_class
       where oid in ('public.pipeline_stages_public'::regclass, 'public.property_pipeline_public'::regclass,
                     'public.pipeline_board_public'::regclass)
         and reloptions @> array['security_invoker=true']) <> 3
     or (select count(*) from information_schema.columns
          where table_schema = 'public'
            and table_name in ('pipeline_stages_public', 'property_pipeline_public', 'pipeline_board_public')
            and column_name in ('edited_by_account_id', 'edited_at')) <> 6 then
    raise exception '641: a board view lost security_invoker or the stamp columns';
  end if;

  if exists (select 1 from notification_dispatches where source_kind = 'collection_share')
     or exists (select 1 from notification_dispatches
                 where source_kind = 'share' and collection_id is not null
                   and left(dedupe_key, 17) <> 'share:collection:') then
    raise exception '641: a collection_share dispatch survived the rename';
  end if;
end $$;

commit;
