# W3: one current claim per fact (design 2026-10-31; as built 2026-10-31)

Wave W3 of the location data quality programme (`PROGRAM.md`; rulings Q4, Q5, Q13; order Q14:
W0 → W1 → W2 → W7 → W8 → W4 → **W3** → W3b → W5 → W5b → W6). Ticket #2223. Built on branch
`feature/location-w3-one-claim-per-fact` (PR-1: migration 699, `resolver:v5.9.1`; it carries W4's
#2220 and merges after it); PR-2 is migration 700 and the purge's deletion. §§ 1–2 are the design as
reviewed, with "As built" notes where the build differs or adds; §§ 3–6 and 8 are the as-built record.
Every production number was read on 2026-10-31 between 13:10Z and 14:30Z through the Supabase MCP,
SELECT only (code read at `main` bddea095d). `location_claims` was never scanned unfiltered except once
for a licence-class count (one aggregate row). Where a number is a sample estimate, the sample and the
scale factor are given.

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
  `operator_corrections` call the same function, `claims_store.write_claims(cur, claims, reason)`,
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

**As built (§ 2.1).** `location_data/claims_store.py` holds the one write and nothing else (so the
operator's API route does not import the intake lane): `_WRITE_CLAIMS_SQL` + `write_claims(cur, claims, reason)`, chunked
by listing (a key never spans two statements) and returning (changed, enqueued). The input is read
`jsonb_array_elements(...) WITH ORDINALITY` + `jsonb_to_record`, so `one` breaks a tie in
`first_observed_at` by the later row of the batch, never by chance. The recordset also
carries `claim_confidence` (ruling R6: 'exact' for the operator, NULL else), set with the value. The
readings half calls the same write and then stamps its readings in the same transaction (one statement
less than before, no DELETE). `Claim.retract` lives on the intake claim (`claims_common.Claim`, what a
reader emits); the resolver's stored-row `Claim` never sees a tombstone. `Claim.to_row()` is exactly the
recordset. `operator_corrections` builds a `Claim` and calls `write_claims(..., reason='operator_edit')`;
its API result says `written` (the `restatement` flag and `inserted` went; the Location Quality page
reads `written`). The payload revert arm: `contract_version = CASE WHEN EXISTS (a newer-observed 2xx
body of the key) THEN NULL ELSE … END` in `_APPEND_SQL`'s ON CONFLICT.

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

**As built (§ 2.2).** The licence rail is built from `resolve_db.ADMISSIBLE_LICENCE_CLASSES` (the tuple
the wave shadow reads too; a test holds the SQL text to it): `c.licence_class IN ('portal', 'operator')
AND (c.licence_class = 'operator' OR EXISTS (… declared …))`. The order within a (listing, type) is the
operator first, then `first_observed_at DESC, id DESC` — the newest observation, since ids no longer
order by recency once a changed value is an UPDATE in place (deterministic should a key ever hold two
rows).

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
| `location_claim_fingerprint(23 args)` (mig 386), `location_value_norm(text)` (mig 382), column `claim_fingerprint`, index `location_claims_fingerprint` (1,098 MB) | migration 700 (§4); prod has no other caller of either function |
| the test that guards it | `tests/location_data/test_claims_intake_fingerprint.py` (189), `test_claims_slim_migration.py:140-151` |

Follow-up cleanup (not counted below, own commit): the hash-only `Claim` inputs nobody stores
(`travel_mode`, `target_text`, `distance_m`, `declared_confidence`, `legacy_source_column`) are threaded
through about 30 reader sites and the 23 golden fixtures in `tests/fixtures/location_w2/golden/`.

**As built (§ 2.3).** What replaced each rule: 1–3 `collect_constraints` reads each type once (a dict of
the one claim per type); the one rule left between two TYPES is that a house-number claim outranks the
number written in the street line. 4 `bind.the_pin` (THE coordinate with a position); `elect_pin`,
`declared_rank` and the `pin_claim_id` threading are gone. 5 `read_declared_precision(claims)`: the
precision declaration's label, else the coordinate's own; either stating blur blurs; the `blur_hint`,
`uncertainty_geometry` and `map_zoom` arms went (no contract may declare those types). 6
`composite.resolve_street(line, …, place_line=)` takes one line; a line reaching two streets still binds
nothing; `Constraints.street_line` is a string. 7 `OPERATOR_METHOD`, `OPERATOR_FIELDS`, `operator_fields`
and FILL's override are gone: the operator outranks at the read and binds through the register. The
hash-only `Claim` inputs (`distance_m`, `travel_mode`, `target_text`, `declared_confidence`,
`legacy_source_column`, and the `conflict_signal` reader's `legacy_source_column` key) went in their own
commit with the 23 goldens that carried them; `value_shape_wkt` (set by `bbox_envelope` and
`json_geometry`'s shape arm, counted by the byte guard, stored nowhere since 498) and the then-dead
`envelope_wkt` went after review, with the key from all 46 goldens.

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

**As built (§ 2.4).** The text lane's `_record` treats `text_reading.is_empty(payload)` (a `location`
block whose `ad_kind`, `country` and every slot are null) as a failure — recorded with `extracted.error`,
retried under GIVE_UP_AFTER, nothing written — ONLY on a portal whose active contract declares
`llm_text` (`description_extraction.location_sources`: the readings that feed location claims, today
bazos and web). Elsewhere the block is unread, and failing the call would throw away its attribute
facts (floor, lift) and bill five retries. The 1,915 stored ones are re-marked by Step 5's statement,
which is scoped the same way.

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

**As built (§ 2.5).** `html_marker` reads its node, then `_marks(node, …)`; a node that fails the test
yields `_page_claim(entry, row, value=None, retract=True)` when `locator.absent == 'retract'` (any
other value is refused; the key on another reader is refused by `READER_CONTRACTS`' appetite). Bumps:
bazos@10, idnes@7 (`body` always exists, so every idnes page without the sentence retracts), realitymix@9
(on W4's @8). No regression fixture carries a failing node, so the three new goldens equal their
predecessors; the per-portal contract tests show the tombstones on real bodies.

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

## 3. Deletion ledger versus additions (as built)

Measured on the branch: every W3 commit (`git log --grep '#2223' --no-merges`), W4's merged commits and
`main` excluded.

| Class | Added | Deleted | Net |
|---|---|---|---|
| Code, SQL, workflows (`location_data/`, `scripts/`, `toolkit/`, `scraper/`, `.github/`, `migrations/`, `contracts/`, `frontend/`) | +936 | −1,077 | −141 |
| Tests | +970 | −975 | −5 |
| Golden fixtures (the 23 rewritten, three new version goldens) | +417 | −1,080 | −663 |
| Docs (CLAUDE.md, architecture, skills, track, PROGRAM, wave-shadow) and `workflow-docs.json` | +124 | −128 | −4 |
| **PR-1 without this design doc** | **+2,447** | **−3,260** | **−813** |
| This design doc (new file) | +677 | 0 | +677 |

Against the plan's −1,100: the retire script, workflow and test (≈ −700) stay in PR-1, re-aimed as the
purge, and go in PR-2 with migration 700 (+≈ 20); the two follow-ups the coordinator added after the
design (the R4 sweep arm, the wave shadow's raw purge) are +326 / −107 (code +155 / −42). Where it went: `bind.py` −64,
`resolve_db.py` −69, `operator_corrections.py` −99, `claims_common.py` −57, `claims_intake.py` −39, the
fingerprint test −189, the operator NOT NULL test −53, the version-window tests −77; the one write
+85, migration 699 +106, the new tests (one write, live, purge, markers, text lane) +337.
Schema after PR-2: −1 column, −2 functions, indexes 7 → 5 (−fingerprint, −listing, −ephemeral, +key).
Rules: 7 tie rules → 0; 4 supersession paths → 2; 2 identities per row → 1.

---

## 4. Migrations and the purge (as built)

Two numbered files: **699** in PR-1, **700** in PR-2 (drafted below as text; append-only numbering, so
PR-2 adds the file). Destructive steps are pre-approved by Q13, each with an R2 backup and a receipt.
Nothing here assumes migration 697 (W2's three-reason CHECK and `dirty_locations_claim` index): every
reason W3 writes (`claim_insert`, `operator_edit`, `full_sweep`) is valid before and after it.

### Steps 0 + 1: backup and purge, one dispatch of the re-aimed `location_claims_retire.yml`
The workflow's first armed step is the backup: a streamed `\copy (select c.* from location_claims c)`
→ gzip → R2 `backups/w3-claims/<UTC date>/w3_claims_<run>_<attempt>.csv.gz` (one object per attempt,
so a resumed run never overwrites the first, fuller one). The count and the copy share one
repeatable-read snapshot, and the step fails unless the CSV parses to exactly `count(*)` records. The
design's `backup_before_drop w3-claims location_claims` was not used: `_dump_table` holds the whole
pg_dump in memory (a ≈ 4 GB text dump on a 7–8 GB private-repo runner). Then
`scripts.location_claims_retire --confirm PURGE` (LAST RUN BEFORE 699; DELETED IN PR-2):
- Keeper per (listing_id, claim_type, licence_class) = the row the old read served:
  `ORDER BY (pc.version <= act.version) DESC NULLS LAST, pc.version DESC NULLS FIRST,
  first_observed_at DESC, id DESC`. Keyset over `listings.id`, 2,000 listings a statement,
  `SET LOCAL lock_timeout '5s'` + `statement_timeout '120s'`, 0.5 s pause, a lost lock retried 5 times,
  resumable from the logged cursor (`--after-listing-id`). The dry run executes the same statement
  with the delete disarmed (`AND %(armed)s`).
- The safety check counts KEYS: after each batch's delete, the distinct keys left in its listing
  range must be ≥ the keys it kept, else the batch rolls back and the run exits 3. A key whose only
  rows sit under a retired version keeps one (the ≈ 15.7 k, logged as `kept_retired_version`).
- Receipt: every listing that lost a row of its keeper's own version (the rows the old window read
  beside the keeper, ≈ 2.8 k), gzipped CSV to R2 beside the backup, for Step 5.

```
gh workflow run location_claims_retire.yml --ref feature/location-w3-one-claim-per-fact -f dry_run=true
gh workflow run location_claims_retire.yml --ref feature/location-w3-one-claim-per-fact \
  -f dry_run=false -f confirm=PURGE -f listings_per_batch=2000 -f after_listing_id=0 -f max_seconds=20000
```
A budget-exhausted run exits 2 and logs `resume with --after-listing-id N`: re-dispatch with
`-f after_listing_id=N`. Expect ≈ 542 batches, 30–45 min; summary line
`PURGE summary armed=True complete=True … deleted≈8.8M kept_retired_version≈15.7k collapsed_listings≈2.8k`.

### Step 2: migration 699 (after the purge, before the merge)
`migrations/699_location_w3_one_claim_per_fact.sql`, statement autocommit (as 498, 653):
1. `alter table location_claims alter column claim_fingerprint drop not null` (the new writer omits it).
2. A guard, no lock: refuse when > 50,000 keys still hold several rows (an unpurged table).
3. `begin; set local lock_timeout '10s'; lock table location_claims in share row exclusive mode;` the
   straggler dedup (rows the old writer added after the purge passed them; keeper the newest
   observation, then id); `create unique index if not exists location_claims_key (listing_id,
   claim_type, licence_class)`; `commit;` — ≈ 1–2 min, readers run, intake batches wait and retry.
4. `drop index concurrently if exists location_claims_listing`.
5. A `$post$` receipt: the key valid and unique, the old index gone, the column nullable.

```
gh workflow run apply_migration.yml --ref feature/location-w3-one-claim-per-fact \
  -f file=699_location_w3_one_claim_per_fact.sql -f dry_run=false -f confirm=APPLY
psql "$SUPABASE_DB_URL" -c "select indisvalid, indisunique from pg_index where indexrelid = 'location_claims_key'::regclass"
psql "$SUPABASE_DB_URL" -c "select to_regclass('location_claims_listing'), count(*) from location_claims"
```
Never through the MCP (DROP INDEX CONCURRENTLY cannot run in a transaction block). Between this apply
and the deploy the old writer's `ON CONFLICT (claim_fingerprint) DO NOTHING` raises unique_violation on a
changed value: its batch rolls back and retries next tick, nothing is lost. CI replays 699 and re-runs
it (retry contract).

**Rollback after 699.** The purge is undone only from the R2 backup. A CODE revert (the PR reverted after
699 applied) also needs the key gone, because the old writer's `ON CONFLICT (claim_fingerprint) DO
NOTHING` raises unique_violation on `location_claims_key` for every changed value — and the old read
needs an index on `listing_id` back first:
```
create index concurrently if not exists location_claims_listing
  on location_claims (listing_id, claim_type, first_observed_at desc);
drop index concurrently if exists location_claims_key;
```
`claim_fingerprint` stays nullable (the old writer fills it; nothing needs NOT NULL). After 700 (PR-2) a
code revert is not possible without restoring the column from the backup.

### Step 3: merge PR-1 (after W4's #2220), Railway rolls out worker + API
`gh api repos/propscout-cz/sreality/commits/<sha>/status` until both services report success.

### Step 4: re-resolution
`RESOLVER_VERSION` = `resolver:v5.9.1`: the worker's reconcile (W2) sweeps the corpus on boot, active
first. The three contract bumps declare the same claim types as their predecessors, so the R4 arm
(§ 8) sweeps no portal on their account.

### Step 5: re-mine and re-read (by hand, right after the deploy, before the next :35 intake run)
- The receipt's listings: un-stamp their latest bodies and readings so they re-mine
  (`:ids` = the receipt's ids):
  `update portal_raw_payloads p set contract_version = null from listings l where l.id = any(:ids) and
  p.source = l.source and p.source_id_native = l.source_id_native and p.page_kind = 'detail';`
  `update listing_description_enrichments set mined_contract_version = null where listing_id = any(:ids);`
  The first intake run on the new code re-walks every body anyway (the bump moves the version set, so
  the bodies cursor restarts at 0), so the un-stamped bodies ride it. Then the payload half once:
  `gh workflow run location_claims_intake.yml --ref main -f mode=full -f source=sreality`.
- The 1,915 empty readings (ruling R2), re-marked failed so the text lane re-reads them:
  `update listing_description_enrichments e set extracted = e.extracted || jsonb_build_object('error',
  'empty location block: ad_kind and every slot null', 'attempts', 1) from listings l where l.id =
  e.listing_id and l.source in ('bazos', 'web') and e.extracted ? 'location'
  and e.extracted -> 'error' is null and jsonb_typeof(e.extracted -> 'location') = 'object' and not
  exists (select 1 from jsonb_each(e.extracted -> 'location') cell where coalesce(cell.value ->> 'value',
  '') <> '');` — expect ≈ 1,915 rows. Run it after the deploy: the old text lane would re-store an empty
  answer as a reading.

### Step 6: migration 700 (PR-2, after one green intake tick on the new code)
```sql
-- 700_location_w3_drop_fingerprint.sql (PR-2; destructive, covered by the Step 1 backup)
set lock_timeout = '5s';
drop index concurrently if exists location_claims_fingerprint;   -- 1,098 MB
drop index concurrently if exists location_claims_ephemeral;     -- 0 rows; the class is never read
alter table location_claims drop column if exists claim_fingerprint;
drop function if exists location_claim_fingerprint(bigint, text, text, text, text, text, text, text,
  text, bigint, text, text, numeric, geometry, geometry, jsonb, integer, text, text, text, text,
  numeric, text);
drop function if exists location_value_norm(text);
-- $post$: the column, both indexes and both functions are gone.
```
PR-2 also deletes `what_if.purge`, `what_if._RAW_CLAIMS_SQL`, `what_if.keeper_rank` and the
`--claims purge` choice (the shadow's second spelling of the keeper rule: it dies with the purge),
`scripts/location_claims_retire.py`, `location_claims_retire.yml` (and its
`GROUP_WORKFLOWS` entry, the workflow-docs entry, `test_workflow_artifacts`' backup test, the
`test_location_batch_hardening` group list entry, the migrations.yml lane entry) and
`tests/location_data/test_claims_retire.py`; `tests/location_data/test_claims_slim_migration.py`'s
KEPT_COLUMNS stays (it pins 498).

### Step 7: space (maintenance, outside the migration files; ruling R7)
`REINDEX INDEX CONCURRENTLY` on `location_claims_pkey`, `_by_method`, `_contract_entry`, `_geom` (online):
index total 2,671 MB → ≈ 0.6 GB. No VACUUM FULL: the ≈ 2.1 GB of heap free space stays (the upsert
inserts ≈ 50 k rows a day).

---

## 5. Rollout order (as built)

1. **Prerequisites:** W2 live (#2216 merged; its reconcile sweeps v5.9.1). W4 (#2220) merged first:
   realitymix@9 is built on its @8 and this branch carries #2220's commits.
2. **Standing autodedup shadow gate, before Step 2** (PROGRAM.md; ≈ 52 % of movers sit in the live
   apply scope). From this branch, before the purge: `location_wave_shadow.yml` with
   `--claims purge` (W3's keeper over a raw read, then the one read, `what_if.purge`) over the movers'
   ids (the active listings with ≥ 2 rows of one type at the effective version, plus the
   precision-declaration and marker cohorts), then `autodedup.shadow` + `harness shadow`; pass = no
   ruled pair moved against its ruling. The ids bound the work (the shadow finds the movers itself):
   ```
   gh workflow run location_wave_shadow.yml --ref feature/location-w3-one-claim-per-fact \
     -f wave=w3-one-claim -f claims=purge -f scope=apply -f ids_sql="SELECT c.listing_id FROM
     location_claims c JOIN listings l ON l.id = c.listing_id AND l.is_active GROUP BY c.listing_id,
     c.claim_type, c.licence_class HAVING count(DISTINCT (c.value_text, c.value_num,
     ST_AsText(c.value_geom), c.value_jsonb::text, c.declared_precision_label, c.blur_evidence)) > 1"
   ```
   (active listings holding two values for one key; one aggregate over the table, read-only.)
3. Steps 0 + 1 (backup + purge, dry run first), Step 2 (699), Step 3 (merge + deploy), Step 4 (automatic),
   Step 5 (hand: re-stamp + payload re-mine + readings re-mark).
4. PR-2 (Step 6) after one green intake tick; then Step 7.
5. **W3b after W3** (the address picker writes operator claims through `write_claims`; outranking is the
   read's ORDER BY). W5 after W3 (bind.py overlap).

---

## 6. Tests (as built)

Offline: `test_claims_one_write.py` (the key, the eight compared value columns, provenance only in the
SET, the tombstone's `gone`, enqueue only on change); `test_claims_slim_migration.py` (one writer,
`operator_corrections` holds no INSERT); `test_resolver_admissibility.py` (the one read's text, a fake
loader obeying it); `test_page_reader_canon.py` + `test_claims_intake_contracts.py` (`absent: retract`,
its refusals); the contract tests on real bodies (bazos, idnes, realitymix tombstones; W4's regression
kept); `test_resolver_jobs.py` (the R4 reconcile arm, the portal sweep); `test_what_if.py` (raw purge,
keeper order, write-then-read); `test_text_reading.py` + `test_description_extraction.py` (an empty
block is a failure); `test_claims_retire.py` offline pins (keeper, keyset, KEYS check).
DB lanes (`scripts/local_pg.sh test`, CI's migrations job): `test_claims_one_write_live.py` (write on
change, A→B→A, two of one key in a batch, operator outranks while the portal updates beneath, the
tombstone, a retract of a first stater, an inactive version's claim still read, the key serves the
read); `test_claims_retire.py` (the purge over a pre-699 table: every keeper case, the receipt, a key-loss
rollback, the shadow's purge equal to the real one); `test_readings_half_live.py` (ruling A);
`test_payloads.py` (the revert arm); `test_checks_quality_sql_live.py` (one coordinate per ad);
`test_sql_schema_prepare.py` (every new statement PREPAREs). Deleted with their rules: the fingerprint
test, the operator SQL NOT NULL test, the pin-election, blur_hint, operator-bypass and version-window
tests.

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

**Rulings (taken 2026-10-31, recorded in #2223; the operator may override)**
- **R1 (Q4) = A** (§1.4): a silent reader writes nothing; the readings DELETE is gone.
- **R2: empty location block = failed reading**, shipped with W3 (`text_reading.is_empty`; the 1,915
  stored ones re-marked in Step 5).
- **R3: tombstone, not a stored label** (`absent: retract`, §2.5; bazos@10, idnes@7, realitymix@9).
- **R4: a retract or an activation is followed by one full re-mine of that source** (the hand step,
  `claims_intake --mode full --source X`; bodies and readings re-mine by their stamps). Automation built
  as a follow-up on top of W2: `drain.reconcile` compares each active contract's DECLARED claim types
  (`_DECLARED_TYPES_SQL`), the one contract fact the read asks, beside the (resolver version, registry
  label) pair; a portal whose set moved has every listing re-resolved (`enqueue_portal_sweep`), a
  same-type bump moves nothing (its re-mine enqueues what it changes), and a portal a retract left with
  no active contract is not swept until one returns (sweeping it would blank it). This closes W2's
  "an activation that drops a claim type". Keyed on declared types rather than the version string on
  purpose: a version-keyed sweep would re-resolve a whole portal (≈ 550 k listings for sreality) on
  every bump for no answer change.
- **R5: no backfill** of the 625/394 already-deleted reading values.
- **R6: `claim_confidence` stays** for W6 (the upsert carries it: 'exact' for the operator, NULL else).
- **R7: no VACUUM FULL;** REINDEX CONCURRENTLY only (Step 7).

**Risks**
- **Purge keeper guesses wrong on reverts:** keeping the newest row is wrong for the ≈ 1.4 % A→B→A keys
  until Step 5 re-mines them (the receipt names them).
- **Provenance of survivors** is the version active at purge time, so a later retract of exactly that
  version would delete most of that portal's claims (R4's re-mine restores them).
- **Old code after 699, before the deploy:** batches with a changed value fail and retry (no loss);
  keep 699 → merge → deploy back to back. The same holds for a code rollback after 699: see § 4
  Step 2's rollback (re-create `location_claims_listing`, then drop `location_claims_key`).
- **Shadow gate:** ≈ 52 % of movers sit in the live apply scope (§5).
- **idnes rewording its sentence** would, with `absent: retract`, clear every idnes disclaimer on the
  next re-mine (today it would only stop new ones). That is the honest reading of the page; the W1
  precision mix shows it.
- **`location_pin_audit_mv.claims_now`** (EXISTS a claim under an ACTIVE contract) turns false over time
  under first-stater provenance (at once for bazos, idnes and realitymix after their bumps). As built:
  left for W6 to delete (a bullet under PROGRAM.md § W6 owns it) — the column is selected by
  `frontend/src/lib/pinAudit.ts` and rendered nowhere.
- **Indexes with unattributed scans:** `location_claims_by_method` (347 MB, 266 k scans, no code
  reader) and `location_claims_geom` (105 MB, 1 scan). Not dropped in W3; attribute through
  pg_stat_statements first.
- **Inactive listings:** their payload claims are re-mined only in full mode, so the purge decides
  their keeper (≈ 730 listings with two values); acceptable, no verdict served from them.
- **The R4 arm is process-local** (like W2's pair): a contract change made while no worker runs is not
  seen. The intake loads contracts while the worker runs, so in practice it is.
