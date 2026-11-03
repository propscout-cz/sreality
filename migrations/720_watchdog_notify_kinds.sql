-- 720_watchdog_notify_kinds.sql -- a watchdog, and a watched collection, choose which property events they tell.
--
-- WHY (operator request 2026-11-03: "make it possible to set what the watchdog sends notifications for"): a watchdog
--   told 'new' and 'price_drop', always both, and nothing else, while a monitored collection tells five kinds. The
--   producers now detect all six property events for a watchdog from the detectors the collection monitor already
--   runs (api/notifications.py, one detector per kind for both producers), and each watchdog keeps the set it tells.
--   A watched collection chooses too (operator ruling 2026-11-03, "yes, build this now"), from the five it tells
--   today: a property it holds is never a new match to it.
--
-- WHAT CHANGES. Additive only: two columns with their CHECKs, two trigger functions restated.
--   1. notification_subscriptions + notify_kinds text[] NOT NULL DEFAULT {new,price_drop}: the property events this
--      watchdog tells, of api/notifications.WATCHDOG_KINDS. The default is what every watchdog told before this
--      migration, so none tells anything new or less until its owner changes it. The CHECK: at least one kind, only
--      those six, and 'reactivated' (Back on the market) only beside 'inactive' (Left the market), because the one
--      answers the other told by the same watchdog (api/notifications._reactivated_sql reads that dispatch). The API
--      stores the kinds once each, in WATCHDOG_KINDS order, so re-saving the same set is no change.
--   2. notification_subscriptions_stamp_active_since (652, body verbatim from the live function 2026-11-03) also
--      restamps on a notify_kinds change, and its trigger fires on that column: a watchdog told to tell something new
--      starts from now, so switching a kind on never replays the past week (ruling R6 (a), as an edited filter).
--   3. collections + notify_kinds text[] NOT NULL DEFAULT all five but 'new' (api/notifications.COLLECTION_KINDS):
--      what every watched collection told before this migration. The same CHECK shape, without 'new'.
--   4. collections_stamp_monitoring_enabled_at (230, body verbatim from the live function 2026-11-03) also restamps
--      monitoring_enabled_at on a notify_kinds change while monitoring is on: the collection's anchor (the producer's
--      greatest(added_at, monitoring_enabled_at)) restarts as a watchdog's active_since does. Its trigger already
--      fires on every UPDATE. The one other reader, the undo of an agent's new collection, asks only whether the
--      column is set, which a restamp never changes.
-- authenticated's table-wide INSERT/UPDATE on notification_subscriptions (290) and collections (290) covers each column
-- as it covers the rest.

set local lock_timeout = '5s';

alter table public.notification_subscriptions
  add column if not exists notify_kinds text[] not null default array['new', 'price_drop'];

alter table public.notification_subscriptions drop constraint if exists notification_subscriptions_notify_kinds_ck;
alter table public.notification_subscriptions add constraint notification_subscriptions_notify_kinds_ck check (
  cardinality(notify_kinds) > 0
  and notify_kinds <@ array['new', 'price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source']
  and ('inactive' = any(notify_kinds) or not 'reactivated' = any(notify_kinds))
);

comment on column public.notification_subscriptions.notify_kinds is
  'The property events this watchdog tells (migration 720), of new, price_drop, price_rise, inactive (Left the market), '
  'reactivated (Back on the market, only with inactive) and new_source; each once, in that order. A change restamps '
  'active_since.';

create or replace function public.notification_subscriptions_stamp_active_since()
returns trigger language plpgsql set search_path = '' as $$
begin
  if TG_OP = 'INSERT' or (new.is_active and old.is_active is distinct from true)
     or new.filter_spec is distinct from old.filter_spec
     or new.notify_kinds is distinct from old.notify_kinds then
    new.active_since := now();
  end if;
  return new;
end;
$$;
revoke all on function public.notification_subscriptions_stamp_active_since() from public, anon, authenticated;

drop trigger if exists notification_subscriptions_active_since on public.notification_subscriptions;
create trigger notification_subscriptions_active_since
  before insert or update of is_active, filter_spec, notify_kinds on public.notification_subscriptions
  for each row execute function public.notification_subscriptions_stamp_active_since();

comment on column public.notification_subscriptions.active_since is
  'When this watchdog last started matching (migrations 652, 720): stamped on insert, on every is_active false -> true '
  'flip and on every filter_spec or notify_kinds change (an edited watchdog is a new start). Its windows start no '
  'earlier, so a watchdog alerts only on what happens after it was switched on or last edited.';

alter table public.collections
  add column if not exists notify_kinds text[] not null
    default array['price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source'];

alter table public.collections drop constraint if exists collections_notify_kinds_ck;
alter table public.collections add constraint collections_notify_kinds_ck check (
  cardinality(notify_kinds) > 0
  and notify_kinds <@ array['price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source']
  and ('inactive' = any(notify_kinds) or not 'reactivated' = any(notify_kinds))
);

comment on column public.collections.notify_kinds is
  'The property events this collection tells while monitoring is on (migration 720), of price_drop, price_rise, '
  'inactive (Left the market), reactivated (Back on the market, only with inactive) and new_source; each once, in '
  'that order. A change while monitoring is on restamps monitoring_enabled_at.';

create or replace function public.collections_stamp_monitoring_enabled_at()
returns trigger language plpgsql set search_path = '' as $$
begin
  if NEW.monitoring_enabled
     and (TG_OP = 'INSERT' or OLD.monitoring_enabled is distinct from true
          or NEW.notify_kinds is distinct from OLD.notify_kinds) then
    NEW.monitoring_enabled_at := now();
  end if;
  return NEW;
end;
$$;
revoke all on function public.collections_stamp_monitoring_enabled_at() from public, anon, authenticated;

comment on column public.collections.monitoring_enabled_at is
  'When this collection last started telling (migrations 230, 720): stamped on every monitoring_enabled false -> true '
  'flip and on every notify_kinds change while monitoring is on. The producer tells a member only what happens after '
  'the later of this and when the property was added.';

do $post$
declare
  v_check text;
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'notification_subscriptions'
                    and column_name = 'notify_kinds' and data_type = 'ARRAY' and is_nullable = 'NO') then
    raise exception '720 did not land: notification_subscriptions.notify_kinds must be a NOT NULL array';
  end if;
  select pg_get_constraintdef(oid) into v_check from pg_constraint
   where conrelid = 'public.notification_subscriptions'::regclass
     and conname = 'notification_subscriptions_notify_kinds_ck';
  if v_check is null or v_check not like '%''new_source''%' or v_check not like '%cardinality%' then
    raise exception '720 did not land: notification_subscriptions_notify_kinds_ck is %', v_check;
  end if;
  if not exists (select 1 from pg_trigger t
                  where t.tgrelid = 'public.notification_subscriptions'::regclass and not t.tgisinternal
                    and t.tgname = 'notification_subscriptions_active_since'
                    and pg_get_triggerdef(t.oid) like '%notify_kinds%') then
    raise exception '720 did not land: the active_since trigger does not fire on notify_kinds';
  end if;
  if (select prosrc from pg_proc
       where oid = 'public.notification_subscriptions_stamp_active_since()'::regprocedure) not like '%notify_kinds%' then
    raise exception '720 did not land: the active_since stamp ignores notify_kinds';
  end if;
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'collections'
                    and column_name = 'notify_kinds' and data_type = 'ARRAY' and is_nullable = 'NO') then
    raise exception '720 did not land: collections.notify_kinds must be a NOT NULL array';
  end if;
  select pg_get_constraintdef(oid) into v_check from pg_constraint
   where conrelid = 'public.collections'::regclass and conname = 'collections_notify_kinds_ck';
  if v_check is null or v_check not like '%''new_source''%' or v_check like '%''new''%' then
    raise exception '720 did not land: collections_notify_kinds_ck is %', v_check;
  end if;
  if (select prosrc from pg_proc
       where oid = 'public.collections_stamp_monitoring_enabled_at()'::regprocedure) not like '%notify_kinds%' then
    raise exception '720 did not land: the monitoring_enabled_at stamp ignores notify_kinds';
  end if;
end $post$;

reset lock_timeout;
