-- 765_revert_location_w6c2_pin_audit_drop.sql
--
-- REVERT for 765_location_w6c2_pin_audit_drop.sql. SHIPPED UNAPPLIED. Lives in migrations/reverts/ because the CI
-- schema replay applies `ls migrations/*.sql | sort` (not recursive).
--
-- Everything here was read live on production on 2026-11-22 (W6-c2, #2361): the definition is
-- pg_get_viewdef('public.location_pin_audit_mv', true) verbatim (md5 95aea8b4e7313bfcea98155a0a8e09e1; migration
-- 524's text as Postgres renders it, 12,273 rows, 40 MB), the eight indexes are their pg_indexes.indexdef, the grants
-- are its relacl (postgres owner, authenticated SELECT, service_role ALL, no anon), the cron line is cron.job's
-- (719's command) and the registry row is derived_artifacts' declaration (its stamps refill on the first refresh).
-- The relation is derived: the CREATE fills it from listings and listing_location, no data is restored.
-- It reads listing_location.match_confidence; once W6-e drops that column, remove the column from both selects.

set lock_timeout = '5s';
set statement_timeout = '900s';

create materialized view if not exists public.location_pin_audit_mv as
 WITH cohort AS MATERIALIZED (
         SELECT l.id AS listing_id,
            l.property_id,
            l.sreality_id,
            l.source,
            l.source_id_native,
            l.source_url,
            l.category_main,
            l.category_type,
            l.disposition,
            l.area_m2,
            l.price_czk,
            l.is_active,
            l.first_seen_at,
            l.last_seen_at,
            location_display_label(ll.street_name, ll.house_number_cp, ll.house_number_co, ll.obec_name, ll.cast_obce_name, ll.country_code, ll.country_status) AS display_label,
            ll.country_status::text AS country_status,
            ll.granularity::text AS granularity,
            ll.match_confidence::text AS match_confidence,
            ll.resolver_version,
            ll.resolved_at,
            ll.listing_id IS NOT NULL AS has_row,
            ll.claim_set_hash IS NOT NULL AND ll.claim_set_hash <> sha256('\x5b5d'::bytea) AS has_claims
           FROM listings l
             LEFT JOIN listing_location ll ON ll.listing_id = l.id
          WHERE NOT (EXISTS ( SELECT 1
                   FROM listing_location sl
                  WHERE sl.listing_id = l.id AND (sl.geom IS NOT NULL OR sl.country_status = 'foreign'::country_status)))
        )
 SELECT c.listing_id,
    c.property_id,
    c.sreality_id,
    c.source,
    c.source_id_native,
    c.source_url,
    c.category_main,
    c.category_type,
    c.disposition,
    c.area_m2,
    c.price_czk,
    c.is_active,
    c.first_seen_at,
    c.last_seen_at,
    c.display_label,
    c.country_status,
    c.granularity,
    c.match_confidence,
    c.resolver_version,
    c.resolved_at,
    c.has_row,
    c.has_claims,
    (EXISTS ( SELECT 1
           FROM location_claims cl
             JOIN portal_contract_entries pce ON pce.id = cl.contract_entry_id
             JOIN portal_contracts pc ON pc.id = pce.contract_id
          WHERE cl.listing_id = c.listing_id AND pc.is_active)) AS claims_now,
    sib.found AS sibling_has_pin,
        CASE
            WHEN c.is_active AND NOT c.has_claims THEN 'active_no_claims'::text
            WHEN c.is_active THEN 'active_unresolved'::text
            WHEN NOT c.has_claims THEN 'delisted_no_claims'::text
            ELSE 'delisted_unresolved'::text
        END AS quality,
        CASE
            WHEN NOT c.has_row THEN 'pending'::text
            WHEN (EXISTS ( SELECT 1
               FROM dirty_locations d
              WHERE d.listing_id = c.listing_id)) THEN 'pending'::text
            WHEN snap.scraped_at > c.resolved_at THEN 'pending'::text
            ELSE 'unresolved'::text
        END AS state,
    now() AS refreshed_at
   FROM cohort c
     LEFT JOIN LATERAL ( SELECT (EXISTS ( SELECT 1
                   FROM listings sib_1
                     JOIN listing_location sll ON sll.listing_id = sib_1.id
                  WHERE sib_1.property_id = c.property_id AND sib_1.id <> c.listing_id AND sll.geom IS NOT NULL)) AS found) sib ON true
     LEFT JOIN LATERAL ( SELECT s.scraped_at
           FROM listing_snapshots s
          WHERE s.listing_id = c.listing_id
          ORDER BY s.scraped_at DESC
         LIMIT 1) snap ON true;

create unique index if not exists location_pin_audit_mv_pk on public.location_pin_audit_mv using btree (listing_id);
create index if not exists location_pin_audit_mv_category on public.location_pin_audit_mv using btree (category_main);
create index if not exists location_pin_audit_mv_first_seen on public.location_pin_audit_mv using btree (first_seen_at, listing_id);
create index if not exists location_pin_audit_mv_last_seen on public.location_pin_audit_mv using btree (last_seen_at, listing_id);
create index if not exists location_pin_audit_mv_quality on public.location_pin_audit_mv using btree (quality);
create index if not exists location_pin_audit_mv_sibling on public.location_pin_audit_mv using btree (listing_id) where sibling_has_pin;
create index if not exists location_pin_audit_mv_source on public.location_pin_audit_mv using btree (source);
create index if not exists location_pin_audit_mv_state on public.location_pin_audit_mv using btree (state);

revoke all on public.location_pin_audit_mv from anon, authenticated;
grant select on public.location_pin_audit_mv to authenticated;
grant all on public.location_pin_audit_mv to service_role;

insert into public.derived_artifacts (name, producer, host, cadence, staleness_budget, is_serving)
values ('location_pin_audit_mv', 'refresh_matview', 'pg_cron', '25 * * * *', interval '02:10:00', true)
on conflict (name) do nothing;

do $cron$
begin
  if to_regclass('cron.job') is null then
    raise notice '765 revert: no pg_cron here; refresh-location-pin-audit not scheduled';
  else
    perform cron.schedule(
      'refresh-location-pin-audit', '25 * * * *',
      $$set statement_timeout='900s'; select public.refresh_matview('location_pin_audit_mv');$$
    );
  end if;
end
$cron$;

reset statement_timeout;
reset lock_timeout;
