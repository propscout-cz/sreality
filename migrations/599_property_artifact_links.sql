-- 599_property_artifact_links.sql
-- "Artifact link": ONE operator-supplied URL per property, a pointer to a page the
-- operator keeps about it (an analysis, a write-up). A single-valued sibling of
-- property_notes (migration 202): same rule #18 curation, but a field, not a journal.
--
-- Shape follows migration 536 (property_dismissals) for tenancy -- RLS on, one `for all`
-- policy on current_account_ids(), `authenticated` DML so the API's tenant_conn writes --
-- with a composite primary key (account_id, property_id) instead of a surrogate id: the
-- key IS the one-link-per-account-and-property rule, and the API's PUT is an upsert on it.
-- UPDATE is column-scoped to what an edit changes. No `_public` view: the SPA reads and
-- writes it through the API, like notes.
--
-- Carried across a merge by toolkit/property_carriers.py (a SET table keyed on account_id):
-- the retired property's link moves to the survivor; where the account already holds one
-- on the survivor, the survivor's stands and the retired one is dropped.
--
-- The scheme CHECK is the database's half of "clickable": the SPA only renders an anchor
-- for http(s), and this keeps a `javascript:` URL out of the table whatever writes it.
--
-- Additive. APPLY BEFORE MERGING: the carrier list names this table, so every merge step
-- runs a statement against it and code that ships ahead of the table fails EVERY merge.
-- Idempotent.
-- Verify:
--   select relrowsecurity from pg_class where oid = 'public.property_artifact_links'::regclass;  -- t
--   select has_table_privilege('anon', 'public.property_artifact_links', 'select');               -- f
--   select has_table_privilege('authenticated', 'public.property_artifact_links', 'insert');      -- t

set lock_timeout = '5s';

create table if not exists public.property_artifact_links (
  account_id  uuid        not null references public.accounts(id) on delete cascade,
  property_id bigint      not null references public.properties(id) on delete cascade,
  url         text        not null
              constraint property_artifact_links_url_ck
              check (url ~* '^https?://[^[:space:]]+$' and char_length(url) <= 2000),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  primary key (account_id, property_id)
);

-- The merge carrier moves rows by property_id alone, and the property FK cascades on it.
create index if not exists property_artifact_links_by_property
  on public.property_artifact_links (property_id);

alter table public.property_artifact_links enable row level security;

revoke all on public.property_artifact_links from anon, authenticated;
grant select, insert, delete on public.property_artifact_links to authenticated;
grant update (url, updated_at) on public.property_artifact_links to authenticated;

drop policy if exists property_artifact_links_tenant_rw on public.property_artifact_links;
create policy property_artifact_links_tenant_rw on public.property_artifact_links
  for all to authenticated
  using (account_id in (select current_account_ids()))
  with check (account_id in (select current_account_ids()));

comment on table public.property_artifact_links is
  'Rule #18 curation: one operator URL per (account, property), an "Artifact link" shown beside '
  'the notes. Written only through PUT/DELETE /properties/{id}/artifact-link; a merge carries it '
  '(toolkit/property_carriers.py), the survivor''s link winning a collision.';
