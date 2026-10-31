# W3b: the address picker — design and as-built record (#2247)

**Status: BUILT 2026-10-31** on top of W3 PR-1 (#2242). The design below is the one that was built;
this block records where the build differs and what it measured.

## As built
- **Files.** `api/maps.py` (`_resolve_address_point`, `_DOOR_LABEL_SQL`, `_entry`; `_resolve_street` returns the `Street`),
  `api/schemas.py` (`address_point`), `api/routes/location_corrections.py` (renamed; the POST only),
  `location_data/operator_corrections.py` (`submit_pick`, `clear`, `PICK_TYPES`, `PICK_COLUMN`, slimmed `read_projection`),
  `toolkit/property_identity.py` (`detach_listings` → `operator_corrections.clear`, R1 b),
  `frontend/src/components/listing-detail/AddressPicker.tsx` (lazy chunk, 3.9 kB), the `addressAction` slot,
  `usePlaceSuggest` + exported `SuggestPanel`, `resolveSuggestion(pick, {addressPoint})`, `submitLocationPick`.
- **Differences from the design.**
  - The door's orientation letter must equal the pick's when the pick names a číslo orientační (no letter
    matches only a door without one). A pick with no `regional.address` number entry binds nothing.
  - The batch row is written after the claims, in the same transaction, so `row_count` is the claims changed;
    `EXTRACTOR_VERSION` is `"2"`. `clear` writes a batch row only when it retracted something.
  - The register reads take `ORDER BY id DESC LIMIT 1` (a code can have two current rows).
  - The save button reads "Uložit pro všechny inzeráty" without "(N)" while the advert list is unread.
  - The live test (`tests/location_data/test_operator_pick_live.py`) seeds its own two-town mirror in the
    rolled-back transaction (the CI database holds no mirror; `tests/location_data/mini_mirror.py` is in
    memory) and runs the real drain: pick → address point on both ads, street re-pick, clear → the
    pre-pick `claim_set_hash`; plus the detach clear. The admin-only pencil is pinned in `PropertyDetail.test.tsx`.
- **Measured.** Product code −636/+555 (net −81; the design estimated −195: the door arm and the picker came
  out larger than estimated), tests −274/+704. First-visit JS 328,008 → 328,240 B gzip (budget 330,000).
- **Owed.** About 20 real Mapy address picks on production before the merge, to confirm the 300 m door slack
  picks one door (needs production access). The operator may override R1 (taken as b).

## Summary (under 400 words)

**W3b turns the address-correction pencil into an admin-only picker. It deletes more than it adds and needs no migration, no new table and no new claim type.**

- **The pencil:** it sits on the property page's address line and only admins see it (`ListingOverview.tsx:145-147`, passed in from `PropertyDetail.tsx:348`). It opens a popover.
- **The search:** the popover reuses the Mapy typeahead that Browse's place chips use (`LocationTypeahead.tsx:118-124`, `/maps/suggest`). Picks are bound to the register by the existing `/maps/resolve` (`api/maps.py:420-443`).
  - Today `/maps/resolve` binds an address only to its street (`maps.py:9-31`).
  - W3b adds an **address-point arm** behind a request flag. It calls the resolver's own `address_points_by_number` (`resolve_db.py:263-273, 737-749`, indexed, measured at 424 buffers) on the bound street, or on the obec for a village with no streets. It accepts the door only if exactly one lies within `REGISTRY_PIN_CONFLICT_M` (300 m, `bind.py:100`) of the pick.
  - Nothing searches the 3.02 M mirror points. No new view is needed.
- **The write:** the admin confirms. `POST /location/corrections {property_id, pick:{level: adresni_misto|ulice|obec, code}|null}` (path kept, body reshaped) calls `operator_corrections.submit_pick`.
  - For every ad of the property it writes the four picker types through W3's `write_claims`: `address_point_id`, `street_name`, `obec_name`, `okres_name`.
  - The picked level's types carry the register's own names. The other types carry W3 **tombstones**, which delete only the operator's own key. A re-pick therefore replaces the operator's whole statement.
  - `pick:null` is the explicit "back to the ads' own words" retraction.
- **Read-your-writes:** the route re-resolves the location ad immediately (`resolve_now`, kept) and returns a slimmed `read_projection` with the new label and `held` (whether the row binds the picked code). The page reloads `properties_public`, a live view joined on the location ad, so the label updates at once. The Browse list follows at its `*/15` rebuild.
- **Deleted:** the inspector, `CorrectionForm`, `toolkit/location_quality.py` (the whole file), two GET routes, the source-pick route tests and two tests that only read the text of migration 400 (the tables they check were dropped in W2-a).
- **Size:** production code about −200 lines net, everything including tests about −40.

**Measured on production:**
- 1 operator claim exists (`address_point_id`, listing 156144, 2026-08-13; it agrees with the portal).
- The mirror holds 3,022,397 current address points (1,444,445 without a street) and 83,476 streets. No obec has two streets with the same name.
- 1,503 of 6,258 obce share a name with another obec; only 2 such pairs sit inside one okres.
- 9,253 listings carry a portal `address_point_id`.
- The 99th percentile is 7 ads per property; the maximum is 138.

**Grain:** the request is per property, but each claim is per ad. Ruling Q5 says "every current ad", which overrides the brief's "location ad only". Rule 18 does not apply, because these are not account curation rows.

**One ruling (R1)**, taken as the default (b): a detached ad leaves the operator's pick behind.

---

# The design (2026-10-31)

Base: main after W3 PR-1 (#2242) merges. Line numbers for the Location Quality files are taken from `origin/feature/location-w1-page-reads-cells` @43af3f00f, which is #2243 (merged 16:04Z). My local main predates that merge.

## 0. Measured on production (read-only, bounded)
| What | Value |
|---|---|
| Operator claims (`extraction_method='operator_manual'`) | **1**: `address_point_id`, listing 156144 (property 138994), 2026-08-13. Both ads of that property sit on kód ADM 24252301, the same value. One `location_claim_batches` row with lane `operator_correction`. Nothing reads that lane. |
| `ruian_address_points` | 3,022,952 rows, **3,022,397 current**. 1,444,445 current points have no street (village doors); 539,297 have a číslo orientační; 908 have no geometry. |
| `ruian_streets` current | 83,476; **0** (obec, name) groups with more than one street code |
| Obce current | 6,258; 591 names shared by 1,503 obce; **2** name pairs inside one okres. The parent level is okres, or kraj for Praha until W7. |
| Portal `address_point_id` claims | 9,256 claims on 9,253 listings (bezrealitky `ruianId`, `contracts/portals/bezrealitky.yaml:152`) |
| Ads per active property | p99 **7**, max **138**, 2,299 properties have more than 10; 305,555 active properties |
| `location_claim_type` | 40 enum labels; the contract vocabulary is TWELVE (`location_data/contracts.py:107-130`) |
| `location_field_policy` | dropped (the W2-a note in `drain.py`) |

## 1. UX
- **Where the pencil goes:**
  - `PropertyIdentity` gains an optional `addressAction?: ReactNode` slot, rendered inline after `{listing.display_label}` (`ListingOverview.tsx:145-147`). The Estimation Detail page shares this component and passes nothing.
  - `PropertyDetail.tsx:348` passes `addressAction={isAdmin ? <AddressPicker …/> : undefined}`. `useAuth` is already imported (line 46) and used (line 608).
  - The picker is wrapped in `lazyChunk`, so non-admins and the bundle budget pay nothing.
  - It is desktop only: `PhoneProperty` is unchanged (the phone frame is for reading and curation).
- **Pencil button:**
  - Reuses `PencilIcon` (`components/icons.tsx:73`) at h-3.5 w-3.5, styled like `ArtifactLinkRow`'s `iconBtn` (`ArtifactLinkRow.tsx:101-102`, at a smaller h-6 w-6).
  - `aria-label="Opravit adresu (RÚIAN)"`. It sits outside `<ActingAs>`.
- **Popover:** an `AnchoredPopover` (`components/AnchoredPopover.tsx`), about 22 rem wide, containing:
  1. The current label, in ink-3.
  2. The typeahead input labelled "Adresa, ulice nebo obec". It is the same suggest hook and `SuggestPanel` as Browse (§ 5).
  3. On a pick, `resolveSuggestion(pick, {addressPoint: true})` returns a preview line: the badge (Adresní místo / Ulice / Obec) and the register's name.
     - A pick at another level (část obce, okres, kraj, stát) shows "Vyberte adresu, ulici nebo obec" and no save button.
     - A pick with `id: null` shows "V RÚIAN nelze jednoznačně najít" and no save button.
  4. A primary button "Uložit pro všechny inzeráty (N)", where N is `sources.length` and the property page already holds it. The label says outright that every ad is written.
  5. A quiet button "Vrátit údaje z inzerátů" (`pick: null`).
- **After saving:**
  - On success the picker invalidates `propertyKeys.row(id)`, `['property-location', id]` and `['property-static-map', id]`. The label, map and city tiles re-read `properties_public`, which is a live view (relkind `v`) joining `listing_location` on `location_listing_ref_id` (migration 636, header lines 22-25). The popover closes.
  - If `held === false` it stays open with: "Uloženo, ale poloha je stále „{location.label}“ — údaje portálu (PSČ, kód adresy z portálu) převážily; zvolte konkrétní adresu."
  - If `resolved === false`: "Uloženo; poloha se přepočítá během několika minut."
- **Undo** is the same picker: pick again, or clear. There is no delete button for one claim (§ 3.4).

## 2. API
**One route, kept and reshaped:** `POST /location/corrections` on the existing `/location` router, admin-gated at the router (`api/routes/location_quality.py:33-35` → `deps.require_admin`, `api/dependencies.py:319-325`). The handler also takes `claims = Depends(deps.require_admin)` for the audit `sub`. Rename the module to `api/routes/location_corrections.py`, since the quality routes went in W1 and the inspector goes here. The import at `api/main.py:79` and the comment at `:346-351` change with it.

```python
PickLevel = Literal["adresni_misto", "ulice", "obec"]
class PickIn(BaseModel):
    level: PickLevel
    code: int = Field(ge=1)            # kód ADM / street code / obec code (RÚIAN)
class CorrectionIn(BaseModel):
    property_id: int = Field(ge=1)
    pick: PickIn | None                # None = back to the ads' own words
```
**Response** (`{data}` envelope):
```json
{"property_id": 138994, "pick": {"level":"adresni_misto","code":24252301},
 "ads": [{"listing_id":156144,"source":"bezrealitky","location_ad":true}, …],
 "written": 2, "enqueued": 2, "resolved": true,
 "location": {"label":"…","granularity":"address_point","ruian_adm_kod":24252301,"ulice_kod":…,"obec_kod":…},
 "held": true}
```
- **Errors:**
  - 403: not an admin.
  - 404 (`UnknownPropertyError`): the property is unknown, not `status='active'`, or has no ads.
  - 422 (`CorrectionError`): the code is not a current RÚIAN unit at that level.
- **The handler:** `submit_pick` → `resolve_now(conn, location_ad)` → `read_projection(conn, location_ad)` → `held = location[{"adresni_misto":"ruian_adm_kod","ulice":"ulice_kod","obec":"obec_kod"}[level]] == code`.
- **Audit:**
  - The claim row itself records `surface='operator_input'`, `extraction_method='operator_manual'`, `licence_class='operator'`, `claim_confidence='exact'` and `first_observed_at` (W3 `operator_corrections.py:129-135`).
  - Each pick writes one `location_claim_batches` row: `lane='operator_correction'`, `source` NULL (a property's ads span portals; the column is nullable and nothing reads the lane), `row_count` = claims changed, and `note = "property P: <level> <code> by <sub>"` or `"property P: cleared by <sub>"`. The free-text note field goes, because the picker has none.
- **`delegated_edits` does not apply.** It stamps writes made through an edit share acting as the owner (`X-Act-As`) on the eleven account-scoped curation tables (rule 18). A location claim is global register evidence, with no `account_id` and admin only. The SPA's `apiPost` never sends `X-Act-As`; only the curation and pipeline wrappers do (`frontend/src/lib/api.ts:113`).

## 3. Write semantics (W3's one write, unchanged)
### 3.1 Grain
- **Claims are per ad, written for every current ad.** The key is (listing_id, claim_type, licence_class) (W3 design § 2.1). The request is per property and is fanned out once, at write time.
- **The ruling overrides the brief.** Q5 (PROGRAM.md:15, :34) says "every current ad of the property", not the location ad alone.
- **Why every ad:** the location ad is re-elected at each property recompute. It is rank 1 of "map point, then foreign, then finest rung, then canonical order" (`scripts/recompute_property_stats.py:12-14, :249`). A `listing_location` write does not mark its property dirty (`docs/architecture.md:3603-3607`). So a pick written only on today's location ad is lost the moment another ad out-ranks it. With the pick on every ad, the property's place no longer depends on which ad is elected.
- "Current ad" means every listing with `listings.property_id = P` now, active or not, because the election ranks over all of a property's ads.
- **Rule 18 does not apply.** These are not curation rows: no `property_id` column, so they sit outside the `PROPERTY_CARRIERS` census (`tests/test_property_carriers_live.py` checks FKs and `%property_id%` columns), and no account.
  - On a merge, the claims stay on their ads; later-attached ads resolve from their own words (Q5).
  - On a detach, see R1.

### 3.2 Which claim types (4 of the twelve; no new type)
`PICK_TYPES = ("address_point_id", "street_name", "obec_name", "okres_name")`. This is a subset of `contracts.CLAIM_TYPES` (`contracts.py:125-130`) and replaces the 8-type `ALLOWED_CLAIM_TYPES` (W3 `operator_corrections.py:38-47`). Each pick writes all four on every ad:

| Pick | Values (register names, never the client's text) | Tombstones |
|---|---|---|
| `adresni_misto` | `address_point_id` = kód ADM | street, obec, okres |
| `ulice` | `street_name` = `ruian_streets.name`, `obec_name`, `okres_name` (the obec's parent; a tombstone if the parent is a kraj) | address_point_id |
| `obec` | `obec_name`, `okres_name` | address_point_id, street_name |
| `null` | — | all four |

How each pick binds:
- **Address point:** the operator's claim is read first (W3 `resolve_db.py:96-109`, `ORDER BY … (licence_class='operator') DESC`). `collect_constraints` takes `kod_adm` (W3 `bind.py:220-222`). R0 binds `registry.address_point(kod)` (W3 `bind.py:550-557`), FILL fills the whole chain, and `place` makes the register point the position (W3 `bind.py:1111`). No `street_name` + číslo claims are needed.
- **Street:** it binds by name inside the operator's obec. There are 0 same-name streets within one obec, so the exact tier is unique.
- **Obec:** `okres_name` narrows same-name towns. Only 2 same-name pairs exist inside one okres.

Because the tombstones go out with every pick, a coarser re-pick cannot be shadowed by the operator's own stale `address_point_id`, which R0 would otherwise keep binding.

### 3.3 `submit_pick` sketch
It goes in `location_data/operator_corrections.py`, which stays one of the two writers (rule 25).
```python
def submit_pick(conn, *, property_id: int, pick: tuple[str, int] | None, by: str | None) -> dict:
    ads = _property_ads(conn, property_id)        # SELECT l.id, l.source, l.source_id_native,
                                                  #   l.id = p.location_listing_ref_id FROM properties p
                                                  #   JOIN listings l ON l.property_id = p.id
                                                  #   WHERE p.id=%s AND p.status='active' ORDER BY l.id
    values = {} if pick is None else _register_values(conn, *pick)   # 3 small SQLs; CorrectionError if not current
    now = datetime.now(UTC)
    claims = [Claim(listing_id=a.id, source=a.source, source_id_native=a.native, claim_type=t,
                    surface="operator_input", page_kind="none", extraction_method="operator_manual",
                    extractor_id=EXTRACTOR_ID, extractor_version=EXTRACTOR_VERSION, contract_entry_id=None,
                    first_observed_at=now, blur_evidence="none", licence_class="operator",
                    value_text=values.get(t), subject_scoped=True, claim_confidence="exact",
                    retract=t not in values)
              for a in ads for t in PICK_TYPES]
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(_BATCH_SQL, (written_note(property_id, pick, by),))
            written, enqueued = write_claims(cur, claims, reason="operator_edit")
    return {...}
```
- **What `_register_values` reads:**
  - `adresni_misto`: `ruian_address_points WHERE kod_adm=%s AND valid_to IS NULL`. This is today's `_KOD_ADM_SQL`, W3 `:62-75`, slimmed to an existence check.
  - `ulice`: `ruian_streets s JOIN ruian_admin_units o ON o.id=s.obec_unit_id LEFT JOIN ruian_admin_units k ON k.id=o.parent_id AND k.level='okres' WHERE s.code=%s AND s.valid_to IS NULL`.
  - `obec`: the same shape by `o.level='obec' AND o.code=%s AND o.valid_to IS NULL`.
- **Write cost:** a property's worth of ads × 4 is at most 552 rows. Unchanged values and absent keys write nothing and enqueue nothing (W3 `claims_store.py:96-110, 120-121`). Writes are enqueued with `reason='operator_edit'`, which the worker resolves ahead of `full_sweep` rows (`migrations/697`, drain `_CLAIM_SLICE_SQL`).
- **Kept from W3:** `resolve_now`. `read_projection` is slimmed to `granularity, ruian_adm_kod, ulice_kod, obec_kod, location_display_label(street_name, house_number_cp, house_number_co, obec_name, cast_obce_name, country_code, country_status) AS label, resolved_at`. That is the one label, rule 25.
- **Deleted from W3's module:** `submit_correction`, `ALLOWED_CLAIM_TYPES`, the registry echo and the 14-column `_PROJECTION_SQL`.

### 3.4 Undo and retraction
- A new operator pick replaces the old one; that is the upsert.
- "Vrátit údaje z inzerátů" writes four tombstones per ad.
  - This is W3's existing explicit absence arm, `Claim.retract` → the `gone` CTE (W3 `claims_store.py:49-52, 104-110`). It is keyed on `licence_class`, so it deletes only the operator's claim. W3's live test pins the symmetric case: a portal tombstone never reaches the operator (`test_claims_one_write_live.py:160-175`).
  - It is no new supersession path: still 2, the upsert and `contracts.retract`.
- `contracts.retract` never reaches operator claims, because they carry no `contract_entry_id` (W3 `operator_corrections.py:9-10`).
- No per-claim delete button is offered.

## 4. Lookup: one path, extended
- **Search:** Mapy suggest, unchanged (`api/main.py:369-376`, `require_token`).
- **Binding:** `/maps/resolve` (`api/main.py:391-404`).
  - `ResolveLocationIn` (`api/schemas.py:297-303`) gains `address_point: bool = False`. Browse never sets it, so chips are byte-identical.
  - With the flag set and `type == "regional.address"`, `resolve()` (`maps.py:420-443`) answers `{level:"adresni_misto", id: kod|None, name: label|None, context: None}` through a new `_resolve_address_point` (about 35 lines):
    1. Run the PIP chain → obec, using the existing `_containing_chain` (`maps.py:202-213`).
    2. Take the number from the `regional.address` entry of `regionalStructure` (e.g. `"610/7"`; `tests/api/test_maps.py:774-777`) and read it with `normalize_house_number` and `house_number` (`normalize.py:137-165`).
    3. If a `regional.street` entry exists, bind it exactly as the chip does: `_resolve_street`, refactored to return the `Street` (`maps.py:328-355`). If it does not bind, return `id: None`; never widen to the whole obec.
    4. Call `registry.address_points_by_number(obec_kod=…, street_name_norm=street.name_norm|None, cislo_domovni=cp, cislo_orientacni=co, typ_so=typ)` (`resolve_db.py:737-749`). Keep only doors with a matching `znak_orientacniho` (if given) that lie within `REGISTRY_PIN_CONFLICT_M` of the pick. Exactly one → its kód ADM; otherwise `None`, with one LOG line.
    5. Build the label with one `SELECT location_display_label(…)` over the door's register fields.
- **Street and obec picks** keep today's arms (`_TYPE_LEVEL`, `maps.py:66-73`).
- **Sizing:** no search runs over the 3.02 M points. Each pick is one keyed lookup on `ruian_ap_street_hn`/`ruian_ap_obec_hn` (the comment at `resolve_db.py:247-256` measures 424 buffers). The API reads the mirror as service role, so no owner-rights view and no new table are needed.
- **Rejected:** our own trigram search over the mirror. It would be a second lookup path plus an index over 3 M rows.

## 5. Frontend reuse; no new tokens
- **Typeahead:** extract `usePlaceSuggest()` from `LocationTypeahead.tsx:97-128`: query, debounce, `suggestQ` and `notConfigured`. Export `SuggestPanel` (`:266-331`) from the same file. Both Browse and the picker use them, and Browse's tests stay green.
- **`lib/maps.ts`:**
  - `resolveSuggestion(pick, {addressPoint})` (`:69-81`) sends the flag.
  - `LocationResolution.level` doc (`:31-40`) gains `adresni_misto`.
- **`lib/locationQuality.ts`** (the `/location/*` wrappers) keeps `LOCATION_SOURCES` and gains `submitLocationPick({property_id, pick})` plus its result type.
- **Components:** `AnchoredPopover`, `PencilIcon`, `ErrorBanner`, `Field`.
- **StatusDot is not used.** It is the Health status mark (`StatusDot.tsx:3-7`, words OK/Watch/Problem); the picker states its outcome in words.
- **Tokens:** existing ones only: `--color-ink-2/3/4`, `--color-copper`, `--color-copper-soft`, `--color-on-copper`, `--color-copper-2`, `--color-brick`, `--color-paper-3`, `--color-rule`, `--radius-xs/sm/md`. Nothing needs the operator's design-token OK.

## 6. Deletions
- **`toolkit/location_quality.py`: the whole file.** After W1 PR 2 it holds only `_INSPECTOR_*_SQL` and `listing_inspector` (96 lines).
- **API routes:**
  - `GET /location/listing/{id}` and `GET /location/listing/by-native/{source}/{native}`.
  - `_KNOWN_SOURCES`/`_check_source` and the `toolkit` and `SOURCES` imports (W1 version `:39-66`).
  - The docstring paragraphs about the inspector.
- **`frontend/src/pages/LocationQuality.tsx`:**
  - `CORRECTABLE`, `InspectorSection`, `InspectorBody` and `CorrectionForm` (W1 version `:167-351`) and `<InspectorSection>` (`:408`).
  - The header sentence about the inspector.
  - Now-unused imports: `useMutation`, `PanelGroup`, `Stat`, `fmtRelative`, `fetchInspector*`, `submitCorrection`, the types. `Field` stays for the Source select.
- **`frontend/src/lib/locationQuality.ts`:** `InspectorClaim`, `Inspector`, the old `CorrectionResult`, `fetchInspector`, `fetchInspectorByNative`, the old `submitCorrection`.
- **Tests:**
  - `LocationQuality.test.tsx`: the inspector fixture and mocks, "names the inspector query box…" and "heads the correction form as an L3 group…".
  - `tests/api/test_location_quality_routes.py`:
    - The inspector 404 test.
    - The unknown-source and per-source tests; the by-native inspector route was the only one checking source names.
    - The three old correction tests, rewritten in § 7.
  - `tests/location_data/test_operator_corrections.py`: the two migration-400 policy-row tests (they read the text of a migration whose tables W2-a dropped), and the 8-type `test_allowed_types_are_typed_column_claims`, replaced.
- **Docs:**
  - The `api/main.py:346-351` comment.
  - The PROGRAM W3b "as built" note.
  - architecture § Location data: one paragraph on the picker in place of the inspector mentions.
  - roadmap/location-data.md.
  - The toolkit-api skill line 97 still holds as written.

## 7. Tests
**pytest:**
1. `tests/api/test_maps.py`, address arm:
   - With `address_point=True`, a street address binds its one door. Fixture: the Týnská ulička 610/7 pick (`:774-777`) plus an `address_points_by_number` row → `{level:"adresni_misto", id}`.
   - A village address with no street entry binds by obec + čp inside the slack.
   - Two doors inside the slack, or none, → `id: None`.
   - A street entry that does not bind → `None`, and the number lookup never runs.
   - Without the flag, an address pick still answers its street (the existing tests stand).
2. `tests/api/test_location_corrections_routes.py` (renamed):
   - `POST /location/corrections` returns 403 to a plain user, and both `/location/listing/*` paths return 404 to an admin (added to the "deleted route families" test).
   - 404 maps `UnknownPropertyError`; 422 maps `CorrectionError`; a body with `level:"cast_obce"` gets 422 from Pydantic.
   - On success the handler calls `resolve_now` on the location ad only and computes `held` from `read_projection`; `pick:null` → `held: None`.
   - `by` is the JWT's `sub`.
3. `tests/location_data/test_operator_corrections.py`, offline with the fake `_Conn`:
   - One pick builds `len(ads) × 4` claims: the picked level's values, `retract=True` for the rest, all with licence `operator`, method `operator_manual` and `reason='operator_edit'`, plus one batch row.
   - `pick=None` gives four tombstones per ad.
   - `PICK_TYPES ⊆ contracts.CLAIM_TYPES` and `"coordinate" ∉ PICK_TYPES` (no new type).
   - The module still never imports the intake lane (kept).
4. Live, using the same CI mini-mirror and rollback setup as `test_claims_one_write_live.py`. Two ads of one property, whose portal claims state street S in town A:
   - Pick an address point in town B → both ads hold exactly one operator `address_point_id`; `drain.run(only_listing_id=)` gives `ruian_adm_kod = picked` on both.
   - Re-pick the street → the operator `address_point_id` is gone and the row is at street grain.
   - Clear → no operator claims, and `claim_set_hash` equals the pre-pick hash.

**vitest:**
1. `components/listing-detail/AddressPicker.test.tsx`:
   - The pencil renders only when `isAdmin`; `PropertyIdentity` without the slot renders no button.
   - Open → type → mocked `fetchSuggest` → pick → `resolveSuggestion` called with `{addressPoint:true}` → preview → save calls `submitLocationPick({property_id, pick:{level, code}})` and invalidates `propertyKeys.row`.
   - A refused level shows no save button; `held:false` shows the notice; the clear action sends `pick:null`.
   - Accessible names, and `expectNoNestedInteractive`.
2. `LocationTypeahead.test.tsx` passes unchanged after the hook extraction.
3. `LocationQuality.test.tsx` gains one assertion: no inspector box, and no `/location/listing` call.

## 8. Acceptance (the gate ticket's checks)
1. **A corrected ad binds the picked point.** On production, pick an address on a property with at least 2 ads whose location ad is coarser than address grain.
   - The response says `held:true` and `resolved:true`.
   - `listing_location.ruian_adm_kod = picked` holds on the location ad at once and on every ad after one drain tick.
   - `location_claims` holds exactly one operator `address_point_id` per ad and no other operator type.
   - The page label equals `location_display_label` of the point without a reload.
2. **The W1 cells move only for the corrected rows.**
   - Between two reads of `location_quality_by_source` (migration 698), the corrected rows change their source's cells by at most the property's ad count.
   - `dirty_locations` rows with reason `operator_edit` are exactly those ads.
   - A portal pin outside the picked town may correctly make a corrected row `pin_outside_obec` (`check.py:268-279`). That is the honest evidence, not drift.
3. **Undo:** a street re-pick, then a clear, returns the ads to their pre-pick `claim_set_hash`.
4. **Gone:** `/location/listing/*` returns 404; `toolkit/location_quality.py` does not exist; the LQ page has no inspector.
5. **Gated:** non-admins see no pencil, and the POST returns 403.
6. **Browse:** the Browse card shows the new label after the next `*/15` `browse_list` rebuild.

## 9. Deletion ledger versus additions (estimates)
| | Deleted | Added |
|---|---|---|
| `toolkit/location_quality.py` | 96 | 0 |
| API route module | ~64 | ~15 |
| `operator_corrections.py` (submit, 8 types, echo, projection) | ~95 | ~80 (`submit_pick`, three register reads, ads read) |
| `LocationQuality.tsx` | ~192 | 0 |
| `lib/locationQuality.ts` | ~50 | ~22 |
| `api/maps.py` + schema + `lib/maps.ts` | 0 | ~47 |
| `AddressPicker.tsx` + slot + page wiring + hook export | 0 | ~140 |
| **Production code** | **~500** | **~305 (net about −195)** |
| Tests | ~160 | ~315 |
| **Total** | **~660** | **~620 (net negative)** |

No migration. No new table, view, index, claim type or token.

## 10. Rulings and named limits
- **R1 (ruling: the default may be unsafe). What does a detached ad keep?**
  - `detach_listings` runs on an operator split, and also on autodedup `mode=unapply` without the operator. A detached ad keeps the operator's pick, because the claims are per ad, and so carries property P's address into its new property. That ad was detached precisely because it was a different place.
  - **(a) Keep:** zero code. The operator re-picks once they notice.
  - **(b) Clear:** `detach_listings` calls `operator_corrections.clear(listing_ids)`, about 6 lines in the rule-15 chokepoint. The writer is still `operator_corrections`; the detached ads return to their own words.
  - Volume is tiny (1 correction in 2.5 months). I recommend (b). **Built: (b).**
- **Limit L1 (no ruling; the outcome is visible): portal evidence can defeat a street or obec pick.**
  - A portal PSČ is applied before the okres narrowing (W3 `bind.py:326` before `:355`). A portal PSČ that fits a same-name town, or a town whose část has that name, can win.
  - A portal kód ADM (9,253 listings) binds at R0 whatever town the operator names.
  - The route reports `held:false` and the UI asks for an address pick. Address picks always hold.
- **L2:** the portal's pin stays as evidence, so a corrected row can read `pin_outside_obec` or `pin_off_street`. Silencing it would need an operator `coordinate` claim. That type is deliberately not offered (W3 `operator_corrections.py:34-37`) and would need a ruling.
- **L3:** a picked area of a town (část obce), okres or kraj is refused. W5 changes what a část claim binds to (Q1), so W3b does not commit to it.
- **Clarification, no ruling needed:** Q5 says "every current ad", not just the location ad. The ruling wins.

## 11. Sequencing
- **Branch** from main after #2242 (W3 PR-1) merges. W3b needs `write_claims`, the tombstone and the operator-first read. It does not depend on W3 PR-2 (migration 700).
- **Order:** it lands before W5 (Q14).
- **No standing autodedup shadow gate:** that gate covers waves that move many positions, and W3b moves only rows the operator picks.
- **Ticket:** #2247. Before merging, test-drive about 20 real Mapy address picks to confirm the 300 m door slack picks one door.

### Critical Files for Implementation
- /home/hejtm/dev/sreality/location_data/operator_corrections.py (W3 version on `origin/feature/location-w3-one-claim-per-fact`; `submit_pick`, `PICK_TYPES`, slimmed `read_projection`)
- /home/hejtm/dev/sreality/api/maps.py (the address-point arm of `resolve`; `_resolve_street` returns the `Street`)
- /home/hejtm/dev/sreality/api/routes/location_quality.py (→ `location_corrections.py`: the inspector GETs go, the POST is reshaped)
- /home/hejtm/dev/sreality/frontend/src/components/listing-detail/ListingOverview.tsx (the `addressAction` slot at :145-147), together with /home/hejtm/dev/sreality/frontend/src/pages/PropertyDetail.tsx (:348) and the new `AddressPicker.tsx`
- /home/hejtm/dev/sreality/frontend/src/components/filter-controls/LocationTypeahead.tsx (extract `usePlaceSuggest` and export `SuggestPanel`)
