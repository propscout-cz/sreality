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
| scalar | none | `show_small` (681, default false): the saved "Malé vzorky"; the Okolí dashboard tab reads it |
| listed by | `GET /filter-presets` | `GET /stats/dashboards` (named own + shared; the working row never) |
| working state | the Browse URL | the working board, the unnamed row: `GET`/`PUT /stats/dashboard` |
| shared | no | `stats_dashboard_shares`, read-only |

The blob format of `filter_spec` is the preset's, verbatim: `{filters: ListingFilters minus the lens keys, sort}`,
read by `readPresetSpec`, captured by `filtersForPreset`, compared by `filtersEqualForPreset`, server-checked by the
same `_coded_chips` validator (`PresetSpec` in `api/schemas.py`). No second reader, no second writer.

## Schema — migration 647 (`647_saved_dashboards.sql`), additive

1. `stats_dashboards`: add `id uuid not null default gen_random_uuid()`; drop the `account_id` primary key; add
   `primary key (id)`; add `name text null check (name is null or length(name) between 1 and 200)`;
   `filter_spec jsonb null`; `position integer not null default 0`; `color text null` with 201's palette CHECK;
   `created_at timestamptz not null default now()`. Index `stats_dashboards_account_idx (account_id)`. Partial unique
   index `stats_dashboards_one_working_per_account on (account_id) where name is null` — the working board's upsert
   target. Existing rows (one per account, name NULL) become the working boards untouched. The old key is dropped by
   its catalog name. A name never crosses NULL: the `BEFORE UPDATE OF name` trigger `stats_dashboards_name_guard`
   (`guard_stats_dashboard_name()`) refuses a named row becoming the working board and the working board being named
   in place (the API does neither; it closes a direct PostgREST UPDATE). Data CHECKs so such a write cannot hand a
   recipient junk: `charts` a JSON array ≤ 256 KiB, `filter_spec` NULL or a JSON object ≤ 64 KiB. Table and column
   comments restated.
2. Policy `stats_dashboards_tenant_rw` stays as 626 wrote it (FOR ALL on `current_account_ids()`, never acting —
   pipeline-sharing §3.7 still holds: a delegate reads no dashboard). Add
   `stats_dashboards_shared_read FOR SELECT TO authenticated USING (name is not null and id in (select dashboard_id
   from stats_dashboard_shares))`. Writes stay owner-only (the SELECT policy widens nothing else).
3. `stats_dashboard_shares (dashboard_id uuid → stats_dashboards on delete cascade, account_id uuid = OWNER,
   recipient_account_id uuid, created_at; primary key (dashboard_id, recipient_account_id); CHECK not-self)`, index on
   the recipient. Owner stamped by a `BEFORE INSERT OR UPDATE OF dashboard_id` trigger `sync_account_id_from_dashboard`
   (610's shape) that also
   RAISES when the dashboard is unnamed: the working board is never shareable, by the database. RLS on; `revoke all
   from anon, authenticated; grant select, delete to authenticated`; policy `stats_dashboard_shares_party FOR ALL`
   using `(owner or recipient in current_account_ids()) and not acting()`, with check owner. No browser INSERT.
4. `share_party_emails()`: CREATE OR REPLACE with a third UNION arm over `stats_dashboard_shares` (641's body + one
   arm; still counterparties only).
5. The `share` notification needs no CHECK change (641:311 leaves `collection_id` NULL and `subscription_id` NULL for
   a non-collection share). Dedupe key: `share:dashboard:<dashboard_id>:<recipient>`.
6. Post-conditions in a DO block (house style): the PK is `id`, `account_id` NOT NULL; the partial unique index
   exists; the name, colour, charts, filter and not-self CHECKs exist; the shares PK is (dashboard_id,
   recipient_account_id) and its `dashboard_id` cascades from `stats_dashboards(id)`; the three policies exist with the
   pinned commands (the shared arm names `name IS NOT NULL`, the party policy `not acting()`); `share_party_emails()`
   names the new table; both triggers exist with their events (`stats_dashboard_shares_account_id_biu`,
   `stats_dashboards_name_guard` / `guard_stats_dashboard_name()`); no browser role executes either trigger function.

Rails that move: `tests/_tenant_registry.py` += `stats_dashboard_shares`;
`tests/test_migration_rls_grants.py::_SHARE_POLICY_PIN` += `stats_dashboard_shares_party` (all) and
`stats_dashboards_shared_read` (select); the same file's hidden-while-acting census += the shares table;
`tests/api/test_acting_routes_census.py::_NEVER_ACTS` += `POST /stats/dashboards` and
`POST /stats/dashboards/{dashboard_id}/shares`; `test_admin_route_coverage.py` reasons; `test_auth.py` JWT rows.
`tests/test_626_stats_dashboard.py` pins the 626 FILE text and stays green; `tests/api/test_stats_dashboard.py` is
rewritten for the new upsert (`ON CONFLICT (account_id) WHERE name IS NULL`).

## API — one module, two instances

`api/saved_sets.py` replaces `api/filter_presets.py` + the CRUD half of `api/routes/filter_presets.py`:

- A frozen `SavedSetKind(table, noun, id_param, blob_columns, create_in, update_in, scalar_columns, nullable,
  empty_on_read, projection_extra, row_scope, order_by, own_rows, refusals, groups)` descriptor. `blob_columns` names
  the jsonb columns (`filter_spec` for presets; `charts`, `filter_spec` for dashboards); each is validated by its type
  on the body models in `api/schemas.py`: `PresetSpec` (`_coded_chips`) and `Charts`
  (`toolkit.stats_registry.validate_board`, its refusal a plain-string 422). `scalar_columns` (migration 681) names
  plain-typed columns, selected, inserted, partially updated and returned exactly like the blobs minus the jsonb cast
  (presets: none; dashboards: `show_small`). `nullable` is the set a PUT's explicit `null` clears (`color`; a
  dashboard's `filter_spec` too); every other null keeps the stored value, so the API never writes `name = NULL`.
  `row_scope` applies to EVERY statement, not only the list (dashboards: `name IS NOT NULL`, so no by-id route reaches
  the working board). `order_by` is the list order. `empty_on_read` keeps the preset reader's `filter_spec or {}`.
  `own_rows` names the caller's own rows where RLS also shows shared ones (dashboards: `_IS_MINE_SQL`), for the
  position below.
- One implementation of list / get / create / update / delete / reorder, keyed by the descriptor: the same SQL shapes
  as before (`position = (SELECT COALESCE(MAX(position), -1) + 1 FROM <table>)`, for dashboards over the caller's own
  rows only (`own_rows`); `unnest ... WITH ORDINALITY` reorder, omitted-vs-null colour), the same response rows plus
  the dashboard projection extras. A PUT that changes nothing locks the row `FOR UPDATE`, which applies the UPDATE
  policy: a recipient's empty PUT is a 404 too.
- One `saved_set_router(kind, prefix)` factory emitting the six CRUD routes (`GET ""`, `POST ""` with
  `require_account_id`, `PUT /reorder` before `/{id}`, `GET/PUT/DELETE /{id}`), every route on `tenant_conn`, every
  id a uuid at the edge (a malformed one, e.g. from a hand-edited link, is a 422), each endpoint tagged with the SQL
  it runs (`runs_sql`, read by the acting-routes census, which cannot read built SQL off the source). The
  filter-presets router becomes `saved_set_router(PRESETS, "/filter-presets")` with identical paths, bodies and
  answers, so the SPA, the phone and `tests/api/test_filter_presets.py`'s behaviour pins hold (update a pin only
  where it names the old module).

Dashboards (`api/routes/stats_dashboards.py`, mounted like the presets router):

- `GET /stats/dashboards` → `{data: [...], total}`; rows = named rows the caller can read: own (RLS tenant policy)
  and shared (`stats_dashboards_shared_read`). Projection extras, `api/curation.py`'s shape: `owner_email` (NULL iff
  `account_id in current_account_ids()`, else `party_label`) and `shared_with` (json_agg of
  `{account_id, email}` over `stats_dashboard_shares`). `WHERE name IS NOT NULL`. ORDER: the caller's own
  dashboards first, then those shared with them, each group by `position, created_at desc`
  (`(account_id in current_account_ids()) DESC, position, created_at DESC`); the SPA splits the list the same way.
- `POST /stats/dashboards {name, charts, filter_spec?, show_small?, color?}` — the "Uložit jako dashboard" write;
  `charts` through `validate_board`, `filter_spec` through `_coded_chips`, `show_small` a boolean (default false);
  `require_account_id` (never acts).
- `PUT /stats/dashboards/reorder {ids}`; `GET/PUT/DELETE /stats/dashboards/{id}` (PUT: partial `name`, `charts`,
  `filter_spec` where an explicit `null` clears the filter, `show_small` where a `null` keeps it, `color`). A
  recipient's PUT/DELETE matches 0 rows → 404.
- Every dashboard row carries `show_small` (migration 681, a scalar column): the Stats tab's "Malé vzorky" switch
  (URL `small=1`, cells under the floor shown as their small-sample summary, migration 678), saved with the dashboard
  and read by the property page's Okolí dashboard tab, which draws that dashboard with its own setting. A recipient
  reads the owner's (it is part of what was shared). The working board's `GET`/`PUT /stats/dashboard` stay `{charts}`.
- `POST /stats/dashboards/{id}/shares {email}` → the ownership gate (`UPDATE stats_dashboards SET updated_at = now()
  WHERE id = %s AND account_id IN (SELECT current_account_ids()) AND name IS NOT NULL RETURNING name`), then
  `shares.grant(..., DashboardShare(id, name))` on the service role: insert `ON CONFLICT DO NOTHING RETURNING
  account_id`, then the dispatch with message `' shared the dashboard "<name>" with you'`, `collection_id` NULL,
  dedupe `share:dashboard:<id>:<recipient>`. Answer: the dashboard row (with `shared_with`).
- `DELETE /stats/dashboards/{id}/shares/{account_id}` — revoke (owner) or leave (recipient); the party policy decides;
  0 rows → 404.
- The working board: `GET /stats/dashboard` → `{id, charts, updated_at}` or `null` (the SPA then shows
  `DEFAULT_CHARTS`); `PUT /stats/dashboard {charts}` keeps its contract, upserting
  `ON CONFLICT (account_id) WHERE name IS NULL`. The GET is a plain tenant read under RLS with no account predicate
  (tenancy shape 1; under `X-Act-As` it returns the caller's own board, as dashboards never act, pipeline-sharing
  §3.7); the PUT keeps `require_account_id` (shape 2: a write names its one account). One membership per user makes
  the read's row the write's; a second membership would need the write's account resolution in the read. The
  single-membership caveat in `lib/stats.ts` goes with the pgRead.

`api/shares.py` gains `DashboardShare(dashboard_id, name)` beside the two kinds (one more `isinstance` arm in
`grant`, one delete SQL, one dispatch call); nothing else there changes. `api/notifications.py`'s projection gains
`CASE WHEN d.source_kind = 'share' THEN split_part(d.dedupe_key, ':', 2) END AS share_kind` and
`... split_part(d.dedupe_key, ':', 3) END AS share_ref` (collection id, owner uuid, dashboard uuid).

## SPA

- **`components/SavedSetBar.tsx`** — THE bar, generic: `{items, activeId, dirty, onLoad, onSaveNew, onUpdate,
  onRename, onDelete, onReorder, leading?, chipMenu?, copy: {empty, save, update, nameLabel, checkbox?: {label,
  initial}}}`. Drag-reorder (dnd-kit), the active chip, the dirty mark and the copper "update" button, the dashed
  "+ save" button, the save modal. `PresetBar.tsx` becomes `FilterPresetBar.tsx` = SavedSetBar + the preset adapter
  (queries, mutations, `loadPreset`, the Pipeline chip in `leading`). `PresetSaveModal.tsx` becomes
  `SavedSetSaveModal.tsx` with `checkbox` in place of `includeMapArea` (presets: "Include map area"; dashboards:
  "Uložit i nastavení filtru"). Behaviour of the preset bar is unchanged, pixel for pixel; its tests move with it.
- **No update button (2026-10-27, operator, both instances):** the copper "update" button and the update dialog are
  gone. A dirty chip's ⋯ menu leads with Save changes / Uložit změny (one click, no dialog: the set keeps its own
  name, colour and checkbox answer, so a preset keeps or lacks its map area and a dashboard its filter as saved) and
  Revert to saved / Zahodit změny (a reload of the stored set); a shared chip gets the revert only.
- **`collapse` (2026-10-22, presets only):** `{label, storageKey, show, fold}` folds the chips behind a
  "Presety 11" toggle; folded, only the active chip shows (eleven presets wrapped to three rows above the
  map). The fold is remembered per browser; the dashboards bar can take it with one prop.
- **`components/stats/DashboardBar.tsx`** = SavedSetBar + the dashboard adapter, mounted at the top of the Stats tab
  (the `StatsDashboard` charts below it). Items: `GET /stats/dashboards`. Own chips drag; a shared chip shows the
  owner's email as its suffix, does not drag, and its menu offers "Načíst", "Uložit jako vlastní" and
  "Přestat sledovat"; an own chip's menu offers "Přejmenovat", "Duplikovat" (a new row as stored, named
  "<name> (kopie)", the active dashboard untouched — operator ask 2026-10-22), "Sdílet…" (a small modal holding
  `ShareBlock`, no modes, `lang="cs"`) and "Smazat" (two-step).
- **The sample lens** (operator ask 2026-10-22; "Vše" 2026-10-23): a control "Vzorek" at the top of the Stats
  tab, "Jako ve filtru" (the Browse filter as set), "Prodáno (trh)" (`lib/stats.soldMarketFilters`: inactive only,
  seen from birth, dismissed included, 2–60 days on market) or "Vše" (`allMarketFilters`: active and inactive,
  dismissed included, no time-on-market bound); every other filter holds (`marketSample`). It applies to whatever
  dashboard is loaded, lives in the URL (`market=sold|all`, replace; `lib/statsMarket` keeps the name and guard
  out of the first-visit bundle) and is never stored with a dashboard; Browse's own filter, the dirty check and
  "Uložit změny" never see it.
- **The property page's "Okolí dashboard"** (operator asks 2026-10-23; migration 664): ONE own dashboard per
  account, under ANY name, is drawn by the property page's "Okolí dashboard" tab over that property's
  neighbourhood (`lib/neighbourhood`). It is a row flag, `stats_dashboards.property_card`: a partial unique index
  keeps one per account, and a CHECK keeps it named and filterless (the tab builds its own sample, and a stored
  filter would replace it when the explore window loads the dashboard). The API projects the flag as the
  caller's own only and moves it with `PUT /stats/dashboards/{id}/property-card` (one transaction: the previous
  one lets go, this one takes it and drops its filter; a shared or unknown id is a 404); a generic PUT that
  tries to give it a filter is a 422 (`SavedSetKind.refusals`). In the bar: its chip's suffix says
  "okolí dashboard"; the save dialog greys "Uložit i nastavení filtru" out for it, saying why; any other own
  dashboard's menu offers "Použít jako okolí dashboard" (asking first when it carries a filter); while the
  account has none, the bar leads with "+ Okolí dashboard", which saves the working board and marks it. It is
  edited like any dashboard (load, edit, "Uložit změny"), renamed freely, and deleting it leaves none.
  #1977's first cut found it by the name "Trh v okolí"; 664 marked that row.
- **Every Stats tab is the same tab** (operator ask 2026-10-22): the explore modals show the dashboard bar and the
  lens exactly as the Browse page does — one component, no `presetBar` gate; the modals' memory view state holds
  the active dashboard and the lens as the URL does, and "Go to Browse" carries both (`dashboard`, `market`).
- **Loading** (`onLoad`): ONE user action does two writes in order: `PUT /stats/dashboard {charts}` (copy into the
  working board, through `useOptimisticWrite` on the existing board cache), then the URL: if the dashboard carries
  `filter_spec`, `loadDashboard(d)` in `browseState.ts` writes the filters + sort exactly as `loadPreset` does
  (factor the shared body `applySavedFilter(spec, sp)` once; both callers use it), deletes `preset`, sets
  `dashboard=<id>`, pushed; without a filter it only sets `dashboard=<id>` (replace). `dashboard` joins
  `OVERLAY_URL_KEYS`; `activeDashboardId`, `setActiveDashboardId`, `loadDashboard` join `BrowseViewState`.
- **Arrival with `?tab=stats&dashboard=<id>`** (a notification link, a bookmark): the URL is the intent — the Stats
  tab loads that dashboard on mount when the working board's charts differ from it (one PUT), as a click would; a
  dashboard the caller can no longer read clears the param.
- **Dirty** = the preset's check: the working board's sanitized charts ≠ the active dashboard's, or (the dashboard
  has a filter and `!filtersEqualForPreset(current, stored)` or the current sort ≠ the stored one, the default sort
  when it stored none). "Uložit změny" = `PUT /stats/dashboards/{id} {charts: working, filter_spec: hadFilter ?
  {filters: filtersForPreset(current), sort: current} : undefined}`. "Uložit jako dashboard" = `POST` with the
  working charts and, when the checkbox is on, the current filter and sort. Both through `useOptimisticWrite`.
- **Working board I/O** (`lib/stats.ts`): `fetchDashboard` becomes `apiGet('/stats/dashboard')` (null → defaults);
  the pgRead and its single-membership comment go. `saveDashboard` unchanged.
- **Notifications**: `ShareNotificationRow` branches on `share_kind`: collection → "Open collection", pipeline →
  "Open pipeline", dashboard → "Open dashboard" → `/browse?tab=stats&dashboard=<share_ref>`.
- Phone: unchanged (presets keep `listFilterPresets`; no dashboards on the phone).
- Bundle: `DashboardBar` ships in the Stats chunk (lazy); the first-visit budget (330 KB gzip) must not grow — the
  Table tab's removal pays for anything the preset bar refactor adds.

## W2 as built (2026-10-20)

Migration 647 and the API, folded into the plan above; what they added beyond the sprint's first contract:

1. A name never crosses NULL: the `BEFORE UPDATE OF name` trigger `stats_dashboards_name_guard`
   (`guard_stats_dashboard_name()`) refuses a named row becoming the working board and the working board being named
   in place. The API does neither; the trigger closes a direct PostgREST UPDATE.
2. Data CHECKs: `charts` a JSON array ≤ 256 KiB, `filter_spec` NULL or a JSON object ≤ 64 KiB, so such a write cannot
   hand a recipient junk.
3. `stats_dashboards_shared_read` names `name is not null` beside the share: belt and braces, as the shares trigger
   already refuses a share of a working board.
4. The list's ORDER is own first, then shared, each by `position, created_at desc`; the plan ordered by position alone.
5. A PUT that changes nothing locks the row `FOR UPDATE`, which applies the UPDATE policy: a recipient's empty PUT is
   the 404 a real change would be, and nothing is bumped.
6. Every id is a uuid at the edge (a malformed one is a 422, not a 500), and every endpoint carries `runs_sql`, the
   SQL it runs, for the acting-routes census, which cannot read built SQL off the source.
7. `SavedSetKind` grew from `(table, blob_columns, projection_extra, list_where)` to carry `row_scope` (on EVERY
   statement, not only the list, so no by-id route reaches the working board), `nullable` (the fields a PUT's
   explicit null clears), `empty_on_read` (the preset reader's `filter_spec or {}`), `projection_extra`, `order_by`
   and `own_rows`, plus `noun`, `id_param`, `create_in`, `update_in`.
8. The working board's `GET /stats/dashboard` is a tenancy shape-1 read
   (`.claude/skills/database/references/tenancy.md`): RLS alone picks the row, with no account predicate; the first
   contract put the read on `require_account_id` beside the PUT.
9. A new row's `position` is `MAX(position) + 1` over the caller's OWN rows: RLS scopes the MAX and `own_rows`
   drops the dashboards shared with the caller, so a new one goes last among its own.
10. The share route's ownership gate is one helper, `shares.owner_gate`, which the collection share runs too; the
    saved set's 404 helper is `row_or_404()` (it was `found()`).

## W3 as built (2026-10-20)

Where it differs from the plan above: (1) the named dashboards' clients and their list key sit in `lib/stats.ts`
beside `fetchDashboard` / `saveDashboard` (the Stats chunk), not in `lib/api.ts` / `lib/queries.ts`, which are
first-visit code; `lib/api.ts` gains only the generic `apiDelete`. (2) Arrival loads the URL's dashboard only when
this browser tab has not loaded it already (a marker in memory, read first, and in `sessionStorage` for a reload): as
planned, every Stats remount (a tab switch, Back) and every reload would have copied the stored charts over the
working board's unsaved edits. It judges only the list as re-read since the mount (`refetchOnMount: 'always'`), so a
dashboard shared a minute ago is found, and it REPLACES the link's history entry (a click still pushes), so Back
leaves Browse instead of landing on a filterless copy of it. This changes the plan's meaning: the plan loaded whenever
the working board differed from the dashboard; as built, arrival loads once per browser tab. The operator may overrule
it. (3) "Uložit změny" opens the same dialog as "Update preset" (name, colour, "Uložit i nastavení filtru" ticked iff
the dashboard carries a filter); left alone it sends the planned PUT, unticked it clears the filter
(`filter_spec: null`), ticked on a filterless one it adds it. (4) "Uložit jako vlastní" is one POST, no dialog, under
the shared dashboard's name and colour. The active one is saved as shown: the working charts and, when it carries a
filter, the current filter and sort, as "Uložit změny" captures them (its dirty hint sends the recipient's edits
there). Any other one is saved as stored. The copy becomes active only when the shared one was. (5) Own chips come
first in their order, shared ones after them (not interleaved by the owner's positions). (6) Superseded 2026-10-22: every Stats tab shows the dashboard bar (the preset bar stays
Browse-page only). (7) The generic bar takes `onReorder` with the own items in their new order, `isOwn` (drags, renames, deletes,
updates) for the planned `reorderable`, `checkbox` as a prop (the preset's box exists only while a map area is
applied) and `suffix`; one hook beside it, `useSavedSet`, reads an instance's list and persists a drag (own rows
first, shared after) for both. Delete and "Přestat sledovat" are the menu's two-step entries, as the preset's Delete…
/ Confirm delete were. (8) `lib/stats.ts` keeps its `pgRead` import — `fetchStatsPivot` reads through it; only the
board read left it. (9) `fetchDashboard` now goes through the API, so a dev or preview build without
`VITE_API_BASE_URL` shows the board's error banner on Stats instead of the defaults (production always has the API).
(10) "Own" is `owner_email == null`, as on a collection; an account with no member email would read as the caller's
own, an edge no share can reach (a share needs a confirmed email). The fold of the preset bar (757 lines) into the
bar, its dialog and the preset instance came to 707 (956 as first cut), not ~600: a generic bar carries its words as
data, a shared chip and instance menu entries. (11) Inherited from PresetBar: shadows and the 0.65rem caption, against
system.md; a follow-up design pass may align both bars at once.

## Groups (migration 672, 2026-10-25)

**Operator ask (2026-10-25):** group the Browse presets: a group gets a bordered box, and a preset moves into a
box, between boxes, or back out.

A group is a saved set with no blob: `filter_preset_groups` (id, account_id, name 1..200, color under 201's
palette, position, created_at, updated_at; RLS `filter_preset_groups_tenant_rw` on `current_account_ids()`, never
acting). It is a second `SavedSetKind` (`GROUPS`, `blob_columns=()`) at `/filter-preset-groups`, with the six
factory routes. `filter_presets.group_id` (NULL = loose) is keyed `(group_id, account_id)` →
`filter_preset_groups (id, account_id)` `ON DELETE SET NULL (group_id)`. A preset can only name its own account's
group. Deleting a group leaves its presets loose and deletes none of them.

- **The kind option.** `SavedSetKind.groups` names the kind whose rows box this kind's. When it is set, every row
  carries `group_id`, the list answers `{data, total, groups}`, and the router adds `PUT <prefix>/layout`
  (`saved_sets.layout`). Dashboards declare no groups, so their SQL, rows and routes are unchanged.
- **One order.** A preset's `position` stays one order over all the account's presets. The layout write sends the
  loose row first, then each box in box order, so a surface that ignores groups (the phone's scope row) reads the
  desktop order. `PUT /filter-presets/layout {groups: [ids], items: [{id, group_id}]}` rewrites the box positions
  and every listed preset's position and group in one transaction, and answers with the canonical list. A group
  the account does not hold trips the key: a 422, and nothing moves.
- **The bar.** `SavedSetBar`'s optional `groups` prop (`SavedSetGroups`: items, words, `onLayout`, create / edit /
  delete) draws each group as a box after the loose chips. A chip drags into a box, between boxes and back out, as in
  dnd-kit's multiple-containers recipe. One `DndContext` holds the chips and the boxes, and a custom collision
  makes a dragged box consider boxes only. While a boxed chip is dragged, a "Drop here to ungroup" target ends the
  loose row. A box drags by its own grip. The chip menu offers "Move to <group>" and "Take out of group", which is
  the keyboard and touch route. Folded ("Presety N"), the bar shows the active chip alone, with no boxes. The
  arrangement logic is pure and unit-tested (`lib/savedSetLayout.ts`). `useSavedSet` takes `layout` and paints a
  drop optimistically (`applyLayout`).
- **Not built:** folding one box, boxes on the phone, and choosing a group in the save dialog. A new preset lands
  loose: at the end of the loose row on the desktop, last on the phone.

## Subtraction ledger (production lines, tests and docs excluded)

Planned: deleted the Table tab (~575, wave 1); `fetchDashboard`'s pgRead and the single-membership caveat (~20);
`PresetBar` + `PresetSaveModal` folded into one bar and one modal (757 → ~600, −157); `api/filter_presets.py` +
the CRUD routes folded into one module (286 → ~260, −26). Added: migration 647 (~250), the dashboards router (~120),
the shares arm (~40), the dashboard adapter + share modal (~260), notifications (~10). Net production ≈ −100.

Actual, production code only: W1 ≈ +353 / −821; W2 ≈ +763 / −354, of which the migration file is 345 added lines;
W3 ≈ +1,212 / −805. The sprint's net is ≈ +350 lines, and ≈ +0 to +50 without the migration file. **The ≈ −100
estimate was wrong.** The generic bar has to carry two instances' strings and menus as data (a shared chip, the
instance's menu entries), so the preset bar and its dialog (757) became a bar, a dialog and the preset instance of
707: −50, not −157. The API fold saved about 6 lines, not 26. The dashboard instance with its share dialog is 300;
browseState, lib/stats, types and Notifications carry the rest of W3. What holds: no flag, no per-surface mode, one
new table that is the third instance of an existing pattern.

## Waves and merges

W1 Table removal (own PR, pure deletion) → W2 migration 647 + API (apply 647 from the branch before merge) → W3 SPA
(branched from W2, merged after it) → W4 docs: this file final, `stats-dashboard.md` ruling 2 amended,
`architecture.md` rule 16/18 share lines, `tenancy.md`, `GLOSSARY.md` ("Dashboard", "Working board"), `CLAUDE.md`
rule 16's producer line (`share` covers three kinds), `roadmap/ui-track.md`. Every merge in a window on the autodedup
session's green light; a read-only production check after each rollout.
