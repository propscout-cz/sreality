-- 723_location_w8_price_stats_obec_key.sql -- W8-2 (#2362): the price-stat rows are keyed by RÚIAN town code.
-- Observations, no-data markers and metrics carry obec_id, backfilled from price_stat_localities (one town per key,
-- 0 orphans, 592,805 observations giving 592,805 distinct new keys on 11-02); the primary keys move onto it, the 2
-- foreign keys into price_stat_localities go, so does the metrics index the new key duplicates, and the entity
-- columns become nullable (dropped in W8-4 with the table). The three public views lose the entity columns and take locality_name from the mirror
-- (ruian_place_names_public); obec_id and every other column stay, so price_stat_growth, price_stat_series and
-- price_stat_growth_shapes read them unchanged. Grants as before: authenticated SELECT, anon nothing, owner rights.
-- DESTRUCTIVE (PK swaps, FK drops). Before applying:
--   1. gh workflow run backup_before_drop.yml -f label=w8-2-price-stats -f targets="price_stat_observations price_stat_city_metrics price_stat_locality_no_data price_stat_localities"
--      and read the receipt back;
--   2. gh workflow disable scrape_price_stats.yml, and confirm no run is in flight: the deployed scraper writes by
--      the old key and fails against this schema;
--   3. apply 722 and record the parity checks.
-- Then apply through apply_migration.yml BEFORE the merge. The deployed API and SPA keep working until the deploy:
-- their reads name only columns this keeps. One transaction: a lock_timeout trip rolls it all back; rerun as is.
-- VERIFY: select count(*), count(distinct (dataset_id, obec_id, category_type_cb, year, month))
--           from price_stat_observations;  -- 592,805 twice (plus rows fetched since 11-02)

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
