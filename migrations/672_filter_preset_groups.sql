-- 672_filter_preset_groups.sql -- Browse preset GROUPS: named boxes an account sorts its filter presets into.
--
-- WHY. Operator 2026-10-25: "group my presets, so a group gets a boundary box where I can move them around, into
-- the boxes or outside of them". A group is a saved set with no blob (docs/design/saved-dashboards.md § Groups):
-- a name, a colour, a position and an owner, the preset row minus filter_spec, served by the same primitive
-- (api/saved_sets.py) at /filter-preset-groups.
--
-- WHAT CHANGES (additive).
--   1. filter_preset_groups: id uuid, account_id NOT NULL, name 1..200, color under 201's palette CHECK,
--      position, created_at, updated_at; unique (id, account_id) for the presets' key. RLS: tenant_rw on
--      current_account_ids(), as filter_presets (a preset is no curation table, so never acting); grants as 290.
--   2. filter_presets.group_id, keyed (group_id, account_id) -> filter_preset_groups (id, account_id)
--      ON DELETE SET NULL (group_id): a preset names only its own account's group, and a deleted group leaves its
--      presets loose, never deletes one. NULL = loose, which every existing preset stays.
--
-- ORDER. A group's position orders the boxes. A preset's position stays ONE order over all the account's presets;
-- the loose row and each box show theirs in it. PUT /filter-presets/layout writes both in one transaction.
--
-- The API reads group_id, so APPLY BEFORE THE MERGE. psql -f, statement autocommit, every statement idempotent.
-- Verify:
--   select (select count(*) from filter_preset_groups) as groups,
--          (select count(*) from filter_presets where group_id is not null) as grouped,
--          (select count(*) from pg_policies where tablename = 'filter_preset_groups') as policies;

set lock_timeout = '5s';

-- 1. The groups.
create table if not exists filter_preset_groups (
  id          uuid        primary key default gen_random_uuid(),
  account_id  uuid        not null references accounts(id) on delete cascade,
  name        text        not null check (length(name) between 1 and 200),
  color       text        null check (color is null or color in (
                'copper', 'sage', 'brick', 'ochre', 'slate', 'plum', 'teal', 'sand')),
  position    integer     not null default 0,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  constraint filter_preset_groups_id_account_id_key unique (id, account_id)
);
create index if not exists filter_preset_groups_account_idx on filter_preset_groups (account_id);

alter table filter_preset_groups enable row level security;
drop policy if exists filter_preset_groups_tenant_rw on filter_preset_groups;
create policy filter_preset_groups_tenant_rw on filter_preset_groups
  for all to authenticated
  using (account_id in (select current_account_ids()))
  with check (account_id in (select current_account_ids()));
revoke all on filter_preset_groups from anon, authenticated;
grant select, insert, update, delete on filter_preset_groups to authenticated;

comment on table filter_preset_groups is
  'Named boxes an account sorts its Browse filter presets into (migration 672). A saved set with no blob, '
  'written by api/saved_sets.py at /filter-preset-groups; deleting one leaves its presets loose.';
comment on column filter_preset_groups.position is
  'Box order (ascending, 0 = first), rewritten whole by PUT /filter-presets/layout; a new group appends.';

-- 2. A preset's group.
alter table filter_presets add column if not exists group_id uuid;
alter table filter_presets
  drop constraint if exists filter_presets_group_fkey,
  add constraint filter_presets_group_fkey foreign key (group_id, account_id)
    references filter_preset_groups (id, account_id) on delete set null (group_id);
create index if not exists filter_presets_group_idx on filter_presets (group_id) where group_id is not null;

comment on column filter_presets.group_id is
  'The box this preset sits in (filter_preset_groups, same account), NULL = loose. A deleted group sets it NULL. '
  'Order inside a box is the preset''s position (migration 672).';

do $post$
begin
  if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'filter_preset_groups'
                    and policyname = 'filter_preset_groups_tenant_rw')
     or not (select relrowsecurity from pg_class where oid = 'public.filter_preset_groups'::regclass)
     or not exists (select 1 from pg_constraint where conname = 'filter_presets_group_fkey'
                       and conrelid = 'public.filter_presets'::regclass and confdeltype = 'n')
     or has_table_privilege('anon', 'public.filter_preset_groups', 'select') then
    raise exception '672: the policy, RLS, the SET NULL key or the anon revoke is missing';
  end if;
end
$post$;
