-- 704: the agent run ledger (#2070, ADR-0001): agent_runs, the append-only diary agent_run_steps, agent_artifacts, a
-- run id on llm_calls, the executor's settings, and the claim that narrows a run's identity.
--
-- WHO WRITES: only api/agents/executor.py on the service connection. Browser roles SELECT under
--   current_account_ids() and nothing else (ADR-0004: the executor writes no tenant rows; the browser no run rows).
-- SHIP THIS BEFORE THE CODE: `LLMClient._record_call` names llm_calls.agent_run_id on every call of every lane, and a
--   missing column there loses a completion the provider has billed.
-- NOTHING RUNS FROM THIS FILE: agent_executor_enabled lands false (work_once claims nothing while it is false or
--   absent), and agent_run_allowlist lands absent (nobody may submit). Set by the operator when agent-worker exists.
--
-- THE NARROWING. A top-level JWT claim `run_account_id`, set only by api/agents/claims.caller_for, intersects
-- current_account_ids() and acting_account_ids() with that one account, turns off acting for another account, and makes
-- is_platform_admin() false. It can only remove power: an account the `sub` is not a member of yields no account. A
-- browser user cannot set it (a Supabase JWT is signed, and what a user may edit lands under user_metadata, read by no
-- identity function), and one who could would gain nothing. Without the claim all three answer as before:
-- tests/test_agent_runs_live.py runs them against 286's, 641's and 330's bodies over a grid of claims, headers, methods
-- and roles. Read pg_get_functiondef on production before applying, in case one has drifted from those files.
--
-- Additive: new tables, a nullable column, four settings, three CREATE OR REPLACE that keep their signatures and
-- grants. The index on llm_calls is CONCURRENTLY at top level (605's shape), so writers of that hot append table never
-- wait on a scan. Plain SET, never SET LOCAL: the apply path is statement-autocommit.
--
-- NO FOREIGN KEY between llm_calls and the runs, either way: `_record_call` inserts after the provider has billed, and a
-- run deleted during a call (an account's deletion) must not cost that row, which the daily ceiling counts.

set lock_timeout = '5s';

begin;

create table agent_runs (
  id                  bigint generated always as identity primary key,
  account_id          uuid        not null references accounts(id) on delete cascade,
  principal_user_id   uuid        not null references auth.users(id) on delete cascade,
  agent               text        not null,
  definition_sha      text        not null,
  tool_set_sha        text        not null,
  commit_sha          text        not null,
  provider            text        not null,
  model               text        not null,
  input               jsonb       not null,
  status              text        not null default 'queued'
                      check (status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  attempts            integer     not null default 1 check (attempts >= 1),
  lease_until         timestamptz,
  cancel_requested_at timestamptz,
  cost_usd            numeric     not null default 0,
  stop_reason         text,
  error               text,
  result_markdown     text,
  cited_property_ids  bigint[]    not null default '{}',
  files               text[]      not null default '{}',
  proposals           jsonb       not null default '[]',
  output              jsonb,
  idempotency_key     text,
  created_at          timestamptz not null default now(),
  started_at          timestamptz,
  finished_at         timestamptz,
  unique (id, account_id),
  constraint agent_runs_running_holds_a_lease check ((status = 'running') = (lease_until is not null))
);
create unique index agent_runs_idempotency_uq on agent_runs (account_id, idempotency_key)
  where idempotency_key is not null;
create index agent_runs_claim_idx on agent_runs (created_at, id) where status = 'queued';
create index agent_runs_in_flight_idx on agent_runs (account_id, status) where status in ('queued', 'running');
create index agent_runs_lease_idx on agent_runs (lease_until) where status = 'running';
create index agent_runs_account_idx on agent_runs (account_id, created_at desc);

-- A retry starts from the beginning (MVP § 5), so a step is keyed by the attempt that made it; the diary keeps them all.
create table agent_run_steps (
  run_id      bigint      not null,
  account_id  uuid        not null,
  attempt     integer     not null,
  n           integer     not null,
  kind        text        not null check (kind in ('model', 'tool', 'file')),
  name        text        not null,
  input_sha   text        not null,
  summary     text        not null,
  payload     jsonb       not null,
  llm_call_id bigint,
  cost_usd    numeric     not null default 0,
  duration_ms integer     not null,
  created_at  timestamptz not null default now(),
  primary key (run_id, attempt, n),
  foreign key (run_id, account_id) references agent_runs(id, account_id) on delete cascade
);
create index agent_run_steps_account_idx on agent_run_steps (account_id);

-- A DELETE passes only once its run is gone: the foreign key's own cascade, as an account's deletion takes the run.
create function agent_run_steps_append_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE' and not exists (select 1 from public.agent_runs r where r.id = old.run_id) then
    return old;
  end if;
  raise exception 'agent_run_steps is append-only (migration 704)' using errcode = 'restrict_violation';
end;
$$;
revoke all on function agent_run_steps_append_only() from public, anon, authenticated;
create trigger agent_run_steps_append_only before update or delete on agent_run_steps
  for each row execute function agent_run_steps_append_only();

create table agent_artifacts (
  id          bigint      generated always as identity primary key,
  account_id  uuid        not null,
  run_id      bigint      not null,
  step_attempt integer,
  step_n      integer,
  property_id bigint      references properties(id),
  kind        text        not null,
  name        text        not null,
  size_bytes  bigint      not null check (size_bytes >= 0),
  sha256      text        not null,
  storage_key text        not null,
  created_at  timestamptz not null default now(),
  foreign key (run_id, account_id) references agent_runs(id, account_id) on delete cascade,
  foreign key (run_id, step_attempt, step_n) references agent_run_steps(run_id, attempt, n) on delete cascade,
  constraint agent_artifacts_step_whole check ((step_attempt is null) = (step_n is null))
);
create index agent_artifacts_run_idx on agent_artifacts (run_id);
create index agent_artifacts_account_idx on agent_artifacts (account_id);
create index agent_artifacts_property_idx on agent_artifacts (property_id) where property_id is not null;

alter table agent_runs enable row level security;
alter table agent_run_steps enable row level security;
alter table agent_artifacts enable row level security;
revoke all on agent_runs, agent_run_steps, agent_artifacts from anon, authenticated;
grant select on agent_runs, agent_run_steps, agent_artifacts to authenticated;
create policy agent_runs_tenant_read on agent_runs for select to authenticated
  using (account_id in (select current_account_ids()));
create policy agent_run_steps_tenant_read on agent_run_steps for select to authenticated
  using (account_id in (select current_account_ids()));
create policy agent_artifacts_tenant_read on agent_artifacts for select to authenticated
  using (account_id in (select current_account_ids()));

alter table llm_calls add column agent_run_id bigint;

insert into app_settings (key, value, description) values
  ('agent_executor_enabled', 'false',
   'The agent executor''s brake: true lets work_once claim; false or absent claims nothing.'),
  ('agent_daily_ceiling_usd', '25',
   'Platform-wide agent spend per UTC day (llm_calls called_for agent_run): at or above it no new run is claimed; running runs finish under their own caps. Absent = nothing claims.'),
  ('agent_account_running_cap', '2',
   'Agent runs one account may have running at once; the rest wait in the queue. Absent = nothing claims.'),
  ('agent_account_in_flight_cap', '20',
   'Agent runs one account may hold queued or running: a submit above it is refused. Absent = no submit.')
on conflict (key) do nothing;

create or replace function current_account_ids()
  returns setof uuid
  language sql
  stable
  security definer
  set search_path = public
as $$
  select am.account_id
  from account_members am
  where am.user_id = nullif(
    current_setting('request.jwt.claims', true)::jsonb ->> 'sub', ''
  )::uuid
    and am.account_id = coalesce(
      (current_setting('request.jwt.claims', true)::jsonb ->> 'run_account_id')::uuid,
      am.account_id
    )
$$;

create or replace function acting_account_ids(for_board boolean default false) returns table (account_id uuid)
language sql stable security definer set search_path = public as $$
  with claims as (
    select nullif(current_setting('request.jwt.claims', true), '')::jsonb as c),
  me as (
    select am.account_id
      from account_members am, claims
     where am.user_id = nullif(claims.c ->> 'sub', '')::uuid
       and am.account_id = coalesce((claims.c ->> 'run_account_id')::uuid, am.account_id)),
  asked as (
    select nullif(nullif(current_setting('request.headers', true), '')::json ->> 'x-act-as', '')::uuid as id)
  select me.account_id from me, asked where asked.id is null
  union
  select s.account_id
    from pipeline_shares s, asked, claims
   where s.account_id = asked.id
     and claims.c ->> 'run_account_id' is null
     and s.recipient_account_id in (select me.account_id from me)
     and (s.mode = 'edit'
          or (for_board and coalesce(current_setting('request.method', true), '') in ('GET', 'HEAD')))
$$;

create or replace function public.is_platform_admin()
returns boolean
language sql
stable
security definer
set search_path to 'public'
as $function$
  select case
    when nullif(current_setting('request.jwt.claims', true), '') is null then
      coalesce(current_setting('role', true), 'none') = 'none'
      and coalesce(
        (select r.rolbypassrls from pg_roles r where r.rolname = session_user),
        false
      )
    when current_setting('request.jwt.claims', true)::jsonb ->> 'run_account_id' is not null then
      false
    else exists (
      select 1 from admins a
      where a.user_id = nullif(
        current_setting('request.jwt.claims', true)::jsonb ->> 'sub', ''
      )::uuid
    )
  end
$function$;

commit;

reset lock_timeout;

create index concurrently if not exists llm_calls_agent_run_idx on llm_calls (agent_run_id)
  where agent_run_id is not null;
