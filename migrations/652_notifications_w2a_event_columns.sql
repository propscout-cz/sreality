-- 652_notifications_w2a_event_columns.sql -- notifications W2a: the event's own columns, beside the old key.
--
-- WHY (docs/design/notifications-push/PROGRAM.md § 3.2 "Migration A", § 5 W2; audit F1, F13, F14, F17, F21):
--   W2a moves the three producers onto the realtime worker's `notifications` lane (300 s), beside the API's matcher for
--   a 48 h overlap. A watchdog's windows start at the moment it was switched on (`active_since`, ruling R6 (a): a new or
--   re-enabled watchdog alerts only on what appears afterwards), which replaces the per-subscription cursor that skipped
--   late facts and flooded on resume (F1). Every producer row now also states what tells it apart (`event_ref`) and, for
--   a share, who caused it (`actor_account_id`), so W3 can swap the dedupe_key for one identity index over typed columns
--   and mark a merged duplicate (`duplicate_of`) instead of deleting it (F13, F21). The one 'shared' kind splits into
--   the three things a share opens, so the feed stops parsing the key to learn which (F24).
--
-- WHAT CHANGES. Additive only: five columns, one trigger, one CHECK widened, backfills.
--   1. notification_dispatches + event_ref text (the discriminator within source, kind and property), actor_account_id
--      uuid -> accounts ON DELETE SET NULL (the sharer; NULL for a detector), duplicate_of uuid -> notification_dispatches
--      ON DELETE SET NULL (nothing writes it before W3).
--   2. notification_subscriptions + active_since timestamptz NOT NULL DEFAULT now(), stamped by a BEFORE trigger on
--      every INSERT and on every is_active false -> true flip (migration 230's pattern), backfilled to created_at by the
--      run that adds it (0 watchdogs on 2026-10-21); and
--      retired_at timestamptz (nothing writes it before W3, when deleting a watchdog becomes retiring it; the producers
--      already skip a retired one).
--   3. notification_dispatches_change_kind_ck gains shared_collection, shared_dashboard and shared_pipeline; every
--      existing value stays ('shared', 'system_alert' and 'broker_change' leave in W6).
--   4. Backfills, from today's dedupe_key grammar (api/notifications.py, api/shares.py), each guarded so a re-run is a
--      no-op:
--        event_ref  price_drop / price_rise -> trigger_snapshot_id (the key's snapshot segment: equal on every live row,
--                     2026-10-21); inactive / reactivated -> the key's trailing epoch segment; new_source -> the
--                     introducing listing's id (the key's 'l<id>' segment, or its sreality id, signed for a legacy
--                     handle, mapped once through listings.sreality_id); shared dashboard -> the dashboard id; shared
--                     pipeline -> the mode; new, shared collection and system history -> NULL.
--        actor_account_id  every share row -> the shared object's owner (collections / stats_dashboards.account_id, or
--                     the pipeline owner the key names).
--        change_kind 'shared' -> shared_<the key's second segment> (2 rows on 2026-10-21).
--   The account trigger (sync_notification_dispatches_account_id, 641) fires on UPDATE OF subscription_id,
--   collection_id, source_kind only, so the backfill re-stamps no account. Nothing is dropped and no grant changes:
--   the browser keeps UPDATE (seen_at) alone (651), and authenticated's table-wide INSERT/UPDATE on
--   notification_subscriptions (290) covers the two new columns as it covers the rest, the trigger owning active_since.
--
-- NOT HERE. The identity index, VISIBLE and the carrier rewrite are W3 (Migration C); dropping dedupe_key and the dead
-- columns is W6.
--
-- APPLY through apply_migration.yml (--ref feature/notifications-w2a) BEFORE merging W2a: the producers and
-- api/shares.py write the new columns and kinds with no fallback. Safe against the deployed code: nothing reads the new
-- columns yet, the old code writes 'shared' (still allowed) and the old feed parses share_kind from the key. A share
-- granted between this apply and the deploy keeps 'shared'; re-running this file after the deploy relabels it.
-- ROLLBACK: forward only (rule 1). Every statement is idempotent; a lock-timeout retry re-runs the file.

set lock_timeout = '5s';

begin;

-- One column per statement, so the migration_drift check probes each (scripts/migration_objects.py).
alter table public.notification_dispatches add column if not exists event_ref text;
alter table public.notification_dispatches
  add column if not exists actor_account_id uuid references public.accounts(id) on delete set null;
alter table public.notification_dispatches
  add column if not exists duplicate_of uuid references public.notification_dispatches(id) on delete set null;

comment on column public.notification_dispatches.event_ref is
  'What tells this event apart within (source, kind, property) (migration 652): price = the snapshot id; inactive = the '
  'epoch of inactive_at; reactivated = the epoch of the inactive dispatch it answers; new_source = the introducing '
  'listing id; shared_dashboard = the dashboard id; shared_pipeline = the mode; NULL for new and shared_collection.';
comment on column public.notification_dispatches.actor_account_id is
  'Who caused the event (migration 652): the sharer for a share, NULL for a detector.';
comment on column public.notification_dispatches.duplicate_of is
  'The row this one duplicates (migration 652; written from W3, when a merge marks rather than deletes).';

-- Existing watchdogs (0 on 2026-10-21) start from their creation, on the run that adds the column only, so a re-run
-- never undoes a later switch-on stamp. 056's touch trigger restamps updated_at on any row this moves.
do $active_since$
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'notification_subscriptions'
                    and column_name = 'active_since') then
    alter table public.notification_subscriptions add column active_since timestamptz not null default now();
    update public.notification_subscriptions set active_since = created_at;
  end if;
end
$active_since$;
alter table public.notification_subscriptions add column if not exists retired_at timestamptz;

comment on column public.notification_subscriptions.active_since is
  'When this watchdog last started matching (migration 652): stamped on insert and on every is_active false -> true '
  'flip. Its new and price windows start no earlier, so a watchdog alerts only on what appears after it is switched on.';
comment on column public.notification_subscriptions.retired_at is
  'When the watchdog was retired (migration 652; written from W3). A retired watchdog matches nothing.';

create or replace function public.notification_subscriptions_stamp_active_since()
returns trigger language plpgsql set search_path = '' as $$
begin
  if TG_OP = 'INSERT' or (new.is_active and old.is_active is distinct from true) then
    new.active_since := now();
  end if;
  return new;
end;
$$;
revoke all on function public.notification_subscriptions_stamp_active_since() from public, anon, authenticated;

drop trigger if exists notification_subscriptions_active_since on public.notification_subscriptions;
create trigger notification_subscriptions_active_since
  before insert or update of is_active on public.notification_subscriptions
  for each row execute function public.notification_subscriptions_stamp_active_since();

alter table public.notification_dispatches drop constraint if exists notification_dispatches_change_kind_ck;
alter table public.notification_dispatches add constraint notification_dispatches_change_kind_ck check (
  change_kind in ('new', 'price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source',
                  'broker_change', 'system_alert', 'shared',
                  'shared_collection', 'shared_dashboard', 'shared_pipeline'));

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
       end
 where d.event_ref is null
   and (d.change_kind in ('price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source')
        or (d.source_kind = 'share' and split_part(d.dedupe_key, ':', 2) in ('dashboard', 'pipeline')));

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

commit;

do $post$
declare
  v_missing text[];
  v_check text;
begin
  select array_agg(want.t || '.' || want.c order by want.t, want.c) into v_missing
    from (values ('notification_dispatches', 'event_ref', 'text'),
                 ('notification_dispatches', 'actor_account_id', 'uuid'),
                 ('notification_dispatches', 'duplicate_of', 'uuid'),
                 ('notification_subscriptions', 'active_since', 'timestamp with time zone'),
                 ('notification_subscriptions', 'retired_at', 'timestamp with time zone')) as want(t, c, typ)
   where not exists (select 1 from information_schema.columns ic
                      where ic.table_schema = 'public' and ic.table_name = want.t
                        and ic.column_name = want.c and ic.data_type = want.typ);
  if v_missing is not null then
    raise exception '652 did not land: missing or mistyped columns %', v_missing;
  end if;
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'notification_subscriptions'
                    and column_name = 'active_since' and is_nullable = 'NO') then
    raise exception '652 did not land: notification_subscriptions.active_since must be NOT NULL';
  end if;
  if not exists (select 1 from pg_trigger t
                  where t.tgrelid = 'public.notification_subscriptions'::regclass and not t.tgisinternal
                    and t.tgname = 'notification_subscriptions_active_since'
                    and t.tgfoid = 'public.notification_subscriptions_stamp_active_since()'::regprocedure) then
    raise exception '652 did not land: the active_since trigger is missing';
  end if;
  select pg_get_constraintdef(oid) into v_check from pg_constraint
   where conrelid = 'public.notification_dispatches'::regclass and conname = 'notification_dispatches_change_kind_ck';
  if v_check is null or v_check not like '%''shared_collection''%' or v_check not like '%''shared_dashboard''%'
     or v_check not like '%''shared_pipeline''%' or v_check not like '%''shared''%' or v_check not like '%''new''%' then
    raise exception '652 did not land: notification_dispatches_change_kind_ck is %', v_check;
  end if;
  if exists (select 1 from public.notification_dispatches where source_kind = 'share' and change_kind = 'shared') then
    raise exception '652 did not land: a share row still reads change_kind ''shared''';
  end if;
  if exists (select 1 from public.notification_dispatches
              where event_ref is null
                and (change_kind in ('inactive', 'reactivated', 'new_source', 'shared_dashboard', 'shared_pipeline')
                     or (change_kind in ('price_drop', 'price_rise') and trigger_snapshot_id is not null))) then
    raise exception '652 did not land: a row whose key names its event has no event_ref';
  end if;
end
$post$;

reset lock_timeout;
