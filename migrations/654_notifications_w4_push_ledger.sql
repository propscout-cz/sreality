-- 654_notifications_w4_push_ledger.sql -- notifications W4 ("Migration D"): the device registry and the push ledger.
--
-- WHY (docs/design/notifications-push/PROGRAM.md § 3.2, § 3.4, § 5 W4; audit F2, F10, F15, F23, F26):
--   Push reaches the installed iPhone app (the PWA) of the event's own account. Two facts need a home, and neither fits
--   a table that exists: the secret capability URL Apple hands one installed app (`push_subscriptions`, one row per
--   installed app per account), and what happened to one event on one device (`notification_deliveries`, one row per
--   (event, device), replacing the never-written `channel_sends`). The old ledger had no lease (F2, F10), an FK that
--   contradicted its CHECK (F15, F26) and one mutable row per channel (F23); this one is leased by `next_attempt_at`,
--   cascades with its event and its device, and ends every row in a named state.
--
-- WHAT. Additive: two new tables, their indexes, RLS on with no policy and no browser grant. No seed, no row.
--   push_subscriptions       written only by the API's /push routes (api/push_devices.py) and the worker's push lane
--                            (a device Apple reports gone). A row's account never changes; ending a row blanks the
--                            three secrets (endpoint, p256dh, auth) and keeps it, for churn. One LIVE row per endpoint
--                            (partial unique index); `origin` is the app's https origin, taken from the Origin header
--                            checked against CORS_ALLOW_ORIGINS, so a push links absolutely without SPA_BASE_URL (F11).
--                            `hold_until` is Apple's 429 on the DEVICE: until then the claim takes none of its rows,
--                            and the throttled rows got their attempt back, so a burst of 429s never fails an alert.
--   notification_deliveries  written only by the push lane (api/push_delivery.py) and POST /push/opened (`opened_at`).
--                            `next_attempt_at` is the lease: a claim moves it 2 minutes ahead, so a crashed send comes
--                            back after it; a terminal failure waits for ever ('infinity'). `reason` is one word
--                            (Apple's reason, an HTTP status, a transport error's class name, or seen | hidden | stale |
--                            device_ended | capped), never an exception string, and no column holds an endpoint.
--   Neither table names a property (rule 18's census is untouched), and neither is a tenant table: browser roles read
--   and write nothing here; the routes resolve the caller's account and write on the service role.
--
-- APPLY through apply_migration.yml (--ref feature/notifications-w4) BEFORE merging W4: the /push routes and the
-- worker's push lane read and write both tables. Safe against the deployed code: nothing reads them yet.
-- ROLLBACK: forward only (rule 1). Idempotent: a re-apply is a no-op.
-- Verify:
--   select relrowsecurity from pg_class where oid = 'public.notification_deliveries'::regclass;          -- t
--   select has_table_privilege('authenticated', 'public.push_subscriptions', 'select');                -- f

set lock_timeout = '5s';

begin;

create table if not exists public.push_subscriptions (
  id            bigint      generated always as identity primary key,
  account_id    uuid        not null references public.accounts(id) on delete cascade,
  endpoint      text,
  p256dh        text,
  auth          text,
  origin        text        not null,
  user_agent    text,
  created_at    timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  hold_until    timestamptz,
  ended_at      timestamptz,
  end_reason    text,
  constraint push_subscriptions_origin_ck check (origin ~ '^https://'),
  constraint push_subscriptions_endpoint_ck
    check (endpoint ~ '^https://[a-z0-9-]+(\.[a-z0-9-]+)*\.push\.apple\.com/'),
  constraint push_subscriptions_live_ck check ((ended_at is null) = (endpoint is not null)),
  constraint push_subscriptions_secrets_ck
    check ((endpoint is null) = (p256dh is null) and (endpoint is null) = (auth is null)),
  -- The browser's base64url, unpadded: a 65-byte uncompressed P-256 point and a 16-byte secret.
  constraint push_subscriptions_p256dh_ck check (p256dh ~ '^[A-Za-z0-9_-]{87}$'),
  constraint push_subscriptions_auth_ck check (auth ~ '^[A-Za-z0-9_-]{22}$'),
  constraint push_subscriptions_end_reason_ck
    check (end_reason in ('gone', 'signed_out', 'switched_off', 'replaced')),
  constraint push_subscriptions_ended_has_reason_ck check ((ended_at is null) = (end_reason is null))
);
comment on table public.push_subscriptions is
  'One installed app (an iPhone PWA) per account: Apple''s secret capability URL and its two keys (migration 654). '
  'Written by the /push routes and the push lane only; ending a row blanks the secrets and keeps the row.';
comment on column public.push_subscriptions.endpoint is
  'Apple''s push resource URL, a secret capability: never logged, never in an error, NULL once the row ended.';
comment on column public.push_subscriptions.origin is
  'The app''s https origin (the Origin header of its PUT, one of CORS_ALLOW_ORIGINS): a push''s absolute link base.';
comment on column public.push_subscriptions.hold_until is
  'Apple answered 429: no row of this device is claimed before this (now() + Retry-After); the throttled rows '
  'got their attempt back, so a 429 never consumes one. NULL or past: not held.';
comment on column public.push_subscriptions.created_at is
  'When this app first registered for this account, never updated: only events after it are pushed to it.';

create unique index if not exists push_subscriptions_live_endpoint_uq
  on public.push_subscriptions (endpoint) where ended_at is null;
create index if not exists push_subscriptions_live_account_idx
  on public.push_subscriptions (account_id) where ended_at is null;

create table if not exists public.notification_deliveries (
  id                    bigint      generated always as identity primary key,
  notification_id       uuid        not null references public.notification_dispatches(id) on delete cascade,
  push_subscription_id  bigint      not null references public.push_subscriptions(id) on delete cascade,
  status                text        not null default 'pending',
  attempts              integer     not null default 0,
  next_attempt_at       timestamptz not null default now(),
  created_at            timestamptz not null default now(),
  sent_at               timestamptz,
  opened_at             timestamptz,
  provider_message_id   text,
  reason                text,
  constraint notification_deliveries_status_ck
    check (status in ('pending', 'sent', 'skipped', 'gone', 'failed')),
  constraint notification_deliveries_attempts_ck check (attempts >= 0),
  constraint notification_deliveries_sent_ck check ((status = 'sent') = (sent_at is not null)),
  -- One word, so no exception text (and no endpoint inside one) can ever be stored.
  constraint notification_deliveries_reason_ck check (reason ~ '^[A-Za-z0-9_]{1,64}$'),
  constraint notification_deliveries_message_id_ck check (provider_message_id ~ '^[A-Za-z0-9-]{1,64}$'),
  constraint notification_deliveries_pair_uq unique (notification_id, push_subscription_id)
);
comment on table public.notification_deliveries is
  'One row per (event, device) (migration 654): the push ledger. pending -> sent | skipped | gone | failed; '
  'next_attempt_at is the claim''s lease. Written by the push lane, opened_at by POST /push/opened.';
comment on column public.notification_deliveries.next_attempt_at is
  'The lease and the retry clock: a claim moves it 2 min ahead; a retry 2^attempts min; terminal failed = infinity.';
comment on column public.notification_deliveries.reason is
  'Apple''s reason word, an HTTP status or a transport error class, or seen | hidden | stale | device_ended | capped.';

create index if not exists notification_deliveries_due_idx
  on public.notification_deliveries (status, next_attempt_at) where status in ('pending', 'failed');
create index if not exists notification_deliveries_device_idx
  on public.notification_deliveries (push_subscription_id);

alter table public.push_subscriptions enable row level security;
alter table public.notification_deliveries enable row level security;
revoke all on public.push_subscriptions from public, anon, authenticated;
revoke all on public.notification_deliveries from public, anon, authenticated;
revoke all on sequence public.push_subscriptions_id_seq from public, anon, authenticated;
revoke all on sequence public.notification_deliveries_id_seq from public, anon, authenticated;

commit;

do $post$
declare
  v_rel text;
  v_role text;
begin
  foreach v_rel in array array['public.push_subscriptions', 'public.notification_deliveries'] loop
    if to_regclass(v_rel) is null then
      raise exception '654 did not land: % is missing', v_rel;
    end if;
    if not (select relrowsecurity from pg_class where oid = to_regclass(v_rel)) then
      raise exception '654 did not land: % has row level security off', v_rel;
    end if;
    if exists (select 1 from pg_policies where schemaname || '.' || tablename = v_rel) then
      raise exception '654 did not land: % carries a policy (it is service-role only)', v_rel;
    end if;
    foreach v_role in array array['anon', 'authenticated'] loop
      if has_table_privilege(v_role, v_rel, 'select') or has_table_privilege(v_role, v_rel, 'insert')
         or has_table_privilege(v_role, v_rel, 'update') or has_table_privilege(v_role, v_rel, 'delete') then
        raise exception '654 did not land: % holds a privilege on %', v_role, v_rel;
      end if;
    end loop;
  end loop;
  if not exists (select 1 from pg_index i
                  where i.indexrelid = to_regclass('public.push_subscriptions_live_endpoint_uq')
                    and i.indisunique and pg_get_expr(i.indpred, i.indrelid) = '(ended_at IS NULL)') then
    raise exception '654 did not land: push_subscriptions_live_endpoint_uq is not unique on the live rows';
  end if;
  if to_regclass('public.push_subscriptions_live_account_idx') is null
     or to_regclass('public.notification_deliveries_due_idx') is null
     or to_regclass('public.notification_deliveries_device_idx') is null then
    raise exception '654 did not land: an index is missing';
  end if;
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.notification_deliveries'::regclass
                    and conname = 'notification_deliveries_pair_uq' and contype = 'u') then
    raise exception '654 did not land: notification_deliveries_pair_uq is missing';
  end if;
end
$post$;

reset lock_timeout;
