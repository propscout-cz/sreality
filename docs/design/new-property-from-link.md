# New property from a pasted ad link — the paste box, the tenth portal, and the retirement of estimation-4

Status: **PROPOSED 2026-10-20 — awaiting the operator's rulings (§8).** Investigated read-only by six Opus
readers (the dormant URL parser, the write path and property birth, every downstream lane, the SPA and
pipeline surfaces, duplicate detection, the governing docs) and verified against the live database and
Railway; the load-bearing claims were then attacked by seven Opus skeptics and a completeness critic,
and §7 records what they changed. Sibling of `docs/design/portal-listing-url.md` (the URL cutover),
`docs/design/field-capture/PROGRAM.md` (whose rulings bound the parser) and
`docs/design/pipeline-sharing.md` (the board the card lands on).

## 1. North star

**A pasted link is an ordinary ad.** One identity rule says which ad a URL names; one fetch path (the
worker's drain) brings its page in; one write (`write_listings`) lands it; one birth (the straggler-attach)
gives it its property; and the sites we do not crawl are **the tenth portal, `web`, not an exception** —
a real `Portal` with a client, a parser, a config row, a location contract, a broker row and a chip, so
every lane that treats the nine treats it too. No second parser for a page we already parse, no second
identity for an ad we already hold, no orphan subject anywhere: the paste box on Pipeline and Browse, the
phone's Odkaz tab and the Chrome extension resolve a link the same way, and an estimation is always of an
ad we hold. When a later decision is in doubt, choose the option that keeps the pasted ad
indistinguishable from a scraped one.

## 2. What exists today (verified 2026-10-20)

**The write path needs no scraper.** `scraper/listing_write.write_listings` upserts on the UNIQUE index
`(source, source_id_native)` (migration 091) and returns `new | updated | unchanged` + `listing_id`;
`listings.source` has no CHECK, no FK to `portals`, and Gate 2 leaves `sreality_id` NULL for every
non-sreality row. New rows land `property_id` NULL; `scraper.db.NEW_SINGLETONS_SQL`
(`create_singleton_properties`) births singletons in the maintenance lane (worker every 120 s, GH cron
*/5) — median 54 s after `first_seen_at` outside the daily full sweep, 593 s (up to 975 s) while the
sweep holds the lease for 13–59 min a day. `db.enqueue_detail(source, [(native, url, None,
QUEUE_PRIORITY_NEW)])` is idempotent on `(source, native)`; the worker drains every `REALTIME_SOURCES`
portal every 30 s, one source after another with a 120 s budget each, through `portal_factory.
build_portal` → `Portal.fetch_detail` (on a per-chunk thread pool) → `PortalDefaults.write_details`
(serial, replayed whole on a transient DB error; stages `portal_raw_pages` + the payload archive, ONE
`write_listings` call). Nothing in `api/` or `toolkit/` enqueues the queue today.

**The exact-link check is already structural — on the natural key, not the URL.** `listings.source_url` is
unindexed and not unique (13 mmreality URLs sit on two rows each; 18 mmreality URLs do not even contain
their native id; sreality 301-redirects any locality slug; bezrealitky `.com` links are never stored).
`(source, source_id_native)` is what the SPA alias `/listing/:source/:nativeId`, the extension's
`POST /listings/lookup` and the writer all use — and every one of the 1.03 M stored native ids conforms
to the browser rules in `frontend/src/lib/portalIds.ts`. A pipeline card is `POST /pipeline/cards
{property_id}` (`tenant_conn`, `require_acting_account_id`, idempotent, merge-survivor aware), reached in
the SPA by ONE hook, `usePipelineCard().add`.

**Five inbound URL→portal rule sets disagree.** `portalIds.ts` (nine portals; the extension's manifest
`matches` derive from its hosts); seven private `_id_from_href` / `_ID_RE` copies in the Python parsers
plus `mmreality_main._REF_ID_RE` (the WALK's identity); `url_parser.extract_sreality_id` (`\d{8,}`, which
rejects 654 stored sreality ids, 254 of them active); `source_dispatcher._KIND_SUFFIXES` (six kinds, its
own vocabulary `idnes_reality`, no bazos/mmreality/maxima); and the live CHECK
`estimation_runs_source_kind_check` (five values — a pasted ceskereality or realitymix URL we do not hold
fails its INSERT today; filed in `roadmap/scraper-track.md:424-430`). Two URL canonicalisers disagree on
scheme and query handling. No stored id differs under any of the candidate rules (live check), so the
rules can be unified without re-keying a row.

**estimation-4 is dormant and never produces an ad.** `scraper/source_dispatcher.parse_listing_url`
fetches any non-sreality host with a plain `requests` call (honest bot UA, no proxy, no client hints, no
SSRF guard, redirects followed) and sends 200 K characters to `claude-sonnet-4-5` with a 17-field
`record_listing` tool (measured: p50 23 s, p95 38 s, $0.13 per call); coordinates come only from a Mapy
forward geocode that is OFF by licence. The result is an estimation target spec cached seven days in
`parsed_url_cache`; it never reaches `listings`. Only the sreality branch persists (`url_parser.
parse_sreality_url(persist=True)` — a second sreality writer that skips the drain's churn instrument and
swallows write failures). Live usage: 12 LLM parses ever (last 2026-07-23), 13 unbound runs (10 with a
URL, last 2026-05-24; 3 spec-only), `subject_attributes` populated on 0 of 106 runs, `building_runs` 0
rows ever, `parsed_url_cache` 12 expired rows; the sreality-only late-binder binds 0 rows. The SPA's
`NewEstimationModal` calls `POST /estimations/preview` and throws everything but `preview.source_url`
away before `POST /estimations` re-parses the same URL; `GET /estimations/preview` has no caller. The
2026-06 decision this design reverses is recorded once: "No stub listings for orphan runs — the listings
table stays scrape-only" (`roadmap/archive.md:1048`); Field-capture R11 also says "the on-demand URL
parser is kept and brought onto the generated vocabulary" (`PROGRAM.md:162-163`).

**Every downstream lane is source-generic — except the portal registries.** Singleton birth and rollup,
image download and pHash, CLIP, DINOv3, the location resolver, sold comps, the estimation lane, autodedup
candidate entry (location-gated to `rt_scope`, not source-gated) and the merge chokepoint never look at
`source`. What does: the worker's `REALTIME_SOURCES` (pinned equal to `_CLIENT_CLASSES`);
`portal_factory.PORTAL_CLASSES / CLIENT_CLASSES`; `scraper.portal._DEFAULTS` (`PortalConfig`);
`contracts/portals/<source>.yaml` + `EXTRACTOR_PREFIXES` + `COORDINATE_RULES` (location claims;
`obec_name` mandatory; pinned by `contracts.lock.json`, a golden fixture and three nine-portal tests);
`claims_common.SOURCES` (the intake's wanted set, every health family's cell list, and
`verify_outbound_urls.CRAWLER_SOURCES`); `toolkit/broker_sources.BROKER_SOURCES` (attribution reads
`raw_json[block][id_key]`; the writer enqueues `dirty_broker_listings` only for its six sources — bazos,
bezrealitky and maxima have 0 broker identities live); `toolkit/filter_registry.PORTAL_OPTIONS` (chips,
labels, `properties.newest_ad_at_<portal>`, a `browse_projection` line and a `rebuild_browse_list`
index; pinned to `PORTAL_CLASSES` and, by `tests/test_canonical_order_migration.py`, to migration 588's
columns); `scraper/attribute_contract.CONTRACT` + `IGNORED` (26 cells per portal; pinned to `_DEFAULTS`;
a census file under `data/field_capture/` and a baseline row per portal, both blessed from production);
`scripts/reparse.SUBSTRATE`; `tests/scraper/test_walk_deadline_wiring.PORTAL_MODULES`; and the text
lane's scope (the contract's `llm_text` entries; live: bazos only). A source outside them is never
re-fetched or delisted, never located (so never SERVED to Browse, the map, the Watchdog or autodedup
under rule 25), never broker-attributed, invisible to every health cell, and ranked last by
`source_trust_rank` (`ELSE 10`). "No special treatment" for an unscraped site is therefore not a flag or
a branch; it is one row in each registry — which is what onboarding a portal means (rule 21).

**Delisting needs a walk, and an empty walk nominates nothing.** Rule 3's only nomination is
`run_index_walk` → `_queue_presence_checks` → `db.presence_candidates` after a walk that reached the
portal's end; both refuse an EMPTY seen set on purpose ("an empty seen set would nominate the whole
scope", `portal_runner.py:314-339`, `db.py:942-944`, pinned by `tests/scraper/test_portal_runner.py:
284-306`), and both scope by `(source, category_main, category_type)`, so an ad with a NULL category is
never nominated (693 such active ads exist today on realitymix, remax and ceskereality). The worker's
probe lane never nominates. A portal with no index therefore needs a declared seam, not a pretend walk.

**Where a fetch may run.** The Railway API service holds no `SCRAPER_PROXY_URL`; ceskereality and
mmreality hard-403 without it, idnes soft-throttles, and every portal's pacing lives in its client +
limiter. The worker has all of it. The text lane's provider is live at scale (`enrichment_model =
gpt-5.6-luna`: 602 k calls / $196 in the last seven days, p50 2.6 s), bounded by its own pass budget,
dollar cap, thread pool and provider-fatal exemption; the dormant parser's `llm_parse_model` is a second
LLM configuration for the same kind of work, with none of those bounds.

**The surfaces.** The phone's Odkaz tab (`pages/Link.tsx`; `lib/pastedLink.readPastedLink` pulls the
first URL out of share-sheet text) resolves the nine portals client-side, says "Tento inzerát v databázi
nemáme" (phone only; desktop shows a bare not-found) or refuses an unknown host before any request. The
Pipeline page adds no card today; its desktop header's right cluster holds the count, Reset, Sdílení and
Spravovat fáze in the outlined `HEADER_BUTTON` style (PRs #1906/#1908, merged 2026-10-20 11:20Z).
Browse's header (`FilterSummary`) has one copper primary, "+ Create watchdog". The extension's panel
hides every control on an ad we lack and offers only an orphan estimate. `AdvertRedirect` reports an ad
we DO hold as missing while its `property_id` is still NULL. The first-visit bundle has ~726 B of
headroom, and CLAUDE.md sits at 299 of 300 lines.

## 3. The design

### 3.1 Identity — one Python canon, one browser mirror, one probe fixture

`scraper/portal_identity.py` (≈ 50 LOC) holds ONE table `{source: (hosts, detail-id pattern)}` and two
functions: `native_id(source, href)` (what the seven parsers and `mmreality_main` import instead of their
private regexes; applied to the URL path, relative index hrefs included, with the exact semantics each
walk uses today — sreality and bezrealitky, which have no Python rule, take the browser rule) and
`resolve_url(url)` → `(source, native_id, canonical_url) | refusal`. Hosts match by registrable-domain
suffix (as `_KIND_SUFFIXES` does), so a portal subdomain never becomes a second identity; a URL on a
portal domain that is not one ad is refused; known wrapper links (`l.facebook.com/l.php?u=`,
`google.com/url?q=`, …) are unwrapped before resolution without a fetch. Patterns use `[0-9]`, no
`\b`, no named groups, no lookbehind, so the same strings are valid in both languages.

Any other `http(s)` host resolves to **`('web', canonical_url)`**, where the canonical URL is the ONE
surviving canonicaliser (today's `api/estimation_runs._canonical_url`, corrected): https, host lowercased,
userinfo and default port stripped, fragment dropped, tracking parameters dropped (`utm_*`, `fbclid`,
`gclid`, `gbraid`, `wbraid`, `mc_eid`, `igshid`; `ref` and `src` are KEPT — sites use them for ids),
remaining query sorted and KEPT (sites that key ads by `?id=` must not collapse), trailing slash stripped,
512 characters at most (live max of any stored URL: 183). The canonical URL is the identity; the as-pasted
URL minus its fragment is the `detail_ref` the drain fetches.

`frontend/src/lib/portalIds.ts` keeps its API and its literals (the extension imports it cross-tree and
the manifest derives from it; a repo-root file would break the SPA's Docker context, whose build CI never
runs). Its three regexes that differ from the canon (idnes `{24}` → `{16,}`, maxima, ceskereality /
realitymix) are aligned, and a shared probe fixture `tests/fixtures/portal_url_probes.json` (positives,
relative hrefs, index pages, wrappers, decoys, `web` URLs) is run by pytest against `resolve_url` /
`native_id` and by vitest against `parsePortalUrl` — the precedent is `tests/test_image_transform_parity.py`
+ `imageUrl.test.ts`. Deleted: the eight private regex copies, `url_parser.extract_sreality_id`,
`source_dispatcher.classify_url` + `_KIND_SUFFIXES` + `canonical_url`, the second canonicaliser, the
`source_kind` CHECK and the SPA `SourceKind` type.

### 3.2 Ingest — the API enqueues, the worker fetches, the SPA polls one state

`POST /listings/ingest {url}` (service-role connection; never `X-Act-As`; market data only — see §8 Q2
for who may call it) resolves identity and answers ONE state shape, also served by
`GET /listings/ingest/{source}/{native_id}` for polling:

| state | meaning | what the route did |
|---|---|---|
| `refused` | not an `http(s)` URL, a portal page that is not one ad, over the length cap | nothing; one Czech sentence |
| `held` | we hold this ad (`listing_id`, `is_active`, `property_id` or null while unborn) | nothing (active) or `enqueue_detail` at NEW (inactive: the drain re-checks the page and revives or confirms) |
| `queued` | not held; a queue row exists (`attempts`, `claimed_at`) | `enqueue_detail(source, [(native, detail_ref, None, NEW)])`; a `given_up` row is re-armed (`given_up=false, attempts=0`) — the one re-arm besides a walk's nomination |
| `gone` | the drain fetched and the page was gone (the latest `detail_queue_completions` outcome for a never-held ad) | nothing; "Inzerát na portálu už není" |
| `failed` | `given_up` after five attempts | nothing; "Stránku se nepodařilo načíst"; a re-paste re-arms |

The route returns no per-account data (CLAUDE.md: a `require_token` route carries none); the card's own
state comes from the existing tenant reads. `PortalLookupItem.source_id` grows to 512 for `web` ids.
`web` goes FIRST in `REALTIME_SOURCES`, so a paste waits only for the 30 s drain interval, never for
eight other portals' budgets.

### 3.3 Birth — every ad within ~32 s, all day

The maintenance lane's code default `MAINTENANCE_INTERVAL_DEFAULT` goes 120 → 30 (no `app_settings`
row exists; the Settings page cannot create one): an idle pass costs ~6 ms (12,925 measured calls at a
mean of 5.8 ms; the lease CAS 0.4 ms), and the broker lane that shares the interval gates its matview
refresh by age, not cadence. The daily full sweep, which today holds the lease for its whole 13–59 min
run and freezes every birth, attaches stragglers inside its batch loop under the lease it already holds
(newborns carry ids above `max_id`, so the sweep never touches them; ≈ 3 LOC). `_attach_stragglers` runs
`properties_changed` under `_bounded` instead of its hand-rolled recompute + `sync_browse_list`; the dead
`recompute_one` goes. No API-held lease, no `ids=` scope (NEW_SINGLETONS_SQL takes no row lock, so a
birth outside the lease can orphan a bare property). The place label still arrives with the location
lanes (54 % of today's births resolved more than 30 s after birth): the card is never hidden
(`pipeline_board_public` LEFT JOINs `properties_public`), the board re-reads, and the dialog tolerates a
missing place.

### 3.4 The tenth portal, `web`

A real portal in `PORTAL_CLASSES`, `CLIENT_CLASSES`, `_DEFAULTS` (`supports_complete_walk=True`,
`shared_rate_limiter=True` so the drain's deadline binds, cadence 1440 min, ONE agenda-wide category
descriptor with `declared_total=0` so coverage reads `complete`), `REALTIME_SOURCES` (first), a
`portals` row (migration), `PORTAL_OPTIONS` (`web`, label "Web"; the card badge shows the HOST from
`source_url`, so the operator still sees which site), `reparse.SUBSTRATE`, `PORTAL_MODULES`,
`attribute_contract.CONTRACT` + `IGNORED` (26 cells: the JSON-LD / OpenGraph keys as `structured`, every
prose field as `text` with a closed gate so a refetch never wipes it), `claims_common.SOURCES` (so its
coverage, delisting and town cells are VISIBLE rather than silently absent), `EXTRACTOR_PREFIXES['web'] =
'wb.'`, `COORDINATE_RULES['web'] = none`, a `BrokerSource(source='web', block='broker', id_key='id',
name/email/phone keys, firm_keys=('agency',))`, and `contracts/portals/web.yaml` — TEXT-ONLY, the five
`llm_text` entries bazos declares (`obec_name` mandatory, part of town, street, house numbers), no pin
(a JSON-LD geo pin is a later `contract_version` bump that needs the licence ruling §8-R-later).

- `scraper/egress.py` (≈ 80 LOC) — ONE safe outbound session: `http(s)` only; DNS-resolve and refuse
  loopback, private, link-local, metadata, IPv6 ULA (Railway's `fd12::/16`, `*.railway.internal`) and
  IPv4-mapped addresses, pinning the validated IP for the connection (no rebinding) and re-validating
  every redirect hop; a wall-clock deadline on the streamed read; a 2 MB cap on DECODED bytes; a desktop
  UA. It replaces the plain sessions in `image_storage.download_image` (page-supplied image URLs reach the
  open `/images/{key}` proxy) and `scripts/verify_outbound_urls` — three fetchers of foreign hosts become
  one. `scraper/web_client.py` (≈ 60) adds per-host pacing (one request per 10 s per host) on top.
- `scraper/web_parser.py` (≈ 200) — **deterministic**, like bazos: JSON-LD (`RealEstateListing`,
  `Product`/`Offer`, `seller`/`agent`), OpenGraph/meta (title, description, image), the gallery `<img>`
  candidates, the stable main text as `description` (JSON-LD description, else `og:description`, else the
  main block — never page chrome, which would snapshot on every fetch), and the shared grammars over title
  + description: `scraper.price_text` (price, per-m² guard), `scraper.area.parse_area_text`,
  `vocabulary.disposition`, `scraper.floor.floor_from_text`, and the title's sale/rent + byt/dům/pozemek/
  komerční words for `category_type` / `category_main` (NULL when the page does not say). Contacts: a
  `+420` phone, a `mailto:`, a JSON-LD seller → `raw['broker'] = {id: normalised email, else normalised
  phone, else absent; name; email; phone; agency}`. **No LLM at ingest** (Field-capture §3 bullet 1,
  R6, R7, R10): the existing text lane is the portal's only reader — its ONE call returns the location
  block from `web.yaml`'s `llm_text` entries and, once the operator opens R7 gates for `(web, field)`,
  the attribute cells with the lane's semantics (floor in the ad's own words, a verbatim evidence quote,
  terrace excluded). Price, area, disposition and category still arrive at ingest.
- `scraper/web_main.py` `WebPortal` (≈ 100) — `fetch_detail` by the stored URL through `web_client`
  (404/410, or on a VERIFY fetch a redirect off the page's host or path root → `ListingGoneError`; on the
  FIRST fetch a redirect that resolves to a portal ad re-keys the row under that portal — a shortener or
  wrapper to an sreality ad becomes the sreality ad, never a second identity); `write_details` = the
  unmodified default. `presence_candidates` is the portal's own: every active `web` row with no queue
  row and no completion within `RECHECK_THROTTLE`, NULL categories included. The shared guard gains ONE
  declared seam: `_queue_presence_checks` skips the empty-seen refusal only for a portal that declares
  `indexless = True` (rule 21: a per-portal need is a `Portal` seam, never an `if source ==`); the
  existing empty-seen tests stay valid for every other portal. A no-op `set_index_page_cap` keeps the
  probe lane quiet. `.github/workflows/web_index_walk.yml` (daily; the per-portal walk convention;
  `web_main --index-only`) nominates; the drain fetches; `gone_evidence` + `GONE_DWELL` + `mark_listing_
  inactive` apply unchanged, so a dead web ad closes 24–48 h after it dies. `delist_flip_cap` never
  throttles it (below the 2,000-row floor) and the gone-rate breaker never trips on it (< 20 ingest
  fetches a run); both are stated, not special-cased.
- Autodedup reads a `web` ad's PORTAL as its host (one helper `portal_of(listing)`, `source` for the
  nine), so two unrelated sites are the `cross` stratum and `same_source` features keep their meaning.
  No per-portal quirk tables; the engine's calibration is unchanged (operator-informed, §8-R-later).
- Migration `64x_web_portal.sql` (additive): the `portals` row; `properties.newest_ad_at_web`; the
  `browse_projection` view and `rebuild_browse_list` function re-created with the tenth line (≈ 280 SQL
  LOC, the rule-21 schema seam; `tests/test_canonical_order_migration.py` learns to compare 588 against
  the first nine). The field-census files are blessed from production after the first week of pastes;
  until then `tests/scraper/test_field_census.py:71` checks the subset its docstring already promises.

### 3.5 Estimation from a URL = add the ad, then estimate it

`POST /estimations` loses its `url` arm and gains `listing_id` (the surrogate twin the Listing-identity
track owes: ONE subject matcher keyed on `listings.id` with the property-canonical facts; the
`sreality_id` body resolves to it; `_load_subject_condition` re-keyed; idempotency arm `ad:{source}:
{native}`). The SPA's "New estimation" on a property page passes the canonical ad's `listing_id`, which
also unlocks estimation for non-sreality ads (today gated on `sreality_id != null`); `runSurfaceUrl` and
`EstimationDetail` route by the run's subject `(source, native)` (added to the run projection), so bound
idnes / bezrealitky runs stop landing on "not in our database". A pasted URL is estimated in two steps:
the paste box lands on the property page, whose button starts the run. The extension's orphan estimate
on an unknown ad becomes "Přidat do databáze" (ingest + poll), after which its controls appear. Deleted:
the dispatcher arm of `_resolve_input`, `preview_estimation` and both preview routes, `_match_listing_by_
url`, `_reject_ungeocodable_submit`, `_subject_attributes_from_result`, `_resolution_for_parse_failure`,
`_listing_from_result`, `_EMPTY_RESOLUTION`, the parse fields of `_Resolution`, the sreality-only
late-binder (binds 0 rows) and the legacy join arms it served, `parsed_url_cache`'s locality arm,
`POST /buildings/from_url` (0 rows ever; the dispatcher's second caller; the modal's building-URL kind
with it — the rule-13 subsystem is otherwise untouched), the modal's URL field and preview step, the
`SourceKind` type, and `subjectAsListing`'s `subject_attributes` arm. The 13 historic unbound runs keep
the read-only `/estimation/:id` page through its `input_spec` arm (rule 12); 6 lose a list locality label
when `parsed_url_cache` goes (accepted). Architecture rule 12's binder paragraph is rewritten in the same PR.

### 3.6 SPA and extension

One `NewPropertyDialog` (Czech; lazy-loaded — Browse and Pipeline are eager imports and the bundle has
726 B of headroom): title "Nová nemovitost", one field "Odkaz na inzerát" (`readPastedLink`'s first-URL
extraction), states Hledám inzerát → Stahuji stránku → Zakládám nemovitost → done, or one refusal
sentence (`refused` / `gone` / `failed` above). It is opened from the Pipeline header's right cluster
(`HEADER_BUTTON`; hidden under `useActingReadOnly`; the card lands on the acting owner's board), from the
Browse `FilterSummary` slot (outlined, not a second copper primary), and from the phone's Odkaz tab —
whose client-side refusal of unknown hosts and whose phone-only `AdvertNotFound` state both go: the tab
pastes into the same body. `held` adds the card at once (an inactive ad we hold still gets its card; the
drain's re-check runs behind it); `queued` polls the GET every 2 s until `held` with a `property_id`
(no cap: the "attaching" state survives a slow birth), then `usePipelineCard().add`. Pipeline: stay,
toast "Přidáno do pipeline · Otevřít"; Browse and Odkaz: navigate to `/property/:id` (Browse cannot show
the property until its location resolves and the next rebuild). An ad already on the board: the toast
says so with its stage. `AdvertRedirect` treats `property_id` NULL as attaching, not missing. The
estimation modal keeps only its spec kind.

## 4. Rejected

- **Keep estimation-4 as the "unscraped" path (LLM spec, no listing).** Special treatment by omission:
  no listing, no property, no images, no broker, no location, no dedup — the opposite of the request.
- **An LLM parser at ingest for `web`** (the brief's first version). Refuted by Field-capture: §3 forbids
  an LLM between a sighting and publication, R6 forbids snapshots minted from model noise (every
  `record_listing` field is a hashed field), R7/R10 forbid LLM-written cells without a ≥ 95 % panel, the
  tool's floor arithmetic and terrace-as-balcony break R11, and the `web.yaml` text entries would have
  made TWO LLM writers with opposite semantics on the same columns. Measured, it also costs 23 s and $0.13
  a page against the text lane's 2.6 s and $0.0003.
- **One `listings.source` per unknown host.** Each host would need its own contract, broker row, chip and
  `newest_ad_at_<host>` column; one `web` portal is one row per registry, and a host graduates to a real
  portal only when it gets a scraper.
- **A pretend walk for `web`.** An empty seen set is refused twice on purpose; the declared `indexless`
  seam is honest and ~30 LOC, the pretend walk would log 40 warnings a run and nominate nothing.
- **A runtime-shared id-rule file** (`shared/portal_ids.json`). Neither Docker image can read it without
  a cross-territory COPY, and the SPA build that would break is not run by CI.
- **Fetch the page inside the API request.** No proxy, no per-portal client state, a 130 s deadline, and
  a second fetch path beside the drain's ledger and gone-evidence.
- **A late-binding estimation executor** that waits for the ad. Submit stamps the subject today; the wait
  would have to move into the executor, exceed the extension's six-minute poll and the 130 s deadline,
  and keep the orphan machinery alive. Two steps delete it instead.
- **A "possible duplicate" suggestion at paste time.** No per-property candidate read exists; deciding
  "same property" at insert is grouping, which rule 15 keeps out-of-band (§8 Q4).
- **A pending pipeline card without a property.** `property_pipeline` is property-grain by rule 22.

## 5. Subtraction ledger

| Addition | Paid by (deletion) | Evidence |
|---|---|---|
| `scraper/portal_identity.py` (≈ 50) + the probe fixture and its two test arms (≈ 70) | eight private regex copies (≈ 50), `url_parser.extract_sreality_id` and `scraper/url_parser.py` (96) + `tests/test_url_parser.py` (190), `classify_url` / `_KIND_SUFFIXES` / `canonical_url` (≈ 55), the second canonicaliser (≈ 40), the `source_kind` CHECK + SPA type (≈ 50) | §3.1 |
| `POST/GET /listings/ingest` + schema + tests (≈ 240) | `GET /estimations/preview` (≈ 160 with tests), `POST /estimations/preview` + `preview_estimation` + the SPA preview step and types (≈ 420) | §3.2 |
| the tenth portal: `egress` (80), `web_client` (60), `web_parser` (200), `web_main` + presence seam + guard (≈ 130), the walk workflow (≈ 90), contract + lock + registry rows (≈ 60), cells (≈ 40), tests (≈ 250) ≈ 910 | `scraper/source_dispatcher.py` (500) + `scraper/source_parsers/*` (313, the `record_listing` tool included) + `tests/scraper/test_source_dispatcher.py` (534) + `tests/scraper/test_source_parsers/*` (253) + `fetch-fixtures.yml` (76) + the `RECORD_LISTING_TOOL` rail in `test_vocabulary.py` (12); the two plain outbound sessions in `image_storage` and `verify_outbound_urls` fold into `egress` | §3.4 |
| migration `64x` (≈ 280 SQL: row, column, view, function) | `parsed_url_cache` + the nine parse columns + two settings + the `idnes_reality` row (W4) | §3.4, §6 |
| `listing_id` arm + one subject matcher + run-projection fields (≈ 90) | `_resolve_input`'s dispatcher arm, `_match_listing_by_url` + SQL, `_reject_ungeocodable_submit`, the orphan helpers, the parse fields, the late-binder + its legacy arms, the locality arm (≈ 330) + their tests (≈ 300); `POST /buildings/from_url` + the modal's building-URL kind (≈ 140) | §3.5 |
| `NewPropertyDialog` + tests (≈ 370), two buttons (≈ 30), the extension's add button (≈ 40) | `Link.tsx`'s `other` arm + `AdvertNotFound` + the desktop/phone split (≈ 60), the modal's URL field + `previewMut` (≈ 150), `runSurfaceUrl`'s orphan branch | §3.6 |
| the sweep's per-batch attach (≈ 3), `portal_of` (≈ 10) | `recompute_one` + `_RECOMPUTE_ONE_SQL` (≈ 20), `_attach_stragglers`' hand-rolled after-step (≈ 4) | §3.3 |

**Net, plainly.** Code (tests excluded): ≈ 1,600 added against ≈ 1,950 deleted; with tests ≈ 2,000
against ≈ 3,250. The tenth portal is the one addition new in kind (≈ 910 incl. tests + 280 SQL); every
other addition replaces a copy. Schema: +1 row, +1 column, one view and one function re-created; −1
table, −9 columns, −2 settings, −1 row. Routes: +2, −3. LLM configurations: −1. Outbound fetchers of
foreign hosts: 3 → 1. URL rule sets: 5 → 1 canon + 1 pinned mirror. Paste surfaces: 3 bodies → 1.
`scraper/geocoding.geocode` STAYS (`scripts/seed_curated_cities.py` uses it); `fetch_and_anonymize_
fixtures.py` stays (`--scrub-contacts`).

## 6. Waves (one PR each; merges only in a window the operator opens ad hoc — e9 announces it and gives the green light per PR)

Migration numbers: 641 merged, 642 (#1911) and 643 (#1910, destructive, gated ≥ 2026-10-21 23:50Z) are
claimed — this sprint starts at **644**, re-checked at merge. #1910 edits `scraper/portal.py`,
`portal_runner.py`, `db.py` and `main.py`, which W3 also touches: W3 lands after it.

- **W0 `roadmap/new-property-from-link-spec`** — this document (draft PR now; merged on approval).
- **W1 `feature/link-ingest`** (identity, ingest, birth cadence, the dialog for the nine portals; from
  main after #1906/#1908, which are in). §3.1 (canon, fixture, aligned TS), §3.2 (both routes, re-arm,
  the 512 cap), §3.3 (30 s default + sweep attach + `properties_changed`), §3.6 (dialog on Pipeline,
  Browse and Odkaz; extension button; `AdvertRedirect`). Unknown hosts answer one sentence ("Tuto
  stránku zatím nečteme") until W3. Tests: the probe fixture both arms, route tests (states, re-arm,
  no per-account data), vitest for the dialog states and both buttons, the sweep-attach test. Docs:
  CLAUDE.md rules 15/19 one clause (a birth also inside the sweep; line-neutral), architecture § rule 20,
  scraper-ops (the ingest route in the queue section), `roadmap/operator-workflow-track.md` entry.
  Verify: paste a held ad (card at once), a new idnes ad (card within ~1 min), a dead ad (`gone`).
- **W2 `cleanup/estimation-four`** (estimation = add, then estimate; the deletion wave). §3.5 and the
  §5 deletions it names; architecture rule 12 binder paragraph; the llm-pipelines skill's URL-parser
  section rewritten (line-neutral), toolkit-api's `parsed_url_cache` line, `roadmap/ui-track.md` /
  `building-decomposition-track.md` / `scraper-track.md` entries, Field-capture R11 amended on the ruling.
  CI rails: `generate_workflow_docs.py --check` (the deleted workflow), `tests/api/test_auth.py`'s
  `url_parser` monkeypatch. Verify: an estimation of a bezrealitky ad from its property page.
- **W3 `feature/web-portal`** (the tenth portal, after #1910). §3.4 end to end; migration 644+;
  `web.yaml` + lock + golden fixture; the four nine-portal test pins (contracts, licence, census subset,
  588 columns), `PORTAL_MODULES`, `REALTIME_SOURCES` pins, `generate_filter_registry --check`; the
  `Link.tsx` `other` arm goes; docs: CLAUDE.md "nine portals" → "ten" and rule 3's `indexless` clause
  (line-neutral), architecture § Data sources (a `web` narrative), rule 21 seams, GLOSSARY ("Web" as a
  portal, an ad keyed by its URL), scraper-ops onboarding section (it is a trigger with no section today).
  Verify: paste a link from an unscraped site — listing, images in R2, broker identity, a town within
  ~10 min, Browse at the next rebuild; a 404 closes it within 48 h.
- **W4 `cleanup/estimation-four-schema`** (destructive, after 643 and W2; pg_dump + operator OK): drop
  `parsed_url_cache` (its `parsed_url_activity` view goes with 643), the `estimation_runs` and
  `building_runs` parse columns, the two `llm_parse_*` settings, the `idnes_reality` portals row
  (`portals.kind` stays; `field_census` reads it).
- **Later, not this sprint:** the field-census bless for `web` after a week of pastes; R7 gates for
  `(web, floor)` / `(web, has_lift)` (ruling: does bazos's passed panel carry under R10?); a JSON-LD geo
  pin (licence ruling); the extension on unscraped hosts (manifest `matches` are the nine).

## 7. Risks and mitigations (what the skeptics changed)

| Risk | Mitigation |
|---|---|
| a web ad is never delisted (the empty-seen guard) | the declared `indexless` seam + the portal's own `presence_candidates` (NULL categories included) + the daily walk; closes in 24–48 h |
| the drain thread blocks on a slow foreign site | `egress` wall-clock deadline + decoded-size cap; `shared_rate_limiter=True` so the drain's 120 s deadline refuses late acquires; `web` first in `REALTIME_SOURCES` |
| SSRF through the worker, the image downloader or the URL prober | ONE `egress` session with IP pinning, private-range refusal on every hop, IPv6 ULA and mapped addresses, used by all three |
| a wrapped or shortened link becomes a second identity | known wrappers unwrapped at resolve; a first-fetch redirect to a portal ad re-keys under that portal |
| a re-paste of a given-up ad hangs forever | ingest re-arms the row; `failed` and `gone` are visible states |
| the card is blank at birth | price/area/category are filled in the birth transaction; the place label follows the location lanes; the board re-reads |
| a paste during the daily sweep waits 10–16 min | stragglers attach inside the sweep's loop; the dialog's attaching state has no cap |
| two unrelated sites read as one portal in autodedup | `portal_of` = host for `web` |
| a pasted ad is shared market data for every account (comparables, watchdogs, broker contacts, a token in a URL) | admin-only in v1 (§8 Q2); the canonicaliser keeps query params by design, so the operator pastes ad links, not session links |
| CI rails: the five nine-portal pins, 588's column pin, the census equality, `generate_*` checks, the bundle budget, the 300-line CLAUDE.md | each named in its wave; the dialog is lazy-loaded; every doc edit line-neutral |
| the text lane reads no town (foreign property, empty description) | the ad stays unserved and red in `location_town_coverage` — visible, not hidden; a country claim path is a later ruling |
| `web`'s census flaps when its few ads close | the bless waits a week; the parsing alarm's zero-ads arm is a true signal for a hand-fed source (accepted) |

## 8. Rulings needed (defaults in bold; the plan runs on the defaults once approved)

1. **Build the tenth portal `web` in this sprint** (§3.4: deterministic parser, text lane, daily
   recheck, chip "Web" with the host badge) / v1 = the nine portals only, unknown sites refused.
2. **Who may paste: admins only in v1** (one gate; a paste makes the worker fetch a foreign page and its
   ad becomes every account's market data; the public-release track widens it with entitlements) / any
   signed-in user.
3. **Estimation from a URL becomes two steps** — the paste box lands on the property, its button starts
   the run; `POST /estimations` gains `listing_id` and loses `url`; the orphan machinery, the previews and
   the late-binder go; Field-capture R11's "the parser is kept" is superseded / keep the URL arm (then
   the dispatcher's LLM parse stays for unknown sites and the ledger turns positive).
4. **"Already have that property": no new surface** — the exact ad is caught structurally; a cross-portal
   twin is merged by the autodedup lane inside its apply scope or by the operator in Browse merge mode,
   as today / a read-only "Možná už máme" list after the ad is located + a one-click operator merge
   (a new candidate read, ≈ 250 LOC, a later sprint).
5. **W4's destructive drop** (`parsed_url_cache`, nine parse columns, two settings, one portals row) with
   pg_dump, last, after 643 / keep the columns frozen.
6. **Birth cadence for every ad: 30 s + attach inside the sweep** (§3.3) / leave 120 s and accept a
   1–2 min card for pasted ads (and 10–16 min during the sweep).

Decisions taken without a ruling (say so to change them): Czech labels ("Nová nemovitost", "Odkaz na
inzerát"); the Pipeline button stays on the board with a toast, Browse and Odkaz navigate to the property;
an inactive ad we hold gets its card at once; `web` ads carry the host as their badge; `ref` / `src`
query parameters are kept in the identity.
