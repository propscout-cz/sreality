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


### map-2: reference overlays — metro, flood zones, kraje (SHIPPED 2026-10-15: #1808, #1810, #1813; cleanup #1809)
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
- **Cleanup #1809 (MERGED 2026-10-15, migration 601 applied):** the MF rent choropleth onto the same path (`rent_map`
  section with VK variants; `fetch_rent_map.yml` re-renders), then migration 601 drops `rent_map_choropleth_public`,
  `rent_map_kraje_public` and the `rent_map_choropleth` matview. Named follow-ups: curated-city
  polygons and price-growth obec shapes stay on GeoJSON (dynamic values); the boundary ingest could
  move onto the same ArcGIS reader (ČÚZK RÚIAN REST) and drop the `geo` extra.
- **Live since 2026-10-15** (first ingest from `main`): metro 22 tiles, flood 2,497 tiles (3,995 zones → 114,921
  pieces, 43 MB, ~31 min for the section), kraje 83, rent_map 2,964 (7,630 places, 45 s). Two production fixes on
  the way in: the register's Q500 layer spells its fields in mixed case (#1810, fields matched by case) and a
  nationwide per-band `ST_UnaryUnion` ran past the 30-minute statement budget (#1813, per-zone overlap cutting).
  Open follow-up: two expanded chip panels crowd the split-view map (collapse legends by default?).
- **Stations in service (2026-10-16, operator ruling: station proximity must be readable):** a
  fourth reader on the `metro` layer, IPR's named `ReferencedServices/DOP_MetroZarizeni_b`
  (`DRUH=1`, native S-JTSK), class = the line letter so each of the 61 dots wears its line's colour
  (transfer stations once per line); no SPA change, no migration, re-ingest `--sections metro`.

### map-3: count marks — one count rule on world tiles (built 2026-10-15)
- **Why:** the count discs hid the basemap on any dense cohort (232 discs at a 55 px median spacing
  over Prague; 439's 20 × 13 viewport grid painted with the cluster bubble ramp, never merged, and
  tied to the viewer: a 100 px pan moved a place's count 922 → 1 262). Spec + the six rulings:
  `docs/design/map-counts.md`.
- **W1 (done, PR #1812):** ONE count rule, `frontend/src/lib/mapCounts.ts`: points or server cells
  are grouped by world tile at `round(zoom) + 2` (90 to 181 px pitch); a tile holding one known
  property is that pin, any other tile its exact count in a dark pill at the tile centre, one symbol
  layer, never dropped, never overlapping. Deleted: supercluster, both layer pairs, their click and
  hover paths, three zoom constants, the cell helpers, the off-grid bucket, the compact formatters,
  the point read's dead 50 000 limit. A click on a mark re-scopes the list.
- **W2 (done, migration 602):** `browse_map_cells` keeps 598's signature and predicate byte for byte
  and answers in world tiles: one index-only pass at level 18, coarsened in memory to the finest
  level with ≤ 1 024 occupied tiles; `{total, clustered, level, cells[{lat, lng, n}]}` with tile
  centres. Rails: `tests/test_602_*`, the rewritten `tests/test_browse_map_cells_live.py`
  (fixture parity, bounded-and-finest, one tile per row, pan-stable interiors, children sum to
  their parent), `tests/fixtures/map_tile_lattice.json` read by pytest AND vitest.
- **W4 (done 2026-10-16, rulings 7–8):** pins up to a user-set limit at any zoom (default 200,
  range 20–2 000; the input above the map, URL `pins=`), which IS the server's point budget (one
  number end to end, in the map query key); price labels a tick box (URL `labels=0`). Deleted the
  zoom-14 pin rule, the browser-side grouping of points and `MAP_POINT_BUDGET`.
- **Named follow-ups:** town- and quarter-centroid stacks (up to 1 369 rows on one point) sit in one
  tile (a location-precision concern); `DatasetMap.tsx` interpolates `obec_name` into popup HTML
  unescaped; four `MAP_COLS` columns are never read and commercial popups show "—" for `subtype`.
