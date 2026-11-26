-- A merge that brings a live property into one long off the market IS that property's return (#2583, spec #2574).
--
-- Migration 754 stamped no return when the merge brought in a property already published: whoever followed
-- the live record was told there. That silenced the followers of the OTHER record too: a collection holding
-- the long-gone property was never told it came back. The trigger cannot tell followers apart, so the rule
-- leaves it: a property away 24 hours or more that is active again has returned, however its active ad
-- arrived. Who is told is decided where the followers are known (api/notifications.py):
--   - a collection whose membership a merge moved onto the survivor is told only if a row of it came from a
--     property off the market (`_HELD_AT_THE_RETURN`, off property_merge_carries.from_on_market, migration 777);
--   - a watchdog telling 'new' stays silent through its slice's merged-member exemption (`_SLICE_PASS_SQL`).
--
-- One `create or replace function`: the function loses its `not exists (... merged_at = now() ...)` arm and
-- nothing else. No table, column, index, trigger or stored value changes. `properties` is hot, but replacing
-- a function takes no lock on the table (only on the function's own catalog row); lock_timeout 5 s all the same.
-- Order: AFTER the code of #2669 is live (before it, a collection whose membership a merge moved from a live
-- property would be told too). 777, the column that code writes, goes BEFORE it. Gate #2670.
-- Undo: restate 754's function body.

set lock_timeout = '5s';

create or replace function public.properties_stamp_back_on_market_at()
returns trigger language plpgsql set search_path = '' as $$
begin
  if tg_op = 'INSERT' or not new.is_active then
    new.left_market_at := now();
    return new;
  end if;
  if old.left_market_at is null or old.left_market_at <= now() - interval '24 hours' then
    new.back_on_market_at := now();
  end if;
  new.left_market_at := null;
  return new;
end;
$$;
revoke all on function public.properties_stamp_back_on_market_at() from public, anon, authenticated;

comment on column public.properties.back_on_market_at is
  'When this property last came back on the market after 24 hours or more away (migrations 754, 778): '
  'is_active false -> true while it stays a live property, a merge that brings in a live property included. '
  'Stamped by properties_stamp_back_on_market_at; Back on the market reads it for watchdogs and collections alike.';

do $post$
begin
  if position('merged_at' in pg_get_functiondef('public.properties_stamp_back_on_market_at()'::regprocedure)) > 0 then
    raise exception '778: the merge exemption is still in properties_stamp_back_on_market_at';
  end if;
end
$post$;
