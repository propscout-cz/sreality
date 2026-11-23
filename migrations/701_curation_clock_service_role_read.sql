-- 701: curation sync, the service role reads the curation clock table again (docs/design/curation-sync.md § 3.2;
-- spec #2197, post-review fixes #2244).
--
-- WHY: the doctrine. The Backend territory's service role reads anything (CLAUDE.md § Territories), and 696's
--   `revoke all … from service_role` took SELECT along with the writes. This restores the doctrine; it repairs no
--   refusal anyone saw. It grants SELECT on the table and nothing else: the service role still holds no INSERT,
--   UPDATE, DELETE or TRUNCATE, because only bump_curation_clock() writes the clock and no counter is ever reset
--   (`.claude/skills/database/references/tenancy.md` § Curation clock: "service_role reads, never writes").
-- NOT THE VIEW: curation_clock_public is the browser's read. Its audience, curation_clock_audience(), reads the
--   caller's accounts from the JWT, and 696 granted its EXECUTE to authenticated alone; a SELECT grant on the view
--   would give the service role a permission error, and with EXECUTE an empty answer. A backend reader reads the
--   table, whose rows it sees whole (service_role bypasses RLS).
-- TRUNCATE: row triggers never fire on TRUNCATE, so a TRUNCATE of a clocked table moves no counter and every open
--   window keeps the rows it showed. Never TRUNCATE a clocked table: delete instead (census e flags it).
-- APPLY: psql -f, statement autocommit (apply_migration.yml): every statement is idempotent and is its own
--   transaction, so a re-run of this file resumes a partial apply. Under autocommit the `$post$` block rolls nothing
--   back: a failed assert leaves the grants as they are and fails the apply, which the workflow's exit status, this
--   lane's live test (tests/test_curation_clock_live.py) and the census are there to catch. A GRANT takes a brief
--   lock on its relation; lock_timeout bounds the wait, which the workflow retries. Independent of 697-700.
-- ROLLBACK: forward only (rule 1): `revoke select on public.curation_clock from service_role;` in a new migration.

set lock_timeout = '5s';

grant select on public.curation_clock to service_role;

do $post$
begin
  if not has_table_privilege('service_role', 'public.curation_clock', 'SELECT') then
    raise exception '701: service_role must read the clock'; end if;
  if has_table_privilege('service_role', 'public.curation_clock', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('service_role', 'public.curation_clock_public', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('anon', 'public.curation_clock', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('anon', 'public.curation_clock_public', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('authenticated', 'public.curation_clock', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('authenticated', 'public.curation_clock_public', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or not has_table_privilege('authenticated', 'public.curation_clock', 'SELECT')
     or not has_table_privilege('authenticated', 'public.curation_clock_public', 'SELECT') then
    raise exception '701: clock grants drifted: the browser reads both, the service role the table, nobody writes';
  end if;
end $post$;

reset lock_timeout;
