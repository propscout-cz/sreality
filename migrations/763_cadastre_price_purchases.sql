-- Cadastre prices bought on request (#2595, spec #2590).
--
-- An account asks for the registered prices of one building's units: one paid WSDP output (50 Kč for up
-- to 200 units), bought with that account's own ČÚZK login (`account_cuzk_logins`, 759) on its own ČÚZK
-- account. Operator ruling 2026-11-22: a bought price is visible ONLY to the account that bought it, so
-- neither table is shared and neither joins `sold_transactions`.
--
--   cadastre_price_purchases  one row per request, written BEFORE the report is ordered. It is the
--                             spending ledger and the resume point: `report_id` is stored the moment
--                             ČÚZK accepts the order, so an interrupted request picks the same report up
--                             instead of ordering (and paying for) a second one.
--   cadastre_deeds            what a finished purchase returned: one row per deed. A deed has ONE price
--                             for everything it transfers, so its properties ride in `properties`
--                             ([{kind, iskn_id, label}]) and no row is per unit. No area, no owner: the
--                             report states neither.
--
-- Service-role only, like `account_cuzk_logins`: RLS on, no policy, no browser grant; the API names the
-- caller's own account in every statement. An account's deletion takes its rows.
--
-- Additive.
begin;

set local lock_timeout = '5s';

create table if not exists public.cadastre_price_purchases (
  id               bigint generated always as identity primary key,
  account_id       uuid        not null references public.accounts(id) on delete cascade,
  building_code    bigint      not null,
  iskn_building_id bigint      not null,
  object_count     integer,
  status           text        not null default 'pending'
                   check (status in ('pending', 'ordered', 'done', 'failed')),
  report_id        bigint,
  cost_czk         integer,
  error            text,
  requested_at     timestamptz not null default now(),
  finished_at      timestamptz,
  constraint cadastre_price_purchases_ordered_has_report
    check (status not in ('ordered', 'done') or report_id is not null)
);

-- One request in flight per account and building: the second click resumes, it never orders twice.
create unique index if not exists cadastre_price_purchases_in_flight_uq
  on public.cadastre_price_purchases (account_id, building_code) where status in ('pending', 'ordered');
create index if not exists cadastre_price_purchases_account_building_idx
  on public.cadastre_price_purchases (account_id, building_code, requested_at desc);

create table if not exists public.cadastre_deeds (
  id           bigint generated always as identity primary key,
  purchase_id  bigint  not null references public.cadastre_price_purchases(id) on delete cascade,
  account_id   uuid    not null references public.accounts(id) on delete cascade,
  proceeding   text    not null,
  kind         text    not null,
  price        numeric(16, 2),
  currency     text,
  signed_on    date,
  effective_on date,
  entered_on   date,
  properties   jsonb   not null
);

create index if not exists cadastre_deeds_purchase_idx on public.cadastre_deeds (purchase_id);

alter table public.cadastre_price_purchases enable row level security;
alter table public.cadastre_deeds enable row level security;
revoke all on public.cadastre_price_purchases from public, anon, authenticated;
revoke all on public.cadastre_deeds from public, anon, authenticated;

commit;

do $post$
declare
  v_rel text;
  v_role text;
begin
  foreach v_rel in array array['public.cadastre_price_purchases', 'public.cadastre_deeds'] loop
    if to_regclass(v_rel) is null then
      raise exception '763 did not land: % is missing', v_rel;
    end if;
    if not (select relrowsecurity from pg_class where oid = to_regclass(v_rel)) then
      raise exception '763 did not land: % has row level security off', v_rel;
    end if;
    if exists (select 1 from pg_policies where schemaname || '.' || tablename = v_rel) then
      raise exception '763 did not land: % carries a policy (it is service-role only)', v_rel;
    end if;
    foreach v_role in array array['anon', 'authenticated'] loop
      if has_table_privilege(v_role, v_rel, 'select') or has_table_privilege(v_role, v_rel, 'insert')
         or has_table_privilege(v_role, v_rel, 'update') or has_table_privilege(v_role, v_rel, 'delete') then
        raise exception '763 did not land: % holds a privilege on %', v_role, v_rel;
      end if;
    end loop;
  end loop;
end
$post$;
