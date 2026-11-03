-- 723_location_w8_price_stats_obec_key.sql -- W8-2 (#2362): the price-stat rows are keyed by RÚIAN town code.
-- Observations and no-data markers get obec_id from price_stat_localities (one town per key, 0 orphans, 592,805
-- observations = 592,805 new keys on 11-02); the 3 primary keys move onto obec_id; the 2 FKs into
-- price_stat_localities and the metrics index the new key duplicates go; the entity columns become nullable (W8-4
-- drops them). The 3 public views lose the entity columns and take locality_name from the mirror; every other
-- column stays, so price_stat_growth/series/growth_shapes read them unchanged. Grants: authenticated SELECT only.
-- DESTRUCTIVE. First: gh workflow run backup_before_drop.yml -f label=w8-2-price-stats -f targets="price_stat_observations
-- price_stat_city_metrics price_stat_locality_no_data price_stat_localities", receipt read back; gh workflow disable
-- scrape_price_stats.yml with no run in flight (the deployed scraper writes by the old key and fails here); 722 and
-- its parity. Then apply_migration.yml BEFORE the merge: the deployed API and SPA read only columns this keeps.
-- One transaction: a lock_timeout trip rolls it all back; rerun as is.
-- VERIFY: select count(*), count(distinct (dataset_id, obec_id, category_type_cb, year, month))
--           from price_stat_observations;  -- equal, 592,805 plus rows fetched since 11-02

begin;
set local lock_timeout = '5s';

alter table price_stat_observations add column if not exists obec_id bigint;
alter table price_stat_locality_no_data add column if not exists obec_id bigint;

update price_stat_observations o set obec_id = l.obec_id
  from price_stat_localities l
 where o.obec_id is null and l.entity_type = o.entity_type and l.entity_id = o.entity_id;
update price_stat_locality_no_data nd set obec_id = l.obec_id
  from price_stat_localities l
 where nd.obec_id is null and l.entity_type = nd.entity_type and l.entity_id = nd.entity_id;

-- The old primary key goes before its columns can drop NOT NULL (one ALTER runs its drops in the order written).
alter table price_stat_observations
  drop constraint if exists price_stat_observations_entity_type_entity_id_fkey,
  drop constraint if exists price_stat_observations_pkey,
  alter column entity_type drop not null,
  alter column entity_id drop not null,
  alter column obec_id set not null,
  add constraint price_stat_observations_pkey primary key (dataset_id, obec_id, category_type_cb, year, month);
alter table price_stat_locality_no_data
  drop constraint if exists price_stat_locality_no_data_pkey,
  alter column entity_type drop not null,
  alter column entity_id drop not null,
  alter column obec_id set not null,
  add constraint price_stat_locality_no_data_pkey primary key (dataset_id, obec_id);
alter table price_stat_city_metrics
  drop constraint if exists price_stat_city_metrics_entity_type_entity_id_fkey,
  drop constraint if exists price_stat_city_metrics_pkey,
  alter column entity_type drop not null,
  alter column entity_id drop not null,
  alter column obec_id set not null,
  add constraint price_stat_city_metrics_pkey primary key (dataset_id, obec_id);
drop index if exists price_stat_city_metrics_obec_idx;  -- the new primary key's twin

drop view if exists price_stat_observations_public;
create view price_stat_observations_public as
  select o.dataset_id, n.name as locality_name, o.obec_id, o.category_type_cb, o.year, o.month, o.price,
         o.active_count, o.new_count, o.deleted_count
    from price_stat_observations o
    left join ruian_place_names_public n on n.level = 'obec' and n.code = o.obec_id;

drop view if exists price_stat_city_metrics_public;
create view price_stat_city_metrics_public as
  select m.dataset_id, n.name as locality_name, m.obec_id, m.window_years,
         m.sale_latest_price, m.sale_latest_ym, m.sale_cagr_pct, m.sale_months, m.sale_min_active,
         m.rent_latest_price, m.rent_latest_ym, m.rent_cagr_pct, m.rent_months, m.rent_min_active,
         m.gross_yield_pct, m.computed_at
    from price_stat_city_metrics m
    left join ruian_place_names_public n on n.level = 'obec' and n.code = m.obec_id;

drop view if exists price_stat_no_data_public;
create view price_stat_no_data_public as
  select nd.dataset_id, nd.obec_id, n.name as locality_name
    from price_stat_locality_no_data nd
    left join ruian_place_names_public n on n.level = 'obec' and n.code = nd.obec_id;

revoke all on price_stat_observations_public from public, anon, authenticated;
grant select on price_stat_observations_public to authenticated;
revoke all on price_stat_city_metrics_public from public, anon, authenticated;
grant select on price_stat_city_metrics_public to authenticated;
revoke all on price_stat_no_data_public from public, anon, authenticated;
grant select on price_stat_no_data_public to authenticated;

commit;
