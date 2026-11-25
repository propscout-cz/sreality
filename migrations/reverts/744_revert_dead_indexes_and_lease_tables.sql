-- 744_revert_dead_indexes_and_lease_tables.sql
--
-- REVERT for 744_dead_indexes_and_lease_tables.sql. SHIPPED UNAPPLIED. Lives in migrations/reverts/ because the CI
-- schema replay applies `ls migrations/*.sql | sort` (not recursive).
--
-- Each index is its live pg_get_indexdef of 2026-11-14 (the definitions of migrations 001, 099, 142, 185, 195, 198,
-- 381), rebuilt CONCURRENTLY: psql -f, statement autocommit, never inside a transaction block. A build reads its
-- whole table twice (properties 659 MB, listing_snapshots 2 GB) and takes minutes on a busy instance; a cancelled
-- build leaves an INVALID index: drop it CONCURRENTLY and re-run. Each table is its live shape (columns, comment, RLS on with no
-- policy, grants) holding the one row it held when 744 dropped it.

set lock_timeout = '5s';

create table if not exists public.property_maintenance_lease (
  id         smallint primary key default 1 check (id = 1),
  holder     text,
  expires_at timestamptz
);
alter table public.property_maintenance_lease enable row level security;
revoke all on public.property_maintenance_lease from anon, authenticated;
grant select on public.property_maintenance_lease to authenticated;
grant all on public.property_maintenance_lease to service_role;
comment on table public.property_maintenance_lease is
  'Single-row lease serializing property-maintenance writers (worker lane, GH cron, daily full sweep) across pooled connections — migration 279. Claimed by one atomic UPDATE ... RETURNING (scripts/recompute_property_stats.py); expiry self-heals a crashed holder. Replaces the #716 advisory lock, which is unsound over the transaction pooler.';
insert into public.property_maintenance_lease (id, holder, expires_at) values (1, null, null)
on conflict (id) do nothing;

create table if not exists public.broker_resolution_lock (
  id           int primary key default 1 check (id = 1),
  holder       text,
  mode         text,
  acquired_at  timestamptz,
  heartbeat_at timestamptz
);
alter table public.broker_resolution_lock enable row level security;
revoke all on public.broker_resolution_lock from anon, authenticated;
grant all on public.broker_resolution_lock to service_role;
insert into public.broker_resolution_lock (id, holder, mode, acquired_at, heartbeat_at)
values (1, null, 'incremental', '2026-11-03 19:27:38.568114+00', '2026-11-03 19:27:40.490535+00')
on conflict (id) do nothing;

set lock_timeout = 0;
set statement_timeout = '1900s';

create index concurrently if not exists listing_snapshots_content_hash_idx
  on public.listing_snapshots using btree (content_hash);
create index concurrently if not exists properties_near_pop_5km_idx
  on public.properties using btree (near_pop_5km) where (near_pop_5km is not null);
create index concurrently if not exists properties_near_pop_15km_idx
  on public.properties using btree (near_pop_15km) where (near_pop_15km is not null);
create index concurrently if not exists properties_near_jobs_5km_idx
  on public.properties using btree (near_jobs_5km) where (near_jobs_5km is not null);
create index concurrently if not exists properties_near_jobs_15km_idx
  on public.properties using btree (near_jobs_15km) where (near_jobs_15km is not null);
create index concurrently if not exists properties_near_youth_5km_idx
  on public.properties using btree (near_youth_5km) where (near_youth_5km is not null);
create index concurrently if not exists properties_near_youth_15km_idx
  on public.properties using btree (near_youth_15km) where (near_youth_15km is not null);
create index concurrently if not exists properties_near_overall_5km_idx
  on public.properties using btree (near_overall_5km) where (near_overall_5km is not null);
create index concurrently if not exists properties_near_overall_15km_idx
  on public.properties using btree (near_overall_15km) where (near_overall_15km is not null);
create index concurrently if not exists properties_mf_yield_keyset_idx
  on public.properties using btree (mf_gross_yield_pct, id) where (mf_gross_yield_pct is not null);
create index concurrently if not exists portal_raw_pages_unparsed_idx
  on public.portal_raw_pages using btree (source, fetched_at) where (parsed_at is null);
create index concurrently if not exists ruian_name_homonym
  on public.ruian_name_index using btree (name_norm) where (homonym_count > 1);
create index concurrently if not exists firm_identities_domain_idx
  on public.firm_identities using btree (email_domain) where (email_domain is not null);
create index concurrently if not exists broker_identities_email_idx
  on public.broker_identities using btree (email) where (email is not null);
create index concurrently if not exists listing_videos_sreality_id_idx
  on public.listing_videos using btree (sreality_id);

reset statement_timeout;
reset lock_timeout;
