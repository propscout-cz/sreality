# The tool registry (`api/capabilities/`, #2062)

Every platform operation an agent may call is ONE `Entry` in ONE table, reached through ONE function,
`registry.call(caller, name, raw_args)`. The agent loop (#2065), the terminator, Apply (#2072) and any later MCP or
HTTP adapter are thin callers of `validate` / `call` / `schemas`; none reshapes an entry.

## An entry

`Entry(name, description, args, handler, tenancy, effect)`: no field has a default, so a tool cannot exist
without saying whose data it reads and what it does.

- **`tenancy`**: `MARKET` (the service-role autocommit connection, ADR-0002's one exception: toolkit analytics)
  or `TENANT` (the caller's own `tenant_pool.tenant_transaction`: `SET LOCAL ROLE authenticated`, their claims,
  `act_as=None` always, method `GET` for a read and `POST` otherwise).
- **`effect`**: `READ`, `WRITE` (tenant only, by census), `COSTLY` (money: Valuo, an LLM) or `CODE`.
- **`args`**: a Pydantic model on `api.filter_args.Args` (`extra="forbid"`: an unknown key is an error, never
  dropped). Filter fields are GENERATED from `toolkit/filter_registry.py` by `filter_model(name, ids,
  consumer)`: the registry's type, vocabulary (`Literal`), description and `min` (as `ge`), the CONSUMER's
  default (never `FilterDef.default`, which is Browse's), nullable exactly when that default is None. The
  registry's `max`/`step` are slider axes and are never bounds. A filter the consumer has no parameter for
  raises at import. `filter_values(args)` hands the filters on by registry id, so none is dropped.
- **`handler(ctx, args)`**: `ctx.conn` is the one connection the tenancy picked; a handler opens none (AST
  census). It returns the toolkit's envelope unchanged.

## Subjects (`api/capabilities/subject.py`)

A cohort tool takes exactly one of `property_id`, `listing_id` or `point`. A property stands for its canonical ad
(`property_identity.canonical_advert`; a merged-away id follows its survivor); a held ad is placed by
`toolkit.subject.match_listing` (the location ad's point, R7) and leaves its whole property out of the cohort
(Decision 13); a category filter the call leaves unset takes the property's own category, an explicit null means
every category. A `point` has no category, so it must state both. No `sreality_id` input exists (rule 21).

## What `call` does

1. `validate`: UNKNOWN_TOOL or INVALID_ARGUMENTS (each error's `loc`/`msg`/`type`; the input is never echoed),
   before any connection exists.
2. Opens the connection (`_connection`, the only seam: #2063's read-only flag, timeout and row cap land there),
   runs the handler; any raise leaves the transaction first (a tenant write rolls back), then maps.
3. Maps every exception through `errors.to_tool_error`, the one table: HTTPException 400/413/415/422 →
   INVALID_ARGUMENTS, 401/403 → FORBIDDEN, 404 → NOT_FOUND, 409 → CONFLICT, 503 → UNAVAILABLE, other 4xx →
   REFUSED; `deps.DB_BUSY_ERRORS` → BUSY (retryable); InsufficientPrivilege → FORBIDDEN; other
   OperationalError → UNAVAILABLE (retryable); a toolkit `ValueError` → REFUSED with its text; anything else →
   INTERNAL with a logged reference and no exception text.
4. `truncate.fit`: a result within `RESULT_BUDGET` (20 000 chars of `encode`) is returned as is; else the lists
   under `data` keep their longest whole-row heads (smaller lists whole first) and `metadata.truncated` records
   `{path: {kept, total}}`; metadata is never cut. What cannot fit with every list empty is RESULT_TOO_LARGE.

`schemas()` renders each `args` through `schema.portable`: `$ref` inlined, `X | None` collapsed, no titles, no
null defaults (Gemini's client refuses `$ref`/type arrays).

## Adding a tool

One `Entry` in `market.py` or `tenant.py` (a write: tenant, and #2072's inverse). Reuse `filter_model` for any
registry filter; give every other field a `description`. The census (`tests/api/test_capabilities_census.py`)
then checks it: a real tenancy and effect, no identity field at any depth (account, user, acting, owner, …), a
market tool's ids only `property_id`/`listing_id`, a portable schema, no connection opened outside
`registry.py`, and every declared filter changing the SQL the tool runs. The live tenant check is
`tests/test_capabilities_live.py`. List or call one by hand: `python -m scripts.capabilities_demo list|call`.
