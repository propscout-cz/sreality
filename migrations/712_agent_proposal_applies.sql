-- 712: what became of a run's proposals (#2072, ADR-0004): agent_proposal_applies, one row per proposal the user applied,
-- written in the SAME tenant transaction as the write it records.
--
-- WHY A TABLE AND THREE DEFINER FUNCTIONS. Apply's write and its marker must commit together, or a crash between them either
--   loses the marker (a second Apply writes twice) or the write (a marker for nothing). Apply runs under the user's login
--   (`SET LOCAL ROLE authenticated` on the tenant pool), and agent_runs must stay browser-read-only by grant (704, ADR-0004), so
--   the marker cannot be a column there. Here too `authenticated` holds SELECT and nothing else (the curation_clock precedent:
--   a tenant table whose one writer is a definer): the writes are these functions.
-- THE GATE (agent_proposal_account): `authenticated` needs EXECUTE, since the pool's transaction runs as that role, and PostgREST
--   reaches a function it may execute. So each refuses unless the session logged in as `tenant_pool` (session_user survives both
--   SET ROLE and SECURITY DEFINER; 330 relies on the same fact; PostgREST logs in as `authenticator`). A browser therefore cannot
--   mark, forge or reset a proposal, and it could gain nothing but harm to its own account if it could. Also refused: acting for
--   another account, a run's own narrowed claim (the executor writes no tenant rows), a run the caller's accounts do not hold.
-- THE COMPARE-AND-SET: agent_proposal_claim's INSERT ... ON CONFLICT DO NOTHING on the primary key, BEFORE the write. A second
--   Apply waits on the first's uncommitted row: a commit makes its claim false, so it writes nothing; a rollback (a refused write)
--   lets it through. A row is never removed, so a proposal applies once, and an undone one stays undone.
-- applied_at IS THE STAMP: now() is the transaction's start, so the rows this Apply wrote carry exactly this applied_at. Undo reads
--   that equality to tell the agent's rows from the user's (api/capabilities/writes.py).
--
-- APPLY THIS BEFORE THE CODE: GET /runs/{id}/proposals reads the table, so the Runs page's proposals section fails without it, and
--   Apply and Undo answer 500. Nothing else reads it, and no production run holds a proposal yet (the brake is off).
-- Additive and idempotent, safe for `psql -f` (apply_migration.yml, statement autocommit): a new empty table, its index, policy and
--   functions. The foreign key takes a brief SHARE ROW EXCLUSIVE on agent_runs under the lock_timeout; there is nothing to scan.
-- ROLLBACK: forward only (rule 1): drop the three functions, the helper and the table in a new migration.

set lock_timeout = '5s';

create table if not exists agent_proposal_applies (
  run_id         bigint      not null,
  account_id     uuid        not null,
  proposal_index integer     not null check (proposal_index >= 0),
  entry          text        not null,
  applied_by     uuid        not null,
  applied_at     timestamptz not null default now(),
  effect         jsonb,
  undone_by      uuid,
  undone_at      timestamptz,
  primary key (run_id, proposal_index),
  foreign key (run_id, account_id) references agent_runs(id, account_id) on delete cascade,
  constraint agent_proposal_applies_undone_whole check ((undone_at is null) = (undone_by is null))
);
create index if not exists agent_proposal_applies_account_idx on agent_proposal_applies (account_id);

alter table agent_proposal_applies enable row level security;
revoke all on agent_proposal_applies from anon, authenticated;
grant select on agent_proposal_applies to authenticated;
drop policy if exists agent_proposal_applies_tenant_read on agent_proposal_applies;
create policy agent_proposal_applies_tenant_read on agent_proposal_applies for select to authenticated
  using (account_id in (select current_account_ids()));

create or replace function public.agent_proposal_account(p_run_id bigint) returns uuid
language plpgsql stable security definer set search_path = '' as $$
declare
  v_account uuid;
begin
  if session_user <> 'tenant_pool' then
    raise exception 'a proposal is applied through the API (migration 712)' using errcode = 'insufficient_privilege';
  end if;
  if public.acting() or current_setting('request.jwt.claims', true)::jsonb ->> 'run_account_id' is not null then
    raise exception 'a proposal is applied by the signed-in user, for themselves' using errcode = 'insufficient_privilege';
  end if;
  select r.account_id into v_account from public.agent_runs r
   where r.id = p_run_id and r.status = 'succeeded' and r.account_id in (select public.current_account_ids());
  if v_account is null then
    raise exception 'no such run' using errcode = 'no_data_found';
  end if;
  return v_account;
end;
$$;
revoke all on function public.agent_proposal_account(bigint) from public, anon, authenticated, service_role;

create or replace function public.agent_proposal_claim(p_run_id bigint, p_index integer, p_entry text) returns boolean
language plpgsql security definer set search_path = '' as $$
declare
  v_account uuid := public.agent_proposal_account(p_run_id);
  v_claimed integer;
begin
  if (select r.proposals -> p_index ->> 'entry' from public.agent_runs r where r.id = p_run_id) is distinct from p_entry then
    raise exception 'that run holds no such proposal' using errcode = 'no_data_found';
  end if;
  insert into public.agent_proposal_applies (run_id, account_id, proposal_index, entry, applied_by)
  values (p_run_id, v_account, p_index, p_entry,
          (current_setting('request.jwt.claims', true)::jsonb ->> 'sub')::uuid)
  on conflict (run_id, proposal_index) do nothing;
  get diagnostics v_claimed = row_count;
  return v_claimed = 1;
end;
$$;

create or replace function public.agent_proposal_settle(p_run_id bigint, p_index integer, p_effect jsonb) returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_account uuid := public.agent_proposal_account(p_run_id);
begin
  update public.agent_proposal_applies set effect = p_effect
   where run_id = p_run_id and account_id = v_account and proposal_index = p_index and effect is null;
  if not found then
    raise exception 'that proposal is not being applied' using errcode = 'no_data_found';
  end if;
end;
$$;

create or replace function public.agent_proposal_undo(p_run_id bigint, p_index integer) returns boolean
language plpgsql security definer set search_path = '' as $$
declare
  v_account uuid := public.agent_proposal_account(p_run_id);
begin
  update public.agent_proposal_applies
     set undone_at = now(), undone_by = (current_setting('request.jwt.claims', true)::jsonb ->> 'sub')::uuid
   where run_id = p_run_id and account_id = v_account and proposal_index = p_index
     and undone_at is null and effect is not null;
  return found;
end;
$$;

revoke all on function public.agent_proposal_claim(bigint, integer, text) from public, anon, service_role;
revoke all on function public.agent_proposal_settle(bigint, integer, jsonb) from public, anon, service_role;
revoke all on function public.agent_proposal_undo(bigint, integer) from public, anon, service_role;
grant execute on function public.agent_proposal_claim(bigint, integer, text) to authenticated;
grant execute on function public.agent_proposal_settle(bigint, integer, jsonb) to authenticated;
grant execute on function public.agent_proposal_undo(bigint, integer) to authenticated;

reset lock_timeout;
