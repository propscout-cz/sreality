# Broker board

**North star.** A broker board is the Browse cohort counted by broker: one filter definition (the registry, compiled at
`BROWSE_GRAIN`), one property × broker book kept by rule 20's property pass, one attribution rule (MS19's portal rule,
rule 16), one speed measure ("left the market within N days", ranked by its Wilson lower bound). Everything it
replaces is deleted: the leaderboard RPC and its count book (`broker_region_type_stats`, its 7-day liveness rule,
its own geo chips) go, and every broker number is the book's.

## Operator rulings, 2026-10-29

1. **Speed answers everywhere.** No autodedup-scope gate: dedup is rolling out everywhere, and a spell is per property,
   so a later merge only re-points it (the pass recomputes both sides).
2. **Every signed-in user, and their agents, may see speed** (ADR-0002 parity). Broker contacts stay as today (A6).
3. **The board is a `Makléři` tab in Browse**, over Browse's own cohort, not a separate page with its own filters.
4. **Broker numbers use Browse's vocabulary:** property grain, the property's status, no 7-day rule.

## Definitions

**The book** (`broker_book`, migration 692): one row, the broker's SPELL, per (property, canonical broker), stored from
its one view `broker_book_projection` over the ads of the broker's identities on the property (one identity per portal):

| column | value |
| --- | --- |
| `is_active`, `all_sources` / `active_sources` | `portal_status_matches`' inputs at the broker's grain (MS19): any ad active; the portals the broker advertises the property on / those with an ad still running |
| `started_at` | `min(first_seen_at)`: the start `measure_tom_days` uses (617); `first_seen_at` is the drain's first WRITE (444) |
| `ended_at` | NULL while any of the broker's ads on the property is active, else `max(last_seen_at)` |
| `tom_observed` | `measure_tom_observed` (625) of the broker's first ad in `browse_projection`'s order (category first, `first_seen_at`, id) |

A property counts once per broker in volume and in speed; an identity not yet resolved to an active broker has no
row (its property reads unattributed). Service role only.

**Kept by** rule 20's property pass (`properties_changed`, each daily-sweep batch). Dirty sources beyond rule 20's
writers: the attribution link (same statement) and every re-point of `broker_identities.broker_id`
(`broker_sources.dirty_repointed`, its comment holds the lock and its one uncovered race).

**Why the fold is at write time.** 691 folded identities to brokers in a view, so every board read re-folded the book
(production, 2026-10-29): Praha byt/prodej 5.3 s cold (0.41 s with an `ANY` pushdown), national byt/prodej 2.05 s,
national every category 5.95 s warm, past the 8 s bound cold; a plain join to a stored table that size costs ~2 s.

**Volume** = the distinct cohort properties with an ad of the broker in the property's status (MS19).

**Speed** = among the broker's FOLLOWED spells in the cohort, the share `exited` within N ∈ {14, 30, 60, 90}:
`measure_exit_within(tom_observed, started_at, ended_at, property.is_active, property.last_seen_at, N)` (Python face
`toolkit.measures.exit_within_sql`), E0 = 2026-09-08 (rule 3's structural end gate) and M = 7 days (follow-up /
handover margin, W0):

| answer | when |
| --- | --- |
| NULL (not followed) | not `tom_observed`, or S < E0, or S > now() − (N + M), or the property's status / last sighting unknown |
| `exited` | E ≤ S + N, the property is inactive, and L ≤ E + M (no other ad ran on more than M past the spell) |
| `unknown` | the property is flagged active but L < S + N (a zombie, or a gone verdict pending) |
| `not_exited` | otherwise: on the market at day N, or handed over |

n = `exited` + `not_exited`; share = `exited` / n, shown from n ≥ `MIN_N` (5); rank = its Wilson lower bound
(z = 1.96, `toolkit.measures.wilson_lower`); `unknown` is its own count, never in n. Label: **„Opustilo trh do N dní
(prodáno nebo staženo, ne nutně prodej)"** (no portal says sold). An indexless portal's spell (`web`, the `Portal`
seam) answers no speed: its end is never observed. A coverage line names the cohort's portals that publish no broker.

## Waves

- **W0 (done, read-only)** — production measurements, M recalibrated (below).
- **W1 (#2181, dark)** — 691: the identity-grain book, the measure, the pass, the link's dirty source, census rails.
- **W2** — 692 (the book at broker grain, the re-point dirty source) and its readers: the `Makléři` tab, `rank_brokers`,
  Browse's broker scope, Outreach; the leaderboard's routes and toolkit deleted.
- **W3a (#2183, merged 2026-10-30)** — every other broker number read from the book; the count writers deleted. No
  migration: the count book, its hourly publish and its registry row stay, unread, as one unit.
- **W3b** (destructive: the operator's OK and a backup first) — that unit, the count columns, W2's leftovers:
  migration 693, prepared (below).

## W2 as built

**One read, `api/broker_board.board`**, behind `POST /brokers/board` (contacts through `apply_pii_policy`), the
agents' `rank_brokers` (≤ 50 rows, no contact, a contact-shaped name redacted) and Outreach's `select_targets` (the
top 2000 by live properties, then the reachable, unsuppressed and undrafted; the count `metric` is gone: 0 campaigns
in production, a stored one fails closed). `BoardArgs` is the BROWSE filters but `location` and the Curation group
(judged under the reader's RLS, which the service connection has not: refused, never dropped) plus the board's own
options; both languages derive the keys from the registry and are tested against it. `firm_ids` and `min_properties`
narrow only the rows, never the market benchmark. Speed is refused (`outcome_selected`) when an `OUTCOME` filter is
set (a census holds every board filter classified), else (`mixed_category`) when the cohort holds two property or
deal types; `speed_refused_by` names the filters to change.

**The SPA.** `tab=brokers`, gated on the plan's `brokers` agenda, over Browse's sidebar, URL, presets and pills, and
the phone's Makléři screen (its filters in `FilterSheetBody`); the tab's options sit in the URL outside preset
identity. A value under a registry floor is held at it, so Browse and the board judge one cohort. Not Browse's
cohort, and said on the tab: the account lenses and market growth are not applied; dismissed properties count.

## W3a as built

**A broker's own numbers are its book, `toolkit/brokers.BROKER_COUNTS_SQL`**: `properties` = the book's rows of the
broker, `active_properties` = those with one of its ads still running (a live ad makes its property live). The board
reads the same book inside a Browse cohort. Read by name search (its order), the dossier (`GET /brokers/{id}`, plus
`footprint` over the book's properties Browse serves: by region, `admin_boundaries` on `browse_list.region_id`, a
property without one or not yet published counted only in the totals, and by category and deal type), the merge review
and Outreach's draft facts. Not joined to `browse_list` for the totals: name search for `ov` (7,200 matches) took 2.7 s
that way on production and 163 ms on the book alone (2026-10-29); 0.18 % of live book rows are not yet in Browse. A firm chip counts the active
brokers whose primary firm it is: the brokers the board's firm filter selects.

**Deleted**: the identity, broker and firm count rollups with their three copies of the 7-day rule; the property pass's
mirror into `dirty_broker_listings` (the queue stays: attribution, names, contacts, primary firm, memberships); the
SPA's ad counts (both `Inzeráty` tiles, each firm's ad count, the phone's per-type ad tally and its listings read, the
inventory's `z N` ad total; a full inventory page reads `500+`). The firm label vote is one vote per idnes identity, no
longer ad-weighted. Each read is bounded as the board's (READ ONLY, 8 s): `tenant_pool.read_conn` on the routes,
`bound_read` in Outreach.

**Numbers move** (ruling 4): property grain, published and served only, no 7-day rule, a co-listed property counted for
each broker, and foreign properties counted (Browse serves the determined foreign; 396's CZ scope is gone, so an idnes
syndication feed can head a name search, as it can the board). The firm label vote (one vote per idnes identity, no
longer ad-weighted) changes 8 of 2,945 voted firm names on production (6 gain one, 2 differ; 27 lose a 60 % majority and
keep their name).

## W3b as prepared

**Migration 693** (`cleanup/broker-count-book-drop`), applied once W3a's rollout is live on the API and the worker (it
is: #2183 merged 2026-10-30, Railway green 05:01–05:03Z), with the operator's OK and the backup below, and before W3b
merges; nothing W3a deploys reads any of it (its header cites each reader):

1. the `derived_artifacts` row `broker_region_type_stats`, alone and first: the deployed refresh reads the row before
   it refreshes, so it stops before anything it refreshes goes;
2. in one transaction: `broker_geo_options`, `broker_leaderboard(...)` and `district_level_codes(...)` (609's
   signatures), `brokers_public` dropped and re-created without its eight count columns and the matview join (580's
   body otherwise, `anon` and `authenticated` revoked), then the matview `broker_region_type_stats`;
3. in one transaction: `broker_firm_memberships_public` re-created without `listing_count` (187's body otherwise, the
   same revoke), `firms_public` dropped (no reader);
4. the columns `brokers.{listing_count, property_count, active_listing_count, active_property_count}`,
   `firms.{broker_count, listing_count, active_listing_count, stats_computed_at}`,
   `broker_identities.{listing_count, active_listing_count}`, `broker_firm_memberships.listing_count`, one table per
   statement (`brokers.stats_computed_at` stays: the broker lane writes it);
5. 691's `property_broker_spell_projection` and `property_broker_spells` (its index named, so the drift probe retires it);
6. a post-check: every dropped object gone, the two views exactly their columns, neither readable by `anon` or
   `authenticated`.

Backup first (the matview is derived and rebuildable from 580's text, so it gets no dump):
`gh workflow run backup_before_drop.yml -f label=broker-count-book -f targets="brokers:id,listing_count,property_count,active_listing_count,active_property_count firms:id,broker_count,listing_count,active_listing_count,stats_computed_at broker_identities:id,listing_count,active_listing_count broker_firm_memberships:id,listing_count property_broker_spells"`.

Deleted with it: the four `tests/test_broker_leaderboard_*.py` and `migrations.yml`'s leaderboard step, the broker
index pin in `tests/test_matview_indexes.py`, the refresh (`scripts/resolve_brokers._maybe_refresh_matview`, the
broker lane's `refreshed`), the A6 lists' entries for the dropped relations and the leaderboard's EXECUTE probe
(`test_migration_rls_grants.py` with its `firms_public` test, `test_tenant_isolation_live.py`) and the spells'
`NOT_CARRIED` entry.

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

After 692 (2026-10-29): the board took 277 ms for Praha byt/prodej, 865 ms national byt/prodej, 3.18 s national all
categories; 18 of 608,988 rows drifted between the apply and the rollout, left to the 04:15 sweep (a dispatched sweep
holds the maintenance lease for an hour). Still owed: a daily auto-merge's extra dirty load.

## Out of scope

- `discovered_at` as the start of time on market (an app-wide program, not this sprint).
- Telling a sale from a withdrawal: `exited` means left the market.
- New broker attribution sources: portals without broker data get the coverage line.
- Broker contact visibility (A6, ADR-0002 as amended 2026-10-29).
