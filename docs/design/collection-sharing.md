# Collection sharing, view-only — spec (v2, rulings in)

Status: **approved 2026-10-17** — the operator ruled on every question (§8); W1–W6 run on this text.
W1–W3 merged; W4 in PR #… (`feature/collection-shares`); migration 609 (#1829/#1830 renumber past it).
Written from a seven-reader code map, three independent designs and a three-lens judge panel (Opus),
then verified by hand against the code and the live database, then revised to the rulings.

## 1. North star

**A share is one row: (collection, recipient account). It widens only what the recipient can
READ.** No write rule ever widens; the owner's one FOR ALL policy per table stays untouched. Every
surface tells "mine" from "shared with me" by ONE field, `owner_email`, null exactly when the thing
is yours. A note crosses a share only when its AUTHOR marks it shared. No new filter, page, view,
feature flag or role. When a later decision is in doubt, choose the option that keeps the share row,
the note's own `shared` mark and the database's read policies as the ONLY places sharing exists.

## 2. What exists today (verified live 2026-10-16)

- **Tenancy is per account.** `current_account_ids()` (migration 286) is the one definition of
  who is calling; every curation table (`collections`, `collection_properties`, `property_notes`,
  `property_note_attachments`) has one `FOR ALL` policy, `account_id IN (select
  current_account_ids())` for USING and WITH CHECK (migrations 290/292/592). 7 accounts (6 personal
  + system), 5 users, 15 collections (6 are the per-account system "monitoring" seed), 77 member
  rows, 102 notes in one account (**note #2 has `account_id` NULL** — a pre-tenancy row the backfill
  missed, carried once by a merge), 1 attachment.
- **Every read already runs under RLS.** The SPA reads the whole membership map once from the
  `security_invoker` view `collection_properties_public` (`lib/queries.ts` `fetchMemberSet`); that
  one map drives the card glyph, the phone curation row, the Pipeline board and the Browse
  `collections` prefilter (a property-id allowlist, OR over ids). The collection LIST comes from
  `GET /collections` on `tenant_conn`; notes from `GET /properties/{id}/notes`; the Browse note count
  from `property_notes_public`.
- **Child rows take their account from the parent by trigger** (`sync_collection_properties_
  account_id`, migration 292, runs as the invoker). Today "a parent I can see" equals "a parent I
  own", so three write routes probe VISIBILITY where they mean OWNERSHIP (`update_collection` /
  `delete_collection` via `_fetch_collection`, `add_properties_to_collection` via `SELECT 1`). Once a
  row is visible-but-not-owned those probes pass and the write 500s (RLS WITH CHECK) or reports a
  false `{deleted:true}` (rowcount 0 swallowed).
- **Notes carry an author account but no label and no audience.** No tenant-readable path to
  another account's email exists (verified: nothing in `public` reads `auth.users`); only
  `admin.get_entitlements` joins `auth.users`, on the service role. `accounts.name` is the signup
  email, never refreshed.
- **Notifications are one event table.** `notification_dispatches` has three producers
  (`watchdog`, `collection_monitor`, `system_health`; `source_ck` + `change_kind_ck`), the row IS
  the in-app notification, `account_id` is stamped by trigger 364 (watchdog → the subscription's
  account, collection_monitor → the collection's OWNER, else SYSTEM). `system_health` rows have no
  property subject and render as a text row. External delivery (`channel_sends`) resolves ONE
  operator-wide address (`notification_email_to`), so per-recipient email is not built.
- **Alerts and merges need no change.** Collection-monitor dispatches are stamped to the owner.
  Merge carriers move membership by `collection_id` and notes by property; neither reads shares.
- **Duplication sharing would multiply** (evidence in §5): 16 hand-typed `useQuery` sites over the
  collection list / member map in 12 SPA files; a second collection editor on the desktop property
  page (`CurationBlock` `CollectionsRow` + `CollectionToggle`) beside the header's
  `CollectionSaveToggle`; twin row mappers in `api/curation.py`; two views nobody reads
  (`collections_public`, `tags_public`); two mirrored tenant-table registries in tests; six copies
  of the notification `source_kind` `Literal`.

## 3. The design

### 3.1 Data model — migration `609_collection_shares.sql` (additive; 609 is the next free number on main — #1829/#1830 renumber past it)

```sql
begin;
-- 292's trigger body is generic over NEW.collection_id: reuse it, don't write a 9th copy.
alter function sync_collection_properties_account_id() rename to sync_account_id_from_collection;

create table collection_shares (
  collection_id        bigint      not null references collections(id) on delete cascade,
  account_id           uuid        not null references accounts(id)    on delete cascade, -- OWNER, trigger-stamped
  recipient_account_id uuid        not null references accounts(id)    on delete cascade,
  created_at           timestamptz not null default now(),
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

-- The author's mark. Default private; the author flips it; RLS reads it. shared_at = when the
-- mark last turned on (trigger-stamped; NULL while private; an edit never moves it).
alter table property_notes add column shared boolean not null default false;
alter table property_notes add column shared_at timestamptz;
create function stamp_property_notes_shared_at() returns trigger ...  -- on: now(); off: NULL; else keep
create trigger property_notes_shared_at_biu before insert or update on property_notes
  for each row execute function stamp_property_notes_shared_at();

-- Reads widen, FOR SELECT only. The FOR ALL *_tenant_rw owner policies are NOT touched.
create policy collections_shared_read on collections for select to authenticated
  using (id in (select collection_id from collection_shares));
create policy collection_properties_shared_read on collection_properties for select to authenticated
  using (collection_id in (select collection_id from collection_shares));
-- A shared note is readable by every party of a share whose collection holds the property and whose
-- OTHER side is the author. collection_shares is RLS-filtered to the caller's own party rows, so
-- "author is a party" means "author is the counterparty" for anyone but the author (own policy).
-- An author who RECEIVED the share never chose that audience (no acceptance step), so that arm
-- counts only shares that already existed when the author marked the note.
create policy property_notes_shared_read on property_notes for select to authenticated
  using (shared and exists (
          select 1 from collection_shares s
            join collection_properties cp on cp.collection_id = s.collection_id
           where cp.property_id = property_notes.property_id
             and (property_notes.account_id = s.account_id
                  or (property_notes.account_id = s.recipient_account_id
                      and s.created_at <= property_notes.shared_at))));
create policy property_note_attachments_shared_read on property_note_attachments for select to authenticated
  using (note_id in (select id from property_notes));

-- The share notification: a fourth event kind, the collection as subject, stamped to the RECIPIENT.
alter table notification_dispatches drop constraint notification_dispatches_source_ck;
alter table notification_dispatches add constraint notification_dispatches_source_ck check (
     (source_kind = 'watchdog'           and subscription_id is not null and collection_id is null)
  or (source_kind = 'collection_monitor' and collection_id   is not null and subscription_id is null)
  or (source_kind = 'collection_share'   and collection_id   is not null and subscription_id is null)
  or (source_kind = 'system_health'      and subscription_id is null     and collection_id is null));
alter table notification_dispatches drop constraint notification_dispatches_change_kind_ck;
alter table notification_dispatches add constraint notification_dispatches_change_kind_ck
  check (change_kind in ('new','price_drop','price_rise','inactive','reactivated','new_source','broker_change','system_alert','shared'));
create or replace function sync_notification_dispatches_account_id() returns trigger language plpgsql as $$
begin
  if new.source_kind = 'watchdog' then
    select account_id into new.account_id from notification_subscriptions where id = new.subscription_id;
  elsif new.source_kind = 'collection_monitor' then
    select account_id into new.account_id from collections where id = new.collection_id;
  elsif new.source_kind = 'collection_share' then
    if new.account_id is null then raise exception 'collection_share dispatch needs its recipient account'; end if;
  else
    new.account_id := '00000000-0000-0000-0000-000000000000';
  end if;
  return new;
end $$;
-- (tenants hold no INSERT grant on notification_dispatches — 292 grants read + update only — so the
--  supplied account can only come from the service-role share route.)

-- ONE label definition (live auth email, signup-name fallback). Never browser-callable.
create function account_email(target uuid) returns text language sql stable set search_path = '' as $$
  select coalesce(
    (select u.email from public.account_members m join auth.users u on u.id = m.user_id
      where m.account_id = target order by m.created_at, m.user_id limit 1),
    (select a.name from public.accounts a where a.id = target)) $$;
revoke execute on function account_email(uuid) from public, anon, authenticated;

-- counterparty-scoped labels only (auth.users only through account_email); pinned by a live equality test
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
  only see rows that pass the UPDATE/DELETE policy's USING". The recipient reads; the owner's
  `FOR ALL` policy still decides every write.
- **The trigger still fails closed.** A recipient inserting a member row now SEES the shared
  parent, so the trigger stamps the OWNER's account; WITH CHECK refuses it — 42501, nothing written.
  tenancy.md's "the lookup returns NULL" prose becomes "the lookup returns the owner" (W4).
- **No recursion.** `collection_shares_party` reads only its own columns; every other policy
  points INTO `collection_shares` and never back, so 42P17 cannot occur.
- **No new or replaced view.** The `*_public` views inherit through their base tables, so the
  391/393 `security_invoker` hazard does not recur.
- **No carrier entry.** The share row names no property; both carrier censuses look only at
  property foreign keys. `property_notes.shared` + `shared_at` ride the note through a merge, and a
  split's copy writes both (`toolkit/property_carriers.py` `_INSERT_SQL`).
- **`shared` is a fact the author states about one row, not a feature flag.** It replaces an
  implicit time-gated rule (two server-stamped anchors, a column-scoped grant, a merge edge) with
  explicit consent, one predicate. Its one timestamp, `shared_at`, dates the consent: a share the
  author RECEIVED counts only if it existed when the author marked the note (review 2026-10-17,
  see §7; shares need no acceptance, so otherwise anyone knowing an author's email could make
  themselves an audience of every note that author had marked for someone else).
- **No role column; no email stored in the share model** (labels resolve live). The one snapshot
  is the share notification's `message` (the owner's email at share time, §3.5).
- **CI stub:** `scripts/ci_db_bootstrap.sql`'s `auth.users(id, email)` gains
  `email_confirmed_at timestamptz` because the email lookup filters on it.

### 3.2 The visibility rule, stated once

- **Recipient R** reads collection C and its member rows iff a share (C → R) exists.
- **Party X** reads a note by author A on property P iff the note is marked `shared`, and some share
  between X and A names a collection that currently holds P — a share A OWNS (A chose X), or a
  share A RECEIVED that already existed when A marked the note (`shared_at`). Files follow the
  note.
- Everything else stays owner-only exactly as today. **Writes never widen.**

### 3.3 Who can do what

| Action | Owner | Recipient | Anyone else |
|---|---|---|---|
| See the collection (list, Browse picker, page, member count) | yes | yes, labelled `name · owner@email` | no |
| Filter Browse by it (`?collections=<id>`) | yes | yes | no (empty) |
| Rename / description / delete / monitoring / add properties | yes | 404 | 404 |
| Remove properties | yes | no-op `{removed: false}` (the DELETE matches no row; nothing written) | same |
| Share with an email / revoke a recipient | yes | no (no re-share) | no |
| Leave ("Remove from my list") | — | yes | — |
| See who it is shared with | all recipients | only themselves | — |
| Be notified when it is shared | — | one in-app notification per share | — |
| Receive collection-monitor alerts | yes | no | no |
| Write notes on its properties | yes (private by default) | yes (private by default) | yes (private) |
| Mark / unmark own note as shared | author | author | author (no audience yet) |
| Read the other party's SHARED notes on its properties | yes, labelled `b@x.cz` | yes, labelled `owner@email` | no |
| Read another recipient's notes | — | **no** (5b) | — |
| Edit / delete a note, add / remove its files | author only | author only | author only |

### 3.4 Email resolution and enumeration

- **Resolve on the service role** (the API's full-rights login; the browser never holds it), in
  one place: `tenant_pool.account_for_email(conn, email)`. Match `lower(u.email) = lower(%s) AND
  u.email_confirmed_at IS NOT NULL`; pick the oldest membership with the same `ORDER BY` constant
  `resolve_account_id` uses, so the share lands on the account the recipient writes notes under.
- **Store only the account uuid.** Labels resolve live through `account_email()`.
- **Unknown or unconfirmed email → one 422: "No account uses this email."** (ruling 4). Enumeration
  is accepted on purpose for a closed platform: the caller is signed in and owns the collection,
  one request per guess, and the share list reveals the same fact. Revisit if signup opens.
- **Pending invites are out of scope** (CLAUDE.md § out of scope).
- **`authenticated` gets no INSERT on shares.** Account uuids ARE learnable (verified:
  `manual_rental_estimates` is readable by every signed-in user under `using (true)` and carries
  `account_id`), so a raw-uuid share through supabase-js must be impossible — otherwise
  `share_party_emails()` would turn a uuid into an email.

### 3.5 API (`api/curation.py`, wired in `api/main.py`)

**The write is the gate.** PostgreSQL applies the UPDATE/DELETE policy's USING to the rows a write
may touch, so a collection that is visible but not owned matches NO row: rowcount 0 → 404, before
any side effect. This replaces the three visibility probes:
- `update_collection`: one `UPDATE collections SET … WHERE id = %s RETURNING is_system, name`, the
  system rename rule enforced in the SET (`name = CASE WHEN is_system THEN name ELSE … END`), 409
  raised inside the transaction when a system rename was asked (rollback). Both `_fetch_collection`
  probes go.
- `delete_collection`: `DELETE … WHERE id = %s RETURNING is_system`; 0 rows → 404; `true` → 409
  (rollback). The probe goes.
- `add_properties_to_collection`: its existing `UPDATE collections SET updated_at = now()` moves
  to the FRONT; 0 rows → 404; the `SELECT 1` goes.
- Share create: the same `updated_at` UPDATE on the tenant connection is the ownership gate.
- `update_note`: ONE `UPDATE property_notes SET body = coalesce(%s, body), shared = coalesce(%s,
  shared), updated_at = CASE WHEN %s IS NOT NULL THEN now() ELSE updated_at END WHERE id = %s AND
  property_id = %s RETURNING …`; 422 when the body carries nothing to change; `_fetch_note` goes.
  Marking shared is not an edit (`updated_at` untouched).
- `add_attachment`: the probe becomes `SELECT f.sha256_hex FROM property_notes n LEFT JOIN
  property_note_attachments f ON f.note_id = n.id WHERE n.id = %s AND n.property_id = %s FOR
  UPDATE OF n` (FOR UPDATE is documented to apply the UPDATE policy), count + duplicate checks in
  Python, run BEFORE the R2 upload.
- **No 42501 → 403 mapping**: a forgotten GRANT must keep paging as a 500.

**New routes** (both `tenant_conn`; the share INSERT is the second instance of tenancy.md's named
"short tenant transaction, then a service-role write" exception, `POST /estimations/{id}/feedback`;
like it, the service-role connection opens in the handler only after the gate passed,
`deps.open_background_conn`; unlike it, the route keeps the request-long `tenant_conn`, because
its service-role work is two fast INSERTs, not an LLM call):
```
POST   /collections/{collection_id}/shares            body {email}      -> Collection   (owner only)
  1 tenant:   UPDATE collections SET updated_at=now() WHERE id=%s        -> 0 rows: 404
  2 identity: account_for_email                                          -> none: 422 "No account uses this email."
  3 identity, ONE transaction:
              INSERT INTO collection_shares (collection_id, recipient_account_id) ON CONFLICT DO NOTHING
                CheckViolation (self)                                    -> 422 "That's your own account."
              if a row was inserted: INSERT INTO notification_dispatches
                (source_kind='collection_share', collection_id, change_kind='shared', account_id=recipient,
                 message='<owner email> shared the collection "<name>" with you', channel='in_app',
                 status='sent', target_channels='{}', dedupe_key='share:<collection>:<recipient>')
                ON CONFLICT DO NOTHING — ONE notification per pair ever: a revoke + re-share
                loop cannot refill the recipient's inbox with sender-chosen text
                only the share INSERT's CheckViolation maps to 422; any other (a drifted dispatch
                CHECK) stays a 500
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
  null-iff-mine by construction; the label comes from a scalar `share_party_emails()` subselect
  (scalar subselects only, so one projection serves SELECT and every RETURNING). On a shared
  row `monitoring_enabled` is `false` and `notify_channels` `[]`: they describe the CALLER (alerts
  go to the owner), which removes every recipient branch from the bell and pill marks.
  `shared_with`: the owner sees all recipients; a recipient sees only their own grant (`email:
  null` = you), whose `account_id` is the key for leaving.
- **Note** = `{id, property_id, body, shared, created_at, updated_at, owner_email, attachments}`.
  `owner_email` null = mine; a foreign note is always `shared: true`. `CreateNoteIn` = `{body,
  shared = false, origin_listing_ref_id}`; `UpdateNoteIn` = `{body?, shared?}`, at least one. The
  legacy `origin_listing_id` leaves both payloads (the column stays: an R2-era carrier the server
  derives from the surrogate).
- **Notifications**: one `SourceKind = Literal["watchdog", "collection_monitor",
  "collection_share", "system_health"]` alias replaces the six copies in `api/notifications.py` and
  `api/routes/notifications.py`; the list/unread/mark routes need no other change (the unread total
  already sums every kind).
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
- **Browse, Kolekce picker** (ruling 3): same `collections` URL parameter; options grouped
  "Mine" / "Shared with me" (two `MultiselectChips` over one value array); chips use
  `collectionLabel`; selected ids no longer listed (revoked, deleted) render as clearable `#id`
  chips.
- **Collections page:** own rows unchanged plus "shared with N"; a "Shared with me" section reuses
  the same row with "Shared by {email}" and no Default / Delete / monitoring pill; the duplicate-
  name check runs over own names only.
- **Collection page, owner:** Edit, Monitoring, and a **Sharing** block (`<SectionLabel>`, which
  also replaces 4 hand-typed copies): an email field, "Share", the recipient list with a two-step
  "Stop sharing"; a 422 shows inline.
- **Collection page, viewer:** header "Shared by {email} · view only" with a two-step "Remove from
  my list"; Edit, Monitoring and the per-row Remove column hidden; the 404 copy adds "…or it is no
  longer shared with you".
- **Notes** (`NoteRow`, shared by desktop and phone). The **audience** of a property = the
  counterparties of every share whose collection holds it (from the member map × `shared_with` /
  `owner_email`). When the audience is non-empty the composer shows a checkbox, off by default:
  "Share with {emails}"; an own note shows a small "Shared" mark with a toggle (`PATCH {shared}`);
  the mark reads "Shared · nobody can see it yet" when the audience is empty. A foreign note: meta
  `{email} · {time}` and a matching aria-label; no Attach / Edit / Delete, no drop zone, file tiles
  without ×; a neutral left border from an existing token. Note counts mean "notes I can read".
- **Notifications page:** a `collection_share` row renders like the `system_health` text row — the
  message, "Open collection" linking to `/collection/{id}`, the usual seen/dismiss — and the source
  tabs gain "Shared". The unread badge needs nothing.
- **Phone** (Czech; managing shares is desktop-only, #1760 Q11): shared rows under "Sdílené se
  mnou" as "sdílí {email}"; detail header "sdílí {email} · jen ke čtení", no "Otevřít Nové"; 404
  copy "…smazána, nebo už s vámi není sdílená"; footer adds "sdílení"; composer checkbox "Sdílet s
  {email}", mark "Sdíleno".
- **Extension** (vanilla TS, no new message type or endpoint): `ExtCollection` drops `is_system`
  (never read) and gains `owner_email`; `ExtNote` drops `origin_listing_id` (never read) and gains
  `shared` + `owner_email`; the checklist lists `mine` only and `saved = |collection_ids ∩ mine|`
  replaces the double derivation; a foreign note shows "od {email}" with no edit/delete; the
  composer gets the same checkbox when the audience (`collection_ids ∩ shared → owner emails`) is
  non-empty.

### 3.7 Notes: semantics and attribution

- **Author** = the account `require_account_id` stamps at creation (never the collection's).
- **Label** = the live auth email via `account_email()`, never stored (the share notification's
  message is the one snapshot).
- **Consent, not time** (ruling 1): a note is private until its author marks it shared; the mark is
  the author's statement "people I share this property with may read this". It is symmetric —
  owner → recipients and recipient → owner follow the same rule — and co-recipients never see each
  other (5b). One dated edge: a recipient's mark consents to the shares it had received AT THAT
  MOMENT (`shared_at`); a share someone creates later does not inherit it, and re-marking consents
  again. An owner's mark needs no such date — the owner chose every recipient.
- **Visibility recomputes from the live facts**: unmarking, revoking, leaving, or removing the
  property from the collection hides the note again; re-sharing or re-adding shows marked notes
  again (the mark persists). Nothing is deleted by any of these.
- **Attachments follow their note** (ruling 2), served through the existing API proxy.

### 3.8 Merges, splits, monitoring

- Shares are keyed on the collection, which a merge never changes. Membership is carried as the
  owner's rows; notes (with their `shared` mark) are carried by property (append), so visibility
  recomputes on the survivor; a split's note copy keeps the mark and its `shared_at`. The split preview still shows
  only the acting account's items.
- **Monitoring is unchanged**: alerts stay stamped to the owner; a recipient never receives one;
  the masked fields stop the UI implying otherwise. The share notification is the only event a
  recipient gets, once per share creation.

## 4. Rejected from the candidates (one line each)

Untimed automatic note visibility: the owner controls both the share and the membership, so it
would harvest a recipient's whole note history · time-gated automatic visibility (v1): replaced by
the author's explicit mark — simpler and consented · a `shared_note_ids()` definer: a correlated
policy does the same without an RPC surface · an INSERT grant on shares: uuids are learnable ·
`DELETE /shares/{email}`: PII in access logs · a second label definition: one `account_email()` · a
second service-role connection on four hot read routes for labels: a recurring pool cost held by
convention · dropping `legacy_backfill_claim`: tenancy.md:134 forbids it · dropping `is_system`
before the code stops reading it: a migration-438-class outage · replacing the collection table with
cards: an unrelated visible change · email on share: per-recipient external delivery is not built;
the in-app row is the house pattern · pending invites, an acceptance step: off the north star (5e).

## 5. Subtraction ledger

| Addition | Paid by (deletion) | Evidence |
|---|---|---|
| `collection_shares` + 1 party policy | DROP `collections_public`, `tags_public` (W2) | zero readers in api/, toolkit/, scraper/, scripts/, frontend/src, chrome-extension/src; only `tests/test_tenant_isolation_live.py:48,59,816,826` |
| trigger on shares | 292's function renamed and reused, no 9th copy | triggers bind by OID; only migration 292 names it |
| 4 FOR SELECT policies | no new read path, view, filter or endpoint — every reader inherits | GET /collections, the member map, the prefilter, get_collection, portal_lookup, notes, files |
| `property_notes.shared` | the v1 time rule, its two server-stamped anchors and the column-scoped grant; the merge-collapse edge | §4 |
| 4th notification kind + 1 trigger branch | six `Literal[...]` copies → one `SourceKind` | `api/notifications.py:620,759,789`; `api/routes/notifications.py:138,162,176` |
| `account_email()` | `admin.get_entitlements`' inline `auth.users` join + its duplicate rows | `api/routes/admin.py:741-751` |
| `share_party_emails()` | no stored label (the share notification keeps the one snapshot), no second connection on read routes, no `accounts` widening | PII rail |
| the write-is-the-gate pattern | 3 visibility probes + `add_properties`' `SELECT 1` + `_fetch_note` | `curation.py` `_fetch_collection` ×2, `delete_collection` SELECT, `:249-253`, `:389-393, 647-656`; fixes the false `{deleted:true}` and a 500 |
| `owner_email` + `shared` on Note | `origin_listing_id` on the Note surface, `CreateNoteIn.origin_listing_id`, the COALESCE arm, the `sreality_id` prop chain (2 sites), `ExtNote.origin_listing_id` | no reader in frontend/src or the extension; written-but-never-read |
| `owner_email` on ExtCollection | `ExtCollection.is_system` + the double "saved" derivation | `chrome-extension/src/types.ts:102` unread; `index_cards.ts` |
| `owner_email` + `shared_with` on Collection | twin mappers `_to_collection`/`_to_collection_full`, `_to_tag`/`_to_tag_full` | `curation.py:619-633, 679-689` |
| 3 hooks + `isMine` + `collectionLabel` | 16 hand-typed `useQuery` copies + 5 Set derivations | 12 files (CollectionSaveToggle, Filters, CurationRow, MarginMarks, ListingCards, CurationBlock, CurationMarks, Pipeline, PhoneCollections, CollectionSaveMenu, Collections, PhoneCollectionDetail) |
| Sharing block + viewer branches | `CurationBlock` CollectionsRow + CollectionToggle (~120 lines, ruling 6); 4 `SectionLabel` copies; a duplicated bell SVG | `CurationBlock.tsx:123-250`; `CollectionDetail.tsx`; `Collections.tsx` ≈ `CollectionSaveMenu.tsx` |
| the 23rd registry entry | the two mirrored registries become one `tests/_tenant_registry.py` imported by both tests | lists are identical today (22 each); the docstrings still say "20" |
| new share-route tests | the duplicated scripted-connection fake → `tests/api/_script_conn.py` | `test_artifact_link.py` `_Tx`/`_ScriptCur` ≈ `test_curation.py` |
| — | the NULL-owner variant on `collections` and `tags` (SET NOT NULL, W2; `filter_presets` and `notification_subscriptions` were already NOT NULL; `manual_rental_estimates` stays nullable as provenance; `property_notes` waits on a ruling — its one NULL row, id 2, is attributed by PR #1829) | live: 0 NULLs except `property_notes` id 2 |
| — | dead `if TYPE_CHECKING: pass`, stale "service role"/"anon key" prose | `curation.py:14-20, 43-44`; `main.py:1420-1424`; `CurationBlock.tsx:10-14` |

**Net, plainly.** ≤ 0 on read paths (0 new), client special cases (one "is it mine" rule in one
place instead of 16), rules (one consent predicate instead of a time rule + two anchors) and the
Note / ExtNote / ExtCollection field counts. **Not ≤ 0:** Collection fields +2 (`is_system` would
have paid for them; its removal is visible and deferred); database objects +1 table, +1 column, +5
policies, +2 functions, +1 trigger branch against −2 views and −2 nullable variants (a third, `property_notes`, pending a ruling); test lines
(the three-tenant proof). These are irreducible — the share is a genuinely new fact, the Sharing
control is what was asked for, and proving isolation is the price of putting sharing in the
database. Each PR body reports its measured diff.

## 6. Waves (one PR each; Opus subagents; CI is the gate; Railway status checked after each merge)

Migration numbers: W2 took 607/608 (605/606 landed with #1832 and are also claimed by #1829/#1830,
which must renumber); W4 takes the next free number at merge time.

- **W1 `cleanup/curation-write-gate`** (backend, no migration, no behaviour change today). The
  write-is-the-gate rewrites (collections ×3, `update_note` as one UPDATE, 422 on an empty patch);
  the attachment probe before the upload; one mapper each; dead block + stale prose. Tests:
  `tests/api/test_curation.py` (404 before any INSERT/DELETE; the system rename/delete 409s roll
  back; the empty patch 422s; `_fetch_note` gone), `test_note_attachments.py` (probe before
  `upload_bytes`), new `tests/api/_script_conn.py` adopted by `test_artifact_link.py`. Docs:
  tenancy.md names the write-is-the-gate rule under shape 1.
- **W2 `cleanup/curation-schema-and-registry`.** `607_drop_dead_curation_views.sql` (ruling 7;
  the definitions live in 025/202/211/316/319, no data), `608_curation_account_id_not_null.sql`
  (`collections` + `tags` only: a NULL check that raises, then SET NOT NULL; `filter_presets` and
  `notification_subscriptions` already were; `manual_rental_estimates` is provenance by design;
  `property_notes` holds one pre-tenancy NULL row that PR #1829 attributes and its NOT NULL waits
  on the operator's ruling about the split preview's bare-note case). `tests/_tenant_registry.py`;
  the view entries and fixture lines removed from the live lane. Merge first, then apply each
  file via `apply_migration.yml` (dry run, then `confirm=APPLY`), then one `execute_sql` check
  (`to_regclass` of both views NULL; `is_nullable = 'NO'` on both columns), then CI.
- **W3 `cleanup/curation-client-seams`** (SPA + extension + the API note surface; no visible
  change except ruling 6). The 3 hooks replace the 16 copies; CollectionsRow deleted; the legacy
  note origin removed across API, SPA, extension; `SectionLabel` + bell dedup. Tests:
  `collectionCache.test.ts`, `CurationBlock.test.tsx`, `test_curation.py`; extension `npm run
  type-check && npm run build`. Verify: vite status + a read-only production check.
- **W4 `feature/collection-shares`** (database + API + doctrine). the `collection_shares` migration (next free number); CI
  stub column; `account_for_email`; projections (Collection, Note); both routes with the share
  notification; `SourceKind`; entitlements on `account_email`; FilterDef description + regenerated
  file. Tests: new `tests/test_collection_shares_live.py` (three tenants A/B/C: B reads A's shared
  collection + members + count, C nothing; B's UPDATE/DELETE touch 0 rows; B's member INSERT raises
  42501 with the OWNER stamped; B cannot see C's grant; B can leave; an unmarked note is invisible
  both ways, a marked one visible to the counterparty only, never to C, hidden again on unshare /
  leave / membership removal; files follow their note; A cannot edit B's note; the share dispatch is
  stamped to B and readable by B only; `share_party_emails()` equals the visible party set for all
  three; anon has no EXECUTE, authenticated cannot run `account_email`; no 42P17 through any
  `*_public` view); `test_migration_rls_grants.py` (pinned policy census name → cmd,
  roles={authenticated}; `'auth.users'` in `_ADMIN_ONLY_RELATIONS`); `test_curation.py`,
  `test_auth.py`, `test_admin_route_coverage.py`, `test_account_scope_census.py`,
  `test_admin_plans.py`, the notifications tests. Docs in the same PR: tenancy.md (the trigger
  passage, the second named exception, the `auth.users` touchpoints, "a share is never
  membership"), database SKILL summary, architecture.md rules 16 + 18, GLOSSARY ("Share", "Shared
  note"; Collection "may be shared read-only"), CLAUDE.md rules 16 + 18 clauses, toolkit-api route
  list, `api/curation.py` docstring. Verify: apply 609 BEFORE merge (safe: no share can exist until
  the route deploys; the W4 projections need `share_party_emails()` and `property_notes.shared`, so
  merging first 500s every collection and note route), one `execute_sql` policy/privilege check, confirm Supabase → Authentication →
  Email → "Confirm email" is ON, merge, API status.
- **W5 `feature/collection-sharing-surfaces`** (SPA, §3.6 desktop + phone + Notifications page).
  Vitest for the NoteRow foreign variant and the share checkbox/mark, own-only CollectionSaveList,
  picker grouping + `#id` chips, SharingBlock copy, PhoneCollections split, the share notification
  row. Docs: architecture.md phone paragraph; roadmap/ui-track.md. Verify: vite status + read-only
  production check.
- **W6 `feature/collection-sharing-extension`.** Owner filter, saved intersection, foreign notes
  read-only, the share checkbox. Type-check, build, headless harness; ship `dist`. Docs:
  roadmap/operator-workflow-track.md done; ROADMAP index cell. Then the operator makes the first
  real share; I only read.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A later "simplification" re-introduces a SELECT probe before a write (visibility ≠ ownership) | W1 tests assert 404 before any write; a one-line WHY beside each gate; named in tenancy.md |
| A shared-read policy written FOR ALL or without `TO authenticated` silently grants writes or reaches anon | pinned policy census + the migration's assert block |
| `share_party_emails()` restates the party rule; drift mislabels | live equality test against the visible share rows for all three tenants |
| a note marked shared on a property later added to a shared collection becomes visible without a fresh decision | the mark means "people I share this property with may read this"; the composer and mark always show the current audience; unmark hides |
| a stranger who knows an author's email shares a bait collection WITH the author to read notes the author marked for someone else (shares need no acceptance) | the recipient-author arm counts only shares older than the note's `shared_at`; marks made after the bait share do reach the stranger, whose share the author sees in their list and notification and can Leave (live test `test_a_share_made_after_the_mark_does_not_widen_a_recipients_note`). **Operator ruling owed** on this added edge (review 2026-10-17) |
| a revoke + re-share loop floods a recipient's inbox with sender-chosen text | ONE share notification per (collection, recipient) ever (`dedupe_key` without a time) |
| `shared_with[].account_id` lets an owner turn a confirmed email into its account uuid (share, read, revoke), linking a named person to `account_id` rows in read-all tables (`manual_rental_estimates`) | accepted for a closed platform, like enumeration; revisit with signup (an opaque per-share handle would close it) |
| the share notification's message snapshots the owner's email | the one stored copy; it outlives a revoke and an email change on purpose (an inbox row is history) |
| email enumeration | accepted for a closed platform (5 users); rate limit if signup opens |
| a squatter registers someone's address | confirmed-only match + the W4 "Confirm email ON" check |
| unsolicited shares clutter a list; a hostile owner shares >100k properties and breaks the recipient's member map (`expectMax`) | Leave; the share notification; 5 users; watch the map size |
| per-note subquery on the whole-set note-count read | index-backed (share PK, `collection_properties_by_property`); 102 notes; NOT timed by the live test (its fixture is a handful of rows) — measure on production after apply |
| a recipient's own dismissals hide shared properties in Browse | consistent with own collections; documented |
| caches 30 s stale after a revoke | clean 404 copy; clearable `#id` chip |
| Collections nav plan-gated, Browse picker not | same posture as own collections today; flagged, not changed |
| owner-only columns readable raw through supabase-js on a shared row | RLS hides rows, not columns; not secrets; the API masks them |
| migration number collisions with long drafts | next free number at merge |

## 8. Rulings (2026-10-17, final)

1. **Notes:** ONE symmetric rule — a note crosses a share only when its author marks it shared;
   default private; the recipient marks theirs the same way. (Replaces the v1 one-way time rule.)
2. **Files** follow their note: yes.
3. **Browse control:** the same Kolekce picker with a "Shared with me" group.
4. **Email with no account:** plain "No account uses this email".
5. **Scope:** (a) a recipient can leave; (b) recipients don't see each other; (c) no alerts for
   recipients; (d) no re-sharing; (e) **an in-app notification on share** (no email); (f) revoking
   deletes the share row, no history; (g) the system "monitoring" collection is shareable.
6. **Property page:** delete the collection chip row; the header `Uložit do kolekce` stays.
7. **Drop** `collections_public` and `tags_public` (zero readers outside tests; no user impact).
8. **Wording:** follow each page's language (English desktop collection pages + Browse sidebar;
   Czech phone + extension).
