-- 693_broker_count_book_drop.sql -- the old broker count book goes (broker board W3b): one broker vocabulary, the book's.
--
-- WHY (docs/design/broker-board.md, W3b's drop list). Every broker number is now read from `broker_book` (692): the board
-- inside a Browse cohort, and since W3a (#2183) name search, the dossier, the merge review and Outreach's draft facts
-- (toolkit/brokers.BROKER_COUNTS_SQL). W3a deleted every writer of the old counts and left them, unread, for this file:
--   * the count book: the matview broker_region_type_stats (580) with its registry row, the view over it
--     (broker_geo_options) and the leaderboard RPC with its chip helper (609: broker_leaderboard, district_level_codes);
--   * the frozen count columns: brokers.{listing_count, property_count, active_listing_count, active_property_count},
--     firms.{broker_count, listing_count, active_listing_count, stats_computed_at},
--     broker_identities.{listing_count, active_listing_count}, broker_firm_memberships.listing_count (185), and the three
--     views projecting them: brokers_public (580's, eight count columns, four through the matview),
--     broker_firm_memberships_public (187) and firms_public (187);
--   * W1's identity-grain book (691): property_broker_spell_projection and property_broker_spells, written by nothing
--     since 692.
-- brokers_public and broker_firm_memberships_public are re-created without their counts (CREATE OR REPLACE cannot drop a
-- column), every other column and its order kept, as plain owner-rights views with the revoke they had (580, 299): A6,
-- anon and authenticated read neither, the API's service connection does. Neither carried a comment; contacts are
-- masked by the API (apply_pii_policy), not here. firms_public has no reader at all and is dropped, not re-created.
--
-- WHAT THE DEPLOYED W3a CODE READS (main abe764830, the whole tree grepped): brokers_public by named columns, none a count
-- (toolkit/brokers._BROKER_SQL, api/broker_board.board, api/broker_review, api/outreach._broker_context; the numbers are
-- BROKER_COUNTS_SQL over broker_book); broker_firm_memberships_public by named columns without listing_count
-- (toolkit/brokers.get_broker); a firm's broker count is computed (toolkit/brokers.firm_options: count(b.id)), never
-- firms.broker_count; scripts/resolve_brokers writes no count (brokers.stats_computed_at, kept, is still written);
-- recompute_property_stats writes broker_book only. The one deployed touch is resolve_brokers._maybe_refresh_matview: it
-- reads the registry row first and refreshes nothing without it, so the row is deleted FIRST, alone: a refresh already
-- running holds the matview, so step 2 times out and the apply retries (that refresh's stamp then updates no row,
-- silently); a call that read the row in the instant before the delete raises once in the broker lane ("no
-- derived_artifacts row"), and the next pass reads no row. The SPA and the extension read none of these (A6: revoked
-- since 299 / 395).
--
-- BACKUP FIRST (database skill: destructive), to R2:
--   gh workflow run backup_before_drop.yml -f label=broker-count-book -f targets="brokers:id,listing_count,property_count,active_listing_count,active_property_count firms:id,broker_count,listing_count,active_listing_count,stats_computed_at broker_identities:id,listing_count,active_listing_count broker_firm_memberships:id,listing_count property_broker_spells"
-- The matview needs no dump: derived (580's definition over listings and the broker tables), a current-state count with
-- no history in it, rebuildable from 580's text at any time. Its registry row's settings are 440's, 579's and 673's.
--
-- APPLY ORDER: after W3a (#2183) is merged AND its Railway rollout is live on the API and the realtime worker (W2's code
-- reads brokers_public's counts and the worker's broker lane refreshes the matview) -- both met: merged 2026-10-30 as
-- main abe764830, Railway's API, realtime-worker, vite and job-runner statuses green 05:01-05:03Z -- with the operator's
-- OK and the backup above; then BEFORE this PR merges (its code no longer refreshes the matview, its tests expect the
-- drops):
--   gh workflow run apply_migration.yml --ref cleanup/broker-count-book-drop -f file=693_broker_count_book_drop.sql
--   gh workflow run apply_migration.yml --ref cleanup/broker-count-book-drop -f file=693_broker_count_book_drop.sql -f dry_run=false -f confirm=APPLY
-- Not during the daily broker sweep (resolve_brokers_full.yml): its long transactions on brokers and firms would make
-- every column drop time out. Dropping property_broker_spells removes its foreign keys' triggers on properties and
-- broker_identities, an ACCESS EXCLUSIVE on both (measured on the replay: the FK drop alone takes it too), so step 5
-- needs a moment when no long reader holds properties (the */15 browse_list rebuild reads it for its whole run): the
-- 5 s lock_timeout bounds the queue behind each try, and the workflow retries a timed-out file (30 tries, 20 s apart;
-- every statement here is idempotent: IF EXISTS everywhere, the views re-created whole).
-- ROLLBACK: forward only (rule 1): a new migration re-creating what it needs from 580 / 609 / 187 / 691, the counts
-- restored from the backup's CSVs.
-- Verify: the $post$ block below raises unless every dropped object is gone and the two views are as re-created.

set lock_timeout = '5s';

-- 1. The registry row, alone and first (above): the deployed refresh stops before anything it refreshes goes.
delete from public.derived_artifacts where name = 'broker_region_type_stats';

-- 2. The count book and everything over it. brokers_public is dropped and re-created in one transaction, so no reader
--    ever finds it missing; its matview join goes with its count columns.
begin;

drop view if exists public.broker_geo_options;

drop function if exists public.broker_leaderboard(
  text[], boolean[], text[], bigint[], text, text, text, integer, bigint[], integer, boolean, text[], boolean);
drop function if exists public.district_level_codes(text[], bigint[], boolean[], text, boolean);

drop view if exists public.brokers_public;
create view public.brokers_public as
 select b.id as broker_id,
    b.display_name,
    b.primary_email,
    b.primary_phone,
    b.primary_firm_id as firm_id,
    f.canonical_domain as firm_domain,
    f.display_name as firm_name,
    f.is_franchise as firm_is_franchise,
    b.source_count,
    b.distinct_source_count,
    b.first_seen_at,
    b.last_seen_at
   from public.brokers b
     left join public.firms f on f.id = b.primary_firm_id
  where b.status = 'active'::text;
revoke all on public.brokers_public from anon, authenticated;

drop materialized view if exists public.broker_region_type_stats;

commit;

-- 3. The membership view without its count (187's body otherwise), and the firm view nothing reads.
begin;

drop view if exists public.broker_firm_memberships_public;
create view public.broker_firm_memberships_public as
 select m.broker_id,
    m.firm_id,
    f.canonical_domain as firm_domain,
    f.display_name as firm_name,
    m.first_seen_at,
    m.last_seen_at,
    (m.last_seen_at > (now() - '90 days'::interval)) as is_current
   from public.broker_firm_memberships m
     join public.brokers b on b.id = m.broker_id and b.status = 'active'::text
     join public.firms f on f.id = m.firm_id;
revoke all on public.broker_firm_memberships_public from anon, authenticated;

drop view if exists public.firms_public;

commit;

-- 4. The frozen count columns, one table per statement: no view names them any more, and a statement that holds one
--    table's lock never waits for another's.
alter table public.brokers
  drop column if exists listing_count,
  drop column if exists property_count,
  drop column if exists active_listing_count,
  drop column if exists active_property_count;

alter table public.firms
  drop column if exists broker_count,
  drop column if exists listing_count,
  drop column if exists active_listing_count,
  drop column if exists stats_computed_at;

alter table public.broker_identities
  drop column if exists listing_count,
  drop column if exists active_listing_count;

alter table public.broker_firm_memberships
  drop column if exists listing_count;

-- 5. W1's identity-grain book (691). The view reads listings, not the table, so the order is free. The index is named
--    so the drift probe (scripts/migration_objects.py) retires 691's declaration of it with the table.
drop view if exists public.property_broker_spell_projection;
drop index if exists public.property_broker_spells_identity_idx;
drop table if exists public.property_broker_spells;

-- 6. Post-check.
do $post$
declare
  v_rel text;
  v_role text;
  v_left text;
begin
  foreach v_rel in array array['broker_geo_options', 'broker_region_type_stats', 'firms_public',
                               'property_broker_spell_projection', 'property_broker_spells',
                               'property_broker_spells_identity_idx'] loop
    if to_regclass('public.' || v_rel) is not null then
      raise exception '693 did not land: public.% still exists', v_rel;
    end if;
  end loop;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace
                                     and proname in ('broker_leaderboard', 'district_level_codes')) then
    raise exception '693 did not land: broker_leaderboard or district_level_codes still exists';
  end if;
  if exists (select 1 from public.derived_artifacts where name = 'broker_region_type_stats') then
    raise exception '693 did not land: broker_region_type_stats is still registered';
  end if;

  select string_agg(v.rel || '.' || v.col, ', ') into v_left
    from (values ('brokers', 'listing_count'), ('brokers', 'property_count'), ('brokers', 'active_listing_count'),
                 ('brokers', 'active_property_count'), ('firms', 'broker_count'), ('firms', 'listing_count'),
                 ('firms', 'active_listing_count'), ('firms', 'stats_computed_at'),
                 ('broker_identities', 'listing_count'), ('broker_identities', 'active_listing_count'),
                 ('broker_firm_memberships', 'listing_count')) v(rel, col)
    join pg_attribute a on a.attrelid = to_regclass('public.' || v.rel) and a.attname = v.col
                       and a.attnum > 0 and not a.attisdropped;
  if v_left is not null then
    raise exception '693 did not land: % still exist(s)', v_left;
  end if;

  if (select array_agg(a.attname::text order by a.attnum) from pg_attribute a
       where a.attrelid = 'public.brokers_public'::regclass and a.attnum > 0 and not a.attisdropped)
     is distinct from array['broker_id', 'display_name', 'primary_email', 'primary_phone', 'firm_id', 'firm_domain',
                            'firm_name', 'firm_is_franchise', 'source_count', 'distinct_source_count',
                            'first_seen_at', 'last_seen_at'] then
    raise exception '693 did not land: brokers_public is not 580''s columns less the eight counts';
  end if;
  if (select array_agg(a.attname::text order by a.attnum) from pg_attribute a
       where a.attrelid = 'public.broker_firm_memberships_public'::regclass and a.attnum > 0 and not a.attisdropped)
     is distinct from array['broker_id', 'firm_id', 'firm_domain', 'firm_name', 'first_seen_at', 'last_seen_at',
                            'is_current'] then
    raise exception '693 did not land: broker_firm_memberships_public is not 187''s columns less listing_count';
  end if;

  foreach v_role in array array['anon', 'authenticated'] loop
    if has_table_privilege(v_role, 'public.brokers_public', 'select')
       or has_table_privilege(v_role, 'public.broker_firm_memberships_public', 'select') then
      raise exception '693 did not land: % can read a broker view (A6)', v_role;
    end if;
  end loop;
end
$post$;

reset lock_timeout;
