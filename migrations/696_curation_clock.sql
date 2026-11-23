-- 696: curation sync, the curation clock (docs/design/curation-sync.md § 3.2, ADR-0008; spec #2197, W3 #2201).
--
-- One counter per (account, family). The database advances it once per committed transaction that changes that
-- account's curation in that family. Every open SPA window reads its counters and re-reads what moved (W4).
--
-- WHY TRIGGERS: the API (as authenticated), merge/split carriers (service role, any account), signup (definer, 612)
--   and FK cascades (202, 610) all write curation; only the table sees every writer.
-- WHY DEFERRED: the bump runs at COMMIT, so a rolled-back write moves nothing and the counter row is locked only
--   while the commit completes. The API commits before it answers (W0, TenantConn), so a commit-time failure is a 5xx.
-- WHY ONCE PER TRANSACTION: windows compare counters for inequality, so one bump per (tx, account, family) is
--   enough. The transaction-local GUC curation_clock.bumped remembers which (family, account) pairs this transaction
--   already bumped; a bulk statement stays O(rows), never O(rows^2) on one hot counter row.
-- WHY DEFINER: the tenant pool writes as authenticated, which may only SELECT the clock.
-- NO PAYLOAD: a counter says "family F of account A changed", never which row or how.
--
-- WHO FOLLOWS WHOM (rulings 2 and 18/22): the caller's own accounts follow every family; a READ share's recipient
--   follows the owner's board; an EDIT share's recipient follows the board and the five curation families; nobody
--   follows another account's shares. Stated once, in curation_clock_audience(), and pinned to 641's policy split by
--   census (i) (tests/test_curation_sync_census.py). The audience is "follow, never act": it reads
--   current_account_ids(), never acting_account_ids(), so a clock read carries no x-act-as and a stray header fails
--   closed to the caller's own rows (pipeline_shares_party hides while acting).
--
-- NEVER write curation_clock outside bump_curation_clock(), never reset a counter, and never disable a
--   *_curation_clock trigger without a `-- ci-allow-clock-disable: <reason>` annotation
--   (tests/test_curation_sync_census.py rails e and h). The kill switches are forward migrations
--   (.claude/skills/database/references/tenancy.md § Curation clock).
--
-- APPLY: before the PR merges, and only once W0 (#2198, commit before the response) is live, through
--   apply_migration.yml (psql -f, statement autocommit). Additive: a table, a view, two functions, one policy and
--   twelve constraint triggers on the curation tables. There is no begin/commit: every statement is its own
--   transaction and idempotent, so a partial apply is resumed by re-running this same file. Each trigger statement
--   locks its one table (CREATE CONSTRAINT TRIGGER: SHARE ROW EXCLUSIVE; DROP TRIGGER IF EXISTS: nothing on a first
--   apply, ACCESS EXCLUSIVE on a re-run) and releases it at its own commit: no lock outlives its statement, and
--   lock_timeout 5 s bounds each wait, which the workflow retries. WHY: the first apply (2026-10-31) ran
--   as one transaction, held the first four trigger tables until COMMIT and deadlocked on the fifth
--   (collection_properties) with a writer that held it and wanted one of the four; it rolled back whole.
-- RE-RUN: CREATE OR REPLACE is refused for a constraint trigger, so each is dropped and created; a write that
--   commits in the instant between the two moves no counter. IF NOT EXISTS keeps an existing curation_clock as it
--   is: a column or CHECK changed in this file would not reach a database that holds the table, and $post$ checks
--   the grants, policy, view, triggers and seed, not the columns (change the table in a forward migration).
-- ROLLBACK: forward only (rule 1); the writer kill switch disables the twelve triggers, the reader kill switch
--   revokes the reads.

set lock_timeout = '5s';

create table if not exists public.curation_clock (
  account_id uuid        not null references public.accounts(id) on delete cascade,
  family     text        not null,
  counter    bigint      not null default 1,
  changed_at timestamptz not null default clock_timestamp(),
  primary key (account_id, family),
  constraint curation_clock_family_check check (family in
    ('board', 'dismissals', 'collections', 'tags', 'notes', 'links', 'shares'))
);
alter table public.curation_clock enable row level security;
revoke all on public.curation_clock from public, anon, authenticated, service_role;
grant select on public.curation_clock to authenticated;

-- WHO follows an account's curation, stated once. The caller's own accounts follow every family. The owners whose
-- pipeline they share follow board (read share) or every curation family (edit share), per 641 ruling 2.
-- current_account_ids(), never acting_account_ids(): a clock read never acts. The 641 stray-acting rail
-- (tests/test_migration_rls_grants.py) reads POLICY text only; this body is guarded by
-- tests/test_curation_clock_live.py and census (i). ROWS 4: the caller's accounts plus the owners sharing with
-- them, a handful; the default estimate (1000) would steer the clock read off curation_clock_pkey.
create or replace function public.curation_clock_audience()
returns table (account_id uuid, mine boolean, edit boolean)
language sql stable rows 4 set search_path = '' as $$
  select a.id, true, true from public.current_account_ids() as a(id)
  union all
  select s.account_id, false, bool_or(s.mode = 'edit')
    from public.pipeline_shares s
   where s.recipient_account_id in (select public.current_account_ids())
     and s.account_id not in (select public.current_account_ids())
   group by s.account_id
$$;
revoke all on function public.curation_clock_audience() from public, anon, authenticated, service_role;
grant execute on function public.curation_clock_audience() to authenticated;

drop policy if exists curation_clock_audience_read on public.curation_clock;
create policy curation_clock_audience_read on public.curation_clock
  for select to authenticated
  using (
       account_id in (select a.account_id from public.curation_clock_audience() a where a.mine)
    or (family = 'board'
        and account_id in (select a.account_id from public.curation_clock_audience() a))
    or (family in ('dismissals', 'collections', 'tags', 'notes', 'links')
        and account_id in (select a.account_id from public.curation_clock_audience() a where a.edit)));

-- The clock read's one relation. Invoker, so the policy above stays the gate (pinned in _TENANT_VIEWS).
-- `mine` maps a row to own vs acting keys. `age_ms` serves a window's first answer (§ 3.5 Decision 14).
create or replace view public.curation_clock_public with (security_invoker = true) as
  select c.account_id, c.family, c.counter, a.mine,
         greatest(0, extract(epoch from statement_timestamp() - c.changed_at) * 1000)::bigint as age_ms
    from public.curation_clock_audience() a
    join public.curation_clock c on c.account_id = a.account_id;
revoke all on public.curation_clock_public from public, anon, authenticated, service_role;
grant select on public.curation_clock_public to authenticated;

-- THE one writer of the clock. tg_argv[0] = the family. A row names its account in account_id and, on the two share
-- tables, its recipient in recipient_account_id: both parties' `shares` move.
create or replace function public.bump_curation_clock() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  v_new  jsonb := case when tg_op <> 'DELETE' then to_jsonb(new) end;
  v_old  jsonb := case when tg_op <> 'INSERT' then to_jsonb(old) end;
  v_done text  := coalesce(current_setting('curation_clock.bumped', true), '');
  v_ids  uuid[];
begin
  if v_new = v_old then
    return null;                                   -- an UPDATE that changed nothing
  end if;
  -- The join on accounts skips an account whose deletion cascades this row; ORDER BY gives one lock order.
  select array_agg(a.id order by a.id) into v_ids
    from public.accounts a
   where a.id in ((v_new ->> 'account_id')::uuid, (v_old ->> 'account_id')::uuid,
                  (v_new ->> 'recipient_account_id')::uuid, (v_old ->> 'recipient_account_id')::uuid)
     and strpos(v_done, tg_argv[0] || ':' || a.id::text || ';') = 0;
  if v_ids is null then
    return null;                                   -- already bumped in this transaction, or no account left
  end if;
  insert into public.curation_clock as c (account_id, family)
  select u.id, tg_argv[0] from unnest(v_ids) with ordinality as u(id, n) order by u.n
  on conflict (account_id, family) do update
     set counter = c.counter + 1, changed_at = greatest(c.changed_at, clock_timestamp());
  perform set_config('curation_clock.bumped',
    v_done || (select string_agg(tg_argv[0] || ':' || u.id::text || ';', '') from unnest(v_ids) as u(id)), true);
  return null;
end $$;
revoke all on function public.bump_curation_clock() from public, anon, authenticated, service_role;

-- One static drop + create per clocked table (12; tests/_tenant_registry.py CLOCKED_TABLES, frontend SYNC_TABLES).
drop trigger if exists property_pipeline_curation_clock on public.property_pipeline;
create constraint trigger property_pipeline_curation_clock
  after insert or update or delete on public.property_pipeline
  deferrable initially deferred for each row execute function public.bump_curation_clock('board');
drop trigger if exists pipeline_stages_curation_clock on public.pipeline_stages;
create constraint trigger pipeline_stages_curation_clock
  after insert or update or delete on public.pipeline_stages
  deferrable initially deferred for each row execute function public.bump_curation_clock('board');
drop trigger if exists property_dismissals_curation_clock on public.property_dismissals;
create constraint trigger property_dismissals_curation_clock
  after insert or update or delete on public.property_dismissals
  deferrable initially deferred for each row execute function public.bump_curation_clock('dismissals');
drop trigger if exists collections_curation_clock on public.collections;
create constraint trigger collections_curation_clock
  after insert or update or delete on public.collections
  deferrable initially deferred for each row execute function public.bump_curation_clock('collections');
drop trigger if exists collection_properties_curation_clock on public.collection_properties;
create constraint trigger collection_properties_curation_clock
  after insert or update or delete on public.collection_properties
  deferrable initially deferred for each row execute function public.bump_curation_clock('collections');
drop trigger if exists tags_curation_clock on public.tags;
create constraint trigger tags_curation_clock
  after insert or update or delete on public.tags
  deferrable initially deferred for each row execute function public.bump_curation_clock('tags');
drop trigger if exists property_tags_curation_clock on public.property_tags;
create constraint trigger property_tags_curation_clock
  after insert or update or delete on public.property_tags
  deferrable initially deferred for each row execute function public.bump_curation_clock('tags');
drop trigger if exists property_notes_curation_clock on public.property_notes;
create constraint trigger property_notes_curation_clock
  after insert or update or delete on public.property_notes
  deferrable initially deferred for each row execute function public.bump_curation_clock('notes');
drop trigger if exists property_attachments_curation_clock on public.property_attachments;
create constraint trigger property_attachments_curation_clock
  after insert or update or delete on public.property_attachments
  deferrable initially deferred for each row execute function public.bump_curation_clock('notes');
drop trigger if exists property_artifact_links_curation_clock on public.property_artifact_links;
create constraint trigger property_artifact_links_curation_clock
  after insert or update or delete on public.property_artifact_links
  deferrable initially deferred for each row execute function public.bump_curation_clock('links');
drop trigger if exists pipeline_shares_curation_clock on public.pipeline_shares;
create constraint trigger pipeline_shares_curation_clock
  after insert or update or delete on public.pipeline_shares
  deferrable initially deferred for each row execute function public.bump_curation_clock('shares');
drop trigger if exists collection_shares_curation_clock on public.collection_shares;
create constraint trigger collection_shares_curation_clock
  after insert or update or delete on public.collection_shares
  deferrable initially deferred for each row execute function public.bump_curation_clock('shares');

-- Seed: every account starts with every family at 1, so the health invariant holds from this commit on and each
-- window gets a stable baseline. Later accounts gain rows on their first bump (signup bumps board + collections).
insert into public.curation_clock (account_id, family)
select a.id, f.family
  from public.accounts a
 cross join unnest(array['board', 'dismissals', 'collections', 'tags', 'notes', 'links', 'shares']) as f(family)
on conflict do nothing;

comment on table public.curation_clock is
  'Curation sync (696): one counter per (account, family), advanced only by bump_curation_clock() at commit, '
  'once per transaction; read through curation_clock_public. Not history.';
comment on view public.curation_clock_public is
  'Curation sync (696): the clock read. Invoker: the caller''s own counters (mine) and, per pipeline share, the '
  'owner''s board (read) or board and the five curation families (edit); never another account''s shares.';
comment on function public.curation_clock_audience() is
  'Curation sync (696): who the caller follows, stated once ("follow, never act"): own accounts (mine, edit) and '
  'every owner sharing a pipeline with them (edit = an edit share). Reads current_account_ids(), never acting.';
comment on function public.bump_curation_clock() is
  'Curation sync (696): the one writer of curation_clock, on twelve deferred constraint row triggers; bumps '
  'each (account, family) once per transaction through the transaction-local GUC curation_clock.bumped.';

do $post$
begin
  if (select count(*) from pg_trigger t
       where t.tgfoid = 'public.bump_curation_clock()'::regprocedure and not t.tgisinternal
         and t.tgconstraint <> 0 and t.tgdeferrable and t.tginitdeferred and t.tgenabled = 'O') <> 12 then
    raise exception '696: expected 12 deferred, enabled curation-clock triggers'; end if;
  if not (select prosecdef from pg_proc where oid = 'public.bump_curation_clock()'::regprocedure)
     or (select prosecdef from pg_proc where oid = 'public.curation_clock_audience()'::regprocedure)
     or has_function_privilege('anon', 'public.bump_curation_clock()', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.bump_curation_clock()', 'EXECUTE')
     or has_function_privilege('anon', 'public.curation_clock_audience()', 'EXECUTE')
     or not has_function_privilege('authenticated', 'public.curation_clock_audience()', 'EXECUTE')
     or has_table_privilege('anon', 'public.curation_clock', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('authenticated', 'public.curation_clock', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('service_role', 'public.curation_clock', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('anon', 'public.curation_clock_public', 'SELECT')
     or not has_table_privilege('authenticated', 'public.curation_clock_public', 'SELECT') then
    raise exception '696: clock grants drifted'; end if;
  if not exists (select 1 from pg_class where oid = 'public.curation_clock_public'::regclass
                  and reloptions @> array['security_invoker=true']) then
    raise exception '696: curation_clock_public must be security_invoker'; end if;
  if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'curation_clock'
                  and policyname = 'curation_clock_audience_read' and cmd = 'SELECT') then
    raise exception '696: clock policy missing'; end if;
  if (select count(*) from public.curation_clock) < 7 * (select count(*) from public.accounts) then
    raise exception '696: clock seed incomplete'; end if;
end $post$;
