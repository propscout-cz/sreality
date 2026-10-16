-- 605_one_signup_path_default_board.sql
-- One signup path, one default board.
--
-- 2026-10-16: a second account's Pipeline page looked blank and its stage
-- manager "showed the operator's stages". No leak — migration 294's trigger
-- seeds every account with a copy of the operator's JULY board (7 stages, no
-- `code`, so its three "9." stages badge 5/6/7 by the ordinal fallback) and the
-- SPA hid an empty board's columns. This migration makes the default the
-- operator's board as of today (codes from the label numbers), and leaves
-- signup one path.
--
-- 1. seed_default_pipeline(uuid) stays THE definition of the default board: a
--    reviewed literal (keys minted as api/pipeline.py mints them from a label;
--    the generator SELECT that prints it from a live board is in the tenancy
--    reference). No ON CONFLICT: a new account has no rows, so a conflict is a
--    fault that must fail the signup loudly, not drop a stage quietly.
-- 2. handle_new_user() loses the legacy-backfill branch. legacy_backfill_claim
--    was claimed on 2026-07-11 (and in the CI replay by migrations.yml's
--    simulated first signup) and the claim is ON CONFLICT DO NOTHING, so the
--    branch is dead everywhere; the tenant fixture disabled the trigger only
--    because it would mint a second account for its hand-made users. The
--    monitoring collection and the 7-day trial become two inserts inside the
--    trigger.
-- 3. backfill_legacy_account_id, seed_default_collections and
--    seed_trial_entitlement are dropped. The last shipped executable by PUBLIC:
--    362 revoked anon + authenticated, which leaves the built-in PUBLIC grant,
--    so anyone holding the anon key could insert a trial row for any account.
-- 4. Functions are born private from here on. 299's per-schema form
--    (`alter default privileges ... in schema public revoke ... from public`)
--    was a no-op: a per-schema default cannot take away the global built-in
--    PUBLIC EXECUTE (ALTER DEFAULT PRIVILEGES, Notes). The global form below
--    works. The Supabase bootstrap's per-schema EXECUTE to authenticated goes
--    too: a function reaches authenticated only by an explicit grant, which is
--    what the skill has always said.
-- 5. property_pipeline_events.{from,to}_stage_id become account-qualified
--    foreign keys, as property_pipeline.stage_id is since 295: FK checks bypass
--    RLS, so a single-column reference let an event name another account's
--    stage. Zero rows violate; MATCH SIMPLE leaves a NULL from_stage_id alone.
-- 6. Data. Seeded boards born after 377 carry NULL codes; live rows take the
--    label's number (377's own rule; operator-approved 2026-10-16), never a
--    code the operator set. property_notes id 2 was written on 2026-07-14,
--    before note writes were account-scoped (#886), when the claiming account
--    was the only one with a member; its carry row copied the NULL during a
--    merge on 2026-10-16. Both go to that account (the column stays nullable:
--    the split preview models an admin's bare note that way,
--    tests/test_property_split_live.py).
--
-- legacy_backfill_claim stays: read by nothing after this, and dropping a table
-- is the operator's call (not ruled on 2026-10-16).
--
-- Apply 606 (identity ids) BEFORE this file: until then a signed-in user could
-- pre-claim pipeline_stages / collections ids, and the seed's conflict would
-- fail a signup instead of silently dropping a stage.

set lock_timeout = '5s';

begin;

-- 1. The default board --------------------------------------------------------
create or replace function seed_default_pipeline(target_account_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into pipeline_stages
    (account_id, key, label, position, color, code, is_entry, is_terminal)
  values
    (target_account_id, '1_for_review',            '1. For Review',            1,  'teal',  '1', true,  false),
    (target_account_id, '2_for_call',              '2. For Call',              2,  'plum',  '2', false, false),
    (target_account_id, '3_for_visit',             '3. For Visit',             3,  'slate', '3', false, false),
    (target_account_id, '4_budgeting_and_pricing', '4. Budgeting and Pricing', 4,  'ochre', '4', false, false),
    (target_account_id, '5_submit_bid',            '5. Submit Bid',            5,  'brick', '5', false, false),
    (target_account_id, '5_negotiations',          '5. Negotiations',          6,  'sage',  '5', false, false),
    (target_account_id, '9_bid_and_forget',        '9. Bid and forget',        7,  'sand',  '9', false, true),
    (target_account_id, '9_passed',                '9. Passed',                8,  'sand',  '9', false, true),
    (target_account_id, '9_lost',                  '9. Lost',                  9,  'sand',  '9', false, true),
    (target_account_id, '9_bought',                '9. Bought',                10, 'sand',  '9', false, true);
end;
$$;
revoke all on function seed_default_pipeline(uuid) from public, anon, authenticated;

-- 2. One signup path ----------------------------------------------------------
create or replace function handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  new_account_id uuid;
begin
  insert into accounts (kind, name)
    values ('personal', coalesce(new.email, new.id::text))
    returning id into new_account_id;
  insert into account_members (account_id, user_id, role)
    values (new_account_id, new.id, 'owner');
  perform seed_default_pipeline(new_account_id);
  insert into collections (account_id, name, is_system)
    values (new_account_id, 'monitoring', true);
  -- plan='free' + status='trialing': the free plan's trial quota for 7 days
  -- (api/estimation_runs.py _resolve_entitlement), then its monthly quota.
  insert into entitlements (account_id, plan, status, current_period_end)
    values (new_account_id, 'free', 'trialing', now() + interval '7 days');
  return new;
end;
$$;
revoke all on function handle_new_user() from public, anon, authenticated;

drop function if exists backfill_legacy_account_id(uuid);
drop function if exists seed_default_collections(uuid);
drop function if exists seed_trial_entitlement(uuid);

-- 3. Functions are born private ------------------------------------------------
alter default privileges for role postgres revoke execute on functions from public;
alter default privileges for role postgres in schema public
  revoke execute on functions from authenticated;

-- 4. Account-qualified stage references on the event ledger --------------------
alter table property_pipeline_events
  drop constraint property_pipeline_events_from_stage_id_fkey,
  drop constraint property_pipeline_events_to_stage_id_fkey,
  add constraint property_pipeline_events_from_stage_id_fkey
    foreign key (account_id, from_stage_id) references pipeline_stages (account_id, id),
  add constraint property_pipeline_events_to_stage_id_fkey
    foreign key (account_id, to_stage_id) references pipeline_stages (account_id, id);

-- 5. Data ------------------------------------------------------------------------
update pipeline_stages
   set code = substring(label from '^\s*([0-9]{1,3})'), updated_at = now()
 where code is null and archived_at is null and label ~ '^\s*[0-9]';

update property_notes n
   set account_id = c.account_id
  from legacy_backfill_claim c
 where c.claim_key = 'legacy_backfill_v1' and n.account_id is null;
update property_merge_carries m
   set account_id = c.account_id
  from legacy_backfill_claim c
 where c.claim_key = 'legacy_backfill_v1'
   and m.table_name = 'property_notes' and m.account_id is null;

do $$
begin
  assert not has_function_privilege('anon', 'public.seed_default_pipeline(uuid)', 'execute'),
         'anon can execute seed_default_pipeline';
  assert not has_function_privilege('anon', 'public.handle_new_user()', 'execute'),
         'anon can execute handle_new_user';
  assert not has_function_privilege('authenticated', 'public.seed_default_pipeline(uuid)', 'execute'),
         'authenticated can execute seed_default_pipeline';
  assert (select count(*) from pipeline_stages
           where archived_at is null and code is null and label ~ '^\s*[0-9]') = 0,
         'a live numbered stage still has no code';
end $$;

commit;
