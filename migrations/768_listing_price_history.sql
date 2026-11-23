-- 768_listing_price_history.sql -- ONE definition of an advert's price history, read at every grain.
--
-- WHY. The price figures of a property are its canonical advert's LINEAGE (MS10): its own priced snapshots and
-- those of its same-portal predecessors on the property, each link's own steps (`listing_price_steps`, 559) plus
-- one handover step per link, the total compounded from the oldest priced link's first price to the head's last.
-- That rule was spelled once, inline, in the property rollup (scripts/recompute_property_stats.py), so nothing
-- else could read it: a broker page asking "how much does this broker change prices" would have needed a second
-- spelling. Production, 2026-11-23, 512 brokers / 14,178 book rows: 12 % of repriced rows are re-list cuts no
-- single advert's own history shows, and the property's canonical advert is another advertiser's in 9.6 % of rows.
--
--   listing_price_history(head, broker)  -> ONE row: first / last price, priced snapshots, the step counts
--     head    the advert whose history is asked (a property: its canonical advert; a book row: the broker's
--             lead advert; an inventory row: the advert itself)
--     broker  NULL = the lineage follows every same-portal predecessor (the property's figures, unchanged);
--             a broker id = only predecessors that broker advertised (a takeover from another advertiser at a
--             new price is not the broker's own change: 2.8 % of repriced book rows, same sample)
--   measure_price_change_pct(first, last) -> the signed percent every surface shows, NULL without a first price
--
-- The rollup's `lineage` / `members` / `steps` / `price_hist` / `lineage_span` CTEs are this function's body,
-- moved; the same PR makes the rollup call it. Checked read-only on production before the move: 0 differences
-- on 1,560 properties (303 repriced) across total_price_change_pct, price_change_count, price_drop_count,
-- price_rise_count and max_price_drop_pct; about 0.55 ms per head.
--
-- THE BOOK. `broker_book` gains first_price_czk / last_price_czk: `listing_price_history` of the broker's LEAD
-- advert on the property (the first of the broker's adverts in the property's canonical order, so the figure is
-- the property's own whenever its canonical advert is the broker's), bounded to the broker's adverts.
-- `broker_book_projection` stays THE definition of a row and appends the two columns.
--
-- ADDITIVE: two functions, two nullable columns, one view replaced in place with two columns appended.
-- APPLY BEFORE THE CODE MERGES (the pass the same PR ships writes the two columns and calls the function). The
-- deployed pass names its columns, so it keeps working against the wider view and leaves the two columns NULL.
-- POST-DEPLOY: the new pass fills a row when its property is next recomputed; the daily full sweep fills the
-- rest (one cycle begun after the rollout). Until then a NULL pair reads "no priced advert" and is left out of
-- the broker's price figures. Re-runnable. ROLLBACK: forward only (rule 1).
-- Verify:
--   select count(*) from listing_price_history(null, null);                                    -- 1
--   select measure_price_change_pct(100, 90);                                                  -- -10
--   select count(*) from information_schema.columns where table_name = 'broker_book'
--     and column_name in ('first_price_czk', 'last_price_czk');                                -- 2

set lock_timeout = '5s';

begin;

-- ---------------------------------------------------------------------------
-- 1. THE MEASURE: the signed percent between a first and a last price.
-- ---------------------------------------------------------------------------
create or replace function public.measure_price_change_pct(p_first_price bigint, p_last_price bigint)
returns numeric
language sql
immutable parallel safe
as $function$
  select case when p_first_price > 0
              then (p_last_price - p_first_price)::numeric / p_first_price * 100 end
$function$;

comment on function public.measure_price_change_pct(bigint, bigint) is
  'The signed price change in percent, (last - first) / first * 100 (migration 768): NULL without a positive '
  'first price or without a last price. The one spelling behind properties.total_price_change_pct, a broker''s '
  'price figures and a broker inventory row.';

revoke all on function public.measure_price_change_pct(bigint, bigint) from public, anon, authenticated;
grant execute on function public.measure_price_change_pct(bigint, bigint) to service_role;

-- ---------------------------------------------------------------------------
-- 2. THE HISTORY of one advert: the rollup's price CTEs, keyed by the head advert. Always one row.
-- ---------------------------------------------------------------------------
create or replace function public.listing_price_history(p_head_listing_id bigint, p_broker_id bigint)
returns table (
  first_price_czk bigint,
  last_price_czk  bigint,
  price_points    bigint,
  drops           bigint,
  rises           bigint,
  changes         bigint,
  changes_30d     bigint,
  changes_90d     bigint,
  changes_365d    bigint,
  max_drop_pct    numeric
)
language sql
stable
as $function$
  -- The head and its same-portal predecessors on the property: each predecessor is the advert on that portal
  -- last seen latest, strictly before the later link was first seen, so adverts that ran at the same time
  -- never link; first_seen_at falls along the chain, so it ends.
  with recursive link as (
    select l.property_id as pid, l.id, l.source, l.first_seen_at, 0 as hop
    from public.listings l
    where l.id = p_head_listing_id
    union all
    select k.pid, pre.id, k.source, pre.first_seen_at, k.hop + 1
    from link k
    cross join lateral (
      select l.id, l.first_seen_at
      from public.listings l
      where l.property_id = k.pid and l.source = k.source
        and l.last_seen_at < k.first_seen_at and l.first_seen_at < k.first_seen_at
        and (p_broker_id is null
             or exists (select 1 from public.broker_identities bi
                        where bi.id = l.broker_identity_id and bi.broker_id = p_broker_id))
      order by l.last_seen_at desc, l.id desc
      limit 1
    ) pre
  ),
  -- Each priced link's first and last price over its own snapshots, dated by the first.
  members as (
    select g.hop,
      (array_agg(s.price_czk order by s.scraped_at, s.id))[1]           as first_price,
      min(s.scraped_at)                                                 as first_at,
      (array_agg(s.price_czk order by s.scraped_at desc, s.id desc))[1] as last_price,
      count(*)                                                          as price_points
    from link g
    join public.listing_snapshots s on s.listing_id = g.id
    where s.price_czk is not null
    group by g.hop
  ),
  -- Each link's OWN steps (listing_price_steps, 559, the one step definition the alerts read too) plus one
  -- handover step into each priced link from the last price of the next older priced link, dated at the
  -- newer link's first.
  steps as (
    select ps.scraped_at, ps.price_czk, ps.prev_price_czk
    from link g
    join public.listing_price_steps ps on ps.listing_id = g.id
    union all
    select h.first_at, h.first_price, h.prev_price_czk
    from (select m.first_at, m.first_price,
                 lead(m.last_price) over (order by m.hop) as prev_price_czk
          from members m) h
    where h.first_price <> h.prev_price_czk
  )
  -- The span telescopes to the oldest priced link's first price against the head's last (NULL with an
  -- unpriced head). The windowed counts decay as steps age out, so a stored copy is as fresh as its row.
  select sp.first_price_czk::bigint, sp.last_price_czk::bigint, sp.price_points::bigint,
         st.drops, st.rises, st.changes, st.changes_30d, st.changes_90d, st.changes_365d, st.max_drop_pct
  from (select (array_agg(m.first_price order by m.hop desc))[1]     as first_price_czk,
               (array_agg(m.last_price) filter (where m.hop = 0))[1] as last_price_czk,
               sum(m.price_points)                                   as price_points
        from members m) sp
  cross join
       (select count(*) filter (where ps.price_czk < ps.prev_price_czk) as drops,
               count(*) filter (where ps.price_czk > ps.prev_price_czk) as rises,
               count(*)                                                 as changes,
               count(*) filter (where ps.scraped_at >= now() - interval '30 days')  as changes_30d,
               count(*) filter (where ps.scraped_at >= now() - interval '90 days')  as changes_90d,
               count(*) filter (where ps.scraped_at >= now() - interval '365 days') as changes_365d,
               max((ps.prev_price_czk - ps.price_czk)::numeric / ps.prev_price_czk * 100)
                 filter (where ps.price_czk < ps.prev_price_czk)        as max_drop_pct
        from steps ps) st
$function$;

comment on function public.listing_price_history(bigint, bigint) is
  'THE price history of one advert (migration 768, MS10): its own priced snapshots and those of its same-portal '
  'predecessors on its property (with a broker id: only predecessors that broker advertised), as one row: the '
  'oldest priced link''s first price, the head''s last, the priced snapshots, and the price steps (each link''s '
  'own listing_price_steps plus one handover step per link) counted by direction and window. Read by the '
  'property rollup (head = the canonical advert), broker_book_projection (head = the broker''s lead advert) '
  'and the broker inventory (head = the advert).';

revoke all on function public.listing_price_history(bigint, bigint) from public, anon, authenticated;
grant execute on function public.listing_price_history(bigint, bigint) to service_role;

-- ---------------------------------------------------------------------------
-- 3. THE BOOK carries the broker's own first and last price of the property.
-- ---------------------------------------------------------------------------
-- The view before the table: the pass's book write locks them in that order too.
-- 692's body, with the lead advert picked beside the fold and its history appended. A qual on property_id is
-- still a qual on a grouping column of the inner select, so it applies below the aggregate.
create or replace view public.broker_book_projection as
  select s.property_id, s.broker_id, s.is_active, s.all_sources, s.active_sources,
         s.started_at, s.ended_at, s.tom_observed,
         h.first_price_czk, h.last_price_czk
  from (
    select l.property_id,
           bi.broker_id,
           bool_or(l.is_active) as is_active,
           array_agg(distinct l.source order by l.source) as all_sources,
           coalesce(array_agg(distinct l.source order by l.source) filter (where l.is_active), '{}') as active_sources,
           min(l.first_seen_at) as started_at,
           case when bool_or(l.is_active) then null else max(l.last_seen_at) end as ended_at,
           (array_agg(public.measure_tom_observed(l.source, l.category_main, l.category_type, l.first_seen_at)
                      order by (l.category_main is null or l.category_type is null), l.first_seen_at, l.id))[1]
             as tom_observed,
           (array_agg(l.id order by o.canonical_rank))[1] as lead_listing_id
    from public.listings l
    join public.broker_identities bi on bi.id = l.broker_identity_id
    join public.brokers b on b.id = bi.broker_id and b.status = 'active'
    join lateral public.property_canonical_listings(l.property_id) o on o.listing_id = l.id
    where l.property_id is not null
    group by l.property_id, bi.broker_id
  ) s
  cross join lateral public.listing_price_history(s.lead_listing_id, s.broker_id) h;

comment on view public.broker_book_projection is
  'THE definition of a broker book row (migrations 692, 768): one row per (property, active canonical broker) '
  'over the ads of the broker''s identities on the property. is_active / all_sources / active_sources are '
  'portal_status_matches'' inputs at the broker''s grain (MS19); started_at = min(first_seen_at), the start '
  'measure_tom_days uses; ended_at = NULL while any of those ads is active, else max(last_seen_at); tom_observed = '
  'measure_tom_observed of the broker''s first ad in browse_projection''s order; first_price_czk / last_price_czk '
  '= listing_price_history of the broker''s lead ad (its first in the property''s canonical order), bounded to '
  'the broker''s ads. Read only by the property maintenance pass, which stores it in broker_book.';

revoke all on public.broker_book_projection from public, anon, authenticated;

alter table public.broker_book
  add column if not exists first_price_czk bigint,
  add column if not exists last_price_czk  bigint;

comment on column public.broker_book.first_price_czk is
  'listing_price_history(lead advert, broker).first_price_czk (migration 768): the first price the broker asked '
  'for the property, over the lead advert (the first of the broker''s adverts in the property''s canonical '
  'order) and the broker''s own same-portal predecessors. NULL with no priced advert, or not yet recomputed.';
comment on column public.broker_book.last_price_czk is
  'listing_price_history(lead advert, broker).last_price_czk (migration 768): the lead advert''s last price. '
  'measure_price_change_pct(first_price_czk, last_price_czk) is the broker''s price change on the property.';

do $post$
declare
  v_role text;
begin
  if (select count(*) from public.listing_price_history(null, null)) <> 1 then
    raise exception '768 did not land: listing_price_history answers other than one row for no advert';
  end if;
  if public.measure_price_change_pct(100, 90) <> -10 or public.measure_price_change_pct(0, 90) is not null then
    raise exception '768 did not land: measure_price_change_pct is not (last - first) / first * 100';
  end if;
  foreach v_role in array array['anon', 'authenticated'] loop
    if has_table_privilege(v_role, 'public.broker_book_projection', 'select')
       or has_function_privilege(v_role, 'public.listing_price_history(bigint, bigint)', 'execute')
       or has_function_privilege(v_role, 'public.measure_price_change_pct(bigint, bigint)', 'execute') then
      raise exception '768 did not land: % can read the broker book or the price history', v_role;
    end if;
  end loop;
end
$post$;

commit;

reset lock_timeout;
