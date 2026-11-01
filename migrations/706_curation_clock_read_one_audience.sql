-- 706: curation sync, the clock read evaluates its audience once (docs/design/curation-sync.md § 3.2 and § 3.8;
-- spec #2197, follow-up F16 #2308).
--
-- WHY: production read the clock at 4.9 ms mean over 1,266 calls (~133 shared buffers each, 2026-11-01) against gate
--   #2203's 2.5 ms line. One read evaluated curation_clock_audience() FOUR times: once in each of 696's three policy
--   arms (three hashed SubPlans) and once more in the view's join. Each evaluation is several SECURITY DEFINER
--   current_account_ids() calls plus a pipeline_shares read under its own RLS; the tables are tiny, so the cost is
--   per evaluation, not per row.
-- NOW: the policy is ONE hashed SubPlan over the (account, family) pairs the audience follows, so the audience runs
--   once per statement, and the view reads `mine` from current_account_ids() with no join (the policy already keeps
--   only the followed rows). Who follows whom is 696's, unchanged: own accounts every family, a read share the owner's
--   board, an edit share the board and the five curation families, nobody another account's shares; "follow, never
--   act" (census i, tests/test_curation_clock_live.py). The audience function is untouched: its SET search_path
--   already keeps the planner from inlining it, so it is one Function Scan wherever it is called.
-- TRADE: no join left to probe curation_clock_pkey per followed account; the read scans the clock table under the
--   hashed SubPlan, O(clock rows): ~0.4 ms locally today, about the pre-706 cost at 2,000 accounts (§ 3.8).
-- APPLY: psql -f, statement autocommit (apply_migration.yml); each statement is idempotent and its own transaction,
--   so a re-run resumes a partial apply. ALTER POLICY, not drop and create: under autocommit the gap between those
--   two would answer every clock read empty, and each open window would read all its rows as gone. Each statement
--   takes a brief ACCESS EXCLUSIVE lock on its relation; lock_timeout bounds the wait, which the workflow retries.
--   CREATE OR REPLACE VIEW keeps the view's grants (701's: authenticated reads both relations, the service role the
--   table alone); $post$ checks them, the policy's shape and the view's five columns. Independent of 702-705.
-- ROLLBACK: forward only (rule 1): restore 696's policy and view text in a new migration.

set lock_timeout = '5s';

alter policy curation_clock_audience_read on public.curation_clock
  using ((account_id, family) in (
    select a.account_id, f.family
      from public.curation_clock_audience() a
     cross join lateral unnest(case
       when a.mine then array['board', 'dismissals', 'collections', 'tags', 'notes', 'links', 'shares']
       when a.edit then array['board', 'dismissals', 'collections', 'tags', 'notes', 'links']
       else array['board'] end) as f(family)));

create or replace view public.curation_clock_public with (security_invoker = true) as
  select c.account_id, c.family, c.counter, c.account_id in (select public.current_account_ids()) as mine,
         greatest(0, extract(epoch from statement_timestamp() - c.changed_at) * 1000)::bigint as age_ms
    from public.curation_clock c;

do $post$
declare
  v_qual text;
begin
  select pg_get_expr(p.polqual, p.polrelid) into v_qual
    from pg_policy p
   where p.polrelid = 'public.curation_clock'::regclass and p.polname = 'curation_clock_audience_read';
  if v_qual is null
     or length(v_qual) - length(replace(v_qual, 'curation_clock_audience', '')) <> length('curation_clock_audience')
     or (select count(*) from pg_policy where polrelid = 'public.curation_clock'::regclass) <> 1
     or not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'curation_clock'
                     and policyname = 'curation_clock_audience_read' and cmd = 'SELECT'
                     and roles = '{authenticated}'::name[] and with_check is null) then
    raise exception '706: the clock needs one SELECT policy for authenticated naming the audience once: %', v_qual;
  end if;
  if not exists (select 1 from pg_class where oid = 'public.curation_clock_public'::regclass
                  and reloptions @> array['security_invoker=true'])
     or (select array_agg(attname::text order by attnum) from pg_attribute
          where attrelid = 'public.curation_clock_public'::regclass and attnum > 0 and not attisdropped)
        is distinct from array['account_id', 'family', 'counter', 'mine', 'age_ms']
     or pg_get_viewdef('public.curation_clock_public'::regclass) like '%curation_clock_audience%' then
    raise exception '706: curation_clock_public is invoker, has its five columns and never calls the audience';
  end if;
  if (select prosecdef from pg_proc where oid = 'public.curation_clock_audience()'::regprocedure)
     or not has_function_privilege('authenticated', 'public.curation_clock_audience()', 'EXECUTE')
     or has_function_privilege('anon', 'public.curation_clock_audience()', 'EXECUTE')
     or not has_table_privilege('authenticated', 'public.curation_clock', 'SELECT')
     or not has_table_privilege('authenticated', 'public.curation_clock_public', 'SELECT')
     or not has_table_privilege('service_role', 'public.curation_clock', 'SELECT')
     or has_table_privilege('service_role', 'public.curation_clock_public', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('anon', 'public.curation_clock', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('anon', 'public.curation_clock_public', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('authenticated', 'public.curation_clock', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('authenticated', 'public.curation_clock_public', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('service_role', 'public.curation_clock', 'INSERT,UPDATE,DELETE,TRUNCATE') then
    raise exception '706: clock grants drifted from 701: the browser reads both, service_role the table, none write';
  end if;
end $post$;

reset lock_timeout;
