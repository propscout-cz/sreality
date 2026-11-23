-- 685_revert_plausible_plot_area.sql
--
-- Reverts 680: the plot measure reads every stated lot again, 534's body exactly.
--
-- Operator ruling 2026-10-27: no plausibility patches on displayed values. When a value
-- does not make sense, the user narrows it with the filters (Lot area, Price, Price
-- change), not with a hidden rule. 680's band (a lot outside 10 .. 1 000 000 m2 read as
-- no lot) and #2117's placeholder-price rule in the rollup are both withdrawn; the code
-- side is the revert of #2117. The 680 file stays (rule 1: migrations are append-only).
--
-- Effect: browse_list regains the ~800 out-of-band lots at its next 15-minute rebuild;
-- the Watchdog and the comparables cohorts read the stated lot at their next run.
-- Nothing stored changes. Declaration unchanged (IMMUTABLE, PARALLEL SAFE, no SET
-- search_path, so callers still inline it); CREATE OR REPLACE keeps the ACL.
-- Idempotent: re-running replaces the body with itself.

set lock_timeout = '5s';

begin;

create or replace function public.plot_area_m2(
  p_category_main text,
  p_area_m2 numeric,
  p_estate_area numeric
)
 RETURNS numeric
 LANGUAGE sql
 IMMUTABLE PARALLEL SAFE
AS $function$
    SELECT CASE
        WHEN p_category_main = 'pozemek' THEN p_area_m2
        ELSE p_estate_area END
$function$;

comment on function public.plot_area_m2(text, numeric, numeric) is
  'THE plot-area measure: the m2 of LAND a listing offers. For category_main = '
  '''pozemek'' that is area_m2 itself -- the headline is polymorphic by design '
  '(rule 23) and for a parcel it IS the parcel; for every other category it is '
  'estate_area, the side column carrying a dwelling''s lot beside its floor area. '
  'Exists because reading estate_area directly drops 32 626 of 101 021 active land '
  'rows (32.3%, measured 2026-09-17) whose portal states the parcel only as the '
  'headline: bazos, realitymix, mmreality and remax publish no separate parcel cell '
  'on a land page. Do NOT close that gap by filling estate_area for land in the '
  'writers -- that duplicates area_m2 into a second column and leaves every future '
  'portal to remember the rule. Returns NULL when the row states no plot, which is '
  'a visible gap and never a guess. No plausibility band (680 added one, 685 removed '
  'it, operator ruling 2026-10-27: users narrow odd lots with the Lot area filter). '
  'Python face: toolkit.measures.plot_area_m2 / plot_area_sql. No SET search_path: a '
  'SET clause blocks inlining and turns every predicate on the measure into a per-row '
  'function call.';

commit;

-- The catalog probe reads a re-bodied function as present before it is applied, so the
-- restored rule proves itself with calls: 680's out-of-band lots read as stated again.
do $post$
begin
  if public.plot_area_m2('dum', 180, 850) is distinct from 850
     or public.plot_area_m2('dum', 180, 1) is distinct from 1
     or public.plot_area_m2('dum', 180, 0) is distinct from 0
     or public.plot_area_m2('dum', 180, 48194054) is distinct from 48194054
     or public.plot_area_m2('dum', 180, null) is not null
     or public.plot_area_m2('pozemek', 1200, 900) is distinct from 1200
     or public.plot_area_m2('pozemek', 5, 900) is distinct from 5
     or public.plot_area_m2('pozemek', 2000000, null) is distinct from 2000000
     or public.plot_area_m2('byt', 68, null) is not null then
    raise exception '685: plot_area_m2 still applies a band or lost 534''s rule';
  end if;
  if exists (select 1 from pg_proc
             where oid = 'public.plot_area_m2(text,numeric,numeric)'::regprocedure
               and (provolatile <> 'i' or not proparallel = 's' or proconfig is not null)) then
    raise exception '685: plot_area_m2 lost IMMUTABLE / PARALLEL SAFE or gained a SET clause';
  end if;
end
$post$;

reset lock_timeout;
