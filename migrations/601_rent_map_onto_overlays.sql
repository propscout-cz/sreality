-- 601_rent_map_onto_overlays.sql
-- The MF rent choropleth ("Cenová mapa nájemného") now draws from the reference-overlay store
-- (migration 600): section `rent_map`, one layer whose four variants are the MF size categories,
-- rendered from an SQL source over admin_boundaries × the latest rent_map_values revision
-- (scripts/ingest_map_overlays.py). api/rent_map.py re-renders it inside every revision ingest
-- and fetch_rent_map.yml after every run. Design: docs/design/map-overlays.md, § Cleanup PR.
-- The three relations that served the old fetch-everything path go:
--
--   rent_map_choropleth_public  the SPA's GeoJSON read: all 7,630 territories, ~10 MB of
--                               geometry, fetched whole on every page load
--   rent_map_kraje_public       the old nested "Kraje" overlay's read (now the `kraje` section)
--   rent_map_choropleth         the matview behind the first; its unique index
--                               rent_map_choropleth_pk goes with it
--
-- and with the matview its derived_artifacts row: api/rent_map.py no longer refreshes it, and
-- tests/test_derived_artifacts_stamping.py fails a registry row that nothing stamps.
-- rent_map_cells (mf_reference()'s input, migration 565) and its row are untouched.
--
-- APPLY ONLY AFTER THE CLEANUP PR DEPLOYS. The SPA stops reading both views in the same PR,
-- and the API stops refreshing the matview: applied earlier, the deployed SPA's rent-map chip
-- breaks and every rent-map ingest of the deployed API fails on the missing matview. Order:
-- merge; Railway rollout confirmed for the API and the SPA; `rent_map` ingested
-- (ingest_map_overlays.yml, sections=rent_map); then this file.
--
-- DESTRUCTIVE BY THE LETTER (DROP, DELETE), so it waits for the operator's go (database
-- skill) — but it destroys no source data: the matview and both views are derivations of
-- rent_map_values and admin_boundaries, which stay, and the registry row is the matview's
-- freshness metadata. Nothing to back up.
--
-- No CASCADE. The live catalog (2026-10-15) shows rent_map_choropleth_public as the matview's
-- only dependent, nothing depending on either view, no function body naming any of the three
-- and no pg_cron job: a dependent added since fails the drop loudly instead of vanishing.
--
-- Idempotent (IF EXISTS; the DELETE matches nothing on a re-run). apply_migration.yml runs
-- `psql -f` in statement autocommit, so the lock timeout is a plain SET.
-- Verify:
--   select to_regclass('public.rent_map_choropleth_public'), to_regclass('public.rent_map_kraje_public'),
--          to_regclass('public.rent_map_choropleth'), to_regclass('public.rent_map_choropleth_pk');
--                                                                               -- all four null
--   select count(*) from public.derived_artifacts where name = 'rent_map_choropleth';      -- 0
--   select count(*) from public.derived_artifacts where name = 'rent_map_cells';           -- 1
--   select fetched_at is not null from public.map_overlay_sections where key = 'rent_map'; -- t

set lock_timeout = '5s';

drop view if exists public.rent_map_choropleth_public;

drop view if exists public.rent_map_kraje_public;

drop materialized view if exists public.rent_map_choropleth;

delete from public.derived_artifacts where name = 'rent_map_choropleth';

do $$
begin
  if to_regclass('public.rent_map_choropleth_public') is not null
     or to_regclass('public.rent_map_kraje_public') is not null
     or to_regclass('public.rent_map_choropleth') is not null
     or exists (select 1 from public.derived_artifacts where name = 'rent_map_choropleth') then
    raise exception 'the old rent-map read surfaces are still present';
  end if;
end
$$;

notify pgrst, 'reload schema';

reset lock_timeout;
