# Map reference overlays — design and build spec

Status: BUILDING (2026-10-14). Operator rulings in § Rulings. This document is the north star for
the sprint: every decision below was taken once and is not re-litigated inside a wave.

## North star

**One external reference-overlay path.** One store, one renderer, one registry entry per layer, one
`layers` knob. Every static reference geometry a map draws (today: Prague metro, flood zones,
kraj borders; next: the MF rent choropleth) is ingested by one script, rendered once into vector
tiles, served by one API route and drawn by one frontend hook. Adding a layer is one registry entry
plus one ingest run; no per-layer frontend code, no flags.

Vector tiles (MVT) are the industry standard for drawing large geometry: the map asks for small
square pieces of the picture it is looking at instead of downloading everything. The Labe Q100
flood zone alone has 127k vertices, so the fetch-everything path the rent map uses today cannot
carry flood zones.

Out of scope (named follow-ups): the curated-city polygons and the price-growth obec shapes stay on
their GeoJSON path (their values are dynamic per view); the phone app's static Mapy image; moving
the boundary ingest onto the ArcGIS reader.

## Rulings (operator, 2026-10-14)

1. Flood data = the national register as-is (Q5, Q20, Q100, Q500, active zone), nationwide, with a
   legend that says it is the legal extent and ignores flood defences.
2. The transit-corridor estimation tool stays. The paid-for deletion is the MF rent choropleth
   moving onto this path (cleanup PR), plus two helper dedupes. Condition: a professional would do it
   unprompted and it slows nothing for the user — it does not: the first load becomes a few small
   cached tiles instead of a multi-megabyte download.
3. Layer toggles on every MapLibre surface (Browse/Explore, listing-detail header map, comparables
   map), the selection remembered per browser; a Browse URL carrying `layers=` wins.
4. Metro = lines (A/B/C existing, D proposal, territorial reserves including the tangential corridor
   labelled exactly as the plan labels it) plus the named planned stations. Existing stations are not
   drawn (the basemap labels them; the plan's points carry no names).

## Sources (verified live 2026-10-14)

### Metro — IPR Praha, Metropolitní plán (in force 2026-09-01), CC BY, "datový podklad © IPR Praha"

Hosted ArcGIS FeatureServer, stored in EPSG:3857; fetch with `outSR=3857&f=geojson` (no server
reprojection). Base `https://mp.iprpraha.cz/arcgis/rest/services/Hosted/`:

- `MPP_CUR_MPP_STAV_600_DI_TRASY_L/FeatureServer/0` where `kapitola=621` → 3 LineStrings,
  `parametr` A / B / C, `stav=1` (existing), 1.2–1.6k vertices each. Class = the letter.
- `MPP_CUR_MPP_600_DI_TRASY_L/FeatureServer/0` where `kapitola=621` → 15 features. `stav=2`
  (návrh): the Metro D segments and the B extension to Depo Zličín; `stav=3` (územní rezerva): A
  Motol–Zličín, A Želivského–Zahradní Město, C Háje–Uhříněves, D Hlavní nádraží–NNŽ, D Nové
  Dvory–Poliklinika Modřany, and 4 × "Výhledové tangenciální spojení kolejovou dopravou" (an open
  arc Smíchov → Pankrác → Želivského → Vysočanská → Terminál Sever; the press calls it metro E; the
  plan names neither the letter nor the technology). Class: `stav=2` → the line letter parsed from
  `nazev` ("Trasa metra D …" → `D`; "Trasa metra B …" → `B` with `dash=true`); `stav=3` → `reserve`.
- `MPP_CUR_MPP_600_DI_ZARIZENI_B/FeatureServer/0` where `kapitola=621 and druh=2` → planned
  stations, named ("Stanice metra Olbrachtova"); class `station`, `label` = name without the
  "Stanice metra " prefix. Points only; vestibules (`druh=3`) and depots (`druh=1`) are skipped.
- Fields: kapitola, druh, id, nazev, charakter (1 surface / 2 tunnel), stav (1 stav / 2 návrh /
  3 územní rezerva / 4 ke zrušení), parametr. Rows with `stav=4` are skipped.

### Flood zones — VÚV TGM for the Ministry of the Environment (ISVS VODA), CC BY 4.0

ArcGIS MapServer 10.91 `https://ags2.vuv.cz/arcgis/rest/services/isvs_voda/isvs_voda/MapServer`,
native CRS EPSG:5514 (S-JTSK). **Fetch in the native CRS** (`outSR=5514&f=geojson`) and let PostGIS
transform: the server's own WGS84 output applies a 3-parameter datum shift that lands every polygon
about 10 m NNE of its true position (measured against PID stop coordinates). Layers (count):
15 `ZaplavUzemi_Q5` (964), 14 `ZaplavUzemi_Q20` (966), 13 `ZaplavUzemi_Q100` (924),
12 `ZaplavUzemi_Q500` (266), 11 `ZaplavUzemi_AktivniZony` (875). Fields: TOK_ID, NAZ_TOK (the
watercourse, kept as `name`), IDVT, TOKREC_ID, REC_NAZ, MAX_UTOKJN. `maxRecordCount` 2000,
`supportsPagination`. Reference date 31.8.2025 (published 30.10.2025; revised quarterly; the ISO
record `https://heis.vuv.cz/xmicka/record/xml/CZ-VUV-MD-ZaplavUzemi` carries the revision date in
`gmd:dateStamp`/`gmd:CI_Date`). The service is the publisher's generalisation (2 m for Q bands, 5 m
for active zones) of the binding ZIP; polygons are OGC-invalid in places (`ST_MakeValid` first).
Facts the legend must state: zones are indicative; this is the legally designated extent and
ignores flood defences (Karlín shows as Q100 although the city protects it); Q500 is mapped only
locally; bands are nested (Q5 ⊂ Q20 ⊂ Q100 to within 0.2 %), so overlaps render darker; the active
zone can leak outside Q100; nothing is mapped for pluvial flooding; binding information comes only
from the water authority. Resolve layers BY NAME from `?f=json`, never by numeric id.

### Kraj borders — ČÚZK RÚIAN via our `admin_boundaries` (level = 'kraj', already in the database)

SQL source: `ST_Boundary` of each kraj polygon (shared borders are drawn twice today as well; no
regression). Re-rendered at the end of `ingest_boundaries.yml`.

### Datum control point (both coordinate systems from PID open data, stop Florenc M1B, id 689/101)

S-JTSK (EPSG:5514) x = -741651.9, y = -1042827.75 ⇔ WGS84 lon 14.4369316, lat 50.09053.
`ST_Transform(ST_SetSRID(ST_MakePoint(-741651.9, -1042827.75), 5514), 3857)` must land within 1 m
of `ST_Transform(ST_SetSRID(ST_MakePoint(14.4369316, 50.09053), 4326), 3857)`. The ingest runs this
check first and refuses to continue on failure; the live CI test asserts it too.

## Data model (migration 600, additive, applied before merge)

All four tables: RLS enabled, `revoke all … from anon, authenticated`, no policies, no browser
grants — only the API (service role) reads them. PostGIS functions are written unqualified
(`st_asmvt`, not `extensions.st_asmvt`): production keeps PostGIS in schema `extensions`, the CI
replay in `public`; the function sets `search_path = public, extensions`.

```sql
create table map_overlay_sections (          -- one tile source = one chip
  key               text primary key,        -- 'metro' | 'flood' | 'kraje' | 'rent_map'
  label             text not null,
  sort              int  not null,
  minzoom           int  not null,           -- tiles rendered for z in [minzoom, maxzoom]
  maxzoom           int  not null,
  attribution       text not null,           -- shown by MapLibre's attribution control + the chip
  licence           text not null,
  source_url        text not null,
  note              text,                    -- the honest legend text (Czech)
  source_revised_at date,                    -- the publisher's reference/revision date
  fetched_at        timestamptz,             -- last successful ingest (null = never)
  version           bigint not null default 0  -- epoch seconds of fetched_at; tile URL cache key
);
create table map_overlay_layers (            -- one toggle key = one MVT layer in the section tile
  key           text primary key,            -- 'metro' | 'flood_q5' … 'flood_az' | 'kraje' | 'rent_map'
  section       text not null references map_overlay_sections(key) on delete cascade,
  label         text not null,
  sort          int  not null,               -- chip order; drawn in REVERSE sort within a section
  legend        jsonb not null default '[]', -- [{class, label, color, dash?}] categorical paint
  scale         jsonb,                        -- {prop, stops:[[v,color]…], null_color, unit}
  variants      jsonb,                        -- [{key, label, prop}] (rent_map only, PR C)
  feature_count int  not null default 0
);
create table map_overlay_features (
  id        bigserial primary key,
  layer_key text  not null references map_overlay_layers(key) on delete cascade,
  source_id text,
  props     jsonb not null,                  -- flat scalars only: class, name, label, numbers
  geom      geometry(Geometry, 3857) not null -- made valid, dissolved per layer, ST_Subdivide(…, 256)
);
create index on map_overlay_features using gist (geom);
create index on map_overlay_features (layer_key);
create table map_overlay_tiles (
  section text not null references map_overlay_sections(key) on delete cascade,
  z int not null, x int not null, y int not null,
  mvt bytea not null,
  primary key (section, z, x, y)
);
```

`map_overlay_render_section(p_section text) returns int` (plpgsql, `security invoker`, revoked
from anon/authenticated): deletes the section's tiles, then for each z in [minzoom, maxzoom] and
each tile whose envelope intersects the section's feature extent, builds one MVT layer per layer
key with `ST_AsMVTGeom(geom, ST_TileEnvelope(z,x,y), 4096, 64, true)` over features filtered by
`geom && ST_TileEnvelope(z, x, y, margin => 64.0/4096)`, concatenates the non-empty layers
(`||`), stores non-empty tiles, returns the tile count. `props` is expanded by `ST_AsMVT` into
feature attributes (one-level jsonb). No simplification at render time (ST_AsMVTGeom's grid snap
is enough at z ≤ 12 and the sources are already generalised); minzoom bounds the tile size.
`feature_id_name` = `id`.

Seeds: none. The registry rows come from the ingest (UPSERT), so adding a layer needs no migration.

## Ingest — `scripts/ingest_map_overlays.py` + `.github/workflows/ingest_map_overlays.yml`

ONE registry in Python, `SOURCES`: an ordered mapping section → `Section(label, sort, minzoom,
maxzoom, attribution, licence, source_url, note, revised_at reader, layers=[Layer(key, label,
sort, legend, scale, variants, reader)])` where a reader is one of two kinds:

- `ArcGisLayer(service_url, layer_name, where, out_sr, props: Callable[[dict], dict | None])` —
  resolves the layer by name from `{service}?f=json`, asserts `geometryType` and the required field
  names, pages by `returnIdsOnly` + chunks of ≤ 50 ids (`f=geojson`, `outSR`, `outFields=*`),
  0.5 s between requests, retries with backoff on 429/5xx/network, honest User-Agent
  `sreality-tracker/1.0 (+https://github.com/waiff/sreality)` (an HTTP/1.1 session). The mapper
  returns the flat props dict or `None` to skip the feature. Geometry input SRID = `out_sr`.
- `SqlSource(sql)` — a SELECT yielding `(source_id, props jsonb, geom geometry)` in 3857 (kraje,
  later rent_map).

Per section, in ONE transaction (`with conn.transaction():` — `scraper.db.connect()` is
autocommit): the datum control-point check; fetch every layer; completeness guards: every requested
id came back with a non-null geometry, no `exceededTransferLimit`, fetched count == the server's
`returnCountOnly`, and the new count ≥ 90 % of the registry's previous `feature_count` (else refuse
and exit non-zero; `--accept-shrink` overrides); then `delete from map_overlay_features where
layer_key = …`, insert via `ST_Subdivide(ST_UnaryUnion(ST_Collect(ST_MakeValid(ST_Transform(
ST_SetSRID(ST_GeomFromGeoJSON(%s), srid), 3857)))), 256)` for polygon layers (dissolved per layer so
same-band overlaps cannot compound), `ST_Subdivide(…)` of the plain geometry for lines/points,
UPSERT the section + layer rows (label, legend, note, counts, `source_revised_at`, `fetched_at =
now()`, `version = extract(epoch from now())`), run `map_overlay_render_section`, log per section:
features, tiles, max/avg tile bytes, seconds. `statement_timeout` is raised to 30 min for the
session (service role). A section whose `source_revised_at` has not changed and whose feature
count matches is still re-rendered (cheap, idempotent) — simplicity over a skip rule.

Draw order inside the flood section: the SPA draws layers in reverse `sort`, so `flood_q500`
(sort 4) sits under `flood_q100` (3), `flood_q20` (2), `flood_q5` (1); `flood_az` (sort 5) is a
LINE layer (ST_Boundary of the dissolved active-zone polygons, subdivided), drawn on top.

CLI: `python -m scripts.ingest_map_overlays [--sections metro,flood] [--dry-run] [--accept-shrink]`.
Workflow: `workflow_dispatch` (sections, dry_run, accept_shrink) + quarterly cron
`0 4 2 1,4,7,10 *`; `pip install -e .`; `SUPABASE_DB_URL` secret; a leading description comment
under `name:`; regenerate `frontend/src/lib/workflowDocs.generated.ts`
(`python -m scripts.generate_workflow_docs`). `ingest_boundaries.yml` gains a final step
`python -m scripts.ingest_map_overlays --sections kraje`.

Registry values:

| section | label | zoom | layers (sort) | legend classes |
| --- | --- | --- | --- | --- |
| metro | Metro | 9–12 | metro (1) | A #00A562, B #FCC400, C #CF003D, D #1F77D0 dash ("Metro D – návrh"), reserve #8C9196 dash ("Územní rezerva"), station #1F77D0 ("Plánovaná stanice") |
| flood | Záplavová území | 10–12 | flood_q5 (1) #1D4F91, flood_q20 (2) #3B7DD8, flood_q100 (3) #7FB3E6, flood_q500 (4) #C6DCF2, flood_az (5) line #B3261E dash | one class per layer: q5 "Q5 – pětiletá voda", q20, q100 "Q100 – stoletá voda", q500 "Q500 – mapováno jen místy", az "Aktivní zóna" |
| kraje | Kraje | 3–9 | kraje (1) | kraj rgba(40,40,70,0.55) |

Notes (Czech, shown in the chip): metro — "Metropolitní plán hl. m. Prahy (platný od 1. 9. 2026):
stav, návrh a územní rezervy. Rezerva „výhledové tangenciální kolejové spojení“ (v tisku metro E)
nemá rozhodnutou trasu ani technologii."; flood — "Stanovená záplavová území (zákonný rozsah,
orientační). Nezohledňují protipovodňová opatření (např. Karlín), Q500 je vymezeno jen místy,
překryv pásem je tmavší, přívalové povodně nejsou zahrnuty. Závazné informace: vodoprávní úřad.";
kraje — none.

## API — `api/routes/map_overlays.py` (both `require_token`, service-role connection)

- `GET /map-overlays` → `{"sections": [{key, label, sort, minzoom, maxzoom, attribution, licence,
  source_url, note, source_revised_at, fetched_at, version, layers: [{key, label, sort, legend,
  scale, variants, feature_count}]}]}` sorted by sort; a section with `fetched_at` null is omitted
  (never ingested → not offered).
- `GET /map-overlays/{section}/{z}/{x}/{y}.mvt?v=…` → the tile bytes,
  `media_type="application/vnd.mapbox-vector-tile"`, `Cache-Control: private, max-age=31536000,
  immutable` (the URL carries the version). 204 for no tile row or z outside [minzoom, maxzoom];
  404 for an unknown section; 400 for a non-integer or out-of-range x/y. One `select mvt …` by
  primary key; no rendering at request time.

Tests with the `_fakes` FakeConn pattern: registry shape and ordering, omitted-when-unfetched,
tile 200 (headers), 204, 404, 400, auth refused without a token.

## Frontend

### `lib/mapOverlays.ts`

- Types mirroring the API JSON. `fetchMapOverlays()` via `apiGet` (React Query key
  `['map_overlays']`, `staleTime: Infinity`).
- `registerOverlayProtocol()` — idempotent, called from `lib/basemap.ts` at module load:
  `maplibregl.addProtocol('overlay', handler)`; the handler parses
  `overlay://{section}/{z}/{x}/{y}?v=`, calls `apiGetBlob('/map-overlays/{section}/{z}/{x}/{y}.mvt',
  {v}, abortController.signal)` and returns `{data: await blob.arrayBuffer()}`; an empty 204 body
  and a missing session both resolve to an empty ArrayBuffer (no error per tile). One transport,
  one deadline, one error class — nothing new.
- Selection grammar: `key[:variantKey]`, comma-separated, canonical = sorted by key; `''` = none.
  `parseLayers(s)` / `serializeLayers(sel)`; `layersOf(sel, registry)` drops unknown keys.
- `readRememberedLayers()` / `writeRememberedLayers(s)` — localStorage key `map.layers`, guarded
  like `persistedFlag.ts` (add `readValue`/`writeValue` string helpers there; one place).
- `useMapOverlays(map, ready, registry, selection, beforeId)` — for each section with any selected
  layer: `addSource('ov-{section}', {type:'vector', tiles:['overlay://{section}/{z}/{x}/{y}?v={version}'],
  minzoom, maxzoom, attribution})`; per layer key, in reverse sort, style layers inserted before
  `beforeId` (fallback: the first symbol layer of the basemap): polygons → `ov-{key}-fill` (fill,
  `fill-antialias: false`, opacity 0.4, colour = `match` on `class` from the legend, or the scale's
  `interpolate` on `coalesce(get(prop), -1)` with `null_color` below 0); lines → `ov-{key}-line`
  (solid classes) + `ov-{key}-dash` (classes with `dash`, `line-dasharray [2, 1.5]`), width 2.2,
  colour by class; points → `ov-{key}-point` (circle 4 px, white stroke) + `ov-{key}-label` (symbol,
  `text-field: get(label)`, 11 px, offset). Visibility follows the selection; a variant change
  only updates the fill paint (no refetch). Layer ids are removed on unmount.
- `overlayPopupHtml(sectionLabel, props, layer)` — one popup for click on a fill: kicker =
  section label, title = `name`, meta = the class label (and the variant value with its unit via
  `lib/measure.ts` `PPM2_UNIT` when `scale.unit` is `rent_per_m2`). Browse's click handler keeps
  listing layers first (the existing `queryRenderedFeatures` precedence).

### `components/MapOverlayControls.tsx`

Same shape as `RentMapControls`: one chip per section in the bottom-right stack (checkbox +
label); checked = every layer of the section on (variant = the first); the expanded panel below the
chip (only when checked) holds per-layer checkboxes when the section has > 1 layer (the section
checkbox is tri-state), variant radios when present, the legend (swatch per class: filled square,
line, dashed line, dot), a hint "Přibližte mapu" when the map zoom < minzoom, the note, and a footer
"Data k {source_revised_at ?? fetched_at} · {attribution}". `compact` (DetailMap, ComparablesMap):
chips only, no panel. The chip markup is ONE component, `OverlayChip`, also adopted by
`RentMapControls`, `GrowthMapControls` and `CityMapControls` (four copies of the same class string
today).

### State

- `MapOverlayState.layers: string` (the canonical selection; `''` = none) replaces `showKraje`.
  URL key `layers`; legacy `kraje=1` reads as `kraje`. Rule: URL key present (even empty) = explicit;
  absent = the remembered selection. Every change writes `layers=` to the URL (Browse) and the
  remembered value. Add `layers` to `OVERLAY_URL_KEYS`, `setOverlay`, `browseUrlFromState`, the
  round-trip test. Memory adapter (Explore modal): initial = remembered.
- `DetailMap` and `ComparablesMap`: local state initialised from the remembered selection, written
  back on change; both mount the hook (`beforeId` 'pin' / 'comparables-point') and the compact
  controls. `ListingMap`: `beforeId` 'city-fill'.
- Delete: `rent_map_kraje_public` fetch (`fetchRentMapKraje`, `RentMapKraj`), `rentKrajeQuery`,
  `toKrajFC`, the `rent-kraje` source/layer/effect, the nested Kraje checkbox, `showKraje`,
  `onToggleShowKraje`, `rentMapKraje`. The view itself is dropped by migration 601 after deploy.
- Dedupe the haversine ring: `lib/geoRing.ts` `circleRing(lat, lng, radiusM, points = 96)` replaces
  the two copies in `ListingMap.tsx` and `circlePolygon` in `LocationControl.tsx`.

### Tests

`mapOverlays.test.ts` (grammar, legacy mapping, style-layer builders from a legend/scale, protocol
URL parsing); `browseState.test.ts` (round trip incl. `layers`, `kraje=1` legacy);
`MapOverlayControls.test.tsx` (chips, tri-state, variant radio, hint, note, footer, compact);
`persistedFlag.test.ts` (string helpers); `geoRing.test.ts`.

## Cleanup PR (after the feature merges): the MF rent choropleth onto this path

Section `rent_map` (label "Cenová mapa nájemného", 7–12, attribution "Ministerstvo financí ČR –
Cenová mapa nájemného"), layer `rent_map` with `scale = {prop: 'vk1', stops: RENT_RAMP, null_color,
unit: 'rent_per_m2'}` and `variants = [{key:'vk1', label:'1+kk, 1+1', prop:'vk1'} … vk4]`; SqlSource
= `admin_boundaries` × latest `rent_map_values` revision (the matview's query). `fetch_rent_map.yml`
re-renders the section after an ingest. The SPA deletes `fetchRentMapChoropleth`, `RentMapPolygon`,
`toRentFC`, `rentPopupHtml`, the `rent-map` source/layers/effects/handlers, `RentMapControls`,
`showRentMap`/`rentVk` (URL `rentmap`/`rentvk` read as legacy → `rent_map:vkN`). Migration 601 drops
`rent_map_choropleth_public`, `rent_map_kraje_public`, the `rent_map_choropleth` matview and its
refresh call in `api/rent_map.py`.

## Files and ownership

| wave | files |
| --- | --- |
| schema | `migrations/600_map_overlays.sql`; `tests/test_map_overlays_live.py` (CI DB lane: control point, render function on a square + a line + a point, tile bytes contain the layer name); the pytest line in `.github/workflows/migrations.yml` |
| ingest | `scripts/ingest_map_overlays.py`; `.github/workflows/ingest_map_overlays.yml`; `ingest_boundaries.yml` final step; `frontend/src/lib/workflowDocs.generated.ts`; `tests/scripts/test_ingest_map_overlays.py` + `tests/fixtures/map_overlays/*.json` (small real samples) |
| api | `api/routes/map_overlays.py`; `api/main.py` registration; `tests/api/test_map_overlays.py` |
| spa | `frontend/src/lib/mapOverlays.ts`, `geoRing.ts`, `persistedFlag.ts`, `basemap.ts`, `browseState.ts`, `queries.ts`; `components/MapOverlayControls.tsx`, `OverlayChip.tsx`, `ListingMap.tsx`, `BrowseExperience.tsx`, `listing-detail/DetailMap.tsx`, `estimation/ComparablesMap.tsx`, `filter-controls/LocationControl.tsx`; tests |
| docs | this file; `docs/architecture.md` (new section + cross-reference map); `roadmap/map-track.md`; `GLOSSARY.md` ("Reference overlay"); `.claude/skills/toolkit-api/SKILL.md` (the two routes; trim a line first, the file is at its cap); `.claude/skills/database/SKILL.md` (one line: the overlay store is a mirror, rendered tiles, ingest workflow) |

## Deploy sequence

1. Apply 600 from the branch (`apply_migration.yml` dry run, then `confirm=APPLY`); verify with one
   aggregate SELECT. 2. CI green; PR marked ready; the operator merges. 3. Dispatch
   `ingest_map_overlays.yml` from main; verify counts, tile sizes and the datum check in its log.
4. Railway commit status per service; read-only browser check of Browse with `?layers=metro`.
5. Cleanup PR: build, ingest `rent_map` from the branch (the workflow now exists on main), merge,
   then apply 601.

## Build record (2026-10-14) — where the build deviates from the text above, and why

- **Ingest:** the fetch runs BEFORE the write transaction (a multi-minute HTTP fetch must not hold an
  open transaction on the pooler); the datum check still runs first and a refusal writes nothing.
  `statement_timeout` is `SET LOCAL` inside the write transaction (a session SET would leak through
  the transaction-mode pooler). A `Layer` has `readers` (a tuple — metro is fed by three services) and
  a `store` kind (`plain` | `dissolve` | `outline`); flood bands are dissolved per LAYER after insert,
  each piece named with the distinct watercourses within 0.5 m ("Berounka, Vltava"); `ST_Force2D` and
  `ST_CollectionExtract(…, 3)` precede the union (the source carries Z/M and make-valid can yield
  collections). Extra refusals: an empty layer on first run, a class outside the layer's legend, a
  non-flat prop, a proposal without a line letter. Layers removed from the registry are deleted for
  the section being ingested. An unreadable revision date degrades to NULL with a warning. The
  per-section write takes `pg_advisory_xact_lock('map_overlays:<section>')` so the boundaries
  workflow's kraje step and the quarterly run cannot interleave. Legend entries carry an optional
  `geom` (`line` | `point`) that only shapes the chip swatch. The tiles are registered as the derived
  artifact `map_overlay_tiles` (migration 600 seeds the row; the ingest stamps it through
  `scraper.db.stamp_derived_artifact` after a clean non-dry run). The workflow docs file is
  `frontend/public/workflow-docs.json`.
- **API:** 204 answers carry the same immutable `Cache-Control` as the bytes (the URL is versioned,
  and most flood tiles are empty); z/x/y must be 1–9 ASCII digits (else 400 `bad_tile`); order of
  checks 400 → 404 → 204; a registered but never-fetched section answers 204 on tiles.
- **SPA:** the overlay code is two modules — `lib/mapOverlays.ts` (registry types, fetch, grammar,
  remembered selection; eager) and `lib/mapOverlayDraw.ts` (protocol, style layers, hook, popup;
  only the lazy map chunks import it) — to stay inside the first-visit bundle budget;
  `registerOverlayProtocol(maplibregl.addProtocol)` is called from `basemap.ts`. Draw order is global
  across sections: areas, then lines, then points, then labels, reverse sort within each band (the
  flood active-zone line sits above the bands). A line is dashed by its legend class OR its own `dash`
  prop (the metro B extension); the dashed line layer always exists. `escapeHtml.ts` is shared by
  both popup builders; `.overlay-popup` in `globals.css`; compact chips sit top-left on the two small
  maps; a toast reports a failed registry read while layers are selected; `kraje` stays in
  `OVERLAY_URL_KEYS` so an old link keeps its borders through filter changes.
- **After the code review (3 Opus lenses, 2026-10-14):** planned stations are station points in the
  návrh stage only (`druh=2 and stav=2`; the plan's four reserve-stage stations are not drawn);
  the whole-store artifact is stamped only by a run over EVERY section (a kraje-only step after a
  boundaries ingest stamps nothing), with the store-wide tile count; a transient ISO-record failure
  degrades the revision date to NULL instead of failing the section, and a connection dropped
  mid-body is retried; the tile route validates the address before a connection is opened and pins
  a tile for a year only when `?v=` names the section's LIVE version (else `private, no-cache`); a
  click on an overlay area is answered unless a listing is hit (the city polygon no longer swallows
  flood clicks in Prague); the remembered selection is ONE live store (`useSyncExternalStore`) read
  by every map and by Browse's fallback, so two maps on one page agree and a navigation to a bare
  `/browse` keeps the page's own last choice.
- **Zoom floors (review of the cleanup, 2026-10-15):** Browse's nationwide fit lands at z≈4.8–5.1 on
  1280–1440 px screens (map column 42 % beside the 320 px sidebar, 56 px fit padding), and MapLibre
  loads no tile below a source's minzoom. The old Kraje overlay drew at every zoom, so `kraje` is
  tiled from z3 (one tile covers Czechia at z3 and z4; the two extra tiles are tiny lines).

