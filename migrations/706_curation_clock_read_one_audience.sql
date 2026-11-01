-- 706: curation sync, the clock read evaluates its audience twice, not four times (docs/design/curation-sync.md
-- § 3.2 and § 3.8; spec #2197, follow-up F16 #2308).
--
-- WHY: production read the clock at 4.9 ms mean over 1,266 calls (~133 shared buffers each, 2026-11-01) against gate
--   #2203's 2.5 ms line. One read evaluated curation_clock_audience() FOUR times: once in each of 696's three policy
--   arms (three hashed SubPlans) and once more in the view's join. Each evaluation is several SECURITY DEFINER
--   current_account_ids() calls plus a pipeline_shares read under its own RLS; the tables are tiny, so the cost is
--   per evaluation, not per row.
-- NOW: TWO evaluations per read. The policy is ONE hashed SubPlan over the (account, family) pairs the audience
--   follows; the view keeps 696's join on the audience, the second evaluation, which lets the planner probe
--   curation_clock_pkey per followed account. Who follows whom is 696's, unchanged: own accounts every family, a read
--   share the owner's board, an edit share the board and the five curation families, nobody another account's
--   shares; "follow, never act" (census i, tests/test_curation_clock_live.py). The view and the audience function
--   are untouched; the function's SET search_path keeps the planner from inlining it, so each call is one Function
--   Scan.
-- WHY NOT ONE: PostgreSQL never pulls a policy's subquery up into a join, so without the view's join the read scans
--   the whole clock, O(clock rows), and at 2,000 accounts costs as much as 696's read or more; the operator ruled on
--   2026-11-01 to keep the primary-key path at scale for the price of the second evaluation (§ 3.8, § 12 F16).
-- APPLY: psql -f, statement autocommit (apply_migration.yml). One ALTER POLICY, idempotent and its own transaction,
--   so a re-run changes nothing. ALTER, not drop and create: under autocommit the gap between those two would answer
--   every clock read empty, and each open window would read all its rows as gone. It takes a brief ACCESS EXCLUSIVE
--   lock on curation_clock; lock_timeout bounds the wait, which the workflow retries. Nothing is granted: $post$
--   checks row security on the clock (the policy alone limits a share to its families), the policy's shape, the view
--   as 696 left it (invoker, five columns, the audience joined once) and 701's grants. Independent of 702-705.
-- ROLLBACK: forward only (rule 1): restore 696's policy text in a new migration.

set lock_timeout = '5s';

alter policy curation_clock_audience_read on public.curation_clock
  using ((account_id, family) in (
    select a.account_id, f.family
      from public.curation_clock_audience() a
     cross join lateral unnest(case
       when a.mine then array['board', 'dismissals', 'collections', 'tags', 'notes', 'links', 'shares']
       when a.edit then array['board', 'dismissals', 'collections', 'tags', 'notes', 'links']
       else array['board'] end) as f(family)));

do $post$
declare
  v_qual text;
  v_view text := pg_get_viewdef('public.curation_clock_public'::regclass);
begin
  if not (select relrowsecurity from pg_class where oid = 'public.curation_clock'::regclass) then
    raise exception '706: row security is off on curation_clock; its policy alone limits a share to its families';
  end if;
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
     or length(v_view) - length(replace(v_view, 'curation_clock_audience', '')) <> length('curation_clock_audience')
  then
    raise exception '706: curation_clock_public is invoker, has its five columns and joins the audience once: %',
      v_view;
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
