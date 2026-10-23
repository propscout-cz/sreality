-- 668_publication_settled_at.sql -- The one stored publication fact: properties.settled_at (operator ruling
-- 2026-10-23, Q1-Q6: a property is SERVED -- Browse, the map, stats, the watchdog -- once the pipeline has
-- settled it, or once it is 6 h old; the images-first alert gate folds into it; one cap, everywhere).
--
-- WHAT. settled_at is stamped ONCE by the maintenance pass (scripts/recompute_property_stats._settle_fresh)
-- when a property's first photo is stored (or it has no photos) AND the engine has read every ad
-- (autodedup.rt_fp), holds no pending question about them (autodedup.pairs decision 'evidence_pending')
-- and keeps none in a group that straddles another property (autodedup.cluster_members). NULL = not
-- settled before the cap. A merge passes a settled retired property's stamp to an unsettled survivor
-- (toolkit/property_identity._CARRY_SETTLED_SQL): visibility never regresses on a merge.
-- The rule every surface serves by: coalesce(settled_at, first_seen_at + interval '6 hours') <= now()
-- (toolkit/publication.py, the one spelling; browse_projection carries it from migration 669).
-- NOT the frozen properties.published_at / publish_reason (migration 273): they are the removed engine's
-- history, on the autodedup session's drop list, and a column whose NULL means two things across eras is
-- not a fact -- so the stamp is a NEW column with one meaning.
--
-- ADDITIVE: apply BEFORE the merge through apply_migration.yml (--ref the feature branch), the usual order;
-- a column nobody reads is inert. Idempotent. Served by properties_first_seen_keyset_idx (first_seen_at, id)
-- for the stamper's 6 h range; no index of its own. ROLLBACK: forward only (rule 1).
-- Verify:  select count(*) from properties where settled_at is not null;  -- 0 until the stamper deploys

set lock_timeout = '5s';
set statement_timeout = '120s';

alter table public.properties add column if not exists settled_at timestamptz;

comment on column public.properties.settled_at is
  'Publication (migration 668): when the pipeline settled this property -- first photo stored or none, '
  'every ad read by the engine, no pending engine question, no group straddling another property. Stamped '
  'once by the maintenance pass; a merge passes it to an unsettled survivor. NULL = not settled '
  'before the 6 h cap. Served <=> coalesce(settled_at, first_seen_at + 6 h) <= now() (toolkit/publication.py).';
