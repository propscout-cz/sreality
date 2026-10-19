-- 635_property_location_ad.sql -- a property's LOCATION AD: its most precisely located ad, chosen once by
-- the rollup and stored as a pointer (ONE PIN sprint, W3; docs/design/one-pin/PROGRAM.md "The definition:
-- a property's location ad" and "W3"; GLOSSARY "Location ad").
--
-- WHY. A property is placed at its canonical ad's location today, so where the canonical ad is a street
-- ring and another of its ads sits on an address point (property 15100: canonical ad 40102 at `street`,
-- ad 13193960 at `address_point`), its map point, label and RUIAN codes come from the coarser one. The
-- location ad is the ad whose listing_location places the property; the canonical ad keeps the price,
-- photos, link and physical facts (R3: a pointer, not a change to the canonical order, so no price
-- voice, photo or link moves). INVISIBLE until W4: nothing reads the column or location_rank before W4
-- repoints the property surfaces. Rule 25: R3 is the operator's ruling for this addition; the pointer
-- stores no location value, so properties still carries none (migration 508).
--
-- THE RULED ORDER (R1, the operator's ruling of 2026-10-19; it differs from the active-first
-- recommendation), best first:
--   1. served tier: a map point (listing_location.geom) 2 > determined foreign (country_status =
--      'foreign') 1 > anything else 0: rule 25's served test (claims_common.SERVED_LOCATION_PREDICATE);
--   2. the finest granularity rung, location_granularity_rank.rank desc nulls last (migration 380),
--      whatever the ad's status: an exact address point stays exact after its ad closes;
--   3. the canonical order (588) as the only tie-break. It puts active ads first, so among equally
--      precise ads the active one wins; activity is NOT a key of its own.
-- Often the canonical ad, not always (about 13,784 multi-ad properties differ, measured 2026-10-19).
--
-- WHAT CHANGES. ADDITIVE: no row is written; the only drops are the function this file re-creates and a
-- cancelled index build's own leftover (4a).
--   1. properties.location_listing_ref_id bigint, nullable, no default: catalog-only, no table rewrite.
--      No foreign key: ads are never deleted (rule 3), and validating one would scan two hot tables.
--   2. property_canonical_listings(bigint) gains a third OUT column, location_rank (rank 1 = the location
--      ad). CREATE OR REPLACE cannot change a return type, so it is DROP + CREATE with 588's grants
--      restated (EXECUTE: service_role only) and its comment widened. 588's body is the INNER select
--      byte for byte (its prosrc between the first and the last newline); location_rank is computed in an
--      outer select over it, because a window cannot order by a sibling window. 588's alias `ll` does
--      not reach the outer select (the inner one returns only the id and the rank), so the outer select
--      joins listing_location once more and location_granularity_rank once. Still SQL, stable, invoker,
--      no SET, not strict, one SELECT: still inlined into its one caller, the rollup, whose CROSS JOIN
--      LATERAL selects named columns. location_granularity_rank has RLS on and no policy; the two roles
--      that may EXECUTE (postgres, service_role) both bypass it.
--   3. properties_location_listing_ref_active_idx, the twin of 505's properties_repr_listing_ref_active_idx:
--      comparables' spatial-first cohort probes the repr twin index-only (W0's R8 receipt) and would fall
--      back to a heap scan after W4 without it. CONCURRENTLY, outside the transaction (505's and 605's
--      idiom; see APPLY). Empty until the backfill: every row is NULL at the build.
--
-- WRITERS (W3's code, each in the same statement that sets the canonical ad): the singleton birth,
-- scraper/db.py NEW_SINGLETONS_SQL (also a split's new property), sets it to the one ad; the rollup
-- (scripts/recompute_property_stats.py) stores location_rank 1 as it stores canonical_rank 1; its no-ads
-- reset nulls both pointers. NEVER NULL WHILE THE PROPERTY HAS AN AD: from W4 a CHECK enforces
-- (repr_listing_ref_id is null) = (location_listing_ref_id is null). Neither fill is in this file: the
-- property sweep sets the live rows, then one status-agnostic id-batched fill (location = canonical
-- where still NULL) covers the merged_away rows and any property merged away before the sweep reached it.
--
-- GATES. $pre$: property_canonical_listings is 588's body (md5(prosrc) 645aa6268a66ea2dd653d8d310c15d61,
-- read live 2026-10-19 and hashed from migrations/588: identical) or this file's own (a re-run), with one
-- overload; no other function body names it (pg_depend does not track a body; a view would make the DROP
-- refuse); an existing location_listing_ref_id is this file's (bigint, nullable, no default). $post$, in
-- the transaction: this file's body (md5 1b1a9dacead80ddaff07e12939c99bf8), its signature and posture,
-- the ACL and the widened comment, the column, and a smoke call on property 15100 (five ads on
-- 2026-10-19: location_rank and canonical_rank each 1..5, location ad 13193960, canonical ad 40102; the
-- location ad's (tier, rung) at least the canonical ad's, whatever its ads are by the apply; skipped
-- where it has fewer than two ads, i.e. the CI replay). $post_index$: the index is valid and is the repr
-- twin's definition with the column and the name swapped.
--
-- APPLY through apply_migration.yml (--ref the W3 branch) BEFORE the code merges (the rollup reads
-- location_rank and writes the column), never through the MCP: CREATE INDEX CONCURRENTLY cannot run in a
-- transaction block, and every MCP path wraps its payload in one. The transaction is sub-second, but its
-- ALTER needs ACCESS EXCLUSIVE on properties, which the */15 list rebuild and (until 631) the :07/:37 map
-- rebuild read for about a minute each: the 5 s lock limit fails fast instead of stalling the table
-- behind them, and the workflow re-runs the file (every step is idempotent). The index build takes only
-- SHARE UPDATE EXCLUSIVE (no read or write waits on it), scans the 611 MB heap twice and waits for every
-- older transaction, a list rebuild included, so it runs with lock_timeout 0 under a 1900 s budget (605);
-- a cancelled build's INVALID leftover is moved aside and dropped CONCURRENTLY on the re-run.
-- ROLLBACK: migrations/reverts/635_revert_property_location_ad.sql (588's body verbatim; drops the column
-- and the index, losing only the pointer). A re-run reaches the same end state (it re-creates the
-- function and briefly re-takes the ACCESS EXCLUSIVE lock for the IF NOT EXISTS column; the workflow's
-- lock-timeout retry covers it).

set lock_timeout = '5s';
set statement_timeout = '120s';

begin;

-- ---------------------------------------------------------------------------
-- 0. Preconditions.
-- ---------------------------------------------------------------------------
do $pre$
declare
  v_callers text;
  v_column text;
begin
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'property_canonical_listings'), '')
     not in ('645aa6268a66ea2dd653d8d310c15d61', '1b1a9dacead80ddaff07e12939c99bf8') then
    raise exception '635 refused: property_canonical_listings is not 588''s body nor this file''s (or has two overloads)';
  end if;
  select string_agg(p.oid::regprocedure::text, ', ') into v_callers
    from pg_proc p
   where position('property_canonical_listings' in p.prosrc) > 0
     and p.proname <> 'property_canonical_listings';
  if v_callers is not null then
    raise exception '635 refused: % name property_canonical_listings in their body and would meet its third column', v_callers;
  end if;
  select format_type(a.atttypid, a.atttypmod) || ' ' || a.attnotnull || ' ' || a.atthasdef into v_column
    from pg_attribute a
   where a.attrelid = 'public.properties'::regclass and a.attname = 'location_listing_ref_id'
     and not a.attisdropped;
  if v_column is distinct from 'bigint false false' and v_column is not null then
    raise exception '635 refused: properties.location_listing_ref_id exists as "%", not this file''s nullable bigint', v_column;
  end if;
end
$pre$;

-- ---------------------------------------------------------------------------
-- 1. The two orders: 588's body as the inner select, location_rank over it.
-- ---------------------------------------------------------------------------
drop function if exists public.property_canonical_listings(bigint);

create or replace function public.property_canonical_listings(p_property_id bigint)
returns table (listing_id bigint, canonical_rank integer, location_rank integer)
language sql
stable
as $$
  select c.listing_id,
         c.canonical_rank,
         (row_number() over (
            order by case when ll.geom is not null then 2
                          when ll.country_status = 'foreign' then 1
                          else 0 end desc,
                     gr.rank desc nulls last,
                     c.canonical_rank))::integer
    from (
  select l.id,
         (row_number() over (
            order by l.is_active desc,
                     (ll.geom is not null) desc,
                     case when l.is_active then l.first_seen_at end,
                     case when not l.is_active then l.last_seen_at end desc,
                     public.source_trust_rank(l.source),
                     l.id))::integer
    from public.listings l
    left join public.listing_location ll on ll.listing_id = l.id
   where l.property_id = p_property_id
         ) c (listing_id, canonical_rank)
    left join public.listing_location ll on ll.listing_id = c.listing_id
    left join public.location_granularity_rank gr on gr.granularity = ll.granularity
$$;

revoke execute on function public.property_canonical_listings(bigint) from public, anon, authenticated;
grant execute on function public.property_canonical_listings(bigint) to service_role;

comment on function public.property_canonical_listings(bigint) is
  'MS5 (migration 588) and ONE PIN W3 (migration 635): a property''s ads in two orders. canonical_rank '
  'is THE canonical order, rank 1 the canonical ad: active first; a map point; the earliest first '
  'sighting among active ads, the latest last sighting among inactive ones; portal trust; id. '
  'location_rank, rank 1 the location ad: a map point, else determined foreign, else neither; then the '
  'finest location_granularity_rank rung, whatever the ad''s status; then canonical_rank. Its one '
  'caller is the property rollup.';

-- ---------------------------------------------------------------------------
-- 2. The pointer. Last in the transaction: its ACCESS EXCLUSIVE lock is held only until the commit.
-- ---------------------------------------------------------------------------
alter table public.properties add column if not exists location_listing_ref_id bigint;

comment on column public.properties.location_listing_ref_id is
  'ONE PIN (migration 635): the property''s location ad, rank 1 of property_canonical_listings(id).'
  'location_rank (a map point, then determined foreign, then the finest location_granularity_rank rung, '
  'then the canonical order), often the canonical ad but not always; set with repr_listing_ref_id by the '
  'singleton birth (scraper/db.py NEW_SINGLETONS_SQL) and the rollup, and NULL exactly when it is (a '
  'CHECK from W4); no foreign key, ads are never deleted (rule 3).';

-- ---------------------------------------------------------------------------
-- 3. Post-conditions of the transaction.
-- ---------------------------------------------------------------------------
do $post$
declare
  v_fn regprocedure := to_regprocedure('public.property_canonical_listings(bigint)');
  v_ads bigint;
  v_ok boolean;
  v_location_ad bigint;
  v_canonical_ad bigint;
begin
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'property_canonical_listings'), '')
     is distinct from '1b1a9dacead80ddaff07e12939c99bf8' then
    raise exception '635 did not land: property_canonical_listings is not this file''s body (or has two overloads)';
  end if;
  if pg_get_function_result(v_fn) is distinct from
       'TABLE(listing_id bigint, canonical_rank integer, location_rank integer)'
     or (select p.prosecdef or p.proisstrict or p.provolatile <> 's' or p.proconfig is not null
                or not p.proretset or lg.lanname <> 'sql'
           from pg_proc p join pg_language lg on lg.oid = p.prolang
          where p.oid = v_fn) then
    raise exception '635 did not land: property_canonical_listings is not an inlinable SQL stable invoker set function with the three columns';
  end if;
  if not has_function_privilege('service_role', v_fn, 'execute')
     or has_function_privilege('anon', v_fn, 'execute')
     or has_function_privilege('authenticated', v_fn, 'execute')
     or exists (select 1 from pg_proc p, aclexplode(p.proacl) a where p.oid = v_fn and a.grantee = 0) then
    raise exception '635 did not land: property_canonical_listings is executable beyond service_role (588''s ACL)';
  end if;
  if obj_description(v_fn, 'pg_proc') not like 'MS5 (migration 588) and ONE PIN W3 (migration 635):%'
     or obj_description(v_fn, 'pg_proc') is null then
    raise exception '635 did not land: property_canonical_listings lost its comment with the DROP (not restated)';
  end if;
  if (select format_type(a.atttypid, a.atttypmod) || ' ' || a.attnotnull || ' ' || a.atthasdef
        from pg_attribute a
       where a.attrelid = 'public.properties'::regclass and a.attname = 'location_listing_ref_id'
         and not a.attisdropped) is distinct from 'bigint false false'
     or col_description('public.properties'::regclass,
          (select a.attnum from pg_attribute a
            where a.attrelid = 'public.properties'::regclass and a.attname = 'location_listing_ref_id'))
        is null then
    raise exception '635 did not land: properties.location_listing_ref_id is not a commented nullable bigint without a default';
  end if;
  select count(*) into v_ads from public.listings where property_id = 15100;
  if v_ads < 2 then
    raise notice '635: smoke skipped, property 15100 has % ad(s) here (the CI replay)', v_ads;
  else
    select coalesce(count(*) = v_ads
             and count(distinct location_rank) = v_ads and min(location_rank) = 1
             and max(location_rank) = v_ads
             and count(distinct canonical_rank) = v_ads and min(canonical_rank) = 1
             and max(canonical_rank) = v_ads, false),
           min(listing_id) filter (where location_rank = 1),
           min(listing_id) filter (where canonical_rank = 1)
      into v_ok, v_location_ad, v_canonical_ad
      from public.property_canonical_listings(15100);
    if not v_ok then
      raise exception '635 did not land: property_canonical_listings(15100) does not rank its % ads 1..n in both orders', v_ads;
    end if;
    with t as (
      select x.id,
             case when ll.geom is not null then 2 when ll.country_status = 'foreign' then 1 else 0 end as tier,
             coalesce(gr.rank, -1) as rung
        from (values (v_location_ad), (v_canonical_ad)) x (id)
        left join public.listing_location ll on ll.listing_id = x.id
        left join public.location_granularity_rank gr on gr.granularity = ll.granularity)
    select coalesce(row(a.tier, a.rung) >= row(b.tier, b.rung), false) into v_ok
      from t a, t b
     where a.id = v_location_ad and b.id = v_canonical_ad;
    if not coalesce(v_ok, false) then
      raise exception '635 did not land: the location ad of 15100 is less precise than its canonical ad';
    end if;
    raise notice '635: property 15100, % ads: location ad %, canonical ad %', v_ads, v_location_ad, v_canonical_ad;
  end if;
end
$post$;

notify pgrst, 'reload schema';

commit;

-- ---------------------------------------------------------------------------
-- 4a. An INVALID properties_location_listing_ref_active_idx can only be a cancelled build of an earlier
--     attempt, and IF NOT EXISTS would keep it. A DO block is a transaction, where DROP INDEX CONCURRENTLY
--     cannot run, so the block only RENAMES it aside (SHARE UPDATE EXCLUSIVE since PostgreSQL 12: no read
--     or write waits on it) and the drop below, at top level, is CONCURRENTLY.
-- ---------------------------------------------------------------------------
do $invalid$
begin
  if exists (select 1 from pg_index i
              where i.indexrelid = to_regclass('public.properties_location_listing_ref_active_idx')
                and not i.indisvalid) then
    alter index public.properties_location_listing_ref_active_idx
      rename to properties_location_listing_ref_active_idx_invalid;
    raise notice '635: an INVALID properties_location_listing_ref_active_idx from a cancelled build moved aside';
  end if;
end
$invalid$;

-- ---------------------------------------------------------------------------
-- 4. The repr twin (see the header for the budget; the drop waits like the build).
-- ---------------------------------------------------------------------------
set lock_timeout = 0;
set statement_timeout = '1900s';

drop index concurrently if exists public.properties_location_listing_ref_active_idx_invalid;

create index concurrently if not exists properties_location_listing_ref_active_idx
  on public.properties (location_listing_ref_id)
  where status = 'active' and location_listing_ref_id is not null;

set lock_timeout = '5s';
set statement_timeout = '120s';

-- ---------------------------------------------------------------------------
-- 5. The index is valid and is properties_repr_listing_ref_active_idx with the column and name swapped.
-- ---------------------------------------------------------------------------
do $post_index$
begin
  if not exists (select 1 from pg_index i
                  where i.indexrelid = to_regclass('public.properties_location_listing_ref_active_idx')
                    and i.indisvalid and i.indisready)
     or pg_get_indexdef(to_regclass('public.properties_location_listing_ref_active_idx'))
        is distinct from replace(replace(
          pg_get_indexdef(to_regclass('public.properties_repr_listing_ref_active_idx')),
          'repr_listing_ref_id', 'location_listing_ref_id'),
          'properties_repr_listing_ref_active_idx', 'properties_location_listing_ref_active_idx') then
    raise exception '635 did not land: properties_location_listing_ref_active_idx is missing, INVALID or not the twin of properties_repr_listing_ref_active_idx; re-run the file';
  end if;
end
$post_index$;

reset statement_timeout;
reset lock_timeout;
