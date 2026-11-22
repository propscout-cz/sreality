-- An account's own ČÚZK WSDP login (#2597, spec #2590).
--
-- Each account buys cadastre outputs on its own ČÚZK account, so it enters its own WSDP username
-- and password once. One row per account. `password_enc` is a Fernet token (AES + HMAC) made by
-- the API with a key that lives only in the API service's environment (`CUZK_LOGIN_KEY`), so a
-- database read alone never yields a password. `checked_at` is when the service last accepted the
-- login (the free status call made on save).
--
-- Service-role only, like the push device registry (migration 654): RLS on, no policy, no browser
-- grant. The API resolves the caller's own account at the route edge and names it in every
-- statement. An account's deletion takes its row.
--
-- Additive.
begin;

set local lock_timeout = '5s';

create table if not exists public.account_cuzk_logins (
  account_id   uuid        primary key references public.accounts(id) on delete cascade,
  username     text        not null check (length(username) between 1 and 64),
  password_enc text        not null,
  checked_at   timestamptz not null,
  updated_at   timestamptz not null default now()
);

alter table public.account_cuzk_logins enable row level security;
revoke all on public.account_cuzk_logins from public, anon, authenticated;

comment on table public.account_cuzk_logins is
  'One account''s own ČÚZK WSDP login. password_enc is encrypted by the API (CUZK_LOGIN_KEY); service-role only.';

commit;

do $post$
declare
  v_rel constant text := 'public.account_cuzk_logins';
  v_role text;
begin
  if to_regclass(v_rel) is null then
    raise exception '759 did not land: % is missing', v_rel;
  end if;
  if not (select relrowsecurity from pg_class where oid = to_regclass(v_rel)) then
    raise exception '759 did not land: % has row level security off', v_rel;
  end if;
  if exists (select 1 from pg_policies where schemaname || '.' || tablename = v_rel) then
    raise exception '759 did not land: % carries a policy (it is service-role only)', v_rel;
  end if;
  foreach v_role in array array['anon', 'authenticated'] loop
    if has_table_privilege(v_role, v_rel, 'select') or has_table_privilege(v_role, v_rel, 'insert')
       or has_table_privilege(v_role, v_rel, 'update') or has_table_privilege(v_role, v_rel, 'delete') then
      raise exception '759 did not land: % holds a privilege on %', v_role, v_rel;
    end if;
  end loop;
end
$post$;
