-- 670_repair_colliding_image_storage_paths.sql -- every image row whose R2 object another row overwrote goes back to the download queue.
--
-- APPLY AFTER DEPLOY; OPERATOR OK. Apply only once the code that mints `img/{listing_id}/{images.id}.jpg`
-- (scraper/image_storage.image_key) is live on Railway AND on the GitHub image workflows. This file puts rows back
-- in the download queue, and two drains take them within minutes (`images_fresh.yml` on */15 plus its self-chain, and
-- the realtime worker's images lane): under the old code each would upload straight back into the colliding key and
-- overwrite the photo this file leaves in place. A run before deploy is not fatal -- run the file again after deploy.
--
-- THE BUG. A listing photo's R2 key was `{sreality_id}/{seq:04d}.jpg` before Gate 2 and `{listings.id}/{seq:04d}.jpg`
-- after it. Both numbers come from one range, so when a new listing's id equals an older listing's sreality_id, the new
-- listing mints the older listing's keys and its upload silently overwrites that listing's photo. Both rows then point at
-- one object: one of them renders another property's photo and keeps a pHash and CLIP vector of bytes it no longer has.
-- Closed PR #959 found 16 such keys on 2026-08-05 and its migration 371 was never applied. Counted 2026-10-25 (bounded
-- read, 0.09 s): 70 listing pairs where one listing's id is the other's sreality_id, 152 keys held by two rows each (304
-- rows), 0 of the pairs merged into one property. Another 327 sreality ids sit in the next 5M listing ids, so the old code
-- keeps making new pairs as listings.id grows.
--
-- WHY THE AUGUST SURVIVOR RULE IS WRONG NOW. Migration 371 kept the row with the latest `last_download_attempt_at`. The
-- sreality re-master lane (2026-09-11, scripts/remaster_sreality_images.py) breaks that. (a) It RE-UPLOADS an old row
-- under the shared key, so ownership flips back to the old listing. Both August-verified groups flipped on 09-12/13:
-- the active iDNES listing 13561932 now shows sreality listing 332521's photo. (b) It stamps
-- `last_download_attempt_at` with NO upload on every deferred or terminal (`sreality-749-crop`) outcome, so a later stamp
-- does not mean a later upload. Counted: in 9 of the 152 keys the latest stamp belongs to the row that does NOT hold the
-- object.
--
-- WHO HOLDS THE OBJECT. Every upload is either the download path (`db.mark_image_stored`, which stamps `rendition` since
-- #1414 on 2026-09-11) or a successful re-master (`db.mark_image_remastered`, `sreality-1800-fit`). Three facts follow:
--   1. `rendition` in ('sreality-1800-fit', 'native') => `last_download_attempt_at` IS the row's last upload. The
--      re-master selects only `rendition IS NULL`, and its un-retire (`reopen`) touches only `sreality-749-crop`.
--   2. `rendition` NULL or 'sreality-749-crop' => the last upload came before 2026-09-11, so before every upload in fact 1.
--   3. Among uploads from before 2026-09-11, a current-shape key (`{listings.id}/...`, prefix = the row's listing_id) was
--      minted after Gate 2, so it is later than any legacy `{sreality_id}/...` upload of the same key.
-- The owner is therefore ranked: measured (fact 1) first, by stamp; then current shape; then id. That rank agrees with the
-- latest-stamp rule on 143 of 152 keys and differs on exactly the 9 keys from (b). No colliding row is in the re-master's
-- pending shape (rendition NULL on an sdn.cz URL; 0 counted), so that lane cannot race this file.
--
-- WHAT. Every row that does NOT own its key gets its stored state cleared, which puts it back in the download queue
-- (`pending_image_downloads`: storage_path NULL, unavailable_reason NULL, attempts under the cap). It re-downloads under
-- its own `img/` key. The OWNER is cleared too when its upload came after 2026-10-12 11:12Z. That is when the phone-size
-- copy shipped (#1776: `thumb/w1080/<key>.webp`, made on first request and never re-checked). A copy made before that
-- upload may still hold the other row's photo, and SQL cannot delete an R2 object. A new key gets a new copy. Counted:
-- 152 losers + 37 such owners = 189 rows on 27 listings. The other 115 owners keep their object and their key.
--
-- WHICH COLUMNS. `storage_path`, plus everything that describes the bytes: `phash` (the download writes
-- `COALESCE(new, phash)`, so a stale hash would survive a failed inline hash) and `clip_tagged_at` (the CLIP lane's
-- predicate). These two are exactly what `db.invalidate_derived_signals` nulls; the CLIP vector is upserted in place and
-- never deleted. Also `rendition` / `stored_width` / `stored_height` (the download stamps them again), and the queue trio
-- `download_attempts` / `last_error` / `unavailable_reason`, which `record_images` already resets for an unstored row.
-- `image_dinov3_embeddings` is left alone: that is the chokepoint's default, and 0 colliding rows carry a vector (counted).
-- No label, annotation or training table is touched (0 colliding rows in `image_training_examples`).
--
-- RULES. Not a listing write: no snapshot (rule 2), and `last_seen_at` is not touched (rule 4). No `dirty_properties`
-- (rule 20): no property rollup or Browse row stores an image. The Browse cover is read live (`listing_cover_public`), and
-- the publication settle gate stamps once, inside its first-seen cap. A cleared row renders its portal URL until the
-- re-download lands. A row whose URL is dead ends with `unavailable_reason` set and renders a placeholder. That is
-- correct; showing another property's photo was not.
--
-- PREDICATE, NOT ID LIST. Only the pairs above can share a key: legacy keys come from the unique `sreality_id`, current
-- keys from the unique `listings.id`, and a negative pre-Gate-2 id never equals a listing id. So the scan reads only those
-- listings' images (~1.9k rows, sub-second), not the 15M-row table. A full-table check is too slow for this file's
-- timeout. To run it by hand, off-peak, with `set statement_timeout = '15min'`:
--   select storage_path, count(*) from images where storage_path is not null group by 1 having count(*) > 1;
-- The `sequence IS NULL` case (two rows of one listing both minting `.../0000.jpg`) is the only other way to share a key.
-- `image_key` closes it going forward. This file repairs it only inside the pairs.
--
-- BACKUP FIRST (642's pattern). Schema backup_670 is owner-only. Before any change, it holds every row this file clears,
-- with its old values. The UPDATE is driven FROM the backup, so no row can change without being in it. RE-RUNNABLE: the
-- backup is INSERT ... ON CONFLICT DO NOTHING, so a row keeps its first-seen values. The clear is a compare-and-set on the
-- backed-up key, so a row that has since re-downloaded under `img/` is never touched again. RESTORE, should this be
-- overruled (only rows still unstored are touched):
--   update public.images i
--      set storage_path = b.storage_path, phash = b.phash, clip_tagged_at = b.clip_tagged_at,
--          rendition = b.rendition, stored_width = b.stored_width, stored_height = b.stored_height,
--          download_attempts = b.download_attempts, last_error = b.last_error,
--          unavailable_reason = b.unavailable_reason
--     from backup_670.images_r2_key_collision b
--    where b.image_id = i.id and i.storage_path is null;
-- Drop the backup in a FORWARD migration, once the re-downloads have landed (`drop table if exists
-- backup_670.images_r2_key_collision; drop schema if exists backup_670;`), so the migration_drift probe retires what it saw
-- this file declare. The 152 old objects (and their copies) are orphaned in R2, for the owed orphan sweep.
--
-- ONE TRANSACTION. Replay-safe on CI's empty schema: no pairs, so nothing is backed up or cleared. Row locks only, on at
-- most a few hundred rows. The timeouts are set twice on purpose: the plain SETs are the apply workflow's contract, and
-- the LOCAL ones hold even if the session-level pair landed on another backend.

set lock_timeout = '5s';
set statement_timeout = '120s';

begin;

set local lock_timeout = '5s';
set local statement_timeout = '120s';

-- ---------------------------------------------------------------------------
-- 1. The backup. Copied, not moved; never granted to a browser role.
-- ---------------------------------------------------------------------------
create schema if not exists backup_670;
revoke all on schema backup_670 from anon, authenticated;

create table if not exists backup_670.images_r2_key_collision (
  image_id                 bigint primary key,
  listing_id               bigint,
  storage_path             text not null,
  owns_object              boolean not null,
  phash                    bigint,
  clip_tagged_at           timestamptz,
  rendition                text,
  stored_width             integer,
  stored_height            integer,
  download_attempts        integer not null,
  last_download_attempt_at timestamptz,
  last_error               text,
  unavailable_reason       text,
  backed_up_at             timestamptz not null default now()
);

alter table backup_670.images_r2_key_collision enable row level security;
revoke all on all tables in schema backup_670 from anon, authenticated;

with pair as (
  select o.id as legacy_listing_id, n.id as current_listing_id
    from public.listings o
    join public.listings n on n.id = o.sreality_id
   where o.sreality_id > 0
     and o.sreality_id <= (select max(id) from public.listings)
     and n.id <> o.id
),
holder as (
  select i.id, i.listing_id, i.storage_path, i.phash, i.clip_tagged_at, i.rendition,
         i.stored_width, i.stored_height, i.download_attempts, i.last_download_attempt_at,
         i.last_error, i.unavailable_reason,
         coalesce(i.rendition, '') in ('sreality-1800-fit', 'native') as measured
    from public.images i
   where i.listing_id in (select legacy_listing_id from pair union select current_listing_id from pair)
     and i.storage_path is not null
),
ranked as (
  select h.*,
         count(*) over (partition by h.storage_path) as holders,
         row_number() over (
           partition by h.storage_path
           order by h.measured desc,
                    case when h.measured then h.last_download_attempt_at end desc nulls last,
                    (split_part(h.storage_path, '/', 1) = h.listing_id::text) desc,
                    h.id desc) as rank_in_key
    from holder h
)
insert into backup_670.images_r2_key_collision (
  image_id, listing_id, storage_path, owns_object, phash, clip_tagged_at, rendition, stored_width,
  stored_height, download_attempts, last_download_attempt_at, last_error, unavailable_reason
)
select r.id, r.listing_id, r.storage_path, r.rank_in_key = 1, r.phash, r.clip_tagged_at, r.rendition,
       r.stored_width, r.stored_height, r.download_attempts, r.last_download_attempt_at, r.last_error,
       r.unavailable_reason
  from ranked r
 where r.holders > 1
   and (r.rank_in_key > 1
        or (r.measured and r.last_download_attempt_at >= timestamptz '2026-10-12 11:12:05+00'))
on conflict (image_id) do nothing;

-- ---------------------------------------------------------------------------
-- 2. The clear, driven by the backup: a row changes only if it is in the backup AND still holds the backed-up key.
-- ---------------------------------------------------------------------------
update public.images i
   set storage_path       = null,
       phash              = null,
       clip_tagged_at     = null,
       rendition          = null,
       stored_width       = null,
       stored_height      = null,
       download_attempts  = 0,
       last_error         = null,
       unavailable_reason = null
  from backup_670.images_r2_key_collision b
 where b.image_id = i.id
   and i.storage_path = b.storage_path;

-- ---------------------------------------------------------------------------
-- 3. Post-condition: no key is held by two rows inside the pairs. (Empty on the CI replay, so it passes there too.)
-- ---------------------------------------------------------------------------
do $post$
begin
  if exists (
    select 1
      from public.images i
     where i.storage_path is not null
       and i.listing_id in (
             select unnest(array[o.id, n.id])
               from public.listings o
               join public.listings n on n.id = o.sreality_id
              where o.sreality_id > 0
                and o.sreality_id <= (select max(id) from public.listings)
                and n.id <> o.id)
     group by i.storage_path
    having count(*) > 1) then
    raise exception '670 did not land: a listing-photo key is still held by two image rows';
  end if;
end
$post$;

commit;
