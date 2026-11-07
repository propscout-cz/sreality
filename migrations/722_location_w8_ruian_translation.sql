-- 722_location_w8_ruian_translation.sql -- W8-2 (#2362): one table translates an outside source's key into RÚIAN.
-- Rulings Q10a-c of 2026-11-02 (docs/design/location-quality/PROGRAM.md): one translation table (source, the source's
-- own key, RÚIAN level and code, how it was matched, when last confirmed), Sreality Datasets first; today's rows are
-- kept, each price-stats run confirms or adds rows from ads (scraper/price_stats_main.py), and a row stays when its
-- ads end. Source 'sreality_municipality': sreality's municipality id, the key estate_prices fetches a town by.
-- No FK (mirror codes are not unique under SCD-2), no index (6,258 rows, read a few times a run), no policy, and
-- service_role loses the DELETE and TRUNCATE Supabase's default privileges give it (a row stays). The SPA never reads it.
-- Backfill: a frozen copy of price_stat_localities. 'boundary_spatial_join' where admin_boundaries.sreality_id carried
-- the key, else 'suggest_pip' (localities/suggest plus point in polygon); last_confirmed_at is resolved_at, re-stamped
-- by every run, so it means "last resolved", the nearest honest value.
-- ADDITIVE. Apply with the price-stats workflow disabled, before 723. Idempotent.
-- Receipt: 6,258 rows = 3,710 boundary_spatial_join + 2,548 suggest_pip.
-- VERIFY: select matched_by, count(*) from ruian_translation group by 1;  -- 3,710 / 2,548

set lock_timeout = '5s';

create table if not exists ruian_translation (
  source            text        not null,
  source_key        text        not null,
  ruian_level       ruian_level not null,
  ruian_code        bigint      not null,
  matched_by        text        not null,
  last_confirmed_at timestamptz not null,
  primary key (source, source_key)
);
alter table ruian_translation enable row level security;
revoke all on ruian_translation from anon, authenticated;
grant select, insert, update on ruian_translation to service_role;
revoke delete, truncate on ruian_translation from service_role;

insert into ruian_translation
select 'sreality_municipality', l.entity_id::text, 'obec', l.obec_id,
       case when b.sreality_id = l.entity_id
            then 'boundary_spatial_join' else 'suggest_pip' end,
       l.resolved_at
  from price_stat_localities l
  left join admin_boundaries b on b.id = l.obec_id and b.level = 'obec'
on conflict do nothing;
