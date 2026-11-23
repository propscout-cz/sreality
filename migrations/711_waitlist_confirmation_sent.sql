-- 711: when a waitlist entry's confirmation email went out (docs/design/email/PROGRAM.md, PR 2).
--
-- WHO WRITES: only api/waitlist.py, stamping the column to claim one send under the daily cap and clearing it if the send
--   fails. A database count, because a memory count would restart with every API deploy and stop limiting anything.
-- NULL: never sent: an entry older than this column, one over the cap, or a failed send. Nothing retries those.
-- APPLY: before the PR merges (the route's claim names this column). Additive: one nullable column. Idempotent.

alter table public.waitlist_signups add column if not exists confirmation_sent_at timestamptz;

comment on column public.waitlist_signups.confirmation_sent_at is
  'When the confirmation email was sent (mig 711); NULL = never: an older entry, over the daily cap, or a failed send.';
