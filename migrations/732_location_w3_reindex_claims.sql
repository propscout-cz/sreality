-- 732_location_w3_reindex_claims.sql -- location W3 Step 7: rebuild the four location_claims indexes.
--
-- WHY (ticket #2245 line 8; docs/design/location-quality/w3-one-claim-per-fact.md § 4 Step 7; ruling
-- PROGRAM.md 2026-11-01: "W3's REINDEX is a small numbered migration after the body re-walk"): the store
-- went from 15.3 M to 6.0 M rows in 699, but these four indexes kept their old pages (pkey 360 MB,
-- by_method 352 MB, contract_entry 109 MB, geom 108 MB on 2026-11-05). The 571 k body re-walk has ended
-- (both intake lanes' bodies cursors sit at the newest body under the current contract set), so a
-- rebuild now is not re-bloated by it. location_claims_key was built after the purge and stays.
--
-- Not destructive: no row and no definition changes. Apply through apply_migration.yml (session pooler),
-- never the MCP: REINDEX CONCURRENTLY cannot run in a transaction block. A failed attempt leaves an
-- INVALID <index>_ccnew (cancelled while building) or <index>_ccold (cancelled after the swap); re-run
-- the file: it drops both names first, so a retry never stacks a _ccnew1.
--
-- The rebuilds run with no lock timeout (653's idiom): CONCURRENTLY only waits for older transactions
-- to end and never queues a lock that blocks the intake's writes or the resolver's reads.

set lock_timeout = '5s';

drop index concurrently if exists location_claims_pkey_ccnew;
drop index concurrently if exists location_claims_pkey_ccold;
drop index concurrently if exists location_claims_by_method_ccnew;
drop index concurrently if exists location_claims_by_method_ccold;
drop index concurrently if exists location_claims_contract_entry_ccnew;
drop index concurrently if exists location_claims_contract_entry_ccold;
drop index concurrently if exists location_claims_geom_ccnew;
drop index concurrently if exists location_claims_geom_ccold;

set lock_timeout = 0;
set statement_timeout = '1900s';

reindex index concurrently location_claims_pkey;
reindex index concurrently location_claims_by_method;
reindex index concurrently location_claims_contract_entry;
reindex index concurrently location_claims_geom;

set lock_timeout = '5s';
set statement_timeout = '120s';

do $post$
begin
  if exists (select 1 from pg_index i join pg_class c on c.oid = i.indexrelid
              where i.indrelid = 'public.location_claims'::regclass
                and (not i.indisvalid or c.relname like '%\_cc%')) then
    raise exception '732 did not land: location_claims holds an invalid or leftover index; re-run the file';
  end if;
end
$post$;

reset lock_timeout;
reset statement_timeout;
