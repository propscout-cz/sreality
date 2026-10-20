-- 647_saved_dashboards.sql -- saved dashboards: one saved-set primitive, shared read-only.
--
-- WHY. Operator ask + rulings 2026-10-20 (docs/design/saved-dashboards.md, "all as proposed"): the Stats chart
-- set can be saved as a NAMED dashboard, returned to, and shared with other accounts like the pipeline, with the
-- Browse filter optionally embedded. A saved set is a named row an account owns, lists, loads, updates, reorders
-- and shares; filter presets and dashboards are its two instances. Amends docs/design/stats-dashboard.md ruling 2:
-- the WORKING board is one per account, named dashboards are many.
--
-- WHAT CHANGES.
--   1. public.stats_dashboards is re-keyed: `id uuid` becomes the primary key (626's was account_id), and a row
--      gains `name` (1..200, or NULL = the account's working board), `filter_spec` (the preset blob format,
--      NULL = no filter), `position`, `color` (201's palette CHECK, verbatim) and `created_at`. A partial unique
--      index keeps ONE working board per account and is the upsert target of PUT /stats/dashboard
--      (`ON CONFLICT (account_id) WHERE name IS NULL`). The existing rows (one per account, no name) become the
--      working boards untouched. account_id stays NOT NULL, stated again because the old key carried it.
--      A row's name never crosses NULL: the BEFORE UPDATE OF name trigger stats_dashboards_name_guard
--      (guard_stats_dashboard_name()) refuses a named dashboard becoming the working board and the working
--      board being named in place ("Uložit jako dashboard" always INSERTs a new row, so the working row keeps
--      its identity). The API never does either; the guard closes a direct PostgREST UPDATE (the browser
--      holds UPDATE on the table), which would otherwise turn a SHARED dashboard into a working board.
--      The same direct write cannot hand a recipient junk either: `charts` is a JSON array of at most 256 KiB,
--      `filter_spec` NULL or a JSON object of at most 64 KiB (stats_dashboards_charts_check /
--      stats_dashboards_filter_spec_check). The old primary key is dropped by its catalog name.
--   2. 626's FOR ALL policy stats_dashboards_tenant_rw is untouched: it reads current_account_ids(), so a
--      delegate acting under a pipeline share reads no dashboard of the owner (pipeline-sharing.md §3.7). A
--      FOR SELECT policy, stats_dashboards_shared_read, lets a share's recipient READ a shared NAMED dashboard
--      (it names `name is not null` itself, belt and braces with the trigger below); PostgreSQL ORs permissive
--      policies per command, so writes stay the owner's (a recipient's UPDATE or DELETE by id matches no row,
--      the API's 404).
--   3. public.stats_dashboard_shares: one row per (dashboard, recipient account), 610's shape. account_id is
--      the OWNER, stamped by the BEFORE INSERT OR UPDATE OF dashboard_id trigger sync_account_id_from_dashboard(),
--      which also refuses a dashboard with no name: the working board is never shareable, by the database. Browser roles get
--      SELECT + DELETE and NO INSERT (account uuids are learnable): the API creates a share on the service role
--      after resolving a confirmed email (POST /stats/dashboards/{id}/shares). Either party deletes (the owner
--      revokes, the recipient leaves). The party policy carries 641's `not acting()`: a share row is the
--      caller's own consent, so while acting the shared-read arm adds nothing.
--   4. share_party_emails(): 641's body with a third arm over stats_dashboard_shares, still the caller's
--      counterparties only. The share notification needs no CHECK change (641: a 'share' row with
--      collection_id NULL is legal); its key is `share:dashboard:<dashboard>:<recipient>` (api/shares.py).
--
-- NOT HERE. No carrier entry (a dashboard and its shares name no property); no curation policy moves (the
-- eleven acting tables are untouched); current_account_ids() is untouched -- a share is never membership.
--
-- APPLY through apply_migration.yml (--ref feature/saved-dashboards-api), IMMEDIATELY before merging the API
-- that reads it: from the apply until the API rollout, the deployed PUT /stats/dashboard's
-- `ON CONFLICT (account_id)` has no index to infer (42P10), so a Stats board save in those minutes fails and
-- the SPA keeps its unsaved board; nothing stored is lost. Re-runnable: every statement is idempotent (the key
-- swap is guarded), so the workflow's lock-timeout retry and a second apply are no-ops.
-- LOCKS. stats_dashboards holds a few rows; the ALTERs take ACCESS EXCLUSIVE for milliseconds behind a 5 s
-- lock_timeout, so a reader in the way fails the attempt (and the workflow retries) instead of queueing.
-- ROLLBACK: forward only (rule 1).

set lock_timeout = '5s';
set statement_timeout = '60s';

begin;

-- ---------------------------------------------------------------------------
-- 1. stats_dashboards: id is the key; name, filter, order, colour, birth.
-- ---------------------------------------------------------------------------
alter table public.stats_dashboards add column if not exists id uuid not null default gen_random_uuid();
alter table public.stats_dashboards add column if not exists name text
  constraint stats_dashboards_name_check check (name is null or length(name) between 1 and 200);
alter table public.stats_dashboards add column if not exists filter_spec jsonb;
alter table public.stats_dashboards add column if not exists position integer not null default 0;
alter table public.stats_dashboards add column if not exists color text
  constraint stats_dashboards_color_check check (color is null or color in (
      'copper', 'sage', 'brick', 'ochre',
      'slate',  'plum', 'teal',  'sand'
  ));
alter table public.stats_dashboards add column if not exists created_at timestamptz not null default now();

-- ci-allow-dynamic: stats_dashboards' old primary key is dropped by the name the catalog gives it (no CREATE)
do $key$
declare
  old_key text;
begin
  select k.conname into old_key
    from pg_constraint k
    join pg_attribute a on a.attrelid = k.conrelid and a.attnum = any (k.conkey)
   where k.conrelid = 'public.stats_dashboards'::regclass and k.contype = 'p'
     and a.attname = 'account_id';
  if old_key is not null then
    execute format('alter table public.stats_dashboards drop constraint %I', old_key);
    alter table public.stats_dashboards add constraint stats_dashboards_pkey primary key (id);
  end if;
end
$key$;
alter table public.stats_dashboards alter column account_id set not null;

alter table public.stats_dashboards drop constraint if exists stats_dashboards_charts_check;
alter table public.stats_dashboards add constraint stats_dashboards_charts_check
  check (jsonb_typeof(charts) = 'array' and pg_column_size(charts) <= 262144);
alter table public.stats_dashboards drop constraint if exists stats_dashboards_filter_spec_check;
alter table public.stats_dashboards add constraint stats_dashboards_filter_spec_check
  check (filter_spec is null
         or (jsonb_typeof(filter_spec) = 'object' and pg_column_size(filter_spec) <= 65536));

create index if not exists stats_dashboards_account_idx on public.stats_dashboards (account_id);
create unique index if not exists stats_dashboards_one_working_per_account
  on public.stats_dashboards (account_id) where name is null;

create or replace function public.guard_stats_dashboard_name()
returns trigger language plpgsql set search_path = '' as $$
begin
  if old.name is not null and new.name is null then
    raise exception 'stats_dashboards %: a named dashboard never becomes the working board', old.id;
  end if;
  if old.name is null and new.name is not null then
    raise exception 'stats_dashboards %: the working board is never named in place (save a new dashboard)', old.id;
  end if;
  return new;
end;
$$;
revoke all on function public.guard_stats_dashboard_name() from public, anon, authenticated;

drop trigger if exists stats_dashboards_name_guard on public.stats_dashboards;
create trigger stats_dashboards_name_guard
  before update of name on public.stats_dashboards
  for each row execute function public.guard_stats_dashboard_name();

comment on table public.stats_dashboards is
  'Browse > Stats boards (migrations 626, 647). One unnamed row per account is its WORKING board '
  '(GET/PUT /stats/dashboard; partial unique index); every named row is a saved dashboard the account lists, '
  'loads, reorders and may share read-only (stats_dashboard_shares). A name never crosses NULL '
  '(stats_dashboards_name_guard). charts are validated by toolkit.stats_registry.validate_board; filter_spec '
  'is the filter-preset blob, NULL = no filter.';
comment on column public.stats_dashboards.id is
  'The row''s identity (647; was account_id): the saved dashboard''s id, or the working board''s.';
comment on column public.stats_dashboards.name is
  'The dashboard''s name; NULL = the account''s one working board. Never crosses NULL on UPDATE.';
comment on column public.stats_dashboards.filter_spec is
  'The Browse filter saved with the dashboard, in the filter-preset blob format; NULL = no filter.';
comment on column public.stats_dashboards.position is
  'The dashboard''s place in its owner''s list (0 = first); rewritten by PUT /stats/dashboards/reorder.';
comment on column public.stats_dashboards.color is
  'Optional chip colour from the shared tag palette (migration 201''s CHECK); NULL = neutral.';

-- ---------------------------------------------------------------------------
-- 2. The share row: owner-stamped, never of the working board, no browser INSERT.
-- ---------------------------------------------------------------------------
create table if not exists public.stats_dashboard_shares (
  dashboard_id         uuid        not null references public.stats_dashboards(id) on delete cascade,
  account_id           uuid        not null references public.accounts(id) on delete cascade,
  recipient_account_id uuid        not null references public.accounts(id) on delete cascade,
  created_at           timestamptz not null default now(),
  primary key (dashboard_id, recipient_account_id),
  constraint stats_dashboard_shares_not_self check (recipient_account_id <> account_id)
);
create index if not exists stats_dashboard_shares_recipient_idx
  on public.stats_dashboard_shares (recipient_account_id);

create or replace function public.sync_account_id_from_dashboard()
returns trigger language plpgsql set search_path = '' as $$
declare
  dashboard_name text;
begin
  select d.account_id, d.name into new.account_id, dashboard_name
    from public.stats_dashboards d
   where d.id = new.dashboard_id;
  if new.account_id is not null and dashboard_name is null then
    raise exception 'stats_dashboards %: the working board is never shared', new.dashboard_id;
  end if;
  return new;
end;
$$;
revoke all on function public.sync_account_id_from_dashboard() from public, anon, authenticated;

drop trigger if exists stats_dashboard_shares_account_id_biu on public.stats_dashboard_shares;
create trigger stats_dashboard_shares_account_id_biu
  before insert or update of dashboard_id on public.stats_dashboard_shares
  for each row execute function public.sync_account_id_from_dashboard();

alter table public.stats_dashboard_shares enable row level security;
revoke all on public.stats_dashboard_shares from public, anon, authenticated;
grant select, delete on public.stats_dashboard_shares to authenticated;
drop policy if exists stats_dashboard_shares_party on public.stats_dashboard_shares;
create policy stats_dashboard_shares_party on public.stats_dashboard_shares
  for all to authenticated
  using ((account_id in (select current_account_ids())
          or recipient_account_id in (select current_account_ids()))
         and not acting())
  with check (account_id in (select current_account_ids()));

comment on table public.stats_dashboard_shares is
  'A read-only grant of one named Stats dashboard to one account (migration 647). account_id = the owner, '
  'stamped from the dashboard by trigger, which refuses the unnamed working board. Created only by the API '
  '(no browser INSERT); either party may delete (owner revokes, recipient leaves). Widens reads only.';

-- ---------------------------------------------------------------------------
-- 3. The recipient reads the dashboard; writes stay the owner's (626's policy).
-- ---------------------------------------------------------------------------
drop policy if exists stats_dashboards_shared_read on public.stats_dashboards;
create policy stats_dashboards_shared_read on public.stats_dashboards
  for select to authenticated
  using (name is not null and id in (select dashboard_id from public.stats_dashboard_shares));

-- ---------------------------------------------------------------------------
-- 4. The label door over all three share kinds (641's body + the dashboard arm).
-- ---------------------------------------------------------------------------
create or replace function public.share_party_emails()
returns table (account_id uuid, email text)
language sql stable security definer set search_path = '' as $$
  select p.id, public.account_email(p.id)
    from (select c.account_id, c.recipient_account_id from public.collection_shares c
          union all
          select q.account_id, q.recipient_account_id from public.pipeline_shares q
          union all
          select b.account_id, b.recipient_account_id from public.stats_dashboard_shares b) s
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
revoke all on function public.share_party_emails() from public, anon, authenticated;
grant execute on function public.share_party_emails() to authenticated;

-- ---------------------------------------------------------------------------
-- 5. Post-conditions (they hold on the empty replay schema too).
-- ---------------------------------------------------------------------------
do $post$
declare
  expected constant jsonb := jsonb_build_object(
    'stats_dashboards.stats_dashboards_tenant_rw', 'ALL',
    'stats_dashboards.stats_dashboards_shared_read', 'SELECT',
    'stats_dashboard_shares.stats_dashboard_shares_party', 'ALL');
begin
  if (select array_agg(a.attname::text order by a.attnum)
        from pg_constraint k
        join pg_attribute a on a.attrelid = k.conrelid and a.attnum = any (k.conkey)
       where k.conrelid = 'public.stats_dashboards'::regclass and k.contype = 'p') is distinct from array['id'] then
    raise exception '647 did not land: the primary key of stats_dashboards is not (id)';
  end if;
  if not (select a.attnotnull from pg_attribute a
           where a.attrelid = 'public.stats_dashboards'::regclass and a.attname = 'account_id') then
    raise exception '647 did not land: stats_dashboards.account_id lost NOT NULL with the old key';
  end if;
  if not exists (select 1 from pg_index i
                  where i.indexrelid = to_regclass('public.stats_dashboards_one_working_per_account')
                    and i.indisunique
                    and pg_get_indexdef(i.indexrelid) like '%USING btree (account_id) WHERE (name IS NULL)') then
    raise exception '647 did not land: no partial unique index keeps one working board per account';
  end if;
  if (select count(*) from pg_constraint
       where conrelid in ('public.stats_dashboards'::regclass, 'public.stats_dashboard_shares'::regclass)
         and conname in ('stats_dashboards_name_check', 'stats_dashboards_color_check',
                         'stats_dashboards_charts_check', 'stats_dashboards_filter_spec_check',
                         'stats_dashboard_shares_not_self')) <> 5 then
    raise exception '647 did not land: a name, colour, charts, filter or not-self CHECK is missing';
  end if;
  if (select array_agg(a.attname::text order by array_position(k.conkey, a.attnum))
        from pg_constraint k
        join pg_attribute a on a.attrelid = k.conrelid and a.attnum = any (k.conkey)
       where k.conrelid = 'public.stats_dashboard_shares'::regclass and k.contype = 'p')
     is distinct from array['dashboard_id', 'recipient_account_id'] then
    raise exception '647 did not land: the primary key of stats_dashboard_shares is not (dashboard_id, recipient_account_id)';
  end if;
  if not exists (select 1 from pg_constraint k
                  where k.conrelid = 'public.stats_dashboard_shares'::regclass and k.contype = 'f'
                    and k.confrelid = 'public.stats_dashboards'::regclass and k.confdeltype = 'c'
                    and k.conkey = array[(select a.attnum from pg_attribute a
                                           where a.attrelid = 'public.stats_dashboard_shares'::regclass
                                             and a.attname = 'dashboard_id')]
                    and k.confkey = array[(select a.attnum from pg_attribute a
                                            where a.attrelid = 'public.stats_dashboards'::regclass
                                              and a.attname = 'id')]) then
    raise exception '647 did not land: stats_dashboard_shares.dashboard_id does not cascade from stats_dashboards(id)';
  end if;

  if (select count(*) from pg_policies
       where schemaname = 'public'
         and cmd = expected ->> (tablename || '.' || policyname)
         and permissive = 'PERMISSIVE'
         and roles = array['authenticated']::name[]) <> 3 then
    raise exception '647 did not land: expected the three dashboard policies (cmd + roles = {authenticated})';
  end if;
  if (select count(*) from pg_policies where schemaname = 'public' and tablename = 'stats_dashboards') <> 2
     or (select count(*) from pg_policies where schemaname = 'public' and tablename = 'stats_dashboard_shares') <> 1 then
    raise exception '647 did not land: stats_dashboards carries its two policies, stats_dashboard_shares its one';
  end if;
  if not exists (select 1 from pg_policies
                  where schemaname = 'public' and policyname = 'stats_dashboard_shares_party'
                    and position('not acting()' in lower(qual)) > 0
                    and position('current_account_ids()' in qual) > 0) then
    raise exception '647 did not land: stats_dashboard_shares_party does not hide shares while acting';
  end if;
  if not exists (select 1 from pg_policies
                  where schemaname = 'public' and policyname = 'stats_dashboards_shared_read'
                    and position('name IS NOT NULL' in qual) > 0) then
    raise exception '647 did not land: stats_dashboards_shared_read reaches an unnamed row';
  end if;

  if not (select relrowsecurity from pg_class where oid = 'public.stats_dashboard_shares'::regclass) then
    raise exception '647 did not land: RLS is off on stats_dashboard_shares';
  end if;
  if has_table_privilege('authenticated', 'public.stats_dashboard_shares', 'INSERT')
     or has_table_privilege('authenticated', 'public.stats_dashboard_shares', 'UPDATE')
     or not has_table_privilege('authenticated', 'public.stats_dashboard_shares', 'SELECT')
     or not has_table_privilege('authenticated', 'public.stats_dashboard_shares', 'DELETE')
     or has_table_privilege('anon', 'public.stats_dashboard_shares', 'SELECT') then
    raise exception '647 did not land: stats_dashboard_shares grants drifted (SELECT + DELETE for authenticated only)';
  end if;

  if not exists (select 1 from pg_trigger
                  where tgrelid = 'public.stats_dashboard_shares'::regclass
                    and tgname = 'stats_dashboard_shares_account_id_biu'
                    and tgfoid = 'public.sync_account_id_from_dashboard()'::regprocedure
                    and pg_get_triggerdef(oid) like
                        '%BEFORE INSERT OR UPDATE OF dashboard_id ON public.stats_dashboard_shares FOR EACH ROW%') then
    raise exception '647 did not land: the owner-stamping trigger (insert or update of dashboard_id) is missing';
  end if;
  if not exists (select 1 from pg_trigger t
                  where t.tgrelid = 'public.stats_dashboards'::regclass
                    and t.tgname = 'stats_dashboards_name_guard'
                    and t.tgfoid = 'public.guard_stats_dashboard_name()'::regprocedure
                    and pg_get_triggerdef(t.oid) like '%BEFORE UPDATE OF name ON public.stats_dashboards FOR EACH ROW%') then
    raise exception '647 did not land: the name guard (BEFORE UPDATE OF name) is missing';
  end if;
  if position('public.stats_dashboard_shares' in
              (select p.prosrc from pg_proc p where p.oid = 'public.share_party_emails()'::regprocedure)) = 0 then
    raise exception '647 did not land: share_party_emails() does not read stats_dashboard_shares';
  end if;
  if not has_function_privilege('authenticated', 'public.share_party_emails()', 'EXECUTE')
     or has_function_privilege('anon', 'public.share_party_emails()', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.sync_account_id_from_dashboard()', 'EXECUTE')
     or has_function_privilege('anon', 'public.sync_account_id_from_dashboard()', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.guard_stats_dashboard_name()', 'EXECUTE')
     or has_function_privilege('anon', 'public.guard_stats_dashboard_name()', 'EXECUTE') then
    raise exception '647 did not land: function grants drifted';
  end if;
end
$post$;

commit;

select pg_notify('pgrst', 'reload schema');

reset statement_timeout;
reset lock_timeout;
