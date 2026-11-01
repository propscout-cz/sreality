-- 715: curation sync W7, the notification bell follows the clock (docs/design/curation-sync.md § 3.7 and § 8 Q1b;
-- spec #2197, W7 #2327). Ruling Q1b re-ruled 2026-11-01 (the operator): "if I resolve a notification in another window
-- or the app, I want the count to update everywhere."
--
-- WHY: the bell's number (/notifications/unread-count) and the feed (/notifications/dispatches) read
--   notification_dispatches under VISIBLE_SQL, which also depends on notification_subscriptions.retired_at. A mark-seen
--   in one window reached the others only through their 60 s and 30 s polls: ruling Q1b (2026-10-31) kept notifications
--   off the clock, and those polls with them.
-- NOW: an eighth family, notifications, bumped by the same deferred constraint row trigger on notification_dispatches
--   (a mark-seen; a new event a producer writes; a merge's re-point or duplicate mark; a cascade) and on
--   notification_subscriptions (a watchdog created, edited or retired shows or hides its events). Own-only: the policy's
--   `mine` arm grows to eight families; a read share still follows the board, an edit share the board and five
--   families (641 ruling 2). No seed: an account gains its notifications row at its first bump, as a later account
--   does for any family since 696, and a row that appears counts as moved (lib/curationSync clockChanges), so the first
--   change is followed like any other. An SPA deployed before this migration meets a family it does not know and
--   re-reads nothing for it (familyTargets over an empty table set) until its own deploy, keeping its polls until then.
-- WHAT THE CLOCK STILL CANNOT SEE: a merge that moves a property's first_seen_at can withdraw a 'new' event
--   (api/notifications.py RETRACTED_SQL) without touching that account's dispatch rows; market data carries no clock
--   (census rail f), so that case reaches the bell at its next mount, as every market-side change does.
-- COST: the producers write dispatches as service_role, one statement per transaction (api/notifications.py
--   _commit_one). The trigger fires per row and bumps each (account, family) once per transaction, so a set-based
--   monitor statement that writes for N accounts adds N clock updates and N row locks at its commit; an ON CONFLICT DO
--   NOTHING that skips a row fires nothing. Nothing rewrites a watchdog per pass: notification_subscriptions moves only
--   on the owner's create, edit and retire.
-- LOCKS: only a producer or a merge holds several accounts' notifications counters at once, and those two exclude each
--   other through the notification-events lock (shared / exclusive, toolkit/property_carriers.py) held to their commit,
--   where these deferred triggers fire; a mark-seen holds one. So the family adds no lock cycle.
-- APPLY: psql -f, statement autocommit (apply_migration.yml). Every statement is idempotent: the CHECK is dropped and
--   re-added (a bump between the two meets no CHECK, which is harmless: the trigger writes only its own family), the
--   policy is altered in place (never drop + create, which would answer every clock read empty in between), the
--   triggers drop + create as 696's do.
-- ROLLBACK: forward only (rule 1): a new migration drops the two triggers, deletes the family's rows and puts the
--   seven-family CHECK and policy back.

set lock_timeout = '5s';

alter table public.curation_clock drop constraint if exists curation_clock_family_check;
alter table public.curation_clock add constraint curation_clock_family_check check (family in
  ('board', 'dismissals', 'collections', 'tags', 'notes', 'links', 'shares', 'notifications'));

-- 708's one-pass policy, the caller's own accounts following the eighth family too.
alter policy curation_clock_audience_read on public.curation_clock
  using ((account_id, family) in (
    select a.account_id, f.family
      from public.curation_clock_audience() a
     cross join lateral unnest(case
       when a.mine then array['board', 'dismissals', 'collections', 'tags', 'notes', 'links', 'shares', 'notifications']
       when a.edit then array['board', 'dismissals', 'collections', 'tags', 'notes', 'links']
       else array['board'] end) as f(family)));

drop trigger if exists notification_dispatches_curation_clock on public.notification_dispatches;
create constraint trigger notification_dispatches_curation_clock
  after insert or update or delete on public.notification_dispatches
  deferrable initially deferred for each row execute function public.bump_curation_clock('notifications');
drop trigger if exists notification_subscriptions_curation_clock on public.notification_subscriptions;
create constraint trigger notification_subscriptions_curation_clock
  after insert or update or delete on public.notification_subscriptions
  deferrable initially deferred for each row execute function public.bump_curation_clock('notifications');

comment on table public.curation_clock is
  'Curation sync (696, 715): one counter per (account, family), eight families since 715 (notifications), advanced only '
  'by bump_curation_clock() at commit, once per transaction; read through curation_clock_public. Not history.';
comment on function public.bump_curation_clock() is
  'Curation sync (696, 715): the one writer of curation_clock, on fourteen deferred constraint row triggers; bumps '
  'each (account, family) once per transaction through its transaction-local GUC.';

do $post$
declare
  v_qual text;
begin
  if (select count(*) from pg_trigger t
       where t.tgfoid = 'public.bump_curation_clock()'::regprocedure and not t.tgisinternal
         and t.tgconstraint <> 0 and t.tgdeferrable and t.tginitdeferred and t.tgenabled = 'O') <> 14 then
    raise exception '715: expected 14 deferred, enabled curation-clock triggers'; end if;
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.curation_clock'::regclass and conname = 'curation_clock_family_check'
                    and pg_get_constraintdef(oid) like '%''notifications''%') then
    raise exception '715: the family CHECK does not admit notifications'; end if;
  select pg_get_expr(p.polqual, p.polrelid) into v_qual
    from pg_policy p
   where p.polrelid = 'public.curation_clock'::regclass and p.polname = 'curation_clock_audience_read';
  if v_qual is null or length(v_qual) - length(replace(v_qual, 'notifications', '')) <> length('notifications')
     or length(v_qual) - length(replace(v_qual, 'curation_clock_audience', '')) <> length('curation_clock_audience') then
    raise exception '715: the policy names notifications once (the caller''s own arm) and the audience once: %', v_qual;
  end if;
end $post$;

reset lock_timeout;
