# W3: one current claim per fact (design, 2026-10-31)

Wave W3 of the location data quality programme (`PROGRAM.md`; rulings Q4, Q5, Q13; order Q14:
W0 → W1 → W2 → W7 → W8 → W4 → **W3** → W3b → W5 → W5b → W6). Read-only preparation: no repo edit,
no pytest. Code read at `main` bddea095d. Every production number was read on 2026-10-31 between
13:10Z and 14:30Z through the Supabase MCP, SELECT only. `location_claims` was never scanned
unfiltered except once for a licence-class count (one aggregate row). Where a number is a sample
estimate, the sample and the scale factor are given.

Samples used (all deterministic, by listing id):

| Name | Predicate | Listings | Scale to corpus |
|---|---|---|---|
| S1 | `listings.id % 100 = 7` | 10,837 (3,947 active, 6,890 inactive) | ×99.93 (1,082,934 listings) |
| S5a | `id % 20 = 3 AND is_active` | 19,862 active | ×20.0 (397,187 active) |
| S2i | `id % 50 IN (3, 29) AND NOT is_active` (two draws) | 27,475 inactive | ×24.96 (685,747 inactive) |
| S25sr | `source = 'sreality' AND is_active AND id % 4 = 1` | all active sreality in the quarter | ×4 |

---

## 1. Current state, measured

### 1.1 The store

| Relation | Size | Notes |
|---|---|---|
| `location_claims` heap | 3,600 MB | 14,820,663 live tuples, 39 dead, 0 UPDATEs ever, 10.18 M DELETEs (the 09-13 retire) |
| `location_claims_fingerprint` (UNIQUE, `claim_fingerprint`) | 1,098 MB | 772 M scans: every intake insert probes it |
| `location_claims_listing` (`listing_id, claim_type, first_observed_at DESC`) | 664 MB | the resolver's read |
| `location_claims_pkey` | 348 MB | |
| `location_claims_by_method` (`extraction_method, claim_type, first_observed_at DESC`) | 347 MB | 266 k scans; **no code reader found** by grep |
| `location_claims_geom` (GiST, partial) | 105 MB | 1 scan ever (10-30, ad hoc) |
| `location_claims_contract_entry` (partial) | 105 MB | used by `contracts.retract` |
| `location_claims_ephemeral` (partial, `ephemeral_display_only`) | 8 kB | 0 rows; last scan 08-13 |
| **Total** | **6,271 MB** | indexes 2,671 MB |

Licence classes (one full aggregate): `portal` 14,820,662 rows, `operator` 1 row (the 2026-08-13
`address_point_id` correction). No `odbl`, no `ephemeral_display_only`, ever. Every portal claim has a
contract entry. Every active contract has at most one entry per claim type (checked on prod; enforced by
`contracts._check_shape`, contracts.py:929-952). So the natural key `(listing_id, claim_type,
licence_class)` is the existing invariant, never yet written down.

pg_stat_statements (cumulative): the current claim write averages 0.26 s per call (27,633 calls,
7,312 s); the resolver's per-type-window read averages 128 ms per bulk slice (41,472 calls, 5,302 s).

### 1.2 Multiplicity per (listing, claim type, licence class)

S1: 148,057 claims; 59,869 keys.

| Cohort | Keys | Rows | Rows beyond 1 per key | Keys with >1 row |
|---|---|---|---|---|
| Active listings | 22,058 | 48,757 | 26,699 (54.8 %) | 14,844 (67 %) |
| Inactive listings | 37,811 | 99,300 | 61,489 (61.9 %) | 31,067 (82 %) |
| **All** | **59,869** | **148,057** | **88,188 (59.6 %)** | 45,911 |

Corpus estimate: **≈ 5.98 M keys** survive and **≈ 8.81 M rows** go (×99.93). Plan said 5.95 M / 8.82 M.

**What the surplus rows are** (S1, each surplus row compared with its key's newest row on every stored
value column: text, num, geom within 1 m, jsonb, declared label, blur, subject_scoped):

| Surplus row vs the key's newest | Rows | Share |
|---|---|---|
| same value, older contract version | 87,910 | **99.68 %** |
| different value, other version (extractor change or a portal edit across a bump) | 246 | 0.28 % |
| different value, same version (a portal edit; what the tie rules arbitrate) | 29 | 0.03 % |
| same value, same version (hash-only inputs differed) | 3 | 0.00 % |

So the "8.82 M dead rows" are, almost entirely, the same fact re-stated under a newer contract version.
**Basis correction.** inv-B's 8,821,874 counts rows whose contract header is retired. The purge set is
"every row that is not its key's keeper", which is almost the same set but not quite: it keeps
**≈ 15.7 k retired-version rows** that are still their key's current evidence (S1: 157 keys; 156 on
inactive listings), and it also deletes the **≈ 3.8 k same-version duplicates** (below). The 157 keys:
sreality `cast_obce_name`@3 119 (v4 deliberately dropped the legacy `/locality/value` fallback, so v4
is silent on frozen legacy payloads), bazos `obec_name`@7/8 30 (listings with no reading: the slug town
is their only town; architecture.md:4830 already warns about these), bezrealitky house numbers and
country @3 6, idnes `precision_declaration`@5 2 (a marker v6 did not restate). The current version
window serves all of them; deleting them would be W18-b again for one type at a time. This is why the
retire script cannot be re-run as the purge: its re-mine rail counts listings, not types.

**Same-type multiplicity at the effective version** (what the seven tie rules decide today; the
resolver's own window: newest contract version ≤ active, type declared):

| Cohort | Sample | Listings with ≥ 2 values | Corpus | Rows to collapse |
|---|---|---|---|---|
| Active | S5a | 105 / 19,862 (0.53 %): ceskereality 59, sreality 37, realitymix 4, mmreality 3, idnes 2; bazos, remax, bezrealitky, maxima 0 | **≈ 2,100** (inv-B 25 % sample: 2,450) | ≈ 2,900 |
| Inactive (the critic's unmeasured item) | S2i | 29 / 27,475 (0.106 %): ceskereality 7, sreality 5 in the second draw | **≈ 730** (95 % CI ≈ 490 to 1,040) | ≈ 970 |

Types involved (active): coordinate, house_number_cp (ceskereality), cast_obce_name, obec_name,
okres_name, house_number_co, precision_declaration (sreality, mmreality), street_name (idnes).

### 1.3 Newest versus oldest value

S1, keys with ≥ 2 rows, newest row by `(first_observed_at, id)` vs oldest, **across all versions**:

| Type | Active: differ / keys | Inactive: differ / keys |
|---|---|---|
| coordinate (> 1 m) | 17 / 2,300 (0.74 %; 11 moved > 50 m, 4 > 500 m) | 4 / 5,041 |
| obec_name | 12 / 2,613 (0.46 %) | 124 / 4,774 (2.6 %): 100 are sreality v3→v4 composite lines ("Brno" → "Brno - Brno-jih"), 14 idnes, 9 remax ("Praha" → "Praha 10") |
| psc | 1 / 782 | 0 / 2,539 |
| street_name | 1 / 1,027 | 1 / 2,622 |

At the **effective version** (portal edits only, active, inv-B, whole corpus): pins 2,289, towns 104,
PSČ 41, streets 50; the newest claim equals the current page value in 98.6 to 100 %.

Re-check on sreality pins (S25sr): 223 multi-pin listings (≈ 890, inv-B 886). The newest pin equals the
current `raw_json` pin on 220 (98.7 %); 3 are A→B→A reverts. What is served: the **oldest** pin on 102,
the newest on 59, and **a register point (address point or street, not the pin) on 62 (28 %)**. The
acceptance line in the plan ("served pin equals the portal's current pin on ≥ 99 % of sreality multi-pin
ads") cannot hold as worded: `place()` puts 28 % of them on a register point by design. §7 rewords it.

Precision declarations (S25sr-style 25 % sample, sreality + mmreality, active): 133 + 9 listings hold
≥ 2 declarations; 55 + 8 are read blurred although the newest declaration is precise. Corpus ≈ 250
(inv-B 234 on 10-30; the population grows about 45 a day between contract bumps).

### 1.4 Q4: the 630 towns and 397 streets (absence after a newer extractor)

**Where the number comes from.** The readings half (`claims_intake._READING_WRITE_SQL`,
claims_intake.py:985-1013) deletes the listing's same-source claims of every type the reading entries
DECLARE (types built at :1824, passed at :1847), so a null slot in a newer reading deletes the older
reading's value. Reproduced today on active bazos/web listings (`listing_description_enrichments`, rows
with a `location` block and no error, newest by `created_at`):

| | Count |
|---|---|
| active bazos/web listings with ≥ 2 readings | 36,008 (2,044 with ≥ 2 distinct texts) |
| newest reading drops a town an older reading had | **625** (606 on the same text) |
| newest reading drops a street | **394** (365 on the same text) |
| extractor version of every one of those newest readings | `2:ea016577:gpt-5.6-luna` (since 10-14), the older ones `2:f411463b` |

**Why the newer reading is null** (same-text drops):

| Newest reading | Towns (606) | Streets (365) |
|---|---|---|
| **empty location block**: every slot null, `ad_kind` null although the schema says "always set" | **303 (50 %)** | **197 (54 %)** |
| foreign (`country` ≠ CZ, new or old) | 109 | 2 |
| `offer`, Czech: a genuine re-judgement | 153 | 152 |
| reclassified `wanted` / `not_property` / `exchange` | 21 / 19 / 1 | 1 / 11 / 2 |

The empty block is a text-lane defect, not a judgement: the newer extractor version produced **1,915
empty blocks out of 143,117 readings (1.3 %)**; the older version 0 of 108,716.

**Sample of 30** (the same-text town drops, ordered by `md5(listing_id)`, first 30). **Method**: for each,
the older value and its evidence quote (V1-verified against the same text), the newer reading's
`ad_kind`/`country`/`part_of_town`, the ad's current title (`raw_json ->> 'title'`), the served
`listing_location` town and the stored claims; I judged which value names the obec the property is in,
using the title and quote as truth and RÚIAN plausibility of the name. Note that **today none of the 30
holds a reading town claim any more**: the supersession already deleted it, so the served town comes from
the PSČ or pin.

| Verdict | n | Listings |
|---|---|---|
| foreign: neither value binds (the register is Czech) | 7 | 18997538 Torrevieja, 19008969 Szczyrk, 18969665 + 19209966 Slunečné pobřeží, 19091602 Großschirma, 18965152 Pattaya, 18657850 Hurghada |
| **older value right**, newer block EMPTY | 14 | 19065638 Hřensko, 19134069 Tišnovská Nová Ves, 19172618 Praha, 19099234 Brno, 19007031 Hodonín, 19082082 Orlová, 19029644 Praha, 18664967 Brno, 19009178 Vratěnín, 18843571 České Budějovice, 18893201 Tábor, 19080850 Aš, 19085483 Úvaly, 18961150 Brno |
| **older value right**, newer re-judgement wrong | 4 | 18697580 Žlutice (new: not_property, SK); 19246921 Jilemnice (new kept part Hrabačov, same town); 18789429 Křesín and 19273665 Soběsuky ("v k.ú. X" where X is also an obec: likely, not certain) |
| **newer null right** | 5 | 19236863 "U SEDLČAN" and 19233609 "na Hatích u Znojma" (near-town phrases; Hatě lies in Chvalovice); 19191884 "zahrádkářská lokalita Mutěnka" (a garden colony); 19272131 "k.ú. Chotěšov u Vrbičan" and 19271890 "k.ú. Dobkovičky" (cadastral names, not obce) |

Served town today is **wrong in 6 of the 18 older-right cases** (Žlutice → Pšov, Tišnovská Nová Ves →
Křižanov, Vratěnín → Korolupy, České Budějovice → Planá, Křesín → Lhotka nad Labem, Soběsuky →
Troubky-Zdislavice): the deletion costs served quality today.

**Recommendation on Q4: A** (keep the last stated value), with the empty-reading fix beside it.
- Half the cohort is empty readings. Under C every one of them retracts a correct town (14 of 14 in the
  sample). Under A they write nothing.
- On genuine Czech re-judgements C is a coin flip: the newer model was right in 5 of 9 sampled non-empty
  Czech cases, the older in 4. Its wins are principled (distance phrases, cadastral names); so are some
  of its losses.
- C needs new state on one substrate: bodies (`portal_raw_payloads.contract_version`) and readings
  (`mined_contract_version`) carry a "mined at version" stamp, the payload substrate carries none, so
  "silence under a new version" is undefined for sreality and bezrealitky without a new column.
- The payload and page substrates already behave like A across versions today (the ≈ 15.7 k keys of §1.2
  are served from an older version the active one stopped stating). A makes the readings agree.
- An extractor author who wants old values gone has the explicit act: `contracts.retract --extractor-id`.
- **No backfill** of the 625/394 already-deleted values: the empty-reading fix re-reads half of them,
  and the other half is the coin flip above.

---

## 2. Design: one rule

> **For each listing, claim type and author class (`portal` or `operator`), the store holds ONE claim:
> the value that author stated most recently. The operator outranks the portal. A value the page stops
> stating is kept; retraction is an explicit act.**

### 2.1 The key and the one write

- **Key:** `UNIQUE (listing_id, claim_type, licence_class)`, index `location_claims_key`. The author
  class IS the licence class: portal claims are `portal`, operator claims `operator`. (`odbl`, the
  realitymix Nominatim branch in `page_readers`, has emitted 0 rows ever and stays a class the resolver
  never reads; it gets its own key slot, so it can never overwrite a portal claim.)
- **One statement, both writers.** `claims_intake` (payload, page body, reading) and
  `operator_corrections` call the same function, `claims_intake.write_claims(cur, claims, reason)`,
  which runs the same SQL. `operator_corrections._OPERATOR_CLAIM_SQL` (operator_corrections.py:76-153,
  the hand copy) goes; the operator builds a `Claim` and passes `reason='operator_edit'`.

```sql
WITH input AS (
  SELECT * FROM jsonb_to_recordset(%(rows)s::jsonb) AS x(
    listing_id bigint, source text, first_observed_at timestamptz, claim_type text,
    surface text, extraction_method text, contract_entry_id bigint,
    value_text text, value_num numeric, value_geom_wkt text, value_jsonb jsonb,
    declared_precision_label text, declared_radius_m numeric, blur_evidence text,
    licence_class text, subject_scoped boolean, retract boolean)
), one AS (                       -- defensive: 0 measured cases of two values in one observation
  SELECT DISTINCT ON (listing_id, claim_type, licence_class) * FROM input
   ORDER BY listing_id, claim_type, licence_class, first_observed_at DESC
), up AS (
  INSERT INTO location_claims AS c (listing_id, source, first_observed_at, claim_type, surface,
         extraction_method, contract_entry_id, value_text, value_num, value_geom, value_jsonb,
         declared_precision_label, declared_radius_m, blur_evidence, licence_class, subject_scoped)
  SELECT o.listing_id, o.source, o.first_observed_at, o.claim_type::location_claim_type, ...,
         CASE WHEN o.value_geom_wkt IS NULL THEN NULL ELSE ST_GeomFromText(o.value_geom_wkt, 4326) END,
         ...
    FROM one o WHERE NOT o.retract
  ON CONFLICT (listing_id, claim_type, licence_class) DO UPDATE SET
         first_observed_at = EXCLUDED.first_observed_at, surface = EXCLUDED.surface,
         extraction_method = EXCLUDED.extraction_method,
         contract_entry_id = EXCLUDED.contract_entry_id, value_text = EXCLUDED.value_text, ...
   WHERE (c.value_text, c.value_num, c.value_geom, c.value_jsonb, c.declared_precision_label,
          c.declared_radius_m, c.blur_evidence, c.subject_scoped)
         IS DISTINCT FROM
         (EXCLUDED.value_text, EXCLUDED.value_num, EXCLUDED.value_geom, EXCLUDED.value_jsonb,
          EXCLUDED.declared_precision_label, EXCLUDED.declared_radius_m,
          EXCLUDED.blur_evidence, EXCLUDED.subject_scoped)
  RETURNING c.listing_id
), gone AS (                      -- the explicit absence a marker states (§2.5)
  DELETE FROM location_claims c USING one o
   WHERE o.retract AND c.listing_id = o.listing_id AND c.claim_type::text = o.claim_type
     AND c.licence_class::text = o.licence_class
  RETURNING c.listing_id
), enqueued AS (
  INSERT INTO dirty_locations (listing_id, reason)
  SELECT DISTINCT listing_id, %(reason)s FROM (SELECT listing_id FROM up
                                               UNION ALL SELECT listing_id FROM gone) ch
  ON CONFLICT (listing_id) DO UPDATE
     SET enqueued_at = now(), reason = EXCLUDED.reason, attempts = 0, next_eligible_at = now()
  RETURNING listing_id
)
SELECT (SELECT count(*) FROM up), (SELECT count(*) FROM gone), (SELECT count(*) FROM enqueued)
```

- **Writes only on change.** An unchanged re-mine (and every contract bump that restates the same value)
  creates no row version and enqueues nothing. A changed value is an in-place UPDATE: the claim keeps
  its `id`; `claim_set_hash` (serialize.py:71-86) hashes the value and `first_observed_at`, so the
  listing still re-resolves. A→B→A writes twice and ends on A, which the time-free fingerprint could not
  do (12 of 886 sreality pins; every operator restatement).
- **Provenance: the contract entry that FIRST stated the current value.** `contract_entry_id`,
  `surface`, `extraction_method` and `first_observed_at` move only with the value. Consequences, each
  stated in the code's header comment:
  1. A bump with unchanged values writes 0 rows (the point).
  2. `contract_entry_id` is provenance, not admissibility: the read no longer filters by version (§2.2).
     Otherwise re-activating an older contract would strand every claim whose value the newer version
     first stated and the older one restates unchanged (no write, so no new provenance, and a
     version filter would hide it for ever).
  3. `contracts.retract <portal>@N` deletes every value version N first stated, including values later
     versions re-confirmed. Those types are empty until re-mined, so a retract is followed by one full
     re-mine of that source (§8 R4). The same is true of an activation: bodies and readings re-mine by
     their stamps, the payload half needs `claims_intake --mode full --source X` (today's hand step after
     a bump, unchanged).
- **Retract stays** (`contracts.retract`, contracts.py:1299-1357): the explicit retraction. Its
  comment "Safe because nothing UPDATEs `location_claims`" (contracts.py:1141) becomes false: the ctid
  batch still terminates (a concurrently updated row gets a new ctid, is skipped, and the next batch
  picks it up because its `contract_entry_id` still matches), so only the comment changes.
- **Reverted page bodies** (inv-B P2): `payloads._APPEND_SQL` (payloads.py:411) gains one CASE arm in
  its ON CONFLICT: when the conflicting body was not the key's newest-observed body before this
  sighting, `contract_version = NULL`, so the bodies pass re-mines A after A→B→A (14 keys measured).

### 2.2 The one read

`resolve_db._claims_sql` (resolve_db.py:145-175) becomes:

```sql
SELECT DISTINCT ON (c.listing_id, c.claim_type) <_CLAIM_COLUMNS>
  FROM location_claims c
 WHERE {listing_filter}
   AND (c.licence_class = 'operator'
        OR c.licence_class = 'portal' AND EXISTS (
             SELECT 1 FROM portal_contract_entries ace
               JOIN portal_contracts act ON act.id = ace.contract_id AND act.is_active
              WHERE act.source = c.source AND ace.claim_type = c.claim_type))
 ORDER BY c.listing_id, c.claim_type, (c.licence_class = 'operator') DESC
```

(then re-sorted by `listing_id, id` for `core.resolve`). It keeps the licence rail and the
declared-type `EXISTS` (a type the active contract no longer declares is not read; today that filters
nothing: 0 such keys in S1). It deletes the window, `pc.version <= act.version` and the three joins.
**The resolver therefore sees at most one claim per type, and the operator's when there is one (Q5).**

### 2.3 What deletes (each rule is unreachable once the read returns ≤ 1 claim per type)

The seven tie rules:

| # | Rule | Where |
|---|---|---|
| 1 | `address_point_id`: last claim wins (overwrite in the loop) | bind.py:228-230 |
| 2 | PSČ: first claim wins (`psc = psc or value`) | bind.py:231-235 |
| 3 | street key / čp / čo: first claim wins (`street_key or key`, `if cp is None`, `co if co is not None`) | bind.py:236-252 |
| 4 | pin election by `(declared_rank, id)`: oldest of equal rank | bind.py:1038-1045 `declared_rank`, 1091-1098 `elect_pin`; the `pin_claim_id` threading at bind.py:203-209, 259-265, 568, 574 and core.py:56-61, 77 |
| 5 | precision: first label wins, blur OR'd across every claim, `coordinate_claim_id` narrowing | bind.py:1048-1088 (`read_declared_precision` becomes a read of the one declaration plus the one coordinate's own label) |
| 6 | two street claims: per-line `reached` pooling and the cross-claim `spelled` union that ends in `ambiguous_streets` | composite.py:383-418 (the register-ambiguity branch, one line matching several streets, stays) |
| 7 | operator precedence: oldest correction wins (`setdefault`), four fields bypass the register | bind.py:283-303 (`OPERATOR_METHOD`, `OPERATOR_FIELDS`, `operator_fields`), fill.py:34 (docstring), 81, 86, 121-125, core.py:92 |

The supersession paths that go (4 → 2: the upsert and `contracts.retract`):

| Path | Where |
|---|---|
| per-type version window | resolve_db.py:74-131 (`_ADMISSIBLE_CONTRACT` and its 57-line incident comment, re-homed in one paragraph of architecture.md) and 145-175 (`max(pc.version) OVER (...)`, `contract_version = newest_version`) |
| readings DELETE on declared types | claims_intake.py:985-1013 (`superseded` CTE, the `changed` union, the third counter), the `claim_types` plumbing at :993, :1824, :1847, the `claims_superseded` stat (:1857-1869, :1886, :2032, :2253), docstring lines 4-5, 19-20, 55-56 |
| retire script + workflow | `scripts/location_claims_retire.py` (305 lines), `.github/workflows/location_claims_retire.yml` (158), `tests/location_data/test_claims_retire.py` (248), the `GROUP_WORKFLOWS` entry (location_claims_intake.yml:275), `tests/test_workflow_artifacts.py:29`, `test_location_batch_hardening.py:68`, database skill SKILL.md:485, the workflow-docs codegen entry |

The 23-argument fingerprint:

| Piece | Where |
|---|---|
| `_CLAIM_FINGERPRINT_SQL` and its 27-line rationale | claims_intake.py:849-875 |
| `fingerprinted` / `deduped` CTEs, `ON CONFLICT (claim_fingerprint) DO NOTHING` | claims_intake.py:898-918 |
| `chunk_rows` docstring's fingerprint argument | claims_intake.py:1212-1215 (the listing-boundary rule stays: one key never spans two statements) |
| the operator's hand copy and its "unconditional enqueue / restatement" rationale | operator_corrections.py:7-27, 76-153, the `restatement` result field (:251) |
| Python mirror kept only to test the hash | claims_common.py:925-968 (`MIRROR_UNSAFE_CHARS`, `mirror_is_faithful`, `value_norm_mirror`) and their re-exports (claims_intake.py:98, 121, 124) |
| `location_claim_fingerprint(23 args)` (mig 386), `location_value_norm(text)` (mig 382), column `claim_fingerprint`, index `location_claims_fingerprint` (1,098 MB) | migration 6NN+1 (§4); prod has no other caller of either function |
| the test that guards it | `tests/location_data/test_claims_intake_fingerprint.py` (189), `test_claims_slim_migration.py:140-151` |

Follow-up cleanup (not counted below, own commit): the hash-only `Claim` inputs nobody stores
(`travel_mode`, `target_text`, `distance_m`, `declared_confidence`, `legacy_source_column`) are threaded
through about 30 reader sites and the 23 golden fixtures in `tests/fixtures/location_w2/golden/`.

### 2.4 Absence under A and under C

- **A (recommended, §1.4):** a reader that emits nothing for a type writes nothing; the stored claim
  stays. Readings follow the same rule, so a null slot keeps the value. What clears a value: a different
  value from the same author, the operator (who outranks), `contracts.retract`, or a marker's explicit
  absence (§2.5).
- **C (not recommended):** as A, plus "the first mining of a listing under a new extractor version
  re-states the full set": every declared type the new version is silent on is deleted. Needs a
  per-listing, per-substrate "mined at version" stamp: present for bodies and readings, absent for the
  payload substrate (a new column or table on 1.08 M listings). It would retract the ≈ 15.7 k keys of
  §1.2 (mostly the deliberate sreality v4 fallback drop) and, without the empty-reading fix, ~300 correct
  bazos towns and ~200 streets.
- **Companion fix under either option (text lane, `toolkit/description_extraction.py`):** an empty
  location block (`ad_kind` null, every slot null) is a failed reading (`extracted.error`, retried under
  `GIVE_UP_AFTER`), never a reading the claim lane mines. One UPDATE re-marks the 1,915 stored ones so they
  are re-read. This is the text-lane twin of W0's "an empty page is a failed fetch".

### 2.5 Presence markers state their absence

Three `html_marker` entries can only state presence, so under any recency rule a blur, once stated, is
never cleared (today the same is true: the window serves the v5 idnes marker on keys v6 did not restate).

| Entry (active version) | Marker | Claims under active contracts (on active listings) |
|---|---|---|
| bazos `bzs.det.blur_hint` (bazos@9) | `a[href*='/place/']` title contains "Přibližná lokalita" → `approximate_location` | 200,953 (53,523) |
| idnes `id.det.no_exact_disclaimer` (idnes@6) | `body` contains "Nemovitost nemá přesnou adresu, nachází se ve vyznačené oblasti." → `no_exact_address` | 92,756 (31,011) |
| realitymix `rm.det.agency_est_flag` (realitymix@7; W4 re-points it to `#map` in v8) | `div#print-map, div#map` with blank `data-gps-lat/lon` → `estimated` | 7,317 (3,659) |

**Rule:** one optional locator key on `html_marker`, `absent: retract` (contracts.py:364-368 adds it to
`optional_keys`; page_readers.py:371-435 gains about 6 lines). When the selector's node EXISTS and the
marker test fails, the reader emits the key's tombstone (a `Claim` with `retract=True` and no value);
the one write deletes the portal claim of that key and enqueues (§2.1 `gone`). When the node does not
exist (a page with no map, a layout change), nothing is emitted. The three entries add the key: bazos@10,
idnes@7, realitymix@9 (or fold into W4's realitymix@8 if W4's PR is still open).
- Why a tombstone, not a stored neutral label (the critic's accepted `absent_label`): a label such as
  `undeclared` would be read by `read_declared_precision` as "declared and not blurred", i.e. a precise
  pin (bind.py `pin_is_precise = bool(label) and not blurred`), so bind, the W1 accuracy cells and W6's
  `(source, declared label)` table would each need to learn that one word means "no label"; it would
  also add ≈ 120 k active (≈ 300 k total) rows for idnes and realitymix pages without a marker. The
  tombstone leaves those pages exactly as today (no declaration) and teaches no reader anything.
- Effect: a page that drops "Přibližná lokalita", the idnes sentence or the blank `data-gps` stops being
  read blurred on its next re-mine. The 234 to 250 sreality/mmreality rows of §1.3 are not markers; the
  one-claim read alone fixes them.

### 2.6 Rule 25 and the docs that move in the same PR

- CLAUDE.md rule 25 (line 249): "Only `claims_intake` and `operator_corrections` write
  `location_claims`, both fingerprinted in SQL" → "both through the one keyed upsert (one claim per
  listing, type and author; the operator outranks the portal)".
- docs/architecture.md § Location data: 4755-4772 (ONE EVIDENCE TABLE: fingerprint, three DELETE
  paths), 4825-4830 (readings supersession), 5017-5066 (bump/window/retire; the W11 and W18-b incident
  history shrinks to one paragraph: "a bump writes nothing until a value changes, so a partial re-mine
  can never blank a type").
- `.claude/skills/database/SKILL.md:485` (retire workflow).

---

## 3. Deletion ledger versus additions

Approximate lines (code + tests + workflow + docs), from the line ranges above.

| Deleted | Lines |
|---|---|
| retire script, workflow, test | −711 |
| fingerprint test | −189 |
| claims_intake: fingerprint SQL and comment, two CTEs, readings supersession, `claim_types`, superseded stat, docstrings | −95 |
| operator_corrections: hand-copied statement, restatement rationale, result flag | −95 |
| claims_common mirror | −44 |
| resolve_db: window, version rail and its comment, three joins | −85 |
| bind: tie rules 1-5 and 7, `elect_pin`, `declared_rank`, `pin_claim_id` threading, operator fields | −95 |
| fill/core/composite: operator override, pin plumbing, multi-claim pooling | −25 |
| tests that pin deleted behaviour (version-window cases in test_resolver_admissibility, elect_pin/declared_rank/operator_fields cases, readings supersession cases), net of their replacements | ≈ −150 |
| test_claims_slim_migration `test_the_fingerprint_function_is_untouched` | −12 |
| architecture.md supersession/window/retire prose | ≈ −45 |
| **Total deleted** | **≈ −1,550** |

| Added | Lines |
|---|---|
| the one upsert's ON CONFLICT DO UPDATE … WHERE, the `gone` CTE, `write_claims` | +35 |
| the one read (DISTINCT ON, operator first) | +15 |
| `html_marker` `absent: retract` + `Claim.retract` + 3 contract lines + validator key | +15 |
| payloads revert CASE arm | +6 |
| migration 6NN + 6NN+1 (SQL with comments) | +110 |
| purge (the retire script re-aimed for its one last run, deleted in PR-2) | 0 net (lives only between PR-1 and PR-2) |
| new tests (§6) | +260 |
| text-lane empty-block rule (companion) | +10 |
| **Total added** | **≈ +450** |

**Net ≈ −1,100 lines** (plan said −700; the retire test and the test deletions were not in its count).
Schema: −1 column (`claim_fingerprint`), −2 functions, indexes 7 → 6 (−fingerprint, −listing,
−ephemeral, +key). Rules: 7 tie rules → 0; 4 supersession paths → 2; 2 identities per row → 1.

---

## 4. Migration draft

Two numbered files (claim both numbers ≥ 699 with the merge coordinator; 696-698 are taken), plus the
purge run in between. Destructive steps are pre-approved by Q13, each with an R2 backup and a receipt.

### Step 0: backup (Q13)
`backup_before_drop` workflow, target the whole relation: `python -m scripts.backup_before_drop
w3-claims location_claims` → `backups/w3-claims/<UTC date>/location_claims.sql.gz` (pg_dump, schema +
data, session URL). Covers the purge and the column drop (`claim_fingerprint` is not recomputable: 11 of
its inputs were never stored). Size estimate ≈ 1 GB gz (the 09-13 retire CSV was 596 MB for 9.4 M
rows); `\copy` of 9.4 M rows took 78 s then, so a few minutes. Receipt = the workflow's printed size and
row count.

### Step 1: the batched purge (PR-1's re-aimed `location_claims_retire.yml`, dispatched with `--ref`)
The keeper per key is the row today's resolver reads: newest version ≤ active, then newest observation.
Keyset over `listings.id`, 2,000 listings per statement, each its own transaction:

```sql
SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '120s';
WITH batch AS (SELECT id FROM listings WHERE id > %(after)s ORDER BY id LIMIT 2000),
ranked AS (
  SELECT c.id, row_number() OVER (
           PARTITION BY c.listing_id, c.claim_type, c.licence_class
           ORDER BY (pc.version <= act.version) DESC NULLS LAST, pc.version DESC NULLS FIRST,
                    c.first_observed_at DESC, c.id DESC) AS rn
    FROM batch b JOIN location_claims c ON c.listing_id = b.id
    LEFT JOIN portal_contract_entries pce ON pce.id = c.contract_entry_id
    LEFT JOIN portal_contracts pc ON pc.id = pce.contract_id
    LEFT JOIN portal_contracts act ON act.source = pc.source AND act.is_active
), del AS (
  DELETE FROM location_claims c USING ranked r WHERE c.id = r.id AND r.rn > 1 RETURNING c.listing_id)
SELECT count(*), (SELECT max(id) FROM batch) FROM del;
```

- Measured: the selection for 2,000 listings (31 k claims) runs in 1.7 s on prod (EXPLAIN ANALYZE,
  index-driven, 3.5 k buffer reads). With the delete (≈ 16 k rows across 7 indexes) and a 0.5 s pause,
  ≈ 3.5 s a batch; 1,082,934 listings → 542 batches → **≈ 30 to 45 minutes** (the 09-13 retire deleted
  9.44 M rows in 750 s by id keyset; this predicate is heavier).
- No enqueue: for 99.97 % of keys the keeper is what the resolver reads now; the ≈ 2.8 k listings whose
  keeper changes (the same-version duplicates, §1.2) re-resolve in W3's version sweep (Step 4).
- Resumable: the cursor is the listing id; re-running re-derives the same keepers.
- The script also writes the ≈ 2.8 k collapsed listing ids to its receipt (R2), for Step 5.

### Step 2: migration 6NN (apply after the purge, before the merge; statement-autocommit runner as 498)

```sql
-- 6NN_location_w3_one_claim_per_fact.sql
set lock_timeout = '5s';                  -- ACCESS EXCLUSIVE, metadata only; re-run on timeout
alter table location_claims alter column claim_fingerprint drop not null;   -- new writer omits it

begin;                                    -- writers wait (their lock_timeout) and retry; readers run
set local lock_timeout = '10s';
set local statement_timeout = '15min';
lock table location_claims in share row exclusive mode;
delete from location_claims c using (    -- rows the old writer added after the purge passed them
  select id from (
    select id, row_number() over (partition by listing_id, claim_type, licence_class
                                  order by first_observed_at desc, id desc) rn
      from location_claims
     where (listing_id, claim_type, licence_class) in (
           select listing_id, claim_type, licence_class from location_claims
            group by 1, 2, 3 having count(*) > 1)) r
   where rn > 1) d
 where c.id = d.id;
create unique index location_claims_key on location_claims (listing_id, claim_type, licence_class);
commit;

drop index concurrently if exists location_claims_listing;   -- the key serves listing_id lookups
```

- Lock budget: one GROUP BY over a 3.6 GB heap (≈ 6 M live rows) plus a 6 M-row btree build: about 1 to
  2 minutes of SHARE ROW EXCLUSIVE. Readers (resolver drain, Browse never reads claims) are not blocked;
  intake batches hit their 20 s lock_timeout, roll back and resume from their cursors.
- New index ≈ 200 MB (16 key bytes + tuple overhead, 6 M entries).
- **Between this apply and the deploy** the old writer (`ON CONFLICT (claim_fingerprint) DO NOTHING`,
  targeted) raises unique_violation on any changed value: its batch rolls back and retries next tick.
  No data loss (the readings statement rolls back its DELETE too); a stall of at most the deploy time
  (≈ 5 to 10 min) on batches that carry a value change.

### Step 3: merge PR-1, Railway rolls out (worker + API)

### Step 4: re-resolution
PR-1 bumps `RESOLVER_VERSION` (the read and bind semantics change). With W2 live (Q14 puts W2 before
W3), the worker's (resolver version, registry label) trigger sweeps the corpus, active first. If W2 were
not live: today's hand dispatch of `location_resolve.yml` full-resolve.

### Step 5: scoped re-mine of the collapsed keys
For the ≈ 2.8 k listings from Step 1's receipt: un-stamp their latest bodies (`contract_version = NULL`)
and readings (`mined_contract_version = NULL`) so the hourly lane re-mines them; run
`claims_intake --mode full --source sreality` once (payload substrate; writes only the ≈ 1.4 % A→B→A
guesses, about 12 sreality pins). The three marker contract bumps (§2.5) re-mine every latest body of
bazos, idnes and realitymix through the hash gate anyway.

### Step 6: migration 6NN+1 (PR-2, after one green intake tick on the new code)

```sql
-- 6NN+1_location_w3_drop_fingerprint.sql
set lock_timeout = '5s';
drop index concurrently if exists location_claims_fingerprint;   -- 1,098 MB
drop index concurrently if exists location_claims_ephemeral;     -- 0 rows; the class is refused at write
alter table location_claims drop column if exists claim_fingerprint;
drop function if exists location_claim_fingerprint(bigint, text, text, text, text, text, text, text,
  text, bigint, text, text, numeric, geometry, geometry, jsonb, integer, text, text, text, text,
  numeric, text);
drop function if exists location_value_norm(text);
```

PR-2 also deletes the purge script, its workflow and test, and the fingerprint test.

### Step 7: space (maintenance, outside the migration files)
- `REINDEX INDEX CONCURRENTLY` on `location_claims_pkey`, `_by_method`, `_contract_entry`, `_geom`
  (online): index total 2,671 MB → ≈ 0.6 GB.
- The heap stays 3.6 GB with ≈ 2.1 GB free space after the purge. The upsert inserts ≈ 50 k rows a day,
  so the space is not reused for years. Reclaiming it needs `VACUUM FULL location_claims` (ACCESS
  EXCLUSIVE, ≈ 2 to 4 min: the resolver and intake stall that long) or pg_repack (available, not
  installed). Ruling R7 in §8.

### CI replay
- `migrations.yml` replays every file with `psql -f` (statement autocommit), so `CONCURRENTLY` and the
  explicit `begin … commit` work (precedents 605, 618, 625, 635, 653). On an empty table the dedup and
  the build are no-ops.
- `test_claims_slim_migration.py` pins 498's own text (immutable): the partition, create-nothing and
  shadow tests stay green. `test_every_claim_write_inserts_exactly_the_kept_columns` changes to the new
  writer set (`KEPT_COLUMNS − {id, claim_fingerprint}`, one shared statement instead of three);
  `test_the_fingerprint_function_is_untouched` goes in PR-2.
- `tests/sql_corpus` PREPAREs the new upsert: its `ON CONFLICT (listing_id, claim_type, licence_class)`
  needs 6NN in the same PR (it is). After PR-2 the corpus check fails any statement that still names
  `claim_fingerprint`, `location_claim_fingerprint` or `location_value_norm`.

---

## 5. Rollout order

1. **Prerequisites:** W2 live (the version sweep, Step 4). W4 merged (realitymix@8 lands first; W3's
   marker bump is realitymix@9, or folds into @8 if W4 is still open). #2194 (v5.9) merged long since.
2. **PR-1 (draft early):** the code (§2), the re-aimed purge, 6NN, RESOLVER_VERSION bump, contract
   bumps bazos@10 / idnes@7 / realitymix@9, docs and rule-25 text, tests.
3. **Standing autodedup shadow gate, before Step 2.** Praha (town 554782 and its 112 quarter blocks),
   Pardubice, Liberec, 7 more towns and 30 okresy are in the live `autodedup_apply_scope`. W3 moves the
   inputs of ≈ 2.1 to 2.5 k active ads (≈ 2,289 pins, 1,040 of them > 50 m; 104 towns; 41 PSČ; 50
   streets; ≈ 250 blur flags) plus the marker clears. A 2 % sample put **17 of 33 movers (≈ 52 %) inside
   the apply scope** (okres in the 30 districts or obec in the 10 towns). So: run the branch's resolver
   over the movers' post-purge claims (pure function, no write), feed the changed rows to one shadow
   autodedup generation on the approved-split sessions; pass = no approved session changes verdict.
   `DECISION_MODULES` are untouched (W3 is `location_data/` only).
4. Steps 0 → 1 → 2 → 3 → 4 → 5 of §4, then PR-2 (Step 6) after one green intake tick, then Step 7.
5. **W3b after W3** (the address picker writes operator claims through `write_claims`; outranking is
   §2.2's ORDER BY). W5 after W3 (bind.py overlap: W3 empties `collect_constraints` of its tie idioms
   first).

---

## 6. Test plan

Offline (pytest, no DB):
1. `write_claims` builds ONE statement for both writers; `operator_corrections` imports it (no
   `INSERT INTO location_claims` text left in operator_corrections.py; a grep rail).
2. The statement's ON CONFLICT target is the key, its `WHERE` compares exactly the eight value columns,
   and `first_observed_at`/`contract_entry_id` move only inside the `DO UPDATE` (static parse).
3. `_claims_sql`: no `OVER (`, no `version <=`, one `DISTINCT ON (c.listing_id, c.claim_type)`, operator
   first; `test_the_claims_select_maps_onto_claim_positionally` stays green.
4. `html_marker`: node present + marker absent + `absent: retract` → one tombstone; node absent → none;
   `absent` refused on any other reader (contract validator).
5. bind: with one claim per type, `collect_constraints` / `read_declared_precision` give the old answers
   on the existing single-claim fixtures; the deleted symbols are gone (`elect_pin`, `declared_rank`,
   `operator_fields`, `OPERATOR_FIELDS`).
6. `test_resolver_jobs._enqueue_ctes` → {the shared upsert, `_RETRACT_BATCH_SQL`}; the bump semantics
   tests stay.
7. Migration static rails (new `tests/location_data/test_claims_key_migration.py`): 6NN creates exactly
   `location_claims_key (listing_id, claim_type, licence_class)`, locks SHARE ROW EXCLUSIVE before its
   dedup, drops `location_claims_listing` concurrently, drops NOT NULL on `claim_fingerprint`; 6NN+1 drops
   the column, both functions and two indexes, creates nothing; no runtime SQL names a dropped object.

DB lanes (`scripts/local_pg.sh`):
8. **Idempotence:** mine the same payload twice → second call writes 0 rows, enqueues 0; a contract bump
   with unchanged values → 0 rows, provenance unchanged; a changed value → 1 UPDATE, same `id`, new
   `first_observed_at` and entry, 1 enqueue; A→B→A → ends on A.
9. Two claims of one key in one batch → one row, no "cannot affect row a second time".
10. **Readings under A:** a null slot keeps the stored value; an empty block writes nothing; stamps still
    move.
11. **Operator beats portal:** portal street X + operator street Y → the resolver reads Y; a portal
    re-mine updates X beneath it; deleting the operator row serves X again; operator A→B→A writes and
    enqueues each time; the same value twice writes nothing.
12. **Retract:** deletes the values the version first stated (including ones a later version
    re-confirmed), enqueues those listings, survives a concurrent upsert (ctid moved).
13. **Activation of an older version:** claims first stated by the newer version stay readable until a
    re-mine changes them (no version filter).
14. Tombstone end to end: a marker claim, then a page without the marker → claim deleted, listing
    enqueued, resolved unblurred.
15. Payload revert: A→B→A body → `contract_version` NULL → re-mined.
16. EXPLAIN rail: the read over a 250-listing slice uses `location_claims_key` and costs no more than
    today's 11 ms / 1.2 k buffers.

---

## 7. Acceptance

| Measure | Before | After |
|---|---|---|
| `location_claims` rows | 14.82 M | **≈ 5.98 M** right after Step 2 (± 1 %; the corpus grows ≈ 50 k claims a day) |
| duplicate keys | 45.9 k keys per 1 % sample have > 1 row | **0** (`location_claims_key` valid) |
| sreality multi-pin ads whose position is the portal pin, serving the portal's CURRENT pin | ≈ 37 % (S25sr: 59 of 161 pin-placed serve the newest; 102 the oldest) | **≥ 99 %** after Step 5 (the plan's wording "of sreality multi-pin ads" excludes the 28 % on a register point) |
| all portals: multi-pin ads serving the oldest pin | 1,793 of 2,289 (inv-B) | 0 where the pin places the row |
| sreality + mmreality rows read blurred although the portal now declares precise | 234 (10-30), ≈ 250 today | **0** |
| towns / PSČ / street-less listings that follow the newest value | 104 / 41 / 40 | all |
| writers of `location_claims` | 2 statements + 3 DELETE paths | 1 statement + `contracts.retract` |
| tie rules / supersession paths | 7 / 4 | 0 / 2 |
| intake write mean / resolver read mean (pg_stat_statements, 7 days after) | 0.26 s / 128 ms | ≤ both |
| index bytes | 2,671 MB | ≈ 0.6 GB after Step 7 |
| W1 town cell | as before | no redder (A never deletes a town) |

---

## 8. Risks and rulings

**Rulings needed**
- **R1 (Q4, known).** Recommend **A** (§1.4): half the 630/397 are empty readings A keeps correctly and C
  would wipe; on genuine re-judgements the sample is 5 to 4; C needs a new payload-substrate stamp.
- **R2 (new). Empty location block = failed reading** in the text lane (§2.4): 1,915 stored readings
  re-marked and re-read; heals ≈ 300 towns and ≈ 200 streets. Text-lane territory (llm-pipelines);
  ship with W3 or as its own ticket before it.
- **R3 (new, design). Tombstone instead of `absent_label`** for the three markers (§2.5). The critic
  accepted a label; the tombstone needs no reader to learn a sentinel and adds no rows. Either needs the
  three contract bumps.
- **R4 (new). Retract semantics under first-stater provenance:** retracting version N removes every
  value N first stated, including values later versions re-confirmed; those types are empty until a
  full re-mine. The rule becomes "retract and activation are each followed by one full re-mine of that
  source" (today's bump hand step, now also for retract). Optional automation: W2's sweep trigger could
  include the active-contract string (`_ACTIVE_VERSIONS_SQL`), which also closes W2's own "not covered:
  an activation that drops a claim type" (w2 design line 271).
- **R5. Backfill of the 625/394 already-deleted reading values:** recommend none (§1.4).
- **R6. `claim_confidence`:** only the operator writes it ('exact'); only the inspector shows it
  (`toolkit/location_quality.py:115`, `frontend/src/lib/locationQuality.ts:52`). W6 cannot drop the
  `match_confidence` enum while this column uses it. Drop it in 6NN+1 (two reader edits) or leave it to
  W6.
- **R7. Heap bloat:** `VACUUM FULL` in a quiet window (≈ 2 to 4 min ACCESS EXCLUSIVE; resolver and
  intake stall) reclaims ≈ 2.1 GB; or install pg_repack; or accept the dead space. REINDEX CONCURRENTLY
  is online and recommended regardless.

**Risks**
- **Purge keeper guesses wrong on reverts:** keeping the newest row is wrong for the ≈ 1.4 % A→B→A keys
  until Step 5 re-mines them.
- **Provenance of survivors** is the version active at purge time, so a later retract of exactly that
  version would delete most of that portal's claims (R4's re-mine restores them).
- **Old code after 6NN, before the deploy:** batches with a changed value fail and retry (no loss);
  keep 6NN → merge → deploy back to back.
- **Shadow gate:** ≈ 52 % of movers sit in the live apply scope (§5).
- **idnes rewording its sentence** would, with `absent: retract`, clear every idnes disclaimer on the
  next re-mine (today it would only stop new ones). That is the honest reading of the page; the W1
  precision mix shows it.
- **`location_pin_audit_mv.claims_now`** (EXISTS a claim under an ACTIVE contract) turns false over time
  under first-stater provenance. If W1 has not already folded the matview, change it to "a claim of a
  type its active contract declares" in PR-1, or let W6 delete it as planned.
- **Indexes with unattributed scans:** `location_claims_by_method` (347 MB, 266 k scans, no code
  reader) and `location_claims_geom` (105 MB, 1 scan). Not dropped in W3; attribute through
  pg_stat_statements first.
- **Inactive listings:** their payload claims are re-mined only in full mode, so the purge decides
  their keeper (≈ 730 listings with two values); acceptable, no verdict served from them.
