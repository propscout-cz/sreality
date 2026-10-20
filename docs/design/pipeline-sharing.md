# Pipeline sharing — view or edit another account's board, and act as its owner

Status: **v1.2, 2026-10-20 — rulings 1–6 and 8–11 in (§8); ruling 7 (the share notification) pending.** Delegates may manage stages; every delegated edit is stamped (actor + time) in one ledger; the phone may move cards on edit boards. Written from three code maps
(pipeline model + API, SPA surfaces, tenancy mechanics; Opus readers) and verified against the live
database (46 stages across 6 accounts, 180 cards, 1 collection share, 5 users). Sibling of
`docs/design/collection-sharing.md` (migrations 610/613, live since 2026-10-17).

## 1. North star

**Sharing a pipeline lets its recipient ACT AS the owner — on the owner's curation only, one request at a
time, through one identity function.** A share is one row, (owner account, recipient account, mode).
While acting, the recipient *is* the owner to the eleven curation tables (collections, tags, notes, files,
artifact links, dismissals, the pipeline) and to nobody else: billing, estimations, watchdog, presets,
notifications and the share tables stay the recipient's own. No surface, hook, route or policy learns a
second way to read or write curation; the acting identity rides a request header that the database
validates against the share row. Read-only mode is the same identity with writes refused by the
database. When a later decision is in doubt, choose the option that keeps "who am I acting as" in that
one function and that one header.

## 2. What exists today (verified 2026-10-20)

- **One board per account, no board id.** `pipeline_stages` (one entry stage, keys unique per account,
  `(account_id, id)` unique) + `property_pipeline` (PK `(account_id, property_id)`, composite FK onto the
  account's stages) + `property_pipeline_events` (append-only ledger, no actor column). Signup seeds
  the default board (migration 612). Policies: one `FOR ALL … account_id IN (select
  current_account_ids())` per table (294). Browser roles hold full DML on the base tables; the API
  adds the invariants (entry-stage placement, the ledger event, the dismissal lift, `board_position`).
- **Every read of the board is the caller's own.** The SPA reads `pipeline_stages_public`,
  `pipeline_board_public` and `property_pipeline_public` directly (security_invoker; **no `account_id`
  column**), keyed in React Query by `property_id` alone (`pipelineKeys.*`, `curationKeys.*`,
  `dismissalKeys.*` carry no account). Browse's curated-set scope (629) and the dismissal invariant
  (`api/dismissals.py`, uncorrelated by account on purpose) rely on RLS returning ONE account's rows.
  Consequence: **the collection-sharing shape (FOR SELECT read widening) cannot serve a board** — two
  boards would merge into one card map, two entry stages would collide, and the owner's cards would
  silently enter the recipient's Browse scope and dismissal rule.
- **Identity.** `current_account_ids()` (286) = the caller's `account_members` rows by JWT `sub`;
  27 tables' policies, one view, one function and two API projections read it. Writes resolve ONE
  account at the route edge: `require_account_id` (oldest membership) on 15 routes — the curation and
  pipeline writes, plus watchdog subscriptions and filter presets. `account_members.role` is written at
  signup and read by nothing. tenancy.md:266 states "a share is never membership".
- **The share precedent (610/613).** `collection_shares(collection_id, account_id=owner by trigger,
  recipient_account_id)`, one party policy, no browser INSERT (uuids are learnable), the route = a
  tenant UPDATE gate then a service-role write, `account_for_email` (confirmed emails only),
  `share_party_emails()` labels counterparties, one in-app notification per share
  (`source_kind='collection_share'`), the SPA tells mine from shared by `owner_email` (null iff mine).
  The SPA's `SharingBlock` (collection page), `ViewerLine`, `TwoStep` and the "Shared with me" sections
  exist once each; the live lane `tests/test_collection_shares_live.py` is a three-tenant template.
  Four copies of the `_scoped(sub)` test helper exist across the live lanes.
- **PostgREST** exposes every request's headers and method to SQL as the transaction-local GUCs
  `request.headers` (JSON) and `request.method` (PostgREST ≥ 9; Supabase runs ≥ 12). The API's
  `tenant_transaction` sets only `request.jwt.claims` today, as a bind parameter; it can set the two
  others the same way. supabase-js builders take a per-call `setHeader`; the SPA's one read seam is
  `pgRead()`, its one API seam `send()` (which already merges `opts.headers`).

## 3. The design

### 3.1 Data model — migration `640_pipeline_shares_and_acting.sql` (additive)

```sql
begin;
create table pipeline_shares (
  account_id           uuid not null references accounts(id) on delete cascade,   -- the OWNER (the board)
  recipient_account_id uuid not null references accounts(id) on delete cascade,
  mode                 text not null check (mode in ('read', 'edit')),
  created_at           timestamptz not null default now(),
  primary key (account_id, recipient_account_id),
  constraint pipeline_shares_not_self check (recipient_account_id <> account_id)
);
create index pipeline_shares_recipient_idx on pipeline_shares (recipient_account_id);
alter table pipeline_shares enable row level security;
revoke all on pipeline_shares from anon, authenticated;
grant select, delete on pipeline_shares to authenticated;          -- NO insert/update: the API writes the grant
create policy pipeline_shares_party on pipeline_shares for all to authenticated
  using ((account_id in (select current_account_ids()) or recipient_account_id in (select current_account_ids()))
         and not acting())
  with check (account_id in (select current_account_ids()));

-- The acting request: PostgREST and the API both expose the header and the method this way.
create function acting() returns boolean language sql stable set search_path = '' as $$
  select coalesce(current_setting('request.headers', true)::json ->> 'x-act-as', '') <> '' $$;

-- Who the caller acts FOR on curation: membership, or the owner named by the header when that owner
-- shared their pipeline with the caller (edit: always; read: reads only), or NOBODY when the header
-- names anyone else — a stale or forged header shows nothing, never the caller's own rows.
create function acting_account_ids() returns setof uuid language sql stable security definer set search_path = public as $$
  with me as (select am.account_id from account_members am
               where am.user_id = nullif(current_setting('request.jwt.claims', true)::jsonb ->> 'sub', '')::uuid),
       asked as (select nullif(current_setting('request.headers', true)::json ->> 'x-act-as', '')::uuid as id)
  select me.account_id from me, asked where asked.id is null
  union all
  select s.account_id from pipeline_shares s, asked
   where s.account_id = asked.id and s.recipient_account_id in (select account_id from me)
     and (s.mode = 'edit' or coalesce(current_setting('request.method', true), '') in ('GET', 'HEAD')) $$;
revoke execute on function acting(), acting_account_ids() from public, anon;
grant execute on function acting(), acting_account_ids() to authenticated;

-- The eleven curation policies switch identity function; nothing else does (ALTER, never drop: no window).
alter policy collections_tenant_rw on collections
  using (account_id in (select acting_account_ids())) with check (account_id in (select acting_account_ids()));
-- … tags, property_notes, collection_properties, property_tags, property_note_attachments,
--   property_artifact_links, property_dismissals, property_pipeline, pipeline_stages, property_pipeline_events
-- The collection share rows are the caller's OWN consent: invisible while acting (so the four
-- *_shared_read arms add nothing to an owner's view, and a delegate can neither see nor revoke the
-- owner's shares).
alter policy collection_shares_party on collection_shares
  using ((account_id in (select current_account_ids()) or recipient_account_id in (select current_account_ids()))
         and not acting());

-- Every delegated edit is stamped: one append-only ledger, one trigger function on the eleven tables,
-- no column on any curation table. Fires only while acting (ruling 5).
create table delegated_edits (
  id               bigint generated always as identity primary key,
  at               timestamptz not null default now(),
  actor_user_id    uuid not null,                                        -- the JWT sub
  actor_account_id uuid not null references accounts(id) on delete cascade,
  owner_account_id uuid not null references accounts(id) on delete cascade,
  relation         text not null,                                        -- 'property_notes', 'property_pipeline', …
  row_key          text not null,                                        -- the row's primary key, text
  op               text not null check (op in ('insert', 'update', 'delete'))
);
create index delegated_edits_owner_idx on delegated_edits (owner_account_id, at desc);
create index delegated_edits_row_idx on delegated_edits (relation, row_key, at desc);
alter table delegated_edits enable row level security;
revoke all on delegated_edits from anon, authenticated;
grant select on delegated_edits to authenticated;                         -- written by the trigger only
create policy delegated_edits_owner_read on delegated_edits for select to authenticated
  using (owner_account_id in (select current_account_ids()));            -- the OWNER reads what was done as them
create function stamp_delegated_edit() returns trigger language plpgsql security definer set search_path = public as $$
begin
  if acting() then
    insert into delegated_edits (actor_user_id, actor_account_id, owner_account_id, relation, row_key, op)
    select (current_setting('request.jwt.claims', true)::jsonb ->> 'sub')::uuid,
           (select account_id from current_account_ids() limit 1),
           (select account_id from acting_account_ids() limit 1),
           tg_table_name, coalesce(new, old)::text /* the PK, built per table */, lower(tg_op);
  end if;
  return coalesce(new, old);
end $$;
-- one AFTER INSERT OR UPDATE OR DELETE trigger per curation table (eleven), each handing its PK as row_key.

-- One label door for both share kinds (counterparties of every share the caller is party to).
create or replace function share_party_emails() … from collection_shares … union … from pipeline_shares … ;

-- One notification kind for both share kinds: 'share' (collection_id set for a collection, NULL for a pipeline).
alter table notification_dispatches drop constraint notification_dispatches_source_ck;  -- 'collection_share' arm → 'share'
update notification_dispatches set source_kind = 'share' where source_kind = 'collection_share';
-- assert block: RLS on; exactly the policies above with cmd + roles; acting() false without the header;
--   acting_account_ids() = current_account_ids() without the header; grants as above.
commit;
```

- **Why a function, not a column or a role:** the request says who it acts for; the row says who may.
  `current_account_ids()` stays the untouched definition of who the caller IS (billing, estimations,
  notifications, presets, shares, admin); `acting_account_ids()` is the definition of who the caller acts
  FOR on curation. Two functions, two questions, each asked in one place.
- **Replace, never union.** While acting, the eleven tables show the owner's rows and nothing of the
  caller's own — the card map, the stage list, the entry stage, the dismissal rule and Browse's scope
  all stay single-account by construction.
- **Read-only is the same identity with writes refused by the database:** `request.method` is set by
  PostgREST on every request and by the API on every tenant transaction, so a read grant acts on GET
  only; a POST/PATCH/DELETE under a read grant sees no rows (the API answers 404/403, a crafted
  PostgREST write touches nothing).
- **Fail closed:** a header naming an account that did not share, or whose share was revoked, yields an
  empty identity — the board reads empty and the SPA says "no longer shared with you".
- **No carrier entry** (the row names no property). **No actor column** unless ruled (§8-5).
- **CI stub:** nothing new (`auth.users` already carries what `account_for_email` needs).

### 3.2 The delegation rule, stated once

A caller with a pipeline share from owner O may send `x-act-as: O`. On that request, and only on the
eleven curation tables, the caller is O: reads under either mode, writes under `edit`. Everything else
answers as the caller. Without the header nothing changes.

### 3.3 Who can do what

| Action | Owner | Recipient (read) | Recipient (edit) | Anyone else |
|---|---|---|---|---|
| See the board in the Pipeline tab's switcher | own | yes, "{email} · jen ke čtení" | yes, "{email} · úpravy" | no |
| Move a card between stages / remove a card | yes | no (drag off) | yes, as O (desktop drag; phone stage sheet, ruling 6) | no |
| Add a property to the board (property page, funnel button) | yes | no | yes, while acting as O | no |
| Manage stages (create, rename, reorder, archive) | yes | no | yes, as O (ruling 3) | no |
| "Edit as pipeline owner" on a property page | — | no | yes, while O's board is the ACTIVE board (§8-4) | no |
| While acting: notes, collections, tags, files, artifact link, dismissal | — | — | as O (O's lists, O's rows, written as O) | — |
| While acting: O's estimations, watchdog, presets, billing, shares, notifications | — | never | never | — |
| Share the board with an email / change mode / revoke | yes | no | no | no |
| Leave | — | yes | yes | — |
| See who the board is shared with | all recipients | only themselves | only themselves | — |
| Be notified when a board is shared with them | — | one in-app notification | one | — |

### 3.4 API

- **Two write-account dependencies, by declaration:** `require_account_id` (unchanged: the caller's own
  account; **403 when the acting header is present**) stays on watchdog subscriptions and filter presets;
  `require_acting_account_id` (the one row of `acting_account_ids()`; 403 "no edit share from that
  account" when the header is present and the function returns nothing) goes on the curation writes,
  the pipeline-card writes AND the stage routes (ruling 3: a delegate manages the owner's stages as O). The route declares whether it can be delegated; a census pins that every
  `require_acting_account_id` route writes only the eleven tables.
- **`tenant_transaction`** sets `request.headers` (`{"x-act-as": …}` when the header is present, else
  absent) and `request.method` next to `request.jwt.claims`, as bind parameters. `tenant_conn` gains the
  optional `X-Act-As` header parameter. Every tenant READ route follows RLS unchanged.
- **`api/curation.py` projections:** `_is_mine` reads `acting_account_ids()`, so `owner_email` is null iff
  the row belongs to the acting identity.
- **One share module:** `api/shares.py` holds what the collection share route does today — resolve the
  confirmed email, the service-role grant write, the notification — parameterised by kind.
  `POST /pipeline/shares {email, mode}` upserts the grant (re-sharing changes the mode; a mode change
  notifies again only when it widens to edit); `DELETE /pipeline/shares/{account_id}` revokes or leaves
  (the party policy decides); `GET /pipeline/shares` → `{shared_with: [{account_id, email, mode}],
  shared_with_me: [{account_id, email, mode}]}` labelled through `share_party_emails()`.
  `POST /collections/{id}/shares` moves onto the same module (no behaviour change).
- **Notifications:** `SourceKind` value `collection_share` becomes `share` (one kind, one renderer);
  the message says what was shared: `'<owner> shared the collection "<name>" with you'` /
  `'<owner> shared their pipeline with you (view only|edit)'`.
- **Projections carry the stamp:** `_NOTE_PROJECTION`, the card read and `pipeline_board_public` /
  `pipeline_stages_public` gain `edited_by_email` + `edited_at` from the row's latest `delegated_edits`
  entry (null when the owner did it themselves), labelled through `share_party_emails()` (the actor's
  account is the owner's counterparty).
- **Unchanged:** the dismissal invariant SQL (correct under replace
  semantics), merge carriers (per card account), `POST /listings/lookup` (no header → own board),
  Browse RPCs, Watchdog, collection monitor.

### 3.5 SPA

- **One selection, one context, one option, one key segment.** The **active board** is an app-level
  selection made in the Pipeline tab's switcher and remembered per device (`localStorage`, the same
  shape as the desktop-view setting): "Můj pipeline" (default) or one board shared with me. `useActing()`
  returns `{account_id, email, mode} | null` from an `<ActingAs>` provider that two places mount: the
  Pipeline tab, always, when the active board is shared; the property page, when the active board is an
  EDIT share and "Upravit jako {email}" is on. The two transports take `actAs`: `request(path, {actAs})`
  adds the header, `pgRead(builder, {actAs})` calls `setHeader`. The curation and pipeline hooks (the
  W3/W5 hook family in `lib/collectionCache.ts`, `lib/pipelineReads.ts`, `useCardTags`, `useArtifactLinks`,
  `useDismissal`, `NotesRow`'s reads, `usePipelineCard`/`useOptimisticWrite`'s keys and pending index,
  `curationSettle`) prefix their query keys with the acting account, so the owner's rows never land in
  the caller's own caches and the two never collide. Browse, the Collections page, Notifications and the
  extension never act: a shared active board changes nothing there.
- **Pipeline tab:** the switcher above the kanban lists "Můj pipeline" plus every board shared with me,
  labelled `email · jen ke čtení | úpravy`; the choice is the active board. A shared board renders inside
  `<ActingAs>`: the columns are the owner's stages, the cards the owner's; read mode disables drag, the
  stage menu, the trash and the manage button; edit mode enables drag, the stage menu and the manage button (stages are created, renamed,
  reordered and archived as the owner). The owner's own board gets a **Sharing** block (email, mode, recipients with mode
  and a two-step "Stop sharing") — the collection page's `SharingBlock` generalised into one
  `ShareBlock`. A viewer gets the `ViewerLine` with a two-step "Remove from my list" (which also resets
  the active board to own). A revoked active board reads empty and resets to own with a toast.
- **Property page:** while the active board is an EDIT share, the header shows "Upravit jako {email}"
  on every property page (§8-4: the board need not hold the property, so a property can be ADDED to the
  owner's board from here). Switching it on wraps the header's curation controls (pipeline, save to
  collection, dismiss) and the curation block (tags, artifact link, notes) in `<ActingAs>`; a banner
  "Upravujete jako {email} · Ukončit" sits on the page, not in the 3.5 rem top bar. Everything inside
  reads and writes as the owner through the same components; nothing outside changes (estimations,
  Valuo, the map). The mode is sticky per device (§8-10): it stays on across property pages until
  "Ukončit", or until the active board changes back to "Můj pipeline" (which clears it).
- **Notifications page:** the `share` row renders for both kinds (one renderer; "Open collection" /
  "Open pipeline" link).
- **Phone:** the same per-device active board; a read board is read-only, an edit board moves cards
  through the stage sheet as the owner (ruling 6); no "Upravit jako" on the phone property page.
- **Stamps:** a note, card or stage last changed by a delegate shows "upravil {email} · {time}" (desktop
  "edited by") from the projection; the owner's own edits show nothing. **Extension:** unchanged (no header; its lookup and writes stay the caller's own).

### 3.6 Notes while acting

A note written while acting is the owner's note (owner's account, `owner_email` null to the owner).
The consent mark (`shared`) and the audience are the owner's, computed from the owner's shares. Notes
third parties shared with the DELEGATE do not appear while acting (`collection_shares` is invisible
under the header), so a page in acting mode shows exactly what the owner would see of their own
curation and nothing of the delegate's. Every delegated write is recorded in `delegated_edits` (who, as whom, which row, when; ruling 5): the
owner reads it, the delegate does not; items show their latest delegated edit.

### 3.7 What stays identity-scoped, by table

`accounts`, `account_members`, `entitlements`, `usage_ledger`, `admins`, `collection_shares`,
`pipeline_shares`, `share_party_emails()`, `notification_subscriptions`, `notification_dispatches`,
`filter_presets`, `stats_dashboards`, `estimation_runs`, `building_runs` and their four children,
`llm_calls`, `property_estimates_public`, `valuo_valuations`, `manual_rental_estimates`. These keep
`current_account_ids()`; a delegate acting as O can neither read nor write them as O, and O's quotas
and paid calls are never spent by a delegate.

### 3.8 Merges, splits, monitoring, the extension

Cards and curation stay owned by exactly one account, so the carriers, the sold-comps worker and merge
receipts are untouched. `delegated_edits.row_key` names rows, not properties, so it needs no carrier
entry; a merge that moves a note keeps the note's id and therefore its stamps. A split routes only the acting account's items (unchanged: a delegate on a
split route acts as the caller, not the owner — the merge routes are admin-only and never carry the
header). Collection monitoring and watchdog never act. The extension never acts.

## 4. Rejected

Read widening for boards (the collection-sharing shape): merges two boards into one keyed map, two entry
stages, and leaks the owner's cards into the recipient's Browse scope and dismissal rule · a persisted
"acting account" row: global across tabs, devices and the extension, stale after revoke · membership
(`account_members` row with role `member`): widens every one of the 27 tables and still writes to the
oldest membership · service-role delegation per route (tenancy shape 3): a second copy of every curation
read and write route, projections that break on the service role, the opposite of one path · a
`boards` table: an account has one board by construction; a share names the account · widening
`current_account_ids()` itself with "never while acting" restrictive policies on ~15 sensitive tables:
more objects than switching the eleven curation policies, and it muddles "who I am" with "who I act for".

## 5. Subtraction ledger

| Addition | Paid by (deletion) | Evidence |
|---|---|---|
| `pipeline_shares` + party policy | DROP `account_members.role` (641, destructive by class, ruling §8-8) — the dead placeholder for delegation | CLAUDE.md "written and read by nothing"; `grep -rn "\.role\b" api toolkit frontend` = 0 readers |
| `acting()`, `acting_account_ids()` | no new view, route-twin, RPC or SPA read path: eleven `ALTER POLICY`, zero new policies | §3.1 |
| `request.headers` + `request.method` GUCs in `tenant_transaction` | mirrors PostgREST's own shape — one function reads both paths | §2 |
| `require_acting_account_id` | — (a second dependency; declared per route; pinned by a census) | §3.4 |
| `api/shares.py`, `POST/DELETE/GET /pipeline/shares` | the collection share route's body moves into the module (one email resolver, one grant write, one notification); `source_kind` values −1 (`collection_share` → `share`, one renderer) | `api/curation.py:293-370` |
| `ShareBlock` | the collection page's `SharingBlock` (one component for both pages) | `pages/CollectionDetail.tsx` |
| `useActing`, the `actAs` transport option, the key segment | the four `_scoped` copies in the live lanes become one `tests/_tenant_scope.py` (with `act_as=`, `method=`) | test_tenant_isolation_live:355, collection_shares_live:34, tenant_ids_identity_live:81, curated_set_scope_live:58 |
| `delegated_edits` + `stamp_delegated_edit()` + eleven triggers | no column on any curation table; one ledger in the house's append-only event shape (like `property_pipeline_events`, `property_status_events`) | ruling 5 |
| `tests/test_pipeline_shares_live.py` | the collection live lane's three-tenant fixture becomes the shared fixture it already is in shape | — |

**Net, plainly.** Database objects +2 tables, +3 functions, +2 policies, +11 triggers, −1 column; routes +3, −0 (one body
moved); SPA +1 context, +1 transport option, −1 component copy; tests −3 helper copies. Production
lines will be net positive: a second share kind and an acting mode are new facts. The payment is
structural — nothing in the app learns a second way to read or write curation.

## 6. Waves

Merges wait for the autodedup session's green light (`autodedup.settings rt_bootstrap:rt` false and its
word). Migration numbers: 637 is on main, #1898 holds 638/639 → 640 (+641 for the drop) — next free at
merge.

- **W1 `feature/pipeline-shares-acting`** (database + API + doctrine). Migration 640; `tenant_transaction`
  GUCs; `tenant_conn` header; `require_acting_account_id` on the curation, pipeline-card and stage writes, the `delegated_edits` ledger + trigger + the stamp columns in the projections and the two board views (security_invoker restated),
  `require_account_id` refusing the header; `_is_mine`; `api/shares.py` with both kinds; `SourceKind`
  `share`; `tests/_tenant_scope.py` (one `_scoped`); `tests/test_pipeline_shares_live.py` (owner A,
  read recipient B, edit recipient C, bystander D: B reads A's board and nothing of their own while
  acting; B's writes see no rows; C moves A's card and the event lands in A's account; C writes a note,
  a tag, a membership and a dismissal as A and each lands one `delegated_edits` row readable by A only;
  C creates, renames, reorders and archives A's stages; C cannot read A's entitlements, presets, subscriptions,
  shares or estimations while acting; D's header yields nothing; a revoke denies on the next statement;
  `acting_account_ids()` equals `current_account_ids()` without the header on every tenant;
  `share_party_emails()` names both kinds' counterparties; no 42P17); the two-dependency census;
  `test_migration_rls_grants.py` policy pin for the new policy + the eleven ALTERs. Docs: tenancy.md
  (the delegation paragraph: identity vs acting, the eleven tables, the header, fail-closed), database +
  toolkit-api skills, architecture.md rules 16/18/22, GLOSSARY ("Pipeline share", "Acting as"), CLAUDE.md
  rules 18 + 22 one clause each. Verify: apply 640 before merge (nothing can create a share until the
  route deploys; the function equals today's behaviour without the header), one `execute_sql` check,
  merge, API status.
- **W2 `feature/pipeline-sharing-surfaces`** (SPA). §3.5 desktop + phone + Notifications; vitest for the
  switcher gating (drag/menu/trash/manage per mode), the key segment (own caches untouched while a
  shared board is open), the active-board selection (per device, reset on revoke/leave), the property-page button + sticky mode + banner, `ShareBlock`, the share row. Verify: vite
  status, then the operator's first real share.
- **W3 `cleanup/account-members-role`** — migration 641 drops `account_members.role` (ruling §8-8);
  CLAUDE.md "out of scope" line + tenancy.md updated. Separate PR, after W2.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| a route that should not act honours the header | two named dependencies; a census over every tenant write route (own vs acting) |
| a table that must stay identity-scoped reads `acting_account_ids()` by a later edit | the live lane asserts C cannot read A's entitlements/presets/subscriptions/shares while acting; the policy pin lists the eleven |
| the header reaches PostgREST but the function sees no `request.method` (older PostgREST) | the function treats a missing method as NOT a read: a read grant then acts on nothing (fails closed); W1's smoke proves the GUCs on the live gateway |
| two in-flight queries, own and acting, share a cache entry | every curation/pipeline key carries the acting segment; pending index too; vitest pins both |
| a delegate's edit is indistinguishable from the owner's | ruling §8-5 (an actor stamp on the pipeline ledger and notes) |
| a stale header after revoke | empty identity → the SPA shows "no longer shared"; nothing of the caller's own leaks into the acting view |
| email enumeration on share | same stance as collections: accepted for a closed platform |
| the top bar must stay 3.5 rem | the acting banner lives in the page, below the bar |

## 8. Rulings (2026-10-20: 1, 2, 4, 8, 9, 10, 11 as recommended; 3, 5, 6 as ruled below; 7 pending)

1. **Acting covers** **all eleven curation tables** (notes, collections + memberships, tags + memberships,
   note files, artifact link, dismissals, pipeline cards/stages read, pipeline events) / pipeline cards only.
2. **Read-only share** = **see the board only**; nothing on the property page / also "view as owner"
   (read the owner's notes, tags, collections on that property, no writes).
3. Stage management in edit mode — **RULED: delegates too** (2026-10-20).
4. **"Upravit jako {email}" appears on every property page while an edit-mode board is the active
   board** (the board need not hold the property, so you can add one to it from there) / only when
   that board already holds the property.
5. Attribution — **RULED: stamp the acting user and the time** (2026-10-20); built as the `delegated_edits`
   ledger (§3.1), shown on notes, cards and stages.
6. Phone — **RULED: the phone may move cards on edit boards** through the stage sheet (2026-10-20); no
   "Upravit jako" on the phone property page.
7. **Notification on share** (pending the operator's decision): **in-app row, like collections** (one
   `share` kind) / none.
8. **Drop `account_members.role`** (dead; delegation now lives in explicit shares): **yes (W3)** / keep.
9. **Co-recipients:** **do not see each other** (each sees only their own grant); the board shows no
   "who else" / list them.
10. **The mode is sticky per device:** once on, it stays on across property pages until "Ukončit" or
    until the active board goes back to "Můj pipeline" / it ends when you leave the property page.
11. **The active board is remembered per device** (like the desktop-view setting; survives reloads; the
    extension and other devices are unaffected) / per tab only.
