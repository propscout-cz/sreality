-- 642_legacy_vocabulary_heal.sql -- the last off-canon condition spellings and the impossible dispositions speak the canon.
--
-- WHY. Browse > Stats ruling 9 (docs/design/stats-dashboard.md, 2026-10-19): a chart on "Stav" or "Dispozice" shows
-- every stored spelling as its own cell, so 31 spellings no portal states today ("dobry_stav", "smisena",
-- "bardzo_dobry", "v_puvodnim,_peclive_udrzovanem_stavu" ...) sit under the eleven canonical members as "pod 5"
-- rows, and 28 digit pairs no flat can have ("6+2", "1+0", "8+7") do the same beside the N+kk / N+1 grammar.
-- Counted 2026-10-20 09:20Z: 105 listings carry such a condition (all bazos, all inactive, last seen 2026-09-05 at
-- the latest); 812 carry such a disposition (811 bazos, 102 of them active; 1 idnes); 871 properties between them.
--
-- WHERE THEY CAME FROM, AND WHY NO MORE ARRIVE. bazos states neither field. Its disposition was mined from the
-- advert's prose with `\d\+(kk|\d)` -- scraper/vocabulary.py now carries the one grammar, `[1-9]+(kk|1)`, and a
-- near-miss is refused and counted, never stored. Its condition came from the text lane before FIELD CAPTURE W5
-- made that lane's enum the canon itself (toolkit/description_extraction.py: an off-vocabulary value is refused and
-- counted). Both producers are closed; what is left is what they wrote before.
--
-- WHY NOT THE RE-PARSE SEAM. scripts/reparse.py replays the portal's parser and NEVER blanks a stored value -- the
-- right rule for a parser fix, and the opposite of what this heal must do: bazos's parser derives no condition at
-- all (the text lane did), and the grammar's answer to "6+2" is None. Migration 554 is the precedent: a data
-- migration for a heal the seam cannot make, under the seam's own rules.
--
-- WHAT. One mapping, pinned to scraper/vocabulary.py by tests/test_642_legacy_vocabulary_heal.py: every target is a
-- canon member, every source is not, and where the vocabulary already reads a spelling (its words, de-underscored)
-- the target IS its answer. The rest is the operator's reading of each phrase, recorded in that test: a foreign
-- "very good" (sehr / bardzo / zeer / muito dobry) is velmi_dobry; a typo is its word; "v puvodnim stavu" and its
-- elaborations are what the vocabulary already says of the plain phrase (dobry), or udrzovany where the phrase says
-- so; a construction stage (hruba stavba, pred kolaudaci) is ve_vystavbe; a word that is not a condition at all
-- (smisena = a building material, nizkoenergeticka and zatepleny = energy, demontovana) becomes NULL -- the answer
-- the lane gives it today. Any other off-canon spelling present at apply time also becomes NULL (the `else`), so the
-- post-condition holds either way. Every disposition outside the grammar becomes NULL; a later
-- `reparse --source bazos --fields disposition --missing` may fill those whose page states one grammatically (a
-- NULL filled is not a value blanked, which the seam allows).
--
-- RULES. The seam's (R9), as 554: no listing_snapshots row -- bazos hashes the PARSED fields, so each healed LIVE
-- row's next detail fetch appends ONE genuine snapshot and an inactive row never gets one (its column and its last
-- snapshot disagree from now on, the recorded asymmetry); never last_seen_at (rule 4); every touched row's property
-- enqueued into dirty_properties in the SAME statement (rule 20), so properties.condition / .disposition and the
-- Browse row follow on the maintenance lane's next pass. BACKUP FIRST (606's pattern): schema backup_642,
-- owner-only, holds every touched row's old values. RESTORE:
--   update public.listings l set condition = b.condition, disposition = b.disposition
--     from backup_642.listings_vocabulary b where b.listing_id = l.id;
-- The operator drops schema backup_642 once the Stats charts have read clean for a week.
-- ONE TRANSACTION. IDEMPOTENT: the backup is CREATE TABLE IF NOT EXISTS ... AS and a re-run heals nothing. Replay-safe
-- on CI's empty schema. DML only: two UPDATEs on under 1,000 rows, row locks only, each statement under 120 s.

set lock_timeout = '5s';
set statement_timeout = '120s';

begin;

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
-- 2. Condition: each off-canon spelling reads as the canon, or as NULL.
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
                       when 'v_originalnim,_autentickem_stavu'          then 'dobry'
                       when 'dobre_stavu'                               then 'dobry'
                       when 'dobny'                                     then 'dobry'
                       when 'bezny'                                     then 'dobry'
                       when 'v_puvodnim,_avsak_velmi_udrzovanem_stavu'  then 'udrzovany'
                       when 'v_puvodnim,_peclive_udrzovanem_stavu'      then 'udrzovany'
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
insert into dirty_properties (property_id)
select distinct property_id from healed where property_id is not null
on conflict (property_id) do update set marked_at = now();

-- ---------------------------------------------------------------------------
-- 3. Disposition: a digit pair outside the grammar is not a flat.
-- ---------------------------------------------------------------------------
with refused as (
  update public.listings l
     set disposition = null
   where l.disposition is not null
     and l.disposition !~ '^[1-9]\+(kk|1)$'
  returning l.property_id
)
insert into dirty_properties (property_id)
select distinct property_id from refused where property_id is not null
on conflict (property_id) do update set marked_at = now();

-- ---------------------------------------------------------------------------
-- 4. Post-conditions: nothing off-canon is left. (Empty on the CI replay, so they pass there too.)
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
