-- 663_stats_dashboard_property_card.sql -- the property page's "Okolí dashboard" is one named dashboard, by row.
--
-- WHY. Operator ask 2026-10-23: the dashboard the property page's neighbourhood tab draws ("Okolí dashboard",
-- frontend/src/components/listing-detail/MarketTile.tsx) is chosen as THAT dashboard, under any name — #1977
-- found it by the name "Trh v okolí", so renaming it lost it — and it carries no filter (the tab builds its own
-- sample from the property, lib/neighbourhood; a stored filter would replace that sample when the explore
-- window loads the dashboard), so the save dialog's "Uložit i nastavení filtru" is greyed out for it.
--
-- WHAT CHANGES. Additive.
--   1. public.stats_dashboards.property_card boolean NOT NULL DEFAULT false: true on the account's one dashboard
--      the property page draws. A partial unique index keeps at most ONE per account
--      (stats_dashboards_one_property_card_per_account), and a CHECK (stats_dashboards_property_card_check)
--      keeps it a NAMED dashboard that carries no filter, so a direct PostgREST write (the browser holds UPDATE
--      on the table, 647) cannot break either rule. The API moves the role in one transaction
--      (PUT /stats/dashboards/{id}/property-card: clear the caller's other row, set this one, drop its filter).
--   2. Backfill: the dashboard #1977 found by name — the account's own one named exactly 'Trh v okolí', the
--      first by position, with no filter — becomes it. Read before writing (2026-10-23): one such row in
--      production, one account, no filter. Idempotent: a re-run finds the flag already set and changes nothing.
--
-- The column reaches the browser like every other (stats_dashboards_tenant_rw / _shared_read are unchanged): a
-- recipient of a shared dashboard may read the owner's flag; the API projects it as false on a row that is not
-- the caller's own, and the SPA counts only its own.

set lock_timeout = '5s';

begin;

alter table public.stats_dashboards
  add column if not exists property_card boolean not null default false;

do $do$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.stats_dashboards'::regclass
                    and conname = 'stats_dashboards_property_card_check') then
    alter table public.stats_dashboards
      add constraint stats_dashboards_property_card_check
      check (not property_card or (name is not null and filter_spec is null));
  end if;
end
$do$;

create unique index if not exists stats_dashboards_one_property_card_per_account
  on public.stats_dashboards (account_id) where property_card;

comment on column public.stats_dashboards.property_card is
  'True on the account''s ONE named dashboard the property page''s "Okolí dashboard" tab draws (migration 663; '
  'at most one per account, never the working board, never with a filter). Moved by '
  'PUT /stats/dashboards/{id}/property-card.';

update public.stats_dashboards d
   set property_card = true
 where d.id in (select distinct on (t.account_id) t.id
                  from public.stats_dashboards t
                 where t.name = 'Trh v okolí' and t.filter_spec is null
                 order by t.account_id, t.position, t.created_at)
   and not exists (select 1 from public.stats_dashboards o
                    where o.account_id = d.account_id and o.property_card);

do $post$
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'stats_dashboards'
                    and column_name = 'property_card' and data_type = 'boolean'
                    and is_nullable = 'NO' and column_default = 'false') then
    raise exception '663 did not land: stats_dashboards.property_card is not boolean NOT NULL DEFAULT false';
  end if;
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.stats_dashboards'::regclass
                    and conname = 'stats_dashboards_property_card_check'
                    and pg_get_constraintdef(oid) like '%property_card%name IS NOT NULL%filter_spec IS NULL%') then
    raise exception '663 did not land: the property card CHECK (named, no filter) is missing';
  end if;
  if not exists (select 1 from pg_indexes
                  where schemaname = 'public' and tablename = 'stats_dashboards'
                    and indexname = 'stats_dashboards_one_property_card_per_account'
                    and indexdef like 'CREATE UNIQUE INDEX%(account_id) WHERE property_card') then
    raise exception '663 did not land: no partial unique index keeps one property card per account';
  end if;
end
$post$;

commit;

select pg_notify('pgrst', 'reload schema');

reset lock_timeout;
