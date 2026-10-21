-- 653_notifications_w3_event_identity.sql -- notifications W3 ("Migration C"): one event identity.
--
-- WHY (docs/design/notifications-push/PROGRAM.md § 1 rule 2, § 3.2, § 5 W3; audit F6, F13, F21, F24):
--   A dispatch had two identities: the text dedupe_key, which embeds a property id a merge changes, and the merge
--   carrier's coarser "twin" key, which collapsed distinct events and DELETED one. From W3 one tuple decides "same
--   event?" for the producers' ON CONFLICT and for the merge alike: EVENT_IDENTITY in api/notifications.py, the eight
--   columns of the unique index below, NULLS NOT DISTINCT, over the rows that are not themselves a duplicate. A merge
--   re-points a retired property's alerts and marks the ones the survivor already holds (`duplicate_of`); it deletes
--   nothing. The producers stop writing dedupe_key, so it loses NOT NULL; W6 drops it.
--
-- WHAT CHANGES. Non-destructive: one transaction of fills, marks and two plain indexes, then the identity index.
--   0. The event columns, filled since 652 ("fill event_ref since A"): rows the pre-W2a matcher wrote between 652's
--      apply and W2a's deploy carry no event_ref, actor or share kind ('shared'), and the identity would tell their
--      repeat apart from them. 652's three backfills run again, verbatim and guarded, so they touch only such rows,
--      whether or not 652 was re-run after the deploy as its header asks; the relabel leaves no 'shared' row. Its
--      system arm keys system history on its old key, opaque (program § 3.2): with a NULL event_ref the 904 system
--      rows would be ONE identity (dry run 2026-10-21: 903 would be marked repeats; keyed, 0).
--   1. (a) Exact repeats are marked: among the rows not yet marked, every row that shares the identity tuple with an
--      earlier one (dispatched_at, id) gets duplicate_of = the earliest. 0 expected (dry run 2026-10-21, 41 tenant
--      rows): the old carrier deleted its twins and dedupe_key kept the rest apart.
--   2. (b) dedupe_key DROP NOT NULL. Its UNIQUE stays until W6 (a NULL never conflicts).
--   3. (c) notification_dispatches_account_feed_idx (account_id, dispatched_at desc, id desc), the feed, the unread
--      count and W4's fan-out; notification_dispatches_duplicate_of_idx, partial, for the self-FK's SET NULL and the
--      double-new gauge. The indexes it replaces leave in W6, after EXPLAIN.
--   4. (d) LAST, outside the transaction: notification_dispatches_identity_uq, CREATE UNIQUE INDEX CONCURRENTLY
--      (no write waits on it), then $post$: the index is valid and is exactly this definition.
--
-- NOT HERE. VISIBLE, the producers' conflict target, retire-on-delete and the one-UPDATE carrier are W3's code. The
-- old dedupe_key UNIQUE, the dead columns and the replaced indexes are W6's drops.
--
-- APPLY through apply_migration.yml (--ref feature/notifications-w3) BEFORE merging W3 (its producers name this
-- index in ON CONFLICT and write no dedupe_key) and only AFTER W2a's code is deployed (the program: after W2b).
-- Never through the MCP: CREATE INDEX CONCURRENTLY cannot run in a transaction block, and every MCP path wraps its
-- payload in one. Safe against the deployed W2a code: its producers write event_ref and end in a target-less
-- ON CONFLICT DO NOTHING, which absorbs this index as it does the old key, and its carrier deletes every identity
-- twin before its move (its twin key is coarser), so neither can trip the index. NOT safe against pre-W2a code: its
-- producers write no event_ref and conflict on (dedupe_key) only, so a property's second price step (or 'inactive')
-- would share the first's NULL event_ref and fail its whole statement here.
-- RECOVERY. A repeat written between (a) and (d) fails the build ("could not create unique index ... is duplicated")
-- and leaves an INVALID index. Re-run the file: it moves the INVALID leftover aside and drops it, re-marks the
-- repeats (a) and builds again. By hand: DROP INDEX CONCURRENTLY public.notification_dispatches_identity_uq; then
-- re-run (a); then re-run the file.
-- ROLLBACK: forward only (rule 1). Every statement is idempotent; a lock-timeout retry re-runs the file.

set lock_timeout = '5s';
set statement_timeout = '120s';

begin;

-- 0. The event columns since 652, verbatim from 652 (each guarded on the column it fills or relabels).
update public.notification_dispatches d
   set event_ref = case
         when d.change_kind in ('price_drop', 'price_rise') then d.trigger_snapshot_id::text
         when d.change_kind in ('inactive', 'reactivated') then split_part(d.dedupe_key, ':', 5)
         when d.change_kind = 'new_source' then case
           when split_part(d.dedupe_key, ':', 4) ~ '^l[0-9]+$' then substr(split_part(d.dedupe_key, ':', 4), 2)
           when split_part(d.dedupe_key, ':', 4) ~ '^-?[0-9]+$' then
             (select l.id::text from public.listings l
               where l.sreality_id = split_part(d.dedupe_key, ':', 4)::bigint)
         end
         when d.source_kind = 'share' and split_part(d.dedupe_key, ':', 2) = 'dashboard'
           then split_part(d.dedupe_key, ':', 3)
         when d.source_kind = 'share' and split_part(d.dedupe_key, ':', 2) = 'pipeline'
           then split_part(d.dedupe_key, ':', 5)
         when d.source_kind = 'system_health' then d.dedupe_key
       end
 where d.event_ref is null
   and (d.change_kind in ('price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source')
        or (d.source_kind = 'share' and split_part(d.dedupe_key, ':', 2) in ('dashboard', 'pipeline'))
        or d.source_kind = 'system_health');

update public.notification_dispatches d
   set actor_account_id = case split_part(d.dedupe_key, ':', 2)
         when 'collection' then (select c.account_id from public.collections c where c.id = d.collection_id)
         when 'dashboard' then (select s.account_id from public.stats_dashboards s
                                 where s.id::text = split_part(d.dedupe_key, ':', 3))
         when 'pipeline' then (select a.id from public.accounts a
                                where a.id::text = split_part(d.dedupe_key, ':', 3))
       end
 where d.source_kind = 'share' and d.actor_account_id is null;

update public.notification_dispatches d
   set change_kind = 'shared_' || split_part(d.dedupe_key, ':', 2)
 where d.source_kind = 'share' and d.change_kind = 'shared'
   and split_part(d.dedupe_key, ':', 2) in ('collection', 'dashboard', 'pipeline');

-- 1. (a) Exact repeats: the earliest unmarked row of each identity stands, every later one is marked.
do $repeats$
declare
  v_marked bigint;
begin
  with ranked as (
    select id,
           first_value(id) over (
             partition by account_id, source_kind, subscription_id, collection_id, change_kind, property_id,
                          event_ref, actor_account_id
             order by dispatched_at, id) as first_id
      from public.notification_dispatches
     where duplicate_of is null)
  update public.notification_dispatches d
     set duplicate_of = r.first_id
    from ranked r
   where r.id = d.id and r.first_id <> d.id and d.duplicate_of is null;
  get diagnostics v_marked = row_count;
  raise notice '653: % exact repeats marked duplicate_of', v_marked;
end
$repeats$;

-- 2. (b) Nothing writes the key from W3.
alter table public.notification_dispatches alter column dedupe_key drop not null;

comment on column public.notification_dispatches.dedupe_key is
  'The pre-W3 idempotency key (migration 206): history only. Nothing writes it since migration 653, whose '
  'notification_dispatches_identity_uq decides identity; W6 drops it.';

-- 3. (c) The feed's index, and the duplicates' (both tiny: 945 rows on 2026-10-21).
create index if not exists notification_dispatches_account_feed_idx
  on public.notification_dispatches (account_id, dispatched_at desc, id desc);
create index if not exists notification_dispatches_duplicate_of_idx
  on public.notification_dispatches (duplicate_of) where duplicate_of is not null;

commit;

-- 4a. An INVALID identity index can only be a failed build of an earlier run, and IF NOT EXISTS would keep it. A DO
--     block is a transaction, where DROP INDEX CONCURRENTLY cannot run, so the block only RENAMES it aside (635's
--     idiom) and the drop below, at top level, is CONCURRENTLY.
do $invalid$
begin
  if exists (select 1 from pg_index i
              where i.indexrelid = to_regclass('public.notification_dispatches_identity_uq')
                and not i.indisvalid) then
    alter index public.notification_dispatches_identity_uq rename to notification_dispatches_identity_uq_invalid;
    raise notice '653: an INVALID notification_dispatches_identity_uq from a failed build moved aside';
  end if;
end
$invalid$;

-- 4. (d) The identity. A concurrent build waits for every older transaction in the database (a list rebuild
--     included), so no lock limit and a 1900 s budget (605, 635); it scans 945 rows.
set lock_timeout = 0;
set statement_timeout = '1900s';

drop index concurrently if exists public.notification_dispatches_identity_uq_invalid;

create unique index concurrently if not exists notification_dispatches_identity_uq
  on public.notification_dispatches
  (account_id, source_kind, subscription_id, collection_id, change_kind, property_id, event_ref, actor_account_id)
  nulls not distinct where duplicate_of is null;

set lock_timeout = '5s';
set statement_timeout = '120s';

comment on index public.notification_dispatches_identity_uq is
  'ONE event identity (migration 653): the producers'' ON CONFLICT target and the merge carrier''s match, '
  'EVENT_IDENTITY in api/notifications.py. A row with duplicate_of set is a marked repeat, outside it.';

do $post$
declare
  v_cols text[];
begin
  select array_agg(a.attname::text order by k.ord) into v_cols
    from pg_index i
    cross join lateral unnest(i.indkey::int2[]) with ordinality as k(attnum, ord)
    join pg_attribute a on a.attrelid = i.indrelid and a.attnum = k.attnum
   where i.indexrelid = to_regclass('public.notification_dispatches_identity_uq');
  if not exists (select 1 from pg_index i
                  where i.indexrelid = to_regclass('public.notification_dispatches_identity_uq')
                    and i.indrelid = 'public.notification_dispatches'::regclass
                    and i.indisvalid and i.indisready and i.indisunique and i.indnullsnotdistinct
                    and pg_get_expr(i.indpred, i.indrelid) = '(duplicate_of IS NULL)')
     or v_cols is distinct from array['account_id', 'source_kind', 'subscription_id', 'collection_id', 'change_kind',
                                      'property_id', 'event_ref', 'actor_account_id'] then
    raise exception '653 did not land: notification_dispatches_identity_uq is missing, INVALID or not the identity '
      '(columns %). Recover: DROP INDEX CONCURRENTLY public.notification_dispatches_identity_uq; re-run (a), the '
      'repeat marking; re-run the file (a re-run does all three)', v_cols;
  end if;
  if to_regclass('public.notification_dispatches_account_feed_idx') is null
     or to_regclass('public.notification_dispatches_duplicate_of_idx') is null then
    raise exception '653 did not land: the feed or the duplicate_of index is missing';
  end if;
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'notification_dispatches'
                and column_name = 'dedupe_key' and is_nullable = 'NO') then
    raise exception '653 did not land: notification_dispatches.dedupe_key is still NOT NULL';
  end if;
  if exists (select 1 from public.notification_dispatches
              where event_ref is null
                and (change_kind in ('inactive', 'reactivated', 'new_source', 'shared_dashboard', 'shared_pipeline')
                     or source_kind = 'system_health'
                     or (change_kind in ('price_drop', 'price_rise') and trigger_snapshot_id is not null))) then
    raise exception '653 did not land: a row whose event the identity must tell apart has no event_ref';
  end if;
end
$post$;

reset statement_timeout;
reset lock_timeout;
