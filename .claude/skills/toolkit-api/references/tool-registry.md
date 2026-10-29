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
  transaction is READ ONLY anyway), so a write is only ever to the user's own data.
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
   is no other row cap: each tool caps its own input (a search page of `property_search.PAGE_MAX` cards, sized so
   the widest fits whole; 20 properties; 20 broker people).
5. Records: `caller.record(name, raw_args, payload)` exactly once per call, with the fitted envelope or, on any
   refusal or failure (a validation refusal included), the ToolError's payload. A `Caller` is the run context:
   #2065's loop and #2070's ledger plug in here, and no tool keeps a ledger of its own (the broker read included).

`schemas()` renders each `args` through `schema.portable`: `$ref` inlined, `X | None` as X plus `nullable: true`,
no titles, no null defaults (Gemini's client refuses `$ref`, type arrays and exclusive bounds; the census builds
every schema with its SDK).

## The parity reads (#2063, ADR-0002)

An agent reads what the signed-in person's screens read: the same relations and columns, under the same login,
through ONE server-side reader per screen. A new reader of these relations calls it; it never copies the SQL.

- `search_properties` (TENANT): Browse's own source, `browse_list_visible` (migration 629: the user's dismissals and
  tag, collection and pipeline scope judged under their RLS), the registry's `Agenda.BROWSE` filters (no Search
  agenda; `location` gives way to `bounds` + `districts`) compiled by `compile_filter_where` over `BROWSE_GRAIN`
  (rule 16), Browse's sort menu and one-portal order, the card allowlist `SEARCH_COLUMNS`, a page plus the total.
  `category_type` is required but nullable: it decides the per-m² unit. Browse's broker scope, market growth and
  centre + radius live outside the registry and are not offered (#2172).
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

The census holds them to it: every column allowlist (`SEARCH_COLUMNS`, `curation.STATE_COLS`, each
`property_read.*_COLS`) is disjoint from `forbidden_columns()`, derived from the registry (rule 14's retired
levels, rule 17's city-quality columns), and names no broker; the reader modules name no relation that holds those,
a broker or a stored place, nor one that reads as nothing under the login (base `listings`, `canonical_advert`, …);
each read, run on a fixture, returns no forbidden key at any depth and every per-m² figure with its basis.

## Adding a tool

One `Entry` in `market.py` or `tenant.py`, its effect named at the declaration (`market._read` builds READ
entries only; a costly or writing tool is its own `Entry`, tenant; a write comes with #2072's inverse). Reuse
`filter_model` for any registry filter; give every other field a `description`. The census (`tests/api/test_capabilities_census.py`)
then checks it: a real tenancy and effect, no identity field at any depth (account, user, acting, owner, …), a
market tool only reading and taking no id but `property_id`/`listing_id` (or their lists `property_ids`/`listing_ids`),
a portable schema that Gemini's SDK builds, null offered where it is accepted, no parked condition level or other
column no surface may show, no connection opened outside `registry.py`, and every declared filter and point attribute
changing the SQL the tool runs. The live tenant check is `tests/test_capabilities_live.py`. List or call one by hand:
`python -m scripts.capabilities_demo list|call`.
