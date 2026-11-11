-- 735_watchdog_notify_kinds.sql -- a watchdog, and a watched collection, choose which property events they tell; a
--   watchdog keeps its slice and tells a New match each time a property enters it.
--
-- WHY (operator request 2026-11-03: "make it possible to set what the watchdog sends notifications for"): a watchdog
--   told 'new' and 'price_drop', always both, and nothing else, while a monitored collection tells five kinds. The
--   producers now detect every property event for a watchdog from the detectors the collection monitor already runs
--   (api/notifications.py, one detector per kind for both producers), and each watchdog keeps the set it tells. A
--   watched collection chooses too (ruling 2026-11-03, "yes, build this now"), from the five it tells today: a
--   property it holds is never a new match to it.
--   The grill rulings of 2026-11-03 (GLOSSARY.md: Watchdog, New match): a watchdog watches its SLICE, the properties
--   on the market its filter matches, and a New match is a property ENTERING it, whatever brought it in (newly
--   listed, a price change, back on the market, a new ad, an ad's facts, its place), told again on every later
--   entry and naming why, but never for a revision of our own data (city index, rent map, proximity) unless the
--   filter uses a Velocity filter, where time passing is itself a cause. Back on the market folds into New match for
--   a watchdog (a returning property re-enters), so a watchdog chooses from five kinds; a collection keeps six minus
--   'new'. One change sends one notification: Price drop and New portal go only to a property already in the slice.
--
-- WHAT CHANGES. Additive only: six columns with their CHECKs, two tables, three trigger functions.
--   1. notification_subscriptions + notify_kinds text[] NOT NULL DEFAULT {new,price_drop}: the property events this
--      watchdog tells, of api/notifications.WATCHDOG_KINDS. The default is what every watchdog told before, so none
--      tells anything new or less until its owner changes it. The CHECK: at least one kind, only those five. The API
--      stores the kinds once each, in api/notifications.KIND_ORDER, so re-saving the same set is no change.
--   2. notification_subscriptions_stamp_active_since (652, body verbatim from the live function 2026-11-03) also
--      restamps on a notify_kinds change, and its trigger fires on that column: a watchdog told to tell something new
--      starts from now, so switching a kind on never replays the past week (ruling R6 (a), as an edited filter).
--   3. collections + notify_kinds text[] NOT NULL DEFAULT all five but 'new' (api/notifications.COLLECTION_KINDS):
--      what every watched collection told before. The CHECK: at least one, only those five, and 'reactivated' (Back
--      on the market) only beside 'inactive' (Left the market), which it answers (api/notifications._reactivated_sql
--      reads that collection's own dispatch).
--   4. collections_stamp_monitoring_enabled_at (230, body verbatim from the live function 2026-11-03) also restamps
--      monitoring_enabled_at on a notify_kinds change while monitoring is on: the collection's anchor (the producer's
--      greatest(added_at, monitoring_enabled_at)) restarts as a watchdog's active_since does. Its trigger already
--      fires on every UPDATE. The one other reader, the undo of an agent's new collection, asks only whether the
--      column is set, which a restamp never changes.
--   5. watchdog_slices (one row per watchdog telling 'new': the start its slice was taken for, and its last pass) and
--      watchdog_slice_members (the properties in it, when each entered, and when one left). Written only by the
--      producer (api/notifications.match_once), service role only: RLS on, no policy, no grant. A pass locks its
--      watchdog's row (FOR UPDATE SKIP LOCKED), so a deploy overlap's two workers never tell one entry twice. A
--      member that left more than a day ago is forgotten; one back within the day re-enters silently (a portal
--      blink, a price bouncing across a cap). A merge is not carried (toolkit/property_carriers.NOT_CARRIED): the
--      next pass reads it, and a survivor of a member takes its place silently.
--   6. properties + back_on_market_at timestamptz: when the property last came back on the market (is_active false
--      -> true on a live property), stamped by a BEFORE UPDATE trigger, so every writer that revives one stamps it.
--      Nothing recorded a return before (listings.inactive_at is cleared on revival), and a New match names it.
--      NULL = never back since this migration. A detach's reactivation (status merged_away -> active) is no return.
--   7. notification_dispatches + match_reason text: why a New match entered (listed, price, back, portal, changed,
--      other), shown on the alert; NULL on every other kind, and on a 'new' written before this migration (each a
--      newly listed property, so read as 'listed').
-- authenticated's table-wide INSERT/UPDATE on notification_subscriptions (290) and collections (290) covers each
-- new column as it covers the rest.

set local lock_timeout = '5s';

-- 1-2. a watchdog's kinds

alter table public.notification_subscriptions
  add column if not exists notify_kinds text[] not null default array['new', 'price_drop'];

alter table public.notification_subscriptions drop constraint if exists notification_subscriptions_notify_kinds_ck;
alter table public.notification_subscriptions add constraint notification_subscriptions_notify_kinds_ck check (
  cardinality(notify_kinds) > 0
  and notify_kinds <@ array['new', 'price_drop', 'price_rise', 'inactive', 'new_source']
);

comment on column public.notification_subscriptions.notify_kinds is
  'The property events this watchdog tells (migration 735), of new (New match: a property entering its slice, a '
  'return included), price_drop, price_rise, inactive (Left the market) and new_source; each once, in that order. '
  'A change restamps active_since.';

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
  'When this watchdog last started matching (migrations 652, 735): stamped on insert, on every is_active false -> true '
  'flip and on every filter_spec or notify_kinds change (an edited watchdog is a new start). Its windows start no '
  'earlier, and its slice is taken afresh, so a watchdog alerts only on what happens after it was switched on or '
  'last edited.';

-- 3-4. a watched collection's kinds

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
  'The property events this collection tells while monitoring is on (migration 735), of price_drop, price_rise, '
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
  'When this collection last started telling (migrations 230, 735): stamped on every monitoring_enabled false -> true '
  'flip and on every notify_kinds change while monitoring is on. The producer tells a member only what happens after '
  'the later of this and when the property was added.';

-- 5. a watchdog's slice

create table if not exists public.watchdog_slices (
  subscription_id uuid primary key references public.notification_subscriptions(id) on delete cascade,
  started_at timestamptz not null,
  sliced_at timestamptz
);
alter table public.watchdog_slices enable row level security;
revoke all on public.watchdog_slices from public, anon, authenticated;

comment on table public.watchdog_slices is
  'One row per watchdog telling new (migration 735): started_at = the active_since its slice was taken for (a '
  'restart takes it afresh, silently), sliced_at = its last pass (NULL until the first). Producer state, service '
  'role only; a pass holds this row FOR UPDATE.';

create table if not exists public.watchdog_slice_members (
  subscription_id uuid not null references public.watchdog_slices(subscription_id) on delete cascade,
  property_id bigint not null references public.properties(id),
  entered_at timestamptz not null,
  left_at timestamptz,
  primary key (subscription_id, property_id)
);
alter table public.watchdog_slice_members enable row level security;
revoke all on public.watchdog_slice_members from public, anon, authenticated;

comment on table public.watchdog_slice_members is
  'The properties in a watchdog''s slice (migration 735): on the market and matching its filter at its last pass. '
  'entered_at = the pass it entered at (a return within a day keeps it), left_at = the pass it left at (NULL while '
  'in it; forgotten a day after). Price drop and New portal tell only a member, of a change after it entered.';

-- 6. a property's return

alter table public.properties add column if not exists back_on_market_at timestamptz;

comment on column public.properties.back_on_market_at is
  'When this property last came back on the market (migration 735): is_active false -> true while it stays a live '
  'property, stamped by properties_stamp_back_on_market_at. NULL = no return since 735.';

create or replace function public.properties_stamp_back_on_market_at()
returns trigger language plpgsql set search_path = '' as $$
begin
  new.back_on_market_at := now();
  return new;
end;
$$;
revoke all on function public.properties_stamp_back_on_market_at() from public, anon, authenticated;

drop trigger if exists properties_back_on_market_at on public.properties;
create trigger properties_back_on_market_at
  before update of is_active on public.properties
  for each row
  when (old.is_active = false and new.is_active and old.status = 'active' and new.status = 'active')
  execute function public.properties_stamp_back_on_market_at();

-- 7. why a New match entered

alter table public.notification_dispatches add column if not exists match_reason text;

alter table public.notification_dispatches drop constraint if exists notification_dispatches_match_reason_ck;
alter table public.notification_dispatches add constraint notification_dispatches_match_reason_ck check (
  match_reason is null
  or (change_kind = 'new' and match_reason in ('listed', 'price', 'back', 'portal', 'changed', 'other'))
);

comment on column public.notification_dispatches.match_reason is
  'Why a New match entered its watchdog''s slice (migration 735): listed (newly listed), price (its price changed), '
  'back (back on the market), portal (on a new portal), changed (an ad or its place changed), other (time passed, '
  'or nothing we name). NULL on every other kind, and on a new written before 735 (newly listed).';

do $post$
declare
  v_check text;
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'notification_subscriptions'
                    and column_name = 'notify_kinds' and data_type = 'ARRAY' and is_nullable = 'NO') then
    raise exception '735 did not land: notification_subscriptions.notify_kinds must be a NOT NULL array';
  end if;
  select pg_get_constraintdef(oid) into v_check from pg_constraint
   where conrelid = 'public.notification_subscriptions'::regclass
     and conname = 'notification_subscriptions_notify_kinds_ck';
  if v_check is null or v_check not like '%''new_source''%' or v_check like '%reactivated%' then
    raise exception '735 did not land: notification_subscriptions_notify_kinds_ck is %', v_check;
  end if;
  if not exists (select 1 from pg_trigger t
                  where t.tgrelid = 'public.notification_subscriptions'::regclass and not t.tgisinternal
                    and t.tgname = 'notification_subscriptions_active_since'
                    and pg_get_triggerdef(t.oid) like '%notify_kinds%') then
    raise exception '735 did not land: the active_since trigger does not fire on notify_kinds';
  end if;
  if (select prosrc from pg_proc
       where oid = 'public.notification_subscriptions_stamp_active_since()'::regprocedure) not like '%notify_kinds%' then
    raise exception '735 did not land: the active_since stamp ignores notify_kinds';
  end if;
  select pg_get_constraintdef(oid) into v_check from pg_constraint
   where conrelid = 'public.collections'::regclass and conname = 'collections_notify_kinds_ck';
  if v_check is null or v_check not like '%''new_source''%' or v_check like '%''new''%' then
    raise exception '735 did not land: collections_notify_kinds_ck is %', v_check;
  end if;
  if (select prosrc from pg_proc
       where oid = 'public.collections_stamp_monitoring_enabled_at()'::regprocedure) not like '%notify_kinds%' then
    raise exception '735 did not land: the monitoring_enabled_at stamp ignores notify_kinds';
  end if;
  if to_regclass('public.watchdog_slices') is null or to_regclass('public.watchdog_slice_members') is null then
    raise exception '735 did not land: the slice tables are missing';
  end if;
  if exists (select 1 from pg_class where oid in ('public.watchdog_slices'::regclass,
                                                  'public.watchdog_slice_members'::regclass)
                                      and not relrowsecurity) then
    raise exception '735 did not land: a slice table has RLS off';
  end if;
  if not exists (select 1 from pg_trigger t
                  where t.tgrelid = 'public.properties'::regclass and not t.tgisinternal
                    and t.tgname = 'properties_back_on_market_at') then
    raise exception '735 did not land: the back_on_market_at trigger is missing';
  end if;
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.notification_dispatches'::regclass
                    and conname = 'notification_dispatches_match_reason_ck') then
    raise exception '735 did not land: notification_dispatches_match_reason_ck is missing';
  end if;
end $post$;

reset lock_timeout;
