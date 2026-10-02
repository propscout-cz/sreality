-- 582: a stable identity for each deal-pipeline card (rule 22, operator decision 2026-10-02).
--
-- `property_pipeline.card_id` is minted when a card is born (this column default: `add_card`,
-- a raw insert, a test seed) and kept through every stage move, reorder and merge: the merge
-- carrier moves the winning ROW onto the survivor instead of copying its stage. Removing a
-- card deletes the row; a re-add is a NEW card with a new id. Unique, so the carrier finds a
-- deal by this id alone; it still names account_id in every lookup (it runs as service role).
-- The volatile default rewrites this small table once, giving every existing card its own id.
--
-- `property_pipeline_events.card_id` names the card an event is about: the API's add / move /
-- remove events, the merge snapshot (`merge_absorb`), a detach's send-home (`unmerge_restore`)
-- and put-back (`split_carry`). NULL on every row written before this migration, and no
-- default: a default would invent ids no card ever had. A merge snapshotted before 582 keeps
-- today's inference (toolkit/pipeline_identity.py's card_id IS NULL fallback), made
-- non-destructive: the card a survivor absorbed moves back, never deleted.
--
-- `property_merge_events_retired_idx`: a detach reads the merge that LAST retired the property
-- it reactivates (the deal it went in with), by retired_property_id, newest first; no index
-- served that column (~128k rows on 2026-10-02).
--
-- ADDITIVE. No RLS, grant or view change: table grants cover the new columns, the policies
-- key on account_id, and every reader names its columns. Today's code names its columns on
-- every write, so it inherits the defaults and runs unchanged with this applied.
--
-- APPLY FIRST, THEN MERGE the code that names card_id (`database` skill: apply before you merge
-- when the code reads the new schema; the migration 438 outage). Railway deploys the API and
-- the realtime worker on merge, and without this column that code raises UndefinedColumn:
-- every pipeline add / move / remove would 500, and every merge (operator and engine) and every
-- detach that reactivates a property would abort. Verify before merging: two rows from
--   SELECT table_name FROM information_schema.columns WHERE column_name = 'card_id'
--     AND table_name IN ('property_pipeline', 'property_pipeline_events');
-- and the indexes property_pipeline_card_id_uq and property_merge_events_retired_idx in
-- pg_indexes. Then deploy the API and the realtime worker together and keep merges and detaches
-- off until both run the new code: a new merge (the winning row moves, the loser is parked)
-- undone by an old detach (it restores a copy) duplicates a deal and strands the parked one.

begin;

set local lock_timeout = '6s';

alter table property_pipeline
  add column if not exists card_id uuid not null default gen_random_uuid();

create unique index if not exists property_pipeline_card_id_uq on property_pipeline (card_id);

comment on column property_pipeline.card_id is
  'Stable deal identity (rule 22, migration 582): kept through stage moves and merges; '
  'a re-add after a remove is a new card.';

alter table property_pipeline_events add column if not exists card_id uuid;

create index if not exists property_pipeline_events_card
  on property_pipeline_events (card_id, id) where card_id is not null;

comment on column property_pipeline_events.card_id is
  'The property_pipeline.card_id this event is about (migration 582); NULL before 582.';

create index if not exists property_merge_events_retired_idx
  on property_merge_events (retired_property_id, id);

commit;
