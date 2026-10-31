# Wave shadow: the standing autodedup gate for location waves (as built, #2225)

Gate (PROGRAM.md, "Standing gate"): every wave that moves positions or quarter codes runs one shadow
autodedup generation before it deploys, judged on the rulings ledger inside the affected blocks (the 413
post-2026-10-27 `same` pairs must hold). Binds W3, W4, W5, W5b and W6. No production write, no autodedup
pause, `DECISION_MODULES` untouched.

## The seam
The engine reads every position field through `COHORT_LOCATION_SQL` (`autodedup/export_sql.py`) and
builds the record with `export.build_location`, in the cohort export and in the live lane alike
(`incremental_lane` `SqlFacts.facts`). So the shadow rewrites the movers' `listing.location` blocks in an
exported cohort artifact and runs the harness on it. Consumers of those fields: `fingerprint.block_key_of`,
the blocking probes (`blocking.probes_town_key`, E300), `features` (`dist_norm`, `same_exact_pin`,
`pin_pop`) and the apply scope's membership (`apply.py`).

## Three steps, three commands
1. **Post-wave positions**: `location_data/resolver/what_if.py`. Re-resolves an explicit id list with the
   checkout's resolver over `drain.recompute` (the read path `--dry-run` uses: prefetch, the claim change,
   warm, the four steps; SELECTs only), on `scraper.db.connect_read_only()`. One claim change per wave:
   - `none` (W5, W5b, W6): today's claims, the branch's resolver.
   - `remine` (W4): each listing of the contract's portal re-mined under `--contract` (payload via
     `claims_intake.extract_listing`, latest stored page via `claims_intake.scan_rows` +
     `page_readers.load_bodies` + `extract_pages`; entries projected by `contracts.entries_of`), then read
     back the way `resolve_db._claims_sql` reads a bump (`what_if.restate`): per claim type the newest
     version (the re-mine's where it stated the type, today's otherwise), a type the new contract stops
     declaring is not read, operator claims and other portals' are untouched. Re-mined claims get ids above
     every stored one (`NEW_CLAIM_ID`), as an insert would.
   - `purge` (W3): one claim per (listing, claim type, licence class), the newest observation of what
     today's read returns (W3's keeper; the read already applies "newest version <= active").

   A **mover** is a listing whose new row differs from `COHORT_LOCATION_SQL`'s row today in any field
   (numbers within 1e-6: the store keeps the radius as `numeric`). Writes `positions.jsonl` (each mover's
   new row, `COHORT_LOCATION_SQL`'s columns by name) and `movers.jsonl` (`old` and `new`), and logs the dry
   run's per-portal tally (`town`, `part`, `moved_300m`, `granularity`, `old>new`, `movers`). Exit 1 when
   a page failed: a partial shadow is re-run, never judged.
2. **Base cohort, rulings, live scorer**: `python3 -m autodedup.shadow <dir>` (`autodedup/shadow.py`).
   Blocks = each mover's old and new block at E300's grain (`blocks_for`: the quarter in Praha, Brno and
   Ostrava, the town elsewhere; a split-city side with no quarter would export the whole city, so it is
   left out and counted). Calls `export.run_export` and `labels_lane.run_labels` in-process, so no
   `autodedup.iterations` row is written (`lane.run` writes it and is not called), and reads the live
   generation's scorer (`RT_CALIBRATION_SCORER_SQL`, generation `rt`) into `scorer.json`. Every statement
   runs in a READ ONLY transaction.
3. **The gate**: `python3 -m autodedup.harness shadow` (DB-free). `shadow.override` rewrites the movers'
   blocks with `build_location` (other records byte-identical); both arms run under the live scorer with
   the labels' `must_not_link.jsonl` / `must_link.jsonl` (the same body as `harness run`);
   then `evaluate --base`; `shadow.judge` reads its report into `shadow.json` and the exit code.
   **PASS** = E111 clean on the candidate and no ruled pair moved against its ruling (a `same` pair the
   base held together split, a `different` pair newly joined). A ruling newly honoured is reported, not
   failed. D83 edges (`merge_gained/lost`, `copairs_gained/lost`) touching a mover are printed for the
   operator (`--show`, all in `shadow.json`), never a fail: positions moving legitimately changes
   candidates.

## Running it for a wave (W4, realitymix@8, #2219 / PR #2220)
Needs `SUPABASE_DB_URL` (or `SUPABASE_DB_SESSION_URL`) and, for `remine`, the `R2_*` variables (the bodies
live in R2). Run from a checkout carrying this tool; the wave's contract comes from its branch.
```bash
mkdir -p shadow/w4
git show origin/feature/location-w4-realitymix-v8:contracts/portals/realitymix.yaml > shadow/w4/realitymix@8.yaml
# Candidates: active realitymix ads with a pin and no `estimated` declaration. Scope it to the apply
# scope's blocks (app_settings 'autodedup_apply_scope') by adding e.g. `AND ll.obec_kod = ANY('{...}')`.
psql "$SUPABASE_DB_URL" -At -c "
  SELECT l.id FROM listings l JOIN listing_location ll ON ll.listing_id = l.id
   WHERE l.source = 'realitymix' AND l.is_active
     AND EXISTS (SELECT 1 FROM location_claims c
                  WHERE c.listing_id = l.id AND c.claim_type = 'coordinate')
     AND NOT EXISTS (SELECT 1 FROM location_claims c
                      WHERE c.listing_id = l.id AND c.claim_type = 'precision_declaration'
                        AND c.value_text = 'estimated')
   ORDER BY l.id" > shadow/w4/ids.txt
python3 -m location_data.resolver.what_if --ids shadow/w4/ids.txt --out shadow/w4 \
    --claims remine --contract shadow/w4/realitymix@8.yaml
python3 -m autodedup.shadow shadow/w4
python3 -m autodedup.harness shadow shadow/w4/base/cohort.jsonl.gz shadow/w4/positions.jsonl \
    shadow/w4/labels --scorer shadow/w4/scorer.json --out shadow/w4/judge
```
W3: `--claims purge`; W5, W5b, W6: `--claims none` from the wave's branch. Size it before step 2: the
export prints its block count, and a block costs what its town does (Pardubice: 7,252 ads, 107 MB,
5-minute export, 180 s harness run at 2.2 GB RSS; all of Praha would be 167k ads, about 2.4 GB). A wide
wave runs per area (one id list per slice of the apply scope), never all of Praha at once.

## Rulings taken (defaults; the operator may override)
- "Approved-split sessions" (named nowhere in the repo) := the rulings ledger inside the affected blocks
  (5,578 rulings, 896 must-not-link pairs, 363 operator-merge groups at scoping); PROGRAM.md reworded.
- Pass bar: a ruled pair moving against its ruling fails, as does a revoked E110; D83 deltas are reported.
- No production write: read-only transactions throughout, exporters in-process without the ledger row.

## Caveats
- The harness re-cuts the `pin_pop` calibration over the cohort; live keeps the seed's
  (`rt_equivalence` `calibration_cohort`).
- Moves reach the live store lazily (re-decided, never forced): the shadow shows the eventual state.
- Cohorts are per block: a mover's candidates in other blocks (an unknown-quarter ad of a split city,
  location-free probes such as photos and text) are outside the cohort.
- A mover whose OLD side is in no exported block (no row today, or a split-city row with no quarter) is in
  neither arm; the gate prints `movers in cohort M of N`. W5's about 6k Praha rows that gain a quarter fall
  here: covering them needs an id-grain export block (owed before W5's shadow, not built).
- `remine` leaves the text lane's reading claims as they are (no stored reading is re-read); realitymix
  has none. `purge` applies W3's keeper to today's read; W3's own branch replaces that read, so its
  shadow keeps the version window's read for this step.
- `connect_read_only` is transaction-scoped (`BEGIN READ ONLY`, psycopg's `read_only`), never a session
  `SET`, which a pooler would leak to the next client; a statement outside a transaction is not covered,
  and none of these steps runs one.

## Brake alternative (for the record)
Interval 10 s → 0, deploy, re-mine (about 1 h) + resolve, export, harness against a pre-deploy export,
resume or roll back: saves only step 1, costs a 3–6 h nationwide merge pause, needs the operator twice,
measures after positions are already served; rollback = entry-scoped retract + re-resolve + second re-mine.
