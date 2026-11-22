-- properties_public reads the property's OWN storey (#2552 W4, step 2).
--
-- Migration 751 added `properties.floor` / `total_floors` and the rollup fills them with the
-- value most of the property's adverts state (a tie goes to the canonical order). Until now the
-- view took both from the canonical advert alone, so whichever advert was canonical decided:
-- 2,400 of 17,134 multi-advert flats hold adverts that disagree on the storey.
--
-- 636's body with those two columns re-sourced; same columns, same order, same types.
-- APPLY ONLY AFTER a full property sweep has completed since 751's code deployed
-- (derived_artifacts `property_sweep`): before that the columns are NULL on unswept rows.
set lock_timeout = '5s';

create or replace view public.properties_public as
  select
      p.id as property_id,
      p.repr_listing_id as sreality_id,
      p.first_seen_at,
      p.last_seen_at,
      p.is_active,
      p.category_main,
      p.category_type,
      p.current_price_czk as price_czk,
      l.price_unit,
      p.area_m2,
      p.disposition,
      st_y(ll.geom) as lat,
      st_x(ll.geom) as lng,
      p.floor,
      p.total_floors,
      p.has_balcony,
      p.has_parking,
      p.has_lift,
      p.building_type,
      p.condition,
      p.energy_rating,
      p.estate_area,
      p.usable_area,
      p.garden_area,
      p.category_sub_cb,
      p.furnished,
      p.terrace,
      p.cellar,
      p.garage,
      p.parking_lots,
      p.ownership,
      l.broker_name,
      null::text as broker_email,
      null::text as broker_phone,
      measure_tom_days(p.is_active, p.first_seen_at, p.last_seen_at) as tom_days,
      measure_price_per_m2(p.current_price_czk::numeric, p.area_m2, p.category_main, p.category_type) as price_per_m2,
      p.building_condition_level,
      p.apartment_condition_level,
      l.description,
      p.source_count,
      p.price_drop_count,
      p.price_rise_count,
      p.max_price_drop_pct,
      p.stats_computed_at,
      p.source,
      mf.mf_reference_rent_czk,
      mf.mf_gross_yield_pct,
      ll.obec_name as obec,
      p.home_obec_pop,
      p.near_pop_5km,
      p.near_pop_15km,
      p.near_jobs_5km,
      p.near_jobs_15km,
      p.near_youth_5km,
      p.near_youth_15km,
      p.near_overall_5km,
      p.near_overall_15km,
      p.subtype,
      p.last_change_at,
      ll.obec_kod  as obec_id,
      ll.okres_kod as okres_id,
      ll.kraj_kod  as region_id,
      p.price_change_count,
      p.price_change_count_30d,
      p.price_change_count_90d,
      p.price_change_count_365d,
      p.total_price_change_pct,
      mf.mf_reference_rent,
      p.repr_listing_ref_id as listing_id,
      l.source_id_native,
      measure_price_per_m2_basis(p.category_main, p.category_type) as price_per_m2_basis,
      location_display_label(ll.street_name, ll.house_number_cp, ll.house_number_co,
                             ll.obec_name, ll.cast_obce_name, ll.country_code,
                             ll.country_status) as display_label,
      ll.cast_obce_kod as cast_obce_id,
      ll.ulice_kod as ulice_id,
      coalesce(p.all_sources, '{}'::text[]) as all_sources,
      coalesce(p.active_sources, '{}'::text[]) as active_sources,
      p.location_listing_ref_id
  from properties p
       left join listings l on l.id = p.repr_listing_ref_id
       left join listing_location ll on ll.listing_id = p.location_listing_ref_id
       left join lateral public.mf_reference(
           p.category_main, p.category_type, p.disposition, p.area_m2, p.current_price_czk,
           p.condition, p.has_balcony, p.terrace, p.furnished, p.garage, p.has_lift,
           p.building_type, ll.obec_kod, ll.katastr_kod, ll.country_status) mf on true
  where p.status = 'active'::text;

comment on view public.properties_public is
  'One row per active property: the watchdog''s relation and the property page''s. Its place (lat, lng, '
  'obec, display_label, the five RUIAN codes) and the MF reference rent read the property''s LOCATION AD, '
  'location_listing_ref_id, the last column (migration 636); floor and total_floors are the property''s '
  'own, the value most of its ads state (migration 751); listing_id and the remaining ad facts (price_unit, '
  'broker_name, description, source_id_native) are its CANONICAL ad''s.';
