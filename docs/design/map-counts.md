# Browse map counts — design and build spec

Status: PROPOSED (2026-10-15), awaiting the operator's rulings in § Rulings needed. Once ruled, this
document is the north star for the sprint: every decision below is taken once and not re-litigated
inside a wave. Investigation: 14 Opus agents (four read-only readers, three independent designs,
three judges, one synthesis, three adversarial verifiers); every number below was measured on
production (SELECT-only) or re-read in code at `main` be69959d unless marked "estimated".

## The defect, in plain words

The operator's screenshot (Prague, zoom ≈ 12.9, split view, map ≈ 1350 × 1250 px) shows 232 dark
green discs. Each disc is 28 to 64 px wide at 35 to 90 % opacity. Their centres sit a median 55 px
apart, so many discs are wider than the gap between them; 78 of the 232 touch a neighbour, and the
central part of the map is 37 % covered (94 % at phone width). The streets, districts and the river
are gone.

Why it happens:

1. **Two mechanisms draw the same disc.** At 2 000 mappable properties or fewer, MapLibre's built-in
   browser clustering ("supercluster") merges nearby pins into bubbles and keeps them at least
   48 px apart (`ListingMap.tsx` source `listings`, layers `clusters` + `cluster-count`). Above
   2 000, the server function `browse_map_cells` (migration 439, body now in 598) returns a grid of
   20 × 13 cells laid over the current map window, and the SPA paints every cell with a copy of the
   bubble paint (layers `cells` + `cell-count`). The grid never merges, so on a dense cohort every
   cell is populated and the discs tile the map. The code comment "a server cell and a client
   cluster are visually the same object" was true of the paint only.
2. **The "64-px cell rule" of migration 439 does not exist in the code.** One twentieth of the window
   is 64 px only on a 1280 px wide map; on the operator's split map a cell is 67 × 96 px, on a phone
   17 px. The disc ramp (14 to 32 px radius) was tuned for sparse clusters, not for a lattice.
3. **The darkness scale stops at 200 but cells hold up to ≈ 1 500**, so 95.7 % of the properties sit in
   the two darkest shades and the colour carries nothing.
4. **The grid belongs to the viewer, not to the ground.** It starts at the window's corner, so the
   number a place shows changes when the operator pans, zooms, or resizes the split. Measured at
   Náměstí Míru: a 100 px pan moved the count from 922 to 1 262. Statisticians call this the
   *modifiable areal unit problem* (MAUP): the figure reflects where the edges fell.
5. **Clicking a disc does not re-scope the list.** The click eases the map two zoom levels in, but
   the URL guard ignores programmatic moves, so the cohort stays and the old discs merely spread.

## North star

**One count rule, one mark, bounded by construction.** Every count on the Browse map belongs to a
fixed square of ground (a world tile), is computed by one rule at every cohort size, and is drawn
as one mark that is never wider than half its tile, so the basemap stays readable by construction
rather than by tuning.

Principles that follow:

- **Points, not areas.** The cohort is drawn as pins and count marks. Area fills stay with the
  context layers (cities, rent map, growth, metro, flood, kraje); a fifth wash would fight them and
  repeat the 2026-09-22 lesson (overlapping area marks make the map unusable).
- **One mark per tile, at the tile centre.** Marks cannot overlap each other, so none is ever hidden
  and the numbers on screen always add up to the pill's total.
- **One rule, one formatter, one layer.** Supercluster, the second layer pair, the second click and
  hover path, the three zoom constants, the three number formatters and the dead response fields go.
- **Exact and reproducible.** No sampling (the 439 lesson stands), the sum of counts equals the
  total, the same tile shows the same number for every viewer at a zoom, and four child tiles sum to
  their parent.
- **Every wave deletes more than it adds.**

## What the operator will see

A *world tile* is the fixed square grid every web map uses: level L cuts the world into 2^L × 2^L
squares. MapLibre's world is 512 px wide at zoom 0, so a level-L tile is 512 × 2^(zoom − L) px on
screen. The display level is `round(zoom) + 2`, which keeps the tile pitch between 90 and 181 px:
on a 1350 × 1250 px map that is 52 to 210 marks, on a 390 px phone width 2 to 4 columns.

| Zoom band | Tile on the ground (lat 50) | Drawn |
|---|---|---|
| Country z5–7 | 50 to 12 km | count marks |
| Region z8–10 | 6.3 to 1.6 km | count marks |
| City z11–13 | 780 to 200 m | count marks (Prague: ≈ 100 to 210 marks) |
| District z14–15 | 100 to 50 m | pins with prices when the view holds ≤ 2 000 properties, else count marks (the Prague rental cohort is still counts here) |
| Street z16+ | pins | pins (Prague rental cohort drops under 2 000 at ≈ z16) |

**Small cohorts (≤ 2 000: collections, brokers, narrow filters).** Below zoom 14 a tile holding one
property shows that property's pin (popup, hover to its card); a tile holding two or more shows a
count mark whose hover lights its members' cards. From zoom 14 every property is a pin, exactly as
today's `clusterMaxZoom: 13` behaves.

**The mark** (one symbol layer, `counts`; replaces `clusters`, `cluster-count`, `cells`, `cell-count`):

| Property | Value |
|---|---|
| shape | a rounded pill drawn as a stretchable 9-slice image (`map.addImage` with `stretchX`/`stretchY`/`content`), `icon-text-fit: both`, padding 3 / 6 px, so the pill is exactly as wide as its number |
| fill | `#2f5750` (the price-label ink), opaque; a 1 px white outer edge for separation |
| text | `['number-format', n, {locale: 'cs-CZ'}]` → "1 465", "32 801": the header pill's format, white, Noto Sans Bold 11 (the price labels' font) |
| size | 17 px tall; 22 px wide for one digit, 44 px for "1 465", 50 px for "32 801" |
| placement | tile centre; `icon-allow-overlap` and `text-allow-overlap` true (our marks are never dropped: with one mark per tile and a 90 px minimum pitch they cannot collide); `ignore-placement` false, so basemap labels still avoid them |
| coverage | worst instant (90 px pitch, every tile a 4-digit count) 9 %; typical 4 to 6 %; today 37 to 94 % |
| click | eases two zoom levels in on the tile and passes the click's `originalEvent` through `easeTo`'s event data, so the existing URL guard treats it as a user move and the list and stats re-scope |
| hover (small lane) | the member ids travel on the feature; the cards light as a cluster's leaves do today |

White on `#2f5750` has a contrast ratio above 8:1. A legend is unnecessary: every mark prints its
own number and no mark is ever hidden.

**Also changed, deliberately:** the header pill loses "· grouped into N cells" and "· N outside the
mapped extent" (the server's grid is an implementation detail and the off-grid bucket no longer
exists: every row is in exactly one tile); the "Ceny" hint loses "(od úrovně 14)" (it was false in
Prague); isolated pins get their price label from zoom 14 instead of 13 (one pin zoom constant).

## Architecture after the sprint

**Client: one `groupByTile`.** `frontend/src/lib/mapCounts.ts` holds `tileOf(lng, lat, level)`
(MapLibre's own Mercator formula, latitude clamped to ±85.05°), `countLevel(zoom)`, `groupByTile`
(sums `n`, carries member ids in the small lane) and the pill image factory. Whatever the server
returns, points or cells, is grouped the same way at `min(countLevel(zoom), server level)`;
re-grouping listens to the `zoom` event and calls `setData` only when the level changes, so no
refetch is needed to zoom and marks never crowd mid-gesture. A user pan or zoom still refetches
through the URL bbox exactly as today (the viewport is a filter; nothing changes in react-query).

**Server: `browse_map_cells` keeps its signature** (74 parameters including `point_budget`; the
map lane is still chosen from the filters alone; the query key stays `['map', filters]`). Only the
body changes, in migration 602 (601 is held by open PR #1809):

- the cohort predicate stays byte-identical to `browse_stats_properties` (a static test pins it);
- one index-only scan over the cover index `(category_main, category_type, lat, lng)` groups the
  cohort into level-18 tiles (≈ 100 m at lat 50) reading only `lat` and `lng`;
- if the total is ≤ `point_budget` the function returns `cells: null` and the pin lane runs as today
  (strict threshold; an empty cohort is not a small one);
- otherwise a cascade of chained CTEs coarsens level by level (`x >> 1`, `y >> 1`, `sum(n)`; each
  level groups the previous level's groups, never the base rows again, so no sort spills) and the
  finest level with at most 1 024 occupied tiles is returned. Bounded by construction, no LIMIT, no
  sampling. Measured shape: default rental cohort nationwide 175 839 rows → 36 186 level-18 tiles,
  cascade ≈ 260 ms; the worst cohort (no filters, 820 676 rows) ≈ 1.0 to 1.5 s against 657 ms today,
  far under the 8 s statement limit; the W2 receipt records both with `temp written = 0`;
- response `{total, clustered, level, cells: [{lat, lng, n}]}` with `lat`/`lng` the tile centre
  rounded to 5 decimals (≈ 1 m): 4 keys instead of 10, ≈ 20 KB raw for 1 024 cells against 11 KB
  today. `off_grid`, `cell_lat_deg`, `cell_lng_deg` and the four `grid_*` keys go; nothing read them
  but the pill's off-grid clause.

Because the client groups by `tileOf` on whatever `lat`/`lng` it receives, the old body and the new
SPA work together (marks at the old grid's mean positions) and so do the new body and the old SPA.
There is no deploy window with a broken map, and no "reads both shapes" variant.

**Invariants** (each pinned by a test): sum of cells = total; every row in exactly one tile,
including the 23 rows south of the old CZ box; interior tiles identical under pan and resize;
children sum to their parent; the SQL and TypeScript tile formulas agree on a shared fixture
(`tests/fixtures/map_tile_lattice.json`, read by pytest and vitest); the `g` CTE still projects only
`l.lat`, `l.lng` (index-only scan, Heap Fetches 0).

**Known limits, stated not hidden.** The count is exact; the *position* is exact only for rows
located to a street or address. In the Prague rental cohort 27 % of rows are placed only at their
town or quarter centroid, and 46 identical-coordinate stacks hold 50 or more rows each (the largest
1 369), so the tile holding a centroid carries that town's rows. That is a location-data fact, not a
map fact, and is filed as a follow-up issue. Under the default status "any", roughly three quarters
of the marks are delisted history (the 2026-10-13 any-ad ruling); the mark does not split them.

## Subtraction ledger (estimated; each PR body carries the real diff count)

| Where | Deleted | Added |
|---|---|---|
| `ListingMap.tsx` | `listings` cluster config; layers `clusters`, `cluster-count`, `cells`, `cell-count` and their two paint copies; `map-cells` source; cluster click (`getClusterExpansionZoom`) + hover (`getClusterLeaves`); cell click + hover; `abbreviateCount`, `toCellFeatureCollection`, `CellFeatureProps`, `CellFC`; `CLUSTER_MAX_ZOOM`, `PRICE_LABEL_MIN_ZOOM`, `PIN_LABEL_VISIBLE_ZOOM` + the 15-line comment; the cells data effect and second fit path; the off-grid and cell-count pill clauses | one `counts` source + one symbol layer; one click/hover pair; `PIN_ZOOM` |
| `lib/mapCounts.ts` (new) | | `tileOf`, `countLevel`, `groupByTile`, the pill image, the layer spec (≈ 110 lines) |
| `lib/queries.ts`, `BrowseExperience.tsx` | `offGrid` field, prop and plumbing; `MapGrid.off_grid` | `MapGrid.level` |
| `lib/format.ts` | `czNumberCompact`, `fmtCountCompact` (zero consumers) | |
| SQL body (migration 602) | CZ box constants, 20 × 13 constants, extent fallback, edge clamps, seven output keys | level-18 grouping + cascade + level pick (≈ +30 inside the body) |
| Tests | the grid-bound and off-grid live tests; `_GRID_LOCALS` in the plan test | the invariants above (vitest ≈ +80, pytest ≈ ±0 net) |

Estimated production net: about −100 lines of TypeScript, +30 of SQL body, one append-only
migration of ≈ 330 lines in history (rule 1). Counters 2 → 1, count layers 4 → 1, paint copies
2 → 1, number formatters 3 → 1, click and hover paths 2 → 1, zoom constants 3 → 1, fit paths 2 → 1,
grid extents 3 → 0, response keys 10 → 4, flags 0, dependencies 0.

**Priced and declined:** a tinted-square density wash behind the marks (a "choropleth", areas
coloured by value) would serve the same moment, be a fifth area fill over four, and cost a second
layer, a legend and a control (≈ +60 lines); not offered as a setting. A number with only a halo and
no pill (the most subtractive mark, one fewer image) reads like a basemap label (road numbers and
house numbers are halo'd digits on this basemap) and is offered as an alternative in the rulings.

## Waves and deploy order

Lands after PR #1809 (open, green, rewrites 314 lines of `ListingMap.tsx`); if #1809 is still open
at "go", W1 branches from its head and is retargeted to `main` before that branch is deleted.

**W1 `feature/map-counts` (frontend only, no database change).** `mapCounts.ts`, the `counts`
layer, the deletions above, the click that re-scopes, the pill wording, `PIN_ZOOM`, the dead
formatters, and the stale comments (`ListingMap.tsx` "visually the same object", the city-label
placement note, the "50 000" and "nothing labels at z13" notes; `queries.ts:90` "WebGL with
clustering" and the `MapResult` doc; `uncertaintyCircle.ts:23`; `ListingCards.tsx:194`;
`BrowseExperience.tsx:295`). Verification: vitest (`tileOf` parity with the fixture, totals
conserved, exact nesting, a lone property stays a pin below `PIN_ZOOM`, re-grouping by level
change, pill width ≤ 0.55 × 90 px for a five-digit count); typecheck, lint, build; the local SPA
against production data at Prague z12.0 / z12.5 / z12.9 on a 1350 px and a 700 px split and a
390 px window, a small cohort with lone pins and n = 2 marks, cities + rent + metro layers on, the
no-break space rendering in Noto Sans Bold; before/after screenshots in the PR; a count of surviving
basemap labels (`queryRenderedFeatures` on the basemap's symbol layers) before and after. After
merge: Railway `vite` status, then a read-only production check.

**W2 `feature/map-counts-tiles` (database, tests, docs).** Migration 602 as specified, with a header
that corrects 439's stale claims (the 64-px rule, "why not PostGIS"). Tests: a static
`tests/test_602_*_migration.py` (md5 gate on the live body read at branch time, one transaction, no
DROP or GRANT, the WHERE block byte-identical to 598's map WHERE and to the stats WHERE, receipt
objects); `tests/test_property_ids_excluded_migration.py` and `tests/test_one_place_predicate.py`
get a per-function latest-definition map (stats → 598, cells → 602); the plan test's extractor keeps
anchoring on the `g` CTE; the live tests replace grid-bound and off-grid with bounded-and-finest,
every-row-in-one-tile, pan-stable interiors, parent sums and the lattice fixture; the strict-budget,
id-space and empty-cohort tests stay. The SPA reads `level` (three lines). Docs in the same PR:
`docs/architecture.md` § browse read path (the bounded-result paragraph), `GLOSSARY.md` (count
mark), `roadmap/map-track.md`, the `migrations.yml` step comment + `scripts/generate_workflow_docs.py
--check`; the docs-staleness warning for `frontend/` → `interface-design` and `migrations/` →
`database` is answered in the PR body (no doctrine changes). Deploy: W1 live first; W2 CI green →
dry run then apply 602 from the branch via `apply_migration.yml` → one receipt (md5, response keys,
sum = total, cells ≤ 1 024 at the finest level, EXPLAIN index-only outside the :07–:10 / :37–:40
rebuild windows, worst-cohort timing with `temp written = 0`, payload bytes raw and gzip) → merge →
Railway status → read-only production check (pan a quarter tile, zoom one level, phone width,
Explore area, Explore broker, centre + radius mode, hover on a broker cohort).

**Rollback.** W1: revert the PR (Railway redeploys); the old SPA draws the new body's tile centres
with its discs, which is no worse than today. The body never rolls back in place: if it ever must,
migration 603 restates 598's body, md5-gated on 602.

## Rulings needed (defaults let the sprint run on "go")

1. **Mark style.** Dark pill with a white number (default) · number only with a white halo (lighter;
   reads like a basemap label) · white pill with a dark number (Airbnb style; less contrast against
   the light basemap).
2. **Mark position.** Tile centre (default: never overlaps, every number always visible, a lattice
   look at country zoom) · the members' average position (sits on the town; measured 3 % of
   neighbouring marks touch, and a hidden mark would break "the numbers add up").
3. **Small cohorts are grouped by tiles too** (default yes): supercluster goes; a click zooms two
   levels instead of "to where the cluster splits"; lone properties stay pins; hover to cards stays.
4. **Clicking a count re-scopes the list and stats** to the new view (default yes).
5. **Numbers exact** ("1 465", "32 801"; default) · abbreviated above 10 000 ("33 tis.").
6. **Tile pitch 90 to 181 px** (default: 52 to 210 marks on a laptop split map) · 64 to 128 px
   (100 to 400 marks, denser, more numbers to read).

## Risks and how each is caught

- Count marks displace some basemap labels (they are placed first, as today's discs are). Measured
  in W1's browser check as a before/after label count; the layer order decision is recorded there.
- Lat/lng drift between SQL and TypeScript: the shared fixture is read by both test suites.
- A programmatic fly-to with no bbox in the URL gets the server's coarser level (bounded at 1 024
  tiles nationwide): stated, not hidden; the next user move refetches at the bbox.
- Response size up to ≈ 20 KB raw: measured in the W2 receipt.
- Worst-cohort server time ≈ 1.0 to 1.5 s: measured in the W2 receipt; the cascade shape is pinned.
- Heap fetches for ≈ 3 minutes after each half-hourly matview rebuild: pre-existing, noted.

## Validated vs assumed

Re-read in code: the two copied ramps; `abbreviateCount` vs `point_count_abbreviated`; the URL
guard and the event-less `easeTo`; the price-label paint and font; the dead formatters; `useMapZoom`
flooring on `zoomend`; `test_property_ids_excluded_migration.py` pinning 598 as the latest
definition; PR #1809's footprint; MapLibre 4.7's `icon-text-fit` and stretchable images. Measured
live: cohort sizes, cell and mark counts per zoom, spacing, coverage, pan instability, centroid
stacks, cascade and fan-out timings, index-only plans. Not verified: a browser render of the new
mark (W1's first screenshot is the receipt); gzip payload size; the operator's exact cohort and
bbox (approximated with the Prague rental cohort, 31 to 40 k rows, which reproduces 232 to 246
cells).

Patchwork found and removed by this sprint: the "64-px rule"; "visually the same object"; 439's
"why not PostGIS" rationale; six dead response fields; `off_grid` dead whenever a bbox is set;
`MAP_CAP` dead on this path; the city-label placement comment that states the opposite of the code.

## Follow-ups outside the north star (to file as issues)

- `DatasetMap.tsx:108` interpolates `obec_name` into popup HTML unescaped.
- Four `MAP_COLS` columns are never read; commercial popups show "—" because `subtype` is no longer
  selected.
- Town- and quarter-centroid stacks (up to 1 369 rows on one point) sit in one tile; a location
  precision concern, not a map one.

## Build notes

**W1 (2026-10-15, PR #1812).** Built as specified, rulings 1 to 6 on their defaults; receipt in the
PR. Frontend production ledger +354 / −330 against #1809's head; the grouping, the pill image and
the layer spec live in `lib/mapCounts.ts`. The point read's dead `MAP_CAP` limit went with it. The
header count above the cards is the whole cohort; the map's "X of Y mapped" names the gap (the
properties without coordinates) and the marks always sum to X.

**W2 (2026-10-15, migration 602).** Measured on production before apply (SELECT-only). Default
rental cohort nationwide, 176 023 rows: Index Only Scan, Heap Fetches 0, 36 205 level-18 tiles,
402 ms cold (2 332 blocks) and 223 ms warm; each coarsening level about 21 ms in memory (Batches 1,
no temp). Worst cohort (no filter, 821 264 rows): parallel Index Only Scan, Heap Fetches 0,
133 614 level-18 tiles, 1.37 s cold (11 144 blocks), about 90 ms per coarsening level, so about
2.1 s for a level-11 answer; under the 8 s statement limit, no temp. Live `md5(prosrc)` of 598's
body confirmed `8bf9e3ad…`; 602's own body `e4af9601…`.
