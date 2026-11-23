-- 709: curation sync, the clock's audience is planned once per backend and calls current_account_ids() once
-- (docs/design/curation-sync.md § 3.2 and § 3.8; spec #2197, follow-up F17 #2311).
--
-- WHY: after 708 a clock read evaluates curation_clock_audience() twice (the policy's one hashed SubPlan and the view's
--   join). Production read it at 2.63 ms mean and ~88 shared buffers over 25 reads (2026-11-01), against gate #2203's
--   2.5 ms line. 696's audience is LANGUAGE SQL and never inlined (its SET search_path), so its body is parsed and
--   planned at every call. Each call also runs current_account_ids() five times (three in the body, two in
--   pipeline_shares' row policy) plus acting(). Each of those is a SQL function too, planned again at each call. Warm,
--   planning is most of an evaluation: the body's own plan is ~0.09 ms of ~0.38 ms, and one current_account_ids() call
--   is 0.023 ms of planning against 0.011 ms of execution [local].
-- NOW: the same rows, three changes:
--   (a) LANGUAGE plpgsql: RETURN QUERY's plan is prepared once per backend and reused by every later read on it.
--   (b) SECURITY DEFINER: pipeline_shares is read as its owner, so its row policy is neither planned nor run (two
--       current_account_ids() calls and acting()). The body says itself what that policy still decided: the
--       recipient is one of the caller's accounts (the WHERE always required it), and no share row while acting.
--   (c) current_account_ids() once per evaluation, in a CTE, not three times.
--   Locally, median of 300 warm reads interleaved across the shapes, as authenticated: owner / edit-share recipient
--   0.93 / 1.05 ms and 11 / 13 buffers under 708 → 0.32 / 0.27 ms and 5 / 5 at 10 accounts; 0.98 / 1.08 ms and
--   13 / 18 → 0.35 / 0.32 ms and 7 / 10 at 2,010 accounts, curation_clock_pkey kept (charter § 3.8 has every shape).
-- THE FIRST READ ON A BACKEND pays catalog warm-up: ~300 execution and ~320-350 planning buffers, 1.7-2.1 ms. That is
--   the "first audience call ~270 buffers, second 9" pattern seen in production, and every shape pays it alike. This
--   shape makes it no worse (708: 1.81-2.11 ms; 709: 1.69-1.89 ms). plpgsql alone (a, a+c) adds ~0.2 ms to that read;
--   the definer takes it back.
-- THE TRADE, (b): `authenticated` may call this SECURITY DEFINER function through PostgREST
--   (/rpc/curation_clock_audience). Its rows are the invoker body's: own accounts, and the owners whose share names
--   one of the caller's accounts as recipient, none while acting. The live lane proves that for own, read share, edit
--   share, no share and x-act-as. The cost: pipeline_shares' row policy no longer backs this body, so this body is
--   the only filter. Hence the rails: census (i) and $post$ hold the definer body's `me` and its WHERE verbatim,
--   tests/_admin_gate_registry.py UNGATED registers it 'reachable', and $post$ requires the owner to bypass
--   pipeline_shares' row security (else a recipient follows nobody). The invoker shapes keep that backstop but leave
--   the warm read at 0.41-0.62 ms (a+c), and plpgsql alone makes a backend's first read slower; the definer carries
--   that part of the win.
-- FOLLOW, NEVER ACT is unchanged: current_account_ids(), never acting_account_ids(); a read sent with x-act-as
--   follows the caller's own accounts only (696, census i, tests/test_curation_clock_live.py).
-- APPLY: psql -f, statement autocommit (apply_migration.yml). CREATE OR REPLACE keeps the function's OID, owner and
--   ACL, so 708's policy and 696's view, which name it, are untouched. $pre$ refuses, before anything changes, an
--   owner that pipeline_shares' row policy would hold. A backend's cached plpgsql copy recompiles on its next call.
--   The grants are re-asserted without a gap: revoked from public, anon and service_role, granted
--   to authenticated, never revoked from it (a revoke-then-grant pair would refuse every clock read in between).
--   Every statement is idempotent; a re-run changes nothing. Independent of 702-707.
-- ROLLBACK: forward only (rule 1): restore 696's function text in a new migration.

set lock_timeout = '5s';

-- CREATE OR REPLACE keeps the function's owner, and a definer runs as it: refuse before anything changes when
-- pipeline_shares' row policy would hold that owner (the policy is for authenticated, so the owner would read no share).
do $pre$
begin
  if not exists (select 1 from pg_proc p, pg_class c, pg_roles r
                  where p.oid = 'public.curation_clock_audience()'::regprocedure
                    and c.oid = 'public.pipeline_shares'::regclass and r.oid = p.proowner
                    and (r.rolsuper or r.rolbypassrls or (c.relowner = p.proowner and not c.relforcerowsecurity))) then
    raise exception '709 refused: the audience''s owner is held by pipeline_shares'' row policy; a definer audience '
      'would follow nobody';
  end if;
end $pre$;

create or replace function public.curation_clock_audience()
returns table (account_id uuid, mine boolean, edit boolean)
language plpgsql stable security definer rows 4 set search_path = '' as $$
begin
  return query
  with me as (select t.id from public.current_account_ids() as t(id))
  select me.id, true, true from me
  union all
  select s.account_id, false, bool_or(s.mode = 'edit')
    from public.pipeline_shares s
   where s.recipient_account_id in (select me.id from me)
     and s.account_id not in (select me.id from me)
     and not public.acting()
   group by s.account_id;
end $$;
revoke all on function public.curation_clock_audience() from public, anon, service_role;
grant execute on function public.curation_clock_audience() to authenticated;

comment on function public.curation_clock_audience() is
  'Curation sync (696, 709): who the caller follows, stated once ("follow, never act"): own accounts (mine, edit) and '
  'every owner sharing a pipeline with them (edit = an edit share), none while acting. Reads current_account_ids(), '
  'never acting. plpgsql + SECURITY DEFINER since 709: planned once per backend, pipeline_shares read as its owner.';

do $post$
declare
  v_fn   oid := 'public.curation_clock_audience()'::regprocedure;
  v_src  text;
  v_qual text;
  v_view text := pg_get_viewdef('public.curation_clock_public'::regclass);
begin
  -- No policy backs a definer body, so its filters are held whole (census i holds the same two strings): a substring
  -- of either would pass `(not public.acting() or true)` or a dropped `not in`.
  select lower(regexp_replace(p.prosrc, '\s+', ' ', 'g')) into v_src from pg_proc p where p.oid = v_fn;
  if not exists (select 1 from pg_proc p join pg_language l on l.oid = p.prolang
                  where p.oid = v_fn and l.lanname = 'plpgsql' and p.prosecdef and p.provolatile = 's'
                    and p.prorows = 4 and p.proretset and p.proconfig = array['search_path=""']
                    and pg_get_function_result(p.oid) = 'TABLE(account_id uuid, mine boolean, edit boolean)')
     or strpos(v_src, 'acting_account_ids') > 0
     or strpos(v_src, 'with me as (select t.id from public.current_account_ids() as t(id))') = 0
     or strpos(v_src, 'where s.recipient_account_id in (select me.id from me) and s.account_id not in '
                      '(select me.id from me) and not public.acting() group by s.account_id') = 0 then
    raise exception '709: curation_clock_audience() is plpgsql, stable, definer, rows 4, search_path '''', and its '
      'body reads current_account_ids() for the recipient, never their own accounts, and hides share rows while acting';
  end if;
  if not exists (select 1 from pg_proc p, pg_class c, pg_roles r
                  where p.oid = v_fn and c.oid = 'public.pipeline_shares'::regclass and r.oid = p.proowner
                    and (r.rolsuper or r.rolbypassrls or (c.relowner = p.proowner and not c.relforcerowsecurity))) then
    raise exception '709: the audience''s owner is held by pipeline_shares'' row policy: a recipient would follow nobody';
  end if;
  if not has_function_privilege('authenticated', v_fn, 'EXECUTE')
     or has_function_privilege('anon', v_fn, 'EXECUTE')
     or has_function_privilege('service_role', v_fn, 'EXECUTE')
     or exists (select 1 from pg_proc p, aclexplode(p.proacl) a where p.oid = v_fn and a.grantee = 0) then
    raise exception '709: only authenticated executes curation_clock_audience()';
  end if;
  perform set_config('request.jwt.claims', '{}', true);
  if exists (select 1 from public.curation_clock_audience()) then
    raise exception '709: the audience answers a caller with no identity';
  end if;
  select pg_get_expr(p.polqual, p.polrelid) into v_qual
    from pg_policy p
   where p.polrelid = 'public.curation_clock'::regclass and p.polname = 'curation_clock_audience_read';
  if v_qual is null
     or length(v_qual) - length(replace(v_qual, 'curation_clock_audience', '')) <> length('curation_clock_audience')
     or length(v_view) - length(replace(v_view, 'curation_clock_audience', '')) <> length('curation_clock_audience')
  then
    raise exception '709: 708''s policy and 696''s view each name the audience once: % | %', v_qual, v_view;
  end if;
end $post$;

reset lock_timeout;
