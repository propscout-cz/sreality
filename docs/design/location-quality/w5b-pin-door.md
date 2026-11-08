# W5b: resolver v5.11. What is certain gets a door (as built, 2026-11-02)

**Status: rebuilt 2026-11-02 (#2338, PR #2339) on W5's branch; merges after W5.** Ruling Q8 (a), the operator, 2026-11-02: "What is certain gets a door." It narrows Q8 ("the map is the source when it is there") and replaces the first build's 5 m for every portal and ad type.

## The rule

**A flat or a house, its words naming the street, its pin within 1 m of the door, the words not disagreeing.** Commercial, plots and other get no door from a pin. No bands, flags or estimates.

`bind.pin_door` runs in `core` between `pin_inside_bound_obec` and `place`. It reads the ad type first and refines the row when all of these hold:
- `listings.category_main` is `byt` or `dum` (`PIN_DOOR_AD_TYPES`; NULL gets no door);
- the words bound a street (`target_kind` street). **F1, the operator's ruling Q24 (2026-11-07):** a town or a část alone gets no door. The known false doors were geocoded village-centre points shared by many ads (1,251 shared points under 4,755 ads); the cost is about 4,183 village ads that keep their pin, labelled approximate. A street line naming a část (`street_names_part`) binds that část, so it gets none either. A pin may still settle which town a shared name means (`narrow_towns`): the name matched, so the word bound;
- the pin is exact (`declared.blurred` false, so area markers and W4's `estimated` are out; an undeclared pin counts, unlike `core`'s `pin_is_precise`) and inside the bound town;
- exactly one valid RÚIAN door lies within `PIN_DOOR_M` (1 m) of it;
- the door is on that street and agrees with the words' obec, every část they claim and their PSČ;
- the words state nothing a door cannot be checked against.

The row gets that address point's keys at rung `R5`: `ruian_adm_kod`, house number, street, PSČ, KÚ, část, address grain. **Its position stays the ad's own pin** (origin `portal_pin`; engine owner's review, 2026-11-02): on the door point, two ads with different pins within 1 m of one door would share one `pin_key` (6 decimals), so `same_exact_pin` and pin population would fire on nothing the ads said. So a mover's position does not move, since an exact pin inside the words' town already placed it, with one exception: a row on its street's point because its exact pin was off the street (`pin_off_street`) now lands on that pin, and the flag goes. The shadow's `position_moved` lists these. No reader needs a pin-door row on the door: nothing outside the resolver reads `Position.origin`, and no surface compares a row's `geom` with its door. Any refusal is silent: the words stand. The door's část is adopted where the words claimed none. A pin cannot corroborate the door it named (`grade._pin_corroborates` requires `registry_point`), so such a row never grades `exact`.

The refusals from the words (`names_a_door` reads the stored claims; `Binding.words_unchecked` and `words_part_ids` are BIND's):

| The words state | Why it refuses |
|---|---|
| Any stored claim of a door type (`WORD_DOOR_CLAIM_TYPES`), parsed or not | A door the words named |
| A digit in the street line, parsed or not (`12a`, `12-14`, `12,`) | A number R1 could not bind. `28. října` pays for this one rule |
| A locality line the town was only composed from (`Praha 4`, `Praha 4 - Podolí`, `Praha - Vokovice`) | Closes a MOMC: its borders cross části and nothing here checks it |
| A part claim that matched no část (a MOMC is none) | Nothing to check against |
| A část the door is not in, at every rung | R2 takes the register's část and R3 carries none, so BIND keeps what the words claimed |
| A town word the town did not come from: unknown, or out of reach, with the PSČ or a part claim binding the town | The door would stand against that word, though obec, část and PSČ agree. With no town word, the PSČ's or the part's town may still anchor a door |
| An okres or kraj word the bound town is not in, read as `narrow_towns` reads it | The words disagree. A word the register does not hold at that level (`Praha` as an okres, `Vysočina` as a kraj) is a spelling and does not refuse. Measured 2026-11-02: 0 rows of the 4,230 lone doors (1 in 10) |
| An okres word naming a MOMC the door is not in (`Praha 5` on a Praha 4 door; a word naming the door's own MOMC keeps it) | The door's `momc_unit_id` rides the door read, no second lookup. Measured 2026-11-02: of 4,230 lone doors (1 in 10), 22 carry such a word, 11 on their own MOMC, 11 refused, ≈ 110 rows. A portal's `Praha N` can mean the wider správní obvod; such an ad loses a door, which costs nothing |
| A PSČ the door does not carry | `door.psc` is on the read FILL makes anyway. A claim the normalizer rejected states no PSČ and is not read |

**Why ceskereality gains části.** Its contract declares no část: its words are the town, the okres and rarely a street. Of the scoping run's 2,405 ceskereality movers gaining one (2,311 at obec grain, 94 at street grain), a 1-in-12 id sample of 213 held no `cast_obce_name` claim, a town word equal to the row's town on all 213, no KÚ word, and 7 street claims naming no část. So the door names a část where the words were silent, and none disagrees with a stated one. On 30 sampled non-main-part ads the page title and locality name the door's část on none. On the 21 of them whose title states a street, the door's street is that street. The door's část is the town's main část by name on 1,428 of the 2,405 and another on 977. Since F1 only the 94 at street grain still move.

## Q23 (a): an ambiguous door yields none

The operator's ruling of 2026-11-07, as GLOSSARY.md's RÚIAN door states it: an ad has a RÚIAN door only when that door is certain ("co je jisté, dostane bod, žádné odhady", 11-01). Before it, R1 bound every register point sharing the stated number, and the same-rung tie (`at_top`) marked the row ambiguous while the pick followed `ORDER BY kod_adm`. `bind._stated_doors` now narrows the points the register holds for the stated č.p. and č.o.:
- the č.o.'s letter exactly (case-insensitive). No letter stated: the letter-less entrance, else the lettered ones. The letter rides from `normalize` (a street line's `3389/8b`, a č.o. claim's `8b`, "8 b" too) through `Constraints` and `StreetBind`, with its č.o. as one unit from one source: R1 reads the street line's č.o. first, as it reads its č.p. (unchanged; a claim outranks the line only in `collect_constraints`, a mismatch filed separately). The register read is unchanged, since it already answers each point's `znak_orientacniho`;
- a č.o. or a letter that no point carries is no door, as before W5b: the words contradict the register (č.p. 1234 as Stočesova 1234/8 and Bořetická 1234/2, words "Stočesova 1234/2", stand at Stočesova);
- a PSČ the words state settles it when it leaves exactly one (the words decide; about 14 ads);
- none or more than one left: no point candidate. R2 binds the street, so the row stands at its street point, street grain, and the map says "Přibližná poloha". The pin cannot add a door: the street line's number refuses it (`names_a_door`).

R1 inside a část (no street) reads the letter the same way and still wants exactly one door; none keeps the row at its část. R0 binds one kód and R3 binds no door, so neither can pick among doors.

**The cohort.** Production, sreality-33, 2026-11-07: of about 1,186 active ads first counted as ambiguous, 538 name exactly ONE point once the letter is read, and 245 of those stood on the WRONG entrance (words 3389/8b bound 8a; 777/9c bound 9; 1065/32 bound 32a beside a plain 32): the letter fix corrects them. The ruling's cohort is the about 649 genuine ties, mostly ceskereality č.p.-only ads (19338297, Stočesova 1168, entrances 3 and 1); they stand at their street.

## Why 1 m, and why flats and houses

The bar is how often two stated doors of one property agree. A pin-named door must agree with a sibling ad's stated door as often (production, 2026-11-02, the operator's measurement):

| Ad type | Two stated doors | Pin door, 1 m | Pin door, 5 m |
|---|---|---|---|
| Flat (`byt`) | 95.70 % | 96.99 % | 94.5 % |
| House (`dum`) | 99.47 % | 99.33 % (within noise) | 99.0 % |

## The ad type

`resolve_db._SOURCES_BULK_SQL` reads `id, source, category_main`; `sources_bulk` and `_Slice.sources` hand (source, ad type) to `core.resolve(ad_type=…)`. `category_main` is the canonical vocabulary every portal writes (`byt`, `dum`, `pozemek`, `komercni`, `ostatni`; 2 NULLs among 395k active ads), not a place value, so rule 25's "listings carry no location value" and its claim-type ceiling stand. `doors_near` is asked only for a flat or a house, though `warm_points` still warms every claim coordinate.

**A fourth input.** The purity scan does not mind: the core stays pure, and the type is an argument. But "the row is a pure function of its claims and the register" no longer holds alone. The type is not in `claim_set_hash`: migration 524's `has_claims` reads that hash against `sha256('[]')`, so hashing a type would flip a claimless flat from `active_no_claims` to `active_unresolved`. So the three stamps name no ad type. Two rows stamped alike can differ by type, and what keeps a row current is the re-resolve queue below.

**A type change queues the row.** A flat turned commercial must lose its door, so the statement that changes `category_main` also enqueues that listing into `dirty_locations`, bumped as a claim change is, in the same transaction. The reason is `claim_insert`, the queue's "this listing's evidence moved" (697 allows three values; no migration). It is one CTE, `listing_write.resolve_requeue_cte`, inside the upsert, so the hot path gains no round trip, only a join over the batch; only a row whose value moved queues, and a new row (no `prior`) never does. Against a row a resolver slice holds, the write waits for that slice as `claims_store` does; the drain takes no conflicting lock on `listings`, so no deadlock. The column's writers, read from the code on 2026-11-02:
- `scraper/listing_write.py` `_upsert_sql`, the one `INSERT INTO listings`: covered;
- `scripts/reparse.py` `_update_sql` when `--fields` names it: covered, the moved rows only;
- no migration heal, trigger or other script writes it. `properties.category_main` (`recompute_property_stats`) and the sold table (`sold_db`) are other tables. A future bulk `UPDATE` outside these two owes its own enqueue, because nothing else notices: the sweep (`_SWEEP_SQL`) compares only the resolver and registry versions, so such a row waits for the next version or registry bump. The census cannot see a DB-side writer.

## The registry read

`RegistryView.doors_near(lat, lon)` answers at most two kódy. `_DOORS_NEAR_SQL` has the same array shape as `CONTAINING_OBEC_SQL`, its `ST_Expand(… / 60000)` box letting `ruian_ap_geom_gist` drive. "Exactly one?" does not depend on which two, so nothing orders or measures them. `warm_points` asks three point-keyed questions: one extra round trip per slice.

## The scorecard (migration 736)

The operator retired the radius on 2026-11-01; W6 becomes deletion only. So the pin sample has no reader. 736 (first numbered 717, renumbered above W6-c1's 733) is `create or replace` of the one view, 733's text (698's plus the `position` metric) but for:
- the `sample` cohort is gone: the `smp` and `pin` CTEs, `pin_error_m`, `radius_cover`;
- determinism, W5's deferral: the containment probe orders by code behind an `OFFSET 0` fence, the same text as `CONTAINING_OBEC_SQL`. `EXPLAIN` rail: the fenced plan reaches `ruian_aug_pip_gist` before `ruian_admin_units`; a bare `ORDER BY` plan does not.

The gauge reads the view in one statement and loses `{p}/accuracy` with its two thresholds: 57 cells, five per portal. The door scorecard is what stood already: `{p}/street`, the `grain` rows (the town cell's `positions` since W6-c1), `{p}/ku`, `{p}/outside`. `listing_location.uncertainty_radius_m` and `grade.RADIUS_M` are untouched; deleting them is W6's.

## Rollout

Prerequisites: W5 merged and swept; W4's realitymix@8 re-mine done (otherwise hidden server geocodes on doors would refine).

1. Shadow from the W5b branch, once it has merged `main`. `ids_sql` (claims none, `wave-shadow.md`), one arm per amendment. Measured on production 2026-11-07: the F1 arm 28,054 ads; the Q23 arms 1,187 low-graded doors, 3,468 numbered ads not at a door and 1,057 doors whose words carry a lettered č.o.:
```sql
SELECT l.id FROM listings l JOIN listing_location ll ON ll.listing_id = l.id
 WHERE l.is_active AND ll.country_status = 'cz' AND (
   (l.category_main IN ('byt', 'dum') AND ll.granularity = 'street'
    AND coalesce(ll.disputed, '') NOT IN ('pin_outside_obec', 'pin_outside_cz', 'country_conflict')
    AND NOT EXISTS (SELECT 1 FROM location_claims w WHERE w.listing_id = l.id
                     AND w.claim_type IN ('address_point_id', 'house_number_cp', 'house_number_co'))
    AND EXISTS (SELECT 1 FROM location_claims c
                 WHERE c.listing_id = l.id AND c.claim_type = 'coordinate' AND c.value_geom IS NOT NULL
                   AND EXISTS (SELECT 1 FROM ruian_address_points ap WHERE ap.valid_to IS NULL
                                 AND ap.geom && ST_Expand(c.value_geom, 1 / 60000.0)
                                 AND ST_DWithin(ap.geom::geography, c.value_geom::geography, 1))))
   OR (ll.granularity = 'address_point' AND ll.match_confidence = 'low')
   OR (ll.granularity <> 'address_point' AND EXISTS (SELECT 1 FROM location_claims h WHERE h.listing_id = l.id
         AND (h.claim_type IN ('house_number_cp', 'house_number_co')
              OR (h.claim_type = 'street_name' AND h.value_text ~ '[0-9]'))))
   OR (ll.granularity = 'address_point' AND EXISTS (SELECT 1 FROM location_claims h WHERE h.listing_id = l.id
         AND h.claim_type IN ('house_number_co', 'street_name') AND coalesce(h.value_text, '') ~ '[0-9][[:space:]]*[[:alpha:]]')))
```
   `what_if` keeps real movers. Dispatch `location_wave_shadow.yml --ref feature/location-w5b-pin-door -f wave=w5b -f claims=none -f scope=apply -f all_blocks=true -f group=1/1 -f ids_sql="…"`, then the printed `i/n` groups with `positions_run_id`. **`all_blocks=true` is required here:** by default only blocks holding a ruling are exported, and most of this wave's movers have none, so the door report would not see them. No gate runs until W5's sweep ends.

   **Scope.** `scope=apply`, `all_blocks=true`, nothing outside the apply scope. The gate result says nothing about blocks outside it, and the run prints so.

   **The gate is a sample of groups** (engine owner, 2026-11-02; restated for F1 and Q23, 2026-11-07). The refused scoping run prints the ids, movers, blocks, ads and groups needed under the caps, every group's counts and the picks, reproducible by re-running step 2 with `positions_run_id`:
   - **Stage 1** (`shadow.gate_sample`): up to 3 groups ranked by the Q23 losers (a door in the stored row, none in the candidate) whose old obec holds their old č.p., same `typ_so`, in 2 or more current části, then by all Q23 losers, ties to the lowest group number; and 1 group with the most part-gaining movers, any portal. Deduplicated. The spanning read (`shadow.spanning_losers`) is one read-only registry query in the REFUSED branch, bounded by the losers' ids. A stage 1 fail stops the gate and goes to the operator.
   - **Stage 2:** `gate_sample` prints the remaining groups ranked by `sha256("w5b-gate-" + group number)`, the first 12. As run on 2026-11-07, the engine owner chose instead the next 17 groups by the stage 1 ranking plus the first 3 Q23-free groups of the sha256 order as controls, one at a time: the 20k cap (a 57k group died after 3 h 12 min of judging) cut the wave into 421 small groups, where 12 random ones covered too few Q23 losers.

   **What it does and does not say.** The result states that it is a sample: on 2026-11-07, 24 of 421 groups held 5,429 of 13,752 movers and 365 of 507 Q23 losers. If it finds nothing harmful, harmful pairs in the groups not run can still be missed, more so if they cluster, because a group is one contiguous run of block keys. `addr_new` and `cap_displaced` are seen only inside the groups run. The result states the share of the movers and of both kinds of Q23 loser covered, per source, from the run's `gate sample` line.

   **Result (2026-11-08, all 24 groups read by the engine owner): HOLD for one class.** 0 wrong positions, 0 real splits, 6 property-changing gains all the same flat. The harm: the unit-designator veto (`autodedup/guards.py` `unit_designator_conflict`) compares `address_block_key`, so a mover gaining a door on street S no longer meets a partner left at `street:S`. 12 real lost vetoes, 1 fused wrong merge (flats 905 and 1005 on one Mukařovského door). The shadow's cohort is active ads only, so a split or loss whose partner is inactive is real only when that partner's own v5.11 answer is not the same door (the sweep re-resolves inactive ads after active ones). Operator ruling 2026-11-08: deploy nothing now; the next engine deploy carries the common-grain fix, and W5b deploys after it, once the engine owner has re-read the class.

   **Group counts** (`[shadow] group i/n: …`, printed when step 2 is REFUSED): movers, Q23 losers and those of them on a č.p. spanning 2+ části, movers per source, part-gaining movers per source. A mover counts once, at the group that exports its stored row: its old block's, else (unquartered) the `ids` block's; a new block in another group holds only its counterparts. `chunks` cuts the sorted block keys, so a dispatched `i/n` holds exactly those blocks. A block that does not fit the caps waits for the engine owner.

   **Guard.** If `e242` and `e272` read 0 for both `base` and `new` in every group, that is not a pass: the report is tested on a real pair first.

   **The pass rule.** Rulings broken 0 and new E111 0, as `harness shadow` exits (0 = pass). Then the engine owner reads `shadow.json`'s `door` in every group: `e242.new`, `e272.new`, `merge_gained`, `merge_lost`, all of them, or a random 60 per kind if there are more. Any loss or new blocker on a pair that reads as one property fails. Any gain that reads as different fails. A fail goes to the operator.

   **Reading `door`** (`autodedup.shadow.door_report`, base against candidate, one per group; it never sets PASS):
   - `state` and `started_at`, beside `door`: `judged`, or `nothing_exported`, a valid zero with `pass` null and `crossing_pairs.zero` naming why. Step 2 writes that `shadow.json`, since step 3 is skipped. A failed piece has none.
   - `piece`: `first`, `last`, `blocks`: the span of block keys this group judged, also in the log, so coverage can be confirmed.
   - `crossing_pairs`: `movers`, `pairs`, `by_zone`, `merge_pairs` (the merge zone's `[lo, hi]`, so overlapping pieces dedupe): the live store's pairs (`autodedup.pairs`, generation `rt`) a mover of this cohort holds with an ad outside it. A run pairs only the ads it was given, so no arm can judge these; the engine owner reads the number before calling a group read. Step 2 reads them, bounded by the piece's mover ids, and step 3 reports them. Null = not read.
   - `door_gainers`: ads with no door in the base and one in the candidate.
   - `position_moved`: `count`, and `listings` as `[listing_id, metres]`: movers whose `pin_key` changed.
   - `e242`, `e272`: `base` and `candidate` counts, `new` and `gone` as `[lo, hi, zone]`. Stored rows touching a mover. E242 is `stored_house_number_conflict`; E272 is `_filed_apart` on a pair stating two storeys.
   - `same_house_number_to_1`: `[lo, hi, zone]`, stored rows now reading 1 that did not.
   - `addr_new`: `[lo, hi, zone]`, candidate pairs the `addr` probe now reaches. Zone null = under the store floor.
   - `cap_displaced`: `[lo, hi, base zone, source pair, část gained]`, base pairs still sharing a key that neither side retrieves now, because a side sits at the fan-out cap (60). `část gained` is true when a mover in the pair gained one.
   - `merge_gained`: `[lo, hi]`; `merge_lost`: `[lo, hi, source pair, část gained]`. Merge-zone rows, `evaluate --base`'s own lists.
   - `block_key_split`: `count`, and `pairs` as `[lo, hi, zone, source pair]`: pairs with a stored decision in either arm that shared a `block_key` in the base and not in the candidate, read off the fingerprints (`block_key_of`). A door's část moves a row from `o<obec>` to `c<cast>`, and `attr_dispo` and `attr_area` key on it. Zone is the candidate's, null when the pair is gone there.
   - `two_doors_one_building`: `[lo, hi, door, door, zone]`, a door-gaining pair on two different doors with one č.p. and one část. That is the building proxy, since the mirror holds no building code.

   Both blockers read the settings the arms ran under: `harness.cmd_shadow` builds them from `scorer.json` (the live `autodedup.rt_calibration` row: `d43_stored_house_number` guarded, `d43_floor_sequential_address_split` true, `d43_house_number_entrance` stored, as `autodedup/settings/w31.json`) and passes them to `door_report`. Under the `Settings` defaults both are off.

   Read a pair with `python3 -m autodedup.harness pair`. The log prints the counts per group. Blockers and flips read the stored rows, so a pair under the store floor is not in them; retrieval uses `BlockIndex` in each arm, the same fill the run uses.
2. Only after the engine with the designator fix is live. Apply `736` immediately before the merge (733 first: the view reads its matview). Between apply and deploy the old gauge reads no sample row, so its accuracy cells read Not measured for minutes. New code on the old view is harmless: it ignores the sample rows.
3. Merge; W2's reconcile sweeps.

## Acceptance (W1 cells; read after `freshness/version` clears)

**The fire count.** Read on production 2026-11-02 by SQL, not by the resolver, on 1 in 10 active flats and houses at street, quarter or obec grain with an exact pin, no door claim and no dispute: 7,371 candidates, 4,230 with one door within 1 m, 242 of those refused by the words (the part and locality checks compare names, so approximately), **3,988 fire**. The town-word refusal added after review takes about 113 per 1 in 10 (≈ 1.1k rows, a name comparison): all of them were already inside that 242, because the read refused every town line that is not the row's town by name, so the figure stands. So **≈ 40k rows gain a door**: flats ≈ 21.9k, houses ≈ 18.0k; from street ≈ 24.5k, quarter ≈ 8.1k, obec ≈ 7.3k. The read's rows were not all at v5.10. The shadow's mover count is the real figure. **F1 (Q24, 2026-11-07) removes the quarter and obec movers**: the operator's count is about 4,183 village ads that keep their pin, labelled approximate.

| Measure | After |
|---|---|
| Address-grain rows | up by the pin's street movers, down by the Q23 losers (the shadow's mover counts decide) |
| `{p}/street` | not unchanged: a Q23 loser goes from address to street grain (still inside the share), one bound inside a část from address to quarter (out of it), and the letter fix inside a část can move a quarter row to address grain (into it); the pin's movers were already at street grain |
| `{p}/ku` | up (a door carries its KÚ) |
| `{p}/outside` | no higher |
| Pin-named doors on a non-flat, non-house ad, over 1 m from their pin, on another street than the bound one, or in another část than the claimed one | 0, once the queue has drained |
| Town cells, `disputed` mix | unchanged |
| Shadow | PASS on every group |

## Ledger

Rule 25 asks a location PR that adds more than it deletes to say why. The operator approved the net addition (2026-11-02): Q8 reverses W2-a's "a pin never yields a street" and adds a rung, a registry question, an input and its enqueue. The PR body carries the measured numbers.
