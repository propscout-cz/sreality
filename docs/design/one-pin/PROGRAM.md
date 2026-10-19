<!-- STATUS: APPROVED 2026-10-19. Rulings: R1 most precise even if inactive; R2+R10 retire the matview, no backup; R3 pointer; R4 "properties"; R5/R7/R8/R9 defaults; R11 drop the dead MF functions (verified off every live path); R12 no temp index. Execution: W0 done; W1–W3 delivered (W1 #1887 = 630, W2 #1896 = 631, W3 #1895 = 635, each merged and applied); W4 = 636 (this PR). Investigation artifacts: ~/one-pin-artifacts on the operator machine. -->

# ONE PROPERTY, ONE LOCATION, ONE MAP: the approved plan (rulings taken 2026-10-19)

Every figure below was measured read-only on production on 2026-10-19, or is a round-one figure that survived the refuters. Estimates are labelled as estimates. Dedup merges run in parallel, so expect drift of a few units in every count.

## Your three questions

**1. When ads are merged, which address does the property get?**

Today a property shows the whole location row of its **canonical ad**: the point, its precision, the address label and the town/quarter/street codes. The canonical ad is rank 1 of the canonical order in migration 588:60-76:
1. active ads first;
2. then ads with any map point;
3. then the earliest first seen (latest last seen among inactive ads);
4. then portal trust, then id.

Precision is not part of that order. Only "has a point or not" counts.
- **Your property is 15100.** All five of its ads are active. Its canonical ad is sreality 40102, which sits on a street centroid (the middle of the street, ±300 m) with the label "Pod Sychrovem II, Praha".
- **The ad your merge brought in is 13193960.** It has the exact RÚIAN address point 850/43 (±10 m), about 94 m away, and nothing shows it.
- 40102 wins only because it was first seen earlier: 2026-05-09 against 2026-08-03 (one-pin-verdicts.md:89).

**2. Is that the only pin?**

Yes, once the map has caught up. The map draws one pin per property (824,958 map rows = 824,958 property ids; one-pin-verdicts.md:39). The two pins you saw were two properties:
- the survivor 15100, the open ring;
- 604858, the property your merge had just retired, the solid dot at 850/43.

The map's table is rebuilt only at :07 and :37, and a merge never updates it. The retired pin left at the 13:37 rebuild, which swapped in at 13:38:00Z.

The list had its own hole, which round one missed:
- Your merge's transaction started at 13:30:01.119Z, 1.1 s after the 13:30 list rebuild had taken its snapshot.
- The merge's patch to the list was thrown away when that rebuild swapped in at 13:31:02.
- 604858 should have been back in the list from 13:31:02 to 13:46:01Z. This is derived from code and timings, because no row history exists.
- So your "1 of 1" list next to 2 pins was fetched between the merge's commit and 13:31:02.

Separately, property 603623 is an unmerged idnes property at the same point as the retired pin. Your sreality chip hid it.

**3. What should the one pin be?**

The location of the property's **location ad**, which is a new term. Everything that comes from a location moves together:
- the point;
- the dot-or-ring face;
- the label;
- the town, quarter, street and KÚ codes;
- the MF inputs;
- the city figures;
- the served rule.

The order that picks the location ad (your ruling R1: the most precise ad, even if it is inactive):
1. an ad with a map point, then an ad determined foreign;
2. the finest RÚIAN rung;
3. ties broken by the canonical order, which puts active ads first.

For 15100 that means one solid dot at Pod Sychrovem II 850/43, ±10 m, labelled "Pod Sychrovem II 850/43, Praha". Price, category and photos stay with 40102: komerční / ubytování, 25 990 000 Kč.

The map will also read the same patched table as the list. A merge then removes the retired pin in its own commit, even when it lands during a rebuild.

---

## North star

> **A property has exactly one location: the location of its location ad. The choice is stored once, as a pointer. Every surface that places the property reads through that pointer. A merge, detach or split changes the list, the map and the counts in its own commit, including when it lands while a rebuild is running.**

Each change in this sprint must pass one test. Does it make the location:
- (a) one per property;
- (b) read through the one pointer; or
- (c) updated in the identity change's own commit?

A change that does none of these is out of scope.

## Terms used below

- **Ad / property.** An ad is one portal's advert. A property is the unit it advertises; several ads can belong to one property (GLOSSARY).
- **Canonical ad.** The ad whose price, area, description, photos and link a property shows (GLOSSARY.md:27-30). It is unchanged by this plan.
- **Location ad (new).** The ad whose location a property shows. It is often the canonical ad, but not always.
- **Live / dormant property.** A live property has at least one active ad. A dormant property has none. Dormant properties ARE on the default map: the default filter is `status: 'any'` (frontend/src/lib/filters.ts:322); `is_active` is filtered only for 'active'/'inactive' (queries.ts:268-271); the projection keeps every `status='active'` property (625:295).
- **Served.** Rule 25's test for whether a surface may show a location: the ad has a point, or it is determined foreign (location_data/claims_common.py:43-47).
- **Granularity rung.** How precisely the resolver matched an ad to the RÚIAN register, from address point (±10 m), building and parcel, through street (±300 m) and quarter, down to town, district, region and country. The map draws a solid dot at rank ≥ 90, otherwise a ring (frontend/src/lib/uncertaintyCircle.ts:29).
- **Read model.** A pre-built table the app reads instead of computing the answer on every request.
- **Materialized view (matview).** A saved query result that Postgres can only rebuild whole; it cannot be patched one row at a time.
- **Rebuild / swap.** The list table is rebuilt every 15 minutes:
  1. copy the projection into a new table;
  2. build its indexes;
  3. drop the old table and rename the new one into its place.

  The last step is the "swap".
- **Patch race.** A write that patches the old list table after the copy started is lost at the swap.
- **pg_cron.** The database's built-in timer.
- **Rollup.** The property recompute (`scripts/recompute_property_stats.py`) that writes a property's summary from its ads.
- **Pointer.** A column that holds another row's id. It copies no data, so it cannot drift.
- **CHECK constraint.** A rule the database itself refuses to break. Any write that would break it fails.
- **UNLOGGED table.** A table Postgres empties after a crash, in exchange for cheaper writes. `browse_list` is one (toolkit/browse_read_model.py:3).
- **Latency target (SLO).** The slowest a query may be before we act.
- **md5 gate.** A migration first checks that the function it rewrites is byte-for-byte the version it expects, and refuses otherwise.
- **CI rail.** An automated test that fails the build when a rule is broken.
- **Backfill.** Filling a new column for existing rows.

---

## What happened, re-verified (read-only, 2026-10-19)

| Time (Z) | Event | List (`browse_list`) | Map (`properties_map_mv`) |
|---|---|---|---|
| 13:07 | map rebuilt | — | 15100 (ring) and 604858 (dot): two properties |
| 13:30:00.03 | list rebuild starts; its copy sees 604858 as active | old table still served | unchanged |
| 13:30:01.119 | your merge's transaction starts, patches the OLD list table, commits | 1 row (correct) | **2 pins** |
| until 13:31:02 | **your view was fetched in this window** ("1 of 1", 2 pins) | 1 row | 2 pins |
| 13:31:02.27 | list swap: your patch is discarded with the old table | 604858 back (derived) | 2 pins |
| 13:37:00 to 13:38:00.7 | map rebuild swaps in | 604858 still listed (derived) | 1 pin: 15100 at the street centroid |
| 13:45:00 to 13:46:01.6 | next list rebuild swaps in | 1 row | 1 pin |

Sources: cron.job_run_details, property_merge_events id 145323; `created_at` is the transaction start (toolkit/property_identity.py:114-124).

**Defect A: freshness.** Browse has two read models built from one view, `browse_projection`, whose latest definer is migration 625:204-298.
- **Map table (`properties_map_mv`).** Rebuilt only by pg_cron at :07/:37 (522:356-361) and never patched. Pins and tile counts go stale together for up to 31 minutes after every merge. Autodedup retired 13,036 properties on 10-16 alone (one-pin-understanding.md:697).
- **List table (`browse_list`).** Patched inside every identity change: `properties_changed` → `sync_browse_list` (recompute_property_stats.py:437-438; browse_read_model.py:44-74). A patch that commits between the rebuild's copy and its swap is lost (605:650-722):
  - that window is 61.6 to 62.5 s of every 900 s, mean 61.9 s, or 6.9 %;
  - over 72 h, about 90 % of autodedup patches survived (one-pin-verdicts.md:242);
  - 1 of the last 5 operator merges did not survive, yours included.
  - Nothing repairs a lost patch before the next rebuild. Merges never enqueue `dirty_properties` (recompute_property_stats.py:417-418), and a retired property has no ads left to dirty it.

**Defect B: selection.** The canonical order uses location only as `(ll.geom is not null) desc` (588:68). Of the 67,885 properties with two or more ads, the share showing an exact dot today is:
- 36.5 % of the 22,152 live ones;
- 35.1 % of the 45,733 dormant ones.

For 13,784 of these properties the most precise ad is not the canonical ad (all ads ranked by precision, canonical ties). The rulings below show exactly how many each option fixes.

**Not a defect.** There is no client-side grouping and no per-ad pin lane. In steady state the map holds one row per property.

---

## The definition: a property's location ad

`properties.location_listing_ref_id` points to the ad that ranks first by:

1. **Served tier**, best first: has a map point (2) > determined foreign (1) > anything else (0). This puts rule 25's served test first. The property is served exactly when any of its ads is (measured: 0 newly unserved in every slice).
2. **Granularity rung**, finest first (`location_granularity_rank.rank desc nulls last`, migrations/380:254-265), whatever the ad's status (ruling R1). This is the same rank the map's dot rule uses.
3. **The canonical order** (588:66-72), as the only tie-break. It puts active ads first, so among equally precise ads the active one wins.

**It is never NULL while the property has an ad, and the database enforces it:**
- **Both writers set it in the same statement as the canonical ad:**
  - the singleton birth: `scraper/db.py` `NEW_SINGLETONS_SQL`, :596-607, the one birth path, also used by a split's new property via property_identity.py:719;
  - the rollup: recompute_property_stats.py:231-234.
- **The no-ads reset (`_NO_ADS`, :339-352) nulls both.**
- **W4 adds `CHECK ((repr_listing_ref_id IS NULL) = (location_listing_ref_id IS NULL))`.** A property has a canonical ad exactly when it has an ad, so a write that sets one pointer without the other fails.
- **Retired or missed rows are covered by one status-agnostic fill after the sweep.** Every row still NULL with a canonical ad gets `location = canonical` once (the 120,013 `merged_away` rows and any property merged away before the sweep reached it). That keeps the CHECK true when a detach revives one (`_REACTIVATE_SQL`, property_identity.py:225-231); the inline rollup then picks the real location ad.

**Spelled once.** The order is a second window, `location_rank`, inside `property_canonical_listings`, next to `canonical_rank`. It ties back to `canonical_rank` instead of restating it. The rollup stores rank 1 the same way it stores the canonical ad (its `canon` CTE, recompute_property_stats.py:104-111).

**What follows the location ad** (`listing_location ll ON ll.listing_id = p.location_listing_ref_id`):
- the point, the label and the precision rank and radius;
- the five RÚIAN codes;
- the MF reference rent and yield inputs;
- the city-proximity point;
- the served rule.

**What stays with the canonical ad:** price, category and subtype, area, photos, link, description and the per-field physical facts. The canonical order is unchanged (R3).

**Which surfaces follow which ad.** A surface that places a PROPERTY reads the location ad:
- Browse list, map and counts;
- the property page and the phone map;
- the watchdog matcher;
- notifications (R5);
- the estimation subject (R7);
- comparables (R8);
- the extension's label next to its property MF (R9).

A surface about ONE AD keeps that ad's own location:
- the ad page (`listings_public`);
- `location_pin_audit_mv` (524:59);
- autodedup;
- the location census.

No single response mixes the two.

**Why the most precise ad, even if inactive (R1, your ruling).** The location is a fact about the building, not about the advert: an exact address point stays exact after its ad closes. So the finest evidence wins whatever the ad's status, and the canonical order (active first) only breaks ties.
- Against active-first this adds 1,036 more dots on live properties (exact pins 47.1 % → 51.8 %).
- The accepted cost: 1,376 live properties take their location from an ad that has been gone a median of 73 days (p90 129 days).
- In a sample of 830 of those 1,376, the dead ad's point sits inside the live ad's own circle in 62 % (within 1 m in 28 %); in 24 % it lies more than max(1 km, 2× radius) away. Both orders leave the far-apart count unchanged, so this is not a merge-quality risk either way.

**Why the finest rung and not a consensus or centroid.** The ads are independent, noisy measurements of one building, and each carries its own noise as rung and radius.
- Where every ad is exact, they agree: p90 0 m, p99 34 to 82 m depending on the population (one-pin-verdicts.md:113, :134).
- Approximate circles contain the exact point 98.5 % of the time at street level, but only 86.1 % at quarter and 60.4 % at town level. The misses come from town and quarter radii drawn too small, not from ads disagreeing.
- A centroid would invent a point no source claimed.

**Measured caveat: the rung ignores match confidence.** In the sampled slice (property ids 1-158,046, active-first order), 87 of the 4,979 changed location ads carry `match_confidence = 'low'`: 6 of 407 live, 81 of 4,572 dormant. 77 of them sit at an exact rung. (Re-verified read-only by the second critic; W0 re-runs and records the query.) The map already draws such points as solid dots today whenever they are canonical, so this is not a new class. Whether a low-confidence point should drop a rung is a location-track question (out of scope).

**Why a pointer, not a pick at read time.**
- **Rule 25 still holds.** A pointer stores no location value; `properties` still carries no location (mig 508).
- **One spelling instead of ten.** A pick at read time would spell the order inside every reader: 3 database objects and 7 code sites today.
- **City proximity can see a change.** It recomputes only rows whose `city_proximity_computed_at` is NULL (508:1044), and only a stored choice can reset that stamp when the location ad changes.
- **The alternative needs no column but moves price.** Folding the order into the canonical order is put to you in R3.

---

## Architecture: now → after

| | Now | After |
|---|---|---|
| Browse read models | 2: `browse_list` (patched) + `properties_map_mv` (timer only) | **1**: `browse_list` |
| A merge reaches list / map / counts | list in its commit unless it lands in the 6.9 % rebuild window (then lost until the next swap); map ≤31 min | all three in the merge's commit; a merge inside the rebuild window is re-patched into the new table before the swap |
| Map tile counts | `browse_map_cells` over the matview | the same function over `browse_list`, plus `lat/lng is not null` |
| Map index | `properties_map_mv_cover` (92.4 MB) | `browse_list_map_idx`, partial, same key, `property_id` in place of `sreality_id`, built by the */15 rebuild |
| `browse_list` heap order | category_main, category_type, first_seen_at | **category_main, category_type, lat, lng**: the matview's layout, so the map index is cheap (W1 receipts, 2026-10-19) |
| A property's location | the canonical ad's row, read keyed on `repr_listing_ref_id` | the location ad's row, read keyed on `location_listing_ref_id`; CHECK + CI rail |
| Canonical order | 588 | unchanged |
| Estimation subject location | 4 resolvers, 3 definitions | 1 shared join (R7) |
| Comparables location | each property's canonical ad's own point | the property's location ad (R8) |
| Extension panel | the viewed ad's label beside the property's MF | the property's label beside the property's MF (R9) |
| Notification address | the ad that was canonical at dispatch time | the property's location ad (R5) |
| Map pill noun | "listing(s)" (it counts properties) | "property / properties" (R4) |
| Dead MF functions in the database | 2 | 0 (R11, verified dead: no cron job, no workflow, no code or database caller) |

---

## Waves (each wave is one PR, verified before the next starts)

**Process for every wave:**
- Migration numbers start at **630**. 629 is taken by the curated-set-scope branch; re-check `ls migrations | tail` at branch time.
  - **[2026-10-19]** W1 = 630, W2 = 631; 632-634 went to other sprints, so W3 = **635** and W4 = **636**.
- Each branch is cut from `main` in an on-disk worktree, never in the shared working tree (another session's branch with uncommitted changes).
- Merges and deploys only while `autodedup.settings rt_bootstrap:rt` reads false; additive applies need no window.
- **Every wave's migration is applied and verified BEFORE its PR merges** (apply before deploy, the migration-438 incident class). W3's birth INSERT and W4's watchdog read name the new column, so code that deploys before its migration fails on every singleton birth.

### W0: preconditions and baselines (read-only, no PR)

- **Gate:** `629_curated_set_scope.sql` is merged to `main`. It is already live in production (browse_map_cells takes more than 80 arguments, and the 10-argument browse_list_visible exists), but it is committed only on `feature/curated-set-scope`. Record the live md5 of these bodies as W1's gates:
  - `browse_map_cells`, `properties_map_visible` and `browse_list_visible` (629);
  - `rebuild_browse_list` (605:650-722).
- **Baselines.** Re-take this round's receipts with the same SQL, hot and as the first call after a swap. All are inner SELECTs with `hide_dismissed = true`:
  - (a) your call: dům + komerční prodej, Praha bbox, sreality chip. Matview 31.1 ms; browse_list without an index 129.4 ms.
  - (b) byt/prodej nationwide + sreality: 215.3 / 222.6 ms.
  - (c) the whole country + sreality: 861.3 / 907.4 ms. Both use a seq scan.
  - the default byt/pronájem call;
  - the pin read at limit 2000: 15.5 / 61.2 ms;
  - the list page and the exact count;
  - `derived_artifacts.last_duration_ms` for `browse_list`: mean 61.9 s.
- **Rule out the other explanations for the second pin.** Run your exact filter read-only against current data. List every property and pin in it, including 59364 and 603623.

**W0 receipts, taken 2026-10-19 15:32Z** (file: `~/one-pin-artifacts/w0-receipts.md`, exact SQL for every number):
- Gates: one overload each, every live body equals its file; `rebuild_browse_list` be7b0ec2… (605), `browse_map_cells` 7406f856… (629), `properties_map_visible` 9c1351bb…, `browse_list_visible` a19987a9….
- Baselines, hot, matview / `browse_list`: (a) 29.7 / 72.6 ms; (b) 204 / 219 ms; (c) 1,501 ms cold / 904 ms; pin read 15.9 / 61.8 ms; list page 0.44 ms; exact count 49.7 ms. Every `browse_list` figure is inside its W1 target before any index exists.
- Replay: both relations return the same 108 properties for the Praha bbox, row for row; 604858 is in neither (`merged_away`); the sreality chip plus active status leaves only 15100, which is the operator's "1 of 1". No row on Pod Sychrovem II is at rank ≥ 90, so the solid dot could only have been 604858.
- R8: the spatial-first cohort shape reads 55.6k buffers against 42.5k for today's EXISTS (1.3×), both over the repr partial index; hence W3's twin index on the pointer.
- Ruled-order totals: 67,892 multi-ad properties; 13,776 change location ad; 10,790 rings become dots; 84 change town (35 with both codes known); no dot becomes a ring; low-confidence location ads 259 of 13,776 (1.9 %).
- Stray: one `browse-map-rebuild` row in `cron.job_run_details` reads `connecting` with a NULL start time; W2 looks at it before unscheduling the job.

### W1: one Browse read model, race closed (PR 1; migration 630 additive; SPA)

**Migration 630, md5-gated on 605's `rebuild_browse_list` and 629's `browse_map_cells`** (the repo's gate hashes `prosrc`, per name: `be7b0ec2297d269f495a42fdcc474517` and `7406f8566504c33efc7fe4273b4f76cc`, read live 2026-10-19 15:32Z; 629's author confirmed no further change before merge)**:**
- **`rebuild_browse_list()`, restated from 605:650-722 with three changes and nothing else** (the heap order, the map index, the re-patch):
  1. **[as built 2026-10-19] The heap order and the map index: three states measured, the matview's layout chosen.** The map index `browse_list_map_idx` is the matview cover's key and width, `property_id` in place of `sreality_id`, partial on a point. Through the function (targets (a) ≤150 ms, (b) ≤400 ms, (c) ≤1.2 s):
     - **The index on 605's first_seen heap** (first apply, 17:44Z): (a) 71 ms, (b) 399 ms, (c) 1,932 ms. The planner prices the index as heap-ordered (correlation 0.9999 on `category_main`) and pays one heap page per row.
     - **No map index** (second apply, 18:22Z): (a) 225 ms, (b) 285 ms, (c) 1,229 ms. (a) lost its bbox path; (c) missed by 29 ms.
     - **The retired matview**, laid out by category_main, category_type, lat, lng with the same index: (a) ~30 ms, (b) 204 ms, (c) 874 ms.
     - **Decision (2026-10-19, the named fallback below):** give `browse_list` the matview's layout. The CTAS orders by `category_main, category_type, lat, lng` (no point sorts last) and builds the map index again, so rows in index order sit on consecutive heap pages and the planner's correlation assumption is true. Cost accepted: see the risk "List page locality". The third apply lands over either earlier state (the gates accept all three).
     - **Third receipts** (after the 18:45Z tick): superseded by the operator's ruling (2026-10-19) accepting the whole-country tile count at 1.4 s.
  2. **The identity re-patch.**
     - At the start, read `v_since` = the previous run's `derived_artifacts.complete_through` minus 15 minutes.
     - After `drop table if exists browse_list` holds its lock and before the rename, delete from and re-insert into `browse_list_next` (from `browse_projection`) every property named by a `property_merge_events` row with `created_at >= v_since or undone_at >= v_since`:
       - its survivor, retired and previous property;
       - the current property of its moved ad.
     - At that point every identity change that wrote the old table has committed, and later ones queue and land in the new table.
     - Measured size per 15-minute window: average 13 ids, p95 44, max 474. Readers wait tens of ms typically and about 1 s at worst (estimate).
     - Built: ONE read after the DROP (a pre-DROP read cannot patch anything, and a cheap id-watermark delta is not complete: a plain detach only stamps `undone_at` on old rows and ids are not commit-ordered). 630 therefore also creates `property_merge_events_recent_idx` on `greatest(created_at, undone_at)` and reads `greatest(e.created_at, e.undone_at) >= v_since`, so the time under the ACCESS EXCLUSIVE lock is bounded by the window, not by the ledger (98 MB, +12 %/week). Measured: the window is ~30-45 min; an autodedup burst re-patches up to ~2k ids (p95 1,281, max 2,031 over 14 days); projecting 1,156 ids took 155 ms. If the previous run was skipped by the advisory lock, `v_since` reaches back one more window, which is safe.
     - This closes the ghost for every merge, detach and split. Splits and undos stamp `undone_at` (property_identity.py:148-151, :198-206).
- **`browse_map_cells`**, restated from 629, reads `from browse_list l` and gains `and l.lat is not null and l.lng is not null`.
  - Without that line it would count browse_list's 53,146 unmappable rows as a NULL tile: byt/prodej + sreality reads 54,101 rows instead of 51,904.
  - Its comment no longer claims "ONE index-only pass" (629:723-724).
- **No forced rebuild.** The next */15 tick lays the table out in the map index's order and builds the index; the receipts are taken after that tick.
- A revert file in `migrations/reverts/` restores 605's rebuild and 629's `browse_map_cells`.

**SPA:**
- `fetchMapPoints` reads `listSource`, i.e. `browse_list` / `browse_list_visible`. Delete `MAP_RELATION`, its `HIDING_DISMISSED` entry and `mapSource` (queries.ts:299, :306, :335 on main; :303 on the local branch).
- `MAP_COLS` (queries.ts:125) drops the four columns ListingMap never reads: `sreality_id`, `source`, `source_id_native`, `category_type`. The matching `MapRow` fields go too; the type check proves nothing reads them. (A subtraction rider: outside the scope test, kept because it is pure deletion in a file W1 already edits.)
- A new `queries.test.ts` case: for one filter, the pin read and the list read hit the same relation with identical filter parameters (only select, order and limit differ). This rules out a status or portal-rule mismatch between list and map.
- Pill noun per R4: "property / properties".
- Comments the move makes false: queries.ts:93, :104, :837-845; BrowseExperience.tsx:620, :1214; districtCodes.ts:26; measure.ts:13.

**Tests and CI (retargets, from inventory B):**
- **`tests/test_browse_map_cells_plan.py`** points at `browse_list_map_idx` and asserts the DEFAULT call's full column set, target list AND WHERE, is contained in the index [as built 2026-10-19: deleted with the index in the second iteration, restored with it in the third].
- **Other retargets:**
  - `tests/test_browse_map_cells_live.py:25-27, :54-55, :113` → `browse_list`. A table needs no park trick.
  - `tests/test_portal_rule.py:146-155`: the map case.
  - `tests/test_one_place_predicate.py:123, :248`: the pair becomes `("browse_map_cells", "browse_list")`.
  - The branch's `tests/test_browse_map_read_contract.py:168`.
  - `frontend/src/lib/queries.test.ts:768` and the :580 comment.
- **`.github/workflows/migrations.yml:240-261`:** the step "The map tiles stay index-only and bounded" is renamed "The map tiles stay bounded", and its comment names browse_list's map index; it runs the plan rail and `tests/test_browse_map_cells_live.py`.
- **A body test:** the re-patch statement sits after the DROP and before the rename.

**Docs touched by this wave:**
- docs/design/browse-merge-consistency.md:
  - :39-40 says */5; it is */15;
  - :244-251 and :268-277 wrongly say "the fresh rebuild already read the post-merge state";
  - close the non-goal at :354-363 and deferred item 4 at :399-400.
- toolkit/browse_read_model.py:16-20 and scripts/recompute_property_stats.py:423-424 say a rebuild is "in flight ~26 %". Measured: 61.9 s per 900 s. Also describe the re-patch.

**Sequencing: apply right before the merge.** From the moment 630 is applied, the tile counts read `browse_list` while the pins still read the matview, until Railway deploys the SPA. So 630 is applied only once the PR is green and ready, the receipts below are taken during the first */15 tick that runs the new body, and the PR merges straight after. The split lasts minutes, never longer.

**Verify between apply and merge (one */15 tick).** Latency targets are hot server execution:
- (a) and every city-bbox call with any portal chip ≤ 150 ms;
- (b) nationwide one category ≤ 400 ms;
- (c) the whole country ≤ 1.2 s (a seq scan on both relations; no index changes it);
- pin read ≤ 100 ms;
- the default call, reported.
- One run of each lands in the first minute after the swap (cold pages). Heap fetches are reported, never asserted.
- The `browse_map_cells` total for (b) on `browse_list` equals the matview's, within the drift between their last rebuilds.
- `derived_artifacts.last_duration_ms` before and after (with the map index it measured +0.35 s and +1.38 s, inside the old body's 61.9-66.2 s spread).
- The post-DROP section timed once, ideally during an autodedup burst (the re-patch's cost under the lock).

**Verify after merge, with Railway status green:**
- **Identity invariant:** `select count(*) from browse_list b join properties p on p.id = b.property_id where p.status <> 'active'` = 0 on at least 10 reads over 48 h, at least 3 of them in the minute after a swap.
- At the next real merge, the retired id is gone from `browse_list` in the merge's commit, and the map's bbox total drops by 1 on the next call.
- Read-only real-browser check of your filter at phone and desktop width: the pill equals the list count. This holds for a map-area filter; without one the list also counts about 53k served properties that have no point (determined foreign), which the map cannot draw.

**Stop if** a target is missed. Surface the numbers and the named fallback:
- **Fallback:** order the rebuild's heap by category_main, category_type, lat, lng. It saves about 11k buffer hits on (a) but costs non-place list pages about 10× the heap pages (113 → 1,188), so it is used only on a miss. **[taken 2026-10-19]** after the second apply missed (a) and (c) (W1 item 1).

**W1 receipts 2026-10-19** (17:44-18:04Z, read-only; file: `~/one-pin-artifacts/w1-receipts.md`, exact SQL for every number). The first apply's rebuild built `browse_list_map_idx` at the 17:45 tick:

| call (sreality chip, hide_dismissed) | target | W0, no map index | with the map index, hot | met with it |
|---|---|---|---|---|
| (a) dům + komerční prodej, Praha bbox | ≤150 ms | 72.6 ms | 71.1 ms | yes |
| (b) byt/prodej nationwide | ≤400 ms | 218.7 ms | 399.3 ms | by 0.7 ms |
| (c) whole country | ≤1.2 s | 904 ms | 1,932 ms | **no** |
| pin read, limit 2000 | ≤100 ms | 61.8 ms | 29.6 ms | yes |
| default byt/pronájem, no chip | reported | not taken | 265.8 ms (453.1 ms, visibility map empty) | n/a |

- **Cause:** the function's `(not hide_dismissed or not exists …)` stays a SubPlan filter, and the planner prices the map index as heap-ordered (its leading column `category_main` has correlation 0.9999) while inside a category it runs in lat/lng order: one heap page per row (773k buffers for (c), 139k for (b)). Without `all_sources` in it, no chip call can be index-only.
- **Decision (2026-10-19):** revert 630 in production and re-apply it without the map index; the re-patch and `property_merge_events_recent_idx` stay.
- **Second receipts (18:23Z, no map index):** (a) 224.6 ms (**no**), (b) 285.0 ms, (c) 1,228.8 ms (**no**, by 29 ms), pins 62.8 ms. **Decision 2:** the named fallback, the matview's layout with the map index (W1 item 1). Third receipts after the 18:45Z tick: superseded by the operator's 1.4 s ruling (2026-10-19).
- (b) total: 51,962 on `browse_list` = 51,962 on the matview. Identity invariant 0 at 17:51 and 18:03 (after a burst of at least 47 identity events). Replay: 108 = 108 properties, row for row; 604858 absent.
- Rebuild: 62,686 and 63,717 ms against 62,337 ms for the old body (whose last five ran 61.9-66.2 s). Re-patch windows: 103 events / 108 ids and 3 / 6; 0 inside either run.
- **Operator ruling (2026-10-19):** the whole-country tile count, 1.4 s at full zoom-out, is accepted (target (c) was ≤1.2 s). Follow-up candidate: the dismissal clause as a plain `NOT EXISTS`, one text across the four 629 bodies (three once 631 drops `properties_map_visible`).

### W2: retire the map matview (PR 2; migration 631 DESTRUCTIVE: pauses for R2 and R10)

- **Order:** only after W1's SPA bundle is live, so no new page load calls a dropped function. A tab opened before W1's deploy and never reloaded still calls `properties_map_visible`: its pins vanish until reload, its list does not. Named here, accepted.
- **Migration 631:**
  - `$pre$` guard: refuse while any view or function body still names `properties_map_mv` (the shape of 618's guard).
  - `cron.unschedule('browse-map-rebuild')`.
  - Drop `properties_map_visible(<629 signature>)`, `rebuild_properties_map_mv()`, and the matview `properties_map_mv` with its two indexes.
  - Delete its `derived_artifacts` row.
  - Per R11, drop the dead `recompute_mf_gross_yields()` and `recompute_property_mf(...)` (latest bodies 507:380, :524), verified off every live path.
  - `notify pgrst`.
  - No backup (R10: only derived objects are dropped; the revert, migrations/reverts/631_revert_retire_properties_map_mv.sql, restores them).
- **[as built 2026-10-19] Migration 631 drops** (live identities read 2026-10-19), behind the `$pre$` guard, with no backup (R10 waived it):
  - the `browse-map-rebuild` cron job (`7,37 * * * *`), unscheduled;
  - `properties_map_visible(...)`, its one overload (629's ten-parameter signature);
  - `rebuild_properties_map_mv()`;
  - the matview `properties_map_mv` with `properties_map_mv_pk` and `properties_map_mv_cover`;
  - the `derived_artifacts` row `properties_map_mv` (deleted);
  - per R11, `recompute_property_mf(p_ids bigint[])` and `recompute_mf_gross_yields()`;
  - then `notify pgrst`.
- **SPA:**
  - Delete the "Browse map" label (derivedArtifacts.ts:105).
  - Edit comments: derivedArtifacts.test.ts:38, and Health.tsx:89-93, which is a comment, not a threshold; the thresholds left in migration 440.
  - Note 2026-10-20: Health W8 (PR #1894) deletes derivedArtifacts.ts with its test and rewrites Health.tsx (it reads only pipeline_checks_public), so after it lands this SPA step has nothing left to edit; on rebase keep its deletion.
- **Delete these test cases:**
  - `tests/test_cron_statement_timeout_guard.py:272-278` (`browse-map-rebuild` budget) and :309;
  - `tests/test_derived_artifacts_registry.py:184-188` (the map case) and the :244 docstring;
  - `tests/test_browse_read_path_guardrail.py:196-201` (the map rebuild test) and the :227 docstring;
  - `tests/test_portal_rule.py:220-231`, where the shape check goes from three relations to two.
- **Comment edits:**
  - api/location_filter.py:40;
  - toolkit/filter_registry.py:1551-1552;
  - tests/toolkit/test_filter_registry.py:127;
  - scripts/backfill_support.py:41 (the LIKE pattern still matches);
  - tests/test_tenant_isolation_live.py:1570.

  scripts/reparse.py:254-255 stays as incident history.
- **Keep as history:**
  - tests of old migrations: test_read_model_rewrite_migration.py, test_one_property_view_migration.py, test_serve_street_code_migration.py, test_browse_hides_dismissed_migration.py, test_location_w5_serve_resolved.py:143-148, test_migration_rls_grants.py:421, test_602_*.py, test_curated_set_scope_migration.py, test_location_w3_projection.py:7;
  - test_browse_grant_drift.py:50, which scans old migrations;
  - test_measure_registry_census.py:811-812, whose assertion stays true;
  - test_checks_platform.py:313-318, a synthetic cron name;
  - the fixtures health_capacity_cron_8w.csv and health_jobs_14d.csv, recorded history.
  - `tests/test_matview_indexes.py` does not name the map objects on main.
- **Docs:**
  - CLAUDE.md:51: drop `properties_map_mv` from the no-RLS read models.
  - `.claude/skills/database/SKILL.md`:
    - :152 (the map rebuild in the lock note);
    - :263-264;
    - :271-279: delete the map bridge and the forced map rebuild from the add/remove-a-column recipes;
    - :291-295: two dependents of browse_projection's row type become one;
    - :322-330: the two-rebuild warning.
  - docs/architecture.md:644, :681, :2427-2430, :3259, :4145, :4149, :4162, :4183, :4202.
  - docs/design/browse-merge-consistency.md:42, :174.
  - A "superseded" note on docs/design/browse-read-model.md.
  - docs/design/location-serving-contract.md:68.
  - docs/design/health-dashboard.md:1266.
  - The roadmap track's done-bullet, and the PR-F bullet (roadmap/measure-track.md:589-590) loses the function drop.
  - Dated design and roadmap logs stay.
- **Verify:**
  - `to_regclass('public.properties_map_mv')` is NULL;
  - no `browse-map-rebuild` row in `cron.job` and no `derived_artifacts` row;
  - the `cron/browse-map-rebuild` Health cell is gone (cells come from live `cron.job`, scripts/checks/platform.py:190-217) and no `ops_incidents` row for it stays open;
  - no 404 for `properties_map_visible` in the API logs;
  - CI green, including the migrations lane;
  - read-only production map check.

### W3: choose the location ad (PR 3; migration 635 additive; invisible)

- **Migration 635:**
  - `alter table properties add column location_listing_ref_id bigint`:
    - nullable, metadata-only, guarded by `lock_timeout`;
    - a column comment states the rule;
    - no foreign key, because ads are never deleted (rule 3) and validating one would scan two hot tables;
    - one partial index, `properties_location_listing_ref_active_idx (location_listing_ref_id) where status = 'active' and location_listing_ref_id is not null`, the twin of `properties_repr_listing_ref_active_idx`. W0's R8 receipt shows the spatial-first cohort shape probes exactly that index (index-only); without the twin, comparables would fall back to a heap scan after W4.
  - `property_canonical_listings` is dropped and re-created (md5-gated on 588), returning `(listing_id, canonical_rank, location_rank)`.
    - The canonical window is byte-identical to 588:60-76, and a test pins that.
    - `location_rank` = `row_number() over (order by served tier desc, rung desc nulls last, canonical_rank)`, adding one join to `location_granularity_rank`.
    - A window cannot order by a sibling window, so `location_rank` is computed in an outer select over the canonical window; the byte-identical test allows that wrapping. The output columns change, so the function is DROP + CREATE with 588's grants restated (service_role only). The rollup's `CROSS JOIN LATERAL … o` (recompute_property_stats.py:107) selects named columns, so it survives the new signature.
    - Execute rights are restated as service_role only. The rollup is its only caller (588:81-84).
- **Writers, in the same statement as the canonical ad:**
  - `NEW_SINGLETONS_SQL` (scraper/db.py:598) inserts `(repr_listing_ref_id, location_listing_ref_id, is_active)` = `(l.id, l.id, l.is_active)`.
  - The rollup adds `loc AS (SELECT * FROM kids WHERE location_rank = 1)` and sets `location_listing_ref_id = loc.id`.
  - `_NO_ADS` (:339-345) nulls it.
  - The new column does NOT join `toolkit/listing_identity.py:54` `R2_CARRIERS`, the legacy-to-surrogate registry. It has no legacy twin, and adding it would break tests/scripts/test_backfill_child_listing_ids.py:27,37.
- **Tests:**
  - an inactive finer ad beats an active coarser one;
  - among equally precise ads the canonical order decides, so the active one wins;
  - point > foreign > none;
  - equal rungs fall back to the canonical order;
  - a single-ad property picks its ad;
  - birth sets both pointers;
  - no-ads nulls both;
  - the canonical window is unchanged.
- **Backfill:**
  - **Live rows:** dispatch the existing resumable full property sweep, or wait for the daily run. The last sweep took 1,051,811 ms and finished at 10:13:25Z against a 04:15 schedule.
  - **The fill, any status, after the sweep cycle completes:** `update properties set location_listing_ref_id = repr_listing_ref_id where location_listing_ref_id is null and repr_listing_ref_id is not null`, in id-range batches (the database skill's backfill recipe). It covers the 120,013 `merged_away` rows and any property merged away before the sweep reached it. Only two statuses exist live (`active` 891,088, of which 18 childless rows have repr NULL; `merged_away` 120,013), so the CHECK is satisfiable.
- **Verify:**
  - 0 rows of any status where `(repr_listing_ref_id IS NULL) <> (location_listing_ref_id IS NULL)`;
  - 15100 → 13193960;
  - location ≠ canonical on about 13,784 multi-ad properties (4,309 live + 9,475 dormant, measured 10-19);
  - singletons: location = canonical on 100 %;
  - the canonical ad still equals rank 1 of the inline 588 order on 100 %. This is the round-two check, `repr_ne_inline_canon = 0`, re-run.
- **The far-apart-ads hand-off, as a deliverable.** The W3 PR body carries the census:
  - conflicting pairs by rung and cross-town count, from measure:r1's query;
  - a sample of slice ids 1-158,046: 1,580 live conflicts, 1,568 of them involving a town or quarter centroid, 34 crossing a town line.
  - One bullet in roadmap/location-data.md: centroid radii drawn too small (town 60.4 %, quarter 86.1 % containment).
  - One bullet in roadmap/autodedup.md: the cross-town property ids as merge-quality input.
- **[as built 2026-10-19] W3 merged as #1895 (22:07Z), migration 635 applied.** Backfill and verify, read-only:
  - the full property sweep ran to success at 22:25Z; the status-agnostic fill (location = canonical where still NULL) finished at 22:35Z;
  - rows of any status where `(repr_listing_ref_id IS NULL) <> (location_listing_ref_id IS NULL)`: **0**, so W4's `$pre$` gate and CHECK can pass;
  - 15100 → 13193960, the ad holding the exact point;
  - location ≠ canonical on **14,051** `active` properties, live and dormant (planned about 13,784 from the 10-19 census; the difference is merges and re-resolves since; 14,073 on a re-read 2026-10-20, mismatched pairs still 0);
  - singletons: location = canonical on 100 %;
  - a 3,000-property sample: the canonical ad unchanged against the inline 588 order (`repr_ne_inline_canon = 0`).
  - The two hand-off bullets (roadmap/location-data.md, roadmap/autodedup.md) were owed by W3's PR and land with W4's.

### W4: every property surface reads the location ad (PR 4; migration 636 additive; visible)

**Migration 636, md5-gated on the bodies it restates:**
- **`$pre$` gate:** refuse while any row has `(repr_listing_ref_id IS NULL) <> (location_listing_ref_id IS NULL)`.
- **The CHECK.** `alter table properties add constraint properties_location_ad_present check ((repr_listing_ref_id is null) = (location_listing_ref_id is null)) not valid`, guarded by `lock_timeout`, then `validate constraint`. Validation scans without blocking reads or writes.
- **`browse_projection`**, restated from 625:204-298: only the join at 625:289 changes key. The MF lateral (625:291-294) follows because it reads `ll`. `listing_id` and `source_id_native` stay the canonical ad's link (625:260-261). The column list is unchanged, so `browse_list` needs no bridge.
- **`properties_public`**, restated from 617:98-185: only the join at 617:180 changes. The ad-facts join at 617:179 stays canonical. One column, `location_listing_ref_id`, is appended for the watchdog's served rule.
- **`recompute_city_proximity`**, restated from 508:971-1051:
  - the join at 508:1028 changes key;
  - its outer UPDATE gains `and p.location_listing_ref_id = s.location_listing_ref_id`, so a run that races a location change writes nothing stale.
- **One-time proximity reset.** `city_proximity_computed_at = NULL` where location ≠ canonical (about 13.8k rows); the hourly job (`.github/workflows/recompute_city_proximity.yml:17`) recomputes them.
- **No forced rebuild.** The next */15 tick carries the change into Browse.

**Code, one key each from `repr_listing_ref_id` to `location_listing_ref_id`:**
- api/routes/property_location.py:80, plus its docstring at :3-8 (the phone's Poloha and the Mapy static map);
- api/estimation_runs.py:1986 `COALESCE(p.location_listing_ref_id, l.id)`, plus its comment at :1971-1974;
- scraper/sold_db.py:87 (the sold-comps lane is live: interval 3600 s);
- scripts/checks/publishing.py:47 and :51, plus its comment at :43-45;
- api/notifications.py:257 `served_location_predicate("l.location_listing_ref_id")`;
- **The rollup's proximity reset (recompute_property_stats.py:233)** fires when the location ad changes, not the canonical ad.
  - Its `city_proximity_computed_at < repr_since` clause, which repaired a proximity run that raced a canonical change, is deleted. The UPDATE guard above replaces it.
  - `repr_since` stays on the canonical ad, so price-alert windows are untouched.
- location_data/claims_common.py:38 (comment); the phone comments at propertyLocation.ts:1 and LocationBody.tsx:2.

**One definition, two shapes** (R7 + R8), both in location_data/claims_common.py beside the served rule. The SUBJECT fragment, a primary-key lookup from one ad:
`LEFT JOIN properties lp ON lp.id = l.property_id AND lp.status = 'active' LEFT JOIN listing_location ll ON ll.listing_id = COALESCE(lp.location_listing_ref_id, l.id)`. An unattached ad falls back to its own row.
- **R7, the estimation subject** uses it at:
  - estimation_runs.py:1590-1606, where it replaces the `ll` + `pll` pair and its `COALESCE(pll.geom, ll.geom)`: one join fewer;
  - :1524-1550 (by URL);
  - notifications.py:728-742 (the watchdog kickoff, :741);
  - toolkit/velocity.py:274-283;
  - scripts/smoke_agent.py:159;
  - the /estimations list label (estimation_runs.py:392-399, :417-421).

  New runs only; past results stay immutable (rule 12).
- **R8, comparables** (comparables.py:225-231, :320; velocity.py:52, :64; transit_axis.py:277, :338) use the COHORT shape, which is spatial-first. A cohort must be driven by the spatial index, and Postgres cannot drive `listing_location_geog_gist` through a COALESCE key (measured on a read-only proxy, byt/prodej within 1 km of the Praha centre: 67k buffers with today's shape against 1.76M with the subject fragment, 26×):
  `listing_location ll` (the spatial filter) → `properties lp ON lp.location_listing_ref_id = ll.listing_id AND lp.status = 'active'` → `listings l ON l.id = lp.repr_listing_ref_id` (the canonical ad is the comparable).
  - Decision 13's `EXISTS (... canon_p.repr_listing_ref_id = l.id)` disappears into that join: a property enters the cohort once, as its canonical ad, placed at its location ad. No COALESCE is needed, because an unattached ad is never a comparable (comparables.py:224-227).
  - The comparable stays its canonical ad: price, area and `snapshot_id` (rule 8). Its distance, district and place filters follow its property's location ad.
  - Three spelled joins and one EXISTS become one cohort shape; the subject keeps its fragment. Two shapes, one definition.
  - W0 takes the proxy EXPLAIN as the receipt; W4 compares three comparables calls before and after, and any slowdown goes in the PR body.

**Watchdog, notifications and extension:**
- In-app feed: `lp.display_label` → `pp.display_label` (notifications.py:476; `pp` is already joined at :515-516).
- Outbox: delete both inline `location_display_label(ll.…)` compositions (notification_outbox.py:165-181) and read `properties_public.display_label` by `d.property_id`. The `l` join stays for disposition, price and source (R5).
- Extension panel (api/portal_lookup.py:87-89): label = `COALESCE(pp.display_label, <the ad's own label>)`. The fallback only fires for an ad not yet attached to a property, which has no MF either (R9).

**The CI rail, a new test with three arms** covering every shape in inventory A:
1. **Statements and bodies.** Fail on `repr_listing_ref_id` in any view or function body of the replayed CI schema, or in any SQL statement found by `tests/sql_corpus.discover(include_inline=True, resolve_imports=True)` (without `resolve_imports` the corpus holds only literal `*_SQL` constants, and publishing.py:47/:51 live in an f-string), when the same statement also names `listing_location` or `served_location_predicate`. SQL assembled inside functions (comparables, notifications.py:257) never reaches this arm; arms 2 and 3 exist for it. This covers:
   - the joins: 625:289, 617:180, 508:1028, 507:406, property_location.py:80, estimation_runs.py:1602, sold_db.py:87, publishing.py:51;
   - the predicate argument at publishing.py:47;
   - the COALESCE key at estimation_runs.py:1986.

   It does not flag an ad's own `ll.listing_id = l.id`.
2. **Served-rule registry.** Every `served_location_predicate(` call (5 on main: notifications.py:257, publishing.py:47, location_steps.py:56, dedup_candidates_sql.py:94, claims_common.py:55) is registered with its grain, the `SERVED_LOCATION_PREDICATE` constant's ad-grain uses included. A property-grain call must pass a `…location_listing_ref_id` argument. This catches the indirect key through `properties_public.listing_id` (notifications.py:257).
3. **Module census.** The modules that name both `repr_listing_ref_id` and `listing_location` are listed with a one-line reason, and a new one fails until reviewed.
   - 9 today.
   - 4 expected after W4, each for a non-location reason:
     - curation.py (comment only, :139);
     - notifications.py (price lineage, :1510-1671);
     - autodedup/ui_sql.py (identity);
     - location_data/claims_common.py (defines R8's cohort shape, whose comparable is the canonical ad). [as built: comparables.py left the census when it took that shape from claims_common.]
   - This arm catches shapes assembled across functions, such as comparables' gate plus join.
4. **[as built] Shape registry.** The R7 subject sites (estimation by id, by URL, the /estimations label, the subject MF facts, the watchdog kickoff, the velocity subject, scripts/smoke_agent.py) each reference `SUBJECT_LOCATION_JOIN`, and every function that calls `_shared_filter_where` references `COHORT_LOCATION_FROM`. An ad-keyed join (`ll.listing_id = l.id`) passes arm 1, so without this arm a new cohort could count every ad of a property.

   The definers (db.py, recompute_property_stats.py) do not name `listing_location`, so no allow-list entry is needed. The two dead MF functions are dropped in W2 (R11), so the rail starts with an empty allow-list.

**Tests changing with W4:**
- tests/api/test_property_location.py:141;
- tests/scraper/test_sold_db.py:140;
- tests/scripts/test_checks_publishing.py:210;
- tests/test_checks_publishing_sql_live.py:115, 213, 226;
- tests/test_mf_reference.py:377;
- tests/test_location_w5_serve_resolved.py:296. Its line 47 pins migration 514 and stays as history.
- tests/toolkit/test_comparables.py:580.
- [as built] tests/test_location_ad_order_live.py (a location-ad change now clears the proximity stamp), tests/test_merge_safety_live.py (a stamp older than `repr_since` is kept), tests/test_measure_price_per_m2.py (its fixture sets both pointers, for the CHECK), tests/test_one_place_predicate.py (claims_common's fragments count as the `listing_location` join), tests/test_filter_compiler_rows_live.py with tests/fixtures/filter_compile_golden.json and filter_compile_rows.json.

**Docs, same PR:**
- CLAUDE.md:
  - rule 15: "a property's location is its location ad (`location_listing_ref_id`)";
  - rule 25, line 244: "`listings`/`properties` carry no location VALUE; properties carry one pointer".
- GLOSSARY entry, below.
- `.claude/skills/database/SKILL.md:281-287` and **:457-460**.
- docs/architecture.md:1454, :3645-3647, :4093-4099.
- docs/design/location-serving-contract.md:23-26 ("aggregated at read" is false).
- roadmap/location-data.md:407, :516, plus the track bullet.

**GLOSSARY entry**, under "Around a property", after Canonical ad (GLOSSARY.md:27-30, which gains "its location is the location ad's"):

> **Location ad**:
> The ad whose resolved location a property shows: its map pin and precision, its address label, its town, quarter, street and KÚ codes, and everything computed from them (MF reference rent, city proximity, whether it is served). Chosen by a fixed order, never by hand: an ad with a map point first (then one determined foreign), then the finest RÚIAN rung whatever the ad's status, ties by the canonical order (active ads first). Often the canonical ad, not always. Stored as `properties.location_listing_ref_id`.
> _Avoid_: place, pin ad, anchor ad, located ad, representative location

**Pre-check, in the PR body.** Dry-run the watchdog matcher over properties whose location codes change, and count the once-ever ":new:" alerts the swap would fire. Town changes are measured at 84 (35 with both codes known). Street and KÚ gains are measured here, not predicted. If the count is above zero, the merge waits for your word.
- **How the count is taken: read-only SQL after the apply, before the merge.** After 636 `properties_public` already reads the location ad, so for each active subscription:
  - render its WHERE with this branch's `_build_match_clauses` (Python, no connection);
  - run it as one read-only SELECT over `properties_public`, restricted to properties whose location ad ≠ canonical ad;
  - keep only rows the matcher can still fire on: `first_seen_at` past the subscription's cursor or inside the lookback re-scan (`max(60, 2 × notifications_new_requires_image_timeout_minutes)` min, 60 by default);
  - drop rows that already hold a `wd:{sub}:new:{property_id}` dispatch.
  - The same SELECT counts the `:price_drop:` alerts the swap could add: a cut inside `notifications_price_drop_window_days` (2 days by default) on a property that only now matches.
- **Why it is small by construction.** `:new:` is keyed on arrival (`first_seen_at`), so a place change on an older property never fires it; only properties first seen inside that window can.
- **What the count does not do.** The apply itself moves `properties_public`, so the deployed matcher sees the new place at its next pass, before the merge. The count reports what that pass can fire; it does not hold it back. The PR body also reports the `change_kind = 'new'` dispatches written between the apply and the merge on properties whose location ad ≠ canonical ad.

**Verify, live after apply:**
- **15100.** `browse_list` (after the next tick), `properties_public` and `GET /properties/15100/location` all return 50.05995, 14.46709, rank 100, radius 10, "Pod Sychrovem II 850/43, Praha". Category is still komercni/ubytovani, and the price is unchanged.
- **Counts.** Measured 10-19; expect drift of a few units:

  | | Expected |
  |---|---|
  | newly unserved | 0 |
  | newly served | 1 (a foreign-only dormant property) |
  | town changes | about 84 |
  | location ≠ canonical | about 13,784 (14,051 after W3's backfill) |
  | rings that become dots | about 10,797 |
  | live properties placed from an inactive ad (R1, accepted) | about 1,376 |

- **Order holds, bounded.** The count of served properties whose stored location ad is outranked by another of their ads is measured, not asserted at 0. The resolver never dirties a property (resolve_db.py:518-555; about 27.5k canonical-ad re-resolves a day), so a rung change on a non-location ad waits for that property's next rollup, up to one sweep cycle. It must read 0 on properties recomputed after their ads' last `resolved_at`.
- **Constraint and rails.** The CHECK is validated; the rail and the location census are green.
- **Proximity.** The proximity reset rows reach 0 through the hourly job; report the count per run.
- **Extension.** The panel's label and MF name one location on a sample of non-canonical active sale-flat ads. Today 1,391 of 9,074 (15.3 %) differ from their canonical ad by KÚ.
- **Comparables.** EXPLAIN (ANALYZE) three comparables calls before and after; the cohort shape is spatial-first by design (W0 receipt), and any slowdown goes in the PR body for your call.
- **Browser (read-only).** Your exact filter at phone and desktop width shows one solid dot and no ring, the same label on card and pin, and a pill reading 1.

**[as built 2026-10-20] W4, one PR (migration 636 + code + docs):**
- **Migration 636** (additive; applied and verified before the merge):
  - the `$pre$` gate refuses while any row has one pointer without the other, and md5-gates each restated body on its live definition;
  - `properties_location_ad_present`, added `not valid` under `lock_timeout`, then validated;
  - `browse_projection`, `properties_public` and `recompute_city_proximity` key their `listing_location` join on `location_listing_ref_id`; `properties_public` appends that column; the proximity UPDATE writes a row only while its pointer is unchanged;
  - the one-time proximity reset where location ≠ canonical and a stamp is set; a revert file in `migrations/reverts/`;
  - the transaction commits before the reset and the VALIDATE; apply via apply_migration.yml only (header of 636), and re-dispatch on a lock timeout after the commit or on "deadlock detected".
- **One definition, two shapes**, in location_data/claims_common.py beside the served rule:
  - `SUBJECT_LOCATION_JOIN` (R7): estimation by id (it replaces the `ll` + `pll` pair), by URL, the subject MF facts, the /estimations label, the watchdog kickoff, the velocity subject and scripts/smoke_agent.py;
  - `COHORT_LOCATION_FROM` (R8): comparables, velocity and the transit corridor. Decision 13's `EXISTS` is gone into it; `exclude_listing_ids` stays the one exclusion. Beyond R8's spelled shape it keeps `AND l.property_id = lp.id` (Decision 13's guard against a stale pointer; a filter on the `listings_pkey` probe, no plan change).
- **Rails:** tests/test_property_location_ad_rail.py (arms 1-4; census: curation, notifications, autodedup/ui_sql, claims_common), tests/test_636_property_location_ad_readers.py (the file's text and md5 gates), tests/test_location_ad_readers_live.py, and a migrations.yml step running the live file and the rail's schema arm.
- **Keyed on the pointer:** the property location route (phone Poloha + Mapy static map), the sold-comps lane's towns, the publishing check, the watchdog's served rule; the rollup clears `city_proximity_computed_at` when the location ad changes, and its `< repr_since` clause is deleted.
- **Labels:** the in-app feed and the outbox print `properties_public.display_label` (R5; the outbox's two inline compositions are deleted); the extension panel prints `COALESCE(pp.display_label, the ad's own label)` (R9).
- **Docs:** CLAUDE.md rules 15 and 25, GLOSSARY (Canonical ad, Location ad), the database skill, architecture § rules 15/16/20/25 and § Location data, location-serving-contract.md, and the roadmap tracks (location-data: the track bullet and the W3 radius hand-off; autodedup: the cross-town hand-off; ui-track: W3 and W4).

### F1 (named next subtraction, NOT in this sprint): delete the */15 list rebuild

Not safe today:
- 78 % of newly served properties (153 of 196 measured) reach Browse only through the rebuild.
- City proximity (about 4.7k rows a day) is never patched.
- `tom_days` moves with the clock: about 366k active rows tick once a day.

**Gates:**
1. the resolver marks its property dirty, in the upsert's own transaction, when served fields change. This is the withdrawn R6; here it closes a measured lag.
2. `recompute_city_proximity` marks dirty the rows it updates;
3. your ruling on `tom_days`.

**Then:** a daily rebuild after the sweep, and `browse_list` becomes LOGGED (about 0.66 GB of WAL a day).

**Ledger:**
- **Removes:** 96 rebuilds a day × about 62 s (about 1.6 h a day of database CPU); the swap lock-queue hazard and its `_DRAIN_LOCK_TIMEOUT` rationale; publishing's `waiting_for_rebuild` stage (publishing.py:38-53).
- **Adds:** gates 1-3.

---

## Subtraction ledger (honest counts)

| | Added | Deleted |
|---|---|---|
| Relations | 0 | **1** matview (`properties_map_mv`, 824,675 rows, 464 MB with indexes) |
| Indexes | 3 (`browse_list_map_idx`, the matview cover's key and width, W1; `property_merge_events_recent_idx`, a few MB, W1; `properties_location_listing_ref_active_idx`, the twin of the repr index, W3) | 2 (`properties_map_mv_pk` + `properties_map_mv_cover`, 92.4 MB; W2, migration 631) → W1+W2: 2 added / 2 deleted, **net 0**; with W3's twin, net +1 |
| Functions | 0 | **4**, all in migration 631 (W2): **2** map (`rebuild_properties_map_mv()`, `properties_map_visible(...)`) + **2** dead MF (`recompute_property_mf(p_ids bigint[])`, `recompute_mf_gross_yields()`; R11) |
| Cron jobs / registry rows / Health cells | 0 | **1 / 1 / 1** |
| Table columns | **1** (`properties.location_listing_ref_id`) | 0 |
| Constraints | 1 CHECK (the column's enforcement) | 0 |
| View / function output columns | 2 (`properties_public.location_listing_ref_id`, `property_canonical_listings.location_rank`) | 0 |
| Rebuild steps | 1 identity re-patch (~12 lines) | the whole map rebuild |
| Map wire columns | 0 | 4 (+ 4 `MapRow` fields) |
| SPA symbols | 0 | `MAP_RELATION`, the map `HIDING_DISMISSED` entry, `mapSource`, the "Browse map" label (**−4**) |
| Location spellings in code | 1 definition in 2 shapes (subject fragment, spatial-first cohort; R7/R8) | 5 subject point reads → 1 fragment; 3 cohort joins + Decision 13's EXISTS → 1 shape; by-id's second location join; 2 outbox label compositions; the feed's ad-label dependency; the proximity reset's `< repr_since` clause |
| Modules naming the canonical pointer next to a location | — | 9 → 4 (each with a non-location reason) |
| Browse freshness contracts | — | 2 → **1** |
| Rebuild CPU | the map index build (+0.35 s and +1.38 s measured, inside the old body's 61.9-66.2 s spread) and the re-patch (≈155 ms at a 1,156-id burst) per list rebuild × 96/day | ≈60 s × 48/day of matview builds |
| WAL | 0 | ≈22 GB/day (logged matview rebuilds) |
| Tests | 1 rail (3 arms), order tests, 1 SPA same-chain test, 1 rebuild-body test | 3 map test cases + the cron-guard map entry |
| Docs | 1 GLOSSARY entry | the map half of the skill's column-bridge recipes; CLAUDE.md:51 entry |
| Lines | append-only migration restatements (rule 1 forbids editing history) | runtime lines: measured with `git diff --stat` and reported in each PR body (no estimate here) |

**The one addition is the pointer column**, and its CHECK is how it is enforced. W1 and W2 pay for it, and they land first:
- one relation, 2 functions, 1 cron job, net 0 indexes;
- about 22 GB a day of WAL;
- about 48 minutes a day of rebuild time.

W4's consolidations then shrink the location spellings. **W3 on its own only adds.** Rule 25 says a location PR that adds more than it deletes needs your ruling: **R3 is that ruling.**

---

## Rulings (taken 2026-10-19)

**R1. Which ad supplies a property's location?**
→ **Ruled: the most precise ad, even if inactive** (served tier → finest rung → canonical order). The recommendation was active-first; the operator chose precision, accepting the 1,376 live properties placed from an inactive ad.

| measured 10-19 | Live (22,152): active-first | Live: all-ads | Dormant (45,733): both orders |
|---|---|---|---|
| location ad ≠ canonical ad | 2,953 | 4,309 | 9,475 |
| ring → dot | 2,342 | 3,378 | 7,419 |
| dot → ring | 0 | 0 | 0 |
| exact pins, today → after | 36.5 % → 47.1 % | 36.5 % → 51.8 % | 35.1 % → 51.4 % |
| placed from an inactive ad while an active ad has a point | **0** | **1,376** | n/a |
| town changes (any / both codes known) | 8 / 5 | 9 / 6 | 75 / 29 |
| newly served / unserved | 0 / 0 | 0 / 0 | 1 / 0 |

- *All ads (precision before activity):*
  - 1,036 more live dots;
  - 1,376 live properties placed from an ad gone a median of 73 days;
  - the dead point sits inside the live circle 62 % of the time, and more than max(1 km, 2r) away 24 % of the time.
- *Status quo:* 15100 stays a ±300 m ring.
- *Centroid or consensus:* invents points no ad claimed.
- **Two notes:**
  - **Low confidence:** 1.7 % of changed location ads in the sampled slice are `low` confidence. They are already drawn as dots when canonical; no confidence key now.
  - **Far-apart ads:** the far-apart-ads count does not depend on the order (live: 2,646 status quo / 2,655 / 2,662), so it is not an argument either way.

**R2. Retire `properties_map_mv` so pins and counts read `browse_list` (W2's drop is destructive)?**
→ **Ruled: yes, and accept the UNLOGGED bound.** After a crash, list, map and counts read empty for up to about 16 min, until the next */15 tick. The list carries that risk today.
- *Yes, and make `browse_list` LOGGED now:* about 64 GB of WAL a day at */15 against the 22 GB a day the matview writes today, net about +42 GB a day. That is better paid in F1, at a daily cadence (about 0.66 GB a day).
- *Keep the matview:* up to 31 min of ghost pins and stale counts after every merge.
- *Make the matview its own patched table:* two copies of one projection, two lock orders, two rebuilds.

**R3. Store the location ad as a pointer, or fold the location order into the canonical order?**
→ **Ruled: the pointer.** No card's price, photos or link changes, and a geocoding change can never move the price voice.
- *Fold it into the canonical order:* no new column, rail or term, but:
  - the canonical ad changes on 12,428 properties: price voice, photos, link and portal (how many prices differ was not measured);
  - each of those restamps `repr_since`, which restarts its price-alert window (recompute_property_stats.py:232; notifications.py:1372);
  - 15100's own card would turn into dům (one-pin-understanding.md:121).
- *Status quo:* defect B stays.
- This ruling is also rule 25's ruling for W3, a location PR that adds more than it deletes.

**R4 (UX consult). The map pill counts properties but says "listing(s)". What should it say?**
→ **Ruled: "property / properties"** (English, like the rest of the pill).
- *Czech "nemovitost / nemovitosti / nemovitostí":* matches the pin-limit tooltip.
- *Keep "listings":* the word that made your stale pair read as two ads.

**R5. Notification text (in-app feed, email/Telegram): which address?**
→ **Ruled (default accepted): the property's location ad**, via `properties_public.display_label`. Dispatches are property-grain (rule 16), and this deletes the outbox's two private label compositions.
- *Keep the ad that was canonical at dispatch time:* it can name a different address than the property page.

**R7. Where does an estimation subject stand?**
→ **Ruled (default accepted): one shared join.** The subject stands at its property's location ad, or at its own row when the ad is unattached. It is used by:
- by-id;
- by-URL;
- the watchdog kickoff (notifications.py:741);
- the velocity subject;
- the smoke script;
- the /estimations label.

Past runs stay immutable.
- *Leave as is:* four resolvers and three definitions; one flat estimated from a card and from its URL stands at two points.
- **Note:** R7 unifies LOCATION only. The by-URL and kickoff resolvers also read the ad's own area and price, while by-id reads the property's, so a by-URL estimate will stand at the property's location ad while keeping the ad's own area and price. That divergence is in FACTS, not location; it is reported, not fixed here.

**R8. Where do comparables stand?**
→ **Ruled (default accepted): at their property's location ad.** The canonical ad stays the comparable (price, area, `snapshot_id`), so subject and comparables use one definition in every estimate. Velocity and the transit axis inherit it.
- *Keep the canonical ads' own points:* each estimate then states that its subject and comparables are placed two ways.

**R9. The extension panel prints the viewed ad's label next to the property's MF. Which label?**
→ **Ruled (default accepted): the property's label (`pp.display_label`).** The ad's own label is used only for an ad not yet attached to a property, which has no MF. This matches the panel's own stated contract, "the same [label] Browse, the map and the notifications print" (portal_lookup.py:83-86).
- *Keep the ad's label and name the property's location next to the MF:* a second label line.
- *Price the MF at the viewed ad's own codes:* reverses ruling Q8 b (567:21-24) and the panel's "MF is PROPERTY-grain" contract (portal_lookup.py:100-106).

**R10. Back up before W2's drop?** Rule 1 says destructive = your OK + pg_dump.
→ **Ruled: waive the backup for this drop.** Everything dropped can be rebuilt from migration history:
- the matview is a derived copy of `browse_projection`, defined in 522 and 629;
- the two map functions are defined in 522 and 629, the two dead MF functions in 507;
- no stored data is lost.

The stored `properties.mf_*` columns are NOT dropped here; they stay with PR-F and its own backup.
- *Run `backup_before_drop.yml` first anyway.*

**R11. The two dead MF functions** (`recompute_property_mf`, `recompute_mf_gross_yields`, 507:380 / :524) still place a property at its canonical ad. Nothing calls them:
- no cron job;
- no workflow, since 8b0327b2 (2026-09-26) deleted `recompute_mf_yields.yml`;
- no Python caller;
- no database caller (live check).

→ **Ruled: drop both in W2's migration, under the same OK.** The operator's condition: they must not be part of the MF estimation or publishing path. Verified on origin/main and live: no `cron.schedule` names them; the hourly workflow that called them (`recompute_mf_yields.yml`) was deleted in 8b0327b2 on 2026-09-26; no Python module, script, check or workflow calls them; no database object depends on them. Browse's MF reference rent and gross yield come from `browse_projection`'s `mf` lateral (625:239-240, :291-294), computed from the location ad's codes at rebuild time, and the on-demand reference-rent path is untouched. The stored `properties.mf_*` columns stay with PR-F (roadmap/measure-track.md:589-590), which loses only the function drop.
- *Keep them and allow-list them as "dead, PR-F":* one purpose fewer in W2, two allow-list entries in the rail.

**R12. Measure the map index on live `browse_list` before W1?**
→ **Decided, not asked: no.** It would be out-of-band DDL on production. W1 already meets every target without the index (129 ms for (a)), and its own receipt has a named fallback.
- **[as built 2026-10-19]** W1's applies measured it in production instead: on 605's first_seen heap it slowed (b) and (c); without it (a) and (c) missed; on the matview's layout it stays (W1 item 1, W1 receipts).
- *Yes:* a temporary `create index concurrently` right after a swap; it vanishes at the next rebuild.

*Withdrawn: R6* (the resolver marks its property dirty). Its effect on this sprint was not measured. It moves to F1, where it closes a measured lag (78 % of new properties).

---

## Risks

- **Residual patch race.** Only identity changes are re-patched. A dirty-drain or birth patch that commits inside the rebuild window still waits for the next swap, up to 15 min. That is today's bound, and it never produces a ghost.
- **UNLOGGED after a crash** (R2). A crash or failover resets `browse_list` (662 MB) to empty: list, map pins and tile counts read 0 until the next */15 tick.
  - Up to about 16 min.
  - Nothing else triggers a rebuild.
  - `derived_artifacts` still reads fresh, but the publishing stock check (scripts/checks/publishing.py:94-102) goes red.
  - **Mitigation:** accept now (the list's bound today); LOGGED comes with F1.
- **Map index effect is estimated.** The index's effect on (a) is estimated at 40 to 80 ms, not measured. Without the index, (a) already runs at 129 ms against a 150 ms target, and the fallback's list cost is known. **[as built 2026-10-19]** Measured: on the first_seen heap (a) 71 ms, (b) and (c) slower; without it (a) 225 ms; the heap now takes the matview's layout (W1 item 1).
- **List page locality (accepted 2026-10-19).** Laying `browse_list` out by category, lat, lng scatters a filtered newest-first list page across the heap: measured 113 → 1,188 heap pages per page. Hot, that is a few ms; accepted as the price of the map index (W1 item 1).
- **Counts and pins briefly disagree.** Between applying 630 and Railway deploying W1's SPA, the tile counts read `browse_list` while the pins still read the matview. That is why 630 is applied only right before the merge: the split lasts minutes.
- **Visible changes in W4.**
  - About 13,784 locations change and about 10,797 rings become dots.
  - About 84 properties change town.
  - About 1,376 live properties take their location from an inactive ad (R1, ruled and accepted).
  - Street-chip and KÚ cohorts, MF yields and `browse_stats` shift, mostly as refinements; the street and KÚ gains are measured in W4.
  - New estimates use finer subject and comparable points.
  - Watchdog ":new:" alerts are counted before merging.
- **Wrong merges.** A wrongly merged property's pin can move to the other building's point. This is rare across towns (34 of 1,580 sampled live conflicts), and the count barely depends on the order. The W3 census hands those ids to autodedup.
- **The choice can lag.** A re-resolve that changes WHICH ad ranks first waits for the next rollup of that property:
  - one sweep cycle at worst (the sweep resumes from a cursor across budget-stopped runs; the last run took 1,051,811 ms and started about 6 h late);
  - this is the same bound the canonical ad has today;
  - the chosen ad's point, label and codes still refresh at every */15 rebuild.
- **The CHECK fails loudly.** A future writer that sets one pointer without the other fails, which is intended. VALIDATE scans about 1.01M rows (890,895 active + 120,013 retired) under a lock that does not block reads or writes.
- **Backfill ordering.** W4's `$pre$` gate refuses to apply until every row satisfies the CHECK.
- **Collision with the curated-set-scope branch.** 629 and the local SPA layers A/B must land first; all line numbers are re-derived at branch time.

## Out of scope (reported only)

- **Merging the same-house duplicates on Pod Sychrovem II:** 15100, 42194, 59364, 105010, 135129, 367837, 603623, all 25 990 000 Kč and 255 m². This is dedup coverage, and your call through the normal merge.
- **The map popup's subtype "—"** for commercial pins (`subtype` missing from `MAP_COLS`). A separate fix.
- **One precision vocabulary across surfaces.** The detail map always draws a dot, the phone has its own thresholds, and the PDF prints 6-decimal GPS.
- **Keying map pins and hover by `property_id`.**
- **Location-track items:** consensus flags for far-apart ads, centroid stacks in Brno and Ostrava, centroid radius calibration, and whether `low` confidence should drop a rung.
- **The estimation subject FACTS divergence** (by-URL and kickoff read the ad's own area and price).
- **F1:** deleting the */15 rebuild, with R6, the proximity dirty marking and the `tom_days` ruling.
- **Other owed drops:** PR-F's stored `properties.mf_*` columns, and the orphan `browse_list_mf` (owed by the merge sprint).
- **A rule mismatch:** CLAUDE.md rule 20 lists two exceptions, while docs/architecture.md:2640-2642 adds a third (location writes). Unchanged here.

## Stale docs and assumptions, each fixed in the wave that touches it

- **W1:**
  - browse-merge-consistency.md:39-40 (*/5 → */15), :244-251 and :268-277 ("the fresh rebuild already read the post-merge state" is false for a patch inside the window), :354-363 and :399-400 (closed);
  - toolkit/browse_read_model.py:16-20 and scripts/recompute_property_stats.py:423-424 ("~26 %" → 61.9 s of every 900 s);
  - migrations.yml:240-261 and 629:723-724 ("index-only" is false for the real call);
  - the plan-rail docstring's `district`.
- **W2:** CLAUDE.md:51; database SKILL.md:152, :263-264, :271-279, :291-295, :322-330; Health.tsx:89-93 (a comment, not a threshold); the architecture and design lines listed in W2.
- **W4:**
  - location-serving-contract.md:23-26 ("aggregated at read" is false: every surface reads one ad);
  - database SKILL.md:281-287 and :457-460;
  - architecture.md:1454, :3645-3647, :4093-4099;
  - CLAUDE.md rules 15 and 25 (:244);
  - claims_common.py:38;
  - the docstrings of property_location.py and estimation_runs.py.
- **Round-one figures withdrawn from this plan:**
  - "8,456 given up" (7,418 of them are dormant properties that both orders fix; the true cost of active-first is 1,036);
  - "≈84 town changes" as an active-first figure (it is the all-ads figure, 84: 9 live + 75 dormant; active-first gives 83);
  - "p90 118 m" (p90 0 m);
  - "91.6 %" as one aggregate (it varies by rung);
  - "6,368 > 2 km" (a different metric; replaced by the census);
  - "≈4.2k street/KÚ gains" (measured in W4 instead);
  - "4,044 s sweep" (last run 1,051,811 ms);
  - "357 MB vs 512 MB shared buffers" (shared_buffers is 10 GB);
  - "+18 s per rebuild" for a read-time pick (not verified);
  - "Index Only Scan, Heap Fetches ≈0" (the matview itself is not index-only for the real call).
