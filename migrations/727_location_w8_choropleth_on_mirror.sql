-- 727_location_w8_choropleth_on_mirror.sql -- W8-3 (#2362): the Datasets growth map draws each town by the RÚIAN
-- mirror's render polygon (ruling Q12b: a unit-keyed dataset borrows its polygon from the mirror at render time).
-- price_stat_choropleth was 145's join of the metrics to admin_boundaries; it is the same eleven columns over
-- ruian_unit_render (725), its name the register's. A matview's query cannot be replaced, so it and the view over it
-- are dropped and re-created, with 145's unique index and today's grants (authenticated SELECT on both; the service
-- role keeps Supabase's default). Its derived_artifacts row (440) is keyed by name and stays; every price-stats run
-- still refreshes it through refresh_matview.
-- DESTRUCTIVE by the letter (DROP VIEW, DROP MATERIALIZED VIEW): a derived artifact, rebuilt here from its sources,
-- nothing to back up. Apply after 725, BEFORE the merge: the deployed SPA reads price_stat_choropleth_public, whose
-- columns this keeps. One transaction (about 1.3 s to build): a lock_timeout trip rolls it all back; rerun as is.
-- Receipt: the row count before = after (9,212 on 11-03), 0 NULL geojson.
-- VERIFY: select count(*), count(*) filter (where geojson is null) from price_stat_choropleth;

begin;
set local lock_timeout = '5s';

drop view if exists price_stat_choropleth_public;
drop materialized view if exists price_stat_choropleth;

create materialized view price_stat_choropleth as
  select m.dataset_id,
         r.code                  as obec_id,
         r.name                  as obec_name,
         st_asgeojson(r.geom, 5) as geojson,
         m.sale_cagr_pct,
         m.rent_cagr_pct,
         m.gross_yield_pct,
         m.sale_latest_price,
         m.rent_latest_price,
         m.sale_min_active,
         m.rent_min_active
    from price_stat_city_metrics m
    join ruian_unit_render r on r.level = 'obec' and r.code = m.obec_id;

create unique index price_stat_choropleth_pk
  on price_stat_choropleth (dataset_id, obec_id);

create view price_stat_choropleth_public as
  select dataset_id, obec_id, obec_name, geojson, sale_cagr_pct, rent_cagr_pct,
         gross_yield_pct, sale_latest_price, rent_latest_price,
         sale_min_active, rent_min_active
    from price_stat_choropleth;

revoke all on price_stat_choropleth from public, anon, authenticated;
grant select on price_stat_choropleth to authenticated;
revoke all on price_stat_choropleth_public from public, anon, authenticated;
grant select on price_stat_choropleth_public to authenticated;

commit;
