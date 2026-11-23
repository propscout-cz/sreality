-- 692_broker_book.sql -- the broker book stored at the grain every reader reads: (property, canonical broker).
--
-- WHY (docs/design/broker-board.md): 691 stored it per broker identity (property_broker_spells) and folded identities
-- to brokers at read time in the VIEW broker_book, which cost the board 5.3 s cold for Praha byt/prodej and 5.95 s warm
-- nationally (production, 2026-10-29). So the fold moves to WRITE time, into rule 20's property pass: the view
-- broker_book_projection is THE definition of a row, and broker_book stores it, written only by the property
-- maintenance pass (scripts/recompute_property_stats.py). The price: every writer of broker_identities.broker_id (an
-- operator merge or unmerge, the resolver's auto-merge and singleton attach) dirties the properties of the identity's
-- ads in its own transaction (toolkit/broker_sources.py dirty_repointed, rule 20).
--
-- 691's view broker_book is dropped (nothing deployed reads it). property_broker_spells and its projection STAY: the
-- deployed W1 pass writes them until this PR deploys; they go in the destructive batch, with the operator's OK and a
-- backup first. measure_exit_within (691) is unchanged.
--
-- APPLY through apply_migration.yml (--ref feature/broker-board) right BEFORE merging: the pass the same PR ships
-- writes broker_book on every slice, so a merge before the apply fails every maintenance pass and every merge; the
-- deployed code neither reads nor writes either. THE GAP: the deployed W1 pass never writes broker_book, so a change
-- between the apply and the rollout leaves a row missing or stale, and a re-apply does not heal it (the backfill only
-- inserts the pairs the book lacks). POST-DEPLOY, right after the rollout: dispatch recompute_property_stats.yml until a
-- sweep cycle BEGUN after the rollout completes (app_settings.property_sweep_cursor gone and the property_sweep artifact
-- stamped after it; one or two runs, a run resumes an earlier cycle at its cursor); the third query then reads ~0.
-- ROLLBACK: forward only (rule 1). Idempotent: the view is dropped only while broker_book is a view, the projection
-- replaces itself, and the backfill inserts only the rows it lacks.
-- Verify (the second right after the backfill, the third after that post-deploy cycle):
--   select count(*) from broker_book;                                                                -- > 0
--   select count(*) from broker_book_projection v left join broker_book b using (property_id, broker_id)
--     where b.property_id is null;                                                                    -- ~0
--   select count(*) from broker_book_projection v full join broker_book b using (property_id, broker_id)
--     where v.property_id is null or b.property_id is null
--        or (v.is_active, v.all_sources, v.active_sources, v.started_at, v.ended_at, v.tom_observed)
--           is distinct from (b.is_active, b.all_sources, b.active_sources, b.started_at, b.ended_at, b.tom_observed);
--                                                                                                     -- ~0

set lock_timeout = '5s';
set statement_timeout = '900s';

begin;

-- ---------------------------------------------------------------------------
-- 1. 691's read-time fold goes; its name becomes the stored book's.
-- ---------------------------------------------------------------------------
do $drop$
begin
  if exists (select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
             where n.nspname = 'public' and c.relname = 'broker_book' and c.relkind = 'v') then
    drop view public.broker_book;
  end if;
end
$drop$;

-- ---------------------------------------------------------------------------
-- 2. THE book row. A qual on property_id (the maintenance pass's id list or id range) is a qual on a grouping column,
--    so the planner applies it below the aggregate, on listings_property_id_idx.
-- ---------------------------------------------------------------------------
create or replace view public.broker_book_projection as
  select l.property_id,
         bi.broker_id,
         bool_or(l.is_active) as is_active,
         array_agg(distinct l.source order by l.source) as all_sources,
         coalesce(array_agg(distinct l.source order by l.source) filter (where l.is_active), '{}') as active_sources,
         min(l.first_seen_at) as started_at,
         case when bool_or(l.is_active) then null else max(l.last_seen_at) end as ended_at,
         (array_agg(public.measure_tom_observed(l.source, l.category_main, l.category_type, l.first_seen_at)
                    order by (l.category_main is null or l.category_type is null), l.first_seen_at, l.id))[1]
           as tom_observed
  from public.listings l
  join public.broker_identities bi on bi.id = l.broker_identity_id
  join public.brokers b on b.id = bi.broker_id and b.status = 'active'
  where l.property_id is not null
  group by l.property_id, bi.broker_id;

comment on view public.broker_book_projection is
  'THE definition of a broker book row (migration 692): one row per (property, active canonical broker) over the ads '
  'of the broker''s identities on the property. is_active / all_sources / active_sources are portal_status_matches'' '
  'inputs at the broker''s grain (MS19); started_at = min(first_seen_at), the start measure_tom_days uses; ended_at = '
  'NULL while any of those ads is active, else max(last_seen_at); tom_observed = measure_tom_observed of the broker''s '
  'first ad in browse_projection''s order. Read only by the property maintenance pass, which stores it in broker_book.';

revoke all on public.broker_book_projection from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. THE book.
-- ---------------------------------------------------------------------------
create table if not exists public.broker_book (
  property_id     bigint      not null references public.properties(id),
  broker_id       bigint      not null references public.brokers(id),
  is_active       boolean     not null,
  all_sources     text[]      not null,
  active_sources  text[]      not null,
  started_at      timestamptz not null,
  ended_at        timestamptz,
  tom_observed    boolean     not null,
  primary key (property_id, broker_id)
);

create index if not exists broker_book_broker_idx on public.broker_book (broker_id);

comment on table public.broker_book is
  'The broker book (migration 692): broker_book_projection, stored, one row per (property, canonical broker). '
  'Derived, never history: written only by the property maintenance pass (properties_changed and the daily sweep, '
  'scripts/recompute_property_stats.py), which upserts the view''s rows for the properties it recomputes and deletes '
  'the ones the view no longer holds; a broker re-point (merge, unmerge, singleton attach) dirties the properties it '
  'moves. Read by the broker board, Browse''s broker scope and Outreach. Service role only (A6, migration 299).';
comment on column public.broker_book.tom_observed is
  'measure_tom_observed of the broker''s first ad in browse_projection''s order: false when that ad was first written '
  'on a sweep day: it was already on the market, so started_at is later than the row''s real start (left-censored) '
  'and measure_exit_within does not follow it.';

alter table public.broker_book enable row level security;
revoke all on public.broker_book from public, anon, authenticated;

commit;

-- ---------------------------------------------------------------------------
-- 4. Backfill, from the one spelling, in its own statement AFTER the DDL commits: inside that transaction it would
--    hold the table's REFERENCES locks (SHARE ROW EXCLUSIVE on properties and brokers) for its whole run, stalling
--    every rollup, birth, merge, detach and settle stamp and the broker lane's rollups. Alone, its foreign-key checks
--    take per-row KEY SHARE locks, which no non-key UPDATE of those tables waits on. Idempotent.
-- ---------------------------------------------------------------------------
insert into public.broker_book
  (property_id, broker_id, is_active, all_sources, active_sources, started_at, ended_at, tom_observed)
select property_id, broker_id, is_active, all_sources, active_sources, started_at, ended_at, tom_observed
  from public.broker_book_projection
on conflict do nothing;

do $post$
declare
  v_role text;
begin
  if (select relkind from pg_class where oid = 'public.broker_book'::regclass) <> 'r' then
    raise exception '692 did not land: broker_book is not a table';
  end if;
  if not (select relrowsecurity from pg_class where oid = 'public.broker_book'::regclass) then
    raise exception '692 did not land: broker_book has row level security off';
  end if;
  if exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'broker_book') then
    raise exception '692 did not land: broker_book carries a policy (it is service-role only)';
  end if;
  foreach v_role in array array['anon', 'authenticated'] loop
    if has_table_privilege(v_role, 'public.broker_book', 'select')
       or has_table_privilege(v_role, 'public.broker_book_projection', 'select') then
      raise exception '692 did not land: % can read the broker book', v_role;
    end if;
  end loop;
end
$post$;

reset statement_timeout;
reset lock_timeout;
