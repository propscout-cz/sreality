# Price-stats datasets (sreality `ceny-nemovitosti`)

A second, independent data domain alongside the listing scraper: the **aggregate
market statistics** sreality publishes at `/ceny-nemovitosti`. Not individual
listings — per-locality monthly time series of average price/m², listing
duration, active/new offer counts, and views. We use it to build named
**datasets** (one filter set each, e.g. "byty / velmi dobrý / panel / osobní /
30–80 m²"), join them to our geo at the municipality (obec) level, and compute
rental growth, sale-price growth, and gross yield — surfaced as analyses and as
two new map heat layers.

## Data source — it's a clean JSON API, not a DOM scrape

The old uploaded scraper (formerly `scraper/inspiration_sraper_del_me/`, kept
only as inspiration and since removed) drove a headless browser, clicking the
date-picker month-by-month — ~3,770 click-tasks over 25–35 h, fragile to CSS
changes. The Next.js rebuild exposes the data directly:

```
GET https://www.sreality.cz/api/v1/estate_prices
    ?category_main_cb=1        # int  — Apartments=1 Houses=2 Land=3 Commercial=4 Other=5
    &category_type_cb=1        # int  — Sale=1 Lease=2 Auction=3 Shares=4
    &building_condition=1      # str  — VeryGood=1 Good=2 Poor=3 UnderConstruction=4 Project=5
                               #         NewBuilding=6 ToBeDemolished=7 ForRenovation=8 Renovated=9 InRenovation=10
    &building_type=5           # str  — Panel=5 Brick=2 Other=10   (NB: stats map, not the search facet Panel=1)
    &ownership=1               # str  — Personal=1 Cooperative=2 Municipal=3
    &usable_area_from=50&usable_area_to=80   # int m²
    &entity_id=3412&entity_type=muni         # the town's sreality key, from ruian_translation
    &distance=0                # int km ("v okolí")
    &default_from=2015-01&default_to=2026-06 # YYYY-MM window
```

Response (trimmed):

```json
{"result":{
  "avg_price_per_area":124140,            // Kč/m² (rent = Kč/m²/month for Lease)
  "avg_published_days_overall":13,
  "avg_views_per_day":299,
  "advert_count":1, "new_advert_count":1,
  "dev_price_by_month":[{"year":2025,"month":12,"price":124140}, ...],
  "dev_count_advert_by_month":[{"year":2025,"month":12,"active":1,"new":1,"deleted":0}, ...],
  "previous_range":null                    // pagination cursor for older months
}}
```

`dev_price_by_month` / `dev_count_advert_by_month` are **monthly series** — one
call returns the history. `avg_published_days_overall` / `avg_views_per_day` are
scalar for the requested window only (monthly versions need per-month calls;
out of scope for v1, which needs only price + counts).

Town keys (location W8-2, #2362; rulings Q10a-d): a town is fetched under its
sreality municipality id, read from `ruian_translation` (migration 722; source
`sreality_municipality`, the key, RÚIAN level and code, how it was matched, when
last confirmed). The 6,258 rows the old resolver cached were copied in
(`boundary_spatial_join` / `suggest_pip`). A town has one fetched key: the smallest
non-ad key, and a key matched from ads (`ad_bound_obec`) only when it is the town's
only row (`FETCH_KEYS_SQL` in `scraper/price_stats_db.py`).

Each run except `--dry-run` first confirms or adds keys from active sreality ads
(`listings.raw_json` `locality.municipality_id` against the ad's bound town,
`CONFIRM_TRANSLATION_SQL`). A stored row stands: an ad naming another town never
moves or confirms it; a new id whose ads name one town is added; a new id whose ads
name several is not, until they name one. A row stays when its ads end. Run log only:
`TRANSLATION confirmed=N added=M`, `TRANSLATION added key=… obec=…`, and
`WARNING TRANSLATION disagree key=… stored=… ad=… ads=n`. A correction to a stored
row is by hand. Nothing resolves a town by name, suggest or point in polygon any more.

## Auth — login required (the one wrinkle)

`estate_prices` returns **HTTP 401** without a logged-in Seznam session
(verified live; `localities/suggest` and the listing `estates/search` API are
both open). Approach (operator-chosen): **Playwright login in CI** mints a fresh
session cookie each run; all data fetching is then pure `requests`. A manual
`SREALITY_SESSION_COOKIE` env var short-circuits the browser when set (fast path
for validation / cookie reuse).

Required CI secrets: `SREALITY_LOGIN_EMAIL`, `SREALITY_LOGIN_PASSWORD`.

## Schema (migrations 144–145)

- `price_stat_datasets` — one row per named filter set. Columns mirror the API
  filter params (`category_main_cb`, `building_condition`, `building_type`,
  `ownership`, `usable_area_from/to`, `distance`) + `name`, `slug`, `description`,
  `is_active`, `created_at/by`. A dataset always covers **both** prodej (1) and
  pronájem (2); category_type is not stored on the dataset.
- `price_stat_localities` — the old resolved-entity cache, read by nothing since
  migration 723; dropped in W8-4 with the entity columns.
- `price_stat_observations` — latest-wins fact table, keyed by RÚIAN town code
  (migration 723): PK `(dataset_id, obec_id, category_type_cb, year, month)`. Columns:
  `price`, `advert_count`, `active_count`, `new_count`, `deleted_count`,
  `run_id`, `fetched_at`. Append-only history lives in `price_stat_runs` join +
  is acceptable to keep latest-wins here (monthly aggregates rarely revise).
- `price_stat_runs` — one row per ingestion run (dataset coverage, counts,
  status), like `scrape_runs`.
- `price_stat_locality_no_data` — per (dataset, obec) "checked, no data" marker.
- `price_stat_city_metrics` — **precomputed** per (dataset, obec) derived metrics
  (sale_cagr, rent_cagr, gross_yield, latest prices, window, advert-count
  sufficiency flags), recomputed at end of each run. The map layer + analysis
  tab read this, never compute live (anon 3s statement_timeout — see memory).
- Public views: `price_stat_datasets_public`, `price_stat_city_metrics_public`,
  `price_stat_no_data_public` and `price_stat_observations_public` (the per-town
  series drill-down), keyed by `obec_id`, `locality_name` from the mirror
  (`ruian_place_names_public`). Owner rights, `authenticated` SELECT only.
  The API's drill-down is `GET /price-stats/datasets/{dataset_id}/cities/{obec_id}/series`.

## Analyses (`toolkit/price_stats.py`)

Facts + provenance, standard envelope. Growth = **CAGR over a chosen window**:
`(end/start)**(1/years) - 1` on `dev_price_by_month`. Gross yield is per-m² so it
cancels cleanly: `gross_yield ≈ 12 × rent_per_m² / sale_per_m²`. Thin months
(`advert_count` below a floor) are flagged so the UI can suppress noise — this is
the real cause of the "values jumping 50%" the old scraper hit.

## Surfaces

- Writes (create/edit dataset, trigger run) → FastAPI (`api/price_stats.py`).
- Reads → public views/RPCs.
- Frontend `/datasets` route: dataset picker, per-city analysis table
  (sale CAGR / rent CAGR / gross yield), and a MapLibre map with two obec
  choropleth layers (rent-growth p.a., sale-growth p.a.) over
  `price_stat_choropleth_public`, each town drawn by its RÚIAN render polygon
  (`ruian_unit_render`, migration 727); the city picker is the register's
  kraj/okres/obec tree with `obec_population` (`price_stat_obce_picker_public`,
  migration 725).
- Ingestion → `.github/workflows/scrape_price_stats.yml` (manual + weekly),
  walking ONE queue of (dataset, obec) stalest first across the active datasets
  (never-scraped first) × {prodej, pronájem} until its time budget; a run that
  shrank the never-scraped set chains once more on the job's own token (#2013).
