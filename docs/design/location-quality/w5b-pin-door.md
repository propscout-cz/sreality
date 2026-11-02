# W5b: resolver v5.11. What is certain gets a door (as built, 2026-11-02)

**Status: rebuilt 2026-11-02 (#2338, PR #2339) on W5's branch; merges after W5.** Ruling Q8 (a), the operator, 2026-11-02: "What is certain gets a door." It narrows Q8 ("the map is the source when it is there") and replaces the first build's 5 m for every portal and ad type.

## The rule

**A flat or a house, its pin within 1 m of the door, the words not disagreeing.** Commercial, plots and other get no door from a pin. No bands, flags or estimates.

`bind.pin_door` runs in `core` between `pin_inside_bound_obec` and `place`. It reads the ad type first and refines the row when all of these hold:
- `listings.category_main` is `byt` or `dum` (`PIN_DOOR_AD_TYPES`; NULL gets no door);
- the words bound a town, a street or a část (`target_kind` street or admin_unit, a town the pin did not establish);
- the pin is exact (`declared.blurred` false, so area markers and W4's `estimated` are out; an undeclared pin counts, unlike `core`'s `pin_is_precise`) and inside the bound town;
- exactly one valid RÚIAN door lies within `PIN_DOOR_M` (1 m) of it;
- the door agrees with the words' obec and street, every část they claim and their PSČ;
- the words state nothing a door cannot be checked against.

The row becomes that address point at rung `R5` and stands on the door (origin `pin_door`). Any refusal is silent: the words stand. The door's část is adopted where the words claimed none. A pin cannot corroborate the door it named (`grade._pin_corroborates` requires `registry_point`), so such a row never grades `exact`.

The refusals from the words (`names_a_door` reads the stored claims; `Binding.words_unchecked` and `words_part_ids` are BIND's):

| The words state | Why it refuses |
|---|---|
| Any stored claim of a door type (`WORD_DOOR_CLAIM_TYPES`), parsed or not | A door the words named |
| A digit in the street line, parsed or not (`12a`, `12-14`, `12,`) | A number R1 could not bind. `28. října` pays for this one rule |
| A street line that bound nothing, `ambiguous_streets` included | The door may be on another street. A line naming a část (`street_names_part`) works as that část |
| A locality line the town was only composed from (`Praha 4`, `Praha 4 - Podolí`, `Praha - Vokovice`) | Closes a MOMC: its borders cross části and nothing here checks it |
| A part claim that matched no část (a MOMC is none) | Nothing to check against |
| A část the door is not in, at every rung | R2 takes the register's část and R3 carries none, so BIND keeps what the words claimed |
| A PSČ the door does not carry | `door.psc` is on the read FILL makes anyway. A claim the normalizer rejected states no PSČ and is not read |

## Why 1 m, and why flats and houses

The bar is how often two stated doors of one property agree. A pin-named door must agree with a sibling ad's stated door as often (production, 2026-11-02, the operator's measurement):

| Ad type | Two stated doors | Pin door, 1 m | Pin door, 5 m |
|---|---|---|---|
| Flat (`byt`) | 95.70 % | 96.99 % | 94.5 % |
| House (`dum`) | 99.47 % | 99.33 % (within noise) | 99.0 % |

## The ad type

`resolve_db._SOURCES_BULK_SQL` reads `id, source, category_main`; `sources_bulk` and `_Slice.sources` hand (source, ad type) to `core.resolve(ad_type=…)`. `category_main` is the canonical vocabulary every portal writes (`byt`, `dum`, `pozemek`, `komercni`, `ostatni`; 2 NULLs among 395k active ads), not a place value, so rule 25's "listings carry no location value" and its claim-type ceiling stand. `doors_near` is asked only for a flat or a house, though `warm_points` still warms every claim coordinate.

**A fourth input.** The purity scan does not mind: the core stays pure, and the type is an argument. But "the row is a pure function of its claims and the register" no longer holds alone, so `claim_set_hash` covers the type where there is one. The three stamps still pin the answer.

**A type change does not re-resolve the row.** The enqueue is claims-only: `dirty_locations` is written by `claims_store` when a claim's value changes, by the operator's edit, and by the sweep. A portal moving an ad from flat to commercial, with unchanged location claims, appends a snapshot and enqueues `dirty_properties` (rules 2, 20), and the resolver is not told. The row keeps its door until that ad's next claim change or the next sweep (a resolver bump, or the monthly registry label). Commercial to flat waits for its door the same way, which is harmless. The other direction breaks the ruling for that ad until the sweep. Read 2026-11-02 on 1 in 50 active sreality ads: 7 of 2,157 ever held two main categories, 1 of them across the flat-or-house line (a crude test; other portals not measured). Nothing is built for it, to keep the rule one rule; closing it would be one enqueue in the listing write.

## The registry read

`RegistryView.doors_near(lat, lon)` answers at most two kódy. `_DOORS_NEAR_SQL` has the same array shape as `CONTAINING_OBEC_SQL`, its `ST_Expand(… / 60000)` box letting `ruian_ap_geom_gist` drive. "Exactly one?" does not depend on which two, so nothing orders or measures them. `warm_points` asks three point-keyed questions: one extra round trip per slice.

## The scorecard (migration 717)

The operator retired the radius on 2026-11-02; W6 becomes deletion only. So the pin sample has no reader. 717 is `create or replace` of the one view, 698's text but for:
- the `sample` cohort is gone: the `smp` and `pin` CTEs, `pin_error_m`, `radius_cover`;
- determinism, W5's deferral: the containment probe orders by code behind an `OFFSET 0` fence, the same text as `CONTAINING_OBEC_SQL`. `EXPLAIN` rail: the fenced plan reaches `ruian_aug_pip_gist` before `ruian_admin_units`; a bare `ORDER BY` plan does not.

The gauge reads the view in one statement and loses `{p}/accuracy` with its two thresholds: 57 cells, five per portal. The door scorecard is what stood already: `{p}/street`, the `grain` facts, `{p}/ku`, `{p}/outside`. `listing_location.uncertainty_radius_m` and `grade.RADIUS_M` are untouched; deleting them is W6's.

## Rollout

Prerequisites: W5 merged and swept; W4's realitymix@8 re-mine done (otherwise hidden server geocodes on doors would refine).

1. Shadow from the W5b branch, once it has merged `main`. `ids_sql` (claims none, `wave-shadow.md`):
```sql
SELECT l.id FROM listings l JOIN listing_location ll ON ll.listing_id = l.id
 WHERE l.is_active AND l.category_main IN ('byt', 'dum') AND ll.country_status = 'cz'
   AND ll.granularity IN ('street', 'cast_obce_or_quarter', 'obec')
   AND coalesce(ll.disputed, '') NOT IN ('pin_outside_obec', 'pin_outside_cz', 'country_conflict')
   AND NOT EXISTS (SELECT 1 FROM location_claims w WHERE w.listing_id = l.id
                    AND w.claim_type IN ('address_point_id', 'house_number_cp', 'house_number_co'))
   AND EXISTS (SELECT 1 FROM location_claims c
                WHERE c.listing_id = l.id AND c.claim_type = 'coordinate' AND c.value_geom IS NOT NULL
                  AND EXISTS (SELECT 1 FROM ruian_address_points ap WHERE ap.valid_to IS NULL
                                AND ap.geom && ST_Expand(c.value_geom, 1 / 60000.0)
                                AND ST_DWithin(ap.geom::geography, c.value_geom::geography, 1)))
```
   `what_if` keeps real movers. Dispatch `location_wave_shadow.yml --ref feature/location-w5b-pin-door -f wave=w5b -f claims=none -f scope=apply -f group=1/1 -f ids_sql="…"`, then the printed `i/n` groups with `positions_run_id`. Watch: ruled `same` pairs where only one side gains a door or the two get neighbouring doors; ruled `different` pairs in one building newly joined through `_unit_number_arm`; `pin_pop` / `same_exact_pin`. PASS on every group.
2. Apply `717` immediately before the merge. Between apply and deploy the old gauge reads no sample row, so its accuracy cells read Not measured for minutes. New code on the old view is harmless: it ignores the sample rows.
3. Merge; W2's reconcile sweeps.

## Acceptance (W1 cells; read after `freshness/version` clears)

**The fire count.** Read on production 2026-11-02 by SQL, not by the resolver, on 1 in 10 active flats and houses at street, quarter or obec grain with an exact pin, no door claim and no dispute: 7,371 candidates, 4,230 with one door within 1 m, 242 of those refused by the words (the part and locality checks compare names, so approximately), **3,988 fire**. So **≈ 40k rows gain a door**: flats ≈ 21.9k, houses ≈ 18.0k; from street ≈ 24.5k, quarter ≈ 8.1k, obec ≈ 7.3k. The read's rows were not all at v5.10. The shadow's mover count is the real figure.

| Measure | After |
|---|---|
| Address-grain rows | +≈ 40k (the shadow's mover count decides) |
| `{p}/street` | up by the quarter and obec movers |
| `{p}/ku` | up (a door carries its KÚ) |
| `{p}/outside` | no higher |
| Pin-named doors on a non-flat, non-house ad, over 1 m from their pin, on another street than the bound one, or in another část than the claimed one | 0, but for an ad whose type moved since its last resolve |
| Town cells, `disputed` mix | unchanged |
| Shadow | PASS on every group |

## Ledger

Rule 25 asks a location PR that adds more than it deletes to say why. The operator approved the net addition (2026-11-02): Q8 reverses W2-a's "a pin never yields a street" and adds a rung, a registry question and an input. The PR body carries the measured numbers.
