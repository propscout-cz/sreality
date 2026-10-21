-- 651_notifications_w1_narrowing.sql -- notifications W1: the browser may only mark its own alerts read.
--
-- WHY (docs/design/notifications-push/PROGRAM.md § 5 W1; audit F4; ruling R10, 2026-10-21):
--   "Estimate rent" on a notification is deleted (W1a). Its route read any account's estimation run by serial id
--   and filed tenants' runs under SYSTEM, where every tenant could read them; it was the one writer of
--   notification_dispatches.estimation_run_id and the owed rule-12 fork. With it gone nothing writes that column,
--   so the browser role keeps the one column its routes write, seen_at.
--
-- WHAT CHANGES. Narrowing only: no row is written or deleted, no column or table is dropped.
--   1. authenticated: UPDATE (seen_at) on notification_dispatches, replacing 619's (seen_at, estimation_run_id).
--      The column stays (0 non-null rows) until the W6 prune drops it with the feed's other dead columns.
--
-- APPLY through apply_migration.yml (--ref feature/notifications-w1-backend) before merging W1. Safe in either order
-- against the deployed code: no browser code writes estimation_run_id (the deleted route wrote it on the service
-- role, which this does not touch).
-- ROLLBACK: forward only (rule 1). `grant update (estimation_run_id) on public.notification_dispatches to
-- authenticated;` restores the column grant. Every statement is idempotent; a lock-timeout retry re-runs the file.

set lock_timeout = '5s';

begin;

revoke update on public.notification_dispatches from authenticated;
grant update (seen_at) on public.notification_dispatches to authenticated;

commit;

reset lock_timeout;
