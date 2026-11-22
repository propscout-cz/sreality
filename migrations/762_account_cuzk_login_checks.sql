-- The ledger of ČÚZK login checks, and their budget (#2600, spec #2590).
--
-- `PUT /account/cuzk-login` proves a login against production ČÚZK before it stores it. ČÚZK locks an
-- account for 15 minutes at THREE CONSECUTIVE refused logins; a correct login resets its counter (WSDP
-- 3.1 documentation, "Bezpečnost": "Uživatelský účet se automaticky zamyká při dosažení třech po sobě
-- jdoucích neúspěšných pokusech na dobu 15 minut … zadá chybné heslo a poté správné, počítadlo … se
-- nuluje"). Without a limit any signed-in account could try pairs from our address, learn valid from
-- invalid, and lock somebody's ČÚZK account, repeatably. One row per check, written BEFORE the outbound
-- call (`pending`) and settled after it; the API counts them and answers 429 without calling ČÚZK once a
-- budget is spent (`api/cuzk_login.py` states the three budgets).
--
-- Two KEYED hashes (HMAC, secret derived from `CUZK_LOGIN_KEY`) name the username: `username_key` of its
-- folded form (NFKC, casefold, format characters dropped), which the budget COUNTS by, so spelling variants
-- share one budget; `username_exact_key` of the name as typed, which an accepted check RESETS by, so a
-- correct login on 'Novak' never clears the run of 'novak' should ČÚZK hold them as two accounts. A
-- dictionary cannot reverse either without the server's key, and a password typed into the wrong box never
-- lands here in clear. `admitted_at` is when we took the check from the budget, `answered_at` when ČÚZK
-- answered: an accepted check resets only the unaccepted ones that had SETTLED before it was ADMITTED,
-- because the order we admitted two overlapping checks in is not the order ČÚZK handled them in. `account_id` goes NULL when its account is deleted and the row stays: the
-- per-username budget is over ALL accounts, so deleting an account must not hand its spend back.
-- Rows older than two days are pruned by the API as it admits a check.
--
-- Service-role only, like `account_cuzk_logins` (759).
--
-- Additive.
begin;

set local lock_timeout = '5s';

create table if not exists public.account_cuzk_login_checks (
  id                 bigint generated always as identity primary key,
  account_id         uuid        references public.accounts(id) on delete set null,
  username_key       text        not null check (length(username_key) = 64),
  username_exact_key text        not null check (length(username_exact_key) = 64),
  outcome            text        not null default 'pending'
                     check (outcome in ('pending', 'accepted', 'refused', 'no_answer')),
  admitted_at        timestamptz not null default now(),
  answered_at         timestamptz,
  constraint account_cuzk_login_checks_answered_ck check ((outcome = 'pending') = (answered_at is null))
);

create index if not exists account_cuzk_login_checks_username_idx
  on public.account_cuzk_login_checks (username_key, admitted_at desc);
create index if not exists account_cuzk_login_checks_accepted_idx
  on public.account_cuzk_login_checks (username_exact_key, admitted_at desc) where outcome = 'accepted';
create index if not exists account_cuzk_login_checks_account_idx
  on public.account_cuzk_login_checks (account_id, admitted_at desc);
create index if not exists account_cuzk_login_checks_admitted_at_idx
  on public.account_cuzk_login_checks (admitted_at);

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
