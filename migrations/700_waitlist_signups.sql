-- 700: the propscout.cz waitlist — one row per email that asked to be invited from the landing page.
--
-- WHO WRITES: only api/waitlist.py, through the open POST /waitlist and POST /waitlist/answer routes (service role).
-- WHO READS: the operator (SQL) and, later, the launch-invite sender. No browser role reads or writes it: RLS on with no
--   policy, everything revoked from anon/authenticated, like every service-role-only table.
-- CONSENT: an invitation email is a commercial communication (zákon 480/2004 Sb. § 7), so each row keeps the version of
--   the consent wording the person submitted (`consent_version`, api/waitlist.py CONSENT_VERSION, the same text the
--   landing page shows) and when; proving consent is the sender's burden.
-- DATA KEPT: the email, the consent version and time, the page variant and utm_* tags the ad carried, and the optional
--   thank-you answer. No IP address, no user agent, no cookie id.
-- `followup_token` ties the thank-you page's optional question to the row without putting the email in a URL; it is
--   handed out once, on the first signup of that email, and an answer is accepted once.
-- APPLY: before the PR merges (the API route reads this table). Additive: one table and its two unique constraints.
--   Idempotent (IF NOT EXISTS keeps an existing table as it is: change it in a forward migration).

create table if not exists public.waitlist_signups (
  id               bigint generated always as identity primary key,
  email            text        not null,
  created_at       timestamptz not null default now(),
  consent_version  text        not null,
  page_variant     text,
  utm              jsonb       not null default '{}'::jsonb,
  followup_token   uuid        not null default gen_random_uuid(),
  investor_type    text,
  note             text,
  answered_at      timestamptz,
  unsubscribed_at  timestamptz,
  invited_at       timestamptz,
  constraint waitlist_signups_email_shape check (
    email = lower(email) and length(email) between 3 and 254 and email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
  constraint waitlist_signups_consent_version_len check (length(consent_version) between 1 and 40),
  constraint waitlist_signups_page_variant_len check (length(page_variant) <= 40),
  constraint waitlist_signups_utm_object check (jsonb_typeof(utm) = 'object'),
  constraint waitlist_signups_investor_type check (investor_type in ('flip', 'rent', 'both', 'starting')),
  constraint waitlist_signups_note_len check (length(note) <= 2000),
  constraint waitlist_signups_followup_token_uq unique (followup_token),
  constraint waitlist_signups_email_uq unique (email)
);

alter table public.waitlist_signups enable row level security;
revoke all on public.waitlist_signups from public, anon, authenticated;
revoke all on sequence public.waitlist_signups_id_seq from public, anon, authenticated;

comment on table public.waitlist_signups is
  'propscout.cz landing-page waitlist (mig 700): written only by api/waitlist.py via POST /waitlist; consent_version names the wording submitted.';
