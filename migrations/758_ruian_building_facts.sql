-- RÚIAN building facts (#2591, spec #2590).
--
-- `ruian_building_objects` was created by migration 381 with no writer and held only a type, a
-- dwelling count and two geometries, and `ruian_address_points.stavebni_objekt_code` was never
-- filled, so no listing reached its building. The building load (`location_data.ruian_buildings`)
-- now fills both from the monthly per-obec RÚIAN exchange files. This migration adds what the
-- register states about a building and the labels of its code lists.
--
-- The table keeps its 381 shape: one row per (code, valid_from), the current one has
-- `valid_to is null`. `valid_from` is the register's own PlatiOd, so a changed building gets a
-- new row and its old one is closed.
--
-- Values are stored as the register gives them. Three things a reader must know:
--   * `floors` counts floors below ground too, so it differs from an ad's count by design;
--   * `completed_on` is a 31 December placeholder (1919, 1945, 1960, 1970, 1980, 1990, 2000)
--     for most older buildings: a period, not a date (`ruian_completion_period`);
--   * a code whose label is flagged `unknown` ("Nezjištěno", "Nedefinováno") is a gap.
--
-- Additive. Both tables are service-role only, like the rest of the mirror.
set lock_timeout = '5s';

alter table public.ruian_building_objects
  add column if not exists iskn_building_id      bigint,
  add column if not exists identifying_parcel_id bigint,
  add column if not exists house_numbers         integer[],
  add column if not exists usage_code            smallint,
  add column if not exists completed_on          date,
  add column if not exists construction_code     smallint,
  add column if not exists floors                smallint,
  add column if not exists built_up_area_m2      integer,
  add column if not exists floor_area_m2         integer,
  add column if not exists enclosed_volume_m3    integer,
  add column if not exists lift_code             smallint,
  add column if not exists water_code            smallint,
  add column if not exists sewer_code            smallint,
  add column if not exists gas_code              smallint,
  add column if not exists heating_code          smallint,
  add column if not exists protection_codes      smallint[];

create unique index if not exists ruian_bo_current_code
  on public.ruian_building_objects (code) where valid_to is null;
create index if not exists ruian_bo_current_iskn
  on public.ruian_building_objects (iskn_building_id)
  where valid_to is null and iskn_building_id is not null;

-- One row per obec file read into a registry version: the load's resume point and its receipt.
create table if not exists public.ruian_building_loads (
  version_id  bigint      not null references public.registry_versions(id),
  obec_kod    bigint      not null,
  buildings   integer     not null,
  links       integer     not null,
  sha256      text        not null,
  loaded_at   timestamptz not null default now(),
  primary key (version_id, obec_kod)
);
alter table public.ruian_building_loads enable row level security;
revoke all on public.ruian_building_loads from anon, authenticated;

create table if not exists public.ruian_code_labels (
  code_list text     not null,
  code      smallint not null,
  label     text     not null,
  unknown   boolean  not null default false,
  primary key (code_list, code)
);
alter table public.ruian_code_labels enable row level security;
revoke all on public.ruian_code_labels from anon, authenticated;

comment on table public.ruian_code_labels is
  'RÚIAN code lists of the building attributes (ČÚZK číselníky, read 2026-11-22). unknown = the register states no value.';

insert into public.ruian_code_labels (code_list, code, label, unknown) values
  ('usage', 1, 'průmyslový objekt', false),
  ('usage', 2, 'zemědělská usedlost', false),
  ('usage', 3, 'objekt k bydlení', false),
  ('usage', 4, 'objekt lesního hospodářství', false),
  ('usage', 5, 'objekt občanské vybavenosti', false),
  ('usage', 6, 'bytový dům', false),
  ('usage', 7, 'rodinný dům', false),
  ('usage', 8, 'stavba pro rodinnou rekreaci', false),
  ('usage', 9, 'stavba pro shromažďování většího počtu osob', false),
  ('usage', 10, 'stavba pro obchod', false),
  ('usage', 11, 'stavba ubytovacího zařízení', false),
  ('usage', 12, 'stavba pro výrobu a skladování', false),
  ('usage', 13, 'zemědělská stavba', false),
  ('usage', 14, 'stavba pro administrativu', false),
  ('usage', 15, 'stavba občanského vybavení', false),
  ('usage', 16, 'stavba technického vybavení', false),
  ('usage', 17, 'stavba pro dopravu', false),
  ('usage', 18, 'garáž', false),
  ('usage', 19, 'jiná stavba', false),
  ('usage', 20, 'víceúčelová stavba', false),
  ('usage', 21, 'skleník', false),
  ('usage', 22, 'přehrada', false),
  ('usage', 23, 'hráz přehrazující vodní tok nebo údolí', false),
  ('usage', 24, 'hráz k ochraně nemovitostí před zaplavením při povodni', false),
  ('usage', 25, 'hráz ohrazující umělou vodní nádrž', false),
  ('usage', 26, 'jez', false),
  ('usage', 27, 'stavba k plavebním účelům v korytech nebo na březích vodních toků', false),
  ('usage', 28, 'stavba k využití vodní energie (vodní elektrárna)', false),
  ('usage', 29, 'stavba odkaliště', false),
  ('usage', 30, 'rozestavěné jednotky', false),
  ('construction', 1, 'Cihly, tvárnice, cihlové bloky', false),
  ('construction', 2, 'Kámen', false),
  ('construction', 3, 'Kámen a cihly', false),
  ('construction', 4, 'Stěnové panely', false),
  ('construction', 5, 'Nepálené cihly', false),
  ('construction', 6, 'Dřevo', false),
  ('construction', 7, 'Jiné materiály a kombinace', false),
  ('construction', 8, 'Nedefinováno', true),
  ('construction', 9, 'Nezjištěno', true),
  ('construction', 10, 'Kámen, cihly, tvárnice včetně kombinací', false),
  ('construction', 11, 'Monolit', false),
  ('construction', 41, 'Stěnové panely: beton a železobeton', false),
  ('construction', 42, 'Stěnové panely: dřevo', false),
  ('construction', 43, 'Stěnové panely: ostatní', false),
  ('construction', 61, 'Dřevo: sruby a roubenky', false),
  ('construction', 62, 'Dřevo: lehký rámový skelet (panelová montáž)', false),
  ('construction', 63, 'Dřevo: lehký rámový skelet (staveništní montáž)', false),
  ('construction', 64, 'Dřevo: těžký skelet', false),
  ('construction', 65, 'Dřevo: panely z masivního dřeva', false),
  ('construction', 66, 'Dřevo: ostatní dřevěné konstrukce', false),
  ('sewer', 1, 'Přípoj na kanalizační síť', false),
  ('sewer', 2, 'Vlastní čistička odpadních vod', false),
  ('sewer', 3, 'Žumpa, jímka, septik', false),
  ('sewer', 4, 'Bez kanalizace a jímky', false),
  ('sewer', 8, 'Nedefinováno', true),
  ('sewer', 9, 'Nezjištěno', true),
  ('gas', 1, 'Plyn z veřejné sítě', false),
  ('gas', 2, 'Plyn z domovního zásobníku', false),
  ('gas', 3, 'Bez plynu', false),
  ('gas', 8, 'Nedefinováno', true),
  ('gas', 9, 'Nezjištěno', true),
  ('gas', 21, 'Plyn z domovního zásobníku: LPG', false),
  ('gas', 22, 'Plyn z domovního zásobníku: CNG', false),
  ('gas', 23, 'Plyn z domovního zásobníku: bioplyn', false),
  ('gas', 24, 'Plyn z domovního zásobníku: důlní plyn', false),
  ('gas', 25, 'Plyn z domovního zásobníku: ostatní', false),
  ('gas', 51, 'Plyn v domě', false),
  ('water', 1, 'S vodovodem', false),
  ('water', 2, 'Bez vodovodu', false),
  ('water', 9, 'Nezjištěno', true),
  ('lift', 1, 'S výtahem', false),
  ('lift', 2, 'Bez výtahu', false),
  ('lift', 9, 'Nezjištěno', true),
  ('heating', 1, 'Centrální domovní (kotel ve stavbě)', false),
  ('heating', 2, 'Centrální dálkové (kotel mimo stavbu)', false),
  ('heating', 3, 'Lokální (kamna nebo jiné topidlo v bytě)', false),
  ('heating', 4, 'Jiné (i bez topení)', false),
  ('heating', 9, 'Nezjištěno', true),
  ('heating', 11, 'Centrální domovní: zemní plyn', false),
  ('heating', 12, 'Centrální domovní: uhlí', false),
  ('heating', 13, 'Centrální domovní: dřevo a biomasa', false),
  ('heating', 14, 'Centrální domovní: dřevěné rostlinné pelety', false),
  ('heating', 15, 'Centrální domovní: elektřina', false),
  ('heating', 16, 'Centrální domovní: tepelné čerpadlo', false),
  ('heating', 17, 'Centrální domovní: solární a fotovoltaické systémy', false),
  ('heating', 18, 'Centrální domovní: kapalná paliva', false),
  ('heating', 19, 'Centrální domovní: jiné', false),
  ('heating', 21, 'Centrální dálkové: zemní plyn', false),
  ('heating', 22, 'Centrální dálkové: uhlí', false),
  ('heating', 23, 'Centrální dálkové: dřevo a biomasa', false),
  ('heating', 24, 'Centrální dálkové: dřevěné rostlinné pelety', false),
  ('heating', 25, 'Centrální dálkové: elektřina', false),
  ('heating', 26, 'Centrální dálkové: tepelné čerpadlo', false),
  ('heating', 27, 'Centrální dálkové: solární a fotovoltaické systémy', false),
  ('heating', 28, 'Centrální dálkové: kapalná paliva', false),
  ('heating', 29, 'Centrální dálkové: jiné', false),
  ('heating', 31, 'Lokální: zemní plyn', false),
  ('heating', 32, 'Lokální: uhlí', false),
  ('heating', 33, 'Lokální: dřevo a biomasa', false),
  ('heating', 34, 'Lokální: dřevěné rostlinné pelety', false),
  ('heating', 35, 'Lokální: elektřina', false),
  ('heating', 36, 'Lokální: tepelné čerpadlo', false),
  ('heating', 37, 'Lokální: solární a fotovoltaické systémy', false),
  ('heating', 38, 'Lokální: kapalná paliva', false),
  ('heating', 39, 'Lokální: jiné', false)
on conflict (code_list, code) do update set label = excluded.label, unknown = excluded.unknown;

-- The register's placeholder completion dates name the END of a census period; any other date is
-- a real one. Returns the period's two years, equal for a real date, NULL when the register is silent.
create or replace function public.ruian_completion_period(p_completed_on date)
returns int4range
language sql
immutable
parallel safe
set search_path = ''
as $$
  select case
    when p_completed_on is null then null
    when to_char(p_completed_on, 'MM-DD') <> '12-31' then
      int4range(extract(year from p_completed_on)::int, extract(year from p_completed_on)::int, '[]')
    else case extract(year from p_completed_on)::int
      when 1919 then int4range(null, 1919, '[]')
      when 1945 then int4range(1920, 1945, '[]')
      when 1960 then int4range(1946, 1960, '[]')
      when 1970 then int4range(1961, 1970, '[]')
      when 1980 then int4range(1971, 1980, '[]')
      when 1990 then int4range(1981, 1990, '[]')
      when 2000 then int4range(1991, 2000, '[]')
      else int4range(extract(year from p_completed_on)::int, extract(year from p_completed_on)::int, '[]')
    end
  end
$$;
revoke all on function public.ruian_completion_period(date) from public, anon, authenticated;
