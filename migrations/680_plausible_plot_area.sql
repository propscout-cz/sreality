-- 680_plausible_plot_area.sql
--
-- The plot measure (migration 534) reads an impossible lot as NO lot.
--
-- Operator ruling 2026-10-26: a lot under 10 m2 or over 1 000 000 m2 (100 ha) is a
-- portal artefact, not a lot. The Browse sort by lot (#1925) put those rows on the
-- first page in both directions -- a house "on 48 194 054 m2 pozemku" at the top, a
-- run of "1 m2 pozemku" at the bottom -- and the Lot area filter, the card's lot and
-- the Watchdog read the same measure. Measured live 2026-10-26 over ACTIVE properties:
-- 158 728 carry a lot; 791 are under 10 m2 (298 of them land) and 9 are over 100 ha.
-- Those 800 now read NULL: last in either sort direction, outside every lot bound, no
-- lot on the card -- a visible gap, never a guess, as 534 already says of a row that
-- states no plot.
--
-- THE BAND LIVES IN THE MEASURE, not in its readers: browse_projection (and through it
-- browse_list, rebuilt every 15 min), the Watchdog and the comparables cohorts all call
-- plot_area_m2(), so one definition moves all of them together. Nothing stored changes:
-- area_m2 and estate_area keep what the page said, so the ruling is reversible by
-- re-applying 534's body. area_m2 itself is untouched -- the Plocha sort and the area
-- filter still read a land row's headline as stated.
--
-- Declaration unchanged from 534 and for the same reason: one SQL expression,
-- IMMUTABLE, PARALLEL SAFE, no SET search_path (a SET clause blocks inlining and turns
-- every predicate on the measure into a per-row call). Each argument appears more than
-- once now; the planner still inlines, because every caller passes a plain column.
-- Python face: toolkit.measures.plot_area_m2 + PLOT_AREA_MIN_M2 / PLOT_AREA_MAX_M2.
--
-- Additive in effect (no table written, no column touched); CREATE OR REPLACE keeps
-- the function's ACL. Idempotent: re-running replaces the body with itself.

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
        WHEN p_category_main = 'pozemek' THEN
            CASE WHEN p_area_m2 BETWEEN 10 AND 1000000 THEN p_area_m2 END
        ELSE
            CASE WHEN p_estate_area BETWEEN 10 AND 1000000 THEN p_estate_area END
        END
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
  'portal to remember the rule. Returns NULL when the row states no plot, and since '
  'migration 680 when the stated plot is outside 10 .. 1 000 000 m2 (a portal '
  'artefact, operator ruling 2026-10-26): a visible gap, never a guess. Python face: '
  'toolkit.measures.plot_area_m2 / plot_area_sql. No SET search_path: a SET clause '
  'blocks inlining and turns every predicate on the measure into a per-row function call.';

commit;

-- The catalog probe reads a re-bodied function as present before it is applied, so the
-- new rule proves itself with calls: the band's edges, both arms, and the NULL passthrough.
do $post$
begin
  if public.plot_area_m2('dum', 180, 850) is distinct from 850
     or public.plot_area_m2('dum', 180, 10) is distinct from 10
     or public.plot_area_m2('dum', 180, 1000000) is distinct from 1000000
     or public.plot_area_m2('dum', 180, 9.99) is not null
     or public.plot_area_m2('dum', 180, 1) is not null
     or public.plot_area_m2('dum', 180, 0) is not null
     or public.plot_area_m2('dum', 180, 48194054) is not null
     or public.plot_area_m2('dum', 180, null) is not null
     or public.plot_area_m2('pozemek', 1200, 900) is distinct from 1200
     or public.plot_area_m2('pozemek', 5, 900) is not null
     or public.plot_area_m2('pozemek', 2000000, null) is not null
     or public.plot_area_m2('byt', 68, null) is not null then
    raise exception '680: plot_area_m2 does not apply the 10 .. 1 000 000 m2 band';
  end if;
  if exists (select 1 from pg_proc
             where oid = 'public.plot_area_m2(text,numeric,numeric)'::regprocedure
               and (provolatile <> 'i' or not proparallel = 's' or proconfig is not null)) then
    raise exception '680: plot_area_m2 lost IMMUTABLE / PARALLEL SAFE or gained a SET clause';
  end if;
end
$post$;

reset lock_timeout;
