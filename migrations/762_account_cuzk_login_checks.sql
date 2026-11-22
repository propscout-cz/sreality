-- The ledger of ČÚZK login checks, and their budget (#2600, spec #2590).
--
-- `PUT /account/cuzk-login` proves a login against production ČÚZK before it stores it. Without a
-- limit any signed-in account could try username/password pairs from our address, learn valid from
-- invalid, and lock somebody's ČÚZK account (ČÚZK blocks one for 15 minutes after three refused
-- logins), repeatably. One row per check, written BEFORE the outbound call (`pending`) and settled
-- after it; the API counts the unaccepted ones per username and per account and answers 429 without
-- calling ČÚZK once a budget is spent (`api/cuzk_login.py`).
--
-- The username is stored as its SHA-256: the count needs equality only, and a password typed into
-- the wrong box never lands here in clear. Service-role only, like `account_cuzk_logins` (759).
--
-- Additive.
begin;

set local lock_timeout = '5s';

create table if not exists public.account_cuzk_login_checks (
  id           bigint generated always as identity primary key,
  account_id   uuid        not null references public.accounts(id) on delete cascade,
  username_sha text        not null check (length(username_sha) = 64),
  outcome      text        not null default 'pending'
               check (outcome in ('pending', 'accepted', 'refused', 'no_answer')),
  checked_at   timestamptz not null default now()
);

create index if not exists account_cuzk_login_checks_username_idx
  on public.account_cuzk_login_checks (username_sha, checked_at desc);
create index if not exists account_cuzk_login_checks_account_idx
  on public.account_cuzk_login_checks (account_id, checked_at desc);

alter table public.account_cuzk_login_checks enable row level security;
revoke all on public.account_cuzk_login_checks from public, anon, authenticated;

commit;

do $post$
declare
  v_rel constant text := 'public.account_cuzk_login_checks';
  v_role text;
begin
  if to_regclass(v_rel) is null then
    raise exception '762 did not land: % is missing', v_rel;
  end if;
  if not (select relrowsecurity from pg_class where oid = to_regclass(v_rel)) then
    raise exception '762 did not land: % has row level security off', v_rel;
  end if;
  if exists (select 1 from pg_policies where schemaname || '.' || tablename = v_rel) then
    raise exception '762 did not land: % carries a policy (it is service-role only)', v_rel;
  end if;
  foreach v_role in array array['anon', 'authenticated'] loop
    if has_table_privilege(v_role, v_rel, 'select') or has_table_privilege(v_role, v_rel, 'insert')
       or has_table_privilege(v_role, v_rel, 'update') or has_table_privilege(v_role, v_rel, 'delete') then
      raise exception '762 did not land: % holds a privilege on %', v_role, v_rel;
    end if;
  end loop;
end
$post$;
