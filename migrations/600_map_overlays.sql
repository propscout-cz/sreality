-- 600_map_overlays.sql
-- Reference overlays: ONE store and ONE renderer for every static reference geometry a map
-- draws (Prague metro, flood zones, kraj borders; next, the MF rent choropleth). Design and
-- operator rulings: docs/design/map-overlays.md.
--
-- Four tables. `map_overlay_sections`: one tile source, one chip in the SPA.
-- `map_overlay_layers`: one toggle key, which is one MVT layer inside its section's tile.
-- `map_overlay_features`: the geometry in EPSG:3857, made valid, dissolved per layer and
-- subdivided by the ingest. `map_overlay_tiles`: the rendered vector tiles, one row per
-- (section, z, x, y). The registry rows come from the ingest's UPSERT
-- (scripts/ingest_map_overlays.py), so this file seeds nothing and a new layer needs no
-- migration.
--
-- WHY PRE-RENDERED TILES. The Labe Q100 flood zone alone has 127k vertices, which the
-- fetch-everything GeoJSON path the rent map uses cannot carry. `map_overlay_render_section`
-- renders a section once per ingest; GET /map-overlays/{section}/{z}/{x}/{y}.mvt answers with
-- one primary-key read and never renders at request time. `version` (epoch seconds of
-- `fetched_at`) rides in the tile URL, so the API can mark a tile immutable.
--
-- A MIRROR, not history (like the OSM mirrors, rules #10/#11): an ingest replaces a layer's
-- features and a render replaces its section's tiles wholesale.
--
-- Posture: service-role only. RLS on with zero policies and everything revoked from the
-- browser roles; the SPA reaches the registry and the tiles through the API alone. The
-- render function is revoked from public, anon and authenticated (Supabase's default ACL
-- grants every new function to both browser roles, migration 287) and granted to
-- service_role.
--
-- PostGIS is called UNQUALIFIED under `set search_path = public, extensions`: production
-- keeps it in `extensions`, the CI replay in `public` (migration 545). Only PostGIS 3.3
-- functions: production runs 3.3.7 and CI 3.5, so CI cannot catch a 3.4+ call.
--
-- Additive. APPLY BEFORE MERGING: the API routes and the ingest read these tables.
-- Idempotent (IF NOT EXISTS / CREATE OR REPLACE): a re-apply is a no-op.
-- Verify:
--   select count(*) from pg_class where relnamespace = 'public'::regnamespace and relrowsecurity
--    and relname in ('map_overlay_sections', 'map_overlay_layers', 'map_overlay_features',
--                    'map_overlay_tiles');                                                      -- 4
--   select has_table_privilege('authenticated', 'public.map_overlay_tiles', 'select');           -- f
--   select has_function_privilege('authenticated', 'public.map_overlay_render_section(text)',
--                                 'execute');                                                    -- f
--   select st_distance(st_transform(st_setsrid(st_makepoint(-741651.9, -1042827.75), 5514), 3857),
--                      st_transform(st_setsrid(st_makepoint(14.4369316, 50.09053), 4326), 3857))
--          < 1;                                                                                  -- t

set lock_timeout = '5s';

create table if not exists public.map_overlay_sections (
  key               text        primary key,
  label             text        not null,
  sort              int         not null,
  minzoom           int         not null,
  maxzoom           int         not null,
  attribution       text        not null,
  licence           text        not null,
  source_url        text        not null,
  note              text,
  source_revised_at date,
  fetched_at        timestamptz,
  version           bigint      not null default 0
);

create table if not exists public.map_overlay_layers (
  key           text  primary key,
  section       text  not null references public.map_overlay_sections(key) on delete cascade,
  label         text  not null,
  sort          int   not null,
  legend        jsonb not null default '[]'::jsonb,
  scale         jsonb,
  variants      jsonb,
  feature_count int   not null default 0
);

create table if not exists public.map_overlay_features (
  id        bigserial primary key,
  layer_key text  not null references public.map_overlay_layers(key) on delete cascade,
  source_id text,
  props     jsonb not null,
  geom      geometry(Geometry, 3857) not null
);

create index if not exists map_overlay_features_geom_idx
  on public.map_overlay_features using gist (geom);
create index if not exists map_overlay_features_layer_key_idx
  on public.map_overlay_features (layer_key);

create table if not exists public.map_overlay_tiles (
  section text  not null references public.map_overlay_sections(key) on delete cascade,
  z       int   not null,
  x       int   not null,
  y       int   not null,
  mvt     bytea not null,
  primary key (section, z, x, y)
);

alter table public.map_overlay_sections enable row level security;
alter table public.map_overlay_layers   enable row level security;
alter table public.map_overlay_features enable row level security;
alter table public.map_overlay_tiles    enable row level security;

revoke all on public.map_overlay_sections from anon, authenticated;
revoke all on public.map_overlay_layers   from anon, authenticated;
revoke all on public.map_overlay_features from anon, authenticated;
revoke all on public.map_overlay_tiles    from anon, authenticated;
revoke all on sequence public.map_overlay_features_id_seq from anon, authenticated;

comment on table public.map_overlay_sections is
  'Reference overlays: one tile source (one SPA chip) per row, written by the ingest '
  '(scripts/ingest_map_overlays.py). Tiles exist for z in [minzoom, maxzoom]; version is the '
  'epoch of fetched_at and the tile URL cache key. A row with null fetched_at is not offered.';
comment on table public.map_overlay_layers is
  'Reference overlays: one toggle key per row = one MVT layer, named by key, inside its '
  'section''s tile. legend/scale/variants drive the SPA paint.';
comment on table public.map_overlay_features is
  'Reference overlays: the geometry (EPSG:3857), replaced per layer by each ingest. props are '
  'flat scalars, encoded by ST_AsMVT as feature attributes.';
comment on table public.map_overlay_tiles is
  'Reference overlays: rendered vector tiles, replaced per section by '
  'map_overlay_render_section(); the API serves one row per request by primary key.';

create or replace function public.map_overlay_render_section(p_section text)
returns int
language plpgsql
security invoker
set search_path = public, extensions
as $$
declare
  v_world  constant double precision := 20037508.342789244;
  v_minz   int;
  v_maxz   int;
  v_xmin   double precision;
  v_ymin   double precision;
  v_xmax   double precision;
  v_ymax   double precision;
  v_layers text[];
  v_layer  text;
  v_n      int;
  v_size   double precision;
  v_buf    double precision;
  v_env    geometry;
  v_qenv   geometry;
  v_mvt    bytea;
  v_tile   bytea;
  v_count  int := 0;
begin
  select minzoom, maxzoom into v_minz, v_maxz
  from map_overlay_sections
  where key = p_section;
  if not found then
    raise exception 'map_overlay_render_section: unknown section %', p_section;
  end if;

  delete from map_overlay_tiles where section = p_section;

  select array_agg(key order by sort, key) into v_layers
  from map_overlay_layers
  where section = p_section;

  select st_xmin(e), st_ymin(e), st_xmax(e), st_ymax(e)
    into v_xmin, v_ymin, v_xmax, v_ymax
  from (
    select st_extent(f.geom) as e
    from map_overlay_features f
    join map_overlay_layers l on l.key = f.layer_key
    where l.section = p_section
  ) s;
  if v_xmin is null then
    return 0;
  end if;

  for v_z in v_minz .. v_maxz loop
    v_n := 1 << v_z;
    v_size := 2 * v_world / v_n;
    -- The range is widened by the 64/4096 buffer: a tile just outside the extent still
    -- carries the edge of a feature inside its buffer, as the && filter below selects it.
    v_buf := v_size * 64 / 4096;
    for v_x in greatest(0, floor((v_xmin - v_buf + v_world) / v_size)::int)
            .. least(v_n - 1, floor((v_xmax + v_buf + v_world) / v_size)::int) loop
      for v_y in greatest(0, floor((v_world - v_ymax - v_buf) / v_size)::int)
              .. least(v_n - 1, floor((v_world - v_ymin + v_buf) / v_size)::int) loop
        v_env := st_tileenvelope(v_z, v_x, v_y);
        v_qenv := st_tileenvelope(v_z, v_x, v_y, margin => 64.0 / 4096);
        v_tile := ''::bytea;
        foreach v_layer in array v_layers loop
          -- ST_AsMVTGeom returns NULL for a feature that clips away or collapses on the grid.
          select st_asmvt(q, v_layer, 4096, 'geom', 'id') into v_mvt
          from (
            select f.id, f.props, st_asmvtgeom(f.geom, v_env, 4096, 64, true) as geom
            from map_overlay_features f
            where f.layer_key = v_layer
              and f.geom && v_qenv
          ) q
          where q.geom is not null;
          if coalesce(octet_length(v_mvt), 0) > 0 then
            v_tile := v_tile || v_mvt;
          end if;
        end loop;
        if octet_length(v_tile) > 0 then
          insert into map_overlay_tiles (section, z, x, y, mvt)
          values (p_section, v_z, v_x, v_y, v_tile);
          v_count := v_count + 1;
        end if;
      end loop;
    end loop;
  end loop;

  return v_count;
end
$$;

comment on function public.map_overlay_render_section(text) is
  'Replaces a reference-overlay section''s tiles: for z in [minzoom, maxzoom], every tile over '
  'the section''s feature extent gets one MVT layer per layer key (extent 4096, buffer 64, '
  'feature id = map_overlay_features.id, attributes = props); empty tiles are not stored. '
  'Returns the stored tile count. Called by the ingest inside its per-section transaction.';

revoke execute on function public.map_overlay_render_section(text) from public, anon, authenticated;
grant execute on function public.map_overlay_render_section(text) to service_role;

-- Corollary E (migrations 437/440/441): the tiles are a precomputed artifact. The ingest
-- stamps this row (scraper.db.stamp_derived_artifact) after a clean run, so the Health
-- panel can tell a dead quarterly job from a quiet one. 100 days = one quarter + grace.
insert into public.derived_artifacts (name, producer, host, cadence, staleness_budget, is_serving)
values ('map_overlay_tiles', 'scripts/ingest_map_overlays.py', 'github-actions',
        '0 4 2 1,4,7,10 *', interval '100 days', true)
on conflict (name) do nothing;

notify pgrst, 'reload schema';

reset lock_timeout;
