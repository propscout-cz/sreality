-- 707_location_w3_drop_fingerprint.sql -- location W3 PR-2: the fingerprint dedup is gone.
--
-- WHY (ticket #2223; docs/design/location-quality/w3-one-claim-per-fact.md § 4 Step 6): since 699 the
-- store holds one claim per (listing, claim type, author), keyed by `location_claims_key`, and the one
-- write (`claims_store.write_claims`) neither fills nor reads `claim_fingerprint`. Its index cost
-- 1,098 MB. `location_claims_ephemeral` covers a class nothing reads (0 rows). Nothing else calls the
-- two functions: no view reads the column and no function names them (checked live 2026-11-01).
--
-- DESTRUCTIVE: covered by the purge's count-verified backup of the whole table, column included
-- (R2 backups/w3-claims/2026-11-01/w3_claims_41304606866_1.csv.gz, 15,313,392 rows). Apply through
-- apply_migration.yml, never the MCP: DROP INDEX CONCURRENTLY cannot run in a transaction block. A code
-- revert past this file is not possible without restoring the column from that backup.

set lock_timeout = '5s';

drop index concurrently if exists location_claims_fingerprint;
drop index concurrently if exists location_claims_ephemeral;

alter table location_claims drop column if exists claim_fingerprint;

drop function if exists location_claim_fingerprint(bigint, text, text, text, text, text, text, text,
  text, bigint, text, text, numeric, geometry, geometry, jsonb, integer, text, text, text, text,
  numeric, text);
drop function if exists location_value_norm(text);

do $post$
begin
  if to_regclass('public.location_claims_fingerprint') is not null
     or to_regclass('public.location_claims_ephemeral') is not null then
    raise exception '707 did not land: an index is still there';
  end if;
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'location_claims'
                and column_name = 'claim_fingerprint') then
    raise exception '707 did not land: location_claims.claim_fingerprint is still there';
  end if;
  if exists (select 1 from pg_proc where proname in ('location_claim_fingerprint', 'location_value_norm')) then
    raise exception '707 did not land: a fingerprint function is still there';
  end if;
end
$post$;

reset lock_timeout;
