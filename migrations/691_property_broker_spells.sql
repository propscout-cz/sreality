-- 691_property_broker_spells.sql -- the broker book and the speed measure (broker board W1: dark, nothing reads either).
--
-- WHY (docs/design/broker-board.md). A broker board is the Browse cohort counted by broker. Counting needs one fact per
-- (property, broker identity): when that identity's ads on the property began and whether they have ended -- a SPELL.
-- The matview broker_region_type_stats counts ads by region with its own 7-day liveness rule and no property status, so
-- it can answer neither "how many of this cohort's properties" nor "how fast did they leave the market".
--
-- WHAT. Additive; one definition per concept:
--   property_broker_spell_projection  THE definition of a spell, over `listings`: one row per (property_id,
--                                     broker_identity_id); started_at = the identity's first ad's first_seen_at (the
--                                     start measure_tom_days uses, 617; discovered_at, 444, is the sighting time and
--                                     out of scope here); ended_at = NULL while any of its ads on the property runs,
--                                     else the last sighting of the last; tom_observed = measure_tom_observed (625) of
--                                     the identity's FIRST ad in browse_projection's order (669: an ad with a category
--                                     first, then first_seen_at, then id), so a spell's flag means what a property's does.
--   property_broker_spells            the book: that view, stored. Derived, never history: written only by the
--                                     property maintenance pass (scripts/recompute_property_stats.py: properties_changed,
--                                     which every merge, detach, birth and dirty-drain slice runs, and the daily sweep's
--                                     batches), each time from the view. Service role only, like every broker relation
--                                     (Phase 0 A6, migration 299; ADR-0002 amended 2026-10-29).
--   broker_book                       the book read by canonical broker (identities folded), what every reader reads.
--   measure_exit_within(...)          THE speed measure: did this spell's property leave the market within N days.
--                                     Python face toolkit.measures.exit_within_sql.
-- The book is keyed on the IDENTITY, so a broker merge (it re-points broker_identities.broker_id) changes no row; a
-- re-attribution (listings.broker_identity_id) dirties the ad's property (toolkit/broker_sources.py, rule 20).
--
-- APPLY through apply_migration.yml (--ref feature/broker-book) BEFORE merging: the maintenance pass the same PR ships
-- writes the table on every slice, so a merge before the apply fails every maintenance pass and every merge. Safe
-- against the deployed code: nothing reads or writes it yet. A spell that changes between the apply and the deploy is
-- healed by the first daily sweep after it. The backfill reads every attributed ad once (one aggregate, the view's own
-- SQL), in its own statement after the DDL commits (§4: no table lock on properties or broker_identities while it
-- runs). ROLLBACK: forward only (rule 1). Idempotent: a re-apply replaces the view and the function with themselves and
-- the backfill inserts only the spells the book lacks.
-- Verify:
--   select count(*) from property_broker_spells;                                                   -- > 0
--   select count(*) from property_broker_spell_projection v left join property_broker_spells s
--     using (property_id, broker_identity_id) where s.property_id is null;                           -- 0

set lock_timeout = '5s';
set statement_timeout = '900s';

begin;

-- ---------------------------------------------------------------------------
-- 1. THE spell. A qual on property_id (the maintenance pass's id list or id range) is a qual on a grouping column, so
--    the planner applies it below the aggregate, on listings_property_id_idx.
-- ---------------------------------------------------------------------------
create or replace view public.property_broker_spell_projection as
  select l.property_id,
         l.broker_identity_id,
         min(l.first_seen_at) as started_at,
         case when bool_or(l.is_active) then null else max(l.last_seen_at) end as ended_at,
         (array_agg(public.measure_tom_observed(l.source, l.category_main, l.category_type, l.first_seen_at)
                    order by (l.category_main is null or l.category_type is null), l.first_seen_at, l.id))[1]
           as tom_observed
  from public.listings l
  where l.property_id is not null and l.broker_identity_id is not null
  group by l.property_id, l.broker_identity_id;

comment on view public.property_broker_spell_projection is
  'THE definition of a broker spell (migration 691): one row per (property, broker identity) over the identity''s ads on '
  'the property. started_at = min(first_seen_at), the start measure_tom_days uses; ended_at = NULL while any of those ads '
  'is active, else max(last_seen_at); tom_observed = measure_tom_observed of the identity''s first ad in browse_projection''s '
  'order. Read only by the property maintenance pass, which stores it in property_broker_spells.';

revoke all on public.property_broker_spell_projection from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. THE book.
-- ---------------------------------------------------------------------------
create table if not exists public.property_broker_spells (
  property_id         bigint      not null references public.properties(id),
  broker_identity_id  bigint      not null references public.broker_identities(id) on delete cascade,
  started_at          timestamptz not null,
  ended_at            timestamptz,
  tom_observed        boolean     not null,
  primary key (property_id, broker_identity_id)
);

create index if not exists property_broker_spells_identity_idx
  on public.property_broker_spells (broker_identity_id);

comment on table public.property_broker_spells is
  'The broker book (migration 691): property_broker_spell_projection, stored, one row per (property, broker identity). '
  'Derived, never history: written only by the property maintenance pass (properties_changed and the daily sweep, '
  'scripts/recompute_property_stats.py), which upserts the view''s rows for the properties it recomputes and deletes the '
  'ones the view no longer holds. Service role only (broker relations are the API''s: A6, migration 299).';
comment on column public.property_broker_spells.started_at is
  'The first_seen_at of the identity''s first ad on the property: our first WRITE of it (the drain''s, migration 444), '
  'the start measure_tom_days counts from.';
comment on column public.property_broker_spells.ended_at is
  'NULL while any of the identity''s ads on the property is active; else the last sighting of the last of them.';
comment on column public.property_broker_spells.tom_observed is
  'measure_tom_observed of the identity''s first ad in browse_projection''s order: false when that ad was first written '
  'on a sweep day: it was already on the market, so started_at is later than the spell''s real start (left-censored) '
  'and measure_exit_within does not follow the spell.';

alter table public.property_broker_spells enable row level security;
revoke all on public.property_broker_spells from public, anon, authenticated;

-- 2b. The book read as (property, canonical broker): every reader of "this broker's properties" reads THIS (the board,
--     Browse's broker scope, the broker counts), never the spells. A broker's identities are one per portal, so the
--     sources are the portals the broker advertises the property on (active_sources: with an ad still running), the
--     shape portal_status_matches takes (MS19: broker + portal + status = one ad). started_at / ended_at / tom_observed
--     fold the broker's spells as a spell folds its ads; tom_observed is the earliest spell's. An identity not yet
--     resolved to a broker has no row here (its property reads unattributed).
create or replace view public.broker_book as
  select s.property_id,
         bi.broker_id,
         bool_or(s.ended_at is null) as is_active,
         array_agg(distinct bi.source order by bi.source) as all_sources,
         coalesce(array_agg(distinct bi.source order by bi.source) filter (where s.ended_at is null), '{}')
           as active_sources,
         min(s.started_at) as started_at,
         case when bool_or(s.ended_at is null) then null else max(s.ended_at) end as ended_at,
         (array_agg(s.tom_observed order by s.started_at, s.broker_identity_id))[1] as tom_observed
  from public.property_broker_spells s
  join public.broker_identities bi on bi.id = s.broker_identity_id
  join public.brokers b on b.id = bi.broker_id and b.status = 'active'
  group by s.property_id, bi.broker_id;

comment on view public.broker_book is
  'The broker book by canonical broker (migration 691): one row per (property, broker) over property_broker_spells. '
  'is_active / all_sources / active_sources are portal_status_matches'' inputs at the broker''s grain (MS19); started_at, '
  'ended_at and tom_observed fold the broker''s spells (measure_exit_within''s inputs). Service role only (A6).';

revoke all on public.broker_book from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. THE speed measure, beside measure_tom_days (617). One expression, no sub-select, no SET: inlinable.
-- ---------------------------------------------------------------------------
create or replace function public.measure_exit_within(
  p_observed boolean,
  p_started_at timestamptz,
  p_ended_at timestamptz,
  p_property_active boolean,
  p_property_last_seen timestamptz,
  p_days integer
)
 returns text
 language sql
 stable parallel safe
as $function$
    select case
        when not coalesce(p_observed
                          and p_started_at >= timestamptz '2026-09-08 00:00:00+00'
                          and p_started_at <= now() - (p_days + 7) * interval '24 hours', false)
          or p_property_active is null or p_property_last_seen is null
          then null
        when p_ended_at <= p_started_at + p_days * interval '24 hours'
         and not p_property_active
         and p_property_last_seen <= p_ended_at + 7 * interval '24 hours'
          then 'exited'
        when p_property_active and p_property_last_seen < p_started_at + p_days * interval '24 hours'
          then 'unknown'
        else 'not_exited'
    end
$function$;

comment on function public.measure_exit_within(boolean, timestamptz, timestamptz, boolean, timestamptz, integer) is
  'THE "left the market within N days" measure (migration 691) over one broker spell (property_broker_spells) and its '
  'property: started_at S, ended_at E, the property''s is_active A and last_seen_at L, N = p_days, a day 24 h. '
  'NULL = not followed: the spell is not tom_observed (its first ad came in a sweep: left-censored), S is before '
  '2026-09-08 (the structural end gate, rule 3: earlier exits are untrustworthy), its window N + M (M = 7 days) has not '
  'closed, or A or L is unknown. ''exited'' = the spell ended by day N (E <= S + N), the property left the market (not A) '
  'and no other ad ran on more than M past it (L <= E + M). ''unknown'' = the property is flagged live but unseen since '
  'before day N (A and L < S + N: a zombie, or a gone verdict still pending). ''not_exited'' = otherwise: on the market at '
  'day N, or handed over (another ad ran on more than M days). M is a follow-up / handover margin, recalibrated from '
  'production (broker board W0). Python face: toolkit.measures.exit_within_sql. No SET search_path: a SET clause blocks '
  'inlining.';

revoke all on function public.measure_exit_within(boolean, timestamptz, timestamptz, boolean, timestamptz, integer)
  from public, anon, authenticated;
grant execute on function public.measure_exit_within(boolean, timestamptz, timestamptz, boolean, timestamptz, integer)
  to authenticated, service_role;

commit;

-- ---------------------------------------------------------------------------
-- 4. Backfill, from the one spelling, in its own statement AFTER the DDL commits: inside that transaction it would
--    hold the table's REFERENCES locks (SHARE ROW EXCLUSIVE on properties and broker_identities) for its whole run,
--    stalling every rollup, birth, merge, detach and settle stamp and the broker lane's identity upserts. Alone, its
--    foreign-key checks take per-row KEY SHARE locks, which no non-key UPDATE of those tables waits on. Idempotent.
-- ---------------------------------------------------------------------------
insert into public.property_broker_spells (property_id, broker_identity_id, started_at, ended_at, tom_observed)
select property_id, broker_identity_id, started_at, ended_at, tom_observed
  from public.property_broker_spell_projection
on conflict do nothing;

do $post$
declare
  v_role text;
begin
  if not (select relrowsecurity from pg_class where oid = 'public.property_broker_spells'::regclass) then
    raise exception '691 did not land: property_broker_spells has row level security off';
  end if;
  if exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'property_broker_spells') then
    raise exception '691 did not land: property_broker_spells carries a policy (it is service-role only)';
  end if;
  foreach v_role in array array['anon', 'authenticated'] loop
    if has_table_privilege(v_role, 'public.property_broker_spells', 'select')
       or has_table_privilege(v_role, 'public.property_broker_spell_projection', 'select')
       or has_table_privilege(v_role, 'public.broker_book', 'select') then
      raise exception '691 did not land: % can read the broker book', v_role;
    end if;
  end loop;
end
$post$;

reset statement_timeout;
reset lock_timeout;
