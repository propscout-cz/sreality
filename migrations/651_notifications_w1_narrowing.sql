-- 651_notifications_w1_narrowing.sql -- notifications W1: the browser marks only its own alerts read.
--
-- WHY (docs/design/notifications-push/PROGRAM.md § 5 W1; audit F4 and F5; rulings R10 and R2, 2026-10-21):
--   1. "Estimate rent" on a notification is deleted (W1a). Its route read any account's estimation run by serial
--      id and filed tenants' runs under SYSTEM, where every tenant could read them; it was the one writer of
--      notification_dispatches.estimation_run_id and the owed rule-12 fork. Nothing writes that column now, so the
--      browser role keeps the one column its routes write, seen_at.
--   2. System alerts leave the bell (W1d). The health ladder (toolkit/system_alerts.reconcile) now tells by
--      stamping its ops_incidents rows and writes no notification_dispatches row; Health shows the incidents and
--      the GitHub canary e-mails an unacknowledged alarm. 902 SYSTEM-account rows sat unread in the one feed, the
--      badge counted them and "Mark all read" silently answered the canary. With the SYSTEM arms gone, the bell,
--      the badge and the feed count one set: an account's own rows.
--
-- WHAT CHANGES. Narrowing only: no row is written or deleted, no column, table or trigger is dropped.
--   1. authenticated: UPDATE (seen_at) on notification_dispatches, replacing 619's (seen_at, estimation_run_id).
--   2. notification_dispatches_tenant_read loses its SYSTEM arm (364, hoisted by 431): an account reads its own
--      rows only. ALTER POLICY, not DROP + CREATE, so the table is never unpoliced and keeps TO authenticated.
--      SYSTEM has no members, so no browser caller reads a system alert any more.
--   3. notification_dispatches_admin_update (619) is dropped: it let a platform admin mark a SYSTEM row read,
--      and no surface shows one now. 292's notification_dispatches_tenant_update is untouched.
--
-- NOT HERE. The 902 system rows stay as history (ruling R2 (b) deletes them after a backup, in the W6 window), as
-- do estimation_run_id and the other dead columns (W6). The account trigger's SYSTEM else-branch (364, 641) stays
-- until W6.
--
-- APPLY through apply_migration.yml (--ref feature/notifications-w1) before merging W1. Safe in either
-- order against the deployed code: no browser code writes estimation_run_id (the deleted route wrote it on the
-- service role, which this does not touch), and before the deploy only a platform admin's bell loses the system
-- rows it showed, which is this wave's purpose.
-- ROLLBACK: forward only (rule 1). Every statement is idempotent; a lock-timeout retry re-runs the file. The
-- previous state is 619's grant and policy text plus 431's tenant_read expression.

set lock_timeout = '5s';

begin;

revoke update on public.notification_dispatches from authenticated;
grant update (seen_at) on public.notification_dispatches to authenticated;

alter policy notification_dispatches_tenant_read on public.notification_dispatches
  using (account_id in (select current_account_ids()));

drop policy if exists notification_dispatches_admin_update on public.notification_dispatches;

commit;

do $post$
begin
  if has_table_privilege('authenticated', 'public.notification_dispatches', 'UPDATE')
     or has_column_privilege('authenticated', 'public.notification_dispatches', 'estimation_run_id', 'UPDATE')
     or not has_column_privilege('authenticated', 'public.notification_dispatches', 'seen_at', 'UPDATE') then
    raise exception '651 did not land: authenticated must UPDATE notification_dispatches.seen_at and nothing else';
  end if;
  if exists (select 1 from pg_policies
              where schemaname = 'public' and tablename = 'notification_dispatches'
                and coalesce(qual, '') || coalesce(with_check, '') like '%is_platform_admin%') then
    raise exception '651 did not land: a notification_dispatches policy still admits SYSTEM rows to a platform admin';
  end if;
  if not exists (select 1 from pg_policies
                  where schemaname = 'public' and tablename = 'notification_dispatches'
                    and policyname = 'notification_dispatches_tenant_read' and cmd = 'SELECT'
                    and roles = array['authenticated']::name[]
                    and position('current_account_ids()' in qual) > 0) then
    raise exception '651 did not land: notification_dispatches_tenant_read no longer reads the caller''s own rows';
  end if;
end
$post$;

reset lock_timeout;
