-- 716: waitlist invitations (docs/design/email/PROGRAM.md, PR 3): only an invited address may create an account.
--
-- `invited_at` (mig 700): the permission, set at the first invitation sent and never moved. `invitation_sent_at`: the
--   latest send, invitation or "send again", which the daily cap counts. Written only by api/waitlist.py.
-- `hook_before_user_created` is Supabase Auth's Before User Created hook: `{}` allows the new account, an `error` object
--   refuses it and its message reaches the client. It reads no RLS policy, because the table holds personal data and
--   grants no policy to anyone: SECURITY DEFINER with an empty search_path, callable by Auth's role alone.
-- INERT UNTIL ENABLED: Authentication → Hooks in the dashboard, an operator step, done before sign-up is turned on.
-- APPLY: before the PR merges (the admin list and the invite route read the new column). Additive. Idempotent.

alter table public.waitlist_signups add column if not exists invitation_sent_at timestamptz;

comment on column public.waitlist_signups.invitation_sent_at is
  'The latest invitation email sent (mig 716); the daily cap counts it. invited_at is the first one, the permission.';

create or replace function public.hook_before_user_created(event jsonb)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if exists (
    select 1 from public.waitlist_signups w
    where w.email = lower(event -> 'user' ->> 'email') and w.invited_at is not null
  ) then
    return '{}'::jsonb;
  end if;
  return jsonb_build_object('error', jsonb_build_object(
    'http_code', 403,
    'message', 'Tato adresa zatím nemá pozvánku. Přidejte se na čekací listinu na propscout.cz.'
  ));
end;
$$;

revoke all on function public.hook_before_user_created(jsonb) from public, anon, authenticated;

do $$
begin
  -- Supabase's own role: absent from CI's replayed schema.
  if exists (select 1 from pg_roles where rolname = 'supabase_auth_admin') then
    grant usage on schema public to supabase_auth_admin;
    grant execute on function public.hook_before_user_created(jsonb) to supabase_auth_admin;
  end if;
end;
$$;
