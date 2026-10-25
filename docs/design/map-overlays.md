# Map reference overlays — design and build spec

Status: SHIPPED 2026-10-15 (#1808 feature, #1810 + #1813 production fixes, #1809 cleanup; migrations 600 + 601
applied). Operator rulings in § Rulings. This document is the north star for
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
   labelled exactly as the plan labels it) plus the named planned stations. Existing stations were
   not drawn at first (the plan's own station points carry no names); **since 2026-10-16 they are**
   (operator ruling: a property's distance to a station must be readable), from IPR's named
   metro-equipment layer `ReferencedServices/DOP_MetroZarizeni_b` (`DRUH=1`, fetched in native
   S-JTSK), each dot in its line's colour, a transfer station once per line.
5. **Universities (2026-10-16, four rulings):** a `universities` section of Prague's university sites
   from IPR's planning-analysis layer. (a) A site is **primary** when 2 000 or more students in
   present-form study are counted there: MŠMT's per-faculty counts (F21) tied to each faculty's main
   teaching building by a curated table in the ingest (`UNIVERSITY_SEATS`), summed per site; every
   other teaching or service site is secondary. (b) Dorms are a toggle of their own (`dorms`) beside
   the teaching sites. (c) Service sites — rectorates, canteens, sport halls, libraries, hospital
   clinics where the medical faculties teach — all show as secondary; no filtering rule. (d) Primary
   sites carry the conventional short label ("FF UK", "FEL ČVUT", "PEF ČZU", "VŠE"); secondary sites
   and dorms show their IPR name on click only.

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
- `ReferencedServices/DOP_MetroZarizeni_b/FeatureServer/0` where `DRUH=1` (2026-10-16) → the 61
  stations in service, named (`NAZEV`), with their line letter (`TRASA`: A 17, B 24, C 20; Muzeum,
  Můstek and Florenc once per line). Class = the letter, so the point wears its line's legend colour
  and adds no legend row; `label` = the name. Fetched in native S-JTSK (5514): the server's WGS84
  output is the same ~10 m datum shift off as the VÚV register's. Field names are upper case.
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

### Universities — IPR Praha ÚAP `Hosted/FSV_CUR_OV_SKOLYVS_B` (CC BY) + MŠMT F21 (verified live 2026-10-16)

- IPR's point layer of university facilities: 289 sites, stored in 3857, fields `id_zar` (stable
  site id), `nazev_zar` (the facility name, e.g. "UK - F filosofická", "ČVUT - FIT, F architektury",
  "UK - kolej 17. listopadu"), `nazev_vs`, `druh_vs` (VVS public 248 / SVS private 39 / STS state
  2), `typ_prac_hl` (F faculty 111, CEL whole school 31, KAT, U, M, A, R, S…), `typ_uap` — `02_01`
  teaching 168, `02_02` other facility 55, `02_03` dorm 66. No student numbers; the typed flag
  columns (`fakulta`, `dekanat`…) are unpopulated except `kolej`. Layer `editingInfo.lastEditDate`
  = 2026-01-20 (the section's `source_revised_at`).
- MŠMT, Výkonové ukazatele VŠ, table **F21** "studenti podle vysoké školy/fakulty"
  (`https://dsia.msmt.cz/vystupy/f2/f21.xlsx`): one sheet per year 2000–2025, column B the SIMS
  code (`11210` = UK Filozofická fakulta, `6D000` = UNYP, `21900` = ČVUT celoškolská pracoviště),
  column G all students, **column H students in present-form study** (the ones on site daily — the
  count the rule uses); state of SIMS as of 2026-01-20 for 2025. Read with the stdlib (zip + XML),
  the newest year sheet wins. 2025: UK FF 5 757, FSV 5 652, PřF 5 189, 1. LF 4 556, PedF 3 861, PF
  3 636, MFF 3 173, FHS 2 725, 3. LF 2 517, 2. LF 2 093, FTVS 2 046; ČVUT FSv 4 066, FEL 3 398,
  FIT 2 406 + FA 1 750 (one building), FS 1 616; VŠCHT 4 070; VŠE 13 694 (Prague faculties); ČZU
  PEF 7 811, FLD + FŽP 3 096, FAPPZ 2 884; AMBIS 3 712, MUP 2 751, VŠFS 2 274 (whole schools).
- The join, `UNIVERSITY_SEATS` (49 rows): faculty code(s) → the IPR `id_zar` of the main teaching
  building + the short label. Faculties outside Prague (ČVUT FBMI Kladno, VŠE FM Jindřichův Hradec,
  UK Plzeň/Hradec) and schools seated elsewhere (ŠKODA AUTO VŠ) have no row; an unmapped site is
  secondary. Rails: a code F21 no longer carries refuses the run (stale join); a primary seat that
  matched no IPR point refuses the run; the unit test pins every seat id to the 289 real ids
  (`tests/fixtures/map_overlays/ipr_universities_ids.json`).
- Result (2026-10-16): 223 teaching/service sites of which **22 primary**, 66 dorms; 17 tiles at z10–12
  on the first ingest. Tiled from **z9** since the same day: Browse's whole-of-Prague view (the 0.45°
  city bbox fitted into the split view's map column) lands at z≈9.9, where a z10 floor shows nothing
  but the chip's "Přibližte mapu".

### Railway stations — ČÚZK ZABAGED® `ZABAGED_POLOHOPIS/MapServer/58` (CC BY 4.0, verified live 2026-10-19)

`https://ags.cuzk.gov.cz/arcgis/rest/services/ZABAGED_POLOHOPIS/MapServer`, layer "Železniční stanice,
zastávka" (id 58): 2,797 points for the whole country at 1:10 000, native EPSG:5514 (fetched native,
PostGIS transforms, as for the flood register). Fields: `fid_zbg` (stable id), `zst_k` (the SR70-style
station code), `zst_p` (name), `typ` ∈ {stanice, zastávka}. Class = `typ` (station / halt), name and
label = `zst_p`, `code` = `zst_k`; an unknown `typ` or an unnamed point refuses the run. ČÚZK has
published ZABAGED® as open data under CC BY 4.0 since 2023-07-01. Alternatives rejected: ČÚZK Data50
(2,786 points at 1:50 000, same classes, coarser), Data250 (generalised), Správa železnic (no public
REST; the SR70 list has no permanent URL), OSM (licence incompatible, less authoritative).

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
  scale         jsonb,                        -- {prop, stops:[[v,color]…], null_color, unit, opacity?}
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
| railway | Železniční stanice | 7–12 | railway_stations (1) | station #37474F ("Železniční stanice", point, labelled), halt #90A4AE ("Železniční zastávka", point, labelled) |
| universities | Vysoké školy | 9–12 | universities (1), dorms (2) | primary #5E35B1 ("Hlavní místo výuky (2 000+ studentů)", point, labelled), secondary #B39DDB ("Další pracoviště VŠ", point); dorm #F57C00 ("Kolej", point) |

Notes (Czech, shown in the chip): metro — "Metropolitní plán hl. m. Prahy (platný od 1. 9. 2026):
stav, návrh a územní rezervy. Rezerva „výhledové tangenciální kolejové spojení“ (v tisku metro E)
nemá rozhodnutou trasu ani technologii."; flood — "Stanovená záplavová území (zákonný rozsah,
orientační). Nezohledňují protipovodňová opatření (např. Karlín), Q500 je vymezeno jen místy,
překryv pásem je tmavší, přívalové povodně nejsou zahrnuty. Závazné informace: vodoprávní úřad.";
kraje — none; universities — "Místa vysokých škol v Praze podle ÚAP IPR Praha. Hlavní místo výuky =
budova fakulty nebo školy, kde podle MŠMT (SIMS, poslední zveřejněný rok) studuje 2 000 a více
studentů prezenčního studia; u škol s pobočkami mimo Prahu jde o počet za celou školu. Další
pracoviště: ostatní budovy, kliniky, rektoráty, menzy, sportoviště."; railway — "Železniční stanice a
zastávky v celé ČR podle ZABAGED® ČÚZK; název a druh podle Správy železnic (kód SR70 v detailu)."

A third reader kind serves the universities layer: `UniversitySites(where)` reads F21 once per run
(`Http.content`, `parse_f21`), sums the counts per seat (`students_on_site`) and then runs the ArcGIS
pager with the counts bound into the props mapper (`functools.partial(university_site_props,
on_site)`), so the pager, its guards and the datum check are the same code path as every other
layer. The dorms layer is a plain `_ipr(...)` reader on the same service, which also supplies the
section's revision date.

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
"Data k {source_revised_at ?? fetched_at} · {attribution}". `compact` (ComparablesMap): chips only,
no panel. (The property page's header minimap, `DetailMap`, carries no overlays since 2026-10-23 —
operator: zoom is its only control.) The chip markup is ONE component, `OverlayChip`, also adopted by
`RentMapControls`, `GrowthMapControls` and `CityMapControls` (four copies of the same class string
today).

**Browse since 2026-10-22 (the map rail, `components/MapRail.tsx`).** The four corner stacks covered a
quarter of a 450 px map (the map gets 42 % of the width beside the filters, so it is that narrow on a
1440 px screen). Browse's map now carries one column of icon buttons at its right edge: Vrstvy (badge =
layer groups drawn), the per-m² pin labels, the price labels, Piny a ceny, zoom in / out (createMap's
`navigationControl: false`), and "Zobrazit vše" while a map area is set. Vrstvy opens a panel beside the
rail (a bottom sheet on a map under 520 px — a container query on the map, not the window) holding every
toggle: Města + "Obarvit podle" + Min., Růst cen a nájmů + its settings, then each registry section as
above, its toggle a plain row (`OverlayChip` without `compact`) and its settings indented under it
(`OverlayPanel`). The legends left the panel: `MapOverlayLegends` (plus the growth and city-index keys)
draws a card per layer group that is on in the map's bottom-left corner, so a choropleth reads with the
panel closed; the stack folds to one button, remembered per browser. The count pill keeps the pin key
(swatch + count; the words from a 900 px map up); the full key and the pin limit live in Piny a ceny.

### State

- `MapOverlayState.layers: string` (the canonical selection; `''` = none) replaces `showKraje`.
  URL key `layers`; legacy `kraje=1` reads as `kraje`. Rule: URL key present (even empty) = explicit;
  absent = the remembered selection. Every change writes `layers=` to the URL (Browse) and the
  remembered value. Add `layers` to `OVERLAY_URL_KEYS`, `setOverlay`, `browseUrlFromState`, the
  round-trip test. Memory adapter (Explore modal): initial = remembered.
- `ComparablesMap`: local state initialised from the remembered selection, written back on change;
  it mounts the hook (`beforeId` 'comparables-point') and the compact controls. (`DetailMap` did too
  until 2026-10-23.) `ListingMap`: under the first growth-choropleth layer (where the rent map and the
  kraj borders have always sat), so a choropleth on top still tints them as before.
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

Backend, as built (2026-10-15):

- **Registry** (`scripts/ingest_map_overlays.py`): section `rent_map`, sort 4, **zoom 5–12, not 7–12**:
  the old layer draws at every zoom, and at 7 it would vanish from the whole-country view (≈ z6.6 on a
  1000×700 desktop map, ≈ z5.3 on a phone), against the operator's no-visible-change condition. Measured
  on production 2026-10-15, a low zoom costs nothing: the z5 tile over Czechia carries 7,582 of the 7,630
  territories in 1.0 MB, a z6 tile 1.0 MB, a z7 tile 0.77 MB (z8 ≈ 128 KB, z10 ≈ 17 KB, z12 ≈ 2 KB),
  against ~10 MB of GeoJSON geometry the old read fetched whole on every load. Reverting to 7 is one
  number. Attribution "Ministerstvo financí ČR – Cenová mapa nájemného"; licence "Licence neuvedena
  (© Ministerstvo financí ČR)" (the MF page states no licence for the data, only its site's copyright);
  `source_url` = `MF_INFOGRAPHIC_URL`, moved to `toolkit/rent_map.py` so the ingest and `api/rent_map.py`
  share it without an import cycle; note "Měsíční nájemné referenčního bytu za m² podle Ministerstva
  financí ČR, zvlášť pro každou velikostní kategorii bytu."; `source_revised_at` = the `source_date` of
  the revision the source draws (the latest by `source_revision`).
- **Layer** `rent_map`: legend empty, store `plain`, `scale = {prop: 'vk1', stops: RENT_RAMP (150
  #eaf0f6 … 600 #1a1a5e), null_color: 'rgba(140, 140, 140, 0.35)', unit: 'rent_per_m2', opacity: 0.62}`
  — `opacity` is new to the scale contract (the SPA's `OverlayScale` had none); `variants` vk1..vk4
  with the MF labels. `RENT_MAP_SQL` is migration 132's matview query without its GeoJSON column:
  props `{name, kraj, level, vk1..vk4}` through `jsonb_strip_nulls` (an unpriced VK or a NULL kraj is
  absent and paints `null_color`), geometry `st_transform(geom::geometry, 3857)`. On production it yields
  7,630 features identical (name, kraj, vk1–4) to the matview's 7,630 rows; only 15 polygons exceed 256
  vertices, so `ST_Subdivide` splits almost nothing.
- **`render_sql_section(conn, key)`**: one SQL-sourced section through `run_section` — datum check,
  legend/flat-props and shrink guards, the one write transaction (a savepoint inside a caller's) —
  from the database alone: its HTTP session refuses any fetch at once, never retried, so a request
  never waits on the network. `api/rent_map.py`'s `insert_revision` calls it where it refreshed the
  matview, after the COPYs and before the `rent_map_cells` refresh. A refused guard writes nothing and
  is caught: the revision still lands (`mf_reference()` keeps flowing), the map keeps its previous
  tiles, the API logs an ERROR, and `fetch_rent_map.yml`'s re-render step fails red on the same refusal
  (`ingest_map_overlays.yml` with `accept_shrink` overrides). Any other failure rolls the whole ingest
  back, as a failed refresh did. Cost, estimated (not yet measured end to end): the ingest request
  grows by 30–60 s — 19 MB of EPSG:3857 GeoJSON through Python and ~4.7k tile envelopes rendered (a
  dense Prague sample of z8–z12 tiles took ~14 ms a tile, sparse ones far less) — inside the SPA's
  130 s request deadline.
- **Workflow**: `fetch_rent_map.yml` ends with `python -m scripts.ingest_map_overlays --sections
  rent_map` (`--dry-run` mirrored); every run re-renders, so the tile version, and with it the
  browsers' cached tiles, turns over monthly. `ingest_map_overlays.yml`'s full quarterly run includes
  `rent_map`, so it still stamps `map_overlay_tiles`; the API's single-section render stamps nothing.
- **Migration 601**: drops both views and the matview (its unique index goes with it) and deletes the
  matview's `derived_artifacts` row (`tests/test_derived_artifacts_stamping.py` fails a row nothing
  stamps); no CASCADE (the live catalog shows no other dependent), a DO-block self-check. Applied only
  after the cleanup deploys, with the operator's go.
- **Deploy order, a window either way**: the deployed feature SPA draws a chip for every registry
  section, so `rent_map` ingested before this PR's SPA is live (step 5 below) shows a second rent-map
  chip (generic paint, no `opacity`) until the rollout lands. Ingested after the rollout, the rent layer
  is missing until the ingest finishes, and for a tab opened in between until a reload (the registry
  query never goes stale). Which is quieter turns on whether the new SPA keeps a `rentmap=` /
  `rent_map:vkN` selection while the registry lacks the section.

## Files and ownership

| wave | files |
| --- | --- |
| schema | `migrations/600_map_overlays.sql`; `tests/test_map_overlays_live.py` (CI DB lane: control point, render function on a square + a line + a point, tile bytes contain the layer name); the pytest line in `.github/workflows/migrations.yml` |
| ingest | `scripts/ingest_map_overlays.py`; `.github/workflows/ingest_map_overlays.yml`; `ingest_boundaries.yml` final step; `frontend/src/lib/workflowDocs.generated.ts`; `tests/scripts/test_ingest_map_overlays.py` + `tests/fixtures/map_overlays/*.json` (small real samples) |
| api | `api/routes/map_overlays.py`; `api/main.py` registration; `tests/api/test_map_overlays.py` |
| spa | `frontend/src/lib/mapOverlays.ts`, `geoRing.ts`, `persistedFlag.ts`, `basemap.ts`, `browseState.ts`, `queries.ts`; `components/MapOverlayControls.tsx`, `OverlayChip.tsx`, `ListingMap.tsx`, `BrowseExperience.tsx`, `estimation/ComparablesMap.tsx`, `filter-controls/LocationControl.tsx`; tests |
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
- **Cleanup integration (2026-10-15):** the `rent_map` layer is stored `whole` (no `ST_Subdivide`;
  15 of 7,630 places exceed 256 vertices) because the SPA strokes each place's own outline
  (`scale.outline {color, width}`, today's hairline borders); `scale.opacity` (0.62) keeps today's
  fill; the section has sort 0 (its chip stays directly under the growth control, as before) and
  no note; minzoom 3 (see the zoom-floor note below).
  Draw order: a scale (choropleth) layer is always the bottom overlay area, so the rent map never
  covers flood zones; inside a curated city a click on the choropleth leaves the city card in place
  (as today) while flood areas take the click. Accepted small differences: the panel gains the
  source footer and the map's attribution credits the Ministry of Finance; "Načítání…" is gone;
  the rent map is part of the remembered selection, so it follows the operator to the other maps.
  Old links: `rentmap=1&rentvk=N` reads as `rent_map:vkN`. Deploy order: merge the cleanup, then
  dispatch `--sections rent_map` at once (the chip is absent for the minute the render takes; an
  already-open tab needs a reload), then apply 601.
- **Zoom floors (review of the cleanup, 2026-10-15):** Browse's nationwide fit lands at z≈4.8–5.1 on
  1280–1440 px screens (map column 42 % beside the 320 px sidebar, 56 px fit padding), and MapLibre
  loads no tile below a source's minzoom. The old Kraje and rent overlays drew at every zoom, so
  `kraje` and `rent_map` are tiled from z3 (one tile covers Czechia at z3 and at z4; a rent tile
  there is ≤ 0.8 MB, the kraje ones are tiny). The rent fill keeps MapLibre's default antialiasing
  (`fill-antialias` = the layer has a `scale.outline`): the 1 px same-colour edge it draws was most
  of today's borders; flood fills stay un-antialiased because their pieces are subdivided. The last
  picked size category is remembered per layer (`map.layers.variant.<key>`) so switching the rent
  map off and on brings it back, as the old `rentvk` URL key did. A Settings upload or "fetch now"
  whose overlay render was refused now says so next to "Ingested revision N" (`overlay_refused`).
- **Production (2026-10-15):** first full ingest numbers — metro 22 tiles, flood 2,497 (3,995 zones → 114,921
  pieces, 43 MB, ~31 min for the section), kraje 83, rent_map 2,964 (7,630 places, 45 s). Two things the first
  runs taught: the register's Q500 layer spells its fields `Naz_tok` where the other four spell `NAZ_TOK` (fields
  are now matched by case and handed to the mapper in the declared spelling, #1810), and a nationwide
  `ST_UnaryUnion` per band does not finish inside the 30-minute statement budget on Supabase Large, so each zone
  now loses only the area an earlier-inserted overlapping zone of the same band already covers (#1813; cost
  proportional to the few confluences, each piece keeps its own watercourse name).
- **Universities (2026-10-16, ruling 5):** no SPA change and no migration — a section with two
  layers, both drawn by the existing point + label rules (a primary site = a labelled dark dot, the
  same shape as a metro station). The live readers' first run: F21 2025 read (205 codes), 49 seats,
  22 primary sites (all matched an IPR point), 201 secondary, 66 dorms. Present-form students (F21
  column H) rather than all students: the combined-form students of the private schools come in
  blocks, so VŠEM (3 266 all / 1 505 present) and Panevropská (2 704 / 1 874) are secondary while
  AMBIS, MUP and VŠFS stay primary.
- **Railway stations (2026-10-19):** a `railway` section — every station and stop in the country from
  ČÚZK's ZABAGED® (2,797 points, stanice / zastávka), fetched in native S-JTSK; one registry entry, one
  mapper, four fixtures, no frontend change. Tiled from z7 so a regional view shows the network's stops
  without cluttering the country view; labels are the station names (MapLibre thins collisions).

