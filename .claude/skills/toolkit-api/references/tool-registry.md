# The tool registry (`api/capabilities/`, #2062)

Every platform operation an agent may call is ONE `Entry` in ONE table, reached through ONE function,
`registry.call(caller, name, raw_args)`. The agent loop (#2065), the terminator, Apply (#2072) and any later MCP or
HTTP adapter are thin callers of `validate` / `call` / `schemas`; none reshapes an entry.

## An entry

`Entry(name, description, args, handler, tenancy, effect)`: no field has a default, so a tool cannot exist
without saying whose data it reads and what it does.

- **`tenancy`**: `MARKET` (the service-role autocommit connection, ADR-0002's exception: the toolkit's analytics
  and the property page's broker read, whose relations A6 keeps off every login) or `TENANT` (the caller's own
  `tenant_pool.tenant_transaction`: `SET LOCAL ROLE authenticated`, their claims, `act_as=None` always, method `GET`
  for a read and `POST` otherwise). Every read is bounded as PostgREST bounds the SPA's signed-in GET
  (`tenant_pool.READ_BOUND`: READ ONLY under `authenticated`'s 8 s `statement_timeout`): a tenant read through its
  GET, a market call in a transaction of its own, so its `SET LOCAL`s end with it and never outlive the call on the
  shared service connection.
- **`effect`**: `READ`, `WRITE`, `COSTLY` (money: Valuo, an LLM) or `CODE`. A market tool only reads (census; its
  transaction is READ ONLY anyway), so a write is only ever to the user's own data. One named exception
  (`_PAID_MARKET_FACTS` in the census), below: a market fact that costs money the first time and writes only the cache of
  what it paid for, with no transaction round the call.
- **`args`**: a Pydantic model on `api.filter_args.Args` (`extra="forbid"`: an unknown key is an error, never
  dropped). Filter fields are GENERATED from `toolkit/filter_registry.py` by `filter_model(name, ids,
  consumer)`: the registry's type, vocabulary (`Literal`, integer vocabularies too), description (plus its unit
  and basis) and `min` (as `ge`), the CONSUMER's default, or the registry's own `FilterDef.default` when the
  consumer is the filter compiler (`consumer=None`, a mapping consumer: `search_properties`), nullable exactly when
  that default is None. The composites are strict models: place chips (`Chip`), city-index rules (`CityIndexRule`)
  and a curated-set scope (`SetScope`, held in `setScope.ts` normalizeScope's form); the stored Watchdog blobs keep
  their lenient ones. Gemini's adapter drops an integer `enum` (its schema takes strings only); the server still
  refuses another value. The registry's `max`/`step` are slider axes and are never bounds; an integer's one ceiling
  is its int4 column. A filter the consumer has no parameter for raises at import; a consumer whose semantics
  differ restates the text (`descriptions=`). A model holding both `max_age_days` and `lifecycle` refuses recency
  without `lifecycle='active'`, which the cohort would drop. `filter_values(args)` hands the filters on by registry id.
- **`handler(ctx, args)`**: `ctx.conn` is the one connection the tenancy picked; a handler opens none (AST
  census). A market handler returns a toolkit envelope (`data` + `metadata`) unchanged; a tenant handler returns
  its module's own shape (at least `data`).

## Subjects (`api/capabilities/subject.py`)

A cohort tool takes exactly one of `property_id`, `listing_id` or `point`. A property stands for its canonical ad
(`property_identity.canonical_advert`; a merged-away id follows its survivor); a held ad is placed by
`toolkit.subject.match_listing` (the location ad's point, R7) and leaves its whole property out of the cohort
(Decision 13); a category filter the call leaves unset takes the property's own category, an explicit null means
every category. A `point` has no category, so it must state both; a tool that applies no area, layout or floor
takes a `Place` (lat, lng) only. A band the caller asks for (`area_band_pct`, `disposition_match`, `floor_band`)
whose anchor the subject lacks is REFUSED (`subject.unanchored`, shared with the REST body), never dropped. No
`sreality_id` input exists (rule 21).

## What `call` does

1. `validate`: UNKNOWN_TOOL or INVALID_ARGUMENTS (each error's `loc`/`msg`/`type`; the input is never echoed),
   before any connection exists.
2. Opens the connection (`_connection`, the only seam: the tenancy and the read bound are chosen there), runs the
   handler; any raise leaves the transaction first (a tenant write rolls back), then maps. A handler that swallowed
   a database error left its transaction aborted (`INERROR`; `rent_map`'s reference swallows any, the 8 s cancel
   included): that is BUSY, never the handler's answer.
3. Maps every exception through `errors.to_tool_error`, the one table: HTTPException 400/413/415/422 →
   INVALID_ARGUMENTS, 401/403 → FORBIDDEN, 404 → NOT_FOUND, 409 → CONFLICT, 503 → UNAVAILABLE, other 4xx →
   REFUSED; `deps.DB_BUSY_ERRORS` → BUSY (retryable); a `DataError` (a value out of the database's range) →
   REFUSED; InsufficientPrivilege → FORBIDDEN; other OperationalError → UNAVAILABLE (retryable); a toolkit
   `ValueError` → REFUSED with its text; anything else → INTERNAL with a logged reference and no exception text.
4. `truncate.fit`: a result within `RESULT_BUDGET` (20 000 chars of `encode`) is returned as is; else the lists
   under `data` keep their longest whole-row heads (smaller lists whole first) and `metadata.truncated` records
   `{path: {kept, total}}`; metadata is never cut. What cannot fit with every list empty is RESULT_TOO_LARGE. There
   is no other row cap: each tool bounds itself (search: pages of `property_search.PAGE_MAX` cards, sized so the
   widest fits whole, within the first `REACH` matches; 20 properties per call; the page's newest 100 runs per
   property in the dossier, where a property no result can hold whole keeps the heads of its ads (canonical, live,
   ended, newest first) and runs that fit by `truncate.heads`, the same rule, its own `truncated` naming each cut;
   the broker read stopping at the 20th person).
5. Records nothing: the transport that calls a tool records it. #2065's loop journals each tool step's raw
   arguments and what the model got back (the fitted envelope, or the ToolError's payload on any refusal), which is
   where ADR-0002's recorded broker read lives; `scripts/capabilities_demo.py` records nothing.

`schemas()` renders each `args` through `schema.portable`: `$ref` inlined, `X | None` as X plus `nullable: true`,
no titles, no null defaults (Gemini's client refuses `$ref`, type arrays and exclusive bounds; the census builds
every schema with its SDK).

## The parity reads (#2063, ADR-0002)

An agent reads what the signed-in person's screens read: the same relations and columns, under the same login,
through ONE server-side reader per screen. A new reader of these relations calls it; it never copies the SQL.

- `search_properties` (TENANT): Browse's own source, `browse_list_visible` (migration 629: the user's dismissals and
  tag, collection and pipeline scope judged under their RLS), the registry's `Agenda.BROWSE` filters (no Search
  agenda; `location` gives way to `bounds` or `circle` + `districts`) compiled by `compile_filter_where` over
  `BROWSE_GRAIN` (rule 16), the area through `area_where` (`in_map_area`, migration 694: a true circle), Browse's
  sort menu and one-portal order, the card allowlist `SEARCH_COLUMNS`, a page plus the total. `category_type` is
  required but nullable: it decides the per-m² unit. Browse's broker scope and market growth live outside the
  registry and are not offered (#2172).
- `get_property_dossier` (TENANT): `api/property_read.py`, the property page's own relations and allowlists
  (`properties_public`, `property_sources_public`, `listings_public`, `listing_snapshots_public`, and the runs the
  page lists from `estimation_runs` under RLS), which the extension's price chart and the PDF download read through
  too. A merged-away id follows its survivor (`resolve_active_property_ids`). No broker.
- `get_my_property_context` (TENANT): the extension's state read (`curation.property_state`, shared with
  `/listings/lookup`), `curation.notes_of` and the vocabularies as the API lists them (a colleague's e-mail
  included), in the user's own identity, never acting.
- `get_property_brokers` (MARKET): the page's `POST /brokers/by-listings` read (`toolkit.brokers.listing_brokers`,
  unmasked as the page shows every signed-in user), grouped as the page groups them (`brokers.property_brokers`,
  MS7), stopping at 20 people per call. MCP stays masked (AR13): an MCP export leaves it out or masks it.
- `rank_brokers` (MARKET): Browse's Makléři tab (`api/broker_board.board`) less `firm_ids` and `broker_id`, at most 50
  brokers, masked as a non-admin's tab (`docs/design/broker-board.md`).
- `aggregate_properties` (TENANT, #2064): the Stats dashboard's own function, `browse_stats_properties`, handed exactly
  the arguments the dashboard sends for the same Browse filters by ONE mapper, `api/stats_pivot.pivot_args`
  (`buildBrowseStatsArgs` over `resolveBrowsePrefilters` and `scopeArgs`: an empty list as null, the chips as four
  arrays, the city rules and `with_estimates` resolved under the login), plus a spec from `toolkit/stats_registry.py`
  (`rows`/`cols`/`measure`; what `validate_spec` refuses is INVALID_ARGUMENTS). `data` is the function's payload as it
  answers, `metadata.price_per_m2_basis` its `basis` (else mixed or unknown). The area bounds take whole m² (the
  function's are integers; a fraction is refused, never rounded). Over budget it cuts `cells` from the tail and keeps
  the `places` a kept cell names (`fit` would keep every name and few cells). `tests/api/test_stats_pivot_census.py`
  holds `PARAMS` (sent, with their SQL types) and `UNMAPPED` (the four parked condition levels, each with why) to the
  latest signature, and `PARAMS` to the keys the dashboard sends, each from the field it feeds it from
  (`filters.ts` `REGISTRY_KEY_MAP`).
- `market_evidence` (TENANT, #2064): prices achieved round a subject, two arms never mixed, every row and summary
  labelled `evidence` (`proxy` / `registered`) beside its `price_per_m2_basis` (`api/market_evidence.py`). The subject
  is a `PlaceSubject` placed through the page's readers (`listings_public`, `properties_public`; `match_listing` is
  dark under the login), its own property never its evidence. Proxy: the Stats tab's sold-market lens (`stats.ts`
  `soldMarketFilters`: inactive, observed from the first ad, dismissed included) over a circle of `radius_m`, validated
  as `AggregateArgs` and read by `cohort_where` over `browse_list`, nearest first; its summary is the pivot's total
  over exactly that cohort. The window is the registry's `tom_days_min`/`tom_days_max`, by default
  `stats_registry.SOLD_MARKET_DAYS` (2–60, twin of `stats.ts`); any other needs `window_reason`, echoed in
  `metadata.window`. Registered: `sold_comparables` with the SOLD filters over `SOLD_GRAIN`, ordered and capped (200,
  `more`) as the page reads it, within `radius_m` but never past `sold_db.MAX_READ_RADIUS_M` (5 km, the margin the
  fetcher covers round every town; the radius read is `metadata.registered_radius_m`), summed up by a port of
  `soldComps.ts` `summarizeSold` (its constants pinned as twins); byt and dum for sale only, else empty with
  `metadata.registered_refused`; `sold_coverage` in `metadata.registered_coverage` (the centre's town only). A filter
  an arm cannot apply is named in `metadata.unapplied`.

The census holds them to it: every column allowlist (`SEARCH_COLUMNS`, `curation.STATE_COLS`, each
`property_read.*_COLS`, `market_evidence`'s two) is disjoint from `forbidden_columns()`, derived from the registry
(rule 14's retired levels, rule 17's city-quality columns), and names no broker; `api/property_search.py`,
`api/property_read.py` and `api/market_evidence.py` name none of the relations those values are stored in
(`curated_cities*`, `city_index_*`, `city_population`, `listing_condition_scores`, `listing_broker_public`,
`listing_location`), nor one that reads as nothing under the login (base `listings`, `canonical_advert`, …);
`api/stats_pivot.py` is held to that last pattern only, since it resolves the city rules through
`curated_cities_matching`, as Browse does. The read models they do read (`browse_list_visible`, `properties_public`)
still publish parked levels, city-quality and (`properties_public`, #2174) broker columns, which only the allowlists
keep out; the dossier, broker, own-context and achieved-price reads, each run on a fixture, return no forbidden key at
any depth and every per-m² figure with its basis. Not covered: `api/curation.py`'s relations (only its `STATE_COLS`
allowlist is checked) and a fixture run of `search_properties` (only its `SEARCH_COLUMNS`).

## A paid market fact: `assess_property_visuals` (#2066, ADR-0007)

`api/capabilities/visuals.py` over `toolkit/photo_assessment.py`: MARKET, COSTLY, takes one `property_id`. The judgement
is shared by every agent, so it reads and writes on the service connection, in autocommit: `_connection` gives a
COSTLY market tool no transaction and no READ ONLY bound, since a model call must not hold one open. What goes into a
row is an ad's public photos and the facet file, never an account's data; the row names no account, run, property or ad
(`tests/test_photo_assessment_census.py`, which also holds that nothing but this tool names the cache).

**A run lends it what it may spend** (`api/capabilities/run_scope.py`): `loop` sets a `RunScope` around each tool call
(the run's own `LLMClient`, the money left, a tally, a bill) and `registry.call` hands it over as `ctx.run`. It is a
ContextVar because `call_tool` is a two-argument closure each runner builds: no runner has to know a tool can spend, and
a runner's own client (the one that stamps its run on `llm_calls`) reaches the tool. The bill is `run.charge`d as soon as
the provider has billed; the loop `settle`s it after the call, refused or not, into the run's cost and the tool's journal
step, so the cost cap sees it.

**The order:** no usable picture is a `no_photos` result; a cached judgement is served to anyone, free, with or without a
run; a miss while a live ad's photo is still downloading into the set is a `photos_pending` result, since the finished set
would be paid for again (rare: no live ad of a 1-in-500 sample had one on 2026-11-01); only then `_pay`, which refuses before any money with no run, past `MAX_PAID_PER_RUN` (10) bills in this
run, or when the worst case (`worst_case_usd`: every prompt token at the dearer rate, a reply at its limit) exceeds what
is left. A cached answer takes no slot (ADR-0007's ten bounds vision cost); a reply refused by the schema is billed,
takes a slot and is not cached; a provider failure bills nothing and takes none. An interrupted run that starts over
gets ten again, but what its first start paid for is now cached.

**What it judges.** The facets and prompt are `skills/photo-facets.md`, read each call; the prompt version is the hash of
what the model is given (the file, the reply rule and the schema generated from its `### key` headings), so any edit is a
new version with no code change, and old rows simply stop being read. The cache key is photo-set hash + version, with no
model: the first judgement stands for every agent (two runs racing both pay, and both get the first). A photo set is the
first 30 stored pictures of the canonical ad (else the first of the property's ads with one, live then newest) plus its
first 2 `floor_plan`-tagged ones. Each picture's identity holds its id, key, rendition, size and role, since the
re-master lane overwrites bytes under a stable key: a photo arriving, a re-master, a tag or a new canonical ad is a new set,
judged the next time it is asked about, and a photo past the cap changes nothing the model sees. A plan not yet tagged
travels as a photo.

**Model and cost.** `app_settings.llm_photo_assessment_model`, else `claude-opus-5-5` until
`scripts/photo_assessment_compare.py` (paid; writes no cache row) has spoken. Photos go at `COMPARISON_MAX_EDGE`, plans at
`DOCUMENT_MAX_EDGE`. `tool_choice` cannot be forced (a 400 on Opus and Sonnet 5.5), so a reply that skips the tool, is cut
off at `MAX_REPLY_TOKENS` (8,000, thinking included) or fails the schema is billed and never cached. About $0.10 a property
on Opus 5.5 (13–20 photos at ~590 tokens each, 2–3k tokens out), half that on Sonnet 5.5, an estimate until the
comparison measures it; at most about $0.32 on Opus. `llm_calls.called_for = 'assess_property_visuals'`.

## Adding a tool

One `Entry` in `market.py` or `tenant.py`, its effect named at the declaration (`market._read` builds READ
entries only; a costly or writing tool is its own `Entry`, tenant unless it is a paid market fact, § below; a write comes
with #2072's inverse). Reuse
`filter_model` for any registry filter; give every other field a `description`. The census (`tests/api/test_capabilities_census.py`)
then checks it: a real tenancy and effect, no identity field at any depth (account, user, acting, owner, …), a
market tool only reading and taking no id but `property_id`/`listing_id` (or their lists `property_ids`/`listing_ids`),
a portable schema that Gemini's SDK builds, null offered where it is accepted, no parked condition level or other
column no surface may show, no connection opened outside `registry.py`, and every declared filter and point attribute
changing the SQL the tool runs. The live tenant check is `tests/test_capabilities_live.py`. List or call one by hand:
`python -m scripts.capabilities_demo list|call`.
