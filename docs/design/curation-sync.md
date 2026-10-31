# Curation sync: open windows follow a curation change made anywhere

Status: **Approved 2026-10-31 (spec #2197); build in progress.** All five rulings are in (§ 8; Q3 was ruled (a), not the recommended (b)). Waves W0–W4 (§ 6) are #2198–#2202 and the gate #2203 follows W4; track `roadmap/operator-workflow-track.md` § Phase U-SYNC; decision record ADR-0008. The design is Lens C (the pulled curation clock), revised against four adversarial reviews (security, correctness, operability, completeness); § 12 is that review's ledger, and its build ledger records what each wave built differently.

Tags:
- **[V]** means the reviser read it this session (code, `node_modules`, site-packages or docs).
- **[V·r]** means a reader or reviewer verified it and I did not re-read it.
- **[V, W1]** means read in W1's code as built (PR #2205), not on origin/main.
- **[A]** means assumed.

Line numbers refer to origin/main at `934e904a3` (2026-10-31).

Vocabulary. These are new GLOSSARY terms (W0 adds them):
- **Curation sync**: how every open window follows a change to the user's curation, or to the shares that decide who sees it, made anywhere.
- **Curation clock**: one counter per (account, family). Only the database advances it, on a committed change. It is not history.
- **Clock read**: one window's read of the curation clock.
- **Owed read** (code term only, not a GLOSSARY term): a re-read a window must still make.

Rejected terms:
- "probe": GLOSSARY uses it for the Realtime worker's probes (`GLOSSARY.md:397`) and lists it under _Avoid_ for Check (`:429`) [V].
- "tick": Letter _Avoid_ (`:145`) [V].
- "version": Snapshot _Avoid_ [V·r].
- "operator state": Curation _Avoid_ (`:192`) [V].
- "live" and "Realtime": these mean a live deal and the scraper worker [V].

---

## 1. North star

**Every open window of a signed-in user converges on the server's committed curation, and on the shares that decide who sees it:**
- while the window is visible, within one clock interval (≤ 5 s in the desktop frame, ≤ 15 s in the phone frame; ruling Q2);
- when the window is shown again, on its first clock read.

**How it works:**
- The window learns *what* changed from one per-(account, family) curation clock. Only the database advances it.
- It re-reads only the reads it already has, through one table→read map and one read scheduler, which its own writes also use.
- A re-read stays owed until a read issued after the change has landed: a change can be delayed, never lost.

**Named exceptions** (each one is ruled or filed as a follow-up):
- An open Browse, Feed or collection list keeps its rows, counts, map and stats until the next navigation (or Feed's resume re-read, or a reconnect refetch of a Browse or Feed list); only the per-row marks follow now (ruling Q3a). The window's own writes settle by ruling 9's per-property judge (Decision 7).
- The two parties of a collection share do not yet follow each other's shared curation (F2).
- An open property that is merged away elsewhere shows its emptied curation until navigation (F14).
- Extension panels do not receive (Q4/F1).

**Checks every decision is tested against:**
1. **One source.** Only database triggers advance the clock. No API, worker or client writes it (census h, privilege revokes).
2. **No state in a signal.** Nothing renders from a clock row or a nudge. Windows re-read.
3. **One map, one scheduler.** Every curation re-read, local or remote, is named by `SYNC_TABLES` and paid by `revalidate`. Holds are enforced by the query cache, whatever starts a read.
4. **Lossless end to end.**
   - Server: a persistent counter, compared only for inequality.
   - Client: an owed read is paid only by a read issued after the change.
   - Every trigger means only "read the clock now".
5. **One gate per fact.** Who may follow an account is stated once in SQL (`curation_clock_audience()`). A census pins it to ruling 2.
6. **A failure is visible.** A write that does not commit never answers 200 (W0).

## 2. What exists today (verified)

**No window learns of an outside change.**
- Board and members have staleTime 30 s and stages 60 s, with no interval (`lib/pipelineReads.ts:19-46`) [V·r].
- `refetchOnWindowFocus:false` is set globally (`main.tsx:25`) [V]. `refetchOnReconnect` is unset, so it defaults to true [V].
- query-core is 5.100.9 [V·r]. Its focusManager listens only to `visibilitychange` [V·r].
- There is no `.channel(` anywhere [V·r]. Production has 0 publication tables, 0 slots and 0 `realtime.messages` partitions [V·r].

**Writers.** Only the tables see all of them.
- The API writes on `tenant_conn` (`SET LOCAL ROLE authenticated`, `api/tenant_pool.py:87`) [V].
- Merge and split carriers write on the service role and move any account's rows (8 carriers, `toolkit/property_carriers.py:276-290`) [V·r].
- Signup seeds rows as definer (612) [V·r]. FK cascades apply (202) [V·r]. The extension writes only through the API [V·r].
- Every clocked row carries `account_id` (0 NULLs) [V·r]. The share tables also carry `recipient_account_id` [V·r].

**D6: the commit happens after the response.**
- `tenant_conn` is a yield dependency with default (request) scope. FastAPI 0.139.0 closes `request_stack` only after `await response(...)` (`fastapi/routing.py:131-138`) [V].
- `Depends(scope="function")` exists (`params.py:749`) [V], and its stack closes before the response is sent [V].
- The dependency cache key includes the scope (`dependencies/models.py:63-71`) [V]. A partial switch would therefore open two transactions per request.
- `require_account_id` and `require_acting_account_id` are plain functions, so they may depend on a function-scoped dependency (`dependencies/utils.py:319-327` forbids only request-scoped generators) [V].
- There are 68 `Depends(…tenant_conn)` route sites in 6 files, plus the 2 helpers (71 grep hits with one docstring) [V]. None streams or uses `BackgroundTasks` [V].
- `pyproject.toml:43` pins only `fastapi>=0.115` [V].
- Production has 0 deferrable constraints and 0 constraint triggers [V·r], so today nothing runs at COMMIT.

**Identity and acting.**
- `current_account_ids()` is definer and reads `request.jwt.claims->>'sub'` [V·r].
- `acting_account_ids(for_board)` reads `x-act-as` and the method [V·r].
- `pipeline_shares_party` is readable `… and not acting()` [V·r].
- Acting keys are `['acting', owner, ...k]` (`lib/acting.ts:192-194`) [V·r].
- The identity effect clears the cache on every change of user, including sign-out (`lib/auth.tsx:174-194`) [V].

**Write policy.**
- `useOptimisticWrite` holds by `await`-ing `cancelQueries` and snapshots whole values (`:89-95`, `:193-198`) [V].
- `onSettled` invalidates unconditionally (`:204-208`) [V]. That is **D1**: overlapping writes repaint stale state.
- A snapshot rollback erases a concurrent write's patch, or resurrects a failed one.
- `Query.cancel` reverts synchronously (`retryer.js:29-35`) [V], so the `await` is unnecessary.

**Hand-typed revalidate lists.**
- `PIPELINE_REVALIDATE`, `pipelineRevalidate` and `revalidatePipeline` (`lib/pipelineCache.ts:84-100`) [V].
- `revalidateCollections`, which invalidates acting variants no reader holds (**D5**) (`lib/collectionCache.ts:119-137`) [V].
- `revalidateTags` (`lib/tagCache.ts:19-22`) [V] and `revalidatePipelineShares` (`lib/pipelineShares.ts:49-50`) [V].
- StageManager's list omits `members` (**D2**), and its comment says the opposite (`pages/Pipeline.tsx:950-960`) [V].
- There are 105 `invalidate/refetch/resetQueries` sites in 39 files, most of them market data [V].

**Local drafts that write a remote change back (D3 class).**
- `StageEditorRow` (`Pipeline.tsx:1058-1091`) [V].
- TagEditPopover (`:109-131`), CollectionDetail rename (`:350-371`), PhoneCollectionDetail (`:234`), the CurationBlock note body (`:619,667`), and ArtifactLinkRow's `setQueryData`-on-success (`:48-61`) [V·r].

**TanStack traps** [V]:
- `fetch` dispatches `{type:'fetch'}` before it creates the retryer (`query.js:261-264`).
- An invalidation of a query with no data dedupes onto the in-flight load (`query.js:183-193`), and the landing load clears `isInvalidated`.
- Cache listeners run synchronously inside `notifyManager.batch` (`queryCache.js:72-78`).
- `cancelQueries` defaults to `revert:true`, and a manual `setData` resets `isInvalidated`.

**Browse.**
- Lists are patched in place and never refetched for a settle (`lib/browseKeys.ts:41-44`) [V].
- The dismissed lens is on by default [V·r]. The map can be up to ~22 MB (`lib/pgRead.ts:29-35`) [V].
- `browseReads` returns `[]` while acting (`curationSettle.ts:112-113`) [V].
- Shared-note visibility joins `collection_properties` (`migrations/610:136-145`) [V].

**Health harness.**
- `Check(key, fn, domain, cls, host, cadence_s, label, unit, owner_path, budget_s)` (`scripts/verify_pipeline.py:454-468`) [V].
- A test pins `fn.__name__ == f"check_{key}"` (`tests/scripts/test_verify_pipeline.py:452`) [V].
- Worker alarms fill the 500 s pass exactly (`verify_pipeline.py:449`; `realtime_worker.py:538`) [V].
- `previous_result(conn, key)` exists (`scripts/checks/platform.py:43,305`) [V].

**Volumes** [V·r]:
- Over 14 days, about 425 curation changes from 2 active accounts (~15 per account per day): 125 pipeline events, 102 dismissals, 46 collection adds, 18 tags, 38 notes, 71 files, 10 stage edits, 6 links.
- Market data runs at about 10⁴ changes a day.
- Database baseline: about 1.21 vCPU of statement time on average.
- 382 deadlocks in 14.3 days, from existing workloads.

## 3. The design

### 3.1 Shape

```
any writer ─tx─▶ clocked row ─DEFERRED row trigger (definer, once per tx·account·family)─▶ curation_clock.counter += 1 at COMMIT
API (W0): COMMIT runs before the response, so a commit-time failure is a 5xx, never a 200
every signed-in window ── ONE owner loop: clock read every 5 s / 15 s while visible; at once on show / pageshow;
   on focus / online / nudge (≥ 1 s apart); back-off on errors
   └▶ diff ▶ ONE map (SYNC_TABLES) ▶ ONE scheduler: owed reads, cache-enforced holds, patch ledger ◀ local writes too
same browser: mutation success ─BroadcastChannel {v:1}─▶ other VISIBLE tabs "read the clock now"
```

- **Decision 1: the source is triggers on the curation tables.** Only the table sees service-role merges, definer signup, cascades and direct DML. An API emitter would need N emitters (R8).
- **Decision 2: the signal is a pulled per-(account, family) counter, compared for inequality.**
  - Authorization is per-request RLS, so a revoke lands on the next read.
  - There is no vendor schema, socket or slot.
  - Pattern references: Replicache version-pull plus an optional data-free poke (https://doc.replicache.dev/concepts/how-it-works, https://doc.replicache.dev/byob/poke); Fowler's Event Notification (https://martinfowler.com/articles/201701-event-driven.html).

### 3.2 Data model: migration `696_curation_clock.sql`

The migration is additive. 696 is claimed with the merge coordinator before W3 writes the file (`docs/agents/issue-tracker.md:66`); should another branch take it first, "696" in this document means "the clock migration".

| family | tables | read share sees | edit share sees |
|---|---|---|---|
| board | property_pipeline, pipeline_stages | ✓ | ✓ |
| dismissals | property_dismissals | — | ✓ |
| collections | collections, collection_properties | — | ✓ |
| tags | tags, property_tags | — | ✓ |
| notes | property_notes, property_attachments | — | ✓ |
| links | property_artifact_links | — | ✓ |
| shares | pipeline_shares, collection_shares (owner **and** recipient) | own only | own only |

```sql
-- 696: curation sync, the curation clock (docs/design/curation-sync.md, ADR-0008).
-- One counter per (account, family). The database advances it once per committed transaction that changes that
-- account's curation in that family. Every open SPA window reads its counters and re-reads what moved.
-- WHY TRIGGERS: the API (as authenticated), merge/split carriers (service role, any account), signup (definer, 612)
--   and FK cascades (202) all write curation; only the table sees every writer.
-- WHY DEFERRED: the bump runs at COMMIT, so a rolled-back write moves nothing and the counter row is locked only
--   while the commit completes. The API commits before it answers (W0), so a commit-time failure is a 5xx.
-- WHY ONCE PER TRANSACTION: windows compare counters for inequality, so one bump per (tx, account, family) is
--   enough. A bulk statement stays O(rows), never O(rows^2) on one hot counter row.
-- WHY DEFINER: the tenant pool writes as authenticated, which may only SELECT the clock.
-- NO PAYLOAD: a counter says "family F of account A changed", never which row or how.
-- NEVER write curation_clock outside bump_curation_clock(), never reset a counter, and never disable a
--   *_curation_clock trigger without a `-- ci-allow-clock-disable:` annotation
--   (tests/test_curation_sync_census.py).
set lock_timeout = '5s';
begin;

create table public.curation_clock (
  account_id uuid        not null references public.accounts(id) on delete cascade,
  family     text        not null,
  counter    bigint      not null default 1,
  changed_at timestamptz not null default clock_timestamp(),
  primary key (account_id, family),
  constraint curation_clock_family_check check (family in
    ('board', 'dismissals', 'collections', 'tags', 'notes', 'links', 'shares'))
);
alter table public.curation_clock enable row level security;
revoke all on public.curation_clock from public, anon, authenticated, service_role;
grant select on public.curation_clock to authenticated;

-- WHO follows an account's curation, stated once. The caller's own accounts follow every family. The owners whose
-- pipeline they share follow board (read share) or every curation family (edit share), per 641 ruling 2.
-- current_account_ids(), never acting_account_ids(): a clock read never acts. The 641 stray-acting rail
-- (tests/test_migration_rls_grants.py:736-757) reads POLICY text only; this body is guarded by
-- tests/test_curation_clock_live.py and census (i). ROWS 4: the caller's accounts plus the owners sharing with
-- them, a handful; the default estimate (1000) would steer the clock read off curation_clock_pkey.
create function public.curation_clock_audience()
returns table (account_id uuid, mine boolean, edit boolean)
language sql stable rows 4 set search_path = '' as $$
  select a.id, true, true from public.current_account_ids() as a(id)
  union all
  select s.account_id, false, bool_or(s.mode = 'edit')
    from public.pipeline_shares s
   where s.recipient_account_id in (select public.current_account_ids())
     and s.account_id not in (select public.current_account_ids())
   group by s.account_id
$$;
revoke all on function public.curation_clock_audience() from public, anon, authenticated, service_role;
grant execute on function public.curation_clock_audience() to authenticated;

create policy curation_clock_audience_read on public.curation_clock
  for select to authenticated
  using (
       account_id in (select a.account_id from public.curation_clock_audience() a where a.mine)
    or (family = 'board'
        and account_id in (select a.account_id from public.curation_clock_audience() a))
    or (family in ('dismissals', 'collections', 'tags', 'notes', 'links')
        and account_id in (select a.account_id from public.curation_clock_audience() a where a.edit)));

-- The clock read's one relation. Invoker, so the policy above stays the gate (pinned in _TENANT_VIEWS).
-- `mine` maps a row to own vs acting keys. `age_ms` serves a window's first answer (§3.5 Decision 14).
create view public.curation_clock_public with (security_invoker = true) as
  select c.account_id, c.family, c.counter, a.mine,
         greatest(0, extract(epoch from statement_timestamp() - c.changed_at) * 1000)::bigint as age_ms
    from public.curation_clock_audience() a
    join public.curation_clock c on c.account_id = a.account_id;
revoke all on public.curation_clock_public from public, anon, authenticated, service_role;
grant select on public.curation_clock_public to authenticated;

-- THE one writer of the clock. tg_argv[0] = the family.
create function public.bump_curation_clock() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  v_new  jsonb := case when tg_op <> 'DELETE' then to_jsonb(new) end;
  v_old  jsonb := case when tg_op <> 'INSERT' then to_jsonb(old) end;
  v_done text  := coalesce(current_setting('curation_clock.bumped', true), '');
  v_ids  uuid[];
begin
  if v_new = v_old then
    return null;                                   -- an UPDATE that changed nothing
  end if;
  -- The join on accounts skips an account whose deletion cascades this row; ORDER BY gives one lock order.
  select array_agg(a.id order by a.id) into v_ids
    from public.accounts a
   where a.id in ((v_new ->> 'account_id')::uuid, (v_old ->> 'account_id')::uuid,
                  (v_new ->> 'recipient_account_id')::uuid, (v_old ->> 'recipient_account_id')::uuid)
     and strpos(v_done, tg_argv[0] || ':' || a.id::text || ';') = 0;
  if v_ids is null then
    return null;                                   -- already bumped in this transaction
  end if;
  insert into public.curation_clock as c (account_id, family)
  select u.id, tg_argv[0] from unnest(v_ids) with ordinality as u(id, n) order by u.n
  on conflict (account_id, family) do update
     set counter = c.counter + 1, changed_at = greatest(c.changed_at, clock_timestamp());
  perform set_config('curation_clock.bumped',
    v_done || (select string_agg(tg_argv[0] || ':' || u.id::text || ';', '') from unnest(v_ids) as u(id)), true);
  return null;
end $$;
revoke all on function public.bump_curation_clock() from public, anon, authenticated, service_role;

create constraint trigger property_pipeline_curation_clock
  after insert or update or delete on public.property_pipeline
  deferrable initially deferred for each row execute function public.bump_curation_clock('board');
-- … one static line per table of the families table (12 in all):
--   pipeline_stages(board) property_dismissals(dismissals) collections, collection_properties(collections)
--   tags, property_tags(tags) property_notes, property_attachments(notes) property_artifact_links(links)
--   pipeline_shares, collection_shares(shares)

-- Seed: every account starts with every family at 1, so the health invariant holds from this commit on and each
-- window gets a stable baseline. Later accounts gain rows on their first bump (signup bumps board + collections).
insert into public.curation_clock (account_id, family)
select a.id, f.family
  from public.accounts a
 cross join unnest(array['board','dismissals','collections','tags','notes','links','shares']) as f(family)
on conflict do nothing;

comment on table public.curation_clock is
  'Curation sync (696): one counter per (account, family), advanced only by bump_curation_clock() at commit, '
  'once per transaction; read through curation_clock_public. Not history.';

do $post$
begin
  if (select count(*) from pg_trigger t
       where t.tgfoid = 'public.bump_curation_clock()'::regprocedure and not t.tgisinternal
         and t.tgconstraint <> 0 and t.tgdeferrable and t.tginitdeferred and t.tgenabled = 'O') <> 12 then
    raise exception '696: expected 12 deferred, enabled curation-clock triggers'; end if;
  if not (select prosecdef from pg_proc where oid = 'public.bump_curation_clock()'::regprocedure)
     or (select prosecdef from pg_proc where oid = 'public.curation_clock_audience()'::regprocedure)
     or has_function_privilege('anon', 'public.bump_curation_clock()', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.bump_curation_clock()', 'EXECUTE')
     or has_function_privilege('anon', 'public.curation_clock_audience()', 'EXECUTE')
     or not has_function_privilege('authenticated', 'public.curation_clock_audience()', 'EXECUTE')
     or has_table_privilege('anon', 'public.curation_clock', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('authenticated', 'public.curation_clock', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('service_role', 'public.curation_clock', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('anon', 'public.curation_clock_public', 'SELECT')
     or not has_table_privilege('authenticated', 'public.curation_clock_public', 'SELECT') then
    raise exception '696: clock grants drifted'; end if;
  if not exists (select 1 from pg_class where oid = 'public.curation_clock_public'::regclass
                  and reloptions @> array['security_invoker=true']) then
    raise exception '696: curation_clock_public must be security_invoker'; end if;
  if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'curation_clock'
                  and policyname = 'curation_clock_audience_read' and cmd = 'SELECT') then
    raise exception '696: clock policy missing'; end if;
  if (select count(*) from public.curation_clock) < 7 * (select count(*) from public.accounts) then
    raise exception '696: clock seed incomplete'; end if;
end $post$;
commit;
```

**Decision 3: deferred constraint row triggers.** A no-op UPDATE is skipped, and the bump runs **once per (transaction, account, family)**.
- Row triggers never fire on 0-row statements. That matters because there are about 21k autodedup steps a day that touch no curation [V·r].
- Deferral means the counter row is locked only from the bump until the commit record is flushed (milliseconds), and a rollback does nothing.
- A transaction-local GUC dedupes the bump. It is correct because windows compare only for inequality. It keeps a 10⁴-row heal migration O(rows), so there is no sanctioned "disable the trigger" backfill recipe and census (e) stays strict.
- Savepoint rollbacks are safe: deferred events queued in an aborted subtransaction are discarded [A: standard PG].

**Decision 4: the bump is SECURITY DEFINER; the audience function is INVOKER.**
- `authenticated` has no write on the clock, hence definer.
- The browser cannot forge `account_id` (curation WITH CHECK) or `recipient_account_id` (no browser INSERT on share tables) [V·r].
- A trigger function needs EXECUTE only at CREATE [V·r 641].

**Decision 5: the audience is stated once in SQL, and census (i) pins it to ruling 2.**
- The read→board / edit→all mapping restates 641 ruling 2 in family terms.
- The restatement is unavoidable: the clock read and a Realtime join carry no `x-act-as`, and Realtime sets no `request.method` [V·r]. So `acting_account_ids(true)` would deny read shares.
- `references/tenancy.md` names it as the sanctioned **"follow, never act"** union of own and owner accounts, beside "replace, never union".

**Decision 21 (new): seed one row per (account, family).** The health invariant holds from the apply, with no false red over the next 24 h, and every window has a stable baseline.

**Decision 22 (new): the tenancy rails hold the clock.**
- `curation_clock` joins `TENANT_TABLES` **and** gets `BROWSER_WRITE_COLUMNS['curation_clock'] = frozenset()` (the `building_runs` precedent, `tests/_tenant_registry.py:52-56` [V]). So both tenancy gates check `account_id`, the policy, no anon access and **no browser write**, today and in every later migration.
- `curation_clock_public` joins `_TENANT_VIEWS` (`tests/test_tenant_isolation_live.py:48-60` [V]).
- The `$post$` block asserts the same at apply time.

**Deadlocks (residual).**
- The dedupe leaves at most one bump per (tx, account, family). Within a firing, the order is by account id.
- Across firings, two concurrent transactions that bump the same two (account, family) rows in opposite orders can still deadlock. Example: `move_card` bumps board then dismissals, while `route_curation` (a detach) may bump dismissals then board (`property_carriers.py:657-678`) [V·r].
- PostgreSQL aborts one of them after 1 s. Because W0 runs the commit before the response, the API victim answers `503 db_busy` (`api/main.py:212-220`) [V]. The mutation rolls back its paint and toasts, so the failure is visible.
- Curation-carrying merges number about 0–3 a day [V·r]. An ordering census across writers is rejected (R30).

### 3.3 Event shape

- **Clock read answer:** rows of `curation_clock_public`.
  ```ts
  interface ClockRow { account_id: string; family: CurationFamily; counter: number; mine: boolean; age_ms: number }
  ```
  - An event is a counter change, a row that appears, or a row that disappears (a revoke or downgrade) between two answers.
  - Size: about 931 B of headers plus about 0.9 KB of body for 8 rows [V·r].
- **Same-browser nudge:** `BroadcastChannel('curation-sync')` carrying `{v:1}`, meaning "read the clock now". It carries no data.

### 3.4 Authorization

| Caller | Rows visible |
|---|---|
| Own account(s) | every family, `mine=true` → bare keys |
| Read recipient of owner O | O's `board`, `mine=false` → `actingKey(O, …)` |
| Edit recipient of O | O's board, dismissals, collections, tags, notes, links (never `shares`) |
| Stranger, revoked recipient, or a read sent with `x-act-as` | nothing of O. The share rows hide while acting, so a stray header fails closed to own rows |
| anon | permission denied |

- **Revocation and downgrade.**
  - A revoke or leave is a hard DELETE, and a mode change is an upsert [V·r].
  - The trigger bumps both parties' `shares` row.
  - The recipient's own `shares` targets re-read `pipelineShareKeys.all`, and `useBoardSelection` toasts and resets [V·r].
  - The owner's rows, or their edit families, vanish from the very next read.
- **The clock read never acts.** `fetchCurationClock` calls `pgRead` without `actAs`. A vitest pins this, plus a live case.
- **What a recipient learns.** For each family it can already read, it learns the cumulative counter (this is change volume, including history from before the share and changes that leave no readable trace) and the exact last-change time.
  - The base table `/curation_clock` and `/rpc/curation_clock_audience` are directly readable, scoped exactly like the view (live lane).
  - The audience lists owner ids that `pipeline_shares` already shows [V·r].
  - No PII and no row ids are exposed. This is accepted and documented in `tenancy.md` (R23 rejects the owner-rights view alternative).
- **Abuse note (pre-existing, low).** Anyone may share to a victim's confirmed email, which bumps the victim's `shares` clock and costs one `pipelineShareKeys` re-read per visible window per read. Share grants have no rate limit. This is accepted and named in §7.

### 3.5 Client architecture

**`frontend/src/lib/curationSync.ts`: THE table→read map. It is pure.**

```ts
export const CURATION_FAMILIES = ['board','dismissals','collections','tags','notes','links','shares'] as const;
export type CurationFamily = (typeof CURATION_FAMILIES)[number];
interface SyncEntry { family: CurationFamily; reads: readonly QueryKey[]; ownReads?: readonly QueryKey[]; lens?: CurationLens }
export const SYNC_TABLES = { /* rows below */ } as const satisfies Record<string, SyncEntry>;
export type SyncTable = keyof typeof SYNC_TABLES;
/** Ruling Q3(a): a REMOTE lens change reaches Browse at its next mount, lists and aggregates alike; marks follow now. */
export const LENS_REREAD = { lists: 'mount', aggregates: 'mount' } as const satisfies Record<string, ReadMode>;
export const SYNC_ROOTS: readonly QueryKey[];   // ['pipeline'] ['curation'] ['dismissals'] ['pipeline-shares'] ['acting']
export function applySyncQueryDefaults(qc: QueryClient): void;      // refetchOnReconnect:false per SYNC_ROOT (main.tsx, called from W4)
export function tableReads(tables: readonly SyncTable[], actAs: string | null): QueryKey[];
export function revalidateTables(qc: QueryClient, tables: readonly SyncTable[], actAs?: string | null, opts?: { release?: Hold }): Promise<void>;
export function familyTargets(qc: QueryClient, family: CurationFamily, owner: string | null): Revalidation[];
export interface ClockRow { account_id: string; family: CurationFamily; counter: number; mine: boolean; age_ms: number }
export const clockKey: (row: Pick<ClockRow, 'account_id' | 'family'>) => string;   // `${account_id}:${family}`, the key of clockChanges' prev map
export function clockChanges(prev: ReadonlyMap<string, ClockRow> | null, next: readonly ClockRow[], firstWindowMs: number): ClockRow[];
export const NOT_SYNCED: Readonly<Record<string, string>>;           // key-factory member → reason (key census); empty as built
```

| table | family | reads (own → bare; owner → `['acting',O,…]`) | ownReads (never acting) | lens |
|---|---|---|---|---|
| property_pipeline | board | `pipelineKeys.board`, `.members` | — | pipeline |
| pipeline_stages | board | `.stages`, `.board`, `.members` (**closes D2**) | — | pipeline |
| property_dismissals | dismissals | `dismissalKeys.all` | — | dismissed |
| collections | collections | `curationKeys.collections` | `curationKeys.anyCollection` (new member `['curation','collection']`; a remote change owes it `LENS_REREAD.lists`) | collections |
| collection_properties | collections | `.propertyCollectionMembers`, `.collections` | `.anyCollection`, `.allPropertyNotes`, `.noteCounts` (shared-note visibility joins this table, 610:136-145 [V]) | collections |
| tags | tags | `.tags` (`property_tags_public` reads only `property_tags`; a tag DELETE names both tables, 202 cascades) | — | tags |
| property_tags | tags | `.propertyTagMembers`, `.tags` (GET /tags counts `listing_count`) | — | tags |
| property_notes | notes | `.allPropertyNotes` (notes, files, viewings: one prefix) | `.noteCounts` | — |
| property_attachments | notes | `.allPropertyNotes` | — | — |
| property_artifact_links | links | `.artifactLinks` | — | — |
| pipeline_shares | shares | — | `pipelineShareKeys.all` | — |
| collection_shares | shares | — | `.collections`, `.anyCollection`, `.propertyCollectionMembers` (`collection_properties_shared_read`, 610, joins this table), `.allPropertyNotes`, `.noteCounts` | — |

**Rule for an entry:** a key sits under a table **iff** its read selects that table, or a view or policy that joins it.

**Decision 6: one map keyed by table; families group tables.**
- A local write calls `revalidateTables` or names keys **inside** a root of a table it writes. Example: `useDismissal` keeps `[stateKey, countKey]` [V], which sits under `dismissalKeys.all`.
- A vitest pins that every local write's keys are prefix-covered by its tables' entries, so local and remote cannot drift.
- `PIPELINE_REVALIDATE` is replaced by `tableReads(['property_pipeline','property_dismissals'], actAs)`, pinned **set-equal** (by `hashKey`) to today's `[members, board, dismissals]`.
- A remote change knows only its family and takes the union of the family's tables.

**Decision 7: remote lens changes go through `LENS_REREAD` (ruling Q3(a), 2026-10-31); a local write keeps ruling 9's judge, with the stale-side rule.**
- `lensTargets(qc, lens, reread)` is a new export of `curationSettle.ts` and reuses `cohorts()`. `familyTargets` passes `LENS_REREAD` as `reread`, because `curationSync.ts` imports `curationSettle.ts` and not the other way round [V, W1]. For each cohort that engages the lens:
  - `browseKeys.aggregates` (map, stats, counts, broker board) are owed `'mount'`;
  - lists are owed `'mount'`.
- An open collection page (`curationKeys.anyCollection`) is a list too: `familyTargets` owes it `LENS_REREAD.lists` [V, W1].
- Every other read the family's tables feed re-reads now: the member maps (the per-card marks), the kanban board and its stages, the collections and tag indexes with their counts, notes, links and shares. No Browse list, Browse aggregate or open collection page moves under the user [V, W1].
- Local writes keep ruling 9's per-property judge (`curationSettle.ts` header [V, W1]). The stale-side rule below refines its "stale map" row.
- **The stale-side rule** (`curationSettle.ts` `staleMap`, used by `settlePatches` [V, W1]). A click's before-verdict from a member map can say "no" while a cached list still shows the row. One of them is stale:
  - The **map** is, if one of the member reads the verdict stands on is owed (`isOwed`) or was read before the list was (its `dataUpdatedAt` strictly older than the newest of the cohort's lists showing the row; equal read times count as the list side). Nothing drops, and the after-verdict decides the re-read as for any row: a cohort that still says no (or cannot judge) re-reads whole; one the click brought in re-reads nothing — as today.
  - Otherwise the **list** is: a change made elsewhere after it was read, which under Q3(a) a list follows only at its next mount. The row is judged like any other: a click that leaves it out drops it in place and only the aggregates re-read; one that brings it in re-reads nothing.
- The ledger writes every patch with `updatedAt` = its base's read time (Decision 9), so a paint never makes a map look fresh.
- **What changes versus today: exactly one case.** A local click on a row that a list shows while a member map read no earlier than the list (nothing owed) already says "no", and the click leaves the row out. Today that cohort re-reads whole; now the row drops in place and only the aggregates re-read.

**Decision 8: Browse never receives owner-space targets.** `familyTargets(…, owner≠null)` returns only `actingKey(owner, reads)`, never ownReads or lens targets (`curationSettle.ts:112-113` [V]). This invariant has its own test.

**`frontend/src/lib/revalidate.ts`: THE read scheduler** (one per QueryClient, held in a WeakMap).

```ts
export type ReadMode = 'now' | 'mount';
export interface Revalidation { queryKey: QueryKey; exact?: boolean; mode?: ReadMode }   // default 'now'
export interface Hold { release(): void; rollback(): void }                              // idempotent, epoch-guarded
export function revalidate(qc: QueryClient, items: readonly (Revalidation | QueryKey)[], opts?: { release?: Hold }): Promise<void>;  // settles with the reads it started or joined
export function holdReads(qc: QueryClient, filters: readonly QueryFilters[], patches?: readonly CachePatch[]): Hold;
export function useReadHold(keys: readonly QueryKey[], active: boolean): void;
export function isOwed(qc: QueryClient, key: QueryKey): boolean;
export function repayOwed(qc: QueryClient): void;            // W4's owner, on each clock answer: retry errored 'now' debts
export function resetReads(qc: QueryClient): void;           // epoch++; drop owed reads, holds, ledgers
export const HOLD_TTL_MS = 140_000;                           // ONLINE time; = REQUEST_DEADLINE_MS (130 s) + 10 s, pinned by revalidate.test.ts
```

**Decision 14 (rewritten): owed reads make the client lossless.**
1. **Owe.** `revalidate(items)` records, for each cached query that an item matches, an owed read `{since, mode, attempts}`, deduped by `hashKey`, with `'now'` winning over `'mount'`. `since` comes from the scheduler's own counter, which also stamps every fetch start [V, W1]. With `opts.release`, the release and the new items form one batch, so there is never a double invalidate.
2. **Pay.**
   - A query that is active, not held, enabled and owed `'now'` is paid at once with `query.fetch(undefined, {cancelRefetch: true})`. That is the same call `refetchQueries` makes, and disabled or static queries are skipped as it skips them.
   - An in-flight full fetch started after `since` is joined (its promise awaited), never restarted. The start time is the scheduler's stamp of the cache's `fetch` action (the starvation guard).
   - An initial load (no data) started before `since` is awaited and then paid anew. This closes the swallowed-invalidation trap.
   - An inactive or disabled query owed `'now'` is paid when an observer makes it active, in a microtask. One owed `'mount'` is paid only on its first observer (or when a disabled first observer is enabled); a second reader joining a mounted query pays nothing [V, W1].
   - A held query is parked until its last overlapping hold releases.
   - The scheduler never uses `isInvalidated` as its carrier. A local in-place drop, a rollback or a `fetchNextPage` therefore cannot erase an owed read.
3. **Settle a payment.**
   - The fetch resolving pays the debt, if it started after `since`.
   - A `CancelledError` leaves it owed.
   - Any other error leaves it owed with `attempts+1`. It is re-paid on the next clock answer (`repayOwed`), up to 3 attempts, and then falls to `'mount'`, so an error never loops. A failure of a read that started before `since` is re-paid at once, uncounted [V, W1].
   - Removing a query drops its debt.
4. **Holds** (Decision 9, below).
5. **Epoch.**
   - The identity effect calls `resetReads(queryClient)` immediately before `queryClient.clear()` (`auth.tsx`).
   - Holds, rollbacks, releases and debts from an older epoch become no-ops. The previous account's in-flight write can therefore neither paint its rollback into the new cache nor park the new account's keys.

**First answer (Decision 14b).**
- Before the first read, `start()` owes every synced query already cached, in its family's mode (`familyTargets` of every own family, plus the `['acting']` root), **bar an initial load in flight** [V, W4]. Those are the norm, not none: `RequireAuth` mounts the page in the very commit in which the session is read, and React runs the page's effects (which start its reads) before `AuthProvider`'s. Such a load started within that commit, inside the margin below; owing it would read every mounted synced query twice on every launch (the cost R22 rejects). A read with data, or a refetch in flight, is owed.
- On the first answer, a family counts as changed iff `age_ms ≤ (now − t0) + SYNC_START_MARGIN_MS` (10 s), where `t0` is `performance.now()` at start and `now` the answer's arrival. Both sides are durations, so there is no clock skew. The window is the measured time since start, not a constant.
- Why this is exact: every synced read left unowed started after `t0`, give or take the commit that mounted the owner. A commit missed by such a read therefore bumped (`changed_at`) no earlier than `t0 − d`, where `d` is that commit's few milliseconds plus the gap between a deferred bump and its commit becoming visible; the 10 s margin covers it. This holds however late the first good answer arrives, including after a PostgREST outage at boot.
- Later answers diff with `clockChanges` and collect all `familyTargets` into one `revalidate` call per answer. A disappeared row is treated as a change of that family and owner.
- R22 rejects the empty-baseline alternative.

**Decision 9 (rewritten): holds are synchronous, per key, enforced by the cache, and composed by a patch ledger.**
- `holdReads(filters, patches)` registers synchronously inside the click (synchronous because the app's `MutationCache` has no `onMutate`: query-core awaits a cache-level one before the hook's, so adding one would defer every hold by a microtask [V, W1]).
  - It cancels every matching fetch in flight with `revert` (synchronous, `retryer.js:29-35` [V]) and owes the full ones. A page fetch in flight is dropped, not owed: it was built on the pages from before the patch, and the list's sentinel or next scroll asks again [V, W1].
  - It then applies `patches` through a per-query **ledger** `{base, patches[] in order}`, writing each with the base's read time (`updatedAt`).
  - There is no `await` between registration and paint.
- While a key is held, any later non-page fetch that starts on it, whatever started it (mount refetch, reconnect, a stray `invalidateQueries`, a remote target), is cancelled in a microtask and owed. A microtask is needed because `fetch` dispatches before it creates the retryer [V]. Page fetches (`meta.fetchMore`) pass, so scrolling during a pending dismissal never turns into a full-list refetch.
- `rollback()` drops this hold's patches and sets each query to `base` plus the remaining patches in order. Rollbacks in any order are exact, with no erased concurrent patch and no resurrected failed one: this is Replicache's rebase model.
- A write that lands on a held query from outside the ledger (a landed page, a site's `setQueryData` of a server answer) becomes the new base with the patches in it, and the ledger drops them. Rollback is therefore exact only while no such write has landed; after one, the query is left to the re-read that follows [V, W1].
- `release()`: the write's patches stay in the ledger as confirmed until the query's last hold releases. Then the parked debts are paid and the ledger is dropped.
- A hold older than `HOLD_TTL_MS` of **online** time (the clock pauses while `onlineManager` is offline, so a paused write cannot trip it) is released with `console.warn`.

```ts
// useOptimisticWrite (W1)
onMutate: (vars) => {
  const painted = patch ? patch(vars, qc) : [];
  if (typeof painted === 'function') return { rollback: painted };
  return { hold: holdReads(qc, painted.map((p) => p.filters), painted) };     // no await
},
onSettled: (_d, error, vars, ctx) => {
  if (error) (ctx?.hold?.rollback ?? ctx?.rollback)?.();                      // before the re-read, unchanged
  revalidate(qc, keysFor(vars, error), { release: ctx?.hold });               // one batch
},
```

This is TanStack's documented norm for concurrent optimistic updates ("invalidate only when the last one settles", https://tkdodo.eu/blog/concurrent-optimistic-updates-in-react-query), generalised to keys and enforced at the cache. It **fixes D1**, and fixes rollback composition.

`settleMemberWrite` (non-optimistic tag and collection writes) uses the same `holdReads` plus `revalidate`, so no read can interleave regardless of microtask order.

**Decision 10 (extended): interaction holds, and one draft rule for every curation editor.**
- **Kanban drag:** an imperative `holdReads` in `onDragStart` over `actingKey(actAs, board|members|stages)`.
  - Stages are included because they are the droppable columns.
  - `onDragEnd` calls `move.mutate(plan)` first (its own hold registers), **then** releases the drag hold. `onDragCancel` and unmount also release it (`Pipeline.tsx:870-884` [V]).
- **Phone pager** (`PhonePipeline.tsx:241-268` [V]):
  - `settle(i)` also sets `remembered.current`;
  - `target` stores a **stage id**, not an index;
  - `useReadHold([board, members], gestureActive)` covers an active touch or pager scroll.
  - The stage sheet already re-resolves its card [V·r].
- **`lib/useServerDraft.ts`** (new):

  ```ts
  /** A field edited over server state: follows the server while untouched; saves only what the user changed. */
  export function useServerDraft(server: string): {
    value: string; onChange(v: string): void; onFocus(): void; onBlur(): void;
    edited(): string | null;   // null = the user changed nothing (a server change is never written back)
    reset(): void;
  };
  ```

  - It is used by `StageEditorRow` (label and code, D3), TagEditPopover, the CollectionDetail rename, PhoneCollectionDetail, the CurationBlock note body and ArtifactLinkRow.
  - ArtifactLinkRow's `setQueryData`-on-success is replaced by `revalidateTables(['property_artifact_links'])` (done in W1, #2205; W2 adds only `useServerDraft`).

**`frontend/src/lib/curationSyncOwner.ts`: THE owner.** It is a plain module: no React state and no query-cache entry.

```ts
export const SYNC_INTERVAL_MS = { desktop: 5_000, phone: 15_000 } as const;   // ruling Q2; frame via phoneShell isPhoneFrame()
export const SYNC_MIN_GAP_MS = 1_000;          // no two trigger-driven reads start < 1 s apart
export const SYNC_NUDGE_DEBOUNCE_MS = 100;
export const SYNC_BACKOFF_MAX_MS = 60_000;
export const SYNC_START_MARGIN_MS = 10_000;
export function startCurationSync(qc: QueryClient): { stop(): void };
export function useCurationSync(userId: string | null): void;
// lib/queries.ts
export function fetchCurationClock(o: { signal?: AbortSignal }): Promise<ClockRow[]>;   // pgRead, NEVER actAs
```

**Decision 11 (rewritten): the owner runs its own loop and has no `QueryObserver`.**
- Every behaviour TanStack would contribute is overridden here: visibility-gated triggers, abort on resume, back-off and minimum gap.
- A cache entry would need its own root and exclusion from `clear()` and from sweeps (R26).
- The loop never renders React.

**Decision 12 (rewritten): the owner owns every trigger, and every trigger is visibility-gated.**
- **Interval:** `SYNC_INTERVAL_MS[frame]` from the last read's start (and never sooner than 1 s after its answer) while `document.visibilityState === 'visible'`; the frame is `isPhoneFrame()`, read at each answer. Hidden means no timer and zero requests: hiding aborts a read in flight and drops a pending nudge [V, W4].
- **`visibilitychange`→visible and `pageshow`:** abort any in-flight read and read now. A read in flight across an iOS suspension could otherwise hang until `PG_DEADLINE_MS` (20 s) [V]. A read that started under 1 s ago is already the shown page's, so the pair a bfcache restore fires reads once [V, W4].
- **`focus`, `online` and the nudge** (coalesced over 100 ms): request a read, ignored while hidden, coalesced with the read in flight (one follow-up), and never less than 1 s after the last read started. As built, each asks for a read that starts after it: `focus` also takes one that started under 1 s before it (a desktop tab switch fires `visibilitychange` then `focus`, one read), `online` and the nudge do not (a read from before cannot have reached the server, or seen that commit) [V, W4]. A trigger that is already due reads at once; the start's first read waits one task, so a StrictMode remount sends one request.
- **Errors:**
  - Transient (network, the 20 s deadline, 5xx, 408, 429): interval of 5 s × 2ⁿ, capped at 60 s, reset on success.
  - Permanent (any other 4xx: 400, 401, 403, 404, e.g. the kill switch or a renamed view): no interval; triggers spaced at least 60 s apart. A transient failure while in this mode keeps it (no back-off timer); the first answer ends it [V, W4].
  - No toast and no retry storm.
- The global focusManager is untouched (R12), and `refetchOnWindowFocus:false` stays, with its reason now stated: the owner supersedes it.
- `applySyncQueryDefaults` sets `refetchOnReconnect:false` on `SYNC_ROOTS`, so the clock read is the single reconnect path for curation and hidden tabs stay at zero cost. Browse lists keep today's behaviour. `main.tsx` calls it beside the owner (W4; W1 left it uncalled until the clock read existed) [V, W4].
- The nudge is posted from one `MutationCache` subscription on every mutation `success`, held by the owner (`qc.getMutationCache().subscribe`), so `stop()` ends it and a signed-out window posts nothing [V, W4]. Under W0, success means committed. Without `BroadcastChannel` the owner neither posts nor listens and still reads on its other triggers, and the whole owner is gated on `isSupabaseConfigured()`.

**Decision 13: mount `useCurationSync(loading ? null : userId)` in `AuthProvider`, declared after the identity effect.**
- React runs the changed cleanups before the setups, so an account switch runs **stop → `resetReads` + `clear()` → start**.
- `stop()` aborts the read, clears timers, closes the channel, unsubscribes, and removes listeners.
- A read resolving after `stop()` is dropped (tested).
- StrictMode double-mount is idempotent (tested).

**Lifecycle.**

| Moment | Effect |
|---|---|
| sign-in, launch | start; first answer by the age rule |
| hourly TOKEN_REFRESHED | nothing: keyed on `userId`; each read calls `getSession()` (`index.mjs:524-529`) [V·r] |
| account switch, sign-out | stop → reset + clear → start / nothing |
| hidden | no reads; a read in flight is aborted |
| shown, pageshow | abort and read now |
| focus, online, nudge | one read, ≥ 1 s gap, visible only |
| PostgREST or DB down | transient back-off; first good answer diffs against the last good one; errored debts re-paid ≤ 3× |
| kill switch | permanent-error mode: ≤ 1 read per 60 s per triggered window; app equals today's |

**Decision 15 (rationale rewritten): self-echo is kept, with no origin suppression.**
- After W0 the writer's own settle re-read is post-commit, so the echo is no longer load-bearing.
- It costs at most one family re-read set per answer per writing window (§3.8).
- A census forbids origin headers and GUCs (rail g), and client-side echo prediction is rejected (R24).

### 3.6 API: commit before the response (W0)

**Decision 17 (changed): the D6 root fix is wave W0 and a prerequisite of 696.**
- 696 adds the first commit-time code. Without W0, a deadlock, a lock timeout or an FK race at COMMIT would roll back a write whose client already holds a 200: a silent lost write that no clock would ever signal.

```python
# api/tenant_pool.py
TenantConn = Annotated[psycopg.Connection, Depends(tenant_conn, scope="function")]
def require_account_id(conn: TenantConn, claims: dict = Depends(deps.verify_jwt), act_as: ... ) -> uuid.UUID: ...
def require_acting_account_id(conn: TenantConn, ...) -> uuid.UUID: ...
# every route: `conn: tenant_pool.TenantConn` (68 route sites, 6 files)
```

- One alias is used everywhere, so routes and the `require_*` helpers share one cache key and one transaction.
- The function-scoped stack closes after the endpoint and response serialization and before `await response(...)` [V]. A commit exception reaches the app's handlers: deadlock and lock errors become `503 db_busy`, anything else a 500. The mutation fails, rolls back and toasts.
- `pyproject.toml`: `fastapi>=0.139`, the verified installed version that provides `Depends(scope=…)` [V]. The exact first version is [A].
- The 19 `dependency_overrides[tenant_conn]` test sites stay valid, because overrides are keyed by callable [V].
- As built (#2198): eleven route signatures move `conn` ahead of their defaulted parameters, because an `Annotated` `Depends` takes no default; none of them depends on the order. `tests/api/test_tenant_conn_scope.py` holds the census, the function scope on every route, the 503/5xx on a failed commit and one connection per acting write.

### 3.7 Scope

**Decision 16 (changed): in scope** are the 12 tables above and every surface that reads them: kanban, phone stages, Browse glyphs, lens aggregates, property header and `CurationBlock`, collections, viewings, note counts, files, links, the share switcher and revocation. Every writer is covered.

**Explicitly out:**
- Notifications (Q1b; the 30 s and 60 s polls stay, `pages/Notifications.tsx:51`, `lib/unreadCount.tsx:43`). `VISIBLE_SQL` also depends on `notification_subscriptions.retired_at` and `properties.first_seen_at`, which this clock cannot cover [V·r].
- Ad status and price (the "Stav" filter); a census forbids clocking `listings`, `properties` or `listing_snapshots`.
- Estimation runs, presets, dashboards and watchdogs (F5).
- The extension as receiver (Q4).
- Stale-intent conflicts (Q5).
- `own_account_id` (F4).

### 3.8 Cost model

Formulas:
- **Clock reads:** visible windows ÷ T × ~1.8 KB, at ~2.3 ms of DB each (`pipeline_stages_public` mean 2.28 ms over 2,426 calls) [V·r].
- **Change fan-out:** for each change of family F of account A, Σ over visible windows of A (and of board recipients) of F's *mounted* targets (under the unruled Q3(b), plus the lens aggregates mounted; under the ruled Q3(a) they wait for the next mount). Coalesced per answer per window. The self-echo is included.

| Scenario | Clock reads | Fan-out |
|---|---|---|
| Today (~5 visible windows, ~30 changes/day) | 1 read/s ≈ 0.0023 vCPU (0.19 % of the 1.21 vCPU baseline [V·r]), ~4 GB/month [A] | ~30 × 2 windows × ~0.3 MB ≈ 18 MB/day [A]: noise |
| 1,000 accounts, ~325 visible windows [A], 50 changes/account/day [A] | 65 reads/s ≈ 0.15 vCPU; ~90 GB/month at 10 h × 22 d [A] | 50k changes × ~2 windows × ~0.3 MB [A] ≈ 30 GB/day ≈ 660 GB/month before coalescing (≈ $37/month over the 250 GB quota at $0.09/GB [V·r]) |

- **The dominant term under Q3(b) would be the Browse map**: up to 22 MB [V], typically ~0.5 MB [A], re-read in every visible Browse window of the account on each remote dismissal or pipeline change. **Ruling Q3(a) removes it** (`LENS_REREAD.aggregates = 'mount'`), so the fan-out column above is an upper bound; per-answer coalescing still caps bursts of mark re-reads.
- The phone frame at 15 s cuts phone clock reads by 3×.
- The F7 doorbell (not built, ruling Q2) would cut clock-read cost (T relaxes to 30 s) but **not** fan-out.
- **Breakpoints:**
  - The clock-read share reaches 1 % of statement time at ≈ 26 concurrent visible windows at today's load.
  - The ongoing `load` cell watches at 5 % (§3.9). That is the planned trigger to re-cost (a longer T, or F7 on a ruling), replacing v1's "~1,000 windows".

### 3.9 Operations

**Health check `curation_sync`.**

| Field | Value |
|---|---|
| fn | `check_curation_sync` in `scripts/checks/platform.py` |
| domain | `platform` |
| class | **alarm** (these failures "should not happen") |
| host | `realtime-worker` |
| cadence | `_HOURLY` (3600) |
| label | "Curation sync" |
| unit | `cells` |
| owner_path | `""` |
| budget_s | `5.0` (tiny reads, like `notifications`) |
| position | after `notifications` |

Cells:
- `triggers` (W3): **Problem** unless exactly `THRESHOLDS["CURATION_CLOCK_TRIGGERS"]` (12) triggers on `bump_curation_clock` are enabled (`tgenabled='O'`) and initially deferred. A census pins the threshold equal to `len(CLOCKED_TABLES)`.
- `board_bumps` (W3): **Problem** when any `property_pipeline_events` row of the last 24 h has no `board` clock row with `changed_at >= created_at` for its account.
  - This is exact because events are written only in the transaction of a `property_pipeline` change (`api/pipeline.py:280,326,380`) [V·r].
  - `now()` is the transaction start, which is ≤ the bump's `clock_timestamp()`.
  - The seed makes it hold from the moment of apply.
- `reads` (W4): **Watch** (never paged) when the `pg_stat_statements` call count for `curation_clock_public` has not advanced for 24 h (`THRESHOLDS["CURATION_CLOCK_QUIET_H"]`). As built, the count is the entries run as `authenticated` whose text names the view (the pattern is a bound parameter, so the check's own statement never matches; a verification query run as postgres never counts). `quiet_since` is carried forward in the cell facts through `previous_result`; an evicted entry (fewer calls, no reset) is no advance, a statistics reset (`pg_stat_statements_info.stats_reset`) with calls since is one. Not measured without the extension (CI; the PREPARE sweep allowlists the read, the `cron.` precedent) [V, W4]. Worker read privilege on `pg_stat_statements`, and PostgREST's read being recorded under `authenticated` (it runs after `SET LOCAL ROLE`), are [A].
- `load` (W4): **Watch** when the clock read's share of total statement time (`total_exec_time`) between two measurements is ≥ 5 % (`THRESHOLDS["CURATION_CLOCK_LOAD_PCT"]`, the re-costing trigger). A first measurement, a statistics reset or an evicted entry takes a baseline: the cell is Not measured (`unknown`, its facts kept for the next run) [V, W4].

Registry edits (W3):
- `CHECKS` (and the comment "these thirteen" → fourteen; the budget arithmetic comment at `verify_pipeline.py:449` becomes 7 × 45 + 180 + 5 + 5 = 505 s: when every alarm falls due at once, the last one defers one pass, an existing tested path).
- `THRESHOLDS`.
- `tests/scripts/test_verify_pipeline.py`: `_WORKER_KEYS` (`:55-58`), the owner/domain map (`:455-474`), and the non-default budget map (`:424`).
- `tests/toolkit/test_system_alerts_replay.py` `_REGISTRY` (`:46-53`): `"curation_sync": ("alarm", 3600, _RW)`.
- A `docs/design/health-dashboard.md` § 4 section, plus `.claude/skills/scraper-ops/references/pipeline-verification.md`.

**Kill switches (Decision 19).**
- **Reader:** a forward migration `revoke select on curation_clock_public, curation_clock from authenticated; revoke execute on function curation_clock_audience() from authenticated;`.
  - Clients go to permanent-error mode, which sheds the load.
  - Polls are unaffected (Q1b out).
  - Re-enabling takes a migration that re-grants.
- **Writer** (any commit-time failure the clock causes, e.g. a bump bug that 5xx's every curation write): a forward migration `alter table <t> disable trigger <t>_curation_clock` × 12, carrying `-- ci-allow-clock-disable: <reason>`.
  - It is non-destructive (database skill).
  - The `triggers` cell goes red while it is in force, by intent.
  - Census (a) and (d) do not read the enabled state and are unaffected.
  - Re-enabling takes a forward migration with `enable trigger`.

## 4. Rejected alternatives

| # | Alternative | Why rejected |
|---|---|---|
| R1 | Broadcast-from-DB as primary transport | At-most-once: `realtime.send` swallows errors [V·r]. 0 partitions until a client joins. Authorization is cached until JWT refresh. Needs a vendor-schema shim and a logical slot over ~108 GB/day of WAL [V·r]. Deferred to F7 as a doorbell only (ruling Q2). |
| R2 | Postgres Changes over an outbox | Unsafe echo suppression; ~3k-subscriber ceiling; DELETE bypasses RLS [V·r]. |
| R3 | Own WebSocket hub via `pg_notify` | ~650 owned lines, a second auth entry point, a SPOF [V·r]. |
| R4 | Postgres Changes on the curation tables | Views cannot be published; DELETE skips RLS; `apply_rls` sets no headers [V·r]. |
| R5 | `refetchInterval`/focus on the data keys | Visibility-only focus; cost scales with board size × windows; Map caches re-render. |
| R6 | `broadcastQueryClient` | Experimental, breaks in patch releases; same-origin only; a second write path. |
| R7 | Fat events / `setQueryData` from payloads | A second rendering path that cannot merge onto pending patches. |
| R8 | API-layer emitter | Misses merges, signup, cascades and DML. |
| R9 | Statement-level transition-table triggers | 3 per table, ~340k empty fires a day, not deferrable [V·r]. |
| R10 | Clock from `max(updated_at)` | Deletes leave no trace. |
| R11 | Coarse "any write pending" queue | `painted` is synchronous; one hung write would stall everything. |
| R12 | Changing the global focusManager | App-wide behaviour change. |
| R13 | Origin-header echo suppression | Needs an origin marker (rail g); not needed after W0. |
| R14 | Admin-only canary wave | Measures nothing the gate does not, at 5 users. |
| R15 | A separate degraded-mode poll | The clock read *is* the fallback. |
| R16 | Explicit reconnect jitter | Interval phases are uniform from mount; back-off spreads errors. |
| R17 | Closing `own_account_id` via the clock | Couples identity display to sync (F4). |
| R18 | Insert-only outbox + `max(id)` | Needs a pg_cron trim. W0 plus the dedupe already make a lock conflict a visible, retryable 503. |
| R19 | Immediate (non-deferred) bump | Holds the counter lock for the rest of a merge transaction and blocks clicks; under W0 deferred failures are equally visible. |
| R20 | Web Push as transport | `sw.js` must show every push [V·r]. |
| R21 | Runtime kill-switch flag read by the clock read | Adds a read to the hot path; a forward revoke does the same. |
| R22 | Empty first-answer baseline (re-read every family per start) | Exact, but re-reads every mounted synced read on every page load, including the Browse map (≤ 22 MB [V]). The duration rule (Decision 14b) is equally exact under a 10 s commit bound. |
| R23 | Owner-rights view, no base-table grant | Restates the policy in the view body and runs as postgres (RLS bypass), the exact shape `_TENANT_VIEWS` forbids. The leak it removes is timing metadata that `pipeline_shares` visibility already bounds. |
| R24 | Client echo prediction (expected counter delta per own write) | Side effects (live-deal dismissal lift `api/pipeline.py:383-385` [V·r], cascades) make the delta unknowable; a mispredicted +1 hides a remote change. |
| R25 | Statement-order census across multi-family writers | Fragile (pins statement order in every writer, `route_curation` included). W0 makes the residual deadlock visible. |
| R26 | `QueryObserver` owner | Every inherited behaviour is overridden; a cache entry would need its own root and exclusion from clear/sweeps. |
| R27 | Holds by call-site discipline only | Misses mount refetches and stray invalidations; check 3 would be a convention. |
| R28 | App-wide ESLint ban on `invalidateQueries` | 105 sites in 39 files, mostly market data [V]: scope creep. The ban targets curation key factories only. |
| R29 | Q3 `settleChanged` (map-diff drop in place) | A second judge path that cannot cover adds. Replaced by the Q3(c) pointer-hold option, which reuses holds and covers both directions. |
| R30 | Deferring D6 to a separate ticket | 696 would turn latent D6 into reachable silent loss (W0). |

## 5. Subtraction ledger

| Deleted / replaced | Where | Wave | Closes |
|---|---|---|---|
| `holdQueries` + whole-value snapshot rollback + unconditional `onSettled` invalidation | `lib/useOptimisticWrite.ts:89-95,193-208` → `holdReads` + ledger + `revalidate` | W1 | D1 |
| `PIPELINE_REVALIDATE`, `pipelineRevalidate`, `revalidatePipeline` (caller `lib/mergedAdverts.ts:297`) | `lib/pipelineCache.ts:84-100` | W1 | — |
| `revalidateCollections` (14 sites) and its dead acting variants | `lib/collectionCache.ts:119-137` | W1 | D5 |
| `revalidateTags` (3), `revalidatePipelineShares` (2) | `lib/tagCache.ts:19-22`, `lib/pipelineShares.ts:49-50` | W1 | — |
| StageManager list + wrong comment | `pages/Pipeline.tsx:950-960` → `revalidateTables(qc,['pipeline_stages'],actAs)` | W1 | D2 |
| Direct curation invalidations | `CurationBlock.tsx:236-241`, `PropertyPhotos.tsx:92`, `curationSettle.ts:190-192` | W1 | — |
| ArtifactLinkRow `setQueryData`-on-success | `ArtifactLinkRow.tsx:48-61` | W1 | — |
| Seed-once drafts (6 editors) | Pipeline stage row, TagEditPopover, CollectionDetail, PhoneCollectionDetail, CurationBlock note, ArtifactLinkRow | W2 | D3 class |
| `remembered` read once; index-valued `target` | `PhonePipeline.tsx:245,258-268` | W2 | — |
| Bare `Depends(tenant_conn)` (68 route sites + 2 helpers) | `api/` → `TenantConn` | W0 | D6 |
| Stale "two places mount" | `lib/acting.ts:10-12`, `docs/design/pipeline-sharing.md:237` | W1 | — |
| "A revoked board reads empty" as the only revoke signal | `docs/design/pipeline-sharing.md:256` (rewritten: the clock is the signal; the empty-read guard stays) | W4 | — |
| Rule-22 cache doctrine naming settle/`PIPELINE_REVALIDATE` as the only re-read paths | `docs/architecture.md:3739-3776` (rewritten) | W1 | — |
| Stale "one pre-tenancy NULL `property_notes` row" | `.claude/skills/database/references/tenancy.md` | W3 | — |

**Kept on purpose:**
- the notification polls (Q1b);
- Feed's resume re-read;
- `useBuildSkew`, `serviceWorker.ts`, the acting `storage` sync and `useSharedBoardGuard`;
- job polls;
- staleTimes (the mount backstop);
- `refetchOnWindowFocus:false`.

**Not added:** no socket, no `realtime.*` object, no CI shim, no `migration_objects.py` change, no new dependency.

## 6. Waves

Each wave is one PR, with a draft PR opened early.
- **Claim:** claim the wave ticket (`python3 -m scripts.ticket_claim claim <n>`). The PR says `Closes #<wave>`; it uses `Refs #<spec>` (and `Refs #<gate>` where a gate is owed).
- **Merge:** merge `origin/main` and get CI green on the light; merge-coordinator light; Railway commit status after merge (`gh api repos/{owner}/{repo}/commits/<sha>/status`).
- **Build:** one heavy agent at a time locally.
- **Order:** W0 ∥ W1 ∥ W2, then W3 (after W0 is **live**), then W4 (after W1 and W2 merged and 696 applied). There is no W5: ruling Q2 needs no doorbell (F7).

**Tickets** (filed 2026-10-31):
- spec #2197 `Curation sync (U-SYNC)`;
- W0 #2198, W1 #2199, W2 #2200, W3 #2201, W4 #2202;
- `gate` #2203 after W4 (blocked by W4);
- F-items: `ready-for-human` where a ruling is needed, `ready-for-agent` where complete.

**Docs budgets:** CLAUDE.md is **not touched** (15 B free [V]). ROADMAP edits are byte-neutral or smaller (27 B free on 2026-10-31 [V]). Database SKILL.md (270 B free [V]) gets at most two pointer phrases; the rest goes to `references/tenancy.md` (uncapped).

**W0 `fix/tenant-commit-before-response` (backend + program charter; in progress, #2198, PR #2204).**
- Code:
  - `api/tenant_pool.py` (`TenantConn`; `require_account_id`/`require_acting_account_id` take it);
  - 68 route sites in `api/main.py`, `api/saved_sets.py`, `api/routes/{notifications,billing,property_download,stats_dashboards}.py`;
  - `pyproject.toml` `fastapi>=0.139`.
- Tests:
  - `tests/api/test_tenant_conn_scope.py`:
    - (a) census: no `Depends(tenant_conn` / `Depends(tenant_pool.tenant_conn` in `api/` outside the alias (AST);
    - (b) with `tenant_pool.tenant_transaction` patched to raise `DeadlockDetected` on exit, a card move answers 503 `db_busy` and a GET answers 5xx, never 200;
    - (c) one route that resolves `require_acting_account_id` and writes runs on **one** connection (a counter on the patched `psycopg.connect`).
  - The full `pytest -q tests/api`.
- Docs:
  - `docs/architecture.md` (API tenant pool paragraph: commits before the response);
  - database SKILL.md one phrase ("one transaction per request, committed before the response (`TenantConn`)");
  - `references/tenancy.md` (the alias rule + census);
  - **charter:** `docs/design/curation-sync.md` (this document), `docs/adr/0008-curation-sync-pulls-a-database-clock.md`, `GLOSSARY.md` (three terms);
  - `roadmap/operator-workflow-track.md` new "Phase U-SYNC" (W0 bullet);
  - `ROADMAP.md:47` `🟢 mostly done` → `🟡 U-SYNC` (shorter).

**W1 `cleanup/curation-read-scheduler` (SPA, no DB; built, PR #2205).** Valuable on its own: D1, D2, D5, rollback composition. What it built differently is in § 12's build ledger.
- New: `lib/revalidate.ts` + test; `lib/curationSync.ts` + test (no owner yet); `test/reads.ts` (`watchReads`: how a test sees a re-read, a fetch or an owed read; W2 and W4 tests use it or `isOwed`, since curation re-reads no longer go through `invalidateQueries`).
- Edited:
  - `lib/useOptimisticWrite.ts` (holds, ledger, header);
  - `lib/curationSettle.ts` (`lensTargets`, `settleMemberWrite` on holds, the stale-side rule of Decision 7);
  - `lib/queries.ts` (`curationKeys.anyCollection`);
  - `lib/auth.tsx` (`resetReads` before `clear()`);
  - `main.tsx`: a comment on why focus refetch stays off; `applySyncQueryDefaults` is called in W4;
  - every W1 ledger site; `pages/Pipeline.tsx` StageManager.
- `frontend/eslint.config.js`: one selector in the ONE block. It bans `CallExpression[callee.property.name=/^(invalidateQueries|refetchQueries|resetQueries)$/]` with a descendant `MemberExpression[object.name=/^(pipelineKeys|curationKeys|dismissalKeys|pipelineShareKeys)$/]`. `lib/revalidate.ts` needs no exemption: it pays with `query.fetch`, never `invalidateQueries` [V, W1]. The file-exemption list does not grow (`eslint.config.js:196-210` [V]).
- Tests:
  - `lib/hydration/hydration.test.ts` adds "SYNC_ROOTS disjoint from `['hydration']`";
  - a key-coverage vitest: every member of the four factories is prefix-covered by a `SYNC_TABLES` entry or listed in `NOT_SYNCED` with a reason.
- Docs: `docs/architecture.md` rule-22 cache doctrine rewrite and Frontend territory (scheduler + map); `lib/acting.ts`; `pipeline-sharing.md:237`. The track bullet rides in W0 (#2204).

**W2 `fix/curation-editors-follow-server` (SPA).**
- New: `lib/useServerDraft.ts` + test.
- The 6 editors.
- The drag hold (imperative, with stages).
- Phone pager fixes and the gesture hold.
- Tests per §9.
- Docs: `docs/architecture.md` Frontend territory ("a field over server state uses `useServerDraft`"); track bullet.

**W3 `feature/curation-clock` (DB). Prerequisite: W0 deployed.**
- `migrations/696_curation_clock.sql` (§3.2).
- `tests/_tenant_registry.py`: `curation_clock` in `TENANT_TABLES`; `BROWSER_WRITE_COLUMNS['curation_clock'] = frozenset()`; new `CLOCKED_TABLES: dict[str,str]` (table → family) and `NOT_CLOCKED: dict[str,str]` (table → reason). `NOT_CLOCKED` covers:
  - `curation_clock` (the clock itself);
  - `delegated_edits` (written in its curation row's transaction);
  - `property_pipeline_events` (same, board);
  - `notification_dispatches` (Q1b);
  - presets, groups, subscriptions, estimations, buildings, entitlements, dashboards and dashboard shares (F5).
- `tests/test_tenant_isolation_live.py`: `curation_clock_public` added to `_TENANT_VIEWS`.
- `tests/test_curation_sync_census.py` (offline):
  - (a) the trigger set the migrations leave == `CLOCKED_TABLES` == `SYNC_TABLES` table→family parsed from `frontend/src/lib/curationSync.ts`. The migrations are replayed in order: a drop of a trigger, of the bump function or of a clocked table removes a trigger, a table or trigger rename moves it, and every trigger left must be named `<table>_curation_clock`.
  - (b) `TENANT_TABLES ⊆ CLOCKED ∪ NOT_CLOCKED`; every reason non-empty.
  - (c) the newest family CHECK == `CURATION_FAMILIES` (TS) == the families in `CLOCKED_TABLES` == the seed's families.
  - (d) every clock trigger is `constraint … after insert or update or delete … deferrable initially deferred … for each row`.
  - (e) nothing silences a clock trigger without a `-- ci-allow-clock-disable: <reason>` line of its own: in a migration or in code, `DISABLE` or `ENABLE REPLICA` of a `*_curation_clock` trigger (or `all` / `user` on a clocked table, also as one action of several in an `ALTER TABLE`), or `session_replication_role`; in a migration after 696, a redefinition of `bump_curation_clock()`.
  - (f) no clock trigger on `listings|properties|listing_snapshots`.
  - (g) no `x-sync-origin` or origin GUC in `api/`; nothing in code or migrations but 696 names the dedupe GUC `curation_clock.bumped`.
  - (h) no DML on `curation_clock` in `api/ toolkit/ scraper/ scripts/ autodedup/` or in any migration after 696; 696 writes it exactly twice (the bump and the seed).
  - (i) the policy's board arm families map to tables == `_BOARD_POLICIES` tables ∩ `CLOCKED_TABLES`; the edit arm == (`_ACTING_POLICIES` − board) ∩ `CLOCKED_TABLES`; `shares` appears only in the `mine` arm.
  - (j) `THRESHOLDS["CURATION_CLOCK_TRIGGERS"] == len(CLOCKED_TABLES)`.
- `tests/test_curation_clock_live.py` + a `.github/workflows/migrations.yml` step beside `test_pipeline_shares_live` (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED: "1"`), else `test_live_lanes_census.py:100-105` fails [V·r].
- The health check with the `triggers` and `board_bumps` cells (§3.9) and its registry edits.
- No CI bootstrap change [V·r].
- Docs:
  - `references/tenancy.md`: the idiom; "follow, never act"; the metadata a recipient sees; never write or reset the clock; never disable without the annotation; the kill-switch runbook; fix the stale NULL note;
  - SKILL.md: one pointer;
  - `docs/architecture.md`: the DB half, a paragraph in rule 18 beside W0's (the file has no § Curation sync);
  - `health-dashboard.md` § 4;
  - `pipeline-verification.md`;
  - track bullet.
- Apply via MCP `apply_migration` **before merge**, and tell the coordinator. Verify with the §9 catalog row.

**W4 `feature/curation-sync` (SPA owner). After W1, W2 and the 696 apply.**
- New: `lib/curationSyncOwner.ts` + test.
- `lib/queries.ts` `fetchCurationClock` (the select list stays apostrophe-free).
- `lib/phoneShell.ts` exports `isPhoneFrame()` (the hook reuses it).
- `lib/auth.tsx` mounts the owner.
- `main.tsx` calls `applySyncQueryDefaults(queryClient)` beside the owner (W1 left it uncalled, so curation keeps its reconnect refetch until then). This is W4's, and it MUST land with the owner.
- `lib/auth.test.tsx` and `auth.push.test.tsx` mock `@/lib/curationSyncOwner`.
- `tests/test_curation_clock_read_contract.py`: `fetchCurationClock`'s columns ⊆ the latest `curation_clock_public` columns (precedent `tests/test_property_page_read_contract.py`).
- Health cells `reads` and `load`.
- Docs: `docs/architecture.md` Frontend territory ("the clock is the re-read trigger; staleTime is the backstop"); `pipeline-sharing.md:256`; track (phase built, gate owed).
- After merge:
  - Railway status;
  - read-only production check (§9);
  - the operator's two-window recipe;
  - start the gate clock.
- **Gate #2203** (label `gate`, 7 days). Pass when all of these hold:
  - `curation_sync` has no Problem;
  - clock read `mean_exec_time ≤ 2.5 ms`, and its share ≤ 1 % of statement time (expected ≈ 0.2 %);
  - no `deadlock detected` log line naming `bump_curation_clock` (MCP `get_logs`, postgres);
  - Railway `http-error-rate` on the API not above its 7-day pre-rollout baseline;
  - Browse map/stats/count RPC calls per day ≤ baseline + 25 % (fan-out);
  - the operator's on-device steps passed.

  If it fails, use the reader kill switch (§3.9) and reopen W4. If the API raises commit-time 5xx caused by the clock, use the writer switch. Whoever closes the gate flips `ROADMAP.md:47` back to 🟢.

## 7. Risks and mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | Latency up to T | Ruling Q2 (≤ 5 s desktop, ≤ 15 s phone); the F7 doorbell would need no rework |
| 2 | Cost (§3.8): fan-out dominated by the Browse map | `LENS_REREAD` switch, per-answer coalescing, `load` cell, gate thresholds |
| 3 | iOS suspension / bfcache; a hung read across suspension | No socket; `visibilitychange`/`pageshow` abort and re-read; on-device step [A: iOS fires one of them] |
| 4 | Token expiry | `getSession()` per read; a 401 is a permanent-error read (≤ 1 per 60 s) until the session resolves |
| 5 | PostgREST/DB outage | Transient back-off; errored debts re-paid ≤ 3×; the first good answer catches up exactly once |
| 6 | RLS mistake (read recipient sees curation families; stranger rows) | One audience function; census (i); `_TENANT_VIEWS`; `$post$`; live matrix incl. base table, RPC, `x-act-as` and downgrade |
| 7 | Ordering / duplication / loss | Inequality on persistent counters; owed reads paid only by post-change reads |
| 8 | Publications / slots | Untouched |
| 9 | Deadlock on counter rows | Per-tx dedupe; ordered within a firing; W0 → visible retryable 503; gate reads postgres logs, not the global counter (382 baseline [V·r]) |
| 10 | Commit-time failure | W0 (5xx, rollback, toast); writer kill switch |
| 11 | A hygiene migration silences the clock | Census (e) incl. named disables; `triggers` cell |
| 12 | Swallowed re-read (initial load, inactive in-flight, hold cancel, manual setData) | Owed reads (Decision 14) |
| 13 | Startup race | Duration rule (Decision 14b) + start-time owe |
| 14 | Leaked hold | Epoch reset; TTL 140 s online time + warn |
| 15 | Rows, counts, map and stats of an open list stay as read until navigation (ruling Q3a) | Marks repaint now; a click on a row a remote change left stale is settled by the stale-side rule (Decision 7): under a stale map nothing drops and a cohort that still says no re-reads whole; under a stale list a click that leaves the row out drops it in place and only the aggregates re-read |
| 16 | Self-echo cost | ≤ 1 family re-read set per answer per writing window; accepted |
| 17 | Nudge before commit | Resolved by W0 (success = committed) |
| 18 | A window left visible overnight | ≈ 17k reads/day ≈ 31 MB; F12 idle back-off only if measured |
| 19 | Extension panels never update | Q4 |
| 20 | Future CSP | Same-origin REST to `*.supabase.co`, already required |
| 21 | Signup fails at commit | The new account has no other writer (no contention, no FK race); only a bump bug can fail it; the live lane's signup case catches that pre-merge |
| 22 | Share-to-victim bumps the victim's `shares` clock | One `pipelineShareKeys` read per visible window per read; no rate limit on grants; accepted, named |
| 23 | Metadata visible to recipients (counter volume, last-change time) | Documented in `tenancy.md`; no PII or row ids (R23) |
| 24 | A note deleted elsewhere while edited unmounts its editor and loses the draft | Accepted (the note no longer exists); last-writer-wins otherwise (Q5) |
| 25 | A dead clock read is invisible (no client telemetry, `lib/lazyChunk.ts:123-129` [V·r]) | Read-contract test; `reads` cell |

## 8. Rulings (operator, 2026-10-31: all five in; Q3 as (a), the rest as recommended)

**Q1. Which statuses follow across windows? RULED: all curation plus the pipeline and collection shares; nothing else.**
- (a) **In:** stage, Skryto, Kolekce, Štítky, Poznámky/Prohlídky/soubory, Odkaz, share grants and revokes.
- (b) **Out:** the notification badge, feed and read state. Their visibility also depends on deleted watchdogs and on merges changing a property's first-seen date, which the clock cannot see, so the 30 s and 60 s polls stay as they are.
- (c) **Out:** ad Aktivní/Neaktivní and price (the Pipeline "Stav" filter). It is scraper data at about 10⁴ changes a day, and it refreshes with every board re-read.
- (d) **Out:** estimation runs started elsewhere (F5).

**Q2. How fast must a change from another device reach a window you are looking at? RULED: (a) and (c), no doorbell.**
- (a) **Desktop ≤ 5 s** (mean ≈ 2.5 s) with no new infrastructure; same-browser tabs ≈ 0.5 s; a hidden window is current the moment it is shown.
- (c) **Phone screen (under 768 px): a read every 15 s while open**, and at once when the app is reopened. A request every 5 s keeps the phone's radio awake [A].
- (b) ≈ 1 s via a Realtime doorbell: **not built**; follow-up F7, only on a future ≈ 1 s ruling. It would add a WAL-decoding slot (cost unmeasured), a CI shim, a dashboard toggle and a feature without a GA label, and open a revocation window: a recipient whose share is revoked keeps receiving content-free "something changed" pokes until their login token refreshes (≤ 1 h at Supabase's default [A]).

**Q3. An open Browse, Feed or collection list when a property changes on another device. RULED: (a).**
- (a) **Everything the list shows (rows, counts, map, stats) stays as read until you navigate; only the per-card marks update.** Cheapest, and fully consistent: `LENS_REREAD = { lists: 'mount', aggregates: 'mount' }` (Decision 7). The window's own writes keep today's behaviour, read as ruling 9's per-property judge (§ 12).
- (b) *Recommended, not ruled:* counts, map and stats update within seconds; rows refresh on the next navigation. A later ruling would switch `aggregates` to `'now'`; the stale-side rule is already built (Decision 7).
- (c) *Not ruled:* rows also enter and leave while the pointer is outside the list; each change re-reads every loaded page.

**Q4. The Chrome extension as a receiver. RULED: (i), it stays a sender.** Re-reading what is on screen when the portal tab becomes visible (ii) is follow-up F1.

**Q5. Two devices moving the same card at once. RULED: (a), last-writer-wins.** Sync shrinks the stale window from "until you reload" to ≤ 5 s. Rejecting a move made from an outdated stage with a conflict and a re-read (b, an API contract change across 4 writers) is follow-up F3.

## 9. Test plan

**Vitest** (CI `test.yml`: `tsc --noEmit` + `npm test`).
- `lib/revalidate.test.ts`:
  - no hold → `query.fetch` issued synchronously for active matches; inactive ones paid on `observerAdded`;
  - overlapping hold → parked, one payment on the last release; prefix overlap in both directions; `exact` honoured;
  - `'mount'` debts never fetched while mounted, paid on remount, and they survive a manual `setQueryData`, a rollback and a landed `fetchNextPage` (iii);
  - an initial load started before `since` is awaited and re-paid;
  - an inactive in-flight read that lands pre-change stays owed and is paid on remount (iv);
  - a fetch started after `since` is deduped, not restarted (starvation);
  - errors re-paid ≤ 3 then `'mount'`; CancelledError stays owed;
  - a fetch started on a held key (mount, reconnect, stray invalidate) is cancelled and owed; `fetchNextPage` passes; a page in flight when the hold registers is dropped, not owed;
  - a `'mount'` debt: a second reader joining a mounted query pays nothing; a disabled first reader pays when enabled;
  - ledger: A fails while B is pending on `members` → B's patch survives (ii); A fails after B fails → no resurrection; a count patch is never double-applied; an outside write becomes the base, never patched twice; a patch keeps its base's read time through a paint and a rollback;
  - epoch: after `resetReads`, an old rollback or release paints and parks nothing (vi), and a settle releasing an old hold owes nothing;
  - TTL counts only online time; `HOLD_TTL_MS` equals `REQUEST_DEADLINE_MS + 10_000`;
  - release + settle keys → exactly one fetch (the D1 regression, using the real `createMutationCache()` and the `deferred()` helper, as in `useOptimisticWrite.test.tsx:37-47`).
- `lib/curationSync.test.ts`:
  - every `SyncTable` × {own, owner}; owner targets are `['acting',O,…]` only (Decision 8);
  - `pipeline_stages` reaches `members` (D2);
  - `tableReads(['property_pipeline','property_dismissals'], null)` is set-equal to `[members, board, dismissals]`;
  - `clockChanges` changed/appeared/disappeared/unchanged, and the first-answer age rule;
  - `LENS_REREAD` targets: a remote lens change owes Browse lists and aggregates `'mount'` and nothing `'now'`; an open collection page waits for its next mount while the marks re-read now;
  - key-coverage census; local-write keys covered by their tables.
- `lib/curationSettle.test.ts`:
  - an aggregate re-read in flight at click time → `browse-count` correct after settle (i);
  - the stale side, both branches, each with a click that leaves the row out: a member map that is owed, or was read before the list, says "no" → that cohort re-reads whole and nothing drops; a map read no earlier than the list, nothing owed, says "no" → the row drops in place and only the aggregates re-read.
- `lib/useServerDraft.test.ts`:
  - follows the server while untouched;
  - a server change while focused is never written back;
  - `edited()` returns null unless the user typed.
- `lib/curationSyncOwner.test.ts` (fake timers as in `useBuildSkew.test.tsx`; `vi.stubGlobal('BroadcastChannel', Fake)`; deferred `fetchCurationClock`):
  - first answer: only families within `elapsed + 10 s` revalidate, including when the first good answer arrives 40 s after start (v);
  - a board counter change → exact targets; a disappearing owner row → that family's acting targets + own shares;
  - hidden: no interval, a `{v:1}` received while hidden sends no request;
  - visible/pageshow abort the in-flight read and read once; focus/online/nudge are coalesced with a ≥ 1 s gap;
  - transient back-off; permanent 403 → no interval, ≤ 1 read per 60 s, no toast;
  - `fetchCurationClock` never sets `x-act-as`;
  - `stop()` closes, unsubscribes, and drops a late answer;
  - StrictMode double mount idempotent;
  - `isSupabaseConfigured()` false → no start;
  - phone frame → 15 s.
- `lib/auth.test.tsx`: an account switch runs stop → `resetReads` → `clear()` → start in order (`h.listeners` fake); sign-out runs stop.
- `pages/Pipeline.test.tsx`:
  - an outside rename followed by focus and blur of an untouched field writes nothing (D3);
  - a remote target during a drag lands after the drop;
  - a drag that starts while a board re-read is in flight shows no repaint mid-drag (vii);
  - StageManager reaches `members`.
- Editor tests for TagEditPopover, CollectionDetail, PhoneCollectionDetail, CurationBlock and ArtifactLinkRow (the same D3 case).
- `components/phone/PhonePipeline.test.tsx`:
  - an archive of the viewed stage falls to the neighbour, not the mount-time stage;
  - a reorder during a tab-tap scroll lands on the tapped stage;
  - a remote add during an active touch waits.

**Offline pytest:**
- W0 `tests/api/test_tenant_conn_scope.py`;
- `tests/test_curation_sync_census.py` (a)–(j);
- `tests/test_curation_clock_read_contract.py`;
- health registry pins;
- existing rails unchanged (`test_migration_rls_grants` rules 2/3/5 and the stray-acting rail; tenant live sweeps; `test_pipeline_shares_live` exact `delegated_edits` counts, because the bump writes only `curation_clock`).

**Live lane `tests/test_curation_clock_live.py`** (replayed schema; owner A, read recipient B, edit recipient C, stranger D and E, the party the share and cascade cases grant to and take back from, via the `tenants`/`_scoped(sub)` pattern):
1. A tenant-shaped write on each clocked table bumps exactly its (account, family) once. A second connection sees nothing before COMMIT.
2. Rollback, savepoint rollback, a 0-row statement and a no-op UPDATE bump nothing.
3. A 500-row `collection_properties` insert bumps `collections` exactly once.
4. A delegate's write (`x-act-as`, edit share) bumps the **owner**.
5. A service-role `merge_property_set` over A and D bumps both. An autocommit service-role write (`db.connect`) bumps.
6. FK cascades bump: a collection delete → `collection_properties`; a tag delete → `property_tags`. The nested `property_notes_files_follow` UPDATE bumps `notes`. A child that names its parent's (account, family) is folded into the parent's bump by the dedupe, so these cases also count `bump_curation_clock()` firings (`pg_stat_xact_user_functions`). A property delete, which writes no parent table, bumps every cascaded family (A's `collections` and `tags`, D's `dismissals`); a service-role UPDATE moving a row's `account_id` from A to D bumps both accounts.
7. A share grant, a revoke and an edit→read downgrade each bump both parties' `shares`. After the downgrade, C's next read loses the 5 edit families.
8. Signup (`handle_new_user`) succeeds and bumps the new account's board and collections. Deleting an account that holds curation succeeds.
9. **Forced commit-time failure:** a second connection holds `curation_clock (A,'board')` `FOR UPDATE`; the writer, with a low `lock_timeout`, fails at **COMMIT** and its card change is absent afterwards. This is the case W0 surfaces as a 5xx.
10. Visibility through `curation_clock_public` **and** the base table:
    - A sees all own families (`mine=true`);
    - B sees A's `board` only;
    - C sees board + 5 families, never `shares`;
    - D sees nothing;
    - with `request.headers` `x-act-as` (valid owner, foreign owner, garbage) each caller sees own rows only and no error;
    - `curation_clock_audience()` returns only own accounts and share owners;
    - anon is denied.
11. `EXPLAIN (format json)` of the read as a tenant uses `curation_clock_pkey`, with no seq scan on the clock.
12. The `board_bumps` SQL returns 0 after scripted card writes.
13. Catalog: 12 enabled deferred triggers; definer bump; invoker audience; no browser or service-role DML on the clock; view `security_invoker`; seed complete.

**Manual recipe** (operator, production, own data):
1. Two windows on `/pipeline`; move a card in B → A follows in ≈ 0.5 s.
2. Move from the phone, then from the extension → A follows within 5 s.
3. Rename a stage in B → A's column and the Browse badges follow.
4. Dismiss on the phone → A's Browse card shows the eye; the row, the count, the map and the stats stay until A navigates (Q3a).
5. Minimise A, move on the phone, restore A → current.
6. Drag in A while the phone moves another card → nothing moves until the drop.
7. Revoke a share → the recipient toasts within 5 s.
8. Network panel on an idle visible A: one `curation_clock_public` GET per 5 s and no data reads; a hidden tab makes none.
9. On the iPhone PWA: lock, move on desktop, unlock → current within one read.

**Production read-only check** (MCP, one row):

```sql
select (select count(*) from pg_trigger where tgfoid = 'public.bump_curation_clock()'::regprocedure
          and not tgisinternal and tgdeferrable and tginitdeferred and tgenabled = 'O') trg,
       has_function_privilege('authenticated','public.bump_curation_clock()','EXECUTE') auth_exec,
       has_table_privilege('authenticated','public.curation_clock','INSERT,UPDATE,DELETE,TRUNCATE') auth_dml,
       (select count(*) from curation_clock) - 7 * (select count(*) from accounts) seed_gap,
       (select max(changed_at) from curation_clock) last_change,
       (select count(*) from property_pipeline_events e where e.created_at > now() - interval '24 hours'
          and not exists (select 1 from curation_clock c where c.account_id = e.account_id
                            and c.family = 'board' and c.changed_at >= e.created_at)) missed,
       (select round(mean_exec_time::numeric, 2) from pg_stat_statements
         where query ilike '%curation_clock_public%' order by calls desc limit 1) read_ms
```

Expected: `12 / false / false / ≥ 0 / advancing / 0 / ≤ 2.5`, plus the Railway status for the merge SHA.

## 10. Assumptions validated

| Claim (source) | Verdict |
|---|---|
| Rule 22: card writes = ONE hook over ONE policy | TRUE [V·r]; stage, collection, tag, note and link writes are not card writes |
| Rule 22: writes go through `tenant_conn` | TRUE for API and extension; FALSE for carriers, signup and the residual grant [V·r] → triggers |
| `tenant_conn` commits before the client learns the outcome | **FALSE** (D6) [V] → W0 (`TenantConn`, #2198) |
| `TENANT_TABLES` makes a table SELECT-only | **FALSE**: it is the DML allowlist (`_tenant_registry.py:1-6`) [V] → `BROWSER_WRITE_COLUMNS` frozenset |
| `test_tenant_views_are_security_invoker` covers new views | **FALSE**: hand list (`:48-60`) [V] → added |
| Health check "registered like migration_drift" | **FALSE**: GitHub-hosted, and key must equal fn suffix (`:452`) [V] → worker alarm `curation_sync` |
| `pg_stat_database.deadlocks` delta 0 as a gate | **FALSE**: baseline ~27 a day [V·r] → logs |
| Kill switch = revoke the view | Partial: also base table + RPC, and the client must stop [V] |
| Hidden tabs cost 0 | FALSE as v1 specified (nudge/online refetch) [V·r] → visibility-gated owner |
| v1's "probe" term | Collides with GLOSSARY (`:397,429`) [V] → "clock read" |
| F10 "revoke residual `authenticated` DML" | **FALSE premise**: load-bearing for `tenant_pool`; rejected in `PROGRAM.md:823-825` [V] → restated as F10 |
| Q1b deletes the polls | Unsafe: `VISIBLE_SQL` reads unclocked relations [V·r] → recommended out |
| GLOSSARY Curation covers shares | FALSE (`:190-192`) [V] → the term names "and the shares that decide who sees it" |
| Docs budgets | database SKILL.md 270 B, CLAUDE.md 15 B, ROADMAP.md 68 B free [V] → `tenancy.md`; CLAUDE.md untouched |
| `Query.cancel` is async | FALSE: reverts synchronously [V] → hold without `await` |
| The settle microtask ordering protects `settleMemberWrite` | TRUE today [V·r]; replaced by holds (no ordering reliance) |
| SurvivorRedirect only on initial null | FALSE: any null render (`PropertyDetail.tsx:280`) [V]; the property read is not clocked (F14) |
| Shared-note visibility depends on `collection_properties` | TRUE (610:136-145) [V] → in the map |
| Facts: TanStack 5.59; 6 supabase mocks; 87 `tenant_conn` refs | 5.100.9 [V·r]; 10 [V·r]; 68 route sites + 2 helpers (71 `Depends` grep hits with one docstring; 87 incl. prose) [V] |
| Still [A] | iOS resume event delivery; map typical size; clock-read cost at scale; worker read of `pg_stat_statements`; `service_role` in the CI bootstrap; Realtime topic form (F7); the exact FastAPI version introducing `scope` |

## 11. Follow-ups (out of scope)

- **F1** Extension as receiver (Q4 ii).
- **F2** Collection-share parties follow each other's shared curation: an audience arm over `collection_shares`, plus recipient bumps for `collection_properties` and shared notes.
- **F3** Stale-intent 409 on moves (Q5 b).
- **F4** `own_account_id` / "vámi" (`roadmap/operator-workflow-track.md` § Phase U-PIPE-SHARE, its **Owed** note).
- **F5** Presets, dashboards, watchdogs, estimation runs and agent Runs/approvals (`MVP.md:57-58`) join the clock: one trigger + one map row each.
- **F7** The Realtime doorbell, only on a future ≈ 1 s ruling (ruling Q2: ≤ 5 s desktop, ≤ 15 s phone without it); the sketch follows this list.
- **F8** "Allow public access to channels" **off**: recommended **now** as a zero-code operator click, independent of this build. Nothing uses Realtime, and the anon key opens public channels today.
- **F9** Structural sharing for Map-shaped caches (D4), if re-render cost shows.
- **F10** (restated) A separate tenant write role, then revoke browser curation DML.
- **F11** NOT NULL on the four nullable curation `account_id` columns.
- **F12** Idle back-off for visible but untouched windows, if measured.
- **F13** A "moved elsewhere" cue.
- **F14** An open property merged away elsewhere follows its survivor: re-read the open property when an own curation family moves.

(F6 became W0.)

**F7 sketch: the Realtime doorbell** (`feature/curation-sync-doorbell`, its own migration number claimed then). Built only on a future ≈ 1 s ruling; ruling Q2 needs none. Kept as the reviews left it.
- The bump also calls `realtime.send(jsonb_build_object('v',1), 'poke', 'sync:'||account||':'||scope, true)`, deduped per (tx, topic) by the same GUC. Scopes: `board` = {board}, `curation` = {dismissals, collections, tags, notes, links}, `own` = {shares}.
- Policy:

  ```sql
  create policy curation_sync_receive on realtime.messages for select to authenticated using (
    realtime.messages.extension = 'broadcast'
    and (current_setting('request.jwt.claims', true)::jsonb ->> 'client_id') is null   -- MCP OAuth tokens never (PROGRAM.md:648)
    and case when (select realtime.topic()) ~ '^sync:[0-9a-f-]{36}:(board|curation|own)$' then exists (
          select 1 from public.curation_clock_audience() a
           where a.account_id = split_part((select realtime.topic()), ':', 2)::uuid
             and case split_part((select realtime.topic()), ':', 3)
                   when 'board' then true when 'curation' then a.edit else a.mine end)
        else false end);
  ```

  Never an INSERT policy (census rail). The topic form under Realtime is [A]; verify in the lane.
- Same PR:
  - the `scripts/ci_db_bootstrap.sql` realtime shim (inserting `send`, production shape at realtime `schema_migrations` 20261002120000; why Realtime is at-most-once and what its slot costs in WAL: § 4 R1);
  - the `scripts/migration_objects.py` schema-aware policy idents (`:215-217,329-333`) + test;
  - a live matrix under the realtime GUCs (claims, headers, role; **no method**): owner on 3 scopes; read recipient on board only; edit recipient on board + curation, never own; stranger nothing; malformed topic → false without error; `x-act-as` fails closed; a `client_id` token denied;
  - a health arm: `WarnSendingBroadcastMessage` count and partition count.
- The owner joins private channels for its audience and leaves an owner's channels when that owner's rows vanish. A poke means "read now". `SYNC_INTERVAL_MS` becomes 30 s.
- Operator: Dashboard → Realtime → "Allow public access to channels" **off** (F8).
- Go/no-go: slot lag p95 ≤ 64 MB and never > 512 MB; spill bytes; 0 send warnings once partitions exist.
- Billing: per recipient (1 sent + 1 per receiver); 500 peak connections are included, then $10 per 1,000. Under a spend cap, 500 concurrent connections is a hard limit [V·r].

## 12. Changes from the adversarial review

The review's ledger, kept as written. Since the rulings, "W5" below is follow-up F7 (Q2). The orchestrator read Q3(a)'s "the window's own writes keep today's behaviour" (2026-10-31) as echoing this design's "local writes keep ruling 9's per-property judge unchanged", not as dropping C3's stale-side rule, so that rule stays as W1 built it (Decision 7). That reading changes one own-write case versus today (Decision 7) and is the operator's to confirm.

| ID | Objection (verdict) | Outcome |
|---|---|---|
| S1 | `TENANT_TABLES` ≠ SELECT-only (REFUTED) | **Changed**: `BROWSER_WRITE_COLUMNS['curation_clock']=frozenset()`, `$post$` privilege asserts, live asserts (Decision 22) |
| S2 | View not in `_TENANT_VIEWS` (WEAKENED) | **Changed**: added + `$post$` `security_invoker` assert |
| S3 | Audience "stated once" restates ruling 2 (WEAKENED) | **Kept with rail**: census (i) pins the arms to `_BOARD_POLICIES`/`_ACTING_POLICIES`; "follow, never act" in `tenancy.md`; restatement shown unavoidable (Decision 5) |
| S4 | Leak "nothing more" (WEAKENED) | **Text fixed; accepted**: counter volume and last-change time documented; owner-rights view rejected (R23) |
| S5 | Revoke on next read (HOLDS) | Added the downgrade live case |
| S6–S8, S10 | Definer/invoker, Realtime GUCs, token expiry, rails parse (HOLDS) | Explicit revokes kept; audience comment reworded; W5 uses the audience only |
| S9 | "Never acts" untested (WEAKENED) | **Added**: vitest + `x-act-as` live cases |
| S11 | Only the DB advances the clock (WEAKENED) | **Changed**: `service_role` DML revoked; census (h); "never reset" doctrine |
| S12 | W4 doorbell revocation and spec (WEAKENED) | **Changed**: W5 policy (`extension`, CASE-guarded cast, scope map, `client_id` refusal), live matrix, revocation window as part of ruling Q2b |
| S13 | Kill switch partial (WEAKENED) | **Changed**: revokes view + table + RPC (Decision 19) |
| S14 | Nudge throttle (HOLDS) | Nudges go through the 1 s min gap |
| S15 | Account switch (HOLDS) | Added: late-answer-after-stop test |
| S-M | Abuse note; signup at commit; F8 now | Risks 21–22; live case 8; F8 marked "do now" |
| C1 | Lossless refuted client-side (REFUTED) | **Changed**: owed reads paid only by post-change fetches; holds owe what they cancel; `isInvalidated` never the carrier (Decision 14) |
| C2 | Holds incomplete; rollback composition; reconnect bypass (WEAKENED) | **Changed**: patch ledger (rebase), cache-enforced holds, `refetchOnReconnect:false` on `SYNC_ROOTS` (Decision 9, 12) |
| C3 | Q3a marks fragile; reconnect; click on a stale row (WEAKENED) | **Changed, BUILT in W1 (repaired form)**: `'mount'` debts in the scheduler; lists not marked stale; the stale-side rule judges by owed reads and by read time (Decision 7) |
| C4 | 30 s window inexact (WEAKENED) | **Changed, partly rebutted**: duration rule `age ≤ elapsed + 10 s` + start-time owe; empty baseline rejected on cost (R22) |
| C5 | D6 makes losses silent (REFUTED) | **Changed**: W0 prerequisite (Decision 17) |
| C6 | Family order not global (WEAKENED) | **Changed**: per-tx dedupe + W0 makes the residual visible; ordering census rejected (R25) |
| C7 | Drag hold late, no stages (WEAKENED) | **Changed**: imperative hold in `onDragStart` incl. stages; holds cancel in-flight reads |
| C8 | Phone pager (WEAKENED) | **Changed**: `remembered` sync, stage-id target, gesture hold |
| C9 | Release + settle double fetch (HOLDS nit) | **Changed**: one batch |
| C10 | Acting mapping (HOLDS) | No change |
| C11 | Identity determinism (WEAKENED) | **Changed**: `resetReads` epoch in the identity effect |
| C12 | TTL for paused writes (WEAKENED) | **Changed**: online-time TTL |
| C13 | Rule 4 too narrow (WEAKENED) | **Subsumed** by owed reads |
| C14 | Aggregate starvation (HOLDS caveat) | **Changed**: dedupe onto a fetch started after `since` |
| C15 | Reconnect second path (HOLDS caveat) | **Changed**: `refetchOnReconnect:false` on `SYNC_ROOTS` |
| C16 | `PIPELINE_REVALIDATE` deep-equal (WEAKENED) | **Changed**: set-equality; no Q1b delta (Q1b out) |
| C-M | Race tests (i)–(vii); `settleChanged` spec; settle microtask | **Added** to §9; `settleChanged` replaced by Q3(c) pointer hold (R29); `settleMemberWrite` on holds |
| O1 | Cost math incomplete (WEAKENED) | **Changed**: §3.8 full model, map named dominant, 26-window breakpoint, ongoing `load` cell |
| O2 | W4 billing (HOLDS) | Stated in W5 |
| O3 | Hidden tabs cost (REFUTED) | **Changed**: every trigger visibility-gated; test |
| O4 | Health cell false red, wrong registration (REFUTED) | **Changed**: seed; `triggers` arm; worker alarm spec + 4 registry edits |
| O5 | Deadlock gate (REFUTED) | **Changed**: postgres-log gate |
| O6 | Kill switch sheds nothing (REFUTED) | **Changed**: permanent-error mode; writer runbook; polls kept |
| O7 | Q1b polls deletion unsafe (REFUTED) | **Changed**: Q1b recommended out; polls stay |
| O8 | No CI shim (HOLDS) | No change |
| O9 | Rails don't cover new objects (WEAKENED) | **Changed**: S1/S2 |
| O10 | First commit-time code; lock duration (WEAKENED) | **Changed**: W0; lock described as "until commit flush" |
| O11 | O(N²) bulk updates (WEAKENED) | **Changed**: per-tx dedupe; no backfill exception needed |
| O12 | iOS hung read; phone battery (WEAKENED) | **Changed**: abort on resume; phone cadence ruling Q2c |
| O13 | No back-off (HOLDS caveat) | **Changed**: back-off |
| O14 | Observability gaps (WEAKENED) | **Changed**: named-disable census (e), read-contract test, `reads` cell |
| P1 | `NOT_CLOCKED` must include the clock (WEAKENED) | **Changed** |
| P2 | Health key/fn naming (WEAKENED) | **Changed**: `curation_sync`/`check_curation_sync` + docs sections |
| P3 | Docs budgets (WEAKENED) | **Changed**: `tenancy.md`; CLAUDE.md untouched; ROADMAP byte-neutral |
| P4 | F10 wrong (REFUTED) | **Changed**: restated |
| P5 | D6 ruling or W0 (WEAKENED) | **Changed**: W0 |
| P6 | Q1b and vocabulary (WEAKENED) | **Changed**: Q1b out; GLOSSARY wording covers shares |
| P7 | North-star exceptions; adds; merged-away property (WEAKENED) | **Changed**: exceptions named in §1; Q3 covers adds; F14 |
| P8 | D3 in other editors (WEAKENED) | **Changed**: `useServerDraft` for six editors; ArtifactLinkRow via the map |
| P9 | Regex census misses variable keys (WEAKENED) | **Changed**: ESLint selector in the ONE block, key-coverage vitest, cache-enforced holds; app-wide ban rejected (R28) |
| P10 | Dead read invisible (WEAKENED) | **Changed**: read-contract test + `reads` cell |
| P11 | Phone battery; fan-out (WEAKENED) | **Changed**: Q2c; §3.8 |
| P12 | F1/F3 deserve rulings (WEAKENED) | **Changed**: Q4, Q5 |
| P13 | Bookkeeping per wave; ledger doc sites; tickets (WEAKENED) | **Changed**: per-wave track edits; `pipeline-sharing.md:237,256`; spec + wave + gate tickets |
| P14 | StrictMode; `isSupabaseConfigured` (HOLDS caveat) | **Added**: tests + gate |
| P15 | 696 free (HOLDS) | Claim with the coordinator |
| P-M | `delegated_edits` named; extra live cases; vitest list; ADR-0008; agent Runs page | **Added** (W3 registry, §9 live 5–9, W0 ADR, F5) |
| Own | "probe" collides with GLOSSARY | **Changed**: "clock read" |
| Own | v1's `stop()` "mark synced keys stale" redundant (identity effect clears on every change incl. sign-out [V]) | **Removed** |

### Build ledger (what each wave changed versus this document)

Where a wave built something other than the design, the section concerned now says what was built, and a line here records the change. For W1 the code is the truth; for W2–W4 this document is. Each line names the file it lives in (`frontend/src/` omitted).

**W0, PR #2204 (#2198).** Its deviations are written where they apply: § 2 (68 route sites + 2 helpers), § 3.6 ("As built") and § 6 W0. The API comments that described the old lifetime (`api/tenant_pool.py` `read_conn`, `api/main.py` `post_estimations`, `api/dependencies.py` `require_token` and `token_claims`) now name `TenantConn` and its commit before the response.

**W1, PR #2205 (#2199).** Three commits on `934e904a3`, each with `Co-Authored-By: Claude Opus 5.5` (the implementer) [V].
1. `lib/curationSync.ts` `SYNC_TABLES`: `tags` lists only `curationKeys.tags` (`property_tags_public` reads only `property_tags`); `property_tags` also lists `curationKeys.tags` (GET /tags counts `listing_count`); `collection_shares` own reads also list `propertyCollectionMembers` (policy `collection_properties_shared_read`, 610, joins `collection_shares`). § 3.5's table shows this.
2. `components/curation/TagEditPopover.tsx`: a tag DELETE names both `tags` and `property_tags` (migration 202 cascades the memberships).
3. `lib/curationSync.ts`: acting reads are NOT mapped under `pipeline_shares` (its `reads` are empty). When an owner's clock row disappears, the owner (W4) re-reads that family's acting keys. `NOT_SYNCED` is empty.
4. `lib/revalidate.ts`, `lib/curationSync.ts`: `revalidate` and `revalidateTables` return `Promise<void>`, which settles when the reads they started or joined settle; parked and `'mount'` reads are not awaited. `CurationBlock.tsx`, `PropertyPhotos.tsx` and `ArtifactLinkRow.tsx` await it before closing an editor. `useOptimisticWrite`'s `onSettled` does not return it.
5. `lib/revalidate.ts`: `repayOwed(qc)` is exported for W4's owner. An errored debt is retried at most 3 times, then falls to `'mount'`.
6. `lib/curationSettle.ts`: `lensTargets(qc, lens, reread)` takes the `LENS_REREAD` modes as a parameter, and `settleMemberWrite(qc, set, propertyId, elementId, member, { actAs, reads })` takes the caller's table reads in one options object and returns `void`. Both avoid an import cycle (curationSync imports curationSettle). `lib/collectionCache.ts` and `lib/tagCache.ts` pass `reads: tableReads([...], actAs)`.
7. `lib/revalidate.ts`: `HOLD_TTL_MS` is the literal `140_000`, pinned equal to `REQUEST_DEADLINE_MS + 10_000` by `revalidate.test.ts`. Tests that mock `lib/api` whole would otherwise fail to load the writers.
8. `lib/revalidate.ts` ledger: a write that lands on a held query from outside the ledger (a landed page, a site's `setQueryData` of a server answer) becomes the new base with the patches baked in, and the ledger drops them. `rollback()` replays base + the remaining patches, so a rollback is exact only when no such write landed; after one, the query is left to the re-read that follows. `'mount'` debts survive a rollback.
9. `lib/revalidate.ts`: a page fetch already in flight when a hold registers is cancelled with `revert` and NOT owed (the sentinel or the next scroll asks again). A page that starts while the hold is on passes.
10. `lib/revalidate.ts`: a `'mount'` debt is paid only by a real mount (the FIRST observer, or a disabled first observer becoming enabled); a mount that came while the key was held is paid at the release; a second reader joining pays nothing; a mount payment a scroll's page cancelled is paid once that page settles (or at the release of a hold that dropped the page). `pay()` does not stamp its own start: query-core 5.100.9 dispatches `'fetch'` on a `cancelRefetch` restart (`query.js:261`, `null !== undefined`).
11. `lib/revalidate.ts`: the epoch guard also covers a `revalidate` that releases a hold from an older epoch. A re-read with no hold can still owe a new identity's reads after `resetReads` (cost: one extra read).
12. `lib/curationSync.ts` `familyTargets(own)`: owes `curationKeys.anyCollection` (the open collection page) as `LENS_REREAD.lists` (`'mount'`); the family's other reads (the board and its stages, the collections and tag indexes, the member maps, the note reads) are owed `'now'`.
13. `lib/curationSettle.ts`: the stale-side rule is built as Decision 7 states, judging by owed reads and by read time; `lib/revalidate.ts` writes each patch with `updatedAt` = its base's read time.
14. `main.tsx` does NOT call `applySyncQueryDefaults` yet: reconnect refetch on the curation roots stays on until W4 mounts the owner. **W4 MUST add that call in `main.tsx` beside the owner** (§ 6 W4).
15. Kept: `pages/Pipeline.tsx`'s `sharePipeline(...).then(next => qc.setQueryData(pipelineShareKeys.all, next))` (the POST answers both lists). `lib/mergedAdverts.ts` `refreshAfterSplit` re-reads by the tables the merge carriers move instead of the `['curation']` sweep. Note writes (`CurationBlock.tsx`) re-read every property-notes query plus note counts for the own identity.
16. `frontend/eslint.config.js`: `lib/revalidate.ts` needs no per-line exemption from the ban (it never calls `invalidateQueries`).
17. Tests: `lib/revalidate.test.ts` 37, `lib/curationSync.test.ts` 53 (the Q3(a) remote-lens cases live here, including the mounted-cohort case), `lib/curationSettle.test.ts` 20 (both § 9 item (i) variants, `'now'` and `'mount'`); full vitest 4294 (local at `c8d164ea8`; 4283 at CI on `570acac0f`) [V].
18. `test/reads.ts` (new): `watchReads`, how a test sees a re-read (a fetch since the watch, or an owed read). W1's tests use it in place of `invalidateQueries` spies; § 6 W1 lists it.
19. `roadmap/operator-workflow-track.md`: W1's track bullet rides in W0 (#2204); W1 (#2205) leaves `roadmap/` untouched.
20. Code-review repairs (`8621763a6`, `c8d164ea8`): a scroll's page fetch that cancels a payment (query-core's `fetchNextPage` defaults to `cancelRefetch`) re-dues it through a per-debt `paying` flag and `repayAfterPage`, `'mount'` debts included; a mount under a hold is paid at the release; `CachePatch` / `cachePatch` live in `lib/revalidate.ts` (no type cycle with `useOptimisticWrite`); `deletedTables(table)` over a private `ON_DELETE` map holds the foreign-key actions once (202 and 610 cascade, 660 set null) and the four delete sites call it; `@tanstack/react-query` floor raised to `^5.100.9` with the lock in sync, and the `revalidate.ts` header names the query-core internals it relies on (re-run `revalidate.test.ts` on any bump); `tests/test_carried_tables_read_contract.py` pins `CARRIED_TABLES` (`lib/mergedAdverts.ts`) to `toolkit.property_carriers` minus the rule-16 event table; `useOptimisticWrite` imports `revalidate` unaliased; the ESLint and `curationSync.ts` headers say exactly what the selector, the census and the map guarantee.

---

**Why this is not patchwork:**
- **One change source.** One definer function on 12 static deferred triggers. It bumps once per transaction, and only the database writes it.
- **One fact.** A counter per (account, family), compared for inequality.
- **One gate.** Who may follow an account is one SQL function, pinned to ruling 2 by a census.
- **One map** from table to reads, shared by a window's own writes and every other window.
- **One scheduler.** A re-read is owed until a read issued after the change lands. Holds are enforced by the cache itself and composed by a patch ledger. That one structure fixes D1, rollback composition and the TanStack swallow traps along with remote sync.
- **Failures are visible.** The API now commits before it answers, so a write that does not commit never says 200.
- **Every client trigger means only "read the clock now"**: interval, show, focus, reconnect, nudge, and a future doorbell. Speed-ups are accelerators on the same truth.
- **Rails.** Census tests, the tenancy registry, the read-contract test and the `curation_sync` alarm each fail CI or production health when someone adds a second source, map, gate or writer.

**W2 (#2200, PR #2209), as built:**
1. `lib/useServerDraft.ts`: generic `useServerDraft<T extends string>(server, normalize = asIs)` (TagEditPopover's colour is a union); exports `trimDraft`; returns `saved()` besides the five members § 3.5 lists. Focus does NOT freeze the field: an untouched field follows the server even while focused (the tag popover autofocuses its name field for its whole life); the user's first change starts a draft over the value they saw; following resumes at the blur (at once on an unfocused control) when the draft equals its base or the server has reached it; `edited()` is also `null` when the server already holds what the user typed; `reset()` clears the focus flag (a field unmounting while focused never blurs); `saved()` ends a saved draft at the next server value (a change made elsewhere between the save and its re-read is not overwritten).
2. Six editors save only the fields the user changed: `StageEditorRow` (label, code), `TagEditPopover` (name, colour), `CollectionDetail` and `PhoneCollectionDetail` (name, description), the `CurationBlock` note (body AND the viewing date, the same D3 defect, through a local day-time codec), `ArtifactLinkRow` (its re-read already moved to `revalidateTables` in W1; only the draft rule here).
3. `pages/Pipeline.tsx` `useDragHold`: `holdReads` over `actingKey(actAs, board | members | stages)` at `onDragStart`; `onDragEnd` calls `move.mutate(plan)` first (its hold registers synchronously, the app's MutationCache has no `onMutate`), then releases; cancel and unmount release. Board and members parked by the drag are paid once at the move's settle; stages at the drop.
4. `components/phone/PhonePipeline.tsx`: no `remembered` ref; the stage on screen is state seeded from `readLastStage`, switched to by id when it does not resolve (the neighbour after an archive, the entry stage when none), remembered by an effect; the tab-tap target is a stage id; `useReadHold([board, members], touching || scrolling)` with touchstart/touchend/touchcancel (a finger still down keeps the hold) and scroll events plus a 150 ms idle timer (`SCROLL_IDLE_MS`, React 18 has no onScrollEnd); the pager's own re-aim (`landPager`, after a reorder or resize) does not start the hold.
5. `pages/CollectionDetail.tsx`, `pages/phone/PhoneCollectionDetail.tsx`: a cleared description is sent as `''` until #2208 teaches `PATCH /collections` to tell an explicit `null` from an absent field (`coalesce` reads `null` as "unchanged").
6. Tests: `lib/useServerDraft.test.ts` 11; Pipeline (D3 rename, typed name kept and saved alone, a remote target during a drag lands after the drop, a drag started during a board re-read shows no repaint, unmount mid-drag); PhonePipeline (archive falls to the neighbour, a reorder during a tab-tap scroll lands on the tapped stage, a remote add during a touch waits, a scroll with no touch); D3 cases for the five other editors; full vitest 4332 after merging W1's final head.

**W3 (#2201), as built:**
1. `migrations/696_curation_clock.sql` (the number is provisional until the merge coordinator's claim): `curation_clock_audience()` declares `ROWS 4`. With the default estimate of 1000, live item 11 (the read on `curation_clock_pkey` with 2,000 accounts) fails [V]. § 3.2 shows it. The migration also adds a COMMENT ON for the view and both functions, and a longer header (who follows whom, apply order, rollback).
2. `tests/test_curation_sync_census.py` goes beyond § 6's first wording, and § 6 now says so. (a) replays drops and renames and checks each trigger's name. (c) adds the seed's families. (e) also catches `ENABLE REPLICA`, an action buried in a multi-action `ALTER TABLE`, code as well as migrations, and a later redefinition of the bump; an annotation counts only on a line of its own. (g) also covers the dedupe GUC. (h) pins 696's two writes. The review added the replay in (a) and the wider (e): `drop function … cascade` and `enable replica trigger` had passed every rail.
3. `tests/test_curation_clock_live.py`: a fifth tenant E; item 6 counts firings and adds the property delete and the `account_id` move (§ 9). A trigger missing from `collection_properties`, `property_tags` or `property_attachments` had passed the first cascade case [V].
4. `docs/architecture.md`: the DB half is a paragraph in rule 18 beside W0's (§ 6 lists it).
5. The 696 apply (MCP, before the merge, after W0 is live) and its § 9 catalog check are owed.

**W4 (#2202), as built** (two commits on W3's head `cb1dcd054`, the owner `d8a55f59a`):
1. `lib/curationSyncOwner.ts`: the owner as § 3.5 lists it, a closure per `startCurationSync(qc)` (no module state, no cache entry). Decision 12 and Decision 14b now say what it does where it refines them: `focus` takes a read that started under 1 s before it, `online` and the nudge need one that starts after them (the nudge keeps the newest receipt time over its 100 ms coalescing, so a nudge arriving inside that window is never answered by an older read); a show within 1 s of a read's start is that read (a bfcache restore fires `pageshow` and `visibilitychange`: one read); hiding aborts the read in flight; the interval runs from the last read's start but never sooner than 1 s after its answer; a transient failure while in permanent-error mode keeps that mode; a due trigger reads at once, the start's first read one task later (StrictMode's remount stops it unsent); the next read is armed before an answer is processed, so a processing error cannot stop the loop.
2. `lib/curationSyncOwner.ts` `cachedReads`: the start-time owe skips an initial load in flight (Decision 14b says why: owing it would read every mounted synced query twice per launch, the R22 cost); the charter's "normally there are none" did not hold, `RequireAuth` mounts the page in the commit that reads the session and the page's effects start its reads before `AuthProvider`'s.
3. The nudge's `MutationCache` subscription is the owner's (`qc.getMutationCache().subscribe`), not `lib/mutationCache.ts`'s: `stop()` unsubscribes it, so only a signed-in window posts.
4. `lib/queries.ts` `fetchCurationClock({ signal } = {})` over `CURATION_CLOCK_COLS` (`account_id,family,counter,mine,age_ms`); `tests/test_curation_clock_read_contract.py` pins it to the latest `curation_clock_public` (696), to `ClockRow`'s fields in `lib/curationSync.ts`, and to never naming `actAs`.
5. `lib/phoneShell.ts` `isPhoneFrame()` = narrow screen and no desktop-view override, read live (the same two inputs as `usePhoneShell`); the owner reads it at each answer.
6. `lib/auth.tsx` mounts `useCurationSync(loading ? null : userId)` after the identity effect; `lib/auth.test.tsx` pins stop → `resetReads` → `clear()` → start on a switch, stop alone on a sign-out, no start before the session is read nor on a token refresh; `lib/auth.push.test.tsx` mocks the owner away.
7. `main.tsx` calls `applySyncQueryDefaults(queryClient)` (W1's item 14 closed); its focus comment names the owner.
8. `scripts/checks/platform.py`: `reads` and `load` as § 3.9 now states, from one `pg_stat_statements` aggregate (`_CURATION_STATEMENTS_SQL`) plus the check's previous result; two `THRESHOLDS` (`CURATION_CLOCK_QUIET_H` 24, `CURATION_CLOCK_LOAD_PCT` 5.0); `board_bumps` no longer ends the check when the clock is absent. `tests/test_sql_schema_prepare.py` allowlists the read (the platform's extension, absent from the replayed schema, like `cron.`): it PREPAREs against PostgreSQL 17's `pg_stat_statements` [V], and unpreloaded it raises 55000, which the check reads as Not measured. `tests/test_curation_clock_live.py` is unchanged: in the replayed schema both cells read Not measured.
9. Docs: `docs/architecture.md` Frontend territory (the clock is the re-read trigger, staleTime the backstop), `docs/design/pipeline-sharing.md` (a revoke reaches the recipient through the clock; the empty-read guard stays), `docs/design/health-dashboard.md` and `pipeline-verification.md` (the two cells as built), `references/tenancy.md` (rails), the track bullet.
10. Tests: `lib/curationSyncOwner.test.ts` 28 (every § 9 case, plus: hiding aborts a read in flight, a page that starts hidden reads nothing, an owner's row maps to acting reads, the start-time owe, an errored debt re-paid at most 3 times, the nudge posted only on success, no `BroadcastChannel`, a signed-out window); `lib/auth.test.tsx` +2; `lib/phoneShell.test.tsx` +2; `tests/scripts/test_checks_curation_sync.py` 23 (17 new); `tests/test_curation_clock_read_contract.py` 3.
11. Owed after the merge: the Railway status, the § 9 read-only check, the operator's manual recipe (§ 9), then gate #2203's 7 days.
