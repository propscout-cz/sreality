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

## W0 measurements (production, read-only)

1. Handover gaps: for properties with two identities' spells, the gap from one's `ended_at` to the other's
   `started_at` and to the property's `last_seen_at` — sets M.
2. Gone lag: last sighting → `is_active = false` flip per portal (rule 3's 12–24 h dwell), so L reflects the exit.
3. Revivals within M of an `exited` spell's end, per portal.
4. Followed share per portal and N: `tom_observed`, S ≥ E0, window closed.
5. `unknown` share per portal and N.
6. Broker coverage per portal: active ads with `broker_identity_id`, the coverage line's numbers.
7. Brokers with n ≥ `MIN_N` per N, and how often two identities of one broker share a property.
8. Book cost: backfill time and rows, the scoped upsert per drain slice, the sweep batch's added time (2-min ceiling).
9. Drift: view rows the table lacks, and table rows the view lacks, after one daily sweep (expected 0).

## Open for W2

- A broker with two identities on one property has two spells; W2 decides whether speed counts the property once
  per broker, and spells it once if so.

## Out of scope

- `discovered_at` as the start of time on market (an app-wide program, not this sprint).
- Telling a sale from a withdrawal: `exited` means left the market.
- Exits before 2026-09-08, and any autodedup-scope gate (ruling 1).
- The 7-day liveness rule and the count book's geo chips (deleted, not ported).
- New broker attribution sources: portals without broker data get the coverage line.
- Broker contact visibility (A6, ADR-0002 as amended 2026-10-29).
