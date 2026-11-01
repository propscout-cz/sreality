# W7: RÚIAN loader — full partitions, read levels only (as built, #2342)

Rulings: Q1 (quarter = část obce; MOMC stays a match key), Q10 (Praha's okres = 9999 under ČÚZK's own
name), Q13 (destructive steps pre-approved, each with an R2 backup and a receipt), and of 2026-11-01:
hold the 11-01 load and dispatch vintage 2026-10-31 after the merge; ORP and POU retire too; the
standing shadow gate runs. Numbers measured on prod 2026-11-01 unless dated.

## The rule
**Levels loaded = levels bound, served or used as match keys, and every served level is a full
partition.** `LEVEL_ORDER` is stát, region soudržnosti, kraj, okres, obec, MOMC, část obce, KÚ. So
`spravni_obvod`, `zsj`, `orp` and `pou` retire. **The okres of an obec is the one ČÚZK's state SHP
pack publishes on that feature** (`OBCE_P.OKRES_KOD`); the CSV chain's `OKRES_KOD` is compared, never
used. That is what gives Praha okres 9999 with no constant: the CSV chain dropped Praha's technical okres
when RÚIAN 3.4.1 abolished 3100 (2022) and is the only product that omits it.

## What was found (the reasons)
- **The skipped feature is OKRESY_P 9999.** v3's `boundary_skipped_no_unit` = 1 of 20,031 features; the
  pack's OKRESY_P has 77 rows, the mirror 76 okres units. `KOD 9999, NAZEV "území Hlavního města Prahy"`.
- **Agreement.** OBCE_P's `OKRES_KOD` on all 6,258 obce; its pairs equalled the mirror's okres parent for
  all 6,257 non-Praha obce (md5 of the sorted pairs, 2026-10-31). Switching the source moves only Praha.
- **No unit or street had ever been SCD-2-closed in production** (0 of 58,904, 0 of 83,476).
- **Praha in the serving layer.** 179,398 `listing_location` rows with `obec_kod = 554782` and okres
  NULL (49,900 active), 70,338 `browse_list` rows (23,841 active); no other obec has one.
- **The SO tie.** 463 Praha rows (199 active) sit at quarter grain with `cast_obce_kod NULL`: a "Praha N"
  part claim binds the SO, which wins the tie by unit id, and FILL maps nothing for `spravni_obvod`. 0
  such rows outside Praha.
- **No reader, re-checked on main at 699a8e206** (repo: `location_data`, `api`, `toolkit`, `scraper`,
  `autodedup`, `scripts`, `frontend/src`, the extension; live `pg_proc`, `pg_views`, `pg_depend`): none for
  the four levels, for `orp_of`/`pou_of`/`momc_in_so`, or for the six columns. `listing_location` has no
  `orp_kod`/`pou_kod` since W2-b, and no live unit hangs under a retired level.

| retiring | live units | also |
|---|---|---|
| `spravni_obvod` | 22 (one named "Kutná Hora") | 12 name-index rows a version, 452 geometry rows of office polygons |
| `zsj` | 23,604 (all named by their code) | 0 name-index rows |
| `orp` | 206 | 368 name-index rows a version; `orp_of` 18,774 relation rows; 33,238 geometry rows |
| `pou` | 393 | 723 name-index rows a version; `pou_of` 18,774 relation rows; 43,792 geometry rows |

24,225 units of 58,904 (41 %). `momc_in_so` holds 282 rows. Six dead columns: `ruian_admin_units.nuts_lau` (0
non-null), `definition_point` (0 non-null; the one read was `coalesce(u.definition_point, gp.pt)`, which
never fired), `has_polygon`, `display_path` (written, never read); `ruian_address_points.praha_obvod_unit_id`,
`momc_unit_id` (the change log keeps both in its before/after JSON).

## What changed
- `ruian_boundaries.pack_okres(pack)` reads `OBCE_P.dbf` and `OKRESY_P.dbf` straight from the zip member (no
  geometry, no extraction): `PackOkres(obec, okresy)`. A missing layer or field raises
  `BoundarySchemaError`; `dry_run` calls it, so `dry_run=true` proves the fields before any DB write. A
  resume reads the vintage's archived pack, so one version never reads two.
- `load_assertions.okres_partition` (blocking): every staged obec has an okres in OBCE_P and every such code
  is an OKRESY_P feature. `okres_csv_disagreements` is counted into `row_counts` (`okres_csv_disagreements`)
  and logged, so a January okres change shows; Praha's blank CSV okres is no disagreement.
- `ruian_load.build_unit_rows(conn, stage, obec_okres)`: both `if okres: … else: …kraj` branches, the `pou`
  loop, the SO name source, the SO `add`s, the `op_kod` loop and the ZSJ `add` are gone. No obec hangs off a
  kraj any more. `upsert_relations` writes `katastr_in_obec` only.
- **Staging keeps only what is read.** The `pou` stage and strukt member are gone; `chain` is `adm_kod` alone
  (3.02 M rows × 11 fewer bigints a load); `_copy_strukt_member` names CSV headers and projects them, while
  `iter_strukt` still checks each member's whole header. The address-row parser stays a faithful 19 columns.
- `ruian_boundaries`: `ORP_P` and `POU_P` leave `LAYERS` (6 of the pack's 12 layers load); `COMPLETE_LEVELS`
  gains `okres`. `name_index._UNIT_LEVELS` follows `LEVEL_ORDER`.
- Resolver, dead references only, no `RESOLVER_VERSION` bump: `PART_LEVELS = ("cast_obce", "momc")`; no `"zsj"`
  in `bind` / `core`; `resolve_db`'s by-name reads select a NULL point, the chain reads `gp.pt`.
- CI: one Praha tree (`mini_mirror._praha_tree`: kraj 19, okres 9999, obec 554782); the fake okres 3100 and
  its name collision with kraj 19 are gone. `test_registry_reparent_live.py` (schema-replay job) drives the
  first close through the loader's own statements and replays the migration over seeded rows.

**Effect on the tree.** Okres 9999 is born under kraj 19 with placeholder name "9999"; the boundary phase
loads OKRESY_P 9999 like any okres (render 83 m) and `upgrade_name` sets "území Hlavního města Prahy". Obec
Praha's path becomes `t1.g19.k19.o9999.b554782`. FILL writes `okres_kod 9999` and the name through the
ordinary chain walk.

**The first close, at the first W7 load:** okres 9999 opened; obec Praha and its 281 children (112 části, 57
MOMC, 112 KÚ) close and reopen; 7,675 Praha streets close and reopen; 134,676 Praha address points update in
place (one change-log row each, about 135 MB); about 114 units re-derive geometry, because carry-forward is
keyed by unit id (Praha, 112 KÚ, okres 9999).

## Consumers
- **Statistics, the one visible change:** okres pivots gain a cell "území Hlavního města Prahy" (about 23.8k
  active properties, the largest okres, until now "vyřazeno"). Kraj pivots do not move.
- **Filters, map, watchdog: no result change.** Chip arms are `okres_id = any(codes)`; no saved preset (16),
  dashboard (3) or live watchdog (8) holds an okres (2026-10-31); an excluded okres chip keeps Praha.
- **Labels:** `location_display_label` reads no okres. **Envelopes:** the `district` field becomes the name
  instead of null for Praha; summary caches key on `(sreality_id, snapshot_id)`; no estimate reads okres.
- **Operator picker:** `operator_corrections` writes `okres_name` from the obec's okres parent, so a Praha pick
  now carries ČÚZK's name, which binds okres 9999 once the version is live.
- **Autodedup, no file touched.** `COHORT_LOCATION_SQL` has no okres, so no decision module reads it.
  `apply_scope` reads `okres_kod` for `district:` blocks: no scope holds `district:9999` and Praha enters
  through `town:554782`. `contains()` answers okres 9999 → obec Praha by construction. Its docstring "Praha is
  no okres" (`autodedup/incremental_scope.py`) goes stale: fix it with the next edit to that file.
- **Resolver confidence: unchanged.** Portal okres claims ("Praha", "Praha N", "Hlavní město Praha") do not match
  ČÚZK's name, as today. **Legacy `admin_boundaries`** already says 9999; both registries now agree, which W8 needs.
- **Transitional, the 463 rows (199 active):** with the SO gone, "Praha N" binds MOMC "Praha N" and FILL's `momc`
  arm writes a quarter code until W5 moves them to obec grain. The autodedup quarter blocks and the new-dedup
  district veto start seeing it. This is what the shadow gate is for.

## Migration 718 and the rollout
`migrations/718_location_w7_registry_read_levels.sql` retires the four levels (`valid_to`, `retired_at`, nothing
deleted: the loader never closes a unit it stops staging), deletes the three relations and drops the six
columns (metadata only, no rewrite). It takes effect at once: `_ADMIN_BY_NAME_SQL` and `maps._PART_LOOKUP_SQL`
join `u.valid_to IS NULL`, so v3's name-index rows for those units stop binding. Name-index rows stay (rebuildable)
and geometry rows stay (registry history; 77,482 rows, 279 MB of geometry): their removal is its own ruling.

**The order is forced in both directions.** Pre-W7 code reads `definition_point` and writes `display_path`, so
the migration waits for the deploy. W7's loader omits `display_path`, which is NOT NULL until the migration
applies, so the load waits for the migration (run before it, it fails at the first `upsert_units`, moving nothing).

**The label guard lets the dispatch through.** `recorded_version('ruian:2026-10-31')` finds nothing: the 11-01 cron
was cancelled before any step, `registry_versions` holds v1–v3 only and 0 staging tables are left. The vintage's
two CSV zips answer 200 on HEAD. A vintage already recorded could not carry W7: a current one only finishes, a staged
one resumes from its checkpoints and keeps its old tree. Registry liveness goes red when v3 passes 40 days, about 11-10.

1. **Wave shadow, from the branch once it has merged main** (`wave-shadow.md`):
   `gh workflow run location_wave_shadow.yml --ref feature/location-w7-registry-read-levels -f wave=w7-registry -f claims=none -f scope=apply -f ids_sql="SELECT ll.listing_id FROM listing_location ll WHERE ll.obec_kod = 554782 AND ll.granularity = 'cast_obce_or_quarter' AND ll.cast_obce_kod IS NULL"`
2. Merge. Confirm the worker and api rollout: `gh api repos/propscout-cz/sreality/commits/<sha>/status`.
3. Back up, read the receipt:
   `gh workflow run backup_before_drop.yml -f label=w7-registry -f targets="ruian_admin_units:id,display_path,has_polygon ruian_address_points:kod_adm,praha_obvod_unit_id,momc_unit_id ruian_admin_unit_relations"`
4. Apply 718 through the MCP (a tripped 5 s lock timeout: apply again). Verify: 0 live units at the four levels, 24,225
   with `retired_at`, 37,830 relation rows gone, the six columns gone.
5. **Wait until no full sweep is queued:** `select count(*) from dirty_locations where reason = 'full_sweep'` = 0. See the
   window below.
6. `gh workflow enable location_registry_load.yml`, then `gh workflow run location_registry_load.yml -f vintage=20261031 -f dry_run=true`
   (proves `OKRES_KOD` on the day's pack), then `gh workflow run location_registry_load.yml -f vintage=20261031`.
7. Publish. W2's `drain.reconcile` sees the new label on its next pass and enqueues every row whose `registry_version`
   differs, no hand step. At the measured 171k rows/h a corpus of 1.09 M takes about 6.4 h. Then § Acceptance.

**The load window.** Units close about 8 minutes into a load, and it publishes about 5.5 hours later (v3: 16:55 to
22:31; two layers fewer now). In between, a resolve pinned to v3 cannot bind Praha by name, because its v3 name-index rows
point at closed ids. A pin still finds Praha (`containing_obec` is pinned to v3's geometry); a row with no pin comes
out townless, and is served again once the post-publish sweep repairs it. Measured 26,477 Praha resolves in one hour
while a full sweep runs, against about 30 an hour at rest (design, 10-30), so step 5 is the difference between about 160
rows and about 145,000. A versioned unit predicate would not close the gap: address points are current-only by design.

## W5 (PR #2254) and the merge order
Main keeps working once the units close, with or without W5: every by-name read filters `u.valid_to IS NULL`, so a closed
level simply answers nothing. W5 names none of the retired levels either.

They conflict textually and only textually: 6 files (`bind.py`, `composite.py`, `core.py`, `fill.py`,
`test_resolver_check.py`, `test_resolver_katastr.py`), every hunk W7's one-liner against W5's rewrite of the same lines.
Taking W5's side of all six and rewriting `o3100` to `o9999` in the tests (trial merge of 81d27d84b, 2026-11-01): 345 resolver
and loader tests pass. Neither wave needs the other's data.

**The order changes the data, not the code.** W5 first: the 463 rows go straight to obec grain, W7's shadow has no movers, and
the two sweeps can be one if W5's deploy and the publish fall together. W7 first (Q14): those rows carry a MOMC code from the
publish until W5, the shape Q1 rejects, and two corpus sweeps. W5 first if it can land before registry liveness goes red
(about 11-10); otherwise W7 first and accept the transition.

## Acceptance (after publish and W2's sweep)
1. 0 rows, and 0 active: `select count(*) from listing_location ll join listings l on l.id = ll.listing_id where ll.obec_kod = 554782 and ll.okres_kod is null` (and with `l.is_active`). Every Praha row: `okres_kod = 9999`, `okres_name = 'území Hlavního města Prahy'`.
2. `browse_list`: 0 rows with `obec_id = 554782 and okres_id is null`.
3. `browse_stats_properties` with `rows => ['okres']` has a cell keyed 9999 named "území Hlavního města Prahy" (about 23.8k); "vyřazeno" falls by the same; kraj pivot counts unchanged.
4. Mirror: 77 live okresy; obec 554782's parent is okres 9999, path `t1.g19.k19.o9999.b554782`; 0 live obce whose parent is a kraj; okres 9999 has pip, authoritative and render rows at the new version; `row_counts.boundary_skipped_no_unit = 0`, `okres_csv_disagreements = 0`.
5. 0 live units at the four levels, 24,225 with `retired_at`; the new version's name index holds 0 rows for them; 0 `momc_in_so`/`orp_of`/`pou_of` rows.
6. `information_schema.columns` returns 0 for the six columns; index `ruian_admin_units_defpoint` gone.
7. 0 non-Praha `listing_location` rows change okres in the sweep.
8. Every row carries the new `registry_version`. Praha quarter-grain rows with `cast_obce_kod NULL` = 0 (from 463).

## Risks and what stays open
- **ČÚZK changes the pack.** Dropping 9999 or the field fails the load loudly (`okres_partition`, `BoundarySchemaError`, the okres completeness gate): a ruling, never a silent NULL. The same if ČÚZK splits Praha into okres-level obvody.
- **Latent mirror defect (not W7).** The loader never closes a unit or street RÚIAN dropped; 1 street is live that v3 no longer contains.
- **`katastr_in_obec` has no reader either** (39,222 rows, 13,074 a version, one obec per KÚ, so nothing beyond `parent_id`). It is the last relation, so removing it retires `ruian_admin_unit_relations` whole. Kept: not in the ruling.
- **`ruian_name_index.parent_okres_unit_id`** is written and read by nothing (`parent_obec_unit_id` is read). Kept: not in the ruling.
- **Draft PR #1634** (MF PR-F, never applied) also drops `has_polygon`; remove its line when it is revived.
