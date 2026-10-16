# Collection sharing, view-only — spec + open rulings

Status: **proposal, 2026-10-16; rulings 2026-10-17 in §8** — notes model and share notification being revised to the rulings. Written from a seven-reader
code map, three independent designs and a three-lens judge panel (Opus), then verified by hand
against the code and the live database. Rulings are recorded in §8 as they land; the waves in §6
are the build order.

## 1. North star

**A share is one row: (collection, recipient account). It widens only what the recipient can
READ.** No write rule ever widens; the owner's one FOR ALL policy per table stays untouched. Every
surface tells "mine" from "shared with me" by ONE field, `owner_email`, which is null exactly when
the thing is yours. No new filter, page, view, flag or role. When a later decision is in doubt,
choose the option that keeps the share row plus the database's read policies as the ONLY place
sharing exists.

## 2. What exists today (verified live 2026-10-16)

- **Tenancy is per account.** `current_account_ids()` (migration 286) is the one definition of
  who is calling; every curation table (`collections`, `collection_properties`, `property_notes`,
  `property_note_attachments`) has one `FOR ALL` policy: `account_id IN (select
  current_account_ids())` for USING and WITH CHECK (migrations 290/292/592). 7 accounts (6 personal
  + system), 5 users, 15 collections (6 are the per-account system "monitoring" seed), 77 member
  rows, 102 notes (one account; **note #2 has `account_id` NULL** — a pre-tenancy row the backfill
  missed, carried once by a merge), 1 attachment.
- **Every read already runs under RLS.** The SPA reads the whole membership map once from the
  `security_invoker` view `collection_properties_public` (`lib/queries.ts` `fetchMemberSet`); that
  one map drives the card glyph, the phone curation row, the Pipeline board and the Browse
  `collections` prefilter (a property-id allowlist, OR over ids). The collection LIST comes from
  `GET /collections` on `tenant_conn`. Notes come from `GET /properties/{id}/notes`; the Browse note
  count reads `property_notes_public`.
- **Child rows take their account from the parent by trigger** (`sync_collection_properties_
  account_id`, migration 292, runs as the invoker). Today "a parent I can see" equals "a parent I
  own", so three write routes probe VISIBILITY where they mean OWNERSHIP (`update_collection` /
  `delete_collection` `_fetch_collection`, `add_properties_to_collection` `SELECT 1`). The moment a
  row is visible-but-not-owned those probes pass and the write then 500s (RLS WITH CHECK) or
  reports a false `{deleted:true}` (rowcount 0 swallowed).
- **Notes carry an author account but no label.** No tenant-readable path to another account's
  email exists (verified: no view or function in `public` reads `auth.users`); only
  `admin.get_entitlements` joins `auth.users`, on the service role. `accounts.name` is the signup
  email, never refreshed.
- **Alerts and merges need no change.** Collection-monitor dispatches are stamped to the
  collection owner's account (trigger, 292/364). Merge carriers move membership by
  `collection_id` and notes by property; neither looks at who may read.
- **Duplication sharing would multiply** (evidence in §5): 16 hand-typed `useQuery` sites over the
  collection list / member map in 12 SPA files; a second collection editor on the desktop property
  page (`CurationBlock` `CollectionsRow` + `CollectionToggle`) beside the header's
  `CollectionSaveToggle`; twin row mappers in `api/curation.py`; two views nobody reads
  (`collections_public`, `tags_public`); two mirrored tenant-table registries in tests.

## 3. The design

### 3.1 Data model — migration `607_collection_shares.sql` (additive)

```sql
begin;
-- 292's trigger body is generic over NEW.collection_id: reuse it, don't write a 9th copy.
alter function sync_collection_properties_account_id() rename to sync_account_id_from_collection;

create table collection_shares (
  collection_id        bigint      not null references collections(id) on delete cascade,
  account_id           uuid        not null references accounts(id)    on delete cascade, -- OWNER, trigger-stamped
  recipient_account_id uuid        not null references accounts(id)    on delete cascade,
  created_at           timestamptz not null default now(),   -- server truth: authenticated has no INSERT grant
  primary key (collection_id, recipient_account_id),
  constraint collection_shares_not_self check (recipient_account_id <> account_id)
);
create index collection_shares_recipient_idx on collection_shares (recipient_account_id);
create trigger collection_shares_account_id_bi before insert on collection_shares
  for each row execute function sync_account_id_from_collection();

alter table collection_shares enable row level security;
revoke all on collection_shares from anon, authenticated;
grant select, delete on collection_shares to authenticated;   -- NO insert: shares are created only by the API
create policy collection_shares_party on collection_shares for all to authenticated
  using      (account_id in (select current_account_ids()) or recipient_account_id in (select current_account_ids()))
  with check (account_id in (select current_account_ids()));

-- Reads widen, FOR SELECT only. The FOR ALL *_tenant_rw owner policies are NOT touched.
create policy collections_shared_read on collections for select to authenticated
  using (id in (select collection_id from collection_shares));
create policy collection_properties_shared_read on collection_properties for select to authenticated
  using (collection_id in (select collection_id from collection_shares));
create policy property_notes_shared_read on property_notes for select to authenticated
  using (exists (select 1 from collection_shares s
                   join collection_properties cp on cp.collection_id = s.collection_id
                  where s.recipient_account_id = property_notes.account_id
                    and cp.property_id        = property_notes.property_id
                    and property_notes.created_at >= greatest(s.created_at, cp.added_at)));
create policy property_note_attachments_shared_read on property_note_attachments for select to authenticated
  using (note_id in (select id from property_notes));

-- added_at becomes server truth too (precedent: 536's column-scoped grant; the API inserts only these two columns).
revoke insert on collection_properties from authenticated;
grant insert (collection_id, property_id) on collection_properties to authenticated;

-- ONE label definition (live auth email, signup-name fallback). Never browser-callable.
create function account_email(target uuid) returns text language sql stable set search_path = '' as $$
  select coalesce(
    (select u.email from public.account_members m join auth.users u on u.id = m.user_id
      where m.account_id = target order by m.created_at, m.user_id limit 1),
    (select a.name from public.accounts a where a.id = target)) $$;
revoke execute on function account_email(uuid) from public, anon, authenticated;

-- ci-allow-ungated: share_party_emails — counterparty-scoped labels only; pinned by a live equality test
create function share_party_emails() returns table (account_id uuid, email text)
language sql stable security definer set search_path = '' as $$
  select distinct p.id, public.account_email(p.id)
    from public.collection_shares s
    cross join lateral (values (s.account_id), (s.recipient_account_id)) p(id)
   where (s.account_id in (select public.current_account_ids())
       or s.recipient_account_id in (select public.current_account_ids()))
     and p.id not in (select public.current_account_ids()) $$;
revoke execute on function share_party_emails() from public, anon, authenticated;
grant  execute on function share_party_emails() to authenticated;
-- assert block: RLS on; exactly these 5 policies with the expected cmd and roles={authenticated}; grants as above.
commit;
```

Why this shape and nothing more:
- **Permissive policies OR together per command** (PostgreSQL, CREATE POLICY): a `FOR SELECT`
  policy adds rows to what SELECT returns and NEVER to what UPDATE/DELETE may touch — those "will
  only see rows that pass the UPDATE/DELETE policy's USING". So the recipient reads; the owner's
  `FOR ALL` policy still decides every write.
- **The trigger still fails closed.** A recipient inserting a member row now SEES the shared
  parent, so the trigger stamps the OWNER's account; WITH CHECK (`account_id IN
  current_account_ids()`) refuses it — 42501, nothing written. tenancy.md's "the lookup returns
  NULL" prose becomes "the lookup returns the owner"; same outcome, reworded in W4.
- **No recursion.** `collection_shares_party` reads only its own columns; every other policy
  points INTO `collection_shares` and never back, so 42P17 cannot occur.
- **No new or replaced view.** The `*_public` views inherit through their base tables, so the
  391/393 `security_invoker` hazard does not recur.
- **No carrier entry.** The share row names no property; both carrier censuses look only at
  property foreign keys (`tests/test_property_carriers.py`).
- **No role column** (view-only is the only role; a role column would be a flag — see what
  `account_members.role` costs: written once, read by nothing). **No stored email.**
- **CI stub:** `scripts/ci_db_bootstrap.sql`'s `auth.users(id, email)` gains
  `email_confirmed_at timestamptz` because the email lookup filters on it.

### 3.2 The visibility rule, stated once

- **Recipient R** reads collection C and its member rows iff a share (C → R) exists.
- **Owner O** reads a note (and its files) by author A on property P iff O shares some collection
  C with A, P is currently in C, and the note was written after both the share and P's joining C.
- Everything else stays owner-only exactly as today. **Writes never widen.**

### 3.3 Who can do what

| Action | Owner | Recipient | Anyone else |
|---|---|---|---|
| See the collection (list, Browse picker, page, member count) | yes | yes, labelled `name · owner@email` | no |
| Filter Browse by it (`?collections=<id>`) | yes | yes | no (empty) |
| Rename / description / delete / monitoring / add or remove properties | yes | 404 | 404 |
| Share with an email / revoke a recipient | yes | no (no re-share) | no |
| Leave ("Remove from my list") | — | yes | — |
| See who it is shared with | all recipients | only themselves | — |
| Receive collection-monitor alerts | yes | no | no |
| Write notes on its properties | yes (private) | yes, into their own account | yes (private) |
| Read a recipient's notes written after the share + membership | yes, marked `from b@x.cz` | own only | no |
| Read the owner's notes | own | **no** (default, ruling 1) | no |
| Edit / delete a note, add / remove its files | author only | author only | author only |

### 3.4 Email resolution and enumeration

- **Resolve on the service role** (the API's full-rights login; the browser never holds it), in
  one place: `tenant_pool.account_for_email(conn, email)`. Match `lower(u.email) = lower(%s) AND
  u.email_confirmed_at IS NOT NULL`; pick the oldest membership with the same `ORDER BY` constant
  `resolve_account_id` uses, so the share lands on the account the recipient writes notes under.
- **Store only the account uuid.** Labels resolve live through `account_email()`, so an email
  change shows everywhere at once.
- **Unknown or unconfirmed email → one 422: "No account uses this email."** Enumeration (learning
  which emails are registered from differing answers) is accepted on purpose: the caller is signed
  in and owns the collection, it is one request per guess, and the owner's own share list reveals
  the same fact. Revisit with a rate limit if signup opens.
- **Pending invites are out of scope** (a stored email, a signup hook, an invite surface —
  CLAUDE.md § out of scope).
- **`authenticated` gets no INSERT on shares.** Account uuids ARE learnable (verified:
  `manual_rental_estimates` is readable by every signed-in user under a `using (true)` policy and
  carries `account_id`), so a raw-uuid share through supabase-js must be impossible — otherwise
  `share_party_emails()` would turn a uuid into an email.

### 3.5 API (`api/curation.py`, wired in `api/main.py`)

**The write is the gate.** PostgreSQL applies the UPDATE/DELETE policy's USING to the rows a
write may touch, so a collection that is visible but not owned simply matches NO row: rowcount 0 →
404, before any side effect. This replaces the three visibility probes:
- `update_collection`: one `UPDATE collections SET … WHERE id = %s RETURNING is_system, name`
  (the system rename rule enforced in the SET via CASE, 409 raised inside the transaction so it
  rolls back); the two `_fetch_collection` probes go.
- `delete_collection`: `DELETE … WHERE id = %s RETURNING is_system`; 0 rows → 404; `true` → 409
  (rollback). The probe goes.
- `add_properties_to_collection`: its existing `UPDATE collections SET updated_at = now()` moves
  to the FRONT; 0 rows → 404; the `SELECT 1` goes.
- Share create: the same `updated_at` UPDATE on the tenant connection is the ownership gate.
- `add_attachment`: the probe becomes `SELECT f.sha256_hex FROM property_notes n LEFT JOIN
  property_note_attachments f ON f.note_id = n.id WHERE n.id = %s AND n.property_id = %s FOR
  UPDATE OF n` (FOR UPDATE is documented to apply the UPDATE policy), count + duplicate checks in
  Python, run BEFORE the R2 upload (today a foreign note would upload bytes and then fail).
- `UpdateNoteIn.body` becomes required; the empty-body branch and `_fetch_note` go.
- **No 42501 → 403 mapping**: a forgotten GRANT must keep paging as a 500.

**New routes** (both `tenant_conn`; the share INSERT is the second instance of tenancy.md's named
"short tenant transaction, then a service-role write" exception, `POST /estimations/{id}/feedback`):
```
POST   /collections/{collection_id}/shares            body {email}      -> Collection   (owner only)
  1 tenant:   UPDATE collections SET updated_at=now() WHERE id=%s        -> 0 rows: 404
  2 identity: account_for_email                                          -> none: 422 "No account uses this email."
  3 identity: INSERT INTO collection_shares (collection_id, recipient_account_id) ON CONFLICT DO NOTHING
              CheckViolation (self)                                      -> 422 "That's your own account."
  4 tenant:   re-read, return the Collection
DELETE /collections/{collection_id}/shares/{account_id}                -> {deleted}    (owner revokes OR recipient leaves)
  tenant: DELETE … ; the party policy's USING decides who may; 0 rows -> 404
```
`ShareCollectionIn{email}`: stripped, lower-cased, 3..320 chars, exactly one `@`; no dependency.
The uuid, not the email, keys the DELETE (an email in a URL lands in access logs).

**Shapes — one projection and one mapper each** (the twins `_to_collection`/`_to_collection_full`
and `_to_tag`/`_to_tag_full` go):
- **Collection** = today's fields + `owner_email: str | null` + `shared_with: [{account_id,
  email | null}]`. `owner_email` is null iff `c.account_id IN (select current_account_ids())` —
  null-iff-mine by construction, never by a failed label lookup; the label comes from `LEFT JOIN
  share_party_emails()`. On a shared row `monitoring_enabled` is `false` and `notify_channels`
  `[]`: they describe the CALLER (alerts go to the owner), which removes every recipient branch
  from the bell and pill marks. `shared_with`: the owner sees all recipients; a recipient sees only
  their own grant (`email: null` = you), whose `account_id` is the key for leaving.
- **Note** = `{id, property_id, body, created_at, updated_at, owner_email, attachments}`.
  `owner_email` null = mine. The legacy `origin_listing_id` leaves the payload and `CreateNoteIn`
  (the column stays: an R2-era carrier the server derives from the surrogate).
- `admin.get_entitlements` switches to `account_email(a.id)`, deleting its inline `auth.users`
  join and its duplicate rows for multi-member accounts.
- **FilterDef `collections`:** description only ("ids of collections the caller can read: own or
  shared"), then `python scripts/generate_filter_registry.py`. **No new filter.**
- **Unchanged, deliberately:** the collection-monitor producer, merge receipt, split preview, PDF
  and zip download, Browse RPCs 598/602, presets and Watchdog (`collections` is already excluded
  from both), `portal_lookup` SQL (now returns own + shared ids; the extension partitions them).

### 3.6 SPA and extension

**One predicate, one label, three hooks**, in `lib/collectionCache.ts`:
`isMine(x) = x.owner_email == null`; `collectionLabel(c)` = `name`, or `name · owner@email` for a
shared row (disambiguates same-named collections — every account has a "monitoring"; works on
touch); `useCollections() → {mine, shared, byId}`; `useCollectionMembers()` (the visible map,
unchanged); `useCollectionMembership(pid) → {mine, shared, saved}`, `undefined` until both reads
resolve so no wrong mark flashes. The 16 hand-typed `useQuery` copies become these calls.

- **Bookmark marks** (card glyph, phone curation row, margin marks, listing header, pipeline card)
  read `saved = mine.size > 0`: a property that sits only in someone else's collection never
  shows as "saved", and the mark always agrees with the save menu.
- **Cohort readers** keep the full visible map: the Browse prefilter, the Pipeline board, the
  phone roll's `stillHere`.
- **Save menu** (`CollectionSaveList`, inherited by the popover, the header button and the phone
  sheet): lists `mine` only — a view-only target is never offered, so no disabled variant.
- **Property page:** the second editor (`CurationBlock` `CollectionsRow` + `CollectionToggle`,
  ~120 lines) is deleted (ruling 6); the header `Uložit do kolekce` stays, as on the phone.
- **Browse, Kolekce picker:** same `collections` URL parameter; options grouped "Mine" /
  "Shared with me" (two `MultiselectChips` over one value array); chips use `collectionLabel`;
  selected ids no longer listed (revoked, deleted) render as clearable `#id` chips.
- **Collections page:** own rows unchanged plus "shared with N"; a "Shared with me" section reuses
  the same row with "Shared by {email}" and no Default / Delete / monitoring pill; the duplicate-
  name check runs over own names only.
- **Collection page, owner:** Edit, Monitoring, and a **Sharing** block (`<SectionLabel>`, which
  also replaces 4 hand-typed copies): an email field, "Share", the recipient list with a two-step
  "Stop sharing"; a 422 shows inline.
- **Collection page, viewer:** header "Shared by {email} · view only" with a two-step "Remove from
  my list"; Edit, Monitoring and the per-row Remove column hidden; the 404 copy adds "…or it is no
  longer shared with you".
- **Notes** (`NoteRow`, shared by desktop and phone). A foreign note: meta `{email} · {time}` and
  a matching aria-label; no Attach / Edit / Delete, no drop zone, file tiles without ×; a neutral
  left border from an existing token. **The composer's audience line** appears whenever the
  property is in a collection shared WITH me: "Visible to {owner} (shared collection {name})." —
  exactly when a new note will be visible. Note counts mean "notes I can read".
- **Phone** (Czech; managing shares is desktop-only, #1760 Q11): shared rows under "Sdílené se
  mnou" as "sdílí {email}"; detail header "sdílí {email} · jen ke čtení", no "Otevřít Nové"; 404
  copy "…smazána, nebo už s vámi není sdílená"; footer adds "sdílení"; audience line "Uvidí i
  {email} (sdílená sbírka {name})."
- **Extension** (vanilla TS, no new message type or endpoint): `ExtCollection` drops `is_system`
  (never read) and gains `owner_email`; `ExtNote` drops `origin_listing_id` (never read) and gains
  `owner_email`; the checklist lists `mine` only and `saved = |collection_ids ∩ mine|` replaces
  the double derivation; a foreign note shows "od {email}" with no edit/delete; the composer gets
  the same audience line (`collection_ids ∩ shared → owner emails`).

### 3.7 Notes: semantics and attribution

- **Author** = the account `require_account_id` stamps at creation (never the collection's).
- **Label** = the live auth email via `account_email()`, never stored.
- **Direction (default, ruling 1): one-way.** Recipients' notes reach the owner; the owner's notes
  stay private (an agent sharing a shortlist with a client never leaks "max offer"). Co-recipients
  never see each other's notes or emails.
- **Time-scoped, server-anchored.** Nothing becomes visible retroactively: both anchors
  (`collection_shares.created_at`, `collection_properties.added_at`) are stamped by the server (no
  INSERT grant on shares; column-scoped INSERT on members), so neither can be backdated through
  supabase-js. Editing an old note does not surface it.
- **Revocation hides, never deletes.** Revoke, leave, or removing the property from the collection
  hides the notes from the owner again; they stay with their author. Re-sharing or re-adding shows
  only notes written afterwards.
- **Attachments follow their note**, served through the existing API proxy.

### 3.8 Merges, splits, monitoring

- Shares are keyed on the collection, which a merge never changes. Membership is carried as the
  owner's rows; notes are carried by property (append), so visibility recomputes on the survivor.
  The split preview still shows only the acting account's items.
- **Documented edge, pinned by a live test:** the membership collapse keeps the SURVIVOR's
  `added_at`, so a recipient note written after the share on a duplicate property OUTSIDE the
  collection can become visible when an admin/autodedup merge folds that property into a member.
  Bounded by the share date; merges assert the same physical property; the reverse cases fail
  closed (a detach re-creating membership with `added_at = now()` hides older notes).
- **Monitoring is unchanged**: alerts stay stamped to the owner; a recipient never receives one;
  the masked fields stop the UI implying otherwise.

## 4. Rejected from the candidates (one line each)

Untimed note visibility (two designs): the owner controls both the share and the membership, so it
would harvest a recipient's whole note history · a `shared_note_ids()` definer: a correlated policy
does the same without an RPC surface · an INSERT grant on shares: uuids are learnable · `DELETE
/shares/{email}`: PII in access logs · a second label definition (`coalesce(email, uuid)`): one
`account_email()` · a second service-role connection on four hot read routes for labels: a
recurring pool cost held by convention · dropping `legacy_backfill_claim`: tenancy.md:134 forbids
it · dropping `is_system` before the code stops reading it: a migration-438-class outage ·
replacing the collection table with cards: an unrelated visible change · a recipient
notification, pending invites, an acceptance step: off the north star (ruling 5e).

## 5. Subtraction ledger

| Addition | Paid by (deletion) | Evidence |
|---|---|---|
| `collection_shares` + 1 party policy | DROP `collections_public`, `tags_public` (W2) | zero readers in api/, toolkit/, scraper/, scripts/, frontend/src, chrome-extension/src; only `tests/test_tenant_isolation_live.py:48,59,816,826` |
| trigger on shares | 292's function renamed and reused, no 9th copy | triggers bind by OID; only migration 292 names it |
| 4 FOR SELECT policies | no new read path, view, filter or endpoint — every reader inherits | GET /collections, the member map, the prefilter, get_collection, portal_lookup, notes, files |
| column-scoped INSERT on members | replaces the table-wide grant (net 0) | 292:33; `curation.py:243` inserts exactly these two columns |
| `account_email()` | `admin.get_entitlements`' inline `auth.users` join + its duplicate rows | `api/routes/admin.py:741-751` |
| `share_party_emails()` | no stored email, no second connection on read routes, no `accounts` widening | PII rail |
| the write-is-the-gate pattern | 3 visibility probes + `add_properties`' `SELECT 1` | `curation.py` `_fetch_collection` ×2, `delete_collection` SELECT, `:249-253`; fixes the false `{deleted:true}` and a 500 |
| `owner_email` on Note | `origin_listing_id` on the Note surface, `CreateNoteIn.origin_listing_id`, the COALESCE arm, the `sreality_id` prop chain (2 sites), `ExtNote.origin_listing_id` | no reader in frontend/src or the extension; written-but-never-read |
| `owner_email` on ExtCollection | `ExtCollection.is_system` + the double "saved" derivation | `chrome-extension/src/types.ts:102` unread; `index_cards.ts` |
| `owner_email` + `shared_with` on Collection | twin mappers `_to_collection`/`_to_collection_full`, `_to_tag`/`_to_tag_full` | `curation.py:619-633, 679-689` |
| 3 hooks + `isMine` + `collectionLabel` | 16 hand-typed `useQuery` copies + 5 Set derivations | 12 files (CollectionSaveToggle, Filters, CurationRow, MarginMarks, ListingCards, CurationBlock, CurationMarks, Pipeline, PhoneCollections, CollectionSaveMenu, Collections, PhoneCollectionDetail) |
| Sharing block + viewer branches | `CurationBlock` CollectionsRow + CollectionToggle (~120 lines, ruling 6); 4 `SectionLabel` copies; a duplicated bell SVG | `CurationBlock.tsx:123-250`; `CollectionDetail.tsx`; `Collections.tsx` ≈ `CollectionSaveMenu.tsx` |
| `UpdateNoteIn.body` required | empty-body branch + `_fetch_note` | `curation.py:389-393, 647-656`; both clients always send a body |
| the 23rd registry entry | the two mirrored registries become one `tests/_tenant_registry.py` imported by both tests | lists are identical today (22 each); the docstrings still say "20" |
| new share-route tests | the duplicated scripted-connection fake → `tests/api/_script_conn.py` | `test_artifact_link.py` `_Tx`/`_ScriptCur` ≈ `test_curation.py` |
| — | the NULL-owner variant on 6 curation tables (SET NOT NULL, W2; note #2 attributed first) | live: 0 NULLs except `property_notes` id 2 |
| — | dead `if TYPE_CHECKING: pass`, stale "service role"/"anon key" prose | `curation.py:14-20, 43-44`; `main.py:1420-1424`; `CurationBlock.tsx:10-14` |

**Net, plainly.** ≤ 0 on read paths (0 new), client special cases (one "is it mine" rule in one
place instead of 16) and the Note / ExtNote / ExtCollection field counts. **Not ≤ 0:** Collection
fields +2 (`is_system` would have paid for them; its removal is visible and deferred); database
objects +1 table, +5 policies, +2 functions against −2 views and −6 nullable variants; test lines
(the three-tenant proof). These are irreducible — the share is a genuinely new fact, the Sharing
control is what was asked for, and proving isolation is the price of putting sharing in the
database. Each PR body reports its measured diff.

## 6. Waves (one PR each; Opus subagents; CI is the gate; Railway status checked after each merge)

Migration numbers: 604 is the highest on disk and 603 is unclaimed by any open PR; 605+ is used
here. Renumber to the next free at merge time.

- **W1 `cleanup/curation-write-gate`** (backend, no migration, no behaviour change today). The
  write-is-the-gate rewrites; the attachment probe before the upload; one mapper each; body
  required; dead block + stale prose. Tests: `tests/api/test_curation.py` (404 before any INSERT/
  DELETE; the system rename/delete 409s roll back), `test_note_attachments.py` (probe before
  `upload_bytes`), new `tests/api/_script_conn.py` adopted by `test_artifact_link.py`. Docs:
  tenancy.md names the write-is-the-gate rule under shape 1.
- **W2 `cleanup/curation-schema-and-registry`.** `605_drop_dead_curation_views.sql` (destructive →
  operator OK, ruling 7; definitions saved in the PR, no data), `606_curation_account_id_not_null.sql`
  (preceded by one aggregate zero-NULL check per table and the one-row attribution of note #2 to
  its only candidate account — stop and report on any other NULL). `tests/_tenant_registry.py`.
  Apply via `apply_migration.yml`, one `execute_sql` check, then CI.
- **W3 `cleanup/curation-client-seams`** (SPA + extension + the API note surface; no visible
  change except ruling 6). The 3 hooks replace the 16 copies; CollectionsRow deleted; the legacy
  note origin removed across API, SPA, extension; `SectionLabel` + bell dedup. Tests:
  `collectionCache.test.ts`, `CurationBlock.test.tsx`, `test_curation.py`; extension `npm run
  type-check && npm run build`. Verify: vite status + a read-only production check.
- **W4 `feature/collection-shares`** (database + API + doctrine). `607_collection_shares.sql`; CI
  stub column; `account_for_email`; projections; both routes; entitlements on `account_email`;
  FilterDef description + regenerated file. Tests: new `tests/test_collection_shares_live.py`
  (three tenants A/B/C: B reads A's shared collection + members + count, C nothing; B's UPDATE/
  DELETE touch 0 rows; B's member INSERT raises 42501 with the OWNER stamped; B cannot see C's
  grant; B can leave; notes before the share / before membership / backdated stay hidden, later
  ones visible to A; files follow their note; A cannot edit B's note; `share_party_emails()` equals
  the visible party set for all three; anon has no EXECUTE, authenticated cannot run
  `account_email`; no 42P17 through any `*_public` view; the merge-collapse edge);
  `test_migration_rls_grants.py` (pinned policy census name → cmd, roles={authenticated};
  `'auth.users'` in `_ADMIN_ONLY_RELATIONS`); `test_curation.py`, `test_auth.py`,
  `test_admin_route_coverage.py`, `test_account_scope_census.py`, `test_admin_plans.py`. Docs in
  the same PR: tenancy.md (the trigger passage, the second named exception, the `auth.users`
  touchpoints, "a share is never membership"), database SKILL summary, architecture.md rule 18,
  GLOSSARY ("Share"; Collection "may be shared read-only"), CLAUDE.md rule 18 clause, toolkit-api
  route list, `api/curation.py` docstring. Verify: apply 607 BEFORE merge (safe: no share can exist
  until the route deploys), one `execute_sql` policy/privilege check, confirm Supabase →
  Authentication → Email → "Confirm email" is ON (all 5 users carry `email_confirmed_at` today,
  which auto-confirm would also set), merge, API status.
- **W5 `feature/collection-sharing-surfaces`** (SPA, §3.6 desktop + phone). Vitest for the NoteRow
  foreign variant, own-only CollectionSaveList, picker grouping + `#id` chips, SharingBlock copy,
  PhoneCollections split. Docs: architecture.md phone paragraph; roadmap/ui-track.md. Verify:
  vite status + read-only production check.
- **W6 `feature/collection-sharing-extension`.** Owner filter, saved intersection, foreign notes
  read-only, audience line. Type-check, build, headless harness; ship `dist`. Docs:
  roadmap/operator-workflow-track.md done; ROADMAP index cell. Then the operator makes the first
  real share; I only read.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A later "simplification" re-introduces a SELECT probe before a write (visibility ≠ ownership) | W1 tests assert 404 before any write; a one-line WHY beside each gate; named in tenancy.md |
| A shared-read policy written FOR ALL or without `TO authenticated` silently grants writes or reaches anon | pinned policy census + the migration's assert block |
| `share_party_emails()` restates the party rule; drift mislabels | live equality test against the visible share rows for all three tenants |
| merge-collapse edge exposes a post-share note from a duplicate's property | admin-only merges; bounded by the share date; documented; live test |
| email enumeration | accepted for a closed platform (5 users); rate limit if signup opens |
| a squatter registers someone's address | confirmed-only match + the W4 "Confirm email ON" check |
| unsolicited shares clutter a list; a hostile owner shares >100k properties and breaks the recipient's member map (`expectMax`) | Leave; 5 users; watch the map size |
| per-note subquery on the whole-set note-count read | index-backed (share PK, `collection_properties_by_property`); 102 notes; timed in the live test |
| a recipient's own dismissals hide shared properties in Browse | consistent with own collections; documented |
| caches 30 s stale after a revoke | clean 404 copy; clearable `#id` chip |
| Collections nav plan-gated, Browse picker not | same posture as own collections today; flagged, not changed |
| owner-only columns readable raw through supabase-js on a shared row | RLS hides rows, not columns; not secrets; the API masks them |
| migration number collisions with long drafts | next free number at merge |

## 8. Rulings

Recorded 2026-10-17 from the operator's answers to the eight questions below. Settled: **3** (same
Kolekce picker, "Shared with me" group), **4** (plain "No account uses this email"), **5 a–d, f, g**
(leave; recipients unseen to each other; no alerts for recipients; no re-share; revoke deletes the
row; the system collection is shareable), **7** (drop the two views — verified zero readers outside
tests, so no user impact), **8** (follow each page's language). **Revised, awaiting one-word
confirmation:** **1 + 2** — the operator wants to mark THEIR OWN notes as shared so recipients see
them, unmarked notes staying private; proposed as ONE symmetric rule, "a note crosses a share only
when its author marks it shared" (both directions, default private, files follow the note), which
replaces the time rule and the two server-stamped anchors. **5 e** — the operator WANTS a
notification on share: proposed as an in-app `notification_dispatches` row for the recipient
(`source_kind='collection_share'`, `change_kind='shared'`, the collection as subject, written in
the share route's transaction; no email, since per-account external channels are not built — the
outbox resolves ONE operator-wide address). **6** — needs a clearer description before ruling (the
property page's "Collections" chip row under the description vs the header's "Uložit do kolekce"
button: two editors of the same membership).

### The questions as asked (bold = the recommended default at the time)

1. **Notes direction + time rule.** **One-way, time-scoped**: a recipient's notes written after
   the share (and after the property joined) reach the owner, marked with the recipient's email;
   the owner's notes stay private. Alternatives: two-way (owner's post-share notes reach recipients
   too), or untimed (every note on a member property — rejected by all three judges as a harvest).
2. **Files on a recipient's note** visible to the owner with the note: **yes** / no.
3. **Browse "view shared collection".** **The same Kolekce picker with a "Shared with me" group** /
   a separate control.
4. **Email with no account.** **Plain "No account uses this email"** / a vague answer.
5. **Scope bundle** — **yes to all**, or amend by letter: (a) a recipient can leave; (b) recipients
   don't see each other; (c) no alerts for recipients; (d) no re-sharing; (e) no acceptance step
   and no notification on share — the composer's audience line + Leave are the consent mechanics;
   (f) revoking deletes the share row, no access history; (g) the system "monitoring" collection
   can be shared like any other.
6. **Property page:** delete the collection chip row under the description (the header
   `Uložit do kolekce` stays, as on the phone): **delete** / keep.
7. **Drop the unused views** `collections_public`, `tags_public`: **yes** / no.
8. **Wording.** **Follow each page's language** (desktop collection pages + Browse sidebar in
   English; phone + extension in Czech with "Sdílené se mnou" / "sbírka") / Czech everywhere.
