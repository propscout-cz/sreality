-- 713: per-account agent limits and a plan that carries agents (#2319; rulings in docs/design/agent-platform/MVP.md § 4).
--
-- THE LIMIT. `agent_account_daily_limit_usd` is the default every account has, $10 per UTC day; `agent_account_limits`
--   holds a row only for an account whose user chose a number, so a new account needs no row and the default moves
--   without touching one. 0 means no run is claimed. The claim reads it beside the platform ceiling, which stays.
-- THE SPEND. `agent_account_spent_today` is its one definition: the claim calls it on the service connection, GET
--   /runs/options on the caller's own. llm_calls has no browser grant, so it is a definer with its own check: a member
--   reads their account, the service role any, and anyone else gets NULL, which the claim's `<` never passes.
--   An account's deletion cascades its runs and leaves their llm_calls rows (704: no foreign key): those stop counting
--   for any account and still count towards the platform ceiling. So do the comparison script's rows, which carry no run.
--   `finished_at` prunes the runs to probe; the extra day covers a lost worker's call landing just after its run ended.
-- THE PLAN. `pilot` is `free`'s agendas and quotas plus `agents`, a comp the Stripe webhook leaves alone (api/routes/billing.py).
--   Nothing is assigned here: production's allowlist is absent today, and a public file names no account. An operator
--   assigns it per account on Settings → Tiers, or runs this once after setting the allowlist; it names no email:
--     insert into entitlements (account_id, plan, status)
--     select distinct m.account_id, 'pilot', 'active'
--       from app_settings s
--      cross join lateral jsonb_array_elements_text(s.value) as allowed(email)
--       join auth.users u on lower(u.email) = lower(allowed.email)
--       join account_members m on m.user_id = u.id
--      where s.key = 'agent_run_allowlist'
--     on conflict (account_id) do update
--       set plan = 'pilot', status = case when entitlements.status = 'canceled' then 'active' else entitlements.status end,
--           updated_at = now();
-- APPLY BEFORE MERGING (apply_migration.yml). Without the table and function GET /runs/options is a 500 for an allowed
--   account, so its Runs link disappears; PUT /runs/limit fails; a missing default parks every claim.
-- Additive and idempotent: a re-apply leaves an operator's edited default, plan or row as it is.
-- Verify:
--   select value from app_settings where key = 'agent_account_daily_limit_usd';  -- 10
--   select agendas -> 'agents', is_default from plans where key = 'pilot';       -- true, false

set lock_timeout = '5s';

begin;

create table if not exists public.agent_account_limits (
  account_id      uuid          primary key references public.accounts(id) on delete cascade,
  daily_limit_usd numeric(10,2) not null check (daily_limit_usd >= 0),
  updated_at      timestamptz   not null default now()
);

alter table public.agent_account_limits enable row level security;
revoke all on public.agent_account_limits from anon, authenticated;
grant select on public.agent_account_limits to authenticated;
drop policy if exists agent_account_limits_tenant_read on public.agent_account_limits;
create policy agent_account_limits_tenant_read on public.agent_account_limits for select to authenticated
  using (account_id in (select current_account_ids()));

insert into public.app_settings (key, value, description) values
  ('agent_account_daily_limit_usd', '10',
   'Agent spend per UTC day for an account with no agent_account_limits row: at or above it the account claims no new run. Absent = nothing claims.')
on conflict (key) do nothing;

create or replace function public.agent_account_spent_today(p_account uuid, p_called_for text[])
returns numeric
language sql
stable
security definer
set search_path = public
as $$
  select case when is_platform_admin() then s.usd
              when p_account in (select current_account_ids()) then s.usd end
    from (select coalesce(sum(c.cost_usd), 0) as usd
            from agent_runs r
            join llm_calls c on c.agent_run_id = r.id
           where r.account_id = p_account
             and (r.finished_at is null or r.finished_at >= date_trunc('day', now(), 'UTC') - interval '1 day')
             and c.called_for = any(p_called_for)
             and c.called_at >= date_trunc('day', now(), 'UTC')) s
$$;
revoke all on function public.agent_account_spent_today(uuid, text[]) from public, anon;
grant execute on function public.agent_account_spent_today(uuid, text[]) to authenticated;

insert into public.plans (key, name, position, agendas, agent_estimations_monthly_quota, trial_agent_estimations_monthly_quota)
select 'pilot', 'Pilot', position + 1, agendas || '{"agents": true}'::jsonb, agent_estimations_monthly_quota,
       trial_agent_estimations_monthly_quota
  from public.plans where is_default
on conflict (key) do nothing;

commit;

reset lock_timeout;
