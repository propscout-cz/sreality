-- 770_broker_price_every_own_ad.sql -- a broker's price change reads every one of their adverts; "no change" has
-- one definition; repriced counts a price that came back.
--
-- WHY (operator ruling 2026-11-24, #2639, after the review of #2626):
--   * A property whose price was cut and then restored WAS repriced. The book's first/last pair cannot show it
--     (first = last), so the book stores the step count: broker_book.price_changes.
--   * 768 read ONE advert per (property, broker), the broker's first in the property's canonical order, chosen
--     without looking at its price: an unpriced lead beside a priced advert of the same broker read NULL (5 of
--     2,617 rows, 2026-11-23), and a cut on the broker's advert on another portal was not counted. Every advert
--     of the broker is now read; the row is the history of their MOST-REPRICED advert there (ties: one with a
--     last price, one with any price, a live one, the latest seen).
--   * "No change" had two definitions: the rollup gave no percent below two priced snapshots, the broker
--     inventory gave 0. The rule now lives in the function (`change_pct`) and both read it.
--   * max_drop_pct divided inline beside the measure; it now calls measure_price_change_pct.
--   * The view no longer calls property_canonical_listings, so migrations/reverts/635 (which drops that function)
--     works again, and 768's header ("the figure is the property's own whenever its canonical advert is the
--     broker's", which never held: the broker-bounded lineage leaves out other brokers' predecessors) describes
--     nothing any more.
--
-- COST, read-only on production 2026-11-24 (warm, same rows): this projection 32 ms vs 768's 69 ms (properties
-- 600000-602000, 828 rows), 154 ms vs 356 ms (1100000-1102000, 997 rows).
--
-- SHAPE: listing_price_history gains an OUT column, which CREATE OR REPLACE cannot do, so it is dropped and
-- created again, and its one dependent, broker_book_projection (pg_depend, 2026-11-24: nothing depends on the
-- view), with it, in ONE transaction. A reader waits on the view's lock and then sees the new objects; if a lock
-- is not had within 5 s the transaction rolls back and everything stays as it was (re-run the file). Grants are
-- re-issued as they were: EXECUTE to service_role only; the view to service_role only.
-- DEPLOYED CODE: the rollup reads the function as `h.*` into a CTE whose readers all name their columns, and the
-- book write names its columns, so the deployed pass keeps working; it leaves price_changes NULL.
-- APPLY BEFORE THE CODE MERGES (the pass and the dossier read price_changes); 771 fills the book after the rollout.
-- Re-runnable. ROLLBACK: forward only (rule 1).
-- Verify:
--   select change_pct from listing_price_history(null, null);                                  -- NULL, one row
--   select count(*) from information_schema.columns where table_name = 'broker_book'
--     and column_name = 'price_changes';                                                       -- 1

set lock_timeout = '5s';

begin;

drop view if exists public.broker_book_projection;
drop function if exists public.listing_price_history(bigint, bigint);

-- ---------------------------------------------------------------------------
-- 1. THE HISTORY of one advert (768's body; change_pct appended, max_drop_pct through the measure).
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
  max_drop_pct    numeric,
  change_pct      numeric
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
  -- unpriced head). Below two priced snapshots nothing on the advert ever changed (rule 2), so there is no
  -- change to state: change_pct is NULL there, the one definition every surface reads. The windowed counts
  -- decay as steps age out, so a stored copy is as fresh as its row.
  select sp.first_price_czk, sp.last_price_czk, sp.price_points,
         st.drops, st.rises, st.changes, st.changes_30d, st.changes_90d, st.changes_365d, st.max_drop_pct,
         case when sp.price_points >= 2
              then public.measure_price_change_pct(sp.first_price_czk, sp.last_price_czk) end
  from (select ((array_agg(m.first_price order by m.hop desc))[1])::bigint     as first_price_czk,
               ((array_agg(m.last_price) filter (where m.hop = 0))[1])::bigint as last_price_czk,
               (sum(m.price_points))::bigint                                   as price_points
        from members m) sp
  cross join
       (select count(*) filter (where ps.price_czk < ps.prev_price_czk) as drops,
               count(*) filter (where ps.price_czk > ps.prev_price_czk) as rises,
               count(*)                                                 as changes,
               count(*) filter (where ps.scraped_at >= now() - interval '30 days')  as changes_30d,
               count(*) filter (where ps.scraped_at >= now() - interval '90 days')  as changes_90d,
               count(*) filter (where ps.scraped_at >= now() - interval '365 days') as changes_365d,
               -min(public.measure_price_change_pct(ps.prev_price_czk, ps.price_czk))
                 filter (where ps.price_czk < ps.prev_price_czk)        as max_drop_pct
        from steps ps) st
$function$;

comment on function public.listing_price_history(bigint, bigint) is
  'THE price history of one advert (migrations 768, 770, MS10): its own priced snapshots and those of its '
  'same-portal predecessors on its property (with a broker id: only predecessors that broker advertised), as one '
  'row: the oldest priced link''s first price, the head''s last, the priced snapshots, the price steps (each '
  'link''s own listing_price_steps plus one handover step per link) counted by direction and window, the largest '
  'drop, and change_pct = measure_price_change_pct(first, last) from two priced snapshots up (NULL below: nothing '
  'on the advert ever changed). Read by the property rollup (head = the canonical advert), '
  'broker_book_projection (head = each advert of the broker) and the broker inventory (head = the advert).';

revoke all on function public.listing_price_history(bigint, bigint) from public, anon, authenticated;
grant execute on function public.listing_price_history(bigint, bigint) to service_role;

-- ---------------------------------------------------------------------------
-- 2. THE BOOK ROW: 692's fold, and the history of the broker's most-repriced advert on the property.
-- ---------------------------------------------------------------------------
-- The view before the table: the pass's book write locks them in that order too.
create view public.broker_book_projection as
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
         (array_agg(h.first_price_czk order by h.changes desc, h.last_price_czk is null, h.first_price_czk is null,
                                               l.is_active desc, l.last_seen_at desc, l.id desc))[1] as first_price_czk,
         (array_agg(h.last_price_czk order by h.changes desc, h.last_price_czk is null, h.first_price_czk is null,
                                              l.is_active desc, l.last_seen_at desc, l.id desc))[1] as last_price_czk,
         max(h.changes) as price_changes
  from public.listings l
  join public.broker_identities bi on bi.id = l.broker_identity_id
  join public.brokers b on b.id = bi.broker_id and b.status = 'active'
  cross join lateral public.listing_price_history(l.id, bi.broker_id) h
  where l.property_id is not null
  group by l.property_id, bi.broker_id;

comment on view public.broker_book_projection is
  'THE definition of a broker book row (migrations 692, 768, 770): one row per (property, active canonical broker) '
  'over the ads of the broker''s identities on the property. is_active / all_sources / active_sources are '
  'portal_status_matches'' inputs at the broker''s grain (MS19); started_at = min(first_seen_at), the start '
  'measure_tom_days uses; ended_at = NULL while any of those ads is active, else max(last_seen_at); tom_observed = '
  'measure_tom_observed of the broker''s first ad in browse_projection''s order; first_price_czk / last_price_czk / '
  'price_changes = listing_price_history, bounded to the broker''s ads, of their ad with the most price steps on '
  'the property (ties: one with a last price, one with any price, a live one, the latest seen). Read only by the '
  'property maintenance pass, which stores it in broker_book.';

revoke all on public.broker_book_projection from public, anon, authenticated;
grant select on public.broker_book_projection to service_role;

alter table public.broker_book
  add column if not exists price_changes bigint;

comment on column public.broker_book.first_price_czk is
  'listing_price_history(advert, broker).first_price_czk (migrations 768, 770) of the broker''s most-repriced '
  'advert on the property: the first price the broker asked over it and their own same-portal predecessors. '
  'NULL with no priced advert, or not yet recomputed.';
comment on column public.broker_book.last_price_czk is
  'listing_price_history(advert, broker).last_price_czk (migrations 768, 770) of the same advert: its last price.';
comment on column public.broker_book.price_changes is
  'listing_price_history(advert, broker).changes (migration 770) of the same advert: the price steps the broker '
  'made there, a re-list at a new price included. > 0 = the broker repriced the property, a price that came back '
  'included. NULL = not yet recomputed.';

do $post$
declare
  v_role text;
begin
  if (select count(*) from public.listing_price_history(null, null) where change_pct is null) <> 1 then
    raise exception '770 did not land: listing_price_history answers other than one row without a change';
  end if;
  if exists (select 1 from pg_depend d join pg_rewrite r on r.oid = d.objid
             where r.ev_class = 'public.broker_book_projection'::regclass
               and d.refobjid = 'public.property_canonical_listings(bigint)'::regprocedure) then
    raise exception '770 did not land: broker_book_projection still calls property_canonical_listings';
  end if;
  if not has_function_privilege('service_role', 'public.listing_price_history(bigint, bigint)', 'execute')
     or not has_table_privilege('service_role', 'public.broker_book_projection', 'select') then
    raise exception '770 did not land: service_role lost the price history or the book projection';
  end if;
  foreach v_role in array array['anon', 'authenticated'] loop
    if has_table_privilege(v_role, 'public.broker_book_projection', 'select')
       or has_function_privilege(v_role, 'public.listing_price_history(bigint, bigint)', 'execute') then
      raise exception '770 did not land: % can read the broker book or the price history', v_role;
    end if;
  end loop;
end
$post$;

commit;

reset lock_timeout;
