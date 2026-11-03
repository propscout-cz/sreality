-- 724: the notification email (docs/design/email/PROGRAM.md, PR 4) — each account's choice and the ledger of what went out.
--
-- `account_notification_email`: one row per account. `mode` is off | daily | instant; an account with no row reads as
--   `daily`, the default (the worker's email lane inserts the missing rows as each pass begins, so every email it sends has
--   an `unsubscribe_token`). The token is the one credential of the email's off link: it sets `mode = 'off'` and nothing else.
-- `notification_email_sends`: one row per email claimed, never per event (an email covers many events and has no device,
--   so it is not `notification_deliveries`, which is keyed on event x device). `covered_until` is the claim's clock: an
--   email lists the account's VISIBLE unseen events dispatched after the previous claim that did not fail and at most 24 h
--   before it. A daily row is unique per account and Prague `local_day`, so a day sends at most one; a daily check with
--   nothing to say is kept as `empty`, so an event later that day sends nothing. A `failed` daily is claimed again at most
--   three times in all; `reason` is the HTTP failure's kind and status, never an address. A row left `sending` (a process
--   that died mid-send) counts as sent: an email never goes twice.
-- WHO WRITES: the settings, only the API (`PUT /account/notification-email`, `POST /notifications/email/unsubscribe`,
--   api/routes/notification_email.py) and the lane's missing-row insert; the sends, only api/notification_email.py on the
--   realtime worker's `email` lane, which appends a row and then updates only its status. No browser role reads or writes
--   either: RLS on with no policy, everything revoked (the push tables' posture, migration 654).
-- APPLY: before the PR merges (the routes and the lane read both tables). Additive: two tables, their indexes. Idempotent.

create table if not exists public.account_notification_email (
  account_id         uuid        primary key references public.accounts(id) on delete cascade,
  mode               text        not null default 'daily' check (mode in ('off', 'daily', 'instant')),
  unsubscribe_token  uuid        not null default gen_random_uuid() unique,
  updated_at         timestamptz not null default now()
);

alter table public.account_notification_email enable row level security;
revoke all on public.account_notification_email from public, anon, authenticated;

comment on table public.account_notification_email is
  'Each account''s notification email choice (mig 724): off | daily (the default, also for an account with no row) | instant. Service role only.';

create table if not exists public.notification_email_sends (
  id                   bigint generated always as identity primary key,
  account_id           uuid        not null references public.accounts(id) on delete cascade,
  mode                 text        not null check (mode in ('daily', 'instant')),
  local_day            date,
  claimed_at           timestamptz not null default now(),
  covered_until        timestamptz not null,
  status               text        not null check (status in ('sending', 'sent', 'empty', 'failed')),
  attempts             int         not null default 1,
  event_count          int         not null default 0,
  sent_at              timestamptz,
  provider_message_id  text,
  reason               text,
  constraint notification_email_sends_daily_day check ((mode = 'daily') = (local_day is not null))
);

create unique index if not exists notification_email_sends_daily_uq
  on public.notification_email_sends (account_id, local_day) where mode = 'daily';
create index if not exists notification_email_sends_account_claimed_idx
  on public.notification_email_sends (account_id, claimed_at desc);

alter table public.notification_email_sends enable row level security;
revoke all on public.notification_email_sends from public, anon, authenticated;
revoke all on sequence public.notification_email_sends_id_seq from public, anon, authenticated;

comment on table public.notification_email_sends is
  'One row per notification email claimed (mig 724), written only by api/notification_email.py: covered_until is the next email''s start; daily unique per Prague day.';
