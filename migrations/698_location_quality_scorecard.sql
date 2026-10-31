-- 698_location_quality_scorecard.sql -- W1: ONE location scorecard, every location quality number defined once.
--
-- WHY. Location data quality programme, wave W1 (ticket #2218; docs/design/location-quality/PROGRAM.md). Operator
-- rulings 2026-10-31: Q7, fixed red thresholds from day one and nothing measured twice; Q6, the town cell splits into
-- UNRESOLVED (town-bearing evidence, no town: red until zero) and PAGE-WITHHELD (no town-bearing claim at all: Watch);
-- Q3, strict containment by the authoritative polygon, no tolerance. Until now "share with a town" had three
-- definitions (the gauge's, the Location Quality page's, the audit waterfall's) and "street" four spellings, two of
-- them an enum-ordinal comparison.
--
-- WHAT. One view, public.location_quality_by_source, read by the location_town_coverage gauge (scripts/checks/
-- quality.py) every 6 h; the Location Quality page will render the gauge's stored cells, never this view. Six metrics:
--   (i)   completeness: town facts, the rule 25 broken set split by evidence (unresolved / withheld), KÚ by grain;
--   (ii)  consistency: positions outside the bound town's authoritative polygon, asked where CHECK flagged the pin;
--   (iii) the disputed-class mix;
--   (iv)  grain shares by the RANK table (never the enum order) and the one street share = (address + street) /
--         Czech rows with a town;
--   (v)   pin accuracy on a deterministic sample (listing id % 10 = 0): pin-to-truth distance per declared label,
--         the truth a word-bound RÚIAN address point of the same property, and radius cover;
--   (vi)  freshness: rows per resolver version, rows on a stale registry label, the resolve queue by class.
-- Cohorts: `listing` (active ads first written more than NEW_AD_H = 24 h ago: publishing owns the younger ones),
-- `served` (live, active, PUBLISHED properties, each at its LOCATION ad, rule 15: never the canonical ad), `sample`
-- and `queue`. LONG shape (cohort, source, metric, class, n, of_n, q50, q90, q95, q100): every row says what it
-- counts of what, a new class needs no column, and every UNION branch carries a LITERAL cohort, so the gauge reads
-- `where cohort <> 'sample'` and `where cohort = 'sample'` as two statements and the planner prunes the other
-- branches (One-Time Filter: false).
--
-- ONE DEFINITION. The flags are location_data.location_steps.step_flags_sql(listings='c') and town_missing_sql(),
-- the evidence probe town_evidence_sql(), the published rule toolkit.publication.published_predicate('p'), all
-- rendered VERBATIM; tests/location_data/test_location_quality_view_contract.py pins each against its Python.
--
-- PLAN. Every CTE but the three scalar lookups and the two single-use feeders (degenerate_ku_obce, cohort_rows) is
-- `as materialized`: with the single-reference CTEs inlined the same text ran 34.8 s on production against 11.7 s
-- materialized (2026-10-31), inside a 45 s check budget. The contract test pins the list.
--
-- GRANTS. service_role only: the browser never reads this view (default privileges hand `authenticated` SELECT on a
-- new relation, migration 299, so it is revoked here). security_invoker: the caller's privileges apply.
-- PostGIS functions are unqualified (production keeps PostGIS in `extensions`, CI replays it in `public`).
--
-- ADDITIVE: one new view, no row written. APPLY ORDER: before the PR merges (the worker's gauge reads it).
-- VERIFY: select cohort, metric, count(*) from location_quality_by_source where cohort <> 'sample' group by 1, 2;
--         select has_table_privilege('authenticated', 'public.location_quality_by_source', 'select');  -- false

set lock_timeout = '5s';

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
-- pip pieces tile it; resolve_db.CONTAINING_OBEC_SQL)? Asked only where CHECK flagged the elected pin: elsewhere the
-- position is a register point or a pin CHECK already found inside (a 1/50 sample of the rest: 4 of 7,333 outside,
-- register-point slivers). Read over the rows `placed_n` counts, so the count is a subset of its denominator.
outside as materialized (
  select b.cohort, b.source, count(*) as position_outside_n
    from b
   where b.disputed in ('pin_outside_obec', 'pin_outside_cz', 'country_conflict')
     and b.has_town and b.geom is not null
     and (select u.code
            from ruian_admin_unit_geometries g
            join ruian_admin_units u on u.id = g.unit_id
           where g.registry_version_id = (select id from cur) and g.purpose = 'pip'
             and u.level = 'obec' and st_covers(g.geom, b.geom)
           limit 1) is distinct from b.obec_kod
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
-- THE SAMPLE (pin accuracy): every tenth listing-cohort ad whose own row, or its property's location ad, is an address
-- point bound from words (a pin never yields one). The pin is its newest coordinate claim with a position.
smp as materialized (
  select l.id, l.source, ll.geom, ll.uncertainty_radius_m, gr.is_address_grain,
         case when ll.granularity = 'address_point' then ll.geom
              when tl.granularity = 'address_point' then tl.geom end as truth
    from listings l
    join listing_location ll on ll.listing_id = l.id
    join location_granularity_rank gr on gr.granularity = ll.granularity
    left join properties p on p.id = l.property_id and p.status = 'active'
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
  'W1 (migration 698): the ONE definition of every location quality number, long shape, cohorts listing/served/sample/queue. Read by the location_town_coverage gauge; service_role only; the SPA reads its stored cells.';

-- Default privileges hand authenticated SELECT on a new relation (migration 299): take it back.
revoke all on public.location_quality_by_source from public, anon, authenticated;
grant select on public.location_quality_by_source to service_role;
