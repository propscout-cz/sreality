# Broker board

**North star.** A broker board is the Browse cohort counted by broker: one filter definition (the registry, compiled at
`BROWSE_GRAIN`), one property × broker book kept by rule 20's property pass, one attribution rule (MS19's portal rule,
rule 16), one speed measure ("left the market within N days", ranked by its Wilson lower bound). Everything it
replaces is deleted: the leaderboard RPC and its count book (`broker_region_type_stats`, its 7-day liveness rule,
its own geo chips) go once the board reads the book.

## Operator rulings, 2026-10-29

1. **Speed answers everywhere.** No autodedup-scope gate: dedup is rolling out everywhere, and a spell is per property,
   so a later merge only re-points it (the pass recomputes both sides).
2. **Every signed-in user, and their agents, may see speed** (ADR-0002 parity). Broker contacts stay as today (A6).
3. **The board is a `Makléři` tab in Browse**, over Browse's own cohort, not a separate page with its own filters.
4. **Broker numbers use Browse's vocabulary:** property grain, the property's status, no 7-day rule.

## Definitions

**Spell** (`property_broker_spells`, migration 691): one row per (property, broker identity), stored from its one
view `property_broker_spell_projection` over the identity's ads on the property:

| column | value |
| --- | --- |
| `started_at` | `min(first_seen_at)`: the start `measure_tom_days` uses (617). `first_seen_at` is the drain's first WRITE (444); `discovered_at`, the walk's sighting, is out of scope (an app-wide program). |
| `ended_at` | NULL while any of the identity's ads on the property is active, else `max(last_seen_at)` |
| `tom_observed` | `measure_tom_observed` (625) of the identity's first ad in `browse_projection`'s order (category first, `first_seen_at`, id), so it means what the property's flag means |

Kept by `scripts/recompute_property_stats.py`: `properties_changed` (dirty drain, merge, detach, split, birth) and
each daily-sweep batch upsert the view's rows for their properties (a row is rewritten only when it differs) and
delete the spells the view no longer holds. Dirty sources: every existing rule-20 writer, plus the attribution link
(`toolkit/broker_sources.py`), which enqueues a re-attributed advert's property in the same statement. The book is
identity-keyed, so a broker merge (it re-points `broker_identities.broker_id`) changes no row. Service role only.

**Book by broker** (`broker_book`, same migration): the spells folded to (property, canonical broker), what every
reader reads. `is_active` / `all_sources` / `active_sources` are `portal_status_matches`' inputs at the broker's grain
(a broker's identities are one per portal, so the sources are the portals the broker advertises the property on);
`started_at`, `ended_at` and `tom_observed` fold the broker's spells as a spell folds its ads (the earliest spell's
flag). A property therefore counts once per broker in volume and in speed. An identity not yet resolved to a broker
has no row, so its property reads unattributed.

**Volume** = the distinct cohort properties with an ad of the broker in the property's status (MS19: under active,
an active ad; otherwise any ad).

**Speed** = among the broker's FOLLOWED spells in the cohort, the share `exited` within N, N ∈ {14, 30, 60, 90}:
`measure_exit_within(tom_observed, started_at, ended_at, property.is_active, property.last_seen_at, N)` (Python face
`toolkit.measures.exit_within_sql`), with E0 = 2026-09-08 (rule 3's structural end gate: earlier exits are not
trustworthy) and M = 7 days (the follow-up / handover margin, recalibrated by W0):

| answer | when |
| --- | --- |
| NULL (not followed) | not `tom_observed`, or S < E0, or S > now() − (N + M), or the property's status / last sighting unknown |
| `exited` | E ≤ S + N, the property is inactive, and L ≤ E + M (no other ad ran on more than M past the spell) |
| `unknown` | the property is flagged active but L < S + N (a zombie, or a gone verdict pending) |
| `not_exited` | otherwise: on the market at day N, or handed over |

- n = the broker's spells answered `exited` or `not_exited` (followed spells less `unknown`); p = `exited` / n.
- Ranked by the Wilson lower bound, z = 1.96: (p + z²/2n − z·√(p(1−p)/n + z²/4n²)) / (1 + z²/n).
- Shown when n ≥ `MIN_N` = 5 (so n is never 0); `unknown` is shown as its own count, never in n.
- Label: **„Opustilo trh do N dní (prodáno nebo staženo, ne nutně prodej)"**. No portal says sold.
- A coverage line names the cohort's portals that publish no broker (their properties count toward nothing).
- Indexless portals (`web`) are excluded from speed through the `Portal` seam (`indexless`): with no walk, an ad's
  end is not observed.

## Waves

- **W0 (next, read-only)** — measure production, recalibrate M; nothing ships to users.
- **W1 (this wave, dark)** — the book (view, table, backfill), the measure, the rule-20 pass and dirty source, the
  census rails. No reader.
- **W2** — readers: the `Makléři` tab (volume, speed, coverage), the agents' tool, the broker page; delete the
  leaderboard RPC and its SPA path.
- **W3** — delete the count book: `broker_region_type_stats`, its hourly publish in the broker-maintenance lane, its
  registry row, `broker_geo_options`.

## W0 measurements (production, read-only, 2026-10-29)

| measure | result | consequence |
| --- | --- | --- |
| start lag `first_seen_at − discovered_at`, ads since 2026-09-08 | p99 ≤ 2.4 h on every broker portal (bazos 23 h, no broker) | `first_seen_at` is a sound start |
| active ads unseen 7+ days | 0 on every portal | `unknown` is rare; the 7-day rule is a no-op today |
| gone lag `inactive_at − last_seen_at`, delisted since 2026-10-09 | p50 31–39 h, p99 45–65 h; ceskereality / realitymix 1 % tails 28–58 d | M = 7 d covers p99; tails read `unknown` |
| properties with 2+ canonical brokers | 7.4 % national, 8.8 % byt/prodej, 31.6 % Praha byt/prodej (4,620 of 4,659 across portals, 73 one name) | real co-listing (non-exclusive mandates): every broker present at the exit is credited |
| look-alike successor within 14 d (≈100 m, disposition, area ±3 %, price ±15 %) after a byt/prodej exit | Praha 11.2 %, elsewhere 33.2 % (upper bound) | outside the autodedup scope up to a third of exits may be re-posts until dedup covers the area (ruling 1) |
| sreality `state_cb` Prodáno / Rezervováno on recent delistings | 266 of 22,881 (1.2 %) | too sparse to validate the exit proxy |
| book size | ~725k (property, identity) pairs; Praha byt/prodej 32k over 14.7k properties | backfill in one statement |

Still owed after the apply: the board query's cold and warm EXPLAIN on production, the backfill's time, and
the book against today's broker scope (`broker_property_ids`) per broker × status × portal (expected: equal).

## Out of scope

- `discovered_at` as the start of time on market (an app-wide program, not this sprint).
- Telling a sale from a withdrawal: `exited` means left the market.
- Exits before 2026-09-08, and any autodedup-scope gate (ruling 1).
- The 7-day liveness rule and the count book's geo chips (deleted, not ported).
- New broker attribution sources: portals without broker data get the coverage line.
- Broker contact visibility (A6, ADR-0002 as amended 2026-10-29).
