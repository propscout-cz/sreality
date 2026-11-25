-- 772_drop_broker_listings_public.sql -- the per-ad broker inventory view goes: a broker's inventory is their book.
--
-- WHY (#2654, docs/design/broker-board.md § The inventory by property; operator ruling 4, 2026-11-24). The broker
-- page's table reads the book, one row per property (toolkit.brokers.broker_properties: broker_book joined to
-- browse_list). broker_listings_public (189; 224, 358, 503; last defined by 508) served only the per-ad table it
-- replaces: GET /brokers/{id}/listings through toolkit.brokers.broker_listings, both deleted by this PR.
--
-- NO BACKUP: a view holds no data, and its last definition is 508's text (migrations/508, section 1g).
-- DEPENDENTS (production, 2026-11-24): none in pg_depend, no function body names it; service_role and postgres
-- alone held a grant (A6, 299).
-- APPLY ORDER: after the PR that moved the page off the view (#2656) is live on the API and the SPA; the API
-- deployed then still serves GET /brokers/{id}/listings to a tab left open on an older bundle, which answers an
-- error from this apply until this PR's rollout removes the route. Apply BEFORE this PR merges (its tests expect
-- the view gone) or right after: no code in this PR reads it.
-- The comment on listing_price_history names its readers, so it is re-issued without the inventory.
-- Re-runnable. ROLLBACK: forward only (rule 1); 508 (section 1g) holds the view's last text.
-- Verify:
--   select to_regclass('public.broker_listings_public');   -- NULL

set lock_timeout = '5s';

begin;

drop view if exists public.broker_listings_public;

comment on function public.listing_price_history(bigint, bigint) is
  'THE price history of one advert (migrations 768, 770, MS10): its own priced snapshots and those of its '
  'same-portal predecessors on its property (with a broker id: only predecessors that broker advertised), as one '
  'row: the oldest priced link''s first price, the head''s last, the priced snapshots, the price steps (each '
  'link''s own listing_price_steps plus one handover step per link) counted by direction and window, the largest '
  'drop, and change_pct = measure_price_change_pct(first, last) from two priced snapshots up (NULL below: nothing '
  'on the advert ever changed). Read by the property rollup (head = the canonical advert) and '
  'broker_book_projection (head = each advert of the broker); the broker page reads the book (772).';

do $post$
begin
  if to_regclass('public.broker_listings_public') is not null then
    raise exception '772 did not land: broker_listings_public still exists';
  end if;
end
$post$;

commit;

reset lock_timeout;
