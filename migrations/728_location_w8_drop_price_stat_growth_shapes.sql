-- 728_location_w8_drop_price_stat_growth_shapes.sql -- W8-3 (#2362), default D5: the growth-shape RPC goes.
-- price_stat_growth_shapes(bigint) (migration 415) drew a dataset's towns from admin_boundaries_public; it was the SPA's
-- fallback for a dataset whose choropleth was empty, and price_stat_choropleth (727) holds the same towns. The SPA of
-- this PR no longer calls it (frontend/src/lib/priceStats.ts), and it is admin_boundaries' last function reader.
-- DESTRUCTIVE (DROP FUNCTION; a body, no data). Apply AFTER the deploy: the deployed SPA calls it until then.
-- Idempotent.
-- VERIFY: select to_regprocedure('public.price_stat_growth_shapes(bigint)');  -- NULL

drop function if exists price_stat_growth_shapes(bigint);
