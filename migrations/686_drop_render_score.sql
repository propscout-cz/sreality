-- 686: render scoring removed end to end (operator ruling 2026-10-28, review note on P03:
-- "remove render scoring entirely, from front end and backend").
--
-- image_clip_tags.render_score (migration 239) was a CLIP softmax over render-vs-photo
-- anchors, one number per photo. Its last readers are gone in the same PR: the tagger no
-- longer computes it, clip_tag_backfill / retag_from_embeddings no longer write it, the SPA
-- no longer selects clip_render_score, and the DINOv3 bake-off no longer filters on it.
-- Nothing else reads it: no SQL function, RPC or matview (live catalog, 2026-10-28), and none
-- of autodedup's DECISION_MODULES (its CLIP read takes fine_tag, logical_tag and confidence).
-- The room tags, the CLIP embeddings and everything autodedup reads stay as they are.
--
-- DESTRUCTIVE: applied only after the operator's OK (given 2026-10-28) and a backup of the
-- column (backup_before_drop.yml, to R2), after the code PR is deployed — never before, or
-- the deployed tagger's INSERT and the deployed SPA's select would fail.
--
-- The two views that published it are dropped and recreated without clip_render_score
-- (CREATE OR REPLACE cannot drop a column), with migration 591's and 416's bodies otherwise
-- byte-for-byte and the grants they had: authenticated reads, anon gets nothing. Both in one
-- transaction, so no reader ever sees a view missing. The 5 s lock_timeout makes the drop
-- give up instead of queueing the autodedup lane's reads of image_clip_tags behind it.

set lock_timeout = '5s';

begin;

drop view if exists images_public;
drop view if exists listing_cover_public;

-- images_public reads image_tag_scores + tag_head_models with no admin gate on purpose
-- (tests/_admin_gate_registry.py UNGATED, sanctioned in 591 and here).

create view images_public as
 select i.id,
    i.sreality_id,
    i.sequence,
    i.sreality_url,
    i.storage_path,
    ct.fine_tag as clip_fine_tag,
    ct.logical_tag as clip_logical_tag,
    ct.confidence as clip_confidence,
    i.phash,
    i.listing_id,
    i.rendition,
    th.scores as tag_head_scores
   from images i
     left join lateral ( select t.fine_tag,
            t.logical_tag,
            t.confidence
           from image_clip_tags t
          where t.image_id = i.id
          order by t.tagged_at desc
         limit 1) ct on true
     left join image_tag_scores th
       on th.image_id = i.id
      and th.model_id = (select m.id from tag_head_models m where m.status = 'active');

comment on column images_public.tag_head_scores is
  'The ACTIVE tag model''s per-head scores for this photo, {"<tag_taxonomy.id>": 0..1} (independent sigmoids, image_tag_scores.scores). NULL = not scored by the active model. Read by the Browse card cover choice (migration 591).';

revoke all on images_public from public, anon;
grant select on images_public to authenticated;

create view listing_cover_public as
with cover as (
  select distinct on (images.listing_id) images.id,
     images.listing_id,
     images.sreality_id,
     images.sequence,
     images.sreality_url,
     images.storage_path,
     images.phash
    from images
   where images.listing_id is not null
   order by images.listing_id, images.sequence, images.id
)
select c.id,
   c.sreality_id,
   c.sequence,
   c.sreality_url,
   c.storage_path,
   ct.fine_tag as clip_fine_tag,
   ct.logical_tag as clip_logical_tag,
   ct.confidence as clip_confidence,
   c.phash,
   c.listing_id
  from cover c
    left join lateral ( select t.fine_tag,
           t.logical_tag,
           t.confidence
          from image_clip_tags t
         where t.image_id = c.id
         order by t.tagged_at desc
        limit 1) ct on true;

revoke all on listing_cover_public from public, anon;
grant select on listing_cover_public to authenticated;

drop index if exists image_clip_tags_render_null_idx;
alter table image_clip_tags drop column if exists render_score;

commit;

reset lock_timeout;
