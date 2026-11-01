# W5b: resolver v5.11. The pin names its door (as built, 2026-11-01)

**Status: built 2026-11-01 (#2338) on W5's branch; merges after W5.** Ruling Q8, "the map is the source when it is there". Measurements are production reads of 2026-10-31 17:45–18:30Z unless dated otherwise.

## The rule

`bind.pin_door` runs in `core` between `pin_inside_bound_obec` and `place`. It refines the row to a door when all of these hold:
- the words bound a town, a street or a část (`target_kind` street or admin_unit, a town the pin did not establish);
- the pin is exact (`declared.blurred` false, so area markers and W4's `estimated` are out; an undeclared pin counts, unlike `core`'s `pin_is_precise`);
- the pin lies inside the bound town;
- the words state nothing a door cannot be checked against (below).

The refinement: exactly one valid RÚIAN door within `PIN_DOOR_M` (5 m) of the pin, in the words' obec and street, in every část they claim, and with their PSČ. The row becomes that address point at rung `R5` and stands on the door (origin `pin_door`). Any refusal is silent: the words stand. The door's část is adopted where the words claimed none. A pin cannot corroborate the door it named (`grade._pin_corroborates` requires `registry_point`), so such a row never grades `exact`.

**Ruling Q8: it never contradicts the words. A lost refinement costs nothing; a contradiction breaks the ruling.** So the words refuse in these cases (`names_a_door` reads the stored claims, `Binding.words_unchecked` and `words_part_ids` are BIND's):

| The words state | Why it refuses |
|---|---|
| Any stored claim of a door type (`WORD_DOOR_CLAIM_TYPES`), parsed or not | A door the words named; `abc` too, or the scorecard would grade the pin against the door it named |
| A digit in the street line, parsed or not (`12a`, `12-14`, `12,`) | Design A1: a number R1 could not bind. `28. října` pays for this one rule |
| A street line that bound nothing, `ambiguous_streets` included | The door may be on another street. A line naming a část (`street_names_part`) works as that část |
| A locality line the town was only composed from (`Praha 4`, `Praha 4 - Podolí`, `Praha - Vokovice`) | Closes a MOMC: its borders cross části and nothing here checks it. Chosen over comparing `momc_unit_id` because it adds no column to the door read, no field and no level |
| A part claim that matched no část (a MOMC is none) | Nothing to check against |
| A část the door is not in, at every rung | R2 takes the register's část and R3 carries none, so BIND keeps what the words claimed |
| A PSČ the door does not carry | `door.psc` is on the read FILL makes anyway. A claim the normalizer rejected states no PSČ and is not read |

## Why 5 m

The q95 of `pin_error_m` measures how far pins stray, not whether a lone door is the right one. Pins are bimodal: 74.8 % of exact pins sit within 2 m of a door. The measure is the rule's precision against the door the words bound:

| Radius | Precision | Sibling truths only |
|---|---|---|
| 3 m | 98.2 % | |
| 5 m | 97.7 % | 96.5 % |
| 10 m | 95.8 % | 93.4 % |
| 25 m | 93.7 % | |

Two word-bound doors of one property agree 97.1 % of the time, so 5 m sits at the words' own self-agreement. It is the widest radius whose sibling-truth precision stays at or above 95 %, and going from 10 m to 5 m costs 2 % of fires. The rule is one for every portal (Q3, no bands).

## The registry read

`RegistryView.doors_near(lat, lon)` answers at most two kódy. `_DOORS_NEAR_SQL` has the same array shape as `CONTAINING_OBEC_SQL`, its `ST_Expand(… / 60000)` box letting `ruian_ap_geom_gist` drive. "Exactly one?" does not depend on which two, so nothing orders them or measures them. `warm_points` asks three point-keyed questions: one extra round trip per slice.

## The scorecard (migration 717)

Once pins name doors, address grain is no truth: a pin-named door's error is at most 5 m by construction and would poison W6's q95. The one view change for W5b and W6:
- **Truth** = `location_steps.pin_truth_sql()`: a door the words named (`bind.WORD_DOOR_CLAIM_TYPES`), the row's own else its live property's location ad's. `names_a_door` refuses a pin on any stored claim of those types, so a pin-named door cannot be a truth (contract test: every claim type is asked).
- **`pin_error_m`** is given the bind (truth in the row's obec), classed `lower(label)`, plus `+door` when exactly one valid door lies within 5 m of the pin; its error is then the door's.
- **`radius_cover`**: `given_bind` (rows with a truth not standing on their own word-bound door, truth in the bound obec) replaces `below_address`; `wrong_town` (truth in another obec, of rows with a truth) counts what it sets aside. The gauge keeps `{p}/accuracy` Watch-only until W6.
- **Determinism**, W5's deferral: `CONTAINING_OBEC_SQL` and the view's probe order by code behind an `OFFSET 0` fence. `EXPLAIN` rail: the fenced plan reaches `ruian_aug_pip_gist` before `ruian_admin_units`; a bare `ORDER BY` plan does not.

## As built (where the build differs from the design of 2026-10-31)

- **The refusals above** go beyond the design's "the words' část (if one is bound)" and its `Constraints.names_a_door`. Two read-only reviews reproduced each contradiction on the mini mirror; the ruling wins.
- **The fire count is lower.** Read on production 2026-11-01 by SQL, not by the resolver, on 1 in 10 active rows at street, quarter or obec grain with an exact pin, no door claim and no dispute:

  | Measure | 1 in 10 |
  |---|---|
  | Lone door within 5 m (the design's ≈ 66.7k) | 6,577 |
  | Door on another street than the bound one | 12 |
  | Street line that bound nothing | 88 |
  | Digit in the street line (45 of them a name such as `28. října`) | 57 |
  | PSČ stated / differs from the door | 119 / 0 |
  | Part claim stated / not the door's část by name (MOMC lines included) | 4,791 / 86 |
  | Locality line that is not the row's town by name (upper bound for composed lines; 35 hold a digit) | 212 |
  | Any of those but the another-street row | 420 |

  So about 6,150 of 6,577 survive: **≈ 61.5k rows move, not 66.7k**, about 6.5 % fewer. The MOMC rule costs at most 2,120 rows, of which about 350 hold a digit. Rejected door claims cost nothing here, the cohort already holds none. Per-grain splits after the refusals are not measured; the shadow's mover count is the real figure.
- **No ordering in `doors_near`.** The rule does not read which door, so `ORDER BY` went with the metres, rather than ordering by `kod_adm`.
- **`pin_error_m` n is not "unchanged".** Dropping wrong-town rows lowers it: read on production 2026-11-01, 10,593 → 10,584 pins and `below_address` 5,072 = `given_bind` 5,059 + `wrong_town` 13. Cover on that reading: 4,917 of 5,059 (97.2 %). Compare the sum of a portal's classes plus its wrong-town rows, not the old n alone.
- **The rail's home.** The EXPLAIN and `+door` live tests live in `tests/test_checks_quality_sql_live.py`, already in the schema lane, so `migrations.yml` is unchanged.
- **Replay fixture.** `health_quality_location_view_now.csv`'s radius-cover rows are relabelled `given_bind`.

## Rollout

Prerequisites: W5 merged and swept; W4's realitymix@8 re-mine done (otherwise about 2.1k hidden server geocodes on doors would refine).

1. Shadow from the W5b branch, once it has merged `main`. `ids_sql` (claims none, `wave-shadow.md`):
```sql
SELECT l.id FROM listings l JOIN listing_location ll ON ll.listing_id = l.id
 WHERE l.is_active AND ll.country_status = 'cz'
   AND ll.granularity IN ('street', 'cast_obce_or_quarter', 'obec')
   AND coalesce(ll.disputed, '') NOT IN ('pin_outside_obec', 'pin_outside_cz', 'country_conflict')
   AND NOT EXISTS (SELECT 1 FROM location_claims w WHERE w.listing_id = l.id
                    AND w.claim_type IN ('address_point_id', 'house_number_cp', 'house_number_co'))
   AND EXISTS (SELECT 1 FROM location_claims c
                WHERE c.listing_id = l.id AND c.claim_type = 'coordinate' AND c.value_geom IS NOT NULL
                  AND EXISTS (SELECT 1 FROM ruian_address_points ap WHERE ap.valid_to IS NULL
                                AND ap.geom && ST_Expand(c.value_geom, 5 / 60000.0)
                                AND ST_DWithin(ap.geom::geography, c.value_geom::geography, 5)))
```
   About 70k ids; `what_if` keeps real movers. Dispatch `location_wave_shadow.yml --ref feature/location-w5b-pin-door -f wave=w5b -f claims=none -f scope=apply -f group=1/1 -f ids_sql="…"`, then the printed `i/n` groups with `positions_run_id`. Watch: ruled `same` pairs where only one side gains a door or the two get neighbouring doors (`same_ruian_adm_kod` turns into a present 0, about 3.5 % at 5 m); ruled `different` pairs in one building newly joined through `_unit_number_arm`; `pin_pop` / `same_exact_pin` for pins under 5 m apart that now share a point. PASS on every group.
2. Apply `717` immediately before the merge. Between apply and deploy the old gauge reads no `below_address` row, so the Watch-only accuracy cells read Not measured for minutes.
3. Merge; W2's reconcile sweeps.

## Acceptance (W1 cells; read after `freshness/version` clears)

| Measure | Before (pre-W5, 1/10 ×10) | After |
|---|---|---|
| Address-grain rows | ≈ 63.4k | +≈ 61.5k (the shadow's mover count decides); per grain not measured after the refusals |
| `{p}/street` | per W1 | up by the quarter and obec movers |
| `{p}/ku` | per W1 | up (a door carries its KÚ) |
| `{p}/outside` | after W5 | no higher |
| `pin_error_m` | see above | a `+door` class with q95 ≈ 0 m |
| `radius_cover/given_bind` | 97.2 % (2026-11-01) | ≥ 95 % |
| Pin-named doors over 5 m from their pin, on another street than the bound one, or in another část than the claimed one | — | 0 |
| Sibling-truth precision of R5 rows | 96.5 % | ≥ 95 % |
| Town cells, `disputed` mix | — | unchanged |

## Ledger

Rule 25 asks a location PR that adds more than it deletes to say why. **W5b does not pay for itself:** Q8 is a ruled reversal of W2-a's "a pin never yields a street" and adds a rung, a registry question and a re-rendered view. W6 pays for the pair (its design: −1,600 / +400). The PR body carries the measured numbers.
