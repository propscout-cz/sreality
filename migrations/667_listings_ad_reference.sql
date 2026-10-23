-- 667_listings_ad_reference.sql -- The advertiser's own number for the offer, as ONE typed column (operator request
-- 2026-10-23: "show in the Makléř section the ad id the broker advertises under, so I can quote it when I call; make
-- sure we scrape it correctly"). ADDITIVE: one nullable text column on listings, appended LAST to
-- property_sources_public; nothing dropped, rewritten or backfilled here.
--
-- WHAT. `listings.ad_reference` holds the number the ad PRINTS for callers: sreality's "ID zakázky" (API key
-- `advert_code`, blank on a private seller's ad), idnes's and remax's "Číslo zakázky" (`IDNES-00004`,
-- `ID 096-NP06026`), ceskereality's "ID nemovitosti", realitymix's "Číslo zakázky" (`REALITYMIX-N02161`, printed in
-- the price table its parser never read before), maxima's "ID zakázky" and mmreality's "Číslo nabídky" (both equal
-- the portal id, so the column repeats it there). It is read once per portal by the attribute contract
-- (scraper/attribute_contract.py, cell `ad_reference`; two IGNORED entries per portal became that cell) and
-- normalised by scraper.vocabulary.reference: whitespace collapsed, blank -> NULL, otherwise VERBATIM -- the
-- portal prefixes stay, because the broker reads the same string off the same page. NULL means the ad prints no
-- such number (bezrealitky, bazos and web print only their portal id) or the stored substrate cannot yield it.
--
-- NOT HASHED: identity-adjacent like source_url (a reference names the offer, it is not the offer's content), so
-- neither the heal (`reparse.yml --fields ad_reference`, every portal, stored pages / raw_json) nor the first live
-- write mints a snapshot (rule 2). NO INDEX: read by property through the ads view, never a filter or a sort key.
-- NOT exported into the autodedup artifact (autodedup/export_sql.py UNEXPORTED_COLUMNS): joining `attrs` would move
-- every stored decision digest mid-build.
--
-- property_sources_public: the live definition (migration 334) with the column appended last -- `create or
-- replace view` can only append, and no positional reader exists (the SPA selects PROPERTY_SOURCE_COLS by name,
-- api/routes/property_download.py by name). Same audience (authenticated; anon dark since 299), same data class
-- as source_url (a value the public ad page prints; not contact PII). The ACL is re-asserted rather than trusted.
--
-- View-only + one metadata-only ALTER, one short transaction: lock_timeout so it fails fast behind a reader
-- rather than queueing on the hot table (re-run; `if not exists` keeps it idempotent).
set lock_timeout = '5s';

alter table listings add column if not exists ad_reference text;

comment on column listings.ad_reference is
  'The advertiser''s own number for the offer, exactly as the ad prints it (ID zakázky / Číslo zakázky / '
  'ID nemovitosti / Číslo nabídky). Producer: scraper/attribute_contract.py cell ad_reference, normalised by '
  'scraper.vocabulary.reference (verbatim, whitespace collapsed). NULL = the ad prints no such number. '
  'Not in the content hash (migration 667).';

create or replace view property_sources_public as
 select property_id,
    sreality_id,
    source,
    source_url,
    source_id_native,
    is_active,
    price_czk,
    first_seen_at,
    last_seen_at,
    id,
    ad_reference
   from listings l
  where property_id is not null;

revoke all on property_sources_public from anon;
grant select on property_sources_public to authenticated;

-- PostgREST caches the schema; without this a fresh `select=ad_reference` 400s until its next reload.
notify pgrst, 'reload schema';

reset lock_timeout;
