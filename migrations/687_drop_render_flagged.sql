-- 687: the last render-scoring column goes (operator ruling 2026-10-28, after migration 686
-- dropped image_clip_tags.render_score: "drop the render_flagged unless it is dropped already").
--
-- image_tag_annotations.render_flagged (migration 308) was ClipAudit's per-photo "this is a
-- render" label. It was never set: the table holds 0 rows (live, 2026-10-28), and no code,
-- SQL function, RPC or matview reads it. Its one reader is the admin-gated view
-- image_tag_annotations_public (migration 318's wrapper), which is dropped and recreated
-- with 318's body minus render_flagged (CREATE OR REPLACE cannot drop a column), under the
-- same gate and the same grants: authenticated reads, anon gets nothing.
--
-- DESTRUCTIVE: applied only after the operator's OK (given 2026-10-28) and a backup of the
-- table (backup_before_drop.yml, label render-flagged, to R2). The table itself and its other
-- columns stay; dropping it is wave C of docs/design/tag-annotation-matrix.md, not this.

set lock_timeout = '5s';

begin;

drop view if exists public.image_tag_annotations_public;

alter table image_tag_annotations drop column if exists render_flagged;

create view public.image_tag_annotations_public as
select * from (SELECT image_id,
    tag_flagged,
    note,
    updated_at
   FROM image_tag_annotations
) __admin_gate
where is_platform_admin();

revoke all on public.image_tag_annotations_public from public, anon;
grant select on public.image_tag_annotations_public to authenticated;

commit;

reset lock_timeout;
