# Saved dashboards: one saved-set primitive (2026-10-20)

**Operator ask (2026-10-20):** save the current chart set as a named dashboard you can return to; dashboards get their own
buttons on Browse › Stats; a dashboard can be shared with other users like the pipeline; the Browse filter can be saved
with it (a checkbox at save time); and the Browse "Table" tab goes, with everything that belongs to it.

**Rulings 2026-10-20 ("all as proposed"):** (1) loading a dashboard COPIES it into the working board; the saved
dashboard changes only on an explicit "Uložit změny" — exactly the filter-preset model; (2) the filter is EMBEDDED in
the dashboard row in the preset blob format, never a hidden linked preset; (3) a share is READ-ONLY: the recipient
loads a copy and may save it as their own; no edit mode, no acting; (4) the cards' sort menu gains "Plocha" and
"Naposledy viděno", the URL-only `estate_area` sort is deleted; (5) filter presets fold into the same primitive now:
one bar, one API module, two instances.

## North star

**Save, return, share: ONE saved-set primitive.** A saved set is a named row an account owns, lists, loads, updates,
reorders and shares. Filter presets and dashboards are two instances of it, nothing more. The account's working
board is the ONE unnamed dashboard row. Every decision in this sprint is measured against that sentence; a second
shape for "a named thing I can come back to" is a defect.

Amends `docs/design/stats-dashboard.md` ruling 2: a dashboard is no longer "one per account, independent of the
filter" — the WORKING board is one per account; named dashboards are many, and may carry a filter.

## The primitive

| | filter preset (`filter_presets`) | dashboard (`stats_dashboards`) |
| --- | --- | --- |
| identity | `id uuid` | `id uuid` (647; was `account_id` PK) |
| owner | `account_id` NOT NULL | `account_id` NOT NULL |
| name | `name` 1..200 | `name` 1..200, or NULL = the working board (one per account, partial unique) |
| order / colour | `position`, `color` (8-colour palette) | `position`, `color` (same CHECK text) |
| blob | `filter_spec {filters, sort}` | `charts` (validate_board) + `filter_spec {filters, sort}` NULL = no filter |
| listed by | `GET /filter-presets` | `GET /stats/dashboards` (named own + shared; the working row never) |
| scratch | the Browse URL | the unnamed row: `GET`/`PUT /stats/dashboard` |
| shared | no | `stats_dashboard_shares`, read-only |

The blob format of `filter_spec` is the preset's, verbatim: `{filters: ListingFilters minus the lens keys, sort}`,
read by `readPresetSpec`, captured by `filtersForPreset`, compared by `filtersEqualForPreset`, server-checked by the
same `_coded_chips` validator. No second reader, no second writer.

## Schema — migration 647 (`647_saved_dashboards.sql`), additive

1. `stats_dashboards`: add `id uuid not null default gen_random_uuid()`; drop the `account_id` primary key; add
   `primary key (id)`; add `name text null check (name is null or length(name) between 1 and 200)`;
   `filter_spec jsonb null`; `position integer not null default 0`; `color text null` with 201's palette CHECK;
   `created_at timestamptz not null default now()`. Index `stats_dashboards_account_idx (account_id)`. Partial unique
   index `stats_dashboards_one_working_per_account on (account_id) where name is null` — the working board's upsert
   target. Existing rows (one per account, name NULL) become the working boards untouched. Comment restated.
2. Policy `stats_dashboards_tenant_rw` stays as 626 wrote it (FOR ALL on `current_account_ids()`, never acting —
   pipeline-sharing §3.7 still holds: a delegate reads no dashboard). Add
   `stats_dashboards_shared_read FOR SELECT TO authenticated USING (id in (select dashboard_id from
   stats_dashboard_shares))`. Writes stay owner-only (the SELECT policy widens nothing else).
3. `stats_dashboard_shares (dashboard_id uuid → stats_dashboards on delete cascade, account_id uuid = OWNER,
   recipient_account_id uuid, created_at; primary key (dashboard_id, recipient_account_id); CHECK not-self)`, index on
   the recipient. Owner stamped by a BEFORE INSERT trigger `sync_account_id_from_dashboard` (610's shape) that also
   RAISES when the dashboard is unnamed: the working board is never shareable, by the database. RLS on; `revoke all
   from anon, authenticated; grant select, delete to authenticated`; policy `stats_dashboard_shares_party FOR ALL`
   using `(owner or recipient in current_account_ids()) and not acting()`, with check owner. No browser INSERT.
4. `share_party_emails()`: CREATE OR REPLACE with a third UNION arm over `stats_dashboard_shares` (641's body + one
   arm; still counterparties only).
5. The `share` notification needs no CHECK change (641:311 leaves `collection_id` NULL and `subscription_id` NULL for
   a non-collection share). Dedupe key: `share:dashboard:<dashboard_id>:<recipient>`.
6. Post-conditions in a DO block (house style): the PK is `id`; the partial unique index exists; the three policies
   exist with the pinned commands; `share_party_emails()` names the new table; the trigger exists.

Rails that move: `tests/_tenant_registry.py` += `stats_dashboard_shares`;
`tests/test_migration_rls_grants.py::_SHARE_POLICY_PIN` += `stats_dashboard_shares_party` (all) and
`stats_dashboards_shared_read` (select); the same file's hidden-while-acting census += the shares table;
`tests/api/test_acting_routes_census.py::_NEVER_ACTS` += `POST /stats/dashboards` and
`POST /stats/dashboards/{dashboard_id}/shares`; `test_admin_route_coverage.py` reasons; `test_auth.py` JWT rows.
`tests/test_626_stats_dashboard.py` pins the 626 FILE text and stays green; `tests/api/test_stats_dashboard.py` is
rewritten for the new upsert (`ON CONFLICT (account_id) WHERE name IS NULL`).

## API — one module, two instances

`api/saved_sets.py` replaces `api/filter_presets.py` + the CRUD half of `api/routes/filter_presets.py`:

- A frozen `SavedSetKind(table, blob_columns, projection_extra, list_where)` descriptor. Blob columns are
  `{"filter_spec": PresetSpec}` for presets and `{"charts": Charts, "filter_spec": PresetSpec | None}` for dashboards,
  each named with its validator (`_coded_chips` moves here; `Charts` = `toolkit.stats_registry.validate_board`).
- One implementation of list / get / create / update / delete / reorder, keyed by the descriptor: the same SQL shapes
  as today (`position = (SELECT COALESCE(MAX(position), -1) + 1 FROM <table>)`, `unnest ... WITH ORDINALITY`
  reorder, `_UNSET` colour sentinel), the same response rows plus the dashboard projection extras.
- One `saved_set_router(kind, prefix)` factory emitting the six CRUD routes (`GET ""`, `POST ""` with
  `require_account_id`, `PUT /reorder` before `/{id}`, `GET/PUT/DELETE /{id}`), every route on `tenant_conn`. The
  filter-presets router becomes `saved_set_router(PRESETS, "/filter-presets")` with identical paths, bodies and
  answers, so the SPA, the phone and `tests/api/test_filter_presets.py`'s behaviour pins hold (update a pin only
  where it names the old module).

Dashboards (`api/routes/stats_dashboards.py`, mounted like the presets router):

- `GET /stats/dashboards` → `{data: [...], total}`; rows = named rows the caller can read: own (RLS tenant policy)
  and shared (`shared_read`). Projection extras, `api/curation.py`'s shape: `owner_email` (NULL iff
  `account_id in current_account_ids()`, else `party_label`) and `shared_with` (json_agg of
  `{account_id, email}` over `stats_dashboard_shares`). `WHERE name IS NOT NULL`, order `position, created_at desc`.
- `POST /stats/dashboards {name, charts, filter_spec?, color?}` — the "Uložit jako dashboard" write; `charts`
  through `validate_board`, `filter_spec` through `_coded_chips`; `require_account_id` (never acts).
- `PUT /stats/dashboards/reorder {ids}`; `GET/PUT/DELETE /stats/dashboards/{id}` (PUT: partial `name`, `charts`,
  `filter_spec` where an explicit `null` clears the filter, `color`). A recipient's PUT/DELETE matches 0 rows → 404.
- `POST /stats/dashboards/{id}/shares {email}` → the ownership gate (`UPDATE stats_dashboards SET updated_at = now()
  WHERE id = %s AND account_id IN (SELECT current_account_ids()) AND name IS NOT NULL RETURNING name`), then
  `shares.grant(..., DashboardShare(id, name))` on the service role: insert `ON CONFLICT DO NOTHING RETURNING
  account_id`, then the dispatch with message `' shared the dashboard "<name>" with you'`, `collection_id` NULL,
  dedupe `share:dashboard:<id>:<recipient>`. Answer: the dashboard row (with `shared_with`).
- `DELETE /stats/dashboards/{id}/shares/{account_id}` — revoke (owner) or leave (recipient); the party policy decides;
  0 rows → 404.
- The working board: `GET /stats/dashboard` → `{id, charts, updated_at}` or `null` (the SPA then shows
  `DEFAULT_CHARTS`); `PUT /stats/dashboard {charts}` keeps its contract, upserting
  `ON CONFLICT (account_id) WHERE name IS NULL`. Both on `require_account_id`, so the read and the write resolve the
  SAME account — the single-membership caveat in `lib/stats.ts` and the design doc is deleted with the pgRead.

`api/shares.py` gains `DashboardShare(dashboard_id, name)` beside the two kinds (one more `isinstance` arm in
`grant`, one delete SQL, one dispatch call); nothing else there changes. `api/notifications.py`'s projection gains
`CASE WHEN d.source_kind = 'share' THEN split_part(d.dedupe_key, ':', 2) END AS share_kind` and
`... split_part(d.dedupe_key, ':', 3) END AS share_ref` (collection id, owner uuid, dashboard uuid).

## SPA

- **`components/SavedSetBar.tsx`** — THE bar, generic: `{items, activeId, dirty, onLoad, onSaveNew, onUpdate,
  onRename, onDelete, onReorder, leading?, chipMenu?, copy: {empty, save, update, nameLabel, extra?: {label,
  initial}}}`. Drag-reorder (dnd-kit), the active chip, the dirty mark and the copper "update" button, the dashed
  "+ save" button, the save modal. `PresetBar.tsx` becomes `FilterPresetBar.tsx` = SavedSetBar + the preset adapter
  (queries, mutations, `loadPreset`, the Pipeline chip in `leading`). `PresetSaveModal.tsx` becomes
  `SavedSetSaveModal.tsx` with `extra` in place of `includeMapArea` (presets: "Include map area"; dashboards:
  "Uložit i nastavení filtru"). Behaviour of the preset bar is unchanged, pixel for pixel; its tests move with it.
- **`components/stats/DashboardBar.tsx`** = SavedSetBar + the dashboard adapter, mounted at the top of the Stats tab
  (the `StatsDashboard` card list below it). Items: `GET /stats/dashboards`. Own chips drag; a shared chip shows the
  owner's email as its suffix, does not drag, and its menu offers "Načíst", "Uložit jako vlastní" and
  "Přestat sledovat"; an own chip's menu offers "Přejmenovat", "Sdílet…" (a small modal holding `ShareBlock`, no
  modes, `lang="cs"`) and "Smazat" (two-step).
- **Loading** (`onLoad`): ONE user action does two writes in order: `PUT /stats/dashboard {charts}` (copy into the
  working board, through `useOptimisticWrite` on the existing board cache), then the URL: if the dashboard carries
  `filter_spec`, `loadDashboard(d)` in `browseState.ts` writes the filters + sort exactly as `loadPreset` does
  (factor the shared body `applySavedFilter(spec, sp)` once; both callers use it), deletes `preset`, sets
  `dashboard=<id>`, pushed; without a filter it only sets `dashboard=<id>` (replace). `dashboard` joins
  `OVERLAY_URL_KEYS`; `activeDashboardId`, `setActiveDashboardId`, `loadDashboard` join `BrowseViewState`.
- **Arrival with `?tab=stats&dashboard=<id>`** (a notification link, a bookmark): the URL is the intent — the Stats
  tab loads that dashboard on mount when the working board's charts differ from it (one PUT), as a click would; a
  dashboard the caller can no longer read clears the param.
- **Dirty** = the working board's sanitized charts ≠ the active dashboard's, or (the dashboard has a filter and
  `!filtersEqualForPreset(current, stored)`). "Uložit změny" = `PUT /stats/dashboards/{id} {charts: working,
  filter_spec: hadFilter ? filtersForPreset(current) : undefined}`. "Uložit jako dashboard" = `POST` with the
  working charts and, when the checkbox is on, the current filter. Both through `useOptimisticWrite`.
- **Working board I/O** (`lib/stats.ts`): `fetchDashboard` becomes `apiGet('/stats/dashboard')` (null → defaults);
  the pgRead and its single-membership comment go. `saveDashboard` unchanged.
- **Notifications**: `ShareNotificationRow` branches on `share_kind`: collection → "Open collection", pipeline →
  "Open pipeline", dashboard → "Open dashboard" → `/browse?tab=stats&dashboard=<share_ref>`.
- Phone: unchanged (presets keep `listFilterPresets`; no dashboards on the phone).
- Bundle: `DashboardBar` ships in the Stats chunk (lazy); the first-visit budget (330 KB gzip) must not grow — the
  Table tab's removal pays for anything the preset bar refactor adds.

**W3 as built** (2026-10-20). Where it differs from the plan above: (1) the named dashboards' clients and their
list key sit in `lib/stats.ts` beside `fetchDashboard` / `saveDashboard` (the Stats chunk), not in `lib/api.ts` /
`lib/queries.ts`, which are first-visit code; `lib/api.ts` gains only the generic `apiDelete`. (2) Arrival loads
the URL's dashboard only when this browser tab has not loaded it already (a marker in memory, read first, and in
`sessionStorage` for a reload): as planned, every Stats remount (a tab switch, Back) and every reload would have
copied the stored charts over the working board's unsaved edits. It judges only the list as re-read since the
mount (`refetchOnMount: 'always'`), so a dashboard shared a minute ago is found, and it REPLACES the link's
history entry (a click still pushes), so Back leaves Browse instead of landing on a filterless copy of it. (3) "Uložit změny" opens the same dialog as "Update preset"
(name, colour, "Uložit i nastavení filtru" ticked iff the dashboard carries a filter); left alone it sends the
planned PUT, unticked it clears the filter (`filter_spec: null`), ticked on a filterless one it adds it.
(4) "Uložit jako vlastní" is one POST of the shared dashboard's stored charts, filter, name and colour, no dialog;
the copy becomes active only when the shared one was. (5) Own chips come first in their order, shared ones after
them (not interleaved by the owner's positions). (6) The bar is Browse-page only: the modals' `presetBar: false`
gates both saved-set bars, so the memory adapter's three dashboard members are no-ops. (7) The generic bar takes
`onReorder` with the own items in their new order, `isOwn` (drags, renames, deletes, updates) for the planned
`reorderable`, `extra` as a prop (the preset's box exists only while a map area is applied) and `suffix`; one hook
beside it, `useSavedSet`, reads an instance's list and persists a drag (own rows first, shared after) for both.
Delete and "Přestat sledovat" are the menu's two-step entries, as the preset's Delete… / Confirm delete were.
(8) `lib/stats.ts` keeps its `pgRead` import — `fetchStatsPivot` reads through it; only the board read left it.
(9) `fetchDashboard` now goes through the API, so a dev or preview build without `VITE_API_BASE_URL` shows the
board's error banner on Stats instead of the defaults (production always has the API). (10) "Own" is
`owner_email == null`, as on a collection; an account with no member email would read as the caller's own, an
edge no share can reach (a share needs a confirmed email). The fold of the preset bar (757 lines) into the bar,
its dialog and the preset instance came to 707 (956 as first cut), not ~600: a generic bar carries its words as
data, a shared chip and instance menu entries.

## Subtraction ledger (production lines, tests and docs excluded)

Planned: deleted the Table tab (~575, wave 1); `fetchDashboard`'s pgRead and the single-membership caveat (~20);
`PresetBar` + `PresetSaveModal` folded into one bar and one modal (757 → ~600, −157); `api/filter_presets.py` +
the CRUD routes folded into one module (286 → ~260, −26). Added: migration 647 (~250), the dashboards router (~120),
the shares arm (~40), the dashboard adapter + share modal (~260), notifications (~10). Net production ≈ −100; no flag,
no per-surface mode, one new table that is the third instance of an existing pattern.

Actual W3 (SPA production, after review): +1,212 / −805, net +407 — the bar, its dialog and the preset instance
are 707 lines where the preset bar and its dialog were 757 (−50, not −157); the dashboard instance with its share
dialog is 291; browseState, lib/stats, types and Notifications carry the rest. The sprint's net turns on W1's
Table removal and W2's API fold.

## Waves and merges

W1 Table removal (own PR, pure deletion) → W2 migration 647 + API (apply 647 from the branch before merge) → W3 SPA
(branched from W2, merged after it) → W4 docs: this file final, `stats-dashboard.md` ruling 2 amended,
`architecture.md` rule 16/18 share lines, `tenancy.md`, `GLOSSARY.md` ("Dashboard", "Working board"), `CLAUDE.md`
rule 16's producer line (`share` covers three kinds), `roadmap/ui-track.md`. Every merge in a window on the autodedup
session's green light; a read-only production check after each rollout.
