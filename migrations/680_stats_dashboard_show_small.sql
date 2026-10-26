-- 680_stats_dashboard_show_small.sql -- a dashboard saves its "Malé vzorky" switch.
--
-- WHY. Operator ask 2026-10-26: Browse › Stats' "Malé vzorky" switch (URL `small=1`) shows a cell under the n ≥ 5
-- floor as its small-sample summary (min / mean / median / max, migration 678). The switch is saved WITH a
-- dashboard, so a dashboard carries it — in particular the account's "Okolí dashboard" (`property_card`, migration
-- 664), which the property page's tab draws read-only with the dashboard's own setting.
--
-- WHAT CHANGES. Additive, one column, no data.
--   public.stats_dashboards.show_small boolean NOT NULL DEFAULT false: the API's saved-set CRUD selects, inserts
--   and updates it beside the blobs (`SavedSetKind.scalar_columns`, api/saved_sets.py). Every existing dashboard
--   reads false, as before the switch existed. The working board (name IS NULL) carries the column too, but nothing
--   reads it there: `GET`/`PUT /stats/dashboard` stay `{charts}`, and the tab's switch lives in the URL.
--
-- The column reaches the browser like every other (stats_dashboards_tenant_rw / _shared_read are unchanged): a
-- recipient of a shared dashboard reads the owner's setting, which is the point — it is part of what was shared.
-- A non-volatile default is a catalog-only change (no table rewrite). Idempotent: a re-run changes nothing.
-- ROLLBACK: forward only (rule 1).

set lock_timeout = '5s';
set statement_timeout = '60s';

begin;

alter table public.stats_dashboards
  add column if not exists show_small boolean not null default false;

comment on column public.stats_dashboards.show_small is
  'True when the dashboard shows a Stats cell under the minimum sample as its min/mean/median/max summary '
  '(the Stats tab''s "Malé vzorky" switch, migration 678), saved with the dashboard (migration 680). The property '
  'page''s "Okolí dashboard" tab reads it. The working board (name IS NULL) may carry it; nothing reads it there.';

do $post$
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'stats_dashboards'
                    and column_name = 'show_small' and data_type = 'boolean'
                    and is_nullable = 'NO' and column_default = 'false') then
    raise exception '680 did not land: stats_dashboards.show_small is not boolean NOT NULL DEFAULT false';
  end if;
end
$post$;

commit;

select pg_notify('pgrst', 'reload schema');

reset statement_timeout;
reset lock_timeout;
