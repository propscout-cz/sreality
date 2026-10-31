-- 701: curation sync, the service role reads the curation clock again (docs/design/curation-sync.md § 3.2;
-- spec #2197, post-review fixes #2244).
--
-- WHY: the Backend territory's rule is that the service role reads anything (CLAUDE.md § Territories). 696's
--   `revoke all … from service_role` took SELECT along with the writes, so a service-role reader of the clock (the
--   worker's `curation_sync` health alarm reads it, as any backend tool may) was refused. This grants SELECT on the
--   table and on its read view, and nothing else: the service role still holds no INSERT, UPDATE, DELETE or
--   TRUNCATE on either, because only bump_curation_clock() writes the clock and no counter is ever reset
--   (`.claude/skills/database/references/tenancy.md` § Curation clock: "service_role reads, never writes").
-- TRUNCATE: row triggers never fire on TRUNCATE, so a TRUNCATE of a clocked table moves no counter and every open
--   window keeps the rows it showed. Never TRUNCATE a clocked table: delete instead (census e flags it).
-- APPLY: psql -f, statement autocommit (apply_migration.yml): every statement is idempotent and is its own
--   transaction, so a re-run of this file resumes a partial apply. Under autocommit the `$post$` block rolls nothing
--   back: a failed assert leaves the grants as they are and fails the apply, which the workflow's exit status, this
--   lane's live test (tests/test_curation_clock_live.py) and the census are there to catch. A GRANT takes a brief
--   lock on its relation; lock_timeout bounds the wait, which the workflow retries. Independent of 697-700.
-- ROLLBACK: forward only (rule 1): `revoke select on public.curation_clock, public.curation_clock_public from
--   service_role;` in a new migration.

set lock_timeout = '5s';

grant select on public.curation_clock to service_role;
grant select on public.curation_clock_public to service_role;

do $post$
begin
  if not has_table_privilege('service_role', 'public.curation_clock', 'SELECT')
     or not has_table_privilege('service_role', 'public.curation_clock_public', 'SELECT') then
    raise exception '701: service_role must read the clock and its view'; end if;
  if has_table_privilege('service_role', 'public.curation_clock', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('service_role', 'public.curation_clock_public', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('anon', 'public.curation_clock', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('authenticated', 'public.curation_clock', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or not has_table_privilege('authenticated', 'public.curation_clock', 'SELECT') then
    raise exception '701: clock grants drifted: only bump_curation_clock() writes the clock'; end if;
end $post$;

reset lock_timeout;
