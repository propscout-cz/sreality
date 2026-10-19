-- 635_revert_property_location_ad.sql
--
-- REVERT for 635_property_location_ad.sql. SHIPPED UNAPPLIED.
--
-- Lives in migrations/reverts/ for the same two verified reasons as every revert in this
-- build: the CI schema replay applies `ls migrations/*.sql | sort` (NOT recursive), so a
-- revert beside its forward migration would be applied right after it and silently undo the
-- change in every replayed environment; and tests/test_migration_numbers.py globs the same
-- way and forbids a duplicate number above 304.
--
-- It restores, verbatim, the body 635 replaced, with 588's grants and comment:
--   property_canonical_listings(bigint)   588's body, returns (listing_id, canonical_rank)
--                                          md5(prosrc) 645aa6268a66ea2dd653d8d310c15d61
-- 635 changed its OUT columns, so this is DROP + CREATE again. It also drops what 635 added: the
-- index properties_location_listing_ref_active_idx and the column properties.location_listing_ref_id.
--
-- WHAT APPLYING THIS COSTS, stated plainly so it is never done casually:
--   * DATA LOSS OF THE POINTER ONLY: every property's stored location ad is dropped with its column.
--     It is a derived choice (the rollup recomputes it from the ads); no ad, listing_location row,
--     canonical ad or curation row is touched. DROP COLUMN is destructive by the database skill's
--     definition: apply only with the operator's OK. The pointer needs no backup (re-applying 635 and
--     re-running the sweep and the fill rebuild it).
--   * The location ad is gone, so every surface that reads it (from W4) has nothing to read.
-- ORDER: revert W3's code first and let it deploy (the rollup reads location_rank and writes the
-- column; the singleton birth inserts it): until then every rollup batch and every birth would fail
-- against this file. The old code runs unchanged on 635's function (it selects named columns), so code
-- first, then this file, is the safe order. It refuses once anything else is tied to the column or the
-- function (W4's CHECK or views, a function body naming either); revert W4 first.
-- APPLY: one transaction (the Supabase MCP or psql), not in the minute a read-model rebuild reads
-- properties (:00-:02, :15-:17, :30-:32, :45-:47, and :07/:37 until 631): the DROP COLUMN and the plain
-- DROP INDEX take ACCESS EXCLUSIVE on properties under the 5 s lock limit; re-run on a timeout. A
-- re-run after it landed is a no-op: the precondition accepts 588's body.

begin;

set local lock_timeout = '5s';

do $pre$
declare
  v_attnum smallint := (select a.attnum from pg_attribute a
                         where a.attrelid = 'public.properties'::regclass
                           and a.attname = 'location_listing_ref_id' and not a.attisdropped);
  v_tied text;
begin
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'property_canonical_listings'), '')
     not in ('1b1a9dacead80ddaff07e12939c99bf8', '645aa6268a66ea2dd653d8d310c15d61') then
    raise exception '635 revert refused: property_canonical_listings is not 635''s body (a later migration replaced it, or two overloads)';
  end if;
  select string_agg(pg_describe_object(d.classid, d.objid, d.objsubid), ', ') into v_tied
    from pg_depend d
   where d.refclassid = 'pg_class'::regclass
     and d.refobjid = 'public.properties'::regclass
     and d.refobjsubid = v_attnum
     and not (d.classid = 'pg_class'::regclass
              and d.objid = coalesce(to_regclass('public.properties_location_listing_ref_active_idx')::oid, 0));
  if v_tied is not null then
    raise exception '635 revert refused: % depend on properties.location_listing_ref_id (revert W4 first)', v_tied;
  end if;
  select string_agg(p.oid::regprocedure::text, ', ') into v_tied
    from pg_proc p
   where (position('location_listing_ref_id' in p.prosrc) > 0 or position('location_rank' in p.prosrc) > 0)
     and p.proname <> 'property_canonical_listings';
  if v_tied is not null then
    raise exception '635 revert refused: % name location_listing_ref_id or location_rank in their body (revert W4 first)', v_tied;
  end if;
end
$pre$;

drop index if exists public.properties_location_listing_ref_active_idx;
alter table public.properties drop column if exists location_listing_ref_id;

drop function if exists public.property_canonical_listings(bigint);

create or replace function public.property_canonical_listings(p_property_id bigint)
returns table (listing_id bigint, canonical_rank integer)
language sql
stable
as $$
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
$$;

revoke execute on function public.property_canonical_listings(bigint) from public, anon, authenticated;
grant execute on function public.property_canonical_listings(bigint) to service_role;

comment on function public.property_canonical_listings(bigint) is
  'MS5 (migration 588): THE canonical order of a property''s ads; rank 1 is the canonical ad. '
  'Active first; a map point; the earliest first sighting among active ads, the latest last '
  'sighting among inactive ones; portal trust; id. Its one caller is the property rollup.';

do $post$
declare
  v_fn regprocedure := to_regprocedure('public.property_canonical_listings(bigint)');
begin
  if coalesce((select string_agg(md5(p.prosrc), ',') from pg_proc p
       where p.pronamespace = 'public'::regnamespace and p.proname = 'property_canonical_listings'), '')
     is distinct from '645aa6268a66ea2dd653d8d310c15d61'
     or pg_get_function_result(v_fn) is distinct from 'TABLE(listing_id bigint, canonical_rank integer)' then
    raise exception '635 revert did not land: property_canonical_listings is not 588''s body and signature';
  end if;
  if not has_function_privilege('service_role', v_fn, 'execute')
     or has_function_privilege('anon', v_fn, 'execute')
     or has_function_privilege('authenticated', v_fn, 'execute')
     or exists (select 1 from pg_proc p, aclexplode(p.proacl) a where p.oid = v_fn and a.grantee = 0) then
    raise exception '635 revert did not land: property_canonical_listings is executable beyond service_role (588''s ACL)';
  end if;
  if exists (select 1 from pg_attribute a
              where a.attrelid = 'public.properties'::regclass
                and a.attname = 'location_listing_ref_id' and not a.attisdropped)
     or to_regclass('public.properties_location_listing_ref_active_idx') is not null then
    raise exception '635 revert did not land: properties.location_listing_ref_id or its index survived';
  end if;
end
$post$;

notify pgrst, 'reload schema';

commit;
