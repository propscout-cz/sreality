-- 733_location_w6c_position_audit.sql -- W6-c1: where every ad's point came from, counted once and listed.
--
-- WHY. Location data quality programme, wave W6 (ticket #2361; docs/design/location-quality/PROGRAM.md). Operator
-- rulings 2026-11-02: Q2, the pin audit page and its matview go only once the operator has a new, current view of the
-- database's location quality (this one; its line count does not count against rule 25); Q3, an ad below address
-- level on a portal pin keeps its pin and reads as approximate, and the audit view shows that with enough breakdown.
-- 2026-11-01: no radius anywhere. Q7: one scorecard, nothing measured twice. The old pin audit page, its matview and
-- its cron STAY until the operator has compared the two (W6-c2 deletes them).
--
-- WHAT. Additive, three objects and one restated view:
--   1. location_position_mv, LISTING grain, refreshed hourly by pg_cron through the chokepoint refresh_matview (578)
--      at :07 (the old job keeps :25). Cohort: every active ad, plus every ad in any status the consumer rule refuses
--      (claims_common.SERVED_LOCATION_PREDICATE, verbatim, negated): the old page's rows are this one's `none` rows.
--      Each row carries its POSITION KIND, decided by geometric identity (location_steps.position_kind_sql, verbatim):
--      door, street_point, own_pin, part_point, town_point, unit_point, foreign, none, else `unexplained` (a finding,
--      shown, not judged); its GRAIN class (location_steps.position_grain_sql, verbatim, the scorecard's four classes
--      first); and, on `none` rows only, the town evidence (town_evidence_sql, verbatim: unresolved vs withheld), a
--      sibling ad of the same property that has a point, and a queued re-resolve.
--   2. location_position_audit_public, the list the Location Quality page reads, behind the 619/687 admin gate
--      (`) __admin_gate where is_platform_admin()`), granted to authenticated only. Its rows are LIVE listings: the
--      scorecard's listing cohort (`in_reading`, its edge verbatim) and every row the matview holds. A row carries the
--      READING's kind (location_steps.position_reading_kind_sql: the matview's kind while resolved_at is unchanged,
--      else `unclassified`) and its live grain, exactly as the `position` metric counts them, so a grid cell's list
--      differs from its number only by churn since the reading; `snapshot_kind` is the matview's own (the every-status
--      no-answer entry), the none-row facts are the matview's, the property and the label are live.
--   3. location_quality_by_source restated (rule 1: in full, 698's text) with one more metric, `position` (class
--      `kind/grain`) over the listing cohort: the matview's kind while the row's answer is the one it classified
--      (resolved_at unchanged), else `unclassified`, by the row's LIVE grain, so the grid's column totals are exactly
--      the `grain` counts. `b` gains two columns (granularity, resolved_at); every other metric is byte-identical.
--
-- PLAN. The kind costs one location_claims key probe per row the door and the street left open (6 M rows, 3.6 GB):
-- the pin is fetched by location_claims_key behind OFFSET 0 and compared outside, because a pin predicate the planner
-- serves from location_claims_geom (GiST) took a 59,768-row slice from 5.2 s to 50 s. Street and část points are the
-- centroids of their valid doors (resolve_db _STREET_POINT_SQL / _PART_POINT_SQL), computed once for the streets and
-- parts the cohort names (23k + 10k). Measured on production 2026-11-05/06: the classification of all active ads took
-- 48 s warm in one statement; per portal, sreality 109k rows 50.4 s (45,952 door probes, cold), idnes 113k rows
-- 13.8 s, maxima 0.3k rows 7.4 s (the fixed centroid cost). The build may take a few minutes, hence the
-- statement_timeout below; the hourly CONCURRENTLY refresh runs under the cron command's own 900 s. The list view
-- reads one portal's live cohort (112k rows at most: its edge and live grain 0.5 s on idnes) per count or page, or the
-- matview's ~12k no-answer rows through location_position_mv_cell.
--
-- GRANTS. The matview is service_role only (ADMIN_ONLY_RELATIONS; a matview cannot embed the gate). The scorecard view
-- stays service_role only and security_invoker. The list view runs as its owner behind the admin gate.
--
-- ADDITIVE. Apply through apply_migration.yml (psql -f, statement autocommit): every statement is idempotent, so a
-- lock_timeout retry re-runs the file from statement 1. Not at minute :25 (the old refresh) nor :07.
-- VERIFY:
--   select position_kind, count(*) from location_position_mv group by 1 order by 2 desc;   -- unexplained 0 expected
--   select class, sum(n) from location_quality_by_source where cohort = 'listing' and metric = 'position' group by 1;
--   select has_table_privilege('authenticated', 'public.location_position_mv', 'select');  -- false
--   select has_table_privilege('authenticated', 'public.location_position_audit_public', 'select'); -- true
--   select schedule, command from cron.job where jobname = 'refresh-location-position';
--   then after the next :07, derived_artifacts.last_succeeded_at for location_position_mv moves.

set lock_timeout = '5s';
set statement_timeout = '600s';

create materialized view if not exists public.location_position_mv as
with
obec_rank as (select r.rank from location_granularity_rank r where r.granularity = 'obec'),
street_rank as (select r.rank from location_granularity_rank r where r.granularity = 'street'),
cur as (select id from registry_versions where is_current),
-- Every active ad, then every other ad the consumer rule refuses, any status (one hash anti-join each).
cohort as materialized (
  select l.id, l.property_id, l.source, l.is_active, l.first_seen_at, l.last_seen_at, l.category_main
    from listings l
   where l.is_active
  union all
  select l.id, l.property_id, l.source, l.is_active, l.first_seen_at, l.last_seen_at, l.category_main
    from listings l
   where not l.is_active
     and not EXISTS (SELECT 1 FROM listing_location sl WHERE sl.listing_id = l.id AND (sl.geom IS NOT NULL OR sl.country_status = 'foreign'))),
-- Each member with its answer row and the flags the grain class reads (has_town: location_steps.step_flags_sql).
c as materialized (
  select co.id as listing_id, co.property_id, co.source, co.is_active, co.first_seen_at, co.last_seen_at,
         co.category_main, ll.geom, ll.granularity, gr.rank, gr.is_address_grain, ll.country_status,
         ll.resolved_at, ll.registry_version, ll.ruian_adm_kod, ll.ulice_kod, ll.cast_obce_kod, ll.obec_kod,
         ll.katastr_kod, ll.okres_kod, ll.kraj_kod,
         (ll.obec_kod is not null and gr.rank >= (SELECT r.rank FROM location_granularity_rank r WHERE r.granularity = 'obec')) as has_town
    from cohort co
    left join listing_location ll on ll.listing_id = co.id
    left join location_granularity_rank gr on gr.granularity = ll.granularity),
-- WHERE A STREET IS: the centroid of its valid doors (resolve_db._STREET_POINT_SQL), for the streets a doorless row names.
streets as materialized (
  select s.code, ST_Centroid(ST_Collect(ap.geom)) as pt
    from ruian_streets s
    join ruian_address_points ap on ap.street_id = s.id and ap.valid_to is null and ap.geom is not null
   where s.valid_to is null
     and s.code in (select c.ulice_kod from c where c.geom is not null and c.ruian_adm_kod is null)
   group by s.code),
-- WHERE A ČÁST OBCE IS: the same over its doors (resolve_db._PART_POINT_SQL); RÚIAN draws it no polygon.
parts as materialized (
  select u.code, ST_Centroid(ST_Collect(ap.geom)) as pt
    from ruian_admin_units u
    join ruian_address_points ap on ap.cast_obce_unit_id = u.id and ap.valid_to is null and ap.geom is not null
   where u.level = 'cast_obce' and u.valid_to is null
     and u.code in (select c.cast_obce_kod from c where c.geom is not null)
   group by u.code),
-- A unit's point is its authoritative polygon's representative point at the current registry (FILL's chain read),
-- one per code as the chain by code picks it (the newest valid unit), so no join below can double a listing.
units as materialized (
  select distinct on (u.level, u.code) u.level::text as level, u.code, g.representative_point as pt
    from ruian_admin_units u
    join ruian_admin_unit_geometries g on g.unit_id = u.id and g.purpose = 'authoritative'
                                      and g.registry_version_id = (select id from cur)
   where u.level in ('obec', 'katastralni_uzemi', 'okres', 'kraj') and u.valid_to is null
   order by u.level, u.code, u.valid_from desc),
p as (
  select c.*, st.pt as street_pt, pa.pt as part_pt, ob.pt as town_pt, ku.pt as katastr_pt, ok.pt as okres_pt,
         kr.pt as kraj_pt
    from c
    left join streets st on st.code = c.ulice_kod
    left join parts pa on pa.code = c.cast_obce_kod
    left join units ob on ob.level = 'obec' and ob.code = c.obec_kod
    left join units ku on ku.level = 'katastralni_uzemi' and ku.code = c.katastr_kod
    left join units ok on ok.level = 'okres' and ok.code = c.okres_kod
    left join units kr on kr.level = 'kraj' and kr.code = c.kraj_kod),
-- THE KIND, location_steps.position_kind_sql('p') verbatim.
b as (
  select p.*,
         CASE WHEN p.geom IS NULL AND p.country_status = 'foreign' THEN 'foreign'
              WHEN p.geom IS NULL THEN 'none'
              WHEN p.ruian_adm_kod IS NOT NULL AND ST_DWithin(p.geom, (SELECT ap.geom FROM ruian_address_points ap WHERE ap.kod_adm = p.ruian_adm_kod), 1e-7) THEN 'door'
              WHEN p.ruian_adm_kod IS NULL AND ST_DWithin(p.geom, p.street_pt, 1e-7) THEN 'street_point'
              WHEN (SELECT bool_or(ST_DWithin(k.value_geom, p.geom, 1e-7)) FROM (SELECT c.value_geom FROM location_claims c WHERE c.listing_id = p.listing_id AND c.claim_type = 'coordinate' OFFSET 0) k) THEN 'own_pin'
              WHEN ST_DWithin(p.geom, p.part_pt, 1e-7) THEN 'part_point'
              WHEN ST_DWithin(p.geom, p.town_pt, 1e-7) THEN 'town_point'
              WHEN ST_DWithin(p.geom, p.katastr_pt, 1e-7) OR ST_DWithin(p.geom, p.okres_pt, 1e-7) OR ST_DWithin(p.geom, p.kraj_pt, 1e-7) THEN 'unit_point'
              ELSE 'unexplained' END as position_kind
    from p)
select b.listing_id, b.source, b.is_active, b.first_seen_at, b.last_seen_at, b.category_main,
       b.granularity::text as granularity, b.rank as granularity_rank,
       -- THE GRAIN CLASS, location_steps.position_grain_sql('b') verbatim.
       CASE WHEN b.has_town and b.country_status = 'cz' and b.is_address_grain THEN 'address'
            WHEN b.has_town and b.country_status = 'cz' and b.rank >= (select rank from street_rank) and not b.is_address_grain THEN 'street'
            WHEN b.has_town and b.country_status = 'cz' and b.rank > (select rank from obec_rank) and b.rank < (select rank from street_rank) THEN 'quarter'
            WHEN b.has_town and b.country_status = 'cz' and b.rank = (select rank from obec_rank) THEN 'obec'
            WHEN b.rank >= (select rank from obec_rank) THEN 'other'
            ELSE coalesce(b.granularity::text, 'unknown') END as grain,
       b.position_kind, b.country_status::text as country_status, b.resolved_at, b.registry_version,
       -- On `none` rows only: rule 25's evidence split (location_steps.town_evidence_sql verbatim), a sibling ad of
       -- the same property with a point, and a re-resolve already queued.
       case when b.position_kind = 'none' then EXISTS (SELECT 1 FROM location_claims ec WHERE ec.listing_id = b.listing_id AND ec.claim_type IN ('obec_name', 'cast_obce_name', 'psc', 'address_point_id', 'coordinate') AND ec.subject_scoped IS NOT FALSE AND (ec.claim_type <> 'coordinate' OR ec.value_geom IS NOT NULL)) end as town_evidence,
       case when b.position_kind = 'none' then exists (
              select 1 from listings sib join listing_location sll on sll.listing_id = sib.id
               where sib.property_id = b.property_id and sib.id <> b.listing_id and sll.geom is not null) end
         as sibling_placed,
       case when b.position_kind = 'none' then exists (
              select 1 from dirty_locations d where d.listing_id = b.listing_id) end as queued,
       now() as refreshed_at
  from b;

-- REFRESH ... CONCURRENTLY needs one plain unique index: listing grain, so the listing id.
create unique index if not exists location_position_mv_pk on public.location_position_mv (listing_id);
-- A kind's rows (the every-status no-answer entry reads `none` through it).
create index if not exists location_position_mv_cell
  on public.location_position_mv (position_kind, source, grain, listing_id);

-- Default privileges hand authenticated SELECT on a new relation (migration 299): take it back.
revoke all on public.location_position_mv from public, anon, authenticated;
grant select on public.location_position_mv to service_role;

insert into public.derived_artifacts (name, producer, host, cadence, staleness_budget, is_serving)
values ('location_position_mv', 'refresh_matview', 'pg_cron', '7 * * * *', interval '130 minutes', true)
on conflict (name) do nothing;

do $cron$
begin
  perform cron.schedule(
    'refresh-location-position',
    '7 * * * *',
    $$set statement_timeout='900s'; select public.refresh_matview('location_position_mv');$$
  );
exception when invalid_schema_name then
  raise notice 'pg_cron unavailable; refresh-location-position not scheduled (%)', sqlerrm;
end
$cron$;

-- THE LIST (header item 2). Owner rights behind the admin gate. A grid cell's list filters `in_reading` and the
-- reading's kind and grain, which the planner computes over that portal's live cohort (no index serves the kind rule);
-- the no-answer entry filters `snapshot_kind = 'none'`, which turns the matview join inner and reads its kind index.
create or replace view public.location_position_audit_public as
with
obec_rank as (select r.rank from location_granularity_rank r where r.granularity = 'obec'),
street_rank as (select r.rank from location_granularity_rank r where r.granularity = 'street')
select * from (
  select b.listing_id, b.property_id, b.source, b.is_active, b.first_seen_at, b.last_seen_at, b.category_main,
         b.category_type, b.disposition, b.area_m2, b.price_czk, b.source_url,
         location_display_label(b.street_name, b.house_number_cp, b.house_number_co,
                                b.obec_name, b.cast_obce_name, b.country_code,
                                b.country_status) as display_label,
         b.granularity::text as granularity, b.rank as granularity_rank,
         -- THE READING: location_steps.position_grain_sql('b') and position_reading_kind_sql('b'), verbatim.
         CASE WHEN b.has_town and b.country_status = 'cz' and b.is_address_grain THEN 'address'
              WHEN b.has_town and b.country_status = 'cz' and b.rank >= (select rank from street_rank) and not b.is_address_grain THEN 'street'
              WHEN b.has_town and b.country_status = 'cz' and b.rank > (select rank from obec_rank) and b.rank < (select rank from street_rank) THEN 'quarter'
              WHEN b.has_town and b.country_status = 'cz' and b.rank = (select rank from obec_rank) THEN 'obec'
              WHEN b.rank >= (select rank from obec_rank) THEN 'other'
              ELSE coalesce(b.granularity::text, 'unknown') END as grain,
         coalesce(case when m.resolved_at is not distinct from b.resolved_at then m.position_kind end, 'unclassified') as position_kind,
         m.position_kind as snapshot_kind, b.in_reading,
         b.country_status::text as country_status, b.resolved_at,
         m.town_evidence, m.sibling_placed, m.queued, m.refreshed_at
    from (
      select l.id as listing_id, l.property_id, l.source, l.is_active, l.first_seen_at, l.last_seen_at,
             l.category_main, l.category_type, l.disposition, l.area_m2, l.price_czk, l.source_url,
             -- The scorecard's listing cohort, its edge verbatim (scripts/checks/common.py NEW_AD_H).
             (l.is_active and l.first_seen_at < now() - interval '24 hours') as in_reading,
             ll.street_name, ll.house_number_cp, ll.house_number_co, ll.obec_name, ll.cast_obce_name,
             ll.country_code, ll.country_status, ll.granularity, ll.resolved_at, gr.rank, gr.is_address_grain,
             (ll.obec_kod is not null and gr.rank >= (SELECT r.rank FROM location_granularity_rank r WHERE r.granularity = 'obec')) as has_town
        from listings l
        left join listing_location ll on ll.listing_id = l.id
        left join location_granularity_rank gr on gr.granularity = ll.granularity) b
    left join location_position_mv m on m.listing_id = b.listing_id
   where b.in_reading or m.listing_id is not null
) __admin_gate
where is_platform_admin();

revoke all on public.location_position_audit_public from public, anon, authenticated;
grant select on public.location_position_audit_public to authenticated;

-- THE SCORECARD, 698's text with the `position` metric (header item 3).
create or replace view public.location_quality_by_source
with (security_invoker = true) as
with
obec_rank as (select r.rank from location_granularity_rank r where r.granularity = 'obec'),
street_rank as (select r.rank from location_granularity_rank r where r.granularity = 'street'),
cur as (select id, label from registry_versions where is_current),
-- The KÚ invariant's one excuse, moved verbatim from quality.py: an obec holding a KÚ the boundary loader recorded as
-- `degenerate_boundary_geometry` at the current version has a hole no address point can be placed in.
degenerate_ku_obce as (
  select o.code
    from registry_load_discrepancies d
    join ruian_admin_units k on k.level = 'katastralni_uzemi' and k.code = d.entity_code
                            and k.valid_to is null
    join ruian_admin_units o on o.id = k.parent_id
   where d.entity_kind = 'katastralni_uzemi'
     and d.discrepancy = 'degenerate_boundary_geometry'
     and d.registry_version_id = (select id from cur)),
-- THE TWO COHORTS. listing: active ads first written more than 24 h ago (scripts/checks/common.py NEW_AD_H).
-- served: live, active, published properties (toolkit/publication.py), each at its LOCATION ad.
cohort_rows as (
  select 'listing'::text as cohort, l.id, l.source
    from listings l
   where l.is_active and l.first_seen_at < now() - interval '24 hours'
  union all
  select 'served'::text, l.id, l.source
    from properties p
    join listings l on l.id = p.location_listing_ref_id
   where p.status = 'active' and p.is_active
     and coalesce(p.settled_at, p.first_seen_at + interval '6 hours') <= now()),
-- One row per cohort member with its answer row, cut by the shared step flags (location_steps, verbatim).
b as materialized (
  select c.cohort, c.id, c.source,
         ll.geom, ll.obec_kod, ll.katastr_kod, ll.country_status, ll.disputed,
         ll.resolver_version, ll.registry_version, gr.rank, gr.is_address_grain,
         ll.granularity, ll.resolved_at,
         (ll.listing_id is not null) as has_row,
         EXISTS (SELECT 1 FROM listing_location sl WHERE sl.listing_id = c.id AND (sl.geom IS NOT NULL OR sl.country_status = 'foreign')) as located,
         (ll.country_status = 'foreign') as is_foreign,
         (ll.obec_kod is not null and gr.rank >= (SELECT r.rank FROM location_granularity_rank r WHERE r.granularity = 'obec')) as has_town,
         (not coalesce((ll.country_status = 'foreign'), false) and not coalesce((ll.obec_kod is not null and gr.rank >= (SELECT r.rank FROM location_granularity_rank r WHERE r.granularity = 'obec')), false)) as town_missing
    from cohort_rows c
    left join listing_location ll on ll.listing_id = c.id
    left join location_granularity_rank gr on gr.granularity = ll.granularity),
agg as materialized (
  select b.cohort, b.source,
    count(*)                                                                       as ads,
    count(*) filter (where b.is_foreign)                                           as foreign_n,
    count(*) filter (where b.has_town and not coalesce(b.is_foreign, false))       as town_n,
    count(*) filter (where not b.has_row)                                          as no_row_n,
    count(*) filter (where not b.located)                                          as hidden_n,
    count(*) filter (where b.has_town and b.country_status = 'cz' and b.is_address_grain)          as g_address,
    count(*) filter (where b.has_town and b.country_status = 'cz' and b.rank >= (select rank from street_rank) and not b.is_address_grain) as g_street,
    count(*) filter (where b.has_town and b.country_status = 'cz' and b.rank > (select rank from obec_rank) and b.rank < (select rank from street_rank)) as g_quarter,
    count(*) filter (where b.has_town and b.country_status = 'cz' and b.rank = (select rank from obec_rank)) as g_obec,
    count(*) filter (where b.has_town and b.country_status = 'cz' and b.is_address_grain and b.katastr_kod is not null) as ku_address,
    count(*) filter (where b.has_town and b.country_status = 'cz' and b.rank >= (select rank from street_rank) and not b.is_address_grain and b.katastr_kod is not null) as ku_street,
    count(*) filter (where b.has_town and b.country_status = 'cz' and b.rank > (select rank from obec_rank) and b.rank < (select rank from street_rank) and b.katastr_kod is not null) as ku_quarter,
    count(*) filter (where b.has_town and b.country_status = 'cz' and b.rank = (select rank from obec_rank) and b.katastr_kod is not null) as ku_obec,
    count(*) filter (where b.disputed = 'pin_outside_obec')                        as d_pin_outside_obec,
    count(*) filter (where b.disputed = 'pin_off_street')                          as d_pin_off_street,
    count(*) filter (where b.disputed = 'country_conflict')                        as d_country_conflict,
    count(*) filter (where b.disputed = 'pin_outside_cz')                          as d_pin_outside_cz,
    count(*) filter (where b.has_town and b.geom is not null)                      as placed_n
  from b group by b.cohort, b.source),
-- Rule 25's broken set (town_missing_sql), split by EVIDENCE (ruling Q6): unresolved = the listing holds a
-- town-bearing claim (location_steps.town_evidence_sql, verbatim); withheld = it holds none, with or without a row.
townless as materialized (
  select b.cohort, b.source,
         count(*) filter (where ev.found)     as unresolved_n,
         count(*) filter (where not ev.found) as withheld_n
    from b
    cross join lateral (select EXISTS (SELECT 1 FROM location_claims ec WHERE ec.listing_id = b.id AND ec.claim_type IN ('obec_name', 'cast_obce_name', 'psc', 'address_point_id', 'coordinate') AND ec.subject_scoped IS NOT FALSE AND (ec.claim_type <> 'coordinate' OR ec.value_geom IS NOT NULL)) as found) ev
   where b.town_missing
   group by b.cohort, b.source),
-- Strict containment (ruling Q3, no tolerance): is the POSITION inside the bound town's authoritative polygon (the
-- pip pieces tile it)? The probe is the one containing-obec statement, resolve_db.CONTAINING_OBEC_SQL's FROM ... LIMIT 1
-- block, verbatim but for its version parameter and its point (pinned by the contract test). Asked only where CHECK
-- flagged the elected pin: elsewhere the position is a register point or a pin CHECK already found inside (a 1/50
-- sample of the rest: 4 of 7,333 outside, register-point slivers). Read over the rows `placed_n` counts, so the count
-- is a subset of its denominator.
outside as materialized (
  select b.cohort, b.source, count(*) as position_outside_n
    from b
   where b.disputed in ('pin_outside_obec', 'pin_outside_cz', 'country_conflict')
     and b.has_town and b.geom is not null
     and (SELECT u.code
            FROM ruian_admin_unit_geometries g
            JOIN ruian_admin_units u ON u.id = g.unit_id
           WHERE g.registry_version_id = (select id from cur)
             AND g.purpose = 'pip'
             AND u.level = 'obec'
             AND ST_Covers(g.geom, b.geom)
           LIMIT 1) is distinct from b.obec_kod
   group by b.cohort, b.source),
-- Freshness and the KÚ invariant per resolver version (the gauge picks the version the code runs). The per-source total
-- is a window HERE, never in a UNION branch: a window function in a branch blocks the cohort qual's pushdown.
versions as materialized (
  select b.source, b.resolver_version,
         count(*) as n,
         (sum(count(*)) over (partition by b.source))::bigint as source_n,
         count(*) filter (where b.registry_version is distinct from (select label from cur)) as stale_registry_n,
         count(*) filter (where b.country_status = 'cz' and b.is_address_grain and b.obec_kod is not null) as ku_scope_n,
         count(*) filter (where b.country_status = 'cz' and b.is_address_grain and b.katastr_kod is null
                            and b.obec_kod is not null
                            and b.obec_kod not in (select code from degenerate_ku_obce)) as ku_gap_n
    from b where b.cohort = 'listing' and b.has_row
   group by b.source, b.resolver_version),
-- W6-c: where each listing-cohort ad's point came from. The kind is location_position_mv's (hourly) while the row's
-- answer is the one it classified, else `unclassified` (location_steps.position_reading_kind_sql, verbatim; the list
-- view location_position_audit_public reads the same rule); the grain is the row's LIVE grain class
-- (location_steps.position_grain_sql, verbatim), whose first four classes are `agg`'s grain FILTERs, so the classes of
-- one grain sum to its `grain` count. Class `kind/grain`; of_n the source's cohort.
positions as materialized (
  select b.source,
         coalesce(case when m.resolved_at is not distinct from b.resolved_at then m.position_kind end, 'unclassified')
           || '/' ||
         CASE WHEN b.has_town and b.country_status = 'cz' and b.is_address_grain THEN 'address'
              WHEN b.has_town and b.country_status = 'cz' and b.rank >= (select rank from street_rank) and not b.is_address_grain THEN 'street'
              WHEN b.has_town and b.country_status = 'cz' and b.rank > (select rank from obec_rank) and b.rank < (select rank from street_rank) THEN 'quarter'
              WHEN b.has_town and b.country_status = 'cz' and b.rank = (select rank from obec_rank) THEN 'obec'
              WHEN b.rank >= (select rank from obec_rank) THEN 'other'
              ELSE coalesce(b.granularity::text, 'unknown') END as class,
         count(*) as n,
         (sum(count(*)) over (partition by b.source))::bigint as source_n
    from b
    left join location_position_mv m on m.listing_id = b.id
   where b.cohort = 'listing'
   group by 1, 2),
stock as materialized (
  select a.cohort, a.source, v.metric, v.class, v.n, v.of_n
    from agg a
    left join townless t on t.cohort = a.cohort and t.source = a.source
    left join outside  o on o.cohort = a.cohort and o.source = a.source
    cross join lateral (values
      ('town',     'ads',              a.ads,                             a.ads),
      ('town',     'foreign',          a.foreign_n,                       a.ads),
      ('town',     'has_town',         a.town_n,                          a.ads - a.foreign_n),
      ('town',     'no_row',           a.no_row_n,                        a.ads),
      ('town',     'unresolved',       coalesce(t.unresolved_n, 0),       a.ads - a.foreign_n),
      ('town',     'withheld',         coalesce(t.withheld_n, 0),         a.ads - a.foreign_n),
      ('town',     'hidden',           a.hidden_n,                        a.ads),
      ('ku',       'address',          a.ku_address,                      a.g_address),
      ('ku',       'street',           a.ku_street,                       a.g_street),
      ('ku',       'quarter',          a.ku_quarter,                      a.g_quarter),
      ('ku',       'obec',             a.ku_obec,                         a.g_obec),
      ('outside',  'position',         coalesce(o.position_outside_n, 0), a.placed_n),
      ('disputed', 'pin_outside_obec', a.d_pin_outside_obec,              a.ads),
      ('disputed', 'pin_off_street',   a.d_pin_off_street,                a.ads),
      ('disputed', 'country_conflict', a.d_country_conflict,              a.ads),
      ('disputed', 'pin_outside_cz',   a.d_pin_outside_cz,                a.ads),
      ('grain',    'address',          a.g_address,                       a.g_address + a.g_street + a.g_quarter + a.g_obec),
      ('grain',    'street',           a.g_street,                        a.g_address + a.g_street + a.g_quarter + a.g_obec),
      ('grain',    'quarter',          a.g_quarter,                       a.g_address + a.g_street + a.g_quarter + a.g_obec),
      ('grain',    'obec',             a.g_obec,                          a.g_address + a.g_street + a.g_quarter + a.g_obec)
    ) v(metric, class, n, of_n)),
-- THE SAMPLE (pin accuracy): every tenth listing-cohort ad whose own row, or its live property's location ad (the
-- served cohort's property filter), is an address point bound from words (a pin never yields one). The pin is its
-- newest coordinate claim with a position.
smp as materialized (
  select l.id, l.source, ll.geom, ll.uncertainty_radius_m, gr.is_address_grain,
         case when ll.granularity = 'address_point' then ll.geom
              when tl.granularity = 'address_point' then tl.geom end as truth
    from listings l
    join listing_location ll on ll.listing_id = l.id
    join location_granularity_rank gr on gr.granularity = ll.granularity
    left join properties p on p.id = l.property_id and p.status = 'active' and p.is_active
    left join listing_location tl on tl.listing_id = p.location_listing_ref_id
   where l.is_active and l.first_seen_at < now() - interval '24 hours' and l.id % 10 = 0),
pin as materialized (
  select s.source,
         st_distance(c.value_geom::geography, s.truth::geography) as err_m,
         coalesce((select d.declared_precision_label from location_claims d
                    where d.listing_id = s.id and d.claim_type = 'precision_declaration'
                    order by d.first_observed_at desc, d.id desc limit 1), 'undeclared') as label
    from smp s
    cross join lateral (select c.value_geom from location_claims c
                         where c.listing_id = s.id and c.claim_type = 'coordinate'
                           and c.value_geom is not null
                         order by c.first_observed_at desc, c.id desc limit 1) c
   where s.truth is not null)
select 'listing'::text as cohort, s.source, s.metric, s.class, s.n, s.of_n,
       null::numeric as q50, null::numeric as q90, null::numeric as q95, null::numeric as q100
  from stock s where s.cohort = 'listing'
union all
select 'served'::text, s.source, s.metric, s.class, s.n, s.of_n, null, null, null, null
  from stock s where s.cohort = 'served'
union all
select 'listing'::text, v.source, 'version', v.resolver_version, v.n, v.source_n, null, null, null, null
  from versions v
union all
select 'listing'::text, v.source, 'registry_stale', v.resolver_version, v.stale_registry_n, v.n,
       null, null, null, null
  from versions v
union all
select 'listing'::text, v.source, 'ku_gap', v.resolver_version, v.ku_gap_n, v.ku_scope_n, null, null, null, null
  from versions v
union all
select 'listing'::text, p.source, 'position', p.class, p.n, p.source_n, null, null, null, null
  from positions p
union all
select 'sample'::text, p.source, 'pin_error_m', p.label, count(*), null,
       percentile_cont(0.5)  within group (order by p.err_m)::numeric,
       percentile_cont(0.9)  within group (order by p.err_m)::numeric,
       percentile_cont(0.95) within group (order by p.err_m)::numeric,
       max(p.err_m)::numeric
  from pin p group by p.source, p.label
union all
select 'sample'::text, s.source, 'radius_cover', 'below_address',
       count(*) filter (where st_distance(s.geom::geography, s.truth::geography) <= s.uncertainty_radius_m),
       count(*), null, null, null, null
  from smp s where s.truth is not null and s.geom is not null and not s.is_address_grain
 group by s.source
union all
select 'queue'::text, null::text, 'backlog_s',
       case when d.reason in ('claim_insert', 'operator_edit') then 'evidence' else 'backfill' end,
       count(*), count(*) filter (where d.attempts > 0),
       percentile_cont(0.5) within group (order by extract(epoch from now() - d.enqueued_at))::numeric,
       null, null,
       max(extract(epoch from now() - d.enqueued_at))::numeric
  from dirty_locations d
 group by 4;

comment on view public.location_quality_by_source is
  'W1 (migration 698), W6-c (733: the position metric): the ONE definition of every location quality number, long shape, cohorts listing/served/sample/queue. Read by the location_town_coverage gauge; service_role only; the SPA reads its stored cells.';

-- Default privileges hand authenticated SELECT on a new relation (migration 299): take it back.
revoke all on public.location_quality_by_source from public, anon, authenticated;
grant select on public.location_quality_by_source to service_role;
