-- Bootstrap for the migration smoke-test (.github/workflows/migrations.yml, scripts/local_pg.sh).
-- NOT part of the tracked schema. It provisions what Supabase provides in
-- production but a vanilla postgis/postgis container does not, so the full
-- migrations/ chain applies cleanly:
--   * the Supabase roles the migrations GRANT to / write RLS policies for
--   * the pg_trgm extension that migrations 067 / 074 rely on for trigram
--     indexes but never `create`. (postgis is already enabled by the container
--     image; we create both idempotently here for good measure.)
-- Roles are cluster-wide and local_pg.sh builds one template per migration set in one
-- cluster, so each is created only when absent (the guard migration 293 uses).
create extension if not exists postgis;
create extension if not exists pg_trgm;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin bypassrls; end if;
  -- PostgREST's connection role: migration 394 pins pgrst.db_max_rows on it.
  if not exists (select 1 from pg_roles where rolname = 'authenticator') then create role authenticator nologin; end if;
end $$;

-- Supabase's public-schema DEFAULT ACL, which a vanilla container has NOT got.
-- In production `pg_default_acl` carries a postgres/public/TABLES entry, so every
-- relation a migration creates is born with a browser-role SELECT grant that no
-- migration ever writes — that invisible grant is how firms_public (migration 395)
-- and listings_public/properties_public (migration 398) came to be readable by any
-- logged-in session. Without it here the replay starts from an all-empty ACL, every
-- `has_table_privilege('authenticated', ...)` assertion is vacuously satisfied, and
-- the whole default-ACL reachability class is unassertable in CI.
--
-- SELECT only, granted BEFORE the chain runs, so 299 PART A/B's revokes act on a
-- real baseline: PART A trims the default to `authenticated=r` (production's exact
-- live value) and PART B clears anon off the pre-299 objects. Write privileges are
-- deliberately not seeded — production's writes were revoked long ago and seeding
-- them would assert a state that no longer exists.
alter default privileges for role postgres in schema public
  grant select on tables to anon, authenticated;

-- Supabase provides the `auth` schema + `auth.users` in production. A vanilla
-- container does not, so migrations that FK-reference or trigger on auth.users
-- (multi-tenant accounts, migration 286+) fail the smoke-test without this stub.
-- Minimal shape sufficient for FK targets + AFTER INSERT triggers, plus the
-- confirmation stamp the share route's email lookup filters on (migration 610).
create schema if not exists auth;
create table if not exists auth.users (
  id                 uuid primary key default gen_random_uuid(),
  email              text,
  email_confirmed_at timestamptz
);

-- Supabase provides the `storage` schema in production: the bucket row a migration inserts (714) and the objects
-- agent_files.sweep reads. Only the columns those two name; RLS on and no grant, as a project's default leaves them to
-- a browser role with no policy.
create schema if not exists storage;
create table if not exists storage.buckets (
  id                 text primary key,
  name               text not null,
  public             boolean default false,
  file_size_limit    bigint,
  allowed_mime_types text[]
);
create table if not exists storage.objects (
  id         uuid primary key default gen_random_uuid(),
  bucket_id  text references storage.buckets (id),
  name       text,
  created_at timestamptz default now()
);
alter table storage.buckets enable row level security;
alter table storage.objects enable row level security;
