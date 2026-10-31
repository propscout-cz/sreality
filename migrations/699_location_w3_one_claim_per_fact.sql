-- 699_location_w3_one_claim_per_fact.sql -- location W3: one claim per (listing, claim type, author).
--
-- WHY (ticket #2223; docs/design/location-quality/w3-one-claim-per-fact.md; PROGRAM.md § W3, rulings Q4, Q5, Q13):
--   location_claims kept every claim every contract version ever stated. 59.6 % of its 14.82 M rows were a second or
--   later copy of one (listing, claim type, author) fact, 99.68 % of them the same value restated under a newer version,
--   and the resolver needed a per-type version window and seven tie rules to pick one answer. From W3 the store holds
--   ONE claim per key, the value that author stated most recently: one keyed upsert writes it
--   (location_data.claims_store.write_claims, shared by the operator) and one DISTINCT ON reads it, the operator first
--   (location_data.resolver.resolve_db._claims_sql). The author IS the licence class (portal | operator). This file
--   gives the key its index. The ≈ 8.8 M surplus rows go BEFORE it, in the batched purge.
--
-- WHAT.
--   1. claim_fingerprint DROP NOT NULL: the new writer does not compute it. Migration 700 (W3 PR-2) drops the column,
--      its 1.1 GB index and the two functions that computed it.
--   2. THE GUARD (no lock taken): refuse when more than 50,000 keys still hold several rows. That is a table the purge
--      has not run on, and its dedup does not belong under the lock below.
--   3. One transaction under SHARE ROW EXCLUSIVE (writers wait, readers run): delete the rows the OLD writer added
--      after the purge passed their listing (keeper: the newest observation, then the highest id), then build
--      location_claims_key. One transaction, so no duplicate lands between the dedup and the build. About 1-2 min
--      on ≈ 6 M rows; an intake batch waits on its own 20 s lock_timeout and retries from its cursor.
--   4. DROP INDEX CONCURRENTLY location_claims_listing (664 MB): the key leads with listing_id and serves every reader.
--
-- DESTRUCTIVE (step 3 deletes rows): pre-approved by ruling Q13. The R2 backup of the whole table is the purge
-- workflow's first step (location_claims_retire.yml, its last run), taken before any row goes.
--
-- APPLY ORDER (design § 5): purge (location_claims_retire.yml --ref the W3 branch, confirm=PURGE) -> THIS FILE ->
-- merge -> Railway deploy, back to back. Through apply_migration.yml (psql -f, statement autocommit, as 498 and 653),
-- never the MCP: DROP INDEX CONCURRENTLY cannot run inside a transaction block. Between this apply and the deploy the
-- old writer's ON CONFLICT (claim_fingerprint) DO NOTHING raises unique_violation on a changed value: its batch rolls
-- back and retries next tick, nothing is lost.
-- RE-RUN: every statement is idempotent; apply_migration.yml retries the whole file on a lock timeout.
-- ROLLBACK: forward only (rule 1); the purged rows are in R2. The old code reads a one-row-per-key table
-- correctly, but its writer CANNOT run beside the key: `ON CONFLICT (claim_fingerprint) DO NOTHING` raises
-- unique_violation on location_claims_key for every changed value. A code revert after this file therefore
-- also needs, in this order (the old read must keep an index on listing_id):
--   create index concurrently if not exists location_claims_listing
--     on location_claims (listing_id, claim_type, first_observed_at desc);
--   drop index concurrently if exists location_claims_key;
-- claim_fingerprint stays nullable: the old writer fills it, and nothing needs it NOT NULL.
-- VERIFY (the $post$ block raises unless all of it holds):
--   select indisvalid, indisunique from pg_index where indexrelid = 'location_claims_key'::regclass;  -- t, t
--   select to_regclass('location_claims_listing');                                                       -- null
--   select is_nullable from information_schema.columns
--    where table_name = 'location_claims' and column_name = 'claim_fingerprint';                          -- YES
-- RECEIPT: select count(*) from location_claims;  -- ≈ 5.98 M on 2026-10-31 (+ ≈ 50 k a day)

set lock_timeout = '5s';
set statement_timeout = '15min';

alter table location_claims alter column claim_fingerprint drop not null;

do $guard$
declare
  v_keys bigint;
begin
  if to_regclass('public.location_claims_key') is not null then
    return;
  end if;
  select count(*) into v_keys
    from (select 1 from location_claims
           group by listing_id, claim_type, licence_class having count(*) > 1) d;
  if v_keys > 50000 then
    raise exception '699: % keys still hold several claims; run the purge first '
      '(location_claims_retire.yml, dry_run=false, confirm=PURGE)', v_keys;
  end if;
end
$guard$;

begin;
set local lock_timeout = '10s';
lock table location_claims in share row exclusive mode;
delete from location_claims c
 using (select id
          from (select id, row_number() over (partition by listing_id, claim_type, licence_class
                                              order by first_observed_at desc, id desc) as rn
                  from location_claims
                 where (listing_id, claim_type, licence_class) in (
                       select listing_id, claim_type, licence_class from location_claims
                        group by 1, 2, 3 having count(*) > 1)) r
         where rn > 1) d
 where c.id = d.id;
create unique index if not exists location_claims_key
  on location_claims (listing_id, claim_type, licence_class);
commit;

comment on index location_claims_key is
  'W3 (migration 699): ONE claim per listing, claim type and author (licence class). The ON CONFLICT target of '
  'location_data.claims_store.write_claims, the one write; the resolver read DISTINCT ON its leading columns.';

drop index concurrently if exists location_claims_listing;

do $post$
begin
  if not exists (select 1 from pg_index i
                  where i.indexrelid = to_regclass('public.location_claims_key')
                    and i.indisvalid and i.indisready and i.indisunique and i.indpred is null
                    and pg_get_indexdef(i.indexrelid) like '%(listing_id, claim_type, licence_class)') then
    raise exception '699 did not land: location_claims_key is missing, INVALID or not the key. Recover: '
      'DROP INDEX CONCURRENTLY location_claims_key; re-run the file';
  end if;
  if to_regclass('public.location_claims_listing') is not null then
    raise exception '699 did not land: location_claims_listing is still there';
  end if;
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'location_claims'
                and column_name = 'claim_fingerprint' and is_nullable = 'NO') then
    raise exception '699 did not land: location_claims.claim_fingerprint is still NOT NULL';
  end if;
end
$post$;

reset statement_timeout;
reset lock_timeout;
