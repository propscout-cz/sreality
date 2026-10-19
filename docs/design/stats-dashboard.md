# Browse › Stats as a dashboard the user composes

**Status:** approved 2026-10-19 (operator rulings below), built on `feature/stats-dashboard`, migration 626.
**North star:** *one sample, one key, one box.* Every chart reads the Browse cohort at property grain through ONE
pivot RPC; every dimension value is ONE key rendered from ONE registry; every number is ONE box shape drawn by
ONE component. Nothing is computed twice and nothing is labelled from somewhere other than the payload.

## Why

The Stats tab was four fixed cards. The 2026-10-18 audit (235 Opus agents, 70 findings verified by three refuters
each) found their box axes squashed by raw extremes, turnover boxes pooling live ages with finished durations,
two unit bugs, and two hand-copied chart implementations with five box types and four copies of one threshold.
The operator chose to replace them with charts the user builds, which deletes the defects instead of patching them.

## Rulings (2026-10-19, all as recommended except 7)

1. The builder REPLACES the Stats tab; the default dashboard is four seed charts; the fixed RPC body, both chart
   files and the LLM "annotations" feature are deleted.
2. ONE dashboard per account, independent of the filter (the filter only picks the sample); several charts,
   add / remove / reorder. Stored whole.
3. A turnover chart requires ONE status in the sample (active or delisted) — the same refusal as mixed property
   types. Plain percentiles over the picked sample; no survival estimator.
4. "Mixed" = more than one `category_main` OR more than one `category_type` in the cohort.
5. Bands: price per m² sale 5 000 / 10 000, rent 25 / 50; total price sale 250 000 / 500 000 / 1 000 000, rent
   1 000 / 2 500 / 5 000; floor area 5 / 10 m², byty only. Defined once (`toolkit/stats_registry.py`).
6. Statistics n, mean, median, p10, p25, p75, p90; a cell under **10** properties shows n and no statistics
   (server-enforced); a box draws p10–p90 whiskers, the p25–p75 box and the median.
7. Table = rows × columns; line / bar = one row dimension on x, the column dimension as series; box = rows only.
   Nesting up to **six** row levels (table, box).
8. A NULL on any chosen dimension (or the measure) excludes the property and is counted as "excluded for
   missing data"; it is never shown as "ne" or "neuvedeno".
9. The legacy-vocabulary heal (89 condition spellings, 766 dispositions) is a separate PR; land rent keeps the
   rent basis until its own ruling.
10. Design: a sample ledger on every chart; edit in place with shelves Řádky / Sloupce and a Hodnota well;
    the filter sidebar's own chips; copper for the first series, ink tints for the rest.

"Stav" is the scraped `condition` (portal → canonical folding in `scraper/vocabulary.py`, 11 values, best to
worst in `toolkit.filter_registry.CONDITION_OPTIONS`). bazos states none (0 % of 15,100 active byt ads), so a
chart on stav excludes every bazos property — visibly, in the ledger. The estimated two-axis condition levels
(rule 14, paused) are not a dimension.

## The contract

### Registry — `toolkit/stats_registry.py` (the one definition)

`DIMENSIONS` (id, labels, kind `enum | band | geo | bool`, the SQL over `stats_dim_key`'s arguments, the ordered
options, category availability, band steps), `MEASURES` (`tom_days`, `area`, `price`, `ppm2`; `single_status`
on turnover), `STATS`, `MIN_N = 10`, `MAX_ROWS = 6`, `MAX_COLS = 1`, `MAX_CELLS = 2000`, `VIEWS`.
Three faces, none hand-restated: `dim_key_function_sql()` and `measure_value_sql()` are carried verbatim by
migration 626 (pinned by `tests/test_626_stats_dashboard.py`); `registry_json()` is emitted to
`frontend/src/lib/statsRegistry.generated.ts` by `scripts/generate_filter_registry.py` (CI `--check`);
`validate_chart` gates `PUT /stats/dashboard`.

### Pivot RPC — `public.browse_stats_properties(<625's 74 cohort params>, spec jsonb)`

Same name and cohort parameters as before (the 625 WHERE byte for byte, so every rail that pins a cohort
fragment still holds; `tests/test_browse_map_read_contract.py` learns that `spec` is stats-only, as
`point_budget` is map-only). `spec = {rows: [dim…], cols: [dim] | [], measure}`.

```
{ spec, filtered, sample, excluded,            -- properties in the cohort / with every needed value / the rest
  category_main, category_type,                -- the cohort's single values, null when mixed
  status: 'active' | 'inactive' | 'mixed' | null,
  basis: <migration-425 token> | null,         -- rent_monthly_czk_m2 ⇒ rent steps and "Kč / měs"
  min_n: 10,
  refused: null | 'empty' | 'mixed_category' | 'mixed_status' | 'too_many_cells' | 'bad_spec', reason?,
  cells: [ { keys: [k1 … kL], col: kc | null, n, mean, median, p10, p25, p75, p90 } ] }
```

One pass: `group by kc, rollup(k1 … k6)` over the sample, six fixed row slots and one column slot keyed by
`public.stats_dim_key(dim, …)` (SQL-language, IMMUTABLE, inlined). A cell with `keys.length = L` is the rollup
at level L (L = 0 is the whole sample, per column value). Statistics are `null` below `min_n`. Measured live
2026-10-19: five dimensions + a column over 60k properties 381 ms; two dimensions over 139k rows 493 ms. A refused
payload still carries the counts and the cohort facts; `cells` is empty.

### Saved board — `public.stats_dashboards(account_id pk, charts jsonb, updated_at)`

One row per account. Read by the SPA through RLS (`current_account_ids()`); written only by
`PUT /stats/dashboard {charts: [...]}` (tenant connection, `require_account_id`, whole-document upsert). A chart
is `{id, view, rows, cols, measure, stat}`; `stat` (one of the chart stats, default `median`) is read by line
and bar only; the title is derived, never stored. No row ⇒ the SPA shows `DEFAULT_CHARTS` and the first save
persists the whole list.

### SPA — `frontend/src/lib/stats.ts` + `frontend/src/components/stats/`

- `lib/stats.ts`: the types above; `fetchStatsPivot(filters, spec)` = `resolveBrowsePrefilters` +
  `supabase.rpc('browse_stats_properties', {...buildBrowseStatsArgs(f, resolved), spec})`, query key
  `[browseKeys.root.stats, f, spec]` (so `browseInvalidation`'s aggregate root keeps working);
  `fetchDashboard` / `saveDashboard`; `cellTree` (cells → nested rows, children by key prefix); `sortKeys`
  (enum/bool by registry order, unknown members after, bands numeric, places `localeCompare('cs')`);
  `keyLabel` (band ⇒ "80 – 90 m²" / "5 000 – 10 000 Kč/m²" from the step for the payload's basis; enum ⇒
  `label_cs`); `measureUnit` (price ⇒ `PRICE_PERIOD_UNIT` by basis, ppm2 ⇒ `PPM2_UNIT[ppm2BasisFromToken]`,
  area ⇒ "m²", tom_days ⇒ "dní"); `chartTitle`; `DEFAULT_CHARTS`; the refusal texts (Czech).
- `components/stats/StatsDashboard.tsx`: the tab. Reads the board, renders one `StatsChart` per chart, "Přidat
  graf" at the end, saves through the API, invalidates.
- `components/stats/StatsChart.tsx`: one card — derived title, `SampleLedger`, the view, Upravit / Odebrat; in
  edit mode the card flips in place to `ChartBuilder` (same card, same width), Uložit / Zrušit.
- `components/stats/ChartBuilder.tsx`: a palette of dimension chips grouped (Vlastnost · Cena · Místo ·
  Vybavení), the shelves Řádky (six slots, sortable) and Sloupce (one), the Hodnota well, the view picker and
  the stat picker (line / bar). Drag with dnd-kit; every drop also has a click path (a chip click appends to
  Řádky; a slot's × removes) so the keyboard works. Rules enforced in the UI exactly as in the registry: a
  dimension once, six rows, one column, box ⇒ no column, line / bar ⇒ one row; dimensions outside the
  cohort's category (area bands off byty) are shown disabled with the reason. The preview re-queries as the spec
  changes.
- `components/stats/SampleLedger.tsx`: the signature mark. One line — "Ve filtru 73 537 · vzorek 60 123 ·
  vyřazeno 13 414 (chybí údaje)" — over a thin three-segment bar in ink tints, plus the cohort chips (byt ·
  prodej · aktivní). The same component on every chart, before the view.
- `components/stats/views/PivotTable.tsx` (nested rows with indent, a column dimension ⇒ one value per column
  showing the chart's `stat` with n beneath; no column dimension ⇒ the seven statistics), `PivotLines.tsx` and
  `PivotBars.tsx` (recharts, axes through `lib/chartAxis.valueAxisSpec`, series = column values: copper first,
  ink tints after), `views/BoxTable.tsx` (THE box primitive: one shared axis from every row's p10–p90, nested
  rows indented, n per row, a dashed placeholder for a cell under `min_n`; no tooltips — the numbers are
  columns of the same table).
- Refusals render as one sentence in the card body; the ledger still shows the counts.
- `BrowseExperience.tsx`: the stats tab renders `<StatsDashboard filters={filters} />`; `statsQuery`, the
  annotations query, `BrowseStatsView` and their props are gone.

### Deleted

`frontend/src/components/BrowseStats.tsx`, `PriceBandVelocity.tsx`, `region/DispositionBoxPlots.tsx` (and the
`region/` folder: `RangeStrip` moves to `components/estimation/`), `fetchBrowseStats`, `BrowseStats*` /
`TomBox` / `Ppm2Box` / `RegionDispositionRow` / `PriceBandVelocityRow` types, `fetchRegionDispositionAnnotations`
and its types, `pricePeriodOfCohort` / `mixedBasisCause` if nothing else reads them (check first);
`toolkit/region_annotations.py`, its route, schemas, tests and `llm_client` entry; migration 627 drops
`region_disposition_annotations` and the two `llm_region_annotation_*` app_settings rows (destructive: applied
only on the operator's word, after the code is gone). Census rows in `toolkit.measures.REGISTERED_SITES` for the
deleted files are replaced by rows for the files that now carry the unit strings, with recounted hits.

### Rails

- `tests/test_626_stats_dashboard.py` (offline): the effective `stats_dim_key` text equals
  `dim_key_function_sql()`; the stats statement contains `measure_value_sql()`, `v_min_n constant int := 10`
  and `v_max_cells constant int := 2000`; 626's cohort WHERE equals 625's; the signature is 625's 74 parameters
  plus `spec jsonb default null`; `--check` of the generated TS passes.
- `tests/test_stats_pivot_live.py` (CI DB job, a `migrations.yml` step): seeds two dispositions × three stavs
  (one NULL) with prices and areas; asserts filtered / sample / excluded, the rollup levels, `min_n` nulling,
  `mixed_category`, `mixed_status`, `too_many_cells` (rows on a high-cardinality key), `bad_spec`, and the band
  keys for rent vs sale.
- `tests/api/test_stats_dashboard.py`: PUT validates through the registry (422 on a bad chart), upserts, scopes
  to the caller's account.
- Updated: `test_browse_map_read_contract` (`_STATS_ONLY_PARAMS`), `test_one_place_predicate` (latest = 626,
  the parameter order), the two per-migration pins (602, 598 files), `test_measure_price_per_m2` (the stats arm
  reads the pivot or is dropped in favour of the one-measure census), `test_portal_rule` (live call shape),
  `tests/api/test_watchdog_browse_one_measure`, the measure census.
- SPA: `stats.test.ts` (tree, sort, labels, title, defaults), a test per component (builder rules, ledger,
  table shapes, box axis from p10–p90, refusal texts), `BrowseExperience.test.tsx` updated.

### Deploy

Number 626 (re-check `git ls-tree --name-only origin/main migrations/` before the apply). Apply through
`apply_migration.yml --ref feature/stats-dashboard` (dry run, then live) immediately before merging; the old SPA
sees dashes for the rollout minutes. The apply restarts nothing; the merge redeploys the API and the worker, so
it waits for the build flag (`autodedup.settings rt_bootstrap:rt` = false). Then the Railway commit status and
a read-only browser check of the Stats tab on production.
