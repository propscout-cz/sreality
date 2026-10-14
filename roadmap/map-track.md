> Track file — part of [ROADMAP.md](../ROADMAP.md). After shipping, edit only this file + its index row.

## Map track (parallel)

Geographic drill-down beyond the existing district facet. Independent
of the analytical and UI phases; runs alongside them.

### map-1: typed locality IDs
- **Part A (done):** inspection of `raw_json.recommendations_data`
  confirmed 100% coverage on `locality_municipality_id`,
  `locality_quarter_id`, and `locality_ward_id` across active
  listings. Cardinality and naming notes captured in commit
  `d663233`.
- **Part B (proposed):** migration 016 promotes those three IDs to
  typed columns, sanitising sreality's `-1` sentinel to `NULL`.
  Parser + scraper write-path landed in commit `d663233`; migration
  is committed in `migrations/016_locality_ids_extended.sql`.
  Confirm-and-mark-done item: verify whether Part B has actually
  been applied to the production database (auto-status block above
  should show migration 016 applied if so) and update this entry
  accordingly.
- **Part C (done):** backfill from `raw_json` for existing rows;
  exposed via `listings_public`.
- **Part D (done):** spatial join scaffolding for ČÚZK / RÚIAN
  polygons. Migration 017 (`admin_boundaries`),
  `scripts/ingest_boundaries.py`,
  `.github/workflows/ingest_boundaries.yml`. Bridge table populated
  by the ingest workflow.


### map-2: reference overlays — metro, flood zones, kraje (building 2026-10-14)
- **One tile path** (spec + rulings: `docs/design/map-overlays.md`; draft PR #1808): migration 600
  (`map_overlay_sections` / `_layers` / `_features` in 3857 / pre-rendered `_tiles` + the render
  function), `scripts/ingest_map_overlays.py` (ONE Python registry; ArcGIS REST + SQL readers;
  datum control point, completeness guards; quarterly `ingest_map_overlays.yml`), two API routes
  (`GET /map-overlays`, `GET /map-overlays/{section}/{z}/{x}/{y}.mvt`), one SPA hook + chip
  control on Browse/Explore, the listing-detail header map and the comparables map; selection
  remembered per browser, Browse URL `layers=` wins.
- **Sources:** IPR Praha Metropolitní plán (A/B/C existing, Metro D proposals, territorial
  reserves incl. the tangential corridor the press calls metro E, named planned stations) and the
  VÚV TGM / MŽP national flood-zone register (Q5/Q20/Q100/Q500 + active zone, fetched in native
  S-JTSK — the server's WGS84 output is ~10 m off). Kraje move off `rent_map_kraje_public`.
- **Cleanup PR (building, stacked on #1808):** the MF rent choropleth onto the same path (`rent_map`
  section with VK variants; `fetch_rent_map.yml` re-renders), then migration 601 drops `rent_map_choropleth_public`,
  `rent_map_kraje_public` and the `rent_map_choropleth` matview. Named follow-ups: curated-city
  polygons and price-growth obec shapes stay on GeoJSON (dynamic values); the boundary ingest could
  move onto the same ArcGIS reader (ČÚZK RÚIAN REST) and drop the `geo` extra.
