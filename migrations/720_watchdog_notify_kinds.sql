-- 720_watchdog_notify_kinds.sql -- a watchdog chooses which property events it tells.
--
-- WHY (operator request 2026-11-03: "make it possible to set what the watchdog sends notifications for"): a watchdog
--   told 'new' and 'price_drop', always both, and nothing else, while a monitored collection tells five kinds. The
--   producers now detect all six property events for a watchdog from the detectors the collection monitor already
--   runs (api/notifications.py, one detector per kind for both producers), and each watchdog keeps the set it tells.
--
-- WHAT CHANGES. Additive only: one column with its CHECK, one trigger function restated.
--   1. notification_subscriptions + notify_kinds text[] NOT NULL DEFAULT {new,price_drop}: the property events this
--      watchdog tells, of api/notifications.WATCHDOG_KINDS. The default is what every watchdog told before this
--      migration, so none tells anything new or less until its owner changes it. The CHECK: at least one kind, only
--      those six, and 'reactivated' (Relisted) only beside 'inactive' (Delisted), because a relisting answers a
--      delisting the same watchdog told (api/notifications._reactivated_sql reads that dispatch). The API stores the
--      kinds once each, in WATCHDOG_KINDS order, so re-saving the same set is no change.
--   2. notification_subscriptions_stamp_active_since (652, body verbatim from the live function 2026-11-03) also
--      restamps on a notify_kinds change, and its trigger fires on that column: a watchdog told to tell something new
--      starts from now, so switching a kind on never replays the past week (ruling R6 (a), as an edited filter).
-- authenticated's table-wide INSERT/UPDATE on notification_subscriptions (290) covers the column as it covers the rest.

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
  'The property events this watchdog tells (migration 720), of new, price_drop, price_rise, inactive (Delisted), '
  'reactivated (Relisted, only with inactive) and new_source; each once, in that order. A change restamps active_since.';

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
end $post$;

reset lock_timeout;
