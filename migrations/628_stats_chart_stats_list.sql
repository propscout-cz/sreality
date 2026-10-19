-- 628_stats_chart_stats_list.sql -- a saved Stats chart's `stat` becomes `stats`, a list.
--
-- WHY. Operator request 2026-10-19: a line chart shows several statistics at once (median, p10, p25 ...).
-- The saved chart's single `stat` string becomes `stats`, a non-empty list in the order the user picked
-- (the first leads); line, bar and a table with a column read it (toolkit/stats_registry.validate_chart).
-- The board is one jsonb document per account (626), so the stored shape changes with the code.
--
-- WHAT CHANGES. One UPDATE on public.stats_dashboards: every chart object that carries `stat` gets
-- `stats = [stat]` and loses `stat`; the charts keep their order; a `stat` outside the six chart
-- statistics (the old validator refused them, so none is stored) becomes the default, 'median'.
-- LOSSLESS (stats[0] is the old stat) and IDEMPOTENT (a chart without `stat` is untouched, so a re-run
-- changes nothing); `updated_at` is left alone, because this is a normalisation and not a user save.
-- The code reads a chart without a usable `stats` as ['median'], so merge-before-apply and
-- apply-before-merge are both safe; the standard order (apply, then merge) applies.
--
-- APPLY through apply_migration.yml (--ref the feature branch) BEFORE merging the code. No table, column,
-- grant or function changes, so nothing restarts. ROLLBACK: forward only (rule 1).

set lock_timeout = '5s';

update public.stats_dashboards d
   set charts = (
     select coalesce(jsonb_agg(
              case when c ? 'stat'
                   then (c - 'stat') || jsonb_build_object('stats', jsonb_build_array(
                          case when c->>'stat' in ('mean', 'median', 'p10', 'p25', 'p75', 'p90')
                               then c->>'stat' else 'median' end))
                   else c end
              order by ord), '[]'::jsonb)
       from jsonb_array_elements(d.charts) with ordinality as t(c, ord))
 where exists (select 1 from jsonb_array_elements(d.charts) c where c ? 'stat');

do $post$
begin
  if exists (select 1 from public.stats_dashboards d, jsonb_array_elements(d.charts) c where c ? 'stat') then
    raise exception '628 did not land: a stored chart still carries the single `stat`';
  end if;
end
$post$;

reset lock_timeout;
