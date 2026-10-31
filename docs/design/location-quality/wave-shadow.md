# Wave shadow: the standing engine gate for location waves (as built, #2225)

Gate (PROGRAM.md, "Standing gate"): every wave that moves positions or quarter codes runs one shadow
engine generation before it deploys, judged on the rulings ledger inside the affected blocks (the 413
post-2026-10-27 `same` pairs must hold). Binds W3, W4, W5, W5b and W6. No production write, no engine
pause, `DECISION_MODULES` untouched.

## The seam
The engine reads every position field through `COHORT_LOCATION_SQL` (`autodedup/export_sql.py`) and
builds the record with `export.build_location`, in the cohort export and in the live lane alike
(`incremental_lane` `SqlFacts.facts`). So the shadow rewrites the movers' `listing.location` blocks in an
exported cohort artifact and runs the harness on it. Consumers of those fields: `fingerprint.block_key_of`,
the blocking probes (`blocking.probes_town_key`, E300), `features` (`dist_norm`, `same_exact_pin`,
`pin_pop`) and the apply scope's membership (`apply.py`).

## No write, by construction
Every statement runs inside a `conn.transaction()` on `scraper.db.connect_read_only()`: `connect()` (the
transaction pooler) with psycopg's `read_only`, so each transaction opens `BEGIN READ ONLY` and the server
refuses a write (`tests/scraper/test_connect_read_only_live.py`, in the schema-replay job). Nothing is set
on the session, which a pooler would hand to the next client. `autodedup.shadow.read_cursor` is the only
way `location_data.what_if` and `autodedup.shadow` reach SQL, and the exporters' own `_run` opens a
transaction per statement; an AST rail (`tests/location_data/test_what_if.py`) pins all four. The cohort and
rulings exports are called in-process (`run_export`, `run_labels`), never through `lane.run`, the writer
of the `autodedup.iterations` row; an import rail proves no shadow path even loads `autodedup.lane`.

## Three steps, three commands
1. **Post-wave positions**: `location_data/what_if.py`. Re-resolves an id list (`--ids <file>` or
   `--ids-sql "<SELECT>"`, run as one subquery on the extended protocol, so `COMMIT; ...` cannot escape
   the read-only transaction; `--scope apply` keeps the ids the live apply scope admits, by
   `apply.Scope.outside`; `--limit N` the lowest N) with the checkout's resolver over `drain.recompute`
   (the `--dry-run` read path: prefetch, the claim change, warm, the four steps). One claim change a wave:
   - `none` (W5, W5b, W6): today's claims, the branch's resolver.
   - `remine` (W4): each listing of the contract's portal re-mined under `--contract` (payload via
     `claims_intake.extract_listing`, latest stored page via `claims_intake.scan_rows` +
     `page_readers.load_bodies` + `extract_pages`; entries projected by `contracts.entries_of`; claims
     read back by `resolve_db.claim_from_intake`), then `what_if.restate`: `resolve_db._claims_sql` after
     the bump (admissible licence, per claim type the newest version, a type the new contract stops
     declaring not read, operator claims and other portals' untouched), pinned to that SQL's text by a
     test. Re-mined claims get ids above every stored one (`NEW_CLAIM_ID`), as an insert would. A stored
     page that would not re-mine (not fetched, refused, scope incomplete) keeps today's claims and is
     counted failed, so the run exits 1.
   - `purge` (W3): one claim per (listing, claim type, licence class), the newest observation of what
     today's read returns (W3's keeper; the read already applies "newest version <= active").

   **`restate` and `purge` are deleted when W3 lands** (W3 replaces the read; `what_if --claims purge` then
   reads all claims raw and applies the keeper rule).

   A **mover** is a listing whose new row differs from `COHORT_LOCATION_SQL`'s row today in any field
   (numbers within 1e-6: the store keeps the radius as `numeric`). Writes `positions.jsonl` (each mover's
   new row, `COHORT_LOCATION_SQL`'s columns by name), `movers.jsonl` (`old` and `new`) and `what_if.json`
   (ids requested, scope, limit, failures), and logs the dry run's per-portal tally (`drain.tally` over
   `drain.StoredRow`, by name: `town`, `part`, `moved_300m`, `granularity`, `old>new`, `movers`). Exit 1
   when a page or a re-mine failed: a partial shadow is re-run, never judged.
2. **Size, then export**: `python3 -m autodedup.shadow <dir>`. Blocks = each mover's old and new block at
   E300's grain (`blocks_for`: the quarter in Praha, Brno and Ostrava, the town elsewhere; a split-city side
   with no quarter would export the whole city, so it is left out and counted). It prints every block with
   its ad count first (`block_sizes`, the export's own selection) and refuses, exit 2, above
   `--max-blocks` (40) or `--max-ads` (60,000). Then the cohort, the rulings and the live generation's
   scorer (`RT_CALIBRATION_SCORER_SQL`, generation `rt`) into `scorer.json`.
3. **The gate**: `python3 -m autodedup.harness shadow` (DB-free). `shadow.override` rewrites the movers'
   blocks with `build_location` (other records byte-identical); both arms run under the live scorer with
   the labels' `must_not_link.jsonl` / `must_link.jsonl` (the same body as `harness run`); then
   `evaluate --base`; `shadow.judge` writes `shadow.json` and sets the exit code.
   **PASS** = E111 clean on the candidate and no ruled pair moved against its ruling (a `same` pair the
   base held together split, a `different` pair newly joined). A ruling newly honoured is reported, not
   failed. **PARTIAL** labels a verdict whenever `limit` cut the ids or a mover sat in no exported block
   (`movers in cohort M of N`); it is still judged. The 413-style recent `same` rulings (newest word
   `same`, written since `--recent-since`, default 2026-10-27) are counted apart: judged, held, broken,
   apart in both, unjudged (an ad outside the cohort: printed, never a fail). D83 edges
   (`merge_gained/lost`, `copairs_gained/lost`) touching a mover are printed for the operator (`--show`,
   all in `shadow.json`), never a fail: positions moving legitimately changes candidates.

## Running it for a wave (W4, realitymix@8, #2219 / PR #2220)
Where the secrets live, as the dispatch-only workflow `.github/workflows/location_wave_shadow.yml` (no
cron, one run at a time, 300 minutes; dispatchable once it is on `main`, GitHub's rule for a new
`workflow_dispatch`):
```bash
gh workflow run location_wave_shadow.yml --ref main -f wave=w4-realitymix-v8 \
    -f claims=remine -f scope=apply -f limit=50 \
    -f contract_ref="origin/feature/location-w4-realitymix-v8:contracts/portals/realitymix.yaml"
```
The ids_sql input defaults to the W4 candidates (active realitymix ads with a pin and no `estimated`
declaration); edit it or add a `LIMIT`. No movers: steps 2 and 3 are skipped and the run ends green. The
artifact `wave-shadow-<wave>-<run id>` (30 days) holds `positions.jsonl`, `movers.jsonl`, `what_if.json`,
`scorer.json` and `judge/` (both arms' `run.json`, `clusters.json`, `pairs.jsonl.gz`, `evaluate.json`,
and `shadow.json`), never a cohort artifact. `--ref` decides the resolver: W4 changes only a contract, so
`main`; W5, W5b and W6 dispatch from their own branch once it has merged `main`.

By hand, with `SUPABASE_DB_URL` and (for `remine`) the `R2_*` variables:
```bash
mkdir -p shadow/w4
git show origin/feature/location-w4-realitymix-v8:contracts/portals/realitymix.yaml > shadow/w4/realitymix@8.yaml
python3 -m location_data.what_if --out shadow/w4 --scope apply --claims remine \
    --contract shadow/w4/realitymix@8.yaml --ids-sql "
  SELECT l.id FROM listings l
   WHERE l.source = 'realitymix' AND l.is_active
     AND EXISTS (SELECT 1 FROM location_claims c
                  WHERE c.listing_id = l.id AND c.claim_type = 'coordinate')
     AND NOT EXISTS (SELECT 1 FROM location_claims c
                      WHERE c.listing_id = l.id AND c.claim_type = 'precision_declaration'
                        AND c.value_text = 'estimated')"
python3 -m autodedup.shadow shadow/w4
python3 -m autodedup.harness shadow shadow/w4/base/cohort.jsonl.gz shadow/w4/positions.jsonl \
    shadow/w4/labels --scorer shadow/w4/scorer.json --out shadow/w4/judge
```
W3: `--claims purge`; W5, W5b, W6: `--claims none` from the wave's branch. A block costs what its town does
(Pardubice: 7,252 ads, 107 MB, 5-minute export, 180 s harness run at 2.2 GB RSS; all of Praha would be
167k ads, about 2.4 GB); over the caps, narrow the ids (one id list per slice of the apply scope).

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
  neither arm and makes the verdict PARTIAL. W5's about 6k Praha rows that gain a quarter fall here:
  covering them needs an id-grain export block (owed before W5's shadow, not built).
- `--scope apply` judges membership on today's location: a mover entering the scope from outside is not
  selected.
- `remine` leaves the text lane's reading claims as they are (no stored reading is re-read); realitymix
  has none.

## Brake alternative (for the record)
Interval 10 s → 0, deploy, re-mine (about 1 h) + resolve, export, harness against a pre-deploy export,
resume or roll back: saves only step 1, costs a 3–6 h nationwide merge pause, needs the operator twice,
measures after positions are already served; rollback = entry-scoped retract + re-resolve + second re-mine.
