-- Cadastre prices bought on request (#2595, spec #2590).
--
-- An account asks for the registered prices of one building's units: one paid WSDP output (50 Kč for up
-- to 200 units), bought with that account's own ČÚZK login (`account_cuzk_logins`, 759) on its own ČÚZK
-- account. Operator ruling 2026-11-22: a bought price is visible ONLY to the account that bought it, so
-- neither table is shared and neither joins `sold_transactions`.
--
--   cadastre_price_purchases  one row per purchase, written BEFORE anything is sent to ČÚZK. It is the
--                             spending ledger and the purchase's state (the table of who may move which
--                             state, and on what guard, heads api/cadastre_prices.py):
--                               pending     no order is on record yet
--                               ordered     ČÚZK accepted the order, `report_id` is known; `fetch_sent_at` is
--                                           set before the fetch that charges, so a lost answer is known
--                               done        fetched, charged (`cost_czk`), its deeds stored
--                               failed      ended with nothing charged (the order failed, or the report was
--                                           never fetched and ČÚZK no longer serves it, or states another price)
--                               paid_unread fetched, so charged or possibly charged, but its deeds are not
--                                           stored; retried for free, from `raw_report` when it is kept
--                             `raw_report` is the fetched file encrypted with the API's key (it names the
--                             requester's WSDP login), written before anything reads it and cleared the
--                             moment its deeds are stored. A report that held no deed keeps its file: it
--                             is the only proof that the cadastre knows no price, not that we read none.
--   cadastre_deeds            what a finished purchase returned: one row per deed. A deed has ONE price
--                             for everything it transfers, so its properties ride in `properties`
--                             ([{kind, iskn_id, label}]) and no row is per unit. Kept: proceeding number,
--                             deed kind, price, currency, three dates, the properties. NOT kept, because the
--                             report states none: names, birth numbers, addresses of persons, shares, areas.
--                             Kept until the account is deleted; the buyer paid for them.
--   account_cuzk_logins       three columns:
--     refused_at              set when ČÚZK refuses the stored login during a purchase: nothing more is sent
--                             on it until the login is saved again (which clears it).
--     run_token, run_beat_at  the login lease: ONE request per account uses the stored login at a time (a
--                             purchase run, or a save or removal of the login). Its holder renews it before
--                             every call to ČÚZK and gives it back; another request may take it only once it
--                             has not been renewed for longer than any single call can last.
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
                   check (status in ('pending', 'ordered', 'done', 'failed', 'paid_unread')),
  report_id        bigint,
  fetch_sent_at    timestamptz,
  cost_czk         integer,
  raw_report       bytea,
  error            text,
  requested_at     timestamptz not null default now(),
  finished_at      timestamptz,
  constraint cadastre_price_purchases_ordered_has_report
    check (status not in ('ordered', 'done', 'paid_unread') or report_id is not null),
  constraint cadastre_price_purchases_fetched_has_report
    check (fetch_sent_at is null or report_id is not null),
  constraint cadastre_price_purchases_raw_only_fetched
    check (raw_report is null or status in ('ordered', 'paid_unread', 'done'))
);

-- One purchase in flight per account and building: a second one cannot be started beside it.
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

alter table public.account_cuzk_logins
  add column if not exists refused_at  timestamptz,
  add column if not exists run_token   uuid,
  add column if not exists run_beat_at timestamptz;

-- A purchase run sends about twenty calls on the login; each is one row of the check ledger (762). `in_run`
-- marks the calls after a run's first, which do not count toward the account's hourly total of checks.
alter table public.account_cuzk_login_checks add column if not exists in_run boolean not null default false;

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
