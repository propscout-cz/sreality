-- 642_legacy_vocabulary_heal.sql -- the last off-canon condition spellings and the dispositions outside the grammar speak the canon.
--
-- WHY. Browse > Stats ruling 9 (docs/design/stats-dashboard.md, 2026-10-19): a chart on "Stav" or "Dispozice" shows
-- every stored spelling as its own cell, so 31 spellings no portal states today ("dobry_stav", "smisena",
-- "bardzo_dobry", "v_puvodnim,_peclive_udrzovanem_stavu" ...) sit under the eleven canonical members as "pod 5"
-- rows, and 28 digit pairs outside the N+kk / N+1 grammar ("6+2", "1+0", "8+7") do the same beside it -- the
-- vocabulary refuses them (its one grammar: N rooms plus kk or 1, N never zero); in bazos prose most are phone
-- numbers or dimensions. Counted 2026-10-20 09:20Z: 105 listings carry such a condition (all bazos, all inactive,
-- last seen 2026-09-05 at the latest); 812 carry such a disposition (811 bazos, 102 of them active; 1 idnes);
-- 871 properties between them.
--
-- WHERE THEY CAME FROM, AND WHY NO MORE ARRIVE. bazos states neither field. Its disposition was mined from the
-- advert's prose with `\d\+(kk|\d)` -- scraper/vocabulary.py now carries the one grammar, `[1-9]+(kk|1)`, and a
-- near-miss is refused and counted, never stored. Its condition came from the text lane before FIELD CAPTURE W5
-- made that lane's enum the canon itself (toolkit/description_extraction.py: an off-vocabulary value is refused and
-- counted). Both producers are closed; what is left is what they wrote before, and it stays: on bazos both columns
-- are `text` cells of the attribute contract, which the ingest upsert PRESERVES when the parse is silent
-- (scraper/db.py `_preserved_columns`), so no refetch ever clears them.
--
-- WHY NOT THE RE-PARSE SEAM. scripts/reparse.py replays the portal's parser and NEVER blanks a stored value -- the
-- right rule for a parser fix, and the opposite of what this heal must do: bazos's parser derives no condition at
-- all (the text lane did), and the grammar's answer to "6+2" is None. Migration 554 is the precedent: a data
-- migration for a heal the seam cannot make, under the seam's own rules.
--
-- WHAT. One mapping, pinned to scraper/vocabulary.py by tests/test_642_legacy_vocabulary_heal.py: every target is a
-- canon member, every source is not, and where the vocabulary reads a spelling (its words, de-underscored) the
-- target agrees with its answer. The rest is this PR's reading of each phrase under the operator's delegation of
-- 2026-10-20 ("resolve the legacy vocabulary"), listed once in that test: a foreign "very good" (sehr / bardzo /
-- zeer / muito dobry) is velmi_dobry; a typo is its word; "v puvodnim stavu" and every elaboration of it
-- ("zachovaly", "peclive udrzovany") is what the vocabulary already says of the plain phrase, dobry -- udrzovany
-- ranks BELOW dobry in the registry, so a phrase describing a better-kept original state must not land there; a
-- construction stage (hruba stavba, pred kolaudaci -- legally unfinished) is ve_vystavbe; a word that is not a
-- condition at all (smisena = a building material, nizkoenergeticka and zatepleny = energy, demontovana) becomes
-- NULL -- the answer the lane gives it today. Any other off-canon spelling present at apply time also becomes NULL
-- (the `else`), so the post-condition holds either way. Every disposition outside the grammar becomes NULL; a later
-- `reparse --source bazos --fields disposition --missing --write --allow-snapshot-deferral` (dry run first) may fill
-- those whose page states one grammatically (a NULL filled is not a value blanked, which the seam allows).
--
-- RULES. The seam's (R9), as 554: never last_seen_at (rule 4); every touched row's property enqueued into
-- dirty_properties in the SAME statement as its write (rule 20), stamped with the statement clock so a maintenance
-- pass whose cutoff falls inside this transaction cannot clear the mark before it recomputed on the healed values
-- (scripts/recompute_property_stats.py claims and deletes by `marked_at <= cutoff`); properties.condition /
-- .disposition and the Browse row follow on that lane's next pass (served rows only: a merged-away property keeps
-- its last rollup, which nothing serves). No listing_snapshots row -- and none deferred either: bazos hashes its own
-- PARSE, which this file does not change, so the next fetch's hash is what it would have been anyway; the column
-- simply stops disagreeing with the parse. BACKUP FIRST (606's pattern): schema backup_642, owner-only, holds every
-- touched row's old values, and a gate refuses the heal if a row about to change is not in it. RESTORE, should a
-- reading be overruled -- a compare-and-set that only touches a row still holding what this file wrote, enqueuing
-- its property as the heal did:
--   with restored as (
--     update public.listings l
--        set condition   = case when l.condition is null or l.condition in (<the eleven>) then b.condition
--                               else l.condition end,
--            disposition = case when l.disposition is null then b.disposition else l.disposition end
--       from backup_642.listings_vocabulary b
--      where b.listing_id = l.id
--     returning l.property_id)
--   insert into dirty_properties (property_id, marked_at)
--   select distinct property_id, clock_timestamp() from restored where property_id is not null
--   on conflict (property_id) do update set marked_at = excluded.marked_at;
-- The backup goes once the Stats charts have read clean for a week, as a FORWARD migration (`drop table if exists
-- backup_642.listings_vocabulary; drop schema if exists backup_642;`) so the migration_drift probe retires the
-- object it saw this file declare.
-- ONE TRANSACTION. IDEMPOTENT: the backup is CREATE TABLE IF NOT EXISTS ... AS and a re-run heals nothing. Replay-safe
-- on CI's empty schema. DML plus the backup schema: two UPDATEs on under 1,000 rows, row locks only, each statement
-- under 120 s. The timeouts are set twice on purpose: the plain SETs are the apply workflow's contract, the LOCAL ones
-- hold even where the session-level pair landed on another backend.

set lock_timeout = '5s';
set statement_timeout = '120s';

begin;

set local lock_timeout = '5s';
set local statement_timeout = '120s';

-- ---------------------------------------------------------------------------
-- 1. The backup. Copied, not moved; never granted to a browser role.
-- ---------------------------------------------------------------------------
create schema if not exists backup_642;
revoke all on schema backup_642 from anon, authenticated;

create table if not exists backup_642.listings_vocabulary as
  select l.id as listing_id, l.source, l.property_id, l.is_active, l.condition, l.disposition,
         now() as backed_up_at
    from public.listings l
   where (l.condition is not null
          and l.condition not in ('projekt', 've_vystavbe', 'novostavba', 'po_rekonstrukci', 'velmi_dobry', 'dobry',
                                  'udrzovany', 'v_rekonstrukci', 'pred_rekonstrukci', 'spatny', 'k_demolici'))
      or (l.disposition is not null and l.disposition !~ '^[1-9]\+(kk|1)$');

alter table backup_642.listings_vocabulary enable row level security;
revoke all on all tables in schema backup_642 from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. THE GATE: every row about to change is in the backup. After a completed apply nothing matches, so it passes.
-- ---------------------------------------------------------------------------
do $gate$
declare
  missing bigint;
begin
  select count(*) into missing
    from public.listings l
   where ((l.condition is not null
           and l.condition not in ('projekt', 've_vystavbe', 'novostavba', 'po_rekonstrukci', 'velmi_dobry', 'dobry',
                                   'udrzovany', 'v_rekonstrukci', 'pred_rekonstrukci', 'spatny', 'k_demolici'))
       or (l.disposition is not null and l.disposition !~ '^[1-9]\+(kk|1)$'))
     and not exists (select 1 from backup_642.listings_vocabulary b where b.listing_id = l.id);
  if missing > 0 then
    raise exception '642 refused: % row(s) to heal are not in backup_642 -- drop the backup schema and re-apply', missing;
  end if;
end
$gate$;

-- ---------------------------------------------------------------------------
-- 3. Condition: each off-canon spelling reads as the canon, or as NULL.
-- ---------------------------------------------------------------------------
with healed as (
  update public.listings l
     set condition = case l.condition
                       when 'dobry_stav'                                then 'dobry'
                       when 'v_puvodnim_stavu'                          then 'dobry'
                       when 'puvodni'                                   then 'dobry'
                       when 'puvodni_zachovaly'                         then 'dobry'
                       when 'zachovaly'                                 then 'dobry'
                       when 'v_puvodnim_ale_zachovalem_stavu'           then 'dobry'
                       when 'v_puvodnim,_avsak_velmi_udrzovanem_stavu'  then 'dobry'
                       when 'v_puvodnim,_peclive_udrzovanem_stavu'      then 'dobry'
                       when 'v_originalnim,_autentickem_stavu'          then 'dobry'
                       when 'dobre_stavu'                               then 'dobry'
                       when 'dobny'                                     then 'dobry'
                       when 'bezny'                                     then 'dobry'
                       when 've_velmi_dobrem_stavu'                     then 'velmi_dobry'
                       when 'sehr_dobry'                                then 'velmi_dobry'
                       when 'bardzo_dobry'                              then 'velmi_dobry'
                       when 'zeer_dobry'                                then 'velmi_dobry'
                       when 'muito_dobry'                               then 'velmi_dobry'
                       when 'po_castecne_rekonstrukci'                  then 'po_rekonstrukci'
                       when 'po_castecne_renovaci'                      then 'po_rekonstrukci'
                       when 'predrekonstrukci'                          then 'pred_rekonstrukci'
                       when 'predkonstrukci'                            then 'pred_rekonstrukci'
                       when 'pred_rekonstrukciou'                       then 'pred_rekonstrukci'
                       when 'v_prestavbe'                               then 'v_rekonstrukci'
                       when 've_vstavbe'                                then 've_vystavbe'
                       when 've_vyvstavbe'                              then 've_vystavbe'
                       when 'hruba_stavba'                              then 've_vystavbe'
                       when 'pred_kolaudaci'                            then 've_vystavbe'
                       when 'smisena'                                   then null
                       when 'nizkoenergeticka'                          then null
                       when 'zatepleny'                                 then null
                       when 'demontovana'                               then null
                       else null
                     end
   where l.condition is not null
     and l.condition not in ('projekt', 've_vystavbe', 'novostavba', 'po_rekonstrukci', 'velmi_dobry', 'dobry',
                             'udrzovany', 'v_rekonstrukci', 'pred_rekonstrukci', 'spatny', 'k_demolici')
  returning l.property_id
)
insert into dirty_properties (property_id, marked_at)
select distinct property_id, clock_timestamp() from healed where property_id is not null
on conflict (property_id) do update set marked_at = excluded.marked_at;

-- ---------------------------------------------------------------------------
-- 4. Disposition: a digit pair outside the grammar is not a disposition.
-- ---------------------------------------------------------------------------
with refused as (
  update public.listings l
     set disposition = null
   where l.disposition is not null
     and l.disposition !~ '^[1-9]\+(kk|1)$'
  returning l.property_id
)
insert into dirty_properties (property_id, marked_at)
select distinct property_id, clock_timestamp() from refused where property_id is not null
on conflict (property_id) do update set marked_at = excluded.marked_at;

-- ---------------------------------------------------------------------------
-- 5. Post-conditions: no listing is left off-canon. (Empty on the CI replay, so they pass there too.)
-- ---------------------------------------------------------------------------
do $post$
begin
  if exists (select 1 from public.listings
              where condition is not null
                and condition not in ('projekt', 've_vystavbe', 'novostavba', 'po_rekonstrukci', 'velmi_dobry', 'dobry',
                                      'udrzovany', 'v_rekonstrukci', 'pred_rekonstrukci', 'spatny', 'k_demolici')) then
    raise exception '642 did not land: an off-canon condition survived';
  end if;
  if exists (select 1 from public.listings
              where disposition is not null and disposition !~ '^[1-9]\+(kk|1)$') then
    raise exception '642 did not land: a disposition outside the grammar survived';
  end if;
end
$post$;

commit;

reset statement_timeout;
reset lock_timeout;
