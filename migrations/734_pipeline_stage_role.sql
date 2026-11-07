-- 734_pipeline_stage_role.sql
-- A stage's ROLE: what a stage means to the app, whatever its owner calls it (operator request
-- 2026-11-06, #2429). One role today, `viewing`: the stage where a property waits for its viewing.
-- Adding a viewing date to a property (a note with a date, migration 666) offers to move its card
-- there, and the stage set-up shows which stage it is.
--
-- WHY A COLUMN, NOT THE KEY OR THE LABEL. Neither carries meaning: a key is minted once from the
-- label and never follows a rename (the operator's board: key `viewing` is "2. For Call", `offer`
-- is "3. For Visit"), and a label is the owner's own words. `is_entry` and `is_terminal` answer
-- other questions (where a bookmark lands; whether the deal is closed). So a role is its own
-- column over a fixed vocabulary (the CRM "stage type"): the next role is one more CHECK value,
-- not one more boolean.
--
-- RULES, held here:
--   * at most one stage per role per account (partial unique index);
--   * a viewing stage is open, never terminal: a viewing comes before the deal closes;
--   * an archived stage has no role (api/pipeline.py archive_stage clears it), so a retired
--     viewing stage never blocks marking another.
--
-- DATA. Backfilled once where exactly ONE live, open stage's label names a visit (`visit`,
-- `prohlíd`): 10 of 10 boards on 2026-11-07, each its "3. For Visit". A board with none or several
-- stays unmarked for its owner (operator ruling 2026-11-06). seed_default_pipeline marks
-- `3_for_visit` for every new account: 612's reviewed literal plus the role, nothing else changed.
--
-- Additive: one nullable column, two checks, one partial index, one column appended to
-- pipeline_stages_public (its grants and security_invoker re-asserted, as 641 does), the seed
-- function re-created.

set lock_timeout = '5s';

begin;

-- Both locks up front, the view first (it locks pipeline_stages with it): a reader holding the view
-- while it waits on the table can then never deadlock against the ALTER below.
lock table pipeline_stages_public in access exclusive mode;

alter table pipeline_stages
  add column if not exists role text
  check (role is null or role in ('viewing'));

comment on column pipeline_stages.role is
  'What the stage means to the app, whatever its label (migration 734): viewing = where a property '
  'waits for its viewing. At most one stage per role per account; NULL = no role.';

alter table pipeline_stages
  add constraint pipeline_stages_viewing_open check (role is distinct from 'viewing' or not is_terminal);
alter table pipeline_stages
  add constraint pipeline_stages_archived_no_role check (archived_at is null or role is null);

create unique index if not exists pipeline_stages_one_role
  on pipeline_stages (account_id, role) where role is not null;

-- The one-time backfill: a board's single open stage whose label names a visit.
with candidates as (
  select id, count(*) over (partition by account_id) as n
    from pipeline_stages
   where archived_at is null
     and not is_terminal
     and label ~* '(visit|prohl[ií]d)'
)
update pipeline_stages ps
   set role = 'viewing'
  from candidates c
 where c.id = ps.id
   and c.n = 1
   and not exists (select 1 from pipeline_stages o where o.account_id = ps.account_id and o.role = 'viewing');

-- 641's body with `role` appended, so every reader keeps its column order.
create or replace view pipeline_stages_public
with (security_invoker = true) as
  select ps.id, ps.key, ps.label, ps.position, ps.color, ps.is_terminal, ps.is_entry, ps.code,
         (select l.actor_account_id
            from latest_delegated_edit('pipeline_stages', ps.id::text, ps.updated_at) l)
           as edited_by_account_id,
         (select l.at
            from latest_delegated_edit('pipeline_stages', ps.id::text, ps.updated_at) l)
           as edited_at,
         ps.role
  from pipeline_stages ps
  where ps.archived_at is null;

revoke all on public.pipeline_stages_public from public, anon, authenticated;
grant select on public.pipeline_stages_public to authenticated;

-- 612's default board, with its "3. For Visit" as the viewing stage.
create or replace function seed_default_pipeline(target_account_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into pipeline_stages
    (account_id, key, label, position, color, code, is_entry, is_terminal, role)
  values
    (target_account_id, '1_for_review',            '1. For Review',            1,  'teal',  '1', true,  false, null),
    (target_account_id, '2_for_call',              '2. For Call',              2,  'plum',  '2', false, false, null),
    (target_account_id, '3_for_visit',             '3. For Visit',             3,  'slate', '3', false, false, 'viewing'),
    (target_account_id, '4_budgeting_and_pricing', '4. Budgeting and Pricing', 4,  'ochre', '4', false, false, null),
    (target_account_id, '5_submit_bid',            '5. Submit Bid',            5,  'brick', '5', false, false, null),
    (target_account_id, '5_negotiations',          '5. Negotiations',          6,  'sage',  '5', false, false, null),
    (target_account_id, '9_bid_and_forget',        '9. Bid and forget',        7,  'sand',  '9', false, true,  null),
    (target_account_id, '9_passed',                '9. Passed',                8,  'sand',  '9', false, true,  null),
    (target_account_id, '9_lost',                  '9. Lost',                  9,  'sand',  '9', false, true,  null),
    (target_account_id, '9_bought',                '9. Bought',                10, 'sand',  '9', false, true,  null);
end;
$$;
revoke all on function seed_default_pipeline(uuid) from public, anon, authenticated;

do $post$
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'pipeline_stages_public'
                    and column_name = 'role') then
    raise exception '734: pipeline_stages_public has no role column';
  end if;
  if to_regclass('public.pipeline_stages_one_role') is null then
    raise exception '734: pipeline_stages_one_role is missing';
  end if;
  if not exists (select 1 from pg_class where oid = 'public.pipeline_stages_public'::regclass
                    and reloptions @> array['security_invoker=true']) then
    raise exception '734: pipeline_stages_public lost security_invoker';
  end if;
  if position('''viewing''' in pg_get_functiondef('public.seed_default_pipeline(uuid)'::regprocedure)) = 0 then
    raise exception '734: seed_default_pipeline does not mark the viewing stage';
  end if;
  if has_function_privilege('authenticated', 'public.seed_default_pipeline(uuid)', 'execute') then
    raise exception '734: authenticated can execute seed_default_pipeline';
  end if;
end
$post$;

commit;
