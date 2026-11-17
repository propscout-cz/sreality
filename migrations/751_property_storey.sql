-- A property's storey and above-ground storey count, chosen from ALL its adverts (#2552 W4).
--
-- Until now `properties_public` took both from the canonical advert alone: 2,400 of 17,134
-- multi-advert flats hold adverts that disagree on the storey, and whichever advert happened
-- to be canonical decided. The rollup (`scripts/recompute_property_stats.py`) now writes the
-- value most adverts state, a tie going to the canonical order; these are its two columns.
--
-- Additive: two nullable columns, no default, so the ALTER rewrites nothing. No reader changes
-- here: `properties_public` keeps reading the canonical advert until a full sweep has filled
-- the columns, and a later migration switches the view.
set lock_timeout = '5s';

alter table public.properties
  add column if not exists floor integer,
  add column if not exists total_floors integer;

comment on column public.properties.floor is
  'The storey most of the property''s adverts state (ground = 0); a tie goes to the canonical order. Written by the property rollup.';
comment on column public.properties.total_floors is
  'The above-ground storey count most of the property''s adverts state; a tie goes to the canonical order. Written by the property rollup.';
