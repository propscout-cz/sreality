-- Back on the market: one fact on the property, one kind for watchdogs and collections (#2576, spec #2574).
--
-- Until now one market event had two definitions. A collection told 'reactivated' by reading its own
-- earlier 'inactive' alert; a watchdog told the same return as a New match with reason 'back', off
-- properties.back_on_market_at, which 735's trigger stamped on EVERY return, a portal blink included.
-- From here the property carries the fact and both producers read it (api/notifications._reactivated_sql):
--
--   1. properties + left_market_at timestamptz: since when the property is off the market. Stamped when
--      it leaves (is_active true -> false on a live property) and when it is born with no active ad;
--      cleared on its return. Nullable, no default: catalog-only, no rewrite.
--   2. properties_stamp_back_on_market_at restated. Its UPDATE trigger fires on both flips; a second
--      trigger stamps a birth. Returning stamps back_on_market_at ONLY when
--        - the property was away 24 hours or more (api/notifications.INACTIVE_DWELL: the dwell after which
--          Left the market is told), left_market_at NULL counting as away long enough (step 3), and
--        - the return is not a merge bringing in a property that was itself published: that unit was on
--          the market in our records all along, under the retired record, and whoever follows it was told
--          there. A merge retires with merged_at = now() and recomputes the survivor in the same
--          transaction (toolkit/property_identity.merge_property_set), so the trigger reads exactly that.
--          A merge of an ad never published (the engine read it first) IS the unit's return.
--      Stored back_on_market_at values stay as they are: nothing is erased.
--   3. left_market_at filled for the properties that left within the last 24 hours (the newest ended ad's
--      inactive_at): one UPDATE of a few thousand rows (5,404 on 2026-11-21) that sets neither is_active
--      nor any rollup column, so it fires no trigger and enqueues nothing into dirty_properties. After it,
--      a NULL on a property off the market means it left more than a day before this migration: away long
--      enough. Without it every blink straddling the apply would be stamped a return.
--   4. properties_back_on_market_at_idx, partial (back_on_market_at IS NOT NULL): the detector reads the
--      returns of the last 7 days. Built CONCURRENTLY with no lock_timeout (605/746/747: at the house 5 s
--      the build's wait for a long reader cancels it and leaves it INVALID); an INVALID one from a
--      cancelled run is moved aside and dropped, so the file can be run again.
--   5. A watchdog may tell 'reactivated'; a collection may tell it without 'inactive' (the detector no
--      longer reads the 'inactive' alert). Both CHECKs restated; notification_subscriptions (17 rows) and
--      collections are small and cold, so each CHECK is replaced in one step.
--   6. Every watchdog gains 'reactivated' (operator ruling 2026-11-21: all 17 tell 'new' today, which
--      carried a return), and the column default gains it for a new one. active_since is put back in the
--      same transaction: 735's trigger restamps it on a kinds change, which would restart each slice and
--      drop the price steps and delistings not yet told. No restart is needed: a watchdog telling 'new'
--      tells a return only as the property re-enters its slice, so an old return is never told again.
--
-- Not transactional as a whole (step 4 is CONCURRENTLY): apply through apply_migration.yml, never the MCP.
-- Every step is idempotent. APPLY MINUTES BEFORE THE CODE MERGES, with CI green and the queue free: until
-- the deploy the old API refuses a watchdog save (its kinds have no 'reactivated').

set lock_timeout = '5s';

alter table public.properties add column if not exists left_market_at timestamptz;

comment on column public.properties.left_market_at is
  'Since when this property is off the market (migration 754): stamped by properties_stamp_back_on_market_at '
  'when it leaves (is_active true -> false while it stays a live property) or is born with no active ad, '
  'cleared on its return. NULL while on the market, and on a property that left more than a day before 754.';

comment on column public.properties.back_on_market_at is
  'When this property last came back on the market after 24 hours or more away (migration 754; before it, '
  'after any absence): is_active false -> true while it stays a live property, unless a merge brought in a '
  'property already published. Stamped by properties_stamp_back_on_market_at; Back on the market reads it '
  'for watchdogs and collections alike.';

create or replace function public.properties_stamp_back_on_market_at()
returns trigger language plpgsql set search_path = '' as $$
begin
  if tg_op = 'INSERT' or not new.is_active then
    new.left_market_at := now();
    return new;
  end if;
  if (old.left_market_at is null or old.left_market_at <= now() - interval '24 hours')
     and not exists (select 1 from public.properties x
                      where x.merged_into = new.id and x.merged_at = now() and x.settled_at is not null) then
    new.back_on_market_at := now();
  end if;
  new.left_market_at := null;
  return new;
end;
$$;
revoke all on function public.properties_stamp_back_on_market_at() from public, anon, authenticated;

create or replace trigger properties_back_on_market_at
  before update of is_active on public.properties
  for each row
  when (old.is_active is distinct from new.is_active and old.status = 'active' and new.status = 'active')
  execute function public.properties_stamp_back_on_market_at();

create or replace trigger properties_born_off_market
  before insert on public.properties
  for each row
  when (not new.is_active)
  execute function public.properties_stamp_back_on_market_at();

update public.properties p
   set left_market_at = g.left_at
  from (select l.property_id, max(l.inactive_at) as left_at
          from public.listings l
         where l.inactive_at > now() - interval '24 hours'
         group by l.property_id) g
 where p.id = g.property_id and p.status = 'active' and not p.is_active and p.left_market_at is null;

do $invalid$
begin
  if exists (select 1 from pg_index i
              where i.indexrelid = to_regclass('public.properties_back_on_market_at_idx') and not i.indisvalid) then
    alter index public.properties_back_on_market_at_idx rename to properties_back_on_market_at_idx_invalid;
    raise notice '754: an INVALID properties_back_on_market_at_idx from a cancelled build moved aside';
  end if;
end
$invalid$;

set lock_timeout = 0;
set statement_timeout = '1900s';

drop index concurrently if exists public.properties_back_on_market_at_idx_invalid;

create index concurrently if not exists properties_back_on_market_at_idx
  on public.properties (back_on_market_at) where back_on_market_at is not null;

set lock_timeout = '5s';
set statement_timeout = '600s';

alter table public.notification_subscriptions drop constraint if exists notification_subscriptions_notify_kinds_ck;
alter table public.notification_subscriptions add constraint notification_subscriptions_notify_kinds_ck check (
  cardinality(notify_kinds) > 0
  and notify_kinds <@ array['new', 'price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source']
);

alter table public.notification_subscriptions
  alter column notify_kinds set default array['new', 'price_drop', 'reactivated'];

comment on column public.notification_subscriptions.notify_kinds is
  'The property events this watchdog tells (migrations 735, 754), of new (New match: a property entering its '
  'slice), price_drop, price_rise, inactive (Left the market), reactivated (Back on the market) and '
  'new_source; each once, in that order. A change restamps active_since.';

alter table public.collections drop constraint if exists collections_notify_kinds_ck;
alter table public.collections add constraint collections_notify_kinds_ck check (
  cardinality(notify_kinds) > 0
  and notify_kinds <@ array['price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source']
);

comment on column public.collections.notify_kinds is
  'The property events this collection tells while monitoring is on (migrations 735, 754), of price_drop, '
  'price_rise, inactive (Left the market), reactivated (Back on the market) and new_source; each once, in '
  'that order. A change restamps monitoring_enabled_at.';

do $kinds$
declare
  w record;
begin
  for w in select id, active_since from public.notification_subscriptions
            where not 'reactivated' = any (notify_kinds)
  loop
    update public.notification_subscriptions s
       set notify_kinds = array(
             select k from unnest(array['new', 'price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source']) k
              where k = any (s.notify_kinds || array['reactivated']))
     where s.id = w.id;
    update public.notification_subscriptions s set active_since = w.active_since where s.id = w.id;
  end loop;
end
$kinds$;

do $post$
declare
  v_check text;
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'properties' and column_name = 'left_market_at') then
    raise exception '754 did not land: properties.left_market_at is missing';
  end if;
  if (select prosrc from pg_proc
       where oid = 'public.properties_stamp_back_on_market_at()'::regprocedure) not like '%merged_into%' then
    raise exception '754 did not land: the return stamp does not read left_market_at and the merge';
  end if;
  if not exists (select 1 from pg_trigger t
                  where t.tgrelid = 'public.properties'::regclass and not t.tgisinternal
                    and t.tgname = 'properties_back_on_market_at'
                    and pg_get_triggerdef(t.oid) like '%IS DISTINCT FROM%') then
    raise exception '754 did not land: the return trigger does not fire on both flips';
  end if;
  if not exists (select 1 from pg_index i
                  where i.indexrelid = to_regclass('public.properties_back_on_market_at_idx') and i.indisvalid) then
    raise exception '754 did not land: properties_back_on_market_at_idx is missing or invalid';
  end if;
  select pg_get_constraintdef(oid) into v_check from pg_constraint
   where conrelid = 'public.notification_subscriptions'::regclass
     and conname = 'notification_subscriptions_notify_kinds_ck';
  if v_check is null or v_check not like '%reactivated%' then
    raise exception '754 did not land: notification_subscriptions_notify_kinds_ck is %', v_check;
  end if;
  select pg_get_constraintdef(oid) into v_check from pg_constraint
   where conrelid = 'public.collections'::regclass and conname = 'collections_notify_kinds_ck';
  if v_check is null or v_check like '%ANY (notify_kinds)%' then
    raise exception '754 did not land: collections_notify_kinds_ck is %', v_check;
  end if;
  if exists (select 1 from public.notification_subscriptions where not 'reactivated' = any (notify_kinds)) then
    raise exception '754 did not land: a watchdog does not tell reactivated';
  end if;
  if not exists (select 1 from pg_trigger t
                  where t.tgrelid = 'public.properties'::regclass and not t.tgisinternal
                    and t.tgname = 'properties_born_off_market') then
    raise exception '754 did not land: the birth trigger is missing';
  end if;
end
$post$;

reset statement_timeout;
reset lock_timeout;
