-- A second paid cadastre output beside the price report: "Informace o jednotkách" for ONE unit (WSDP
-- generujInfoOJednotkach, price list item 5004, 10 Kč a unit), bought on the account's own ČÚZK login like the
-- price report (763) and run by the same machine. Spec #2590, operator's yes 2026-11-23.
--
-- cadastre_price_purchases gains `kind` ('prices' = the building's price report, as every row so far; 'unit' =
-- one unit's information) and `unit_iskn_id` (the unit, exactly when kind = 'unit').
--
-- The in-flight rule becomes one purchase in flight per account, building, kind and unit. The old index (account,
-- building) is replaced: created first, dropped after. coalesce(unit_iskn_id, 0), because NULLs are distinct in
-- a unique index and a price purchase has no unit. Code that predates this file inserts kind 'prices' by default
-- and no unit, so between this apply and its merge the new index holds exactly what the old one held.
--
-- cadastre_unit_facts: what the report states of the unit itself, the buying account's own (ruling 2026-11-22):
-- floor area (recorded by the cadastre since 2024, so often absent), the way of use, the share of the building's
-- common parts. The report also names the unit's OWNERS (name, address, birth number): no column here holds
-- them, and the stored file has that part cut out before it is kept (operator ruling 2026-11-23).
--
-- Service-role only, like 763: RLS on, no policy, no grant to anon or authenticated.
--
-- Additive in effect (one index replaced by a wider one).
begin;

set local lock_timeout = '5s';

alter table public.cadastre_price_purchases
  add column if not exists kind text not null default 'prices',
  add column if not exists unit_iskn_id bigint;

do $constraints$
begin
  if not exists (select 1 from pg_constraint where conname = 'cadastre_price_purchases_kind_check') then
    alter table public.cadastre_price_purchases
      add constraint cadastre_price_purchases_kind_check check (kind in ('prices', 'unit'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'cadastre_price_purchases_unit_iff_unit_kind') then
    alter table public.cadastre_price_purchases
      add constraint cadastre_price_purchases_unit_iff_unit_kind check ((kind = 'unit') = (unit_iskn_id is not null));
  end if;
end
$constraints$;

create unique index if not exists cadastre_price_purchases_in_flight_kind_uq
  on public.cadastre_price_purchases (account_id, building_code, kind, coalesce(unit_iskn_id, 0))
  where status in ('pending', 'ordered');
drop index if exists public.cadastre_price_purchases_in_flight_uq;

create table if not exists public.cadastre_unit_facts (
  id            bigint generated always as identity primary key,
  purchase_id   bigint  not null references public.cadastre_price_purchases(id) on delete cascade,
  account_id    uuid    not null references public.accounts(id) on delete cascade,
  unit_iskn_id  bigint  not null,
  label         text    not null,
  floor_area_m2 numeric(10, 2),
  use_code      text,
  use_label     text,
  share_num     bigint,
  share_den     bigint,
  created_at    timestamptz not null default now()
);

create index if not exists cadastre_unit_facts_purchase_idx on public.cadastre_unit_facts (purchase_id);
create index if not exists cadastre_unit_facts_account_unit_idx on public.cadastre_unit_facts (account_id, unit_iskn_id);

alter table public.cadastre_unit_facts enable row level security;
revoke all on public.cadastre_unit_facts from public, anon, authenticated;

commit;

do $post$
declare
  v_role text;
begin
  if to_regclass('public.cadastre_unit_facts') is null then
    raise exception '767 did not land: cadastre_unit_facts is missing';
  end if;
  if not (select relrowsecurity from pg_class where oid = 'public.cadastre_unit_facts'::regclass) then
    raise exception '767 did not land: cadastre_unit_facts has row level security off';
  end if;
  if exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'cadastre_unit_facts') then
    raise exception '767 did not land: cadastre_unit_facts carries a policy (it is service-role only)';
  end if;
  foreach v_role in array array['anon', 'authenticated'] loop
    if has_table_privilege(v_role, 'public.cadastre_unit_facts', 'select')
       or has_table_privilege(v_role, 'public.cadastre_unit_facts', 'insert')
       or has_table_privilege(v_role, 'public.cadastre_unit_facts', 'update')
       or has_table_privilege(v_role, 'public.cadastre_unit_facts', 'delete') then
      raise exception '767 did not land: % holds a privilege on cadastre_unit_facts', v_role;
    end if;
  end loop;
  if (select count(*) from information_schema.columns
       where table_schema = 'public' and table_name = 'cadastre_price_purchases'
         and column_name in ('kind', 'unit_iskn_id')) <> 2 then
    raise exception '767 did not land: cadastre_price_purchases lacks kind or unit_iskn_id';
  end if;
  if (select count(*) from pg_constraint
       where conrelid = 'public.cadastre_price_purchases'::regclass
         and conname in ('cadastre_price_purchases_kind_check', 'cadastre_price_purchases_unit_iff_unit_kind')) <> 2 then
    raise exception '767 did not land: a kind constraint is missing';
  end if;
  if to_regclass('public.cadastre_price_purchases_in_flight_kind_uq') is null
     or to_regclass('public.cadastre_price_purchases_in_flight_uq') is not null then
    raise exception '767 did not land: the in-flight index was not replaced';
  end if;
end
$post$;
